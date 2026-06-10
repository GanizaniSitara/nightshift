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

### Optional: deploy as a Windows service (to `C:\servers`)
The plain module is the default, but you can run the watchdog always-on as a Windows service
(`service_watchdog.py`, pywin32), service identity `PythonNightshiftWatchdog`. The deploy model
separates **source** (this repo) from **runtime** (`C:\servers\nightshift`):

```
refresh.cmd   :: from the SOURCE repo: stop+remove old service, copy runtime to
              :: C:\servers\nightshift, then install+start from there. Self-elevates.
remove.cmd    :: stop and remove the service by name (run from source or runtime).
```

Order matters and is deliberate: a **running service locks its own files**, so `refresh.cmd`
stops + removes the existing registration *before* copying — never copy first. It also removes
**by service name**, so changing the deploy path is safe. Run the **source** copy of `refresh.cmd`,
not the one under `C:\servers\nightshift` (the deploy step would overwrite a running script).

- Interpreter defaults to the local Python with pywin32; override with `set "PY=...python.exe"`.
- Runtime config: `C:\servers\nightshift\config\nightshift.config.json` (create from the `.example`;
  override path with `NIGHTSHIFT_WATCHDOG_CONFIG`).
- Under LocalSystem, point at your real profile via `NIGHTSHIFT_USER_HOME=C:\Users\you`, or run the
  service under your own account, or just use absolute paths in the config.

**Before first install:** retire the legacy `PythonAgentRunnerWatchdog` (old `agent-runner` repo, its
own `remove.cmd`) — it's a different service name, so `refresh.cmd` won't touch it, and two watchdogs
polling the same sessions would double-alert.
