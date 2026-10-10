"""Strict resolved-scenario compiler for the World Simulation vertical."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    FileRef,
    GoalSpec,
    Identifier,
    Sha256,
    StrictModel,
    TaskSpec,
)
from aero_bench.providers.stages import (
    BUSINESS_ENVIRONMENT,
    NETWORK,
    ProviderStage,
    parse_runtime_stage,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world import contracts, frame_math

RESOLVED_SCENARIO_SCHEMA_VERSION = "aero-bench.resolved-scenario/v4"
SCALAR_GRID_SCHEMA_VERSION = "aero-bench.scalar-grid/v1"
_SOLVER_MAX_ITERATIONS = 80
_SOLVER_TOLERANCE_M = 1e-9
_MAX_SEED = 9_223_372_036_854_775_807


def _require_finite(name: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _require_sorted_unique(
    label: str,
    values: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique")
    if normalized != tuple(sorted(normalized)):
        raise ValueError(f"{label} values must be sorted")
    return normalized


def _require_sorted_records(
    label: str,
    values: Sequence[StrictModel],
    key_name: str,
) -> None:
    keys = tuple(getattr(value, key_name) for value in values)
    _require_sorted_unique(label, keys)


def _selector_parts(selector: str) -> tuple[str, str | None]:
    path, fragment = (
        selector.split("#", maxsplit=1) if "#" in selector else (selector, None)
    )
    return path, fragment


def _selector_file_ref(selector: contracts.ArtifactSelector) -> FileRef:
    path, _fragment = _selector_parts(selector.selector)
    return FileRef(path=path, sha256=selector.sha256)


def _verify_selector_file(
    *,
    reader: BundleReader,
    selector: contracts.ArtifactSelector,
    label: str,
    expected_size: int | None,
) -> tuple[str, str | None, int]:
    reference = _selector_file_ref(selector)
    resolved_path = reader.resolve_file(reference)
    size_bytes = resolved_path.stat().st_size
    if expected_size is not None and size_bytes != expected_size:
        raise ValueError(
            f"{label} byte_size mismatch: expected {expected_size}, got {size_bytes}"
        )
    if size_bytes <= 0:
        raise ValueError(f"{label} must be non-empty")
    selector_path, fragment = _selector_parts(selector.selector)
    return selector_path, fragment, size_bytes


def _validate_seed_value(seed: object) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if seed < 0 or seed > _MAX_SEED:
        raise ValueError(f"seed must be in [0, {_MAX_SEED}]")
    return seed


class ScalarGrid(StrictModel):
    schema_version: Literal["aero-bench.scalar-grid/v1"]
    frame_id: Literal["ENU"]
    east_axis_m: tuple[float, ...] = Field(min_length=2)
    north_axis_m: tuple[float, ...] = Field(min_length=2)
    values_m: tuple[tuple[float, ...], ...] = Field(min_length=2)

    @field_validator("east_axis_m", "north_axis_m")
    @classmethod
    def finite_axis(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        for item in value:
            _require_finite("scalar grid axis", item)
        return value

    @field_validator("values_m")
    @classmethod
    def finite_values(
        cls,
        value: tuple[tuple[float, ...], ...],
    ) -> tuple[tuple[float, ...], ...]:
        for row in value:
            for item in row:
                _require_finite("scalar grid value", item)
        return value

    @model_validator(mode="after")
    def dimensions_match(self) -> "ScalarGrid":
        if self.east_axis_m != tuple(sorted(self.east_axis_m)):
            raise ValueError("scalar grid east_axis_m must be sorted")
        if self.north_axis_m != tuple(sorted(self.north_axis_m)):
            raise ValueError("scalar grid north_axis_m must be sorted")
        if len(set(self.east_axis_m)) != len(self.east_axis_m):
            raise ValueError("scalar grid east_axis_m must be unique")
        if len(set(self.north_axis_m)) != len(self.north_axis_m):
            raise ValueError("scalar grid north_axis_m must be unique")
        if len(self.values_m) != len(self.north_axis_m):
            raise ValueError("scalar grid values_m row count must equal north_axis_m")
        for row in self.values_m:
            if len(row) != len(self.east_axis_m):
                raise ValueError(
                    "scalar grid values_m column count must equal east_axis_m"
                )
        return self

    def sample(
        self, east_m: float, north_m: float, interpolation: contracts.InterpolationKind
    ) -> float:
        try:
            return frame_math.ScalarGridSampler(
                east_axis_m=self.east_axis_m,
                north_axis_m=self.north_axis_m,
                values_m=self.values_m,
            ).sample(east_m, north_m, interpolation)
        except frame_math.FrameMathError as error:
            raise ValueError("scalar grid sample is invalid") from error


class ResolvedQuaternion(StrictModel):
    qw: float
    qx: float
    qy: float
    qz: float

    @field_validator("qw", "qx", "qy", "qz")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("quaternion", value)

    @model_validator(mode="after")
    def canonical_unit(self) -> "ResolvedQuaternion":
        frame_math.UnitQuaternion(self.qw, self.qx, self.qy, self.qz)
        canonical = frame_math._canonical_quaternion_sign(
            self.qw, self.qx, self.qy, self.qz
        )
        if (self.qw, self.qx, self.qy, self.qz) != canonical:
            raise ValueError("quaternion must use the canonical sign representation")
        return self


class ResolvedRotationMatrix(StrictModel):
    rows: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    @model_validator(mode="after")
    def valid_matrix(self) -> "ResolvedRotationMatrix":
        frame_math.RotationMatrix(self.rows)
        return self


class ResolvedEnuPosition(StrictModel):
    east_m: float
    north_m: float
    up_m: float

    @field_validator("east_m", "north_m", "up_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("ENU position", value)


class ResolvedNedPosition(StrictModel):
    north_m: float
    east_m: float
    down_m: float

    @field_validator("north_m", "east_m", "down_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("NED position", value)


class ResolvedEcefPosition(StrictModel):
    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("ECEF position", value)


class ResolvedCartesianVector(StrictModel):
    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("Cartesian vector", value)


class ResolvedWgs84Position(StrictModel):
    longitude_deg: float
    latitude_deg: float
    ellipsoid_height_m: float

    @field_validator("longitude_deg", "latitude_deg", "ellipsoid_height_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("WGS84 position", value)

    @model_validator(mode="after")
    def ranges(self) -> "ResolvedWgs84Position":
        if not -180.0 <= self.longitude_deg < 180.0:
            raise ValueError("resolved WGS84 longitude_deg must be in [-180, 180)")
        if not -90.0 < self.latitude_deg < 90.0:
            raise ValueError("resolved WGS84 latitude_deg must be in (-90, 90)")
        return self


class ResolvedCoordinate(StrictModel):
    enu: ResolvedEnuPosition
    ned: ResolvedNedPosition
    ecef: ResolvedEcefPosition
    wgs84: ResolvedWgs84Position
    geoid_separation_m: float
    amsl_m: float
    terrain_amsl_m: float
    agl_m: float

    @field_validator("geoid_separation_m", "amsl_m", "terrain_amsl_m", "agl_m")
    @classmethod
    def finite(cls, value: float) -> float:
        return _require_finite("resolved coordinate scalar", value)


class ResolvedPose(StrictModel):
    position: ResolvedCoordinate
    orientation_enu: ResolvedQuaternion
    orientation_ned: ResolvedQuaternion


class ResolvedFrameAuthority(StrictModel):
    geodetic_frame_id: Literal["WGS84"]
    ecef_frame_id: Literal["ECEF"]
    enu_frame_id: Literal["ENU"]
    ned_frame_id: Literal["NED"]
    origin: ResolvedCoordinate
    origin_ecef: ResolvedEcefPosition
    ecef_to_enu_rotation: ResolvedRotationMatrix
    enu_to_ecef_rotation: ResolvedRotationMatrix
    enu_to_ned_rotation: ResolvedRotationMatrix
    ned_to_enu_rotation: ResolvedRotationMatrix
    spatial_extent: contracts.SpatialBounds
    geoid_correction_asset_id: Identifier
    terrain_height_asset_id: Identifier
    geoid_interpolation: contracts.InterpolationKind
    terrain_interpolation: contracts.InterpolationKind
    geoid_precision_m: float = Field(gt=0)
    terrain_precision_m: float = Field(gt=0)

    @field_validator("geoid_precision_m", "terrain_precision_m")
    @classmethod
    def finite_precision(cls, value: float) -> float:
        return _require_finite("resolved vertical datum precision", value)


class ResolvedProvider(StrictModel):
    provider_id: Identifier
    runtime_stage: ProviderStage
    roles: tuple[contracts.ScenarioRoleKind, ...] = Field(min_length=1)
    capability_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def sorted_fields(self) -> "ResolvedProvider":
        parse_runtime_stage(self.runtime_stage)
        _require_sorted_unique("resolved provider role", self.roles)
        _require_sorted_unique("resolved provider capability_id", self.capability_ids)
        return self


class ResolvedAssetAudience(StrictModel):
    role: Literal["harness", "provider", "agent", "verifier"]
    workload_id: Identifier


class ResolvedAssetLicense(StrictModel):
    license_id: str
    selector: str
    selector_fragment: str | None
    file: FileRef
    byte_size: int = Field(gt=0)


class ResolvedWorldAssetMetadata(StrictModel):
    asset_role: contracts.AssetRole
    media_type: str
    units: tuple[contracts.UnitDeclaration, ...] = Field(min_length=1)
    precision: tuple[contracts.PrecisionMetadata, ...] = Field(min_length=1)
    source_frame: contracts.SourceFrameKind
    provenance: contracts.AssetProvenance
    selector: str
    selector_fragment: str | None
    license: ResolvedAssetLicense


class ResolvedAsset(StrictModel):
    """One byte-exact scenario asset with an exact workload authorization.

    World-only measurement, provenance, selector, and license metadata is kept in
    ``world``. Task assets deliberately leave ``world`` unset rather than
    inventing spatial metadata or a license declaration that their source
    contract does not provide.
    """

    source_kind: Literal["world", "task"]
    asset_id: Identifier
    file: FileRef
    byte_size: int = Field(gt=0)
    classification: Literal["public", "private"]
    audiences: tuple[ResolvedAssetAudience, ...]
    world: ResolvedWorldAssetMetadata | None

    @model_validator(mode="after")
    def source_and_audiences_are_canonical(self) -> "ResolvedAsset":
        audience_keys = tuple(
            f"{audience.role}:{audience.workload_id}" for audience in self.audiences
        )
        _require_sorted_unique("resolved asset audience", audience_keys)
        if self.source_kind == "world" and self.world is None:
            raise ValueError("resolved world assets require world metadata")
        if self.source_kind == "task" and self.world is not None:
            raise ValueError("resolved task assets cannot carry invented world metadata")
        return self

    @property
    def selector_path(self) -> str:
        return self.file.path

    @property
    def sha256(self) -> str:
        return self.file.sha256

    @property
    def license_path(self) -> str | None:
        return None if self.world is None else self.world.license.file.path

    @property
    def license_sha256(self) -> str | None:
        return None if self.world is None else self.world.license.file.sha256

    @property
    def license_byte_size(self) -> int | None:
        return None if self.world is None else self.world.license.byte_size


class ResolvedTaskToolBinding(StrictModel):
    binding_id: Identifier
    agent_id: Identifier
    tool_id: Identifier
    endpoint_id: Identifier
    request_schema: FileRef
    response_schema: FileRef
    timeout_ms: int = Field(gt=0)
    idempotent: bool


class ResolvedTaskQueryBinding(StrictModel):
    binding_id: Identifier
    agent_id: Identifier
    query_type: Identifier
    endpoint_id: Identifier
    request_schema: FileRef
    response_schema: FileRef
    timeout_ms: int = Field(gt=0)


class ResolvedInspectionObservationProjection(StrictModel):
    """Redacted task semantics required by an inspection observation provider."""

    projection_kind: Literal["inspection"]
    observation_id: Identifier
    work_order_id: Identifier
    target_id: Identifier
    simulation_asset_id: Identifier
    sensor_id: Identifier
    media_type: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+$",
    )
    min_distance_m: float
    max_distance_m: float
    min_view_angle_deg: float
    max_view_angle_deg: float
    earliest_time_ns: int = Field(ge=0)
    latest_time_ns: int = Field(gt=0)

    @field_validator(
        "min_distance_m",
        "max_distance_m",
        "min_view_angle_deg",
        "max_view_angle_deg",
    )
    @classmethod
    def finite_metric(cls, value: float) -> float:
        return _require_finite("inspection observation metric", value)

    @model_validator(mode="after")
    def ranges_are_closed(self) -> "ResolvedInspectionObservationProjection":
        if not 0.0 < self.min_distance_m <= self.max_distance_m:
            raise ValueError("inspection observation distance range is invalid")
        if not (
            0.0
            <= self.min_view_angle_deg
            <= self.max_view_angle_deg
            <= 180.0
        ):
            raise ValueError("inspection observation view-angle range is invalid")
        if self.earliest_time_ns >= self.latest_time_ns:
            raise ValueError("inspection observation time window is empty")
        return self


class ResolvedUrbanRecoveryObservationProjection(StrictModel):
    """Provider-safe observation ownership for the urban recovery package."""

    projection_kind: Literal["urban_recovery"]
    observation_id: Identifier
    observation_kind: Literal["camera", "telemetry", "safety", "mailbox"]
    vehicle_id: Identifier | None
    camera_id: Identifier | None

    @model_validator(mode="after")
    def binding_shape_is_explicit(self) -> "ResolvedUrbanRecoveryObservationProjection":
        if self.observation_kind == "camera":
            if self.vehicle_id is None or self.camera_id is None:
                raise ValueError("urban camera observations require vehicle and camera IDs")
        elif self.observation_kind in {"telemetry", "safety"}:
            if self.vehicle_id is None or self.camera_id is not None:
                raise ValueError(
                    "urban telemetry and safety observations require only a vehicle ID"
                )
        elif self.vehicle_id is not None or self.camera_id is not None:
            raise ValueError("urban mailbox observations cannot claim UAV sensor identity")
        return self


class ResolvedFlightObservationProjection(StrictModel):
    """Provider-safe ownership for Agent-visible flight sensor observations."""

    projection_kind: Literal["flight"]
    observation_id: Identifier
    observation_kind: Literal["telemetry", "gnss"]
    vehicle_id: Identifier

    @model_validator(mode="after")
    def stable_observation_id(self) -> "ResolvedFlightObservationProjection":
        expected = f"flight.{self.observation_kind}.{self.vehicle_id}"
        if self.observation_id != expected:
            raise ValueError(
                "flight observation_id must be flight.<kind>.<vehicle_id>"
            )
        return self


class ResolvedTaskScenarioProjection(StrictModel):
    """Task-package projection used only while compiling scenario authority."""

    logical_endpoint_ids: tuple[Identifier, ...]
    logical_capability_ids: tuple[Identifier, ...]
    observations: tuple[ResolvedInspectionObservationProjection, ...]
    urban_observations: tuple[ResolvedUrbanRecoveryObservationProjection, ...] = ()
    flight_observations: tuple[ResolvedFlightObservationProjection, ...] = ()

    @model_validator(mode="after")
    def projection_is_canonical(self) -> "ResolvedTaskScenarioProjection":
        _require_sorted_unique(
            "task projection logical endpoint", self.logical_endpoint_ids
        )
        _require_sorted_unique(
            "task projection logical capability", self.logical_capability_ids
        )
        _require_sorted_records(
            "task projection observation", self.observations, "observation_id"
        )
        _require_sorted_records(
            "task projection urban observation", self.urban_observations, "observation_id"
        )
        _require_sorted_records(
            "task projection flight observation", self.flight_observations, "observation_id"
        )
        groups = (
            {observation.observation_id for observation in self.observations},
            {
                observation.observation_id
                for observation in self.urban_observations
            },
            {
                observation.observation_id
                for observation in self.flight_observations
            },
        )
        if len(set().union(*groups)) != sum(len(group) for group in groups):
            raise ValueError("task projection observation IDs must be globally unique")
        if self.logical_capability_ids and not self.logical_endpoint_ids:
            raise ValueError(
                "task projection logical capabilities require a logical endpoint"
            )
        return self


class ResolvedTaskObservationBinding(StrictModel):
    binding_id: Identifier
    agent_id: Identifier
    observation_id: Identifier
    endpoint_id: Identifier
    schema_file: FileRef
    timeout_ms: int = Field(gt=0)
    inspection: ResolvedInspectionObservationProjection | None
    urban: ResolvedUrbanRecoveryObservationProjection | None = None
    flight: ResolvedFlightObservationProjection | None = None

    @model_validator(mode="after")
    def at_most_one_projection(self) -> "ResolvedTaskObservationBinding":
        if sum(
            projection is not None
            for projection in (self.inspection, self.urban, self.flight)
        ) > 1:
            raise ValueError("observation binding cannot declare multiple projections")
        return self


class ResolvedTaskBinding(StrictModel):
    task_id: Identifier
    package_id: Identifier
    package_config: FileRef
    package_schema: FileRef
    instruction: FileRef
    verifier_id: Identifier
    required_capability_ids: tuple[Identifier, ...]
    required_tool_ids: tuple[Identifier, ...]
    logical_endpoint_ids: tuple[Identifier, ...]
    asset_ids: tuple[Identifier, ...]
    tools: tuple[ResolvedTaskToolBinding, ...]
    queries: tuple[ResolvedTaskQueryBinding, ...]
    observations: tuple[ResolvedTaskObservationBinding, ...]
    goals: tuple[GoalSpec, ...] = Field(min_length=1)
    task_contract_digest: Sha256

    @model_validator(mode="after")
    def canonical_bindings(self) -> "ResolvedTaskBinding":
        _require_sorted_unique(
            "resolved task required capability", self.required_capability_ids
        )
        _require_sorted_unique("resolved task required tool", self.required_tool_ids)
        _require_sorted_unique(
            "resolved task logical endpoint", self.logical_endpoint_ids
        )
        _require_sorted_unique("resolved task asset", self.asset_ids)
        _require_sorted_records("resolved task tool", self.tools, "binding_id")
        _require_sorted_records("resolved task query", self.queries, "binding_id")
        _require_sorted_records(
            "resolved task observation", self.observations, "binding_id"
        )
        _require_sorted_records("resolved task goal", self.goals, "goal_id")
        if set(self.required_tool_ids) - {binding.tool_id for binding in self.tools}:
            raise ValueError("resolved task required tools are not completely bound")
        return self


class ResolvedBuilding(StrictModel):
    building_id: Identifier
    entity_id: Identifier
    source_asset_id: Identifier
    render_asset_id: Identifier
    collision_asset_id: Identifier
    anchor_east_m: float
    anchor_north_m: float
    base_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)
    top_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)

    @field_validator("anchor_east_m", "anchor_north_m")
    @classmethod
    def finite_anchor(cls, value: float) -> float:
        return _require_finite("building anchor", value)


class ResolvedRoad(StrictModel):
    road_id: Identifier
    kind: contracts.RoadKind
    width_m: float
    terrain_points: tuple[ResolvedCoordinate, ...] = Field(min_length=2)

    @field_validator("width_m")
    @classmethod
    def finite_width(cls, value: float) -> float:
        return _require_finite("road width", value)


class ResolvedRegion(StrictModel):
    region_id: Identifier
    kind: contracts.RegionKind
    communications_shadow_attenuation_db: float | None
    anchor_east_m: float
    anchor_north_m: float
    lower_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)
    upper_vertices: tuple[ResolvedCoordinate, ...] = Field(min_length=3)

    @field_validator(
        "communications_shadow_attenuation_db",
        "anchor_east_m",
        "anchor_north_m",
    )
    @classmethod
    def finite_optional(cls, value: float | None) -> float | None:
        if value is None:
            return value
        return _require_finite("region scalar", value)


class ResolvedLaunchSite(StrictModel):
    launch_site_id: Identifier
    primary_uav_entity_id: Identifier
    allowed_uav_entity_ids: tuple[Identifier, ...] = Field(min_length=1)
    pose: ResolvedPose
    pad_radius_m: float
    selected: bool

    @field_validator("pad_radius_m")
    @classmethod
    def finite_radius(cls, value: float) -> float:
        return _require_finite("launch site radius", value)

    @model_validator(mode="after")
    def sorted_uavs(self) -> "ResolvedLaunchSite":
        _require_sorted_unique(
            "resolved launch allowed_uav_entity_id",
            self.allowed_uav_entity_ids,
        )
        return self


class ResolvedEntity(StrictModel):
    entity_id: Identifier
    kind: contracts.EntityKind
    owner_kind: Literal["scenario", "provider"]
    owner_id: Identifier
    source_provider_id: Identifier | None
    authority_kind: contracts.AuthorityKind
    state: Literal["static", "dynamic"]
    model_asset_id: Identifier
    initial_pose: ResolvedPose
    selected_launch_override: bool

    @model_validator(mode="after")
    def ownership_is_explicit(self) -> "ResolvedEntity":
        if self.owner_kind == "scenario":
            if self.owner_id != "scenario.compiler" or self.source_provider_id is not None:
                raise ValueError("scenario-owned entities must name only scenario.compiler")
            if self.state != "static":
                raise ValueError("dynamic entities cannot be scenario-owned")
        elif self.source_provider_id != self.owner_id:
            raise ValueError("provider-owned entity source and resolved owner must match")
        return self

    @property
    def provider_id(self) -> str:
        """Resolved owner ID retained as a read-only adapter convenience."""

        return self.owner_id


class ResolvedSensor(StrictModel):
    sensor_id: Identifier
    provider_id: Identifier
    parent_entity_id: Identifier
    kind: contracts.SensorKind
    initial_pose: ResolvedPose
    horizontal_fov_deg: float
    vertical_fov_deg: float
    resolution_width_px: int
    resolution_height_px: int

    @field_validator("horizontal_fov_deg", "vertical_fov_deg")
    @classmethod
    def finite_fov(cls, value: float) -> float:
        return _require_finite("sensor fov", value)


class ResolvedSemanticTarget(StrictModel):
    target_id: Identifier
    parent_entity_id: Identifier
    required_sensor_id: Identifier
    pose: ResolvedPose
    geometry: contracts.TargetGeometry
    surface_normal_target: contracts.TargetSurfaceNormal
    min_distance_m: float
    max_distance_m: float
    max_view_angle_deg: float
    fov_margin_deg: float
    dwell_time_s: float
    required_evidence: tuple[contracts.EvidenceKind, ...] = Field(min_length=1)

    @field_validator(
        "min_distance_m",
        "max_distance_m",
        "max_view_angle_deg",
        "fov_margin_deg",
        "dwell_time_s",
    )
    @classmethod
    def finite_metrics(cls, value: float) -> float:
        return _require_finite("semantic target metric", value)

    @model_validator(mode="after")
    def sorted_evidence(self) -> "ResolvedSemanticTarget":
        _require_sorted_unique(
            "resolved semantic target evidence", self.required_evidence
        )
        return self


class ResolvedEngineFrameBinding(StrictModel):
    binding_id: Identifier
    provider_id: Identifier
    engine: contracts.EngineKind
    scene_asset_id: Identifier | None
    source_frame_id: str
    target_frame_id: str
    translation_m: ResolvedCartesianVector
    rotation_matrix: ResolvedRotationMatrix
    rotation_quaternion: ResolvedQuaternion


class ResolvedSumoConfiguration(StrictModel):
    provider_id: Identifier
    config_asset_id: Identifier
    network_asset_id: Identifier
    routes_asset_id: Identifier
    additional_asset_id: Identifier
    frame_binding_id: Identifier
    object_bindings: tuple[contracts.SumoEntityBinding, ...]

    @model_validator(mode="after")
    def sorted_object_bindings(self) -> "ResolvedSumoConfiguration":
        keys = tuple(binding.sumo_object_id for binding in self.object_bindings)
        _require_sorted_unique("resolved SUMO object binding", keys)
        return self


class ResolvedNetworkConfiguration(StrictModel):
    provider_id: Identifier
    radio_profiles: tuple[contracts.WirelessRadioProfile, ...] = Field(min_length=1)
    node_bindings: tuple[contracts.NetworkNodeBinding, ...] = Field(min_length=1)
    links: tuple[contracts.NetworkLinkBinding, ...]

    @model_validator(mode="after")
    def sorted_network_records(self) -> "ResolvedNetworkConfiguration":
        _require_sorted_unique(
            "resolved network radio profile",
            tuple(profile.radio_profile_id for profile in self.radio_profiles),
        )
        _require_sorted_unique(
            "resolved network node binding",
            tuple(binding.node_id for binding in self.node_bindings),
        )
        _require_sorted_unique(
            "resolved network link",
            tuple(link.link_id for link in self.links),
        )
        return self


class ResolvedMissionRequirement(StrictModel):
    requirement_id: Identifier
    kind: contracts.MissionRequirementKind
    dependencies: tuple[Identifier, ...]
    launch_site_id: Identifier | None
    target_id: Identifier | None
    expected_public_asset_id: Identifier | None

    @model_validator(mode="after")
    def sorted_dependencies(self) -> "ResolvedMissionRequirement":
        _require_sorted_unique("resolved mission dependency", self.dependencies)
        return self


class ResolvedScenario(StrictModel):
    schema_version: Literal["aero-bench.resolved-scenario/v4"]
    source_world_package: FileRef
    world_schema_version: Literal["aero-bench.world/v2"]
    world_id: Identifier
    world_digest: Sha256
    source_asset_digest: Sha256
    scenario_asset_digest: Sha256
    selected_launch_site_id: Identifier | None
    seed: int
    frame_authority: ResolvedFrameAuthority
    providers: tuple[ResolvedProvider, ...]
    assets: tuple[ResolvedAsset, ...] = Field(min_length=1)
    task: ResolvedTaskBinding
    base_layers: tuple[contracts.ViewerLayerSource, ...]
    layers: tuple[contracts.PublicLayer, ...]
    buildings: tuple[ResolvedBuilding, ...]
    roads: tuple[ResolvedRoad, ...]
    regions: tuple[ResolvedRegion, ...]
    launch_sites: tuple[ResolvedLaunchSite, ...]
    entities: tuple[ResolvedEntity, ...] = Field(min_length=1)
    sensors: tuple[ResolvedSensor, ...]
    semantic_targets: tuple[ResolvedSemanticTarget, ...]
    weather: tuple[contracts.WeatherSample, ...] = Field(min_length=1)
    engine_frame_bindings: tuple[ResolvedEngineFrameBinding, ...]
    sumo: ResolvedSumoConfiguration | None
    network: ResolvedNetworkConfiguration | None
    mission_requirements: tuple[ResolvedMissionRequirement, ...]
    expected_public_assets: tuple[contracts.ExpectedPublicAsset, ...]
    scenario_digest: Sha256

    @field_validator("seed", mode="before")
    @classmethod
    def strict_seed(cls, value: object) -> int:
        return _validate_seed_value(value)

    @model_validator(mode="after")
    def content_is_canonical(self) -> "ResolvedScenario":
        _validate_seed_value(self.seed)
        if self.selected_launch_site_id is None and (
            self.task.package_id != "logistics.arrivals.v1"
            or len(self.providers) != 1
            or self.providers[0].runtime_stage != BUSINESS_ENVIRONMENT
            or set(self.providers[0].capability_ids) != {
                "logistics.facilities.state", "logistics.orders.authority",
                "logistics.orders.scheduled-arrivals",
            }
            or self.launch_sites
            or any(entity.state != "static" or entity.selected_launch_override for entity in self.entities)
            or self.sensors or self.semantic_targets or self.mission_requirements
            or self.engine_frame_bindings or self.sumo is not None or self.network is not None
            or self.task.tools or self.task.queries or self.task.observations
        ):
            raise ValueError("no-launch scenario requires the static Business-only arrivals profile")
        _require_sorted_records("resolved provider", self.providers, "provider_id")
        _require_sorted_records("resolved asset", self.assets, "asset_id")
        _require_sorted_records("resolved base layer", self.base_layers, "layer_id")
        _require_sorted_records("resolved layer", self.layers, "layer_id")
        _require_sorted_records("resolved building", self.buildings, "building_id")
        _require_sorted_records("resolved road", self.roads, "road_id")
        _require_sorted_records("resolved region", self.regions, "region_id")
        _require_sorted_records(
            "resolved launch site", self.launch_sites, "launch_site_id"
        )
        _require_sorted_records("resolved entity", self.entities, "entity_id")
        _require_sorted_records("resolved sensor", self.sensors, "sensor_id")
        _require_sorted_records(
            "resolved semantic target", self.semantic_targets, "target_id"
        )
        _require_sorted_records("resolved weather", self.weather, "sample_id")
        _require_sorted_records(
            "resolved engine frame binding",
            self.engine_frame_bindings,
            "binding_id",
        )
        _require_sorted_records(
            "resolved mission requirement",
            self.mission_requirements,
            "requirement_id",
        )
        _require_sorted_records(
            "resolved expected public asset",
            self.expected_public_assets,
            "asset_id",
        )
        expected_asset_digest = hashlib.sha256(
            canonical_json_bytes(
                [asset.model_dump(mode="json") for asset in self.assets]
            )
        ).hexdigest()
        if self.scenario_asset_digest != expected_asset_digest:
            raise ValueError("scenario_asset_digest does not match the asset catalog")
        task_asset_ids = tuple(
            asset.asset_id for asset in self.assets if asset.source_kind == "task"
        )
        if self.task.asset_ids != task_asset_ids:
            raise ValueError("resolved task asset IDs differ from the asset catalog")
        provider_ids = {provider.provider_id for provider in self.providers}
        asset_map = {asset.asset_id: asset for asset in self.assets}
        entity_map = {entity.entity_id: entity for entity in self.entities}
        sensor_map = {sensor.sensor_id: sensor for sensor in self.sensors}
        target_map = {target.target_id: target for target in self.semantic_targets}
        launch_site_map = {
            launch_site.launch_site_id: launch_site for launch_site in self.launch_sites
        }
        engine_binding_map = {
            binding.binding_id: binding for binding in self.engine_frame_bindings
        }
        mission_map = {
            requirement.requirement_id: requirement
            for requirement in self.mission_requirements
        }
        expected_public_asset_map = {
            asset.asset_id: asset for asset in self.expected_public_assets
        }
        logical_endpoint_ids = set(self.task.logical_endpoint_ids)
        if logical_endpoint_ids & provider_ids:
            raise ValueError(
                "resolved task logical endpoints must be disjoint from providers"
            )
        valid_endpoint_ids = provider_ids | logical_endpoint_ids
        for binding in (*self.task.tools, *self.task.observations):
            if binding.endpoint_id not in valid_endpoint_ids:
                raise ValueError(
                    "resolved task binding names neither a provider nor a logical endpoint"
                )
        for entity in self.entities:
            model_asset = asset_map.get(entity.model_asset_id)
            if model_asset is None or model_asset.source_kind != "world":
                raise ValueError("resolved entity model is absent from world assets")
            if entity.owner_kind == "provider":
                if entity.owner_id not in provider_ids:
                    raise ValueError("resolved entity owner is not an enabled provider")
                if not any(
                    audience.role == "provider"
                    and audience.workload_id == entity.owner_id
                    for audience in model_asset.audiences
                ):
                    raise ValueError(
                        "resolved dynamic entity model does not authorize its provider"
                    )
        # runtime_stage is the single execution-stage authority. Cross-check it
        # against scenario ownership without ever deriving stage from roles or
        # capabilities. The motion Provider set must exactly equal the set of
        # provider-owned dynamic entity owners (SceneStateAssembler enforces the
        # same equality at assembly time).
        motion_provider_ids = {
            provider.provider_id
            for provider in self.providers
            if provider.runtime_stage == "motion"
        }
        dynamic_owner_ids = {
            entity.owner_id
            for entity in self.entities
            if entity.state == "dynamic" and entity.owner_kind == "provider"
        }
        if motion_provider_ids != dynamic_owner_ids:
            raise ValueError(
                "motion stage Provider IDs must exactly equal provider-owned "
                "dynamic entity owner IDs: "
                f"motion={sorted(motion_provider_ids)}, "
                f"dynamic_owners={sorted(dynamic_owner_ids)}"
            )
        if self.network is not None:
            network_provider = next(
                (
                    provider
                    for provider in self.providers
                    if provider.provider_id == self.network.provider_id
                ),
                None,
            )
            if network_provider is None or network_provider.runtime_stage != NETWORK:
                raise ValueError(
                    "scenario.network.provider_id must name a network-stage Provider"
                )
        for launch_site in self.launch_sites:
            allowed = set(launch_site.allowed_uav_entity_ids)
            primary = entity_map.get(launch_site.primary_uav_entity_id)
            if launch_site.primary_uav_entity_id not in allowed:
                raise ValueError(
                    "resolved launch primary UAV is absent from allowed UAVs"
                )
            for entity_id in allowed:
                entity = entity_map.get(entity_id)
                if (
                    entity is None
                    or entity.kind != "uav"
                    or entity.state != "dynamic"
                    or entity.owner_kind != "provider"
                ):
                    raise ValueError(
                        "resolved launch sites must reference enabled dynamic UAVs"
                    )
            if primary is None:
                raise ValueError("resolved launch primary UAV is absent")
        for sensor in self.sensors:
            if (
                sensor.provider_id not in provider_ids
                or sensor.parent_entity_id not in entity_map
            ):
                raise ValueError(
                    "resolved sensor names a disabled provider or absent parent entity"
                )
        for target in self.semantic_targets:
            parent = entity_map.get(target.parent_entity_id)
            sensor = sensor_map.get(target.required_sensor_id)
            if (
                parent is None
                or parent.owner_kind != "scenario"
                or parent.state != "static"
                or sensor is None
            ):
                raise ValueError(
                    "resolved semantic target names an invalid parent or sensor"
                )
        for engine_binding in self.engine_frame_bindings:
            if engine_binding.provider_id not in provider_ids:
                raise ValueError(
                    "resolved engine frame binding names a disabled provider"
                )
            if engine_binding.engine == "gazebo":
                scene_asset = asset_map.get(engine_binding.scene_asset_id or "")
                if (
                    scene_asset is None
                    or scene_asset.source_kind != "world"
                    or scene_asset.world is None
                    or scene_asset.world.asset_role != "other"
                    or not any(
                        audience.role == "provider"
                        and audience.workload_id == engine_binding.provider_id
                        for audience in scene_asset.audiences
                    )
                ):
                    raise ValueError(
                        "resolved Gazebo frame binding lacks its authorized scene asset"
                    )
            elif engine_binding.scene_asset_id is not None:
                raise ValueError(
                    "resolved non-Gazebo frame binding cannot name a scene asset"
                )
        if self.sumo is not None:
            frame_binding = engine_binding_map.get(self.sumo.frame_binding_id)
            sumo_asset_ids = {
                self.sumo.config_asset_id,
                self.sumo.network_asset_id,
                self.sumo.routes_asset_id,
                self.sumo.additional_asset_id,
            }
            if (
                self.sumo.provider_id not in provider_ids
                or frame_binding is None
                or frame_binding.provider_id != self.sumo.provider_id
                or frame_binding.engine != "sumo"
            ):
                raise ValueError("resolved SUMO authority binding is invalid")
            for asset_id in sumo_asset_ids:
                asset = asset_map.get(asset_id)
                if asset is None or not any(
                    audience.role == "provider"
                    and audience.workload_id == self.sumo.provider_id
                    for audience in asset.audiences
                ):
                    raise ValueError(
                        "resolved SUMO asset does not authorize its provider"
                    )
            owned_sumo_entity_ids = {
                entity.entity_id
                for entity in self.entities
                if entity.owner_kind == "provider"
                and entity.owner_id == self.sumo.provider_id
                and entity.authority_kind == "sumo_traffic"
            }
            if {
                binding.entity_id for binding in self.sumo.object_bindings
            } != owned_sumo_entity_ids:
                raise ValueError(
                    "resolved SUMO object bindings do not close over owned entities"
                )
        if self.network is not None:
            if self.network.provider_id not in provider_ids:
                raise ValueError(
                    "resolved network configuration names a disabled provider"
                )
            profile_map = {
                profile.radio_profile_id: profile
                for profile in self.network.radio_profiles
            }
            node_map = {
                binding.node_id: binding for binding in self.network.node_bindings
            }
            endpoint_ids = [
                binding.endpoint_id for binding in self.network.node_bindings
            ]
            bound_entity_ids = [
                binding.entity_id for binding in self.network.node_bindings
            ]
            if (
                len(endpoint_ids) != len(set(endpoint_ids))
                or len(bound_entity_ids) != len(set(bound_entity_ids))
            ):
                raise ValueError(
                    "resolved network node endpoint and entity bindings must be unique"
                )
            for profile in self.network.radio_profiles:
                if profile.provider_id != self.network.provider_id:
                    raise ValueError(
                        "resolved network radio profile names another provider"
                    )
            for node in self.network.node_bindings:
                if (
                    node.entity_id not in entity_map
                    or node.radio_profile_id not in profile_map
                ):
                    raise ValueError(
                        "resolved network node names an absent entity or radio profile"
                    )
            for link in self.network.links:
                if (
                    link.source_node_id not in node_map
                    or link.destination_node_id not in node_map
                ):
                    raise ValueError(
                        "resolved network link names an absent node binding"
                    )
        for requirement in self.mission_requirements:
            if any(
                dependency not in mission_map
                for dependency in requirement.dependencies
            ):
                raise ValueError(
                    "resolved mission dependency names an absent requirement"
                )
            if (
                requirement.kind in {"takeoff", "return_to_launch", "land"}
                and requirement.launch_site_id not in launch_site_map
            ):
                raise ValueError(
                    "resolved mission requirement names an absent launch site"
                )
            if (
                requirement.kind == "observation"
                and requirement.target_id not in target_map
            ):
                raise ValueError(
                    "resolved mission requirement names an absent semantic target"
                )
            if (
                requirement.kind == "upload_or_buffer"
                and requirement.expected_public_asset_id
                not in expected_public_asset_map
            ):
                raise ValueError(
                    "resolved mission requirement names an absent public asset"
                )
        visited_requirements: set[str] = set()
        visiting_requirements: set[str] = set()

        def visit_requirement(requirement_id: str) -> None:
            if requirement_id in visited_requirements:
                return
            if requirement_id in visiting_requirements:
                raise ValueError("resolved mission requirements contain a cycle")
            visiting_requirements.add(requirement_id)
            for dependency in mission_map[requirement_id].dependencies:
                visit_requirement(dependency)
            visiting_requirements.remove(requirement_id)
            visited_requirements.add(requirement_id)

        for requirement_id in mission_map:
            visit_requirement(requirement_id)
        for expected_asset in self.expected_public_assets:
            producer = mission_map.get(expected_asset.producer_requirement_id)
            if (
                producer is None
                or producer.kind != "upload_or_buffer"
                or producer.expected_public_asset_id != expected_asset.asset_id
            ):
                raise ValueError(
                    "resolved expected public asset has no matching producer requirement"
                )
        for binding in self.task.observations:
            projection = binding.inspection
            if projection is None:
                continue
            target = target_map.get(projection.target_id)
            sensor = sensor_map.get(projection.sensor_id)
            simulation_asset = asset_map.get(projection.simulation_asset_id)
            parent = None if target is None else entity_map.get(target.parent_entity_id)
            if (
                projection.observation_id != binding.observation_id
                or binding.endpoint_id not in provider_ids
                or target is None
                or sensor is None
                or sensor.provider_id != binding.endpoint_id
                or target.required_sensor_id != sensor.sensor_id
                or parent is None
                or parent.owner_kind != "scenario"
                or simulation_asset is None
                or simulation_asset.source_kind != "task"
                or not any(
                    audience.role == "provider"
                    and audience.workload_id == binding.endpoint_id
                    for audience in simulation_asset.audiences
                )
                or projection.min_distance_m != target.min_distance_m
                or projection.max_distance_m != target.max_distance_m
                or projection.max_view_angle_deg != target.max_view_angle_deg
            ):
                raise ValueError(
                    "resolved inspection observation differs from scenario authority"
                )
        for binding in self.task.observations:
            projection = binding.urban
            if projection is None:
                continue
            if projection.observation_id != binding.observation_id:
                raise ValueError("resolved urban observation identity differs from binding")
            if binding.endpoint_id not in provider_ids:
                raise ValueError("resolved urban observation endpoint is not a Provider")
            if projection.observation_kind == "camera":
                sensor = sensor_map.get(projection.camera_id or "")
                vehicle = entity_map.get(projection.vehicle_id or "")
                if (
                    sensor is None
                    or sensor.kind != "camera"
                    or sensor.provider_id != binding.endpoint_id
                    or sensor.parent_entity_id != projection.vehicle_id
                    or sensor.resolution_width_px != 640
                    or sensor.resolution_height_px != 480
                    or vehicle is None
                    or vehicle.kind != "uav"
                    or vehicle.state != "dynamic"
                ):
                    raise ValueError(
                        "resolved urban camera observation differs from scenario authority"
                    )
            elif projection.observation_kind in {"telemetry", "safety"}:
                vehicle = entity_map.get(projection.vehicle_id or "")
                if (
                    vehicle is None
                    or vehicle.kind != "uav"
                    or vehicle.state != "dynamic"
                    or vehicle.owner_id != binding.endpoint_id
                ):
                    raise ValueError(
                        "resolved urban UAV observation differs from scenario authority"
                    )
        for binding in self.task.observations:
            projection = binding.flight
            if projection is None:
                continue
            vehicle = entity_map.get(projection.vehicle_id)
            if (
                projection.observation_id != binding.observation_id
                or binding.endpoint_id not in provider_ids
                or vehicle is None
                or vehicle.kind != "uav"
                or vehicle.state != "dynamic"
                or vehicle.owner_kind != "provider"
                or vehicle.owner_id != binding.endpoint_id
            ):
                raise ValueError(
                    "resolved flight observation differs from scenario authority"
                )

        for entity in self.entities:
            if entity.owner_kind == "provider" and entity.owner_id not in provider_ids:
                raise ValueError("resolved entity owner is not an enabled provider")
        if self.sumo is not None and self.sumo.provider_id not in provider_ids:
            raise ValueError("resolved SUMO configuration names a disabled provider")
        if self.network is not None and self.network.provider_id not in provider_ids:
            raise ValueError("resolved network configuration names a disabled provider")
        selected_launch_ids = [
            launch_site.launch_site_id
            for launch_site in self.launch_sites
            if launch_site.selected
        ]
        expected_launch_ids = [] if self.selected_launch_site_id is None else [self.selected_launch_site_id]
        if selected_launch_ids != expected_launch_ids:
            raise ValueError(
                "resolved launch site selection must match selected_launch_site_id"
            )
        digest = scenario_digest_value(self)
        if self.scenario_digest != digest:
            raise ValueError("scenario_digest does not match resolved scenario content")
        _validate_coordinate_within_bounds(
            self.frame_authority.origin,
            bounds=self.frame_authority.spatial_extent,
            label="frame authority origin",
        )
        for launch_site in self.launch_sites:
            _validate_pose_within_bounds(
                launch_site.pose,
                bounds=self.frame_authority.spatial_extent,
                label=f"launch site {launch_site.launch_site_id} pose",
            )
        for entity in self.entities:
            _validate_pose_within_bounds(
                entity.initial_pose,
                bounds=self.frame_authority.spatial_extent,
                label=f"entity {entity.entity_id} initial_pose",
            )
        for sensor in self.sensors:
            _validate_pose_within_bounds(
                sensor.initial_pose,
                bounds=self.frame_authority.spatial_extent,
                label=f"sensor {sensor.sensor_id} initial_pose",
            )
        for target in self.semantic_targets:
            _validate_pose_within_bounds(
                target.pose,
                bounds=self.frame_authority.spatial_extent,
                label=f"semantic target {target.target_id} pose",
            )
        for road in self.roads:
            for index, point in enumerate(road.terrain_points):
                _validate_coordinate_within_bounds(
                    point,
                    bounds=self.frame_authority.spatial_extent,
                    label=f"road {road.road_id} terrain_points[{index}]",
                )
        for building in self.buildings:
            for label, vertices in (
                ("base_vertices", building.base_vertices),
                ("top_vertices", building.top_vertices),
            ):
                for index, point in enumerate(vertices):
                    _validate_coordinate_within_bounds(
                        point,
                        bounds=self.frame_authority.spatial_extent,
                        label=f"building {building.building_id} {label}[{index}]",
                    )
        for region in self.regions:
            for label, vertices in (
                ("lower_vertices", region.lower_vertices),
                ("upper_vertices", region.upper_vertices),
            ):
                for index, point in enumerate(vertices):
                    _validate_coordinate_within_bounds(
                        point,
                        bounds=self.frame_authority.spatial_extent,
                        label=f"region {region.region_id} {label}[{index}]",
                    )
        return self


def _scenario_document(
    scenario: ResolvedScenario,
) -> dict[str, object]:
    return scenario.model_dump(mode="json", exclude={"scenario_digest"})


def scenario_digest_value(scenario: ResolvedScenario) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_scenario_document(scenario))
    ).hexdigest()


def scenario_assets_for_workload(
    scenario: ResolvedScenario,
    *,
    role: Literal["harness", "provider", "agent", "verifier"],
    workload_id: str,
) -> tuple[ResolvedAsset, ...]:
    """Return the exact, compiler-expanded asset authorization for a workload."""

    if role not in {"harness", "provider", "agent", "verifier"}:
        raise ValueError("unsupported workload role for scenario asset projection")
    if not isinstance(workload_id, str) or not workload_id:
        raise ValueError("workload_id must be a non-empty string")
    return tuple(
        asset
        for asset in scenario.assets
        if any(
            audience.role == role and audience.workload_id == workload_id
            for audience in asset.audiences
        )
    )


def _load_world_package(
    reader: BundleReader,
    world_package: FileRef,
) -> contracts.WorldPackage:
    document = reader.load_document(world_package)
    if not isinstance(document, dict):
        raise ValueError("world package root must be a mapping")
    return contracts.WorldPackage.model_validate(document)


def _normalize_provider_capabilities(
    provider_capabilities: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    normalized: dict[str, tuple[str, ...]] = {}
    for provider_id, capabilities in provider_capabilities.items():
        if not isinstance(provider_id, str):
            raise ValueError("provider capability mapping keys must be strings")
        if isinstance(capabilities, (str, bytes)):
            raise ValueError(
                f"provider {provider_id} capabilities must be a sequence of strings"
            )
        capability_tuple = tuple(capabilities)
        if not capability_tuple:
            raise ValueError(
                f"provider {provider_id} must declare at least one capability"
            )
        if any(not isinstance(capability, str) for capability in capability_tuple):
            raise ValueError(
                f"provider {provider_id} capabilities must be a sequence of strings"
            )
        if len(capability_tuple) != len(set(capability_tuple)):
            raise ValueError(f"provider {provider_id} capabilities must be unique")
        normalized[provider_id] = tuple(sorted(capability_tuple))
    return dict(sorted(normalized.items()))


def _require_provider_mapping(
    world: contracts.WorldPackage,
    provider_capabilities: Mapping[str, Sequence[str]],
    provider_stages: Mapping[str, ProviderStage],
) -> tuple[ResolvedProvider, ...]:
    """Resolve only the providers explicitly enabled by the environment.

    WorldPackage provider requirements describe available physical ownership;
    they are not a mandate to launch every optional simulator. A provider whose
    only role is ``static_scene`` is rejected because static declarations are
    compiled into the scenario and have no runtime RPC authority.

    ``provider_stages`` is a pure stage projection (provider_id -> runtime_stage)
    already resolved by the explicit ProviderRegistry. This compiler never
    imports the registry; it only binds the projected stage into each frozen
    ``ResolvedProvider``.
    """

    normalized = _normalize_provider_capabilities(provider_capabilities)
    requirements = {
        requirement.provider_id: requirement
        for requirement in world.provider_requirements
    }
    providers: list[ResolvedProvider] = []
    for provider_id, declared in normalized.items():
        requirement = requirements.get(provider_id)
        if requirement is None:
            raise ValueError(
                f"enabled provider {provider_id} is not declared by WorldPackage"
            )
        runtime_roles = requirement.roles
        if not runtime_roles:
            raise ValueError(f"provider {provider_id} has no runtime scenario role")
        missing = tuple(
            capability
            for capability in requirement.required_capability_ids
            if capability not in declared
        )
        if missing:
            raise ValueError(
                f"provider {provider_id} capabilities must include "
                f"required_capability_ids; missing {list(missing)}"
            )
        if provider_id not in provider_stages:
            raise ValueError(
                f"enabled provider {provider_id} has no registered runtime stage"
            )
        runtime_stage = parse_runtime_stage(provider_stages[provider_id])
        providers.append(
            ResolvedProvider(
                provider_id=provider_id,
                runtime_stage=runtime_stage,
                roles=runtime_roles,
                capability_ids=declared,
            )
        )
    if set(provider_stages) != set(normalized):
        raise ValueError("runtime stage projection must exactly cover enabled Providers")
    return tuple(providers)


def _precision_tolerance_m(
    asset: contracts.AssetRecord,
    *,
    label: str,
) -> float:
    tolerances = tuple(
        item.absolute_tolerance
        for item in asset.precision
        if item.kind == "absolute_tolerance" and item.unit == "m"
    )
    if not tolerances:
        raise ValueError(f"{label} must declare at least one metre absolute_tolerance")
    return min(tolerance for tolerance in tolerances if tolerance is not None)


def _load_scalar_grid(
    *,
    reader: BundleReader,
    asset: contracts.AssetRecord,
    bounds: contracts.SpatialBounds,
    expected_precision_m: float,
    label: str,
) -> ScalarGrid:
    selector_path, selector_fragment = _selector_parts(asset.artifact.selector)
    if asset.media_type != "application/json":
        raise ValueError(f"{label} asset must use media_type application/json")
    if selector_fragment is not None:
        raise ValueError(f"{label} asset selector must not include a fragment")
    tolerance_m = _precision_tolerance_m(asset, label=label)
    if tolerance_m > expected_precision_m:
        raise ValueError(
            f"{label} precision must be <= declared vertical datum precision "
            f"{expected_precision_m}"
        )
    grid = ScalarGrid.model_validate(
        reader.load_document(FileRef(path=selector_path, sha256=asset.artifact.sha256))
    )
    if (
        grid.east_axis_m[0] > bounds.min_east_m
        or grid.east_axis_m[-1] < bounds.max_east_m
    ):
        raise ValueError(
            f"{label} east-axis coverage must include WorldFrame.spatial_extent"
        )
    if (
        grid.north_axis_m[0] > bounds.min_north_m
        or grid.north_axis_m[-1] < bounds.max_north_m
    ):
        raise ValueError(
            f"{label} north-axis coverage must include WorldFrame.spatial_extent"
        )
    return grid


def _math_quaternion_from_world_pose(
    pose: contracts.WorldPose | contracts.ParentRelativePose | contracts.RigidOffset,
) -> frame_math.UnitQuaternion:
    return frame_math.UnitQuaternion(
        w=pose.qw,
        x=pose.qx,
        y=pose.qy,
        z=pose.qz,
    )


def _math_transform_from_world_pose(
    pose: contracts.WorldPose,
) -> frame_math.RigidTransform:
    return frame_math.RigidTransform(
        rotation=_math_quaternion_from_world_pose(pose).to_rotation_matrix(),
        translation=frame_math.Vector3(
            x=pose.east_m,
            y=pose.north_m,
            z=pose.up_m,
        ),
    )


def _math_transform_from_parent_pose(
    pose: contracts.ParentRelativePose | contracts.RigidOffset,
) -> frame_math.RigidTransform:
    return frame_math.RigidTransform(
        rotation=_math_quaternion_from_world_pose(pose).to_rotation_matrix(),
        translation=frame_math.Vector3(x=pose.x_m, y=pose.y_m, z=pose.z_m),
    )


def _resolved_quaternion(
    quaternion: frame_math.UnitQuaternion,
) -> ResolvedQuaternion:
    return ResolvedQuaternion(
        qw=quaternion.w,
        qx=quaternion.x,
        qy=quaternion.y,
        qz=quaternion.z,
    )


def _resolved_rotation(
    rotation: frame_math.RotationMatrix,
) -> ResolvedRotationMatrix:
    return ResolvedRotationMatrix(rows=rotation.rows)


def _resolved_coordinate(
    *,
    enu_transform: frame_math.EnuTransform,
    geoid_grid: ScalarGrid,
    terrain_grid: ScalarGrid,
    geoid_interpolation: contracts.InterpolationKind,
    terrain_interpolation: contracts.InterpolationKind,
    east_m: float,
    north_m: float,
    up_m: float,
) -> ResolvedCoordinate:
    enu_vector = frame_math.Vector3(x=east_m, y=north_m, z=up_m)
    ecef_vector = enu_transform.ecef_from_enu(enu_vector)
    longitude_deg, latitude_deg, ellipsoid_height_m = enu_transform.enu_to_geodetic(
        enu_vector
    )
    geoid_m = geoid_grid.sample(east_m, north_m, geoid_interpolation)
    terrain_amsl_m = terrain_grid.sample(east_m, north_m, terrain_interpolation)
    amsl_m = ellipsoid_height_m - geoid_m
    ned_vector = frame_math.enu_to_ned(enu_vector)
    return ResolvedCoordinate(
        enu=ResolvedEnuPosition(east_m=east_m, north_m=north_m, up_m=up_m),
        ned=ResolvedNedPosition(
            north_m=ned_vector.x,
            east_m=ned_vector.y,
            down_m=ned_vector.z,
        ),
        ecef=ResolvedEcefPosition(
            x_m=ecef_vector.x,
            y_m=ecef_vector.y,
            z_m=ecef_vector.z,
        ),
        wgs84=ResolvedWgs84Position(
            longitude_deg=longitude_deg,
            latitude_deg=latitude_deg,
            ellipsoid_height_m=ellipsoid_height_m,
        ),
        geoid_separation_m=geoid_m,
        amsl_m=amsl_m,
        terrain_amsl_m=terrain_amsl_m,
        agl_m=amsl_m - terrain_amsl_m,
    )


def _validate_coordinate_within_bounds(
    coordinate: ResolvedCoordinate,
    *,
    bounds: contracts.SpatialBounds,
    label: str,
) -> None:
    if not bounds.min_east_m <= coordinate.enu.east_m <= bounds.max_east_m:
        raise ValueError(f"{label} east_m lies outside WorldFrame.spatial_extent")
    if not bounds.min_north_m <= coordinate.enu.north_m <= bounds.max_north_m:
        raise ValueError(f"{label} north_m lies outside WorldFrame.spatial_extent")
    if not bounds.min_up_m <= coordinate.enu.up_m <= bounds.max_up_m:
        raise ValueError(f"{label} up_m lies outside WorldFrame.spatial_extent")


def _validate_pose_within_bounds(
    pose: ResolvedPose,
    *,
    bounds: contracts.SpatialBounds,
    label: str,
) -> None:
    _validate_coordinate_within_bounds(pose.position, bounds=bounds, label=label)


def _orientation_ned_from_rotation(
    rotation_enu: frame_math.RotationMatrix,
) -> frame_math.UnitQuaternion:
    return frame_math.UnitQuaternion.from_rotation_matrix(
        frame_math.ENU_TO_NED_ROTATION.compose(rotation_enu)
    )


def _resolved_pose_from_transform(
    *,
    transform: frame_math.RigidTransform,
    enu_transform: frame_math.EnuTransform,
    geoid_grid: ScalarGrid,
    terrain_grid: ScalarGrid,
    geoid_interpolation: contracts.InterpolationKind,
    terrain_interpolation: contracts.InterpolationKind,
) -> ResolvedPose:
    position = _resolved_coordinate(
        enu_transform=enu_transform,
        geoid_grid=geoid_grid,
        terrain_grid=terrain_grid,
        geoid_interpolation=geoid_interpolation,
        terrain_interpolation=terrain_interpolation,
        east_m=transform.translation.x,
        north_m=transform.translation.y,
        up_m=transform.translation.z,
    )
    orientation_enu = frame_math.UnitQuaternion.from_rotation_matrix(transform.rotation)
    orientation_ned = _orientation_ned_from_rotation(transform.rotation)
    return ResolvedPose(
        position=position,
        orientation_enu=_resolved_quaternion(orientation_enu),
        orientation_ned=_resolved_quaternion(orientation_ned),
    )


def _solve_enu_up(
    *,
    enu_transform: frame_math.EnuTransform,
    geoid_grid: ScalarGrid,
    terrain_grid: ScalarGrid,
    geoid_interpolation: contracts.InterpolationKind,
    terrain_interpolation: contracts.InterpolationKind,
    bounds: contracts.SpatialBounds,
    east_m: float,
    north_m: float,
    height_reference: contracts.HeightReferenceKind | Literal["enu_up"],
    height_m: float,
    label: str,
) -> float:
    if height_reference == "enu_up":
        if not bounds.min_up_m <= height_m <= bounds.max_up_m:
            raise ValueError(
                f"{label} ENU height lies outside WorldFrame.spatial_extent"
            )
        return height_m
    geoid_m = geoid_grid.sample(east_m, north_m, geoid_interpolation)
    terrain_amsl_m = terrain_grid.sample(east_m, north_m, terrain_interpolation)
    if height_reference == "wgs84_ellipsoid":
        target_ellipsoid_m = height_m
    elif height_reference == "amsl":
        target_ellipsoid_m = height_m + geoid_m
    else:
        target_ellipsoid_m = height_m + geoid_m + terrain_amsl_m

    def ellipsoid_height_for_up(up_m: float) -> float:
        return enu_transform.enu_to_geodetic(
            frame_math.Vector3(x=east_m, y=north_m, z=up_m)
        )[2]

    low = bounds.min_up_m
    high = bounds.max_up_m
    low_value = ellipsoid_height_for_up(low) - target_ellipsoid_m
    high_value = ellipsoid_height_for_up(high) - target_ellipsoid_m
    if low_value == 0.0:
        return low
    if high_value == 0.0:
        return high
    if math.copysign(1.0, low_value) == math.copysign(1.0, high_value):
        raise ValueError(f"{label} cannot be solved within WorldFrame.spatial_extent")
    for _ in range(_SOLVER_MAX_ITERATIONS):
        midpoint = (low + high) * 0.5
        value = ellipsoid_height_for_up(midpoint) - target_ellipsoid_m
        if abs(value) <= _SOLVER_TOLERANCE_M:
            return midpoint
        if math.copysign(1.0, value) == math.copysign(1.0, low_value):
            low = midpoint
            low_value = value
        else:
            high = midpoint
            high_value = value
    midpoint = (low + high) * 0.5
    if (
        abs(ellipsoid_height_for_up(midpoint) - target_ellipsoid_m)
        > _SOLVER_TOLERANCE_M
    ):
        raise ValueError(f"{label} ENU height solve did not converge")
    return midpoint


def _fixed_anchor(points: Sequence[contracts.EnuPoint]) -> tuple[float, float]:
    anchor = min((point.east_m, point.north_m) for point in points)
    return anchor


def _resolved_vertices(
    *,
    points: Sequence[contracts.EnuPoint],
    bounds: contracts.SpatialBounds,
    height_reference: contracts.HeightReferenceKind | Literal["enu_up"],
    height_m: float,
    label: str,
    enu_transform: frame_math.EnuTransform,
    geoid_grid: ScalarGrid,
    terrain_grid: ScalarGrid,
    geoid_interpolation: contracts.InterpolationKind,
    terrain_interpolation: contracts.InterpolationKind,
) -> tuple[ResolvedCoordinate, ...]:
    return tuple(
        _resolved_coordinate(
            enu_transform=enu_transform,
            geoid_grid=geoid_grid,
            terrain_grid=terrain_grid,
            geoid_interpolation=geoid_interpolation,
            terrain_interpolation=terrain_interpolation,
            east_m=point.east_m,
            north_m=point.north_m,
            up_m=_solve_enu_up(
                enu_transform=enu_transform,
                geoid_grid=geoid_grid,
                terrain_grid=terrain_grid,
                geoid_interpolation=geoid_interpolation,
                terrain_interpolation=terrain_interpolation,
                bounds=bounds,
                east_m=point.east_m,
                north_m=point.north_m,
                height_reference=height_reference,
                height_m=height_m,
                label=f"{label}[{point.east_m},{point.north_m}]",
            ),
        )
        for point in points
    )


def _all_runtime_audiences(
    *,
    provider_ids: frozenset[str],
    agents: tuple[AgentSpec, ...],
    verifier_id: str,
) -> tuple[ResolvedAssetAudience, ...]:
    return tuple(
        sorted(
            (
                ResolvedAssetAudience(role="harness", workload_id="harness"),
                *(
                    ResolvedAssetAudience(role="provider", workload_id=provider_id)
                    for provider_id in provider_ids
                ),
                *(
                    ResolvedAssetAudience(role="agent", workload_id=agent.agent_id)
                    for agent in agents
                ),
                ResolvedAssetAudience(role="verifier", workload_id=verifier_id),
            ),
            key=lambda item: (item.role, item.workload_id),
        )
    )


def _resolved_world_audiences(
    *,
    asset: contracts.AssetRecord,
    world: contracts.WorldPackage,
    provider_ids: frozenset[str],
    agents: tuple[AgentSpec, ...],
    verifier_id: str,
) -> tuple[ResolvedAssetAudience, ...]:
    entity_provider = {entity.entity_id: entity.provider_id for entity in world.entities}
    declared_provider_ids = {
        requirement.provider_id for requirement in world.provider_requirements
    }
    resolved: set[tuple[str, str]] = set()
    for audience in asset.audiences:
        if audience.audience_kind == "public":
            resolved.update(
                (item.role, item.workload_id)
                for item in _all_runtime_audiences(
                    provider_ids=provider_ids,
                    agents=agents,
                    verifier_id=verifier_id,
                )
            )
        elif audience.audience_kind == "provider":
            if audience.audience_id not in declared_provider_ids:
                raise ValueError(
                    f"world asset {asset.artifact.artifact_id} names an undeclared provider"
                )
            if audience.audience_id in provider_ids:
                resolved.add(("provider", audience.audience_id))
        elif audience.audience_kind == "entity":
            if audience.audience_id not in entity_provider:
                raise ValueError(
                    f"world asset {asset.artifact.artifact_id} names an undeclared entity"
                )
            owner = entity_provider[audience.audience_id]
            if owner is None:
                raise ValueError(
                    f"world asset {asset.artifact.artifact_id} cannot authorize a compiler-owned entity"
                )
            if owner in provider_ids:
                resolved.add(("provider", owner))
        elif audience.audience_kind == "verifier":
            if audience.audience_id != verifier_id:
                raise ValueError(
                    f"world asset {asset.artifact.artifact_id} names another verifier"
                )
            resolved.add(("verifier", verifier_id))
        else:
            raise ValueError(
                f"world asset {asset.artifact.artifact_id} names unsupported viewer runtime audience"
            )
    return tuple(
        ResolvedAssetAudience(role=role, workload_id=workload_id)
        for role, workload_id in sorted(resolved)
    )


def _resolved_assets(
    *,
    reader: BundleReader,
    world: contracts.WorldPackage,
    task: TaskSpec,
    agents: tuple[AgentSpec, ...],
    provider_ids: frozenset[str],
) -> tuple[ResolvedAsset, ...]:
    used_asset_ids = {
        world.frame.vertical_datum.geoid_correction_asset_id,
        world.frame.vertical_datum.terrain_height_asset_id,
        # Raw OSM provenance is an authoritative world input even though it is
        # intentionally not exposed through a viewer layer.
        *(
            asset.artifact.artifact_id
            for asset in world.assets
            if asset.asset_role == "osm_source"
            or (
                asset.asset_role == "layer_tiles"
                and asset.artifact.selector.startswith("world/osm2world/assets/")
            )
        ),
        *(layer.asset_id for layer in world.base_layers),
        *(layer.asset_id for layer in world.layers),
        *(entity.model_asset_id for entity in world.entities),
        *(
            binding.scene_asset_id
            for binding in world.engine_frame_bindings
            if binding.scene_asset_id is not None
        ),
    }
    if world.sumo is not None:
        used_asset_ids.update(
            {
                world.sumo.config_asset_id,
                world.sumo.network_asset_id,
                world.sumo.routes_asset_id,
                world.sumo.additional_asset_id,
            }
        )
    for building in world.buildings:
        used_asset_ids.update(
            {
                building.source_asset_id,
                building.render_asset_id,
                building.collision_asset_id,
            }
        )
    world_asset_ids = {asset.artifact.artifact_id for asset in world.assets}
    unused_asset_ids = tuple(sorted(world_asset_ids - used_asset_ids))
    if unused_asset_ids:
        raise ValueError(f"unused source asset records: {list(unused_asset_ids)}")
    task_asset_ids = {asset.asset_id for asset in task.assets}
    collisions = tuple(sorted(world_asset_ids & task_asset_ids))
    if collisions:
        raise ValueError(
            f"world and task asset IDs must be disjoint: {list(collisions)}"
        )

    resolved_assets: list[ResolvedAsset] = []
    for asset in sorted(world.assets, key=lambda item: item.artifact.artifact_id):
        selector_path, selector_fragment, byte_size = _verify_selector_file(
            reader=reader,
            selector=asset.artifact,
            label=f"asset {asset.artifact.artifact_id}",
            expected_size=asset.byte_size,
        )
        license_path, license_fragment, license_byte_size = _verify_selector_file(
            reader=reader,
            selector=asset.license_file,
            label=f"asset {asset.artifact.artifact_id} license_file",
            expected_size=None,
        )
        resolved_assets.append(
            ResolvedAsset(
                source_kind="world",
                asset_id=asset.artifact.artifact_id,
                file=FileRef(path=selector_path, sha256=asset.artifact.sha256),
                byte_size=byte_size,
                classification=asset.visibility,
                audiences=_resolved_world_audiences(
                    asset=asset,
                    world=world,
                    provider_ids=provider_ids,
                    agents=agents,
                    verifier_id=task.verifier.verifier_id,
                ),
                world=ResolvedWorldAssetMetadata(
                    asset_role=asset.asset_role,
                    media_type=asset.media_type,
                    units=asset.units,
                    precision=asset.precision,
                    source_frame=asset.source_frame,
                    provenance=asset.provenance,
                    selector=asset.artifact.selector,
                    selector_fragment=selector_fragment,
                    license=ResolvedAssetLicense(
                        license_id=asset.license_id,
                        selector=asset.license_file.selector,
                        selector_fragment=license_fragment,
                        file=FileRef(
                            path=license_path,
                            sha256=asset.license_file.sha256,
                        ),
                        byte_size=license_byte_size,
                    ),
                ),
            )
        )

    declared_workloads = {
        "provider": provider_ids,
        "agent": frozenset(agent.agent_id for agent in agents),
        "verifier": frozenset({task.verifier.verifier_id}),
    }
    for asset in sorted(task.assets, key=lambda item: item.asset_id):
        source = reader.resolve_file(asset.file)
        byte_size = source.stat().st_size
        if byte_size <= 0:
            raise ValueError(f"task asset {asset.asset_id} must be non-empty")
        audiences: list[ResolvedAssetAudience] = []
        for audience in asset.audiences:
            unknown = set(audience.workload_ids) - declared_workloads[audience.role]
            if unknown:
                raise ValueError(
                    f"task asset {asset.asset_id} names undeclared {audience.role} "
                    f"workloads: {sorted(unknown)}"
                )
            audiences.extend(
                ResolvedAssetAudience(role=audience.role, workload_id=workload_id)
                for workload_id in audience.workload_ids
            )
        resolved_assets.append(
            ResolvedAsset(
                source_kind="task",
                asset_id=asset.asset_id,
                file=asset.file,
                byte_size=byte_size,
                classification=asset.classification,
                audiences=tuple(
                    sorted(audiences, key=lambda item: (item.role, item.workload_id))
                ),
                world=None,
            )
        )
    payload_paths = [asset.file.path for asset in resolved_assets]
    if len(payload_paths) != len(set(payload_paths)):
        raise ValueError("resolved world/task asset payload files must be unique")
    license_paths = {
        asset.world.license.file.path
        for asset in resolved_assets
        if asset.world is not None
    }
    if set(payload_paths) & license_paths:
        raise ValueError(
            "resolved asset payload files must be disjoint from license files"
        )
    return tuple(sorted(resolved_assets, key=lambda item: item.asset_id))


def _resolved_task_binding(
    *,
    task: TaskSpec,
    agents: tuple[AgentSpec, ...],
    providers: tuple[ResolvedProvider, ...],
    task_projection: ResolvedTaskScenarioProjection,
) -> ResolvedTaskBinding:
    provider_ids = frozenset(provider.provider_id for provider in providers)
    logical_endpoint_ids = frozenset(task_projection.logical_endpoint_ids)
    if provider_ids & logical_endpoint_ids:
        raise ValueError("task logical endpoints must be disjoint from providers")
    physical_capability_ids = {
        capability_id
        for provider in providers
        for capability_id in provider.capability_ids
    }
    missing_physical_capabilities = (
        set(task.required_capabilities) - physical_capability_ids
    )
    if set(task_projection.logical_capability_ids) != missing_physical_capabilities:
        raise ValueError(
            "task projection logical capabilities must equal capabilities absent "
            "from physical providers"
        )
    inspection_observations = task_projection.observations
    urban_observations = task_projection.urban_observations
    flight_observations = task_projection.flight_observations
    projection_by_observation = {
        projection.observation_id: projection
        for projection in (
            *inspection_observations,
            *urban_observations,
            *flight_observations,
        )
    }
    inspection_projection_by_observation = {
        projection.observation_id: projection
        for projection in inspection_observations
    }
    urban_projection_by_observation = {
        projection.observation_id: projection
        for projection in urban_observations
    }
    flight_projection_by_observation = {
        projection.observation_id: projection
        for projection in flight_observations
    }
    if len(projection_by_observation) != (
        len(inspection_observations)
        + len(urban_observations)
        + len(flight_observations)
    ):
        raise ValueError("task observation projections must be globally unique")
    granted_observation_ids = {
        grant.observation_id for agent in agents for grant in agent.observations
    }
    if set(projection_by_observation) - granted_observation_ids:
        raise ValueError("task observation projection names an ungranted observation")
    tools = tuple(
        sorted(
            (
                ResolvedTaskToolBinding(
                    binding_id=f"{agent.agent_id}.{grant.tool_id}",
                    agent_id=agent.agent_id,
                    tool_id=grant.tool_id,
                    endpoint_id=grant.provider_id,
                    request_schema=grant.request_schema,
                    response_schema=grant.response_schema,
                    timeout_ms=grant.timeout_ms,
                    idempotent=grant.idempotent,
                )
                for agent in agents
                for grant in agent.tools
            ),
            key=lambda item: item.binding_id,
        )
    )
    queries = tuple(
        sorted(
            (
                ResolvedTaskQueryBinding(
                    binding_id=f"{agent.agent_id}.{grant.query_type}",
                    agent_id=agent.agent_id,
                    query_type=grant.query_type,
                    endpoint_id=grant.provider_id,
                    request_schema=grant.request_schema,
                    response_schema=grant.response_schema,
                    timeout_ms=grant.timeout_ms,
                )
                for agent in agents
                for grant in agent.queries
            ),
            key=lambda item: item.binding_id,
        )
    )
    observations = tuple(
        sorted(
            (
                ResolvedTaskObservationBinding(
                    binding_id=f"{agent.agent_id}.{grant.observation_id}",
                    agent_id=agent.agent_id,
                    observation_id=grant.observation_id,
                    endpoint_id=grant.provider_id,
                    schema_file=grant.schema_file,
                    timeout_ms=grant.timeout_ms,
                    inspection=inspection_projection_by_observation.get(
                        grant.observation_id
                    ),
                    urban=urban_projection_by_observation.get(grant.observation_id),
                    flight=flight_projection_by_observation.get(grant.observation_id),
                )
                for agent in agents
                for grant in agent.observations
            ),
            key=lambda item: item.binding_id,
        )
    )
    bound_endpoint_ids = {
        binding.endpoint_id for binding in (*tools, *queries, *observations)
    }
    unknown_endpoint_ids = bound_endpoint_ids - provider_ids - logical_endpoint_ids
    if unknown_endpoint_ids:
        raise ValueError(
            "task grants name undeclared physical or logical endpoints: "
            f"{sorted(unknown_endpoint_ids)}"
        )
    if bound_endpoint_ids & logical_endpoint_ids != logical_endpoint_ids:
        raise ValueError("task projection declares an unused logical endpoint")
    resolved_logical_endpoint_ids = tuple(sorted(logical_endpoint_ids))
    task_document = {
        "task": task.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
    }
    return ResolvedTaskBinding(
        task_id=task.task_id,
        package_id=task.package.package_id,
        package_config=task.package.config.file,
        package_schema=task.package.config.schema_file,
        instruction=task.instruction,
        verifier_id=task.verifier.verifier_id,
        required_capability_ids=tuple(sorted(task.required_capabilities)),
        required_tool_ids=tuple(sorted(task.required_tools)),
        logical_endpoint_ids=resolved_logical_endpoint_ids,
        asset_ids=tuple(sorted(asset.asset_id for asset in task.assets)),
        tools=tools,
        queries=queries,
        observations=observations,
        goals=tuple(sorted(task.goals, key=lambda item: item.goal_id)),
        task_contract_digest=hashlib.sha256(
            canonical_json_bytes(task_document)
        ).hexdigest(),
    )


def compile_resolved_scenario(
    reader: BundleReader,
    world_package: FileRef,
    launch_site_id: str | None,
    seed: int,
    provider_capabilities: Mapping[str, Sequence[str]],
    provider_stages: Mapping[str, ProviderStage],
    task: TaskSpec,
    agents: tuple[AgentSpec, ...],
    task_projection: ResolvedTaskScenarioProjection,
) -> ResolvedScenario:
    seed = _validate_seed_value(seed)
    world = _load_world_package(reader, world_package)
    providers = _require_provider_mapping(world, provider_capabilities, provider_stages)
    provider_ids = frozenset(provider.provider_id for provider in providers)
    if launch_site_id is None and (
        task.package.package_id != "logistics.arrivals.v1"
        or len(providers) != 1
        or providers[0].runtime_stage != BUSINESS_ENVIRONMENT
        or world.launch_sites or any(entity.state != "static" for entity in world.entities)
        or world.sensors or world.semantic_targets or world.mission_requirements
        or world.engine_frame_bindings or world.sumo is not None or world.network is not None
    ):
        raise ValueError("no-launch compilation requires the static Business-only arrivals profile")
    assets = _resolved_assets(
        reader=reader,
        world=world,
        task=task,
        agents=agents,
        provider_ids=provider_ids,
    )
    task_binding = _resolved_task_binding(
        task=task,
        agents=agents,
        providers=providers,
        task_projection=task_projection,
    )
    scenario_asset_digest = hashlib.sha256(
        canonical_json_bytes(
            [asset.model_dump(mode="json") for asset in assets]
        )
    ).hexdigest()
    asset_map = {asset.artifact.artifact_id: asset for asset in world.assets}

    geoid_asset = asset_map[world.frame.vertical_datum.geoid_correction_asset_id]
    terrain_asset = asset_map[world.frame.vertical_datum.terrain_height_asset_id]
    geoid_grid = _load_scalar_grid(
        reader=reader,
        asset=geoid_asset,
        bounds=world.frame.spatial_extent,
        expected_precision_m=world.frame.vertical_datum.geoid_precision_m,
        label="geoid grid",
    )
    terrain_grid = _load_scalar_grid(
        reader=reader,
        asset=terrain_asset,
        bounds=world.frame.spatial_extent,
        expected_precision_m=world.frame.vertical_datum.terrain_precision_m,
        label="terrain grid",
    )

    launch_site_map = {launch.launch_site_id: launch for launch in world.launch_sites}
    selected_launch = launch_site_map.get(launch_site_id)
    if launch_site_id is not None and selected_launch is None:
        raise ValueError("launch_site_id must reference a declared launch site")
    source_entity_map = {entity.entity_id: entity for entity in world.entities}
    if selected_launch is not None and source_entity_map[selected_launch.primary_uav_entity_id].provider_id not in provider_ids:
        raise ValueError(
            "selected launch site primary UAV provider is not enabled by the scenario"
        )

    enu_transform = frame_math.EnuTransform.from_origin(
        longitude_deg=world.frame.origin.longitude_deg,
        latitude_deg=world.frame.origin.latitude_deg,
        altitude_m=world.frame.origin.altitude_m,
    )
    origin = _resolved_coordinate(
        enu_transform=enu_transform,
        geoid_grid=geoid_grid,
        terrain_grid=terrain_grid,
        geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
        terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
        east_m=0.0,
        north_m=0.0,
        up_m=0.0,
    )
    frame_authority = ResolvedFrameAuthority(
        geodetic_frame_id=world.frame.geodetic_frame_id,
        ecef_frame_id=world.frame.ecef_frame_id,
        enu_frame_id=world.frame.enu_frame_id,
        ned_frame_id=world.frame.ned_frame_id,
        origin=origin,
        origin_ecef=ResolvedEcefPosition(
            x_m=enu_transform.origin_ecef.x,
            y_m=enu_transform.origin_ecef.y,
            z_m=enu_transform.origin_ecef.z,
        ),
        ecef_to_enu_rotation=_resolved_rotation(enu_transform.ecef_to_enu),
        enu_to_ecef_rotation=_resolved_rotation(enu_transform.enu_to_ecef),
        enu_to_ned_rotation=_resolved_rotation(frame_math.ENU_TO_NED_ROTATION),
        ned_to_enu_rotation=_resolved_rotation(frame_math.NED_TO_ENU_ROTATION),
        spatial_extent=world.frame.spatial_extent,
        geoid_correction_asset_id=world.frame.vertical_datum.geoid_correction_asset_id,
        terrain_height_asset_id=world.frame.vertical_datum.terrain_height_asset_id,
        geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
        terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
        geoid_precision_m=world.frame.vertical_datum.geoid_precision_m,
        terrain_precision_m=world.frame.vertical_datum.terrain_precision_m,
    )

    entity_transforms: dict[str, frame_math.RigidTransform] = {}
    for entity in world.entities:
        pose = (
            selected_launch.pose
            if selected_launch is not None and entity.entity_id == selected_launch.primary_uav_entity_id
            else entity.pose
        )
        entity_transforms[entity.entity_id] = _math_transform_from_world_pose(pose)

    entities = tuple(
        ResolvedEntity(
            entity_id=entity.entity_id,
            kind=entity.kind,
            owner_kind="scenario" if entity.state == "static" else "provider",
            owner_id=(
                "scenario.compiler" if entity.state == "static" else entity.provider_id
            ),
            source_provider_id=(None if entity.state == "static" else entity.provider_id),
            authority_kind=entity.authority_kind,
            state=entity.state,
            model_asset_id=entity.model_asset_id,
            initial_pose=_resolved_pose_from_transform(
                transform=entity_transforms[entity.entity_id],
                enu_transform=enu_transform,
                geoid_grid=geoid_grid,
                terrain_grid=terrain_grid,
                geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
            ),
            selected_launch_override=selected_launch is not None and entity.entity_id
            == selected_launch.primary_uav_entity_id,
        )
        for entity in sorted(world.entities, key=lambda item: item.entity_id)
        if entity.state == "static" or entity.provider_id in provider_ids
    )

    sensors = tuple(
        ResolvedSensor(
            sensor_id=sensor.sensor_id,
            provider_id=sensor.provider_id,
            parent_entity_id=sensor.parent_entity_id,
            kind=sensor.kind,
            initial_pose=_resolved_pose_from_transform(
                transform=entity_transforms[sensor.parent_entity_id].compose(
                    _math_transform_from_parent_pose(sensor.pose)
                ),
                enu_transform=enu_transform,
                geoid_grid=geoid_grid,
                terrain_grid=terrain_grid,
                geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
            ),
            horizontal_fov_deg=sensor.horizontal_fov_deg,
            vertical_fov_deg=sensor.vertical_fov_deg,
            resolution_width_px=sensor.resolution_width_px,
            resolution_height_px=sensor.resolution_height_px,
        )
        for sensor in sorted(world.sensors, key=lambda item: item.sensor_id)
        if sensor.provider_id in provider_ids
        and source_entity_map[sensor.parent_entity_id].state == "static"
        or (
            sensor.provider_id in provider_ids
            and source_entity_map[sensor.parent_entity_id].provider_id in provider_ids
        )
    )

    active_sensor_ids = {sensor.sensor_id for sensor in sensors}
    active_entity_ids = {entity.entity_id for entity in entities}
    semantic_targets = tuple(
        ResolvedSemanticTarget(
            target_id=target.target_id,
            parent_entity_id=target.parent_entity_id,
            required_sensor_id=target.required_sensor_id,
            pose=_resolved_pose_from_transform(
                transform=entity_transforms[target.parent_entity_id].compose(
                    _math_transform_from_parent_pose(target.pose)
                ),
                enu_transform=enu_transform,
                geoid_grid=geoid_grid,
                terrain_grid=terrain_grid,
                geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
            ),
            geometry=target.geometry,
            surface_normal_target=target.surface_normal_target,
            min_distance_m=target.min_distance_m,
            max_distance_m=target.max_distance_m,
            max_view_angle_deg=target.max_view_angle_deg,
            fov_margin_deg=target.fov_margin_deg,
            dwell_time_s=target.dwell_time_s,
            required_evidence=tuple(sorted(target.required_evidence)),
        )
        for target in sorted(world.semantic_targets, key=lambda item: item.target_id)
        if target.required_sensor_id in active_sensor_ids
        and target.parent_entity_id in active_entity_ids
    )

    buildings = []
    for building in sorted(world.buildings, key=lambda item: item.building_id):
        anchor_east_m, anchor_north_m = _fixed_anchor(building.geometry.footprint_enu_m)
        buildings.append(
            ResolvedBuilding(
                building_id=building.building_id,
                entity_id=building.entity_id,
                source_asset_id=building.source_asset_id,
                render_asset_id=building.render_asset_id,
                collision_asset_id=building.collision_asset_id,
                anchor_east_m=anchor_east_m,
                anchor_north_m=anchor_north_m,
                base_vertices=_resolved_vertices(
                    points=building.geometry.footprint_enu_m,
                    bounds=world.frame.spatial_extent,
                    height_reference=building.geometry.height_reference,
                    height_m=building.geometry.base_altitude_m,
                    label=f"building {building.building_id} base_altitude_m",
                    enu_transform=enu_transform,
                    geoid_grid=geoid_grid,
                    terrain_grid=terrain_grid,
                    geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                    terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                ),
                top_vertices=_resolved_vertices(
                    points=building.geometry.footprint_enu_m,
                    bounds=world.frame.spatial_extent,
                    height_reference=building.geometry.height_reference,
                    height_m=building.geometry.top_altitude_m,
                    label=f"building {building.building_id} top_altitude_m",
                    enu_transform=enu_transform,
                    geoid_grid=geoid_grid,
                    terrain_grid=terrain_grid,
                    geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                    terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                ),
            )
        )

    roads = tuple(
        ResolvedRoad(
            road_id=road.road_id,
            kind=road.kind,
            width_m=road.width_m,
            terrain_points=tuple(
                _resolved_coordinate(
                    enu_transform=enu_transform,
                    geoid_grid=geoid_grid,
                    terrain_grid=terrain_grid,
                    geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                    terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                    east_m=point.east_m,
                    north_m=point.north_m,
                    up_m=_solve_enu_up(
                        enu_transform=enu_transform,
                        geoid_grid=geoid_grid,
                        terrain_grid=terrain_grid,
                        geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                        terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                        bounds=world.frame.spatial_extent,
                        east_m=point.east_m,
                        north_m=point.north_m,
                        height_reference="agl",
                        height_m=0.0,
                        label=f"road {road.road_id} centerline terrain point",
                    ),
                )
                for point in road.centerline_enu_m
            ),
        )
        for road in sorted(world.roads, key=lambda item: item.road_id)
    )

    regions = []
    for region in sorted(world.regions, key=lambda item: item.region_id):
        anchor_east_m, anchor_north_m = _fixed_anchor(region.geometry.footprint_enu_m)
        regions.append(
            ResolvedRegion(
                region_id=region.region_id,
                kind=region.kind,
                communications_shadow_attenuation_db=region.communications_shadow_attenuation_db,
                anchor_east_m=anchor_east_m,
                anchor_north_m=anchor_north_m,
                lower_vertices=_resolved_vertices(
                    points=region.geometry.footprint_enu_m,
                    bounds=world.frame.spatial_extent,
                    height_reference=region.geometry.height_reference,
                    height_m=region.geometry.min_altitude_m,
                    label=f"region {region.region_id} min_altitude_m",
                    enu_transform=enu_transform,
                    geoid_grid=geoid_grid,
                    terrain_grid=terrain_grid,
                    geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                    terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                ),
                upper_vertices=_resolved_vertices(
                    points=region.geometry.footprint_enu_m,
                    bounds=world.frame.spatial_extent,
                    height_reference=region.geometry.height_reference,
                    height_m=region.geometry.max_altitude_m,
                    label=f"region {region.region_id} max_altitude_m",
                    enu_transform=enu_transform,
                    geoid_grid=geoid_grid,
                    terrain_grid=terrain_grid,
                    geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                    terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
                ),
            )
        )

    launch_sites = tuple(
        ResolvedLaunchSite(
            launch_site_id=launch.launch_site_id,
            primary_uav_entity_id=launch.primary_uav_entity_id,
            allowed_uav_entity_ids=tuple(
                sorted(
                    entity_id
                    for entity_id in launch.allowed_uav_entity_ids
                    if entity_id in active_entity_ids
                )
            ),
            pose=_resolved_pose_from_transform(
                transform=_math_transform_from_world_pose(launch.pose),
                enu_transform=enu_transform,
                geoid_grid=geoid_grid,
                terrain_grid=terrain_grid,
                geoid_interpolation=world.frame.vertical_datum.geoid_interpolation,
                terrain_interpolation=world.frame.vertical_datum.terrain_interpolation,
            ),
            pad_radius_m=launch.pad_radius_m,
            selected=launch.launch_site_id == launch_site_id,
        )
        for launch in sorted(world.launch_sites, key=lambda item: item.launch_site_id)
        if launch.primary_uav_entity_id in active_entity_ids
    )

    engine_frame_bindings = tuple(
        ResolvedEngineFrameBinding(
            binding_id=binding.binding_id,
            provider_id=binding.provider_id,
            engine=binding.engine,
            scene_asset_id=binding.scene_asset_id,
            source_frame_id=binding.source_frame_id,
            target_frame_id=binding.target_frame_id,
            translation_m=ResolvedCartesianVector(
                x_m=binding.transform.x_m,
                y_m=binding.transform.y_m,
                z_m=binding.transform.z_m,
            ),
            rotation_matrix=_resolved_rotation(
                _math_quaternion_from_world_pose(binding.transform).to_rotation_matrix()
            ),
            rotation_quaternion=_resolved_quaternion(
                _math_quaternion_from_world_pose(binding.transform)
            ),
        )
        for binding in sorted(
            world.engine_frame_bindings, key=lambda item: item.binding_id
        )
        if binding.provider_id in provider_ids
    )

    sumo = (
        ResolvedSumoConfiguration(
            provider_id=world.sumo.provider_id,
            config_asset_id=world.sumo.config_asset_id,
            network_asset_id=world.sumo.network_asset_id,
            routes_asset_id=world.sumo.routes_asset_id,
            additional_asset_id=world.sumo.additional_asset_id,
            frame_binding_id=world.sumo.frame_binding_id,
            object_bindings=tuple(
                sorted(
                    world.sumo.object_bindings,
                    key=lambda binding: binding.sumo_object_id,
                )
            ),
        )
        if world.sumo is not None and world.sumo.provider_id in provider_ids
        else None
    )
    active_network_node_bindings = (
        tuple(
            sorted(
                (
                    binding
                    for binding in world.network.node_bindings
                    if binding.entity_id in active_entity_ids
                ),
                key=lambda binding: binding.node_id,
            )
        )
        if world.network is not None and world.network.provider_id in provider_ids
        else ()
    )
    if (
        world.network is not None
        and world.network.provider_id in provider_ids
        and not active_network_node_bindings
    ):
        raise ValueError(
            "enabled network provider has no node bound to an enabled scenario entity"
        )
    active_network_node_ids = {
        binding.node_id for binding in active_network_node_bindings
    }
    active_radio_profile_ids = {
        binding.radio_profile_id for binding in active_network_node_bindings
    }
    network = (
        ResolvedNetworkConfiguration(
            provider_id=world.network.provider_id,
            radio_profiles=tuple(
                sorted(
                    (
                        profile
                        for profile in world.network.radio_profiles
                        if profile.radio_profile_id in active_radio_profile_ids
                    ),
                    key=lambda profile: profile.radio_profile_id,
                )
            ),
            node_bindings=active_network_node_bindings,
            links=tuple(
                sorted(
                    (
                        link
                        for link in world.network.links
                        if link.source_node_id in active_network_node_ids
                        and link.destination_node_id in active_network_node_ids
                    ),
                    key=lambda link: link.link_id,
                )
            ),
        )
        if world.network is not None and world.network.provider_id in provider_ids
        else None
    )
    active_target_ids = {target.target_id for target in semantic_targets}
    unavailable_mission_targets = tuple(
        sorted(
            requirement.target_id
            for requirement in world.mission_requirements
            if requirement.kind == "observation"
            and requirement.target_id not in active_target_ids
        )
    )
    if unavailable_mission_targets:
        raise ValueError(
            "enabled scenario providers cannot satisfy mission targets: "
            f"{list(unavailable_mission_targets)}"
        )
    mission_requirements = tuple(
        ResolvedMissionRequirement(
            requirement_id=requirement.requirement_id,
            kind=requirement.kind,
            dependencies=tuple(sorted(requirement.dependencies)),
            launch_site_id=(
                launch_site_id
                if requirement.kind in {"takeoff", "return_to_launch", "land"}
                else requirement.launch_site_id
            ),
            target_id=requirement.target_id,
            expected_public_asset_id=requirement.expected_public_asset_id,
        )
        for requirement in sorted(
            world.mission_requirements,
            key=lambda item: item.requirement_id,
        )
    )
    expected_public_assets = tuple(
        sorted(world.expected_public_assets, key=lambda asset: asset.asset_id)
    )
    scenario_without_digest = ResolvedScenario.model_construct(
        schema_version=RESOLVED_SCENARIO_SCHEMA_VERSION,
        source_world_package=world_package,
        world_schema_version=world.schema_version,
        world_id=world.world_id,
        world_digest=world.world_digest,
        source_asset_digest=world.asset_digest,
        scenario_asset_digest=scenario_asset_digest,
        selected_launch_site_id=launch_site_id,
        seed=seed,
        frame_authority=frame_authority,
        providers=providers,
        assets=assets,
        task=task_binding,
        base_layers=tuple(sorted(world.base_layers, key=lambda layer: layer.layer_id)),
        layers=tuple(sorted(world.layers, key=lambda layer: layer.layer_id)),
        buildings=tuple(buildings),
        roads=roads,
        regions=tuple(regions),
        launch_sites=launch_sites,
        entities=entities,
        sensors=sensors,
        semantic_targets=semantic_targets,
        weather=tuple(sorted(world.weather, key=lambda sample: sample.sample_id)),
        engine_frame_bindings=engine_frame_bindings,
        sumo=sumo,
        network=network,
        mission_requirements=mission_requirements,
        expected_public_assets=expected_public_assets,
        scenario_digest="1" * 64,
    )
    scenario_digest = hashlib.sha256(
        canonical_json_bytes(_scenario_document(scenario_without_digest))
    ).hexdigest()
    return ResolvedScenario(
        schema_version=RESOLVED_SCENARIO_SCHEMA_VERSION,
        source_world_package=world_package,
        world_schema_version=world.schema_version,
        world_id=world.world_id,
        world_digest=world.world_digest,
        source_asset_digest=world.asset_digest,
        scenario_asset_digest=scenario_asset_digest,
        selected_launch_site_id=launch_site_id,
        seed=seed,
        frame_authority=frame_authority,
        providers=providers,
        assets=assets,
        task=task_binding,
        base_layers=tuple(sorted(world.base_layers, key=lambda layer: layer.layer_id)),
        layers=tuple(sorted(world.layers, key=lambda layer: layer.layer_id)),
        buildings=tuple(buildings),
        roads=roads,
        regions=tuple(regions),
        launch_sites=launch_sites,
        entities=entities,
        sensors=sensors,
        semantic_targets=semantic_targets,
        weather=tuple(sorted(world.weather, key=lambda sample: sample.sample_id)),
        engine_frame_bindings=engine_frame_bindings,
        sumo=sumo,
        network=network,
        mission_requirements=mission_requirements,
        expected_public_assets=expected_public_assets,
        scenario_digest=scenario_digest,
    )


__all__ = [
    "RESOLVED_SCENARIO_SCHEMA_VERSION",
    "SCALAR_GRID_SCHEMA_VERSION",
    "ResolvedAsset",
    "ResolvedAssetAudience",
    "ResolvedAssetLicense",
    "ResolvedBuilding",
    "ResolvedCartesianVector",
    "ResolvedCoordinate",
    "ResolvedEngineFrameBinding",
    "ResolvedEntity",
    "ResolvedFrameAuthority",
    "ResolvedFlightObservationProjection",
    "ResolvedInspectionObservationProjection",
    "ResolvedLaunchSite",
    "ResolvedMissionRequirement",
    "ResolvedNetworkConfiguration",
    "ResolvedPose",
    "ResolvedProvider",
    "ResolvedRegion",
    "ResolvedRoad",
    "ResolvedScenario",
    "ResolvedSemanticTarget",
    "ResolvedSensor",
    "ResolvedSumoConfiguration",
    "ResolvedTaskBinding",
    "ResolvedTaskObservationBinding",
    "ResolvedTaskQueryBinding",
    "ResolvedTaskScenarioProjection",
    "ResolvedUrbanRecoveryObservationProjection",
    "ResolvedTaskToolBinding",
    "ResolvedWorldAssetMetadata",
    "ScalarGrid",
    "compile_resolved_scenario",
    "scenario_assets_for_workload",
    "scenario_digest_value",
]
