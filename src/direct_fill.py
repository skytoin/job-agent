"""Direct fill: extract form fields via JS, map with 1 LLM call, fill via JS.

Cost: ~$0.03-0.05 per application (vs ~$0.30 with browser-use agent).
Falls back to browser-use agent if the form is too complex.

Layered dropdown handling:
  - Layer 0: template + fuzzy match (``src/dropdown_templates.py``)
  - Layer 1: Haiku mini-LLM retry for unresolved dropdowns
  - Layer 2: keyboard-typing fallback inside fill_fields_js
"""

import asyncio
import json
import logging
from pathlib import Path

import anthropic
import openai
from playwright.async_api import Page

from src.aria_extractor import extract_fields_from_aria
from src.aria_yaml_parser import parse_aria_yaml
from src.dropdown_layers import (
    haiku_patch_bad_dropdown_values,
    keyboard_select_fallback,
)
from src.dropdown_templates import apply_templates
from src.layer0_cache import Layer0Cache
from src.llm import is_openai_model
from src.profile import Profile

logger = logging.getLogger("job-agent")

# Domains that are always complex — skip direct fill
COMPLEX_DOMAINS = [
    "myworkdayjobs.com",
    "workday.com",
    "oraclecloud.com",
    "taleo.net",
    "icims.com",
    "phenom.com",
]

# JS to extract all form fields from the page
EXTRACT_FIELDS_JS = """
(() => {
    const fields = [];
    const seen = new Set();

    function getLabel(el) {
        // Try aria-label
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        // Try associated label
        const id = el.id;
        if (id) {
            const label = document.querySelector(`label[for="${id}"]`);
            if (label) return label.textContent.trim();
        }
        // Try parent label
        const parent = el.closest('label');
        if (parent) return parent.textContent.trim();
        // Try preceding sibling or nearby text
        const prev = el.previousElementSibling;
        if (prev && prev.tagName === 'LABEL') return prev.textContent.trim();
        // Try placeholder
        if (el.placeholder) return el.placeholder;
        // Try name attribute
        return el.name || el.id || '';
    }

    function getOptions(el) {
        if (el.tagName === 'SELECT') {
            return Array.from(el.options)
                .filter(o => o.value)
                .map(o => ({value: o.value, text: o.textContent.trim()}));
        }
        return [];
    }

    // Standard inputs
    document.querySelectorAll('input, select, textarea').forEach(el => {
        const type = el.type || el.tagName.toLowerCase();
        if (['hidden', 'submit', 'button', 'image'].includes(type)) return;
        if (el.closest('[style*="display: none"]')) return;
        const key = el.id || el.name || '';
        if (!key || seen.has(key)) return;
        seen.add(key);

        const field = {
            id: el.id || '',
            name: el.name || '',
            type: type,
            label: getLabel(el),
            required: el.required || el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            placeholder: el.placeholder || '',
        };

        if (type === 'radio') {
            const groupName = el.name;
            if (seen.has('radio_' + groupName)) return;
            seen.add('radio_' + groupName);
            const options = Array.from(document.querySelectorAll(
                `input[name="${groupName}"]`
            )).map(r => ({
                value: r.value,
                text: (r.labels?.[0]?.textContent || r.value).trim(),
                id: r.id,
            }));
            field.options = options;
            field.type = 'radio_group';
            field.name = groupName;
        } else if (type === 'checkbox') {
            field.checked = el.checked;
        } else if (el.tagName === 'SELECT') {
            field.options = getOptions(el);
        }

        fields.push(field);
    });

    // React-select comboboxes
    document.querySelectorAll('[role="combobox"]').forEach(el => {
        const key = el.id || el.getAttribute('aria-labelledby') || '';
        if (seen.has('combo_' + key)) return;
        seen.add('combo_' + key);
        fields.push({
            id: el.id || '',
            name: el.name || '',
            type: 'combobox',
            label: getLabel(el),
            required: el.getAttribute('aria-required') === 'true',
            value: el.value || '',
            placeholder: el.placeholder || '',
        });
    });

    // File inputs (for resume)
    document.querySelectorAll('input[type="file"]').forEach(el => {
        const key = el.id || el.name || 'file_' + fields.length;
        if (seen.has(key)) return;
        seen.add(key);
        fields.push({
            id: el.id || '',
            name: el.name || '',
            type: 'file',
            label: getLabel(el),
            required: el.required,
            accept: el.accept || '',
        });
    });

    // Buttons that look like Yes/No toggles
    document.querySelectorAll('button[type="button"]').forEach(el => {
        const text = el.textContent.trim();
        if (['Yes', 'No'].includes(text)) {
            const container = el.parentElement;
            const label = container?.previousElementSibling?.textContent?.trim() || '';
            const key = 'btn_' + label.slice(0, 30);
            if (seen.has(key)) return;
            seen.add(key);
            const siblings = Array.from(container.querySelectorAll('button'));
            fields.push({
                id: key,
                name: key,
                type: 'button_group',
                label: label,
                options: siblings.map(b => ({
                    text: b.textContent.trim(),
                    index: b.getAttribute('data-index') || '',
                })),
            });
        }
    });

    return fields;
})()
"""

