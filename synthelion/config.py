# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""JSON-based configuration for Synthelion: session storage backend, vector-store
backend (for RAG cross-session memory), and dashboard settings.

Config resolution order (first match wins):
  1. `SYNTHELION_CONFIG` env var — an explicit path (useful for mounting a
     per-node/per-container config file, e.g. a Kubernetes ConfigMap volume).
  2. `./synthelion.config.json` — project-local, for per-repo overrides.
  3. `~/.synthelion/config.json` — the user/machine default.
  4. Built-in defaults (single-node, local file storage) if none of the above exist.

Every backend is optional at the code level: choosing "redis"/"postgres"/"qdrant"
only imports that client library when actually selected, so installing Synthelion
without those extras still works for the default local/chromadb setup.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

_VALID_COMPRESSION_LEVELS = ("none", "light", "semantic", "aggressive", "statistical", "syntactic")

_DEFAULT_CONFIG: dict[str, Any] = {
    "compression": {
        "default_level": "semantic",
    },
    "wiki": {
        "default_depth": 2,
    },
    "session_store": {
        # "local" | "redis" | "postgres"
        "backend": "local",
        "local": {"directory": "~/.synthelion"},
        "redis": {"url": "redis://localhost:6379/0", "key_prefix": "synthelion:", "active_ttl_seconds": 300},
        "postgres": {"dsn": "postgresql://synthelion:synthelion@localhost:5432/synthelion"},
    },
    "vector_store": {
        # "chromadb" | "qdrant" | "lexical" (no external service, cosine bag-of-words fallback)
        "backend": "chromadb",
        "chromadb": {"directory": "~/.synthelion/sessions"},
        "qdrant": {"url": "http://localhost:6333", "collection": "synthelion_decisions", "api_key": None},
    },
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8787,
        # "websocket" | "polling"
        "realtime": "websocket",
        "websocket_port": 8788,
    },
    "privacy": {
        # Master switch — set to False to disable PII detection/masking entirely
        # and fall back to today's pre-1.2.2 behavior (no privacy pre-pass at all).
        "enabled": True,
        # Mask detected PII (email, IBAN, national tax/ID numbers, credit cards, ...)
        # with recoverable [PG_n] placeholders before the text is compressed/sent.
        "auto_masking": True,
        # Heuristic screening for prompt-injection/jailbreak attempts.
        "prompt_injection_guard": True,
        "language": "en",
        # EU AI Act Art.50 "you're talking to an AI" disclosure — off by default,
        # since whether/how to show it is an application-level, not library-level,
        # decision.
        "ai_transparency_notice": False,
        # Overrides the built-in localized transparency message when set.
        "transparency_custom_message": "",
        # Exact-match values PrivacyAnalyzer should never flag/mask (e.g. a
        # company support email that's fine to see in plaintext logs).
        "whitelist": [],
        # If True, the UserPromptSubmit hook rejects the prompt outright
        # (hook `decision: "block"`) instead of masking-and-continuing when
        # the detected PII risk score is >= block_min_score. Off by default —
        # masking-and-continue (today's behavior) stays the default so this
        # is an explicit, informed opt-in, same posture as waf.block_mode.
        "block_on_risk": False,
        # Score threshold (0-100, see PrivacyAnalyzer._risk_level) at/above
        # which a prompt is blocked when block_on_risk is on. 61 = "High" or
        # "Critical" risk (score bands: <=15 Safe, <=35 Low, <=60 Medium,
        # <=85 High, >85 Critical).
        "block_min_score": 61,
    },
    "documents": {
        # Master switch for the mask-document CLI/MCP-tool/dashboard endpoint.
        "enabled": True,
        # Reject files larger than this before reading them into memory —
        # DOCX/XLSX in-place masking loads the full object model, so this is
        # the main safety valve against an accidentally huge upload/path.
        "max_file_size_mb": 200,
        # Boundary-safety window for chunked/streaming masking (see
        # synthelion/privacy_stream.py) — must stay >= the longest bounded PII
        # pattern in privacy_rules.yaml (IBAN etc. top out well under 100).
        "chunk_overlap_chars": 512,
        # Masked output naming: "<stem><output_suffix><ext>", e.g.
        # report.docx -> report.masked.docx. The original file is never
        # overwritten.
        "output_suffix": ".masked",
        "supported_formats": ["pdf", "docx", "xlsx", "csv", "md", "txt"],
    },
    "enterprise_guard": {
        # Master switch — outbound data-loss-prevention firewall (cloud/database/
        # FTP/git credentials, private keys, bulk .env dumps, plus user-defined
        # file "security zones" that must never be read into an agent's context).
        # Distinct from `privacy.*` (PII, masked-and-continue) — EnterpriseGuard
        # data has no safe redacted form, so it's always block-or-allow, never mask.
        "enabled": True,
        # Per-category toggles for check_text()'s content detectors.
        "content_categories": {
            "cloud_credentials": True,
            "database_connections": True,
            "ftp_credentials": True,
            "git_credentials": True,
            "private_keys": True,
            "api_tokens": True,
            "dotenv_bulk": True,
        },
        # User-defined fnmatch-style glob patterns (e.g. "**/fatture/**",
        # "**/payroll/*.xlsx") — file paths an agent must never be allowed to
        # read, checked by `synthelion firewall-check` (a PreToolUse hook) and
        # by check_path()/check_tool_call(). Additive to the built-in defaults
        # below unless use_default_blocked_paths is turned off.
        "blocked_paths": [],
        "use_default_blocked_paths": True,
        # Auto-register a never-before-seen proxy client IP into the client
        # registry (disabled, unlabeled) on its first request, so it shows
        # up in the dashboard's EnterpriseGuard > Clients table for an admin
        # to review/label/enable rather than staying invisible until someone
        # manually adds it. The client stays inert (no extra restrictions)
        # until explicitly enabled.
        "auto_discover_clients": True,
    },
    "waf": {
        # Master switch — set to False to disable request inspection entirely.
        "enabled": True,
        # False (default) = detect-only: log matches but never block. Matches the
        # safe default of the original Caveman.Digitalsolutions WAF this was
        # ported from — flipping this on is an explicit, informed opt-in.
        "block_mode": False,
        "rule_sql_injection": True,
        "rule_xss": True,
        "rule_path_traversal": True,
        "rule_command_injection": True,
        "rule_bad_user_agent": True,
        "rule_scanner_probe": True,
        # Inspect small JSON POST bodies too (off by default — most false
        # positives come from legitimate JSON payloads containing SQL-ish or
        # script-ish substrings, e.g. a saved decision note).
        "inspect_body": False,
        # Requests from an already-authenticated dashboard session skip content
        # inspection (trusted operator, avoids false positives on the editor).
        "skip_authenticated": True,
        "auto_ban_enabled": True,
        "auto_ban_threshold": 8,
        "auto_ban_window_minutes": 10,
        "auto_ban_duration_minutes": 120,
        "rate_limit_enabled": True,
        "rate_limit_requests_per_minute": 120,
        "rate_limit_ban_minutes": 15,
        "block_status_code": 403,
        "block_message": "Request blocked by Synthelion firewall.",
        "log_retention_days": 30,
        # Path prefixes exempt from inspection (one per line in the UI).
        "excluded_paths": [],
    },
    "proxy": {
        # Master switch. Off by default — the proxy is an additional, opt-in
        # enforcement layer for agents with no MCP/hook support (Aider, Cursor
        # today); it never replaces or is required by the existing MCP/hook
        # integrations (`synthelion install --agent ...`), which keep working
        # exactly as before whether the proxy is running or not.
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8788,
        # Upstream base URL per wire format, matched by request path prefix
        # (Anthropic: /v1/messages* and /v1/complete*; OpenAI-compatible:
        # /v1/chat/completions, /v1/completions, /v1/responses, /v1/embeddings,
        # /v1/models). Point these at Bedrock/Azure/local gateways to reuse the
        # same routing instead of the public APIs.
        "anthropic_upstream": "https://api.anthropic.com",
        # Also used for any OpenAI-*compatible* provider at the same paths —
        # Groq, OpenRouter, Together, Azure OpenAI, Mistral, DeepSeek, xAI,
        # local vLLM/Ollama-OpenAI-shim servers, etc. all speak this same
        # wire format; point this at theirs instead to route through them.
        "openai_upstream": "https://api.openai.com",
        # Gemini's wire format/paths differ from both of the above
        # (`/v1beta/models/{model}:generateContent`), so it gets its own slot.
        "gemini_upstream": "https://generativelanguage.googleapis.com",
        # Fallback upstream for any other path not matched above — forwarded
        # with the same recursive JSON-string compression/privacy pass (that
        # part is schema-agnostic and works regardless of provider), just
        # without provider-specific path knowledge. Empty = respond 502 for
        # unrecognized paths instead of guessing.
        "default_upstream": "",
        # User-defined overrides, checked before anthropic/openai/gemini/
        # default above (first prefix match wins) — lets a user point at any
        # provider/path models.dev knows about (or a private gateway) without
        # needing a dedicated config slot. Each entry:
        # {"label": str, "path_prefix": str, "upstream": str}. The dashboard's
        # Proxy page can pre-fill `upstream`/`label` from a provider picked out
        # of models.dev's public provider list — an explicit, on-demand fetch
        # (`GET /api/proxy/providers`), never automatic.
        "custom_routes": [],
        # Circuit breaker: if an upstream returns 429 (rate-limited) this many
        # times within the window, the proxy stops forwarding to it for the
        # cooldown period and fails fast (503, no upstream call made) instead
        # of hammering an already-rate-limited provider. Any non-429 success
        # resets that upstream's failure count immediately.
        "circuit_breaker_enabled": True,
        "circuit_breaker_threshold": 3,
        "circuit_breaker_window_seconds": 60,
        "circuit_breaker_cooldown_seconds": 30,
        # Failover pool: if the resolved primary upstream doesn't respond —
        # connection refused, DNS failure, TLS error, timeout, 429, or a 5xx —
        # the proxy retries the *same* request against each of these in
        # order before giving up. Capped at 10 (enforced when read, extras
        # silently dropped) so one misconfigured list can't turn a single
        # request into an unbounded retry storm.
        "fallback_upstreams": [],
        # Per-attempt timeout while trying a candidate upstream (connect +
        # headers). Once a response's headers are being streamed to the
        # client, no further failover happens for that request — bytes
        # already sent can't be taken back.
        "attempt_timeout_seconds": 30,
        # Rolling-history compression: once a `messages` array reaches this
        # many turns, everything except the most recent half is compressed at
        # `aggressive` instead of the configured default level — older turns
        # shrink harder, recent ones stay closer to full detail. Never merges
        # or drops messages (stays valid for every provider's exact schema).
        "rolling_history_enabled": True,
        "rolling_history_threshold": 6,
        # CCR ("compress, cache, retrieve") — reversible compression. When a
        # string's compression saves at least `ccr_min_tokens_saved` tokens,
        # the original is cached (SQLite, ~/.synthelion/ccr.db) for
        # `ccr_ttl_seconds` and a `[ccr:<token>]` marker is appended to the
        # compressed text — an agent that decides it needs the full detail
        # can call the `synthelion_retrieve` MCP tool / `synthelion retrieve`
        # CLI command with that token to get it back. Off by default: it
        # changes the wire text (adds a visible marker), so it's an informed
        # opt-in rather than always-on behavior.
        "ccr_enabled": False,
        "ccr_min_tokens_saved": 15,
        "ccr_ttl_seconds": 3600,
        # Response cache: identical (upstream, path, exact request body)
        # within `response_cache_ttl_seconds` is served from a local cache
        # instead of re-calling the provider. Exact-match only — deliberately
        # not embedding-similarity/"semantic" caching, consistent with
        # Synthelion's zero-ML-models stance; two prompts that mean the same
        # thing but aren't byte-identical are two separate cache entries.
        "response_cache_enabled": False,
        "response_cache_ttl_seconds": 120,
        "response_cache_max_entries": 200,
        # Daily budget cap, in USD, estimated the same way the dashboard's
        # cost-saved KPI is (see ledger.py's per-token price constant). Once
        # the day's estimated spend crosses this, further requests are
        # refused (503) until the next UTC day. 0/absent = no cap.
        "daily_budget_usd": 0,
        # Output shaping: append a short "be terse, don't restate context"
        # instruction to the system prompt — Anthropic (`system` field) and
        # OpenAI-shaped (a `system`/`developer` role message) requests only,
        # since where the system prompt lives is provider-specific; silently
        # skipped for any other shape. Off by default.
        "output_shaping_enabled": False,
    },
    "cluster": {
        # "standalone" (default) | "master" | "slave"
        "role": "standalone",
        "node_id": "",
        # Shared secret for node-to-node calls (join/heartbeat/self-status) —
        # every node in a cluster must carry the same token. Never sent to the
        # browser; the dashboard's Cluster page only ever shows it masked.
        "node_token": "",
        # Slaves only: the master's base URL, e.g. "http://master-host:8787".
        "master_url": "",
        # This node's own reachable URL, reported to the master on join/heartbeat
        # so the master's Cluster page can link to it. Optional for a slave that
        # only the master needs to reach — set it if slaves should reach each
        # other directly too.
        "self_url": "",
    },
}

