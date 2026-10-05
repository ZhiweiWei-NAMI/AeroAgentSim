"""Materialize accepted logistics facility geometry into the real Gazebo world staging path.

The declared ``px4.gazebo`` Provider launches Gazebo from a paused copy of the
world its config selects: ``ensure_paused_world_sdf`` in
``containers/px4-gazebo/service.py`` reads ``config.world_sdf`` and copies it to
the runtime tmp dir with ``<paused>true</paused>`` before ``gz sim -s`` starts.
A logistics execution therefore has exactly one real seam: the world document it
puts in front of that staging helper must already carry every configured
facility's landing-contact and structural-collision geometry.

This module is that seam, and it is deliberately the *smallest* logistics-side
integration.  It consumes the committed acceptance geometry
(:func:`aero_bench.tasks.logistics.facility_physics.facility_physics` and
:func:`aero_bench.tasks.logistics.facility_physics.facility_sdf_model_blocks`)
and splices the resulting ``launch_pad.*``/``facility_*`` static models into an
existing ``<world>`` element, matching the splice contract the committed module
documents for the compiled ``gazebo/scene.sdf``.  It never replaces the urban
world, never invents a Provider or flight model, never spawns agents, and never
claims real pickup/delivery.

Contract
--------

* Input facilities are the canonical :class:`FacilityCatalogue` (or a sequence
  of :class:`FacilityCapabilities` lowered by
  :func:`aero_bench.tasks.logistics.facilities.compile_authored_facilities`).
  Each facility is lowered by ``facility_physics``; a facility the accepted
  geometry rejects (no pads, protrusion, slab overlap, coplanar contact, render
  minimums, ...) is re-raised as :class:`FacilityWorldError` naming the
  facility id.
* The base world must be a well-formed SDF with exactly one ``<world>`` that
  already declares ``spherical_coordinates`` (the compiled urban scene does).
  The declared coordinates must match the supplied :class:`SceneOrigin` to the
  documented tolerances: the facility bodies are already in that origin's ENU
  frame and are spliced verbatim, never re-centred.
* Model names must be unique across the *combined* world (base world plus every
  facility).  Collision geometry must be non-overlapping across the whole
  catalogue: a pad contact never overlaps another facility's pad-contact slab or
  structural body in 3D, and structural bodies from different facilities never
  overlap each other in 3D.  Pairs inside one facility are already validated by
  ``facility_physics``; this module adds exactly the cross-facility pairs.
  Cross-facility obstruction beyond a 3D intersection (for example a suspended
  body far above a pad) is intentionally out of scope: the accepted per-facility
  gates already protect every pad from its own facility's bodies.
* Output is deterministic: the same catalogue and base world always produce the
  same bytes, and the pads appear at their exact configured ENU coordinates and
  height with SDF pose units (metres translation, radians rotation) and the
  PX4 ``launch_pad.*``/``ground.*`` alias classification.
"""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence, TypeAlias

from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityCatalogue,
)
from aero_bench.tasks.logistics.facility_physics import (
    FacilityPadContact,
    FacilityPhysics,
    FacilitySolidBody,
    facility_physics,
    facility_sdf_model_blocks,
)
from aero_bench.world.scene_compiler import SceneOrigin

#: The only accepted ENU frame; the committed geometry module materializes every
#: contact and body in this exact frame.
ENU_FRAME = "enu_m"

#: Tolerances for matching the staged world's ``spherical_coordinates`` to the
#: compiled :class:`SceneOrigin` before splicing ENU geometry into it.  A
#: mismatched origin would silently place the pads kilometres away in WGS84.
_ORIGIN_LAT_LON_TOL_DEG = 1e-6
_ORIGIN_ELEVATION_TOL_M = 1e-3

_RectLike: TypeAlias = "FacilityPadContact | FacilitySolidBody"


class FacilityWorldError(ValueError):
    """A contract violation in the facility -> Gazebo world staging step."""


