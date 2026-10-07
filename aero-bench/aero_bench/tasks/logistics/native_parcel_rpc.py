"""Business-service component for the closed-stage single-parcel contract.

The enclosing provider authenticates Harness RPC and supplies its current clock.
This component constructs reception evidence at that clock; callers cannot supply
principal grants, custody, dwell results, or reception timestamps. It does not
start a server, advance a provider, or alter a sealed motion SceneState.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import CommandRequest, SceneState, SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.facility_geometry import FacilityLandingPad
from aero_bench.tasks.logistics.native_parcel_contract import (
    AdmittedParcelAction, NativeParcelContract, NATIVE_PARCEL_ACTION_SCHEMA_VERSION,
)
from aero_bench.tasks.logistics.native_parcel_runtime import (
    NativeParcelRuntimeError, NativeParcelStateMachine, ParcelAdmissionOutcome,
    parcel_carriage_pose,
)
from aero_bench.tasks.logistics.orders import LogisticsIdentifier
from aero_bench.tasks.logistics.observation_ingress import LogisticsObservationBatch
from aero_bench.tasks.logistics.native_parcel_stage import NativeParcelStageConsumer
from aero_bench.world.resolved import ResolvedQuaternion

PARCEL_SNAPSHOT_OPERATION = "logistics.parcel.snapshot"
PARCEL_PICKUP_TOOL = "logistics.parcel.pickup"
PARCEL_DROPOFF_TOOL = "logistics.parcel.dropoff"


class DeclaredParcelFacilityPose(StrictModel):
    """Authored parcel resting pose, in the shared scene frame, in metres."""
    x_m: float = Field(allow_inf_nan=False)
    y_m: float = Field(allow_inf_nan=False)
    z_m: float = Field(allow_inf_nan=False)
    orientation: ResolvedQuaternion


class NativeParcelSessionConfig(StrictModel):
    schema_version: Literal["aero-bench.native-parcel-session/v1"]
    contract: NativeParcelContract
    pickup_pad: FacilityLandingPad
    dropoff_pad: FacilityLandingPad
    initial_parcel_pose: DeclaredParcelFacilityPose
    final_parcel_pose: DeclaredParcelFacilityPose
    step_ns: int = Field(gt=0)
    max_steps: int = Field(gt=0)
    calibration_digest: Sha256

    @model_validator(mode="after")
    def bound_pads(self):
        ids = self.contract.identities
        if (self.pickup_pad.facility_id != ids.pickup_facility_id
                or self.dropoff_pad.facility_id != ids.dropoff_facility_id):
            raise ValueError("parcel pads must bind the exact declared facilities")
        for pad, pose in ((self.pickup_pad,self.initial_parcel_pose),
                          (self.dropoff_pad,self.final_parcel_pose)):
            if pose.y_m < pad.y:
                raise ValueError("declared resting parcel reference cannot be below its pad")
            angle = math.radians(pad.rotation_deg)
            dx, dz = pose.x_m-pad.x, pose.z_m-pad.z
            local_x = math.cos(angle)*dx + math.sin(angle)*dz
            local_z = -math.sin(angle)*dx + math.cos(angle)*dz
            if abs(local_x) > pad.width_m/2 or abs(local_z) > pad.depth_m/2:
                raise ValueError("declared resting parcel reference is outside its pad")
        return self


class NativeParcelProjection(StrictModel):
    """Derived business entity, separate from Gazebo physical motion samples."""
    schema_version: Literal["aero-bench.native-parcel-projection/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    source_scene_state_digest: Sha256
    source_stage_barrier_digest: Sha256
    source_observation_digest: Sha256
    parcel_id: LogisticsIdentifier
    order_id: LogisticsIdentifier
    carrier_entity_id: Identifier | None
    custody_holder_id: LogisticsIdentifier
    custody_holder_kind: Literal["pickup_facility", "carrier", "dropoff_facility"]
    destination_id: LogisticsIdentifier
    state: Literal["awaiting_pickup", "loaded", "in_transit", "delivered"]
    pose: DeclaredParcelFacilityPose
    authority: Literal["modeled_business_custody"]


class NativeParcelSceneFrame(StrictModel):
    """One immutable motion SceneState with its separately derived business entity.

    The parcel is not a fabricated Gazebo StateSample or a motion provider.
    Both values are from this same closed stage and retain separate authorities.
    """
    schema_version: Literal["aero-bench.native-parcel-scene-frame/v1"]
    scene_state: SceneState
    parcel: NativeParcelProjection

    @model_validator(mode="after")
    def same_closed_native_stage(self):
        scene, parcel = self.scene_state,self.parcel
        if (scene.run_id != parcel.run_id or scene.scenario_digest != parcel.scenario_digest
                or scene.at != parcel.at or scene.scene_state_digest != parcel.source_scene_state_digest
                or scene.stage_barrier.barrier_digest != parcel.source_stage_barrier_digest):
            raise ValueError("parcel frame differs from its closed native SceneState")
        return self


class NativeParcelActionRecord(StrictModel):
    request: CommandRequest
    action: AdmittedParcelAction
    outcome: ParcelAdmissionOutcome

    @model_validator(mode="after")
    def command_binding(self):
        request,action = self.request,self.action
        if (request.run_id != action.run_id or request.command_id != action.action_id
                or request.agent_id != action.principal_id
                or self.outcome.action_id != action.action_id
                or self.outcome.action_digest != action.action_digest()
                or self.outcome.decision_at != action.received_at):
            raise ValueError("parcel action record differs from the received command")
        tools = {"pickup":PARCEL_PICKUP_TOOL,"dropoff":PARCEL_DROPOFF_TOOL}
        if (request.tool_id != tools[action.kind] or request.issued_at != action.received_at
                or {v.name:v.value for v in request.arguments} != {
                    "order_id":action.order_id,"parcel_id":action.parcel_entity_id,
                    "actor_id":action.carrier_entity_id}):
            raise ValueError("parcel command payload differs from its server action")
        return self


class NativeParcelRpcComponent:
    def __init__(self, *, config: NativeParcelSessionConfig, run_id: str,
                 scenario_digest: str, business_provider_id: str):
        self.config = config
        self.run_id = run_id
        self.scenario_digest = scenario_digest
        self.business_provider_id = business_provider_id
        self.machine = NativeParcelStateMachine(
            contract=config.contract, run_id=run_id,
            now=SimulationTime(tick=0, sim_time_ns=0),
            pickup_pad_index=config.pickup_pad.pad_index,
            dropoff_pad_index=config.dropoff_pad.pad_index,
        )
        self.batch: LogisticsObservationBatch | None = None
        self.stage_consumer = NativeParcelStageConsumer(self.machine)
        self.actions: list[dict[str, object]] = []
        self._requests: dict[tuple[str, str, str], tuple[bytes, dict[str, object]]] = {}

    def ingest_closed_stage(self, batch: LogisticsObservationBatch) -> None:
        if batch.run_id != self.run_id or batch.scenario_digest != self.scenario_digest:
            raise NativeParcelRuntimeError("parcel batch belongs to another run/scenario")
        if (batch.at.tick > self.config.max_steps
                or batch.at.sim_time_ns != batch.at.tick * self.config.step_ns):
            raise NativeParcelRuntimeError("parcel batch violates the declared clock/run bound")
        expected_pads = {self.config.pickup_pad.facility_id:self.config.pickup_pad,
                         self.config.dropoff_pad.facility_id:self.config.dropoff_pad}
        for observation in batch.observations:
            if expected_pads.get(observation.facility_id) != observation.pad:
                raise NativeParcelRuntimeError("parcel batch geometry differs from compiled pad")
        # Batch completeness (every declared pad exactly once) and shared
        # carrier-source consistency are owned by the strict stage batch
        # validator, which runs inside consume_batch; the session keeps only
        # its compiled geometry, clock and run-bound checks.
        self.stage_consumer.consume_batch(batch)
        self.batch = batch

    def transition(self, request: CommandRequest, *, now: SimulationTime) -> dict[str, object]:
        if self.batch is None or now != self.batch.at:
            raise NativeParcelRuntimeError("parcel admission needs the current closed observation stage")
        if request.run_id != self.run_id:
            raise NativeParcelRuntimeError("parcel command has a foreign run")
        kind = {PARCEL_PICKUP_TOOL:"pickup", PARCEL_DROPOFF_TOOL:"dropoff"}.get(request.tool_id)
        if kind is None:
            raise NativeParcelRuntimeError("unsupported native parcel action")
        ids = self.config.contract.identities
        args = {item.name:item.value for item in request.arguments}
        expected = {"order_id":ids.order_id, "parcel_id":ids.parcel_entity_id,
                    "actor_id":ids.carrier_entity_id}
        if args != expected:
            raise NativeParcelRuntimeError("parcel action must name its exact order/parcel/carrier")
        if request.agent_id != ids.authorized_principal_id:
            raise NativeParcelRuntimeError("principal is not independently bound to this carrier")
        key = (request.run_id, request.agent_id, request.command_id)
        encoded = canonical_json_bytes(request.model_dump(mode="json"))
        if key in self._requests:
            original, record = self._requests[key]
            if original != encoded:
                raise NativeParcelRuntimeError("parcel action ID reused with different request content")
            return record
        if request.issued_at != now:
            raise NativeParcelRuntimeError("new parcel action issue time differs from current admission time")
        # This is the enclosing authenticated service's actual reception time
        # and previously accepted barrier, never a caller-supplied RX claim.
        action = AdmittedParcelAction(
            schema_version=NATIVE_PARCEL_ACTION_SCHEMA_VERSION,
            run_id=self.run_id, action_id=request.command_id, kind=kind,
            evidence_basis="closed_stage_action_rx", order_id=ids.order_id,
            parcel_entity_id=ids.parcel_entity_id, carrier_entity_id=ids.carrier_entity_id,
            principal_id=request.agent_id, received_at=now,
            rx_stage_barrier_digest=self.batch.source_stage_barrier_digest,
        )
        result = self.machine.admit_action(action, now=now)
        record = NativeParcelActionRecord(request=request,action=action,outcome=result).model_dump(mode="json")
        self.actions.append(record)
        self._requests[key] = (encoded, record)
        return record

    def projection(self) -> NativeParcelProjection:
        if self.batch is None:
            raise NativeParcelRuntimeError("parcel projection has no closed native source")
        observation = self.batch.observations[0]
        ids = self.config.contract.identities
        state = self.machine.parcel_state
        if state.holder.holder_kind == "carrier":
            derived = parcel_carriage_pose(attachment=self.config.contract.attachment,
                                           observation=observation)
            pose = DeclaredParcelFacilityPose(
                x_m=derived.position_x_m, y_m=derived.position_y_m, z_m=derived.position_z_m,
                orientation=ResolvedQuaternion(qw=derived.orientation_w,qx=derived.orientation_x,
                                               qy=derived.orientation_y,qz=derived.orientation_z),
            )
            carrier = self.config.contract.carrier.native_vehicle_id
        else:
            pose = (self.config.initial_parcel_pose if state.state == "awaiting_pickup"
                    else self.config.final_parcel_pose)
            carrier = None
        return NativeParcelProjection(
            schema_version="aero-bench.native-parcel-projection/v1",
            run_id=self.run_id, scenario_digest=self.scenario_digest, at=self.batch.at,
            source_scene_state_digest=self.batch.source_scene_state_digest,
            source_stage_barrier_digest=self.batch.source_stage_barrier_digest,
            source_observation_digest=observation.observation_digest,
            parcel_id=ids.parcel_entity_id, order_id=ids.order_id,
            carrier_entity_id=carrier, custody_holder_id=state.holder.holder_id,
            custody_holder_kind=state.holder.holder_kind, destination_id=ids.dropoff_facility_id,
            state=state.state, pose=pose, authority="modeled_business_custody",
        )

    def snapshot(self) -> dict[str, object]:
        return {"config":self.config.model_dump(mode="json"),
                "machine":self.machine.snapshot().model_dump(mode="json"),
                "actions":list(self.actions),
                "projection":(None if self.batch is None else self.projection().model_dump(mode="json"))}
