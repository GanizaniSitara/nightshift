"""WebVerifier wiring tests with capture + evaluator monkeypatched (no browser/CLI)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.verifiers import web  # noqa: E402
from nightshift.verifiers.base import Increment, Verdict, VisionFinding  # noqa: E402
from nightshift.verifiers.capture import Capture  # noqa: E402


class WebVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = web.WebVerifier()

    def test_no_target_is_fail(self):
        inc = Increment(id="i", summary="x", deliverable_type="web")
        result = self.verifier.verify(inc, config={})
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertIn("no target", result.notes)

    def test_unloadable_page_is_fail_without_calling_evaluator(self):
        inc = Increment(id="i", summary="x", deliverable_type="web", target="http://x")
        bad = Capture(url="http://x", screenshot_path="s.png", loaded=False, error="boom")
        with mock.patch.object(web, "capture", return_value=bad), mock.patch.object(
            web, "evaluate_screenshot"
        ) as ev:
            result = self.verifier.verify(inc, config={})
        ev.assert_not_called()
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertFalse(result.built)
        self.assertIn("did not load", result.notes)

    def test_loaded_page_runs_evaluator_and_returns_verdict(self):
        inc = Increment(id="i", summary="x", deliverable_type="web", target="http://x")
        good = Capture(url="http://x", screenshot_path="s.png", loaded=True, title="Example Site")
        findings = [VisionFinding(rubric_item="header", verdict=Verdict.PASS)]
        with mock.patch.object(web, "capture", return_value=good), mock.patch.object(
            web, "evaluate_screenshot", return_value=(Verdict.PASS, findings, "{}")
        ):
            result = self.verifier.verify(inc, config={})
        self.assertEqual(result.verdict, Verdict.PASS)
        self.assertTrue(result.built)
        self.assertEqual(result.screenshots, ["s.png"])
        self.assertEqual(len(result.vision_findings), 1)


if __name__ == "__main__":
    unittest.main()
