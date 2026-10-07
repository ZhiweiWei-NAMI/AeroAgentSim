from __future__ import annotations

import math
from dataclasses import dataclass

from aero_bench.tasks.inspection.formal_v2_contracts import (
    FORMAL_V2_COMPONENT_IDS,
    CanonicalInspectionReportV2,
    CapturedFrameEvidenceV2,
    DefectTruthSetV2,
    EvaluationComponentResultV2,
    EvaluationWitnessV2,
    FormalInspectionEvaluationResultV2,
    InspectionFormalEvidenceBundleV2,
    InspectionFormalPolicyV3,
    InspectionMissionContextV2,
    InspectionTargetContextV2,
    PhysicalContactEventV2,
    PhysicalTraceSampleV2,
    PhysicalTraceV2,
    PolygonPrismV2,
)
from aero_bench.world import frame_math


_FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class _FrameQualification:
    frame_id: str
    sample_index: int
    sequence: int
    capture_time_s: float


def _component(
    component_id: str,
    *,
    passed: bool,
    reason: str,
    sample_sequences: tuple[int, ...] = (),
    frame_ids: tuple[str, ...] = (),
) -> EvaluationComponentResultV2:
    return EvaluationComponentResultV2(
        component_id=component_id,
        passed=passed,
        reason=reason,
        witness=EvaluationWitnessV2(
            sample_sequences=sample_sequences,
            frame_ids=frame_ids,
        ),
    )


def _sample_to_position(sample: PhysicalTraceSampleV2) -> frame_math.Vector3:
    return sample.position_enu.as_frame()


def _sample_prism_position(
    sample: PhysicalTraceSampleV2, prism: PolygonPrismV2
) -> frame_math.Vector3:
    if prism.vertical_reference == "enu_up":
        return sample.position_enu.as_frame()
    return frame_math.Vector3(
        sample.position_enu.x_m, sample.position_enu.y_m, sample.altitude_amsl_m
    )


def _sample_linear_speed(sample: PhysicalTraceSampleV2) -> float:
    return sample.linear_velocity_enu_mps.as_frame().norm()


def _sample_angular_speed(sample: PhysicalTraceSampleV2) -> float:
    return sample.angular_velocity_body_rps.as_frame().norm()


def _horizontal_distance(left: frame_math.Vector3, right: frame_math.Vector3) -> float:
    return math.hypot(right.x - left.x, right.y - left.y)


