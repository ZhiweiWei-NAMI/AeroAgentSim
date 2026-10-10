from __future__ import annotations

import hashlib
import http.server
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from aero_bench.agent.session_contracts import (
    SessionDescriptor,
    ToolCallRequest,
    ToolExecutionResult,
)
from aero_bench.providers.rpc import ProviderRpcError, parse_json_object
from aero_bench.serialization import canonical_json_bytes


_MAX_UPSTREAM_BYTES = 128 * 1024 * 1024
_MODELS_PATH = re.compile(
    r"^/backend-api/codex/models(?:\?client_version=[A-Za-z0-9._+-]{1,128})?$"
)
_SETTINGS_PATH = "/backend-api/wham/settings/user"
_RESPONSES_PATH = "/backend-api/codex/responses"
_FORBIDDEN_OUTPUT_TYPES = frozenset(
    {
        "custom_tool_call",
        "local_shell_call",
        "computer_call",
        "web_search_call",
        "mcp_call",
        "mcp_approval_request",
        "image_generation_call",
        "code_interpreter_call",
        "file_search_call",
    }
)


class ModelProxyError(RuntimeError):
    pass


class ModelProxyViolation(ModelProxyError):
    pass


@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str | None
    data: str
    event_id: str | None = None
    retry: int | None = None


