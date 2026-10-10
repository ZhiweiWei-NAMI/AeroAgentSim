from __future__ import annotations

import base64
import hashlib
import http.client
import json
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from aero_bench.agent.bridge import BridgeHttpServer, SessionBridge, compile_tools
from aero_bench.agent.runtime import AgentContext, GatewayClient, GatewayRequestError
from aero_bench.agent.session_contracts import (
    SessionDescriptor,
    SessionPolicy,
    ToolCallRequest,
    text_digest,
    tool_catalog_digest,
)
from aero_bench.config.models import (
    ArtifactRequirement,
    FileRef,
    ObservationGrant,
    QueryGrant,
    ToolGrant,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _Dump:
    def __init__(self, value: dict[str, Any], **attributes: Any):
        self.value = value
        for name, item in attributes.items():
            setattr(self, name, item)

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return self.value


class _Gateway:
    instances: list["_Gateway"] = []

    def __init__(self, context: Any, *, audit: Any = None):
        self.context = context
        self.audit = audit
        self.operations: list[tuple[str, dict[str, Any]]] = []
        self.observe_calls = 0
        self.status_calls = 0
        self.complete_calls = 0
        self.fail_next_observe = False
        self.terminal_after = 3
        self.image = b"\x89PNG\r\n\x1a\nreal-camera-frame"
        self.__class__.instances.append(self)

    def connect(self) -> None:
        return

    def close(self) -> None:
        return

    def probe(self) -> dict[str, Any]:
        value = {
            "status": "ready",
            "run_id": self.context.contract.run_id,
            "current": {"tick": 0, "sim_time_ns": 0},
        }
        self._record("probe", {}, value)
        return value

    def command(self, **kwargs: Any) -> _Dump:
        self.operations.append(("command.invoke", kwargs))
        value = {
            "command_id": kwargs["command_id"],
            "receipts": [],
            "response": [],
            "observation": None,
        }
        self._record("command.invoke", kwargs, value)
        return _Dump(value)

    def query(self, **kwargs: Any) -> _Dump:
        self.operations.append(("query.invoke", kwargs))
        value = {"query_id": kwargs["query_id"], "response": []}
        self._record("query.invoke", kwargs, value)
        return _Dump(value)

    def observe(self, **kwargs: Any) -> _Dump:
        self.observe_calls += 1
        self.operations.append(("observation.get", kwargs))
        if self.fail_next_observe:
            self.fail_next_observe = False
            raise GatewayRequestError(
                "observation.unavailable", "camera frame unavailable"
            )
        encoded = base64.b64encode(self.image).decode("ascii")
        value = {
            "run_id": self.context.contract.run_id,
            "agent_id": self.context.agent_id,
            "observation_id": kwargs["observation_id"],
            "time": kwargs["at"].model_dump(mode="json"),
            "payload_schema": {
                "path": "schemas/camera.json",
                "sha256": "8" * 64,
            },
            "payload": [
                {"name": "frame_id", "value": f"camera-frame-{self.observe_calls}"},
                {"name": "image_sha256", "value": _digest(self.image)},
                {"name": "image_base64", "value": encoded},
            ],
            "payload_digest": "9" * 64,
        }
        self._record("observation.get", kwargs, value)
        return _Dump(value)

    def command_status(self, **kwargs: Any) -> _Dump:
        self.status_calls += 1
        phase = "completed" if self.status_calls >= self.terminal_after else "pending"
        value = {
            "command_id": kwargs["command_id"],
            "phase": phase,
            "time": kwargs["at"].model_dump(mode="json"),
        }
        self.operations.append(("command.status", kwargs))
        self._record("command.status", kwargs, value)
        return _Dump(value, phase=phase)

    def decision_summary(self, **kwargs: Any) -> _Dump:
        value = {"summary_id": kwargs["summary_id"]}
        self._record("decision.summary", kwargs, value)
        return _Dump(value)

    def complete_turn(self, **kwargs: Any) -> _Dump:
        self.complete_calls += 1
        at = SimulationTime(
            tick=kwargs["at"].tick + 1,
            sim_time_ns=kwargs["at"].sim_time_ns + 500_000_000,
        )
        status = "terminated" if kwargs["disposition"] == "finished" else "advanced"
        value = {
            "run_id": self.context.contract.run_id,
            "status": status,
            "at": at.model_dump(mode="json"),
            "active_agent_ids": [],
            "missing_agent_ids": [],
        }
        self.operations.append(("turn.complete", kwargs))
        self._record("turn.complete", kwargs, value)
        return _Dump(value, status=status, at=at)

    def _record(
        self, operation: str, request: dict[str, Any], response: dict[str, Any]
    ) -> None:
        if self.audit is not None:

            def jsonable(value: Any) -> Any:
                if hasattr(value, "model_dump"):
                    return value.model_dump(mode="json")
                if isinstance(value, dict):
                    return {key: jsonable(item) for key, item in value.items()}
                if isinstance(value, (list, tuple)):
                    return [jsonable(item) for item in value]
                return value

            self.audit(
                operation,
                jsonable({"operation": operation, **request}),
                jsonable(response),
            )


class _Context:
    def __init__(self, contract: Any, files: dict[str, bytes]):
        self.contract = contract
        self.agent_id = contract.agent.agent_id
        self.files = files
        self.writes: dict[str, bytes] = {}

    def _read_bundle_file(self, path: str, digest: str) -> bytes:
        payload = self.files[path]
        assert _digest(payload) == digest
        return payload

    def write_artifact(self, artifact_id: str, payload: bytes) -> None:
        if artifact_id in self.writes:
            raise AssertionError("context must never overwrite an artifact")
        self.writes[artifact_id] = payload

    def asset_bytes(self, asset_id: str) -> bytes:
        assert asset_id == "asset.instructions"
        return b'{"public":true}'


def _schema(files: dict[str, bytes], name: str, properties: dict[str, Any]) -> FileRef:
    value = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    payload = canonical_json_bytes(value)
    path = f"schemas/{name}.json"
    files[path] = payload
    return FileRef(path=path, sha256=_digest(payload))


def _fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SessionBridge, _Context, _Gateway]:
    files: dict[str, bytes] = {}
    actor = {"type": "string", "minLength": 1}
    identifier = {"type": "string", "minLength": 1, "maxLength": 128}
    action_schema = _schema(
        files,
        "business-start",
        {"actor_id": actor, "work_order_id": identifier},
    )
    network_schema = _schema(
        files,
        "network-send",
        {
            "actor_id": actor,
            "payload_base64": {"type": "string"},
            "payload_sha256": {"type": "string"},
        },
    )
    query_schema = _schema(
        files,
        "business-query",
        {"actor_id": actor, "work_order_id": identifier},
    )
    response_schema = _schema(files, "response", {})
    camera_schema = _schema(
        files,
        "camera",
        {
            "frame_id": identifier,
            "image_sha256": {"type": "string"},
            "image_base64": {"type": "string"},
        },
    )
    artifact = ArtifactRequirement(
        artifact_id="artifact.report",
        artifact_type="inspection.report",
        producer_id="participant.agent",
        visibility="public",
        relative_path="agent/report.json",
        max_size_bytes=1024 * 1024,
        source_asset_id=None,
    )
    agent = SimpleNamespace(
        agent_id="participant.agent",
        tools=(
            ToolGrant(
                tool_id="business.start",
                provider_id="business",
                request_schema=action_schema,
                response_schema=response_schema,
                timeout_ms=180_000,
                idempotent=False,
            ),
            ToolGrant(
                tool_id="network.send",
                provider_id="network",
                request_schema=network_schema,
                response_schema=response_schema,
                timeout_ms=180_000,
                idempotent=False,
            ),
        ),
        queries=(
            QueryGrant(
                query_type="business.work-order",
                provider_id="business",
                request_schema=query_schema,
                response_schema=response_schema,
                timeout_ms=180_000,
            ),
        ),
        observations=(
            ObservationGrant(
                observation_id="observation.camera",
                provider_id="flight",
                schema_file=camera_schema,
                timeout_ms=180_000,
            ),
        ),
        artifact_requirements=(artifact,),
    )
    contract = SimpleNamespace(
        run_id="1" * 64,
        task_id="inspection.task",
        agent=agent,
        clock=SimpleNamespace(step_ns=500_000_000, max_steps=100),
        scenario_assets=(SimpleNamespace(asset_id="asset.instructions"),),
    )
    context = _Context(contract, files)
    monkeypatch.setattr("aero_bench.agent.bridge.GatewayClient", _Gateway)
    tools = compile_tools(
        contract, lambda ref: context._read_bundle_file(ref.path, ref.sha256)
    )
    instruction = "Complete the inspection through declared functions."
    policy = SessionPolicy(
        schema_version="aero-bench.agent-session-policy/v1",
        model="gpt-6-astra",
        reasoning_effort="xhigh",
        max_model_requests=64,
        max_tool_calls=96,
        max_image_observations=12,
        model_call_timeout_s=180,
        session_wall_timeout_s=13_200,
        max_output_tokens=8192,
    )
    descriptor = SessionDescriptor(
        schema_version="aero-bench.agent-session/v1",
        run_id=contract.run_id,
        attempt_id="attempt.test",
        task_id=contract.task_id,
        agent_id=agent.agent_id,
        driver_id="astra.driver",
        instruction=instruction,
        instruction_sha256=text_digest(instruction),
        initial_input="Execute the task.",
        initial_input_sha256=text_digest("Execute the task."),
        policy=policy,
        tools=tools,
        tools_digest=tool_catalog_digest(tools),
    )
    bridge = SessionBridge(context, descriptor)
    return bridge, context, _Gateway.instances[-1]


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(call_id=call_id, name=name, arguments=arguments)


