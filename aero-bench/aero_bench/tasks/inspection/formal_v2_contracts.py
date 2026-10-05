from __future__ import annotations

import base64
import binascii
import hashlib
import math
from typing import Annotated, Final, Literal, TypeAlias

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.image_evidence import decode_strict_rgb_png
from aero_bench.world import frame_math


_FLOAT_TOLERANCE: Final = 1e-9
_GROUND_CONTACT_COUNTERPARTY_KINDS: Final[frozenset[str]] = frozenset(
    {"ground", "launch_pad"}
)
FORMAL_V2_COMPONENT_IDS: Final[tuple[str, ...]] = (
    "initial_outside_arrival",
    "armed_after_start",
    "takeoff_agl",
    "trace_coverage",
    "pre_observation_ground_track",
    "target_observations",
    "geofence_containment",
    "no_fly_avoidance",
    "contact_safety",
    "report_outcome",
    "return_to_launch",
    "landing",
    "stopped_dwell",
    "terminal_disarm",
    "semantic_f1",
)

FormalV2ComponentId: TypeAlias = Literal[
    "initial_outside_arrival",
    "armed_after_start",
    "takeoff_agl",
    "trace_coverage",
    "pre_observation_ground_track",
    "target_observations",
    "geofence_containment",
    "no_fly_avoidance",
    "contact_safety",
    "report_outcome",
    "return_to_launch",
    "landing",
    "stopped_dwell",
    "terminal_disarm",
    "semantic_f1",
]
OutcomeKind: TypeAlias = Literal["delivered", "durably_buffered"]


def _ensure_finite_tree(value: object, path: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{path} must be finite")
    if isinstance(value, BaseModel):
        _ensure_finite_tree(value.model_dump(mode="python"), path)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _ensure_finite_tree(child, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _ensure_finite_tree(child, f"{path}[{index}]")


def _require_positive(name: str, value: float) -> float:
    if value <= 0.0:
        raise ValueError(f"{name} must be strictly positive")
    return value


def _require_nonnegative(name: str, value: float) -> float:
    if value < 0.0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _require_strictly_sorted_unique(
    values: tuple[str, ...], name: str
) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")
    if tuple(sorted(values)) != values:
        raise ValueError(f"{name} must be sorted")
    return values


def _strict_float(value: object, name: str) -> float:
    if type(value) is not float:
        raise ValueError(f"{name} must be a JSON float")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


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
        a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
    ) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

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


def _validate_simple_polygon(
    vertices: tuple[tuple[float, float], ...], name: str
) -> None:
    if len(vertices) < 3:
        raise ValueError(f"{name} must contain at least 3 vertices")
    if len(vertices) != len(set(vertices)):
        raise ValueError(f"{name} must not repeat vertices")
    area2 = 0.0
    for index, vertex in enumerate(vertices):
        next_vertex = vertices[(index + 1) % len(vertices)]
        area2 += vertex[0] * next_vertex[1] - next_vertex[0] * vertex[1]
    if abs(area2) <= _FLOAT_TOLERANCE:
        raise ValueError(f"{name} must enclose positive area")
    edge_count = len(vertices)
    for left in range(edge_count):
        left_start = vertices[left]
        left_end = vertices[(left + 1) % edge_count]
        for right in range(left + 1, edge_count):
            if right in {left, (left + 1) % edge_count}:
                continue
            if left == 0 and right == edge_count - 1:
                continue
            right_start = vertices[right]
            right_end = vertices[(right + 1) % edge_count]
            if _segments_intersect_2d(left_start, left_end, right_start, right_end):
                raise ValueError(f"{name} must be simple and non-self-intersecting")


class FormalV2Model(StrictModel):

    @model_validator(mode="after")
    def reject_nonfinite_numbers(self) -> "FormalV2Model":
        _ensure_finite_tree(self.model_dump(mode="python"), type(self).__name__)
        return self


class Vector3V2(FormalV2Model):
    x_m: StrictFloat
    y_m: StrictFloat
    z_m: StrictFloat

    def as_frame(self) -> frame_math.Vector3:
        return frame_math.Vector3(self.x_m, self.y_m, self.z_m)


class UnitVector3V2(FormalV2Model):
    x: StrictFloat
    y: StrictFloat
    z: StrictFloat

    @model_validator(mode="after")
    def unit_length(self) -> "UnitVector3V2":
        norm = math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)
        if abs(norm - 1.0) > 1e-9:
            raise ValueError("unit vector must have norm 1")
        return self

    def as_frame(self) -> frame_math.Vector3:
        return frame_math.Vector3(self.x, self.y, self.z)


class QuaternionV2(FormalV2Model):
    w: StrictFloat
    x: StrictFloat
    y: StrictFloat
    z: StrictFloat

    @model_validator(mode="after")
    def unit_quaternion(self) -> "QuaternionV2":
        frame_math.UnitQuaternion(w=self.w, x=self.x, y=self.y, z=self.z)
        canonical = frame_math._canonical_quaternion_sign(
            self.w,
            self.x,
            self.y,
            self.z,
        )
        if (self.w, self.x, self.y, self.z) != canonical:
            raise ValueError("quaternion must use the canonical sign representation")
        return self

    def as_frame(self) -> frame_math.UnitQuaternion:
        return frame_math.UnitQuaternion(w=self.w, x=self.x, y=self.y, z=self.z)


