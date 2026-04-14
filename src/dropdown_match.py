"""Low-level matching primitives for the dropdown template system.

No dependency on ``FieldTemplate`` or ``TEMPLATE_MAP`` — these are pure helper
functions used by ``src/dropdown_templates.py``.

Contains:
  - ``fuzzy_pick_option``: rapidfuzz wrapper for best-match option selection
  - ``match_years_to_range``: numeric range matching for "Years of experience" dropdowns
  - ``match_education_to_level``: degree → level keyword mapping
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# Fuzzy score thresholds (0-100 scale from rapidfuzz).
# The absolute floor is low because job-application labels vary a lot — we
# rely mainly on the RELATIVE gap between top and runner-up to avoid picking
# ambiguous matches.
OPTION_ABS_MIN_SCORE = 50  # floor: a match must score at least this
OPTION_GAP_RATIO = 1.3  # top must beat runner-up by at least this ratio


def fuzzy_pick_option(
    target: str,
    options: list[str],
    abs_min_score: int = OPTION_ABS_MIN_SCORE,
    gap_ratio: float = OPTION_GAP_RATIO,
) -> str | None:
    """Return the option that best matches ``target`` with ambiguity guard.

    Uses rapidfuzz's ``WRatio`` (case-insensitive) to score every option, then
    applies two safety checks so we never silently pick a "least wrong" option:

    1. The top option's score must be at least ``abs_min_score``.
    2. When there are multiple options, the top option's score must be at
       least ``gap_ratio`` times the runner-up's score. This protects against
       cases like ``("5-7 years", "5-10 years")`` where both score similarly.

    Returns ``None`` if either check fails.
    """
    if not options or not target:
        return None

    target_lc = target.lower()
    scored = [(opt, fuzz.WRatio(target_lc, opt.lower())) for opt in options]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    top_opt, top_score = scored[0]
    if top_score < abs_min_score:
        return None

    if len(scored) >= 2:
        second_score = scored[1][1]
        if second_score > 0 and top_score < second_score * gap_ratio:
            return None

    return top_opt


def parse_year_range(text: str) -> tuple[int, int] | None:
    """Parse a dropdown option into an inclusive (low, high) year range.

    Understands: ``"0-1"``, ``"1-3 years"``, ``"5+"``, ``"10 or more"``,
    ``"Less than 1"``, ``"5 years"``. Returns ``None`` for unparseable text
    (seniority labels like "Entry level" intentionally skipped).
    """
    s = text.lower().strip()

    if "less than" in s:
        m = re.search(r"less than\s*(\d+)", s)
        if m:
            return (0, max(0, int(m.group(1)) - 1))

    m = re.search(r"(\d+)\s*\+", s) or re.search(r"(\d+)\s*or\s*more", s)
    if m:
        return (int(m.group(1)), 99)

    m = re.search(r"(\d+)\s*[-–to]+\s*(\d+)", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))

    m = re.search(r"(\d+)\s*year", s)
    if m:
        n = int(m.group(1))
        return (n, n)

    return None


def match_years_to_range(years: int, options: list[str]) -> str | None:
    """Pick the option whose numeric range contains ``years``.

    When multiple ranges match, the narrowest wins (ties broken by first
    encountered). Returns ``None`` if no option parses or contains ``years``.
    """
    best: tuple[str, int] | None = None  # (option_text, span)

    for opt in options:
        parsed = parse_year_range(opt)
        if parsed is None:
            continue
        low, high = parsed
        if low <= years <= high:
            span = high - low
            if best is None or span < best[1]:
                best = (opt, span)

    return best[0] if best else None


# Degree keyword → education level. Ordered highest to lowest; first hit wins.
_DEGREE_LEVEL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("doctorate", ("phd", "ph.d", "doctorate", "doctoral")),
    ("master", ("master", "ms ", "m.s.", "msc", "m.a.", "mba")),
    ("bachelor", ("bachelor", "bs ", "b.s.", "ba ", "b.a.", "undergraduate")),
    ("associate", ("associate", "aa ", "a.a.", "aas")),
    ("highschool", ("high school", "hs diploma", "ged")),
]

# Education level → substrings that identify a matching dropdown option.
_LEVEL_OPTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "doctorate": ("phd", "ph.d", "doctorate", "doctoral"),
    "master": ("master", "ms", "m.s", "mba"),
    "bachelor": ("bachelor", "bs", "b.s", "ba", "b.a", "4-year"),
    "associate": ("associate", "aa", "a.a", "2-year"),
    "highschool": ("high school", "ged", "diploma"),
}


def match_education_to_level(degree: str, options: list[str]) -> str | None:
    """Map a degree string (e.g. "MS Computer Science") to the best option.

    Two stages: first classify the degree into a canonical level
    (doctorate/master/bachelor/associate/highschool), then scan ``options`` for
    the first one whose text contains a matching keyword for that level.
    """
    degree_lower = degree.lower()

    target_level: str | None = None
    for level, keywords in _DEGREE_LEVEL_KEYWORDS:
        if any(kw in degree_lower for kw in keywords):
            target_level = level
            break

    if target_level is None:
        return None

    matching_keywords = _LEVEL_OPTION_KEYWORDS[target_level]
    for opt in options:
        opt_lower = opt.lower()
        if any(kw in opt_lower for kw in matching_keywords):
            return opt

    return None


def label_contains(label: str, pattern: str) -> bool:
    """True if ``label`` contains ``pattern`` (case-insensitive substring)."""
    return pattern.lower() in label.lower()
