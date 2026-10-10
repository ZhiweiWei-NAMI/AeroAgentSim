"""Prism safety uses the pinned region's declared vertical reference."""

import pytest

from aero_bench.tasks.inspection.formal_v2_contracts import (
    InspectionFormalEvidenceBundleV2, PolygonPrismV2,
)
from aero_bench.tasks.inspection.formal_v2_sealed import _region_prism
from aero_bench.tasks.inspection.formal_v2_verifier import (
    _point_inside_prism, _sample_prism_position, _segment_inside_prism,
    _segment_intersects_no_fly_prism, verify_formal_v2,
)
from aero_bench.world import frame_math
from aero_bench.world.contracts import RegionGeometry
from aero_bench.world.resolved import ResolvedCoordinate, ResolvedRegion
from tests.test_inspection_formal_v2 import _build_bundle_dict, _issue_context


ORIGIN = frame_math.EnuTransform.from_origin(longitude_deg=120.0, latitude_deg=30.0, altitude_m=30.0)
FOOTPRINT = ((0.0, 0.0), (1000.0, 0.0), (1000.0, 300.0), (0.0, 300.0))


def _coordinate(east, north, up):
    point = frame_math.Vector3(east, north, up)
    ecef = ORIGIN.ecef_from_enu(point)
    longitude, latitude, ellipsoid = ORIGIN.enu_to_geodetic(point)
    amsl = ellipsoid - 10.0
    return ResolvedCoordinate.model_validate({
        "enu": {"east_m": east, "north_m": north, "up_m": up},
        "ned": {"north_m": north, "east_m": east, "down_m": -up},
        "ecef": {"x_m": ecef.x, "y_m": ecef.y, "z_m": ecef.z},
        "wgs84": {"longitude_deg": longitude, "latitude_deg": latitude, "ellipsoid_height_m": ellipsoid},
        "geoid_separation_m": 10.0, "amsl_m": amsl,
        "terrain_amsl_m": 20.0, "agl_m": amsl - 20.0,
    })


def _region(reference):
    lower_height, upper_height = (0.0, 180.0) if reference == "enu_up" else (20.0, 200.0)

    def vertex(east, north, height):
        if reference == "enu_up":
            return _coordinate(east, north, height)
        up = height - 20.0
        for _ in range(5):
            coordinate = _coordinate(east, north, up)
            up += height - coordinate.amsl_m
        return _coordinate(east, north, up)

    geometry = RegionGeometry.model_validate({
        "footprint_enu_m": [{"east_m": east, "north_m": north} for east, north in FOOTPRINT],
        "min_altitude_m": lower_height, "max_altitude_m": upper_height,
        "height_reference": reference,
    })
    region = ResolvedRegion(
        region_id="region.test", kind="geofence", communications_shadow_attenuation_db=None,
        anchor_east_m=0.0, anchor_north_m=0.0,
        lower_vertices=tuple(vertex(east, north, lower_height) for east, north in FOOTPRINT),
        upper_vertices=tuple(vertex(east, north, upper_height) for east, north in FOOTPRINT),
    )
    return region, geometry


@pytest.mark.parametrize("point,inside", [
    ((500.0, 100.0, 90.0), True), ((0.0, 0.0, 0.0), True),
    ((1000.0, 300.0, 180.0), True), ((-0.001, 100.0, 90.0), False),
    ((500.0, 300.001, 90.0), False), ((500.0, 100.0, -0.001), False),
    ((500.0, 100.0, 180.001), False),
])
def test_kilometre_enu_region_accepts_curvature_and_preserves_boundary(point, inside):
    region, geometry = _region("enu_up")
    assert max(v.amsl_m for v in region.lower_vertices) - min(v.amsl_m for v in region.lower_vertices) > 0.07
    prism = _region_prism(region, geometry=geometry)
    assert prism.vertical_reference == "enu_up"
    assert (prism.min_altitude_m, prism.max_altitude_m) == (0.0, 180.0)
    assert _point_inside_prism(frame_math.Vector3(*point), prism,
                               horizontal_margin_m=0.0, vertical_margin_m=0.0) is inside


