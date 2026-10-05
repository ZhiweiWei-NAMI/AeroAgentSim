"""Real-container integration for the Inspection Business provider.

The test drives the actual digest-pinned image over TCP exactly like a formal
run: the shared readiness-probe identity frame, prepare binding, reset, step
barrier, commands, query, snapshot, the Docker HEALTHCHECK, shutdown, and the
declared private artifact. Every Docker command is bounded and runs through
the ``owned_container`` lifecycle helper, which guarantees cleanup and proves
no container residue survives — including when ``docker run`` itself fails.

The test requires ``AERO_BENCH_INSPECTION_BUSINESS_IMAGE`` and
``AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE``: the same non-placeholder
digest-pinned reference, so the tested image, the contract's declared runtime
image, and the image the HEALTHCHECK attests are one identity. Missing
resources skip the test locally but BLOCK the formal validation run; they
never silently pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from pathlib import Path

import pytest

from aero_bench.serialization import canonical_json_bytes
from tests.providers import container_lifecycle
from tests.test_inspection_business import PAYLOAD_DIGEST, _package


RUN_ID = "b" * 64
SEED = 1701
PROTOCOL_VERSION = "aero-bench.inspection-business-rpc/v1"
PROVIDER_ADAPTER = "inspection.business"
PROVIDER_PROBE_SCHEMA = "aero-bench.provider-probe/v1"
IMAGE_VERSION = "0.2.0-inspection-business.1"
SOURCE_REVISION = "b" * 40
SESSION_TOKEN = "9" * 64
CONTAINER_LABEL = "business"
DOCKER_TIMEOUT_SECONDS = 60.0
RUN_TIMEOUT_SECONDS = 120.0
LISTENER_DEADLINE_SECONDS = 30.0
HEALTH_DEADLINE_SECONDS = 90.0
EXIT_DEADLINE_SECONDS = 30.0
FORMAL_VALIDATION_ENV = "AERO_BENCH_INSPECTION_BUSINESS_FORMAL"
DIGEST_IMAGE_PATTERN = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


def _skip_or_block(reason: str) -> None:
    if os.environ.get(FORMAL_VALIDATION_ENV) == "1":
        pytest.fail(f"BLOCKED: {reason}")
    pytest.skip(reason)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode()


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
    connection.sendall(_canonical({"operation": operation, **payload}) + b"\n")
    response_bytes = b""
    while not response_bytes.endswith(b"\n"):
        chunk = connection.recv(65_536)
        if not chunk:
            raise AssertionError("business service closed the RPC connection")
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
) -> dict[str, object]:
    connection.sendall(_canonical({"operation": operation, **payload}) + b"\n")
    response_bytes = b""
    while not response_bytes.endswith(b"\n"):
        chunk = connection.recv(65_536)
        if not chunk:
            raise AssertionError("business service closed the RPC connection")
        response_bytes += chunk
    response = json.loads(response_bytes)
    assert "error" in response, response
    assert response["error"]["code"] == expected_code, response
    return response["error"]


def _authorized(payload: dict[str, object]) -> dict[str, object]:
    """The Harness session presents its granted token on every frame.

    The pinned token wins, mirroring the production session: a payload key
    can never replace the credential.
    """

    return {**payload, "session_token": SESSION_TOKEN}


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


def _listener_connection(container_name: str, host_port: int) -> socket.socket:
    """Connect once the service answers a side-effect-free probe."""

    deadline = time.monotonic() + LISTENER_DEADLINE_SECONDS
    while True:
        probe = None
        try:
            probe = socket.create_connection(("127.0.0.1", host_port), timeout=2)
            probe.sendall(b'{"operation":"probe"}\n')
            if not probe.recv(4096):
                raise OSError("service closed before its listener was ready")
            probe.close()
            return socket.create_connection(("127.0.0.1", host_port), timeout=2)
        except OSError:
            if probe is not None:
                probe.close()
            if time.monotonic() >= deadline:
                logs = container_lifecycle.docker(
                    "logs", container_name, timeout=DOCKER_TIMEOUT_SECONDS
                )
                raise AssertionError(
                    f"business service did not accept RPC within "
                    f"{LISTENER_DEADLINE_SECONDS:.0f}s: "
                    f"{logs.stderr or logs.stdout}"
                )
            time.sleep(0.1)


def _wait_until_healthy(container_name: str) -> None:
    """The shared readiness HEALTHCHECK must pass against the live service."""

    deadline = time.monotonic() + HEALTH_DEADLINE_SECONDS
    health = ""
    while time.monotonic() < deadline:
        health = container_lifecycle.docker(
            "inspect",
            "--format",
            "{{.State.Health.Status}}",
            container_name,
            timeout=DOCKER_TIMEOUT_SECONDS,
        ).stdout.strip()
        if health == "healthy":
            return
        time.sleep(1)
    raise AssertionError(
        f"business container health is {health!r} after "
        f"{HEALTH_DEADLINE_SECONDS:.0f}s; the shared readiness probe never "
        "accepted the serving identity"
    )


def test_inspection_business_container_lifecycle(tmp_path: Path) -> None:
    image = os.environ.get("AERO_BENCH_INSPECTION_BUSINESS_IMAGE")
    if image is None:
        _skip_or_block("AERO_BENCH_INSPECTION_BUSINESS_IMAGE must name the image")
    if DIGEST_IMAGE_PATTERN.fullmatch(image) is None:
        _skip_or_block("AERO_BENCH_INSPECTION_BUSINESS_IMAGE must be digest-pinned")
    try:
        container_lifecycle.docker("info", timeout=DOCKER_TIMEOUT_SECONDS)
    except (OSError, container_lifecycle.DockerCommandError):
        _skip_or_block("Docker daemon is unavailable")
    image_inspect = container_lifecycle.docker(
        "image",
        "inspect",
        "--format",
        "{{.Id}} {{json .Config}}",
        image,
        timeout=DOCKER_TIMEOUT_SECONDS,
        check=False,
    )
    if image_inspect.returncode != 0:
        _skip_or_block(f"business integration image is unavailable: {image}")
    image_id, _, config_json = image_inspect.stdout.strip().partition(" ")
    # The image declares no EXPOSE: the RPC port is materializer-owned.
    assert "ExposedPorts" not in json.loads(config_json)
    runtime_image = os.environ.get("AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE")
    if runtime_image is None:
        _skip_or_block(
            "AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE must identify the image"
        )
    if DIGEST_IMAGE_PATTERN.fullmatch(runtime_image) is None:
        _skip_or_block(
            "AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE must be digest-pinned"
        )
    if runtime_image != image:
        _skip_or_block(
            "AERO_BENCH_INSPECTION_BUSINESS_RUNTIME_IMAGE must equal "
            "AERO_BENCH_INSPECTION_BUSINESS_IMAGE"
        )
    bundle = tmp_path / "bundle"
    artifacts = tmp_path / "artifacts"
    bundle.mkdir(mode=0o755)
    artifacts.mkdir(mode=0o777)
    artifacts.chmod(0o777)
    config_document = {
        "schema_version": "aero-bench.inspection-business/v1",
        "provider_id": "business",
        "task_package": _package().model_dump(mode="json"),
    }
    (bundle / "provider.config.json").write_bytes(
        canonical_json_bytes(config_document) + b"\n"
    )
    (bundle / "provider.schema.json").write_bytes(
        b'{"$schema":"https://json-schema.org/draft/2020-12/schema"}\n'
    )
    config_ref = _ref(bundle, "provider.config.json")
    schema_ref = _ref(bundle, "provider.schema.json")
    config_digest = config_ref["sha256"]
    package = _package().model_dump(mode="json")
    package_digest = hashlib.sha256(canonical_json_bytes(package)).hexdigest()
    requirement = {
        "artifact_id": "artifact.business",
        "artifact_type": "business.state",
        "producer_id": "business",
        "visibility": "private",
        "relative_path": "business/state.json",
        "max_size_bytes": 1_048_576,
        "source_asset_id": None,
    }
    contract = {
        "schema_version": "aero-bench.workload-contract/v1",
        "role": "provider",
        "run_id": RUN_ID,
        "seed": SEED,
        "workload_id": "business",
        "clock": {
            "authority": "provider_barrier",
            "step_ns": 1_000_000_000,
            "max_steps": 8,
            "provider_timeout_ms": 10000,
        },
        "provider": {
            "provider_id": "business",
            "adapter": PROVIDER_ADAPTER,
            "port": 17435,
            "workload": {
                "runtime": {"image": runtime_image, "command": ["provider", "serve"]},
                "resources": {
                    "cpu_millicores": 1000,
                    "memory_mib": 1024,
                    "gpu_count": 0,
                },
                "implementation": {
                    "component_id": "inspection.business",
                    "kind": "production",
                    "source_uri": "https://github.com/ZhiweiWei-NAMI/AERO_BENCH",
                    "source_revision": SOURCE_REVISION,
                    "version": IMAGE_VERSION,
                },
            },
            "config": {"file": config_ref, "schema_file": schema_ref},
            "protocol_schema": schema_ref,
            "capabilities": ["business.work-order"],
            "artifact_requirements": [requirement],
        },
        "assets": [],
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_bytes(canonical_json_bytes(contract) + b"\n")
    host_port = _port()
    with container_lifecycle.owned_container(
        CONTAINER_LABEL,
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=256m",
        "--publish",
        f"127.0.0.1:{host_port}:17435",
        "--mount",
        f"type=bind,src={bundle},dst=/input/bundle,readonly",
        "--mount",
        f"type=bind,src={artifacts},dst=/input/artifacts",
        "--mount",
        f"type=bind,src={contract_path},dst=/input/contract.json,readonly",
        "--env",
        "AERO_BENCH_ROLE=provider",
        "--env",
        "AERO_BENCH_PROVIDER_BIND_HOST=0.0.0.0",
        "--env",
        "AERO_BENCH_PROVIDER_PORT=17435",
        "--env",
        f"AERO_BENCH_RUN_ID={RUN_ID}",
        "--env",
        "AERO_BENCH_WORKLOAD_ID=business",
        "--env",
        f"AERO_BENCH_SEED={SEED}",
        "--env",
        f"AERO_BENCH_PROVIDER_TOKEN={SESSION_TOKEN}",
        "--env",
        "AERO_BENCH_CONTRACT=/input/contract.json",
        "--env",
        "AERO_BENCH_BUNDLE_DIR=/input/bundle",
        "--env",
        "AERO_BENCH_ARTIFACT_DIR=/input/artifacts",
        image,
        run_timeout=RUN_TIMEOUT_SECONDS,
        command_timeout=DOCKER_TIMEOUT_SECONDS,
    ) as container_name:
        connection = _listener_connection(container_name, host_port)
        try:
            # The exact shared readiness identity frame: the in-container
            # HEALTHCHECK independently recomputes and requires it.
            probe = _rpc(connection, "probe", {})
            assert probe == {
                "schema_version": PROVIDER_PROBE_SCHEMA,
                "status": "accepting",
                "run_id": RUN_ID,
                "provider_id": "business",
                "adapter": PROVIDER_ADAPTER,
                "runtime_image": runtime_image,
                "config_digest": config_digest,
            }
            prepare = {
                "provider_id": "business",
                "run_id": RUN_ID,
                "protocol_version": PROTOCOL_VERSION,
                "runtime_image": runtime_image,
                "config_digest": config_digest,
                "artifact_requirements": [requirement],
                "session_token": SESSION_TOKEN,
                "task_id": "inspection.task",
                "package_digest": package_digest,
                "task_package": package,
            }
            # Race counterexample over the real container: a peer that
            # reaches the first prepare with a wrong executor-issued token
            # is denied and binds no state, so the legitimate session below
            # can still prepare the unprepared workload.
            with socket.create_connection(("127.0.0.1", host_port), timeout=5) as racer:
                _rpc_error(
                    racer,
                    "prepare",
                    {**prepare, "session_token": "a" * 64},
                    "principal.denied",
                )
            # Authentication precedes identity: even a frame that also
            # drifts public identity learns nothing beyond principal.denied.
            with socket.create_connection(("127.0.0.1", host_port), timeout=5) as racer:
                _rpc_error(
                    racer,
                    "prepare",
                    {
                        **prepare,
                        "session_token": "a" * 64,
                        "provider_id": "other.provider",
                    },
                    "principal.denied",
                )
            assert _rpc(connection, "prepare", prepare)["status"] == "ready"
            # The denied racer stays locked out of the bound session: even a
            # replayed prepare with the wrong token is still principal.denied.
            with socket.create_connection(("127.0.0.1", host_port), timeout=5) as racer:
                _rpc_error(
                    racer,
                    "prepare",
                    {**prepare, "session_token": "c" * 64},
                    "principal.denied",
                )
            assert _rpc(
                connection,
                "reset",
                _authorized(
                    {"provider_id": "business", "run_id": RUN_ID, "seed": SEED}
                ),
            )["receipt"]["reached"] == {"tick": 0, "sim_time_ns": 0}
            # Strict counterexample over the real container: a second
            # connection that replays every public identity field (provider,
            # run, a business-role actor, a valid time) but cannot present
            # the executor-issued session token forges no principal — with
            # no token and with a wrong token alike.
            with socket.create_connection(
                ("127.0.0.1", host_port), timeout=5
            ) as forger:
                forged = _command_payload(
                    "business.complete",
                    "command.forge",
                    1,
                    "business",
                    actor_id="business",
                    work_order_id="wo.1",
                )
                _rpc_error(forger, "command", forged, "principal.denied")
                _rpc_error(
                    forger,
                    "command",
                    {**forged, "session_token": "8" * 64},
                    "principal.denied",
                )
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
            arguments_by_tool = {
                "business.claim": {
                    "actor_id": "agent.1",
                    "work_order_id": "wo.1",
                },
                "business.start": {
                    "actor_id": "agent.1",
                    "work_order_id": "wo.1",
                },
                "business.observation_ready": {
                    "actor_id": "observation.provider",
                    "work_order_id": "wo.1",
                    "observation_id": "obs.1",
                },
                "business.submit": {
                    "actor_id": "agent.1",
                    "work_order_id": "wo.1",
                    "observation_id": "obs.1",
                    "report_payload_digest": PAYLOAD_DIGEST,
                },
                "business.complete": {
                    "actor_id": "business",
                    "work_order_id": "wo.1",
                },
            }
            for command_id, tick, agent_id, tool_id in lifecycle:
                assert _rpc(
                    connection,
                    "step_to",
                    _authorized(
                        {
                            "provider_id": "business",
                            "run_id": RUN_ID,
                            "request": {
                                "run_id": RUN_ID,
                                "target": {
                                    "tick": tick,
                                    "sim_time_ns": tick * 1_000_000_000,
                                },
                            },
                        }
                    ),
                )["receipt"]["reached"] == {
                    "tick": tick,
                    "sim_time_ns": tick * 1_000_000_000,
                }
                receipts = _rpc(
                    connection,
                    "command",
                    _authorized(
                        _command_payload(
                            tool_id,
                            command_id,
                            tick,
                            agent_id,
                            **arguments_by_tool[tool_id],
                        )
                    ),
                )["receipts"]
                assert [item["phase"] for item in receipts] == [
                    "received",
                    "accepted",
                    "applied",
                    "completed",
                ]
            query = _rpc(
                connection,
                "query",
                _authorized(
                    {
                        "provider_id": "business",
                        "run_id": RUN_ID,
                        "query": {
                            "run_id": RUN_ID,
                            "query_id": "query.1",
                            "kind": "work_order",
                            "work_order_id": "wo.1",
                            "issued_at": {"tick": 5, "sim_time_ns": 5_000_000_000},
                        },
                    }
                ),
            )["result"]
            assert query["work_orders"][0]["status"] == "completed"
            assert _rpc(
                connection,
                "snapshot",
                _authorized({"provider_id": "business", "run_id": RUN_ID}),
            )["snapshot_digest"]
            finalization = _rpc(
                connection,
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
                                "tick": 5,
                                "sim_time_ns": 5_000_000_000,
                            },
                            "event_chain_root": "d" * 64,
                        },
                    }
                ),
            )["receipt"]
            # The shared readiness HEALTHCHECK must observe the serving
            # identity before the run is shut down.
            _wait_until_healthy(container_name)
            assert _rpc(
                connection,
                "shutdown",
                _authorized({"provider_id": "business", "run_id": RUN_ID}),
            ) == {"status": "stopped"}
        finally:
            connection.close()
        exit_code = container_lifecycle.wait_for_exit(
            container_name, deadline_seconds=EXIT_DEADLINE_SECONDS
        )
        assert exit_code == 0, f"business container exited with {exit_code}"
        evidence = artifacts / "business/state.json"
        assert evidence.is_file()
        content = evidence.read_bytes()
        document = json.loads(content)
        assert finalization["event_chain_root"] == "d" * 64
        assert finalization["artifacts"] == [
            {
                "artifact_id": "artifact.business",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        ]
        history = document["history"]
        assert len(history) == 5
        assert document["business_history_root"] == history[-1]["record_hash"]
        assert document["state"]["work_orders"][0]["status"] == "completed"
        assert document["event_chain_root"] == "d" * 64
        assert "event_ledger" not in document
        previous_hash = "0" * 64
        for record in history:
            assert record["previous_hash"] == previous_hash
            previous_hash = record["record_hash"]
