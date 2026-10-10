from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, model_validator

from aero_bench.config.loader import BundleReader, load_suite
from aero_bench.config.models import (
    AgentSpec,
    ArtifactRequirement,
    EnvironmentSpec,
    FeasibilityAssessment,
    FileRef,
    Identifier,
    MatrixAxis,
    MatrixValue,
    StrictModel,
    SuiteSpec,
    TaskSpec,
)
from aero_bench.providers.registry import ProviderRegistry
from aero_bench.providers.stages import ProviderStage
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.contracts import WorldPackage
from aero_bench.world.resolved import (
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)


ExecutorKind = Literal["docker_reference", "kubernetes_cluster"]


class AppliedOverride(StrictModel):
    axis_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    target: str = Field(pattern=r"^/(task|environment|agents)(?:/[A-Za-z0-9_~.-]+)+$")
    value: MatrixValue


class ResolvedRunSpec(StrictModel):
    schema_version: Literal["aero-bench.resolved-run/v5"]
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    executor_kind: Literal["docker_reference", "kubernetes_cluster"]
    execution_scope: Literal["executor_validation", "formal_benchmark"]
    suite: FileRef
    suite_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    launch_site_id: Identifier | None
    seed: Annotated[
        int,
        Field(strict=True, ge=0, le=9_223_372_036_854_775_807),
    ]
    scenario: ResolvedScenario
    task: TaskSpec
    environment: EnvironmentSpec
    agents: tuple[AgentSpec, ...]
    feasibility: FeasibilityAssessment
    artifact_requirements: tuple[ArtifactRequirement, ...]
    verification_outputs: tuple[ArtifactRequirement, ...]
    overrides: tuple[AppliedOverride, ...]

    @model_validator(mode="after")
    def validate_contracts(self) -> "ResolvedRunSpec":
        if self.launch_site_id is None and self.task.package.package_id != "logistics.arrivals.v1":
            raise ValueError("only logistics.arrivals.v1 permits a no-launch run")
        if self.scenario.seed != self.seed:
            raise ValueError("ResolvedScenario seed must equal ResolvedRun seed")
        if self.scenario.selected_launch_site_id != self.launch_site_id:
            raise ValueError(
                "ResolvedScenario selected launch must equal ResolvedRun launch_site_id"
            )
        if (
            self.scenario.task.task_id != self.task.task_id
            or self.scenario.task.package_id != self.task.package.package_id
            or self.scenario.task.package_config != self.task.package.config.file
            or self.scenario.task.package_schema != self.task.package.config.schema_file
            or self.scenario.task.instruction != self.task.instruction
            or self.scenario.task.verifier_id != self.task.verifier.verifier_id
        ):
            raise ValueError("ResolvedScenario task binding differs from ResolvedRun")
        if (
            self.scenario.task.required_capability_ids
            != tuple(sorted(self.task.required_capabilities))
            or self.scenario.task.required_tool_ids
            != tuple(sorted(self.task.required_tools))
            or self.scenario.task.asset_ids
            != tuple(sorted(asset.asset_id for asset in self.task.assets))
            or self.scenario.task.goals
            != tuple(sorted(self.task.goals, key=lambda goal: goal.goal_id))
        ):
            raise ValueError(
                "ResolvedScenario task requirements differ from ResolvedRun task"
            )
        expected_tool_bindings = tuple(
            sorted(
                (
                    {
                        "binding_id": f"{agent.agent_id}.{grant.tool_id}",
                        "agent_id": agent.agent_id,
                        "tool_id": grant.tool_id,
                        "endpoint_id": grant.provider_id,
                        "request_schema": grant.request_schema.model_dump(mode="json"),
                        "response_schema": grant.response_schema.model_dump(
                            mode="json"
                        ),
                        "timeout_ms": grant.timeout_ms,
                        "idempotent": grant.idempotent,
                    }
                    for agent in self.agents
                    for grant in agent.tools
                ),
                key=lambda binding: binding["binding_id"],
            )
        )
        actual_tool_bindings = tuple(
            binding.model_dump(mode="json") for binding in self.scenario.task.tools
        )
        if actual_tool_bindings != expected_tool_bindings:
            raise ValueError(
                "ResolvedScenario task tool bindings differ from AgentSpec"
            )
        expected_observation_bindings = tuple(
            sorted(
                (
                    {
                        "binding_id": f"{agent.agent_id}.{grant.observation_id}",
                        "agent_id": agent.agent_id,
                        "observation_id": grant.observation_id,
                        "endpoint_id": grant.provider_id,
                        "schema_file": grant.schema_file.model_dump(mode="json"),
                        "timeout_ms": grant.timeout_ms,
                    }
                    for agent in self.agents
                    for grant in agent.observations
                ),
                key=lambda binding: binding["binding_id"],
            )
        )
        actual_observation_bindings = tuple(
            binding.model_dump(mode="json", exclude={"inspection", "urban", "flight"})
            for binding in self.scenario.task.observations
        )
        if actual_observation_bindings != expected_observation_bindings:
            raise ValueError(
                "ResolvedScenario task observation bindings differ from AgentSpec"
            )
        expected_query_bindings = tuple(
            sorted(
                (
                    {
                        "binding_id": f"{agent.agent_id}.{grant.query_type}",
                        "agent_id": agent.agent_id,
                        "query_type": grant.query_type,
                        "endpoint_id": grant.provider_id,
                        "request_schema": grant.request_schema.model_dump(mode="json"),
                        "response_schema": grant.response_schema.model_dump(
                            mode="json"
                        ),
                        "timeout_ms": grant.timeout_ms,
                    }
                    for agent in self.agents
                    for grant in agent.queries
                ),
                key=lambda binding: binding["binding_id"],
            )
        )
        actual_query_bindings = tuple(
            binding.model_dump(mode="json") for binding in self.scenario.task.queries
        )
        if actual_query_bindings != expected_query_bindings:
            raise ValueError(
                "ResolvedScenario task query bindings differ from AgentSpec"
            )
        expected_task_digest = _digest(
            {
                "task": self.task.model_dump(mode="json"),
                "agents": [agent.model_dump(mode="json") for agent in self.agents],
            }
        )
        if self.scenario.task.task_contract_digest != expected_task_digest:
            raise ValueError(
                "ResolvedScenario task contract digest differs from run inputs"
            )
        scenario_capabilities = {
            provider.provider_id: provider.capability_ids
            for provider in self.scenario.providers
        }
        environment_capabilities = {
            provider.provider_id: tuple(sorted(provider.capabilities))
            for provider in self.environment.providers
        }
        if scenario_capabilities != environment_capabilities:
            raise ValueError(
                "ResolvedScenario provider capabilities must equal the resolved environment"
            )
        workloads = (
            self.environment.harness,
            *(provider.workload for provider in self.environment.providers),
            *(agent.workload for agent in self.agents),
            *(
                agent.driver.workload
                for agent in self.agents
                if agent.driver is not None
            ),
            self.task.verifier.workload,
        )
        local_image_workloads = tuple(
            workload for workload in workloads if workload.runtime.is_local_id
        )
        if local_image_workloads and self.execution_scope != "executor_validation":
            raise ValueError(
                "local Docker image IDs are allowed only for executor_validation"
            )
        if local_image_workloads and self.executor_kind != "docker_reference":
            raise ValueError(
                "local Docker image IDs require the docker_reference executor"
            )
        required_kind = (
            "production"
            if self.execution_scope == "formal_benchmark" or local_image_workloads
            else "mechanical_fixture"
        )
        if any(workload.implementation.kind != required_kind for workload in workloads):
            raise ValueError(
                f"{self.execution_scope} requires every workload implementation kind "
                f"to be {required_kind}"
            )
        if self.feasibility.package_id != self.task.package.package_id:
            raise ValueError("feasibility package_id must match TaskSpec package_id")

        derived_artifacts = _derive_artifact_requirements(
            self.task, self.environment, self.agents
        )
        if self.artifact_requirements != derived_artifacts:
            raise ValueError(
                "artifact_requirements must be derived from resolved producer and "
                "Verifier contracts"
            )
        if self.verification_outputs != self.task.verifier.output_artifacts:
            raise ValueError(
                "verification_outputs must equal the resolved Verifier output contract"
            )

        canonical_payload = self.model_dump(mode="json", exclude={"run_id"})
        if self.run_id != _digest(canonical_payload):
            raise ValueError("run_id does not match the canonical ResolvedRun payload")

        provider_capabilities = {
            capability
            for provider in self.environment.providers
            for capability in provider.capabilities
        }
        missing_physical_capabilities = (
            set(self.task.required_capabilities) - provider_capabilities
        )
        if (
            missing_physical_capabilities
            and not self.scenario.task.logical_endpoint_ids
        ):
            raise ValueError(
                "environment misses capabilities and the task declares no logical endpoint: "
                f"{sorted(missing_physical_capabilities)}"
            )

        granted_tools = {tool.tool_id for agent in self.agents for tool in agent.tools}
        missing_tools = set(self.task.required_tools) - granted_tools
        if missing_tools:
            raise ValueError(f"agents miss required tools: {sorted(missing_tools)}")

        provider_ids = {provider.provider_id for provider in self.environment.providers}
        endpoint_ids = provider_ids | set(self.scenario.task.logical_endpoint_ids)
        unknown_tool_providers = {
            grant.provider_id
            for agent in self.agents
            for grant in agent.tools
            if grant.provider_id not in endpoint_ids
        }
        unknown_observation_providers = {
            grant.provider_id
            for agent in self.agents
            for grant in agent.observations
            if grant.provider_id not in endpoint_ids
        }
        unknown_query_providers = {
            grant.provider_id
            for agent in self.agents
            for grant in agent.queries
            if grant.provider_id not in endpoint_ids
        }
        if (
            unknown_tool_providers
            or unknown_query_providers
            or unknown_observation_providers
        ):
            raise ValueError(
                "agent grants name undeclared physical or task endpoints: "
                f"tools={sorted(unknown_tool_providers)}, "
                f"queries={sorted(unknown_query_providers)}, "
                f"observations={sorted(unknown_observation_providers)}"
            )

        agent_ids = [agent.agent_id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("agent_id values must be unique")
        provider_ids = [provider.provider_id for provider in self.environment.providers]
        driver_ids = [
            agent.driver.driver_id for agent in self.agents if agent.driver is not None
        ]
        workload_identities = [
            "harness",
            "bundle",
            *provider_ids,
            *agent_ids,
            *driver_ids,
        ]
        if len(workload_identities) != len(set(workload_identities)):
            raise ValueError(
                "runtime workload and reserved producer ids must be disjoint"
            )
        if self.task.verifier.verifier_id in set(workload_identities):
            raise ValueError("verifier_id must be disjoint from runtime producer ids")

        verifier_runtime = self.task.verifier.workload.runtime
        verifier_image_identity = (
            "local_id" if verifier_runtime.is_local_id else "repository_digest",
            verifier_runtime.immutable_digest,
        )
        runtime_image_identities = {
            (
                "local_id"
                if self.environment.harness.runtime.is_local_id
                else "repository_digest",
                self.environment.harness.runtime.immutable_digest,
            ),
            *(
                (
                    "local_id"
                    if provider.workload.runtime.is_local_id
                    else "repository_digest",
                    provider.workload.runtime.immutable_digest,
                )
                for provider in self.environment.providers
            ),
            *(
                (
                    "local_id"
                    if agent.workload.runtime.is_local_id
                    else "repository_digest",
                    agent.workload.runtime.immutable_digest,
                )
                for agent in self.agents
            ),
            *(
                (
                    "local_id"
                    if agent.driver.workload.runtime.is_local_id
                    else "repository_digest",
                    agent.driver.workload.runtime.immutable_digest,
                )
                for agent in self.agents
                if agent.driver is not None
            ),
        }
        if verifier_image_identity in runtime_image_identities:
            raise ValueError(
                "verifier image digest must be distinct from every runtime workload "
                "image digest"
            )

        workload_ids = {
            "agent": {agent.agent_id for agent in self.agents},
            "provider": {
                provider.provider_id for provider in self.environment.providers
            },
            "verifier": {self.task.verifier.verifier_id},
        }
        for asset in self.task.assets:
            for audience in asset.audiences:
                unknown = set(audience.workload_ids) - workload_ids[audience.role]
                if unknown:
                    raise ValueError(
                        f"asset {asset.asset_id} names undeclared {audience.role} workloads: "
                        f"{sorted(unknown)}"
                    )

        runtime_producers = {
            "harness",
            *(provider.provider_id for provider in self.environment.providers),
            *(agent.agent_id for agent in self.agents),
            *(
                agent.driver.driver_id
                for agent in self.agents
                if agent.driver is not None
            ),
        }
        if any(
            requirement.producer_id not in runtime_producers | {"bundle"}
            for requirement in self.artifact_requirements
        ):
            raise ValueError("sealed artifact requirement names an unknown producer")
        assets_by_id = {asset.asset_id: asset for asset in self.task.assets}
        for requirement in self.artifact_requirements:
            if requirement.producer_id != "bundle":
                continue
            asset = assets_by_id.get(requirement.source_asset_id or "")
            if asset is None:
                raise ValueError("bundle artifact names an undeclared source asset")
            expected_audiences = (
                (
                    "verifier",
                    (self.task.verifier.verifier_id,),
                ),
            )
            actual_audiences = tuple(
                (audience.role, audience.workload_ids) for audience in asset.audiences
            )
            if (
                asset.classification != "private"
                or actual_audiences != expected_audiences
            ):
                raise ValueError(
                    "bundle artifact source must be private and exclusively visible "
                    "to the declared verifier"
                )
        if any(
            requirement.producer_id != self.task.verifier.verifier_id
            for requirement in self.verification_outputs
        ):
            raise ValueError(
                "verification outputs must be produced by the declared verifier"
            )
        return self


class TaskPackageResolver(Protocol):
    @property
    def package_id(self) -> str: ...

    def scenario_projection(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
    ) -> ResolvedTaskScenarioProjection: ...

    def resolve(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> FeasibilityAssessment: ...


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _verify_task_files(reader: BundleReader, task: TaskSpec) -> None:
    reader.resolve_file(task.instruction)
    reader.validate_schema_bound_file(task.package.config)
    for asset in task.assets:
        reader.resolve_file(asset.file)
    reader.validate_schema_bound_file(task.verifier.config)


def _verify_environment_files(
    reader: BundleReader, environment: EnvironmentSpec
) -> None:
    reader.validate_schema(environment.gateway.protocol_schema)
    for provider in environment.providers:
        reader.validate_schema_bound_file(provider.config)
        reader.validate_schema(provider.protocol_schema)


def _verify_agent_files(reader: BundleReader, agent: AgentSpec) -> None:
    if agent.driver is not None:
        reader.validate_schema_bound_file(agent.driver.config)
    for tool in agent.tools:
        reader.validate_schema(tool.request_schema)
        reader.validate_schema(tool.response_schema)
    for observation in agent.observations:
        reader.validate_schema(observation.schema_file)
    for query in agent.queries:
        reader.validate_schema(query.request_schema)
        reader.validate_schema(query.response_schema)


def _verify_asset_control_path_separation(
    *,
    scenario: ResolvedScenario,
    control_references: tuple[FileRef, ...],
) -> None:
    control_paths = {reference.path for reference in control_references}
    scenario_file_paths = {asset.file.path for asset in scenario.assets} | {
        asset.world.license.file.path
        for asset in scenario.assets
        if asset.world is not None
    }
    overlap = tuple(sorted(control_paths & scenario_file_paths))
    if overlap:
        raise ValueError(
            "scenario payload/license files must be disjoint from control files: "
            f"{list(overlap)}"
        )


def _provider_capability_map(
    environment: EnvironmentSpec,
) -> dict[str, tuple[str, ...]]:
    return {
        provider.provider_id: provider.capabilities
        for provider in environment.providers
    }


def _provider_stage_map(
    environment: EnvironmentSpec,
    provider_registry: ProviderRegistry,
) -> dict[str, ProviderStage]:
    """Project each declared Provider's exact adapter to its registered stage.

    This is the single registry lookup that binds runtime stage into the resolved
    identity. Unknown adapters fail here via ``runtime_stage_for``; there is no
    role/capability/adapter-name inference and no fallback.
    """
    return {
        provider.provider_id: provider_registry.runtime_stage_for(provider.adapter)
        for provider in environment.providers
    }


def _decode_json_pointer(pointer: str) -> tuple[str, ...]:
    return tuple(
        token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")
    )


def _matrix_value(value: MatrixValue) -> object:
    if isinstance(value, FileRef):
        return value.model_dump(mode="json")
    return value


def _apply_override(
    document: dict[str, object], axis: MatrixAxis, value: MatrixValue
) -> None:
    segments = _decode_json_pointer(axis.target)
    if not segments:
        raise ValueError("matrix target cannot address the document root")

    parent: object = document
    for segment in segments[:-1]:
        if isinstance(parent, dict):
            if segment not in parent:
                raise ValueError(f"matrix target does not exist: {axis.target}")
            parent = parent[segment]
            continue
        if isinstance(parent, list):
            if not segment.isdecimal():
                raise ValueError(f"matrix list target must be an index: {axis.target}")
            index = int(segment)
            if index >= len(parent):
                raise ValueError(f"matrix target does not exist: {axis.target}")
            parent = parent[index]
            continue
        raise ValueError(
            f"matrix target does not address a structured value: {axis.target}"
        )

    leaf = segments[-1]
    replacement = _matrix_value(value)
    if isinstance(parent, dict):
        if leaf not in parent:
            raise ValueError(f"matrix target does not exist: {axis.target}")
        parent[leaf] = replacement
        return
    if isinstance(parent, list):
        if not leaf.isdecimal():
            raise ValueError(f"matrix list target must be an index: {axis.target}")
        index = int(leaf)
        if index >= len(parent):
            raise ValueError(f"matrix target does not exist: {axis.target}")
        parent[index] = replacement
        return
    raise ValueError(
        f"matrix target does not address a structured value: {axis.target}"
    )


def _expand_axes(
    axes: tuple[MatrixAxis, ...],
) -> tuple[tuple[AppliedOverride, ...], ...]:
    if not axes:
        return ((),)
    return tuple(
        tuple(
            AppliedOverride(axis_id=axis.axis_id, target=axis.target, value=value)
            for axis, value in zip(axes, combination, strict=True)
        )
        for combination in itertools.product(*(axis.values for axis in axes))
    )


def _derive_artifact_requirements(
    task: TaskSpec,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
) -> tuple[ArtifactRequirement, ...]:
    producer_requirements = [
        *environment.harness_artifact_requirements,
        *(
            requirement
            for provider in environment.providers
            for requirement in provider.artifact_requirements
        ),
        *(
            requirement
            for agent in agents
            for requirement in agent.artifact_requirements
        ),
        *(
            requirement
            for agent in agents
            if agent.driver is not None
            for requirement in agent.driver.artifact_requirements
        ),
    ]
    by_artifact_id: dict[str, ArtifactRequirement] = {}
    for requirement in producer_requirements:
        existing = by_artifact_id.get(requirement.artifact_id)
        if existing is not None and existing != requirement:
            raise ValueError(
                "runtime producers disagree about artifact contract: "
                f"{requirement.artifact_id}"
            )
        by_artifact_id[requirement.artifact_id] = requirement

    for requirement in task.verifier.artifact_requirements:
        produced = by_artifact_id.get(requirement.artifact_id)
        if produced is None and requirement.producer_id == "bundle":
            by_artifact_id[requirement.artifact_id] = requirement
            continue
        if produced is None:
            raise ValueError(
                "verifier requires an undeclared runtime artifact: "
                f"{requirement.artifact_id}"
            )
        if produced != requirement:
            raise ValueError(
                "verifier and producer disagree about artifact contract: "
                f"{requirement.artifact_id}"
            )
    artifact_ids = list(by_artifact_id)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise ValueError(
            "resolved artifact requirements must have unique artifact_id values"
        )
    relative_paths = [
        requirement.relative_path for requirement in by_artifact_id.values()
    ]
    if len(relative_paths) != len(set(relative_paths)):
        raise ValueError(
            "resolved artifact requirements must have unique relative_path values"
        )
    return tuple(by_artifact_id[key] for key in sorted(by_artifact_id))


def validate_runtime_run_bundle(
    run: ResolvedRunSpec,
    *,
    bundle_root: str | Path,
    task_package_resolvers: tuple[TaskPackageResolver, ...],
    provider_registry: ProviderRegistry,
) -> BundleReader:
    """Revalidate only inputs deliberately materialized to the Harness.

    The executor performs full source-Suite and asset validation before creating
    the execution plan. The runtime Harness must not be given Agent-, Provider-,
    or Verifier-private assets merely to repeat that materializer check. It
    revalidates its self-authenticating ResolvedRun, runtime schemas/configs, and
    the package-derived feasibility decisions that it actually consumes.
    """

    ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
    # Explicit registry drift gate: adapter -> registered stage -> serialized
    # stage, and the exact Provider set, before any further work.
    provider_registry.verify_runtime_stage_binding(
        environment_providers=run.environment.providers,
        resolved_providers=run.scenario.providers,
    )
    reader = BundleReader(Path(bundle_root))
    reader.validate_schema_bound_file(run.task.package.config)
    _verify_environment_files(reader, run.environment)
    for agent in run.agents:
        _verify_agent_files(reader, agent)

    package_resolvers = {
        resolver.package_id: resolver for resolver in task_package_resolvers
    }
    if len(package_resolvers) != len(task_package_resolvers):
        raise ValueError("task package resolver ids must be unique")
    package_resolver = package_resolvers.get(run.task.package.package_id)
    if package_resolver is None:
        raise ValueError(
            "no task package resolver is registered for "
            f"{run.task.package.package_id}"
        )
    runtime_resolve = getattr(package_resolver, "resolve_runtime", None)
    recomputed = (
        runtime_resolve(
            reader=reader,
            task=run.task,
            environment=run.environment,
            agents=run.agents,
            scenario=run.scenario,
        )
        if callable(runtime_resolve)
        else package_resolver.resolve(
            reader=reader,
            task=run.task,
            environment=run.environment,
            agents=run.agents,
            scenario=run.scenario,
        )
    )
    if recomputed != run.feasibility:
        raise ValueError(
            "ResolvedRun feasibility does not match the runtime task package"
        )
    return reader


def validate_resolved_run_bundle(
    run: ResolvedRunSpec,
    *,
    bundle_root: str | Path,
    task_package_resolvers: tuple[TaskPackageResolver, ...],
    provider_registry: ProviderRegistry,
) -> BundleReader:
    """Revalidate pinned bundle inputs and every derived task-package decision.

    A serialized ResolvedRun is immutable, but it is still untrusted executor input.
    Materializers call this boundary before emitting any runtime-specific plan.
    """

    ResolvedRunSpec.model_validate(run.model_dump(mode="json"))
    # Explicit registry drift gate before full re-resolution.
    provider_registry.verify_runtime_stage_binding(
        environment_providers=run.environment.providers,
        resolved_providers=run.scenario.providers,
    )
    reader = BundleReader(Path(bundle_root))
    package_resolvers = {
        resolver.package_id: resolver for resolver in task_package_resolvers
    }
    if len(package_resolvers) != len(task_package_resolvers):
        raise ValueError("task package resolver ids must be unique")

    _verify_task_files(reader, run.task)
    WorldPackage.model_validate(reader.load_document(run.scenario.source_world_package))
    _verify_environment_files(reader, run.environment)
    for agent in run.agents:
        _verify_agent_files(reader, agent)

    package_resolver = package_resolvers.get(run.task.package.package_id)
    if package_resolver is None:
        raise ValueError(
            "no task package resolver is registered for "
            f"{run.task.package.package_id}"
        )
    recomputed = package_resolver.resolve(
        reader=reader,
        task=run.task,
        environment=run.environment,
        agents=run.agents,
        scenario=run.scenario,
    )
    if recomputed != run.feasibility:
        raise ValueError(
            "ResolvedRun feasibility does not match the pinned task package"
        )
    suite_path = reader.resolve_file(run.suite)
    source_runs = resolve_suite(
        str(suite_path),
        executor_kind=run.executor_kind,
        task_package_resolvers=task_package_resolvers,
        provider_registry=provider_registry,
    )
    matches = tuple(item for item in source_runs if item.run_id == run.run_id)
    if matches != (run,):
        raise ValueError(
            "ResolvedRun is not an exact output of its pinned source Suite"
        )
    return reader


def _resolved_models(
    task: TaskSpec,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
    overrides: tuple[AppliedOverride, ...],
) -> tuple[TaskSpec, EnvironmentSpec, tuple[AgentSpec, ...]]:
    document: dict[str, object] = {
        "task": task.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
    }
    for override in overrides:
        _apply_override(
            document,
            MatrixAxis(
                axis_id=override.axis_id,
                target=override.target,
                values=(override.value,),
            ),
            override.value,
        )
    resolved_task = TaskSpec.model_validate(document["task"])
    resolved_environment = EnvironmentSpec.model_validate(document["environment"])
    resolved_agents = tuple(
        AgentSpec.model_validate(item) for item in document["agents"]
    )
    return resolved_task, resolved_environment, resolved_agents


def _resolve_case(
    *,
    suite: SuiteSpec,
    suite_reference: FileRef,
    reader: BundleReader,
    case_index: int,
    executor_kind: ExecutorKind,
    package_resolvers: dict[str, TaskPackageResolver],
    provider_registry: ProviderRegistry,
) -> tuple[ResolvedRunSpec, ...]:
    case = suite.cases[case_index]
    task = reader.load_yaml(case.task, TaskSpec)
    if None in case.launch_site_ids and task.package.package_id != "logistics.arrivals.v1":
        raise ValueError("only logistics.arrivals.v1 permits a no-launch case")
    environment = reader.load_yaml(case.environment, EnvironmentSpec)
    agents = tuple(reader.load_yaml(agent_ref, AgentSpec) for agent_ref in case.agents)
    _verify_task_files(reader, task)
    _verify_environment_files(reader, environment)
    for agent in agents:
        _verify_agent_files(reader, agent)

    resolved_runs: list[ResolvedRunSpec] = []
    for seed in case.seeds:
        for launch_site_id in case.launch_site_ids:
            for overrides in _expand_axes(case.axes):
                resolved_task, resolved_environment, resolved_agents = _resolved_models(
                    task, environment, agents, overrides
                )
                _verify_task_files(reader, resolved_task)
                _verify_environment_files(reader, resolved_environment)
                for agent in resolved_agents:
                    _verify_agent_files(reader, agent)
                package_resolver = package_resolvers.get(
                    resolved_task.package.package_id
                )
                if package_resolver is None:
                    raise ValueError(
                        "no task package resolver is registered for "
                        f"{resolved_task.package.package_id}"
                    )
                task_projection = package_resolver.scenario_projection(
                    reader=reader,
                    task=resolved_task,
                    environment=resolved_environment,
                    agents=resolved_agents,
                )
                scenario = compile_resolved_scenario(
                    reader,
                    case.world_package,
                    launch_site_id,
                    seed,
                    _provider_capability_map(resolved_environment),
                    _provider_stage_map(resolved_environment, provider_registry),
                    resolved_task,
                    resolved_agents,
                    task_projection,
                )
                _verify_asset_control_path_separation(
                    scenario=scenario,
                    control_references=(
                        suite_reference,
                        case.task,
                        case.environment,
                        *case.agents,
                        case.world_package,
                        resolved_task.instruction,
                        resolved_task.package.config.file,
                        resolved_task.package.config.schema_file,
                        resolved_task.verifier.config.file,
                        resolved_task.verifier.config.schema_file,
                        resolved_environment.gateway.protocol_schema,
                        *(
                            reference
                            for provider in resolved_environment.providers
                            for reference in (
                                provider.config.file,
                                provider.config.schema_file,
                                provider.protocol_schema,
                            )
                        ),
                        *(
                            reference
                            for agent in resolved_agents
                            for grant in agent.tools
                            for reference in (
                                grant.request_schema,
                                grant.response_schema,
                            )
                        ),
                        *(
                            grant.schema_file
                            for agent in resolved_agents
                            for grant in agent.observations
                        ),
                        *(
                            reference
                            for agent in resolved_agents
                            for grant in agent.queries
                            for reference in (
                                grant.request_schema,
                                grant.response_schema,
                            )
                        ),
                        *(
                            reference
                            for agent in resolved_agents
                            if agent.driver is not None
                            for reference in (
                                agent.driver.config.file,
                                agent.driver.config.schema_file,
                            )
                        ),
                    ),
                )
                feasibility = package_resolver.resolve(
                    reader=reader,
                    task=resolved_task,
                    environment=resolved_environment,
                    agents=resolved_agents,
                    scenario=scenario,
                )
                if feasibility.package_id != resolved_task.package.package_id:
                    raise ValueError(
                        "task package resolver returned another package_id"
                    )
                artifact_requirements = _derive_artifact_requirements(
                    resolved_task, resolved_environment, resolved_agents
                )
                payload: dict[str, object] = {
                    "schema_version": "aero-bench.resolved-run/v5",
                    "executor_kind": executor_kind,
                    "execution_scope": suite.execution_scope,
                    "suite": suite_reference.model_dump(mode="json"),
                    "suite_id": suite.suite_id,
                    "case_id": case.case_id,
                    "launch_site_id": launch_site_id,
                    "seed": seed,
                    "scenario": scenario.model_dump(mode="json"),
                    "task": resolved_task.model_dump(mode="json"),
                    "environment": resolved_environment.model_dump(mode="json"),
                    "agents": [
                        agent.model_dump(mode="json") for agent in resolved_agents
                    ],
                    "feasibility": feasibility.model_dump(mode="json"),
                    "artifact_requirements": [
                        requirement.model_dump(mode="json")
                        for requirement in artifact_requirements
                    ],
                    "verification_outputs": [
                        requirement.model_dump(mode="json")
                        for requirement in resolved_task.verifier.output_artifacts
                    ],
                    "overrides": [
                        override.model_dump(mode="json") for override in overrides
                    ],
                }
                run_id = _digest(payload)
                resolved_runs.append(ResolvedRunSpec(run_id=run_id, **payload))
    return tuple(resolved_runs)


def resolve_suite(
    suite_path: str,
    *,
    executor_kind: ExecutorKind,
    task_package_resolvers: tuple[TaskPackageResolver, ...],
    provider_registry: ProviderRegistry,
) -> tuple[ResolvedRunSpec, ...]:
    loaded_suite = load_suite(suite_path)
    reader = BundleReader(loaded_suite.root)
    suite_reference = FileRef(
        path=loaded_suite.suite_path.relative_to(loaded_suite.root).as_posix(),
        sha256=loaded_suite.suite_digest,
    )
    package_resolvers = {
        resolver.package_id: resolver for resolver in task_package_resolvers
    }
    if len(package_resolvers) != len(task_package_resolvers):
        raise ValueError("task package resolver ids must be unique")
    return tuple(
        run
        for case_index in range(len(loaded_suite.suite.cases))
        for run in _resolve_case(
            suite=loaded_suite.suite,
            suite_reference=suite_reference,
            reader=reader,
            case_index=case_index,
            executor_kind=executor_kind,
            package_resolvers=package_resolvers,
            provider_registry=provider_registry,
        )
    )
