"""Module tests for the container service of the Logistics Business provider.

The service module is loaded from ``containers/logistics-business/service.py``
so the tests exercise the exact bytes shipped into the digest-pinned image.

This is an incomplete-provider development checkpoint. The service is the
single authority for the canonical non-physical order ledger, the facility
catalogue, the staged receipts, and the append-only history. Physical
pickup/handoff/deliver transitions are explicitly refused until the later
authoritative physical-evidence interface is wired; every ``pick_up``,
``handoff`` or ``deliver`` command returns an ordered ``received, failed``
receipt naming the missing interface, never an arbitrary ``evidence_ref`` or a
synthetic boolean.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from aero_bench.executor.contracts import ProviderWorkloadContract
from aero_bench.providers.rpc import (
    JsonLineRpcServer,
    JsonLineRpcTransport,
    ProviderRemoteError,
)
from aero_bench.runtime.contracts import (
    BusinessEnvironmentStageRequest,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    stage_barrier_digest_value,
)
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import lower_logistics_task_package
from aero_bench.tasks.logistics.order_arrivals import (
    OrderArrivalEvent,
    replay_order_arrivals,
)
from aero_bench.tasks.logistics.orders import OrderRequest
from aero_bench.world.resolved import ResolvedScenario, scenario_assets_for_workload
from containers.selfcheck_scenario import (
    ScenarioProvider,
    build_resolved_scenario,
)
from tests.tasks.test_logistics_package import _package_document


_SERVICE_PATH = (
    Path(__file__).parents[2] / "containers" / "logistics-business" / "service.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_logistics_business_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)


RUN_ID = "a" * 64
IMAGE = "registry.invalid/logistics-business@sha256:" + "1" * 64
FILE_DIGEST = "c" * 64
SERVICE_PORT = 18435
PROVIDER_ID = "logistics.business"
PROTOCOL_VERSION = "aero-bench.logistics-business-rpc/v1"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
SESSION_TOKEN = "9" * 64
SEED = 1701
CAPABILITIES = ("logistics.facilities.state", "logistics.orders.authority")
PENDING_CAPABILITIES = ("logistics.airspace.events", "logistics.delivery.observation")


@dataclass(frozen=True, slots=True)
class _Endpoint:
    host: str
    port: int


def _file_ref(path: str) -> dict[str, str]:
    return {"path": path, "sha256": FILE_DIGEST}


def _requirement() -> dict[str, object]:
    return {
        "artifact_id": "artifact.logistics",
        "artifact_type": "logistics.business.state",
        "producer_id": "logistics.business",
        "visibility": "private",
        "relative_path": "logistics/state.json",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }


def _logistics_package_document() -> dict[str, object]:
    return lower_logistics_task_package(_package_document()).model_dump(mode="json")


def _config_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": _logistics_package_document(),
        "observation": None,
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


def _write_config(root: Path, *, canonical: bool) -> dict[str, dict[str, str]]:
    config_path = root / "provider.config.json"
    if canonical:
        config_path.write_bytes(canonical_json_bytes(_config_document()) + b"\n")
    else:
        config_path.write_bytes(json.dumps(_config_document()).encode("utf-8"))
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


def _two_aircraft_package_document() -> dict[str, object]:
    """Two expanded aircraft from one fleet entry plus dispatcher/business grants."""

    return _package_document(
        fleet=[
            {
                "id": "fleet-alpha",
                "assetId": "model:logistics-drone-v1",
                "count": 2,
                "homeFacilityId": "facility-1",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 5,
            }
        ],
        performanceProfiles=[
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
        orders=[
            {
                "id": "order-1",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 600,
            },
            {
                "id": "order-2",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 700,
            },
        ],
        actors=[
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "fleet-alpha:2", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    )


def _central_agent_config_document() -> dict[str, object]:
    """One native principal explicitly bound to two declared aircraft actors."""

    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": lower_logistics_task_package(
            _two_aircraft_package_document()
        ).model_dump(mode="json"),
        "observation": None,
        "principal_bindings": [
            {
                "principal_id": "central.fleet",
                "actor_id": "fleet-alpha:1",
                "role": "aircraft_agent",
            },
            {
                "principal_id": "central.fleet",
                "actor_id": "fleet-alpha:2",
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


def _mixed_capacity_package_document() -> dict[str, object]:
    """A low-payload aircraft plus a high-payload aircraft that could lift order-1."""

    return _package_document(
        fleet=[
            {
                "id": "fleet-alpha",
                "assetId": "model:logistics-drone-v1",
                "count": 1,
                "homeFacilityId": "facility-1",
                "batteryWh": 20000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 2,
            },
            {
                "id": "fleet-bravo",
                "assetId": "model:logistics-drone-v2",
                "count": 1,
                "homeFacilityId": "facility-1",
                "batteryWh": 30000,
                "reserveRatio": 0.2,
                "maxPayloadKg": 50,
            },
        ],
        performanceProfiles=[
            {
                "fleetEntryId": "fleet-alpha",
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.6, "yM": 0.6, "zM": 0.3},
                "cruiseSpeedMps": 15,
                "cruisePowerW": 900,
                "hoverPowerW": 700,
                "chargeEfficiency": 0.85,
            },
            {
                "fleetEntryId": "fleet-bravo",
                "sourceLabel": "operator estimate",
                "provenance": "selected-city v3 planning data",
                "aircraftBody": {"xM": 0.7, "yM": 0.7, "zM": 0.4},
                "cruiseSpeedMps": 18,
                "cruisePowerW": 1100,
                "hoverPowerW": 850,
                "chargeEfficiency": 0.85,
            },
        ],
        actors=[
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "fleet-bravo:1", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    )


def _mixed_capacity_config_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": lower_logistics_task_package(
            _mixed_capacity_package_document()
        ).model_dump(mode="json"),
        "observation": None,
        "principal_bindings": [
            {
                "principal_id": "agent.provider",
                "actor_id": "fleet-alpha:1",
                "role": "aircraft_agent",
            },
            {
                "principal_id": "agent.bravo",
                "actor_id": "fleet-bravo:1",
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


def _future_order_config_document() -> dict[str, object]:
    """One immediately released order plus one order gated at releaseAtS=3."""

    package = _package_document(
        orders=[
            {
                "id": "order-t0",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 0,
                "deliverByS": 600,
            },
            {
                "id": "order-future",
                "sourceFacilityId": "facility-1",
                "destinationFacilityId": "facility-2",
                "hubHandoffFacilityId": "hub-3",
                "cargoKg": 1,
                "releaseAtS": 3,
                "deliverByS": 900,
            },
        ],
        actors=[
            {"actor_id": "fleet-alpha:1", "role": "aircraft_agent"},
            {"actor_id": "logistics.dispatcher", "role": "dispatcher"},
            {"actor_id": "logistics.business", "role": "business"},
        ],
    )
    return {
        "schema_version": "aero-bench.logistics-business/v3",
        "scheduled_orders": [],
        "provider_id": PROVIDER_ID,
        "task_package": lower_logistics_task_package(package).model_dump(mode="json"),
        "observation": None,
        "principal_bindings": [
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


def _prepare_payload_for(
    task_package: dict[str, object], config_digest: str
) -> dict[str, object]:
    """Prepare request bound to an explicit (custom) task package document."""

    return {
        "provider_id": PROVIDER_ID,
        "run_id": RUN_ID,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": IMAGE,
        "config_digest": config_digest,
        "artifact_requirements": [_requirement()],
        "session_token": SESSION_TOKEN,
        "task_id": task_package["task_id"],
        "package_digest": hashlib.sha256(
            canonical_json_bytes(task_package)
        ).hexdigest(),
        "task_package": task_package,
        "capabilities": list(CAPABILITIES),
    }


async def _advance_to(service, *, to_tick: int) -> None:
    """Advance the authoritative barrier to ``to_tick`` through every tick."""

    previous_scene_state = None
    for tick in range(1, to_tick + 1):
        request = _stage_request(
            tick=tick,
            sim_time_ns=tick * 1_000_000_000,
            previous_scene_state=previous_scene_state,
        )
        await service.handle("step_stage", _stage_payload(request))
        previous_scene_state = request.scene_state
    assert service._current == (to_tick, to_tick * 1_000_000_000)


def _make_service_with_config(
    tmp_path: Path, config_document: dict[str, object], *, port: int = SERVICE_PORT
):
    """Build a service whose provider bundle carries an explicit config document."""

    (tmp_path / "artifacts").mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "provider.config.json"
    config_path.write_bytes(canonical_json_bytes(config_document) + b"\n")
    schema_path = tmp_path / "provider.schema.json"
    schema_path.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    refs = {
        "config": {
            "path": "provider.config.json",
            "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        },
        "schema": {
            "path": "provider.schema.json",
            "sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest(),
        },
    }
    identity = _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id=PROVIDER_ID,
        provider_port=port,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=SEED,
        contract=_contract(refs, port),
    )
    return _SERVICE.LogisticsBusinessProviderService(
        bundle_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=port,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )


async def _prepared_service_with_config(
    tmp_path: Path, config_document: dict[str, object]
):
    service = _make_service_with_config(tmp_path, config_document)
    task_package = config_document["task_package"]
    await service.handle(
        "prepare",
        _prepare_payload_for(task_package, service._workload_identity.config_digest),
    )
    await service.handle(
        "reset",
        _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED}),
    )
    return service


def _write_bundle(root: Path) -> dict[str, dict[str, str]]:
    return _write_config(root, canonical=True)


def _resolved_scenario() -> ResolvedScenario:
    raw = build_resolved_scenario(
        seed=SEED,
        providers=(
            ScenarioProvider(
                PROVIDER_ID,
                ("mission",),
                CAPABILITIES,
                "business_environment",
            ),
            ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
        ),
        dynamic_provider_id="flight",
        verifier_id="logistics.verifier",
        world_id="world.city-demo",
    )
    return ResolvedScenario.model_validate(raw)


def _contract(refs: dict[str, dict[str, str]], port: int) -> ProviderWorkloadContract:
    scenario = _resolved_scenario()
    return ProviderWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="provider",
        run_id=RUN_ID,
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
            "capabilities": list(CAPABILITIES),
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


def _identity(root: Path, *, port: int = SERVICE_PORT) -> _SERVICE.WorkloadIdentity:
    refs = _write_bundle(root)
    return _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id=PROVIDER_ID,
        provider_port=port,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=SEED,
        contract=_contract(refs, port),
    )


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    """The Harness session presents its granted token on every frame."""

    return {"session_token": SESSION_TOKEN, **payload}


def _prepare_payload(config_digest: str) -> dict[str, object]:
    task_package = _logistics_package_document()
    return {
        "provider_id": PROVIDER_ID,
        "run_id": RUN_ID,
        "protocol_version": PROTOCOL_VERSION,
        "runtime_image": IMAGE,
        "config_digest": config_digest,
        "artifact_requirements": [_requirement()],
        "session_token": SESSION_TOKEN,
        "task_id": "logistics.task",
        "package_digest": hashlib.sha256(canonical_json_bytes(task_package)).hexdigest(),
        "task_package": task_package,
        "capabilities": list(CAPABILITIES),
    }


def _command_payload(
    tool_id: str,
    command_id: str,
    tick: int,
    agent_id: str,
    **arguments: object,
) -> dict[str, object]:
    return {
        "provider_id": PROVIDER_ID,
        "run_id": RUN_ID,
        "request": {
            "run_id": RUN_ID,
            "command_id": command_id,
            "agent_id": agent_id,
            "tool_id": tool_id,
            "issued_at": {"tick": tick, "sim_time_ns": tick * 1_000_000_000},
            "arguments": [
                {"name": name, "value": value}
                for name, value in sorted(arguments.items())
            ],
        },
    }


def _make_service(tmp_path: Path, *, port: int = SERVICE_PORT):
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    return _SERVICE.LogisticsBusinessProviderService(
        bundle_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=port,
        workload_identity=_identity(tmp_path, port=port),
        session_token=SESSION_TOKEN,
    )


async def _prepared_service(tmp_path: Path):
    service = _make_service(tmp_path)
    digest = service._workload_identity.config_digest
    await service.handle("prepare", _prepare_payload(digest))
    await service.handle(
        "reset",
        _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED}),
    )
    return service


async def _advance(service, *, tick: int = 1) -> None:
    """Advance the authoritative barrier to ``tick`` (1e9 ns per tick)."""

    await service.handle(
        "step_stage",
        _stage_payload(_stage_request(tick=tick, sim_time_ns=tick * 1_000_000_000)),
    )


def _stage_request(
    *,
    tick: int,
    sim_time_ns: int,
    scenario: ResolvedScenario | None = None,
    previous_scene_state=None,
) -> BusinessEnvironmentStageRequest:
    scenario = scenario or _resolved_scenario()
    static_entity = next(
        entity for entity in scenario.entities if entity.state == "static"
    )
    static_scenario = scenario.model_copy(update={"entities": (static_entity,)})
    assembler = SceneStateAssembler(static_scenario)
    target = SimulationTime(tick=tick, sim_time_ns=sim_time_ns)
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": RUN_ID,
        "scenario_digest": scenario.scenario_digest,
        "at": target,
        "stage": "motion",
        "input_scene_state_digest": None,
        "predecessor_barriers": (),
        "provider_ids": (),
        "receipts": (),
        "receipt_digests": (),
    }
    unsigned = StageBarrier.model_construct(
        **barrier_fields,
        barrier_digest="0" * 64,
    )
    barrier = StageBarrier(
        **barrier_fields,
        barrier_digest=stage_barrier_digest_value(unsigned),
    )
    scene_state = assembler.assemble(
        run_id=RUN_ID,
        at=target,
        barrier=barrier,
        contributions=(),
        previous_scene_state=previous_scene_state,
    )
    return BusinessEnvironmentStageRequest(
        schema_version="aero-bench.provider-stage-request/v1",
        run_id=RUN_ID,
        scenario_digest=scenario.scenario_digest,
        provider_id=PROVIDER_ID,
        target=target,
        stage="business_environment",
        scene_state=scene_state,
        scene_state_digest=scene_state.scene_state_digest,
        predecessor_barriers=(
            StageBarrierDigest(
                stage="motion",
                barrier_digest=barrier.barrier_digest,
            ),
        ),
    )


def _stage_payload(request: BusinessEnvironmentStageRequest) -> dict[str, object]:
    return _authorized(
        {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "protocol_version": PROTOCOL_VERSION,
            "request": request.model_dump(mode="json"),
        }
    )


def _run(coro) -> None:
    asyncio.run(coro)


def test_service_error_carries_stable_failure_classes() -> None:
    for code in (
        "request.invalid",
        "identity.mismatch",
        "not.ready",
        "principal.denied",
        "query.rejected",
        "artifact.write-failed",
        "operation.unknown",
    ):
        error = _SERVICE.LogisticsBusinessServiceError(code, "detail")
        assert error.code == code
        assert error.detail == "detail"


def test_service_config_is_strict_domain_declaration(tmp_path: Path) -> None:
    refs = _write_bundle(tmp_path)
    config_path = tmp_path / "provider.config.json"
    document = json.loads(config_path.read_bytes())
    assert "runtime_image" not in document
    assert "endpoint" not in document
    assert "protocol_version" not in document
    assert "capabilities" not in document
    assert document["provider_id"] == PROVIDER_ID
    assert document["task_package"]["task_id"] == "logistics.task"
    assert refs["config"]["sha256"] == hashlib.sha256(
        config_path.read_bytes()
    ).hexdigest()


def test_service_rejects_non_canonical_contract_bytes(tmp_path: Path) -> None:
    refs = _write_bundle(tmp_path)
    document = _contract(refs, SERVICE_PORT).model_dump(mode="json")
    contract_path = tmp_path / "contract.json"
    contract_path.write_bytes(json.dumps(document).encode("utf-8"))
    with pytest.raises(_SERVICE.LogisticsBusinessServiceError, match="canonical"):
        _SERVICE._load_canonical_model_bytes(
            contract_path, ProviderWorkloadContract, label="AERO_BENCH_CONTRACT"
        )
    contract_path.write_bytes(canonical_json_bytes(document) + b"\n")
    loaded = _SERVICE._load_canonical_model_bytes(
        contract_path, ProviderWorkloadContract, label="AERO_BENCH_CONTRACT"
    )
    assert loaded == ProviderWorkloadContract.model_validate(document)


def test_service_rejects_non_canonical_provider_config_bytes(
    tmp_path: Path,
) -> None:
    refs = _write_config(tmp_path, canonical=False)
    identity = _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id=PROVIDER_ID,
        provider_port=SERVICE_PORT,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=SEED,
        contract=_contract(refs, SERVICE_PORT),
    )
    (tmp_path / "artifacts").mkdir()
    service = _SERVICE.LogisticsBusinessProviderService(
        bundle_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=SERVICE_PORT,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )
    with pytest.raises(
        _SERVICE.LogisticsBusinessServiceError, match="not canonical"
    ) as errors:
        asyncio.run(service.handle("prepare", _prepare_payload(identity.config_digest)))
    assert errors.value.code == "identity.mismatch"


def test_service_probe_accepts_identity_before_prepare(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    response = asyncio.run(service.handle("probe", {}))
    assert set(response) == {
        "schema_version",
        "status",
        "run_id",
        "provider_id",
        "adapter",
        "runtime_image",
        "config_digest",
    }
    assert response["schema_version"] == PROVIDER_PROBE_SCHEMA
    assert response["status"] == "accepting"
    assert response["provider_id"] == PROVIDER_ID
    assert response["run_id"] == RUN_ID
    assert response["adapter"] == _SERVICE.PROVIDER_ADAPTER
    assert response["runtime_image"] == IMAGE
    assert response["config_digest"] == service._workload_identity.config_digest
    assert service._config is None
    assert service._machine is None


def test_service_probe_rejects_non_empty_payload(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    with pytest.raises(
        _SERVICE.LogisticsBusinessServiceError, match="must be empty"
    ) as errors:
        asyncio.run(service.handle("probe", {"session_token": SESSION_TOKEN}))
    assert errors.value.code == "request.invalid"


def test_service_denies_wrong_token_before_identity_on_every_operation(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        before = service._machine.ledger.model_dump(mode="json")
        denied = {
            "provider_id": "other.provider",
            "run_id": "b" * 64,
            "session_token": "8" * 64,
        }
        unauthenticated = {
            "provider_id": "other.provider",
            "run_id": "b" * 64,
        }
        operations = ("reset", "step_stage", "command", "query", "snapshot", "shutdown")
        for payload in (denied, unauthenticated):
            for operation in operations:
                with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
                    await service.handle(operation, payload)
                assert errors.value.code == "principal.denied", operation
        assert service._machine.ledger.model_dump(mode="json") == before
        assert service._current == (0, 0)

    _run(run())


def test_service_denies_racing_prepare_with_wrong_executor_token(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        digest = service._workload_identity.config_digest
        payload = _prepare_payload(digest)
        for token in ("8" * 64, "a" * 64, SESSION_TOKEN[:-1] + "e"):
            with pytest.raises(
                _SERVICE.LogisticsBusinessServiceError, match="session token"
            ) as errors:
                await service.handle("prepare", {**payload, "session_token": token})
            assert errors.value.code == "principal.denied"
        assert service._config is None
        prepared = await service.handle("prepare", payload)
        assert prepared["status"] == "ready"
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="session token"
        ) as errors:
            await service.handle(
                "reset",
                {"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED},
            )
        assert errors.value.code == "principal.denied"

    _run(run())


def test_service_rejects_prepare_binding_drift(tmp_path: Path) -> None:
    """Wrong run/provider/config/package digests are identity mismatches."""

    async def run() -> None:
        service = _make_service(tmp_path)
        digest = service._workload_identity.config_digest
        payload = _prepare_payload(digest)
        drifts = (
            ({"run_id": "b" * 64}, "identity.mismatch"),
            ({"provider_id": "other.provider"}, "identity.mismatch"),
            (
                {"runtime_image": "registry.invalid/other@sha256:" + "2" * 64},
                "identity.mismatch",
            ),
            ({"config_digest": "e" * 64}, "identity.mismatch"),
            ({"task_id": "another.task"}, "identity.mismatch"),
            ({"package_digest": "e" * 64}, "identity.mismatch"),
            ({"protocol_version": "aero-bench.other-rpc/v1"}, "identity.mismatch"),
            ({"extra": True}, "request.invalid"),
        )
        for drift, expected_code in drifts:
            with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
                await service.handle("prepare", {**payload, **drift})
            assert errors.value.code == expected_code
        drifted_package = dict(payload["task_package"])
        drifted_package["task_id"] = "another.task"
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await service.handle(
                "prepare", {**payload, "task_package": drifted_package}
            )
        assert errors.value.code == "identity.mismatch"
        prepared = await service.handle("prepare", payload)
        assert prepared["status"] == "ready"
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="twice"
        ) as errors:
            await service.handle("prepare", payload)
        assert errors.value.code == "not.ready"

    _run(run())


def _pending_contract(
    refs: dict[str, dict[str, str]],
    port: int,
    *,
    pending_capabilities: tuple[str, ...] = ("logistics.airspace.events",),
) -> ProviderWorkloadContract:
    """A contract (as a stale manifest could declare) claiming a pending cap."""

    capability_ids = tuple(sorted((*pending_capabilities, "logistics.orders.authority")))
    scenario = ResolvedScenario.model_validate(
        build_resolved_scenario(
            seed=SEED,
            providers=(
                ScenarioProvider(PROVIDER_ID, ("mission",), capability_ids, "business_environment"),
                ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
            ),
            dynamic_provider_id="flight",
            verifier_id="logistics.verifier",
            world_id="world.city-demo",
        )
    )
    return ProviderWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="provider",
        run_id=RUN_ID,
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
            "capabilities": list(capability_ids),
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


def _pending_service(
    tmp_path: Path,
    *,
    pending_capabilities: tuple[str, ...] = ("logistics.airspace.events",),
):
    refs = _write_bundle(tmp_path)
    identity = _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id=PROVIDER_ID,
        provider_port=SERVICE_PORT,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=SEED,
        contract=_pending_contract(
            refs,
            SERVICE_PORT,
            pending_capabilities=pending_capabilities,
        ),
    )
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    return _SERVICE.LogisticsBusinessProviderService(
        bundle_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=SERVICE_PORT,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )


def test_service_refuses_unimplemented_capability_claim(tmp_path: Path) -> None:
    async def run() -> None:
        pending = _pending_service(tmp_path)
        payload = _prepare_payload(pending._workload_identity.config_digest)
        payload["capabilities"] = [
            "logistics.airspace.events",
            "logistics.orders.authority",
        ]
        # The manifest exactly matches its workload contract yet claims a
        # capability whose physical interface is not implemented: refused.
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError,
            match="not yet implemented",
        ) as errors:
            await pending.handle("prepare", payload)
        assert errors.value.code == "identity.mismatch"
        assert "logistics.airspace.events" in errors.value.detail
        assert pending._config is None

        # Even a single pending observation claim is refused.
        observation = _pending_service(
            tmp_path,
            pending_capabilities=("logistics.delivery.observation",),
        )
        observation_payload = _prepare_payload(
            observation._workload_identity.config_digest
        )
        observation_payload["capabilities"] = [
            "logistics.delivery.observation",
            "logistics.orders.authority",
        ]
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError,
            match="not yet implemented",
        ):
            await observation.handle("prepare", observation_payload)
        assert observation._config is None

        # A capability list that drifts from the workload contract is refused
        # on identity grounds, before the naming guard is ever reached.
        standard = _make_service(tmp_path)
        standard_payload = _prepare_payload(
            standard._workload_identity.config_digest
        )
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await standard.handle(
                "prepare",
                {
                    **standard_payload,
                    "capabilities": ["logistics.airspace.events"],
                },
            )
        assert errors.value.code == "identity.mismatch"
        assert "differ from the workload contract" in errors.value.detail

    _run(run())


def test_service_enforces_trusted_principal_bindings(tmp_path: Path) -> None:
    """Role forgery: the service never trusts actor_id/actor_role from agents.

    The native principal is attested by the Harness Gateway; the service maps
    it through the provider config's canonical ``OrderActorGrant`` bindings and
    derives the canonical ``actor_id``/``actor_role`` itself. An unbound
    principal is denied outright, and a bound principal that tries to issue an
    event outside its canonical role is rejected by the domain state machine —
    never applied.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="is not bound to logistics"
        ) as errors:
            await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        "logistics.order.offer",
                        "command.forge",
                        1,
                        "intruder.agent",
                        order_id="order-1",
                        assignee_id="fleet-alpha:1",
                        actor_id="logistics.dispatcher",
                        expected_version=0,
                    )
                ),
            )
        assert errors.value.code == "principal.denied"
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

        # An aircraft agent (canonical fleet-alpha:1) cannot forge a dispatcher
        # offer even though the arguments otherwise form a legal offer.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "command.forge2",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="fleet-alpha:1",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        detail = response["receipts"][-1]["detail"]
        assert "offer cannot be issued by aircraft_agent" in detail
        assert "actor_role" not in "".join(
            str(argument) for argument in response["receipts"]
        )
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

        # The dispatcher cannot forge an aircraft accept.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "command.forge3",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "accept cannot be issued by dispatcher" in response["receipts"][-1][
            "detail"
        ]

        # A dispatcher offer that names a non-aircraft assignee fails the
        # canonical assigned-aircraft gate.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "command.forge4",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="logistics.dispatcher",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "not a declared aircraft unit" in (
            response["receipts"][-1]["detail"]
        )
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

    _run(run())


