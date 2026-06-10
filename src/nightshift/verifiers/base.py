"""Core Verifier contract: ``Increment`` in, ``VerificationResult`` out."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_HUMAN = "needs-human"  # cannot be judged unattended (taste, device, ambiguity)


@dataclass
class VisionFinding:
    """One vision-model judgement of a screenshot/render against a single rubric line."""

    rubric_item: str
    verdict: Verdict
    notes: str = ""


@dataclass
class Increment:
    """A unit of work the orchestrator hands to a worker and a Verifier checks.

    ``requires`` declares what the increment cannot be done/verified without.
    Anything containing ``"device"`` or ``"human"`` is never unattended-eligible
    and routes to the human-gated queue.
    """

    id: str
    summary: str
    deliverable_type: str
    acceptance_criteria: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    rubric_path: str | None = None
    target: str | None = None  # URL, scheme, file path — interpreted by the Verifier


@dataclass
class VerificationResult:
    """Structured, evidence-bearing outcome of a verification pass."""

    deliverable_type: str
    verdict: Verdict
    built: bool = False
    tests_ran: bool = False
    tests_passed: int = 0
    tests_failed: int = 0
    screenshots: list[str] = field(default_factory=list)
    vision_findings: list[VisionFinding] = field(default_factory=list)
    evidence_paths: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == Verdict.PASS


class Verifier(abc.ABC):
    """Checks a built increment against its acceptance criteria for one deliverable type."""

    #: The deliverable type this Verifier handles, e.g. ``"web"``, ``"ios"``, ``"pdf"``.
    deliverable_type: str = "base"

    @abc.abstractmethod
    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        """Build/render the deliverable and judge it. Must be side-effect-safe to re-run."""
        raise NotImplementedError

    def can_verify_unattended(self, increment: Increment) -> bool:
        """An increment needing a device or human judgement is never unattended-eligible."""
        blocked = {"device", "human"}
        return not (blocked & set(increment.requires))
