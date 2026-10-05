"""Independent verification of deterministic facility physical geometry.

The module under test lowers one accepted :class:`FacilityCapabilities` into
static ENU collision bodies and pad contacts and emits deterministic SDF
``<model>`` blocks.  These tests never measure a building and never reuse the
implementation's own arithmetic for ground truth: the cross-language pad and
body values are read from ``frontend_facility_physics_fixture.json``, which is
produced by executing the *real* frontend ``createFacilityVisual`` + three.js
``matrixWorld`` under vitest (see the generator next to the fixture), and the
frame/identity policy is asserted against the current authoring contracts
(``scene_east_south_m`` -> ENU signed map, PX4 ``launch_pad.*`` /
``ground.*`` classification).

The collision set is a *partition* of the actual carrying platforms: a carrying
platform (vertiport raised deck, hub sorting-hall roof, charger per-station pad
base) is never emitted as one solid box whose top coincides with the
``launch_pad.*`` contact surface.  Instead it is decomposed into collision
boxes that surround each pad footprint with ``CONTACT_BOUNDARY_GAP_M``
clearance.  The distinction is tested explicitly: ``partition_of is None``
bodies must match a frontend fixture box record 1:1, while partition pieces
carry the platform's ``frontend_mesh`` and ``partition_of`` provenance and are
verified to fill the platform footprint minus the pad columns.
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aero_bench.tasks.logistics.facilities import compile_authored_facilities
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_physics import (
    CONTACT_BOUNDARY_GAP_M,
    FacilityPadContact,
    FacilityPhysics,
    FacilityPhysicsError,
    FacilitySolidBody,
    any_body_overlaps_pad_slab,
    any_structure_top_coplanar_under_contact,
    body_overlaps_pad_slab,
    facility_physics,
    facility_physics_digest,
    facility_sdf_model_blocks,
    facility_world_sdf_bytes,
    structure_protrudes_pad,
    structure_protrudes_pad_contact,
    structure_top_coplanar_under_contact_interior,
)
from aero_bench.world.scene_compiler import SceneOrigin

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "logistics_facility_physics"
    / "frontend_facility_physics_fixture.json"
)

ABS_TOL = 1e-9
BODY_TOL = 1e-6

#: Frontend ``FACILITY_MIN_DIMENSIONS`` (city-facility-models.ts): (width, depth,
#: height) metres per kind.  Kept literal here as the independent renderer
#: contract, not imported back from the module under test.
RENDER_MINIMA_BY_KIND = {
    "vertiport": (12.0, 8.0, 4.0),
    "hub": (14.0, 11.0, 5.0),
    "charger": (10.0, 8.0, 3.2),
}

#: Structural body mesh names (frontend ``box(...)`` labels) the module lowers;
#: per-station charger bodies strip their trailing station number.
STRUCTURAL_MESH_NAMES = {
    "vertiport": {"raised landing deck", "landing deck support"},
    "hub": {"sorting hall", "sorting hall roof", "loading dock", "loading canopy",
            "dispatch office", "office roof"},
    "charger": {"drone charging pad", "charger control pedestal",
                "equipment canopy column", "solar equipment canopy",
                "power conversion cabinet"},
}

#: Frontend mesh names of the carrying platforms that are partitioned around the
#: accepted pad footprints (never emitted as a monolithic collision box).
PLATFORM_MESH_NAMES = {
    "vertiport": ("raised landing deck",),
    "hub": ("sorting hall roof",),
    "charger": ("drone charging pad",),
}

CASE_IDS = [
    "vp_ground_3pads_rot35",
    "vp_ground_1pad_rot90",
    "vp_rooftop_2pads_support150_rot90",
    "vp_rooftop_1pad_support150_rot30",
    "hub_ground_2pads_rot30",
    "hub_ground_1pad_rot0",
    "ch_ground_3pads_rot30",
    "ch_ground_1pad_rot90",
]


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _mesh_base(name: str) -> str:
    """Normalize per-station charger mesh labels to their base name."""
    for prefix in ("drone charging pad ", "charger control pedestal "):
        if name.startswith(prefix):
            return prefix.rstrip()
    return name


def _canonical_facility(facility: dict[str, object]):
    """Lower one v3 authoring facility (fixture shape) to the canonical model."""
    authored = {key: value for key, value in facility.items() if key != "design"}
    return compile_authored_facilities([authored]).facilities[0]


def _facility_of_kind(
    kind: str,
    facility_id: str,
    *,
    position: dict[str, float] | None = None,
    rotation_deg: float = 0.0,
    support_height_m: float | None = None,
    parking_slots: int = 1,
    slots: int = 3,
    width_m: float | None = None,
    depth_m: float | None = None,
) -> dict[str, object]:
    """Build one v3 authoring facility with explicit capabilities."""
    min_width, min_depth, min_height = RENDER_MINIMA_BY_KIND[kind]
    base: dict[str, object] = {
        "id": facility_id,
        "name": facility_id,
        "kind": kind,
        "placement": "rooftop" if support_height_m is not None else "ground",
        "buildingId": None if support_height_m is None else "building.0",
        "supportHeightM": support_height_m,
        "position": position if position is not None else {"x": 0, "z": 0},
        "rotationDeg": rotation_deg,
        "widthM": width_m if width_m is not None else min_width,
        "depthM": depth_m if depth_m is not None else min_depth,
        "heightM": min_height,
    }
    if kind == "vertiport":
        base["landing"] = {"parkingSlots": parking_slots, "movementsPerHour": 30}
        base["cargo"] = None
        base["charging"] = None
    elif kind == "hub":
        base["landing"] = {"parkingSlots": parking_slots, "movementsPerHour": 20}
        base["cargo"] = {"storageCapacityKg": 1000, "throughputPerHourKg": 1000}
        base["charging"] = None
    else:
        base["landing"] = None
        base["cargo"] = None
        base["charging"] = {
            "slots": slots,
            "powerW": 5000,
            "priceAmount": 1.0,
            "priceCurrency": "CNY",
            "priceUnit": "kWh",
        }
    return base


def _to_local_frame(
    east_m: float, north_m: float, facility
) -> tuple[float, float]:
    """Invert the scene->ENU signed map and the facility Y rotation.

    Given an ENU ``(east, north)`` position and a canonical facility, return the
    position in the facility's root-local (pre-rotation, pre-translation) frame,
    where the frontend box calls are authored in metres.
    """
    scene_dx = east_m - facility.position.x
    scene_dz = (-north_m) - facility.position.z
    angle = math.radians(facility.rotation_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        cosine * scene_dx - sine * scene_dz,
        sine * scene_dx + cosine * scene_dz,
    )


def _axis_rect(center_x: float, center_z: float, size_x: float, size_z: float):
    return (
        center_x - size_x / 2.0,
        center_x + size_x / 2.0,
        center_z - size_z / 2.0,
        center_z + size_z / 2.0,
    )


def _rect_separation(rect_a: tuple, rect_b: tuple) -> float:
    """EUCLID distance between two axis-aligned rectangles in the local frame."""
    dx = max((rect_a[0] - rect_b[1]), (rect_b[0] - rect_a[1]), 0.0)
    dz = max((rect_a[2] - rect_b[3]), (rect_b[2] - rect_a[3]), 0.0)
    return math.hypot(dx, dz)


def _rect_area(rect: tuple) -> float:
    return max(0.0, rect[1] - rect[0]) * max(0.0, rect[3] - rect[2])


def _union_area(rects: list[tuple]) -> float:
    """Inclusion-exclusion area over a small set of axis-aligned rectangles."""
    total = 0.0
    count = len(rects)
    for mask in range(1, 1 << count):
        inter: tuple | None = None
        bits = bin(mask).count("1")
        for index in range(count):
            if not (mask & (1 << index)):
                continue
            current = rects[index]
            if inter is None:
                inter = current
            else:
                inter = (
                    max(inter[0], current[0]),
                    min(inter[1], current[1]),
                    max(inter[2], current[2]),
                    min(inter[3], current[3]),
                )
        if inter is not None and inter[1] > inter[0] and inter[3] > inter[2]:
            sign = -1.0 if bits % 2 == 0 else 1.0
            total += sign * (inter[1] - inter[0]) * (inter[3] - inter[2])
    return total


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_bodies_and_pads_match_independently_captured_frontend_values(case_id: str) -> None:
    """Cross-language parity against the fixture from the REAL frontend.

    Pads are the accepted ``facilityLandingPads`` world rectangles read from the
    actual three.js ``matrixWorld``; bodies are the model-group box meshes.
    Every pad contact matches the fixture under the explicit signed frame map
    ``east = world.x, north = -world.z, up = world.y``.  Monolithic structural
    bodies (``partition_of is None``) match the corresponding frontend box
    records exactly; carrying-platform partition pieces are the deterministic
    collision decomposition of the frontend platform box (verified separately).
    """
    fixture = _load_fixture()
    case = next(c for c in fixture["cases"] if c["case_id"] == case_id)
    facility = _canonical_facility(case["facility"])
    physics = facility_physics(facility)
    kind = case["facility"]["kind"]

    # --- Landing contacts match the frontend matrixWorld pads exactly. ---
    assert len(physics.contacts) == len(case["pads"])
    for contact, pad in zip(physics.contacts, case["pads"]):
        assert contact.east_m == pytest.approx(pad["x"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.north_m == pytest.approx(-pad["z"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.up_top_m == pytest.approx(pad["y"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.size_east_m == pytest.approx(pad["width_m"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.size_north_m == pytest.approx(pad["depth_m"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.yaw_deg == pytest.approx(pad["rotation_deg"], rel=ABS_TOL, abs=ABS_TOL)
        assert contact.frame == "enu_m"
        assert contact.facility_id == pad["facility_id"]
        assert contact.pad_index == pad["pad_index"]

    # --- Monolithic structural bodies match the frontend model-group boxes. ---
    structural = [
        box for box in case["bodies"] if _mesh_base(box["mesh"]) in STRUCTURAL_MESH_NAMES[kind]
    ]
    platform_meshes = PLATFORM_MESH_NAMES[kind]
    platform_box_count = sum(
        1 for box in structural if _mesh_base(box["mesh"]) in platform_meshes
    )
    monolithic = [body for body in physics.bodies if body.partition_of is None]
    assert len(monolithic) == len(structural) - platform_box_count
    unmatched = []
    for body in monolithic:
        expected = [
            box for box in structural
            if _mesh_base(box["mesh"]) == _mesh_base(body.frontend_mesh)
            and _mesh_base(body.frontend_mesh) in STRUCTURAL_MESH_NAMES[kind]
        ]
        match = next(
            (
                box for box in expected
                if body.east_m == pytest.approx(box["world"]["x"], rel=BODY_TOL, abs=BODY_TOL)
                and body.north_m == pytest.approx(-box["world"]["z"], rel=BODY_TOL, abs=BODY_TOL)
                and body.up_bottom_m
                == pytest.approx(box["world"]["y"] - box["size_world"]["y"] / 2, rel=BODY_TOL, abs=BODY_TOL)
                and body.up_top_m
                == pytest.approx(box["world"]["y"] + box["size_world"]["y"] / 2, rel=BODY_TOL, abs=BODY_TOL)
                and body.size_east_m == pytest.approx(box["size_world"]["x"], rel=BODY_TOL, abs=BODY_TOL)
                and body.size_north_m == pytest.approx(box["size_world"]["z"], rel=BODY_TOL, abs=BODY_TOL)
                and body.yaw_deg == pytest.approx(box["yaw_deg"], rel=BODY_TOL, abs=BODY_TOL)
            ),
            None,
        )
        if match is None:
            unmatched.append(body.body_id)
    assert not unmatched, f"monolithic bodies with no frontend mesh twin: {unmatched}"

    # --- Every partition piece derives from a frontend platform box. ---
    partition = [body for body in physics.bodies if body.partition_of is not None]
    assert partition, "every carrying platform is partitioned; none may be monolithic"
    for body in partition:
        assert _mesh_base(body.frontend_mesh) in platform_meshes
        assert len(physics.bodies) == len({b.body_id for b in physics.bodies})
        # A platform piece must stay inside the real frontend platform box(es).
        expected = [
            box for box in structural
            if _mesh_base(box["mesh"]) == _mesh_base(body.frontend_mesh)
        ]
        local_x, local_z = _to_local_frame(body.east_m, body.north_m, facility)
        piece_rect = _axis_rect(local_x, local_z, body.size_east_m, body.size_north_m)
        assert any(
            _rect_separation(
                piece_rect,
                _axis_rect(
                    *((lambda xy: (xy[0], xy[1]))(
                        _to_local_frame(
                            box["world"]["x"], -box["world"]["z"], facility
                        )
                    )),
                    box["size_world"]["x"], box["size_world"]["z"],
                ),
            ) == pytest.approx(0.0, abs=BODY_TOL)
            for box in expected
        ), f"{body.body_id} escapes its frontend platform box"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_structural_volume_overlaps_a_pad_contact_slab(case_id: str) -> None:
    """For every kind and orientation, no solid body penetrates a ``launch_pad.*``
    contact slab volume, no protrusion exists, and no top face is coplanar with a
    contact surface under its interior."""
    fixture = _load_fixture()
    case = next(c for c in fixture["cases"] if c["case_id"] == case_id)
    facility = _canonical_facility(case["facility"])
    physics = facility_physics(facility)
    for body in physics.bodies:
        for contact in physics.contacts:
            assert body_overlaps_pad_slab(body, contact) is False, (
                f"{body.body_id} overlaps pad {contact.pad_index} slab"
            )
            assert structure_top_coplanar_under_contact_interior(body, contact) is False, (
                f"{body.body_id} top is coplanar with pad {contact.pad_index} contact"
            )
            assert structure_protrudes_pad(body, contact) is False, (
                f"{body.body_id} protrudes through pad {contact.pad_index}"
            )
    assert any_body_overlaps_pad_slab(physics) is False
    assert any_structure_top_coplanar_under_contact(physics) is False


def test_pad_contacts_are_exactly_the_accepted_facility_landing_pads() -> None:
    """Contacts must equal ``facility_landing_pads`` under the signed ENU map."""
    authored = _facility_of_kind("vertiport", "vp-map", parking_slots=2, rotation_deg=90)
    facility = _canonical_facility(authored)
    pads = facility_landing_pads(facility)
    physics = facility_physics(facility)
    assert len(physics.contacts) == len(pads)
    for contact, pad in zip(physics.contacts, pads):
        assert contact.east_m == pad.x
        assert contact.north_m == -pad.z
        assert contact.up_top_m == pad.y
        assert contact.size_east_m == pad.width_m
        assert contact.size_north_m == pad.depth_m
        assert contact.yaw_deg == pad.rotation_deg
        assert contact.pad_index == pad.pad_index
        assert contact.facility_id == pad.facility_id


@pytest.mark.parametrize("rotation_deg", [0.0, 90.0, 35.0])
def test_orientation_0_90_and_noncardinal_preserve_body_pad_identity(rotation_deg: float) -> None:
    """x-east pads rotate to ENU east/north exactly; no recentring or inference.

    A pad at the facility's local ``+x`` with ``rotation_deg = 90`` must land at
    scene ``z = -1`` (ENU north = +1), exactly the frontend three.js Y-rotation.
    """
    authored = _facility_of_kind(
        "vertiport", f"vp-orient-{int(rotation_deg)}",
        position={"x": 5.0, "z": -7.0}, rotation_deg=rotation_deg, parking_slots=2,
    )
    facility = _canonical_facility(authored)
    physics = facility_physics(facility)
    assert len(physics.contacts) == 2

    geometry = facility_landing_pads(facility)
    for contact, pad in zip(physics.contacts, geometry):
        # ``facility_landing_pads`` already applied the explicit scene-frame
        # rotation; the signed ENU map reproduces it exactly with no further
        # rotation: east = scene x, north = -scene z, up = scene y.
        expected_east = pad.x
        expected_north = -pad.z
        assert contact.east_m == pytest.approx(expected_east, abs=ABS_TOL)
        assert contact.north_m == pytest.approx(expected_north, abs=ABS_TOL)
        assert contact.up_top_m == pytest.approx(0.72, abs=ABS_TOL)
        assert contact.yaw_deg == rotation_deg
    # The +x pad of a 90 rotation must land on scene -z (ENU north == +x).
    if rotation_deg == 90.0:
        assert physics.contacts[1].north_m == pytest.approx(2.7 + 7.0, abs=ABS_TOL)

    # The carrying deck is partitioned around the pads: every deck piece keeps
    # the declared 0.2 m slab and the 0.72 m contact level, but none sits under a
    # pad interior.
    deck = [body for body in physics.bodies if body.partition_of == "raised_landing_deck"]
    assert len(deck) > 0
    for piece in deck:
        assert piece.frontend_mesh == "raised landing deck"
        assert piece.up_top_m == pytest.approx(0.72, abs=ABS_TOL)
        assert piece.up_bottom_m == pytest.approx(0.52, abs=ABS_TOL)
        for contact in physics.contacts:
            assert structure_top_coplanar_under_contact_interior(piece, contact) is False
            assert body_overlaps_pad_slab(piece, contact) is False


def test_rooftop_support_height_is_retained_not_inferred() -> None:
    """A mounted vertiport keeps the declared 150 m support unchanged across the
    partitioned deck and the pads."""
    authored = _facility_of_kind(
        "vertiport", "vp-roof", support_height_m=150.0,
        position={"x": 10.0, "z": -20.0}, rotation_deg=90, parking_slots=2,
    )
    facility = _canonical_facility(authored)
    physics = facility_physics(facility)
    assert all(contact.up_top_m == pytest.approx(150.72, abs=ABS_TOL) for contact in physics.contacts)
    assert all(body.up_top_m > 150.0 for body in physics.bodies if body.size_east_m > 1.0)
    deck = [body for body in physics.bodies if body.partition_of == "raised_landing_deck"]
    assert deck
    for piece in deck:
        assert piece.up_bottom_m == pytest.approx(150.52, abs=ABS_TOL)
        assert piece.up_top_m == pytest.approx(150.72, abs=ABS_TOL)
    # No fabricated extra height: the roof/support is exactly the declared value.
    assert max(contact.up_top_m for contact in physics.contacts) == pytest.approx(150.72, abs=ABS_TOL)


def test_structural_bodies_never_carry_ground_or_launch_pad_contact_identity() -> None:
    """PX4-style naming separation: only pads get ``launch_pad.*``.  The whole
    building is never labelled ``ground.*`` or ``launch_pad.*``."""
    for slot_count in (1, 3):
        authored = _facility_of_kind("charger", f"ch-px4-{slot_count}", slots=slot_count)
        physics = facility_physics(_canonical_facility(authored))
        assert len(physics.contacts) == slot_count
        for contact in physics.contacts:
            assert contact.model_name.startswith("launch_pad.")
            assert f"ch-px4-{slot_count}" in contact.model_name
        for body in physics.bodies:
            assert body.model_name.startswith("facility_")
            assert not body.model_name.startswith(("ground.", "launch_pad."))
    hub = _canonical_facility(_facility_of_kind("hub", "hub-px4"))
    for body in facility_physics(hub).bodies:
        assert not body.model_name.startswith(("ground.", "launch_pad."))
    vertiport = _canonical_facility(_facility_of_kind("vertiport", "vp-px4"))
    physics = facility_physics(vertiport)
    assert physics.contacts[0].model_name == "launch_pad.vp-px4.0"


def test_identifier_encoding_is_injective_for_delimiter_combinations() -> None:
    """Colon/underscore/dot/hyphen facility ids never share a model name.

    The old ``_`` fold made ``hub:wx`` and ``hub_wx`` collide.  The injective
    encoding escapes ``_`` as ``__`` and ``:`` as ``_3A``, keeps ``.``/``-``
    verbatim, and the factory enforces per-materialization uniqueness.
    """
    cases = {
        "hub:wx": "hub_3Awx",
        "hub_wx": "hub__wx",
        "hub.wx": "hub.wx",
        "hub-wx": "hub-wx",
        "hub_3A": "hub__3A",
        "hub:3A": "hub_3A3A",
    }
    physics_by_id: dict[str, FacilityPhysics] = {}
    for raw_id, expected_fragment in cases.items():
        physics = facility_physics(_canonical_facility(
            _facility_of_kind("hub", raw_id)
        ))
        physics_by_id[raw_id] = physics
        assert physics.contacts[0].model_name == f"launch_pad.{expected_fragment}.0"
        assert all(
            body.model_name.startswith(f"facility_hub_{expected_fragment}_")
            for body in physics.bodies
        )
        # Every materialization is internally unique (no reliance on a future
        # integration fix).
        names = [c.model_name for c in physics.contacts]
        names.extend(b.model_name for b in physics.bodies)
        assert len(names) == len(set(names)), raw_id

    # The previously-colliding pair now emits disjoint model-name populations.
    all_names: set[str] = set()
    for raw_id, physics in physics_by_id.items():
        owned = {c.model_name for c in physics.contacts}
        owned.update(b.model_name for b in physics.bodies)
        assert not (owned & all_names), raw_id
        all_names.update(owned)
    assert len(all_names) == sum(
        len(p.contacts) + len(p.bodies) for p in physics_by_id.values()
    )
    # The named pair that collided under the legacy fold are still distinct.
    assert physics_by_id["hub:wx"].contacts[0].model_name != (
        physics_by_id["hub_wx"].contacts[0].model_name
    )


def test_multiple_pads_preserve_pad_index_identity_and_carrying_partition() -> None:
    """Three vertiport pads keep stable indices, exact contact rects, and a deck
    partition that never occupies a pad column."""
    authored = _facility_of_kind(
        "vertiport", "vp-multi", parking_slots=3, rotation_deg=35,
        width_m=20.0, depth_m=20.0,
    )
    physics = facility_physics(_canonical_facility(authored))
    assert [c.pad_index for c in physics.contacts] == [0, 1, 2]
    assert len({c.model_name for c in physics.contacts}) == 3
    deck = [body for body in physics.bodies if body.partition_of == "raised_landing_deck"]
    assert len(deck) > 0
    assert len(physics.bodies) == len({b.body_id for b in physics.bodies})
    for piece in deck:
        assert piece.frontend_mesh == "raised landing deck"
        # The partition keeps the full declared deck slab height.
        assert piece.up_top_m - piece.up_bottom_m == pytest.approx(0.2, abs=ABS_TOL)
        for contact in physics.contacts:
            assert body_overlaps_pad_slab(piece, contact) is False


def test_carrying_platform_partition_surrounds_pads_with_explicit_gap() -> None:
    """Platform partition pieces surround each pad footprint with exactly
    ``CONTACT_BOUNDARY_GAP_M`` clearance: the closest structural face is never
    inside the declared full pad footprint."""
    for case_id in ("vp_ground_3pads_rot35", "hub_ground_2pads_rot30", "ch_ground_3pads_rot30"):
        fixture = _load_fixture()
        case = next(c for c in fixture["cases"] if c["case_id"] == case_id)
        facility = _canonical_facility(case["facility"])
        physics = facility_physics(facility)
        pad_rects = [
            _axis_rect(
                *_to_local_frame(contact.east_m, contact.north_m, facility),
                contact.size_east_m, contact.size_north_m,
            )
            for contact in physics.contacts
        ]
        pieces = [body for body in physics.bodies if body.partition_of is not None]
        assert pieces
        for piece in pieces:
            local_x, local_z = _to_local_frame(piece.east_m, piece.north_m, facility)
            piece_rect = _axis_rect(local_x, local_z, piece.size_east_m, piece.size_north_m)
            nearest = min(_rect_separation(piece_rect, pad) for pad in pad_rects)
            assert nearest >= CONTACT_BOUNDARY_GAP_M - 1e-9, (
                f"{piece.body_id} is {nearest:.6f} m from a pad, inside the "
                f"{CONTACT_BOUNDARY_GAP_M} m boundary gap"
            )


def test_partition_union_covers_platform_minus_pad_columns() -> None:
    """One representative case per kind: the partition pieces tile the exact
    frontend platform rectangle with every expanded pad column removed."""
    fixture = _load_fixture()
    representatives = ("vp_ground_1pad_rot90", "hub_ground_1pad_rot0", "ch_ground_1pad_rot90")
    for case_id in representatives:
        case = next(c for c in fixture["cases"] if c["case_id"] == case_id)
        facility = _canonical_facility(case["facility"])
        physics = facility_physics(facility)
        kind = case["facility"]["kind"]
        platform_mesh = PLATFORM_MESH_NAMES[kind][0]

        platform_rects: list[tuple] = []
        for box in case["bodies"]:
            if _mesh_base(box["mesh"]) != platform_mesh:
                continue
            local_x, local_z = _to_local_frame(
                box["world"]["x"], -box["world"]["z"], facility
            )
            platform_rects.append(
                _axis_rect(local_x, local_z, box["size_world"]["x"], box["size_world"]["z"])
            )
        padded_rects = [
            _axis_rect(
                pad["x"], pad["z"], pad["widthM"], pad["depthM"]
            )
            for pad in case["local_pads"]
        ]
        carve_rects = [
            (max(p[0] - CONTACT_BOUNDARY_GAP_M, rect[0]),
             min(p[1] + CONTACT_BOUNDARY_GAP_M, rect[1]),
             max(p[2] - CONTACT_BOUNDARY_GAP_M, rect[2]),
             min(p[3] + CONTACT_BOUNDARY_GAP_M, rect[3]))
            for rect in platform_rects
            for p in padded_rects
        ]
        piece_rects: list[tuple] = []
        for body in physics.bodies:
            if body.partition_of is None:
                continue
            local_x, local_z = _to_local_frame(body.east_m, body.north_m, facility)
            piece_rects.append(
                _axis_rect(local_x, local_z, body.size_east_m, body.size_north_m)
            )
        expected_area = _union_area(platform_rects) - _union_area(carve_rects)
        piece_area = _union_area(piece_rects)
        assert piece_area == pytest.approx(expected_area, abs=BODY_TOL)


def test_vertiport_deck_supports_remain_whole() -> None:
    """Deck supports stay monolithic (never partitioned), keep the frontend
    column sizes, and never overlap a pad slab volume.  Four columns sit under
    every declared pad (12 total for a 3-pad row)."""
    for slot_count, rot in ((1, 0), (3, 35)):
        physics = facility_physics(_canonical_facility(_facility_of_kind(
            "vertiport", f"vp-supports-{slot_count}", parking_slots=slot_count, rotation_deg=rot,
            width_m=20.0, depth_m=20.0,
        )))
        supports = [b for b in physics.bodies if b.body_id.startswith("landing_deck_support.")]
        assert len(supports) == 4 * slot_count
        for support in supports:
            assert support.partition_of is None
            assert support.frontend_mesh == "landing deck support"
            assert support.size_east_m == pytest.approx(0.42, abs=BODY_TOL)
            assert support.size_north_m == pytest.approx(0.28, abs=BODY_TOL)
            for contact in physics.contacts:
                assert body_overlaps_pad_slab(support, contact) is False


def test_structure_protrusion_predicates_are_separating_axis_exact() -> None:
    """The pad-protection predicate rejects only positive-area overlap above the
    contact top and treats boundary touches as non-overlap."""
    body = FacilitySolidBody(
        facility_id="vp-x", body_id="tall", model_name="facility_vertiport_vp-x_tall",
        frontend_mesh="raised landing deck", frame="enu_m",
        east_m=0.0, north_m=0.0, up_bottom_m=0.0, up_top_m=3.0,
        size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
    )
    below = FacilityPadContact(
        facility_id=body.facility_id, pad_index=0,
        model_name=f"launch_pad.{body.facility_id}.0", frame="enu_m",
        east_m=0.0, north_m=0.0, up_top_m=2.0, thickness_m=0.2,
        size_east_m=4.0, size_north_m=4.0, yaw_deg=0.0,
    )
    # Body rises through the pad -> protrusion.
    assert structure_protrudes_pad(body, below) is True
    assert structure_protrudes_pad_contact(
        body_east_m=0.0, body_north_m=0.0, body_size_east_m=2.0,
        body_size_north_m=2.0, body_up_top_m=3.0, body_yaw_deg=0.0,
        pad_east_m=0.0, pad_north_m=0.0, pad_size_east_m=4.0,
        pad_size_north_m=4.0, pad_up_top_m=2.0, pad_yaw_deg=0.0,
    ) is True
    # Carrying platform: top exactly level with the pad -> never a protrusion.
    level = body.model_copy(update={"up_top_m": below.up_top_m})
    assert structure_protrudes_pad(level, below) is False
    # Body fully below the pad top -> never a protrusion regardless of overlap.
    sunken = body.model_copy(update={"up_top_m": 1.0})
    assert structure_protrudes_pad(sunken, below) is False
    # Non-overlapping rectangle (tangent edge) but tall -> no protrusion.
    beside = below.model_copy(update={"east_m": 4.0})
    assert structure_protrudes_pad(body, beside) is False
    # A rotated thin strip crossing the body only overlaps because the
    # separating-axis test accounts for rotation, not just axis-aligned bounds.
    rotated_overlap = below.model_copy(update={"yaw_deg": 45.0,
                                                "size_east_m": 8.0, "size_north_m": 0.2})
    assert structure_protrudes_pad(body, rotated_overlap) is True
    # The same strip moved diagonally clear of the body's convex hull misses it.
    from aero_bench.tasks.logistics.facility_physics import FacilitySolidBody as BodyCls
    shifted = BodyCls(
        facility_id="vp-x", body_id="shifted", model_name="facility_vertiport_vp-x_shifted",
        frontend_mesh="raised landing deck", frame="enu_m",
        east_m=3.0, north_m=-3.0, up_bottom_m=0.0, up_top_m=3.0,
        size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
    )
    assert structure_protrudes_pad(shifted, rotated_overlap) is False
    # Tolerance: tiny above-threshold rise ignored up to the requested tolerance.
    assert structure_protrudes_pad(
        body, below, tolerance_m=1.2,
    ) is False


def test_slab_overlap_and_coplanar_predicates_treat_boundary_as_clear() -> None:
    """Volume-overlap and coplanar-top predicates return False for a body merely
    touching a pad boundary, and True for real penetration/coincidence."""
    body = FacilitySolidBody(
        facility_id="vp-x", body_id="carrier", model_name="facility_vertiport_vp-x_carrier",
        frontend_mesh="raised landing deck", frame="enu_m",
        east_m=0.0, north_m=0.0, up_bottom_m=0.0, up_top_m=0.72,
        size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
    )
    contact = FacilityPadContact(
        facility_id="vp-x", pad_index=0, model_name="launch_pad.vp-x.0", frame="enu_m",
        east_m=0.0, north_m=0.0, up_top_m=0.72, thickness_m=0.2,
        size_east_m=4.0, size_north_m=4.0, yaw_deg=0.0,
    )
    # Coplanar top with any positive-area overlap is the forbidden case.
    assert structure_top_coplanar_under_contact_interior(body, contact) is True
    assert body_overlaps_pad_slab(body, contact) is True
    # A body whose top is exactly level but whose footprint only touches the pad
    # perimeter (tangent edge) is NOT under the contact interior.
    tangent = body.model_copy(update={"east_m": 3.0})
    assert structure_top_coplanar_under_contact_interior(tangent, contact) is False
    # A body below the slab bottom never overlaps the slab volume.
    below = body.model_copy(update={"up_top_m": 0.51})
    assert structure_top_coplanar_under_contact_interior(below, contact) is False
    assert body_overlaps_pad_slab(below, contact) is False


def test_facility_physics_rejects_a_synthetic_protrusion(monkeypatch) -> None:
    """The published factory routes protrusions through the shared guard and
    refuses the whole facility when any structural body pierces a pad."""
    physiology = FacilityPhysics(
        facility_id="vp-synth",
        kind="vertiport",
        frame="enu_m",
        contacts=(
            FacilityPadContact(
                facility_id="vp-synth", pad_index=0,
                model_name="launch_pad.vp-synth.0", frame="enu_m",
                east_m=0.0, north_m=0.0, up_top_m=0.72, thickness_m=0.2,
                size_east_m=4.2, size_north_m=4.2, yaw_deg=0.0,
            ),
        ),
        bodies=(
            FacilitySolidBody(
                facility_id="vp-synth", body_id="tall", model_name="facility_vertiport_vp-synth_tall",
                frontend_mesh="raised landing deck", frame="enu_m",
                east_m=0.0, north_m=0.0, up_bottom_m=0.0, up_top_m=5.0,
                size_east_m=4.0, size_north_m=4.0, yaw_deg=0.0,
            ),
        ),
    )
    from aero_bench.tasks.logistics.facility_physics import any_structure_protrudes_pad
    assert any_structure_protrudes_pad(physiology) is True

    # The factory uses the same predicate as its release gate: force it to
    # report a protrusion and verify the whole lowering is refused.
    import aero_bench.tasks.logistics.facility_physics as fp_module
    authored = _canonical_facility(_facility_of_kind("vertiport", "vp-gate"))
    with monkeypatch.context() as patch:
        patch.setattr(
            fp_module, "any_structure_protrudes_pad",
            lambda _physics, tolerance_m=0.0: True,
        )
        with pytest.raises(FacilityPhysicsError, match="protrudes"):
            fp_module.facility_physics(authored)


def test_facility_physics_rejects_slab_overlap_and_coplanar_contact(monkeypatch) -> None:
    """The factory also routes the partition invariants through shared guards:
    a structural volume overlapping a pad slab and a coplanar top under a pad
    interior both refuse the whole facility."""
    import aero_bench.tasks.logistics.facility_physics as fp_module
    authored = _canonical_facility(_facility_of_kind("vertiport", "vp-gate-slab"))
    with monkeypatch.context() as patch:
        patch.setattr(
            fp_module, "any_body_overlaps_pad_slab",
            lambda _physics, tolerance_m=0.0: True,
        )
        with pytest.raises(FacilityPhysicsError, match="contact slab"):
            fp_module.facility_physics(authored)

    authored = _canonical_facility(_facility_of_kind("hub", "hub-gate-coplanar"))
    with monkeypatch.context() as patch:
        patch.setattr(
            fp_module, "any_structure_top_coplanar_under_contact",
            lambda _physics, tolerance_m=0.0: True,
        )
        with pytest.raises(FacilityPhysicsError, match="coplanar"):
            fp_module.facility_physics(authored)


def test_deterministic_sdf_blocks_and_digest() -> None:
    """Same facility -> identical blocks and digest; any coordinate or identity
    change alters the digest; output is stable byte-for-byte."""
    authored = _facility_of_kind("hub", "hub-det", position={"x": 3.0, "z": 4.0},
                                 rotation_deg=30, parking_slots=2)
    facility = _canonical_facility(authored)
    blocks_a = facility_sdf_model_blocks(facility)
    blocks_b = facility_sdf_model_blocks(facility)
    assert blocks_a == blocks_b
    physics = facility_physics(facility)
    assert len(blocks_a) == len(physics.contacts) + len(physics.bodies)

    world_a = facility_world_sdf_bytes(facility, origin=SceneOrigin())
    world_b = facility_world_sdf_bytes(facility, origin=SceneOrigin())
    assert world_a == world_b

    digest_a = facility_physics_digest(physics)
    digest_b = facility_physics_digest(facility_physics(facility))
    assert digest_a == digest_b
    assert len(digest_a) == 64
    assert all(char in "0123456789abcdef" for char in digest_a)

    moved = _canonical_facility(_facility_of_kind(
        "hub", "hub-det", position={"x": 3.001, "z": 4.0},
        rotation_deg=30, parking_slots=2,
    ))
    assert facility_physics_digest(facility_physics(moved)) != digest_a

    # A partition change must alter the digest too (e.g. body_id identity).
    renamed = _canonical_facility(_facility_of_kind(
        "hub", "hub-dot", position={"x": 3.0, "z": 4.0},
        rotation_deg=30, parking_slots=2,
    ))
    assert facility_physics_digest(facility_physics(renamed)) != digest_a


def test_sdf_pose_top_equals_pad_contact_top_and_blocks_are_parseable() -> None:
    """Each emitted contact model's box top (pose up + thickness/2) is exactly
    the accepted pad contact surface, and the whole world document parses."""
    authored = _facility_of_kind("vertiport", "vp-sdf", parking_slots=1, rotation_deg=0)
    facility = _canonical_facility(authored)
    blocks = facility_sdf_model_blocks(facility)
    physics = facility_physics(facility)
    contact = physics.contacts[0]
    first = blocks[0]
    # Grid axis: the contact block comes first, before all structural blocks.
    assert f'<model name="{contact.model_name}">' in first
    pose = [line for line in first.splitlines() if "<pose>" in line][0]
    values = pose.replace("<pose>", "").replace("</pose>", "").split()
    east, north, up_center, _, _, yaw = (float(value) for value in values)
    assert east == pytest.approx(contact.east_m, abs=ABS_TOL)
    assert north == pytest.approx(contact.north_m, abs=ABS_TOL)
    assert up_center == pytest.approx(contact.up_top_m - contact.thickness_m / 2.0, abs=ABS_TOL)
    assert up_center + contact.thickness_m / 2.0 == pytest.approx(contact.up_top_m, abs=ABS_TOL)
    assert yaw == pytest.approx(math.radians(contact.yaw_deg), abs=ABS_TOL)

    # Every block parses and carries exactly one static model with one box.
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<sdf version="1.10">\n  <world name="t">\n'
        + "\n".join(blocks)
        + "\n  </world>\n</sdf>\n"
    )
    root = ET.fromstring(document)
    model_names = [model.attrib["name"] for model in root.findall("world/model")]
    assert len(model_names) == len(blocks)
    assert model_names[0].startswith("launch_pad.")
    assert all(name.startswith("facility_") for name in model_names[1:])
    assert len(model_names) == len(set(model_names))
    for model in root.findall("world/model"):
        box_size = model.find("link/collision/geometry/box/size")
        assert box_size is not None and box_size.text is not None
        assert len(box_size.text.split()) == 3

    world = facility_world_sdf_bytes(facility, origin=SceneOrigin())
    ET.fromstring(world.decode("utf-8"))


def test_malformed_configs_are_rejected_by_existing_validation_contracts() -> None:
    """Below-render-minima dimensions, over-capacity charger stations, rooftop
    without a declared support, and ground with a support are all rejected by
    the actual existing validation APIs."""
    # Dimensions below the actual renderer minimum are refused by the accepted
    # pad materialization before any physics is built.
    small = _facility_of_kind("vertiport", "vp-small", width_m=10.0, depth_m=8.0)
    with pytest.raises(ValueError, match="at least 12"):
        facility_physics(_canonical_facility(small))

    # A standalone charger model holds at most three stations.
    four_slots = _facility_of_kind("charger", "ch-four", slots=4)
    with pytest.raises(ValueError, match="at most 3"):
        facility_physics(_canonical_facility(four_slots))

    # Rooftop placement requires a bound building and a positive support height.
    unsupported_roof = _facility_of_kind("vertiport", "vp-roof-bad", support_height_m=150.0)
    unsupported_roof["buildingId"] = None  # binding removed
    with pytest.raises(ValueError, match="must bind a building_id and support height"):
        compile_authored_facilities([unsupported_roof])

    # Ground sites cannot carry a building binding/support.
    ground_with_support = _facility_of_kind("vertiport", "vp-gnd-bad", support_height_m=None)
    ground_with_support["supportHeightM"] = 10.0
    with pytest.raises(ValueError, match="ground placement"):
        compile_authored_facilities([ground_with_support])

    # A hub must declare cargo, a charger must declare charging, and pads that
    # cannot fit the declared width are refused by the accepted row-fit gate.
    hub_no_cargo = _facility_of_kind("hub", "hub-no-cargo")
    hub_no_cargo["cargo"] = None
    with pytest.raises(ValueError, match="hub cargo"):
        compile_authored_facilities([hub_no_cargo])
    charger_no_charging = _facility_of_kind("charger", "ch-no-charging")
    charger_no_charging["charging"] = None
    with pytest.raises(ValueError, match="charging"):
        compile_authored_facilities([charger_no_charging])


def test_missing_and_ordered_constructors_reject_invalid_records() -> None:
    """Malformed typed records are refused: non-finite coordinates, non-positive
    sizes, an inverted vertical extent, and an empty partition_of label."""
    with pytest.raises(ValueError, match="finite"):
        FacilityPadContact(
            facility_id="x", pad_index=0, model_name="launch_pad.x.0", frame="enu_m",
            east_m=float("nan"), north_m=0.0, up_top_m=1.0, thickness_m=0.2,
            size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
        )
    with pytest.raises(ValueError, match="positive"):
        FacilityPadContact(
            facility_id="x", pad_index=0, model_name="launch_pad.x.0", frame="enu_m",
            east_m=0.0, north_m=0.0, up_top_m=1.0, thickness_m=0.2,
            size_east_m=-2.0, size_north_m=2.0, yaw_deg=0.0,
        )
    with pytest.raises(ValueError, match="above"):
        FacilitySolidBody(
            facility_id="x", body_id="bad", model_name="facility_hub_x_bad",
            frontend_mesh="sorting hall", frame="enu_m",
            east_m=0.0, north_m=0.0, up_bottom_m=2.0, up_top_m=2.0,
            size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
        )
    with pytest.raises(ValueError, match="enu_m"):
        FacilitySolidBody(
            facility_id="x", body_id="bad-frame", model_name="facility_hub_x_bad-frame",
            frontend_mesh="sorting hall", frame="moon",
            east_m=0.0, north_m=0.0, up_bottom_m=0.0, up_top_m=1.0,
            size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
        )
    with pytest.raises(ValueError, match="above"):
        FacilitySolidBody(
            facility_id="x", body_id="unsorted", model_name="facility_hub_x_unsorted",
            frontend_mesh="loading canopy", frame="enu_m",
            east_m=0.0, north_m=0.0, up_bottom_m=4.0, up_top_m=3.0,
            size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
        )
    with pytest.raises(ValueError, match="partition_of"):
        FacilitySolidBody(
            facility_id="x", body_id="bad-part", model_name="facility_hub_x_bad-part",
            frontend_mesh="sorting hall roof", frame="enu_m",
            east_m=0.0, north_m=0.0, up_bottom_m=0.0, up_top_m=1.0,
            size_east_m=2.0, size_north_m=2.0, yaw_deg=0.0,
            partition_of="",
        )


def test_no_recentring_explicit_signed_enu_map() -> None:
    """Body centres are the facility position plus the exact rotated frontend
    offset under the signed map; there is no normalisation, averaging, or
    inferred origin.  A facility far from the origin keeps its absolute pose."""
    authored = _facility_of_kind(
        "hub", "hub-far", position={"x": 315.0, "z": -412.0}, rotation_deg=90,
    )
    facility = _canonical_facility(authored)
    physics = facility_physics(facility)

    angle = math.radians(90.0)
    # A selected-city 1-pad hub row is centred on the footprint origin
    # (``selectedFacilityLandingPads``), so the dynamic sorting hall follows the
    # row centre at local x = 0.
    expected_east = 315.0 + math.cos(angle) * 0.0 + math.sin(angle) * -1.155
    expected_north = -(-412.0 - math.sin(angle) * 0.0 + math.cos(angle) * -1.155)
    hall = next(body for body in physics.bodies if body.body_id == "sorting_hall")
    assert hall.east_m == pytest.approx(expected_east, abs=ABS_TOL)
    assert hall.north_m == pytest.approx(expected_north, abs=ABS_TOL)
    assert hall.up_bottom_m == pytest.approx(0.225, abs=ABS_TOL)
    assert hall.up_top_m == pytest.approx(3.125, abs=ABS_TOL)
    assert max(abs(body.east_m) for body in physics.bodies) > 300.0
    assert max(abs(body.north_m) for body in physics.bodies) > 400.0


def test_charger_body_counts_scale_with_stations_and_thickness_below_top() -> None:
    """Per-station pads and pedestals double with each station; each pad base is
    partitioned into a ring around its 2.2 x 2.2 m pad and every contact slab is
    entirely below the declared contact surface."""
    single = facility_physics(_canonical_facility(_facility_of_kind("charger", "ch-1", slots=1)))
    triple = facility_physics(_canonical_facility(_facility_of_kind("charger", "ch-3", slots=3)))
    single_base_parts = [b for b in single.bodies if b.partition_of == "drone_charging_pad.0"]
    triple_parts = [b for b in triple.bodies if b.partition_of is not None and b.partition_of.startswith("drone_charging_pad.")]
    triple_ped = [b for b in triple.bodies if b.body_id.startswith("charger_control_pedestal.")]
    assert len(single_base_parts) == 4
    assert len(triple_parts) == 12
    assert len(triple_ped) == 3
    for contact in triple.contacts:
        assert contact.up_top_m == pytest.approx(0.12, abs=ABS_TOL)
        assert contact.thickness_m == pytest.approx(0.1, abs=ABS_TOL)
        assert contact.up_top_m - contact.thickness_m == pytest.approx(0.02, abs=ABS_TOL)
    for piece in triple_parts:
        assert piece.up_top_m == pytest.approx(0.12, abs=ABS_TOL)
        assert piece.up_bottom_m == pytest.approx(0.02, abs=ABS_TOL)
        for contact in triple.contacts:
            assert body_overlaps_pad_slab(piece, contact) is False
    assert {piece.body_id for piece in single_base_parts} == {
        "drone_charging_pad.0.part0",
        "drone_charging_pad.0.part1",
        "drone_charging_pad.0.part2",
        "drone_charging_pad.0.part3",
    }
    for single_piece in single_base_parts:
        assert single_piece.partition_of == "drone_charging_pad.0"
        assert single_piece.up_top_m == pytest.approx(0.12, abs=ABS_TOL)
        assert single_piece.up_bottom_m == pytest.approx(0.02, abs=ABS_TOL)
    assert any(b.body_id == "solar_equipment_canopy" for b in triple.bodies)
    assert any(b.body_id == "power_conversion_cabinet" for b in triple.bodies)


def test_hub_bodies_cover_building_and_load_platforms_not_lights() -> None:
    """The hub collision set is the actual building/roof/dock bodies only; the
    decorative light fascia, status lamps and lane stripes are excluded, and the
    roof is a partition around the pads instead of one coplanar slab."""
    physics = facility_physics(_canonical_facility(_facility_of_kind("hub", "hub-cov")))
    body_ids = {body.body_id for body in physics.bodies}
    partition_ids = {body.body_id for body in physics.bodies if body.partition_of is not None}
    assert partition_ids == {"sorting_hall_roof.part0", "sorting_hall_roof.part1",
                             "sorting_hall_roof.part2", "sorting_hall_roof.part3"}
    assert body_ids >= {"sorting_hall", "loading_dock", "loading_canopy",
                        "dispatch_office", "dispatch_office_roof"}
    assert not body_ids & {
        "canopy light strip", "bay status lamp", "dock lane stripe",
        "roof clerestory", "office mullion",
    }
    assert all(
        body.frontend_mesh not in {
            "canopy light strip", "bay status lamp", "dock lane stripe",
            "roof clerestory", "office mullion",
        }
        for body in physics.bodies
    )
    # The roof carries the hub pads: every roof piece keeps the declared contact
    # level but never occupies a pad column.
    roof = [b for b in physics.bodies if b.partition_of == "sorting_hall_roof"]
    assert len(roof) == 4
    for piece in roof:
        assert piece.frontend_mesh == "sorting hall roof"
        assert piece.up_top_m == pytest.approx(3.3625, abs=ABS_TOL)
        assert piece.up_bottom_m == pytest.approx(3.1375, abs=ABS_TOL)
        for contact in physics.contacts:
            assert body_overlaps_pad_slab(piece, contact) is False
            assert structure_top_coplanar_under_contact_interior(piece, contact) is False
    assert all(contact.up_top_m == pytest.approx(3.3625, abs=ABS_TOL) for contact in physics.contacts)


@pytest.mark.parametrize("kind,count,rotation_deg", [
    ("vertiport", 1, 0.0), ("vertiport", 2, 90.0), ("vertiport", 3, 35.0),
    ("hub", 1, 0.0), ("hub", 2, 90.0), ("hub", 3, 35.0),
])
def test_full_surface_and_structural_support_coverage_across_orientations(
    kind: str, count: int, rotation_deg: float,
) -> None:
    """Every accepted pad's footprint sits inside the continuous carrying
    platform and (vertiport) has four load-bearing columns beneath it, for
    1/2/3 pads at 0/90/noncardinal orientation.

    The carrying platform is partitioned around the pads with
    ``CONTACT_BOUNDARY_GAP_M`` columns, so the collision pieces PLUS the padded
    pad columns must tile one single continuous rectangle whose extents contain
    every pad footprint: that is the contract that a padded deck/roof column
    never hangs unsupported over empty air.
    """
    authored = _facility_of_kind(
        kind, f"{kind}-cover-{count}", parking_slots=count,
        rotation_deg=rotation_deg, width_m=20.0, depth_m=20.0,
    )
    facility = _canonical_facility(authored)
    physics = facility_physics(facility)
    platform_mesh = PLATFORM_MESH_NAMES[kind][0]
    pieces = [
        _axis_rect(
            *_to_local_frame(body.east_m, body.north_m, facility),
            body.size_east_m, body.size_north_m,
        )
        for body in physics.bodies if _mesh_base(body.frontend_mesh) == platform_mesh
    ]
    assert pieces
    columns = [
        _axis_rect(
            *_to_local_frame(contact.east_m, contact.north_m, facility),
            contact.size_east_m, contact.size_north_m,
        )
        for contact in physics.contacts
    ]
    padded = [
        (r[0] - CONTACT_BOUNDARY_GAP_M, r[1] + CONTACT_BOUNDARY_GAP_M,
         r[2] - CONTACT_BOUNDARY_GAP_M, r[3] + CONTACT_BOUNDARY_GAP_M)
        for r in columns
    ]
    # Union of collision pieces + protected pad columns tiles exactly the
    # platform's outer bounding rectangle (no gaps, no dangling slivers).
    combined = pieces + padded
    outer = (
        min(rect[0] for rect in combined),
        max(rect[1] for rect in combined),
        min(rect[2] for rect in combined),
        max(rect[3] for rect in combined),
    )
    assert _union_area(combined) == pytest.approx(_rect_area(outer), abs=BODY_TOL)
    # Every pad footprint is fully inside that single continuous platform rect.
    for contact in physics.contacts:
        lx, lz = _to_local_frame(contact.east_m, contact.north_m, facility)
        rect = _axis_rect(lx, lz, contact.size_east_m, contact.size_north_m)
        assert rect[0] >= outer[0] - BODY_TOL and rect[1] <= outer[1] + BODY_TOL
        assert rect[2] >= outer[2] - BODY_TOL and rect[3] <= outer[3] + BODY_TOL
        assert lz == pytest.approx(
            0.08 if kind == "vertiport" else -1.155, abs=ABS_TOL
        )
    if kind == "vertiport":
        supports = [b for b in physics.bodies if b.body_id.startswith("landing_deck_support.")]
        assert len(supports) == 4 * count
        for support in supports:
            assert support.partition_of is None


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_sdf_contact_volumes_disjoint_from_structural_collision_and_injective_names(
    case_id: str,
) -> None:
    """Emitted SDF contact boxes and structural boxes never share volume, and
    every model name in one world document is unique.

    Each SDF ``<pose>``/``<size>`` is a static box in the ENU world frame whose
    yaw is the facility's own rotation, so contact/structure separation is
    decided by a separating-axis test on the horizontal rectangles plus a strict
    vertical-interval check (boundary touches never overlap).
    """
    fixture = _load_fixture()
    case = next(c for c in fixture["cases"] if c["case_id"] == case_id)
    facility = _canonical_facility(case["facility"])
    blocks = facility_sdf_model_blocks(facility)
    boxes: list[tuple[str, tuple[float, float, float], tuple[float, float, float]]] = []
    for block in blocks:
        model = ET.fromstring(block)
        name = model.attrib["name"]
        pose = [float(v) for v in model.find("pose").text.split()]
        size = [float(v) for v in model.find("link/collision/geometry/box/size").text.split()]
        boxes.append((name, (pose[0], pose[1], pose[2]), (size[0], size[1], size[2])))
    names = [name for name, _, _ in boxes]
    assert len(names) == len(set(names))
    contacts = [(name, pose, size) for name, pose, size in boxes if name.startswith("launch_pad.")]
    structures = [(name, pose, size) for name, pose, size in boxes if name.startswith("facility_")]
    assert contacts and structures
    for contact_name, cp, cs in contacts:
        contact_up = (cp[2] - cs[2] / 2.0, cp[2] + cs[2] / 2.0)
        for structure_name, sp, ss in structures:
            structure_up = (sp[2] - ss[2] / 2.0, sp[2] + ss[2] / 2.0)
            if contact_up[0] >= structure_up[1] or contact_up[1] <= structure_up[0]:
                continue
            overlaps = _oriented_rectangles_overlap(
                center_a_east=cp[0], center_a_north=cp[1],
                half_a_east=cs[0] / 2.0, half_a_north=cs[1] / 2.0,
                yaw_a_deg=facility.rotation_deg,
                center_b_east=sp[0], center_b_north=sp[1],
                half_b_east=ss[0] / 2.0, half_b_north=ss[1] / 2.0,
                yaw_b_deg=facility.rotation_deg,
            )
            assert not overlaps, (
                f"{contact_name} shares volume with {structure_name} in {case_id}"
            )


def _oriented_rectangles_overlap(
    *,
    center_a_east: float,
    center_a_north: float,
    half_a_east: float,
    half_a_north: float,
    yaw_a_deg: float,
    center_b_east: float,
    center_b_north: float,
    half_b_east: float,
    half_b_north: float,
    yaw_b_deg: float,
) -> bool:
    """Separating-axis predicate for two horizontal oriented rectangles.

    Two boxes overlap with positive area iff every candidate axis (the two local
    axes of each box) sees overlapping one-dimensional projections.  Merely
    touching projections do not overlap, matching the contact-boundary gap
    semantics used for pad protection.
    """

    def box_axes(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
        theta = math.radians(yaw_deg)
        cosine = math.cos(theta)
        sine = math.sin(theta)
        return (cosine, sine), (-sine, cosine)

    boxes = (
        (center_a_east, center_a_north, half_a_east, half_a_north, box_axes(yaw_a_deg)),
        (center_b_east, center_b_north, half_b_east, half_b_north, box_axes(yaw_b_deg)),
    )
    for axis in (*box_axes(yaw_a_deg), *box_axes(yaw_b_deg)):
        projections: list[tuple[float, float]] = []
        for center_east, center_north, half_east, half_north, local_axes in boxes:
            projection = center_east * axis[0] + center_north * axis[1]
            radius = (
                half_east * abs(axis[0] * local_axes[0][0] + axis[1] * local_axes[0][1])
                + half_north * abs(axis[0] * local_axes[1][0] + axis[1] * local_axes[1][1])
            )
            projections.append((projection - radius, projection + radius))
        (left_min, left_max), (right_min, right_max) = projections
        if left_max <= right_min or right_max <= left_min:
            return False
    return True


@pytest.mark.parametrize("degrees,radians", [(90, math.pi / 2), (35, 7 * math.pi / 36), (-70, -7 * math.pi / 18)])
def test_exported_sdf_uses_radians_and_matches_oriented_box_axes(degrees, radians):
    import xml.etree.ElementTree as ET
    facility = _canonical_facility(_facility_of_kind("hub", "unit-check", parking_slots=2, rotation_deg=degrees))
    for block in facility_sdf_model_blocks(facility):
        model = ET.fromstring(block)
        pose = model.find("pose")
        assert pose is not None
        assert pose.get("degrees", "false") == "false"
        yaw = float(pose.text.split()[5])
        assert yaw == pytest.approx(radians, abs=1e-12)
        # SDF local +X maps to the same ENU axis as the authored rotation.
        assert (math.cos(yaw), math.sin(yaw)) == pytest.approx((math.cos(radians), math.sin(radians)), abs=1e-12)
