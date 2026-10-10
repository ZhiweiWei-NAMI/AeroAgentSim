from __future__ import annotations

import base64
import hashlib
import math
import re
from typing import Annotated, Any, Literal, TypeAlias

from jsonschema import Draft202012Validator
from pydantic import Field, StrictStr, field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.providers.rpc import ProviderRpcError, parse_json_object
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes


_MAX_JSON_DEPTH = 64
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_OPAQUE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,256}$"
_FORBIDDEN_SECRET_KEYS = frozenset(
    {
        "authorization",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "token",
        "tokens",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "cookie",
        "set_cookie",
    }
)

OpaqueId = Annotated[StrictStr, Field(pattern=_OPAQUE_ID_PATTERN)]
JsonObject: TypeAlias = dict[str, Any]
ReasoningEffort = Literal["low", "xhigh"]


def _normalized_secret_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _is_secret_key(value: str) -> bool:
    normalized = _normalized_secret_key(value)
    return normalized in _FORBIDDEN_SECRET_KEYS or normalized.endswith(
        ("_token", "_secret", "_credential", "_api_key")
    )


def _validate_json_value(
    value: Any,
    *,
    label: str,
    reject_secrets: bool = False,
    depth: int = 0,
) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{label} exceeds the maximum JSON nesting depth")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(
                item,
                label=label,
                reject_secrets=reject_secrets,
                depth=depth + 1,
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} contains a non-string object key")
            if reject_secrets and _is_secret_key(key):
                raise ValueError(f"{label} contains credential-bearing field {key!r}")
            _validate_json_value(
                item,
                label=label,
                reject_secrets=reject_secrets,
                depth=depth + 1,
            )
        return
    raise ValueError(f"{label} is not JSON data")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_object(value: str, *, label: str) -> dict[str, Any]:
    try:
        parsed = parse_json_object(value.encode("utf-8"))
    except (ProviderRpcError, UnicodeEncodeError) as error:
        raise ValueError(f"{label} must be one strict JSON object") from error
    if canonical_json_bytes(parsed).decode("utf-8") != value:
        raise ValueError(f"{label} must use canonical JSON encoding")
    _validate_json_value(parsed, label=label, reject_secrets=True)
    return parsed


def _nested_named_strings(value: Any, name: str) -> tuple[str, ...]:
    found: list[str] = []
    if isinstance(value, dict):
        direct = value.get(name)
        if isinstance(direct, str):
            found.append(direct)
        if value.get("name") == name and isinstance(value.get("value"), str):
            found.append(value["value"])
        for key, item in value.items():
            if key != name:
                found.extend(_nested_named_strings(item, name))
    elif isinstance(value, list):
        for item in value:
            found.extend(_nested_named_strings(item, name))
    return tuple(found)


class SessionPolicy(StrictModel):
    schema_version: Literal["aero-bench.agent-session-policy/v1"]
    model: Literal["gpt-6-astra"]
    reasoning_effort: ReasoningEffort
    max_model_requests: Annotated[int, Field(ge=1, le=10_000)]
    max_tool_calls: Annotated[int, Field(ge=1, le=100_000)]
    max_image_observations: Annotated[int, Field(ge=1, le=1024)]
    model_call_timeout_s: Annotated[int, Field(ge=1, le=3_600)]
    session_wall_timeout_s: Annotated[int, Field(ge=1, le=86_400)]
    max_output_tokens: Annotated[
        int,
        Field(
            ge=1,
            le=200_000,
            description="Maximum accepted response output tokens; the inspected transport rejects larger responses before exposing any output or tool call.",
        ),
    ]


class FunctionTool(StrictModel):
    name: Identifier
    description: Annotated[str, Field(min_length=1, max_length=4096)]
    parameters: JsonObject
    strict: Literal[True]

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool description must contain text")
        return value

    @field_validator("parameters")
    @classmethod
    def strict_object_schema(cls, value: JsonObject) -> JsonObject:
        _validate_json_value(value, label="tool parameters")
        try:
            Draft202012Validator.check_schema(value)
        except Exception as error:
            raise ValueError("tool parameters must be a valid JSON Schema") from error
        if value.get("type") != "object":
            raise ValueError("tool parameters root type must be object")
        if value.get("additionalProperties") is not False:
            raise ValueError("tool parameters must forbid additional properties")
        return value

    def responses_api_value(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }

    def app_server_value(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
        }


