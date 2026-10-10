"""Strict logistics runtime binding contract tests (WP1, native-config corrected).

Covers:
1. the authored aircraft identity (``<fleet_entry_id>:<ordinal>``) is preserved
   verbatim — colon and uppercase never renamed into a native Identifier;
2. positive validation binds every participating aircraft to an existing
   ``px4.gazebo`` Provider, a physical vehicle the *current registered native*
   ``Px4GazeboConfig`` strict v3 model can materialize (schema generated from
   that exact model), and a native controlling agent with a real flight-control
   tool grant — including a single central dispatcher agent controlling multiple
   aircraft and agents holding any of the six current PX4 command tools
   (arm/disarm/takeoff/land/goto/hold);
3. wrong/missing/duplicate/uncontrolled/cross-Provider bindings and
   actor-role / control-grant mismatches reject with concrete messages;
4. stale inspection-v1 release px4 configs, duplicate ``vehicle_id`` entries
   (even under a permissive caller schema), unknown fields, and missing
   required v3 fields are rejected as ``LogisticsBindingError`` with a
   native-config diagnosis;
5. dispatcher/business principal bindings check native agent identity existence
   only — order-tool authorization is not claimed by this contract;
6. the canonical digest covers every binding field (any field change changes it).
"""

from __future__ import annotations

from tests.support import fixture_provider_registry

import hashlib
import json
import re
from pathlib import Path

import pytest

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
    ToolGrant,
    VerifierSpec,
)
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_FLIGHT_CAPABILITIES,
    LOGISTICS_PACKAGE_ID,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    LOGISTICS_BINDINGS_SCHEMA_VERSION,
    LOGISTICS_FLIGHT_CONTROL_TOOLS,
    LOGISTICS_FLIGHT_PROVIDER_ADAPTER,
    LogisticsBindingError,
    LogisticsRuntimeBindings,
    compile_logistics_runtime_bindings,
    validate_logistics_runtime_bindings,
)
from aero_bench.world.resolved import (
    ResolvedTaskScenarioProjection,
    compile_resolved_scenario,
)
from tests.world.support import (
    frame_origin,
    materialize_world_package,
    provider_capabilities,
    uav_entity,
)

_RELEASE_DIR = Path(__file__).resolve().parents[2] / "releases" / "inspection-v1"


def _read_release_json(relative: str) -> dict[str, object]:
    """Read a historical inspection-v1 release document/schema (regression data)."""
    path = _RELEASE_DIR / relative
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _vertiport(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "facility-1",
        "name": "active vertiport",
        "kind": "vertiport",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 10, "z": -20},
        "rotationDeg": 0,
        "widthM": 12,
        "depthM": 8,
        "heightM": 4,
        "landing": {"parkingSlots": 3, "movementsPerHour": 30},
        "cargo": None,
        "charging": None,
    }
    value.update(overrides)
    return value


def _hub(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "hub-3",
        "name": "cargo hub",
        "kind": "hub",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 30, "z": -40},
        "rotationDeg": 0,
        "widthM": 16,
        "depthM": 12,
        "heightM": 6,
        "landing": {"parkingSlots": 2, "movementsPerHour": 20},
        "cargo": {"storageCapacityKg": 100, "throughputPerHourKg": 50},
        "charging": None,
    }
    value.update(overrides)
    return value


def _charger(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "charger-x",
        "name": "east charger",
        "kind": "charger",
        "placement": "ground",
        "buildingId": None,
        "supportHeightM": None,
        "position": {"x": 50, "z": -60},
        "rotationDeg": 0,
        "widthM": 10,
        "depthM": 8,
        "heightM": 3.2,
        "landing": None,
        "cargo": None,
        "charging": {
            "slots": 1,
            "powerW": 5000,
            "priceAmount": 1.2,
            "priceCurrency": "CNY",
            "priceUnit": "kWh",
        },
    }
    value.update(overrides)
    return value


def _facilities() -> list[dict[str, object]]:
    return [
        _vertiport(),
        _vertiport(id="facility-2"),
        _hub(),
        _charger(),
    ]


def _fleet_entry(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "fleet-alpha",
        "assetId": "model:logistics-drone-v1",
        "count": 1,
        "homeFacilityId": "facility-1",
        "batteryWh": 20000,
        "reserveRatio": 0.2,
        "maxPayloadKg": 5,
    }
    value.update(overrides)
    return value


def _fleet(aircraft_count: int = 1) -> list[dict[str, object]]:
    return [_fleet_entry(count=aircraft_count)]


def _performance_profile(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "fleetEntryId": "fleet-alpha",
        "sourceLabel": "operator estimate",
        "provenance": "selected-city v3 planning data",
        "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
        "cruiseSpeedMps": 15,
        "cruisePowerW": 900,
        "hoverPowerW": 700,
        "chargeEfficiency": 0.85,
    }
    value.update(overrides)
    return value


def _order(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": "order-1",
        "sourceFacilityId": "facility-1",
        "destinationFacilityId": "facility-2",
        "hubHandoffFacilityId": "hub-3",
        "cargoKg": 1,
        "releaseAtS": 0,
        "deliverByS": 600,
    }
    value.update(overrides)
    return value


