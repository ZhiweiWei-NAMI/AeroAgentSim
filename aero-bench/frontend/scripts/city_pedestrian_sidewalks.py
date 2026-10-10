"""Derive native SUMO sidewalks for source crossings without exclusive walk ports.

This is an engineering layout. Original OSM tags remain authoritative source
observations; derived sidewalks have separate metadata and source digests.
"""
from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict

from shapely.geometry import LineString

from city_crossing_groups import DEFAULT_LANE_WIDTH_M, DEFAULT_SIDEWALK_WIDTH_M
from city_road_topology import crossing_junction_id, visible_crossing_junctions

SOURCE_KIND = "source-crossing-derived-exclusive-sidewalks-not-surveyed"
ROAD_TYPES = frozenset({"primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street",
    "service", "road"})
ROAD_CLASSES = ("passenger", "private", "taxi", "bus", "coach", "delivery", "truck",
    "trailer", "motorcycle", "moped", "bicycle", "evehicle", "emergency",
    "authority", "army", "custom1", "custom2")


def _allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed, denied = set(lane.get("allow", "").split()), set(lane.get("disallow", "").split())
    return (not allowed or "all" in allowed or vehicle_class in allowed) \
        and vehicle_class not in denied and "all" not in denied


def _exclusive(lane: ET.Element) -> bool:
    return set(lane.get("allow", "").split()) == {"pedestrian"} and _allows(lane, "pedestrian")


def _edges(network: ET.Element) -> dict[str, ET.Element]:
    edges = {edge.attrib["id"]: edge for edge in network.findall("edge")}
    if len(edges) != len(network.findall("edge")):
        raise ValueError("Duplicate SUMO edge identity")
    return edges


def _lane(edge: ET.Element, index: str) -> ET.Element:
    found = [lane for lane in edge.findall("lane") if lane.get("index") == index]
    if len(found) != 1:
        raise ValueError(f"Missing SUMO connection lane: {edge.get('id')}/{index}")
    return found[0]


def crossing_walk_ports(network: ET.Element, selected_ids: set[str]) -> list[dict]:
    """Inspect actual crossing connections, not inferred permission geometry."""
    edges = _edges(network)
    by_from, by_to = defaultdict(list), defaultdict(list)
    for connection in network.findall("connection"):
        by_from[connection.get("from")].append(connection)
        by_to[connection.get("to")].append(connection)
    rows = []
    for crossing_id in sorted(selected_ids):
        crossing = edges.get(crossing_id)
        if crossing is None or crossing.get("function") != "crossing":
            raise ValueError(f"Missing source crossing: {crossing_id}")
        for endpoint in ("start", "end"):
            walking = {c.get("from") for c in by_to[crossing_id]} if endpoint == "start" \
                else {c.get("to") for c in by_from[crossing_id]}
            if not walking or any(edges[key].get("function") != "walkingarea" for key in walking):
                raise ValueError(f"Crossing lacks actual walking areas: {crossing_id}@{endpoint}")
            ports = {}
            for walking_id in sorted(walking):
                for connection, edge_key, lane_key in [
                        *((c, "from", "fromLane") for c in by_to[walking_id]),
                        *((c, "to", "toLane") for c in by_from[walking_id])]:
                    edge = edges[connection.get(edge_key)]
                    if edge.get("function", "normal") != "normal":
                        continue
                    lane = _lane(edge, connection.get(lane_key))
                    if _allows(lane, "pedestrian"):
                        ports[lane.attrib["id"]] = {"edge_id": edge.attrib["id"],
                            "lane_id": lane.attrib["id"], "lane_index": lane.attrib["index"],
                            "exclusive": _exclusive(lane)}
            rows.append({"crossing_id": crossing_id, "crossing_edges": crossing.get("crossingEdges", "").split(),
                "endpoint": endpoint, "walking_area_ids": sorted(walking),
                "normal_ports": [ports[key] for key in sorted(ports)],
                "exclusive_walk_port": any(port["exclusive"] for port in ports.values())})
    return rows


def _source_way(edge_id: str, ways: dict[str, dict]) -> tuple[str, dict]:
    prefix = edge_id.split("#", 1)[0]
    candidates = [key for key in ways if prefix in (key, "-" + key)]
    if len(candidates) != 1:
        raise ValueError(f"SUMO edge does not bind one source way: {edge_id}")
    return candidates[0], ways[candidates[0]]


