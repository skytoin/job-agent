"""Phase 3: Browser agent that fills a single job application form."""

import json
from pathlib import Path

from browser_use import Agent, Browser
from browser_use.agent.views import MessageCompactionSettings

from src.email_action import create_email_tools
from src.llm import create_browser_llm, is_openai_model
from src.profile import ApplicationResult, JobTarget, Profile

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

# Static instructions that don't change per job — cached by browser-use
SYSTEM_INSTRUCTIONS = """You are filling a job application form. Follow these rules:

STRATEGY:
1. Scroll the ENTIRE form ONCE to see all fields
2. Scroll back to top, fill ALL fields TOP TO BOTTOM in one pass
3. Fill multiple fields per action — maximize efficiency
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

RULES:
- If job says "filled"/"closed"/"expired", call done(success=false) immediately
- Do NOT scroll back up to re-check already filled fields
- Do NOT retry same failed action more than twice — skip and move on
- After all fields filled, click Submit / Apply
- If submission needs a verification code, use get_email_verification_code
- Do NOT look for verification codes BEFORE submitting
- After submitting, list what you filled"""


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


def _is_complex_ats(url: str) -> bool:
    """Check if URL is a complex multi-page ATS that needs extra budget."""
    return any(domain in url for domain in COMPLEX_ATS_DOMAINS)


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


def build_task_prompt(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    credentials: dict | None = None,
    use_sensitive_data: bool = True,
) -> str:
    """Build a compact task prompt with only per-job dynamic data.

    Static instructions (strategy, dropdown handling, rules) are in
    SYSTEM_INSTRUCTIONS and cached separately via extend_system_message.
    """
    email_val = "x_email" if use_sensitive_data else profile.email
    phone_val = "x_phone" if use_sensitive_data else profile.phone

    login_block = ""
    if credentials:
        if use_sensitive_data:
            creds = "x_login_email / x_login_password"
        else:
            creds = f"{credentials.get('email', '')} / {credentials.get('password', '')}"
        login_block = f"\nLOGIN: Use {creds}. Create account if needed.\n"

    cover_letter_block = ""
    if cover_letter:
        cover_letter_block = f"\nCOVER LETTER:\n{cover_letter[:500]}\n"

    exp = " | ".join(f"{e.company} ({e.title}, {e.dates})" for e in profile.experience[:4])

    return f"""Apply at: {job.url}
{login_block}
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


async def apply_to_job(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    agent_id: int,
    model_name: str = "claude-sonnet-4-20250514",
    max_steps: int = 50,
    headless: bool = False,
) -> ApplicationResult:
    """Run a single browser agent to fill one job application."""
    cred_key = job.credentials_key or profile.default_credentials_key
    credentials = _load_credentials(cred_key)

    openai_model = is_openai_model(model_name)
    use_sensitive = not openai_model
    is_opus = "opus" in model_name

    task_prompt = build_task_prompt(
        job, profile, cover_letter, credentials, use_sensitive_data=use_sensitive
    )
    sensitive_data = _build_sensitive_data(profile, credentials) if use_sensitive else None

    complex = _is_complex_ats(job.url)
    effective_max_steps = 75 if complex else max_steps

    browser = Browser(
        headless=headless,
        user_data_dir=f"./browser_profiles/agent-{agent_id}",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        wait_between_actions=1.0,
        minimum_wait_page_load_time=1.0,
        wait_for_network_idle_page_load_time=2.0,
    )

    # Opus: full reasoning (expensive but fewer steps)
    # Sonnet/GPT/Haiku: flash_mode (cheap output, more steps but much cheaper)
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
        # Cost optimization
        use_judge=False,
        flash_mode=use_flash,
        use_thinking=not use_flash and complex,
        enable_planning=not use_flash and complex,
        max_actions_per_step=10,
        vision_detail_level="auto" if complex else "low",
        calculate_cost=True,
        message_compaction=MessageCompactionSettings(
            enabled=True,
            compact_every_n_steps=10,
            trigger_char_count=8000,
            keep_last_items=4,
        ),
    )

    try:
        result = await agent.run(max_steps=effective_max_steps)
        agent_summary = result.final_result() or ""
        status = "filled" if result.is_successful() else "error"
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
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error=str(e)[:500],
            failure_category="exception",
        )
    finally:
        await browser.stop()
