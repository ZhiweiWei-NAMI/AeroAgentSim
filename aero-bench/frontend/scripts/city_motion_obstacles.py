"""Validate the current rendered building and effective fixture obstacle contract."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

from city_fixture_geometry import (displayed_fixture_triangles, fixture_display_transform, fixture_in_height_band,
                                   glb_triangles, projected_fixture, world_fixture_triangles)
from city_effective_fixtures import MOTOR_TOP_UP_M
from city_preview_coordinates import source_context_bytes

ROOT = Path(__file__).resolve().parents[2]
MOTION_POLICY = "canonical-rendered-building-footprints-and-effective-fixtures"
FIXTURE_SCHEMA = "aero-bench.city-effective-fixture-geometry/v1"
BASIS_SCHEMA = "aero-bench.city-motion-obstacle-basis/v2"


def document_sha256(document: object) -> str:
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def fixture_model_path(url: str) -> Path:
    if not isinstance(url, str) or not url.startswith("/") or any(
            part in ("", ".", "..") for part in url.split("/")[1:]) or "?" in url or "#" in url:
        raise ValueError("Fixture model requires an absolute public asset URL")
    return ROOT / "frontend/public" / url.lstrip("/")


def fixture_polygon(part: dict) -> Polygon:
    for ring in [part["outline"], *part["holes"]]:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError("Fixture footprints require explicitly closed outer and hole rings")
    polygon = Polygon(part["outline"], part["holes"])
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Fixture footprint is invalid; geometry repair is not permitted")
    return polygon


def road_surface_polygon(part: dict) -> Polygon:
    """Parse one displayed road/walkbed part in the road builder's open-ring format."""
    for ring in [part["outline"], *part["holes"]]:
        if len(ring) < 3 or ring[0] == ring[-1]:
            raise ValueError("Road surface parts require open outer and hole rings of at least three points")
    polygon = Polygon(part["outline"], part["holes"])
    if polygon.is_empty or not polygon.is_valid or polygon.area <= 0:
        raise ValueError("Road surface part is invalid; geometry repair is not permitted")
    return polygon


def world_projected_fixture(triangles, kind: str, location: dict, band_up_m: tuple[float, float] | None = None):
    """Project actual transformed triangles, optionally clipped to a vertical band.

    Vertices are transformed before the union, retaining valid micro holes.
    """
    actual = world_fixture_triangles(triangles, kind, location)
    occupied = projected_fixture(actual) if band_up_m is None else fixture_in_height_band(actual, *band_up_m)
    if occupied.is_empty or not occupied.is_valid:
        raise ValueError("Transformed actual fixture triangles have empty or invalid projected occupied geometry")
    return occupied


