"""Orchestrator: runs the 3-phase pipeline across all jobs in parallel."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.agent import apply_to_job
from src.cover_letter import generate_all_cover_letters
from src.job_parser import scrape_all_jobs
from src.profile import ApplicationResult, JobTarget, Profile
from src.skyvern_client import fill_application_via_skyvern

console = Console()


async def run_all_applications(
    jobs: list[JobTarget],
    profile: Profile,
    max_parallel: int = 3,
    model_name: str = "claude-sonnet-4-6",
    headless: bool = False,
    dry_run: bool = False,
    force_agent: bool = False,
    skyvern_mode: str = "hybrid",
) -> list[ApplicationResult]:
    """Execute the full 3-phase pipeline for all jobs.

    ``skyvern_mode`` controls how Skyvern is used:
      - ``"hybrid"`` (default): browser-use first, Skyvern on failure
      - ``"only"``:   send every job directly to Skyvern, no browser-use
      - ``"off"``:    pure browser-use, Skyvern never called
    """

    if dry_run:
        console.print("\n[green]Dry run: config validated successfully![/]")
        console.print(f"  Profile: {profile.first_name} {profile.last_name}")
        console.print(f"  Jobs: {len(jobs)} targets loaded")
        for i, job in enumerate(jobs):
            console.print(f"    [{i}] {job.company or 'Unknown'} - {job.url[:60]}")
        console.print("\n[yellow]Skipping Phases 1-3 (no API calls in dry run)[/]")
        return []

    # Phase 1: Scrape job descriptions (always use Haiku — cheap, just reading text)
    scrape_model = "claude-haiku-4-5-20251001"
    console.print(f"\n[bold blue]Phase 1:[/] Scraping {len(jobs)} job descriptions...")
    job_descriptions = await scrape_all_jobs(jobs, max_parallel=5, model_name=scrape_model)
    console.print(f"  Scraped {len(job_descriptions)}/{len(jobs)} successfully")

    # Phase 2: Generate cover letters
    console.print("\n[bold blue]Phase 2:[/] Generating cover letters...")
    jd_pairs = [(url, jd) for url, jd in job_descriptions.items()]
    cover_letters = await generate_all_cover_letters(jd_pairs, profile, model_name=model_name)
    console.print(f"  Generated {sum(1 for v in cover_letters.values() if v)}/{len(jobs)} letters")

    # Phase 3: Fill forms with bounded parallelism
    console.print(
        f"\n[bold blue]Phase 3:[/] Filling {len(jobs)} apps "
        f"(max {max_parallel} parallel, mode={skyvern_mode})..."
    )
    semaphore = asyncio.Semaphore(max_parallel)

    async def bounded_apply(job: JobTarget, idx: int) -> ApplicationResult:
        async with semaphore:
            console.print(f"  Agent {idx}: {job.company or job.url[:50]}...")
            cover_letter = cover_letters.get(job.url, "")

            if skyvern_mode == "only":
                result = await fill_application_via_skyvern(job, profile, cover_letter)
            else:
                result = await apply_to_job(
                    job=job,
                    profile=profile,
                    cover_letter=cover_letter,
                    agent_id=idx,
                    model_name=model_name,
                    max_steps=100,
                    headless=headless,
                    force_agent=force_agent,
                )

                if skyvern_mode == "hybrid" and result.status != "filled":
                    console.print(f"  Agent {idx}: browser-use failed, trying Skyvern fallback...")
                    skyvern_result = await fill_application_via_skyvern(job, profile, cover_letter)
                    if skyvern_result.status == "filled":
                        result = skyvern_result

            icon = "[green]OK[/]" if result.status == "filled" else "[red]FAIL[/]"
            tag = f" [{result.retried_with}]" if result.retried_with else ""
            console.print(f"  Agent {idx}: {icon} {result.status}{tag}")
            return result

    results = await asyncio.gather(
        *[bounded_apply(job, i) for i, job in enumerate(jobs)],
        return_exceptions=True,
    )

    # Normalize exceptions into ApplicationResult
    final_results: list[ApplicationResult] = []
    for i, r in enumerate(results):
        if isinstance(r, ApplicationResult):
            final_results.append(r)
        else:
            final_results.append(
                ApplicationResult(
                    job_url=jobs[i].url,
                    status="error",
                    error=str(r),
                )
            )

    # Save results
    _save_results(final_results)
    _print_summary(final_results)

    return final_results


def _save_results(results: list[ApplicationResult]) -> None:
    """Save results to output/results.json."""
    output_path = Path("output/results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "filled": sum(1 for r in results if r.status == "filled"),
        "errors": sum(1 for r in results if r.status == "error"),
        "results": [r.model_dump() for r in results],
    }
    output_path.write_text(json.dumps(data, indent=2))


def _print_summary(results: list[ApplicationResult]) -> None:
    """Print a nice summary table with failure categories."""
    table = Table(title="Application Results")
    table.add_column("Company", style="cyan")
    table.add_column("Position", style="white")
    table.add_column("Status", style="green")
    table.add_column("Category", style="yellow")
    table.add_column("Details", style="red", max_width=60)

    for r in results:
        status_style = "green" if r.status == "filled" else "red"
        # Show first meaningful line of agent summary for errors
        details = ""
        if r.status != "filled" and r.agent_summary:
            first_line = r.agent_summary.strip().split("\n")[0]
            details = first_line[:60]
        elif r.error:
            details = r.error[:60]

        table.add_row(
            r.company or "-",
            r.position or "-",
            f"[{status_style}]{r.status}[/]",
            r.failure_category or "",
            details,
        )

    console.print(table)
    filled = sum(1 for r in results if r.status == "filled")
    console.print(f"\n[bold]Done: {filled}/{len(results)} filled successfully[/]")

    # Print detailed failure summaries
    failures = [r for r in results if r.status != "filled" and r.agent_summary]
    if failures:
        console.print("\n[bold red]Failure Details:[/]")
        for r in failures:
            console.print(f"\n  [cyan]{r.company or r.job_url[:50]}[/] [{r.failure_category}]:")
            # Show first 3 lines of agent summary
            lines = r.agent_summary.strip().split("\n")[:3]
            for line in lines:
                console.print(f"    {line[:80]}")
