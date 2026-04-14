"""Integration test: real Playwright YAML -> parser -> extractor.

This is the test that would have caught Bug #1 (the ``page.accessibility``
API mismatch) — it exercises the FULL pipeline from a real ARIA snapshot
captured by ``probe_aria.py`` against the live Whatnot Ashby form.

Fixture: ``tests/fixtures/whatnot_ashby.yaml`` — captured 2026-04-13.
"""

from pathlib import Path

from src.aria_extractor import extract_fields_from_aria
from src.aria_helpers import reset_id_counter
from src.aria_yaml_parser import parse_aria_yaml

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "whatnot_ashby.yaml"


def setup_function(_fn):
    reset_id_counter()


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_fixture_file_exists():
    assert FIXTURE_PATH.exists(), f"Missing fixture at {FIXTURE_PATH}"


def test_parse_aria_yaml_returns_dict():
    yaml_text = _load_fixture()
    snapshot = parse_aria_yaml(yaml_text)
    assert snapshot is not None
    assert "children" in snapshot
    assert len(snapshot["children"]) > 0


def test_parse_returns_textbox_nodes_with_labels():
    snapshot = parse_aria_yaml(_load_fixture())
    flat: list[dict] = []

    def walk(n: dict) -> None:
        flat.append(n)
        for c in n.get("children") or []:
            walk(c)

    walk(snapshot)

    textboxes = [n for n in flat if n.get("role") == "textbox"]
    labels = [t.get("name", "") for t in textboxes]
    assert any("Email" in label for label in labels)
    assert any("Phone Number" in label for label in labels)
    assert any("Linkedin" in label or "LinkedIn" in label for label in labels)


def test_full_pipeline_extracts_all_critical_fields():
    """The single most important test: every field that was missing in the
    $0.21 and $0.90 runs MUST be present after parse + extract."""
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)

    assert len(fields) >= 7, f"Expected at least 7 fields, got {len(fields)}"

    labels = [f.get("label", "") for f in fields]
    types = [f.get("type", "") for f in fields]

    # Text fields the JS extractor caught fine
    assert any("Email" in label for label in labels), labels
    assert any("Phone Number" in label for label in labels), labels

    # The HARD fields — these are why direct_fill kept failing
    assert any("text" in t for t in types)


def test_extracts_button_groups_for_yes_no_questions():
    """The Whatnot form has TWO button-group questions: work auth + sponsorship.
    These broke direct_fill in both prior runs — make sure we catch them now."""
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)

    button_groups = [f for f in fields if f.get("type") == "button_group"]
    button_group_labels = [bg.get("label", "").lower() for bg in button_groups]

    assert len(button_groups) >= 2, (
        f"Expected at least 2 button groups (work auth + sponsorship), "
        f"got {len(button_groups)}: {button_group_labels}"
    )
    assert any("authorized" in label for label in button_group_labels), button_group_labels
    assert any("sponsorship" in label for label in button_group_labels), button_group_labels


def test_extracts_checkbox_group_for_how_did_you_hear():
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)

    checkbox_groups = [f for f in fields if f.get("type") == "checkbox_group"]
    assert len(checkbox_groups) >= 1, f"Expected checkbox group, got: {checkbox_groups}"

    cg = checkbox_groups[0]
    assert "hear" in cg.get("label", "").lower()
    options = cg.get("options") or []
    assert len(options) >= 5, f"Expected several options, got: {options}"
    option_texts = [o.get("text", "") for o in options]
    assert "LinkedIn" in option_texts


def test_extracts_radio_group_for_hub_location():
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)

    radio_groups = [f for f in fields if f.get("type") == "radio_group"]
    assert len(radio_groups) >= 1, f"Expected hub location radio group, got: {radio_groups}"

    rg = radio_groups[0]
    options = rg.get("options") or []
    option_texts = [o.get("text", "") for o in options]
    assert any("New York" in opt for opt in option_texts)
    assert any("San Francisco" in opt for opt in option_texts)


def test_required_markers_stripped_from_labels():
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)
    for f in fields:
        label = f.get("label", "")
        assert not label.endswith("*"), f"Label still has trailing *: {label!r}"


def test_required_flag_set_for_starred_fields():
    snapshot = parse_aria_yaml(_load_fixture())
    fields = extract_fields_from_aria(snapshot)
    required_count = sum(1 for f in fields if f.get("required"))
    assert required_count >= 5, f"Expected several required fields, got {required_count}"
