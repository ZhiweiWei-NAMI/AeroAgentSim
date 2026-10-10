"""Declared ground-road selection and SUMO topology evidence for any OSM crop.

Selection removes unsupported road ways before presentation or SUMO import. It
does not assign road heights, edit building heights, or claim surveyed terrain.
Network auditing reads an existing SUMO artifact without simulating traffic.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


GROUND_ROAD_POLICY = "aero-bench.declared-ground-roads/v1"
UNSUPPORTED_ROAD_HIGHWAYS = frozenset({"platform"})
FALSE_VALUES = {"", "no", "false", "0", "off"}
TUNNEL_NAME = re.compile(r"tunnel|隧道", re.IGNORECASE)
ELEVATED_NAME = re.compile(r"elevated|高架", re.IGNORECASE)
NUMBER = re.compile(r"\s*([+-]?\d+(?:\.\d+)?)\s*(?:m|meters?)?\s*", re.IGNORECASE)
HEX = re.compile(r"[0-9a-f]{64}\Z")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def osm_tags(element: ET.Element) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag in element.findall("tag"):
        key, value = tag.get("k"), tag.get("v")
        if not key or value is None or key in tags:
            raise ValueError(f"Missing or duplicate OSM tag on {element.tag}/{element.get('id')}")
        tags[key] = value
    return tags


def positive_tag(tags: dict[str, str], key: str) -> bool:
    return key in tags and tags[key].strip().lower() not in FALSE_VALUES


def numeric_values(raw: str | None) -> list[float]:
    if raw is None:
        return []
    values = []
    for part in re.split(r"[;,]", raw):
        match = NUMBER.fullmatch(part)
        if not match:
            return []
        value = float(match.group(1))
        if not math.isfinite(value):
            return []
        values.append(value)
    return values


def nonzero_grade_reason(value: str | None, key: str = "layer") -> str | None:
    if value is None:
        return None
    values = numeric_values(value)
    if not values:
        return f"unparsed_non_ground_{key}:{value}"
    if all(number == 0.0 for number in values):
        return None
    return f"nonzero_{key}:{value}"


def way_filter_reasons(tags: dict[str, str], node_ele: dict[str, str] | None = None) -> list[str]:
    """Classify declared grade evidence; a constant absolute ele stays metadata."""
    reasons = []
    for key in ("bridge", "tunnel", "elevated"):
        if positive_tag(tags, key):
            reasons.append(f"{key}_tag:{tags[key]}")
    for key in ("layer", "level"):
        reason = nonzero_grade_reason(tags.get(key), key)
        if reason:
            reasons.append(reason)
    if tags.get("location", "").strip().lower() in {"underground", "overground", "overhead", "elevated"}:
        reasons.append(f"non_ground_location:{tags['location']}")
    if tags.get("highway") in {"steps", "elevator"}:
        reasons.append(f"nonflat_highway:{tags['highway']}")
    if tags.get("highway") in UNSUPPORTED_ROAD_HIGHWAYS:
        reasons.append(f"unsupported_highway:{tags['highway']}")
    if "incline" in tags:
        raw = tags["incline"]
        value = re.sub(r"[%°]$", "", raw.strip())
        parsed = numeric_values(value)
        if not parsed:
            reasons.append(f"unsupported_incline:{raw}")
        elif any(number != 0.0 for number in parsed):
            reasons.append(f"nonzero_incline:{raw}")
    elevations = []
    supplied = {"way": tags["ele"]} if "ele" in tags else {}
    supplied.update({f"node:{key}": value for key, value in (node_ele or {}).items()})
    for origin, raw in supplied.items():
        parsed = numeric_values(raw)
        if not parsed:
            reasons.append(f"unsupported_ele:{origin}={raw}")
        elevations.extend(parsed)
    if len(set(elevations)) > 1:
        reasons.append(f"nonflat_ele_profile:{min(elevations)}..{max(elevations)}")
    for key, value in tags.items():
        if key != "name" and not key.startswith("name:"):
            continue
        if TUNNEL_NAME.search(value) and not positive_tag(tags, "tunnel"):
            reasons.append(f"tunnel_name_heuristic:{key}={value}")
        if ELEVATED_NAME.search(value) and not positive_tag(tags, "bridge") and not positive_tag(tags, "elevated"):
            reasons.append(f"elevated_name_heuristic:{key}={value}")
    return reasons


def classify_roadways(source: bytes) -> dict:
    root = ET.fromstring(source)
    if root.tag != "osm":
        raise ValueError("Ground road source must be OSM XML")
    nodes = {}
    coordinates: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in root.findall("node"):
        node_id = node.get("id")
        if not node_id or node_id in nodes:
            raise ValueError("Missing or duplicate OSM node ID")
        nodes[node_id] = osm_tags(node)
        lat, lon = node.get("lat"), node.get("lon")
        if lat is None or lon is None or not all(math.isfinite(float(v)) for v in (lat, lon)):
            raise ValueError(f"OSM node lacks finite coordinates: {node_id}")
        coordinates[(lat, lon)].append(node_id)
    roads, excluded, elevation_review = {}, {}, []
    all_way_ids = set()
    ignored_non_highway_grade_way_count = 0
    for way in root.findall("way"):
        way_id = way.get("id")
        if not way_id or way_id in all_way_ids:
            raise ValueError("Missing or duplicate OSM way ID")
        all_way_ids.add(way_id)
        tags = osm_tags(way)
        if not tags.get("highway"):
            ignored_non_highway_grade_way_count += bool(way_filter_reasons(tags))
            continue
        node_ids = [nd.get("ref", "") for nd in way.findall("nd")]
        missing = set(node_ids) - nodes.keys()
        if missing:
            raise ValueError(f"Road way {way_id} refers to missing nodes: {sorted(missing)}")
        node_ele = {node_id: nodes[node_id]["ele"] for node_id in node_ids if "ele" in nodes[node_id]}
        reasons = way_filter_reasons(tags, node_ele)
        values = numeric_values(tags.get("ele"))
        for raw in node_ele.values():
            values.extend(numeric_values(raw))
        record = {
            "osm_way_id": way_id, "original_osm_way_id": tags.get("aero_bench:source_id"),
            "highway": tags["highway"],
            "names": {key: value for key, value in tags.items() if key == "name" or key.startswith("name:")},
            "bridge": tags.get("bridge"), "tunnel": tags.get("tunnel"), "elevated": tags.get("elevated"),
            "layer_raw": tags.get("layer"), "level_raw": tags.get("level"), "incline_raw": tags.get("incline"),
            "way_ele_raw": tags.get("ele"), "node_ele": node_ele, "reasons": reasons,
        }
        roads[way_id] = record
        if reasons:
            excluded[way_id] = record
        if "ele" in tags or node_ele:
            elevation_review.append({
                "osm_way_id": way_id, "original_osm_way_id": tags.get("aero_bench:source_id"),
                "way_ele_raw": tags.get("ele"), "node_ele": node_ele,
                "numeric_ele_min": min(values) if values else None,
                "numeric_ele_max": max(values) if values else None,
                "profile_delta_m": max(values) - min(values) if values else None,
                "absolute_ele_is_not_a_non_ground_filter": True,
            })
    return {
        "policy": GROUND_ROAD_POLICY, "roads": roads, "excluded_ways": excluded,
        "unsupported_road_highways": sorted(UNSUPPORTED_ROAD_HIGHWAYS),
        "elevation_review": elevation_review,
        "ignored_non_highway_grade_way_count": ignored_non_highway_grade_way_count,
        "source_counts": {"nodes": len(nodes), "ways": len(all_way_ids), "road_ways": len(roads),
                          "relations": len(root.findall("relation"))},
        "same_xy_distinct_node_groups": [sorted(ids) for ids in coordinates.values() if len(ids) > 1],
        "height_claim": "Declared tags and supplied profiles only; missing road elevation remains unmeasured",
    }


def filtered_osm_xml(source: bytes, excluded: set[str]) -> bytes:
    """Remove selected ways and relation members while retaining node identities."""
    root = ET.fromstring(source)
    for way in root.findall("way"):
        if way.get("id") in excluded:
            root.remove(way)
    for relation in root.findall("relation"):
        for member in relation.findall("member"):
            if member.get("type") == "way" and member.get("ref") in excluded:
                relation.remove(member)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def filtered_effective_json(source: bytes, road_source: bytes, excluded: set[str]) -> bytes:
    """Derive a road presentation subsource; every non-road element stays intact."""
    document = json.loads(source)
    elements = document["elements"]
    if not isinstance(elements, list):
        raise ValueError("Effective OSM elements must be a list")
    source_roads = {
        way.get("id"): ([nd.get("ref") for nd in way.findall("nd")], osm_tags(way))
        for way in ET.fromstring(road_source).findall("way") if osm_tags(way).get("highway")
    }
    effective_roads = {
        str(element["id"]): ([str(node) for node in element["nodes"]], element["tags"])
        for element in elements if element.get("type") == "way" and element.get("tags", {}).get("highway")
    }
    if source_roads != effective_roads:
        raise ValueError("Effective and SUMO source road nodes/tags differ")
    effective_nodes = {str(element["id"]): element for element in elements if element.get("type") == "node"}
    road_nodes = {node.get("id"): node for node in ET.fromstring(road_source).findall("node")}
    used_nodes = {node_id for node_ids, _ in source_roads.values() for node_id in node_ids}
    for node_id in used_nodes:
        if node_id not in effective_nodes or node_id not in road_nodes:
            raise ValueError(f"Effective and SUMO source road node is missing: {node_id}")
        # The scene compiler serializes XML coordinates with 15 significant
        # digits; compare that representation without a geometric tolerance.
        if any(format(float(effective_nodes[node_id][key]), ".15g") !=
               format(float(road_nodes[node_id].get(key)), ".15g") for key in ("lat", "lon")):
            raise ValueError(f"Effective and SUMO source road coordinates differ: {node_id}")
    document["elements"] = [
        element for element in elements
        if not (element.get("type") == "way" and element.get("tags", {}).get("highway")
                and str(element["id"]) in excluded)
    ]
    for element in document["elements"]:
        if element.get("type") == "relation" and any(
                member.get("type") == "way" and str(member.get("ref")) in excluded
                for member in element.get("members", [])):
            raise ValueError("Excluded road belongs to a retained effective OSM relation; source requires explicit relation review")
    return json_bytes(document)


def edge_source_way_id(edge_id: str, source_way_ids: set[str]) -> str:
    base = edge_id.split("#", 1)[0]
    candidates = [base, base[1:]] if base.startswith("-") else [base]
    matches = [candidate for candidate in candidates if candidate in source_way_ids]
    if len(matches) != 1:
        raise ValueError(f"SUMO edge cannot be mapped uniquely to a source OSM way: {edge_id}")
    return matches[0]


def network_counts(root: ET.Element) -> dict[str, int]:
    edges = root.findall("edge")
    return {
        "all_edges": len(edges),
        "normal_edges": sum(edge.get("function", "normal") == "normal" for edge in edges),
        "internal_edges": sum(edge.get("function") == "internal" for edge in edges),
        "other_generated_edges": sum(edge.get("function", "normal") not in {"normal", "internal"} for edge in edges),
        "connections": len(root.findall("connection")),
        "crossings": sum(edge.get("function") == "crossing" for edge in edges),
        "traffic_light_programs": len(root.findall("tlLogic")),
    }


def _network_index(root: ET.Element) -> tuple[dict, dict]:
    edges, lanes = {}, {}
    for element in root.iter():
        for point in element.get("shape", "").split():
            try:
                coordinates = [float(value) for value in point.split(",")]
            except ValueError as exc:
                raise ValueError(f"Invalid SUMO shape point on {element.tag}/{element.get('id')}") from exc
            if len(coordinates) not in {2, 3} or not all(math.isfinite(value) for value in coordinates):
                raise ValueError(f"Invalid SUMO shape point on {element.tag}/{element.get('id')}")
            if len(coordinates) == 3 and coordinates[2] != 0.0:
                raise ValueError(f"Non-ground SUMO Z remains on {element.tag}/{element.get('id')}")
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge_id in edges:
            raise ValueError("Missing or duplicate SUMO edge ID")
        edges[edge_id] = edge
        for lane in edge.findall("lane"):
            lane_id, index = lane.get("id"), lane.get("index")
            if not lane_id or lane_id in lanes or index is None:
                raise ValueError(f"Missing or duplicate SUMO lane identity: {edge_id}")
            lanes[lane_id] = edge_id
    return edges, lanes


def _connection_lane(edge: ET.Element, raw_index: str | None) -> ET.Element:
    # SUMO connection indices address the edge's lane sequence. SUMO 1.27.1
    # may repeat lane/@index=0 on multiple lanes of a split internal edge.
    try:
        index = int(raw_index)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Dangling SUMO connection lane on {edge.get('id')}: {raw_index}") from exc
    lanes = edge.findall("lane")
    if index < 0 or index >= len(lanes):
        raise ValueError(f"Dangling SUMO connection lane on {edge.get('id')}: {raw_index}")
    return lanes[index]


def audit_ground_network(network: bytes, ground_source: bytes, *, expected_projection: str) -> dict:
    """Prove every normal/generated edge and connection closes on ground ways."""
    source = classify_roadways(ground_source)
    if source["excluded_ways"]:
        raise ValueError(f"Filtered OSM still contains unsupported road ways: {sorted(source['excluded_ways'])}")
    root = ET.fromstring(network)
    if root.tag != "net":
        raise ValueError("Ground network must be SUMO net XML")
    locations = root.findall("location")
    if len(locations) != 1 or not isinstance(expected_projection, str) or not expected_projection:
        raise ValueError("Ground SUMO network requires one location and an expected projection")
    location = locations[0]
    if location.get("projParameter") != expected_projection:
        raise ValueError("Ground SUMO projection differs from expected projection")
    try:
        offset = [float(value) for value in location.get("netOffset", "").split(",")]
    except ValueError as exc:
        raise ValueError("Ground SUMO network requires finite netOffset=0,0") from exc
    if len(offset) != 2 or any(not math.isfinite(value) or value != 0.0 for value in offset):
        raise ValueError("Ground SUMO network requires finite netOffset=0,0")
    edges, lanes = _network_index(root)
    junctions = {}
    for junction in root.findall("junction"):
        junction_id = junction.get("id")
        if not junction_id or junction_id in junctions:
            raise ValueError("Missing or duplicate SUMO junction ID")
        junctions[junction_id] = junction
    normal = {key for key, edge in edges.items() if edge.get("function", "normal") == "normal"}
    if not normal:
        raise ValueError("Ground SUMO network has no normal road edges")
    for edge_id in normal:
        if any(edges[edge_id].get(key) not in junctions for key in ("from", "to")):
            raise ValueError(f"Dangling SUMO normal edge endpoint junction: {edge_id}")
    normal_sources = {key: edge_source_way_id(key, set(source["roads"])) for key in normal}
    forward, reverse = defaultdict(set), defaultdict(set)
    connections = root.findall("connection")
    for connection in connections:
        origin, target = connection.get("from"), connection.get("to")
        if origin not in edges or target not in edges:
            raise ValueError(f"Dangling SUMO connection edge: {connection.attrib}")
        for edge_id, attribute in ((origin, "fromLane"), (target, "toLane")):
            _connection_lane(edges[edge_id], connection.get(attribute))
        via = connection.get("via")
        if via and via not in lanes:
            raise ValueError(f"Dangling SUMO via lane: {via}")
        path = [origin, *([lanes[via]] if via else []), target]
        for first, second in zip(path, path[1:]):
            forward[first].add(second)
            reverse[second].add(first)
    for junction in root.findall("junction"):
        for attribute in ("incLanes", "intLanes"):
            missing = set(junction.get(attribute, "").split()) - lanes.keys()
            if missing:
                raise ValueError(f"Dangling SUMO junction lanes on {junction.get('id')}: {sorted(missing)}")

    def terminal_normals(edge_id: str, graph: dict) -> set[str]:
        pending, seen, found = list(graph[edge_id]), {edge_id}, set()
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            if key in normal:
                found.add(key)
            else:
                pending.extend(graph[key] - seen)
        return found

    generated = {}
    for edge_id, edge in edges.items():
        if edge_id in normal:
            continue
        crossing = set(edge.get("crossingEdges", "").split())
        if crossing - normal:
            raise ValueError(f"Dangling crossing road references on {edge_id}: {sorted(crossing - normal)}")
        incoming, outgoing = terminal_normals(edge_id, reverse), terminal_normals(edge_id, forward)
        parents = incoming | outgoing | crossing
        if not parents:
            raise ValueError(f"Generated SUMO edge has no source road closure: {edge_id}")
        generated[edge_id] = {
            "function": edge.get("function"), "incoming_normal_edge_ids": sorted(incoming),
            "outgoing_normal_edge_ids": sorted(outgoing), "crossing_normal_edge_ids": sorted(crossing),
            "source_way_ids": sorted({normal_sources[key] for key in parents}),
        }
    normal_records = {}
    for edge_id in sorted(normal):
        edge, way_id = edges[edge_id], normal_sources[edge_id]
        normal_records[edge_id] = {
            "source_way_id": way_id, "original_osm_way_id": source["roads"][way_id]["original_osm_way_id"],
            "direction": "forward" if edge_id.split("#", 1)[0] == way_id else "reverse",
            "from": edge.get("from"), "to": edge.get("to"), "type": edge.get("type"),
            "lanes": [{key: lane.get(key) for key in ("id", "index", "allow", "disallow", "width", "speed")}
                      for lane in edge.findall("lane")],
        }

    def parents(edge_id: str) -> set[str]:
        return {normal_sources[edge_id]} if edge_id in normal else set(generated[edge_id]["source_way_ids"])

    connection_records = []
    for index, connection in enumerate(connections):
        edge_ids = [connection.get("from"), connection.get("to")]
        if connection.get("via"):
            edge_ids.append(lanes[connection.get("via")])
        connection_records.append({
            "index": index, "attributes": dict(connection.attrib),
            "source_way_ids": sorted(set().union(*(parents(key) for key in edge_ids))),
        })
    way_records = {}
    for way_id, record in source["roads"].items():
        way_records[way_id] = {
            "original_osm_way_id": record["original_osm_way_id"],
            "normal_edge_ids": sorted(key for key in normal if normal_sources[key] == way_id),
            "generated_edge_ids": sorted(key for key, value in generated.items() if way_id in value["source_way_ids"]),
            "connection_indices": [value["index"] for value in connection_records if way_id in value["source_way_ids"]],
        }
    unrepresented = sorted(key for key, value in way_records.items() if not value["normal_edge_ids"])
    if unrepresented:
        raise ValueError(f"Retained ground ways have no normal SUMO edge: {unrepresented}")
    return {
        "schema_version": "aero-bench.ground-network-closure/v1", "policy": GROUND_ROAD_POLICY,
        "unsupported_road_highways": source["unsupported_road_highways"],
        "network_sha256": sha256(network), "filtered_osm_sha256": sha256(ground_source),
        "location": dict(location.attrib),
        "source_counts": source["source_counts"], "network_counts": network_counts(root),
        "same_xy_distinct_node_groups": source["same_xy_distinct_node_groups"],
        "node_identity_policy": "Source node IDs and coordinates retained; no coordinate-based merge or planar intersection inserted",
        "generated_parent_policy": "Reach normal edges through generated edges only; crossingEdges add crossed road provenance",
        "ways": way_records, "normal_edges": normal_records,
        "generated_edges": generated, "connections": connection_records,
        "unrepresented_ground_way_ids": unrepresented,
        "height_claim": source["height_claim"],
    }


def audit_retained_lane_rights(original: bytes, filtered: bytes, excluded_edge_ids: set[str]) -> dict:
    """Reject removal that changes surviving directional lanes or permissions."""
    before = {edge.get("id"): edge for edge in ET.fromstring(original).findall("edge")
              if edge.get("function", "normal") == "normal"}
    after = {edge.get("id"): edge for edge in ET.fromstring(filtered).findall("edge")
             if edge.get("function", "normal") == "normal"}
    if set(after) != set(before) - excluded_edge_ids:
        raise ValueError("Filtered network changed the expected retained normal edge set")
    checked_lanes = 0
    keys = ("index", "allow", "disallow", "width", "speed")
    for edge_id, edge in after.items():
        if any(before[edge_id].get(key) != edge.get(key) for key in ("from", "to")):
            raise ValueError(f"Ground removal changed retained junction node IDs: {edge_id}")
        old_lanes, new_lanes = before[edge_id].findall("lane"), edge.findall("lane")
        if len(old_lanes) != len(new_lanes):
            raise ValueError(f"Ground removal changed retained lane count: {edge_id}")
        for old, new in zip(old_lanes, new_lanes):
            if any(old.get(key) != new.get(key) for key in keys):
                raise ValueError(f"Ground removal changed retained lane rights/speed/width: {edge_id}/{new.get('index')}")
            checked_lanes += 1
    return {"retained_normal_edges": len(after), "retained_lanes": checked_lanes,
            "directional_edges_and_lane_rights_preserved": True,
            "fields": list(keys), "junction_node_ids_preserved": True,
            "geometry_rebuilt_by_netconvert": True}


def lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed, denied = lane.get("allow", "").split(), lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) \
        and vehicle_class not in denied and "all" not in denied


def audit_demand_routes(network: bytes, routes: bytes) -> dict:
    """Check explicit vehicle/person routes against real lanes and connections.

    This validates authored demand, not departure, arrival, collision freedom,
    or a SUMO run. Unsupported demand elements fail rather than disappear.
    """
    root = ET.fromstring(network)
    edges, lane_edges = _network_index(root)
    lanes = {lane.get("id"): lane for edge in edges.values() for lane in edge.findall("lane")}
    normal = {key for key, edge in edges.items() if edge.get("function", "normal") == "normal"}
    demand = ET.fromstring(routes)
    if demand.tag != "routes" or any(element.tag not in {"vType", "route", "vehicle", "person"} for element in demand):
        raise ValueError("Demand audit requires explicit vType, vehicle route, and person walk elements")
    types = {element.get("id"): element.get("vClass") for element in demand.findall("vType")}
    named_routes = {element.get("id"): element.get("edges", "").split() for element in demand.findall("route")}
    graphs = {}

    def graph_for(vehicle_class: str) -> dict:
        if vehicle_class in graphs:
            return graphs[vehicle_class]
        graph = defaultdict(set)
        for connection in root.findall("connection"):
            origin, target = connection.get("from"), connection.get("to")
            if origin not in edges or target not in edges:
                raise ValueError("Demand network contains a dangling connection")
            first = _connection_lane(edges[origin], connection.get("fromLane"))
            last = _connection_lane(edges[target], connection.get("toLane"))
            via = connection.get("via")
            if lane_allows(first, vehicle_class) and lane_allows(last, vehicle_class) \
                    and (not via or lane_allows(lanes[via], vehicle_class)):
                path = [origin, *([lane_edges[via]] if via else []), target]
                for first_edge, last_edge in zip(path, path[1:]):
                    graph[first_edge].add(last_edge)
        graphs[vehicle_class] = graph
        return graph

    def check_route(entity_id: str, route: list[str], vehicle_class: str) -> None:
        if not route:
            raise ValueError(f"Empty demand route: {entity_id}")
        for edge_id in route:
            if edge_id not in edges or not any(lane_allows(lane, vehicle_class) for lane in edges[edge_id].findall("lane")):
                raise ValueError(f"Illegal {vehicle_class} demand edge on {entity_id}: {edge_id}")
        graph = graph_for(vehicle_class)
        for origin, target in zip(route, route[1:]):
            pending, seen, connected = list(graph[origin]), {origin}, False
            while pending:
                key = pending.pop()
                if key == target:
                    connected = True
                    break
                if key in seen:
                    continue
                seen.add(key)
                if key not in normal:
                    pending.extend(graph[key] - seen)
            if not connected:
                raise ValueError(f"Disconnected {vehicle_class} demand route on {entity_id}: {origin}->{target}")

    vehicles, persons, classes, route_edges = 0, 0, defaultdict(int), set()
    for vehicle in demand.findall("vehicle"):
        vehicle_class = types.get(vehicle.get("type"))
        if not vehicle_class:
            raise ValueError(f"Demand vehicle has no declared vehicle class: {vehicle.get('id')}")
        inline = vehicle.find("route")
        route = inline.get("edges", "").split() if inline is not None else named_routes[vehicle.get("route")]
        check_route(vehicle.get("id"), route, vehicle_class)
        vehicles += 1
        classes[vehicle_class] += 1
        route_edges.update(route)
    for person in demand.findall("person"):
        if not list(person) or any(step.tag != "walk" or "edges" not in step.attrib for step in person):
            raise ValueError(f"Person demand requires explicit walk edges: {person.get('id')}")
        for walk in person:
            route = walk.get("edges").split()
            check_route(person.get("id"), route, "pedestrian")
            route_edges.update(route)
        persons += 1
    return {"schema_version": "aero-bench.ground-demand-legality/v1", "network_sha256": sha256(network),
            "routes_sha256": sha256(routes), "vehicles": vehicles, "persons": persons,
            "vehicle_classes": dict(sorted(classes.items())), "route_edge_count": len(route_edges),
            "route_edge_ids": sorted(route_edges), "lane_permissions_and_connections_valid": True,
            "traffic_recorded": False}


def json_bytes(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def prepare_ground_sources(source_osm: Path, network: Path, effective_osm_json: Path,
                           output_dir: Path, *, expected_source_osm_sha256: str,
                           expected_network_sha256: str, expected_effective_osm_sha256: str,
                           expected_filtered_osm_sha256: str, expected_projection: str) -> dict:
    """Materialize ground subsources only after an existing network passes audit.

    The original effective source remains the building GLB source. The receipt
    distinguishes its digest from each filtered road subsource and from SUMO.
    """
    paths = [Path(value).resolve() for value in (source_osm, network, effective_osm_json)]
    raw_osm, raw_network, raw_effective = [path.read_bytes() for path in paths]
    expected_pins = {
        "source_osm_sha256": expected_source_osm_sha256, "network_sha256": expected_network_sha256,
        "effective_osm_sha256": expected_effective_osm_sha256, "filtered_osm_sha256": expected_filtered_osm_sha256,
        "projection": expected_projection, "net_offset_required": "0,0",
    }
    for label, expected, raw in (
        ("source OSM", expected_source_osm_sha256, raw_osm),
        ("network", expected_network_sha256, raw_network),
        ("effective OSM", expected_effective_osm_sha256, raw_effective),
    ):
        if not isinstance(expected, str) or HEX.fullmatch(expected) is None or sha256(raw) != expected:
            raise ValueError(f"Ground preparation {label} digest differs from expected pin")
    selection = classify_roadways(raw_osm)
    excluded = set(selection["excluded_ways"])
    ground = filtered_osm_xml(raw_osm, excluded)
    if (not isinstance(expected_filtered_osm_sha256, str) or HEX.fullmatch(expected_filtered_osm_sha256) is None
            or sha256(ground) != expected_filtered_osm_sha256):
        raise ValueError("Ground preparation filtered OSM digest differs from expected pin")
    effective_ground = filtered_effective_json(raw_effective, raw_osm, excluded)
    proof = audit_ground_network(raw_network, ground, expected_projection=expected_projection)
    proof["expected_pins"] = expected_pins
    proof["excluded_ways"] = [selection["excluded_ways"][key] for key in sorted(excluded)]
    proof["source_osm_sha256"] = sha256(raw_osm)
    proof["original_effective_osm_sha256"] = sha256(raw_effective)
    proof["filtered_effective_osm_sha256"] = sha256(effective_ground)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Ground source output already exists: {output}")
    output.mkdir(parents=True)
    artifacts = {"ground.osm": ground, "road-presentation.osm.json": effective_ground,
                 "network.net.xml": raw_network, "ground-network-proof.json": json_bytes(proof)}
    for name, raw in artifacts.items():
        (output / name).write_bytes(raw)
    original = json.loads(raw_effective)
    filtered = json.loads(effective_ground)
    unchanged = [element for element in original["elements"]
                 if not (element.get("type") == "way" and element.get("tags", {}).get("highway")
                         and str(element["id"]) in excluded)]
    if filtered["elements"] != unchanged:
        raise ValueError("Road subsource changed a retained source element")
    receipt = {
        "schema_version": "aero-bench.ground-road-sources/v2", "policy": GROUND_ROAD_POLICY,
        "unsupported_road_highways": selection["unsupported_road_highways"],
        "state": "source_subsets_prepared_existing_network_verified",
        "expected_pins": expected_pins,
        "inputs": {name: {"path": str(path), "sha256": sha256(raw), "size_bytes": len(raw)}
                   for name, path, raw in zip(("sumo_road_source", "sumo_network", "building_effective_source"),
                                              paths, (raw_osm, raw_network, raw_effective))},
        "outputs": {name: {"sha256": sha256(raw), "size_bytes": len(raw)} for name, raw in artifacts.items()},
        "source_before": selection["source_counts"], "source_after": proof["source_counts"],
        "excluded_way_count": len(excluded), "excluded_way_ids": sorted(excluded),
        "network": {"sha256": sha256(raw_network), "regenerated": False, "traffic_recorded": False,
                    "normal_edges": len(proof["normal_edges"]), "generated_edges": len(proof["generated_edges"]),
                    "connections": len(proof["connections"])},
        "retained_effective_elements_unchanged": True,
        "original_building_source_pin_unchanged": sha256(raw_effective),
        "road_height_claim": selection["height_claim"],
    }
    for path, raw in zip(paths, (raw_osm, raw_network, raw_effective)):
        if path.read_bytes() != raw:
            raise ValueError(f"Source changed during ground preparation: {path}")
    (output / "ground-source-receipt.json").write_bytes(json_bytes(receipt))
    return receipt
