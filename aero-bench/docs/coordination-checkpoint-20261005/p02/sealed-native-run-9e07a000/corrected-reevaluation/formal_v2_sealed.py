from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from aero_bench.artifacts.contracts import EvidenceReference, SealManifest
from aero_bench.agent.session_contracts import ToolExecutionResult
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.config.loader import BundleReader
from aero_bench.runtime.contracts import SimulationTime, StateSample
from aero_bench.runtime.events import RunEvent
from aero_bench.runtime.ledger import LedgerRecord
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.contracts import (
    DeliveryEvidence,
    InspectionBoundResolution,
    InspectionEvidenceBundle,
    InspectionTaskPackage,
    WorkOrderStatus,
)
from aero_bench.tasks.inspection.runtime_hook import NetworkDeliveryEventPayload
from aero_bench.tasks.inspection.formal_v2_contracts import (
    CameraContextV2,
    CanonicalInspectionReportV2,
    CapturedFrameEvidenceV2,
    DefectDetectionV2,
    DefectTruthSetV2,
    DefectTruthV2,
    DeliveredReportOutcomeV2,
    EvaluationComponentResultV2,
    FormalInspectionEvaluationResultV2,
    InspectionFormalEvidenceBundleV2,
    InspectionFormalPolicyV3,
    InspectionFormalVerifierConfigV3,
    InspectionMissionContextV2,
    InspectionTargetContextV2,
    LaunchPadContextV2,
    LaunchPadGeometryV2,
    PhysicalContactEventV2,
    PhysicalTraceSampleV2,
    PhysicalTraceV2,
    PolygonPrismV2,
    PolygonVertexV2,
    Pose3DV2,
    QuaternionV2,
    TargetBoxGeometryV2,
    UnitVector3V2,
    Vector3V2,
)
from aero_bench.tasks.inspection.formal_v2_verifier import verify_formal_v2
from aero_bench.tasks.inspection.verifier import InspectionVerifier
from aero_bench.verifier.contracts import (
    GoalResult,
    MetricResult,
    VerificationReport,
    validate_report_against_run,
)
from aero_bench.world import frame_math
from aero_bench.world.contracts import RegionGeometry, WorldPackage
from aero_bench.world.resolved import (
    ResolvedPose,
    ResolvedRegion,
    ResolvedScenario,
)


_FLOAT_TOLERANCE = 1e-9
_REGION_PLANE_TOLERANCE_M = 1e-6
_SAFE_CONTACT_KINDS = frozenset({"ground", "launch_pad"})
_CONTACT_KINDS = frozenset(
    {"ground", "launch_pad", "obstacle", "target", "vehicle", "other"}
)
_PHYSICAL_COMPONENT_IDS = frozenset(
    {
        "initial_outside_arrival",
        "armed_after_start",
        "takeoff_agl",
        "trace_coverage",
        "pre_observation_ground_track",
        "geofence_containment",
        "no_fly_avoidance",
        "contact_safety",
        "return_to_launch",
        "landing",
        "stopped_dwell",
        "terminal_disarm",
    }
)


@dataclass(frozen=True, slots=True)
class _CommandWitness:
    tool_id: str
    command_id: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FormalProjection:
    bundle: InspectionFormalEvidenceBundleV2
    evaluation: FormalInspectionEvaluationResultV2
    command_witnesses: tuple[_CommandWitness, ...]
    frame_presentations: tuple["_ModelFramePresentation", ...]
    model_artifact_sequences: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _ModelFramePresentation:
    frame_id: str
    request_sequence: int


def _vector(x: float, y: float, z: float) -> Vector3V2:
    return Vector3V2(x_m=float(x), y_m=float(y), z_m=float(z))


def _quaternion(
    quaternion: frame_math.UnitQuaternion,
) -> QuaternionV2:
    return QuaternionV2(
        w=float(quaternion.w),
        x=float(quaternion.x),
        y=float(quaternion.y),
        z=float(quaternion.z),
    )


def _resolved_transform(pose: ResolvedPose) -> frame_math.RigidTransform:
    quaternion = frame_math.UnitQuaternion(
        w=pose.orientation_enu.qw,
        x=pose.orientation_enu.qx,
        y=pose.orientation_enu.qy,
        z=pose.orientation_enu.qz,
    )
    return frame_math.RigidTransform(
        rotation=quaternion.to_rotation_matrix(),
        translation=frame_math.Vector3(
            pose.position.enu.east_m,
            pose.position.enu.north_m,
            pose.position.enu.up_m,
        ),
    )


def _pose(transform: frame_math.RigidTransform) -> Pose3DV2:
    quaternion = frame_math.UnitQuaternion.from_rotation_matrix(transform.rotation)
    return Pose3DV2(
        position_enu=_vector(
            transform.translation.x,
            transform.translation.y,
            transform.translation.z,
        ),
        orientation_world=_quaternion(quaternion),
    )


def _resolved_pose(pose: ResolvedPose) -> Pose3DV2:
    return _pose(_resolved_transform(pose))


def _sim_time_s(sim_time_ns: int) -> float:
    return float(sim_time_ns) / 1_000_000_000.0


def _selected_launch(scenario: ResolvedScenario):
    matches = tuple(
        item
        for item in scenario.launch_sites
        if item.launch_site_id == scenario.selected_launch_site_id and item.selected
    )
    if len(matches) != 1:
        raise ValueError("ResolvedScenario does not have one selected launch site")
    return matches[0]


def _region_prism(
    region: ResolvedRegion, *, geometry: RegionGeometry
) -> PolygonPrismV2:
    reference = geometry.height_reference
    if reference not in {"enu_up", "amsl"}:
        raise ValueError(f"unsupported formal region vertical reference: {reference}")
    lower_altitudes = tuple(
        vertex.enu.up_m if reference == "enu_up" else vertex.amsl_m
        for vertex in region.lower_vertices
    )
    upper_altitudes = tuple(
        vertex.enu.up_m if reference == "enu_up" else vertex.amsl_m
        for vertex in region.upper_vertices
    )
    if (
        max(lower_altitudes) - min(lower_altitudes) > _REGION_PLANE_TOLERANCE_M
        or max(upper_altitudes) - min(upper_altitudes)
        > _REGION_PLANE_TOLERANCE_M
    ):
        raise ValueError("resolved formal region altitude planes are not level")
    lower_xy = tuple(
        (vertex.enu.east_m, vertex.enu.north_m)
        for vertex in region.lower_vertices
    )
    upper_xy = tuple(
        (vertex.enu.east_m, vertex.enu.north_m)
        for vertex in region.upper_vertices
    )
    if lower_xy != upper_xy:
        raise ValueError("resolved formal region lower and upper footprints differ")
    if lower_xy != tuple((point.east_m, point.north_m) for point in geometry.footprint_enu_m):
        raise ValueError("resolved formal region footprint differs from pinned geometry")
    if any(abs(value - geometry.min_altitude_m) > _REGION_PLANE_TOLERANCE_M for value in lower_altitudes) or any(
        abs(value - geometry.max_altitude_m) > _REGION_PLANE_TOLERANCE_M for value in upper_altitudes
    ):
        raise ValueError("resolved formal region altitude differs from pinned geometry")
    return PolygonPrismV2(
        prism_id=region.region_id,
        vertices_enu=tuple(
            PolygonVertexV2(east_m=float(east), north_m=float(north))
            for east, north in lower_xy
        ),
        vertical_reference=reference,
        min_altitude_m=float(math.fsum(lower_altitudes) / len(lower_altitudes)),
        max_altitude_m=float(math.fsum(upper_altitudes) / len(upper_altitudes)),
    )


