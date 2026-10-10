"""Expand existing SUMO crossings across contiguous lanes of the same approach.

The input is a SUMO ``.net.xml`` whose grade-separated roads have already been
removed.  This module emits plain-XML patches for netconvert; it never edits a
``.net.xml`` directly and never invents a crossing at a node that had none.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from city_road_topology import crossing_junction_id

PEDESTRIAN = "pedestrian"
DEFAULT_LANE_WIDTH_M = 3.2
MAX_DIRECTION_DELTA_DEG = 25.0
MAX_LANE_GAP_M = 0.75
JUNCTION_ADJACENCY_SAMPLE_M = 25.0
DEFAULT_SIDEWALK_WIDTH_M = 2.0
VEHICLE_CLASSES = frozenset({
    "passenger", "private", "taxi", "bus", "coach", "delivery", "truck",
    "trailer", "motorcycle", "moped", "bicycle", "motorized", "evehicle",
    "emergency", "authority", "army", "custom1", "custom2", "rail",
    "rail_urban", "rail_electric", "rail_fast", "ship", "container",
    "scooter", "drone", "electric",
})


@dataclass(frozen=True)
class CrossingReplacement:
    junction_id: str
    previous_edges: tuple[str, ...]
    replacement_edges: tuple[str, ...]
    width_m: float


@dataclass(frozen=True)
class CrossingPatchPlan:
    discarded: tuple[tuple[str, tuple[str, ...]], ...]
    replacements: tuple[CrossingReplacement, ...]


def _tokens(value: str | None) -> set[str]:
    return set(value.split()) if value else set()


def _allows_road_class(allowed: set[str], denied: set[str]) -> bool:
    if "all" in denied:
        return False
    if "all" in allowed:
        return True
    if allowed:
        return bool(allowed & VEHICLE_CLASSES)
    return bool(VEHICLE_CLASSES - denied)


def _effective_permission(lane: ET.Element, edge: ET.Element,
                          edge_type: dict[str, str]) -> tuple[set[str], set[str]]:
    # SUMO selects the closest declaration layer that specifies either
    # permission attribute. Its allow/disallow pair replaces the parent pair;
    # inheriting a parent disallow alongside a child allow misclassifies shared
    # pedestrian/vehicle lanes.
    for declaration in (lane, edge, edge_type):
        if declaration.get("allow") is not None or declaration.get("disallow") is not None:
            return (_tokens(declaration.get("allow")),
                    _tokens(declaration.get("disallow")))
    return set(), set()


def crossing_sidewalk_patch(network: ET.Element,
                            target_edge_ids: set[str] | None = None) -> ET.Element:
    """Add sidewalks only to exclusive road edges in changed crossing groups.

    SUMO's ``sidewalkWidth`` both adds a pedestrian-only lane and removes
    pedestrian access from the other lanes.  Edges with an explicitly shared
    pedestrian permission are left untouched.  Restricting this to changed
    crossing groups keeps unrelated pedestrian routes and signal links intact.
    """
    types = {item.get("id", ""): item.attrib for item in network.findall("type")}
    result = ET.Element("edges")
    for edge in sorted(network.findall("edge"), key=lambda item: item.get("id", "")):
        if edge.get("function", "normal") != "normal":
            continue
        if target_edge_ids is not None and edge.get("id") not in target_edge_ids:
            continue
        edge_type = types.get(edge.get("type", ""), {})
        has_explicit_shared_pedestrian = False
        for lane in edge.findall("lane"):
            allowed, denied = _effective_permission(lane, edge, edge_type)
            permits_pedestrian = "all" in allowed or PEDESTRIAN in allowed
            if permits_pedestrian and PEDESTRIAN not in denied \
                    and _allows_road_class(allowed, denied):
                has_explicit_shared_pedestrian = True
                break
        if has_explicit_shared_pedestrian:
            continue
        if not any(_road_lane(lane, edge, edge_type) for lane in edge.findall("lane")):
            continue
        ET.SubElement(result, "edge", id=edge.get("id", ""),
                      sidewalkWidth=f"{DEFAULT_SIDEWALK_WIDTH_M:g}")
    return result


def _shape(value: str | None) -> list[tuple[float, float]]:
    if not value:
        return []
    points = []
    for token in value.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"Invalid SUMO shape point: {token}")
        point = (float(parts[0]), float(parts[1]))
        if not all(math.isfinite(value) for value in point):
            raise ValueError(f"Nonfinite SUMO shape point: {token}")
        if not points or point != points[-1]:
            points.append(point)
    return points


def _edge_geometry(edge: ET.Element) -> list[tuple[float, float]]:
    points = _shape(edge.get("shape"))
    if len(points) >= 2:
        return points
    for lane in edge.findall("lane"):
        points = _shape(lane.get("shape"))
        if len(points) >= 2:
            return points
    return []


def _outward_direction(edge: ET.Element, junction_id: str) -> tuple[float, float] | None:
    points = _edge_geometry(edge)
    if len(points) < 2:
        return None
    if edge.get("from") == junction_id:
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
    elif edge.get("to") == junction_id:
        dx, dy = points[-2][0] - points[-1][0], points[-2][1] - points[-1][1]
    else:
        return None
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    return dx / length, dy / length


def _same_source_segment(first: ET.Element, second: ET.Element) -> bool:
    """Match SUMO's forward/reverse edge IDs for the same OSM segment."""
    first_id = first.get("id", "")
    second_id = second.get("id", "")
    # netconvert prefixes reverse-direction OSM edges with an extra '-'.
    return first_id.lstrip("-") == second_id.lstrip("-")


