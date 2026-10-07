from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SceneState, SimulationTime
from aero_bench.runtime.events import RunEvent, RunEventAudience


RuntimeControlOperation: TypeAlias = Literal[
    "status",
    "pause",
    "resume",
    "step",
    "stop",
]
RuntimeControlPhase: TypeAlias = Literal[
    "starting",
    "running",
    "pausing",
    "paused",
    "stepping",
    "stopping",
    "completed",
    "aborted",
    "failed",
]


class RuntimeControlStatus(StrictModel):
    schema_version: Literal["aero-bench.runtime-control-status/v1"]
    run_id: Sha256
    phase: RuntimeControlPhase
    current: SimulationTime
    tick_in_progress: bool
    step_budget: Annotated[int, Field(ge=0, le=1)]
    event_chain_root: Sha256
    latest_event_id: Identifier | None

    @model_validator(mode="after")
    def phase_matches_runtime_state(self) -> "RuntimeControlStatus":
        if self.phase == "stepping" and not self.tick_in_progress:
            raise ValueError("stepping control phase requires a tick in progress")
        if self.phase in {"starting", "paused"} and self.tick_in_progress:
            raise ValueError(f"{self.phase} control phase cannot have a tick in progress")
        if self.step_budget and self.phase != "paused":
            raise ValueError("a queued single step requires paused control phase")
        if self.phase in {"completed", "aborted", "failed"} and (
            self.tick_in_progress or self.step_budget
        ):
            raise ValueError("terminal control phase cannot retain tick work")
        return self


class RuntimeControlReceipt(StrictModel):
    schema_version: Literal["aero-bench.runtime-control-receipt/v1"]
    operation: RuntimeControlOperation
    control_id: Identifier | None
    status: RuntimeControlStatus
    audit_event_id: Identifier | None

    @model_validator(mode="after")
    def audit_matches_operation(self) -> "RuntimeControlReceipt":
        if self.operation == "status" and (
            self.control_id is not None or self.audit_event_id is not None
        ):
            raise ValueError("runtime status query cannot invent control identity")
        if self.operation != "status" and (
            self.control_id is None or self.audit_event_id is None
        ):
            raise ValueError("runtime state-changing control requires audited identity")
        return self


class RuntimeProjectionBatch(StrictModel):
    schema_version: Literal["aero-bench.runtime-projection-batch/v1"]
    run_id: Sha256
    after_scene_tick: Annotated[int, Field(ge=0)]
    after_event_sequence: Annotated[int, Field(ge=-1)]
    next_scene_tick: Annotated[int, Field(ge=0)]
    next_event_sequence: Annotated[int, Field(ge=-1)]
    event_chain_root: Sha256
    scene_states: tuple[SceneState, ...]
    events: tuple[RunEvent, ...]
    runtime_terminal: bool
    has_more: bool

    @model_validator(mode="after")
    def canonical_public_batch(self) -> "RuntimeProjectionBatch":
        if self.next_scene_tick < self.after_scene_tick:
            raise ValueError("runtime projection SceneState cursor moved backwards")
        if self.next_event_sequence < self.after_event_sequence:
            raise ValueError("runtime projection RunEvent cursor moved backwards")
        for offset, state in enumerate(self.scene_states, start=1):
            if state.run_id != self.run_id:
                raise ValueError("runtime projection SceneState belongs to another run")
            if state.at.tick != self.after_scene_tick + offset:
                raise ValueError(
                    "runtime projection SceneStates must be contiguous after the cursor"
                )
        if self.scene_states:
            if self.next_scene_tick != self.scene_states[-1].at.tick:
                raise ValueError("runtime projection SceneState cursor is inconsistent")
        elif self.next_scene_tick != self.after_scene_tick:
            raise ValueError("empty runtime projection changed the SceneState cursor")
        event_sequences = tuple(event.sequence for event in self.events)
        if event_sequences != tuple(sorted(set(event_sequences))):
            raise ValueError("runtime projection RunEvents must be sorted and unique")
        public = RunEventAudience(scope="public", audience_id=None)
        for event in self.events:
            if event.run_id != self.run_id or event.sequence <= self.after_event_sequence:
                raise ValueError("runtime projection RunEvent cursor is inconsistent")
            if public not in event.visibility:
                raise ValueError("runtime projection contains a non-public RunEvent")
        if self.events and self.next_event_sequence < self.events[-1].sequence:
            raise ValueError("runtime projection RunEvent cursor is inconsistent")
        if self.has_more and not (self.scene_states or self.events):
            raise ValueError("runtime projection cannot report empty pagination work")
        return self


__all__ = [
    "RuntimeControlOperation",
    "RuntimeControlPhase",
    "RuntimeControlReceipt",
    "RuntimeControlStatus",
    "RuntimeProjectionBatch",
]