# JS to fill a field by ID or name — takes a single object arg for Playwright
FILL_FIELD_JS = """
(args) => {
    const {fieldId, value} = args;
    let el = document.getElementById(fieldId);
    if (!el) el = document.querySelector('[name="' + fieldId + '"]');
    if (!el) return false;

    const nativeSetter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    )?.set || Object.getOwnPropertyDescriptor(
        window.HTMLTextAreaElement.prototype, 'value'
    )?.set;

    if (nativeSetter) {
        nativeSetter.call(el, value);
    } else {
        el.value = value;
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}
"""

MAPPING_PROMPT = """You are mapping applicant data to form fields. Given the fields and profile, \
return a JSON object mapping each field ID to the value to fill.

FORM FIELDS:
{fields_json}

APPLICANT PROFILE:
Name: {first_name} {last_name}
Email: {email}
Phone: {phone}
Location: {location}
Title: {current_title}
Company: {company}
Years of Experience: {years_exp}
Education: {education}
Experience: {experience}
Skills: {skills}
Work Authorization: {work_auth}
Sponsorship Required: {sponsorship}
Gender: {gender}
Hispanic/Latino: {hispanic}
Race/Ethnicity: {race}
Veteran Status: {veteran}
Disability: {disability}

COVER LETTER:
{cover_letter}

CRITICAL RULES (read carefully):
- Return ONLY valid JSON: {{"field_id": "value", ...}}
- **You MUST map every field marked `required: true`.** Required fields cannot
  be skipped — find the best answer from the profile, even if you have to
  infer (e.g. "How did you hear" -> "LinkedIn", "Hub Location" -> closest city
  to the applicant's location).
- For radio_group/button_group/checkbox_group fields: return the EXACT option
  text from the field's options list (e.g. "Yes", "No", "Male", "New York, NY").
- For select fields, return the option text (not value).
- For combobox location fields, return the city (e.g. "New York").
- For checkbox_group multi-select questions like "How did you hear about us":
  return the SINGLE best option text (don't return arrays).
- For file fields, return "UPLOAD_RESUME".
- For open-ended free-text questions, write 2-3 sentences tying ML/AI
  experience to the role.
- For EEO/demographic questions, pick the closest matching option text.
- Standard answers when in doubt:
  - "How did you hear" -> "LinkedIn"
  - "Will you require sponsorship" -> "No"
  - "Authorized to work in the US" -> "Yes"
  - "Are you 18 or older" -> "Yes"
  - "Have you been convicted of a felony" -> "No"
- ONLY skip a field if it is OPTIONAL (required: false) AND you genuinely
  have no relevant data. Never skip a required field."""


def is_complex_url(url: str) -> bool:
    """Check if URL is known to require browser-use agent."""
    return any(domain in url for domain in COMPLEX_DOMAINS)


async def detect_complexity(page: Page) -> tuple[bool, str]:
    """Detect if the loaded page has a complex form. Returns (is_complex, reason)."""
    checks = await page.evaluate("""
    (() => {
        const body = document.body?.textContent?.toLowerCase() || '';
        const html = document.documentElement?.innerHTML?.toLowerCase() || '';

        // Check for multi-step indicators (NOT simple nav tabs like Overview/Application)
        const tabs = document.querySelectorAll('[role="tab"]');
        const tabTexts = Array.from(tabs).map(t => t.textContent.trim().toLowerCase());
        const hasNumberedSteps = tabTexts.some(t => /^(step|page)\\s*\\d/i.test(t));
        const hasProgressSteps = tabTexts.length > 3;
        const hasSteps = !!(
            document.querySelector('[class*="step-indicator"]') ||
            document.querySelector('[class*="progress-bar"]') ||
            hasNumberedSteps ||
            hasProgressSteps ||
            html.match(/step\\s+\\d+\\s+(of|\\/)\\s+\\d+/i)
        );

        // Check for login form
        const hasLogin = !!(
            document.querySelector('input[type="password"]') ||
            document.querySelector('[class*="login"]') ||
            document.querySelector('[class*="sign-in"]')
        );

        // Count visible form fields
        const fields = document.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]), select, textarea'
        );
        const visibleFields = Array.from(fields).filter(
            f => f.offsetParent !== null
        ).length;

        // Check for job filled/closed
        const isClosed = body.includes('been filled') ||
            body.includes('no longer accepting') ||
            body.includes('position has been closed') ||
            body.includes('job is no longer available');

        return {
            hasSteps,
            hasLogin,
            visibleFields,
            isClosed,
        };
    })()
    """)

    if checks.get("isClosed"):
        return True, "job_closed"
    if checks.get("hasLogin"):
        return True, "login_required"
    if checks.get("hasSteps"):
        return True, "multi_step_form"
    if checks.get("visibleFields", 0) < 3:
        return True, "too_few_fields"
    if checks.get("visibleFields", 0) > 30:
        return True, "too_many_fields"

    return False, "simple"


async def extract_fields(page: Page) -> list[dict]:
    """Extract all form fields from the page via JavaScript."""
    return await page.evaluate(EXTRACT_FIELDS_JS)


