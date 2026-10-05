"""Tests for the concrete logistics RuntimeHook (WP1 physical-observation ingress).

The hook consumes the *actual* ``RuntimeHook.on_stage_barriers_closed`` inputs —
the sealed motion ``aero-bench.scene-state/v1``, the closed ``px4.state.v1``
ProviderEvents and the closed ``StageBarrier`` tuple at one authoritative tick —
and derives typed physical observations under *explicit declared pose-reference
calibrations* (the ``DeclaredAircraftPoseReference``; half body height is never
inferred).  The derived batch crosses the real JSON-line loopback into the
actual Logistics Business workspace service workload, which atomically journals
it and later persists it inside the declared business artifact.

The fixtures here are honest: the package, world, resolved scenario, run
bindings and provider environments are compiled through the real
``materialize_world_package``/``compile_resolved_scenario``/
``ResolvedRunSpec``/``validate_logistics_runtime_bindings`` pipeline, and the
provider client is driven against the real service module from
``containers/logistics-business/service.py``.
"""

from __future__ import annotations

from tests.support import fixture_provider_registry

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FileRef,
    NamedValue,
    ObservationGrant,
    ProviderRef,
    SchemaBoundFile,
    TaskSpec,
)
from aero_bench.config.resolver import _derive_artifact_requirements, ResolvedRunSpec
from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.gateway import (
    AgentPrincipal,
    GatewayDispatchError,
    GatewayDispatcher,
    ObservationEnvelope,
    ProviderObservationEndpoint,
)
from aero_bench.providers.logistics_business import (
    LogisticsBusinessConfig,
    LogisticsBusinessProvider,
)
from aero_bench.providers.rpc import JsonLineRpcServer, ProviderRemoteError
from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    stage_barrier_digest_value,
)
from aero_bench.runtime.hooks import RuntimeHook, ValidatedObservation
from aero_bench.runtime.ledger import EventLedger
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationSpec,
    LogisticsObservationSpecItem,
)
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    validate_logistics_runtime_bindings,
)
from aero_bench.tasks.logistics.runtime_hook import (
    LogisticsRuntimeHook,
    LogisticsRuntimeHookError,
    LogisticsRuntimeHookFactory,
)
from aero_bench.world.contracts import ProviderRequirement
from aero_bench.world.resolved import (
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
    scenario_assets_for_workload,
)

from tests.providers.test_logistics_business_provider import _manifest
from tests.providers.test_logistics_business_service import (
    _SERVICE,
    CAPABILITIES,
    IMAGE,
    PROVIDER_ID,
    PROTOCOL_VERSION,
    SEED,
    SESSION_TOKEN,
    _requirement,
)
from tests.tasks.test_logistics_physical_observations import (
    _scene_state,
    _state_event,
    _state_event_values,
    _state_sample,
)
from tests.tasks.test_logistics_runtime_bindings import (
    _build_task,
    _capability_map,
    _facilities,
    _fixture_runtime,
    _make_px4_gazebo_environment,
    _package_document,
    _placeholder_ref,
    _px4_config_document,
    _standard_agents,
    _valid_bindings,
    _vertiport,
    _world_with_uav_ids,
    _write_fixture_file,
)
from tests.support import rebuild_run_agents
from tests.world.support import materialize_world_package


AT = SimulationTime(tick=1, sim_time_ns=1_000_000_000)
SERVICE_PORT = 18439


@dataclass(frozen=True, slots=True)
class _CohesiveFixture:
    """Everything the actual resolved run/package/bindings/scenario need."""

    reader: BundleReader
    task: TaskSpec
    environment: object
    agents: object
    scenario: object
    package: object
    bindings: object
    business_config: LogisticsBusinessConfig
    artifact_requirements: tuple[object, ...]
    resolved_run: ResolvedRunSpec


def _world_mutate(extra_provider_req):
    def mutate(kwargs, root):
        _world_with_uav_ids(("uav.alpha",))(kwargs, root)
        reqs = list(kwargs["provider_requirements"])
        reqs.append(extra_provider_req)
        kwargs["provider_requirements"] = tuple(
            sorted(reqs, key=lambda r: r.provider_id)
        )

    return mutate


def _make_business_provider(
    port: int, *, config_ref: FileRef, schema_ref: FileRef
) -> ProviderRef:
    return ProviderRef(
        provider_id=PROVIDER_ID,
        adapter="logistics.business",
        port=port,
        workload=_fixture_runtime("logistics.business"),
        config=SchemaBoundFile(
            file=config_ref,
            schema_file=schema_ref,
        ),
        protocol_schema=_placeholder_ref("schemas/protocol/logistics-business.json"),
        capabilities=tuple(sorted(CAPABILITIES)),
        artifact_requirements=(),
    )


