from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest

from aero_bench.agent.codex_driver import (
    CodexDriverError,
    DriverContext,
    SessionLogWriter,
    interaction_log_from_jsonl_bytes,
    run_driver,
    validate_interaction_log,
)
from aero_bench.agent.session_contracts import (
    GatewayCallAudit,
    ToolCallRequest,
    ToolExecutionAudit,
    ToolExecutionResult,
    ToolImageContent,
    ToolTextContent,
    tool_visible_result_digest,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from tests.agent.support import descriptor


def _raw_request() -> bytes:
    task = descriptor()
    return canonical_json_bytes(
        {
            "model": "gpt-6-astra",
            "reasoning": {"effort": "xhigh"},
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": task.initial_input}],
                }
            ],
        }
    )


def _sse(output: list[dict], *, response_id: str) -> bytes:
    event = {
        "type": "response.completed",
        "response": {
            "id": response_id,
            "model": "gpt-6-astra",
            "output": output,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 0},
            },
        },
    }
    output_events = b"".join(
        b"data: "
        + canonical_json_bytes(
            {"type": "response.output_item.done", "output_index": index, "item": item}
        )
        + b"\n\n"
        for index, item in enumerate(output)
    )
    return (
        output_events
        + b"data: "
        + canonical_json_bytes(event)
        + b"\n\ndata: [DONE]\n\n"
    )


class Bridge:
    def __init__(self) -> None:
        self.requests: list[ToolCallRequest] = []

    def execute_tool(
        self, request: ToolCallRequest, *, timeout_s: int
    ) -> ToolExecutionResult:
        assert timeout_s > 0
        self.requests.append(request)
        at = SimulationTime(tick=2, sim_time_ns=4)
        content = (ToolTextContent(type="inputText", text='{"healthy":true}'),)
        return ToolExecutionResult(
            schema_version="aero-bench.agent-tool-execution-result/v1",
            success=True,
            content=content,
            audit=ToolExecutionAudit(
                operation_id="operation.observe-1",
                operation="flight.observe",
                command_id=None,
                query_id=None,
                observation_id="observation.flight-1",
                issued_at=at,
                completed_at=at,
                result_digest=tool_visible_result_digest(success=True, content=content),
                gateway_calls=(),
            ),
        )


class ImageBridge(Bridge):
    def execute_tool(
        self, request: ToolCallRequest, *, timeout_s: int
    ) -> ToolExecutionResult:
        self.requests.append(request)
        number = len(self.requests)
        png = b"\x89PNG\r\n\x1a\nimage-" + str(number).encode()
        image_digest = hashlib.sha256(png).hexdigest()
        at = SimulationTime(tick=number, sim_time_ns=number)
        request_json = canonical_json_bytes(
            {"observation_id": f"observation.image-{number}"}
        ).decode()
        response_json = canonical_json_bytes(
            {
                "frame_id": f"camera-frame-{number}",
                "image_sha256": image_digest,
                "payload_digest": str(number) * 64,
            }
        ).decode()
        gateway = GatewayCallAudit(
            operation="observation.get",
            request_json=request_json,
            request_digest=hashlib.sha256(request_json.encode()).hexdigest(),
            response_json=response_json,
            response_digest=hashlib.sha256(response_json.encode()).hexdigest(),
            issued_at=at,
            completed_at=at,
        )
        content = (
            ToolImageContent(
                type="inputImage",
                imageUrl="data:image/png;base64," + base64.b64encode(png).decode(),
            ),
        )
        return ToolExecutionResult(
            schema_version="aero-bench.agent-tool-execution-result/v1",
            success=True,
            content=content,
            audit=ToolExecutionAudit(
                operation_id=f"operation.image-{number}",
                operation="flight.observe",
                command_id=None,
                query_id=None,
                observation_id=f"observation.image-{number}",
                issued_at=at,
                completed_at=at,
                result_digest=tool_visible_result_digest(success=True, content=content),
                gateway_calls=(gateway,),
            ),
        )


