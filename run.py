"""CLI entry point for the job application agent."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

from src.orchestrator import run_all_applications
from src.profile import JobTarget, Profile

console = Console()


def load_config() -> tuple[Profile, list[JobTarget]]:
    """Load and validate profile + jobs config."""
    profile_path = Path("config/profile.json")
    jobs_path = Path("config/jobs.json")

    if not profile_path.exists():
        console.print("[red]Missing config/profile.json[/]")
        console.print("Copy config/profile.example.json -> config/profile.json and fill in your data")
        sys.exit(1)

    if not jobs_path.exists():
        console.print("[red]Missing config/jobs.json[/]")
        console.print("Copy config/jobs.example.json -> config/jobs.json and add your target jobs")
        sys.exit(1)

    profile = Profile(**json.loads(profile_path.read_text()))
    jobs = [JobTarget(**j) for j in json.loads(jobs_path.read_text())]

    # Validate resume exists
    if not Path(profile.resume_path).exists():
        console.print(f"[red]Resume not found: {profile.resume_path}[/]")
        console.print("Update resume_path in config/profile.json to an absolute path")
        sys.exit(1)

    return profile, jobs


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Parallel job application agent")
    parser.add_argument("--dry-run", action="store_true", help="Validate config + generate cover letters only")
    parser.add_argument("--max-parallel", type=int, default=3, help="Max concurrent browser agents")
    parser.add_argument("--job-index", type=int, default=None, help="Run single job by index (for debugging)")
    parser.add_argument("--headless", action="store_true", help="Run browsers in headless mode")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-20250514", help="Model name")
    args = parser.parse_args()

    profile, jobs = load_config()

    if args.job_index is not None:
        if args.job_index >= len(jobs):
            console.print(f"[red]Job index {args.job_index} out of range (0-{len(jobs)-1})[/]")
            sys.exit(1)
        jobs = [jobs[args.job_index]]

    console.print(f"[bold]Job Application Agent[/]")
    console.print(f"  Jobs: {len(jobs)}")
    console.print(f"  Parallel: {args.max_parallel}")
    console.print(f"  Model: {args.model}")
    console.print(f"  Dry run: {args.dry_run}")
    console.print(f"  Est. cost: ~${len(jobs) * 0.02:.2f}")
    console.print()

    if not args.dry_run:
        confirm = input("Press Enter to start (Ctrl+C to cancel)... ")

    results = asyncio.run(
        run_all_applications(
            jobs=jobs,
            profile=profile,
            max_parallel=args.max_parallel,
            model_name=args.model,
            headless=args.headless,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
