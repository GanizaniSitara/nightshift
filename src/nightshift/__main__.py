"""Nightshift CLI.

  python -m nightshift run <task-slug> [--tool claude|codex|copilot]
                                       [--type web] [--target URL] [--rubric FILE]
                                       [--config FILE] [--no-launch]

``run`` dispatches one managed run for an existing task: appends the worker
contract + run brief to the ticket (tasks MCP), launches via the configured
launcher, stamps the launch-info as managed for the watchdog, then monitors to
a terminal state and verifies the deliverable when --target/--rubric are given.
``--no-launch`` skips dispatch and just monitors an already-running session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dispatch.dispatcher import dispatch_run, save_run_record
from .orchestration import report as report_mod
from .orchestration.records import Goal, Run, RunStatus
from .orchestration.shift import monitor_run, start
from .verifiers.base import Increment
from .watchdog.watchdog import load_config
from . import plugins


def _build_increment(args: argparse.Namespace) -> Increment:
    return Increment(
        id=args.slug,
        summary=args.summary or f"Complete the task described in ticket {args.slug}.",
        deliverable_type=args.type,
        acceptance_criteria=args.criteria or [],
        target=args.target,
        rubric_path=args.rubric,
    )


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    plugins.load_plugins(config=config)
    increment = _build_increment(args)
    goal = Goal(id=args.slug, intent=increment.summary)
    shift = start(goal)
    config["shift_id"] = shift.id

    if args.no_launch:
        run = Run(id=f"run-{args.slug}-monitor", shift_id=shift.id, increment_id=args.slug,
                  tool=args.tool or str(config.get("default_tool", "claude")), status=RunStatus.RUNNING)
    else:
        run = dispatch_run(increment, tool=args.tool, config=config)
        if run.status == RunStatus.CRASHED:
            print(f"DISPATCH FAILED: {run.notes}")
            return 1
        print(f"dispatched: {run.id} ({run.notes})")

    run = monitor_run(run, increment, config, on_poll=lambda msg: print(f"  {msg}", flush=True))
    record_path = save_run_record(run, config)

    text = report_mod.render(shift, [run])
    reports_dir = Path(str(config.get("reports_dir", "state/reports")))
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{run.id}.txt"
    report_path.write_text(text, encoding="utf-8")

    print()
    print(text)
    print()
    print(f"run record: {record_path}")
    print(f"report:     {report_path}")
    return 0 if run.status in {RunStatus.DONE, RunStatus.VERIFIED} else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nightshift")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="dispatch + monitor one managed run")
    run_p.add_argument("slug", help="task slug (existing ticket)")
    run_p.add_argument("--tool", choices=["claude", "codex", "copilot"], default=None)
    run_p.add_argument("--type", default="web", help="deliverable type for verification")
    run_p.add_argument("--target", default=None, help="URL/path the verifier should check")
    run_p.add_argument("--rubric", default=None, help="rubric file for the vision pass")
    run_p.add_argument("--summary", default=None)
    run_p.add_argument("--criteria", action="append", default=[])
    run_p.add_argument("--config", default=None)
    run_p.add_argument("--no-launch", action="store_true", help="monitor only; don't dispatch")
    run_p.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
