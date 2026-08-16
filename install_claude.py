#!/usr/bin/env python3
# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# © 2026 Passaro Francesco Paolo — Digitalsolutions.it
#
# Universal Synthelion installer for Claude Code
# Works on Windows, Linux, macOS — requires Python 3.11+
#
# Usage:
#   python install_claude.py
#   python install_claude.py --uninstall
"""Install Synthelion MCP + auto-compression hook for Claude Code."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
PACKAGE = "synthelion"
MIN_PYTHON = (3, 11)
HOOK_MIN_LEN = 0     # chars — shorter prompts are skipped (0 = scan every prompt, incl. PII/injection checks)
HOOK_MIN_EFF = 15    # % — skip injection if savings below this threshold
# ──────────────────────────────────────────────────────────────────────────────

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


# ── colours ──────────────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    if IS_WINDOWS and not os.environ.get("WT_SESSION"):
        return text          # plain CMD — skip ANSI
    return f"\033[{code}m{text}\033[0m"


def ok(msg: str) -> None:   print(_c("32", "  [OK] ") + msg)
def info(msg: str) -> None: print(_c("36", "   --> ") + msg)
def warn(msg: str) -> None: print(_c("33", "   !   ") + msg)
def err(msg: str) -> None:  print(_c("31", "  [X]  ") + msg)
def h1(msg: str) -> None:   print("\n" + _c("1;36", msg))
def h2(msg: str) -> None:   print("\n" + _c("1", msg))


# ── python check ─────────────────────────────────────────────────────────────

def check_python() -> None:
    h2("Checking Python version…")
    v = sys.version_info
    if v < MIN_PYTHON:
        err(f"Python {v.major}.{v.minor} found — Synthelion needs 3.11+.")
        err("Download from https://www.python.org/downloads/")
        sys.exit(1)
    ok(f"Python {v.major}.{v.minor}.{v.micro}")


# ── pip install ───────────────────────────────────────────────────────────────

def install_package(upgrade: bool = False) -> None:
    h2("Installing Synthelion…")
    cmd = [sys.executable, "-m", "pip", "install", PACKAGE]
    if upgrade:
        cmd.append("--upgrade")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err("pip install failed:")
        print(result.stderr)
        sys.exit(1)
    ok(f"pip install {PACKAGE} succeeded")


def remove_services() -> None:
    """Remove the installed Synthelion services, if any.

    Best-effort and always before the package is uninstalled — once pip removes
    synthelion the CLI is gone and any registered systemd unit / LaunchAgent /
    Windows service would be left orphaned, pointing at an executable that no
    longer exists.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-m", "synthelion.cli", "service", "uninstall"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            ok("Services removed")
        else:
            warn("No services to remove (or removal needs elevation)")
    except Exception:
        warn("Could not remove services — remove them manually if installed")


def uninstall_package() -> None:
    h2("Uninstalling Synthelion…")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        ok("Synthelion uninstalled")
    else:
        warn("Synthelion was not installed via pip (skipping)")


# ── find mcp binary ──────────────────────────────────────────────────────────

def find_mcp_binary() -> str:
    """Return the best command string to run synthelion-mcp."""
    # 1. Same Scripts/bin dir as the running Python
    if IS_WINDOWS:
        scripts = Path(sys.executable).parent / "Scripts"
        binary = scripts / "synthelion-mcp.exe"
    else:
        scripts = Path(sys.executable).parent
        binary = scripts / "synthelion-mcp"

    if binary.exists():
        return str(binary)

    # 2. shutil.which — system PATH
    found = shutil.which("synthelion-mcp")
    if found:
        return found

    # 3. Fallback: module form (always works)
    return None  # caller will use module form


def mcp_command_config(binary: str | None) -> dict:
    """Return the mcpServers config dict."""
    if binary:
        return {"command": binary}
    # Module fallback
    return {
        "command": sys.executable,
        "args": ["-m", "synthelion.plugins.mcp_server"],
    }


# ── hook command ─────────────────────────────────────────────────────────────