async def extract_fields_aria(page: Page) -> list[dict]:
    """Extract form fields from Playwright's accessibility tree.

    Calls ``locator.aria_snapshot()`` to get the ARIA tree as YAML, parses it
    into a dict via ``parse_aria_yaml``, then walks the dict via
    ``extract_fields_from_aria``. Returns an empty list on any error so the
    caller can fall back to the JS extractor without crashing.

    Why YAML? ``page.accessibility.snapshot()`` was removed in newer Playwright
    Python versions. ``locator.aria_snapshot()`` is the supported replacement
    but only returns YAML — we adapt it.
    """
    try:
        yaml_text = await page.locator("body").aria_snapshot()
    except Exception as e:
        logger.warning(f"  ARIA snapshot call failed: {e}")
        return []

    snapshot_dict = parse_aria_yaml(yaml_text)
    if not snapshot_dict:
        logger.warning("  ARIA YAML parsed to empty dict")
        return []

    return extract_fields_from_aria(snapshot_dict)


def _normalize_label_for_dedup(label: str) -> str:
    """Strip required markers, punctuation, whitespace so ARIA + JS labels match.

    ARIA strips ``*`` already; JS keeps it. Without normalization, ``"Email"``
    and ``"Email *"`` are seen as different fields and both get processed.
    """
    s = label.strip().lower()
    for marker in ("*", "(required)", "[required]", "(optional)", "[optional]"):
        if s.endswith(marker):
            s = s[: -len(marker)].strip()
    return s.rstrip(":").strip()


def merge_field_lists(aria_fields: list[dict], js_fields: list[dict]) -> list[dict]:
    """Combine ARIA and JS extractor results using label-based matching.

    Strategy (Option B — fixes the synthetic-ID bug):
      1. Build a label index of JS fields (real DOM IDs).
      2. For each ARIA field, look up its normalized label in the index.
         - If a JS field matches: take the JS field as the base (keeps the
           real DOM ID for filling), but enrich it with ARIA's cleaner label
           and ARIA's options if the JS field had none.
         - If no JS field matches: keep the ARIA field but mark it
           ``_label_based=True`` so ``fill_fields_js`` knows to locate the
           element by visible text instead of the synthetic ID.
      3. Append any JS fields that no ARIA field matched.

    This guarantees every field in the output has either a real DOM ID OR
    is explicitly flagged for label-based filling. No silent failures.
    """
    js_by_label: dict[str, dict] = {}
    for jf in js_fields:
        label_key = _normalize_label_for_dedup(jf.get("label", ""))
        if label_key and label_key not in js_by_label:
            js_by_label[label_key] = jf

    out: list[dict] = []
    used_js_labels: set[str] = set()

    for af in aria_fields:
        aria_label_key = _normalize_label_for_dedup(af.get("label", ""))
        if not aria_label_key:
            continue

        js_match = js_by_label.get(aria_label_key)
        if js_match is not None:
            enriched = dict(js_match)
            if af.get("label"):
                enriched["label"] = af["label"]
            if af.get("options") and not enriched.get("options"):
                enriched["options"] = af["options"]
            out.append(enriched)
            used_js_labels.add(aria_label_key)
        else:
            aria_only = dict(af)
            aria_only["_label_based"] = True
            out.append(aria_only)

    for jf in js_fields:
        label_key = _normalize_label_for_dedup(jf.get("label", ""))
        if label_key in used_js_labels:
            continue
        out.append(jf)

    return _drop_group_member_duplicates(out)


def _drop_group_member_duplicates(fields: list[dict]) -> list[dict]:
    """Remove JS individual checkbox/radio fields that are members of an
    ARIA checkbox_group or radio_group already in the list.

    Example: ARIA correctly extracts "How did you hear about us?" as one
    ``checkbox_group`` with 10 options (BuiltIn, LinkedIn, etc.). The JS
    extractor also extracts each of those 10 options as a separate
    ``checkbox`` field. Those individual JS checkboxes are ghost duplicates
    — they inflate the denominator in the fill-ratio check and can't be
    filled meaningfully because the ARIA group path already handles them.

    This function keeps the ARIA group and drops the JS individual members.
    """
    group_member_labels: set[str] = set()
    for f in fields:
        if not f.get("_label_based"):
            continue
        if f.get("type") not in ("checkbox_group", "radio_group"):
            continue
        for opt in f.get("options") or []:
            text = opt.get("text", "") if isinstance(opt, dict) else str(opt)
            if text:
                group_member_labels.add(_normalize_label_for_dedup(text))

    if not group_member_labels:
        return fields

    filtered: list[dict] = []
    for f in fields:
        if f.get("_label_based"):
            filtered.append(f)
            continue
        if f.get("type") in ("checkbox", "radio_group"):
            label_key = _normalize_label_for_dedup(f.get("label", ""))
            if label_key in group_member_labels:
                continue  # ghost member of an ARIA group we already have
        filtered.append(f)
    return filtered


