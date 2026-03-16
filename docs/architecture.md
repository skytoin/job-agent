# Architecture

## 3-Phase Pipeline

```
                    ┌─────────────────────────────────────┐
                    │          config/jobs.json            │
                    │   [{url, company, position, ...}]    │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │      Phase 1: Scrape (parallel)      │
                    │  headless browser → extract JD text   │
                    │  ~500-1000 tokens per job             │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │   Phase 2: Cover Letter (API only)   │
                    │  Anthropic API + prompt caching       │
                    │  ~2000 tokens first, ~300 cached      │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │   Phase 3: Fill Form (parallel)      │
                    │  browser-use agents, bounded by       │
                    │  semaphore (default max=3)            │
                    │  ~3000-8000 tokens per job            │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │       output/results.json            │
                    │  + screenshots + cover letters        │
                    └─────────────────────────────────────┘
```

## Why 3 Phases?

Each phase has a different context requirement. Separating them means:

1. **Scraping** doesn't pollute the form-filling agent's context with irrelevant page HTML
2. **Cover letters** use prompt caching — the profile/system prompt is sent once at full price, then at 10% for every subsequent job
3. **Form filling** gets only the data it needs: profile fields + cover letter text

Combined, this uses ~50-70% fewer tokens than a single agent doing all three.

## Parallelism Model

- Phases 1 & 2: high parallelism (5-10), lightweight operations
- Phase 3: bounded by asyncio.Semaphore (default 3), each spawns a Chromium instance (~400MB RAM)

```python
semaphore = asyncio.Semaphore(max_parallel)
async def bounded_apply(job, idx):
    async with semaphore:
        return await apply_to_job(...)
results = await asyncio.gather(*[bounded_apply(j, i) for i, j in enumerate(jobs)])
```

## Token Budget Per Application

| Phase | Input tokens | Output tokens | Cost (Sonnet) |
|-------|-------------|--------------|---------------|
| Scrape JD | ~800 | ~200 | $0.001 |
| Cover letter (cached) | ~300 cached + ~500 new | ~300 | $0.002 |
| Form filling (30 steps) | ~4000 | ~2000 | $0.012 |
| **Total** | | | **~$0.015** |

## Browser Profile Isolation

Each parallel agent gets its own browser profile directory:
```
browser_profiles/
├── agent-0/    ← Cookies, localStorage, session for agent 0
├── agent-1/
└── agent-2/
```

This prevents session conflicts. Pre-login trick: manually open a browser with one of these profiles, log in to a site, close it. The agent inherits the session.

## Safety

The task prompt ends with an explicit "DO NOT click Submit" instruction. The agent is configured with `max_steps=30` (or 50 for Workday) to prevent infinite loops. All conversations are logged to `output/logs/agent-N/` for debugging.