def _mission_context(
    *,
    package: InspectionTaskPackage,
    scenario: ResolvedScenario,
    config: InspectionFormalVerifierConfigV3,
    region_geometries: dict[str, RegionGeometry],
) -> InspectionMissionContextV2:
    if config.package_id != scenario.task.package_id:
        raise ValueError("formal verifier package_id differs from ResolvedScenario")
    launch = _selected_launch(scenario)
    if launch.primary_uav_entity_id != config.vehicle_id:
        raise ValueError("formal vehicle_id is not the selected launch UAV")
    entities = {entity.entity_id: entity for entity in scenario.entities}
    vehicle = entities.get(config.vehicle_id)
    if (
        vehicle is None
        or vehicle.kind != "uav"
        or vehicle.state != "dynamic"
        or vehicle.owner_kind != "provider"
    ):
        raise ValueError("formal vehicle is not one provider-owned dynamic UAV")
    px4_provider_ids = {
        provider.provider_id
        for provider in scenario.providers
        if "flight.arm" in provider.capability_ids
    }
    if vehicle.owner_id not in px4_provider_ids:
        raise ValueError("formal vehicle is not owned by the PX4 motion authority")

    sensors = {sensor.sensor_id: sensor for sensor in scenario.sensors}
    sensor = sensors.get(config.sensor_id)
    if (
        sensor is None
        or sensor.kind != "camera"
        or sensor.parent_entity_id != config.vehicle_id
        or sensor.provider_id != vehicle.owner_id
    ):
        raise ValueError("formal sensor is not the selected UAV camera")
    vehicle_to_sensor = _resolved_transform(vehicle.initial_pose).inverse().compose(
        _resolved_transform(sensor.initial_pose)
    )

    targets_by_id = {target.target_id: target for target in scenario.semantic_targets}
    if any(target_id not in targets_by_id for target_id in config.target_ids):
        raise ValueError("formal target_ids name an unknown semantic target")
    arrival_radii: dict[str, float] = {}
    for target_id in config.target_ids:
        radii = {
            order.arrival_tolerance_m
            for order in package.work_orders
            if order.target_id == target_id
        }
        if len(radii) != 1:
            raise ValueError(
                "every formal target requires one canonical work-order arrival radius"
            )
        arrival_radii[target_id] = next(iter(radii))
    if {order.target_id for order in package.work_orders} != set(config.target_ids):
        raise ValueError("formal work orders and configured targets differ")

    targets: list[InspectionTargetContextV2] = []
    for target_id in config.target_ids:
        target = targets_by_id[target_id]
        if target.required_sensor_id != config.sensor_id:
            raise ValueError("formal target requires another sensor")
        if abs(target.dwell_time_s - config.dwell_s) > _FLOAT_TOLERANCE:
            raise ValueError("formal target dwell differs from verifier policy")
        targets.append(
            InspectionTargetContextV2(
                target_id=target.target_id,
                pose_world=_resolved_pose(target.pose),
                box_geometry=TargetBoxGeometryV2(
                    size_x_m=float(target.geometry.size_x_m),
                    size_y_m=float(target.geometry.size_y_m),
                    size_z_m=float(target.geometry.size_z_m),
                ),
                surface_normal_target=UnitVector3V2(
                    x=float(target.surface_normal_target.x),
                    y=float(target.surface_normal_target.y),
                    z=float(target.surface_normal_target.z),
                ),
                arrival_radius_m=float(arrival_radii[target_id]),
                min_observation_range_m=float(target.min_distance_m),
                max_observation_range_m=float(target.max_distance_m),
                max_surface_view_angle_deg=float(target.max_view_angle_deg),
                fov_margin_deg=float(target.fov_margin_deg),
            )
        )

    regions = {region.region_id: region for region in scenario.regions}
    geofences = tuple(
        regions.get(region_id) for region_id in config.geofence_ids
    )
    no_fly = tuple(regions.get(region_id) for region_id in config.no_fly_ids)
    if any(region is None or region.kind != "geofence" for region in geofences):
        raise ValueError("formal geofence_ids do not name resolved geofences")
    if any(region is None or region.kind != "no_fly" for region in no_fly):
        raise ValueError("formal no_fly_ids do not name resolved no-fly regions")

    return InspectionMissionContextV2.issue(
        world_frame="enu",
        scenario_digest=scenario.scenario_digest,
        selected_launch_id=launch.launch_site_id,
        vehicle_id=config.vehicle_id,
        sensor_id=config.sensor_id,
        launch_pad=LaunchPadContextV2(
            launch_id=launch.launch_site_id,
            pose_world=_resolved_pose(launch.pose),
            geometry=LaunchPadGeometryV2(
                radius_m=float(launch.pad_radius_m),
                height_m=float(config.launch_pad_height_m),
            ),
        ),
        camera=CameraContextV2(
            sensor_id=sensor.sensor_id,
            vehicle_id=config.vehicle_id,
            mount_vehicle_to_sensor=_pose(vehicle_to_sensor),
            horizontal_fov_deg=float(sensor.horizontal_fov_deg),
            vertical_fov_deg=float(sensor.vertical_fov_deg),
            resolution_width_px=sensor.resolution_width_px,
            resolution_height_px=sensor.resolution_height_px,
            optical_convention=(
                "camera_forward_plus_x__image_right_plus_y__image_up_plus_z"
            ),
        ),
        targets=tuple(targets),
        geofences=tuple(_region_prism(region, geometry=region_geometries[region.region_id]) for region in geofences if region),
        no_fly_prisms=tuple(_region_prism(region, geometry=region_geometries[region.region_id]) for region in no_fly if region),
        uncertainty=config.uncertainty,
    )


def _state_attributes(sample: StateSample) -> dict[str, object]:
    attributes = {attribute.name: attribute.value for attribute in sample.attributes}
    if len(attributes) != len(sample.attributes):
        raise ValueError("StateSample repeats an attribute")
    return attributes


def _required_bool_attribute(
    attributes: dict[str, object], name: str
) -> bool:
    value = attributes.get(name)
    if type(value) is not bool:
        raise ValueError(f"formal StateSample requires boolean attribute {name}")
    return value


def _parse_contact(contact: str) -> tuple[str, str]:
    kind, separator, counterparty_id = contact.partition(".")
    if not separator or kind not in _CONTACT_KINDS or not counterparty_id:
        raise ValueError("StateSample contact does not use a classified counterpart token")
    return kind, counterparty_id


def _validate_contact_identity(
    *,
    kind: str,
    counterparty_id: str,
    scenario: ResolvedScenario,
) -> None:
    if kind == "ground":
        if counterparty_id != "world":
            raise ValueError("formal ground contact must identify ground.world")
        return
    if kind == "launch_pad":
        if counterparty_id not in {
            launch.launch_site_id for launch in scenario.launch_sites
        }:
            raise ValueError("launch-pad contact names an unknown launch site")
        return
    if kind == "target":
        if counterparty_id not in {
            target.target_id for target in scenario.semantic_targets
        }:
            raise ValueError("target contact names an unknown semantic target")
        return
    if kind == "vehicle":
        if counterparty_id not in {entity.entity_id for entity in scenario.entities}:
            raise ValueError("vehicle contact names an unknown entity")
        return
    if kind == "obstacle":
        known_obstacles = {
            building.building_id for building in scenario.buildings
        } | {entity.entity_id for entity in scenario.entities}
        if counterparty_id not in known_obstacles:
            raise ValueError("obstacle contact names an unknown scenario object")