async def call_mapping_llm(
    fields: list[dict],
    profile: Profile,
    cover_letter: str,
    model_name: str,
) -> dict[str, str]:
    """Single LLM call to map profile data to form fields."""
    # Only include relevant field info to save tokens
    compact_fields = []
    for f in fields:
        entry = {
            "id": f.get("id") or f.get("name"),
            "type": f["type"],
            "label": f.get("label", ""),
        }
        if f.get("required"):
            entry["required"] = True
        if f.get("options"):
            entry["options"] = [o.get("text", o.get("value", "")) for o in f["options"]]
        if f.get("placeholder"):
            entry["placeholder"] = f["placeholder"]
        compact_fields.append(entry)

    exp_str = " | ".join(f"{e.company} ({e.title}, {e.dates})" for e in profile.experience[:4])

    prompt = MAPPING_PROMPT.format(
        fields_json=json.dumps(compact_fields, indent=1),
        first_name=profile.first_name,
        last_name=profile.last_name,
        email=profile.email,
        phone=profile.phone,
        location=profile.location,
        current_title=profile.current_title,
        company=profile.experience[0].company if profile.experience else "",
        years_exp=profile.years_experience,
        education=f"{profile.education[0].degree}, {profile.education[0].school}",
        experience=exp_str,
        skills=", ".join(profile.skills[:15]),
        work_auth=profile.work_authorization,
        sponsorship=profile.requires_sponsorship,
        gender=profile.gender,
        hispanic=profile.hispanic_latino,
        race=profile.ethnicity,
        veteran=profile.veteran_status,
        disability=profile.disability_status,
        cover_letter=cover_letter[:400],
    )

    if is_openai_model(model_name):
        client = openai.AsyncOpenAI()
        resp = await client.chat.completions.create(
            model=model_name,
            max_completion_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        text = resp.choices[0].message.content or "{}"
    else:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model=model_name,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text

    # Extract JSON from response
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse LLM mapping response: {text[:200]}")
        return {}


async def _fill_label_based_field(page: Page, field: dict, value: str) -> bool:
    """Fill an ARIA-only field by locating it via its question label and value.

    Used when ``merge_field_lists`` flagged the field with ``_label_based=True``
    because no JS extractor counterpart provided a real DOM ID.

    Strategy:
      - Find the question text on the page using ``page.get_by_text``.
      - From that anchor, follow XPath to the next button/radio/checkbox
        whose visible text matches ``value``.
      - Click it.

    Returns True on a successful click, False if any step couldn't find an element.
    """
    field_type = field.get("type", "")
    label = (field.get("label") or "").strip()
    if not label or not value:
        return False

    # Use the first ~60 chars of the label as a text anchor — long enough to
    # be unique on the page, short enough to handle minor markup differences.
    anchor_text = label[:60]

    try:
        if field_type == "button_group":
            xpath = f"xpath=following::button[normalize-space()='{value}'][1]"
            target = page.get_by_text(anchor_text).first.locator(xpath)
        elif field_type in ("radio_group",):
            # Try clicking a label or radio whose visible text contains the value.
            xpath = (
                f"xpath=following::label[normalize-space()='{value}'][1] | "
                f"following::*[@role='radio'][normalize-space()='{value}'][1]"
            )
            target = page.get_by_text(anchor_text).first.locator(xpath)
        elif field_type in ("checkbox_group",):
            xpath = (
                f"xpath=following::label[normalize-space()='{value}'][1] | "
                f"following::*[@role='checkbox'][normalize-space()='{value}'][1]"
            )
            target = page.get_by_text(anchor_text).first.locator(xpath)
        else:
            return False

        if await target.count() == 0:
            return False
        await target.first.click(timeout=3000)
        return True
    except Exception as e:
        logger.warning(f"  Label-based fill for '{label[:30]}' failed: {e}")
        return False


async def fill_fields_js(
    page: Page,
    mapping: dict[str, str],
    fields: list[dict],
    profile: Profile,
) -> list[str]:
    """Fill form fields using JavaScript. Returns list of filled field IDs.

    Instrumented: every field attempt is logged with status (ok / not_filled /
    exception / label_based_fail / skipped_upload) to ``fill_log``, and a
    breakdown-by-type summary + detailed JSON is written to
    ``output/logs/fill_log.json`` at the end for post-mortem analysis.
    """
    filled: list[str] = []
    field_map = {(f.get("id") or f.get("name")): f for f in fields}
    fill_log: list[dict] = []

    for field_id, raw_value in mapping.items():
        # Coerce LLM JSON values (bool/int) to string before any .lower() etc.
        value = "" if raw_value is None else str(raw_value)

        field_info = field_map.get(field_id, {})
        field_type = field_info.get("type", "text")
        label_based = bool(field_info.get("_label_based"))

        log_entry: dict = {
            "field_id": (field_id or "")[:200],
            "type": field_type,
            "label_based": label_based,
            "label": (field_info.get("label") or "")[:120],
            "value": value[:120],
            "status": "unknown",
        }

        if value == "UPLOAD_RESUME":
            log_entry["status"] = "skipped_upload"
            fill_log.append(log_entry)
            continue

        filled_before = len(filled)

        # ARIA-only fields (no real DOM ID) take the label-based path.
        # ``merge_field_lists`` flags these with ``_label_based=True``.
        if label_based:
            try:
                success = await _fill_label_based_field(page, field_info, value)
                if success:
                    filled.append(field_id)
                    log_entry["status"] = "ok"
                else:
                    log_entry["status"] = "label_based_fail"
            except Exception as e:
                log_entry["status"] = "exception"
                log_entry["error"] = f"{type(e).__name__}: {str(e)[:80]}"
            fill_log.append(log_entry)
            continue

        try:
            if field_type in ("radio_group", "button_group"):
                # Handle native radio buttons, custom span radios, and button groups
                # Try multiple strategies to click the right option
                clicked = False

                if field_type == "radio_group":
                    options = field_info.get("options", [])
                    for opt in options:
                        if value.lower() in opt.get("text", "").lower():
                            radio_id = opt.get("id", "")
                            if radio_id:
                                el = await page.query_selector(f"[id='{radio_id}']")
                                if el:
                                    await el.click()
                                    clicked = True
                            break

                if not clicked:
                    # Fallback: find by visible text (works for span/button radios)
                    # Try button first, then span, then label
                    for tag in ["button", "span", "label", "div"]:
                        els = await page.query_selector_all(f"{tag}:has-text('{value}')")
                        for el in els:
                            if await el.is_visible():
                                text = (await el.text_content() or "").strip()
                                if text == value:
                                    await el.click()
                                    clicked = True
                                    break
                        if clicked:
                            break

                if clicked:
                    filled.append(field_id)

            elif field_type == "select":
                # Use [id='...'] attribute selector (not #id) — CSS ID selectors
                # cannot start with a digit, and many ATSs use UUID-style field IDs.
                selector = f"[id='{field_id}']" if field_info.get("id") else f"[name='{field_id}']"
                selected = False
                try:
                    await page.select_option(selector, label=value)
                    selected = True
                except Exception:
                    # Layer 2 keyboard fallback: type first chars + Enter
                    selected = await keyboard_select_fallback(page, selector, value)
                if selected:
                    filled.append(field_id)

            elif field_type == "combobox":
                # Type into combobox and wait for autocomplete
                selector = f"[id='{field_id}']" if field_info.get("id") else f"[name='{field_id}']"
                await page.click(selector)
                await page.keyboard.type(value[:3], delay=200)
                await asyncio.sleep(1.5)
                # Try to click first suggestion
                suggestion = await page.query_selector(
                    "[role='option']:first-child, [id*='option-0'], [id*='location-0']"
                )
                if suggestion:
                    await suggestion.click()
                    filled.append(field_id)
                else:
                    # Type more and try again
                    await page.keyboard.type(value[3:], delay=100)
                    await asyncio.sleep(1.5)
                    suggestion = await page.query_selector("[role='option']:first-child")
                    if suggestion:
                        await suggestion.click()
                        filled.append(field_id)

            elif field_type == "checkbox":
                # Coerce to string — LLM may return JSON true/false (bool)
                if str(value).lower() in ("true", "yes", "1"):
                    sel = f"[id='{field_id}']" if field_info.get("id") else f"[name='{field_id}']"
                    el = await page.query_selector(sel)
                    if el and not await el.is_checked():
                        await el.click()
                        filled.append(field_id)

            else:
                # Standard text/email/tel/textarea — use Playwright fill()
                # which properly triggers React/Angular change detection
                selector = f"[id='{field_id}']" if field_info.get("id") else f"[name='{field_id}']"
                el = await page.query_selector(selector)
                if el:
                    await el.fill(value)
                    filled.append(field_id)

        except Exception as e:
            logger.warning(f"  Direct fill failed for {field_id[:40]}: {e}")
            log_entry["status"] = "exception"
            log_entry["error"] = f"{type(e).__name__}: {str(e)[:80]}"

        if log_entry["status"] == "unknown":
            if len(filled) > filled_before:
                log_entry["status"] = "ok"
            else:
                log_entry["status"] = "not_filled"

        fill_log.append(log_entry)

    _emit_fill_breakdown(fill_log, filled)
    return filled


def _emit_fill_breakdown(fill_log: list[dict], filled: list[str]) -> None:
    """Log a per-type fill summary + save the detailed fill log to disk."""
    type_stats: dict[str, dict[str, int]] = {}
    for entry in fill_log:
        label_based_flag = "_lb" if entry.get("label_based") else ""
        key = f"{entry['type']}{label_based_flag}"
        stats = type_stats.setdefault(key, {"ok": 0, "total": 0})
        stats["total"] += 1
        if entry.get("status") == "ok":
            stats["ok"] += 1

    breakdown = ", ".join(f"{k}:{s['ok']}/{s['total']}" for k, s in sorted(type_stats.items()))
    logger.info(f"  Direct fill breakdown: {breakdown}")

    failed = [e for e in fill_log if e.get("status") != "ok"]
    if failed:
        failed_summary = " | ".join(
            f"{e['label'][:25]}[{e['type']}]={e['status']}" for e in failed[:6]
        )
        logger.info(f"  Direct fill: first failed fields -> {failed_summary}")

    try:
        log_path = Path("output/logs/fill_log.json")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "filled_count": len(filled),
            "total_mapped": len(fill_log),
            "breakdown": type_stats,
            "entries": fill_log,
        }
        log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"  Could not save fill log: {e}")


