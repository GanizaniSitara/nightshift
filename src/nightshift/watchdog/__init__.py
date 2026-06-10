"""Liveness sensor for managed runs.

Reads the launcher's run records + the agent transcript and classifies each run
HEALTHY / STALLED / CRASHED / DONE with de-duped alerting. Observes and alerts;
does not restart or kill. Implementation migrates here from the existing
service (see README.md in this package).
"""
