# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Precomputed, per-language global document-frequency table for the TF-IDF
summarizer (`synthelion/nlp/summarizer.py`).

Today's TF-IDF (`_tfidf_scores`) computes IDF purely from the document being
summarized — a short or jargon-heavy input gets unstable scores because there
is no external frequency reference. This module ships that reference as a
brotli-compressed binary blob (`{iso3}.idf.br`, one line `N` = reference
corpus size, then `lemma\\tdocument_frequency` per line), loaded the exact
same way as the existing `worddata/*.br` function-word/lemma files — zero new
runtime dependencies, zero ML models, consistent with the rest of Synthelion.

A language with no shipped `{iso3}.idf.br` simply has no global data
(`has_data()` returns False); callers fall back to the local-only IDF
computation, so this is purely additive and never a required input.

Generated offline by `devtools/build_idf_corpus.py` — not part of runtime.
"""
from __future__ import annotations

import importlib.resources
import threading

import brotli


class GlobalIdfProvider:
    """Loads and caches per-language document-frequency tables. Thread-safe,
    can be shared across summarizer instances (data is read-only after load)."""

    _lock = threading.Lock()
    _cache: dict[str, tuple[dict[str, int], int] | None] = {}

    @classmethod
    def _load(cls, iso3: str) -> tuple[dict[str, int], int] | None:
        iso3 = iso3.lower()
        if iso3 in cls._cache:
            return cls._cache[iso3]
        with cls._lock:
            if iso3 in cls._cache:
                return cls._cache[iso3]
            result: tuple[dict[str, int], int] | None = None
            try:
                data = importlib.resources.files("synthelion.worddata").joinpath(f"{iso3}.idf.br").read_bytes()
                raw = brotli.decompress(data).decode("utf-8")
                lines = raw.splitlines()
                if lines:
                    corpus_size = int(lines[0])
                    doc_freq: dict[str, int] = {}
                    for line in lines[1:]:
                        if not line:
                            continue
                        parts = line.split("\t")
                        if len(parts) == 2:
                            doc_freq[parts[0]] = int(parts[1])
                    result = (doc_freq, corpus_size)
            except Exception:
                result = None
            cls._cache[iso3] = result
            return result

    def has_data(self, iso3: str) -> bool:
        return self._load(iso3) is not None

    def get_corpus_size(self, iso3: str) -> int:
        loaded = self._load(iso3)
        return loaded[1] if loaded else 0

    def get_document_frequency(self, lemma: str, iso3: str) -> int:
        loaded = self._load(iso3)
        if not loaded:
            return 0
        return loaded[0].get(lemma, 0)

    @classmethod
    def preload(cls, languages: "list[str] | None" = None, background: bool = True) -> "threading.Thread | None":
        """Warm the class-level cache for `languages` (default: every language with
        a shipped `.idf.br` table) ahead of the first real request.

        Decompressing and parsing the largest tables (millions of documents,
        hundreds of thousands of terms) takes several hundred ms the first time a
        language is used — negligible for a long-running process amortized over
        many requests, but a real, user-visible latency spike on the very first
        `compress`/`summarize` call in a language after a cold process start (e.g.
        a freshly spawned MCP server or proxy worker). Calling this once at
        startup avoids paying that cost on a real request.

        With `background=True` (default), loading happens in a daemon thread and
        this returns immediately with the `Thread` (join it if you need to know
        when warming finished); the cache is thread-safe either way. With
        `background=False`, loads synchronously and returns None.
        """
        if languages is None:
            try:
                languages = [
                    p.name.removesuffix(".idf.br")
                    for p in importlib.resources.files("synthelion.worddata").iterdir()
                    if p.name.endswith(".idf.br")
                ]
            except Exception:
                languages = []

        def _load_all() -> None:
            for iso3 in languages:
                cls._load(iso3)

        if not background:
            _load_all()
            return None

        thread = threading.Thread(target=_load_all, name="synthelion-idf-preload", daemon=True)
        thread.start()
        return thread