def _no_fly_zone(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
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
    value.update(overrides)
    return value


def _actors(aircraft_count: int = 1) -> list[dict[str, object]]:
    aircraft = [
        {"actor_id": f"fleet-alpha:{ordinal}", "role": "aircraft_agent"}
        for ordinal in range(1, aircraft_count + 1)
    ]
    return [
        *aircraft,
        {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
        {"actor_id": "logistics.business", "role": "business"},
    ]


def _scene(**overrides: object) -> dict[str, object]:
    origin = frame_origin()
    value: dict[str, object] = {
        "scene_id": "world.city-demo",
        "coordinate_frame": "scene_east_south_m",
        "origin_latitude_deg": origin.latitude_deg,
        "origin_longitude_deg": origin.longitude_deg,
        "origin_altitude_m": origin.altitude_m,
        "scene_source_sha256": "ab" * 32,
    }
    value.update(overrides)
    return value


def _package_document(aircraft_count: int = 1) -> dict[str, object]:
    return {
        "schema_version": "aero-bench.logistics-task/v1",
        "package_id": LOGISTICS_PACKAGE_ID,
        "task_id": "logistics.task",
        "verifier_id": "logistics.verifier",
        "scene": _scene(),
        "facilities": _facilities(),
        "fleet": _fleet(aircraft_count),
        "performanceProfiles": [_performance_profile()],
        "orders": [_order()],
        "noFlyZones": [_no_fly_zone()],
        "actors": _actors(aircraft_count),
    }


def _placeholder_ref(path: str) -> FileRef:
    return FileRef(path=path, sha256="a1" * 32)


def _write_fixture_file(root: Path, path: str, payload: bytes) -> FileRef:
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return FileRef(path=path, sha256=hashlib.sha256(payload).hexdigest())


def _fixture_runtime(component_id: str) -> RuntimeSpec:
    return RuntimeSpec(
        runtime=RuntimeImage(
            image=f"aero-bench/{component_id}@sha256:{'ab' * 32}",
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


def _px4_vehicle(vehicle_id: str, *, index: int) -> dict[str, object]:
    """A native ``VehicleSpec`` document with per-vehicle distinct identities.

    The current ``Px4GazeboConfig`` model requires every vehicle to declare a
    distinct ``system_id``, distinct MAVLink/MAVSDK ports, and a distinct
    ``gazebo_model_name``, so multi-vehicle fixtures must not reuse the same
    numbers.
    """
    return {
        "vehicle_id": vehicle_id,
        "system_id": index,
        "mavsdk_udp_port": 14540 + (index - 1) * 10,
        "px4_mavlink_udp_port": 14580 + (index - 1) * 10,
        "mavsdk_grpc_port": 15040 + (index - 1) * 10,
        "sys_autostart": 4001,
        "model": "gz_x500_mono_cam",
        "gazebo_model_name": f"x500_mono_cam_{index - 1}",
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


def _px4_physical_completion_policy() -> dict[str, object]:
    """A strict v2 physical-completion policy the native v3 config requires."""
    return {
        "schema_version": "aero-bench.px4-physical-completion-policy/v2",
        "physical_sim_timeout_ns": 30_000_000_000,
        "min_settle_samples": 2,
        "settle_duration_ns": 2_000_000_000,
        "takeoff_altitude_tolerance_m": 0.5,
        "goto_horizontal_tolerance_m": 0.5,
        "goto_vertical_tolerance_m": 0.5,
        "goto_minimum_progress_m": 0.1,
        "hold_drift_radius_m": 1.0,
        "max_horizontal_settled_speed_m_s": 0.5,
        "max_vertical_settled_speed_m_s": 0.5,
        "landing_max_speed_m_s": 0.5,
        "landing_max_height_proxy_m": 0.5,
        "disarm_requires_contact": True,
        "arm_allowed_modes": ["GUIDED"],
        "disarm_allowed_modes": ["GUIDED"],
        "takeoff_allowed_modes": ["GUIDED"],
        "goto_allowed_modes": ["GUIDED"],
        "hold_allowed_modes": ["GUIDED"],
        "land_allowed_modes": ["GUIDED"],
    }


def _px4_config_document(vehicle_ids: tuple[str, ...]) -> dict[str, object]:
    """A strict current v3 document the native ``Px4GazeboConfig`` materializes.

    This is the config shape the currently registered ``px4.gazebo`` backend
    (``aero_bench/providers/px4_gazebo/config.py``) accepts:
    ``schema_version`` v3, ``engine_binding_id`` and
    ``physical_completion_policy`` present, and no inspection-v1-only fields
    (``step_length_ns``, ``world_sdf``, ``inspection``).  The stale inspection-v1
    release v1 document lives in :data:`_read_release_json` regression data.
    """
    return {
        "schema_version": "aero-bench.px4-gazebo/v3",
        "provider_id": "flight",
        "px4": {
            "commit": "381149fb012762f5e38c4a7fdc1b905b28038970",
            "version": "v1.17.0-alpha1-1551-g381149fb01",
        },
        "gazebo": {
            "commit": "1be3cc376fec778cc725b4eeea463245affa56d3",
            "version": "8.11.0",
        },
        "mavsdk": {
            "commit": "9e3ca17faa84aa868caea10a3bbdab7e53810ced",
            "version": "3.17.2",
        },
        "engine_binding_id": "gazebo.enu",
        "physics_step_ns": 4_000_000,
        "px4_executable": "px4",
        "gazebo_executable": "gz",
        "mavsdk_server_executable": "mavsdk-server",
        "vehicles": [
            _px4_vehicle(vehicle_id, index=index)
            for index, vehicle_id in enumerate(vehicle_ids, start=1)
        ],
        "required_commands": ["px4", "gz", "mavsdk-server"],
        "command_timeout_ms": 120_000,
        "physical_completion_policy": _px4_physical_completion_policy(),
        "maximum_agent_decision_wall_time_ms": 120_000,
        "heartbeat_timeout_fixed_margin_ms": 10_000,
    }


_ADAPTER_BY_PROVIDER = {
    "flight": LOGISTICS_FLIGHT_PROVIDER_ADAPTER,
    "perception": "fixture.observation-artifact-writer",
    "radio": "fixture.network-artifact-writer",
    "traffic": "fixture.traffic-provider",
}


def _make_px4_gazebo_environment(
    root: Path,
    px4_config_document: dict[str, object],
    *,
    flight_adapter: str = LOGISTICS_FLIGHT_PROVIDER_ADAPTER,
    px4_config_schema_document: dict[str, object] | None = None,
) -> EnvironmentSpec:
    """Environment with the real ``px4.gazebo`` flight Provider and its config.

    The flight Provider's config is the PX4-Gazebo document written into the
    bundle.  The caller-declared JSON Schema defaults to the one *generated from
    the current registered native model* (``Px4GazeboConfig.model_json_schema``),
    so a config the backend cannot materialize is rejected.  Callers may mount a
    different (e.g. stale or deliberately permissive) schema via
    ``px4_config_schema_document`` to exercise the native-model layer.
    """
    config_ref = _write_fixture_file(
        root,
        "configs/px4.json",
        canonical_json_bytes(px4_config_document) + b"\n",
    )
    schema_document = (
        px4_config_schema_document
        if px4_config_schema_document is not None
        else Px4GazeboConfig.model_json_schema()
    )
    schema_ref = _write_fixture_file(
        root,
        "schemas/px4-config.json",
        canonical_json_bytes(schema_document) + b"\n",
    )

    capabilities = dict(provider_capabilities())
    capabilities["flight"] = (
        *capabilities["flight"],
        *sorted(LOGISTICS_FLIGHT_CAPABILITIES),
    )
    adapters = dict(_ADAPTER_BY_PROVIDER)
    adapters["flight"] = flight_adapter

    providers: list[ProviderRef] = []
    for index, (provider_id, provider_caps) in enumerate(capabilities.items()):
        if provider_id == "flight":
            config = SchemaBoundFile(file=config_ref, schema_file=schema_ref)
        else:
            config = SchemaBoundFile(
                file=_placeholder_ref(f"configs/providers/{provider_id}.json"),
                schema_file=_placeholder_ref(f"schemas/providers/{provider_id}.json"),
            )
        providers.append(
            ProviderRef(
                provider_id=provider_id,
                adapter=adapters[provider_id],
                port=17601 + index,
                workload=_fixture_runtime(adapters[provider_id]),
                config=config,
                protocol_schema=_placeholder_ref(f"schemas/protocol/{provider_id}.json"),
                capabilities=provider_caps,
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
            ArtifactRequirement(
                artifact_id="harness.event-log",
                artifact_type="event.log",
                producer_id="harness",
                visibility="private",
                relative_path="harness/event-log.json",
                max_size_bytes=1048576,
                source_asset_id=None,
            ),
            ArtifactRequirement(
                artifact_id="harness.plan",
                artifact_type="run.plan",
                producer_id="harness",
                visibility="private",
                relative_path="harness/plan.json",
                max_size_bytes=4096,
                source_asset_id=None,
            ),
        ),
    )


def _flight_tool(tool_id: str) -> ToolGrant:
    return ToolGrant(
        tool_id=tool_id,
        provider_id="flight",
        request_schema=_placeholder_ref(f"schemas/{tool_id}.json"),
        response_schema=_placeholder_ref("schemas/tool-response.json"),
        timeout_ms=120000,
        idempotent=False,
    )


def _dispatcher_agent(agent_id: str = "dispatcher.agent") -> AgentSpec:
    return AgentSpec(
        schema_version="aero-bench.agent/v2",
        agent_id=agent_id,
        workload=_fixture_runtime(agent_id),
        tools=tuple(_flight_tool(tool_id) for tool_id in LOGISTICS_FLIGHT_CONTROL_TOOLS),
        queries=(),
        observations=(),
        artifact_requirements=(),
    )


def _flight_agent(agent_id: str, tool_ids: tuple[str, ...]) -> AgentSpec:
    """An agent holding exactly the given flight tool grants against ``flight``."""
    return AgentSpec(
        schema_version="aero-bench.agent/v2",
        agent_id=agent_id,
        workload=_fixture_runtime(agent_id),
        tools=tuple(_flight_tool(tool_id) for tool_id in tool_ids),
        queries=(),
        observations=(),
        artifact_requirements=(),
    )


def _unnamed_agent(agent_id: str) -> AgentSpec:
    return AgentSpec(
        schema_version="aero-bench.agent/v2",
        agent_id=agent_id,
        workload=_fixture_runtime(agent_id),
        tools=(),
        queries=(),
        observations=(),
        artifact_requirements=(),
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


def _build_task(root: Path, package_document: dict[str, object]) -> TaskSpec:
    package_config = _write_fixture_file(
        root,
        "tasks/logistics-package.json",
        canonical_json_bytes(package_document) + b"\n",
    )
    package_schema = _write_fixture_file(
        root,
        "schemas/logistics-package.schema.json",
        canonical_json_bytes(_logistics_package_schema()) + b"\n",
    )
    instruction = _write_fixture_file(
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
            config=SchemaBoundFile(file=package_config, schema_file=package_schema),
        ),
        instruction=instruction,
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
            output_artifacts=(
                ArtifactRequirement(
                    artifact_id="logistics.verification",
                    artifact_type="verification.report",
                    producer_id=verifier_id,
                    visibility="public",
                    relative_path="verification/logistics.json",
                    max_size_bytes=4096,
                    source_asset_id=None,
                ),
            ),
        ),
    )


def _world_with_uav_ids(uav_ids: tuple[str, ...]):
    def mutate(kwargs: dict[str, object], root: Path) -> None:
        entities = [
            entity for entity in kwargs["entities"] if entity.kind != "uav"
        ]
        for index, uav_id in enumerate(uav_ids):
            entities.append(uav_entity(entity_id=uav_id, east_m=5.0 + 20.0 * index))
        kwargs["entities"] = tuple(sorted(entities, key=lambda entity: entity.entity_id))

    return mutate


def _capability_map(environment: EnvironmentSpec) -> dict[str, tuple[str, ...]]:
    return {
        provider.provider_id: provider.capabilities
        for provider in environment.providers
    }


class _RuntimeFixture:
    def __init__(
        self,
        *,
        reader,
        package,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario,
    ) -> None:
        self.reader = reader
        self.package = package
        self.environment = environment
        self.agents = agents
        self.scenario = scenario


def _build_runtime_fixture(
    root: Path,
    *,
    aircraft_count: int,
    px4_vehicle_ids: tuple[str, ...],
    world_uav_ids: tuple[str, ...],
    agents: tuple[AgentSpec, ...],
    flight_adapter: str = LOGISTICS_FLIGHT_PROVIDER_ADAPTER,
    px4_config_document: dict[str, object] | None = None,
    px4_config_schema_document: dict[str, object] | None = None,
) -> _RuntimeFixture:
    root.mkdir(parents=True, exist_ok=True)
    reader, world_ref, world = materialize_world_package(
        root,
        mutate_kwargs=_world_with_uav_ids(world_uav_ids),
    )
    raw = _package_document(aircraft_count=aircraft_count)
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(root, raw)
    package = lower_logistics_task_package(raw)
    environment = _make_px4_gazebo_environment(
        root,
        px4_config_document
        if px4_config_document is not None
        else _px4_config_document(px4_vehicle_ids),
        flight_adapter=flight_adapter,
        px4_config_schema_document=px4_config_schema_document,
    )
    projection = ResolvedTaskScenarioProjection(
        logical_endpoint_ids=(),
        logical_capability_ids=(),
        observations=(),
    )
    scenario = compile_resolved_scenario(
        reader,
        world_ref,
        "launch.alpha",
        7,
        _capability_map(environment),
        {provider.provider_id: fixture_provider_registry().runtime_stage_for(provider.adapter) for provider in environment.providers},
        task,
        agents,
        projection,
    )
    return _RuntimeFixture(
        reader=reader,
        package=package,
        environment=environment,
        agents=agents,
        scenario=scenario,
    )


def _aircraft_binding(
    *,
    ordinal: int,
    vehicle_id: str,
    controlling_agent_id: str = "dispatcher.agent",
    provider_id: str = "flight",
) -> dict[str, object]:
    return {
        "aircraft_id": f"fleet-alpha:{ordinal}",
        "fleet_entry_id": "fleet-alpha",
        "visual_asset_id": "model:logistics-drone-v1",
        "provider_id": provider_id,
        "vehicle_id": vehicle_id,
        "controlling_agent_id": controlling_agent_id,
    }


def _principal_binding(
    *,
    actor_id: str,
    role: str,
    agent_id: str,
) -> dict[str, object]:
    return {"actor_id": actor_id, "role": role, "agent_id": agent_id}


def _bindings_document(
    *,
    aircraft: list[dict[str, object]],
    principals: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": LOGISTICS_BINDINGS_SCHEMA_VERSION,
        "aircraft": aircraft,
        "principals": principals,
    }


def _valid_bindings(aircraft_count: int = 1) -> dict[str, object]:
    vehicles = ("uav.alpha", "uav.bravo", "uav.charlie")
    aircraft = [
        _aircraft_binding(ordinal=ordinal, vehicle_id=vehicles[ordinal - 1])
        for ordinal in range(1, aircraft_count + 1)
    ]
    principals = [
        _principal_binding(
            actor_id="logistics.dispatcher",
            role="dispatcher",
            agent_id="dispatcher.agent",
        ),
        _principal_binding(
            actor_id="logistics.business",
            role="business",
            agent_id="business.agent",
        ),
    ]
    return _bindings_document(aircraft=aircraft, principals=principals)


# ---------------------------------------------------------------- model level


def test_colon_aircraft_id_is_preserved_verbatim() -> None:
    bindings = compile_logistics_runtime_bindings(
        _valid_bindings(aircraft_count=1)
    )
    bound = bindings.aircraft[0]
    assert bound.aircraft_id == "fleet-alpha:1"
    assert ":" in bound.aircraft_id
    assert bound.aircraft_id != bound.vehicle_id
    assert bound.visual_asset_id == "model:logistics-drone-v1"
    assert bound.visual_asset_id != bound.vehicle_id
    assert re.fullmatch(r"[0-9a-f]{64}", bindings.canonical_digest())


def test_compile_rejects_unknown_top_level_key() -> None:
    document = _valid_bindings()
    document["runtimeMagic"] = True
    with pytest.raises(LogisticsBindingError, match="unknown top-level"):
        compile_logistics_runtime_bindings(document)


def test_compile_rejects_missing_aircraft_section() -> None:
    document = _valid_bindings()
    del document["aircraft"]
    with pytest.raises(LogisticsBindingError, match="missing required keys"):
        compile_logistics_runtime_bindings(document)


def test_compile_rejects_duplicate_physical_aircraft() -> None:
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha"),
            _aircraft_binding(ordinal=2, vehicle_id="uav.alpha"),
        ],
        principals=[],
    )
    with pytest.raises(ValueError, match="duplicate physical aircraft"):
        compile_logistics_runtime_bindings(document)


def test_compile_rejects_duplicate_aircraft_binding() -> None:
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha"),
            _aircraft_binding(ordinal=1, vehicle_id="uav.bravo"),
        ],
        principals=[],
    )
    with pytest.raises(ValueError, match="duplicate aircraft"):
        compile_logistics_runtime_bindings(document)


