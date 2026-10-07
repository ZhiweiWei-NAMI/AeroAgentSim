"""Strict logistics task package lowering and resolver/projection integration.

Covers:
1. a valid minimal hub-mediated package lowers deterministically with a stable
   canonical digest;
2. reference/actor-role/profile/hub/coordinate mismatches reject instead of
   lowering into a runnable claim;
3. changing order/facility/battery/no-fly parameters changes the canonical
   package digest;
4. the builtin registry exposes the logistics resolver without claiming a
   runnable runtime;
5. the resolver refuses missing real logistics Providers with explicit
   infeasibility.
"""

from __future__ import annotations

from tests.support import fixture_provider_registry

import hashlib
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
    VerifierSpec,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_BUSINESS_CAPABILITIES,
    LOGISTICS_FLIGHT_CAPABILITIES,
    LOGISTICS_PACKAGE_ID,
    LOGISTICS_REQUIRED_CAPABILITIES,
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.integration import (
    LogisticsPackageResolutionError,
    LogisticsTaskPackageResolver,
    load_logistics_package,
)
from aero_bench.tasks.registry import (
    builtin_runtime_hook_factories,
    builtin_task_package_resolvers,
)
from aero_bench.world.resolved import compile_resolved_scenario
from tests.world.support import (
    frame_origin,
    materialize_world_package,
    provider_capabilities,
)


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


def _fleet() -> list[dict[str, object]]:
    return [_fleet_entry()]


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


def _orders() -> list[dict[str, object]]:
    return [_order()]


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


def _no_fly_zones() -> list[dict[str, object]]:
    return [_no_fly_zone()]


