# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Command-line interface for Synthelion."""
from __future__ import annotations

import argparse
import json
import sys


def main() -> None:
    # Force UTF-8 I/O — needed on Windows (default cp1252 mangles non-ASCII).
    # utf-8-sig on stdin strips BOM written by PowerShell pipes.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8-sig")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="synthelion",
        description="Synthelion — Python port of Caveman. Token compressor for LLMs.",
    )
    import synthelion as _synthelion_pkg
    parser.add_argument(
        "--version", action="version", version=f"synthelion {_synthelion_pkg.__version__}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # version (subcommand form — some callers/scripts probe `synthelion version`
    # instead of the `--version` flag; support both rather than erroring)
    p_version = sub.add_parser("version", help="Show the installed Synthelion version")
    p_version.add_argument("--json", action="store_true")

    # compress
    p_cmp = sub.add_parser("compress", help="Compress text to reduce LLM tokens")
    p_cmp.add_argument("--text", "-t", help="Text to compress (or use stdin)")
    p_cmp.add_argument("--level", "-l", choices=["light", "semantic", "aggressive", "statistical", "syntactic"], default=None, help="Default: configured value (see `synthelion configure --show`), normally 'semantic'")
    p_cmp.add_argument("--language", "-L", help="ISO 639-3 code (auto-detected if omitted)")
    p_cmp.add_argument("--json", action="store_true", help="Output as JSON")

    # detect
    p_det = sub.add_parser("detect", help="Detect language of text")
    p_det.add_argument("--text", "-t", help="Text to analyse (or use stdin)")
    p_det.add_argument("--scores", action="store_true", help="Show per-language scores")

    # route
    p_route = sub.add_parser("route", help="Content-aware routing and compression")
    p_route.add_argument("--text", "-t", help="Content to route (or use stdin)")
    p_route.add_argument("--file", "-f", help="Path to input file")
    p_route.add_argument("--profile", choices=["light", "balanced", "agent", "aggressive"], default="balanced")
    p_route.add_argument("--json", action="store_true")

    # summarize
    p_sum = sub.add_parser("summarize", help="Extractive summarization")
    p_sum.add_argument("--text", "-t", help="Text to summarize (or use stdin)")
    p_sum.add_argument("--sentences", "-n", type=int)
    p_sum.add_argument("--ratio", "-r", type=float)
    p_sum.add_argument("--algo", choices=["tfidf", "textrank"], default="textrank")

    # serve-mcp
    sub.add_parser("serve-mcp", help="Start MCP server on stdio")

    # serve-dashboard
    p_dash = sub.add_parser("serve-dashboard", help="Start local read-only web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1, local only)")
    p_dash.add_argument("--port", type=int, default=8787, help="Port (default: 8787)")

    # serve-proxy — local reverse proxy for agents with no MCP/hook support
    p_proxy = sub.add_parser(
        "serve-proxy",
        help="Start the local privacy/compression proxy (point ANTHROPIC_BASE_URL/OPENAI_BASE_URL at it)",
    )
    p_proxy.add_argument("--host", default=None, help="Bind address (default: configured value, normally 127.0.0.1)")
    p_proxy.add_argument("--port", type=int, default=None, help="Port (default: configured value, normally 8788)")

    # doctor — check installation health
    p_doctor = sub.add_parser("doctor", help="Check Synthelion installation health")
    p_doctor.add_argument("--json", action="store_true", help="Output as JSON")

    # install — register MCP server in agent configs
    p_install = sub.add_parser("install", help="Register Synthelion MCP server in an AI agent config")
    p_install.add_argument("--agent", choices=["claude", "gemini", "opencode", "cursor", "windsurf", "aider", "codex"], default="claude", help="Agent to configure (default: claude)")
    p_install.add_argument("--local", action="store_true", help="Install in project-local config instead of global")
    p_install.add_argument("--no-rule", action="store_true", help="Skip the mandatory privacy instruction file (Cursor Rule / Codex AGENTS.md)")
    p_install.add_argument("--no-hook", action="store_true", help="Cursor only: skip the best-effort beforeSubmitPrompt observability hook")
    p_install.add_argument("--experimental-hooks", action="store_true", help="Codex CLI only: also enable [features].codex_hooks + hooks.json — unstable, unconfirmed schema, off by default")
    p_install.add_argument("--uninstall", action="store_true", help="Remove Synthelion config instead of installing (cursor/aider/codex only)")

    # status — aggregate savings from the ledger
    p_status = sub.add_parser("status", help="Show aggregate token savings statistics")
    p_status.add_argument("--days", "-d", type=int, help="Restrict to last N days")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")

    # gain — savings history
    p_gain = sub.add_parser("gain", help="Show token savings history")
    p_gain.add_argument("--days", "-d", type=int, default=30, help="Range in days (default: 30)")
    p_gain.add_argument("--all", action="store_true", help="Show all-time records")
    p_gain.add_argument("--json", action="store_true", help="Output as JSON")

    # bench — benchmark compression on sample corpus
    p_bench = sub.add_parser("bench", help="Benchmark compression on built-in sample corpus")
    p_bench.add_argument("--json", action="store_true", help="Output as JSON")
    p_bench.add_argument("--level", "-l", choices=["light", "semantic", "aggressive", "statistical", "syntactic"], default="semantic")

    # upgrade — self-upgrade via pip
    p_upgrade = sub.add_parser("upgrade", help="Upgrade Synthelion to the latest version from PyPI")
    p_upgrade.add_argument("--dry-run", action="store_true", help="Show what would run without running it")
    p_upgrade.add_argument("--check", action="store_true", help="Report the latest PyPI version without upgrading")

    # export — export savings ledger
    p_export = sub.add_parser("export", help="Export savings ledger to CSV or JSONL")
    p_export.add_argument("--format", "-F", choices=["csv", "jsonl"], default="csv", help="Output format (default: csv)")
    p_export.add_argument("--output", "-o", help="Output file path (stdout if omitted)")
    p_export.add_argument("--days", "-d", type=int, help="Restrict to last N days")

    # configure — write the JSON config (session store / vector store / dashboard backend)
    p_conf = sub.add_parser(
        "configure",
        help="Write ~/.synthelion/config.json (session storage, RAG vector store, dashboard) for single-node or cluster deployment",
    )
    p_conf.add_argument("--session-store", choices=["local", "redis", "postgres"], help="Session/analytics storage backend")
    p_conf.add_argument("--redis-url", help="Redis connection URL, e.g. redis://host:6379/0")
    p_conf.add_argument("--postgres-dsn", help="Postgres DSN, e.g. postgresql://user:pass@host:5432/synthelion")
    p_conf.add_argument("--vector-store", choices=["chromadb", "qdrant", "lexical"], help="RAG cross-session memory backend")
    p_conf.add_argument("--qdrant-url", help="Qdrant URL, e.g. http://host:6333")
    p_conf.add_argument("--dashboard-host", help="Dashboard bind address")
    p_conf.add_argument("--dashboard-port", type=int, help="Dashboard HTTP port")
    p_conf.add_argument("--realtime", choices=["websocket", "polling"], help="Dashboard live-update mechanism")
    p_conf.add_argument("--output", "-o", help="Config file path (default: ~/.synthelion/config.json)")
    p_conf.add_argument("--show", action="store_true", help="Print the effective config and exit (no changes written)")

    # commit — generate commit message from a diff
    p_commit = sub.add_parser("commit", help="Generate a conventional commit message from a git diff")
    p_commit.add_argument("--diff", help="Diff text (or use stdin, e.g. `git diff | synthelion commit`)")
    p_commit.add_argument("--json", action="store_true")

    # review — generate PR review comments from a diff
    p_review = sub.add_parser("review", help="Generate single-line review comments from a git diff")
    p_review.add_argument("--diff", help="Diff text (or use stdin, e.g. `git diff | synthelion review`)")
    p_review.add_argument("--json", action="store_true")

    # dashboard-passwd — change the dashboard's HTTP Basic Auth login
    p_dashpw = sub.add_parser("dashboard-passwd", help="Change the dashboard login (default: admin/admin)")
    p_dashpw.add_argument("--username", "-u", help="New username (default: keep current)")
    p_dashpw.add_argument("--password", "-p", help="New password (omit to be prompted securely)")

    # loop-check — pre-tool loop guardrail (persisted across process invocations)
    p_loopck = sub.add_parser(
        "loop-check",
        help="Pre-tool loop guardrail: check whether a tool call repeats a prior one too many times",
    )
    p_loopck.add_argument("--tool", "-t", required=True, help="Name of the tool about to be called")
    p_loopck.add_argument("--args", "-a", help="JSON object of the arguments that would be passed to it")
    p_loopck.add_argument("--session", "-s", default="default", help="Session/agent id (default: 'default')")
    p_loopck.add_argument("--max-repeats", type=int, default=2, help="Identical repeats allowed before blocking (default: 2)")
    p_loopck.add_argument("--json", action="store_true", help="Output as JSON")

    # loop-reset — clear loop guardrail history for a session
    p_loopreset = sub.add_parser("loop-reset", help="Clear loop-guard call history for a session")
    p_loopreset.add_argument("--session", "-s", default="default", help="Session/agent id (default: 'default')")

    # firewall-check — EnterpriseGuard pre-tool guardrail (blocked file paths + secret-shaped content)
    p_fwck = sub.add_parser(
        "firewall-check",
        help="Pre-tool EnterpriseGuard check: block a tool call that would read a protected file or leak credentials",
    )
    p_fwck.add_argument("--tool", "-t", required=True, help="Name of the tool about to be called")
    p_fwck.add_argument("--args", "-a", help="JSON object of the arguments that would be passed to it")
    p_fwck.add_argument("--client-mac", help="Override the client identity used to look up per-client blocked paths (default: this machine's own MAC address)")
    p_fwck.add_argument("--client-ip", help="Client identity by IP instead of MAC (rarely needed for a local hook — mainly for testing a proxy-style client's policy)")
    p_fwck.add_argument("--no-client", action="store_true", help="Skip per-client policy lookup entirely, apply only the global default policy")
    p_fwck.add_argument("--json", action="store_true", help="Output as JSON")

    # clients — EnterpriseGuard per-client (IP/MAC) registry
    p_clients = sub.add_parser("clients", help="Manage EnterpriseGuard's per-client (IP/MAC) protected-path registry")
    clients_sub = p_clients.add_subparsers(dest="clients_cmd", required=True)

    clients_sub.add_parser("list", help="List all registered clients").add_argument(
        "--json", action="store_true", help="Output as JSON",
    )

    p_clients_add = clients_sub.add_parser("add", help="Register a new client")
    p_clients_add.add_argument("--label", default="", help="Human-readable name")
    p_clients_add.add_argument("--ip", default="", help="Client IP (for proxy clients)")
    p_clients_add.add_argument("--mac", default="", help="Client MAC (for local machines — use 'self' for this machine's own MAC)")
    p_clients_add.add_argument("--blocked-path", action="append", dest="blocked_paths", default=[], help="A protected-path glob pattern; repeat for more")
    p_clients_add.add_argument("--disabled", action="store_true", help="Register but leave disabled (default: enabled)")

    p_clients_update = clients_sub.add_parser("update", help="Update a registered client")
    p_clients_update.add_argument("client_id")
    p_clients_update.add_argument("--label")
    p_clients_update.add_argument("--ip")
    p_clients_update.add_argument("--mac")
    p_clients_update.add_argument("--blocked-path", action="append", dest="blocked_paths", default=None, help="Replaces the client's whole blocked-path list; repeat for more")
    p_clients_update.add_argument("--enable", action="store_true", help="Enable this client's per-client policy")
    p_clients_update.add_argument("--disable", action="store_true", help="Disable this client's per-client policy")

    p_clients_remove = clients_sub.add_parser("remove", help="Remove a registered client")
    p_clients_remove.add_argument("client_id")

    # cluster — multi-node master/slave management
    p_cluster = sub.add_parser("cluster", help="Multi-node cluster management (master/slave)")
    cluster_sub = p_cluster.add_subparsers(dest="cluster_cmd", required=True)

    cluster_sub.add_parser("init", help="Become a cluster master (generates node id + shared token)")

    p_cluster_join = cluster_sub.add_parser("join", help="Join this node to a cluster master")
    p_cluster_join.add_argument("master_url", nargs="?", help="Master's base URL, e.g. http://master-host:8787 (prompted if omitted)")
    p_cluster_join.add_argument("--token", help="Cluster shared token (prompted if omitted)")
    p_cluster_join.add_argument("--node-id", help="This node's id (auto-generated if omitted)")
    p_cluster_join.add_argument("--self-url", default="", help="This node's own reachable URL, reported to the master")

    cluster_sub.add_parser("status", help="Show this node's cluster role, and (if master) joined nodes")
    cluster_sub.add_parser("leave", help="Return this node to standalone mode")

    # retrieve — CCR (compress, cache, retrieve) reversible-compression lookup
    p_retrieve = sub.add_parser("retrieve", help="Retrieve the original text behind a [ccr:token] marker (proxy CCR feature)")
    p_retrieve.add_argument("--token", "-t", required=True, help="The token from a [ccr:xxxxxxxx] marker")
    p_retrieve.add_argument("--json", action="store_true")

    # serve-proxy is defined above; launch — start the proxy + an agent together
    p_launch = sub.add_parser("launch", help="Start the proxy and launch an agent already pointed at it, in one command")
    p_launch.add_argument("agent", choices=["claude", "codex", "aider", "cursor"], help="Agent to launch (cursor just prints the config — it's an IDE, not a spawnable CLI)")
    p_launch.add_argument("--port", type=int, default=None, help="Proxy port (default: configured value, normally 8788)")
    p_launch.add_argument("--no-proxy-start", action="store_true", help="Assume the proxy is already running; only set env vars and launch the agent")
    p_launch.add_argument("agent_args", nargs=argparse.REMAINDER, help="Extra arguments passed through to the agent's own CLI")

    # learn — mine the ledger/proxy log for deterministic, actionable patterns
    p_learn = sub.add_parser("learn", help="Scan recent activity for actionable patterns (repeated blocks, low-efficiency tools) and write them to CLAUDE.md/AGENTS.md")
    p_learn.add_argument("--days", type=int, default=7, help="Look-back window (default: 7)")
    p_learn.add_argument("--output", default="CLAUDE.md", help="File to append findings to (default: CLAUDE.md)")
    p_learn.add_argument("--dry-run", action="store_true", help="Print findings without writing the file")

    # memory — cross-agent shared memory (dedup'd notes any agent can read/write)
    p_memory = sub.add_parser("memory", help="Cross-agent shared memory — notes visible to every agent, not just the one that wrote them")
    memory_sub = p_memory.add_subparsers(dest="memory_cmd", required=True)
    p_mem_add = memory_sub.add_parser("add", help="Add a note (deduplicated by content hash)")
    p_mem_add.add_argument("--text", "-t", required=True)
    p_mem_add.add_argument("--agent", "-a", default="", help="Name of the agent/tool writing this (optional)")
    p_mem_list = memory_sub.add_parser("list", help="List recent shared-memory notes")
    p_mem_list.add_argument("--limit", type=int, default=20)
    p_mem_list.add_argument("--json", action="store_true")
    p_mem_clear = memory_sub.add_parser("clear", help="Delete all shared-memory notes")

    # wiki — generate AI-friendly project documentation
    p_wiki = sub.add_parser("wiki", help="Generate AI-friendly, compressed project documentation")
    p_wiki.add_argument("path", help="Project folder to scan")
    p_wiki.add_argument("--output", "-o", help="Output file path (stdout if omitted)")
    p_wiki.add_argument("--no-contents", action="store_true", help="Metadata/structure only, skip file contents")
    p_wiki.add_argument("--max-file-size", type=int, default=100 * 1024, help="Max file size in bytes (default: 100KB)")
    p_wiki.add_argument("--depth", type=int, choices=[1, 2, 3, 4], default=None, help="Detail level 1-4. Default: configured value (see `synthelion configure --show`), normally 2. 4 adds short code excerpts.")

    # mask-document — PII masking for PDF/Word/Excel/CSV/Markdown/plain-text files
    p_mask = sub.add_parser("mask-document", help="Mask PII in a PDF/Word/Excel/CSV/Markdown/text file (requires: pip install \"synthelion[documents]\")")
    p_mask.add_argument("path", help="Path to the input file")
    p_mask.add_argument("--output", "-o", help="Output path (default: <name>.masked.<ext> next to the input)")
    p_mask.add_argument("--language", "-L", default=None, help="ISO 639-3 code (default: from privacy config)")
    p_mask.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if args.cmd == "version":
        _cmd_version(args)
    elif args.cmd == "compress":
        _cmd_compress(args)
    elif args.cmd == "detect":
        _cmd_detect(args)
    elif args.cmd == "route":
        _cmd_route(args)
    elif args.cmd == "summarize":
        _cmd_summarize(args)
    elif args.cmd == "serve-mcp":
        from synthelion.plugins.mcp_server import main as mcp_main
        mcp_main()
    elif args.cmd == "serve-dashboard":
        from synthelion.plugins.dashboard import run_dashboard
        run_dashboard(host=args.host, port=args.port)
    elif args.cmd == "serve-proxy":
        from synthelion.config import load_config
        from synthelion.plugins.proxy import run_proxy
        pcfg = load_config().get("proxy", {})
        run_proxy(host=args.host or pcfg.get("host", "127.0.0.1"), port=args.port or pcfg.get("port", 8788))
    elif args.cmd == "status":
        _cmd_status(args)
    elif args.cmd == "gain":
        _cmd_gain(args)
    elif args.cmd == "bench":
        _cmd_bench(args)
    elif args.cmd == "doctor":
        _cmd_doctor(args)
    elif args.cmd == "install":
        _cmd_install(args)
    elif args.cmd == "upgrade":
        _cmd_upgrade(args)
    elif args.cmd == "export":
        _cmd_export(args)
    elif args.cmd == "configure":
        _cmd_configure(args)
    elif args.cmd == "commit":
        _cmd_commit(args)
    elif args.cmd == "review":
        _cmd_review(args)
    elif args.cmd == "wiki":
        _cmd_wiki(args)
    elif args.cmd == "dashboard-passwd":
        _cmd_dashboard_passwd(args)
    elif args.cmd == "loop-check":
        _cmd_loop_check(args)
    elif args.cmd == "loop-reset":
        _cmd_loop_reset(args)
    elif args.cmd == "firewall-check":
        _cmd_firewall_check(args)
    elif args.cmd == "clients":
        _cmd_clients(args)
    elif args.cmd == "cluster":
        _cmd_cluster(args)
    elif args.cmd == "retrieve":
        _cmd_retrieve(args)
    elif args.cmd == "launch":
        _cmd_launch(args)
    elif args.cmd == "learn":
        _cmd_learn(args)
    elif args.cmd == "memory":
        _cmd_memory(args)
    elif args.cmd == "mask-document":
        _cmd_mask_document(args)


def _read_input(args) -> str:
    if hasattr(args, "file") and args.file:
        with open(args.file, encoding="utf-8") as f:
            return f.read()
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print("ERROR: provide --text, --file, or pipe input via stdin", file=sys.stderr)
    raise SystemExit(1)


def _record_ledger(
    tool: str, before: int, after: int, content_type: str = "", language: str = "",
    duration_ms: float = 0.0, pii_masked_count: int = 0,
) -> None:
    """Log a CLI compression event to the same ledger the MCP server and dashboard read.

    Every user-facing command that actually compresses something (compress,
    route, summarize) goes through here, so `synthelion status` and the web
    dashboard reflect CLI/hook usage, not just MCP tool calls.
    """
    try:
        from synthelion.analytics.ledger import get_ledger
        get_ledger().record(
            tool, before, after, content_type=content_type, language=language,
            duration_ms=duration_ms, pii_masked_count=pii_masked_count,
        )
    except Exception:
        pass


def _cmd_compress(args) -> None:
    import time
    from synthelion.core import CompressionService
    from synthelion.models import CompressionLevel
    level_map = {
        "none": CompressionLevel.NONE,
        "light": CompressionLevel.LIGHT,
        "semantic": CompressionLevel.SEMANTIC,
        "aggressive": CompressionLevel.AGGRESSIVE,
        "statistical": CompressionLevel.STATISTICAL,
        "syntactic": CompressionLevel.SYNTACTIC,
    }
    if args.level is None:
        from synthelion.config import default_compression_level
        args.level = default_compression_level()
    text = _read_input(args)
    _original_text = text

    # Privacy pre-pass: this is the exact command the UserPromptSubmit hook calls
    # (see install_claude.py/.ps1/.sh) — masking PII here means it's active for
    # every hook-driven prompt from the moment it's installed, not an opt-in a user
    # has to discover. [PG_n] placeholders survive NLP compression untouched (they
    # match no stopword list in any language). `privacy.enabled = False` restores
    # exactly the pre-1.2.2 behavior.
    from synthelion.config import privacy_config
    from synthelion.privacy_analyzer import PrivacyAnalysisResult
    pcfg = privacy_config()
    privacy_masked = False
    privacy_masked_count = 0
    privacy_score = None
    privacy_risk_level = None
    privacy_categories: list[str] = []
    privacy_compliance: list[str] = []
    injection_score = None
    presult = PrivacyAnalysisResult()
    if pcfg["enabled"]:
        from synthelion.privacy_analyzer import PrivacyAnalyzer
        from synthelion.privacy_session import PrivacySession
        analyzer = PrivacyAnalyzer()
        if pcfg.get("whitelist"):
            analyzer.add_to_whitelist(*pcfg["whitelist"])
        session = PrivacySession() if pcfg["auto_masking"] else None
        presult = analyzer.analyze(text, pcfg["language"], session=session, auto_masking=pcfg["auto_masking"])
        privacy_score = presult.score
        privacy_risk_level = presult.risk_level
        privacy_categories = presult.detected_categories
        privacy_compliance = presult.compliance_flags
        if pcfg["auto_masking"] and presult.masked_text:
            text = presult.masked_text
            if presult.match_count > 0:
                privacy_masked = True
                privacy_masked_count = presult.match_count
        if pcfg["prompt_injection_guard"]:
            from synthelion.prompt_injection_guard import PromptInjectionGuard
            injection_score = PromptInjectionGuard().analyze(text).score

    transparency_notice = None
    if pcfg["enabled"] and pcfg["ai_transparency_notice"]:
        from synthelion.ai_transparency_notice import get_transparency_notice
        transparency_notice = get_transparency_notice(
            pcfg["language"], pcfg.get("transparency_custom_message") or None,
        )

    # Block-on-risk: an explicit opt-in (privacy.block_on_risk) that rejects
    # the prompt outright — via the hook's `decision: "block"` — instead of
    # masking-and-continuing, once the PII risk score crosses
    # privacy.block_min_score. See install_claude.py/.ps1/.sh: the hook
    # scripts only branch on `blocked`, all formatting happens here so the
    # three hook dialects (PowerShell/bash/local settings.json) never have to
    # duplicate this logic again.
    privacy_blocked = bool(
        pcfg["enabled"] and pcfg.get("block_on_risk")
        and privacy_score is not None and privacy_score >= pcfg.get("block_min_score", 61)
    )

    # EnterpriseGuard: outbound DLP, independent of and complementary to the
    # PII pre-pass above — credential-shaped content (cloud/database/FTP/git
    # secrets, private keys, bulk .env dumps) has no safe redacted form the
    # way PII does, so unlike privacy.block_on_risk this is always
    # block-or-allow, never mask-and-continue, whenever it fires. Checked
    # against the ORIGINAL (pre-privacy-masking) text: PrivacyGuard's own
    # masking can replace a hostname/value inside a connection string with a
    # [PG_n] placeholder, which would break EnterpriseGuard's shape match and
    # let a real secret slip through as a false negative.
    from synthelion.enterprise_guard import EnterpriseGuard
    eg_result = EnterpriseGuard().check_text(_original_text, source="cli")

    from synthelion.global_idf_provider import GlobalIdfProvider
    svc = CompressionService(global_idf=GlobalIdfProvider())
    start = time.perf_counter()
    r = svc.compress(text, level_map[args.level], iso3=args.language) if not (privacy_blocked or eg_result.blocked) else None
    duration_ms = (time.perf_counter() - start) * 1000 if r is not None else 0.0
    if r is not None:
        _record_ledger(
            "cli_compress", r.original_tokens, r.compressed_tokens, language=args.language or "",
            duration_ms=duration_ms, pii_masked_count=privacy_masked_count,
        )

    # Full human-readable breakdown — savings + complete PII/privacy note +
    # AI Act transparency notice — used both as the hook's `systemMessage`
    # (masking-and-continue path) and as the `reason` shown to the user when
    # the prompt is blocked outright, so the disclosure is identical either way.
    # `build_privacy_notice` is the single shared formatter (also used by
    # RagAgent/adapters and the MCP `compress` tool) so every agent shows the
    # same PII/AI-Act disclosure, not just Claude Code's hook.
    from synthelion.privacy_analyzer import build_privacy_notice
    pii_notice = build_privacy_notice(presult, transparency_notice, blocked=privacy_blocked)
    blocked = privacy_blocked or eg_result.blocked
    if privacy_blocked:
        notice = pii_notice
    elif eg_result.blocked:
        notice = eg_result.reason
    else:
        pct = round(r.efficiency_pct)
        savings_line = f"[Synthelion {pct}% saved - {round(r.estimated_energy_saved_mwh, 3)} mWh - {round(r.estimated_co2_saved_mg, 3)} mg CO2 saved]"
        notice = f"{savings_line}\n\n{pii_notice}" if pii_notice else savings_line

    if args.json:
        print(json.dumps({
            "compressed": r.compressed_text if r is not None else "",
            "efficiency_pct": round(r.efficiency_pct, 2) if r is not None else 0.0,
            "energy_mwh": round(r.estimated_energy_saved_mwh, 3) if r is not None else 0.0,
            "co2_mg": round(r.estimated_co2_saved_mg, 3) if r is not None else 0.0,
            "privacy_masked": privacy_masked,
            "privacy_masked_count": privacy_masked_count,
            "privacy_score": privacy_score,
            "privacy_risk_level": privacy_risk_level,
            "privacy_categories": privacy_categories,
            "privacy_compliance": privacy_compliance,
            "prompt_injection_score": injection_score,
            "ai_transparency_notice": transparency_notice,
            "blocked": blocked,
            "enterprise_guard_blocked": eg_result.blocked,
            "enterprise_guard_category": eg_result.category,
            "notice": notice,
        }))
    elif blocked:
        print(f"BLOCKED: {notice}", file=sys.stderr)
        raise SystemExit(1)
    else:
        print(r.compressed_text)
        suffix = f" — {privacy_masked_count} sensitive item(s) masked" if privacy_masked else ""
        print(f"\n[{r.efficiency_pct:.1f}% saved — {r.original_tokens} → {r.compressed_tokens} tokens{suffix}]", file=sys.stderr)


def _default_masked_output_path(path: str, output_suffix: str) -> str:
    from pathlib import Path
    p = Path(path)
    return str(p.with_name(f"{p.stem}{output_suffix}{p.suffix}"))


def _cmd_mask_document(args) -> None:
    import os
    import time
    from synthelion.config import documents_config, privacy_config
    from synthelion.privacy_analyzer import PrivacyAnalyzer
    from synthelion.privacy_session import PrivacySession
    from synthelion import document_extractor as de

    dcfg = documents_config()
    pcfg = privacy_config()
    language = args.language or pcfg.get("language", "en")

    max_bytes = dcfg["max_file_size_mb"] * 1024 * 1024
    size = os.path.getsize(args.path)
    if size > max_bytes:
        print(
            f"ERROR: file is {size / (1024 * 1024):.1f} MB, exceeds documents.max_file_size_mb ({dcfg['max_file_size_mb']})",
            file=sys.stderr,
        )
        raise SystemExit(1)

    fmt = de.detect_format(args.path)
    output_path = args.output or _default_masked_output_path(args.path, dcfg["output_suffix"])

    analyzer = PrivacyAnalyzer()
    if pcfg.get("whitelist"):
        analyzer.add_to_whitelist(*pcfg["whitelist"])
    session = PrivacySession()

    start = time.perf_counter()

    if fmt == "docx":
        doc = de.load_docx(args.path)
        de.mask_docx_in_place(doc, analyzer, session, language)
        doc.save(output_path)
    elif fmt == "xlsx":
        wb = de.load_xlsx(args.path)
        de.mask_xlsx_in_place(wb, analyzer, session, language)
        wb.save(output_path)
    else:
        from synthelion.privacy_stream import PrivacyStreamMasker
        masker = PrivacyStreamMasker(
            analyzer=analyzer, session=session, language=language, overlap_chars=dcfg["chunk_overlap_chars"],
        )
        if fmt == "pdf":
            chunks = de.extract_pdf_text(args.path)
        elif fmt == "csv":
            chunks = de.extract_csv_rows(args.path)
        else:
            chunks = de.extract_text_chunks(args.path)
        parts = [masker.feed(chunk) for chunk in chunks]
        parts.append(masker.flush())
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("".join(parts))

    duration_ms = (time.perf_counter() - start) * 1000
    masked_count = session.count
    # Redacted summary only — placeholder + category, never the original
    # values (see PrivacySession.summary()'s docstring).
    detected_categories = sorted({entry["category"] for entry in session.summary()})
    _record_ledger("cli_mask_document", 0, 0, language=language, duration_ms=duration_ms, pii_masked_count=masked_count)

    if masked_count:
        notice = f"[Synthelion] {masked_count} PII item(s) masked ({', '.join(detected_categories)}) -> {output_path}"
    else:
        notice = f"[Synthelion] No PII detected -> {output_path}"

    if args.json:
        print(json.dumps({
            "output_path": output_path,
            "masked_count": masked_count,
            "detected_categories": detected_categories,
            "notice": notice,
        }))
    else:
        print(notice)


def _cmd_version(args) -> None:
    import synthelion
    if getattr(args, "json", False):
        print(json.dumps({"version": synthelion.__version__}))
    else:
        print(f"synthelion {synthelion.__version__}")


def _cmd_detect(args) -> None:
    from synthelion.detector import LanguageDetector
    text = _read_input(args)
    det = LanguageDetector()
    if args.scores:
        scores = det.detect_with_scores(text)
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        for iso3, score in top:
            print(f"{iso3}: {score:.3f}")
    else:
        print(det.detect(text))


def _cmd_route(args) -> None:
    import time
    from synthelion.content_router import ContentRouter
    from synthelion.models import CompressionProfile
    profile_map = {
        "light": CompressionProfile.LIGHT, "balanced": CompressionProfile.BALANCED,
        "agent": CompressionProfile.AGENT, "aggressive": CompressionProfile.AGGRESSIVE,
    }
    text = _read_input(args)
    router = ContentRouter.from_profile(profile_map[args.profile])
    start = time.perf_counter()
    r = router.route(text)
    duration_ms = (time.perf_counter() - start) * 1000
    _record_ledger("cli_route", r.tokens_before, r.tokens_after, content_type=r.detected_type.value, duration_ms=duration_ms)
    if args.json:
        print(json.dumps({
            "compressed": r.compressed, "type": r.detected_type.value,
            "strategy": r.strategy_used, "savings_pct": round(r.savings_pct, 2),
        }))
    else:
        print(r.compressed)
        print(f"\n[{r.detected_type.value} → {r.strategy_used} — {r.savings_pct:.1f}% saved]", file=sys.stderr)


def _cmd_summarize(args) -> None:
    import time
    from synthelion.nlp.summarizer import TfIdfSummarizer
    from synthelion.nlp.text_rank import TextRankSummarizer
    from synthelion.global_idf_provider import GlobalIdfProvider
    text = _read_input(args)
    summ = TfIdfSummarizer(global_idf=GlobalIdfProvider()) if args.algo == "tfidf" else TextRankSummarizer()
    start = time.perf_counter()
    summary = summ.summarize(text, sentence_count=args.sentences, ratio=args.ratio)
    duration_ms = (time.perf_counter() - start) * 1000
    _record_ledger("cli_summarize", len(text.split()), len(summary.split()), content_type="summary", duration_ms=duration_ms)
    print(summary)


def _cmd_status(args) -> None:
    from synthelion.analytics.ledger import get_ledger
    ledger = get_ledger()
    days = getattr(args, "days", None)
    records = ledger.records_since(int(days)) if days else ledger.all_records()
    s = ledger.summary(records)
    if getattr(args, "json", False):
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    label = f"last {days} days" if days else "all time"
    print(f"Synthelion savings — {label}")
    print(f"  Calls         : {s['total_calls']}")
    print(f"  Tokens before : {s['tokens_before']:,}")
    print(f"  Tokens after  : {s['tokens_after']:,}")
    print(f"  Tokens saved  : {s['tokens_saved']:,}")
    print(f"  Avg efficiency: {s['avg_efficiency_pct']:.1f}%")
    cost = s.get("cost_usd_saved", 0.0)
    if cost:
        print(f"  Cost saved    : ${cost:.4f}  ({s.get('pricing_note', '')})")
    if s["by_tool"]:
        print("  By tool:")
        for tool, saved in sorted(s["by_tool"].items(), key=lambda x: x[1], reverse=True):
            print(f"    {tool}: {saved:,} tokens saved")
    if s["by_content_type"]:
        print("  By content type:")
        for ct, saved in sorted(s["by_content_type"].items(), key=lambda x: x[1], reverse=True):
            print(f"    {ct}: {saved:,} tokens saved")
    _print_star_cta()


def _cmd_gain(args) -> None:
    from synthelion.analytics.ledger import get_ledger
    ledger = get_ledger()
    if getattr(args, "all", False):
        records = ledger.all_records()
        label = "all time"
    else:
        days = getattr(args, "days", 30)
        records = ledger.records_since(days)
        label = f"last {days} days"
    s = ledger.summary(records)
    if getattr(args, "json", False):
        print(json.dumps({"range": label, **s}, ensure_ascii=False, indent=2))
        return
    cost = s.get("cost_usd_saved", 0.0)
    cost_str = f" · ${cost:.4f} saved" if cost else ""
    print(f"Synthelion gain — {label}")
    print(f"  {s['total_calls']} calls · {s['tokens_saved']:,} tokens saved · {s['avg_efficiency_pct']:.1f}% avg efficiency{cost_str}")


def _cmd_bench(args) -> None:
    """Run compression on built-in sample corpus and report savings by content type."""
    from synthelion.content_router import ContentRouter
    from synthelion.models import CompressionProfile

    corpus = _bench_corpus()
    router = ContentRouter.from_profile(CompressionProfile.BALANCED)
    results = []
    for item in corpus:
        r = router.route(item["text"])
        saved_pct = r.savings_pct
        results.append({
            "label": item["label"],
            "content_type": r.detected_type.value,
            "tokens_before": r.tokens_before,
            "tokens_after": r.tokens_after,
            "savings_pct": round(saved_pct, 1),
        })

    if getattr(args, "json", False):
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    total_before = sum(r["tokens_before"] for r in results)
    total_after = sum(r["tokens_after"] for r in results)
    mean_pct = sum(r["savings_pct"] for r in results) / len(results) if results else 0

    print("Synthelion bench — built-in corpus")
    print(f"{'Label':<30} {'Type':<20} {'Before':>8} {'After':>8} {'Saved':>8}")
    print("-" * 80)
    for r in results:
        print(f"{r['label']:<30} {r['content_type']:<20} {r['tokens_before']:>8} {r['tokens_after']:>8} {r['savings_pct']:>7.1f}%")
    print("-" * 80)
    saved = total_before - total_after
    overall_pct = (saved / total_before * 100) if total_before else 0
    print(f"{'TOTAL':<30} {'':<20} {total_before:>8} {total_after:>8} {overall_pct:>7.1f}%")
    print(f"\nMean savings per sample: {mean_pct:.1f}%")


def _bench_corpus() -> list[dict]:
    return [
        {
            # A realistically verbose request, the shape an actual user/agent
            # message takes — not a bare sentence. Hedging, politeness filler,
            # and restated context are exactly what SEMANTIC-level removes.
            "label": "plain_text_eng",
            "text": (
                "I would like to kindly ask if it would be possible for you to please "
                "take a look at the following issue that we have been experiencing "
                "with our application over the past few days. Basically, what is "
                "happening is that whenever a user attempts to submit the form on "
                "the checkout page, the request seems to time out after a certain "
                "amount of time has passed, and we are not entirely sure why this is "
                "occurring. We have tried to investigate the problem ourselves, but "
                "we were unable to determine the root cause of the issue. It would "
                "be greatly appreciated if you could review the relevant logs and "
                "provide us with some guidance on how we might be able to go about "
                "resolving this particular problem as soon as possible, since it is "
                "currently affecting a significant number of our customers."
            ),
        },
        {
            "label": "plain_text_ita",
            "text": (
                "Vorrei gentilmente chiederle se fosse possibile, quando ha un "
                "momento di tempo libero, dare un'occhiata al problema che stiamo "
                "riscontrando da diversi giorni con la nostra applicazione. In "
                "pratica, quello che sta succedendo è che ogni volta che un utente "
                "prova a inviare il modulo nella pagina di pagamento, la richiesta "
                "sembra andare in timeout dopo un certo periodo di tempo, e non "
                "siamo del tutto sicuri del motivo per cui questo stia accadendo. "
                "Abbiamo provato a indagare da soli sul problema, ma non siamo "
                "riusciti a determinare la causa principale. Le saremmo molto grati "
                "se potesse esaminare i log pertinenti e fornirci qualche "
                "indicazione su come potremmo risolvere questo particolare "
                "problema il prima possibile, dato che sta attualmente "
                "interessando un numero significativo dei nostri clienti."
            ),
        },
        {
            # 20-row array of API-response-shaped records — realistic payload
            # size for a "list users"/"list orders" tool response.
            "label": "json_array",
            "text": json.dumps([
                {
                    "id": i, "name": f"User{i}", "email": f"user{i}@example.com",
                    "age": 20 + (i % 40), "city": ["Rome", "Milan", "Naples", "Turin", "Bologna"][i % 5],
                    "active": bool(i % 2), "role": ["admin", "member", "guest"][i % 3],
                }
                for i in range(1, 21)
            ], ensure_ascii=False),
        },
        {
            # Multi-file diff with generous unchanged context — realistic PR
            # diff, the kind DiffCompressor's context-trimming actually targets.
            "label": "git_diff",
            "text": (
                "diff --git a/src/checkout/service.py b/src/checkout/service.py\n"
                "index 1234567..abcdefg 100644\n"
                "--- a/src/checkout/service.py\n"
                "+++ b/src/checkout/service.py\n"
                "@@ -1,15 +1,15 @@\n"
                " import logging\n"
                " from decimal import Decimal\n"
                " from typing import Optional\n"
                " \n"
                " logger = logging.getLogger(__name__)\n"
                " \n"
                " class CheckoutService:\n"
                "     def __init__(self, gateway, timeout=30):\n"
                "         self.gateway = gateway\n"
                "-        self.timeout = timeout\n"
                "+        self.timeout = timeout * 2\n"
                " \n"
                "     def process(self, order):\n"
                "         logger.info('Processing order %s', order.id)\n"
                "-        return self.gateway.charge(order.total, timeout=self.timeout)\n"
                "+        return self.gateway.charge(order.total, timeout=self.timeout, retries=3)\n"
                " \n"
                " def validate(order):\n"
                "     if order.total <= 0:\n"
                "         raise ValueError('Invalid total')\n"
                "diff --git a/tests/test_checkout.py b/tests/test_checkout.py\n"
                "index 89abcde..fedcba9 100644\n"
                "--- a/tests/test_checkout.py\n"
                "+++ b/tests/test_checkout.py\n"
                "@@ -20,10 +20,12 @@ def test_process_order():\n"
                "     service = CheckoutService(gateway=FakeGateway())\n"
                "     order = make_order(total=100)\n"
                "     result = service.process(order)\n"
                "-    assert result.success\n"
                "+    assert result.success\n"
                "+    assert result.retries == 3\n"
                " \n"
                " def test_validate_rejects_negative():\n"
                "     with pytest.raises(ValueError):\n"
                "         validate(make_order(total=-1))\n"
            ),
        },
        {
            # Realistically documented module — docstrings, comments, blank
            # lines make up a large share, which CodeCompressor strips.
            "label": "code_python",
            "text": (
                '"""Utility module for computing Fibonacci numbers.\n\n'
                "This module provides both a naive recursive implementation and a\n"
                "memoized version for comparison purposes in the benchmark suite.\n"
                '"""\n'
                "\n"
                "# Cache for memoized results, keyed by input value.\n"
                "_cache = {}\n"
                "\n"
                "\n"
                "def fibonacci(n):\n"
                "    # Base cases: fibonacci(0) == 0, fibonacci(1) == 1\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    # Recursive call — exponential time complexity, intentionally\n"
                "    # naive for benchmarking purposes.\n"
                "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
                "\n"
                "\n"
                "def fibonacci_memoized(n):\n"
                "    # Check the cache first to avoid redundant computation.\n"
                "    if n in _cache:\n"
                "        return _cache[n]\n"
                "    # Base cases, same as the naive version above.\n"
                "    if n <= 1:\n"
                "        return n\n"
                "    # Compute and store in cache before returning.\n"
                "    result = fibonacci_memoized(n - 1) + fibonacci_memoized(n - 2)\n"
                "    _cache[n] = result\n"
                "    return result\n"
                "\n"
                "\n"
                "# Main entry point — prints the first 10 Fibonacci numbers using\n"
                "# both implementations for comparison.\n"
                "if __name__ == '__main__':\n"
                "    for i in range(10):\n"
                "        print(fibonacci(i), fibonacci_memoized(i))\n"
            ),
        },
        {
            # A realistic burst of repeated errors from a flaky retry loop —
            # the shape LogCompressor's dedup is actually built for.
            "label": "log_stacktrace",
            "text": "".join(
                f"ERROR 2026-01-01 12:00:{i:02d} Exception in thread main\n"
                "java.lang.NullPointerException: Cannot invoke \"Order.getTotal()\" because \"order\" is null\n"
                "\tat com.example.checkout.CheckoutService.process(CheckoutService.java:42)\n"
                "\tat com.example.checkout.CheckoutController.submit(CheckoutController.java:18)\n"
                "\tat com.example.App.main(App.java:10)\n"
                for i in range(20)
            ) + "INFO  2026-01-01 12:00:20 Application started\n",
        },
        {
            # A realistic marketing/landing page — nav, footer, scripts, and
            # styling markup dominate over the actual visible text.
            "label": "html_content",
            "text": (
                "<!doctype html><html><head><title>Acme Corp — Home</title>"
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<link rel="stylesheet" href="/style.css"><script src="/analytics.js"></script>'
                "</head><body>"
                '<nav><ul><li><a href="/">Home</a></li><li><a href="/about">About</a></li>'
                '<li><a href="/pricing">Pricing</a></li><li><a href="/contact">Contact</a></li></ul></nav>'
                '<header class="hero"><h1>Welcome to Acme Corp</h1>'
                "<p>This is a paragraph with some <strong>bold</strong> and <em>italic</em> text "
                "describing what our product does and why customers should care about it.</p>"
                '<button class="cta">Get started</button></header>'
                '<section class="features"><ul>'
                "<li>Item one describing a feature</li><li>Item two describing another feature</li>"
                "<li>Item three describing a third feature</li></ul></section>"
                '<footer><p>&copy; 2026 Acme Corp. All rights reserved.</p>'
                '<ul><li><a href="/privacy">Privacy</a></li><li><a href="/terms">Terms</a></li></ul>'
                "</footer></body></html>"
            ),
        },
        {
            # New in 1.2.2 — JsonCrusher's ToolSignature strategy for
            # OpenAI/Anthropic-style tool-definition arrays.
            "label": "tool_schema_json",
            "text": json.dumps([
                {
                    "name": "search_flights",
                    "description": "Search for available flights between two airports on a given date, optionally filtering by cabin class and maximum number of stops.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "origin": {"type": "string", "description": "IATA airport code of the departure airport."},
                            "destination": {"type": "string", "description": "IATA airport code of the arrival airport."},
                            "date": {"type": "string", "description": "Departure date in YYYY-MM-DD format."},
                            "cabin_class": {"type": "string", "description": "Preferred cabin class: economy, premium_economy, business, or first."},
                            "max_stops": {"type": "integer", "description": "Maximum number of layovers allowed."},
                        },
                        "required": ["origin", "destination", "date"],
                    },
                },
                {
                    "name": "get_weather_forecast",
                    "description": "Retrieve the multi-day weather forecast for a given city, including temperature, precipitation probability, and wind speed.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "Name of the city to fetch the forecast for."},
                            "days": {"type": "integer", "description": "Number of forecast days to return."},
                            "units": {"type": "string", "description": "Measurement units: metric or imperial."},
                        },
                        "required": ["city"],
                    },
                },
            ], ensure_ascii=False),
        },
        {
            # New in 1.2.2 — JsonCrusher's ChainCollapse strategy for single JSON
            # objects with deep single-child nesting (config-shaped data).
            "label": "nested_json_object",
            "text": json.dumps({
                "application": {
                    "server": {
                        "network": {
                            "listener": {
                                "bind_address": "0.0.0.0",
                            },
                        },
                    },
                },
                "logging": {"level": "info", "format": "json"},
            }),
        },
    ]