def test_compile_rejects_aircraft_actor_as_dispatcher_principal() -> None:
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.alpha")],
        principals=[
            _principal_binding(
                actor_id="fleet-alpha:1", role="dispatcher", agent_id="dispatcher.agent"
            )
        ],
    )
    with pytest.raises(ValueError, match="aircraft"):
        compile_logistics_runtime_bindings(document)


def test_native_identifiers_reject_colon_and_uppercase() -> None:
    with pytest.raises(Exception, match="string_pattern_mismatch|StringPatternError"):
        compile_logistics_runtime_bindings(
            _bindings_document(
                aircraft=[
                    {
                        "aircraft_id": "fleet-alpha:1",
                        "fleet_entry_id": "fleet-alpha",
                        "visual_asset_id": "model:logistics-drone-v1",
                        "provider_id": "flight",
                        "vehicle_id": "UAV.1",  # native vehicle id must be lowercase
                        "controlling_agent_id": "dispatcher.agent",
                    }
                ],
                principals=[],
            )
        )


# ------------------------------------------------------- full runtime validation


def _standard_agents() -> tuple[AgentSpec, ...]:
    return (_dispatcher_agent(), _unnamed_agent("business.agent"))


def test_valid_bindings_validate_and_allow_centralized_control(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=2,
        px4_vehicle_ids=("uav.alpha", "uav.bravo"),
        world_uav_ids=("uav.alpha", "uav.bravo"),
        agents=_standard_agents(),
    )
    document = _valid_bindings(aircraft_count=2)
    # Both aircraft are controlled by the single central dispatcher agent.
    assert [
        binding["controlling_agent_id"] for binding in document["aircraft"]
    ] == ["dispatcher.agent", "dispatcher.agent"]

    validated = validate_logistics_runtime_bindings(
        bindings_document=document,
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    )
    assert isinstance(validated, LogisticsRuntimeBindings)
    assert len(validated.aircraft) == 2
    assert {binding.aircraft_id for binding in validated.aircraft} == {
        "fleet-alpha:1",
        "fleet-alpha:2",
    }
    assert validated.aircraft[0].provider_id == "flight"
    assert validated.aircraft[0].vehicle_id == "uav.alpha"
    assert validated.aircraft[0].controlling_agent_id == "dispatcher.agent"
    assert re.fullmatch(r"[0-9a-f]{64}", validated.canonical_digest())
    assert validated.canonical_digest() == validated.canonical_digest()


