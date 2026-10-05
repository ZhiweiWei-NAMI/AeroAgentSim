#!/usr/bin/env python3
"""Rebuild an OSM SUMO network after removing explicitly non-ground ways.

The OSM and SUMO inputs are read-only. A digest-pinned SUMO container runs
netconvert against the existing network so it can rebuild junction internals
and pedestrian crossings after edge removal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"
SUMO_VERSION = "1.27.1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from aero_bench.world.ground_roads import (
    GROUND_ROAD_POLICY,
    UNSUPPORTED_ROAD_HIGHWAYS,
    audit_ground_network,
    audit_retained_lane_rights,
    classify_roadways,
    edge_source_way_id,
    filtered_osm_xml,
    network_counts as _network_counts,
    way_filter_reasons,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan_filter(network_path: Path, source_osm_path: Path) -> dict:
    """Classify OSM ways and list removable normal edge IDs; no files are changed."""
    osm_root = ET.parse(source_osm_path).getroot()
    source_way_ids = {way.get("id", "") for way in osm_root.findall("way")}
    source_plan = classify_roadways(source_osm_path.read_bytes())
    ways = source_plan["excluded_ways"]
    elevation_review = source_plan["elevation_review"]
    ignored_non_highway_grade_way_count = source_plan["ignored_non_highway_grade_way_count"]

    network_root = ET.parse(network_path).getroot()
    normal_edges = [edge for edge in network_root.findall("edge") if edge.get("function", "normal") == "normal"]
    removed_edge_ids = []
    edge_source_map = {}
    unmapped_normal_edges = []
    removed_by_way: dict[str, list[str]] = {}
    for edge in normal_edges:
        edge_id = edge.get("id", "")
        try:
            way_id = edge_source_way_id(edge_id, source_way_ids)
        except ValueError:
            unmapped_normal_edges.append(edge_id)
            continue
        edge_source_map[edge_id] = way_id
        if way_id not in ways:
            continue
        removed_edge_ids.append(edge_id)
        removed_by_way.setdefault(way_id, []).append(edge_id)
    if unmapped_normal_edges:
        raise ValueError(f"Cannot map {len(unmapped_normal_edges)} normal SUMO edges to OSM way IDs; first={unmapped_normal_edges[:5]}")
    for way_id, record in ways.items():
        record["normal_edge_ids"] = sorted(removed_by_way.get(way_id, []))
        record["normal_edge_count"] = len(record["normal_edge_ids"])

    location = network_root.find("location")
    if location is None:
        raise ValueError("SUMO network has no <location> projection contract")
    net_offset = location.attrib.get("netOffset")
    if net_offset is None:
        raise ValueError("SUMO network <location> is missing netOffset")
    try:
        offset_x, offset_y = (float(value) for value in net_offset.split(","))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SUMO network has invalid netOffset: {net_offset!r}") from exc
    if offset_x != 0.0 or offset_y != 0.0:
        raise ValueError(f"ground-only output requires netOffset=0,0; source has {net_offset!r}")
    projection = location.attrib.get("projParameter")
    if not projection:
        raise ValueError("SUMO network <location> is missing projParameter")
    return {
        "network_root": network_root,
        "ways": ways,
        "elevation_review": elevation_review,
        "ignored_non_highway_grade_way_count": ignored_non_highway_grade_way_count,
        "removed_edge_ids": sorted(removed_edge_ids),
        "edge_source_map": edge_source_map,
        "projection": projection,
        "net_offset": net_offset,
        "input_counts": _network_counts(network_root),
        "normal_edge_count": len(normal_edges),
    }


def _docker_base() -> list[str]:
    return ["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
            "--tmpfs", "/tmp:rw,nosuid,size=256m"]


def verify_netconvert_image() -> str:
    command = [*_docker_base(), "--entrypoint", "netconvert", SUMO_IMAGE, "--version"]
    result = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT)
    version_text = result.stdout
    match = re.search(r"\bnetconvert\s+(?:Version\s+)?(\d+\.\d+\.\d+)\b", version_text)
    if not match or match.group(1) != SUMO_VERSION:
        raise ValueError(f"Pinned netconvert image did not report version {SUMO_VERSION}: {version_text.strip()}")
    return match.group(1)


def _run_netconvert(network_path: Path, remove_ids_path: Path, staged_output: Path) -> list[str]:
    network_path = network_path.resolve()
    remove_ids_path = remove_ids_path.resolve()
    output_dir = staged_output.parent.resolve()
    command = [*_docker_base(),
               "--mount", f"type=bind,source={network_path},target=/input/source.net.xml,readonly",
               "--mount", f"type=bind,source={remove_ids_path},target=/input/remove-edge-ids.txt,readonly",
               "--mount", f"type=bind,source={output_dir},target=/output",
               "--entrypoint", "netconvert", SUMO_IMAGE,
               "--sumo-net-file", "/input/source.net.xml",
               "--remove-edges.input-file", "/input/remove-edge-ids.txt",
               "--output-file", f"/output/{staged_output.name}",
               "--offset.disable-normalization"]
    subprocess.run(command, check=True)
    return command


def build_ground_network(network_path: Path, source_osm_path: Path, output_dir: Path) -> dict:
    network_path = network_path.resolve()
    source_osm_path = source_osm_path.resolve()
    output_dir = output_dir.resolve()
    if not network_path.is_file() or not source_osm_path.is_file():
        raise FileNotFoundError("--network and --source-osm must refer to existing files")
    targets = [output_dir / "network.net.xml", output_dir / "engineering-inputs.json",
               output_dir / "ground-filter-report.json", output_dir / "ground.osm",
               output_dir / "ground-network-proof.json"]
    if any(path.exists() for path in targets):
        raise FileExistsError(f"Output already exists; use a fresh --output-dir: {output_dir}")
    plan = plan_filter(network_path, source_osm_path)
    netconvert_version = verify_netconvert_image()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ground-netconvert-", dir=output_dir) as temp:
        tempdir = Path(temp)
        removed_path = tempdir / "remove-edge-ids.txt"
        removed_path.write_text("".join(f"{edge_id}\n" for edge_id in plan["removed_edge_ids"]),
                                encoding="utf-8")
        staged_network = tempdir / "network.net.xml"
        command = _run_netconvert(network_path, removed_path, staged_network)
        rebuilt = ET.parse(staged_network).getroot()
        staged_osm = tempdir / "ground.osm"
        staged_osm.write_bytes(filtered_osm_xml(source_osm_path.read_bytes(), set(plan["ways"])))
        proof = audit_ground_network(staged_network.read_bytes(), staged_osm.read_bytes(),
                                     expected_projection=plan["projection"])
        proof["retention"] = audit_retained_lane_rights(
            network_path.read_bytes(), staged_network.read_bytes(), set(plan["removed_edge_ids"]))
        staged_proof = tempdir / "ground-network-proof.json"
        staged_proof.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_location = rebuilt.find("location")
        if (output_location is None or
                output_location.attrib.get("projParameter") != plan["projection"] or
                output_location.attrib.get("netOffset") != plan["net_offset"]):
            raise ValueError("netconvert changed or dropped source projParameter/netOffset")
        remaining_normal = {edge.get("id") for edge in rebuilt.findall("edge")
                            if edge.get("function", "normal") == "normal"}
        stale_edges = sorted(set(plan["removed_edge_ids"]) & remaining_normal)
        if stale_edges:
            raise ValueError(f"Filtered SUMO network still contains removed normal edges: {stale_edges[:10]}")
        input_sha = file_sha256(network_path)
        osm_sha = file_sha256(source_osm_path)
        output_sha = file_sha256(staged_network)
        report = {
            "schema_version": "aero-bench.city-ground-network-filter/v1",
            "status": "generated",
            "road_scope": "ground-only",
            "input": {"network": str(network_path), "network_sha256": input_sha,
                      "source_osm": str(source_osm_path), "source_osm_sha256": osm_sha},
            "output": {"network": "network.net.xml", "network_sha256": output_sha,
                       "engineering_inputs": "engineering-inputs.json"},
            "projection": {"proj_parameter": plan["projection"],
                           "net_offset": plan["net_offset"], "net_offset_required": "0,0",
                           "preserved_exactly": True, "normalization": "disabled"},
            "filter_policy": {
                "exclude_positive_bridge_tag": True,
                "exclude_positive_tunnel_tag": True,
                "exclude_nonzero_or_unparsed_layer": True,
                "policy": GROUND_ROAD_POLICY,
                "exclude_unsupported_highway_values": sorted(UNSUPPORTED_ROAD_HIGHWAYS),
                "exclude_nonzero_or_unparsed_level": True,
                "exclude_positive_elevated_tag": True,
                "exclude_by_declared_nonflat_elevation_profile": True,
                "exclude_nonzero_or_unsupported_incline": True,
                "missing_elevation_is_unmeasured": True,
                "absolute_ele_is_not_a_non_ground_filter": True,
                "exclude_name_only_tunnel_suspects": True,
                "name_only_tunnel_policy": "conservative exclusion: a way name containing tunnel or 隧道 is excluded when tunnel=yes is absent; report retains the heuristic reason for review",
                "layer_to_meters_conversion": "never",
            },
            "excluded_way_ids": sorted(plan["ways"]),
            "ignored_non_highway_grade_way_count": plan["ignored_non_highway_grade_way_count"],
            "removed_edge_ids": plan["removed_edge_ids"],
            "excluded_ways": [plan["ways"][way_id] for way_id in sorted(plan["ways"])],
            "elevation_review": plan["elevation_review"],
            "unrepresented_excluded_ways": [way_id for way_id in sorted(plan["ways"])
                                            if not plan["ways"][way_id]["normal_edge_ids"]],
            "network_before": plan["input_counts"],
            "network_after": _network_counts(rebuilt),
            "filtered_osm": {"path": "ground.osm", "sha256": file_sha256(staged_osm)},
            "network_closure": {"path": "ground-network-proof.json", "sha256": file_sha256(staged_proof)},
            "removed_normal_edge_count": len(plan["removed_edge_ids"]),
            "internal_edge_policy": "internal edges are not listed as removals; netconvert rebuilds them from the filtered network",
            "crossing_policy": "netconvert rebuilds crossings/connections from the filtered network; traffic routes are generated afresh from the output network",
            "netconvert": {"image": SUMO_IMAGE, "version": netconvert_version,
                           "command": command, "source_osm_rebuilt": False,
                           "filtered_osm_materialized_for_audit": True},
        }
        engineering = {
            "schema_version": "aero-bench.urban-engineering-traffic-inputs/v1",
            "source_osm": str(source_osm_path), "source_osm_sha256": osm_sha,
            "source_network": str(network_path), "source_network_sha256": input_sha,
            "filtered_source_osm": "ground.osm", "filtered_source_osm_sha256": file_sha256(staged_osm),
            "network_closure": "ground-network-proof.json",
            "road_scope": "ground-only", "ground_filter_report": "ground-filter-report.json",
            "projection": plan["projection"], "sumo_image_id": SUMO_IMAGE,
            "net_offset": plan["net_offset"], "net_offset_required": "0,0",
            "netconvert_version": netconvert_version,
            "network_generation": "read existing SUMO network and remove classified edges; regenerate junction internals and crossing connections",
            "removed_edge_count": len(plan["removed_edge_ids"]),
        }
        staged_engineering = tempdir / "engineering-inputs.json"
        staged_report = tempdir / "ground-filter-report.json"
        staged_engineering.write_text(json.dumps(engineering, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for source, target in zip((staged_network, staged_engineering, staged_report, staged_osm, staged_proof), targets):
            source.replace(target)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True, help="Original SUMO network.net.xml")
    parser.add_argument("--source-osm", type=Path, required=True, help="Matching SUMO input OSM")
    parser.add_argument("--output-dir", type=Path, required=True, help="New output directory; existing targets are preserved")
    args = parser.parse_args(argv)
    try:
        report = build_ground_network(args.network, args.source_osm, args.output_dir)
    except (OSError, ValueError, subprocess.CalledProcessError, ET.ParseError) as exc:
        print(f"ground network build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"network": str(args.output_dir / "network.net.xml"),
                      "removed_way_count": len(report["excluded_way_ids"]),
                      "removed_edge_count": report["removed_normal_edge_count"],
                      "network_after": report["network_after"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
