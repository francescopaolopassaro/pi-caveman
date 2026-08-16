"""Universal, cross-OS service installer for Synthelion.

One interface, three native backends. The user always types the same thing:

    synthelion service install      # install + start now, and on every login
    synthelion service uninstall    # stop + remove
    synthelion service status       # is it registered / running?

Under the hood each OS gets its native, user-level (no root/admin) mechanism:

    Linux    -> systemd  --user unit   (~/.config/systemd/user/<name>.service)
    macOS    -> launchd  LaunchAgent   (~/Library/LaunchAgents/<label>.plist)
    Windows  -> Task Scheduler task    (registered "at logon", auto-restart)

Design choices (see the PR discussion):
  * User-level everywhere — the proxy listens on 127.0.0.1 and serves the
    logged-in user's agents; it never needs root/admin, and user-level is the
    only tier that behaves the same on all three OSes.
  * Absolute executable path baked into the unit — a service started at
    boot/login has no guarantee the shell PATH is loaded, so we resolve the
    real path to the `synthelion` entry point (or `python -m synthelion.cli`)
    at install time.
  * Auto-restart on failure on all three backends.

The unit-*generators* in this module are pure functions of a ServiceSpec and
are fully unit-tested; the activation calls (systemctl / launchctl / schtasks)
are thin wrappers around them.
"""
from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

# Reverse-DNS namespace for launchd labels / task names.
NAMESPACE = "it.digitalsolutions.synthelion"


# ── what to run ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ServiceSpec:
    """A single long-running Synthelion process to supervise."""
    name: str                       # short id, e.g. "synthelion-proxy"
    description: str                # human-readable
    target: str = "serve-proxy"     # CLI subcommand: serve-proxy | serve-dashboard
    argv: list[str] = field(default_factory=list)  # full command, absolute exe first

    @property
    def label(self) -> str:
        """Reverse-DNS label, e.g. it.digitalsolutions.synthelion.proxy."""
        return f"{NAMESPACE}.{self.name.replace('synthelion-', '')}"


def resolve_executable() -> list[str]:
    """Absolute command prefix for invoking the Synthelion CLI.

    Prefer the installed `synthelion` console script sitting next to the
    current interpreter; fall back to ``<python> -m synthelion.cli`` so the
    service works even when only the module (not the script) is on disk. Always
    absolute — a boot/login-time service can't rely on PATH.
    """
    exe_dir = Path(sys.executable).resolve().parent
    candidates = [exe_dir / "synthelion", exe_dir / "synthelion.exe"]
    for c in candidates:
        if c.exists():
            return [str(c)]
    return [str(Path(sys.executable).resolve()), "-m", "synthelion.cli"]


def default_specs(proxy_only: bool = False) -> list[ServiceSpec]:
    """The services installed by default.

    Both the dashboard (the control surface, port 8787) and the proxy (the
    enforcement path, port 8788) run as always-on services. `--proxy-only`
    installs just the proxy.
    """
    base = resolve_executable()
    dashboard = ServiceSpec(
        name="synthelion-dashboard",
        description="Synthelion local control dashboard",
        target="serve-dashboard",
        argv=base + ["serve-dashboard"],
    )
    proxy = ServiceSpec(
        name="synthelion-proxy",
        description="Synthelion local privacy/compression proxy",
        target="serve-proxy",
        argv=base + ["serve-proxy"],
    )
    return [proxy] if proxy_only else [dashboard, proxy]


# Backwards-compatible alias.
def proxy_spec(with_dashboard: bool = False) -> list[ServiceSpec]:
    return default_specs(proxy_only=not with_dashboard)


# ── pure unit generators (fully testable) ─────────────────────────────────────

def _quote_systemd(argv: list[str]) -> str:
    # systemd ExecStart: wrap any arg containing whitespace in double quotes.
    out = []
    for a in argv:
        out.append(f'"{a}"' if (" " in a or "\t" in a) else a)
    return " ".join(out)


