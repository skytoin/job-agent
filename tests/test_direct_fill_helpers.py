"""Regression tests for direct_fill helpers.

Covers:
  - Bug #4: CSS ID selectors crashed on UUID-style IDs starting with a digit
  - Bug #5: dedup didn't normalize away required markers, so ARIA "Email"
    and JS "Email *" appeared as two distinct fields
  - Bug #6: ARIA synthetic IDs didn't match real DOM elements (Option B fix)
  - Bug #7 (2026-04-14): JS extractor over-counted checkbox_group members
    as individual fields (ghost filter fix)
  - New: required-field threshold check, prefill-hint collection
"""

from src.direct_fill import (
    _build_prefill_hints,
    _check_required_fields_covered,
    _normalize_label_for_dedup,
    merge_field_lists,
)

# ---------------------------------------------------------------------------
# Bug #5: dedup label normalizer
# ---------------------------------------------------------------------------


def test_normalize_strips_trailing_required_marker():
    assert _normalize_label_for_dedup("Email *") == "email"
    assert _normalize_label_for_dedup("Email*") == "email"
    assert _normalize_label_for_dedup("Phone (required)") == "phone"
    assert _normalize_label_for_dedup("Name [required]") == "name"
    assert _normalize_label_for_dedup("Notes (optional)") == "notes"


def test_normalize_strips_punctuation_and_whitespace():
    assert _normalize_label_for_dedup("  Email  ") == "email"
    assert _normalize_label_for_dedup("Email Address:") == "email address"
    assert _normalize_label_for_dedup("FULL LEGAL NAME *") == "full legal name"


def test_normalize_handles_empty_and_none_safely():
    assert _normalize_label_for_dedup("") == ""
    assert _normalize_label_for_dedup("   ") == ""


# ---------------------------------------------------------------------------
# Bug #5: merge dedupes ARIA(stripped) + JS(with asterisk)
# ---------------------------------------------------------------------------


def test_merge_dedupes_starred_label_against_clean():
    """The exact pattern that caused 28 fields when there should be ~22."""
    aria_fields = [
        {"id": "aria_email_1", "label": "Email", "type": "text"},
        {"id": "aria_phone_1", "label": "Phone Number", "type": "text"},
    ]
    js_fields = [
        {"id": "js_email_1", "label": "Email *", "type": "text"},
        {"id": "js_phone_1", "label": "Phone Number *", "type": "text"},
        {"id": "js_other_1", "label": "Cover letter", "type": "textarea"},
    ]
    merged = merge_field_lists(aria_fields, js_fields)
    labels = [f["label"] for f in merged]

    assert len(merged) == 3, f"Expected 3 fields after dedup, got {len(merged)}: {labels}"
    assert "Email" in labels  # ARIA's clean label wins
    assert "Cover letter" in labels  # JS-only field kept
    assert "Email *" not in labels


def test_merge_keeps_aria_before_js_unique_fields():
    aria_fields = [{"id": "a1", "label": "Field A", "type": "text"}]
    js_fields = [{"id": "j1", "label": "Field B", "type": "text"}]
    merged = merge_field_lists(aria_fields, js_fields)
    assert [f["label"] for f in merged] == ["Field A", "Field B"]


def test_merge_skips_aria_with_empty_label():
    aria_fields = [{"id": "a1", "label": "", "type": "text"}]
    js_fields = [{"id": "j1", "label": "Email", "type": "text"}]
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1
    assert merged[0]["label"] == "Email"


def test_merge_dedupes_same_label_different_types():
    """Under Option B, ARIA and JS reporting different types for the same
    label is the EXPECTED case (ARIA sees ``text`` for an ``<input
    type="email">`` while JS sees ``email``). They must dedupe to one field
    using the JS ID."""
    aria_fields = [{"id": "a1", "label": "Status", "type": "text"}]
    js_fields = [{"id": "j1", "label": "Status", "type": "select"}]
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1
    assert merged[0]["id"] == "j1"  # JS ID wins


# ---------------------------------------------------------------------------
# Bug #4: CSS selector format documentation
# ---------------------------------------------------------------------------
# We can't easily unit-test the live Playwright query without a browser, so
# instead we test the SHAPE of the selector string we'd construct. Real
# Playwright will accept ``[id='...']`` for any string but reject ``#7abc``.


