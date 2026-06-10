"""Mapping of deliverable type -> Verifier instance.

A project can register several Verifiers (e.g. one app, one docs deliverable).
New deliverable types extend the registry without touching the orchestration brain.
"""

from __future__ import annotations

from .base import Increment, Verifier

_REGISTRY: dict[str, Verifier] = {}


def register(verifier: Verifier) -> Verifier:
    """Register a Verifier under its ``deliverable_type``. Returns it for chaining."""
    _REGISTRY[verifier.deliverable_type] = verifier
    return verifier


def get(deliverable_type: str) -> Verifier | None:
    return _REGISTRY.get(deliverable_type)


def known_types() -> list[str]:
    return sorted(_REGISTRY)


def can_verify_unattended(increment: Increment) -> bool:
    """True iff a Verifier exists for the increment's type and it needs no device/human.

    This is the trust boundary: it gates whether the orchestrator may run an
    increment unattended or must route it to the human-gated queue.
    """
    verifier = get(increment.deliverable_type)
    if verifier is None:
        return False
    return verifier.can_verify_unattended(increment)
