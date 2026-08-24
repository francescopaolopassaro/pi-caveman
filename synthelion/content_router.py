# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

import hashlib
import time
from threading import Lock

from synthelion.compressors.code_compressor import CodeCompressor, compress_python_ast
from synthelion.compressors.diff_compressor import DiffCompressor
from synthelion.compressors.html_extractor import HtmlExtractor
from synthelion.compressors.json_crusher import JsonCrusher
from synthelion.compressors.log_compressor import LogCompressor
from synthelion.compressors.sql_compressor import SqlCompressor
from synthelion.compressors.tabular import TabularCompressor
from synthelion.content_detector import ContentDetector
from synthelion.core import CompressionService
from synthelion.models import (
    CompressionLevel,
    CompressionProfile,
    ContentType,
    RoutedCompressionResult,
)
from synthelion.success_collapse import collapse as _collapse_success, is_known_low_signal
from synthelion.terminal_noise import strip_ansi_noise

_CACHE_TTL = 1800   # 30 minutes
_CACHE_MAX = 512    # max entries — evict oldest 25% when full

# Content whose meaning is carried by syntax that the prose compressor removes:
# operators, delimiters, clause keywords. For these, the NLP compressor produces
# output that reads as compressed and is in fact unusable — `price = base -
# (base * discount)` becomes `price base base discount`, where no reader can
# recover whether the discount was subtracted or added. If the dedicated
# strategy declines, the correct outcome is the original content.
#
# Deliberately narrow. JSON is *not* included: `test_content_router_schema_
# object_falls_back_to_nlp` codifies the NLP fallback for schema objects as
# intended behaviour, and JSON aimed at a language model does not need to remain
# machine-parseable. Whether that should also change is a product question about
# who consumes the output, not one to settle here.
_NEVER_NLP_TYPES = frozenset({
    ContentType.CODE,
    ContentType.SQL,
})


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def _escalate_level(level: CompressionLevel) -> CompressionLevel:
    """One step more aggressive, capped at AGGRESSIVE. STATISTICAL/SYNTACTIC are
    alternate algorithms rather than "more aggressive than AGGRESSIVE" on a single
    scale, so a level already at or past AGGRESSIVE is left untouched — an explicit
    choice of a different algorithm is never silently overridden."""
    if level.value < CompressionLevel.AGGRESSIVE.value:
        return CompressionLevel(level.value + 1)
    return level


# Adaptive-scaling thresholds (approx. token count of the raw input): past these,
# ContentRouter compresses more aggressively than the level/max_items it was
# configured with — mirrors kompact's tiered scaling, adapted to Synthelion's
# existing knobs (CompressionLevel escalation, JsonCrusher row cap) instead of a
# separate parameter set.
_SCALE_LARGE = 5000
_SCALE_HUGE = 25000


def _guard_against_expansion(result: RoutedCompressionResult) -> None:
    """Universal safety net, mutating *result* in place: if whatever compressor ran
    produced output that isn't actually smaller (a rewriting compressor like
    JsonCrusher's LossyRowDrop adds a CCR-hash comment that can outweigh the savings
    on a small input, for instance), fall back to the original content untouched
    rather than silently handing back something longer than what came in.

    Centralized here rather than in each compressor: a new compressor added later
    gets this guarantee for free instead of needing to remember its own check.
    """
    if result.strategy_used in ("Passthrough", "Error"):
        return
    if result.tokens_after >= result.tokens_before:
        result.compressed = result.original
        result.strategy_used = f"{result.strategy_used}→Passthrough(no-gain)"
        result.tokens_after = result.tokens_before
        result.ccr_hash = None


