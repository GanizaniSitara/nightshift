"""iOS-app Verifier (stub): xcodebuild build/test, boot simulator, screenshot, vision critique.

Host-specific: runs only where Xcode + the iOS simulator live (macOS). UI/screen
increments are unattended-eligible; anything needing a physical device, the camera,
or accuracy judgement should declare ``requires=["device"]`` / ``["human"]`` and
route to the human-gated queue instead.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .base import Increment, Verifier, VerificationResult


class IosVerifier(Verifier):
    deliverable_type = "ios"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        raise NotImplementedError("IosVerifier.verify — macOS host, implemented later")


ios_verifier = registry.register(IosVerifier())
