"""Native junction completion for authored ground streets.

Rules, each bound to a declared source fact and applied through real netconvert
inputs (node and connection files); nothing is clipped, relabelled or drawn by hand:

- crop-cut road end: a junction on the declared ENU crop rectangle that joins a single
  road is where the crop cut a longer road. It is an outer-fringe node, so netconvert does
  not join the two sidewalks across the cut carriageway.
- road end inside the crop: a junction joining one two-way road whose two directions
  both carry a pedestrian lane receives an unmarked, unprioritized crossing over that
  road, the native SUMO model for pedestrians crossing a carriageway end.
- crop remnant consumed by its junction: a designed edge between a crop-cut road end and
  one junction whose native lane netconvert could not place on its designed spine (it is
  shorter than designed and lies more than half a lane width off that spine) has no road
  segment inside the crop outside the junction. Its road is excluded, with the measured
  native evidence, and the network is materialized again.
- turnaround across a walkway (applied by the physical refinement): a native turnaround
  movement whose turning lane ribbon overlaps a pedestrian lane ribbon at the same junction
  deeper than the road-walk surface seam would drive vehicles over that walkway, and the
  area cannot be paved as both. The movement is deleted.
- collapsed pass-through: a junction joining one incoming and one outgoing edge of equal
  lane widths, whose straight internal lanes have fewer than two drawable vertices,
  receives one explicit junction pad aligned with the movement bisector: one lane width
  along the movement and the whole carriageway across it, centred between the lane ends.
  The pad lies inside the lane ribbons.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from typing import Mapping, Sequence

from shapely.geometry import LineString, Point, Polygon

from city_road_physical_clearance import NUMERICAL_AREA_EPSILON_M2, SURFACE_SEAM_TOLERANCE_M
from city_road_topology import lane_kind

CROP_TOLERANCE_M = 0.05
COMMON_SCENE_SCHEMA = "aero-bench.urban-scene-common/v1"


def crop_rectangle(common_scene: Mapping, origin: Mapping[str, float]) -> dict[str, float]:
    """Return the declared ENU crop bounds after checking the scene shares the canonical origin."""
    if common_scene.get("schema_version") != COMMON_SCENE_SCHEMA:
        raise ValueError("Crop source is not an urban common-scene document")
    declared = common_scene.get("origin", {})
    if any(declared.get(key) != origin[key] for key in ("latitude_deg", "longitude_deg", "ellipsoid_height_m")):
        raise ValueError("Crop source and canonical city origins differ")
    bounds = common_scene["crop"]["enu_bounds_m"]
    result = {key: float(bounds[key]) for key in ("min_east_m", "max_east_m", "min_north_m", "max_north_m")}
    if not all(math.isfinite(value) for value in result.values()) \
            or result["min_east_m"] >= result["max_east_m"] or result["min_north_m"] >= result["max_north_m"]:
        raise ValueError("Declared crop rectangle is not a finite rectangle")
    return result


def on_crop_boundary(east: float, north: float, crop: Mapping[str, float]) -> bool:
    inside = crop["min_east_m"] - CROP_TOLERANCE_M <= east <= crop["max_east_m"] + CROP_TOLERANCE_M \
        and crop["min_north_m"] - CROP_TOLERANCE_M <= north <= crop["max_north_m"] + CROP_TOLERANCE_M
    edge = min(abs(east - crop["min_east_m"]), abs(east - crop["max_east_m"]),
               abs(north - crop["min_north_m"]), abs(north - crop["max_north_m"]))
    return inside and edge <= CROP_TOLERANCE_M


def _single_road(edge_ids: Sequence[str]) -> bool:
    """One edge, or one edge and its SUMO reverse (the reverse id prefixes the forward id with '-')."""
    if len(edge_ids) == 1:
        return True
    if len(edge_ids) != 2:
        return False
    first, second = edge_ids
    return first == "-" + second or second == "-" + first


def road_end_completion(network: ET.Element, designed: Sequence[Mapping], projection,
                        crop: Mapping[str, float]) -> tuple[ET.Element, ET.Element, list[dict]]:
    """Node and connection patches for crop-cut and interior road ends of the designed network."""
    designed_ids = {item["edge_id"]: item for item in designed}
    incident: dict[str, list[ET.Element]] = {}
    for edge in network.findall("edge"):
        if edge.get("function", "normal") == "normal" and edge.get("id") in designed_ids:
            for side in ("from", "to"):
                incident.setdefault(edge.get(side), []).append(edge)
    nodes, connections, records = ET.Element("nodes"), ET.Element("connections"), []
    for junction in network.findall("junction"):
        junction_id = junction.get("id")
        edges = incident.get(junction_id, [])
        if not edges or not _single_road([edge.get("id") for edge in edges]):
            continue
        # CityEnuProjection returns viewer (east, -north).
        east, south = projection.point(float(junction.get("x")), float(junction.get("y")))
        edge_ids = sorted(edge.get("id") for edge in edges)
        if on_crop_boundary(east, -south, crop):
            ET.SubElement(nodes, "node", id=junction_id, fringe="outer")
            records.append({"junction_id": junction_id, "rule": "crop-cut-road-end", "edges": edge_ids,
                            "enu_m": [east, -south], "netconvert": "fringe=outer"})
            continue
        walkable = [edge for edge in edges if any("pedestrian" in lane["allowed"]
                                                   for lane in designed_ids[edge.get("id")]["lanes"])]
        if len(edges) == 2 and len(walkable) == 2:
            ET.SubElement(connections, "crossing", node=junction_id, edges=" ".join(edge_ids), priority="false")
            records.append({"junction_id": junction_id, "rule": "interior-road-end-crossing", "edges": edge_ids,
                            "enu_m": [east, -south], "netconvert": "crossing priority=false (unmarked)"})
    return nodes, connections, records


def _points(lane: ET.Element) -> list[tuple[float, float]]:
    return [tuple(float(value) for value in token.split(",")[:2]) for token in lane.get("shape").split()]


def _unit(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length == 0:
        raise ValueError("Lane end segment has zero length")
    return dx / length, dy / length


def collapsed_passthrough_pads(network: ET.Element, projection) -> tuple[list[ET.Element], list[dict]]:
    """Junction pads for one-way or reciprocal straight pass-through movements.

    The pad spans the exact local-axis bounds of every participating lane-end
    section.  No turn, side road, lane drop, or nonreciprocal second movement is
    eligible for this geometry completion rule.
    """
    lanes = {lane.get("id"): lane for edge in network.findall("edge") for lane in edge.findall("lane")}
    junctions = {junction.get("id"): junction for junction in network.findall("junction")}
    normal = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
    collapsed: dict[str, list[ET.Element]] = {}
    for connection in network.findall("connection"):
        via = connection.get("via")
        if not via or via not in lanes or len({tuple(point) for point in projection.shape(lanes[via].get("shape"))}) >= 2:
            continue
        collapsed.setdefault(normal[connection.get("from")].get("to"), []).append(connection)
    pads, records = [], []
    for junction_id, connections in sorted(collapsed.items()):
        movement_connections: dict[tuple[str, str], list[ET.Element]] = {}
        for connection in connections:
            movement_connections.setdefault((connection.get("from"), connection.get("to")), []).append(connection)
        movements = sorted(movement_connections)
        touching = [edge for edge in normal.values() if junction_id in (edge.get("from"), edge.get("to"))]
        involved = {edge_id for movement in movements for edge_id in movement}
        valid = len(movements) in (1, 2) and involved == {edge.get("id") for edge in touching} \
            and len(involved) == 2 * len(movements) \
            and all(connection.get("dir") == "s" for connection in connections)
        movement_rows = []
        for source_id, target_id in movements:
            incoming, outgoing = normal[source_id], normal[target_id]
            source_lanes = {lane.get("index"): lane for lane in incoming.findall("lane")}
            target_lanes = {lane.get("index"): lane for lane in outgoing.findall("lane")}
            linked = movement_connections[(source_id, target_id)]
            if incoming.get("to") != junction_id or outgoing.get("from") != junction_id \
                    or {item.get("fromLane") for item in linked} != set(source_lanes) \
                    or {item.get("toLane") for item in linked} != set(target_lanes) \
                    or any(source_lanes[item.get("fromLane")].get("width")
                           != target_lanes[item.get("toLane")].get("width") for item in linked):
                valid = False
                continue
            first_source = source_lanes[sorted(source_lanes, key=int)[0]]
            first_target = target_lanes[sorted(target_lanes, key=int)[0]]
            a, b = _points(first_source)[-2:]
            c, d = _points(first_target)[:2]
            u, v = _unit(a, b), _unit(c, d)
            tx, ty = u[0] + v[0], u[1] + v[1]
            norm = math.hypot(tx, ty)
            if norm == 0:
                valid = False
                continue
            movement_rows.append({
                "movement": (source_id, target_id),
                "incoming": tuple(source_lanes.values()),
                "outgoing": tuple(target_lanes.values()),
                "tangent": (tx / norm, ty / norm),
                "heading_change_deg": math.degrees(math.acos(max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1])))),
            })
        if len(movements) == 2 and valid:
            first, second = movement_rows
            valid = normal[first["movement"][0]].get("from") == normal[second["movement"][1]].get("to") \
                and normal[first["movement"][1]].get("to") == normal[second["movement"][0]].get("from")
        if not valid or len(movement_rows) != len(movements):
            raise ValueError(f"Collapsed internal lanes at {junction_id} are not a straight equal-width pass-through; "
                             "no junction completion rule applies")
        tx, ty = movement_rows[0]["tangent"]
        nx, ny = -ty, tx
        lane_ends = []
        for row in movement_rows:
            lane_ends.extend((_points(lane)[-1], float(lane.get("width"))) for lane in row["incoming"])
            lane_ends.extend((_points(lane)[0], float(lane.get("width"))) for lane in row["outgoing"])
        tangent_min = min(x * tx + y * ty - width / 2 for (x, y), width in lane_ends)
        tangent_max = max(x * tx + y * ty + width / 2 for (x, y), width in lane_ends)
        normal_min = min(x * nx + y * ny - width / 2 for (x, y), width in lane_ends)
        normal_max = max(x * nx + y * ny + width / 2 for (x, y), width in lane_ends)
        corners = [(tx * along + nx * across, ty * along + ny * across)
                   for along, across in ((tangent_min, normal_min), (tangent_max, normal_min),
                                         (tangent_max, normal_max), (tangent_min, normal_max))]
        shape = " ".join(f"{px:.2f},{py:.2f}" for px, py in corners)
        pads.append(ET.Element("node", id=junction_id, shape=shape))
        record = {"junction_id": junction_id, "rule": "collapsed-pass-through-pad",
                  "internal_lanes": sorted(connection.get("via") for connection in connections),
                  "heading_change_deg": round(max(row["heading_change_deg"] for row in movement_rows), 3),
                  "pad_along_m": round(tangent_max - tangent_min, 6),
                  "pad_across_m": round(normal_max - normal_min, 6), "netconvert": f"node shape={shape}"}
        if len(movements) == 1:
            record["movement"] = list(movements[0])
        else:
            record["movements"] = [list(movement) for movement in movements]
        records.append(record)
    return pads, records


def consumed_crop_remnants(network: ET.Element, designed: Sequence[Mapping], crop_nodes: set[str],
                           projection) -> list[dict]:
    """Designed crop-remnant edges whose native lanes netconvert placed inside their junction."""
    edges = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
    remnants = []
    for item in designed:
        edge = edges[item["edge_id"]]
        if not crop_nodes & {edge.get("from"), edge.get("to")}:
            continue
        lanes = []
        for lane in item["lanes"]:
            native = edge.find(f"lane[@index='{lane['lane_index']}']")
            spine = LineString(projection.shape(lane["shape"]))
            offset = max(spine.distance(Point(point)) for point in projection.shape(native.get("shape")))
            lanes.append({"lane_id": native.get("id"), "designed_length_m": round(spine.length, 3),
                          "native_length_m": float(native.get("length")), "native_offset_from_design_m": round(offset, 3),
                          "half_width_m": float(native.get("width")) / 2})
        if lanes and all(row["native_length_m"] < row["designed_length_m"] and row["native_offset_from_design_m"] > row["half_width_m"]
                         for row in lanes):
            remnants.append({"edge_id": item["edge_id"], "rule": "crop-remnant-consumed-by-junction", "lanes": lanes,
                             "junction_id": edge.get("to") if edge.get("from") in crop_nodes else edge.get("from")})
    return remnants


def turnarounds_over_walkways(network: ET.Element) -> list[dict]:
    """Turnaround movements whose native turning lane overlaps a pedestrian lane of the same junction."""
    lanes = {lane.get("id"): (edge, lane) for edge in network.findall("edge") for lane in edge.findall("lane")}
    walkways: dict[str, list[tuple[str, Polygon]]] = {}
    for edge, lane in lanes.values():
        function = edge.get("function", "normal")
        if function not in ("normal", "internal") or lane_kind(lane) != "walk":
            continue
        points = _points(lane)
        if len(set(points)) < 2:
            continue
        junctions = {edge.get("from"), edge.get("to")} if function == "normal" else {edge.get("id")[1:].rsplit("_", 1)[0]}
        for junction_id in junctions:
            walkways.setdefault(junction_id, []).append(
                (lane.get("id"), LineString(points).buffer(float(lane.get("width")) / 2, cap_style=2, join_style=2)))
    records = []
    for connection in network.findall("connection"):
        via = connection.get("via")
        if connection.get("dir") != "t" or not via:
            continue
        edge, lane = lanes[via]
        points = _points(lane)
        if len(set(points)) < 2:
            continue
        junction_id = edge.get("id")[1:].rsplit("_", 1)[0]
        ribbon = LineString(points).buffer(float(lane.get("width")) / 2, cap_style=2, join_style=2)
        # Overlap deeper than the road-walk surface seam cannot be paved as both road and walkway.
        crossed = [(lane_id, ribbon.intersection(walk_ribbon.buffer(-SURFACE_SEAM_TOLERANCE_M, join_style=2)).area)
                   for lane_id, walk_ribbon in walkways.get(junction_id, [])]
        crossed = [(lane_id, area) for lane_id, area in crossed if area > NUMERICAL_AREA_EPSILON_M2]
        if crossed:
            records.append({"junction_id": junction_id, "rule": "turnaround-across-walkway", "internal_lane": via,
                            "movement": {key: connection.get(key) for key in ("from", "to", "fromLane", "toLane")},
                            "crossed_walkways": [{"lane_id": lane_id, "overlap_m2": round(area, 4)}
                                                 for lane_id, area in sorted(crossed)]})
    return records
