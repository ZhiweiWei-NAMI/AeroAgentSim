"""Deterministic facility physical geometry and Gazebo SDF/XML emission.

This module lowers one accepted :class:`FacilityCapabilities` into exact static
collision bodies in the shared Gazebo ENU world frame and emits deterministic
SDF ``<model>`` blocks that a future logistics run barrier can splice into the
existing ``gazebo/scene.sdf`` produced by
:func:`aero_bench.world.scene_compiler.gazebo_sdf_bytes`.  It never measures a
building and never fabricates an observation; every structural box is derived
from the actual frontend model dimensions in
``frontend/src/city-facility-models.ts`` (``buildVertiport``, ``buildHub``,
``buildCharger``) and every landing
contact is the accepted
:func:`aero_bench.tasks.logistics.facility_geometry.facility_landing_pads`
rectangle.

Frame contract (frontend ``scene_east_south_m`` vs Gazebo ENU)
--------------------------------------------------------------

The accepted facility authoring and pad materialization live in the selected
scene frame ``scene_east_south_m``: x east, y up, z south.  Gazebo SDF in this
checkout declares ``world_frame_orientation = ENU``, whose axes are x east,
y north, z up.  This module therefore applies the explicit signed map, never a
recentre and never an inferred offset::

    enu_east  = scene_x
    enu_north = -scene_z
    enu_up    = scene_y

A facility's ``rotation_deg`` is a right-handed rotation about the scene up
axis, east-positive.  Under the signed frame map this is exactly the ENU yaw
(a rotation about the ENU up axis from east toward north): a local pad at
``+x`` with ``rotation_deg = 90`` lands on scene ``z = -1``, i.e. ENU
``north = +1``, exactly like the frontend three.js Y-axis rotation the fixtures
capture.  So ``yaw_deg`` in every emitted body equals ``facility.rotation_deg``
unchanged.  Facility/pad positions are used directly relative to the scene's
ENU origin; no aggregate is re-centred.

Structure versus landing-surface identity (PX4 contact authority)
----------------------------------------------------------------

The current PX4 contact journal classifies every contact counterpart token as
``ground.*`` / ``launch_pad.*`` (ground / landing contact) or anything else
(collision).  This module therefore keeps the two physical populations
separately from the outset:

* :class:`FacilityPadContact` -- one per accepted pad, emitted as a static
  model whose name starts with ``launch_pad.`` (``launch_pad.<id>.<index>``).
  A real run barrier must bind these models as the scenario's launch sites (or
  add explicit contact-model tokens with ``launch_pad.*`` tokens) so PX4
  records a landing on a pad as a ground contact, never as a collision.
* :class:`FacilitySolidBody` -- structural bodies emitted with
  ``facility_<kind>_<id>_<body>`` model names, which never start with ``ground.``
  or ``launch_pad.`` and therefore classify as collisions.  The whole building
  is never labelled ``ground`` or ``launch_pad``.

Carrying-platform partition (no coincident contact/collision surfaces)
----------------------------------------------------------------------

The frontend carrying platforms -- the vertiport raised landing deck, the hub
sorting-hall roof, and each charger per-station pad base -- have their upper
face at exactly the pad contact surface.  Emitting that platform as one solid
box would make its top face coincide with each ``launch_pad.*`` contact slab
top and produce both landing and collision tokens at the same surface.  This
module therefore never emits a monolithic carrying platform: each one is
partitioned, in its own local frame, into axis-aligned collision boxes that
surround each pad footprint and leave the pad column empty.  Formally the
platform rectangle is subtracted by the *expanded* pad footprints
(``CONTACT_BOUNDARY_GAP_M`` clearance on every side), and the leftover
rectilinear polygon is decomposed into non-overlapping boxes that all keep the
platform's full declared vertical extent (full-through carve, so no structural
surface exists under any pad interior).  The declared pad rectangles (top,
full footprint, ENU pose) are never altered, the whole platform is never
relabelled ``launch_pad``, and ``pad`` contact height is never shifted.

The distinction between frontend *source* geometry and the physical *collision
partition* is explicit in the typed output:

* ``FacilitySolidBody.frontend_mesh`` always names the actual frontend
  ``box(...)`` the body is derived from (including partition pieces);
* ``FacilitySolidBody.partition_of`` is ``None`` for a body taken 1:1 from one
  frontend box call and equals the original platform ``body_id`` for every
  partition piece;
* every partition piece gets ``body_id`` ``<platform_body_id>.part<index>`` and
  a distinct collision ``model_name``.

Roof support and pad height authority
--------------------------------------

A rooftop vertiport carries the already-verified declared
``support_height_m``; this module adds it to every body and pad height
unchanged and never infers a roof.  Per-pad contact surface ``up_top_m`` is
exactly the accepted ``FacilityLandingPad.y`` (deck or supported roof deck);
the contact slab has its thickness entirely below that top.  By construction no
structural collider occupies or rises through a pad column, and
:func:`facility_physics` rejects any structural body whose volume overlaps a
pad contact slab or whose top face is coplanar with a contact surface under its
interior.

Identifier encoding
-------------------

Facility ids allow ``[A-Za-z0-9_.:-]``.  Gazebo model names must map every id
to a single safe token and the mapping must be injective: two facilities
different only by a delimiter (``hub:wx`` vs ``hub_wx`` vs ``hub.wx``) must
never share a model name.  :func:`_injective_model_fragment` keeps the already
safe bytes ``[A-Za-z0-9.-]`` as-is, escapes ``_`` as ``__``, and escapes every
other byte (e.g. ``:``) as its two uppercase hex digits prefixed by ``_``
(``hub:wx`` -> ``hub_3Awx``).  Any ``_`` in the output is unambiguously the
start of an escape, so the encoding is injective and deterministic;
:func:`facility_physics` additionally enforces model-name uniqueness for every
materialization rather than relying on a later integration fix.

Collision simplification scope (documented, no full-city guarantee)
--------------------------------------------------------------------

Structural bodies are an exact subset of the frontend model-group boxes --
load-bearing bodies and platforms -- and nothing else:

* vertiport: the raised landing deck (partitioned around its pads) and four
  deck supports under every declared pad;
* hub: the sorting hall, sorting-hall roof (partitioned around its pads),
  loading dock, loading canopy, dispatch office and office roof;
* charger: per-station drone-charging pad bases (partitioned around each pad),
  per-station control pedestals, four equipment-canopy columns, the solar
  equipment canopy, and the power conversion cabinet.

Excluded as decorative/non-load-bearing: painted boundaries and H marks,
landing rings, approach beacons, illuminated fascias, status lamps,
shutter seams, locker screens, solar panel cells and dividers, glass
clerestories, wayfinding pylons/lights, contact bars, bollards, lane stripes,
the 3.5 cm painted ground perimeter, and the passenger shelter GLB.  This is an
explicitly simplified collision set for one facility; it makes no claim about
full-city collision completeness, street furniture, or host buildings.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityIdentifier,
    FacilityKind,
)
from aero_bench.tasks.logistics.facility_geometry import (
    CHARGER_PAD_LAYOUT,
    HUB_PAD_LAYOUT,
    VERTIPORT_PAD_LAYOUT,
    facility_landing_pads,
)
from aero_bench.world.scene_compiler import SceneOrigin

ENUFrame: TypeAlias = Literal["enu_m"]
ENU_FRAME: ENUFrame = "enu_m"
"""Shared Gazebo world frame: x east, y north, z up (metres)."""

PadIndex: TypeAlias = Annotated[int, Field(ge=0)]

#: Per-kind pad contact slab thickness, taken from the actual frontend platform
#: slab that carries the pads (the whole slab height lies below the contact
#: top).  ``buildVertiport`` raises the deck to 0.72 with a 0.2 m slab, the hub
#: roof reaches 3.3625 through a 0.225 m slab, and ``buildCharger`` makes the
#: per-station pad base 0.1 m thick up to contact 0.12.
PAD_CONTACT_THICKNESS_M: dict[FacilityKind, float] = {
    "vertiport": 0.2,
    "hub": 0.225,
    "charger": 0.1,
}

#: Explicit horizontal clearance between every structural collision body and
#: every pad contact footprint.  Carrying-platform partitions carve out each
#: pad footprint plus this margin, so a structural collider never shares a face
#: with a ``launch_pad.*`` contact slab; the closest possible contact
#: relationship is a separated boundary at exactly this distance.
CONTACT_BOUNDARY_GAP_M = 0.02

#: Bytes already safe for a single SDF model-name token.
_SDF_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-"
)
_SDF_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_DEFAULT_PROTRUSION_TOLERANCE_M = 1e-9
_PARTITION_EPS_M = 1e-9


def _injective_model_fragment(value: str) -> str:
    """Injective, deterministic SDF-safe encoding of a facility id.

    Safe bytes ``[A-Za-z0-9.-]`` are kept verbatim.  ``_`` is escaped as ``__``
    and every other byte (``:`` and anything outside the safe set) is escaped as
    ``_HH`` with two uppercase hex digits.  The mapping is injective: every ``_``
    in the output unambiguously begins ``__`` (a literal underscore) or ``_HH``
    (one raw byte), so no two distinct ids produce the same token.
    """
    out: list[str] = []
    for character in value:
        if character in _SDF_SAFE:
            out.append(character)
        elif character == "_":
            out.append("__")
        else:
            for byte in character.encode("utf-8"):
                out.append(f"_{byte:02X}")
    return "".join(out)


def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("cannot serialize a non-finite number")
    return format(value, ".15g")


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _positive(value: object, label: str) -> float:
    numeric = _finite(value, label)
    if numeric <= 0:
        raise ValueError(f"{label} must be positive")
    return numeric


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


class FacilityPadContact(StrictModel):
    """One landing-pad contact body in the shared ENU frame.

    ``up_top_m`` is exactly the accepted ``FacilityLandingPad.y`` contact
    surface; ``thickness_m`` sits entirely below that surface and is the
    frontend platform slab thickness for the facility kind.  The rectangle
    ``size_east_m`` x ``size_north_m`` is the accepted pad ``width_m`` x
    ``depth_m`` rotated by the accepted ``yaw_deg``.  The record carries its own
    PX4-safe ``launch_pad.*`` model name.
    """

    facility_id: FacilityIdentifier
    pad_index: PadIndex
    model_name: str
    frame: ENUFrame
    east_m: float
    north_m: float
    up_top_m: float
    thickness_m: float
    size_east_m: float
    size_north_m: float
    yaw_deg: float

    @field_validator("pad_index", mode="before")
    @classmethod
    def _pad_index_strict(cls, value: object) -> int:
        index = _strict_int(value, "pad_index")
        if index < 0:
            raise ValueError("pad_index must be nonnegative")
        return index

    @field_validator("east_m", "north_m", "up_top_m", "yaw_deg", mode="before")
    @classmethod
    def _coordinate_strict(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @field_validator("thickness_m", "size_east_m", "size_north_m", mode="before")
    @classmethod
    def _size_strict(cls, value: object, info) -> float:
        return _positive(value, info.field_name)

    @field_validator("model_name", mode="before")
    @classmethod
    def _model_name_strict(cls, value: object) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("model_name must be a non-empty string")
        return value

    @field_validator("frame", mode="before")
    @classmethod
    def _frame_strict(cls, value: object) -> str:
        if value != ENU_FRAME:
            raise ValueError("frame must be exactly 'enu_m'")
        return value


class FacilitySolidBody(StrictModel):
    """One deterministic structural collision box in the shared ENU frame.

    ``east_m``/``north_m`` are the box centre; ``up_bottom_m``/``up_top_m`` are
    its vertical extent; ``size_east_m`` x ``size_north_m`` are the box's local
    horizontal dimensions rotated by ``yaw_deg``; ``frontend_mesh`` names the
    exact frontend ``box(...)`` it is derived from.  ``partition_of`` is
    ``None`` for a body taken 1:1 from one frontend box call and names the
    original carrying-platform ``body_id`` for every partition piece (a
    deterministic collision decomposition that is never a frontend mesh itself).
    """

    facility_id: FacilityIdentifier
    body_id: str
    model_name: str
    frontend_mesh: str
    frame: ENUFrame
    east_m: float
    north_m: float
    up_bottom_m: float
    up_top_m: float
    size_east_m: float
    size_north_m: float
    yaw_deg: float
    partition_of: str | None = None

    @field_validator("east_m", "north_m", "up_bottom_m", "up_top_m", "yaw_deg", mode="before")
    @classmethod
    def _coordinate_strict(cls, value: object, info) -> float:
        return _finite(value, info.field_name)

    @field_validator("size_east_m", "size_north_m", mode="before")
    @classmethod
    def _size_strict(cls, value: object, info) -> float:
        return _positive(value, info.field_name)

    @field_validator("body_id", "model_name", "frontend_mesh", mode="before")
    @classmethod
    def _label_strict(cls, value: object, info) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return value

    @field_validator("partition_of", mode="before")
    @classmethod
    def _partition_of_strict(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("partition_of must be a non-empty string or None")
        return value

    @field_validator("frame", mode="before")
    @classmethod
    def _frame_strict(cls, value: object) -> str:
        if value != ENU_FRAME:
            raise ValueError("frame must be exactly 'enu_m'")
        return value

    @model_validator(mode="after")
    def vertical_extent_is_ordered(self) -> "FacilitySolidBody":
        if not self.up_top_m > self.up_bottom_m:
            raise ValueError("up_top_m must be above up_bottom_m")
        return self


class FacilityPhysicsError(ValueError):
    """Raised when a facility cannot be lowered into consistent physical geometry."""


@dataclass(frozen=True, slots=True)
class _LocalBody:
    """One frontend-derived body in the facility root-local scene frame.

    ``(x, y, z)`` are the box centre in metres (x east, y up, z south, all in
    the facility's own rotated local frame); ``size_x/size_y/size_z`` are the
    box's local dimensions.  The values are the *actual* frontend
    ``box(width, height, depth, x, y, z)`` arguments already scaled by the
    model group's ``FACILITY_MIN_DIMENSIONS`` scale.  ``partition_of`` is
    ``None`` for a monolithic frontend body and names the source platform
    ``body_id`` for a deterministic collision-partition piece.
    """

    body_id: str
    mesh: str
    x: float
    y: float
    z: float
    size_x: float
    size_y: float
    size_z: float
    partition_of: str | None = None


@dataclass(frozen=True, slots=True)
class _LocalRect:
    """Imutable axis-aligned rectangle in the facility root-local frame."""

    x0: float
    x1: float
    z0: float
    z1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.z1 - self.z0

    def overlaps(self, other: "_LocalRect", *, strict: bool) -> bool:
        if strict:
            return not (
                self.x1 <= other.x0 or other.x1 <= self.x0
                or self.z1 <= other.z0 or other.z1 <= self.z0
            )
        return not (
            self.x1 < other.x0 or other.x1 < self.x0
            or self.z1 < other.z0 or other.z1 < self.z0
        )


def _local_pad_row_xs(kind: str, count: int) -> tuple[float, ...]:
    """Local pad X centres for a selected-city row, mirroring
    ``selectedFacilityLandingPads`` in the frontend: ``(index - (count - 1) /
    2) * pitch`` along the footprint's local X axis."""
    layout = VERTIPORT_PAD_LAYOUT if kind == "vertiport" else HUB_PAD_LAYOUT
    return tuple((index - (count - 1) / 2.0) * layout.pitch_m for index in range(count))


def _clamped_row_lip(
    *,
    pad_width_m: float,
    row_xs: tuple[float, ...],
    footprint_width_m: float,
    declared_lip_m: float,
) -> tuple[float, float, float, float]:
    """Continuous carrying-platform X row for a pad list.

    Returns ``(row_min_x, row_max_x, row_extent_m, lip_x_m)``: the outer pad
    edges, their extent, and the platform lip beyond them clamped so the
    platform never exceeds the declared footprint width (mirrors the frontend
    ``rowMinXM/rowMaxXM/lipXM`` arithmetic in ``buildVertiport`` and
    ``buildHub``).
    """
    row_min_x = min(x - pad_width_m / 2.0 for x in row_xs)
    row_max_x = max(x + pad_width_m / 2.0 for x in row_xs)
    row_extent_m = row_max_x - row_min_x
    lip_x_m = max(0.0, min(declared_lip_m, (footprint_width_m - row_extent_m) / 2.0))
    return row_min_x, row_max_x, row_extent_m, lip_x_m


def _vertiport_bodies(facility: FacilityCapabilities) -> tuple[_LocalBody, ...]:
    """Vertiport structural boxes (design box 12 x 4 x 8) covering the accepted
    pad row.

    Mirrors the dynamic ``buildVertiport`` in ``city-facility-models.ts``: one
    continuous raised landing deck spans the whole row (lip 1.92 m beyond the
    outer pad edges, clamped to the footprint), and four load-bearing columns
    sit under every declared pad at the historical 3.24 m lateral offset
    (clamped to the deck edge on a tight row).
    """
    layout = VERTIPORT_PAD_LAYOUT
    row_xs = _local_pad_row_xs("vertiport", facility.landing.parking_slots)
    row_min_x, row_max_x, row_extent_m, lip_x_m = _clamped_row_lip(
        pad_width_m=layout.width_m, row_xs=row_xs,
        footprint_width_m=facility.width_m, declared_lip_m=1.92,
    )
    row_centre_x = (row_min_x + row_max_x) / 2.0

    bodies: list[_LocalBody] = [
        _LocalBody(
            body_id="raised_landing_deck",
            mesh="raised landing deck",
            x=row_centre_x, y=0.155 * 4, z=layout.z_m,
            size_x=row_extent_m + 2 * lip_x_m, size_y=0.05 * 4, size_z=6.32,
        )
    ]
    support_index = 0
    for pad_x in row_xs:
        # A 0.42 m support column must clear the 4.2 m pad column (pad half
        # width 2.1 + half support 0.21) and stay inside the deck edges.
        support_lateral = min(
            3.24,
            row_max_x + lip_x_m - pad_x - 0.21,
            pad_x - (row_min_x - lip_x_m) - 0.21,
        )
        for side_x in (-support_lateral, support_lateral):
            for side_z in (-2.56, 2.56):
                bodies.append(
                    _LocalBody(
                        body_id=f"landing_deck_support.{support_index}",
                        mesh="landing deck support",
                        x=pad_x + side_x, y=0.0875 * 4, z=layout.z_m + side_z,
                        size_x=0.42, size_y=0.105 * 4, size_z=0.28,
                    )
                )
                support_index += 1
    return tuple(bodies)


def _hub_bodies(facility: FacilityCapabilities) -> tuple[_LocalBody, ...]:
    """Hub structural boxes (design box 14 x 5 x 11) covering the accepted pad row.

    Mirrors the canonical ``buildHub`` in ``city-facility-models.ts``:
    the sorting hall and its roof form one continuous platform spanning the pad
    row (roof lip 3.08 m beyond the outer pad edges, clamped to the footprint,
    with the historical 0.42 m hall eave), the loading dock/canopy follow the
    same row centre, and the glass dispatch office stays under the platform so
    it never enters a usable landing prism.
    """
    layout = HUB_PAD_LAYOUT
    row_xs = _local_pad_row_xs("hub", facility.landing.parking_slots)
    row_min_x, row_max_x, row_extent_m, lip_x_m = _clamped_row_lip(
        pad_width_m=layout.width_m, row_xs=row_xs,
        footprint_width_m=facility.width_m, declared_lip_m=3.08,
    )
    row_centre_x = (row_min_x + row_max_x) / 2.0
    roof_width_m = row_extent_m + 2 * lip_x_m
    hall_width_m = roof_width_m - 0.84

    bodies: list[_LocalBody] = [
        _LocalBody("sorting_hall", "sorting hall",
                   row_centre_x, 0.335 * 5, layout.z_m,
                   hall_width_m, 0.58 * 5, 6.82),
        _LocalBody("sorting_hall_roof", "sorting hall roof",
                   row_centre_x, 0.65 * 5, layout.z_m,
                   roof_width_m, 0.045 * 5, 7.37),
        _LocalBody("loading_dock", "loading dock",
                   row_centre_x, 0.078 * 5, 3.3,
                   roof_width_m - 0.7, 0.055 * 5, 2.09),
        _LocalBody("loading_canopy", "loading canopy",
                   row_centre_x, 0.64 * 5, 3.41,
                   roof_width_m - 0.56, 0.025 * 5, 1.98),
    ]
    # buildHub keeps the glass office below the pad platform.
    office_x = max(1.5, min(5.11, hall_width_m / 2.0 - 1.4))
    bodies.extend(
        [
            _LocalBody("dispatch_office", "dispatch office",
                       office_x, 0.288 * 5, -1.76,
                       2.8, 0.31 * 5, 2.64),
            _LocalBody("dispatch_office_roof", "office roof",
                       office_x, 0.467 * 5, -1.76,
                       3.36, 0.027 * 5, 3.08),
        ]
    )
    return tuple(bodies)


def _charger_bodies(stations: int) -> tuple[_LocalBody, ...]:
    """Charger structural boxes (design box 10 x 3.2 x 8) for ``stations`` pads.

    Station offsets follow ``buildCharger``: ``x = (index - (stations-1)/2) *
    0.30`` normalized, i.e. 3 m real pitch per ``CHARGER_SLOT_PITCH_M``.
    """
    bodies: list[_LocalBody] = []
    for index in range(stations):
        local_x = (index - (stations - 1) / 2.0) * 3.0
        bodies.append(
            _LocalBody(
                body_id=f"drone_charging_pad.{index}",
                mesh=f"drone charging pad {index + 1}",
                x=local_x, y=0.07, z=1.0,
                size_x=0.26 * 10, size_y=0.1, size_z=2.6,
            )
        )
        bodies.append(
            _LocalBody(
                body_id=f"charger_control_pedestal.{index}",
                mesh=f"charger control pedestal {index + 1}",
                x=local_x, y=0.23 * 3.2, z=-0.13 * 8,
                size_x=0.05 * 10, size_y=0.35 * 3.2, size_z=0.052 * 8,
            )
        )
    column_index = 0
    for local_x in (-0.43, 0.43):
        for local_z in (-0.42, -0.24):
            bodies.append(
                _LocalBody(
                    body_id=f"equipment_canopy_column.{column_index}",
                    mesh="equipment canopy column",
                    x=local_x * 10, y=0.40 * 3.2, z=local_z * 8,
                    size_x=0.023 * 10, size_y=0.74 * 3.2, size_z=0.023 * 8,
                )
            )
            column_index += 1
    bodies.append(
        _LocalBody(
            body_id="solar_equipment_canopy", mesh="solar equipment canopy",
            x=0.0, y=0.79 * 3.2, z=-0.33 * 8,
            size_x=0.91 * 10, size_y=0.04 * 3.2, size_z=0.24 * 8,
        )
    )
    bodies.append(
        _LocalBody(
            body_id="power_conversion_cabinet", mesh="power conversion cabinet",
            x=0.36 * 10, y=0.33 * 3.2, z=-0.33 * 8,
            size_x=0.10 * 10, size_y=0.56 * 3.2, size_z=0.08 * 8,
        )
    )
    return tuple(bodies)


def _contact_model_name(facility_id: str, pad_index: int) -> str:
    return f"launch_pad.{_injective_model_fragment(facility_id)}.{pad_index}"


def _structural_model_name(*, kind: str, facility_id: str, body_id: str) -> str:
    return f"facility_{kind}_{_injective_model_fragment(facility_id)}_{body_id}"


def _scene_to_enu(
    x_scene: float, y_scene: float, z_scene: float
) -> tuple[float, float, float]:
    return x_scene, -z_scene, y_scene


def _rect_axis_vectors(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    theta = math.radians(yaw_deg)
    cosine = math.cos(theta)
    sine = math.sin(theta)
    return (cosine, sine), (-sine, cosine)


def _rotated_rectangles_overlap(
    *,
    a_center_east_m: float,
    a_center_north_m: float,
    a_half_east_m: float,
    a_half_north_m: float,
    a_yaw_deg: float,
    b_center_east_m: float,
    b_center_north_m: float,
    b_half_east_m: float,
    b_half_north_m: float,
    b_yaw_deg: float,
) -> bool:
    """Separating-axis predicate for two horizontal rectangles in the ENU plane.

    Two oriented boxes overlap with positive area iff every candidate axis (the
    two local axes of each box) sees one-dimensional projections that overlap
    with positive measure.  Merely touching projections do not overlap, matching
    the conservative boundary semantics used for pad protection and the explicit
    contact-boundary gap.
    """

    boxes = (
        (
            a_center_east_m,
            a_center_north_m,
            a_half_east_m,
            a_half_north_m,
            _rect_axis_vectors(a_yaw_deg),
        ),
        (
            b_center_east_m,
            b_center_north_m,
            b_half_east_m,
            b_half_north_m,
            _rect_axis_vectors(b_yaw_deg),
        ),
    )
    for axis in (*_rect_axis_vectors(a_yaw_deg), *_rect_axis_vectors(b_yaw_deg)):
        projections: list[tuple[float, float]] = []
        for center_east, center_north, half_east, half_north, box_axes in boxes:
            projection = center_east * axis[0] + center_north * axis[1]
            radius = (
                half_east
                * abs(axis[0] * box_axes[0][0] + axis[1] * box_axes[0][1])
                + half_north
                * abs(axis[0] * box_axes[1][0] + axis[1] * box_axes[1][1])
            )
            projections.append((projection - radius, projection + radius))
        (left_min, left_max), (right_min, right_max) = projections
        if left_max <= right_min or right_max <= left_min:
            return False
    return True


def structure_protrudes_pad_contact(
    *,
    body_east_m: float,
    body_north_m: float,
    body_size_east_m: float,
    body_size_north_m: float,
    body_up_top_m: float,
    body_yaw_deg: float,
    pad_east_m: float,
    pad_north_m: float,
    pad_size_east_m: float,
    pad_size_north_m: float,
    pad_up_top_m: float,
    pad_yaw_deg: float,
    tolerance_m: float = 0.0,
) -> bool:
    """Return ``True`` when a structural body rises above a pad contact surface
    inside the pad footprint.

    The body must not protrude through the declared pad: if the body's
    horizontal rectangle overlaps the pad's rectangle (positive-area overlap --
    a mere boundary touch is not protrusion) and the body's top is above the pad
    contact surface (beyond ``tolerance_m``), the pad is obstructed.  A body
    whose top is level with the pad (the carrying-platform top around the pads)
    is never a protrusion.
    """
    if body_up_top_m <= pad_up_top_m + tolerance_m:
        return False
    return _rotated_rectangles_overlap(
        a_center_east_m=body_east_m,
        a_center_north_m=body_north_m,
        a_half_east_m=body_size_east_m / 2.0,
        a_half_north_m=body_size_north_m / 2.0,
        a_yaw_deg=body_yaw_deg,
        b_center_east_m=pad_east_m,
        b_center_north_m=pad_north_m,
        b_half_east_m=pad_size_east_m / 2.0,
        b_half_north_m=pad_size_north_m / 2.0,
        b_yaw_deg=pad_yaw_deg,
    )


def structure_protrudes_pad(
    body: "FacilitySolidBody",
    contact: "FacilityPadContact",
    *,
    tolerance_m: float = _DEFAULT_PROTRUSION_TOLERANCE_M,
) -> bool:
    """Convenience predicate over two typed records."""
    return structure_protrudes_pad_contact(
        body_east_m=body.east_m,
        body_north_m=body.north_m,
        body_size_east_m=body.size_east_m,
        body_size_north_m=body.size_north_m,
        body_up_top_m=body.up_top_m,
        body_yaw_deg=body.yaw_deg,
        pad_east_m=contact.east_m,
        pad_north_m=contact.north_m,
        pad_size_east_m=contact.size_east_m,
        pad_size_north_m=contact.size_north_m,
        pad_up_top_m=contact.up_top_m,
        pad_yaw_deg=contact.yaw_deg,
        tolerance_m=tolerance_m,
    )


def any_structure_protrudes_pad(
    physics: "FacilityPhysics",
    *,
    tolerance_m: float = _DEFAULT_PROTRUSION_TOLERANCE_M,
) -> bool:
    """``True`` when any structural body of a facility protrudes through a pad."""
    return any(
        structure_protrudes_pad(body, contact, tolerance_m=tolerance_m)
        for body in physics.bodies
        for contact in physics.contacts
    )


def body_overlaps_pad_slab(
    body: "FacilitySolidBody",
    contact: "FacilityPadContact",
    *,
    tolerance_m: float = 0.0,
) -> bool:
    """``True`` when a structural body's solid volume overlaps a pad contact slab.

    The pad slab occupies the contact footprint (full declared rectangle, exact
    ENU pose) from ``up_top_m - thickness_m`` up to ``up_top_m``.  A body only
    overlaps it when both the vertical intervals overlap with positive measure
    and the horizontal rectangles overlap with positive area; mere boundary
    touches never overlap.  ``tolerance_m`` shrinks the vertical intervals
    before the test so a caller can allow contact-level slop.
    """
    slab_bottom = contact.up_top_m - contact.thickness_m
    if body.up_top_m <= slab_bottom + tolerance_m:
        return False
    if body.up_bottom_m >= contact.up_top_m - tolerance_m:
        return False
    return _rotated_rectangles_overlap(
        a_center_east_m=body.east_m,
        a_center_north_m=body.north_m,
        a_half_east_m=body.size_east_m / 2.0,
        a_half_north_m=body.size_north_m / 2.0,
        a_yaw_deg=body.yaw_deg,
        b_center_east_m=contact.east_m,
        b_center_north_m=contact.north_m,
        b_half_east_m=contact.size_east_m / 2.0,
        b_half_north_m=contact.size_north_m / 2.0,
        b_yaw_deg=contact.yaw_deg,
    )


def any_body_overlaps_pad_slab(
    physics: "FacilityPhysics",
    *,
    tolerance_m: float = 0.0,
) -> bool:
    """``True`` when any structural body volume overlaps any pad contact slab."""
    return any(
        body_overlaps_pad_slab(body, contact, tolerance_m=tolerance_m)
        for body in physics.bodies
        for contact in physics.contacts
    )


def structure_top_coplanar_under_contact_interior(
    body: "FacilitySolidBody",
    contact: "FacilityPadContact",
    *,
    tolerance_m: float = _DEFAULT_PROTRUSION_TOLERANCE_M,
) -> bool:
    """``True`` when a body's top face is coplanar with a pad contact surface
    under the pad's interior.

    A carrying platform exactly level with the contact top must never exist
    below the pad interior: the emitted collision solid would share the landing
    surface.  The test requires the top to be level with the pad top within
    ``tolerance_m`` AND the horizontal rectangles to overlap with positive area.
    """
    if abs(body.up_top_m - contact.up_top_m) > tolerance_m:
        return False
    return _rotated_rectangles_overlap(
        a_center_east_m=body.east_m,
        a_center_north_m=body.north_m,
        a_half_east_m=body.size_east_m / 2.0,
        a_half_north_m=body.size_north_m / 2.0,
        a_yaw_deg=body.yaw_deg,
        b_center_east_m=contact.east_m,
        b_center_north_m=contact.north_m,
        b_half_east_m=contact.size_east_m / 2.0,
        b_half_north_m=contact.size_north_m / 2.0,
        b_yaw_deg=contact.yaw_deg,
    )


def any_structure_top_coplanar_under_contact(
    physics: "FacilityPhysics",
    *,
    tolerance_m: float = _DEFAULT_PROTRUSION_TOLERANCE_M,
) -> bool:
    """``True`` when any structural top face is coplanar with a contact surface
    under that contact's interior."""
    return any(
        structure_top_coplanar_under_contact_interior(body, contact, tolerance_m=tolerance_m)
        for body in physics.bodies
        for contact in physics.contacts
    )


def facility_physics_digest(physics: "FacilityPhysics") -> str:
    """Deterministic SHA-256 over the canonical JSON of the full physical model.

    The digest covers every contact and every structural body (identity,
    frame, coordinates, dimensions, orientation, model name, partition
    provenance), so any of these changing--even a single 1-ulp float--changes
    the digest.  This is a plain integrity record, not a cryptographic seal.
    """
    return hashlib.sha256(canonical_json_bytes(physics.model_dump())).hexdigest()


def _local_pad_rects(facility: FacilityCapabilities) -> tuple[_LocalRect, ...]:
    """Declared pad rectangles in the facility root-local frame, derived only
    from the same selected-city layout the frontend emits.

    The values mirror ``selectedFacilityLandingPads`` in
    ``city-facility-models.ts``: vertiport/hub pads at the per-kind pitch along
    the local X axis with the per-kind local Z centre/contact Y, and one charger
    pad per slot at ``CHARGER_SLOT_PITCH_M = 3`` with local ``z = 1``.
    """
    if facility.kind == "vertiport":
        layout = VERTIPORT_PAD_LAYOUT
        count = facility.landing.parking_slots
    elif facility.kind == "hub":
        layout = HUB_PAD_LAYOUT
        count = facility.landing.parking_slots
    else:
        layout = CHARGER_PAD_LAYOUT
        assert facility.charging is not None
        count = facility.charging.slots
    rects: list[_LocalRect] = []
    for index in range(count):
        local_x = (index - (count - 1) / 2.0) * layout.pitch_m
        half_w = layout.width_m / 2.0
        half_d = layout.depth_m / 2.0
        rects.append(
            _LocalRect(
                x0=local_x - half_w,
                x1=local_x + half_w,
                z0=layout.z_m - half_d,
                z1=layout.z_m + half_d,
            )
        )
    return tuple(rects)


def _local_rect(local: _LocalBody) -> _LocalRect:
    return _LocalRect(
        x0=local.x - local.size_x / 2.0,
        x1=local.x + local.size_x / 2.0,
        z0=local.z - local.size_z / 2.0,
        z1=local.z + local.size_z / 2.0,
    )


def _pad_carve_rects(pads: tuple[_LocalRect, ...], gap_m: float) -> tuple[_LocalRect, ...]:
    """Pad footprints expanded by ``gap_m`` -- the exact columns carved out of a
    carrying platform so structural colliders stay clear of the pad slabs."""
    return tuple(
        _LocalRect(
            x0=pad.x0 - gap_m,
            x1=pad.x1 + gap_m,
            z0=pad.z0 - gap_m,
            z1=pad.z1 + gap_m,
        )
        for pad in pads
    )


def _subtract_one_hole(rect: _LocalRect, hole: _LocalRect) -> tuple[_LocalRect, ...]:
    """Return the axis-aligned boxes covering ``rect`` minus ``hole``.

    A positive-area overlap produces the canonical plus-shape decomposition: a
    left strip, a right strip, a bottom strip and a top strip whose union is
    exactly ``rect`` with ``hole`` removed.  Each returned box keeps the full
    parent ``rect`` extent on the axis it spans.
    """
    overlap_x0 = max(rect.x0, hole.x0)
    overlap_x1 = min(rect.x1, hole.x1)
    overlap_z0 = max(rect.z0, hole.z0)
    overlap_z1 = min(rect.z1, hole.z1)
    if (
        overlap_x0 >= overlap_x1
        or overlap_z0 >= overlap_z1
        or overlap_x0 >= rect.x1
        or overlap_x1 <= rect.x0
        or overlap_z0 >= rect.z1
        or overlap_z1 <= rect.z0
    ):
        return (rect,)
    pieces: list[_LocalRect] = []
    if rect.x0 < overlap_x0:
        pieces.append(_LocalRect(rect.x0, overlap_x0, rect.z0, rect.z1))
    if overlap_x1 < rect.x1:
        pieces.append(_LocalRect(overlap_x1, rect.x1, rect.z0, rect.z1))
    if rect.z0 < overlap_z0:
        pieces.append(_LocalRect(overlap_x0, overlap_x1, rect.z0, overlap_z0))
    if overlap_z1 < rect.z1:
        pieces.append(_LocalRect(overlap_x0, overlap_x1, overlap_z1, rect.z1))
    return tuple(pieces)


def _partition_platform_rect(
    platform: _LocalRect,
    pads: tuple[_LocalRect, ...],
    gap_m: float,
) -> tuple[_LocalRect, ...]:
    """Deterministic local-box subtraction of a carrying platform around its pads.

    Starting from the full platform rectangle, each expanded pad rectangle is
    subtracted in ``pad_index`` order and each produced box is re-subtracted in
    turn (the standard plus-shape decomposition).  The result is a set of
    non-overlapping axis-aligned boxes whose union is the platform rectangle with
    every pad column removed; a zero- or negative-area piece is dropped and the
    remaining pieces are sorted canonically by ``(x0, z0, x1, z1)`` so the
    emitted partition is stable across runs.
    """
    pieces = _subtract_one_hole(platform, _pad_carve_rects(pads, gap_m)[0]) if pads else (platform,)
    for hole in _pad_carve_rects(pads, gap_m)[1:]:
        next_pieces: list[_LocalRect] = []
        for piece in pieces:
            next_pieces.extend(_subtract_one_hole(piece, hole))
        pieces = tuple(
            rect
            for rect in next_pieces
            if rect.width > _PARTITION_EPS_M and rect.depth > _PARTITION_EPS_M
        )
    return tuple(sorted(pieces, key=lambda rect: (rect.x0, rect.z0, rect.x1, rect.z1)))


def _local_body_from_part(
    source: _LocalBody,
    rect: _LocalRect,
    part_index: int,
) -> _LocalBody:
    return _LocalBody(
        body_id=f"{source.body_id}.part{part_index}",
        mesh=source.mesh,
        x=(rect.x0 + rect.x1) / 2.0,
        y=source.y,
        z=(rect.z0 + rect.z1) / 2.0,
        size_x=rect.width,
        size_y=source.size_y,
        size_z=rect.depth,
        partition_of=source.body_id,
    )


_CARRYING_PLATFORM_BODY_IDS: dict[FacilityKind, tuple[str, ...]] = {
    "vertiport": ("raised_landing_deck",),
    "hub": ("sorting_hall_roof",),
    "charger": (),
}


def _partitioned_local_bodies(
    facility: FacilityCapabilities,
    local_bodies: tuple[_LocalBody, ...],
) -> tuple[_LocalBody, ...]:
    """Replace each carrying platform with its deterministic collision partition.

    A carrying platform (vertiport raised deck, hub sorting-hall roof, charger
    per-station pad base) is never emitted as one solid box: its top face is the
    pad contact surface, so a monolithic box would coincide with the
    ``launch_pad.*`` slab.  The platform rectangle is subtracted around every
    pad footprint (with ``CONTACT_BOUNDARY_GAP_M`` clearance) instead.
    """
    pads = _local_pad_rects(facility)
    platform_ids = _CARRYING_PLATFORM_BODY_IDS[facility.kind]
    out: list[_LocalBody] = []
    for local in local_bodies:
        if facility.kind == "charger" and local.body_id.startswith("drone_charging_pad."):
            station_index = int(local.body_id.rsplit(".", 1)[1])
            pieces = _partition_platform_rect(
                _local_rect(local),
                (pads[station_index],),
                CONTACT_BOUNDARY_GAP_M,
            )
            out.extend(
                _local_body_from_part(local, rect, index)
                for index, rect in enumerate(pieces)
            )
            continue
        if local.body_id in platform_ids:
            pieces = _partition_platform_rect(_local_rect(local), pads, CONTACT_BOUNDARY_GAP_M)
            out.extend(
                _local_body_from_part(local, rect, index)
                for index, rect in enumerate(pieces)
            )
            continue
        out.append(local)
    return tuple(out)


def _require_unique_model_names(physics: "FacilityPhysics") -> None:
    """Enforce model-name uniqueness within one materialization.

    The injective identifier encoding makes distinct facility ids map to
    distinct fragments; this guard additionally proves the compound contact and
    structural model names are all distinct for this facility rather than
    deferring the check to a future integration fix.
    """
    names = [contact.model_name for contact in physics.contacts]
    names.extend(body.model_name for body in physics.bodies)
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise FacilityPhysicsError(
            f"{physics.kind} {physics.facility_id} materializes duplicate model "
            f"names: {duplicates}"
        )


def _structural_body_from_local(
    facility: FacilityCapabilities,
    *,
    local: _LocalBody,
    support_y: float,
) -> "FacilitySolidBody":
    """Map one frontend root-local body (or partition piece) into the shared ENU
    frame.

    The rotation is the facility's own ``rotation_deg`` applied in the scene
    frame (east-positive about up); the scene->ENU signed map turns it into the
    identical ENU yaw, so ``yaw_deg == facility.rotation_deg``.
    """
    angle = math.radians(facility.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    scene_x = facility.position.x + cosine * local.x + sine * local.z
    scene_z = facility.position.z - sine * local.x + cosine * local.z
    up_center = local.y + support_y
    east_m, north_m, _ = _scene_to_enu(scene_x, 0.0, scene_z)
    return FacilitySolidBody(
        facility_id=facility.facility_id,
        body_id=local.body_id,
        model_name=_structural_model_name(
            kind=facility.kind,
            facility_id=facility.facility_id,
            body_id=local.body_id,
        ),
        frontend_mesh=local.mesh,
        frame=ENU_FRAME,
        east_m=east_m,
        north_m=north_m,
        up_bottom_m=up_center - local.size_y / 2.0,
        up_top_m=up_center + local.size_y / 2.0,
        size_east_m=local.size_x,
        size_north_m=local.size_z,
        yaw_deg=facility.rotation_deg,
        partition_of=local.partition_of,
    )


class FacilityPhysics(StrictModel):
    """Immutable ENU physical model of one facility: pad contacts + solid bodies."""

    facility_id: FacilityIdentifier
    kind: FacilityKind
    frame: ENUFrame
    contacts: tuple[FacilityPadContact, ...]
    bodies: tuple[FacilitySolidBody, ...]

    @field_validator("contacts", "bodies", mode="before")
    @classmethod
    def _collections_strict(cls, value: object, info) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @field_validator("frame", mode="before")
    @classmethod
    def _frame_strict(cls, value: object) -> str:
        if value != ENU_FRAME:
            raise ValueError("frame must be exactly 'enu_m'")
        return value


def facility_physics(facility: FacilityCapabilities) -> "FacilityPhysics":
    """Lower one accepted facility into exact ENU collision geometry.

    Landing contacts come from the accepted
    :func:`aero_bench.tasks.logistics.facility_geometry.facility_landing_pads`
    (which applies the frontend render-minimum and row-fit gates); carrying
    platforms are partitioned around the accepted pad footprints and every other
    structural body comes from the documented frontend model-box subset.  Every
    pad contact top equals the accepted pad ``y`` exactly, every body is
    converted through the explicit scene->ENU signed map with no recentring, and
    the returned model is rejected when model names collide, a structural body
    protrudes through a pad contact surface, a structural volume overlaps a pad
    contact slab, or a structural top is coplanar with a contact surface under
    its interior.
    """
    pads = facility_landing_pads(facility)
    if not pads:
        raise FacilityPhysicsError(
            f"{facility.kind} {facility.facility_id} lowers no landing contact"
        )
    support_y = facility.support_height_m if facility.support_height_m is not None else 0.0

    contacts = tuple(
        FacilityPadContact(
            facility_id=pad.facility_id,
            pad_index=pad.pad_index,
            model_name=_contact_model_name(facility.facility_id, pad.pad_index),
            frame=ENU_FRAME,
            east_m=pad.x,
            north_m=-pad.z,
            up_top_m=pad.y,
            thickness_m=PAD_CONTACT_THICKNESS_M[facility.kind],
            size_east_m=pad.width_m,
            size_north_m=pad.depth_m,
            yaw_deg=pad.rotation_deg,
        )
        for pad in pads
    )

    if facility.kind == "vertiport":
        local_bodies = _vertiport_bodies(facility)
    elif facility.kind == "hub":
        local_bodies = _hub_bodies(facility)
    else:
        assert facility.charging is not None
        local_bodies = _charger_bodies(facility.charging.slots)
    local_bodies = _partitioned_local_bodies(facility, local_bodies)

    bodies = tuple(
        _structural_body_from_local(facility, local=local, support_y=support_y)
        for local in sorted(local_bodies, key=lambda item: item.body_id)
    )

    physics = FacilityPhysics(
        facility_id=facility.facility_id,
        kind=facility.kind,
        frame=ENU_FRAME,
        contacts=contacts,
        bodies=bodies,
    )
    _require_unique_model_names(physics)
    if any_structure_protrudes_pad(physics):
        raise FacilityPhysicsError(
            f"{facility.kind} {facility.facility_id} structural body protrudes "
            "through a declared pad contact surface"
        )
    if any_body_overlaps_pad_slab(physics):
        raise FacilityPhysicsError(
            f"{facility.kind} {facility.facility_id} structural body overlaps a "
            "declared pad contact slab volume"
        )
    if any_structure_top_coplanar_under_contact(physics):
        raise FacilityPhysicsError(
            f"{facility.kind} {facility.facility_id} structural top face is "
            "coplanar with a declared pad contact surface under its interior"
        )
    return physics


def _box_model_sdf_block(
    *,
    model_name: str,
    east_m: float,
    north_m: float,
    up_center_m: float,
    yaw_deg: float,
    size_east_m: float,
    size_north_m: float,
    size_up_m: float,
) -> str:
    return (
        f'    <model name="{model_name}">\n'
        "      <static>true</static>\n"
        f"      <pose>{_format_number(east_m)} {_format_number(north_m)} "
        f"{_format_number(up_center_m)} 0 0 {_format_number(math.radians(yaw_deg))}</pose>\n"
        '      <link name="body">\n'
        '        <collision name="body_collision">\n'
        "          <geometry><box><size>"
        f"{_format_number(size_east_m)} {_format_number(size_north_m)} "
        f"{_format_number(size_up_m)}"
        "</size></box></geometry>\n"
        "        </collision>\n"
        '        <visual name="body_visual">\n'
        "          <geometry><box><size>"
        f"{_format_number(size_east_m)} {_format_number(size_north_m)} "
        f"{_format_number(size_up_m)}"
        "</size></box></geometry>\n"
        "        </visual>\n"
        "      </link>\n"
        "    </model>"
    )


def facility_sdf_model_blocks(facility: FacilityCapabilities) -> tuple[str, ...]:
    """Return deterministic SDF ``<model>`` blocks for one facility.

    Contact (``launch_pad.*``) models come first in pad order, then structural
    (``facility_*``) bodies in ``body_id`` order.  Each block is a
    self-contained static box model in the shared ENU world frame that a future
    run barrier can splice directly into the existing ``gazebo/scene.sdf``
    ``<world>``.
    """
    physics = facility_physics(facility)
    blocks: list[str] = []
    for contact in physics.contacts:
        blocks.append(
            _box_model_sdf_block(
                model_name=contact.model_name,
                east_m=contact.east_m,
                north_m=contact.north_m,
                up_center_m=contact.up_top_m - contact.thickness_m / 2.0,
                yaw_deg=contact.yaw_deg,
                size_east_m=contact.size_east_m,
                size_north_m=contact.size_north_m,
                size_up_m=contact.thickness_m,
            )
        )
    for body in physics.bodies:
        blocks.append(
            _box_model_sdf_block(
                model_name=body.model_name,
                east_m=body.east_m,
                north_m=body.north_m,
                up_center_m=(body.up_bottom_m + body.up_top_m) / 2.0,
                yaw_deg=body.yaw_deg,
                size_east_m=body.size_east_m,
                size_north_m=body.size_north_m,
                size_up_m=body.up_top_m - body.up_bottom_m,
            )
        )
    return tuple(blocks)


def facility_world_sdf_bytes(
    facility: FacilityCapabilities,
    *,
    origin: SceneOrigin,
) -> bytes:
    """Emit a standalone ENU SDF world document for one facility.

    The world carries the same ``spherical_coordinates`` declaration as
    :func:`aero_bench.world.scene_compiler.gazebo_sdf_bytes` so the emitted
    bodies belong in the same ENU origin plane; the body poses themselves are
    the explicit scene->ENU coordinates of :func:`facility_physics` and never
    re-centre.  This is a verification/assembler convenience: the actual run
    integration splices :func:`facility_sdf_model_blocks` into the compiled
    urban world instead of replacing it.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sdf version="1.10">',
        '  <world name="facility_physics_scene">',
        "    <spherical_coordinates>",
        "      <surface_model>EARTH_WGS84</surface_model>",
        "      <world_frame_orientation>ENU</world_frame_orientation>",
        f"      <latitude_deg>{_format_number(float(origin.latitude_deg))}</latitude_deg>",
        f"      <longitude_deg>{_format_number(float(origin.longitude_deg))}</longitude_deg>",
        f"      <elevation>{_format_number(float(origin.amsl_m))}</elevation>",
        "      <heading_deg>0</heading_deg>",
        "    </spherical_coordinates>",
    ]
    lines.extend(facility_sdf_model_blocks(facility))
    lines.extend(("  </world>", "</sdf>"))
    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = [
    "CONTACT_BOUNDARY_GAP_M",
    "ENUFrame",
    "ENU_FRAME",
    "FacilityPadContact",
    "FacilityPhysics",
    "FacilityPhysicsError",
    "FacilitySolidBody",
    "PadIndex",
    "PAD_CONTACT_THICKNESS_M",
    "any_body_overlaps_pad_slab",
    "any_structure_protrudes_pad",
    "any_structure_top_coplanar_under_contact",
    "body_overlaps_pad_slab",
    "facility_physics",
    "facility_physics_digest",
    "facility_sdf_model_blocks",
    "facility_world_sdf_bytes",
    "structure_protrudes_pad",
    "structure_protrudes_pad_contact",
    "structure_top_coplanar_under_contact_interior",
]
