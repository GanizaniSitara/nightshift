r"""Optional Windows service wrapper for the Nightshift watchdog.

Service name: PythonNightshiftWatchdog.

The watchdog also runs as a plain module (``python -m nightshift.watchdog``); this
wrapper is only for deploying it as an always-on Windows service. It runs the
watchdog in-process so the same baseline/de-dupe state file applies whether the
watchdog is run manually or under SCM.

Install / refresh:  refresh.cmd   (self-elevates)
Remove:             remove.cmd

Requires pywin32 (``pip install nightshift[service]``).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
import socket
import sys
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

import watchdog  # sibling module (script dir is first on sys.path)


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGER = logging.getLogger("nightshift_watchdog_service")
LOGGER.setLevel(logging.INFO)
handler = RotatingFileHandler(
    LOG_DIR / "watchdog-service.log",
    maxBytes=2_000_000,
    backupCount=5,
    encoding="utf-8",
)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
LOGGER.addHandler(handler)


def pin_user_profile() -> None:
    """Pin user-scoped paths when running under LocalSystem.

    LocalSystem resolves USERPROFILE to the system profile, so a service that
    needs a real user's tasks/.claude must be told where they live. Set
    ``NIGHTSHIFT_USER_HOME`` (e.g. ``C:\\Users\\you``) to pin it. If unset, the
    environment is left as-is — prefer absolute paths in the config instead.
    """
    home = os.environ.get("NIGHTSHIFT_USER_HOME")
    if not home:
        return
    os.environ["USERPROFILE"] = home
    drive, sep, tail = home.partition(":")
    if sep and tail:
        os.environ["HOMEDRIVE"] = f"{drive}:"
        os.environ["HOMEPATH"] = tail


def load_service_config() -> dict:
    config_env = os.environ.get("NIGHTSHIFT_WATCHDOG_CONFIG")
    config_path = Path(config_env) if config_env else REPO_ROOT / "config" / "nightshift.config.json"
    if config_path.exists():
        LOGGER.info("loading config %s", config_path)
        return watchdog.load_config(config_path)
    LOGGER.info("using default watchdog config")
    return watchdog.load_config(None)


def summarize(observations: list["watchdog.Observation"]) -> str:
    if not observations:
        return "no active sessions"
    parts = []
    for obs in observations:
        actions = ",".join(obs.actions) if obs.actions else "-"
        errors = ",".join(obs.errors) if obs.errors else "-"
        parts.append(f"{obs.launch.task_slug}:{obs.state}:actions={actions}:errors={errors}")
    return " | ".join(parts)


class NightshiftWatchdogService(win32serviceutil.ServiceFramework):
    _svc_name_ = "PythonNightshiftWatchdog"
    _svc_display_name_ = "Python Nightshift Watchdog"
    _svc_description_ = "Liveness watchdog for launcher-managed agent sessions (Nightshift)"
    _svc_startup_type_ = win32service.SERVICE_AUTO_START

    def __init__(self, args):
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        socket.setdefaulttimeout(60)

    def SvcStop(self):
        LOGGER.info("SvcStop received")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self.main()

    def main(self):
        pin_user_profile()
        config = load_service_config()
        poll_ms = int(float(config["poll_seconds"]) * 1000)
        LOGGER.info(
            "service loop starting poll_seconds=%s state_file=%s",
            config["poll_seconds"],
            config["state_file"],
        )

        while True:
            try:
                observations = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                    include_existing=False,
                )
                LOGGER.info("cycle %s", summarize(observations))
            except Exception:
                LOGGER.exception("watchdog cycle failed")

            rc = win32event.WaitForSingleObject(self.hWaitStop, poll_ms)
            if rc == win32event.WAIT_OBJECT_0:
                LOGGER.info("stop signalled, exiting")
                break


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(NightshiftWatchdogService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(NightshiftWatchdogService)
