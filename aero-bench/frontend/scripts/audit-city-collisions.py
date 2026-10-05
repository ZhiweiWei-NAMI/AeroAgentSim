#!/usr/bin/env python3
"""Audit actual preview samples and rendered proxy boxes in the OSM map frame."""

from __future__ import annotations

import json
import math
import argparse
import hashlib
from collections import Counter
from pathlib import Path

from shapely import affinity
from shapely.geometry import Point, box
from shapely.strtree import STRtree

from city_fixture_geometry import SIGNAL_DISPLAY_HEIGHT_M
from city_preview_scene import DEFAULT_SCENE, read_scene_paths


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "frontend/validation/city-collision-audit.json"
VEHICLE_SIZE = {
    "sedan": (1.8, 4.5), "taxi": (1.85, 4.7), "police": (1.9, 4.8),
    "bus": (2.5, 11.5), "truck": (2.2, 7.0), "bicycle": (0.65, 1.8),
}


def rectangle(x: float, z: float, width: float, depth: float, heading: float):
    return affinity.translate(
        affinity.rotate(box(-width / 2, -depth / 2, width / 2, depth / 2),
                        heading, origin=(0, 0)), xoff=x, yoff=z)


def blend_angle(before: float, after: float, fraction: float) -> float:
    return before + ((after - before + 180) % 360 - 180) * fraction


def interpolated_samples(before: list, after: list, fraction: float):
    upcoming = {sample[0]: sample for sample in after}
    for sample in before:
        next_sample = upcoming.get(sample[0], sample)
        yield sample[0], (sample[1] * (1 - fraction) + next_sample[1] * fraction), (
            sample[2] * (1 - fraction) + next_sample[2] * fraction), blend_angle(
                sample[3], next_sample[3], fraction), sample[4:]


def rendered_building_proxies(buildings: dict) -> list[dict]:
    """Match the viewer: complete source OBBs replace their decomposed parts."""
    envelopes = buildings["complete_footprint_envelopes"]
    complete_ids = {item["building_id"] for item in envelopes}
    return [*envelopes, *(item for item in buildings["placements"]
                         if item["building_id"] not in complete_ids)]


