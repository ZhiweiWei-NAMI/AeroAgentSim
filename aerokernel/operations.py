"""State and scheduling proposals; publication authority is kernel-owned."""

from __future__ import annotations

from dataclasses import dataclass

from .ids import EntityRef, FieldKey, ItemRef, LocalCause
from .time import Instant, Interval, Stamp


@dataclass(frozen=True)
class Create:
    """Create a generation under its separately bound lifecycle controller."""

    ref: EntityRef


@dataclass(frozen=True)
class Remove:
    """Remove a generation after all explicit cleanup acknowledgments."""

    ref: EntityRef
    cleanup_refs: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class LifecycleReady:
    """Actual final native cleanup; freezes this participant's later writes."""

    ref: EntityRef


@dataclass(frozen=True)
class FactWrite:
    """Publish a typed measurement or configuration with explicit validity."""

    key: FieldKey
    value: object
    acquired: Stamp
    valid: Interval
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class RetractFact:
    """Make absence win over older overlapping versions."""

    key: FieldKey
    valid: Interval
    reason: str
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class ScheduleTimer:
    """Schedule one owned, never-reused one-shot timer."""

    timer_id: str
    due: Instant
    payload: object


@dataclass(frozen=True)
class CancelTimer:
    """Cancel a pending partition-owned timer."""

    timer_id: str


@dataclass(frozen=True)
class Activate:
    """Explicitly request another declared same-time reaction."""

    partition: str


@dataclass(frozen=True)
class UnsupportedOperation:
    """Explicit extension boundary for milestone 2 contracts."""

    feature: str


class AssertEdge(UnsupportedOperation):
    """Temporal relation assertions are implemented in milestone 2."""


class SampleFrame(UnsupportedOperation):
    """Sample-frame publication is implemented in milestone 2."""
