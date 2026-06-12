"""Goal definitions: the semantic layer the shift works from.

A goal is a folder (typically in a private goals directory, outside the public
repo) describing what the product should become and the ordered increments that
get it there. Tickets are NOT the steering layer — the shift creates them per
increment as transport and evidence handles.

Layout:

    <goals_dir>/<goal-id>/
      goal.md             frontmatter: goal, title, repo, branch, tool,
                          task_prefix, status; body = the intent (what the
                          product should become)
      increments/
        01-<slug>.md      frontmatter: deliverable, target, rubric, requires,
                          tool (all optional except deliverable); body = the
                          full brief handed to the worker. Ordering = filename.

`requires: device` / `requires: human` marks an increment human-gated; the shift
records it BLOCKED and moves on rather than attempting it blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..verifiers.base import Increment
from .queue import _parse_frontmatter


@dataclass
class GoalSpec:
    id: str
    title: str
    intent: str
    repo: str | None = None
    branch: str | None = None
    tool: str | None = None
    task_prefix: str = "TECH"
    status: str = "draft"
    serve_cmd: str | None = None  # for server-rendered apps: how the verifier restarts it
    path: Path | None = None
    increments: list["IncrementSpec"] = field(default_factory=list)


@dataclass
class IncrementSpec:
    """One ordered step of a goal, as authored (pre-ticket)."""

    slug: str
    order: int
    brief: str
    deliverable_type: str = "cli"
    target: str | None = None
    rubric_path: str | None = None
    requires: list[str] = field(default_factory=list)
    tool: str | None = None

    def to_increment(self, ticket_slug: str, summary: str) -> Increment:
        return Increment(
            id=ticket_slug,
            summary=summary,
            deliverable_type=self.deliverable_type,
            requires=list(self.requires),
            target=self.target,
            rubric_path=self.rubric_path,
        )


def _split_body(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].strip()
    return text.strip()


def _load_increment(path: Path, order: int, goal_dir: Path) -> IncrementSpec:
    text = path.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)
    requires = [r.strip() for r in meta.get("requires", "").split(",") if r.strip()]
    rubric = meta.get("rubric", "").strip() or None
    if rubric and not Path(rubric).is_absolute():
        rubric = str((goal_dir / rubric).resolve())
    return IncrementSpec(
        slug=path.stem,
        order=order,
        brief=_split_body(text),
        deliverable_type=meta.get("deliverable", "cli").strip() or "cli",
        target=meta.get("target", "").strip() or None,
        rubric_path=rubric,
        requires=requires,
        tool=meta.get("tool", "").strip() or None,
    )


def assert_ready(goal: GoalSpec) -> None:
    """The human approval gate: a shift may only execute a goal marked ready.

    The planner writes drafts; a person reviews and flips ``status: ready`` in
    goal.md. Automated planning, manual approval.
    """
    if goal.status != "ready":
        raise PermissionError(
            f"goal '{goal.id}' has status '{goal.status}' — review the draft and set "
            f"'status: ready' in {goal.path / 'goal.md' if goal.path else 'goal.md'} to approve it"
        )


def load_goal(goals_dir: str | Path, goal_id: str) -> GoalSpec:
    goal_dir = Path(goals_dir) / goal_id
    goal_md = goal_dir / "goal.md"
    if not goal_md.is_file():
        raise FileNotFoundError(f"no goal.md at {goal_md}")
    text = goal_md.read_text(encoding="utf-8")
    meta = _parse_frontmatter(text)

    spec = GoalSpec(
        id=meta.get("goal", goal_id),
        title=meta.get("title", goal_id),
        intent=_split_body(text),
        repo=meta.get("repo", "").strip() or None,
        branch=meta.get("branch", "").strip() or None,
        tool=meta.get("tool", "").strip() or None,
        task_prefix=meta.get("task_prefix", "TECH").strip() or "TECH",
        status=meta.get("status", "draft").strip() or "draft",
        serve_cmd=meta.get("serve_cmd", "").strip() or None,
        path=goal_dir,
    )
    inc_dir = goal_dir / "increments"
    if inc_dir.is_dir():
        for order, path in enumerate(sorted(inc_dir.glob("*.md")), start=1):
            spec.increments.append(_load_increment(path, order, goal_dir))
    return spec
