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

## Migration checklist (in progress)

The working implementation currently lives outside this repo and is being migrated here with a
careful scrub, because this is a public repo. Steps:

1. Bring in `watchdog.py` and `service_watchdog.py`.
2. **Scrub for public release:**
   - SMTP password default → require `NIGHTSHIFT_SMTP_PASSWORD` env var (no literal default).
   - Replace personal email/SMTP identities with config-driven values.
   - Replace the hardcoded `C:\Users\<name>` LocalSystem profile pin with a config/env-driven home.
   - Remove any personal paths from defaults; everything comes from `nightshift.config.json`.
3. Rename service identity to `PythonNightshiftWatchdog`; env prefix `AGENT_RUNNER_*` → `NIGHTSHIFT_*`.
4. Fix imports/tests to `nightshift.watchdog.*` and bring `test_watchdog.py` into `tests/`.
5. Verify: `python -m unittest discover -s tests` passes; a low-threshold dry-run discriminates
   stalled vs healthy; no literal secrets/paths remain (`git grep` clean before commit).

Until cutover, the live service continues to run from its existing location; this is a code move plus
a deliberate, separately-elevated service reinstall — not a hot edit of the running service.
