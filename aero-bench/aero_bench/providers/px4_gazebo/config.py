from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from aero_bench.config.models import FileRef, Identifier, Sha256, StrictModel


StrictIdentifier = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_.-]*$")]
StrictFlightMode = Annotated[str, Field(strict=True, pattern=r"^[A-Z][A-Z0-9_]*$")]


class SoftwareIdentity(StrictModel):
    version: Annotated[str, Field(min_length=1)]
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class Pose(StrictModel):
    x_m: float
    y_m: float
    z_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float

    @field_validator("x_m", "y_m", "z_m", "roll_rad", "pitch_rad", "yaw_rad")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("vehicle pose values must be finite")
        return value


class VehicleSpec(StrictModel):
    vehicle_id: Identifier
    system_id: Annotated[int, Field(ge=1, le=255)]
    mavsdk_udp_port: Annotated[int, Field(ge=1024, le=65535)]
    px4_mavlink_udp_port: Annotated[int, Field(ge=1024, le=65535)]
    mavsdk_grpc_port: Annotated[int, Field(ge=1024, le=65535)]
    sys_autostart: Annotated[int, Field(ge=1)]
    model: Identifier
    gazebo_model_name: Identifier
    gazebo_resource: Identifier
    initial_pose: Pose


class Point3D(StrictModel):
    x_m: float
    y_m: float
    z_m: float

    @field_validator("x_m", "y_m", "z_m")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("target position values must be finite")
        return value


