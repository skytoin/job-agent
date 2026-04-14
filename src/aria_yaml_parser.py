"""Convert Playwright's ``locator.aria_snapshot()`` YAML into a dict tree.

Playwright's ARIA snapshot looks like:

    - list:
      - listitem:
        - link "Whatnot":
          - /url: https://whatnot.com
    - textbox "Email *":
      - /placeholder: hello@example.com
    - text: Full Legal Name *

This module parses that YAML (it's valid YAML, just with Playwright-specific
key conventions) and converts it into the nested ``{role, name, children}``
dict shape that ``src/aria_extractor.py`` expects.

Why this exists: ``page.accessibility.snapshot()`` was removed in newer
Playwright Python versions. ``locator.aria_snapshot()`` is the supported
replacement but only returns YAML text, so we adapt it.
"""

from __future__ import annotations

import re

import yaml

# Match "role" or 'role "accessible name"' with optional [attribute] suffix.
_ROLE_NAME_RE = re.compile(r'^([\w-]+)(?:\s+"(.*?)")?(?:\s*\[.*\])?\s*$')


def parse_aria_yaml(yaml_text: str) -> dict | None:
    """Parse a Playwright ARIA YAML snapshot into a single root dict.

    Returns ``None`` when input is empty or unparseable.
    The returned dict has the same shape as ``page.accessibility.snapshot()``::

        {"role": "WebArea", "name": "", "children": [...]}
    """
    if not yaml_text or not yaml_text.strip():
        return None

    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None

    if loaded is None:
        return None

    children: list[dict] = []
    if isinstance(loaded, list):
        for item in loaded:
            converted = _convert(item)
            if converted:
                children.append(converted)
    else:
        converted = _convert(loaded)
        if converted:
            children.append(converted)

    return {"role": "WebArea", "name": "", "children": children}


def _convert(node) -> dict | None:
    """Recursively convert one parsed YAML node into our dict shape.

    Inputs come from ``yaml.safe_load`` so they are nested dicts/lists/strings.
    """
    if node is None:
        return None

    if isinstance(node, str):
        # Bare item like ``- listitem`` parses as the string ``"listitem"``.
        role, name = _parse_role_string(node)
        if not role:
            return None
        return {"role": role, "name": name, "children": []}

    if isinstance(node, dict):
        # Single-key dict where the key is a role + optional accessible name.
        if len(node) != 1:
            return None
        key = next(iter(node))
        value = node[key]

        if not isinstance(key, str):
            return None

        # Skip Playwright "attribute" entries like ``/url``, ``/placeholder``.
        if key.startswith("/"):
            return None

        role, name = _parse_role_string(key)
        if not role:
            return None

        # Value can be: None (empty), a string (text content), or a list (children).
        if value is None:
            return {"role": role, "name": name, "children": []}

        if isinstance(value, str):
            # ``- text: Full Legal Name *`` -> role=text, name="Full Legal Name *"
            return {"role": role, "name": name or value, "children": []}

        if isinstance(value, list):
            children: list[dict] = []
            for child in value:
                converted = _convert(child)
                if converted:
                    children.append(converted)
            return {"role": role, "name": name, "children": children}

    return None


def _parse_role_string(s: str) -> tuple[str, str]:
    """Parse ``'textbox "Email *"'`` -> ``("textbox", "Email *")``.

    Handles bare roles (``"list"``), optional attribute suffixes (``"[level=2]"``),
    and unmatchable strings (returns ``("", "")``).
    """
    s = s.strip()
    if not s:
        return ("", "")

    # Special cases: lone keywords like ``StaticText`` or ``WebArea``
    if " " not in s and '"' not in s and "[" not in s:
        return (s, "")

    m = _ROLE_NAME_RE.match(s)
    if not m:
        return ("", "")
    return (m.group(1), m.group(2) or "")