class FakeAppServer:
    def __init__(self, proxy, *, fail: str | None = None) -> None:
        self.state = proxy.state
        self.fail = fail
        self.step = 0
        self.closed = False
        self.responses: list[ToolExecutionResult] = []

    def initialize(self, *, timeout_s: float) -> dict:
        return {"userAgent": "test"}

    def start_thread(self, task, *, timeout_s: float) -> dict:
        return {
            "model": "gpt-6-astra",
            "reasoningEffort": "xhigh",
            "modelProvider": "aero-bench-openai",
            "thread": {
                "id": "01a09420-f774-7880-8f1e-064d97cadfc6",
                "cliVersion": "0.153.4",
            },
        }

    def start_turn(
        self,
        *,
        thread_id: str,
        initial_input: str,
        timeout_s: float,
    ) -> dict:
        return {"turn": {"id": "01a09420-f7a1-7222-9581-03be9ea27a98"}}

    def next_event(self, *, timeout_s: float) -> dict:
        if self.fail == "timeout":
            raise TimeoutError
        if self.step == 0:
            self.state.rewrite_request(_raw_request())
            name = "functions.exec" if self.fail == "undeclared" else "flight.observe"
            self.state.validate_response(
                content_type="text/event-stream",
                body=_sse(
                    [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_U9g1iUT05a3bNaoInYwZstuv",
                            "name": name,
                            "arguments": '{"scope":"public"}',
                        }
                    ],
                    response_id="resp_1",
                ),
            )
            self.step = 1
            return {
                "id": 7,
                "method": "item/tool/call",
                "params": {
                    "callId": "call_U9g1iUT05a3bNaoInYwZstuv",
                    "tool": "flight.observe",
                    "arguments": {"scope": "public"},
                },
            }
        if self.step == 1:
            assert self.responses
            self.state.rewrite_request(_raw_request())
            self.state.validate_response(
                content_type="text/event-stream",
                body=_sse(
                    [
                        {
                            "type": "message",
                            "id": "msg_1",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {"type": "output_text", "text": "Mission complete."}
                            ],
                        }
                    ],
                    response_id="resp_2",
                ),
            )
            self.step = 2
            return {
                "method": "turn/completed",
                "params": {
                    "turn": {
                        "id": "01a09420-f7a1-7222-9581-03be9ea27a98",
                        "status": "completed",
                    }
                },
            }
        raise AssertionError("unexpected next_event")

    def respond_tool(self, request_id: Any, result: ToolExecutionResult) -> None:
        assert request_id == 7
        self.responses.append(result)

    def close(self) -> None:
        self.closed = True


class FakeTwoImageAppServer(FakeAppServer):
    def next_event(self, *, timeout_s: float) -> dict:
        if self.step == 0:
            return super().next_event(timeout_s=timeout_s)
        if self.step == 1:
            assert len(self.responses) == 1
            self.state.rewrite_request(_raw_request())
            self.state.validate_response(
                content_type="text/event-stream",
                body=_sse(
                    [
                        {
                            "type": "function_call",
                            "id": "fc_2",
                            "call_id": "call_image_2",
                            "name": "flight.observe",
                            "arguments": '{"scope":"public"}',
                        }
                    ],
                    response_id="resp_2",
                ),
            )
            self.step = 2
            return {
                "id": 8,
                "method": "item/tool/call",
                "params": {
                    "callId": "call_image_2",
                    "tool": "flight.observe",
                    "arguments": {"scope": "public"},
                },
            }
        raise AssertionError("driver must reject the second image before continuation")


def _context(tmp_path: Path) -> DriverContext:
    artifacts = tmp_path / "artifacts"
    work = tmp_path / "work"
    codex_home = tmp_path / "codex-home"
    artifacts.mkdir()
    work.mkdir()
    codex_home.mkdir()
    binary = tmp_path / "codex"
    binary.write_bytes(b"pinned test binary")
    return DriverContext(
        run_id="1" * 64,
        attempt_id="attempt-1",
        private_artifact_root=artifacts,
        work_root=work,
        codex_binary=binary,
        codex_home=codex_home,
        codex_cli_version="0.153.4",
        codex_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
    )