_VALID_CLUSTER_ROLES = ("standalone", "master", "slave")

# Container-native overrides — set at deploy time via environment variables
# rather than baking a config.json into the image, matching how the Dockerfile
# / docker-compose / k8s manifests configure everything else (SYNTHELION_CONFIG
# already works this way for the config *path*; these are for the cluster
# fields specifically, which are usually per-replica and awkward to template
# into a single shared JSON file).
_CLUSTER_ENV_VARS = {
    "role": "SYNTHELION_ROLE",
    "node_id": "SYNTHELION_NODE_ID",
    "node_token": "SYNTHELION_NODE_TOKEN",
    "master_url": "SYNTHELION_MASTER_URL",
    "self_url": "SYNTHELION_SELF_URL",
}


def _apply_cluster_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    for key, env_var in _CLUSTER_ENV_VARS.items():
        value = os.environ.get(env_var)
        if value:
            config["cluster"][key] = value
    return config


def _expand(value: Any) -> Any:
    """Recursively expands ~ and env vars in string config values (e.g. directory paths)."""
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merges a partial config *override* over a full *base* config, keeping
    every key *override* doesn't touch. Used by the dashboard's Settings panel to
    apply a partial POST body over the currently effective config."""
    return _deep_merge(base, override)


def default_config() -> dict[str, Any]:
    return copy.deepcopy(_DEFAULT_CONFIG)


def config_path() -> Path | None:
    """Returns the config file path that would be used, or None if none exists
    (in which case built-in defaults apply)."""
    env_path = os.environ.get("SYNTHELION_CONFIG")
    if env_path:
        return Path(env_path)
    local = Path.cwd() / "synthelion.config.json"
    if local.exists():
        return local
    home = Path.home() / ".synthelion" / "config.json"
    if home.exists():
        return home
    return None


def default_config_path() -> Path:
    """Where `save_config()` writes when no explicit path is given — the
    user/machine default location, not the project-local override."""
    return Path.home() / ".synthelion" / "config.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Loads config, merging over the built-in defaults so a partial file (e.g. just
    overriding `session_store.backend`) is enough — every other key keeps its default.
    `cluster.*` env vars (see `_CLUSTER_ENV_VARS`), if set, override whatever the
    file or defaults say — applied last, every call, so a container's env is
    always authoritative for its own node identity."""
    target = path or config_path()
    if target is None or not target.exists():
        return _apply_cluster_env_overrides(default_config())

    try:
        with open(target, encoding="utf-8") as fh:
            user_config = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid Synthelion config at {target}: {exc}") from exc

    merged = _deep_merge(_DEFAULT_CONFIG, user_config)
    return _apply_cluster_env_overrides(_expand(merged))


