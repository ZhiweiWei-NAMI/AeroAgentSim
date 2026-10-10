"""Focused tests for materializing logistics facility geometry into the Gazebo world.

The module under test is the smallest logistics-side integration with the real
``px4.gazebo`` world staging path: it consumes the committed acceptance geometry
(``facility_physics``) and splices every configured facility's ``launch_pad.*``
and ``facility_*`` static models into an existing ``<world>`` document.  These
tests verify the *emitted SDF document*, not just the in-memory model: exact
configured ENU poses with SDF units (metres, radians), pad alias linkage,
global collision clearance across multiple pads/facilities, and explicit
rejection of overlapping or unsupported placements.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityCatalogue,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_physics import (
    FacilityPhysics,
)
from aero_bench.tasks.logistics.facility_world import (
    FacilityWorldError,
    combined_facility_physics,
    facility_world_bytes,
    facility_world_model_blocks,
    materialize_facility_world,
    oriented_rectangles_have_positive_overlap,
    pad_body_overlap_3d,
    pad_contacts_overlap_3d,
    structural_bodies_overlap_3d,
)
from aero_bench.world.scene_compiler import SceneOrigin
from tests.tasks.test_logistics_facility_physics import (
    _canonical_facility,
    _facility_of_kind,
)

ORIGIN = SceneOrigin(latitude_deg=31.2288, longitude_deg=121.481)

POS_TOL = 1e-9
YAW_TOL = 1e-12

_PHYSICS_CHILD = (
    "  <physics type='ode'>\n"
    "    <max_step_size>0.001</max_step_size>\n"
    "    <real_time_factor>1</real_time_factor>\n"
    "  </physics>\n"
)


def _base_world_bytes(
    *,
    origin: SceneOrigin = ORIGIN,
    extra_models: tuple[str, ...] = (),
    with_physics: bool = True,
) -> bytes:
    models = "\n".join(extra_models)
    physics = _PHYSICS_CHILD if with_physics else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sdf version="1.10">\n'
        '  <world name="test_urban_scene">\n'
        "    <spherical_coordinates>\n"
        "      <surface_model>EARTH_WGS84</surface_model>\n"
        "      <world_frame_orientation>ENU</world_frame_orientation>\n"
        f"      <latitude_deg>{float(origin.latitude_deg)}</latitude_deg>\n"
        f"      <longitude_deg>{float(origin.longitude_deg)}</longitude_deg>\n"
        f"      <elevation>{float(origin.amsl_m)}</elevation>\n"
        "      <heading_deg>0</heading_deg>\n"
        "    </spherical_coordinates>\n"
        "    <paused>true</paused>\n"
        f"{models}\n"
        f"{physics}"
        '  </world>\n'
        '</sdf>\n'
    ).encode("utf-8")


def _facility(kind: str, facility_id: str, **kwargs) -> FacilityCapabilities:
    return _canonical_facility(_facility_of_kind(kind, facility_id, **kwargs))


def _parse_world(payload: bytes) -> ET.Element:
    root = ET.fromstring(payload)
    worlds = [element for element in root if element.tag == "world"]
    assert len(worlds) == 1, "exactly one <world> expected"
    return worlds[0]


def _model_map(world: ET.Element) -> dict[str, ET.Element]:
    return {
        element.attrib["name"]: element
        for element in world
        if element.tag == "model" and "name" in element.attrib
    }


def _model_pose(model: ET.Element) -> list[float]:
    pose = next(iter(model.findall("pose")))
    assert pose.attrib.get("degrees") in (None, "false"), "SDF pose must be radians"
    return [float(value) for value in pose.text.split()]


def _model_size(model: ET.Element) -> list[float]:
    size = model.find("./link/collision/geometry/box/size")
    assert size is not None and size.text
    return [float(value) for value in size.text.split()]


@pytest.fixture(name="multi_facility")
def _multi_facility() -> list[FacilityCapabilities]:
    hub = _facility(
        "hub", "hub-ex", parking_slots=2, rotation_deg=30.0,
        position={"x": 15.0, "z": -12.0},
    )
    vertiport = _facility(
        "vertiport", "vp-ex", parking_slots=1, rotation_deg=-22.0,
        position={"x": 45.0, "z": 30.0},
    )
    charger = _facility(
        "charger", "ch-ex", slots=3, rotation_deg=0.0,
        position={"x": -20.0, "z": 5.0},
    )
    return [hub, vertiport, charger]


def test_every_pad_placed_at_exact_configured_enu_pose_with_sdf_units(
    tmp_path: Path,
) -> None:
    hub = _facility(
        "hub", "hub-pose", parking_slots=3, rotation_deg=37.5,
        width_m=20.0, position={"x": 15.0, "z": -12.0},
    )
    destination = tmp_path / "worlds" / "logistics_scene.sdf"
    result = materialize_facility_world(
        _base_world_bytes(), destination, facilities=[hub], origin=ORIGIN
    )
    assert result == destination
    assert destination.is_file()

    world = _parse_world(destination.read_bytes())
    models = _model_map(world)
    physics: FacilityPhysics = combined_facility_physics([hub])[0]
    pads = facility_landing_pads(hub)

    assert len(physics.contacts) == 3
    for contact in physics.contacts:
        model = models[contact.model_name]
        assert contact.model_name.startswith("launch_pad.")
        pose = _model_pose(model)
        assert pose[0] == pytest.approx(contact.east_m, abs=POS_TOL)
        assert pose[1] == pytest.approx(contact.north_m, abs=POS_TOL)
        assert pose[2] == pytest.approx(
            contact.up_top_m - contact.thickness_m / 2.0, abs=POS_TOL
        )
        assert pose[3] == 0.0
        assert pose[4] == 0.0
        assert pose[5] == pytest.approx(math.radians(contact.yaw_deg), abs=YAW_TOL)

    # The pad contact top is exactly the accepted landing pad y (no recentring).
    for contact, pad in zip(physics.contacts, pads, strict=True):
        assert contact.up_top_m == pad.y
        assert contact.east_m == pad.x
        assert contact.north_m == -pad.z
        assert contact.yaw_deg == pad.rotation_deg


def test_pad_alias_linkage_and_structural_identity_in_combined_world(
    tmp_path: Path,
) -> None:
    hub = _facility("hub", "hub-alias", parking_slots=2, rotation_deg=30.0)
    destination = tmp_path / "logistics.sdf"
    materialize_facility_world(
        _base_world_bytes(), destination, facilities=[hub], origin=ORIGIN
    )
    world = _parse_world(destination.read_bytes())
    models = _model_map(world)

    contact_names = sorted(
        name for name in models if name.startswith("launch_pad.")
    )
    structural_names = sorted(
        name for name in models if not name.startswith("launch_pad.")
    )
    physics = combined_facility_physics([hub])[0]
    expected_contacts = sorted(contact.model_name for contact in physics.contacts)
    expected_bodies = sorted(body.model_name for body in physics.bodies)
    assert contact_names == expected_contacts
    assert structural_names == expected_bodies

    # PX4 contact classification: only launch_pad.* / ground.* are landing
    # contacts; structural bodies carry the facility_<kind>_ identity so the
    # journal never records a landing on a carrying deck as a collision.
    for name in structural_names:
        assert name.startswith("facility_hub_")
        assert "launch_pad" not in name
        assert not name.startswith("ground")

    # Model names are unique across the combined world.
    assert len(models) == len(expected_contacts) + len(expected_bodies)
    for name in (*contact_names, *structural_names):
        assert name in models
    for model in _parse_world(destination.read_bytes()).findall("model"):
        assert model.attrib["name"].startswith(("launch_pad.", "facility_"))


def test_multiple_pads_and_facilities_are_globally_collision_free(
    tmp_path: Path,
) -> None:
    facilities = [
        _facility("hub", "hub-multi", parking_slots=3, rotation_deg=30.0,
                  width_m=20.0, position={"x": 15.0, "z": -12.0}),
        _facility("vertiport", "vp-multi", parking_slots=2, rotation_deg=-22.0,
                  position={"x": 45.0, "z": 30.0}),
        _facility("charger", "ch-multi", slots=3, rotation_deg=0.0,
                  position={"x": -20.0, "z": 5.0}),
    ]
    payload = facility_world_bytes(
        _base_world_bytes(), facilities=facilities, origin=ORIGIN
    )
    world = _parse_world(payload)
    models = _model_map(world)

    expected_models = len(facility_world_model_blocks(facilities))
    assert len(models) == expected_models

    combined = combined_facility_physics(facilities)
    assert sum(len(physics.contacts) + len(physics.bodies) for physics in combined) == expected_models
    owner: dict[str, str] = {}
    for physics in combined:
        for contact in physics.contacts:
            owner[contact.model_name] = physics.facility_id
        for body in physics.bodies:
            owner[body.model_name] = physics.facility_id

    # Independent pairwise check on the *emitted SDF* collision boxes: no two
    # models from *different* facilities may intersect with positive volume.
    # (Pairs inside one facility are the accepted per-facility geometry, which
    # this staging module intentionally preserves verbatim.)
    entries: list[tuple[str, tuple[float, ...], tuple[float, ...]]] = []
    for name, model in sorted(models.items()):
        pose = _model_pose(model)
        size = _model_size(model)
        entries.append((name, tuple(pose), tuple(size)))

    for index in range(len(entries)):
        name_a, pose_a, size_a = entries[index]
        for name_b, pose_b, size_b in entries[index + 1 :]:
            if owner[name_a] == owner[name_b]:
                continue
            horizontal = oriented_rectangles_have_positive_overlap(
                a_center_east_m=pose_a[0],
                a_center_north_m=pose_a[1],
                a_half_east_m=size_a[0] / 2.0,
                a_half_north_m=size_a[1] / 2.0,
                a_yaw_deg=math.degrees(pose_a[5]),
                b_center_east_m=pose_b[0],
                b_center_north_m=pose_b[1],
                b_half_east_m=size_b[0] / 2.0,
                b_half_north_m=size_b[1] / 2.0,
                b_yaw_deg=math.degrees(pose_b[5]),
            )
            if not horizontal:
                continue
            a_bottom = pose_a[2] - size_a[2] / 2.0
            b_bottom = pose_b[2] - size_b[2] / 2.0
            vertical_overlap = max(a_bottom, b_bottom) < min(
                pose_a[2] + size_a[2] / 2.0, pose_b[2] + size_b[2] / 2.0
            )
            assert not vertical_overlap, (
                f"SDF collision boxes {name_a} and {name_b} intersect: "
                f"pose {pose_a} vs {pose_b} size {size_a} vs {size_b}"
            )


def test_materialization_is_deterministic() -> None:
    facilities = [
        _facility("hub", "hub-det", parking_slots=2, rotation_deg=12.5,
                  position={"x": 7.0, "z": -3.0}),
        _facility("charger", "ch-det", slots=2, rotation_deg=45.0,
                  position={"x": -12.0, "z": 8.0}),
    ]
    first = facility_world_bytes(_base_world_bytes(), facilities=facilities, origin=ORIGIN)
    second = facility_world_bytes(_base_world_bytes(), facilities=facilities, origin=ORIGIN)
    assert first == second


def test_staging_preserves_paused_physics_and_world_identity() -> None:
    facilities = [_facility("vertiport", "vp-pres", parking_slots=1, rotation_deg=0.0)]
    payload = facility_world_bytes(_base_world_bytes(), facilities=facilities, origin=ORIGIN)
    world = _parse_world(payload)
    assert world.attrib["name"] == "test_urban_scene"
    paused = next(iter(world.findall("paused")))
    assert paused.text == "true"
    physics = next(iter(world.findall("physics")))
    step = physics.find("max_step_size")
    assert step is not None and step.text == "0.001"
    # The staging helper owns <paused>; materialization must preserve it even
    # when the base world explicitly declares the other value.
    base = _base_world_bytes().replace(b"<paused>true</paused>", b"<paused>false</paused>")
    preserved = _parse_world(facility_world_bytes(base, facilities=facilities, origin=ORIGIN))
    assert next(iter(preserved.findall("paused"))).text == "false"


def test_overlapping_pads_between_facilities_are_rejected(tmp_path: Path) -> None:
    left = _facility("vertiport", "vp-left", parking_slots=1, rotation_deg=0.0,
                     position={"x": 0.0, "z": 0.0})
    right = _facility("vertiport", "vp-right", parking_slots=1, rotation_deg=0.0,
                      position={"x": 0.0, "z": 0.0})
    a_pad = combined_facility_physics([left])[0].contacts[0]
    b_pad = combined_facility_physics([right])[0].contacts[0]
    assert pad_contacts_overlap_3d(a_pad, b_pad)

    with pytest.raises(FacilityWorldError, match="overlapping"):
        combined_facility_physics([left, right])
    with pytest.raises(FacilityWorldError, match="vp-left pad 0 overlapping"):
        materialize_facility_world(
            _base_world_bytes(),
            tmp_path / "bad.sdf",
            facilities=[left, right],
            origin=ORIGIN,
        )


def test_pad_overlapping_other_facility_structural_body_is_rejected(
    tmp_path: Path,
) -> None:
    # Two vertiports 5 m apart: their pads do not touch, but one facility's
    # carrying-deck partition overlaps the other's pad slab, so the combined
    # world must be rejected as a real static-collision set.
    left = _facility("vertiport", "vp-body-a", parking_slots=1, rotation_deg=0.0,
                     position={"x": 0.0, "z": 0.0})
    right = _facility("vertiport", "vp-body-b", parking_slots=1, rotation_deg=0.0,
                      position={"x": 5.0, "z": 0.0})

    left_physics = combined_facility_physics([left])[0]
    right_physics = combined_facility_physics([right])[0]
    assert not pad_contacts_overlap_3d(left_physics.contacts[0], right_physics.contacts[0])
    assert any(
        pad_body_overlap_3d(left_physics.contacts[0], body)
        for body in right_physics.bodies
    )

    with pytest.raises(FacilityWorldError, match="structural body"):
        combined_facility_physics([left, right])
    with pytest.raises(FacilityWorldError, match="structural body"):
        facility_world_bytes(
            _base_world_bytes(), facilities=[left, right], origin=ORIGIN
        )


def test_structural_bodies_never_overlap_across_facilities() -> None:
    left = _facility("vertiport", "vp-close-a", parking_slots=1, rotation_deg=0.0,
                     position={"x": 0.0, "z": 0.0})
    right = _facility("vertiport", "vp-close-b", parking_slots=1, rotation_deg=0.0,
                      position={"x": 5.0, "z": 0.0})
    left_physics = combined_facility_physics([left])[0]
    right_physics = combined_facility_physics([right])[0]
    assert any(
        structural_bodies_overlap_3d(body_a, body_b)
        for body_a in left_physics.bodies
        for body_b in right_physics.bodies
    )
    with pytest.raises(FacilityWorldError):
        combined_facility_physics([left, right])


def test_accepted_close_but_clear_facilities_combine() -> None:
    # Two single-pad vertiports 8.2 m apart: pads clear each other and all
    # structural bodies clear both pad skins; the combined world is valid.
    facilities = [
        _facility("vertiport", "vp-clear-a", parking_slots=1, rotation_deg=0.0,
                  position={"x": 0.0, "z": 0.0}),
        _facility("vertiport", "vp-clear-b", parking_slots=1, rotation_deg=0.0,
                  position={"x": 8.2, "z": 0.0}),
    ]
    payload = facility_world_bytes(
        _base_world_bytes(), facilities=facilities, origin=ORIGIN
    )
    models = _model_map(_parse_world(payload))
    assert len(models) == len(facility_world_model_blocks(facilities))


def test_invalid_and_unsupported_inputs_are_rejected_explicitly(
    tmp_path: Path,
) -> None:
    too_small = _facility("vertiport", "vp-small", parking_slots=1,
                          width_m=10.0, depth_m=8.0)
    with pytest.raises(FacilityWorldError):
        combined_facility_physics([too_small])

    with pytest.raises(FacilityWorldError, match="at least one configured facility"):
        combined_facility_physics([])
    with pytest.raises(FacilityWorldError, match="at least one configured facility"):
        facility_world_bytes(
            _base_world_bytes(), facilities=FacilityCatalogue(), origin=ORIGIN
        )

    with pytest.raises(FacilityWorldError, match="FacilityCapabilities"):
        combined_facility_physics(["hub-ex"])  # type: ignore[list-item]

    with pytest.raises(FacilityWorldError, match="SceneOrigin"):
        facility_world_bytes(_base_world_bytes(), facilities=[_facility("hub", "hub-o")], origin=object())  # type: ignore[arg-type]

    with pytest.raises(FacilityWorldError, match="spherical_coordinates"):
        facility_world_bytes(
            b'<sdf version="1.10"><world name="w"></world></sdf>',
            facilities=[_facility("hub", "hub-noorigin")], origin=ORIGIN,
        )

    mismatched = _base_world_bytes().replace(b"121.481", b"114.1")
    with pytest.raises(FacilityWorldError, match="longitude"):
        facility_world_bytes(mismatched, facilities=[_facility("hub", "hub-mis")], origin=ORIGIN)

    # A world whose spherical origin disagrees with the supplied SceneOrigin.
    drifted = _base_world_bytes().replace(b"31.2288", b"31.9999")
    with pytest.raises(FacilityWorldError, match="latitude"):
        facility_world_bytes(drifted, facilities=[_facility("hub", "hub-drift")], origin=ORIGIN)

    with pytest.raises(FacilityWorldError, match="exactly one <world>"):
        facility_world_bytes(
            b'<sdf version="1.10"><world name="a"></world><world name="b"></world></sdf>',
            facilities=[_facility("hub", "hub-two")], origin=ORIGIN,
        )

    duplicate = _base_world_bytes(
        extra_models=(
            "    <model name='launch_pad.unit-ex.0'>"
            "      <static>true</static>"
            "      <pose>0 0 0 0 0 0</pose>"
            "    </model>",
        )
    )
    with pytest.raises(FacilityWorldError, match="already declares a model named"):
        facility_world_bytes(
            duplicate,
            facilities=[_facility("hub", "unit-ex", position={"x": 0.0, "z": 0.0})],
            origin=ORIGIN,
        )


def test_multiple_facilities_accepts_a_catalogue() -> None:
    hub = _facility("hub", "hub-cat", parking_slots=2, rotation_deg=30.0,
                     position={"x": 0.0, "z": 0.0})
    charger = _facility("charger", "ch-cat", slots=2, rotation_deg=0.0,
                        position={"x": 25.0, "z": 0.0})
    catalogue = FacilityCatalogue(facilities=(hub, charger))
    payload = facility_world_bytes(_base_world_bytes(), facilities=catalogue, origin=ORIGIN)
    world = _parse_world(payload)
    names = {element.attrib["name"] for element in world if element.tag == "model"}
    assert "launch_pad.hub-cat.0" in names
    assert "launch_pad.ch-cat.0" in names
    assert any(name.startswith("facility_hub_") for name in names)
    assert any(name.startswith("facility_charger_") for name in names)
