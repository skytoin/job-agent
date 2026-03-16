"""Phase 2: Generate tailored cover letters via Anthropic API with prompt caching."""

import asyncio
from pathlib import Path

import anthropic

from src.profile import JobDescription, Profile


TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "cover_letter.txt"


def _load_system_prompt() -> str:
    return TEMPLATE_PATH.read_text()


async def generate_cover_letter(
    profile: Profile,
    job: JobDescription,
    model_name: str = "claude-sonnet-4-20250514",
) -> str:
    """Generate a cover letter tailored to the job description.

    Uses prompt caching: the system prompt + profile data are cached after the
    first call, reducing input cost to ~10% for subsequent jobs.
    """
    client = anthropic.AsyncAnthropic()

    system_prompt = _load_system_prompt()
    profile_compact = profile.to_compact_str()

    # Cache the system prompt + profile (identical across all jobs)
    cached_block = f"{system_prompt}\n\nAPPLICANT PROFILE:\n{profile_compact}"

    response = await client.messages.create(
        model=model_name,
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": cached_block,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Write a cover letter for the position of {job.position} "
                    f"at {job.company}.\n\n"
                    f"JOB DESCRIPTION:\n{job.description[:2000]}"
                ),
            }
        ],
    )

    return response.content[0].text


async def generate_all_cover_letters(
    jobs: list[tuple[str, JobDescription]],
    profile: Profile,
    output_dir: Path = Path("output/cover_letters"),
    model_name: str = "claude-sonnet-4-20250514",
) -> dict[str, str]:
    """Generate cover letters for all jobs. Returns {url: cover_letter_text}."""
    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, str] = {}

    for url, jd in jobs:
        try:
            letter = await generate_cover_letter(profile, jd, model_name)
            results[url] = letter

            # Save to file
            safe_name = jd.company.replace(" ", "_").lower()[:30] or "unknown"
            path = output_dir / f"{safe_name}_cover_letter.txt"
            path.write_text(letter)

        except Exception as e:
            print(f"  Cover letter failed for {jd.company}: {e}")
            results[url] = ""

    return results
