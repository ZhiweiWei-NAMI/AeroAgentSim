"""One native curb-derived signal inventory for road, fixtures and real recording.

Locations are engineering estimates from incoming SUMO motor lanes. They are
not surveyed OSM pole positions. TLS phases are not modified by this module.
"""
from __future__ import annotations
import math
import xml.etree.ElementTree as ET

SIGNAL_MOTOR_CLASSES = ("passenger", "private", "taxi", "bus", "coach", "delivery", "truck",
    "trailer", "motorcycle", "moped", "evehicle", "emergency", "authority", "army", "custom1", "custom2")
def allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed = lane.get("allow", "").split()
    denied = lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) and vehicle_class not in denied and "all" not in denied

def signal_inventory(network_xml: ET.Element) -> list[dict[str, object]]:
    edges = {edge.get("id"): edge for edge in network_xml.findall("edge")
             if edge.get("function", "normal") == "normal"}
    unique: dict[tuple[str, str], ET.Element] = {}
    for connection in network_xml.findall("connection"):
        tls_id = connection.get("tl")
        source = connection.get("from")
        if tls_id and source in edges and connection.get("linkIndex") is not None:
            lanes = edges[source].findall("lane")
            lane_index = int(connection.attrib["fromLane"])
            if lane_index >= len(lanes) or not any(allows(lanes[lane_index], vehicle_class)
                                                     for vehicle_class in SIGNAL_MOTOR_CLASSES):
                continue
            key = (tls_id, source)
            prior = unique.get(key)
            rank = (lane_index, connection.get("dir") != "s", int(connection.attrib["linkIndex"]))
            prior_rank = (int(prior.attrib["fromLane"]), prior.get("dir") != "s",
                          int(prior.attrib["linkIndex"])) if prior is not None else None
            if prior_rank is None or rank < prior_rank:
                unique[key] = connection
    signals = []
    for (tls_id, source), connection in sorted(unique.items()):
        # Use the rightmost signal-controlled motor lane for both the pole and its
        # state. Lane 0 can be a generated sidewalk, and walkingarea links are not car signals.
        lane = edges[source].findall("lane")[int(connection.attrib["fromLane"])]
        points = [tuple(map(float, point.split(",")[:2])) for point in lane.attrib["shape"].split()]
        if len(points) < 2:
            raise ValueError(f"Signal lane has no source tangent: {lane.attrib['id']}")
        x0, y0 = points[-2]; x1, y1 = points[-1]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length < 0.01:
            raise ValueError(f"Signal lane tangent is degenerate: {lane.attrib['id']}")
        # Place the pole beside the incoming lane, before the stop line.
        curb_offset = float(lane.get("width", "3.2")) / 2 + 1.0
        sx, sy = x1 - dx / length * 2.5 + dy / length * curb_offset, y1 - dy / length * 2.5 - dx / length * curb_offset
        signals.append({"id": f"{tls_id}:{source}", "tls": tls_id, "link": int(connection.attrib["linkIndex"]),
                        "x": round(sx, 3), "z": round(sy, 3),
                        "heading": round(math.degrees(math.atan2(dx, dy)), 1)})
    return signals


def canonical_signal_inventory(network: ET.Element, projection) -> list[dict]:
    signals = signal_inventory(network)
    for signal in signals:
        native_x, native_y = signal["x"], signal["z"]
        signal["heading"] = projection.heading(native_x, native_y, signal["heading"])
        signal["x"], signal["z"] = projection.point(native_x, native_y)
    return signals