def run_doctor_checks() -> list[dict]:
    """Health check: verify MCP package, ledger, session DB, and installation.

    Shared by `synthelion doctor` and the dashboard's Settings > System panel
    (`GET /api/doctor` in dashboard.py) so both surfaces run the exact same
    checks instead of two copies drifting apart.
    """
    import shutil
    from pathlib import Path

    checks: list[dict] = []

    # 1. MCP package
    try:
        import mcp
        checks.append({"check": "mcp package", "status": "ok", "detail": getattr(mcp, "__version__", "installed")})
    except ImportError:
        checks.append({"check": "mcp package", "status": "error", "detail": "not installed — run: pip install mcp"})

    # 2. Synthelion version
    try:
        import synthelion
        checks.append({"check": "synthelion", "status": "ok", "detail": f"v{synthelion.__version__}"})
    except Exception as e:
        checks.append({"check": "synthelion", "status": "error", "detail": str(e)})

    # 3. Savings ledger
    try:
        from synthelion.analytics.ledger import get_ledger
        ledger = get_ledger()
        s = ledger.summary()
        checks.append({"check": "savings ledger", "status": "ok", "detail": f"{s['total_calls']} calls recorded"})
    except Exception as e:
        checks.append({"check": "savings ledger", "status": "error", "detail": str(e)})

    # 4. Session DB
    try:
        from synthelion.analytics.session_db import get_session_db
        db = get_session_db()
        checks.append({"check": "session db", "status": "ok", "detail": f"backend={db.backend()}"})
    except Exception as e:
        checks.append({"check": "session db", "status": "error", "detail": str(e)})

    # 5. synthelion-mcp in PATH
    mcp_cmd = shutil.which("synthelion-mcp")
    if mcp_cmd:
        checks.append({"check": "synthelion-mcp in PATH", "status": "ok", "detail": mcp_cmd})
    else:
        checks.append({"check": "synthelion-mcp in PATH", "status": "warn", "detail": "not found — run: pip install synthelion"})

    # 6. Claude Code global config
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        try:
            import json as _json
            cfg = _json.loads(claude_json.read_text(encoding="utf-8"))
            if "synthelion" in cfg.get("mcpServers", {}):
                checks.append({"check": "claude code mcp", "status": "ok", "detail": str(claude_json)})
            else:
                checks.append({"check": "claude code mcp", "status": "warn", "detail": "synthelion not in ~/.claude.json — run: synthelion install"})
        except Exception:
            checks.append({"check": "claude code mcp", "status": "warn", "detail": "could not read ~/.claude.json"})
    else:
        checks.append({"check": "claude code mcp", "status": "warn", "detail": "~/.claude.json not found — run: synthelion install"})

    return checks