class SessionDescriptor(StrictModel):
    schema_version: Literal["aero-bench.agent-session/v1"]
    run_id: Sha256
    attempt_id: Identifier
    task_id: Identifier
    agent_id: Identifier
    driver_id: Identifier
    instruction: Annotated[str, Field(min_length=1, max_length=131_072)]
    instruction_sha256: Sha256
    initial_input: Annotated[str, Field(min_length=1, max_length=131_072)]
    initial_input_sha256: Sha256
    policy: SessionPolicy
    tools: tuple[FunctionTool, ...] = Field(min_length=1, max_length=1024)
    tools_digest: Sha256

    @model_validator(mode="after")
    def digests_and_catalog_are_exact(self) -> "SessionDescriptor":
        if not self.instruction.strip() or not self.initial_input.strip():
            raise ValueError("session instruction and initial_input must contain text")
        if _sha256(self.instruction.encode("utf-8")) != self.instruction_sha256:
            raise ValueError("session instruction_sha256 does not match instruction")
        if _sha256(self.initial_input.encode("utf-8")) != self.initial_input_sha256:
            raise ValueError(
                "session initial_input_sha256 does not match initial_input"
            )
        names = tuple(tool.name for tool in self.tools)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("session tools must have unique names in sorted order")
        catalog = [tool.model_dump(mode="json") for tool in self.tools]
        if _sha256(canonical_json_bytes(catalog)) != self.tools_digest:
            raise ValueError("session tools_digest does not match tools")
        return self

    @property
    def tool_map(self) -> dict[str, FunctionTool]:
        return {tool.name: tool for tool in self.tools}


class ToolCallRequest(StrictModel):
    call_id: OpaqueId
    name: Identifier
    arguments: JsonObject

    @field_validator("arguments")
    @classmethod
    def arguments_are_json(cls, value: JsonObject) -> JsonObject:
        _validate_json_value(value, label="tool arguments")
        return value


class ToolTextContent(StrictModel):
    type: Literal["inputText"]
    text: Annotated[str, Field(max_length=16 * 1024 * 1024)]


class ToolImageContent(StrictModel):
    type: Literal["inputImage"]
    imageUrl: Annotated[str, Field(min_length=30, max_length=24 * 1024 * 1024)]

    @field_validator("imageUrl")
    @classmethod
    def canonical_png_data_url(cls, value: str) -> str:
        prefix = "data:image/png;base64,"
        if not value.startswith(prefix):
            raise ValueError("tool images must be PNG data URLs")
        encoded = value[len(prefix) :]
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("tool image is not valid base64") from error
        if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("tool image data is not a PNG")
        if len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("tool image exceeds the size bound")
        if base64.b64encode(decoded).decode("ascii") != encoded:
            raise ValueError("tool image base64 is not canonical")
        return value

    @property
    def image_bytes(self) -> bytes:
        return base64.b64decode(self.imageUrl.partition(",")[2], validate=True)


ToolContent: TypeAlias = Annotated[
    ToolTextContent | ToolImageContent, Field(discriminator="type")
]


class GatewayCallAudit(StrictModel):
    operation: Identifier
    request_json: Annotated[str, Field(min_length=2, max_length=16 * 1024 * 1024)]
    request_digest: Sha256
    response_json: Annotated[str, Field(min_length=2, max_length=16 * 1024 * 1024)]
    response_digest: Sha256
    issued_at: SimulationTime
    completed_at: SimulationTime

    @model_validator(mode="after")
    def canonical_secret_free_frames(self) -> "GatewayCallAudit":
        _canonical_object(self.request_json, label="gateway request_json")
        _canonical_object(self.response_json, label="gateway response_json")
        if _sha256(self.request_json.encode("utf-8")) != self.request_digest:
            raise ValueError("gateway request_digest does not match request_json")
        if _sha256(self.response_json.encode("utf-8")) != self.response_digest:
            raise ValueError("gateway response_digest does not match response_json")
        if (self.completed_at.tick, self.completed_at.sim_time_ns) < (
            self.issued_at.tick,
            self.issued_at.sim_time_ns,
        ):
            raise ValueError("gateway call completed_at precedes issued_at")
        return self


