"""Tests for the Layer 0 dropdown template system.

Covers:
  - label matching positives for each template group
  - false-positive guards (negative patterns, unrelated labels)
  - specificity ordering (more specific pattern wins)
  - field-type filtering (email pattern shouldn't match a checkbox)
  - specialized matchers (years range, education level)
  - fuzzy_pick_option behavior
  - apply_templates end-to-end on a realistic field list
"""

from src.dropdown_match import (
    fuzzy_pick_option,
    match_education_to_level,
    match_years_to_range,
    parse_year_range,
)
from src.dropdown_registry import TEMPLATE_MAP
from src.dropdown_templates import apply_templates, match_field_to_template
from src.profile import Profile


def _sample_profile(**overrides) -> Profile:
    defaults = {
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.com",
        "phone": "+1-555-000-0000",
        "location": "Brooklyn, NY",
        "linkedin_url": "https://linkedin.com/in/test",
        "github_url": "https://github.com/test",
        "current_title": "ML Engineer",
        "years_experience": 5,
        "summary": "Test summary",
        "education": [{"school": "MIT", "degree": "MS Computer Science", "year": 2022}],
        "experience": [
            {
                "company": "TestCorp",
                "title": "Engineer",
                "dates": "2020-2024",
                "bullets": ["Did things"],
            }
        ],
        "skills": ["Python", "PyTorch"],
        "resume_path": "/tmp/resume.pdf",
    }
    defaults.update(overrides)
    return Profile(**defaults)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


def test_template_count_is_27():
    assert len(TEMPLATE_MAP) == 27


def test_every_template_has_patterns_and_resolver():
    for t in TEMPLATE_MAP:
        assert t.name
        assert t.label_patterns, f"{t.name} has no label patterns"
        assert t.field_types, f"{t.name} has no field types"
        assert callable(t.resolver), f"{t.name} resolver must be callable"


# ---------------------------------------------------------------------------
# Label matching — positives
# ---------------------------------------------------------------------------


def test_match_first_name_variants():
    for label in ["First Name", "FIRST NAME", "Given name", "First name *"]:
        t = match_field_to_template(label, "text")
        assert t is not None and t.name == "first_name", label


def test_match_work_authorization_variants():
    labels = [
        "Are you legally authorized to work in the United States?",
        "Work authorization",
        "Eligible to work in the US",
    ]
    for label in labels:
        t = match_field_to_template(label, "select")
        assert t is not None and t.name == "work_auth", label


def test_match_sponsorship_variants():
    labels = [
        "Will you now or in the future require sponsorship?",
        "Visa sponsorship required",
        "Do you need sponsorship to work?",
    ]
    for label in labels:
        t = match_field_to_template(label, "select")
        assert t is not None and t.name == "sponsorship", label


def test_match_eeo_demographics():
    cases = [
        ("Gender", "gender"),
        ("Hispanic or Latino?", "hispanic_latino"),
        ("Race/Ethnicity", "ethnicity"),
        ("Veteran status", "veteran"),
        ("Disability status", "disability"),
    ]
    for label, expected in cases:
        t = match_field_to_template(label, "select")
        assert t is not None and t.name == expected, label


# ---------------------------------------------------------------------------
# Label matching — false positive guards
# ---------------------------------------------------------------------------


def test_email_pattern_rejects_alerts_checkbox():
    t = match_field_to_template("Email me job alerts", "checkbox")
    assert t is None


def test_email_pattern_rejects_newsletter():
    t = match_field_to_template("Sign up for our newsletter email", "checkbox")
    assert t is None


def test_unrelated_question_returns_none():
    assert match_field_to_template("What is your favorite color?", "text") is None


def test_ethnicity_rejects_embrace_diversity():
    assert match_field_to_template("We embrace diversity", "text") is None


def test_empty_label_returns_none():
    assert match_field_to_template("", "text") is None


# ---------------------------------------------------------------------------
# Specificity & field-type filtering
# ---------------------------------------------------------------------------


def test_email_address_beats_generic_email():
    t = match_field_to_template("Email address", "text")
    assert t is not None and t.name == "email"


def test_email_pattern_does_not_match_select_type():
    # Email should only match text/email field types, not radio_group
    t = match_field_to_template("Email", "radio_group")
    assert t is None


# ---------------------------------------------------------------------------
# fuzzy_pick_option
# ---------------------------------------------------------------------------


def test_fuzzy_pick_exact_match():
    result = fuzzy_pick_option("Yes", ["Yes", "No", "Maybe"])
    assert result == "Yes"


def test_fuzzy_pick_case_insensitive():
    result = fuzzy_pick_option("yes", ["Yes", "No"])
    assert result == "Yes"


