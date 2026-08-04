# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""MCP server exposing Synthelion compression tools for Claude Code, OpenCode, and other MCP clients.

mcp is included as a core dependency — no extra install step needed.

Run as standalone server:
    synthelion-mcp
    # or
    python -m synthelion.plugins.mcp_server

Configure in Claude Code (.claude/settings.json):
    {
      "mcpServers": {
        "synthelion": { "command": "synthelion-mcp" }
      }
    }
"""
from __future__ import annotations

import asyncio
import json as _json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from synthelion.plugins.openai_tools import execute_tool, get_tool_definitions, get_tool_list  # noqa: F401

# Tools that only read / compress — do not modify external state.
_READ_ONLY_TOOLS = frozenset({
    "compress", "detect_language", "route_content", "summarize", "compress_batch",
    "compress_for_context", "compress_conversation", "deduplicate",
    "compress_file",
    "session_recall", "synthelion_status",
    "safety_check", "check_sensitive_content", "check_enterprise_guard", "analyze_waste", "check_cache_alignment", "align_cache_prompt", "shape_output",
    "focus_relevant", "estimate_cost", "generate_commit_message", "review_diff",
    "generate_project_wiki", "list_relevant_tools", "expand_masked_output",
    "get_artifact_index", "rewrite_command",
    "get_response_style_guidance", "check_read_maturity",
    "restore_privacy_text", "check_prompt_injection", "get_ai_transparency_notice",
    "retrieve_compressed_text", "memory_recall",
})


# ── auto-update state ─────────────────────────────────────────────────────────
#
# Many synthelion-mcp processes can be running at once (one per agent session,
# as happens behind an AI provider). All of them independently notice a new
# PyPI release at roughly the same time. Running `pip install --upgrade`
# concurrently from several processes can corrupt the installed package, so
# only one process is allowed to actually perform the upgrade.
#
# Rather than a blocking lock (which would make every other session wait on
# an unrelated process's pip install), we use a non-blocking atomic claim:
# os.O_CREAT | os.O_EXCL is a single OS syscall that either creates the file
# or fails immediately if it already exists — there is no waiting involved.
# Losing processes just skip the upgrade and move on. A stale claim (crashed
# updater) expires after _CLAIM_TTL seconds so the system self-heals.

_update: dict = {"status": "idle", "new_version": None}  # idle | checking | updating | updated | failed

_CLAIM_FILE = Path.home() / ".synthelion" / "update.lock"
_CLAIM_TTL = 300  # seconds — stale claims are ignored after this


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _try_claim_update() -> bool:
    """Non-blocking: True if this process may perform the upgrade, False otherwise."""
    try:
        _CLAIM_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_CLAIM_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        try:
            age = time.time() - _CLAIM_FILE.stat().st_mtime
        except OSError:
            return False
        if age <= _CLAIM_TTL:
            return False
        # Stale claim from a crashed/killed updater — clear it and retry once.
        try:
            _CLAIM_FILE.unlink()
        except OSError:
            return False
        return _try_claim_update()
    except OSError:
        return False


def _release_update_claim() -> None:
    try:
        _CLAIM_FILE.unlink()
    except OSError:
        pass


def _check_and_update() -> None:
    """Background thread: fetch latest version from PyPI and auto-install if newer.

    The PyPI check itself is safe for every session to run concurrently — it's
    the actual `pip install` that needs to be exclusive to one process.
    """
    _update["status"] = "checking"
    try:
        import synthelion as _syn
        current = _syn.__version__

        with urllib.request.urlopen(
            "https://pypi.org/pypi/synthelion/json", timeout=8
        ) as resp:
            info = _json.loads(resp.read())
        latest = info["info"]["version"]

        if _version_tuple(latest) <= _version_tuple(current):
            _update["status"] = "up_to_date"
            return

        if not _try_claim_update():
            # Another session is already upgrading (or just did) — don't duplicate work.
            _update["status"] = "up_to_date"
            return

        try:
            _update["status"] = "updating"
            _update["new_version"] = latest
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", "synthelion", "-q"],
                capture_output=True,
                timeout=120,
            )
            _update["status"] = "updated"
        finally:
            _release_update_claim()
    except Exception:
        _update["status"] = "failed"


# ── server ────────────────────────────────────────────────────────────────────

def main() -> None:
    """Entry point for synthelion-mcp CLI command."""
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        import mcp.types as types
    except ImportError:
        print(
            "ERROR: MCP package not installed. Run: pip install 'synthelion[mcp]'",
            flush=True,
        )
        raise SystemExit(1)

    # Start version check in background — does not block server startup
    threading.Thread(target=_check_and_update, daemon=True).start()

    app = Server("synthelion")
    _tool_defs = get_tool_definitions()

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools = []
        for td in _tool_defs:
            fn = td["function"]
            annotation = None
            try:
                if fn["name"] in _READ_ONLY_TOOLS:
                    annotation = types.ToolAnnotations(readOnlyHint=True)
            except Exception:
                pass
            tools.append(types.Tool(
                name=fn["name"],
                description=fn["description"],
                inputSchema=fn["parameters"],
                annotations=annotation,
            ))
        return tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        results: list[types.TextContent] = []

        # Notify once when an update has been installed
        if _update["status"] == "updating":
            results.append(types.TextContent(
                type="text",
                text=(
                    f"⏳ Synthelion {_update['new_version']} trovato su PyPI — "
                    "installazione in corso in background. "
                    "Chiudi e riapri Claude Code al termine per attivare la nuova versione."
                ),
            ))
        elif _update["status"] == "updated":
            results.append(types.TextContent(
                type="text",
                text=(
                    f"✅ Synthelion aggiornato alla versione {_update['new_version']}. "
                    "Chiudi e riapri Claude Code per attivare la nuova versione."
                ),
            ))
            _update["status"] = "notified"  # show the message only once

        try:
            # Run the (CPU-bound, synchronous) tool off the event loop thread so
            # concurrent tool calls — from this session or others reusing the
            # process — don't serialize behind one slow compression/summarization.
            result = await asyncio.to_thread(execute_tool, name, arguments)
            text = _json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as exc:
            text = _json.dumps({"error": str(exc)})
        results.append(types.TextContent(type="text", text=text))
        return results

    async def _serve() -> None:
        # stdio only — this process never opens a network socket, so it has no
        # network attack surface today. If a network transport (SSE/HTTP) is
        # ever added here, gate it with synthelion.waf_guard.get_waf_engine()
        # .gate(...) — the same call synthelion/plugins/dashboard.py already
        # makes for the dashboard's HTTP server — rather than writing a second
        # WAF/firewall engine from scratch.
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