def test_catalog_is_exactly_compiled_from_grants_and_hides_injected_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _, _ = _fixture(monkeypatch)
    names = tuple(tool.name for tool in bridge.descriptor.tools)
    assert names == tuple(sorted(names))
    assert names == (
        "action_business_start",
        "action_network_send",
        "finish",
        "get_command_status",
        "get_run_status",
        "query_business_work_order",
        "read_observation_camera",
        "read_public_asset",
        "record_decision_summary",
        "wait_duration",
        "wait_for_command",
        "write_json_artifact",
    )
    business = bridge.descriptor.tool_map["action_business_start"].parameters
    assert "actor_id" not in business["properties"]
    network = bridge.descriptor.tool_map["action_network_send"].parameters
    assert set(network["properties"]) == {"artifact_id", "decision_summary"}
    assert network["properties"]["artifact_id"]["enum"] == ["artifact.report"]


def test_actor_is_injected_and_network_sends_exact_owned_artifact_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, context, gateway = _fixture(monkeypatch)
    started = bridge.execute_tool(
        _call(
            "call_start",
            "action_business_start",
            {
                "work_order_id": "order.1",
                "decision_summary": "Start the selected order.",
            },
        ),
        timeout_s=180,
    )
    assert started.success
    operation, invocation = gateway.operations[-1]
    assert operation == "command.invoke"
    assert invocation["arguments"] == {
        "actor_id": "participant.agent",
        "work_order_id": "order.1",
    }
    content_json = '{"reports":[{"status":"ok"}]}'
    written = bridge.execute_tool(
        _call(
            "call_write",
            "write_json_artifact",
            {"artifact_id": "artifact.report", "content_json": content_json},
        ),
        timeout_s=180,
    )
    assert written.success
    owned = canonical_json_bytes(json.loads(content_json))
    assert context.writes == {"artifact.report": owned}
    sent = bridge.execute_tool(
        _call(
            "call_send",
            "action_network_send",
            {
                "artifact_id": "artifact.report",
                "decision_summary": "Transmit the completed report.",
            },
        ),
        timeout_s=180,
    )
    assert sent.success
    _, invocation = gateway.operations[-1]
    assert invocation["arguments"] == {
        "actor_id": "participant.agent",
        "payload_base64": base64.b64encode(owned).decode("ascii"),
        "payload_sha256": _digest(owned),
    }


