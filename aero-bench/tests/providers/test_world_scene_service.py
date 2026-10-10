from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from aero_bench.executor.contracts import ProviderWorkloadContract


_CONTAINER_ROOT = Path(__file__).parents[2] / "containers" / "world-scene"
_SERVICE_PATH = _CONTAINER_ROOT / "service.py"
_SELFCHECK_PATH = _CONTAINER_ROOT / "selfcheck.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SERVICE = _load_module("aero_bench_world_scene_service", _SERVICE_PATH)
_previous_service = sys.modules.get("service")
sys.modules["service"] = _SERVICE
try:
    _SELFCHECK = _load_module("aero_bench_world_scene_selfcheck", _SELFCHECK_PATH)
finally:
    if _previous_service is None:
        sys.modules.pop("service", None)
    else:
        sys.modules["service"] = _previous_service


RUN_ID = _SELFCHECK.RUN_ID
SEED = _SELFCHECK.SEED
IMAGE = _SELFCHECK.RUNTIME_IMAGE
PROVIDER_ID = _SELFCHECK.PROVIDER_ID
SESSION_TOKEN = _SELFCHECK.SESSION_TOKEN
STEP_NS = _SELFCHECK.STEP_NS
REQUIREMENT = _SELFCHECK._artifact_requirement()


def _fixture(tmp_path: Path):
    bundle = tmp_path / "bundle"
    artifacts = tmp_path / "artifacts"
    bundle.mkdir()
    artifacts.mkdir()
    contract_path, contract, scenario = _SELFCHECK._write_workload_contract(
        bundle, port=17436
    )
    identity = _SERVICE._load_workload_identity(
        contract_path=contract_path,
        bundle_root=bundle,
        expected_run_id=RUN_ID,
        expected_seed=SEED,
        expected_provider_id=PROVIDER_ID,
        expected_provider_port=17436,
    )
    service = _SERVICE.WorldSceneService(
        bundle_root=bundle,
        artifact_root=artifacts,
        rpc_port=17436,
        workload_identity=identity,
        session_token=SESSION_TOKEN,
    )
    prepare = {
        "provider_id": PROVIDER_ID,
        "run_id": RUN_ID,
        "runtime_image": IMAGE,
        "config_digest": identity.config_digest,
        "artifact_requirements": [REQUIREMENT],
        "scenario_digest": scenario["scenario_digest"],
        "session_token": SESSION_TOKEN,
    }
    return service, contract_path, contract, scenario, identity, prepare


def test_workload_contract_v3_binds_a_read_only_resolved_scenario(
    tmp_path: Path,
) -> None:
    service, contract_path, contract, scenario, identity, _ = _fixture(tmp_path)
    try:
        validated = ProviderWorkloadContract.model_validate_json(
            contract_path.read_bytes()
        )
        assert validated.schema_version == "aero-bench.workload-contract/v5"
        assert validated.scenario.schema_version == "aero-bench.resolved-scenario/v4"
        assert validated.scenario.scenario_digest == scenario["scenario_digest"]
        assert identity.seed == SEED
        assert identity.step_ns == STEP_NS
        assert identity.max_steps == _SELFCHECK.MAX_STEPS
        assert identity.projection.static_entity_ids == ("static.selfcheck",)
        assert identity.projection.dynamic_entity_ids == ("uav.selfcheck",)
        assert identity.projection.weather_sample_ids == ("weather.clear",)
        assert identity.projection.building_ids == ()
        assert [asset["asset_id"] for asset in contract["scenario_assets"]] == [
            "imagery.tiles",
            "terrain.tiles",
        ]
        static_entity = next(
            entity for entity in scenario["entities"] if entity["state"] == "static"
        )
        assert static_entity["owner_kind"] == "scenario"
        assert static_entity["owner_id"] == "scenario.compiler"
        assert static_entity["source_provider_id"] is None
        assert static_entity["authority_kind"] == "scenario_static"
        assert set(contract["provider"]["config"]) == {"file", "schema_file"}
    finally:
        service.close_runtime()


def test_service_rejects_placeholder_and_malformed_session_tokens(
    tmp_path: Path,
) -> None:
    service, _, _, _, identity, _ = _fixture(tmp_path)
    service.close_runtime()
    for token in ("0" * 64, "g" * 64, "", "9" * 63):
        with pytest.raises(ValueError, match="session token"):
            _SERVICE.WorldSceneService(
                bundle_root=tmp_path / "bundle",
                artifact_root=tmp_path / "artifacts",
                rpc_port=17436,
                workload_identity=identity,
                session_token=token,
            )


