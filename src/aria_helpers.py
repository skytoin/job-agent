"""Helpers for ``src/aria_extractor.py``.

Pure utility functions: label cleaning, synthetic ID generation, dedup, and
the post-walk button-group detection pass for Yes/No-style questions.
"""

from __future__ import annotations

import re

# Required-marker characters at the end of a label.
REQUIRED_MARKERS = ("*", "(required)", "[required]")

_id_counter = {"n": 0}


def synthetic_id(prefix: str, label: str) -> str:
    """Build a stable-ish field ID from a role prefix + label slug."""
    _id_counter["n"] += 1
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower())[:32].strip("_")
    if slug:
        return f"aria_{prefix}_{slug}_{_id_counter['n']}"
    return f"aria_{prefix}_{_id_counter['n']}"


def reset_id_counter() -> None:
    """Reset the synthetic-ID counter (used by tests for stable IDs)."""
    _id_counter["n"] = 0


def clean_label(label: str) -> str:
    """Strip required markers and excess whitespace from a label."""
    s = label.strip()
    for marker in REQUIRED_MARKERS:
        if s.lower().endswith(marker):
            s = s[: -len(marker)].strip()
    return s.rstrip(":").strip()


def is_required(node: dict, label: str) -> bool:
    """True if the ARIA node says required OR the label ends with a marker."""
    if node.get("required") is True:
        return True
    s = (label or "").strip().lower()
    return any(s.endswith(m) for m in REQUIRED_MARKERS)


def dedupe_by_label(fields: list[dict]) -> list[dict]:
    """Remove duplicate fields with the same cleaned label + type."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for f in fields:
        key = (f.get("label", "").lower(), f.get("type", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# ---------------------------------------------------------------------------
# Button group post-pass (Yes/No-style questions)
# ---------------------------------------------------------------------------


def attach_button_groups(snapshot: dict, out: list[dict]) -> None:
    """Find sequences of adjacent buttons preceded by a question text node.

    Many ATSs (Ashby, Lever) implement Yes/No questions as a literal text
    node followed by two ``button`` elements with text "Yes" and "No". They
    don't appear as radiogroups, so we scan the tree for the pattern after
    the main walk and append any matches to ``out``.
    """
    for parent in iter_nodes(snapshot):
        children = parent.get("children") or []
        if len(children) < 3:
            continue

        i = 0
        while i < len(children):
            if not _is_question_text(children[i]):
                i += 1
                continue

            buttons: list[dict] = []
            j = i + 1
            while j < len(children) and children[j].get("role") == "button":
                btn_name = (children[j].get("name") or "").strip()
                if btn_name and len(btn_name) < 30:
                    buttons.append({"text": btn_name, "value": btn_name})
                j += 1

            if 2 <= len(buttons) <= 6:
                question_label = (children[i].get("name") or "").strip()
                out.append(
                    {
                        "id": synthetic_id("buttongroup", question_label),
                        "name": question_label,
                        "type": "button_group",
                        "label": clean_label(question_label),
                        "required": question_label.endswith("*"),
                        "value": "",
                        "placeholder": "",
                        "options": buttons,
                    }
                )
            i = j if j > i else i + 1


def _is_question_text(node: dict) -> bool:
    """Heuristic: a text node that ends with `*`, `?`, or contains 'select'."""
    if node.get("role") not in ("text", "StaticText", "paragraph"):
        return False
    text = (node.get("name") or "").strip().lower()
    if not text or len(text) < 8:
        return False
    return text.endswith("*") or text.endswith("?") or "select" in text or "please" in text


def iter_nodes(node: dict):
    """Yield every node in the tree (DFS). Public for use by extractor."""
    yield node
    for child in node.get("children") or []:
        yield from iter_nodes(child)