class Pose3DV2(FormalV2Model):
    position_enu: Vector3V2
    orientation_world: QuaternionV2

    def as_rigid_transform(self) -> frame_math.RigidTransform:
        return frame_math.RigidTransform(
            rotation=self.orientation_world.as_frame().to_rotation_matrix(),
            translation=self.position_enu.as_frame(),
        )


class PolygonVertexV2(FormalV2Model):
    east_m: StrictFloat
    north_m: StrictFloat

    def as_tuple(self) -> tuple[float, float]:
        return (self.east_m, self.north_m)


class PolygonPrismV2(FormalV2Model):
    prism_id: Identifier
    vertices_enu: tuple[PolygonVertexV2, ...] = Field(min_length=3)
    vertical_reference: Literal["enu_up", "amsl"]
    min_altitude_m: StrictFloat
    max_altitude_m: StrictFloat

    @model_validator(mode="after")
    def valid_prism(self) -> "PolygonPrismV2":
        if self.max_altitude_m <= self.min_altitude_m:
            raise ValueError("polygon prism altitude range must be strictly increasing")
        _validate_simple_polygon(
            tuple(vertex.as_tuple() for vertex in self.vertices_enu),
            "vertices_enu",
        )
        return self


class LaunchPadGeometryV2(FormalV2Model):
    radius_m: StrictFloat
    height_m: StrictFloat

    @model_validator(mode="after")
    def positive_geometry(self) -> "LaunchPadGeometryV2":
        _require_positive("radius_m", self.radius_m)
        _require_positive("height_m", self.height_m)
        return self


class LaunchPadContextV2(FormalV2Model):
    launch_id: Identifier
    pose_world: Pose3DV2
    geometry: LaunchPadGeometryV2


class CameraContextV2(FormalV2Model):
    sensor_id: Identifier
    vehicle_id: Identifier
    mount_vehicle_to_sensor: Pose3DV2
    horizontal_fov_deg: StrictFloat
    vertical_fov_deg: StrictFloat
    resolution_width_px: StrictInt = Field(gt=0)
    resolution_height_px: StrictInt = Field(gt=0)
    optical_convention: Literal[
        "camera_forward_plus_x__image_right_plus_y__image_up_plus_z"
    ]

    @model_validator(mode="after")
    def valid_camera(self) -> "CameraContextV2":
        if not 0.0 < self.horizontal_fov_deg < 180.0:
            raise ValueError("horizontal_fov_deg must be in (0, 180)")
        if not 0.0 < self.vertical_fov_deg < 180.0:
            raise ValueError("vertical_fov_deg must be in (0, 180)")
        return self


class TargetBoxGeometryV2(FormalV2Model):
    size_x_m: StrictFloat
    size_y_m: StrictFloat
    size_z_m: StrictFloat

    @model_validator(mode="after")
    def positive_sizes(self) -> "TargetBoxGeometryV2":
        _require_positive("size_x_m", self.size_x_m)
        _require_positive("size_y_m", self.size_y_m)
        _require_positive("size_z_m", self.size_z_m)
        return self

    def half_extents(self) -> frame_math.Vector3:
        return frame_math.Vector3(
            self.size_x_m / 2.0,
            self.size_y_m / 2.0,
            self.size_z_m / 2.0,
        )


class InspectionTargetContextV2(FormalV2Model):
    target_id: Identifier
    pose_world: Pose3DV2
    box_geometry: TargetBoxGeometryV2
    surface_normal_target: UnitVector3V2
    arrival_radius_m: StrictFloat
    min_observation_range_m: StrictFloat
    max_observation_range_m: StrictFloat
    max_surface_view_angle_deg: StrictFloat
    fov_margin_deg: StrictFloat

    @model_validator(mode="after")
    def valid_target_rules(self) -> "InspectionTargetContextV2":
        _require_positive("arrival_radius_m", self.arrival_radius_m)
        _require_nonnegative(
            "min_observation_range_m", self.min_observation_range_m
        )
        _require_positive("max_observation_range_m", self.max_observation_range_m)
        if self.min_observation_range_m > self.max_observation_range_m:
            raise ValueError(
                "min_observation_range_m must not exceed max_observation_range_m"
            )
        if not 0.0 < self.max_surface_view_angle_deg < 90.0:
            raise ValueError("max_surface_view_angle_deg must be in (0, 90)")
        if not 0.0 <= self.fov_margin_deg < 45.0:
            raise ValueError("fov_margin_deg must be in [0, 45)")
        normal = self.surface_normal_target
        components = (abs(normal.x), abs(normal.y), abs(normal.z))
        if sum(abs(component - 1.0) <= 1e-9 for component in components) != 1:
            raise ValueError("surface_normal_target must align with one box axis")
        if sum(component <= 1e-9 for component in components) != 2:
            raise ValueError("surface_normal_target must align with one box axis")
        return self


