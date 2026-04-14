"""Layer 1 and Layer 2 helpers for dropdown handling in ``direct_fill.py``.

Layer 0 lives in ``src/dropdown_templates.py`` (pre-LLM template matching).
This module holds the two fallback strategies that kick in AFTER the main
LLM mapping call:

  - ``haiku_dropdown_retry``: tiny Haiku call that picks from the real option
    list when the main LLM returned a value no option actually contains.
  - ``haiku_patch_bad_dropdown_values``: orchestrates ``haiku_dropdown_retry``
    across every unmatched dropdown in the mapping.
  - ``keyboard_select_fallback``: Layer 2 — focus + type + Enter last-ditch.
"""

from __future__ import annotations

import asyncio
import logging

import anthropic
from playwright.async_api import Page

from src.dropdown_match import fuzzy_pick_option

logger = logging.getLogger("job-agent")

HAIKU_MODEL = "claude-haiku-4-5-20251001"

DROPDOWN_TYPES = {"select", "radio_group", "button_group", "combobox"}


async def keyboard_select_fallback(page: Page, selector: str, value: str) -> bool:
    """Layer 2: focus the field and type the value's first chars + Enter.

    Works as a last-ditch attempt on stubborn native ``<select>`` elements:
    most browsers jump to the option whose visible text starts with the typed
    characters. Returns True if the key sequence ran without exceptions — the
    caller treats this as best-effort and cannot verify success without
    re-reading the DOM.
    """
    try:
        el = await page.query_selector(selector)
        if not el:
            return False
        await el.focus()
        await page.keyboard.type(value[:4], delay=80)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


async def haiku_dropdown_retry(field: dict, target_value: str) -> str | None:
    """Layer 1: ask Claude Haiku to pick the best option for ONE dropdown.

    Keeps the prompt tiny (~200 input tokens + ~5 output tokens) so the
    per-retry cost is ~$0.0002 vs a full Sonnet remapping at ~$0.005.
    """
    raw = field.get("options") or []
    options = [o.get("text", "") if isinstance(o, dict) else str(o) for o in raw]
    options = [o for o in options if o]
    if not options:
        return None

    label = field.get("label", "")
    prompt = (
        f"Pick exactly one option from this list that best matches the answer "
        f'"{target_value}" for the form field labelled "{label}".\n\n'
        "Options:\n" + "\n".join(f"- {o}" for o in options) + "\n\n"
        "Respond with ONLY the exact text of the chosen option, nothing else."
    )

    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        chosen = resp.content[0].text.strip().strip('"').strip("'")
    except Exception as e:
        logger.warning(f"  Layer 1 Haiku retry failed: {e}")
        return None

    for opt in options:
        if opt.lower() == chosen.lower():
            return opt

    # Haiku may paraphrase slightly — fuzzy match to the real option list.
    return fuzzy_pick_option(chosen, options)


async def haiku_patch_bad_dropdown_values(
    fields: list[dict],
    mapping: dict[str, str],
    unmatched: list[dict],
) -> dict[str, str]:
    """For each dropdown in ``mapping`` whose value isn't a real option,
    retry it through ``haiku_dropdown_retry``.

    Only inspects fields in ``unmatched`` (fields the main LLM actually saw);
    template-pre-filled fields already passed fuzzy-match and are trusted.
    """
    by_id = {(f.get("id") or f.get("name")): f for f in fields}
    unmatched_ids = {(f.get("id") or f.get("name")) for f in unmatched}

    patched = dict(mapping)
    for field_id, value in mapping.items():
        field = by_id.get(field_id)
        if field is None or field.get("type") not in DROPDOWN_TYPES:
            continue
        if field_id not in unmatched_ids:
            continue

        raw = field.get("options") or []
        option_texts = [o.get("text", "") if isinstance(o, dict) else str(o) for o in raw]
        option_texts = [o for o in option_texts if o]
        if not option_texts:
            continue

        if any(opt.lower() == value.lower() for opt in option_texts):
            continue  # already a valid option

        patched_value = await haiku_dropdown_retry(field, value)
        if patched_value:
            logger.info(
                f"  Layer 1: Haiku patched '{field.get('label', '')[:40]}' "
                f"'{value[:30]}' -> '{patched_value[:30]}'"
            )
            patched[field_id] = patched_value

    return patched
