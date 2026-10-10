from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


SUMO_VERSION = "1.27.1"
SUMO_COMMIT = "7717f2379d9e314a0c81c5cec748444de06a2a91"
PROTOCOL_VERSION = "aero-bench.sumo-traci/v1"
EVIDENCE_ARTIFACT_ID = "artifact.sumo.traffic"
EVIDENCE_TYPE = "sumo.traffic.evidence"
EVIDENCE_ARTIFACT_PATH = "nested/traffic/sumo-evidence.jsonl"
EVIDENCE_MAX_SIZE_BYTES = 16_777_216
RUN_ID = "b" * 64
STEP_NS = 100_000_000
SESSION_TOKEN = "c" * 64
FORMAL_VALIDATION_ENV = "AERO_BENCH_SUMO_FORMAL"
DIGEST_IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def _skip_or_block(reason: str) -> None:
    if os.environ.get(FORMAL_VALIDATION_ENV) == "1":
        pytest.fail(f"BLOCKED: {reason}")
    pytest.skip(reason)


def _write_scenario(root: Path) -> None:
    subprocess.run(
        (
            "netgenerate",
            "--grid",
            "--grid.number=2",
            "--grid.length=100",
            "--output-file",
            str(root / "network.net.xml"),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    network_root = ET.parse(root / "network.net.xml").getroot()
    edge_id = next(
        edge.attrib["id"]
        for edge in network_root.findall("edge")
        if not edge.attrib["id"].startswith(":")
    )
    routes = ET.Element("routes")
    ET.SubElement(
        routes,
        "vType",
        id="reference.vehicle",
        accel="2.6",
        decel="4.5",
        length="5.0",
        maxSpeed="13.9",
        sigma="0",
    )
    ET.SubElement(routes, "route", id="reference.route", edges=edge_id)
    ET.SubElement(
        routes,
        "vehicle",
        id="reference.0",
        route="reference.route",
        type="reference.vehicle",
        depart="0",
    )
    ET.ElementTree(routes).write(
        root / "route.rou.xml", encoding="utf-8", xml_declaration=True
    )
    configuration = ET.Element("configuration")
    inputs = ET.SubElement(configuration, "input")
    ET.SubElement(inputs, "net-file", value="network.net.xml")
    ET.SubElement(inputs, "route-files", value="route.rou.xml")
    timing = ET.SubElement(configuration, "time")
    ET.SubElement(timing, "step-length", value="0.1")
    ET.ElementTree(configuration).write(
        root / "scenario.sumocfg", encoding="utf-8", xml_declaration=True
    )


def _ref(root: Path, path: str) -> dict[str, str]:
    source = root / path
    return {"path": path, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def _port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _rpc(
    connection: socket.socket, operation: str, payload: dict[str, object]
) -> dict[str, object]:
    frame = (
        json.dumps(
            {"operation": operation, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
    )
    connection.sendall(frame)
    response_bytes = b""
    while not response_bytes.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            raise AssertionError("SUMO service closed the RPC connection")
        response_bytes += chunk
    response = json.loads(response_bytes)
    if "error" in response:
        raise AssertionError(response["error"])
    return response


def _rpc_error(
    connection: socket.socket,
    operation: str,
    payload: dict[str, object],
    expected_code: str,
) -> None:
    frame = (
        json.dumps(
            {"operation": operation, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        + b"\n"
    )
    connection.sendall(frame)
    response_bytes = b""
    while not response_bytes.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            raise AssertionError("SUMO service closed the RPC connection")
        response_bytes += chunk
    response = json.loads(response_bytes)
    error = response.get("error")
    assert isinstance(error, dict)
    assert error.get("code") == expected_code


@pytest.mark.sumo_integration
def test_sumo_default_service_container_rpc_round_trip(tmp_path: Path) -> None:
    """Exercise the real SUMO/TraCI container; ordinary pytest is not evidence."""
    image = os.environ.get("AERO_BENCH_SUMO_IMAGE")
    if image is None:
        _skip_or_block("AERO_BENCH_SUMO_IMAGE must name the current SUMO image")
    if DIGEST_IMAGE_PATTERN.fullmatch(image) is None:
        _skip_or_block("AERO_BENCH_SUMO_IMAGE must be digest-pinned")
    if subprocess.run(("docker", "info"), capture_output=True).returncode != 0:
        _skip_or_block("Docker daemon is unavailable")
    image_inspect = subprocess.run(
        ("docker", "image", "inspect", "--format", "{{.Id}}", image),
        capture_output=True,
        text=True,
    )
    if image_inspect.returncode != 0:
        _skip_or_block(f"SUMO integration image is unavailable: {image}")
    runtime_image = os.environ.get("AERO_BENCH_SUMO_RUNTIME_IMAGE")
    if runtime_image is None:
        _skip_or_block("AERO_BENCH_SUMO_RUNTIME_IMAGE must identify the tested image")
    if DIGEST_IMAGE_PATTERN.fullmatch(runtime_image) is None:
        _skip_or_block("AERO_BENCH_SUMO_RUNTIME_IMAGE must be digest-pinned")
    if runtime_image != image:
        _skip_or_block("AERO_BENCH_SUMO_RUNTIME_IMAGE must equal AERO_BENCH_SUMO_IMAGE")
    bundle = tmp_path / "bundle"
    artifacts = tmp_path / "artifacts"
    bundle.mkdir(mode=0o755)
    artifacts.mkdir(mode=0o777)
    artifacts.chmod(0o777)
    _write_scenario(bundle)
    provider_config = bundle / "provider.config.json"
    provider_config.write_bytes(b'{"schema_version":"aero-bench.sumo/v1"}\n')
    provider_schema = bundle / "provider.schema.json"
    provider_schema.write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    provider_config_ref = _ref(bundle, provider_config.name)
    provider_schema_ref = _ref(bundle, provider_schema.name)
    config_digest = provider_config_ref["sha256"]
    requirement = {
        "artifact_id": EVIDENCE_ARTIFACT_ID,
        "artifact_type": EVIDENCE_TYPE,
        "producer_id": "traffic",
        "visibility": "private",
        "relative_path": EVIDENCE_ARTIFACT_PATH,
        "max_size_bytes": EVIDENCE_MAX_SIZE_BYTES,
        "source_asset_id": None,
    }
    contract = {
        "schema_version": "aero-bench.workload-contract/v1",
        "role": "provider",
        "run_id": RUN_ID,
        "seed": 1701,
        "workload_id": "traffic",
        "clock": {
            "authority": "provider_barrier",
            "step_ns": STEP_NS,
            "max_steps": 2,
            "provider_timeout_ms": 10000,
        },
        "provider": {
            "provider_id": "traffic",
            "adapter": "sumo.traci",
            "port": 17434,
            "workload": {
                "runtime": {"image": runtime_image, "command": ["provider", "serve"]},
                "resources": {
                    "cpu_millicores": 1000,
                    "memory_mib": 1024,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": "sumo.traci",
                    "kind": "production",
                    "source_uri": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                    "source_revision": "462f5a8e8a651b266caec252c668cc9c781d169d",
                    "version": "0.2.0-sumo.1",
                },
            },
            "config": {"file": provider_config_ref, "schema_file": provider_schema_ref},
            "protocol_schema": provider_schema_ref,
            "capabilities": ["traffic.state"],
            "artifact_requirements": [requirement],
        },
        "assets": [],
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(
        json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n"
    )
    host_port = _port()
    container_id = subprocess.check_output(
        (
            "docker",
            "run",
            "--detach",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=256m",
            "--publish",
            f"127.0.0.1:{host_port}:17434",
            "--mount",
            f"type=bind,src={bundle},dst=/input/bundle,readonly",
            "--mount",
            f"type=bind,src={artifacts},dst=/input/artifacts",
            "--mount",
            f"type=bind,src={contract_path},dst=/input/contract.json,readonly",
            "--env",
            "AERO_BENCH_PROVIDER_BIND_HOST=0.0.0.0",
            "--env",
            "AERO_BENCH_PROVIDER_PORT=17434",
            "--env",
            f"AERO_BENCH_RUN_ID={RUN_ID}",
            "--env",
            "AERO_BENCH_WORKLOAD_ID=traffic",
            "--env",
            "AERO_BENCH_CONTRACT=/input/contract.json",
            "--env",
            "AERO_BENCH_BUNDLE_DIR=/input/bundle",
            "--env",
            "AERO_BENCH_ARTIFACT_DIR=/input/artifacts",
            "--env",
            f"AERO_BENCH_PROVIDER_TOKEN={SESSION_TOKEN}",
            image,
        ),
        text=True,
    ).strip()
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                probe = socket.create_connection(("127.0.0.1", host_port), timeout=2)
                probe.sendall(b'{"operation":"probe"}\n')
                if not probe.recv(4096):
                    raise OSError("SUMO service closed before its listener was ready")
                probe.close()
                connection = socket.create_connection(
                    ("127.0.0.1", host_port), timeout=2
                )
                break
            except OSError:
                if "probe" in locals():
                    probe.close()
                if time.monotonic() >= deadline:
                    logs = subprocess.run(
                        ("docker", "logs", container_id), capture_output=True, text=True
                    )
                    raise AssertionError(logs.stderr or logs.stdout)
                time.sleep(0.1)
        try:
            scenario_files = [
                _ref(bundle, path)
                for path in ("network.net.xml", "route.rou.xml", "scenario.sumocfg")
            ]
            payload = {
                "provider_id": "traffic",
                "run_id": RUN_ID,
                "protocol_version": PROTOCOL_VERSION,
                "session_token": SESSION_TOKEN,
                "runtime_image": runtime_image,
                "config_digest": config_digest,
                "artifact_requirements": [requirement],
                "sumo": {"version": SUMO_VERSION, "commit": SUMO_COMMIT},
                "sumo_binary": "sumo",
                "traci_port": 17534,
                "step_length_ns": STEP_NS,
                "scenario_config": scenario_files[2],
                "scenario_files": scenario_files,
                "sumo_args": ["--no-step-log", "true"],
                "required_commands": ["sumo"],
                "command_timeout_ms": 10000,
            }
            _rpc_error(
                connection,
                "prepare",
                {**payload, "session_token": "a" * 64},
                "principal.denied",
            )
            assert _rpc(connection, "prepare", payload)["status"] == "ready"
            assert _rpc(
                connection,
                "reset",
                {
                    "provider_id": "traffic",
                    "run_id": RUN_ID,
                    "seed": 1701,
                    "session_token": SESSION_TOKEN,
                },
            )
            for tick in (1, 2):
                response = _rpc(
                    connection,
                    "step_to",
                    {
                        "provider_id": "traffic",
                        "run_id": RUN_ID,
                        "session_token": SESSION_TOKEN,
                        "request": {
                            "run_id": RUN_ID,
                            "target": {"tick": tick, "sim_time_ns": tick * STEP_NS},
                        },
                    },
                )
                assert response["receipt"]["reached"] == {
                    "tick": tick,
                    "sim_time_ns": tick * STEP_NS,
                }
            assert _rpc(
                connection,
                "snapshot",
                {
                    "provider_id": "traffic",
                    "run_id": RUN_ID,
                    "session_token": SESSION_TOKEN,
                },
            )
            assert _rpc(
                connection,
                "shutdown",
                {
                    "provider_id": "traffic",
                    "run_id": RUN_ID,
                    "session_token": SESSION_TOKEN,
                },
            ) == {"status": "stopped"}
            connection.close()
        finally:
            connection.close()
        assert (
            subprocess.check_output(("docker", "wait", container_id), text=True).strip()
            == "0"
        )
        evidence = artifacts / EVIDENCE_ARTIFACT_PATH
        assert evidence.is_file()
        records = [json.loads(line) for line in evidence.read_text().splitlines()]
        assert len(records) == 4
        assert all(
            record["artifact_id"] == EVIDENCE_ARTIFACT_ID
            and record["artifact_type"] == EVIDENCE_TYPE
            for record in records
        )
    finally:
        subprocess.run(("docker", "rm", "-f", container_id), capture_output=True)
