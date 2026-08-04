# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Boundary-safe chunked/streaming PII masking tests for
`synthelion/privacy_stream.py`. The critical case is a PII match split
exactly across two `feed()` calls — it must still be masked once the buffer
is reassembled, not silently missed because a regex saw only half of it."""
from __future__ import annotations

import re

from synthelion.privacy_analyzer import PrivacyAnalyzer
from synthelion.privacy_session import PrivacySession
from synthelion.privacy_stream import PrivacyStreamMasker, mask_stream


class TestPrivacyStreamMasker:
    def test_feed_and_flush_basic(self):
        text = "My email is test@example.com and that's it."
        # Overlap larger than the whole text: nothing is safe to emit until
        # flush(), so this exercises the simplest possible case — the full
        # text reaches PrivacyAnalyzer.analyze() in one piece, same as a
        # direct (non-streaming) call.
        masker = PrivacyStreamMasker(overlap_chars=1000)
        out = masker.feed(text) + masker.flush()

        direct = PrivacyAnalyzer().analyze(text, auto_masking=True).masked_text
        assert out == direct
        assert "test@example.com" not in out

    def test_iban_split_across_chunk_boundary(self):
        iban = "IT60X0542811101000000123456"
        prefix = "Please wire funds to "
        suffix = " before Friday."
        full_text = prefix + iban + suffix

        # Cut the input stream exactly in the middle of the IBAN.
        split_point = len(prefix) + len(iban) // 2
        chunk1, chunk2 = full_text[:split_point], full_text[split_point:]

        # Generous overlap (>= iban + suffix) guarantees the emission cut
        # never lands inside the IBAN's character range for this input —
        # see privacy_stream.py's module docstring on overlap sizing.
        masker = PrivacyStreamMasker(overlap_chars=len(iban) + len(suffix))
        out = masker.feed(chunk1) + masker.feed(chunk2) + masker.flush()

        assert iban not in out
        assert "Please wire funds to" in out
        assert "before Friday." in out

    def test_shared_session_dedup_across_chunks(self):
        session = PrivacySession()
        masker = PrivacyStreamMasker(session=session, overlap_chars=1000)
        chunk1 = "Contact test@example.com now. "
        chunk2 = "Again, reach test@example.com later."
        out = masker.feed(chunk1) + masker.feed(chunk2) + masker.flush()

        placeholders = set(re.findall(r"\[PG_\d+\]", out))
        assert len(placeholders) == 1  # same value across chunks -> same placeholder
        assert session.count == 1

    def test_mask_stream_convenience_generator(self):
        chunks = ["My email is ", "test@example.com", " thanks."]
        out = "".join(mask_stream(chunks, overlap_chars=1000))
        assert "test@example.com" not in out
        assert "thanks." in out

    def test_whitespace_only_chunk_passes_through(self):
        masker = PrivacyStreamMasker(overlap_chars=2)
        # Enough whitespace to exceed overlap_chars and force an emission,
        # without ever calling analyze() on empty/whitespace-only text.
        out = masker.feed("     ") + masker.flush()
        assert out == "     "