def _other_endpoint(edge: ET.Element, junction_id: str) -> str | None:
    if edge.get("from") == junction_id:
        return edge.get("to")
    if edge.get("to") == junction_id:
        return edge.get("from")
    return None


def _aligned(first: ET.Element, second: ET.Element, junction_id: str) -> bool:
    first_direction = _outward_direction(first, junction_id)
    second_direction = _outward_direction(second, junction_id)
    if first_direction is None or second_direction is None:
        return False
    dot = (first_direction[0] * second_direction[0]
           + first_direction[1] * second_direction[1])
    return dot >= math.cos(math.radians(MAX_DIRECTION_DELTA_DEG))


def _point_segment_distance(point: tuple[float, float], start: tuple[float, float],
                            end: tuple[float, float]) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    along = max(0.0, min(1.0, ((point[0] - start[0]) * dx
                               + (point[1] - start[1]) * dy) / denominator))
    return math.hypot(point[0] - (start[0] + along * dx),
                      point[1] - (start[1] + along * dy))


def _orientation(a: tuple[float, float], b: tuple[float, float],
                 c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a: tuple[float, float], b: tuple[float, float],
                        c: tuple[float, float], d: tuple[float, float]) -> bool:
    epsilon = 1e-9
    o1, o2, o3, o4 = (_orientation(a, b, c), _orientation(a, b, d),
                      _orientation(c, d, a), _orientation(c, d, b))
    if ((o1 > epsilon and o2 < -epsilon or o1 < -epsilon and o2 > epsilon)
            and (o3 > epsilon and o4 < -epsilon or o3 < -epsilon and o4 > epsilon)):
        return True
    return (abs(o1) <= epsilon and _point_segment_distance(c, a, b) <= epsilon
            or abs(o2) <= epsilon and _point_segment_distance(d, a, b) <= epsilon
            or abs(o3) <= epsilon and _point_segment_distance(a, c, d) <= epsilon
            or abs(o4) <= epsilon and _point_segment_distance(b, c, d) <= epsilon)


def _polyline_distance(first: list[tuple[float, float]],
                       second: list[tuple[float, float]]) -> float:
    best = math.inf
    for a, b in zip(first, first[1:]):
        for c, d in zip(second, second[1:]):
            if _segments_intersect(a, b, c, d):
                return 0.0
            best = min(best, _point_segment_distance(a, c, d),
                       _point_segment_distance(b, c, d),
                       _point_segment_distance(c, a, b),
                       _point_segment_distance(d, a, b))
    return best


def _lane_width(lane: ET.Element) -> float:
    width = float(lane.get("width", str(DEFAULT_LANE_WIDTH_M)))
    if not math.isfinite(width) or width <= 0:
        raise ValueError(f"SUMO lane has invalid width: {lane.get('id')}")
    return width


