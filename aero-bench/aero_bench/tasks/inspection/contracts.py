from __future__ import annotations

import hashlib
import math
from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from aero_bench.agent.session_contracts import AgentSessionManifest, InteractionRecord
from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    Identifier,
    NamedValue,
    Sha256,
    StrictModel,
)
from aero_bench.runtime.contracts import SceneState, SimulationTime
from aero_bench.runtime.ledger import LedgerRecord
GEOMETRY_OBSERVATION_SCHEMA = "aero-bench.observation.geometry.v1"
RGB_OBSERVATION_SCHEMA = "aero-bench.observation.rgb/v1"
EXTERNAL_VISUAL_KIND = "external_renderer"
EXTERNAL_VISUAL_STATUS = "reserved"
TASK_LOCAL_BUSINESS_PRINCIPAL = "inspection.internal-business"


def geometry_observation_public_payload(
    *,
    target_id: str,
    camera_id: str,
    distance_m: float,
    view_angle_deg: float,
) -> dict[str, object]:
    """Agent-visible observation payload. Geometry only; no image bytes."""

    return {
        "schema_version": GEOMETRY_OBSERVATION_SCHEMA,
        "target_id": target_id,
        "camera_id": camera_id,
        "distance_m": distance_m,
        "view_angle_deg": view_angle_deg,
        "visual_kind": EXTERNAL_VISUAL_KIND,
        "visual_status": EXTERNAL_VISUAL_STATUS,
    }


def rgb_observation_public_payload(
    *,
    target_id: str,
    camera_id: str,
    distance_m: float,
    view_angle_deg: float,
    frame_id: str,
    vehicle_id: str,
    engine_sim_time_ns: int,
    logical_origin_engine_ns: int,
    image_sha256: str,
    size_bytes: int,
    selector: str,
    width: int,
    height: int,
    capture_pose_source: str,
    camera_pose_sha256: str,
    target_pose_sha256: str,
    image_base64: str,
) -> dict[str, object]:
    """Canonical Agent-visible Gazebo RGB observation payload."""

    return {
        "schema_version": RGB_OBSERVATION_SCHEMA,
        "target_id": target_id,
        "camera_id": camera_id,
        "distance_m": distance_m,
        "view_angle_deg": view_angle_deg,
        "visual_kind": "gazebo_rgb",
        "visual_status": "captured",
        "frame_id": frame_id,
        "vehicle_id": vehicle_id,
        "engine_sim_time_ns": engine_sim_time_ns,
        "logical_origin_engine_ns": logical_origin_engine_ns,
        "image_sha256": image_sha256,
        "size_bytes": size_bytes,
        "selector": selector,
        "width": width,
        "height": height,
        "media_type": "image/png",
        "source": "gazebo.camera",
        "capture_pose_source": capture_pose_source,
        "camera_pose_sha256": camera_pose_sha256,
        "target_pose_sha256": target_pose_sha256,
        "image_base64": image_base64,
    }


def _finite(value: float, name: str, *, positive: bool = False) -> float:
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        qualifier = "finite and positive" if positive else "finite and nonnegative"
        raise ValueError(f"{name} must be {qualifier}")
    return value


class MissionBoundSpec(StrictModel):
    """Configuration inputs for the mission-time necessary condition."""

    shortest_path_m: float
    max_speed_mps: float
    fixed_time_s: float
    inspection_dwell_s: float
    deadline_s: float

    @field_validator("shortest_path_m", "fixed_time_s", "inspection_dwell_s")
    @classmethod
    def nonnegative_finite(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name)

    @field_validator("max_speed_mps", "deadline_s")
    @classmethod
    def positive_finite(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name, positive=True)

    @property
    def minimum_time_s(self) -> float:
        return (
            self.shortest_path_m / self.max_speed_mps
            + self.fixed_time_s
            + self.inspection_dwell_s
        )


class LinkBoundSpec(StrictModel):
    """Configuration inputs for the idealized upload-time necessary condition."""

    minimum_payload_bytes: Annotated[int, Field(gt=0)]
    max_bandwidth_bps: float
    minimum_latency_s: float
    upload_deadline_s: float

    @field_validator("max_bandwidth_bps", "upload_deadline_s")
    @classmethod
    def positive_finite(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name, positive=True)

    @field_validator("minimum_latency_s")
    @classmethod
    def nonnegative_finite(cls, value: float) -> float:
        return _finite(value, "minimum_latency_s")

    @property
    def minimum_transfer_time_s(self) -> float:
        return (
            self.minimum_latency_s
            + self.minimum_payload_bytes * 8 / self.max_bandwidth_bps
        )

    @property
    def maximum_payload_bytes_before_deadline(self) -> int:
        available_s = max(0.0, self.upload_deadline_s - self.minimum_latency_s)
        return math.floor(available_s * self.max_bandwidth_bps / 8)


class ImagingBoundSpec(StrictModel):
    """Configuration inputs for the pinhole-camera resolvability condition."""

    defect_size_m: float
    standoff_distance_m: float
    focal_length_m: float
    pixel_pitch_m: float
    minimum_resolvable_pixels: float

    @field_validator(
        "defect_size_m",
        "standoff_distance_m",
        "focal_length_m",
        "pixel_pitch_m",
        "minimum_resolvable_pixels",
    )
    @classmethod
    def positive_finite(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name, positive=True)

    @property
    def projected_defect_pixels(self) -> float:
        return (
            self.defect_size_m
            * self.focal_length_m
            / (self.standoff_distance_m * self.pixel_pitch_m)
        )


class InspectionBoundSpec(StrictModel):
    """All inspection-specific feasibility parameters, with no hidden defaults."""

    mission: MissionBoundSpec
    link: LinkBoundSpec
    imaging: ImagingBoundSpec
    total_defects: Annotated[int, Field(gt=0)]
    geometrically_visible_defects: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def visible_count_is_bounded(self) -> "InspectionBoundSpec":
        if self.geometrically_visible_defects > self.total_defects:
            raise ValueError(
                "geometrically_visible_defects cannot exceed total_defects"
            )
        return self


InspectionBoundField: TypeAlias = Literal[
    "mission.shortest_path_m",
    "mission.max_speed_mps",
    "mission.fixed_time_s",
    "mission.inspection_dwell_s",
    "mission.deadline_s",
    "link.minimum_payload_bytes",
    "link.max_bandwidth_bps",
    "link.minimum_latency_s",
    "link.upload_deadline_s",
    "imaging.defect_size_m",
    "imaging.standoff_distance_m",
    "imaging.focal_length_m",
    "imaging.pixel_pitch_m",
    "imaging.minimum_resolvable_pixels",
    "total_defects",
    "geometrically_visible_defects",
]