def test_attribute_selector_works_for_digit_starting_id():
    """``[id='7abc']`` is a valid CSS attribute selector, ``#7abc`` is not."""
    field_id = "7ed44ab8-d75a-4674-8fdc-6257f0e2baff_00e55bbf-labeled-checkbox-5"
    selector = f"[id='{field_id}']"
    assert selector.startswith("[id='")
    assert field_id in selector
    # The OLD broken format would have produced "#7ed44ab8..." which CSS rejects.
    assert not selector.startswith("#")


def test_attribute_selector_works_for_uuid_with_hyphens():
    field_id = "abc-def-123_xyz"
    selector = f"[id='{field_id}']"
    # Hyphens are valid in CSS IDs, but underscores around digits are flaky.
    # Attribute selectors sidestep all of these rules.
    assert "[id='abc-def-123_xyz']" == selector


# ---------------------------------------------------------------------------
# Option B: label-based ARIA -> JS matching
# ---------------------------------------------------------------------------


def test_merge_dedupes_email_type_mismatch():
    """The exact bug from the $0.30 run: ARIA gives type='text' for an email
    input, JS gives type='email'. Old dedup keyed on (label, type) and missed
    the match. New label-based merge should produce ONE entry."""
    aria_fields = [
        {
            "id": "aria_textbox_email_1",
            "label": "Email",
            "type": "text",
            "options": [],
        }
    ]
    js_fields = [
        {
            "id": "_systemfield_email",
            "label": "Email *",
            "type": "email",
            "options": [],
        }
    ]
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1, f"Expected 1 merged field, got {len(merged)}: {merged}"
    # The result should use JS's REAL ID (not ARIA's synthetic one)
    assert merged[0]["id"] == "_systemfield_email"
    # And ARIA's cleaner label (no asterisk)
    assert merged[0]["label"] == "Email"


def test_merge_promotes_aria_options_to_js_field():
    """If ARIA found options for a field but JS didn't, keep them."""
    aria_fields = [
        {
            "id": "aria_radiogroup_loc_1",
            "label": "Hub Location",
            "type": "radio_group",
            "options": [{"text": "New York", "value": "ny"}],
        }
    ]
    js_fields = [
        {
            "id": "real_loc_id",
            "label": "Hub Location *",
            "type": "radio_group",
            "options": [],
        }
    ]
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1
    assert merged[0]["id"] == "real_loc_id"
    assert merged[0]["options"] == [{"text": "New York", "value": "ny"}]


def test_merge_aria_only_field_marked_label_based():
    """Yes/No button groups exist in ARIA but not in JS extractor output.
    These should survive the merge and carry the ``_label_based`` marker so
    fill_fields_js uses text-based locators instead of the synthetic ID."""
    aria_fields = [
        {
            "id": "aria_buttongroup_work_auth_3",
            "label": "Are you legally authorized to work in the US?",
            "type": "button_group",
            "options": [{"text": "Yes", "value": "Yes"}, {"text": "No", "value": "No"}],
        }
    ]
    js_fields = []
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1
    assert merged[0]["_label_based"] is True
    assert merged[0]["type"] == "button_group"
    assert merged[0]["label"].startswith("Are you legally authorized")


def test_merge_keeps_js_only_fields_unchanged():
    """JS fields with no ARIA counterpart pass through untouched and are NOT
    flagged as label-based (they have real DOM IDs)."""
    aria_fields = []
    js_fields = [
        {"id": "real_text_1", "label": "Custom Question", "type": "text"},
    ]
    merged = merge_field_lists(aria_fields, js_fields)
    assert len(merged) == 1
    assert merged[0]["id"] == "real_text_1"
    assert "_label_based" not in merged[0]


