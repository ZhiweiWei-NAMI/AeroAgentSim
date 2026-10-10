"""Show SUMO walking-area links that remain level with the motor roadbed.

These polygons and markings are derived visual guides. SUMO topology does not
establish surveyed paint or exclusive pedestrian right of way.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Iterable
from xml.etree.ElementTree import Element

from shapely import prepare, set_precision
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union
from shapely.validation import make_valid


SOURCE_KIND = "sumo-walkingarea-derived-visual-guide-not-surveyed"
GUIDE_WIDTH_M = 0.12
DASH_LENGTH_M = 0.5
DASH_GAP_M = 0.5
ENDPOINT_LIMIT_M = 0.6
PORT_PATH_SOURCE_KIND = "derived-from-sumo-pedestrian-connection-ports-not-surveyed"
PAVING_SEAM_TOLERANCE_M = 0.003


def _polygons(geometry: BaseGeometry) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _polygons(part)


def _lines(geometry: BaseGeometry) -> Iterable[LineString]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _lines(part)


def _coordinates(points: Any, label: str, minimum: int) -> list[tuple[float, float]]:
    if not isinstance(points, (list, tuple)) or len(points) < minimum:
        raise ValueError(f"{label} needs at least {minimum} coordinates")
    result = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"{label} has an invalid coordinate")
        try:
            x, z = float(point[0]), float(point[1])
        except (TypeError, ValueError) as error:
            raise ValueError(f"{label} has an invalid coordinate") from error
        if not math.isfinite(x) or not math.isfinite(z):
            raise ValueError(f"{label} has a non-finite coordinate")
        if not result or (x, z) != result[-1]:
            result.append((x, z))
    if len(result) < minimum:
        raise ValueError(f"{label} has too few distinct coordinates")
    return result


def _walk_geometry(records: list[dict[str, Any]]) -> dict[str, BaseGeometry]:
    geometries: dict[str, BaseGeometry] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
            raise ValueError("each walking area needs an edge id")
        edge_id = record["id"]
        if edge_id in geometries:
            raise ValueError(f"duplicate walking area: {edge_id}")
        polygon = make_valid(Polygon(_coordinates(record.get("shape"), edge_id, 3)))
        parts = [part for part in _polygons(polygon) if part.area > 1e-8]
        geometries[edge_id] = unary_union(parts) if parts else Polygon()
    return geometries


def _crossing_index(network: Element, crossings: list[dict[str, Any]]) -> tuple[
        dict[str, dict[str, Any]], dict[str, dict[str, set[str]]]]:
    edges = {edge.get("id"): edge for edge in network.findall("edge")}
    lane_to_edge = {}
    for edge_id, edge in edges.items():
        for lane in edge.findall("lane"):
            lane_to_edge[lane.get("id")] = edge_id
    indexed: dict[str, dict[str, Any]] = {}
    adjacency: dict[str, dict[str, set[str]]] = {}
    for crossing in crossings:
        if not isinstance(crossing, dict) or not isinstance(crossing.get("id"), str):
            raise ValueError("each visible crossing needs a lane id")
        lane_id = crossing["id"]
        if lane_id in indexed:
            raise ValueError(f"duplicate visible crossing: {lane_id}")
        edge_id = lane_to_edge.get(lane_id)
        if edge_id is None or edges[edge_id].get("function") != "crossing":
            raise ValueError(f"visible crossing is not a SUMO crossing lane: {lane_id}")
        line = LineString(_coordinates(crossing.get("shape"), lane_id, 2))
        width = float(crossing.get("width", 0))
        if line.length <= 1e-8 or not math.isfinite(width) or width <= 0:
            raise ValueError(f"visible crossing has invalid shape or width: {lane_id}")
        indexed[lane_id] = {"edge_id": edge_id, "line": line, "width": width}
        adjacency[lane_id] = {"start": set(), "end": set()}
    for connection in network.findall("connection"):
        from_edge, to_edge = connection.get("from"), connection.get("to")
        from_lane = f"{from_edge}_{connection.get('fromLane')}"
        to_lane = f"{to_edge}_{connection.get('toLane')}"
        if to_lane in adjacency and edges.get(from_edge) is not None \
                and edges[from_edge].get("function") == "walkingarea":
            adjacency[to_lane]["start"].add(from_edge)
        if from_lane in adjacency and edges.get(to_edge) is not None \
                and edges[to_edge].get("function") == "walkingarea":
            adjacency[from_lane]["end"].add(to_edge)
    return indexed, adjacency


def _polygon_record(polygon: Polygon) -> dict[str, Any]:
    def ring(coords: Any) -> list[list[float]]:
        # GEOS intersections can put distinct vertices less than 0.0001 m
        # apart. Rounding these independently can make a valid sliver cross
        # itself when the JSON is read back.
        return [[float(x), float(z)] for x, z in coords]
    return {"outline": ring(polygon.exterior.coords),
            "holes": [ring(interior.coords) for interior in polygon.interiors]}


def _root_component(area: BaseGeometry, endpoint: Point, limit_m: float) -> Polygon | None:
    """Select the unique connected source face reached at a crossing end.

    Unary union has already merged faces sharing a positive-length edge.
    Faces left separate after make_valid may touch only at a point. If two
    faces are equally near the endpoint, that topology is ambiguous and no
    visual guide is inferred from either one.
    """
    nearby = endpoint.buffer(max(limit_m, 0.01))
    candidates = [(endpoint.distance(part), part) for part in _polygons(area)
                  if endpoint.distance(part) <= limit_m + 1e-8
                  and part.intersection(nearby).area > 1e-6]
    if not candidates:
        return None
    distance = min(item[0] for item in candidates)
    nearest = [part for gap, part in candidates if gap <= distance + 1e-6]
    return nearest[0] if len(nearest) == 1 else None


def _polygonal(surface: BaseGeometry, label: str) -> None:
    if not isinstance(surface, BaseGeometry) or not surface.is_valid \
            or surface.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"{label} must be a valid polygonal Shapely geometry")


def build_pedestrian_connections(network: Element, walking_areas: list[dict[str, Any]],
                                 visible_crossings: list[dict[str, Any]],
                                 roadbed: BaseGeometry, walkbed: BaseGeometry,
                                 buildings: BaseGeometry) -> dict[str, Any]:
    """Return shared polygons and pre-cut guide dashes on the final roadbed."""
    _polygonal(roadbed, "roadbed")
    _polygonal(walkbed, "walkbed")
    _polygonal(buildings, "buildings")
    source = _walk_geometry(walking_areas)
    indexed, adjacency = _crossing_index(network, visible_crossings)
    connected: dict[str, set[str]] = defaultdict(set)
    roots: dict[str, list[Polygon]] = defaultdict(list)
    for crossing_id, ends in adjacency.items():
        for end_name, edge_ids in ends.items():
            coordinates = list(indexed[crossing_id]["line"].coords)
            endpoint = Point(coordinates[0 if end_name == "start" else -1])
            for edge_id in edge_ids:
                connected[edge_id].add(crossing_id)
                if edge_id in source:
                    root = _root_component(source[edge_id], endpoint, ENDPOINT_LIMIT_M)
                    if root is not None:
                        roots[edge_id].append(root)
    crossing_paint = unary_union([
        item["line"].buffer(item["width"] / 2 + GUIDE_WIDTH_M,
                            cap_style=2, join_style=2)
        for item in indexed.values()
    ]).buffer(GUIDE_WIDTH_M / 2 + 0.005) if indexed else Polygon()
    # Insetting the displayed roadbed keeps the entire 12 cm ribbon on asphalt.
    paintable = roadbed.buffer(-(GUIDE_WIDTH_M / 2 + 0.005))
    if not walkbed.is_empty:
        paintable = paintable.difference(walkbed.buffer(GUIDE_WIDTH_M / 2 + 0.005))
    if not buildings.is_empty:
        paintable = paintable.difference(buildings.buffer(GUIDE_WIDTH_M / 2 + 0.005))
    if not crossing_paint.is_empty:
        paintable = paintable.difference(crossing_paint)
    records = []
    markings = []
    for edge_id in sorted(connected):
        if edge_id not in source:
            continue
        reachable = roots[edge_id]
        if not reachable:
            continue
        connected_source = unary_union(reachable)
        shared = connected_source.intersection(roadbed).difference(walkbed).difference(buildings)
        parts = [part for part in _polygons(make_valid(shared)) if part.area > 1e-8]
        if not parts:
            continue
        records.append({"id": edge_id, "crossing_ids": sorted(connected[edge_id]),
                        "polygons": [_polygon_record(part) for part in parts],
                        "source_kind": SOURCE_KIND, "exclusive_pedestrian_right_of_way": False})
        # Only the source walking-area boundary is marked. Clipping must not
        # turn a roadbed or building cut into an invented pedestrian boundary.
        original_boundary = connected_source.boundary.intersection(paintable)
        for line in _lines(original_boundary):
            position = 0.0
            while position < line.length - 1e-8:
                end = min(position + DASH_LENGTH_M, line.length)
                if end - position >= 0.12:
                    segment = substring(line, position, end)
                    if isinstance(segment, LineString) and segment.length >= 0.12:
                        shape = [[round(x, 4), round(z, 4)] for x, z in segment.coords]
                        if len(set(map(tuple, shape))) >= 2:
                            markings.append({"kind": "pedestrian_connection_guide",
                                             "color": "white", "pattern": "dashed",
                                             "width_m": GUIDE_WIDTH_M, "shape": shape,
                                             "source_walking_area_id": edge_id,
                                             "source_crossing_ids": sorted(connected[edge_id]),
                                             "source_kind": SOURCE_KIND})
                position += DASH_LENGTH_M + DASH_GAP_M
    return {"shared_areas": records, "markings": markings,
            "profile": {"source_kind": SOURCE_KIND, "surveyed_marking": False,
                        "exclusive_pedestrian_right_of_way": False,
                        "guide_dash_m": DASH_LENGTH_M, "guide_gap_m": DASH_GAP_M,
                        "guide_width_m": GUIDE_WIDTH_M}}


def _connection_ports(network: Element, visible_crossings: list[dict[str, Any]],
                      lanes: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    edges = {edge.get("id"): edge for edge in network.findall("edge")}
    crossing_index, adjacency = _crossing_index(network, visible_crossings)
    lane_index = {}
    for lane in lanes:
        lane_id = lane.get("id")
        if not isinstance(lane_id, str) or not lane_id or lane_id in lane_index:
            raise ValueError(f"missing or duplicate scene lane id: {lane_id}")
        lane_index[lane_id] = lane
    ports: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: {"crossing": [], "normal": []})
    for crossing_id, ends in adjacency.items():
        item = crossing_index[crossing_id]
        coordinates = list(item["line"].coords)
        for end_name, edge_ids in ends.items():
            point = coordinates[0 if end_name == "start" else -1]
            for walking_id in edge_ids:
                ports[walking_id]["crossing"].append({
                    "port_id": f"{crossing_id}@{end_name}", "lane_id": crossing_id,
                    "end": end_name, "point": point, "width": item["width"],
                    "kind": "crossing"})
    for connection in network.findall("connection"):
        from_edge, to_edge = connection.get("from"), connection.get("to")
        if from_edge not in edges or to_edge not in edges:
            continue
        if edges[from_edge].get("function") == "walkingarea" \
                and edges[to_edge].get("function", "normal") == "normal":
            walking_id, normal_edge, lane_number, end_name = (
                from_edge, to_edge, connection.get("toLane"), "start")
        elif edges[to_edge].get("function") == "walkingarea" \
                and edges[from_edge].get("function", "normal") == "normal":
            walking_id, normal_edge, lane_number, end_name = (
                to_edge, from_edge, connection.get("fromLane"), "end")
        else:
            continue
        lane_id = f"{normal_edge}_{lane_number}"
        lane = lane_index.get(lane_id)
        if lane is None:
            raise ValueError(f"SUMO walking area {walking_id} lacks connected scene lane {lane_id}")
        if lane.get("kind") != "walk":
            continue
        line = LineString(_coordinates(lane.get("shape"), lane_id, 2))
        width = float(lane.get("width", 0))
        if line.length <= 1e-8 or not math.isfinite(width) or width <= 0:
            raise ValueError(f"connected walk lane has invalid geometry: {lane_id}")
        point = list(line.coords)[0 if end_name == "start" else -1]
        ports[walking_id]["normal"].append({
            "port_id": f"{lane_id}@{end_name}", "lane_id": lane_id,
            "end": end_name, "point": point, "width": width, "kind": "normal"})
    for groups in ports.values():
        for kind in ("crossing", "normal"):
            groups[kind] = list({port["port_id"]: port for port in groups[kind]}.values())
    return ports


def _destination_port(source: dict[str, Any],
                      groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    normal = groups["normal"]
    choices = normal if normal else [port for port in groups["crossing"]
                                     if port["port_id"] != source["port_id"]]
    return min(choices, key=lambda port: (math.dist(source["point"], port["point"]),
                                           port["port_id"])) if choices else None


def audit_pedestrian_walkbed_landings(network: Element, lanes: list[dict[str, Any]],
                                     walkbed: BaseGeometry,
                                     connection_paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A source route and paved guide do not prove a dedicated walkbed landing."""
    _polygonal(walkbed, "walkbed")
    source_lanes = {lane.get("id"): lane for edge in network.findall("edge")
                    if edge.get("function", "normal") == "normal" for lane in edge.findall("lane")}
    scene_lanes = {lane["id"]: lane for lane in lanes}
    paved_walkbed = set_precision(walkbed, 0).buffer(PAVING_SEAM_TOLERANCE_M)
    failures = []
    for path in connection_paths:
        if path["destination_kind"] != "normal":
            continue
        lane_id = path["destination_lane_id"]
        if lane_id not in source_lanes or lane_id not in scene_lanes:
            raise ValueError(f"Pedestrian landing lacks its source and scene lane: {lane_id}")
        source_lane, lane = source_lanes[lane_id], scene_lanes[lane_id]
        allowed = set(source_lane.get("allow", "").split())
        disallowed = set(source_lane.get("disallow", "").split())
        permission = (not allowed or "pedestrian" in allowed) and "pedestrian" not in disallowed
        # An unrestricted or mixed vehicle lane remains a source route; it does
        # not supply an exclusive sidewalk merely by allowing pedestrians.
        exclusive = permission and allowed == {"pedestrian"} and lane["kind"] == "walk"
        coordinates = _coordinates(lane["shape"], lane_id, 2)
        end = path["destination_endpoint"]
        if end not in ("start", "end"):
            raise ValueError(f"Pedestrian landing has an invalid endpoint: {lane_id}")
        point, adjacent = (coordinates[0], coordinates[1]) if end == "start" \
            else (coordinates[-1], coordinates[-2])
        dx, dz = point[0] - adjacent[0], point[1] - adjacent[1]
        length = math.hypot(dx, dz)
        width = float(path["width_m"])
        if length <= 1e-8 or not math.isfinite(width) or width <= 0:
            raise ValueError(f"Pedestrian landing has invalid geometry: {lane_id}")
        landing = LineString([(point[0] - dz / length * width / 2,
                               point[1] + dx / length * width / 2),
                              (point[0] + dz / length * width / 2,
                               point[1] - dx / length * width / 2)])
        covered = paved_walkbed.covers(landing)
        if exclusive and covered:
            continue
        failures.append({"walking_area_id": path["walking_area_id"],
                         "source_crossing_id": path["source_crossing_id"],
                         "source_crossing_endpoint": path["source_crossing_endpoint"],
                         "normal_lane_id": lane_id, "normal_lane_kind": lane["kind"],
                         "source_pedestrian_permission": permission,
                         "exclusive_source_pedestrian_lane": exclusive,
                         "displayed_walkbed_covers_landing": covered,
                         "normal_point_to_walkbed_m": (Point(point).distance(walkbed)
                                                        if not walkbed.is_empty else None),
                         "uncovered_landing_length_m": landing.difference(paved_walkbed).length,
                         "reason": ("source_route_has_no_exclusive_pedestrian_lane" if not exclusive
                                    else "normal_port_lacks_displayed_walkbed")})
    return failures


