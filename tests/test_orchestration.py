"""Orchestration scaffold tests: queue parsing, routing decision, report rendering."""

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.orchestration import queue, report, shift  # noqa: E402
from nightshift.orchestration.records import Goal, Run, RunStatus  # noqa: E402
from nightshift.verifiers import registry  # noqa: E402
from nightshift.verifiers.base import Increment, Verdict, Verifier, VerificationResult  # noqa: E402


class _WebStub(Verifier):
    deliverable_type = "web"

    def verify(self, increment, *, config):
        return VerificationResult(deliverable_type="web", verdict=Verdict.PASS)


PLAN_MD = """---
id: PLAN-1
goal_id: PROJ-17
summary: polish dashboard
increments: inc-a, inc-b
ready: true
---
body text
"""


class QueueTests(unittest.TestCase):
    def test_ready_plans_parsed_from_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "p1.md").write_text(PLAN_MD, encoding="utf-8")
            plans = queue.ready_plans(tmp)
            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].id, "PLAN-1")
            self.assertEqual(plans[0].increments, ["inc-a", "inc-b"])
            self.assertTrue(plans[0].ready)

    def test_missing_dir_is_empty(self):
        self.assertEqual(queue.load_plans("C:/no/such/dir"), [])


class ShiftRoutingTests(unittest.TestCase):
    def setUp(self):
        registry.register(_WebStub())
        self.shift = shift.start(Goal(id="PROJ-17", intent="x"), started_at="2026-01-01T00:00:00Z")

    def test_unattended_when_verifiable_and_no_requirements(self):
        inc = Increment(id="i1", summary="x", deliverable_type="web")
        run = shift.route_or_run(self.shift, inc, run_id="r1")
        self.assertEqual(run.status, RunStatus.PENDING)

    def test_device_requirement_is_human_gated(self):
        inc = Increment(id="i2", summary="x", deliverable_type="web", requires=["device"])
        run = shift.route_or_run(self.shift, inc, run_id="r2")
        self.assertEqual(run.status, RunStatus.BLOCKED)
        self.assertIn("device", run.notes)

    def test_unknown_type_is_human_gated(self):
        inc = Increment(id="i3", summary="x", deliverable_type="mystery")
        run = shift.route_or_run(self.shift, inc, run_id="r3")
        self.assertEqual(run.status, RunStatus.BLOCKED)

    def test_pick_next_skips_done(self):
        a = Increment(id="a", summary="", deliverable_type="web")
        b = Increment(id="b", summary="", deliverable_type="web")
        self.assertEqual(shift.pick_next_step([a, b], {"a"}).id, "b")
        self.assertIsNone(shift.pick_next_step([a], {"a"}))


class ReportTests(unittest.TestCase):
    def test_report_groups_and_flags_waste(self):
        sh = shift.start(Goal(id="G", intent="x"), started_at="2026-01-01T00:00:00Z")
        runs = [
            Run(id="r1", shift_id=sh.id, increment_id="a", status=RunStatus.VERIFIED),
            Run(id="r2", shift_id=sh.id, increment_id="b", status=RunStatus.BLOCKED, notes="device"),
            Run(id="r3", shift_id=sh.id, increment_id="c", status=RunStatus.CRASHED),
        ]
        text = report.render(sh, runs)
        self.assertIn("Verified: 1", text)
        self.assertIn("Blocked / human-gated: 1", text)
        self.assertIn("Capacity wasted: 1", text)

    def test_no_runs_reports_unused(self):
        sh = shift.start(Goal(id="G", intent="x"), started_at="2026-01-01T00:00:00Z")
        self.assertIn("Capacity unused", report.render(sh, []))


if __name__ == "__main__":
    unittest.main()