class PositionUncertaintyV2(FormalV2Model):
    horizontal_position_uncertainty_m: StrictFloat
    vertical_position_uncertainty_m: StrictFloat
    terrain_uncertainty_m: StrictFloat

    @model_validator(mode="after")
    def nonnegative_uncertainty(self) -> "PositionUncertaintyV2":
        _require_nonnegative(
            "horizontal_position_uncertainty_m",
            self.horizontal_position_uncertainty_m,
        )
        _require_nonnegative(
            "vertical_position_uncertainty_m",
            self.vertical_position_uncertainty_m,
        )
        _require_nonnegative("terrain_uncertainty_m", self.terrain_uncertainty_m)
        return self


def canonical_mission_context_digest(document: dict[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _json_tree(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: _json_tree(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_tree(child) for child in value]
    return value


class InspectionMissionContextV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-mission-context/v2"]
    world_frame: Literal["enu"]
    scenario_digest: Sha256
    selected_launch_id: Identifier
    vehicle_id: Identifier
    sensor_id: Identifier
    launch_pad: LaunchPadContextV2
    camera: CameraContextV2
    targets: tuple[InspectionTargetContextV2, ...] = Field(min_length=2)
    geofences: tuple[PolygonPrismV2, ...] = Field(min_length=1)
    no_fly_prisms: tuple[PolygonPrismV2, ...] = Field(min_length=1)
    uncertainty: PositionUncertaintyV2
    context_digest: Sha256

    @classmethod
    def issue(cls, **data: object) -> "InspectionMissionContextV2":
        payload = dict(data)
        payload["schema_version"] = "aero-bench.inspection-mission-context/v2"
        digest_payload = _json_tree(payload)
        if not isinstance(digest_payload, dict):
            raise TypeError("mission context payload must be a JSON object")
        payload["context_digest"] = canonical_mission_context_digest(digest_payload)
        return cls.model_validate(payload)

    @model_validator(mode="after")
    def validate_projection(self) -> "InspectionMissionContextV2":
        if self.launch_pad.launch_id != self.selected_launch_id:
            raise ValueError("launch_pad.launch_id must match selected_launch_id")
        if self.camera.sensor_id != self.sensor_id:
            raise ValueError("camera.sensor_id must match sensor_id")
        if self.camera.vehicle_id != self.vehicle_id:
            raise ValueError("camera.vehicle_id must match vehicle_id")
        target_ids = tuple(target.target_id for target in self.targets)
        geofence_ids = tuple(prism.prism_id for prism in self.geofences)
        no_fly_ids = tuple(prism.prism_id for prism in self.no_fly_prisms)
        _require_strictly_sorted_unique(target_ids, "targets.target_id")
        _require_strictly_sorted_unique(geofence_ids, "geofences.prism_id")
        _require_strictly_sorted_unique(no_fly_ids, "no_fly_prisms.prism_id")
        body = self.model_dump(mode="json")
        actual_digest = body.pop("context_digest")
        expected = canonical_mission_context_digest(body)
        if actual_digest != expected:
            raise ValueError("context_digest does not match canonical mission context")
        return self


class FormalGoalBindingV2(FormalV2Model):
    component_id: FormalV2ComponentId
    goal_id: Identifier


class InspectionFormalVerifierConfigV3(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-verifier/v3"]
    package_id: Identifier
    vehicle_id: Identifier
    sensor_id: Identifier
    target_ids: tuple[Identifier, ...] = Field(min_length=2)
    geofence_ids: tuple[Identifier, ...] = Field(min_length=1)
    no_fly_ids: tuple[Identifier, ...] = Field(min_length=1)
    required_tool_ids: tuple[Identifier, ...] = Field(min_length=1)
    goal_bindings: tuple[FormalGoalBindingV2, ...] = Field(
        min_length=len(FORMAL_V2_COMPONENT_IDS),
        max_length=len(FORMAL_V2_COMPONENT_IDS),
    )
    minimum_takeoff_agl_m: StrictFloat
    minimum_horizontal_ground_track_m: StrictFloat
    startup_clearance_margin_m: StrictFloat
    minimum_distinct_frames_per_target: StrictInt = Field(ge=2)
    dwell_s: StrictFloat
    max_trace_gap_s: StrictFloat
    return_radius_m: StrictFloat
    launch_pad_height_m: StrictFloat
    landing_max_agl_m: StrictFloat
    landing_max_vertical_speed_mps: StrictFloat
    stopped_linear_speed_mps: StrictFloat
    stopped_angular_speed_rps: StrictFloat
    stopped_dwell_s: StrictFloat
    terminal_disarm_required: Literal[True]
    network_delivery_required: Literal[True]
    network_deadline_s: StrictFloat
    accepted_outcome_kinds: tuple[OutcomeKind, ...] = Field(min_length=1)
    minimum_buffer_retention_s: StrictFloat
    minimum_detection_f1: StrictFloat
    uncertainty: PositionUncertaintyV2

    @model_validator(mode="after")
    def valid_formal_config(self) -> "InspectionFormalVerifierConfigV3":
        for name, values in (
            ("target_ids", self.target_ids),
            ("geofence_ids", self.geofence_ids),
            ("no_fly_ids", self.no_fly_ids),
            ("required_tool_ids", self.required_tool_ids),
            ("accepted_outcome_kinds", self.accepted_outcome_kinds),
        ):
            _require_strictly_sorted_unique(values, name)
        component_ids = tuple(binding.component_id for binding in self.goal_bindings)
        if component_ids != FORMAL_V2_COMPONENT_IDS:
            raise ValueError(
                "goal_bindings must cover the fixed v2 component order exactly"
            )
        goal_ids = tuple(binding.goal_id for binding in self.goal_bindings)
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("goal_bindings goal_id values must be unique")
        if self.minimum_takeoff_agl_m <= 10.0:
            raise ValueError("minimum_takeoff_agl_m must be strictly greater than 10")
        if self.minimum_horizontal_ground_track_m <= 50.0:
            raise ValueError(
                "minimum_horizontal_ground_track_m must be strictly greater than 50"
            )
        for name, value in (
            ("startup_clearance_margin_m", self.startup_clearance_margin_m),
            ("dwell_s", self.dwell_s),
            ("max_trace_gap_s", self.max_trace_gap_s),
            ("return_radius_m", self.return_radius_m),
            ("launch_pad_height_m", self.launch_pad_height_m),
            ("landing_max_agl_m", self.landing_max_agl_m),
            ("landing_max_vertical_speed_mps", self.landing_max_vertical_speed_mps),
            ("stopped_linear_speed_mps", self.stopped_linear_speed_mps),
            ("stopped_angular_speed_rps", self.stopped_angular_speed_rps),
            ("stopped_dwell_s", self.stopped_dwell_s),
            ("network_deadline_s", self.network_deadline_s),
            ("minimum_buffer_retention_s", self.minimum_buffer_retention_s),
        ):
            _require_positive(name, value)
        if not 0.0 < self.minimum_detection_f1 <= 1.0:
            raise ValueError("minimum_detection_f1 must be in (0, 1]")
        return self


class InspectionFormalPolicyV3(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-formal-policy/v3"]
    scenario_digest: Sha256
    selected_launch_id: Identifier
    vehicle_id: Identifier
    sensor_id: Identifier
    target_ids: tuple[Identifier, ...] = Field(min_length=2)
    geofence_ids: tuple[Identifier, ...] = Field(min_length=1)
    no_fly_ids: tuple[Identifier, ...] = Field(min_length=1)
    minimum_takeoff_agl_m: StrictFloat
    minimum_horizontal_ground_track_m: StrictFloat
    startup_clearance_margin_m: StrictFloat
    minimum_distinct_frames_per_target: StrictInt = Field(ge=2)
    dwell_s: StrictFloat
    max_trace_gap_s: StrictFloat
    return_radius_m: StrictFloat
    landing_max_agl_m: StrictFloat
    landing_max_vertical_speed_mps: StrictFloat
    stopped_linear_speed_mps: StrictFloat
    stopped_angular_speed_rps: StrictFloat
    stopped_dwell_s: StrictFloat
    terminal_disarm_required: Literal[True]
    network_deadline_s: StrictFloat
    accepted_outcome_kinds: tuple[OutcomeKind, ...] = Field(min_length=1)
    minimum_buffer_retention_s: StrictFloat
    minimum_detection_f1: StrictFloat

    @model_validator(mode="after")
    def valid_policy(self) -> "InspectionFormalPolicyV3":
        _require_strictly_sorted_unique(self.target_ids, "target_ids")
        _require_strictly_sorted_unique(self.geofence_ids, "geofence_ids")
        _require_strictly_sorted_unique(self.no_fly_ids, "no_fly_ids")
        _require_strictly_sorted_unique(
            self.accepted_outcome_kinds,
            "accepted_outcome_kinds",
        )
        if self.minimum_takeoff_agl_m <= 10.0:
            raise ValueError("minimum_takeoff_agl_m must be strictly greater than 10")
        if self.minimum_horizontal_ground_track_m <= 50.0:
            raise ValueError(
                "minimum_horizontal_ground_track_m must be strictly greater than 50"
            )
        _require_positive("startup_clearance_margin_m", self.startup_clearance_margin_m)
        _require_positive("dwell_s", self.dwell_s)
        _require_positive("max_trace_gap_s", self.max_trace_gap_s)
        _require_positive("return_radius_m", self.return_radius_m)
        _require_positive("landing_max_agl_m", self.landing_max_agl_m)
        _require_positive(
            "landing_max_vertical_speed_mps",
            self.landing_max_vertical_speed_mps,
        )
        _require_positive("stopped_linear_speed_mps", self.stopped_linear_speed_mps)
        _require_positive("stopped_angular_speed_rps", self.stopped_angular_speed_rps)
        _require_positive("stopped_dwell_s", self.stopped_dwell_s)
        _require_positive("network_deadline_s", self.network_deadline_s)
        _require_positive(
            "minimum_buffer_retention_s",
            self.minimum_buffer_retention_s,
        )
        if not 0.0 < self.minimum_detection_f1 <= 1.0:
            raise ValueError("minimum_detection_f1 must be in (0, 1]")
        return self


class PhysicalTraceSampleV2(FormalV2Model):
    sequence: StrictInt = Field(ge=0)
    sim_time_s: StrictFloat
    position_enu: Vector3V2
    altitude_amsl_m: StrictFloat
    terrain_altitude_amsl_m: StrictFloat
    altitude_agl_m: StrictFloat
    orientation_world: QuaternionV2
    linear_velocity_enu_mps: Vector3V2
    angular_velocity_body_rps: Vector3V2
    armed: StrictBool
    in_air: StrictBool
    landed: StrictBool
    ground_contact: StrictBool

    @model_validator(mode="after")
    def consistent_sample(self) -> "PhysicalTraceSampleV2":
        expected_agl = self.altitude_amsl_m - self.terrain_altitude_amsl_m
        if abs(expected_agl - self.altitude_agl_m) > _FLOAT_TOLERANCE:
            raise ValueError(
                "altitude_agl_m must equal altitude_amsl_m - terrain_altitude_amsl_m"
            )
        if self.landed and self.in_air:
            raise ValueError("landed and in_air cannot both be true")
        if self.landed and not self.ground_contact:
            raise ValueError("landed samples must assert ground_contact")
        return self


class PhysicalContactEventV2(FormalV2Model):
    sequence: StrictInt = Field(ge=0)
    sim_time_s: StrictFloat
    counterparty_kind: Literal[
        "launch_pad",
        "ground",
        "obstacle",
        "target",
        "vehicle",
        "other",
    ]
    counterparty_id: Identifier
    phase: Literal["began", "ended"]


class PhysicalTraceV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-physical-trace/v2"]
    vehicle_id: Identifier
    trace_start_sequence: StrictInt = Field(ge=0)
    trace_end_sequence: StrictInt = Field(ge=0)
    trace_start_time_s: StrictFloat
    trace_end_time_s: StrictFloat
    maximum_gap_s: StrictFloat
    contacts_complete: Literal[True]
    samples: tuple[PhysicalTraceSampleV2, ...] = Field(min_length=2)
    contact_events: tuple[PhysicalContactEventV2, ...]

    @model_validator(mode="after")
    def valid_trace(self) -> "PhysicalTraceV2":
        _require_positive("maximum_gap_s", self.maximum_gap_s)
        sequences = [sample.sequence for sample in self.samples]
        times = [sample.sim_time_s for sample in self.samples]
        sample_time_by_sequence = {
            sample.sequence: sample.sim_time_s for sample in self.samples
        }
        if sequences[0] != self.trace_start_sequence:
            raise ValueError(
                "trace_start_sequence must equal the first sample sequence"
            )
        if sequences[-1] != self.trace_end_sequence:
            raise ValueError("trace_end_sequence must equal the last sample sequence")
        if abs(times[0] - self.trace_start_time_s) > _FLOAT_TOLERANCE:
            raise ValueError("trace_start_time_s must equal the first sample time")
        if abs(times[-1] - self.trace_end_time_s) > _FLOAT_TOLERANCE:
            raise ValueError("trace_end_time_s must equal the last sample time")
        for index in range(1, len(self.samples)):
            if self.samples[index].sequence <= self.samples[index - 1].sequence:
                raise ValueError("trace sample sequences must be strictly increasing")
            if self.samples[index].sim_time_s <= self.samples[index - 1].sim_time_s:
                raise ValueError("trace sample times must be strictly increasing")
        actual_max_gap = max(
            self.samples[index].sim_time_s - self.samples[index - 1].sim_time_s
            for index in range(1, len(self.samples))
        )
        if abs(actual_max_gap - self.maximum_gap_s) > _FLOAT_TOLERANCE:
            raise ValueError("maximum_gap_s must equal the observed maximum trace gap")
        valid_sequences = {sample.sequence for sample in self.samples}
        previous_contact_key: tuple[int, float] | None = None
        contact_state: dict[tuple[str, str], bool] = {}
        contact_sample_transitions: set[tuple[tuple[str, str], int]] = set()
        contact_events_by_sequence: dict[int, list[PhysicalContactEventV2]] = {
            sample.sequence: [] for sample in self.samples
        }
        for event in self.contact_events:
            if event.sequence not in valid_sequences:
                raise ValueError("contact event sequence must bind to a trace sample")
            sample_time_s = sample_time_by_sequence[event.sequence]
            if abs(event.sim_time_s - sample_time_s) > _FLOAT_TOLERANCE:
                raise ValueError(
                    "contact event sim_time_s must exactly match its trace sample time"
                )
            if (
                previous_contact_key is not None
                and (event.sequence, event.sim_time_s) < previous_contact_key
            ):
                raise ValueError("contact events must be monotonically ordered")
            previous_contact_key = (event.sequence, event.sim_time_s)
            contact_key = (event.counterparty_kind, event.counterparty_id)
            contact_transition_key = (contact_key, event.sequence)
            if contact_transition_key in contact_sample_transitions:
                raise ValueError(
                    "a contact counterparty can change phase at most once per trace sample"
                )
            contact_sample_transitions.add(contact_transition_key)
            contact_events_by_sequence[event.sequence].append(event)
            if event.phase == "began":
                if contact_state.get(contact_key, False):
                    raise ValueError(
                        "contact began events require the counterparty to be inactive"
                    )
                contact_state[contact_key] = True
                continue
            if contact_key not in contact_state:
                contact_state[contact_key] = False
                continue
            if not contact_state[contact_key]:
                raise ValueError(
                    "contact ended events require an active or trace-start contact"
                )
            contact_state[contact_key] = False
        first_sample_sequence = self.samples[0].sequence
        if not any(
            event.counterparty_kind in _GROUND_CONTACT_COUNTERPARTY_KINDS
            for event in contact_events_by_sequence[first_sample_sequence]
        ):
            raise ValueError(
                "contacts_complete traces must declare explicit initial ground or launch-pad contact state at the first sample"
            )
        active_ground_contacts: set[tuple[str, str]] = set()
        for sample in self.samples:
            for event in contact_events_by_sequence[sample.sequence]:
                contact_key = (event.counterparty_kind, event.counterparty_id)
                if event.phase == "began":
                    active_ground_contacts.add(contact_key)
                    continue
                active_ground_contacts.discard(contact_key)
            sample_has_ground_contact = any(
                kind in _GROUND_CONTACT_COUNTERPARTY_KINDS
                for kind, _ in active_ground_contacts
            )
            if sample.ground_contact != sample_has_ground_contact:
                raise ValueError(
                    "sample ground_contact must exactly match active ground or launch-pad contacts when contacts_complete is true"
                )
        return self


class CapturedFrameEvidenceV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-captured-rgb-frame/v1"]
    frame_id: Identifier
    frame_sequence: StrictInt = Field(ge=0)
    capture_time_s: StrictFloat
    observation_id: Identifier
    target_id: Identifier
    sensor_id: Identifier
    vehicle_id: Identifier
    trace_sample_sequence: StrictInt = Field(ge=0)
    distance_m: StrictFloat
    view_angle_deg: StrictFloat
    selector: str = Field(min_length=1, max_length=512)
    engine_sim_time_ns: StrictInt = Field(ge=0)
    logical_origin_engine_ns: StrictInt = Field(ge=0)
    payload_digest: Sha256
    image_sha256: Sha256
    size_bytes: StrictInt = Field(gt=0, le=2 * 1024 * 1024)
    width: StrictInt = Field(gt=0)
    height: StrictInt = Field(gt=0)
    media_type: Literal["image/png"]
    source: Literal["gazebo.camera"]
    capture_pose_source: Literal["gazebo.pose.private_digest"]
    camera_pose_sha256: Sha256
    target_pose_sha256: Sha256
    image_base64: str = Field(min_length=1, max_length=2_796_204)

    @classmethod
    def issue(cls, **data: object) -> "CapturedFrameEvidenceV2":
        payload = dict(data)
        payload["schema_version"] = "aero-bench.inspection-captured-rgb-frame/v1"
        return cls.model_validate(payload)

    @model_validator(mode="after")
    def valid_geometry(self) -> "CapturedFrameEvidenceV2":
        if not math.isfinite(self.distance_m) or self.distance_m <= 0.0:
            raise ValueError("distance_m must be finite and positive")
        if not math.isfinite(self.view_angle_deg) or self.view_angle_deg < 0.0:
            raise ValueError("view_angle_deg must be finite and nonnegative")
        if self.selector != f"frames/{self.frame_id}":
            raise ValueError("captured frame selector must identify its frame")
        if self.engine_sim_time_ns < self.logical_origin_engine_ns or (
            self.engine_sim_time_ns - self.logical_origin_engine_ns
        ) / 1_000_000_000 != self.capture_time_s:
            raise ValueError(
                "captured frame engine time differs from logical capture time"
            )
        try:
            encoded = self.image_base64.encode("ascii")
            image = base64.b64decode(encoded, validate=True)
        except (UnicodeEncodeError, binascii.Error) as error:
            raise ValueError("captured frame image_base64 is not strict base64") from error
        if base64.b64encode(image).decode("ascii") != self.image_base64:
            raise ValueError("captured frame image_base64 is not canonical")
        if len(image) != self.size_bytes:
            raise ValueError("captured frame byte size differs from image_base64")
        if hashlib.sha256(image).hexdigest() != self.image_sha256:
            raise ValueError("captured frame digest differs from image_base64")
        decoded_width, decoded_height = decode_strict_rgb_png(image)
        if (decoded_width, decoded_height) != (self.width, self.height):
            raise ValueError("captured frame dimensions differ from decoded PNG")
        return self


class DefectDetectionV2(FormalV2Model):
    detection_id: Identifier
    defect_id: Identifier
    target_id: Identifier
    frame_id: Identifier


def canonical_report_payload(document: dict[str, object]) -> bytes:
    return canonical_json_bytes(document)


class CanonicalInspectionReportV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-report-evidence/v2"]
    report_id: Identifier
    reported_at_s: StrictFloat
    qualifying_frame_ids: tuple[Identifier, ...] = Field(min_length=1)
    detections: tuple[DefectDetectionV2, ...]
    report_digest: Sha256
    report_size_bytes: StrictInt = Field(gt=0)
    transport_artifact_digest: Sha256
    transport_artifact_size_bytes: StrictInt = Field(gt=0)

    @classmethod
    def issue(cls, **data: object) -> "CanonicalInspectionReportV2":
        payload = dict(data)
        payload["schema_version"] = "aero-bench.inspection-report-evidence/v2"
        canonical_body = {
            key: value
            for key, value in payload.items()
            if key not in {"report_digest", "report_size_bytes"}
        }
        encoded = canonical_report_payload(canonical_body)
        payload["report_digest"] = hashlib.sha256(encoded).hexdigest()
        payload["report_size_bytes"] = len(encoded)
        return cls.model_validate(payload)

    @model_validator(mode="after")
    def valid_report(self) -> "CanonicalInspectionReportV2":
        _require_strictly_sorted_unique(
            self.qualifying_frame_ids,
            "qualifying_frame_ids",
        )
        detection_ids = tuple(detection.detection_id for detection in self.detections)
        if len(detection_ids) != len(set(detection_ids)):
            raise ValueError("detection_id values must be unique")
        for detection in self.detections:
            if detection.frame_id not in self.qualifying_frame_ids:
                raise ValueError("every detection must bind to a qualifying frame")
        body = self.model_dump(mode="json")
        actual_digest = body.pop("report_digest")
        actual_size = body.pop("report_size_bytes")
        encoded = canonical_report_payload(body)
        if hashlib.sha256(encoded).hexdigest() != actual_digest:
            raise ValueError("report_digest does not match canonical report payload")
        if len(encoded) != actual_size:
            raise ValueError(
                "report_size_bytes does not match canonical report payload"
            )
        return self


class DefectTruthV2(FormalV2Model):
    defect_id: Identifier
    target_id: Identifier


class DefectTruthSetV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-defect-truth/v2"]
    scenario_digest: Sha256
    truths: tuple[DefectTruthV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_truths(self) -> "DefectTruthSetV2":
        defect_ids = tuple(item.defect_id for item in self.truths)
        if len(defect_ids) != len(set(defect_ids)):
            raise ValueError("defect truth ids must be unique")
        return self


class DeliveredReportOutcomeV2(FormalV2Model):
    outcome_kind: Literal["delivered"]
    ownership_kind: Literal["provider"]
    report_digest: Sha256
    report_size_bytes: StrictInt = Field(gt=0)
    delivered_at_s: StrictFloat


class DurablyBufferedReportOutcomeV2(FormalV2Model):
    outcome_kind: Literal["durably_buffered"]
    ownership_kind: Literal["provider"]
    provider_buffer_id: Identifier
    report_digest: Sha256
    report_size_bytes: StrictInt = Field(gt=0)
    buffered_at_s: StrictFloat
    storage_uri: str = Field(min_length=1)
    terminal_presence: Literal[True]
    expires_at_s: StrictFloat
    retention_guarantee_s: StrictFloat

    @model_validator(mode="after")
    def valid_retention(self) -> "DurablyBufferedReportOutcomeV2":
        _require_positive("retention_guarantee_s", self.retention_guarantee_s)
        if self.expires_at_s <= self.buffered_at_s:
            raise ValueError("expires_at_s must be later than buffered_at_s")
        if self.expires_at_s + _FLOAT_TOLERANCE < (
            self.buffered_at_s + self.retention_guarantee_s
        ):
            raise ValueError(
                "expires_at_s must cover the guaranteed retention interval"
            )
        return self


NetworkOutcomeV2: TypeAlias = Annotated[
    DeliveredReportOutcomeV2 | DurablyBufferedReportOutcomeV2,
    Field(discriminator="outcome_kind"),
]


class InspectionFormalEvidenceBundleV2(FormalV2Model):
    policy: InspectionFormalPolicyV3
    mission_context: InspectionMissionContextV2
    physical_trace: PhysicalTraceV2
    captured_frames: tuple[CapturedFrameEvidenceV2, ...] = Field(min_length=1)
    report: CanonicalInspectionReportV2
    defect_truth: DefectTruthSetV2
    network_outcome: NetworkOutcomeV2

    @model_validator(mode="after")
    def globally_unique_frames(self) -> "InspectionFormalEvidenceBundleV2":
        frame_ids = tuple(frame.frame_id for frame in self.captured_frames)
        frame_sequences = tuple(frame.frame_sequence for frame in self.captured_frames)
        frame_times = tuple(frame.capture_time_s for frame in self.captured_frames)
        if len(frame_ids) != len(set(frame_ids)):
            raise ValueError("captured frame ids must be globally unique")
        if len(frame_sequences) != len(set(frame_sequences)):
            raise ValueError("captured frame sequences must be globally unique")
        if len(frame_times) != len(set(frame_times)):
            raise ValueError("captured frame capture times must be globally unique")
        policy_target_ids = set(self.policy.target_ids)
        context_target_ids = {
            target.target_id for target in self.mission_context.targets
        }
        allowed_target_ids = policy_target_ids & context_target_ids
        if (
            allowed_target_ids != policy_target_ids
            or allowed_target_ids != context_target_ids
        ):
            raise ValueError("policy and mission context target ids must match exactly")
        for truth in self.defect_truth.truths:
            if truth.target_id not in allowed_target_ids:
                raise ValueError(
                    "defect truth target_id must belong to the policy and mission context"
                )
        frame_lookup = {frame.frame_id: frame for frame in self.captured_frames}
        for frame in self.captured_frames:
            if (
                frame.sensor_id != self.policy.sensor_id
                or frame.sensor_id != self.mission_context.sensor_id
                or frame.vehicle_id != self.policy.vehicle_id
                or frame.vehicle_id != self.mission_context.vehicle_id
                or frame.target_id not in allowed_target_ids
                or frame.width
                != self.mission_context.camera.resolution_width_px
                or frame.height
                != self.mission_context.camera.resolution_height_px
            ):
                raise ValueError(
                    "captured RGB frame differs from the formal camera mission identity"
                )
        for qualifying_frame_id in self.report.qualifying_frame_ids:
            if qualifying_frame_id not in frame_lookup:
                raise ValueError(
                    "report qualifying_frame_ids must reference captured frames"
                )
        reported_defect_ids = tuple(
            detection.defect_id for detection in self.report.detections
        )
        if len(reported_defect_ids) != len(set(reported_defect_ids)):
            raise ValueError("report defect_ids must be unique")
        for detection in self.report.detections:
            if detection.target_id not in allowed_target_ids:
                raise ValueError(
                    "report detection target_id must belong to the policy and mission context"
                )
            frame = frame_lookup.get(detection.frame_id)
            if frame is None:
                raise ValueError("report detections must reference captured frames")
            if frame.target_id != detection.target_id:
                raise ValueError(
                    "report detections must use qualifying frames captured for the same target"
                )
        return self


class EvaluationWitnessV2(FormalV2Model):
    sample_sequences: tuple[StrictInt, ...]
    frame_ids: tuple[Identifier, ...]


class EvaluationComponentResultV2(FormalV2Model):
    component_id: FormalV2ComponentId
    passed: StrictBool
    reason: str = Field(min_length=1)
    witness: EvaluationWitnessV2


class FormalInspectionEvaluationResultV2(FormalV2Model):
    schema_version: Literal["aero-bench.inspection-formal-evaluation/v2"]
    passed: StrictBool
    semantic_f1: StrictFloat
    components: tuple[EvaluationComponentResultV2, ...] = Field(
        min_length=len(FORMAL_V2_COMPONENT_IDS),
        max_length=len(FORMAL_V2_COMPONENT_IDS),
    )

    @model_validator(mode="after")
    def fixed_component_order(self) -> "FormalInspectionEvaluationResultV2":
        component_ids = tuple(component.component_id for component in self.components)
        if component_ids != FORMAL_V2_COMPONENT_IDS:
            raise ValueError("components must use the fixed v2 component id order")
        computed_passed = all(component.passed for component in self.components)
        if self.passed != computed_passed:
            raise ValueError("passed must equal the conjunction of every component")
        if not 0.0 <= self.semantic_f1 <= 1.0:
            raise ValueError("semantic_f1 must be in [0, 1]")
        semantic_component = self.components[-1]
        if semantic_component.component_id != "semantic_f1":
            raise ValueError("the final component must be semantic_f1")
        if self.semantic_f1 == 0.0 and semantic_component.passed:
            raise ValueError("semantic_f1 component cannot pass with zero F1")
        return self


__all__ = [
    "CanonicalInspectionReportV2",
    "CapturedFrameEvidenceV2",
    "DefectDetectionV2",
    "DefectTruthSetV2",
    "DefectTruthV2",
    "DeliveredReportOutcomeV2",
    "DurablyBufferedReportOutcomeV2",
    "EvaluationComponentResultV2",
    "EvaluationWitnessV2",
    "FORMAL_V2_COMPONENT_IDS",
    "FormalGoalBindingV2",
    "FormalInspectionEvaluationResultV2",
    "FormalV2ComponentId",
    "InspectionFormalEvidenceBundleV2",
    "InspectionFormalPolicyV3",
    "InspectionFormalVerifierConfigV3",
    "InspectionMissionContextV2",
    "InspectionTargetContextV2",
    "LaunchPadContextV2",
    "LaunchPadGeometryV2",
    "NetworkOutcomeV2",
    "PhysicalContactEventV2",
    "PhysicalTraceSampleV2",
    "PhysicalTraceV2",
    "PolygonPrismV2",
    "PolygonVertexV2",
    "PositionUncertaintyV2",
    "TargetBoxGeometryV2",
    "Vector3V2",
    "canonical_mission_context_digest",
    "canonical_report_payload",
]
