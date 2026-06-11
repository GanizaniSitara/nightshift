"""Launch one managed run via the platform launcher and mark it managed.

Flow:
1. Build the run prompt (worker contract + step brief + done-signal instructions)
   and append it to the ticket through the tasks MCP — the launcher injects the
   entire task file as the agent's first prompt, so the contract rides along
   with zero launcher changes.
2. Invoke the launcher (single atomic ``powershell -File`` call); it returns
   immediately after spawning the session.
3. Wait for the launcher's ``<slug>.launch-info.json`` to appear, then post-stamp
   the managed markers the watchdog gates on (JSON round-trip, preserving the
   launcher's fields — the watchdog reads, never modifies, launcher data; the
   dispatcher only adds keys).
4. Persist a Run record under ``state/runs/``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..orchestration.records import Run, RunStatus
from ..verifiers.base import Increment
from ..watchdog.watchdog import TasksMcpClient, task_id_from_slug

#: Markers the watchdog recognises as "this run is managed by Nightshift".
MANAGED_MARKERS: dict[str, Any] = {"RunnerManaged": True, "ManagedBy": "nightshift"}

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "worker" / "contract.md"


def build_run_prompt(
    increment: Increment,
    *,
    contract_text: str | None = None,
    goal_context: str | None = None,
) -> str:
    """Assemble worker contract + step brief + done-signal instructions.

    ``goal_context`` is the coherence thread for goal shifts: the goal intent,
    the shared workspace/branch, and what previous increments accomplished —
    so each worker builds ON the last one instead of starting cold.
    """
    contract = contract_text if contract_text is not None else CONTRACT_PATH.read_text(encoding="utf-8")
    criteria = "\n".join(f"- {c}" for c in increment.acceptance_criteria) or "- (none stated)"
    goal_block = f"### Goal context\n\n{goal_context}\n\n" if goal_context else ""
    return (
        "## Managed run brief (Nightshift)\n\n"
        "This session is a MANAGED RUN. Follow the worker contract below exactly.\n\n"
        f"{goal_block}"
        f"### Your increment\n\n{increment.summary}\n\n"
        f"### Acceptance criteria\n\n{criteria}\n\n"
        "### Heartbeat and done signal\n\n"
        "- Append a short progress note to THIS ticket via the tasks MCP "
        "(`append_task_note`) at each checkpoint — that note is your heartbeat; "
        "a silent session is treated as stalled.\n"
        "- When finished, append a final completion note (what changed, what was "
        "verified, remaining gaps) and move this ticket to done via the tasks MCP "
        "(`move_task`). Moving the ticket IS the done signal.\n\n"
        f"### Worker contract\n\n{contract}"
    )


def launcher_command(config: dict[str, Any], slug: str, tool: str) -> list[str]:
    launcher = str(config.get("launcher_path", ""))
    powershell = shutil.which("powershell") or "powershell"
    return [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        launcher,
        "task",
        slug,
        "-Tool",
        tool,
    ]


def stamp_managed(launch_info_path: Path) -> dict[str, Any]:
    """Add the managed markers to launch-info.json, preserving existing fields."""
    raw = json.loads(launch_info_path.read_text(encoding="utf-8-sig"))
    raw.update(MANAGED_MARKERS)
    launch_info_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return raw


def wait_for_launch_info(path: Path, *, timeout_seconds: float = 30, poll: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(poll)
    return path.exists()


def save_run_record(run: Run, config: dict[str, Any], extra: dict[str, Any] | None = None) -> Path:
    runs_dir = Path(config.get("runs_dir", "state/runs"))
    runs_dir.mkdir(parents=True, exist_ok=True)
    record = dataclasses.asdict(run)
    record["status"] = run.status.value
    if extra:
        record.update(extra)
    path = runs_dir / f"{run.id}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


def dispatch_run(
    increment: Increment,
    *,
    tool: str | None = None,
    config: dict[str, Any],
    goal_context: str | None = None,
) -> Run:
    """Launch a managed run for an increment. ``increment.id`` is the task slug."""
    slug = increment.id
    tool = tool or str(config.get("default_tool", "claude"))
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    run = Run(
        id=f"run-{slug}-{now.replace(':', '').replace('+', 'Z')[:17]}",
        shift_id=str(config.get("shift_id", "adhoc")),
        increment_id=slug,
        tool=tool,
        status=RunStatus.PENDING,
        started_at=now,
    )

    # 1. Move the ticket to in-progress THROUGH the tasks MCP before launching.
    # The launcher would otherwise file-move it itself WITHOUT updating frontmatter,
    # and the MCP (frontmatter = source of truth) then re-homes the file to backlog
    # mid-run, orphaning the launch-info. Moving via MCP first keeps state coherent
    # and the launcher takes the already-in-progress path (no file move of its own).
    task_id = task_id_from_slug(slug) or slug
    client = TasksMcpClient(
        str(config.get("mcp_url", "http://127.0.0.1:8876/mcp")),
        float(config.get("mcp_timeout_seconds", 10)),
    )
    tasks_root = Path(str(config.get("tasks_root", "")))
    already_in_progress = any((tasks_root / "in-progress").glob(f"{slug}*.md"))
    if not already_in_progress:
        client.move_task(task_id, "in-progress")

    # 2. Deliver contract + brief through the ticket (launcher injects task file verbatim).
    prompt = build_run_prompt(increment, goal_context=goal_context)
    client.append_task_note(task_id, prompt, heading="Managed run")

    # 3. Fire the launcher.
    cmd = launcher_command(config, slug, tool)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    if proc.returncode != 0:
        run.status = RunStatus.CRASHED
        run.notes = f"launcher failed rc={proc.returncode}: {proc.stderr.strip()[:300]}"
        save_run_record(run, config)
        return run

    # 4. Stamp managed markers so the deployed watchdog tracks/alerts this run.
    launch_info = tasks_root / "in-progress" / f"{slug}.launch-info.json"
    if wait_for_launch_info(launch_info, timeout_seconds=float(config.get("launch_info_timeout_seconds", 30))):
        stamp_managed(launch_info)
        run.status = RunStatus.RUNNING
        run.notes = "launched and stamped managed"
    else:
        run.status = RunStatus.RUNNING
        run.notes = "launched but launch-info not found within timeout (not stamped managed)"

    save_run_record(run, config, extra={"launch_info": str(launch_info)})
    return run
