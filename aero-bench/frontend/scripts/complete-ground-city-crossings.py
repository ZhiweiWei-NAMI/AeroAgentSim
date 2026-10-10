#!/usr/bin/env python3
"""Rebuild ground-demo crossings in SUMO and verify the resulting pedestrian links."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from city_crossing_groups import crossing_replacements, write_patches
from city_road_topology import crossing_junction_id

SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crossings(root: ET.Element) -> dict:
    result = {}
    for edge in root.findall("edge"):
        if edge.get("function") != "crossing":
            continue
        key = (crossing_junction_id(edge.attrib["id"]), frozenset(edge.attrib["crossingEdges"].split()))
        if key in result:
            raise ValueError(f"Duplicate pedestrian crossing group: {key}")
        result[key] = edge
    return result


def validate_crossings(before: ET.Element, after: ET.Element, replacements) -> list[dict]:
    source, rebuilt = crossings(before), crossings(after)
    replaced = {(item.junction_id, frozenset(item.previous_edges)) for item in replacements}
    expected = set(source) - replaced
    expected.update((item.junction_id, frozenset(item.replacement_edges)) for item in replacements)
    if expected != set(rebuilt):
        raise ValueError(f"Crossing groups changed unexpectedly: missing={expected - set(rebuilt)}, added={set(rebuilt) - expected}")
    edges = {edge.attrib["id"]: edge for edge in after.findall("edge")}
    programs = {}
    for program in after.findall("tlLogic"):
        programs.setdefault(program.attrib["id"], []).append(program)
    connections = after.findall("connection")
    for connection in connections:
        if connection.attrib["from"] not in edges or connection.attrib["to"] not in edges:
            raise ValueError("SUMO connection references a missing edge")
        if connection.get("tl"):
            tl, index = connection.attrib["tl"], int(connection.attrib["linkIndex"])
            if tl not in programs or any(index >= len(phase.attrib["state"])
                    for program in programs[tl] for phase in program.findall("phase")):
                raise ValueError("SUMO traffic-light phase misses a connection index")
    checks = []
    for item in replacements:
        edge = rebuilt[(item.junction_id, frozenset(item.replacement_edges))]
        incoming = [c for c in connections if c.get("to") == edge.attrib["id"]]
        outgoing = [c for c in connections if c.get("from") == edge.attrib["id"]]
        if not incoming or not outgoing or any(edges[c.attrib["from"]].get("function") != "walkingarea" for c in incoming) \
                or any(edges[c.attrib["to"]].get("function") != "walkingarea" for c in outgoing):
            raise ValueError(f"Expanded crossing has no connected walking areas: {edge.attrib['id']}")
        controlled = [c for c in incoming if c.get("tl")]
        if not controlled:
            raise ValueError(f"Expanded crossing has no pedestrian signal: {edge.attrib['id']}")
        # Crossings over an approach precede its vehicle stop line. Both may
        # not receive a protected green simultaneously. Permissive 'g' yields.
        for ped in controlled:
            tl, ped_index = ped.attrib["tl"], int(ped.attrib["linkIndex"])
            for program in programs[tl]:
                if not any(phase.attrib["state"][ped_index] in "gG" for phase in program.findall("phase")):
                    raise ValueError(f"Expanded crossing never receives green: {edge.attrib['id']}")
                for phase in program.findall("phase"):
                    state = phase.attrib["state"]
                    if state[ped_index] not in "gG":
                        continue
                    for vehicle in connections:
                        if vehicle.get("tl") == tl and (vehicle.get("from") in item.replacement_edges
                                or vehicle.get("to") in item.replacement_edges) \
                                and state[int(vehicle.attrib["linkIndex"])] == "G":
                            raise ValueError(f"Protected vehicle green conflicts with crossing: {edge.attrib['id']}")
        checks.append({"junction_id": item.junction_id, "crossing_id": edge.attrib["id"],
                       "previous_edges": list(item.previous_edges), "crossed_edges": list(item.replacement_edges),
                       "before_length_m": float(source[(item.junction_id, frozenset(item.previous_edges))].find("lane").attrib["length"]),
                       "after_length_m": float(edge.find("lane").attrib["length"]),
                       "walking_area_in": [c.attrib["from"] for c in incoming],
                       "walking_area_out": [c.attrib["to"] for c in outgoing],
                       "pedestrian_signal_links": [int(c.attrib["linkIndex"]) for c in controlled]})
    return checks


def build(network: Path, output: Path) -> dict:
    network, output = network.resolve(), output.resolve()
    engineering = json.loads((network.parent / "engineering-inputs.json").read_text())
    if engineering.get("road_scope") != "ground-only":
        raise ValueError("Crossing completion requires a filtered ground-only network")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "network.net.xml"
    if target.exists():
        raise FileExistsError(target)
    before = ET.parse(network).getroot()
    replacements = crossing_replacements(before)
    _, discarded_count, replacement_count = write_patches(str(network), str(output / "roads.edg.xml"),
                                                          str(output / "crossings.con.xml"))
    sidewalk_edges = [edge.attrib["id"] for edge in ET.parse(output / "roads.edg.xml").getroot().findall("edge")]
    command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
               "--tmpfs", "/tmp:rw,nosuid,size=256m",
               "--mount", f"type=bind,source={network.parent},target=/input,readonly",
               "--mount", f"type=bind,source={output},target=/output",
               "--entrypoint", "netconvert", SUMO_IMAGE,
               "--sumo-net-file", f"/input/{network.name}", "--edge-files", "/output/roads.edg.xml",
               "--connection-files", "/output/crossings.con.xml", "--offset.disable-normalization",
               "--output-file", "/output/network.net.xml"]
    with (output / "netconvert.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    after = ET.parse(target).getroot()
    normal_edges = lambda root: {e.attrib["id"] for e in root.findall("edge") if e.get("function", "normal") == "normal"}
    if normal_edges(before) != normal_edges(after):
        raise ValueError("Crossing rebuild changed the ground road edge inventory")
    for key in ("netOffset", "projParameter"):
        if before.find("location").attrib[key] != after.find("location").attrib[key]:
            raise ValueError(f"Crossing rebuild changed {key}")
    checks = validate_crossings(before, after, replacements)
    ground_report = json.loads((network.parent / engineering["ground_filter_report"]).read_text())
    ground_report["network_after_filter"] = ground_report["network_after"]
    ground_report["network_after"] = {
        "all_edges": len(after.findall("edge")), "normal_edges": len(normal_edges(after)),
        "internal_edges": sum(e.get("function") == "internal" for e in after.findall("edge")),
        "other_generated_edges": sum(e.get("function", "normal") not in ("normal", "internal") for e in after.findall("edge")),
        "connections": len(after.findall("connection")), "crossings": len(crossings(after)),
        "traffic_light_programs": len(after.findall("tlLogic"))}
    ground_report["output"]["network_sha256"] = digest(target)
    ground_report["crossing_completion_report"] = "crossing-completion-report.json"
    (output / "ground-filter-report.json").write_text(json.dumps(ground_report, ensure_ascii=False, indent=2) + "\n")
    engineering.update({"ground_filter_report": "ground-filter-report.json", "network_sha256": digest(target),
                        "crossing_completion_report": "crossing-completion-report.json"})
    (output / "engineering-inputs.json").write_text(json.dumps(engineering, ensure_ascii=False, indent=2) + "\n")
    report = {"source_kind": "derived-ground-demo-crossings-not-surveyed", "network_sha256": digest(target),
              "base_network_sha256": digest(network), "image": SUMO_IMAGE, "command": command,
              "sidewalk_edge_count": len(sidewalk_edges), "sidewalk_edge_ids": sidewalk_edges,
              "replacement_count": replacement_count, "discarded_crossing_count": discarded_count,
              "checks": checks, "crossing_count_before": len(crossings(before)),
              "crossing_count_after": len(crossings(after)),
              "edge_patch_sha256": digest(output / "roads.edg.xml"),
              "crossing_patch_sha256": digest(output / "crossings.con.xml")}
    (output / "crossing-completion-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.network, args.output_dir), ensure_ascii=False, indent=2))