def load_effective_fixtures(path: Path, source_context: dict, signals: list[dict],
                            street_lamps: list[dict], surface_sha256: str) -> tuple[list, dict]:
    """Re-derive each effective projected fixture from its actual displayed GLB."""
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != FIXTURE_SCHEMA or data.get("source_context") != source_context:
        raise ValueError("Effective fixture geometry differs from the current rendered source context")
    if data.get("displayed_surface_sha256") != surface_sha256:
        raise ValueError("Effective fixtures differ from the displayed road/walkbed surfaces")
    inventories = {"signals": signals, "street_lamps": street_lamps}
    if data.get("source_inventories") != inventories:
        raise ValueError("Effective fixture inventories differ from the current native derived placements")
    if data.get("source_inventory_sha256") != {
            key: document_sha256(value) for key, value in inventories.items()}:
        raise ValueError("Effective fixture inventory digests differ from the actual inventories")
    sources = {"signal": signals, "street_lamp": street_lamps}
    models = {}
    for kind in sources:
        model = data["models"][kind]
        if model.get("display_transform") != fixture_display_transform(kind):
            raise ValueError("Fixture declared display transform differs from the current viewer transform")
        model_path = fixture_model_path(model["url"])
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != model["sha256"]:
            raise ValueError(f"{kind}: actual displayed fixture GLB differs from its model pin")
        models[kind] = displayed_fixture_triangles(glb_triangles(model_path), kind)
    extents = {kind: (float(triangles[:, :, 1].min()), float(triangles[:, :, 1].max()))
               for kind, triangles in models.items()}
    effective = data["effective_fixtures"]
    if not isinstance(effective, list):
        raise ValueError("Effective fixture inventory must be explicit")
    seen = {kind: set() for kind in sources}
    polygons = []
    for item in effective:
        kind, index = item["kind"], item["source_index"]
        if kind not in sources or isinstance(index, bool) or not isinstance(index, int) \
                or not 0 <= index < len(sources[kind]) or index in seen[kind]:
            raise ValueError("Effective fixture source index is invalid or duplicated")
        seen[kind].add(index)
        location = sources[kind][index]
        if item["source_location"] != location or item["model_sha256"] != data["models"][kind]["sha256"]:
            raise ValueError("Effective fixture placement/model differs from the source inventory")
        if any(isinstance(item[key], bool) or not isinstance(item[key], (int, float))
                or not math.isfinite(item[key]) for key in ("base_up_m", "top_up_m")):
            raise ValueError("Effective fixture vertical extent must be finite")
        parts = item["footprints"]
        if not isinstance(parts, list) or not parts:
            raise ValueError("Effective fixture has no measured projected model footprint")
        stored = unary_union([fixture_polygon(part) for part in parts])
        actual = world_projected_fixture(models[kind], kind, location)
        if actual.symmetric_difference(stored).area > 1e-8:
            raise ValueError("Effective fixture footprint differs from the actual transformed model")
        motion_parts = item["motion_footprints"]
        if not isinstance(motion_parts, list) or not motion_parts:
            raise ValueError("Effective fixture has no measured motor-band model footprint")
        stored_motion = unary_union([fixture_polygon(part) for part in motion_parts])
        actual_motion = world_projected_fixture(models[kind], kind, location, (0, MOTOR_TOP_UP_M))
        if actual_motion.symmetric_difference(stored_motion).area > 1e-8:
            raise ValueError("Effective fixture motor-band footprint differs from the actual transformed model")
        if abs(item["base_up_m"] - extents[kind][0]) > 1e-8 \
                or abs(item["top_up_m"] - extents[kind][1]) > 1e-8:
            raise ValueError("Effective fixture vertical extent differs from the actual model")
        polygons.extend(fixture_polygon(part) for part in motion_parts)
    for kind, omitted_key in [("signal", "omitted_signals"), ("street_lamp", "omitted_street_lamps")]:
        omitted = set()
        for item in data[omitted_key]:
            index = item["source_index"]
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(sources[kind]) \
                    or index in omitted or index in seen[kind] or item["source_location"] != sources[kind][index] \
                    or not isinstance(item["reason"], str) or not item["reason"]:
                raise ValueError("Effective fixture omissions differ from the source inventory")
            omitted.add(index)
        if seen[kind] | omitted != set(range(len(sources[kind]))):
            raise ValueError("Effective and omitted fixtures do not cover the complete source inventory")
    basis = {"schema_version": BASIS_SCHEMA, "policy": MOTION_POLICY,
             "route_obstacle_basis": MOTION_POLICY, "signal_pole_obstacle_basis": MOTION_POLICY,
             "source_context_sha256": hashlib.sha256(source_context_bytes(source_context)).hexdigest(),
             "rendered_footprint_count": source_context["building_geometry"]["count"],
             "building_footprint_contract": source_context["building_geometry"]["source_footprint_contract"],
             "effective_fixture_geometry_sha256": hashlib.sha256(raw).hexdigest(),
             "effective_fixture_geometry_size_bytes": len(raw),
             "effective_fixture_count": len(effective), "effective_fixture_polygon_count": len(polygons),
             "route_fixture_geometry": "actual-displayed-triangles-clipped-to-declared-motor-height-band",
             "motor_height_band_up_m": [0, MOTOR_TOP_UP_M]}
    return polygons, basis


