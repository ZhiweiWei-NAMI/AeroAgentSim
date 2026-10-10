from __future__ import annotations

import hashlib
from pathlib import Path

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import FileRef
from aero_bench.providers.registry import ProviderRegistry
from aero_bench.config.resolver import (
    ResolvedRunSpec,
    TaskPackageResolver,
    validate_resolved_run_bundle,
)
from aero_bench.executor.contracts import (
    AgentDriverWorkloadContract,
    AgentWorkloadContract,
    BundleInputPlan,
    ExecutionPlan,
    HarnessWorkloadContract,
    InlineInputPlan,
    ProviderRuntimeManifest,
    ProviderWorkloadContract,
    VerifierWorkloadContract,
    WorkloadPlan,
)
from aero_bench.executor.facility_world import (
    LogisticsWorldMaterializationError,
    apply_logistics_world_materialization,
    logistics_package_for_run,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.resolved import ResolvedAsset, scenario_assets_for_workload
from aero_bench.world.scene_compiler import SceneOrigin


def _bundle_inputs(
    reader: BundleReader,
    references: tuple[tuple[FileRef, int | None], ...],
) -> tuple[BundleInputPlan, ...]:
    by_destination: dict[str, tuple[FileRef, int | None]] = {}
    for reference, expected_size in references:
        source = reader.resolve_file(reference)
        actual_size = source.stat().st_size
        if actual_size <= 0:
            raise ValueError(f"bundle input must be non-empty: {reference.path}")
        if expected_size is not None and actual_size != expected_size:
            raise ValueError(
                f"bundle input size mismatch for {reference.path}: "
                f"expected {expected_size}, got {actual_size}"
            )
        destination = f"bundle/{reference.path}"
        previous = by_destination.get(destination)
        if previous is not None and (
            previous[0] != reference
            or (
                previous[1] is not None
                and expected_size is not None
                and previous[1] != expected_size
            )
        ):
            raise ValueError(f"conflicting bundle inputs target {destination}")
        retained_size = expected_size
        if previous is not None and previous[1] is not None:
            retained_size = previous[1]
        by_destination[destination] = (reference, retained_size)
    return tuple(
        BundleInputPlan(
            source=by_destination[destination][0],
            destination=destination,
            size_bytes=reader.resolve_file(by_destination[destination][0])
            .stat()
            .st_size,
        )
        for destination in sorted(by_destination)
    )


def _scenario_references(
    assets: tuple[ResolvedAsset, ...],
) -> tuple[tuple[FileRef, int | None], ...]:
    references: list[tuple[FileRef, int | None]] = []
    for asset in assets:
        references.append((asset.file, asset.byte_size))
        if asset.world is not None:
            references.append(
                (
                    asset.world.license.file,
                    asset.world.license.byte_size,
                )
            )
    return tuple(references)


def _workload_inputs(
    reader: BundleReader,
    references: tuple[FileRef, ...],
    *,
    scenario_assets: tuple[ResolvedAsset, ...],
) -> tuple[BundleInputPlan, ...]:
    return _bundle_inputs(
        reader,
        (
            *((reference, None) for reference in references),
            *_scenario_references(scenario_assets),
        ),
    )


def _inline_contract(contract: object) -> InlineInputPlan:
    if not hasattr(contract, "model_dump"):
        raise TypeError("workload contract must be a strict model")
    content = (
        canonical_json_bytes(contract.model_dump(mode="json")).decode("utf-8") + "\n"
    )
    return InlineInputPlan(
        destination="contract.json",
        content_utf8=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def build_execution_plan(
    run: ResolvedRunSpec,
    *,
    executor_kind: str,
    bundle_root: str | Path,
    task_package_resolvers: tuple[TaskPackageResolver, ...],
    provider_registry: ProviderRegistry,
    resolved_scene_origin: SceneOrigin | None = None,
    base_world_sdf: bytes | Path | None = None,
) -> ExecutionPlan:
    if run.executor_kind != executor_kind:
        raise ValueError(
            "ResolvedRun executor_kind does not match the requested materializer"
        )
    reader = validate_resolved_run_bundle(
        run,
        bundle_root=bundle_root,
        task_package_resolvers=task_package_resolvers,
        provider_registry=provider_registry,
    )
    harness_scenario_assets = scenario_assets_for_workload(
        run.scenario,
        role="harness",
        workload_id="harness",
    )
    harness_contract = HarnessWorkloadContract.from_run(run)
    harness_refs = (
        run.task.package.config.file,
        run.task.package.config.schema_file,
        run.environment.gateway.protocol_schema,
        *(provider.config.file for provider in run.environment.providers),
        *(provider.config.schema_file for provider in run.environment.providers),
        *(provider.protocol_schema for provider in run.environment.providers),
        *(grant.request_schema for agent in run.agents for grant in agent.tools),
        *(grant.response_schema for agent in run.agents for grant in agent.tools),
        *(grant.request_schema for agent in run.agents for grant in agent.queries),
        *(grant.response_schema for agent in run.agents for grant in agent.queries),
        *(grant.schema_file for agent in run.agents for grant in agent.observations),
        *(agent.driver.config.file for agent in run.agents if agent.driver is not None),
        *(
            agent.driver.config.schema_file
            for agent in run.agents
            if agent.driver is not None
        ),
    )
    runtime_workloads: list[WorkloadPlan] = [
        WorkloadPlan(
            workload_id="harness",
            role="harness",
            phase="runtime",
            workload=run.environment.harness,
            contract=_inline_contract(harness_contract),
            bundle_inputs=_workload_inputs(
                reader,
                harness_refs,
                scenario_assets=harness_scenario_assets,
            ),
        )
    ]

    for provider in run.environment.providers:
        scenario_assets = scenario_assets_for_workload(
            run.scenario,
            role="provider",
            workload_id=provider.provider_id,
        )
        contract = ProviderWorkloadContract(
            schema_version="aero-bench.workload-contract/v5",
            role="provider",
            run_id=run.run_id,
            seed=run.seed,
            workload_id=provider.provider_id,
            clock=run.environment.clock,
            provider=provider,
            scenario_digest=run.scenario.scenario_digest,
            scenario=run.scenario,
            scenario_assets=scenario_assets,
        )
        references = (
            provider.config.file,
            provider.config.schema_file,
            provider.protocol_schema,
        )
        runtime_workloads.append(
            WorkloadPlan(
                workload_id=provider.provider_id,
                role="provider",
                phase="runtime",
                workload=provider.workload,
                contract=_inline_contract(contract),
                bundle_inputs=_workload_inputs(
                    reader,
                    references,
                    scenario_assets=scenario_assets,
                ),
            )
        )

    for agent in run.agents:
        scenario_assets = scenario_assets_for_workload(
            run.scenario,
            role="agent",
            workload_id=agent.agent_id,
        )
        contract = AgentWorkloadContract(
            schema_version="aero-bench.workload-contract/v5",
            role="agent",
            run_id=run.run_id,
            seed=run.seed,
            clock=run.environment.clock,
            workload_id=agent.agent_id,
            task_id=run.task.task_id,
            instruction=run.task.instruction,
            gateway=run.environment.gateway,
            agent=agent,
            scenario_digest=run.scenario.scenario_digest,
            scenario=run.scenario,
            scenario_assets=scenario_assets,
        )
        references = (
            run.task.instruction,
            run.environment.gateway.protocol_schema,
            *(grant.request_schema for grant in agent.tools),
            *(grant.response_schema for grant in agent.tools),
            *(grant.request_schema for grant in agent.queries),
            *(grant.response_schema for grant in agent.queries),
            *(grant.schema_file for grant in agent.observations),
            *(
                (agent.driver.config.file, agent.driver.config.schema_file)
                if agent.driver is not None
                else ()
            ),
        )
        runtime_workloads.append(
            WorkloadPlan(
                workload_id=agent.agent_id,
                role="agent",
                phase="runtime",
                workload=agent.workload,
                contract=_inline_contract(contract),
                bundle_inputs=_workload_inputs(
                    reader,
                    references,
                    scenario_assets=scenario_assets,
                ),
            )
        )
        if agent.driver is not None:
            driver_contract = AgentDriverWorkloadContract(
                schema_version="aero-bench.agent-driver-workload-contract/v2",
                role="agent_driver",
                workload_id=agent.driver.driver_id,
                context=contract,
            )
            runtime_workloads.append(
                WorkloadPlan(
                    workload_id=agent.driver.driver_id,
                    role="agent_driver",
                    phase="runtime",
                    workload=agent.driver.workload,
                    contract=_inline_contract(driver_contract),
                    bundle_inputs=_workload_inputs(
                        reader, references, scenario_assets=()
                    ),
                )
            )

    verifier_id = run.task.verifier.verifier_id
    verifier_scenario_assets = scenario_assets_for_workload(
        run.scenario,
        role="verifier",
        workload_id=verifier_id,
    )
    verifier_contract = VerifierWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="verifier",
        run_id=run.run_id,
        seed=run.seed,
        workload_id=verifier_id,
        task_id=run.task.task_id,
        run=run,
        package=run.task.package,
        feasibility=run.feasibility,
        goals=run.task.goals,
        verifier=run.task.verifier,
        providers=tuple(
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
            for provider in run.environment.providers
        ),
        scenario_digest=run.scenario.scenario_digest,
        scenario_assets=verifier_scenario_assets,
        sealed_artifacts=run.artifact_requirements,
    )
    verifier_refs = (
        run.scenario.source_world_package,
        run.task.package.config.file,
        run.task.package.config.schema_file,
        run.task.verifier.config.file,
        run.task.verifier.config.schema_file,
        *(provider.config.file for provider in run.environment.providers),
        *(provider.config.schema_file for provider in run.environment.providers),
        *(grant.schema_file for agent in run.agents for grant in agent.observations),
        *(
            (run.task.instruction,)
            if any(agent.driver is not None for agent in run.agents)
            else ()
        ),
        *(agent.driver.config.file for agent in run.agents if agent.driver is not None),
        *(
            agent.driver.config.schema_file
            for agent in run.agents
            if agent.driver is not None
        ),
        *(
            grant.request_schema
            for agent in run.agents
            if agent.driver is not None
            for grant in agent.tools
        ),
        *(
            grant.response_schema
            for agent in run.agents
            if agent.driver is not None
            for grant in agent.tools
        ),
        *(
            grant.request_schema
            for agent in run.agents
            if agent.driver is not None
            for grant in agent.queries
        ),
        *(
            grant.response_schema
            for agent in run.agents
            if agent.driver is not None
            for grant in agent.queries
        ),
    )
    verifier_workload = WorkloadPlan(
        workload_id=verifier_id,
        role="verifier",
        phase="verification",
        workload=run.task.verifier.workload,
        contract=_inline_contract(verifier_contract),
        bundle_inputs=_workload_inputs(
            reader,
            verifier_refs,
            scenario_assets=verifier_scenario_assets,
        ),
    )
    plan = ExecutionPlan(
        run=run,
        executor_kind=executor_kind,
        bundle_root=str(reader.root),
        runtime_workloads=tuple(runtime_workloads),
        verifier_workload=verifier_workload,
    )
    if run.task.package.package_id == "logistics.task.v1":
        # The logistics resolver/scene barrier is the only producer of a
        # logistics ResolvedRun with a resolved SceneOrigin; this seam consumes
        # that authoritative origin and the compiled base world SDF, and stages
        # the deterministic facility-spliced world into the px4.gazebo Provider
        # workload.  It never claims a successful logistics run.  The logistics
        # package id constant is imported here (function-local) so the executor
        # package stays importable on snapshots where the logistics world-v2
        # resolved surface is absent.
        from aero_bench.tasks.logistics.contracts import LOGISTICS_PACKAGE_ID

        if run.task.package.package_id != LOGISTICS_PACKAGE_ID:
            raise LogisticsWorldMaterializationError(
                "logistics world materialization requires a logistics TaskSpec "
                f"package, found {run.task.package.package_id}"
            )
        if resolved_scene_origin is None:
            raise LogisticsWorldMaterializationError(
                "executor materialization of a logistics run requires the "
                "resolved SceneOrigin (ResolvedScenario.frame_authority.origin); "
                "the logistics run barrier must supply it explicitly"
            )
        if base_world_sdf is None:
            raise LogisticsWorldMaterializationError(
                "executor materialization of a logistics run requires the "
                "compiled base world SDF (gazebo/scene.sdf); the logistics run "
                "barrier must supply it explicitly"
            )
        package = logistics_package_for_run(reader=reader, run=run)
        plan = apply_logistics_world_materialization(
            plan,
            package=package,
            origin=resolved_scene_origin,
            base_world=base_world_sdf,
        )
    return plan