def _print_star_cta() -> None:
    """Small, one-line reminder shown at the end of human-readable CLI output
    (never in --json mode, which must stay machine-parseable) — the CLI is
    often the only place a happy user ever sees Synthelion's name."""
    print("\n⭐ Enjoying Synthelion? Star us on GitHub: https://github.com/francescopaolopassaro/synthelion")


def _cmd_doctor(args) -> None:
    checks = run_doctor_checks()

    if getattr(args, "json", False):
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return

    has_error = any(c["status"] == "error" for c in checks)
    print("Synthelion doctor")
    for c in checks:
        icon = {"ok": "✓", "warn": "!", "error": "✗"}.get(c["status"], "?")
        print(f"  [{icon}] {c['check']}: {c['detail']}")
    if has_error:
        print("\nSome checks failed. Run with --json for structured output.")
        _print_star_cta()
        raise SystemExit(1)
    else:
        print("\nAll checks passed.")
        _print_star_cta()


def _install_opencode_plugin(path: Path) -> None:
    _PLUGIN_CODE = r"""import { type Plugin } from "@opencode-ai/plugin"

const MIN_LEN = 10
const MIN_EFF = 15

async function syn($: any, subcmd: string, flags: Record<string, any> = {}): Promise<any> {
  const args: string[] = [subcmd, "--json"]
  for (const [k, v] of Object.entries(flags)) {
    if (v === undefined || v === null) continue
    const flag = k.length === 1 ? `-${k}` : `--${k.replace(/_/g, "-")}`
    args.push(flag)
    if (v !== true) args.push(typeof v === "string" ? v : JSON.stringify(v))
  }
  for (const bin of ["synthelion", "python -m synthelion.cli"]) {
    try {
      const out = await $`${bin} ${args}`.text()
      const parsed = JSON.parse(out.trim())
      if (parsed.error) continue
      return parsed
    } catch {}
  }
  return { error: "synthelion not available" }
}

export const SynthelionPlugin: Plugin = async (ctx) => {
  const { $ } = ctx

  return {
    "chat.message": async (_input, output) => {
      const textParts = output.parts.filter(
        (p): p is { type: "text"; text: string } =>
          "text" in p && typeof (p as any).text === "string",
      )
      if (textParts.length === 0) return
      const fullText = textParts.map((p) => p.text).join("\n").trim()
      if (fullText.length < MIN_LEN) return
      try {
        const r = await syn($, "compress", { text: fullText })
        if (r.error || !r.compressed_text || r.efficiency_pct <= MIN_EFF) return
        for (const p of textParts) p.text = r.compressed_text
        output.parts.push({
          type: "text",
          text: `\n\n[⚡ Synthelion: ${r.original_tokens}→${r.compressed_tokens} tok, ${r.efficiency_pct}% saved]`,
        })
      } catch {}
    },

    "experimental.chat.system.transform": async (_input, output) => {
      const text = output.system.join("\n")
      if (text.length < 50) return
      try {
        const r = await syn($, "shape_output", { system_prompt: text, level: "no_restatement" })
        if (r.system_prompt) output.system = [r.system_prompt]
      } catch {}
    },

    "experimental.chat.messages.transform": async (_input, output) => {
      const msgs = output.messages
      if (msgs.length === 0) return

      const last = msgs[msgs.length - 1]
      if (last.info.role === "user") {
        const text = last.parts
          .filter((p): p is { type: "text"; text: string } => "text" in p && typeof (p as any).text === "string")
          .map((p) => p.text)
          .join("\n")
          .trim()
        if (text.length >= MIN_LEN) {
          try {
            const r = await syn($, "compress", { text })
            if (!r.error && r.blocked) {
              last.parts = [{ type: "text", text: r.notice || "[Blocked by Synthelion: PII detected]" }]
              return
            }
            if (!r.error && r.privacy_masked && r.compressed_text) {
              for (const p of last.parts) {
                if ("text" in p && typeof (p as any).text === "string") (p as any).text = r.compressed_text
              }
            }
          } catch {}
        }
      }

      if (msgs.length < 6) return
      const keep = Math.max(2, Math.floor(msgs.length / 2))
      const old = msgs.slice(0, -keep)
      if (old.length === 0) return
      const oldText = old.map((m) => `${m.info.role}: ${m.parts.filter((p) => "text" in p).map((p: any) => p.text).join(" ")}`).join("\n")
      if (oldText.length < 100) return
      try {
        const r = await syn($, "compress", { text: oldText, level: "aggressive" })
        if (r.compressed_text && r.efficiency_pct > 20) {
          const tail = msgs.slice(-keep)
          output.messages = [{
            info: { role: "system" } as any,
            parts: [{ type: "text", text: `[Compressed conversation — ${r.original_tokens}→${r.compressed_tokens} tok, ${r.efficiency_pct}% saved]\n${r.compressed_text}` }],
          }, ...tail]
        }
      } catch {}
    },

    "experimental.session.compacting": async (_input, output) => {
      output.context.push("## Synthelion Plugin\nActive Synthelion MCP tools: compression, PII masking, summarization, and more.")
    },
  }
}
"""
    path.write_text(_PLUGIN_CODE.strip(), encoding="utf-8")