def test_canonical_digest_covers_every_binding_field(tmp_path: Path) -> None:
    root = tmp_path
    agents = (
        _dispatcher_agent(),
        _dispatcher_agent("second.dispatcher"),
        _unnamed_agent("business.agent"),
    )
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=2,
        px4_vehicle_ids=("uav.alpha", "uav.bravo"),
        world_uav_ids=("uav.alpha", "uav.bravo"),
        agents=agents,
    )
    base = validate_logistics_runtime_bindings(
        bindings_document=_valid_bindings(aircraft_count=2),
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    ).canonical_digest()

    # Same semantic document yields the same digest (no ordering/allocator drift).
    again = validate_logistics_runtime_bindings(
        bindings_document=_valid_bindings(aircraft_count=2),
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    ).canonical_digest()
    assert again == base

    principals = [
        _principal_binding(
            actor_id="logistics.dispatcher",
            role="dispatcher",
            agent_id="dispatcher.agent",
        ),
        _principal_binding(
            actor_id="logistics.business",
            role="business",
            agent_id="business.agent",
        ),
    ]
    variants = {
        # vehicle field differs (both vehicles are real declared identities).
        "vehicle": _bindings_document(
            aircraft=[
                _aircraft_binding(ordinal=2, vehicle_id="uav.alpha"),
                _aircraft_binding(ordinal=1, vehicle_id="uav.bravo"),
            ],
            principals=principals,
        ),
        # controlling native agent differs (both agents hold flight grants).
        "controlling agent": _bindings_document(
            aircraft=[
                _aircraft_binding(ordinal=1, vehicle_id="uav.alpha")
                | {"controlling_agent_id": "second.dispatcher"},
                _aircraft_binding(ordinal=2, vehicle_id="uav.bravo"),
            ],
            principals=principals,
        ),
        # native principal agent differs.
        "principal agent": _bindings_document(
            aircraft=[
                _aircraft_binding(ordinal=1, vehicle_id="uav.alpha"),
                _aircraft_binding(ordinal=2, vehicle_id="uav.bravo"),
            ],
            principals=[
                _principal_binding(
                    actor_id="logistics.dispatcher",
                    role="dispatcher",
                    agent_id="second.dispatcher",
                ),
                _principal_binding(
                    actor_id="logistics.business",
                    role="business",
                    agent_id="business.agent",
                ),
            ],
        ),
    }
    for label, variant in variants.items():
        candidate = validate_logistics_runtime_bindings(
            bindings_document=variant,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        ).canonical_digest()
        assert candidate != base, label


