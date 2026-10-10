"""Source and in-image self-check for the optional world-scene projection.

The adapter is not a built-in provider and owns no static scene declaration.
This check constructs the smallest selfcheck-only ``ResolvedScenario/v2`` that
exercises the service's current contract surface, seals it canonically, projects
only assets authorized to this workload, and loads it through an exact
``aero-bench.workload-contract/v5``. One static entity is deliberately owned by
``scenario.compiler``; the projection workload only mirrors its identity.

The real JSON-line service is then exercised through probe, reduced prepare,
reset, contract-clock barriers, snapshot, finalization, and shutdown. Negative
checks prove that a wrong session token binds no state, a stale prepare shape is
not accepted, and the compiler scenario digest cannot be replaced at prepare.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from containers.selfcheck_scenario import (
    ScenarioProvider,
    build_resolved_scenario,
    project_scenario_assets,
)

# The production image copies ``workload_scenario.py`` beside ``service.py``.
# When this selfcheck is run from a source checkout, point the import at the
# same authoritative module rather than maintaining a second copy.
_SOURCE_WORKLOAD_MODULE_DIR = (
    Path(__file__).resolve().parents[2] / "aero_bench" / "world"
)
if (_SOURCE_WORKLOAD_MODULE_DIR / "workload_scenario.py").is_file():
    sys.path.insert(0, str(_SOURCE_WORKLOAD_MODULE_DIR))

from aero_bench.providers.rpc import (  # noqa: E402
    JsonLineRpcServer,
    JsonLineRpcTransport,
    ProviderRemoteError,
)
from service import (  # noqa: E402
    EVIDENCE_ARTIFACT_TYPE,
    EVIDENCE_SCHEMA,
    FINALIZATION_BINDING_SCHEMA,
    PROTOCOL_VERSION,
    PROVIDER_ADAPTER,
    PROVIDER_PROBE_SCHEMA,
    SCENE_VERSION,
    STATE_SCHEMA,
    STATIC_AUTHORITY,
    WorldSceneService,
    _load_workload_identity,
)


RUN_ID = "a" * 64
SEED = 7
SOURCE_REVISION = "b" * 40
RUNTIME_IMAGE = "registry.invalid/aero-bench/world-scene@sha256:" + "1" * 64
PROVIDER_ID = "world"
PROJECTION_CAPABILITY = "scene.projection"
WORKLOAD_CONTRACT_SCHEMA = "aero-bench.workload-contract/v5"
SCENARIO_SCHEMA = "aero-bench.resolved-scenario/v4"
CONFIG_SCHEMA = "aero-bench.world-scene/v2"
SESSION_TOKEN = hashlib.sha256(
    b"aero-bench world-scene selfcheck session token"
).hexdigest()
RACER_TOKEN = "c" * 64
EVIDENCE_ARTIFACT_ID = "artifact.world.scene"
EVIDENCE_ARTIFACT_PATH = "scene/selfcheck-evidence.jsonl"
EVIDENCE_MAX_SIZE_BYTES = 1_048_576
STEP_NS = 100_000_000
MAX_STEPS = 3


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_canonical_json(path: Path, value: object) -> None:
    path.write_bytes(_canonical_json_bytes(value) + b"\n")


def _file_ref(bundle: Path, relative_path: str) -> dict[str, str]:
    payload = (bundle / relative_path).read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _resolved_scenario() -> dict[str, object]:
    return build_resolved_scenario(
        seed=SEED,
        providers=(
            ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
            ScenarioProvider(
                PROVIDER_ID,
                ("sensor",),
                (PROJECTION_CAPABILITY,),
                "business_environment",
            ),
        ),
        dynamic_provider_id="flight",
        verifier_id="verifier.selfcheck",
    )


def _artifact_requirement() -> dict[str, object]:
    return {
        "artifact_id": EVIDENCE_ARTIFACT_ID,
        "artifact_type": EVIDENCE_ARTIFACT_TYPE,
        "producer_id": PROVIDER_ID,
        "visibility": "private",
        "relative_path": EVIDENCE_ARTIFACT_PATH,
        "max_size_bytes": EVIDENCE_MAX_SIZE_BYTES,
        "source_asset_id": None,
    }


def _write_workload_contract(
    bundle: Path, *, port: int
) -> tuple[Path, dict[str, object], dict[str, object]]:
    scenario = _resolved_scenario()
    provider_config = {
        "schema_version": CONFIG_SCHEMA,
        "provider_id": PROVIDER_ID,
        "scene": {"version": SCENE_VERSION, "commit": SOURCE_REVISION},
    }
    _write_canonical_json(bundle / "provider.config.json", provider_config)
    _write_canonical_json(
        bundle / "provider.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": CONFIG_SCHEMA,
        },
    )
    _write_canonical_json(
        bundle / "protocol.schema.json",
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": PROTOCOL_VERSION,
        },
    )
    requirement = _artifact_requirement()
    contract: dict[str, object] = {
        "schema_version": WORKLOAD_CONTRACT_SCHEMA,
        "role": "provider",
        "run_id": RUN_ID,
        "seed": SEED,
        "workload_id": PROVIDER_ID,
        "clock": {
            "authority": "provider_barrier",
            "step_ns": STEP_NS,
            "max_steps": MAX_STEPS,
            "provider_timeout_ms": 5_000,
        },
        "provider": {
            "provider_id": PROVIDER_ID,
            "adapter": PROVIDER_ADAPTER,
            "port": port,
            "workload": {
                "runtime": {
                    "image": RUNTIME_IMAGE,
                    "command": ["provider", "serve"],
                },
                "resources": {
                    "cpu_millicores": 100,
                    "memory_mib": 64,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": PROVIDER_ADAPTER,
                    "kind": "production",
                    "source_uri": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                    "source_revision": SOURCE_REVISION,
                    "version": SCENE_VERSION,
                },
            },
            "config": {
                "file": _file_ref(bundle, "provider.config.json"),
                "schema_file": _file_ref(bundle, "provider.schema.json"),
            },
            "protocol_schema": _file_ref(bundle, "protocol.schema.json"),
            "capabilities": [PROJECTION_CAPABILITY],
            "artifact_requirements": [requirement],
        },
        "scenario_digest": scenario["scenario_digest"],
        "scenario": scenario,
        "scenario_assets": project_scenario_assets(
            scenario, role="provider", workload_id=PROVIDER_ID
        ),
    }
    contract_path = bundle / "workload-contract.json"
    _write_canonical_json(contract_path, contract)
    return contract_path, contract, scenario


def _assert_workload_identity(
    identity: object, contract: dict[str, object], scenario: dict[str, object]
) -> None:
    projection = getattr(identity, "projection", None)
    expected = {
        "run_id": RUN_ID,
        "seed": SEED,
        "provider_id": PROVIDER_ID,
        "runtime_image": RUNTIME_IMAGE,
        "scenario_digest": scenario["scenario_digest"],
        "step_ns": STEP_NS,
        "max_steps": MAX_STEPS,
        "source_revision": SOURCE_REVISION,
    }
    actual = {field: getattr(identity, field, None) for field in expected}
    if actual != expected:
        raise RuntimeError(f"world-scene WorkloadIdentity mismatch: {actual}")
    if (
        projection is None
        or projection.static_entity_ids != ("static.selfcheck",)
        or projection.dynamic_entity_ids != ("uav.selfcheck",)
        or projection.weather_sample_ids != ("weather.clear",)
        or projection.building_ids != ()
        or projection.scenario.scenario_digest != scenario["scenario_digest"]
    ):
        raise RuntimeError("world-scene compiler projection identity is invalid")
    projected = contract.get("scenario_assets")
    if not isinstance(projected, list) or [
        asset.get("asset_id") for asset in projected if isinstance(asset, dict)
    ] != ["imagery.tiles", "terrain.tiles"]:
        raise RuntimeError("world-scene workload asset projection is not exact")


def _assert_probe_identity(response: object, config_digest: str) -> None:
    expected = {
        "schema_version": PROVIDER_PROBE_SCHEMA,
        "status": "accepting",
        "run_id": RUN_ID,
        "provider_id": PROVIDER_ID,
        "adapter": PROVIDER_ADAPTER,
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": config_digest,
    }
    if response != expected:
        raise RuntimeError(f"world-scene probe identity mismatch: {response}")


def _connect(port: int) -> JsonLineRpcTransport:
    return JsonLineRpcTransport.connect(
        SimpleNamespace(host="127.0.0.1", port=port),
        component="world-scene-selfcheck",
    )


async def _expect_remote_error(
    transport: JsonLineRpcTransport,
    operation: str,
    payload: dict[str, object],
    *,
    code: str,
    detail_fragment: str,
) -> None:
    try:
        response = await transport.request(operation, payload)
    except ProviderRemoteError as exc:
        if exc.code != code or detail_fragment not in exc.detail:
            raise RuntimeError(
                f"world-scene rejection mismatch: code={exc.code!r}, detail={exc.detail!r}"
            ) from exc
    else:
        raise RuntimeError(f"world-scene accepted invalid {operation}: {response}")


async def _expect_principal_denied(
    port: int, operation: str, payload: dict[str, object]
) -> None:
    racer = await _connect(port)
    try:
        await _expect_remote_error(
            racer,
            operation,
            payload,
            code="principal.denied",
            detail_fragment="executor-issued",
        )
    finally:
        await racer.close()


async def _run_selfcheck() -> int:
    with tempfile.TemporaryDirectory(prefix="aero-world-scene-") as temporary:
        bundle = Path(temporary) / "bundle"
        artifacts = Path(temporary) / "artifacts"
        bundle.mkdir()
        artifacts.mkdir()

        port_socket = socket.socket()
        port_socket.bind(("127.0.0.1", 0))
        port = int(port_socket.getsockname()[1])
        port_socket.close()

        contract_path, contract, scenario = _write_workload_contract(bundle, port=port)
        identity = _load_workload_identity(
            contract_path=contract_path,
            bundle_root=bundle,
            expected_run_id=RUN_ID,
            expected_seed=SEED,
            expected_provider_id=PROVIDER_ID,
            expected_provider_port=port,
        )
        _assert_workload_identity(identity, contract, scenario)
        config_digest = identity.config_digest
        requirement = _artifact_requirement()
        service = WorldSceneService(
            bundle_root=bundle,
            artifact_root=artifacts,
            rpc_port=port,
            workload_identity=identity,
            session_token=SESSION_TOKEN,
        )
        rpc_server = JsonLineRpcServer(service.serve_rpc)
        await rpc_server.start(host="127.0.0.1", port=port)
        transport = await _connect(port)
        finalization_receipt: dict[str, object] | None = None
        try:
            _assert_probe_identity(await transport.request("probe", {}), config_digest)
            prepare_payload = {
                "provider_id": PROVIDER_ID,
                "run_id": RUN_ID,
                "runtime_image": RUNTIME_IMAGE,
                "config_digest": config_digest,
                "artifact_requirements": [requirement],
                "scenario_digest": scenario["scenario_digest"],
                "session_token": SESSION_TOKEN,
            }
            await _expect_principal_denied(
                port,
                "prepare",
                {**prepare_payload, "session_token": RACER_TOKEN},
            )
            await _expect_principal_denied(
                port,
                "prepare",
                {
                    key: value
                    for key, value in prepare_payload.items()
                    if key != "session_token"
                },
            )
            await _expect_remote_error(
                transport,
                "prepare",
                {**prepare_payload, "world_package": {"stale": True}},
                code="request.invalid",
                detail_fragment="fields are not exact",
            )
            await _expect_remote_error(
                transport,
                "prepare",
                {**prepare_payload, "scenario_digest": "f" * 64},
                code="request.invalid",
                detail_fragment="differs from the workload contract",
            )
            readiness = await transport.request("prepare", prepare_payload)
            expected_readiness = {
                "status": "ready",
                "provider_id": PROVIDER_ID,
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": RUNTIME_IMAGE,
                "scenario_schema_version": SCENARIO_SCHEMA,
                "scenario_digest": scenario["scenario_digest"],
                "world_schema_version": "aero-bench.world/v2",
                "world_id": scenario["world_id"],
                "world_digest": scenario["world_digest"],
                "source_asset_digest": scenario["source_asset_digest"],
                "scenario_asset_digest": scenario["scenario_asset_digest"],
                "static_authority": STATIC_AUTHORITY,
                "scene_version": SCENE_VERSION,
                "scene_commit": SOURCE_REVISION,
            }
            if readiness != expected_readiness:
                raise RuntimeError(
                    f"world-scene readiness projection mismatch: {readiness}"
                )
            await _expect_principal_denied(
                port,
                "prepare",
                {**prepare_payload, "session_token": RACER_TOKEN},
            )
            await _expect_remote_error(
                transport,
                "prepare",
                prepare_payload,
                code="request.invalid",
                detail_fragment="called twice",
            )

            identity_fields = {
                "provider_id": PROVIDER_ID,
                "run_id": RUN_ID,
                "session_token": SESSION_TOKEN,
            }
            reset = await transport.request(
                "reset", {**identity_fields, "seed": SEED}
            )
            receipt = reset.get("receipt")
            if not isinstance(receipt, dict) or receipt.get("reached") != {
                "tick": 0,
                "sim_time_ns": 0,
            }:
                raise RuntimeError("world-scene reset did not establish time zero")
            await _expect_principal_denied(
                port,
                "reset",
                {
                    "provider_id": PROVIDER_ID,
                    "run_id": RUN_ID,
                    "seed": SEED,
                    "session_token": RACER_TOKEN,
                },
            )

            last_state_digest = receipt.get("state_digest")
            for tick in range(1, MAX_STEPS + 1):
                response = await transport.request(
                    "step_to",
                    {
                        **identity_fields,
                        "request": {
                            "run_id": RUN_ID,
                            "target": {
                                "tick": tick,
                                "sim_time_ns": tick * STEP_NS,
                            },
                        },
                    },
                )
                receipt = response.get("receipt")
                if not isinstance(receipt, dict) or receipt.get("reached") != {
                    "tick": tick,
                    "sim_time_ns": tick * STEP_NS,
                }:
                    raise RuntimeError(
                        f"world-scene step did not reach exact target: {response}"
                    )
                state_events = [
                    event
                    for event in receipt.get("events", [])
                    if event.get("payload_schema_id") == STATE_SCHEMA
                ]
                if len(state_events) != 1:
                    raise RuntimeError("world-scene receipt lacks one projection event")
                payload = {
                    item["name"]: item["value"]
                    for item in state_events[0]["payload"]
                }
                if (
                    payload.get("scenario_digest") != scenario["scenario_digest"]
                    or payload.get("static_authority") != STATIC_AUTHORITY
                    or json.loads(payload["weather_sample_ids_json"])
                    != ["weather.clear"]
                    or payload.get("entity_count") != 2
                    or payload.get("building_count") != 0
                    or payload.get("scene_state_sha256")
                    != receipt.get("state_digest")
                ):
                    raise RuntimeError(
                        f"world-scene event is not the compiler projection: {payload}"
                    )
                last_state_digest = receipt.get("state_digest")

            snapshot = await transport.request("snapshot", identity_fields)
            if snapshot.get("snapshot_digest") != last_state_digest:
                raise RuntimeError(
                    "world-scene snapshot digest differs from the barrier state"
                )
            finalization = await transport.request(
                "finalize",
                {
                    **identity_fields,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {
                            "tick": MAX_STEPS,
                            "sim_time_ns": MAX_STEPS * STEP_NS,
                        },
                        "event_chain_root": "d" * 64,
                    },
                },
            )
            candidate = finalization.get("receipt")
            if not isinstance(candidate, dict):
                raise RuntimeError("world-scene service did not finalize its evidence")
            finalization_receipt = candidate
            stopped = await transport.request("shutdown", identity_fields)
            if stopped != {"status": "stopped"}:
                raise RuntimeError("world-scene service did not acknowledge shutdown")
        finally:
            await transport.close()
            await rpc_server.graceful_close()
            service.close_runtime()

        evidence = artifacts / EVIDENCE_ARTIFACT_PATH
        if not evidence.is_file():
            raise RuntimeError("world-scene service did not write its evidence artifact")
        records = [json.loads(line) for line in evidence.read_text().splitlines()]
        state_records = records[:-1]
        expected_operations = ["reset", *("step_to" for _ in range(MAX_STEPS)), "snapshot"]
        if (
            [record.get("operation") for record in state_records]
            != expected_operations
            or any(
                record.get("schema_version") != EVIDENCE_SCHEMA
                or record.get("artifact_id") != EVIDENCE_ARTIFACT_ID
                or record.get("artifact_type") != EVIDENCE_ARTIFACT_TYPE
                or record.get("scenario_digest") != scenario["scenario_digest"]
                or record.get("static_authority") != STATIC_AUTHORITY
                or record.get("scene_state", {}).get("projection_kind")
                != "resolved_scenario"
                for record in state_records
            )
        ):
            raise RuntimeError(
                "world-scene evidence does not contain the read-only projection sequence"
            )
        finalization_binding = records[-1] if records else None
        if finalization_binding != {
            "schema_version": FINALIZATION_BINDING_SCHEMA,
            "run_id": RUN_ID,
            "provider_id": PROVIDER_ID,
            "terminal_event": "run.completed",
            "terminal_time": {
                "tick": MAX_STEPS,
                "sim_time_ns": MAX_STEPS * STEP_NS,
            },
            "event_chain_root": "d" * 64,
        }:
            raise RuntimeError("world-scene evidence is not bound to finalization")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        if finalization_receipt is None or finalization_receipt != {
            "schema_version": "aero-bench.provider-finalization-receipt/v1",
            "run_id": RUN_ID,
            "provider_id": PROVIDER_ID,
            "event_chain_root": "d" * 64,
            "artifacts": [
                {
                    "artifact_id": EVIDENCE_ARTIFACT_ID,
                    "sha256": digest,
                    "size_bytes": evidence.stat().st_size,
                }
            ],
        }:
            raise RuntimeError("world-scene finalization receipt is not exact")

        print(
            json.dumps(
                {
                    "config_schema": CONFIG_SCHEMA,
                    "evidence_sha256": digest,
                    "projection_kind": "resolved_scenario",
                    "protocol_version": PROTOCOL_VERSION,
                    "scenario_digest": scenario["scenario_digest"],
                    "scenario_schema": SCENARIO_SCHEMA,
                    "static_authority": STATIC_AUTHORITY,
                    "status": "read-only-scenario-projection-rpc-ok",
                    "steps": MAX_STEPS,
                    "workload_contract_schema": WORKLOAD_CONTRACT_SCHEMA,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        from service import main as service_main

        return service_main(arguments)
    return asyncio.run(_run_selfcheck())


if __name__ == "__main__":
    raise SystemExit(main())
