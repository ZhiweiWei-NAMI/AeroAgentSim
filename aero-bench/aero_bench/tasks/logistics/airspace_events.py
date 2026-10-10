"""Bounded no-fly violation detector for selected-scene logistics airspace.

This is a narrow, pure domain component. It strictly lowers the current
selected-scenario no-fly authoring shape (X east / Z south local metres,
``floorM``/``ceilingM``, ``startsAtS``/``endsAtS``, ``source``) into a
clearly framed canonical detector input, then deterministically reports
violation intervals from *caller-supplied* authoritative aircraft position
samples. It never generates positions, never fabricates telemetry, and never
pretends to be a Provider or a public trace.

Deliberate boundaries (documented for consumers)
------------------------------------------------

* Not a Provider. A future Airspace Provider may call
  :func:`detect_no_fly_events` at the harness barrier with its own
  authoritative samples and per-run identity, but this module does not
  integrate with any Provider and produces no public trace artifact.
* Between-sample crossing is *inferred* under the explicit linear
  interpolation assumption: positions and time interpolate linearly between
  consecutive authoritative samples. Every report carries
  ``interpolation_assumed = True`` and each event carries
  ``start_inferred``/``end_inferred`` flags, so exactly which interval
  endpoints were inferred rather than measured is always explicit. No event
  is ever presented as if the inference were a measurement. An endpoint that
  coincides with a *measured* sample is never marked inferred: a sample lying
  exactly on a volume surface ends the open interior there and is reported as
  ``end_basis = "sample_boundary_touch"`` (measured, not interpolated).
* A time-window activation while the airframe is already inside is an
  activation cause, not a stationarity claim: when the bracketing samples
  place the airframe at one position the event is ``stationary_activation``,
  and when they show motion it is ``window_activation_inside``. Neither name
  is ever invented; both follow directly from the caller's samples.
* Aircraft geometry is a zero-extent reference point at the sample
  coordinates. Body clearance is NOT handled and is never fabricated; the
  report's ``body_clearance`` field is literally ``"none"``.
* The volume interior is strict for the reference point: polygon edges and
  the floor/ceiling surfaces are not inside. Active-time windows are
  half-open ``[starts_at_s, ends_at_s)``; an unbounded window is
  ``[starts_at_s, inf)``.
* Everything fails loudly: non-finite coordinates/time, non-monotonic sample
  time, malformed/self-intersecting/degenerate polygons, time reversal, and
  unknown frames are rejected. There is no silent degradation.
* The module is side-effect free: no module-level mutable state, no I/O, no
  network, no global registry.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Annotated, Literal, Sequence, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes


#: Local selected-scene frame shared by the lowering step and the detector.
#: X is east (m), Z is south (m), Y is up (m); times are seconds.
NO_FLY_FRAME: Literal["scene_east_south_m"] = "scene_east_south_m"
NoFlyFrame: TypeAlias = Literal["scene_east_south_m"]

#: Strict identifier grammar shared by the current frontend authoring UI.
NoFlyIdentifier: TypeAlias = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
]

AirspaceEventType: TypeAlias = Literal[
    "already_inside",
    "observed_entry",
    "stationary_activation",
    "window_activation_inside",
]
StartBasis: TypeAlias = Literal[
    "initial_sample",
    "sample_endpoint",
    "interpolated_crossing",
    "time_window_activation",
]
EndBasis: TypeAlias = Literal[
    "interpolated_crossing",
    "time_window_expiry",
    "sample_boundary_touch",
    "stream_end",
]

_GEOM_EPS = 1e-9
_TIME_EPS = 1e-9
_MIN_AREA = 1e-8


class SourceInfo(StrictModel):
    """Provenance mirror of the current frontend ``source`` object."""

    kind: Literal["manual", "geojson"]
    label: str
    uri: str | None = None

    @field_validator("label")
    @classmethod
    def nonempty_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no-fly source label must be a nonempty string")
        return value

    @field_validator("uri")
    @classmethod
    def nonempty_uri(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("no-fly source uri must be a nonempty string or null")
        return value


class XzPoint(StrictModel):
    """One local metre point in the selected-scene frame: x east, z south."""

    x: float
    z: float

    @field_validator("x", "z")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("scene XZ coordinate must be finite")
        return value


def _finite_positive(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _finite_nonnegative(value: float, name: str) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def validated_open_ring(points: tuple[XzPoint, ...]) -> tuple[XzPoint, ...]:
    """Normalize a possibly-closed ring and reject malformed geometry."""
    ring = list(points)
    if len(ring) >= 2 and ring[0].x == ring[-1].x and ring[0].z == ring[-1].z:
        ring.pop()
    if len(ring) < 3:
        raise ValueError("no-fly polygon needs at least three vertices")
    vertices = {(point.x, point.z) for point in ring}
    if len(vertices) < 3:
        raise ValueError("no-fly polygon needs three distinct vertices")
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        if current.x == following.x and current.z == following.z:
            raise ValueError("no-fly polygon contains a zero-length edge")
    double_area = 0.0
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        double_area += current.x * following.z - following.x * current.z
    if abs(double_area) * 0.5 <= _MIN_AREA:
        raise ValueError("no-fly polygon has zero area")
    for first in range(len(ring)):
        for second in range(first + 1, len(ring)):
            if second == first + 1 or (first == 0 and second == len(ring) - 1):
                continue
            if _segments_touch(
                ring[first],
                ring[(first + 1) % len(ring)],
                ring[second],
                ring[(second + 1) % len(ring)],
            ):
                raise ValueError("no-fly polygon self-intersects or touches itself")
    return tuple(ring)


class AuthoredNoFlyZone(StrictModel):
    """Current frontend field names, lowered exactly and never mixed.

    Only the selected-scenario field names are accepted (``id``, ``name``,
    ``polygon``, ``floorM``, ``ceilingM``, ``startsAtS``, ``endsAtS``,
    ``source``). The model carries no frame field because the current
    authoring shape does not; lowering attaches the canonical frame.
    """

    zone_id: NoFlyIdentifier = Field(validation_alias="id")
    name: str
    polygon: tuple[XzPoint, ...]
    floor_m: float = Field(validation_alias="floorM")
    ceiling_m: float = Field(validation_alias="ceilingM")
    starts_at_s: float = Field(validation_alias="startsAtS")
    ends_at_s: float | None = Field(validation_alias="endsAtS")
    source: SourceInfo

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no-fly zone name must be a nonempty string")
        return value

    @field_validator("polygon")
    @classmethod
    def valid_polygon(cls, value: tuple[XzPoint, ...]) -> tuple[XzPoint, ...]:
        return validated_open_ring(value)

    @field_validator("floor_m")
    @classmethod
    def nonnegative_floor(cls, value: float) -> float:
        return _finite_nonnegative(value, "no-fly floor_m")

    @field_validator("ceiling_m")
    @classmethod
    def positive_ceiling(cls, value: float) -> float:
        return _finite_positive(value, "no-fly ceiling_m")

    @field_validator("starts_at_s")
    @classmethod
    def nonnegative_start(cls, value: float) -> float:
        return _finite_nonnegative(value, "no-fly starts_at_s")

    @field_validator("ends_at_s")
    @classmethod
    def positive_end(cls, value: float | None) -> float | None:
        if value is not None:
            return _finite_positive(value, "no-fly ends_at_s")
        return value

    @model_validator(mode="after")
    def zone_volume_is_consistent(self) -> "AuthoredNoFlyZone":
        _require_volume_ordering(
            zone_id=self.zone_id,
            ceiling_m=self.ceiling_m,
            floor_m=self.floor_m,
            starts_at_s=self.starts_at_s,
            ends_at_s=self.ends_at_s,
        )
        return self


class NoFlyZone(StrictModel):
    """Canonical, clearly framed detector input.

    ``frame`` is part of the contract: unknown frames cannot exist because
    ``NoFlyFrame`` is a closed literal. ``polygon`` is normalized to an open
    ring (a trailing closing vertex is removed) and geometry is fully
    validated; ``floor_m``/``ceiling_m`` define a positive altitude interval;
    ``starts_at_s``/``ends_at_s`` define a nonempty half-open window.
    """

    zone_id: NoFlyIdentifier
    name: str
    frame: NoFlyFrame
    polygon: tuple[XzPoint, ...]
    floor_m: float
    ceiling_m: float
    starts_at_s: float
    ends_at_s: float | None
    source: SourceInfo

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no-fly zone name must be a nonempty string")
        return value

    @field_validator("polygon")
    @classmethod
    def valid_polygon(cls, value: tuple[XzPoint, ...]) -> tuple[XzPoint, ...]:
        return validated_open_ring(value)

    @field_validator("floor_m")
    @classmethod
    def nonnegative_floor(cls, value: float) -> float:
        return _finite_nonnegative(value, "no-fly floor_m")

    @field_validator("ceiling_m")
    @classmethod
    def positive_ceiling(cls, value: float) -> float:
        return _finite_positive(value, "no-fly ceiling_m")

    @field_validator("starts_at_s")
    @classmethod
    def nonnegative_start(cls, value: float) -> float:
        return _finite_nonnegative(value, "no-fly starts_at_s")

    @field_validator("ends_at_s")
    @classmethod
    def positive_end(cls, value: float | None) -> float | None:
        if value is not None:
            return _finite_positive(value, "no-fly ends_at_s")
        return value

    @model_validator(mode="after")
    def zone_volume_is_consistent(self) -> "NoFlyZone":
        _require_volume_ordering(
            zone_id=self.zone_id,
            ceiling_m=self.ceiling_m,
            floor_m=self.floor_m,
            starts_at_s=self.starts_at_s,
            ends_at_s=self.ends_at_s,
        )
        return self


def _require_volume_ordering(
    *,
    zone_id: str,
    ceiling_m: float,
    floor_m: float,
    starts_at_s: float,
    ends_at_s: float | None,
) -> None:
    if not math.isfinite(ceiling_m) or not math.isfinite(floor_m):
        raise ValueError(f"no-fly zone {zone_id}: altitude bounds must be finite")
    if ceiling_m <= floor_m:
        raise ValueError(f"no-fly zone {zone_id}: ceiling must be above floor")
    if ends_at_s is not None and ends_at_s <= starts_at_s:
        raise ValueError(f"no-fly zone {zone_id}: end time must follow the start time")


def compile_no_fly_zone(value: object) -> NoFlyZone:
    """Strictly lower one current authoring zone into the canonical input."""
    authored = AuthoredNoFlyZone.model_validate(value)
    lowered = authored.model_dump()
    lowered["frame"] = NO_FLY_FRAME
    return NoFlyZone.model_validate(lowered)


def compile_no_fly_zones(values: object) -> tuple[NoFlyZone, ...]:
    """Lower a list of current authoring zones, rejecting duplicate ids."""
    if not isinstance(values, list):
        raise ValueError("no-fly zone list must be a JSON array")
    zones = tuple(compile_no_fly_zone(value) for value in values)
    ids = [zone.zone_id for zone in zones]
    if len(ids) != len(set(ids)):
        raise ValueError("no-fly zone ids must be unique")
    return zones


class PositionSample(StrictModel):
    """One caller-supplied authoritative aircraft position sample.

    ``sample_ref`` is the caller's stable reference for this sample and is
    echoed back as evidence on every violation event; ``time_s`` is in
    seconds; ``x``/``y``/``z`` are local scene metres (east / up / south).
    """

    sample_ref: NoFlyIdentifier
    time_s: float
    x: float
    y: float
    z: float

    @field_validator("time_s")
    @classmethod
    def nonnegative_time(cls, value: float) -> float:
        return _finite_nonnegative(value, "sample time_s")

    @field_validator("x", "y", "z")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sample coordinate must be finite")
        return value


class AirspaceViolation(StrictModel):
    """One maximal violation interval on the interpolated sample path.

    ``event_id`` is a deterministic SHA-256 over the violation's canonical
    fact fields, so identical inputs produce identical ids and distinct
    violations are distinct. ``bound_start_sample_ref`` and
    ``bound_end_sample_ref`` are the caller's authoritative sample refs for
    the segments that bound the estimated interval; they are the evidence a
    verifier can check, not fabricated positions.
    """

    event_id: Sha256
    zone_id: NoFlyIdentifier
    zone_name: str
    subject_id: NoFlyIdentifier
    event_type: AirspaceEventType
    start_basis: StartBasis
    end_basis: EndBasis
    start_time_s: float
    end_time_s: float
    start_inferred: bool
    end_inferred: bool
    bound_start_sample_ref: NoFlyIdentifier
    bound_end_sample_ref: NoFlyIdentifier

    @field_validator("start_time_s", "end_time_s")
    @classmethod
    def finite_bound(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("violation interval endpoint must be finite")
        return value

    @model_validator(mode="after")
    def violation_is_consistent(self) -> "AirspaceViolation":
        if self.end_time_s < self.start_time_s:
            raise ValueError("violation interval must not be reversed")
        if self.start_inferred != (self.start_basis == "interpolated_crossing"):
            raise ValueError("start_inferred must agree with start_basis")
        if self.end_inferred != (self.end_basis == "interpolated_crossing"):
            raise ValueError("end_inferred must agree with end_basis")
        return self


class NoFlyEventsReport(StrictModel):
    """Deterministic detection result for one zone and one sample stream.

    ``interpolation_assumed`` is always ``True``: the detector's inferred
    between-sample crossing is a declared assumption, never a hidden
    measurement. ``body_clearance`` is always ``"none"``: aircraft geometry
    is a reference point only, and clearance is never fabricated.
    """

    zone_id: NoFlyIdentifier
    zone_name: str
    subject_id: NoFlyIdentifier
    frame: NoFlyFrame
    interpolation_assumed: Literal[True] = True
    body_clearance: Literal["none"] = "none"
    sample_count: Annotated[int, Field(ge=0)]
    violation_count: Annotated[int, Field(ge=0)]
    violations: tuple[AirspaceViolation, ...] = ()

    @model_validator(mode="after")
    def report_is_consistent(self) -> "NoFlyEventsReport":
        if self.violation_count != len(self.violations):
            raise ValueError("violation_count must equal the violation list length")
        events = [violation.event_id for violation in self.violations]
        if len(events) != len(set(events)):
            raise ValueError("violation event ids must be unique")
        starts = [violation.start_time_s for violation in self.violations]
        if any(starts[index] > starts[index + 1] for index in range(len(starts) - 1)):
            raise ValueError("violations must be sorted by start time")
        for violation in self.violations:
            if violation.zone_id != self.zone_id:
                raise ValueError("violation names the wrong zone")
            if violation.subject_id != self.subject_id:
                raise ValueError("violation names the wrong subject")
        return self


def _cross_xz(a: XzPoint, b: XzPoint, c: XzPoint) -> float:
    return (b.x - a.x) * (c.z - a.z) - (b.z - a.z) * (c.x - a.x)


def _on_segment_xz(point: XzPoint, start: XzPoint, end: XzPoint) -> bool:
    return (
        abs(_cross_xz(start, end, point)) <= _GEOM_EPS
        and point.x >= min(start.x, end.x) - _GEOM_EPS
        and point.x <= max(start.x, end.x) + _GEOM_EPS
        and point.z >= min(start.z, end.z) - _GEOM_EPS
        and point.z <= max(start.z, end.z) + _GEOM_EPS
    )


def _segments_properly_cross(
    a: XzPoint, b: XzPoint, c: XzPoint, d: XzPoint
) -> bool:
    ab_c = _cross_xz(a, b, c)
    ab_d = _cross_xz(a, b, d)
    cd_a = _cross_xz(c, d, a)
    cd_b = _cross_xz(c, d, b)
    return bool(
        (ab_c > _GEOM_EPS and ab_d < -_GEOM_EPS)
        or (ab_c < -_GEOM_EPS and ab_d > _GEOM_EPS)
    ) and bool(
        (cd_a > _GEOM_EPS and cd_b < -_GEOM_EPS)
        or (cd_a < -_GEOM_EPS and cd_b > _GEOM_EPS)
    )


def _segments_touch(
    a: XzPoint, b: XzPoint, c: XzPoint, d: XzPoint
) -> bool:
    return (
        _segments_properly_cross(a, b, c, d)
        or _on_segment_xz(c, a, b)
        or _on_segment_xz(d, a, b)
        or _on_segment_xz(a, c, d)
        or _on_segment_xz(b, c, d)
    )


def _point_in_polygon_interior_xz(
    point: XzPoint, ring: tuple[XzPoint, ...]
) -> bool:
    """Strict interior test: a point on a polygon edge is not inside."""
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        if _on_segment_xz(point, start, end):
            return False
    inside = False
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        if (start.z > point.z) != (end.z > point.z) and point.x < (
            (end.x - start.x) * (point.z - start.z) / (end.z - start.z) + start.x
        ):
            inside = not inside
    return inside


def _edge_crossing_fraction(
    from_xz: XzPoint, to_xz: XzPoint, edge_start: XzPoint, edge_end: XzPoint
) -> float | None:
    ray_x = to_xz.x - from_xz.x
    ray_z = to_xz.z - from_xz.z
    edge_x = edge_end.x - edge_start.x
    edge_z = edge_end.z - edge_start.z
    denominator = ray_x * edge_z - ray_z * edge_x
    if abs(denominator) <= _GEOM_EPS:
        return None
    offset_x = edge_start.x - from_xz.x
    offset_z = edge_start.z - from_xz.z
    fraction = (offset_x * edge_z - offset_z * edge_x) / denominator
    edge_fraction = (offset_x * ray_z - offset_z * ray_x) / denominator
    if fraction < -_GEOM_EPS or fraction > 1.0 + _GEOM_EPS:
        return None
    if edge_fraction < -_GEOM_EPS or edge_fraction > 1.0 + _GEOM_EPS:
        return None
    return min(1.0, max(0.0, fraction))


def _on_ring_boundary_xz(point: XzPoint, ring: tuple[XzPoint, ...]) -> bool:
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        if _on_segment_xz(point, start, end):
            return True
    return False


def _lerp_point(from_xz: XzPoint, to_xz: XzPoint, fraction: float) -> XzPoint:
    return XzPoint(
        x=from_xz.x + (to_xz.x - from_xz.x) * fraction,
        z=from_xz.z + (to_xz.z - from_xz.z) * fraction,
    )


def _segment_polygon_interior_intervals(
    from_xz: XzPoint,
    to_xz: XzPoint,
    ring: tuple[XzPoint, ...],
) -> tuple[tuple[float, float], ...]:
    """Intervals in [0, 1] whose points are strictly polygon-interior."""
    candidates: set[float] = {0.0, 1.0}
    if _on_ring_boundary_xz(from_xz, ring):
        candidates.add(0.0)
    if _on_ring_boundary_xz(to_xz, ring):
        candidates.add(1.0)
    for index, edge_start in enumerate(ring):
        edge_end = ring[(index + 1) % len(ring)]
        fraction = _edge_crossing_fraction(from_xz, to_xz, edge_start, edge_end)
        if fraction is not None:
            candidates.add(fraction)
    ordered = sorted(candidates)
    intervals: list[tuple[float, float]] = []
    for index in range(len(ordered) - 1):
        lo, hi = ordered[index], ordered[index + 1]
        if hi - lo <= _GEOM_EPS:
            continue
        midpoint = _lerp_point(from_xz, to_xz, (lo + hi) / 2.0)
        if _point_in_polygon_interior_xz(midpoint, ring):
            intervals.append((lo, hi))
    return tuple(intervals)


def _value_band_interior_fractions(
    from_value: float, to_value: float, low: float, high: float
) -> tuple[tuple[float, float], ...]:
    """Intervals in [0, 1] where low < value(u) < high (strict interior)."""
    candidates: set[float] = {0.0, 1.0}
    delta = to_value - from_value
    if abs(delta) > _GEOM_EPS:
        for bound in (low, high):
            fraction = (bound - from_value) / delta
            if 0.0 <= fraction <= 1.0:
                candidates.add(fraction)
    ordered = sorted(candidates)
    intervals: list[tuple[float, float]] = []
    for index in range(len(ordered) - 1):
        lo, hi = ordered[index], ordered[index + 1]
        if hi - lo <= _GEOM_EPS:
            continue
        value = from_value + delta * (lo + hi) / 2.0
        if low < value < high:
            intervals.append((lo, hi))
    return tuple(intervals)


def _time_active_fraction(
    time_from: float, time_to: float, starts_at_s: float, ends_at_s: float | None
) -> tuple[float, float] | None:
    if time_to < starts_at_s:
        return None
    if ends_at_s is not None and time_from >= ends_at_s:
        return None
    span = time_to - time_from
    lo = 0.0
    hi = 1.0
    if starts_at_s > time_from:
        lo = (starts_at_s - time_from) / span
    if ends_at_s is not None and ends_at_s < time_to:
        hi = (ends_at_s - time_from) / span
    if lo >= hi:
        return None
    return lo, hi


def _tolerance(value: float) -> float:
    return max(1.0, abs(value)) * _TIME_EPS


@dataclass(frozen=True)
class _ActiveInterval:
    segment_idx: int
    end_segment_idx: int
    lo: float
    hi: float
    lo_causes: frozenset[str] = field(default_factory=frozenset)
    hi_causes: frozenset[str] = field(default_factory=frozenset)
    start_time: float = 0.0
    end_time: float = 0.0


_CONSTRAINT_NAMES = ("polygon", "altitude", "time")


def _causes(value: float, *bounds: float) -> frozenset[str]:
    return frozenset(
        name for name, bound in zip(_CONSTRAINT_NAMES, bounds) if value == bound
    )


def _sample_spatial_inside(sample: PositionSample, zone: NoFlyZone) -> bool:
    altitude_inside = zone.floor_m < sample.y < zone.ceiling_m
    xz_point = XzPoint(x=sample.x, z=sample.z)
    return altitude_inside and _point_in_polygon_interior_xz(
        xz_point, zone.polygon
    )


def _on_spatial_boundary(sample: PositionSample, zone: NoFlyZone) -> bool:
    """True when a measured sample lies on a strict-interior volume surface.

    The volume interior is open: polygon edges and the floor/ceiling surfaces
    are not inside. A sample exactly on any of those surfaces is a measured
    boundary touch, the endpoint of the open interior it bounds.
    """
    if abs(sample.y - zone.floor_m) <= _GEOM_EPS:
        return True
    if abs(sample.y - zone.ceiling_m) <= _GEOM_EPS:
        return True
    return _on_ring_boundary_xz(XzPoint(x=sample.x, z=sample.z), zone.polygon)


def _samples_stationary(first: PositionSample, second: PositionSample) -> bool:
    """True when caller-supplied samples place the aircraft at one position.

    This is a direct reading of the authoritative samples, never invented
    telemetry: coincident sample positions are the module's only evidence of a
    hover, and any sampled motion is reported as motion.
    """
    return (
        abs(first.x - second.x) <= _GEOM_EPS
        and abs(first.y - second.y) <= _GEOM_EPS
        and abs(first.z - second.z) <= _GEOM_EPS
    )


def _sample_active_inside(sample: PositionSample, zone: NoFlyZone) -> bool:
    if not _sample_spatial_inside(sample, zone):
        return False
    if sample.time_s < zone.starts_at_s:
        return False
    if zone.ends_at_s is not None and sample.time_s >= zone.ends_at_s:
        return False
    return True


def _build_active_intervals(
    zone: NoFlyZone, samples: tuple[PositionSample, ...]
) -> tuple[_ActiveInterval, ...]:
    intervals: list[_ActiveInterval] = []
    for segment in range(len(samples) - 1):
        first = samples[segment]
        second = samples[segment + 1]
        span = second.time_s - first.time_s
        time_interval = _time_active_fraction(
            first.time_s, second.time_s, zone.starts_at_s, zone.ends_at_s
        )
        if time_interval is None:
            continue
        time_lo, time_hi = time_interval
        first_xz = XzPoint(x=first.x, z=first.z)
        second_xz = XzPoint(x=second.x, z=second.z)
        polygon_intervals = _segment_polygon_interior_intervals(
            first_xz, second_xz, zone.polygon
        )
        altitude_intervals = _value_band_interior_fractions(
            first.y, second.y, zone.floor_m, zone.ceiling_m
        )
        for poly_lo, poly_hi in polygon_intervals:
            for alt_lo, alt_hi in altitude_intervals:
                lo = max(poly_lo, alt_lo, time_lo)
                hi = min(poly_hi, alt_hi, time_hi)
                if lo < hi - _GEOM_EPS:
                    intervals.append(
                        _ActiveInterval(
                            segment_idx=segment,
                            end_segment_idx=segment,
                            lo=lo,
                            hi=hi,
                            lo_causes=_causes(lo, poly_lo, alt_lo, time_lo),
                            hi_causes=_causes(hi, poly_hi, alt_hi, time_hi),
                            start_time=first.time_s + lo * span,
                            end_time=first.time_s + hi * span,
                        )
                    )
    return tuple(intervals)


def _merge_intervals(
    intervals: tuple[_ActiveInterval, ...],
    *,
    samples: tuple[PositionSample, ...],
    zone: NoFlyZone,
) -> tuple[_ActiveInterval, ...]:
    merged: list[_ActiveInterval] = []
    current: _ActiveInterval | None = None
    for interval in intervals:
        if current is None:
            current = interval
            continue
        junction = samples[interval.segment_idx]
        # A sample exactly on a strict-interior surface interrupts the open
        # volume at that measured instant: the two neighbouring maximal
        # intervals must not be collapsed across it.
        junction_touches_boundary = _on_spatial_boundary(junction, zone)
        if (
            interval.segment_idx == current.end_segment_idx + 1
            and current.hi == 1.0
            and interval.lo == 0.0
            and abs(interval.start_time - current.end_time)
            <= _tolerance(interval.start_time)
            and not junction_touches_boundary
        ):
            current = _ActiveInterval(
                segment_idx=current.segment_idx,
                end_segment_idx=interval.end_segment_idx,
                lo=current.lo,
                hi=interval.hi,
                lo_causes=current.lo_causes,
                hi_causes=interval.hi_causes,
                start_time=current.start_time,
                end_time=interval.end_time,
            )
        else:
            merged.append(current)
            current = interval
    if current is not None:
        merged.append(current)
    return tuple(merged)


def _event_id(
    zone_id: str,
    subject_id: str,
    event_type: str,
    start_basis: str,
    end_basis: str,
    start_time_s: float,
    end_time_s: float,
    bound_start_sample_ref: str,
    bound_end_sample_ref: str,
) -> str:
    body = {
        "zone_id": zone_id,
        "subject_id": subject_id,
        "event_type": event_type,
        "start_basis": start_basis,
        "end_basis": end_basis,
        "start_time_s": start_time_s,
        "end_time_s": end_time_s,
        "bound_start_sample_ref": bound_start_sample_ref,
        "bound_end_sample_ref": bound_end_sample_ref,
    }
    return hashlib.sha256(canonical_json_bytes(body)).hexdigest()


def _make_violation(
    zone: NoFlyZone,
    samples: tuple[PositionSample, ...],
    subject_id: str,
    interval: _ActiveInterval,
) -> AirspaceViolation:
    start = _classify_start(zone, samples, interval)
    end = _classify_end(zone, samples, interval)
    event_id = _event_id(
        zone.zone_id,
        subject_id,
        start[0],
        start[1],
        end[0],
        start[2],
        end[1],
        start[3],
        end[2],
    )
    return AirspaceViolation(
        event_id=event_id,
        zone_id=zone.zone_id,
        zone_name=zone.name,
        subject_id=subject_id,
        event_type=start[0],
        start_basis=start[1],
        end_basis=end[0],
        start_time_s=start[2],
        end_time_s=end[1],
        start_inferred=start[4],
        end_inferred=end[3],
        bound_start_sample_ref=start[3],
        bound_end_sample_ref=end[2],
    )


def _classify_start(
    zone: NoFlyZone,
    samples: tuple[PositionSample, ...],
    interval: _ActiveInterval,
) -> tuple[AirspaceEventType, StartBasis, float, str, bool]:
    segment = interval.segment_idx
    if segment == 0 and interval.lo == 0.0:
        return (
            "already_inside",
            "initial_sample",
            samples[0].time_s,
            samples[0].sample_ref,
            False,
        )
    previous_spatial = False
    if interval.lo == 0.0 and segment > 0:
        previous_spatial = _sample_spatial_inside(samples[segment - 1], zone)
    time_zone_begins_here = (
        interval.lo == 0.0
        and segment > 0
        and samples[segment].time_s == zone.starts_at_s
        and samples[segment - 1].time_s < zone.starts_at_s
    )
    if (
        interval.lo > 0.0
        and "time" in interval.lo_causes
        and "polygon" not in interval.lo_causes
        and "altitude" not in interval.lo_causes
    ):
        # The scheduled window activates while the airframe is inside the open
        # spatial volume. The truthful cause is the window, and whether the
        # airframe is hovering at that instant is decided only from the
        # caller-supplied bracketing samples (never invented telemetry).
        if _samples_stationary(samples[segment], samples[segment + 1]):
            return (
                "stationary_activation",
                "time_window_activation",
                zone.starts_at_s,
                samples[segment].sample_ref,
                False,
            )
        return (
            "window_activation_inside",
            "time_window_activation",
            zone.starts_at_s,
            samples[segment].sample_ref,
            False,
        )
    if interval.lo == 0.0:
        if previous_spatial and time_zone_begins_here:
            if _samples_stationary(samples[segment - 1], samples[segment]):
                return (
                    "stationary_activation",
                    "time_window_activation",
                    samples[segment].time_s,
                    samples[segment].sample_ref,
                    False,
                )
            return (
                "window_activation_inside",
                "time_window_activation",
                samples[segment].time_s,
                samples[segment].sample_ref,
                False,
            )
        return (
            "observed_entry",
            "sample_endpoint",
            samples[segment].time_s,
            samples[segment].sample_ref,
            False,
        )
    return (
        "observed_entry",
        "interpolated_crossing",
        interval.start_time,
        samples[segment].sample_ref,
        True,
    )


def _classify_end(
    zone: NoFlyZone,
    samples: tuple[PositionSample, ...],
    interval: _ActiveInterval,
) -> tuple[EndBasis, float, str, bool]:
    segment = interval.end_segment_idx
    if interval.hi == 1.0 and segment == len(samples) - 2:
        # The interval reaches the final measured sample.
        if (
            "time" in interval.hi_causes
            and zone.ends_at_s is not None
            and zone.ends_at_s == samples[-1].time_s
        ):
            # The last sample sits exactly on the exclusive expiry boundary and
            # is still spatially interior: the stop cause is the scheduled
            # window, measured at the sample that closes it.
            return (
                "time_window_expiry",
                zone.ends_at_s,
                samples[-1].sample_ref,
                False,
            )
        return (
            "stream_end",
            samples[-1].time_s,
            samples[-1].sample_ref,
            False,
        )
    if interval.hi == 1.0:
        # The interval reaches a non-final measured sample. If that sample lies
        # exactly on a strict-interior surface (polygon edge or floor/ceiling),
        # the open volume ends there: the endpoint is measured at the sample,
        # not inferred, and the scheduled window is not the cause.
        touch = samples[segment + 1]
        if _on_spatial_boundary(touch, zone):
            return (
                "sample_boundary_touch",
                touch.time_s,
                touch.sample_ref,
                False,
            )
    if "time" in interval.hi_causes:
        if zone.ends_at_s is None:
            # Unbounded windows never expire mid-segment; an interval reaching
            # the end of a segment while the airframe is not on a spatial
            # boundary would have merged into the next segment. Keep the
            # interpolation fallback only as a defensive guard.
            return (
                "interpolated_crossing",
                interval.end_time,
                samples[segment + 1].sample_ref,
                True,
            )
        return (
            "time_window_expiry",
            zone.ends_at_s,
            samples[segment + 1].sample_ref,
            False,
        )
    return (
        "interpolated_crossing",
        interval.end_time,
        samples[segment + 1].sample_ref,
        True,
    )


def detect_no_fly_events(
    zone: NoFlyZone,
    samples: Sequence[PositionSample],
    *,
    subject_id: NoFlyIdentifier,
) -> NoFlyEventsReport:
    """Deterministically report violation intervals for one zone.

    Samples must be a strictly time-increasing, caller-supplied authoritative
    stream. Between-sample positions are inferred only under the explicit
    linear interpolation assumption carried by the report; this function
    never fabricates samples or claims measured positions it did not receive.
    """
    validated = tuple(samples)
    if any(
        index > 0 and sample.time_s <= validated[index - 1].time_s
        for index, sample in enumerate(validated)
    ):
        raise ValueError("sample times must be strictly increasing (reversed)")

    violations: list[AirspaceViolation] = []
    if len(validated) == 1 and _sample_active_inside(validated[0], zone):
        event_id = _event_id(
            zone.zone_id,
            subject_id,
            "already_inside",
            "initial_sample",
            "stream_end",
            validated[0].time_s,
            validated[0].time_s,
            validated[0].sample_ref,
            validated[0].sample_ref,
        )
        violations.append(
            AirspaceViolation(
                event_id=event_id,
                zone_id=zone.zone_id,
                zone_name=zone.name,
                subject_id=subject_id,
                event_type="already_inside",
                start_basis="initial_sample",
                end_basis="stream_end",
                start_time_s=validated[0].time_s,
                end_time_s=validated[0].time_s,
                start_inferred=False,
                end_inferred=False,
                bound_start_sample_ref=validated[0].sample_ref,
                bound_end_sample_ref=validated[0].sample_ref,
            )
        )
    elif len(validated) >= 2:
        intervals = _merge_intervals(
            _build_active_intervals(zone, validated),
            samples=validated,
            zone=zone,
        )
        violations = [
            _make_violation(zone, validated, subject_id, interval)
            for interval in intervals
        ]

    return NoFlyEventsReport(
        zone_id=zone.zone_id,
        zone_name=zone.name,
        subject_id=subject_id,
        frame=zone.frame,
        sample_count=len(validated),
        violation_count=len(violations),
        violations=tuple(violations),
    )


__all__ = [
    "AirspaceEventType",
    "AirspaceViolation",
    "AuthoredNoFlyZone",
    "EndBasis",
    "NO_FLY_FRAME",
    "NoFlyEventsReport",
    "NoFlyFrame",
    "NoFlyIdentifier",
    "NoFlyZone",
    "PositionSample",
    "SourceInfo",
    "StartBasis",
    "XzPoint",
    "compile_no_fly_zone",
    "compile_no_fly_zones",
    "detect_no_fly_events",
]
