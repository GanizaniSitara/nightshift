"""Nudger tests: per-tool classification, Claude inject, Codex detect-and-alert, monitor wiring."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.orchestration import shift  # noqa: E402
from nightshift.orchestration.records import Run, RunStatus  # noqa: E402
from nightshift.verifiers.base import Increment  # noqa: E402
from nightshift.watchdog import nudger  # noqa: E402

# Representative console tails (verbatim wording from the real CLIs).
CLAUDE_RATE = "You've hit your limit\nWhat do you want to do?\n  resets 14:00\n"
CLAUDE_CTX = "Your conversation is full. Run /compact to compact the conversation.\n"
CODEX_USAGE = "You've hit your usage limit. Upgrade to Plus to continue using Codex\n"
CODEX_CREDITS = "Your workspace is out of credits. Add credits to continue.\n"
CODEX_CTX = "Codex ran out of room in the model's context window. Start a new thread before retrying.\n"
WORKING = "Editing greeting.py ... running tests ... 2 passed\n"


class ClassifyTests(unittest.TestCase):
    def test_claude_rate_limit_needs_recovery_ui(self):
        rules = nudger.get_rules(None)["claude"]
        self.assertEqual(nudger.classify(CLAUDE_RATE, rules)["name"], "rate-limit")
        # banner without the recovery-UI line must NOT match (avoids false positives)
        self.assertIsNone(nudger.classify("You've hit your limit\n(some unrelated text)\n", rules))

    def test_claude_context(self):
        rules = nudger.get_rules(None)["claude"]
        self.assertEqual(nudger.classify(CLAUDE_CTX, rules)["name"], "context-limit")

    def test_codex_states(self):
        rules = nudger.get_rules(None)["codex"]
        self.assertEqual(nudger.classify(CODEX_USAGE, rules)["name"], "usage-limit")
        self.assertEqual(nudger.classify(CODEX_CREDITS, rules)["name"], "out-of-credits")
        self.assertEqual(nudger.classify(CODEX_CTX, rules)["name"], "context-full")

    def test_working_session_matches_nothing(self):
        self.assertIsNone(nudger.classify(WORKING, nudger.get_rules(None)["claude"]))
        self.assertIsNone(nudger.classify(WORKING, nudger.get_rules(None)["codex"]))


class ProbeTests(unittest.TestCase):
    def _ps(self, read_tail, send_ok=True, verify_tail=WORKING):
        """Fake _run_ps: first read returns read_tail, sends succeed, 2nd read = verify_tail."""
        reads = [{"ok": True, "tail": read_tail}, {"ok": True, "tail": verify_tail}]
        def fake(pid, action, **kw):
            if action == "read":
                return reads.pop(0) if reads else {"ok": True, "tail": verify_tail}
            return {"ok": send_ok, "tail": ""}
        return fake

    def test_claude_rate_limit_injects_continue_and_verifies(self):
        with mock.patch.object(nudger, "_run_ps", side_effect=self._ps(CLAUDE_RATE)):
            r = nudger.probe_and_nudge(111, tool="claude", apply=True)
        self.assertEqual(r.state, "rate-limit")
        self.assertEqual(r.action, "continue-sent")
        self.assertTrue(r.nudged)
        self.assertTrue(r.verified)
        self.assertFalse(r.needs_human)

    def test_claude_context_sends_compact(self):
        with mock.patch.object(nudger, "_run_ps", side_effect=self._ps(CLAUDE_CTX)):
            r = nudger.probe_and_nudge(111, tool="claude", apply=True)
        self.assertEqual(r.action, "compact-sent")
        self.assertTrue(r.nudged)

    def test_codex_usage_limit_alerts_without_injecting(self):
        sent = {"n": 0}
        def fake(pid, action, **kw):
            if action == "send":
                sent["n"] += 1
            return {"ok": True, "tail": CODEX_USAGE}
        with mock.patch.object(nudger, "_run_ps", side_effect=fake):
            r = nudger.probe_and_nudge(111, tool="codex", apply=True)
        self.assertEqual(r.state, "usage-limit")
        self.assertEqual(r.action, "alert")
        self.assertTrue(r.needs_human)
        self.assertFalse(r.nudged)
        self.assertEqual(sent["n"], 0)  # never injects into codex

    def test_working_session_untouched(self):
        with mock.patch.object(nudger, "_run_ps", side_effect=self._ps(WORKING)):
            r = nudger.probe_and_nudge(111, tool="claude", apply=True)
        self.assertEqual(r.state, "working-or-unknown")
        self.assertFalse(r.nudged)
        self.assertFalse(r.needs_human)

    def test_attach_failure(self):
        with mock.patch.object(nudger, "_run_ps", return_value=None):
            r = nudger.probe_and_nudge(111, tool="claude", apply=True)
        self.assertEqual(r.state, "attach-failed")

    def test_unknown_tool_skipped(self):
        with mock.patch.object(nudger, "_run_ps") as rp:
            r = nudger.probe_and_nudge(111, tool="opencode", apply=True)
        rp.assert_not_called()
        self.assertEqual(r.state, "skipped-tool")

    def test_config_can_add_a_tool_rule(self):
        cfg = {"nudge_rules": {"copilot": [{"name": "rl", "any": [r"rate limit"], "action": "alert"}]}}
        with mock.patch.object(nudger, "_run_ps", return_value={"ok": True, "tail": "hit a rate limit now"}):
            r = nudger.probe_and_nudge(111, tool="copilot", apply=True, config=cfg)
        self.assertEqual(r.state, "rl")
        self.assertTrue(r.needs_human)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "done").mkdir()
        (root / "in-progress").mkdir()
        self.config = {"tasks_root": self.tmp.name, "poll_seconds": 0, "max_run_seconds": 60,
                       "nudge_enabled": True, "nudge_probe_cooldown_seconds": 0}

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, slug, tool="claude"):
        return Run(id="r1", shift_id="s1", increment_id=slug, tool=tool, status=RunStatus.RUNNING)

    def test_codex_needs_human_stops_run_early(self):
        slug = "TECH-960-codexstuck"
        alert = nudger.NudgeResult("usage-limit", "alert", False, False, True)
        with mock.patch.object(shift, "task_is_done", return_value=False), \
             mock.patch.object(shift, "session_alive", return_value=True), \
             mock.patch.object(shift, "session_pid", return_value=4321), \
             mock.patch.object(shift, "_note") as note, \
             mock.patch("nightshift.watchdog.nudger.probe_and_nudge", return_value=alert):
            run = shift.monitor_run(self._run(slug, "codex"),
                                    Increment(id=slug, summary="x", deliverable_type="cli"), self.config)
        self.assertEqual(run.status, RunStatus.STALLED)
        self.assertIn("won't self-resume", run.notes)
        note.assert_called()

    def test_claude_nudge_then_complete(self):
        slug = "TECH-961-nudge"
        done_dir = Path(self.tmp.name) / "done"
        calls = {"n": 0}
        def fake_done(s, c):
            calls["n"] += 1
            if calls["n"] >= 2:
                (done_dir / f"{slug}.md").write_text("x", encoding="utf-8")
                return True
            return False
        nudged = nudger.NudgeResult("rate-limit", "continue-sent", True, True, False)
        with mock.patch.object(shift, "task_is_done", side_effect=fake_done), \
             mock.patch.object(shift, "session_alive", return_value=True), \
             mock.patch.object(shift, "session_pid", return_value=4321), \
             mock.patch.object(shift, "_note"), \
             mock.patch("nightshift.watchdog.nudger.probe_and_nudge", return_value=nudged):
            run = shift.monitor_run(self._run(slug, "claude"),
                                    Increment(id=slug, summary="x", deliverable_type="cli"), self.config)
        self.assertEqual(run.status, RunStatus.DONE)
        self.assertIn("auto-nudged", run.notes)


if __name__ == "__main__":
    unittest.main()
