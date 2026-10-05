"""Real-logistics package fixture for executor materialization tests.

The fixture builds a *fully valid* ``aero-bench.resolved-run/v5``
``ResolvedRunSpec`` through the real baseline machinery: a byte-real world
package is materialized with ``tests.world.support.materialize_world_package``,
the logistics package document is lowered against that world (scene binding to
the world's authoritative asset digest and WGS84 origin at 43.0 m), the task is
a real lowered ``TaskSpec`` written into the same bundle root, and the resolved
scenario is produced by ``compile_resolved_scenario`` from the logistics
``LogisticsTaskPackageResolver`` projection.  ``resolve`` returns the honest
infeasible feasibility (runtime barrier off); the run's identity digest binds
exactly the canonical payload.  No fake or reconstructed resolved surface is
involved.
"""

from __future__ import annotations

import hashlib
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from aero_bench.config.models import (
    AgentSpec,
    ArtifactRequirement,
    ClockSpec,
    EnvironmentSpec,
    FileRef,
    GatewaySpec,
    GoalSpec,
    ImplementationIdentity,
    ProviderRef,
    ResourceBudget,
    RuntimeImage,
    RuntimeSpec,
    SchemaBoundFile,
    TaskPackageRef,
    TaskSpec,
    VerifierSpec,
)
from aero_bench.config.resolver import ResolvedRunSpec, _derive_artifact_requirements
from aero_bench.executor.contracts import (
    ExecutionPlan,
    InlineInputPlan,
    WorkloadPlan,
)
from aero_bench.executor.facility_world import (
    DEFAULT_LOGISTICS_WORLD_DESTINATION,
    materialize_logistics_world,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.integration import (
    LOGISTICS_FLIGHT_CAPABILITIES,
    LogisticsTaskPackageResolver,
)
from aero_bench.world.contracts import ProviderRequirement
from aero_bench.world.resolved import compile_resolved_scenario
from aero_bench.world.scene_compiler import SceneOrigin
from aero_bench.providers.registry import ProviderRegistration, ProviderRegistry
from tests.support import FixtureProviderConfig, fixture_provider_registry
from tests.world.support import frame_origin, materialize_world_package, provider_capabilities

#: Resolved scene origin values the package scene binding declares (WGS84).
_DEFAULT_FRAME_ORIGIN = frame_origin()
SCENE_LATITUDE_DEG = _DEFAULT_FRAME_ORIGIN.latitude_deg
SCENE_LONGITUDE_DEG = _DEFAULT_FRAME_ORIGIN.longitude_deg
SCENE_ALTITUDE_M = float(_DEFAULT_FRAME_ORIGIN.altitude_m)
SCENE_SOURCE_SHA256 = "ab" * 32

#: Real PX4/Gazebo flight Provider in the logistics run environment.
PX4_PROVIDER_ID = "flight"

_LAUNCH_SITE_ID = "launch.alpha"


# Synthetic adapters used by this fixture's non-flight Providers. They are not
# production adapters, so each one is registered explicitly with its stage.
_FIXTURE_ADAPTER_STAGES = (
    ("flight.gazebo", "motion"),
    ("perception.camera", "business_environment"),
    ("radio.wifi", "network"),
    ("traffic.sumo", "motion"),
)


def logistics_fixture_provider_registry() -> ProviderRegistry:
    """Builtin registry plus explicit registrations for the synthetic adapters."""
    registry = fixture_provider_registry()

    def unused_builder(*args, **kwargs):
        raise AssertionError("this fixture only projects registered runtime stages")

    for adapter, stage in _FIXTURE_ADAPTER_STAGES:
        registry.register(ProviderRegistration(
            adapter=adapter,
            runtime_stage=stage,
            config_model=FixtureProviderConfig,
            session_builder=unused_builder,
        ))
    return registry


def _placeholder_ref(path: str) -> FileRef:
    return FileRef(path=path, sha256="a1" * 32)


def _fixture_runtime(component_id: str) -> RuntimeSpec:
    return RuntimeSpec(
        runtime=RuntimeImage(
            image=(
                f"aero-bench/{component_id}@sha256:"
                f"{hashlib.sha256(component_id.encode('utf-8')).hexdigest()}"
            ),
            command=("python", "-m", component_id),
        ),
        resources=ResourceBudget(cpu_millicores=100, memory_mib=64, gpu_count=0),
        implementation=ImplementationIdentity(
            component_id=component_id,
            kind="mechanical_fixture",
            source_uri="https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
            source_revision="34" * 20,
            version="fixture.1",
        ),
    )


def _harness_artifact(*, artifact_id: str, artifact_type: str, relative_path: str) -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        producer_id="harness",
        visibility="private",
        relative_path=relative_path,
        max_size_bytes=1_048_576,
        source_asset_id=None,
    )


