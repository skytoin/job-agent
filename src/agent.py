"""Phase 3: Fill job applications — direct fill (cheap) or browser-use agent (flexible)."""

import json
import logging
import random
from pathlib import Path

from browser_use import Agent, Browser
from browser_use.agent.views import MessageCompactionSettings
from playwright.async_api import Page, async_playwright

from src.direct_fill import (
    _build_prefill_hints,
    detect_complexity,
    direct_fill_application,
    extract_fields_aria,
    is_complex_url,
    merge_field_lists,
)
from src.direct_fill import (
    extract_fields as direct_extract_fields,
)
from src.dropdown_templates import apply_templates
from src.email_action import create_email_tools
from src.layer0_cache import Layer0Cache
from src.llm import create_browser_llm, is_openai_model
from src.profile import ApplicationResult, JobTarget, Profile

logger = logging.getLogger("job-agent")

# Opus escalation is the working fallback: if Sonnet (40 steps) fails on a tough
# form, retry with Opus (60 steps). Previously frozen for a Skyvern experiment —
# Skyvern rejected on cost grounds 2026-04-13, so Opus is back on as the real fallback.
OPUS_ESCALATION_ENABLED = True

# Maximum consecutive scroll-only steps before we abort the browser-use agent.
# The anti-paranoia prompt tells the agent not to scroll-verify, but Sonnet 4.6
# still ignores it sometimes. This is the code-level enforcement of last resort.
MAX_CONSECUTIVE_SCROLL_STEPS = 3

COMPLEX_ATS_DOMAINS = ["oraclecloud.com", "myworkdayjobs.com", "workday.com"]

AUTH_KEYWORDS = [
    "login",
    "sign in",
    "sign-in",
    "log in",
    "create account",
    "create profile",
    "verification code",
    "verify email",
    "verify your",
    "authentication",
    "password",
    "credentials",
    "otp",
    "one-time",
    "account required",
    "register",
    "email code",
]
FORM_KEYWORDS = [
    "dropdown",
    "validation",
    "required field",
    "stuck",
    "radio button",
    "combobox",
    "could not fill",
    "field not found",
]
PAGE_KEYWORDS = [
    "404",
    "not found",
    "page error",
    "captcha",
    "access denied",
    "forbidden",
    "site down",
    "timed out",
    "timeout",
    "no longer available",
    "been filled",
    "no longer accepting",
    "position has been filled",
    "expired",
    "job closed",
]