def test_service_applies_legitimate_nonphysical_lifecycle(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        lifecycle = (
            ("command.offer", 1, "logistics.dispatcher", "logistics.order.offer", {
                "order_id": "order-1",
                "assignee_id": "fleet-alpha:1",
                "actor_id": "logistics.dispatcher",
                "expected_version": 0,
            }),
            ("command.accept", 1, "agent.provider", "logistics.order.accept", {
                "order_id": "order-1",
                "actor_id": "fleet-alpha:1",
                "expected_version": 1,
            }),
            ("command.assign", 1, "logistics.dispatcher", "logistics.order.assign", {
                "order_id": "order-1",
                "assignee_id": "fleet-alpha:1",
                "actor_id": "logistics.dispatcher",
                "capacity_kg": 5,
                "expected_version": 2,
            }),
        )
        for command_id, tick, agent_id, tool_id, arguments in lifecycle:
            response = await service.handle(
                "command",
                _authorized(_command_payload(tool_id, command_id, tick, agent_id, **arguments)),
            )
            assert [item["phase"] for item in response["receipts"]] == [
                "received",
                "accepted",
                "applied",
                "completed",
            ]
            assert response["history_record"] == (
                service._machine.ledger.history[-1].model_dump(mode="json")
            )
        order = service._machine.ledger.orders[0]
        assert order.status.value == "assigned"
        assert order.assignee_id == "fleet-alpha:1"
        assert order.capacity_kg == 5.0

    _run(run())


def test_service_refuses_physical_pickup_until_evidence_interface(
    tmp_path: Path,
) -> None:
    """Mandatory-hub physical commands are refused with an explicit failure.

    ``pick_up`` is an aircraft-agent event that requires a hub-mediated
    mandate; even with the correct canonical grant and the mandatory hub, this
    checkpoint cannot accept an arbitrary ``evidence_ref`` as proof, so the
    ordered receipts terminate in ``failed`` naming the missing interface.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.pick_up",
                    "command.pickup",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    expected_version=0,
                    evidence_ref="evidence.photo.1",
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert set(response) == {"receipts"}
        detail = response["receipts"][-1]["detail"]
        assert "physical evidence interface pending" in detail
        assert "pick_up" in detail
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

        for tool_id, command_id in (
            ("logistics.order.handoff", "command.handoff"),
            ("logistics.order.deliver", "command.deliver"),
        ):
            response = await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        tool_id,
                        command_id,
                        1,
                        "agent.provider",
                        order_id="order-1",
                        expected_version=0,
                        evidence_ref="evidence.photo.2",
                        handoff_facility_id="hub-3",
                    )
                ),
            )
            assert [item["phase"] for item in response["receipts"]] == [
                "received",
                "failed",
            ]
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

    _run(run())


def test_service_requires_optimistic_version(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "command.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=1,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert set(response) == {"receipts"}
        assert service._machine.ledger.model_dump(mode="json")["version"] == 0

    _run(run())


def test_service_step_stage_is_monotonic_and_receipt_bound(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        first = _stage_request(tick=1, sim_time_ns=1_000_000_000)
        response = await service.handle("step_stage", _stage_payload(first))
        result = response["result"]
        receipt = result["step_receipt"]
        assert receipt["reached"] == {"tick": 1, "sim_time_ns": 1_000_000_000}
        state_events = [
            event
            for event in receipt["events"]
            if event["payload_schema_id"] == _SERVICE.STATE_SCHEMA
        ]
        assert len(state_events) == 1
        fields = {item["name"]: item["value"] for item in state_events[0]["payload"]}
        assert fields["stage_input_scenario_digest"] == first.scenario_digest
        assert fields["stage_input_scene_state_digest"] == first.scene_state_digest
        assert fields["stage_input_motion_barrier_digest"] == (
            first.predecessor_barriers[0].barrier_digest
        )
        assert fields["current_time_ns"] == 1_000_000_000
        assert fields["stage_input_count"] == 1
        assert fields["state_digest"] == receipt["state_digest"]
        assert result["input_scene_state_digest"] == first.scene_state_digest
        assert not result["contribution"]["samples"]
        assert not result["contribution"]["attribute_updates"]

        # A second stage must advance monotonically.
        second = _stage_request(
            tick=2,
            sim_time_ns=2_000_000_000,
            previous_scene_state=first.scene_state,
        )
        response = await service.handle("step_stage", _stage_payload(second))
        assert response["result"]["step_receipt"]["reached"] == {
            "tick": 2,
            "sim_time_ns": 2_000_000_000,
        }
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="monotonically"
        ) as errors:
            await service.handle("step_stage", _stage_payload(first))
        assert errors.value.code == "request.invalid"

        # The pinned scenario digest cannot change mid-session.
        drifted = _stage_request(
            tick=1,
            sim_time_ns=1_000_000_000,
            scenario=ResolvedScenario.model_validate(
                build_resolved_scenario(
                    seed=SEED + 1,
                    providers=(
                        ScenarioProvider(PROVIDER_ID, ("mission",), CAPABILITIES, "business_environment"),
                        ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
                    ),
                    dynamic_provider_id="flight",
                    verifier_id="logistics.verifier",
                    world_id="world.city-demo",
                )
            ),
        )
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="scenario digest"
        ) as errors:
            await service.handle("step_stage", _stage_payload(drifted))
        assert errors.value.code == "identity.mismatch"

    _run(run())


def test_service_query_is_read_only_and_facilities_catalogue(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await service.handle("step_stage", _stage_payload(
            _stage_request(tick=1, sim_time_ns=1_000_000_000)
        ))
        before = service._machine.ledger.model_dump(mode="json")
        orders_response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.orders",
                        "kind": "orders",
                        "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                    },
                }
            ),
        )
        orders = orders_response["result"]["orders"]
        assert len(orders) == 1
        assert orders[0]["order_id"] == "order-1"
        assert orders[0]["status"] == "created"

        detail_response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.detail",
                        "kind": "order",
                        "order_id": "order-1",
                        "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                    },
                }
            ),
        )
        assert detail_response["result"]["orders"][0]["order_id"] == "order-1"

        facilities_response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.facilities",
                        "kind": "facilities",
                        "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                    },
                }
            ),
        )
        views = facilities_response["result"]["facilities"]
        assert [view["facility_id"] for view in views] == [
            "facility-1",
            "facility-2",
            "hub-3",
            "charger-x",
        ]
        hub = next(view for view in views if view["facility_id"] == "hub-3")
        assert hub["storage_capacity_kg"] == 100.0

        # Read-only: the ledger digests are untouched by any query.
        assert service._machine.ledger.model_dump(mode="json") == before

        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="unknown logistics order"
        ) as errors:
            await service.handle(
                "query",
                _authorized(
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "query": {
                            "run_id": RUN_ID,
                            "query_id": "query.missing",
                            "kind": "order",
                            "order_id": "order-missing",
                            "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                        },
                    }
                ),
            )
        assert errors.value.code == "query.rejected"

    _run(run())


def test_service_snapshot_is_deterministic(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        first = (await service.handle(
            "snapshot", _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID})
        ))["snapshot_digest"]
        second = (await service.handle(
            "snapshot", _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID})
        ))["snapshot_digest"]
        assert first == second
        assert first != "0" * 64

    _run(run())


def test_service_finalization_writes_declared_artifact_chain(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await service.handle("step_stage", _stage_payload(
            _stage_request(tick=1, sim_time_ns=1_000_000_000)
        ))
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "command.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        finalization = await service.handle(
            "finalize",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {
                            "tick": 1,
                            "sim_time_ns": 1_000_000_000,
                        },
                        "event_chain_root": "d" * 64,
                    },
                }
            ),
        )
        await service.handle(
            "shutdown", _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID})
        )
        artifact = tmp_path / "artifacts" / "logistics/state.json"
        content = artifact.read_bytes()
        document = json.loads(content)
        receipt = finalization["receipt"]
        assert receipt["event_chain_root"] == "d" * 64
        assert receipt["artifacts"] == [
            {
                "artifact_id": "artifact.logistics",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ]
        history = document["history"]
        assert len(history) == 1
        assert document["logistics_history_root"] == history[-1]["record_hash"]
        assert history[0]["previous_hash"] == "0" * 64
        assert document["ledger"]["version"] == 1
        assert document["ledger"]["orders"][0]["status"] == "offered"
        assert document["facilities"]["facilities"][0]["facility_id"] == "facility-1"
        assert document["principal_bindings"][0]["principal_id"] == "agent.provider"

    _run(run())


def test_service_finalization_is_cached_and_root_fixed(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        finalization = await service.handle(
            "finalize",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {"tick": 0, "sim_time_ns": 0},
                        "event_chain_root": "d" * 64,
                    },
                }
            ),
        )
        repeated = await service.handle(
            "finalize",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {"tick": 0, "sim_time_ns": 0},
                        "event_chain_root": "d" * 64,
                    },
                }
            ),
        )
        assert repeated == finalization
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="cannot be changed"
        ) as errors:
            await service.handle(
                "finalize",
                _authorized(
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "request": {
                            "schema_version": "aero-bench.provider-finalization-request/v1",
                            "run_id": RUN_ID,
                            "terminal_event": "run.completed",
                            "terminal_time": {"tick": 0, "sim_time_ns": 0},
                            "event_chain_root": "e" * 64,
                        },
                    }
                ),
            )
        assert errors.value.code == "not.ready"

    _run(run())


def test_service_requires_prepare_and_reset_ordering(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await service.handle(
                "reset",
                {"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED},
            )
        assert errors.value.code == "principal.denied"
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await service.handle(
                "reset",
                _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED}),
            )
        assert errors.value.code == "not.ready"
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await service.handle(
                "snapshot", _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID})
            )
        assert errors.value.code == "not.ready"
        await service.handle(
            "prepare", _prepare_payload(service._workload_identity.config_digest)
        )
        with pytest.raises(_SERVICE.LogisticsBusinessServiceError) as errors:
            await service.handle(
                "step_stage", _stage_payload(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            )
        assert errors.value.code == "not.ready"

    _run(run())


def test_logistics_state_writer_is_exact_and_atomic(tmp_path: Path) -> None:
    writer = _SERVICE.LogisticsStateWriter(tmp_path, _requirement())
    digest = writer.write(b'{"state":1}\n')
    assert writer.path == tmp_path / "logistics/state.json"
    assert writer.path.read_bytes() == b'{"state":1}\n'
    assert digest == hashlib.sha256(b'{"state":1}\n').hexdigest()
    writer.write(b'{"state":2}\n')
    assert writer.path.read_bytes() == b'{"state":2}\n'
    assert tuple(tmp_path.rglob("*")) == (tmp_path / "logistics", writer.path)


def test_logistics_state_writer_enforces_max_size(tmp_path: Path) -> None:
    requirement = dict(_requirement())
    requirement["max_size_bytes"] = 4
    writer = _SERVICE.LogisticsStateWriter(tmp_path, requirement)
    with pytest.raises(_SERVICE.LogisticsBusinessServiceError, match="max_size"):
        writer.write(b"12345")
    assert not writer.path.exists()


def test_logistics_state_writer_rejects_undeclared_files(tmp_path: Path) -> None:
    writer = _SERVICE.LogisticsStateWriter(tmp_path, _requirement())
    writer.write(b"{}")
    (tmp_path / "stray.json").write_bytes(b"{}")
    with pytest.raises(_SERVICE.LogisticsBusinessServiceError, match="undeclared"):
        writer.write(b"{}")


def test_logistics_state_writer_rejects_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "logistics").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "logistics" / "state.json").symlink_to(tmp_path / "outside")
    with pytest.raises(_SERVICE.LogisticsBusinessServiceError, match="symbolic link"):
        _SERVICE.LogisticsStateWriter(tmp_path, _requirement())


def test_service_rejects_unsafe_artifact_requirement(tmp_path: Path) -> None:
    for relative_path in (
        "/state.json",
        ".",
        "a/../state.json",
        "a\\state.json",
    ):
        requirement = dict(_requirement())
        requirement["relative_path"] = relative_path
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="normalized"
        ):
            _SERVICE.LogisticsStateWriter(tmp_path, requirement)


def test_service_serves_stable_classes_over_real_rpc(tmp_path: Path) -> None:
    """The real JSON-line wire carries token refusal and a full lifecycle.

    This is a focused unit round trip on a loopback socket with synthetic
    inputs; it is not a formal milestone run.
    """

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        transport = await JsonLineRpcTransport.connect(
            _Endpoint(host="127.0.0.1", port=port), component="test-harness"
        )
        try:
            probe = await transport.request("probe", {})
            assert probe["schema_version"] == PROVIDER_PROBE_SCHEMA
            assert probe["status"] == "accepting"
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "reset",
                    {"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED},
                )
            assert errors.value.code == "principal.denied"
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "reset",
                    _authorized(
                        {"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED}
                    ),
                )
            assert errors.value.code == "not.ready"
            await transport.request(
                "prepare", _prepare_payload(service._workload_identity.config_digest)
            )
            await transport.request(
                "reset",
                _authorized(
                    {"provider_id": PROVIDER_ID, "run_id": RUN_ID, "seed": SEED}
                ),
            )
            await transport.request(
                "step_stage", _stage_payload(_stage_request(tick=1, sim_time_ns=1_000_000_000))
            )
            response = await transport.request(
                "command",
                _authorized(
                    _command_payload(
                        "logistics.order.offer",
                        "command.offer",
                        1,
                        "logistics.dispatcher",
                        order_id="order-1",
                        assignee_id="fleet-alpha:1",
                        actor_id="logistics.dispatcher",
                        expected_version=0,
                    )
                ),
            )
            assert [item["phase"] for item in response["receipts"]] == [
                "received",
                "accepted",
                "applied",
                "completed",
            ]
            assert response["history_record"] == (
                service._machine.ledger.history[-1].model_dump(mode="json")
            )
            query = await transport.request(
                "query",
                _authorized(
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "query": {
                            "run_id": RUN_ID,
                            "query_id": "query.orders",
                            "kind": "orders",
                            "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                        },
                    }
                ),
            )
            assert query["result"]["orders"][0]["status"] == "offered"
            finalization = await transport.request(
                "finalize",
                _authorized(
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "request": {
                            "schema_version": "aero-bench.provider-finalization-request/v1",
                            "run_id": RUN_ID,
                            "terminal_event": "run.completed",
                            "terminal_time": {
                                "tick": 1,
                                "sim_time_ns": 1_000_000_000,
                            },
                            "event_chain_root": "d" * 64,
                        },
                    }
                ),
            )
            assert finalization["receipt"]["event_chain_root"] == "d" * 64
            assert finalization["receipt"]["artifacts"][0]["artifact_id"] == (
                "artifact.logistics"
            )
            await transport.request(
                "shutdown", _authorized({"provider_id": PROVIDER_ID, "run_id": RUN_ID})
            )
        finally:
            await transport.close()
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_service_binds_assignment_capacity_to_exact_aircraft_max_payload(
    tmp_path: Path,
) -> None:
    """Assign capacity is bounded by the exact assigned aircraft's canonical payload.

    ``capacity_kg`` may never exceed the canonical ``max_payload_kg`` of the
    AircraftUnit the dispatcher actually assigned, and it must still carry the
    cargo. An oversized capacity returns an ordered ``received, failed`` receipt
    and leaves the ledger and history untouched. No clipping or silent
    replacement to the canonical bound is ever performed.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "cap.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "cap.accept",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    actor_id="fleet-alpha:1",
                    expected_version=1,
                )
            ),
        )
        before = service._machine.ledger.model_dump(mode="json")
        canonical = {
            unit.aircraft_id: unit.max_payload_kg
            for unit in service._config.task_package.fleet.expand_aircraft_units()
        }
        assert canonical["fleet-alpha:1"] == 5.0

        # 10000 kg on a 5 kg aircraft is refused; history and ledger unchanged.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.assign",
                    "cap.assign.oversized",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    capacity_kg=10000,
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert set(response) == {"receipts"}
        detail = response["receipts"][-1]["detail"]
        assert "max_payload_kg" in detail
        assert "fleet-alpha:1" in detail
        assert service._machine.ledger.model_dump(mode="json") == before
        order = service._machine.ledger.orders[0]
        assert order.status.value == "accepted"
        assert order.capacity_kg is None

        # The exact canonical bound is accepted (a lower requested capacity
        # within the cargo bound is also fine, but exact max is the boundary).
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.assign",
                    "cap.assign.ok",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    capacity_kg=5,
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        order = service._machine.ledger.orders[0]
        assert order.status.value == "assigned"
        assert order.capacity_kg == 5.0

        # A positive capacity below the cargo mass is still refused by the
        # canonical domain (cargo cannot exceed the assigned capacity).
        service2 = await _prepared_service(tmp_path)
        await _advance(service2)
        await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "cap2.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "cap2.accept",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    actor_id="fleet-alpha:1",
                    expected_version=1,
                )
            ),
        )
        below_cargo = await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.assign",
                    "cap2.assign.below",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    capacity_kg=0.5,
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in below_cargo["receipts"]] == [
            "received",
            "failed",
        ]
        assert service2._machine.ledger.model_dump(mode="json")["version"] == 2

    _run(run())


