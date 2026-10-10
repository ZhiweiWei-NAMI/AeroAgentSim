"""Inspect a coherent historical v1 SUMO preview quartet.

This checker is retained for explicit inspection of archived v1 inputs. It has no
default scene: the ground and walkable recordings remain pinned to their original
mesh pack and cannot be combined with the current render pack. Current acceptance
uses ``audit-sumo-canonical-motion-v2.py``.

The checks still derive every input from source files. They do not import the
preview builder or trust the payload's collision claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RETIRED_GROUND_SCENE = ROOT / "frontend/public/city-presentation/huangpu-ground-scene-v1.json"
RETIRED_WALKABLE_SCENE = ROOT / "frontend/public/city-presentation/huangpu-walkable-scene-v1.json"

UNION_POLICY = "placement-proxies-union-true-rendered-footprints"
VEHICLE_DIMENSIONS = {"sedan": (1.8, 4.5), "taxi": (1.85, 4.7), "police": (1.9, 4.8),
                      "bus": (2.5, 11.5), "truck": (2.2, 7.0), "bicycle": (0.65, 1.8)}
VEHICLE_VCLASS = {"sedan": "passenger", "taxi": "taxi", "police": "emergency",
                  "bus": "bus", "truck": "delivery", "bicycle": "bicycle"}
VEHICLE_OVERLAP_M2 = 0.03
PEDESTRIAN_RADIUS_M = 0.3
PEDESTRIAN_OVERLAP_M2 = 0.02
SURFACE_TOLERANCE_M = 0.01
FRAME_STEP_SECONDS = 0.25
MIN_DISPLAYED_VEHICLES = 50


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allow, disallow = lane.get("allow"), lane.get("disallow")
    if allow is not None:
        return vehicle_class in allow.split()
    if disallow is not None:
        return vehicle_class not in disallow.split()
    return True


def network_lane_index(network: ET.Element) -> tuple[dict[str, ET.Element], dict[str, ET.Element], set[str]]:
    lanes: dict[str, ET.Element] = {}
    edges: dict[str, ET.Element] = {}
    tls_ids: set[str] = set()
    for edge in network.findall("edge"):
        if edge.get("id") is not None:
            edges[edge.get("id")] = edge
        for lane in edge.findall("lane"):
            lanes[lane.get("id")] = lane
    for tl in network.findall("tlLogic"):
        if tl.get("id") is not None:
            tls_ids.add(tl.get("id"))
    return lanes, edges, tls_ids


def footprint_polygons(objects: dict, failures: list[str]):
    from shapely.geometry import Polygon

    polygons = []
    for building in objects.get("buildings", []):
        ring = building.get("footprint_enu_m")
        if not isinstance(ring, list) or len(ring) < 3:
            failures.append(f"rendered building {building.get('object_id')!r} has no usable footprint")
            continue
        polygons.append(Polygon([(east, -north) for east, north in ring]).buffer(0))
    return polygons


def surface_polygons(road: dict, failures: list[str]):
    from shapely.geometry import Polygon

    surfaces = []
    for kind in ("roadbed", "walkbed"):
        for part in road.get(kind, []):
            try:
                surfaces.append(Polygon(part["outline"], part.get("holes", [])))
            except (KeyError, TypeError) as error:
                failures.append(f"{kind} polygon is malformed: {error}")
    return surfaces


def check_pins(traffic, road, render_manifest, objects, pins, failures) -> dict:
    recorded = {
        "placement_sha256": pins["placement_sha256"],
        "traffic_placement_pin": traffic.get("building_placement_sha256"),
        "road_placement_pin": road.get("building_placement_sha256"),
        "road_traffic_recorded_pin": road.get("traffic_recorded_building_placement_sha256"),
        "objects_sha256": pins["objects_sha256"],
        "render_manifest_sha256": pins["render_manifest_sha256"],
        "network_sha256": pins["network_sha256"],
        "visual_obstacle_basis": traffic.get("visual_obstacle_basis"),
    }
    if traffic.get("building_placement_sha256") != pins["placement_sha256"]:
        failures.append("traffic placement pin is stale")
    if road.get("building_placement_sha256") != pins["placement_sha256"]:
        failures.append("road placement pin is stale")
    if road.get("traffic_recorded_building_placement_sha256") != pins["placement_sha256"]:
        failures.append("road traffic_recorded placement pin is stale")
    if traffic.get("source_network_sha256") != pins["network_sha256"]:
        failures.append("traffic source network pin is stale")
    basis = traffic.get("visual_obstacle_basis") or {}
    if basis.get("policy") != UNION_POLICY:
        failures.append("traffic was not recorded against the union obstacle basis")
    if basis.get("route_obstacle_basis") != UNION_POLICY:
        failures.append("traffic routes were not recorded against the union obstacle basis")
    if basis.get("objects_json_sha256") != pins["objects_sha256"]:
        failures.append("traffic obstacle basis objects pin differs from the render manifest pin")
    if basis.get("render_manifest_sha256") != pins["render_manifest_sha256"]:
        failures.append("traffic obstacle basis render manifest pin is stale")
    source_ids = [building["object_id"] for building in objects["buildings"]]
    expected_count = len(source_ids)
    if expected_count == 0:
        failures.append("objects.json building inventory is empty")
    if len(set(source_ids)) != expected_count:
        failures.append("objects.json building inventory has duplicate object IDs")
    if basis.get("rendered_footprint_count") != expected_count:
        failures.append(f"traffic obstacle basis does not cover all {expected_count} rendered footprints")
    manifest_ids = [building["object_id"] for building in render_manifest.get("buildings", [])]
    if not manifest_ids:
        failures.append("render manifest building inventory is empty")
    if len(manifest_ids) != expected_count:
        failures.append("render manifest actual building count differs from the source objects inventory")
    if len(set(manifest_ids)) != len(manifest_ids):
        failures.append("render manifest building inventory has duplicate object IDs")
    if set(manifest_ids) != set(source_ids):
        failures.append("render manifest building IDs differ from the source objects inventory")
    scene_pin = (render_manifest.get("scene") or {})
    if scene_pin.get("objects_json_sha256") != pins["objects_sha256"]:
        failures.append("objects.json does not match the render manifest objects pin")
    pack_source_pin = scene_pin.get("mesh_pack_source_sha256")
    if not pack_source_pin:
        failures.append("render manifest mesh pack source pin is missing")
    if pack_source_pin != traffic.get("mesh_pack_source_sha256"):
        failures.append("traffic mesh pack source differs from the render manifest pack pin")
    if (basis.get("pack_source_sha256") != pack_source_pin
            or basis.get("pack_source_sha256") != traffic.get("mesh_pack_source_sha256")):
        failures.append("traffic obstacle basis pack source differs from the render manifest or traffic source pin")
    if render_manifest.get("counts", {}).get("buildings") != expected_count:
        failures.append("render manifest building count differs from the source objects inventory")
    return recorded


def check_footprint_penetration(traffic, footprints, failures) -> dict:
    from shapely import affinity
    from shapely.geometry import Point, box
    from shapely.strtree import STRtree

    tree = STRtree(footprints)
    counts = {"vehicle_point": 0, "vehicle_box": 0, "person_point": 0, "person_circle": 0}
    offenders: dict[str, set[str]] = {key: set() for key in counts}
    for frame in traffic["frames"]:
        for sample in frame["vehicles"]:
            point = Point(sample[1], sample[2])
            if any(footprints[index].covers(point) for index in tree.query(point)):
                counts["vehicle_point"] += 1
                offenders["vehicle_point"].add(sample[0])
            width, length = VEHICLE_DIMENSIONS[sample[4]]
            body = affinity.translate(affinity.rotate(
                box(-width / 2, -length / 2, width / 2, length / 2), sample[3], origin=(0, 0)),
                xoff=sample[1], yoff=sample[2])
            if any(footprints[index].intersection(body).area > VEHICLE_OVERLAP_M2
                   for index in tree.query(body)):
                counts["vehicle_box"] += 1
                offenders["vehicle_box"].add(sample[0])
        for sample in frame["persons"]:
            point = Point(sample[1], sample[2])
            if any(footprints[index].covers(point) for index in tree.query(point)):
                counts["person_point"] += 1
                offenders["person_point"].add(sample[0])
            proxy = point.buffer(PEDESTRIAN_RADIUS_M)
            if any(footprints[index].intersection(proxy).area > PEDESTRIAN_OVERLAP_M2
                   for index in tree.query(proxy)):
                counts["person_circle"] += 1
                offenders["person_circle"].add(sample[0])
    for key, count in counts.items():
        if count:
            failures.append(f"{count} {key} samples inside rendered building footprints")
    return {"samples": counts,
            "offenders": {key: sorted(value) for key, value in offenders.items()}}


def check_surface_coverage(traffic, surfaces, failures) -> dict:
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    tree = STRtree(surfaces)
    summary = {"vehicle_off_surface": 0, "person_off_surface": 0,
               "max_vehicle_distance_m": 0.0, "max_person_distance_m": 0.0}
    offenders: dict[str, set[str]] = {"vehicle": set(), "person": set()}

    def distance(point):
        candidates = [index for index in tree.query(point.buffer(2.0))]
        if not candidates:
            return 999.0
        return min(surfaces[index].distance(point) for index in candidates)

    for frame in traffic["frames"]:
        for sample in frame["vehicles"]:
            point = Point(sample[1], sample[2])
            if not any(surfaces[index].covers(point) for index in tree.query(point)):
                gap = distance(point)
                summary["max_vehicle_distance_m"] = max(summary["max_vehicle_distance_m"], round(gap, 4))
                if gap > SURFACE_TOLERANCE_M:
                    summary["vehicle_off_surface"] += 1
                    offenders["vehicle"].add(sample[0])
        for sample in frame["persons"]:
            point = Point(sample[1], sample[2])
            if not any(surfaces[index].covers(point) for index in tree.query(point)):
                gap = distance(point)
                summary["max_person_distance_m"] = max(summary["max_person_distance_m"], round(gap, 4))
                if gap > SURFACE_TOLERANCE_M:
                    summary["person_off_surface"] += 1
                    offenders["person"].add(sample[0])
    if summary["vehicle_off_surface"]:
        failures.append(f"{summary['vehicle_off_surface']} vehicle samples off the road/walkbed")
    if summary["person_off_surface"]:
        failures.append(f"{summary['person_off_surface']} person samples off the road/walkbed")
    summary["offenders"] = {key: sorted(value) for key, value in offenders.items()}
    return summary


def check_class_preservation(traffic, failures) -> dict:
    vehicle_types: dict[str, str] = {}
    person_ids: set[str] = set()
    for frame in traffic["frames"]:
        for sample in frame["vehicles"]:
            vehicle_types[sample[0]] = sample[4]
        for sample in frame["persons"]:
            person_ids.add(sample[0])
    displayed_bicycles = sorted(vid for vid, kind in vehicle_types.items() if kind == "bicycle")
    if len(vehicle_types) < MIN_DISPLAYED_VEHICLES:
        failures.append(f"fewer than {MIN_DISPLAYED_VEHICLES} vehicles remain in the displayed traffic preview")
    if not displayed_bicycles:
        failures.append("no bicycles remain in the displayed traffic preview")
    if not person_ids:
        failures.append("no pedestrians remain in the displayed traffic preview")
    return {"displayed_vehicle_count": len(vehicle_types),
            "displayed_bicycle_ids": displayed_bicycles,
            "displayed_person_count": len(person_ids)}


def check_route_legality(traffic, lanes, edges, failures) -> dict:
    summary = {"vehicle_lane_violations": [], "person_edge_violations": [],
               "missing_lane_ids": [], "missing_person_edges": []}
    observed_types: dict[str, str] = {}
    for frame in traffic["frames"]:
        for sample in frame["vehicles"]:
            observed_types[sample[0]] = sample[4]
    for vehicle_id, used_lanes in sorted((traffic.get("vehicle_lane_use") or {}).items()):
        vehicle_class = VEHICLE_VCLASS.get(observed_types.get(vehicle_id, ""))
        for lane_id in used_lanes:
            lane = lanes.get(lane_id)
            if lane is None:
                summary["missing_lane_ids"].append(lane_id)
                continue
            if vehicle_class is not None and not lane_id.startswith(":") and not lane_allows(lane, vehicle_class):
                summary["vehicle_lane_violations"].append(f"{vehicle_id}:{lane_id}")
    for bicycle_id, used_lanes in sorted((traffic.get("bicycle_lane_use") or {}).items()):
        for lane_id in used_lanes:
            lane = lanes.get(lane_id)
            if lane is None:
                summary["missing_lane_ids"].append(f"{bicycle_id}:{lane_id}")
                continue
            if not lane_id.startswith(":") and not lane_allows(lane, "bicycle"):
                summary["vehicle_lane_violations"].append(f"{bicycle_id}:{lane_id}")
    for person_id, edge_id in sorted((traffic.get("person_route_edges") or {}).items()):
        edge = edges.get(edge_id)
        if edge is None or edge.get("function") == "internal":
            summary["missing_person_edges"].append(f"{person_id}:{edge_id}")
            continue
        if not any(lane_allows(lane, "pedestrian") for lane in edge.findall("lane")):
            summary["person_edge_violations"].append(f"{person_id}:{edge_id}")
    cycle_indices = traffic.get("paved_bicycle_cycle_indices") or []
    if len(cycle_indices) != 6 or len(set(cycle_indices)) != 6 or any(i < 0 for i in cycle_indices):
        failures.append("painted bicycle cycle selection is not six distinct cycles")
    if traffic.get("sumo_colliding_vehicle_ids"):
        failures.append("SUMO reported colliding vehicle ids")
    for key in ("missing_lane_ids", "missing_person_edges", "vehicle_lane_violations", "person_edge_violations"):
        if summary[key]:
            failures.append(f"{len(summary[key])} route legality violations in {key}")
    summary["paved_bicycle_cycle_indices"] = list(cycle_indices)
    summary["sumo_colliding_vehicle_ids"] = list(traffic.get("sumo_colliding_vehicle_ids") or [])
    return summary


def check_signal_timing(traffic, network, tls_ids, failures) -> dict:
    programs: dict[str, tuple[float, list[tuple[float, str]], set[str], float]] = {}
    for tl in network.findall("tlLogic"):
        phases = [(float(phase.get("duration")), phase.get("state")) for phase in tl.findall("phase")]
        cycle = sum(duration for duration, _ in phases)
        programs[tl.get("id")] = (float(tl.get("offset", "0")), phases,
                                  {state for _, state in phases}, cycle)
    inventory_links: dict[str, int] = {}
    for signal in traffic.get("signals", []):
        inventory_links[signal["tls"]] = max(inventory_links.get(signal["tls"], -1), signal["link"])
    summary = {"tls_count": len(tls_ids), "state_samples": 0, "transitions": 0,
               "unknown_tls": [], "state_not_in_program": 0, "length_mismatches": 0,
               "transition_off_boundary": 0, "state_at_wrong_time": 0}
    previous: dict[str, str] = {}
    for frame in traffic["frames"]:
        states = frame.get("tls", {})
        for tls_id in tls_ids:
            if tls_id not in states:
                summary["unknown_tls"].append(f"missing tls {tls_id} at {frame['second']}")
        for tls_id, state in states.items():
            summary["state_samples"] += 1
            program = programs.get(tls_id)
            if program is None:
                summary["unknown_tls"].append(f"unknown tls {tls_id}")
                continue
            offset, phases, known_states, cycle = program
            if state not in known_states:
                summary["state_not_in_program"] += 1
            if cycle <= 0:
                raise ValueError(f"Network traffic light {tls_id!r} has no positive phase cycle")

            def expected_state(second: float) -> str:
                time_in_cycle = (second + offset) % cycle
                boundary = 0.0
                for duration, phase_state in phases:
                    boundary += duration
                    if time_in_cycle < boundary - 1e-9:
                        return phase_state
                return phases[0][1]

            second = float(frame["second"])
            # TraCI may retain the phase that ended during its .25 s integration
            # step. Membership in a phase program alone cannot validate timing.
            valid_at_sample = {expected_state(second),
                               expected_state(max(0.0, second - FRAME_STEP_SECONDS + 1e-8))}
            if state not in valid_at_sample:
                summary["state_at_wrong_time"] += 1
            if len(state) not in {len(known) for known in known_states}:
                summary["length_mismatches"] += 1
            elif inventory_links.get(tls_id, -1) + 1 > len(state):
                summary["length_mismatches"] += 1
            prior = previous.get(tls_id)
            if prior is not None and prior != state:
                summary["transitions"] += 1
                time_in_cycle = (float(frame["second"]) + offset) % cycle if cycle else 0.0
                boundary = 0.0
                distance = float("inf")
                for duration, _ in phases:
                    gap = min(abs(time_in_cycle - boundary), cycle - abs(time_in_cycle - boundary))
                    distance = min(distance, gap)
                    boundary += duration
                gap = min(abs(time_in_cycle - boundary), cycle - abs(time_in_cycle - boundary))
                distance = min(distance, gap)
                if distance > FRAME_STEP_SECONDS + 1e-6:
                    summary["transition_off_boundary"] += 1
            previous[tls_id] = state
    if summary["unknown_tls"]:
        failures.append("recorded tls states do not cover the network traffic lights")
    if summary["state_not_in_program"]:
        failures.append("recorded tls states outside the network phase programs")
    if summary["length_mismatches"]:
        failures.append("recorded tls state lengths contradict the network programs")
    if summary["transition_off_boundary"]:
        failures.append("recorded tls transitions off the network phase boundaries")
    if summary["state_at_wrong_time"]:
        failures.append("recorded tls states contradict the network phase at the sample time")
    summary["unknown_tls"] = summary["unknown_tls"][:10]
    return summary


def audit(traffic, road, objects, render_manifest, network, pins) -> dict:
    """Run every independent check; returns a machine-readable report with ok=True only
    when placement pins are current and zero true-footprint penetrations remain."""
    failures: list[str] = []
    lanes, edges, tls_ids = network_lane_index(network)
    footprints = footprint_polygons(objects, failures)
    surfaces = surface_polygons(road, failures)
    report = {
        "pins": check_pins(traffic, road, render_manifest, objects, pins, failures),
        "footprint_penetration": check_footprint_penetration(traffic, footprints, failures),
        "surface_coverage": check_surface_coverage(traffic, surfaces, failures),
        "class_preservation": check_class_preservation(traffic, failures),
        "route_legality": check_route_legality(traffic, lanes, edges, failures),
        "signal_timing": check_signal_timing(traffic, network, tls_ids, failures),
        "rendered_footprints_checked": len(footprints),
        "surface_polygons_checked": len(surfaces),
        "failures": failures,
    }
    report["ok"] = not failures
    return report


def load_artifacts(scene_path: Path, network_path: Path,
                   rendered_objects_path: Path, render_manifest_path: Path,
                   road_path: Path | None = None, traffic_path: Path | None = None) -> dict:
    from city_preview_scene import read_scene_paths

    paths = read_scene_paths(scene_path)
    traffic = json.loads((traffic_path or paths.traffic).read_text())
    road = json.loads((road_path or paths.road).read_text())
    objects = json.loads(rendered_objects_path.read_text())
    render_manifest = json.loads(render_manifest_path.read_text())
    network = ET.parse(network_path).getroot()
    pins = {
        "placement_sha256": sha256_file(paths.placement),
        "objects_sha256": sha256_file(rendered_objects_path),
        "render_manifest_sha256": sha256_file(render_manifest_path),
        "network_sha256": sha256_file(network_path),
    }
    return {"traffic": traffic, "road": road, "objects": objects,
            "render_manifest": render_manifest, "network": network, "pins": pins}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", type=Path, required=True)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--rendered-objects", type=Path, required=True)
    parser.add_argument("--render-objects-manifest", type=Path, required=True)
    parser.add_argument("--road", type=Path, help="Audit a staged road file before publication")
    parser.add_argument("--traffic", type=Path, help="Audit a staged SUMO recording before publication")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    arguments = parser.parse_args()
    artifacts = load_artifacts(arguments.scene, arguments.network,
                               arguments.rendered_objects, arguments.render_objects_manifest,
                               arguments.road, arguments.traffic)
    result = audit(artifacts["traffic"], artifacts["road"], artifacts["objects"],
                   artifacts["render_manifest"], artifacts["network"], artifacts["pins"])
    serialized = json.dumps(result, indent=2)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(serialized + "\n")
    print(serialized)
    raise SystemExit(0 if result["ok"] else 1)
