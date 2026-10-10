from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from aero_bench.artifacts.contracts import SealManifest
from aero_bench.config.models import (
    AgentSpec,
    ArtifactRequirement,
    ClockSpec,
    FileRef,
    FeasibilityAssessment,
    GatewaySpec,
    GoalSpec,
    Identifier,
    ImplementationIdentity,
    ObservationGrant,
    ProviderRef,
    QueryGrant,
    RuntimeSpec,
    Sha256,
    StrictModel,
    ToolGrant,
    TaskPackageRef,
    VerifierSpec,
)
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.verifier.output import ValidatedVerificationOutput
from aero_bench.world.resolved import (
    ResolvedAsset,
    ResolvedScenario,
    scenario_assets_for_workload,
)


def _relative_input_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or value.strip() != value
        or "\\" in value
    ):
        raise ValueError("input destination must be a normalized relative path")
    return value


class BundleInputPlan(StrictModel):
    source: FileRef
    destination: Annotated[str, Field(min_length=1)]
    size_bytes: Annotated[int, Field(gt=0)]

    _validate_destination = field_validator("destination")(_relative_input_path)


class InlineInputPlan(StrictModel):
    destination: Annotated[str, Field(min_length=1)]
    content_utf8: str
    sha256: Sha256

    _validate_destination = field_validator("destination")(_relative_input_path)

    @model_validator(mode="after")
    def content_matches_digest(self) -> "InlineInputPlan":
        actual = hashlib.sha256(self.content_utf8.encode("utf-8")).hexdigest()
        if actual != self.sha256:
            raise ValueError("inline input digest does not match content")
        return self


class ProviderRuntimeManifest(StrictModel):
    provider_id: Identifier
    adapter: Identifier
    implementation: ImplementationIdentity
    runtime_image: Annotated[
        str,
        Field(pattern=(r"^(?:[^@\s]+@sha256:[0-9a-f]{64}|sha256:[0-9a-f]{64})$")),
    ]
    config_digest: Sha256
    port: Annotated[int, Field(ge=1024, le=65535)]
    capabilities: tuple[Identifier, ...]
    protocol_schema: FileRef
    artifact_requirements: tuple[ArtifactRequirement, ...]


class AgentRuntimeManifest(StrictModel):
    agent_id: Identifier
    tools: tuple[ToolGrant, ...]
    queries: tuple[QueryGrant, ...]
    observations: tuple[ObservationGrant, ...]
    artifact_requirements: tuple[ArtifactRequirement, ...]


class HarnessWorkloadContract(StrictModel):
    schema_version: Literal["aero-bench.harness-workload-contract/v6"]
    role: Literal["harness"]
    workload_id: Literal["harness"]
    run: ResolvedRunSpec
    scenario_digest: Sha256
    scenario_assets: tuple[ResolvedAsset, ...]

    @classmethod
    def from_run(cls, run: ResolvedRunSpec) -> "HarnessWorkloadContract":
        return cls(
            schema_version="aero-bench.harness-workload-contract/v6",
            role="harness",
            workload_id="harness",
            run=run,
            scenario_digest=run.scenario.scenario_digest,
            scenario_assets=scenario_assets_for_workload(
                run.scenario,
                role="harness",
                workload_id="harness",
            ),
        )

    @model_validator(mode="after")
    def scenario_projection_matches_run(self) -> "HarnessWorkloadContract":
        if (
            self.scenario_digest != self.run.scenario.scenario_digest
            or self.scenario_assets
            != scenario_assets_for_workload(
                self.run.scenario,
                role="harness",
                workload_id=self.workload_id,
            )
        ):
            raise ValueError("Harness scenario projection differs from ResolvedRun")
        return self