async def upload_resume(page: Page, resume_path: str) -> bool:
    """Upload resume to the first file input on the page."""
    try:
        file_input = await page.query_selector("input[type='file']")
        if file_input:
            await file_input.set_input_files(resume_path)
            await asyncio.sleep(2)  # Wait for processing
            return True
    except Exception as e:
        logger.warning(f"  Resume upload failed: {e}")
    return False


# Patterns ATSs use for "Autofill from resume" buttons / sections.
AUTOFILL_TEXT_PATTERNS = (
    "autofill from resume",
    "autofill",
    "parse resume",
    "upload to autofill",
    "populate from resume",
    "import from resume",
)


async def click_autofill_from_resume(page: Page, resume_path: str) -> bool:
    """If the page has an Ashby/Lever-style 'Autofill from resume' section,
    upload the resume to it so the ATS pre-populates fields server-side.

    Returns True if we found and used an autofill widget. Best-effort —
    failures are non-fatal because the regular extractor + LLM still runs.
    """
    try:
        # Find any heading/text that mentions autofill
        autofill_heading = None
        for pattern in AUTOFILL_TEXT_PATTERNS:
            heading = await page.query_selector(
                f"heading:has-text('{pattern}'), :text-matches('{pattern}', 'i')"
            )
            if heading and await heading.is_visible():
                autofill_heading = heading
                break

        if not autofill_heading:
            return False

        # Find the nearest file input that follows the autofill heading
        file_inputs = await page.query_selector_all("input[type='file']")
        if not file_inputs:
            return False

        # Use the FIRST visible file input — typically the autofill section
        # is at the top of the form, before the main resume upload field.
        for fi in file_inputs:
            try:
                await fi.set_input_files(resume_path)
                logger.info("  Autofill: uploaded resume to autofill widget")
                await asyncio.sleep(4)  # let server-side parser populate fields
                return True
            except Exception:
                continue
        return False
    except Exception as e:
        logger.warning(f"  Autofill click failed (non-fatal): {e}")
        return False