def test_missing_aircraft_binding_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=2,
        px4_vehicle_ids=("uav.alpha", "uav.bravo"),
        world_uav_ids=("uav.alpha", "uav.bravo"),
        agents=_standard_agents(),
    )
    document = _valid_bindings(aircraft_count=1)  # fleet expanded to two units
    with pytest.raises(LogisticsBindingError, match="unbound aircraft"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_unknown_native_provider_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha")
            | {"provider_id": "ghost.provider"}
        ],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="unknown native provider"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_wrong_adapter_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
        flight_adapter="fixture.flight-artifact-writer",  # registered fixture, not px4.gazebo
    )
    with pytest.raises(LogisticsBindingError, match="not px4.gazebo"):
        validate_logistics_runtime_bindings(
            bindings_document=_valid_bindings(aircraft_count=1),
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_unknown_vehicle_in_px4_config_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.missing")
        ],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="not declared by the px4.gazebo"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_scenario_authority_missing_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.extra",),  # the backend declares it as a vehicle ...
        world_uav_ids=("uav.alpha",),  # ... but no resolved scenario entity exists
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.extra")],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="no resolved scenario entity"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_cross_provider_wrong_entity_authority_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("ugv.beta",),  # declared as a px4 vehicle ...
        world_uav_ids=("uav.alpha",),  # ugv.beta is owned by the traffic provider
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="ugv.beta")],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="Gazebo-physics UAV"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_unknown_controlling_agent_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha")
            | {"controlling_agent_id": "ghost.agent"}
        ],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="unknown controlling native agent"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_control_grant_mismatch_rejects(tmp_path: Path) -> None:
    root = tmp_path
    agents = (*_standard_agents(), _unnamed_agent("helper.agent"))
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=agents,
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha")
            | {"controlling_agent_id": "helper.agent"}
        ],
        principals=[],
    )
    with pytest.raises(LogisticsBindingError, match="control-grant mismatch"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_duplicate_physical_aircraft_rejects_at_validation(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=2,
        px4_vehicle_ids=("uav.alpha", "uav.bravo"),
        world_uav_ids=("uav.alpha", "uav.bravo"),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha"),
            _aircraft_binding(ordinal=2, vehicle_id="uav.alpha"),
        ],
        principals=[],
    )
    # The document model rejects the duplicate before runtime validation runs.
    with pytest.raises(ValueError, match="duplicate physical aircraft"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_actor_role_mismatch_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.alpha")],
        principals=[
            # The package declares logistics.dispatcher as dispatcher, not as business.
            _principal_binding(
                actor_id="logistics.dispatcher",
                role="business",
                agent_id="business.agent",
            ),
        ],
    )
    with pytest.raises(LogisticsBindingError, match="actor-role mismatch"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_unknown_principal_agent_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.alpha")],
        principals=[
            _principal_binding(
                actor_id="logistics.dispatcher",
                role="dispatcher",
                agent_id="ghost.agent",
            ),
        ],
    )
    with pytest.raises(LogisticsBindingError, match="unknown native agent"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_aircraft_agent_principal_rejected(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.alpha")],
        principals=[
            _principal_binding(
                actor_id="fleet-alpha:1",
                role="aircraft_agent",
                agent_id="dispatcher.agent",
            ),
        ],
    )
    with pytest.raises(LogisticsBindingError, match="aircraft_agent authority"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_unbound_non_aircraft_actor_grant_reports_limitation(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[_aircraft_binding(ordinal=1, vehicle_id="uav.alpha")],
        principals=[
            _principal_binding(
                actor_id="logistics.dispatcher",
                role="dispatcher",
                agent_id="dispatcher.agent",
            ),
        ],
    )
    # The package declares a business grant; the bindings omit it, so the exact
    # limitation is reported instead of silently claiming a runnable principal.
    with pytest.raises(LogisticsBindingError, match="no native principal agent"):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_fleet_field_mismatch_rejects(tmp_path: Path) -> None:
    root = tmp_path
    fixture = _build_runtime_fixture(
        root,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(ordinal=1, vehicle_id="uav.alpha")
            | {"visual_asset_id": "model:some-other-drone"}
        ],
        principals=[],
    )
    with pytest.raises(
        LogisticsBindingError, match="visual_asset_id does not match"
    ):
        validate_logistics_runtime_bindings(
            bindings_document=document,
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


# ------------------------------------------------ current native-config binding


_PERMISSIVE_PX4_CONFIG_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
}


def _flight_provider_config(fixture: _RuntimeFixture) -> SchemaBoundFile:
    provider = next(
        item for item in fixture.environment.providers if item.provider_id == "flight"
    )
    return provider.config


def test_flight_control_allowlist_covers_every_current_px4_command() -> None:
    """The allowlist is the full current PX4 command set, not an inspection subset."""
    assert LOGISTICS_FLIGHT_CONTROL_TOOLS == frozenset(
        {
            "flight.arm",
            "flight.disarm",
            "flight.takeoff",
            "flight.land",
            "flight.goto",
            "flight.hold",
        }
    )


def test_positive_fixture_config_materializes_with_current_native_model(
    tmp_path: Path,
) -> None:
    """The positive fixture is the current v3 config the native backend builds."""
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
    )
    loaded = fixture.reader.validate_schema_bound_file(
        _flight_provider_config(fixture)
    )
    assert loaded["schema_version"] == "aero-bench.px4-gazebo/v3"
    # The caller schema was generated from the exact current native model...
    assert "additionalProperties" in Px4GazeboConfig.model_json_schema()
    native = Px4GazeboConfig.model_validate(loaded)
    assert native.schema_version == "aero-bench.px4-gazebo/v3"
    assert native.engine_binding_id == "gazebo.enu"
    assert (
        native.physical_completion_policy.schema_version
        == "aero-bench.px4-physical-completion-policy/v2"
    )
    assert {vehicle.vehicle_id for vehicle in native.vehicles} == {"uav.alpha"}