class ProviderWorkloadContract(StrictModel):
    schema_version: Literal["aero-bench.workload-contract/v5"]
    role: Literal["provider"]
    run_id: Sha256
    seed: int
    workload_id: Identifier
    clock: ClockSpec
    provider: ProviderRef
    scenario_digest: Sha256
    scenario: ResolvedScenario
    scenario_assets: tuple[ResolvedAsset, ...]

    @model_validator(mode="after")
    def scenario_projection_matches_provider(self) -> "ProviderWorkloadContract":
        scenario_provider = next(
            (
                item
                for item in self.scenario.providers
                if item.provider_id == self.provider.provider_id
            ),
            None,
        )
        if (
            self.workload_id != self.provider.provider_id
            or self.seed != self.scenario.seed
            or self.scenario_digest != self.scenario.scenario_digest
            or scenario_provider is None
            or scenario_provider.capability_ids
            != tuple(sorted(self.provider.capabilities))
            or self.scenario_assets
            != scenario_assets_for_workload(
                self.scenario,
                role="provider",
                workload_id=self.workload_id,
            )
        ):
            raise ValueError("Provider scenario projection differs from its contract")
        return self


class AgentWorkloadContract(StrictModel):
    schema_version: Literal["aero-bench.workload-contract/v5"]
    role: Literal["agent"]
    run_id: Sha256
    seed: int
    clock: ClockSpec
    workload_id: Identifier
    task_id: Identifier
    instruction: FileRef
    gateway: GatewaySpec
    agent: AgentSpec
    scenario_digest: Sha256
    scenario: ResolvedScenario
    scenario_assets: tuple[ResolvedAsset, ...]

    @model_validator(mode="after")
    def scenario_projection_matches_agent(self) -> "AgentWorkloadContract":
        if (
            self.workload_id != self.agent.agent_id
            or self.seed != self.scenario.seed
            or self.scenario_digest != self.scenario.scenario_digest
            or self.scenario_assets
            != scenario_assets_for_workload(
                self.scenario,
                role="agent",
                workload_id=self.workload_id,
            )
        ):
            raise ValueError("Agent scenario projection differs from its contract")
        return self


class VerifierWorkloadContract(StrictModel):
    schema_version: Literal["aero-bench.workload-contract/v5"]
    role: Literal["verifier"]
    run_id: Sha256
    seed: int
    workload_id: Identifier
    task_id: Identifier
    run: ResolvedRunSpec
    package: TaskPackageRef
    feasibility: FeasibilityAssessment
    goals: tuple[GoalSpec, ...]
    verifier: VerifierSpec
    providers: tuple[ProviderRuntimeManifest, ...]
    scenario_digest: Sha256
    scenario_assets: tuple[ResolvedAsset, ...]
    sealed_artifacts: tuple[ArtifactRequirement, ...]

    @model_validator(mode="after")
    def projections_match_resolved_run(self) -> "VerifierWorkloadContract":
        if (
            self.run_id != self.run.run_id
            or self.seed != self.run.seed
            or self.task_id != self.run.task.task_id
            or self.workload_id != self.run.task.verifier.verifier_id
            or self.package != self.run.task.package
            or self.feasibility != self.run.feasibility
            or self.goals != self.run.task.goals
            or self.verifier != self.run.task.verifier
            or self.scenario_digest != self.run.scenario.scenario_digest
            or self.sealed_artifacts != self.run.artifact_requirements
        ):
            raise ValueError("Verifier workload projections differ from ResolvedRun")
        expected_providers = tuple(
            ProviderRuntimeManifest(
                provider_id=provider.provider_id,
                adapter=provider.adapter,
                implementation=provider.workload.implementation,
                runtime_image=provider.workload.runtime.image,
                config_digest=provider.config.file.sha256,
                port=provider.port,
                capabilities=provider.capabilities,
                protocol_schema=provider.protocol_schema,
                artifact_requirements=provider.artifact_requirements,
            )
            for provider in self.run.environment.providers
        )
        if self.providers != expected_providers:
            raise ValueError("Verifier provider inputs differ from ResolvedRun")
        if self.scenario_assets != scenario_assets_for_workload(
            self.run.scenario,
            role="verifier",
            workload_id=self.workload_id,
        ):
            raise ValueError("Verifier scenario assets differ from ResolvedRun")
        return self


