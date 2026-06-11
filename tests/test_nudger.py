"""Nudger tests: output parsing, tool gating, and monitor integration (mocked)."""

import subprocess
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


def _proc(stdout, rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class NudgerParseTests(unittest.TestCase):
    def _run(self, file_content):
        # The helper writes its JSON to the -OutFile path (stdout is hijacked by
        # AttachConsole), so the fake subprocess writes there too.
        def fake(cmd, **kw):
            out = cmd[cmd.index("-OutFile") + 1]
            Path(out).write_text(file_content, encoding="utf-8")
            return _proc("")
        with mock.patch.object(nudger.subprocess, "run", side_effect=fake):
            return nudger.probe_and_nudge(1234, tool="claude", apply=True)

    def test_rate_limit_continue_sent_and_verified(self):
        r = self._run('{"state":"rate-limit","action":"continue-sent","verified":true}')
        self.assertEqual(r.state, "rate-limit")
        self.assertTrue(r.nudged)
        self.assertTrue(r.verified)
        self.assertTrue(r.recoverable)

    def test_context_limit_compact(self):
        r = self._run('{"state":"context-limit","action":"compact-sent","verified":false}')
        self.assertTrue(r.nudged)
        self.assertEqual(r.action, "compact-sent")

    def test_working_session_is_not_nudged(self):
        r = self._run('{"state":"working-or-unknown","action":"none","verified":false}')
        self.assertFalse(r.nudged)
        self.assertFalse(r.recoverable)

    def test_non_claude_tool_skipped_without_subprocess(self):
        with mock.patch.object(nudger.subprocess, "run") as run:
            r = nudger.probe_and_nudge(1234, tool="codex", apply=True)
        run.assert_not_called()
        self.assertEqual(r.state, "skipped-tool")
        self.assertFalse(r.nudged)

    def test_noise_before_json_is_tolerated(self):
        r = self._run('WARN something\n{"state":"rate-limit","action":"continue-sent","verified":true}')
        self.assertTrue(r.nudged)

    def test_bad_output_is_error_not_crash(self):
        r = self._run("not json at all")
        self.assertEqual(r.state, "error")
        self.assertFalse(r.nudged)


class MonitorNudgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "done").mkdir()
        (root / "in-progress").mkdir()
        # config: probe immediately (cooldown 0), short poll
        self.config = {
            "tasks_root": self.tmp.name, "poll_seconds": 0, "max_run_seconds": 60,
            "nudge_enabled": True, "nudge_probe_cooldown_seconds": 0,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, slug):
        return Run(id="r1", shift_id="s1", increment_id=slug, tool="claude", status=RunStatus.RUNNING)

    def test_monitor_nudges_then_completes(self):
        slug = "TECH-950-nudge"
        # First loop turn: alive + not done -> probe nudges. Then mark done so loop ends.
        done_dir = Path(self.tmp.name) / "done"
        calls = {"n": 0}

        def fake_done(s, c):
            calls["n"] += 1
            if calls["n"] >= 2:  # become done on the 2nd check (after one nudge)
                (done_dir / f"{slug}.md").write_text("x", encoding="utf-8")
                return True
            return False

        nudged = nudger.NudgeResult("rate-limit", "continue-sent", True, True)
        with mock.patch.object(shift, "task_is_done", side_effect=fake_done), \
             mock.patch.object(shift, "session_alive", return_value=True), \
             mock.patch.object(shift, "session_pid", return_value=4321), \
             mock.patch.object(shift, "_note") as note, \
             mock.patch("nightshift.watchdog.nudger.probe_and_nudge", return_value=nudged):
            run = shift.monitor_run(self._run(slug), Increment(id=slug, summary="x", deliverable_type="cli"), self.config)

        self.assertEqual(run.status, RunStatus.DONE)
        self.assertIn("auto-nudged", run.notes)
        note.assert_called()  # a heartbeat note was filed for the nudge

    def test_monitor_does_not_nudge_working_session(self):
        slug = "TECH-951-nonudge"
        done_dir = Path(self.tmp.name) / "done"
        calls = {"n": 0}

        def fake_done(s, c):
            calls["n"] += 1
            if calls["n"] >= 2:
                (done_dir / f"{slug}.md").write_text("x", encoding="utf-8")
                return True
            return False

        working = nudger.NudgeResult("working-or-unknown", "none", False, False)
        with mock.patch.object(shift, "task_is_done", side_effect=fake_done), \
             mock.patch.object(shift, "session_alive", return_value=True), \
             mock.patch.object(shift, "session_pid", return_value=4321), \
             mock.patch.object(shift, "_note") as note, \
             mock.patch("nightshift.watchdog.nudger.probe_and_nudge", return_value=working):
            run = shift.monitor_run(self._run(slug), Increment(id=slug, summary="x", deliverable_type="cli"), self.config)

        self.assertEqual(run.status, RunStatus.DONE)
        self.assertNotIn("auto-nudged", run.notes or "")
        note.assert_not_called()


if __name__ == "__main__":
    unittest.main()