# Static instructions for browser-use agent — cached via extend_system_message
SYSTEM_INSTRUCTIONS = """You are filling a job application form. Follow these rules:

FIRST STEPS:
- If there is a cookie banner, accept/acknowledge it before doing anything else
- If there is an age verification popup (18+ or 21+), confirm/acknowledge it
- If a click opens a new tab, SWITCH to it immediately — do NOT click the same button again
- If you see a Sign In button on the page header, try Sign In FIRST
- Do NOT click "Autofill with MyGreenhouse" or similar third-party autofill buttons
- If you see "Use My Last Application" option, ALWAYS choose it over other options
- If no login is needed, just fill the form directly

TYPING INTO FIELDS:
- On Workday (myworkdayjobs.com) and Oracle sites, ALWAYS use send_keys, NEVER input action
- For ANY field where input action doesn't stick or shows wrong text:
  1. Click the field first
  2. Use send_keys with Ctrl+a to select all existing text
  3. Use send_keys to type the value — this types character by character
  4. NEVER use the input action on these fields
- If a field shows garbled/wrong text, clear it (Ctrl+a, Backspace) and retype
- ALWAYS use the provided credentials — NEVER make up passwords
- After typing each field with send_keys, press Tab to move to next field
  (this triggers change events so the form registers the value)
- After typing password and pressing Tab, press Enter to submit the form
- On Workday, if clicking a button by index doesn't work (page doesn't change):
  1. Try clicking by COORDINATES instead — look at the screenshot, find the button,
     and use click(coordinate_x=X, coordinate_y=Y) with the button's center position
  2. If that doesn't work, Tab to the button and press Enter
  3. NEVER click the same button by index more than twice

WORKDAY DATE PICKERS (MM/YYYY split fields):
- These use div elements (dateSectionMonth/dateSectionYear), NOT input fields
- CORRECT method:
  1. Click the MONTH div (shows "MM")
  2. send_keys the 2-digit month (e.g. "12") — picker auto-advances to year
  3. send_keys the 4-digit year (e.g. "2024") IMMEDIATELY — no Tab between
  4. The year accepts digits right after month auto-advances
- NEVER send Tab between month and year — Tab skips past the year field
- If it shows wrong year, click the year div explicitly and retype
- Each send_keys call should contain ONLY the digits, nothing else

STRATEGY:
1. Scroll the ENTIRE form ONCE to see all fields
2. Scroll back to top, fill ALL fields TOP TO BOTTOM in one pass
3. BATCH multiple fields per step to minimize steps:
   - Fill ALL visible text fields in ONE step (name, email, phone together)
   - Fill multiple Yes/No dropdowns in ONE step (click+select, click+select)
   - Only handle these separately: file uploads, location autocomplete, long text
4. For open-ended questions, write 2-3 sentences connecting ML/AI experience

RESUME UPLOAD:
- Upload resume FIRST before filling any fields
- If form says "Resume upload is mandatory" after clicking Next, the upload
  was lost — scroll back to the upload field and re-upload immediately
- Do NOT keep clicking Next if it keeps saying resume is mandatory
- After re-uploading, wait 3 seconds for the form to process before continuing

DROPDOWN & AUTOCOMPLETE HANDLING:
- For native <select>: use select_dropdown action
- For react-select / combobox (role=combobox with "Toggle flyout"):
  1. Use dropdown_options to see available options
  2. Click the combobox INPUT (NOT "Toggle flyout" button)
  3. Type a SHORT keyword (e.g. "Male", "Decline", "No", "Prefer")
  4. Wait 1s, click matching option or press Enter, then Tab to next field
- For location/autocomplete fields (typing shows a dark suggestion list):
  1. Click the input field
  2. Use send_keys to type SLOWLY — one or two letters at a time (e.g. "Ne")
  3. Wait 2 seconds for the dark dropdown with suggestions to appear
  4. Check if the right option is in the list (e.g. "New York, NY, USA")
  5. If yes, CLICK that suggestion — the field only accepts clicked suggestions
  6. If not, send_keys to add more letters (e.g. "w Y") and wait again
  7. NEVER use the input action on location fields — use send_keys only
- If "No options", clear and try a shorter keyword
- NEVER click "Toggle flyout" buttons
- If stuck after 2 attempts, SKIP and move on

RADIO BUTTONS & TOGGLE SELECTIONS:
- Custom radio buttons often use <span> elements, not native <input type="radio">
- Click the option ONCE, then check the screenshot for visual confirmation
- If the color changed or a highlight appeared, the selection WORKED — move on
- NEVER click the same radio option twice — clicking again DESELECTS it
- Radio groups are mutually exclusive: selecting one deselects the others
- After selecting, immediately move to the next field — do NOT re-verify

LOOP PREVENTION (READ THIS — VIOLATING IT WASTES MONEY):
- DO NOT scroll up and down repeatedly to "verify" a selection. Once you
  click a radio button, checkbox, or option, TRUST IT and move forward.
- If you have done 2 scroll actions in a row without typing into a field
  or clicking a non-scroll element, YOU ARE LOOPING. STOP scrolling and
  click Submit Application IMMEDIATELY.
- After clicking a button/radio/checkbox, the NEXT action MUST advance
  the form (fill another field or click Submit). The next action MUST
  NOT be a scroll-to-verify or a re-click of the same element.
- The form is correct after your clicks. Trust it. Verification belongs
  AFTER Submit (validation errors), never before.
- A disabled-looking Submit button is often actually enabled — try
  clicking it before assuming a field is missing.

RULES:
- If job says "filled"/"closed"/"expired", call done(success=false) immediately
- Do NOT scroll back up to re-check already filled fields
- Do NOT retry same failed action more than twice — skip and move on
- Once you fill a dropdown (Gender, Hispanic, Race, Veteran, Disability),
  NEVER go back to change it — move forward to the next unfilled field
- Fill EEO/demographic fields in order from top to bottom, ONE TIME ONLY
- After all fields filled, click Submit / Apply IMMEDIATELY — do not re-verify
- If submission needs a verification code, use get_email_verification_code
- Do NOT look for verification codes BEFORE submitting
- After submitting, list what you filled"""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


