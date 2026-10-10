"""Count actual native lane permissions and required authored city route capacity."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from city_ground_fleet import GROUND_FLEET


def lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed,denied=lane.get("allow","").split(),lane.get("disallow","").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) and vehicle_class not in denied and "all" not in denied


def active_ground_fleet_types(authored_counts: dict) -> set[str]:
    if not isinstance(authored_counts, dict) or not authored_counts or any(
            kind not in GROUND_FLEET or type(count) is not int or count < 0
            for kind, count in authored_counts.items()):
        raise ValueError("Configured authored vehicle demand must give explicit nonnegative supported type counts")
    return {kind for kind, count in authored_counts.items() if count > 0}


def native_route_capabilities(network: ET.Element, excluded_walk_edges: set[str],
                              authored_counts: dict, requirements: dict) -> dict:
    active_types = active_ground_fleet_types(authored_counts)
    classes={GROUND_FLEET[name].vehicle_class for name in active_types}
    if any(type(requirements.get(key)) is not bool for key in
            ("require_multiple_motor_lane_edge", "require_bidirectional_motor_roads")) \
            or type(requirements.get("minimum_authored_person_routes")) is not int \
            or requirements["minimum_authored_person_routes"] < 0:
        raise ValueError("Route requirements must be explicit configured booleans and a nonnegative person route count")
    per_class={kind:{"normal_edges":0,"normal_lanes":0} for kind in sorted(classes)}
    motor_classes=classes-{"bicycle"}
    directions=set();multi=[];walk=[]
    for edge in network.findall("edge"):
        if edge.get("function","normal") != "normal":
            continue
        lanes=edge.findall("lane")
        for kind in per_class:
            permitted=sum(lane_allows(lane,kind) for lane in lanes)
            per_class[kind]["normal_lanes"]+=permitted
            per_class[kind]["normal_edges"]+=permitted > 0
        motor=sum(any(lane_allows(lane,kind) for kind in motor_classes) for lane in lanes)
        if motor:
            first,last=edge.get("from"),edge.get("to")
            if first is None or last is None:
                raise ValueError("Native motor edge lacks its actual directed junction identity")
            directions.add((first,last))
            if motor >= 2:multi.append(edge.attrib["id"])
        if edge.get("id") not in excluded_walk_edges and any(lane.get("allow")=="pedestrian"
                and 45 <= float(lane.attrib["length"]) <= 230 for lane in lanes):
            walk.append(edge.attrib["id"])
    bidirectional=sorted([list((a,b)) for a,b in directions if a < b and (b,a) in directions])
    missing=sorted(kind for kind,counts in per_class.items() if not counts["normal_edges"])
    failures=[]
    if missing:failures.append(f"native lane permissions provide no declared fleet class: {missing}")
    if requirements["require_multiple_motor_lane_edge"] and not multi:failures.append("native network has no actual multiple-motor-lane edge")
    if requirements["require_bidirectional_motor_roads"] and not bidirectional:failures.append("native network has no actual bidirectional motor junction pair")
    if len(walk)<requirements["minimum_authored_person_routes"]:failures.append("native network has fewer lawful selected dedicated pedestrian route edges than configured demand")
    return {"schema_version":"aero-bench.city-native-route-capabilities/v1","status":"BLOCKED" if failures else "PASS",
            "failures":failures,"configured_active_vehicle_types":sorted(active_types),"configured_route_requirements":requirements,"classes":per_class,"multiple_motor_lane_edge_ids":sorted(multi),
            "bidirectional_motor_junction_pairs":bidirectional,"selected_dedicated_walk_edge_ids":sorted(walk),
            "scope":"static permissions and directed lanes only; actual connected authored routes and observations are audited separately"}
