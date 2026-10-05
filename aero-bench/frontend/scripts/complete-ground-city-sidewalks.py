#!/usr/bin/env python3
"""Materialize a source-bound engineering sidewalk layout with real netconvert."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import audit_ground_network
from city_pedestrian_sidewalks import (SOURCE_KIND, audit_completed_crossings,
    audit_motor_lanes, plan_crossing_sidewalks)

SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def completion_engineering(engineering: dict, network_path: Path, source_path: Path,
                           output: Path) -> dict:
    """Bind this stage while retaining the preceding filter report as history."""
    engineering = dict(engineering)
    preceding_report = network_path.parent / engineering.pop("ground_filter_report")
    shutil.copyfile(preceding_report, output / "source-ground-filter-report.json")
    engineering.update({
        "source_ground_filter_report": {"path": "source-ground-filter-report.json",
            "sha256": sha(preceding_report), "network_sha256": sha(network_path),
            "scope": "preceding-ground-filter-stage; not the completed sidewalk network"},
        "source_ground_removed_edge_count": engineering.pop("removed_edge_count"),
        "source_network": str(network_path), "source_network_sha256": sha(network_path),
        "original_source_osm": engineering["source_osm"],
        "original_source_osm_sha256": engineering["source_osm_sha256"],
        "sidewalk_input_ground_osm_sha256": sha(source_path),
        "source_osm": str(output / "derived-ground.osm"),
        "source_osm_sha256": sha(output / "derived-ground.osm"),
        "filtered_source_osm": "derived-ground.osm",
        "filtered_source_osm_sha256": sha(output / "derived-ground.osm"),
        "road_presentation_source": "road-presentation.osm.json",
        "road_presentation_source_sha256": sha(output / "road-presentation.osm.json"),
        "network_sha256": sha(output / "network.net.xml"),
        "network_closure": "ground-network-proof.json",
        "sidewalk_completion_report": "sidewalk-completion-report.json",
        "network_generation": "native netconvert scoped source-crossing engineering sidewalks; original motor lane rights preserved"})
    return engineering


def derived_sources(source: ET.Element, road_json: dict, plan: dict) -> tuple[ET.Element, dict]:
    """Append explicitly derived metadata; never rewrite original sidewalk tags."""
    source = ET.fromstring(ET.tostring(source))
    road_json = json.loads(json.dumps(road_json))
    by_way = {}
    for record in plan["derived_edges"]:
        by_way.setdefault(record["source_way_id"], []).append(record)
    ways = {way.attrib["id"]: way for way in source.findall("way")}
    json_ways = {str(way["id"]): way for way in road_json["elements"] if way["type"] == "way"}
    for way_id, records in by_way.items():
        metadata = {"aero_bench:derived_sidewalk_profile": plan["profile"],
            "aero_bench:derived_sidewalk_width_m": str(plan["sidewalk_width_m"]),
            "aero_bench:derived_sidewalk_edge_ids": " ".join(sorted(record["edge_id"] for record in records)),
            "aero_bench:derived_sidewalk_surveyed": "no"}
        observed = {tag.attrib["k"]: tag.attrib["v"] for tag in ways[way_id].findall("tag")}
        if any(json_ways[way_id].get("tags", {}).get(key) != value for key, value in observed.items()):
            raise ValueError(f"Road JSON and ground OSM source tags differ: {way_id}")
        if metadata.keys() & observed.keys():
            raise ValueError(f"Sidewalk input already contains derived policy tags: {way_id}")
        for key, value in sorted(metadata.items()):
            ET.SubElement(ways[way_id], "tag", k=key, v=value)
        json_ways[way_id].setdefault("tags", {}).update(metadata)
    road_json["derived_pedestrian_lane_layout"] = plan
    return source, road_json


def build(network_path: Path, source_path: Path, road_source_path: Path,
          expected_projection: str, output: Path) -> dict:
    network_path, source_path, road_source_path, output = [path.resolve() for path in
        (network_path, source_path, road_source_path, output)]
    source_proof = audit_ground_network(network_path.read_bytes(), source_path.read_bytes(),
                                       expected_projection=expected_projection)
    before = ET.parse(network_path).getroot()
    source = ET.parse(source_path).getroot()
    patch, plan = plan_crossing_sidewalks(before, source)
    derived_xml, derived_road = derived_sources(source, json.loads(road_source_path.read_text()), plan)
    output.mkdir(parents=True, exist_ok=True)
    target = output / "network.net.xml"
    if target.exists():
        raise FileExistsError(target)
    ET.indent(patch, space="  ")
    ET.ElementTree(patch).write(output / "sidewalks.edg.xml", encoding="utf-8", xml_declaration=True)
    ET.indent(derived_xml, space="  ")
    ET.ElementTree(derived_xml).write(output / "derived-ground.osm", encoding="utf-8", xml_declaration=True)
    write_json(output / "road-presentation.osm.json", derived_road)
    write_json(output / "sidewalk-plan.json", plan)
    command = ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,size=256m",
        "--mount", f"type=bind,source={network_path.parent},target=/input,readonly",
        "--mount", f"type=bind,source={output},target=/output", "--entrypoint", "netconvert", SUMO_IMAGE,
        "--sumo-net-file", f"/input/{network_path.name}", "--edge-files", "/output/sidewalks.edg.xml",
        "--offset.disable-normalization", "--walkingareas", "true", "--output-file", "/output/network.net.xml"]
    write_json(output / "netconvert-command.json", command)
    with (output / "netconvert.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    after = ET.parse(target).getroot()
    added = {record["edge_id"] for record in plan["derived_edges"]
             if record["kind"] == "derived-reverse-pedestrian-only"}
    motor_proof = audit_motor_lanes(before, after, added)
    crossing_proof = audit_completed_crossings(before, after, plan)
    closure = audit_ground_network(target.read_bytes(), (output / "derived-ground.osm").read_bytes(),
                                  expected_projection=expected_projection)
    write_json(output / "ground-network-proof.json", closure)
    report = {"schema_version": "aero-bench.city-derived-sidewalks/v1", "source_kind": SOURCE_KIND,
        "source_network_sha256": sha(network_path), "source_ground_osm_sha256": sha(source_path),
        "source_road_json_sha256": sha(road_source_path), "network_sha256": sha(target),
        "derived_ground_osm_sha256": sha(output / "derived-ground.osm"),
        "derived_road_json_sha256": sha(output / "road-presentation.osm.json"),
        "ground_network_audit_module_sha256": sha(Path(audit_ground_network.__code__.co_filename)),
        "expected_projection": expected_projection,
        "edge_patch_sha256": sha(output / "sidewalks.edg.xml"), "profile": plan["profile"],
        "surveyed_sidewalk": False, "source_ground_counts": source_proof["source_counts"],
        "source_missing_crossing_endpoints": len(plan["missing_source_endpoints"]),
        "derived_source_way_count": len({record["source_way_id"] for record in plan["derived_edges"]}),
        "added_native_sidewalk_lanes": sum(record["kind"] == "native-right-sidewalk" for record in plan["derived_edges"]),
        "added_pedestrian_only_edges": len(added), "motor_proof": motor_proof, "crossing_proof": crossing_proof,
        "native_image": SUMO_IMAGE, "native_command": command,
        "ordinary_displayed_walkbed_and_real_demand": "PENDING_CANONICAL_ROAD_BUILD_AND_REAL_RECORDING"}
    write_json(output / "sidewalk-completion-report.json", report)
    engineering = completion_engineering(
        json.loads((network_path.parent / "engineering-inputs.json").read_text()),
        network_path, source_path, output)
    write_json(output / "engineering-inputs.json", engineering)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("network", "ground-osm", "road-json", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-projection", required=True)
    args = parser.parse_args()
    report = build(args.network, args.ground_osm, args.road_json, args.expected_projection, args.output)
    print(json.dumps({key: report[key] for key in ("network_sha256", "source_missing_crossing_endpoints",
        "derived_source_way_count", "added_native_sidewalk_lanes", "added_pedestrian_only_edges")}))
