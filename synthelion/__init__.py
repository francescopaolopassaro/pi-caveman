# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Synthelion: token compressor for LLMs. 50+ languages, zero ML models.

Python port of Caveman (C#) by Passaro Francesco Paolo (Digitalsolutions.it).
Original: https://github.com/francescopaolopassaro/caveman
"""
from synthelion.models import (
    CompressionLevel,
    CompressionProfile,
    CompressionResult,
    ContentType,
    RoutedCompressionResult,
    VerbosityLevel,
)
from synthelion.word_provider import FunctionWordProvider
from synthelion.detector import LanguageDetector
from synthelion.core import CompressionFilter, CompressionService
from synthelion.content_detector import ContentDetector
from synthelion.content_router import ContentRouter
from synthelion.cache_aligner import CacheAligner, VolatileFinding
from synthelion.safety_guard import SafetyGuard, SafetyLevel, SafetyVerdict
from synthelion.sensitive_guard import find_sensitive
from synthelion.terminal_noise import strip_ansi_noise
from synthelion.success_collapse import collapse as collapse_success, is_known_low_signal
from synthelion.waste_analyzer import WasteAnalyzer, WasteAnalysis
from synthelion.relevance_filter import RelevanceFilter, RelevanceHit
from synthelion.shared_context import SharedContext, SharedContextEntry
from synthelion.output_shaper import OutputShaper
from synthelion.ccr_store import CcrStore
from synthelion.cost_estimator import default_usd_per_1k_tokens, usd, eur
from synthelion.tokenizer import ModelTokenizer, LlmModel
from synthelion.loop_guard import LoopGuard, LoopVerdict, LoopCheckResult, PersistentLoopGuard
from synthelion.plugins.openai_tools import filter_relevant_tools
from synthelion.response_style import get_style_guidance
from synthelion.read_lifecycle import ReadLifecycleTracker, get_read_lifecycle_tracker
from synthelion.privacy_analyzer import PrivacyAnalyzer, PrivacyAnalysisResult
from synthelion.privacy_session import PrivacySession
from synthelion.prompt_injection_guard import PromptInjectionGuard, PromptInjectionResult
from synthelion.ai_transparency_notice import get_transparency_notice

__version__ = "1.2.5"
__author__ = "Passaro Francesco Paolo"


def count_tokens(text: str, mode: str = "approx") -> int:
    """Estimate token count of *text*.

    mode="approx"  → GPT-style estimate: len(text) // 4  (fast, ±15%)
    mode="words"   → whitespace word count (language-dependent)
    """
    if not text:
        return 0
    if mode == "words":
        return len(text.split())
    return len(text) // 4


__all__ = [
    "CompressionLevel",
    "CompressionProfile",
    "CompressionResult",
    "CompressionFilter",
    "ContentType",
    "RoutedCompressionResult",
    "VerbosityLevel",
    "FunctionWordProvider",
    "LanguageDetector",
    "CompressionService",
    "ContentDetector",
    "ContentRouter",
    "count_tokens",
    "CacheAligner",
    "VolatileFinding",
    "SafetyGuard",
    "SafetyLevel",
    "SafetyVerdict",
    "find_sensitive",
    "strip_ansi_noise",
    "collapse_success",
    "is_known_low_signal",
    "WasteAnalyzer",
    "WasteAnalysis",
    "RelevanceFilter",
    "RelevanceHit",
    "SharedContext",
    "SharedContextEntry",
    "OutputShaper",
    "CcrStore",
    "default_usd_per_1k_tokens",
    "usd",
    "eur",
    "ModelTokenizer",
    "LlmModel",
    "LoopGuard",
    "LoopVerdict",
    "LoopCheckResult",
    "PersistentLoopGuard",
    "filter_relevant_tools",
    "get_style_guidance",
    "ReadLifecycleTracker",
    "get_read_lifecycle_tracker",
    "PrivacyAnalyzer",
    "PrivacyAnalysisResult",
    "PrivacySession",
    "PromptInjectionGuard",
    "PromptInjectionResult",
    "get_transparency_notice",
]