def _last_step_action_types(a: Agent) -> list[str]:
    """Return the list of action-type keys from the agent's most recent step.

    browser-use's internal API can change between versions, so this is
    defensively wrapped — returns ``[]`` on any ``AttributeError`` /
    ``IndexError`` / ``TypeError``.
    """
    try:
        history = getattr(a.state, "history", None)
        if history is None:
            return []
        steps = getattr(history, "history", None) or []
        if not steps:
            return []
        last = steps[-1]
        model_output = getattr(last, "model_output", None)
        if model_output is None:
            return []
        actions = getattr(model_output, "action", None) or []

        types: list[str] = []
        for act in actions:
            if hasattr(act, "model_dump"):
                data = act.model_dump(exclude_none=True)
            elif isinstance(act, dict):
                data = act
            else:
                continue
            # Each action model has one top-level key naming its type.
            for key, value in data.items():
                if value is not None:
                    types.append(key)
        return types
    except (AttributeError, IndexError, TypeError):
        return []


def _looks_like_submit_click(a: Agent) -> bool:
    """Heuristic: did the last step click something whose text looks like Submit?

    Used to track whether the agent actually clicked Submit before declaring
    done(success=True). Defensively wrapped against browser-use API drift.
    """
    try:
        history = getattr(a.state, "history", None)
        if history is None:
            return False
        steps = getattr(history, "history", None) or []
        if not steps:
            return False
        last = steps[-1]
        results = getattr(last, "result", None) or []
        for r in results:
            content = str(getattr(r, "extracted_content", "") or "").lower()
            if any(kw in content for kw in ("submit application", "submit", "apply now")):
                return True
    except (AttributeError, IndexError, TypeError):
        pass
    return False


def _classify_failure(agent_summary: str) -> str:
    """Classify failure type from the agent's own description."""
    text = agent_summary.lower()
    if any(kw in text for kw in AUTH_KEYWORDS):
        return "auth"
    if any(kw in text for kw in PAGE_KEYWORDS):
        return "page"
    if any(kw in text for kw in FORM_KEYWORDS):
        return "form"
    return "unknown"


def _load_credentials(key: str | None) -> dict | None:
    """Load credentials for a specific site from config/credentials.json."""
    if not key:
        return None
    creds_path = Path("config/credentials.json")
    if not creds_path.exists():
        return None
    all_creds = json.loads(creds_path.read_text())
    return all_creds.get(key)


def _build_sensitive_data(profile: Profile, credentials: dict | None = None) -> dict[str, str]:
    """Build sensitive_data dict — values masked in logs."""
    data = {"email": profile.email, "phone": profile.phone}
    if credentials:
        data["login_email"] = credentials.get("email", "")
        data["login_password"] = credentials.get("password", "")
    return data


TEXT_INPUT_TYPES = ("text", "email", "tel", "url", "textarea")


