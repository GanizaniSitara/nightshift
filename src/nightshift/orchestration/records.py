"""The four-level work model: Goal -> Plan -> Run (over an Increment) within a Shift.

These are the durable records. A Run is one managed agent attempt at one
increment; a Session (process/PID/transcript) is tracked separately by the
watchdog. The watchdog operates on runs and sessions, not raw tasks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    VERIFIED = "verified"        # built and the Verifier passed
    NEEDS_REVIEW = "needs-review"  # built but verdict was needs-human / variants await an eye
    BLOCKED = "blocked"          # needs a device, credential, or asset; filed and skipped
    STALLED = "stalled"          # watchdog flagged no progress
    CRASHED = "crashed"          # watchdog flagged process gone
    DONE = "done"


@dataclass
class Goal:
    """The semantic outcome a shift advances, e.g. 'polish the review screen'.

    Not a flat ticket queue: the goal carries intent the orchestrator derives
    useful next steps from.
    """

    id: str
    intent: str
    project: str | None = None


@dataclass
class Plan:
    """A solution approach for a goal, human- or agent-written.

    ``ready`` marks a plan as eligible for unattended execution; ``increments``
    are the ordered increment ids it decomposes into.
    """

    id: str
    goal_id: str
    summary: str
    increments: list[str] = field(default_factory=list)
    ready: bool = False


@dataclass
class Run:
    """One managed agent attempt at one increment during a shift."""

    id: str
    shift_id: str
    increment_id: str
    tool: str = "claude"  # claude | codex | copilot
    branch: str | None = None
    status: RunStatus = RunStatus.PENDING
    started_at: str | None = None
    ended_at: str | None = None
    evidence_paths: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Shift:
    """A bounded window of unattended capacity aimed at a goal.

    First version optimises for a single seat; ``seats`` is here so multi-seat
    pooling can come later without reshaping the record.
    """

    id: str
    goal_id: str
    started_at: str
    ends_at: str | None = None
    seats: int = 1
    runs: list[str] = field(default_factory=list)