def _cmd_install(args) -> None:
    """Register Synthelion MCP server in an AI agent config."""
    import json as _json
    import shutil
    from pathlib import Path

    agent = getattr(args, "agent", "claude")
    local = getattr(args, "local", False)

    mcp_cmd = shutil.which("synthelion-mcp") or "synthelion-mcp"
    mcp_entry = {"command": mcp_cmd, "args": []}

    if agent == "claude":
        if local:
            config_path = Path(".claude") / "settings.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            config_path = Path.home() / ".claude.json"

        # Read or create
        if config_path.exists():
            try:
                cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        else:
            cfg = {}

        cfg.setdefault("mcpServers", {})["synthelion"] = mcp_entry
        config_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Synthelion MCP registered in {config_path}")
        print("  Restart Claude Code to activate.")

    elif agent == "gemini":
        if local:
            config_path = Path(".gemini") / "settings.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            config_path = Path.home() / ".gemini" / "settings.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            try:
                cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        else:
            cfg = {}

        cfg.setdefault("mcpServers", {})["synthelion"] = mcp_entry
        config_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Synthelion MCP registered in {config_path}")
        print("  Restart Gemini CLI to activate.")

    elif agent == "opencode":
        # OpenCode config schema differs from Claude/Gemini: MCP servers live
        # under "mcp", each entry needs an explicit "type" and "command" as an
        # array (not a single string + args list). Config files are merged by
        # OpenCode itself, so we only need to merge our own key in here.
        if local:
            config_path = Path("opencode.json")
        else:
            config_path = Path.home() / ".config" / "opencode" / "opencode.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            try:
                cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        else:
            cfg = {"$schema": "https://opencode.ai/config.json"}

        cfg.setdefault("mcp", {})["synthelion"] = {
            "type": "local",
            "command": [mcp_cmd],
            "enabled": True,
        }
        config_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Synthelion MCP registered in {config_path}")

        # Install the Synthelion plugin with auto-compression + PII hook
        if local:
            plugin_dir = Path(".opencode") / "plugins"
        else:
            plugin_dir = Path.home() / ".config" / "opencode" / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin_file = plugin_dir / "synthelion.ts"
        if not plugin_file.exists():
            _install_opencode_plugin(plugin_file)
            print(f"✓ Synthelion plugin installed in {plugin_file}")
        else:
            print(f"  (plugin already exists at {plugin_file} — delete and re-run to reinstall)")
        print()
        print("  Next steps:")
        print("  1. Restart OpenCode (or run `opencode mcp list`) to activate MCP tools.")
        print("  2. The plugin auto-compresses every prompt and masks PII.")
        print("  3. Run `opencode doctor` to verify everything loaded.")

    elif agent == "windsurf":
        # Reads a plain { "mcpServers": {...} } file, same shape as Claude —
        # no project-local variant documented well enough to rely on, so
        # --local is a no-op here.
        config_path = Path.home() / ".codeium" / "windsurf" / "mcp_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        if config_path.exists():
            try:
                cfg = _json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                cfg = {}
        else:
            cfg = {}

        cfg.setdefault("mcpServers", {})["synthelion"] = mcp_entry
        config_path.write_text(_json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"✓ Synthelion MCP registered in {config_path}")
        print("  Restart Windsurf to activate.")

    elif agent == "cursor":
        # MCP + a mandatory privacy Rule (.mdc) the model can call, + a
        # best-effort beforeSubmitPrompt hook. Cursor's hook is documented as
        # informational-only in the current beta (it can't block/rewrite the
        # prompt), so enforcement here still depends on the model choosing to
        # call the MCP tool — same limitation as any tool-use-only agent.
        from synthelion.integrations import cursor as cursor_integration

        if getattr(args, "uninstall", False):
            removed = cursor_integration.remove(local=local)
            print(f"✓ Removed from Cursor: {removed or 'nothing found'}")
            return

        written = cursor_integration.configure(
            binary=mcp_cmd, local=local,
            add_rule=not getattr(args, "no_rule", False),
            add_hook=not getattr(args, "no_hook", False),
        )
        print(f"✓ Synthelion MCP registered in {written['mcp_path']}")
        if "rule_path" in written:
            print(f"✓ Privacy rule installed in {written['rule_path']}")
        if "hooks_path" in written:
            print(f"  beforeSubmitPrompt hook wired in {written['hooks_path']}")
            print("  NOTE: Cursor's beta does not act on hook output — this only logs")
            print("  usage to Synthelion's ledger, it cannot block/rewrite prompts.")
        print("  Restart Cursor to activate.")

    elif agent == "aider":
        # No MCP client, no pre-send hook — the only real lever is an
        # advisory conventions file loaded via `read:` in .aider.conf.yml.
        # This cannot mask or block anything automatically; say so plainly.
        from synthelion.integrations import aider as aider_integration

        if getattr(args, "uninstall", False):
            removed = aider_integration.remove(local=local)
            print(f"✓ Removed from Aider: {removed or 'nothing found'}")
            return

        written = aider_integration.configure(local=local)
        print(f"✓ Conventions file written to {written['conventions_path']}")
        print(f"✓ Registered in {written['conf_path']}")
        print()
        print("  NOTE: this is advisory only. Aider has no MCP client and no")
        print("  pre-send hook, so nothing is actually masked or blocked —")
        print("  the model is only asked to warn the user and suggest running")
        print("  `synthelion compress` manually. For real enforcement, use")
        print("  Claude Code, Cursor, or Codex CLI instead.")

    elif agent == "codex":
        # MCP (`[mcp_servers.synthelion]` in config.toml) + AGENTS.md
        # instructions by default. The experimental hook engine
        # (`[features].codex_hooks` + hooks.json) is only wired when
        # --experimental-hooks is passed — its schema is still marked
        # "under development" upstream and unconfirmed by official docs.
        from synthelion.integrations import codex as codex_integration

        if getattr(args, "uninstall", False):
            removed = codex_integration.remove()
            print(f"✓ Removed from Codex CLI: {removed or 'nothing found'}")
            return

        written = codex_integration.configure(
            binary=mcp_cmd,
            add_agents=not getattr(args, "no_rule", False),
            experimental_hooks=getattr(args, "experimental_hooks", False),
        )
        print(f"✓ Synthelion MCP registered in {written['config_path']}")
        if "agents_md_path" in written:
            print(f"✓ Privacy instructions written to {written['agents_md_path']}")
        if written.get("experimental"):
            print(f"  [EXPERIMENTAL] hook wired in {written['hooks_path']} — schema unconfirmed,")
            print("  verify with `codex /hooks` inside a session; may do nothing on your build.")
        print("  Restart Codex CLI (or run `codex mcp list`) to activate.")

    else:
        print(f"ERROR: unsupported agent '{agent}'. Supported: claude, gemini, opencode, cursor, windsurf, aider, codex", file=sys.stderr)
        raise SystemExit(1)