async def click_submit(page: Page) -> bool:
    """Find and click the submit button."""
    selectors = [
        "button:has-text('Submit Application')",
        "button:has-text('Submit application')",
        "button[type='submit']",
        "button:has-text('Submit')",
        "input[type='submit']",
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                return True
        except Exception:
            continue
    return False


async def _read_validation_errors(page: Page) -> list[str]:
    """Read visible validation error messages from the page.

    Looks for the standard ATS error indicators and returns their text content.
    """
    return await page.evaluate(
        """(() => {
            const sel = '[role="alert"],[aria-invalid="true"],'
                + '[class*="error"],[class*="invalid"],[class*="alert-danger"]';
            const errors = document.querySelectorAll(sel);
            const out = [];
            for (const e of errors) {
                if (e.offsetParent === null) continue;
                const text = (e.textContent || '').trim();
                if (text && text.length < 200) out.push(text);
            }
            return out.slice(0, 20);
        })()"""
    )


async def _retry_after_validation_errors(
    page: Page,
    profile: Profile,
    cover_letter: str,
    model_name: str,
    error_texts: list[str],
) -> tuple[bool, str]:
    """Surgically fix the fields named in validation error messages, then re-submit.

    Strategy (NOT a blind re-mapping):
      1. Re-extract the fields (some may have changed after submit).
      2. For each error text, fuzzy-match it against field labels to find
         which field is failing.
      3. For the matched fields, build a focused Haiku call asking ONLY for
         the values of those specific fields, given the error context.
      4. Apply only the patches and submit again.

    This is much cheaper than a full re-mapping AND much more likely to
    actually fix the problem, because we tell the model what's broken.
    """
    logger.info(f"  Retry: {len(error_texts)} validation errors detected")

    aria_fields = await extract_fields_aria(page)
    js_fields = await extract_fields(page)
    fields = merge_field_lists(aria_fields, js_fields)

    failing_fields = _match_errors_to_fields(error_texts, fields)
    if not failing_fields:
        logger.warning("  Retry: could not match any error to a field — giving up")
        return False, f"Could not identify failing fields from {len(error_texts)} errors"

    logger.info(f"  Retry: matched {len(failing_fields)} failing fields, calling Haiku")

    patches = await _haiku_fix_failing_fields(failing_fields, error_texts, profile)
    if not patches:
        return False, "Haiku returned no patches for failing fields"

    filled = await fill_fields_js(page, patches, fields, profile)
    logger.info(f"  Retry: patched {len(filled)} fields")

    await asyncio.sleep(1)
    submitted = await click_submit(page)
    if not submitted:
        return False, f"Retry: could not click Submit ({len(filled)} patched)"

    await asyncio.sleep(5)
    remaining_errors = await _read_validation_errors(page)
    if remaining_errors:
        return False, (
            f"Retry failed: still {len(remaining_errors)} errors after patch. "
            f"First: {remaining_errors[0][:80]}"
        )

    return True, f"Submitted on retry. Patched {len(filled)} fields from {len(error_texts)} errors."


def _match_errors_to_fields(error_texts: list[str], fields: list[dict]) -> list[dict]:
    """Find the fields whose labels appear in the error messages.

    Uses substring matching on lowercased text. A field matches if any 6+ char
    chunk of its label appears in any error message.
    """
    matched: list[dict] = []
    seen_ids: set[str] = set()

    for field in fields:
        label = (field.get("label") or "").strip().lower()
        if len(label) < 6:
            continue
        key = field.get("id") or field.get("name", "")
        if key in seen_ids:
            continue

        for err in error_texts:
            err_lower = err.lower()
            if label[:30] in err_lower or label[:50] in err_lower:
                matched.append(field)
                seen_ids.add(key)
                break

    return matched


async def _haiku_fix_failing_fields(
    failing_fields: list[dict],
    error_texts: list[str],
    profile: Profile,
) -> dict[str, str]:
    """One small Haiku call: given the failing fields + error context + profile,
    return ``{field_id: value}`` patches.

    Cost: ~$0.001 vs ~$0.02 for a full Sonnet remapping.
    """
    compact_fields = []
    for f in failing_fields:
        entry = {
            "id": f.get("id") or f.get("name"),
            "type": f.get("type"),
            "label": f.get("label", ""),
        }
        if f.get("options"):
            entry["options"] = [
                o.get("text", "") if isinstance(o, dict) else str(o) for o in f["options"]
            ]
        compact_fields.append(entry)

    prompt = (
        "These form fields failed validation. Fill them based on the profile.\n\n"
        f"FAILING FIELDS:\n{json.dumps(compact_fields, indent=1)}\n\n"
        f"ERROR MESSAGES:\n" + "\n".join(f"- {e[:200]}" for e in error_texts) + "\n\n"
        f"PROFILE:\n"
        f"  Name: {profile.first_name} {profile.last_name}\n"
        f"  Email: {profile.email}\n"
        f"  Phone: {profile.phone}\n"
        f"  Location: {profile.location}\n"
        f"  Years experience: {profile.years_experience}\n"
        f"  Work auth: {profile.work_authorization}\n"
        f"  Sponsorship: {profile.requires_sponsorship}\n\n"
        'Return ONLY a JSON object: {"field_id": "value", ...}\n'
        "For dropdown fields, value MUST be one of the option texts.\n"
        "For 'How did you hear' style multi-selects, pick 'LinkedIn'.\n"
        "For location radio groups, pick the option matching the profile location."
    )

    try:
        client = anthropic.AsyncAnthropic()
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"  Retry Haiku call failed: {e}")
        return {}

    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"  Retry Haiku returned invalid JSON: {text[:200]}")
        return {}


