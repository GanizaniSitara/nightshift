"""Ready-plan / next-step queue.

Plans are markdown files with a small frontmatter block. The shift loop pulls
candidate plans from here; the backlog is one possible source, hydrated into
not-yet-ready candidates. Conservative: nothing here invents work.
"""

from __future__ import annotations

from pathlib import Path

from .records import Plan


def _parse_frontmatter(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
    return meta


def _plan_from_file(path: Path) -> Plan:
    meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
    increments = [s.strip() for s in meta.get("increments", "").split(",") if s.strip()]
    ready = meta.get("ready", "").lower() in {"1", "true", "yes"}
    return Plan(
        id=meta.get("id", path.stem),
        goal_id=meta.get("goal_id", ""),
        summary=meta.get("summary", path.stem),
        increments=increments,
        ready=ready,
    )


def load_plans(queue_dir: str | Path) -> list[Plan]:
    directory = Path(queue_dir)
    if not directory.is_dir():
        return []
    return [_plan_from_file(p) for p in sorted(directory.glob("*.md"))]


def ready_plans(queue_dir: str | Path) -> list[Plan]:
    """Plans explicitly marked ready for unattended execution."""
    return [p for p in load_plans(queue_dir) if p.ready]


def hydrate_from_backlog(backlog_dir: str | Path, project: str | None = None) -> list[Plan]:
    """Build (not-yet-ready) candidate plans from a backlog directory of markdown.

    The user's steer: next-step selection hydrates from the backlog rather than
    requiring every step to be pre-ticketed before the shift starts.
    """
    plans = load_plans(backlog_dir)
    if project:
        plans = [p for p in plans if p.goal_id == project or project in p.id]
    return plans