class InspectionFlightSpec(StrictModel):
    work_order_id: Identifier
    target_id: Identifier
    target_position: Point3D
    target_model_asset_id: Identifier
    observation_id: Identifier
    camera_id: Identifier
    camera_vehicle_id: Identifier
    camera_mount: Pose
    metadata_schema: FileRef
    media_type: Literal["image/png"] = "image/png"
    min_distance_m: Annotated[float, Field(gt=0)]
    max_distance_m: Annotated[float, Field(gt=0)]
    min_view_angle_deg: Annotated[float, Field(ge=0, le=180)]
    max_view_angle_deg: Annotated[float, Field(ge=0, le=180)]
    earliest_time_ns: Annotated[int, Field(ge=0)]
    latest_time_ns: Annotated[int, Field(gt=0)]

    @field_validator(
        "min_distance_m", "max_distance_m", "min_view_angle_deg", "max_view_angle_deg"
    )
    @classmethod
    def finite_bounds(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("inspection camera bounds must be finite")
        return value

    @model_validator(mode="after")
    def ranges_are_ordered(self) -> "InspectionFlightSpec":
        if self.min_distance_m > self.max_distance_m:
            raise ValueError("inspection distance range is inverted")
        if self.min_view_angle_deg > self.max_view_angle_deg:
            raise ValueError("inspection view-angle range is inverted")
        if self.earliest_time_ns >= self.latest_time_ns:
            raise ValueError("inspection observation time window is empty")
        return self


class AirspaceTransitionSpec(StrictModel):
    """Authoritative axis-aligned no-fly region observed by Gazebo."""

    world_id: Identifier
    world_digest: Sha256
    region_id: Identifier
    region_digest: Sha256
    incident_vehicle: Identifier
    transition_topic: Annotated[str, Field(min_length=1, pattern=r"^/[^\s]+$")]
    min_east_m: float
    max_east_m: float
    min_north_m: float
    max_north_m: float
    min_up_m: float
    max_up_m: float

    @field_validator(
        "min_east_m", "max_east_m", "min_north_m", "max_north_m",
        "min_up_m", "max_up_m",
    )
    @classmethod
    def finite_bounds(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("airspace bounds must be finite")
        return value

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> "AirspaceTransitionSpec":
        for lower, upper, axis in (
            (self.min_east_m, self.max_east_m, "east"),
            (self.min_north_m, self.max_north_m, "north"),
            (self.min_up_m, self.max_up_m, "up"),
        ):
            if lower > upper:
                raise ValueError(f"airspace {axis} bounds are inverted")
        if self.world_digest == "0" * 64 or self.region_digest == "0" * 64:
            raise ValueError("airspace identity digests must be real")
        return self


class PhysicalCompletionPolicy(StrictModel):
    schema_version: Literal["aero-bench.px4-physical-completion-policy/v2"]
    physical_sim_timeout_ns: Annotated[int, Field(gt=0, strict=True)]
    min_settle_samples: Annotated[int, Field(ge=2, strict=True)]
    settle_duration_ns: Annotated[int, Field(gt=0, strict=True)]
    takeoff_altitude_tolerance_m: Annotated[float, Field(gt=0, strict=True)]
    goto_horizontal_tolerance_m: Annotated[float, Field(gt=0, strict=True)]
    goto_vertical_tolerance_m: Annotated[float, Field(gt=0, strict=True)]
    goto_minimum_progress_m: Annotated[float, Field(gt=0, strict=True)]
    hold_drift_radius_m: Annotated[float, Field(gt=0, strict=True)]
    max_horizontal_settled_speed_m_s: Annotated[float, Field(gt=0, strict=True)]
    max_vertical_settled_speed_m_s: Annotated[float, Field(gt=0, strict=True)]
    landing_max_speed_m_s: Annotated[float, Field(gt=0, strict=True)]
    landing_max_height_proxy_m: Annotated[float, Field(gt=0, strict=True)]
    disarm_requires_contact: Annotated[bool, Field(strict=True)]
    arm_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)
    disarm_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)
    takeoff_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)
    goto_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)
    hold_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)
    land_allowed_modes: tuple[StrictFlightMode, ...] = Field(min_length=1)

    @field_validator(
        "takeoff_altitude_tolerance_m",
        "goto_horizontal_tolerance_m",
        "goto_vertical_tolerance_m",
        "goto_minimum_progress_m",
        "hold_drift_radius_m",
        "max_horizontal_settled_speed_m_s",
        "max_vertical_settled_speed_m_s",
        "landing_max_speed_m_s",
        "landing_max_height_proxy_m",
        mode="before",
    )
    @classmethod
    def strict_float_input(cls, value: object) -> object:
        if not isinstance(value, float):
            raise ValueError(
                "physical completion policy floats must be explicit floats"
            )
        return value

    @field_validator(
        "takeoff_altitude_tolerance_m",
        "goto_horizontal_tolerance_m",
        "goto_vertical_tolerance_m",
        "goto_minimum_progress_m",
        "hold_drift_radius_m",
        "max_horizontal_settled_speed_m_s",
        "max_vertical_settled_speed_m_s",
        "landing_max_speed_m_s",
        "landing_max_height_proxy_m",
    )
    @classmethod
    def finite_positive_float(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("physical completion policy floats must be finite")
        return value

    @field_validator(
        "arm_allowed_modes",
        "disarm_allowed_modes",
        "takeoff_allowed_modes",
        "goto_allowed_modes",
        "hold_allowed_modes",
        "land_allowed_modes",
    )
    @classmethod
    def sorted_unique_modes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("physical completion policy modes must be unique")
        if tuple(sorted(value)) != value:
            raise ValueError("physical completion policy modes must be sorted")
        return value

    @model_validator(mode="after")
    def non_vacuous(self) -> "PhysicalCompletionPolicy":
        if self.settle_duration_ns >= self.physical_sim_timeout_ns:
            raise ValueError(
                "settle_duration_ns must be less than physical_sim_timeout_ns"
            )
        return self


class BundleWorldInput(StrictModel):
    """A digest-verified world SDF staged on the non-host input volume.

    ``path`` names the *exact* derived-input destination the executor staged
    (e.g. ``bundle/facility/logistics_world.sdf``).  Inside the provider
    container the service resolves it under ``AERO_BENCH_BUNDLE_DIR`` (the
    input volume's ``bundle/`` directory, after stripping the ``bundle/``
    input-volume prefix) and accepts the world ONLY from that volume with a
    startup SHA-256 verification.  There is never a fallback to the scenario
    image world for a declared bundle world, and provider configs without
    ``world_input`` keep the existing explicit world contract unchanged.
    """

    schema_version: Annotated[
        str, Field(pattern=r"^aero-bench\.px4-gazebo/bundle-world/v1$")
    ]
    source: Literal["bundle"]
    path: Annotated[
        str,
        Field(
            pattern=(
                r"^bundle/"
                r"(?:[a-z0-9][a-z0-9_.-]*/)*"
                r"[a-z0-9][a-z0-9_.-]*\.sdf$"
            )
        ),
    ]
    sha256: Sha256


class Px4GazeboConfig(StrictModel):
    """Implementation tuning and logical bindings for the real PX4 stack."""

    schema_version: Annotated[str, Field(pattern=r"^aero-bench\.px4-gazebo/v3$")]
    provider_id: Identifier
    px4: SoftwareIdentity
    gazebo: SoftwareIdentity
    mavsdk: SoftwareIdentity
    engine_binding_id: Identifier
    physics_step_ns: Annotated[int, Field(gt=0)]
    px4_executable: Annotated[str, Field(min_length=1)]
    gazebo_executable: Annotated[str, Field(min_length=1)]
    mavsdk_server_executable: Annotated[str, Field(min_length=1)]
    vehicles: tuple[VehicleSpec, ...] = Field(min_length=1)
    required_commands: tuple[Annotated[str, Field(min_length=1)], ...] = Field(
        min_length=1
    )
    command_timeout_ms: Annotated[int, Field(gt=0)]
    physical_completion_policy: PhysicalCompletionPolicy
    maximum_agent_decision_wall_time_ms: Annotated[int, Field(gt=0)]
    heartbeat_timeout_fixed_margin_ms: Annotated[int, Field(gt=0)]
    airspace_transition: AirspaceTransitionSpec | None = None
    world_name: Identifier | None = None
    world_input: BundleWorldInput | None = None

    @property
    def incoming_heartbeat_timeout_s(self) -> float:
        return (
            self.maximum_agent_decision_wall_time_ms
            + self.heartbeat_timeout_fixed_margin_ms
        ) / 1000.0

    @model_validator(mode="after")
    def unique_vehicle_identity(self) -> "Px4GazeboConfig":
        vehicle_ids = [vehicle.vehicle_id for vehicle in self.vehicles]
        system_ids = [vehicle.system_id for vehicle in self.vehicles]
        gazebo_names = [vehicle.gazebo_model_name for vehicle in self.vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise ValueError("vehicle_id values must be unique")
        if len(system_ids) != len(set(system_ids)):
            raise ValueError("PX4 system_id values must be unique")
        if len(gazebo_names) != len(set(gazebo_names)):
            raise ValueError("gazebo_model_name values must be unique")
        ports: list[int] = []
        for vehicle in self.vehicles:
            ports.extend(
                (
                    vehicle.mavsdk_udp_port,
                    vehicle.px4_mavlink_udp_port,
                    vehicle.mavsdk_grpc_port,
                )
            )
        if len(ports) != len(set(ports)):
            raise ValueError("PX4 MAVLink and MAVSDK ports must be unique")
        if len(self.required_commands) != len(set(self.required_commands)):
            raise ValueError("required_commands must be unique")
        executables = {
            self.px4_executable,
            self.gazebo_executable,
            self.mavsdk_server_executable,
        }
        if len(executables) != 3:
            raise ValueError("PX4, Gazebo, and MAVSDK executables must be distinct")
        missing = executables.difference(self.required_commands)
        if missing:
            raise ValueError(
                "required_commands must include every declared executable: "
                f"{sorted(missing)}"
            )
        if self.airspace_transition is not None:
            if self.world_name is None:
                raise ValueError(
                    "airspace transition requires an explicit world_name"
                )
            if self.airspace_transition.world_id != self.world_name:
                raise ValueError(
                    "airspace transition world_id must match world_name"
                )
            if self.airspace_transition.incident_vehicle not in {
                vehicle.gazebo_model_name for vehicle in self.vehicles
            }:
                raise ValueError("airspace transition incident_vehicle is undeclared")
        if self.world_input is not None and self.world_name is None:
            raise ValueError(
                "a digest-pinned bundle world input requires an explicit world_name"
            )
        return self