def test_release_v1_px4_config_is_negative_regression(tmp_path: Path) -> None:
    """The historical inspection-v1 release px4 config is no longer accepted.

    The stale v1 document still passes its own stale caller schema, but the
    current registered native v3 model cannot materialize it, so the binding
    validator must reject it as a ``LogisticsBindingError`` with a native-config
    diagnosis.
    """
    historical_doc = _read_release_json("configs/px4.json")
    release_schema = _read_release_json("schemas/px4-config.json")
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
        px4_config_document=historical_doc,
        px4_config_schema_document=release_schema,
    )
    loaded = fixture.reader.validate_schema_bound_file(
        _flight_provider_config(fixture)
    )
    assert loaded["schema_version"] == "aero-bench.px4-gazebo/v1"
    with pytest.raises(Exception, match="schema_version"):
        Px4GazeboConfig.model_validate(loaded)
    with pytest.raises(LogisticsBindingError, match="Px4GazeboConfig"):
        validate_logistics_runtime_bindings(
            bindings_document=_valid_bindings(aircraft_count=1),
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_px4_config_duplicate_vehicle_rejected_even_with_permissive_schema(
    tmp_path: Path,
) -> None:
    """A duplicate ``vehicle_id`` passes the caller schema but the native model rejects it."""
    # The generated JSON Schema has no ``uniqueItems`` on ``vehicles``, so the
    # duplicate document sails through layer 1; only the native v3 model catches
    # it.  No frozenset masking is involved anywhere.
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha", "uav.alpha"),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
        px4_config_schema_document=Px4GazeboConfig.model_json_schema(),
    )
    loaded = fixture.reader.validate_schema_bound_file(
        _flight_provider_config(fixture)
    )
    assert [item["vehicle_id"] for item in loaded["vehicles"]] == [
        "uav.alpha",
        "uav.alpha",
    ]
    with pytest.raises(LogisticsBindingError, match="vehicle_id values must be unique"):
        validate_logistics_runtime_bindings(
            bindings_document=_valid_bindings(aircraft_count=1),
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_px4_config_unknown_field_rejected_with_permissive_schema(
    tmp_path: Path,
) -> None:
    """An inspection-v1-only field is rejected even under a permissive caller schema."""
    broken = _px4_config_document(("uav.alpha",))
    broken["step_length_ns"] = 2_000_000_000  # inspection-v1 leftover, not v3
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
        px4_config_document=broken,
        px4_config_schema_document=_PERMISSIVE_PX4_CONFIG_SCHEMA,
    )
    loaded = fixture.reader.validate_schema_bound_file(
        _flight_provider_config(fixture)
    )
    assert loaded["step_length_ns"] == 2_000_000_000
    with pytest.raises(Exception, match="step_length_ns"):
        Px4GazeboConfig.model_validate(loaded)
    with pytest.raises(LogisticsBindingError, match="Px4GazeboConfig"):
        validate_logistics_runtime_bindings(
            bindings_document=_valid_bindings(aircraft_count=1),
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_px4_config_missing_required_v3_fields_rejected_with_permissive_schema(
    tmp_path: Path,
) -> None:
    """Missing ``engine_binding_id`` / ``physical_completion_policy`` is rejected."""
    broken = _px4_config_document(("uav.alpha",))
    del broken["engine_binding_id"]
    del broken["physical_completion_policy"]
    with pytest.raises(Exception, match="engine_binding_id"):
        Px4GazeboConfig.model_validate(broken)
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=_standard_agents(),
        px4_config_document=broken,
        px4_config_schema_document=_PERMISSIVE_PX4_CONFIG_SCHEMA,
    )
    with pytest.raises(LogisticsBindingError, match="Px4GazeboConfig"):
        validate_logistics_runtime_bindings(
            bindings_document=_valid_bindings(aircraft_count=1),
            reader=fixture.reader,
            environment=fixture.environment,
            agents=fixture.agents,
            package=fixture.package,
            scenario=fixture.scenario,
        )


