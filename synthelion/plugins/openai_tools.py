# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""OpenAI-compatible function tool definitions for Synthelion.

Usage:
    from synthelion.plugins.openai_tools import get_tool_definitions, execute_tool

    tools = get_tool_definitions()
    # Pass to: client.chat.completions.create(tools=tools, ...)

    result = execute_tool("compress", {"text": "...", "level": "semantic"})
"""
from __future__ import annotations

import threading
import time

from synthelion.core import CompressionService
from synthelion.detector import LanguageDetector
from synthelion.global_idf_provider import GlobalIdfProvider
from synthelion.models import CompressionLevel, CompressionProfile
from synthelion.nlp.text_rank import TextRankSummarizer

_svc = CompressionService(global_idf=GlobalIdfProvider())
_det = LanguageDetector()
_tr = TextRankSummarizer()

# Per-thread call timer: execute_tool() starts it, _record_ledger() reads the
# elapsed time. Thread-local because execute_tool runs in a thread pool
# (asyncio.to_thread in mcp_server.py) — each concurrent call gets its own.
_call_timer = threading.local()

_LEVEL_MAP = {
    "none": CompressionLevel.NONE,
    "light": CompressionLevel.LIGHT,
    "semantic": CompressionLevel.SEMANTIC,
    "aggressive": CompressionLevel.AGGRESSIVE,
}


def _default_level() -> str:
    from synthelion.config import default_compression_level
    return default_compression_level()


def get_tool_definitions() -> list[dict]:
    """Return the list of tool definitions in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "compress",
                "description": (
                    "Compress a text prompt to reduce LLM token usage. "
                    "Removes stop words and lemmatizes content words. "
                    "Supports 50+ languages with zero ML model dependency."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The text to compress."},
                        "level": {
                            "type": "string",
                            "enum": ["light", "semantic", "aggressive"],
                            "description": "Compression level. Default: semantic.",
                        },
                        "language": {
                            "type": "string",
                            "description": "ISO 639-3 code (e.g. 'eng', 'ita'). Auto-detected when omitted.",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "detect_language",
                "description": "Detect the language of a text and return the ISO 639-3 code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The text to analyse."},
                        "with_scores": {
                            "type": "boolean",
                            "description": "Return per-language confidence scores.",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "route_content",
                "description": (
                    "Auto-detect content type (JSON, HTML, diff, log, code, prose) "
                    "and apply the best compression strategy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The content to compress."},
                        "profile": {
                            "type": "string",
                            "enum": ["light", "balanced", "agent", "aggressive"],
                            "description": "Compression profile. Default: balanced.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Optional relevance query for JSON BM25 row selection.",
                        },
                        "command": {
                            "type": "string",
                            "description": (
                                "Optional: the shell command that produced this content (e.g. "
                                "'npm install'). Combined with exit_code=0, known low-signal "
                                "commands (git push, npm install, docker build, terraform apply, "
                                "etc.) get collapsed to 1-3 salient facts instead of full compression."
                            ),
                        },
                        "exit_code": {
                            "type": "integer",
                            "description": "Optional: exit code of `command`. Only 0 enables success collapse.",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "summarize",
                "description": "Extractive summarization of a text block.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The text to summarize."},
                        "sentence_count": {
                            "type": "integer",
                            "description": "Number of sentences to keep.",
                        },
                        "ratio": {
                            "type": "number",
                            "description": "Fraction of sentences to keep (0.0–1.0). Used when sentence_count is omitted.",
                        },
                        "algorithm": {
                            "type": "string",
                            "enum": ["tfidf", "textrank"],
                            "description": "Summarization algorithm. Default: textrank.",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compress_batch",
                "description": "Compress a list of texts in one call.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "texts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of texts to compress.",
                        },
                        "level": {
                            "type": "string",
                            "enum": ["light", "semantic", "aggressive"],
                            "description": "Compression level. Default: semantic.",
                        },
                    },
                    "required": ["texts"],
                },
            },
        },
        # ── AI-agent context tools (Claude-first design) ─────────────────────
        {
            "type": "function",
            "function": {
                "name": "compress_for_context",
                "description": (
                    "Compress content to fit within a token budget before inserting it into "
                    "an LLM context window. Automatically chains routing → NLP compression → "
                    "summarization until the content fits. Token counts are word-count estimates. "
                    "Returns fits_budget=true when the result is within max_tokens."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Content to compress."},
                        "max_tokens": {
                            "type": "integer",
                            "description": "Target token budget. Omit to compress without a limit.",
                        },
                        "profile": {
                            "type": "string",
                            "enum": ["light", "balanced", "agent", "aggressive"],
                            "description": "Compression profile. Default: agent.",
                        },
                        "prefer": {
                            "type": "string",
                            "enum": ["compress", "summarize", "auto"],
                            "description": "Prefer compression or summarization when budget is tight. Default: auto.",
                        },
                    },
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compress_conversation",
                "description": (
                    "Compress a conversation history (list of {role, content} messages) to reduce "
                    "token usage. Keeps the last keep_last_n messages verbatim and compresses/summarizes "
                    "older turns. Returns a compressed messages array compatible with the OpenAI/Anthropic format."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "messages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "role": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["role", "content"],
                            },
                            "description": "Conversation history in OpenAI/Anthropic format.",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": "Target token budget for the entire conversation. Omit to compress without a limit.",
                        },
                        "keep_last_n": {
                            "type": "integer",
                            "description": "Number of recent messages to keep verbatim. Default: 4.",
                        },
                    },
                    "required": ["messages"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "deduplicate",
                "description": (
                    "Remove near-duplicate texts from a list using cosine bag-of-words similarity. "
                    "Useful when multiple sources return overlapping content. Returns the deduplicated list "
                    "and the number of items removed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "texts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of text blocks to deduplicate.",
                        },
                        "threshold": {
                            "type": "number",
                            "description": "Similarity threshold (0.0–1.0). Texts with similarity >= threshold are considered duplicates. Default: 0.8.",
                        },
                    },
                    "required": ["texts"],
                },
            },
        },
        # ── file-level tool ───────────────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "compress_file",
                "description": (
                    "Read a file by path and compress it using the best algorithm for its content type. "
                    "Avoids loading the full raw file into context — returns only the compressed version. "
                    "Supports JSON, HTML, git diff, logs, source code, and plain text. "
                    "Optionally applies a max_tokens budget (chains routing + summarization)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to the file."},
                        "profile": {
                            "type": "string",
                            "enum": ["light", "balanced", "agent", "aggressive"],
                            "description": "Compression profile. Default: agent.",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "description": "Optional token budget. Triggers summarization when routing alone is insufficient.",
                        },
                        "encoding": {
                            "type": "string",
                            "description": "File encoding. Default: utf-8.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        # ── document PII masking (PDF/Word/Excel/CSV/Markdown/text) ───────────
        {
            "type": "function",
            "function": {
                "name": "mask_document",
                "description": (
                    "Mask PII in a PDF/Word/Excel/CSV/Markdown/text file by path. "
                    "PDF/CSV/Markdown/plain-text return masked text; DOCX/XLSX are "
                    "edited in-place into a new masked copy (the original file is "
                    "never overwritten). Writes a file to disk — not read-only. "
                    "Requires the host to have installed synthelion[documents]."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative path to the input file."},
                        "output_path": {
                            "type": "string",
                            "description": "Optional output path. Default: <name>.masked.<ext> next to the input.",
                        },
                        "language": {"type": "string", "description": "ISO 639-3 code. Default: from privacy config."},
                        "session_id": {
                            "type": "string",
                            "description": "Reuse an existing PrivacySession id (see analyze_privacy) so restore_privacy_text works across calls.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        # ── session / memory tools (mirrors tokensave pattern) ────────────────
        {
            "type": "function",
            "function": {
                "name": "session_record",
                "description": (
                    "Save a design/architecture decision or context note that persists "
                    "across sessions. Stored in ChromaDB (semantic recall) or lexical "
                    "fallback when chromadb is not installed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The decision or context note to save."},
                        "reason": {"type": "string", "description": "Optional reason or rationale."},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of tags for filtering.",
                        },
                        "files": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional file paths related to this decision.",
                        },
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "session_recall",
                "description": (
                    "Recall previously saved decisions by semantic similarity (ChromaDB) "
                    "or lexical cosine search (fallback). Use to avoid re-explaining "
                    "architecture choices across sessions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Natural-language search query."},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results. Default: 10.",
                        },
                        "since_days": {
                            "type": "number",
                            "description": "Only return decisions from the last N days.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "session_start",
                "description": "Mark the start of a new agent session. Returns a session ID.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "session_end",
                "description": "Mark the end of the current session and return a summary.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "synthelion_status",
                "description": "Return aggregate token savings statistics from the savings ledger.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {
                            "type": "integer",
                            "description": "Restrict to last N days. Omit for all-time.",
                        },
                    },
                    "required": [],
                },
            },
        },
        # ── ported from Caveman C# 1.4.0 ──────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "safety_check",
                "description": (
                    "Check whether a message contains security-critical or destructive-command "
                    "patterns before compressing it. Returns Normal/Warning/Critical."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"message": {"type": "string", "description": "Text to check."}},
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_sensitive_content",
                "description": (
                    "Scans text for credential-shaped content (AWS/GitHub/Slack tokens, PEM key "
                    "blocks, Bearer headers, bulk .env dumps) before persisting it (e.g. with "
                    "session_record). Read-only — never blocks compression, only flags."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to scan."}},
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_enterprise_guard",
                "description": (
                    "EnterpriseGuard: checks text for outbound-DLP-worthy content (cloud/database/"
                    "FTP/git credentials, private keys, bulk .env dumps) and/or a file path against "
                    "the configured protected security-zone patterns. Read-only/advisory here — the "
                    "actual enforcement on the compress/summarize path is automatic; use this to "
                    "pre-check something (e.g. before reading a file) or to explain why a prior call "
                    "was blocked."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to scan for credential-shaped content."},
                        "path": {"type": "string", "description": "File path to check against protected security-zone patterns."},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyze_waste",
                "description": (
                    "Detect token waste in content: HTML noise, base64 blobs, excessive "
                    "whitespace, large inline JSON blocks. Read-only — does not modify content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string", "description": "Content to analyze."}},
                    "required": ["content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_cache_alignment",
                "description": (
                    "Scan a system prompt for volatile tokens (UUIDs, ISO-8601 timestamps, JWTs, "
                    "hex hashes) that would invalidate the LLM provider's KV-cache prefix reuse."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"system_prompt": {"type": "string", "description": "System prompt to scan."}},
                    "required": ["system_prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "align_cache_prompt",
                "description": (
                    "Rewrite a system prompt so blocks containing volatile tokens (UUIDs, timestamps, "
                    "JWTs, hashes) sink to the end, keeping the stable prefix identical call-to-call so "
                    "the LLM provider's KV-cache can reuse it. Splits on paragraphs (or lines)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"system_prompt": {"type": "string", "description": "System prompt to reorder."}},
                    "required": ["system_prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "shape_output",
                "description": (
                    "Append verbosity-steering instructions to a system prompt to reduce the "
                    "model's OUTPUT tokens (skip ceremony/restatement/reasoning). Idempotent."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "system_prompt": {"type": "string", "description": "System prompt to shape."},
                        "level": {
                            "type": "string",
                            "enum": ["off", "skip_ceremony", "no_restatement", "conclusions_only", "minimum_tokens"],
                            "description": "Verbosity level. Default: no_restatement.",
                        },
                    },
                    "required": ["system_prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "focus_relevant",
                "description": (
                    "Query-focused context shaping: split text into blocks and keep only the "
                    "top-K most relevant to a query (lexical overlap, embedding-free)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to filter."},
                        "query": {"type": "string", "description": "Query to score blocks against."},
                        "top_k": {"type": "integer", "description": "Number of blocks to keep. Default: 3."},
                    },
                    "required": ["text", "query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "estimate_cost",
                "description": "Estimate the USD/EUR monetary value of a token count for a given model.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tokens": {"type": "integer", "description": "Token count."},
                        "model": {
                            "type": "string",
                            "enum": ["gpt4", "gpt3_5_turbo", "llama3", "gemma3", "claude3"],
                            "description": "Model to price against. Default: gpt4.",
                        },
                    },
                    "required": ["tokens"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_commit_message",
                "description": "Generate an ultra-compact conventional commit message from a git diff.",
                "parameters": {
                    "type": "object",
                    "properties": {"diff": {"type": "string", "description": "Unified git diff text."}},
                    "required": ["diff"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "review_diff",
                "description": (
                    "Generate single-line PR review comments from a git diff: flags likely bugs, "
                    "security-sensitive lines, perf-relevant constructs, and TODOs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"diff": {"type": "string", "description": "Unified git diff text."}},
                    "required": ["diff"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_tool_loop",
                "description": (
                    "Pre-tool guardrail: check whether a tool call would repeat an identical prior "
                    "call too many times in a row for this session (agent stuck retrying the same "
                    "failed approach). Call this BEFORE issuing the real tool call. Returns Allow/Block; "
                    "on Block, stop and change approach or ask the user instead of retrying."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Name of the tool about to be called."},
                        "arguments": {"type": "object", "description": "Arguments that would be passed to it."},
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                        "max_repeats": {
                            "type": "integer",
                            "description": "Identical repeats allowed before blocking. Default: 2.",
                        },
                    },
                    "required": ["tool"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reset_tool_loop",
                "description": "Clear the loop-guard call history for a session (use after a genuine change of approach).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_relevant_tools",
                "description": (
                    "Filter the full set of available tool definitions down to the ones most "
                    "relevant to a task/query, for orchestrators that build their own per-turn "
                    "tools=[...] array for an LLM. Does not affect this MCP server's own "
                    "tools/list (which has no per-turn query)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The task/query to score tools against."},
                        "top_k": {"type": "integer", "description": "How many tools to keep. Default: 10."},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "mask_old_tool_output",
                "description": (
                    "Given a chronological list of {tool, output} tool-call results, replaces all "
                    "but the most recent `keep_last` outputs with a short placeholder, storing the "
                    "originals for later retrieval via expand_masked_output. Use to keep old tool "
                    "output from silently bloating every subsequent turn's context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "outputs": {
                            "type": "array",
                            "items": {"type": "object"},
                            "description": "Chronological list of {tool, output, ...} dicts.",
                        },
                        "keep_last": {"type": "integer", "description": "How many recent entries to leave untouched. Default: 3."},
                    },
                    "required": ["outputs"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "expand_masked_output",
                "description": "Retrieve the original text behind a placeholder produced by mask_old_tool_output, by its hash.",
                "parameters": {
                    "type": "object",
                    "properties": {"hash": {"type": "string", "description": "The hash from the masked placeholder."}},
                    "required": ["hash"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_artifact_index",
                "description": (
                    "Returns a catalog of everything mask_old_tool_output has masked so far, "
                    "grouped by tool, with the hash needed to retrieve each one via "
                    "expand_masked_output. Meant to be re-injected into context so the model "
                    "knows what was hidden and can ask for it back."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "rewrite_command",
                "description": (
                    "Suggests a less verbose variant of a known shell command (same semantics "
                    "and exit code, fewer decorative banners/pager/audit output) — e.g. adds "
                    "--no-pager to git log, --no-fund --no-audit to npm install. Advisory only: "
                    "never executed, the caller decides whether to actually run the suggestion. "
                    "Refuses to rewrite composite commands (&&, |, ;, backticks, $(), redirects)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string", "description": "The shell command to consider rewriting."}},
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "diff_tool_output",
                "description": (
                    "For a tool called again with identical arguments (same session), returns a "
                    "unified diff against the previous call's output instead of the full text again "
                    "— but only when that diff is actually shorter. First call for a given "
                    "tool/arguments/session always returns the output unchanged."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Name of the tool that was called."},
                        "arguments": {"type": "object", "description": "Arguments it was called with."},
                        "output": {"type": "string", "description": "The output to evaluate/diff."},
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                    },
                    "required": ["tool", "output"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_response_style_guidance",
                "description": (
                    "Returns a block of verbosity-reduction instructions to inject into an agent's "
                    "own system prompt, so its generated responses are more concise (no filler "
                    "openings, no restating the question, structured bug-fix format at full/ultra). "
                    "Different axis from every other tool here: this shapes what the model writes, "
                    "not what enters its context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "string", "enum": ["lite", "full", "ultra"], "description": "Aggressiveness. Default: lite."},
                        "language": {"type": "string", "description": "ISO 639-3 code of the response language, e.g. 'zho'. Adds a CJK-specific note when applicable."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "track_file_read",
                "description": (
                    "Records a file read for freshness tracking within a session. Returns whether "
                    "this read is fresh or already stale (a write landed at/after this turn). Use "
                    "alongside track_file_write and check_read_maturity to know when an earlier "
                    "Read's output sitting in context is safe to collapse."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path that was read."},
                        "turn": {"type": "integer", "description": "Monotonically increasing turn/step counter for this session."},
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                    },
                    "required": ["path", "turn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "track_file_write",
                "description": "Records a file write for freshness tracking — any earlier reads of this path become stale.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path that was written/edited."},
                        "turn": {"type": "integer", "description": "Monotonically increasing turn/step counter for this session."},
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                    },
                    "required": ["path", "turn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_read_maturity",
                "description": (
                    "Checks whether a previously-tracked file read is stale/superseded and has been "
                    "quiet long enough to safely collapse into a compact marker (mirrors "
                    "provider KV-cache-breakpoint stability: a file still being actively edited "
                    "would just invalidate again next turn, so maturation waits for quiescence)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to check."},
                        "turn": {"type": "integer", "description": "Current turn/step counter for this session."},
                        "session_id": {"type": "string", "description": "Session/agent id. Default: 'default'."},
                    },
                    "required": ["path", "turn"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "analyze_privacy",
                "description": (
                    "Detects PII/sensitive data in text across 33 country/region rule sets (email, "
                    "IBAN, credit cards, national tax/ID numbers with real checksum validation, GPS "
                    "coordinates, etc.), scores it 0-100 with a risk level, maps GDPR/EU AI "
                    "Act/NIS2/PCI-DSS/NIST compliance flags, and optionally masks detected values with "
                    "recoverable placeholders (use restore_privacy_text with the returned session_id "
                    "to get the original text back). "
                    "When auto_masking=true, the result includes 'blocked' (bool): true if the message "
                    "exceeds the configured risk threshold — agents MUST discard the original text "
                    "and refuse to process it when blocked=true."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to analyze."},
                        "language": {"type": "string", "description": "Message language: en/it/de/fr/es. Default: en."},
                        "auto_masking": {"type": "boolean", "description": "Mask detected PII with placeholders. Default: false."},
                        "session_id": {"type": "string", "description": "Reuse an existing masking session (dedupes placeholders across calls). Default: new session."},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "restore_privacy_text",
                "description": "Replaces [PG_n] placeholders in text with the original values from a session created by analyze_privacy(auto_masking=true).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text containing [PG_n] placeholders."},
                        "session_id": {"type": "string", "description": "The session_id returned by analyze_privacy."},
                    },
                    "required": ["text", "session_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_prompt_injection",
                "description": (
                    "Heuristic screening of untrusted text (user messages, tool output, retrieved "
                    "documents) for prompt-injection/jailbreak attempts before it reaches an LLM's "
                    "context — instruction override, system-prompt exfiltration, role hijack, "
                    "delimiter injection, encoded payloads, exfiltration coercion."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "Text to screen."}},
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_ai_transparency_notice",
                "description": (
                    "Returns a user-facing disclosure message that the user is interacting with an AI "
                    "system whose input is screened/masked for sensitive data (supports EU AI Act "
                    "Art.50 transparency obligations — confirm applicability/wording with legal counsel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "language": {"type": "string", "description": "en/it/de/fr/es. Default: en."},
                        "custom_message": {"type": "string", "description": "Override the built-in message entirely."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_project_wiki",
                "description": (
                    "Recursively scan a project folder and produce AI-friendly, semantically "
                    "compressed Markdown documentation (structure, dependencies, file contents)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Project folder to scan."},
                        "include_contents": {
                            "type": "boolean",
                            "description": "Include compressed file contents. Default: true.",
                        },
                        "depth": {
                            "type": "integer",
                            "enum": [1, 2, 3, 4],
                            "description": "Detail level 1-4. Default: the dashboard/config-configured value (normally 2). 1=overview only, 4=adds short code excerpts.",
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "retrieve_compressed_text",
                "description": (
                    "Retrieves the original text behind a [ccr:xxxxxxxx] marker — appears in proxy "
                    "responses when proxy.ccr_enabled is on and a string was compressed enough to be "
                    "worth caching. Same idea as restore_privacy_text, but for ordinary lossy "
                    "compression instead of PII masking. Entries expire after proxy.ccr_ttl_seconds."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"token": {"type": "string", "description": "The token from a [ccr:token] marker."}},
                    "required": ["token"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_add",
                "description": (
                    "Adds a note to Synthelion's cross-agent shared memory — visible to every agent "
                    "(Claude Code, Cursor, Aider, Codex CLI, ...) that calls memory_recall, not just "
                    "the one that wrote it. Deduplicated by exact text: adding the same note twice is a "
                    "no-op. For small, durable, cross-agent facts (\"this repo uses pnpm, not npm\"), "
                    "not for large-scale semantic search."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "The note to remember."},
                        "agent": {"type": "string", "description": "Name of the agent/tool writing this (optional)."},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "memory_recall",
                "description": "Lists the most recent notes from Synthelion's cross-agent shared memory (see memory_add).",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Max notes to return. Default: 20."}},
                    "required": [],
                },
            },
        },
    ]


def get_tool_list() -> list[str]:
    return [t["function"]["name"] for t in get_tool_definitions()]


def execute_tool(name: str, arguments: dict) -> dict:
    """Execute a Synthelion tool by name and return a JSON-serializable result."""
    _call_timer.start = time.perf_counter()
    if name == "compress":
        # Same PrivacyGuard pre-pass as the Claude Code hook (cli.py's
        # `_cmd_compress`) and RagAgent.prepare_message — this tool is what
        # Claude Code's MCP server, OpenCode, and any other MCP/OpenAI
        # function-calling client actually call for compression, so it needs
        # the same masking/block-on-risk guard, not just the terminal hook.
        text = arguments["text"]
        _original_text = text
        from synthelion.config import privacy_config
        pcfg = privacy_config()
        if pcfg["enabled"]:
            from synthelion.privacy_analyzer import PrivacyAnalyzer, PrivacyBlockedError, build_privacy_notice
            from synthelion.privacy_session import PrivacySession

            analyzer = PrivacyAnalyzer()
            if pcfg.get("whitelist"):
                analyzer.add_to_whitelist(*pcfg["whitelist"])
            session = PrivacySession() if pcfg["auto_masking"] else None
            presult = analyzer.analyze(text, pcfg["language"], session=session, auto_masking=pcfg["auto_masking"])

            if pcfg.get("block_on_risk") and presult.score >= pcfg.get("block_min_score", 61):
                transparency_notice = None
                if pcfg["ai_transparency_notice"]:
                    from synthelion.ai_transparency_notice import get_transparency_notice
                    transparency_notice = get_transparency_notice(
                        pcfg["language"], pcfg.get("transparency_custom_message") or None,
                    )
                raise PrivacyBlockedError(presult, build_privacy_notice(presult, transparency_notice, blocked=True))

            if pcfg["auto_masking"] and presult.masked_text:
                text = presult.masked_text

        # EnterpriseGuard: outbound DLP, independent of the PII pre-pass above —
        # credential-shaped content has no safe redacted form, always block-or-allow.
        # Checked against the ORIGINAL (pre-privacy-masking) text — see cli.py's
        # identical comment for why (masking can break a secret's regex shape).
        from synthelion.enterprise_guard import EnterpriseGuard, EnterpriseGuardBlockedError
        eg_result = EnterpriseGuard().check_text(_original_text)
        if eg_result.blocked:
            raise EnterpriseGuardBlockedError(eg_result)

        level = _LEVEL_MAP.get((arguments.get("level") or _default_level()).lower(), CompressionLevel.SEMANTIC)
        r = _svc.compress(text, level, iso3=arguments.get("language"))
        _record_ledger("compress", r.original_tokens, r.compressed_tokens)
        return {
            "compressed_text": r.compressed_text,
            "original_tokens": r.original_tokens,
            "compressed_tokens": r.compressed_tokens,
            "efficiency_pct": round(r.efficiency_pct, 2),
            "error": r.error_message,
            "synthelion_metrics": _fmt_metrics(r.original_tokens, r.compressed_tokens),
        }

    if name == "detect_language":
        text = arguments["text"]
        if arguments.get("with_scores"):
            return {"scores": _det.detect_with_scores(text)}
        return {"language": _det.detect(text)}

    if name == "route_content":
        from synthelion.content_router import ContentRouter
        profile_map = {
            "light": CompressionProfile.LIGHT,
            "balanced": CompressionProfile.BALANCED,
            "agent": CompressionProfile.AGENT,
            "aggressive": CompressionProfile.AGGRESSIVE,
        }
        profile = profile_map.get((arguments.get("profile") or "balanced").lower(), CompressionProfile.BALANCED)
        router = ContentRouter.from_profile(profile)
        r = router.route(
            arguments["content"], arguments.get("query"),
            command=arguments.get("command"), exit_code=arguments.get("exit_code"),
        )
        _record_ledger("route_content", r.tokens_before, r.tokens_after, r.detected_type.value)
        return {
            "compressed": r.compressed,
            "detected_type": r.detected_type.value,
            "strategy_used": r.strategy_used,
            "tokens_before": r.tokens_before,
            "tokens_after": r.tokens_after,
            "savings_pct": round(r.savings_pct, 2),
            "ccr_hash": r.ccr_hash,
            "synthelion_metrics": _fmt_metrics(r.tokens_before, r.tokens_after),
        }

    if name == "summarize":
        algo = arguments.get("algorithm", "textrank")
        text = arguments["text"]
        sc = arguments.get("sentence_count")
        ratio = arguments.get("ratio")
        if algo == "tfidf":
            from synthelion.nlp.summarizer import TfIdfSummarizer
            summ = TfIdfSummarizer(global_idf=GlobalIdfProvider())
        else:
            summ = _tr
        summary = summ.summarize(text, sentence_count=sc, ratio=ratio)
        before = len(text.split())
        after = len(summary.split())
        _record_ledger("summarize", before, after)
        return {
            "summary": summary,
            "synthelion_metrics": _fmt_metrics(before, after),
        }

    if name == "compress_batch":
        level = _LEVEL_MAP.get((arguments.get("level") or _default_level()).lower(), CompressionLevel.SEMANTIC)
        results = _svc.compress_batch(arguments["texts"], level)
        total_before = sum(r.original_tokens for r in results)
        total_after = sum(r.compressed_tokens for r in results)
        _record_ledger("compress_batch", total_before, total_after)
        return {
            "results": [
                {
                    "compressed_text": r.compressed_text,
                    "efficiency_pct": round(r.efficiency_pct, 2),
                    "error": r.error_message,
                }
                for r in results
            ],
            "synthelion_metrics": _fmt_metrics(total_before, total_after),
        }

    if name == "compress_file":
        return _exec_compress_file(arguments)

    if name == "mask_document":
        return _exec_mask_document(arguments)

    if name == "compress_for_context":
        return _exec_compress_for_context(arguments)

    if name == "compress_conversation":
        return _exec_compress_conversation(arguments)

    if name == "deduplicate":
        return _exec_deduplicate(arguments)

    # ── session tools ─────────────────────────────────────────────────────────

    if name == "session_record":
        from synthelion.analytics.session_db import get_session_db
        db = get_session_db()
        decision_id = db.record_decision(
            text=arguments["text"],
            reason=arguments.get("reason", ""),
            tags=arguments.get("tags"),
            files=arguments.get("files"),
        )
        return {"id": decision_id, "status": "recorded", "backend": db.backend()}

    if name == "session_recall":
        from synthelion.analytics.session_db import get_session_db
        import time as _time
        db = get_session_db()
        since_days = arguments.get("since_days")
        since_ts = _time.time() - since_days * 86400 if since_days else None
        decisions = db.session_recall(
            query=arguments.get("query"),
            since=since_ts,
            limit=int(arguments.get("limit", 10)),
        )
        return {"decisions": decisions, "backend": db.backend()}

    if name == "session_start":
        from synthelion.analytics.session_db import get_session_db
        return get_session_db().session_start()

    if name == "session_end":
        from synthelion.analytics.session_db import get_session_db
        return get_session_db().session_end()

    if name == "synthelion_status":
        from synthelion.analytics.ledger import get_ledger
        ledger = get_ledger()
        days = arguments.get("days")
        records = ledger.records_since(int(days)) if days else ledger.all_records()
        return ledger.summary(records)

    if name == "safety_check":
        return _exec_safety_check(arguments)

    if name == "check_sensitive_content":
        return _exec_check_sensitive_content(arguments)

    if name == "check_enterprise_guard":
        return _exec_check_enterprise_guard(arguments)

    if name == "analyze_waste":
        return _exec_analyze_waste(arguments)

    if name == "check_cache_alignment":
        return _exec_check_cache_alignment(arguments)

    if name == "align_cache_prompt":
        return _exec_align_cache_prompt(arguments)

    if name == "shape_output":
        return _exec_shape_output(arguments)

    if name == "focus_relevant":
        return _exec_focus_relevant(arguments)

    if name == "estimate_cost":
        return _exec_estimate_cost(arguments)

    if name == "generate_commit_message":
        return _exec_generate_commit_message(arguments)

    if name == "review_diff":
        return _exec_review_diff(arguments)

    if name == "generate_project_wiki":
        return _exec_generate_project_wiki(arguments)

    if name == "check_tool_loop":
        return _exec_check_tool_loop(arguments)

    if name == "reset_tool_loop":
        return _exec_reset_tool_loop(arguments)

    if name == "list_relevant_tools":
        return _exec_list_relevant_tools(arguments)

    if name == "mask_old_tool_output":
        return _exec_mask_old_tool_output(arguments)

    if name == "expand_masked_output":
        return _exec_expand_masked_output(arguments)

    if name == "diff_tool_output":
        return _exec_diff_tool_output(arguments)

    if name == "get_artifact_index":
        return _exec_get_artifact_index(arguments)

    if name == "rewrite_command":
        return _exec_rewrite_command(arguments)

    if name == "get_response_style_guidance":
        return _exec_get_response_style_guidance(arguments)

    if name == "track_file_read":
        return _exec_track_file_read(arguments)

    if name == "track_file_write":
        return _exec_track_file_write(arguments)

    if name == "check_read_maturity":
        return _exec_check_read_maturity(arguments)

    if name == "analyze_privacy":
        return _exec_analyze_privacy(arguments)

    if name == "restore_privacy_text":
        return _exec_restore_privacy_text(arguments)

    if name == "check_prompt_injection":
        return _exec_check_prompt_injection(arguments)

    if name == "get_ai_transparency_notice":
        return _exec_get_ai_transparency_notice(arguments)

    if name == "retrieve_compressed_text":
        return _exec_retrieve_compressed_text(arguments)

    if name == "memory_add":
        return _exec_memory_add(arguments)

    if name == "memory_recall":
        return _exec_memory_recall(arguments)

    return {"error": f"Unknown tool: {name}"}


def _exec_retrieve_compressed_text(arguments: dict) -> dict:
    from synthelion.analytics.ccr_store import get_ccr_store
    token = arguments.get("token", "")
    original = get_ccr_store().retrieve(token)
    if original is None:
        return {"error": "token not found or expired"}
    return {"original": original}


def _exec_memory_add(arguments: dict) -> dict:
    from synthelion.analytics.shared_memory import get_shared_memory
    text = arguments.get("text", "")
    if not text.strip():
        return {"error": "text is required"}
    added = get_shared_memory().add(text, agent=arguments.get("agent", ""))
    return {"added": added}


def _exec_memory_recall(arguments: dict) -> dict:
    from synthelion.analytics.shared_memory import get_shared_memory
    limit = int(arguments.get("limit", 20))
    return {"notes": get_shared_memory().recent(limit)}


# ── helpers ───────────────────────────────────────────────────────────────────

_SONNET_PRICE_PER_TOKEN: float = 3e-6  # $3.00/MTok (Sonnet 4.6 input)


def _fmt_metrics(before: int, after: int) -> str:
    saved = max(0, before - after)
    pct = (saved / before * 100) if before > 0 else 0.0
    cost = saved * _SONNET_PRICE_PER_TOKEN
    return f"before={before} after={after} saved={saved} ({pct:.1f}%) ~${cost:.5f}"


def _record_ledger(tool: str, before: int, after: int, content_type: str = "") -> None:
    try:
        from synthelion.analytics.ledger import get_ledger
        start = getattr(_call_timer, "start", None)
        duration_ms = (time.perf_counter() - start) * 1000 if start is not None else 0.0
        get_ledger().record(tool, before, after, content_type=content_type, duration_ms=duration_ms)
    except Exception:
        pass


# ── new AI-agent context tools ────────────────────────────────────────────────

def _exec_compress_file(arguments: dict) -> dict:
    import os
    path = arguments["path"]
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}
    encoding = arguments.get("encoding", "utf-8")
    try:
        with open(path, encoding=encoding, errors="replace") as fh:
            content = fh.read()
    except Exception as exc:
        return {"error": f"Could not read file: {exc}"}
    ctx_args: dict = {"content": content, "profile": arguments.get("profile", "agent")}
    if "max_tokens" in arguments:
        ctx_args["max_tokens"] = arguments["max_tokens"]
    result = _exec_compress_for_context(ctx_args)
    result["path"] = path
    result["file_size_chars"] = len(content)
    return result


def _exec_mask_document(arguments: dict) -> dict:
    import os
    from synthelion.config import documents_config, privacy_config
    from synthelion.privacy_analyzer import PrivacyAnalyzer
    from synthelion import document_extractor as de

    path = arguments["path"]
    if not os.path.isfile(path):
        return {"error": f"File not found: {path}"}

    dcfg = documents_config()
    pcfg = privacy_config()
    language = arguments.get("language") or pcfg.get("language", "en")

    max_bytes = dcfg["max_file_size_mb"] * 1024 * 1024
    size = os.path.getsize(path)
    if size > max_bytes:
        return {"error": f"File is {size / (1024 * 1024):.1f} MB, exceeds documents.max_file_size_mb ({dcfg['max_file_size_mb']})"}

    from synthelion.cli import _default_masked_output_path
    output_path = arguments.get("output_path") or _default_masked_output_path(path, dcfg["output_suffix"])

    analyzer = PrivacyAnalyzer()
    if pcfg.get("whitelist"):
        analyzer.add_to_whitelist(*pcfg["whitelist"])
    session_id, session = _get_or_create_privacy_session(arguments.get("session_id"))
    count_before = session.count

    try:
        fmt = de.detect_format(path)
        if fmt == "docx":
            doc = de.load_docx(path)
            de.mask_docx_in_place(doc, analyzer, session, language)
            doc.save(output_path)
        elif fmt == "xlsx":
            wb = de.load_xlsx(path)
            de.mask_xlsx_in_place(wb, analyzer, session, language)
            wb.save(output_path)
        else:
            from synthelion.privacy_stream import PrivacyStreamMasker
            masker = PrivacyStreamMasker(
                analyzer=analyzer, session=session, language=language, overlap_chars=dcfg["chunk_overlap_chars"],
            )
            if fmt == "pdf":
                chunks = de.extract_pdf_text(path)
            elif fmt == "csv":
                chunks = de.extract_csv_rows(path)
            else:
                chunks = de.extract_text_chunks(path)
            parts = [masker.feed(chunk) for chunk in chunks]
            parts.append(masker.flush())
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("".join(parts))
    except ImportError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"Masking failed: {exc}"}

    # Redacted summary only (placeholder + category) — never the original
    # values; see PrivacySession.summary()'s docstring. Sliced to just the
    # entries added by this call, since a reused session_id may already carry
    # entries from earlier calls.
    new_entries = session.summary()[count_before:]
    return {
        "output_path": output_path,
        "masked_count": len(new_entries),
        "detected_categories": sorted({entry["category"] for entry in new_entries}),
        "session_id": session_id,
    }


def _exec_compress_for_context(arguments: dict) -> dict:
    from synthelion.content_router import ContentRouter
    from synthelion.models import CompressionProfile
    from synthelion.nlp.text_rank import TextRankSummarizer

    profile_map = {
        "light": CompressionProfile.LIGHT,
        "balanced": CompressionProfile.BALANCED,
        "agent": CompressionProfile.AGENT,
        "aggressive": CompressionProfile.AGGRESSIVE,
    }
    content = arguments["content"]
    max_tokens: int | None = arguments.get("max_tokens")
    prefer = (arguments.get("prefer") or "auto").lower()
    profile = profile_map.get((arguments.get("profile") or "agent").lower(), CompressionProfile.AGENT)

    router = ContentRouter.from_profile(profile)
    routed = router.route(content)
    result_text = routed.compressed or content
    result_tokens = routed.tokens_after

    fits = max_tokens is None or result_tokens <= max_tokens
    strategy = "route"

    if not fits and prefer in ("summarize", "auto"):
        # Try summarization to hit the budget
        ratio = max(0.05, min(0.9, (max_tokens or result_tokens) / max(result_tokens, 1)))
        try:
            summary = TextRankSummarizer().summarize(result_text, ratio=ratio)
            summary_tokens = len(summary.split())
            if summary and summary_tokens < result_tokens:
                result_text = summary
                result_tokens = summary_tokens
                strategy = "route+summarize"
                fits = max_tokens is None or result_tokens <= max_tokens
        except Exception:
            pass

    _record_ledger("compress_for_context", routed.tokens_before, result_tokens, routed.detected_type.value)
    out: dict = {
        "compressed": result_text,
        "detected_type": routed.detected_type.value,
        "tokens_before": routed.tokens_before,
        "tokens_after": result_tokens,
        "strategy": strategy,
        "fits_budget": fits,
        "synthelion_metrics": _fmt_metrics(routed.tokens_before, result_tokens),
    }
    if max_tokens is not None and not fits:
        out["budget_exceeded_by"] = result_tokens - max_tokens
    return out


def _exec_compress_conversation(arguments: dict) -> dict:
    from synthelion.content_router import ContentRouter
    from synthelion.models import CompressionProfile
    from synthelion.nlp.text_rank import TextRankSummarizer

    messages: list[dict] = arguments.get("messages") or []
    max_tokens: int | None = arguments.get("max_tokens")
    keep_last_n: int = max(0, int(arguments.get("keep_last_n") or 4))

    def _words(msg: dict) -> int:
        return len(str(msg.get("content", "")).split())

    total_before = sum(_words(m) for m in messages)

    kept_tail = messages[-keep_last_n:] if keep_last_n > 0 and len(messages) > keep_last_n else []
    to_compress = messages[: len(messages) - len(kept_tail)]

    if not to_compress:
        return {
            "messages": messages,
            "tokens_before": total_before,
            "tokens_after": total_before,
            "messages_before": len(messages),
            "messages_after": len(messages),
            "strategy": "none",
            "synthelion_metrics": _fmt_metrics(total_before, total_before),
        }

    router = ContentRouter.from_profile(CompressionProfile.AGENT)
    compressed_msgs: list[dict] = []
    for msg in to_compress:
        content = str(msg.get("content", ""))
        if len(content.split()) > 15:
            r = router.route(content)
            compressed_msgs.append({**msg, "content": r.compressed or content})
        else:
            compressed_msgs.append(msg)

    result_messages = compressed_msgs + kept_tail
    total_after = sum(_words(m) for m in result_messages)

    strategy = "route"

    # If still over budget, collapse older messages into a summary
    if max_tokens and total_after > max_tokens:
        tail_tokens = sum(_words(m) for m in kept_tail)
        budget_for_history = max(20, (max_tokens or total_after) - tail_tokens)
        history_text = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}" for m in compressed_msgs
        )
        ratio = max(0.05, min(0.8, budget_for_history / max(len(history_text.split()), 1)))
        try:
            summary = TextRankSummarizer().summarize(history_text, ratio=ratio)
            if summary:
                result_messages = [
                    {"role": "system", "content": f"[Compressed conversation history]\n{summary}"}
                ] + kept_tail
                total_after = sum(_words(m) for m in result_messages)
                strategy = "route+summarize"
        except Exception:
            pass

    _record_ledger("compress_conversation", total_before, total_after)
    return {
        "messages": result_messages,
        "tokens_before": total_before,
        "tokens_after": total_after,
        "messages_before": len(messages),
        "messages_after": len(result_messages),
        "strategy": strategy,
        "synthelion_metrics": _fmt_metrics(total_before, total_after),
    }


def _exec_deduplicate(arguments: dict) -> dict:
    import re
    from collections import Counter

    texts: list[str] = arguments.get("texts") or []
    threshold: float = float(arguments.get("threshold") or 0.8)

    def _bag(t: str) -> Counter:
        return Counter(re.findall(r"\b\w{3,}\b", t.lower()))

    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        dot = sum(a[w] * b[w] for w in a if w in b)
        na = sum(v * v for v in a.values()) ** 0.5
        nb = sum(v * v for v in b.values()) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    bags = [_bag(t) for t in texts]
    kept_indices: list[int] = []
    removed = 0

    for i in range(len(texts)):
        is_dup = any(_cosine(bags[i], bags[j]) >= threshold for j in kept_indices)
        if is_dup:
            removed += 1
        else:
            kept_indices.append(i)

    result = [texts[i] for i in kept_indices]
    tokens_before = sum(len(t.split()) for t in texts)
    tokens_after = sum(len(t.split()) for t in result)
    _record_ledger("deduplicate", tokens_before, tokens_after)
    return {
        "texts": result,
        "original_count": len(texts),
        "deduplicated_count": len(result),
        "removed_count": removed,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "synthelion_metrics": _fmt_metrics(tokens_before, tokens_after),
    }


# ── ported from Caveman C# 1.4.0 ──────────────────────────────────────────────

def _exec_safety_check(arguments: dict) -> dict:
    from synthelion.safety_guard import SafetyGuard
    verdict = SafetyGuard().check(arguments["message"])
    return {"level": verdict.level.value, "reason": verdict.reason, "should_compress": verdict.should_compress}


def _exec_check_sensitive_content(arguments: dict) -> dict:
    from synthelion.sensitive_guard import find_sensitive
    match = find_sensitive(arguments["text"])
    return {"sensitive": match is not None, "class": match}


def _exec_check_enterprise_guard(arguments: dict) -> dict:
    from synthelion.enterprise_guard import EnterpriseGuard
    guard = EnterpriseGuard()
    text = arguments.get("text")
    path = arguments.get("path")
    result = None
    if text:
        result = guard.check_text(text)
    if (result is None or not result.blocked) and path:
        path_result = guard.check_path(path)
        if path_result.blocked:
            result = path_result
        elif result is None:
            result = path_result
    if result is None:
        result = guard.check_text("")
    return {
        "blocked": result.blocked,
        "category": result.category,
        "rule_name": result.rule_name,
        "reason": result.reason,
    }


def _exec_analyze_waste(arguments: dict) -> dict:
    from synthelion.waste_analyzer import WasteAnalyzer
    a = WasteAnalyzer().analyze(arguments["content"])
    return {
        "html_noise_tokens": a.html_noise_tokens,
        "base64_tokens": a.base64_tokens,
        "whitespace_tokens": a.whitespace_tokens,
        "json_bloat_tokens": a.json_bloat_tokens,
        "total_waste_tokens": a.total_waste_tokens,
    }


def _exec_check_cache_alignment(arguments: dict) -> dict:
    from synthelion.cache_aligner import CacheAligner
    findings = CacheAligner().scan(arguments["system_prompt"])
    return {
        "has_volatile_tokens": len(findings) > 0,
        "findings": [{"label": f.label, "sample": f.sample} for f in findings],
    }


def _exec_align_cache_prompt(arguments: dict) -> dict:
    from synthelion.cache_aligner import CacheAligner
    result = CacheAligner().align(arguments["system_prompt"])
    return {"system_prompt": result.prompt, "reordered": result.reordered, "moved_blocks": result.moved_blocks}


_VERBOSITY_MAP = None


def _exec_shape_output(arguments: dict) -> dict:
    global _VERBOSITY_MAP
    from synthelion.models import VerbosityLevel
    from synthelion.output_shaper import OutputShaper
    if _VERBOSITY_MAP is None:
        _VERBOSITY_MAP = {
            "off": VerbosityLevel.OFF,
            "skip_ceremony": VerbosityLevel.SKIP_CEREMONY,
            "no_restatement": VerbosityLevel.NO_RESTATEMENT,
            "conclusions_only": VerbosityLevel.CONCLUSIONS_ONLY,
            "minimum_tokens": VerbosityLevel.MINIMUM_TOKENS,
        }
    level = _VERBOSITY_MAP.get((arguments.get("level") or "no_restatement").lower(), VerbosityLevel.NO_RESTATEMENT)
    shaped = OutputShaper().shape_system_prompt(arguments["system_prompt"], level)
    return {"system_prompt": shaped}


def _exec_focus_relevant(arguments: dict) -> dict:
    from synthelion.relevance_filter import RelevanceFilter
    top_k = int(arguments.get("top_k") or 3)
    focused = RelevanceFilter().focus(arguments["text"], arguments["query"], top_k)
    return {"focused": focused}


_LLM_MODEL_MAP = None


def _exec_estimate_cost(arguments: dict) -> dict:
    global _LLM_MODEL_MAP
    from synthelion.cost_estimator import default_usd_per_1k_tokens, eur, usd
    from synthelion.tokenizer import LlmModel
    if _LLM_MODEL_MAP is None:
        _LLM_MODEL_MAP = {
            "gpt4": LlmModel.GPT4, "gpt3_5_turbo": LlmModel.GPT3_5_TURBO,
            "llama3": LlmModel.LLAMA3, "gemma3": LlmModel.GEMMA3, "claude3": LlmModel.CLAUDE3,
        }
    model = _LLM_MODEL_MAP.get((arguments.get("model") or "gpt4").lower(), LlmModel.GPT4)
    tokens = int(arguments["tokens"])
    price = default_usd_per_1k_tokens(model)
    return {"usd": round(usd(tokens, price), 6), "eur": round(eur(tokens, price), 6), "usd_per_1k_tokens": price}


def _exec_generate_commit_message(arguments: dict) -> dict:
    from synthelion.devtools.commit_generator import CommitGenerator
    s = CommitGenerator().generate_from_diff(arguments["diff"])
    return {"message": s.full_message, "type": s.type, "scope": s.scope, "subject": s.subject}


def _exec_review_diff(arguments: dict) -> dict:
    from synthelion.devtools.review_service import ReviewService
    r = ReviewService().review_diff(arguments["diff"])
    return {
        "changed_files": r.changed_files,
        "additions": r.additions,
        "deletions": r.deletions,
        "total_issues": r.total_issues,
        "comments": [
            {"line": c.line, "severity": c.severity, "message": c.message} for c in r.comments
        ],
    }


def _exec_generate_project_wiki(arguments: dict) -> dict:
    from synthelion.config import default_wiki_depth
    from synthelion.devtools.wiki import ProjectWiki
    include_contents = arguments.get("include_contents", True)
    depth = int(arguments.get("depth") or default_wiki_depth())
    try:
        markdown = ProjectWiki().generate(arguments["path"], include_contents=include_contents, depth=depth)
    except NotADirectoryError as exc:
        return {"error": str(exc)}
    return {"markdown": markdown}


_loop_guard = None
_loop_guard_lock = threading.Lock()


def _get_loop_guard():
    global _loop_guard
    if _loop_guard is None:
        with _loop_guard_lock:
            if _loop_guard is None:
                from synthelion.loop_guard import LoopGuard
                _loop_guard = LoopGuard()
    return _loop_guard


def _exec_check_tool_loop(arguments: dict) -> dict:
    guard = _get_loop_guard()
    result = guard.check(
        arguments["tool"],
        arguments.get("arguments"),
        session_id=arguments.get("session_id") or "default",
        max_repeats=arguments.get("max_repeats"),
    )
    return {
        "verdict": result.verdict.value,
        "should_block": result.should_block,
        "repeat_count": result.repeat_count,
        "reason": result.reason,
    }


def _exec_reset_tool_loop(arguments: dict) -> dict:
    guard = _get_loop_guard()
    guard.reset(arguments.get("session_id") or "default")
    return {"status": "reset"}


def filter_relevant_tools(
    query: str, top_k: int = 10, tool_defs: list[dict] | None = None,
) -> list[dict]:
    """Filters tool definitions down to the *top_k* most relevant to *query*, scoring
    each tool's `name + description` with `RelevanceFilter.score()` (lemmatized
    content-word overlap — the same scorer `focus_relevant` already uses on arbitrary
    text). Keeps the original relative order among the tools kept (stable, does not
    reorder the list a caller then hands to an LLM)."""
    from synthelion.relevance_filter import RelevanceFilter
    defs = tool_defs if tool_defs is not None else get_tool_definitions()
    if top_k >= len(defs):
        return list(defs)

    scorer = RelevanceFilter()
    scored = []
    for i, td in enumerate(defs):
        fn = td["function"]
        text = f"{fn['name']} {fn.get('description', '')}"
        scored.append((scorer.score(text, query), i, td))

    scored.sort(key=lambda t: (-t[0], t[1]))
    kept = sorted(scored[: max(1, top_k)], key=lambda t: t[1])
    return [td for _, _, td in kept]


def _exec_list_relevant_tools(arguments: dict) -> dict:
    top_k = int(arguments.get("top_k") or 10)
    all_defs = get_tool_definitions()
    relevant = filter_relevant_tools(arguments["query"], top_k, all_defs)
    return {
        "tools": [td["function"]["name"] for td in relevant],
        "total_available": len(all_defs),
    }


def _exec_mask_old_tool_output(arguments: dict) -> dict:
    from synthelion.output_mask import get_output_mask_store, mask_old_outputs
    keep_last = int(arguments.get("keep_last") if arguments.get("keep_last") is not None else 3)
    store = get_output_mask_store()
    outputs = mask_old_outputs(arguments["outputs"], keep_last, store=store)
    return {"outputs": outputs, "artifact_index": store.render_index()}


def _exec_expand_masked_output(arguments: dict) -> dict:
    from synthelion.output_mask import get_output_mask_store
    return {"output": get_output_mask_store().retrieve(arguments["hash"])}


def _exec_get_artifact_index(arguments: dict) -> dict:
    from synthelion.output_mask import get_output_mask_store
    return {"index": get_output_mask_store().render_index()}


def _exec_rewrite_command(arguments: dict) -> dict:
    from synthelion.command_rewrite import rewrite_command
    command, rewritten = rewrite_command(arguments["command"])
    return {"command": command, "rewritten": rewritten}


def _exec_get_response_style_guidance(arguments: dict) -> dict:
    from synthelion.response_style import get_style_guidance
    guidance = get_style_guidance(arguments.get("level") or "lite", arguments.get("language"))
    return {"guidance": guidance}


def _exec_track_file_read(arguments: dict) -> dict:
    from synthelion.read_lifecycle import get_read_lifecycle_tracker
    tracker = get_read_lifecycle_tracker()
    return tracker.record_read(
        arguments["path"], int(arguments["turn"]), session_id=arguments.get("session_id") or "default",
    )


def _exec_track_file_write(arguments: dict) -> dict:
    from synthelion.read_lifecycle import get_read_lifecycle_tracker
    tracker = get_read_lifecycle_tracker()
    tracker.record_write(arguments["path"], int(arguments["turn"]), session_id=arguments.get("session_id") or "default")
    return {"status": "recorded"}


def _exec_check_read_maturity(arguments: dict) -> dict:
    from synthelion.read_lifecycle import get_read_lifecycle_tracker
    tracker = get_read_lifecycle_tracker()
    path = arguments["path"]
    turn = int(arguments["turn"])
    session_id = arguments.get("session_id") or "default"
    status = tracker.classify(path, session_id=session_id)
    should_mature = tracker.should_mature(path, turn, session_id=session_id)
    marker = tracker.maturation_marker(path, status) if should_mature else None
    return {"status": status, "should_mature": should_mature, "marker": marker}


def _exec_diff_tool_output(arguments: dict) -> dict:
    from synthelion.repeat_diff import get_repeat_differ
    differ = get_repeat_differ()
    output, was_diffed = differ.diff_if_repeated(
        arguments["tool"],
        arguments.get("arguments"),
        arguments["output"],
        session_id=arguments.get("session_id") or "default",
    )
    return {"output": output, "was_diffed": was_diffed}


# ── Privacy / PII detection (Caveman.PrivacyGuard port) ────────────────────────

_privacy_analyzer = None
_privacy_analyzer_lock = threading.Lock()
_privacy_sessions: dict = {}
_privacy_sessions_lock = threading.Lock()


def _get_privacy_analyzer():
    global _privacy_analyzer
    if _privacy_analyzer is None:
        with _privacy_analyzer_lock:
            if _privacy_analyzer is None:
                from synthelion.privacy_analyzer import PrivacyAnalyzer
                _privacy_analyzer = PrivacyAnalyzer()
    return _privacy_analyzer


def _get_or_create_privacy_session(session_id: str | None):
    import uuid

    from synthelion.privacy_session import PrivacySession
    with _privacy_sessions_lock:
        if session_id and session_id in _privacy_sessions:
            return session_id, _privacy_sessions[session_id]
        sid = session_id or str(uuid.uuid4())
        session = PrivacySession()
        _privacy_sessions[sid] = session
        return sid, session


def _exec_analyze_privacy(arguments: dict) -> dict:
    from synthelion.config import privacy_config

    analyzer = _get_privacy_analyzer()
    auto_masking = bool(arguments.get("auto_masking", False))
    session = None
    session_id = None
    if auto_masking:
        session_id, session = _get_or_create_privacy_session(arguments.get("session_id"))

    result = analyzer.analyze(arguments["text"], arguments.get("language") or "en", session=session, auto_masking=auto_masking)

    pcfg = privacy_config()
    blocked = bool(
        pcfg.get("enabled") and pcfg.get("block_on_risk")
        and result.score >= pcfg.get("block_min_score", 61)
    )

    out = {
        "score": result.score,
        "risk_level": result.risk_level,
        "detected_categories": result.detected_categories,
        "compliance_flags": result.compliance_flags,
        "warning_message": result.warning_message,
        "is_safe_for_ai": result.is_safe_for_ai,
        "match_count": result.match_count,
        "matches_per_category": result.matches_per_category,
        "blocked": blocked,
    }
    if auto_masking:
        out["masked_text"] = result.masked_text
        out["session_id"] = session_id
    return out


def _exec_restore_privacy_text(arguments: dict) -> dict:
    with _privacy_sessions_lock:
        session = _privacy_sessions.get(arguments["session_id"])
    if session is None:
        return {"error": f"Unknown session_id: {arguments['session_id']}"}
    return {"text": session.restore(arguments["text"])}


def _exec_check_prompt_injection(arguments: dict) -> dict:
    from synthelion.prompt_injection_guard import PromptInjectionGuard
    result = PromptInjectionGuard().analyze(arguments["text"])
    return {
        "score": result.score,
        "risk_level": result.risk_level,
        "detected_categories": result.detected_categories,
        "is_clean": result.is_clean,
    }


def _exec_get_ai_transparency_notice(arguments: dict) -> dict:
    from synthelion.ai_transparency_notice import get_transparency_notice
    return {"notice": get_transparency_notice(arguments.get("language") or "en", arguments.get("custom_message"))}
