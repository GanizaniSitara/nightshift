"""Goal layer tests: goal/increment loading and coherent sequential execution."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.orchestration import goal_shift, goals  # noqa: E402
from nightshift.orchestration.records import Run, RunStatus  # noqa: E402

GOAL_MD = """---
goal: demo-goal
title: Make the demo great
repo: C:/x/repo
branch: goal/demo
task_prefix: TECH
status: ready
---

The demo app should become fast and pretty.
"""

INC_1 = """---
deliverable: cli
---

Step one: do the first thing.
"""

INC_2 = """---
deliverable: web
target: http://127.0.0.1:9/
rubric: rubrics/demo.md
---

Step two: polish it.
"""

INC_GATED = """---
deliverable: cli
requires: device
---

Needs a physical camera.
"""


def _write_goal(root: Path, incs: dict[str, str]) -> Path:
    gdir = root / "demo-goal"
    (gdir / "increments").mkdir(parents=True)
    (gdir / "goal.md").write_text(GOAL_MD, encoding="utf-8")
    for name, text in incs.items():
        (gdir / "increments" / name).write_text(text, encoding="utf-8")
    return gdir


class GoalLoaderTests(unittest.TestCase):
    def test_load_goal_parses_intent_meta_and_ordered_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            gdir = _write_goal(Path(tmp), {"01-first.md": INC_1, "02-second.md": INC_2})
            goal = goals.load_goal(tmp, "demo-goal")
            self.assertEqual(goal.id, "demo-goal")
            self.assertEqual(goal.branch, "goal/demo")
            self.assertIn("fast and pretty", goal.intent)
            self.assertEqual([s.slug for s in goal.increments], ["01-first", "02-second"])
            self.assertEqual(goal.increments[0].deliverable_type, "cli")
            second = goal.increments[1]
            self.assertEqual(second.deliverable_type, "web")
            self.assertEqual(second.target, "http://127.0.0.1:9/")
            # relative rubric resolves against the goal dir
            self.assertEqual(Path(second.rubric_path), (gdir / "rubrics" / "demo.md").resolve())

    def test_requires_parsed_as_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_goal(Path(tmp), {"01-gated.md": INC_GATED})
            goal = goals.load_goal(tmp, "demo-goal")
            self.assertEqual(goal.increments[0].requires, ["device"])


class _SeqHarness:
    """Mocks ticket creation, dispatch, and monitoring for run_shift."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.dispatched = []  # (slug, goal_context)
        self.created = []
        self._n = 0

    def create_task(self, prefix, title, body, priority="P4"):
        self._n += 1
        slug = f"TECH-90{self._n}-{title.split()[-1]}"
        self.created.append((prefix, title))
        return {"task_id": f"TECH-90{self._n}", "path": f"C:/t/backlog/{slug}.md"}

    def dispatch(self, increment, *, tool, config, goal_context=None):
        self.dispatched.append((increment.id, goal_context))
        return Run(id=f"r{self._n}", shift_id="s", increment_id=increment.id,
                   tool=tool, status=RunStatus.RUNNING, started_at="2026-01-01T00:00:00+00:00")

    def monitor(self, run, increment, config, on_poll=None):
        run.status = self.statuses.pop(0)
        run.ended_at = "2026-01-01T00:05:00+00:00"
        return run


def _run(goal, statuses, config=None):
    h = _SeqHarness(statuses)
    fake_client = mock.Mock()
    fake_client.create_task.side_effect = h.create_task
    with mock.patch.object(goal_shift, "TasksMcpClient", return_value=fake_client), \
         mock.patch.object(goal_shift, "dispatch_run", side_effect=h.dispatch), \
         mock.patch.object(goal_shift, "monitor_run", side_effect=h.monitor), \
         mock.patch.object(goal_shift, "save_run_record"), \
         mock.patch.object(goal_shift, "ensure_goal_branch", return_value="exists"), \
         mock.patch.object(goal_shift, "branch_commits_since", return_value=["abc123 step one"]):
        results, report = goal_shift.run_shift(goal, config or {}, on_event=lambda m: None)
    return h, results, report


class RunShiftTests(unittest.TestCase):
    def _goal(self, incs):
        with tempfile.TemporaryDirectory() as tmp:
            _write_goal(Path(tmp), incs)
            return goals.load_goal(tmp, "demo-goal")

    def test_sequence_threads_prior_results_into_next_context(self):
        goal = self._goal({"01-first.md": INC_1, "02-second.md": INC_1})
        h, results, report = _run(goal, [RunStatus.DONE, RunStatus.DONE])
        self.assertEqual(len(results), 2)
        first_ctx = h.dispatched[0][1]
        second_ctx = h.dispatched[1][1]
        self.assertIn("fast and pretty", first_ctx)
        self.assertIn("goal/demo", first_ctx)
        self.assertNotIn("Previous increments", first_ctx)
        self.assertIn("Previous increments", second_ctx)
        self.assertIn("01-first: done", second_ctx)
        self.assertIn("2/2 landed", report)
        self.assertIn("abc123 step one", report)

    def test_human_gated_increment_skipped_without_ticket(self):
        goal = self._goal({"01-gated.md": INC_GATED, "02-first.md": INC_1})
        h, results, report = _run(goal, [RunStatus.DONE])
        self.assertEqual(results[0][1].status, RunStatus.BLOCKED)
        self.assertEqual(len(h.created), 1)  # only the non-gated increment got a ticket
        self.assertIn("awaiting you (human-gated): 01-gated", report)

    def test_crash_stops_the_shift(self):
        goal = self._goal({"01-first.md": INC_1, "02-second.md": INC_1, "03-third.md": INC_1})
        h, results, report = _run(goal, [RunStatus.DONE, RunStatus.CRASHED])
        self.assertEqual(len(results), 2)
        self.assertIn("shift stopped early", report)
        self.assertIn("03-third: not started", report)

    def test_needs_review_stops_by_default(self):
        goal = self._goal({"01-first.md": INC_1, "02-second.md": INC_1})
        h, results, report = _run(goal, [RunStatus.NEEDS_REVIEW, RunStatus.DONE])
        self.assertEqual(len(results), 1)
        self.assertIn("needs review", report)


if __name__ == "__main__":
    unittest.main()
