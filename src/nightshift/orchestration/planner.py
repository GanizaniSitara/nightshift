"""The planner pass — Nightshift's "Mayor" judgment, run on demand, never a daemon.

Takes a high-level ask, recons the project with a headless agent-CLI session
(read-only tools), and decomposes it into a DRAFT goal folder: intent + ordered
increment briefs with deliverable types, verification targets/rubrics, and
device/human gates. The human reviews the draft and flips ``status: ready``
before ``nightshift shift`` will touch it — planning is automated, approval is
not.

The decomposition is guided by recipes (reusable increment-sequence shapes)
embedded in the prompt; recipe files can replace these later without changing
the contract.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..verifiers.evaluator import _extract_json

RECIPES = """\
- feature: recon/design note -> implement -> targeted tests -> verify deliverable
- polish: capture current state -> rubric-checkable improvements (one screen/surface per increment)
- audit/research: sweep -> synthesize findings (report-only increments; findings are the deliverable)
- fix: reproduce -> fix -> regression test
"""

_PROMPT = """You are the planning pass of an unattended build system. Your ONLY job is to
decompose the ask below into a goal with ordered increments that disposable
single-task agent sessions can execute overnight without a human present.
You may read the project to ground the plan; change nothing.

## The ask

{ask}

## Project

Repository / working directory: {repo}
Recon it (read README, layout, key files) before planning.

## Decomposition rules

- At most {max_increments} increments, strictly ordered; each must be completable
  by one agent session in well under an hour, and each later increment may build
  on earlier ones (they execute sequentially on one shared branch).
- Recipes to shape the sequence:
{recipes}
- Every increment gets a precise, self-contained brief: exact files/areas, the
  definition of done, and the commands to run to check it. Write briefs for a
  cold reader with no other context.
- Verification: if an increment's result can be checked by loading a URL and
  judging a screenshot against a rubric, set deliverable "web", a target URL,
  and 3-6 concrete rubric_lines. If it is code checkable by tests/smoke-run,
  use deliverable "cli". If it genuinely needs a physical device or human
  taste to judge, add "device" or "human" to requires — such increments are
  routed to the human instead of attempted blind.
- CRITICAL for web targets: the target URL must VISIBLY EXERCISE the change in
  one shot. The verifier loads it and screenshots the rendered page — it cannot
  type, click, scroll, or log in. Do NOT point at the bare app root if the
  feature only appears after an action. For a search/results feature use a query
  URL that returns results (e.g. .../?q=<term>&limit=10); for a detail/record
  feature use a deep link to a specific record if the repo lets you form one.
  rubric_lines must describe what is visible in THAT rendered state.
- Be conservative: do not invent work beyond the ask; prefer fewer, sharper
  increments.

## Output

Output ONLY one JSON object, no prose, exactly this shape:
{{"goal": {{"id": "<kebab-case>", "title": "...", "branch": "goal/<kebab>",
  "intent": "<2-5 sentences: what the product should become and why>"}},
 "increments": [{{"slug": "<kebab-case>", "deliverable": "cli|web",
  "target": null, "requires": [], "rubric_lines": [],
  "brief": "<the full brief text>"}}]}}
"""


def build_planner_prompt(ask: str, repo: str, *, max_increments: int = 5) -> str:
    return _PROMPT.format(ask=ask.strip(), repo=repo, max_increments=max_increments, recipes=RECIPES)


def run_planner(
    ask: str,
    repo: str,
    *,
    config: dict[str, Any],
    max_increments: int = 5,
    timeout: int = 600,
) -> dict[str, Any]:
    """Run the headless planner session and return the parsed plan dict."""
    tool = str(config.get("plan_tool", config.get("eval_tool", "claude")))
    model = str(config.get("plan_model", config.get("eval_model", "sonnet")))
    binary = shutil.which(tool) or tool
    cmd = [
        binary, "-p",
        "--safe-mode",
        "--output-format", "json",
        "--allowedTools", "Read,Glob,Grep",
        "--add-dir", repo,
        "--model", model,
    ]
    prompt = build_planner_prompt(ask, repo, max_increments=max_increments)
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"planner failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}")
    stdout = proc.stdout.strip()
    try:
        envelope = json.loads(stdout)
        inner = envelope.get("result", envelope)
    except json.JSONDecodeError:
        inner = stdout
    if isinstance(inner, dict) and "goal" in inner:
        return inner
    return _extract_json(inner if isinstance(inner, str) else json.dumps(inner))


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "increment"


def write_goal_draft(
    plan: dict[str, Any],
    goals_dir: str | Path,
    *,
    repo: str,
    task_prefix: str = "TECH",
    tool: str = "claude",
) -> Path:
    """Materialize a plan dict as a DRAFT goal folder. Errors if the goal exists."""
    goal = plan["goal"]
    goal_id = _slug(str(goal["id"]))
    goal_dir = Path(goals_dir) / goal_id
    if goal_dir.exists():
        raise FileExistsError(f"goal already exists: {goal_dir}")
    inc_dir = goal_dir / "increments"
    inc_dir.mkdir(parents=True)

    branch = str(goal.get("branch") or f"goal/{goal_id}")
    (goal_dir / "goal.md").write_text(
        "---\n"
        f"goal: {goal_id}\n"
        f"title: {goal.get('title', goal_id)}\n"
        f"repo: {repo}\n"
        f"branch: {branch}\n"
        f"tool: {tool}\n"
        f"task_prefix: {task_prefix}\n"
        "status: draft\n"
        "---\n\n"
        f"{str(goal.get('intent', '')).strip()}\n",
        encoding="utf-8",
    )

    for order, inc in enumerate(plan.get("increments", []), start=1):
        slug = _slug(str(inc.get("slug", f"step-{order}")))
        rubric_rel = ""
        rubric_lines = [str(r) for r in inc.get("rubric_lines") or []]
        if rubric_lines:
            rubric_dir = goal_dir / "rubrics"
            rubric_dir.mkdir(exist_ok=True)
            (rubric_dir / f"{slug}.md").write_text(
                "\n".join(f"- {line}" for line in rubric_lines) + "\n", encoding="utf-8"
            )
            rubric_rel = f"rubrics/{slug}.md"

        meta = [
            "---",
            f"deliverable: {inc.get('deliverable', 'cli')}",
        ]
        if inc.get("target"):
            meta.append(f"target: {inc['target']}")
        if rubric_rel:
            meta.append(f"rubric: {rubric_rel}")
        requires = [str(r) for r in inc.get("requires") or []]
        if requires:
            meta.append(f"requires: {', '.join(requires)}")
        meta.append("---")
        body = str(inc.get("brief", "")).strip()
        (inc_dir / f"{order:02d}-{slug}.md").write_text(
            "\n".join(meta) + "\n\n" + body + "\n", encoding="utf-8"
        )
    return goal_dir
