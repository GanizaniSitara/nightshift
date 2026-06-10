import dataclasses
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path
import time
import unittest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.watchdog import watchdog  # noqa: E402


class WatchdogTests(unittest.TestCase):
    def make_stalled_launch(
        self,
        root: Path,
        *,
        managed: bool,
    ) -> tuple[dict, Path, Path]:
        task_slug = "TECH-999-watchdog-regression"
        session_id = "session-999"
        tasks_root = root / "tasks"
        in_progress = tasks_root / "in-progress"
        in_progress.mkdir(parents=True)
        task_path = in_progress / f"{task_slug}.md"
        task_path.write_text("---\ntask: TECH-999\n---\n\n# Regression\n", encoding="utf-8")

        projects = root / ".claude" / "projects" / "demo"
        projects.mkdir(parents=True)
        transcript = projects / f"{session_id}.jsonl"
        transcript.write_text("old output\n", encoding="utf-8")

        old = dt.datetime.now(watchdog.UTC) - dt.timedelta(hours=2)
        for path in (task_path, transcript):
            os.utime(path, (old.timestamp(), old.timestamp()))

        launch = {
            "TaskSlug": task_slug,
            "Tool": "claude",
            "PID": os.getpid(),
            "Zone": 1,
            "SessionId": session_id,
            "WindowTitle": f"Claude {task_slug}",
            "LaunchedAt": "2026-06-10 00:00:00",
        }
        if managed:
            launch["WatchdogOptIn"] = True
        (in_progress / f"{task_slug}.launch-info.json").write_text(json.dumps(launch), encoding="utf-8")

        config = watchdog.default_config()
        config["tasks_root"] = str(tasks_root)
        config["launch_info_glob"] = str(in_progress / "*.launch-info.json")
        config["claude_projects_root"] = str(root / ".claude" / "projects")
        config["state_file"] = str(root / "watchdog-state.json")
        config["stall_seconds"] = 1
        config["heartbeat_seconds"] = 1
        config["watchdog_note_grace_seconds"] = 60
        return config, task_path, transcript

    def test_task_id_from_slug(self):
        self.assertEqual(watchdog.task_id_from_slug("TECH-013-overnight"), "TECH-013")
        self.assertEqual(watchdog.task_id_from_slug("OP-001"), "OP-001")
        self.assertIsNone(watchdog.task_id_from_slug("sample-task"))

    def test_parse_utc_datetime(self):
        parsed = watchdog.parse_datetime("2026-06-09T11:59:51.8699862Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.tzinfo, watchdog.UTC)
        self.assertEqual(parsed.hour, 11)

    def test_codex_transcript_prefers_task_slug(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".codex" / "sessions" / "2026" / "06" / "09"
            sessions.mkdir(parents=True)
            older = sessions / "rollout-2026-06-09T12-00-00-old.jsonl"
            newer = sessions / "rollout-2026-06-09T12-01-00-new.jsonl"
            older.write_text("TECH-013-overnight hello\n", encoding="utf-8")
            newer.write_text("OTHER-001 hello\n", encoding="utf-8")

            launched = dt.datetime.now(watchdog.UTC) - dt.timedelta(seconds=5)
            info = watchdog.LaunchInfo(
                path=root / "TECH-013.launch-info.json",
                task_slug="TECH-013-overnight",
                tool="codex",
                pid=123,
                zone=1,
                session_id="",
                window_title="Codex TECH-013",
                process_start_utc=launched,
                launched_at=launched,
            )
            config = watchdog.default_config()
            config["codex_sessions_root"] = str(root / ".codex" / "sessions")
            config["transcript_match_window_seconds"] = 60

            transcript = watchdog.resolve_codex_transcript(info, config)
            self.assertIsNotNone(transcript)
            self.assertEqual(transcript.path, older)

    def test_codex_transcript_prefers_launch_proximity_over_newest_slug_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".codex" / "sessions" / "2026" / "06" / "09"
            sessions.mkdir(parents=True)
            correct = sessions / "rollout-2026-06-09T11-51-48-correct.jsonl"
            polluted = sessions / "rollout-2026-06-09T12-59-52-polluted.jsonl"
            correct.write_text("TECH-033-sample initial prompt\n", encoding="utf-8")
            polluted.write_text("later conversation mentioned TECH-033\n", encoding="utf-8")

            launched = watchdog.codex_rollout_time(correct)
            self.assertIsNotNone(launched)

            info = watchdog.LaunchInfo(
                path=root / "TECH-033.launch-info.json",
                task_slug="TECH-033-sample",
                tool="codex",
                pid=123,
                zone=1,
                session_id="",
                window_title="Codex TECH-033",
                process_start_utc=launched,
                launched_at=launched,
            )
            config = watchdog.default_config()
            config["codex_sessions_root"] = str(root / ".codex" / "sessions")
            config["transcript_match_window_seconds"] = 60

            transcript = watchdog.resolve_codex_transcript(info, config)
            self.assertIsNotNone(transcript)
            self.assertEqual(transcript.path, correct)

    def test_default_policy_keeps_unmanaged_sessions_unalertable(self):
        now = dt.datetime.now(watchdog.UTC)
        launched = now - dt.timedelta(hours=1)
        info = watchdog.LaunchInfo(
            path=Path("TECH-013.launch-info.json"),
            task_slug="TECH-013-overnight",
            tool="codex",
            pid=123,
            zone=1,
            session_id="",
            window_title="Codex TECH-013",
            process_start_utc=launched,
            launched_at=launched,
        )
        process = watchdog.ProcessCheck(True, "alive", launched)
        stalled = watchdog.Observation(
            launch=info,
            state="STALLED",
            process=process,
            task_id="TECH-013",
            task_path=None,
            done_path=None,
            transcript=None,
            transcript_idle_seconds=3600,
            heartbeat_idle_seconds=3600,
            last_transcript_lines=[],
            episode_key="TECH-013|pid123|STALLED",
        )
        state = {"episodes": {}, "sessions": {}}

        baseline_started = watchdog.ensure_baseline_initialized(state, now)
        self.assertTrue(baseline_started)
        self.assertFalse(watchdog.session_is_alertable(state, stalled))

        watchdog.update_session_state(
            state,
            stalled,
            now,
            baseline_started_this_cycle=True,
        )
        key = watchdog.session_identity(info)
        self.assertTrue(state["sessions"][key]["baseline_suppressed"])
        self.assertFalse(state["sessions"][key]["alertable"])
        self.assertFalse(state["sessions"][key]["managed_for_alerts"])

        healthy = dataclasses.replace(stalled, state="HEALTHY")
        watchdog.update_session_state(
            state,
            healthy,
            now + dt.timedelta(minutes=5),
            baseline_started_this_cycle=False,
        )
        self.assertFalse(state["sessions"][key]["alertable"])
        self.assertFalse(watchdog.session_is_alertable(state, stalled))

    def test_managed_baseline_session_becomes_alertable_after_seen_healthy(self):
        now = dt.datetime.now(watchdog.UTC)
        launched = now - dt.timedelta(hours=1)
        info = watchdog.LaunchInfo(
            path=Path("TECH-013.launch-info.json"),
            task_slug="TECH-013-overnight",
            tool="codex",
            pid=123,
            zone=1,
            session_id="",
            window_title="Codex TECH-013",
            process_start_utc=launched,
            launched_at=launched,
            extra={"WatchdogOptIn": True},
        )
        process = watchdog.ProcessCheck(True, "alive", launched)
        stalled = watchdog.Observation(
            launch=info,
            state="STALLED",
            process=process,
            task_id="TECH-013",
            task_path=None,
            done_path=None,
            transcript=None,
            transcript_idle_seconds=3600,
            heartbeat_idle_seconds=3600,
            last_transcript_lines=[],
            episode_key="TECH-013|pid123|STALLED",
        )
        state = {"episodes": {}, "sessions": {}}

        baseline_started = watchdog.ensure_baseline_initialized(state, now)
        self.assertTrue(baseline_started)
        self.assertFalse(watchdog.session_is_alertable(state, stalled))

        watchdog.update_session_state(
            state,
            stalled,
            now,
            baseline_started_this_cycle=True,
        )
        key = watchdog.session_identity(info)
        self.assertTrue(state["sessions"][key]["baseline_suppressed"])
        self.assertTrue(state["sessions"][key]["managed_for_alerts"])
        self.assertFalse(state["sessions"][key]["alertable"])

        healthy = dataclasses.replace(stalled, state="HEALTHY")
        watchdog.update_session_state(
            state,
            healthy,
            now + dt.timedelta(minutes=5),
            baseline_started_this_cycle=False,
        )
        self.assertTrue(state["sessions"][key]["alertable"])
        self.assertTrue(watchdog.session_is_alertable(state, stalled))

    def test_unmanaged_stalled_session_is_observed_but_not_alerted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, _task_path, _transcript = self.make_stalled_launch(root, managed=False)

            original_handle_alert = watchdog.handle_alert

            def fail_handle_alert(*_args, **_kwargs):
                raise AssertionError("unmanaged sessions must not alert")

            watchdog.handle_alert = fail_handle_alert
            try:
                observations = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                )
            finally:
                watchdog.handle_alert = original_handle_alert

            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].state, "STALLED")
            self.assertEqual(observations[0].actions, ["unmanaged-suppressed"])

            state = watchdog.load_state(Path(config["state_file"]))
            session = next(iter(state["sessions"].values()))
            self.assertFalse(session["alertable"])
            self.assertFalse(session["managed_for_alerts"])

    def test_watchdog_task_note_does_not_clear_episode_or_repeat_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, task_path, _transcript = self.make_stalled_launch(root, managed=True)
            config["email"]["enabled"] = True  # public default is off; this test exercises the email path
            email_subjects: list[str] = []
            test_case = self

            class FakeTasksMcpClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def append_task_note(self, _task_id, _note, heading="Watchdog"):
                    test_case.assertEqual(heading, "Watchdog")
                    task_path.write_text(task_path.read_text(encoding="utf-8") + "\n## Watchdog\n\nalert\n", encoding="utf-8")
                    now = time.time()
                    os.utime(task_path, (now, now))

            original_client = watchdog.TasksMcpClient
            original_send_email = watchdog.send_email
            watchdog.TasksMcpClient = FakeTasksMcpClient
            watchdog.send_email = lambda _config, subject, _body: email_subjects.append(subject)
            try:
                first = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                    include_existing=True,
                )
                second = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                    include_existing=True,
                )
            finally:
                watchdog.TasksMcpClient = original_client
                watchdog.send_email = original_send_email

            self.assertEqual(first[0].state, "STALLED")
            self.assertIn("task-note", first[0].actions)
            self.assertIn("email", first[0].actions)
            self.assertEqual(len(email_subjects), 1)

            self.assertEqual(second[0].state, "STALLED")
            self.assertIn("watchdog-heartbeat-ignored", second[0].actions)
            self.assertIn("deduped", second[0].actions)
            self.assertEqual(len(email_subjects), 1)

    def test_transcript_progress_clears_prior_alert_episode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, task_path, transcript = self.make_stalled_launch(root, managed=True)

            class FakeTasksMcpClient:
                def __init__(self, *_args, **_kwargs):
                    pass

                def append_task_note(self, _task_id, _note, heading="Watchdog"):
                    now = time.time()
                    os.utime(task_path, (now, now))

            original_client = watchdog.TasksMcpClient
            original_send_email = watchdog.send_email
            watchdog.TasksMcpClient = FakeTasksMcpClient
            watchdog.send_email = lambda *_args, **_kwargs: None
            try:
                first = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                    include_existing=True,
                )
                transcript.write_text(transcript.read_text(encoding="utf-8") + "new output\n", encoding="utf-8")
                now = time.time()
                os.utime(transcript, (now, now))
                second = watchdog.run_cycle(
                    config,
                    dry_run=False,
                    alerts_enabled=True,
                    notes_enabled=True,
                    email_enabled=True,
                    include_existing=True,
                )
            finally:
                watchdog.TasksMcpClient = original_client
                watchdog.send_email = original_send_email

            self.assertEqual(first[0].state, "STALLED")
            self.assertEqual(second[0].state, "HEALTHY")
            state = watchdog.load_state(Path(config["state_file"]))
            self.assertEqual(state["episodes"], {})


if __name__ == "__main__":
    unittest.main()
