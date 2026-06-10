"""Evaluator tests: output parsing and verdict mapping, with subprocess mocked."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.verifiers import evaluator  # noqa: E402
from nightshift.verifiers.base import Verdict  # noqa: E402


def _proc(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class EvaluatorTests(unittest.TestCase):
    def _run(self, stdout):
        with mock.patch.object(evaluator.subprocess, "run", return_value=_proc(stdout)):
            return evaluator.evaluate_screenshot("img.png", "header present", ["loads"])

    def test_claude_envelope_with_json_result(self):
        verdict_json = json.dumps(
            {"verdict": "pass", "findings": [{"rubric_item": "header present", "verdict": "pass"}]}
        )
        envelope = json.dumps({"type": "result", "result": verdict_json})
        verdict, findings, _ = self._run(envelope)
        self.assertEqual(verdict, Verdict.PASS)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].verdict, Verdict.PASS)

    def test_structured_output_field_is_preferred(self):
        envelope = json.dumps(
            {
                "result": "Here is my assessment of the page in prose, no JSON here.",
                "structured_output": {
                    "verdict": "fail",
                    "findings": [{"rubric_item": "header", "verdict": "fail", "notes": "missing"}],
                },
            }
        )
        verdict, findings, _ = self._run(envelope)
        self.assertEqual(verdict, Verdict.FAIL)
        self.assertEqual(findings[0].verdict, Verdict.FAIL)

    def test_fail_verdict_maps(self):
        verdict_json = json.dumps({"verdict": "fail", "findings": []})
        envelope = json.dumps({"result": verdict_json})
        verdict, _, _ = self._run(envelope)
        self.assertEqual(verdict, Verdict.FAIL)

    def test_result_text_with_codefence_is_extracted(self):
        fenced = "Here is the result:\n```json\n{\"verdict\":\"needs-human\",\"findings\":[]}\n```"
        envelope = json.dumps({"result": fenced})
        verdict, _, _ = self._run(envelope)
        self.assertEqual(verdict, Verdict.NEEDS_HUMAN)

    def test_unknown_verdict_string_falls_back_to_needs_human(self):
        envelope = json.dumps({"result": json.dumps({"verdict": "maybe", "findings": []})})
        verdict, _, _ = self._run(envelope)
        self.assertEqual(verdict, Verdict.NEEDS_HUMAN)

    def test_nonzero_rc_with_no_output_raises(self):
        with mock.patch.object(evaluator.subprocess, "run", return_value=_proc("", 1, "boom")):
            with self.assertRaises(RuntimeError):
                evaluator.evaluate_screenshot("img.png", "x", [])

    def test_command_uses_safe_mode_and_json_output(self):
        cmd = evaluator._build_command("claude", "C:/dir", "sonnet")
        self.assertIn("--safe-mode", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("-p", cmd)
        self.assertIn("--add-dir", cmd)

    def test_prompt_passed_via_stdin_not_argv(self):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["input"] = kwargs.get("input")
            return _proc('{"result":"{\\"verdict\\":\\"pass\\",\\"findings\\":[]}"}')

        with mock.patch.object(evaluator.subprocess, "run", side_effect=fake_run):
            evaluator.evaluate_screenshot("img.png", "header present", ["loads"])
        self.assertIn("header present", captured["input"])
        self.assertNotIn("header present", " ".join(captured["cmd"]))


if __name__ == "__main__":
    unittest.main()
