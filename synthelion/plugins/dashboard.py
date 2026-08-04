# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Local, read-only web dashboard over Synthelion's savings ledger and session memory.

Serves a Bootstrap 5 page (vendored locally — no CDN, works offline) backed by
a few read-only JSON endpoints. Binds to 127.0.0.1 by default: it is meant to
be opened in a browser on the same machine, not exposed to a network.

Protected by a login page (session cookie, see dashboard_auth.py). A default
admin/admin login is created on first run so the dashboard works out of the
box; change it with `synthelion dashboard-passwd`. The login page UI is built
with Material Dashboard Free by Creative Tim (MIT License, vendored locally —
see dashboard_assets/vendor/material-dashboard/ATTRIBUTION.md); the rest of
the dashboard is original Synthelion code over vendored Bootstrap 5.

Run:
    synthelion serve-dashboard
    synthelion serve-dashboard --port 8787 --host 0.0.0.0   # explicit opt-in to expose it

Every request just reads from disk (ledger.py / session_db.py already support
many concurrent readers/writers without locks — see those modules), so
multiple browser tabs or a monitoring script can poll this concurrently.
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from synthelion.analytics.proxy_log import get_proxy_log
from synthelion.plugins import dashboard_auth

_ASSETS_DIR = Path(__file__).parent / "dashboard_assets"

# Explicit allow-list of static files servable under /assets/ — avoids any
# path-traversal surface from request paths. These are public (not gated by
# login) since none of them carry user data — the login page itself needs to
# load its stylesheet before the user has a session.
_STATIC_FILES = {
    "/assets/dashboard.css": _ASSETS_DIR / "dashboard.css",
    "/assets/dashboard.js": _ASSETS_DIR / "dashboard.js",
    "/assets/vendor/bootstrap/bootstrap.min.css": _ASSETS_DIR / "vendor" / "bootstrap" / "bootstrap.min.css",
    "/assets/vendor/bootstrap/bootstrap.bundle.min.js": _ASSETS_DIR / "vendor" / "bootstrap" / "bootstrap.bundle.min.js",
    "/assets/vendor/chartjs/chart.umd.min.js": _ASSETS_DIR / "vendor" / "chartjs" / "chart.umd.min.js",
    "/assets/vendor/material-dashboard/material-dashboard.min.css": _ASSETS_DIR / "vendor" / "material-dashboard" / "material-dashboard.min.css",
    "/assets/vendor/material-dashboard/material-dashboard.min.js": _ASSETS_DIR / "vendor" / "material-dashboard" / "material-dashboard.min.js",
    "/assets/vendor/material-dashboard/img/synthelion-login.png": _ASSETS_DIR / "vendor" / "material-dashboard" / "img" / "synthelion-login.png",
    "/assets/vendor/material-dashboard/css/inter.css": _ASSETS_DIR / "vendor" / "material-dashboard" / "css" / "inter.css",
    "/assets/vendor/material-dashboard/fonts/inter-latin.woff2": _ASSETS_DIR / "vendor" / "material-dashboard" / "fonts" / "inter-latin.woff2",
    "/assets/vendor/material-dashboard/js/perfect-scrollbar.min.js": _ASSETS_DIR / "vendor" / "material-dashboard" / "js" / "perfect-scrollbar.min.js",
    "/assets/vendor/material-dashboard/js/smooth-scrollbar.min.js": _ASSETS_DIR / "vendor" / "material-dashboard" / "js" / "smooth-scrollbar.min.js",
    "/assets/img/logo.png": _ASSETS_DIR / "img" / "logo.png",
    "/assets/img/synthelion-banner.png": _ASSETS_DIR / "img" / "synthelion-banner.png",
    "/assets/img/mark.png": _ASSETS_DIR / "img" / "mark.png",
    "/assets/img/apple-touch-icon.png": _ASSETS_DIR / "img" / "apple-touch-icon.png",
    "/assets/img/favicon-96x96.png": _ASSETS_DIR / "img" / "favicon-96x96.png",
    "/favicon.ico": _ASSETS_DIR / "img" / "favicon.ico",
}

# Client-side routed "pages" — every one of these serves the same index.html
# shell; dashboard.js shows/hides the matching <section data-page="..."> based
# on the current path and intercepts sidenav/navbar link clicks with
# history.pushState so navigating between them doesn't reload the page.
_PAGE_ROUTES = frozenset({
    "/", "/index.html", "/overview", "/charts", "/sessions", "/requests",
    "/decisions", "/settings", "/doctor", "/version", "/profile", "/notifications", "/cluster",
    "/privacy", "/security", "/proxy",
})

# Node-to-node cluster endpoints authenticate with the cluster's shared
# token (Authorization: Bearer <token>, see _cluster_authenticated), never
# with the browser session cookie — a joining/heartbeating node has no
# browser and no login. Listed here so do_GET/do_POST can route them before
# the session-cookie auth gate that applies to everything else.
_CLUSTER_TOKEN_GET_PATHS = frozenset({"/api/cluster/self-status"})
_CLUSTER_TOKEN_POST_PATHS = frozenset({"/api/cluster/join", "/api/cluster/heartbeat"})