def _point_to_segment_distance_2d(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= _FLOAT_TOLERANCE:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    projection = max(0.0, min(1.0, projection))
    closest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.hypot(point[0] - closest[0], point[1] - closest[1])


def _segment_distance_2d(
    start_a: tuple[float, float],
    end_a: tuple[float, float],
    start_b: tuple[float, float],
    end_b: tuple[float, float],
) -> float:
    if (
        abs(start_a[0] - end_a[0]) <= _FLOAT_TOLERANCE
        and abs(start_a[1] - end_a[1]) <= _FLOAT_TOLERANCE
    ):
        return _point_to_segment_distance_2d(start_a, start_b, end_b)
    if (
        abs(start_b[0] - end_b[0]) <= _FLOAT_TOLERANCE
        and abs(start_b[1] - end_b[1]) <= _FLOAT_TOLERANCE
    ):
        return _point_to_segment_distance_2d(start_b, start_a, end_a)
    if _segments_intersect_2d(start_a, end_a, start_b, end_b):
        return 0.0
    return min(
        _point_to_segment_distance_2d(start_a, start_b, end_b),
        _point_to_segment_distance_2d(end_a, start_b, end_b),
        _point_to_segment_distance_2d(start_b, start_a, end_a),
        _point_to_segment_distance_2d(end_b, start_a, end_a),
    )


def _camera_world_pose(
    sample: PhysicalTraceSampleV2,
    context: InspectionMissionContextV2,
) -> frame_math.RigidTransform:
    vehicle_pose = frame_math.RigidTransform(
        rotation=sample.orientation_world.as_frame().to_rotation_matrix(),
        translation=sample.position_enu.as_frame(),
    )
    return vehicle_pose.compose(
        context.camera.mount_vehicle_to_sensor.as_rigid_transform()
    )


def _target_world_pose(target: InspectionTargetContextV2) -> frame_math.RigidTransform:
    return target.pose_world.as_rigid_transform()


def _point_on_segment_2d(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    cross = (point[0] - start[0]) * (end[1] - start[1]) - (point[1] - start[1]) * (
        end[0] - start[0]
    )
    if abs(cross) > _FLOAT_TOLERANCE:
        return False
    dot = (point[0] - start[0]) * (end[0] - start[0]) + (point[1] - start[1]) * (
        end[1] - start[1]
    )
    if dot < -_FLOAT_TOLERANCE:
        return False
    length2 = (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2
    return dot <= length2 + _FLOAT_TOLERANCE


def _segments_intersect_2d(
    start_a: tuple[float, float],
    end_a: tuple[float, float],
    start_b: tuple[float, float],
    end_b: tuple[float, float],
) -> bool:
    def orient(
        left: tuple[float, float],
        right: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return (right[0] - left[0]) * (point[1] - left[1]) - (right[1] - left[1]) * (
            point[0] - left[0]
        )

    orient1 = orient(start_a, end_a, start_b)
    orient2 = orient(start_a, end_a, end_b)
    orient3 = orient(start_b, end_b, start_a)
    orient4 = orient(start_b, end_b, end_a)
    if abs(orient1) <= _FLOAT_TOLERANCE and _point_on_segment_2d(
        start_b, start_a, end_a
    ):
        return True
    if abs(orient2) <= _FLOAT_TOLERANCE and _point_on_segment_2d(end_b, start_a, end_a):
        return True
    if abs(orient3) <= _FLOAT_TOLERANCE and _point_on_segment_2d(
        start_a, start_b, end_b
    ):
        return True
    if abs(orient4) <= _FLOAT_TOLERANCE and _point_on_segment_2d(end_a, start_b, end_b):
        return True
    return (orient1 > 0.0) != (orient2 > 0.0) and (orient3 > 0.0) != (orient4 > 0.0)


def _point_in_polygon_or_boundary(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _point_on_segment_2d(point, start, end):
            return True
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if (start[1] > point[1]) != (end[1] > point[1]):
            x_cross = (end[0] - start[0]) * (point[1] - start[1]) / (
                end[1] - start[1]
            ) + start[0]
            if abs(x_cross - point[0]) <= _FLOAT_TOLERANCE:
                return True
            if x_cross > point[0]:
                inside = not inside
    return inside


def _segment_inside_polygon(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    if (
        abs(start[0] - end[0]) <= _FLOAT_TOLERANCE
        and abs(start[1] - end[1]) <= _FLOAT_TOLERANCE
    ):
        return _point_in_polygon_or_boundary(start, polygon)
    if not _point_in_polygon_or_boundary(start, polygon):
        return False
    if not _point_in_polygon_or_boundary(end, polygon):
        return False
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        if _segments_intersect_2d(start, end, edge_start, edge_end):
            if _point_on_segment_2d(
                start, edge_start, edge_end
            ) and _point_on_segment_2d(
                end,
                edge_start,
                edge_end,
            ):
                continue
            return False
    return True


def _point_inside_polygon_with_margin(
    point: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    margin_m: float,
) -> bool:
    if not _point_in_polygon_or_boundary(point, polygon):
        return False
    if margin_m <= _FLOAT_TOLERANCE:
        return True
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        if _point_to_segment_distance_2d(point, edge_start, edge_end) <= (
            margin_m + _FLOAT_TOLERANCE
        ):
            return False
    return True


def _segment_inside_polygon_with_margin(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    margin_m: float,
) -> bool:
    if not _segment_inside_polygon(start, end, polygon):
        return False
    if not _point_inside_polygon_with_margin(start, polygon, margin_m):
        return False
    if not _point_inside_polygon_with_margin(end, polygon, margin_m):
        return False
    if margin_m <= _FLOAT_TOLERANCE:
        return True
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        if _segment_distance_2d(start, end, edge_start, edge_end) <= (
            margin_m + _FLOAT_TOLERANCE
        ):
            return False
    return True


def _segment_intersects_polygon_or_boundary(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
) -> bool:
    if (
        abs(start[0] - end[0]) <= _FLOAT_TOLERANCE
        and abs(start[1] - end[1]) <= _FLOAT_TOLERANCE
    ):
        return _point_in_polygon_or_boundary(start, polygon)
    if _point_in_polygon_or_boundary(start, polygon):
        return True
    if _point_in_polygon_or_boundary(end, polygon):
        return True
    return any(
        _segments_intersect_2d(
            start,
            end,
            polygon[index],
            polygon[(index + 1) % len(polygon)],
        )
        for index in range(len(polygon))
    )


def _segment_intersects_polygon_or_margin(
    start: tuple[float, float],
    end: tuple[float, float],
    polygon: tuple[tuple[float, float], ...],
    margin_m: float,
) -> bool:
    if _segment_intersects_polygon_or_boundary(start, end, polygon):
        return True
    if margin_m <= _FLOAT_TOLERANCE:
        return False
    for index, edge_start in enumerate(polygon):
        edge_end = polygon[(index + 1) % len(polygon)]
        if _segment_distance_2d(start, end, edge_start, edge_end) <= (
            margin_m + _FLOAT_TOLERANCE
        ):
            return True
    return False


def _point_inside_prism(
    position: frame_math.Vector3,
    prism: PolygonPrismV2,
    *,
    horizontal_margin_m: float,
    vertical_margin_m: float,
) -> bool:
    polygon = tuple(vertex.as_tuple() for vertex in prism.vertices_enu)
    if not _point_inside_polygon_with_margin(
        (position.x, position.y),
        polygon,
        horizontal_margin_m,
    ):
        return False
    return (
        position.z - vertical_margin_m >= prism.min_altitude_m
        and position.z + vertical_margin_m <= prism.max_altitude_m
    )


def _segment_inside_prism(
    start: frame_math.Vector3,
    end: frame_math.Vector3,
    prism: PolygonPrismV2,
    *,
    horizontal_margin_m: float,
    vertical_margin_m: float,
) -> bool:
    polygon = tuple(vertex.as_tuple() for vertex in prism.vertices_enu)
    if not _segment_inside_polygon_with_margin(
        (start.x, start.y),
        (end.x, end.y),
        polygon,
        horizontal_margin_m,
    ):
        return False
    return (
        start.z - vertical_margin_m >= prism.min_altitude_m
        and start.z + vertical_margin_m <= prism.max_altitude_m
        and end.z - vertical_margin_m >= prism.min_altitude_m
        and end.z + vertical_margin_m <= prism.max_altitude_m
    )


def _segment_intersects_no_fly_prism(
    start: frame_math.Vector3,
    end: frame_math.Vector3,
    prism: PolygonPrismV2,
    *,
    horizontal_margin_m: float,
    vertical_margin_m: float,
) -> bool:
    low = min(start.z, end.z) - vertical_margin_m
    high = max(start.z, end.z) + vertical_margin_m
    if high < prism.min_altitude_m or low > prism.max_altitude_m:
        return False
    polygon = tuple(vertex.as_tuple() for vertex in prism.vertices_enu)
    return _segment_intersects_polygon_or_margin(
        (start.x, start.y),
        (end.x, end.y),
        polygon,
        horizontal_margin_m,
    )


def _lookup_sample_index(trace: PhysicalTraceV2, sequence: int) -> int:
    for index, sample in enumerate(trace.samples):
        if sample.sequence == sequence:
            return index
    raise ValueError(f"trace sample sequence {sequence} is missing")


def _verify_policy_context_alignment(
    policy: InspectionFormalPolicyV3,
    context: InspectionMissionContextV2,
    truth: DefectTruthSetV2,
    trace: PhysicalTraceV2,
) -> None:
    if policy.scenario_digest != context.scenario_digest:
        raise ValueError("policy scenario_digest must match mission context")
    if policy.scenario_digest != truth.scenario_digest:
        raise ValueError("defect truth scenario_digest must match policy")
    if policy.selected_launch_id != context.selected_launch_id:
        raise ValueError("policy selected_launch_id must match mission context")
    if policy.vehicle_id != context.vehicle_id or policy.sensor_id != context.sensor_id:
        raise ValueError("policy vehicle_id and sensor_id must match mission context")
    if trace.vehicle_id != policy.vehicle_id:
        raise ValueError("physical trace vehicle_id must match policy vehicle_id")
    context_target_ids = tuple(target.target_id for target in context.targets)
    if context_target_ids != policy.target_ids:
        raise ValueError("mission context target_ids must match policy target_ids")
    context_geofence_ids = tuple(prism.prism_id for prism in context.geofences)
    if context_geofence_ids != policy.geofence_ids:
        raise ValueError("mission context geofence_ids must match policy geofence_ids")
    context_no_fly_ids = tuple(prism.prism_id for prism in context.no_fly_prisms)
    if context_no_fly_ids != policy.no_fly_ids:
        raise ValueError("mission context no_fly_ids must match policy no_fly_ids")


def _sample_qualifies_target_geometry(
    sample: PhysicalTraceSampleV2,
    target: InspectionTargetContextV2,
    context: InspectionMissionContextV2,
) -> bool:
    camera_world = _camera_world_pose(sample, context)
    target_world = _target_world_pose(target)
    camera_inverse = camera_world.inverse()
    half_extents = target.box_geometry.half_extents()
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                target_corner = frame_math.Vector3(
                    sx * half_extents.x,
                    sy * half_extents.y,
                    sz * half_extents.z,
                )
                world_corner = target_world.apply_position(target_corner)
                camera_corner = camera_inverse.apply_position(world_corner)
                if camera_corner.x <= 0.0:
                    return False
                horizontal_angle = math.degrees(
                    math.atan2(abs(camera_corner.y), camera_corner.x)
                )
                vertical_angle = math.degrees(
                    math.atan2(abs(camera_corner.z), camera_corner.x)
                )
                if (
                    horizontal_angle
                    >= context.camera.horizontal_fov_deg / 2.0 - target.fov_margin_deg
                ):
                    return False
                if (
                    vertical_angle
                    >= context.camera.vertical_fov_deg / 2.0 - target.fov_margin_deg
                ):
                    return False
    center_world = target_world.translation
    camera_to_center_world = center_world - camera_world.translation
    center_distance = camera_to_center_world.norm()
    if center_distance <= _FLOAT_TOLERANCE:
        return False
    if center_distance < target.min_observation_range_m:
        return False
    if center_distance > target.max_observation_range_m:
        return False
    surface_normal_world = target_world.rotation.apply(
        target.surface_normal_target.as_frame()
    )
    viewing_direction = frame_math.Vector3(
        -camera_to_center_world.x / center_distance,
        -camera_to_center_world.y / center_distance,
        -camera_to_center_world.z / center_distance,
    )
    dot = surface_normal_world.dot(viewing_direction)
    dot = max(-1.0, min(1.0, dot))
    view_angle = math.degrees(math.acos(dot))
    if view_angle > target.max_surface_view_angle_deg:
        return False
    return True


def _qualifies_geometry(
    sample: PhysicalTraceSampleV2,
    frame: CapturedFrameEvidenceV2,
    target: InspectionTargetContextV2,
    context: InspectionMissionContextV2,
) -> bool:
    if frame.capture_time_s != sample.sim_time_s:
        return False
    return _qualifies_airborne_observation_sample(
        sample
    ) and _sample_qualifies_target_geometry(sample, target, context)


def _qualifies_airborne_observation_sample(sample: PhysicalTraceSampleV2) -> bool:
    return (
        sample.armed
        and sample.in_air
        and not sample.landed
        and not sample.ground_contact
    )


def _collect_qualifying_frames(
    bundle: InspectionFormalEvidenceBundleV2,
) -> tuple[dict[str, tuple[_FrameQualification, ...]], int, int, tuple[str, ...]]:
    trace = bundle.physical_trace
    policy = bundle.policy
    context = bundle.mission_context
    by_target: dict[str, list[_FrameQualification]] = {
        target_id: [] for target_id in policy.target_ids
    }
    target_lookup = {target.target_id: target for target in context.targets}
    for frame in bundle.captured_frames:
        if frame.sensor_id != policy.sensor_id or frame.vehicle_id != policy.vehicle_id:
            continue
        if frame.target_id not in target_lookup:
            continue
        sample_index = _lookup_sample_index(trace, frame.trace_sample_sequence)
        sample = trace.samples[sample_index]
        if _qualifies_geometry(sample, frame, target_lookup[frame.target_id], context):
            by_target[frame.target_id].append(
                _FrameQualification(
                    frame_id=frame.frame_id,
                    sample_index=sample_index,
                    sequence=sample.sequence,
                    capture_time_s=frame.capture_time_s,
                )
            )
    qualifying: dict[str, tuple[_FrameQualification, ...]] = {}
    earliest_sample_index: int | None = None
    latest_sample_index: int | None = None
    witness_frame_ids: list[str] = []
    for target_id in policy.target_ids:
        candidates = sorted(
            by_target[target_id],
            key=lambda item: (item.capture_time_s, item.sequence, item.frame_id),
        )
        sample_frames: dict[int, list[_FrameQualification]] = {}
        for candidate in candidates:
            sample_frames.setdefault(candidate.sample_index, []).append(candidate)
        geometry_ok = [
            _qualifies_airborne_observation_sample(sample)
            and _sample_qualifies_target_geometry(
                sample, target_lookup[target_id], context
            )
            for sample in trace.samples
        ]
        runs: list[tuple[int, int]] = []
        run_start: int | None = None
        for index, sample_ok in enumerate(geometry_ok):
            gap_broken = (
                index > 0
                and trace.samples[index].sim_time_s
                - trace.samples[index - 1].sim_time_s
                > policy.max_trace_gap_s + _FLOAT_TOLERANCE
            )
            if sample_ok and not gap_broken:
                if run_start is None:
                    run_start = index
                continue
            if run_start is not None:
                runs.append((run_start, index - 1))
                run_start = None
            if sample_ok:
                run_start = index
        if run_start is not None:
            runs.append((run_start, len(trace.samples) - 1))
        window_found: tuple[_FrameQualification, ...] | None = None
        selected_window_bounds: tuple[int, int] | None = None
        for run_start, run_end in runs:
            for left in range(run_start, run_end + 1):
                for right in range(left, run_end + 1):
                    if (
                        trace.samples[right].sim_time_s - trace.samples[left].sim_time_s
                        < policy.dwell_s - _FLOAT_TOLERANCE
                    ):
                        continue
                    window = tuple(
                        frame
                        for sample_index in range(left, right + 1)
                        for frame in sample_frames.get(sample_index, ())
                    )
                    if (
                        len({item.frame_id for item in window})
                        < policy.minimum_distinct_frames_per_target
                    ):
                        continue
                    window_found = window
                    selected_window_bounds = (left, right)
                    break
                if window_found is not None:
                    break
            if window_found is not None:
                break
        if window_found is None:
            return {}, -1, -1, ()
        assert selected_window_bounds is not None
        qualifying[target_id] = window_found
        witness_frame_ids.extend(item.frame_id for item in window_found)
        window_start_index, window_end_index = selected_window_bounds
        if earliest_sample_index is None or window_start_index < earliest_sample_index:
            earliest_sample_index = window_start_index
        if latest_sample_index is None or window_end_index > latest_sample_index:
            latest_sample_index = window_end_index
    assert earliest_sample_index is not None
    assert latest_sample_index is not None
    return (
        {target_id: tuple(value) for target_id, value in qualifying.items()},
        earliest_sample_index,
        latest_sample_index,
        tuple(witness_frame_ids),
    )


def _f1_score(
    report: CanonicalInspectionReportV2,
    truth: DefectTruthSetV2,
    qualifying: dict[str, tuple[_FrameQualification, ...]],
) -> float:
    truth_by_defect = {item.defect_id: item.target_id for item in truth.truths}
    qualified_frame_to_target = {
        frame.frame_id: target_id
        for target_id, frames in qualifying.items()
        for frame in frames
    }
    true_positive = 0
    false_positive = 0
    matched_truth_ids: set[str] = set()
    for detection in report.detections:
        truth_target_id = truth_by_defect.get(detection.defect_id)
        qualified_target_id = qualified_frame_to_target.get(detection.frame_id)
        if (
            truth_target_id is not None
            and detection.target_id == truth_target_id
            and qualified_target_id == detection.target_id
        ):
            true_positive += 1
            matched_truth_ids.add(detection.defect_id)
        else:
            false_positive += 1
    false_negative = len(truth_by_defect) - len(matched_truth_ids)
    denominator = 2 * true_positive + false_positive + false_negative
    if denominator == 0:
        return 0.0
    return (2.0 * true_positive) / denominator


def verify_formal_v2(
    bundle: InspectionFormalEvidenceBundleV2,
) -> FormalInspectionEvaluationResultV2:
    """Evaluate an independently reconstructed formal Inspection v2 bundle."""

    bundle = InspectionFormalEvidenceBundleV2.model_validate(
        bundle.model_dump(mode="python")
    )
    _verify_policy_context_alignment(
        bundle.policy,
        bundle.mission_context,
        bundle.defect_truth,
        bundle.physical_trace,
    )
    samples = bundle.physical_trace.samples
    policy = bundle.policy
    context = bundle.mission_context
    uncertainty = context.uncertainty
    components: list[EvaluationComponentResultV2] = []
    horizontal_uncertainty_m = uncertainty.horizontal_position_uncertainty_m
    vertical_trace_margin_m = (
        uncertainty.vertical_position_uncertainty_m + uncertainty.terrain_uncertainty_m
    )

    start_sample = samples[0]
    start_position = _sample_to_position(start_sample)
    violating_targets = tuple(
        target.target_id
        for target in context.targets
        if _horizontal_distance(
            target.pose_world.position_enu.as_frame(), start_position
        )
        - horizontal_uncertainty_m
        <= target.arrival_radius_m + policy.startup_clearance_margin_m
    )
    if violating_targets:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[0],
                passed=False,
                reason="initial sample is already inside a target arrival radius plus startup margin",
                sample_sequences=(start_sample.sequence,),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[0],
                passed=True,
                reason="initial sample is outside every target arrival radius plus startup margin",
                sample_sequences=(start_sample.sequence,),
            )
        )

    if (
        start_sample.armed
        or start_sample.in_air
        or not start_sample.landed
        or not start_sample.ground_contact
    ):
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[1],
                passed=False,
                reason="initial sample must remain disarmed, landed, and ground-contacting before the later armed transition",
                sample_sequences=(start_sample.sequence,),
            )
        )
        return _finalize_result(bundle, components)
    armed_index = next(
        (
            index
            for index in range(1, len(samples))
            if not samples[index - 1].armed and samples[index].armed
        ),
        None,
    )
    if armed_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[1],
                passed=False,
                reason="trace never reaches a later disarmed-to-armed transition",
                sample_sequences=(start_sample.sequence,),
            )
        )
        return _finalize_result(bundle, components)
    takeoff_index: int | None = None
    selected_armed_index = armed_index
    arm_transition_indices = [
        index
        for index in range(1, len(samples))
        if not samples[index - 1].armed and samples[index].armed
    ]
    for transition_index in arm_transition_indices:
        for index in range(transition_index + 1, len(samples)):
            sample = samples[index]
            if not sample.armed:
                break
            if _qualifies_takeoff_sample(
                sample,
                policy,
                conservative_vertical_margin_m=vertical_trace_margin_m,
            ):
                selected_armed_index = transition_index
                takeoff_index = index
                break
        if takeoff_index is not None:
            break
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[1],
            passed=True,
            reason="trace contains a later disarmed-to-armed transition",
            sample_sequences=(samples[selected_armed_index].sequence,),
        )
    )
    if takeoff_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[2],
                passed=False,
                reason="trace never reaches a later armed in-air sample that conservatively exceeds the takeoff AGL threshold without ground contact",
                sample_sequences=(samples[selected_armed_index].sequence,),
            )
        )
        return _finalize_result(bundle, components)
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[2],
            passed=True,
            reason="trace reaches a later armed in-air sample that conservatively exceeds the takeoff AGL threshold without ground contact",
            sample_sequences=(samples[takeoff_index].sequence,),
        )
    )
    if bundle.physical_trace.maximum_gap_s > policy.max_trace_gap_s + _FLOAT_TOLERANCE:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[3],
                passed=False,
                reason="physical_trace.maximum_gap_s exceeds policy.max_trace_gap_s, so complete authoritative trace coverage is missing",
                sample_sequences=(samples[0].sequence, samples[-1].sequence),
            )
        )
        return _finalize_result(bundle, components)
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[3],
            passed=True,
            reason="physical_trace.maximum_gap_s stays within policy.max_trace_gap_s, so authoritative trace coverage is complete",
            sample_sequences=(samples[0].sequence, samples[-1].sequence),
        )
    )

    (
        qualifying,
        first_observation_index,
        last_observation_index,
        qualifying_frame_ids,
    ) = _collect_qualifying_frames(bundle)
    if not qualifying:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[4],
                passed=False,
                reason="no qualifying observation interval exists before evaluating pre-observation ground track",
            )
        )
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[5],
                passed=False,
                reason="required targets do not have a contiguous qualifying dwell interval",
            )
        )
        return _finalize_result(bundle, components)
    ground_track = 0.0
    track_sequences = [
        sample.sequence
        for sample in samples[takeoff_index : first_observation_index + 1]
    ]
    continuous_airborne_chain = all(
        _qualifies_airborne_observation_sample(samples[index])
        for index in range(takeoff_index, first_observation_index + 1)
    )
    if continuous_airborne_chain:
        for index in range(takeoff_index + 1, first_observation_index + 1):
            previous = _sample_to_position(samples[index - 1])
            current = _sample_to_position(samples[index])
            conservative_segment = max(
                0.0,
                _horizontal_distance(previous, current)
                - 2.0 * uncertainty.horizontal_position_uncertainty_m,
            )
            ground_track += conservative_segment
    if not continuous_airborne_chain:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[4],
                passed=False,
                reason="conservative pre-observation horizontal ground track requires one continuous armed in-air non-contacting chain from verified takeoff through the first observation boundary",
                sample_sequences=tuple(track_sequences),
            )
        )
    elif ground_track <= policy.minimum_horizontal_ground_track_m:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[4],
                passed=False,
                reason="conservative pre-observation horizontal ground track does not strictly exceed the policy threshold",
                sample_sequences=tuple(track_sequences),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[4],
                passed=True,
                reason="conservative pre-observation horizontal ground track strictly exceeds the policy threshold",
                sample_sequences=tuple(track_sequences),
            )
        )

    if first_observation_index <= takeoff_index:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[5],
                passed=False,
                reason="qualifying observations begin before the verified takeoff phase is complete",
                sample_sequences=(samples[first_observation_index].sequence,),
                frame_ids=qualifying_frame_ids,
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[5],
                passed=True,
                reason="each required target has enough distinct decoded frames in one qualifying dwell interval",
                sample_sequences=tuple(
                    sequence
                    for target_frames in qualifying.values()
                    for sequence in (item.sequence for item in target_frames)
                ),
                frame_ids=qualifying_frame_ids,
            )
        )

    geofence_failure_index: int | None = None
    for index, sample in enumerate(samples):
        if not all(
            _point_inside_prism(
                _sample_prism_position(sample, prism),
                prism,
                horizontal_margin_m=horizontal_uncertainty_m,
                vertical_margin_m=vertical_trace_margin_m,
            )
            for prism in context.geofences
        ):
            geofence_failure_index = index
            break
        if index > 0:
            previous = samples[index - 1]
            if any(
                not _segment_inside_prism(
                    _sample_prism_position(previous, prism),
                    _sample_prism_position(sample, prism),
                    prism,
                    horizontal_margin_m=horizontal_uncertainty_m,
                    vertical_margin_m=vertical_trace_margin_m,
                )
                for prism in context.geofences
            ):
                geofence_failure_index = index
                break
    if geofence_failure_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[6],
                passed=True,
                reason="every sample and swept segment remains inside the selected geofence prisms",
                sample_sequences=(samples[0].sequence, samples[-1].sequence),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[6],
                passed=False,
                reason="a sample or swept segment exits the selected geofence prisms",
                sample_sequences=(
                    samples[geofence_failure_index - 1].sequence,
                    samples[geofence_failure_index].sequence,
                )
                if geofence_failure_index > 0
                else (samples[0].sequence,),
            )
        )

    no_fly_failure_index: int | None = None
    for index in range(1, len(samples)):
        previous = samples[index - 1]
        current = samples[index]
        if any(
            _segment_intersects_no_fly_prism(
                _sample_prism_position(previous, prism),
                _sample_prism_position(current, prism),
                prism,
                horizontal_margin_m=horizontal_uncertainty_m,
                vertical_margin_m=vertical_trace_margin_m,
            )
            for prism in context.no_fly_prisms
        ):
            no_fly_failure_index = index
            break
    if no_fly_failure_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[7],
                passed=True,
                reason="the swept path stays outside every selected no-fly prism",
                sample_sequences=(samples[0].sequence, samples[-1].sequence),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[7],
                passed=False,
                reason="a swept segment intersects a selected no-fly prism",
                sample_sequences=(
                    samples[no_fly_failure_index - 1].sequence,
                    samples[no_fly_failure_index].sequence,
                ),
            )
        )

    forbidden_event = next(
        (
            event
            for event in bundle.physical_trace.contact_events
            if event.counterparty_kind not in {"ground", "launch_pad"}
        ),
        None,
    )
    if forbidden_event is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[8],
                passed=True,
                reason="the provider-owned contact stream contains no forbidden contact",
                sample_sequences=tuple(
                    event.sequence for event in bundle.physical_trace.contact_events
                ),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[8],
                passed=False,
                reason="the provider-owned contact stream contains forbidden contact",
                sample_sequences=(forbidden_event.sequence,),
            )
        )

    observation_end_time = samples[last_observation_index].sim_time_s
    outcome = bundle.network_outcome
    deadline = samples[0].sim_time_s + policy.network_deadline_s
    report_outcome_passed = True
    report_outcome_reason = "report outcome is exact, provider-owned, and timely"
    outcome_witness_sequences: tuple[int, ...] = ()
    if outcome.outcome_kind not in policy.accepted_outcome_kinds:
        report_outcome_passed = False
        report_outcome_reason = "network outcome kind is not accepted by policy"
    elif outcome.report_digest != bundle.report.transport_artifact_digest:
        report_outcome_passed = False
        report_outcome_reason = (
            "network outcome digest does not match the exact sealed report artifact"
        )
    elif outcome.report_size_bytes != bundle.report.transport_artifact_size_bytes:
        report_outcome_passed = False
        report_outcome_reason = (
            "network outcome size does not match the exact sealed report artifact"
        )
    elif bundle.report.reported_at_s <= observation_end_time:
        report_outcome_passed = False
        report_outcome_reason = (
            "canonical report timestamp must follow the observation interval"
        )
    elif bundle.report.reported_at_s > deadline:
        report_outcome_passed = False
        report_outcome_reason = "canonical report misses the network deadline"
    elif outcome.outcome_kind == "delivered":
        if outcome.delivered_at_s <= observation_end_time:
            report_outcome_passed = False
            report_outcome_reason = (
                "report delivery must occur after the observation interval"
            )
        # Same-tick delivery is allowed, but the provider-owned outcome cannot
        # precede the canonical report materialization.
        elif outcome.delivered_at_s < bundle.report.reported_at_s:
            report_outcome_passed = False
            report_outcome_reason = (
                "report delivery cannot precede the canonical report timestamp"
            )
        elif outcome.delivered_at_s > deadline:
            report_outcome_passed = False
            report_outcome_reason = "report delivery misses the network deadline"
        else:
            outcome_witness_sequences = (samples[last_observation_index].sequence,)
    else:
        if outcome.buffered_at_s <= observation_end_time:
            report_outcome_passed = False
            report_outcome_reason = (
                "durable buffering must occur after the observation interval"
            )
        # Same-tick buffering is allowed, but the provider-owned outcome cannot
        # precede the canonical report materialization.
        elif outcome.buffered_at_s < bundle.report.reported_at_s:
            report_outcome_passed = False
            report_outcome_reason = (
                "durable buffering cannot precede the canonical report timestamp"
            )
        elif outcome.buffered_at_s > deadline:
            report_outcome_passed = False
            report_outcome_reason = "durable buffering misses the network deadline"
        elif outcome.retention_guarantee_s < policy.minimum_buffer_retention_s:
            report_outcome_passed = False
            report_outcome_reason = (
                "buffer retention is shorter than the policy minimum"
            )
        elif (
            outcome.expires_at_s
            < outcome.buffered_at_s + policy.minimum_buffer_retention_s
        ):
            report_outcome_passed = False
            report_outcome_reason = (
                "buffer expiry does not cover the policy retention interval"
            )
        else:
            outcome_witness_sequences = (samples[last_observation_index].sequence,)
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[9],
            passed=report_outcome_passed,
            reason=report_outcome_reason,
            sample_sequences=outcome_witness_sequences,
            frame_ids=bundle.report.qualifying_frame_ids,
        )
    )

    return_index = next(
        (
            index
            for index in range(last_observation_index + 1, len(samples))
            if _horizontal_distance(
                samples[index].position_enu.as_frame(),
                context.launch_pad.pose_world.position_enu.as_frame(),
            )
            + horizontal_uncertainty_m
            <= policy.return_radius_m
        ),
        None,
    )
    if return_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[10],
                passed=False,
                reason="trace never returns within the selected launch radius after observations",
                sample_sequences=(samples[last_observation_index].sequence,),
            )
        )
        return _finalize_result(bundle, components)
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[10],
            passed=True,
            reason="trace returns within the selected launch radius after observations",
            sample_sequences=(samples[return_index].sequence,),
        )
    )

    landing_witness = next(
        (
            witness
            for index in range(return_index + 1, len(samples))
            if (witness := _landing_transition_witness(
                samples,
                index,
                return_index,
                bundle.physical_trace.contact_events,
                context,
                policy,
                horizontal_uncertainty_m,
                uncertainty.vertical_position_uncertainty_m,
                vertical_trace_margin_m,
            )) is not None
        ),
        None,
    )
    if landing_witness is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[11],
                passed=False,
                reason="trace never lands on the selected launch pad with descent and permitted contact",
                sample_sequences=(samples[return_index].sequence,),
            )
        )
        landing_index = return_index
    else:
        landing_index = landing_witness[-1]
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[11],
                passed=True,
                reason="trace descends into permitted pad contact and confirms grounded landing within the policy limits",
                sample_sequences=tuple(
                    samples[index].sequence for index in sorted(set(landing_witness))
                ),
            )
        )

    stop_start_index: int | None = None
    stopped_end_index: int | None = None
    current_stop_start: int | None = None
    for index in range(landing_index, len(samples)):
        qualifies = _qualifies_stopped_sample(
            samples[index],
            context,
            policy,
            horizontal_position_margin_m=horizontal_uncertainty_m,
            vertical_position_margin_m=uncertainty.vertical_position_uncertainty_m,
        )
        if index > landing_index and (
            samples[index].sim_time_s - samples[index - 1].sim_time_s
            > policy.max_trace_gap_s + _FLOAT_TOLERANCE
        ):
            current_stop_start = None
        if not qualifies:
            current_stop_start = None
            continue
        if current_stop_start is None:
            current_stop_start = index
        if (
            samples[index].sim_time_s - samples[current_stop_start].sim_time_s
            >= policy.stopped_dwell_s - _FLOAT_TOLERANCE
        ):
            stop_start_index = current_stop_start
            stopped_end_index = index
            break
    if stop_start_index is None or stopped_end_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[12],
                passed=False,
                reason="trace does not maintain a continuous stopped dwell after landing",
                sample_sequences=(samples[landing_index].sequence,),
            )
        )
        stopped_end_index = landing_index
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[12],
                passed=True,
                reason="trace maintains a continuous stopped dwell after landing",
                sample_sequences=(
                    samples[stop_start_index].sequence,
                    samples[stopped_end_index].sequence,
                ),
            )
        )

    disarm_index: int | None = None
    for index in range(stopped_end_index, len(samples)):
        if samples[index].armed:
            continue
        if all(
            _qualifies_terminal_rest_sample(
                sample,
                context,
                policy,
                horizontal_position_margin_m=horizontal_uncertainty_m,
                vertical_position_margin_m=uncertainty.vertical_position_uncertainty_m,
            )
            for sample in samples[index:]
        ):
            disarm_index = index
            break
    if disarm_index is None:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[13],
                passed=False,
                reason="trace never reaches a terminal disarmed state that remains physically at rest on the selected launch pad",
                sample_sequences=(samples[stopped_end_index].sequence,),
            )
        )
    else:
        components.append(
            _component(
                FORMAL_V2_COMPONENT_IDS[13],
                passed=True,
                reason="trace reaches a terminal disarmed state and remains physically at rest on the selected launch pad",
                sample_sequences=tuple(
                    sample.sequence for sample in samples[disarm_index:]
                ),
            )
        )

    report_frame_ids = tuple(bundle.report.qualifying_frame_ids)
    semantic_f1 = _f1_score(bundle.report, bundle.defect_truth, qualifying)
    semantic_passed = semantic_f1 >= policy.minimum_detection_f1
    semantic_reason = (
        "semantic F1 meets the mandatory policy threshold"
        if semantic_passed
        else "semantic F1 is below the mandatory policy threshold"
    )
    components.append(
        _component(
            FORMAL_V2_COMPONENT_IDS[14],
            passed=semantic_passed,
            reason=semantic_reason,
            sample_sequences=tuple(
                sequence
                for target_frames in qualifying.values()
                for sequence in (item.sequence for item in target_frames)
            ),
            frame_ids=report_frame_ids,
        )
    )

    return _finalize_result(bundle, components, semantic_f1=semantic_f1)


