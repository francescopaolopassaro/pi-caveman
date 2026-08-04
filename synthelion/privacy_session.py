# Synthelion — Python port of Caveman.PrivacyGuard (https://github.com/francescopaolopassaro/Caveman.PrivacyGuard)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Thread-safe session mapping placeholders (`[PG_n]`) to the original sensitive
values they replaced — use with `PrivacyAnalyzer` to mask PII before sending text
to an AI model, then restore the original values client-side once the model's
response comes back (the model itself never sees the real data)."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

_PLACEHOLDER_RE = re.compile(r"\[PG_\d+\]")


@dataclass
class PlaceholderEntry:
    original_value: str
    category: str
    placeholder: str


@dataclass
class RestoreResult:
    text: str
    restored_count: int


class PrivacySession:
    """Not shared across threads by identity — safe to call concurrently on the
    same instance, matching the C# original's thread-safety guarantee."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._map: dict[str, PlaceholderEntry] = {}
        self._value_index: dict[str, str] = {}
        self._counter = 0

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._map)

    def add_entry(self, category: str, original_value: str) -> str:
        """Adds or retrieves a placeholder for *original_value*. Reuses the same
        placeholder for duplicate values within the session."""
        with self._lock:
            existing = self._value_index.get(original_value)
            if existing is not None:
                return existing
            self._counter += 1
            placeholder = f"[PG_{self._counter}]"
            self._map[placeholder] = PlaceholderEntry(original_value, category, placeholder)
            self._value_index[original_value] = placeholder
            return placeholder

    # Public alias matching the C# API name.
    def add_or_get(self, category: str, original_value: str) -> str:
        return self.add_entry(category, original_value)

    def restore(self, text: str) -> str:
        if not text or not self._map:
            return text
        with self._lock:
            snapshot = dict(self._map)
        return _PLACEHOLDER_RE.sub(lambda m: snapshot[m.group()].original_value if m.group() in snapshot else m.group(), text)

    def restore_detailed(self, text: str) -> RestoreResult:
        if not text or not self._map:
            return RestoreResult(text or "", 0)
        with self._lock:
            snapshot = dict(self._map)
        count = 0

        def _sub(m: re.Match) -> str:
            nonlocal count
            entry = snapshot.get(m.group())
            if entry is None:
                return m.group()
            count += 1
            return entry.original_value

        result = _PLACEHOLDER_RE.sub(_sub, text)
        return RestoreResult(result, count)

    def get_entry(self, placeholder: str) -> PlaceholderEntry | None:
        with self._lock:
            return self._map.get(placeholder)

    def get_all(self) -> dict[str, PlaceholderEntry]:
        """Returns every placeholder mapped to its `PlaceholderEntry`, which
        includes `original_value` in cleartext. This is sensitive — callers
        must not log, persist, or return this over a network response; it
        defeats the entire purpose of masking. Use `summary()` instead
        wherever only placeholder/category bookkeeping is needed."""
        with self._lock:
            return dict(self._map)

    def summary(self) -> list[dict[str, str]]:
        """Redacted view of the session: placeholder + category only, never
        `original_value`. Safe to log, display, or return from an API/CLI
        response — unlike `get_all()`/`to_json()`, which carry the real PII."""
        with self._lock:
            return [{"placeholder": k, "category": e.category} for k, e in self._map.items()]

    def merge_from(self, other: "PrivacySession") -> None:
        for entry in other.get_all().values():
            with self._lock:
                already = entry.original_value in self._value_index
            if not already:
                self.add_entry(entry.category, entry.original_value)

    def clear(self) -> None:
        with self._lock:
            self._map.clear()
            self._value_index.clear()
            self._counter = 0

    def to_json(self) -> str:
        """Full-fidelity export INCLUDING cleartext `original_value` for every
        placeholder — meant only for trusted cross-process session handoff
        (e.g. persisting a session so a later process can `restore()` it).
        Never expose this over an HTTP response, log it, or write it anywhere
        an untrusted party could read it; use `summary()` for a safe, redacted
        view instead."""
        with self._lock:
            entries = [
                {"placeholder": k, "original_value": e.original_value, "category": e.category}
                for k, e in self._map.items()
            ]
            counter = self._counter
        return json.dumps({"counter": counter, "entries": entries}, ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "PrivacySession":
        parsed = json.loads(data)
        session = cls()
        session._counter = parsed["counter"]
        for e in parsed["entries"]:
            entry = PlaceholderEntry(e["original_value"], e["category"], e["placeholder"])
            session._map[e["placeholder"]] = entry
            session._value_index[e["original_value"]] = e["placeholder"]
        return session

    def import_from_json(self, data: str) -> None:
        self.merge_from(PrivacySession.from_json(data))