def test_single_flight_grant_is_declared_operation_authority_only(
    tmp_path: Path,
) -> None:
    """One declared flight grant satisfies the binding's existence check only.

    It establishes that the agent is declared operable for that one operation
    authority (e.g. ``flight.hold``); the static contract intentionally claims
    nothing further — no sequencing, no refuel/charge or business gates, no full
    flight-mission authorization beyond the declared granted operation.
    """
    hold_only = _flight_agent("hold.agent", ("flight.hold",))
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=(*_standard_agents(), hold_only),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(
                ordinal=1, vehicle_id="uav.alpha", controlling_agent_id="hold.agent"
            )
        ],
        principals=[
            _principal_binding(
                actor_id="logistics.dispatcher",
                role="dispatcher",
                agent_id="dispatcher.agent",
            ),
            _principal_binding(
                actor_id="logistics.business",
                role="business",
                agent_id="business.agent",
            ),
        ],
    )
    validated = validate_logistics_runtime_bindings(
        bindings_document=document,
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    )
    assert validated.aircraft[0].controlling_agent_id == "hold.agent"


def test_goto_land_only_agent_is_accepted_as_controlling(tmp_path: Path) -> None:
    """``flight.goto`` / ``flight.land`` are current PX4 tools, not refused."""
    goto_land = _flight_agent("goto.agent", ("flight.goto", "flight.land"))
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=(*_standard_agents(), goto_land),
    )
    document = _bindings_document(
        aircraft=[
            _aircraft_binding(
                ordinal=1, vehicle_id="uav.alpha", controlling_agent_id="goto.agent"
            )
        ],
        principals=[
            _principal_binding(
                actor_id="logistics.dispatcher",
                role="dispatcher",
                agent_id="dispatcher.agent",
            ),
            _principal_binding(
                actor_id="logistics.business",
                role="business",
                agent_id="business.agent",
            ),
        ],
    )
    validated = validate_logistics_runtime_bindings(
        bindings_document=document,
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    )
    assert validated.aircraft[0].controlling_agent_id == "goto.agent"


def test_dispatcher_business_principals_check_identity_existence_only(
    tmp_path: Path,
) -> None:
    """Principal bindings assert native agent existence, never order authority."""
    dispatcher = _dispatcher_agent("dispatcher.agent")
    business = _unnamed_agent("business.agent")  # exists, has zero tool grants
    fixture = _build_runtime_fixture(
        tmp_path,
        aircraft_count=1,
        px4_vehicle_ids=("uav.alpha",),
        world_uav_ids=("uav.alpha",),
        agents=(dispatcher, business),
    )
    validated = validate_logistics_runtime_bindings(
        bindings_document=_valid_bindings(aircraft_count=1),
        reader=fixture.reader,
        environment=fixture.environment,
        agents=fixture.agents,
        package=fixture.package,
        scenario=fixture.scenario,
    )
    assert [principal.agent_id for principal in validated.principals] == [
        "dispatcher.agent",
        "business.agent",
    ]
