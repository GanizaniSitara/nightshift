"""Dispatcher tests: prompt assembly, launcher command, managed stamping, run record."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.dispatch import dispatcher  # noqa: E402
from nightshift.orchestration.records import RunStatus  # noqa: E402
from nightshift.verifiers.base import Increment  # noqa: E402


def _inc(slug="TECH-900-test-run"):
    return Increment(
        id=slug,
        summary="Do the thing",
        deliverable_type="web",
        acceptance_criteria=["thing is done"],
    )


class PromptTests(unittest.TestCase):
    def test_prompt_contains_contract_brief_and_done_signal(self):
        prompt = dispatcher.build_run_prompt(_inc(), contract_text="CONTRACT-BODY")
        self.assertIn("Managed run brief", prompt)
        self.assertIn("Do the thing", prompt)
        self.assertIn("thing is done", prompt)
        self.assertIn("append_task_note", prompt)
        self.assertIn("move_task", prompt)
        self.assertIn("CONTRACT-BODY", prompt)

    def test_default_contract_loads_from_worker_dir(self):
        prompt = dispatcher.build_run_prompt(_inc())
        self.assertIn("Nightshift worker contract", prompt)


class CommandTests(unittest.TestCase):
    def test_launcher_command_is_atomic_file_invocation(self):
        cmd = dispatcher.launcher_command({"launcher_path": "C:/x/launch.ps1"}, "SLUG-001", "codex")
        self.assertIn("-File", cmd)
        self.assertIn("C:/x/launch.ps1", cmd)
        self.assertEqual(cmd[cmd.index("task") + 1], "SLUG-001")
        self.assertEqual(cmd[cmd.index("-Tool") + 1], "codex")
        self.assertIn("-NoProfile", cmd)


class StampTests(unittest.TestCase):
    def test_stamp_preserves_existing_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.launch-info.json"
            p.write_text(json.dumps({"TaskSlug": "S", "PID": 7, "Zone": 2}), encoding="utf-8")
            stamped = dispatcher.stamp_managed(p)
            self.assertEqual(stamped["PID"], 7)
            self.assertEqual(stamped["Zone"], 2)
            self.assertTrue(stamped["RunnerManaged"])
            self.assertEqual(stamped["ManagedBy"], "nightshift")
            on_disk = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, stamped)


class DispatchTests(unittest.TestCase):
    def _config(self, tmp):
        return {
            "launcher_path": "C:/x/launch.ps1",
            "tasks_root": tmp,
            "runs_dir": str(Path(tmp) / "runs"),
            "mcp_url": "http://127.0.0.1:9/mcp",
            "launch_info_timeout_seconds": 0.2,
        }

    def test_successful_dispatch_moves_ticket_first_then_notes_launches_stamps_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            slug = "TECH-900-test-run"
            in_prog = Path(tmp) / "in-progress"
            in_prog.mkdir()
            li = in_prog / f"{slug}.launch-info.json"
            li.write_text(json.dumps({"TaskSlug": slug, "PID": 1}), encoding="utf-8")

            calls = []
            fake_client = mock.Mock()
            fake_client.move_task.side_effect = lambda tid, status, **kw: calls.append(("move", tid, status))
            fake_client.append_task_note.side_effect = lambda tid, note, heading: calls.append(("note", tid, heading))

            with mock.patch.object(dispatcher, "TasksMcpClient", return_value=fake_client), mock.patch.object(
                dispatcher.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ):
                run = dispatcher.dispatch_run(_inc(slug), config=self._config(tmp))

            self.assertEqual(run.status, RunStatus.RUNNING)
            # MCP move (frontmatter+file coherent) MUST precede the brief note and launch.
            self.assertEqual(calls[0], ("move", "TECH-900", "in-progress"))
            self.assertEqual(calls[1], ("note", "TECH-900", "Managed run"))
            stamped = json.loads(li.read_text(encoding="utf-8"))
            self.assertEqual(stamped["ManagedBy"], "nightshift")
            records = list((Path(tmp) / "runs").glob("*.json"))
            self.assertEqual(len(records), 1)

    def test_dispatch_skips_move_when_ticket_already_in_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            slug = "TECH-900-test-run"
            in_prog = Path(tmp) / "in-progress"
            in_prog.mkdir()
            (in_prog / f"{slug}.md").write_text("---\ntask: TECH-900\n---\n", encoding="utf-8")
            (in_prog / f"{slug}.launch-info.json").write_text(
                json.dumps({"TaskSlug": slug, "PID": 1}), encoding="utf-8"
            )
            fake_client = mock.Mock()
            with mock.patch.object(dispatcher, "TasksMcpClient", return_value=fake_client), mock.patch.object(
                dispatcher.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ):
                dispatcher.dispatch_run(_inc(slug), config=self._config(tmp))
            fake_client.move_task.assert_not_called()
            fake_client.append_task_note.assert_called_once()

    def test_launcher_failure_marks_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "in-progress").mkdir()
            fake_client = mock.Mock()
            with mock.patch.object(dispatcher, "TasksMcpClient", return_value=fake_client), mock.patch.object(
                dispatcher.subprocess, "run",
                return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="boom"),
            ):
                run = dispatcher.dispatch_run(_inc(), config=self._config(tmp))
            self.assertEqual(run.status, RunStatus.CRASHED)
            self.assertIn("boom", run.notes)


if __name__ == "__main__":
    unittest.main()
