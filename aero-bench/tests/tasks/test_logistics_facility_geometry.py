from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from aero_bench.tasks.logistics.facilities import compile_authored_facilities
from aero_bench.tasks.logistics.facility_geometry import (
    CHARGER_PAD_LAYOUT,
    FacilityLandingPad,
    PAD_FRAME,
    VERTIPORT_PAD_LAYOUT,
    facility_landing_pads,
)

FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "logistics_facility_geometry"
    / "frontend_pads_fixture.json"
)

ABS_TOL = 1e-9

#: Frontend ``FACILITY_MIN_DIMENSIONS`` (city-facility-models.ts): (width, depth,
#: height) metres per kind.  Kept literal here as the independent renderer
#: contract being tested, not imported back from the Python helper.
RENDER_MINIMA_BY_KIND = {
    "vertiport": (12.0, 8.0, 4.0),
    "hub": (14.0, 11.0, 5.0),
    "charger": (10.0, 8.0, 3.2),
}


def _load_frontend_fixture() -> dict[str, object]:
    return __import__("json").loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _canonical(facility: dict[str, object]):
    catalogue = compile_authored_facilities([facility])
    return catalogue.facilities[0]


def _facility_of_kind(
    kind: str,
    *,
    width_m: float,
    depth_m: float,
    height_m: float,
    placement: str = "ground",
    parking_slots: int = 1,
    slots: int = 3,
) -> dict[str, object]:
    """Build one v3 authoring facility with the requested dimensions/capabilities."""
    base: dict[str, object] = {
        "id": f"{kind}.dims",
        "name": f"{kind} dims",
        "kind": kind,
        "placement": placement,
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 0, "z": 0},
        "rotationDeg": 0,
        "widthM": width_m,
        "depthM": depth_m,
        "heightM": height_m,
    }
    if kind == "vertiport":
        base["landing"] = {"parkingSlots": parking_slots, "movementsPerHour": 30}
        base["cargo"] = None
        base["charging"] = None
    elif kind == "hub":
        base["landing"] = {"parkingSlots": parking_slots, "movementsPerHour": 20}
        base["cargo"] = {"storageCapacityKg": 1000, "throughputPerHourKg": 1000}
        base["charging"] = None
    else:  # standalone charger
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