def systemd_unit(spec: ServiceSpec) -> str:
    """A systemd --user service unit that starts at login and restarts on
    failure. `WantedBy=default.target` is the user-session equivalent of
    multi-user.target."""
    return (
        "[Unit]\n"
        f"Description={spec.description}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={_quote_systemd(spec.argv)}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist(spec: ServiceSpec) -> bytes:
    """A launchd LaunchAgent plist: RunAtLoad (start at login) + KeepAlive
    (restart if it exits). Returned as serialized XML bytes."""
    data = {
        "Label": spec.label,
        "ProgramArguments": list(spec.argv),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_XML)


def windows_service_script() -> str:
    """Absolute path to the module the service runs (`win_service.py`)."""
    return str(Path(__file__).resolve().parent / "win_service.py")


def windows_service_interpreter() -> str:
    """The interpreter the service should run under.

    Prefers `pythonw.exe` next to the current interpreter (no console window
    when launched by the SCM), falling back to `python.exe`. This is the venv's
    own interpreter — the same one that runs `synthelion serve-dashboard`
    correctly from a shell — which is why the service does not need pywin32's
    `pythonservice.exe` host (that host fails the SCM handshake inside a venv:
    error 1053)."""
    exe = Path(sys.executable).resolve()
    pythonw = exe.parent / "pythonw.exe"
    return str(pythonw if pythonw.exists() else exe)


def windows_bin_path(spec: ServiceSpec) -> str:
    """The ImagePath registered for this service: the venv interpreter running
    win_service.py, told which service it is."""
    return (
        f'"{windows_service_interpreter()}" "{windows_service_script()}" '
        f'--service-name {spec.name}'
    )


def windows_create_command(spec: ServiceSpec) -> list[str]:
    """`sc.exe create` args registering an auto-start service under LocalSystem."""
    return [
        "sc.exe", "create", spec.name,
        "binPath=", windows_bin_path(spec),
        "start=", "auto",
        "DisplayName=", spec.description,
    ]


def windows_failure_command(spec: ServiceSpec) -> list[str]:
    """`sc.exe failure` args giving the SCM automatic restart-on-crash: three
    restarts 5s apart, failure counter reset after a day of health."""
    return [
        "sc.exe", "failure", spec.name,
        "reset=", "86400",
        "actions=", "restart/5000/restart/5000/restart/5000",
    ]


def config_path_for_service() -> str | None:
    """The user's active config path to hand to the service, so it reads the
    right config even when the SCM runs it as LocalSystem. None if there is no
    resolvable config yet (the service then falls back to built-in defaults)."""
    try:
        from synthelion.config import config_path as _resolve
        p = _resolve()
        return str(p) if p else None
    except Exception:
        return None


# ── locations ─────────────────────────────────────────────────────────────────

def systemd_unit_path(spec: ServiceSpec) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd" / "user" / f"{spec.name}.service"


def launchd_plist_path(spec: ServiceSpec) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{spec.label}.plist"


# ── backend dispatch ──────────────────────────────────────────────────────────

def current_backend() -> str:
    s = platform.system()
    if s == "Linux":
        return "systemd"
    if s == "Darwin":
        return "launchd"
    if s == "Windows":
        return "windows"
    raise RuntimeError(f"Unsupported OS for service install: {s}")


class ServiceError(RuntimeError):
    """Raised when a backend activation command fails."""


def is_admin() -> bool:
    """True if the current process can register a Windows service.

    Registering a Windows service (auto-start at boot, managed by the SCM)
    needs an elevated (Administrator) token — unlike systemd --user / launchd,
    there is no unprivileged per-user equivalent that also starts at boot. On
    POSIX this is always True (user services need no elevation)."""
    if platform.system() != "Windows":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run(cmd: list[str], dry_run: bool) -> None:
    if dry_run:
        print("   would run:", " ".join(cmd))
        return
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise ServiceError(f"command failed (exit {rc}): {' '.join(cmd)}")


def install(specs: list[ServiceSpec], dry_run: bool = False) -> None:
    backend = current_backend()
    if backend == "windows" and not dry_run and not is_admin():
        raise ServiceError(
            "registering a Windows startup task requires elevation — "
            "run this command from an Administrator terminal "
            "(Start > Windows PowerShell > Run as administrator)."
        )
    config_path = config_path_for_service()
    for spec in specs:
        if backend == "systemd":
            path = systemd_unit_path(spec)
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(systemd_unit(spec), encoding="utf-8")
            _run(["systemctl", "--user", "daemon-reload"], dry_run)
            _run(["systemctl", "--user", "enable", "--now", f"{spec.name}.service"], dry_run)
        elif backend == "launchd":
            path = launchd_plist_path(spec)
            if not dry_run:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(launchd_plist(spec))
            _run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)], dry_run)
        elif backend == "windows":
            _install_windows_service(spec, config_path, dry_run)


