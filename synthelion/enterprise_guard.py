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
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Content detectors
# ---------------------------------------------------------------------------

_SCAN_CAP_BYTES = 64 * 1024  # size of one scan window (kept name for compat)
# Overlap between consecutive windows — must be >= the longest single
# credential a detector can match, so a secret straddling a window boundary is
# never split across two windows and missed. The longest fixed-shape match here
# (Azure/GCP multi-line, ADO/JDBC connection strings, a PEM header line) stays
# well under 1 KiB.
_SCAN_OVERLAP_BYTES = 1024

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


# ---------------------------------------------------------------------------
# Per-client registry — protected-path policies are not one global list.
# Synthelion is typically a shared install (a proxy or MCP server many
# different machines/agents talk to), so "block reading this file" has to be
# answerable per-caller, not just globally. A client is identified by:
#   - IP address — the natural identity for network callers (the proxy,
#     which sees the real connecting IP per request).
#   - MAC address — the natural identity for the *local* machine a CLI/hook
#     invocation runs on (`uuid.getnode()`), useful when the same shared
#     config/registry is consulted by several different physical machines
#     (e.g. synced dotfiles, a central admin policy) rather than each having
#     fully independent config.
# A client's `blocked_paths` are ADDITIVE to the global `blocked_paths`/
# defaults (from `enterprise_guard_config()`) — registering a client only
# ever adds restrictions, never removes the baseline ones.
# ---------------------------------------------------------------------------

_CLIENTS_FILE = "enterprise_guard_clients.json"
_clients_lock = threading.Lock()


def _clients_path(directory: "Path | None" = None) -> "Path":
    d = directory or (Path.home() / ".synthelion")
    d.mkdir(parents=True, exist_ok=True)
    return d / _CLIENTS_FILE


@dataclass
class EnterpriseGuardClient:
    id: str
    label: str = ""
    ip: str = ""
    mac: str = ""
    blocked_paths: "list[str]" = None  # type: ignore[assignment]
    created_at: float = 0.0
    # False for an auto-discovered client (see discover_client) — its own
    # blocked_paths are ignored until an admin reviews and enables it from
    # the dashboard, so a never-seen-before caller never silently gains
    # extra restrictions (or, more importantly, is never silently trusted
    # with anything beyond the baseline global policy) without a human
    # looking at it first. Manually added clients default to enabled.
    enabled: bool = True
    auto_discovered: bool = False

    def __post_init__(self) -> None:
        if self.blocked_paths is None:
            self.blocked_paths = []

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "ip": self.ip, "mac": self.mac,
            "blocked_paths": self.blocked_paths, "created_at": self.created_at,
            "enabled": self.enabled, "auto_discovered": self.auto_discovered,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EnterpriseGuardClient":
        return cls(
            id=d["id"], label=d.get("label", ""), ip=d.get("ip", ""), mac=d.get("mac", ""),
            blocked_paths=list(d.get("blocked_paths", [])), created_at=d.get("created_at", 0.0),
            enabled=d.get("enabled", True), auto_discovered=d.get("auto_discovered", False),
        )


def _load_clients(directory: "Path | None" = None) -> "list[EnterpriseGuardClient]":
    path = _clients_path(directory)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [EnterpriseGuardClient.from_dict(d) for d in data]
    except (OSError, json.JSONDecodeError, KeyError):
        return []


