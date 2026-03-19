# Job Application Agent

Parallel browser agents that fill job applications using browser-use + Claude Sonnet 4.6.

See @README.md for overview. See @docs/architecture.md for design details.

## Tech Stack

- Python 3.11+ managed exclusively by `uv` (NEVER use pip, poetry, or conda)
- browser-use 0.12+ — LLM-driven browser automation via Playwright
- langchain-anthropic — Claude as the LLM backbone for browser agents
- anthropic SDK — direct API calls for cover letter generation with prompt caching
- pydantic v2 — all data models
- ruff — linting and formatting (NEVER use black, flake8, pylint, isort)

## Commands

```bash
uv run python run.py                    # Run all applications
uv run python run.py --dry-run          # Validate config, generate cover letters only
uv run python run.py --max-parallel 3   # Override concurrency cap
uv run python run.py --job-index 2      # Debug single job by index
uv run pytest tests/ -x                 # Run tests (stop on first failure)
uv run ruff check src/ tests/           # Lint
uv run ruff format src/ tests/          # Format
```

## Architecture

3-phase pipeline per application (separate LLM calls for token efficiency):
1. **Scrape** (`job_parser.py`) — headless browser, extract job description → JSON
2. **Cover Letter** (`cover_letter.py`) — Anthropic API with prompt caching
3. **Fill Form** (`agent.py`) — browser-use agent fills application, stops before submit

Orchestrator (`orchestrator.py`) runs all phases with semaphore-bounded concurrency.

## Project Layout

```
config/profile.json        — Structured resume data (Pydantic-validated)
config/jobs.json            — [{url, company?, position?, credentials_key?}]
config/credentials.json     — Login creds per site (GITIGNORED)
templates/cover_letter.txt  — System prompt for cover letter generation
src/profile.py              — Pydantic models: Profile, JobTarget, ApplicationResult
src/job_parser.py           — Phase 1: scrape job descriptions
src/cover_letter.py         — Phase 2: generate tailored cover letters
src/agent.py                — Phase 3: browser agent fills one application
src/orchestrator.py         — Parallel runner with progress display
src/utils.py                — Logging, screenshots, retry decorator
run.py                      — CLI entry point with argparse
tests/                      — Mirrors src/ structure
```

## Python Style

- Python 3.11+ syntax: `list[str]` not `List[str]`, `str | None` not `Optional[str]`
- Type hints on ALL function signatures, including return types
- Pydantic v2 BaseModel for every data structure that crosses function boundaries
- async/await everywhere — browser-use is fully async
- f-strings only. Never `.format()` or `%`
- Use `pathlib.Path` for all file paths, never `os.path`

## Code Readability — IMPORTANT

- **Max 300 lines per file.** If a file approaches 300 lines, split it. No exceptions
- **Max 40 lines per function.** Extract helpers aggressively. A function should do one thing
- **Docstrings on every public function.** One-liner is fine for simple functions. For complex logic, add a brief description of the approach
- **Comments explain WHY, not WHAT.** Don't comment obvious code. Do explain non-obvious decisions, workarounds, and magic numbers
- **Blank line between logical blocks** inside functions. Group related lines, then separate with a blank line — like paragraphs in prose
- **Descriptive variable names.** `job_description` not `jd`. `cover_letter_text` not `cl`. No single-letter variables except `i` in simple loops
- **No nested functions deeper than 2 levels.** If you need a third level, extract to a helper
- **No bare `except:`.** Always catch specific exceptions. At minimum `except Exception as e:`
- **Constants at module top** with UPPER_SNAKE_CASE. No magic numbers buried in logic

## Package Management

- Install deps: `uv add <package>`
- Remove deps: `uv remove <package>`
- Sync after pull: `uv sync`
- Run anything: `uv run <command>` (never bare `python` or `pip`)

## Linting & Formatting

Run after EVERY code change:
```bash
uv run ruff format src/ tests/
uv run ruff check src/ tests/ --fix
```
If ruff reports errors, fix them before moving on. Do not leave unfixed warnings.

## Testing

- Use `pytest` with `pytest-asyncio` for async tests
- Tests in `tests/` mirroring `src/` structure
- Run single test: `uv run pytest tests/test_profile.py -x`
- Every new function with logic gets at least one test
- Mock external APIs (Anthropic, browser-use) in tests — never make real API calls

## Git Workflow

- Commit after each logical change, not after a full feature
- Commit messages: imperative mood, under 72 chars. e.g. "Add retry logic to form filler"
- NEVER auto-commit. Only commit when explicitly asked
- NEVER commit: `.env`, `config/credentials.json`, `config/profile.json`, `browser_profiles/`

## CRITICAL PROJECT RULES

- Browser agents **click Submit/Apply** after filling all fields — this is fully automated
- **NEVER hardcode credentials** in source code — always read from config/credentials.json
- `temperature=0` for all browser agents (deterministic form filling)
- `max_steps=30` default for agents (50 for Workday multi-page forms)
- Use **Sonnet 4.6** for all agents — Opus is unnecessary overhead for form filling
- Resume path in profile.json MUST be an **absolute path** for Playwright file upload
- Mark system prompt + profile with `cache_control: {"type": "ephemeral"}` in cover letter API calls

## Error Handling

- Wrap every `agent.run()` in try/except, log the error, save a screenshot on failure
- Save ALL results to `output/results.json` regardless of success or failure
- Use `save_conversation_path` on every browser agent for post-mortem debugging
- Retry failed applications once with `max_steps=50` before marking as failed
- Never silently swallow exceptions. Always log what went wrong and where

## Before Declaring Any Task Done

1. Run `uv run ruff format src/ tests/`
2. Run `uv run ruff check src/ tests/`
3. Run `uv run pytest tests/ -x`
4. Verify no file exceeds 300 lines: `find src/ -name '*.py' | xargs wc -l | sort -n`