def _truncate_polyline(points: list[tuple[float, float]], max_length: float
                       ) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []
    result = [points[0]]
    consumed = 0.0
    for start, end in zip(points, points[1:]):
        segment_length = math.dist(start, end)
        if segment_length <= 1e-12:
            continue
        remaining = max_length - consumed
        if segment_length <= remaining:
            result.append(end)
            consumed += segment_length
            continue
        ratio = max(0.0, remaining / segment_length)
        result.append((start[0] + (end[0] - start[0]) * ratio,
                       start[1] + (end[1] - start[1]) * ratio))
        break
    return result


def _lane_geometry_near_junction(lane: ET.Element, edge: ET.Element,
                                 junction_id: str) -> list[tuple[float, float]]:
    points = _shape(lane.get("shape"))
    if edge.get("from") == junction_id:
        pass
    elif edge.get("to") == junction_id:
        points.reverse()
    else:
        return []
    return _truncate_polyline(points, JUNCTION_ADJACENCY_SAMPLE_M)


def _adjacent(first: ET.Element, second: ET.Element, junction_id: str) -> bool:
    first_lanes = first.findall("lane")
    second_lanes = second.findall("lane")
    if not first_lanes or not second_lanes:
        return False
    for first_lane in first_lanes:
        first_shape = _lane_geometry_near_junction(first_lane, first, junction_id)
        if len(first_shape) < 2:
            continue
        for second_lane in second_lanes:
            second_shape = _lane_geometry_near_junction(second_lane, second, junction_id)
            if len(second_shape) < 2:
                continue
            distance = _polyline_distance(first_shape, second_shape)
            if distance <= (_lane_width(first_lane) + _lane_width(second_lane)) / 2 + MAX_LANE_GAP_M:
                return True
    return False


def _road_lane(lane: ET.Element, edge: ET.Element,
               edge_type: dict[str, str]) -> bool:
    allowed, denied = _effective_permission(lane, edge, edge_type)
    return _allows_road_class(allowed, denied)


def _road_edge(edge: ET.Element, types: dict[str, dict[str, str]]) -> bool:
    if edge.get("function", "normal") != "normal":
        return False
    lanes = edge.findall("lane")
    if not lanes:
        return False
    edge_type = types.get(edge.get("type", ""), {})
    return any(_road_lane(lane, edge, edge_type) for lane in lanes)


def _approach_group(network_edges: dict[str, ET.Element],
                    types: dict[str, dict[str, str]], junction_id: str,
                    crossed_edge_ids: tuple[str, ...]) -> tuple[str, ...]:
    group: dict[str, ET.Element] = {}
    for edge_id in crossed_edge_ids:
        edge = network_edges.get(edge_id)
        if edge is None:
            raise ValueError(f"SUMO crossing references a missing road edge: {edge_id}")
        if _other_endpoint(edge, junction_id) is None:
            raise ValueError(f"Crossing road edge is not incident to its junction: {edge_id}")
        if not _road_edge(edge, types):
            raise ValueError(f"Crossing references a non-road edge: {edge_id}")
        group[edge_id] = edge

    candidates = [edge for edge in network_edges.values()
                  if edge.get("id") not in group
                  and _other_endpoint(edge, junction_id) is not None
                  and _road_edge(edge, types)]
    changed = True
    while changed:
        changed = False
        for candidate in candidates:
            candidate_id = candidate.get("id", "")
            for current in tuple(group.values()):
                related = (_same_source_segment(current, candidate)
                           or _other_endpoint(current, junction_id)
                           == _other_endpoint(candidate, junction_id))
                if (related and _aligned(current, candidate, junction_id)
                        and _adjacent(current, candidate, junction_id)):
                    group[candidate_id] = candidate
                    changed = True
                    break
            if changed:
                candidates = [edge for edge in candidates if edge.get("id") not in group]
                break
    return tuple([*crossed_edge_ids,
                  *sorted(edge_id for edge_id in group if edge_id not in crossed_edge_ids)])


