"""Layer 0 cache: persistent learning of question -> answer mappings.

Each successful direct_fill teaches the cache the answers the LLM produced
for previously-unknown questions. The next time the same question shows up
on a different application, ``Layer0Cache.lookup`` returns the cached value
for free, skipping the LLM mapping call entirely.

What we cache (safe to reuse across jobs):
  - select / radio_group / checkbox_group / button_group / combobox values
  - These are dropdown-style answers tied to a fixed option set.

What we DO NOT cache (job-specific):
  - text / textarea fields — answers vary per job (cover letters, custom
    questions, "why this company")
  - file uploads

Storage: ``output/cache/layer0_cache.json``. Gitignored.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("job-agent")

DEFAULT_CACHE_PATH = Path("output/cache/layer0_cache.json")
CACHE_VERSION = 1

# Field types whose values are safe to share across jobs.
CACHEABLE_TYPES = frozenset({"select", "radio_group", "checkbox_group", "button_group", "combobox"})


def _normalize(label: str) -> str:
    """Lower-case, strip whitespace + required markers + trailing colon."""
    s = (label or "").strip().lower()
    for marker in ("*", "(required)", "[required]", "(optional)", "[optional]"):
        if s.endswith(marker):
            s = s[: -len(marker)].strip()
    return s.rstrip(":").strip()


def _make_key(label: str, field_type: str) -> str:
    """Cache key combines normalized label + field type."""
    return f"{_normalize(label)}|{field_type}"


class Layer0Cache:
    """Persistent label -> answer cache for dropdown-style fields."""

    def __init__(self, path: Path = DEFAULT_CACHE_PATH) -> None:
        self.path = path
        self.entries: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"  Layer0Cache: failed to load {self.path}: {e}")
            return {}
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return {}
        entries = data.get("entries", {})
        return entries if isinstance(entries, dict) else {}

    def lookup(self, label: str, field_type: str) -> str | None:
        """Return the cached answer for this question, or None if not known."""
        if not label or field_type not in CACHEABLE_TYPES:
            return None
        entry = self.entries.get(_make_key(label, field_type))
        if not entry:
            return None
        # Touch the entry so we know it's still in use.
        entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
        entry["hit_count"] = int(entry.get("hit_count", 0)) + 1
        return entry.get("value")

    def remember(self, label: str, field_type: str, value: str) -> bool:
        """Store a new (label, type) -> value mapping. Returns True if stored.

        Skips text/textarea (job-specific) and empty values. If the question
        is already in the cache with the same value, just updates timestamps.
        """
        if not label or not value or field_type not in CACHEABLE_TYPES:
            return False
        if len(value) > 200:
            # Long values smell like cover-letter blurbs slipped into a
            # weirdly-typed field. Don't poison the cache.
            return False

        key = _make_key(label, field_type)
        now = datetime.now().isoformat(timespec="seconds")
        existing = self.entries.get(key)

        if existing and existing.get("value") == value:
            existing["last_seen"] = now
            return True

        self.entries[key] = {
            "value": value,
            "field_type": field_type,
            "label_sample": label[:120],
            "first_seen": now,
            "last_seen": now,
            "hit_count": 0,
        }
        return True

    def save(self) -> None:
        """Persist the cache to disk. Creates parent directories if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "entries": self.entries,
        }
        try:
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning(f"  Layer0Cache: failed to save {self.path}: {e}")

    def clear(self) -> None:
        """Wipe all entries (in-memory + on disk)."""
        self.entries = {}
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError as e:
                logger.warning(f"  Layer0Cache: failed to delete {self.path}: {e}")

    def __len__(self) -> int:
        return len(self.entries)
