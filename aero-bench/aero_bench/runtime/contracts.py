from __future__ import annotations

import hashlib
import math
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from aero_bench.config.models import Identifier, NamedValue, Sha256, StrictModel
from aero_bench.providers.stages import ProviderStage
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.resolved import ResolvedPose


class SimulationTime(StrictModel):
    tick: Annotated[int, Field(ge=0)]
    sim_time_ns: Annotated[int, Field(ge=0)]


class AgentTurnCompletion(StrictModel):
    schema_version: Literal["aero-bench.agent-turn-completion/v1"]
    run_id: Sha256
    agent_id: Identifier
    completion_id: Identifier
    at: SimulationTime
    disposition: Literal["advance", "finished"]
    command_ids: tuple[Identifier, ...]
    observation_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def unique_references(self) -> "AgentTurnCompletion":
        if len(self.command_ids) != len(set(self.command_ids)):
            raise ValueError("agent turn command_ids must be unique")
        if self.command_ids != tuple(sorted(self.command_ids)):
            raise ValueError("agent turn command_ids must be sorted")
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("agent turn observation_ids must be unique")
        if self.observation_ids != tuple(sorted(self.observation_ids)):
            raise ValueError("agent turn observation_ids must be sorted")
        return self


class AgentTurnDecision(StrictModel):
    schema_version: Literal["aero-bench.agent-turn-decision/v1"]
    run_id: Sha256
    status: Literal["waiting", "advanced", "terminated"]
    at: SimulationTime
    active_agent_ids: tuple[Identifier, ...]
    missing_agent_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def unique_agent_sets(self) -> "AgentTurnDecision":
        if len(self.active_agent_ids) != len(set(self.active_agent_ids)):
            raise ValueError("agent turn active_agent_ids must be unique")
        if len(self.missing_agent_ids) != len(set(self.missing_agent_ids)):
            raise ValueError("agent turn missing_agent_ids must be unique")
        if not set(self.missing_agent_ids).issubset(self.active_agent_ids):
            raise ValueError("agent turn missing agents must be active")
        if self.status == "terminated" and (
            self.active_agent_ids or self.missing_agent_ids
        ):
            raise ValueError("terminated agent turn decision cannot have active agents")
        return self


class ProviderEvent(StrictModel):
    provider_id: Identifier
    event_id: Identifier
    time: SimulationTime
    payload_schema_id: Identifier
    payload: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def unique_payload_names(self) -> "ProviderEvent":
        names = [item.name for item in self.payload]
        if len(names) != len(set(names)):
            raise ValueError("Provider event payload names must be unique")
        return self


class FinalizedArtifact(StrictModel):
    artifact_id: Identifier
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def digest_is_not_placeholder(self) -> "FinalizedArtifact":
        if self.sha256 == "0" * 64:
            raise ValueError("finalized artifact digest cannot be a placeholder")
        return self


class ProviderFinalizationRequest(StrictModel):
    schema_version: Literal["aero-bench.provider-finalization-request/v1"]
    run_id: Sha256
    terminal_event: Literal["run.completed"]
    terminal_time: SimulationTime
    event_chain_root: Sha256

    @model_validator(mode="after")
    def root_is_not_placeholder(self) -> "ProviderFinalizationRequest":
        if self.event_chain_root == "0" * 64:
            raise ValueError("final event chain root cannot be a placeholder")
        return self


class ProviderFinalizationReceipt(StrictModel):
    schema_version: Literal["aero-bench.provider-finalization-receipt/v1"]
    run_id: Sha256
    provider_id: Identifier
    event_chain_root: Sha256
    artifacts: tuple[FinalizedArtifact, ...]

    @model_validator(mode="after")
    def finalized_artifacts_are_unique(self) -> "ProviderFinalizationReceipt":
        if self.event_chain_root == "0" * 64:
            raise ValueError("finalization receipt root cannot be a placeholder")
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("finalization receipt artifact IDs must be unique")
        return self


class StepRequest(StrictModel):
    run_id: Sha256
    target: SimulationTime


