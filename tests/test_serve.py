"""Tests for the verifier-owned app restart (serve.py) and its monitor wiring."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift import serve  # noqa: E402
from nightshift.orchestration import shift  # noqa: E402
from nightshift.orchestration.records import Run, RunStatus  # noqa: E402
from nightshift.verifiers import registry  # noqa: E402
from nightshift.verifiers.base import Increment, Verdict, Verifier, VerificationResult  # noqa: E402


class _ServeVerifier(Verifier):
    deliverable_type = "webserve"

    def verify(self, increment, *, config):
        return VerificationResult(deliverable_type="webserve", verdict=Verdict.PASS)


class RestartGuardTests(unittest.TestCase):
    def test_no_serve_cmd_or_port_is_noop(self):
        self.assertFalse(serve.restart_app("", 8875)["ok"])
        self.assertFalse(serve.restart_app("python x", 0)["ok"])


class MonitorRestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "done").mkdir()
        (root / "in-progress").mkdir()
        self.config = {
            "tasks_root": self.tmp.name, "poll_seconds": 0, "max_run_seconds": 60,
            "nudge_enabled": False, "serve_cmd": "python run_web.py",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_app_restarted_on_target_port_before_verify(self):
        slug = "TECH-970-serve"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        registry.register(_ServeVerifier())
        inc = Increment(id=slug, summary="x", deliverable_type="webserve",
                        target="http://127.0.0.1:8875/?q=demo", rubric_path="r.md")
        run = Run(id="r1", shift_id="s1", increment_id=slug, tool="claude", status=RunStatus.RUNNING)
        with mock.patch.object(shift, "task_is_done", return_value=True), \
             mock.patch("nightshift.serve.restart_app", return_value={"ok": True, "killed": [1]}) as rs:
            out = shift.monitor_run(run, inc, self.config)
        rs.assert_called_once()
        self.assertEqual(rs.call_args.args[0], "python run_web.py")
        self.assertEqual(rs.call_args.args[1], 8875)  # port parsed from target
        self.assertEqual(out.status, RunStatus.VERIFIED)

    def test_no_restart_when_no_serve_cmd(self):
        slug = "TECH-971-noserve"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        registry.register(_ServeVerifier())
        cfg = dict(self.config)
        cfg.pop("serve_cmd")
        inc = Increment(id=slug, summary="x", deliverable_type="webserve",
                        target="http://127.0.0.1:8875/", rubric_path="r.md")
        run = Run(id="r2", shift_id="s1", increment_id=slug, tool="claude", status=RunStatus.RUNNING)
        with mock.patch.object(shift, "task_is_done", return_value=True), \
             mock.patch("nightshift.serve.restart_app") as rs:
            shift.monitor_run(run, inc, cfg)
        rs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