def _hook_command_windows(cli: str) -> str:
    # `& "cli"` (not bare `"cli" args`) is required — a quoted path followed
    # by arguments is a parse error in PowerShell without the call operator.
    # All formatting (savings label, PII/privacy breakdown, AI Act notice)
    # happens in `synthelion compress --json` itself (see cli.py's `notice`
    # field) — the hook script only has to branch on `blocked`:
    #   - blocked   -> `decision: "block"` with `reason=$r.notice` (full PII
    #                  breakdown + AI Act notice), prompt never reaches Claude.
    #   - otherwise -> `systemMessage=$r.notice` (visible in the terminal) +
    #                  `additionalContext=$r.compressed` (invisible to the
    #                  terminal, but that's the actual compressed prompt Claude reads).
    cli_q = cli.replace("\\", "\\\\")          # escape backslashes for PS string
    return (
        f"$j=[Console]::In.ReadToEnd()|ConvertFrom-Json;"
        f"$p=$j.prompt;"
        f"if($p -and $p.Length -gt {HOOK_MIN_LEN})"
        f"{{$r=($p| & \"{cli_q}\" compress --json 2>$null)|ConvertFrom-Json;"
        f"if($r -and $r.blocked)"
        f"{{@{{decision='block';reason=$r.notice}}|ConvertTo-Json -Compress}}"
        f"elseif($r -and $r.efficiency_pct -gt {HOOK_MIN_EFF})"
        f"{{@{{systemMessage=$r.notice;hookSpecificOutput=@{{hookEventName='UserPromptSubmit';additionalContext=$r.compressed}}}}|ConvertTo-Json -Compress}}}}"
    )


def _hook_command_unix(cli: str) -> str:
    return (
        f"prompt=$(cat | python3 -c \"import sys,json; print(json.load(sys.stdin).get('prompt',''))\"); "
        f"if [ ${{#prompt}} -gt {HOOK_MIN_LEN} ]; then "
        f"r=$(printf '%s' \"$prompt\" | \"{cli}\" compress --json 2>/dev/null); "
        f"if [ -n \"$r\" ]; then "
        f"out=$(printf '%s' \"$r\" | python3 -c \""
        f"import sys,json; d=json.load(sys.stdin); eff=int(d.get('efficiency_pct',0)); "
        f"print(json.dumps({{'decision':'block','reason':d.get('notice','')}})) if d.get('blocked') "
        f"else print(json.dumps({{'systemMessage':d.get('notice',''),'hookSpecificOutput':{{'hookEventName':'UserPromptSubmit','additionalContext':d.get('compressed','')}}}})) if eff>{HOOK_MIN_EFF} else None\"); "
        f"[ -n \"$out\" ] && printf '%s' \"$out\"; fi; fi"
    )


def _find_cli_binary() -> str | None:
    """Find the synthelion CLI binary (not the MCP server binary)."""
    # Prefer synthelion CLI over MCP server
    found = shutil.which("synthelion")
    if found:
        return found
    # Scripts dir next to running Python
    if IS_WINDOWS:
        scripts = Path(sys.executable).parent / "Scripts"
        b = scripts / "synthelion.exe"
    else:
        scripts = Path(sys.executable).parent
        b = scripts / "synthelion"
    if b.exists():
        return str(b)
    return None


def build_hook_entry(cli_binary: str | None) -> dict:
    cli = _find_cli_binary() or cli_binary or "synthelion"
    if IS_WINDOWS:
        return {
            "type": "command",
            "shell": "powershell",
            "command": _hook_command_windows(cli),
            "statusMessage": "Compressing prompt...",
            "timeout": 15,
        }
    else:
        return {
            "type": "command",
            "shell": "bash",
            "command": _hook_command_unix(cli),
            "statusMessage": "Compressing prompt...",
            "timeout": 15,
        }


# ── settings.json helpers ────────────────────────────────────────────────────

def _settings_path() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("USERPROFILE", Path.home()))
    else:
        base = Path.home()
    return base / ".claude" / "settings.json"


