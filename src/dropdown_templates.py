"""Layer 0: template + fuzzy-match pre-fill for common job application fields.

For every field extracted from a form, this module tries to match its label to
a known question template (e.g. "Work authorization", "Gender", "First name").
If the match succeeds, we resolve the answer directly from the user's profile
and fuzzy-pick the best option from the dropdown's own option list — skipping
the LLM mapping call entirely for that field.

Public API:
  ``apply_templates(fields, profile) -> (pre_filled_map, unmatched_fields)``

Template data lives in ``src/dropdown_registry.py``.
Matching primitives live in ``src/dropdown_match.py``.
"""

from __future__ import annotations

import logging

from src.dropdown_match import fuzzy_pick_option, label_contains
from src.dropdown_registry import TEMPLATE_MAP, TEXT_FIELD_TYPES, FieldTemplate
from src.profile import Profile

logger = logging.getLogger("job-agent")


def _template_label_matches(label: str, template: FieldTemplate) -> bool:
    """Apply positive + negative pattern checks for one template."""
    label_lower = label.lower()
    if any(neg.lower() in label_lower for neg in template.negative_patterns):
        return False
    return any(label_contains(label, pat) for pat in template.label_patterns)


def match_field_to_template(label: str, field_type: str) -> FieldTemplate | None:
    """Find the most specific template that matches this field.

    Specificity = length of the longest matching label pattern. "email address"
    (13 chars) beats "email" (5 chars) so generic patterns never steal fields
    from more-specific templates.
    """
    if not label:
        return None

    best: tuple[FieldTemplate, int] | None = None

    for template in TEMPLATE_MAP:
        if field_type not in template.field_types:
            continue
        if not _template_label_matches(label, template):
            continue

        longest_hit = max(
            (len(p) for p in template.label_patterns if label_contains(label, p)),
            default=0,
        )
        if best is None or longest_hit > best[1]:
            best = (template, longest_hit)

    return best[0] if best else None


def _extract_option_texts(field: dict) -> list[str]:
    """Pull clean option text from a field dict."""
    raw = field.get("options") or []
    return [o.get("text", "") if isinstance(o, dict) else str(o) for o in raw if o]


def _resolve_option_value(raw_answer: str, field: dict, field_type: str) -> str | None:
    """Convert a profile-derived raw answer into the value to fill.

    Text fields → return the raw answer unchanged.
    Dropdown/radio/button fields → fuzzy-match against the option list.
    Returns ``None`` if no option passes the fuzzy threshold.
    """
    if field_type in TEXT_FIELD_TYPES:
        return raw_answer

    option_texts = _extract_option_texts(field)
    if not option_texts:
        return raw_answer  # combobox with no pre-known options — fill as-is

    for opt in option_texts:
        if opt.lower() == raw_answer.lower():
            return opt

    return fuzzy_pick_option(raw_answer, option_texts)


def apply_templates(
    fields: list[dict],
    profile: Profile,
) -> tuple[dict[str, str], list[dict]]:
    """Walk all fields, pre-fill those that match a template.

    Returns:
        ``(pre_filled_map, unmatched_fields)``
        - ``pre_filled_map``: ``{field_id: value}`` ready for ``fill_fields_js``.
        - ``unmatched_fields``: fields left for the LLM mapping call.
    """
    pre_filled: dict[str, str] = {}
    unmatched: list[dict] = []

    for field in fields:
        field_id = field.get("id") or field.get("name") or ""
        label = field.get("label", "")
        field_type = field.get("type", "")

        if not field_id:
            unmatched.append(field)
            continue

        template = match_field_to_template(label, field_type)
        if template is None:
            unmatched.append(field)
            continue

        raw_answer = template.resolver(profile, field)
        if not raw_answer:
            unmatched.append(field)
            continue

        final_value = _resolve_option_value(raw_answer, field, field_type)
        if final_value is None:
            unmatched.append(field)
            continue

        pre_filled[field_id] = final_value
        logger.info(f"  Layer 0: matched '{label[:40]}' -> {template.name} = '{final_value[:40]}'")

    return pre_filled, unmatched
