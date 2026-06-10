"""The host-agnostic orchestration brain: goals, plans, runs, shifts, reporting.

Pure data and decision logic — no process launching, no host paths. The
host-specific "hands" live in ``nightshift.dispatch`` and ``nightshift.watchdog``.
"""

from .records import Goal, Plan, Run, RunStatus, Shift

__all__ = ["Goal", "Plan", "Run", "RunStatus", "Shift"]
