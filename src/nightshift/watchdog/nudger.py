"""Auto-unstick nudger — revive a managed session parked on a recoverable prompt.

The headline overnight failure is the subscription rate-limit pause: the session
sits at a prompt forever waiting for a human. This nudger, driven by the
in-session monitor, peeks the session's console and:

- rate-limit prompt  -> dismiss + type "continue"  (the "just say continue" case)
- context/compaction -> send "/compact"
- working / no prompt -> nothing (never inject into a healthy or ambiguous session)

Safety against a Gas-Town-style nudge storm: the caller tracks attempts and a
cooldown per run; this module just performs one probe-and-maybe-act and reports
what it saw. Claude-only for now (the prompts are Claude Code's); other tools
return ``skipped-tool`` so the caller falls through to alerting.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

_HELPER = Path(__file__).resolve().parent / "nudge_console.ps1"


@dataclass
class NudgeResult:
    state: str          # rate-limit | context-limit | working-or-unknown | attach-failed | skipped-tool | error
    action: str         # continue-sent | compact-sent | inject-failed | none
    verified: bool      # did the prompt clear after injecting?
    nudged: bool        # did we actually inject anything?

    @property
    def recoverable(self) -> bool:
        return self.state in {"rate-limit", "context-limit"}


def probe_and_nudge(cmd_pid: int, *, tool: str = "claude", apply: bool = True, lines: int = 30) -> NudgeResult:
    """Probe a session's console and, if it's on a recoverable prompt, nudge it."""
    if tool != "claude":
        return NudgeResult("skipped-tool", "none", False, False)
    if cmd_pid <= 0:
        return NudgeResult("error", "none", False, False)

    powershell = shutil.which("powershell") or "powershell"
    out_file = Path(tempfile.gettempdir()) / f"nightshift-nudge-{uuid.uuid4().hex}.json"
    cmd = [
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(_HELPER), "-CmdPid", str(cmd_pid), "-Lines", str(lines),
        "-OutFile", str(out_file),
    ]
    if apply:
        cmd.append("-Apply")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        raw = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
    except (subprocess.TimeoutExpired, OSError) as exc:
        return NudgeResult("error", f"helper-failed: {type(exc).__name__}", False, False)
    finally:
        try:
            out_file.unlink()
        except OSError:
            pass

    start = raw.find("{")
    if start == -1:
        return NudgeResult("error", f"no-json (rc={proc.returncode}): {(proc.stderr or '').strip()[:150]}", False, False)
    try:
        data = json.loads(raw[start:])
    except json.JSONDecodeError:
        return NudgeResult("error", "bad-json", False, False)

    state = str(data.get("state", "error"))
    action = str(data.get("action", "none"))
    return NudgeResult(
        state=state,
        action=action,
        verified=bool(data.get("verified", False)),
        nudged=action in {"continue-sent", "compact-sent"},
    )
