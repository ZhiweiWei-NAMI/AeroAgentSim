"""Unbounded integer simulation time and pinned rational clock conversion."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .errors import KernelError


def integer(value: int, name: str, minimum: int | None = None) -> int:
    """Require an actual int, rejecting bool and scalar coercions."""
    if type(value) is not int or (minimum is not None and value < minimum):
        raise KernelError("TIME_INTEGER", f"invalid integer {name}")
    return value


@dataclass(frozen=True, order=True)
class Instant:
    """Canonical elapsed nanoseconds and reactive wave index."""

    ns: int
    microstep: int = 0

    def __post_init__(self) -> None:
        integer(self.ns, "ns", 0)
        integer(self.microstep, "microstep", 0)


@dataclass(frozen=True)
class Cut:
    """Immutable prefix coordinates; the store checks issued index/Instant pairs."""

    index: int
    instant: Instant

    def __post_init__(self) -> None:
        integer(self.index, "cut index", 0)
        if not isinstance(self.instant, Instant):
            raise KernelError("CUT", "cut requires an Instant")


@dataclass(frozen=True)
class Interval:
    """An explicit half-open validity interval, optionally unbounded."""

    start: Instant
    end: Instant | None

    def __post_init__(self) -> None:
        if not isinstance(self.start, Instant) or (
            self.end is not None
            and (not isinstance(self.end, Instant) or self.end <= self.start)
        ):
            raise KernelError("INTERVAL", "nonempty half-open interval required")

    def contains(self, at: Instant) -> bool:
        """Whether validity includes the supplied Instant."""
        return self.start <= at and (self.end is None or at < self.end)

    def intersects(self, start: Instant, end: Instant) -> bool:
        """Whether a nonempty query interval intersects this validity interval."""
        Interval(start, end)
        return self.start < end and (self.end is None or start < self.end)


@dataclass(frozen=True)
class Stamp:
    """Lossless rational source time plus pinned mapping identity."""

    clock_id: str
    numerator: int
    denominator: int
    mapping_id: str

    def __post_init__(self) -> None:
        from .ids import validate_text

        validate_text(self.clock_id)
        validate_text(self.mapping_id)
        integer(self.numerator, "numerator")
        integer(self.denominator, "denominator", 1)


@dataclass(frozen=True)
class ClockMapping:
    """Positive rational scale and explicit rounding to canonical nanoseconds."""

    mapping_id: str
    clock_id: str
    offset_ns: int = 0
    p: int = 1
    q: int = 1
    rounding: str = "exact"

    def __post_init__(self) -> None:
        from .ids import validate_text

        validate_text(self.mapping_id)
        validate_text(self.clock_id)
        integer(self.offset_ns, "offset")
        integer(self.p, "scale numerator", 1)
        integer(self.q, "scale denominator", 1)
        if self.rounding not in {"exact", "floor", "ceil", "nearest_ties_even"}:
            raise KernelError("CLOCK_ROUNDING", "unknown rounding policy")

    def map(self, stamp: Stamp) -> int:
        """Convert rationally; acquisition may map before the run's origin."""
        if (stamp.clock_id, stamp.mapping_id) != (self.clock_id, self.mapping_id):
            raise KernelError("CLOCK_MAPPING", "stamp does not match pinned mapping")
        value = self.offset_ns + Fraction(self.p, self.q) * Fraction(
            stamp.numerator, stamp.denominator
        )
        if self.rounding == "exact":
            if value.denominator != 1:
                raise KernelError("CLOCK_INEXACT", "exact mapping is nonintegral")
            return value.numerator
        if self.rounding == "floor":
            return value.numerator // value.denominator
        if self.rounding == "ceil":
            return -(-value.numerator // value.denominator)
        return round(value)


def instant_data(instant: Instant) -> list[int]:
    """Portable canonical time coordinates."""
    return [instant.ns, instant.microstep]


def instant_from(data: Any) -> Instant:
    """Decode exactly two integer coordinates."""
    if not isinstance(data, (list, tuple)) or len(data) != 2:
        raise KernelError("INSTANT_DATA", "expected two coordinates")
    return Instant(data[0], data[1])


def cut_data(cut: Cut) -> dict[str, Any]:
    """Portable prefix coordinates."""
    return {"index": cut.index, "instant": instant_data(cut.instant)}


def cut_from(data: Any) -> Cut:
    """Decode an exact prefix coordinate object."""
    if not isinstance(data, dict) or set(data) != {"index", "instant"}:
        raise KernelError("CUT_DATA", "unexpected cut fields")
    return Cut(data["index"], instant_from(data["instant"]))