_SESSION_COOKIE = "synthelion_session"
_SESSION_TTL_SECONDS = 12 * 3600

# In-process session store: {token: (expiry_ts, credentials_fingerprint)}. One
# dashboard server = one process, so a plain lock is enough (same reasoning as
# LoopGuard's in-process history — see loop_guard.py). Storing the credentials
# fingerprint alongside each token means a password change via `synthelion
# dashboard-passwd` invalidates every session already issued by *this* running
# server the next time each one is checked, without needing cross-process
# coordination.
_sessions_lock = threading.Lock()
_sessions: dict[str, tuple[float, str]] = {}


class _DashboardHandler(BaseHTTPRequestHandler):
    server_version = "SynthelionDashboard/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - silence default stderr logging
        pass

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if not self._waf_gate(path, parsed.query, ""):
            return

        if path == "/login":
            self._serve_file(_ASSETS_DIR / "login.html", "text/html; charset=utf-8")
            return

        if path == "/logout":
            self._handle_logout()
            return

        if path in _STATIC_FILES:
            self._serve_file(_STATIC_FILES[path], _guess_type(path))
            return

        if path in _CLUSTER_TOKEN_GET_PATHS:
            if not self._cluster_authenticated():
                self._error(401, "Unauthorized")
                return
            try:
                self._serve_json(self._cluster_self_status())
            except Exception as exc:  # noqa: BLE001
                self._error(500, str(exc))
            return

        if not self._authenticated():
            if path.startswith("/api/"):
                self._error(401, "Unauthorized")
            else:
                self._redirect_to_login(path)
            return

        try:
            if path in _PAGE_ROUTES:
                self._serve_file(_ASSETS_DIR / "index.html", "text/html; charset=utf-8")
            elif path == "/api/summary":
                self._serve_json(self._summary(qs))
            elif path == "/api/records":
                self._serve_json(self._records(qs))
            elif path == "/api/sessions":
                self._serve_json(self._sessions(qs))
            elif path == "/api/decisions":
                self._serve_json(self._decisions(qs))
            elif path == "/api/version":
                self._serve_json(self._version())
            elif path == "/api/config":
                self._serve_json(self._config())
            elif path == "/api/storage-status":
                self._serve_json(self._storage_status())
            elif path == "/api/notifications":
                self._serve_json(self._notifications())
            elif path == "/api/account":
                self._serve_json({"username": dashboard_auth.current_username()})
            elif path == "/api/doctor":
                from synthelion.cli import run_doctor_checks
                self._serve_json({"checks": run_doctor_checks()})
            elif path == "/api/version-check":
                from synthelion.cli import check_pypi_version
                self._serve_json(check_pypi_version())
            elif path == "/api/cluster/status":
                self._serve_json(self._cluster_status())
            elif path == "/api/cluster/compose-file":
                from synthelion.cluster import render_docker_compose
                nodes = int(qs.get("nodes", ["2"])[0])
                self._serve_text(render_docker_compose(nodes), "docker-compose.cluster.yml")
            elif path == "/api/cluster/k8s-manifest":
                from synthelion.cluster import render_k8s_manifest
                nodes = int(qs.get("nodes", ["2"])[0])
                self._serve_text(render_k8s_manifest(nodes), "synthelion-cluster.k8s.yaml")
            elif path == "/api/enterprise-guard/events":
                self._serve_json(self._enterprise_guard_events(qs))
            elif path == "/api/waf/events":
                self._serve_json(self._waf_events(qs))
            elif path == "/api/waf/ip-rules":
                self._serve_json(self._waf_ip_rules())
            elif path == "/api/proxy/status":
                self._serve_json(self._proxy_status())
            elif path == "/api/proxy/logs":
                limit = int(qs.get("limit", ["100"])[0])
                self._serve_json({"logs": get_proxy_log().recent(limit)})
            elif path == "/api/proxy/providers":
                self._serve_json(self._proxy_providers())
            else:
                self._error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 - never let one bad request kill the server
            self._error(500, str(exc))

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        path = urlparse(self.path).path
        # Always drain the request body before writing any response, even a
        # rejection — responding while the client still has unsent/unacked
        # body bytes in flight can make the OS reset the connection instead
        # of closing it cleanly (observed as a flaky ConnectionAbortedError
        # on Windows for the 401-before-body-read case in particular).
        self._body = self._read_raw_body()

        parsed = urlparse(self.path)
        if not self._waf_gate(path, parsed.query, self._body.decode("utf-8", errors="ignore") if self._body else ""):
            return

        if path == "/login":
            self._handle_login()
            return
        if path == "/logout":
            self._handle_logout()
            return

        if path in _CLUSTER_TOKEN_POST_PATHS:
            if not self._cluster_authenticated():
                self._error(401, "Unauthorized")
                return
            try:
                if path == "/api/cluster/join":
                    self._serve_json(self._cluster_join())
                else:
                    self._serve_json(self._cluster_heartbeat())
            except Exception as exc:  # noqa: BLE001
                self._error(500, str(exc))
            return

        if not self._authenticated():
            self._error(401, "Unauthorized")
            return
        try:
            if path == "/api/config":
                self._serve_json(self._update_config())
            elif path == "/api/account":
                self._serve_json(self._update_account())
            elif path == "/api/sessions/prune":
                self._serve_json(self._prune_sessions())
            elif path == "/api/sessions/delete":
                self._serve_json(self._delete_session())
            elif path == "/api/decisions/prune":
                self._serve_json(self._prune_decisions())
            elif path == "/api/upgrade":
                self._serve_json(self._run_upgrade())
            elif path == "/api/restart":
                self._serve_json(self._restart_dashboard())
            elif path == "/api/cluster/action":
                self._serve_json(self._cluster_action())
            elif path == "/api/privacy-test":
                self._serve_json(self._privacy_test())
            elif path == "/api/enterprise-guard-test":
                self._serve_json(self._enterprise_guard_test())
            elif path == "/api/documents/mask":
                self._serve_json(self._documents_mask())
            elif path == "/api/waf/ip-rules":
                self._serve_json(self._waf_add_ip_rule())
            elif path == "/api/waf/ip-rules/delete":
                self._serve_json(self._waf_delete_ip_rule())
            elif path == "/api/proxy/start":
                self._serve_json(self._proxy_start())
            elif path == "/api/proxy/stop":
                self._serve_json(self._proxy_stop())
            else:
                self._error(404, "Not found")
        except Exception as exc:  # noqa: BLE001 - never let one bad request kill the server
            self._error(500, str(exc))

    def _waf_gate(self, path: str, query: str, body: str) -> bool:
        """Returns True if the request should proceed. Writes the block response
        itself and returns False otherwise. Called at the very start of both
        do_GET and do_POST — before login/logout, before routing, before the
        session-cookie auth check — so it protects every endpoint including the
        bearer-token cluster ones. See synthelion/waf_guard.py."""
        if path.startswith("/assets/") or path in _STATIC_FILES or path in ("/login", "/logout"):
            return True
        from synthelion.config import waf_config
        from synthelion.waf_guard import get_waf_engine

        cfg = waf_config()
        if not cfg.get("enabled", True):
            return True
        excluded = cfg.get("excluded_paths") or []
        if any(path.startswith(p.strip()) for p in excluded if p and p.strip()):
            return True
        if cfg.get("skip_authenticated", True) and self._authenticated():
            return True

        ip = self.client_address[0] if self.client_address else ""
        ua = self.headers.get("User-Agent", "")
        decision = get_waf_engine().gate(ip, self.command, path, query, ua, body, cfg)
        if not decision.allowed:
            self._waf_block(cfg)
            return False
        return True

    def _waf_block(self, cfg: dict) -> None:
        message = cfg.get("block_message") or "Request blocked by Synthelion firewall."
        status = int(cfg.get("block_status_code") or 403)
        payload = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError:
            pass

    def _read_raw_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _read_json_body(self) -> dict:
        if not self._body:
            raise ValueError("empty request body")
        data = json.loads(self._body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    # ── read-only data endpoints ─────────────────────────────────────────────

    @staticmethod
    def _summary(qs: dict) -> dict:
        from synthelion.analytics.ledger import get_ledger

        ledger = get_ledger()
        days = qs.get("days", [None])[0]
        records = ledger.records_since(int(days)) if days else ledger.all_records()
        return ledger.summary(records)

    @staticmethod
    def _records(qs: dict) -> dict:
        from synthelion.analytics.ledger import get_ledger

        ledger = get_ledger()
        days = qs.get("days", [None])[0]
        records = ledger.records_since(int(days)) if days else ledger.all_records()
        limit = qs.get("limit", [None])[0]
        if limit:
            records = sorted(records, key=lambda r: r.get("ts") or "", reverse=True)[: int(limit)]
        return {"records": records, "count": len(records)}

    @staticmethod
    def _sessions(qs: dict) -> dict:
        from synthelion.analytics.ledger import get_ledger

        ledger = get_ledger()
        days = qs.get("days", [None])[0]
        records = ledger.records_since(int(days)) if days else ledger.all_records()
        sessions = ledger.sessions_summary(records)
        limit = qs.get("limit", [None])[0]
        if limit:
            sessions = sessions[: int(limit)]
        return {"sessions": sessions, "count": len(sessions)}

    @staticmethod
    def _decisions(qs: dict) -> dict:
        from synthelion.analytics.session_db import get_session_db

        db = get_session_db()
        limit = int(qs.get("limit", [20])[0])
        return {"decisions": db.list_decisions(limit=limit), "backend": db.backend()}

    @staticmethod
    def _version() -> dict:
        import synthelion

        return {"version": synthelion.__version__}

    @staticmethod
    def _config() -> dict:
        from synthelion.config import config_path, load_config

        path = config_path()
        return {"config": load_config(), "path": str(path) if path else None}

    def _update_config(self) -> dict:
        from synthelion.config import load_config, merge_config, save_config

        partial = self._read_json_body()
        current = load_config()
        merged = merge_config(current, partial)
        save_config(merged)
        return {"config": merged, "status": "saved"}

    def _privacy_test(self) -> dict:
        """Live PrivacyGuard tester for the Privacy & Security page — runs both
        PII detection and prompt-injection screening on submitted text. Never
        persists the submitted text anywhere."""
        from synthelion.privacy_analyzer import PrivacyAnalyzer
        from synthelion.prompt_injection_guard import PromptInjectionGuard

        body = self._read_json_body()
        text = body.get("text", "")
        language = body.get("language") or "en"

        privacy = PrivacyAnalyzer().analyze(text, language, auto_masking=True)
        injection = PromptInjectionGuard().analyze(text)
        return {
            "privacy": {
                "score": privacy.score,
                "risk_level": privacy.risk_level,
                "detected_categories": privacy.detected_categories,
                "compliance_flags": privacy.compliance_flags,
                "masked_text": privacy.masked_text,
                "match_count": privacy.match_count,
            },
            "prompt_injection": {
                "score": injection.score,
                "risk_level": injection.risk_level,
                "detected_categories": injection.detected_categories,
                "is_clean": injection.is_clean,
            },
        }

    def _documents_mask(self) -> dict:
        """Mask PII in an uploaded document (PDF/Word/Excel/CSV/Markdown/text).
        No multipart-upload precedent exists in this handler, so — consistent
        with every other endpoint here — the file travels as base64 inside the
        same JSON-body convention `_read_json_body` already uses everywhere
        else, rather than adding raw multipart parsing from scratch. Intended
        for the local, single-operator admin dashboard use case; respects
        `documents.max_file_size_mb`."""
        import base64
        import os
        import tempfile

        from synthelion.config import documents_config, privacy_config
        from synthelion.privacy_analyzer import PrivacyAnalyzer
        from synthelion.privacy_session import PrivacySession
        from synthelion import document_extractor as de

        body = self._read_json_body()
        filename = body.get("filename") or "upload.txt"
        content_b64 = body.get("content_base64")
        if not content_b64:
            return {"error": "content_base64 is required"}
        try:
            raw = base64.b64decode(content_b64)
        except Exception:
            return {"error": "content_base64 is not valid base64"}

        dcfg = documents_config()
        max_bytes = dcfg["max_file_size_mb"] * 1024 * 1024
        if len(raw) > max_bytes:
            return {"error": f"File is {len(raw) / (1024 * 1024):.1f} MB, exceeds documents.max_file_size_mb ({dcfg['max_file_size_mb']})"}

        pcfg = privacy_config()
        language = body.get("language") or pcfg.get("language", "en")
        suffix = Path(filename).suffix
        fmt = de.detect_format(filename)

        analyzer = PrivacyAnalyzer()
        if pcfg.get("whitelist"):
            analyzer.add_to_whitelist(*pcfg["whitelist"])
        session = PrivacySession()

        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_in:
            tmp_in.write(raw)
            in_path = tmp_in.name

        stem = Path(filename).stem
        masked_name = f"{stem}{dcfg['output_suffix']}{suffix}"
        out_fd, out_path = tempfile.mkstemp(suffix=suffix)
        os.close(out_fd)

        try:
            if fmt == "docx":
                doc = de.load_docx(in_path)
                de.mask_docx_in_place(doc, analyzer, session, language)
                doc.save(out_path)
                with open(out_path, "rb") as f:
                    masked_content_b64 = base64.b64encode(f.read()).decode("ascii")
                result: dict = {"masked_content_base64": masked_content_b64, "filename": masked_name}
            elif fmt == "xlsx":
                wb = de.load_xlsx(in_path)
                de.mask_xlsx_in_place(wb, analyzer, session, language)
                wb.save(out_path)
                with open(out_path, "rb") as f:
                    masked_content_b64 = base64.b64encode(f.read()).decode("ascii")
                result = {"masked_content_base64": masked_content_b64, "filename": masked_name}
            else:
                from synthelion.privacy_stream import PrivacyStreamMasker
                masker = PrivacyStreamMasker(
                    analyzer=analyzer, session=session, language=language, overlap_chars=dcfg["chunk_overlap_chars"],
                )
                if fmt == "pdf":
                    chunks = de.extract_pdf_text(in_path)
                elif fmt == "csv":
                    chunks = de.extract_csv_rows(in_path)
                else:
                    chunks = de.extract_text_chunks(in_path)
                parts = [masker.feed(chunk) for chunk in chunks]
                parts.append(masker.flush())
                result = {"masked_text": "".join(parts)}
        except ImportError as exc:
            return {"error": str(exc)}
        finally:
            for p in (in_path, out_path):
                try:
                    os.remove(p)
                except OSError:
                    pass

        # Redacted summary only (placeholder + category), never original values.
        new_entries = session.summary()
        result["masked_count"] = len(new_entries)
        result["detected_categories"] = sorted({e["category"] for e in new_entries})
        return result

    @staticmethod
    def _enterprise_guard_events(qs: dict) -> dict:
        from synthelion.enterprise_guard import recent_blocks

        limit = int(qs.get("limit", [100])[0])
        return {"events": recent_blocks(limit=limit)}

    def _enterprise_guard_test(self) -> dict:
        """Live EnterpriseGuard tester for the Security page — checks submitted
        text and/or path without persisting either. Distinct from the
        recent-blocks log: a manual test here is never recorded as a real
        block event."""
        from synthelion.enterprise_guard import EnterpriseGuard

        body = self._read_json_body()
        text = body.get("text") or ""
        path = body.get("path") or ""
        guard = EnterpriseGuard()

        text_result = guard._check_text(text) if text else None
        path_result = guard._check_path(path) if path else None

        return {
            "text_result": None if text_result is None else {
                "blocked": text_result.blocked, "category": text_result.category,
                "rule_name": text_result.rule_name, "reason": text_result.reason,
            },
            "path_result": None if path_result is None else {
                "blocked": path_result.blocked, "category": path_result.category,
                "rule_name": path_result.rule_name, "reason": path_result.reason,
            },
        }

    @staticmethod
    def _waf_events(qs: dict) -> dict:
        from synthelion.waf_guard import get_waf_engine

        limit = int(qs.get("limit", [200])[0])
        since_days = qs.get("since_days", [None])[0]
        events = get_waf_engine().all_events(limit=limit, since_days=float(since_days) if since_days else None)
        return {"events": events}

    @staticmethod
    def _waf_ip_rules() -> dict:
        from synthelion.waf_guard import get_waf_engine

        rules = get_waf_engine().list_ip_rules()
        return {"rules": [r.to_dict() for r in rules]}

    def _waf_add_ip_rule(self) -> dict:
        from synthelion.waf_guard import get_waf_engine

        data = self._read_json_body()
        ip = (data.get("ip") or "").strip()
        kind = data.get("kind") or "Block"
        if not ip:
            raise ValueError("ip is required")
        if kind not in ("Block", "Allow"):
            raise ValueError("kind must be Block or Allow")
        minutes = data.get("minutes")
        get_waf_engine().add_ip_rule(ip, kind, reason=data.get("reason") or "Manual", minutes=minutes)
        return {"status": "added", "ip": ip, "kind": kind}

    def _waf_delete_ip_rule(self) -> dict:
        from synthelion.waf_guard import get_waf_engine

        data = self._read_json_body()
        ip = (data.get("ip") or "").strip()
        kind = data.get("kind") or "Block"
        if not ip:
            raise ValueError("ip is required")
        get_waf_engine().delete_ip_rule(ip, kind)
        return {"status": "deleted", "ip": ip, "kind": kind}

    @staticmethod
    def _storage_status() -> dict:
        from synthelion.analytics.ledger import get_ledger
        from synthelion.analytics.session_db import get_session_db

        ledger = get_ledger()
        records = ledger.all_records()
        sessions = ledger.sessions_summary(records)
        db = get_session_db()
        decisions = db.list_decisions(limit=10_000)
        return {
            "sessions": len(sessions),
            "decisions": len(decisions),
            "ledger_records": len(records),
            "vector_backend": db.backend(),
        }

    @staticmethod
    def _notifications() -> dict:
        """Real, locally-computable health signals — never fabricated demo content.

        Currently: a security warning if the dashboard still has the default
        admin/admin login, and one warning per configured backend (session_store
        / vector_store) whose Python package isn't installed, so it's silently
        falling back to the local/lexical default instead of what's configured.
        """
        import importlib.util

        from synthelion.config import load_config

        items = []
        if dashboard_auth.is_using_default_password():
            items.append({
                "level": "warning",
                "title": "Default login in use",
                "message": "The dashboard still uses the default admin/admin password. Change it with `synthelion dashboard-passwd`.",
            })

        cfg = load_config()
        backend_packages = {
            "redis": ("redis", cfg["session_store"]["backend"] == "redis"),
            "psycopg": ("psycopg", cfg["session_store"]["backend"] == "postgres"),
            "chromadb": ("chromadb", cfg["vector_store"]["backend"] == "chromadb"),
            "qdrant_client": ("qdrant_client", cfg["vector_store"]["backend"] == "qdrant"),
        }
        for module_name, (label, selected) in backend_packages.items():
            if selected and importlib.util.find_spec(module_name) is None:
                items.append({
                    "level": "error",
                    "title": f"Missing package for '{label}' backend",
                    "message": f"Configured backend requires the '{module_name}' package (pip install 'synthelion[{label}]') — falling back to the local default until installed.",
                })

        return {"notifications": items, "count": len(items)}

    def _update_account(self) -> dict:
        data = self._read_json_body()
        current_password = data.get("current_password") or ""
        new_username = (data.get("new_username") or "").strip() or dashboard_auth.current_username()
        new_password = data.get("new_password") or ""

        if not dashboard_auth.verify(dashboard_auth.current_username(), current_password):
            raise ValueError("current password is incorrect")
        if not new_password:
            raise ValueError("new password must not be empty")

        dashboard_auth.set_credentials(new_username, new_password)
        return {"username": new_username, "status": "saved"}

    def _prune_sessions(self) -> dict:
        from synthelion.analytics.ledger import get_ledger

        data = self._read_json_body()
        days = int(data.get("days", 30))
        if days < 1:
            raise ValueError("days must be >= 1")
        removed = get_ledger().prune_older_than(days)
        return {"removed": removed, "days": days}

    def _delete_session(self) -> dict:
        from synthelion.analytics.ledger import get_ledger

        data = self._read_json_body()
        session_id = data.get("session_id")
        if not session_id:
            raise ValueError("session_id is required")
        removed = get_ledger().delete_session(session_id)
        return {"removed": removed, "session_id": session_id}

    def _prune_decisions(self) -> dict:
        from synthelion.analytics.session_db import get_session_db

        data = self._read_json_body()
        days = int(data.get("days", 30))
        if days < 1:
            raise ValueError("days must be >= 1")
        removed = get_session_db().prune_older_than(days)
        return {"removed": removed, "days": days}

    @staticmethod
    def _run_upgrade() -> dict:
        """`pip install --upgrade synthelion`, run synchronously from a user click
        (Settings > System > Upgrade now). Blocks the request until pip finishes —
        acceptable here since it's an explicit, infrequent admin action, not
        something on any hot path."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "synthelion"],
            capture_output=True, text=True, timeout=120,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout or "") + (result.stderr or ""),
        }

    @staticmethod
    def _restart_dashboard() -> dict:
        """Re-exec this process in place (same host/port, same argv) so a
        freshly-`pip install --upgrade`d Synthelion actually takes effect —
        upgrading the package on disk doesn't change what's already loaded in
        this running process's memory, so without this the "Upgrade now"
        button silently kept serving the old code until someone found a
        terminal and killed/relaunched `synthelion serve-dashboard` by hand.

        Re-launches via `<python> -m synthelion.cli <original argv[1:]>`
        rather than re-invoking sys.argv[0] directly, since argv[0] may be a
        platform-specific console-script wrapper (e.g. a Windows .exe) that
        `os.execv` can't treat as a script — `-m synthelion.cli` works
        identically regardless of how the process was originally launched.
        """
        import os
        import sys
        import threading
        import time

        def _do_restart() -> None:
            time.sleep(0.5)  # let the HTTP response for this request flush first
            python_exe = sys.executable
            os.execv(python_exe, [python_exe, "-m", "synthelion.cli"] + sys.argv[1:])

        threading.Thread(target=_do_restart, daemon=False).start()
        return {"restarting": True}

    # ── proxy (start/stop/status) ─────────────────────────────────────────────
    #
    # The proxy (synthelion/plugins/proxy.py) is a separate, opt-in process —
    # managing it here just means spawning/killing that subprocess and
    # tracking its PID in ~/.synthelion/proxy.pid, the same way the dashboard
    # itself is a separate process from the MCP server. Starting/stopping the
    # proxy never touches, restarts, or otherwise affects the MCP/hook
    # integrations (`synthelion install --agent ...`) — they're independent.

    @staticmethod
    def _proxy_pid_path() -> Path:
        return Path.home() / ".synthelion" / "proxy.pid"

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        import platform
        import subprocess
        if platform.system() == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
            )
            return str(pid) in result.stdout
        import os
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _proxy_status(self) -> dict:
        from synthelion.config import load_config
        cfg = load_config().get("proxy", {})
        pid_path = self._proxy_pid_path()
        pid = None
        running = False
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
                running = self._pid_alive(pid)
            except (ValueError, OSError):
                pass
        return {
            "enabled": cfg.get("enabled", False), "running": running, "pid": pid,
            "host": cfg.get("host"), "port": cfg.get("port"),
            "anthropic_upstream": cfg.get("anthropic_upstream"),
            "openai_upstream": cfg.get("openai_upstream"),
        }

    def _proxy_start(self) -> dict:
        import subprocess
        import sys

        pid_path = self._proxy_pid_path()
        if pid_path.exists():
            try:
                existing_pid = int(pid_path.read_text(encoding="utf-8").strip())
                if self._pid_alive(existing_pid):
                    return {"status": "already_running", "pid": existing_pid}
            except (ValueError, OSError):
                pass

        proc = subprocess.Popen(
            [sys.executable, "-m", "synthelion.cli", "serve-proxy"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        return {"status": "started", "pid": proc.pid}

    def _proxy_stop(self) -> dict:
        pid_path = self._proxy_pid_path()
        if not pid_path.exists():
            return {"status": "not_running"}
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid_path.unlink(missing_ok=True)
            return {"status": "not_running"}
        if not self._pid_alive(pid):
            pid_path.unlink(missing_ok=True)
            return {"status": "not_running"}

        import platform
        import subprocess
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                import os
                import signal
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        pid_path.unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}

    @staticmethod
    def _proxy_providers() -> dict:
        """On-demand only (never called automatically) — fetches a provider
        list so the Proxy page can offer a "pick a provider" convenience
        instead of the user hand-typing base URLs. Sourced from
        digitalsolutions.it's own synced mirror (same underlying data as
        models.dev, refreshed periodically server-side) rather than models.dev
        directly, since the latter isn't reachable from every network this
        runs on. Consistent with the project's only other outbound call
        (check_pypi_version): explicit user action, not a background poll."""
        import json as _json
        import urllib.request

        try:
            req = urllib.request.Request(
                "https://www.digitalsolutions.it/IaModels?handler=DownloadProviders",
                headers={"User-Agent": "Synthelion-Dashboard"},
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                data = _json.loads(resp.read())
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "providers": []}

        providers = [
            {"id": p.get("id", ""), "name": p.get("name", p.get("id", "")), "api": p.get("api", "")}
            for p in data
            if isinstance(p, dict) and p.get("api")
        ]
        providers.sort(key=lambda p: p["name"].lower())
        return {"providers": providers}

    # ── cluster (master/slave) ──────────────────────────────────────────────
    #
    # Two auth schemes coexist here: the endpoints under _CLUSTER_TOKEN_*_PATHS
    # (join/heartbeat/self-status) use the cluster's shared token, since the
    # caller is another node's process, not a browser with a session cookie.
    # Everything else below (status/action) uses the normal session cookie,
    # since those are the dashboard UI's own Cluster page talking to its own
    # backend.

    def _cluster_authenticated(self) -> bool:
        from synthelion.config import load_config
        token = load_config().get("cluster", {}).get("node_token", "")
        if not token:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):], token)

    @staticmethod
    def _cluster_self_status() -> dict:
        import synthelion
        from synthelion.analytics.ledger import get_ledger
        from synthelion.config import load_config

        cfg = load_config()["cluster"]
        summary = get_ledger().summary()
        return {
            "node_id": cfg["node_id"],
            "role": cfg["role"],
            "version": synthelion.__version__,
            "total_calls": summary["total_calls"],
            "tokens_saved": summary["tokens_saved"],
        }

    def _cluster_join(self) -> dict:
        from synthelion.analytics.cluster_registry import get_cluster_registry
        from synthelion.config import load_config

        cfg = load_config()
        if cfg["cluster"]["role"] != "master":
            raise ValueError("this node is not a cluster master")

        data = self._read_json_body()
        node_id = data.get("node_id")
        url = data.get("url")
        if not node_id or not url:
            raise ValueError("node_id and url are required")

        get_cluster_registry().register(node_id, url)
        # Deliberately narrow: propagate only the settings that should behave
        # identically across the whole cluster (compression/wiki defaults).
        # session_store/vector_store are NOT propagated — those often differ
        # per node/region (e.g. each node's own local Redis), and silently
        # overwriting a joining node's storage config on join would be a much
        # more disruptive default than the operator likely expects.
        shareable = {"compression": cfg["compression"], "wiki": cfg["wiki"]}
        return {"config": shareable, "master_node_id": cfg["cluster"]["node_id"]}

    def _cluster_heartbeat(self) -> dict:
        from synthelion.analytics.cluster_registry import get_cluster_registry

        data = self._read_json_body()
        node_id = data.get("node_id")
        if not node_id:
            raise ValueError("node_id is required")
        ok = get_cluster_registry().heartbeat(node_id, stats=data.get("stats"))
        if not ok:
            raise ValueError(f"node '{node_id}' is not registered with this master — re-join required")
        return {"status": "ok"}

    @staticmethod
    def _cluster_status() -> dict:
        from synthelion.analytics.cluster_registry import get_cluster_registry
        from synthelion.config import load_config

        cfg = load_config()["cluster"]
        nodes = get_cluster_registry().list_nodes() if cfg["role"] == "master" else []
        return {
            "role": cfg["role"],
            "node_id": cfg["node_id"],
            "node_token": cfg["node_token"],
            "master_url": cfg["master_url"],
            "self_url": cfg["self_url"],
            "nodes": nodes,
        }

    def _cluster_action(self) -> dict:
        from synthelion.cluster import ClusterJoinError, join_master
        from synthelion.config import load_config, new_cluster_token, new_node_id, save_config

        data = self._read_json_body()
        action = data.get("action")
        cfg = load_config()

        if action == "become_master":
            cfg["cluster"]["role"] = "master"
            cfg["cluster"]["node_id"] = cfg["cluster"]["node_id"] or new_node_id()
            cfg["cluster"]["node_token"] = cfg["cluster"]["node_token"] or new_cluster_token()
            cfg["cluster"]["master_url"] = ""
            save_config(cfg)
            return {"cluster": cfg["cluster"]}

        if action == "join":
            master_url = (data.get("master_url") or "").rstrip("/")
            token = data.get("token") or ""
            self_url = data.get("self_url") or cfg["cluster"].get("self_url", "")
            node_id = data.get("node_id") or cfg["cluster"]["node_id"] or new_node_id()
            if not master_url or not token:
                raise ValueError("master_url and token are required")
            try:
                result = join_master(master_url, token, node_id, self_url)
            except ClusterJoinError as exc:
                raise ValueError(str(exc)) from exc

            cfg["cluster"]["role"] = "slave"
            cfg["cluster"]["node_id"] = node_id
            cfg["cluster"]["node_token"] = token
            cfg["cluster"]["master_url"] = master_url
            cfg["cluster"]["self_url"] = self_url
            shared = result.get("config") or {}
            if "compression" in shared:
                cfg["compression"] = shared["compression"]
            if "wiki" in shared:
                cfg["wiki"] = shared["wiki"]
            save_config(cfg)
            return {"cluster": cfg["cluster"], "joined_master": result.get("master_node_id")}

        if action == "leave":
            cfg["cluster"]["role"] = "standalone"
            cfg["cluster"]["master_url"] = ""
            save_config(cfg)
            return {"cluster": cfg["cluster"]}

        if action == "rotate_token":
            if cfg["cluster"]["role"] != "master":
                raise ValueError("only a master node can rotate the cluster token")
            cfg["cluster"]["node_token"] = new_cluster_token()
            save_config(cfg)
            return {"cluster": cfg["cluster"]}

        raise ValueError(f"unknown action: {action}")

    # ── auth ─────────────────────────────────────────────────────────────────

    def _session_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        jar: SimpleCookie = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(_SESSION_COOKIE)
        return morsel.value if morsel else None

    def _authenticated(self) -> bool:
        token = self._session_token()
        if not token:
            return False
        with _sessions_lock:
            entry = _sessions.get(token)
        if entry is None:
            return False
        expiry, fingerprint = entry
        valid = time.time() <= expiry and fingerprint == dashboard_auth.credentials_fingerprint()
        if not valid:
            with _sessions_lock:
                _sessions.pop(token, None)
        return valid

    def _redirect_to_login(self, next_path: str) -> None:
        self.send_response(302)
        self.send_header("Location", f"/login?next={quote(next_path, safe='')}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_login(self) -> None:
        fields = parse_qs(self._body.decode("utf-8"))
        username = fields.get("username", [""])[0]
        password = fields.get("password", [""])[0]
        next_path = fields.get("next", ["/"])[0]
        if not next_path.startswith("/") or next_path.startswith("//"):
            next_path = "/"  # only ever redirect within this app

        if not dashboard_auth.verify(username, password):
            self.send_response(302)
            self.send_header("Location", "/login?error=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            _sessions[token] = (time.time() + _SESSION_TTL_SECONDS, dashboard_auth.credentials_fingerprint())
        self.send_response(302)
        self.send_header("Location", next_path)
        self.send_header(
            "Set-Cookie",
            f"{_SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={_SESSION_TTL_SECONDS}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle_logout(self) -> None:
        token = self._session_token()
        if token:
            with _sessions_lock:
                _sessions.pop(token, None)
        self.send_response(302)
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", f"{_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._error(404, "Not found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_json(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_text(self, text: str, download_filename: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{download_filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, message: str) -> None:
        data = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _guess_type(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _cluster_heartbeat_loop() -> None:
    """Background thread for a slave node: on startup, join the configured
    master (retrying — in Docker/k8s the master and slaves typically start at
    the same time, so the master may not be reachable yet), then heartbeat
    every 30s. This is what makes `SYNTHELION_ROLE=slave` +
    `SYNTHELION_MASTER_URL=...` env vars (see the generated compose/k8s
    templates) actually self-register, instead of just sitting there
    configured-but-disconnected."""
    import time as _time

    from synthelion.cluster import ClusterJoinError, join_master, send_heartbeat
    from synthelion.config import load_config, new_node_id, save_config

    cfg = load_config()
    if cfg["cluster"]["role"] != "slave" or not cfg["cluster"]["master_url"]:
        return

    if not cfg["cluster"]["node_id"]:
        cfg["cluster"]["node_id"] = new_node_id()
        save_config(cfg)

    node_id = cfg["cluster"]["node_id"]
    master_url = cfg["cluster"]["master_url"]
    token = cfg["cluster"]["node_token"]
    self_url = cfg["cluster"]["self_url"]

    joined = False
    delay = 3
    while not joined:
        try:
            join_master(master_url, token, node_id, self_url)
            joined = True
            print(f"[cluster] joined master at {master_url} as '{node_id}'")
        except ClusterJoinError as exc:
            print(f"[cluster] could not join master yet ({exc}); retrying in {delay}s")
            _time.sleep(delay)
            delay = min(delay * 2, 60)

    while True:
        _time.sleep(30)
        try:
            stats = _DashboardHandler._cluster_self_status()
            send_heartbeat(master_url, token, node_id, stats=stats)
        except ClusterJoinError as exc:
            print(f"[cluster] heartbeat failed: {exc}")


def run_dashboard(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Start the dashboard HTTP server and block until interrupted."""
    dashboard_auth.ensure_default_credentials()
    threading.Thread(target=_cluster_heartbeat_loop, daemon=True).start()
    server = ThreadingHTTPServer((host, port), _DashboardHandler)
    url = f"http://{host}:{port}/"
    print(f"Synthelion dashboard — {url}  (Ctrl+C to stop)")
    if dashboard_auth.is_using_default_password():
        print("Login page: default credentials admin / admin. Change with `synthelion dashboard-passwd`.")
    else:
        print(f"Login page — username: {dashboard_auth.current_username()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
