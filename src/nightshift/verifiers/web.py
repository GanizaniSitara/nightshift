"""Web-app Verifier: load a page, screenshot it, critique against a rubric.

First real Verifier (the tracer bullet). The plan: drive Playwright to load
``increment.target`` (a URL), capture a screenshot, run any configured
unit/e2e tests, then send the screenshot + the rubric at ``increment.rubric_path``
to a vision model and turn each rubric line into a ``VisionFinding``.

Implementation lands next; the contract and seams are fixed here so the
orchestration brain can depend on them.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .base import Increment, Verdict, Verifier, VerificationResult


class WebVerifier(Verifier):
    deliverable_type = "web"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        # TODO(tracer-bullet): Playwright load -> screenshot -> vision-vs-rubric.
        # Sanity requirement once implemented: a deliberately-broken screen must
        # return FAIL and a good screen PASS — the oracle has to discriminate.
        raise NotImplementedError("WebVerifier.verify — tracer bullet, implemented next")


web_verifier = registry.register(WebVerifier())
