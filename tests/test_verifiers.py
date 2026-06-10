"""Tests for the Verifier registry and the trust boundary it derives."""

import sys
import unittest
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nightshift.verifiers import registry  # noqa: E402
from nightshift.verifiers.base import (  # noqa: E402
    Increment,
    Verdict,
    Verifier,
    VerificationResult,
)


class _StubVerifier(Verifier):
    deliverable_type = "stub"

    def verify(self, increment: Increment, *, config: dict[str, Any]) -> VerificationResult:
        return VerificationResult(deliverable_type=self.deliverable_type, verdict=Verdict.PASS)


class RegistryTests(unittest.TestCase):
    def test_register_and_get(self):
        v = registry.register(_StubVerifier())
        self.assertIs(registry.get("stub"), v)
        self.assertIn("stub", registry.known_types())

    def test_unattended_eligible_when_no_device_or_human(self):
        registry.register(_StubVerifier())
        inc = Increment(id="i1", summary="x", deliverable_type="stub")
        self.assertTrue(registry.can_verify_unattended(inc))

    def test_device_requirement_blocks_unattended(self):
        registry.register(_StubVerifier())
        inc = Increment(id="i2", summary="x", deliverable_type="stub", requires=["device"])
        self.assertFalse(registry.can_verify_unattended(inc))

    def test_human_requirement_blocks_unattended(self):
        registry.register(_StubVerifier())
        inc = Increment(id="i3", summary="x", deliverable_type="stub", requires=["human"])
        self.assertFalse(registry.can_verify_unattended(inc))

    def test_unknown_deliverable_type_is_not_unattended_eligible(self):
        inc = Increment(id="i4", summary="x", deliverable_type="does-not-exist")
        self.assertFalse(registry.can_verify_unattended(inc))


if __name__ == "__main__":
    unittest.main()
