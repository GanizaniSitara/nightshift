"""Ready-plan / next-step queue.

Hydrated from the backlog (e.g. a tasks store) plus any explicitly-authored
ready plans. The shift loop pulls candidate steps from here. Conservative by
design: if nothing is ready, the shift may do bounded design/recon to create
the next safe step — it never invents broad new work.
"""

from __future__ import annotations

from .records import Plan


def hydrate_from_backlog(project: str | None = None) -> list[Plan]:
    """Build candidate plans from the backlog. Implemented next.

    The user's steer: next-step selection hydrates from the backlog rather than
    requiring every step to be pre-ticketed before the shift starts.
    """
    raise NotImplementedError("queue.hydrate_from_backlog — implemented next")


def ready_plans() -> list[Plan]:
    """Return plans explicitly marked ready for unattended execution."""
    raise NotImplementedError("queue.ready_plans — implemented next")
