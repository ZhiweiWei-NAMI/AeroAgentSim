from __future__ import annotations

import json
import queue
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aero_bench.agent.codex_driver import (
    CodexDriverError,
    DriverContext,
    NativeCodexAppServerClient,
    run_driver,
)
from aero_bench.agent.model_proxy import (
    ModelProxyViolation,
    ProxySessionState,
    RestrictedModelProxyServer,
)
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    SessionPolicy,
    SessionUsage,
)
from aero_bench.serialization import canonical_json_bytes
from tests.agent.support import descriptor
from tests.agent.test_codex_driver import (
    Bridge,
    FakeAppServer,
    _context,
    _raw_request,
    _sse,
)


def _policy_schema() -> dict[str, Any]:
    return SessionPolicy.model_json_schema()["properties"]["reasoning_effort"]


def _manifest(**overrides: Any) -> AgentSessionManifest:
    task = overrides.pop("task", descriptor())
    payload = {
        "schema_version": "aero-bench.agent-session-manifest/v1",
        "run_id": task.run_id,
        "attempt_id": task.attempt_id,
        "session_id": "session_test",
        "task_id": task.task_id,
        "agent_id": task.agent_id,
        "driver_id": task.driver_id,
        "status": "failed",
        "model": task.policy.model,
        "reasoning_effort": task.policy.reasoning_effort,
        "codex_cli_version": "0.153.4",
        "codex_binary_sha256": "4" * 64,
        "policy": task.policy,
        "instruction_sha256": task.instruction_sha256,
        "initial_input_sha256": task.initial_input_sha256,
        "tools_digest": task.tools_digest,
        "started_wall_time_ns": 1,
        "completed_wall_time_ns": 2,
        "terminal_turn_id": None,
        "failure_code": "driver.descriptor.invalid",
        "failure_detail": "policy rejected",
        "usage": SessionUsage(
            model_requests=0,
            tool_calls=0,
            image_observations=0,
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        ),
        "interactions_sha256": "5" * 64,
        "interactions_size_bytes": 1,
        "log_record_count": 1,
        "log_chain_root": "6" * 64,
    }
    payload.update(overrides)
    return AgentSessionManifest.model_validate(payload)


class _LinePipe:
    def __init__(self) -> None:
        self._lines: queue.Queue[bytes] = queue.Queue()

    def readline(self, limit: int = -1) -> bytes:
        return self._lines.get()

    def put(self, line: bytes) -> None:
        self._lines.put(line)

    def close(self) -> None:
        self._lines.put(b"")


class _RecordingStdin:
    def __init__(self, process: "RecordingAppServerProcess") -> None:
        self._process = process

    def write(self, data: bytes) -> int:
        self._process.writes.append(data)
        message = json.loads(data)
        if "id" in message:
            self._process.stdout.put(
                canonical_json_bytes(
                    {"id": message["id"], "result": self._process.result_for(message)}
                )
                + b"\n"
            )
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