class ContentRouter:
    """Routes content to the best compressor based on detected type.

    Ported from C# CavemanContentRouter. Two-tier in-process cache (hash → result,
    TTL 30 min). Supports CompressionProfile presets.
    """

    def __init__(
        self,
        prose_level: CompressionLevel = CompressionLevel.SEMANTIC,
        max_json_items: int = 15,
        compression_service: CompressionService | None = None,
        python_ast: bool = False,
    ) -> None:
        self._prose_level = prose_level
        # Rebuild Python from its AST instead of only stripping comments. Off by
        # default because it also drops docstrings and normalises formatting.
        self.python_ast = python_ast
        self._detector = ContentDetector()
        self._nlp = compression_service or CompressionService()
        self._json = JsonCrusher(max_json_items)
        self._html = HtmlExtractor()
        self._diff = DiffCompressor()
        self._log = LogCompressor()
        self._code = CodeCompressor()
        self._sql = SqlCompressor()
        self._table = TabularCompressor()
        self._cache: dict[str, tuple[RoutedCompressionResult, float]] = {}
        self._cache_lock = Lock()

    @classmethod
    def from_profile(cls, profile: CompressionProfile) -> "ContentRouter":
        params = {
            CompressionProfile.LIGHT:      (CompressionLevel.LIGHT, 25),
            CompressionProfile.BALANCED:   (CompressionLevel.SEMANTIC, 15),
            CompressionProfile.AGENT:      (CompressionLevel.SEMANTIC, 10),
            CompressionProfile.AGGRESSIVE: (CompressionLevel.AGGRESSIVE, 8),
        }
        level, max_items = params.get(profile, (CompressionLevel.SEMANTIC, 15))
        return cls(prose_level=level, max_json_items=max_items)

    def route(
        self,
        content: str,
        query: str | None = None,
        command: str | None = None,
        exit_code: int | None = None,
    ) -> RoutedCompressionResult:
        if not content or not content.strip():
            return RoutedCompressionResult(
                compressed=content, original=content,
                detected_type=ContentType.PLAIN_TEXT,
                strategy_used="Passthrough",
            )

        cache_key = hashlib.md5(
            f"{content}\x00{command or ''}\x00{exit_code}".encode()
        ).hexdigest()
        with self._cache_lock:
            entry = self._cache.get(cache_key)
            if entry and time.time() - entry[1] < _CACHE_TTL:
                return entry[0]

        try:
            result = self._route_inner(content, query, command, exit_code)
        except Exception as exc:
            result = RoutedCompressionResult(
                compressed=content, original=content,
                detected_type=ContentType.PLAIN_TEXT,
                strategy_used="Error",
                error_message=str(exc),
            )

        _guard_against_expansion(result)

        with self._cache_lock:
            self._cache[cache_key] = (result, time.time())
            if len(self._cache) > _CACHE_MAX:
                # Evict oldest 25% of entries
                sorted_keys = sorted(self._cache, key=lambda k: self._cache[k][1])
                for k in sorted_keys[: _CACHE_MAX // 4]:
                    del self._cache[k]

        return result

    def _route_inner(
        self,
        content: str,
        query: str | None,
        command: str | None = None,
        exit_code: int | None = None,
    ) -> RoutedCompressionResult:
        tb = _approx_tokens(content)

        if command and exit_code == 0 and is_known_low_signal(command):
            summary = _collapse_success(content, command)
            if summary is not None:
                return RoutedCompressionResult(
                    compressed=summary, original=content,
                    detected_type=ContentType.PLAIN_TEXT,
                    strategy_used="SuccessCollapse",
                    tokens_before=tb, tokens_after=_approx_tokens(summary),
                )

        content = strip_ansi_noise(content)
        detection = self._detector.detect(content)
        ct = detection.type

        # Adaptive scaling: the bigger the input, the more aggressively it's
        # compressed by default — computed once, applied uniformly below. Both are
        # purely local values (never mutate self.*), so concurrent calls with
        # different content sizes never interfere with each other.
        if tb >= _SCALE_HUGE:
            effective_level = _escalate_level(_escalate_level(self._prose_level))
            effective_max_items = max(3, int(self._json._max_items * 0.35))
        elif tb >= _SCALE_LARGE:
            effective_level = _escalate_level(self._prose_level)
            effective_max_items = max(3, int(self._json._max_items * 0.6))
        else:
            effective_level = self._prose_level
            effective_max_items = self._json._max_items

        if ct == ContentType.JSON_ARRAY:
            r = self._json.crush(content, query, max_items=effective_max_items)
            compressed = r["compressed"]
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct,
                strategy_used=f"JsonCrush:{r['strategy']}",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
                ccr_hash=r.get("ccr_hash"),
            )

        if ct == ContentType.JSON_OBJECT:
            r = self._json.crush(content, query, max_items=effective_max_items)
            if r["was_crushed"]:
                compressed = r["compressed"]
                return RoutedCompressionResult(
                    compressed=compressed, original=content,
                    detected_type=ct,
                    strategy_used=f"JsonCrush:{r['strategy']}",
                    tokens_before=tb, tokens_after=_approx_tokens(compressed),
                    ccr_hash=r.get("ccr_hash"),
                )
            # Nothing was crushed. Why matters: a JSON-Schema-shaped object is
            # metadata that reads much like prose, and falling through to the
            # prose compressor is the intended behaviour for it. A data object
            # that merely failed to shrink is different — its values are the
            # payload, and the prose compressor would strip the punctuation
            # holding them together, turning "127.0.0.1" into "127 0 0 1" and
            # lemmatising the key "roles" into "role". For that case the
            # original is the correct output.
            if r.get("decline_reason") == "no-gain":
                return RoutedCompressionResult(
                    compressed=content, original=content,
                    detected_type=ct, strategy_used="JsonCrush→Passthrough(no-gain)",
                    tokens_before=tb, tokens_after=tb,
                )

        if ct == ContentType.GIT_DIFF:
            compressed, _ = self._diff.compress(content)
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="DiffCompression",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.LOG_OR_STACKTRACE:
            compressed, _ = self._log.compress(content)
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="LogCompression",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.HTML:
            extracted = self._html.extract(content)
            nlp_result = self._nlp.compress(extracted, effective_level)
            compressed = nlp_result.compressed_text
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="HtmlExtract+NlpCompression",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.CODE:
            compressed, lang, _, _ = self._code.compress(content)
            strategy = f"CodeCompression:{lang}"
            # For Python, an AST round-trip goes considerably further than the
            # comment-stripping pass and is valid by construction. Off by
            # default: it also drops docstrings and normalises formatting, which
            # matters when the code is going to be read back and edited rather
            # than only understood. See `python_ast` on the profile.
            if self.python_ast and lang == "python":
                rebuilt, changed = compress_python_ast(compressed)
                if changed:
                    compressed, strategy = rebuilt, f"{strategy}+AstRebuild"
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used=strategy,
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.SQL:
            compressed, _ = self._sql.compress(content)
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="SqlCompression",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.TABULAR:
            compressed, was = self._table.compress(content)
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="TabularCompression" if was else "Passthrough",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        if ct == ContentType.SEARCH_RESULTS:
            nlp_result = self._nlp.compress(content, effective_level)
            compressed = nlp_result.compressed_text
            return RoutedCompressionResult(
                compressed=compressed, original=content,
                detected_type=ct, strategy_used="NlpCompression",
                tokens_before=tb, tokens_after=_approx_tokens(compressed),
            )

        # Everything that reaches here is PlainText, or a type whose own
        # strategy declined to collapse it. PlainText goes to the NLP
        # compressor; code and SQL must not — see _NEVER_NLP_TYPES.
        if ct in _NEVER_NLP_TYPES:
            return RoutedCompressionResult(
                compressed=content, original=content,
                detected_type=ct, strategy_used="Passthrough(structured)",
                tokens_before=tb, tokens_after=tb,
            )

        nlp_result = self._nlp.compress(content, effective_level)
        compressed = nlp_result.compressed_text
        return RoutedCompressionResult(
            compressed=compressed, original=content,
            detected_type=ct, strategy_used="NlpCompression",
            tokens_before=tb, tokens_after=_approx_tokens(compressed),
        )
