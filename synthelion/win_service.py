"""Windows Service entry point for Synthelion.

Bridge between the Windows Service Control Manager (SCM) and a long-running
Synthelion server (`serve-proxy` / `serve-dashboard`). The SCM does not just
launch an executable — it expects the process to report start/running/stop and
to stop promptly on request. A plain `synthelion serve-proxy` never does that,
so the SCM kills it after the start timeout. This module implements that
contract.

Why this runs under the venv's python.exe rather than pywin32's service host:
pywin32 ships `pythonservice.exe`, and `win32serviceutil.InstallService` would
normally register it as the service binary. Inside a virtualenv that host fails
to complete the SCM handshake (error 1053) — it is copied into the venv and
loses its link to the base interpreter's DLLs and import path. Instead, the
installer registers the venv's own `python.exe` running THIS file, which then
calls the SCM dispatcher itself. That is the same interpreter that runs
`synthelion serve-dashboard` correctly from a shell, so nothing about the
environment has to be reconstructed.

Design:
  * One installed Windows service per Synthelion server. The service name is
    passed on the command line by the registered ImagePath; the target
    (serve-proxy / serve-dashboard) and the user's config path are read from
    that service's registry Parameters key, written by the installer.
  * The server runs in a daemon worker thread; the main thread blocks on a stop
    event that ``SvcStop`` sets, so a stop request returns quickly.
  * Running under the SCM means LocalSystem, whose home directory is not the
    user's, so the recorded config path is exported as ``SYNTHELION_CONFIG``
    before the config module loads.

pywin32 is imported defensively: on non-Windows the import fails and the service
class is simply not defined, keeping the package and test suite importable on
Linux/macOS/CI.
"""
from __future__ import annotations

import os
import sys
import threading

# Valid targets a service may run. Keep in sync with service.py.
VALID_TARGETS = ("serve-proxy", "serve-dashboard")

try:  # pragma: no cover - Windows-only
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    _HAVE_PYWIN32 = True
except ImportError:
    _HAVE_PYWIN32 = False


def _service_registry(service_name: str) -> tuple[str, str | None]:
    """Return (target, config_path) recorded for this service, with safe
    fallbacks if the keys are missing."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            rf"SYSTEM\CurrentControlSet\Services\{service_name}\Parameters",
        )
        try:
            target, _ = winreg.QueryValueEx(key, "Target")
            try:
                config_path, _ = winreg.QueryValueEx(key, "ConfigPath")
            except OSError:
                config_path = None
        finally:
            winreg.CloseKey(key)
        if target not in VALID_TARGETS:
            target = "serve-proxy"
        return target, (config_path or None)
    except OSError:
        return "serve-proxy", None


def _run_target(target: str, config_path: str | None = None) -> None:
    """Start the blocking Synthelion server for ``target`` in this thread.

    ``config_path`` is exported as SYNTHELION_CONFIG *before* importing the
    config module, so a service running as LocalSystem still reads the user's
    config (config.py checks SYNTHELION_CONFIG first)."""
    if config_path:
        os.environ["SYNTHELION_CONFIG"] = config_path
    if target == "serve-proxy":
        from synthelion.config import load_config
        from synthelion.plugins.proxy import run_proxy
        pcfg = load_config().get("proxy", {})
        run_proxy(host=pcfg.get("host", "127.0.0.1"), port=pcfg.get("port", 8788))
    elif target == "serve-dashboard":
        from synthelion.config import load_config
        from synthelion.plugins.dashboard import run_dashboard
        dcfg = load_config().get("dashboard", {})
        run_dashboard(host=dcfg.get("host", "127.0.0.1"), port=dcfg.get("port", 8787))
    else:
        raise ValueError(f"unknown service target: {target!r}")


def parse_service_name(argv: list[str], default: str = "synthelion-proxy") -> str:
    """Extract --service-name from the argv the SCM starts us with.

    The installer bakes this into the service's ImagePath so a single script can
    back several services (dashboard and proxy) and still know which one it is.
    """
    for i, a in enumerate(argv):
        if a == "--service-name" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--service-name="):
            return a.split("=", 1)[1]
    return default


if _HAVE_PYWIN32:  # pragma: no cover - Windows-only

    class SynthelionService(win32serviceutil.ServiceFramework):
        _svc_name_ = "synthelion-proxy"
        _svc_display_name_ = "Synthelion"
        _svc_description_ = "Synthelion always-on service"

        def __init__(self, args):
            super().__init__(args)
            self._name = args[0] if args else self._svc_name_
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._worker: threading.Thread | None = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self):
            # Tell the SCM we're coming up before doing any work, so a slow
            # import can never be mistaken for a hung service (error 1053).
            self.ReportServiceStatus(
                win32service.SERVICE_START_PENDING, waitHint=30000
            )
            target, config_path = _service_registry(self._name)
            self._worker = threading.Thread(
                target=_run_target, args=(target, config_path), daemon=True
            )
            self._worker.start()
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - Windows-only
    """Entry point registered as the service binary.

    Started by the SCM as: python.exe win_service.py --service-name <name>
    """
    argv = list(sys.argv if argv is None else argv)
    if not _HAVE_PYWIN32:
        raise SystemExit("pywin32 is required to run the Windows service")
    name = parse_service_name(argv)
    SynthelionService._svc_name_ = name
    SynthelionService._svc_display_name_ = name
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(SynthelionService)
    servicemanager.StartServiceCtrlDispatcher()


if __name__ == "__main__":
    main()
