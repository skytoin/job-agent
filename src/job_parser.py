"""Phase 1: Scrape job descriptions from career pages."""

import asyncio

from browser_use import Agent, Browser
from langchain_anthropic import ChatAnthropic

from src.profile import JobDescription, JobTarget


SCRAPE_PROMPT = """Visit {url} and extract the job posting information.

Return the following in a structured way:
- Company name
- Job title/position
- Full job description text
- List of requirements/qualifications
- Location (remote/hybrid/onsite + city)
- Salary range if listed

Just read the page and report back. Do NOT click Apply or interact with any forms.
"""


async def scrape_job(job: JobTarget, model_name: str = "claude-sonnet-4-20250514") -> JobDescription:
    """Scrape a single job posting. Returns structured job data."""
    browser = Browser(headless=True)

    agent = Agent(
        task=SCRAPE_PROMPT.format(url=job.url),
        llm=ChatAnthropic(model=model_name, temperature=0),
        browser=browser,
        max_steps=10,
    )

    try:
        result = await agent.run()
        # TODO: Parse agent result into JobDescription
        return JobDescription(
            url=job.url,
            company=job.company or "",
            position=job.position or "",
            description=str(result),
        )
    finally:
        await browser.close()


async def scrape_all_jobs(
    jobs: list[JobTarget],
    max_parallel: int = 5,
    model_name: str = "claude-sonnet-4-20250514",
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

    return {
        url: jd
        for url, jd in results
        if isinstance((url, jd), tuple) and not isinstance(jd, Exception)
    }
