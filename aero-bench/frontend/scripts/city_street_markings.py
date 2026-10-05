"""Derive visual urban road markings from SUMO lane and connection geometry.

The result is a design supplement for a 3D presentation. SUMO supplies the
travel topology and lane geometry; it does not claim surveyed marking paint.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union
from shapely.validation import make_valid


SOURCE_KIND = "derived-urban-design-profile"
GUIDE_SOURCE_KIND = "sumo-lane-topology-not-surveyed-marking"
WHITE = "white"
YELLOW = "yellow"

_MARKING_WIDTH_M = {
    "lane_edge": 0.12,
    "lane_divider": 0.12,
    "centerline": 0.12,
    "stop_line": 0.22,
}
_DASH_LENGTH_M = 3.0
_DASH_GAP_M = 6.0
_APPROACH_SOLID_M = 12.0
_JUNCTION_CLEARANCE_M = 0.08
_EDGE_PAINT_CLEARANCE_M = 0.02


def _finite_number(value: Any, label: str, *, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{label} must be a finite positive number" if positive else
                         f"{label} must be a finite number")
    return number


def _line(points: Any, label: str) -> LineString:
    if not isinstance(points, (list, tuple)):
        raise ValueError(f"{label} shape must be a coordinate list")
    normalized: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError(f"{label} contains an invalid coordinate")
        coordinate = (_finite_number(point[0], f"{label} x"),
                      _finite_number(point[1], f"{label} z"))
        if not normalized or math.dist(coordinate, normalized[-1]) > 1e-7:
            normalized.append(coordinate)
    if len(normalized) < 2:
        raise ValueError(f"{label} needs at least two distinct coordinates")
    geometry = LineString(normalized)
    if geometry.length <= 1e-6 or not geometry.is_valid:
        raise ValueError(f"{label} is a degenerate line")
    return geometry


def _rounded_shape(geometry: LineString) -> list[list[float]]:
    points: list[list[float]] = []
    for x, z in geometry.coords:
        point = [round(float(x), 3), round(float(z), 3)]
        if not points or point != points[-1]:
            points.append(point)
    return points


def _rounded_ring(polygon: Polygon) -> list[list[float]]:
    points = [[round(float(x), 3), round(float(z), 3)] for x, z in polygon.exterior.coords[:-1]]
    if points and points[0] != points[-1]:
        points.append(points[0])
    return points


def _lines(geometry: Any) -> Iterable[LineString]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
        return
    if geometry.geom_type in ("MultiLineString", "GeometryCollection"):
        for part in geometry.geoms:
            yield from _lines(part)


def _polygons(geometry: Any) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
        return
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geometry.geoms:
            yield from _polygons(part)


def _junction_geometry(junctions: list[dict[str, Any]]) -> tuple[Any, int]:
    polygons: list[Polygon] = []
    seen: set[str] = set()
    repaired = 0
    for junction in junctions:
        if not isinstance(junction, dict) or not isinstance(junction.get("id"), str):
            raise ValueError("each junction needs a string id")
        if junction["id"] in seen:
            raise ValueError(f"duplicate junction id: {junction['id']}")
        seen.add(junction["id"])
        points = junction.get("shape")
        if not isinstance(points, (list, tuple)) or len(points) < 3:
            raise ValueError(f"junction {junction['id']} needs a polygon shape")
        coordinates = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                raise ValueError(f"junction {junction['id']} contains an invalid coordinate")
            coordinates.append((_finite_number(point[0], "junction x"),
                                _finite_number(point[1], "junction z")))
        polygon = Polygon(coordinates)
        if polygon.area <= 1e-4:
            raise ValueError(f"junction {junction['id']} has no usable polygon area")
        if not polygon.is_valid:
            polygon = make_valid(polygon)
            repaired += 1
        valid_parts = [part for part in _polygons(polygon) if part.area > 1e-4 and part.is_valid]
        if not valid_parts:
            raise ValueError(f"junction {junction['id']} could not be repaired into a polygon")
        polygons.extend(valid_parts)
    return (unary_union(polygons) if polygons else GeometryCollection()), repaired


def _lane_and_edge_index(edges: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    edge_index: dict[str, dict[str, Any]] = {}
    lane_index: dict[str, dict[str, Any]] = {}
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
            raise ValueError("each edge needs a string id")
        edge_id = edge["id"]
        if edge_id in edge_index:
            raise ValueError(f"duplicate edge id: {edge_id}")
        if not isinstance(edge.get("from"), str) or not isinstance(edge.get("to"), str):
            raise ValueError(f"edge {edge_id} needs from/to node ids")
        if not isinstance(edge.get("lanes"), (list, tuple)):
            raise ValueError(f"edge {edge_id} needs a lane list")
        indexed_lanes: set[int] = set()
        lanes = []
        for lane in edge["lanes"]:
            if not isinstance(lane, dict) or not isinstance(lane.get("id"), str):
                raise ValueError(f"edge {edge_id} contains a lane without an id")
            lane_id = lane["id"]
            if lane_id in lane_index:
                raise ValueError(f"duplicate lane id: {lane_id}")
            try:
                index = int(lane["index"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"lane {lane_id} needs an integer index") from error
            if index < 0 or index in indexed_lanes:
                raise ValueError(f"edge {edge_id} has a negative or duplicate lane index")
            indexed_lanes.add(index)
            width = _finite_number(lane.get("width"), f"lane {lane_id} width", positive=True)
            lane_line = _line(lane.get("shape"), f"lane {lane_id}")
            lane_record = {**lane, "_line": lane_line, "_width": width, "_index": index,
                           "_edge_id": edge_id}
            lane_index[lane_id] = lane_record
            lanes.append(lane_record)
        lanes.sort(key=lambda item: item["_index"])
        edge_record = {**edge, "_lanes": lanes}
        edge_index[edge_id] = edge_record
    return edge_index, lane_index


def _same_direction_midline(first: dict[str, Any], second: dict[str, Any]) -> LineString | None:
    """Place a separator midway between adjacent lane-width envelopes."""
    first_line, second_line = first["_line"], second["_line"]
    if not 0.6 <= first_line.length / second_line.length <= 1.67:
        return None
    fractions = {0.0, 1.0}
    for line in (first_line, second_line):
        total = 0.0
        fractions.add(0.0)
        coordinates = list(line.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            total += math.dist(start, end)
            fractions.add(total / line.length)
    sample_count = max(2, math.ceil(max(first_line.length, second_line.length) / 3.0))
    fractions.update(index / sample_count for index in range(sample_count + 1))
    coordinates = []
    for fraction in sorted(fractions):
        a = first_line.interpolate(fraction, normalized=True)
        b = second_line.interpolate(fraction, normalized=True)
        dx, dz = b.x - a.x, b.y - a.y
        distance = math.hypot(dx, dz)
        if distance <= 1e-7:
            return None
        fraction_from_first = 0.5 + (first["_width"] - second["_width"]) / (4 * distance)
        fraction_from_first = min(1.0, max(0.0, fraction_from_first))
        point = (a.x + dx * fraction_from_first, a.y + dz * fraction_from_first)
        if not coordinates or math.dist(point, coordinates[-1]) > 1e-5:
            coordinates.append(point)
    if len(coordinates) < 2:
        raise ValueError("adjacent lane centre lines collapsed to one point")
    return LineString(coordinates)


def _reverse_lane_boundary_gap(first: dict[str, Any], second: dict[str, Any]) -> float | None:
    """Measure whether opposing lane envelopes meet along an entire segment."""
    a, b = first["_line"], second["_line"]
    if not 0.65 <= a.length / b.length <= 1.55:
        return None
    fractions = {index / max(8, math.ceil(max(a.length, b.length) / 3.0))
                 for index in range(max(8, math.ceil(max(a.length, b.length) / 3.0)) + 1)}
    for line in (a, b):
        distance = 0.0
        coordinates = list(line.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            distance += math.dist(start, end)
            fractions.add(distance / line.length)
    gaps = []
    for fraction in fractions:
        first_point = a.interpolate(fraction, normalized=True)
        second_point = b.interpolate(1.0 - fraction, normalized=True)
        center_distance = first_point.distance(second_point)
        gaps.append(center_distance - (first["_width"] + second["_width"]) / 2)
    # Matching only at shared endpoints is insufficient. Every sampled width
    # envelope must meet within a small tolerance along the road segment.
    if max(abs(gap) for gap in gaps) > 0.35:
        return None
    if a.hausdorff_distance(LineString(list(reversed(b.coords)))) > max(8.0, first["_width"] + second["_width"]):
        return None
    return max(abs(gap) for gap in gaps)


def _paired_reverse_edges(edge_index: dict[str, dict[str, Any]]) -> set[frozenset[str]]:
    pairs: set[frozenset[str]] = set()
    by_nodes: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edge_index.values():
        by_nodes.setdefault((edge["from"], edge["to"]), []).append(edge)
    for (source, target), candidates in by_nodes.items():
        for edge in candidates:
            edge_id = edge["id"]
            lanes = [lane for lane in edge["_lanes"] if lane.get("kind") == "motor"]
            if not lanes:
                continue
            for other in by_nodes.get((target, source), []):
                other_id = other["id"]
                if other_id <= edge_id:
                    continue
                if edge.get("highway") and other.get("highway") and edge["highway"] != other["highway"]:
                    continue
                other_lanes = [lane for lane in other["_lanes"] if lane.get("kind") == "motor"]
                if not other_lanes:
                    continue
                gaps = [_reverse_lane_boundary_gap(first, second)
                        for first in lanes for second in other_lanes]
                if any(gap is not None for gap in gaps):
                    pairs.add(frozenset((edge_id, other_id)))
    return pairs


def _trim_at_junctions(line: LineString, junction_mask: Any) -> list[LineString]:
    if junction_mask.is_empty:
        return [line]
    clipped = line.difference(junction_mask)
    return [part for part in _lines(clipped) if part.length >= 0.35]


def _offset_lines(line: LineString, distance: float) -> list[LineString]:
    return [part for part in _lines(line.offset_curve(distance, join_style="mitre", mitre_limit=2.0))
            if part.length >= 0.35]


def _dash(line: LineString, dash_m: float = _DASH_LENGTH_M,
          gap_m: float = _DASH_GAP_M) -> list[LineString]:
    pieces = []
    period = dash_m + gap_m
    start = 0.0
    while start < line.length - 1e-6:
        end = min(start + dash_m, line.length)
        if end - start >= 0.35:
            piece = substring(line, start, end)
            if isinstance(piece, LineString) and piece.length >= 0.35:
                pieces.append(piece)
        start += period
    return pieces


def _marking(kind: str, color: str, pattern: str, line: LineString,
             lane_ids: Iterable[str], *, source_kind: str = SOURCE_KIND,
             semantic_group_id: str | None = None) -> dict[str, Any]:
    if kind not in _MARKING_WIDTH_M:
        raise ValueError(f"unsupported marking kind: {kind}")
    shape = _rounded_shape(line)
    if len(shape) < 2:
        raise ValueError("marking collapsed after coordinate rounding")
    result = {"kind": kind, "color": color, "pattern": pattern,
              "width_m": _MARKING_WIDTH_M[kind], "shape": shape,
              "source_lane_ids": sorted(set(lane_ids)), "source_kind": source_kind}
    if semantic_group_id is not None:
        result["semantic_group_id"] = semantic_group_id
    return result


def _junctions_for_guide(guide: dict[str, Any], edge_index: dict[str, dict[str, Any]]) -> set[str]:
    edge_ids = guide.get("edge_ids")
    if not isinstance(edge_ids, (list, tuple)) or len(edge_ids) != 2:
        raise ValueError(f"direction guide {guide.get('id')} needs exactly two edge ids")
    normalized = {str(edge_id) for edge_id in edge_ids}
    missing = normalized.difference(edge_index)
    if missing:
        raise ValueError(f"direction guide references unknown edges: {sorted(missing)}")
    return normalized


def _arrow_local_polygon(movement: str) -> Polygon:
    if movement == "s":
        path = [(0.0, -1.02), (0.0, 0.5)]
    elif movement == "l":
        path = [(0.0, -1.02), (0.0, -0.03), (0.04, 0.26),
                (0.2, 0.5), (0.49, 0.72)]
    elif movement == "r":
        path = [(0.0, -1.02), (0.0, -0.03), (-0.04, 0.26),
                (-0.2, 0.5), (-0.49, 0.72)]
    else:
        raise ValueError(f"unsupported movement arrow: {movement}")
    shaft = LineString(path).buffer(0.065, cap_style="flat", join_style="round")
    tip_x, tip_y = path[-1]
    previous_x, previous_y = path[-2]
    direction_length = math.hypot(tip_x - previous_x, tip_y - previous_y)
    ux, uy = (tip_x - previous_x) / direction_length, (tip_y - previous_y) / direction_length
    head_length, head_half_width = 0.42, 0.25
    base_x, base_y = tip_x - ux * head_length, tip_y - uy * head_length
    normal_x, normal_y = -uy, ux
    head = Polygon([(tip_x, tip_y),
                    (base_x + normal_x * head_half_width, base_y + normal_y * head_half_width),
                    (base_x - normal_x * head_half_width, base_y - normal_y * head_half_width)])
    result = unary_union([shaft, head])
    polygons = sorted(_polygons(result), key=lambda polygon: polygon.area, reverse=True)
    if not polygons or not polygons[0].is_valid:
        raise ValueError("could not construct a valid lane arrow")
    return polygons[0]


def _absolute_arrow(local: Polygon, x: float, z: float,
                    forward: tuple[float, float], lateral_offset: float) -> Polygon:
    fx, fz = forward
    # In east/south coordinates (-fz, fx) is geographic right. SUMO lane 0
    # occupies the driver's right side, so positive lateral is its outer side.
    right = (-fz, fx)
    left = (fz, -fx)
    shifted_x = x + right[0] * lateral_offset
    shifted_z = z + right[1] * lateral_offset
    return affinity.affine_transform(local, [left[0], fx, left[1], fz, shifted_x, shifted_z])


def _tangent(line: LineString, distance: float) -> tuple[float, float]:
    step = min(0.5, max(0.12, line.length / 30.0))
    before = line.interpolate(max(0.0, distance - step))
    after = line.interpolate(min(line.length, distance + step))
    dx, dz = after.x - before.x, after.y - before.y
    magnitude = math.hypot(dx, dz)
    if magnitude <= 1e-8:
        raise ValueError("could not determine lane direction at marking position")
    return dx / magnitude, dz / magnitude


def _stop_line(lane: dict[str, Any], junction_stop_mask: Any) -> list[LineString]:
    centerline: LineString = lane["_line"]
    if centerline.length < 1.8:
        return []
    station = centerline.length - 1.0
    # Move the stop line toward the approach when the external lane endpoint
    # lies inside a node polygon. Check the full stripe, including its ends.
    candidate = None
    while station > 0.5:
        center = centerline.interpolate(station)
        fx, fz = _tangent(centerline, station)
        nx, nz = -fz, fx
        half = max(0.1, lane["_width"] / 2 - 0.08)
        candidate = LineString([(center.x - nx * half, center.y - nz * half),
                                (center.x + nx * half, center.y + nz * half)])
        if junction_stop_mask.is_empty or not junction_stop_mask.intersects(candidate):
            break
        station -= 0.5
    if station <= 0.5 or candidate is None:
        return []
    corridor = centerline.buffer(max(0.05, lane["_width"] / 2 - 0.025), cap_style="flat", join_style="mitre")
    clipped = candidate.intersection(corridor)
    return [part for part in _lines(clipped) if part.length >= 0.55]


def build_street_markings(edges: list[dict[str, Any]], junctions: list[dict[str, Any]],
                          connections: list[dict[str, Any]],
                          direction_guides: list[dict[str, Any]]) -> dict[str, Any]:
    """Return 2D markings and per-lane arrows in east/up/south metre coordinates.

    Marking `shape` values are centerlines; arrow `outline` values are filled
    ground polygons. Only SUMO `s`, `l`, and `r` movement connections receive
    arrows. The profile documents that paint style and exact placement are
    design additions rather than OSM survey or standards compliance.
    """
    for name, value in (("edges", edges), ("junctions", junctions),
                        ("connections", connections), ("direction_guides", direction_guides)):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{name} must be a list")
    edge_index, lane_index = _lane_and_edge_index(list(edges))
    junction_geometry, repaired_junction_count = _junction_geometry(list(junctions))
    junction_marking_mask = (junction_geometry.buffer(_JUNCTION_CLEARANCE_M)
                             if not junction_geometry.is_empty else GeometryCollection())
    junction_stop_mask = (junction_geometry.buffer(0.02)
                          if not junction_geometry.is_empty else GeometryCollection())
    reverse_pairs = _paired_reverse_edges(edge_index)
    certified_pairs: set[frozenset[str]] = set()
    guide_records: list[tuple[LineString, list[str], str | None]] = []
    incomplete_yellow_double_omissions: list[dict[str, Any]] = []
    seen_guide_ids: set[str] = set()
    for guide in direction_guides:
        if not isinstance(guide, dict) or not isinstance(guide.get("id"), str):
            raise ValueError("each direction guide needs a string id")
        if guide["id"] in seen_guide_ids:
            raise ValueError(f"duplicate direction guide id: {guide['id']}")
        seen_guide_ids.add(guide["id"])
        guide_edge_ids = _junctions_for_guide(guide, edge_index)
        if guide.get("marking") not in (None, "derived_direction_guide"):
            raise ValueError(f"direction guide {guide['id']} has an unsupported marking kind")
        guide_line = _line(guide.get("shape"), f"direction guide {guide['id']}")
        lanes = [lane for edge_id in guide_edge_ids for lane in edge_index[edge_id]["_lanes"]
                 if lane.get("kind") == "motor"]
        lane_count = len(lanes)
        if lane_count < 2 or lane_count == 3:
            continue
        source_lane_ids = sorted(lane["id"] for lane in lanes)
        if lane_count <= 2:
            for trimmed in _trim_at_junctions(guide_line, junction_marking_mask):
                guide_records.append((trimmed, source_lane_ids, None))
        else:
            offset_segments = []
            for offset in (-0.125, 0.125):
                segments = []
                for line in _offset_lines(guide_line, offset):
                    segments.extend(_trim_at_junctions(line, junction_marking_mask))
                segments.sort(key=lambda part: guide_line.project(part.interpolate(part.length / 2)))
                offset_segments.append(segments)
            for segment_index in range(max(map(len, offset_segments), default=0)):
                group_id = f"{guide['id']}:yellow-double:{segment_index}"
                if any(segment_index >= len(segments) for segments in offset_segments):
                    incomplete_yellow_double_omissions.append({
                        "semantic_group_id": group_id,
                        "source_lane_ids": source_lane_ids,
                        "source_kind": GUIDE_SOURCE_KIND,
                        "reason": "paired_yellow_stripe_segment_missing_after_junction_clipping",
                    })
                    continue
                for segments in offset_segments:
                    guide_records.append((segments[segment_index], source_lane_ids, group_id))
        certified_pairs.add(frozenset(guide_edge_ids))

    markings: list[dict[str, Any]] = []

    edge_inset = _MARKING_WIDTH_M["lane_edge"] / 2 + _EDGE_PAINT_CLEARANCE_M
    # White outside edges: lane index 0 is SUMO's rightmost motor lane. The
    # opposite edge's measured shared envelope identifies a two-way center;
    # only unpaired one-way carriageways get a far-left white boundary.
    for edge_id in sorted(edge_index):
        edge = edge_index[edge_id]
        motor_lanes = [lane for lane in edge["_lanes"] if lane.get("kind") == "motor"]
        if not motor_lanes:
            continue
        paired = any(edge_id in pair for pair in reverse_pairs | certified_pairs)
        rightmost = min(motor_lanes, key=lambda lane: lane["_index"])
        for line in _offset_lines(rightmost["_line"], rightmost["_width"] / 2 - edge_inset):
            for trimmed in _trim_at_junctions(line, junction_marking_mask):
                markings.append(_marking("lane_edge", WHITE, "solid", trimmed, [rightmost["id"]]))
        if not paired:
            leftmost = max(motor_lanes, key=lambda lane: lane["_index"])
            for line in _offset_lines(leftmost["_line"], -leftmost["_width"] / 2 + edge_inset):
                for trimmed in _trim_at_junctions(line, junction_marking_mask):
                    markings.append(_marking("lane_edge", WHITE, "solid", trimmed, [leftmost["id"]]))

    controlled_lane_ids: set[str] = set()
    valid_connections: list[dict[str, Any]] = []
    for connection in connections:
        if not isinstance(connection, dict):
            raise ValueError("each connection must be an attribute dictionary")
        source_id = connection.get("from")
        if not isinstance(source_id, str) or source_id not in edge_index:
            if connection.get("tl") and isinstance(source_id, str) and not source_id.startswith(":"):
                raise ValueError(f"controlled connection references unknown source edge: {source_id}")
            continue
        try:
            from_lane_index = int(connection["fromLane"])
        except (KeyError, TypeError, ValueError) as error:
            if connection.get("tl"):
                raise ValueError(f"controlled connection from {source_id} needs fromLane") from error
            continue
        source_lanes = edge_index[source_id]["_lanes"]
        lane = next((item for item in source_lanes if item["_index"] == from_lane_index), None)
        if lane is None:
            if connection.get("tl"):
                raise ValueError(f"controlled connection points to a missing lane: {source_id}_{from_lane_index}")
            continue
        if lane.get("kind") != "motor":
            continue
        valid_connections.append({**connection, "_lane": lane, "_edge_id": source_id,
                                  "_source_connection": dict(connection)})
        if connection.get("tl"):
            controlled_lane_ids.add(lane["id"])

    # Same-direction lane separators use actual adjacent motor lane centre
    # lines. They stop at junction polygons; a controlled approach changes to
    # a solid line for its final 12 metres before the node.
    for edge_id in sorted(edge_index):
        motor_lanes = [lane for lane in edge_index[edge_id]["_lanes"] if lane.get("kind") == "motor"]
        motor_lanes.sort(key=lambda lane: lane["_index"])
        for first, second in zip(motor_lanes, motor_lanes[1:]):
            divider = _same_direction_midline(first, second)
            if divider is None or divider.length < 0.35:
                continue
            controlled_approach = first["id"] in controlled_lane_ids or second["id"] in controlled_lane_ids
            if controlled_approach:
                split = max(0.0, divider.length - _APPROACH_SOLID_M)
                sections = [(substring(divider, 0.0, split), "dashed"),
                            (substring(divider, split, divider.length), "solid")]
            else:
                sections = [(divider, "dashed")]
            source_lanes = [first["id"], second["id"]]
            for section, pattern in sections:
                if not isinstance(section, LineString) or section.length < 0.35:
                    continue
                for trimmed in _trim_at_junctions(section, junction_marking_mask):
                    pieces = [trimmed] if pattern == "solid" else _dash(trimmed)
                    for piece in pieces:
                        markings.append(_marking("lane_divider", WHITE, pattern, piece, source_lanes))

    # Certified opposing-lane guides are the only source for yellow center
    # paint. Two-lane streets get one solid stripe; four or more motor lanes
    # get a pair. A three-lane asymmetric case is deliberately left unpainted.
    for line, lane_ids, group_id in guide_records:
        markings.append(_marking("centerline", YELLOW, "solid", line, lane_ids,
                                 source_kind=GUIDE_SOURCE_KIND,
                                 semantic_group_id=group_id))

    # Every actual signal-controlled motor approach receives a transverse
    # stop line one metre before its lane tail, clipped to that lane corridor.
    yellow_clearance = unary_union([line.buffer(
        (_MARKING_WIDTH_M["centerline"] + _MARKING_WIDTH_M["stop_line"]) / 2 + .03,
        cap_style=2, join_style=2) for line, _, _ in guide_records])
    for lane_id in sorted(controlled_lane_ids):
        lane = lane_index[lane_id]
        for line in _stop_line(lane, junction_stop_mask):
            for part in _lines(line.difference(yellow_clearance)):
                if part.length >= .55:
                    markings.append(_marking("stop_line", WHITE, "solid", part, [lane_id]))

    # SUMO uppercase L/R encode partial turns; draw them with the matching
    # left/right glyph and retain every original code for source traceability.
    movement_code_map = {"s": "s", "l": "l", "L": "l", "r": "r", "R": "r"}
    movement_by_lane: dict[str, dict[str, list[dict[str, Any]]]] = {}
    turnaround_connections_omitted = 0
    other_direction_connections_omitted = 0
    for connection in valid_connections:
        original_direction = connection.get("dir")
        movement = movement_code_map.get(original_direction)
        if movement is not None:
            movement_by_lane.setdefault(connection["_lane"]["id"], {}).setdefault(
                movement, []).append(connection["_source_connection"])
        elif original_direction == "t":
            turnaround_connections_omitted += 1
        else:
            other_direction_connections_omitted += 1
    movement_order = {"l": 0, "s": 1, "r": 2}
    arrows = []
    for lane_id in sorted(movement_by_lane):
        lane = lane_index[lane_id]
        centerline: LineString = lane["_line"]
        if centerline.length < 3.5:
            continue
        station = min(centerline.length - 1.4, max(1.2, centerline.length - 7.0))
        center = centerline.interpolate(station)
        forward = _tangent(centerline, station)
        movements = sorted(movement_by_lane[lane_id], key=lambda item: movement_order[item])
        offsets = [0.0] if len(movements) == 1 else [
            (index - (len(movements) - 1) / 2) * 0.72 for index in range(len(movements))]
        lane_corridor = centerline.buffer(max(0.1, lane["_width"] / 2 - 0.08),
                                          cap_style="flat", join_style="mitre")
        for movement, offset in zip(movements, offsets):
            polygon = _absolute_arrow(_arrow_local_polygon(movement), center.x, center.y,
                                      forward, offset)
            clipped = polygon.intersection(lane_corridor)
            if not junction_marking_mask.is_empty:
                clipped = clipped.difference(junction_marking_mask)
            visible = sorted(_polygons(clipped), key=lambda part: part.area, reverse=True)
            if not visible or visible[0].area < polygon.area * 0.82:
                continue
            outline = _rounded_ring(visible[0])
            if len(outline) < 4:
                continue
            source_connections = sorted(
                movement_by_lane[lane_id][movement],
                key=lambda item: tuple(str(item.get(key, "")) for key in
                                       ("from", "fromLane", "to", "toLane", "dir", "tl", "linkIndex")))
            original_directions = [str(item["dir"]) for item in source_connections
                                   if isinstance(item.get("dir"), str)]
            if not original_directions:
                raise ValueError(f"arrow for {lane_id} lost its source SUMO direction")
            arrows.append({"kind": "turn_arrow" if movement != "s" else "straight_arrow",
                           "movement": movement, "color": WHITE, "outline": outline,
                           "original_connection_direction": original_directions[0],
                           "source_connection_directions": original_directions,
                           "source_connections": source_connections,
                           "heading_rad": round(math.atan2(forward[1], forward[0]), 6),
                           "position": [round(center.x + (-forward[1]) * offset, 3),
                                        round(center.y + forward[0] * offset, 3)],
                           "source_lane_ids": [lane_id], "source_kind": SOURCE_KIND})

    return {
        "markings": markings,
        "arrows": arrows,
        "profile": {
            "source_kind": SOURCE_KIND,
            "coordinate_system": "east-up-south metres",
            "topology_source": "SUMO edge lanes, junctions, signal connections, and prequalified direction guides",
            "paint_source": "derived visual design profile; no surveyed OSM marking geometry",
            "surveyed_markings": False,
            "edge_paint_clearance_m": _EDGE_PAINT_CLEARANCE_M,
            "traffic_standard_compliance": "not_asserted",
            "rules": {
                "outer_edges": "white solid on the SUMO rightmost lane; add far-left white only when no reverse shared corridor is present",
                "lane_dividers": "white dashed between adjacent motor lanes; controlled approaches become solid over the final 12 metres",
                "centerlines": "yellow solid only on provided prequalified direction guides; one line for two total lanes, two lines for four or more",
                "stop_lines": "white solid from signal-controlled SUMO connections, one metre before the incoming lane tail and clipped to its lane corridor",
                "turn_arrows": "SUMO dir s maps straight, l/L left, and r/R right (uppercase codes are partial turns); t is a U-turn and is not drawn",
                "dash_geometry": "dashed marking shapes already contain one explicit dash segment each; render as solid ribbons without a second dash pattern",
                "junctions": "ordinary lane and edge markings are clipped outside SUMO junction polygons",
            },
            "counts": {"markings": len(markings), "arrows": len(arrows),
                       "repaired_junction_shapes": repaired_junction_count,
                       "turnaround_connections_omitted": turnaround_connections_omitted,
                       "other_direction_connections_omitted": other_direction_connections_omitted},
            "unrendered_movement_codes": {
                "t": {"meaning": "U-turn", "rendered": False,
                      "connection_count": turnaround_connections_omitted},
                "other": {"meaning": "unsupported or unknown direction code", "rendered": False,
                          "connection_count": other_direction_connections_omitted},
            },
            "unrendered_yellow_double_segments": incomplete_yellow_double_omissions,
        },
    }


def rendered_marking_footprint(shape: Any, width_m: float) -> BaseGeometry:
    """Return the exact strip triangles emitted by the Three.js road renderer.

    This mirrors `laneOffsets()` and `ribbon()` in `frontend/src/city-roads.ts`.
    It intentionally preserves the renderer's bounded miter joins so audits can
    detect geometry that a simple Shapely line buffer would miss.
    """
    line = _line(shape, "rendered marking")
    width = _finite_number(width_m, "rendered marking width", positive=True)
    points = list(line.coords)
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for index, current in enumerate(points):
        prior = points[max(0, index - 1)]
        next_point = points[min(len(points) - 1, index + 1)]
        before_length = math.dist(current, prior)
        after_length = math.dist(next_point, current)
        before_x = (current[1] - prior[1]) / before_length if before_length else 0.0
        before_z = -(current[0] - prior[0]) / before_length if before_length else 0.0
        after_x = (next_point[1] - current[1]) / after_length if after_length else 0.0
        after_z = -(next_point[0] - current[0]) / after_length if after_length else 0.0
        tangent_x, tangent_z = before_x + after_x, before_z + after_z
        tangent_length = math.hypot(tangent_x, tangent_z)
        if tangent_length > 0.01:
            side_x, side_z = tangent_x / tangent_length, tangent_z / tangent_length
        else:
            side_x = before_x or after_x
            side_z = before_z or after_z
        normal_x, normal_z = ((after_x, after_z) if after_length
                              else (before_x, before_z))
        miter = min(width, width / (2 * max(0.55, side_x * normal_x + side_z * normal_z)))
        left.append((current[0] + side_x * miter, current[1] + side_z * miter))
        right.append((current[0] - side_x * miter, current[1] - side_z * miter))

    triangles = []
    for index, (start, end) in enumerate(zip(points, points[1:])):
        if math.dist(start, end) < 0.01:
            continue
        for triangle_points in ((left[index], right[index], left[index + 1]),
                                (right[index], right[index + 1], left[index + 1])):
            triangle = Polygon(triangle_points)
            if triangle.area > 1e-12:
                triangles.append(triangle)
    return unary_union(triangles) if triangles else GeometryCollection()


def clip_street_markings_to_roadbed(layout: dict[str, Any],
                                    displayed_roadbed: BaseGeometry) -> dict[str, Any]:
    """Qualify paint against the exact tiled road surface rendered by the viewer.

    White line ribbons may be shortened where the final roadbed is narrower
    than the source lane geometry. Yellow double lines and turn arrows retain
    their complete meaning: if any part of a yellow pair or arrow does not fit,
    that semantic marking is omitted and its source and reason are recorded.
    """
    if not isinstance(layout, dict):
        raise ValueError("marking layout must be an object")
    if not isinstance(displayed_roadbed, BaseGeometry) or displayed_roadbed.is_empty:
        raise ValueError("displayed roadbed must be a non-empty Shapely geometry")
    if not displayed_roadbed.is_valid or displayed_roadbed.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("displayed roadbed must be a valid polygonal geometry")
    if not isinstance(layout.get("markings"), list) or not isinstance(layout.get("arrows"), list):
        raise ValueError("marking layout needs markings and arrows lists")

    roundoff_margin_m = 0.005
    safe_roadbeds: dict[float, BaseGeometry] = {}

    def safe_roadbed(width_m: float) -> BaseGeometry:
        inset = width_m / 2 + roundoff_margin_m
        if inset not in safe_roadbeds:
            safe_roadbeds[inset] = displayed_roadbed.buffer(-inset)
        return safe_roadbeds[inset]

    qualified_markings: list[dict[str, Any]] = []
    marking_omissions: list[dict[str, Any]] = []
    marking_clip_records: list[dict[str, Any]] = []
    clipped_count = 0
    omitted_count = 0
    centerline_groups: dict[str, list[int]] = {}
    for index, marking in enumerate(layout["markings"]):
        if not isinstance(marking, dict):
            raise ValueError(f"marking {index} must be an object")
        if marking.get("kind") == "centerline" and marking.get("color") == YELLOW:
            group_id = marking.get("semantic_group_id")
            if group_id is None:
                continue
            if not isinstance(group_id, str) or not group_id:
                raise ValueError(f"yellow centerline {index} needs a valid semantic group id")
            centerline_groups.setdefault(group_id, []).append(index)

    paired_centerline_omitted_indexes: set[int] = set()
    for group_id, indexes in centerline_groups.items():
        if len(indexes) != 2:
            raise ValueError(f"yellow double-line group {group_id} must contain exactly two lines")
        lane_ids = tuple(sorted(layout["markings"][indexes[0]].get("source_lane_ids", [])))
        if any(tuple(sorted(layout["markings"][index].get("source_lane_ids", []))) != lane_ids
               for index in indexes[1:]):
            raise ValueError(f"yellow double-line group {group_id} has inconsistent source lanes")
        should_omit_pair = False
        for index in indexes:
            marking = layout["markings"][index]
            width_m = _finite_number(marking.get("width_m"), f"marking {index} width", positive=True)
            line = _line(marking.get("shape"), f"marking {index}")
            if not safe_roadbed(width_m).covers(line):
                should_omit_pair = True
        if should_omit_pair:
            paired_centerline_omitted_indexes.update(indexes)
            for index in indexes:
                marking = layout["markings"][index]
                line = _line(marking.get("shape"), f"marking {index}")
                width_m = _finite_number(marking.get("width_m"), f"marking {index} width", positive=True)
                painted = line.buffer(width_m / 2, cap_style=2)
                marking_omissions.append({
                    "kind": marking.get("kind"),
                    "source_lane_ids": list(marking.get("source_lane_ids", [])),
                    "source_kind": marking.get("source_kind"),
                    "reason": "yellow_double_line_pair_does_not_fit_final_displayed_roadbed",
                    "outside_paint_area_m2": round(painted.difference(displayed_roadbed).area, 6),
                    "pair_source_lane_ids": list(lane_ids),
                    "semantic_group_id": group_id,
                })
                omitted_count += 1

    for index, marking in enumerate(layout["markings"]):
        if index in paired_centerline_omitted_indexes:
            continue
        width_m = _finite_number(marking.get("width_m"), f"marking {index} width", positive=True)
        line = _line(marking.get("shape"), f"marking {index}")
        safe = safe_roadbed(width_m)
        if safe.covers(line):
            qualified_markings.append(dict(marking))
            continue
        source = {
            "kind": marking.get("kind"),
            "source_lane_ids": list(marking.get("source_lane_ids", [])),
            "source_kind": marking.get("source_kind"),
        }
        fragments = [part for part in _lines(line.intersection(safe)) if part.length >= 0.35]
        retained_length_m = sum(part.length for part in fragments)
        painted = line.buffer(width_m / 2, cap_style=2)
        outside_area_m2 = painted.difference(displayed_roadbed).area
        if not fragments:
            marking_omissions.append({
                **source,
                "reason": "outside_final_displayed_roadbed",
                "outside_paint_area_m2": round(outside_area_m2, 6),
                "original_length_m": round(line.length, 3),
            })
            omitted_count += 1
            continue
        for fragment in fragments:
            qualified_markings.append({**marking, "shape": _rounded_shape(fragment)})
        marking_clip_records.append({
            **source,
            "reason": "paint_footprint_clipped_to_final_displayed_roadbed",
            "outside_paint_area_m2": round(outside_area_m2, 6),
            "original_length_m": round(line.length, 3),
            "retained_length_m": round(retained_length_m, 3),
            "retained_fragments": len(fragments),
        })
        clipped_count += 1

    safe_arrow_roadbed = displayed_roadbed.buffer(-roundoff_margin_m)
    qualified_arrows: list[dict[str, Any]] = []
    arrow_omissions: list[dict[str, Any]] = []
    for index, arrow in enumerate(layout["arrows"]):
        if not isinstance(arrow, dict):
            raise ValueError(f"arrow {index} must be an object")
        outline = arrow.get("outline")
        if not isinstance(outline, list) or len(outline) < 4:
            raise ValueError(f"arrow {index} needs a polygon outline")
        polygon = Polygon([(_finite_number(point[0], f"arrow {index} x"),
                            _finite_number(point[1], f"arrow {index} z"))
                           for point in outline])
        if not polygon.is_valid or polygon.area <= 1e-5:
            raise ValueError(f"arrow {index} has an invalid polygon outline")
        if safe_arrow_roadbed.covers(polygon):
            qualified_arrows.append(dict(arrow))
            continue
        arrow_omissions.append({
            "kind": arrow.get("kind"),
            "movement": arrow.get("movement"),
            "source_lane_ids": list(arrow.get("source_lane_ids", [])),
            "source_kind": arrow.get("source_kind"),
            "original_connection_direction": arrow.get("original_connection_direction"),
            "source_connection_directions": list(arrow.get("source_connection_directions", [])),
            "source_connections": list(arrow.get("source_connections", [])),
            "reason": "complete_arrow_does_not_fit_final_displayed_roadbed",
            "outside_paint_area_m2": round(polygon.difference(displayed_roadbed).area, 6),
        })

    result = dict(layout)
    result["markings"] = qualified_markings
    result["arrows"] = qualified_arrows
    profile = dict(layout.get("profile", {}))
    counts = dict(profile.get("counts", {}))
    counts.update({"markings": len(qualified_markings), "arrows": len(qualified_arrows)})
    profile["counts"] = counts
    profile["final_roadbed_clip"] = {
        "source": "final-rendered-roadbed-tiled-union",
        "roundoff_margin_m": roundoff_margin_m,
        "input_markings": len(layout["markings"]),
        "output_markings": len(qualified_markings),
        "clipped_markings": clipped_count,
        "omitted_markings": omitted_count,
        "input_arrows": len(layout["arrows"]),
        "output_arrows": len(qualified_arrows),
        "omitted_arrows": len(arrow_omissions),
        "marking_clips": marking_clip_records,
        "marking_omissions": marking_omissions,
        "arrow_omissions": arrow_omissions,
        "rules": {
            "white_lines": "centerline ribbons are clipped to a roadbed inset of half paint width plus roundoff margin; source lane IDs and source kind are retained",
            "yellow_double_lines": "both lines are omitted together if either complete paint footprint does not fit the final roadbed",
            "arrows": "each arrow is preserved whole or omitted whole; no partial glyphs are emitted",
        },
    }
    result["profile"] = profile
    return result
