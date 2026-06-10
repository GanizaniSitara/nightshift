"""PDF-document Verifier (stub): render pages to images, critique against a rubric.

For document/curriculum deliverables. Render the generated PDF pages, send each
page image + the rubric to a vision model, and judge layout/quality/correctness.
Note: final visual sign-off for taste-sensitive documents may stay
``requires=["human"]`` — the vision pass raises the floor, not the ceiling.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .base import Increment, Verifier, VerificationResult


class PdfVerifier(Verifier):
    deliverable_type = "pdf"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        raise NotImplementedError("PdfVerifier.verify — implemented later")


pdf_verifier = registry.register(PdfVerifier())