INSPECTION_BOUND_FIELDS: tuple[str, ...] = (
    "mission.shortest_path_m",
    "mission.max_speed_mps",
    "mission.fixed_time_s",
    "mission.inspection_dwell_s",
    "mission.deadline_s",
    "link.minimum_payload_bytes",
    "link.max_bandwidth_bps",
    "link.minimum_latency_s",
    "link.upload_deadline_s",
    "imaging.defect_size_m",
    "imaging.standoff_distance_m",
    "imaging.focal_length_m",
    "imaging.pixel_pitch_m",
    "imaging.minimum_resolvable_pixels",
    "total_defects",
    "geometrically_visible_defects",
)


INSPECTION_LINK_BOUND_FIELDS: frozenset[str] = frozenset(
    {
        "link.minimum_payload_bytes",
        "link.max_bandwidth_bps",
        "link.minimum_latency_s",
        "link.upload_deadline_s",
    }
)


class InspectionBoundSource(StrictModel):
    """Authoritative source for one physical inspection-bound input.

    The provider id resolves the file through the resolved EnvironmentSpec.
    The pointer is evaluated against that provider's digest-pinned JSON config;
    no value in this contract is authoritative until the resolver performs that
    lookup and compares it with the task package.
    """

    bound_field: InspectionBoundField
    provider_id: Identifier
    config_pointer: Annotated[str, Field(min_length=2)]

    @field_validator("config_pointer")
    @classmethod
    def normalized_json_pointer(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("inspection bound config_pointer must be a JSON pointer")
        tokens = value[1:].split("/")
        if not tokens or any(token == "" for token in tokens):
            raise ValueError(
                "inspection bound config_pointer cannot contain empty tokens"
            )
        for token in tokens:
            index = 0
            while index < len(token):
                if token[index] != "~":
                    index += 1
                    continue
                if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                    raise ValueError(
                        "inspection bound config_pointer has invalid escape"
                    )
                index += 2
        return value


class InspectionProviderConfigBinding(StrictModel):
    provider_id: Identifier
    config_digest: Sha256


class InspectionBoundResolution(StrictModel):
    bounds: InspectionBoundSpec
    provider_configs: tuple[InspectionProviderConfigBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def provider_ids_are_unique(self) -> "InspectionBoundResolution":
        provider_ids = [item.provider_id for item in self.provider_configs]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("resolved inspection provider config ids must be unique")
        return self


class InspectionTheoreticalBounds(StrictModel):
    """Necessary-condition ceilings computed from one resolved task package."""

    mission_minimum_time_s: float
    upload_minimum_time_s: float
    projected_defect_pixels: float
    success_upper_bound: Literal[0.0, 1.0]
    recall_upper_bound: float
    detection_f1_upper_bound: float
    failed_conditions: tuple[Identifier, ...]

    @field_validator(
        "mission_minimum_time_s",
        "upload_minimum_time_s",
        "projected_defect_pixels",
        "recall_upper_bound",
        "detection_f1_upper_bound",
    )
    @classmethod
    def finite_nonnegative(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name)

    @model_validator(mode="after")
    def score_bounds_are_consistent(self) -> "InspectionTheoreticalBounds":
        if self.recall_upper_bound > 1.0 or self.detection_f1_upper_bound > 1.0:
            raise ValueError("theoretical score bounds cannot exceed one")
        if self.success_upper_bound == 0.0 and not self.failed_conditions:
            raise ValueError("zero success bound requires a failed necessary condition")
        if self.success_upper_bound == 1.0 and self.failed_conditions:
            raise ValueError("positive success bound cannot list failed conditions")
        return self


class InspectionAssetRef(StrictModel):
    """An inspection asset is private to a world or verifier, never to an agent."""

    asset_id: Identifier
    file: FileRef
    visibility: Literal["world", "verifier"]
    media_type: Annotated[str, Field(min_length=1)]


class WorkOrderSpec(StrictModel):
    work_order_id: Identifier
    target_id: Identifier
    required_observation_id: Identifier
    arrival_tolerance_m: float
    report_artifact_type: Identifier
    upload_deadline_ns: Annotated[int, Field(gt=0)]

    @field_validator("arrival_tolerance_m")
    @classmethod
    def positive_tolerance(cls, value: float) -> float:
        return _finite(value, "arrival_tolerance_m", positive=True)

    def initial_state(self, *, run_id: Sha256) -> "WorkOrderState":
        return WorkOrderState(
            run_id=run_id,
            work_order_id=self.work_order_id,
            target_id=self.target_id,
            required_observation_id=self.required_observation_id,
            arrival_tolerance_m=self.arrival_tolerance_m,
            report_artifact_type=self.report_artifact_type,
            upload_deadline_ns=self.upload_deadline_ns,
        )


class ObservationTrigger(StrictModel):
    observation_id: Identifier
    target_id: Identifier
    provider_id: Identifier
    camera_id: Identifier
    min_distance_m: float
    max_distance_m: float
    min_view_angle_deg: float
    max_view_angle_deg: float
    earliest_time_ns: Annotated[int, Field(ge=0)]
    latest_time_ns: Annotated[int, Field(gt=0)]

    @field_validator("min_distance_m", "max_distance_m")
    @classmethod
    def positive_distance(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name, positive=True)

    @field_validator("min_view_angle_deg", "max_view_angle_deg")
    @classmethod
    def finite_angle(cls, value: float, info: object) -> float:
        name = getattr(info, "field_name", "value")
        return _finite(value, name)

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> "ObservationTrigger":
        if self.min_distance_m > self.max_distance_m:
            raise ValueError("min_distance_m cannot exceed max_distance_m")
        if not 0.0 <= self.min_view_angle_deg <= self.max_view_angle_deg <= 180.0:
            raise ValueError("view angle range must be within [0, 180] degrees")
        if self.earliest_time_ns >= self.latest_time_ns:
            raise ValueError("observation time window must be non-empty")
        return self


class ObservationContract(StrictModel):
    observation_id: Identifier
    target_id: Identifier
    simulation_asset_id: Identifier
    metadata_schema: FileRef
    media_type: Literal["application/json"]
    trigger: ObservationTrigger

    @model_validator(mode="after")
    def trigger_identity_matches(self) -> "ObservationContract":
        if self.trigger.observation_id != self.observation_id:
            raise ValueError("observation trigger id does not match contract")
        if self.trigger.target_id != self.target_id:
            raise ValueError("observation trigger target does not match contract")
        return self


class InspectionArtifactRequirement(ArtifactRequirement):
    """Inspection evidence binding over the canonical artifact contract."""

    evidence_kind: Literal[
        "business_state",
        "trajectory",
        "delivery",
        "observation",
        "sensor_frame",
        "camera_frame_data",
        "model_session_manifest",
        "model_interactions",
        "truth",
        "detection",
        "report",
        "theoretical_bounds",
        "event_log",
        "scene_state_history",
    ]


class InspectionActorGrant(StrictModel):
    actor_id: Identifier
    role: Literal["agent", "observation_provider", "business", "verifier"]


class InspectionGoal(StrictModel):
    goal_id: Identifier
    metric_id: Identifier
    operator: Literal["ge", "gt", "le", "lt", "eq"]
    threshold: float
    evidence: Literal["authoritative_state", "event_log", "artifact"]
    parameters: tuple[NamedValue, ...]

    @field_validator("threshold")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        return _finite(value, "threshold")

    @model_validator(mode="after")
    def parameter_names_are_unique(self) -> "InspectionGoal":
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("inspection goal parameter names must be unique")
        return self


class InspectionTaskPackage(StrictModel):
    """Strict task-scoped contract consumed by the business and verifier layers."""

    schema_version: Literal["aero-bench.inspection-task/v4"]
    task_id: Identifier
    verifier_id: Identifier
    network_delivery_required: bool
    actors: tuple[InspectionActorGrant, ...] = Field(min_length=1)
    assets: tuple[InspectionAssetRef, ...] = Field(min_length=1)
    work_orders: tuple[WorkOrderSpec, ...] = Field(min_length=1)
    observations: tuple[ObservationContract, ...] = Field(min_length=1)
    bounds: InspectionBoundSpec
    bound_sources: tuple[InspectionBoundSource, ...]
    artifact_requirements: tuple[InspectionArtifactRequirement, ...] = Field(
        min_length=1
    )
    goals: tuple[InspectionGoal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def package_contract_is_consistent(self) -> "InspectionTaskPackage":
        asset_ids = [asset.asset_id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("inspection asset ids must be unique")
        work_order_ids = [item.work_order_id for item in self.work_orders]
        if len(work_order_ids) != len(set(work_order_ids)):
            raise ValueError("inspection work order ids must be unique")
        observation_ids = [item.observation_id for item in self.observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("inspection observation ids must be unique")
        goal_ids = [goal.goal_id for goal in self.goals]
        if len(goal_ids) != len(set(goal_ids)):
            raise ValueError("inspection goal ids must be unique")
        artifact_ids = [item.artifact_id for item in self.artifact_requirements]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("inspection artifact ids must be unique")
        artifact_paths = [item.relative_path for item in self.artifact_requirements]
        if len(artifact_paths) != len(set(artifact_paths)):
            raise ValueError("inspection artifact relative paths must be unique")
        actor_ids = [actor.actor_id for actor in self.actors]
        if len(actor_ids) != len(set(actor_ids)):
            raise ValueError("inspection actor ids must be unique")
        actors_by_id = {actor.actor_id: actor for actor in self.actors}
        verifier = actors_by_id.get(self.verifier_id)
        if verifier is None or verifier.role != "verifier":
            raise ValueError("verifier_id must name an authorized verifier actor")
        if not any(actor.role == "agent" for actor in self.actors):
            raise ValueError("inspection package must declare an agent actor")
        if not any(actor.role == "observation_provider" for actor in self.actors):
            raise ValueError(
                "inspection package must declare an observation provider actor"
            )
        if TASK_LOCAL_BUSINESS_PRINCIPAL in actor_ids:
            raise ValueError(
                "inspection package actors cannot use the reserved task-local "
                "Business principal"
            )
        if sum(actor.role == "business" for actor in self.actors) > 1:
            raise ValueError("inspection package may declare at most one business actor")
        bound_fields = [source.bound_field for source in self.bound_sources]
        required_bound_fields = set(INSPECTION_BOUND_FIELDS)
        if not self.network_delivery_required:
            required_bound_fields -= INSPECTION_LINK_BOUND_FIELDS
        if set(bound_fields) != required_bound_fields or len(bound_fields) != len(
            set(bound_fields)
        ):
            raise ValueError(
                "inspection package must bind every required physical input exactly once"
            )
        evidence_kinds = [item.evidence_kind for item in self.artifact_requirements]
        if len(evidence_kinds) != len(set(evidence_kinds)):
            raise ValueError("inspection package repeats an evidence kind")
        required_evidence_kinds = {
            "business_state",
            "trajectory",
            "observation",
            "sensor_frame",
            "camera_frame_data",
            "truth",
            "detection",
            "report",
            "theoretical_bounds",
            "event_log",
            "scene_state_history",
        }
        optional_model_evidence_kinds = {
            "model_session_manifest",
            "model_interactions",
        }
        allowed_evidence_kinds = (
            required_evidence_kinds | {"delivery"} | optional_model_evidence_kinds
        )
        if not required_evidence_kinds <= set(evidence_kinds) or not set(
            evidence_kinds
        ) <= allowed_evidence_kinds:
            raise ValueError(
                "inspection package must declare every core evidence kind and only "
                "the paired model session or required network evidence may be optional"
            )
        if ("delivery" in evidence_kinds) != self.network_delivery_required:
            raise ValueError(
                "delivery evidence must exactly match network_delivery_required"
            )
        declared_model_evidence = set(evidence_kinds) & optional_model_evidence_kinds
        if declared_model_evidence and (
            declared_model_evidence != optional_model_evidence_kinds
        ):
            raise ValueError(
                "model session manifest and interactions evidence must be declared together"
            )
        report_requirement = next(
            item
            for item in self.artifact_requirements
            if item.evidence_kind == "report"
        )
        detection_requirement = next(
            item
            for item in self.artifact_requirements
            if item.evidence_kind == "detection"
        )
        truth_requirement = next(
            item for item in self.artifact_requirements if item.evidence_kind == "truth"
        )
        scene_state_history_requirement = next(
            item
            for item in self.artifact_requirements
            if item.evidence_kind == "scene_state_history"
        )
        sensor_frame_requirement = next(
            item
            for item in self.artifact_requirements
            if item.evidence_kind == "sensor_frame"
        )
        camera_frame_data_requirement = next(
            item
            for item in self.artifact_requirements
            if item.evidence_kind == "camera_frame_data"
        )
        model_session_requirement = next(
            (
                item
                for item in self.artifact_requirements
                if item.evidence_kind == "model_session_manifest"
            ),
            None,
        )
        model_interactions_requirement = next(
            (
                item
                for item in self.artifact_requirements
                if item.evidence_kind == "model_interactions"
            ),
            None,
        )
        observation_provider_ids = {
            observation.trigger.provider_id for observation in self.observations
        }
        if (
            sensor_frame_requirement.artifact_type != "sensor-frame"
            or observation_provider_ids != {sensor_frame_requirement.producer_id}
            or sensor_frame_requirement.visibility != "public"
            or sensor_frame_requirement.source_asset_id is not None
        ):
            raise ValueError(
                "sensor frames must be a public Observation Provider runtime artifact"
            )
        if (
            camera_frame_data_requirement.artifact_type != "camera-frame-data"
            or observation_provider_ids
            != {camera_frame_data_requirement.producer_id}
            or camera_frame_data_requirement.visibility != "public"
            or camera_frame_data_requirement.source_asset_id is not None
        ):
            raise ValueError(
                "camera frame data must be a public Observation Provider runtime artifact"
            )
        if model_session_requirement is not None:
            assert model_interactions_requirement is not None
            if (
                model_session_requirement.artifact_type
                != "model.session-manifest"
                or model_interactions_requirement.artifact_type
                != "model.interactions"
                or model_session_requirement.producer_id
                != model_interactions_requirement.producer_id
                or model_session_requirement.visibility != "private"
                or model_interactions_requirement.visibility != "private"
                or model_session_requirement.source_asset_id is not None
                or model_interactions_requirement.source_asset_id is not None
                or model_session_requirement.max_size_bytes > 1024 * 1024
                or model_interactions_requirement.max_size_bytes > 64 * 1024 * 1024
            ):
                raise ValueError(
                    "model session evidence must be private paired Driver artifacts"
                )
        if (
            scene_state_history_requirement.artifact_type != "scene.state-history"
            or scene_state_history_requirement.producer_id != "harness"
            or scene_state_history_requirement.visibility != "public"
            or scene_state_history_requirement.source_asset_id is not None
        ):
            raise ValueError(
                "scene state history must be a public Harness runtime artifact"
            )
        if report_requirement.visibility != "public":
            raise ValueError("inspection reports must be public evidence")
        if detection_requirement.visibility != "public":
            raise ValueError("inspection detections must be public evidence")
        if any(
            item.report_artifact_type != report_requirement.artifact_type
            for item in self.work_orders
        ):
            raise ValueError("work order report type must match the report artifact")
        observation_by_id = {item.observation_id: item for item in self.observations}
        asset_by_id = {item.asset_id: item for item in self.assets}
        truth_asset = asset_by_id.get(truth_requirement.source_asset_id or "")
        if (
            truth_requirement.producer_id != "bundle"
            or truth_requirement.visibility != "private"
            or truth_asset is None
            or truth_asset.visibility != "verifier"
        ):
            raise ValueError(
                "immutable truth evidence must bind a verifier-private bundle asset"
            )
        for work_order in self.work_orders:
            observation = observation_by_id.get(work_order.required_observation_id)
            if observation is None:
                raise ValueError(
                    "work order references an undeclared observation: "
                    f"{work_order.required_observation_id}"
                )
            if observation.target_id != work_order.target_id:
                raise ValueError("work order target and observation target differ")
        for observation in self.observations:
            asset = asset_by_id.get(observation.simulation_asset_id)
            if asset is None:
                raise ValueError(
                    "observation references an undeclared simulation asset: "
                    f"{observation.simulation_asset_id}"
                )
            if asset.visibility != "world":
                raise ValueError(
                    "observation simulation asset must be world-visible only"
                )
            if asset.media_type != "application/vnd.gazebo.sdf+xml":
                raise ValueError(
                    "observation simulation asset must be a Gazebo SDF model"
                )
            provider_actor = actors_by_id.get(observation.trigger.provider_id)
            if provider_actor is None or provider_actor.role != "observation_provider":
                raise ValueError(
                    "observation trigger provider must be an authorized provider actor"
                )
        if not any(asset.visibility == "verifier" for asset in self.assets):
            raise ValueError("inspection package must declare verifier-private truth")
        return self

    def initial_business_state(self, *, run_id: Sha256) -> "InspectionBusinessState":
        return InspectionBusinessState(
            run_id=run_id,
            work_orders=tuple(
                work_order.initial_state(run_id=run_id)
                for work_order in self.work_orders
            ),
        )


class WorkOrderStatus(str, Enum):
    CREATED = "created"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    OBSERVATION_READY = "observation_ready"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkOrderTransition(StrictModel):
    run_id: Sha256
    work_order_id: Identifier
    event: Literal[
        "claim",
        "start",
        "observation_ready",
        "submit",
        "complete",
        "fail",
        "cancel",
    ]
    actor_id: Identifier
    actor_role: Literal["agent", "observation_provider", "business", "verifier"]
    time: SimulationTime
    observation_id: Identifier | None = None
    report_payload_digest: Sha256 | None = None
    reason: Identifier | None = None


class WorkOrderState(StrictModel):
    run_id: Sha256
    work_order_id: Identifier
    target_id: Identifier
    required_observation_id: Identifier
    arrival_tolerance_m: float
    report_artifact_type: Identifier
    upload_deadline_ns: Annotated[int, Field(gt=0)]
    status: WorkOrderStatus = WorkOrderStatus.CREATED
    version: Annotated[int, Field(ge=0)] = 0
    last_time: SimulationTime = SimulationTime(tick=0, sim_time_ns=0)
    claimed_by: Identifier | None = None
    observation_id: Identifier | None = None
    report_payload_digest: Sha256 | None = None
    failure_reason: Identifier | None = None

    @model_validator(mode="after")
    def state_invariants(self) -> "WorkOrderState":
        if self.status == WorkOrderStatus.CREATED and self.version != 0:
            raise ValueError("created work order must have version zero")
        if self.status != WorkOrderStatus.CREATED and self.version == 0:
            raise ValueError("non-created work order must have a positive version")
        if self.status == WorkOrderStatus.CREATED and any(
            value is not None
            for value in (
                self.claimed_by,
                self.observation_id,
                self.report_payload_digest,
                self.failure_reason,
            )
        ):
            raise ValueError("created work order cannot carry execution state")
        if (
            self.status
            in {
                WorkOrderStatus.CLAIMED,
                WorkOrderStatus.IN_PROGRESS,
                WorkOrderStatus.OBSERVATION_READY,
                WorkOrderStatus.SUBMITTED,
                WorkOrderStatus.COMPLETED,
            }
            and self.claimed_by is None
        ):
            raise ValueError("active work order must have a claimant")
        if (
            self.status
            in {
                WorkOrderStatus.OBSERVATION_READY,
                WorkOrderStatus.SUBMITTED,
                WorkOrderStatus.COMPLETED,
            }
            and self.observation_id is None
        ):
            raise ValueError("observation state must name an observation")
        if self.status in {
            WorkOrderStatus.CLAIMED,
            WorkOrderStatus.IN_PROGRESS,
        } and any(
            value is not None
            for value in (
                self.observation_id,
                self.report_payload_digest,
                self.failure_reason,
            )
        ):
            raise ValueError("pre-observation state cannot carry observation or report")
        if self.status == WorkOrderStatus.OBSERVATION_READY and any(
            value is not None
            for value in (self.report_payload_digest, self.failure_reason)
        ):
            raise ValueError("observation-ready state cannot carry report or failure")
        if self.status in {WorkOrderStatus.SUBMITTED, WorkOrderStatus.COMPLETED}:
            if self.report_payload_digest is None:
                raise ValueError("submitted work order must name report payload digest")
            if self.failure_reason is not None:
                raise ValueError("successful work order cannot carry failure reason")
        if self.status in {WorkOrderStatus.FAILED, WorkOrderStatus.CANCELLED}:
            if self.failure_reason is None:
                raise ValueError("terminal unsuccessful state requires a reason")
        if (
            self.observation_id is not None
            and self.observation_id != self.required_observation_id
        ):
            raise ValueError(
                "work order observation does not match required observation"
            )
        return self


class InspectionBusinessState(StrictModel):
    run_id: Sha256
    current_time: SimulationTime = SimulationTime(tick=0, sim_time_ns=0)
    work_orders: tuple[WorkOrderState, ...] = Field(min_length=1)
    version: Annotated[int, Field(ge=0)] = 0
    processed_command_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def state_contract_is_consistent(self) -> "InspectionBusinessState":
        if any(item.run_id != self.run_id for item in self.work_orders):
            raise ValueError("business state contains another run id")
        ids = [item.work_order_id for item in self.work_orders]
        if len(ids) != len(set(ids)):
            raise ValueError("business state work order ids must be unique")
        if len(self.processed_command_ids) != len(set(self.processed_command_ids)):
            raise ValueError("processed command ids must be unique")
        return self


class _CommandBase(StrictModel):
    command_id: Identifier
    work_order_id: Identifier
    actor_id: Identifier
    issued_at: SimulationTime


class ClaimWorkOrderCommand(_CommandBase):
    kind: Literal["claim"] = "claim"
    actor_role: Literal["agent"] = "agent"


class StartWorkOrderCommand(_CommandBase):
    kind: Literal["start"] = "start"
    actor_role: Literal["agent"] = "agent"


class ObservationReadyCommand(_CommandBase):
    kind: Literal["observation_ready"] = "observation_ready"
    actor_role: Literal["observation_provider"] = "observation_provider"
    observation_id: Identifier


class SubmitInspectionCommand(_CommandBase):
    kind: Literal["submit"] = "submit"
    actor_role: Literal["agent"] = "agent"
    observation_id: Identifier
    report_payload_digest: Sha256


class CompleteWorkOrderCommand(_CommandBase):
    kind: Literal["complete"] = "complete"
    actor_role: Literal["business"] = "business"


class FailWorkOrderCommand(_CommandBase):
    kind: Literal["fail"] = "fail"
    actor_role: Literal["business"] = "business"
    reason: Identifier


class CancelWorkOrderCommand(_CommandBase):
    kind: Literal["cancel"] = "cancel"
    actor_role: Literal["business"] = "business"
    reason: Identifier


InspectionCommand: TypeAlias = Annotated[
    ClaimWorkOrderCommand
    | StartWorkOrderCommand
    | ObservationReadyCommand
    | SubmitInspectionCommand
    | CompleteWorkOrderCommand
    | FailWorkOrderCommand
    | CancelWorkOrderCommand,
    Field(discriminator="kind"),
]


class BusinessCommandEnvelope(StrictModel):
    run_id: Sha256
    command: InspectionCommand


class BusinessHistoryRecord(StrictModel):
    sequence: Annotated[int, Field(ge=0)]
    command: BusinessCommandEnvelope
    transition: WorkOrderTransition
    previous_hash: Sha256
    record_hash: Sha256


class BusinessCommandResult(StrictModel):
    run_id: Sha256
    command_id: Identifier
    transition: WorkOrderTransition
    state: InspectionBusinessState
    history_record: BusinessHistoryRecord


class BusinessQuery(StrictModel):
    run_id: Sha256
    query_id: Identifier
    kind: Literal["work_order", "summary"]
    issued_at: SimulationTime
    work_order_id: Identifier | None = None

    @model_validator(mode="after")
    def query_target_is_consistent(self) -> "BusinessQuery":
        if self.kind == "work_order" and self.work_order_id is None:
            raise ValueError("work_order query requires work_order_id")
        if self.kind == "summary" and self.work_order_id is not None:
            raise ValueError("summary query cannot select a work order")
        return self


class WorkOrderView(StrictModel):
    work_order_id: Identifier
    target_id: Identifier
    status: WorkOrderStatus
    version: Annotated[int, Field(ge=0)]
    last_time: SimulationTime
    claimed_by: Identifier | None = None
    observation_id: Identifier | None = None
    report_payload_digest: Sha256 | None = None


class BusinessQueryResult(StrictModel):
    run_id: Sha256
    query_id: Identifier
    observed_at: SimulationTime
    work_orders: tuple[WorkOrderView, ...] = Field(min_length=1)


class AuthoritativeViewContext(StrictModel):
    """Provider-produced geometry; it is not an agent claim or an image."""

    source_provider_id: Identifier
    camera_id: Identifier
    observation_id: Identifier
    target_id: Identifier
    time: SimulationTime
    distance_m: float
    view_angle_deg: float
    camera_pose_digest: Sha256
    target_pose_digest: Sha256
    provider_config_digest: Sha256

    @field_validator("distance_m")
    @classmethod
    def positive_distance(cls, value: float) -> float:
        return _finite(value, "distance_m", positive=True)

    @field_validator("view_angle_deg")
    @classmethod
    def finite_angle(cls, value: float) -> float:
        return _finite(value, "view_angle_deg")


class ObservationRequest(StrictModel):
    run_id: Sha256
    agent_id: Identifier
    work_order_id: Identifier
    observation_id: Identifier
    requested_at: SimulationTime


class ObservationReceipt(StrictModel):
    run_id: Sha256
    agent_id: Identifier
    work_order_id: Identifier
    observation_id: Identifier
    status: Literal["captured", "not_captured"]
    time: SimulationTime
    payload_digest: Sha256 | None = None
    reason: Identifier | None = None

    @model_validator(mode="after")
    def capture_fields_are_consistent(self) -> "ObservationReceipt":
        if self.status == "captured" and self.payload_digest is None:
            raise ValueError("captured observation requires a payload digest")
        if self.status == "not_captured" and self.reason is None:
            raise ValueError("rejected observation requires a reason")
        return self


class PublicObservation(StrictModel):
    """Agent-visible observation envelope; it deliberately has no asset or truth fields."""

    run_id: Sha256
    agent_id: Identifier
    observation_id: Identifier
    time: SimulationTime
    schema_file: FileRef
    payload_digest: Sha256


class ObservationMetadata(StrictModel):
    """Verifier-private geometry and provenance for one real RGB capture."""

    schema_version: Literal["aero-bench.inspection-observation-metadata/v1"]
    source_artifact_id: Identifier
    run_id: Sha256
    work_order_id: Identifier
    observation_id: Identifier
    target_id: Identifier
    time: SimulationTime
    camera_id: Identifier
    frame_id: Identifier
    selector: Annotated[str, Field(min_length=1, max_length=512)]
    vehicle_id: Identifier
    engine_sim_time_ns: Annotated[int, Field(ge=0)]
    logical_origin_engine_ns: Annotated[int, Field(ge=0)]
    image_sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0, le=2 * 1024 * 1024)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    media_type: Literal["image/png"]
    source: Literal["gazebo.camera"]
    capture_pose_source: Literal["gazebo.pose.private_digest"]
    camera_pose_sha256: Sha256
    target_pose_sha256: Sha256
    payload_digest: Sha256
    view: AuthoritativeViewContext
    visual_kind: Literal["gazebo_rgb"]
    visual_status: Literal["captured"]

    @model_validator(mode="after")
    def rgb_capture_is_internally_consistent(self) -> "ObservationMetadata":
        if self.view.observation_id != self.observation_id:
            raise ValueError("observation view id does not match metadata")
        if self.view.target_id != self.target_id:
            raise ValueError("observation view target does not match metadata")
        if self.view.camera_id != self.camera_id:
            raise ValueError("observation view camera does not match metadata")
        if self.view.time != self.time:
            raise ValueError("observation view time does not match metadata")
        if self.selector != f"frames/{self.frame_id}":
            raise ValueError("observation selector must identify its RGB frame")
        if (
            self.engine_sim_time_ns - self.logical_origin_engine_ns
            != self.time.sim_time_ns
        ):
            raise ValueError(
                "observation engine time does not match its logical simulation time"
            )
        if self.camera_pose_sha256 != self.view.camera_pose_digest:
            raise ValueError("RGB camera pose digest disagrees with view authority")
        if self.target_pose_sha256 != self.view.target_pose_digest:
            raise ValueError("RGB target pose digest disagrees with view authority")
        return self

    def public_payload(self, *, image_base64: str) -> dict[str, object]:
        return rgb_observation_public_payload(
            target_id=self.target_id,
            camera_id=self.camera_id,
            distance_m=self.view.distance_m,
            view_angle_deg=self.view.view_angle_deg,
            frame_id=self.frame_id,
            vehicle_id=self.vehicle_id,
            engine_sim_time_ns=self.engine_sim_time_ns,
            logical_origin_engine_ns=self.logical_origin_engine_ns,
            image_sha256=self.image_sha256,
            size_bytes=self.size_bytes,
            selector=self.selector,
            width=self.width,
            height=self.height,
            capture_pose_source=self.capture_pose_source,
            camera_pose_sha256=self.camera_pose_sha256,
            target_pose_sha256=self.target_pose_sha256,
            image_base64=image_base64,
        )


class PublicSensorFrameRecord(StrictModel):
    """Public content-addressed handle for one real Gazebo RGB frame."""

    schema_version: Literal["aero-bench.public-sensor-frame-artifact/v2"]
    source_artifact_id: Identifier
    run_id: Sha256
    frame_id: Identifier
    selector: Annotated[str, Field(min_length=1, max_length=512)]
    observation_id: Identifier
    time: SimulationTime
    camera_id: Identifier
    vehicle_id: Identifier
    engine_sim_time_ns: Annotated[int, Field(ge=0)]
    logical_origin_engine_ns: Annotated[int, Field(ge=0)]
    image_sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0, le=2 * 1024 * 1024)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    media_type: Literal["image/png"]
    source: Literal["gazebo.camera"]
    capture_pose_source: Literal["gazebo.pose.private_digest"]
    camera_pose_sha256: Sha256
    target_pose_sha256: Sha256
    payload_digest: Sha256

    @model_validator(mode="after")
    def selector_identifies_frame(self) -> "PublicSensorFrameRecord":
        if self.selector != f"frames/{self.frame_id}":
            raise ValueError("public sensor-frame selector must identify its frame")
        if (
            self.engine_sim_time_ns - self.logical_origin_engine_ns
            != self.time.sim_time_ns
        ):
            raise ValueError(
                "public sensor-frame engine time does not match logical simulation time"
            )
        return self


class CameraFrameDataRecord(StrictModel):
    """Loader-issued exact PNG bytes from the sealed camera-frame archive."""

    source_artifact_id: Identifier
    frame_id: Identifier
    image_sha256: Sha256
    size_bytes: Annotated[int, Field(gt=0, le=2 * 1024 * 1024)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    media_type: Literal["image/png"]
    png_bytes: bytes

    @model_validator(mode="after")
    def bytes_match_declared_identity(self) -> "CameraFrameDataRecord":
        if len(self.png_bytes) != self.size_bytes:
            raise ValueError("camera frame byte size disagrees with its declaration")
        if hashlib.sha256(self.png_bytes).hexdigest() != self.image_sha256:
            raise ValueError("camera frame bytes disagree with their SHA-256")
        return self


class TrajectoryWgs84Position(StrictModel):
    longitude_deg: float
    latitude_deg: float
    altitude_m: float

    @field_validator("longitude_deg", "latitude_deg", "altitude_m")
    @classmethod
    def finite_components(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("trajectory WGS84 position must be finite")
        return value

    @field_validator("longitude_deg")
    @classmethod
    def longitude_in_range(cls, value: float) -> float:
        if not -180.0 <= value <= 180.0:
            raise ValueError("trajectory longitude_deg must be in [-180, 180]")
        return value

    @field_validator("latitude_deg")
    @classmethod
    def latitude_in_range(cls, value: float) -> float:
        if not -90.0 <= value <= 90.0:
            raise ValueError("trajectory latitude_deg must be in [-90, 90]")
        return value


class TrajectoryOrientation(StrictModel):
    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("qw", "qx", "qy", "qz")
    @classmethod
    def finite_components(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("trajectory orientation must be finite")
        return value

    @model_validator(mode="after")
    def unit_quaternion(self) -> "TrajectoryOrientation":
        norm = math.sqrt(self.qw**2 + self.qx**2 + self.qy**2 + self.qz**2)
        if abs(norm - 1.0) > 1e-6:
            raise ValueError("trajectory orientation must be a unit quaternion")
        return self


class TrajectoryLinearVelocity(StrictModel):
    east_mps: float
    north_mps: float
    up_mps: float

    @field_validator("east_mps", "north_mps", "up_mps")
    @classmethod
    def finite_components(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("trajectory linear velocity must be finite")
        return value


class TrajectoryEvidence(StrictModel):
    source_artifact_id: Identifier
    run_id: Sha256
    work_order_id: Identifier
    target_id: Identifier
    vehicle_id: Identifier
    time: SimulationTime
    position_wgs84: TrajectoryWgs84Position
    target_position_wgs84: TrajectoryWgs84Position
    orientation: TrajectoryOrientation
    linear_velocity_enu_mps: TrajectoryLinearVelocity
    flight_mode: Annotated[str, Field(min_length=1, max_length=64)]
    armed: bool
    battery_percent: float
    collision_contact: bool
    distance_to_target_m: float

    @field_validator("distance_to_target_m")
    @classmethod
    def nonnegative_distance(cls, value: float) -> float:
        return _finite(value, "distance_to_target_m")

    @field_validator("battery_percent")
    @classmethod
    def bounded_battery_percent(cls, value: float) -> float:
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError("battery_percent must be finite and in [0, 100]")
        return value


class DeliveryEvidence(StrictModel):
    source_artifact_id: Identifier
    run_id: Sha256
    work_order_id: Identifier
    message_id: Identifier
    payload_digest: Sha256
    sent_at: SimulationTime
    delivered_at: SimulationTime | None = None

    @model_validator(mode="after")
    def delivery_time_is_monotonic(self) -> "DeliveryEvidence":
        if self.delivered_at is not None:
            if self.delivered_at.tick < self.sent_at.tick:
                raise ValueError("delivery tick moved backwards")
            if self.delivered_at.sim_time_ns < self.sent_at.sim_time_ns:
                raise ValueError("delivery time moved backwards")
        return self


class DefectTruth(StrictModel):
    """Immutable verifier-private truth bound to a sealed source asset, not a run."""

    source_artifact_id: Identifier
    defect_id: Identifier
    target_id: Identifier
    geometrically_visible: bool


class DefectDetection(StrictModel):
    schema_version: Literal["aero-bench.inspection-detection/v1"] = (
        "aero-bench.inspection-detection/v1"
    )
    source_artifact_id: Identifier
    run_id: Sha256
    work_order_id: Identifier
    observation_id: Identifier
    frame_id: Identifier
    image_sha256: Sha256
    defect_id: Identifier
    target_id: Identifier


class InspectionReportDetection(StrictModel):
    defect_id: Identifier
    target_id: Identifier
    frame_id: Identifier
    image_sha256: Sha256


class InspectionReportPayload(StrictModel):
    """One agent report, cryptographically bound to observation and detections."""

    schema_version: Literal["aero-bench.inspection-report/v2"] = (
        "aero-bench.inspection-report/v2"
    )
    source_artifact_id: Identifier
    run_id: Sha256
    work_order_id: Identifier
    observation_id: Identifier
    observation_payload_digest: Sha256
    detection_payload_digest: Sha256
    detections: tuple[InspectionReportDetection, ...] = ()

    @model_validator(mode="after")
    def detections_are_unique(self) -> "InspectionReportPayload":
        detection_ids = [
            (detection.defect_id, detection.target_id) for detection in self.detections
        ]
        if len(detection_ids) != len(set(detection_ids)):
            raise ValueError("report detections must be unique")
        return self


class EvidenceBinding(StrictModel):
    artifact_id: Identifier
    artifact_type: Identifier
    producer_id: Identifier
    visibility: Literal["public", "private"]
    evidence_kind: Literal[
        "business_state",
        "trajectory",
        "delivery",
        "observation",
        "sensor_frame",
        "camera_frame_data",
        "model_session_manifest",
        "model_interactions",
        "truth",
        "detection",
        "report",
        "theoretical_bounds",
        "event_log",
        "scene_state_history",
    ]


class EventLedgerEvidence(StrictModel):
    source_artifact_id: Identifier
    records: tuple[LedgerRecord, ...] = Field(min_length=0)


class BusinessLedgerBinding(StrictModel):
    business_history_sequence: Annotated[int, Field(ge=0)]
    business_history_record_hash: Sha256
    command_id: Identifier
    ledger_sequence: Annotated[int, Field(ge=0)]
    ledger_event_hash: Sha256


class BusinessStateEvidence(StrictModel):
    source_artifact_id: Identifier
    event_chain_root: Sha256
    business_history_root: Sha256
    history: tuple[BusinessHistoryRecord, ...]
    state: InspectionBusinessState


class TheoreticalBoundsEvidence(StrictModel):
    source_artifact_id: Identifier
    run_id: Sha256
    bounds: InspectionTheoreticalBounds
    provider_configs: tuple[InspectionProviderConfigBinding, ...] = Field(min_length=1)


class ModelSessionManifestEvidence(StrictModel):
    source_artifact_id: Identifier
    manifest: AgentSessionManifest


class ModelInteractionsEvidence(StrictModel):
    source_artifact_id: Identifier
    records: tuple[InteractionRecord, ...] = Field(min_length=1)


class InspectionEvidenceBundle(StrictModel):
    """Typed verifier input. Every record must be backed by a SealManifest artifact."""

    run_id: Sha256
    seal: SealManifest
    bindings: tuple[EvidenceBinding, ...] = Field(min_length=1)
    event_ledger: EventLedgerEvidence
    scene_states: tuple[SceneState, ...] = Field(min_length=1)
    business_state: BusinessStateEvidence
    business_ledger_bindings: tuple[BusinessLedgerBinding, ...]
    theoretical_bounds: TheoreticalBoundsEvidence
    trajectory: tuple[TrajectoryEvidence, ...]
    deliveries: tuple[DeliveryEvidence, ...]
    observations: tuple[ObservationMetadata, ...]
    sensor_frames: tuple[PublicSensorFrameRecord, ...]
    camera_frames: tuple[CameraFrameDataRecord, ...]
    model_session: ModelSessionManifestEvidence | None = None
    model_interactions: ModelInteractionsEvidence | None = None
    truth: tuple[DefectTruth, ...]
    detections: tuple[DefectDetection, ...]
    report: tuple[InspectionReportPayload, ...]

    @model_validator(mode="after")
    def input_run_ids_match(self) -> "InspectionEvidenceBundle":
        if (
            self.seal.run_id != self.run_id
            or self.business_state.state.run_id != self.run_id
            or self.theoretical_bounds.run_id != self.run_id
            or any(state.run_id != self.run_id for state in self.scene_states)
        ):
            raise ValueError(
                "sealed evidence and business state must name the same run"
            )
        if (self.model_session is None) != (self.model_interactions is None):
            raise ValueError(
                "model session manifest and interactions evidence must be paired"
            )
        if self.model_session is not None:
            assert self.model_interactions is not None
            if (
                self.model_session.manifest.run_id != self.run_id
                or self.model_session.manifest.attempt_id != self.seal.attempt_id
                or any(
                    record.run_id != self.run_id
                    or record.attempt_id != self.seal.attempt_id
                    for record in self.model_interactions.records
                )
            ):
                raise ValueError("model session evidence belongs to another run")
        binding_ids = [item.artifact_id for item in self.bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("evidence binding artifact ids must be unique")
        business_sequences = [
            item.business_history_sequence for item in self.business_ledger_bindings
        ]
        ledger_sequences = [
            item.ledger_sequence for item in self.business_ledger_bindings
        ]
        if len(business_sequences) != len(set(business_sequences)):
            raise ValueError("business ledger bindings repeat a history sequence")
        if len(ledger_sequences) != len(set(ledger_sequences)):
            raise ValueError("business ledger bindings repeat a ledger sequence")
        for record in self.business_state.history:
            if (
                record.command.run_id != self.run_id
                or record.transition.run_id != self.run_id
            ):
                raise ValueError("business history record belongs to another run")
        trajectory_keys = [
            (record.work_order_id, record.time.tick, record.time.sim_time_ns)
            for record in self.trajectory
        ]
        if len(trajectory_keys) != len(set(trajectory_keys)):
            raise ValueError("trajectory evidence repeats a work-order time")
        delivery_ids = [record.message_id for record in self.deliveries]
        if len(delivery_ids) != len(set(delivery_ids)):
            raise ValueError("delivery evidence repeats a message id")
        observation_keys = [
            (
                record.work_order_id,
                record.observation_id,
                record.time.tick,
                record.time.sim_time_ns,
            )
            for record in self.observations
        ]
        if len(observation_keys) != len(set(observation_keys)):
            raise ValueError(
                "observation evidence repeats a work-order barrier capture"
            )
        sensor_frame_ids = [record.frame_id for record in self.sensor_frames]
        sensor_frame_selectors = [record.selector for record in self.sensor_frames]
        if (
            len(sensor_frame_ids) != len(set(sensor_frame_ids))
            or len(sensor_frame_selectors) != len(set(sensor_frame_selectors))
            or any(record.run_id != self.run_id for record in self.sensor_frames)
        ):
            raise ValueError(
                "public sensor-frame evidence has duplicate identity or another run"
            )
        camera_frame_ids = [record.frame_id for record in self.camera_frames]
        if len(camera_frame_ids) != len(set(camera_frame_ids)):
            raise ValueError("camera frame-data evidence repeats a frame identity")
        truth_ids = [record.defect_id for record in self.truth]
        if len(truth_ids) != len(set(truth_ids)):
            raise ValueError("truth evidence repeats a defect id")
        detection_keys = [
            (record.work_order_id, record.defect_id) for record in self.detections
        ]
        if len(detection_keys) != len(set(detection_keys)):
            raise ValueError("detection evidence repeats a defect")
        report_work_orders = [item.work_order_id for item in self.report]
        if len(report_work_orders) != len(set(report_work_orders)):
            raise ValueError("report evidence repeats a work order")
        for record in (
            *self.trajectory,
            *self.deliveries,
            *self.observations,
            *self.detections,
            *self.report,
        ):
            if record.run_id != self.run_id:
                raise ValueError("evidence record belongs to another run")
        return self


__all__ = [
    "AuthoritativeViewContext",
    "BusinessStateEvidence",
    "BusinessLedgerBinding",
    "BusinessCommandEnvelope",
    "BusinessCommandResult",
    "BusinessHistoryRecord",
    "BusinessQuery",
    "BusinessQueryResult",
    "CancelWorkOrderCommand",
    "CameraFrameDataRecord",
    "ClaimWorkOrderCommand",
    "CompleteWorkOrderCommand",
    "DefectDetection",
    "DefectTruth",
    "DeliveryEvidence",
    "EvidenceBinding",
    "EventLedgerEvidence",
    "FailWorkOrderCommand",
    "INSPECTION_BOUND_FIELDS",
    "INSPECTION_LINK_BOUND_FIELDS",
    "InspectionBoundField",
    "ImagingBoundSpec",
    "InspectionArtifactRequirement",
    "InspectionActorGrant",
    "InspectionAssetRef",
    "InspectionBoundSpec",
    "InspectionBoundResolution",
    "InspectionBoundSource",
    "InspectionBusinessState",
    "InspectionCommand",
    "InspectionEvidenceBundle",
    "InspectionGoal",
    "InspectionReportDetection",
    "InspectionReportPayload",
    "InspectionTaskPackage",
    "InspectionTheoreticalBounds",
    "ModelInteractionsEvidence",
    "ModelSessionManifestEvidence",
    "InspectionProviderConfigBinding",
    "LinkBoundSpec",
    "EXTERNAL_VISUAL_KIND",
    "EXTERNAL_VISUAL_STATUS",
    "GEOMETRY_OBSERVATION_SCHEMA",
    "RGB_OBSERVATION_SCHEMA",
    "MissionBoundSpec",
    "geometry_observation_public_payload",
    "rgb_observation_public_payload",
    "ObservationContract",
    "ObservationMetadata",
    "ObservationReadyCommand",
    "ObservationReceipt",
    "ObservationRequest",
    "ObservationTrigger",
    "PublicObservation",
    "PublicSensorFrameRecord",
    "StartWorkOrderCommand",
    "SubmitInspectionCommand",
    "TASK_LOCAL_BUSINESS_PRINCIPAL",
    "TrajectoryEvidence",
    "TrajectoryLinearVelocity",
    "TrajectoryOrientation",
    "TrajectoryWgs84Position",
    "TheoreticalBoundsEvidence",
    "WorkOrderSpec",
    "WorkOrderState",
    "WorkOrderStatus",
    "WorkOrderTransition",
    "WorkOrderView",
]