def test_service_low_payload_aircraft_not_rescued_by_other_aircraft(
    tmp_path: Path,
) -> None:
    """An oversized assign to a low-payload aircraft is refused even if another
    declared aircraft could lift the cargo.

    The bound must be the EXACT assigned AircraftUnit, never whichever aircraft
    has sufficient payload somewhere else in the package.
    """

    async def run() -> None:
        service = await _prepared_service_with_config(
            tmp_path, _mixed_capacity_config_document()
        )
        await _advance(service)
        canonical = {
            unit.aircraft_id: unit.max_payload_kg
            for unit in service._config.task_package.fleet.expand_aircraft_units()
        }
        assert canonical == {"fleet-alpha:1": 2.0, "fleet-bravo:1": 50.0}

        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "mix.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "mix.accept",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    actor_id="fleet-alpha:1",
                    expected_version=1,
                )
            ),
        )
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.assign",
                    "mix.assign.low",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    capacity_kg=10,
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "fleet-alpha:1" in response["receipts"][-1]["detail"]
        assert "2.0" in response["receipts"][-1]["detail"]
        assert "fleet-bravo:1" not in response["receipts"][-1]["detail"]
        order = service._machine.ledger.orders[0]
        assert order.status.value == "accepted"
        assert order.capacity_kg is None

        # The same order offered to the high-payload aircraft assigns cleanly,
        # proving the package really has another aircraft that can carry it.
        service2 = await _prepared_service_with_config(
            tmp_path / "bravo", _mixed_capacity_config_document()
        )
        await _advance(service2)
        await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "mix2.offer",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-bravo:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "mix2.accept",
                    1,
                    "agent.bravo",
                    order_id="order-1",
                    actor_id="fleet-bravo:1",
                    expected_version=1,
                )
            ),
        )
        high_capacity = await service2.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.assign",
                    "mix2.assign.high",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-bravo:1",
                    actor_id="logistics.dispatcher",
                    capacity_kg=10,
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in high_capacity["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]

    _run(run())


def test_service_assignee_must_be_a_declared_aircraft_unit(tmp_path: Path) -> None:
    """Offer/assign cannot name a non-existent aircraft unit."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        before = service._machine.ledger.model_dump(mode="json")
        for command_id, tool_id, arguments in (
            (
                "ghost.offer",
                "logistics.order.offer",
                {
                    "order_id": "order-1",
                    "assignee_id": "ghost-fleet:1",
                    "actor_id": "logistics.dispatcher",
                    "expected_version": 0,
                },
            ),
            (
                "ghost.assign",
                "logistics.order.assign",
                {
                    "order_id": "order-1",
                    "assignee_id": "ghost-fleet:1",
                    "actor_id": "logistics.dispatcher",
                    "capacity_kg": 3,
                    "expected_version": 0,
                },
            ),
        ):
            response = await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        tool_id,
                        command_id,
                        1,
                        "logistics.dispatcher",
                        **arguments,
                    )
                ),
            )
            assert [item["phase"] for item in response["receipts"]] == [
                "received",
                "failed",
            ]
            assert "not a declared aircraft unit" in response["receipts"][-1][
                "detail"
            ]
        assert service._machine.ledger.model_dump(mode="json") == before

    _run(run())


def test_service_malformed_token_fails_closed_over_real_wire_and_recovers(
    tmp_path: Path,
) -> None:
    """Non-ASCII / list / numeric session tokens get a stable principal.denied
    error frame over the real JSON-line stream, and the same connection then
    serves a healthy valid request (no torn socket, no state binding)."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        transport = await JsonLineRpcTransport.connect(
            _Endpoint(host="127.0.0.1", port=port), component="test-harness"
        )
        try:
            query = {
                "provider_id": PROVIDER_ID,
                "run_id": RUN_ID,
                "query": {
                    "run_id": RUN_ID,
                    "query_id": "query.token-edge",
                    "kind": "orders",
                    "issued_at": {"tick": 0, "sim_time_ns": 0},
                },
            }
            for bad_token in ("é" * 64, [SESSION_TOKEN], 123, None, True):
                with pytest.raises(ProviderRemoteError) as errors:
                    await transport.request("query", {**query, "session_token": bad_token})
                assert errors.value.code == "principal.denied", repr(bad_token)
            assert service._machine.ledger.model_dump(mode="json")["version"] == 0

            # The same connection stays healthy: a valid request is served.
            healthy = await transport.request("query", _authorized(query))
            assert healthy["result"]["orders"][0]["order_id"] == "order-1"
        finally:
            await transport.close()
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_service_wrong_token_prepare_cannot_bind_state(tmp_path: Path) -> None:
    """A wrong/malformed session token on prepare leaves the provider unbound,
    and the legitimate prepare afterwards still succeeds on the same wire."""

    async def run() -> None:
        service = _make_service(tmp_path)
        server = JsonLineRpcServer(service.serve_rpc)
        handle = await server.start(host="127.0.0.1", port=0)
        port = handle.sockets[0].getsockname()[1]
        transport = await JsonLineRpcTransport.connect(
            _Endpoint(host="127.0.0.1", port=port), component="test-harness"
        )
        try:
            payload = _prepare_payload(service._workload_identity.config_digest)
            for bad_token in ("é" * 64, "8" * 64, SESSION_TOKEN[:-1] + "e"):
                with pytest.raises(ProviderRemoteError) as errors:
                    await transport.request(
                        "prepare", {**payload, "session_token": bad_token}
                    )
                assert errors.value.code == "principal.denied"
                assert service._config is None
            assert service._config is None
            prepared = await transport.request("prepare", payload)
            assert prepared["status"] == "ready"
            assert service._config is not None

            # A wrong token on a stateful frame after binding still denies.
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "reset",
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "seed": SEED,
                        "session_token": "é" * 64,
                    },
                )
            assert errors.value.code == "principal.denied"
        finally:
            await transport.close()
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_service_commands_bind_exact_current_simulation_time(tmp_path: Path) -> None:
    """Commands must be issued at the exact current harness SimulationTime.

    Stale (older tick), future, and mismatched tick/ns frames are all refused
    before any ledger mutation; only the exact current barrier applies.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance_to(service, to_tick=5)
        assert service._current == (5, 5_000_000_000)
        before = service._machine.ledger.model_dump(mode="json")

        def timed_payload(
            command_id: str, tick: int, sim_time_ns: int
        ) -> dict[str, object]:
            return _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": {
                        "run_id": RUN_ID,
                        "command_id": command_id,
                        "agent_id": "logistics.dispatcher",
                        "tool_id": "logistics.order.offer",
                        "issued_at": {"tick": tick, "sim_time_ns": sim_time_ns},
                        "arguments": [
                            {"name": "order_id", "value": "order-1"},
                            {"name": "assignee_id", "value": "fleet-alpha:1"},
                            {"name": "actor_id", "value": "logistics.dispatcher"},
                            {"name": "expected_version", "value": 0},
                        ],
                    },
                }
            )

        # Stale: tick 0 while the barrier is at tick 5.
        stale = await service.handle(
            "command", timed_payload("time.stale", 0, 0)
        )
        assert [item["phase"] for item in stale["receipts"]] == ["received", "failed"]
        assert "current provider barrier" in stale["receipts"][-1]["detail"]

        # Future: tick 6 beyond the barrier.
        future = await service.handle(
            "command", timed_payload("time.future", 6, 6_000_000_000)
        )
        assert [item["phase"] for item in future["receipts"]] == [
            "received",
            "failed",
        ]

        # Mismatched tick/ns at the same tick: (5, 4e9) is not the barrier.
        mismatched = await service.handle(
            "command", timed_payload("time.mismatch", 5, 4_000_000_000)
        )
        assert [item["phase"] for item in mismatched["receipts"]] == [
            "received",
            "failed",
        ]
        assert service._machine.ledger.model_dump(mode="json") == before

        # Exact current barrier applies normally.
        exact = await service.handle(
            "command", timed_payload("time.exact", 5, 5_000_000_000)
        )
        assert [item["phase"] for item in exact["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        order = service._machine.ledger.orders[0]
        assert order.status.value == "offered"
        assert order.last_time_s == 5.0

    _run(run())


def test_service_central_principal_controls_two_declared_aircraft_actors(
    tmp_path: Path,
) -> None:
    """A single native principal explicitly bound to two aircraft actors can act
    as either; an unbound actor selection is denied; an aircraft actor cannot
    dispatch; and pair lookup has no ambiguous first match."""

    async def run() -> None:
        service = await _prepared_service_with_config(
            tmp_path, _central_agent_config_document()
        )
        await _advance(service)

        # dispatcher offers order-1 to fleet-alpha:1 and order-2 to fleet-alpha:2.
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "central.offer.1",
                    1,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "central.offer.2",
                    1,
                    "logistics.dispatcher",
                    order_id="order-2",
                    assignee_id="fleet-alpha:2",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )

        # The central principal accepts order-1 as fleet-alpha:1 and order-2 as
        # fleet-alpha:2. Selecting the second actor proves the exact-pair lookup
        # (first-match-on-principal would pick fleet-alpha:1 and fail the
        # assigned-aircraft gate on order-2).
        accept1 = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "central.accept.1",
                    1,
                    "central.fleet",
                    order_id="order-1",
                    actor_id="fleet-alpha:1",
                    expected_version=1,
                )
            ),
        )
        assert [item["phase"] for item in accept1["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        accept2 = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "central.accept.2",
                    1,
                    "central.fleet",
                    order_id="order-2",
                    actor_id="fleet-alpha:2",
                    expected_version=1,
                )
            ),
        )
        assert [item["phase"] for item in accept2["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        assert accept2["history_record"]["transition"]["actor_id"] == "fleet-alpha:2"
        assert accept2["history_record"]["transition"]["actor_role"] == "aircraft_agent"

        # Unauthorized actor selection: central.fleet is not bound to the
        # dispatcher actor, so selecting it is denied before any role is used.
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="is not bound to logistics"
        ) as errors:
            await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        "logistics.order.cancel",
                        "central.evil",
                        1,
                        "central.fleet",
                        order_id="order-1",
                        reason="why-not",
                        actor_id="logistics.dispatcher",
                        expected_version=2,
                    )
                ),
            )
        assert errors.value.code == "principal.denied"

        # An aircraft actor cannot dispatch: selecting fleet-alpha:1 (an
        # aircraft_agent) for an offer still yields an ordered failure because
        # the derived canonical role is aircraft_agent.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "central.dispatch.forge",
                    1,
                    "central.fleet",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="fleet-alpha:1",
                    expected_version=2,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "offer cannot be issued by aircraft_agent" in response["receipts"][
            -1
        ]["detail"]

        # The two central flows were both applied to the authoritative ledger.
        orders = {
            order.order_id: order for order in service._machine.ledger.orders
        }
        assert orders["order-1"].status.value == "accepted"
        assert orders["order-2"].status.value == "accepted"
        assert orders["order-1"].assignee_id == "fleet-alpha:1"
        assert orders["order-2"].assignee_id == "fleet-alpha:2"

    _run(run())


def test_service_reset_barrier_accepts_exact_zero_time_command(
    tmp_path: Path,
) -> None:
    """The empty initial reset barrier (0, 0) is a real time position.

    A command issued at exactly the reset time is accepted; there is no
    compatibility window where stale or future times are tolerated.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        assert service._current == (0, 0)
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "zero.offer",
                    0,
                    "logistics.dispatcher",
                    order_id="order-1",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        order = service._machine.ledger.orders[0]
        assert order.status.value == "offered"
        assert order.last_time_s == 0.0

        # A future tick on the reset barrier is refused without mutation.
        before = service._machine.ledger.model_dump(mode="json")
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.accept",
                    "zero.future",
                    1,
                    "agent.provider",
                    order_id="order-1",
                    actor_id="fleet-alpha:1",
                    expected_version=1,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "current provider barrier" in response["receipts"][-1]["detail"]
        assert service._machine.ledger.model_dump(mode="json") == before

    _run(run())


