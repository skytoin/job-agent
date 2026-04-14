"""Tests for src/aria_extractor.py.

Fixtures mimic the dict shape returned by ``page.accessibility.snapshot()``
across multiple ATS patterns (Ashby, Greenhouse, Lever, edge cases).
"""

from src.aria_extractor import extract_fields_from_aria
from src.aria_helpers import (
    attach_button_groups,
    clean_label,
    dedupe_by_label,
    is_required,
    reset_id_counter,
    synthetic_id,
)


def setup_function(_fn):
    reset_id_counter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_clean_label_strips_required_marker():
    assert clean_label("Email *") == "Email"
    assert clean_label("Email*") == "Email"
    assert clean_label("Phone (required)") == "Phone"
    assert clean_label("Name [required]") == "Name"
    assert clean_label("Email Address:") == "Email Address"
    assert clean_label("  Padded  ") == "Padded"


def test_is_required_detects_aria_flag():
    assert is_required({"required": True}, "Email") is True
    assert is_required({"required": False}, "Email") is False


def test_is_required_detects_label_marker():
    assert is_required({}, "Email *") is True
    assert is_required({}, "Email") is False


def test_synthetic_id_uses_label_slug():
    reset_id_counter()
    assert synthetic_id("textbox", "First Name").startswith("aria_textbox_first_name")
    assert synthetic_id("textbox", "").startswith("aria_textbox_")


def test_dedupe_keeps_first_of_duplicates():
    fields = [
        {"id": "a", "label": "Email", "type": "text"},
        {"id": "b", "label": "Email", "type": "text"},
        {"id": "c", "label": "Phone", "type": "text"},
    ]
    out = dedupe_by_label(fields)
    assert len(out) == 2
    assert out[0]["id"] == "a"
    assert out[1]["id"] == "c"


# ---------------------------------------------------------------------------
# Empty / null inputs
# ---------------------------------------------------------------------------


def test_empty_snapshot_returns_empty_list():
    assert extract_fields_from_aria(None) == []
    assert extract_fields_from_aria({}) == []
    assert extract_fields_from_aria({"role": "WebArea", "children": []}) == []


# ---------------------------------------------------------------------------
# Textbox extraction
# ---------------------------------------------------------------------------


