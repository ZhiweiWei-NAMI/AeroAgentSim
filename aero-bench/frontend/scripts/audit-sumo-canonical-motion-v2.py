#!/usr/bin/env python3
"""Verify v2 canonical motion against actual source/GLB geometry and native SUMO records.

This entrypoint accepts the explicit v2 contract only. The historical v1 union
checker remains unchanged and does not define the current rendered scene.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from city_ground_fleet import BODY_DIMENSIONS as VEHICLE_DIMENSIONS, GROUND_FLEET, fleet_width_contract
from city_native_route_capabilities import active_ground_fleet_types, native_route_capabilities
VEHICLE_VCLASS = {name:item.vehicle_class for name,item in GROUND_FLEET.items()}
VEHICLE_OVERLAP_M2 = 1e-11
PEDESTRIAN_RADIUS_M = .3
PEDESTRIAN_OVERLAP_M2 = 1e-11
SURFACE_TOLERANCE_M = .01
ROOT = Path(__file__).resolve().parents[2]
FRAME_STEP_SECONDS = .25
SUMO_IMAGE = "sha256:6974eeb6110526b9f65c6766ff725cefa9bd48e856ebed6fd828b3c3ea6d80cd"
# First line of `sumo --version` printed by the pinned image.
SUMO_VERSION_LINE = "Eclipse SUMO sumo 1.27.1"


def lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed = lane.get("allow", "").split()
    denied = lane.get("disallow", "").split()
    return (not allowed or "all" in allowed or vehicle_class in allowed) and vehicle_class not in denied and "all" not in denied


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
            failures.append(f"{count} {key} samples contacting actual rendered buildings or effective fixtures")
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
            nearest = tree.nearest(point)
            if nearest is None:
                raise ValueError("Actual displayed surface geometry is empty")
            return surfaces[int(nearest)].distance(point)
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
    active_types = active_ground_fleet_types(traffic["demand"]["authored"])
    requirements = traffic["display_requirements"]
    require(all(type(requirements.get(key)) is int and requirements[key] >= 0 for key in
            ("minimum_displayed_vehicle_count", "minimum_displayed_person_count")), "Display requirements must be explicitly configured nonnegative counts")
    minimum_vehicles = requirements["minimum_displayed_vehicle_count"]
    missing_types = sorted(active_types - set(vehicle_types.values()))
    if missing_types:
        failures.append(f"declared authored fleet types absent from displayed native traffic: {missing_types}")
    if len(vehicle_types) < minimum_vehicles:
        failures.append(f"fewer than {minimum_vehicles} vehicles remain in the displayed traffic preview")
    if "bicycle" in active_types and not displayed_bicycles:
        failures.append("no bicycles remain in the displayed traffic preview")
    if len(person_ids) < requirements["minimum_displayed_person_count"]:
        failures.append("no pedestrians remain in the displayed traffic preview")
    return {"displayed_vehicle_count": len(vehicle_types),
            "displayed_bicycle_ids": displayed_bicycles, "missing_displayed_fleet_types": missing_types, "configured_active_vehicle_types":sorted(active_types),
            "configured_display_requirements":requirements,
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
            if vehicle_class is not None and not lane_allows(lane, vehicle_class):
                summary["vehicle_lane_violations"].append(f"{vehicle_id}:{lane_id}")
    for bicycle_id, used_lanes in sorted((traffic.get("bicycle_lane_use") or {}).items()):
        for lane_id in used_lanes:
            lane = lanes.get(lane_id)
            if lane is None:
                summary["missing_lane_ids"].append(f"{bicycle_id}:{lane_id}")
                continue
            if not lane_allows(lane, "bicycle"):
                summary["vehicle_lane_violations"].append(f"{bicycle_id}:{lane_id}")
    for person_id, edge_id in sorted((traffic.get("person_route_edges") or {}).items()):
        edge = edges.get(edge_id)
        if edge is None or edge.get("function") == "internal":
            summary["missing_person_edges"].append(f"{person_id}:{edge_id}")
            continue
        if not any(lane_allows(lane, "pedestrian") for lane in edge.findall("lane")):
            summary["person_edge_violations"].append(f"{person_id}:{edge_id}")
    cycle_indices = traffic["paved_bicycle_cycle_indices"]
    cycle_count = traffic["paved_bicycle_cycle_count"]
    if type(cycle_count) is not int or cycle_count < 0 or len(cycle_indices) != cycle_count \
            or len(set(cycle_indices)) != cycle_count or any(type(i) is not int or i < 0 for i in cycle_indices):
        failures.append("painted bicycle cycle selection differs from the explicit configured distinct cycle count")
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


NATIVE_SCHEMA = "aero-bench.city-sumo-native-recording/v1"
PUBLIC_SCHEMA = "aero-bench.city-sumo-preview/v2"
INPUT_SCHEMA = "aero-bench.city-sumo-recording-inputs/v2"
NATIVE_VEHICLE_FIELDS = ["id", "front_x_m", "front_y_m", "clockwise_from_north_deg", "type",
                         "native_z_m", "length_m", "width_m"]
NATIVE_PERSON_FIELDS = ["id", "x_m", "y_m", "clockwise_from_north_deg", "native_z_m"]
PUBLIC_VEHICLE_FIELDS = ["id", "center_east_m", "center_south_m", "clockwise_from_north_deg", "type", "render_up_m"]
PUBLIC_PERSON_FIELDS = ["id", "east_m", "south_m", "clockwise_from_north_deg", "render_up_m"]
PRIVATE_KEYS = {"vehicle_lane_use", "bicycle_lane_use", "person_route_edges", "recording_inputs_sha256",
                "excluded_static_or_motor_crossing_walk_edges", "paved_bicycle_cycle_indices",
                "sumo_colliding_vehicle_ids"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_workspace_demand_authoring(traffic: dict, native: dict, inputs: dict,
                                     workspace_bytes: bytes | None) -> dict | None:
    """Bind an authored preview claim to the exact validated workspace bytes."""
    claims = (
        traffic.get("demand_authoring"),
        native.get("demand_authoring"),
        inputs.get("recording_parameters", {}).get("authoring_source"),
    )
    if workspace_bytes is None:
        require(all(claim is None for claim in claims),
                "A workspace-authored demand claim requires an explicit workspace audit input")
        return None
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from aero_bench.authoring.traffic_preview import parse_workspace_preview_demand
    from aero_bench.providers.rpc import ProviderRpcError
    from pydantic import ValidationError
    try:
        demand = parse_workspace_preview_demand(workspace_bytes)
    except (ProviderRpcError, ValidationError) as error:
        raise ValueError(f"Workspace demand input failed its current contract: {error}") from error
    expected = demand.source_identity()
    require(all(claim == expected for claim in claims),
            "Native, public, or materialized demand authoring differs from the exact workspace")
    authored = native["demand"]["authored"]
    require(native.get("seed") == traffic.get("seed") == demand.seed
            and sum(count for kind, count in authored.items() if kind != "bicycle") == demand.motor_vehicles
            and authored.get("bicycle", 0) == demand.bicycles
            and native["demand"]["persons"] == traffic["demand"]["persons"] == demand.pedestrians,
            "Recorded seed or authored route census differs from the workspace demand")
    return expected


def native_inventory(native: dict) -> tuple[dict[str, str], set[str], dict[str, int]]:
    """Count real observed identities from native rows, rather than the header."""
    vehicles, persons = {}, set()
    for frame in native["frames"]:
        for row in frame["vehicles"]:
            require(isinstance(row, list) and len(row) == 8 and isinstance(row[0], str)
                    and row[4] in VEHICLE_VCLASS
                    and all(finite(row[i]) for i in (1, 2, 3, 5, 6, 7))
                    and row[5] == 0 and row[6] > 0 and row[7] > 0,
                    "Malformed or elevated native TraCI vehicle observation")
            require(row[0] not in vehicles or vehicles[row[0]] == row[4], "Native vehicle changed its type")
            vehicles[row[0]] = row[4]
        for row in frame["persons"]:
            require(isinstance(row, list) and len(row) == 5 and isinstance(row[0], str)
                    and all(finite(row[i]) for i in (1, 2, 3, 4)) and row[4] == 0,
                    "Malformed or elevated native TraCI pedestrian observation")
            persons.add(row[0])
    counts = {"vehicles":len(vehicles), "persons":len(persons)}
    for kind in set(vehicles.values()):
        counts[kind] = sum(value == kind for value in vehicles.values())
    return vehicles, persons, counts


def check_native_trace(traffic: dict, native: dict, projection, native_signals: list,
                       canonical_signals: list, context: dict, basis: dict, inputs: dict,
                       pins: dict) -> dict:
    """Independently reconstruct every displayed row from its actual native record."""
    require(traffic.get("schema_version") == PUBLIC_SCHEMA and native.get("schema_version") == NATIVE_SCHEMA,
            "Only explicit public v2 and native v1 recording contracts are accepted")
    for document in (native, traffic):
        require(document.get("artifact_class") == "offline-engineering-preview"
                and document.get("source_kind") == "offline-sumo-engineering-preview",
                "Preview must declare its offline engineering class")
        require(document.get("source_context") == context and document.get("visual_obstacle_basis") == basis,
                "Recording/rendered source context or motion obstacle basis drifted")
        require(document.get("source_network_sha256") == context["source_network_sha256"]
                and document.get("source_osm_sha256") == context["source_osm_sha256"]
                and document.get("mesh_pack_source_sha256") == context["mesh_pack_source_sha256"],
                "Recording source pins differ from the actual current inputs")
    require(not (PRIVATE_KEYS & traffic.keys()), "Provider private state was published to the viewer trace")
    require(inputs.get("schema_version") == INPUT_SCHEMA and inputs.get("source_context") == context
            and inputs.get("visual_obstacle_basis") == basis, "Materialized recording input identity drifted")
    parameters = inputs["recording_parameters"]
    require(parameters.get("fleet_width_contract") == fleet_width_contract(), "Materialized fleet dimensions differ from the current native/displayed contract")
    active_types=active_ground_fleet_types(parameters["authored_vehicle_counts"])
    require(native["demand"]["authored"] == parameters["authored_vehicle_counts"], "Native authored demand differs from its configured counts")
    require(native.get("display_requirements") == traffic.get("display_requirements") == parameters["display_requirements"]
            and native.get("paved_bicycle_cycle_count") == parameters["paved_bicycle_cycle_count"],
            "Native/public configured display/cycle requirements drifted")
    require(native.get("recording_inputs_sha256") == pins["recording_inputs_sha256"],
            "Native record differs from the actual materialized recording inputs")
    require(inputs.get("expected_route_sha256") == pins["route_sha256"], "Preserved native route differs from its real preflight bytes")
    require(native.get("route_sha256") == pins["route_sha256"], "Native route bytes differ from the preserved actual route")
    require(native.get("sumo_image_id") == SUMO_IMAGE and native.get("sumo_version") == SUMO_VERSION_LINE,
            "Native recording is not from the pinned SUMO backend/version")
    require(native.get("vehicle_position_reference") == "native-TraCI-front-bumper"
            and native.get("vehicle_fields") == NATIVE_VEHICLE_FIELDS and native.get("person_fields") == NATIVE_PERSON_FIELDS,
            "Native position reference or field contract drifted")
    require(traffic.get("vehicle_position_reference") == "center-derived-from-native-TraCI-front-bumper-and-length"
            and traffic.get("vehicle_fields") == PUBLIC_VEHICLE_FIELDS and traffic.get("person_fields") == PUBLIC_PERSON_FIELDS,
            "Public center/axis/unit field contract drifted")
    require(traffic.get("projection") == projection.metadata(), "Public canonical ENU origin/axes/units/heading identity drifted")
    require(native.get("signals") == native_signals and traffic.get("signals") == canonical_signals,
            "Native/canonical curb-derived signal inventory drifted or poles were relocated")
    require(traffic.get("signal_pole_placement") == {
        "policy":"native-curb-derived-source-placement-with-explicit-effective-omissions",
        "source_signal_count":len(canonical_signals), "extra_relocated_count":0}, "Signal source placement policy drifted")
    require(native.get("step_seconds") == FRAME_STEP_SECONDS
            and native.get("step_seconds") == parameters["step_seconds"]
            and native.get("duration_seconds") == parameters["duration_seconds"]
            and native.get("seed") == parameters["seed"]
            and native.get("paved_bicycle_cycle_indices") == parameters["bicycle_cycle_indices"]
            and native.get("excluded_static_or_motor_crossing_walk_edges") == parameters["excluded_walk_edges"]
            and native.get("demand_departure_offsets_seconds") == parameters["departure_offsets_seconds"],
            "Native demand/configuration differs from the actual materialized configuration")
    expected_source = {"schema_version":NATIVE_SCHEMA, "sha256":pins["native_sha256"],
        "size_bytes":pins["native_size_bytes"], "provider":"SUMO-TraCI", "sumo_image_id":native["sumo_image_id"],
        "sumo_version":native["sumo_version"], "source_network_sha256":context["source_network_sha256"],
        "source_osm_sha256":context["source_osm_sha256"], "route_sha256":pins["route_sha256"],
        "recording_inputs_sha256":pins["recording_inputs_sha256"], "seed":native["seed"],
        "duration_seconds":native["duration_seconds"], "step_seconds":native["step_seconds"]}
    require(traffic.get("native_motion_source") == expected_source, "Public motion provenance does not bind the actual native bytes")
    for key in ("sumo_image_id", "sumo_version", "seed", "duration_seconds", "step_seconds", "route_sha256", "demand"):
        require(traffic.get(key) == native.get(key), f"Public/native metadata mismatch: {key}")
    native_types, native_persons, observed = native_inventory(native)
    require(native["demand"]["observed"] == observed, "Native observed census differs from actual TraCI rows")
    require(set(native["vehicle_lane_use"]) == set(native_types), "Native lane evidence omits or invents observed vehicles")
    require(set(native["bicycle_lane_use"]) == {i for i, kind in native_types.items() if kind == "bicycle"},
            "Native bicycle lane evidence does not match its observed bicycle inventory")
    require(native_persons <= set(native["person_route_edges"]), "Native route evidence omits observed pedestrians")
    require(isinstance(traffic.get("frames"), list) and len(traffic["frames"]) == len(native["frames"])
            == round(native["duration_seconds"] / FRAME_STEP_SECONDS) + 1, "Public/native frame census or duration drifted")
    displayed = {row[0] for frame in traffic["frames"] for row in frame["vehicles"]}
    require(displayed <= set(native_types), "Public trace invented a vehicle absent from native TraCI")
    filtering = traffic["visual_vehicle_filter"]
    require(filtering.get("policy") == "native-sumo-whole-actor-routes-with-rendered-static-or-body-pair-contacts-omitted",
            "Public whole-actor omission policy drifted")
    static_ids, pair_ids = set(filtering["excluded_static_overlap_ids"]), set(filtering["excluded_pair_overlap_ids"])
    require(not (static_ids & pair_ids) and static_ids | pair_ids == set(native_types) - displayed
            and filtering["sumo_observed_vehicle_count"] == len(native_types)
            and filtering["displayed_vehicle_count"] == len(displayed), "Whole-actor omission/census metadata drifted")
    samples = {"vehicles":0, "persons":0, "tls":0}
    for index, (public_frame, native_frame) in enumerate(zip(traffic["frames"], native["frames"])):
        require(native_frame["second"] == public_frame["second"] == index * FRAME_STEP_SECONDS,
                "Public/native sample time drifted")
        require(public_frame["tls"] == native_frame["tls"], "Public traffic light states differ from actual TraCI states")
        samples["tls"] += len(native_frame["tls"])
        for kind, expected_length in (("vehicles", 6), ("persons", 5)):
            rows = public_frame[kind]
            require(all(isinstance(row, list) and len(row) == expected_length for row in rows)
                    and all(finite(row[i]) for row in rows for i in (1, 2, 3, expected_length - 1))
                    and len({row[0] for row in rows}) == len(rows), "Malformed or duplicated public actor rows")
            require(len({row[0] for row in native_frame[kind]}) == len(native_frame[kind]), "Duplicated native actor rows")
            expected = []
            for row in native_frame[kind]:
                if kind == "vehicles":
                    if row[0] not in displayed:
                        continue
                    angle = math.radians(row[3])
                    nx, ny = row[1] - math.sin(angle)*row[6]/2, row[2] - math.cos(angle)*row[6]/2
                    east, south = projection.point(nx, ny)
                    expected.append([row[0], east, south, projection.heading(nx, ny, row[3]), row[4]])
                else:
                    east, south = projection.point(row[1], row[2])
                    expected.append([row[0], east, south, projection.heading(row[1], row[2], row[3])])
            require([row[:-1] for row in rows] == expected,
                    f"Public {kind} drift from native position/heading/type or whole-route visibility")
            require(all(finite(row[-1]) for row in rows), "Public render height is not finite")
            samples[kind] += len(rows)
    return {"observed":observed, "displayed_vehicle_count":len(displayed), "samples_checked":samples,
            "position_precision_m":.01, "heading_precision_deg":.1,
            "native_elevation_contract":"ground-only-actual-native-z-equals-zero"}


def check_authored_routes(routes: ET.Element, network: ET.Element, native: dict) -> dict:
    """Check actual authored routes and connection permissions, including hidden actors."""
    lanes, edges, _ = network_lane_index(network)
    types = {element.attrib["id"]:element for element in routes.findall("vType")}
    connections = {}
    for connection in network.findall("connection"):
        connections.setdefault((connection.get("from"), connection.get("to")), []).append(connection)
    census, authored_ids = {}, set()
    for vehicle in routes.findall("vehicle"):
        identifier, kind = vehicle.attrib["id"], vehicle.attrib["type"]
        require(identifier not in authored_ids and kind in VEHICLE_VCLASS, "Duplicate or unsupported authored vehicle")
        authored_ids.add(identifier)
        vtype = types[kind]
        vclass = vtype.attrib["vClass"]
        require(vclass == VEHICLE_VCLASS[kind] and float(vtype.attrib["width"]) == GROUND_FLEET[kind].native_width_m
                and float(vtype.attrib["length"]) == GROUND_FLEET[kind].length_m,
                "Authored SUMO class/dimensions differ from the current fleet contract")
        path = vehicle.find("route").attrib["edges"].split()
        require(bool(path), "Empty authored vehicle route")
        for edge_id in path:
            require(edge_id in edges and edges[edge_id].get("function", "normal") == "normal"
                    and any(lane_allows(lane, vclass) for lane in edges[edge_id].findall("lane")),
                    "Authored route uses a missing or forbidden native edge")
        for first, last in zip(path, path[1:]):
            require(any(lane_allows(edges[first].findall("lane")[int(c.attrib["fromLane"])], vclass)
                        and lane_allows(edges[last].findall("lane")[int(c.attrib["toLane"])], vclass)
                        and (not c.get("via") or lane_allows(lanes[c.get("via")], vclass))
                        for c in connections.get((first, last), [])),
                    "Authored route has a forbidden or missing native connection")
        census[kind] = census.get(kind, 0) + 1
    observed, persons, _ = native_inventory(native)
    require(active_ground_fleet_types(native["demand"]["authored"]) <= set(observed.values()), "Declared authored vehicle family was not actually observed by SUMO")
    require(set(observed) <= authored_ids and census == {kind:count for kind,count in native["demand"]["authored"].items() if count > 0},
            "Authored demand census differs from its real route bytes/observed vehicle IDs")
    for frame in native["frames"]:
        for row in frame["vehicles"]:
            require(row[6] == float(types[row[4]].attrib["length"])
                    and row[7] == float(types[row[4]].attrib["width"]),
                    "Measured native vehicle dimensions differ from its actual authored vType")
    person_routes = {person.attrib["id"]:person.find("walk").attrib["edges"] for person in routes.findall("person")}
    require(len(person_routes) == len(routes.findall("person")) == native["demand"]["persons"]
            and person_routes == native["person_route_edges"] and persons <= set(person_routes),
            "Actual authored pedestrian demand differs from the recorded route identity")
    for edge_id in person_routes.values():
        require(edge_id in edges and any(lane.get("allow") == "pedestrian" for lane in edges[edge_id].findall("lane")),
                "Authored pedestrian route lacks an exclusive pedestrian native lane")
    return {"vehicles":len(authored_ids), "persons":len(person_routes), "by_type":census,
            "observed_native_dimensions_checked":True}


def road_surfaces(road: dict, context: dict) -> tuple[list, dict, tuple]:
    from shapely.strtree import STRtree
    from city_motion_obstacles import road_surface_polygon, validate_recording_road
    validate_recording_road(road, context)
    by_kind = {kind:[road_surface_polygon(part) for part in road[kind]] for kind in ("roadbed", "walkbed")}
    require(all(by_kind.values()), "Road has no actual roadbed or walkbed surfaces")
    trees = {kind:STRtree(parts) for kind, parts in by_kind.items()}
    heights = {"roadbed":road["street_layout"]["road_height_m"],
               "walkbed":road["street_layout"]["sidewalk_height_m"]}
    require(all(finite(value) for value in heights.values()), "Rendered ground plane height is not finite")
    return [*by_kind["roadbed"], *by_kind["walkbed"]], by_kind, (trees, heights)


def check_render_heights(traffic: dict, by_kind: dict, indices_and_heights: tuple) -> dict:
    from shapely.geometry import Point
    trees, heights = indices_and_heights
    counts = {"roadbed":0, "walkbed":0}
    for frame in traffic["frames"]:
        for row in (*frame["vehicles"], *frame["persons"]):
            point = Point(row[1], row[2])
            for kind in ("walkbed", "roadbed"):
                if any(by_kind[kind][i].distance(point) <= SURFACE_TOLERANCE_M
                       for i in trees[kind].query(point.buffer(.011))):
                    require(row[-1] == heights[kind], "Public height differs from its actual rendered ground plane")
                    counts[kind] += 1
                    break
            else:
                raise ValueError("Public actor has no actual rendered ground surface")
    require(traffic.get("ground_snap") == {"source":"actual-v3-road-and-walkbed-render-planes", "samples":counts},
            "Public ground plane summary differs from its real samples")
    return counts


def native_public_poses(native: dict, projection) -> dict:
    """Reconstruct body poses of all observed native actors for private evidence."""
    frames=[]
    for frame in native["frames"]:
        vehicles,persons=[],[]
        for row in frame["vehicles"]:
            angle=math.radians(row[3])
            x,y=row[1]-math.sin(angle)*row[6]/2,row[2]-math.cos(angle)*row[6]/2
            east,south=projection.point(x,y)
            vehicles.append([row[0],east,south,projection.heading(x,y,row[3]),row[4],row[5]])
        for row in frame["persons"]:
            east,south=projection.point(row[1],row[2])
            persons.append([row[0],east,south,projection.heading(row[1],row[2],row[3]),row[4]])
        frames.append({"second":frame["second"],"vehicles":vehicles,"persons":persons})
    return {"frames":frames}


def check_omission_witnesses(traffic: dict, native: dict, projection, obstacles: list) -> dict:
    from city_motion_continuity import continuity_contacts
    proof=continuity_contacts(native_public_poses(native,projection),obstacles)
    require(not proof["unresolved"], "Native body interpolation still has unresolved omission evidence")
    static={record["actor_id"] for record in proof["vehicle_static_contacts"]}
    pairs={tuple(sorted(record["actor_ids"])) for record in proof["vehicle_pair_contacts"]}
    filtering=traffic["visual_vehicle_filter"]
    require(set(filtering["excluded_static_overlap_ids"]) == static, "Static whole-actor omissions lack actual contact witnesses")
    pair_omitted=set(filtering["excluded_pair_overlap_ids"])
    remaining={pair for pair in pairs if not (set(pair) & static)}
    participating=set().union(*(set(pair) for pair in remaining)) if remaining else set()
    require(pair_omitted <= participating and all(set(pair) & pair_omitted for pair in remaining),
            "Pair whole-actor omissions lack actual contact witnesses or leave a body conflict")
    return proof


def audit_paths(*, traffic_path: Path, native_path: Path, routes_path: Path, recording_inputs_path: Path,
                road_path: Path, objects_path: Path, render_manifest_path: Path, pack_path: Path,
                network_path: Path, source_osm_path: Path, fixture_path: Path,
                engineering_inputs_path: Path | None = None,
                workspace_path: Path | None = None) -> dict:
    from city_preview_coordinates import CityEnuProjection, load_canonical_city_geometry
    from city_motion_obstacles import load_effective_fixtures, native_road_geometry_gate
    from city_road_physical_clearance import LANE_CENTRE_LINE_DECIMALS
    from city_motion_continuity import continuity_contacts
    from city_native_signals import canonical_signal_inventory, signal_inventory

    paths={"traffic":traffic_path,"native":native_path,"routes":routes_path,"recording_inputs":recording_inputs_path,
           "road":road_path,"objects":objects_path,"render_manifest":render_manifest_path,"pack":pack_path,
           "network":network_path,"source_osm":source_osm_path,"effective_fixtures":fixture_path}
    if workspace_path is not None:
        paths["workspace"] = workspace_path
    public_root=(ROOT/"frontend/public").resolve()
    require(all(not paths[key].resolve().is_relative_to(public_root) for key in ("native","routes","recording_inputs")),
            "Native Provider records were placed inside public viewer assets")
    byte_inputs={key:path.read_bytes() for key,path in paths.items()}
    identities={key:{"sha256":hashlib.sha256(raw).hexdigest(),"size_bytes":len(raw)} for key,raw in byte_inputs.items()}
    traffic,native,road,inputs=(json.loads(byte_inputs[key]) for key in ("traffic","native","road","recording_inputs"))
    demand_authoring = check_workspace_demand_authoring(
        traffic, native, inputs, byte_inputs.get("workspace"),
    )
    network=ET.fromstring(byte_inputs["network"])
    canonical=load_canonical_city_geometry(objects_path,render_manifest_path,pack_path)
    context=canonical.source_context(network_path,source_osm_path,engineering_inputs_path)
    projection=CityEnuProjection(network,canonical.origin)
    surfaces,by_kind,heights=road_surfaces(road,context)
    signals=canonical_signal_inventory(network,projection)
    fixtures,basis=load_effective_fixtures(fixture_path,context,signals,road["street_lamps"],road["displayed_surface_sha256"])
    obstacles=[*[item.rendered_polygon for item in canonical.footprints],*fixtures]
    basis["obstacle_polygon_count"]=len(obstacles)
    pins={"native_sha256":identities["native"]["sha256"],"native_size_bytes":identities["native"]["size_bytes"],
          "route_sha256":identities["routes"]["sha256"],"recording_inputs_sha256":identities["recording_inputs"]["sha256"]}
    failures=[]
    road_proof=native_road_geometry_gate(network,projection,CityEnuProjection(network,canonical.origin,decimals=LANE_CENTRE_LINE_DECIMALS),
                                         [item.rendered_polygon for item in canonical.footprints],road)
    require(road_proof["status"] == "PASS", "Actual native road geometry contradicted its published physical PASS gate")
    native_trace=check_native_trace(traffic,native,projection,signal_inventory(network),signals,context,basis,inputs,pins)
    require(native.get("projection") == {"name":"native-SUMO-network-projection", "axes":"x-east,y-north,z-up-meters",
            "location":dict(network.find("location").attrib)}, "Native projection metadata differs from actual network bytes")
    capabilities=native_route_capabilities(network,set(inputs["recording_parameters"]["excluded_walk_edges"]),
        inputs["recording_parameters"]["authored_vehicle_counts"],inputs["recording_parameters"]["route_requirements"])
    require(capabilities["status"] == "PASS" and inputs.get("native_route_capabilities") == capabilities,
            "Actual native fleet/multiple-lane/bidirectional/walking capability gate drifted")
    authored=check_authored_routes(ET.fromstring(byte_inputs["routes"]),network,native)
    omissions=check_omission_witnesses(traffic,native,projection,obstacles)
    lanes,edges,tls_ids=network_lane_index(network)
    route_legality=check_route_legality(native,lanes,edges,failures)
    classes=check_class_preservation(traffic,failures)
    signals_result=check_signal_timing(traffic,network,tls_ids,failures)
    render_heights=check_render_heights(traffic,by_kind,heights)
    penetration=check_footprint_penetration(traffic,obstacles,failures)
    coverage=check_surface_coverage(traffic,surfaces,failures)
    continuous=continuity_contacts(traffic,obstacles)
    for key in ("vehicle_static_contacts","vehicle_pair_contacts","person_static_contacts","unresolved"):
        if continuous[key]:
            failures.append(f"Continuous public interpolation failed: {len(continuous[key])} {key}")
    result = {"schema_version":"aero-bench.city-canonical-motion-audit/v2", "artifact_class":"offline-engineering-preview-audit",
            "status":"PASS" if not failures else "FAIL","failures":failures,"inputs":identities,
            "source_context":context,"visual_obstacle_basis":basis,"private_omission_geometry":omissions,"native_trace":native_trace,"authored_routes":authored,"native_road_geometry":road_proof,"native_route_capabilities":capabilities,
            "route_legality":route_legality,"classes":classes,"signals":signals_result,"render_ground_planes":render_heights,
            "static_penetration":penetration,"surface_coverage":coverage,"continuous_public_interpolation":continuous,
            "scope":"actual preserved native recording and public viewer interpolation; not a formal sealed Run"}
    if demand_authoring is not None:
        result["demand_authoring"] = demand_authoring
    return result


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("traffic","native","routes","recording-inputs","road","objects","render-manifest","pack","network","source-osm","effective-fixtures","output"):
        parser.add_argument(f"--{name}",type=Path,required=True)
    parser.add_argument("--engineering-inputs",type=Path)
    parser.add_argument("--workspace",type=Path)
    args=parser.parse_args()
    if args.output.resolve().is_relative_to((ROOT/"frontend/public").resolve()):
        parser.error("Native joins/hidden actor witnesses require a private audit output outside public assets")
    try:
        result=audit_paths(traffic_path=args.traffic,native_path=args.native,routes_path=args.routes,
            recording_inputs_path=args.recording_inputs,road_path=args.road,objects_path=args.objects,
            render_manifest_path=args.render_manifest,pack_path=args.pack,network_path=args.network,
            source_osm_path=args.source_osm,fixture_path=args.effective_fixtures,
            engineering_inputs_path=args.engineering_inputs,workspace_path=args.workspace)
    except (ValueError,KeyError,TypeError,IndexError) as error:
        result={"schema_version":"aero-bench.city-canonical-motion-audit/v2", "status":"FAIL",
                "failures":[f"Explicit current contract rejected: {type(error).__name__}: {error}"]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"status":result["status"],"failures":result["failures"],"output":str(args.output)}))
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
