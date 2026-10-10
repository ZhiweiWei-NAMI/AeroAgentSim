"""Reject road clipping that hides a native lane crossing a rendered building."""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Polygon, box
from shapely import affinity
from shapely.strtree import STRtree

from city_road_topology import lane_kind

NUMERICAL_AREA_EPSILON_M2 = 1e-8
SURFACE_SEAM_TOLERANCE_M = .003
# Native lane centre lines are materialized by netconvert at 0.1 mm and projected at the same
# resolution: a ribbon is buffered from its centre line, and rounding a short end segment more
# coarsely rotates its flat cap and moves the ribbon corner by several millimetres.
LANE_CENTRE_LINE_DECIMALS = 4


def _source_way(edge_id: str, ways: dict) -> str:
    prefix = edge_id.split("#", 1)[0]
    choices = [way_id for way_id in ways if prefix in (way_id, "-" + way_id)]
    if len(choices) != 1:
        raise ValueError(f"Native lane does not bind exactly one source way: {edge_id}")
    return choices[0]


def lane_building_conflicts(network: ET.Element, source: ET.Element, lanes: list[dict],
                            buildings, projection) -> dict:
    """Classify source spine, native offset and ribbon contacts without editing them."""
    polygons = [item.rendered_polygon for item in buildings]
    tree = STRtree(polygons)
    ways = {way.attrib["id"]: way for way in source.findall("way")}
    edges = {edge.attrib["id"]: edge for edge in network.findall("edge")}
    to_native = Transformer.from_crs(CRS.from_epsg(4326),
        CRS.from_proj4(projection.projection), always_xy=True)
    source_nodes = {node.attrib["id"]: projection.point(*to_native.transform(
        float(node.attrib["lon"]), float(node.attrib["lat"]))) for node in source.findall("node")}
    source_lines = {}
    rows = []
    degenerate_generated_areas = []
    degenerate_native_lanes = []
    for lane in lanes:
        if lane["kind"] not in {"motor", "shared", "cycle", "walk"}:
            continue
        edge_id = lane["id"].rsplit("_", 1)[0]
        edge = edges[edge_id]
        function = edge.get("function", "normal")
        way_id, tags = None, {}
        if function == "normal":
            way_id = _source_way(edge_id, ways)
            way = ways[way_id]
            tags = {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
            if way_id not in source_lines:
                points = [source_nodes[node.attrib["ref"]] for node in way.findall("nd")]
                if len(points) < 2:
                    raise ValueError(f"Source way has no coordinate spine: {way_id}")
                source_lines[way_id] = LineString(points)
        if function == "walkingarea" and len(lane["shape"]) < 3:
            degenerate_generated_areas.append({"lane_id": lane["id"], "function": function,
                "reason": "source-generated-walking-area-has-fewer-than-three-vertices"})
            continue
        if function != "walkingarea" and len({tuple(point) for point in lane["shape"]}) < 2:
            native_lane = next(item for item in edge.findall("lane") if item.get("id") == lane["id"])
            degenerate_native_lanes.append({"lane_id": lane["id"], "function": function,
                "native_declared_length_m": float(native_lane.get("length")),
                "reason": "native-lane-has-fewer-than-two-distinct-displayed-vertices"})
            continue
        native = LineString(lane["shape"])
        ribbon = Polygon(lane["shape"]) if function == "walkingarea" else \
            native.buffer(lane["width"] / 2, cap_style=2, join_style=2)
        if not ribbon.is_valid:
            degenerate_generated_areas.append({"lane_id": lane["id"], "function": function,
                "reason": "source-generated-geometry-is-invalid"})
            continue
        for index in tree.query(ribbon):
            polygon, building = polygons[index], buildings[index]
            area = ribbon.intersection(polygon).area
            if area <= NUMERICAL_AREA_EPSILON_M2:
                continue
            source_length = source_lines[way_id].intersection(polygon).difference(polygon.boundary).length if way_id is not None else None
            native_length = native.intersection(polygon).difference(polygon.boundary).length if function != "walkingarea" else None
            source_boundary_length = source_lines[way_id].intersection(polygon.boundary).length if way_id is not None else None
            native_boundary_length = native.intersection(polygon.boundary).length if function != "walkingarea" else None
            classification = "native-generated-geometry-contact" if function != "normal" else \
                "source-spine-enters-building" if source_length > 1e-8 else \
                "native-offset-spine-enters-building" if native_length > 1e-8 else "lane-ribbon-only-contact"
            rows.append({"lane_id": lane["id"], "edge_id": edge_id, "lane_kind": lane["kind"],
                "function": function,
                "source_way_id": way_id, "original_osm_way_id": tags.get("aero_bench:source_id", way_id),
                "source_tags": tags, "native_spread": edge.get("spreadType", "right"),
                "native_lane_width_m": lane["width"], "source_width_is_declared": "width" in tags if way_id is not None else None,
                "building_id": building.object_id, "area_m2": area,
                "source_spine_inside_building_m": source_length, "native_spine_inside_building_m": native_length,
                "source_spine_boundary_contact_m": source_boundary_length, "native_spine_boundary_contact_m": native_boundary_length,
                "classification": classification})
    return {"status": "BLOCKED" if rows or degenerate_generated_areas or degenerate_native_lanes else "PASS", "policy": "actual-rendered-building-footprints-and-native-lane-ribbons",
        "source_width_missing_is_not_a_measurement": True, "numerical_area_epsilon_m2": NUMERICAL_AREA_EPSILON_M2,
        "pair_count": len(rows), "contact_area_sum_per_lane_m2": sum(row["area_m2"] for row in rows),
        "rows": rows, "unrenderable_generated_areas": degenerate_generated_areas,
        "unrenderable_native_lanes": degenerate_native_lanes}


def internal_lane_neighbours(network: ET.Element) -> dict[str, tuple[str, str]]:
    """Map each internal lane to the lane that enters it and the lane it enters."""
    entering = {connection.get("via"): f'{connection.get("from")}_{connection.get("fromLane")}'
                for connection in network.findall("connection") if connection.get("via")}
    leaving = {f'{connection.get("from")}_{connection.get("fromLane")}': f'{connection.get("to")}_{connection.get("toLane")}'
               for connection in network.findall("connection") if connection.get("from", "").startswith(":")}
    return {lane_id: (entering[lane_id], leaving[lane_id]) for lane_id in entering if lane_id in leaving}


def _end_direction(shape, at_end: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    points = [tuple(point) for point in (reversed(shape) if at_end else shape)]
    anchor = points[0]
    other = next((point for point in points[1:] if point != anchor), None)
    if other is None:
        raise ValueError("Lane shape has no distinct direction at its end")
    dx, dy = (anchor[0] - other[0], anchor[1] - other[1]) if at_end else (other[0] - anchor[0], other[1] - anchor[1])
    length = (dx * dx + dy * dy) ** .5
    return anchor, (dx / length, dy / length)


def lane_ribbon(shape, width: float, entry_shape=None, exit_shape=None):
    """Native lane ribbon: flat caps and mitre joins along the lane centre line.

    An internal lane begins at the end line of the lane entering it and ends at the start line
    of the lane it enters. A flat cap square to the internal lane's own first or last segment
    reaches across that line when the two directions differ; within one lane width of each end
    the part across the adjoining lane's line is not traversed and is removed.
    """
    ribbon = LineString(shape).buffer(width / 2, cap_style=2, join_style=2)
    for adjoining, at_end in ((entry_shape, True), (exit_shape, False)):
        if adjoining is None:
            continue
        (x, y), (dx, dy) = _end_direction(adjoining, at_end)
        # Untraversed side of the line: behind the entering lane's end, or past the next lane's start.
        cut = box(x - width, y - width, x, y + width) if at_end else box(x, y - width, x + width, y + width)
        ribbon = ribbon.difference(affinity.rotate(cut, math.atan2(dy, dx), origin=(x, y), use_radians=True))
    return ribbon


def _lane_points(lane: ET.Element) -> list[tuple[float, float]]:
    return [tuple(float(value) for value in token.split(",")[:2]) for token in lane.get("shape").split()]


def walkway_vehicle_overlaps(network: ET.Element) -> list[dict]:
    """Normal sidewalk lanes whose ribbon overlaps a vehicle lane deeper than the road-walk seam.

    Such a sidewalk cannot keep its own paving: the displayed roadbed takes the overlap.
    Vehicle ribbons of internal lanes are aligned to their adjoining lane ends (lane_ribbon).
    """
    neighbours = internal_lane_neighbours(network)
    lanes = {lane.get("id"): (edge, lane) for edge in network.findall("edge") for lane in edge.findall("lane")}

    def ribbon(lane_id: str):
        edge, lane = lanes[lane_id]
        entry = exit_ = None
        if edge.get("function") == "internal":
            entry, exit_ = (_lane_points(lanes[item][1]) for item in neighbours[lane_id])
        return lane_ribbon(_lane_points(lane), float(lane.get("width")), entry, exit_)

    def drawable(lane: ET.Element) -> bool:
        return len(set(_lane_points(lane))) >= 2

    vehicle_ids = [lane_id for lane_id, (edge, lane) in lanes.items()
                   if edge.get("function", "normal") in ("normal", "internal")
                   and lane_kind(lane) in ("motor", "shared", "cycle") and drawable(lane)]
    vehicle = [ribbon(lane_id) for lane_id in vehicle_ids]
    tree = STRtree(vehicle)
    records = []
    for lane_id, (edge, lane) in lanes.items():
        if edge.get("function", "normal") != "normal" or lane_kind(lane) != "walk" or not drawable(lane):
            continue
        walk = ribbon(lane_id).buffer(-SURFACE_SEAM_TOLERANCE_M / 2, join_style=2)
        crossed = sorted((vehicle_ids[index], walk.intersection(vehicle[index]).area) for index in tree.query(walk))
        crossed = [{"lane_id": other, "overlap_m2": round(area, 4)} for other, area in crossed if area > NUMERICAL_AREA_EPSILON_M2]
        if crossed:
            records.append({"lane_id": lane_id, "edge_id": edge.get("id"), "rule": "sidewalk-overlaps-vehicle-lane",
                            "vehicle_lanes": crossed})
    return records


def surface_lane_coverage(lanes: list[dict], roadbed, walkbed) -> dict:
    """Require the full native motor and dedicated walking ribbons to remain paved."""
    rows = []
    for lane in lanes:
        if lane["kind"] not in {"motor", "shared", "cycle", "walk"}:
            continue
        if lane.get("function") in {"walkingarea", "crossing"} or "function" not in lane and lane["id"].startswith(":"):
            continue  # Crossing paving is checked by the crossing/walking-area endpoint gate.
        ribbon = lane_ribbon(lane["shape"], lane["width"], lane.get("entry_shape"), lane.get("exit_shape"))
        surface = walkbed if lane["kind"] == "walk" else roadbed
        missing = ribbon.difference(surface.buffer(SURFACE_SEAM_TOLERANCE_M))
        if missing.area > NUMERICAL_AREA_EPSILON_M2:
            rows.append({"lane_id": lane["id"], "kind": lane["kind"],
                "missing_area_m2": missing.area, "missing_component_count": len(
                    missing.geoms) if hasattr(missing, "geoms") else 1,
                "reason": "native-lane-ribbon-missing-displayed-paving"})
    return {"status": "BLOCKED" if rows else "PASS", "surface_seam_tolerance_m": SURFACE_SEAM_TOLERANCE_M,
        "numerical_area_epsilon_m2": NUMERICAL_AREA_EPSILON_M2, "rows": rows}
