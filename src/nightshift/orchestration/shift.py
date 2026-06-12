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


def _launch_info(slug: str, config: dict[str, Any]):
    from ..watchdog.watchdog import load_launch_info

    path = Path(str(config.get("tasks_root", ""))) / "in-progress" / f"{slug}.launch-info.json"
    if not path.exists():
        return None
    return load_launch_info(path)


def session_alive(slug: str, config: dict[str, Any]) -> bool | None:
    """True/False if determinable from launch-info; None if no launch-info exists."""
    from ..watchdog.watchdog import check_process

    info = _launch_info(slug, config)
    if info is None:
        return None
    return check_process(info, float(config.get("process_start_tolerance_seconds", 5))).alive


def session_pid(slug: str, config: dict[str, Any]) -> int:
    info = _launch_info(slug, config)
    return info.pid if info else 0


def _note(config: dict[str, Any], task_id: str, note: str, heading: str = "Nightshift") -> None:
    """Best-effort progress note to the ticket (also bumps heartbeat)."""
    from ..watchdog.watchdog import TasksMcpClient

    try:
        client = TasksMcpClient(
            str(config.get("mcp_url", "http://127.0.0.1:8876/mcp")),
            float(config.get("mcp_timeout_seconds", 10)),
        )
        client.append_task_note(task_id, note, heading=heading)
    except Exception:
        pass


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
    from ..watchdog.watchdog import task_id_from_slug
    from ..watchdog.nudger import probe_and_nudge

    poll_seconds = float(config.get("poll_seconds", 60))
    max_seconds = float(config.get("max_run_seconds", 7200))
    nudge_enabled = bool(config.get("nudge_enabled", True))
    nudge_cooldown = float(config.get("nudge_probe_cooldown_seconds", 300))
    slug = increment.id
    task_id = task_id_from_slug(slug) or slug
    started = time.monotonic()
    last_probe = started  # first probe one cooldown in (a fresh session can't be rate-limited yet)
    nudge_count = 0

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

        # Auto-unstick: every cooldown, peek the console; if it's parked on a
        # rate-limit / compaction prompt, revive it ("just say continue"). Only
        # ever acts on a CONFIRMED recoverable prompt, never a working session.
        # Probe frequency is bounded by the cooldown, and max_run_seconds is the
        # backstop — no nudge storm.
        if nudge_enabled and (time.monotonic() - last_probe) >= nudge_cooldown:
            last_probe = time.monotonic()
            pid = session_pid(slug, config)
            if pid:
                res = probe_and_nudge(pid, tool=run.tool, apply=True, config=config)
                if res.nudged:
                    nudge_count += 1
                    if on_poll:
                        on_poll(f"NUDGE: {res.state} -> {res.action} (verified={res.verified})")
                    _note(config, task_id,
                          f"Session was parked on {res.state}; sent {res.action} (verified={res.verified}).")
                elif res.needs_human:
                    # A detected stuck state with no auto-revival (e.g. Codex usage
                    # cap). Don't burn the slot to timeout — flag precisely and stop.
                    _note(config, task_id,
                          f"Session hit {res.state} ({run.tool}); this tool won't self-resume — "
                          f"abandoning the run for review (try a different seat or wait for reset).")
                    run.status = RunStatus.STALLED
                    run.notes = f"{res.state}: {run.tool} won't self-resume (needs you)"
                    if on_poll:
                        on_poll(f"STUCK: {res.state} ({run.tool}) — needs you; stopping run")
                    break
                elif res.state == "attach-failed" and on_poll:
                    on_poll("nudge probe: attach-failed (monitor must run in the session, not session 0)")

        if on_poll:
            on_poll(f"waiting: done=False alive={alive}")
        sleep(poll_seconds)

    if nudge_count:
        run.notes = (run.notes + "; " if run.notes else "") + f"auto-nudged {nudge_count}x"

    run.ended_at = dt.datetime.now(dt.timezone.utc).isoformat()

    if run.status == RunStatus.DONE:
        # The agent signals done via the tasks MCP move, which doesn't remove the
        # launcher's metadata file (the launcher's own `done` command would have).
        # Clean it up here so no orphaned launch-info lingers in in-progress/.
        leftover = Path(str(config.get("tasks_root", ""))) / "in-progress" / f"{slug}.launch-info.json"
        if leftover.exists():
            leftover.unlink()

    if run.status == RunStatus.DONE and increment.target and increment.rubric_path:
        # Server-rendered app: restart it from the current branch before capture,
        # so the verifier judges the just-committed code (not a stale instance).
        serve_cmd = config.get("serve_cmd")
        if serve_cmd:
            from urllib.parse import urlparse
            from ..serve import restart_app

            port = urlparse(increment.target).port or 80
            r = restart_app(serve_cmd, port)
            if on_poll:
                on_poll(f"restarted app on :{port} for verify (ok={r.get('ok')}, killed={r.get('killed')})")
        verifier = registry.get(increment.deliverable_type)
        if verifier is not None:
            try:
                result = verifier.verify(increment, config=config)
            except Exception as exc:  # a verifier fault must not crash the shift
                run.status = RunStatus.NEEDS_REVIEW
                run.notes = (run.notes + "; " if run.notes else "") + f"verifier error: {type(exc).__name__}: {exc}"[:200]
                return run
            run.evidence_paths.extend(result.evidence_paths)
            findings = [
                {"rubric_item": f.rubric_item, "verdict": f.verdict.value, "notes": f.notes}
                for f in result.vision_findings
            ]
            run.verification = {
                "verdict": result.verdict.value,
                "findings": findings,
                "screenshots": result.screenshots,
                "notes": result.notes,
            }
            run.status = RunStatus.VERIFIED if result.verdict == Verdict.PASS else RunStatus.NEEDS_REVIEW
            run.notes = (run.notes + "; " if run.notes else "") + f"verifier verdict={result.verdict.value}"
            if result.verdict != Verdict.PASS:
                fails = [
                    f"- [{f['verdict']}] {f['rubric_item']}: {f['notes']}"
                    for f in findings if f["verdict"] != "pass"
                ] or ["(verifier returned no per-rubric detail)"]
                _note(
                    config, task_id,
                    f"Verifier verdict {result.verdict.value} on {increment.target}.\n"
                    f"Findings:\n" + "\n".join(fails)
                    + (f"\nScreenshot: {';'.join(result.screenshots)}" if result.screenshots else ""),
                    heading="Verifier",
                )
    return run
