"""monitor_run tests: terminal-state classification + verify-on-done wiring."""

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
from nightshift.verifiers import registry  # noqa: E402
from nightshift.verifiers.base import (  # noqa: E402
    Increment,
    Verdict,
    Verifier,
    VerificationResult,
)


def _run(slug):
    return Run(id="r1", shift_id="s1", increment_id=slug, status=RunStatus.RUNNING)


def _inc(slug, **kw):
    return Increment(id=slug, summary="x", deliverable_type=kw.pop("dtype", "webmon"), **kw)


class _MonVerifier(Verifier):
    deliverable_type = "webmon"

    def __init__(self, verdict):
        self._verdict = verdict
        self.calls = 0

    def verify(self, increment, *, config):
        self.calls += 1
        return VerificationResult(
            deliverable_type=self.deliverable_type, verdict=self._verdict,
            evidence_paths=["shot.png"],
        )


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "done").mkdir()
        (root / "in-progress").mkdir()
        self.config = {"tasks_root": self.tmp.name, "poll_seconds": 0, "max_run_seconds": 60}

    def tearDown(self):
        self.tmp.cleanup()

    def test_done_ticket_terminates_done(self):
        slug = "TECH-901-mon"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        run = shift.monitor_run(_run(slug), _inc(slug), self.config)
        self.assertEqual(run.status, RunStatus.DONE)
        self.assertIsNotNone(run.ended_at)

    def test_dead_process_terminates_crashed(self):
        slug = "TECH-902-mon"
        with mock.patch.object(shift, "session_alive", return_value=False):
            run = shift.monitor_run(_run(slug), _inc(slug), self.config)
        self.assertEqual(run.status, RunStatus.CRASHED)

    def test_timeout_terminates_stalled_without_kill(self):
        slug = "TECH-903-mon"
        self.config["max_run_seconds"] = 0
        with mock.patch.object(shift, "session_alive", return_value=True):
            run = shift.monitor_run(_run(slug), _inc(slug), self.config)
        self.assertEqual(run.status, RunStatus.STALLED)
        self.assertIn("timeout", run.notes)

    def test_done_with_target_and_rubric_verifies_pass(self):
        slug = "TECH-904-mon"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        v = registry.register(_MonVerifier(Verdict.PASS))
        run = shift.monitor_run(
            _run(slug), _inc(slug, target="http://x", rubric_path="r.md"), self.config
        )
        self.assertEqual(v.calls, 1)
        self.assertEqual(run.status, RunStatus.VERIFIED)
        self.assertIn("shot.png", run.evidence_paths)

    def test_done_with_fail_verdict_needs_review(self):
        slug = "TECH-905-mon"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        registry.register(_MonVerifier(Verdict.FAIL))
        run = shift.monitor_run(
            _run(slug), _inc(slug, target="http://x", rubric_path="r.md"), self.config
        )
        self.assertEqual(run.status, RunStatus.NEEDS_REVIEW)

    def test_done_without_target_skips_verifier(self):
        slug = "TECH-906-mon"
        (Path(self.tmp.name) / "done" / f"{slug}.md").write_text("x", encoding="utf-8")
        v = registry.register(_MonVerifier(Verdict.PASS))
        run = shift.monitor_run(_run(slug), _inc(slug), self.config)
        self.assertEqual(v.calls, 0)
        self.assertEqual(run.status, RunStatus.DONE)


if __name__ == "__main__":
    unittest.main()