def test_simple_textbox():
    snapshot = {
        "role": "WebArea",
        "children": [
            {"role": "textbox", "name": "Email Address *", "value": "", "required": True},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "text"
    assert fields[0]["label"] == "Email Address"
    assert fields[0]["required"] is True


def test_textbox_with_existing_value():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "textbox", "name": "Phone", "value": "+1-555-0000"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert fields[0]["value"] == "+1-555-0000"


def test_textbox_inherits_parent_label_when_unnamed():
    snapshot = {
        "role": "form",
        "children": [
            {
                "role": "group",
                "name": "Personal Info",
                "children": [
                    {"role": "textbox", "name": ""},
                ],
            }
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert fields[0]["label"] == "Personal Info"


# ---------------------------------------------------------------------------
# Combobox
# ---------------------------------------------------------------------------


def test_combobox_extraction():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "combobox", "name": "Country", "required": True},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "combobox"
    assert fields[0]["label"] == "Country"
    assert fields[0]["required"] is True


# ---------------------------------------------------------------------------
# Radio groups — multiple structural patterns
# ---------------------------------------------------------------------------


def test_radiogroup_proper_aria():
    """Greenhouse pattern: native radiogroup role."""
    snapshot = {
        "role": "form",
        "children": [
            {
                "role": "radiogroup",
                "name": "Hub Location *",
                "required": True,
                "children": [
                    {"role": "radio", "name": "New York"},
                    {"role": "radio", "name": "San Francisco"},
                    {"role": "radio", "name": "Remote"},
                ],
            }
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "radio_group"
    assert fields[0]["label"] == "Hub Location"
    assert fields[0]["required"] is True
    assert len(fields[0]["options"]) == 3
    assert fields[0]["options"][0]["text"] == "New York"


def test_radio_group_inside_generic_group():
    """Ashby pattern: group container with radio children."""
    snapshot = {
        "role": "form",
        "children": [
            {
                "role": "group",
                "name": "Are you authorized to work in the US? *",
                "children": [
                    {"role": "radio", "name": "Yes"},
                    {"role": "radio", "name": "No"},
                ],
            }
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "radio_group"
    assert fields[0]["label"] == "Are you authorized to work in the US?"


def test_group_with_empty_name_uses_first_text_child():
    snapshot = {
        "role": "form",
        "children": [
            {
                "role": "group",
                "name": "",
                "children": [
                    {"role": "text", "name": "Pick your shift *"},
                    {"role": "radio", "name": "Morning"},
                    {"role": "radio", "name": "Evening"},
                ],
            }
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["label"] == "Pick your shift"
    assert fields[0]["type"] == "radio_group"


# ---------------------------------------------------------------------------
# Checkbox groups
# ---------------------------------------------------------------------------


def test_checkbox_group():
    """Multi-select question with checkboxes inside a group."""
    snapshot = {
        "role": "form",
        "children": [
            {
                "role": "group",
                "name": "How did you hear about us? *",
                "children": [
                    {"role": "checkbox", "name": "LinkedIn"},
                    {"role": "checkbox", "name": "Indeed"},
                    {"role": "checkbox", "name": "Referral"},
                ],
            }
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox_group"
    assert len(fields[0]["options"]) == 3


# ---------------------------------------------------------------------------
# Single checkbox (consent box, terms agreement)
# ---------------------------------------------------------------------------


def test_single_checkbox():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "checkbox", "name": "I agree to the privacy policy", "checked": False},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox"
    assert fields[0]["label"] == "I agree to the privacy policy"


# ---------------------------------------------------------------------------
# Button groups (Yes/No with adjacent buttons)
# ---------------------------------------------------------------------------


def test_button_group_yes_no():
    """Ashby/Lever pattern: text question + 2 buttons (no group container)."""
    snapshot = {
        "role": "form",
        "children": [
            {"role": "text", "name": "Are you legally authorized to work in the US? *"},
            {"role": "button", "name": "Yes"},
            {"role": "button", "name": "No"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    btn_groups = [f for f in fields if f["type"] == "button_group"]
    assert len(btn_groups) == 1
    assert btn_groups[0]["label"] == "Are you legally authorized to work in the US?"
    assert len(btn_groups[0]["options"]) == 2
    assert btn_groups[0]["options"][0]["text"] == "Yes"


def test_two_button_groups_back_to_back():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "text", "name": "Authorized to work? *"},
            {"role": "button", "name": "Yes"},
            {"role": "button", "name": "No"},
            {"role": "text", "name": "Need sponsorship? *"},
            {"role": "button", "name": "Yes"},
            {"role": "button", "name": "No"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    btn_groups = [f for f in fields if f["type"] == "button_group"]
    assert len(btn_groups) == 2
    labels = [g["label"] for g in btn_groups]
    assert "Authorized to work?" in labels
    assert "Need sponsorship?" in labels


def test_button_group_does_not_match_lone_buttons():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "text", "name": "Random heading"},
            {"role": "button", "name": "Submit"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    btn_groups = [f for f in fields if f["type"] == "button_group"]
    assert btn_groups == []


# ---------------------------------------------------------------------------
# End-to-end realistic ATS fixtures
# ---------------------------------------------------------------------------


def test_full_ashby_form_realistic():
    """Mimic the Whatnot form structure we probed live."""
    snapshot = {
        "role": "form",
        "children": [
            {"role": "textbox", "name": "Full Legal Name *"},
            {"role": "textbox", "name": "Email *"},
            {"role": "textbox", "name": "Phone Number *"},
            {"role": "textbox", "name": "Preferred First Name *"},
            {"role": "textbox", "name": "Preferred Last Name *"},
            {"role": "textbox", "name": "Linkedin Profile or Website *"},
            {
                "role": "group",
                "name": "How did you hear about this opportunity? *",
                "children": [
                    {"role": "checkbox", "name": "LinkedIn"},
                    {"role": "checkbox", "name": "Glassdoor"},
                    {"role": "checkbox", "name": "Other"},
                ],
            },
            {
                "role": "group",
                "name": "Hub Location *",
                "children": [
                    {"role": "radio", "name": "New York, NY"},
                    {"role": "radio", "name": "San Francisco, CA"},
                ],
            },
            {"role": "textbox", "name": "City and state *"},
            {"role": "text", "name": "Are you legally authorized to work in the US? *"},
            {"role": "button", "name": "Yes"},
            {"role": "button", "name": "No"},
            {"role": "text", "name": "Will you require visa sponsorship? *"},
            {"role": "button", "name": "Yes"},
            {"role": "button", "name": "No"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)

    by_type = {}
    for f in fields:
        by_type.setdefault(f["type"], []).append(f)

    assert (
        len(by_type.get("text", [])) == 7
    )  # Full Legal Name + Email + Phone + 2 Pref + LinkedIn + city
    assert len(by_type.get("checkbox_group", [])) == 1
    assert len(by_type.get("radio_group", [])) == 1
    assert len(by_type.get("button_group", [])) == 2

    # Make sure required markers are stripped from labels
    for f in fields:
        assert "*" not in f["label"]


def test_skipped_roles_dont_pollute_fields():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "img", "name": "Logo"},
            {"role": "navigation", "name": "Header"},
            {"role": "textbox", "name": "Email *"},
            {"role": "img", "name": "Decoration"},
        ],
    }
    fields = extract_fields_from_aria(snapshot)
    assert len(fields) == 1
    assert fields[0]["type"] == "text"


# ---------------------------------------------------------------------------
# attach_button_groups also runs as a post-pass; verify integration
# ---------------------------------------------------------------------------


def test_attach_button_groups_standalone():
    snapshot = {
        "role": "form",
        "children": [
            {"role": "text", "name": "Pick one *"},
            {"role": "button", "name": "Option A"},
            {"role": "button", "name": "Option B"},
            {"role": "button", "name": "Option C"},
        ],
    }
    out: list[dict] = []
    attach_button_groups(snapshot, out)
    assert len(out) == 1
    assert out[0]["type"] == "button_group"
    assert len(out[0]["options"]) == 3