def test_service_ignores_transition_id_evidence_reference(tmp_path: Path) -> None:
    """OrderTransition.transition_id is never usable as external evidence."""

    from aero_bench.tasks.logistics.orders import OrderTransition

    with pytest.raises(ValueError, match="evidence"):
        OrderTransition.model_validate(
            {
                "order_id": "order-1",
                "transition_id": "command.pickup",
                "event": "pick_up",
                "actor_id": "fleet-alpha:1",
                "actor_role": "aircraft_agent",
                "time_s": 1.0,
                "expected_version": 0,
                "assignee_id": "fleet-alpha:1",
                "evidence_ref": "command.pickup",
            }
        )


def _online_create_arguments(
    *, order_id: str, release_time_s: float = 1, command_args: bool = True
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "order_id": order_id,
        "origin_facility_id": "facility-1",
        "destination_facility_id": "facility-2",
        "hub_handoff_facility_id": "hub-3",
        "cargo_mass_kg": 1,
        "release_time_s": release_time_s,
        "deadline_s": 900,
        "actor_id": "logistics.business",
    }
    if not command_args:
        arguments.pop("cargo_mass_kg", None)
    return arguments


def test_service_online_order_create_is_authenticated_and_available(
    tmp_path: Path,
) -> None:
    """The business actor can create an online order bound to its principal.

    The arrival journal records one created event (source = the authenticated
    principal) and, because the declared release gate equals the current native
    time, the exact one-time release; the new order is immediately available.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        before_events = service._arrival.snapshot.events
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.create-online",
                    1,
                    "logistics.business",
                    **_online_create_arguments(order_id="order-online"),
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        arrival_record = response["arrival_record"]
        assert arrival_record["kind"] == "created"
        assert arrival_record["event_id"] == "command.create-online"
        assert arrival_record["order_id"] == "order-online"
        assert arrival_record["time"] == {"tick": 1, "sim_time_ns": 1_000_000_000}
        assert arrival_record["source_identity"] == "logistics.business"
        assert arrival_record["actor_id"] == "logistics.business"
        assert arrival_record["order"]["order_id"] == "order-online"
        assert arrival_record["order"]["release_time_s"] == 1.0

        # The journal grows by exactly two bound records: create + one-time
        # release; the release hash-chains onto the creation record.
        events = service._arrival.snapshot.events
        assert len(events) == len(before_events) + 2
        created = events[-2]
        released = events[-1]
        assert created.kind == "created"
        assert created.event_id == "command.create-online"
        assert released.kind == "released"
        assert released.order_id == "order-online"
        assert released.previous_hash == created.record_hash

        # Both orders are available now; the online order was gated at 1 s.
        response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.after-create",
                        "kind": "orders",
                        "issued_at": {"tick": 1, "sim_time_ns": 1_000_000_000},
                    },
                }
            ),
        )
        assert [item["order_id"] for item in response["result"]["orders"]] == [
            "order-1",
            "order-online",
        ]
        assert service._arrival.snapshot.canonical_digest() != "0" * 64

    _run(run())


def test_service_online_create_principal_forgery_is_denied(tmp_path: Path) -> None:
    """A native principal can never claim another principal's actor binding."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        forged_arguments = {
            **_online_create_arguments(order_id="order-forged"),
            "actor_id": "logistics.dispatcher",
        }
        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="not bound"
        ) as errors:
            await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        "logistics.order.create",
                        "command.forge-principal",
                        1,
                        "logistics.business",
                        **forged_arguments,
                    )
                ),
            )
        assert errors.value.code == "principal.denied"
        # The journal is untouched: only the baseline create+release remain.
        assert len(service._arrival.snapshot.events) == 2

    _run(run())