def build_fixture(root: Path, *, port: int = SERVICE_PORT,
                  no_fly_zones: tuple[dict, ...] = ()) -> _CohesiveFixture:
    """Compile the honest cohesive run fixture.

    One world package declares both the flight (motion) provider and the
    logistics business provider so the resolved scenario carries ONE digest usable
    both by the logistics runtime bindings/hook and by the business workload
    contract.  ``facility-1`` is a pad-feasible vertiport (``widthM=18`` for 3
    landing pads).  The verifier image digest is patched to differ from every
    fixture workload image digest as ``ResolvedRunSpec`` requires.

    The logistics business provider config FILE + SCHEMA FILE are real bytes in
    the bundle with digest-pinned FileRefs (the schema is generated from the
    current registered native v2 model), so the resolved run pins exactly the
    configuration the runtime-hook factory must prove against through the real
    digest-verified BundleReader contract.
    """
    root.mkdir(parents=True, exist_ok=True)
    business_req = ProviderRequirement(
        provider_id=PROVIDER_ID,
        roles=("mission",),
        required_capability_ids=tuple(sorted(CAPABILITIES)),
    )
    reader, world_ref, world = materialize_world_package(
        root,
        mutate_kwargs=_world_mutate(business_req),
    )
    raw = _package_document(aircraft_count=1)
    raw["noFlyZones"] = list(no_fly_zones)
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    facilities = _facilities()
    facilities[0] = _vertiport(id="facility-1", widthM=18)
    raw["facilities"] = facilities
    task = _build_task(root, raw)
    task_document = task.model_dump(mode="json")
    task_document["verifier"]["workload"]["runtime"]["image"] = (
        "registry.invalid/logistics-verifier@sha256:" + "e" * 64
    )
    task = TaskSpec.model_validate(task_document)
    package = lower_logistics_task_package(raw)
    business_document = _business_config_document(
        package.model_dump(mode="json"),
        bindings_document=_valid_bindings(aircraft_count=1),
    )
    business_config = LogisticsBusinessConfig.model_validate(business_document)
    # The declared business config + schema are REAL bytes in the bundle, with
    # digest-pinned FileRefs.  The schema is the one generated from the current
    # registered native v2 model so a config the backend cannot materialize is
    # rejected; the factory reads these exact bytes through the digest-verified
    # BundleReader and proves its typed config equals them.
    business_config_ref = _write_fixture_file(
        root,
        "configs/providers/logistics-business.json",
        canonical_json_bytes(business_config.model_dump(mode="json")) + b"\n",
    )
    business_schema_ref = _write_fixture_file(
        root,
        "schemas/providers/logistics-business.json",
        canonical_json_bytes(LogisticsBusinessConfig.model_json_schema()) + b"\n",
    )
    env_base = _make_px4_gazebo_environment(root, _px4_config_document(("uav.alpha",)))
    environment = env_base.model_copy(
        update={
            "providers": (
                *env_base.providers,
                _make_business_provider(
                    port=port,
                    config_ref=business_config_ref,
                    schema_ref=business_schema_ref,
                ),
            )
        }
    )
    agents = _standard_agents()
    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=(),
        logical_capability_ids=(),
        observations=(),
    )
    scenario = compile_resolved_scenario(
        reader,
        world_ref,
        "launch.alpha",
        SEED,
        _capability_map(environment),
        {provider.provider_id: fixture_provider_registry().runtime_stage_for(provider.adapter) for provider in environment.providers},
        task,
        agents,
        projection,
    )
    bindings = validate_logistics_runtime_bindings(
        bindings_document=_valid_bindings(aircraft_count=1),
        reader=reader,
        environment=environment,
        agents=agents,
        package=package,
        scenario=scenario,
    )
    artifact_requirements = _derive_artifact_requirements(task, environment, agents)
    resolved_run = _build_resolved_run(
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
        artifact_requirements=artifact_requirements,
    )
    return _CohesiveFixture(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
        package=package,
        bindings=bindings,
        business_config=business_config,
        artifact_requirements=artifact_requirements,
        resolved_run=resolved_run,
    )


def _rebind_business_config_file(
    fixture: _CohesiveFixture,
    root: Path,
    *,
    business_config: LogisticsBusinessConfig,
) -> _CohesiveFixture:
    """Return a cohesive fixture whose bundle BUSINESS CONFIG FILE is ``business_config``.

    The bytes are written at the SAME bundle path the run environment pins for
    the logistics business provider, and the run environment's provider config
    FileRef is updated to the new file's real digest.  The authoritative
    ``run.task.package`` file is left untouched, so a factory whose declared
    config equals the new business config can be driven against a run whose
    task package FILE differs from the business config's nested ``task_package``
    (same IDs, different orders/profile/constraints).
    """
    config_ref = _write_fixture_file(
        root,
        "configs/providers/logistics-business.json",
        canonical_json_bytes(business_config.model_dump(mode="json")) + b"\n",
    )
    existing = next(
        provider
        for provider in fixture.environment.providers
        if provider.provider_id == PROVIDER_ID
    )
    business_provider = _make_business_provider(
        port=existing.port,
        config_ref=config_ref,
        schema_ref=existing.config.schema_file,
    )
    providers = tuple(
        business_provider
        if provider.provider_id == PROVIDER_ID
        else provider
        for provider in fixture.environment.providers
    )
    environment = fixture.environment.model_copy(update={"providers": providers})
    resolved_run = _build_resolved_run(
        task=fixture.task,
        environment=environment,
        agents=fixture.agents,
        scenario=fixture.scenario,
        artifact_requirements=fixture.artifact_requirements,
    )
    return _CohesiveFixture(
        reader=fixture.reader,
        task=fixture.task,
        environment=environment,
        agents=fixture.agents,
        scenario=fixture.scenario,
        package=fixture.package,
        bindings=fixture.bindings,
        business_config=business_config,
        artifact_requirements=fixture.artifact_requirements,
        resolved_run=resolved_run,
    )


def _divergent_order_document(document: dict) -> dict:
    """Same task_id/package_id/scene_id, different ORDER content (cargo mass)."""
    orders = list(document["orders"])
    orders[0] = dict(orders[0])
    orders[0]["cargo_mass_kg"] = 2.0
    document["orders"] = orders
    return document


def _divergent_profile_document(document: dict) -> dict:
    """Same task_id/package_id/scene_id, different PERFORMANCE PROFILE body."""
    profiles = list(document["performance_profiles"])
    profiles[0] = dict(profiles[0])
    profiles[0]["aircraft_body"] = dict(profiles[0]["aircraft_body"])
    profiles[0]["aircraft_body"]["x_m"] = 0.7
    document["performance_profiles"] = profiles
    return document


def _divergent_business_fixture(
    root: Path,
    *,
    mutate_package,
) -> _CohesiveFixture:
    """An honest fixture whose declared business config diverges from the task package.

    The bundle BUSINESS CONFIG FILE (pinned in the run environment) carries a
    nested ``task_package`` reconstructed with ``mutate_package`` — the SAME
    task_id/package_id/scene_id but different order/profile/constraint content —
    while the authoritative ``run.task.package`` file keeps the original
    package.  The run environment's business provider config FileRef is rebound
    to the real digest of the divergent business config bytes.
    """
    fixture = build_fixture(root)
    document = fixture.package.model_dump(mode="json")
    divergent = mutate_package(document)
    business_document = _business_config_document(
        divergent,
        bindings_document=_valid_bindings(aircraft_count=1),
    )
    business_config = LogisticsBusinessConfig.model_validate(business_document)
    return _rebind_business_config_file(
        fixture,
        root,
        business_config=business_config,
    )


def _observation_config_document(bindings_document: dict) -> dict:
    """The declared digest-bound observation branch used by the hook fixture.

    The exact authored aircraft ``fleet-alpha:1`` is bound to the native
    ``flight``/``uav.alpha`` provider/vehicle + fleet/asset identity, the
    pose-reference calibration is declared at 0.1 m (never inferred), and the
    one (fleet-alpha:1, facility-1, pad 0) plan item matches the hypothesis the
    ``_closed_stage`` sample rests on.
    """
    return {
        "schema_version": "aero-bench.logistics-observation-config/v1",
        "bindings": bindings_document,
        "pose_references": [
            {
                "aircraft_id": "fleet-alpha:1",
                "pose_reference_above_contact_m": 0.1,
            }
        ],
        "tolerances": {
            "vertical_tolerance_m": 0.05,
            "horizontal_uncertainty_m": 0.0,
            "max_stationary_speed_m_s": 0.5,
        },
        "spec": {
            "schema_version": "aero-bench.logistics-observation-spec/v1",
            "items": [
                {
                    "aircraft_id": "fleet-alpha:1",
                    "facility_id": "facility-1",
                    "pad_index": 0,
                }
            ],
        },
    }