def _install_windows_service(spec: ServiceSpec, config_path: str | None, dry_run: bool) -> None:
    """Register one Synthelion server as an auto-start Windows service via
    pywin32, record its target + config in the registry, and enable SCM
    restart-on-crash. Kept in its own function so the pywin32 import stays off
    the POSIX code paths."""
    if dry_run:
        print(f"   would install service {spec.name} (auto-start), target={spec.target}, "
              f"config={config_path or '(defaults)'}")
        print("   would run:", " ".join(windows_create_command(spec)))
        print("   would run:", " ".join(windows_failure_command(spec)))
        return
    _run(windows_create_command(spec), dry_run=False)
    _write_service_registry(spec, config_path)
    _run(windows_failure_command(spec), dry_run=False)
    print(f"   registered service {spec.name} (auto-start)")
    try:
        _run(["sc.exe", "start", spec.name], dry_run=False)
        print(f"   started {spec.name}")
    except Exception as e:  # noqa: BLE001 — report, don't abort: it's registered for boot
        print(f"   note: {spec.name} registered but did not start now ({e}); "
              f"it will start at next boot. Check `sc.exe query {spec.name}`.")


def _write_service_registry(spec: ServiceSpec, config_path: str | None) -> None:
    """Store this service's Target (and the user's ConfigPath) under its
    Parameters key, where win_service.py reads them at startup."""
    import winreg
    key = winreg.CreateKey(
        winreg.HKEY_LOCAL_MACHINE,
        rf"SYSTEM\CurrentControlSet\Services\{spec.name}\Parameters",
    )
    try:
        winreg.SetValueEx(key, "Target", 0, winreg.REG_SZ, spec.target)
        if config_path:
            winreg.SetValueEx(key, "ConfigPath", 0, winreg.REG_SZ, config_path)
    finally:
        winreg.CloseKey(key)


def uninstall(specs: list[ServiceSpec], dry_run: bool = False) -> None:
    backend = current_backend()
    if backend == "windows" and not dry_run and not is_admin():
        raise ServiceError(
            "removing a Windows startup task requires elevation — "
            "run this command from an Administrator terminal."
        )
    for spec in specs:
        if backend == "systemd":
            _run(["systemctl", "--user", "disable", "--now", f"{spec.name}.service"], dry_run)
            path = systemd_unit_path(spec)
            if not dry_run and path.exists():
                path.unlink()
            _run(["systemctl", "--user", "daemon-reload"], dry_run)
        elif backend == "launchd":
            path = launchd_plist_path(spec)
            _run(["launchctl", "bootout", f"gui/{os.getuid()}/{spec.label}"], dry_run)
            if not dry_run and path.exists():
                path.unlink()
        elif backend == "windows":
            _uninstall_windows_service(spec, dry_run)


def _uninstall_windows_service(spec: ServiceSpec, dry_run: bool) -> None:
    """Stop and remove a Synthelion Windows service. Missing or already-stopped
    services are not an error (uninstall must be idempotent)."""
    if dry_run:
        print(f"   would stop and remove service {spec.name}")
        return
    import subprocess
    # Stop first; a stopped or absent service is fine, so failures are ignored.
    subprocess.run(["sc.exe", "stop", spec.name], capture_output=True)
    r = subprocess.run(["sc.exe", "delete", spec.name], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"   removed service {spec.name}")
    elif "1060" in (r.stdout + r.stderr):  # ERROR_SERVICE_DOES_NOT_EXIST
        print(f"   service {spec.name} not installed — nothing to remove")
    else:
        raise ServiceError(
            f"could not remove service {spec.name}: {(r.stdout + r.stderr).strip()}"
        )
