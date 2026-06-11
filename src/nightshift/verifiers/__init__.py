"""Deliverable-type Verifier registry.

The single genuinely new primitive in Nightshift. A Verifier checks a built
increment against its acceptance criteria for one *deliverable type* (web app,
iOS app, PDF, CLI, ...). The trust boundary for unattended work falls out of
the registry: an increment is unattended-eligible iff a Verifier exists for its
deliverable type and the check needs no physical device or human taste.
"""

from .base import Increment, Verdict, Verifier, VerificationResult, VisionFinding
from . import registry

# Import the bundled reference verifiers so they self-register on package import.
# (Imports are cheap: playwright is loaded lazily inside the web capture, not here.)
from . import web, cli, ios, pdf  # noqa: E402,F401

__all__ = [
    "Increment",
    "Verdict",
    "Verifier",
    "VerificationResult",
    "VisionFinding",
    "registry",
]
