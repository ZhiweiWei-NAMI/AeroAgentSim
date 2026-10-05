"""Strict declarations and engine evidence for the urban recovery demo."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime

PACKAGE_ID = "urban.uav-recovery-demo.v1"
DURATION_NS = 600_000_000_000
STEP_NS = 200_000_000
PHYSICS_STEP_NS = 4_000_000
FINAL_TICK = 3000


class DemoVector(StrictModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    z: float = Field(allow_inf_nan=False)


class DemoRole(StrictModel):
    agent_id: Identifier
    role: Literal["uav", "groundstation"]
    endpoint_id: Identifier
    vehicle_id: Identifier | None
    # These fields remain nullable so the reusable contract can still describe
    # camera-bearing workloads, but urban packages must leave them unset.
    camera_id: Identifier | None = None
    telemetry_observation_id: Identifier | None
    safety_observation_id: Identifier | None
    camera_observation_id: Identifier | None = None
    mailbox_observation_id: Identifier

    @model_validator(mode="after")
    def role_grants_are_complete(self) -> "DemoRole":
        vehicle_fields = (
            self.vehicle_id, self.telemetry_observation_id,
            self.safety_observation_id,
        )
        if self.role == "uav" and any(value is None for value in vehicle_fields):
            raise ValueError("UAV role requires its vehicle and telemetry/safety grants")
        if self.role == "groundstation" and any(
            value is not None
            for value in (
                *vehicle_fields,
                self.camera_id,
                self.camera_observation_id,
            )
        ):
            raise ValueError("groundstation cannot own UAV flight or sensor authority")
        return self


class RecoveryPolicy(StrictModel):
    incident_vehicle_id: Identifier
    incident_region_id: Identifier
    injection_start_ns: int = Field(ge=0, le=120_000_000_000)
    heartbeat_interval_ns: Literal[1_000_000_000]
    response_timeout_ns: int = Field(gt=0, le=30_000_000_000)
    maximum_retries: int = Field(ge=0, le=5)
    vehicle_radius_m: float = Field(gt=0, le=5, allow_inf_nan=False)
    obstacle_margin_m: float = Field(gt=0, le=50, allow_inf_nan=False)
    cruise_agl_m: float = Field(gt=0, le=200, allow_inf_nan=False)
    landing_deadline_ns: int = Field(gt=0, le=540_000_000_000)


class DemoTaskPackage(StrictModel):
    schema_version: Literal["aero-bench.urban-recovery-demo/v1"]
    package_id: Literal["urban.uav-recovery-demo.v1"]
    replay_mode: Literal["indexed"]
    execution_profile: Literal["formal", "engineering"] = "formal"
    recovery_variant: Literal["baseline", "direct-recovery", "no-recovery"] = "baseline"
    task_id: Identifier
    verifier_id: Identifier
    scene_source_sha256: Sha256
    duration_ns: int = Field(gt=0, le=DURATION_NS)
    step_ns: Literal[200_000_000]
    physics_step_ns: Literal[4_000_000]
    final_tick: int = Field(gt=0, le=FINAL_TICK)
    roles: tuple[DemoRole, ...] = Field(min_length=3, max_length=3)
    recovery: RecoveryPolicy
    flight_provider_id: Identifier
    network_provider_id: Identifier
    traffic_provider_id: Identifier
    required_channels: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def exact_demo_scope(self) -> "DemoTaskPackage":
        if self.scene_source_sha256 == "0" * 64:
            raise ValueError("scene_source_sha256 must be a real digest")
        if self.execution_profile == "formal" and (
            self.duration_ns != DURATION_NS
            or self.final_tick != FINAL_TICK
            or self.recovery_variant != "baseline"
            or self.recovery.injection_start_ns < 60_000_000_000
            or self.recovery.landing_deadline_ns <= 120_000_000_000
        ):
            raise ValueError(
                "formal demo requires the exact 600-second baseline horizon and formal recovery timing"
            )
        ids = tuple(role.agent_id for role in self.roles)
        if ids != tuple(sorted(set(ids))):
            raise ValueError("demo roles must have sorted unique Agent identities")
        expected_roles = {
            "groundstation.rule": "groundstation",
            "uav.policy.01": "uav",
            "uav.policy.02": "uav",
        }
        if {role.agent_id: role.role for role in self.roles} != expected_roles:
            raise ValueError("demo roles must be exactly the two UAV policies and groundstation rule")
        if (self.flight_provider_id, self.network_provider_id, self.traffic_provider_id) != (
            "flight", "network", "traffic"
        ):
            raise ValueError("demo Providers must be flight, network, and traffic")
        if len({role.endpoint_id for role in self.roles}) != len(self.roles):
            raise ValueError("each role must own a unique network endpoint")
        uavs = tuple(role for role in self.roles if role.role == "uav")
        if len(uavs) != 2 or sum(role.role == "groundstation" for role in self.roles) != 1:
            raise ValueError("demo requires exactly two UAV roles and one groundstation")
        if len({role.vehicle_id for role in uavs}) != 2:
            raise ValueError("UAVs must have distinct vehicles")
        if any(
            role.camera_id is not None or role.camera_observation_id is not None
            for role in self.roles
        ):
            raise ValueError("urban recovery packages cannot declare RGB camera authority")
        observation_ids = {
            observation_id
            for role in self.roles
            for observation_id in (
                role.telemetry_observation_id,
                role.safety_observation_id,
                role.mailbox_observation_id,
            )
            if observation_id is not None
        }
        declared_observation_ids = [
            observation_id
            for role in self.roles
            for observation_id in (
                role.telemetry_observation_id,
                role.safety_observation_id,
                role.mailbox_observation_id,
            )
            if observation_id is not None
        ]
        if len(observation_ids) != len(declared_observation_ids):
            raise ValueError("demo observation identities must be globally unique")
        if self.recovery.incident_vehicle_id not in {role.vehicle_id for role in uavs}:
            raise ValueError("incident vehicle must be a declared UAV")
        if self.required_channels != tuple(sorted(set(self.required_channels))):
            raise ValueError("required channels must be sorted and unique")
        required = {"agent", "gazebo.airspace", "gazebo.contact", "gazebo.force", "gazebo.wind", "mavlink", "ns3", "px4", "sumo", "sumo.signals"}
        if set(self.required_channels) != required:
            raise ValueError("demo must declare all required evidence channels exactly")
        if self.step_ns * self.final_tick != self.duration_ns or self.step_ns % self.physics_step_ns:
            raise ValueError("demo physical steps and exact horizon do not close")
        return self


class EngineWindow(StrictModel):
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    at: SimulationTime
    engine_start_ns: int = Field(ge=0)
    engine_end_ns: int = Field(gt=0)
    logical_origin_engine_ns: int = Field(ge=0)
    first_sequence: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    record_count: int = Field(ge=0)
    journal_sha256: Sha256
    plugin_sha256: Sha256

    @model_validator(mode="after")
    def closed_window(self) -> "EngineWindow":
        if self.engine_end_ns - self.engine_start_ns != STEP_NS:
            raise ValueError("engine window must span exactly one demo barrier")
        if self.engine_end_ns - self.logical_origin_engine_ns != self.at.sim_time_ns:
            raise ValueError("engine and logical time mapping does not close")
        if self.at.tick < 1 or self.at.tick > FINAL_TICK or self.at.sim_time_ns != self.at.tick * STEP_NS:
            raise ValueError("engine window is outside the demo tick grid")
        if self.last_sequence < self.first_sequence or self.last_sequence - self.first_sequence != self.record_count:
            raise ValueError("engine journal sequence inventory does not close")
        return self


class AirspaceTransition(StrictModel):
    schema_version: Literal["aero-bench.gazebo-airspace-transition/v1"]
    vehicle_id: Identifier
    region_id: Identifier
    engine_model: str = Field(min_length=1, max_length=256)
    engine_sim_time_ns: int = Field(ge=0)
    sequence: int = Field(ge=0)
    transition: Literal["entered", "exited"]
    source: Literal["gazebo.system"]
    world_sha256: Sha256
    region_sha256: Sha256
    position_enu_m: DemoVector


class AppliedWrench(StrictModel):
    vehicle_id: Identifier
    engine_link: str = Field(min_length=1, max_length=256)
    engine_sim_time_ns: int = Field(ge=0)
    component: Literal["wind", "aerodynamic", "rotor", "gravity", "contact"]
    origin: Literal["applied", "solver_measured"]
    frame: Literal["ENU"]
    force_n: DemoVector
    torque_nm: DemoVector

    @model_validator(mode="after")
    def force_origin_is_explicit(self) -> "AppliedWrench":
        if (self.component == "contact") != (self.origin == "solver_measured"):
            raise ValueError("contact wrench must come from the solver, not a model or telemetry inference")
        return self


class PhysicsJournalRecord(StrictModel):
    """One native 4 ms step, not a telemetry-derived force estimate.

    EngineWindow.journal_sha256 hashes the exact fifty JSONL records in
    (first_sequence, last_sequence], including their trailing newlines.
    """

    schema_version: Literal["aero-bench.gazebo-physics-journal/v1"]
    source: Literal["gazebo.system"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Literal["flight"]
    plugin_sha256: Sha256
    engine_sim_time_ns: int = Field(gt=0)
    sequence: int = Field(gt=0)
    wrenches: tuple[AppliedWrench, ...] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def exact_step_inventory(self) -> "PhysicsJournalRecord":
        if self.engine_sim_time_ns % PHYSICS_STEP_NS:
            raise ValueError("physics journal time is outside the 4 ms grid")
        if any(wrench.engine_sim_time_ns != self.engine_sim_time_ns for wrench in self.wrenches):
            raise ValueError("physics journal wrenches do not belong to this step")
        keys = tuple((wrench.vehicle_id, wrench.component, wrench.engine_link) for wrench in self.wrenches)
        if keys != tuple(sorted(set(keys))):
            raise ValueError("physics journal wrench inventory must be sorted and unique")
        return self


class CameraFrameRef(StrictModel):
    schema_version: Literal["aero-bench.gazebo-rgb-frame/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    vehicle_id: Identifier
    camera_id: Identifier
    frame_id: Identifier
    at: SimulationTime
    engine_sim_time_ns: int = Field(ge=0)
    logical_origin_engine_ns: int = Field(ge=0)
    pose_sha256: Sha256
    intrinsics_sha256: Sha256
    image_sha256: Sha256
    size_bytes: int = Field(gt=0, le=2 * 1024 * 1024)
    width: Literal[640]
    height: Literal[480]
    media_type: Literal["image/png"]
    artifact_id: Identifier
    selector: str = Field(pattern=r"^frames/[A-Za-z0-9_.-]+$")
    source: Literal["gazebo.camera"]

    @model_validator(mode="after")
    def exact_capture_time(self) -> "CameraFrameRef":
        if self.at.tick < 1 or self.at.tick > FINAL_TICK or self.at.sim_time_ns != self.at.tick * STEP_NS:
            raise ValueError("RGB frame must bind a main-run demo tick")
        if self.engine_sim_time_ns - self.logical_origin_engine_ns != self.at.sim_time_ns:
            raise ValueError("RGB header time differs from expected logical capture time")
        return self
