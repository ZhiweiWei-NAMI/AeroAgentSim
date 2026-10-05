#!/usr/bin/env python3
"""Author, refine and audit ground streets until native walking areas clear the buildings.

Each round materializes authored-ground-streets with the current sidewalk overrides,
applies the physical movement refinement, and audits the result. Walking areas that
touch a rendered building or cannot be drawn escalate the sidewalks feeding them:
level 1 caps them at the minimum width, level 2 forbids a separate sidewalk (local
streets then share the carriageway, arterials omit the sidewalk), level 3 removes
pedestrian access from a shared carriageway. A sidewalk lane whose ribbon overlaps a
vehicle lane deeper than the road-walk seam escalates its own edge the same way. Every
escalation is recorded with the defect that caused it. The loop stops when the gate passes
with no such overlap, or no sidewalk can be escalated further.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from city_road_physical_clearance import walkway_vehicle_overlaps

HERE = Path(__file__).resolve().parent
MAX_ROUNDS = 6
MAX_SIDEWALK_LEVEL = 3


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


author = _load("author_ground_city_streets", "author-ground-city-streets.py")
refine = _load("refine_ground_network_physical", "refine-ground-network-physical.py")


def walking_area_defects(final_audit: dict) -> list[str]:
    contacts = [row["lane_id"] for row in final_audit["native_lane_clearance"]["rows"] if row["function"] == "walkingarea"]
    undrawable = [item["lane_id"] for item in final_audit["undrawable_required_walking_areas"]]
    return sorted(set(contacts) | set(undrawable))


def sidewalk_edges(network: ET.Element, walking_lane: str) -> set[str]:
    walk_edge = walking_lane.rsplit("_", 1)[0]
    normal = {edge.get("id") for edge in network.findall("edge") if edge.get("function", "normal") == "normal"}
    linked = set()
    for connection in network.findall("connection"):
        if connection.get("to") == walk_edge and connection.get("from") in normal:
            linked.add(connection.get("from"))
        if connection.get("from") == walk_edge and connection.get("to") in normal:
            linked.add(connection.get("to"))
    return linked


def run(args) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    overrides: dict[str, dict] = {}
    rounds = []
    for number in range(MAX_ROUNDS):
        round_dir = output / f"round-{number}"
        (output / f"round-{number}-overrides.json").write_text(json.dumps(overrides, indent=1, sort_keys=True) + "\n")
        built = author.build(args.network, args.source_osm, args.objects, args.render, args.pack, args.fleet_widths,
                             round_dir / "authored", common_scene_path=args.common_scene,
                             netconvert_options=(), sidewalk_overrides=overrides)
        refined = refine.refine(round_dir / "authored" / "network.net.xml", round_dir / "authored" / "authored-ground.osm",
                                round_dir / "refined", args.objects, args.render, args.pack)
        final_audit = json.loads((round_dir / "refined" / "final-physical-audit.json").read_text())
        defects = walking_area_defects(final_audit)
        network = ET.parse(round_dir / "refined" / "network.net.xml").getroot()
        escalated = []
        for walking_lane in defects:
            for edge in sorted(sidewalk_edges(network, walking_lane)):
                level = overrides.get(edge, {}).get("level", 0)
                if level >= MAX_SIDEWALK_LEVEL:
                    continue
                overrides[edge] = {"level": level + 1, "reason": f"native walking area {walking_lane} "
                                   + ("enters a rendered building" if walking_lane in [r["lane_id"] for r in final_audit["native_lane_clearance"]["rows"]]
                                      else "is not drawable")}
                escalated.append(edge)
        overlaps = walkway_vehicle_overlaps(network)
        for record in overlaps:
            edge, level = record["edge_id"], overrides.get(record["edge_id"], {}).get("level", 0)
            if edge in escalated or level >= MAX_SIDEWALK_LEVEL:
                continue
            crossed = ", ".join(f'{item["lane_id"]} ({item["overlap_m2"]} m2)' for item in record["vehicle_lanes"])
            overrides[edge] = {"level": level + 1, "reason": f"sidewalk lane {record['lane_id']} overlaps vehicle lanes {crossed}"}
            escalated.append(edge)
        passed = refined["final_gate"]["status"] == "PASS" and not overlaps
        rounds.append({"round": number, "authored_final_normal_edges": built["final_normal_edges"],
                       "gate": refined["final_gate"], "connected_share": refined["connected_share"],
                       "interior_dead_ends": refined["interior_dead_ends"], "walking_area_defects": defects,
                       "sidewalk_vehicle_overlaps": overlaps, "status": "PASS" if passed else "BLOCKED",
                       "escalated_edges": escalated})
        print(json.dumps(rounds[-1], ensure_ascii=False))
        if passed or not escalated:
            break
    summary = {"schema_version": "aero-bench.ground-streets-closed-loop/v1", "rounds": rounds,
               "final_round": rounds[-1]["round"], "final_status": rounds[-1]["status"],
               "final_overrides": overrides}
    (output / "closed-loop-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("network", "source-osm", "objects", "render", "pack", "fleet-widths", "common-scene", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    summary = run(parser.parse_args())
    print(json.dumps({"final_status": summary["final_status"], "rounds": len(summary["rounds"])}))


if __name__ == "__main__":
    main()
