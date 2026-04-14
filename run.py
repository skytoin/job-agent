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
        console.print(
            "Copy config/profile.example.json -> config/profile.json and fill in your data"
        )
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
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate config + generate cover letters only"
    )
    parser.add_argument("--max-parallel", type=int, default=3, help="Max concurrent browser agents")
    parser.add_argument(
        "--job-index", type=int, default=None, help="Run single job by index (for debugging)"
    )
    parser.add_argument("--headless", action="store_true", help="Run browsers in headless mode")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-6", help="Model name")
    parser.add_argument(
        "--force-agent", action="store_true", help="Skip direct fill, always use browser-use agent"
    )
    parser.add_argument(
        "--skyvern-only",
        action="store_true",
        help="Send every job directly to Skyvern (no browser-use). Pure Skyvern cost mode.",
    )
    parser.add_argument(
        "--no-skyvern",
        action="store_true",
        help="Disable Skyvern entirely. Pure browser-use mode for Anthropic cost comparison.",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Wipe the Layer 0 learned-answers cache before running.",
    )
    args = parser.parse_args()

    if args.clear_cache:
        from src.layer0_cache import Layer0Cache

        cache = Layer0Cache()
        before = len(cache)
        cache.clear()
        console.print(f"[yellow]Cleared Layer 0 cache ({before} entries)[/]")

    if args.skyvern_only and args.no_skyvern:
        console.print("[red]--skyvern-only and --no-skyvern are mutually exclusive.[/]")
        sys.exit(1)

    # Default is "off" — Skyvern is dormant after cost evaluation rejected it.
    # Pass --skyvern-only explicitly if you ever want to try it again on a single job.
    if args.skyvern_only:
        skyvern_mode = "only"
    else:
        skyvern_mode = "off"

    profile, jobs = load_config()

    if args.job_index is not None:
        if args.job_index >= len(jobs):
            console.print(f"[red]Job index {args.job_index} out of range (0-{len(jobs) - 1})[/]")
            sys.exit(1)
        jobs = [jobs[args.job_index]]

    console.print("[bold]Job Application Agent[/]")
    console.print(f"  Jobs: {len(jobs)}")
    console.print(f"  Parallel: {args.max_parallel}")
    console.print(f"  Model: {args.model}")
    console.print(f"  Mode: {skyvern_mode}")
    console.print(f"  Dry run: {args.dry_run}")
    console.print(f"  Est. cost: ~${len(jobs) * 0.02:.2f}")
    console.print()

    if not args.dry_run:
        input("Press Enter to start (Ctrl+C to cancel)... ")

    asyncio.run(
        run_all_applications(
            jobs=jobs,
            profile=profile,
            max_parallel=args.max_parallel,
            model_name=args.model,
            headless=args.headless,
            dry_run=args.dry_run,
            force_agent=args.force_agent,
            skyvern_mode=skyvern_mode,
        )
    )


if __name__ == "__main__":
    main()
