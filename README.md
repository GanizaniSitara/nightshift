# Nightshift

**Turn unattended AI-agent capacity into verified, incremental progress toward a goal.**

Nightshift is a shift manager for coding agents. While you're asleep, at work, or away for a
weekend, it runs disposable agent workers (Claude, Codex, Copilot) against a goal, **verifies their
output mechanically**, and reports what was built, what passed, what needs your eye, and where
capacity was wasted.

It is inspired by the *idea* behind Gas Town — state lives outside the agent, and agents are
disposable workers — without rebuilding its infrastructure. Nightshift stays deliberately boring:
use the slot, finish the work, verify it, report what happened.

## The problem it solves

Today, long agent runs need hand-holding. The agent hits a decision it can't resolve, drifts from
intent, stops early, or produces work nobody can tell is good. Nightshift removes the hand-holding by:

1. **Interviewing once, up front** — extract the acceptance criteria, then run unattended.
2. **Decomposing into self-verifying increments** — state lives in git + the ticket, so a dead
   context loses nothing.
3. **Branching instead of blocking** — at a genuine fork the worker builds *variants* for you to
   judge; at a real blocker it files the blocker and moves to the next independent increment.
4. **Verifying mechanically** — a per-deliverable Verifier decides pass/fail/needs-human.
5. **Reporting** — what was built, verified, left for review, blocked, or wasted.

## The core idea: verification is per *deliverable type*

The one genuinely new primitive is the **Verifier**, keyed by the kind of thing produced — not the
source language. A project can register several.

| Deliverable type | Verifier |
| --- | --- |
| Web app | Playwright load → screenshot → vision-model critique vs a rubric + e2e/unit tests |
| iOS app | `xcodebuild` build/test → boot simulator → screenshot → vision critique |
| PDF document | render pages → vision critique vs a layout/quality rubric |
| CLI / library / service | run tests + smoke-run + parse output |

The **trust boundary falls out of the registry**: an increment is *unattended-eligible* iff a
Verifier exists for its deliverable type **and** the check needs no physical device or human taste.
Increments that declare `requires: device` or `requires: human` auto-route to a human-gated queue —
never attempted blind.

## Model

| Level | Meaning |
| --- | --- |
| **Goal** | The semantic outcome a shift advances |
| **Plan** | A solution approach for a goal (human- or agent-written) |
| **Step / Increment** | A concrete build/test increment of a plan |
| **Run** | One managed agent attempt at one increment during a shift |
| **Session** | The actual agent process / window / transcript / PID |

The watchdog operates on **runs and sessions**, not raw tasks.

## Components

```
src/nightshift/
  orchestration/   the host-agnostic brain: goals, plans, runs, shift lifecycle, report
  dispatch/        the host-specific hands: launch a managed run, mark it managed
  verifiers/       the deliverable-type Verifier registry (web / iOS / PDF / CLI)
  watchdog/        the liveness sensor: is the run alive, moving, blocked, or done?
  worker/          the worker contract injected into every managed run
```

## Status

- **Built:** the liveness watchdog (process / transcript / heartbeat → HEALTHY · STALLED · CRASHED ·
  DONE, de-duped alerting). Runs as a background service.
- **Scaffolded (contracts + stubs):** the Verifier registry, orchestration records, dispatcher,
  worker contract, end-of-shift report.
- **Next:** the first real Verifier (web), then the thinnest shift loop around it. Nothing heavier
  gets built until a Verifier verdict has earned trust.

## Autonomy is a ratchet, not a switch

You earn unattendedness in rungs, each safe only because of self-verification + the watchdog +
findings-in-git: approve plans → watch one verified increment → chain low-risk verifiable increments
unattended → big features + variants.

## License

MIT.
