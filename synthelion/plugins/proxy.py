# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Local reverse proxy: real, enforced privacy/compression for any agent that
respects a custom API base URL — no MCP or hook support required on the
client side.

This exists specifically to close the gap the MCP/Rule integrations (Cursor,
Aider, Codex CLI — see `synthelion install`) cannot: those only work if the
model *chooses* to call a tool. Point an agent's `ANTHROPIC_BASE_URL` /
`OPENAI_BASE_URL` at this proxy instead, and every request gets the same
privacy pre-pass + compression Synthelion applies everywhere else, enforced
server-side before the request ever reaches the real provider — a risky
prompt can be blocked outright (HTTP 400, request never forwarded), same
posture as Claude Code's `block_on_risk` hook.

This is an *additional*, strictly opt-in layer (`proxy.enabled` defaults to
False) — it does not replace, require, or change the behavior of the
MCP/hook integrations in `synthelion/integrations/`; both can run
independently or side by side.

Routing is by request path prefix, not by inspecting a specific provider's
full schema, so unknown JSON shapes still get *some* protection: any string
value found anywhere in the JSON body above a minimum length is run through
the same privacy/compression pipeline `synthelion compress` uses, recursively,
regardless of which field it's nested under. This is deliberately schema-
agnostic — it is why the proxy works for "any provider and wire format"
rather than only the two hardcoded upstream routes below.
"""
from __future__ import annotations

import http.client
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MIN_LEN = 20  # chars — shorter strings (IDs, model names, flags) skip the pipeline entirely
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host"}
# Response-only exclusions: BaseHTTPRequestHandler.send_response() always
# injects its own fresh Server/Date headers, so forwarding the upstream's
# copies of those (or replaying them from a cached entry) would duplicate
# both — every response header pass-through uses this superset.
_RESPONSE_STRIP = _HOP_BY_HOP | {"server", "date"}


class _Blocked(Exception):
    def __init__(self, notice: str) -> None:
        self.notice = notice


class _CcrStore:
    """Thin, per-request config wrapper around the persistent CCR store
    (synthelion/analytics/ccr_store.py) — holds this call's enabled/min-
    savings/TTL settings so `_process_text` doesn't need to re-read config."""

    def __init__(self, cfg: dict) -> None:
        self.enabled = bool(cfg.get("ccr_enabled", False))
        self.min_tokens_saved = int(cfg.get("ccr_min_tokens_saved", 15))
        self.ttl_seconds = float(cfg.get("ccr_ttl_seconds", 3600))

    def store(self, text: str) -> str:
        from synthelion.analytics.ccr_store import get_ccr_store
        return get_ccr_store().store(text, ttl_seconds=self.ttl_seconds)


class _CircuitBreaker:
    """Per-upstream failure tracking, shared across all request threads.

    Trips (opens) an upstream after `circuit_breaker_threshold` 429 responses
    land within `circuit_breaker_window_seconds`, and stays open for
    `circuit_breaker_cooldown_seconds` — during that window every request for
    that upstream fails fast (503, no network call to the already-rate-
    limited provider) instead of adding to the pile-up. Any non-429 response
    clears that upstream's failure history immediately.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._open_until: dict[str, float] = {}

    def seconds_until_closed(self, upstream: str) -> float:
        with self._lock:
            remaining = self._open_until.get(upstream, 0.0) - time.time()
        return remaining if remaining > 0 else 0.0

    def record_success(self, upstream: str) -> None:
        with self._lock:
            self._failures.pop(upstream, None)

    def record_rate_limit(self, upstream: str, cfg: dict) -> None:
        if not cfg.get("circuit_breaker_enabled", True):
            return
        threshold = int(cfg.get("circuit_breaker_threshold", 3))
        window = float(cfg.get("circuit_breaker_window_seconds", 60))
        cooldown = float(cfg.get("circuit_breaker_cooldown_seconds", 30))
        now = time.time()
        with self._lock:
            hist = self._failures.setdefault(upstream, [])
            hist.append(now)
            cutoff = now - window
            hist[:] = [t for t in hist if t >= cutoff]
            if len(hist) >= threshold:
                self._open_until[upstream] = now + cooldown
                hist.clear()

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._open_until.clear()


_circuit_breaker = _CircuitBreaker()


class _ResponseCache:
    """Exact-match request/response cache, process-local. Not semantic/
    embedding-based — see the `response_cache_enabled` config comment for why
    that's a deliberate choice, not a missing feature."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, int, list[tuple[str, str]], bytes]] = {}

    @staticmethod
    def _key(upstream: str, path: str, body: bytes) -> str:
        import hashlib
        return hashlib.sha256(upstream.encode() + b"\x00" + path.encode() + b"\x00" + body).hexdigest()

    def get(self, upstream: str, path: str, body: bytes, ttl: float):
        key = self._key(upstream, path, body)
        with self._lock:
            entry = self._entries.get(key)
        if not entry:
            return None
        ts, status, headers, payload = entry
        if time.time() - ts > ttl:
            return None
        return status, headers, payload

    def put(self, upstream: str, path: str, body: bytes, status: int, headers: list[tuple[str, str]], payload: bytes, max_entries: int) -> None:
        key = self._key(upstream, path, body)
        with self._lock:
            self._entries[key] = (time.time(), status, headers, payload)
            if len(self._entries) > max_entries:
                oldest = sorted(self._entries.items(), key=lambda kv: kv[1][0])[: max(1, len(self._entries) - max_entries)]
                for k, _ in oldest:
                    self._entries.pop(k, None)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