class AgentDriverWorkloadContract(StrictModel):
    schema_version: Literal["aero-bench.agent-driver-workload-contract/v2"]
    role: Literal["agent_driver"]
    workload_id: Identifier
    context: AgentWorkloadContract

    @model_validator(mode="after")
    def driver_matches_agent(self) -> "AgentDriverWorkloadContract":
        if self.context.agent.driver is None:
            raise ValueError(
                "driver workload requires an explicitly declared Agent driver"
            )
        if self.workload_id != self.context.agent.driver.driver_id:
            raise ValueError("driver workload identity differs from AgentSpec")
        return self


class LogisticsWorldPlan(StrictModel):
    """Formal executor-materialized logistics world identity.

    ``LogisticsTaskPackage.facilities`` splice into the declared base world SDF
    and yield the deterministic ``world_sha256`` content the executor stages as
    a derived input to the ``px4.gazebo`` Provider workload.  The record binds
    every relevant digest to the plan so an immutable ResolvedRun produces an
    auditable materialization without mounting any host path into a workload.
    """

    schema_version: Literal["aero-bench.logistics-world-plan/v1"]
    destination: str
    world_sha256: Sha256
    world_size_bytes: int
    package_sha256: Sha256
    base_world_sha256: Sha256
    scene_origin_sha256: Sha256
    facility_ids: tuple[Identifier, ...]
    facility_pad_count: int
    provider_id: Identifier


class WorkloadPlan(StrictModel):
    workload_id: Identifier
    role: Literal["harness", "provider", "agent", "agent_driver", "verifier"]
    phase: Literal["runtime", "verification"]
    workload: RuntimeSpec
    contract: InlineInputPlan
    bundle_inputs: tuple[BundleInputPlan, ...]
    #: Content-derived inputs (verified by SHA-256) staged into the workload
    #: input volume alongside pinned bundle inputs.  Examples: the deterministic
    #: facility-spliced world SDF.  No host path is ever mounted.
    derived_inputs: tuple[InlineInputPlan, ...] = ()


class ExecutionPlan(StrictModel):
    run: ResolvedRunSpec
    executor_kind: Literal["docker_reference", "kubernetes_cluster"]
    bundle_root: str
    runtime_workloads: tuple[WorkloadPlan, ...]
    verifier_workload: WorkloadPlan
    #: Formal identity of the executor-materialized logistics facility world, if
    #: the run's declared TaskSpec package is a logistics package.
    logistics_world: LogisticsWorldPlan | None = None

    @field_validator("bundle_root")
    @classmethod
    def absolute_bundle_root(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("bundle_root must be absolute")
        return value


class PreflightBlocker(StrictModel):
    code: Identifier
    detail: str


class PreflightReport(StrictModel):
    run_id: str
    executor_kind: Literal["docker_reference", "kubernetes_cluster"]
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    ready: bool
    blockers: tuple[PreflightBlocker, ...]


@runtime_checkable
class Executor(Protocol):
    def materialize(
        self, run: ResolvedRunSpec, *, bundle_root: str | Path
    ) -> ExecutionPlan: ...

    def preflight(self, plan: ExecutionPlan) -> PreflightReport: ...

    def start_runtime(self, plan: ExecutionPlan) -> object: ...

    def wait_runtime(self, handle: object, *, timeout_seconds: int) -> None: ...

    def collect_failure_outputs(
        self,
        plan: ExecutionPlan,
        handle: object,
        *,
        destination_root: Path,
    ) -> None: ...

    def collect_and_seal(
        self,
        plan: ExecutionPlan,
        handle: object,
        *,
        destination_root: Path,
    ) -> SealManifest: ...

    def start_verifier(self, plan: ExecutionPlan, seal: SealManifest) -> object: ...

    def wait_verifier(self, handle: object, *, timeout_seconds: int) -> None: ...

    def collect_verification_outputs(
        self,
        plan: ExecutionPlan,
        handle: object,
        *,
        destination_root: Path,
    ) -> ValidatedVerificationOutput: ...

    def cleanup(self, handle: object) -> None: ...

    def cleanup_run(self, run_id: str) -> None: ...