def _verification_output() -> ArtifactRequirement:
    return ArtifactRequirement(
        artifact_id="logistics.verification",
        artifact_type="verification.report",
        producer_id="logistics.verifier",
        visibility="public",
        relative_path="verification/logistics.json",
        max_size_bytes=4096,
        source_asset_id=None,
    )


def _logistics_package_schema() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "schema_version": {"const": "aero-bench.logistics-task/v1"},
            "package_id": {"const": LOGISTICS_PACKAGE_ID},
            "task_id": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"},
            "verifier_id": {"type": "string", "pattern": "^[a-z][a-z0-9_.-]*$"},
            "scene": {"type": "object"},
            "facilities": {"type": "array"},
            "fleet": {"type": "array"},
            "performanceProfiles": {"type": "array"},
            "orders": {"type": "array"},
            "noFlyZones": {"type": "array"},
            "actors": {"type": "array"},
        },
        "required": [
            "schema_version",
            "package_id",
            "task_id",
            "verifier_id",
            "scene",
            "facilities",
            "fleet",
            "performanceProfiles",
            "orders",
            "noFlyZones",
            "actors",
        ],
        "additionalProperties": False,
    }


def _write_fixture_file(root: Path, path: str, payload: bytes) -> FileRef:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return FileRef(path=path, sha256=hashlib.sha256(payload).hexdigest())


def _build_task(root: Path, package_document: dict[str, object]) -> TaskSpec:
    """A byte-real lowered logistics TaskSpec pinned to package files on ``root``."""
    config_ref = _write_fixture_file(
        root,
        "tasks/logistics-package.json",
        canonical_json_bytes(package_document) + b"\n",
    )
    schema_ref = _write_fixture_file(
        root,
        "schemas/logistics-package.schema.json",
        canonical_json_bytes(_logistics_package_schema()) + b"\n",
    )
    instruction_ref = _write_fixture_file(
        root,
        "instructions/logistics-task.txt",
        b"execute the logistics plan\n",
    )
    verifier_id = str(package_document["verifier_id"])
    return TaskSpec(
        schema_version="aero-bench.task/v1",
        task_id=str(package_document["task_id"]),
        package=TaskPackageRef(
            package_id=LOGISTICS_PACKAGE_ID,
            config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        ),
        instruction=instruction_ref,
        required_capabilities=(),
        required_tools=(),
        assets=(),
        goals=(
            GoalSpec(
                goal_id="logistics.goal",
                verifier_id=verifier_id,
                metric_id="logistics.delivery_rate",
                operator="ge",
                threshold=1.0,
                evidence="authoritative_state",
                parameters=(),
            ),
        ),
        verifier=VerifierSpec(
            verifier_id=verifier_id,
            workload=_fixture_runtime(verifier_id),
            config=SchemaBoundFile(
                file=_placeholder_ref("configs/logistics-verifier.json"),
                schema_file=_placeholder_ref("schemas/logistics-verifier.json"),
            ),
            artifact_requirements=(),
            output_artifacts=(_verification_output(),),
        ),
    )


def _build_agents() -> tuple[AgentSpec, ...]:
    return (
        AgentSpec(
            schema_version="aero-bench.agent/v2",
            agent_id="logistics.agent",
            workload=_fixture_runtime("logistics.agent"),
            tools=(),
            queries=(),
            observations=(),
            artifact_requirements=(),
        ),
    )


def _capability_map(environment: EnvironmentSpec) -> dict[str, tuple[str, ...]]:
    return {
        provider.provider_id: provider.capabilities
        for provider in environment.providers
    }