def crossing_replacements(network: ET.Element) -> tuple[CrossingReplacement, ...]:
    """Find existing signalized crossings whose road-edge group is incomplete."""
    types = {item.get("id", ""): item.attrib for item in network.findall("type")}
    junctions = {item.get("id", ""): item for item in network.findall("junction")}
    edges = {item.get("id", ""): item for item in network.findall("edge")
             if item.get("id")}
    replacements = []
    for crossing_edge in network.findall("edge"):
        if crossing_edge.get("function") != "crossing":
            continue
        crossing_id = crossing_edge.get("id", "")
        junction_id = crossing_junction_id(crossing_id)
        junction = junctions.get(junction_id)
        if junction is None:
            raise ValueError(f"SUMO crossing references a missing junction: {crossing_id}")
        if junction.get("type") not in {"traffic_light", "traffic_light_unregulated"}:
            continue
        previous = tuple(dict.fromkeys(crossing_edge.get("crossingEdges", "").split()))
        if not previous:
            raise ValueError(f"SUMO crossing has no crossed-edge list: {crossing_id}")
        expanded = _approach_group(edges, types, junction_id, previous)
        if len(expanded) == len(previous):
            continue
        lanes = crossing_edge.findall("lane")
        widths = [_lane_width(lane) for lane in lanes]
        if not widths:
            raise ValueError(f"SUMO crossing has no lanes: {crossing_id}")
        replacements.append(CrossingReplacement(
            junction_id=junction_id,
            previous_edges=previous,
            replacement_edges=expanded,
            width_m=max(widths),
        ))
    unique = {}
    for replacement in replacements:
        key = (replacement.junction_id, replacement.previous_edges,
               replacement.replacement_edges)
        unique[key] = replacement
    return tuple(unique[key] for key in sorted(unique))


def crossing_patch(network: ET.Element) -> ET.Element:
    """Return a `.con.xml` patch replacing only expanded crossing approaches."""
    plan = crossing_patch_plan(network)
    result = ET.Element("connections")
    for junction_id, edge_ids in plan.discarded:
        ET.SubElement(result, "crossing", node=junction_id,
                      edges=" ".join(edge_ids), discard="true")
    for replacement in plan.replacements:
        ET.SubElement(result, "crossing", node=replacement.junction_id,
                      edges=" ".join(replacement.replacement_edges),
                      priority="true", width=f"{replacement.width_m:g}")
    return result


def crossing_patch_plan(network: ET.Element) -> CrossingPatchPlan:
    """Deduplicate rebuilt crossings and discard every covered old definition."""
    expanded = crossing_replacements(network)
    additions: dict[tuple[str, tuple[str, ...]], CrossingReplacement] = {}
    for replacement in expanded:
        canonical_edges = tuple(sorted(replacement.replacement_edges))
        key = replacement.junction_id, canonical_edges
        previous = additions.get(key)
        if previous is None:
            additions[key] = CrossingReplacement(
                junction_id=replacement.junction_id,
                previous_edges=replacement.previous_edges,
                replacement_edges=canonical_edges,
                width_m=replacement.width_m,
            )
        elif replacement.width_m > previous.width_m:
            additions[key] = CrossingReplacement(
                junction_id=previous.junction_id,
                previous_edges=previous.previous_edges,
                replacement_edges=previous.replacement_edges,
                width_m=replacement.width_m,
            )

    discarded = set()
    for replacement in additions.values():
        replacement_set = set(replacement.replacement_edges)
        for edge in network.findall("edge"):
            if edge.get("function") != "crossing":
                continue
            junction_id = crossing_junction_id(edge.get("id", ""))
            if junction_id != replacement.junction_id:
                continue
            edge_ids = tuple(dict.fromkeys(edge.get("crossingEdges", "").split()))
            if edge_ids and set(edge_ids) <= replacement_set:
                discarded.add((junction_id, edge_ids))

    return CrossingPatchPlan(
        discarded=tuple(sorted(discarded)),
        replacements=tuple(additions[key] for key in sorted(additions)),
    )


def write_patches(network_path: str, edge_patch_path: str,
                  crossing_patch_path: str) -> tuple[int, int, int]:
    """Write deterministic patches and return sidewalk/discard/add counts."""
    network = ET.parse(network_path).getroot()
    plan = crossing_patch_plan(network)
    target_edge_ids = {edge_id for replacement in plan.replacements
                       for edge_id in replacement.replacement_edges}
    edge_patch = crossing_sidewalk_patch(network, target_edge_ids)
    con_patch = crossing_patch(network)
    for element in (edge_patch, con_patch):
        ET.indent(element, space="  ")
    ET.ElementTree(edge_patch).write(edge_patch_path, encoding="utf-8", xml_declaration=True)
    ET.ElementTree(con_patch).write(crossing_patch_path, encoding="utf-8", xml_declaration=True)
    sidewalk_edge_count = len(edge_patch.findall("edge"))
    return sidewalk_edge_count, len(plan.discarded), len(plan.replacements)
