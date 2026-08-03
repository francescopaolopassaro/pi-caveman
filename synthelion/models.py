# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CompressionLevel(Enum):
    NONE = 0
    LIGHT = 1
    SEMANTIC = 2
    AGGRESSIVE = 3
    # Ported from Caveman C# 1.4.1.
    STATISTICAL = 4  # TF-IDF word scoring instead of curated dictionaries
    SYNTACTIC = 5    # rule-based grammatical-glue pruning, POS-gated hedge-clause elision


class ContentType(Enum):
    PLAIN_TEXT = "PlainText"
    JSON_ARRAY = "JsonArray"
    JSON_OBJECT = "JsonObject"
    CODE = "Code"
    LOG_OR_STACKTRACE = "LogOrStacktrace"
    GIT_DIFF = "GitDiff"
    HTML = "Html"
    SEARCH_RESULTS = "SearchResults"
    TABULAR = "Tabular"
    SQL = "Sql"


class CompressionProfile(Enum):
    LIGHT = "Light"
    BALANCED = "Balanced"
    AGENT = "Agent"
    AGGRESSIVE = "Aggressive"


class VerbosityLevel(Enum):
    OFF = 0
    SKIP_CEREMONY = 1
    NO_RESTATEMENT = 2
    CONCLUSIONS_ONLY = 3
    MINIMUM_TOKENS = 4


@dataclass
class CompressionResult:
    compressed_text: str = ""
    original_tokens: int = 0
    compressed_tokens: int = 0
    error_message: str | None = None

    @property
    def saved_tokens(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    @property
    def efficiency_pct(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return self.saved_tokens / self.original_tokens * 100

    @property
    def estimated_energy_saved_mwh(self) -> float:
        return self.saved_tokens * 0.005

    @property
    def estimated_co2_saved_mg(self) -> float:
        return self.estimated_energy_saved_mwh * 0.4

    @property
    def has_error(self) -> bool:
        return bool(self.error_message)


@dataclass
class ContentDetectionResult:
    type: ContentType = ContentType.PLAIN_TEXT
    confidence: float = 1.0


@dataclass
class RoutedCompressionResult:
    compressed: str = ""
    original: str = ""
    detected_type: ContentType = ContentType.PLAIN_TEXT
    strategy_used: str = ""
    tokens_before: int = 0
    tokens_after: int = 0
    ccr_hash: str | None = None
    error_message: str | None = None

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def savings_pct(self) -> float:
        if self.tokens_before == 0:
            return 0.0
        return self.tokens_saved / self.tokens_before * 100

    @property
    def has_error(self) -> bool:
        return bool(self.error_message)


@dataclass
class WordDataFile:
    iso3: str = ""
    iso1: str = ""
    name: str = ""
    function_words: list[str] = field(default_factory=list)
    lemmas: dict[str, str] = field(default_factory=dict)
    verbs: dict[str, list[str]] = field(default_factory=dict)
    proper_nouns: list[str] = field(default_factory=list)
