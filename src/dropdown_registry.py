"""Template registry — the 27 known job-application questions.

This module is pure data + the helper resolvers that the templates reference.
It does not contain matching logic or the public entry point; see
``src/dropdown_templates.py`` for ``apply_templates``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from src.dropdown_match import match_education_to_level, match_years_to_range
from src.profile import Profile

# Field-type groupings used by templates.
TEXT_FIELD_TYPES = ("text", "email", "tel", "url", "textarea")
SELECT_FIELD_TYPES = ("select", "radio_group", "button_group", "combobox")
ALL_SELECTABLE = SELECT_FIELD_TYPES + ("checkbox",)


@dataclass
class FieldTemplate:
    """One known question — how to recognize it and how to answer it."""

    name: str
    label_patterns: list[str]
    field_types: tuple[str, ...]
    resolver: Callable[[Profile, dict[str, Any]], str | None]
    negative_patterns: list[str] = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Resolver helpers used by templates below
# ---------------------------------------------------------------------------


def _opts(field: dict) -> list[str]:
    """Extract clean option text list from a field dict."""
    raw = field.get("options") or []
    return [o.get("text", "") if isinstance(o, dict) else str(o) for o in raw if o]


def _full_name(p: Profile, _f: dict) -> str:
    return f"{p.first_name} {p.last_name}"


def _current_company(p: Profile, _f: dict) -> str | None:
    return p.experience[0].company if p.experience else None


def _years_text(p: Profile, _f: dict) -> str:
    return str(p.years_experience)


def _years_dropdown(p: Profile, field: dict) -> str | None:
    options = _opts(field)
    return match_years_to_range(p.years_experience, options) if options else None


def _education_level(p: Profile, field: dict) -> str | None:
    if not p.education:
        return None
    options = _opts(field)
    return match_education_to_level(p.education[0].degree, options) if options else None


# ---------------------------------------------------------------------------
# The 27 templates
# ---------------------------------------------------------------------------


TEMPLATE_MAP: list[FieldTemplate] = [
    # ---- Group A: simple text fields ----
    FieldTemplate(
        name="first_name",
        label_patterns=["first name", "given name", "firstname"],
        field_types=TEXT_FIELD_TYPES,
        resolver=lambda p, _f: p.first_name,
    ),
    FieldTemplate(
        name="last_name",
        label_patterns=["last name", "surname", "family name", "lastname"],
        field_types=TEXT_FIELD_TYPES,
        resolver=lambda p, _f: p.last_name,
    ),
    FieldTemplate(
        name="full_name",
        label_patterns=["full name", "your name", "applicant name"],
        field_types=TEXT_FIELD_TYPES,
        resolver=_full_name,
        negative_patterns=["first", "last", "company", "school", "university"],
    ),
    FieldTemplate(
        name="email",
        label_patterns=["email address", "e-mail", "email"],
        field_types=("text", "email"),
        resolver=lambda p, _f: p.email,
        negative_patterns=["alerts", "updates", "notifications", "newsletter"],
    ),
    FieldTemplate(
        name="phone",
        label_patterns=["phone number", "telephone", "mobile", "phone"],
        field_types=("text", "tel"),
        resolver=lambda p, _f: p.phone,
    ),
    FieldTemplate(
        name="linkedin",
        label_patterns=["linkedin"],
        field_types=("text", "url"),
        resolver=lambda p, _f: p.linkedin_url,
    ),
    FieldTemplate(
        name="github",
        label_patterns=["github"],
        field_types=("text", "url"),
        resolver=lambda p, _f: p.github_url,
    ),
    FieldTemplate(
        name="portfolio",
        label_patterns=["portfolio", "personal website", "website url"],
        field_types=("text", "url"),
        resolver=lambda p, _f: p.portfolio_url,
    ),
    FieldTemplate(
        name="location",
        label_patterns=["current location", "city", "where are you based", "location"],
        field_types=("text", "combobox"),
        resolver=lambda p, _f: p.location,
    ),
    FieldTemplate(
        name="current_company",
        label_patterns=["current company", "current employer", "employer"],
        field_types=TEXT_FIELD_TYPES,
        resolver=_current_company,
    ),
    FieldTemplate(
        name="current_title",
        label_patterns=["current role", "current title", "current position", "job title"],
        field_types=TEXT_FIELD_TYPES,
        resolver=lambda p, _f: p.current_title,
    ),
    FieldTemplate(
        name="years_text",
        label_patterns=["years of experience", "total experience", "how many years"],
        field_types=("text",),
        resolver=_years_text,
    ),
    # ---- Group B: yes/no fuzzy dropdowns ----
    FieldTemplate(
        name="work_auth",
        label_patterns=[
            "authorized to work",
            "work authorization",
            "legally authorized",
            "eligible to work",
            "legal right to work",
        ],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.work_authorization,
    ),
    FieldTemplate(
        name="sponsorship",
        label_patterns=[
            "require sponsorship",
            "need sponsorship",
            "visa sponsorship",
            "work visa",
            "immigration sponsorship",
        ],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.requires_sponsorship,
    ),
    FieldTemplate(
        name="age_18",
        label_patterns=["18 years or older", "at least 18", "are you 18"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda _p, _f: "Yes",
    ),
    FieldTemplate(
        name="prev_employed",
        label_patterns=[
            "previously employed",
            "prior employment",
            "worked here before",
            "worked for this company",
        ],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda _p, _f: "No",
    ),
    FieldTemplate(
        name="felony",
        label_patterns=["felony", "criminal conviction", "been convicted"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda _p, _f: "No",
    ),
    FieldTemplate(
        name="bg_check",
        label_patterns=["background check", "consent to background"],
        field_types=ALL_SELECTABLE,
        resolver=lambda _p, _f: "Yes",
    ),
    FieldTemplate(
        name="agree_terms",
        label_patterns=["agree to the terms", "privacy policy", "terms and conditions"],
        field_types=("checkbox",),
        resolver=lambda _p, _f: "Yes",
    ),
    # ---- Group C: EEO demographics ----
    FieldTemplate(
        name="gender",
        label_patterns=["gender"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.gender,
    ),
    FieldTemplate(
        name="hispanic_latino",
        label_patterns=["hispanic or latino", "hispanic/latino"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.hispanic_latino,
    ),
    FieldTemplate(
        name="ethnicity",
        label_patterns=["race", "ethnicity", "race/ethnicity"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.ethnicity,
        negative_patterns=["embrace", "tracer"],
    ),
    FieldTemplate(
        name="veteran",
        label_patterns=["veteran status", "protected veteran", "armed forces"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.veteran_status,
    ),
    FieldTemplate(
        name="disability",
        label_patterns=["disability status", "disabled", "have a disability"],
        field_types=SELECT_FIELD_TYPES,
        resolver=lambda p, _f: p.disability_status,
    ),
    # ---- Group D: specialized matchers ----
    FieldTemplate(
        name="years_dropdown",
        label_patterns=["years of experience", "experience level"],
        field_types=SELECT_FIELD_TYPES,
        resolver=_years_dropdown,
    ),
    FieldTemplate(
        name="education_level",
        label_patterns=["education level", "highest degree", "highest education"],
        field_types=SELECT_FIELD_TYPES,
        resolver=_education_level,
    ),
    FieldTemplate(
        name="start_date",
        label_patterns=["start date", "notice period", "when can you start", "earliest start"],
        field_types=ALL_SELECTABLE + TEXT_FIELD_TYPES,
        resolver=lambda p, _f: p.start_date,
    ),
]