def _build_environment(
    *,
    flight_adapter: str,
    extra_px4_provider_id: str | None,
) -> EnvironmentSpec:
    """The shared world environment with the real PX4/Gazebo flight Provider.

    The ``flight`` Provider uses the existing ``px4.gazebo`` adapter and
    declares the real ``flight.command`` / ``observation.capture`` capabilities
    on top of the world-required flight capabilities, so the environment still
    compiles against the shared test world.
    """
    adapters = {
        "flight": flight_adapter,
        "perception": "perception.camera",
        "radio": "radio.wifi",
        "traffic": "traffic.sumo",
    }
    capabilities = dict(provider_capabilities())
    if flight_adapter == "px4.gazebo":
        capabilities["flight"] = (
            *capabilities["flight"],
            *sorted(LOGISTICS_FLIGHT_CAPABILITIES),
        )
    providers: list[ProviderRef] = []
    for index, (provider_id, provider_caps) in enumerate(capabilities.items()):
        providers.append(
            ProviderRef(
                provider_id=provider_id,
                adapter=adapters[provider_id],
                port=17601 + index,
                workload=_fixture_runtime(adapters[provider_id]),
                config=SchemaBoundFile(
                    file=_placeholder_ref(f"configs/providers/{provider_id}.json"),
                    schema_file=_placeholder_ref(
                        f"schemas/providers/{provider_id}.json"
                    ),
                ),
                protocol_schema=_placeholder_ref(
                    f"schemas/protocol/{provider_id}.json"
                ),
                capabilities=provider_caps,
                artifact_requirements=(),
            )
        )
    if extra_px4_provider_id is not None:
        providers.append(
            ProviderRef(
                provider_id=extra_px4_provider_id,
                adapter="px4.gazebo",
                port=17602,
                workload=_fixture_runtime("px4.gazebo"),
                config=SchemaBoundFile(
                    file=_placeholder_ref(
                        f"configs/providers/{extra_px4_provider_id}.json"
                    ),
                    schema_file=_placeholder_ref(
                        f"schemas/providers/{extra_px4_provider_id}.json"
                    ),
                ),
                protocol_schema=_placeholder_ref(
                    f"schemas/protocol/{extra_px4_provider_id}.json"
                ),
                capabilities=("flight.command", "gazebo.extra", "observation.capture"),
                artifact_requirements=(),
            )
        )
    return EnvironmentSpec(
        schema_version="aero-bench.environment/v1",
        environment_id="logistics.env",
        harness=_fixture_runtime("aero-bench.harness"),
        clock=ClockSpec(
            authority="provider_barrier",
            step_ns=1_000_000_000,
            max_steps=1000,
            provider_timeout_ms=5000,
        ),
        gateway=GatewaySpec(
            protocol_schema=_placeholder_ref("schemas/gateway.json"),
            port=17501,
        ),
        providers=tuple(providers),
        harness_artifact_requirements=(
            _harness_artifact(
                artifact_id="harness.event-log",
                artifact_type="event.log",
                relative_path="harness/event-log.json",
            ),
            _harness_artifact(
                artifact_id="harness.plan",
                artifact_type="run.plan",
                relative_path="harness/plan.json",
            ),
        ),
    )


def logistics_task(
    *,
    park_slots: int = 2,
    hub_slots: int = 2,
    charge_slots: int = 3,
) -> tuple[TaskSpec, tuple[ArtifactRequirement, ...]]:
    """A real lowered logistics TaskSpec pinned to the fixture package document."""
    package_document = _package_document(
        park_slots=park_slots, hub_slots=hub_slots, charge_slots=charge_slots
    )
    config = SchemaBoundFile(
        file=_placeholder_ref("tasks/logistics-package.json"),
        schema_file=_placeholder_ref("schemas/logistics-package.schema.json"),
    )
    return (
        TaskSpec(
            schema_version="aero-bench.task/v1",
            task_id=str(package_document["task_id"]),
            package=TaskPackageRef(
                package_id=LOGISTICS_PACKAGE_ID,
                config=config,
            ),
            instruction=_placeholder_ref("instructions/logistics-task.txt"),
            required_capabilities=(),
            required_tools=(),
            assets=(),
            goals=(
                GoalSpec(
                    goal_id="logistics.goal",
                    verifier_id=str(package_document["verifier_id"]),
                    metric_id="logistics.delivery_rate",
                    operator="ge",
                    threshold=1.0,
                    evidence="authoritative_state",
                    parameters=(),
                ),
            ),
            verifier=VerifierSpec(
                verifier_id=str(package_document["verifier_id"]),
                workload=_fixture_runtime(str(package_document["verifier_id"])),
                config=SchemaBoundFile(
                    file=_placeholder_ref("configs/logistics-verifier.json"),
                    schema_file=_placeholder_ref("schemas/logistics-verifier.json"),
                ),
                artifact_requirements=(),
                output_artifacts=(_verification_output(),),
            ),
        ),
        tuple(
            [
                _harness_artifact(
                    artifact_id="harness.event-log",
                    artifact_type="event.log",
                    relative_path="harness/event-log.json",
                ),
                _harness_artifact(
                    artifact_id="harness.plan",
                    artifact_type="run.plan",
                    relative_path="harness/plan.json",
                ),
            ]
        ),
    )


def _mutate_world_for_extra_provider(
    kwargs: dict[str, object], _root: Path, extra_px4_provider_id: str
) -> None:
    provider_requirements = list(kwargs["provider_requirements"])
    provider_requirements.append(
        ProviderRequirement(
            provider_id=extra_px4_provider_id,
            roles=("motion",),
            required_capability_ids=("gazebo.extra",),
        )
    )
    provider_requirements.sort(key=lambda requirement: requirement.provider_id)
    kwargs["provider_requirements"] = tuple(provider_requirements)
    # A motion-stage Provider must own a dynamic entity, so the extra px4
    # Provider owns a second UAV.
    template = next(
        entity for entity in kwargs["entities"]
        if entity.state == "dynamic" and entity.provider_id == "flight"
    )
    kwargs["entities"] = (
        *kwargs["entities"],
        template.model_copy(
            update={
                "entity_id": f"uav.{extra_px4_provider_id}",
                "provider_id": extra_px4_provider_id,
                "pose": template.pose.model_copy(
                    update={"east_m": template.pose.east_m + 30.0}
                ),
            }
        ),
    )


