"""Auto-unstick nudger — per-tool rules over a managed session's console.

The dominant overnight failure is a worker parked on a limit prompt waiting for a
human. This module reads the session's console (via the dumb ``nudge_console.ps1``
primitive) and classifies it against per-tool rules:

- **Claude** stalls have a clean keystroke revival, so its rules ``inject``:
  rate-limit -> dismiss + ``continue``; context/compaction -> ``/compact``.
- **Codex** has no equivalent revival (its usage-limit banner points at upgrading;
  its context-full says "start a new thread"), and it auto-retries transient
  errors itself. So its rules ``alert``: we detect the wedged state precisely and
  tell the caller a human is needed — we never blind-inject into Codex.

Safety: classification runs only on a confirmed prompt match, the caller bounds
probe frequency with a cooldown, and ``max_run_seconds`` is the backstop — no
nudge storm, and a healthy/ambiguous session is never touched. Rules are
data: ``config["nudge_rules"]`` can extend or override any tool (e.g. to add a
Codex recovery keystroke once a real one is observed).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

_HELPER = Path(__file__).resolve().parent / "nudge_console.ps1"

# Each rule: name, "any" (>=1 must appear), optional "all_any" (>=1 of these too),
# action "inject"|"alert", and for inject the "keys" to type in order ("" = Enter).
# Patterns are case-insensitive substrings/regexes matched against the console tail.
BUILTIN_RULES: dict[str, list[dict]] = {
    "claude": [
        {
            "name": "rate-limit",
            "any": [r"you've hit your limit", r"usage limit"],
            "all_any": [r"what do you want to do\?", r"/rate-limit-options",
                        r"upgrade to increase your usage limit", r"resets\s+\d{1,2}:\d{2}"],
            "action": "inject",
            "keys": ["", "continue"],
        },
        {
            "name": "context-limit",
            "any": [r"context window", r"compaction", r"compact the conversation"],
            "action": "inject",
            "keys": ["/compact"],
        },
    ],
    # Codex: detect-and-alert only (strings lifted verbatim from codex.exe).
    "codex": [
        {"name": "usage-limit", "any": [r"you've hit your usage limit", r"usage limit reached"], "action": "alert"},
        {"name": "out-of-credits", "any": [r"out of credits", r"workspace credit limit", r"spend cap"], "action": "alert"},
        {"name": "context-full", "any": [r"ran out of room in the model's context window"], "action": "alert"},
    ],
}


@dataclass
class NudgeResult:
    state: str          # rule name | working-or-unknown | attach-failed | skipped-tool | error
    action: str         # continue-sent | compact-sent | alert | inject-failed | none
    verified: bool      # for inject: did the prompt clear afterwards?
    nudged: bool        # did we inject a keystroke?
    needs_human: bool   # detected a stuck state we cannot auto-fix (caller should escalate)


def get_rules(config: dict | None) -> dict[str, list[dict]]:
    rules = {tool: list(rs) for tool, rs in BUILTIN_RULES.items()}
    override = (config or {}).get("nudge_rules")
    if isinstance(override, dict):
        for tool, rs in override.items():
            rules[tool] = rs  # full per-tool replacement
    return rules


def _run_ps(cmd_pid: int, action: str, *, text: str = "", lines: int = 30) -> dict | None:
    powershell = shutil.which("powershell") or "powershell"
    out_file = Path(tempfile.gettempdir()) / f"nightshift-nudge-{uuid.uuid4().hex}.json"
    cmd = [
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(_HELPER), "-CmdPid", str(cmd_pid), "-Action", action,
        "-Lines", str(lines), "-OutFile", str(out_file),
    ]
    if action == "send":
        cmd += ["-Text", text]
    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        raw = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            out_file.unlink()
        except OSError:
            pass
    start = raw.find("{")
    if start == -1:
        return None
    try:
        return json.loads(raw[start:])
    except json.JSONDecodeError:
        return None


def _matches(tail: str, rule: dict) -> bool:
    low = tail.lower()
    if not any(re.search(p, low) for p in rule["any"]):
        return False
    if rule.get("all_any") and not any(re.search(p, low) for p in rule["all_any"]):
        return False
    return True


def classify(tail: str, tool_rules: list[dict]) -> dict | None:
    for rule in tool_rules:
        if _matches(tail, rule):
            return rule
    return None


def probe_and_nudge(
    cmd_pid: int, *, tool: str = "claude", apply: bool = True, lines: int = 30, config: dict | None = None
) -> NudgeResult:
    """Probe a session's console; revive it (Claude) or flag it (Codex) if stuck."""
    rules = get_rules(config)
    tool_rules = rules.get(tool)
    if tool_rules is None:
        return NudgeResult("skipped-tool", "none", False, False, False)
    if cmd_pid <= 0:
        return NudgeResult("error", "none", False, False, False)

    read = _run_ps(cmd_pid, "read", lines=lines)
    if not read or not read.get("ok"):
        return NudgeResult("attach-failed", "none", False, False, False)
    tail = str(read.get("tail", ""))

    rule = classify(tail, tool_rules)
    if rule is None:
        return NudgeResult("working-or-unknown", "none", False, False, False)

    name = rule["name"]
    if rule["action"] == "alert":
        return NudgeResult(name, "alert", False, False, True)

    # inject
    if not apply:
        return NudgeResult(name, "none", False, False, False)
    ok = True
    for i, key in enumerate(rule["keys"]):
        if i:
            time.sleep(0.35)
        res = _run_ps(cmd_pid, "send", text=key)
        ok = ok and bool(res and res.get("ok"))
    if not ok:
        return NudgeResult(name, "inject-failed", False, False, False)

    time.sleep(0.7)
    after = _run_ps(cmd_pid, "read", lines=lines)
    verified = bool(after and after.get("ok") and classify(str(after.get("tail", "")), tool_rules) is None)
    action = "compact-sent" if name == "context-limit" else "continue-sent"
    return NudgeResult(name, action, verified, True, False)