def _business_config_document(
    task_package: dict, *, bindings_document: dict | None = None
) -> dict:
    bindings_document = (
        bindings_document
        if bindings_document is not None
        else _valid_bindings(aircraft_count=1)
    )
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": task_package,
        "observation": _observation_config_document(bindings_document),
        "principal_bindings": [
            {
                "principal_id": "agent.provider",
                "actor_id": "fleet-alpha:1",
                "role": "aircraft_agent",
            },
            {
                "principal_id": "logistics.dispatcher",
                "actor_id": "logistics.dispatcher",
                "role": "dispatcher",
            },
            {
                "principal_id": "logistics.business",
                "actor_id": "logistics.business",
                "role": "business",
            },
        ],
    }


def _write_business_bundle(root: Path, config_document: dict) -> dict:
    config_path = root / "provider.config.json"
    config_path.write_bytes(canonical_json_bytes(config_document) + b"\n")
    schema_path = root / "provider.schema.json"
    schema_path.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    return {
        "config": {
            "path": "provider.config.json",
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "schema": {
            "path": "provider.schema.json",
            "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        },
    }


def _contract(
    refs: dict, port: int, scenario, *, run_id: str
) -> ProviderWorkloadContract:
    return ProviderWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="provider",
        run_id=run_id,
        seed=SEED,
        workload_id=PROVIDER_ID,
        clock={
            "authority": "provider_barrier",
            "step_ns": 1_000_000_000,
            "max_steps": 2,
            "provider_timeout_ms": 10_000,
        },
        provider={
            "provider_id": PROVIDER_ID,
            "adapter": "logistics.business",
            "port": port,
            "workload": {
                "runtime": {"image": IMAGE, "command": ["provider", "serve"]},
                "resources": {
                    "cpu_millicores": 1000,
                    "memory_mib": 1024,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": "logistics.business",
                    "kind": "mechanical_fixture",
                    "source_uri": "https://github.com/moby/moby",
                    "source_revision": "4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
                    "version": "test-fixture-1",
                },
            },
            "config": {"file": refs["config"], "schema_file": refs["schema"]},
            "protocol_schema": refs["schema"],
            "capabilities": list(sorted(CAPABILITIES)),
            "artifact_requirements": [_requirement()],
        },
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        scenario_assets=scenario_assets_for_workload(
            scenario,
            role="provider",
            workload_id=PROVIDER_ID,
        ),
    )


def _workload_identity(
    root: Path, *, port: int, refs: dict, scenario, run_id: str
) -> _SERVICE.WorkloadIdentity:
    return _SERVICE.WorkloadIdentity(
        run_id=run_id,
        provider_id=PROVIDER_ID,
        provider_port=port,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=SEED,
        contract=_contract(refs, port, scenario, run_id=run_id),
    )


def _build_resolved_run(
    *, task, environment, agents, scenario, artifact_requirements
) -> ResolvedRunSpec:
    payload = {
        "schema_version": "aero-bench.resolved-run/v5",
        "executor_kind": "docker_reference",
        "execution_scope": "executor_validation",
        "suite": FileRef(path="suite.yaml", sha256="c" * 64).model_dump(mode="json"),
        "suite_id": "logistics.suite",
        "case_id": "logistics.case",
        "launch_site_id": scenario.selected_launch_site_id,
        "seed": SEED,
        "scenario": scenario.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "environment": environment.model_dump(mode="json"),
        "agents": [agent.model_dump(mode="json") for agent in agents],
        "feasibility": {
            "package_id": LOGISTICS_PACKAGE_ID,
            "feasible": True,
            "success_upper_bound": 1.0,
            "failed_conditions": [],
            "bounds": [],
        },
        "artifact_requirements": [
            requirement.model_dump(mode="json")
            for requirement in artifact_requirements
        ],
        "verification_outputs": [
            requirement.model_dump(mode="json")
            for requirement in task.verifier.output_artifacts
        ],
        "overrides": (),
    }
    run_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return ResolvedRunSpec(run_id=run_id, **payload)


def _stage_request_with_scenario(
    *,
    tick: int,
    sim_time_ns: int,
    scenario,
    run_id: str,
    previous_scene_state=None,
) -> BusinessEnvironmentStageRequest:
    static_entity = next(entity for entity in scenario.entities if entity.state == "static")
    static_scenario = scenario.model_copy(update={"entities": (static_entity,)})
    assembler = SceneStateAssembler(static_scenario)
    target = SimulationTime(tick=tick, sim_time_ns=sim_time_ns)
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": run_id,
        "scenario_digest": scenario.scenario_digest,
        "at": target,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    unsigned = StageBarrier.model_construct(**barrier_fields, barrier_digest="0" * 64)
    barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned),
    )
    scene_state = assembler.assemble(
        run_id=run_id,
        at=target,
        barrier=barrier,
        contributions=(),
        previous_scene_state=previous_scene_state,
    )
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=run_id,
        scenario_digest=scenario.scenario_digest,
        provider_id=PROVIDER_ID,
        target=target,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(stage="motion", barrier_digest=barrier.barrier_digest),
        ),
    )


@asynccontextmanager
async def _running_stack(fixture: _CohesiveFixture, root: Path, *, port: int = SERVICE_PORT):
    """Start the real service + provider client over a JSON-line loopback.

    The client is prepared/reset and stepped to ``AT`` (tick 1), so the
    authoritative business provider barrier is exactly ``AT`` before the hook or
    any observation batch is submitted.
    """
    refs = _write_business_bundle(
        root, fixture.business_config.model_dump(mode="json")
    )
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    identity = _workload_identity(
        root,
        port=port,
        refs=refs,
        scenario=fixture.scenario,
        run_id=fixture.resolved_run.run_id,
    )
    service = _SERVICE.LogisticsBusinessProviderService(
        bundle_root=root,
        artifact_root=root / "artifacts",
        rpc_port=port,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )
    server = JsonLineRpcServer(service.serve_rpc)
    handle = await server.start(host="127.0.0.1", port=0)
    client_port = handle.sockets[0].getsockname()[1]
    client = LogisticsBusinessProvider(
        config=fixture.business_config,
        manifest=_manifest(identity.config_digest),
        runtime_endpoint=RuntimeEndpoint(host="127.0.0.1", port=client_port),
        run_id=fixture.resolved_run.run_id,
        session_token=SESSION_TOKEN,
        scenario=fixture.scenario,
    )
    try:
        await client.prepare()
        await client.reset(seed=SEED)
        request = _stage_request_with_scenario(
            tick=AT.tick,
            sim_time_ns=AT.sim_time_ns,
            scenario=fixture.scenario,
            run_id=fixture.resolved_run.run_id,
        )
        result = await client.step_stage(request)
        assert result.step_receipt.reached == AT
        yield service, client
    finally:
        await server.graceful_close()
        await handle.wait_closed()


