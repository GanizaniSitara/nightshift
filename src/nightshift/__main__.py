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


def cmd_shift(args: argparse.Namespace) -> int:
    from .orchestration.goal_shift import run_shift
    from .orchestration.goals import assert_ready, load_goal

    config = load_config(Path(args.config) if args.config else None)
    plugins.load_plugins(config=config)
    goals_dir = args.goals_dir or config.get("goals_dir")
    if not goals_dir:
        print("no goals dir: pass --goals-dir or set goals_dir in config")
        return 1

    goal = load_goal(goals_dir, args.goal)
    if not goal.increments:
        print(f"goal {goal.id} has no increments")
        return 1
    if not args.dry_run:
        try:
            assert_ready(goal)
        except PermissionError as exc:
            print(f"NOT APPROVED: {exc}")
            return 1
    if args.dry_run:
        print(f"goal {goal.id}: {goal.title} — {len(goal.increments)} increment(s)")
        for spec in goal.increments:
            gate = " [human-gated]" if {"device", "human"} & set(spec.requires) else ""
            print(f"  {spec.order:02d} {spec.slug} ({spec.deliverable_type}){gate}")
        return 0

    results, report = run_shift(goal, config, max_increments=args.max_increments)

    reports_dir = Path(str(config.get("reports_dir", "state/reports")))
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = results[-1][1].ended_at if results and results[-1][1].ended_at else "report"
    report_path = reports_dir / f"goal-{goal.id}-{stamp.replace(':', '').replace('+', 'Z')[:17]}.txt"
    report_path.write_text(report, encoding="utf-8")

    print()
    print(report)
    print()
    print(f"report: {report_path}")
    from .orchestration.goal_shift import TERMINAL_OK
    landed_all = all(run.status in TERMINAL_OK for _, run, _ in results) and len(results) == len(goal.increments)
    return 0 if landed_all else 2


def cmd_plan(args: argparse.Namespace) -> int:
    from .orchestration.goals import load_goal
    from .orchestration.planner import run_planner, write_goal_draft

    config = load_config(Path(args.config) if args.config else None)
    goals_dir = args.goals_dir or config.get("goals_dir")
    if not goals_dir:
        print("no goals dir: pass --goals-dir or set goals_dir in config")
        return 1
    repo = str(Path(args.repo).resolve())

    print(f"planning against {repo} (headless {config.get('plan_tool', 'claude')} session)...")
    plan = run_planner(args.ask, repo, config=config, max_increments=args.max_increments)
    goal_dir = write_goal_draft(
        plan, goals_dir, repo=repo,
        task_prefix=args.task_prefix, tool=str(config.get("default_tool", "claude")),
    )

    goal = load_goal(goals_dir, goal_dir.name)
    print()
    print(f"DRAFT goal written: {goal_dir}")
    print(f"  {goal.title}")
    print(f"  branch {goal.branch} · {len(goal.increments)} increment(s):")
    for spec in goal.increments:
        gate = " [human-gated]" if {"device", "human"} & set(spec.requires) else ""
        verify = " +rubric" if spec.rubric_path else ""
        print(f"    {spec.order:02d} {spec.slug} ({spec.deliverable_type}{verify}){gate}")
    print()
    print("Review the draft, then set 'status: ready' in goal.md to approve it for a shift.")
    return 0


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

    plan_p = sub.add_parser("plan", help="planner pass: decompose an ask into a DRAFT goal for approval")
    plan_p.add_argument("ask", help="the high-level ask, in plain language")
    plan_p.add_argument("--repo", required=True, help="project repo/working dir the planner recons")
    plan_p.add_argument("--goals-dir", default=None)
    plan_p.add_argument("--config", default=None)
    plan_p.add_argument("--task-prefix", default="TECH", help="ticket prefix for the goal's transport tickets")
    plan_p.add_argument("--max-increments", type=int, default=5)
    plan_p.set_defaults(func=cmd_plan)

    shift_p = sub.add_parser("shift", help="work a goal: ordered increments, shared branch, goal report")
    shift_p.add_argument("goal", help="goal id (folder name under the goals dir)")
    shift_p.add_argument("--goals-dir", default=None)
    shift_p.add_argument("--config", default=None)
    shift_p.add_argument("--max-increments", type=int, default=None)
    shift_p.add_argument("--dry-run", action="store_true", help="list the plan; run nothing")
    shift_p.set_defaults(func=cmd_shift)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
