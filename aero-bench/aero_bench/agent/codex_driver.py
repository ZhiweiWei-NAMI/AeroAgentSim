from __future__ import annotations

import hashlib
import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from aero_bench.agent.model_proxy import (
    ModelAuditSink,
    ModelProxyError,
    ModelProxyViolation,
    ProxySessionState,
    RestrictedModelProxyServer,
    UpstreamTransport,
)
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    InteractionRecord,
    SessionDescriptor,
    SessionUsage,
    ToolCallRequest,
    ToolExecutionResult,
)
from aero_bench.providers.rpc import ProviderRpcError, parse_json_object
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes


_GENESIS_HASH = "0" * 64
_MAX_APP_SERVER_FRAME = 16 * 1024 * 1024
_OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


class CodexDriverError(RuntimeError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_path(value: Path, *, label: str, kind: str) -> Path:
    if not value.is_absolute() or os.fspath(value) != os.path.normpath(
        os.fspath(value)
    ):
        raise CodexDriverError(
            "driver.context.invalid", f"{label} is not absolute and normalized"
        )
    try:
        resolved = value.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CodexDriverError(
            "driver.context.invalid", f"{label} is unavailable"
        ) from error
    if resolved != value:
        raise CodexDriverError(
            "driver.context.invalid", f"{label} contains a symbolic link"
        )
    if kind == "directory" and not resolved.is_dir():
        raise CodexDriverError("driver.context.invalid", f"{label} is not a directory")
    if kind == "file" and not resolved.is_file():
        raise CodexDriverError("driver.context.invalid", f"{label} is not a file")
    return resolved


@dataclass(frozen=True, slots=True)
class DriverContext:
    """Executor materialization for one driver, separate from session policy."""

    run_id: str
    attempt_id: str
    private_artifact_root: Path
    work_root: Path
    codex_binary: Path
    codex_home: Path
    codex_cli_version: str
    codex_binary_sha256: str
    max_interactions_bytes: int = 64 * 1024 * 1024
    max_manifest_bytes: int = 1024 * 1024

    def validate(self, descriptor: SessionDescriptor) -> None:
        if self.run_id != descriptor.run_id or self.attempt_id != descriptor.attempt_id:
            raise CodexDriverError(
                "driver.identity.mismatch",
                "driver context identity differs from SessionDescriptor",
            )
        artifact_root = _safe_path(
            self.private_artifact_root,
            label="private_artifact_root",
            kind="directory",
        )
        work_root = _safe_path(self.work_root, label="work_root", kind="directory")
        binary = _safe_path(self.codex_binary, label="codex_binary", kind="file")
        codex_home = _safe_path(self.codex_home, label="codex_home", kind="directory")
        roots = (artifact_root, work_root, codex_home)
        if any(
            first == second
            or first.is_relative_to(second)
            or second.is_relative_to(first)
            for index, first in enumerate(roots)
            for second in roots[index + 1 :]
        ):
            raise CodexDriverError(
                "driver.context.invalid", "driver private roots must be distinct"
            )
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", self.codex_cli_version
        ):
            raise CodexDriverError(
                "driver.context.invalid", "codex_cli_version is invalid"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.codex_binary_sha256):
            raise CodexDriverError(
                "driver.context.invalid", "codex_binary_sha256 is invalid"
            )
        hasher = hashlib.sha256()
        with binary.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != self.codex_binary_sha256:
            raise CodexDriverError(
                "driver.binary.mismatch",
                "Codex binary digest differs from executor contract",
            )
        if not 1024 <= self.max_interactions_bytes <= 64 * 1024 * 1024:
            raise CodexDriverError(
                "driver.context.invalid", "interaction artifact budget is invalid"
            )
        if not 1024 <= self.max_manifest_bytes <= 1024 * 1024:
            raise CodexDriverError(
                "driver.context.invalid", "manifest artifact budget is invalid"
            )


class BridgeToolClient(Protocol):
    def execute_tool(
        self,
        request: ToolCallRequest,
        *,
        timeout_s: int,
    ) -> ToolExecutionResult: ...


class AppServerClient(Protocol):
    def initialize(self, *, timeout_s: float) -> Mapping[str, Any]: ...

    def start_thread(
        self, descriptor: SessionDescriptor, *, timeout_s: float
    ) -> Mapping[str, Any]: ...

    def start_turn(
        self,
        *,
        thread_id: str,
        initial_input: str,
        timeout_s: float,
    ) -> Mapping[str, Any]: ...

    def next_event(self, *, timeout_s: float) -> Mapping[str, Any]: ...

    def respond_tool(self, request_id: Any, result: ToolExecutionResult) -> None: ...

    def close(self) -> None: ...


