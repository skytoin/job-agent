"""Phase 1: Scrape job descriptions from career pages.

Primary: HTTP fetch + BeautifulSoup + single LLM call (fast, cheap).
Fallback: browser-use agent for JS-heavy pages.
"""

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

from src.llm import is_openai_model
from src.profile import JobDescription, JobTarget

logger = logging.getLogger("job-agent")

EXTRACT_PROMPT = """Extract job posting info from this HTML. Return ONLY this format:

COMPANY: <name>
POSITION: <title>
LOCATION: <location>
SALARY: <range or "Not listed">
DESCRIPTION: <full job description text>
REQUIREMENTS: <bullet list of requirements>"""

# Sites that need JS rendering (browser fallback)
# Most ATS platforms are JS-rendered SPAs. HTTP works for simple career pages.
# Add domains here as you discover they need browser rendering.
JS_REQUIRED_DOMAINS = [
    "myworkdayjobs.com",
    "workday.com",
    "ashbyhq.com",
    "lever.co",
    "greenhouse.io",
    "careers.gene.com",
    "phenom.com",
    "icims.com",
    "smartrecruiters.com",
]


def _needs_browser(url: str) -> bool:
    """Check if URL requires JS rendering."""
    return any(domain in url for domain in JS_REQUIRED_DOMAINS)


def _clean_html(html: str, max_chars: int = 15000) -> str:
    """Strip HTML to readable text, keeping structure."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise elements
    for tag in soup(["script", "style", "nav", "footer", "header", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    # Collapse blank lines
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:max_chars]


def _parse_llm_response(text: str, job: JobTarget, url: str) -> JobDescription:
    """Parse structured LLM response into JobDescription."""
    fields = {}
    for line in text.splitlines():
        for key in ("COMPANY", "POSITION", "LOCATION", "SALARY", "DESCRIPTION"):
            if line.startswith(f"{key}:"):
                fields[key] = line[len(key) + 1 :].strip()

    # Description may span multiple lines after DESCRIPTION:
    if "DESCRIPTION:" in text:
        desc_start = text.index("DESCRIPTION:") + len("DESCRIPTION:")
        req_start = text.find("REQUIREMENTS:")
        if req_start > 0:
            fields["DESCRIPTION"] = text[desc_start:req_start].strip()
        else:
            fields["DESCRIPTION"] = text[desc_start:].strip()

    requirements_text = ""
    if "REQUIREMENTS:" in text:
        requirements_text = text[text.index("REQUIREMENTS:") + len("REQUIREMENTS:") :]

    return JobDescription(
        url=url,
        company=fields.get("COMPANY", job.company or ""),
        position=fields.get("POSITION", job.position or ""),
        description=fields.get("DESCRIPTION", ""),
        requirements=[r.strip("- ").strip() for r in requirements_text.splitlines() if r.strip()],
        location=fields.get("LOCATION", ""),
        salary_range=fields.get("SALARY", ""),
    )


async def _scrape_via_http(
    job: JobTarget, model_name: str = "claude-haiku-4-5-20251001"
) -> JobDescription:
    """Scrape via HTTP + LLM extraction. Fast and cheap."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        resp = await client.get(
            job.url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobAgent/1.0)"},
        )
        resp.raise_for_status()

    page_text = _clean_html(resp.text)
    if len(page_text) < 100:
        raise ValueError("Page text too short — likely JS-rendered")

    # Single LLM call to extract structured data
    llm_text = await _call_extraction_llm(model_name, page_text)
    return _parse_llm_response(llm_text, job, job.url)


async def _call_extraction_llm(model_name: str, page_text: str) -> str:
    """Call the appropriate LLM API for text extraction."""
    prompt = f"{EXTRACT_PROMPT}\n\n---\n{page_text}"

    if is_openai_model(model_name):
        import openai

        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model=model_name,
            max_completion_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    import anthropic

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model=model_name,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def _scrape_via_browser(
    job: JobTarget, model_name: str = "claude-haiku-4-5-20251001"
) -> JobDescription:
    """Fallback: use browser-use agent for JS-heavy pages."""
    from browser_use import Agent, Browser

    from src.llm import create_browser_llm

    browser = Browser(
        headless=True,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/133.0.0.0 Safari/537.36"
        ),
        minimum_wait_page_load_time=1.0,
    )
    agent = Agent(
        task=(
            f"Visit {job.url} and extract: company name, job title, "
            f"full description, requirements, location, salary. "
            f"Report back. Do NOT click Apply."
        ),
        llm=create_browser_llm(model_name, temperature=0),
        browser=browser,
        use_vision=False,
        use_judge=False,
        use_thinking=False,
        enable_planning=False,
    )

    try:
        result = await agent.run(max_steps=8)
        final_text = result.final_result() or ""
        return JobDescription(
            url=job.url,
            company=job.company or "",
            position=job.position or "",
            description=str(final_text),
        )
    finally:
        await browser.stop()


async def scrape_job(
    job: JobTarget, model_name: str = "claude-haiku-4-5-20251001"
) -> JobDescription:
    """Scrape a single job posting. HTTP first, browser fallback."""
    if _needs_browser(job.url):
        logger.info(f"  Using browser for JS-heavy site: {job.url[:60]}")
        return await _scrape_via_browser(job, model_name)

    try:
        return await _scrape_via_http(job, model_name)
    except Exception as e:
        logger.warning(f"  HTTP scrape failed ({e}), falling back to browser")
        return await _scrape_via_browser(job, model_name)


async def scrape_all_jobs(
    jobs: list[JobTarget],
    max_parallel: int = 5,
    model_name: str = "claude-haiku-4-5-20251001",
) -> dict[str, JobDescription]:
    """Scrape all job descriptions in parallel."""
    semaphore = asyncio.Semaphore(max_parallel)

    async def bounded_scrape(job: JobTarget) -> tuple[str, JobDescription]:
        async with semaphore:
            jd = await scrape_job(job, model_name)
            return job.url, jd

    results = await asyncio.gather(
        *[bounded_scrape(j) for j in jobs],
        return_exceptions=True,
    )

    scraped = {}
    for i, r in enumerate(results):
        if isinstance(r, tuple):
            url, jd = r
            scraped[url] = jd
        else:
            logger.error(f"  Scrape failed for {jobs[i].url[:60]}: {r}")

    return scraped
