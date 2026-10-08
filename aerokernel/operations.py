"""State and scheduling proposals; publication authority is kernel-owned."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .ids import EntityRef, FieldKey, ItemRef, LocalCause
from .time import Cut, Instant, Interval, Stamp


@dataclass(frozen=True)
class Create:
    """Create a generation under its separately bound lifecycle controller."""

    ref: EntityRef
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class Remove:
    """Remove a generation after all explicit cleanup acknowledgments."""

    ref: EntityRef
    cleanup_refs: tuple[ItemRef | LocalCause, ...] = ()
    causes: tuple[ItemRef | LocalCause, ...] = ()


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
    causes: tuple[ItemRef | LocalCause, ...] = ()


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
    """Legacy explicit unsupported-extension sentinel."""

    feature: str


@dataclass(frozen=True)
class AssertEdge:
    """Assert a never-reused transport ID with immutable endpoints."""

    edge_id: str
    relation_id: str
    source: EntityRef
    target: EntityRef
    valid: Interval
    acquired: Stamp
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class CloseEdge:
    """End an already started edge exactly at publication."""

    edge_id: str
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class CancelEdge:
    """Cancel an edge not yet started, including equality at publication."""

    edge_id: str
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class ActivateObligation:
    """Activate a declared minimum only over this explicit interval."""

    obligation_id: str
    relation_id: str
    direction: str
    endpoint_ref: EntityRef
    valid: Interval
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class EndObligation:
    """End a started obligation at publication, never backdating."""

    obligation_id: str
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class CancelObligation:
    """Cancel an obligation whose interval has not started."""

    obligation_id: str
    causes: tuple[ItemRef | LocalCause, ...] = ()


@dataclass(frozen=True)
class SampleFrame:
    """One settled context observation, without a kernel predicate language."""

    context_id: str
    physical_ns: int
    cut: Cut
    bindings: Mapping[str, EntityRef]
    clocks: Mapping[str, tuple[str, str]]
    result: object
    sources: Mapping[str, str]
    causes: tuple[ItemRef | LocalCause, ...] = ()

    def __post_init__(self) -> None:
        for name in ("bindings", "clocks", "sources"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


_NON_OUTPUT_OPERATIONS = (Activate, ScheduleTimer, CancelTimer, UnsupportedOperation)


def is_observable_output(operation: object) -> bool:
    """Guard every publication; only declared scheduling controls are non-output."""
    return not isinstance(operation, _NON_OUTPUT_OPERATIONS)