class SessionLogWriter(ModelAuditSink):
    INTERACTIONS_NAME = "model.interactions.jsonl"
    MANIFEST_NAME = "model.session-manifest.json"

    def __init__(
        self,
        *,
        root: Path,
        descriptor: SessionDescriptor,
        session_id: str,
        maximum_bytes: int,
        wall_time_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.root = root
        self.descriptor = descriptor
        self.session_id = session_id
        self.maximum_bytes = maximum_bytes
        self._wall_time_ns = wall_time_ns
        self._lock = threading.RLock()
        self._sequence = 0
        self._previous_hash = _GENESIS_HASH
        self._size = 0
        self._digest = hashlib.sha256()
        self._turn_id: str | None = None
        self._sim_time: SimulationTime | None = None
        path = self.root / self.INTERACTIONS_NAME
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor_fd = os.open(path, flags, 0o600)
        except OSError as error:
            raise CodexDriverError(
                "driver.artifact.unavailable",
                "interaction artifact could not be created",
            ) from error
        self._stream = os.fdopen(descriptor_fd, "wb")
        self._closed = False

    @property
    def turn_id(self) -> str | None:
        with self._lock:
            return self._turn_id

    @property
    def current_sim_time(self) -> SimulationTime | None:
        return self._current_sim_time()

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size

    @property
    def record_count(self) -> int:
        with self._lock:
            return self._sequence

    @property
    def chain_root(self) -> str:
        with self._lock:
            return self._previous_hash

    @property
    def file_digest(self) -> str:
        with self._lock:
            return self._digest.hexdigest()

    def set_turn_id(self, turn_id: str) -> None:
        if not isinstance(turn_id, str) or _OPAQUE_ID.fullmatch(turn_id) is None:
            raise CodexDriverError(
                "driver.protocol.invalid", "app-server turn id is invalid"
            )
        with self._lock:
            if self._turn_id is not None:
                raise CodexDriverError(
                    "driver.protocol.invalid", "app-server turn id was already set"
                )
            self._turn_id = turn_id

    def record_model_request(self, payload: Mapping[str, Any]) -> None:
        self.append(
            "model.request",
            dict(payload),
            turn_id=self._required_turn_id(),
            sim_time=self._current_sim_time(),
        )

    def record_model_response(self, payload: Mapping[str, Any]) -> None:
        self.append(
            "model.response",
            dict(payload),
            turn_id=self._required_turn_id(),
            sim_time=self._current_sim_time(),
        )

    def _required_turn_id(self) -> str:
        with self._lock:
            if self._turn_id is None:
                raise CodexDriverError(
                    "driver.protocol.invalid",
                    "model event arrived before turn identity",
                )
            return self._turn_id

    def _current_sim_time(self) -> SimulationTime | None:
        with self._lock:
            return self._sim_time

    def append(
        self,
        record_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
        call_id: str | None = None,
        sim_time: SimulationTime | None = None,
    ) -> InteractionRecord:
        with self._lock:
            if self._closed:
                raise CodexDriverError(
                    "driver.artifact.closed", "interaction log is closed"
                )
            sequence = self._sequence + 1
            if (
                sim_time is not None
                and self._sim_time is not None
                and (
                    sim_time.tick,
                    sim_time.sim_time_ns,
                )
                < (self._sim_time.tick, self._sim_time.sim_time_ns)
            ):
                raise CodexDriverError(
                    "driver.artifact.invalid",
                    "interaction simulation clock moved backward",
                )
            payload_digest = _sha256(canonical_json_bytes(payload))
            document: dict[str, Any] = {
                "schema_version": "aero-bench.agent-interaction/v1",
                "sequence": sequence,
                "run_id": self.descriptor.run_id,
                "attempt_id": self.descriptor.attempt_id,
                "session_id": self.session_id,
                "turn_id": turn_id,
                "call_id": call_id,
                "record_type": record_type,
                "wall_time_ns": self._wall_time_ns(),
                "sim_time": None
                if sim_time is None
                else sim_time.model_dump(mode="json"),
                "payload": payload,
                "payload_digest": payload_digest,
                "previous_record_hash": self._previous_hash,
            }
            document["record_hash"] = _sha256(canonical_json_bytes(document))
            try:
                record = InteractionRecord.model_validate(document)
            except Exception as error:
                raise CodexDriverError(
                    "driver.artifact.invalid",
                    "interaction record failed strict validation",
                ) from error
            encoded = canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
            if self._size + len(encoded) > self.maximum_bytes:
                raise CodexDriverError(
                    "driver.artifact.exhausted",
                    "interaction artifact exceeds size budget",
                )
            self._stream.write(encoded)
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._digest.update(encoded)
            self._size += len(encoded)
            self._sequence = sequence
            self._previous_hash = record.record_hash
            if sim_time is not None:
                self._sim_time = sim_time
            return record

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
            self._closed = True


def _app_server_argv(
    context: DriverContext,
    proxy: RestrictedModelProxyServer,
    *,
    model: str,
    reasoning_effort: str,
) -> list[str]:
    """Build the pinned app-server command from the session descriptor policy."""

    argv = [os.fspath(context.codex_binary), "app-server", "--stdio"]
    for feature in NativeCodexAppServerClient._DISABLED_FEATURES:
        argv.extend(["-c", f"features.{feature}=false"])
    argv.extend(
        [
            "-c",
            "features.skip_host_skill_discovery=true",
            "-c",
            'web_search="disabled"',
            "-c",
            f'model="{model}"',
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'model_reasoning_summary="none"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'model_provider="aero-bench-openai"',
            "-c",
            'model_providers.aero-bench-openai.name="AERO-BENCH inspected transport"',
            "-c",
            'model_providers.aero-bench-openai.wire_api="responses"',
            "-c",
            "model_providers.aero-bench-openai.requires_openai_auth=true",
            "-c",
            "model_providers.aero-bench-openai.supports_websockets=false",
            "-c",
            f'model_providers.aero-bench-openai.base_url="{proxy.codex_base_url}"',
            "-c",
            f'chatgpt_base_url="{proxy.chatgpt_base_url}"',
        ]
    )
    return argv


class NativeCodexAppServerClient:
    """Minimal JSON-RPC client for a pinned native Codex app-server binary."""

    _DISABLED_FEATURES = (
        "apps",
        "plugins",
        "hooks",
        "shell_tool",
        "unified_exec",
        "multi_agent",
        "multi_agent_v2",
        "browser_use",
        "browser_use_external",
        "computer_use",
        "in_app_browser",
        "image_generation",
        "view_image",
        "skill_search",
        "skill_mcp_dependency_install",
        "memories",
        "goals",
        "sleep_tool",
        "tool_suggest",
        "workspace_dependencies",
        "enable_request_compression",
        "unbounded_connection_retries",
    )

    def __init__(
        self,
        context: DriverContext,
        proxy: RestrictedModelProxyServer,
    ) -> None:
        policy = proxy.state.descriptor.policy
        self._model = policy.model
        self._reasoning_effort = policy.reasoning_effort
        argv = _app_server_argv(
            context,
            proxy,
            model=self._model,
            reasoning_effort=self._reasoning_effort,
        )
        environment = {
            "CODEX_HOME": os.fspath(context.codex_home),
            "HOME": os.fspath(context.work_root),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "TERM": "dumb",
        }
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=context.work_root,
                env=environment,
            )
        except OSError as error:
            raise CodexDriverError(
                "driver.app_server.start_failed", "Codex app-server could not start"
            ) from error
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._work_root = context.work_root
        self._deferred: list[dict[str, Any]] = []
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(
            target=self._read_messages,
            name="aero-bench-codex-app-server-reader",
            daemon=True,
        )
        self._reader.start()

    def _read_messages(self) -> None:
        stream = self._process.stdout
        assert stream is not None
        while True:
            raw = stream.readline(_MAX_APP_SERVER_FRAME + 1)
            if not raw:
                break
            if len(raw) > _MAX_APP_SERVER_FRAME or not raw.endswith(b"\n"):
                self._messages.put(None)
                return
            try:
                message = parse_json_object(raw[:-1])
            except ProviderRpcError:
                self._messages.put(None)
                return
            self._messages.put(message)
        self._messages.put(None)

    def _send(self, value: Mapping[str, Any]) -> None:
        raw = canonical_json_bytes(dict(value)) + b"\n"
        if len(raw) > _MAX_APP_SERVER_FRAME:
            raise CodexDriverError(
                "driver.protocol.oversize", "app-server request exceeds frame limit"
            )
        with self._write_lock:
            stream = self._process.stdin
            if stream is None:
                raise CodexDriverError(
                    "driver.app_server.closed", "Codex app-server stdin is unavailable"
                )
            try:
                stream.write(raw)
                stream.flush()
            except OSError as error:
                raise CodexDriverError(
                    "driver.app_server.closed", "Codex app-server transport closed"
                ) from error

    def _request(
        self, method: str, params: Mapping[str, Any], *, timeout_s: float
    ) -> Mapping[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._send({"id": request_id, "method": method, "params": dict(params)})
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexDriverError(
                    "driver.app_server.timeout", "Codex app-server request timed out"
                )
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty as error:
                raise CodexDriverError(
                    "driver.app_server.timeout", "Codex app-server request timed out"
                ) from error
            if message is None:
                raise CodexDriverError(
                    "driver.app_server.closed", "Codex app-server exited"
                )
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                if "error" in message:
                    raise CodexDriverError(
                        "driver.app_server.rejected",
                        "Codex app-server rejected request",
                    )
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CodexDriverError(
                        "driver.protocol.invalid",
                        "Codex app-server response is malformed",
                    )
                return result
            self._deferred.append(message)

    def initialize(self, *, timeout_s: float) -> Mapping[str, Any]:
        result = self._request(
            "initialize",
            {
                "clientInfo": {"name": "aero_bench_driver", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
            timeout_s=timeout_s,
        )
        self._send({"method": "initialized", "params": {}})
        return result

    def start_thread(
        self, descriptor: SessionDescriptor, *, timeout_s: float
    ) -> Mapping[str, Any]:
        if descriptor.policy.model != self._model:
            raise CodexDriverError(
                "driver.model.mismatch",
                "Codex app-server thread model differs from process policy",
            )
        if descriptor.policy.reasoning_effort != self._reasoning_effort:
            raise CodexDriverError(
                "driver.model.mismatch",
                "Codex app-server thread effort differs from process policy",
            )
        tools = [tool.app_server_value() for tool in descriptor.tools]
        return self._request(
            "thread/start",
            {
                "model": descriptor.policy.model,
                "allowProviderModelFallback": False,
                "ephemeral": True,
                "cwd": os.fspath(self._work_root),
                "runtimeWorkspaceRoots": [],
                "environments": [],
                "selectedCapabilityRoots": [],
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "dynamicTools": tools,
                "baseInstructions": descriptor.instruction,
                "developerInstructions": descriptor.instruction,
            },
            timeout_s=timeout_s,
        )

    def start_turn(
        self,
        *,
        thread_id: str,
        initial_input: str,
        timeout_s: float,
    ) -> Mapping[str, Any]:
        return self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "effort": self._reasoning_effort,
                "summary": "none",
                "environments": [],
                "input": [{"type": "text", "text": initial_input, "text_elements": []}],
            },
            timeout_s=timeout_s,
        )

    def next_event(self, *, timeout_s: float) -> Mapping[str, Any]:
        if self._deferred:
            return self._deferred.pop(0)
        try:
            message = self._messages.get(timeout=timeout_s)
        except queue.Empty as error:
            raise TimeoutError("Codex app-server event deadline") from error
        if message is None:
            raise CodexDriverError(
                "driver.app_server.closed", "Codex app-server exited"
            )
        return message

    def respond_tool(self, request_id: Any, result: ToolExecutionResult) -> None:
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            raise CodexDriverError(
                "driver.protocol.invalid", "tool callback request id is invalid"
            )
        self._send({"id": request_id, "result": result.app_server_result()})

    def close(self) -> None:
        process = self._process
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


AppServerFactory = Callable[
    [DriverContext, RestrictedModelProxyServer], AppServerClient
]


def _native_app_server_factory(
    context: DriverContext, proxy: RestrictedModelProxyServer
) -> AppServerClient:
    return NativeCodexAppServerClient(context, proxy)


def _session_id(descriptor: SessionDescriptor) -> str:
    value = canonical_json_bytes(
        {
            "run_id": descriptor.run_id,
            "attempt_id": descriptor.attempt_id,
            "task_id": descriptor.task_id,
            "agent_id": descriptor.agent_id,
            "driver_id": descriptor.driver_id,
        }
    )
    return "session_" + _sha256(value)[:40]


def _remaining(deadline: float, *, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CodexDriverError(
            "driver.session.timeout", "agent session exceeded wall-clock deadline"
        )
    return min(maximum, remaining)


def _thread_identity(
    result: Mapping[str, Any], descriptor: SessionDescriptor, context: DriverContext
) -> str:
    if result.get("model") != descriptor.policy.model:
        raise CodexDriverError(
            "driver.model.mismatch", "Codex app-server selected an unexpected model"
        )
    if result.get("reasoningEffort") != descriptor.policy.reasoning_effort:
        raise CodexDriverError(
            "driver.model.mismatch",
            "Codex app-server selected unexpected reasoning effort",
        )
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise CodexDriverError(
            "driver.protocol.invalid", "Codex app-server thread is missing"
        )
    thread_id = thread.get("id")
    if not isinstance(thread_id, str) or _OPAQUE_ID.fullmatch(thread_id) is None:
        raise CodexDriverError(
            "driver.protocol.invalid", "Codex app-server thread id is invalid"
        )
    if thread.get("cliVersion") != context.codex_cli_version:
        raise CodexDriverError(
            "driver.binary.mismatch",
            "Codex app-server CLI version differs from contract",
        )
    if result.get("modelProvider") != "aero-bench-openai":
        raise CodexDriverError(
            "driver.model.mismatch",
            "Codex app-server bypassed inspected model transport",
        )
    return thread_id


def _turn_identity(result: Mapping[str, Any]) -> str:
    turn = result.get("turn")
    if not isinstance(turn, dict):
        raise CodexDriverError(
            "driver.protocol.invalid", "Codex app-server turn is missing"
        )
    turn_id = turn.get("id")
    if not isinstance(turn_id, str) or _OPAQUE_ID.fullmatch(turn_id) is None:
        raise CodexDriverError(
            "driver.protocol.invalid", "Codex app-server turn id is invalid"
        )
    return turn_id


def _write_manifest(
    *, root: Path, manifest: AgentSessionManifest, maximum_bytes: int
) -> None:
    encoded = canonical_json_bytes(manifest.model_dump(mode="json")) + b"\n"
    if len(encoded) > maximum_bytes:
        raise CodexDriverError(
            "driver.artifact.exhausted", "session manifest exceeds size budget"
        )
    final_path = root / SessionLogWriter.MANIFEST_NAME
    temporary_path = root / ("." + SessionLogWriter.MANIFEST_NAME + ".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor_fd = os.open(temporary_path, flags, 0o600)
        with os.fdopen(descriptor_fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, final_path, follow_symlinks=False)
        os.unlink(temporary_path)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        raise CodexDriverError(
            "driver.artifact.unavailable", "session manifest could not be committed"
        ) from error


def run_driver(
    context: DriverContext,
    descriptor: SessionDescriptor,
    bridge: BridgeToolClient,
    *,
    upstream_transport: UpstreamTransport | None = None,
    app_server_factory: AppServerFactory = _native_app_server_factory,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic: Callable[[], float] = time.monotonic,
) -> AgentSessionManifest:
    """Run one isolated Codex session and seal success or failure evidence."""

    context.validate(descriptor)
    if any(context.private_artifact_root.iterdir()):
        raise CodexDriverError(
            "driver.artifact.not_empty", "driver artifact root is not empty at startup"
        )
    started_wall_time_ns = wall_time_ns()
    session_id = _session_id(descriptor)
    writer = SessionLogWriter(
        root=context.private_artifact_root,
        descriptor=descriptor,
        session_id=session_id,
        maximum_bytes=context.max_interactions_bytes,
        wall_time_ns=wall_time_ns,
    )
    state = ProxySessionState(descriptor, audit_sink=writer)
    proxy = RestrictedModelProxyServer(state, upstream=upstream_transport)
    app_server: AppServerClient | None = None
    terminal_turn_id: str | None = None
    terminal_status = "failed"
    failure_code: str | None = None
    failure_detail: str | None = None
    tool_calls = 0
    image_observations = 0
    deadline = monotonic() + descriptor.policy.session_wall_timeout_s
    try:
        proxy.start()
        app_server = app_server_factory(context, proxy)
        app_server.initialize(timeout_s=_remaining(deadline, maximum=30))
        thread = app_server.start_thread(
            descriptor, timeout_s=_remaining(deadline, maximum=30)
        )
        thread_id = _thread_identity(thread, descriptor, context)
        turn = app_server.start_turn(
            thread_id=thread_id,
            initial_input=descriptor.initial_input,
            timeout_s=_remaining(deadline, maximum=30),
        )
        terminal_turn_id = _turn_identity(turn)
        writer.set_turn_id(terminal_turn_id)
        state.set_turn_id(terminal_turn_id)
        while True:
            event = app_server.next_event(
                timeout_s=_remaining(
                    deadline, maximum=descriptor.policy.model_call_timeout_s + 30
                )
            )
            method = event.get("method")
            params = event.get("params")
            if method == "item/tool/call":
                if not isinstance(params, dict):
                    raise CodexDriverError(
                        "driver.protocol.invalid", "tool callback params are malformed"
                    )
                tool_calls += 1
                if tool_calls > descriptor.policy.max_tool_calls:
                    raise CodexDriverError(
                        "driver.tool_budget.exhausted",
                        "session tool-call budget is exhausted",
                    )
                try:
                    request = ToolCallRequest(
                        call_id=params.get("callId"),
                        name=params.get("tool"),
                        arguments=params.get("arguments"),
                    )
                except Exception as error:
                    raise CodexDriverError(
                        "driver.protocol.invalid", "tool callback is malformed"
                    ) from error
                proposal = state.proposed_call(request.call_id)
                if (
                    proposal is None
                    or proposal.name != request.name
                    or proposal.arguments != request.arguments
                ):
                    raise CodexDriverError(
                        "driver.tool.undeclared",
                        "tool callback lacks validated model proposal",
                    )
                tool = descriptor.tool_map.get(request.name)
                if tool is None:
                    raise CodexDriverError(
                        "driver.tool.undeclared",
                        "tool callback requested undeclared function",
                    )
                errors = tuple(
                    Draft202012Validator(tool.parameters).iter_errors(request.arguments)
                )
                if errors:
                    raise CodexDriverError(
                        "driver.tool.arguments_invalid",
                        "tool callback arguments violate schema",
                    )
                writer.append(
                    "tool.call",
                    {
                        "name": request.name,
                        "arguments": request.arguments,
                        "arguments_digest": _sha256(
                            canonical_json_bytes(request.arguments)
                        ),
                        "model_response_id": proposal.response_id,
                    },
                    turn_id=terminal_turn_id,
                    call_id=request.call_id,
                    sim_time=writer.current_sim_time,
                )
                try:
                    result = bridge.execute_tool(
                        request,
                        timeout_s=max(
                            1,
                            int(
                                _remaining(
                                    deadline,
                                    maximum=1800,
                                )
                            ),
                        ),
                    )
                except Exception as error:
                    raise CodexDriverError(
                        "driver.bridge.failed", "authorized tool bridge call failed"
                    ) from error
                if not isinstance(result, ToolExecutionResult):
                    try:
                        result = ToolExecutionResult.model_validate(result)
                    except Exception as error:
                        raise CodexDriverError(
                            "driver.bridge.invalid",
                            "tool bridge returned an invalid result",
                        ) from error
                if result.audit.operation != request.name:
                    raise CodexDriverError(
                        "driver.bridge.invalid",
                        "tool bridge audit operation differs from requested function",
                    )
                if result.success and result.image_provenance() is not None:
                    image_observations += 1
                    if image_observations > descriptor.policy.max_image_observations:
                        raise CodexDriverError(
                            "driver.image_budget.exhausted",
                            "session image-observation budget is exhausted",
                        )
                state.register_tool_result(request, result)
                writer.append(
                    "tool.result",
                    {
                        "name": request.name,
                        "result": result.model_dump(mode="json"),
                    },
                    turn_id=terminal_turn_id,
                    call_id=request.call_id,
                    sim_time=result.audit.completed_at,
                )
                app_server.respond_tool(event.get("id"), result)
                continue
            if method == "turn/completed":
                if not isinstance(params, dict):
                    raise CodexDriverError(
                        "driver.protocol.invalid",
                        "turn completion params are malformed",
                    )
                completed_turn = params.get("turn")
                if not isinstance(completed_turn, dict):
                    raise CodexDriverError(
                        "driver.protocol.invalid", "turn completion is malformed"
                    )
                if completed_turn.get("id") != terminal_turn_id:
                    raise CodexDriverError(
                        "driver.protocol.invalid", "turn completion id changed"
                    )
                if completed_turn.get("status") != "completed":
                    raise CodexDriverError(
                        "driver.turn.failed", state.failure_detail or "Codex model turn did not complete"
                    )
                writer.append(
                    "session.complete",
                    {
                        "app_server_thread_id": thread_id,
                        "terminal_turn_id": terminal_turn_id,
                        "model_requests": state.model_request_count,
                        "tool_calls": tool_calls,
                        "image_observations": image_observations,
                    },
                )
                terminal_status = "completed"
                break
            if isinstance(event.get("id"), (str, int)) and "method" in event:
                raise CodexDriverError(
                    "driver.app_server.unauthorized_request",
                    "Codex app-server requested an unsupported host capability",
                )
            if method == "error":
                raise CodexDriverError(
                    "driver.turn.failed", state.failure_detail or "Codex app-server reported a turn error"
                )
            # Notifications carry no executable authority and are ignored.
    except CodexDriverError as error:
        failure_code = error.code
        failure_detail = error.detail
    except (ModelProxyError, ModelProxyViolation):
        failure_code = "driver.model_proxy.failed"
        failure_detail = "inspected model transport rejected the session"
    except TimeoutError:
        failure_code = "driver.session.timeout"
        failure_detail = "agent session exceeded a response deadline"
    except Exception:
        failure_code = "driver.internal.failed"
        failure_detail = "driver failed without exposing internal state"
    finally:
        if terminal_status == "failed":
            if failure_code is None:
                failure_code = "driver.internal.failed"
            if failure_detail is None:
                failure_detail = "driver failed without a terminal result"
            try:
                writer.append(
                    "session.failed",
                    {"failure_code": failure_code, "failure_detail": failure_detail},
                )
            except CodexDriverError:
                pass
        if app_server is not None:
            app_server.close()
        proxy.close()
        writer.close()
    input_tokens, cached_tokens, output_tokens = state.token_usage
    manifest = AgentSessionManifest(
        schema_version="aero-bench.agent-session-manifest/v1",
        run_id=descriptor.run_id,
        attempt_id=descriptor.attempt_id,
        session_id=session_id,
        task_id=descriptor.task_id,
        agent_id=descriptor.agent_id,
        driver_id=descriptor.driver_id,
        status=terminal_status,
        model=descriptor.policy.model,
        reasoning_effort=descriptor.policy.reasoning_effort,
        codex_cli_version=context.codex_cli_version,
        codex_binary_sha256=context.codex_binary_sha256,
        policy=descriptor.policy,
        instruction_sha256=descriptor.instruction_sha256,
        initial_input_sha256=descriptor.initial_input_sha256,
        tools_digest=descriptor.tools_digest,
        started_wall_time_ns=started_wall_time_ns,
        completed_wall_time_ns=wall_time_ns(),
        terminal_turn_id=terminal_turn_id,
        failure_code=failure_code,
        failure_detail=failure_detail,
        usage=SessionUsage(
            model_requests=state.model_request_count,
            tool_calls=tool_calls,
            image_observations=image_observations,
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        interactions_sha256=writer.file_digest,
        interactions_size_bytes=writer.size_bytes,
        log_record_count=writer.record_count,
        log_chain_root=writer.chain_root,
    )
    _write_manifest(
        root=context.private_artifact_root,
        manifest=manifest,
        maximum_bytes=context.max_manifest_bytes,
    )
    return manifest


def interaction_log_from_jsonl_bytes(
    raw: bytes,
    *,
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
    maximum_bytes: int = 64 * 1024 * 1024,
) -> tuple[InteractionRecord, ...]:
    """Validate sealed bytes without reopening a verifier-controlled path."""
    if not raw or len(raw) > maximum_bytes or not raw.endswith(b"\n"):
        raise CodexDriverError(
            "driver.artifact.invalid", "interaction log framing or size is invalid"
        )
    records: list[InteractionRecord] = []
    previous_hash = _GENESIS_HASH
    previous_wall_time = -1
    previous_sim_time: tuple[int, int] | None = None
    expected_sequence = 1
    current_request = False
    proposals: dict[str, tuple[str, str]] = {}
    called: set[str] = set()
    completed: set[str] = set()
    operation_ids: set[str] = set()
    terminal = False
    session_id: str | None = None
    turn_id: str | None = None
    for line in raw.splitlines(keepends=True):
        if terminal or not line.endswith(b"\n"):
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction records follow terminal state"
            )
        try:
            document = parse_json_object(line[:-1])
            if canonical_json_bytes(document) + b"\n" != line:
                raise ValueError("noncanonical")
            record = InteractionRecord.model_validate(document)
        except Exception as error:
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction record is invalid"
            ) from error
        if (
            record.sequence != expected_sequence
            or record.previous_record_hash != previous_hash
        ):
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction hash chain is discontinuous"
            )
        if record.wall_time_ns < previous_wall_time:
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction wall clock moved backward"
            )
        if record.sim_time is not None:
            current_sim_time = (record.sim_time.tick, record.sim_time.sim_time_ns)
            if previous_sim_time is not None and current_sim_time < previous_sim_time:
                raise CodexDriverError(
                    "driver.artifact.invalid",
                    "interaction simulation clock moved backward",
                )
            previous_sim_time = current_sim_time
        if expected_run_id is not None and record.run_id != expected_run_id:
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction run identity changed"
            )
        if expected_attempt_id is not None and record.attempt_id != expected_attempt_id:
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction attempt identity changed"
            )
        if session_id is None:
            session_id = record.session_id
        elif record.session_id != session_id:
            raise CodexDriverError(
                "driver.artifact.invalid", "interaction session identity changed"
            )
        if record.turn_id is not None:
            if turn_id is None:
                turn_id = record.turn_id
            elif record.turn_id != turn_id:
                raise CodexDriverError(
                    "driver.artifact.invalid", "interaction turn identity changed"
                )
        if record.record_type == "model.request":
            if current_request or set(proposals) != completed:
                raise CodexDriverError(
                    "driver.artifact.invalid",
                    "model request lacks completed prior causality",
                )
            current_request = True
            proposals.clear()
            called.clear()
            completed.clear()
        elif record.record_type == "model.response":
            if not current_request:
                raise CodexDriverError(
                    "driver.artifact.invalid", "model response lacks request"
                )
            current_request = False
            function_calls = record.payload.get("function_calls")
            if not isinstance(function_calls, list):
                raise CodexDriverError(
                    "driver.artifact.invalid", "model response call list is absent"
                )
            for item in function_calls:
                if not isinstance(item, dict) or set(item) != {
                    "call_id",
                    "name",
                    "arguments",
                    "arguments_digest",
                }:
                    raise CodexDriverError(
                        "driver.artifact.invalid", "model response call is malformed"
                    )
                call_id = item["call_id"]
                name = item["name"]
                arguments = item["arguments"]
                arguments_digest = item["arguments_digest"]
                if (
                    not isinstance(call_id, str)
                    or not isinstance(name, str)
                    or not isinstance(arguments, dict)
                    or not isinstance(arguments_digest, str)
                    or _sha256(canonical_json_bytes(arguments)) != arguments_digest
                ):
                    raise CodexDriverError(
                        "driver.artifact.invalid", "model response call is malformed"
                    )
                if call_id in proposals:
                    raise CodexDriverError(
                        "driver.artifact.invalid", "model response repeats call_id"
                    )
                proposals[call_id] = (name, arguments_digest)
        elif record.record_type == "tool.call":
            assert record.call_id is not None
            if (
                current_request
                or record.call_id not in proposals
                or record.call_id in called
            ):
                raise CodexDriverError(
                    "driver.artifact.invalid", "tool call lacks model proposal"
                )
            name = record.payload.get("name")
            arguments = record.payload.get("arguments")
            arguments_digest = record.payload.get("arguments_digest")
            if (
                not isinstance(name, str)
                or not isinstance(arguments, dict)
                or not isinstance(arguments_digest, str)
                or _sha256(canonical_json_bytes(arguments)) != arguments_digest
                or proposals[record.call_id] != (name, arguments_digest)
            ):
                raise CodexDriverError(
                    "driver.artifact.invalid",
                    "tool call differs from the validated model proposal",
                )
            called.add(record.call_id)
        elif record.record_type == "tool.result":
            assert record.call_id is not None
            if record.call_id not in called or record.call_id in completed:
                raise CodexDriverError(
                    "driver.artifact.invalid", "tool result lacks unique tool call"
                )
            name = record.payload.get("name")
            result_payload = record.payload.get("result")
            try:
                result = ToolExecutionResult.model_validate(result_payload)
            except Exception as error:
                raise CodexDriverError(
                    "driver.artifact.invalid", "tool result payload is invalid"
                ) from error
            if (
                not isinstance(name, str)
                or name != proposals[record.call_id][0]
                or result.audit.operation != name
                or result.audit.operation_id in operation_ids
                or record.sim_time != result.audit.completed_at
            ):
                raise CodexDriverError(
                    "driver.artifact.invalid", "tool result audit breaks call causality"
                )
            operation_ids.add(result.audit.operation_id)
            completed.add(record.call_id)
        elif record.record_type == "session.complete":
            if current_request or proposals or called or completed:
                if set(proposals) != completed:
                    raise CodexDriverError(
                        "driver.artifact.invalid",
                        "completed session has pending tool calls",
                    )
            terminal = True
        elif record.record_type == "session.failed":
            terminal = True
        previous_hash = record.record_hash
        previous_wall_time = record.wall_time_ns
        expected_sequence += 1
        records.append(record)
    if not terminal:
        raise CodexDriverError(
            "driver.artifact.invalid", "interaction log has no terminal record"
        )
    return tuple(records)


def validate_interaction_log(
    path: Path,
    *,
    expected_run_id: str | None = None,
    expected_attempt_id: str | None = None,
    maximum_bytes: int = 64 * 1024 * 1024,
) -> tuple[InteractionRecord, ...]:
    """Path convenience wrapper; formal verifiers should pass sealed bytes."""

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CodexDriverError(
            "driver.artifact.unavailable", "interaction log is unavailable"
        ) from error
    return interaction_log_from_jsonl_bytes(
        raw,
        expected_run_id=expected_run_id,
        expected_attempt_id=expected_attempt_id,
        maximum_bytes=maximum_bytes,
    )


__all__ = [
    "AppServerClient",
    "AppServerFactory",
    "BridgeToolClient",
    "CodexDriverError",
    "DriverContext",
    "NativeCodexAppServerClient",
    "SessionLogWriter",
    "interaction_log_from_jsonl_bytes",
    "run_driver",
    "validate_interaction_log",
]