def _load_settings(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            warn(f"{path} has invalid JSON — will back up and recreate.")
            path.rename(path.with_suffix(".json.bak"))
    return {}


def _backup_settings(path: Path) -> Path | None:
    """Copy an existing settings file to a timestamped ``.bak-<ts>`` before we
    modify it, so a user's working config always has a restore point. Returns
    the backup path, or None if there was nothing to back up / the copy failed
    (a failed backup must never block or crash the install)."""
    if not path.exists():
        return None
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{ts}")
    try:
        shutil.copy2(path, backup)
        return backup
    except OSError:
        return None


def _save_settings(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    # Write to a temp file in the same directory, then atomically replace the
    # target: an interrupted or concurrent write can never leave a truncated /
    # corrupt settings.json (os.replace is atomic on the same filesystem).
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


# ── install flow ─────────────────────────────────────────────────────────────

def configure_claude(binary: str | None, add_hook: bool) -> None:
    h2("Configuring Claude Code…")
    path = _settings_path()
    info(f"Settings file: {path}")

    settings = _load_settings(path)

    backup = _backup_settings(path)
    if backup:
        info(f"Backed up existing settings -> {backup}")

    # MCP server
    settings.setdefault("mcpServers", {})
    mcp_cfg = mcp_command_config(binary)
    settings["mcpServers"]["synthelion"] = mcp_cfg
    ok(f"MCP server configured: {mcp_cfg}")

    # Hook
    if add_hook:
        hook_entry = build_hook_entry(binary)
        hooks = settings.setdefault("hooks", {})
        existing = hooks.get("UserPromptSubmit", [])
        # Remove any previous synthelion hook group
        existing = [g for g in existing if not _is_synthelion_hook(g)]
        existing.append({"hooks": [hook_entry]})
        hooks["UserPromptSubmit"] = existing
        ok("UserPromptSubmit hook configured")
    else:
        info("Skipping hook (--no-hook)")

    _save_settings(path, settings)
    ok(f"Saved -> {path}")


def _is_synthelion_hook(group: dict) -> bool:
    for h in group.get("hooks", []):
        cmd = h.get("command", "")
        if "synthelion" in cmd.lower():
            return True
    return False


def remove_claude_config() -> None:
    h2("Removing Synthelion from Claude Code…")
    path = _settings_path()
    if not path.exists():
        warn("settings.json not found — nothing to remove.")
        return
    settings = _load_settings(path)

    backup = _backup_settings(path)
    if backup:
        info(f"Backed up existing settings -> {backup}")

    # Remove MCP
    mcp = settings.get("mcpServers", {})
    if "synthelion" in mcp:
        del mcp["synthelion"]
        ok("MCP server removed")

    # Remove hook
    hooks = settings.get("hooks", {})
    before = len(hooks.get("UserPromptSubmit", []))
    hooks["UserPromptSubmit"] = [
        g for g in hooks.get("UserPromptSubmit", [])
        if not _is_synthelion_hook(g)
    ]
    after = len(hooks["UserPromptSubmit"])
    if before != after:
        ok("UserPromptSubmit hook removed")
    if not hooks["UserPromptSubmit"]:
        del hooks["UserPromptSubmit"]
    if not hooks:
        settings.pop("hooks", None)

    _save_settings(path, settings)
    ok(f"Saved -> {path}")


# ── smoke test ───────────────────────────────────────────────────────────────

def smoke_test(binary: str | None) -> None:
    h2("Running smoke test…")
    # Import test
    try:
        import synthelion  # noqa: F401
        ok("import synthelion OK")
    except ImportError as e:
        err(f"import synthelion failed: {e}")
        sys.exit(1)

    # Compress test
    try:
        from synthelion import CompressionService, CompressionLevel
        svc = CompressionService()
        r = svc.compress(
            "I would like to know if it is possible to receive information.",
            CompressionLevel.SEMANTIC,
        )
        assert r.compressed_text, "empty output"
        ok(f"compress OK — {r.original_tokens}->{r.compressed_tokens} tokens ({r.efficiency_pct:.0f}% saved)")
    except Exception as e:
        err(f"compress failed: {e}")
        sys.exit(1)

    # MCP binary test
    cli_bin = binary or shutil.which("synthelion-mcp")
    if cli_bin:
        result = subprocess.run(
            [cli_bin, "--help"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 or "mcp" in result.stdout.lower() or "mcp" in result.stderr.lower():
            ok(f"synthelion-mcp reachable at {cli_bin}")
        else:
            warn(f"synthelion-mcp returned code {result.returncode} — may still work")
    else:
        warn("synthelion-mcp not found in PATH — using Python module form instead")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install Synthelion MCP + hook for Claude Code",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove Synthelion from Claude Code settings and pip",
    )
    parser.add_argument(
        "--no-hook", action="store_true",
        help="Skip the UserPromptSubmit auto-compression hook",
    )
    parser.add_argument(
        "--upgrade", action="store_true",
        help="pip install --upgrade (update to latest version)",
    )
    parser.add_argument(
        "--no-pip", action="store_true",
        help="Skip pip install (assume Synthelion is already installed)",
    )
    args = parser.parse_args()

    h1("Synthelion Installer for Claude Code")
    print(f"  Platform : {platform.system()} {platform.machine()}")
    print(f"  Python   : {sys.version.split()[0]}")

    if args.uninstall:
        remove_claude_config()
        remove_services()
        uninstall_package()
        h1("Uninstall complete.")
        return

    check_python()

    if not args.no_pip:
        install_package(upgrade=args.upgrade)

    binary = find_mcp_binary()
    if binary:
        info(f"synthelion-mcp found at: {binary}")
    else:
        warn("synthelion-mcp not in PATH — will use Python module form")

    smoke_test(binary)
    configure_claude(binary, add_hook=not args.no_hook)

    h1("Installation complete!")
    print()
    print("  Next steps:")
    print("  1. Restart Claude Code (or open /hooks to reload config)")
    print("  2. In Claude Code, type: 'Use Synthelion to compress this text'")
    print("  3. Every prompt is auto-compressed and scanned for PII/prompt-injection")
    print()
    print("  To update Synthelion later:")
    print(f"    python {Path(__file__).name} --upgrade")
    print()
    print("  To uninstall:")
    print(f"    python {Path(__file__).name} --uninstall")
    print()


if __name__ == "__main__":
    main()