def test_prepare_requires_principal_and_exact_reduced_shape(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _, scenario, _, prepare = _fixture(tmp_path)
        try:
            for payload in (
                {**prepare, "session_token": "8" * 64},
                {key: value for key, value in prepare.items() if key != "session_token"},
            ):
                with pytest.raises(_SERVICE.WorldSceneServiceError) as errors:
                    await service.handle("prepare", payload)
                assert errors.value.code == "principal.denied"
                assert service._scene is None

            with pytest.raises(
                _SERVICE.WorldSceneServiceError, match="fields are not exact"
            ):
                await service.handle(
                    "prepare", {**prepare, "world_package": {"stale": True}}
                )
            with pytest.raises(
                _SERVICE.WorldSceneServiceError,
                match="scenario digest differs from the workload contract",
            ):
                await service.handle(
                    "prepare", {**prepare, "scenario_digest": "f" * 64}
                )
            assert service._scene is None

            ready = await service.handle("prepare", prepare)
            assert ready == {
                "status": "ready",
                "provider_id": PROVIDER_ID,
                "protocol_version": "aero-bench.world-scene-rpc/v2",
                "runtime_image": IMAGE,
                "scenario_schema_version": "aero-bench.resolved-scenario/v4",
                "scenario_digest": scenario["scenario_digest"],
                "world_schema_version": "aero-bench.world/v2",
                "world_id": scenario["world_id"],
                "world_digest": scenario["world_digest"],
                "source_asset_digest": scenario["source_asset_digest"],
                "scenario_asset_digest": scenario["scenario_asset_digest"],
                "static_authority": "scenario.compiler",
                "scene_version": "0.3.0-world-scene.2",
                "scene_commit": _SELFCHECK.SOURCE_REVISION,
            }
            with pytest.raises(_SERVICE.WorldSceneServiceError, match="called twice"):
                await service.handle("prepare", prepare)
        finally:
            service.close_runtime()

    asyncio.run(run())


def test_projection_barriers_and_finalization_are_digest_bound(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _, scenario, _, prepare = _fixture(tmp_path)
        identity_fields = {
            "provider_id": PROVIDER_ID,
            "run_id": RUN_ID,
            "session_token": SESSION_TOKEN,
        }
        try:
            await service.handle("prepare", prepare)
            reset = await service.handle(
                "reset", {**identity_fields, "seed": SEED}
            )
            assert reset["receipt"]["reached"] == {"tick": 0, "sim_time_ns": 0}

            step = await service.handle(
                "step_to",
                {
                    **identity_fields,
                    "request": {
                        "run_id": RUN_ID,
                        "target": {"tick": 1, "sim_time_ns": STEP_NS},
                    },
                },
            )
            receipt = step["receipt"]
            event = next(
                item
                for item in receipt["events"]
                if item["payload_schema_id"] == "world.scene.projection.v2"
            )
            payload = {item["name"]: item["value"] for item in event["payload"]}
            assert payload["scenario_digest"] == scenario["scenario_digest"]
            assert payload["static_authority"] == "scenario.compiler"
            assert json.loads(payload["weather_sample_ids_json"]) == ["weather.clear"]
            assert payload["entity_count"] == 2
            assert payload["building_count"] == 0

            with pytest.raises(
                _SERVICE.WorldSceneServiceError, match="next contract clock barrier"
            ):
                await service.handle(
                    "step_to",
                    {
                        **identity_fields,
                        "request": {
                            "run_id": RUN_ID,
                            "target": {"tick": 3, "sim_time_ns": 3 * STEP_NS},
                        },
                    },
                )
            snapshot = await service.handle("snapshot", identity_fields)
            assert snapshot["snapshot_digest"] == receipt["state_digest"]

            finalization = await service.handle(
                "finalize",
                {
                    **identity_fields,
                    "request": {
                        "schema_version": "aero-bench.provider-finalization-request/v1",
                        "run_id": RUN_ID,
                        "terminal_event": "run.completed",
                        "terminal_time": {"tick": 1, "sim_time_ns": STEP_NS},
                        "event_chain_root": "d" * 64,
                    },
                },
            )
            evidence = tmp_path / "artifacts" / REQUIREMENT["relative_path"]
            content = evidence.read_bytes()
            records = [json.loads(line) for line in content.splitlines()]
            assert records[-1] == {
                "schema_version": _SERVICE.FINALIZATION_BINDING_SCHEMA,
                "run_id": RUN_ID,
                "provider_id": PROVIDER_ID,
                "terminal_event": "run.completed",
                "terminal_time": {"tick": 1, "sim_time_ns": STEP_NS},
                "event_chain_root": "d" * 64,
            }
            assert finalization["receipt"]["artifacts"] == [
                {
                    "artifact_id": REQUIREMENT["artifact_id"],
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }
            ]
            with pytest.raises(_SERVICE.WorldSceneServiceError, match="already finalized"):
                await service.handle("reset", {**identity_fields, "seed": SEED})
            assert await service.handle("shutdown", identity_fields) == {
                "status": "stopped"
            }
        finally:
            service.close_runtime()

    asyncio.run(run())


def test_service_maps_denied_peer_to_stable_wire_error(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _, _, _, prepare = _fixture(tmp_path)
        try:
            response = await service.serve_rpc(
                "prepare", {**prepare, "session_token": "8" * 64}
            )
            assert response["error"]["code"] == "principal.denied"
            assert (await service.serve_rpc("prepare", prepare))["status"] == "ready"
        finally:
            service.close_runtime()

    asyncio.run(run())