def _collect_facilities(
    facilities: FacilityCatalogue | Sequence[FacilityCapabilities],
) -> tuple[FacilityCapabilities, ...]:
    """Normalize the facilities input and reject unsupported/missing shapes."""
    if isinstance(facilities, FacilityCatalogue):
        items = tuple(facilities.facilities)
    elif isinstance(facilities, (tuple, list)):
        items = tuple(facilities)
    else:
        raise FacilityWorldError(
            "facilities must be a FacilityCatalogue or a sequence of "
            "FacilityCapabilities"
        )
    if not items:
        raise FacilityWorldError(
            "facility world staging requires at least one configured facility"
        )
    for index, facility in enumerate(items):
        if not isinstance(facility, FacilityCapabilities):
            raise FacilityWorldError(
                f"facilities[{index}] must be a FacilityCapabilities record"
            )
    facility_ids = [facility.facility_id for facility in items]
    if len(facility_ids) != len(set(facility_ids)):
        raise FacilityWorldError("facility ids must be unique across the catalogue")
    return items


def _axis_vectors(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """Unit axis vectors of an oriented rectangle in the ENU horizontal plane."""
    theta = math.radians(yaw_deg)
    cosine = math.cos(theta)
    sine = math.sin(theta)
    return (cosine, sine), (-sine, cosine)


def oriented_rectangles_have_positive_overlap(
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
    """Separating-axis predicate for two oriented ENU rectangles.

    Mirrors the accepted geometry module's boundary semantics: two boxes
    overlap with positive area iff every candidate axis sees one-dimensional
    projections that overlap with positive measure, and a mere boundary touch
    is never an overlap.
    """
    boxes = (
        (
            a_center_east_m,
            a_center_north_m,
            a_half_east_m,
            a_half_north_m,
            _axis_vectors(a_yaw_deg),
        ),
        (
            b_center_east_m,
            b_center_north_m,
            b_half_east_m,
            b_half_north_m,
            _axis_vectors(b_yaw_deg),
        ),
    )
    for axis in (*_axis_vectors(a_yaw_deg), *_axis_vectors(b_yaw_deg)):
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


def _horizontal_overlap(rect_a: _RectLike, rect_b: _RectLike) -> bool:
    return oriented_rectangles_have_positive_overlap(
        a_center_east_m=rect_a.east_m,
        a_center_north_m=rect_a.north_m,
        a_half_east_m=rect_a.size_east_m / 2.0,
        a_half_north_m=rect_a.size_north_m / 2.0,
        a_yaw_deg=rect_a.yaw_deg,
        b_center_east_m=rect_b.east_m,
        b_center_north_m=rect_b.north_m,
        b_half_east_m=rect_b.size_east_m / 2.0,
        b_half_north_m=rect_b.size_north_m / 2.0,
        b_yaw_deg=rect_b.yaw_deg,
    )


def pad_contacts_overlap_3d(a: FacilityPadContact, b: FacilityPadContact) -> bool:
    """``True`` when two pad-contact slabs intersect with positive volume.

    A pad contact occupies its full footprint from
    ``up_top_m - thickness_m`` to ``up_top_m``; the pair overlaps only when both
    the horizontal footprints and the contact-slab vertical intervals overlap.
    """
    if not _horizontal_overlap(a, b):
        return False
    a_bottom = a.up_top_m - a.thickness_m
    b_bottom = b.up_top_m - b.thickness_m
    return max(a_bottom, b_bottom) < min(a.up_top_m, b.up_top_m)


def pad_body_overlap_3d(contact: FacilityPadContact, body: FacilitySolidBody) -> bool:
    """``True`` when a pad-contact slab intersects a structural body volume."""
    if not _horizontal_overlap(contact, body):
        return False
    contact_bottom = contact.up_top_m - contact.thickness_m
    if body.up_top_m <= contact_bottom:
        return False
    if body.up_bottom_m >= contact.up_top_m:
        return False
    return True


def structural_bodies_overlap_3d(a: FacilitySolidBody, b: FacilitySolidBody) -> bool:
    """``True`` when two structural collision boxes intersect with positive volume."""
    if not _horizontal_overlap(a, b):
        return False
    if a.up_top_m <= b.up_bottom_m:
        return False
    if b.up_top_m <= a.up_bottom_m:
        return False
    return True


def _flatten_physics(
    physics_by_facility: dict[str, FacilityPhysics],
) -> tuple[
    list[tuple[str, FacilityPadContact]],
    list[tuple[str, FacilitySolidBody]],
]:
    contacts: list[tuple[str, FacilityPadContact]] = []
    bodies: list[tuple[str, FacilitySolidBody]] = []
    for facility_id, physics in physics_by_facility.items():
        contacts.extend((facility_id, contact) for contact in physics.contacts)
        bodies.extend((facility_id, body) for body in physics.bodies)
    return contacts, bodies


def _require_unique_combined_model_names(
    physics_by_facility: dict[str, FacilityPhysics],
) -> None:
    owner: dict[str, tuple[str, str, str]] = {}
    for facility_id, physics in physics_by_facility.items():
        for contact in physics.contacts:
            key = f"{facility_id}:pad:{contact.pad_index}"
            model_name = contact.model_name
            if model_name in owner:
                raise FacilityWorldError(
                    f"duplicate facility model name {model_name!r} across "
                    f"{owner[model_name]} and {key}"
                )
            owner[model_name] = key
        for body in physics.bodies:
            key = f"{facility_id}:body:{body.body_id}"
            model_name = body.model_name
            if model_name in owner:
                raise FacilityWorldError(
                    f"duplicate facility model name {model_name!r} across "
                    f"{owner[model_name]} and {key}"
                )
            owner[model_name] = key


def _cross_facility_collision_violations(
    physics_by_facility: dict[str, FacilityPhysics],
) -> tuple[str, ...]:
    """Explicit cross-facility landing-contact/collision violations.

    Only pairs from *different* facilities are checked: the accepted per-facility
    gates in ``facility_physics`` already prove one facility's pads and bodies do
    not collide inside that facility.
    """
    contacts, bodies = _flatten_physics(physics_by_facility)
    violations: list[str] = []

    for index in range(len(contacts)):
        a_facility, contact_a = contacts[index]
        for other_facility, contact_b in contacts[index + 1 :]:
            if a_facility == other_facility:
                continue
            if pad_contacts_overlap_3d(contact_a, contact_b):
                violations.append(
                    f"{a_facility} pad {contact_a.pad_index} overlapping "
                    f"{other_facility} pad {contact_b.pad_index}"
                )

    for a_facility, contact in contacts:
        for b_facility, body in bodies:
            if a_facility == b_facility:
                continue
            if pad_body_overlap_3d(contact, body):
                violations.append(
                    f"{a_facility} pad {contact.pad_index} overlaps "
                    f"{b_facility} structural body {body.body_id}"
                )

    for index in range(len(bodies)):
        a_facility, body_a = bodies[index]
        for other_facility, body_b in bodies[index + 1 :]:
            if a_facility == other_facility:
                continue
            if structural_bodies_overlap_3d(body_a, body_b):
                violations.append(
                    f"{a_facility} structural body {body_a.body_id} overlaps "
                    f"{other_facility} structural body {body_b.body_id}"
                )

    return tuple(dict.fromkeys(violations))


def combined_facility_physics(
    facilities: FacilityCatalogue | Sequence[FacilityCapabilities],
) -> tuple[FacilityPhysics, ...]:
    """Lower every configured facility and prove the combined geometry valid.

    Returns the deterministic per-facility :class:`FacilityPhysics` models in
    catalogue order after the combined model-name and cross-facility
    non-overlap gates pass.  Unsupported/missing inputs and accepted-geometry
    rejections raise :class:`FacilityWorldError` naming the facility.
    """
    items = _collect_facilities(facilities)
    physics_by_facility: dict[str, FacilityPhysics] = {}
    try:
        for facility in items:
            physics_by_facility[facility.facility_id] = facility_physics(facility)
    except ValueError as exc:
        # ``facility_physics`` rejects unsupported placements with both
        # ``FacilityPhysicsError`` and the accepted geometry module's plain
        # ``ValueError`` (render minimums, row fit, capability contract).
        # Every one of them is an explicit staging rejection, not a fallback.
        raise FacilityWorldError(str(exc)) from exc

    _require_unique_combined_model_names(physics_by_facility)
    violations = _cross_facility_collision_violations(physics_by_facility)
    if violations:
        raise FacilityWorldError(
            "cross-facility landing contact/collision overlap in the combined "
            "world: " + "; ".join(violations)
        )
    return tuple(physics_by_facility[item.facility_id] for item in items)


def facility_world_model_blocks(
    facilities: FacilityCatalogue | Sequence[FacilityCapabilities],
) -> tuple[str, ...]:
    """Deterministic SDF ``<model>`` blocks for every configured facility.

    Contacts come first per facility in pad order, then structural bodies in
    ``body_id`` order, facilities in catalogue order.  Every ``launch_pad.*``
    contact and ``facility_*`` body is emitted verbatim from the committed
    geometry module after :func:`combined_facility_physics` passes.
    """
    combined = combined_facility_physics(facilities)
    items = _collect_facilities(facilities)
    blocks: list[str] = []
    for facility in items:
        blocks.extend(facility_sdf_model_blocks(facility))
    assert len(blocks) == sum(
        len(physics.contacts) + len(physics.bodies) for physics in combined
    )
    return tuple(blocks)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _world_element(root: ET.Element) -> ET.Element:
    worlds = [
        element
        for element in root
        if _local_name(element.tag) == "world"
    ]
    if len(worlds) != 1:
        raise FacilityWorldError(
            f"source world SDF must declare exactly one <world>, found {len(worlds)}"
        )
    return worlds[0]


def _spherical_child_text(spherical: ET.Element, name: str) -> str:
    element = next(
        (candidate for candidate in spherical if _local_name(candidate.tag) == name),
        None,
    )
    if element is None or not element.text or not element.text.strip():
        raise FacilityWorldError(
            f"source world <{name}> is missing inside <spherical_coordinates>"
        )
    return element.text.strip()


def _spherical_float(spherical: ET.Element, name: str) -> float:
    text = _spherical_child_text(spherical, name)
    try:
        return float(text)
    except ValueError as exc:
        raise FacilityWorldError(
            f"source world <{name}> must be a finite number"
        ) from exc


def _validate_origin_matches_world(world: ET.Element, origin: SceneOrigin) -> None:
    spherical = next(
        (e for e in world if _local_name(e.tag) == "spherical_coordinates"), None
    )
    if spherical is None:
        raise FacilityWorldError(
            "source world must declare <spherical_coordinates>: the compiled "
            "urban scene does, and the facility ENU bodies are relative to that "
            "scene origin"
        )
    surface_model = _spherical_child_text(spherical, "surface_model")
    if surface_model != "EARTH_WGS84":
        raise FacilityWorldError(
            f"source world surface_model must be EARTH_WGS84, found {surface_model}"
        )

    latitude = _spherical_float(spherical, "latitude_deg")
    longitude = _spherical_float(spherical, "longitude_deg")
    elevation = _spherical_float(spherical, "elevation")
    if not math.isclose(
        latitude, float(origin.latitude_deg), rel_tol=0.0, abs_tol=_ORIGIN_LAT_LON_TOL_DEG
    ):
        raise FacilityWorldError(
            f"source world latitude {latitude} does not match the SceneOrigin "
            f"{float(origin.latitude_deg)}"
        )
    if not math.isclose(
        longitude, float(origin.longitude_deg), rel_tol=0.0, abs_tol=_ORIGIN_LAT_LON_TOL_DEG
    ):
        raise FacilityWorldError(
            f"source world longitude {longitude} does not match the SceneOrigin "
            f"{float(origin.longitude_deg)}"
        )
    if not math.isclose(
        elevation, float(origin.amsl_m), rel_tol=0.0, abs_tol=_ORIGIN_ELEVATION_TOL_M
    ):
        raise FacilityWorldError(
            f"source world elevation {elevation} does not match the SceneOrigin "
            f"amsl_m {float(origin.amsl_m)}"
        )


def facility_world_bytes(
    source: bytes | Path,
    *,
    facilities: FacilityCatalogue | Sequence[FacilityCapabilities],
    origin: SceneOrigin,
) -> bytes:
    """Return the base world document with every configured facility spliced in.

    ``source`` is either the compiled urban ``gazebo/scene.sdf`` bytes or the
    Path to it.  The returned document preserves every existing ``<world>``
    child (including ``<paused>`` and the scheduling/name state the staging
    helper must set later) and appends the facility ``<model>`` blocks after the
    combined geometry gates pass.
    """
    if not isinstance(origin, SceneOrigin):
        raise FacilityWorldError("origin must be a SceneOrigin")
    if isinstance(source, Path):
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FacilityWorldError(
                f"cannot read source world SDF {source}: {exc}"
            ) from exc
    elif isinstance(source, (bytes, bytearray)):
        try:
            text = bytes(source).decode("utf-8")
        except UnicodeError as exc:
            raise FacilityWorldError(
                "source world SDF bytes are not valid UTF-8 text"
            ) from exc
    else:
        raise FacilityWorldError(
            "source world must be SDF bytes or a Path to an SDF file"
        )
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FacilityWorldError(
            "source world SDF is not well-formed XML"
        ) from exc

    world = _world_element(root)
    _validate_origin_matches_world(world, origin)

    known: dict[str, str] = {}
    for model in world:
        if _local_name(model.tag) != "model" or model.attrib.get("name") is None:
            continue
        known[model.attrib["name"]] = "base world"

    for block in facility_world_model_blocks(facilities):
        try:
            model = ET.fromstring(block)
        except ET.ParseError as exc:  # pragma: no cover - committed blocks are SDF
            raise FacilityWorldError(
                "committed facility SDF model block is not parseable XML"
            ) from exc
        name = model.attrib.get("name")
        if name is None:  # pragma: no cover - committed blocks always name models
            raise FacilityWorldError(
                "committed facility SDF model block has no name attribute"
            )
        if name in known:
            raise FacilityWorldError(
                f"combined world already declares a model named {name!r} "
                f"(declared by {known[name]})"
            )
        known[name] = "configured facility"
        world.append(model)

    buffer = io.BytesIO()
    ET.ElementTree(root).write(buffer, encoding="utf-8", xml_declaration=True)
    return buffer.getvalue()


def materialize_facility_world(
    source: bytes | Path,
    destination: Path,
    *,
    facilities: FacilityCatalogue | Sequence[FacilityCapabilities],
    origin: SceneOrigin,
) -> Path:
    """Write the spliced world to ``destination`` (staging-helper compatible).

    This mirrors the declared staging helper's ``(source, destination)`` shape
    so a future logistics run barrier can hand the returned world copy to the
    real ``px4.gazebo`` Provider exactly where ``ensure_paused_world_sdf``
    reads its source.
    """
    if not isinstance(destination, Path):
        raise FacilityWorldError("destination must be a Path")
    payload = facility_world_bytes(
        source, facilities=facilities, origin=origin
    )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    except OSError as exc:
        raise FacilityWorldError(
            f"cannot write staged facility world {destination}: {exc}"
        ) from exc
    return destination


__all__ = [
    "ENU_FRAME",
    "FacilityWorldError",
    "combined_facility_physics",
    "facility_world_bytes",
    "facility_world_model_blocks",
    "materialize_facility_world",
    "oriented_rectangles_have_positive_overlap",
    "pad_body_overlap_3d",
    "pad_contacts_overlap_3d",
    "structural_bodies_overlap_3d",
]