class RecordingAppServerProcess:
    """In-process stand-in for the pinned Codex app-server subprocess."""

    instances: list["RecordingAppServerProcess"] = []

    def __init__(self, argv: list[str], **_: Any) -> None:
        self.argv = list(argv)
        self.writes: list[bytes] = []
        self.stdin = _RecordingStdin(self)
        self.stdout = _LinePipe()
        self.returncode: int | None = None
        self.instances.append(self)

    def result_for(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method == "initialize":
            return {"userAgent": "test"}
        if method == "thread/start":
            return {
                "model": "gpt-6-astra",
                "reasoningEffort": self.configured_effort(),
                "modelProvider": "aero-bench-openai",
                "thread": {
                    "id": "01a09420-f774-7880-8f1e-064d97cadfc6",
                    "cliVersion": "0.153.4",
                },
            }
        if method == "turn/start":
            return {"turn": {"id": "01a09420-f7a1-7222-9581-03be9ea27a98"}}
        return {"ok": True}

    def configured_effort(self) -> str:
        for index, item in enumerate(self.argv):
            if item == "-c" and index + 1 < len(self.argv):
                value = self.argv[index + 1]
                if value.startswith("model_reasoning_effort="):
                    return value.split("=", 1)[1].strip('"')
        raise AssertionError("app-server argv is missing model_reasoning_effort")

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


class PolicyAwareAppServer(FakeAppServer):
    def start_thread(self, task, *, timeout_s: float) -> dict:
        return {
            "model": task.policy.model,
            "reasoningEffort": task.policy.reasoning_effort,
            "modelProvider": "aero-bench-openai",
            "thread": {
                "id": "01a09420-f774-7880-8f1e-064d97cadfc6",
                "cliVersion": "0.153.4",
            },
        }

    def next_event(self, *, timeout_s: float) -> dict:
        effort = self.state.descriptor.policy.reasoning_effort
        if self.step == 0:
            rebuilt = json.loads(
                self.state.rewrite_request(_raw_request_with_effort(effort))
            )
            self.rebuilt_requests.append(rebuilt)
            self.state.validate_response(
                content_type="text/event-stream",
                body=_sse(
                    [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call_U9g1iUT05a3bNaoInYwZstuv",
                            "name": "flight.observe",
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
            rebuilt = json.loads(
                self.state.rewrite_request(_raw_request_with_effort(effort))
            )
            self.rebuilt_requests.append(rebuilt)
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

    def __init__(self, proxy, *, fail: str | None = None) -> None:
        super().__init__(proxy, fail=fail)
        self.rebuilt_requests: list[dict[str, Any]] = []


class MismatchedEffortAppServer(PolicyAwareAppServer):
    def start_thread(self, task, *, timeout_s: float) -> dict:
        reported = "xhigh" if task.policy.reasoning_effort == "low" else "low"
        result = super().start_thread(task, timeout_s=timeout_s)
        result["reasoningEffort"] = reported
        return result


def _raw_request_with_effort(effort: str) -> bytes:
    task = descriptor()
    payload = json.loads(_raw_request())
    payload["reasoning"] = {"effort": effort}
    payload["input"] = [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": task.initial_input}],
        }
    ]
    return canonical_json_bytes(payload)


def test_policy_accepts_declared_low_and_historical_xhigh_only() -> None:
    schema = _policy_schema()
    assert schema.get("enum") == ["low", "xhigh"]
    assert "const" not in schema
    for effort in ("low", "xhigh"):
        policy = descriptor(reasoning_effort=effort).policy
        assert policy.model == "gpt-6-astra"
        assert policy.reasoning_effort == effort
        restored = SessionPolicy.model_validate(policy.model_dump(mode="json"))
        assert restored == policy
    for invalid in ("medium", "high", "minimal", "", "XHIGH", "Low"):
        payload = descriptor().policy.model_dump(mode="json")
        payload["reasoning_effort"] = invalid
        with pytest.raises(ValidationError, match="low|xhigh"):
            SessionPolicy.model_validate(payload)


def test_manifest_keeps_xhigh_readable_and_rejects_effort_mismatch() -> None:
    historical = _manifest(task=descriptor(reasoning_effort="xhigh"))
    assert historical.reasoning_effort == "xhigh"
    assert historical.policy.reasoning_effort == "xhigh"
    low = _manifest(task=descriptor(reasoning_effort="low"))
    assert low.reasoning_effort == "low"
    with pytest.raises(ValidationError, match="must match policy"):
        _manifest(
            task=descriptor(reasoning_effort="low"),
            reasoning_effort="xhigh",
        )


def test_app_server_argv_and_turn_use_descriptor_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "aero_bench.agent.codex_driver.subprocess.Popen",
        RecordingAppServerProcess,
    )
    RecordingAppServerProcess.instances.clear()
    context = _context(tmp_path)
    for effort in ("low", "xhigh"):
        RecordingAppServerProcess.instances.clear()
        task = descriptor(reasoning_effort=effort)
        proxy = RestrictedModelProxyServer(ProxySessionState(task)).start()
        client = None
        try:
            client = NativeCodexAppServerClient(context, proxy)
            process = RecordingAppServerProcess.instances[-1]
            assert f'model="{task.policy.model}"' in process.argv
            assert f'model_reasoning_effort="{effort}"' in process.argv
            if effort == "low":
                assert 'model_reasoning_effort="xhigh"' not in process.argv
            assert process.configured_effort() == effort
            client.initialize(timeout_s=2)
            thread = client.start_thread(task, timeout_s=2)
            assert thread["reasoningEffort"] == effort
            client.start_turn(
                thread_id=thread["thread"]["id"],
                initial_input=task.initial_input,
                timeout_s=2,
            )
            turn_start = [
                json.loads(raw)
                for raw in process.writes
                if json.loads(raw).get("method") == "turn/start"
            ]
            assert turn_start and turn_start[0]["params"]["effort"] == effort
        finally:
            if client is not None:
                client.close()
            proxy.close()


def test_low_session_rebuilds_outgoing_request_and_seals_manifest(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    task = descriptor(reasoning_effort="low")
    clients: list[PolicyAwareAppServer] = []

    def factory(_context: DriverContext, proxy: RestrictedModelProxyServer):
        client = PolicyAwareAppServer(proxy)
        clients.append(client)
        return client

    manifest = run_driver(context, task, Bridge(), app_server_factory=factory)
    assert manifest.status == "completed"
    assert manifest.model == "gpt-6-astra"
    assert manifest.reasoning_effort == "low"
    assert manifest.policy.reasoning_effort == "low"
    assert manifest.usage.model_requests == 2
    rebuilt = clients[0].rebuilt_requests
    assert rebuilt
    assert all(item["reasoning"]["effort"] == "low" for item in rebuilt)
    assert all(item["model"] == "gpt-6-astra" for item in rebuilt)


def test_historical_xhigh_session_still_seals(tmp_path: Path) -> None:
    context = _context(tmp_path)
    task = descriptor(reasoning_effort="xhigh")
    clients: list[PolicyAwareAppServer] = []

    def factory(_context: DriverContext, proxy: RestrictedModelProxyServer):
        client = PolicyAwareAppServer(proxy)
        clients.append(client)
        return client

    manifest = run_driver(context, task, Bridge(), app_server_factory=factory)
    assert manifest.status == "completed"
    assert manifest.reasoning_effort == "xhigh"
    assert clients[0].rebuilt_requests[0]["reasoning"]["effort"] == "xhigh"


def test_thread_effort_mismatch_fails_before_model_call(tmp_path: Path) -> None:
    context = _context(tmp_path)
    task = descriptor(reasoning_effort="low")

    def factory(_context: DriverContext, proxy: RestrictedModelProxyServer):
        return MismatchedEffortAppServer(proxy)

    manifest = run_driver(context, task, Bridge(), app_server_factory=factory)
    assert manifest.status == "failed"
    assert manifest.failure_code == "driver.model.mismatch"
    assert manifest.reasoning_effort == "low"


def test_proxy_rejects_undeclared_effort_and_rebuilds_from_policy() -> None:
    low = descriptor(reasoning_effort="low")
    state = ProxySessionState(low)
    state.set_turn_id("turn_1")
    with pytest.raises(ModelProxyViolation, match="undeclared reasoning effort"):
        state.rewrite_request(_raw_request_with_effort("xhigh"))
    rebuilt = json.loads(state.rewrite_request(_raw_request_with_effort("low")))
    assert rebuilt["reasoning"] == {"context": "all_turns", "effort": "low"}
    historical = ProxySessionState(descriptor(reasoning_effort="xhigh"))
    historical.set_turn_id("turn_1")
    with pytest.raises(ModelProxyViolation, match="undeclared reasoning effort"):
        historical.rewrite_request(_raw_request_with_effort("low"))
    restored = json.loads(
        historical.rewrite_request(_raw_request_with_effort("xhigh"))
    )
    assert restored["reasoning"] == {"context": "all_turns", "effort": "xhigh"}


def test_native_client_rejects_thread_descriptor_effort_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "aero_bench.agent.codex_driver.subprocess.Popen",
        RecordingAppServerProcess,
    )
    RecordingAppServerProcess.instances.clear()
    context = _context(tmp_path)
    proxy = RestrictedModelProxyServer(
        ProxySessionState(descriptor(reasoning_effort="low"))
    ).start()
    client = NativeCodexAppServerClient(context, proxy)
    try:
        client.initialize(timeout_s=2)
        with pytest.raises(CodexDriverError, match="thread effort differs"):
            client.start_thread(descriptor(reasoning_effort="xhigh"), timeout_s=2)
    finally:
        client.close()
        proxy.close()