class SSEDecoder:
    """Incremental UTF-8 SSE framing with CRLF and multiline-data support."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lines: list[bytes] = []

    def feed(self, chunk: bytes) -> tuple[SSEEvent, ...]:
        if not isinstance(chunk, bytes):
            raise TypeError("SSE chunks must be bytes")
        self._buffer.extend(chunk)
        emitted: list[SSEEvent] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line:
                event = self._dispatch()
                if event is not None:
                    emitted.append(event)
            else:
                self._lines.append(line)
        return tuple(emitted)

    def finalize(self) -> tuple[SSEEvent, ...]:
        if self._buffer:
            line = bytes(self._buffer)
            self._buffer.clear()
            if line.endswith(b"\r"):
                line = line[:-1]
            if line:
                self._lines.append(line)
        event = self._dispatch()
        return () if event is None else (event,)

    def _dispatch(self) -> SSEEvent | None:
        if not self._lines:
            return None
        event_name: str | None = None
        event_id: str | None = None
        retry: int | None = None
        data: list[str] = []
        for raw_line in self._lines:
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as error:
                self._lines.clear()
                raise ModelProxyViolation("upstream SSE is not UTF-8") from error
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data.append(value)
            elif field == "id" and "\x00" not in value:
                event_id = value
            elif field == "retry" and value.isascii() and value.isdecimal():
                retry = int(value)
        self._lines.clear()
        if not data:
            return None
        return SSEEvent(
            event=event_name,
            data="\n".join(data),
            event_id=event_id,
            retry=retry,
        )


def parse_sse_chunks(chunks: Iterable[bytes]) -> tuple[SSEEvent, ...]:
    decoder = SSEDecoder()
    events: list[SSEEvent] = []
    for chunk in chunks:
        events.extend(decoder.feed(chunk))
    events.extend(decoder.finalize())
    return tuple(events)


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class UpstreamTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: int,
    ) -> UpstreamResponse: ...


class UrllibUpstreamTransport:
    """Fixed-origin transport. Environment proxy settings may carry egress."""

    ORIGIN = "https://chatgpt.com"

    def request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: int,
    ) -> UpstreamResponse:
        deadline = time.monotonic() + timeout_s
        request = urllib.request.Request(
            self.ORIGIN + path,
            data=body if method == "POST" else None,
            headers=dict(headers),
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=timeout_s)
        except urllib.error.HTTPError as error:
            response = error
        except Exception as error:
            raise ModelProxyError("model upstream transport failed") from error
        try:
            parts: list[bytes] = []
            size = 0
            while True:
                if time.monotonic() >= deadline:
                    raise ModelProxyError("model response exceeded its wall-time bound")
                part = response.read1(64 * 1024)
                if not part:
                    break
                size += len(part)
                if size > _MAX_UPSTREAM_BYTES:
                    raise ModelProxyViolation(
                        "model upstream response exceeds size bound"
                    )
                parts.append(part)
            if time.monotonic() > deadline:
                raise ModelProxyError("model response exceeded its wall-time bound")
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            return UpstreamResponse(
                status=response.status,
                headers=response_headers,
                body=b"".join(parts),
            )
        finally:
            response.close()


class ModelAuditSink(Protocol):
    def record_model_request(self, payload: Mapping[str, Any]) -> None: ...

    def record_model_response(self, payload: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class ProposedFunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    response_id: str


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = parse_json_object(raw)
    except ProviderRpcError as error:
        raise ModelProxyViolation(f"{label} is not strict JSON") from error
    return value


def _input_message_text(item: Mapping[str, Any]) -> str | None:
    if item.get("type") != "message" or item.get("role") != "user":
        return None
    content = item.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for entry in content:
        if not isinstance(entry, dict):
            return None
        if entry.get("type") not in {"input_text", "text"}:
            return None
        text = entry.get("text")
        if not isinstance(text, str):
            return None
        parts.append(text)
    return "".join(parts)


def _arguments_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        raw = canonical_json_bytes(value)
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raise ModelProxyViolation("model function arguments are not a JSON object")
    return _strict_json_object(raw, label="model function arguments")


def _image_descriptors(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"image_url", "imageUrl"} and isinstance(item, str):
                prefix, separator, encoded = item.partition(",")
                if separator and prefix == "data:image/png;base64":
                    import base64

                    try:
                        raw = base64.b64decode(encoded, validate=True)
                    except (ValueError, base64.binascii.Error) as error:
                        raise ModelProxyViolation(
                            "model input contains invalid image data"
                        ) from error
                    found.append(
                        {
                            "media_type": "image/png",
                            "sha256": _sha256(raw),
                            "size_bytes": len(raw),
                        }
                    )
            else:
                found.extend(_image_descriptors(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_image_descriptors(item))
    return found


class ProxySessionState:
    """Trusted transcript used to rebuild every outbound model request."""

    def __init__(
        self,
        descriptor: SessionDescriptor,
        *,
        audit_sink: ModelAuditSink | None = None,
    ) -> None:
        self.descriptor = descriptor
        self._audit_sink = audit_sink
        self._lock = threading.RLock()
        self._turn_ready = threading.Condition(self._lock)
        self._history: list[dict[str, Any]] = []
        self._proposals: dict[str, ProposedFunctionCall] = {}
        self._completed_call_ids: set[str] = set()
        self._pending_call_ids: set[str] = set()
        self._image_provenance: list[dict[str, Any]] = []
        self._request_count = 0
        self._input_tokens = 0
        self._cached_input_tokens = 0
        self._output_tokens = 0
        self._current_turn_id: str | None = None
        self._failure_detail: str | None = None

    @property
    def failure_detail(self) -> str | None:
        with self._lock:
            return self._failure_detail

    def record_upstream_failure(self, status: int, body: bytes) -> None:
        """Keep bounded error identifiers; never retain headers or raw error text."""
        detail = f"model upstream returned HTTP {status}"
        try:
            error = parse_json_object(body).get("error")
        except ProviderRpcError:
            error = None
        if isinstance(error, dict):
            for key in ("type", "code", "param"):
                value = error.get(key)
                pattern = (
                    r"(?:input|tools|model|reasoning)(?:\.[a-z_]+|\[[0-9]+\])*"
                    if key == "param" else r"[a-z_]{1,64}"
                )
                if isinstance(value, str) and len(value) <= 96 and re.fullmatch(pattern, value):
                    detail += f"; {key}={value}"
        with self._lock:
            if self._failure_detail is None:
                self._failure_detail = detail

    @property
    def model_request_count(self) -> int:
        with self._lock:
            return self._request_count

    @property
    def token_usage(self) -> tuple[int, int, int]:
        with self._lock:
            return (
                self._input_tokens,
                self._cached_input_tokens,
                self._output_tokens,
            )

    @property
    def current_turn_id(self) -> str | None:
        with self._lock:
            return self._current_turn_id

    def set_turn_id(self, turn_id: str) -> None:
        if not isinstance(turn_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,256}", turn_id
        ):
            raise ModelProxyViolation("app-server turn id is invalid")
        with self._lock:
            if self._current_turn_id is not None:
                raise ModelProxyViolation("model proxy turn id was already set")
            self._current_turn_id = turn_id
            self._turn_ready.notify_all()

    def set_audit_sink(self, audit_sink: ModelAuditSink) -> None:
        with self._lock:
            if self._audit_sink is not None:
                raise ModelProxyViolation("model proxy audit sink was already set")
            self._audit_sink = audit_sink

    def proposed_call(self, call_id: str) -> ProposedFunctionCall | None:
        with self._lock:
            return self._proposals.get(call_id)

    def register_tool_result(
        self,
        request: ToolCallRequest,
        result: ToolExecutionResult,
    ) -> None:
        with self._lock:
            proposal = self._proposals.get(request.call_id)
            if proposal is None:
                raise ModelProxyViolation("tool result has no validated model proposal")
            if request.call_id in self._completed_call_ids:
                raise ModelProxyViolation("tool result repeats a completed call_id")
            if proposal.name != request.name or proposal.arguments != request.arguments:
                raise ModelProxyViolation("tool execution differs from model proposal")
            output: str | list[dict[str, Any]]
            if all(item.type == "inputText" for item in result.content):
                output = "\n".join(item.text for item in result.content)  # type: ignore[union-attr]
            else:
                output_items: list[dict[str, Any]] = []
                for item in result.content:
                    if item.type == "inputText":
                        output_items.append({"type": "input_text", "text": item.text})
                    else:
                        output_items.append(
                            {"type": "input_image", "image_url": item.imageUrl}
                        )
                output = output_items
            self._history.append(
                {
                    "type": "function_call_output",
                    "call_id": request.call_id,
                    "output": output,
                }
            )
            self._completed_call_ids.add(request.call_id)
            self._pending_call_ids.remove(request.call_id)
            provenance = result.image_provenance()
            if provenance is not None:
                image_indexes = [
                    index
                    for index, item in enumerate(result.content)
                    if item.type == "inputImage"
                ]
                assert len(image_indexes) == 1
                self._image_provenance.append(
                    {
                        "source_call_id": request.call_id,
                        "content_index": image_indexes[0],
                        "operation_id": result.audit.operation_id,
                        "observation_id": result.audit.observation_id,
                        **provenance,
                        "media_type": "image/png",
                        "size_bytes": len(
                            result.content[image_indexes[0]].image_bytes  # type: ignore[union-attr]
                        ),
                    }
                )

    def rewrite_request(self, raw: bytes) -> bytes:
        document = _strict_json_object(raw, label="app-server model request")
        with self._turn_ready:
            if self._current_turn_id is None:
                self._turn_ready.wait(timeout=10)
            if self._current_turn_id is None:
                raise ModelProxyViolation("model request arrived before turn identity")
            raw_model = document.get("model")
            if raw_model is not None and raw_model != self.descriptor.policy.model:
                raise ModelProxyViolation("app-server requested an undeclared model")
            reasoning = document.get("reasoning")
            if reasoning is not None:
                if not isinstance(reasoning, dict):
                    raise ModelProxyViolation(
                        "app-server reasoning config is malformed"
                    )
                effort = reasoning.get("effort")
                if (
                    effort is not None
                    and effort != self.descriptor.policy.reasoning_effort
                ):
                    raise ModelProxyViolation(
                        "app-server requested undeclared reasoning effort"
                    )
            raw_input = document.get("input")
            if not isinstance(raw_input, list):
                raise ModelProxyViolation("app-server model input must be a list")
            user_messages = [
                text
                for item in raw_input
                if isinstance(item, dict)
                and (text := _input_message_text(item)) is not None
            ]
            if user_messages.count(self.descriptor.initial_input) != 1:
                raise ModelProxyViolation(
                    "app-server did not carry exactly one approved initial input"
                )
            if self._request_count >= self.descriptor.policy.max_model_requests:
                raise ModelProxyViolation("session model request budget is exhausted")
            if self._pending_call_ids:
                raise ModelProxyViolation(
                    "model continuation requested before every proposed tool completed"
                )
            tools = [tool.responses_api_value() for tool in self.descriptor.tools]
            tool_item = {
                "type": "additional_tools",
                "id": "at_" + self.descriptor.tools_digest[:32],
                "role": "developer",
                "tools": tools,
            }
            user_item = {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": self.descriptor.initial_input}
                ],
            }
            rebuilt = {
                "model": self.descriptor.policy.model,
                "instructions": self.descriptor.instruction,
                "input": [tool_item, user_item, *self._history],
                "reasoning": {
                    "effort": self.descriptor.policy.reasoning_effort,
                    "context": "all_turns",
                },
                "include": ["reasoning.encrypted_content"],
                "parallel_tool_calls": False,
                "store": False,
                "stream": True,
            }
            encoded = canonical_json_bytes(rebuilt)
            self._request_count += 1
            actual_images = _image_descriptors(rebuilt["input"])
            if len(actual_images) != len(self._image_provenance):
                raise ModelProxyViolation(
                    "model image transcript provenance is incomplete"
                )
            image_inputs: list[dict[str, Any]] = []
            for actual, provenance in zip(
                actual_images, self._image_provenance, strict=True
            ):
                if (
                    actual["sha256"] != provenance["image_sha256"]
                    or actual["size_bytes"] != provenance["size_bytes"]
                ):
                    raise ModelProxyViolation(
                        "model image bytes differ from tool evidence"
                    )
                image_inputs.append(dict(provenance))
            payload = {
                "request_index": self._request_count,
                "request_digest": _sha256(encoded),
                "request_size_bytes": len(encoded),
                "model": self.descriptor.policy.model,
                "reasoning_effort": self.descriptor.policy.reasoning_effort,
                "instructions_sha256": self.descriptor.instruction_sha256,
                "initial_input_sha256": self.descriptor.initial_input_sha256,
                "tools_digest": self.descriptor.tools_digest,
                "history_item_types": [item.get("type") for item in self._history],
                "image_inputs": image_inputs,
            }
            if self._audit_sink is not None:
                try:
                    self._audit_sink.record_model_request(payload)
                except Exception as error:
                    raise ModelProxyError(
                        "model request audit could not be committed"
                    ) from error
            return encoded

    def validate_response(
        self,
        *,
        content_type: str,
        body: bytes,
    ) -> dict[str, Any]:
        with self._lock:
            # The Codex stream endpoint can omit Content-Type. The declared
            # stream=true protocol still requires complete, valid SSE framing.
            if content_type.lower().split(";", 1)[0].strip() in {
                "",
                "text/event-stream",
            }:
                events = parse_sse_chunks((body,))
                documents: list[dict[str, Any]] = []
                for event in events:
                    if event.data == "[DONE]":
                        continue
                    try:
                        raw = event.data.encode("utf-8")
                    except UnicodeEncodeError as error:
                        raise ModelProxyViolation(
                            "model SSE data is not UTF-8"
                        ) from error
                    document = _strict_json_object(raw, label="model SSE event")
                    self._validate_event_tool_types(document)
                    documents.append(document)
                response = self._completed_response(documents)
            else:
                raise ModelProxyViolation(
                    "model response content type is not supported"
                )
            return self._accept_response(response=response, raw_body=body)

    def _validate_event_tool_types(self, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._validate_event_tool_types(item)
            return
        if not isinstance(value, dict):
            return
        item_type = value.get("type")
        if item_type in _FORBIDDEN_OUTPUT_TYPES:
            raise ModelProxyViolation(
                f"model requested forbidden tool type {item_type}"
            )
        if item_type == "function_call":
            name = value.get("name")
            if not isinstance(name, str) or name not in self.descriptor.tool_map:
                raise ModelProxyViolation("model requested an undeclared function")
        for item in value.values():
            self._validate_event_tool_types(item)

    @staticmethod
    def _completed_response(events: list[dict[str, Any]]) -> dict[str, Any]:
        completed: list[dict[str, Any]] = []
        outputs: dict[int, dict[str, Any]] = {}
        for event in events:
            event_type = event.get("type")
            response = event.get("response")
            if event_type == "response.output_item.done":
                index = event.get("output_index")
                item = event.get("item")
                if (
                    type(index) is not int
                    or index < 0
                    or index in outputs
                    or not isinstance(item, dict)
                ):
                    raise ModelProxyViolation(
                        "model output-item completion inventory is invalid"
                    )
                outputs[index] = item
            if event_type == "response.completed" and isinstance(response, dict):
                completed.append(response)
        if len(completed) != 1 or events[-1].get("type") != "response.completed":
            raise ModelProxyViolation("model SSE must contain one completed response")
        if sorted(outputs) != list(range(len(outputs))):
            raise ModelProxyViolation("model output-item indexes are incomplete")
        ordered = [outputs[index] for index in range(len(outputs))]
        response = dict(completed[0])
        aggregate = response.get("output")
        if aggregate not in (None, []) and aggregate != ordered:
            raise ModelProxyViolation(
                "completed model output differs from output-item events"
            )
        response["output"] = ordered
        return response

    def _accept_response(
        self, *, response: dict[str, Any], raw_body: bytes
    ) -> dict[str, Any]:
        response_id = response.get("id")
        if not isinstance(response_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,256}", response_id
        ):
            raise ModelProxyViolation("model response id is invalid")
        actual_model = response.get("model")
        if actual_model != self.descriptor.policy.model:
            raise ModelProxyViolation("upstream response used an undeclared model")
        output = response.get("output")
        if not isinstance(output, list):
            raise ModelProxyViolation("model response output is missing")
        trusted: list[dict[str, Any]] = []
        explicit_messages: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        for item in output:
            if not isinstance(item, dict):
                raise ModelProxyViolation("model output item is malformed")
            item_type = item.get("type")
            if item_type in _FORBIDDEN_OUTPUT_TYPES:
                raise ModelProxyViolation(
                    f"model requested forbidden tool type {item_type}"
                )
            if item_type == "reasoning":
                # Retain opaque server-issued reasoning only for the next request.
                # It is intentionally absent from the audit payload.
                encrypted = item.get("encrypted_content")
                if encrypted is not None:
                    if (
                        not isinstance(encrypted, str)
                        or len(encrypted) > 16 * 1024 * 1024
                    ):
                        raise ModelProxyViolation(
                            "encrypted reasoning item is malformed"
                        )
                    retained_reasoning = {
                        key: item[key]
                        for key in ("type", "id", "encrypted_content")
                        if key in item
                    }
                    # Required Responses envelope field. Opaque state is replayed,
                    # while reasoning text and summaries stay outside the log.
                    retained_reasoning["summary"] = []
                    trusted.append(retained_reasoning)
                continue
            if item_type == "function_call":
                name = item.get("name")
                call_id = item.get("call_id")
                arguments = _arguments_object(item.get("arguments"))
                try:
                    request = ToolCallRequest(
                        call_id=call_id,
                        name=name,
                        arguments=arguments,
                    )
                except Exception as error:
                    raise ModelProxyViolation(
                        "model function call is malformed"
                    ) from error
                tool = self.descriptor.tool_map.get(request.name)
                if tool is None:
                    raise ModelProxyViolation(
                        f"model requested undeclared function {request.name}"
                    )
                errors = sorted(
                    Draft202012Validator(tool.parameters).iter_errors(arguments),
                    key=lambda value: list(value.absolute_path),
                )
                if errors:
                    raise ModelProxyViolation("model function arguments violate schema")
                if request.call_id in self._proposals:
                    raise ModelProxyViolation("model repeated a call_id")
                proposal = ProposedFunctionCall(
                    call_id=request.call_id,
                    name=request.name,
                    arguments=request.arguments,
                    response_id=response_id,
                )
                self._proposals[request.call_id] = proposal
                self._pending_call_ids.add(request.call_id)
                calls.append(
                    {
                        "call_id": request.call_id,
                        "name": request.name,
                        "arguments": request.arguments,
                        "arguments_digest": _sha256(
                            canonical_json_bytes(request.arguments)
                        ),
                    }
                )
                trusted.append(
                    {
                        key: item[key]
                        for key in ("type", "id", "call_id", "name", "arguments")
                        if key in item
                    }
                )
                continue
            if item_type == "message":
                if item.get("role") != "assistant":
                    raise ModelProxyViolation("model emitted a non-assistant message")
                content = item.get("content")
                if not isinstance(content, list):
                    raise ModelProxyViolation("model assistant content is malformed")
                sanitized_content: list[dict[str, Any]] = []
                texts: list[str] = []
                for part in content:
                    if not isinstance(part, dict) or part.get("type") not in {
                        "output_text",
                        "text",
                    }:
                        raise ModelProxyViolation(
                            "model emitted non-text assistant content"
                        )
                    text = part.get("text")
                    if not isinstance(text, str):
                        raise ModelProxyViolation("model assistant text is malformed")
                    texts.append(text)
                    sanitized_content.append(
                        {key: part[key] for key in ("type", "text") if key in part}
                    )
                explicit_messages.append(
                    {
                        "message_id": item.get("id"),
                        "text": "".join(texts),
                    }
                )
                sanitized = {
                    key: item[key]
                    for key in ("type", "id", "role", "status")
                    if key in item
                }
                sanitized["content"] = sanitized_content
                trusted.append(sanitized)
                continue
            raise ModelProxyViolation(
                f"model emitted unsupported output type {item_type}"
            )
        usage = response.get("usage")
        usage_payload = self._validated_usage(usage)
        if usage_payload["output_tokens"] > self.descriptor.policy.max_output_tokens:
            raise ModelProxyViolation(
                "model response exceeds the accepted output-token bound"
            )
        self._input_tokens += usage_payload["input_tokens"]
        self._cached_input_tokens += usage_payload["cached_input_tokens"]
        self._output_tokens += usage_payload["output_tokens"]
        self._history.extend(trusted)
        audit = {
            "request_index": self._request_count,
            "response_id": response_id,
            "response_digest": _sha256(raw_body),
            "response_size_bytes": len(raw_body),
            "model": self.descriptor.policy.model,
            "function_calls": calls,
            "assistant_messages": explicit_messages,
            "usage": usage_payload,
        }
        if self._audit_sink is not None:
            try:
                self._audit_sink.record_model_response(audit)
            except Exception as error:
                raise ModelProxyError(
                    "model response audit could not be committed"
                ) from error
        return audit

    @staticmethod
    def _validated_usage(value: Any) -> dict[str, int]:
        if not isinstance(value, dict):
            raise ModelProxyViolation("model response usage is malformed")
        input_tokens = value.get("input_tokens")
        output_tokens = value.get("output_tokens")
        details = value.get("input_tokens_details")
        if not isinstance(details, dict) or "cached_tokens" not in details:
            raise ModelProxyViolation("model response cache usage is missing")
        cached = details["cached_tokens"]
        for item in (input_tokens, output_tokens, cached):
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                raise ModelProxyViolation(
                    "model response usage contains invalid tokens"
                )
        if cached > input_tokens:
            raise ModelProxyViolation("model cached token count exceeds input tokens")
        return {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached,
            "output_tokens": output_tokens,
        }


class _ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class RestrictedModelProxyServer:
    """Local HTTP boundary with a fixed HTTPS upstream and fail-closed responses."""

    def __init__(
        self,
        state: ProxySessionState,
        *,
        upstream: UpstreamTransport | None = None,
    ) -> None:
        self.state = state
        self._upstream = UrllibUpstreamTransport() if upstream is None else upstream
        self._server: _ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise ModelProxyError("model proxy is not started")
        return int(self._server.server_port)

    @property
    def codex_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/backend-api/codex"

    @property
    def chatgpt_base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/backend-api/"

    def start(self) -> "RestrictedModelProxyServer":
        if self._server is not None:
            raise ModelProxyError("model proxy is already started")
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: object) -> None:
                return

            def do_CONNECT(self) -> None:
                self.send_error(403)

            def do_PUT(self) -> None:
                self.send_error(403)

            def do_DELETE(self) -> None:
                self.send_error(403)

            def do_PATCH(self) -> None:
                self.send_error(403)

            def do_GET(self) -> None:
                if not (
                    _MODELS_PATH.fullmatch(self.path) or self.path == _SETTINGS_PATH
                ):
                    self.send_error(403)
                    return
                self._forward(body=b"")

            def do_POST(self) -> None:
                if self.path != _RESPONSES_PATH:
                    self.send_error(403)
                    return
                length = self.headers.get("Content-Length")
                if length is None or not length.isascii() or not length.isdecimal():
                    self.send_error(400)
                    return
                size = int(length)
                if size > _MAX_UPSTREAM_BYTES:
                    self.send_error(413)
                    return
                raw = self.rfile.read(size)
                try:
                    body = owner.state.rewrite_request(raw)
                except ModelProxyError:
                    self.send_error(403)
                    return
                self._forward(body=body)

            def _forward(self, *, body: bytes) -> None:
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower()
                    not in {
                        "host",
                        "connection",
                        "content-length",
                        "accept-encoding",
                        "proxy-authorization",
                    }
                }
                headers["Accept-Encoding"] = "identity"
                try:
                    response = owner._upstream.request(
                        method=self.command,
                        path=self.path,
                        headers=headers,
                        body=body,
                        timeout_s=owner.state.descriptor.policy.model_call_timeout_s,
                    )
                    content_type = response.headers.get("content-type", "")
                    if self.command == "POST" and not 200 <= response.status < 300:
                        owner.state.record_upstream_failure(response.status, response.body)
                    if self.command == "POST" and 200 <= response.status < 300:
                        owner.state.validate_response(
                            content_type=content_type,
                            body=response.body,
                        )
                        content_type = "text/event-stream"
                except ModelProxyViolation:
                    self.send_error(502)
                    return
                except ModelProxyError:
                    self.send_error(502)
                    return
                self.send_response(response.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response.body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(response.body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True

        self._server = _ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="aero-bench-model-proxy",
            daemon=True,
        )
        self._thread.start()
        return self

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def __enter__(self) -> "RestrictedModelProxyServer":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "ModelAuditSink",
    "ModelProxyError",
    "ModelProxyViolation",
    "ProposedFunctionCall",
    "ProxySessionState",
    "RestrictedModelProxyServer",
    "SSEDecoder",
    "SSEEvent",
    "UpstreamResponse",
    "UpstreamTransport",
    "UrllibUpstreamTransport",
    "parse_sse_chunks",
]
