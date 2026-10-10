from __future__ import annotations

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aero_bench.agent.driver_entrypoint import (
    BridgeHttpClient,
    BundleFileReader,
    DriverEntrypointError,
    _BridgeApi,
    _copy_codex_auth,
    run_from_environment,
)
from aero_bench.agent.session_contracts import (
    SessionPolicy,
    ToolCallRequest,
    ToolExecutionAudit,
    ToolExecutionResult,
    ToolTextContent,
    tool_visible_result_digest,
)
from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    SchemaBoundFile,
    Sha256,
    StrictModel,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from tests.agent.support import descriptor


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tool_result() -> ToolExecutionResult:
    content = (ToolTextContent(type="inputText", text='{"ready":true}'),)
    at = SimulationTime(tick=0, sim_time_ns=0)
    return ToolExecutionResult(
        schema_version="aero-bench.agent-tool-execution-result/v1",
        success=True,
        content=content,
        audit=ToolExecutionAudit(
            operation_id="operation.test",
            operation="flight.observe",
            command_id=None,
            query_id=None,
            observation_id=None,
            issued_at=at,
            completed_at=at,
            result_digest=tool_visible_result_digest(success=True, content=content),
            gateway_calls=(),
        ),
    )


class _Server(ThreadingHTTPServer):
    descriptor = descriptor()
    result = _tool_result()
    token = "a" * 64
    get_count = 0
    post_count = 0
    delay_first_get = False
    reject_post = False
    request_body: bytes | None = None


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *_: object) -> None:
        return

    def _send(self, status: int, value: object) -> None:
        body = canonical_json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        assert self.headers["Authorization"] == "Bearer " + self.server.token
        self.server.get_count += 1
        if self.server.delay_first_get and self.server.get_count == 1:
            self._send(503, {"error": "not.ready"})
            return
        self._send(200, self.server.descriptor.model_dump(mode="json"))

    def do_POST(self) -> None:
        assert self.headers["Authorization"] == "Bearer " + self.server.token
        self.server.post_count += 1
        size = int(self.headers["Content-Length"])
        self.server.request_body = self.rfile.read(size)
        if self.server.reject_post:
            self._send(503, {"error": "failed"})
            return
        self._send(200, self.server.result.model_dump(mode="json"))