def check_pypi_version(timeout: float = 8.0) -> dict:
    """Explicit, on-demand PyPI version check — never called automatically on a
    page load or CLI startup, only in response to a direct user action
    ("Check for updates" in the dashboard, `synthelion upgrade` on the CLI),
    consistent with Synthelion otherwise making zero outbound network calls.
    """
    import urllib.request

    import synthelion
    current = synthelion.__version__
    try:
        with urllib.request.urlopen("https://pypi.org/pypi/synthelion/json", timeout=timeout) as resp:
            info = json.loads(resp.read())
        latest = info["info"]["version"]
    except Exception as exc:
        return {"current": current, "latest": None, "update_available": False, "error": str(exc)}

    def _tuple(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return (0,)

    return {
        "current": current,
        "latest": latest,
        "update_available": _tuple(latest) > _tuple(current),
    }


def _detect_install_method() -> str:
    """Returns 'editable' | 'pipx' | 'uv' | 'pip' — how this Synthelion
    install got here, so `synthelion upgrade` can run the command that
    actually works instead of always guessing `pip install --upgrade`
    (which silently no-ops for an editable/git checkout, and works but isn't
    idiomatic for pipx/uv-tool installs, which have their own upgrade
    commands that also refresh the shim)."""
    exe_lower = sys.executable.replace("\\", "/").lower()
    if "pipx" in exe_lower:
        return "pipx"
    if "/uv/tools/" in exe_lower or "uv/tool" in exe_lower:
        return "uv"
    try:
        import importlib.metadata as im
        dist = im.distribution("synthelion")
        direct_url_text = dist.read_text("direct_url.json") or ""
        if '"editable"' in direct_url_text and "true" in direct_url_text.lower():
            return "editable"
    except Exception:
        pass
    # Fallback: some editable installs (older setuptools `develop`-style, or
    # metadata without a direct_url.json) don't self-report — but the actual
    # imported module still resolves to the source checkout, not a copy
    # inside site-packages, which is the real thing we care about anyway
    # ("does `pip install --upgrade` even change what's running").
    try:
        from pathlib import Path
        import synthelion as _syn
        module_path = Path(_syn.__file__).resolve()
        if "site-packages" not in str(module_path).replace("\\", "/"):
            return "editable"
    except Exception:
        pass
    return "pip"


def _cmd_upgrade(args) -> None:
    """Upgrade Synthelion — detects how it was installed (pip/pip --user,
    pipx, uv tool, or an editable/git checkout) and runs the command that
    actually applies for that install, rather than always shelling out to
    `pip install --upgrade` and leaving editable/pipx/uv installs either
    silently unchanged or upgraded the "wrong" (non-idiomatic) way."""
    import subprocess

    if getattr(args, "check", False):
        info = check_pypi_version()
        if info.get("error"):
            print(f"Could not reach PyPI: {info['error']}", file=sys.stderr)
            raise SystemExit(1)
        if info["update_available"]:
            print(f"Update available: v{info['current']} -> v{info['latest']}")
        else:
            print(f"Up to date (v{info['current']}).")
        return

    method = _detect_install_method()

    if method == "editable":
        print("Editable install detected (pip install -e .) — Synthelion is running")
        print("directly from a source checkout, not a packaged release.")
        print("Update by pulling the latest source instead: git pull")
        return
    if method == "pipx":
        cmd, desc = ["pipx", "upgrade", "synthelion"], "pipx upgrade synthelion"
    elif method == "uv":
        cmd, desc = ["uv", "tool", "upgrade", "synthelion"], "uv tool upgrade synthelion"
    else:
        cmd, desc = [sys.executable, "-m", "pip", "install", "--upgrade", "synthelion"], f"{sys.executable} -m pip install --upgrade synthelion"

    if getattr(args, "dry_run", False):
        print(f"Detected install method: {method}")
        print(f"Would run: {desc}")
        return

    print(f"Detected install method: {method}")
    print(f"Upgrading via: {desc}")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        import importlib
        import synthelion as _syn
        importlib.reload(_syn)
        print(f"\n[OK] Synthelion {_syn.__version__} installed.")
        print("Restart your agent or MCP server to activate the new version.")
    else:
        print("\n[X] Upgrade failed.", file=sys.stderr)
        raise SystemExit(result.returncode)


def _cmd_export(args) -> None:
    """Export savings ledger to CSV or JSONL."""
    import csv
    import io
    from synthelion.analytics.ledger import get_ledger
    ledger = get_ledger()
    days = getattr(args, "days", None)
    records = ledger.records_since(int(days)) if days else ledger.all_records()
    fmt = getattr(args, "format", "csv")
    output_path = getattr(args, "output", None)

    if not records:
        print("No records found.", file=sys.stderr)
        return

    if fmt == "jsonl":
        content = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    else:
        buf = io.StringIO()
        # Collect all unique keys across all records (different records may have different fields)
        all_keys: list[str] = []
        seen: set[str] = set()
        for rec in records:
            for k in rec:
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)
        writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        content = buf.getvalue()

    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"[OK] Exported {len(records)} records to {output_path}")
    else:
        sys.stdout.write(content)


