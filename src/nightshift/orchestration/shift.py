"""Shift lifecycle: open a shift, pick the next step, route it.

Kept deliberately thin. ``route_or_run`` applies the trust boundary — an
increment that no Verifier can judge unattended (or that needs a device/human)
is routed to the human-gated queue (a BLOCKED run) instead of being fired blind.
Actual process launch is the dispatcher's job (out of scope here).
"""

from __future__ import annotations

import datetime as dt

from ..verifiers import registry
from ..verifiers.base import Increment
from .records import Goal, Run, RunStatus, Shift


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def start(goal: Goal, *, seats: int = 1, started_at: str | None = None) -> Shift:
    return Shift(id=f"shift-{goal.id}", goal_id=goal.id, started_at=started_at or _now(), seats=seats)


def pick_next_step(
    candidates: list[Increment], done_ids: set[str] | None = None
) -> Increment | None:
    """Return the next not-yet-done candidate increment, or None if none remain."""
    done = done_ids or set()
    for increment in candidates:
        if increment.id not in done:
            return increment
    return None


def route_or_run(shift: Shift, increment: Increment, *, run_id: str, tool: str = "claude") -> Run:
    """Decide unattended vs human-gated for an increment and return the resulting Run."""
    if registry.can_verify_unattended(increment):
        return Run(
            id=run_id,
            shift_id=shift.id,
            increment_id=increment.id,
            tool=tool,
            status=RunStatus.PENDING,
            notes="unattended: verifier available, no device/human required",
        )
    reason = ", ".join(increment.requires) or "no verifier for deliverable type"
    return Run(
        id=run_id,
        shift_id=shift.id,
        increment_id=increment.id,
        tool=tool,
        status=RunStatus.BLOCKED,
        notes=f"human-gated: {reason}",
    )