class RunningServer:
    def __init__(self, *, delay_first_get: bool = False, reject_post: bool = False):
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.get_count = 0
        self.server.post_count = 0
        self.server.delay_first_get = delay_first_get
        self.server.reject_post = reject_post
        self.server.request_body = None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self.server

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_bundle_reader_digest_bounds_and_symlink_rejection(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    payload = canonical_json_bytes({"type": "object"})
    declared = root / "schema.json"
    declared.write_bytes(payload)
    reader = BundleFileReader(root)
    reference = FileRef(path="schema.json", sha256=_digest(payload))
    assert reader(reference) == payload
    with pytest.raises(DriverEntrypointError, match="digest"):
        reader(FileRef(path="schema.json", sha256="f" * 64))
    alias = root / "alias.json"
    alias.symlink_to(declared)
    with pytest.raises(DriverEntrypointError, match="unsafe"):
        reader(FileRef(path="alias.json", sha256=_digest(payload)))


def test_bridge_waits_only_for_startup_and_never_retries_tool_post() -> None:
    with RunningServer(delay_first_get=True) as server:
        client = BridgeHttpClient(
            host="127.0.0.1", port=server.server_port, token=server.token
        )
        assert "<redacted>" in repr(client)
        assert server.token not in repr(client)
        assert (
            client.wait_for_session(timeout_s=3, sleep=lambda _: None) == descriptor()
        )
        assert server.get_count == 2
        request = ToolCallRequest(
            call_id="call_1",
            name="flight.observe",
            arguments={"scope": "public"},
        )
        assert client.execute_tool(request, timeout_s=2) == _tool_result()
        assert server.post_count == 1
        assert json.loads(server.request_body) == request.model_dump(mode="json")
    with RunningServer(reject_post=True) as server:
        client = BridgeHttpClient(
            host="127.0.0.1", port=server.server_port, token=server.token
        )
        with pytest.raises(DriverEntrypointError, match="rejected"):
            client.execute_tool(request, timeout_s=2)
        assert server.post_count == 1


def test_codex_auth_copy_is_private_exact_and_error_never_contains_secret(
    tmp_path: Path,
) -> None:
    secret = "secret-that-must-never-be-logged"
    source = tmp_path / "source.json"
    source.write_bytes(canonical_json_bytes({"accessToken": secret}))
    destination = tmp_path / "codex-home"
    destination.mkdir()
    _copy_codex_auth(source, destination)
    copied = destination / "auth.json"
    assert copied.read_bytes() == source.read_bytes()
    assert copied.stat().st_mode & 0o777 == 0o600
    with pytest.raises(DriverEntrypointError) as captured:
        _copy_codex_auth(source, destination)
    assert secret not in str(captured.value)


class _Config(StrictModel):
    schema_version: str
    policy: SessionPolicy
    initial_input: str
    codex_cli_version: str
    codex_binary_sha256: Sha256


class _ContractModel:
    value: Any = None

    @classmethod
    def model_validate(cls, _: object) -> Any:
        return cls.value


def _write_declared(root: Path, relative: str, payload: bytes) -> FileRef:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FileRef(path=relative, sha256=_digest(payload))


def test_full_entrypoint_validates_environment_descriptor_and_private_scratch(
    tmp_path: Path,
) -> None:
    task = descriptor()
    input_root = tmp_path / "input"
    bundle = input_root / "bundle"
    artifacts = tmp_path / "artifacts"
    auth = input_root / "runtime-secrets" / "codex-auth.json"
    bundle.mkdir(parents=True)
    artifacts.mkdir()
    auth.parent.mkdir()
    auth.write_bytes(canonical_json_bytes({"accessToken": "private-value"}))
    instruction_ref = _write_declared(
        bundle, "instruction.md", task.instruction.encode()
    )
    binary = tmp_path / "codex"
    binary.write_bytes(b"pinned-native-binary")
    binary_digest = _digest(binary.read_bytes())
    config_document = {
        "schema_version": "aero-bench.agent-driver-config/v1",
        "policy": task.policy.model_dump(mode="json"),
        "initial_input": task.initial_input,
        "codex_cli_version": "0.153.4",
        "codex_binary_sha256": binary_digest,
    }
    config_ref = _write_declared(
        bundle, "configs/driver.json", canonical_json_bytes(config_document)
    )
    config_schema = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "policy": {"type": "object"},
            "initial_input": {"type": "string"},
            "codex_cli_version": {"type": "string"},
            "codex_binary_sha256": {"type": "string"},
        },
        "required": list(config_document),
        "additionalProperties": False,
    }
    schema_ref = _write_declared(
        bundle, "schemas/driver.json", canonical_json_bytes(config_schema)
    )
    driver = SimpleNamespace(
        driver_id="inspection.driver",
        bridge_port=17731,
        config=SchemaBoundFile(file=config_ref, schema_file=schema_ref),
        artifact_requirements=(
            ArtifactRequirement(
                artifact_id="artifact.model-manifest",
                artifact_type="model.session-manifest",
                producer_id="inspection.driver",
                visibility="private",
                relative_path="model.session-manifest.json",
                max_size_bytes=1024 * 1024,
                source_asset_id=None,
            ),
            ArtifactRequirement(
                artifact_id="artifact.model-interactions",
                artifact_type="model.interactions",
                producer_id="inspection.driver",
                visibility="private",
                relative_path="model.interactions.jsonl",
                max_size_bytes=64 * 1024 * 1024,
                source_asset_id=None,
            ),
        ),
    )
    context = SimpleNamespace(
        run_id=task.run_id,
        seed=7,
        task_id=task.task_id,
        instruction=instruction_ref,
        agent=SimpleNamespace(driver=driver, tools=(), queries=(), observations=()),
    )
    contract = SimpleNamespace(
        role="agent_driver",
        workload_id=driver.driver_id,
        context=context,
    )
    _ContractModel.value = contract
    contract_path = input_root / "contract.json"
    contract_path.write_bytes(canonical_json_bytes({"contract": "test"}) + b"\n")
    bridge_api = _BridgeApi(
        AgentDriverConfig=_Config,
        compile_tools=lambda _context, _reader: task.tools,
        build_descriptor=lambda _context, _attempt, _reader: task,
    )
    captured: dict[str, Any] = {}

    def runner(driver_context, actual_descriptor, client):
        captured["context"] = driver_context
        captured["descriptor"] = actual_descriptor
        captured["client"] = client
        copied = driver_context.codex_home / "auth.json"
        assert copied.read_bytes() == auth.read_bytes()
        assert driver_context.work_root != driver_context.codex_home
        return SimpleNamespace(status="completed")

    environment = {
        "AERO_BENCH_INPUT_DIR": os.fspath(input_root),
        "AERO_BENCH_CONTRACT": os.fspath(contract_path),
        "AERO_BENCH_BUNDLE_DIR": os.fspath(bundle),
        "AERO_BENCH_ARTIFACT_DIR": os.fspath(artifacts),
        "AERO_BENCH_CODEX_AUTH_FILE": os.fspath(auth),
        "AERO_BENCH_SEED": "7",
        "AERO_BENCH_ATTEMPT_ID": "attempt-1",
        "AERO_BENCH_ROLE": "agent_driver",
        "AERO_BENCH_RUN_ID": task.run_id,
        "AERO_BENCH_WORKLOAD_ID": driver.driver_id,
        "AERO_BENCH_SESSION_HOST": "127.0.0.1",
        "AERO_BENCH_SESSION_PORT": "17731",
        "AERO_BENCH_SESSION_TOKEN": "a" * 64,
    }
    with RunningServer() as server:
        server.descriptor = task
        environment["AERO_BENCH_SESSION_PORT"] = str(server.server_port)
        driver.bridge_port = server.server_port
        result = run_from_environment(
            environment,
            codex_binary=binary,
            bridge_api=bridge_api,
            contract_model=_ContractModel,
            driver_runner=runner,
        )
    assert result.status == "completed"
    assert captured["descriptor"] == task
    assert not captured["context"].codex_home.exists()