def _trace_sample(
    *,
    sample: StateSample,
    scenario: ResolvedScenario,
) -> tuple[PhysicalTraceSampleV2, tuple[tuple[str, str], ...]]:
    if sample.angular_velocity_body is None:
        raise ValueError("formal StateSample omits body angular velocity")
    if sample.armed is None:
        raise ValueError("formal StateSample omits armed state")
    attributes = _state_attributes(sample)
    if not _required_bool_attribute(attributes, "contacts_complete"):
        raise ValueError("formal StateSample contact inventory is not complete")
    in_air = _required_bool_attribute(attributes, "in_air")
    landed = _required_bool_attribute(attributes, "landed")
    ground_contact = _required_bool_attribute(attributes, "ground_contact")
    collision_contact = _required_bool_attribute(attributes, "collision_contact")
    landed_state = attributes.get("landed_state")
    if landed_state not in {"ON_GROUND", "IN_AIR", "TAKING_OFF", "LANDING"}:
        raise ValueError("formal StateSample has no authoritative landed state")
    if landed != (landed_state == "ON_GROUND"):
        raise ValueError("formal landed attribute differs from MAVSDK landed state")
    parsed_contacts = tuple(_parse_contact(contact) for contact in sample.contacts)
    for kind, counterparty_id in parsed_contacts:
        _validate_contact_identity(
            kind=kind,
            counterparty_id=counterparty_id,
            scenario=scenario,
        )
    observed_ground_contact = any(
        kind in _SAFE_CONTACT_KINDS for kind, _ in parsed_contacts
    )
    observed_collision_contact = any(
        kind not in _SAFE_CONTACT_KINDS for kind, _ in parsed_contacts
    )
    if ground_contact != observed_ground_contact:
        raise ValueError("ground_contact differs from classified Gazebo contacts")
    if collision_contact != observed_collision_contact:
        raise ValueError("collision_contact differs from classified Gazebo contacts")
    coordinate = sample.pose.position
    angular = sample.angular_velocity_body
    physical = PhysicalTraceSampleV2(
        sequence=sample.at.tick,
        sim_time_s=_sim_time_s(sample.at.sim_time_ns),
        position_enu=_vector(
            coordinate.enu.east_m,
            coordinate.enu.north_m,
            coordinate.enu.up_m,
        ),
        altitude_amsl_m=float(coordinate.amsl_m),
        terrain_altitude_amsl_m=float(coordinate.terrain_amsl_m),
        altitude_agl_m=float(coordinate.agl_m),
        orientation_world=QuaternionV2(
            w=float(sample.pose.orientation_enu.qw),
            x=float(sample.pose.orientation_enu.qx),
            y=float(sample.pose.orientation_enu.qy),
            z=float(sample.pose.orientation_enu.qz),
        ),
        linear_velocity_enu_mps=_vector(
            sample.linear_velocity_enu.east_mps,
            sample.linear_velocity_enu.north_mps,
            sample.linear_velocity_enu.up_mps,
        ),
        angular_velocity_body_rps=_vector(
            angular.x_radps,
            angular.y_radps,
            angular.z_radps,
        ),
        armed=sample.armed,
        in_air=in_air,
        landed=landed,
        ground_contact=ground_contact,
    )
    return physical, parsed_contacts


def _physical_trace(
    *,
    evidence: InspectionEvidenceBundle,
    scenario: ResolvedScenario,
    config: InspectionFormalVerifierConfigV3,
) -> PhysicalTraceV2:
    entity = next(
        (item for item in scenario.entities if item.entity_id == config.vehicle_id),
        None,
    )
    if entity is None or entity.owner_kind != "provider":
        raise ValueError("formal trace vehicle has no provider owner")
    samples: list[PhysicalTraceSampleV2] = []
    contact_sets: list[tuple[tuple[str, str], ...]] = []
    for state in evidence.scene_states:
        matches = tuple(
            sample for sample in state.samples if sample.entity_id == config.vehicle_id
        )
        if len(matches) != 1:
            raise ValueError("each SceneState must contain exactly one formal UAV sample")
        state_sample = matches[0]
        if (
            state_sample.provider_id != entity.owner_id
            or state_sample.sample_kind != "dynamic"
            or state_sample.at != state.at
        ):
            raise ValueError("formal UAV StateSample has the wrong authority")
        physical, contacts = _trace_sample(
            sample=state_sample,
            scenario=scenario,
        )
        samples.append(physical)
        contact_sets.append(contacts)
    if len(samples) < 2:
        raise ValueError("formal physical trace requires at least two SceneStates")

    events: list[PhysicalContactEventV2] = []
    active: set[tuple[str, str]] = set()
    for sample, contacts in zip(samples, contact_sets, strict=True):
        current = set(contacts)
        for kind, counterparty_id in sorted(active - current):
            events.append(
                PhysicalContactEventV2(
                    sequence=sample.sequence,
                    sim_time_s=sample.sim_time_s,
                    counterparty_kind=kind,
                    counterparty_id=counterparty_id,
                    phase="ended",
                )
            )
        for kind, counterparty_id in sorted(current - active):
            events.append(
                PhysicalContactEventV2(
                    sequence=sample.sequence,
                    sim_time_s=sample.sim_time_s,
                    counterparty_kind=kind,
                    counterparty_id=counterparty_id,
                    phase="began",
                )
            )
        active = current
    maximum_gap_s = max(
        samples[index].sim_time_s - samples[index - 1].sim_time_s
        for index in range(1, len(samples))
    )
    return PhysicalTraceV2(
        schema_version="aero-bench.inspection-physical-trace/v2",
        vehicle_id=config.vehicle_id,
        trace_start_sequence=samples[0].sequence,
        trace_end_sequence=samples[-1].sequence,
        trace_start_time_s=samples[0].sim_time_s,
        trace_end_time_s=samples[-1].sim_time_s,
        maximum_gap_s=float(maximum_gap_s),
        contacts_complete=True,
        samples=tuple(samples),
        contact_events=tuple(events),
    )


