"""End-of-shift report.

Groups runs by outcome and surfaces wasted capacity. Answers: how much verified
progress toward the goal, and where was capacity wasted?
"""

from __future__ import annotations

from collections import defaultdict

from .records import Run, RunStatus, Shift

_GROUPS: list[tuple[str, set[RunStatus]]] = [
    ("Verified", {RunStatus.VERIFIED, RunStatus.DONE}),
    ("Needs review", {RunStatus.NEEDS_REVIEW}),
    ("Blocked / human-gated", {RunStatus.BLOCKED}),
    ("Stalled / crashed", {RunStatus.STALLED, RunStatus.CRASHED}),
    ("Still running", {RunStatus.RUNNING, RunStatus.PENDING}),
]


def render(shift: Shift, runs: list[Run]) -> str:
    by_status: dict[RunStatus, list[Run]] = defaultdict(list)
    for run in runs:
        by_status[run.status].append(run)

    lines = [
        f"Shift {shift.id} - goal {shift.goal_id}",
        f"started {shift.started_at}  seats {shift.seats}  runs {len(runs)}",
        "",
    ]
    for title, statuses in _GROUPS:
        group = [run for status in statuses for run in by_status.get(status, [])]
        lines.append(f"{title}: {len(group)}")
        for run in group:
            note = f" - {run.notes}" if run.notes else ""
            lines.append(f"  - {run.increment_id} [{run.tool}]{note}")

    wasted = [r for r in runs if r.status in {RunStatus.STALLED, RunStatus.CRASHED}]
    if not runs:
        lines += ["", "Capacity unused: no runs this shift."]
    elif wasted:
        lines += ["", f"Capacity wasted: {len(wasted)} run(s) stalled/crashed."]
    return "\n".join(lines)
