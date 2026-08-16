"""Tests for the universal service installer.

Backend activation (systemctl/launchctl/pywin32 SCM) is OS-specific and
validated on real machines; the generation and dispatch logic — where
correctness lives — is pure and covered here for all three backends.
"""
from __future__ import annotations

import plistlib
from unittest import mock

import pytest

from synthelion import service
from synthelion.service import ServiceSpec


def _spec(target="serve-proxy", argv=None):
    return ServiceSpec(
        name="synthelion-proxy",
        description="Synthelion local privacy/compression proxy",
        target=target,
        argv=argv or ["/opt/venv/bin/synthelion", "serve-proxy"],
    )


# ── spec set ──────────────────────────────────────────────────────────────────

def test_default_installs_dashboard_and_proxy():
    specs = service.default_specs()
    assert [(s.name, s.target) for s in specs] == [
        ("synthelion-dashboard", "serve-dashboard"),
        ("synthelion-proxy", "serve-proxy"),
    ]


def test_proxy_only_installs_just_proxy():
    specs = service.default_specs(proxy_only=True)
    assert [s.target for s in specs] == ["serve-proxy"]


def test_resolve_executable_is_absolute():
    argv = service.resolve_executable()
    assert argv[0].startswith("/") or argv[0][1:3] == ":\\"


def test_label_is_reverse_dns():
    assert _spec().label == "it.digitalsolutions.synthelion.proxy"


# ── systemd ───────────────────────────────────────────────────────────────────

def test_systemd_unit_has_restart_and_login_start():
    unit = service.systemd_unit(_spec())
    assert "[Unit]" in unit and "[Service]" in unit and "[Install]" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit
    assert "ExecStart=/opt/venv/bin/synthelion serve-proxy" in unit


def test_systemd_quotes_paths_with_spaces():
    unit = service.systemd_unit(_spec(argv=["/opt/my venv/bin/synthelion", "serve-proxy"]))
    assert 'ExecStart="/opt/my venv/bin/synthelion" serve-proxy' in unit


def test_systemd_unit_path_uses_user_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert service.systemd_unit_path(_spec()) == tmp_path / "systemd" / "user" / "synthelion-proxy.service"


# ── launchd ───────────────────────────────────────────────────────────────────

def test_launchd_plist_is_valid_and_complete():
    data = plistlib.loads(service.launchd_plist(_spec()))
    assert data["Label"] == "it.digitalsolutions.synthelion.proxy"
    assert data["ProgramArguments"] == ["/opt/venv/bin/synthelion", "serve-proxy"]
    assert data["RunAtLoad"] is True
    assert data["KeepAlive"] == {"SuccessfulExit": False}


# ── windows service ───────────────────────────────────────────────────────────

def test_windows_failure_command_requests_restarts():
    cmd = service.windows_failure_command(_spec())
    assert cmd[:3] == ["sc.exe", "failure", "synthelion-proxy"]
    assert "restart/5000/restart/5000/restart/5000" in cmd


def test_windows_binpath_runs_this_interpreter_and_script():
    """The service must run under the same interpreter that can import
    synthelion — not pywin32's pythonservice.exe host, which fails the SCM
    handshake inside a venv (error 1053)."""
    bp = service.windows_bin_path(_spec())
    assert "win_service.py" in bp
    assert "--service-name synthelion-proxy" in bp
    assert bp.startswith('"')  # interpreter path is quoted (spaces in paths)


def test_windows_create_command_is_auto_start():
    cmd = service.windows_create_command(_spec())
    assert cmd[:3] == ["sc.exe", "create", "synthelion-proxy"]
    assert "start=" in cmd and "auto" in cmd
    assert "binPath=" in cmd


def test_windows_service_script_points_at_module():
    assert service.windows_service_script().endswith("win_service.py")


def test_windows_install_dry_run_lists_both_services(capsys):
    specs = service.default_specs()
    with mock.patch.object(service, "current_backend", lambda: "windows"), \
         mock.patch.object(service, "is_admin", lambda: True):
        service.install(specs, dry_run=True)
    out = capsys.readouterr().out
    assert "synthelion-dashboard" in out and "target=serve-dashboard" in out
    assert "synthelion-proxy" in out and "target=serve-proxy" in out
    assert "sc.exe create" in out


def test_windows_uninstall_dry_run(capsys):
    with mock.patch.object(service, "current_backend", lambda: "windows"), \
         mock.patch.object(service, "is_admin", lambda: True):
        service.uninstall(service.default_specs(), dry_run=True)
    out = capsys.readouterr().out
    assert "remove service synthelion-dashboard" in out
    assert "remove service synthelion-proxy" in out


def test_windows_install_requires_admin():
    with mock.patch.object(service, "current_backend", lambda: "windows"), \
         mock.patch.object(service, "is_admin", lambda: False):
        with pytest.raises(service.ServiceError) as e:
            service.install(service.default_specs(), dry_run=False)
    assert "elevation" in str(e.value).lower() or "administrator" in str(e.value).lower()


def test_win_service_parses_service_name():
    import synthelion.win_service as w
    assert w.parse_service_name(["s.py", "--service-name", "synthelion-dashboard"]) == "synthelion-dashboard"
    assert w.parse_service_name(["s.py", "--service-name=synthelion-proxy"]) == "synthelion-proxy"
    assert w.parse_service_name(["s.py"]) == "synthelion-proxy"


# ── failure handling ──────────────────────────────────────────────────────────

def test_run_raises_on_nonzero_exit(monkeypatch):
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", lambda *a, **k: type("R", (), {"returncode": 1})())
    with pytest.raises(service.ServiceError):
        service._run(["false"], dry_run=False)


def test_run_dry_run_never_executes(capsys):
    service._run(["sc.exe", "failure"], dry_run=True)
    assert "would run" in capsys.readouterr().out


def test_is_admin_true_on_posix(monkeypatch):
    monkeypatch.setattr(service.platform, "system", lambda: "Linux")
    assert service.is_admin() is True


# ── win_service module (import-safe target logic) ─────────────────────────────

def test_win_service_imports_without_pywin32():
    import synthelion.win_service as w
    assert w.VALID_TARGETS == ("serve-proxy", "serve-dashboard")


def test_win_service_rejects_unknown_target():
    import synthelion.win_service as w
    with pytest.raises(ValueError):
        w._run_target("bogus")
