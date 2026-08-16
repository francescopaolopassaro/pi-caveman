"""Tests for install_claude.py settings.json handling — atomic write + backup.

The installer touches the user's most important config file (`~/.claude/
settings.json`). Two hardening guarantees are exercised here:

  1. `_save_settings` writes atomically (temp sibling + os.replace) and leaves
     no leftover temp file — an interrupted/concurrent write can't truncate it.
  2. `_backup_settings` copies an existing config to a timestamped `.bak-<ts>`
     before it is modified, so there is always a restore point.

Unrelated keys already in settings.json must survive a configure/remove pass.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

# install_claude.py lives at the repo root, not inside the package.
_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("install_claude", _ROOT / "install_claude.py")
install_claude = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(install_claude)  # type: ignore[union-attr]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_save_settings_writes_valid_json(tmp_path):
    path = tmp_path / "settings.json"
    install_claude._save_settings(path, {"mcpServers": {"synthelion": {"command": "x"}}})
    assert _read(path)["mcpServers"]["synthelion"]["command"] == "x"


def test_save_settings_leaves_no_temp_file(tmp_path):
    path = tmp_path / "settings.json"
    install_claude._save_settings(path, {"a": 1})
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp-" in p.name]
    assert leftovers == [], f"temp file(s) left behind: {leftovers}"


def test_save_settings_overwrites_atomically(tmp_path):
    path = tmp_path / "settings.json"
    install_claude._save_settings(path, {"v": 1})
    install_claude._save_settings(path, {"v": 2})
    assert _read(path) == {"v": 2}


def test_backup_none_when_no_existing_file(tmp_path):
    assert install_claude._backup_settings(tmp_path / "settings.json") is None


def test_backup_copies_existing_file(tmp_path):
    path = tmp_path / "settings.json"
    original = {"mcpServers": {"other": {"command": "keep-me"}}}
    path.write_text(json.dumps(original), encoding="utf-8")

    backup = install_claude._backup_settings(path)

    assert backup is not None and backup.exists()
    assert ".bak-" in backup.name
    assert _read(backup) == original          # backup holds the pre-change content
    assert _read(path) == original            # original untouched by the backup step


def test_unrelated_keys_survive_backup_and_save(tmp_path, monkeypatch):
    """A configure-style pass must preserve a user's existing MCP servers/keys
    and leave a backup of what was there before."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(install_claude, "_settings_path", lambda: path)

    existing = {
        "mcpServers": {"someone-else": {"command": "theirs"}},
        "permissions": {"allow": ["Read"]},
    }
    path.write_text(json.dumps(existing), encoding="utf-8")

    install_claude.configure_claude(binary="synthelion-mcp", add_hook=False)

    saved = _read(path)
    # our server was added, theirs is intact, unrelated key preserved
    assert saved["mcpServers"]["synthelion"] == {"command": "synthelion-mcp"}
    assert saved["mcpServers"]["someone-else"] == {"command": "theirs"}
    assert saved["permissions"] == {"allow": ["Read"]}
    # a timestamped backup of the pre-change file exists and holds the original
    backups = [p for p in tmp_path.iterdir() if ".bak-" in p.name]
    assert backups, "no backup was created"
    assert any(_read(b) == existing for b in backups)


def test_uninstall_removes_services_before_package():
    """Services must be removed while the CLI still exists — after pip drops
    the package, a registered unit would point at a missing executable."""
    src = (_ROOT / "install_claude.py").read_text(encoding="utf-8")
    body = src[src.index("if args.uninstall:"):]
    assert "remove_services()" in body
    assert body.index("remove_services()") < body.index("uninstall_package()")


def test_shell_installers_remove_services_on_uninstall():
    for name in ("install_claude.sh", "install_claude.ps1"):
        src = (_ROOT / name).read_text(encoding="utf-8")
        assert "service uninstall" in src, f"{name} does not remove services"