def _check_required_fields_covered(
    fields: list[dict],
    mapping: dict[str, str],
    filled: list[str],
) -> tuple[bool, list[str]]:
    """Check whether every required field in ``fields`` was filled.

    Returns ``(all_covered, missing_labels)``.
    - ``all_covered`` is True if every field with ``required=True`` appears in
      ``filled``, OR if the form has no detectable required fields at all
      (lazy ATS — fall through to "always submit" behavior).
    - ``missing_labels`` is the list of required field labels that were NOT
      filled. Used for logging when we bail.
    """
    required_fields = [f for f in fields if f.get("required")]
    if not required_fields:
        # No required fields detected — let caller decide (Option C fallback).
        return True, []

    filled_set = set(filled)
    missing = [
        f.get("label", "") or (f.get("id") or f.get("name") or "")
        for f in required_fields
        if (f.get("id") or f.get("name")) not in filled_set
    ]
    return (len(missing) == 0), missing


def _build_prefill_hints(
    fields: list[dict],
    template_map: dict[str, str],
    cache_map: dict[str, str],
) -> list[tuple[str, str, str]]:
    """Collect (field_type, label, value) tuples from Layer 0 matches.

    These hints are handed to the browser-use agent if direct_fill escalates,
    so the agent doesn't waste Sonnet tokens re-deriving answers we already
    know are correct from the profile.
    """
    combined: dict[str, str] = {**template_map, **cache_map}
    fields_by_id = {(f.get("id") or f.get("name")): f for f in fields}

    hints: list[tuple[str, str, str]] = []
    for field_id, value in combined.items():
        f = fields_by_id.get(field_id)
        if not f:
            continue
        label = (f.get("label") or "").strip()
        field_type = f.get("type", "text")
        if label and value:
            hints.append((field_type, label, str(value)))
    return hints


