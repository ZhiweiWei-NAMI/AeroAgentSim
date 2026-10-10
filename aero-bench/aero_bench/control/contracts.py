from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StrictStr, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.control import RuntimeControlOperation, RuntimeControlReceipt
from aero_bench.runtime.contracts import SceneState
from aero_bench.trace.contracts import PublicRunEvent, PublicScenario
from aero_bench.runner.session import RunExecutionSnapshot, RunExecutionTransition


_CONTROL_TOKEN_PATTERN = r"^[0-9a-f]{64}$"


class CatalogRun(StrictModel):
    run_id: Sha256
    suite_id: Identifier
    case_id: Identifier
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    world_id: Identifier
    scenario_digest: Sha256
    task_id: Identifier
    seed: Annotated[int, Field(ge=0)]
    launch_site_id: Identifier
    matrix_axis_ids: tuple[Identifier, ...]

    @model_validator(mode="after")
    def canonical_matrix_axes(self) -> "CatalogRun":
        if self.matrix_axis_ids != tuple(sorted(set(self.matrix_axis_ids))):
            raise ValueError("catalog matrix axes must be sorted and unique")
        return self


class ControlCatalog(StrictModel):
    schema_version: Literal["aero-bench.control-catalog/v1"]
    suite_id: Identifier
    suite_sha256: Sha256
    runs: tuple[CatalogRun, ...]

    @model_validator(mode="after")
    def canonical_runs(self) -> "ControlCatalog":
        run_ids = tuple(run.run_id for run in self.runs)
        if run_ids != tuple(sorted(set(run_ids))):
            raise ValueError("catalog runs must be sorted and unique")
        if any(run.suite_id != self.suite_id for run in self.runs):
            raise ValueError("catalog run belongs to another suite")
        return self


class StartRunRequest(StrictModel):
    schema_version: Literal["aero-bench.start-run-request/v1"]
    start_id: Identifier
    run_id: Sha256


class RunAccessCredentials(StrictModel):
    schema_version: Literal["aero-bench.run-access-credentials/v1"]
    run_id: Sha256
    operator_token: Annotated[
        StrictStr,
        Field(pattern=_CONTROL_TOKEN_PATTERN, repr=False),
    ]
    csrf_token: Annotated[
        StrictStr,
        Field(pattern=_CONTROL_TOKEN_PATTERN, repr=False),
    ]

    @model_validator(mode="after")
    def non_placeholder_credentials(self) -> "RunAccessCredentials":
        if (
            self.operator_token == "0" * 64
            or self.csrf_token == "0" * 64
            or self.operator_token == self.csrf_token
        ):
            raise ValueError("run access credentials are invalid")
        return self


class ReplayAccessRequest(StrictModel):
    schema_version: Literal["aero-bench.replay-access-request/v1"]


class ReplayAccessResponse(StrictModel):
    schema_version: Literal["aero-bench.replay-access-response/v1"]
    credentials: RunAccessCredentials
    read_only: Literal[True]


class StartRunResponse(StrictModel):
    schema_version: Literal["aero-bench.start-run-response/v1"]
    run_id: Sha256
    credentials: RunAccessCredentials
    snapshot: RunExecutionSnapshot
    scenario: PublicScenario

    @model_validator(mode="after")
    def identities_match(self) -> "StartRunResponse":
        if (
            self.credentials.run_id != self.run_id
            or self.snapshot.run_id != self.run_id
        ):
            raise ValueError("start response identities disagree")
        return self


class RunStatusResponse(StrictModel):
    schema_version: Literal["aero-bench.run-status-response/v1"]
    snapshot: RunExecutionSnapshot
    management_failure_classes: tuple[Identifier, ...]


class RuntimeControlRequest(StrictModel):
    schema_version: Literal["aero-bench.runtime-control-request/v1"]
    control_id: Identifier


class RuntimeControlResponse(StrictModel):
    schema_version: Literal["aero-bench.runtime-control-response/v1"]
    receipt: RuntimeControlReceipt
    snapshot: RunExecutionSnapshot

    @model_validator(mode="after")
    def identities_match(self) -> "RuntimeControlResponse":
        if self.receipt.status.run_id != self.snapshot.run_id:
            raise ValueError("runtime control response identities disagree")
        if self.receipt.operation == "status":
            raise ValueError("state-changing response cannot contain status operation")
        return self


class RunTransitionEvent(StrictModel):
    schema_version: Literal["aero-bench.run-transition-event/v1"]
    run_id: Sha256
    transition: RunExecutionTransition

    @model_validator(mode="after")
    def identity_matches(self) -> "RunTransitionEvent":
        if self.transition.run_id != self.run_id:
            raise ValueError("transition event belongs to another run")
        return self


class SceneStateStreamEvent(StrictModel):
    schema_version: Literal["aero-bench.scene-state-stream-event/v1"]
    run_id: Sha256
    scene_state: SceneState

    @model_validator(mode="after")
    def identity_matches(self) -> "SceneStateStreamEvent":
        if self.scene_state.run_id != self.run_id:
            raise ValueError("streamed SceneState belongs to another run")
        return self


class PublicRunEventStreamEvent(StrictModel):
    schema_version: Literal["aero-bench.public-run-event-stream-event/v1"]
    run_id: Sha256
    event: PublicRunEvent

    @model_validator(mode="after")
    def identity_matches(self) -> "PublicRunEventStreamEvent":
        if self.event.run_id != self.run_id:
            raise ValueError("streamed public RunEvent belongs to another run")
        return self


ControlStreamEvent: TypeAlias = (
    RunTransitionEvent | SceneStateStreamEvent | PublicRunEventStreamEvent
)


class ControlApiError(StrictModel):
    code: Identifier
    detail: str


class ControlApiErrorResponse(StrictModel):
    schema_version: Literal["aero-bench.control-error-response/v1"]
    error: ControlApiError


CONTROL_MUTATIONS: tuple[RuntimeControlOperation, ...] = (
    "pause",
    "resume",
    "step",
    "stop",
)


__all__ = [
    "CONTROL_MUTATIONS",
    "CatalogRun",
    "ControlApiError",
    "ControlApiErrorResponse",
    "ControlCatalog",
    "ControlStreamEvent",
    "PublicRunEventStreamEvent",
    "ReplayAccessRequest",
    "ReplayAccessResponse",
    "RunAccessCredentials",
    "RunStatusResponse",
    "RunTransitionEvent",
    "RuntimeControlRequest",
    "RuntimeControlResponse",
    "SceneStateStreamEvent",
    "StartRunRequest",
    "StartRunResponse",
]
