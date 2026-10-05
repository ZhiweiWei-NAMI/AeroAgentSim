from __future__ import annotations

import importlib.util
import hashlib
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from aero_bench.world.resolved import ResolvedScenario, scenario_assets_for_workload
from containers.selfcheck_scenario import ScenarioProvider, build_resolved_scenario


_PROBE_PATH = Path(__file__).parents[1] / "containers" / "readiness_probe.py"
_SPEC = importlib.util.spec_from_file_location(
    "aero_bench_readiness_probe", _PROBE_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
_PROBE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_PROBE)


RUN_ID = "a" * 64
RUNTIME_IMAGE = "registry.example/aero-bench/provider@sha256:" + "1" * 64
CONFIG_DIGEST = hashlib.sha256(b'{"schema_version":"provider/v1"}\n').hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def _scenario() -> ResolvedScenario:
    return ResolvedScenario.model_validate(
        build_resolved_scenario(
            seed=7,
            providers=(
                ScenarioProvider(
                    "network",
                    ("wireless_network",),
                    ("network.message",),
                    "network",
                ),
                ScenarioProvider("flight", ("motion",), ("gazebo.physics",), "motion"),
            ),
            dynamic_provider_id="flight",
        )
    )


def _scenario_fields(role: str, workload_id: str) -> dict[str, Any]:
    scenario = _scenario()
    return {
        "scenario_digest": scenario.scenario_digest,
        "scenario_assets": [
            asset.model_dump(mode="json")
            for asset in scenario_assets_for_workload(
                scenario, role=role, workload_id=workload_id
            )
        ],
    }


def _file_ref(bundle: Path, name: str, content: bytes) -> dict[str, str]:
    path = bundle / name
    path.write_bytes(content)
    return {"path": name, "sha256": hashlib.sha256(content).hexdigest()}


def _provider_contract(root: Path, port: int) -> tuple[Path, dict[str, Any]]:
    bundle = root / "bundle"
    bundle.mkdir()
    config = _file_ref(bundle, "provider.json", b'{"schema_version":"provider/v1"}\n')
    schema = _file_ref(bundle, "provider.schema.json", b'{"type":"object"}\n')
    requirement = {
        "artifact_id": "artifact.network.delivery",
        "artifact_type": "network.delivery",
        "producer_id": "network",
        "visibility": "private",
        "relative_path": "network/delivery.json",
        "max_size_bytes": 4096,
        "source_asset_id": None,
    }
    contract = {
        "schema_version": "aero-bench.workload-contract/v5",
        "role": "provider",
        "run_id": RUN_ID,
        "seed": 7,
        "workload_id": "network",
        "clock": {},
        "provider": {
            "provider_id": "network",
            "adapter": "ns3.rpc",
            "port": port,
            "workload": {
                "runtime": {"image": RUNTIME_IMAGE, "command": ["provider", "serve"]},
                "resources": {},
                "implementation": {},
            },
            "config": {"file": config, "schema_file": schema},
            "protocol_schema": schema,
            "capabilities": ["network.message"],
            "artifact_requirements": [requirement],
        },
        "scenario": _scenario().model_dump(mode="json"),
        **_scenario_fields("provider", "network"),
    }
    contract_path = root / "contract.json"
    contract_path.write_bytes(_canonical(contract))
    return contract_path, contract


def _harness_contract(root: Path, port: int) -> Path:
    contract = {
        "schema_version": "aero-bench.harness-workload-contract/v6",
        "role": "harness",
        "workload_id": "harness",
        "run": {
            "schema_version": "aero-bench.resolved-run/v5",
            "run_id": RUN_ID,
            "seed": 7,
            "scenario": _scenario().model_dump(mode="json"),
            "environment": {"gateway": {"port": port}},
        },
        **_scenario_fields("harness", "harness"),
    }
    contract_path = root / "harness-contract.json"
    contract_path.write_bytes(_canonical(contract))
    return contract_path


def _serve_once(
    response: bytes, ready: threading.Event, received: list[bytes]
) -> tuple[threading.Thread, int]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])

    def run() -> None:
        ready.set()
        with listener:
            connection, _ = listener.accept()
            with connection:
                received.append(connection.recv(4096))
                connection.sendall(response)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, port


def _set_provider_environment(
    monkeypatch, contract: Path, bundle: Path, port: int
) -> None:
    monkeypatch.setenv("AERO_BENCH_ROLE", "provider")
    monkeypatch.setenv("AERO_BENCH_PROVIDER_PORT", str(port))
    monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
    monkeypatch.setenv("AERO_BENCH_WORKLOAD_ID", "network")
    monkeypatch.setenv("AERO_BENCH_CONTRACT", str(contract))
    monkeypatch.setenv("AERO_BENCH_BUNDLE_DIR", str(bundle))


def test_provider_probe_is_one_canonical_empty_request(
    tmp_path: Path, monkeypatch
) -> None:
    received: list[bytes] = []
    ready = threading.Event()
    response = {
        "schema_version": "aero-bench.provider-probe/v1",
        "status": "accepting",
        "run_id": RUN_ID,
        "provider_id": "network",
        "adapter": "ns3.rpc",
        "runtime_image": RUNTIME_IMAGE,
        "config_digest": CONFIG_DIGEST,
    }
    thread, port = _serve_once(_canonical(response), ready, received)
    root = tmp_path
    contract, _ = _provider_contract(root, port)
    _set_provider_environment(monkeypatch, contract, root / "bundle", port)
    assert ready.wait(1)
    assert _PROBE.main(["--timeout-seconds", "1"]) == 0
    thread.join(timeout=1)
    assert received == [b'{"operation":"probe"}\n']