def test_service_online_create_role_forgery_is_atomically_rejected(
    tmp_path: Path,
) -> None:
    """Only the canonical business grant may introduce an online order."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        before = service._arrival.snapshot.canonical_digest()
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.forge-role",
                    1,
                    "logistics.dispatcher",
                    order_id="order-forged",
                    origin_facility_id="facility-1",
                    destination_facility_id="facility-2",
                    hub_handoff_facility_id="hub-3",
                    cargo_mass_kg=1,
                    release_time_s=1,
                    deadline_s=900,
                    actor_id="logistics.dispatcher",
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert set(response) == {"receipts"}
        assert (
            "not authorized to introduce orders"
            in response["receipts"][-1]["detail"]
        )
        assert service._arrival.snapshot.canonical_digest() == before

    _run(run())


def test_service_online_create_duplicate_and_malformed_are_atomic(
    tmp_path: Path,
) -> None:
    """Duplicate order ids, replayed command ids, and malformed input fail closed."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        first = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.create-online",
                    1,
                    "logistics.business",
                    **_online_create_arguments(order_id="order-online"),
                )
            ),
        )
        assert first["receipts"][-1]["phase"] == "completed"
        before_events = service._arrival.snapshot.events

        # A fresh command id naming the same order is rejected without mutation.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.duplicate-order",
                    1,
                    "logistics.business",
                    **_online_create_arguments(order_id="order-online"),
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "already registered" in response["receipts"][-1]["detail"]
        assert set(response) == {"receipts"}
        assert service._arrival.snapshot.events == before_events

        # A replayed command id naming a fresh order is rejected without mutation.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.create-online",
                    1,
                    "logistics.business",
                    **_online_create_arguments(order_id="order-replay"),
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "already processed" in response["receipts"][-1]["detail"]
        assert service._arrival.snapshot.events == before_events

        # A malformed create payload is rejected before any journal mutation.
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.malformed",
                    1,
                    "logistics.business",
                    **_online_create_arguments(
                        order_id="order-malformed", command_args=False
                    ),
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "create requires exactly" in response["receipts"][-1]["detail"]
        assert set(response) == {"receipts"}
        assert service._arrival.snapshot.events == before_events

    _run(run())


