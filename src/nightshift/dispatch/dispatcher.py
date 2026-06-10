"""Launch one managed run and stamp it as managed so the watchdog tracks it.

Reuses the existing platform launcher rather than reimplementing process launch.
After the launcher creates its launch-info record, the dispatcher stamps the
managed markers the watchdog gates on (so the launcher itself stays untouched —
the watchdog reads, never modifies, the launcher's data).
"""

from __future__ import annotations

from ..orchestration.records import Run
from ..verifiers import Increment

#: Markers the watchdog recognises as "this run is managed by Nightshift".
MANAGED_MARKERS = {"RunnerManaged": True, "ManagedBy": "nightshift"}


def dispatch_run(increment: Increment, *, tool: str = "claude", config: dict | None = None) -> Run:
    """Launch a managed run for an increment under the worker contract. Implemented later."""
    raise NotImplementedError("dispatcher.dispatch_run")