class ToolExecutionAudit(StrictModel):
    operation_id: Identifier
    operation: Identifier
    command_id: Identifier | None
    query_id: Identifier | None
    observation_id: Identifier | None
    issued_at: SimulationTime
    completed_at: SimulationTime
    result_digest: Sha256
    gateway_calls: tuple[GatewayCallAudit, ...]

    @model_validator(mode="after")
    def times_and_gateway_calls_are_ordered(self) -> "ToolExecutionAudit":
        start = (self.issued_at.tick, self.issued_at.sim_time_ns)
        end = (self.completed_at.tick, self.completed_at.sim_time_ns)
        if end < start:
            raise ValueError("tool audit completed_at precedes issued_at")
        previous = start
        for call in self.gateway_calls:
            call_start = (call.issued_at.tick, call.issued_at.sim_time_ns)
            call_end = (call.completed_at.tick, call.completed_at.sim_time_ns)
            if call_start < previous or call_end > end:
                raise ValueError(
                    "gateway call lies outside ordered tool audit interval"
                )
            previous = call_end
        return self


class ToolExecutionResult(StrictModel):
    schema_version: Literal["aero-bench.agent-tool-execution-result/v1"]
    success: bool
    content: tuple[ToolContent, ...] = Field(min_length=1, max_length=256)
    audit: ToolExecutionAudit

    @model_validator(mode="after")
    def result_digest_is_exact(self) -> "ToolExecutionResult":
        result = {
            "success": self.success,
            "content": [item.model_dump(mode="json") for item in self.content],
        }
        if _sha256(canonical_json_bytes(result)) != self.audit.result_digest:
            raise ValueError("tool result_digest does not match visible result content")
        images = [item for item in self.content if item.type == "inputImage"]
        if len(images) > 1:
            raise ValueError("one tool result cannot expose multiple image inputs")
        if images:
            self._image_provenance(images[0])
        return self

    def _image_provenance(self, image: ToolImageContent) -> dict[str, str]:
        if self.audit.observation_id is None:
            raise ValueError("image tool result requires an observation_id")
        image_digest = _sha256(image.image_bytes)
        candidates: list[dict[str, str]] = []
        for call in self.audit.gateway_calls:
            document = _canonical_object(
                call.response_json, label="gateway response_json"
            )
            frame_ids = set(_nested_named_strings(document, "frame_id"))
            image_digests = set(_nested_named_strings(document, "image_sha256"))
            payload_digests = set(_nested_named_strings(document, "payload_digest"))
            if not frame_ids and not image_digests and not payload_digests:
                continue
            if (
                len(frame_ids) != 1
                or len(image_digests) != 1
                or len(payload_digests) != 1
            ):
                raise ValueError("image Gateway audit has ambiguous provenance fields")
            frame_id = next(iter(frame_ids))
            declared_image_digest = next(iter(image_digests))
            payload_digest = next(iter(payload_digests))
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", frame_id):
                raise ValueError("image Gateway audit frame_id is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", declared_image_digest):
                raise ValueError("image Gateway audit image_sha256 is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", payload_digest):
                raise ValueError("image Gateway audit payload_digest is invalid")
            if declared_image_digest != image_digest:
                raise ValueError("image bytes differ from Gateway image_sha256")
            candidates.append(
                {
                    "frame_id": frame_id,
                    "image_sha256": declared_image_digest,
                    "observation_payload_digest": payload_digest,
                    "gateway_response_digest": call.response_digest,
                }
            )
        if len(candidates) != 1:
            raise ValueError(
                "image tool result requires one Gateway provenance response"
            )
        return candidates[0]

    def image_provenance(self) -> dict[str, str] | None:
        images = [item for item in self.content if item.type == "inputImage"]
        return None if not images else self._image_provenance(images[0])

    def app_server_result(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "contentItems": [item.model_dump(mode="json") for item in self.content],
        }


class SessionUsage(StrictModel):
    model_requests: Annotated[int, Field(ge=0)]
    tool_calls: Annotated[int, Field(ge=0)]
    image_observations: Annotated[int, Field(ge=0)]
    input_tokens: Annotated[int, Field(ge=0)]
    cached_input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def token_total_is_exact(self) -> "SessionUsage":
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError(
                "session total_tokens must equal input_tokens + output_tokens"
            )
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self


