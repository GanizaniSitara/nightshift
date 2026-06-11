"""Planner tests: prompt assembly, draft materialization round-trip, approval gate."""

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.orchestration import goals, planner  # noqa: E402

PLAN = {
    "goal": {
        "id": "Demo Goal!",
        "title": "Make demo great",
        "branch": "goal/demo-goal",
        "intent": "The demo should become great.",
    },
    "increments": [
        {"slug": "First Step", "deliverable": "cli", "brief": "Do the first thing."},
        {
            "slug": "polish-ui",
            "deliverable": "web",
            "target": "http://127.0.0.1:9/",
            "rubric_lines": ["header visible", "no overlap"],
            "brief": "Polish the UI.",
        },
        {"slug": "camera-check", "deliverable": "cli", "requires": ["device"], "brief": "Needs hardware."},
    ],
}


class PromptTests(unittest.TestCase):
    def test_prompt_contains_ask_repo_recipes_and_json_contract(self):
        p = planner.build_planner_prompt("make it great", "C:/x/repo", max_increments=4)
        self.assertIn("make it great", p)
        self.assertIn("C:/x/repo", p)
        self.assertIn("audit/research", p)
        self.assertIn('"goal"', p)
        self.assertIn("at most 4", p.lower())


class WriteDraftTests(unittest.TestCase):
    def test_draft_round_trips_through_load_goal(self):
        with tempfile.TemporaryDirectory() as tmp:
            goal_dir = planner.write_goal_draft(PLAN, tmp, repo="C:/x/repo", task_prefix="TECH")
            self.assertEqual(goal_dir.name, "demo-goal")

            goal = goals.load_goal(tmp, "demo-goal")
            self.assertEqual(goal.status, "draft")
            self.assertEqual(goal.repo, "C:/x/repo")
            self.assertEqual(goal.branch, "goal/demo-goal")
            self.assertEqual(
                [s.slug for s in goal.increments],
                ["01-first-step", "02-polish-ui", "03-camera-check"],
            )
            web = goal.increments[1]
            self.assertEqual(web.deliverable_type, "web")
            self.assertEqual(web.target, "http://127.0.0.1:9/")
            self.assertTrue(Path(web.rubric_path).is_file())
            self.assertIn("header visible", Path(web.rubric_path).read_text(encoding="utf-8"))
            self.assertEqual(goal.increments[2].requires, ["device"])

    def test_existing_goal_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner.write_goal_draft(PLAN, tmp, repo="C:/x")
            with self.assertRaises(FileExistsError):
                planner.write_goal_draft(PLAN, tmp, repo="C:/x")


class ApprovalGateTests(unittest.TestCase):
    def test_draft_goal_is_rejected_until_marked_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner.write_goal_draft(PLAN, tmp, repo="C:/x")
            goal = goals.load_goal(tmp, "demo-goal")
            with self.assertRaises(PermissionError):
                goals.assert_ready(goal)

            goal_md = Path(tmp) / "demo-goal" / "goal.md"
            goal_md.write_text(
                goal_md.read_text(encoding="utf-8").replace("status: draft", "status: ready"),
                encoding="utf-8",
            )
            goals.assert_ready(goals.load_goal(tmp, "demo-goal"))  # no raise


if __name__ == "__main__":
    unittest.main()
