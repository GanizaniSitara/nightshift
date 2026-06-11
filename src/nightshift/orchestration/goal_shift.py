"""Goal shift: execute a goal's ordered increments coherently through managed runs.

This is the layer that makes Nightshift a solution manager rather than a ticket
manager. The shift works a GOAL (intent + ordered increments), not the backlog:

- one transport ticket is created per increment (handle + evidence, not steering)
- all increments share one workspace: the goal's repo + goal branch, so the
  output accumulates as commits in ONE place — the shift's product delta is a
  single reviewable branch diff
- each run's outcome is threaded into the next run's brief (goal context), so
  workers build on each other instead of starting cold
- the report speaks at goal level: what the product became, not tickets closed

Conservative sequencing: increments are ordered and assumed dependent, so the
shift STOPS on CRASHED/STALLED (and, by default, on a failed verification) rather
than building on a broken base. Human-gated increments (requires device/human)
are recorded BLOCKED and skipped — never attempted blind.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..dispatch.dispatcher import dispatch_run, save_run_record
from ..verifiers import registry
from ..watchdog.watchdog import TasksMcpClient
from .goals import GoalSpec, IncrementSpec
from .records import Run, RunStatus
from .shift import monitor_run

TERMINAL_OK = {RunStatus.DONE, RunStatus.VERIFIED}


def ensure_goal_branch(repo: str, branch: str) -> str:
    """Make sure the goal branch exists (created from current HEAD if missing)."""
    probe = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", branch],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        return "exists"
    create = subprocess.run(
        ["git", "-C", repo, "branch", branch],
        capture_output=True, text=True,
    )
    if create.returncode != 0:
        raise RuntimeError(f"could not create branch {branch} in {repo}: {create.stderr.strip()}")
    return "created"


def branch_commits_since(repo: str, branch: str, since_iso: str) -> list[str]:
    log = subprocess.run(
        ["git", "-C", repo, "log", branch, f"--since={since_iso}", "--oneline"],
        capture_output=True, text=True,
    )
    if log.returncode != 0:
        return []
    return [line for line in log.stdout.splitlines() if line.strip()]


def _workspace_text(goal: GoalSpec) -> str:
    if not goal.repo:
        return "This increment has no code workspace; the ticket is the deliverable surface."
    branch = goal.branch or "main"
    return (
        f"Workspace: `{goal.repo}`. Do ALL work on branch `{branch}` "
        f"(check it out first; it exists). Commit your work to that branch as you go. "
        f"Never commit to the default branch and never push."
    )


def _goal_context(goal: GoalSpec, prior: list[tuple[IncrementSpec, Run, str]]) -> str:
    lines = [
        f"You are working increment-by-increment toward this goal: **{goal.title}**.",
        "",
        goal.intent.strip(),
        "",
        _workspace_text(goal),
    ]
    if prior:
        lines += ["", "Previous increments this shift (read their tickets in tasks/done/ for full findings):"]
        for spec, run, ticket in prior:
            note = f" — {run.notes}" if run.notes else ""
            lines.append(f"- {spec.slug}: {run.status.value} (ticket {ticket}){note}")
        lines.append("Build on that work; do not redo it.")
    return "\n".join(lines)


def _unattended_eligible(spec: IncrementSpec) -> tuple[bool, str]:
    blocked = {"device", "human"} & set(spec.requires)
    if blocked:
        return False, f"requires {', '.join(sorted(blocked))}"
    if spec.target and spec.rubric_path and registry.get(spec.deliverable_type) is None:
        return False, f"no verifier registered for deliverable type '{spec.deliverable_type}'"
    return True, ""


def _ticket_body(goal: GoalSpec, spec: IncrementSpec) -> str:
    codebase = f"codebase: {goal.repo}\n\n" if goal.repo else ""
    return f"{codebase}## Increment {spec.order:02d} of goal {goal.id}\n\n{spec.brief}"


def run_shift(
    goal: GoalSpec,
    config: dict[str, Any],
    *,
    max_increments: int | None = None,
    on_event: Callable[[str], None] = print,
) -> tuple[list[tuple[IncrementSpec, Run, str]], str]:
    """Execute the goal's increments sequentially. Returns (results, report_text)."""
    started_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    config = dict(config)
    config["shift_id"] = f"shift-{goal.id}"

    if goal.repo and goal.branch:
        state = ensure_goal_branch(goal.repo, goal.branch)
        on_event(f"goal branch {goal.branch}: {state}")

    client = TasksMcpClient(
        str(config.get("mcp_url", "http://127.0.0.1:8876/mcp")),
        float(config.get("mcp_timeout_seconds", 10)),
    )

    results: list[tuple[IncrementSpec, Run, str]] = []
    stop_reason = ""
    todo = goal.increments[: max_increments or len(goal.increments)]

    for spec in todo:
        eligible, why = _unattended_eligible(spec)
        if not eligible:
            run = Run(
                id=f"run-{goal.id}-{spec.slug}",
                shift_id=config["shift_id"],
                increment_id=spec.slug,
                status=RunStatus.BLOCKED,
                notes=f"human-gated: {why}",
            )
            results.append((spec, run, "(no ticket)"))
            on_event(f"[{spec.slug}] BLOCKED (human-gated: {why}) — skipping, not attempted blind")
            continue

        task = client.create_task(
            goal.task_prefix,
            f"{goal.id} {spec.order:02d} {spec.slug}",
            _ticket_body(goal, spec),
        )
        ticket_slug = Path(str(task.get("path", ""))).stem or str(task.get("task_id", spec.slug))
        ticket_id = str(task.get("task_id", ticket_slug))
        on_event(f"[{spec.slug}] ticket {ticket_id} created")

        increment = spec.to_increment(ticket_slug, summary=spec.brief)
        context = _goal_context(goal, results)
        tool = spec.tool or goal.tool or str(config.get("default_tool", "claude"))

        run = dispatch_run(increment, tool=tool, config=config, goal_context=context)
        if run.status == RunStatus.CRASHED:
            results.append((spec, run, ticket_id))
            stop_reason = f"dispatch failed on {spec.slug}"
            break
        on_event(f"[{spec.slug}] dispatched ({run.notes})")

        run = monitor_run(run, increment, config, on_poll=lambda m, s=spec.slug: on_event(f"[{s}] {m}"))
        save_run_record(run, config)
        results.append((spec, run, ticket_id))
        on_event(f"[{spec.slug}] {run.status.value}")

        if run.status in {RunStatus.CRASHED, RunStatus.STALLED}:
            stop_reason = f"{spec.slug} ended {run.status.value}; later increments depend on it"
            break
        if run.status == RunStatus.NEEDS_REVIEW and bool(config.get("stop_on_needs_review", True)):
            stop_reason = f"{spec.slug} needs review; not building further on an unverified base"
            break

    report = render_goal_report(goal, results, started_iso, stop_reason)
    return results, report