def _path_corridor(source: dict[str, Any], destination: dict[str, Any]) -> tuple[LineString, Polygon, float]:
    line = LineString([source["point"], destination["point"]])
    width = min(1.5, source["width"], destination["width"])
    return line, line.buffer(width / 2, cap_style=2, join_style=2), width


def _path_markings(line: LineString, width: float, paintable: BaseGeometry,
                   walking_id: str, crossing_ids: list[str],
                   normal_lane_id: str | None) -> list[dict[str, Any]]:
    coordinates = list(line.coords)
    dx = (coordinates[-1][0] - coordinates[0][0]) / line.length
    dz = (coordinates[-1][1] - coordinates[0][1]) / line.length
    markings = []
    for side in (-1, 1):
        offset = side * width / 2
        edge = LineString([(x - dz * offset, z + dx * offset) for x, z in coordinates])
        for part in _lines(edge.intersection(paintable)):
            position = 0.0
            while position < part.length - 1e-8:
                end = min(position + DASH_LENGTH_M, part.length)
                if end - position >= 0.12:
                    segment = substring(part, position, end)
                    if isinstance(segment, LineString) and segment.length >= 0.12:
                        shape = [[round(x, 4), round(z, 4)] for x, z in segment.coords]
                        if len(set(map(tuple, shape))) >= 2:
                            markings.append({"kind": "pedestrian_connection_guide",
                                             "color": "white", "pattern": "dashed",
                                             "width_m": GUIDE_WIDTH_M, "shape": shape,
                                             "source_walking_area_id": walking_id,
                                             "source_crossing_ids": crossing_ids,
                                             "source_normal_lane_id": normal_lane_id,
                                             "source_kind": PORT_PATH_SOURCE_KIND})
                position += DASH_LENGTH_M + DASH_GAP_M
    return markings


