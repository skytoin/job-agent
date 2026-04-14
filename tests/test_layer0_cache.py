"""Tests for src/layer0_cache.py — persistent answer learning."""

import json
from pathlib import Path

from src.layer0_cache import CACHEABLE_TYPES, Layer0Cache, _normalize


def _temp_cache(tmp_path: Path) -> Layer0Cache:
    return Layer0Cache(path=tmp_path / "cache.json")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_strips_required_marker_and_case():
    assert _normalize("Email *") == "email"
    assert _normalize("Email  ") == "email"
    assert _normalize("Phone Number (required)") == "phone number"
    assert _normalize("ADDRESS:") == "address"


def test_normalize_handles_empty_input():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ---------------------------------------------------------------------------
# Lookup behavior
# ---------------------------------------------------------------------------


def test_lookup_misses_on_empty_cache(tmp_path):
    c = _temp_cache(tmp_path)
    assert c.lookup("Gender", "select") is None


def test_lookup_returns_remembered_value(tmp_path):
    c = _temp_cache(tmp_path)
    c.remember("How did you hear about us?", "checkbox_group", "LinkedIn")
    assert c.lookup("How did you hear about us?", "checkbox_group") == "LinkedIn"


def test_lookup_normalizes_label(tmp_path):
    """Cache hit even when label asterisks/case differ."""
    c = _temp_cache(tmp_path)
    c.remember("How did you hear?", "select", "LinkedIn")
    assert c.lookup("HOW DID YOU HEAR?  ", "select") == "LinkedIn"
    assert c.lookup("How did you hear? *", "select") == "LinkedIn"


def test_lookup_increments_hit_count(tmp_path):
    c = _temp_cache(tmp_path)
    c.remember("Gender", "select", "Male")
    c.lookup("Gender", "select")
    c.lookup("Gender", "select")
    c.lookup("Gender", "select")
    entry = c.entries["gender|select"]
    assert entry["hit_count"] == 3


# ---------------------------------------------------------------------------
# Type filtering — text/textarea must NOT be cached
# ---------------------------------------------------------------------------


def test_remember_skips_text_field(tmp_path):
    """Text fields contain job-specific answers (cover letters, custom Q&A)."""
    c = _temp_cache(tmp_path)
    stored = c.remember("Why this company?", "text", "Because I like you")
    assert stored is False
    assert len(c) == 0


def test_remember_skips_textarea_field(tmp_path):
    c = _temp_cache(tmp_path)
    stored = c.remember("Cover letter", "textarea", "Long blurb here")
    assert stored is False
    assert len(c) == 0


def test_remember_accepts_all_dropdown_types(tmp_path):
    c = _temp_cache(tmp_path)
    for ftype in CACHEABLE_TYPES:
        assert c.remember(f"Question {ftype}", ftype, "answer") is True
    assert len(c) == len(CACHEABLE_TYPES)


def test_lookup_skips_text_type_even_if_present_in_dict(tmp_path):
    c = _temp_cache(tmp_path)
    # Tamper directly with internal state to put a text entry in
    c.entries["weird|text"] = {"value": "x", "field_type": "text"}
    assert c.lookup("weird", "text") is None


# ---------------------------------------------------------------------------
# Long-value filter (poisoning guard)
# ---------------------------------------------------------------------------


def test_remember_rejects_long_value(tmp_path):
    c = _temp_cache(tmp_path)
    long_text = "x" * 250
    assert c.remember("Some dropdown", "select", long_text) is False
    assert len(c) == 0


def test_remember_accepts_short_value(tmp_path):
    c = _temp_cache(tmp_path)
    assert c.remember("Some dropdown", "select", "Yes") is True


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_and_reload_roundtrip(tmp_path):
    path = tmp_path / "cache.json"
    c1 = Layer0Cache(path=path)
    c1.remember("Years of experience", "select", "5-7")
    c1.remember("Gender", "select", "Male")
    c1.save()

    assert path.exists()

    c2 = Layer0Cache(path=path)
    assert len(c2) == 2
    assert c2.lookup("Years of experience", "select") == "5-7"
    assert c2.lookup("Gender", "select") == "Male"


def test_save_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "subdir" / "cache.json"
    c = Layer0Cache(path=nested)
    c.remember("Q", "select", "A")
    c.save()
    assert nested.exists()


def test_load_handles_corrupt_json_gracefully(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not valid json", encoding="utf-8")
    c = Layer0Cache(path=path)
    assert len(c) == 0


def test_load_handles_wrong_version(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"version": 999, "entries": {"x|select": {"value": "y"}}}))
    c = Layer0Cache(path=path)
    assert len(c) == 0


def test_clear_wipes_memory_and_disk(tmp_path):
    path = tmp_path / "cache.json"
    c = Layer0Cache(path=path)
    c.remember("A", "select", "1")
    c.save()
    assert path.exists()

    c.clear()
    assert len(c) == 0
    assert not path.exists()


# ---------------------------------------------------------------------------
# Realistic scenario — across two "applications"
# ---------------------------------------------------------------------------


def test_second_application_reuses_first_application_learnings(tmp_path):
    """Simulate the value: app #1 teaches the cache, app #2 hits it for free."""
    path = tmp_path / "cache.json"

    # Application #1 — LLM picks LinkedIn for "How did you hear", we save it
    c1 = Layer0Cache(path=path)
    c1.remember("How did you hear about us?", "checkbox_group", "LinkedIn")
    c1.remember("Gender", "select", "Male")
    c1.remember("Are you authorized?", "button_group", "Yes")
    c1.save()

    # Application #2 — same questions, no LLM call needed
    c2 = Layer0Cache(path=path)
    assert c2.lookup("How did you hear about us? *", "checkbox_group") == "LinkedIn"
    assert c2.lookup("Gender ", "select") == "Male"
    assert c2.lookup("Are you authorized?", "button_group") == "Yes"
