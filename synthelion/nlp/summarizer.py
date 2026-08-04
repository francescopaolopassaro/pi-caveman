# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

from synthelion.detector import LanguageDetector
from synthelion.nlp.sentence_detector import SentenceDetector
from synthelion.nlp.topic_segmenter import TopicSegmenter
from synthelion.word_provider import FunctionWordProvider

if TYPE_CHECKING:
    from synthelion.global_idf_provider import GlobalIdfProvider

_POSITION_FIRST = 1.5
_POSITION_LAST = 1.2
_POSITION_DEFAULT = 1.0


def _tokenize(text: str, fw: frozenset[str]) -> list[str]:
    import re
    words = re.findall(r"[^\W\d_]+", text.lower(), re.UNICODE)
    return [w for w in words if w not in fw and len(w) > 1]


def _tfidf_scores(
    sentences: list[str], fw: frozenset[str], iso3: str | None = None, global_idf: "GlobalIdfProvider | None" = None,
) -> list[float]:
    n = len(sentences)
    tokenized = [_tokenize(s, fw) for s in sentences]
    df: Counter[str] = Counter()
    for words in tokenized:
        for w in set(words):
            df[w] += 1

    use_global = global_idf is not None and iso3 is not None and global_idf.has_data(iso3)

    scores = []
    for i, words in enumerate(tokenized):
        if not words:
            scores.append(0.0)
            continue
        tf = Counter(words)
        score = 0.0
        for w in tf:
            local_idf = math.log((n + 1) / (df[w] + 1))
            if use_global:
                g_df = global_idf.get_document_frequency(w, iso3)
                g_n = global_idf.get_corpus_size(iso3)
                # Simple smoothed blend: local IDF alone is unstable on short/
                # jargon-heavy documents (few sentences -> few data points),
                # so it's averaged 50/50 with the precomputed global reference
                # when one is available for the language. Falls back to
                # local-only (today's exact behavior) otherwise.
                global_component = math.log((g_n + 1) / (g_df + 1))
                idf = 0.5 * local_idf + 0.5 * global_component
            else:
                idf = local_idf
            score += (tf[w] / len(words)) * idf
        # Position bias
        pos_factor = (
            _POSITION_FIRST if i == 0 else
            _POSITION_LAST if i == n - 1 else
            _POSITION_DEFAULT
        )
        scores.append(score * pos_factor)

    return scores


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def _mmr_select_indices(
    sentences: list[str],
    scores: list[float],
    fw: frozenset[str],
    k: int,
    lam: float = 0.6,
) -> list[int]:
    """Maximum Marginal Relevance selection with Jaccard similarity — returns indices
    into `sentences` (not the text itself), so callers can tell apart two identical
    sentences that happen to appear more than once."""
    if k >= len(sentences):
        return sorted(range(len(sentences)), key=lambda i: scores[i], reverse=True)

    tok_sets = [set(_tokenize(s, fw)) for s in sentences]
    selected_idx: list[int] = []
    candidates = list(range(len(sentences)))

    for _ in range(k):
        best_idx, best_score = -1, float("-inf")
        for ci in candidates:
            if not selected_idx:
                mmr = scores[ci]
            else:
                max_sim = max(_jaccard(tok_sets[ci], tok_sets[si]) for si in selected_idx)
                mmr = lam * scores[ci] - (1 - lam) * max_sim
            if mmr > best_score:
                best_score = mmr
                best_idx = ci
        if best_idx < 0:
            break
        selected_idx.append(best_idx)
        candidates.remove(best_idx)

    selected_idx.sort()  # original document order
    return selected_idx


def _mmr_select(
    sentences: list[str],
    scores: list[float],
    fw: frozenset[str],
    k: int,
    lam: float = 0.6,
) -> list[str]:
    """Maximum Marginal Relevance selection with Jaccard similarity."""
    return [sentences[i] for i in _mmr_select_indices(sentences, scores, fw, k, lam)]