def plan_pedestrian_connection_paths(network: Element,
                                     walking_areas: list[dict[str, Any]],
                                     visible_crossings: list[dict[str, Any]],
                                     lanes: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose source-topology port corridors before final paving is built."""
    source_areas = _walk_geometry(walking_areas)
    ports = _connection_ports(network, visible_crossings, lanes)
    candidates = []
    omissions = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for walking_id in sorted(ports):
        groups = ports[walking_id]
        for crossing_port in sorted(groups["crossing"], key=lambda port: port["port_id"]):
            destination = _destination_port(crossing_port, groups)
            source_meta = {"walking_area_id": walking_id,
                           "source_crossing_id": crossing_port["lane_id"],
                           "source_crossing_endpoint": crossing_port["end"]}
            if walking_id not in source_areas:
                omissions.append({**source_meta, "reason": "missing_walking_area_geometry"})
                continue
            if destination is None:
                omissions.append({**source_meta, "reason": "no_connected_destination_port"})
                continue
            pair = (walking_id, *sorted((crossing_port["port_id"], destination["port_id"])))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            line, corridor, width = _path_corridor(crossing_port, destination)
            if line.length <= 1e-8:
                omissions.append({**source_meta, "reason": "coincident_connection_ports"})
                continue
            crossing_ids = sorted({crossing_port["lane_id"]} | (
                {destination["lane_id"]} if destination["kind"] == "crossing" else set()))
            path = {**source_meta, "source_port_id": crossing_port["port_id"],
                    "destination_port_id": destination["port_id"],
                    "destination_lane_id": destination["lane_id"],
                    "destination_kind": destination["kind"],
                    "destination_endpoint": destination["end"],
                    "source_crossing_ids": crossing_ids,
                    "source_normal_lane_id": (destination["lane_id"]
                                              if destination["kind"] == "normal" else None),
                    "shape": [[float(x), float(z)] for x, z in line.coords],
                    "width_m": width, "length_m": line.length,
                    "corridor": _polygon_record(corridor),
                    "source_kind": PORT_PATH_SOURCE_KIND,
                    "surveyed_marking": False,
                    "exclusive_pedestrian_right_of_way": False}
            candidates.append(path)
    return {"candidates": candidates, "omissions": omissions,
            "profile": {"source_kind": PORT_PATH_SOURCE_KIND,
                        "surveyed_marking": False,
                        "exclusive_pedestrian_right_of_way": False,
                        "path_width_cap_m": 1.5}}


def build_pedestrian_connection_paths(network: Element,
                                      walking_areas: list[dict[str, Any]],
                                      visible_crossings: list[dict[str, Any]],
                                      lanes: list[dict[str, Any]],
                                      roadbed: BaseGeometry, walkbed: BaseGeometry,
                                      buildings: BaseGeometry) -> dict[str, Any]:
    """Qualify planned port corridors against final paving and building geometry."""
    _polygonal(roadbed, "roadbed")
    _polygonal(walkbed, "walkbed")
    _polygonal(buildings, "buildings")
    plan = plan_pedestrian_connection_paths(network, walking_areas, visible_crossings, lanes)
    paved = unary_union([set_precision(roadbed, 0), set_precision(walkbed, 0)])
    allowed_paving = paved.buffer(PAVING_SEAM_TOLERANCE_M)
    prepare(allowed_paving)
    paintable = roadbed.buffer(-(GUIDE_WIDTH_M / 2 + 0.005))
    crossing_mask = unary_union([
        LineString(_coordinates(crossing["shape"], crossing["id"], 2)).buffer(
            float(crossing["width"]) / 2 + GUIDE_WIDTH_M, cap_style=2, join_style=2)
        for crossing in visible_crossings]) if visible_crossings else Polygon()
    if not crossing_mask.is_empty:
        paintable = paintable.difference(crossing_mask.buffer(GUIDE_WIDTH_M / 2 + 0.005))
    if not buildings.is_empty:
        paintable = paintable.difference(buildings.buffer(GUIDE_WIDTH_M / 2 + 0.005))
    paths = []
    markings = []
    omissions = list(plan["omissions"])
    for candidate in plan["candidates"]:
        line = LineString(candidate["shape"])
        part = candidate["corridor"]
        corridor = Polygon(part["outline"], part["holes"])
        if not allowed_paving.covers(corridor):
            omissions.append({**candidate, "reason": "corridor_leaves_final_paving",
                              "uncovered_area_m2": corridor.difference(allowed_paving).area})
            continue
        if not buildings.is_empty and corridor.intersection(buildings).area > 1e-8:
            omissions.append({**candidate, "reason": "corridor_intersects_building",
                              "building_overlap_area_m2": corridor.intersection(buildings).area})
            continue
        paths.append(candidate)
        markings.extend(_path_markings(line, candidate["width_m"], paintable,
                                       candidate["walking_area_id"],
                                       candidate["source_crossing_ids"],
                                       candidate["source_normal_lane_id"]))
    return {"connection_paths": paths, "markings": markings, "omissions": omissions,
            "profile": {"source_kind": PORT_PATH_SOURCE_KIND,
                        "surveyed_marking": False,
                        "exclusive_pedestrian_right_of_way": False,
                        "paving_seam_tolerance_m": PAVING_SEAM_TOLERANCE_M,
                        "guide_dash_m": DASH_LENGTH_M, "guide_gap_m": DASH_GAP_M,
                        "guide_width_m": GUIDE_WIDTH_M}}


def audit_crossing_endpoints(network: Element, walking_areas: list[dict[str, Any]],
                             visible_crossings: list[dict[str, Any]],
                             lanes: list[dict[str, Any]], roadbed: BaseGeometry,
                             walkbed: BaseGeometry, buildings: BaseGeometry,
                             shared_areas: list[dict[str, Any]],
                             connection_paths: list[dict[str, Any]],
                             *, limit_m: float = ENDPOINT_LIMIT_M) -> list[dict[str, Any]]:
    """Find crossing ends without a connected paved landing or complete port path."""
    _polygonal(roadbed, "roadbed")
    _polygonal(walkbed, "walkbed")
    _polygonal(buildings, "buildings")
    if not math.isfinite(limit_m) or limit_m < 0:
        raise ValueError("endpoint limit must be a finite nonnegative distance")
    source = _walk_geometry(walking_areas)
    indexed, adjacency = _crossing_index(network, visible_crossings)
    ports = _connection_ports(network, visible_crossings, lanes)
    shared: dict[str, BaseGeometry] = {}
    for record in shared_areas:
        edge_id = record.get("id")
        if edge_id not in source or edge_id in shared:
            raise ValueError(f"invalid or duplicate shared walking area: {edge_id}")
        polygons = [Polygon(part["outline"], part.get("holes", []))
                    for part in record.get("polygons", [])]
        if any(not polygon.is_valid for polygon in polygons):
            raise ValueError(f"invalid shared walking area polygon: {edge_id}")
        shared[edge_id] = unary_union(polygons) if polygons else Polygon()
    paved = unary_union([set_precision(roadbed, 0), set_precision(walkbed, 0)]).buffer(PAVING_SEAM_TOLERANCE_M)
    prepare(paved)
    valid_paths: set[tuple[str, str, str]] = set()
    port_graph: dict[str, set[str]] = defaultdict(set)
    for crossing_id in indexed:
        start, end = f"{crossing_id}@start", f"{crossing_id}@end"
        port_graph[start].add(end)
        port_graph[end].add(start)
    for record in connection_paths:
        walking_id = record.get("walking_area_id")
        groups = ports.get(walking_id)
        if groups is None:
            raise ValueError(f"connection path has no SUMO walking area: {walking_id}")
        crossing_port = next((port for port in groups["crossing"]
                              if port["port_id"] == record.get("source_port_id")), None)
        if crossing_port is None:
            raise ValueError(f"connection path has no direct crossing port: {record.get('source_port_id')}")
        destination = _destination_port(crossing_port, groups)
        if destination is None or destination["port_id"] != record.get("destination_port_id"):
            raise ValueError(f"connection path has an incorrect destination port: {record.get('destination_port_id')}")
        line, corridor, width = _path_corridor(crossing_port, destination)
        expected_crossing_ids = sorted({crossing_port["lane_id"]} | (
            {destination["lane_id"]} if destination["kind"] == "crossing" else set()))
        if (record.get("source_kind") != PORT_PATH_SOURCE_KIND
                or record.get("surveyed_marking") is not False
                or record.get("exclusive_pedestrian_right_of_way") is not False
                or record.get("source_crossing_id") != crossing_port["lane_id"]
                or record.get("source_crossing_endpoint") != crossing_port["end"]
                or record.get("destination_lane_id") != destination["lane_id"]
                or record.get("destination_kind") != destination["kind"]
                or record.get("destination_endpoint") != destination["end"]
                or record.get("source_crossing_ids") != expected_crossing_ids
                or record.get("source_normal_lane_id") != (
                    destination["lane_id"] if destination["kind"] == "normal" else None)):
            raise ValueError(f"connection path provenance differs from SUMO ports: {record.get('source_port_id')}")
        supplied = LineString(_coordinates(record.get("shape"), "connection path", 2))
        if supplied.hausdorff_distance(line) > 1e-4 or math.dist(supplied.coords[0], line.coords[0]) > 1e-4 \
                or math.dist(supplied.coords[-1], line.coords[-1]) > 1e-4 \
                or abs(float(record.get("width_m", 0)) - width) > 1e-8:
            raise ValueError(f"connection path differs from SUMO ports: {record.get('source_port_id')}")
        footprint_record = record.get("corridor")
        if not isinstance(footprint_record, dict):
            raise ValueError(f"connection path lacks its corridor polygon: {record.get('source_port_id')}")
        supplied_corridor = Polygon(footprint_record.get("outline", []),
                                    footprint_record.get("holes", []))
        if not supplied_corridor.is_valid or supplied_corridor.symmetric_difference(corridor).area > 1e-8:
            raise ValueError(f"connection path corridor differs from SUMO ports: {record.get('source_port_id')}")
        if not paved.covers(corridor) or (not buildings.is_empty
                                          and corridor.intersection(buildings).area > 1e-8):
            raise ValueError(f"connection path leaves final paving or enters a building: {record.get('source_port_id')}")
        port_graph[crossing_port["port_id"]].add(destination["port_id"])
        port_graph[destination["port_id"]].add(crossing_port["port_id"])
        valid_paths.add((walking_id, crossing_port["lane_id"], crossing_port["end"]))
        if destination["kind"] == "crossing":
            valid_paths.add((walking_id, destination["lane_id"], destination["end"]))
    connected_to_normal = {port["port_id"] for groups in ports.values()
                           for port in groups["normal"]}
    queue = deque(connected_to_normal)
    while queue:
        for neighbour in port_graph[queue.popleft()]:
            if neighbour not in connected_to_normal:
                connected_to_normal.add(neighbour)
                queue.append(neighbour)
    failures = []
    for crossing_id, item in indexed.items():
        coordinates = list(item["line"].coords)
        for end_name, coordinate in (("start", coordinates[0]), ("end", coordinates[-1])):
            point = Point(coordinate)
            connected_ids = sorted(adjacency[crossing_id][end_name])
            if any((edge_id, crossing_id, end_name) in valid_paths
                   and f"{crossing_id}@{end_name}" in connected_to_normal
                   for edge_id in connected_ids):
                continue
            connected_parts = {edge_id: root for edge_id in connected_ids
                               if edge_id in source
                               for root in [_root_component(source[edge_id], point, limit_m)]
                               if root is not None}
            candidates = [shared[edge_id].intersection(connected_parts[edge_id])
                          for edge_id in connected_ids
                          if edge_id in shared and edge_id in connected_parts
                          and not connected_parts[edge_id].is_empty]
            raised_source = unary_union(list(connected_parts.values())) if connected_parts else Polygon()
            raised = walkbed.intersection(raised_source) if not raised_source.is_empty else Polygon()
            raised_distance = point.distance(raised) if not raised.is_empty else math.inf
            shared_distance = min((point.distance(area) for area in candidates), default=math.inf)
            failures.append({"crossing_id": crossing_id, "endpoint": end_name,
                             "connected_walking_area_ids": connected_ids,
                             "raised_walkbed_distance_m": (round(raised_distance, 4)
                                                          if math.isfinite(raised_distance) else None),
                             "connected_shared_distance_m": (round(shared_distance, 4)
                                                             if math.isfinite(shared_distance) else None),
                             "limit_m": limit_m,
                             "reason": ("crossing_endpoint_lacks_direct_walkingarea_connection"
                                        if not connected_ids else
                                        "crossing_endpoint_has_no_complete_pedestrian_port_path")})
    return failures