def test_service_predeclared_future_order_is_hidden_until_release(
    tmp_path: Path,
) -> None:
    """A predeclared future release is registered but hidden until its gate."""

    async def run() -> None:
        config_document = _future_order_config_document()
        service = await _prepared_service_with_config(tmp_path, config_document)
        assert service._current == (0, 0)
        response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.orders",
                        "kind": "orders",
                        "issued_at": {"tick": 0, "sim_time_ns": 0},
                    },
                }
            ),
        )
        assert [item["order_id"] for item in response["result"]["orders"]] == [
            "order-t0"
        ]

        with pytest.raises(
            _SERVICE.LogisticsBusinessServiceError, match="not yet released"
        ) as errors:
            await service.handle(
                "query",
                _authorized(
                    {
                        "provider_id": PROVIDER_ID,
                        "run_id": RUN_ID,
                        "query": {
                            "run_id": RUN_ID,
                            "query_id": "query.detail",
                            "kind": "order",
                            "order_id": "order-future",
                            "issued_at": {"tick": 0, "sim_time_ns": 0},
                        },
                    }
                ),
            )
        assert errors.value.code == "query.rejected"

        before = service._machine.ledger.model_dump(mode="json")
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "future.offer",
                    0,
                    "logistics.dispatcher",
                    order_id="order-future",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert "not yet available" in response["receipts"][-1]["detail"]
        assert service._machine.ledger.model_dump(mode="json") == before

        # Advancing across the release boundary makes the order available once.
        await _advance_to(service, to_tick=3)
        response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.released",
                        "kind": "orders",
                        "issued_at": {"tick": 3, "sim_time_ns": 3_000_000_000},
                    },
                }
            ),
        )
        assert [item["order_id"] for item in response["result"]["orders"]] == [
            "order-t0",
            "order-future",
        ]
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.offer",
                    "future.released.offer",
                    3,
                    "logistics.dispatcher",
                    order_id="order-future",
                    assignee_id="fleet-alpha:1",
                    actor_id="logistics.dispatcher",
                    expected_version=0,
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        assert service._machine.ledger.orders[1].status.value == "offered"

    _run(run())


