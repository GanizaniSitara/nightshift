# Nightshift design

## Purpose

Use unattended time — overnight, during the workday, a weekend block — to make active design, build,
and incremental progress toward a broader goal, instead of leaving agent capacity idle. Boring on
purpose: use the slot, finish the work, verify it, report what happened.

## What a shift is

A bounded window where the user isn't steering: a start/end time, one or more agent seats, a broader
semantic goal, ready plans or next-step candidates, active managed runs, and a report at the end. The
core metric is not "did an agent run?" but *how much verified progress toward the goal, and where was
capacity wasted?*

## Input model — semantic, not a flat ticket queue

The input is a semantic understanding of what the product/workstream should become, not a batch list
of tickets to drain. Tickets are useful handles for history and evidence, but the shift chooses steps
by **product intent**: what's missing, what direction is already chosen, what's safe to do without
the user present, what would most improve the product by shift end.

## Borrowed from Gas Town (idea, not implementation)

The useful idea: **state lives outside the agent; agents are disposable workers.** Borrow: a semantic
work graph, reusable run recipes, a worker contract, persistent findings outside the transcript, an
explicit done signal, a mechanical watcher, a later review step, a capacity scheduler. Do **not**
borrow: the names, the data-versioning store, the daemon zoo, the worker swarm, containers, or the
merge-queue complexity. Agents do the work; Nightshift owns context, sequencing, state, and
completion discipline.

## The four-level model

| Level | Meaning |
| --- | --- |
| Goal | The semantic outcome a shift advances |
| Plan | A solution approach for a goal (human- or agent-written) |
| Step / Increment | A concrete build/test increment of a plan |
| Run | One managed agent attempt at one increment during a shift |
| Session | The actual agent process / window / transcript / PID |

The watcher operates on runs and sessions, not raw tasks.

## The mechanical loop

1. The user gives a broader intent, a task, or a ready plan.
2. Nightshift picks the next useful step: execute a ready plan; else bounded design/recon to make the
   next build safe; else verify/test earlier work.
3. It turns the step into a managed run prompt under the worker contract (build, test, verify, record
   changes; stop only when done, blocked, or out of useful work).
4. The dispatcher launches one run via the existing launcher.
5. The run is marked managed (launch metadata).
6. The watchdog monitors mechanically: process alive? transcript moving? heartbeat fresh? done?
7. On stall/crash, the watchdog alerts (it does not restart or kill).
8. If a run finishes early and time remains, Nightshift picks the next useful step.
9. At shift end, the report says what was designed, built, tested, blocked, still running, or wasted.

## Verification (see verifier-contract.md)

A per-deliverable-type Verifier decides pass / fail / needs-human. The trust boundary for unattended
work falls out of the registry. Polish gets an oracle two ways: a **rubric-skill** the worker
self-critiques against (raises the floor, via the Verifier's screenshot + vision pass), and
**variants** for the subjective ceiling (the user judges; one seat ⇒ variants are sequential and
budgeted, not a parallel army).

## Worked example (generic)

Project `PROJ-17`, a web app. Goal: "polish the dashboard." Interview extracts acceptance criteria →
decompose into increments (e.g. "tighten dashboard spacing & button hierarchy"). The increment is
`deliverable_type: web`, `requires: []` → unattended-eligible. The worker builds on a branch, the web
Verifier loads the page, screenshots it, and scores it against the spacing/hierarchy rubric; pass →
recorded for review. A sibling increment "wire up hardware sensor X" declares `requires: device` →
auto-routed to the human-gated queue, never attempted blind. A "redesign the empty state" increment
is a genuine fork → the worker produces two variants on branches for the user to pick.

## Autonomy ratchet

Earn unattendedness in rungs, each safe only because of self-verification + watchdog +
findings-in-git: approve plans → watch one verified increment → chain low-risk verifiable increments
unattended → big features + variants.

## First practical version (build order)

The system balloons into a mess if the framework is built before a Verifier has returned a real
verdict. So:

1. **One Verifier tracer bullet** (web): standalone, render → screenshot → vision-vs-rubric →
   `VerificationResult`. Run by hand; prove the verdict is trustworthy *and discriminating*.
2. Then the thinnest shift loop around it: ready-queue (hydrated from backlog), `shift.start`,
   dispatcher that stamps managed markers, end-of-shift report.
3. Then variants and the polish rubric-skill.
4. Only after that: auto-start the next step, and multi-seat pooling.

## Non-goals (for now)

No daemon zoo, no data-versioning store, no merge queue, no automatic code-review agent, no automatic
restart/kill, no broad task invention, no parallel worker swarm.
