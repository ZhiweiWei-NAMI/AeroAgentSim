#!/usr/bin/env python3
"""Remove physically infeasible movements from a materialized ground network.

Each iteration audits the native network against actual rendered buildings, then:
- deletes turnaround movements (dir="t") whose native turning lane enters a building, or
  overlaps a pedestrian lane of its junction deeper than the road-walk surface seam;
- deletes other contacting movements, and removes contacting normal edges, only when
  crop-aware connectivity for passenger, bicycle and pedestrian does not get worse;
- removes a pedestrian-only edge whose walking area enters a building, when connectivity-neutral;
- keeps every other contact as an unresolved defect.
Deletions are applied by real netconvert through a connection file and an edge-removal
list; nothing is clipped, padded or relaxed. The loop stops when nothing changes.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import audit_ground_network
from audit_native_road_physical import DEFAULT_OBJECTS, DEFAULT_PACK, DEFAULT_RENDER, NETCONVERT_PRECISION_OPTIONS, audit
from city_ground_junction_completion import turnarounds_over_walkways
from report_ground_road_scope import CLASSES, network_connectivity

SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"
MAX_ITERATIONS = 8


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _not_worse(after: dict, before: dict) -> bool:
    # Removing usable edges can improve a percentage while losing reachable roads.
    # Absolute reachability also handles classes with no usable edges (share=None).
    return all(after[name]["boundary_connected_edges"] >= before[name]["boundary_connected_edges"]
               and after[name]["interior_dead_end_edges"] <= before[name]["interior_dead_end_edges"]
               for name in CLASSES)


def _without(network: ET.Element, *, connection: dict | None = None, edge: str | None = None) -> bytes:
    root = copy.deepcopy(network)
    for element in list(root):
        if element.tag == "connection":
            if connection is not None and all(element.get(key) == connection[key]
                                              for key in ("from", "to", "fromLane", "toLane")):
                root.remove(element)
            elif edge is not None and edge in (element.get("from"), element.get("to")):
                root.remove(element)
        elif element.tag == "edge" and edge is not None and element.get("id") == edge:
            root.remove(element)
    return ET.tostring(root)


def _netconvert(source: Path, output: Path, connections: Path, removals: Path) -> list[str]:
    command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,nosuid,size=256m",
               "--mount", f"type=bind,source={source.parent},target=/input,readonly",
               "--mount", f"type=bind,source={output.parent},target=/output",
               "--entrypoint", "netconvert", SUMO_IMAGE,
               "--sumo-net-file", f"/input/{source.name}", "--connection-files", f"/output/{connections.name}",
               "--offset.disable-normalization", "--walkingareas", "true", *NETCONVERT_PRECISION_OPTIONS,
               "--output-file", f"/output/{output.name}"]
    if removals.read_text().strip():
        command += ["--remove-edges.input-file", f"/output/{removals.name}"]
    with (output.parent / "netconvert.log").open("a") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    return command


def ground_scope_exclusions(authored: dict, authored_dir: Path, output: Path, network: ET.Element) -> dict:
    """Edges excluded from the ground scope along the chain: the strict ground filter upstream of the
    authored source network, the authored exclusions and the edges this refinement removed."""
    source_dir = Path(authored["source_network"]).parent
    upstream = json.loads((source_dir / "engineering-inputs.json").read_text())["source_ground_filter_report"]
    upstream_path = source_dir / upstream["path"]
    if _sha(upstream_path) != upstream["sha256"]:
        raise ValueError("Upstream ground filter report differs from its engineering receipt")
    groups = {"strict_ground_filter": json.loads(upstream_path.read_text())["removed_edge_ids"],
              "authored_exclusions": (authored_dir / "excluded-edge-ids.txt").read_text().split(),
              "physical_refinement": (output / "physical-removed-edges.txt").read_text().split()}
    removed = sorted(set().union(*groups.values()))
    present = sorted(set(removed) & {edge.get("id") for edge in network.findall("edge")})
    if present:
        raise ValueError(f"Refined ground network still contains excluded edges: {present}")
    return {"schema_version": "aero-bench.ground-scope-exclusions/v1", "road_scope": "ground-only",
            "upstream_ground_filter_report": {"path": str(upstream_path), "sha256": upstream["sha256"]},
            "removed_edge_ids_by_stage": {name: sorted(ids) for name, ids in groups.items()},
            "removed_edge_ids": removed}


def _write_engineering_receipt(network_path: Path, source_osm: Path, final: Path, output: Path) -> None:
    """Bind the refined network to its authored receipt, closure proof and refinement proof."""
    authored = json.loads((network_path.parent / "engineering-inputs.json").read_text())
    if authored.get("network_sha256") != _sha(network_path) or authored.get("source_osm_sha256") != _sha(source_osm):
        raise ValueError("Refinement input differs from its authored engineering receipt")
    # Ways whose every native edge the refinement removed leave the source, as authored exclusions do.
    network = ET.parse(final).getroot()
    edge_ways = {edge.get("id").split("#", 1)[0] for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
    source = ET.parse(source_osm).getroot()
    removed_ways = []
    for way in list(source.findall("way")):
        if way.get("id") not in edge_ways and "-" + way.get("id") not in edge_ways:
            removed_ways.append(way.get("id"))
            source.remove(way)
    refined_osm = output / "refined-ground.osm"
    ET.ElementTree(source).write(refined_osm, encoding="utf-8", xml_declaration=True)
    closure = audit_ground_network(final.read_bytes(), refined_osm.read_bytes(), expected_projection=authored["projection"])
    (output / "ground-network-proof.json").write_text(json.dumps(closure, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    exclusions = output / "ground-filter-report.json"
    exclusions.write_text(json.dumps(ground_scope_exclusions(authored, network_path.parent, output, network),
                                     ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    receipt = {**authored, "network_sha256": _sha(final),
               "source_osm": str(refined_osm), "source_osm_sha256": _sha(refined_osm),
               "filtered_source_osm": refined_osm.name, "filtered_source_osm_sha256": _sha(refined_osm),
               "authored_source_osm": str(source_osm), "authored_source_osm_sha256": _sha(source_osm),
               "refinement_removed_source_ways": removed_ways,
               "network_generation": authored["network_generation"] + "; physical movement refinement by real netconvert",
               "authored_network": str(network_path), "authored_network_sha256": _sha(network_path),
               "authored_engineering_inputs_sha256": _sha(network_path.parent / "engineering-inputs.json"),
               "network_closure": "ground-network-proof.json",
               "ground_filter_report": exclusions.name, "ground_filter_report_sha256": _sha(exclusions),
               "physical_refinement": "physical-refinement-proof.json",
               "physical_refinement_sha256": _sha(output / "physical-refinement-proof.json"),
               "final_physical_audit_sha256": _sha(output / "final-physical-audit.json")}
    (output / "engineering-inputs.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def refine(network_path: Path, source_osm: Path, output: Path, objects: Path, render: Path, pack: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    if (output / "network.net.xml").exists():
        raise FileExistsError(output / "network.net.xml")
    deleted_connections: list[dict] = []
    removed_edges: list[dict] = []
    iterations = []
    current = network_path
    for iteration in range(MAX_ITERATIONS):
        result = audit(current, source_osm, objects, render, pack)
        network = ET.parse(current).getroot()
        via = {element.get("via"): dict(element.attrib) for element in network.findall("connection") if element.get("via")}
        baseline = network_connectivity(ET.tostring(network))
        proposed = network
        normal_edges = {edge.get("id"): edge for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
        new_connections, new_edges, unresolved = [], [], []
        for row in result["native_lane_clearance"]["rows"]:
            lane_id, function = row["lane_id"], row["function"]
            if function == "internal" and lane_id in via:
                movement = {key: via[lane_id][key] for key in ("from", "to", "fromLane", "toLane")}
                if movement in [item["movement"] for item in deleted_connections + new_connections]:
                    continue
                if via[lane_id].get("dir") == "t":
                    new_connections.append({"movement": movement, "lane_id": lane_id, "area_m2": row["area_m2"],
                        "building_id": row["building_id"], "rule": "turnaround loop enters a rendered building"})
                    proposed = ET.fromstring(_without(proposed, connection=movement))
                    continue
                trial = _without(proposed, connection=movement)
                after = network_connectivity(trial)
                if _not_worse(after, baseline):
                    new_connections.append({"movement": movement, "lane_id": lane_id, "area_m2": row["area_m2"],
                        "building_id": row["building_id"], "rule": "turning lane enters a rendered building; removal is connectivity-neutral"})
                    proposed = ET.fromstring(trial)
                    continue
            elif function == "normal":
                edge = row["edge_id"]
                if edge in [item["edge_id"] for item in removed_edges + new_edges]:
                    continue
                trial = _without(proposed, edge=edge)
                after = network_connectivity(trial)
                if _not_worse(after, baseline):
                    new_edges.append({"edge_id": edge, "lane_id": lane_id, "area_m2": row["area_m2"],
                        "building_id": row["building_id"], "classification": row["classification"],
                        "rule": "native lane enters a rendered building; removal is connectivity-neutral"})
                    proposed = ET.fromstring(trial)
                    continue
            elif function == "walkingarea":
                walk_edge = lane_id.rsplit("_", 1)[0]
                incident = sorted({element.get(side) for element in network.findall("connection")
                                   for side, other in (("from", "to"), ("to", "from")) if element.get(other) == walk_edge})
                pedestrian_only = [edge for edge in incident if edge in normal_edges
                                   and all(lane.get("allow") == "pedestrian" for lane in normal_edges[edge].findall("lane"))]
                chosen = None
                for edge in sorted(pedestrian_only, key=lambda key: float(normal_edges[key].find("lane").get("length", "0"))):
                    if edge in [item["edge_id"] for item in removed_edges + new_edges]:
                        continue
                    trial = _without(proposed, edge=edge)
                    if _not_worse(network_connectivity(trial), baseline):
                        chosen = edge
                        proposed = ET.fromstring(trial)
                        break
                if chosen is not None:
                    new_edges.append({"edge_id": chosen, "lane_id": lane_id, "area_m2": row["area_m2"],
                        "building_id": row["building_id"], "classification": row["classification"],
                        "rule": "walking area of a pedestrian-only edge enters a rendered building; removal is connectivity-neutral"})
                    continue
            unresolved.append({"lane_id": lane_id, "function": function, "area_m2": row["area_m2"],
                               "building_id": row["building_id"], "classification": row["classification"]})
        for record in turnarounds_over_walkways(network):
            if record["movement"] in [item["movement"] for item in deleted_connections + new_connections]:
                continue
            new_connections.append({"movement": record["movement"], "lane_id": record["internal_lane"],
                "crossed_walkways": record["crossed_walkways"], "rule": "turnaround loop overlaps a walkway"})
            proposed = ET.fromstring(_without(proposed, connection=record["movement"]))
        iterations.append({"iteration": iteration, "network_sha256": _sha(current), "gate": result["gate"],
                           "connectivity": {name: {key: baseline[name][key] for key in ("boundary_connected_share", "interior_dead_end_edges")}
                                            for name in CLASSES},
                           "deleted_movements": len(new_connections), "removed_edges": len(new_edges), "unresolved_contacts": unresolved})
        if not new_connections and not new_edges:
            break
        deleted_connections += new_connections
        removed_edges += new_edges
        connections = ET.Element("connections")
        removed_ids = {item["edge_id"] for item in removed_edges}
        for item in deleted_connections:
            # Movements on removed edges disappear with the edge.
            if item["movement"]["from"] in removed_ids or item["movement"]["to"] in removed_ids:
                continue
            ET.SubElement(connections, "delete", **item["movement"])
        ET.indent(connections, space="  ")
        connection_file = output / "physical-deletions.con.xml"
        ET.ElementTree(connections).write(connection_file, encoding="utf-8", xml_declaration=True)
        removal_file = output / "physical-removed-edges.txt"
        removal_file.write_text("".join(item["edge_id"] + "\n" for item in removed_edges))
        target = output / f"network-iteration-{iteration + 1}.net.xml"
        _netconvert(network_path, target, connection_file, removal_file)
        current = target
    final = output / "network.net.xml"
    final.write_bytes(current.read_bytes())
    final_audit = audit(final, source_osm, objects, render, pack)
    proof = {"schema_version": "aero-bench.ground-network-physical-refinement/v1",
             "input_network_sha256": _sha(network_path), "source_osm_sha256": _sha(source_osm),
             "output_network_sha256": _sha(final), "sumo_image_id": SUMO_IMAGE,
             "rules": __doc__.strip().splitlines()[2:9],
             "deleted_movements": deleted_connections, "removed_edges": removed_edges,
             "iterations": iterations, "final_gate": final_audit["gate"],
             "final_connectivity": final_audit["connectivity"]}
    (output / "physical-refinement-proof.json").write_text(json.dumps(proof, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    (output / "final-physical-audit.json").write_text(json.dumps(final_audit, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    _write_engineering_receipt(network_path, source_osm, final, output)
    return {"output_network_sha256": proof["output_network_sha256"], "deleted_movements": len(deleted_connections),
            "removed_edges": len(removed_edges), "final_gate": final_audit["gate"],
            "connected_share": {name: final_audit["connectivity"][name]["boundary_connected_share"] for name in CLASSES},
            "interior_dead_ends": {name: final_audit["connectivity"][name]["interior_dead_end_edges"] for name in CLASSES}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--source-osm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--objects", type=Path, default=DEFAULT_OBJECTS)
    parser.add_argument("--render", type=Path, default=DEFAULT_RENDER)
    parser.add_argument("--pack", type=Path, default=DEFAULT_PACK)
    args = parser.parse_args()
    print(json.dumps(refine(args.network.resolve(), args.source_osm.resolve(), args.output.resolve(),
                            args.objects, args.render, args.pack), ensure_ascii=False))


if __name__ == "__main__":
    main()