def _cmd_configure(args) -> None:
    """Write ~/.synthelion/config.json — session storage / RAG vector store /
    dashboard backend, for single-node or cluster (Redis/Postgres-backed) deployment."""
    from pathlib import Path
    from synthelion.config import config_path, default_config_path, load_config, save_config

    if args.show:
        current = load_config()
        print(json.dumps(current, indent=2, ensure_ascii=False))
        existing = config_path()
        print(f"\n# source: {existing if existing else '(built-in defaults, no config file found)'}", file=sys.stderr)
        return

    cfg = load_config()  # start from whatever's already configured (or defaults)

    if args.session_store:
        cfg["session_store"]["backend"] = args.session_store
    if args.redis_url:
        cfg["session_store"]["redis"]["url"] = args.redis_url
    if args.postgres_dsn:
        cfg["session_store"]["postgres"]["dsn"] = args.postgres_dsn
    if args.vector_store:
        cfg["vector_store"]["backend"] = args.vector_store
    if args.qdrant_url:
        cfg["vector_store"]["qdrant"]["url"] = args.qdrant_url
    if args.dashboard_host:
        cfg["dashboard"]["host"] = args.dashboard_host
    if args.dashboard_port:
        cfg["dashboard"]["port"] = args.dashboard_port
    if args.realtime:
        cfg["dashboard"]["realtime"] = args.realtime

    target = Path(args.output) if args.output else default_config_path()
    written = save_config(cfg, target)

    print(f"[OK] Wrote configuration to {written}")
    print(f"  session_store: {cfg['session_store']['backend']}")
    print(f"  vector_store:  {cfg['vector_store']['backend']}")
    print(f"  dashboard:     {cfg['dashboard']['host']}:{cfg['dashboard']['port']} "
          f"(realtime={cfg['dashboard']['realtime']})")

    backend = cfg["session_store"]["backend"]
    if backend == "redis":
        print("\nInstall the Redis client: pip install 'synthelion[redis]'")
    elif backend == "postgres":
        print("\nInstall the Postgres client: pip install 'synthelion[postgres]'")
    if cfg["vector_store"]["backend"] == "qdrant":
        print("Install the Qdrant client: pip install 'synthelion[qdrant]'")
    print("\nSet SYNTHELION_CONFIG=<path> on every node to point them all at a shared "
          "config file (e.g. a mounted Kubernetes ConfigMap) if you'd rather not rely on "
          "each node writing its own ~/.synthelion/config.json.")


