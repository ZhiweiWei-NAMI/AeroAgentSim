from __future__ import annotations

import base64
import hashlib

import pytest
from pydantic import ValidationError

from aero_bench.agent.session_contracts import (
    GatewayCallAudit,
    SessionDescriptor,
    SessionPolicy,
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_session_descriptor_is_strict_digest_bound_and_schema_generatable() -> None:
    value = descriptor()
    restored = SessionDescriptor.model_validate(value.model_dump(mode="json"))
    assert restored == value
    assert "tools_digest" in SessionDescriptor.model_json_schema()["properties"]
    bad = value.model_dump(mode="json")
    bad["instruction_sha256"] = "2" * 64
    with pytest.raises(ValidationError, match="instruction_sha256"):
        SessionDescriptor.model_validate(bad)
    extra = value.model_dump(mode="json")
    extra["legacy_fallback"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        SessionDescriptor.model_validate(extra)


def test_policy_rejects_coercion_wrong_model_and_unbounded_values() -> None:
    value = descriptor().policy.model_dump(mode="json")
    value["max_tool_calls"] = "20"
    with pytest.raises(ValidationError):
        SessionPolicy.model_validate(value)
    value = descriptor().policy.model_dump(mode="json")
    value["model"] = "gpt-6"
    with pytest.raises(ValidationError):
        SessionPolicy.model_validate(value)
    value = descriptor().policy.model_dump(mode="json")
    value["session_wall_timeout_s"] = 86_401
    with pytest.raises(ValidationError):
        SessionPolicy.model_validate(value)


def test_native_call_ids_are_opaque_bounded_and_not_identifiers() -> None:
    request = ToolCallRequest(
        call_id="01a09420-f7a1-7222-9581-03be9ea27a98",
        name="flight.observe",
        arguments={"scope": "public"},
    )
    assert request.call_id.startswith("01")
    with pytest.raises(ValidationError):
        ToolCallRequest(
            call_id="../../escape",
            name="flight.observe",
            arguments={"scope": "public"},
        )


def test_gateway_audit_rejects_noncanonical_frames_and_credentials() -> None:
    at = SimulationTime(tick=1, sim_time_ns=2)
    response = canonical_json_bytes({"ready": True}).decode()
    request = canonical_json_bytes({"scope": "public"}).decode()
    audit = GatewayCallAudit(
        operation="observation.get",
        request_json=request,
        request_digest=_digest(request),
        response_json=response,
        response_digest=_digest(response),
        issued_at=at,
        completed_at=at,
    )
    assert audit.operation == "observation.get"
    with pytest.raises(ValidationError, match="canonical"):
        GatewayCallAudit(
            **{
                **audit.model_dump(mode="json"),
                "request_json": '{"scope": "public"}',
            }
        )
    secret_request = canonical_json_bytes(
        {"scope": "public", "session_token": "never-log-me"}
    ).decode()
    with pytest.raises(ValidationError, match="credential-bearing"):
        GatewayCallAudit(
            **{
                **audit.model_dump(mode="json"),
                "request_json": secret_request,
                "request_digest": _digest(secret_request),
            }
        )


def test_image_result_requires_unique_gateway_provenance_and_exact_png() -> None:
    png = b"\x89PNG\r\n\x1a\nsource-frame"
    image_sha = hashlib.sha256(png).hexdigest()
    payload_sha = "3" * 64
    request_json = canonical_json_bytes(
        {"observation_id": "observation.image-1"}
    ).decode()
    response_json = canonical_json_bytes(
        {
            "payload": [
                {"name": "frame_id", "value": "camera-frame-1"},
                {"name": "image_sha256", "value": image_sha},
            ],
            "payload_digest": payload_sha,
        }
    ).decode()
    at = SimulationTime(tick=4, sim_time_ns=8)
    gateway = GatewayCallAudit(
        operation="observation.get",
        request_json=request_json,
        request_digest=_digest(request_json),
        response_json=response_json,
        response_digest=_digest(response_json),
        issued_at=at,
        completed_at=at,
    )
    image = ToolImageContent(
        type="inputImage",
        imageUrl="data:image/png;base64," + base64.b64encode(png).decode(),
    )
    content = (ToolTextContent(type="inputText", text="camera frame"), image)
    result = ToolExecutionResult(
        schema_version="aero-bench.agent-tool-execution-result/v1",
        success=True,
        content=content,
        audit=ToolExecutionAudit(
            operation_id="operation.image-1",
            operation="flight.observe",
            command_id=None,
            query_id=None,
            observation_id="observation.image-1",
            issued_at=at,
            completed_at=at,
            result_digest=tool_visible_result_digest(success=True, content=content),
            gateway_calls=(gateway,),
        ),
    )
    assert result.image_provenance() == {
        "frame_id": "camera-frame-1",
        "image_sha256": image_sha,
        "observation_payload_digest": payload_sha,
        "gateway_response_digest": gateway.response_digest,
    }
    bad = result.model_dump(mode="json")
    bad["audit"]["observation_id"] = None
    with pytest.raises(ValidationError, match="observation_id"):
        ToolExecutionResult.model_validate(bad)