def _landing_transition_witness(
    samples: tuple[PhysicalTraceSampleV2, ...],
    confirmation_index: int,
    return_index: int,
    contacts: tuple[PhysicalContactEventV2, ...],
    context: InspectionMissionContextV2,
    policy: InspectionFormalPolicyV3,
    horizontal_position_margin_m: float,
    vertical_position_margin_m: float,
    conservative_agl_margin_m: float,
) -> tuple[int, int, int] | None:
    """Bind descent, native contact and delayed MAVSDK confirmation separately.

    The velocity-magnitude limit remains on grounded landing confirmation,
    as in the former rule. It is not an unmeasured impact-speed bound.
    """

    sample = samples[confirmation_index]
    previous = samples[confirmation_index - 1]
    if not sample.landed or sample.in_air or not sample.ground_contact:
        return None
    if previous.landed:
        return None
    contact_index = confirmation_index
    while contact_index > return_index and samples[contact_index - 1].ground_contact:
        contact_index -= 1
    if contact_index <= return_index:
        return None
    descent_index = contact_index - 1
    descent = samples[descent_index]
    contact = samples[contact_index]
    if (
        descent.landed
        or not descent.in_air
        or descent.ground_contact
        or descent.linear_velocity_enu_mps.z_m >= 0.0
        or descent.altitude_amsl_m <= contact.altitude_amsl_m
    ):
        return None
    for grounded in samples[contact_index:confirmation_index + 1]:
        if (
            not grounded.ground_contact
            or abs(grounded.linear_velocity_enu_mps.z_m)
            > policy.landing_max_vertical_speed_mps
            or grounded.altitude_agl_m + conservative_agl_margin_m
            > policy.landing_max_agl_m
            or not _is_on_launch_pad(
                grounded,
                context,
                horizontal_position_margin_m=horizontal_position_margin_m,
                vertical_position_margin_m=vertical_position_margin_m,
            )
            or not _has_active_landing_contact(
                contacts, grounded, context.selected_launch_id
            )
        ):
            return None
    return descent_index, contact_index, confirmation_index