class StepReceipt(StrictModel):
    run_id: Sha256
    provider_id: Identifier
    reached: SimulationTime
    state_digest: Sha256
    events: tuple[ProviderEvent, ...] = ()

    @model_validator(mode="after")
    def validate_authoritative_receipt(self) -> "StepReceipt":
        if self.state_digest == "0" * 64:
            raise ValueError("Provider state_digest cannot be a placeholder")
        for event in self.events:
            if event.provider_id != self.provider_id:
                raise ValueError("Provider receipt contains another Provider's event")
            if (
                event.time.tick > self.reached.tick
                or event.time.sim_time_ns > self.reached.sim_time_ns
            ):
                raise ValueError("Provider event occurs after the reached barrier time")
        return self


class CommandRequest(StrictModel):
    run_id: Sha256
    command_id: Identifier
    agent_id: Identifier
    tool_id: Identifier
    issued_at: SimulationTime
    arguments: tuple[NamedValue, ...] = ()

    @model_validator(mode="after")
    def unique_arguments(self) -> "CommandRequest":
        names = [item.name for item in self.arguments]
        if len(names) != len(set(names)):
            raise ValueError("command argument names must be unique")
        return self


class CommandReceipt(StrictModel):
    run_id: Sha256
    command_id: Identifier
    provider_id: Identifier
    phase: Literal["received", "accepted", "applied", "completed", "failed"]
    time: SimulationTime
    detail: str | None = None


# ProviderStage is the single cycle-free definition in
# ``aero_bench.providers.stages``; it is re-exported here for existing callers.
StateSampleKind: TypeAlias = Literal["static", "dynamic"]
StateAttributeType: TypeAlias = Literal["bool", "int", "float", "str", "null"]
StateAttributeValue: TypeAlias = StrictBool | StrictInt | StrictFloat | StrictStr | None


class StageBarrierDigest(StrictModel):
    """One causally prior closed StageBarrier, represented by its digest."""

    stage: ProviderStage
    barrier_digest: Sha256

    @model_validator(mode="after")
    def non_placeholder_digest(self) -> "StageBarrierDigest":
        if self.barrier_digest == "0" * 64:
            raise ValueError("predecessor barrier digest cannot be a placeholder")
        return self


def _validate_stage_dependencies(
    *,
    stage: ProviderStage,
    input_scene_state_digest: str | None,
    predecessor_barriers: tuple[StageBarrierDigest, ...],
) -> None:
    predecessor_stages = tuple(item.stage for item in predecessor_barriers)
    if len(predecessor_stages) != len(set(predecessor_stages)):
        raise ValueError("predecessor barrier stages must be unique")
    if stage == "motion":
        if input_scene_state_digest is not None:
            raise ValueError("motion stage cannot consume a SceneState digest")
        if predecessor_barriers:
            raise ValueError("motion stage cannot have predecessor barriers")
        return
    if input_scene_state_digest is None:
        raise ValueError("non-motion stages require an input SceneState digest")
    if input_scene_state_digest == "0" * 64:
        raise ValueError("input SceneState digest cannot be a placeholder")
    if stage == "network":
        expected = ("motion",)
    else:
        # A Business/Environment stage may follow Motion directly when Network is
        # disabled, or follow the exact Motion then Network closure.
        if predecessor_stages not in {("motion",), ("motion", "network")}:
            raise ValueError(
                "business_environment predecessors must be motion or motion then network"
            )
        return
    if predecessor_stages != expected:
        raise ValueError("network predecessors must be exactly the motion barrier")