def _read_diff_input(args) -> str:
    if getattr(args, "diff", None):
        return args.diff
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print("ERROR: provide --diff or pipe a diff via stdin, e.g. `git diff | synthelion commit`", file=sys.stderr)
    raise SystemExit(1)


def _cmd_commit(args) -> None:
    from synthelion.devtools.commit_generator import CommitGenerator
    diff = _read_diff_input(args)
    s = CommitGenerator().generate_from_diff(diff)
    if args.json:
        print(json.dumps({"message": s.full_message, "type": s.type, "scope": s.scope, "subject": s.subject}))
    else:
        print(s.full_message)


def _cmd_review(args) -> None:
    from synthelion.devtools.review_service import ReviewService
    diff = _read_diff_input(args)
    r = ReviewService().review_diff(diff)
    if args.json:
        print(json.dumps({
            "changed_files": r.changed_files, "additions": r.additions, "deletions": r.deletions,
            "total_issues": r.total_issues,
            "comments": [{"line": c.line, "severity": c.severity, "message": c.message} for c in r.comments],
        }, ensure_ascii=False))
        return
    print(f"Reviewed diff: {r.changed_files} file(s), +{r.additions}/-{r.deletions}, {r.total_issues} issue(s)")
    for c in r.comments:
        print(f"  {c}")


def _cmd_retrieve(args) -> None:
    """CCR lookup: the proxy (synthelion/plugins/proxy.py) appends a
    `[ccr:xxxxxxxx]` marker after any string it compressed away enough of,
    when `proxy.ccr_enabled` is on. Call this with that token to get the
    original text back — same idea as PII's `[PG_n]` placeholders, but for
    ordinary lossy compression instead of privacy masking."""
    from synthelion.analytics.ccr_store import get_ccr_store
    original = get_ccr_store().retrieve(args.token)
    if original is None:
        if args.json:
            print(json.dumps({"error": "token not found or expired"}))
        else:
            print("ERROR: token not found or expired (CCR entries expire after proxy.ccr_ttl_seconds)", file=sys.stderr)
        raise SystemExit(1)
    if args.json:
        print(json.dumps({"original": original}, ensure_ascii=False))
    else:
        print(original)