async def direct_fill_application(
    page: Page,
    profile: Profile,
    cover_letter: str,
    model_name: str = "claude-sonnet-4-6",
) -> tuple[bool, str, list[tuple[str, str, str]]]:
    """Try to fill an application using direct JS + 1 LLM call.

    Returns ``(success, summary_message, prefill_hints)``. ``prefill_hints``
    is a list of ``(field_type, label, value)`` tuples from Layer 0 matches
    that the browser-use agent can use if direct_fill escalates.
    """
    # Apply button already clicked by caller (_try_direct_fill)
    # Step 1: Try the ATS's own "Autofill from resume" widget first. This is
    # free server-side parsing — if Ashby/Lever pre-fills 60-80% of fields,
    # our Layer 0 + LLM only need to handle the remainder.
    autofilled = await click_autofill_from_resume(page, profile.resume_path)
    if autofilled:
        logger.info("  Direct fill: autofill from resume succeeded")

    # Step 2: Upload resume to the main resume field as well (some forms
    # have separate autofill + resume slots; some require both)
    await upload_resume(page, profile.resume_path)

    # Step 3: Wait for any resume parsing
    await asyncio.sleep(2)

    # Step 4: Extract all form fields — DUAL EXTRACTION
    # Run BOTH ARIA-tree extraction (handles modern React widgets via semantic
    # roles) and the original JS extractor (catches edge cases). Merge by label.
    aria_fields = await extract_fields_aria(page)
    js_fields = await extract_fields(page)
    fields = merge_field_lists(aria_fields, js_fields)
    logger.info(
        f"  Direct fill: extracted ARIA={len(aria_fields)} JS={len(js_fields)} "
        f"merged={len(fields)} form fields"
    )

    if len(fields) < 2:
        return False, "Too few fields found — page may not have loaded", []

    # Step 4.5: Layer 0 — pre-fill known questions from templates (zero API cost)
    template_map, unmatched = apply_templates(fields, profile)
    logger.info(
        f"  Layer 0: pre-filled {len(template_map)}/{len(fields)} fields from templates "
        f"({len(unmatched)} remain for LLM)"
    )

    # Step 4.6: Layer 0 cache — reuse answers learned from previous successful
    # applications. Free, gets smarter every successful run.
    cache = Layer0Cache()
    cache_map: dict[str, str] = {}
    still_unmatched: list[dict] = []
    for f in unmatched:
        cached_value = cache.lookup(f.get("label", ""), f.get("type", ""))
        if cached_value:
            field_id = f.get("id") or f.get("name")
            if field_id:
                cache_map[field_id] = cached_value
                logger.info(
                    f"  Layer 0 cache: hit '{f.get('label', '')[:40]}' = '{cached_value[:40]}'"
                )
                continue
        still_unmatched.append(f)
    if cache_map:
        logger.info(
            f"  Layer 0 cache: {len(cache_map)} hits, {len(still_unmatched)} fields remain for LLM"
        )

    # Step 5: LLM maps only the fields cache + templates couldn't handle
    if still_unmatched:
        llm_mapping = await call_mapping_llm(still_unmatched, profile, cover_letter, model_name)
        logger.info(
            f"  Direct fill: LLM mapped {len(llm_mapping)} of {len(still_unmatched)} unmatched"
        )
    else:
        llm_mapping = {}
        logger.info("  Direct fill: templates + cache covered everything — skipping LLM call")

    # Combined mapping: templates and cache always win over LLM (more reliable)
    mapping: dict[str, str] = {}
    mapping.update(llm_mapping)
    mapping.update(cache_map)
    mapping.update(template_map)

    # Compute the prefill hints NOW — we need them on EVERY return path below
    # (success or failure) so the browser-use fallback can benefit from what
    # Layer 0 + cache already figured out.
    prefill_hints = _build_prefill_hints(fields, template_map, cache_map)

    if not mapping:
        return (
            False,
            "Layer 0 + cache + LLM mapping all returned empty — falling back",
            prefill_hints,
        )

    # Step 5.5: Layer 1 — Haiku retry for dropdowns where the LLM value doesn't
    # match any actual option on the field. Small surgical per-dropdown fix.
    mapping = await haiku_patch_bad_dropdown_values(fields, mapping, unmatched)

    # Step 6: Fill all fields via JavaScript
    filled = await fill_fields_js(page, mapping, fields, profile)
    logger.info(f"  Direct fill: filled {len(filled)}/{len(mapping)} fields")

    # NEW THRESHOLD (replaces the old 50% rule): check whether all REQUIRED
    # fields were filled. If the form has no detected required fields at all,
    # fall through to "always try submit" (Option C behavior).
    required_ok, missing_required = _check_required_fields_covered(fields, mapping, filled)
    if not required_ok:
        first_missing = ", ".join(m[:40] for m in missing_required[:5])
        return (
            False,
            f"Missing {len(missing_required)} required fields: {first_missing}",
            prefill_hints,
        )

    # Step 7: Click submit
    await asyncio.sleep(1)
    submitted = await click_submit(page)

    if submitted:
        # Wait for validation errors / success page to render
        await asyncio.sleep(5)

        # Screenshot after submit to capture any validation errors
        screenshot_path = Path("output/screenshots/direct_fill_submit.png")
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path))

        # Check for validation errors (read full text, not just count)
        error_texts = await _read_validation_errors(page)

        def _persist_cache_learnings() -> None:
            """Write LLM-mapped answers to the persistent cache on success."""
            written = 0
            for f in still_unmatched:
                fid = f.get("id") or f.get("name")
                if fid and fid in llm_mapping:
                    val = str(llm_mapping[fid])
                    if cache.remember(f.get("label", ""), f.get("type", ""), val):
                        written += 1
            if written:
                cache.save()
                logger.info(f"  Layer 0 cache: learned {written} new field mappings")

        if error_texts:
            logger.warning(
                f"  Direct fill: {len(error_texts)} validation errors after submit "
                f"(screenshot: {screenshot_path})"
            )

            # Stage 6: ONE retry — re-extract, re-map, re-fill, re-submit
            success, retry_summary = await _retry_after_validation_errors(
                page, profile, cover_letter, model_name, error_texts
            )
            if success:
                _persist_cache_learnings()
                return True, retry_summary, prefill_hints

            return (
                False,
                (
                    f"Submit clicked but {len(error_texts)} validation errors. "
                    f"Retry failed: {retry_summary}. "
                    f"Filled {len(filled)} fields. Screenshot: {screenshot_path}"
                ),
                prefill_hints,
            )

        # Check for success indicators
        body = await page.evaluate("document.body?.textContent || ''")
        body_lower = body.lower()
        success_kws = (
            "thank you",
            "application submitted",
            "successfully",
            "received your application",
            "application received",
        )
        if any(kw in body_lower for kw in success_kws):
            _persist_cache_learnings()
            return True, f"Submitted via direct fill. Fields filled: {len(filled)}", prefill_hints

        # Submit clicked, no errors, no clear confirmation — still treat as success
        _persist_cache_learnings()
        return (
            True,
            f"Submit clicked, {len(filled)} fields filled. Check confirmation.",
            prefill_hints,
        )

    return False, f"Could not find submit button. Filled {len(filled)} fields.", prefill_hints
