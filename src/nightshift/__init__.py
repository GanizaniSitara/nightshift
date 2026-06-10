"""Nightshift — turn unattended AI-agent capacity into verified, incremental progress.

The host-agnostic orchestration brain lives in ``nightshift.orchestration``; the
host-specific runner ("the hands") lives in ``nightshift.dispatch`` and
``nightshift.watchdog``. Verification is pluggable per deliverable type in
``nightshift.verifiers``.
"""

__version__ = "0.1.0"
