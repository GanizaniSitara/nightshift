"""CLI / library / service Verifier (stub): run tests, smoke-run, parse output.

The simplest deliverable type and fully unattended-eligible: success is a clean
test run plus a smoke invocation, with no screenshot or vision pass required.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .base import Increment, Verifier, VerificationResult


class CliVerifier(Verifier):
    deliverable_type = "cli"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        raise NotImplementedError("CliVerifier.verify — implemented later")


cli_verifier = registry.register(CliVerifier())