class AgentSessionManifest(StrictModel):
    schema_version: Literal["aero-bench.agent-session-manifest/v1"]
    run_id: Sha256
    attempt_id: Identifier
    session_id: OpaqueId
    task_id: Identifier
    agent_id: Identifier
    driver_id: Identifier
    status: Literal["completed", "failed"]
    model: Literal["gpt-6-astra"]
    reasoning_effort: ReasoningEffort
    codex_cli_version: Annotated[str, Field(min_length=1, max_length=128)]
    codex_binary_sha256: Sha256
    policy: SessionPolicy
    instruction_sha256: Sha256
    initial_input_sha256: Sha256
    tools_digest: Sha256
    started_wall_time_ns: Annotated[int, Field(ge=0)]
    completed_wall_time_ns: Annotated[int, Field(ge=0)]
    terminal_turn_id: OpaqueId | None
    failure_code: Identifier | None
    failure_detail: Annotated[str, Field(min_length=1, max_length=4096)] | None
    usage: SessionUsage
    interactions_sha256: Sha256
    interactions_size_bytes: Annotated[int, Field(gt=0, le=64 * 1024 * 1024)]
    log_record_count: Annotated[int, Field(gt=0)]
    log_chain_root: Sha256

    @model_validator(mode="after")
    def terminal_state_is_consistent(self) -> "AgentSessionManifest":
        if self.reasoning_effort != self.policy.reasoning_effort:
            raise ValueError("manifest reasoning_effort must match policy.reasoning_effort")
        if self.completed_wall_time_ns < self.started_wall_time_ns:
            raise ValueError("session completion time precedes start time")
        if self.status == "completed":
            if self.terminal_turn_id is None:
                raise ValueError("completed session requires terminal_turn_id")
            if self.failure_code is not None or self.failure_detail is not None:
                raise ValueError("completed session cannot contain failure fields")
            if self.usage.model_requests > self.policy.max_model_requests:
                raise ValueError("completed session exceeds model request policy")
            if self.usage.tool_calls > self.policy.max_tool_calls:
                raise ValueError("completed session exceeds tool call policy")
            if self.usage.image_observations > self.policy.max_image_observations:
                raise ValueError("completed session exceeds image observation policy")
        elif self.failure_code is None or self.failure_detail is None:
            raise ValueError("failed session requires failure_code and failure_detail")
        return self


class InteractionRecord(StrictModel):
    schema_version: Literal["aero-bench.agent-interaction/v1"]
    sequence: Annotated[int, Field(ge=1)]
    run_id: Sha256
    attempt_id: Identifier
    session_id: OpaqueId
    turn_id: OpaqueId | None
    call_id: OpaqueId | None
    record_type: Literal[
        "model.request",
        "model.response",
        "tool.call",
        "tool.result",
        "session.complete",
        "session.failed",
    ]
    wall_time_ns: Annotated[int, Field(ge=0)]
    sim_time: SimulationTime | None
    payload: JsonObject
    payload_digest: Sha256
    previous_record_hash: Sha256
    record_hash: Sha256

    @field_validator("payload")
    @classmethod
    def payload_is_secret_free_json(cls, value: JsonObject) -> JsonObject:
        _validate_json_value(value, label="interaction payload", reject_secrets=True)
        return value

    @model_validator(mode="after")
    def identifiers_and_hashes_are_exact(self) -> "InteractionRecord":
        if _sha256(canonical_json_bytes(self.payload)) != self.payload_digest:
            raise ValueError("interaction payload_digest does not match payload")
        needs_turn = self.record_type in {
            "model.request",
            "model.response",
            "tool.call",
            "tool.result",
        }
        needs_call = self.record_type in {"tool.call", "tool.result"}
        if (self.turn_id is not None) != needs_turn:
            raise ValueError("interaction turn_id does not match record_type")
        if (self.call_id is not None) != needs_call:
            raise ValueError("interaction call_id does not match record_type")
        body = self.model_dump(mode="json", exclude={"record_hash"})
        if _sha256(canonical_json_bytes(body)) != self.record_hash:
            raise ValueError("interaction record_hash does not match record")
        return self


def tool_catalog_digest(tools: tuple[FunctionTool, ...] | list[FunctionTool]) -> str:
    return _sha256(
        canonical_json_bytes([tool.model_dump(mode="json") for tool in tools])
    )


def text_digest(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def tool_visible_result_digest(
    *, success: bool, content: tuple[ToolContent, ...] | list[ToolContent]
) -> str:
    return _sha256(
        canonical_json_bytes(
            {
                "success": success,
                "content": [item.model_dump(mode="json") for item in content],
            }
        )
    )


__all__ = [
    "AgentSessionManifest",
    "FunctionTool",
    "GatewayCallAudit",
    "InteractionRecord",
    "OpaqueId",
    "SessionDescriptor",
    "SessionPolicy",
    "SessionUsage",
    "ToolCallRequest",
    "ToolContent",
    "ToolExecutionAudit",
    "ToolExecutionResult",
    "ToolImageContent",
    "ToolTextContent",
    "text_digest",
    "tool_catalog_digest",
    "tool_visible_result_digest",
]