def _side_allowed(tags: dict, side: str) -> bool:
    if tags.get("foot") in {"no", "private", "use_sidepath"} \
            or tags.get("access") in {"no", "private"} and tags.get("foot") not in {"yes", "designated", "permissive"}:
        return False
    declaration = tags.get("sidewalk", "unknown")
    permitted = declaration not in {"no", "none", "separate"} \
        and (declaration not in {"left", "right"} or declaration == side)
    override = tags.get("sidewalk:" + side)
    return permitted if override is None else override in {"yes", "designated"}


def _shape(value: str) -> list[tuple[float, float]]:
    points = [tuple(map(float, token.split(",")[:2])) for token in value.split()]
    if len(points) < 2 or any(not all(math.isfinite(v) for v in point) for point in points):
        raise ValueError("Derived sidewalk needs a finite source road shape")
    return points


def plan_crossing_sidewalks(network: ET.Element, source: ET.Element) -> tuple[ET.Element, dict]:
    """Use the existing visible-crossing policy and original road side/access tags."""
    edges = _edges(network)
    normal = {key: edge for key, edge in edges.items() if edge.get("function", "normal") == "normal"}
    visible = visible_crossing_junctions(network, source)
    selected = {key for key, edge in edges.items() if edge.get("function") == "crossing"
                and crossing_junction_id(key) in visible}
    ports = crossing_walk_ports(network, selected)
    missing = [row for row in ports if not row["exclusive_walk_port"]]
    junctions = {crossing_junction_id(row["crossing_id"]) for row in missing}
    ways = {way.attrib["id"]: {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")}
            for way in source.findall("way")}
    nodes = {node.attrib["id"]: (float(node.attrib["x"]), float(node.attrib["y"]))
             for node in network.findall("junction")}
    patch, records = ET.Element("edges"), []
    for edge_id, edge in sorted(normal.items()):
        if not junctions.intersection({edge.get("from"), edge.get("to")}) \
                or any(_exclusive(lane) for lane in edge.findall("lane")):
            continue
        way_id, tags = _source_way(edge_id, ways)
        if tags.get("highway") not in ROAD_TYPES:
            continue
        forward = edge_id.split("#", 1)[0] == way_id
        native_side = "right" if forward else "left"
        if not _side_allowed(tags, native_side):
            raise ValueError(f"Source forbids inferred sidewalk on {native_side}: {edge_id}")
        ET.SubElement(patch, "edge", id=edge_id, sidewalkWidth=f"{DEFAULT_SIDEWALK_WIDTH_M:g}")
        records.append({"edge_id": edge_id, "source_way_id": way_id,
            "original_osm_way_id": tags.get("aero_bench:source_id", way_id),
            "source_tags": tags, "kind": "native-right-sidewalk", "source_way_side": native_side})
        opposite = "-" + edge_id if forward else edge_id[1:]
        if opposite in normal or not forward or not _side_allowed(tags, "left"):
            continue
        points = _shape(edge.get("shape", "")) if edge.get("shape") else [nodes[edge.attrib["from"]], nodes[edge.attrib["to"]]]
        width = sum(float(lane.get("width", str(DEFAULT_LANE_WIDTH_M))) for lane in edge.findall("lane"))
        if width <= 0 or not math.isfinite(width):
            raise ValueError(f"Source road width is invalid: {edge_id}")
        spread = edge.get("spreadType", "right")
        if spread not in {"right", "center", "roadCenter"}:
            raise ValueError(f"Unsupported source road spread: {edge_id}")
        offset = DEFAULT_SIDEWALK_WIDTH_M / 2 + (0 if spread == "right" else width / 2)
        curb = LineString(points).parallel_offset(offset, "left", join_style=2)
        if curb.geom_type != "LineString" or curb.is_empty:
            raise ValueError(f"Derived left sidewalk is disconnected: {edge_id}")
        sidewalk_shape = " ".join(f"{x:.8f},{y:.8f}" for x, y in reversed(list(curb.coords)))
        ET.SubElement(patch, "edge", id=opposite, **{"from": edge.attrib["to"], "to": edge.attrib["from"]},
            type=edge.get("type", ""), priority=edge.get("priority", "0"), numLanes="1", allow="pedestrian",
            width=f"{DEFAULT_SIDEWALK_WIDTH_M:g}", speed=edge.findall("lane")[0].attrib["speed"],
            spreadType="center", shape=sidewalk_shape)
        records.append({"edge_id": opposite, "source_way_id": way_id,
            "original_osm_way_id": tags.get("aero_bench:source_id", way_id), "source_tags": tags,
            "kind": "derived-reverse-pedestrian-only", "source_way_side": "left",
            "source_forward_edge_id": edge_id, "shape": sidewalk_shape})
    if missing and not records:
        raise ValueError("Crossing walk ports are missing and source rules allow no derived sidewalk")
    return patch, {"source_kind": SOURCE_KIND, "profile": "crossing-exclusive-sidewalk-v1",
        "sidewalk_width_m": DEFAULT_SIDEWALK_WIDTH_M, "surveyed_sidewalk": False,
        "selected_source_crossing_ids": sorted(selected), "missing_source_endpoints": missing,
        "affected_junction_ids": sorted(junctions), "derived_edges": records}


def audit_motor_lanes(before: ET.Element, after: ET.Element, added_pedestrian_edges: set[str]) -> dict:
    old = {key: edge for key, edge in _edges(before).items() if edge.get("function", "normal") == "normal"}
    new = {key: edge for key, edge in _edges(after).items() if edge.get("function", "normal") == "normal"}
    if set(new) != set(old) | added_pedestrian_edges or added_pedestrian_edges.intersection(old):
        raise ValueError("Sidewalk conversion changed the declared normal edge inventory")
    for key in added_pedestrian_edges:
        if not new[key].findall("lane") or not all(_exclusive(lane) for lane in new[key].findall("lane")):
            raise ValueError(f"Derived reverse edge permits vehicle contraflow: {key}")
    lane_count, changed_shapes = 0, []
    for key, edge in old.items():
        if any(edge.get(field) != new[key].get(field) for field in ("from", "to")):
            raise ValueError(f"Sidewalk conversion changed motor direction: {key}")
        old_lanes = [lane for lane in edge.findall("lane") if not _exclusive(lane)]
        new_lanes = [lane for lane in new[key].findall("lane") if not _exclusive(lane)]
        if len(old_lanes) != len(new_lanes):
            raise ValueError(f"Sidewalk conversion changed motor lane count: {key}")
        for first, second in zip(old_lanes, new_lanes):
            if any(first.get(field) != second.get(field) for field in ("speed", "width")) \
                    or any(set(first.get(field, "").split()) - {"pedestrian"}
                           != set(second.get(field, "").split()) - {"pedestrian"}
                           for field in ("allow", "disallow")):
                raise ValueError(f"Sidewalk conversion changed motor rights/speed/width: {key}")
            lane_count += 1
            if first.get("shape") != second.get("shape"):
                changed_shapes.append(key)
    def connections(root):
        edges = _edges(root)
        result = set()
        for connection in root.findall("connection"):
            first, second = edges[connection.attrib["from"]], edges[connection.attrib["to"]]
            if any(edge.get("function", "normal") != "normal" for edge in (first, second)):
                continue
            a, b = _lane(first, connection.attrib["fromLane"]), _lane(second, connection.attrib["toLane"])
            if _exclusive(a) or _exclusive(b):
                continue
            for vehicle_class in ROAD_CLASSES:
                if _allows(a, vehicle_class) and _allows(b, vehicle_class):
                    result.add((first.attrib["id"], second.attrib["id"], vehicle_class))
        return result
    old_connections, new_connections = connections(before), connections(after)
    lost = old_connections - new_connections
    if lost:
        raise ValueError(f"Sidewalk conversion removed motor route connections: {sorted(lost)}")
    return {"normal_edges_before": len(old), "normal_edges_after": len(new),
        "added_pedestrian_only_edges": sorted(added_pedestrian_edges), "motor_lanes_preserved": lane_count,
        "motor_class_connections_before": len(old_connections), "after": len(new_connections),
        "added_motor_class_connections": len(new_connections - old_connections),
        "checked_road_classes": list(ROAD_CLASSES), "changed_motor_shape_edge_ids": sorted(set(changed_shapes)),
        "source_motor_direction_count_rights_width_speed_preserved": True}


def audit_completed_crossings(before: ET.Element, after: ET.Element, plan: dict) -> dict:
    def groups(root):
        return {(crossing_junction_id(key), frozenset(edge.get("crossingEdges", "").split())): key
                for key, edge in _edges(root).items() if edge.get("function") == "crossing"}
    old, new = groups(before), groups(after)
    if old.keys() - new.keys():
        raise ValueError("Native sidewalk conversion discarded an unplanned source crossing")
    selected_keys = {key for key, crossing_id in old.items() if crossing_id in plan["selected_source_crossing_ids"]}
    ports = crossing_walk_ports(after, {new[key] for key in selected_keys})
    failed = [row for row in ports if not row["exclusive_walk_port"]]
    if failed:
        raise ValueError(f"Native sidewalk conversion still lacks exclusive crossing walk ports: {failed}")
    return {"source_crossing_groups": len(old), "final_crossing_groups": len(new),
        "selected_crossing_count": len(selected_keys), "selected_endpoints": len(ports),
        "all_selected_endpoints_have_exclusive_walk_ports": True, "endpoints": ports}
