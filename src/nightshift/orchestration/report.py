"""End-of-shift report.

Answers the core metric: how much useful progress toward the goal, how much is
verified, and where was capacity wasted? Enumerates each waste case explicitly
(no task queued, finished early with no next step, stopped before tests, waited
on a prompt, stalled while the slot burned, result not summarised, activity but
no movement toward the goal).
"""

from __future__ import annotations

from .records import Shift


def render(shift: Shift) -> str:
    """Produce the human-readable end-of-shift report. Implemented later."""
    raise NotImplementedError("report.render")
