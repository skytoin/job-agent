# Job Application Agent

Parallel browser agents that fill job applications using browser-use + Claude API.

## Tech Stack

- Python 3.11+ managed by `uv`
- browser-use (v0.12+) — LLM-driven browser automation via Playwright
- langchain-anthropic — Claude as LLM for browser agents
- anthropic SDK — direct API for cover letter generation with prompt caching
- pydantic v2 — all data models

## Commands

```bash
uv run python run.py                    # Run all applications
uv run python run.py --dry-run          # Validate config only
uv run python run.py --max-parallel 3   # Override concurrency
uv run python run.py --job-index 2      # Debug single job
uv run pytest tests/ -x                 # Tests
uv run ruff check src/                  # Lint
uv run ruff format src/                 # Format
```

## Architecture

3-phase pipeline per job (separate LLM calls for token efficiency):
1. **Scrape** (`job_parser.py`) — headless, extract JD → JSON
2. **Cover Letter** (`cover_letter.py`) — Anthropic API with prompt caching
3. **Fill Form** (`agent.py`) — browser-use agent, stops before submit

See @docs/architecture.md for detailed design.

## Project Layout

```
config/profile.json       — Structured resume (Pydantic-validated)
config/jobs.json           — [{url, company?, position?, credentials_key?}]
config/credentials.json    — Login creds (GITIGNORED)
src/profile.py             — Pydantic models
src/job_parser.py          — Phase 1: scrape
src/cover_letter.py        — Phase 2: generate
src/agent.py               — Phase 3: fill
src/orchestrator.py        — Parallel runner
src/utils.py               — Logging, screenshots, retries
run.py                     — Entry point
```

## Code Rules

- Python 3.11+ syntax: `list[str]`, `str | None`, no typing imports
- Type hints on ALL function signatures
- Pydantic v2 for all data structures
- async/await everywhere — browser-use is fully async
- f-strings only, never .format()
- Functions under 40 lines

## CRITICAL

- **NEVER click Submit/Apply** in browser agents — stop before final button
- **NEVER hardcode credentials** — read from config/credentials.json
- **NEVER commit** .env, credentials.json, or browser_profiles/
- `temperature=0` for browser agents (deterministic)
- `max_steps=30` default (50 for Workday multi-page)
- Use **Sonnet 4.6** for agents, not Opus
- Resume path must be **absolute** for Playwright file upload
- Mark system prompt + profile with `cache_control` in cover letter calls

## Error Handling

- Wrap every agent.run() in try/except, log error + take screenshot
- Save ALL results to output/results.json
- Use save_conversation_path on every agent
- Retry failed apps once with max_steps=50