def _assert_pad_matches_expected(pad: FacilityLandingPad, expected: dict[str, object]) -> None:
    assert pad.facility_id == expected["facility_id"]
    assert pad.pad_index == expected["pad_index"]
    assert pad.x == pytest.approx(expected["x"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.y == pytest.approx(expected["y"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.z == pytest.approx(expected["z"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.width_m == pytest.approx(expected["width_m"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.depth_m == pytest.approx(expected["depth_m"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.rotation_deg == pytest.approx(expected["rotation_deg"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.frame == expected["frame"] == PAD_FRAME == "scene_east_south_m"


@pytest.mark.parametrize(
    "case_id",
    [
        "vp_ground_3pads_rot35",
        "vp_ground_1pad_rot90",
        "vp_rooftop_2pads_support150_rot90",
        "vp_rooftop_1pad_support150_rot30",
        "hub_ground_2pads_rot30",
        "hub_ground_1pad_rot0",
        "ch_ground_3pads_rot30",
        "ch_ground_1pad_rot90",
    ],
)
def test_backend_pads_match_independently_captured_frontend_values(case_id: str) -> None:
    """Cross-language parity against the fixture captured by executing the real
    frontend ``facilityLandingPads(toFacilityVisualSpec(...))`` plus the real
    three.js world matrix of ``createFacilityVisual``.  This compares the
    backend only against independent frontend values, never against a second
    copy of the Python formula."""
    fixture = _load_frontend_fixture()
    case = next(entry for entry in fixture["cases"] if entry["case_id"] == case_id)
    facility = _canonical(case["facility"])
    pads = facility_landing_pads(facility)

    expected_pads = case["pads"]
    assert len(pads) == len(expected_pads) == len(case["local_pads"])
    assert [pad.pad_index for pad in pads] == list(range(len(expected_pads)))
    for pad, expected in zip(pads, expected_pads, strict=True):
        _assert_pad_matches_expected(pad, expected)


def test_fixture_covers_ground_vertiport_hub_and_charger() -> None:
    fixture = _load_frontend_fixture()
    cases = fixture["cases"]
    kinds = {entry["facility"]["kind"] for entry in cases}
    assert kinds == {"vertiport", "hub", "charger"}
    placements = {entry["facility"]["placement"] for entry in cases}
    assert placements == {"ground", "rooftop"}
    counts = {entry["facility"]["landing"]["parkingSlots"] if entry["facility"]["landing"] else 0
              for entry in cases}
    assert counts >= {1, 2, 3}
    rotations = {entry["facility"]["rotationDeg"] for entry in cases}
    assert rotations >= {0, 30, 35, 90}
    supports = {entry["facility"]["supportHeightM"] for entry in cases}
    assert supports == {None, 150}


def test_rtol_full_fixture_matches_with_low_tolerance() -> None:
    """Every captured frontend pad is reproduced by the backend within 1e-9 m."""
    fixture = _load_frontend_fixture()
    for case in fixture["cases"]:
        facility = _canonical(case["facility"])
        pads = facility_landing_pads(facility)
        assert [pad.pad_index for pad in pads] == list(range(len(case["pads"])))
        for pad, expected in zip(pads, case["pads"], strict=True):
            for field in ("x", "y", "z", "width_m", "depth_m", "rotation_deg"):
                got = getattr(pad, field)
                want = expected[field]
                assert got == pytest.approx(want, rel=ABS_TOL, abs=ABS_TOL)


def test_pad_y_is_contact_surface_not_body_centre() -> None:
    """The backend must return the deck/support surface y; the aircraft
    body-centre half height is a later dispatch concern and must never be
    added by the pad materializer."""
    fixture = _load_frontend_fixture()

    vertiport = _canonical(next(
        entry["facility"] for entry in fixture["cases"] if entry["case_id"] == "vp_ground_3pads_rot35"
    ))
    for pad in facility_landing_pads(vertiport):
        assert pad.y == pytest.approx(VERTIPORT_PAD_LAYOUT.y_m, rel=ABS_TOL, abs=ABS_TOL)

    rooftop = _canonical(next(
        entry["facility"] for entry in fixture["cases"]
        if entry["case_id"] == "vp_rooftop_2pads_support150_rot90"
    ))
    assert rooftop.support_height_m == 150.0
    for pad in facility_landing_pads(rooftop):
        # Declared support height lifts the contact surface; no body-centre offset.
        assert pad.y == pytest.approx(VERTIPORT_PAD_LAYOUT.y_m + 150.0, rel=ABS_TOL, abs=ABS_TOL)
        assert pad.y != pytest.approx(VERTIPORT_PAD_LAYOUT.y_m + 150.0 + 0.5, abs=1e-3)

    hub = _canonical(next(
        entry["facility"] for entry in fixture["cases"] if entry["case_id"] == "hub_ground_2pads_rot30"
    ))
    for pad in facility_landing_pads(hub):
        assert pad.y == pytest.approx(3.3625, rel=ABS_TOL, abs=ABS_TOL)

    charger = _canonical(next(
        entry["facility"] for entry in fixture["cases"] if entry["case_id"] == "ch_ground_3pads_rot30"
    ))
    for pad in facility_landing_pads(charger):
        assert pad.y == pytest.approx(CHARGER_PAD_LAYOUT.y_m, rel=ABS_TOL, abs=ABS_TOL)


def test_pad_indexing_is_stable_and_deterministic() -> None:
    fixture = _load_frontend_fixture()
    case = next(entry for entry in fixture["cases"] if entry["case_id"] == "vp_ground_3pads_rot35")
    facility = _canonical(case["facility"])
    first = facility_landing_pads(facility)
    second = facility_landing_pads(facility)
    assert first == second
    assert [pad.pad_index for pad in first] == [0, 1, 2]
    assert [pad.facility_id for pad in first] == ["vp-ground-3"] * 3
    # Indices stay the low-to-high X order of the frontend single-row layout.
    xs = [pad.x for pad in first]
    assert xs == sorted(xs)


def test_world_rotation_matches_frontend_90_degree_case() -> None:
    """A 90-degree asymmetric rotation maps the local X row onto the scene z
    axis; the captured frontend values encode exactly that and the backend
    reproduces the world x/z without reinventing a rotation sign."""
    fixture = _load_frontend_fixture()
    case = next(entry for entry in fixture["cases"] if entry["case_id"] == "ch_ground_1pad_rot90")
    facility = _canonical(case["facility"])
    (pad,) = facility_landing_pads(facility)
    expected = case["pads"][0]
    assert facility.rotation_deg == 90.0
    assert pad.rotation_deg == 90.0
    assert pad.x == pytest.approx(expected["x"], rel=ABS_TOL, abs=ABS_TOL)
    assert pad.z == pytest.approx(expected["z"], rel=ABS_TOL, abs=ABS_TOL)


def test_rejects_landing_row_wider_than_footprint() -> None:
    base = {
        "id": "vp.bad", "name": "bad", "kind": "vertiport", "placement": "ground",
        "buildingId": None, "supportHeightM": None,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 12, "depthM": 8, "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None, "charging": None,
    }
    facility = _canonical(base)
    # 12 m is the exact vertiport render minimum width, so the facility itself
    # is renderable; the row is not.  frontend maxLandingParkingSlots caps the
    # count at 2 (floor((12 - 4.2 + 5.4)/5.4) == 2), so 3 pads are rejected.
    with pytest.raises(ValueError, match="footprint width"):
        facility_landing_pads(facility)


def test_rejects_hub_depth_below_render_minimum() -> None:
    """A too-shallow hub is a render-model-fit rejection, not a hint to invent pads.

    The old too-small boundary (depth 5 m carrying a 4.76 x 4.4 m pad column at
    local z=-1.155) described geometry the actual frontend renderer refuses to
    draw: ``validateSpec`` requires a hub to be at least 14 x 11 x 5 m.  The
    physical helper rejects the same model-fit violation instead of fabricating
    pad rectangles the renderer would never produce.
    """
    base = {
        "id": "hub.bad", "name": "bad", "kind": "hub", "placement": "ground",
        "buildingId": None, "supportHeightM": None,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 18, "depthM": 5, "heightM": 5.2,
        "landing": {"parkingSlots": 1, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 10000, "throughputPerHourKg": 10000},
        "charging": None,
    }
    facility = _canonical(base)
    # A valid hub column would fit any depth >= 11 m, so a 5 m depth is below the
    # renderer minimum and must be rejected with the render-model-fit message.
    with pytest.raises(ValueError, match="at least"):
        facility_landing_pads(facility)


def test_rejects_charger_with_more_than_three_stations() -> None:
    base = {
        "id": "ch.bad", "name": "bad", "kind": "charger", "placement": "ground",
        "buildingId": None, "supportHeightM": None,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 20, "depthM": 8, "heightM": 3.2,
        "landing": None, "cargo": None,
        "charging": {"slots": 4, "powerW": 5000, "priceAmount": 1.0,
                     "priceCurrency": "CNY", "priceUnit": "kWh"},
    }
    facility = _canonical(base)
    with pytest.raises(ValueError, match="at most 3"):
        facility_landing_pads(facility)


def test_rejects_charger_width_below_render_minimum() -> None:
    """A too-narrow charger is rejected before any station row is materialized.

    The old too-small boundary (8.5 m wide with a 9 m three-station row) described
    geometry the actual frontend renderer refuses to draw: ``validateSpec``
    requires a charger to be at least 10 x 8 x 3.2 m.  The backend mirrors that
    authoring gate instead of silently clamping the slots down to fit.
    """
    base = {
        "id": "ch.bad2", "name": "bad", "kind": "charger", "placement": "ground",
        "buildingId": None, "supportHeightM": None,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 8.5, "depthM": 8, "heightM": 3.2,
        "landing": None, "cargo": None,
        "charging": {"slots": 3, "powerW": 5000, "priceAmount": 1.0,
                     "priceCurrency": "CNY", "priceUnit": "kWh"},
    }
    facility = _canonical(base)
    with pytest.raises(ValueError, match="at least"):
        facility_landing_pads(facility)


@pytest.mark.parametrize(
    ("kind", "width_m", "depth_m", "height_m"),
    [
        # vertiport render minimum 12 x 8 x 4 m
        ("vertiport", 11.99, 8.0, 4.0),
        ("vertiport", 12.0, 7.99, 4.0),
        ("vertiport", 12.0, 8.0, 3.99),
        # hub render minimum 14 x 11 x 5 m
        ("hub", 13.99, 11.0, 5.0),
        ("hub", 14.0, 10.99, 5.0),
        ("hub", 14.0, 11.0, 4.99),
        # charger render minimum 10 x 8 x 3.2 m
        ("charger", 9.99, 8.0, 3.2),
        ("charger", 10.0, 7.99, 3.2),
        ("charger", 10.0, 8.0, 3.19),
    ],
)
def test_rejects_dimensions_below_frontend_render_minimum(
    kind: str, width_m: float, depth_m: float, height_m: float
) -> None:
    """Every width/depth/height below the renderer's per-kind
    ``FACILITY_MIN_DIMENSIONS`` is rejected with the render-model-fit message."""
    min_width, min_depth, min_height = RENDER_MINIMA_BY_KIND[kind]
    assert width_m < min_width or depth_m < min_depth or height_m < min_height
    facility = _canonical(
        _facility_of_kind(kind, width_m=width_m, depth_m=depth_m, height_m=height_m)
    )
    with pytest.raises(ValueError, match="at least"):
        facility_landing_pads(facility)


@pytest.mark.parametrize(
    ("kind", "width_m", "depth_m", "height_m"),
    [
        # vertiport render minimum 12 x 8 x 4 m
        ("vertiport", 12.0, 8.0, 4.0),
        # hub render minimum 14 x 11 x 5 m
        ("hub", 14.0, 11.0, 5.0),
        # charger render minimum 10 x 8 x 3.2 m
        ("charger", 10.0, 8.0, 3.2),
    ],
)
def test_accepts_exact_render_minimum_dimensions(
    kind: str, width_m: float, depth_m: float, height_m: float
) -> None:
    """Facilities at exactly the render minimum are valid and lower pads."""
    min_width, min_depth, min_height = RENDER_MINIMA_BY_KIND[kind]
    assert width_m == min_width and depth_m == min_depth and height_m == min_height
    pads = facility_landing_pads(
        _canonical(_facility_of_kind(kind, width_m=width_m, depth_m=depth_m, height_m=height_m))
    )
    assert pads  # one/pad count accepted, never clamped or rejected


@pytest.mark.parametrize(
    ("kind", "dimension", "min_value"),
    [
        ("vertiport", "widthM", 12.0),
        ("vertiport", "depthM", 8.0),
        ("vertiport", "heightM", 4.0),
        ("hub", "widthM", 14.0),
        ("hub", "depthM", 11.0),
        ("hub", "heightM", 5.0),
        ("charger", "widthM", 10.0),
        ("charger", "depthM", 8.0),
        ("charger", "heightM", 3.2),
    ],
)
def test_rejects_one_ulp_below_render_minimum(kind: str, dimension: str, min_value: float) -> None:
    """The renderer's validateSpec uses a strict ``<`` gate, so the smallest
    representable step below a kind minimum must already be rejected while the
    minimum itself is not — no tolerance may open a window at this boundary."""
    dims = {
        "vertiport": (12.0, 8.0, 4.0),
        "hub": (14.0, 11.0, 5.0),
        "charger": (10.0, 8.0, 3.2),
    }[kind]
    values = dict(zip(("widthM", "depthM", "heightM"), dims))
    values[dimension] = math.nextafter(min_value, 0.0)
    assert min_value == math.nextafter(values[dimension], math.inf)
    assert values[dimension] < min_value  # strict-< territory, per the frontend
    width_m, depth_m, height_m = values["widthM"], values["depthM"], values["heightM"]
    facility = _canonical(
        _facility_of_kind(kind, width_m=width_m, depth_m=depth_m, height_m=height_m)
    )
    with pytest.raises(ValueError, match="at least"):
        facility_landing_pads(facility)


def test_rejects_rooftop_vertiport_with_height_below_render_minimum() -> None:
    """Review R2's 0.5 m roof deck is refused: the renderer's ``validateSpec``
    requires the facility box itself to meet the 4 m vertiport height minimum.

    A rooftop vertiport with a 150 m declared support still needs a renderable
    facade box, so ``heightM=0.5`` is a model-fit violation and must not yield
    pads at y=150.72 above a 0.5 m-declared facade.
    """
    facility = _canonical({
        "id": "vp.roof", "name": "roof", "kind": "vertiport",
        "placement": "rooftop", "buildingId": "building.42", "supportHeightM": 150.0,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 14, "depthM": 10, "heightM": 0.5,
        "landing": {"parkingSlots": 1, "movementsPerHour": 10},
        "cargo": None, "charging": None,
    })
    with pytest.raises(ValueError, match="at least"):
        facility_landing_pads(facility)


def test_width_capacity_matches_frontend_max_landing_parking_slots_order() -> None:
    """Landing width capacity uses the frontend ``maxLandingParkingSlots``
    predicate operation order, so a 1-ulp window at an exact-fit boundary cannot
    separate the Python helper from the renderer.

    vertiport pads are 4.2 m wide at 5.4 m pitch and the frontend computes
    ``floor((widthM - 4.2 + 5.4) / 5.4)`` left to right.  At width 31.2 that is
    ``floor(5.999999999999999) == 5``: the renderer caps the row at five pads
    even though the plain ``row_extent <= width`` inequality would accept six.
    """
    reject_six = _canonical(_facility_of_kind(
        "vertiport", width_m=31.2, depth_m=8.0, height_m=4.0, parking_slots=6,
    ))
    with pytest.raises(ValueError, match="footprint width"):
        facility_landing_pads(reject_six)

    # At width 20.4 the exact row extent for four pads (3*5.4 + 4.2 =
    # 20.400000000000002 m) is 1 ulp above the float for 20.4, so the old
    # ``row_extent <= width`` inequality wrongly rejected four pads.  The
    # renderer's own predicate accepts them; the backend must too.
    accept_four = _canonical(_facility_of_kind(
        "vertiport", width_m=20.4, depth_m=8.0, height_m=4.0, parking_slots=4,
    ))
    assert [pad.pad_index for pad in facility_landing_pads(accept_four)] == [0, 1, 2, 3]


def test_pad_fit_boundary_is_accepted_exactly() -> None:
    # 14 x 11 x 5 m is the exact hub render minimum; two pads fit because the
    # frontend maxLandingParkingSlots predicate floor((14 - 4.76 + 5.96)/5.96)
    # = 2 and the hub pad column fits the 11 m depth.
    base = {
        "id": "hub.ok", "name": "ok", "kind": "hub", "placement": "ground",
        "buildingId": None, "supportHeightM": None,
        "position": {"x": 0, "z": 0}, "rotationDeg": 0,
        "widthM": 14, "depthM": 11, "heightM": 5.2,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 1000, "throughputPerHourKg": 1000},
        "charging": None,
    }
    pads = facility_landing_pads(_canonical(base))
    assert [pad.pad_index for pad in pads] == [0, 1]


def test_facility_landing_pads_are_immutable_and_self_carrying() -> None:
    """Pad records are frozen models and the tuple is immutable; every record
    carries its own identity, geometry, rotation and explicit frame."""
    fixture = _load_frontend_fixture()
    case = next(entry for entry in fixture["cases"] if entry["case_id"] == "hub_ground_2pads_rot30")
    pads = facility_landing_pads(_canonical(case["facility"]))
    assert isinstance(pads, tuple)
    for pad in pads:
        with pytest.raises(ValueError):
            pad.x = 999.0  # type: ignore[misc]
    assert all(pad.frame == "scene_east_south_m" for pad in pads)


def test_pad_record_rejects_nonfinite_and_bad_types() -> None:
    with pytest.raises(ValueError):
        FacilityLandingPad(
            facility_id="f", pad_index=0, x=math.nan, y=0, z=0,
            width_m=4.2, depth_m=4.2, rotation_deg=0, frame=PAD_FRAME,
        )
    with pytest.raises(ValueError):
        FacilityLandingPad(
            facility_id="f", pad_index=-1, x=0, y=0, z=0,
            width_m=4.2, depth_m=4.2, rotation_deg=0, frame=PAD_FRAME,
        )
    with pytest.raises(ValueError):
        FacilityLandingPad(
            facility_id="f", pad_index=0, x=0, y=0, z=0,
            width_m=0, depth_m=4.2, rotation_deg=0, frame=PAD_FRAME,
        )
    with pytest.raises(ValueError):
        FacilityLandingPad(
            facility_id="f", pad_index=0, x=0, y=0, z=math.inf,
            width_m=4.2, depth_m=4.2, rotation_deg=0, frame=PAD_FRAME,
        )
    with pytest.raises(ValueError):
        FacilityLandingPad(
            facility_id="f", pad_index=0, x=0, y=0, z=0,
            width_m=4.2, depth_m=4.2, rotation_deg=math.inf, frame=PAD_FRAME,
        )
    with pytest.raises(ValueError, match="frame"):
        FacilityLandingPad(
            facility_id="f", pad_index=0, x=0, y=0, z=0,
            width_m=4.2, depth_m=4.2, rotation_deg=0, frame="run_local_m",
        )


def test_fixture_exists_and_records_provenance() -> None:
    fixture = _load_frontend_fixture()
    assert fixture["schema"] == "frontend_facility_landing_pads_v1"
    assert fixture["frame"] == "scene_east_south_m"
    assert len(fixture["model_source_sha256"]) == 64
    assert len(fixture["route_source_sha256"]) == 64
    assert fixture["frontend_commit"]
    assert fixture["node_version"]
    assert len(fixture["cases"]) == 8

    # Stale-fixture guard: the captured parity is only meaningful while the
    # fixture was produced from the exact frontend source bytes that live in
    # this checkout.  A future frontend change must fail here instead of
    # silently leaving the Python parity green against an outdated fixture.
    # This is test-only provenance IO; it never reads production data.
    repo_root = Path(__file__).resolve().parents[2]
    model_src = repo_root / "frontend" / "src" / "city-facility-models.ts"
    route_src = repo_root / "frontend" / "src" / "city-selected-dispatch-route.ts"
    assert model_src.is_file(), "frontend model source must be present in the checkout"
    assert route_src.is_file(), "frontend route source must be present in the checkout"
    assert fixture["model_source_sha256"] == hashlib.sha256(model_src.read_bytes()).hexdigest()
    assert fixture["route_source_sha256"] == hashlib.sha256(route_src.read_bytes()).hexdigest()