def logistics_resolved_run(
    *,
    park_slots: int = 2,
    hub_slots: int = 2,
    charge_slots: int = 3,
    seed: int = 7,
    executor_kind: str = "docker_reference",
    flight_adapter: str = "px4.gazebo",
    extra_px4_provider_id: str | None = None,
) -> ResolvedRunSpec:
    """A real, fully-valid ``aero-bench.resolved-run/v5`` logistics ResolvedRun.

    The environment declares exactly one real ``px4.gazebo`` flight Provider
    (the adapter the facility-spliced world is staged into).  The resolved
    scenario is compiled by the real ``compile_resolved_scenario`` from a
    byte-real world package; feasibility is the honest infeasible assessment
    returned by the real logistics resolver (the deployed runtime barrier is
    off).  ``extra_px4_provider_id`` materializes a second px4 provider for the
    seam's ambiguous-provider rejection test.
    """
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        mutate_kwargs = None
        if extra_px4_provider_id is not None:
            def mutate_kwargs(kwargs: dict[str, object], root_path: Path) -> None:
                _mutate_world_for_extra_provider(
                    kwargs, root_path, extra_px4_provider_id
                )
        reader, world_ref, world = materialize_world_package(
            root, mutate_kwargs=mutate_kwargs
        )
        package_document = _package_document(
            park_slots=park_slots,
            hub_slots=hub_slots,
            charge_slots=charge_slots,
            scene_source_sha256=world.asset_digest,
        )
        task = _build_task(root, package_document)
        agents = _build_agents()
        environment = _build_environment(
            flight_adapter=flight_adapter,
            extra_px4_provider_id=extra_px4_provider_id,
        )
        resolver = LogisticsTaskPackageResolver()
        projection = resolver.scenario_projection(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
        )
        scenario = compile_resolved_scenario(
            reader,
            world_ref,
            _LAUNCH_SITE_ID,
            seed,
            _capability_map(environment),
            {provider.provider_id: logistics_fixture_provider_registry().runtime_stage_for(provider.adapter) for provider in environment.providers},
            task,
            agents,
            projection,
        )
        assessment = resolver.resolve(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
            scenario=scenario,
        )
        agent_models = [agent.model_dump(mode="json") for agent in agents]
        payload = {
            "schema_version": "aero-bench.resolved-run/v5",
            "executor_kind": executor_kind,
            "execution_scope": "executor_validation",
            "suite": _placeholder_ref("suite.yaml").model_dump(mode="json"),
            "suite_id": "logistics.suite",
            "case_id": "logistics.basic",
            "launch_site_id": _LAUNCH_SITE_ID,
            "seed": seed,
            "scenario": scenario.model_dump(mode="json"),
            "task": task.model_dump(mode="json"),
            "environment": environment.model_dump(mode="json"),
            "agents": agent_models,
            "feasibility": assessment.model_dump(mode="json"),
            "artifact_requirements": [
                item.model_dump(mode="json")
                for item in _derive_artifact_requirements(task, environment, agents)
            ],
            "verification_outputs": [
                item.model_dump(mode="json")
                for item in task.verifier.output_artifacts
            ],
            "overrides": [],
        }
        run_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        run = ResolvedRunSpec.model_validate({**payload, "run_id": run_id})
        # The canonical payload is exactly what the run re-emits; the immutable
        # run's identity digest binds precisely that canonical surface.
        assert run.model_dump(mode="json", exclude={"run_id"}) == payload
        return run