def main(scene_path: Path = DEFAULT_SCENE, output_path: Path = OUTPUT) -> None:
    paths = read_scene_paths(scene_path)
    traffic = json.loads(paths.traffic.read_text())
    buildings = json.loads(paths.placement.read_text())
    flight = json.loads(paths.flight.read_text())
    if traffic["mesh_pack_source_sha256"] != buildings["mesh_pack_source_sha256"]:
        raise ValueError("Traffic and building proxies use different OSM sources")
    if flight["mesh_pack_source_sha256"] != buildings["mesh_pack_source_sha256"]:
        raise ValueError("Flight and building proxies use different OSM sources")
    if flight["source_kind"] == "planned-visual-flight" and (
            flight["physical_simulation"] is not False
            or flight["building_placement_sha256"] != hashlib.sha256(paths.placement.read_bytes()).hexdigest()):
        raise ValueError("Visual flight plan is not tied to the audited building placement")
    if flight["independent_from_sumo_preview"] is not True:
        raise ValueError("Flight was not labelled as independent from SUMO")
    if traffic["sumo_colliding_vehicle_ids"]:
        raise ValueError("SUMO reported vehicle collisions")
    building_proxies = rendered_building_proxies(buildings)
    static = [rectangle(item["x"], item["z"], item["width"], item["depth"],
                        -item["rotation_deg"]) for item in building_proxies]
    static_index = STRtree(static)
    signals = [box(item["x"] - 0.275, item["z"] - 0.275,
                   item["x"] + 0.275, item["z"] + 0.275) for item in traffic["signals"]]
    signal_index = STRtree(signals)
    counts = Counter()
    offenders: dict[str, Counter[str]] = {}
    pairs: dict[str, Counter[str]] = {}
    examples = {}

    def record(kind: str, moment: float, first: str, second: str, area: float) -> None:
        counts[kind] += 1
        offenders.setdefault(kind, Counter())[first] += 1
        pairs.setdefault(kind, Counter())[f"{first} / {second}"] += 1
        examples.setdefault(kind, (moment, first, second, round(area, 4)))

    for index, footprint in enumerate(static):
        for other in static_index.query(footprint):
            if other <= index:
                continue
            area = footprint.intersection(static[other]).area
            if area > 0.03:
                record("building_building", 0, building_proxies[index]["building_id"],
                       building_proxies[other]["building_id"], area)
    for index, footprint in enumerate(signals):
        for building in static_index.query(footprint):
            area = footprint.intersection(static[building]).area
            if area > 0.01:
                record("signal_building", 0, traffic["signals"][index]["id"],
                       building_proxies[building]["building_id"], area)
        for other in signal_index.query(footprint):
            if other <= index:
                continue
            area = footprint.intersection(signals[other]).area
            if area > 0.01:
                record("signal_signal", 0, traffic["signals"][index]["id"],
                       traffic["signals"][other]["id"], area)

    frames = traffic["frames"]
    for index, frame in enumerate(frames):
        next_frame = frames[min(index + 1, len(frames) - 1)]
        for fraction in (0.0, 0.5) if index < len(frames) - 1 else (0.0,):
            moment = frame["second"] + fraction * traffic["step_seconds"]
            moving = []
            for entity_id, x, z, heading, rest in interpolated_samples(
                    frame["vehicles"], next_frame["vehicles"], fraction):
                vehicle_type, ground_y = rest
                if not math.isfinite(ground_y) or ground_y < -0.1:
                    record("invalid_vehicle_ground", moment, entity_id, "ground", ground_y)
                width, length = VEHICLE_SIZE[vehicle_type]
                footprint = rectangle(x, z, width, length, heading)
                moving.append((entity_id, footprint))
                for building in static_index.query(footprint):
                    area = footprint.intersection(static[building]).area
                    if area > 0.03:
                        record("vehicle_building", moment, entity_id,
                               building_proxies[building]["building_id"], area)
                for signal in signal_index.query(footprint):
                    area = footprint.intersection(signals[signal]).area
                    if area > 0.02:
                        record("vehicle_signal", moment, entity_id, traffic["signals"][signal]["id"], area)
            moving_index = STRtree([item[1] for item in moving]) if moving else None
            if moving_index is not None:
                for vehicle, (entity_id, footprint) in enumerate(moving):
                    for other in moving_index.query(footprint):
                        if other <= vehicle:
                            continue
                        area = footprint.intersection(moving[other][1]).area
                        if area > 0.03:
                            record("vehicle_vehicle", moment, entity_id, moving[other][0], area)
            for person_id, x, z, _, rest in interpolated_samples(
                    frame["persons"], next_frame["persons"], fraction):
                ground_y = rest[0]
                if not math.isfinite(ground_y) or ground_y < -0.1:
                    record("invalid_person_ground", moment, person_id, "ground", ground_y)
                footprint = Point(x, z).buffer(0.3)
                for building in static_index.query(footprint):
                    area = footprint.intersection(static[building]).area
                    if area > 0.02:
                        record("person_building", moment, person_id,
                               building_proxies[building]["building_id"], area)
                for signal in signal_index.query(footprint):
                    area = footprint.intersection(signals[signal]).area
                    if area > 0.02:
                        record("person_signal", moment, person_id, traffic["signals"][signal]["id"], area)
                if moving_index is not None:
                    for vehicle in moving_index.query(footprint):
                        area = footprint.intersection(moving[vehicle][1]).area
                        if area > 0.02:
                            record("person_vehicle", moment, person_id, moving[vehicle][0], area)

            flight_index = min(int(moment / flight["step_seconds"]), len(flight["frames"]) - 2)
            flight_fraction = (moment - flight_index * flight["step_seconds"]) / flight["step_seconds"]
            flight_next = {item[0]: item for item in flight["frames"][flight_index + 1]}
            aircraft = []
            for sample in flight["frames"][flight_index]:
                after = flight_next[sample[0]]
                x = sample[1] * (1 - flight_fraction) + after[1] * flight_fraction
                z = sample[2] * (1 - flight_fraction) + after[2] * flight_fraction
                up = max(0, sample[3] * (1 - flight_fraction) + after[3] * flight_fraction) + 0.01
                footprint = box(x - 0.5, z - 0.5, x + 0.5, z + 0.5)
                aircraft.append((sample[0], footprint, up))
                for building in static_index.query(footprint):
                    item = building_proxies[building]
                    if up < item["base_y"] + item["height"] and up + 0.35 > item["base_y"]:
                        area = footprint.intersection(static[building]).area
                        if area > 0.02:
                            record("uav_building", moment, sample[0], item["building_id"], area)
                if up < SIGNAL_DISPLAY_HEIGHT_M:
                    for signal in signal_index.query(footprint):
                        area = footprint.intersection(signals[signal]).area
                        if area > 0.02:
                            record("uav_signal", moment, sample[0], traffic["signals"][signal]["id"], area)
                if moving_index is not None:
                    for vehicle in moving_index.query(footprint):
                        vehicle_sample = next(item for item in frame["vehicles"] if item[0] == moving[vehicle][0])
                        vehicle_height = {"sedan": 1.5, "taxi": 1.65, "police": 1.7,
                                          "bus": 3.3, "truck": 3.2, "bicycle": 1.72}[vehicle_sample[4]]
                        if up < vehicle_sample[5] + vehicle_height:
                            area = footprint.intersection(moving[vehicle][1]).area
                            if area > 0.02:
                                record("uav_vehicle_combined_visual", moment, sample[0], moving[vehicle][0], area)
            if aircraft[0][2] < aircraft[1][2] + 0.35 and aircraft[1][2] < aircraft[0][2] + 0.35:
                area = aircraft[0][1].intersection(aircraft[1][1]).area
                if area > 0.02:
                    record("uav_uav", moment, aircraft[0][0], aircraft[1][0], area)

    report = {
        "source": {"traffic": str(paths.traffic.relative_to(ROOT)),
                   "buildings": str(paths.placement.relative_to(ROOT)),
                   "flight": str(paths.flight.relative_to(ROOT))},
        "time_step_seconds": traffic["step_seconds"], "checked_times": 2 * len(frames) - 1,
        "building_proxies": len(static), "signal_pole_proxies": len(signals),
        "vehicle_types": list(VEHICLE_SIZE),
        "collision_counts": dict(counts), "first_examples": examples,
        "first_entity_counts": {kind: dict(items.most_common()) for kind, items in offenders.items()},
        "pair_counts": {kind: dict(items.most_common()) for kind, items in pairs.items()},
        "sumo_colliding_vehicle_ids": traffic["sumo_colliding_vehicle_ids"],
        "ground_snap": traffic["ground_snap"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if counts:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--report", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    main(arguments.scene, arguments.report)
