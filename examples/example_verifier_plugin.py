"""Example Nightshift plugin (generic).

Copy this shape into your PRIVATE plugins directory (the one pointed to by
``NIGHTSHIFT_PLUGINS`` or ``config["plugin_paths"]``) for project-specific
verifiers/rubrics you don't want in a public repo. Each plugin self-registers
on import; a no-arg ``register()`` is optional and called after import.
"""

from __future__ import annotations

from typing import Any

from nightshift.verifiers import registry
from nightshift.verifiers.base import Increment, Verdict, Verifier, VerificationResult


class ExampleVerifier(Verifier):
    deliverable_type = "example"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        # A real plugin would build/render the deliverable and judge it here.
        return VerificationResult(deliverable_type=self.deliverable_type, verdict=Verdict.PASS)


def register() -> None:
    registry.register(ExampleVerifier())
