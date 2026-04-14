"""Skyvern fallback client.

When browser-use fails on a job, this module retries the same job through a
self-hosted Skyvern instance. Skyvern runs in Docker, so we serve the resume
file over a tiny local HTTP server reachable from the Skyvern container via
Docker's magic hostname ``host.docker.internal``.

Public API: ``fill_application_via_skyvern``.
"""

import asyncio
import logging
import os
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from src.profile import ApplicationResult, JobTarget, Profile

logger = logging.getLogger("job-agent")

# Configuration ---------------------------------------------------------------

SKYVERN_BASE_URL_DEFAULT = "http://localhost:8000"
SKYVERN_API_PREFIX = "/v1"

# Newer, smarter Skyvern engine — far better at dropdowns and multi-page forms.
SKYVERN_ENGINE = "skyvern-2.0"

# Job applications are step-heavy (30-50 fields common). Default Skyvern max is 10.
SKYVERN_MAX_STEPS = 50

# Local resume server — Skyvern's container reaches the host via this hostname.
RESUME_SERVER_HOST_FOR_DOCKER = "host.docker.internal"
RESUME_SERVER_PORT_DEFAULT = 8765

# Polling limits (skyvern-2.0 averages ~15 sec/step, so 50 steps ≈ 12 min worst case).
TASK_TIMEOUT_SECONDS = 900  # 15 min hard ceiling per job
POLL_INTERVAL_SECONDS = 5
TERMINAL_STATUSES = {"completed", "failed", "terminated", "timed_out", "canceled"}


# Resume HTTP server ----------------------------------------------------------


class _SilentHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves files without spamming the log."""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def _start_resume_server(directory: Path, port: int) -> ThreadingHTTPServer:
    """Start a background HTTP server serving ``directory``.

    Binds ``0.0.0.0`` so the Skyvern Docker container can reach it via
    ``host.docker.internal``. Runs in a daemon thread so process exit kills it.
    """
    handler = partial(_SilentHandler, directory=str(directory))
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.info(f"  Skyvern: resume server up on port {port} -> {directory}")
    return server


def _stop_resume_server(server: ThreadingHTTPServer) -> None:
    """Gracefully shut the resume server down."""
    try:
        server.shutdown()
        server.server_close()
    except Exception as e:
        logger.warning(f"  Skyvern: resume server shutdown error: {e}")


# Prompt building -------------------------------------------------------------


def _build_prompt(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
    resume_url: str,
) -> str:
    """Build a natural-language instruction prompt for Skyvern."""
    edu = profile.education[0] if profile.education else None
    edu_line = f"{edu.degree}, {edu.school} ({edu.year})" if edu else ""

    exp_lines = "\n".join(
        f"  - {e.company} — {e.title} ({e.dates})" for e in profile.experience[:4]
    )
    skills_line = ", ".join(profile.skills[:20])

    cover_letter_block = ""
    if cover_letter:
        cover_letter_block = (
            f"\nWhen you see a cover letter / 'why are you interested' field, "
            f"paste this exact text:\n---\n{cover_letter[:1500]}\n---\n"
        )

    return f"""Apply to the job posting at {job.url}.

Fill the application form with this applicant profile:
- Name: {profile.first_name} {profile.last_name}
- Email: {profile.email}
- Phone: {profile.phone}
- Location: {profile.location}
- Current role: {profile.current_title}
- Years of experience: {profile.years_experience}
- LinkedIn: {profile.linkedin_url}
- GitHub: {profile.github_url or "N/A"}
- Education: {edu_line}
- Experience:
{exp_lines}
- Top skills: {skills_line}

When you see a resume / CV file upload field, upload the file from this URL:
{resume_url}
{cover_letter_block}
For Equal Employment / demographic questions, use:
- Work authorization: {profile.work_authorization}
- Requires sponsorship: {profile.requires_sponsorship}
- Gender: {profile.gender}
- Hispanic/Latino: {profile.hispanic_latino}
- Ethnicity: {profile.ethnicity}
- Veteran status: {profile.veteran_status}
- Disability status: {profile.disability_status}

For dropdown questions, pick the option that best matches the profile above.
For multi-page forms, advance through every page and fill all required fields.
For free-text questions about interest, write 2-3 sentences tying the role to
the applicant's ML/AI background.