def _actor_grant(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {"actor_id": "logistics.dispatcher", "role": "dispatcher"}
    value.update(overrides)
    return value


def _actors() -> list[dict[str, object]]:
    return [
        {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
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


def _package_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "aero-bench.logistics-task/v1",
        "package_id": LOGISTICS_PACKAGE_ID,
        "task_id": "logistics.task",
        "verifier_id": "logistics.verifier",
        "scene": _scene(),
        "facilities": _facilities(),
        "fleet": _fleet(),
        "performanceProfiles": [_performance_profile()],
        "orders": _orders(),
        "noFlyZones": _no_fly_zones(),
        "actors": _actors(),
    }
    document.update(overrides)
    return document


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


_ADAPTER_BY_PROVIDER = {
    "flight": "fixture.flight-artifact-writer",
    "perception": "fixture.observation-artifact-writer",
    "radio": "fixture.network-artifact-writer",
    "traffic": "fixture.traffic-provider",
}


def _build_provider_environment(
    *,
    adapters: dict[str, str],
    capabilities: dict[str, tuple[str, ...]],
) -> EnvironmentSpec:
    providers = tuple(
        ProviderRef(
            provider_id=provider_id,
            adapter=adapters[provider_id],
            port=17601 + index,
            workload=_fixture_runtime(adapters[provider_id]),
            config=SchemaBoundFile(
                file=_placeholder_ref(f"configs/providers/{provider_id}.json"),
                schema_file=_placeholder_ref(f"schemas/providers/{provider_id}.json"),
            ),
            protocol_schema=_placeholder_ref(f"schemas/protocol/{provider_id}.json"),
            capabilities=capabilities,
            artifact_requirements=(),
        )
        for index, (provider_id, capabilities) in enumerate(capabilities.items())
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
        providers=providers,
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


def _build_environment() -> EnvironmentSpec:
    return _build_provider_environment(
        adapters=_ADAPTER_BY_PROVIDER,
        capabilities=provider_capabilities(),
    )


def _build_px4_gazebo_environment() -> EnvironmentSpec:
    """Current-contract world environment with the real PX4 Gazebo Provider.

    The ``flight`` Provider uses the existing ``px4.gazebo`` adapter and
    declares the real ``flight.command`` / ``observation.capture`` capabilities
    on top of the world-required flight capabilities, so the environment still
    compiles against the shared test world.
    """
    adapters = dict(_ADAPTER_BY_PROVIDER)
    adapters["flight"] = "px4.gazebo"
    capabilities = dict(provider_capabilities())
    capabilities["flight"] = (
        *capabilities["flight"],
        *sorted(LOGISTICS_FLIGHT_CAPABILITIES),
    )
    return _build_provider_environment(adapters=adapters, capabilities=capabilities)


def _build_nonflight_fake_environment() -> EnvironmentSpec:
    """Environment where a non-flight business Provider claims the flight caps.

    The ``flight`` Provider keeps a plain adapter without the flight
    capabilities, while a ``business`` Provider (``inspection.business``)
    attempts to declare ``flight.command`` / ``observation.capture``. The
    resolver must not accept those as a physical supply of the flight backend.
    """
    capabilities = dict(provider_capabilities())
    capabilities["business"] = (
        "business.work-order",
        *sorted(LOGISTICS_FLIGHT_CAPABILITIES),
    )
    adapters = dict(_ADAPTER_BY_PROVIDER)
    adapters["business"] = "inspection.business"
    return _build_provider_environment(adapters=adapters, capabilities=capabilities)


def _build_px4_gazebo_network_environment() -> EnvironmentSpec:
    """Current-contract environment plus the existing ``ns3.rpc`` network Provider."""
    adapters = dict(_ADAPTER_BY_PROVIDER)
    adapters["flight"] = "px4.gazebo"
    adapters["network"] = "ns3.rpc"
    capabilities = dict(provider_capabilities())
    capabilities["flight"] = (
        *capabilities["flight"],
        *sorted(LOGISTICS_FLIGHT_CAPABILITIES),
    )
    capabilities["network"] = ("network.delivery",)
    return _build_provider_environment(adapters=adapters, capabilities=capabilities)


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


def _build_task(
    root: Path,
    package_document: dict[str, object],
    *,
    required_capabilities: tuple[str, ...] = (),
    schema: dict[str, object] | None = None,
) -> TaskSpec:
    package_path = "tasks/logistics-package.json"
    config_ref = _write_fixture_file(
        root,
        package_path,
        canonical_json_bytes(package_document) + b"\n",
    )
    schema_ref = _write_fixture_file(
        root,
        "schemas/logistics-package.schema.json",
        canonical_json_bytes(
            schema if schema is not None else _logistics_package_schema()
        )
        + b"\n",
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
        required_capabilities=required_capabilities,
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


def _capability_map(environment: EnvironmentSpec) -> dict[str, tuple[str, ...]]:
    return {
        provider.provider_id: provider.capabilities
        for provider in environment.providers
    }


def test_valid_minimal_hub_mediated_package_lowers_deterministically() -> None:
    raw = _package_document()
    first = lower_logistics_task_package(raw)
    second = lower_logistics_task_package(_package_document())
    assert isinstance(first, LogisticsTaskPackage)
    assert first.canonical_digest() == second.canonical_digest()
    assert re.fullmatch(r"[0-9a-f]{64}", first.canonical_digest())
    assert first.scene.scene_id == "world.city-demo"
    assert first.orders[0].hub_handoff_facility_id == "hub-3"
    legs = first.orders[0].itinerary().legs
    assert [leg.from_facility_id for leg in legs] == ["facility-1", "hub-3"]
    assert [leg.to_facility_id for leg in legs] == ["hub-3", "facility-2"]
    aircraft_ids = {unit.aircraft_id for unit in first.aircraft_units()}
    assert "fleet-alpha:1" in aircraft_ids
    assert first.fleet.catalogue == first.facilities


def test_unknown_facility_reference_rejects() -> None:
    raw = _package_document()
    raw["orders"] = [_order(destinationFacilityId="ghost.site")]
    with pytest.raises(ValueError, match="unknown"):
        lower_logistics_task_package(raw)


def test_aircraft_actor_grant_rejects_unknown_aircraft_identity() -> None:
    raw = _package_document()
    raw["actors"] = [
        {"actor_id": "fleet-alpha:9", "role": "aircraft_agent"},
        {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
        {"actor_id": "logistics.business", "role": "business"},
    ]
    with pytest.raises(ValueError, match="aircraft"):
        lower_logistics_task_package(raw)


def test_dispatcher_identity_cannot_collide_with_aircraft() -> None:
    raw = _package_document()
    raw["actors"] = [
        {"actor_id": "fleet-alpha:1", "role": "dispatcher"},
    ]
    with pytest.raises(ValueError, match="aircraft identity"):
        lower_logistics_task_package(raw)


def test_duplicate_actor_grant_rejects() -> None:
    raw = _package_document()
    raw["actors"] = [
        {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
        {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        lower_logistics_task_package(raw)


def test_performance_profile_rejects_unknown_fleet_entry() -> None:
    raw = _package_document()
    raw["performanceProfiles"] = [_performance_profile(fleetEntryId="ghost-fleet")]
    with pytest.raises(ValueError, match="unknown fleet entry"):
        lower_logistics_task_package(raw)


def test_vertiport_to_vertiport_goods_bypass_rejects() -> None:
    raw = _package_document()
    raw["orders"] = [_order(hubHandoffFacilityId=None)]
    with pytest.raises(ValueError, match="hub handoff"):
        lower_logistics_task_package(raw)


def test_hub_endpoint_is_accepted_as_its_own_handoff() -> None:
    raw = _package_document()
    raw["orders"] = [
        _order(
            sourceFacilityId="hub-3",
            destinationFacilityId="facility-2",
            hubHandoffFacilityId=None,
        )
    ]
    lowered = lower_logistics_task_package(raw)
    assert lowered.orders[0].hub_handoff_facility_id == "hub-3"
    assert len(lowered.orders[0].itinerary().legs) == 1


def test_parameter_changes_change_canonical_digest() -> None:
    base = lower_logistics_task_package(_package_document()).canonical_digest()
    variants = {
        "order.deadline": _package_document(
            orders=[_order(deliverByS=601)],
        ),
        "facility.width": _package_document(
            facilities=[
                _vertiport(widthM=13),
                _vertiport(id="facility-2"),
                _hub(),
                _charger(),
            ],
        ),
        "fleet.battery": _package_document(
            fleet=[_fleet_entry(batteryWh=21000)],
        ),
        "nofly.ceiling": _package_document(
            noFlyZones=[_no_fly_zone(ceilingM=130)],
        ),
    }
    for label, raw in variants.items():
        digest = lower_logistics_task_package(raw).canonical_digest()
        assert digest != base, label
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_builtin_registry_exposes_logistics_resolver_without_runtime() -> None:
    resolvers = {
        resolver.package_id: resolver for resolver in builtin_task_package_resolvers()
    }
    logistics = resolvers[LOGISTICS_PACKAGE_ID]
    assert isinstance(logistics, LogisticsTaskPackageResolver)
    assert logistics.runtime_available is False
    factory_package_ids = {
        factory.package_id for factory in builtin_runtime_hook_factories()
    }
    assert LOGISTICS_PACKAGE_ID not in factory_package_ids


def test_resolver_refuses_missing_required_real_providers(tmp_path: Path) -> None:
    root = tmp_path
    reader, world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(root, raw)
    agents = _build_agents()
    environment = _build_environment()
    resolver = LogisticsTaskPackageResolver()

    projection = resolver.scenario_projection(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
    )
    assert projection.logical_endpoint_ids == ()
    assert projection.logical_capability_ids == ()
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
    # The ResolvedRun binds the package identity through the schema-bound config.
    assert scenario.task.package_id == LOGISTICS_PACKAGE_ID
    assert scenario.task.package_config == task.package.config.file
    assert scenario.task.package_schema == task.package.config.schema_file
    assert scenario.world_id == "world.city-demo"

    assessment = resolver.resolve(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
    )
    assert assessment.package_id == LOGISTICS_PACKAGE_ID
    assert assessment.feasible is False
    assert assessment.success_upper_bound == 0.0
    failed = set(assessment.failed_conditions)
    assert "logistics.provider.pending" in failed
    assert "logistics.runtime.pending" in failed
    assert LOGISTICS_REQUIRED_CAPABILITIES <= failed

    digest_bound = next(
        item
        for item in assessment.bounds
        if item.name == "logistics.package.digest"
    )
    assert digest_bound.value == lower_logistics_task_package(raw).canonical_digest()
    bound_names = [item.name for item in assessment.bounds]
    assert len(bound_names) == len(set(bound_names))


def test_scene_coordinate_mismatch_rejects_at_resolution(tmp_path: Path) -> None:
    root = tmp_path
    reader, world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    raw["scene"]["origin_longitude_deg"] = 100.0  # differs from the world origin
    task = _build_task(root, raw)
    agents = _build_agents()
    environment = _build_environment()
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
        "launch.alpha",
        7,
        _capability_map(environment),
        {provider.provider_id: fixture_provider_registry().runtime_stage_for(provider.adapter) for provider in environment.providers},
        task,
        agents,
        projection,
    )
    with pytest.raises(LogisticsPackageResolutionError, match="origin"):
        resolver.resolve(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
            scenario=scenario,
        )


def test_scene_origin_tolerance_is_absolute(tmp_path: Path) -> None:
    """True frame round-trip is accepted; rel_tol must not widen the bound.

    math.isclose defaults to rel_tol=1e-9, which would inflate the longitude
    bound by ~116x near 116 deg. The scene binding must use the declared
    absolute tolerances only (rel_tol=0.0): +5e-8 deg (50x abs_tol) and a
    +1.1e-6 m altitude discrepancy both reject.
    """
    root = tmp_path
    reader, world_ref, world = materialize_world_package(root)
    base = _package_document()
    base["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(root, base)
    agents = _build_agents()
    environment = _build_environment()
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
        "launch.alpha",
        7,
        _capability_map(environment),
        {provider.provider_id: fixture_provider_registry().runtime_stage_for(provider.adapter) for provider in environment.providers},
        task,
        agents,
        projection,
    )
    # The exact world-origin package resolves (infeasibility is the missing
    # runtime/provider, not a scene-origin mismatch).
    assessment = resolver.resolve(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
    )
    assert assessment.feasible is False
    assert "logistics.provider.pending" in set(assessment.failed_conditions)

    authored = base["scene"]
    perturbations = [
        (
            "longitude +5.0e-8 deg",
            authored["origin_longitude_deg"] + 5.0e-8,
            authored["origin_latitude_deg"],
            authored["origin_altitude_m"],
        ),
        (
            "latitude +5.0e-8 deg",
            authored["origin_longitude_deg"],
            authored["origin_latitude_deg"] + 5.0e-8,
            authored["origin_altitude_m"],
        ),
        (
            "altitude +1.1e-6 m",
            authored["origin_longitude_deg"],
            authored["origin_latitude_deg"],
            authored["origin_altitude_m"] + 1.1e-6,
        ),
    ]
    for label, lon, lat, alt in perturbations:
        candidate = _package_document()
        candidate["scene"]["scene_source_sha256"] = world.asset_digest
        candidate["scene"]["origin_longitude_deg"] = lon
        candidate["scene"]["origin_latitude_deg"] = lat
        candidate["scene"]["origin_altitude_m"] = alt
        probe_task = _build_task(root, candidate)
        try:
            resolver.resolve(
                reader=reader,
                task=probe_task,
                environment=environment,
                agents=agents,
                scenario=scenario,
            )
        except LogisticsPackageResolutionError as error:
            assert "origin" in str(error), label
        else:
            raise AssertionError(f"expected origin rejection for {label}")


def test_unknown_top_level_key_rejects_in_direct_lowerer() -> None:
    raw = _package_document()
    raw["unknownTopLevel"] = True
    with pytest.raises(ValueError, match="unknown top-level"):
        lower_logistics_task_package(raw)


def test_missing_required_top_level_key_rejects_in_direct_lowerer() -> None:
    raw = _package_document()
    del raw["actors"]
    with pytest.raises(ValueError, match="missing required keys"):
        lower_logistics_task_package(raw)


def test_unknown_top_level_key_rejects_through_bundle_reader(tmp_path: Path) -> None:
    """Entrypoint strictness must not depend solely on caller-supplied schema."""
    root = tmp_path
    reader, _world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    raw["unknownTopLevel"] = True
    loose_schema = _logistics_package_schema()
    loose_schema.pop("additionalProperties", None)
    task = _build_task(root, raw, schema=loose_schema)
    with pytest.raises(ValueError, match="unknown top-level"):
        load_logistics_package(reader=reader, task=task)


def test_declared_logistics_capabilities_report_missing_physical_providers(
    tmp_path: Path,
) -> None:
    """A TaskSpec honestly declaring the logistics capabilities must not hit the
    generic scenario logical-capability invariant: scenario_projection reports
    the missing real Providers clearly (pending implementation, not input error)
    while still accepting the legitimate flight/orders/airspace declarations.
    """
    root = tmp_path
    reader, _world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(
        root,
        raw,
        required_capabilities=tuple(sorted(LOGISTICS_REQUIRED_CAPABILITIES)),
    )
    agents = _build_agents()
    environment = _build_environment()
    resolver = LogisticsTaskPackageResolver()
    with pytest.raises(LogisticsPackageResolutionError) as excinfo:
        resolver.scenario_projection(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
        )
    message = str(excinfo.value)
    for capability in LOGISTICS_REQUIRED_CAPABILITIES:
        assert capability in message
    assert "logistics.facilities.state" in message
    assert "logistics.orders.authority" in message
    assert "not an input error" in message
    assert "pending implementation" in message


def test_actual_px4_gazebo_flight_capabilities_are_recognized(
    tmp_path: Path,
) -> None:
    """The existing px4.gazebo declaration supplies the two real flight caps.

    A TaskSpec honestly declaring the full logistics capability model must not
    be faulted for ``flight.command`` / ``observation.capture`` when the real
    ``px4.gazebo`` Provider is physically declared; only the new ``logistics.*``
    business capabilities stay missing.
    """
    root = tmp_path
    reader, _world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(
        root,
        raw,
        required_capabilities=tuple(sorted(LOGISTICS_REQUIRED_CAPABILITIES)),
    )
    agents = _build_agents()
    environment = _build_px4_gazebo_environment()
    resolver = LogisticsTaskPackageResolver()
    with pytest.raises(LogisticsPackageResolutionError) as excinfo:
        resolver.scenario_projection(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
        )
    message = str(excinfo.value)
    for capability in LOGISTICS_FLIGHT_CAPABILITIES:
        assert capability not in message
    for capability in LOGISTICS_BUSINESS_CAPABILITIES:
        assert capability in message
    assert "not an input error" in message
    assert "pending implementation" in message


def test_px4_gazebo_flight_caps_present_but_runtime_stays_infeasible(
    tmp_path: Path,
) -> None:
    """Recognizing the flight caps never claims a runnable logistics runtime.

    ``resolve`` still reports explicit infeasibility: the missing logistics
    business capabilities and the pending runtime, while the two real flight
    capabilities supplied by ``px4.gazebo`` are not listed as missing.
    """
    root = tmp_path
    reader, world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(root, raw)
    agents = _build_agents()
    environment = _build_px4_gazebo_environment()
    resolver = LogisticsTaskPackageResolver()
    assert resolver.runtime_available is False
    projection = resolver.scenario_projection(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
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
    assessment = resolver.resolve(
        reader=reader,
        task=task,
        environment=environment,
        agents=agents,
        scenario=scenario,
    )
    assert assessment.feasible is False
    assert assessment.success_upper_bound == 0.0
    failed = set(assessment.failed_conditions)
    assert "logistics.provider.pending" in failed
    assert "logistics.runtime.pending" in failed
    assert LOGISTICS_BUSINESS_CAPABILITIES <= failed
    assert not (LOGISTICS_FLIGHT_CAPABILITIES & failed)


def test_nonflight_business_provider_cannot_fake_flight_capabilities(
    tmp_path: Path,
) -> None:
    """A non-flight Provider claiming the flight caps is not trusted.

    The ``flight.command`` / ``observation.capture`` capabilities are supplied
    only by the real ``px4.gazebo`` adapter; a business Provider that merely
    declares the names leaves them missing and gets the clear domain diagnosis.
    """
    root = tmp_path
    reader, _world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    task = _build_task(
        root,
        raw,
        required_capabilities=tuple(sorted(LOGISTICS_FLIGHT_CAPABILITIES)),
    )
    agents = _build_agents()
    environment = _build_nonflight_fake_environment()
    resolver = LogisticsTaskPackageResolver()
    with pytest.raises(LogisticsPackageResolutionError) as excinfo:
        resolver.scenario_projection(
            reader=reader,
            task=task,
            environment=environment,
            agents=agents,
        )
    message = str(excinfo.value)
    assert "flight.command" in message
    assert "observation.capture" in message
    assert "not an input error" in message


def test_optional_existing_network_delivery_is_not_package_excluded(
    tmp_path: Path,
) -> None:
    """Existing physically-declared capabilities are not package-whitelisted out.

    ``network.delivery`` on the real ``ns3.rpc`` Provider is a legitimate
    additional requirement; it is neither rejected as package-external nor
    listed as a missing logistics prerequisite.
    """
    root = tmp_path
    reader, _world_ref, world = materialize_world_package(root)
    raw = _package_document()
    raw["scene"]["scene_source_sha256"] = world.asset_digest
    agents = _build_agents()
    environment = _build_px4_gazebo_network_environment()
    resolver = LogisticsTaskPackageResolver()

    network_task = _build_task(
        root,
        raw,
        required_capabilities=("network.delivery",),
    )
    projection = resolver.scenario_projection(
        reader=reader,
        task=network_task,
        environment=environment,
        agents=agents,
    )
    assert projection.logical_endpoint_ids == ()
    assert projection.logical_capability_ids == ()

    mixed_task = _build_task(
        root,
        raw,
        required_capabilities=("network.delivery", "logistics.facilities.state"),
    )
    with pytest.raises(LogisticsPackageResolutionError) as excinfo:
        resolver.scenario_projection(
            reader=reader,
            task=mixed_task,
            environment=environment,
            agents=agents,
        )
    message = str(excinfo.value)
    assert "outside the logistics package" not in message
    assert "logistics.facilities.state" in message
    assert "network.delivery" not in message

def test_registered_business_only_supplies_implemented_capabilities() -> None:
    from aero_bench.providers.logistics_business.adapter import SUPPORTED_CAPABILITIES
    from aero_bench.tasks.logistics.integration import _physical_logistics_capabilities

    declared = tuple(sorted(LOGISTICS_REQUIRED_CAPABILITIES))
    environment = _build_provider_environment(
        adapters={"business": "logistics.business"},
        capabilities={"business": declared},
    )
    assert _physical_logistics_capabilities(environment) == (
        SUPPORTED_CAPABILITIES & frozenset(declared)
    )
    assert "logistics.orders.scheduled-arrivals" not in declared
    assert LogisticsTaskPackageResolver().runtime_available is False


def test_business_capabilities_require_actual_adapter_and_declaration() -> None:
    from aero_bench.tasks.logistics.integration import _physical_logistics_capabilities

    for adapter, claims, expected in (
        ("inspection.business", ("logistics.orders.authority",), frozenset()),
        ("logistics.business", ("logistics.orders.authority",),
         frozenset({"logistics.orders.authority"})),
    ):
        environment = _build_provider_environment(
            adapters={"business": adapter}, capabilities={"business": claims},
        )
        assert _physical_logistics_capabilities(environment) == expected