def validate_recording_road(road: dict, context: dict) -> str:
    """Require the complete physical gate, rather than a detached PASS label."""
    from city_surface_identity import displayed_surface_sha256
    if road.get("schema_version") != "aero-bench.city-road-preview/v3" or road.get("source_context") != context:
        raise ValueError("Traffic recording requires the actual current v3 road/source context")
    surface_sha = displayed_surface_sha256(road["roadbed"], road["walkbed"])
    if road.get("displayed_surface_sha256") != surface_sha:
        raise ValueError("Traffic recording road geometry differs from its displayed surface identity")
    physical = road.get("physical_clearance", {})
    lanes, coverage = physical.get("native_lane_clearance", {}), physical.get("native_lane_surface_coverage", {})
    if physical.get("status") != "PASS" or lanes.get("status") != "PASS" or coverage.get("status") != "PASS" \
            or lanes.get("policy") != "actual-rendered-building-footprints-and-native-lane-ribbons" \
            or type(lanes.get("pair_count")) is not int or lanes["pair_count"] != 0 \
            or lanes.get("contact_area_sum_per_lane_m2") != 0 or lanes.get("rows") != [] \
            or lanes.get("unrenderable_generated_areas") != [] or coverage.get("rows") != [] \
            or lanes.get("numerical_area_epsilon_m2") != 1e-8 \
            or coverage.get("numerical_area_epsilon_m2") != 1e-8 or coverage.get("surface_seam_tolerance_m") != .003:
        raise ValueError("Real SUMO recording is blocked by the complete native/model physical road clearance gate")
    return surface_sha


def native_road_geometry_gate(network, projection, centre_line_projection, rendered_footprints: list, road: dict) -> dict:
    """Recheck actual native ribbons/model contact and paving independently of labels.

    Walking-area polygons use ``projection``; lane centre lines use ``centre_line_projection``
    (LANE_CENTRE_LINE_DECIMALS). Internal-lane ribbons follow the shared lane_ribbon model.
    """
    from shapely.strtree import STRtree
    from city_road_physical_clearance import internal_lane_neighbours, lane_ribbon
    from city_road_topology import lane_kind
    bodies = STRtree(rendered_footprints)
    neighbours = internal_lane_neighbours(network)
    centre_lines = {lane.get("id"): lane.get("shape") for lane in network.iter("lane")}
    surfaces = {kind:unary_union([road_surface_polygon(part) for part in road[kind]])
                for kind in ("roadbed", "walkbed")}
    contacts, missing, invalid = [], [], []
    lane_count = 0
    for edge in network.findall("edge"):
        function = edge.get("function", "normal")
        for lane in edge.findall("lane"):
            kind = "walk" if function in ("crossing", "walkingarea") else lane_kind(lane)
            if kind not in ("motor", "shared", "cycle", "walk"):
                continue
            points = (centre_line_projection if function in ("normal", "internal") else projection).shape(lane.get("shape"))
            if len(points) < 2 or function == "walkingarea" and len(points) < 3:
                invalid.append({"lane_id":lane.get("id"),"reason":"native-shape-has-too-few-vertices"})
                continue
            # SUMO 1.27.1's explicit network schema default is 3.2 m. This is an
            # engineering default, not a claim that the source measured width.
            width = round(float(lane.get("width", "3.2")), 2)
            if not math.isfinite(width) or width <= 0:
                raise ValueError("Native lane has invalid engineering width")
            entry = exit_ = None
            if function == "internal":
                entry, exit_ = (centre_line_projection.shape(centre_lines[item]) for item in neighbours[lane.get("id")])
            ribbon = Polygon(points) if function == "walkingarea" else lane_ribbon(points, width, entry, exit_)
            if not ribbon.is_valid or ribbon.is_empty or ribbon.area <= 0:
                invalid.append({"lane_id":lane.get("id"),"reason":"native-ribbon-is-invalid-or-degenerate"})
                continue
            lane_count += 1
            for index in bodies.query(ribbon):
                area=ribbon.intersection(rendered_footprints[index]).area
                if area > 1e-8:
                    contacts.append({"lane_id":lane.get("id"),"building_index":int(index),"area_m2":area})
            if function not in ("crossing", "walkingarea"):
                missing_area=ribbon.difference(surfaces["walkbed" if kind == "walk" else "roadbed"].buffer(.003)).area
                if missing_area > 1e-8:
                    missing.append({"lane_id":lane.get("id"),"missing_area_m2":missing_area})
    return {"status":"BLOCKED" if contacts or missing or invalid else "PASS", "native_ribbons_checked":lane_count,
            "model_contacts":contacts,"missing_surface_ribbons":missing,"unrenderable_native_ribbons":invalid,
            "area_epsilon_m2":1e-8,"paving_seam_tolerance_m":.003}