def test_enu_segments_and_uncertainty_keep_geofence_and_no_fly_checks():
    region, geometry = _region("enu_up")
    prism = _region_prism(region, geometry=geometry)
    start = frame_math.Vector3(100.0, 100.0, 50.0)
    inside = frame_math.Vector3(900.0, 100.0, 50.0)
    outside = frame_math.Vector3(1001.0, 100.0, 50.0)
    assert _segment_inside_prism(start, inside, prism, horizontal_margin_m=0.2, vertical_margin_m=0.1)
    assert not _segment_inside_prism(start, outside, prism, horizontal_margin_m=0.2, vertical_margin_m=0.1)
    assert not _point_inside_prism(frame_math.Vector3(0.1, 100.0, 50.0), prism,
                                   horizontal_margin_m=0.2, vertical_margin_m=0.1)
    assert _segment_intersects_no_fly_prism(frame_math.Vector3(-1.0, 100.0, 50.0), outside,
                                           prism, horizontal_margin_m=0.0, vertical_margin_m=0.0)
    assert not _segment_intersects_no_fly_prism(frame_math.Vector3(-1.0, 100.0, 181.0),
                                               frame_math.Vector3(1001.0, 100.0, 181.0),
                                               prism, horizontal_margin_m=0.0, vertical_margin_m=0.1)


def test_amsl_region_retains_amsl_bounds_and_sample_selection():
    region, geometry = _region("amsl")
    prism = _region_prism(region, geometry=geometry)
    assert prism.vertical_reference == "amsl"
    assert prism.min_altitude_m == pytest.approx(20.0, abs=1e-6)
    assert prism.max_altitude_m == pytest.approx(200.0, abs=1e-6)
    bundle = InspectionFormalEvidenceBundleV2.model_validate(_build_bundle_dict())
    sample = bundle.physical_trace.samples[0].model_copy(update={"altitude_amsl_m": 50.0})
    assert _sample_prism_position(sample, prism).z == 50.0
    enu_region, enu_geometry = _region("enu_up")
    assert _sample_prism_position(sample, _region_prism(enu_region, geometry=enu_geometry)).z == sample.position_enu.z_m
    assert _point_inside_prism(frame_math.Vector3(500.0, 100.0, 50.0), prism,
                               horizontal_margin_m=0.0, vertical_margin_m=0.0)
    assert not _point_inside_prism(frame_math.Vector3(500.0, 100.0, 0.0), prism,
                                   horizontal_margin_m=0.0, vertical_margin_m=0.0)


@pytest.mark.parametrize("reference,expected", [("enu_up", True), ("amsl", False)])
def test_full_safety_evaluation_selects_each_prism_datum(reference, expected):
    raw = _build_bundle_dict()
    for sample in raw["physical_trace"]["samples"]:
        sample["altitude_amsl_m"] += 1000.0
        sample["terrain_altitude_amsl_m"] += 1000.0
    for field in ("geofences", "no_fly_prisms"):
        for prism in raw["mission_context"][field]:
            prism["vertical_reference"] = reference
    raw["mission_context"] = _issue_context(raw["mission_context"])
    evaluation = verify_formal_v2(InspectionFormalEvidenceBundleV2.model_validate(raw))
    assert next(c for c in evaluation.components if c.component_id == "geofence_containment").passed is expected


def test_nonlevel_declared_plane_still_rejected_without_flattening():
    region, geometry = _region("enu_up")
    vertices = list(region.lower_vertices)
    vertices[1] = _coordinate(1000.0, 0.0, 0.01)
    with pytest.raises(ValueError, match="planes are not level"):
        _region_prism(region.model_copy(update={"lower_vertices": tuple(vertices)}), geometry=geometry)


def test_prism_requires_explicit_datum_and_rejects_unsupported_geometry():
    with pytest.raises(ValueError, match="vertical_reference"):
        PolygonPrismV2.model_validate({"prism_id": "region.test", "vertices_enu": [
            {"east_m": e, "north_m": n} for e, n in FOOTPRINT],
            "min_altitude_m": 0.0, "max_altitude_m": 180.0})
    region, geometry = _region("enu_up")
    with pytest.raises(ValueError, match="unsupported formal region vertical reference"):
        _region_prism(region, geometry=geometry.model_copy(update={"height_reference": "agl"}))