def _allocate_segment_budget(segment_sentence_counts: list[int], total_budget: int) -> list[int]:
    """Largest-remainder apportionment (ported from Caveman C# 1.4.1): gives each topic
    segment a sentence share proportional to how much of the document it covers, then
    hands out the few leftover slots (from integer rounding) to the segments with the
    biggest fractional remainder — the standard way to round a set of proportions so
    they still sum to the target total."""
    total_sentences = sum(segment_sentence_counts)
    if total_sentences == 0:
        return [0] * len(segment_sentence_counts)

    raw = [count / total_sentences * total_budget for count in segment_sentence_counts]
    alloc = [int(r) for r in raw]  # floor
    remaining = total_budget - sum(alloc)

    by_remainder = sorted(range(len(raw)), key=lambda i: raw[i] - int(raw[i]), reverse=True)
    for i in by_remainder[:remaining]:
        alloc[i] += 1

    return alloc


class TfIdfSummarizer:
    """Extractive summarizer using TF-IDF + position bias + MMR diversity.

    Ported from C# CavemanSummarizer. Best for factual/report text.
    """

    def __init__(
        self, word_provider: FunctionWordProvider | None = None, global_idf: "GlobalIdfProvider | None" = None,
    ) -> None:
        self._provider = word_provider or FunctionWordProvider()
        self._detector = LanguageDetector(self._provider)
        self._splitter = SentenceDetector(self._provider)
        self._topic_segmenter = TopicSegmenter(self._provider)
        self._global_idf = global_idf

    def summarize(
        self,
        text: str,
        sentence_count: int | None = None,
        ratio: float | None = None,
        iso3: str | None = None,
    ) -> str:
        if not text or not text.strip():
            return ""

        lang = iso3 or self._detector.detect(text)
        fw = self._provider.get_function_words(lang)
        sentences = self._splitter.split_text(text, lang)

        if not sentences:
            return text

        k = sentence_count
        if k is None and ratio is not None:
            k = max(1, round(len(sentences) * ratio))
        if k is None:
            k = max(1, len(sentences) // 3)

        if len(sentences) <= k:
            return text

        scores = _tfidf_scores(sentences, fw, iso3=lang, global_idf=self._global_idf)
        selected = _mmr_select(sentences, scores, fw, k)
        return " ".join(selected)

    def summarize_topic_aware(
        self,
        text: str,
        sentence_count: int | None = None,
        ratio: float | None = None,
        iso3: str | None = None,
    ) -> str:
        """Topic-aware summarization (ported from Caveman C# 1.4.1's
        CondenseTextTopicAware).

        Segments the text with TopicSegmenter first, then allocates the sentence
        budget proportionally across topics (largest-remainder rounding) and
        scores/selects sentences independently within each topic, instead of scoring
        the whole document as one undifferentiated bag of sentences. This is a
        separate method from `summarize` — existing behaviour is unchanged — because
        on a single-topic document the two approaches converge, while on a genuinely
        multi-topic document plain TF-IDF scoring can let one statistically dense
        topic dominate the whole summary and starve the others entirely; this method
        guarantees every detected topic gets some representation.

        Falls back to `summarize()` when segmentation finds no real topic structure
        (a single segment).
        """
        if not text or not text.strip():
            return ""

        lang = iso3 or self._detector.detect(text)
        segments = self._topic_segmenter.segment(text, lang)
        if len(segments) <= 1:
            return self.summarize(text, sentence_count, ratio, lang)

        fw = self._provider.get_function_words(lang)
        sentences = self._splitter.split_text(text, lang)
        if not sentences:
            return text

        k = sentence_count
        if k is None and ratio is not None:
            k = max(1, round(len(sentences) * ratio))
        if k is None:
            k = max(1, len(sentences) // 3)
        if len(sentences) <= k:
            return text

        budget = _allocate_segment_budget([s.sentence_count for s in segments], k)

        selected_indexes: set[int] = set()
        for segment, seg_budget in zip(segments, budget):
            if seg_budget <= 0:
                continue
            seg_sentences = sentences[segment.start_sentence:segment.end_sentence]
            if not seg_sentences:
                continue
            scores = _tfidf_scores(seg_sentences, fw, iso3=lang, global_idf=self._global_idf)
            local_indexes = _mmr_select_indices(seg_sentences, scores, fw, min(seg_budget, len(seg_sentences)))
            for local_idx in local_indexes:
                selected_indexes.add(segment.start_sentence + local_idx)

        ordered = sorted(selected_indexes)
        return " ".join(sentences[i] for i in ordered)