def test_wait_advances_every_tick_through_full_turn_completion_without_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _, gateway = _fixture(monkeypatch)
    bridge._all_commands.add("call.known")
    result = bridge.execute_tool(
        _call(
            "call_wait",
            "wait_for_command",
            {
                "command_id": "call.known",
                "timeout_sim_seconds": 10.0,
                "timeout_wall_seconds": 10,
            },
        ),
        timeout_s=10,
    )
    assert result.success
    assert gateway.status_calls == 3
    assert gateway.complete_calls == 2
    assert [name for name, _ in gateway.operations] == [
        "command.status",
        "turn.complete",
        "command.status",
        "turn.complete",
        "command.status",
    ]
    assert all(name != "command.invoke" for name, _ in gateway.operations)
    assert len(result.audit.gateway_calls) == 5
    with pytest.raises(ValueError, match="already executed"):
        bridge.execute_tool(
            _call(
                "call_wait",
                "wait_for_command",
                {
                    "command_id": "call.known",
                    "timeout_sim_seconds": 10.0,
                    "timeout_wall_seconds": 10,
                },
            ),
            timeout_s=10,
        )
    assert gateway.status_calls == 3


def test_rgb_returns_exact_gateway_provenance_and_cap_blocks_thirteenth_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _, gateway = _fixture(monkeypatch)
    results = [
        bridge.execute_tool(
            _call(f"call_image_{index}", "read_observation_camera", {}),
            timeout_s=180,
        )
        for index in range(1, 14)
    ]
    assert all(result.success for result in results[:12])
    assert not results[12].success
    assert gateway.observe_calls == 12
    first = results[0]
    image = next(item for item in first.content if item.type == "inputImage")
    assert image.image_bytes == gateway.image
    assert first.image_provenance() == {
        "frame_id": "camera-frame-1",
        "image_sha256": _digest(gateway.image),
        "observation_payload_digest": "9" * 64,
        "gateway_response_digest": first.audit.gateway_calls[0].response_digest,
    }
    assert first.audit.observation_id == "observation.camera"


