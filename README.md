# Job Application Agent

Parallel AI agents that fill job applications autonomously. You give it a list of URLs and your resume — it scrapes job descriptions, generates tailored cover letters, and fills every form field. Stops before submit so you stay in control.

## Quick Start

```bash
# 1. Install uv (if needed)
# Windows:
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Mac/Linux:
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Initialize git repo
cd job-agent
git init
git add .
git commit -m "Initial scaffold"

# 3. Create venv + install dependencies (uv does both in one command)
uv sync --all-extras
uv run playwright install chromium

# 4. Commit the lockfile
git add uv.lock
git commit -m "Add uv.lock"

# 5. Configure
cp .env.example .env                              # Add your ANTHROPIC_API_KEY
cp config/profile.example.json config/profile.json  # Add your resume data
cp config/jobs.example.json config/jobs.json        # Add job URLs

# 6. Run
uv run python run.py --dry-run          # Test config
uv run python run.py --job-index 0      # Test one job
uv run python run.py --max-parallel 3   # Run all jobs
```

See [docs/setup-guide.md](docs/setup-guide.md) for detailed instructions.

## How It Works

Three-phase pipeline per job application:

1. **Scrape** — Headless browser extracts job description
2. **Cover Letter** — Claude API generates a tailored letter (with prompt caching)
3. **Fill Form** — Browser agent fills the application, stops before submit

Cost: ~$0.02 per application. 100 applications ≈ $2.

## Stack

- Python 3.11+ / uv
- [browser-use](https://github.com/browser-use/browser-use) — LLM-driven browser automation
- Claude Sonnet 4.6 — fast, cheap, accurate for form filling
- Anthropic API with prompt caching — cover letter generation