_response_cache = _ResponseCache()


class _BudgetTracker:
    """Per-day estimated-cost cap, process-local. Uses the same per-token
    price estimate the dashboard's cost-saved KPI does (ledger.py)."""

    _PRICE_PER_TOKEN = 3e-6  # kept in sync with analytics/ledger.py's _DEFAULT_PRICE_PER_TOKEN

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day = ""
        self._spent = 0.0

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def remaining(self, daily_budget_usd: float) -> float | None:
        if not daily_budget_usd:
            return None
        with self._lock:
            if self._day != self._today():
                self._day = self._today()
                self._spent = 0.0
            return daily_budget_usd - self._spent

    def record_spend(self, tokens: int) -> None:
        with self._lock:
            today = self._today()
            if self._day != today:
                self._day = today
                self._spent = 0.0
            self._spent += tokens * self._PRICE_PER_TOKEN


_budget_tracker = _BudgetTracker()


_LEVEL_MAP = None  # populated lazily below to avoid importing models.py at module load


def _level_map() -> dict:
    global _LEVEL_MAP
    if _LEVEL_MAP is None:
        from synthelion.models import CompressionLevel
        _LEVEL_MAP = {
            "none": CompressionLevel.NONE, "light": CompressionLevel.LIGHT,
            "semantic": CompressionLevel.SEMANTIC, "aggressive": CompressionLevel.AGGRESSIVE,
            "statistical": CompressionLevel.STATISTICAL, "syntactic": CompressionLevel.SYNTACTIC,
        }
    return _LEVEL_MAP