def base_execution_plan(run: ResolvedRunSpec, *, bundle_root: str) -> ExecutionPlan:
    """A strict base ExecutionPlan carrying the run's declared workloads.

    Mirrors the WorkloadPlan conventions of the real
    ``aero_bench.executor.planning.build_execution_plan`` (contract inline
    input + pinned bundle inputs) so the seam consumes the same immutable plan
    surface the Docker/Kubernetes executors stage from.  The logistics suite
    barrier cannot yet run ``build_execution_plan`` end-to-end (the
    “next blocker” in the report), so the base plan is assembled from the
    strict models directly; ``apply_logistics_world_materialization`` is the
    exact function the planning branch invokes on the base plan.
    """

    def contract(workload_id: str) -> InlineInputPlan:
        content = (
            canonical_json_bytes({"workload_id": workload_id}).decode("utf-8") + "\n"
        )
        return InlineInputPlan(
            destination="contract.json",
            content_utf8=content,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )

    runtime_workloads: list[WorkloadPlan] = [
        WorkloadPlan(
            workload_id="harness",
            role="harness",
            phase="runtime",
            workload=run.environment.harness,
            contract=contract("harness"),
            bundle_inputs=(),
        )
    ]
    for provider in run.environment.providers:
        runtime_workloads.append(
            WorkloadPlan(
                workload_id=provider.provider_id,
                role="provider",
                phase="runtime",
                workload=provider.workload,
                contract=contract(provider.provider_id),
                bundle_inputs=(),
            )
        )
    for agent in run.agents:
        runtime_workloads.append(
            WorkloadPlan(
                workload_id=agent.agent_id,
                role="agent",
                phase="runtime",
                workload=agent.workload,
                contract=contract(agent.agent_id),
                bundle_inputs=(),
            )
        )
    verifier = run.task.verifier
    return ExecutionPlan(
        run=run,
        executor_kind=run.executor_kind,
        bundle_root=bundle_root,
        runtime_workloads=tuple(runtime_workloads),
        verifier_workload=WorkloadPlan(
            workload_id=verifier.verifier_id,
            role="verifier",
            phase="verification",
            workload=verifier.workload,
            contract=contract(verifier.verifier_id),
            bundle_inputs=(),
        ),
    )


def _vertiport(
    facility_id: str, x: float, z: float, parking_slots: int
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": facility_id,
        "name": facility_id,
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": x, "z": z},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": parking_slots, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    return value


def _hub(
    facility_id: str, x: float, z: float, parking_slots: int
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": facility_id,
        "name": facility_id,
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": x, "z": z},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": parking_slots, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50},
        "charging": None,
    }
    return value


def _charger(
    facility_id: str, x: float, z: float, charging_slots: int
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": facility_id,
        "name": facility_id,
        "kind": "charger",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": x, "z": z},
        "rotationDeg": 0,
        "widthM": 10,
        "depthM": 8,
        "heightM": 3.2,
        "landing": None,
        "cargo": None,
        "charging": {
            "slots": charging_slots,
            "powerW": 5000,
            "priceAmount": 1.2,
            "priceCurrency": "CNY",
            "priceUnit": "kWh",
        },
    }
    return value


