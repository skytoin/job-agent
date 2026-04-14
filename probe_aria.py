"""Diagnostic: dump the ARIA snapshot of a job application form.

Run:
    uv run python probe_aria.py <url>

Opens the given URL in a real Chromium window, clicks any visible "Apply"
button, waits for the form to render, and saves TWO views of the form's
accessibility tree:
  1. ``aria_<ts>.yaml`` — human-readable YAML from ``aria_snapshot()``
  2. ``aria_<ts>.json`` — programmatic dict tree from ``page.accessibility.snapshot()``

The JSON is what production code (``src/aria_extractor.py``) consumes.
The YAML is for human inspection and as a regression reference.

Does NOT modify any production code. Safe to run as many times as needed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

APPLY_BUTTON_SELECTORS = [
    "a:has-text('Apply for this job')",
    "a:has-text('Apply Now')",
    "button:has-text('Apply for this job')",
    "button:has-text('Apply Now')",
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "[role='tab']:has-text('Application')",
]

FORM_SELECTORS = [
    "form",
    "[role='form']",
    "main",
    "[data-testid*='application']",
]


async def _click_apply_if_present(page: Page) -> bool:
    """Try each known Apply-button selector; return True on first success."""
    for sel in APPLY_BUTTON_SELECTORS:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                print(f"  clicked Apply via {sel}")
                await page.wait_for_timeout(3000)
                return True
        except Exception:
            continue
    print("  no Apply button found — assuming we're already on the form")
    return False


async def _find_form_locator(page: Page):
    """Return a locator scoped to the application form, or to body as fallback."""
    for sel in FORM_SELECTORS:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0:
                print(f"  scoping ARIA snapshot to: {sel}")
                return locator
        except Exception:
            continue
    print("  no form container found — scoping to body")
    return page.locator("body")


async def probe(url: str) -> None:
    """Open ``url``, save YAML + JSON snapshots of its application form."""
    output_dir = Path("output/aria_probes")
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    yaml_file = output_dir / f"aria_{ts}.yaml"
    json_file = output_dir / f"aria_{ts}.json"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        print(f"\nNavigating: {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        await _click_apply_if_present(page)
        await page.wait_for_timeout(2000)

        form_locator = await _find_form_locator(page)

        print("\nExtracting YAML aria_snapshot()...")
        try:
            yaml_snapshot = await form_locator.aria_snapshot()
        except Exception as e:
            print(f"  ERROR: aria_snapshot() raised: {e}")
            await browser.close()
            sys.exit(1)

        yaml_file.write_text(yaml_snapshot, encoding="utf-8")
        print(f"  saved YAML ({len(yaml_snapshot)} chars) -> {yaml_file}")

        print("\nExtracting dict accessibility.snapshot()...")
        try:
            dict_snapshot = await page.accessibility.snapshot(interesting_only=True)
        except Exception as e:
            print(f"  ERROR: page.accessibility.snapshot() raised: {e}")
            dict_snapshot = None

        if dict_snapshot is not None:
            json_file.write_text(json.dumps(dict_snapshot, indent=2), encoding="utf-8")
            print(f"  saved JSON dict -> {json_file}")
        else:
            print("  no JSON snapshot (snapshot returned None)")

        print("\n===== ARIA YAML SNAPSHOT =====")
        print(yaml_snapshot)
        print("===== END =====\n")

        print("Browser will stay open for 10 seconds so you can inspect it...")
        await page.wait_for_timeout(10000)
        await browser.close()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python probe_aria.py <url>")
        sys.exit(1)

    url = sys.argv[1]
    asyncio.run(probe(url))


if __name__ == "__main__":
    main()
