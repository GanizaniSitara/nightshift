"""Shift lifecycle: start a shift, pick the next useful step, dispatch, verify, report.

This is the loop, kept deliberately thin. It selects a step (ready plan, else
bounded design/recon, else verify earlier work), turns it into a managed run
prompt under the worker contract, dispatches one run, lets the watchdog watch it,
and on completion either verifies + records or routes to the human-gated queue.

Nothing here is built until a Verifier has earned trust on a real increment.
"""

from __future__ import annotations

from ..verifiers import Increment, registry
from .records import Goal, Run, Shift


def start(goal: Goal, *, seats: int = 1) -> Shift:
    """Open a shift for a goal. Implemented after the first Verifier is proven."""
    raise NotImplementedError("shift.start")


def pick_next_step(shift: Shift) -> Increment | None:
    """Choose the next useful increment toward the goal, or None if no safe work remains."""
    raise NotImplementedError("shift.pick_next_step")


def route_or_run(shift: Shift, increment: Increment) -> Run:
    """Dispatch unattended if ``registry.can_verify_unattended``; else human-gated queue."""
    raise NotImplementedError("shift.route_or_run")
