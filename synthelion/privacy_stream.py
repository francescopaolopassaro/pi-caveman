# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Boundary-safe chunked/streaming PII masking, built on top of the existing
`PrivacyAnalyzer`/`PrivacySession` primitives (no scoring logic is
reimplemented here).

The same `PrivacyStreamMasker` backs both use cases: (a) reading a large file
in pieces without loading it whole into memory, and (b) masking live
incrementally-arriving text (e.g. an LLM's streamed output) as it comes in.

Overlap sizing: `privacy_rules.yaml`'s bounded categories (IBAN, credit cards,
tax IDs, etc.) all match well under 100 characters; the default
`overlap_chars=512` covers them generously. Two categories use unbounded
patterns (JWT, generic secret/password `\\S{8,}`) — a match of one of those
that happens to be split across a chunk boundary by more than `overlap_chars`
characters is a known residual risk, not something a finite overlap window
can fully eliminate. Choose a larger `overlap_chars` for deployments where
this matters.
"""
from __future__ import annotations

import threading
from typing import Iterable, Iterator

from synthelion.privacy_analyzer import PrivacyAnalyzer
from synthelion.privacy_session import PrivacySession

_DEFAULT_OVERLAP_CHARS = 512


class PrivacyStreamMasker:
    """Wraps a `PrivacyAnalyzer` + a shared `PrivacySession` for boundary-safe
    chunked/streaming masking. Call `feed()` per chunk, then `flush()` once
    when the input is exhausted."""

    def __init__(
        self,
        analyzer: PrivacyAnalyzer | None = None,
        session: PrivacySession | None = None,
        language: str = "en",
        overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    ) -> None:
        self._analyzer = analyzer or PrivacyAnalyzer()
        self._session = session
        self._language = language
        self._overlap_chars = max(0, overlap_chars)
        self._buffer = ""
        self._lock = threading.Lock()

    def feed(self, chunk: str) -> str:
        """Appends *chunk* to the internal tail buffer and returns the
        masked-safe prefix (everything except the last `overlap_chars`
        characters, held back in case a PII pattern spans the boundary with
        the next chunk). Returns "" when there isn't yet enough buffered text
        to safely emit anything."""
        if not chunk:
            return ""
        with self._lock:
            self._buffer += chunk
            if len(self._buffer) <= self._overlap_chars:
                return ""
            safe_part = self._buffer[: len(self._buffer) - self._overlap_chars]
            self._buffer = self._buffer[len(self._buffer) - self._overlap_chars:]
        return self._mask(safe_part)

    def flush(self) -> str:
        """Masks and returns whatever remains in the tail buffer. Call once,
        after the last `feed()`, when the input stream is exhausted."""
        with self._lock:
            remainder, self._buffer = self._buffer, ""
        return self._mask(remainder)

    def _mask(self, text: str) -> str:
        if not text or not text.strip():
            # Whitespace-only segment: nothing to analyze, pass through as-is
            # (analyze() would otherwise collapse it to "" via its empty-text
            # early return, silently dropping real whitespace/newlines).
            return text
        result = self._analyzer.analyze(text, language=self._language, session=self._session, auto_masking=True)
        return result.masked_text or text


def mask_stream(
    chunks: Iterable[str],
    session: PrivacySession | None = None,
    language: str = "en",
    overlap_chars: int = _DEFAULT_OVERLAP_CHARS,
    analyzer: PrivacyAnalyzer | None = None,
) -> Iterator[str]:
    """Convenience generator wrapping `PrivacyStreamMasker` — masks an
    iterable of text chunks (from a chunked file read or a live text stream),
    yielding masked pieces as they become safe to emit, plus the final
    remainder once *chunks* is exhausted."""
    masker = PrivacyStreamMasker(analyzer=analyzer, session=session, language=language, overlap_chars=overlap_chars)
    for chunk in chunks:
        out = masker.feed(chunk)
        if out:
            yield out
    tail = masker.flush()
    if tail:
        yield tail