CRITICAL SAFETY RULE: DO NOT click the final Submit or Apply button. Fill every
field, advance through every page, but STOP before final submission."""


# Skyvern HTTP calls ----------------------------------------------------------


async def _start_task(
    client: httpx.AsyncClient,
    prompt: str,
    url: str,
    api_key: str,
) -> str:
    """POST to Skyvern's run-task endpoint. Returns the run_id."""
    resp = await client.post(
        f"{SKYVERN_API_PREFIX}/run/tasks",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "url": url,
            "engine": SKYVERN_ENGINE,
            "max_steps": SKYVERN_MAX_STEPS,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    run_id = data.get("run_id")
    if not run_id:
        raise RuntimeError(f"Skyvern response missing run_id: {data}")
    return run_id


async def _poll_until_done(
    client: httpx.AsyncClient,
    run_id: str,
    api_key: str,
    timeout: int = TASK_TIMEOUT_SECONDS,
) -> dict:
    """Poll ``/runs/{run_id}`` until the task hits a terminal status or times out."""
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(
            f"{SKYVERN_API_PREFIX}/runs/{run_id}",
            headers={"x-api-key": api_key},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status", "unknown")
        if status in TERMINAL_STATUSES:
            return data

        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return {"status": "timed_out", "failure_reason": "Client-side poll timeout"}


# Public API ------------------------------------------------------------------


async def fill_application_via_skyvern(
    job: JobTarget,
    profile: Profile,
    cover_letter: str,
) -> ApplicationResult:
    """Fill a job application through Skyvern. Returns an ``ApplicationResult``.

    Starts a local HTTP server serving the resume file so Skyvern's container
    can download it, runs the task, polls to completion, then tears down.
    """
    api_key = os.getenv("SKYVERN_API_KEY", "")
    base_url = os.getenv("SKYVERN_BASE_URL", SKYVERN_BASE_URL_DEFAULT)
    port = int(os.getenv("SKYVERN_RESUME_SERVER_PORT", RESUME_SERVER_PORT_DEFAULT))

    if not api_key:
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error="SKYVERN_API_KEY not set — cannot call Skyvern fallback",
            failure_category="exception",
            retried_with="skyvern",
        )

    resume_path = Path(profile.resume_path).resolve()
    if not resume_path.is_file():
        return ApplicationResult(
            job_url=job.url,
            status="error",
            error=f"Resume file not found at {resume_path}",
            failure_category="exception",
            retried_with="skyvern",
        )

    # Security: copy the resume into an isolated temp directory before serving.
    # Never serve the resume's real parent — it would expose .env, credentials, etc.
    serve_dir = Path(tempfile.mkdtemp(prefix="skyvern_resume_"))
    shutil.copy2(resume_path, serve_dir / resume_path.name)

    resume_url = f"http://{RESUME_SERVER_HOST_FOR_DOCKER}:{port}/{resume_path.name}"
    server = _start_resume_server(serve_dir, port)

    try:
        prompt = _build_prompt(job, profile, cover_letter, resume_url)

        async with httpx.AsyncClient(base_url=base_url) as http_client:
            logger.info(f"  Skyvern: starting task for {job.url[:60]}")
            run_id = await _start_task(http_client, prompt, job.url, api_key)
            logger.info(f"  Skyvern: run_id={run_id}, polling...")

            result_data = await _poll_until_done(http_client, run_id, api_key)

        return _build_result(job, result_data)

    except httpx.HTTPError as e:
        logger.error(f"  Skyvern HTTP error: {e}")
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error=f"Skyvern HTTP error: {e}"[:500],
            failure_category="exception",
            retried_with="skyvern",
        )
    except Exception as e:
        logger.error(f"  Skyvern fallback error: {e}")
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="error",
            error=str(e)[:500],
            failure_category="exception",
            retried_with="skyvern",
        )
    finally:
        _stop_resume_server(server)
        shutil.rmtree(serve_dir, ignore_errors=True)


def _build_result(job: JobTarget, data: dict) -> ApplicationResult:
    """Translate Skyvern's response payload into an ``ApplicationResult``."""
    status = data.get("status", "unknown")
    output = data.get("output")
    failure_reason = data.get("failure_reason") or ""

    if status == "completed":
        return ApplicationResult(
            job_url=job.url,
            company=job.company or "",
            position=job.position or "",
            status="filled",
            agent_summary=f"[SKYVERN] {str(output)[:1000]}",
            retried_with="skyvern",
        )

    return ApplicationResult(
        job_url=job.url,
        company=job.company or "",
        position=job.position or "",
        status="error",
        error=f"Skyvern status={status}: {failure_reason}"[:500],
        failure_category="form" if status == "failed" else "exception",
        agent_summary=f"[SKYVERN {status}] {failure_reason}"[:1000],
        retried_with="skyvern",
    )
