"""Evaluate a screenshot against a rubric by shelling out to a coding-agent CLI.

Subscription-driven, no API key, no SDK, no separate vision model: the same way
Nightshift drives every agent. A screenshot goes in; a structured pass/fail
verdict comes back. Default tool is the `claude` CLI in headless print mode;
`codex` is a secondary option.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import Verdict, VisionFinding

#: JSON Schema handed to `claude --json-schema` to constrain the structured output.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "needs-human"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rubric_item": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["pass", "fail", "needs-human"]},
                    "notes": {"type": "string"},
                },
                "required": ["rubric_item", "verdict"],
            },
        },
    },
    "required": ["verdict", "findings"],
}

_PROMPT = """You are a strict UI verifier. Read the screenshot image file at this absolute path:
{image}

Evaluate the screenshot against this rubric. Each non-empty, non-heading line is one check:
---
{rubric}
---
Also consider these acceptance criteria:
{criteria}

For each rubric check, decide "pass", "fail", or "needs-human". Use "needs-human" ONLY when you
genuinely cannot judge the item from this single screenshot (e.g. it concerns another screen or a
console). Then give an overall verdict:
- "fail" if any check you CAN judge fails,
- otherwise "pass".
A "needs-human" item is recorded in findings for later human review but does NOT block: it must not
make the overall verdict "fail". Never return "needs-human" as the overall verdict.

Output ONLY a single JSON object, no prose, of the form:
{{"verdict":"pass|fail|needs-human","findings":[{{"rubric_item":"...","verdict":"pass|fail|needs-human","notes":"..."}}]}}
"""


def _coerce_verdict(value: object) -> Verdict:
    try:
        return Verdict(str(value).strip().lower())
    except ValueError:
        return Verdict.NEEDS_HUMAN


def _extract_json(text: str) -> dict:
    """Find and parse the first balanced JSON object in ``text``."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise ValueError("no JSON object found in evaluator output")


def _parse_output(stdout: str, tool: str) -> dict:
    """Pull our verdict dict out of the CLI's stdout."""
    stdout = stdout.strip()
    if not stdout:
        raise ValueError("empty evaluator output")
    if tool == "claude":
        # `--output-format json` wraps the run in an envelope. With `--json-schema`
        # the validated object is in `structured_output`; otherwise it's the text
        # in `result`. Prefer the structured field, then fall back to the text.
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError:
            return _extract_json(stdout)
        structured = envelope.get("structured_output")
        if isinstance(structured, dict) and "verdict" in structured:
            return structured
        inner = envelope.get("result", envelope)
        if isinstance(inner, dict) and "verdict" in inner:
            return inner
        return _extract_json(inner if isinstance(inner, str) else json.dumps(inner))
    return _extract_json(stdout)


def _build_command(tool: str, image_dir: str, model: str) -> list[str]:
    """Build the CLI argv. The prompt is sent via stdin (not argv): a long,
    multiline, brace-laden prompt gets mangled passing through the Windows
    ``claude.CMD`` -> cmd.exe layer as an argument."""
    binary = shutil.which(tool) or tool
    if tool == "claude":
        return [
            binary, "-p",
            "--safe-mode",
            "--output-format", "json",
            "--allowedTools", "Read",
            "--add-dir", image_dir,
            "--model", model,
        ]
    if tool == "codex":
        return [binary, "exec"]
    raise ValueError(f"unknown evaluator tool: {tool}")


def evaluate_screenshot(
    image_path: str | Path,
    rubric_text: str,
    criteria: list[str] | None = None,
    *,
    tool: str = "claude",
    model: str = "sonnet",
    timeout: int = 300,
) -> tuple[Verdict, list[VisionFinding], str]:
    """Return (overall verdict, per-rubric findings, raw stdout) for a screenshot."""
    image = str(Path(image_path).resolve())
    criteria_text = "\n".join(f"- {c}" for c in (criteria or [])) or "(none)"
    prompt = _PROMPT.format(image=image, rubric=rubric_text.strip(), criteria=criteria_text)
    cmd = _build_command(tool, str(Path(image).parent), model)

    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"{tool} eval failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")

    data = _parse_output(proc.stdout, tool)
    findings = [
        VisionFinding(
            rubric_item=str(f.get("rubric_item", "")),
            verdict=_coerce_verdict(f.get("verdict")),
            notes=str(f.get("notes", "")),
        )
        for f in data.get("findings", [])
    ]
    return _coerce_verdict(data.get("verdict")), findings, proc.stdout