def _format_prefill_hints(hints: list[tuple[str, str, str]]) -> str:
    """Render Layer 0 hints as a PRE-VERIFIED FIELDS block for the task prompt."""
    if not hints:
        return ""

    lines = [
        "\nPRE-VERIFIED FIELDS (these values were already checked against the "
        "applicant profile — fill them directly, DO NOT re-reason about them):"
    ]
    for field_type, label, value in hints:
        short_label = label[:80]
        short_value = value[:80]
        if field_type in TEXT_INPUT_TYPES:
            action = "type"
        elif field_type in ("radio_group", "button_group"):
            action = "click option"
        elif field_type in ("checkbox_group",):
            action = "check option"
        elif field_type == "checkbox":
            action = "click"
        elif field_type == "combobox":
            action = "type + select"
        else:
            action = "set"
        lines.append(f"  - {short_label}: {action} '{short_value}'")

    lines.append(
        "If you see any of these fields already filled with a DIFFERENT value, "
        "overwrite with the value above. Never scroll-verify these — trust and move on.\n"
    )
    return "\n".join(lines)


def build_task_prompt(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    credentials: dict | None = None,
    use_sensitive_data: bool = True,
    prefill_hints: list[tuple[str, str, str]] | None = None,
) -> str:
    """Build a compact task prompt with only per-job dynamic data.

    If ``prefill_hints`` is provided, a PRE-VERIFIED FIELDS block is inserted
    between the login block and the profile block so browser-use gets the
    Layer 0 answers up front instead of re-deriving them.
    """
    email_val = "x_email" if use_sensitive_data else profile.email
    phone_val = "x_phone" if use_sensitive_data else profile.phone

    login_block = ""
    if credentials:
        if use_sensitive_data:
            creds = "x_login_email / x_login_password"
        else:
            creds = f"{credentials.get('email', '')} / {credentials.get('password', '')}"
        login_block = (
            f"\nLOGIN: Always try Sign In FIRST with {creds}. "
            f"Only create account if sign in says 'no account found'.\n"
        )

    hints_block = _format_prefill_hints(prefill_hints or [])

    cover_letter_block = ""
    if cover_letter:
        cover_letter_block = f"\nCOVER LETTER:\n{cover_letter[:500]}\n"

    exp = " | ".join(f"{e.company} ({e.title}, {e.dates})" for e in profile.experience[:4])

    return f"""Apply at: {job.url}
{login_block}{hints_block}
{profile.first_name} {profile.last_name} | {email_val} | {phone_val}
Location: {profile.location} | Title: {profile.current_title}
Education: {profile.education[0].degree}, {profile.education[0].school}
Experience: {exp}
Skills: {", ".join(profile.skills[:15])}
Resume: {profile.resume_path}
EEO: Auth={profile.work_authorization}, Sponsor={profile.requires_sponsorship}, \
Gender={profile.gender}, Hispanic={profile.hispanic_latino}, \
Race={profile.ethnicity}, Veteran={profile.veteran_status}, \
Disability={profile.disability_status}
{cover_letter_block}"""


# ---------------------------------------------------------------------------
# Direct fill path (cheap: 1 LLM call)
# ---------------------------------------------------------------------------


# Selectors that match any form control element — used to detect a fully
# rendered application form before running detect_complexity.
FORM_READY_SELECTORS = (
    "input:not([type='hidden']):not([type='submit']):not([type='button']), "
    "select, textarea, "
    "[role='textbox'], [role='combobox'], [role='radio'], [role='checkbox']"
)

# Cookie / consent / popup dismissal button text patterns.
COOKIE_DISMISS_SELECTORS = (
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept Cookies')",
    "button:has-text('I agree')",
    "button:has-text('I Agree')",
    "button:has-text('Agree and continue')",
    "button:has-text('Got it')",
    "button:has-text('Dismiss')",
    "button:has-text('Close cookie')",
    "button[aria-label='Accept all cookies']",
    "button[aria-label='Close cookie banner']",
    "[data-testid='cookie-accept']",
    "[id*='cookie'] button:has-text('Accept')",
    "[class*='cookie-banner'] button:has-text('Accept')",
)


