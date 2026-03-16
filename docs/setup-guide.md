# Setup Guide — Job Application Agent

Complete step-by-step instructions to get from zero to running parallel job application agents.

---

## Prerequisites

- **Windows 11** (your machine)
- **Python 3.11+** installed
- **Anthropic API key** with credit balance
- **Your resume** as a PDF file

---

## Step 1: Install uv (if you don't have it)

uv is a Rust-based Python package manager — 10-100x faster than pip. It handles virtual environments, Python versions, and dependencies in one tool.

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Mac/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Verify:**
```bash
uv --version
# Should print something like: uv 0.10.x
```

---

## Step 2: Initialize the project — git repo + virtual environment

This is the first thing you do with any new Python project. Order matters.

```bash
# Navigate to the project
cd job-agent

# ──────────────────────────────────────────
# 2a. Initialize git FIRST (before anything)
# ──────────────────────────────────────────
git init
git add .
git commit -m "Initial scaffold: project structure, CLAUDE.md, config templates"

# ──────────────────────────────────────────
# 2b. Create venv + install all dependencies
# ──────────────────────────────────────────
# uv sync does THREE things in one command:
#   1. Creates .venv/ in your project root (if it doesn't exist)
#   2. Installs the correct Python version (3.11+)
#   3. Installs all dependencies from pyproject.toml into .venv/
uv sync

# To also install dev dependencies (pytest, ruff):
uv sync --all-extras

# ──────────────────────────────────────────
# 2c. Install Playwright's Chromium browser
# ──────────────────────────────────────────
# browser-use needs a real browser binary to control
uv run playwright install chromium

# ──────────────────────────────────────────
# 2d. Verify everything works
# ──────────────────────────────────────────
uv run python -c "from browser_use import Agent; print('✅ browser-use OK')"
uv run python -c "import anthropic; print('✅ anthropic SDK OK')"
uv run python -c "from src.profile import Profile; print('✅ models OK')"

# ──────────────────────────────────────────
# 2e. Commit the lockfile
# ──────────────────────────────────────────
# uv.lock pins exact versions of every dependency — commit it so
# the project is reproducible on any machine
git add uv.lock
git commit -m "Add uv.lock with pinned dependencies"
```

**What `uv sync` just created:**
```
job-agent/
├── .venv/              ← Virtual environment (auto-created, gitignored)
│   ├── bin/python      ← Isolated Python interpreter
│   └── lib/            ← All installed packages live here
├── uv.lock             ← Pinned dependency versions (COMMIT THIS)
└── pyproject.toml      ← Your dependency declarations (already existed)
```

**Why `uv run` instead of activating the venv?**
`uv run <command>` automatically uses the .venv without you needing to `source .venv/bin/activate` first. It's faster and you never forget to activate. But if you prefer the traditional way:

```bash
# Traditional activation (optional, not needed with uv run)
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate
# Then just use python directly:
python run.py --dry-run
```

---

## Step 3: Configure your profile

```bash
cp config/profile.example.json config/profile.json
```

Edit `config/profile.json` with your real data. **Critical fields:**

- `resume_path`: Must be an **absolute path** like `C:/Users/You/Documents/resume.pdf`
  (Playwright needs the full path for file upload)
- `email`, `phone`: Used by every application
- `skills`: List your top 15 — agents match these to dropdown options
- `salary_expectation`: Format as "150000-180000" or leave empty to skip

---

## Step 4: Configure your job list

```bash
cp config/jobs.example.json config/jobs.json
```

Edit `config/jobs.json` — add real job URLs:

```json
[
    {
        "url": "https://boards.greenhouse.io/company/jobs/12345",
        "company": "CoolStartup",
        "position": "ML Engineer"
    },
    {
        "url": "https://bigcorp.wd5.myworkdayjobs.com/careers/job/apply",
        "company": "BigCorp",
        "position": "Senior ML Engineer",
        "notes": "Workday multi-page, use max_steps=50",
        "credentials_key": "workday"
    }
]
```

---

## Step 5: Set up credentials (if jobs require login)

```bash
cp config/credentials.example.json config/credentials.json
```

Edit with real credentials. This file is gitignored and never committed.

**Pro tip — pre-login instead:** For sites you already have accounts on, open the browser profile manually, log in, then close. The agent inherits your session:

```bash
# Open a browser with the profile the agent will use
uv run python -c "
import asyncio
from playwright.async_api import async_playwright

async def pre_login():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            './browser_profiles/agent-0',
            headless=False,
        )
        print('Log in to your accounts, then close the browser window.')
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto('https://the-job-site.com/login')
        input('Press Enter after you have logged in...')
        await ctx.close()

asyncio.run(pre_login())
"
```

---

## Step 6: Set your API key

```bash
cp .env.example .env
```

Edit `.env`:
```
ANTHROPIC_API_KEY=sk-ant-api03-your-real-key-here
```

---

## Step 7: Test with a single job (DRY RUN)

```bash
# Validate config + generate cover letters only (no browsers)
uv run python run.py --dry-run
```

This confirms:
- Profile loads correctly
- Jobs parse correctly
- Resume file exists
- Cover letters generate properly
- API key works

---

## Step 8: Test with a single job (REAL)

Pick one job you don't care about (or one you've already applied to):

```bash
uv run python run.py --job-index 0
```

Watch the browser window. The agent will:
1. Open the URL
2. Navigate to the application form
3. Fill fields one by one
4. Upload your resume
5. **Stop before submit**

If something goes wrong, check `output/logs/agent-0/` for the conversation trace.

---

## Step 9: Run all jobs in parallel

```bash
uv run python run.py --max-parallel 3
```

3 browser windows will open simultaneously. Each handles a different job.

After completion, check:
- `output/results.json` — status of every application
- `output/screenshots/` — pre-submit screenshots
- `output/cover_letters/` — generated cover letters

---

## Step 10: Review and submit

The agents deliberately stop before clicking Submit. For each completed application:

1. Open the browser profile: the form should still be filled
2. Review the filled data
3. Read the generated cover letter
4. Click Submit manually if everything looks good

---

## Troubleshooting

**"Chromium not found"**
```bash
uv run playwright install chromium
```

**"Resume file not found"**
Update `resume_path` in profile.json to use an absolute path with forward slashes:
`C:/Users/You/Documents/resume.pdf`

**Agent gets stuck in a loop**
Increase max_steps or add more specific instructions in the job's `notes` field.

**Rate limit errors**
Reduce `--max-parallel` to 2, or wait a few minutes. Sonnet has generous limits but 5+ parallel agents can hit them.

**CAPTCHA appears**
You need to solve it manually. Pre-login in the browser profile helps avoid CAPTCHAs on subsequent visits.

**Workday forms timing out**
Workday is slow. Use `--job-index N` for Workday jobs and increase patience. The orchestrator already detects "workday" in the notes field and bumps max_steps to 50.
