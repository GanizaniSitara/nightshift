"""Shift lifecycle: open a shift, pick the next step, route it, monitor a run.

Kept deliberately thin. ``route_or_run`` applies the trust boundary — an
increment that no Verifier can judge unattended (or that needs a device/human)
is routed to the human-gated queue (a BLOCKED run) instead of being fired blind.
``monitor_run`` polls a dispatched run to a terminal state and verifies on done.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any, Callable

from ..verifiers import registry
from ..verifiers.base import Increment, Verdict
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


def task_is_done(slug: str, config: dict[str, Any]) -> bool:
    done_dir = Path(str(config.get("tasks_root", ""))) / "done"
    return any(done_dir.glob(f"{slug}*.md"))


def session_alive(slug: str, config: dict[str, Any]) -> bool | None:
    """True/False if determinable from launch-info; None if no launch-info exists."""
    from ..watchdog.watchdog import check_process, load_launch_info

    path = Path(str(config.get("tasks_root", ""))) / "in-progress" / f"{slug}.launch-info.json"
    if not path.exists():
        return None
    info = load_launch_info(path)
    if info is None:
        return None
    return check_process(info, float(config.get("process_start_tolerance_seconds", 5))).alive


def monitor_run(
    run: Run,
    increment: Increment,
    config: dict[str, Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_poll: Callable[[str], None] | None = None,
) -> Run:
    """Poll a dispatched run to a terminal state; verify on done when possible.

    Terminal states: DONE/VERIFIED/NEEDS_REVIEW (ticket landed in done/),
    CRASHED (session process gone before done), STALLED with a timeout note
    (max_run_seconds exceeded — recorded, never killed: no auto-kill by design).
    """
    poll_seconds = float(config.get("poll_seconds", 60))
    max_seconds = float(config.get("max_run_seconds", 7200))
    slug = increment.id
    started = time.monotonic()

    while True:
        if task_is_done(slug, config):
            run.status = RunStatus.DONE
            break
        alive = session_alive(slug, config)
        if alive is False:
            run.status = RunStatus.CRASHED
            run.notes = "session process gone before ticket reached done"
            break
        if time.monotonic() - started > max_seconds:
            run.status = RunStatus.STALLED
            run.notes = f"timeout after {int(max_seconds)}s (run left alive; review manually)"
            break
        if on_poll:
            on_poll(f"waiting: done={False} alive={alive}")
        sleep(poll_seconds)

    run.ended_at = dt.datetime.now(dt.timezone.utc).isoformat()

    if run.status == RunStatus.DONE:
        # The agent signals done via the tasks MCP move, which doesn't remove the
        # launcher's metadata file (the launcher's own `done` command would have).
        # Clean it up here so no orphaned launch-info lingers in in-progress/.
        leftover = Path(str(config.get("tasks_root", ""))) / "in-progress" / f"{slug}.launch-info.json"
        if leftover.exists():
            leftover.unlink()

    if run.status == RunStatus.DONE and increment.target and increment.rubric_path:
        verifier = registry.get(increment.deliverable_type)
        if verifier is not None:
            result = verifier.verify(increment, config=config)
            run.evidence_paths.extend(result.evidence_paths)
            run.status = RunStatus.VERIFIED if result.verdict == Verdict.PASS else RunStatus.NEEDS_REVIEW
            run.notes = (run.notes + "; " if run.notes else "") + f"verifier verdict={result.verdict.value}"
    return run
