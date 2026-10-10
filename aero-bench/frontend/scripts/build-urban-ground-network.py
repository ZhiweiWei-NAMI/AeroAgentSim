#!/usr/bin/env python3
"""Build a ground-only city network from OSM with explicit urban design defaults.

This is an engineering preview design, not a survey of speed limits or sidewalks.
The complete typemap comes from the pinned SUMO image. Explicit OSM values retain
netconvert's normal precedence over defaults. Non-ground ways are removed before
building junctions, walking areas and traffic-light programs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import (
    GROUND_ROAD_POLICY, UNSUPPORTED_ROAD_HIGHWAYS,
    audit_ground_network, filtered_osm_xml as ground_osm,
)

FILTER = runpy.run_path(str(Path(__file__).with_name("build-ground-city-network.py")))
SUMO_IMAGE = FILTER["SUMO_IMAGE"]
URBAN_SPEEDS_MPS = {"primary": 13.89, "primary_link": 8.33,
    "secondary": 11.11, "secondary_link": 8.33, "tertiary": 11.11,
    "tertiary_link": 8.33, "residential": 8.33, "unclassified": 8.33}
BICYCLE_TYPES = {"cycleway.lane", "cycleway.opposite_lane",
                 "cycleway.track", "cycleway.opposite_track"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def official_typemap(name: str, output: Path, base: list[str]) -> bytes:
    if name not in ("osmNetconvert.typ.xml", "osmNetconvertBicycle.typ.xml"):
        raise ValueError(f"Unsupported official SUMO typemap: {name}")
    command = [*base, "--entrypoint", "python3", SUMO_IMAGE,
        "-c", f"import sumo,pathlib;print((pathlib.Path(sumo.__file__).parent/'data/typemap/{name}').read_text(),end='')"]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stem = "official-bicycle" if name == "osmNetconvertBicycle.typ.xml" else "official"
    (output / f"{stem}-typemap.stdout.log").write_bytes(result.stdout)
    (output / f"{stem}-typemap.stderr.log").write_bytes(result.stderr)
    (output / f"{stem}-typemap-command.json").write_text(json.dumps({
        "command": command, "image": SUMO_IMAGE, "exit_code": result.returncode,
        "host_path_scope": "build_time_only"}, indent=2) + "\n", encoding="utf-8")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    return result.stdout


def urban_typemap(official: bytes, bicycle: bytes) -> bytes:
    root = ET.fromstring(official)
    for item in root.findall("type"):
        name = item.attrib["id"].removeprefix("highway.")
        if name in URBAN_SPEEDS_MPS:
            item.set("speed", str(URBAN_SPEEDS_MPS[name]))
            item.set("sidewalkWidth", "2")
    missing = {"highway." + name for name in URBAN_SPEEDS_MPS} - {
        item.attrib["id"] for item in root.findall("type")}
    if missing:
        raise ValueError(f"Official SUMO typemap lacks urban types: {sorted(missing)}")
    bicycle_root = ET.fromstring(bicycle)
    bicycle_entries = {item.get("id"): item for item in bicycle_root.findall("type")}
    if set(bicycle_entries) != BICYCLE_TYPES or any(
            item.get("allow") != "bicycle" or not item.get("bikeLaneWidth")
            for item in bicycle_entries.values()):
        raise ValueError("Pinned SUMO bicycle typemap differs from expected bicycle lane contract")
    existing = {item.get("id") for item in root.findall("type")}
    if existing & BICYCLE_TYPES:
        raise ValueError("Pinned SUMO urban and bicycle typemaps have duplicate types")
    root.extend(bicycle_entries[name] for name in sorted(BICYCLE_TYPES))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def normalize_busway_tags(source: bytes, typemap: bytes) -> tuple[bytes, list[dict]]:
    """Avoid SUMO 1.27.1 interpreting a same-direction left bus lane as contraflow."""
    root = ET.fromstring(source)
    types = {item.attrib["id"]: item.attrib for item in ET.fromstring(typemap).findall("type")}
    changes = []
    for way in root.findall("way"):
        tags = {item.attrib["k"]: item.attrib["v"] for item in way.findall("tag")}
        if tags.get("oneway") not in ("yes", "true", "1") or tags.get("busway:left") != "lane":
            continue
        if any(tags.get(key) in ("no", "false", "0", "-1") for key in ("oneway:bus", "oneway:psv")) \
                or any("opposite" in value for key, value in tags.items() if key.startswith("busway")):
            continue
        lane_source = "osm_lanes" if "lanes" in tags else "declared_typemap_default"
        count = int(tags["lanes"] if "lanes" in tags else
                    types["highway." + tags["highway"]]["numLanes"])
        if count <= 0:
            raise ValueError(f"Invalid lane count on {way.attrib['id']}")
        value = "|".join(["designated", *(["yes"] * (count - 1))])
        if "psv:lanes" in tags or ("bus:lanes" in tags and tags["bus:lanes"] != value):
            raise ValueError(f"Conflicting bus lane tags require review: {way.attrib['id']}")
        for tag in way.findall("tag"):
            if tag.attrib["k"] == "busway:left":
                way.remove(tag)
        if "bus:lanes" not in tags:
            ET.SubElement(way, "tag", k="bus:lanes", v=value)
        changes.append({"way_id": way.attrib["id"], "original": {"busway:left": "lane", "oneway": tags["oneway"]},
                        "staged_bus_lanes": value, "lane_count": count, "lane_count_source": lane_source})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), changes



def verify_busway_lanes(network: ET.Element, changes: list[dict]) -> None:
    """Reject a netconvert result that reintroduces contraflow or changes lane counts."""
    edges = [edge for edge in network.findall("edge") if edge.get("function", "normal") == "normal"]
    for change in changes:
        way_id = change["way_id"]
        forward = [edge for edge in edges if edge.get("id").partition("#")[0] == way_id]
        reverse = [edge for edge in edges if edge.get("id").partition("#")[0] == "-" + way_id]
        if not forward or reverse:
            raise ValueError(f"Normalized bus way lost its forward edges or gained contraflow: {way_id}")
        for edge in forward:
            motor = [lane for lane in edge.findall("lane") if lane.get("allow") != "pedestrian"]
            if len(motor) != change["lane_count"]:
                raise ValueError(f"Normalized bus way changed lane count: {edge.get('id')}")
            left = max(motor, key=lambda lane: int(lane.get("index")))
            if set(left.get("allow", "").split()) != {"bus"}:
                raise ValueError(f"Normalized bus way lacks the left bus-only lane: {edge.get('id')}")


def verify_bicycle_lanes(source: bytes, network: ET.Element) -> list[dict]:
    """Audit each declared bike facility against generated directional permissions."""
    edges = {edge.get("id", ""): edge for edge in network.findall("edge")
             if edge.get("function", "normal") == "normal"}
    records = []
    for way in ET.fromstring(source).findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag")}
        way_id = way.get("id", "")
        oneway = tags.get("oneway") in ("yes", "true", "1")
        declared = tags.get("cycleway")
        right = tags.get("cycleway:right")
        left = tags.get("cycleway:left")
        both = tags.get("cycleway:both")
        for value in (declared, right, left, both):
            if value not in (None, "no", "none") and value not in {
                    "lane", "track", "opposite_lane", "opposite_track"}:
                raise ValueError(f"Unsupported OSM cycleway tag on way {way_id}: {value}")
        if any(value in ("opposite_lane", "opposite_track") for value in (right, left, both)):
            raise ValueError(f"Unsupported directional opposite cycleway tag on way {way_id}")
        directions: set[str] = set()
        if declared in ("lane", "track"):
            directions.add("forward")
            if not oneway:
                directions.add("reverse")
        if declared in ("opposite_lane", "opposite_track"):
            if not oneway:
                raise ValueError(f"Opposite cycleway on non-oneway road: {way_id}")
            directions.add("reverse")
        if right in ("lane", "track"):
            directions.add("forward")
        if left in ("lane", "track"):
            directions.add("reverse")
        if both in ("lane", "track"):
            directions.update(("forward", "reverse"))
        if oneway and tags.get("oneway:bicycle") == "no":
            directions.add("reverse")
        for direction in sorted(directions):
            prefix = way_id if direction == "forward" else "-" + way_id
            matching = [edge for edge_id, edge in edges.items()
                        if edge_id.split("#", 1)[0] == prefix]
            if not matching:
                raise ValueError(f"Declared {direction} bicycle way has no network edge: {way_id}")
            for edge in matching:
                lanes = edge.findall("lane")
                if not any(lane.get("allow") == "bicycle" for lane in lanes):
                    raise ValueError(f"Declared bicycle way lacks dedicated bike lane: {edge.get('id')}")
                if direction == "reverse" and oneway and any(
                        lane.get("allow") not in ("bicycle", "pedestrian") for lane in lanes):
                    raise ValueError(f"One-way bicycle contraflow admits motor traffic: {edge.get('id')}")
            track_tags = [key for key, value in (
                ("cycleway", declared), ("cycleway:right", right),
                ("cycleway:left", left), ("cycleway:both", both))
                if value in ("track", "opposite_track") and (
                    key in ("cycleway", "cycleway:both")
                    or (key == "cycleway:right" and direction == "forward")
                    or (key == "cycleway:left" and direction == "reverse"))]
            records.append({"way_id": way_id, "direction": direction,
                            "edge_ids": sorted(edge.get("id") for edge in matching),
                            "declared_cycleway": declared,
                            "track_tag_sources": track_tags,
                            "track_geometry_policy": "adjacent lane on OSM way corridor; separate surveyed track geometry unavailable"
                            if track_tags else None})
    return records


def build(source: Path, reference_network: Path, output: Path) -> None:
    source, reference_network, output = source.resolve(), reference_network.resolve(), output.resolve()
    if (output / "network.net.xml").exists():
        raise ValueError("Use a new output directory; an existing network is not overwritten")
    plan = FILTER["plan_filter"](reference_network, source)
    output.mkdir(parents=True, exist_ok=True)
    netconvert_version = FILTER["verify_netconvert_image"]()
    base = ["docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--user", f"{os.getuid()}:{os.getgid()}"]
    official = official_typemap("osmNetconvert.typ.xml", output, base)
    (output / "official.typ.xml").write_bytes(official)
    bicycle = official_typemap("osmNetconvertBicycle.typ.xml", output, base)
    (output / "official-bicycle.typ.xml").write_bytes(bicycle)
    typemap = urban_typemap(official, bicycle)
    (output / "urban.typ.xml").write_bytes(typemap)
    staged_osm, busway_changes = normalize_busway_tags(
        ground_osm(source.read_bytes(), set(plan["ways"])), typemap)
    (output / "ground.osm").write_bytes(staged_osm)
    arguments = ["--osm-files", "/output/ground.osm", "--type-files", "/output/urban.typ.xml",
        "--output-file", "/output/network.net.xml", "--proj", plan["projection"],
        "--sidewalks.guess", "--sidewalks.guess.from-permissions", "--crossings.guess",
        "--walkingareas", "--osm.lane-access", "true", "--osm.bike-access", "true",
        "--offset.disable-normalization", "true"]
    command = [*base, "--mount", f"type=bind,source={output},target=/output",
        "--entrypoint", "netconvert", SUMO_IMAGE, *arguments]
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (output / "netconvert.stdout.log").write_text(result.stdout, encoding="utf-8")
    (output / "netconvert.stderr.log").write_text(result.stderr, encoding="utf-8")
    (output / "netconvert.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    (output / "netconvert-command.json").write_text(json.dumps({
        "command": command, "version": netconvert_version, "image": SUMO_IMAGE,
        "exit_code": result.returncode, "host_path_scope": "build_time_only"}, indent=2) + "\n", encoding="utf-8")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
    network = ET.parse(output / "network.net.xml").getroot()
    closure = audit_ground_network((output / "network.net.xml").read_bytes(), staged_osm,
                                   expected_projection=plan["projection"])
    (output / "ground-network-proof.json").write_text(json.dumps(closure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    verify_busway_lanes(network, busway_changes)
    bicycle_audit = verify_bicycle_lanes(staged_osm, network)
    location = network.find("location")
    if location is None or location.get("netOffset") != "0.00,0.00" \
            or location.get("projParameter") != plan["projection"]:
        raise ValueError("SUMO changed the city coordinate frame")
    ids = {edge.attrib["id"] for edge in network.findall("edge")}
    if ids.intersection(plan["removed_edge_ids"]):
        raise ValueError("Excluded non-ground roads reappeared in the new network")
    log = (output / "netconvert.log").read_text()
    if "Invalid pedestrian topology" in log or "Discarding invalid crossing" in log:
        raise ValueError("SUMO reported incomplete pedestrian topology; candidate not accepted")
    report = {"source_osm_sha256": sha(source), "reference_network_sha256": sha(reference_network),
        "network_sha256": sha(output / "network.net.xml"),
        "filter_policy": {"id": GROUND_ROAD_POLICY,
                          "exclude_unsupported_highway_values": sorted(UNSUPPORTED_ROAD_HIGHWAYS)},
        "removed_way_count": len(plan["ways"]), "removed_edge_ids": plan["removed_edge_ids"],
        "removed_ways": list(plan["ways"].values()), "network_after": FILTER["_network_counts"](network),
        "design_defaults": {"surveyed": False, "urban_speed_mps": URBAN_SPEEDS_MPS,
                            "sidewalk_width_m": 2, "explicit_osm_tags_take_precedence": True},
        "busway_normalization": busway_changes,
        "bicycle_import": {"option": "--osm.bike-access true",
                           "official_bicycle_typemap_sha256": sha(output / "official-bicycle.typ.xml"),
                           "verified_directional_lanes": bicycle_audit},
        "typemap_sha256": sha(output / "urban.typ.xml"),
        "official_typemap_sha256": sha(output / "official.typ.xml"),
        "filtered_osm_sha256": sha(output / "ground.osm"),
        "network_closure": {"path": "ground-network-proof.json", "sha256": sha(output / "ground-network-proof.json")},
        "netconvert_arguments": arguments,
        "netconvert_command": command, "netconvert_version": netconvert_version,
        "netconvert_command_host_paths": "build_time_only",
        "netconvert_stdout_sha256": sha(output / "netconvert.stdout.log"),
        "netconvert_stderr_sha256": sha(output / "netconvert.stderr.log")}
    (output / "ground-filter-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    engineering = {"schema_version": "aero-bench.urban-engineering-traffic-inputs/v1",
        "source_osm": str(source), "source_osm_sha256": sha(source),
        "road_scope": "ground-only", "ground_filter_report": "ground-filter-report.json",
        "network_sha256": sha(output / "network.net.xml"), "projection": plan["projection"],
        "sumo_image_id": SUMO_IMAGE,
        "netconvert_version": netconvert_version,
        "network_generation": "OSM filtered before import, complete SUMO typemap with explicit urban design defaults"}
    (output / "engineering-inputs.json").write_text(json.dumps(engineering, indent=2) + "\n")
    print(json.dumps(report["network_after"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-osm", type=Path, required=True)
    parser.add_argument("--reference-network", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    build(args.source_osm, args.reference_network, args.output_dir)