async def _dismiss_cookie_banner(page: Page) -> bool:
    """Best-effort: click any visible cookie / consent / popup dismiss button.

    Returns True if a banner was dismissed. Non-fatal on every path.
    """
    for selector in COOKIE_DISMISS_SELECTORS:
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                await btn.click(timeout=2000)
                await page.wait_for_timeout(500)
                logger.info(f"  Dismissed banner via {selector[:40]}")
                return True
        except Exception:
            continue
    return False


async def _wait_for_form_ready(page: Page, timeout_ms: int = 10000) -> bool:
    """Wait for at least one form control element to appear on the page.

    Prevents the detect_complexity "too_few_fields" false positive when the
    SPA hasn't finished rendering. Returns True if a form element appeared,
    False on timeout (caller decides what to do).
    """
    try:
        await page.wait_for_selector(FORM_READY_SELECTORS, timeout=timeout_ms, state="attached")
        return True
    except Exception:
        return False


async def _click_apply_button(page: Page) -> bool:
    """Find and click the Apply button on a job description page."""
    selectors = [
        "[role='tab']:has-text('Application')",
        "a:has-text('Apply for this job')",
        "a:has-text('Apply Now')",
        "a:has-text('Apply')",
        "button:has-text('Apply for this job')",
        "button:has-text('Apply Now')",
        "button:has-text('Apply')",
        "[role='button']:has-text('Apply')",
        "[data-testid*='apply']",
        "[aria-label*='Apply']",
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    return False


async def _try_direct_fill(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    model_name: str,
    headless: bool,
) -> tuple[ApplicationResult | None, list[tuple[str, str, str]]]:
    """Attempt direct fill via Playwright + 1 LLM call.

    Returns ``(result, prefill_hints)``. ``result`` is:
      - ``ApplicationResult`` on direct-fill success or terminal failure
        (e.g. job closed — no browser-use fallback needed).
      - ``None`` when the caller should fall through to browser-use.
    ``prefill_hints`` carries Layer 0's verified (type, label, value) tuples
    so the browser-use fallback can reuse them without re-deriving.
    """
    if is_complex_url(job.url):
        logger.info("  Direct fill: skipped (complex ATS domain)")
        return None, []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            await page.goto(job.url, wait_until="domcontentloaded", timeout=15000)

            # Dismiss any cookie banner / consent popup before doing anything
            # else — they can intercept clicks and block the Apply flow.
            await _dismiss_cookie_banner(page)

            # Wait for the SPA to actually render a form element before we try
            # to detect complexity. This prevents the "too_few_fields" race
            # where detect_complexity fires before Ashby/Greenhouse's React
            # app has finished mounting.
            form_ready = await _wait_for_form_ready(page, timeout_ms=10000)
            if not form_ready:
                logger.info("  Direct fill: no form element appeared in 10s — page may be wrong")

            # Check for closed job before clicking Apply
            body_text = await page.evaluate("document.body?.textContent?.toLowerCase() || ''")
            if any(kw in body_text for kw in ["been filled", "no longer accepting", "expired"]):
                return (
                    ApplicationResult(
                        job_url=job.url,
                        company=job.company or "",
                        position=job.position or "",
                        status="error",
                        error="Job is no longer available.",
                        failure_category="page",
                    ),
                    [],
                )

            # Click Apply/Application button to get to the form
            clicked = await _click_apply_button(page)
            if not clicked:
                logger.info("  Direct fill: no Apply button found, trying form on page")
            else:
                # Re-dismiss any popup that showed up after clicking Apply.
                await _dismiss_cookie_banner(page)
                # Give the form page a moment to render after the click.
                await _wait_for_form_ready(page, timeout_ms=5000)

            # NOW detect complexity from the application form page
            is_complex, reason = await detect_complexity(page)
            if is_complex:
                logger.info(f"  Direct fill: skipped ({reason})")
                # Even though direct_fill is skipping, try to collect hints
                # so browser-use gets the Layer 0 handoff anyway.
                skip_hints = await _extract_hints_from_current_page(page, profile)
                return None, skip_hints

            logger.info("  Direct fill: simple form detected, using cheap path")
            success, summary, prefill_hints = await direct_fill_application(
                page, profile, cover_letter, model_name
            )

            if success:
                return (
                    ApplicationResult(
                        job_url=job.url,
                        company=job.company or "",
                        position=job.position or "",
                        status="filled",
                        agent_summary=f"[DIRECT FILL] {summary}",
                    ),
                    prefill_hints,
                )

            logger.info(
                f"  Direct fill failed: {summary} — falling back to agent "
                f"(passing {len(prefill_hints)} pre-verified hints)"
            )
            return None, prefill_hints

        except Exception as e:
            logger.warning(f"  Direct fill error: {e} — falling back to agent")
            return None, []
        finally:
            await browser.close()


async def _extract_hints_from_current_page(
    page: Page, profile: Profile
) -> list[tuple[str, str, str]]:
    """Run dual extraction + Layer 0 against an already-open page.

    Shared by ``_try_direct_fill`` (skip fallback), ``_collect_prefill_hints_only``
    (force_agent path), and anywhere else that wants hints without a full fill.
    Safe to call on a page that has ZERO form elements — returns empty list.
    """
    try:
        aria_fields = await extract_fields_aria(page)
        js_fields = await direct_extract_fields(page)
        fields = merge_field_lists(aria_fields, js_fields)
    except Exception as e:
        logger.warning(f"  Hint extraction failed during field extract: {e}")
        return []

    if not fields:
        return []

    template_map, unmatched = apply_templates(fields, profile)

    cache = Layer0Cache()
    cache_map: dict[str, str] = {}
    for f in unmatched:
        cached = cache.lookup(f.get("label", ""), f.get("type", ""))
        if cached:
            fid = f.get("id") or f.get("name")
            if fid:
                cache_map[fid] = cached

    hints = _build_prefill_hints(fields, template_map, cache_map)
    if hints:
        logger.info(f"  Hint collection: {len(hints)} prefill hints from Layer 0")
    return hints


async def _collect_prefill_hints_only(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,  # noqa: ARG001 — kept for API parity with _try_direct_fill
    model_name: str,  # noqa: ARG001 — we deliberately skip the LLM call here
    headless: bool,
) -> list[tuple[str, str, str]]:
    """Lightweight version of ``_try_direct_fill`` that ONLY collects hints.

    Used on the ``force_agent=True`` path: we open the form, run the dual
    extractor + Layer 0 templates + Layer 0 cache, and return the
    ``(type, label, value)`` tuples — without filling anything, without
    submitting, without calling the mapping LLM. Cheap way to give the
    browser-use agent a running start.
    """
    if is_complex_url(job.url):
        return []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        try:
            await page.goto(job.url, wait_until="domcontentloaded", timeout=15000)
            await _dismiss_cookie_banner(page)
            await _wait_for_form_ready(page, timeout_ms=10000)
            await _click_apply_button(page)
            await _wait_for_form_ready(page, timeout_ms=5000)

            return await _extract_hints_from_current_page(page, profile)
        except Exception as e:
            logger.warning(f"  Hint collection failed: {e}")
            return []
        finally:
            await browser.close()


# ---------------------------------------------------------------------------
# Browser-use agent path (flexible: 15+ LLM calls)
# ---------------------------------------------------------------------------


async def _agent_fill(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    agent_id: int,
    model_name: str,
    max_steps: int,
    headless: bool,
    prefill_hints: list[tuple[str, str, str]] | None = None,
) -> ApplicationResult:
    """Fill application using browser-use agent (current approach).

    ``prefill_hints`` carries Layer 0's verified ``(type, label, value)``
    tuples from a prior direct_fill attempt. When present, they're appended
    to the task prompt as a PRE-VERIFIED FIELDS block so the agent doesn't
    re-derive answers we already know are correct.
    """
    cred_key = job.credentials_key or profile.default_credentials_key
    credentials = _load_credentials(cred_key)

    openai_model = is_openai_model(model_name)
    use_sensitive = not openai_model
    is_opus = "opus" in model_name

    task_prompt = build_task_prompt(
        job,
        profile,
        cover_letter,
        credentials,
        use_sensitive_data=use_sensitive,
        prefill_hints=prefill_hints,
    )
    sensitive_data = _build_sensitive_data(profile, credentials) if use_sensitive else None

    complex = any(d in job.url for d in COMPLEX_ATS_DOMAINS)
    effective_max_steps = 100 if complex else max_steps

    browser = Browser(
        headless=headless,
        user_data_dir=f"./browser_profiles/agent-{agent_id}",
        user_agent=USER_AGENT,
        wait_between_actions=1.0,
        minimum_wait_page_load_time=1.0,
        wait_for_network_idle_page_load_time=2.0,
    )

    use_flash = not is_opus

    agent = Agent(
        task=task_prompt,
        llm=create_browser_llm(model_name, temperature=0),
        browser=browser,
        tools=create_email_tools(),
        available_file_paths=[profile.resume_path],
        sensitive_data=sensitive_data,
        save_conversation_path=f"./output/logs/agent-{agent_id}/",
        extend_system_message=SYSTEM_INSTRUCTIONS,
        use_judge=False,
        flash_mode=use_flash,
        use_thinking=not use_flash and complex,
        enable_planning=not use_flash and complex,
        max_actions_per_step=15,
        vision_detail_level="auto" if complex else "low",
        calculate_cost=True,
        message_compaction=MessageCompactionSettings(
            enabled=True,
            compact_every_n_steps=15,
            trigger_char_count=8000,
            keep_last_items=4,
        ),
    )

    consecutive_failures = [0]
    scroll_only_streak = [0]
    submit_clicked = [False]

    async def _on_step(a: Agent) -> None:
        """Randomize wait + track consecutive failures to detect stuck state."""
        a.browser_profile.wait_between_actions = random.uniform(1.0, 2.5)

    async def _on_step_end(a: Agent) -> None:
        """After each step, detect stuck states and abort to save cost.

        Three triggers:
          1. ``consecutive_failures >= 3`` — browser-use's own failure count
          2. ``scroll_only_streak >= MAX_CONSECUTIVE_SCROLL_STEPS`` — the agent
             is looping on scroll-to-verify even though the anti-paranoia
             prompt told it not to
          3. (tracked but not abort-on) whether the agent ever clicked Submit
        """
        if a.state.consecutive_failures >= 4:
            consecutive_failures[0] += 1
        else:
            consecutive_failures[0] = 0

        if consecutive_failures[0] >= 3:
            raise RuntimeError("Agent stuck — too many consecutive failures")

        # Inspect the last step's actions. If ALL actions were scrolls (no
        # form-interaction like input/click-on-non-scroll), count it as a
        # scroll-only step. Reset the streak the moment a non-scroll action
        # appears.
        action_types = _last_step_action_types(a)
        if action_types:
            meaningful = [t for t in action_types if t not in ("scroll", "wait", "done")]
            if not meaningful:
                scroll_only_streak[0] += 1
            else:
                scroll_only_streak[0] = 0
            # Track submit-click events so we can detect fake-success later.
            for t in action_types:
                if t in ("click", "click_element_by_index", "click_element"):
                    # Approximate: treat any click as potentially a submit click.
                    submit_clicked[0] = submit_clicked[0] or _looks_like_submit_click(a)

        if scroll_only_streak[0] >= MAX_CONSECUTIVE_SCROLL_STEPS:
            raise RuntimeError(
                f"Agent stuck in scroll loop "
                f"({MAX_CONSECUTIVE_SCROLL_STEPS} consecutive scroll-only steps) "
                "— aborting to save cost"
            )

    try:
        result = await agent.run(
            max_steps=effective_max_steps,
            on_step_start=_on_step,
            on_step_end=_on_step_end,
        )
        agent_summary = result.final_result() or ""
        status = "filled" if result.is_successful() else "error"

        # If the agent claimed success but we never saw a submit click, warn
        # loudly — this often means the user manually clicked submit or the
        # agent hallucinated a success banner from stale screenshot state.
        if status == "filled" and not submit_clicked[0]:
            logger.warning(
                "  Agent declared success but no submit click was observed. "
                "Treating as success but flagging for manual verification."
            )
            agent_summary = f"[UNVERIFIED SUBMIT] {agent_summary}"

        failure_cat = None if status == "filled" else _classify_failure(agent_summary)
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status=status,
            error=None if status == "filled" else agent_summary[:500],
            failure_category=failure_cat,
            agent_summary=agent_summary[:1000],
            screenshot_path=f"./output/screenshots/agent-{agent_id}.png",
        )
    except Exception as e:
        failure_cat = "scroll_loop" if "scroll loop" in str(e) else "exception"
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error=str(e)[:500],
            failure_category=failure_cat,
        )
    finally:
        await browser.stop()


