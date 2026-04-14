"""Email reader: fetch verification codes from Gmail via IMAP.

Connects to Gmail, finds the most recent email matching a sender/subject pattern,
and extracts a numeric verification code from the body.
"""

import asyncio
import email
import email.message
import email.utils
import imaplib
import json
import logging
import re
import time
from email.header import decode_header
from pathlib import Path

logger = logging.getLogger("job-agent")

# Common patterns for verification code emails from job sites
VERIFICATION_SENDERS = [
    "noreply",
    "no-reply",
    "verify",
    "confirm",
    "security",
    "careers",
    "jobs",
    "apply",
    "talent",
    "hiring",
    "greenhouse",
    "greenhouse-mail",
    "seatgeek",
    "workday",
    "oracle",
    "ashby",
]

# Regex patterns to extract verification codes (digits or alphanumeric)
CODE_PATTERNS = [
    # Alphanumeric codes in bold/strong tags (Greenhouse uses this)
    r"<strong>([A-Za-z0-9]{6,10})</strong>",
    r"<b>([A-Za-z0-9]{6,10})</b>",
    # "paste this code" pattern (Greenhouse exact wording)
    r"(?:paste|enter|use|type)\s+(?:this\s+)?code[^:]*?:\s*([A-Za-z0-9]{6,10})",
    # Digit-only codes with context keywords
    r"(?:code|pin|otp|verification|verify|confirm)\s*(?:is|:)?\s*[\s:]*(\d{4,8})",
    r"(\d{4,8})\s*(?:is your|as your|verification|code|pin)",
    r"(?:enter|use|type)\s+(?:the\s+)?(?:code\s+)?(\d{4,8})",
    # Standalone codes
    r"(?:^|\s)(\d{6})(?:\s|$)",
    r"(?:^|\s)(\d{8})(?:\s|$)",
]


def _load_email_credentials() -> dict | None:
    """Load Gmail IMAP credentials from config/credentials.json."""
    creds_path = Path("config/credentials.json")
    if not creds_path.exists():
        return None
    all_creds = json.loads(creds_path.read_text())
    return all_creds.get("gmail_imap")


def _decode_email_body(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body += payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")
    return body


def _extract_code_from_text(text: str) -> str | None:
    """Extract a verification code from email text using regex patterns."""
    for pattern in CODE_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1)
    return None


def _decode_subject(subject_header: str | None) -> str:
    """Decode an email subject header."""
    if not subject_header:
        return ""
    decoded_parts = decode_header(subject_header)
    result = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def fetch_verification_code(
    max_age_seconds: int = 300,
    max_emails_to_check: int = 10,
) -> str | None:
    """Fetch the most recent verification code from Gmail.

    Checks the most recent emails (up to max_emails_to_check) received
    within max_age_seconds for a verification code pattern.
    """
    creds = _load_email_credentials()
    if not creds:
        logger.error("No gmail_imap credentials in config/credentials.json")
        return None

    imap_email = creds["email"]
    imap_password = creds["app_password"]

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(imap_email, imap_password)
        mail.select("INBOX")

        # Search for recent emails
        _, message_ids = mail.search(None, "ALL")
        all_ids = message_ids[0].split()

        if not all_ids:
            logger.warning("No emails found in inbox")
            mail.logout()
            return None

        # Check most recent emails first
        recent_ids = all_ids[-max_emails_to_check:]
        recent_ids.reverse()

        cutoff_time = time.time() - max_age_seconds

        for msg_id in recent_ids:
            _, msg_data = mail.fetch(msg_id, "(RFC822)")
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            # Check email age
            date_str = msg.get("Date", "")
            try:
                email_time = email.utils.parsedate_to_datetime(date_str)
                if email_time.timestamp() < cutoff_time:
                    continue
            except (TypeError, ValueError):
                continue

            # Check if sender looks like a verification email
            sender = (msg.get("From", "") or "").lower()
            subject = _decode_subject(msg.get("Subject")).lower()

            is_verification = any(kw in sender or kw in subject for kw in VERIFICATION_SENDERS)
            has_code_keywords = any(
                kw in subject for kw in ["code", "verify", "confirm", "otp", "security"]
            )

            if not (is_verification or has_code_keywords):
                continue

            # Extract code from body
            body = _decode_email_body(msg)
            code = _extract_code_from_text(body)

            if code:
                logger.info(f"Found verification code from: {sender[:40]}")
                mail.logout()
                return code

        mail.logout()
        logger.warning("No verification code found in recent emails")
        return None

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP error: {e}")
        return None
    except Exception as e:
        logger.error(f"Email reader error: {e}")
        return None


async def poll_for_verification_code(
    max_wait_seconds: int = 120,
    poll_interval: int = 10,
    max_age_seconds: int = 300,
) -> str | None:
    """Poll Gmail for a verification code, retrying until found or timeout.

    Runs in a loop, checking every poll_interval seconds for up to
    max_wait_seconds total.
    """
    start = time.time()
    attempts = 0

    while time.time() - start < max_wait_seconds:
        attempts += 1
        logger.info(f"  Checking email for verification code (attempt {attempts})...")

        code = await asyncio.to_thread(fetch_verification_code, max_age_seconds)
        if code:
            return code

        await asyncio.sleep(poll_interval)

    logger.warning(f"No verification code found after {max_wait_seconds}s")
    return None
