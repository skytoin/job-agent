"""Phase 3: Browser agent that fills a single job application form."""

import json
from pathlib import Path

from browser_use import Agent, Browser
from langchain_anthropic import ChatAnthropic

from src.profile import ApplicationResult, JobTarget, Profile


def _load_credentials(key: str | None) -> dict | None:
    """Load credentials for a specific site from config/credentials.json."""
    if not key:
        return None
    creds_path = Path("config/credentials.json")
    if not creds_path.exists():
        return None
    all_creds = json.loads(creds_path.read_text())
    return all_creds.get(key)


def build_task_prompt(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    credentials: dict | None = None,
) -> str:
    """Build the task prompt for the browser agent. Token-efficient."""
    login_block = ""
    if credentials:
        login_block = (
            f"\nIf you see a login/sign-in page, enter:\n"
            f"  Email: {credentials['email']}\n"
            f"  Password: {credentials['password']}\n"
            f"  Then click Sign In / Log In / Continue.\n"
        )

    cover_letter_block = ""
    if cover_letter:
        cover_letter_block = (
            f"\nIf there is a cover letter field or text area, paste this:\n"
            f"{cover_letter[:600]}\n"
        )

    return f"""Fill out the job application at: {job.url}
{login_block}
APPLICANT INFO (use exact values):
- First Name: {profile.first_name}
- Last Name: {profile.last_name}
- Email: {profile.email}
- Phone: {profile.phone}
- Location: {profile.location}
- LinkedIn: {profile.linkedin_url}
- GitHub: {profile.github_url or 'N/A'}
- Current Title: {profile.current_title}
- Years of Experience: {profile.years_experience}
- Work Authorization: {profile.work_authorization}
- Sponsorship Required: {profile.requires_sponsorship}
- Salary Expectation: {profile.salary_expectation}
- Earliest Start: {profile.start_date}

SKILLS (select matching ones from dropdowns/checkboxes):
{', '.join(profile.skills[:15])}
{cover_letter_block}
INSTRUCTIONS:
1. Navigate to the application form (click Apply if needed)
2. Fill every visible field using the info above
3. For file upload fields, upload: {profile.resume_path}
4. For "Why do you want to work here?" questions, write 2-3 sentences
   connecting the applicant's ML/AI background to this specific role
5. For EEO/demographic questions, select "Decline to self-identify"
6. If there are multiple pages, click Next/Continue through all of them

CRITICAL: Do NOT click the final Submit or Apply button.
STOP when the form is fully filled and take a screenshot.
Confirm what you filled in your final message.
"""


async def apply_to_job(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    agent_id: int,
    model_name: str = "claude-sonnet-4-20250514",
    max_steps: int = 30,
    headless: bool = False,
) -> ApplicationResult:
    """Run a single browser agent to fill one job application."""
    credentials = _load_credentials(job.credentials_key)
    task_prompt = build_task_prompt(job, profile, cover_letter, credentials)

    browser = Browser(
        config={
            "headless": headless,
            "user_data_dir": f"./browser_profiles/agent-{agent_id}",
        }
    )

    agent = Agent(
        task=task_prompt,
        llm=ChatAnthropic(model=model_name, temperature=0),
        browser=browser,
        max_steps=max_steps,
        save_conversation_path=f"./output/logs/agent-{agent_id}/",
    )

    try:
        result = await agent.run()
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="filled",
            screenshot_path=f"./output/screenshots/agent-{agent_id}.png",
        )
    except Exception as e:
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error=str(e),
        )
    finally:
        await browser.close()