def test_failed_rgb_observation_does_not_consume_success_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _, gateway = _fixture(monkeypatch)
    bridge.descriptor = bridge.descriptor.model_copy(
        update={
            "policy": bridge.descriptor.policy.model_copy(
                update={"max_image_observations": 1}
            )
        }
    )
    gateway.fail_next_observe = True
    failed = bridge.execute_tool(
        _call("call_image_failed", "read_observation_camera", {}), timeout_s=180
    )
    assert not failed.success
    successful = bridge.execute_tool(
        _call("call_image_success", "read_observation_camera", {}), timeout_s=180
    )
    assert successful.success
    assert gateway.observe_calls == 2


def test_artifacts_are_strict_immutable_and_required_before_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, context, gateway = _fixture(monkeypatch)
    duplicate = bridge.execute_tool(
        _call(
            "call_duplicate",
            "write_json_artifact",
            {"artifact_id": "artifact.report", "content_json": '{"x":1,"x":2}'},
        ),
        timeout_s=180,
    )
    assert not duplicate.success
    assert context.writes == {}
    unfinished = bridge.execute_tool(
        _call("call_finish_early", "finish", {}), timeout_s=180
    )
    assert not unfinished.success
    assert gateway.complete_calls == 0
    valid = bridge.execute_tool(
        _call(
            "call_write",
            "write_json_artifact",
            {"artifact_id": "artifact.report", "content_json": '{"x":1}'},
        ),
        timeout_s=180,
    )
    assert valid.success
    immutable = bridge.execute_tool(
        _call(
            "call_overwrite",
            "write_json_artifact",
            {"artifact_id": "artifact.report", "content_json": '{"x":2}'},
        ),
        timeout_s=180,
    )
    assert not immutable.success
    assert context.writes["artifact.report"] == b'{"x":1}'
    finished = bridge.execute_tool(_call("call_finish", "finish", {}), timeout_s=180)
    assert finished.success
    assert bridge.finished
    assert gateway.complete_calls == 1


def test_http_bearer_is_required_before_session_or_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge, _, _ = _fixture(monkeypatch)
    calls = 0
    original = bridge.execute_tool

    def counted(request: ToolCallRequest, *, timeout_s: int):
        nonlocal calls
        calls += 1
        return original(request, timeout_s=timeout_s)

    bridge.execute_tool = counted  # type: ignore[method-assign]
    token = "a" * 64
    server = BridgeHttpServer(("127.0.0.1", 0), bridge, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2
        )
        connection.request("GET", "/session")
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()
        body = canonical_json_bytes(
            _call("call_unauthorized", "get_run_status", {}).model_dump(mode="json")
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2
        )
        connection.request(
            "POST",
            "/tool",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()
        assert calls == 0
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=2
        )
        connection.request(
            "GET", "/session", headers={"Authorization": "Bearer " + token}
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == bridge.descriptor.model_dump(mode="json")
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_gateway_sdk_audit_has_exact_raw_frame_without_transport_token() -> None:
    secret = "b" * 64
    response = canonical_json_bytes({"ready": True}) + b"\n"

    class Socket:
        def __init__(self) -> None:
            self.sent = b""
            self.received = False

        def sendall(self, value: bytes) -> None:
            self.sent += value

        def recv(self, _: int) -> bytes:
            if self.received:
                return b""
            self.received = True
            return response

        def close(self) -> None:
            return

    context = object.__new__(AgentContext)
    audited: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    client = GatewayClient(context, audit=lambda *args: audited.append(args))
    socket = Socket()
    client._connection = socket  # type: ignore[assignment]
    assert client._request("query.invoke", {"token": secret, "query": {"id": 1}}) == {
        "ready": True
    }
    assert secret.encode() in socket.sent
    assert audited == [
        (
            "query.invoke",
            {"operation": "query.invoke", "query": {"id": 1}},
            {"ready": True},
        )
    ]
    assert secret not in canonical_json_bytes(audited).decode()
