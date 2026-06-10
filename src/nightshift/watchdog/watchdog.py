#!/usr/bin/env python
"""Alert-only liveness watchdog for launcher-managed agent sessions."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
from email.message import EmailMessage
import json
import os
from pathlib import Path
import re
import smtplib
import sys
import time
from typing import Any
import urllib.error
import urllib.request

try:
    import psutil
except ImportError:  # pragma: no cover - production env has psutil.
    psutil = None  # type: ignore[assignment]


UTC = dt.timezone.utc
TASK_ID_RE = re.compile(r"^([A-Z][A-Z0-9]+-\d{3,})\b")
CODEX_ROLLOUT_RE = re.compile(
    r"^rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-"
)


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def user_home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def default_config() -> dict[str, Any]:
    home = user_home()
    root = repo_root()
    smtp_password = os.environ.get("NIGHTSHIFT_SMTP_PASSWORD", "")
    return {
        "tasks_root": str(home / "tasks"),
        "launch_info_glob": str(home / "tasks" / "in-progress" / "*.launch-info.json"),
        "claude_projects_root": str(home / ".claude" / "projects"),
        "codex_sessions_root": str(home / ".codex" / "sessions"),
        "state_file": str(root / "state" / "watchdog-state.json"),
        "mcp_url": os.environ.get("TASKS_MCP_URL", "http://127.0.0.1:8876/mcp"),
        "poll_seconds": 120,
        "stall_seconds": 900,
        "heartbeat_seconds": 1800,
        "process_start_tolerance_seconds": 5,
        "transcript_match_window_seconds": 60,
        "recent_transcript_lines": 5,
        "watchdog_note_grace_seconds": 60,
        "alert_requires_managed": True,
        "managed_launch_fields": [
            "RunnerManaged",
            "AgentRunnerManaged",
            "WatchdogManaged",
            "WatchdogOptIn",
            "Watchdog",
        ],
        "mcp_timeout_seconds": 10,
        "email": {
            "enabled": False,
            "smtp_host": "localhost",
            "smtp_port": 587,
            "smtp_timeout_seconds": 10,
            "smtp_starttls": False,
            "username": "",
            "password": smtp_password,
            "from": "",
            "to": "",
            "subject_prefix": "NIGHTSHIFT",
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path | None) -> dict[str, Any]:
    config = default_config()
    if path and path.exists():
        with path.open("r", encoding="utf-8-sig") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file must contain a JSON object: {path}")
        config = deep_merge(config, loaded)
    return config


def parse_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(UTC)


def iso_utc(value: dt.datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def file_mtime(path: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def file_ctime(path: Path) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_ctime, UTC)
    except OSError:
        return None


def age_seconds(now: dt.datetime, then: dt.datetime | None) -> float | None:
    if not then:
        return None
    return max(0.0, (now - then).total_seconds())


def minutes_text(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    return f"{seconds / 60:.1f}m"


@dataclasses.dataclass(slots=True)
class LaunchInfo:
    path: Path
    task_slug: str
    tool: str
    pid: int
    zone: int | None
    session_id: str
    window_title: str
    process_start_utc: dt.datetime | None
    launched_at: dt.datetime | None
    extra: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True)
class ProcessCheck:
    alive: bool
    reason: str
    create_time_utc: dt.datetime | None = None


@dataclasses.dataclass(slots=True)
class Transcript:
    path: Path
    reason: str


@dataclasses.dataclass(slots=True)
class Observation:
    launch: LaunchInfo
    state: str
    process: ProcessCheck
    task_id: str | None
    task_path: Path | None
    done_path: Path | None
    transcript: Transcript | None
    transcript_idle_seconds: float | None
    heartbeat_idle_seconds: float | None
    last_transcript_lines: list[str]
    episode_key: str
    transcript_mtime: dt.datetime | None = None
    heartbeat_mtime: dt.datetime | None = None
    actions: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)


def load_launch_info(path: Path) -> LaunchInfo | None:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"WARN corrupt launch-info {path}: {exc}", file=sys.stderr)
        return None

    slug = str(raw.get("TaskSlug") or path.name.removesuffix(".launch-info.json"))
    try:
        pid = int(raw.get("PID") or 0)
    except (TypeError, ValueError):
        pid = 0
    try:
        zone = int(raw.get("Zone")) if raw.get("Zone") is not None else None
    except (TypeError, ValueError):
        zone = None

    known_keys = {
        "TaskSlug",
        "Tool",
        "PID",
        "Zone",
        "SessionId",
        "WindowTitle",
        "ProcessStartTimeUtc",
        "LaunchedAt",
    }

    return LaunchInfo(
        path=path,
        task_slug=slug,
        tool=str(raw.get("Tool") or "claude").lower(),
        pid=pid,
        zone=zone,
        session_id=str(raw.get("SessionId") or ""),
        window_title=str(raw.get("WindowTitle") or ""),
        process_start_utc=parse_datetime(raw.get("ProcessStartTimeUtc")),
        launched_at=parse_datetime(raw.get("LaunchedAt")),
        extra={key: value for key, value in raw.items() if key not in known_keys},
    )


def iter_launch_infos(config: dict[str, Any]) -> list[LaunchInfo]:
    glob_text = str(config["launch_info_glob"])
    paths = sorted(Path().glob(glob_text) if not re.match(r"^[A-Za-z]:", glob_text) else Path(glob_text).parent.glob(Path(glob_text).name))
    infos: list[LaunchInfo] = []
    for path in paths:
        info = load_launch_info(path)
        if info:
            infos.append(info)
    return infos


def task_id_from_slug(slug: str) -> str | None:
    match = TASK_ID_RE.match(slug)
    return match.group(1) if match else None


def find_task_path(tasks_root: Path, status: str, slug: str) -> Path | None:
    status_dir = tasks_root / status
    exact = status_dir / f"{slug}.md"
    if exact.exists():
        return exact
    try:
        candidates = [
            path
            for path in status_dir.glob("*.md")
            if path.name.startswith(slug) and not path.name.endswith(".launch-info.json")
        ]
    except OSError:
        return None
    return sorted(candidates, key=lambda p: p.name.lower())[0] if candidates else None


def check_process(info: LaunchInfo, tolerance_seconds: float) -> ProcessCheck:
    if info.pid <= 0:
        return ProcessCheck(False, "no-pid")
    if psutil is None:
        return ProcessCheck(False, "psutil-not-installed")
    try:
        proc = psutil.Process(info.pid)
        create_time = dt.datetime.fromtimestamp(proc.create_time(), UTC)
        if info.process_start_utc:
            drift = abs((create_time - info.process_start_utc).total_seconds())
            if drift > tolerance_seconds:
                return ProcessCheck(False, f"pid-reused-start-drift-{drift:.1f}s", create_time)
        if not proc.is_running():
            return ProcessCheck(False, "not-running", create_time)
        return ProcessCheck(True, "alive", create_time)
    except psutil.NoSuchProcess:
        return ProcessCheck(False, "no-such-process")
    except psutil.AccessDenied:
        return ProcessCheck(True, "access-denied-assumed-alive")
    except Exception as exc:  # pragma: no cover - defensive for platform oddities.
        return ProcessCheck(False, f"process-check-error-{type(exc).__name__}")


def contains_text(path: Path, text: str, max_bytes: int = 20_000_000) -> bool:
    if not text:
        return False
    needle = text.encode("utf-8", errors="ignore").lower()
    if not needle:
        return False
    try:
        with path.open("rb") as handle:
            seen = b""
            remaining = max_bytes
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                haystack = (seen + chunk).lower()
                if needle in haystack:
                    return True
                seen = haystack[-len(needle) :]
                remaining -= len(chunk)
    except OSError:
        return False
    return False


def session_file_time(path: Path) -> dt.datetime:
    rollout_time = codex_rollout_time(path)
    mtime = file_mtime(path)
    ctime = file_ctime(path)
    values = [value for value in (rollout_time, mtime, ctime) if value]
    return max(values) if values else dt.datetime.fromtimestamp(0, UTC)


def codex_rollout_time(path: Path) -> dt.datetime | None:
    match = CODEX_ROLLOUT_RE.match(path.name)
    if not match:
        return None
    year, month, day, hour, minute, second = [int(part) for part in match.groups()]
    local_time = dt.datetime(year, month, day, hour, minute, second).astimezone()
    return local_time.astimezone(UTC)


def after_launch(path: Path, launched: dt.datetime | None, window_seconds: float) -> bool:
    if not launched:
        return True
    threshold = launched - dt.timedelta(seconds=window_seconds)
    rollout_time = codex_rollout_time(path)
    mtime = file_mtime(path)
    ctime = file_ctime(path)
    return bool(
        (rollout_time and rollout_time >= threshold)
        or (mtime and mtime >= threshold)
        or (ctime and ctime >= threshold)
    )


def newest_file(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    return sorted(paths, key=session_file_time, reverse=True)[0]


def closest_file_to_launch(paths: list[Path], launched: dt.datetime | None) -> Path | None:
    if not paths:
        return None
    if not launched:
        return newest_file(paths)

    def score(path: Path) -> tuple[float, float]:
        created = codex_rollout_time(path) or file_ctime(path) or file_mtime(path) or dt.datetime.fromtimestamp(0, UTC)
        modified = file_mtime(path) or created
        return (
            abs((created - launched).total_seconds()),
            -modified.timestamp(),
        )

    return sorted(paths, key=score)[0]


def resolve_claude_transcript(info: LaunchInfo, config: dict[str, Any]) -> Transcript | None:
    root = Path(str(config["claude_projects_root"]))
    if not root.exists():
        return None

    if info.session_id:
        matches = list(root.rglob(f"{info.session_id}.jsonl"))
        chosen = newest_file(matches)
        if chosen:
            return Transcript(chosen, "claude-session-id")

    launched = info.process_start_utc or info.launched_at
    window = float(config["transcript_match_window_seconds"])
    try:
        candidates = [
            path
            for path in root.rglob("*.jsonl")
            if after_launch(path, launched, window)
        ]
    except OSError:
        return None

    containing = [path for path in candidates if contains_text(path, info.task_slug)]
    chosen = closest_file_to_launch(containing, launched)
    if chosen:
        return Transcript(chosen, "claude-task-slug")
    return None


def resolve_codex_transcript(info: LaunchInfo, config: dict[str, Any]) -> Transcript | None:
    root = Path(str(config["codex_sessions_root"]))
    if not root.exists():
        return None

    try:
        all_rollouts = list(root.rglob("rollout-*.jsonl"))
    except OSError:
        return None

    if info.session_id:
        direct = [path for path in all_rollouts if info.session_id in path.name]
        chosen = newest_file(direct)
        if chosen:
            return Transcript(chosen, "codex-session-id")

    launched = info.process_start_utc or info.launched_at
    window = float(config["transcript_match_window_seconds"])
    candidates = [path for path in all_rollouts if after_launch(path, launched, window)]
    containing = [path for path in candidates if contains_text(path, info.task_slug)]
    chosen = closest_file_to_launch(containing, launched)
    if chosen:
        return Transcript(chosen, "codex-task-slug")

    if launched:
        near_launch = []
        for path in candidates:
            ctime = file_ctime(path)
            if ctime and abs((ctime - launched).total_seconds()) <= 180:
                near_launch.append(path)
        chosen = newest_file(near_launch)
        if chosen:
            return Transcript(chosen, "codex-near-launch")

    chosen = newest_file(candidates)
    if chosen and len(candidates) == 1:
        return Transcript(chosen, "codex-single-candidate")
    return None


def resolve_transcript(info: LaunchInfo, config: dict[str, Any]) -> Transcript | None:
    if info.tool == "claude":
        return resolve_claude_transcript(info, config)
    if info.tool == "codex":
        return resolve_codex_transcript(info, config)
    return None


def tail_lines(path: Path, count: int, max_bytes: int = 96_000) -> list[str]:
    if count <= 0:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            raw = handle.read()
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if lines and raw and not raw.startswith(b"\n") and len(raw) == max_bytes:
        lines = lines[1:]
    return [compact_line(line) for line in lines[-count:]]


def compact_line(line: str, limit: int = 700) -> str:
    text = line.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            pieces = []
            for key in ("type", "role", "event", "name"):
                if key in parsed:
                    pieces.append(f"{key}={parsed[key]}")
            for key in ("message", "content", "text"):
                value = parsed.get(key)
                snippet = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
                if snippet:
                    pieces.append(f"{key}={snippet}")
                    break
            text = " ".join(pieces) or text
    except json.JSONDecodeError:
        pass
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def observe(info: LaunchInfo, config: dict[str, Any], now: dt.datetime) -> Observation:
    tasks_root = Path(str(config["tasks_root"]))
    task_path = find_task_path(tasks_root, "in-progress", info.task_slug)
    done_path = find_task_path(tasks_root, "done", info.task_slug)
    process = check_process(info, float(config["process_start_tolerance_seconds"]))
    transcript = resolve_transcript(info, config)
    transcript_mtime = file_mtime(transcript.path) if transcript else None
    transcript_idle = age_seconds(now, transcript_mtime)
    heartbeat_mtime = file_mtime(task_path) if task_path else None
    heartbeat_idle = age_seconds(now, heartbeat_mtime)
    last_lines = tail_lines(transcript.path, int(config["recent_transcript_lines"])) if transcript else []

    task_id = task_id_from_slug(info.task_slug)
    launched = info.process_start_utc or info.launched_at
    missing_transcript_idle = age_seconds(now, launched) if not transcript else None
    effective_transcript_idle = transcript_idle if transcript_idle is not None else missing_transcript_idle

    if done_path:
        state = "DONE"
    elif not process.alive:
        state = "CRASHED"
    elif (
        effective_transcript_idle is not None
        and heartbeat_idle is not None
        and effective_transcript_idle > float(config["stall_seconds"])
        and heartbeat_idle > float(config["heartbeat_seconds"])
    ):
        state = "STALLED"
    else:
        state = "HEALTHY"

    session_key = info.session_id or f"pid{info.pid}-{iso_utc(info.process_start_utc) or info.path.name}"
    episode_key = f"{info.task_slug}|{session_key}|{state}"
    return Observation(
        launch=info,
        state=state,
        process=process,
        task_id=task_id,
        task_path=task_path,
        done_path=done_path,
        transcript=transcript,
        transcript_idle_seconds=effective_transcript_idle,
        heartbeat_idle_seconds=heartbeat_idle,
        last_transcript_lines=last_lines,
        episode_key=episode_key,
        transcript_mtime=transcript_mtime,
        heartbeat_mtime=heartbeat_mtime,
    )


class McpError(RuntimeError):
    pass


class TasksMcpClient:
    def __init__(self, url: str, timeout_seconds: float) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.next_id = 1

    def initialize(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nightshift-watchdog", "version": "1.0"},
            },
        }
        headers, _ = self._post(payload)
        self.session_id = headers.get("mcp-session-id")
        if not self.session_id:
            raise McpError("tasks MCP did not return mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def append_task_note(self, task_id: str, note: str, heading: str = "Watchdog") -> None:
        if not self.session_id:
            self.initialize()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": "append_task_note",
                "arguments": {"task_id": task_id, "heading": heading, "note": note},
            },
        }
        _, body = self._post(payload)
        parsed = parse_mcp_response(body)
        if isinstance(parsed, dict) and parsed.get("error"):
            raise McpError(json.dumps(parsed["error"], ensure_ascii=False))

    def _next_id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, str], str]:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                body = response.read().decode("utf-8", errors="replace")
                return response_headers, body
        except urllib.error.URLError as exc:
            raise McpError(f"tasks MCP request failed: {exc}") from exc


def parse_mcp_response(body: str) -> dict[str, Any] | None:
    text = body.strip()
    if not text:
        return None
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data: "):
            data = line[6:].strip()
            if data:
                return json.loads(data)
    return None


def load_state(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"episodes": {}, "sessions": {}, "_file_existed": False}
    except (OSError, json.JSONDecodeError):
        return {"episodes": {}, "sessions": {}, "_file_existed": False}
    if not isinstance(data, dict):
        return {"episodes": {}, "sessions": {}, "_file_existed": False}
    data.setdefault("episodes", {})
    data.setdefault("sessions", {})
    data["_file_existed"] = True
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    persisted = {key: value for key, value in state.items() if not key.startswith("_")}
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(persisted, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def session_identity(info: LaunchInfo) -> str:
    session_key = info.session_id or f"pid{info.pid}-{iso_utc(info.process_start_utc) or info.path.name}"
    return f"{info.task_slug}|{session_key}"


def session_episode_prefix(info: LaunchInfo) -> str:
    return f"{session_identity(info)}|"


def ensure_baseline_initialized(state: dict[str, Any], now: dt.datetime) -> bool:
    if state.get("baseline_initialized_at"):
        return False
    state["baseline_initialized_at"] = iso_utc(now)
    return True


def is_launched_after_baseline(observation: Observation, state: dict[str, Any]) -> bool:
    baseline = parse_datetime(state.get("baseline_initialized_at"))
    launched = observation.launch.process_start_utc or observation.launch.launched_at
    if not baseline or not launched:
        return False
    return launched >= baseline


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "managed", "opt-in", "optin"}
    return False


def extra_value_case_insensitive(info: LaunchInfo, field_name: str) -> Any:
    if field_name in info.extra:
        return info.extra[field_name]
    lower_name = field_name.lower()
    for key, value in info.extra.items():
        if key.lower() == lower_name:
            return value
    return None


def launch_is_managed_for_alerts(info: LaunchInfo, config: dict[str, Any]) -> bool:
    if not bool(config.get("alert_requires_managed", True)):
        return True

    for field_name in config.get("managed_launch_fields", []):
        if is_truthy(extra_value_case_insensitive(info, str(field_name))):
            return True

    managed_by = extra_value_case_insensitive(info, "ManagedBy")
    if isinstance(managed_by, str) and managed_by.strip().lower() in {"nightshift", "watchdog", "dispatcher"}:
        return True
    return False


def session_is_alertable(
    state: dict[str, Any],
    observation: Observation,
    config: dict[str, Any] | None = None,
) -> bool:
    config = config or default_config()
    if not launch_is_managed_for_alerts(observation.launch, config):
        return False
    sessions = state.setdefault("sessions", {})
    key = session_identity(observation.launch)
    entry = sessions.get(key)
    if isinstance(entry, dict) and bool(entry.get("alertable")):
        return True
    return is_launched_after_baseline(observation, state)


def update_session_state(
    state: dict[str, Any],
    observation: Observation,
    now: dt.datetime,
    *,
    baseline_started_this_cycle: bool,
    config: dict[str, Any] | None = None,
) -> None:
    config = config or default_config()
    sessions = state.setdefault("sessions", {})
    key = session_identity(observation.launch)
    previous = sessions.get(key)
    entry = previous if isinstance(previous, dict) else {}

    launched_after_baseline = is_launched_after_baseline(observation, state)
    managed_for_alerts = launch_is_managed_for_alerts(observation.launch, config)
    ever_healthy = bool(entry.get("ever_healthy")) or observation.state == "HEALTHY"
    alertable = managed_for_alerts and (bool(entry.get("alertable")) or ever_healthy or launched_after_baseline)
    baseline_suppressed = bool(entry.get("baseline_suppressed"))
    if (
        baseline_started_this_cycle
        and observation.state in {"STALLED", "CRASHED"}
        and not launched_after_baseline
    ):
        baseline_suppressed = True

    sessions[key] = {
        "task_slug": observation.launch.task_slug,
        "task_id": observation.task_id,
        "tool": observation.launch.tool,
        "pid": observation.launch.pid,
        "session_id": observation.launch.session_id,
        "process_start_utc": iso_utc(observation.launch.process_start_utc),
        "first_seen_at": entry.get("first_seen_at") or iso_utc(now),
        "last_seen_at": iso_utc(now),
        "last_state": observation.state,
        "ever_healthy": ever_healthy,
        "managed_for_alerts": managed_for_alerts,
        "alertable": alertable,
        "baseline_suppressed": baseline_suppressed,
    }


def clear_episode_state(state: dict[str, Any], observation: Observation) -> None:
    episodes = state.setdefault("episodes", {})
    prefix = session_episode_prefix(observation.launch)
    for key in list(episodes):
        if key.startswith(prefix):
            del episodes[key]


def clear_superseded_task_episodes(state: dict[str, Any], observation: Observation) -> None:
    episodes = state.setdefault("episodes", {})
    task_prefix = f"{observation.launch.task_slug}|"
    current_prefix = session_episode_prefix(observation.launch)
    for key in list(episodes):
        if key.startswith(task_prefix) and not key.startswith(current_prefix):
            del episodes[key]


def session_episode_items(state: dict[str, Any], observation: Observation) -> list[tuple[str, dict[str, Any]]]:
    episodes = state.setdefault("episodes", {})
    prefix = session_episode_prefix(observation.launch)
    items: list[tuple[str, dict[str, Any]]] = []
    for key, value in episodes.items():
        if key.startswith(prefix) and isinstance(value, dict):
            items.append((key, value))
    return items


def episode_entry(state: dict[str, Any], observation: Observation) -> dict[str, Any]:
    episodes = state.setdefault("episodes", {})
    entry = episodes.get(observation.episode_key)
    return entry if isinstance(entry, dict) else {}


def has_real_progress_since_episode(
    observation: Observation,
    entry: dict[str, Any],
    config: dict[str, Any],
) -> bool:
    if observation.state == "DONE" or observation.done_path:
        return True

    transcript_baseline = parse_datetime(entry.get("last_transcript_mtime"))
    if observation.transcript_mtime and transcript_baseline:
        if observation.transcript_mtime > transcript_baseline + dt.timedelta(seconds=1):
            return True
    elif observation.transcript_mtime and not transcript_baseline:
        return True

    task_mtime = observation.heartbeat_mtime
    if not task_mtime:
        return False

    note_at = parse_datetime(entry.get("last_watchdog_note_at"))
    checked_at = parse_datetime(entry.get("last_checked_at"))
    alerted_at = parse_datetime(entry.get("first_alerted_at"))
    cutoff = note_at or checked_at or alerted_at
    if not cutoff:
        return False

    grace = float(config.get("watchdog_note_grace_seconds", 60))
    return task_mtime > cutoff + dt.timedelta(seconds=grace)


def keep_active_episode_without_real_progress(
    state: dict[str, Any],
    observation: Observation,
    config: dict[str, Any],
) -> None:
    if observation.state != "HEALTHY":
        return
    for key, entry in session_episode_items(state, observation):
        prior_state = str(entry.get("state") or "")
        if prior_state not in {"STALLED", "CRASHED"}:
            continue
        if has_real_progress_since_episode(observation, entry, config):
            continue
        observation.state = prior_state
        observation.episode_key = key
        observation.actions.append("watchdog-heartbeat-ignored")
        return


def episode_fully_handled(
    state: dict[str, Any],
    observation: Observation,
    *,
    notes_enabled: bool,
    email_enabled: bool,
    config: dict[str, Any],
) -> bool:
    entry = episode_entry(state, observation)
    if not entry:
        return False
    needs_note = notes_enabled and bool(observation.task_id)
    needs_email = email_enabled and bool(config["email"].get("enabled", True))
    note_done = bool(entry.get("task_note_sent")) or not needs_note
    email_done = bool(entry.get("email_sent")) or not needs_email
    return note_done and email_done


def touch_episode_checked(state: dict[str, Any], observation: Observation, now: dt.datetime) -> None:
    entry = episode_entry(state, observation)
    if entry:
        entry["last_checked_at"] = iso_utc(now)


def remember_alert(
    state: dict[str, Any],
    observation: Observation,
    now: dt.datetime,
    *,
    task_note_sent: bool,
    email_sent: bool,
) -> None:
    episodes = state.setdefault("episodes", {})
    previous = episode_entry(state, observation)
    episodes[observation.episode_key] = {
        "task_slug": observation.launch.task_slug,
        "task_id": observation.task_id,
        "state": observation.state,
        "first_alerted_at": previous.get("first_alerted_at") or iso_utc(now),
        "last_checked_at": iso_utc(now),
        "last_watchdog_note_at": iso_utc(now) if task_note_sent else previous.get("last_watchdog_note_at", ""),
        "last_transcript_mtime": iso_utc(observation.transcript_mtime),
        "last_task_mtime": iso_utc(observation.heartbeat_mtime),
        "pid": observation.launch.pid,
        "session_id": observation.launch.session_id,
        "process_start_utc": iso_utc(observation.launch.process_start_utc),
        "task_note_sent": bool(previous.get("task_note_sent")) or task_note_sent,
        "email_sent": bool(previous.get("email_sent")) or email_sent,
    }


def build_alert_note(observation: Observation) -> str:
    transcript_path = str(observation.transcript.path) if observation.transcript else "(not found)"
    task_path = str(observation.task_path) if observation.task_path else "(not found)"
    last_lines = "\n".join(f"- {line}" for line in observation.last_transcript_lines if line)
    if not last_lines:
        last_lines = "- (no transcript lines available)"
    return (
        f"State: {observation.state}\n"
        f"Task: {observation.launch.task_slug}\n"
        f"Tool: {observation.launch.tool}\n"
        f"PID: {observation.launch.pid} ({observation.process.reason})\n"
        f"ProcessStartTimeUtc: {iso_utc(observation.launch.process_start_utc)}\n"
        f"Transcript: {transcript_path}\n"
        f"Transcript idle: {minutes_text(observation.transcript_idle_seconds)}\n"
        f"Ticket: {task_path}\n"
        f"Heartbeat idle: {minutes_text(observation.heartbeat_idle_seconds)}\n"
        f"Last transcript lines:\n{last_lines}"
    )


def build_email_body(observation: Observation) -> str:
    return build_alert_note(observation)


def send_email(config: dict[str, Any], subject: str, body: str) -> None:
    email_config = config["email"]
    message = EmailMessage()
    message["From"] = str(email_config["from"])
    message["To"] = str(email_config["to"])
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(
        str(email_config["smtp_host"]),
        int(email_config["smtp_port"]),
        timeout=float(email_config["smtp_timeout_seconds"]),
    ) as smtp:
        smtp.ehlo()
        if bool(email_config.get("smtp_starttls")):
            smtp.starttls()
            smtp.ehlo()
        username = str(email_config.get("username") or "")
        password = str(email_config.get("password") or "")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def handle_alert(
    observation: Observation,
    config: dict[str, Any],
    state: dict[str, Any],
    now: dt.datetime,
    dry_run: bool,
    notes_enabled: bool,
    email_enabled: bool,
) -> None:
    if episode_fully_handled(
        state,
        observation,
        notes_enabled=notes_enabled,
        email_enabled=email_enabled,
        config=config,
    ):
        observation.actions.append("deduped")
        touch_episode_checked(state, observation, now)
        return

    note = build_alert_note(observation)
    if dry_run:
        observation.actions.append("dry-run-alert")
        return

    previous = episode_entry(state, observation)
    task_note_sent = bool(previous.get("task_note_sent"))
    email_sent = bool(previous.get("email_sent"))

    if notes_enabled:
        if task_note_sent:
            observation.actions.append("task-note-deduped")
        elif observation.task_id:
            try:
                client = TasksMcpClient(str(config["mcp_url"]), float(config["mcp_timeout_seconds"]))
                client.append_task_note(observation.task_id, note)
                observation.actions.append("task-note")
                task_note_sent = True
            except Exception as exc:
                observation.errors.append(f"task-note-failed: {exc}")
        else:
            observation.errors.append("task-note-skipped: no task id in slug")

    if email_enabled and bool(config["email"].get("enabled", True)):
        if email_sent:
            observation.actions.append("email-deduped")
        else:
            subject = f"{config['email']['subject_prefix']}: {observation.launch.task_slug} {observation.state}"
            try:
                send_email(config, subject, build_email_body(observation))
                observation.actions.append("email")
                email_sent = True
            except Exception as exc:
                observation.errors.append(f"email-failed: {exc}")

    if task_note_sent or email_sent or observation.errors:
        remember_alert(
            state,
            observation,
            now,
            task_note_sent=task_note_sent,
            email_sent=email_sent,
        )


def run_cycle(
    config: dict[str, Any],
    *,
    dry_run: bool,
    alerts_enabled: bool,
    notes_enabled: bool,
    email_enabled: bool,
    include_existing: bool = False,
    include_unmanaged: bool = False,
) -> list[Observation]:
    now = dt.datetime.now(UTC)
    state_path = Path(str(config["state_file"]))
    state = load_state(state_path)
    baseline_started_this_cycle = False
    if not dry_run:
        baseline_started_this_cycle = ensure_baseline_initialized(state, now)
    observations = [observe(info, config, now) for info in iter_launch_infos(config)]

    for observation in observations:
        clear_superseded_task_episodes(state, observation)
        keep_active_episode_without_real_progress(state, observation, config)
        if observation.state in {"HEALTHY", "DONE"}:
            clear_episode_state(state, observation)
        elif alerts_enabled and observation.state in {"STALLED", "CRASHED"}:
            managed_for_alerts = launch_is_managed_for_alerts(observation.launch, config)
            if not managed_for_alerts and not include_unmanaged:
                observation.actions.append("unmanaged-suppressed")
            elif include_existing or session_is_alertable(state, observation, config):
                handle_alert(observation, config, state, now, dry_run, notes_enabled, email_enabled)
            else:
                observation.actions.append("baseline-suppressed")
        if not dry_run:
            update_session_state(
                state,
                observation,
                now,
                baseline_started_this_cycle=baseline_started_this_cycle,
                config=config,
            )

    if not dry_run:
        save_state(state_path, state)
    return observations


def observation_to_dict(observation: Observation) -> dict[str, Any]:
    return {
        "task_slug": observation.launch.task_slug,
        "task_id": observation.task_id,
        "tool": observation.launch.tool,
        "pid": observation.launch.pid,
        "zone": observation.launch.zone,
        "session_id": observation.launch.session_id,
        "state": observation.state,
        "process_alive": observation.process.alive,
        "process_reason": observation.process.reason,
        "transcript": str(observation.transcript.path) if observation.transcript else None,
        "transcript_reason": observation.transcript.reason if observation.transcript else None,
        "transcript_idle_seconds": observation.transcript_idle_seconds,
        "heartbeat_idle_seconds": observation.heartbeat_idle_seconds,
        "task_path": str(observation.task_path) if observation.task_path else None,
        "done_path": str(observation.done_path) if observation.done_path else None,
        "actions": observation.actions,
        "errors": observation.errors,
    }


def print_observations(observations: list[Observation]) -> None:
    if not observations:
        print("No active launch-info files.")
        return
    for obs in observations:
        transcript = str(obs.transcript.path) if obs.transcript else "(no transcript)"
        action_text = f" actions={','.join(obs.actions)}" if obs.actions else ""
        error_text = f" errors={'; '.join(obs.errors)}" if obs.errors else ""
        print(
            f"{obs.state:<7} {obs.launch.task_slug} "
            f"tool={obs.launch.tool} pid={obs.launch.pid} "
            f"process={obs.process.reason} "
            f"transcript_idle={minutes_text(obs.transcript_idle_seconds)} "
            f"heartbeat_idle={minutes_text(obs.heartbeat_idle_seconds)} "
            f"transcript={transcript}{action_text}{error_text}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alert-only watchdog for launcher-managed agent sessions.")
    parser.add_argument("command", nargs="?", choices=["once", "loop", "status"], default="once")
    parser.add_argument("--config", type=Path, help="Path to watchdog config JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Do not send email, append task notes, or update state.")
    parser.add_argument("--json", action="store_true", help="Emit JSON observations.")
    parser.add_argument("--no-email", action="store_true", help="Do not send alert email.")
    parser.add_argument("--no-note", action="store_true", help="Do not append alert notes through tasks MCP.")
    parser.add_argument("--include-existing", action="store_true", help="Alert on currently stale/crashed sessions even if they predate the service baseline.")
    parser.add_argument("--include-unmanaged", action="store_true", help="Alert on stale/crashed sessions that do not have runner-managed launch-info metadata.")
    parser.add_argument("--stall-seconds", type=int, help="Override transcript stall threshold.")
    parser.add_argument("--heartbeat-seconds", type=int, help="Override ticket heartbeat threshold.")
    parser.add_argument("--poll-seconds", type=int, help="Override loop poll interval.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    if args.stall_seconds is not None:
        config["stall_seconds"] = args.stall_seconds
    if args.heartbeat_seconds is not None:
        config["heartbeat_seconds"] = args.heartbeat_seconds
    if args.poll_seconds is not None:
        config["poll_seconds"] = args.poll_seconds

    dry_run = bool(args.dry_run or args.command == "status")
    alerts_enabled = args.command != "status"
    notes_enabled = not args.no_note
    email_enabled = not args.no_email

    if args.command == "loop":
        while True:
            observations = run_cycle(
                config,
                dry_run=dry_run,
                alerts_enabled=alerts_enabled,
                notes_enabled=notes_enabled,
                email_enabled=email_enabled,
                include_existing=args.include_existing,
                include_unmanaged=args.include_unmanaged,
            )
            if args.json:
                print(json.dumps([observation_to_dict(obs) for obs in observations], indent=2))
            else:
                print_observations(observations)
            time.sleep(float(config["poll_seconds"]))

    observations = run_cycle(
        config,
        dry_run=dry_run,
        alerts_enabled=alerts_enabled,
        notes_enabled=notes_enabled,
        email_enabled=email_enabled,
        include_existing=args.include_existing,
        include_unmanaged=args.include_unmanaged,
    )
    if args.json:
        print(json.dumps([observation_to_dict(obs) for obs in observations], indent=2))
    else:
        print_observations(observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
