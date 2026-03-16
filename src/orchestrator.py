"""Orchestrator: runs the 3-phase pipeline across all jobs in parallel."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.agent import apply_to_job
from src.cover_letter import generate_all_cover_letters
from src.job_parser import scrape_all_jobs
from src.profile import ApplicationResult, JobTarget, Profile

console = Console()


async def run_all_applications(
    jobs: list[JobTarget],
    profile: Profile,
    max_parallel: int = 3,
    model_name: str = "claude-sonnet-4-20250514",
    headless: bool = False,
    dry_run: bool = False,
) -> list[ApplicationResult]:
    """Execute the full 3-phase pipeline for all jobs."""

    # Phase 1: Scrape job descriptions
    console.print(f"\n[bold blue]Phase 1:[/] Scraping {len(jobs)} job descriptions...")
    job_descriptions = await scrape_all_jobs(jobs, max_parallel=5, model_name=model_name)
    console.print(f"  Scraped {len(job_descriptions)}/{len(jobs)} successfully")

    # Phase 2: Generate cover letters
    console.print(f"\n[bold blue]Phase 2:[/] Generating cover letters...")
    jd_pairs = [(url, jd) for url, jd in job_descriptions.items()]
    cover_letters = await generate_all_cover_letters(jd_pairs, profile, model_name=model_name)
    console.print(f"  Generated {sum(1 for v in cover_letters.values() if v)}/{len(jobs)} letters")

    if dry_run:
        console.print("\n[yellow]Dry run — skipping Phase 3 (form filling)[/]")
        return []

    # Phase 3: Fill forms with bounded parallelism
    console.print(f"\n[bold blue]Phase 3:[/] Filling {len(jobs)} applications (max {max_parallel} parallel)...")
    semaphore = asyncio.Semaphore(max_parallel)

    async def bounded_apply(job: JobTarget, idx: int) -> ApplicationResult:
        async with semaphore:
            console.print(f"  Agent {idx}: {job.company or job.url[:50]}...")
            max_steps = 50 if job.notes and "workday" in job.notes.lower() else 30
            result = await apply_to_job(
                job=job,
                profile=profile,
                cover_letter=cover_letters.get(job.url, ""),
                agent_id=idx,
                model_name=model_name,
                max_steps=max_steps,
                headless=headless,
            )
            icon = "✅" if result.status == "filled" else "❌"
            console.print(f"  Agent {idx}: {icon} {result.status}")
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
            final_results.append(ApplicationResult(
                job_url=jobs[i].url,
                status="error",
                error=str(r),
            ))

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
    """Print a nice summary table."""
    table = Table(title="Application Results")
    table.add_column("Company", style="cyan")
    table.add_column("Position", style="white")
    table.add_column("Status", style="green")
    table.add_column("Error", style="red", max_width=40)

    for r in results:
        status_style = "green" if r.status == "filled" else "red"
        table.add_row(
            r.company or "—",
            r.position or "—",
            f"[{status_style}]{r.status}[/]",
            (r.error or "")[:40],
        )

    console.print(table)
    filled = sum(1 for r in results if r.status == "filled")
    console.print(f"\n[bold]Done: {filled}/{len(results)} filled successfully[/]")