def _finite_state_value(label: str, value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _sorted_unique(label: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")
    if values != tuple(sorted(values)):
        raise ValueError(f"{label} values must be sorted")


def _digest_document(value: StrictModel, digest_field: str) -> bytes:
    return canonical_json_bytes(
        value.model_dump(mode="json", exclude={digest_field})
    )


class EnuLinearVelocity(StrictModel):
    frame_id: Literal["ENU"] = "ENU"
    east_mps: float
    north_mps: float
    up_mps: float

    @field_validator("east_mps", "north_mps", "up_mps")
    @classmethod
    def finite_components(cls, value: float) -> float:
        return _finite_state_value("ENU linear velocity", value)


class NedLinearVelocity(StrictModel):
    frame_id: Literal["NED"] = "NED"
    north_mps: float
    east_mps: float
    down_mps: float

    @field_validator("north_mps", "east_mps", "down_mps")
    @classmethod
    def finite_components(cls, value: float) -> float:
        return _finite_state_value("NED linear velocity", value)


class BodyAngularVelocity(StrictModel):
    frame_id: Literal["body"] = "body"
    x_radps: float
    y_radps: float
    z_radps: float

    @field_validator("x_radps", "y_radps", "z_radps")
    @classmethod
    def finite_components(cls, value: float) -> float:
        return _finite_state_value("body angular velocity", value)


class StateAttribute(StrictModel):
    name: Identifier
    value_type: StateAttributeType
    value: StateAttributeValue

    @model_validator(mode="after")
    def value_matches_type(self) -> "StateAttribute":
        if self.value is None:
            actual_type = "null"
        elif isinstance(self.value, bool):
            actual_type = "bool"
        elif isinstance(self.value, int):
            actual_type = "int"
        elif isinstance(self.value, float):
            _finite_state_value("state attribute float", self.value)
            actual_type = "float"
        else:
            actual_type = "str"
        if self.value_type != actual_type:
            raise ValueError("state attribute value_type must match value")
        return self


class BatteryState(StrictModel):
    voltage_v: float | None = None
    current_a: float | None = None
    remaining_fraction: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    consumed_mah: float | None = None
    temperature_c: float | None = None
    attributes: tuple[StateAttribute, ...] = ()

    @field_validator(
        "voltage_v",
        "current_a",
        "remaining_fraction",
        "consumed_mah",
        "temperature_c",
    )
    @classmethod
    def finite_optional_values(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite_state_value("battery value", value)

    @model_validator(mode="after")
    def canonical_battery(self) -> "BatteryState":
        attribute_names = tuple(attribute.name for attribute in self.attributes)
        _sorted_unique("battery attribute", attribute_names)
        if (
            self.voltage_v is None
            and self.current_a is None
            and self.remaining_fraction is None
            and self.consumed_mah is None
            and self.temperature_c is None
            and not self.attributes
        ):
            raise ValueError("battery state must contain an available value")
        return self


class HealthState(StrictModel):
    healthy: bool | None = None
    attributes: tuple[StateAttribute, ...] = ()

    @model_validator(mode="after")
    def canonical_health(self) -> "HealthState":
        attribute_names = tuple(attribute.name for attribute in self.attributes)
        _sorted_unique("health attribute", attribute_names)
        if self.healthy is None and not self.attributes:
            raise ValueError("health state must contain an available value")
        return self


class StateAttributePatch(StrictModel):
    entity_id: Identifier
    attributes: tuple[StateAttribute, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_attributes(self) -> "StateAttributePatch":
        _sorted_unique(
            "state attribute patch",
            tuple(attribute.name for attribute in self.attributes),
        )
        return self


class StateSample(StrictModel):
    schema_version: Literal["aero-bench.state-sample/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    stage: ProviderStage
    entity_id: Identifier
    provider_id: Identifier
    sample_kind: StateSampleKind
    pose: ResolvedPose
    linear_velocity_enu: EnuLinearVelocity
    linear_velocity_ned: NedLinearVelocity
    angular_velocity_body: BodyAngularVelocity | None = None
    mode: Annotated[str, Field(min_length=1)] | None = None
    armed: bool | None = None
    battery: BatteryState | None = None
    health: HealthState | None = None
    contacts: tuple[Identifier, ...] = ()
    attributes: tuple[StateAttribute, ...] = ()
    sample_digest: Sha256

    @model_validator(mode="after")
    def canonical_state_sample(self) -> "StateSample":
        if self.stage != "motion":
            raise ValueError("state samples may only bind the motion stage")
        if (
            self.linear_velocity_ned.north_mps != self.linear_velocity_enu.north_mps
            or self.linear_velocity_ned.east_mps != self.linear_velocity_enu.east_mps
            or self.linear_velocity_ned.down_mps != -self.linear_velocity_enu.up_mps
        ):
            raise ValueError("ENU and NED linear velocities must describe one vector")
        _sorted_unique("state sample contact", self.contacts)
        _sorted_unique(
            "state sample attribute",
            tuple(attribute.name for attribute in self.attributes),
        )
        if self.sample_kind == "static":
            if self.provider_id != "scenario.compiler":
                raise ValueError("static state samples must be scenario.compiler-owned")
            if (
                self.linear_velocity_enu.east_mps != 0.0
                or self.linear_velocity_enu.north_mps != 0.0
                or self.linear_velocity_enu.up_mps != 0.0
                or self.angular_velocity_body is not None
            ):
                raise ValueError("static state samples cannot carry motion")
        elif self.provider_id == "scenario.compiler":
            raise ValueError("dynamic state samples require a provider owner")
        if self.sample_digest != state_sample_digest_value(self):
            raise ValueError("sample_digest does not match state sample content")
        return self


class SceneContribution(StrictModel):
    schema_version: Literal["aero-bench.scene-contribution/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    stage: ProviderStage
    provider_id: Identifier
    samples: tuple[StateSample, ...] = ()
    attribute_updates: tuple[StateAttributePatch, ...] = ()
    payload_digest: Sha256
    contribution_digest: Sha256

    @model_validator(mode="after")
    def canonical_scene_contribution(self) -> "SceneContribution":
        sample_ids = tuple(sample.entity_id for sample in self.samples)
        update_ids = tuple(update.entity_id for update in self.attribute_updates)
        _sorted_unique("scene contribution sample", sample_ids)
        _sorted_unique("scene contribution attribute update", update_ids)
        if self.stage == "motion":
            if not self.samples:
                raise ValueError("motion contributions must contain state samples")
            if self.attribute_updates:
                raise ValueError("motion contributions cannot carry attribute updates")
        elif self.samples:
            raise ValueError("non-motion contributions cannot carry state samples")
        for sample in self.samples:
            if (
                sample.run_id != self.run_id
                or sample.scenario_digest != self.scenario_digest
                or sample.at != self.at
                or sample.stage != self.stage
                or sample.provider_id != self.provider_id
            ):
                raise ValueError("scene contribution sample binding is inconsistent")
        if self.payload_digest != scene_contribution_payload_digest_value(self):
            raise ValueError("payload_digest does not match scene contribution payload")
        if self.contribution_digest != scene_contribution_digest_value(self):
            raise ValueError(
                "contribution_digest does not match scene contribution content"
            )
        return self


class StageReceipt(StrictModel):
    schema_version: Literal["aero-bench.stage-receipt/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    stage: ProviderStage
    provider_id: Identifier
    state_digest: Sha256
    step_receipt_digest: Sha256
    contribution_digest: Sha256
    payload_digest: Sha256
    input_scene_state_digest: Sha256 | None = None
    predecessor_barriers: tuple[StageBarrierDigest, ...] = ()
    receipt_digest: Sha256

    @model_validator(mode="after")
    def canonical_stage_receipt(self) -> "StageReceipt":
        if self.state_digest == "0" * 64:
            raise ValueError("stage receipt state_digest cannot be a placeholder")
        if self.step_receipt_digest == "0" * 64:
            raise ValueError("stage receipt StepReceipt digest cannot be a placeholder")
        _validate_stage_dependencies(
            stage=self.stage,
            input_scene_state_digest=self.input_scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        if self.receipt_digest != stage_receipt_digest_value(self):
            raise ValueError("receipt_digest does not match stage receipt content")
        return self


class StageBarrier(StrictModel):
    schema_version: Literal["aero-bench.stage-barrier/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    stage: ProviderStage
    input_scene_state_digest: Sha256 | None = None
    predecessor_barriers: tuple[StageBarrierDigest, ...] = ()
    provider_ids: tuple[Identifier, ...]
    receipts: tuple[StageReceipt, ...]
    receipt_digests: tuple[Sha256, ...]
    barrier_digest: Sha256

    @model_validator(mode="after")
    def canonical_stage_barrier(self) -> "StageBarrier":
        _validate_stage_dependencies(
            stage=self.stage,
            input_scene_state_digest=self.input_scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        _sorted_unique("stage barrier provider", self.provider_ids)
        receipt_provider_ids = tuple(receipt.provider_id for receipt in self.receipts)
        if receipt_provider_ids != self.provider_ids:
            raise ValueError("stage barrier receipts must close over provider_ids")
        for receipt in self.receipts:
            if (
                receipt.run_id != self.run_id
                or receipt.scenario_digest != self.scenario_digest
                or receipt.at != self.at
                or receipt.stage != self.stage
                or receipt.input_scene_state_digest != self.input_scene_state_digest
                or receipt.predecessor_barriers != self.predecessor_barriers
            ):
                raise ValueError("stage barrier receipt binding is inconsistent")
        expected_receipt_digests = tuple(
            receipt.receipt_digest for receipt in self.receipts
        )
        if self.receipt_digests != expected_receipt_digests:
            raise ValueError("receipt_digests must match the ordered receipt inventory")
        contribution_digests = tuple(
            receipt.contribution_digest for receipt in self.receipts
        )
        if len(contribution_digests) != len(set(contribution_digests)):
            raise ValueError("stage barrier contribution digests must be unique")
        if self.barrier_digest != stage_barrier_digest_value(self):
            raise ValueError("barrier_digest does not match stage barrier content")
        return self


SCENE_STATE_ROOT_DIGEST = "0" * 64


class SceneState(StrictModel):
    schema_version: Literal["aero-bench.scene-state/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    at: SimulationTime
    declared_entity_ids: tuple[Identifier, ...] = Field(min_length=1)
    samples: tuple[StateSample, ...] = Field(min_length=1)
    stage_barrier: StageBarrier
    contribution_digests: tuple[Sha256, ...]
    previous_scene_state_digest: Sha256
    scene_state_digest: Sha256

    @model_validator(mode="after")
    def canonical_scene_state(self) -> "SceneState":
        if self.at.tick < 1:
            raise ValueError("scene state tick must start at 1")
        if self.at.tick == 1:
            if self.previous_scene_state_digest != SCENE_STATE_ROOT_DIGEST:
                raise ValueError("first scene state must bind the chain root")
        elif self.previous_scene_state_digest == SCENE_STATE_ROOT_DIGEST:
            raise ValueError("later scene states must bind a prior scene state")
        _sorted_unique("scene state declared entity", self.declared_entity_ids)
        sample_ids = tuple(sample.entity_id for sample in self.samples)
        _sorted_unique("scene state sample", sample_ids)
        if sample_ids != self.declared_entity_ids:
            raise ValueError("scene state samples must close over declared_entity_ids")
        if self.stage_barrier.stage != "motion":
            raise ValueError("scene state requires a motion stage barrier")
        if (
            self.stage_barrier.run_id != self.run_id
            or self.stage_barrier.scenario_digest != self.scenario_digest
            or self.stage_barrier.at != self.at
        ):
            raise ValueError("scene state barrier binding is inconsistent")
        for sample in self.samples:
            if (
                sample.run_id != self.run_id
                or sample.scenario_digest != self.scenario_digest
                or sample.at != self.at
                or sample.stage != "motion"
            ):
                raise ValueError("scene state sample binding is inconsistent")
            if (
                sample.sample_kind == "dynamic"
                and sample.provider_id not in self.stage_barrier.provider_ids
            ):
                raise ValueError("dynamic sample provider is absent from motion barrier")
        expected_contribution_digests = tuple(
            sorted(
                receipt.contribution_digest
                for receipt in self.stage_barrier.receipts
            )
        )
        _sorted_unique("scene state contribution digest", self.contribution_digests)
        if self.contribution_digests != expected_contribution_digests:
            raise ValueError(
                "contribution_digests must match the stage barrier receipt inventory"
            )
        if self.scene_state_digest != scene_state_digest_value(self):
            raise ValueError("scene_state_digest does not match scene state content")
        return self


def _require_canonical_nested_contract(
    value: object,
    *,
    contract_type: type[StrictModel],
    label: str,
) -> None:
    if not isinstance(value, contract_type):
        raise ValueError(f"{label} has the wrong contract type")
    try:
        canonical = contract_type.model_validate(value.model_dump(mode="json"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} fails strict revalidation") from error
    if canonical != value:
        raise ValueError(f"{label} is not canonically serialized")


def _validate_scene_bound_stage_request(
    *,
    run_id: str,
    scenario_digest: str,
    target: SimulationTime,
    stage: ProviderStage,
    scene_state: SceneState,
    scene_state_digest: str,
    predecessor_barriers: tuple[StageBarrierDigest, ...],
) -> None:
    _require_canonical_nested_contract(
        scene_state,
        contract_type=SceneState,
        label="stage request SceneState",
    )
    _validate_stage_dependencies(
        stage=stage,
        input_scene_state_digest=scene_state_digest,
        predecessor_barriers=predecessor_barriers,
    )
    if (
        scene_state.run_id != run_id
        or scene_state.scenario_digest != scenario_digest
        or scene_state.at != target
    ):
        raise ValueError("stage request SceneState binding is inconsistent")
    if scene_state.scene_state_digest != scene_state_digest:
        raise ValueError("stage request SceneState digest is inconsistent")
    motion_barrier = predecessor_barriers[0]
    if (
        motion_barrier.stage != "motion"
        or motion_barrier.barrier_digest != scene_state.stage_barrier.barrier_digest
    ):
        raise ValueError("stage request predecessors do not bind the SceneState")


def _validate_stage_result_bindings(
    *,
    run_id: str,
    scenario_digest: str,
    target: SimulationTime,
    stage: ProviderStage,
    provider_id: str,
    step_receipt: StepReceipt,
    step_receipt_digest: str,
    contribution: SceneContribution,
    input_scene_state_digest: str | None,
    predecessor_barriers: tuple[StageBarrierDigest, ...],
) -> None:
    _require_canonical_nested_contract(
        step_receipt,
        contract_type=StepReceipt,
        label="stage result StepReceipt",
    )
    _require_canonical_nested_contract(
        contribution,
        contract_type=SceneContribution,
        label="stage result SceneContribution",
    )
    _validate_stage_dependencies(
        stage=stage,
        input_scene_state_digest=input_scene_state_digest,
        predecessor_barriers=predecessor_barriers,
    )
    if (
        step_receipt.run_id != run_id
        or step_receipt.provider_id != provider_id
        or step_receipt.reached != target
    ):
        raise ValueError("stage result StepReceipt binding is inconsistent")
    if step_receipt_digest != step_receipt_digest_value(step_receipt):
        raise ValueError("stage result StepReceipt digest is inconsistent")
    if any(event.time != target for event in step_receipt.events):
        raise ValueError("staged StepReceipt events must occur at the stage target")
    if (
        contribution.run_id != run_id
        or contribution.scenario_digest != scenario_digest
        or contribution.at != target
        or contribution.stage != stage
        or contribution.provider_id != provider_id
    ):
        raise ValueError("stage result SceneContribution binding is inconsistent")


class MotionStageRequest(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-request/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["motion"]


class NetworkStageRequest(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-request/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["network"]
    scene_state: SceneState
    scene_state_digest: Sha256
    predecessor_barriers: tuple[StageBarrierDigest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_network_request(self) -> "NetworkStageRequest":
        _validate_scene_bound_stage_request(
            run_id=self.run_id,
            scenario_digest=self.scenario_digest,
            target=self.target,
            stage=self.stage,
            scene_state=self.scene_state,
            scene_state_digest=self.scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        return self


class BusinessEnvironmentStageRequest(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-request/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["business_environment"]
    scene_state: SceneState
    scene_state_digest: Sha256
    predecessor_barriers: tuple[StageBarrierDigest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_business_environment_request(
        self,
    ) -> "BusinessEnvironmentStageRequest":
        _validate_scene_bound_stage_request(
            run_id=self.run_id,
            scenario_digest=self.scenario_digest,
            target=self.target,
            stage=self.stage,
            scene_state=self.scene_state,
            scene_state_digest=self.scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        return self


class MotionStageResult(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-result/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["motion"]
    step_receipt: StepReceipt
    step_receipt_digest: Sha256
    contribution: SceneContribution
    predecessor_barriers: tuple[StageBarrierDigest, ...] = ()

    @model_validator(mode="after")
    def canonical_motion_result(self) -> "MotionStageResult":
        _validate_stage_result_bindings(
            run_id=self.run_id,
            scenario_digest=self.scenario_digest,
            target=self.target,
            stage=self.stage,
            provider_id=self.provider_id,
            step_receipt=self.step_receipt,
            step_receipt_digest=self.step_receipt_digest,
            contribution=self.contribution,
            input_scene_state_digest=None,
            predecessor_barriers=self.predecessor_barriers,
        )
        return self


class NetworkStageResult(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-result/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["network"]
    step_receipt: StepReceipt
    step_receipt_digest: Sha256
    contribution: SceneContribution
    input_scene_state_digest: Sha256
    predecessor_barriers: tuple[StageBarrierDigest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_network_result(self) -> "NetworkStageResult":
        _validate_stage_result_bindings(
            run_id=self.run_id,
            scenario_digest=self.scenario_digest,
            target=self.target,
            stage=self.stage,
            provider_id=self.provider_id,
            step_receipt=self.step_receipt,
            step_receipt_digest=self.step_receipt_digest,
            contribution=self.contribution,
            input_scene_state_digest=self.input_scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        return self


class BusinessEnvironmentStageResult(StrictModel):
    schema_version: Literal["aero-bench.provider-stage-result/v1"]
    run_id: Sha256
    scenario_digest: Sha256
    provider_id: Identifier
    target: SimulationTime
    stage: Literal["business_environment"]
    step_receipt: StepReceipt
    step_receipt_digest: Sha256
    contribution: SceneContribution
    input_scene_state_digest: Sha256
    predecessor_barriers: tuple[StageBarrierDigest, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def canonical_business_environment_result(
        self,
    ) -> "BusinessEnvironmentStageResult":
        _validate_stage_result_bindings(
            run_id=self.run_id,
            scenario_digest=self.scenario_digest,
            target=self.target,
            stage=self.stage,
            provider_id=self.provider_id,
            step_receipt=self.step_receipt,
            step_receipt_digest=self.step_receipt_digest,
            contribution=self.contribution,
            input_scene_state_digest=self.input_scene_state_digest,
            predecessor_barriers=self.predecessor_barriers,
        )
        return self


ProviderStageRequest: TypeAlias = Annotated[
    MotionStageRequest | NetworkStageRequest | BusinessEnvironmentStageRequest,
    Field(discriminator="stage"),
]
ProviderStageResult: TypeAlias = Annotated[
    MotionStageResult | NetworkStageResult | BusinessEnvironmentStageResult,
    Field(discriminator="stage"),
]


def step_receipt_digest_value(value: StepReceipt) -> str:
    return hashlib.sha256(canonical_json_bytes(value.model_dump(mode="json"))).hexdigest()


def state_sample_digest_value(value: StateSample) -> str:
    return hashlib.sha256(_digest_document(value, "sample_digest")).hexdigest()


def scene_contribution_payload_digest_value(value: SceneContribution) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "attribute_updates": [
                    update.model_dump(mode="json")
                    for update in value.attribute_updates
                ],
                "samples": [sample.model_dump(mode="json") for sample in value.samples],
            }
        )
    ).hexdigest()


def scene_contribution_digest_value(value: SceneContribution) -> str:
    return hashlib.sha256(
        _digest_document(value, "contribution_digest")
    ).hexdigest()


def stage_receipt_digest_value(value: StageReceipt) -> str:
    return hashlib.sha256(_digest_document(value, "receipt_digest")).hexdigest()


def stage_barrier_digest_value(value: StageBarrier) -> str:
    return hashlib.sha256(_digest_document(value, "barrier_digest")).hexdigest()


def scene_state_digest_value(value: SceneState) -> str:
    return hashlib.sha256(_digest_document(value, "scene_state_digest")).hexdigest()