def _process_text(
    text: str, pcfg: dict, level_override: str | None = None, ccr: "_CcrStore | None" = None,
    client_ip: str | None = None,
) -> tuple[str, int, int]:
    """Runs the privacy pre-pass + compression on one string. Raises _Blocked
    if the risk score crosses `privacy.block_min_score` and `block_on_risk`
    is on. Returns (possibly masked+compressed text, tokens_before, tokens_after).

    `level_override` lets a caller ask for heavier compression than the
    configured default for this one string — used for the rolling-history
    pass below, where older turns are compressed harder than recent ones."""
    if len(text) < MIN_LEN:
        return text, 0, 0

    original_text = text
    if pcfg.get("enabled", True):
        from synthelion.privacy_analyzer import PrivacyAnalyzer, build_privacy_notice
        from synthelion.privacy_session import PrivacySession

        analyzer = PrivacyAnalyzer()
        if pcfg.get("whitelist"):
            analyzer.add_to_whitelist(*pcfg["whitelist"])
        session = PrivacySession() if pcfg.get("auto_masking") else None
        result = analyzer.analyze(text, pcfg.get("language", "en"), session=session, auto_masking=pcfg.get("auto_masking"))

        if pcfg.get("block_on_risk") and result.score >= pcfg.get("block_min_score", 61):
            raise _Blocked(build_privacy_notice(result, None, blocked=True))

        if pcfg.get("auto_masking") and result.masked_text and result.match_count > 0:
            text = result.masked_text

    # EnterpriseGuard: outbound DLP, independent of the PII pre-pass above —
    # credential-shaped content has no safe redacted form, always block-or-allow.
    # Checked against the ORIGINAL (pre-privacy-masking) text — see cli.py's
    # identical comment for why (masking can break a secret's regex shape).
    # client_ip identifies the connecting caller against the client registry
    # (see enterprise_guard.py) — today this only affects the recorded
    # block's audit source ("proxy:<ip>"), since check_text itself isn't yet
    # per-client-policy-aware (only check_path/check_tool_call are); wiring
    # the real client IP through now means that's a config change, not a
    # code change, if/when per-client content policy is added.
    from synthelion.enterprise_guard import EnterpriseGuard
    guard = EnterpriseGuard(client_ip=client_ip)
    source = f"proxy:{client_ip}" if client_ip else "proxy"
    eg_result = guard.check_text(original_text, source=source)
    if eg_result.blocked:
        raise _Blocked(eg_result.reason)

    from synthelion.config import default_compression_level
    from synthelion.core import CompressionService
    from synthelion.global_idf_provider import GlobalIdfProvider

    level_name = level_override or default_compression_level()
    svc = CompressionService(global_idf=GlobalIdfProvider())
    r = svc.compress(text, _level_map().get(level_name, _level_map()["semantic"]))
    compressed = r.compressed_text or text

    if ccr is not None and ccr.enabled and r.original_tokens - r.compressed_tokens >= ccr.min_tokens_saved:
        token = ccr.store(original_text)
        compressed = f"{compressed} [ccr:{token}]"

    return compressed, r.original_tokens, r.compressed_tokens


def _walk(obj, pcfg: dict, totals: dict, level_override: str | None = None, ccr: "_CcrStore | None" = None):
    """Recursively compresses every string value in a JSON structure in place."""
    if isinstance(obj, str):
        new_text, before, after = _process_text(obj, pcfg, level_override=level_override, ccr=ccr)
        totals["before"] += before
        totals["after"] += after
        return new_text
    if isinstance(obj, dict):
        return {k: _walk(v, pcfg, totals, level_override, ccr) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, pcfg, totals, level_override, ccr) for v in obj]
    return obj