def test_service_online_future_release_is_hidden_until_gate(tmp_path: Path) -> None:
    """An online order with a future gate is hidden and released exactly once."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        previous_scene_state = None
        for tick in range(1, 3):
            request = _stage_request(
                tick=tick,
                sim_time_ns=tick * 1_000_000_000,
                previous_scene_state=previous_scene_state,
            )
            await service.handle("step_stage", _stage_payload(request))
            previous_scene_state = request.scene_state
        assert service._current == (2, 2_000_000_000)
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.create-future",
                    2,
                    "logistics.business",
                    **_online_create_arguments(
                        order_id="order-online-future", release_time_s=5
                    ),
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "accepted",
            "applied",
            "completed",
        ]
        # The future release is registered but hidden at tick 2.
        response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.hidden",
                        "kind": "orders",
                        "issued_at": {"tick": 2, "sim_time_ns": 2_000_000_000},
                    },
                }
            ),
        )
        assert [item["order_id"] for item in response["result"]["orders"]] == [
            "order-1"
        ]
        release_events = tuple(
            event
            for event in service._arrival.snapshot.events
            if event.kind == "released"
        )
        assert [event.order_id for event in release_events] == ["order-1"]

        # Cross the 5 s gate through chained authoritative barriers.
        for tick in range(3, 6):
            request = _stage_request(
                tick=tick,
                sim_time_ns=tick * 1_000_000_000,
                previous_scene_state=previous_scene_state,
            )
            await service.handle("step_stage", _stage_payload(request))
            previous_scene_state = request.scene_state
        assert service._current == (5, 5_000_000_000)
        response = await service.handle(
            "query",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.released",
                        "kind": "orders",
                        "issued_at": {"tick": 5, "sim_time_ns": 5_000_000_000},
                    },
                }
            ),
        )
        assert [item["order_id"] for item in response["result"]["orders"]] == [
            "order-1",
            "order-online-future",
        ]
        release_events = tuple(
            event
            for event in service._arrival.snapshot.events
            if event.kind == "released"
        )
        assert [event.order_id for event in release_events] == [
            "order-1",
            "order-online-future",
        ]

    _run(run())


def test_service_artifact_seals_arrival_journal_snapshot_and_baseline(
    tmp_path: Path,
) -> None:
    """The finalized artifact binds the full journal, snapshot, and baseline."""

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await _advance(service)
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "logistics.order.create",
                    "command.create-sealed",
                    1,
                    "logistics.business",
                    **_online_create_arguments(order_id="order-sealed"),
                )
            ),
        )
        await service.handle(
            "finalize",
            _authorized(
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {"tick": 1, "sim_time_ns": 1_000_000_000},
                        "event_chain_root": "d" * 64,
                    },
                }
            ),
        )
        artifact = tmp_path / "artifacts" / "logistics/state.json"
        document = json.loads(artifact.read_text(encoding="utf-8"))
        assert document["initial_requests"] == [
            request.model_dump(mode="json")
            for request in service._initial_requests
        ]
        assert document["initial_time"] == {"tick": 0, "sim_time_ns": 0}
        assert document["arrival_snapshot_digest"] == (
            service._arrival.snapshot.canonical_digest()
        )
        expected_created = ("order-1", "order-sealed")
        # The full append-only journal survives and replays to the same snapshot.
        replayed = replay_order_arrivals(
            catalogue=service._arrival.catalogue,
            fleet=service._arrival.fleet,
            actor_grants=service._arrival.actor_grants,
            initial_requests=tuple(
                OrderRequest.model_validate(item)
                for item in document["initial_requests"]
            ),
            initial_time=SimulationTime.model_validate(document["initial_time"]),
            events=tuple(
                OrderArrivalEvent.model_validate(item)
                for item in document["arrival"]
            ),
            current_time=SimulationTime(tick=1, sim_time_ns=1_000_000_000),
        )
        assert replayed.canonical_digest() == document["arrival_snapshot_digest"]
        assert replayed.model_dump(mode="json") == document["arrival_snapshot"]
        assert tuple(
            event.order.order_id
            for event in replayed.events
            if event.kind == "created"
        ) == expected_created
        # The sealed canonical snapshot carries the live time and journal length.
        assert replayed.time == SimulationTime(tick=1, sim_time_ns=1_000_000_000)
        assert replayed.version == len(replayed.events)

    _run(run())


__all__ = [
    "CAPABILITIES",
    "IMAGE",
    "PROVIDER_ID",
    "PROTOCOL_VERSION",
    "PROVIDER_PROBE_SCHEMA",
    "RUN_ID",
    "SEED",
    "SESSION_TOKEN",
    "_Endpoint",
    "_SERVICE",
    "_authorized",
    "_central_agent_config_document",
    "_command_payload",
    "_config_document",
    "_contract",
    "_future_order_config_document",
    "_identity",
    "_logistics_package_document",
    "_make_service",
    "_make_service_with_config",
    "_mixed_capacity_config_document",
    "_mixed_capacity_package_document",
    "_prepare_payload",
    "_prepare_payload_for",
    "_prepared_service",
    "_prepared_service_with_config",
    "_requirement",
    "_run",
    "_stage_payload",
    "_stage_request",
    "_two_aircraft_package_document",
    "_write_bundle",
    "_write_config",
]
