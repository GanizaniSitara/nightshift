# watchdog — liveness sensor

The one component already proven in production. It reads the launcher's per-run launch-info records
and the agent transcript, and classifies each managed run:

- **HEALTHY** — process alive and transcript/heartbeat advancing
- **STALLED** — process alive but transcript idle past `stall_seconds` and heartbeat idle past
  `heartbeat_seconds`
- **CRASHED** — process gone (or PID reused) and the work not marked done
- **DONE** — the run's task moved to its done state

It de-dupes alerts per episode (one alert per stall, not one per poll), gates alerting on managed
markers so ad-hoc sessions don't generate noise, and emits a ticket note + optional email. It
**observes and alerts only** — no restart, no kill.

## Run it as a plain module, not a service (for now)

The default is on-demand: `python -m nightshift.watchdog status | once | loop`. A long-running
loop is just `loop`. The Windows service wrapper (`service_watchdog.py`, pywin32) is **optional** and
deferred — we don't need it to iterate, and it adds elevation/cutover friction. Treat the service as
one possible host wrapper to add later, not a requirement.

## Status: migrated (runs as a plain module)

`watchdog.py` is in the repo and runs via `python -m nightshift.watchdog [once|loop|status]`.
Scrubbed for public release: no literal SMTP password (uses `NIGHTSHIFT_SMTP_PASSWORD`, empty
default), no personal email/SMTP identities (config-driven, email **off** by default), paths derive
from `USERPROFILE`/config, and the MCP/managed-marker identities are rebranded to `nightshift`.
`tests/test_watchdog.py` covers it.

### Optional: deploy as a Windows service
The plain module is the default, but you can run the watchdog always-on as a Windows service
(`service_watchdog.py`, pywin32). Service identity `PythonNightshiftWatchdog`. Operate it from the
repo root:

```
refresh.cmd   :: install / reinstall / start (self-elevates via UAC)
remove.cmd    :: stop and remove
```

Both self-elevate and are location-independent. If `python` on PATH lacks pywin32, set `PY` first:
`set "PY=C:\path\to\env\python.exe"`. Under LocalSystem, point the service at your real profile with
`NIGHTSHIFT_USER_HOME=C:\Users\you` (or just use absolute paths in `nightshift.config.json`). The
service reads `config\nightshift.config.json` (override with `NIGHTSHIFT_WATCHDOG_CONFIG`).

The legacy `PythonAgentRunnerWatchdog` service from the old `agent-runner` location is separate and
untouched; retire it deliberately (its own `remove.cmd`) before installing this one to avoid two
watchdogs running at once.
