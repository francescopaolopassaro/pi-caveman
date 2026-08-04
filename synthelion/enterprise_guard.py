# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# (c) 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""EnterpriseGuard — an outbound data-loss-prevention firewall.

Distinct from `PrivacyGuard` (PII: emails, IBANs, tax IDs — data about a
*person*, masked with recoverable placeholders so it can still round-trip)
and `WafEngine` (inbound HTTP request inspection: SQLi/XSS/path-traversal).

EnterpriseGuard is about *categorically secret* enterprise data that should
never reach a model at all — cloud/database/FTP/git credentials, private
keys, bulk `.env` dumps — and, going further than a text scanner can, a
user-defined set of file-path "security zones" that must never be read into
an agent's context in the first place, checked *before* the read happens via
a `PreToolUse`-style hook (`synthelion firewall-check`, mirroring
`loop_guard.py`'s `PersistentLoopGuard` / `_cmd_loop_check`).

Two independent checks, both gated behind `enterprise_guard.enabled`:
  - `check_text(text)` — content-based, for anything about to be sent
    outbound (compress/summarize/proxy-forwarded body/hook prompt). Hard
    block, not mask-and-continue: this data has no "safe redacted form" the
    way a PII placeholder does, so unlike PrivacyGuard the only action is
    block-or-allow.
  - `check_path(path)` / `check_tool_call(tool_name, tool_input)` —
    path-based, for a PreToolUse hook to veto a `Read`/`Bash`/`Grep`/`Glob`
    tool call before it ever executes, against a set of blocked path
    patterns (glob-style, plus a small set of high-value defaults:
    `.env`, `.git/config`, private-key files, cloud-credentials files).

Zero ML, zero network calls — same regex/glob-only philosophy as the rest of
Synthelion.
"""
from __future__ import annotations

import fnmatch
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Content detectors
# ---------------------------------------------------------------------------

_SCAN_CAP_BYTES = 64 * 1024

# Shared with sensitive_guard.py's shapes (private keys, AWS keys, GitHub/Slack
# tokens, generic API secret keys, Bearer headers, dotenv bulk dumps) plus new
# categories this module adds: Azure, GCP, FTP, database connection strings,
# and credentials embedded in a git remote URL.
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN[\s\S]*?PRIVATE KEY-----")
_AWS_ACCESS_KEY_RE = re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16,}(?![A-Z0-9])")
_AWS_SECRET_LINE_RE = re.compile(
    r"(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*=\s*[A-Za-z0-9/+=]{30,}"
)
_GITHUB_TOKEN_RE = re.compile(r"\bghp_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")
_SLACK_TOKEN_RE = re.compile(r"\bxox[bp]-[A-Za-z0-9-]{10,}\b")
_API_SECRET_KEY_RE = re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9]{20,}\b")
_BEARER_TOKEN_RE = re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{20,}")
_DOTENV_MARKERS = ("SECRET", "TOKEN", "PASSWORD", "APIKEY", "API_KEY")
_DOTENV_LINE_RE = re.compile(r"^([A-Z0-9_]+)=(.+)$")

_AZURE_CONNECTION_STRING_RE = re.compile(
    r"DefaultEndpointsProtocol=[^;]+;AccountName=[^;]+;AccountKey=[A-Za-z0-9+/=]{20,}", re.IGNORECASE
)
_AZURE_SAS_TOKEN_RE = re.compile(r"[?&]sig=[A-Za-z0-9%]{20,}")
_GCP_SERVICE_ACCOUNT_RE = re.compile(
    r'"type"\s*:\s*"service_account"[\s\S]{0,400}"private_key"\s*:\s*"-----BEGIN'
)
_FTP_CREDENTIALS_URL_RE = re.compile(r"\b(?:ftp|sftp)://[^\s:@/]+:[^\s@/]+@[^\s/]+", re.IGNORECASE)
_DB_CONNECTION_STRING_URL_RE = re.compile(
    r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|rediss|mssql|oracle)://[^\s:@/]+:[^\s@/]+@[^\s/]+",
    re.IGNORECASE,
)
_DB_ADO_CONNECTION_STRING_RE = re.compile(
    r"(?:Server|Data Source)\s*=\s*[^;]+;[^;]*;?\s*(?:User Id|Uid|User)\s*=\s*[^;]+;\s*Password\s*=\s*[^;]+",
    re.IGNORECASE,
)
_JDBC_CONNECTION_STRING_RE = re.compile(r"\bjdbc:[a-z0-9]+://[^\s'\"]+", re.IGNORECASE)
_GIT_CREDENTIALS_URL_RE = re.compile(
    r"\bhttps?://[^\s:@/]+:[^\s@/]+@(?:github\.com|gitlab\.com|bitbucket\.org|[a-z0-9.-]+\.git[a-z]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ContentDetector:
    category: str
    name: str
    pattern: re.Pattern


CONTENT_DETECTORS: tuple[ContentDetector, ...] = (
    ContentDetector("private_keys", "PEM private key block", _PRIVATE_KEY_RE),
    ContentDetector("cloud_credentials", "AWS access key", _AWS_ACCESS_KEY_RE),
    ContentDetector("cloud_credentials", "AWS secret access key", _AWS_SECRET_LINE_RE),
    ContentDetector("cloud_credentials", "Azure storage connection string", _AZURE_CONNECTION_STRING_RE),
    ContentDetector("cloud_credentials", "Azure SAS token", _AZURE_SAS_TOKEN_RE),
    ContentDetector("cloud_credentials", "GCP service account key", _GCP_SERVICE_ACCOUNT_RE),
    ContentDetector("ftp_credentials", "FTP/SFTP URL with embedded credentials", _FTP_CREDENTIALS_URL_RE),
    ContentDetector("database_connections", "Database connection URL with embedded credentials", _DB_CONNECTION_STRING_URL_RE),
    ContentDetector("database_connections", "ADO.NET connection string", _DB_ADO_CONNECTION_STRING_RE),
    ContentDetector("database_connections", "JDBC connection string", _JDBC_CONNECTION_STRING_RE),
    ContentDetector("git_credentials", "Git remote URL with embedded credentials", _GIT_CREDENTIALS_URL_RE),
    ContentDetector("api_tokens", "GitHub token", _GITHUB_TOKEN_RE),
    ContentDetector("api_tokens", "Slack token", _SLACK_TOKEN_RE),
    ContentDetector("api_tokens", "Generic API secret key", _API_SECRET_KEY_RE),
    ContentDetector("api_tokens", "Bearer token", _BEARER_TOKEN_RE),
)


def _has_dotenv_bulk_secrets(text: str) -> bool:
    count = 0
    for line in text.splitlines():
        m = _DOTENV_LINE_RE.match(line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if not value:
            continue
        if any(marker in key for marker in _DOTENV_MARKERS):
            count += 1
            if count >= 3:
                return True
    return False


# ---------------------------------------------------------------------------
# Default blocked path patterns ("security zones") — fnmatch-style globs,
# matched against both the full path and its basename so a relative or
# absolute path both work. Purely additive to whatever the user configures.
# ---------------------------------------------------------------------------

DEFAULT_BLOCKED_PATH_PATTERNS: tuple[str, ...] = (
    "*.env", "*.env.*", "**/.env", "**/.env.*",
    "**/.git/config", "**/.git/credentials",
    "**/.aws/credentials", "**/.aws/config",
    "**/.azure/**",
    "**/.ssh/id_rsa", "**/.ssh/id_ed25519", "**/.ssh/*.pem",
    "*.pem", "*.pfx", "*.p12", "*.key",
    "**/credentials.json", "**/service-account*.json",
    "**/secrets.yaml", "**/secrets.yml", "**/secrets.json",
    "**/.npmrc", "**/.pypirc", "**/.netrc",
    "**/*kubeconfig*",
)


@dataclass(frozen=True)
class GuardResult:
    blocked: bool
    category: str | None = None
    rule_name: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# In-memory recent-blocks log — for dashboard visibility only. Deliberately
# never stores the triggering text/path (only category/rule/source/time), so
# it can't itself become a place a secret ends up persisted. Process-lifetime
# only, not written to disk — same reasoning as other in-process-only state
# in this project (e.g. dashboard session tokens), and appropriate here since
# this is operational visibility, not a compliance audit trail.
# ---------------------------------------------------------------------------

_EVENTS_CAP = 500
_events_lock = threading.Lock()
_recent_blocks: "deque[dict]" = deque(maxlen=_EVENTS_CAP)


def _record_block(result: GuardResult, source: str) -> None:
    with _events_lock:
        _recent_blocks.appendleft({
            "timestamp": time.time(),
            "category": result.category,
            "rule_name": result.rule_name,
            "source": source,
        })


def recent_blocks(limit: int = 100) -> list[dict]:
    with _events_lock:
        return list(_recent_blocks)[:limit]


class EnterpriseGuard:
    """Stateless-per-config gate — construct with the effective
    `enterprise_guard.*` config (see `synthelion.config.enterprise_guard_config`)
    and call `check_text`/`check_path`/`check_tool_call`."""

    # Common tool-input argument names across Claude Code/MCP-style tools that
    # carry a filesystem path, checked by check_tool_call.
    _PATH_ARG_NAMES = ("file_path", "path", "notebook_path", "filePath", "target_file")

    def __init__(self, config: "dict | None" = None) -> None:
        from synthelion.config import enterprise_guard_config
        cfg = config if config is not None else enterprise_guard_config()
        self.enabled = cfg.get("enabled", True)
        self.categories = cfg.get("content_categories", {})
        self._blocked_patterns: list[str] = list(cfg.get("blocked_paths", []))
        if cfg.get("use_default_blocked_paths", True):
            self._blocked_patterns.extend(DEFAULT_BLOCKED_PATH_PATTERNS)

    # -- content ---------------------------------------------------------

    def check_text(self, text: str, source: str = "unknown") -> GuardResult:
        result = self._check_text(text)
        if result.blocked:
            _record_block(result, source)
        return result

    def _check_text(self, text: str) -> GuardResult:
        if not self.enabled or not text:
            return GuardResult(blocked=False)
        scan = text[:_SCAN_CAP_BYTES]
        for detector in CONTENT_DETECTORS:
            if not self.categories.get(detector.category, True):
                continue
            if detector.pattern.search(scan):
                return GuardResult(
                    blocked=True, category=detector.category, rule_name=detector.name,
                    reason=f"Blocked: detected {detector.name} ({detector.category}) in outbound content.",
                )
        if self.categories.get("dotenv_bulk", True) and _has_dotenv_bulk_secrets(scan):
            return GuardResult(
                blocked=True, category="dotenv_bulk", rule_name="Bulk .env-style secret dump",
                reason="Blocked: detected a bulk .env-style secret dump in outbound content.",
            )
        return GuardResult(blocked=False)

    # -- filesystem paths --------------------------------------------------

    def check_path(self, path: str, source: str = "unknown") -> GuardResult:
        result = self._check_path(path)
        if result.blocked:
            _record_block(result, source)
        return result

    def _check_path(self, path: str) -> GuardResult:
        if not self.enabled or not path:
            return GuardResult(blocked=False)
        normalized = path.replace("\\", "/")
        basename = normalized.rsplit("/", 1)[-1]
        for pattern in self._blocked_patterns:
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(basename, pattern):
                return GuardResult(
                    blocked=True, category="blocked_path", rule_name=pattern,
                    reason=f"Blocked: '{path}' matches a protected security-zone pattern ('{pattern}').",
                )
        return GuardResult(blocked=False)

    def check_tool_call(self, tool_name: str, tool_input: "dict", source: str = "firewall-check") -> GuardResult:
        """PreToolUse-style check: inspects a tool call's arguments for a
        blocked file path (Read/Edit/Write/Glob-shaped tools) or, for a shell
        tool (Bash-shaped), scans the raw command string for both blocked
        path patterns and blocked content patterns (a command can embed a
        credential inline, e.g. `export AWS_SECRET_ACCESS_KEY=...`)."""
        if not self.enabled:
            return GuardResult(blocked=False)

        for arg_name in self._PATH_ARG_NAMES:
            value = tool_input.get(arg_name)
            if isinstance(value, str) and value:
                result = self._check_path(value)
                if result.blocked:
                    _record_block(result, source)
                    return result

        command = tool_input.get("command")
        if isinstance(command, str) and command:
            normalized = command.replace("\\", "/")
            for pattern in self._blocked_patterns:
                # Bash-safe: only match a pattern that appears as a path-like
                # substring, not a bare basename glob like "*.pem" matching
                # unrelated prose — require at least one '/' or a leading dot
                # in the pattern before applying it to free-form command text.
                if ("/" not in pattern and not pattern.startswith(("*.", "**"))):
                    continue
                for token in re.split(r"\s+", normalized):
                    token = token.strip("'\"")
                    if fnmatch.fnmatch(token, pattern) or fnmatch.fnmatch(token.rsplit("/", 1)[-1], pattern):
                        result = GuardResult(
                            blocked=True, category="blocked_path", rule_name=pattern,
                            reason=f"Blocked: command references a protected security-zone path (matches '{pattern}').",
                        )
                        _record_block(result, source)
                        return result
            content_result = self._check_text(command)
            if content_result.blocked:
                _record_block(content_result, source)
                return content_result

        text = tool_input.get("text") or tool_input.get("content")
        if isinstance(text, str) and text:
            content_result = self._check_text(text)
            if content_result.blocked:
                _record_block(content_result, source)
                return content_result

        return GuardResult(blocked=False)


class EnterpriseGuardBlockedError(Exception):
    """Raised by enforced entry points (compress/summarize/proxy) when
    EnterpriseGuard blocks outbound content. Callers surface `result.reason`."""

    def __init__(self, result: GuardResult) -> None:
        super().__init__(result.reason)
        self.result = result