def default_compression_level(config: dict[str, Any] | None = None) -> str:
    """The configured default `--level`/`level=` value for compress/compress_batch
    when the caller doesn't specify one. Falls back to "semantic" if the
    configured value isn't recognized."""
    cfg = config if config is not None else load_config()
    level = cfg.get("compression", {}).get("default_level", "semantic")
    return level if level in _VALID_COMPRESSION_LEVELS else "semantic"


def default_wiki_depth(config: dict[str, Any] | None = None) -> int:
    """The configured default `depth` (1-4) for `synthelion wiki` / the
    `generate_project_wiki` MCP tool when the caller doesn't specify one —
    same "config sets the default, explicit argument always wins" pattern as
    `default_compression_level`. Falls back to 2 if the configured value is
    out of range."""
    cfg = config if config is not None else load_config()
    depth = cfg.get("wiki", {}).get("default_depth", 2)
    return depth if depth in (1, 2, 3, 4) else 2


def privacy_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The effective `privacy.*` settings (PII detection/masking, prompt-injection
    guard, AI transparency notice) — see `_DEFAULT_CONFIG["privacy"]` for defaults.
    `privacy.enabled = False` is the one master switch that disables the whole
    pre-pass, restoring pre-1.2.2 behavior exactly."""
    cfg = config if config is not None else load_config()
    defaults = _DEFAULT_CONFIG["privacy"]
    return {**defaults, **cfg.get("privacy", {})}


def waf_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The effective `waf.*` settings (request inspection, IP allow/block,
    auto-ban, rate limiting) — see `_DEFAULT_CONFIG["waf"]` for defaults.
    `waf.enabled = False` disables the whole gate; `waf.block_mode = False`
    (the default) still inspects and logs but never actually blocks."""
    cfg = config if config is not None else load_config()
    defaults = _DEFAULT_CONFIG["waf"]
    return {**defaults, **cfg.get("waf", {})}