# ---------------------------------------------------------------------------
# Public API: try direct fill first, fall back to browser-use agent
# ---------------------------------------------------------------------------


async def apply_to_job(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    agent_id: int,
    model_name: str = "claude-sonnet-4-6",
    max_steps: int = 100,
    headless: bool = False,
    force_agent: bool = False,
) -> ApplicationResult:
    """Apply to a job. Tries direct fill first (cheap), falls back to agent.

    Set force_agent=True to skip direct fill AND still collect Layer 0 hints
    so the agent gets the PRE-VERIFIED FIELDS block.
    """
    prefill_hints: list[tuple[str, str, str]] = []

    # Try direct fill first (unless forced to use agent)
    if not force_agent:
        result, prefill_hints = await _try_direct_fill(
            job, profile, cover_letter, model_name, headless
        )
        if result is not None:
            return result
    else:
        # force_agent path — still run a lightweight hint collection so
        # browser-use benefits from Layer 0 without burning tokens.
        prefill_hints = await _collect_prefill_hints_only(
            job, profile, cover_letter, model_name, headless
        )

    # Try Sonnet first with 40 step budget, escalate to Opus if it fails
    sonnet_model = "claude-sonnet-4-6"
    opus_model = "claude-opus-4-6"

    # Use requested model if it's already Opus or OpenAI, no escalation needed
    if "opus" in model_name or is_openai_model(model_name):
        logger.info("  Using browser-use agent (full path)")
        return await _agent_fill(
            job,
            profile,
            cover_letter,
            agent_id,
            model_name,
            max_steps,
            headless,
            prefill_hints=prefill_hints,
        )

    # Start with Sonnet (cheap) — 40 step budget
    hint_suffix = f" (+{len(prefill_hints)} hints)" if prefill_hints else ""
    if OPUS_ESCALATION_ENABLED:
        logger.info(f"  Using Sonnet (40 steps) — will escalate to Opus if needed{hint_suffix}")
    else:
        logger.info(f"  Using Sonnet (40 steps) — Opus escalation frozen{hint_suffix}")
    result = await _agent_fill(
        job,
        profile,
        cover_letter,
        agent_id,
        sonnet_model,
        40,
        headless,
        prefill_hints=prefill_hints,
    )

    # If Sonnet succeeded, we're done
    if result.status == "filled":
        return result

    # Sonnet failed — escalate to Opus only if feature flag is enabled
    if not OPUS_ESCALATION_ENABLED:
        return result

    logger.info("  Sonnet failed — escalating to Opus (60 steps)")
    opus_result = await _agent_fill(
        job,
        profile,
        cover_letter,
        agent_id,
        opus_model,
        60,
        headless,
        prefill_hints=prefill_hints,
    )

    # Return Opus result, noting the escalation
    if opus_result.agent_summary:
        opus_result.agent_summary = f"[ESCALATED TO OPUS] {opus_result.agent_summary}"
    return opus_result