def test_fuzzy_pick_partial_match():
    result = fuzzy_pick_option(
        "Yes - US Citizen",
        ["Yes, I am a US Citizen", "No, I need sponsorship", "Prefer not to say"],
    )
    assert result == "Yes, I am a US Citizen"


def test_fuzzy_pick_no_match_returns_none():
    result = fuzzy_pick_option("completely unrelated", ["Yes", "No"])
    assert result is None


def test_fuzzy_pick_empty_options_returns_none():
    assert fuzzy_pick_option("Yes", []) is None
    assert fuzzy_pick_option("", ["Yes", "No"]) is None


# ---------------------------------------------------------------------------
# Specialized matchers — years to range
# ---------------------------------------------------------------------------


def test_parse_year_range_basic():
    assert parse_year_range("3-5 years") == (3, 5)
    assert parse_year_range("1 to 3") == (1, 3)
    assert parse_year_range("10+") == (10, 99)
    assert parse_year_range("10 or more") == (10, 99)
    assert parse_year_range("Less than 1 year") == (0, 0)
    assert parse_year_range("5 years") == (5, 5)
    assert parse_year_range("Entry level") is None


def test_match_years_picks_containing_range():
    options = ["0-1", "1-3", "3-5", "5-7", "7-10", "10+"]
    assert match_years_to_range(5, options) == "3-5" or match_years_to_range(5, options) == "5-7"
    assert match_years_to_range(0, options) == "0-1"
    assert match_years_to_range(15, options) == "10+"


def test_match_years_no_match_returns_none():
    # Only seniority labels — none parseable
    assert match_years_to_range(5, ["Entry", "Mid", "Senior"]) is None


# ---------------------------------------------------------------------------
# Specialized matchers — education level
# ---------------------------------------------------------------------------


def test_match_education_master_variants():
    options = ["High School", "Bachelor's Degree", "Master's Degree", "PhD"]
    assert match_education_to_level("MS Computer Science", options) == "Master's Degree"
    assert match_education_to_level("MBA", options) == "Master's Degree"


def test_match_education_bachelor():
    options = ["HS diploma", "Bachelors", "Masters", "Doctorate"]
    assert match_education_to_level("BS Math", options) == "Bachelors"


def test_match_education_doctorate():
    options = ["High School", "Bachelor's", "Master's", "Doctoral"]
    assert match_education_to_level("PhD Physics", options) == "Doctoral"


def test_match_education_no_match():
    assert match_education_to_level("Unknown cert", ["HS", "BA", "MA"]) is None


# ---------------------------------------------------------------------------
# apply_templates — end to end
# ---------------------------------------------------------------------------


def test_apply_templates_text_field():
    profile = _sample_profile()
    fields = [
        {"id": "f1", "label": "First Name", "type": "text"},
        {"id": "f2", "label": "Email address", "type": "email"},
    ]
    pre_filled, unmatched = apply_templates(fields, profile)
    assert pre_filled == {"f1": "Test", "f2": "test@example.com"}
    assert unmatched == []


def test_apply_templates_dropdown_fuzzy_match():
    profile = _sample_profile()
    fields = [
        {
            "id": "f1",
            "label": "Are you legally authorized to work in the US?",
            "type": "select",
            "options": [
                {"text": "Yes, I am authorized", "value": "yes"},
                {"text": "No, I need sponsorship", "value": "no"},
            ],
        }
    ]
    pre_filled, unmatched = apply_templates(fields, profile)
    assert "f1" in pre_filled
    assert "Yes" in pre_filled["f1"]
    assert unmatched == []


def test_apply_templates_unknown_field_passes_through():
    profile = _sample_profile()
    fields = [{"id": "f1", "label": "What is your favorite color?", "type": "text"}]
    pre_filled, unmatched = apply_templates(fields, profile)
    assert pre_filled == {}
    assert len(unmatched) == 1


def test_apply_templates_mixed():
    profile = _sample_profile()
    fields = [
        {"id": "f1", "label": "First Name", "type": "text"},
        {"id": "f2", "label": "Favorite ice cream flavor", "type": "text"},
        {"id": "f3", "label": "Phone Number", "type": "tel"},
    ]
    pre_filled, unmatched = apply_templates(fields, profile)
    assert pre_filled == {"f1": "Test", "f3": "+1-555-000-0000"}
    assert len(unmatched) == 1 and unmatched[0]["id"] == "f2"


def test_apply_templates_empty_input():
    profile = _sample_profile()
    assert apply_templates([], profile) == ({}, [])


def test_apply_templates_field_with_no_id_goes_to_unmatched():
    profile = _sample_profile()
    fields = [{"id": "", "name": "", "label": "First Name", "type": "text"}]
    pre_filled, unmatched = apply_templates(fields, profile)
    assert pre_filled == {}
    assert len(unmatched) == 1