def enterprise_guard_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The effective `enterprise_guard.*` settings (outbound DLP content
    categories, blocked file-path patterns) — see
    `_DEFAULT_CONFIG["enterprise_guard"]` for defaults. `enterprise_guard.enabled
    = False` disables the whole gate (content checks and path checks alike)."""
    cfg = config if config is not None else load_config()
    defaults = _DEFAULT_CONFIG["enterprise_guard"]
    merged = {**defaults, **cfg.get("enterprise_guard", {})}
    merged["content_categories"] = {**defaults["content_categories"], **cfg.get("enterprise_guard", {}).get("content_categories", {})}
    return merged


def documents_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """The effective `documents.*` settings (mask-document file-size cap,
    chunk-overlap window, masked-output naming) — see
    `_DEFAULT_CONFIG["documents"]` for defaults."""
    cfg = config if config is not None else load_config()
    defaults = _DEFAULT_CONFIG["documents"]
    return {**defaults, **cfg.get("documents", {})}


def new_node_id() -> str:
    """A short, human-scannable node identifier: hostname + a short random
    suffix, so two nodes on the same machine (e.g. two containers named the
    same by an orchestrator) still get distinct IDs."""
    import secrets
    import socket
    host = (socket.gethostname() or "node").lower().replace(" ", "-")[:24]
    return f"{host}-{secrets.token_hex(3)}"


def new_cluster_token() -> str:
    """Shared secret nodes present to each other for cluster API calls
    (join/heartbeat/self-status) — generated once by whichever node calls
    `synthelion cluster init` and handed to every node that joins it."""
    import secrets
    return secrets.token_urlsafe(32)


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    try:
        from synthelion.integrations.opencode import install_or_update as _opencode_install
        _opencode_install()
    except Exception:
        pass
    return target