def _qualifies_takeoff_sample(
    sample: PhysicalTraceSampleV2,
    policy: InspectionFormalPolicyV3,
    *,
    conservative_vertical_margin_m: float,
) -> bool:
    if not sample.armed or not sample.in_air or sample.landed or sample.ground_contact:
        return False
    return (
        sample.altitude_agl_m - conservative_vertical_margin_m
        > policy.minimum_takeoff_agl_m
    )


def _is_on_launch_pad(
    sample: PhysicalTraceSampleV2,
    context: InspectionMissionContextV2,
    *,
    horizontal_position_margin_m: float,
    vertical_position_margin_m: float,
) -> bool:
    pad_inverse = context.launch_pad.pose_world.as_rigid_transform().inverse()
    local_position = pad_inverse.apply_position(sample.position_enu.as_frame())
    horizontal_distance = math.hypot(local_position.x, local_position.y)
    if (
        horizontal_distance + horizontal_position_margin_m
        >= context.launch_pad.geometry.radius_m - _FLOAT_TOLERANCE
    ):
        return False
    half_height = context.launch_pad.geometry.height_m / 2.0
    return (
        local_position.z - vertical_position_margin_m > -half_height + _FLOAT_TOLERANCE
        and local_position.z + vertical_position_margin_m
        < half_height - _FLOAT_TOLERANCE
    )