@pytest.mark.parametrize(
    "response",
    [
        {
            "schema_version": "aero-bench.provider-probe/v1",
            "status": "accepting",
            "run_id": "c" * 64,
            "provider_id": "network",
            "adapter": "ns3.rpc",
            "runtime_image": RUNTIME_IMAGE,
            "config_digest": "b" * 64,
        },
        {
            "schema_version": "aero-bench.provider-probe/v1",
            "status": "accepting",
            "run_id": RUN_ID,
            "provider_id": "network",
            "adapter": "ns3.rpc",
            "runtime_image": RUNTIME_IMAGE,
            "config_digest": "b" * 64,
            "extra": True,
        },
    ],
)
def test_provider_probe_rejects_identity_or_extra_fields(
    tmp_path: Path, monkeypatch, response: dict[str, Any]
) -> None:
    received: list[bytes] = []
    ready = threading.Event()
    thread, port = _serve_once(_canonical(response), ready, received)
    contract, _ = _provider_contract(tmp_path, port)
    _set_provider_environment(monkeypatch, contract, tmp_path / "bundle", port)
    assert ready.wait(1)
    assert _PROBE.main(["--timeout-seconds", "1"]) == 1
    thread.join(timeout=1)


@pytest.mark.parametrize(
    "raw_response",
    [
        b'{"schema_version":"aero-bench.provider-probe/v1","status":"accepting","run_id":"'
        + RUN_ID.encode()
        + b'","provider_id":"network","adapter":"ns3.rpc","adapter":"ns3.rpc","runtime_image":"'
        + RUNTIME_IMAGE.encode()
        + b'","config_digest":"'
        + b"b" * 64
        + b'"}\n',
        b'{"schema_version":"aero-bench.provider-probe/v1","status":"accepting","run_id":"'
        + RUN_ID.encode()
        + b'","provider_id":"network","adapter":"ns3.rpc","runtime_image":"'
        + RUNTIME_IMAGE.encode()
        + b'","config_digest":NaN}\n',
    ],
)
def test_provider_probe_rejects_duplicate_and_nonfinite_json(
    tmp_path: Path, monkeypatch, raw_response: bytes
) -> None:
    received: list[bytes] = []
    ready = threading.Event()
    thread, port = _serve_once(raw_response, ready, received)
    contract, _ = _provider_contract(tmp_path, port)
    _set_provider_environment(monkeypatch, contract, tmp_path / "bundle", port)
    assert ready.wait(1)
    assert _PROBE.main(["--timeout-seconds", "1"]) == 1
    thread.join(timeout=1)


def test_harness_probe_checks_gateway_schema_and_run(
    tmp_path: Path, monkeypatch
) -> None:
    response = {
        "schema_version": "aero-bench.gateway-probe/v1",
        "status": "ready",
        "run_id": RUN_ID,
        "current": {"tick": 0, "sim_time_ns": 0},
    }
    received: list[bytes] = []
    ready = threading.Event()
    thread, port = _serve_once(_canonical(response), ready, received)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    contract = _harness_contract(tmp_path, port)
    monkeypatch.setenv("AERO_BENCH_ROLE", "harness")
    monkeypatch.setenv("AERO_BENCH_GATEWAY_PORT", str(port))
    monkeypatch.setenv("AERO_BENCH_RUN_ID", RUN_ID)
    monkeypatch.setenv("AERO_BENCH_CONTRACT", str(contract))
    monkeypatch.setenv("AERO_BENCH_BUNDLE_DIR", str(bundle))
    assert ready.wait(1)
    assert _PROBE.main(["--timeout-seconds", "1"]) == 0
    thread.join(timeout=1)
    assert received == [b'{"operation":"probe"}\n']


def test_probe_without_a_listener_fails_once(tmp_path: Path, monkeypatch) -> None:
    contract, _ = _provider_contract(tmp_path, 17433)
    _set_provider_environment(monkeypatch, contract, tmp_path / "bundle", 17433)
    assert _PROBE.main(["--timeout-seconds", "0.01"]) == 1


def test_runtime_dockerfiles_declare_dynamic_healthchecks() -> None:
    root = Path(__file__).parents[1]
    for relative in (
        "containers/ns3/Dockerfile",
        "containers/sumo/Dockerfile",
        "containers/world-scene/Dockerfile",
        "containers/harness/Dockerfile",
    ):
        dockerfile = (root / relative).read_text()
        assert "COPY containers/readiness_probe.py" in dockerfile
        assert "HEALTHCHECK --interval=" in dockerfile
        assert "--timeout=" in dockerfile
        assert "--start-period=" in dockerfile
        assert "--retries=" in dockerfile
        assert "readiness_probe.py" in dockerfile
        assert any(
            f'CMD ["{interpreter}", "/opt/aero-bench/readiness_probe.py", '
            '"--timeout-seconds", "2"]' in dockerfile
            for interpreter in ("python", "python3")
        )
        assert "EXPOSE " not in dockerfile