def test_run_driver_seals_complete_causal_session(tmp_path: Path) -> None:
    context = _context(tmp_path)
    bridge = Bridge()
    clients: list[FakeAppServer] = []

    def factory(_context, proxy):
        client = FakeAppServer(proxy)
        clients.append(client)
        return client

    manifest = run_driver(
        context,
        descriptor(),
        bridge,
        app_server_factory=factory,
    )
    assert manifest.status == "completed"
    assert manifest.model == "gpt-6-astra"
    assert manifest.reasoning_effort == "xhigh"
    assert manifest.usage.model_requests == 2
    assert manifest.usage.tool_calls == 1
    assert manifest.usage.image_observations == 0
    assert manifest.usage.total_tokens == 16
    assert clients[0].closed
    assert [request.name for request in bridge.requests] == ["flight.observe"]
    interactions = context.private_artifact_root / "model.interactions.jsonl"
    records = validate_interaction_log(
        interactions,
        expected_run_id=context.run_id,
        expected_attempt_id=context.attempt_id,
    )
    assert (
        interaction_log_from_jsonl_bytes(
            interactions.read_bytes(),
            expected_run_id=context.run_id,
            expected_attempt_id=context.attempt_id,
        )
        == records
    )
    assert [record.record_type for record in records] == [
        "model.request",
        "model.response",
        "tool.call",
        "tool.result",
        "model.request",
        "model.response",
        "session.complete",
    ]
    assert (
        hashlib.sha256(interactions.read_bytes()).hexdigest()
        == manifest.interactions_sha256
    )
    assert (context.private_artifact_root / "model.session-manifest.json").is_file()


@pytest.mark.parametrize("failure", ["timeout", "undeclared"])
def test_run_driver_preserves_failed_session_without_executing_tool(
    tmp_path: Path, failure: str
) -> None:
    context = _context(tmp_path)
    bridge = Bridge()

    def factory(_context, proxy):
        return FakeAppServer(proxy, fail=failure)

    manifest = run_driver(
        context,
        descriptor(),
        bridge,
        app_server_factory=factory,
    )
    assert manifest.status == "failed"
    assert manifest.failure_code in {
        "driver.session.timeout",
        "driver.model_proxy.failed",
    }
    assert bridge.requests == []
    records = validate_interaction_log(
        context.private_artifact_root / "model.interactions.jsonl"
    )
    assert records[-1].record_type == "session.failed"


def test_driver_independently_rejects_image_observation_over_budget(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    bridge = ImageBridge()
    task = descriptor()
    task = task.model_copy(
        update={"policy": task.policy.model_copy(update={"max_image_observations": 1})}
    )

    def factory(_context, proxy):
        return FakeTwoImageAppServer(proxy)

    manifest = run_driver(
        context,
        task,
        bridge,
        app_server_factory=factory,
    )
    assert manifest.status == "failed"
    assert manifest.failure_code == "driver.image_budget.exhausted"
    assert manifest.usage.image_observations == 2
    assert len(bridge.requests) == 2
    records = validate_interaction_log(
        context.private_artifact_root / "model.interactions.jsonl"
    )
    assert [record.record_type for record in records[-2:]] == [
        "tool.call",
        "session.failed",
    ]


def test_log_validator_rejects_missing_tool_result_causality(tmp_path: Path) -> None:
    task = descriptor()
    root = tmp_path / "log"
    root.mkdir()
    writer = SessionLogWriter(
        root=root,
        descriptor=task,
        session_id="session_test",
        maximum_bytes=1024 * 1024,
        wall_time_ns=lambda: 10,
    )
    turn_id = "turn_1"
    arguments = {"scope": "public"}
    arguments_digest = hashlib.sha256(canonical_json_bytes(arguments)).hexdigest()
    writer.append("model.request", {"request_index": 1}, turn_id=turn_id)
    writer.append(
        "model.response",
        {
            "function_calls": [
                {
                    "call_id": "call_1",
                    "name": "flight.observe",
                    "arguments": arguments,
                    "arguments_digest": arguments_digest,
                }
            ]
        },
        turn_id=turn_id,
    )
    writer.append(
        "tool.call",
        {
            "name": "flight.observe",
            "arguments": arguments,
            "arguments_digest": arguments_digest,
        },
        turn_id=turn_id,
        call_id="call_1",
    )
    writer.append("session.complete", {"tool_calls": 1})
    writer.close()
    with pytest.raises(CodexDriverError, match="pending tool calls"):
        validate_interaction_log(root / "model.interactions.jsonl")
