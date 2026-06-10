# Nightshift architecture

Two layers, deliberately separated.

## The brain (host-agnostic) — `nightshift.orchestration`

Pure data and decision logic. No process launching, no host paths. Owns the work model
(Goal → Plan → Run over an Increment, within a Shift), the ready-plan/next-step queue, the shift
loop, and the end-of-shift report. Because it's host-neutral, the same brain can drive runs on any
machine where the code being built actually compiles.

## The hands (host-specific) — `nightshift.dispatch`, `nightshift.watchdog`

- **dispatch** launches one managed run by reusing the platform's existing launcher, then stamps the
  managed markers the watchdog gates on (the launcher stays untouched).
- **watchdog** is the liveness sensor. It reads the launcher's run records and the agent transcript
  and classifies each run HEALTHY · STALLED · CRASHED · DONE, with de-duped alerting. It observes and
  alerts; it does not restart or kill (recovery is a separate, later concern).

## The oracle (pluggable) — `nightshift.verifiers`

A registry of Verifiers keyed by **deliverable type** (web, iOS, PDF, CLI). The single new primitive.
The trust boundary for unattended work is derived here: an increment runs unattended only if a
Verifier covers its deliverable type *and* it needs no physical device or human taste.

## The contract — `nightshift.worker`

The instructions injected into every managed run: one increment, persist findings early, branch
instead of blocking, explicit done signal, completeness first. See `worker/contract.md`.

## Why this split

The brain is the reusable IP and the easiest to test (no side effects). The hands are where
OS/host reality lives and will differ per platform (a Windows service today; a macOS runner for
Xcode-only projects later). Keeping them apart means a new host is a new "hands" implementation, not
a rewrite — and a new deliverable type is a new registry entry, not a change to the brain.