def _duration(run: Run) -> str:
    try:
        start = dt.datetime.fromisoformat(run.started_at)
        end = dt.datetime.fromisoformat(run.ended_at)
        return f"{(end - start).total_seconds() / 60:.0f}m"
    except (TypeError, ValueError):
        return "-"


def render_goal_report(
    goal: GoalSpec,
    results: list[tuple[IncrementSpec, Run, str]],
    started_iso: str,
    stop_reason: str,
) -> str:
    lines = [
        f"GOAL SHIFT REPORT — {goal.title} ({goal.id})",
        f"started {started_iso}",
    ]
    if goal.repo and goal.branch:
        lines.append(f"workspace: {goal.repo} @ {goal.branch}")
        commits = branch_commits_since(goal.repo, goal.branch, started_iso)
        if commits:
            lines.append(f"product delta this shift ({len(commits)} commits on {goal.branch}):")
            lines += [f"  {c}" for c in commits]
        else:
            lines.append("product delta this shift: no new commits on the goal branch")
    lines.append("")

    done_count = sum(1 for _, run, _ in results if run.status in TERMINAL_OK)
    lines.append(f"increments: {done_count}/{len(goal.increments)} landed")
    for spec, run, ticket in results:
        note = f" — {run.notes}" if run.notes else ""
        lines.append(f"  {spec.order:02d} {spec.slug}: {run.status.value} [{_duration(run)}] (ticket {ticket}){note}")

    remaining = goal.increments[len(results):]
    for spec in remaining:
        lines.append(f"  {spec.order:02d} {spec.slug}: not started")

    if stop_reason:
        lines += ["", f"shift stopped early: {stop_reason}"]
    blocked = [s for s, run, _ in results if run.status == RunStatus.BLOCKED]
    if blocked:
        lines += ["", "awaiting you (human-gated): " + ", ".join(s.slug for s in blocked)]
    return "\n".join(lines)
