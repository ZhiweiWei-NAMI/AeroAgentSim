"""Physical gate for a materialized native SUMO network against actual rendered buildings.

Reports lane/building contacts, undrawable generated walking areas and collapsed native
lanes using the reviewed clearance classifier, plus per-class connectivity. The network
is audited as materialized; nothing is repaired, padded or clipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from city_preview_coordinates import CityEnuProjection, load_canonical_city_geometry
from city_road_physical_clearance import LANE_CENTRE_LINE_DECIMALS, lane_building_conflicts
from city_road_topology import lane_kind
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

ROAD_END_TOLERANCE_M = 0.05
from report_ground_road_scope import network_connectivity

PUBLIC = Path(__file__).resolve().parents[1] / "public"
REPO = Path(__file__).resolve().parents[2]
DEFAULT_OBJECTS = REPO / "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/objects.json"
DEFAULT_RENDER = PUBLIC / "building-renders/shanghai-huangpu-east-v1/manifest.json"
DEFAULT_PACK = PUBLIC / "osm2world/packs/shanghai-huangpu-east-v1/manifest.json"
DEFAULT_COMMON_SCENE = REPO / "validation/scene-compiler-shanghai-huangpu-east-v1/metadata/common-scene.json"
# netconvert's default 0.01 m output quantizes each lane shape independently, so abutting
# sidewalk and vehicle ribbons overlap by up to about 1 cm; 0.1 mm keeps them within the 3 mm seam.
NETCONVERT_PRECISION_OPTIONS = ("--precision", str(LANE_CENTRE_LINE_DECIMALS))


def identity(path: Path) -> dict:
    raw = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}


def native_lanes(network: ET.Element, projection: CityEnuProjection) -> list[dict]:
    lanes = []
    for edge in network.findall("edge"):
        function = edge.get("function", "normal")
        for lane in edge.findall("lane"):
            lanes.append({"id": lane.get("id"), "width": float(lane.get("width", "3.2")), "function": function,
                          "kind": "walk" if function in {"walkingarea", "crossing"} else lane_kind(lane),
                          "shape": projection.shape(lane.get("shape"))})
    return lanes


def _lane_end_section(lane: ET.Element, edge: ET.Element, junction: str, projection: CityEnuProjection) -> LineString:
    """Cross-section of a lane where it meets `junction`, spanning the lane width."""
    points = projection.shape(lane.get("shape"))
    (x0, y0), (x1, y1) = (points[-2], points[-1]) if edge.get("to") == junction else (points[1], points[0])
    length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** .5
    nx, ny, half = -(y1 - y0) / length, (x1 - x0) / length, float(lane.get("width", "3.2")) / 2
    return LineString([(x1 - nx * half, y1 - ny * half), (x1 + nx * half, y1 + ny * half)])


def road_end_carriageway_crossings(network: ET.Element, projection: CityEnuProjection, degenerate: list[dict]) -> set[str]:
    """Degenerate walking areas that model pedestrians crossing a single road's carriageway end.

    Accepted only when the junction serves one road (an edge and its reverse), the walking
    area connects only that road's pedestrian lanes, and the gap between the two sidewalk end
    sections is covered by the same road's carriageway lane end sections.
    """
    edges = {edge.get("id"): edge for edge in network.findall("edge")}
    connections = [element.attrib for element in network.findall("connection")]
    accepted = set()
    for item in degenerate:
        walk_edge = item["lane_id"].rsplit("_", 1)[0]
        junction = walk_edge[1:].rsplit("_w", 1)[0]
        incident = [edge for edge in edges.values() if edge.get("function", "normal") == "normal"
                    and junction in (edge.get("from"), edge.get("to"))]
        if len({edge.get("id").lstrip("-") for edge in incident}) != 1 or len(incident) != 2:
            continue
        linked = {c["from"] for c in connections if c["to"] == walk_edge} | {c["to"] for c in connections if c["from"] == walk_edge}
        if not linked or not linked <= {edge.get("id") for edge in incident}:
            continue
        walk_sections, sections = [], []
        for edge in incident:
            for lane in edge.findall("lane"):
                section = _lane_end_section(lane, edge, junction, projection)
                sections.append(section)
                if "pedestrian" in (lane.get("allow") or "").split():
                    walk_sections.append(section)
        # One pedestrian-permitting lane per direction: a sidewalk pair or a shared carriageway.
        if len(walk_sections) != 2:
            continue
        a, b = (section.centroid for section in walk_sections)
        gap = LineString([a, b])
        covered = unary_union([section.buffer(ROAD_END_TOLERANCE_M) for section in sections])
        if gap.difference(covered).length <= ROAD_END_TOLERANCE_M:
            accepted.add(item["lane_id"])
    return accepted


def straight_abutments(network: ET.Element, projection: CityEnuProjection, collapsed: list[dict]) -> set[str]:
    """Collapsed internal lanes that join two equal-width lanes end to end on a straight movement."""
    lanes = {lane.get("id"): lane for edge in network.findall("edge") for lane in edge.findall("lane")}
    by_via = {element.get("via"): element.attrib for element in network.findall("connection") if element.get("via")}
    accepted = set()
    for item in collapsed:
        connection = by_via.get(item["lane_id"])
        if connection is None or connection.get("dir") != "s":
            continue
        incoming = lanes.get(f'{connection["from"]}_{connection["fromLane"]}')
        outgoing = lanes.get(f'{connection["to"]}_{connection["toLane"]}')
        if incoming is None or outgoing is None or incoming.get("width") != outgoing.get("width"):
            continue
        end, start = projection.shape(incoming.get("shape"))[-1], projection.shape(outgoing.get("shape"))[0]
        if ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** .5 <= 0.01:
            accepted.add(item["lane_id"])
    return accepted


def audit(network_path: Path, source_path: Path, objects: Path, render: Path, pack: Path) -> dict:
    geometry = load_canonical_city_geometry(objects, render, pack)
    network_bytes = network_path.read_bytes()
    network, source = ET.fromstring(network_bytes), ET.parse(source_path).getroot()
    projection = CityEnuProjection(network, geometry.origin)
    proof = lane_building_conflicts(network, source, native_lanes(network, projection), geometry.footprints, projection)
    edges = {edge.get("id"): edge.get("function", "normal") for edge in network.findall("edge")}
    contact_functions = Counter(edges[row["lane_id"].rsplit("_", 1)[0]] for row in proof["rows"])
    crossings = road_end_carriageway_crossings(network, projection, proof["unrenderable_generated_areas"])
    required = [item for item in proof["unrenderable_generated_areas"] if item["lane_id"] not in crossings]
    abutments = straight_abutments(network, projection, proof["unrenderable_native_lanes"])
    collapsed = [item for item in proof["unrenderable_native_lanes"] if item["lane_id"] not in abutments]
    return {
        "schema_version": "aero-bench.native-road-physical-audit/v1",
        "building_basis": "ACTUAL_BYTE_VERIFIED_RENDERED_GLB_OCCUPIED_FOOTPRINTS",
        "inputs": {"network": identity(network_path), "source_osm": identity(source_path),
                   "objects": identity(objects), "render": identity(render), "pack": identity(pack)},
        # Publication still consumes the strict lane_building_conflicts contract.
        # End crossings and abutments are diagnostic candidates, not renderable assets.
        "gate": {"status": proof["status"],
                 "contact_pairs": proof["pair_count"],
                 "contact_area_m2": proof["contact_area_sum_per_lane_m2"],
                 "contact_pairs_by_function": dict(contact_functions),
                 "undrawable_walking_areas": len(proof["unrenderable_generated_areas"]),
                 "road_end_carriageway_crossings": len(crossings),
                 "undrawable_required_walking_areas": len(required),
                 "collapsed_native_lanes": len(proof["unrenderable_native_lanes"]),
                 "straight_abutments": len(abutments), "collapsed_turn_lanes": len(collapsed)},
        "candidate_geometry_gate": {
            "status": "BLOCKED" if proof["rows"] or required or collapsed else "PASS",
            "acceptance": "diagnostic-only; requires road producer coverage and renderability proof",
        },
        "native_lane_clearance": proof,
        "road_end_carriageway_crossings": sorted(crossings),
        "undrawable_required_walking_areas": required,
        "straight_abutments": sorted(abutments), "collapsed_turn_lanes": collapsed,
        "connectivity": network_connectivity(network_bytes),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--source-osm", type=Path, required=True)
    parser.add_argument("--objects", type=Path, default=DEFAULT_OBJECTS)
    parser.add_argument("--render", type=Path, default=DEFAULT_RENDER)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.network, args.source_osm, args.objects, args.render, args.pack)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "network": result["inputs"]["network"]["sha256"][:12], **result["gate"],
                      "connected_share": {k: v["boundary_connected_share"] for k, v in result["connectivity"].items()},
                      "interior_dead_ends": {k: v["interior_dead_end_edges"] for k, v in result["connectivity"].items()}}))


if __name__ == "__main__":
    main()
