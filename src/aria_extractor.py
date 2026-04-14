"""ARIA-tree based form field extractor.

Walks the dict returned by ``page.accessibility.snapshot(interesting_only=True)``
and produces field dicts in the same shape as ``EXTRACT_FIELDS_JS`` in
``direct_fill.py``, so downstream code (Layer 0 templates, the LLM mapping
call, ``fill_fields_js``) can consume them without changes.

Designed to handle multiple ATS structural patterns:
  - Whatnot/Ashby: ``group`` containers with text labels + radio/checkbox items
  - Greenhouse: native ``<input>`` rendered as ``radiogroup`` / proper ARIA
  - Lever: similar to Ashby
  - Yes/No button groups: sequential ``button`` items preceded by a ``text`` node

Public API:
  ``extract_fields_from_aria(snapshot: dict) -> list[dict]``
"""

from __future__ import annotations

import logging

from src.aria_helpers import (
    attach_button_groups,
    clean_label,
    dedupe_by_label,
    is_required,
    synthetic_id,
)

logger = logging.getLogger("job-agent")

# Roles that map to a single text-input-style field.
TEXTBOX_ROLES = {"textbox", "searchbox", "spinbutton"}

# Roles representing a single boolean control.
SINGLE_CHECKBOX_ROLES = {"checkbox", "switch"}

# Roles that are NEVER form controls — skipped during traversal so they
# don't pollute the field list.
SKIP_ROLES = {
    "img",
    "image",
    "presentation",
    "none",
    "separator",
    "tooltip",
    "status",
    "log",
    "marquee",
    "timer",
    "navigation",
    "banner",
    "contentinfo",
    "complementary",
}


def extract_fields_from_aria(snapshot: dict | None) -> list[dict]:
    """Walk an accessibility snapshot dict and return field dicts.

    Returns an empty list when ``snapshot`` is None or has no usable children.
    Each returned field dict matches the schema of the JS extractor:
        ``{id, name, type, label, required, value, placeholder, options}``
    """
    if not snapshot:
        return []

    fields: list[dict] = []
    _walk(snapshot, fields, parent_label=None)
    attach_button_groups(snapshot, fields)
    return dedupe_by_label(fields)


# ---------------------------------------------------------------------------
# Tree walker
# ---------------------------------------------------------------------------


def _walk(node: dict, out: list[dict], parent_label: str | None) -> None:
    """Recursive descent — append any field nodes found under ``node``."""
    role = node.get("role", "")
    name = (node.get("name") or "").strip()
    children = node.get("children") or []

    if role in SKIP_ROLES:
        return

    if role in TEXTBOX_ROLES:
        out.append(_field_from_textbox(node, parent_label))
        return

    if role == "combobox":
        out.append(_field_from_combobox(node, parent_label))
        return

    if role == "radiogroup":
        out.append(_field_from_radiogroup(node, name or parent_label))
        return

    if role == "group":
        group_type = _classify_group(node)
        if group_type == "radio_group":
            out.append(_field_from_radiogroup(node, name or _group_label(node)))
            return
        if group_type == "checkbox_group":
            out.append(_field_from_checkboxgroup(node, name or _group_label(node)))
            return
        for child in children:
            _walk(child, out, parent_label=name or parent_label)
        return

    if role in SINGLE_CHECKBOX_ROLES:
        out.append(_field_from_single_checkbox(node, parent_label))
        return

    label_context = name if role in ("heading", "text") else parent_label
    for child in children:
        _walk(child, out, parent_label=label_context)


def _classify_group(node: dict) -> str | None:
    """Return ``"radio_group"``, ``"checkbox_group"``, or None."""
    children = node.get("children") or []
    radios = sum(1 for c in children if c.get("role") == "radio")
    checks = sum(1 for c in children if c.get("role") == "checkbox")

    if radios >= 2 and radios > checks:
        return "radio_group"
    if checks >= 2 and checks > radios:
        return "checkbox_group"
    return None


def _group_label(node: dict) -> str:
    """Find the question text inside a group container."""
    name = (node.get("name") or "").strip()
    if name:
        return name
    for child in node.get("children") or []:
        if child.get("role") in ("text", "heading"):
            t = (child.get("name") or "").strip()
            if t:
                return t
    return ""


# ---------------------------------------------------------------------------
# Field constructors
# ---------------------------------------------------------------------------


def _field_from_textbox(node: dict, parent_label: str | None) -> dict:
    label = (node.get("name") or "").strip() or (parent_label or "")
    return {
        "id": synthetic_id("textbox", label),
        "name": label,
        "type": "text",
        "label": clean_label(label),
        "required": is_required(node, label),
        "value": (node.get("value") or "").strip(),
        "placeholder": "",
        "options": [],
    }


def _field_from_combobox(node: dict, parent_label: str | None) -> dict:
    label = (node.get("name") or "").strip() or (parent_label or "")
    return {
        "id": synthetic_id("combobox", label),
        "name": label,
        "type": "combobox",
        "label": clean_label(label),
        "required": is_required(node, label),
        "value": (node.get("value") or "").strip(),
        "placeholder": "",
        "options": [],
    }


def _field_from_radiogroup(node: dict, label: str | None) -> dict:
    label = label or ""
    options = _collect_options(node, "radio")
    return {
        "id": synthetic_id("radiogroup", label),
        "name": label,
        "type": "radio_group",
        "label": clean_label(label),
        "required": is_required(node, label),
        "value": "",
        "placeholder": "",
        "options": options,
    }


def _field_from_checkboxgroup(node: dict, label: str | None) -> dict:
    label = label or ""
    options = _collect_options(node, "checkbox")
    return {
        "id": synthetic_id("checkboxgroup", label),
        "name": label,
        "type": "checkbox_group",
        "label": clean_label(label),
        "required": is_required(node, label),
        "value": "",
        "placeholder": "",
        "options": options,
    }


def _field_from_single_checkbox(node: dict, parent_label: str | None) -> dict:
    label = (node.get("name") or "").strip() or (parent_label or "")
    return {
        "id": synthetic_id("checkbox", label),
        "name": label,
        "type": "checkbox",
        "label": clean_label(label),
        "required": is_required(node, label),
        "value": "",
        "placeholder": "",
        "checked": bool(node.get("checked", False)),
    }


def _collect_options(node: dict, target_role: str) -> list[dict]:
    """Pull child options of a group, returning ``[{text, value}, ...]``."""
    out: list[dict] = []
    for child in node.get("children") or []:
        if child.get("role") == target_role:
            text = (child.get("name") or "").strip()
            if text:
                out.append({"text": text, "value": text})
    return out
