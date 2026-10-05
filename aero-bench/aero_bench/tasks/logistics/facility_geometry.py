"""Deterministic physical landing-pad materialization for a facility.

This module lowers one canonical :class:`FacilityCapabilities` into the exact
physical landing-pad rectangles that the current frontend produces for a
selected-city facility.  It is a pure, deterministic geometry decoding of the
*declared* capability contract; it never measures a building, never invents a
roof, and never fabricates an observation.

Parity contract (what must match, and why nothing else may be invented)
------------------------------------------------------------------------

The current frontend ``facilityLandingPads(toFacilityVisualSpec(facility))``
lays pads out in the facility-root local frame, then the selected-city dispatch
route estimator (``padFor`` in ``city-selected-dispatch-route.ts``) rotates
and translates that local row into the selected scene's shared
``scene_east_south_m`` frame (x east, y up, z south):

* Ground vertiport pads are 4.2 x 4.2 m rectangles at ``pitch_m=5.4`` along the
  footprint's local X axis, centred on the facility origin, with a local deck
  centre at ``z=0.08`` and contact surface ``y=0.72``.
* Ground hub pads are 4.76 x 4.4 m rectangles at ``pitch_m=5.96``, local deck
  centre ``z=-1.155`` and contact surface ``y=3.3625``.
* A standalone charger gets one 2.2 x 2.2 m pad per charging slot (max 3) at
  ``pitch_m=3.0``, local ``z=1`` and contact surface ``y=0.12``.
* A rooftop vertiport lifts every pad by the *declared, already-bound*
  ``support_height_m``.  That number is carried as the verified support input;
  this module adds it to the deck height and never proves the roof itself.
* The facility's ``rotation_deg`` (Y axis, right-handed, east-positive) maps
  each local pad ``(px, pz)`` through
  ``x = position.x + cos(a) * px + sin(a) * pz`` and
  ``z = position.z - sin(a) * px + cos(a) * pz``.

Every returned pad therefore lives in the explicit ``scene_east_south_m``
frame and carries the facility's rotation as ``rotation_deg``.  No caller
should re-bind these to an unrelated runtime origin: converting the shared
scene frame to a bound run origin is the responsible caller's job (for
example the future logistics run barrier), never this module's.

Decoding is strict where the frontend authoring is strict.  The facility must
meet the renderer's ``FACILITY_MIN_DIMENSIONS`` (vertiport 12 x 8 x 4, hub
14 x 11 x 5, charger 10 x 8 x 3.2 m): ``validateSpec`` in
``city-facility-models.ts`` rejects any of width/depth/height below its per-kind
minimum before it draws a single pad, so the physical helper never lowers
geometry the actual renderer refuses to draw.  A landing count must fit the
footprint width exactly as the frontend ``maxLandingParkingSlots`` decides it,
i.e. ``floor((width - padWidth + pitch) / pitch)`` computed in the frontend's
own left-to-right operation order with no extra tolerance, a charger may declare
at most three stations and its station row must fit the footprint width under
the frontend charger rule, and the pad column (local z centre plus half depth)
must fit the footprint depth.  Counts that cannot physically exist are rejected
instead of being silently clamped or re-derived.

The pad ``y`` is the aircraft contact surface (deck or supported roof deck).
The aircraft body-centre offset is deliberately never added here; dispatch
callers lift the pose by ``body.yM / 2`` themselves.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator

from aero_bench.config.models import StrictModel
from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityIdentifier,
)

PadFrame: TypeAlias = Literal["scene_east_south_m"]
PAD_FRAME: PadFrame = "scene_east_south_m"

#: Small float tolerance used when mirroring the frontend charger width rule.
_GEOM_EPS = 1e-9

#: Frontend ``FACILITY_MIN_DIMENSIONS`` per facility kind: (width, depth, height)
#: in metres.  ``validateSpec`` in ``city-facility-models.ts`` rejects any
#: facility below these before drawing a single pad.
_FACILITY_RENDER_MINIMA: dict[str, tuple[float, float, float]] = {
    "vertiport": (12.0, 8.0, 4.0),
    "hub": (14.0, 11.0, 5.0),
    "charger": (10.0, 8.0, 3.2),
}


class _PadLayout:
    """Immutable pad-row geometry for one landing-capable facility kind."""

    __slots__ = ("width_m", "depth_m", "pitch_m", "z_m", "y_m")

    def __init__(
        self,
        *,
        width_m: float,
        depth_m: float,
        pitch_m: float,
        z_m: float,
        y_m: float,
    ) -> None:
        self.width_m = width_m
        self.depth_m = depth_m
        self.pitch_m = pitch_m
        self.z_m = z_m
        self.y_m = y_m


#: Vertiport pads (frontend ``LANDING_PAD_LAYOUTS.vertiport``).
VERTIPORT_PAD_LAYOUT = _PadLayout(
    width_m=4.2, depth_m=4.2, pitch_m=5.4, z_m=0.08, y_m=0.72
)
#: Hub pads (frontend ``LANDING_PAD_LAYOUTS.hub``).
HUB_PAD_LAYOUT = _PadLayout(
    width_m=4.76, depth_m=4.4, pitch_m=5.96, z_m=-1.155, y_m=3.3625
)
#: Standalone charger pads (frontend ``CHARGER_SLOT_PITCH_M`` = 3 m).
CHARGER_PAD_LAYOUT = _PadLayout(
    width_m=2.2, depth_m=2.2, pitch_m=3.0, z_m=1.0, y_m=0.12
)
#: A standalone charger model holds at most three stations.
CHARGER_MAX_STATIONS = 3

#: Shared selected-scene frame: x east (m), y up (m), z south (m).
SCENE_EAST_SOUTH_M: PadFrame = "scene_east_south_m"

PadIndex: TypeAlias = Annotated[int, Field(ge=0)]


class FacilityLandingPad(StrictModel):
    """One immutable physical landing rectangle in ``scene_east_south_m``.

    ``x``/``y``/``z`` are the pad centre in scene-east-south metres; ``y`` is
    the aircraft contact surface (declared deck height plus an already-bound
    rooftop ``support_height_m`` when present).  The pad's local axes follow
    the facility's ``rotation_deg`` (Y axis, east-positive).  ``pad_index`` is
    the stable local row index the selected-city scheduler reserves against.
    """

    facility_id: FacilityIdentifier
    pad_index: PadIndex
    x: float
    y: float
    z: float
    width_m: float
    depth_m: float
    rotation_deg: float
    frame: PadFrame

    @field_validator("pad_index", mode="before")
    @classmethod
    def _pad_index_strict(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("pad_index must be an integer")
        if value < 0:
            raise ValueError("pad_index must be nonnegative")
        return value

    @field_validator("x", "y", "z", "rotation_deg", mode="before")
    @classmethod
    def _coordinate_strict(cls, value: object, info) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{info.field_name} must be a finite number")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{info.field_name} must be finite")
        return numeric

    @field_validator("width_m", "depth_m", mode="before")
    @classmethod
    def _size_strict(cls, value: object, info) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{info.field_name} must be a finite number")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{info.field_name} must be finite and positive")
        return numeric


def _max_landing_parking_slots(kind: str, width_m: float) -> int:
    """Frontend ``maxLandingParkingSlots``: ``floor((width - padWidth + pitch) / pitch)``.

    The arithmetic is performed in the frontend's exact left-to-right operation
    order (subtract the pad width, add the pitch, divide, floor) so the backend
    and the renderer cannot diverge by a 1-ulp window at an exact-fit boundary.
    A non-finite or non-positive width caps at zero, exactly like the frontend
    guard.
    """
    layout = VERTIPORT_PAD_LAYOUT if kind == "vertiport" else HUB_PAD_LAYOUT
    if not math.isfinite(width_m) or width_m <= 0:
        return 0
    return math.floor((width_m - layout.width_m + layout.pitch_m) / layout.pitch_m)


def _require_render_minima(
    *,
    kind: str,
    width_m: float,
    depth_m: float,
    height_m: float,
) -> None:
    """Mirror the renderer's ``FACILITY_MIN_DIMENSIONS`` authoring gate.

    ``validateSpec`` in ``city-facility-models.ts`` rejects width/depth/height
    below the per-kind minimum before a visual spec is even created; this helper
    applies the same gate in ``facility_landing_pads`` so the backend never
    materializes pads for geometry the actual renderer refuses to draw.
    """
    min_width, min_depth, min_height = _FACILITY_RENDER_MINIMA[kind]
    if width_m < min_width or depth_m < min_depth or height_m < min_height:
        raise ValueError(
            f"{kind} requires at least {min_width:g} x {min_depth:g} x {min_height:g} m "
            f"(width x depth x height); got {width_m:g} x {depth_m:g} x {height_m:g} m"
        )


def _require_depth_fits(
    *,
    kind: str,
    layout: _PadLayout,
    depth_m: float,
) -> None:
    column_extent = abs(layout.z_m) + layout.depth_m / 2.0
    if column_extent > depth_m / 2.0:
        raise ValueError(
            f"{kind} pad column extent {column_extent:g} m exceeds the "
            f"{depth_m:g} m footprint depth"
        )


def facility_landing_pads(
    facility: FacilityCapabilities,
) -> tuple[FacilityLandingPad, ...]:
    """Materialize the physical selected-city landing pads for one facility.

    The returned tuple is immutable and every record lives in the explicit
    ``scene_east_south_m`` frame.  Dimensions the actual frontend renderer
    rejects (any width/depth/height below ``FACILITY_MIN_DIMENSIONS``) and
    geometry that cannot physically fit the declared footprint are rejected.
    ``y`` is the pad contact surface; the aircraft body-centre half-height is
    never added here.
    """
    _require_render_minima(
        kind=facility.kind,
        width_m=facility.width_m,
        depth_m=facility.depth_m,
        height_m=facility.height_m,
    )
    if facility.kind in {"vertiport", "hub"}:
        if facility.landing is None:
            raise ValueError(
                f"{facility.kind} {facility.facility_id} must declare a landing capability"
            )
        layout = (
            VERTIPORT_PAD_LAYOUT if facility.kind == "vertiport" else HUB_PAD_LAYOUT
        )
        count = facility.landing.parking_slots
        max_slots = _max_landing_parking_slots(facility.kind, facility.width_m)
        if count > max_slots:
            raise ValueError(
                f"{facility.kind} pad row for {count} pads at {layout.pitch_m:g} m pitch "
                f"exceeds the {facility.width_m:g} m footprint width "
                f"(frontend maxLandingParkingSlots caps at {max_slots})"
            )
        _require_depth_fits(
            kind=facility.kind,
            layout=layout,
            depth_m=facility.depth_m,
        )
    else:  # standalone charger
        if facility.charging is None:
            raise ValueError(
                f"charger {facility.facility_id} must declare a charging capability"
            )
        count = facility.charging.slots
        if count > CHARGER_MAX_STATIONS:
            raise ValueError(
                f"charger {facility.facility_id} declares {count} charging slots but "
                f"the standalone charger model holds at most {CHARGER_MAX_STATIONS}"
            )
        if count * CHARGER_PAD_LAYOUT.pitch_m > facility.width_m + _GEOM_EPS:
            raise ValueError(
                f"charger {facility.facility_id} station row {count * CHARGER_PAD_LAYOUT.pitch_m:g} m "
                f"exceeds the {facility.width_m:g} m footprint width"
            )
        _require_depth_fits(
            kind="charger",
            layout=CHARGER_PAD_LAYOUT,
            depth_m=facility.depth_m,
        )
        layout = CHARGER_PAD_LAYOUT

    support_y = facility.support_height_m if facility.support_height_m is not None else 0.0
    angle = math.radians(facility.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    origin_x = facility.position.x
    origin_z = facility.position.z

    pads: list[FacilityLandingPad] = []
    for index in range(count):
        local_x = (index - (count - 1) / 2.0) * layout.pitch_m
        pads.append(
            FacilityLandingPad(
                facility_id=facility.facility_id,
                pad_index=index,
                x=origin_x + cosine * local_x + sine * layout.z_m,
                y=layout.y_m + support_y,
                z=origin_z - sine * local_x + cosine * layout.z_m,
                width_m=layout.width_m,
                depth_m=layout.depth_m,
                rotation_deg=facility.rotation_deg,
                frame=PAD_FRAME,
            )
        )
    return tuple(pads)


__all__ = [
    "CHARGER_MAX_STATIONS",
    "CHARGER_PAD_LAYOUT",
    "FacilityLandingPad",
    "HUB_PAD_LAYOUT",
    "PadFrame",
    "PAD_FRAME",
    "SCENE_EAST_SOUTH_M",
    "VERTIPORT_PAD_LAYOUT",
    "facility_landing_pads",
]
