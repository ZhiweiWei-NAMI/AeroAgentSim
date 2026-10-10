"""Classify SUMO lanes and crossings against the OSM network that produced them."""
from __future__ import annotations

import xml.etree.ElementTree as ET


def lane_kind(lane: ET.Element) -> str:
    """Traffic kind from SUMO permissions; a lane that permits no class is closed."""
    allowed = set(lane.get("allow", "").split())
    if not allowed and lane.get("disallow", "").split() == ["all"]:
        # netconvert emits such internal lanes, e.g. footway-to-cycleway turns; nothing travels or is paved there.
        return "closed"
    if not allowed or "passenger" in allowed or "taxi" in allowed or "emergency" in allowed:
        return "motor"
    if "delivery" in allowed or "bus" in allowed or "truck" in allowed:
        return "shared" if "pedestrian" in allowed else "motor"
    if "bicycle" in allowed and "pedestrian" not in allowed:
        return "cycle"
    return "walk"


def motor_neighbors(network: ET.Element) -> dict[str, set[str]]:
    neighbors: dict[str, set[str]] = {}
    for edge in network.findall("edge"):
        if edge.get("function", "normal") != "normal" or not any(
                lane_kind(lane) in ("motor", "shared", "cycle") for lane in edge.findall("lane")):
            continue
        source, destination = edge.get("from"), edge.get("to")
        if source is None or destination is None:
            raise ValueError(f"SUMO motor edge lacks endpoints: {edge.get('id')}")
        neighbors.setdefault(source, set()).add(destination)
        neighbors.setdefault(destination, set()).add(source)
    return neighbors


def zebra_crossing_nodes(osm: ET.Element) -> set[str]:
    mapped = set()
    for node in osm.findall("node"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in node.findall("tag")}
        if (tags.get("crossing") == "zebra" or tags.get("crossing_ref") == "zebra"
                or tags.get("crossing:markings") == "zebra"):
            mapped.add(node.attrib["id"])
    return mapped


def crossing_junction_id(edge_id: str) -> str:
    if not edge_id.startswith(":") or "_c" not in edge_id:
        raise ValueError(f"SUMO crossing has no junction identity: {edge_id}")
    return edge_id[1:].split("_c", 1)[0]


def visible_crossing_junctions(network: ET.Element, osm: ET.Element) -> set[str]:
    """Use OSM zebra tags or SUMO controlled, multiway intersections for markings."""
    marked = zebra_crossing_nodes(osm)
    neighbors = motor_neighbors(network)
    marked.update(junction.attrib["id"] for junction in network.findall("junction")
                  if junction.get("type") == "traffic_light"
                  and len(neighbors.get(junction.attrib["id"], set())) >= 3)
    return marked
