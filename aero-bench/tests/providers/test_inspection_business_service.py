"""Module tests for the container service of the Inspection Business provider.

The service module is loaded from ``containers/inspection-business/service.py``
so the tests exercise the exact bytes shipped into the digest-pinned image.
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
from aero_bench.providers.inspection_business.protocol import (
    PROTOCOL_VERSION as INSPECTION_BUSINESS_PROTOCOL_VERSION,
)
from aero_bench.runtime.contracts import SCENE_STATE_ROOT_DIGEST
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.resolved import ResolvedScenario, scenario_assets_for_workload
from containers.selfcheck_scenario import ScenarioProvider, build_resolved_scenario
from tests.test_inspection_business import PAYLOAD_DIGEST, _package


_SERVICE_PATH = (
    Path(__file__).parents[2] / "containers" / "inspection-business" / "service.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_inspection_business_service", _SERVICE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_SERVICE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SERVICE
_SPEC.loader.exec_module(_SERVICE)

# The selfcheck owns the canonical typed business_environment stage request
# builder; it imports the service as ``service``.
_SELFCHECK_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_inspection_business_selfcheck", _SERVICE_PATH.with_name("selfcheck.py")
)
assert _SELFCHECK_SPEC is not None and _SELFCHECK_SPEC.loader is not None
_SELFCHECK = importlib.util.module_from_spec(_SELFCHECK_SPEC)
_previous_service = sys.modules.get("service")
sys.modules["service"] = _SERVICE
try:
    _SELFCHECK_SPEC.loader.exec_module(_SELFCHECK)
finally:
    if _previous_service is None:
        sys.modules.pop("service", None)
    else:
        sys.modules["service"] = _previous_service


RUN_ID = "a" * 64
IMAGE = "registry.invalid/inspection-business@sha256:" + "1" * 64
FILE_DIGEST = "c" * 64
SERVICE_PORT = 17435
PROVIDER_ADAPTER = "inspection.business"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
SESSION_TOKEN = "9" * 64


@dataclass(frozen=True, slots=True)
class _Endpoint:
    host: str
    port: int


def _file_ref(path: str) -> dict[str, str]:
    return {"path": path, "sha256": FILE_DIGEST}


def _requirement() -> dict[str, object]:
    return {
        "artifact_id": "artifact.business",
        "artifact_type": "business.state",
        "producer_id": "business",
        "visibility": "private",
        "relative_path": "business/state.json",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }


def _config_document() -> dict[str, object]:
    return {
        "schema_version": "aero-bench.inspection-business/v1",
        "provider_id": "business",
        "task_package": _package().model_dump(mode="json"),
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


def _write_bundle(root: Path) -> dict[str, dict[str, str]]:
    return _write_config(root, canonical=True)


def _contract_scenario() -> ResolvedScenario:
    return ResolvedScenario.model_validate(
        build_resolved_scenario(
            seed=1701,
            providers=(
                ScenarioProvider(
                    "business",
                    ("mission",),
                    ("business.work-order",),
                    "business_environment",
                ),
                ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
            ),
            dynamic_provider_id="flight",
        )
    )


def _contract(refs: dict[str, dict[str, str]], port: int) -> ProviderWorkloadContract:
    scenario = _contract_scenario()
    return ProviderWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="provider",
        run_id=RUN_ID,
        seed=1701,
        workload_id="business",
        clock={
            "authority": "provider_barrier",
            "step_ns": 1_000_000_000,
            "max_steps": 2,
            "provider_timeout_ms": 10_000,
        },
        provider={
            "provider_id": "business",
            "adapter": "inspection.business",
            "port": port,
            "workload": {
                "runtime": {"image": IMAGE, "command": ["provider", "serve"]},
                "resources": {
                    "cpu_millicores": 1000,
                    "memory_mib": 1024,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": "inspection.business",
                    "kind": "mechanical_fixture",
                    "source_uri": "https://github.com/moby/moby",
                    "source_revision": "4aeab8310c1c8bf6c2c7b255ae1abc2c767bb556",
                    "version": "test-fixture-1",
                },
            },
            "config": {"file": refs["config"], "schema_file": refs["schema"]},
            "protocol_schema": refs["schema"],
            "capabilities": ["business.work-order"],
            "artifact_requirements": [_requirement()],
        },
        scenario_digest=scenario.scenario_digest,
        scenario=scenario,
        scenario_assets=scenario_assets_for_workload(
            scenario,
            role="provider",
            workload_id="business",
        ),
    )


def _identity(root: Path, *, port: int = SERVICE_PORT) -> _SERVICE.WorkloadIdentity:
    refs = _write_bundle(root)
    return _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id="business",
        provider_port=port,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=1701,
        contract=_contract(refs, port),
    )


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    """The Harness session presents its granted token on every frame."""

    return {"session_token": SESSION_TOKEN, **payload}


def _prepare_payload(config_digest: str) -> dict[str, object]:
    package = _package().model_dump(mode="json")
    return {
        "provider_id": "business",
        "run_id": RUN_ID,
        "protocol_version": INSPECTION_BUSINESS_PROTOCOL_VERSION,
        "runtime_image": IMAGE,
        "config_digest": config_digest,
        "artifact_requirements": [_requirement()],
        "session_token": SESSION_TOKEN,
        "task_id": "inspection.task",
        "package_digest": hashlib.sha256(canonical_json_bytes(package)).hexdigest(),
        "task_package": package,
    }


def _command_payload(
    tool_id: str,
    command_id: str,
    tick: int,
    agent_id: str,
    **arguments: object,
) -> dict[str, object]:
    return {
        "provider_id": "business",
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
    (tmp_path / "artifacts").mkdir()
    return _SERVICE.InspectionBusinessProviderService(
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
        _authorized({"provider_id": "business", "run_id": RUN_ID, "seed": 1701}),
    )
    return service


def _run(coro) -> None:
    asyncio.run(coro)


def _step_stage_payload(
    tick: int,
    previous_scene_state_digest: str = SCENE_STATE_ROOT_DIGEST,
) -> dict[str, object]:
    """Authorized typed business_environment stage frame for one tick."""

    assert _SELFCHECK.RUN_ID == RUN_ID
    scenario_digest = _contract_scenario().scenario_digest
    request = _SELFCHECK._stage_request(
        tick, scenario_digest, previous_scene_state_digest
    )
    return _authorized(
        {
            "provider_id": "business",
            "run_id": RUN_ID,
            "protocol_version": INSPECTION_BUSINESS_PROTOCOL_VERSION,
            "request": request.model_dump(mode="json"),
        }
    )


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
        error = _SERVICE.InspectionBusinessServiceError(code, "detail")
        assert error.code == code
        assert error.detail == "detail"


def test_service_rejects_non_canonical_contract_bytes(tmp_path: Path) -> None:
    refs = _write_bundle(tmp_path)
    document = _contract(refs, SERVICE_PORT).model_dump(mode="json")
    contract_path = tmp_path / "contract.json"
    contract_path.write_bytes(json.dumps(document).encode("utf-8"))
    with pytest.raises(_SERVICE.InspectionBusinessServiceError, match="canonical"):
        _SERVICE._load_canonical_model_bytes(
            contract_path, ProviderWorkloadContract, label="AERO_BENCH_CONTRACT"
        )
    contract_path.write_bytes(canonical_json_bytes(document) + b"\n")
    loaded = _SERVICE._load_canonical_model_bytes(
        contract_path, ProviderWorkloadContract, label="AERO_BENCH_CONTRACT"
    )
    assert loaded == ProviderWorkloadContract.model_validate(document)


def test_service_rejects_non_canonical_provider_config_bytes(tmp_path: Path) -> None:
    refs = _write_config(tmp_path, canonical=False)
    identity = _SERVICE.WorkloadIdentity(
        run_id=RUN_ID,
        provider_id="business",
        provider_port=SERVICE_PORT,
        runtime_image=IMAGE,
        config_digest=refs["config"]["sha256"],
        artifact_requirement=_requirement(),
        seed=1701,
        contract=_contract(refs, SERVICE_PORT),
    )
    (tmp_path / "artifacts").mkdir()
    service = _SERVICE.InspectionBusinessProviderService(
        bundle_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        rpc_port=SERVICE_PORT,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )
    with pytest.raises(
        _SERVICE.InspectionBusinessServiceError, match="not canonical"
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
    assert response["provider_id"] == "business"
    assert response["run_id"] == RUN_ID
    assert response["adapter"] == PROVIDER_ADAPTER
    assert response["runtime_image"] == IMAGE
    assert response["config_digest"] == service._workload_identity.config_digest
    # The probe is stateless: it binds no configuration and mutates nothing.
    assert service._config is None
    assert service._machine is None


def test_service_probe_rejects_non_empty_payload(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    with pytest.raises(
        _SERVICE.InspectionBusinessServiceError, match="must be empty"
    ) as errors:
        asyncio.run(service.handle("probe", {"session_token": SESSION_TOKEN}))
    assert errors.value.code == "request.invalid"


def test_service_denies_wrong_token_before_identity_on_every_operation(
    tmp_path: Path,
) -> None:
    """Counterexample: authentication precedes every identity check.

    A peer that cannot present the executor-issued token is principal.denied
    even when it also replays wrong provider/run identity, on every stateful
    operation, and never mutates service state.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        before = service._machine.state.model_dump(mode="json")
        denied = {
            "provider_id": "other.provider",
            "run_id": "b" * 64,
            "session_token": "8" * 64,
        }
        operations = (
            "reset",
            "step_stage",
            "command",
            "query",
            "snapshot",
            "shutdown",
        )
        for operation in operations:
            with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
                await service.handle(operation, denied)
            assert errors.value.code == "principal.denied", operation
        # The principal.denied ordering holds for a missing token too.
        unauthenticated = {
            "provider_id": "other.provider",
            "run_id": "b" * 64,
        }
        for operation in operations:
            with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
                await service.handle(operation, unauthenticated)
            assert errors.value.code == "principal.denied", operation
        assert service._machine.state.model_dump(mode="json") == before
        assert service._current == (0, 0)

    _run(run())


