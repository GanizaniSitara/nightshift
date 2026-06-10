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

## Migration checklist (in progress)

The working implementation currently lives outside this repo and is being migrated here with a
careful scrub, because this is a public repo. Steps:

1. Bring in `watchdog.py` (the logic) and add a `__main__.py` so `python -m nightshift.watchdog`
   runs `status | once | loop`.
2. **Scrub for public release:**
   - SMTP password default → require `NIGHTSHIFT_SMTP_PASSWORD` env var (no literal default).
   - Replace personal email/SMTP identities with config-driven values.
   - Remove any personal paths from defaults; everything comes from `nightshift.config.json`.
3. Fix imports/tests to `nightshift.watchdog.*` and bring `test_watchdog.py` into `tests/`.
4. Verify: `python -m unittest discover -s tests` passes; a low-threshold dry-run discriminates
   stalled vs healthy; no literal secrets/paths remain (`git grep` clean before commit).
5. **Optional, later:** `service_watchdog.py` (pywin32) as a Windows service wrapper —
   rename identity to `PythonNightshiftWatchdog`, env prefix `AGENT_RUNNER_*` → `NIGHTSHIFT_*`,
   config/env-driven profile home (drop the hardcoded `C:\Users\<name>` pin). Not needed to run.

The existing `PythonAgentRunnerWatchdog` service keeps running from its old location meanwhile;
nothing here hot-edits it. The plain module is the default; the service is a deferred add-on.