def _event_payload(event: RunEvent) -> dict[str, object]:
    payload = {item.name: item.value for item in event.payload}
    if len(payload) != len(event.payload):
        raise ValueError("RunEvent repeats a payload field")
    return payload


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("model artifact content_json repeats an object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"model artifact content_json contains invalid constant {value}")


def _canonical_model_artifact_bytes(content_json: object) -> tuple[bytes, object]:
    if not isinstance(content_json, str) or len(content_json) > 16 * 1024 * 1024:
        raise ValueError("model artifact content_json is absent or oversized")
    try:
        parsed = json.loads(
            content_json,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("model artifact content_json is not strict JSON") from error
    if not isinstance(parsed, list):
        raise ValueError("model artifact content_json must be one record list")
    try:
        encoded = canonical_json_bytes(parsed)
    except (TypeError, ValueError) as error:
        raise ValueError("model artifact content_json cannot be canonicalized") from error
    return encoded, parsed


def _validate_model_session_evidence(
    *,
    package: InspectionTaskPackage,
    evidence: InspectionEvidenceBundle,
    run: ResolvedRunSpec,
) -> tuple[tuple[_ModelFramePresentation, ...], tuple[int, ...]]:
    model_session = evidence.model_session
    model_interactions = evidence.model_interactions
    if model_session is None or model_interactions is None:
        return (), ()
    manifest = model_session.manifest
    if manifest.status != "completed":
        raise ValueError("formal Astra session did not complete")
    agent = next(
        (item for item in run.agents if item.agent_id == manifest.agent_id),
        None,
    )
    agent_ids = {
        actor.actor_id for actor in package.actors if actor.role == "agent"
    }
    driver = None if agent is None else agent.driver
    if (
        agent is None
        or manifest.agent_id not in agent_ids
        or driver is None
        or driver.driver_id != manifest.driver_id
    ):
        raise ValueError("model session identity differs from the resolved Agent Driver")
    binding_by_kind = {
        binding.evidence_kind: binding for binding in evidence.bindings
    }
    session_binding = binding_by_kind.get("model_session_manifest")
    interactions_binding = binding_by_kind.get("model_interactions")
    if (
        session_binding is None
        or interactions_binding is None
        or session_binding.producer_id != manifest.driver_id
        or interactions_binding.producer_id != manifest.driver_id
    ):
        raise ValueError("model session artifacts are not owned by the resolved Driver")
    driver_requirements = {
        item.artifact_type: item for item in driver.artifact_requirements
    }
    if (
        driver_requirements.get("model.session-manifest") is None
        or driver_requirements["model.session-manifest"].artifact_id
        != session_binding.artifact_id
        or driver_requirements.get("model.interactions") is None
        or driver_requirements["model.interactions"].artifact_id
        != interactions_binding.artifact_id
    ):
        raise ValueError("model session evidence differs from Agent Driver outputs")

    sensor_by_frame = {frame.frame_id: frame for frame in evidence.sensor_frames}
    archive_by_frame = {frame.frame_id: frame for frame in evidence.camera_frames}
    observation_by_frame = {
        observation.frame_id: observation for observation in evidence.observations
    }
    tool_results: dict[str, tuple[int, ToolExecutionResult]] = {}
    artifact_calls: dict[str, list[tuple[int, bytes]]] = {}
    presentations: list[_ModelFramePresentation] = []
    presented_frame_ids: set[str] = set()
    first_presentation_sequence: dict[str, int] = {}
    for record in model_interactions.records:
        if record.record_type == "tool.result":
            assert record.call_id is not None
            result = ToolExecutionResult.model_validate(record.payload.get("result"))
            tool_results[record.call_id] = (record.sequence, result)
        elif record.record_type == "tool.call":
            name = record.payload.get("name")
            arguments = record.payload.get("arguments")
            if name != "write_json_artifact":
                continue
            if not isinstance(arguments, dict) or set(arguments) != {
                "artifact_id",
                "content_json",
            }:
                raise ValueError("model artifact write arguments are invalid")
            artifact_id = arguments["artifact_id"]
            encoded, _ = _canonical_model_artifact_bytes(arguments["content_json"])
            if not isinstance(artifact_id, str):
                raise ValueError("model artifact write has no artifact identity")
            artifact_calls.setdefault(artifact_id, []).append(
                (record.sequence, encoded)
            )
        elif record.record_type == "model.request":
            image_inputs = record.payload.get("image_inputs")
            if not isinstance(image_inputs, list):
                raise ValueError("model request has no image input inventory")
            for image_input in image_inputs:
                if not isinstance(image_input, dict):
                    raise ValueError("model image presentation is malformed")
                source_call_id = image_input.get("source_call_id")
                frame_id = image_input.get("frame_id")
                source_result = tool_results.get(source_call_id)
                sensor_frame = sensor_by_frame.get(frame_id)
                archive_frame = archive_by_frame.get(frame_id)
                observation = observation_by_frame.get(frame_id)
                if (
                    source_result is None
                    or sensor_frame is None
                    or archive_frame is None
                    or observation is None
                    or source_result[0] >= record.sequence
                ):
                    raise ValueError(
                        "model image presentation lacks prior sealed capture authority"
                    )
                result = source_result[1]
                provenance = result.image_provenance()
                images = [
                    item for item in result.content if item.type == "inputImage"
                ]
                content_index = image_input.get("content_index")
                if (
                    not result.success
                    or provenance is None
                    or len(images) != 1
                    or not isinstance(content_index, int)
                    or isinstance(content_index, bool)
                    or content_index < 0
                    or content_index >= len(result.content)
                    or result.content[content_index].type != "inputImage"
                ):
                    raise ValueError("model image presentation lacks successful image result")
                expected_provenance = {
                    "source_call_id": source_call_id,
                    "content_index": content_index,
                    "operation_id": result.audit.operation_id,
                    "observation_id": result.audit.observation_id,
                    **provenance,
                    "media_type": "image/png",
                    "size_bytes": len(images[0].image_bytes),
                }
                if image_input != expected_provenance:
                    raise ValueError(
                        "model outbound image inventory differs from trusted tool result"
                    )
                if (
                    images[0].image_bytes != archive_frame.png_bytes
                    or sensor_frame.image_sha256 != image_input["image_sha256"]
                    or sensor_frame.size_bytes != image_input["size_bytes"]
                    or sensor_frame.observation_id != image_input["observation_id"]
                    or sensor_frame.payload_digest
                    != image_input["observation_payload_digest"]
                    or observation.payload_digest != sensor_frame.payload_digest
                ):
                    raise ValueError(
                        "model outbound image differs from sealed RGB observation"
                    )
                presentations.append(
                    _ModelFramePresentation(
                        frame_id=frame_id,
                        request_sequence=record.sequence,
                    )
                )
                presented_frame_ids.add(frame_id)
                first_presentation_sequence.setdefault(frame_id, record.sequence)
    if presented_frame_ids != set(sensor_by_frame):
        raise ValueError("Astra model was not sent every sealed inspection RGB frame")

    seal_by_id = {
        artifact.artifact_id: artifact for artifact in evidence.seal.artifacts
    }
    required_artifact_ids = {
        binding_by_kind["detection"].artifact_id,
        binding_by_kind["report"].artifact_id,
    }
    final_first_presentation_sequence = max(first_presentation_sequence.values())
    artifact_sequences: list[int] = []
    artifact_call_sequences: dict[str, int] = {}
    for artifact_id in required_artifact_ids:
        calls = artifact_calls.get(artifact_id, [])
        if len(calls) != 1:
            raise ValueError(
                "Astra must author each detection and report artifact exactly once"
            )
        call_sequence, encoded = calls[0]
        artifact_call_sequences[artifact_id] = call_sequence
        artifact = seal_by_id[artifact_id]
        if (
            call_sequence <= final_first_presentation_sequence
            or hashlib.sha256(encoded).hexdigest() != artifact.sha256
            or len(encoded) != artifact.size_bytes
        ):
            raise ValueError(
                "model artifact write does not match sealed post-image evidence"
            )
        call_record = next(
            record
            for record in model_interactions.records
            if record.record_type == "tool.call"
            and record.sequence == call_sequence
        )
        assert call_record.call_id is not None
        result_record = next(
            (
                record
                for record in model_interactions.records
                if record.record_type == "tool.result"
                and record.call_id == call_record.call_id
            ),
            None,
        )
        if result_record is None:
            raise ValueError("model artifact write lacks a trusted Driver result")
        result = ToolExecutionResult.model_validate(result_record.payload.get("result"))
        expected_result = {
            "artifact_id": artifact_id,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
        }
        try:
            visible_result = json.loads(
                result.content[0].text,
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=_reject_json_constant,
            )
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            raise ValueError("model artifact write result is not strict JSON") from error
        if (
            not result.success
            or result.audit.operation != "write_json_artifact"
            or len(result.content) != 1
            or result.content[0].type != "inputText"
            or visible_result != expected_result
            or result.content[0].text
            != canonical_json_bytes(expected_result).decode("utf-8")
        ):
            raise ValueError("model artifact write was not completed by the Driver")
        artifact_sequences.extend((call_sequence, result_record.sequence))
    if artifact_call_sequences[binding_by_kind["detection"].artifact_id] >= (
        artifact_call_sequences[binding_by_kind["report"].artifact_id]
    ):
        raise ValueError("Astra report artifact was authored before detections")
    return tuple(presentations), tuple(sorted(artifact_sequences))


def _ancestor_event_ids(
    event: RunEvent,
    *,
    events_by_id: dict[str, RunEvent],
) -> set[str]:
    ancestors: set[str] = set()
    pending = list(event.causal_event_ids)
    while pending:
        event_id = pending.pop()
        if event_id in ancestors:
            continue
        ancestor = events_by_id.get(event_id)
        if ancestor is None:
            raise ValueError("command correlation references an unknown RunEvent")
        ancestors.add(event_id)
        pending.extend(ancestor.causal_event_ids)
    return ancestors


def _canonical_proof(event: RunEvent, *, command_id: str, tool_id: str) -> None:
    payload = _event_payload(event)
    if (
        payload.get("command_id") != command_id
        or payload.get("tool_id") != tool_id
        or payload.get("phase") != "completed"
    ):
        raise ValueError("physical effect payload differs from its command")
    proof_json = payload.get("proof_json")
    if not isinstance(proof_json, str):
        raise ValueError("physical effect omits its canonical proof")
    try:
        proof = json.loads(proof_json)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("physical effect proof is not JSON") from error
    if not isinstance(proof, dict):
        raise ValueError("physical effect proof is not an object")
    if (
        proof.get("command_id") != command_id
        or proof.get("tool_id") != tool_id
        or proof.get("phase") != "completed"
        or proof.get("ack_audit_status") != "decoded_command_ack_accepted"
    ):
        raise ValueError("physical effect proof lacks a matched accepted COMMAND_ACK")


def _command_witnesses(
    *,
    evidence: InspectionEvidenceBundle,
    run: ResolvedRunSpec,
    config: InspectionFormalVerifierConfigV3,
) -> tuple[_CommandWitness, ...]:
    events = tuple(record.event for record in evidence.event_ledger.records)
    if not events:
        return ()
    events_by_id = {event.event_id: event for event in events}
    if len(events_by_id) != len(events):
        raise ValueError("event ledger repeats a RunEvent identity")
    vehicle = next(
        entity for entity in run.scenario.entities if entity.entity_id == config.vehicle_id
    )
    tool_bindings = {
        binding.tool_id: binding for binding in run.scenario.task.tools
    }
    for tool_id in config.required_tool_ids:
        binding = tool_bindings.get(tool_id)
        if binding is None or binding.endpoint_id != vehicle.owner_id:
            raise ValueError("formal required tool is not Gateway-bound to the UAV owner")

    calls = tuple(
        event
        for event in events
        if event.interaction is not None
        and event.interaction.interaction_type == "agent.tool_call.v1"
        and isinstance(event.command_id, str)
        and _event_payload(event).get("tool_id") in config.required_tool_ids
    )
    calls_by_tool: dict[str, list[RunEvent]] = {
        tool_id: [] for tool_id in config.required_tool_ids
    }
    for call in calls:
        tool_id = _event_payload(call).get("tool_id")
        assert isinstance(tool_id, str)
        calls_by_tool[tool_id].append(call)
    if any(not calls_by_tool[tool_id] for tool_id in config.required_tool_ids):
        raise ValueError("formal command ledger omits a required physical tool")

    witnesses: list[_CommandWitness] = []
    for tool_id in config.required_tool_ids:
        for call in calls_by_tool[tool_id]:
            command_id = call.command_id
            assert command_id is not None
            command_events = tuple(
                event
                for event in events
                if event.command_id == command_id
                and event.correlation_id == command_id
                and event.interaction is not None
            )
            by_type: dict[str, list[RunEvent]] = {}
            for event in command_events:
                by_type.setdefault(event.interaction.interaction_type, []).append(event)
            if by_type.get("agent.tool_call.v1") != [call]:
                raise ValueError("physical command does not have one Agent tool call")
            gateways = by_type.get("gateway.dispatch.v1", [])
            results = by_type.get("agent.tool_result.v1", [])
            mavlink_commands = by_type.get("mavlink.command.v1", [])
            acknowledgements = by_type.get("mavlink.command_ack.v1", [])
            effects = [
                event
                for event in by_type.get("physical.effect.v1", [])
                if _event_payload(event).get("phase") == "completed"
            ]
            failed_effects = [
                event
                for event in by_type.get("physical.effect.v1", [])
                if _event_payload(event).get("phase") == "failed"
            ]
            if (
                len(gateways) != 1
                or len(results) != 1
                or len(mavlink_commands) != 1
                or len(acknowledgements) != 1
                or len(effects) != 1
                or failed_effects
            ):
                raise ValueError("physical command correlation chain is incomplete")
            gateway = gateways[0]
            result = results[0]
            mavlink_command = mavlink_commands[0]
            acknowledgement = acknowledgements[0]
            effect = effects[0]
            for event in (gateway, result, mavlink_command, acknowledgement, effect):
                payload_tool_id = _event_payload(event).get("tool_id")
                if payload_tool_id is not None and payload_tool_id != tool_id:
                    raise ValueError("physical command correlation changes tool identity")
                if event.provider_id != vehicle.owner_id:
                    raise ValueError("physical command correlation changes provider owner")
            if _event_payload(result).get("outcome") == "failed":
                raise ValueError("Agent tool result records a failed physical command")
            ancestors = _ancestor_event_ids(effect, events_by_id=events_by_id)
            required_ancestors = {
                call.event_id,
                gateway.event_id,
                result.event_id,
                mavlink_command.event_id,
                acknowledgement.event_id,
            }
            if not required_ancestors <= ancestors:
                raise ValueError("physical effect lacks the complete causal command chain")
            if call.sequence >= effect.sequence:
                raise ValueError("physical command effect precedes its Agent tool call")
            _canonical_proof(effect, command_id=command_id, tool_id=tool_id)
            witnesses.append(
                _CommandWitness(
                    tool_id=tool_id,
                    command_id=command_id,
                    event_ids=tuple(
                        event.event_id
                        for event in (
                            call,
                            gateway,
                            result,
                            mavlink_command,
                            acknowledgement,
                            effect,
                        )
                    ),
                )
            )
    return tuple(witnesses)


def _validate_model_disarm_after_stopped_dwell(
    *,
    evidence: InspectionEvidenceBundle,
    trace: PhysicalTraceV2,
    evaluation: FormalInspectionEvaluationResultV2,
    witnesses: tuple[_CommandWitness, ...],
    config: InspectionFormalVerifierConfigV3,
) -> None:
    if evidence.model_session is None:
        return
    components = {
        component.component_id: component for component in evaluation.components
    }
    stopped = components.get("stopped_dwell")
    terminal = components.get("terminal_disarm")
    if (
        config.stopped_dwell_s < 2.0 - _FLOAT_TOLERANCE
        or stopped is None
        or terminal is None
        or not stopped.passed
        or not terminal.passed
        or len(stopped.witness.sample_sequences) != 2
    ):
        raise ValueError(
            "formal Astra disarm requires a verified two-second stopped dwell"
        )
    stopped_end_sequence = stopped.witness.sample_sequences[-1]
    stopped_end = next(
        (
            sample
            for sample in trace.samples
            if sample.sequence == stopped_end_sequence
        ),
        None,
    )
    if stopped_end is None:
        raise ValueError("stopped-dwell witness names an unknown physical sample")
    events_by_id = {
        record.event.event_id: record.event
        for record in evidence.event_ledger.records
    }
    disarm_calls = tuple(
        events_by_id.get(witness.event_ids[0])
        for witness in witnesses
        if witness.tool_id == "flight.disarm"
    )
    if not disarm_calls or not any(
        event is not None
        and _sim_time_s(event.time.sim_time_ns)
        >= stopped_end.sim_time_s - _FLOAT_TOLERANCE
        for event in disarm_calls
    ):
        raise ValueError(
            "Astra did not issue flight.disarm after the verified stopped dwell"
        )


def _public_frame_events(records: tuple[LedgerRecord, ...]) -> dict[str, RunEvent]:
    frames: dict[str, RunEvent] = {}
    for record in records:
        event = record.event
        if (
            event.event_type == "public.sensor-frame"
            and event.interaction is not None
            and event.interaction.interaction_type == "sensor.frame_ref.v2"
            and event.frame_id is not None
        ):
            if event.frame_id in frames:
                raise ValueError("formal RGB frame has repeated RunEvent authority")
            frames[event.frame_id] = event
    return frames


def _captured_frames(
    *,
    evidence: InspectionEvidenceBundle,
    trace: PhysicalTraceV2,
    config: InspectionFormalVerifierConfigV3,
) -> tuple[CapturedFrameEvidenceV2, ...]:
    binding_by_kind = {
        binding.evidence_kind: binding for binding in evidence.bindings
    }
    camera_archive_binding = binding_by_kind.get("camera_frame_data")
    if camera_archive_binding is None or camera_archive_binding.visibility != "public":
        raise ValueError("formal RGB verification requires sealed public camera frame data")
    observations = {
        observation.frame_id: observation for observation in evidence.observations
    }
    archive_frames = {frame.frame_id: frame for frame in evidence.camera_frames}
    sensor_frame_ids = {frame.frame_id for frame in evidence.sensor_frames}
    if (
        not sensor_frame_ids
        or sensor_frame_ids != set(observations)
        or sensor_frame_ids != set(archive_frames)
    ):
        raise ValueError("formal RGB frame inventories are incomplete or inconsistent")
    frame_events = _public_frame_events(evidence.event_ledger.records)
    trace_sequences = {sample.sequence for sample in trace.samples}
    frames: list[CapturedFrameEvidenceV2] = []
    for frame in evidence.sensor_frames:
        observation = observations.get(frame.frame_id)
        archive = archive_frames.get(frame.frame_id)
        frame_event = frame_events.get(frame.frame_id)
        if (
            observation is None
            or archive is None
            or frame_event is None
            or observation.observation_id != frame.observation_id
            or observation.time != frame.time
            or observation.target_id not in config.target_ids
            or observation.camera_id != config.sensor_id
            or frame.camera_id != config.sensor_id
            or frame.vehicle_id != config.vehicle_id
            or frame.time.tick not in trace_sequences
        ):
            raise ValueError(
                "formal RGB frame lacks matching observation, event, or mission identity"
            )
        frames.append(
            CapturedFrameEvidenceV2.issue(
                frame_id=frame.frame_id,
                frame_sequence=frame_event.sequence,
                capture_time_s=_sim_time_s(frame.time.sim_time_ns),
                observation_id=frame.observation_id,
                target_id=observation.target_id,
                sensor_id=config.sensor_id,
                vehicle_id=config.vehicle_id,
                trace_sample_sequence=frame.time.tick,
                distance_m=observation.view.distance_m,
                view_angle_deg=observation.view.view_angle_deg,
                selector=frame.selector,
                engine_sim_time_ns=frame.engine_sim_time_ns,
                logical_origin_engine_ns=frame.logical_origin_engine_ns,
                payload_digest=frame.payload_digest,
                image_sha256=frame.image_sha256,
                size_bytes=frame.size_bytes,
                width=frame.width,
                height=frame.height,
                media_type=frame.media_type,
                source=frame.source,
                capture_pose_source=frame.capture_pose_source,
                camera_pose_sha256=frame.camera_pose_sha256,
                target_pose_sha256=frame.target_pose_sha256,
                image_base64=base64.b64encode(archive.png_bytes).decode("ascii"),
            )
        )
    return tuple(sorted(frames, key=lambda frame: frame.frame_sequence))


def _delivery_commit_time(
    *,
    evidence: InspectionEvidenceBundle,
    delivery: DeliveryEvidence,
    agent_id: str,
) -> SimulationTime | None:
    binding = next(
        item for item in evidence.bindings if item.evidence_kind == "delivery"
    )
    events = [
        record.event
        for record in evidence.event_ledger.records
        if record.event.event_type == "network.delivery"
        and dict((item.name, item.value) for item in record.event.payload).get(
            "message_id"
        ) == delivery.message_id
    ]
    if len(events) != 1:
        raise ValueError("network delivery lacks a unique authoritative commit event")
    event = events[0]
    payload = NetworkDeliveryEventPayload.model_validate(
        {item.name: item.value for item in event.payload}
    )
    if (
        event.source_kind != "provider"
        or event.source != binding.producer_id
        or event.provider_id != binding.producer_id
        or event.workload_id != binding.producer_id
        or event.command_id != payload.command_id
        or event.correlation_id != payload.command_id
        or event.payload_schema_id != payload.schema_id
        or payload.provider_id != binding.producer_id
        or payload.run_id != evidence.run_id
        or payload.agent_id != agent_id
        or payload.source_artifact_id != binding.artifact_id
        or delivery.source_artifact_id != binding.artifact_id
        or payload.work_order_id != delivery.work_order_id
        or payload.payload_digest != delivery.payload_digest
        or SimulationTime(tick=payload.sent_tick, sim_time_ns=payload.sent_time_ns)
        != delivery.sent_at
        or SimulationTime(
            tick=payload.delivered_tick, sim_time_ns=payload.delivered_time_ns
        ) != delivery.delivered_at
        or payload.delivered_tick != event.time.tick
        or payload.delivered_time_ns > event.time.sim_time_ns
    ):
        raise ValueError("network delivery differs from its authoritative commit event")
    completion_id = "internal.complete." + hashlib.sha256(
        canonical_json_bytes(payload.model_dump(mode="json"))
    ).hexdigest()[:32]
    completions = [
        record
        for record in evidence.business_state.history
        if record.command.command.work_order_id == delivery.work_order_id
        and record.command.command.kind == "complete"
    ]
    if (
        len(completions) != 1
    ):
        raise ValueError("Business work order lacks a unique completion command")
    if completions[0].command.command.command_id != completion_id:
        return None
    if (
        completions[0].command.command.issued_at != event.time
        or completions[0].transition.time != event.time
    ):
        raise ValueError("Business completion does not bind the delivery commit event")
    return event.time


def _report_and_network(
    *,
    package: InspectionTaskPackage,
    evidence: InspectionEvidenceBundle,
    frames: tuple[CapturedFrameEvidenceV2, ...],
    config: InspectionFormalVerifierConfigV3,
) -> tuple[CanonicalInspectionReportV2, DeliveredReportOutcomeV2]:
    seal_by_id = {artifact.artifact_id: artifact for artifact in evidence.seal.artifacts}
    binding_by_kind = {
        binding.evidence_kind: binding for binding in evidence.bindings
    }
    report_binding = binding_by_kind.get("report")
    if report_binding is None:
        raise ValueError("formal evidence omits the sealed report artifact")
    report_artifact = seal_by_id[report_binding.artifact_id]
    reports_by_work_order = {
        report.work_order_id: report for report in evidence.report
    }
    package_by_work_order = {
        work_order.work_order_id: work_order for work_order in package.work_orders
    }
    states_by_work_order = {
        state.work_order_id: state
        for state in evidence.business_state.state.work_orders
    }
    deliveries_by_work_order: dict[str, list[DeliveryEvidence]] = {
        work_order_id: [] for work_order_id in package_by_work_order
    }
    for delivery in evidence.deliveries:
        if delivery.work_order_id not in deliveries_by_work_order:
            raise ValueError("formal delivery names a work order outside the package")
        deliveries_by_work_order[delivery.work_order_id].append(delivery)
    if (
        not package_by_work_order
        or set(reports_by_work_order) != set(package_by_work_order)
        or set(states_by_work_order) != set(package_by_work_order)
    ):
        raise ValueError(
            "formal report and Business state must cover every package work order"
        )

    delivered_by_work_order: dict[str, DeliveryEvidence] = {}
    for work_order_id, work_order in package_by_work_order.items():
        report_payload = reports_by_work_order[work_order_id]
        state = states_by_work_order[work_order_id]
        deliveries = deliveries_by_work_order[work_order_id]
        if (
            state.status is not WorkOrderStatus.COMPLETED
            or state.target_id != work_order.target_id
            or state.required_observation_id != work_order.required_observation_id
            or state.claimed_by != report_binding.producer_id
            or state.observation_id != report_payload.observation_id
            or state.report_payload_digest != report_artifact.sha256
            or report_payload.source_artifact_id != report_binding.artifact_id
            or report_payload.run_id != evidence.run_id
            or not deliveries
            or any(
                delivery.run_id != evidence.run_id
                or delivery.payload_digest != report_artifact.sha256
                for delivery in deliveries
            )
        ):
            raise ValueError(
                "formal report, delivery, and completed Business state are inconsistent"
            )
        delivered = tuple(
            delivery for delivery in deliveries if delivery.delivered_at is not None
        )
        if not delivered:
            raise ValueError("formal work order has no actual network delivery")
        completed_deliveries = [
            (delivery, committed_at)
            for delivery in delivered
            if (committed_at := _delivery_commit_time(
                evidence=evidence,
                delivery=delivery,
                agent_id=report_binding.producer_id,
            )) is not None
        ]
        if len(completed_deliveries) != 1:
            raise ValueError("Business completion lacks a unique actual network delivery")
        completed_delivery, committed_at = completed_deliveries[0]
        assert completed_delivery.delivered_at is not None
        if (
            committed_at != state.last_time
            or completed_delivery.delivered_at.sim_time_ns > work_order.upload_deadline_ns
        ):
            raise ValueError(
                "Business completion or actual delivery deadline is inconsistent"
            )
        delivered_by_work_order[work_order_id] = completed_delivery

    formal_frame_by_id = {frame.frame_id: frame for frame in frames}
    qualifying_frame_ids = tuple(sorted(frame.frame_id for frame in frames))
    detections: list[DefectDetectionV2] = []
    for detection in evidence.detections:
        frame = formal_frame_by_id.get(detection.frame_id)
        if (
            frame is None
            or frame.observation_id != detection.observation_id
            or frame.image_sha256 != detection.image_sha256
        ):
            raise ValueError(
                "formal detection does not reference the exact captured RGB frame"
            )
        detections.append(
            DefectDetectionV2(
                detection_id=(
                    f"detection.{detection.work_order_id}.{detection.defect_id}"
                ),
                defect_id=detection.defect_id,
                target_id=detection.target_id,
                frame_id=frame.frame_id,
            )
        )
    sent_at_s = max(
        _sim_time_s(item.sent_at.sim_time_ns)
        for item in delivered_by_work_order.values()
    )
    delivered_at_s = max(
        _sim_time_s(item.delivered_at.sim_time_ns)
        for item in delivered_by_work_order.values()
        if item.delivered_at is not None
    )
    report = CanonicalInspectionReportV2.issue(
        report_id="report.aggregate",
        reported_at_s=float(sent_at_s),
        qualifying_frame_ids=qualifying_frame_ids,
        detections=tuple(
            item.model_dump(mode="json")
            for item in sorted(detections, key=lambda detection: detection.detection_id)
        ),
        transport_artifact_digest=report_artifact.sha256,
        transport_artifact_size_bytes=report_artifact.size_bytes,
    )
    outcome = DeliveredReportOutcomeV2(
        outcome_kind="delivered",
        ownership_kind="provider",
        report_digest=report_artifact.sha256,
        report_size_bytes=report_artifact.size_bytes,
        delivered_at_s=float(delivered_at_s),
    )
    return report, outcome


def _policy(
    *,
    scenario: ResolvedScenario,
    config: InspectionFormalVerifierConfigV3,
) -> InspectionFormalPolicyV3:
    return InspectionFormalPolicyV3(
        schema_version="aero-bench.inspection-formal-policy/v3",
        scenario_digest=scenario.scenario_digest,
        selected_launch_id=scenario.selected_launch_site_id,
        vehicle_id=config.vehicle_id,
        sensor_id=config.sensor_id,
        target_ids=config.target_ids,
        geofence_ids=config.geofence_ids,
        no_fly_ids=config.no_fly_ids,
        minimum_takeoff_agl_m=config.minimum_takeoff_agl_m,
        minimum_horizontal_ground_track_m=(
            config.minimum_horizontal_ground_track_m
        ),
        startup_clearance_margin_m=config.startup_clearance_margin_m,
        minimum_distinct_frames_per_target=(
            config.minimum_distinct_frames_per_target
        ),
        dwell_s=config.dwell_s,
        max_trace_gap_s=config.max_trace_gap_s,
        return_radius_m=config.return_radius_m,
        landing_max_agl_m=config.landing_max_agl_m,
        landing_max_vertical_speed_mps=config.landing_max_vertical_speed_mps,
        stopped_linear_speed_mps=config.stopped_linear_speed_mps,
        stopped_angular_speed_rps=config.stopped_angular_speed_rps,
        stopped_dwell_s=config.stopped_dwell_s,
        terminal_disarm_required=True,
        network_deadline_s=config.network_deadline_s,
        accepted_outcome_kinds=config.accepted_outcome_kinds,
        minimum_buffer_retention_s=config.minimum_buffer_retention_s,
        minimum_detection_f1=config.minimum_detection_f1,
    )


def _project_formal_bundle(
    *,
    package: InspectionTaskPackage,
    evidence: InspectionEvidenceBundle,
    run: ResolvedRunSpec,
    config: InspectionFormalVerifierConfigV3,
    bundle_root: Path,
) -> _FormalProjection:
    if config.package_id != run.task.package.package_id:
        raise ValueError("formal verifier config belongs to another task package")
    world = WorldPackage.model_validate(
        BundleReader(bundle_root).load_document(run.scenario.source_world_package)
    )
    region_geometries = {region.region_id: region.geometry for region in world.regions}
    if set(region_geometries) != {region.region_id for region in run.scenario.regions}:
        raise ValueError("resolved formal region inventory differs from pinned world")
    context = _mission_context(
        package=package,
        scenario=run.scenario,
        config=config,
        region_geometries=region_geometries,
    )
    trace = _physical_trace(
        evidence=evidence,
        scenario=run.scenario,
        config=config,
    )
    witnesses = _command_witnesses(
        evidence=evidence,
        run=run,
        config=config,
    )
    frame_presentations, model_artifact_sequences = _validate_model_session_evidence(
        package=package,
        evidence=evidence,
        run=run,
    )
    frames = _captured_frames(
        evidence=evidence,
        trace=trace,
        config=config,
    )
    report, network = _report_and_network(
        package=package,
        evidence=evidence,
        frames=frames,
        config=config,
    )
    truths = tuple(
        DefectTruthV2(defect_id=item.defect_id, target_id=item.target_id)
        for item in sorted(evidence.truth, key=lambda item: item.defect_id)
    )
    bundle = InspectionFormalEvidenceBundleV2(
        policy=_policy(scenario=run.scenario, config=config),
        mission_context=context,
        physical_trace=trace,
        captured_frames=frames,
        report=report,
        defect_truth=DefectTruthSetV2(
            schema_version="aero-bench.inspection-defect-truth/v2",
            scenario_digest=run.scenario.scenario_digest,
            truths=truths,
        ),
        network_outcome=network,
    )
    evaluation = verify_formal_v2(bundle)
    _validate_model_disarm_after_stopped_dwell(
        evidence=evidence,
        trace=trace,
        evaluation=evaluation,
        witnesses=witnesses,
        config=config,
    )
    return _FormalProjection(
        bundle=bundle,
        evaluation=evaluation,
        command_witnesses=witnesses,
        frame_presentations=frame_presentations,
        model_artifact_sequences=model_artifact_sequences,
    )


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == "ge":
        return value >= threshold
    if operator == "gt":
        return value > threshold
    if operator == "le":
        return value <= threshold
    if operator == "lt":
        return value < threshold
    if operator == "eq":
        return value == threshold
    raise ValueError("formal goal uses an unsupported comparison operator")


def _artifact_id_by_kind(evidence: InspectionEvidenceBundle) -> dict[str, str]:
    by_kind = {binding.evidence_kind: binding.artifact_id for binding in evidence.bindings}
    if len(by_kind) != len(evidence.bindings):
        raise ValueError("formal evidence repeats an evidence-kind binding")
    return by_kind


def _model_report_workflow_sequences(
    evidence: InspectionEvidenceBundle,
) -> tuple[int, ...]:
    if evidence.model_interactions is None:
        return ()
    names = {
        "query_business_work_orders",
        "query_business_work_order",
        "action_business_claim",
        "action_business_submit",
        "action_network_send",
        "write_json_artifact",
    }
    call_ids = {
        record.call_id
        for record in evidence.model_interactions.records
        if record.record_type == "tool.call"
        and record.payload.get("name") in names
        and record.call_id is not None
    }
    return tuple(
        record.sequence
        for record in evidence.model_interactions.records
        if (
            record.record_type == "tool.call"
            and record.call_id in call_ids
        )
        or (
            record.record_type == "tool.result"
            and record.call_id in call_ids
        )
    )


def _deduplicated_references(
    references: list[EvidenceReference],
) -> tuple[EvidenceReference, ...]:
    seen: set[tuple[str, str | None]] = set()
    result: list[EvidenceReference] = []
    for reference in references:
        key = (reference.artifact_id, reference.selector)
        if key in seen:
            continue
        seen.add(key)
        result.append(reference)
    return tuple(result)


def _component_references(
    *,
    component: EvaluationComponentResultV2,
    evidence: InspectionEvidenceBundle,
    projection: _FormalProjection,
    config: InspectionFormalVerifierConfigV3,
) -> tuple[EvidenceReference, ...]:
    artifact = _artifact_id_by_kind(evidence)
    references: list[EvidenceReference] = []
    if component.component_id in _PHYSICAL_COMPONENT_IDS:
        references.extend(
            EvidenceReference(
                artifact_id=artifact["scene_state_history"],
                selector=f"ticks/{sequence}/entities/{config.vehicle_id}",
            )
            for sequence in component.witness.sample_sequences
        )
        references.extend(
            EvidenceReference(
                artifact_id=artifact["event_log"],
                selector=f"events/{event_id}",
            )
            for witness in projection.command_witnesses
            for event_id in witness.event_ids
        )
    if component.component_id == "target_observations":
        references.extend(
            EvidenceReference(
                artifact_id=artifact[kind], selector=f"frames/{frame_id}"
            )
            for frame_id in component.witness.frame_ids
            for kind in ("sensor_frame", "camera_frame_data")
        )
        references.append(
            EvidenceReference(artifact_id=artifact["observation"], selector="observations")
        )
        if "model_interactions" in artifact:
            references.extend(
                EvidenceReference(
                    artifact_id=artifact["model_interactions"],
                    selector=f"records/{presentation.request_sequence}",
                )
                for presentation in projection.frame_presentations
                if presentation.frame_id in component.witness.frame_ids
            )
    if component.component_id == "report_outcome":
        references.extend(
            (
                EvidenceReference(artifact_id=artifact["report"], selector="reports"),
                EvidenceReference(
                    artifact_id=artifact["delivery"], selector="deliveries"
                ),
                EvidenceReference(
                    artifact_id=artifact["business_state"],
                    selector="work-orders",
                ),
            )
        )
        if "model_session_manifest" in artifact:
            references.append(
                EvidenceReference(
                    artifact_id=artifact["model_session_manifest"],
                    selector="session",
                )
            )
            references.extend(
                EvidenceReference(
                    artifact_id=artifact["model_interactions"],
                    selector=f"records/{sequence}",
                )
                for sequence in _model_report_workflow_sequences(evidence)
            )
    if component.component_id == "semantic_f1":
        references.extend(
            (
                EvidenceReference(artifact_id=artifact["truth"], selector="truths"),
                EvidenceReference(
                    artifact_id=artifact["detection"], selector="detections"
                ),
                EvidenceReference(artifact_id=artifact["report"], selector="reports"),
            )
        )
        references.extend(
            EvidenceReference(
                artifact_id=artifact[kind], selector=f"frames/{frame_id}"
            )
            for frame_id in component.witness.frame_ids
            for kind in ("sensor_frame", "camera_frame_data")
        )
        if "model_session_manifest" in artifact:
            references.append(
                EvidenceReference(
                    artifact_id=artifact["model_session_manifest"],
                    selector="session",
                )
            )
            references.extend(
                EvidenceReference(
                    artifact_id=artifact["model_interactions"],
                    selector=f"records/{presentation.request_sequence}",
                )
                for presentation in projection.frame_presentations
                if presentation.frame_id in component.witness.frame_ids
            )
            references.extend(
                EvidenceReference(
                    artifact_id=artifact["model_interactions"],
                    selector=f"records/{sequence}",
                )
                for sequence in projection.model_artifact_sequences
            )
    if not references:
        references.append(
            EvidenceReference(
                artifact_id=artifact["event_log"],
                selector="runtime-chain-root",
            )
        )
    return _deduplicated_references(references)


def _verification_report(
    *,
    run: ResolvedRunSpec,
    evidence: InspectionEvidenceBundle,
    config: InspectionFormalVerifierConfigV3,
    projection: _FormalProjection,
) -> VerificationReport:
    run_goals = {goal.goal_id: goal for goal in run.task.goals}
    binding_goal_ids = {binding.goal_id for binding in config.goal_bindings}
    if set(run_goals) != binding_goal_ids or len(run_goals) != len(config.goal_bindings):
        raise ValueError("formal goal bindings do not cover the ResolvedRun goals exactly")
    components = {
        component.component_id: component
        for component in projection.evaluation.components
    }
    goals: list[GoalResult] = []
    for binding in config.goal_bindings:
        component = components[binding.component_id]
        goal = run_goals[binding.goal_id]
        value = (
            projection.evaluation.semantic_f1
            if component.component_id == "semantic_f1"
            else float(component.passed)
        )
        passed = component.passed and _compare(value, goal.operator, goal.threshold)
        goals.append(
            GoalResult(
                goal_id=goal.goal_id,
                passed=passed,
                metrics=(
                    MetricResult(
                        metric_id=goal.metric_id,
                        value=float(value),
                        unit=(
                            "f1"
                            if component.component_id == "semantic_f1"
                            else "boolean"
                        ),
                        evidence=_component_references(
                            component=component,
                            evidence=evidence,
                            projection=projection,
                            config=config,
                        ),
                    ),
                ),
                failure_class=(
                    None
                    if passed
                    else f"requirement.{component.component_id}.not_met"
                ),
            )
        )
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status="passed" if all(goal.passed for goal in goals) else "failed",
        goals=tuple(goals),
        coverage_complete=True,
    )
    validate_report_against_run(report, run)
    return report


def _invalid_report(run: ResolvedRunSpec) -> VerificationReport:
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status="invalid",
        goals=tuple(
            GoalResult(
                goal_id=goal.goal_id,
                passed=False,
                metrics=(),
                failure_class="evidence.invalid",
            )
            for goal in run.task.goals
        ),
        coverage_complete=False,
    )
    validate_report_against_run(report, run)
    return report


def verify_formal_v2_sealed(
    *,
    package: InspectionTaskPackage,
    resolved_bounds: InspectionBoundResolution,
    config: InspectionFormalVerifierConfigV3,
    bundle_root: Path,
    run: ResolvedRunSpec,
    seal: SealManifest,
    sealed_root: Path,
) -> VerificationReport:
    """Reconstruct and evaluate Formal Inspection v2 from sealed authority only."""

    validated_run = ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
    validated_config = InspectionFormalVerifierConfigV3.model_validate(
        config.model_dump(mode="json")
    )
    evidence = InspectionVerifier(
        package,
        resolved_bounds=resolved_bounds,
    ).load_validated_sealed_evidence(
        bundle_root=bundle_root,
        run=validated_run,
        seal=seal,
        sealed_root=sealed_root,
    )
    projection = _project_formal_bundle(
        package=package,
        evidence=evidence,
        run=validated_run,
        config=validated_config,
        bundle_root=bundle_root,
    )
    return _verification_report(
        run=validated_run,
        evidence=evidence,
        config=validated_config,
        projection=projection,
    )


__all__ = ["verify_formal_v2_sealed"]