def _observation_spec() -> LogisticsObservationSpec:
    return LogisticsObservationSpec(
        schema_version="aero-bench.logistics-observation-spec/v1",
        items=(
            LogisticsObservationSpecItem(
                aircraft_id="fleet-alpha:1",
                facility_id="facility-1",
                pad_index=0,
            ),
        ),
    )


def _pose_references() -> tuple[DeclaredAircraftPoseReference, ...]:
    return (
        DeclaredAircraftPoseReference(
            aircraft_id="fleet-alpha:1",
            pose_reference_above_contact_m=0.1,
        ),
    )


def _presence_tolerances() -> PresenceTolerances:
    return PresenceTolerances(
        vertical_tolerance_m=0.05,
        horizontal_uncertainty_m=0.0,
        max_stationary_speed_m_s=0.5,
    )


def _closed_stage(fixture: _CohesiveFixture, *, run_id: str | None = None, at: SimulationTime = AT):
    """The honest typed closed-stage inputs: sample + SceneState + ProviderEvent.

    The aircraft rests exactly ``pose_reference_above_contact_m`` above the
    occupied facility pad: the explicit calibration, never an inferred
    half-body-height.
    """
    run_id = run_id if run_id is not None else fixture.resolved_run.run_id
    pads = facility_landing_pads(fixture.package.facilities.require("facility-1"))
    pad = pads[0]
    up_m = pad.y + 0.1
    sample = _state_sample(
        at,
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    scene_state = _scene_state(
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        at=at,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=at.sim_time_ns,
    )
    event = _state_event(at, values=values)
    return sample, scene_state, event, pad


def _derive_observation_batch(
    fixture: _CohesiveFixture,
    *,
    run_id: str | None = None,
    at: SimulationTime = AT,
    up_m_offset: float = 0.0,
):
    from aero_bench.tasks.logistics.observation_ingress import (
        build_observation_batch,
        derive_physical_observations,
    )

    run_id = run_id if run_id is not None else fixture.resolved_run.run_id
    pads = facility_landing_pads(fixture.package.facilities.require("facility-1"))
    pad = pads[0]
    up_m = pad.y + 0.1 + up_m_offset
    sample = _state_sample(
        at,
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
    )
    scene_state = _scene_state(
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        at=at,
        samples=(sample,),
    )
    values = _state_event_values(
        vehicle_id="uav.alpha",
        east_m=pad.x,
        north_m=-pad.z,
        up_m=up_m,
        yaw_deg=15.0,
        landed=True,
        in_air=False,
        landed_state="ON_GROUND",
        ground_contact=True,
        contacts=("ground.pad.0",),
        simulation_time_ns=at.sim_time_ns,
    )
    event = _state_event(at, values=values)
    observations = derive_physical_observations(
        scene_state=scene_state,
        events=(event,),
        package=fixture.package,
        bindings=fixture.bindings,
        scenario=fixture.scenario,
        spec=_observation_spec(),
        pose_references=_pose_references(),
        tolerances=_presence_tolerances(),
        stage_barriers=(scene_state.stage_barrier,),
        expected_run_id=run_id,
        target=at,
    )
    return build_observation_batch(
        observations=observations,
        run_id=run_id,
        scenario_digest=fixture.scenario.scenario_digest,
        at=at,
        source_scene_state_digest=scene_state.scene_state_digest,
        source_stage_barrier_digest=scene_state.stage_barrier.barrier_digest,
    )


def _build_hook(
    fixture: _CohesiveFixture,
    client: LogisticsBusinessProvider,
    ledger: EventLedger | None = None,
    *,
    runtime_hook_time=None,
):
    ledger = ledger if ledger is not None else EventLedger(run_id=fixture.resolved_run.run_id)
    if runtime_hook_time is None:
        def runtime_hook_time():
            return AT

    return LogisticsRuntimeHook(
        run=fixture.resolved_run,
        reader=fixture.reader,
        providers={"logistics.business": client, "flight": None},
        ledger=ledger,
        runtime_hook_time=runtime_hook_time,
        package=fixture.package,
        bindings=fixture.bindings,
        scenario=fixture.scenario,
        tolerances=_presence_tolerances(),
        observation_spec=_observation_spec(),
        pose_references=_pose_references(),
        business_provider_id=PROVIDER_ID,
    )


@pytest.fixture(scope="module")
def logistics_cohesive_fixture(tmp_path_factory) -> _CohesiveFixture:
    root = Path(tmp_path_factory.mktemp("logistics-runtime-hook"))
    return build_fixture(root)


# ------------------------------------------------------------------ behaviour


def test_hook_constructor_requires_the_real_business_ingest_surface(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    fixture = logistics_cohesive_fixture
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)

    class _NoIngest:
        pass

    with pytest.raises(LogisticsRuntimeHookError, match="observation-ingest client surface"):
        LogisticsRuntimeHook(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers={"logistics.business": _NoIngest(), "flight": None},
            ledger=ledger,
            runtime_hook_time=lambda: AT,
            package=fixture.package,
            bindings=fixture.bindings,
            scenario=fixture.scenario,
            tolerances=_presence_tolerances(),
            observation_spec=_observation_spec(),
            pose_references=_pose_references(),
            business_provider_id=PROVIDER_ID,
        )

    with pytest.raises(LogisticsRuntimeHookError, match="cannot find the business provider"):
        LogisticsRuntimeHook(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers={"flight": None},
            ledger=ledger,
            runtime_hook_time=lambda: AT,
            package=fixture.package,
            bindings=fixture.bindings,
            scenario=fixture.scenario,
            tolerances=_presence_tolerances(),
            observation_spec=_observation_spec(),
            pose_references=_pose_references(),
            business_provider_id=PROVIDER_ID,
        )

    with pytest.raises(TypeError, match="ResolvedRunSpec"):
        LogisticsRuntimeHook(
            run={"run_id": fixture.resolved_run.run_id},
            reader=fixture.reader,
            providers={"logistics.business": _NoIngest(), "flight": None},
            ledger=ledger,
            runtime_hook_time=lambda: AT,
            package=fixture.package,
            bindings=fixture.bindings,
            scenario=fixture.scenario,
            tolerances=_presence_tolerances(),
            observation_spec=_observation_spec(),
            pose_references=_pose_references(),
            business_provider_id=PROVIDER_ID,
        )


def test_hook_ingests_closed_typed_stage_over_real_rpc_into_artifact(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            hook = _build_hook(fixture, client, ledger)
            _sample, scene_state, event, pad = _closed_stage(fixture)

            await hook.on_stage_barriers_closed(
                (event,),
                scene_state=scene_state,
                stage_barriers=(scene_state.stage_barrier,),
                target=AT,
            )

            assert service._observations is not None
            records = service._observations.records
            assert len(records) == 1
            record = records[0]
            assert record.sequence == 1
            observation = record.observation
            assert observation.run_id == fixture.resolved_run.run_id
            assert observation.scenario_digest == fixture.scenario.scenario_digest
            assert observation.at == AT
            assert observation.aircraft_id == "fleet-alpha:1"
            assert observation.native_vehicle_id == "uav.alpha"
            assert observation.facility_id == "facility-1"
            assert observation.pad_index == 0
            assert (
                observation.source_stage_barrier_digest
                == scene_state.stage_barrier.barrier_digest
            )
            assert (
                observation.source_scene_state_digest
                == scene_state.scene_state_digest
            )
            assert observation.assessment.eligible is True
            assert record.received_at == AT

            assert [entry.event.event_type for entry in ledger.records] == [
                "logistics.observation.ingested"
            ]
            payload = {item.name: item.value for item in ledger.records[-1].event.payload}
            assert payload["run_id"] == fixture.resolved_run.run_id
            assert payload["provider_id"] == PROVIDER_ID
            assert payload["sequence_count"] == 1
            assert payload["replayed"] is False
            assert payload["journal_digest"] == service._observations.canonical_digest()

            from aero_bench.runtime.contracts import ProviderFinalizationRequest

            receipt = await client.finalize(
                ProviderFinalizationRequest(
                    schema_version="aero-bench.provider-finalization-request/v1",
                    run_id=fixture.resolved_run.run_id,
                    terminal_event="run.completed",
                    terminal_time=AT,
                    event_chain_root="d" * 64,
                )
            )
            assert receipt.artifacts[0].artifact_id == "artifact.logistics"
            artifact = tmp_path / "artifacts" / "logistics/state.json"
            document = json.loads(artifact.read_text(encoding="utf-8"))
            assert document["observation_journal"]["records"][0]["sequence"] == 1
            assert (
                document["observation_journal_digest"]
                == service._observations.canonical_digest()
            )
            journal_record = document["observation_journal"]["records"][0]
            assert journal_record["observation"]["run_id"] == fixture.resolved_run.run_id
            assert (
                journal_record["observation"]["source_stage_barrier_digest"]
                == scene_state.stage_barrier.barrier_digest
            )

    asyncio.run(run())


def test_hook_replay_dedups_journal_and_flags_replayed(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            hook = _build_hook(fixture, client, ledger)
            _sample, scene_state, event, _pad = _closed_stage(fixture)

            await hook.on_stage_barriers_closed(
                (event,),
                scene_state=scene_state,
                stage_barriers=(scene_state.stage_barrier,),
                target=AT,
            )
            digest_after_first = service._observations.canonical_digest()

            await hook.on_stage_barriers_closed(
                (event,),
                scene_state=scene_state,
                stage_barriers=(scene_state.stage_barrier,),
                target=AT,
            )

            assert service._observations.canonical_digest() == digest_after_first
            assert len(service._observations.records) == 1
            event_types = [entry.event.event_type for entry in ledger.records]
            assert event_types == [
                "logistics.observation.ingested",
                "logistics.observation.ingested",
            ]
            replay_payload = {
                item.name: item.value for item in ledger.records[-1].event.payload
            }
            assert replay_payload["replayed"] is True
            assert replay_payload["sequence_count"] == 1
            assert replay_payload["journal_digest"] == digest_after_first

    asyncio.run(run())


def test_hook_rejects_target_differing_from_harness_runtime_hook_time(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            hook = _build_hook(
                fixture,
                client,
                ledger,
                runtime_hook_time=lambda: SimulationTime(tick=2, sim_time_ns=2_000_000_000),
            )
            _sample, scene_state, event, _pad = _closed_stage(fixture)

            with pytest.raises(LogisticsRuntimeHookError, match="Harness runtime hook time"):
                await hook.on_stage_barriers_closed(
                    (event,),
                    scene_state=scene_state,
                    stage_barriers=(scene_state.stage_barrier,),
                    target=AT,
                )
            # Zero mutation: no journal, no ledger event, provider untouched.
            assert service._observations.records == ()
            assert ledger.records == ()
            assert client._last_time == AT

    asyncio.run(run())


def test_hook_rejects_foreign_scene_run_with_zero_mutation(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            hook = _build_hook(fixture, client, ledger)
            _sample, scene_state, event, _pad = _closed_stage(
                fixture, run_id="b" * 64
            )

            with pytest.raises(LogisticsRuntimeHookError, match="another run"):
                await hook.on_stage_barriers_closed(
                    (event,),
                    scene_state=scene_state,
                    stage_barriers=(scene_state.stage_barrier,),
                    target=AT,
                )
            assert service._observations.records == ()
            assert ledger.records == ()

    asyncio.run(run())


def test_hook_never_accepts_a_public_tool_observation(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """No Agent-facing Gateway observation can invent pose/evidence."""
    fixture = logistics_cohesive_fixture
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)

    class _IngestSurface:
        async def ingest_observations(self, batch):
            raise AssertionError("must never be reached")

    hook = LogisticsRuntimeHook(
        run=fixture.resolved_run,
        reader=fixture.reader,
        providers={"logistics.business": _IngestSurface(), "flight": None},
        ledger=ledger,
        runtime_hook_time=lambda: AT,
        package=fixture.package,
        bindings=fixture.bindings,
        scenario=fixture.scenario,
        tolerances=_presence_tolerances(),
        observation_spec=_observation_spec(),
        pose_references=_pose_references(),
        business_provider_id=PROVIDER_ID,
    )
    with pytest.raises(LogisticsRuntimeHookError, match="typed ValidatedObservation"):
        asyncio.run(hook.on_validated_observation({"pose": "a", "evidence": "b"}))


def test_hook_factory_declares_logistics_package_and_creates(
    logistics_cohesive_fixture: _CohesiveFixture,
    tmp_path: Path,
) -> None:
    fixture = logistics_cohesive_fixture

    async def run() -> None:
        async with _running_stack(fixture, tmp_path) as (service, client):
            factory = LogisticsRuntimeHookFactory(
                config=fixture.business_config,
                business_provider_id=PROVIDER_ID,
            )
            assert factory.package_id == LOGISTICS_PACKAGE_ID
            ledger = EventLedger(run_id=fixture.resolved_run.run_id)
            hook = factory.create(
                run=fixture.resolved_run,
                reader=fixture.reader,
                providers={"logistics.business": client, "flight": None},
                ledger=ledger,
                runtime_hook_time=lambda: AT,
            )
            assert isinstance(hook, LogisticsRuntimeHook)
            _sample, scene_state, event, _pad = _closed_stage(fixture)
            await hook.on_stage_barriers_closed(
                (event,),
                scene_state=scene_state,
                stage_barriers=(scene_state.stage_barrier,),
                target=AT,
            )
            assert len(service._observations.records) == 1

    asyncio.run(run())


def test_hook_acknowledges_legitimate_native_validated_observation_without_minting_presence(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """A real Gateway ValidatedObservation is legal camera flow, not a pose API.

    ``on_validated_observation`` is a real generic runtime callback that also
    fires for legitimate camera observations.  A well-formed native observation
    for this run at the authoritative runtime-hook time is acknowledged; no
    physical presence is minted and no journal/ledger mutation happens.
    """
    fixture = logistics_cohesive_fixture
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)

    class _IngestSurface:
        async def ingest_observations(self, batch):
            raise AssertionError("must never be reached")

    hook = _build_hook(fixture, _IngestSurface(), ledger)
    observation = ValidatedObservation(
        run_id=fixture.resolved_run.run_id,
        agent_id="agent.provider",
        provider_id="flight",
        observation_id="camera.frame.1",
        time=AT,
        request_digest="a" * 64,
        payload_digest="b" * 64,
    )

    async def acknowledge() -> None:
        await hook.on_validated_observation(observation)

    asyncio.run(acknowledge())
    assert ledger.records == ()


def test_hook_rejects_foreign_run_or_wrong_time_validated_observation(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """Foreign/wrong-context camera frames are an explicit hook error."""
    fixture = logistics_cohesive_fixture
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)

    class _IngestSurface:
        async def ingest_observations(self, batch):
            raise AssertionError("must never be reached")

    hook = _build_hook(fixture, _IngestSurface(), ledger)

    async def acknowledge(observation: ValidatedObservation) -> None:
        await hook.on_validated_observation(observation)

    foreign = ValidatedObservation(
        run_id="b" * 64,
        agent_id="agent.provider",
        provider_id="flight",
        observation_id="camera.frame.1",
        time=AT,
        request_digest="a" * 64,
        payload_digest="b" * 64,
    )
    with pytest.raises(LogisticsRuntimeHookError, match="another run"):
        asyncio.run(acknowledge(foreign))

    stale = ValidatedObservation(
        run_id=fixture.resolved_run.run_id,
        agent_id="agent.provider",
        provider_id="flight",
        observation_id="camera.frame.1",
        time=SimulationTime(tick=2, sim_time_ns=2_000_000_000),
        request_digest="a" * 64,
        payload_digest="b" * 64,
    )
    with pytest.raises(LogisticsRuntimeHookError, match="authoritative runtime hook time"):
        asyncio.run(acknowledge(stale))

    assert ledger.records == ()


def test_hook_factory_rejects_disabled_observation_config(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """A config whose observation branch is explicitly null cannot build a hook."""
    fixture = logistics_cohesive_fixture
    document = fixture.business_config.model_dump(mode="json")
    document["observation"] = None
    disabled = LogisticsBusinessConfig.model_validate(document)
    factory = LogisticsRuntimeHookFactory(
        config=disabled,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(LogisticsRuntimeHookError, match="observation=null"):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def _dummy_providers() -> dict:
    class _UnusedSurface:
        async def ingest_observations(self, batch):
            raise AssertionError("must never be reached")

    return {"logistics.business": _UnusedSurface(), "flight": None}


def _camera_observation_schema_ref(fixture: _CohesiveFixture) -> FileRef:
    """A real camera-observation schema in the fixture bundle.

    The schema is the one the Gateway dispatcher validates the observation
    envelope payload against (``reader.validate_schema`` via
    ``_validate_named_payload``), so it must be actual bundle bytes, not a
    placeholder.
    """
    return _write_fixture_file(
        fixture.reader.root,
        "schemas/camera-observation.json",
        canonical_json_bytes(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"available": {"type": "boolean"}},
                "required": ["available"],
                "additionalProperties": False,
            }
        )
        + b"\n",
    )


def _camera_granted_run(
    fixture: _CohesiveFixture, *, schema_ref: FileRef
) -> ResolvedRunSpec:
    """A resolved run whose agents additionally hold a camera observation grant.

    The grant names the declared ``flight`` provider and a real schema file in
    the fixture bundle, so a genuine Harness Gateway dispatcher can authorize a
    legitimate camera observation through ``observe`` -> the logistics runtime
    hook.  The run is re-derived through the real resolver binding pipeline
    (``rebuild_run_agents``) exactly as a task/agent contract change would be.
    """
    camera_agent = AgentSpec(
        schema_version="aero-bench.agent/v2",
        agent_id="agent.camera",
        workload=_fixture_runtime("agent.camera"),
        tools=(),
        queries=(),
        observations=(
            ObservationGrant(
                observation_id="camera.frame.1",
                provider_id="flight",
                schema_file=schema_ref,
                timeout_ms=10_000,
            ),
        ),
        artifact_requirements=(),
    )
    return rebuild_run_agents(
        fixture.resolved_run,
        (*fixture.resolved_run.agents, camera_agent),
    )


class _CameraObservationEndpoint:
    """A camera observation Provider endpoint returning one valid envelope."""

    def __init__(self, schema_ref: FileRef) -> None:
        self.schema_ref = schema_ref
        self.calls = 0

    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self.calls += 1
        payload = (NamedValue(name="available", value=True),)
        return ObservationEnvelope(
            run_id=run_id,
            agent_id=agent_id,
            observation_id=observation_id,
            time=requested_at,
            payload_schema=self.schema_ref,
            payload=payload,
            payload_digest=hashlib.sha256(
                canonical_json_bytes({"available": True})
            ).hexdigest(),
        )


def _gateway_dispatcher(
    run: ResolvedRunSpec,
    *,
    fixture: _CohesiveFixture,
    ledger: EventLedger,
    endpoint: _CameraObservationEndpoint,
    hook: RuntimeHook,
) -> GatewayDispatcher:
    """The actual Gateway dispatcher with the logistics runtime hook attached."""
    return GatewayDispatcher(
        run=run,
        bundle_root=fixture.reader.root,
        tool_endpoints={},
        observation_endpoints={"flight": endpoint},
        authoritative_time=lambda: AT,
        ledger=ledger,
        runtime_hook=hook,
    )


def _dispatch_camera_observation(
    dispatcher: GatewayDispatcher, run: ResolvedRunSpec
) -> ObservationEnvelope:
    return asyncio.run(
        dispatcher.observe(
            AgentPrincipal(
                run_id=run.run_id,
                agent_id="agent.camera",
                authentication_id="transport.session",
            ),
            observation_id="camera.frame.1",
            requested_at=AT,
        )
    )


def _observation_failure_payloads(
    ledger: EventLedger,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {item.name: item.value for item in record.event.payload}
        for record in ledger.records
        if record.event.event_type == "gateway.failure"
    )


def test_hook_factory_rejects_config_digest_mismatch(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """The factory proves the declared config equals the run's pinned config.

    A config whose typed content differs from the config file the resolved run
    pins for the business provider (here the pose-reference calibration is
    drifted) is rejected before any hook exists — the BundleReader digests the
    declared config bytes and the typed load must equal the requested config
    exactly.
    """
    fixture = logistics_cohesive_fixture
    document = fixture.business_config.model_dump(mode="json")
    document["observation"]["pose_references"] = [
        {
            "aircraft_id": "fleet-alpha:1",
            "pose_reference_above_contact_m": 0.11,
        }
    ]
    drifted = LogisticsBusinessConfig.model_validate(document)
    factory = LogisticsRuntimeHookFactory(
        config=drifted,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError,
        match="config does not equal the declared provider config file",
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_same_id_different_order_task_package(
    tmp_path_factory,
) -> None:
    """Same task_id/package_id/scene_id, different ORDER content is rejected.

    The business-config nested ``task_package`` is allowed to keep the exact
    same IDs while its order details diverge from the authoritative
    ``run.task.package`` loaded from the bundle; the native factory must load the
    actual task package through the digest-verified reader and require FULL
    canonical equality, never identity-only agreement.
    """
    fixture = _divergent_business_fixture(
        tmp_path_factory.mktemp("factory-divergent-order"),
        mutate_package=_divergent_order_document,
    )
    assert (
        fixture.business_config.task_package.task_id
        == fixture.task.task_id
        == "logistics.task"
    )
    assert (
        fixture.business_config.task_package.package_id
        == fixture.task.package.package_id
        == LOGISTICS_PACKAGE_ID
    )
    assert (
        fixture.business_config.task_package.scene.scene_id
        == fixture.package.scene.scene_id
    )
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError,
        match=r"task_package does not equal the resolved run task package "
        r"loaded from the bundle",
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_same_id_different_profile_task_package(
    tmp_path_factory,
) -> None:
    """Same task_id/package_id/scene_id, different PERFORMANCE PROFILE is rejected.

    A business-config nested ``task_package`` whose aircraft body footprint
    (``aircraft_body.x_m``) differs from the authoritative task package keeps
    the same identity but diverges in the body dimensions the physical-ingress
    service binds its presence profiles to; the factory must reject it.
    """
    fixture = _divergent_business_fixture(
        tmp_path_factory.mktemp("factory-divergent-profile"),
        mutate_package=_divergent_profile_document,
    )
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError,
        match=r"task_package does not equal the resolved run task package "
        r"loaded from the bundle",
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_absent_declared_config_file(tmp_path_factory) -> None:
    """A missing declared provider config file is rejected by the digest reader."""
    fixture = build_fixture(tmp_path_factory.mktemp("factory-absent-config"))
    (fixture.reader.root / "configs/providers/logistics-business.json").unlink()
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError, match="cannot digest-verify/type"
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_tampered_declared_config_file(tmp_path_factory) -> None:
    """A tampered declared provider config file is rejected by the digest reader."""
    fixture = build_fixture(tmp_path_factory.mktemp("factory-tampered-config"))
    path = fixture.reader.root / "configs/providers/logistics-business.json"
    tampered = fixture.business_config.model_dump(mode="json")
    tampered["observation"]["pose_references"] = [
        {"aircraft_id": "fleet-alpha:1", "pose_reference_above_contact_m": 0.11}
    ]
    path.write_bytes(canonical_json_bytes(tampered) + b"\n")
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError, match="cannot digest-verify/type"
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_absent_declared_schema_file(tmp_path_factory) -> None:
    """A missing declared provider schema file is rejected by the digest reader."""
    fixture = build_fixture(tmp_path_factory.mktemp("factory-absent-schema"))
    (fixture.reader.root / "schemas/providers/logistics-business.json").unlink()
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError, match="cannot digest-verify/type"
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_tampered_declared_schema_file(tmp_path_factory) -> None:
    """A tampered declared provider schema file is rejected by the digest reader."""
    fixture = build_fixture(tmp_path_factory.mktemp("factory-tampered-schema"))
    path = fixture.reader.root / "schemas/providers/logistics-business.json"
    path.write_bytes(b"{\"not\": \"a json schema\"}\n")
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError, match="cannot digest-verify/type"
    ):
        factory.create(
            run=fixture.resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_hook_factory_rejects_mismatched_provider_adapter(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """Provider identity is not enough: the adapter must also match precisely.

    A run that declares the logistics business provider identity under a foreign
    adapter (with a matching foreign workload implementation so the strict
    ProviderRef validator accepts it) cannot build the native hook.
    """
    fixture = logistics_cohesive_fixture
    existing = next(
        provider
        for provider in fixture.environment.providers
        if provider.provider_id == PROVIDER_ID
    )
    foreign = existing.model_copy(
        update={
            "adapter": "logistics.business.preview",
            "workload": _fixture_runtime("logistics.business.preview"),
        }
    )
    providers = tuple(
        foreign if provider.provider_id == PROVIDER_ID else provider
        for provider in fixture.environment.providers
    )
    environment = fixture.environment.model_copy(update={"providers": providers})
    resolved_run = _build_resolved_run(
        task=fixture.task,
        environment=environment,
        agents=fixture.agents,
        scenario=fixture.scenario,
        artifact_requirements=fixture.artifact_requirements,
    )
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    ledger = EventLedger(run_id=resolved_run.run_id)
    with pytest.raises(
        LogisticsRuntimeHookError,
        match="provider identity AND adapter",
    ):
        factory.create(
            run=resolved_run,
            reader=fixture.reader,
            providers=_dummy_providers(),
            ledger=ledger,
            runtime_hook_time=lambda: AT,
        )


def test_gateway_dispatch_accepts_valid_camera_observation_without_failure_event(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """A valid camera observation through the REAL Gateway dispatch is not a failure.

    The Gateway's ``observe`` -> ``_notify_validated_observation`` path calls
    ``LogisticsRuntimeHook.on_validated_observation`` for every validated
    Gateway observation.  A genuine camera observation of the same run at the
    authoritative runtime-hook time is acknowledged: no ``gateway.failure``
    event (``runtime_hook_failed``) is recorded, no facility presence is minted
    and no ``logistics.observation.ingested`` event or business journal write
    occurs.
    """
    fixture = logistics_cohesive_fixture
    schema_ref = _camera_observation_schema_ref(fixture)
    run = _camera_granted_run(fixture, schema_ref=schema_ref)
    ledger = EventLedger(run_id=run.run_id)
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    hook = factory.create(
        run=run,
        reader=fixture.reader,
        providers=_dummy_providers(),
        ledger=ledger,
        runtime_hook_time=lambda: AT,
    )
    endpoint = _CameraObservationEndpoint(schema_ref)
    dispatcher = _gateway_dispatcher(
        run,
        fixture=fixture,
        ledger=ledger,
        endpoint=endpoint,
        hook=hook,
    )

    envelope = _dispatch_camera_observation(dispatcher, run)

    assert envelope.observation_id == "camera.frame.1"
    assert endpoint.calls == 1
    event_types = [record.event.event_type for record in ledger.records]
    assert "observation.requested" in event_types
    assert "observation.validated" in event_types
    # The valid camera callback must NEVER be converted into an observation
    # failure event by the Gateway's try/except dispatch.
    assert "gateway.failure" not in event_types
    assert _observation_failure_payloads(ledger) == ()
    # And it must not mint facility presence nor write the logistics journal.
    assert "logistics.observation.ingested" not in event_types
    assert ledger.records
    ledger.verify()


def test_gateway_dispatch_refuses_wrong_time_camera_observation(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """A camera observation not at the authoritative runtime-hook time is refused.

    The Gateway dispatch validates the envelope at the authoritative simulation
    time, then the logistics hook rejects the callback whose time differs from
    its own authoritative runtime-hook time; the Gateway records exactly one
    ``gateway.failure`` event with ``failure_class=runtime_hook_failed`` and
    raises.  No presence or journal mutation happens.
    """
    fixture = logistics_cohesive_fixture
    schema_ref = _camera_observation_schema_ref(fixture)
    run = _camera_granted_run(fixture, schema_ref=schema_ref)
    ledger = EventLedger(run_id=run.run_id)
    factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    hook = factory.create(
        run=run,
        reader=fixture.reader,
        providers=_dummy_providers(),
        ledger=ledger,
        runtime_hook_time=lambda: SimulationTime(tick=2, sim_time_ns=2_000_000_000),
    )
    dispatcher = _gateway_dispatcher(
        run,
        fixture=fixture,
        ledger=ledger,
        endpoint=_CameraObservationEndpoint(schema_ref),
        hook=hook,
    )

    with pytest.raises(GatewayDispatchError, match="validated observation runtime hook failed"):
        _dispatch_camera_observation(dispatcher, run)

    failures = _observation_failure_payloads(ledger)
    assert len(failures) == 1
    assert failures[0]["failure_class"] == "runtime_hook_failed"
    assert failures[0]["observation_id"] == "camera.frame.1"
    event_types = [record.event.event_type for record in ledger.records]
    assert "observation.validated" in event_types
    assert "logistics.observation.ingested" not in event_types
    ledger.verify()


def test_gateway_dispatch_refuses_foreign_run_camera_observation(
    logistics_cohesive_fixture: _CohesiveFixture,
) -> None:
    """A camera observation dispatched for the WRONG run is refused.

    A Gateway dispatcher bound to one run that is wired to a logistics hook
    bound to another run means every validated camera callback is foreign; the
    hook rejects it and the Gateway converts the refusal into exactly one
    ``gateway.failure`` event with ``failure_class=runtime_hook_failed``.
    """
    fixture = logistics_cohesive_fixture
    schema_ref = _camera_observation_schema_ref(fixture)
    run = _camera_granted_run(fixture, schema_ref=schema_ref)
    foreign_ledger = EventLedger(run_id=fixture.resolved_run.run_id)
    foreign_factory = LogisticsRuntimeHookFactory(
        config=fixture.business_config,
        business_provider_id=PROVIDER_ID,
    )
    foreign_hook = foreign_factory.create(
        run=fixture.resolved_run,
        reader=fixture.reader,
        providers=_dummy_providers(),
        ledger=foreign_ledger,
        runtime_hook_time=lambda: AT,
    )
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = _gateway_dispatcher(
        run,
        fixture=fixture,
        ledger=ledger,
        endpoint=_CameraObservationEndpoint(schema_ref),
        hook=foreign_hook,
    )

    with pytest.raises(GatewayDispatchError, match="validated observation runtime hook failed"):
        _dispatch_camera_observation(dispatcher, run)

    failures = _observation_failure_payloads(ledger)
    assert len(failures) == 1
    assert failures[0]["failure_class"] == "runtime_hook_failed"
    assert failures[0]["observation_id"] == "camera.frame.1"
    event_types = [record.event.event_type for record in ledger.records]
    assert "observation.validated" in event_types
    assert "logistics.observation.ingested" not in event_types
    ledger.verify()


def test_logistics_runtime_available_stays_false() -> None:
    """No fabricated motion: the native logistics runtime remains unavailable."""
    from aero_bench.tasks.logistics.integration import (
        LOGISTICS_RUNTIME_IMPLEMENTED,
        LogisticsTaskPackageResolver,
    )

    assert LOGISTICS_RUNTIME_IMPLEMENTED is False
    resolver = LogisticsTaskPackageResolver()
    assert resolver.runtime_available is False


__all__ = [
    "AT",
    "SERVICE_PORT",
    "_CohesiveFixture",
    "_build_hook",
    "_closed_stage",
    "_derive_observation_batch",
    "_observation_spec",
    "_pose_references",
    "_presence_tolerances",
    "_running_stack",
    "build_fixture",
]