def _save_clients(clients: "list[EnterpriseGuardClient]", directory: "Path | None" = None) -> None:
    import os
    path = _clients_path(directory)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump([c.to_dict() for c in clients], fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def list_clients(directory: "Path | None" = None) -> "list[EnterpriseGuardClient]":
    return _load_clients(directory)


def add_client(
    label: str = "", ip: str = "", mac: str = "", blocked_paths: "list[str] | None" = None,
    enabled: bool = True, directory: "Path | None" = None,
) -> EnterpriseGuardClient:
    import secrets
    ip = (ip or "").strip()
    mac = _normalize_mac(mac)
    with _clients_lock:
        clients = _load_clients(directory)
        client = EnterpriseGuardClient(
            id=secrets.token_hex(6), label=label.strip(), ip=ip, mac=mac,
            blocked_paths=list(blocked_paths or []), created_at=time.time(), enabled=enabled,
        )
        clients.append(client)
        _save_clients(clients, directory)
        return client


def update_client(
    client_id: str, label: "str | None" = None, ip: "str | None" = None, mac: "str | None" = None,
    blocked_paths: "list[str] | None" = None, enabled: "bool | None" = None,
    directory: "Path | None" = None,
) -> "EnterpriseGuardClient | None":
    with _clients_lock:
        clients = _load_clients(directory)
        for c in clients:
            if c.id == client_id:
                if label is not None:
                    c.label = label.strip()
                if ip is not None:
                    c.ip = ip.strip()
                if mac is not None:
                    c.mac = _normalize_mac(mac)
                if blocked_paths is not None:
                    c.blocked_paths = list(blocked_paths)
                if enabled is not None:
                    c.enabled = enabled
                _save_clients(clients, directory)
                return c
    return None


def discover_client(ip: str, directory: "Path | None" = None) -> "EnterpriseGuardClient | None":
    """Idempotent auto-registration for a never-before-seen proxy client IP:
    if a client with this IP already exists (whatever its enabled state),
    returns it unchanged with no write. Otherwise creates a new, disabled
    entry labeled "Auto-discovered" — visible in the dashboard for an admin
    to review, label, assign paths to, and enable, but inert (no extra
    restrictions applied) until they do. Never called for an empty/missing
    IP (nothing to register)."""
    ip = (ip or "").strip()
    if not ip:
        return None
    with _clients_lock:
        clients = _load_clients(directory)
        for c in clients:
            if c.ip == ip:
                return c
        import secrets
        client = EnterpriseGuardClient(
            id=secrets.token_hex(6), label="Auto-discovered", ip=ip,
            created_at=time.time(), enabled=False, auto_discovered=True,
        )
        clients.append(client)
        _save_clients(clients, directory)
        return client


def delete_client(client_id: str, directory: "Path | None" = None) -> None:
    with _clients_lock:
        clients = [c for c in _load_clients(directory) if c.id != client_id]
        _save_clients(clients, directory)


def _normalize_mac(mac: str) -> str:
    mac = (mac or "").strip().lower().replace("-", ":")
    return mac


def local_machine_mac() -> str:
    """This machine's own MAC address, formatted `aa:bb:cc:dd:ee:ff` — the
    default client identity for CLI/hook invocations (which always run
    locally, so there's no network-layer IP to key on the way the proxy
    has). Not cryptographically strong client identity (a MAC can be
    spoofed) — a convenience default for shared-config multi-machine setups,
    not a security boundary on its own."""
    import uuid
    node = uuid.getnode()
    return ":".join(f"{(node >> shift) & 0xFF:02x}" for shift in range(40, -1, -8))


def identify_client(
    ip: "str | None" = None, mac: "str | None" = None, directory: "Path | None" = None,
) -> "EnterpriseGuardClient | None":
    """Resolves an incoming connection to a registered client by exact IP or
    MAC match (IP checked first). Returns None for an unregistered
    caller — callers should fall back to the global default policy, never
    fail closed/open silently on a registry miss."""
    if not ip and not mac:
        return None
    mac_norm = _normalize_mac(mac) if mac else None
    for c in _load_clients(directory):
        if ip and c.ip and c.ip == ip:
            return c
        if mac_norm and c.mac and c.mac == mac_norm:
            return c
    return None


@dataclass(frozen=True)
class GuardResult:
    blocked: bool
    category: str | None = None
    rule_name: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Cross-process recent-blocks log — for dashboard visibility. Synthelion runs
# as a server with many independent processes hitting it concurrently (the
# CLI hook is a fresh process per call, the MCP server is its own long-lived
# stdio process per agent session, the proxy is another process again) — an
# in-memory-only log would only ever show blocks that happened to occur in
# whichever process the dashboard itself is running in. Persisted as a JSONL
# file instead, same pattern as `waf_guard.py`'s event log: atomic per-line
# append (`synthelion/analytics/_atomic_append.py`, safe across concurrent
# processes on both POSIX and Windows — see that module's docstring), no
# in-process lock needed because there's nothing to protect beyond the
# atomic-append primitive itself (consistent with this project's "no
# cross-process locks" rule for shared state).
#
# Deliberately never stores the triggering text/path (only
# category/rule/source/timestamp), so the log itself can't become a place a
# secret ends up persisted.
# ---------------------------------------------------------------------------

_EVENTS_FILE = "enterprise_guard_events.jsonl"
_EVENTS_CAP = 2000


def _events_path(directory: "Path | None" = None) -> "Path":
    # Path.home() re-read every call, not cached — see waf_guard.py/ledger.py's
    # identical comment: a cached constant would defeat tests that monkeypatch
    # Path.home() to an isolated tmp_path.
    d = directory or (Path.home() / ".synthelion")
    d.mkdir(parents=True, exist_ok=True)
    return d / _EVENTS_FILE


def _record_block(result: GuardResult, source: str, directory: "Path | None" = None) -> None:
    from synthelion.analytics._atomic_append import append_line
    event = {
        "timestamp": time.time(),
        "category": result.category,
        "rule_name": result.rule_name,
        "source": source,
    }
    try:
        append_line(_events_path(directory), (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
    except OSError:
        pass


def recent_blocks(limit: int = 100, directory: "Path | None" = None) -> list[dict]:
    path = _events_path(directory)
    if not path.exists():
        return []
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    return events[:limit]


class EnterpriseGuard:
    """Stateless-per-config gate — construct with the effective
    `enterprise_guard.*` config (see `synthelion.config.enterprise_guard_config`)
    and call `check_text`/`check_path`/`check_tool_call`.

    Pass `client_ip` (proxy: the connecting socket's IP) and/or `client_mac`
    (CLI/hook: `local_machine_mac()`) to additionally apply that specific
    client's own `blocked_paths` (see `identify_client`/the client registry
    above) on top of the global defaults/config. Neither given, or the
    client isn't registered, falls back to exactly the global-only policy —
    fully backward compatible with callers that don't know about clients."""

    # Common tool-input argument names across Claude Code/MCP-style tools that
    # carry a filesystem path, checked by check_tool_call.
    _PATH_ARG_NAMES = ("file_path", "path", "notebook_path", "filePath", "target_file")
    # Common tool-input argument names that carry a fetch/webhook destination
    # (WebFetch-shaped tools, HTTP client tools, notification/webhook config),
    # checked by check_tool_call against ssrf_guard.find_ssrf_target.
    _URL_ARG_NAMES = ("url", "uri", "endpoint", "target_url", "webhook_url")

    def __init__(
        self, config: "dict | None" = None, *,
        client_ip: "str | None" = None, client_mac: "str | None" = None,
        client_registry_directory: "Path | None" = None,
    ) -> None:
        from synthelion.config import enterprise_guard_config
        cfg = config if config is not None else enterprise_guard_config()
        self.enabled = cfg.get("enabled", True)
        self.categories = cfg.get("content_categories", {})
        self._blocked_patterns: list[str] = list(cfg.get("blocked_paths", []))
        if cfg.get("use_default_blocked_paths", True):
            self._blocked_patterns.extend(DEFAULT_BLOCKED_PATH_PATTERNS)

        self.client: "EnterpriseGuardClient | None" = None
        if client_ip or client_mac:
            self.client = identify_client(client_ip, client_mac, client_registry_directory)
            # Only an explicitly *enabled* client's extra paths apply — an
            # auto-discovered-but-not-yet-reviewed client (see
            # discover_client) stays inert, never silently more restrictive
            # (or, via absence, less protected) than the global baseline.
            if self.client is not None and self.client.enabled:
                self._blocked_patterns.extend(self.client.blocked_paths)

    # -- content ---------------------------------------------------------

    def check_text(self, text: str, source: str = "unknown") -> GuardResult:
        result = self._check_text(text)
        if result.blocked:
            _record_block(result, source)
        return result

    @staticmethod
    def _scan_windows(text: str) -> "list[str]":
        """Yield overlapping windows covering the *entire* text.

        The previous implementation scanned only ``text[:64KiB]`` — a hard
        truncation that let any credential past the first 64 KiB through
        unscanned, a trivial exfiltration path for an outbound DLP (pad with
        filler, append the secret). Windowing with a fixed overlap scans it all
        while keeping each regex pass bounded.
        """
        n = len(text)
        if n <= _SCAN_CAP_BYTES:
            return [text]
        step = _SCAN_CAP_BYTES - _SCAN_OVERLAP_BYTES
        return [text[i:i + _SCAN_CAP_BYTES] for i in range(0, n, step)]

    def _check_text(self, text: str) -> GuardResult:
        if not self.enabled or not text:
            return GuardResult(blocked=False)
        for window in self._scan_windows(text):
            for detector in CONTENT_DETECTORS:
                if not self.categories.get(detector.category, True):
                    continue
                if detector.pattern.search(window):
                    return GuardResult(
                        blocked=True, category=detector.category, rule_name=detector.name,
                        reason=f"Blocked: detected {detector.name} ({detector.category}) in outbound content.",
                    )
        # Line-oriented, so run once over the full text rather than per window.
        if self.categories.get("dotenv_bulk", True) and _has_dotenv_bulk_secrets(text):
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

    @staticmethod
    def _path_forms(path: str) -> "list[str]":
        """The path spellings to match a zone glob against: the literal string
        as given (backslash-normalized), plus its fully canonicalized real path
        with symlinks, '..', './' and relative segments resolved.

        Matching only the literal string let a symlink pointing into a protected
        zone, or a non-canonical spelling of a path inside it, slip past a glob
        written for the canonical location (e.g. '/tmp/link/f.pdf' or the bare
        relative 'fatture/f.pdf' both evade '**/fatture/**'). Resolving first
        closes that gap; the literal form is kept too so a pattern written
        relative still matches an input that can't be resolved.
        """
        forms: "list[str]" = []
        literal = path.replace("\\", "/")
        forms.append(literal)
        try:
            resolved = Path(path).resolve(strict=False).as_posix()
            if resolved not in forms:
                forms.append(resolved)
        except (OSError, ValueError, RuntimeError):
            pass
        return forms

    def _check_path(self, path: str) -> GuardResult:
        if not self.enabled or not path:
            return GuardResult(blocked=False)
        for form in self._path_forms(path):
            basename = form.rsplit("/", 1)[-1]
            for pattern in self._blocked_patterns:
                if fnmatch.fnmatch(form, pattern) or fnmatch.fnmatch(basename, pattern):
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

        if self.categories.get("ssrf_egress", True):
            for arg_name in self._URL_ARG_NAMES:
                value = tool_input.get(arg_name)
                if isinstance(value, str) and value:
                    from synthelion.ssrf_guard import find_ssrf_target
                    target = find_ssrf_target(value)
                    if target:
                        result = GuardResult(
                            blocked=True, category="ssrf_egress", rule_name=target,
                            reason=f"Blocked: '{arg_name}' targets an SSRF-shaped destination ({target}).",
                        )
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
            if self.categories.get("ssrf_egress", True):
                from synthelion.ssrf_guard import find_ssrf_target
                target = find_ssrf_target(command)
                if target:
                    result = GuardResult(
                        blocked=True, category="ssrf_egress", rule_name=target,
                        reason=f"Blocked: command targets an SSRF-shaped destination ({target}).",
                    )
                    _record_block(result, source)
                    return result
            if self.categories.get("destructive_commands", True):
                from synthelion.safety_guard import find_destructive_command
                matched = find_destructive_command(command)
                if matched:
                    result = GuardResult(
                        blocked=True, category="destructive_commands", rule_name=matched,
                        reason=f"Blocked: command matches a destructive-shell pattern ('{matched}').",
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
