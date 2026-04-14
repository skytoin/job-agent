"""Phase 2: Generate tailored cover letters via LLM API.

Supports Anthropic (with prompt caching) and OpenAI.
"""

from pathlib import Path

from src.llm import is_openai_model
from src.profile import JobDescription, Profile

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "cover_letter.txt"


def _load_system_prompt() -> str:
    return TEMPLATE_PATH.read_text()


async def _generate_anthropic(
    system_block: str,
    user_message: str,
    model_name: str,
) -> str:
    """Generate via Anthropic API with prompt caching."""
    import anthropic

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model=model_name,
        max_tokens=500,
        system=[
            {
                "type": "text",
                "text": system_block,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


async def _generate_openai(
    system_block: str,
    user_message: str,
    model_name: str,
) -> str:
    """Generate via OpenAI API."""
    import openai

    client = openai.AsyncOpenAI()
    response = await client.chat.completions.create(
        model=model_name,
        max_completion_tokens=500,
        messages=[
            {"role": "system", "content": system_block},
            {"role": "user", "content": user_message},
        ],
    )
    return response.choices[0].message.content or ""


async def generate_cover_letter(
    profile: Profile,
    job: JobDescription,
    model_name: str = "claude-sonnet-4-6",
) -> str:
    """Generate a cover letter tailored to the job description."""
    system_prompt = _load_system_prompt()
    profile_compact = profile.to_compact_str()
    system_block = f"{system_prompt}\n\nAPPLICANT PROFILE:\n{profile_compact}"

    user_message = (
        f"Write a cover letter for the position of {job.position} "
        f"at {job.company}.\n\n"
        f"JOB DESCRIPTION:\n{job.description[:2000]}"
    )

    if is_openai_model(model_name):
        return await _generate_openai(system_block, user_message, model_name)
    return await _generate_anthropic(system_block, user_message, model_name)


async def generate_all_cover_letters(
    jobs: list[tuple[str, JobDescription]],
    profile: Profile,
    output_dir: Path = Path("output/cover_letters"),
    model_name: str = "claude-sonnet-4-6",
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