def test_service_denies_wrong_token_prepare_before_identity_drift(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        digest = service._workload_identity.config_digest
        payload = _prepare_payload(digest)
        # Wrong token plus drifted identity: the token check wins and the
        # service learns and binds nothing.
        for drift in (
            {"provider_id": "other.provider"},
            {"run_id": "b" * 64},
            {"task_id": "another.task"},
        ):
            with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
                await service.handle(
                    "prepare",
                    {**payload, "session_token": "8" * 64, **drift},
                )
            assert errors.value.code == "principal.denied"
        # A malformed frame without a token is principal.denied, not
        # request.invalid: authentication runs before the shape check.
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle("prepare", {"unrelated": True})
        assert errors.value.code == "principal.denied"
        assert service._config is None

    _run(run())


def test_service_denies_racing_prepare_with_wrong_executor_token(
    tmp_path: Path,
) -> None:
    """Counterexample: a peer that races the first prepare binds nothing.

    Before this phase the first prepare was unauthenticated: whatever token a
    client presented first became the session token. The service now pins the
    executor-issued token at startup, so a racing client with a wrong token is
    denied in constant time and the service stays unprepared for the
    legitimate Harness session.
    """

    async def run() -> None:
        service = _make_service(tmp_path)
        digest = service._workload_identity.config_digest
        payload = _prepare_payload(digest)
        for token in ("8" * 64, "a" * 64, SESSION_TOKEN[:-1] + "e"):
            with pytest.raises(
                _SERVICE.InspectionBusinessServiceError, match="session token"
            ) as errors:
                await service.handle("prepare", {**payload, "session_token": token})
            assert errors.value.code == "principal.denied"
        assert service._config is None
        # Every denied racing attempt bound no state: the legitimate session
        # still finds an unprepared service and prepares it with the matching
        # executor-issued token.
        prepared = await service.handle("prepare", payload)
        assert prepared["status"] == "ready"
        with pytest.raises(
            _SERVICE.InspectionBusinessServiceError, match="session token"
        ) as errors:
            await service.handle(
                "reset",
                {"provider_id": "business", "run_id": RUN_ID, "seed": 1701},
            )
        assert errors.value.code == "principal.denied"

    _run(run())


def test_service_rejects_prepare_binding_drift(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        digest = service._workload_identity.config_digest
        payload = _prepare_payload(digest)
        drifts = (
            ({"task_id": "another.task"}, "identity.mismatch"),
            ({"package_digest": "e" * 64}, "identity.mismatch"),
            (
                {"runtime_image": "registry.invalid/other@sha256:" + "2" * 64},
                "identity.mismatch",
            ),
            ({"protocol_version": "aero-bench.other-rpc/v1"}, "identity.mismatch"),
            ({"run_id": "b" * 64}, "identity.mismatch"),
            ({"extra": True}, "request.invalid"),
        )
        for drift, expected_code in drifts:
            with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
                await service.handle("prepare", {**payload, **drift})
            assert errors.value.code == expected_code
        drifted_package = dict(payload["task_package"])
        drifted_package["task_id"] = "another.task"
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "prepare", {**payload, "task_package": drifted_package}
            )
        assert errors.value.code == "identity.mismatch"
        prepared = await service.handle("prepare", payload)
        assert prepared["status"] == "ready"
        with pytest.raises(
            _SERVICE.InspectionBusinessServiceError, match="twice"
        ) as errors:
            await service.handle("prepare", payload)
        assert errors.value.code == "not.ready"

    _run(run())


def test_service_enforces_trusted_principals(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        forgeries = (
            (
                _command_payload(
                    "business.observation_ready",
                    "command.forge",
                    1,
                    "agent.1",
                    actor_id="observation.provider",
                    observation_id="obs.1",
                    work_order_id="wo.1",
                ),
                "does not match the attested principal",
            ),
            (
                _command_payload(
                    "business.complete",
                    "command.forge2",
                    1,
                    "agent.1",
                    actor_id="business",
                    work_order_id="wo.1",
                ),
                "does not match the attested principal",
            ),
            (
                _command_payload(
                    "business.claim",
                    "command.forge3",
                    1,
                    "agent.1",
                    actor_id="business",
                    work_order_id="wo.1",
                ),
                "does not match the attested principal",
            ),
            (
                _command_payload(
                    "business.claim",
                    "command.forge4",
                    1,
                    "inspection.verifier",
                    actor_id="inspection.verifier",
                    work_order_id="wo.1",
                ),
                "holds role 'verifier' and cannot issue",
            ),
            (
                _command_payload(
                    "business.complete",
                    "command.forge5",
                    1,
                    "observation.provider",
                    actor_id="observation.provider",
                    work_order_id="wo.1",
                ),
                "cannot issue",
            ),
        )
        for payload, detail in forgeries:
            with pytest.raises(
                _SERVICE.InspectionBusinessServiceError, match=detail
            ) as errors:
                await service.handle("command", _authorized(payload))
            assert errors.value.code == "principal.denied"
        assert service._machine.state.version == 0

    _run(run())


def test_service_applies_legitimate_authority_commands(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        lifecycle = (
            ("command.claim", 1, "agent.1", "business.claim"),
            ("command.start", 2, "agent.1", "business.start"),
            (
                "command.observed",
                3,
                "observation.provider",
                "business.observation_ready",
            ),
            ("command.submit", 4, "agent.1", "business.submit"),
            ("command.complete", 5, "business", "business.complete"),
        )
        arguments_by_kind = {
            "business.claim": {"work_order_id": "wo.1", "actor_id": "agent.1"},
            "business.start": {"work_order_id": "wo.1", "actor_id": "agent.1"},
            "business.observation_ready": {
                "work_order_id": "wo.1",
                "actor_id": "observation.provider",
                "observation_id": "obs.1",
            },
            "business.submit": {
                "work_order_id": "wo.1",
                "actor_id": "agent.1",
                "observation_id": "obs.1",
                "report_payload_digest": PAYLOAD_DIGEST,
            },
            "business.complete": {"work_order_id": "wo.1", "actor_id": "business"},
        }
        previous_scene_state_digest = SCENE_STATE_ROOT_DIGEST
        for command_id, tick, agent_id, tool_id in lifecycle:
            stage_payload = _step_stage_payload(tick, previous_scene_state_digest)
            await service.handle("step_stage", stage_payload)
            previous_scene_state_digest = stage_payload["request"]["scene_state_digest"]
            response = await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        tool_id,
                        command_id,
                        tick,
                        agent_id,
                        **arguments_by_kind[tool_id],
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
                service._machine.history[-1].model_dump(mode="json")
            )
            if tool_id == "business.complete":
                assert [
                    event["event_id"]
                    for event in response["state_receipt"]["events"]
                ] == [f"state.{tick}", "public.status"]
            else:
                assert "state_receipt" not in response
        assert service._machine.state.work_orders[0].status.value == "completed"

    _run(run())


def test_service_rejects_unknown_or_misrouted_tools(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        for tool_id in ("business.unknown", "traci.simulation_step"):
            response = await service.handle(
                "command",
                _authorized(
                    _command_payload(
                        tool_id,
                        "command.unknown",
                        1,
                        "agent.1",
                        actor_id="agent.1",
                        work_order_id="wo.1",
                    )
                ),
            )
            assert [item["phase"] for item in response["receipts"]] == [
                "received",
                "failed",
            ]
            assert set(response) == {"receipts"}

    _run(run())


def test_service_rejects_command_time_after_barrier(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await service.handle(
            "step_stage",
            _step_stage_payload(1),
        )
        response = await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "business.claim",
                    "command.claim",
                    2,
                    "agent.1",
                    actor_id="agent.1",
                    work_order_id="wo.1",
                )
            ),
        )
        assert [item["phase"] for item in response["receipts"]] == [
            "received",
            "failed",
        ]
        assert service._machine.state.version == 0

    _run(run())


def test_service_finalization_writes_declared_artifact_chain(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        await service.handle(
            "step_stage",
            _step_stage_payload(1),
        )
        await service.handle(
            "command",
            _authorized(
                _command_payload(
                    "business.claim",
                    "command.claim",
                    1,
                    "agent.1",
                    actor_id="agent.1",
                    work_order_id="wo.1",
                )
            ),
        )
        finalization = await service.handle(
            "finalize",
            _authorized(
                {
                    "provider_id": "business",
                    "run_id": RUN_ID,
                    "request": {
                        "schema_version": (
                            "aero-bench.provider-finalization-request/v1"
                        ),
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
            "shutdown",
            _authorized({"provider_id": "business", "run_id": RUN_ID}),
        )
        artifact = tmp_path / "artifacts" / "business/state.json"
        content = artifact.read_bytes()
        document = json.loads(content)
        receipt = finalization["receipt"]
        assert receipt["event_chain_root"] == "d" * 64
        assert receipt["artifacts"] == [
            {
                "artifact_id": "artifact.business",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ]
        history = document["history"]
        assert len(history) == 1
        assert document["business_history_root"] == history[-1]["record_hash"]
        assert document["state"]["work_orders"][0]["status"] == "claimed"
        assert document["state"]["work_orders"][0]["claimed_by"] == "agent.1"
        assert document["event_chain_root"] == "d" * 64
        assert "event_ledger" not in document
        assert history[0]["previous_hash"] == "0" * 64

    _run(run())


def test_service_query_rejects_unknown_work_order(tmp_path: Path) -> None:
    async def run() -> None:
        service = await _prepared_service(tmp_path)
        with pytest.raises(
            _SERVICE.InspectionBusinessServiceError, match="unknown work order"
        ) as errors:
            await service.handle(
                "query",
                _authorized(
                    {
                        "provider_id": "business",
                        "run_id": RUN_ID,
                        "query": {
                            "run_id": RUN_ID,
                            "query_id": "query.1",
                            "kind": "work_order",
                            "work_order_id": "wo.missing",
                            "issued_at": {"tick": 0, "sim_time_ns": 0},
                        },
                    }
                ),
            )
        assert errors.value.code == "query.rejected"

    _run(run())


def test_service_requires_prepare_and_reset_ordering(tmp_path: Path) -> None:
    async def run() -> None:
        service = _make_service(tmp_path)
        # A pre-prepare frame without the executor-issued token cannot even
        # reach the readiness check.
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "reset",
                {"provider_id": "business", "run_id": RUN_ID, "seed": 1701},
            )
        assert errors.value.code == "principal.denied"
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "reset",
                _authorized(
                    {"provider_id": "business", "run_id": RUN_ID, "seed": 1701}
                ),
            )
        assert errors.value.code == "not.ready"
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "snapshot",
                _authorized({"provider_id": "business", "run_id": RUN_ID}),
            )
        assert errors.value.code == "not.ready"
        await service.handle(
            "prepare", _prepare_payload(service._workload_identity.config_digest)
        )
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "step_stage",
                _step_stage_payload(1),
            )
        assert errors.value.code == "not.ready"

    _run(run())


def test_service_closed_runtime_denies_unauthenticated_stateful_frames(
    tmp_path: Path,
) -> None:
    """Counterexample: a closed runtime discloses nothing without the token.

    Authentication precedes the closed gate on every stateful operation: a
    closed runtime answers a missing or wrong token with principal.denied —
    never the terminal not.ready — so a peer cannot even learn that the
    service has terminated.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        before = service._machine.state.model_dump(mode="json")
        service.close_runtime()
        frames = (
            {"provider_id": "business", "run_id": RUN_ID},
            {"provider_id": "other.provider", "run_id": "b" * 64},
        )
        operations = (
            "prepare",
            "reset",
            "step_stage",
            "command",
            "query",
            "snapshot",
            "shutdown",
        )
        for frame in frames:
            for token in (None, "8" * 64):
                payload = dict(frame)
                if token is not None:
                    payload["session_token"] = token
                for operation in operations:
                    with pytest.raises(
                        _SERVICE.InspectionBusinessServiceError
                    ) as errors:
                        await service.handle(operation, payload)
                    assert errors.value.code == "principal.denied", (
                        operation,
                        token is None,
                    )
        # Even a fully formed prepare payload with a wrong token is denied
        # before any shape, identity, or lifecycle check runs.
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "prepare",
                {
                    **_prepare_payload(service._workload_identity.config_digest),
                    "session_token": "8" * 64,
                },
            )
        assert errors.value.code == "principal.denied"
        assert service._machine.state.model_dump(mode="json") == before
        assert service._current == (0, 0)
        assert not service.shutdown_requested.is_set()

    _run(run())


def test_service_closed_runtime_answers_valid_token_with_terminal_not_ready(
    tmp_path: Path,
) -> None:
    """The documented safe terminal response for the granted session.

    With the executor-issued token presented, every stateful operation on a
    closed runtime is not.ready. The closed gate also precedes payload
    validation, so even a drifted prepare is the terminal not.ready, never
    request.invalid or identity.mismatch, and the token-free probe reports
    not ready for the Docker HEALTHCHECK.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        before = service._machine.state.model_dump(mode="json")
        service.close_runtime()
        frames = {
            "prepare": _prepare_payload(service._workload_identity.config_digest),
            "reset": _authorized(
                {"provider_id": "business", "run_id": RUN_ID, "seed": 1701}
            ),
            "step_stage": _step_stage_payload(1),
            "command": _authorized(
                _command_payload(
                    "business.claim",
                    "command.closed",
                    1,
                    "agent.1",
                    actor_id="agent.1",
                    work_order_id="wo.1",
                )
            ),
            "query": _authorized(
                {
                    "provider_id": "business",
                    "run_id": RUN_ID,
                    "query": {
                        "run_id": RUN_ID,
                        "query_id": "query.closed",
                        "kind": "work_order",
                        "work_order_id": "wo.1",
                        "issued_at": {"tick": 0, "sim_time_ns": 0},
                    },
                }
            ),
            "snapshot": _authorized({"provider_id": "business", "run_id": RUN_ID}),
            "shutdown": _authorized({"provider_id": "business", "run_id": RUN_ID}),
        }
        for operation, payload in frames.items():
            with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
                await service.handle(operation, payload)
            assert errors.value.code == "not.ready", operation
        # The closed gate precedes payload validation: a drifted prepare is
        # still the terminal not.ready.
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle(
                "prepare",
                {
                    **_prepare_payload(service._workload_identity.config_digest),
                    "run_id": "b" * 64,
                },
            )
        assert errors.value.code == "not.ready"
        # The probe stays token-free and stateless; the closed runtime is
        # simply not ready for the shared readiness probe.
        with pytest.raises(_SERVICE.InspectionBusinessServiceError) as errors:
            await service.handle("probe", {})
        assert errors.value.code == "not.ready"
        assert service._machine.state.model_dump(mode="json") == before
        assert not service.shutdown_requested.is_set()

    _run(run())


def test_service_rejects_forged_principal_without_session_token(tmp_path: Path) -> None:
    """Counterexample: replaying every public identity field forges nothing.

    An arbitrary RPC client — a peer workload on the provider network that
    knows provider_id, run_id, the pinned actors, and a valid command time —
    must not be able to act as any principal, because the actor/role checks
    bind to a Harness-attested agent_id that only the prepare-performing
    Harness session can carry behind its granted session token.
    """

    async def run() -> None:
        service = await _prepared_service(tmp_path)
        for token in (None, "8" * 64):
            payload = _command_payload(
                "business.complete",
                "command.forge",
                1,
                "business",
                actor_id="business",
                work_order_id="wo.1",
            )
            if token is not None:
                payload["session_token"] = token
            with pytest.raises(
                _SERVICE.InspectionBusinessServiceError, match="session token"
            ) as errors:
                await service.handle("command", payload)
            assert errors.value.code == "principal.denied"
        assert service._machine.state.version == 0

    _run(run())


def test_service_serves_stable_classes_over_real_rpc(tmp_path: Path) -> None:
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
            # A pre-prepare frame without the token is denied outright.
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "reset",
                    {"provider_id": "business", "run_id": RUN_ID, "seed": 1701},
                )
            assert errors.value.code == "principal.denied"
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "reset",
                    _authorized(
                        {"provider_id": "business", "run_id": RUN_ID, "seed": 1701}
                    ),
                )
            assert errors.value.code == "not.ready"
            await transport.request(
                "prepare", _prepare_payload(service._workload_identity.config_digest)
            )
            await transport.request(
                "reset",
                _authorized(
                    {"provider_id": "business", "run_id": RUN_ID, "seed": 1701}
                ),
            )
            with pytest.raises(ProviderRemoteError) as errors:
                await transport.request(
                    "command",
                    _authorized(
                        _command_payload(
                            "business.complete",
                            "command.forge",
                            1,
                            "agent.1",
                            actor_id="business",
                            work_order_id="wo.1",
                        )
                    ),
                )
            assert errors.value.code == "principal.denied"
            # Strict counterexample over the real wire: a second connection
            # replaying every public identity field without the granted token
            # forges nothing.
            forger = await JsonLineRpcTransport.connect(
                _Endpoint(host="127.0.0.1", port=port), component="test-forger"
            )
            try:
                for payload in (
                    _command_payload(
                        "business.complete",
                        "command.forge",
                        1,
                        "business",
                        actor_id="business",
                        work_order_id="wo.1",
                    ),
                    {
                        **_command_payload(
                            "business.complete",
                            "command.forge",
                            1,
                            "business",
                            actor_id="business",
                            work_order_id="wo.1",
                        ),
                        "session_token": "8" * 64,
                    },
                ):
                    with pytest.raises(ProviderRemoteError) as errors:
                        await forger.request("command", payload)
                    assert errors.value.code == "principal.denied"
            finally:
                await forger.close()
            await transport.request(
                "step_stage",
                _step_stage_payload(1),
            )
            response = await transport.request(
                "command",
                _authorized(
                    _command_payload(
                        "business.claim",
                        "command.claim",
                        1,
                        "agent.1",
                        actor_id="agent.1",
                        work_order_id="wo.1",
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
                service._machine.history[-1].model_dump(mode="json")
            )
        finally:
            await transport.close()
            await server.graceful_close()
            await handle.wait_closed()

    _run(run())


def test_business_state_writer_is_exact_and_atomic(tmp_path: Path) -> None:
    writer = _SERVICE.BusinessStateWriter(tmp_path, _requirement())
    digest = writer.write(b'{"state":1}\n')
    assert writer.path == tmp_path / "business/state.json"
    assert writer.path.read_bytes() == b'{"state":1}\n'
    assert digest == hashlib.sha256(b'{"state":1}\n').hexdigest()
    writer.write(b'{"state":2}\n')
    assert writer.path.read_bytes() == b'{"state":2}\n'
    assert tuple(tmp_path.rglob("*")) == (tmp_path / "business", writer.path)


def test_business_state_writer_enforces_max_size(tmp_path: Path) -> None:
    requirement = dict(_requirement())
    requirement["max_size_bytes"] = 4
    writer = _SERVICE.BusinessStateWriter(tmp_path, requirement)
    with pytest.raises(_SERVICE.InspectionBusinessServiceError, match="max_size"):
        writer.write(b"12345")
    assert not writer.path.exists()


def test_business_state_writer_rejects_undeclared_files(tmp_path: Path) -> None:
    writer = _SERVICE.BusinessStateWriter(tmp_path, _requirement())
    writer.write(b"{}")
    (tmp_path / "stray.json").write_bytes(b"{}")
    with pytest.raises(_SERVICE.InspectionBusinessServiceError, match="undeclared"):
        writer.write(b"{}")


def test_business_state_writer_rejects_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "business").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "business" / "state.json").symlink_to(tmp_path / "outside")
    with pytest.raises(_SERVICE.InspectionBusinessServiceError, match="symbolic link"):
        _SERVICE.BusinessStateWriter(tmp_path, _requirement())


def test_service_rejects_unsafe_artifact_requirement(tmp_path: Path) -> None:
    for relative_path in (
        "/state.json",
        ".",
        "a/../state.json",
        "a\\state.json",
    ):
        requirement = dict(_requirement())
        requirement["relative_path"] = relative_path
        with pytest.raises(_SERVICE.InspectionBusinessServiceError, match="normalized"):
            _SERVICE.BusinessStateWriter(tmp_path, requirement)


def test_service_accepts_public_runtime_requirement(tmp_path: Path) -> None:
    requirement = dict(_requirement())
    requirement["visibility"] = "public"
    writer = _SERVICE.BusinessStateWriter(tmp_path, requirement)
    assert writer.requirement["visibility"] == "public"


def test_service_rejects_asset_backed_requirement(tmp_path: Path) -> None:
    requirement = dict(_requirement())
    requirement["source_asset_id"] = "truth.1"
    with pytest.raises(
        _SERVICE.InspectionBusinessServiceError,
        match="source_asset_id",
    ):
        _SERVICE.BusinessStateWriter(tmp_path, requirement)