def test_ghost_filter_drops_js_checkboxes_covered_by_aria_group():
    """The exact Whatnot bug: ARIA extracts 'How did you hear' as ONE
    checkbox_group with 10 options. JS also extracts each of the 10
    checkbox option elements as individual fields. Those 10 JS fields
    are ghosts — they should be dropped because ARIA's group covers them."""
    aria_fields = [
        {
            "id": "aria_checkboxgroup_how_did_you_hear_1",
            "label": "How did you hear about us?",
            "type": "checkbox_group",
            "options": [
                {"text": "LinkedIn", "value": "LinkedIn"},
                {"text": "Glassdoor", "value": "Glassdoor"},
                {"text": "Referral", "value": "Referral"},
            ],
        }
    ]
    js_fields = [
        {"id": "cb_0", "label": "LinkedIn", "type": "checkbox"},
        {"id": "cb_1", "label": "Glassdoor", "type": "checkbox"},
        {"id": "cb_2", "label": "Referral", "type": "checkbox"},
        {"id": "_systemfield_name", "label": "Full Legal Name", "type": "text"},
    ]
    merged = merge_field_lists(aria_fields, js_fields)

    types = {f["type"]: 0 for f in merged}
    for f in merged:
        types[f["type"]] = types.get(f["type"], 0) + 1

    assert types.get("checkbox_group") == 1
    assert types.get("checkbox", 0) == 0, f"All 3 JS checkboxes should be dropped: {merged}"
    assert types.get("text") == 1  # the unique Full Legal Name
    assert len(merged) == 2


def test_ghost_filter_keeps_unrelated_checkboxes():
    """Standalone consent checkboxes (not part of an ARIA group) must NOT be
    dropped by the ghost filter."""
    aria_fields = [
        {
            "id": "aria_checkboxgroup_how_did_you_hear_1",
            "label": "How did you hear about us?",
            "type": "checkbox_group",
            "options": [{"text": "LinkedIn", "value": "LinkedIn"}],
        }
    ]
    js_fields = [
        {"id": "cb_0", "label": "LinkedIn", "type": "checkbox"},  # ghost, drop
        {"id": "cb_terms", "label": "I agree to the terms", "type": "checkbox"},  # keep
    ]
    merged = merge_field_lists(aria_fields, js_fields)
    labels = [f["label"] for f in merged]
    assert "I agree to the terms" in labels
    assert len([f for f in merged if f["type"] == "checkbox"]) == 1


def test_merge_realistic_whatnot_form_no_synthetic_ids_for_overlap():
    """End-to-end: simulate the Whatnot form with both extractors active.

    The dual extraction should produce a clean list where every overlap uses
    JS's real ID, and only ARIA-unique fields (button groups) keep synthetic
    IDs WITH the _label_based flag."""
    aria_fields = [
        {"id": "aria_textbox_email_1", "label": "Email", "type": "text"},
        {"id": "aria_textbox_phone_2", "label": "Phone Number", "type": "text"},
        {
            "id": "aria_buttongroup_auth_3",
            "label": "Are you authorized to work?",
            "type": "button_group",
            "options": [{"text": "Yes", "value": "Yes"}, {"text": "No", "value": "No"}],
        },
        {
            "id": "aria_buttongroup_sponsor_4",
            "label": "Will you require sponsorship?",
            "type": "button_group",
            "options": [{"text": "Yes", "value": "Yes"}, {"text": "No", "value": "No"}],
        },
    ]
    js_fields = [
        {"id": "_systemfield_email", "label": "Email *", "type": "email"},
        {"id": "_systemfield_phone", "label": "Phone Number *", "type": "tel"},
        {"id": "_systemfield_name", "label": "Full Legal Name *", "type": "text"},
    ]
    merged = merge_field_lists(aria_fields, js_fields)

    assert len(merged) == 5, f"Expected 5 unique fields, got {len(merged)}"

    by_id = {f["id"]: f for f in merged}

    assert "_systemfield_email" in by_id
    assert "_label_based" not in by_id["_systemfield_email"]

    assert "_systemfield_name" in by_id
    assert "_label_based" not in by_id["_systemfield_name"]

    aria_ids = [f["id"] for f in merged if f["id"].startswith("aria_")]
    assert len(aria_ids) == 2
    for aid in aria_ids:
        assert by_id[aid]["_label_based"] is True
        assert by_id[aid]["type"] == "button_group"


# ---------------------------------------------------------------------------
# Required-field check (replaces the 50% threshold)
# ---------------------------------------------------------------------------


def test_required_check_all_filled_returns_ok():
    fields = [
        {"id": "a", "label": "Email", "type": "email", "required": True},
        {"id": "b", "label": "Phone", "type": "tel", "required": True},
        {"id": "c", "label": "Notes", "type": "textarea", "required": False},
    ]
    mapping = {"a": "x@y.com", "b": "555-1234"}
    filled = ["a", "b"]
    ok, missing = _check_required_fields_covered(fields, mapping, filled)
    assert ok is True
    assert missing == []