def _qualifies_stopped_sample(
    sample: PhysicalTraceSampleV2,
    context: InspectionMissionContextV2,
    policy: InspectionFormalPolicyV3,
    *,
    horizontal_position_margin_m: float,
    vertical_position_margin_m: float,
) -> bool:
    return (
        sample.landed
        and not sample.in_air
        and sample.ground_contact
        and _is_on_launch_pad(
            sample,
            context,
            horizontal_position_margin_m=horizontal_position_margin_m,
            vertical_position_margin_m=vertical_position_margin_m,
        )
        and _sample_linear_speed(sample) <= policy.stopped_linear_speed_mps
        and _sample_angular_speed(sample) <= policy.stopped_angular_speed_rps
    )


def _qualifies_terminal_rest_sample(
    sample: PhysicalTraceSampleV2,
    context: InspectionMissionContextV2,
    policy: InspectionFormalPolicyV3,
    *,
    horizontal_position_margin_m: float,
    vertical_position_margin_m: float,
) -> bool:
    return not sample.armed and _qualifies_stopped_sample(
        sample,
        context,
        policy,
        horizontal_position_margin_m=horizontal_position_margin_m,
        vertical_position_margin_m=vertical_position_margin_m,
    )


def _has_active_landing_contact(
    events: tuple[PhysicalContactEventV2, ...],
    landing_sample: PhysicalTraceSampleV2,
    selected_launch_id: str,
) -> bool:
    active_contacts: set[tuple[str, str]] = set()
    seen_contacts: set[tuple[str, str]] = set()
    landing_key = (landing_sample.sequence, landing_sample.sim_time_s)
    for event in events:
        if (event.sequence, event.sim_time_s) > landing_key:
            break
        contact_key = (event.counterparty_kind, event.counterparty_id)
        if event.phase == "began":
            active_contacts.add(contact_key)
            seen_contacts.add(contact_key)
            continue
        if contact_key not in seen_contacts:
            seen_contacts.add(contact_key)
        active_contacts.discard(contact_key)
    if ("launch_pad", selected_launch_id) in active_contacts:
        return True
    return any(kind == "ground" for kind, _ in active_contacts)


def _finalize_result(
    bundle: InspectionFormalEvidenceBundleV2,
    components: list[EvaluationComponentResultV2],
    *,
    semantic_f1: float = 0.0,
) -> FormalInspectionEvaluationResultV2:
    by_id = {component.component_id: component for component in components}
    ordered_components: list[EvaluationComponentResultV2] = []
    for component_id in FORMAL_V2_COMPONENT_IDS:
        component = by_id.get(component_id)
        if component is None:
            component = _component(
                component_id,
                passed=False,
                reason="component was not reached because an earlier ordered gate failed",
            )
        ordered_components.append(component)
    semantic_component = ordered_components[-1]
    if semantic_component.passed and semantic_f1 <= 0.0:
        semantic_f1 = 0.0
    return FormalInspectionEvaluationResultV2(
        schema_version="aero-bench.inspection-formal-evaluation/v2",
        passed=all(component.passed for component in ordered_components),
        semantic_f1=semantic_f1,
        components=tuple(ordered_components),
    )


__all__ = ["verify_formal_v2"]