def _cmd_launch(args) -> None:
    """Start the proxy (unless --no-proxy-start) and launch an agent with its
    base-URL env var already pointed at it — the single-command equivalent of
    running `synthelion serve-proxy` in one terminal and setting
    ANTHROPIC_BASE_URL/OPENAI_BASE_URL by hand in another."""
    import os
    import shutil
    import subprocess
    import time as _time

    from synthelion.config import load_config

    pcfg = load_config().get("proxy", {})
    port = args.port or pcfg.get("port", 8788)
    host = pcfg.get("host", "127.0.0.1")
    base_url = f"http://{host}:{port}"

    if args.agent == "cursor":
        print("Cursor is an IDE, not a spawnable CLI — set this in Cursor's MCP/model settings:")
        print(f"  base URL: {base_url}")
        print("Then start the proxy yourself: synthelion serve-proxy")
        return

    if not args.no_proxy_start:
        print(f"Starting proxy on {base_url} …")
        subprocess.Popen(
            [sys.executable, "-m", "synthelion.cli", "serve-proxy", "--host", host, "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _time.sleep(0.8)  # give it a moment to bind before the agent's first request
    else:
        print(f"Assuming proxy already running on {base_url} …")

    env = os.environ.copy()
    if args.agent == "claude":
        env["ANTHROPIC_BASE_URL"] = base_url
        binary = shutil.which("claude")
        cmd = [binary or "claude"]
    elif args.agent == "codex":
        env["OPENAI_BASE_URL"] = base_url
        binary = shutil.which("codex")
        cmd = [binary or "codex"]
    elif args.agent == "aider":
        env["OPENAI_API_BASE"] = base_url  # aider's own var name for this
        env["OPENAI_BASE_URL"] = base_url
        binary = shutil.which("aider")
        cmd = [binary or "aider"]
    else:
        print(f"ERROR: unsupported agent '{args.agent}'", file=sys.stderr)
        raise SystemExit(1)

    extra = getattr(args, "agent_args", None) or []
    if extra and extra[0] == "--":
        extra = extra[1:]
    cmd += extra

    if not (binary or shutil.which(cmd[0])):
        print(f"ERROR: '{cmd[0]}' not found in PATH — install it first.", file=sys.stderr)
        raise SystemExit(1)

    print(f"Launching: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=env)
    raise SystemExit(result.returncode)


def _cmd_learn(args) -> None:
    """Deterministic pattern mining over the savings ledger and proxy log —
    no ML, no fabricated insights: only things actually true about recent
    activity (repeated privacy blocks on the same category, tools with
    below-average compression efficiency). Writes a plain markdown section
    to CLAUDE.md/AGENTS.md, same "AI-friendly project doc" spirit as
    `synthelion wiki`, not an autonomous rewrite of the file."""
    from synthelion.analytics.ledger import get_ledger
    from synthelion.analytics.proxy_log import get_proxy_log

    records = get_ledger().records_since(args.days)
    proxy_records = [r for r in get_proxy_log().recent(2000) if r.get("ts")]

    findings: list[str] = []

    by_tool: dict[str, list[float]] = {}
    for r in records:
        before, after = r.get("tokens_before", 0), r.get("tokens_after", 0)
        if before:
            by_tool.setdefault(r.get("tool", "unknown"), []).append((before - after) / before * 100)
    for tool, effs in by_tool.items():
        avg = sum(effs) / len(effs)
        if avg < 15 and len(effs) >= 5:
            findings.append(f"- `{tool}` has averaged only {avg:.0f}% compression over {len(effs)} calls in the last {args.days} days — likely already-terse input, or a content type this level under-compresses.")

    blocked = [r for r in proxy_records if r.get("blocked")]
    if len(blocked) >= 3:
        findings.append(f"- The proxy blocked {len(blocked)} requests for privacy risk in the last {args.days} days — if these are expected/whitelisted values, consider adding them to `privacy.whitelist`.")

    failed = [r for r in proxy_records if not r.get("responded")]
    if len(failed) >= 3:
        upstreams = {}
        for r in failed:
            upstreams[r.get("upstream", "?")] = upstreams.get(r.get("upstream", "?"), 0) + 1
        worst = max(upstreams, key=upstreams.get)
        findings.append(f"- {upstreams[worst]} requests to `{worst}` failed to get a response in the last {args.days} days — consider adding a backup in `proxy.fallback_upstreams`.")

    if not findings:
        print(f"No actionable patterns found in the last {args.days} days.")
        return

    section = "\n## Synthelion — observed patterns\n\n" + "\n".join(findings) + "\n"
    if args.dry_run:
        print(section)
        return

    from pathlib import Path
    path = Path(args.output)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = "## Synthelion — observed patterns"
    if marker in existing:
        import re
        existing = re.sub(re.escape(marker) + r".*?(?=\n## |\Z)", section.strip() + "\n", existing, flags=re.DOTALL)
    else:
        existing = existing.rstrip() + "\n" + section if existing.strip() else section.lstrip()
    path.write_text(existing, encoding="utf-8")
    print(f"[OK] Wrote {len(findings)} finding(s) to {path}")


def _cmd_memory(args) -> None:
    from synthelion.analytics.shared_memory import get_shared_memory

    mem = get_shared_memory()
    if args.memory_cmd == "add":
        added = mem.add(args.text, agent=args.agent)
        print("[OK] Added." if added else "Already recorded (deduplicated).")
    elif args.memory_cmd == "list":
        notes = mem.recent(args.limit)
        if args.json:
            print(json.dumps(notes, ensure_ascii=False))
            return
        if not notes:
            print("No shared-memory notes yet.")
            return
        for n in notes:
            tag = f" [{n['agent']}]" if n.get("agent") else ""
            print(f"{n['ts']}{tag}  {n['text']}")
    elif args.memory_cmd == "clear":
        removed = mem.clear()
        print(f"[OK] Removed {removed} note(s).")


def _cmd_wiki(args) -> None:
    from synthelion.devtools.wiki import ProjectWiki
    if args.depth is None:
        from synthelion.config import default_wiki_depth
        args.depth = default_wiki_depth()
    wiki = ProjectWiki()
    markdown = wiki.generate(
        args.path,
        max_file_size_bytes=args.max_file_size,
        include_contents=not args.no_contents,
        depth=args.depth,
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(markdown)
        print(f"[OK] Wrote project wiki to {args.output}")
    else:
        print(markdown)


def _cmd_cluster(args) -> None:
    if args.cluster_cmd == "init":
        _cmd_cluster_init(args)
    elif args.cluster_cmd == "join":
        _cmd_cluster_join(args)
    elif args.cluster_cmd == "status":
        _cmd_cluster_status(args)
    elif args.cluster_cmd == "leave":
        _cmd_cluster_leave(args)


def _cmd_cluster_init(args) -> None:
    from synthelion.config import load_config, new_cluster_token, new_node_id, save_config
    cfg = load_config()
    cfg["cluster"]["role"] = "master"
    cfg["cluster"]["node_id"] = cfg["cluster"]["node_id"] or new_node_id()
    cfg["cluster"]["node_token"] = cfg["cluster"]["node_token"] or new_cluster_token()
    save_config(cfg)
    print("[OK] This node is now a cluster master.")
    print(f"  node_id: {cfg['cluster']['node_id']}")
    print(f"  token:   {cfg['cluster']['node_token']}")
    print("\nOn another node, run:")
    print(f"  synthelion cluster join http://<this-node-host>:8787 --token {cfg['cluster']['node_token']}")


def _cmd_cluster_join(args) -> None:
    from synthelion.cluster import ClusterJoinError, join_master
    from synthelion.config import load_config, new_node_id, save_config

    master_url = args.master_url
    if not master_url:
        master_url = input("Master URL to join (leave empty to stay standalone): ").strip()
        if not master_url:
            print("Staying standalone. Run `synthelion cluster init` to become a master instead.")
            return

    token = args.token
    if not token:
        import getpass
        token = getpass.getpass("Cluster token: ")
    if not token:
        print("ERROR: a cluster token is required", file=sys.stderr)
        raise SystemExit(1)

    cfg = load_config()
    node_id = args.node_id or cfg["cluster"]["node_id"] or new_node_id()

    try:
        result = join_master(master_url, token, node_id, args.self_url)
    except ClusterJoinError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)

    cfg["cluster"]["role"] = "slave"
    cfg["cluster"]["node_id"] = node_id
    cfg["cluster"]["node_token"] = token
    cfg["cluster"]["master_url"] = master_url.rstrip("/")
    cfg["cluster"]["self_url"] = args.self_url
    shared = result.get("config") or {}
    if "compression" in shared:
        cfg["compression"] = shared["compression"]
    if "wiki" in shared:
        cfg["wiki"] = shared["wiki"]
    save_config(cfg)
    print(f"[OK] Joined cluster as '{node_id}' (master: '{result.get('master_node_id', '?')}').")
    print("Copied the master's compression/wiki defaults into this node's config.")


def _cmd_cluster_status(args) -> None:
    import time as _time
    from synthelion.config import load_config
    cfg = load_config()["cluster"]
    print(f"Role: {cfg['role']}")
    if cfg["role"] == "standalone":
        print("Not part of a cluster. `synthelion cluster init` to become a master, "
              "or `synthelion cluster join <url>` to join one.")
        return
    print(f"Node ID: {cfg['node_id']}")
    if cfg["role"] == "slave":
        print(f"Master: {cfg['master_url']}")
        return
    from synthelion.analytics.cluster_registry import get_cluster_registry
    nodes = get_cluster_registry().list_nodes()
    if not nodes:
        print("No nodes have joined yet.")
        return
    print(f"{len(nodes)} node(s) joined:")
    for n in nodes:
        age = _time.time() - n.get("last_seen", 0)
        status = "up" if age < 90 else "stale"
        print(f"  - {n['node_id']}  {n.get('url', '?')}  last seen {int(age)}s ago [{status}]")


def _cmd_cluster_leave(args) -> None:
    from synthelion.config import load_config, save_config
    cfg = load_config()
    cfg["cluster"]["role"] = "standalone"
    cfg["cluster"]["master_url"] = ""
    save_config(cfg)
    print("[OK] This node is now standalone.")


def _cmd_dashboard_passwd(args) -> None:
    from synthelion.plugins import dashboard_auth

    dashboard_auth.ensure_default_credentials()
    username = args.username or dashboard_auth.current_username()
    password = args.password
    if not password:
        import getpass
        password = getpass.getpass("New dashboard password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("ERROR: passwords do not match", file=sys.stderr)
            raise SystemExit(1)

    try:
        dashboard_auth.set_credentials(username, password)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[OK] Dashboard credentials updated for user '{username}'.")


def _cmd_loop_check(args) -> None:
    """Pre-tool guardrail, meant to run as an agent hook (e.g. Claude Code's
    PreToolUse): one process per invocation, so history is read from and
    appended to ~/.synthelion/loop_guard.jsonl via PersistentLoopGuard rather
    than kept in memory. Exit code doubles as the verdict for shell hooks that
    just want to gate on it: 0 = allow, 2 = block, without needing --json.
    """
    from synthelion.loop_guard import PersistentLoopGuard

    try:
        arguments = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        print(f"ERROR: --args is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    guard = PersistentLoopGuard(max_repeats=args.max_repeats)
    result = guard.check(args.tool, arguments, session_id=args.session)

    if args.json:
        print(json.dumps({
            "verdict": result.verdict.value,
            "should_block": result.should_block,
            "repeat_count": result.repeat_count,
            "reason": result.reason,
        }, ensure_ascii=False))
    elif result.should_block:
        print(f"BLOCK: {result.reason}", file=sys.stderr)
    else:
        print(f"ALLOW (repeat {result.repeat_count})")

    raise SystemExit(2 if result.should_block else 0)


def _cmd_loop_reset(args) -> None:
    from synthelion.loop_guard import PersistentLoopGuard
    PersistentLoopGuard().reset(session_id=args.session)
    print(f"[OK] Loop-guard history reset for session '{args.session}'")


def _cmd_firewall_check(args) -> None:
    """Pre-tool guardrail, meant to run as an agent hook (e.g. Claude Code's
    PreToolUse): vetoes a tool call that would read a file matching a
    configured "security zone" pattern, or that embeds credential-shaped
    content (cloud/database/FTP/git secrets, private keys, bulk .env dumps).
    Exit code doubles as the verdict: 0 = allow, 2 = block, without needing
    --json. See `synthelion/enterprise_guard.py`.
    """
    from synthelion.enterprise_guard import EnterpriseGuard, local_machine_mac

    try:
        arguments = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        print(f"ERROR: --args is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.no_client:
        client_mac, client_ip = None, None
    else:
        # A PreToolUse hook always runs on the same machine as the agent, so
        # this machine's own MAC is a free, automatic per-machine identity —
        # no config needed for the common case of "one registered policy per
        # developer laptop", while --client-mac/--client-ip still let an
        # operator test a specific client's policy from any machine.
        client_mac = args.client_mac or (None if args.client_ip else local_machine_mac())
        client_ip = args.client_ip

    guard = EnterpriseGuard(client_ip=client_ip, client_mac=client_mac)
    result = guard.check_tool_call(args.tool, arguments)

    if args.json:
        print(json.dumps({
            "blocked": result.blocked,
            "category": result.category,
            "rule_name": result.rule_name,
            "reason": result.reason,
        }, ensure_ascii=False))
    elif result.blocked:
        print(f"BLOCK: {result.reason}", file=sys.stderr)
    else:
        print("ALLOW")

    raise SystemExit(2 if result.blocked else 0)


def _client_to_row(c) -> dict:
    return {
        "id": c.id, "label": c.label, "ip": c.ip, "mac": c.mac,
        "enabled": c.enabled, "auto_discovered": c.auto_discovered,
        "blocked_paths": c.blocked_paths,
    }


def _cmd_clients(args) -> None:
    """Manage EnterpriseGuard's per-client (IP/MAC) protected-path registry —
    see `synthelion/enterprise_guard.py`'s client-registry section. A client
    the proxy has auto-discovered but nobody has reviewed shows up here with
    `enabled: false` and `auto_discovered: true`."""
    from synthelion import enterprise_guard as eg

    if args.clients_cmd == "list":
        clients = [_client_to_row(c) for c in eg.list_clients()]
        if args.json:
            print(json.dumps(clients, ensure_ascii=False))
            return
        if not clients:
            print("No clients registered.")
            return
        for c in clients:
            flags = []
            if c["auto_discovered"]:
                flags.append("auto-discovered")
            flags.append("enabled" if c["enabled"] else "disabled")
            print(f"{c['id']}  {c['label'] or '(no label)':<20}  ip={c['ip'] or '-':<15}  mac={c['mac'] or '-':<17}  [{', '.join(flags)}]  {len(c['blocked_paths'])} path(s)")
        return

    if args.clients_cmd == "add":
        mac = args.mac
        if mac == "self":
            mac = eg.local_machine_mac()
        client = eg.add_client(
            label=args.label, ip=args.ip, mac=mac, blocked_paths=args.blocked_paths,
            enabled=not args.disabled,
        )
        print(f"[OK] Registered client '{client.id}' ({client.label or 'no label'}).")
        return

    if args.clients_cmd == "update":
        enabled = True if args.enable else (False if args.disable else None)
        client = eg.update_client(
            args.client_id, label=args.label, ip=args.ip, mac=args.mac,
            blocked_paths=args.blocked_paths, enabled=enabled,
        )
        if client is None:
            print(f"ERROR: no client with id '{args.client_id}'", file=sys.stderr)
            raise SystemExit(1)
        print(f"[OK] Updated client '{client.id}'.")
        return

    if args.clients_cmd == "remove":
        eg.delete_client(args.client_id)
        print(f"[OK] Removed client '{args.client_id}' (if it existed).")
        return


if __name__ == "__main__":
    main()