def _package_document(
    *,
    hub_slots: int = 2,
    park_slots: int = 3,
    charge_slots: int = 1,
    scene_source_sha256: str = SCENE_SOURCE_SHA256,
) -> dict[str, object]:
    """A real, valid logistics package with four widely separated facilities."""
    facilities = [
        _vertiport("facility-1", 10, -20, park_slots),
        _vertiport("facility-2", 160, 90, park_slots),
        _hub("hub-3", -60, 40, hub_slots),
        _charger("charger-x", 40, -140, charge_slots),
    ]
    return {
        "schema_version": "aero-bench.logistics-task/v1",
        "package_id": "logistics.task.v1",
        "task_id": "logistics.task",
        "verifier_id": "logistics.verifier",
        "scene": {
            "scene_id": "world.city-demo",
            "coordinate_frame": "scene_east_south_m",
            "origin_latitude_deg": SCENE_LATITUDE_DEG,
            "origin_longitude_deg": SCENE_LONGITUDE_DEG,
            "origin_altitude_m": SCENE_ALTITUDE_M,
            "scene_source_sha256": scene_source_sha256,
        },
        "facilities": facilities,
        "fleet": [
            {
                "id": "fleet-alpha",
                "assetId": "model:logistics-drone-v1",
                "count": 1,
                "homeFacilityId": "facility-1",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        "performanceProfiles": [
            {
                "fleetEntryId": "fleet-alpha",
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
                "cruiseSpeedMps": 15,
                "cruisePowerW": 900,
                "hoverPowerW": 700,
                "chargeEfficiency": 0.85,
            }
        ],
        "orders": [
            {
                "id": "order-1",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 600,
            }
        ],
        "noFlyZones": [
            {
                "id": "nofly.city-center",
                "name": "City center keep-out",
                "polygon": [
                    {"x": 10, "z": -20},
                    {"x": 30, "z": -20},
                    {"x": 20, "z": -40},
                ],
                "floorM": 0,
                "ceilingM": 120,
                "startsAtS": 0,
                "endsAtS": None,
                "source": {"kind": "manual", "label": "planner", "uri": None},
            }
        ],
        "actors": [
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    }


def logistics_package(
    *,
    hub_slots: int = 2,
    park_slots: int = 3,
    charge_slots: int = 1,
) -> LogisticsTaskPackage:
    return lower_logistics_task_package(
        _package_document(
            hub_slots=hub_slots, park_slots=park_slots, charge_slots=charge_slots
        )
    )


def base_world_sdf_bytes(*, origin) -> bytes:
    """A compact base world that declares the resolved SceneOrigin coordinates."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<sdf version="1.10">\n'
        '  <world name="compiled_urban_scene">\n'
        "    <spherical_coordinates>\n"
        "      <surface_model>EARTH_WGS84</surface_model>\n"
        "      <world_frame_orientation>ENU</world_frame_orientation>\n"
        f"      <latitude_deg>{float(origin.latitude_deg)}</latitude_deg>\n"
        f"      <longitude_deg>{float(origin.longitude_deg)}</longitude_deg>\n"
        f"      <elevation>{float(origin.amsl_m)}</elevation>\n"
        "      <heading_deg>0</heading_deg>\n"
        "    </spherical_coordinates>\n"
        "    <paused>false</paused>\n"
        '    <physics type="ode">\n'
        "      <max_step_size>0.001</max_step_size>\n"
        "      <real_time_factor>1</real_time_factor>\n"
        "    </physics>\n"
        '    <model name="ground"><static>true</static>'
        '<link name="ground_link">'
        '<collision name="ground_collision"><geometry><plane>'
        '<normal>0 0 1</normal><size>1000 1000</size></plane></geometry></collision>'
        "</link></model>\n"
        "  </world>\n"
        "</sdf>\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------- #
# Persistent end-to-end logistics bundle (resolve_suite reproduces the run)
# --------------------------------------------------------------------------- #

#: PX4/Gazebo/MAVSDK pins of the candidate-owned container service
#: (``containers/px4-gazebo/service.py``).  The e2e test asserts the bundle's
#: provider config carries exactly these identities, so any service pin change
#: is caught explicitly instead of silently diverging.
_PX4_PIN = {
    "version": "v1.17.0-alpha1-1551-g381149fb01",
    "commit": "381149fb012762f5e38c4a7fdc1b905b28038970",
}
_GAZEBO_PIN = {
    "version": "8.11.0",
    "commit": "1be3cc376fec778cc725b4eeea463245affa56d3",
}
_MAVSDK_PIN = {
    "version": "3.17.2",
    "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced",
}


def _logistics_vehicle() -> dict[str, object]:
    return {
        "vehicle_id": "logistics_drone_1",
        "system_id": 1,
        "mavsdk_udp_port": 14540,
        "px4_mavlink_udp_port": 14570,
        "mavsdk_grpc_port": 50051,
        "sys_autostart": 4001,
        "model": "gz_x500_mono_cam",
        "gazebo_model_name": "x500_mono_cam_0",
        "gazebo_resource": "x500_mono_cam",
        "initial_pose": {
            "x_m": 0.0,
            "y_m": 0.0,
            "z_m": 0.1,
            "roll_rad": 0.0,
            "pitch_rad": 0.0,
            "yaw_rad": 0.0,
        },
    }


def _logistics_physical_completion_policy() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.px4-physical-completion-policy/v2",
        # The logistics run's scenario clock step_ns is 1s; the physical
        # completion window must contain the earliest completion barrier for a
        # 1s step (2 + ceil(20ms/1s) = 3s).
        "physical_sim_timeout_ns": 10_000_000_000,
        "min_settle_samples": 2,
        "settle_duration_ns": 20_000_000,
        "takeoff_altitude_tolerance_m": 0.25,
        "goto_horizontal_tolerance_m": 1.0,
        "goto_vertical_tolerance_m": 0.5,
        "goto_minimum_progress_m": 5.0,
        "hold_drift_radius_m": 0.75,
        "max_horizontal_settled_speed_m_s": 0.4,
        "max_vertical_settled_speed_m_s": 0.3,
        "landing_max_speed_m_s": 0.35,
        "landing_max_height_proxy_m": 0.2,
        "disarm_requires_contact": True,
        "arm_allowed_modes": ["READY"],
        "disarm_allowed_modes": ["LAND"],
        "takeoff_allowed_modes": ["TAKEOFF"],
        "goto_allowed_modes": ["MISSION"],
        "hold_allowed_modes": ["HOLD"],
        "land_allowed_modes": ["LAND"],
    }


def px4_gazebo_config_dict(
    *,
    world_name: str,
    world_input: dict[str, str] | None,
    physics_step_ns: int = 1_000_000,
) -> dict[str, object]:
    """The bundle's real ``px4.gazebo`` provider config document."""
    return {
        "provider_id": PX4_PROVIDER_ID,
        "schema_version": "aero-bench.px4-gazebo/v3",
        "px4": _PX4_PIN,
        "gazebo": _GAZEBO_PIN,
        "mavsdk": _MAVSDK_PIN,
        "engine_binding_id": "binding.gazebo",
        "physics_step_ns": physics_step_ns,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [_logistics_vehicle()],
        "required_commands": ["gz", "mavsdk-server", "px4"],
        "command_timeout_ms": 1_000,
        "physical_completion_policy": _logistics_physical_completion_policy(),
        "maximum_agent_decision_wall_time_ms": 1_000,
        "heartbeat_timeout_fixed_margin_ms": 100,
        "world_name": world_name,
        "world_input": world_input,
    }


def _staged_world_name(world_bytes: bytes) -> str:
    root = ET.fromstring(world_bytes)
    worlds = [element for element in root if element.tag.rsplit("}", 1)[-1] == "world"]
    if len(worlds) != 1:
        raise AssertionError("derived logistics world must have exactly one <world>")
    return worlds[0].attrib["name"]


def logistics_trajectory_artifact() -> ArtifactRequirement:
    """The px4.gazebo flight provider's real trajectory evidence requirement."""
    return ArtifactRequirement(
        artifact_id="artifact.px4.trajectory",
        artifact_type="trajectory",
        producer_id=PX4_PROVIDER_ID,
        visibility="private",
        relative_path="trajectory/evidence.jsonl",
        max_size_bytes=1_048_576,
        source_asset_id=None,
    )


def author_logistics_bundle(
    root: Path,
    *,
    origin: SceneOrigin,
    base_world: bytes,
    pin_world_input: bool,
    park_slots: int = 2,
    hub_slots: int = 2,
    charge_slots: int = 3,
    seed: int = 7,
    flight_adapter: str = "px4.gazebo",
) -> Path:
    """Write a *persistent* logistics bundle and return its ``suite.yaml`` path.

    Every FileRef is byte-real on ``root`` (task package/schema/instruction,
    verifier config/schema, agent, gateway schema, each provider config/schema/
    protocol schema, and the world package).

    When ``pin_world_input`` is True the ``px4.gazebo`` flight provider config
    already carries ``world_input``/``world_name`` exactly matching the
    facility-spliced world derived from ``origin`` + ``base_world`` (the
    committed acceptance authoring flow).  When False the config is authored with
    neither declaration, exactly as an external map/facility pipeline emits it
    *before* an authoring bridge derives and pins the declaration.  Nothing is
    resolved here.
    """
    root.mkdir(parents=True, exist_ok=True)
    reader, world_ref, world = materialize_world_package(root)
    package_document = _package_document(
        park_slots=park_slots,
        hub_slots=hub_slots,
        charge_slots=charge_slots,
        scene_source_sha256=world.asset_digest,
    )
    task = _build_task(root, package_document)

    verifier_config_ref = _write_fixture_file(
        root,
        "configs/logistics-verifier.json",
        canonical_json_bytes({"threshold": 1.0}) + b"\n",
    )
    verifier_schema_ref = _write_fixture_file(
        root,
        "schemas/logistics-verifier.json",
        canonical_json_bytes({"type": "object"}) + b"\n",
    )
    task = task.model_copy(
        update={
            "verifier": task.verifier.model_copy(
                update={
                    "config": SchemaBoundFile(
                        file=verifier_config_ref,
                        schema_file=verifier_schema_ref,
                    )
                }
            )
        }
    )

    (agent,) = _build_agents()
    agent_ref = _write_fixture_file(
        root,
        "agents/logistics-agent.yaml",
        yaml.safe_dump(agent.model_dump(mode="json"), sort_keys=False).encode(
            "utf-8"
        ),
    )

    if pin_world_input:
        package = lower_logistics_task_package(package_document)
        _, derived = materialize_logistics_world(
            package=package,
            origin=origin,
            base_world=base_world,
            provider_id=PX4_PROVIDER_ID,
        )
        staged_world_name = _staged_world_name(derived.content_utf8.encode("utf-8"))
        world_input = {
            "schema_version": "aero-bench.px4-gazebo/bundle-world/v1",
            "source": "bundle",
            "path": derived.destination,
            "sha256": derived.sha256,
        }
        flight_config = px4_gazebo_config_dict(
            world_name=staged_world_name,
            world_input=world_input,
        )
    else:
        flight_config = px4_gazebo_config_dict(
            world_name=None,
            world_input=None,
        )

    env = _build_environment(flight_adapter=flight_adapter, extra_px4_provider_id=None)
    trajectory = logistics_trajectory_artifact()
    env = env.model_copy(
        update={
            "providers": tuple(
                provider.model_copy(
                    update={"artifact_requirements": (trajectory,)}
                )
                if provider.provider_id == PX4_PROVIDER_ID
                else provider
                for provider in env.providers
            )
        }
    )

    gateway_schema_ref = _write_fixture_file(
        root,
        "schemas/gateway.json",
        canonical_json_bytes({"type": "object"}) + b"\n",
    )
    provider_config_refs: dict[str, FileRef] = {}
    provider_schema_refs: dict[str, FileRef] = {}
    provider_protocol_refs: dict[str, FileRef] = {}
    for provider in env.providers:
        payload = flight_config if provider.provider_id == PX4_PROVIDER_ID else {}
        provider_config_refs[provider.provider_id] = _write_fixture_file(
            root,
            f"configs/providers/{provider.provider_id}.json",
            canonical_json_bytes(payload) + b"\n",
        )
        provider_schema_refs[provider.provider_id] = _write_fixture_file(
            root,
            f"schemas/providers/{provider.provider_id}.json",
            canonical_json_bytes({"type": "object"}) + b"\n",
        )
        provider_protocol_refs[provider.provider_id] = _write_fixture_file(
            root,
            f"schemas/protocol/{provider.provider_id}.json",
            canonical_json_bytes({"type": "object"}) + b"\n",
        )
    env = env.model_copy(
        update={
            "gateway": env.gateway.model_copy(
                update={"protocol_schema": gateway_schema_ref}
            ),
            "providers": tuple(
                provider.model_copy(
                    update={
                        "config": SchemaBoundFile(
                            file=provider_config_refs[provider.provider_id],
                            schema_file=provider_schema_refs[provider.provider_id],
                        ),
                        "protocol_schema": provider_protocol_refs[
                            provider.provider_id
                        ],
                    }
                )
                for provider in env.providers
            ),
        }
    )

    task_ref = _write_fixture_file(
        root,
        "tasks/task.yaml",
        yaml.safe_dump(task.model_dump(mode="json"), sort_keys=False).encode(
            "utf-8"
        ),
    )
    environment_ref = _write_fixture_file(
        root,
        "environments/environment.yaml",
        yaml.safe_dump(env.model_dump(mode="json"), sort_keys=False).encode(
            "utf-8"
        ),
    )

    suite_raw = {
        "schema_version": "aero-bench.suite/v2",
        "suite_id": "logistics.suite",
        "aggregator_id": "macro",
        "execution_scope": "executor_validation",
        "cases": [
            {
                "case_id": "logistics.basic",
                "task": task_ref.model_dump(mode="json"),
                "environment": environment_ref.model_dump(mode="json"),
                "agents": [agent_ref.model_dump(mode="json")],
                "world_package": world_ref.model_dump(mode="json"),
                "launch_site_ids": ["launch.alpha"],
                "seeds": [seed],
                "axes": [],
            }
        ],
    }
    suite_path = root / "suite.yaml"
    suite_path.write_bytes(
        yaml.safe_dump(suite_raw, sort_keys=False).encode("utf-8")
    )
    return suite_path


def materialize_logistics_suite(
    root: Path,
    *,
    origin: SceneOrigin,
    base_world: bytes,
    park_slots: int = 2,
    hub_slots: int = 2,
    charge_slots: int = 3,
    seed: int = 7,
    executor_kind: str = "docker_reference",
    flight_adapter: str = "px4.gazebo",
) -> tuple[Path, ResolvedRunSpec]:
    """Author a pinned logistics bundle and resolve it to a reproducible run.

    The ``px4.gazebo`` flight provider config carries ``world_input`` exactly
    matching the facility-spliced world derived from ``origin`` + ``base_world``,
    plus the pinned world name.  The suite's single case is resolved with the
    real logistics resolver, so the returned ``ResolvedRunSpec`` is by
    construction an exact output of its pinned source suite
    (``validate_resolved_run_bundle`` reproduces it).
    """
    suite_path = author_logistics_bundle(
        root,
        origin=origin,
        base_world=base_world,
        pin_world_input=True,
        park_slots=park_slots,
        hub_slots=hub_slots,
        charge_slots=charge_slots,
        seed=seed,
        flight_adapter=flight_adapter,
    )
    from aero_bench.config.resolver import resolve_suite

    runs = resolve_suite(
        str(suite_path),
        executor_kind=executor_kind,
        task_package_resolvers=(LogisticsTaskPackageResolver(),),
        provider_registry=logistics_fixture_provider_registry(),
    )
    assert len(runs) == 1, f"expected one logistics run, found {len(runs)}"
    run = runs[0]
    assert run.task.package.package_id == LOGISTICS_PACKAGE_ID
    return root.resolve(), run


__all__ = [
    "DEFAULT_LOGISTICS_WORLD_DESTINATION",
    "LOGISTICS_PACKAGE_ID",
    "PX4_PROVIDER_ID",
    "SCENE_ALTITUDE_M",
    "SCENE_LATITUDE_DEG",
    "SCENE_LONGITUDE_DEG",
    "SCENE_SOURCE_SHA256",
    "author_logistics_bundle",
    "base_execution_plan",
    "base_world_sdf_bytes",
    "logistics_package",
    "logistics_resolved_run",
    "logistics_task",
    "logistics_trajectory_artifact",
    "materialize_logistics_suite",
    "px4_gazebo_config_dict",
]