def test_required_check_missing_required_returns_fail():
    fields = [
        {"id": "a", "label": "Email", "type": "email", "required": True},
        {"id": "b", "label": "Work Authorization", "type": "button_group", "required": True},
    ]
    mapping = {"a": "x@y.com", "b": "Yes"}
    filled = ["a"]  # Work Authorization not filled
    ok, missing = _check_required_fields_covered(fields, mapping, filled)
    assert ok is False
    assert "Work Authorization" in missing


def test_required_check_no_required_fields_returns_ok_fallthrough():
    """Lazy ATS with no *-marked fields — fall through to Option C behavior."""
    fields = [
        {"id": "a", "label": "Email", "type": "email", "required": False},
        {"id": "b", "label": "Phone", "type": "tel"},  # no required key at all
    ]
    mapping = {"a": "x@y.com"}
    filled = ["a"]
    ok, missing = _check_required_fields_covered(fields, mapping, filled)
    # No required fields detected → "covered" by default (let caller try submit)
    assert ok is True
    assert missing == []


def test_required_check_optional_unfilled_still_passes():
    """Skipping optional fields is allowed."""
    fields = [
        {"id": "a", "label": "Email", "type": "email", "required": True},
        {"id": "b", "label": "Portfolio URL", "type": "url", "required": False},
    ]
    mapping = {"a": "x@y.com"}  # No portfolio mapping
    filled = ["a"]
    ok, _ = _check_required_fields_covered(fields, mapping, filled)
    assert ok is True


def test_required_check_excludes_file_type_fields():
    """The exact Whatnot regression: Resume is marked required but is uploaded
    separately by upload_resume() and never appears in `filled`. The check
    must NOT flag it as missing."""
    fields = [
        {"id": "_systemfield_name", "label": "Full Legal Name", "type": "text", "required": True},
        {"id": "_systemfield_email", "label": "Email", "type": "email", "required": True},
        {"id": "_systemfield_resume", "label": "Resume", "type": "file", "required": True},
    ]
    mapping = {
        "_systemfield_name": "Gennadiy",
        "_systemfield_email": "me@example.com",
        "_systemfield_resume": "UPLOAD_RESUME",  # sentinel, not actually filled
    }
    filled = ["_systemfield_name", "_systemfield_email"]  # resume NOT in filled

    ok, missing = _check_required_fields_covered(fields, mapping, filled)
    assert ok is True, f"File type should be excluded; got missing={missing}"
    assert missing == []


def test_required_check_file_excluded_even_when_explicitly_required():
    """Multiple file fields should also be excluded."""
    fields = [
        {"id": "resume", "label": "Resume", "type": "file", "required": True},
        {"id": "cover", "label": "Cover letter file", "type": "file", "required": True},
    ]
    ok, _ = _check_required_fields_covered(fields, {}, [])
    # No non-file required fields left → returns True (fall-through behavior)
    assert ok is True


# ---------------------------------------------------------------------------
# _build_prefill_hints: collects (type, label, value) tuples from Layer 0
# ---------------------------------------------------------------------------


def test_build_prefill_hints_collects_template_and_cache_matches():
    fields = [
        {"id": "f1", "label": "Email", "type": "email"},
        {"id": "f2", "label": "Gender", "type": "select"},
        {"id": "f3", "label": "Custom Question", "type": "text"},  # not in Layer 0
    ]
    template_map = {"f1": "me@example.com"}
    cache_map = {"f2": "Male"}

    hints = _build_prefill_hints(fields, template_map, cache_map)

    assert len(hints) == 2
    assert ("email", "Email", "me@example.com") in hints
    assert ("select", "Gender", "Male") in hints


def test_build_prefill_hints_skips_fields_not_in_field_list():
    fields = [{"id": "f1", "label": "Email", "type": "email"}]
    template_map = {"f_missing": "orphan value"}  # field_id not in fields
    hints = _build_prefill_hints(fields, template_map, {})
    assert hints == []


def test_build_prefill_hints_skips_empty_labels():
    fields = [{"id": "f1", "label": "", "type": "text"}]
    template_map = {"f1": "value"}
    hints = _build_prefill_hints(fields, template_map, {})
    assert hints == []


def test_build_prefill_hints_empty_when_no_layer_0_matches():
    fields = [{"id": "f1", "label": "Email", "type": "email"}]
    hints = _build_prefill_hints(fields, {}, {})
    assert hints == []