def _walk_body(body: dict, pcfg: dict, proxy_cfg: dict, totals: dict, ccr: "_CcrStore | None" = None) -> dict:
    """Top-level walk with rolling-history awareness: if `messages` is a list
    of 6+ turns (Anthropic/OpenAI-shaped request bodies both use this key),
    turns outside the last `keep` are compressed at `aggressive` instead of
    the configured default — the same "recent turns stay intact, older turns
    shrink harder" idea Headroom's proxy uses, adapted to never restructure
    or merge messages (so it stays safe for every provider's exact schema —
    unlike collapsing old turns into one synthetic message, which is safe for
    OpenAI's `system`-role messages but not valid in Anthropic's `messages`
    array, where the system prompt is a separate top-level field)."""
    if not proxy_cfg.get("rolling_history_enabled", True):
        return _walk(body, pcfg, totals, ccr=ccr)

    messages = body.get("messages")
    threshold = int(proxy_cfg.get("rolling_history_threshold", 6))
    if not isinstance(messages, list) or len(messages) < threshold:
        return _walk(body, pcfg, totals, ccr=ccr)

    keep = max(2, len(messages) // 2)
    old, recent = messages[:-keep], messages[-keep:]
    new_messages = (
        [_walk(m, pcfg, totals, level_override="aggressive", ccr=ccr) for m in old]
        + [_walk(m, pcfg, totals, ccr=ccr) for m in recent]
    )
    result = {k: (_walk(v, pcfg, totals, ccr=ccr) if k != "messages" else None) for k, v in body.items()}
    result["messages"] = new_messages
    return result


_OUTPUT_SHAPING_NOTE = (
    "\n\nBe terse: don't restate context, don't re-print code you were just "
    "shown, skip preamble like \"Great, let's...\". Answer directly."
)


def _apply_output_shaping(body: dict) -> dict:
    """Appends a short terseness instruction to the system prompt — Anthropic
    (`system` field, string or list-of-blocks) and OpenAI-shaped (a
    system/developer role message in `messages`) requests only, since where
    the system prompt lives is provider-specific. Silently a no-op for any
    other shape rather than guessing. Placed at the very end of the existing
    system text so it doesn't disturb prompt-cache-stable prefixes."""
    if isinstance(body.get("system"), str):
        body = {**body, "system": body["system"] + _OUTPUT_SHAPING_NOTE}
    elif isinstance(body.get("system"), list):
        blocks = list(body["system"])
        if blocks and isinstance(blocks[-1], dict) and isinstance(blocks[-1].get("text"), str):
            blocks[-1] = {**blocks[-1], "text": blocks[-1]["text"] + _OUTPUT_SHAPING_NOTE}
            body = {**body, "system": blocks}
    elif isinstance(body.get("messages"), list):
        messages = list(body["messages"])
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if isinstance(m, dict) and m.get("role") in ("system", "developer") and isinstance(m.get("content"), str):
                messages[i] = {**m, "content": m["content"] + _OUTPUT_SHAPING_NOTE}
                body = {**body, "messages": messages}
                break
    return body


def _resolve_upstream(path: str, cfg: dict) -> str | None:
    # User-defined routes win first (first prefix match, in the order the
    # user listed them) — lets someone point an arbitrary path at an
    # arbitrary provider/gateway without needing a dedicated config slot.
    for route in cfg.get("custom_routes") or []:
        prefix = (route.get("path_prefix") or "").strip()
        upstream = (route.get("upstream") or "").strip()
        if prefix and upstream and path.startswith(prefix):
            return upstream

    anthropic_paths = ("/v1/messages", "/v1/complete")
    # OpenAI wire format — also matches Groq/OpenRouter/Together/Azure OpenAI/
    # Mistral/DeepSeek/xAI/vLLM-OpenAI-shim/etc., all of which serve the same
    # paths; `openai_upstream` just needs to point at whichever of those the
    # agent is actually meant to reach.
    openai_paths = ("/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/embeddings", "/v1/models")
    gemini_paths = ("/v1beta/models", "/v1/models:generateContent")
    if any(path.startswith(p) for p in anthropic_paths):
        return cfg.get("anthropic_upstream") or "https://api.anthropic.com"
    if any(path.startswith(p) for p in openai_paths):
        return cfg.get("openai_upstream") or "https://api.openai.com"
    if any(path.startswith(p) for p in gemini_paths):
        return cfg.get("gemini_upstream") or "https://generativelanguage.googleapis.com"
    return cfg.get("default_upstream") or None


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    proxy_cfg: dict = {}
    stats_lock = threading.Lock()
    stats = {"requests": 0, "blocked": 0, "tokens_before": 0, "tokens_after": 0, "errors": 0, "started_at": 0.0}

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802 - stdlib override
        pass  # keep stdout clean; the dashboard reads /stats instead

    def _waf_gate(self, body: bytes, cfg: dict) -> bool:
        """Same firewall the dashboard sits behind (synthelion/waf_guard.py) —
        the proxy is a second internet-facing surface (agents point their API
        base URL at it) and must not be exempt from IP/path/body inspection,
        rate limiting, and auto-ban just because it isn't the dashboard."""
        from synthelion.waf_guard import get_waf_engine

        if not cfg.get("enabled", True):
            return True
        excluded = cfg.get("excluded_paths") or []
        if any(self.path.startswith(p.strip()) for p in excluded if p and p.strip()):
            return True

        ip = self.client_address[0] if self.client_address else ""
        ua = self.headers.get("User-Agent", "")
        query = urlparse(self.path).query
        body_text = body.decode("utf-8", errors="ignore") if cfg.get("inspect_body") else ""
        decision = get_waf_engine().gate(ip, self.command, self.path, query, ua, body_text, cfg)
        if not decision.allowed:
            self._waf_block(cfg)
            return False
        return True

    def _waf_block(self, cfg: dict) -> None:
        message = (cfg.get("block_message") or "Request blocked by Synthelion firewall.").encode("utf-8")
        self.send_response(int(cfg.get("block_status_code") or 403))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(message)))
        self.end_headers()
        try:
            self.wfile.write(message)
        except OSError:
            pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/health", "/stats"):
            from synthelion.config import waf_config
            if not self._waf_gate(b"", waf_config()):
                return
            data = {"status": "ok", **self._stats_snapshot()} if self.path == "/health" else self._stats_snapshot()
            self._serve_json(200, data)
            return
        self._handle_and_forward()

    def do_POST(self) -> None:  # noqa: N802
        self._handle_and_forward()

    def do_PUT(self) -> None:  # noqa: N802
        self._handle_and_forward()

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle_and_forward()

    def _handle_and_forward(self) -> None:
        from synthelion.config import waf_config
        body = self._read_body()
        cfg = waf_config()
        if not self._waf_gate(body, cfg):
            return
        self._forward(body)

    def _stats_snapshot(self) -> dict:
        with self.stats_lock:
            s = dict(self.stats)
        s["uptime_seconds"] = round(time.time() - s["started_at"], 1) if s["started_at"] else 0
        return s

    def _serve_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _forward(self, raw_body: bytes) -> None:
        start = time.perf_counter()
        cfg = self.proxy_cfg
        client_ip = self.client_address[0] if self.client_address else ""

        if client_ip:
            from synthelion.config import enterprise_guard_config
            if enterprise_guard_config().get("auto_discover_clients", True):
                # Auto-register a never-before-seen client IP, disabled by
                # default — an admin reviews/labels/enables it from the
                # dashboard's EnterpriseGuard > Clients table. Idempotent
                # and cheap (a small local JSON file, no network I/O) —
                # no-ops instantly for an already-known IP.
                from synthelion.enterprise_guard import discover_client
                try:
                    discover_client(client_ip)
                except OSError:
                    pass

        upstream = _resolve_upstream(self.path, cfg)
        if not upstream:
            self._serve_json(502, {"error": f"no upstream configured for path {self.path}"})
            self._log_call(upstream="", status_code=502, start=start, responded=False, error="no upstream configured", client_ip=client_ip)
            return

        budget = cfg.get("daily_budget_usd") or 0
        if budget:
            remaining = _budget_tracker.remaining(budget)
            if remaining is not None and remaining <= 0:
                self._serve_json(503, {"error": f"daily budget of ${budget} reached — resets at UTC midnight"})
                self._log_call(upstream=upstream, status_code=503, start=start, responded=False, error="daily budget exceeded", client_ip=client_ip)
                return

        cache_enabled = bool(cfg.get("response_cache_enabled", False))
        if cache_enabled:
            cached = _response_cache.get(upstream, self.path, raw_body, float(cfg.get("response_cache_ttl_seconds", 120)))
            if cached is not None:
                status, headers, payload = cached
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("X-Synthelion-Cache", "hit")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    pass
                self._log_call(upstream=upstream, status_code=status, start=start, responded=True, client_ip=client_ip)
                return

        with self.stats_lock:
            self.stats["requests"] += 1

        body_to_send = raw_body
        content_type = self.headers.get("Content-Type", "")
        tokens_before = tokens_after = 0
        if raw_body and "application/json" in content_type:
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                from synthelion.config import privacy_config
                pcfg = privacy_config()
                totals = {"before": 0, "after": 0}
                ccr = _CcrStore(cfg)
                if cfg.get("output_shaping_enabled") and isinstance(parsed, dict):
                    parsed = _apply_output_shaping(parsed)
                try:
                    parsed = _walk_body(parsed, pcfg, cfg, totals, ccr) if isinstance(parsed, dict) else _walk(parsed, pcfg, totals, ccr=ccr)
                except _Blocked as exc:
                    with self.stats_lock:
                        self.stats["blocked"] += 1
                    self._serve_json(400, {
                        "error": {"type": "synthelion_privacy_block", "message": exc.notice},
                    })
                    self._record_ledger(0, 0, blocked=True)
                    self._log_call(upstream=upstream, status_code=400, start=start, responded=True, blocked=True, client_ip=client_ip)
                    return
                body_to_send = json.dumps(parsed).encode("utf-8")
                tokens_before, tokens_after = totals["before"], totals["after"]
                with self.stats_lock:
                    self.stats["tokens_before"] += tokens_before
                    self.stats["tokens_after"] += tokens_after
                self._record_ledger(tokens_before, tokens_after, blocked=False)
                if budget:
                    _budget_tracker.record_spend(tokens_after)

        self._proxy_request(upstream, body_to_send, start, tokens_before, tokens_after, client_ip, raw_body_for_cache=raw_body if cache_enabled else None)

    def _record_ledger(self, before: int, after: int, blocked: bool) -> None:
        try:
            from synthelion.analytics.ledger import get_ledger
            get_ledger().record(
                "proxy_forward", before or 0, after or before or 0,
                content_type="proxy", pii_masked_count=1 if blocked else 0,
            )
        except Exception:
            pass

    def _log_call(
        self, upstream: str, status_code: int | None, start: float, responded: bool,
        blocked: bool = False, tokens_before: int = 0, tokens_after: int = 0,
        error: str | None = None, client_ip: str = "",
    ) -> None:
        """Structured, prompt-free log entry — see synthelion/analytics/proxy_log.py.
        Never pass request/response bodies or masked/compressed text here."""
        try:
            from synthelion.analytics.proxy_log import get_proxy_log
            get_proxy_log().record(
                method=self.command, path=self.path, upstream=upstream,
                status_code=status_code, duration_ms=(time.perf_counter() - start) * 1000,
                responded=responded, blocked=blocked, tokens_before=tokens_before,
                tokens_after=tokens_after, error=error, client_ip=client_ip,
            )
        except Exception:
            pass

    # Status codes that mean "this provider didn't actually serve the
    # request" — worth trying the next candidate in the failover chain for,
    # as opposed to a normal 4xx (bad request, auth failure) which retrying
    # against a *different* provider wouldn't fix and would just mask.
    _FAILOVER_STATUS_CODES = {429, 500, 502, 503, 504}

    def _build_candidate_chain(self, primary_upstream: str) -> list[str]:
        cfg = self.proxy_cfg
        chain = [primary_upstream]
        for extra in (cfg.get("fallback_upstreams") or [])[:10]:
            extra = (extra or "").strip()
            if extra and extra not in chain:
                chain.append(extra)
        return chain[:10] if len(chain) > 10 else chain

    def _attempt_upstream(self, upstream: str, body: bytes, timeout: float):
        """One connection attempt. Returns (conn, resp) on any HTTP response
        (caller decides whether the status code is retry-worthy), or raises
        on a connection-level failure (refused, DNS, TLS, timeout, ...) —
        every one of those is "the provider didn't respond" and the caller
        treats it identically to a 5xx for failover purposes."""
        parsed_upstream = urlparse(upstream)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        headers["Host"] = parsed_upstream.netloc
        headers["Content-Length"] = str(len(body))

        if parsed_upstream.scheme == "https":
            conn = http.client.HTTPSConnection(parsed_upstream.netloc, timeout=timeout, context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(parsed_upstream.netloc, timeout=timeout)
        conn.request(self.command, self.path, body=body, headers=headers)
        resp = conn.getresponse()
        return conn, resp

    def _proxy_request(
        self, upstream: str, body: bytes, start: float,
        tokens_before: int, tokens_after: int, client_ip: str,
        raw_body_for_cache: bytes | None = None,
    ) -> None:
        cfg = self.proxy_cfg
        timeout = float(cfg.get("attempt_timeout_seconds", 30))
        chain = self._build_candidate_chain(upstream)

        last_error: str | None = None
        last_status: int | None = None
        for i, candidate in enumerate(chain):
            is_last = i == len(chain) - 1
            remaining = _circuit_breaker.seconds_until_closed(candidate)
            if remaining > 0 and not is_last:
                last_error = f"circuit open for {candidate} ({remaining:.0f}s remaining)"
                continue  # skip a known-bad upstream without spending a real attempt on it

            try:
                conn, resp = self._attempt_upstream(candidate, body, timeout)
            except Exception as exc:  # noqa: BLE001 — connect/TLS/DNS/timeout, all "didn't respond"
                _circuit_breaker.record_rate_limit(candidate, cfg)  # counts toward the same cooldown logic
                last_error = f"{candidate}: {exc}"
                last_status = None
                continue

            if resp.status in self._FAILOVER_STATUS_CODES and not is_last:
                _circuit_breaker.record_rate_limit(candidate, cfg)
                last_error = f"{candidate} returned {resp.status}"
                last_status = resp.status
                conn.close()
                continue

            # Committed: either a good response, or the last candidate — from
            # here on we stream to the client and can no longer fail over.
            _circuit_breaker.record_success(candidate)
            self._stream_response(conn, resp, candidate, start, tokens_before, tokens_after, client_ip, raw_body_for_cache)
            return

        # Every candidate failed before we could commit to a response.
        with self.stats_lock:
            self.stats["errors"] += 1
        self._log_call(chain[-1] if chain else "", last_status, start, responded=False,
                        error=(last_error or "no upstream available")[:200], client_ip=client_ip)
        try:
            self._serve_json(502, {"error": f"all upstreams failed: {last_error or 'no upstream available'}"})
        except Exception:
            pass

    def _stream_response(
        self, conn, resp, upstream: str, start: float, tokens_before: int, tokens_after: int,
        client_ip: str, raw_body_for_cache: bytes | None = None,
    ) -> None:
        response_headers = resp.getheaders()
        content_type = next((v for k, v in response_headers if k.lower() == "content-type"), "")
        is_streamy = "event-stream" in content_type or resp.getheader("Transfer-Encoding", "").lower() == "chunked"
        cacheable = raw_body_for_cache is not None and resp.status == 200 and not is_streamy
        buffered = bytearray() if cacheable else None

        try:
            self.send_response(resp.status)
            for k, v in response_headers:
                if k.lower() not in _RESPONSE_STRIP:
                    self.send_header(k, v)
            if cacheable:
                self.send_header("X-Synthelion-Cache", "miss")
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                if buffered is not None:
                    buffered.extend(chunk)
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    buffered = None  # client vanished mid-stream — don't cache a partial body
                    break
            conn.close()
            if buffered is not None:
                cfg = self.proxy_cfg
                cacheable_headers = [(k, v) for k, v in response_headers if k.lower() not in _RESPONSE_STRIP and k.lower() != "x-synthelion-cache"]
                _response_cache.put(upstream, self.path, raw_body_for_cache, resp.status, cacheable_headers, bytes(buffered), int(cfg.get("response_cache_max_entries", 200)))
            self._log_call(upstream, resp.status, start, responded=True,
                            tokens_before=tokens_before, tokens_after=tokens_after, client_ip=client_ip)
        except Exception as exc:  # noqa: BLE001 — failure after headers were already sent; can't fail over now
            with self.stats_lock:
                self.stats["errors"] += 1
            self._log_call(upstream, getattr(resp, "status", None), start, responded=False, error=str(exc)[:200], client_ip=client_ip)


def run_proxy(host: str = "127.0.0.1", port: int = 8788, cfg: dict | None = None) -> None:
    """Start the proxy HTTP server and block until interrupted."""
    from synthelion.config import load_config

    proxy_cfg = cfg if cfg is not None else load_config().get("proxy", {})
    _ProxyHandler.proxy_cfg = proxy_cfg
    _ProxyHandler.stats = {"requests": 0, "blocked": 0, "tokens_before": 0, "tokens_after": 0, "errors": 0, "started_at": time.time()}
    _circuit_breaker.reset()
    _response_cache.reset()

    server = ThreadingHTTPServer((host, port), _ProxyHandler)
    print(f"Synthelion proxy — http://{host}:{port}/  (Ctrl+C to stop)")
    print(f"  Anthropic upstream : {proxy_cfg.get('anthropic_upstream')}")
    print(f"  OpenAI upstream    : {proxy_cfg.get('openai_upstream')}")
    print("  Point an agent's ANTHROPIC_BASE_URL / OPENAI_BASE_URL at this address to enforce")
    print("  privacy masking + compression server-side, independent of MCP/hook support.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
