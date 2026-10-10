from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest

from aero_bench.agent.codex_driver import (
    SessionLogWriter,
    interaction_log_from_jsonl_bytes,
)
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    GatewayCallAudit,
    SessionUsage,
    ToolExecutionAudit,
    ToolExecutionResult,
    ToolImageContent,
    ToolTextContent,
    tool_visible_result_digest,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.formal_v2_sealed import (
    _canonical_model_artifact_bytes,
    _validate_model_disarm_after_stopped_dwell,
    _validate_model_session_evidence,
)
from aero_bench.tasks.inspection.sealed_evidence import _validate_model_log_manifest
from tests.agent.support import descriptor


def _completed_log(tmp_path):
    task = descriptor()
    writer = SessionLogWriter(
        root=tmp_path,
        descriptor=task,
        session_id="session_test",
        maximum_bytes=1024 * 1024,
        wall_time_ns=lambda: 10,
    )
    writer.set_turn_id("turn_test")
    writer.record_model_request(
        {
            "request_index": 1,
            "request_digest": "2" * 64,
            "request_size_bytes": 100,
            "model": task.policy.model,
            "reasoning_effort": task.policy.reasoning_effort,
            "instructions_sha256": task.instruction_sha256,
            "initial_input_sha256": task.initial_input_sha256,
            "tools_digest": task.tools_digest,
            "history_item_types": [],
            "image_inputs": [],
        }
    )
    writer.record_model_response(
        {
            "request_index": 1,
            "response_id": "response_test",
            "response_digest": "3" * 64,
            "response_size_bytes": 200,
            "model": task.policy.model,
            "function_calls": [],
            "assistant_messages": [],
            "usage": {
                "input_tokens": 5,
                "cached_input_tokens": 2,
                "output_tokens": 3,
            },
        }
    )
    writer.append(
        "session.complete",
        {
            "app_server_thread_id": "thread_test",
            "terminal_turn_id": "turn_test",
            "model_requests": 1,
            "tool_calls": 0,
            "image_observations": 0,
        },
    )
    writer.close()
    raw = (tmp_path / SessionLogWriter.INTERACTIONS_NAME).read_bytes()
    records = interaction_log_from_jsonl_bytes(
        raw,
        expected_run_id=task.run_id,
        expected_attempt_id=task.attempt_id,
    )
    manifest = AgentSessionManifest(
        schema_version="aero-bench.agent-session-manifest/v1",
        run_id=task.run_id,
        attempt_id=task.attempt_id,
        session_id="session_test",
        task_id=task.task_id,
        agent_id=task.agent_id,
        driver_id=task.driver_id,
        status="completed",
        model=task.policy.model,
        reasoning_effort=task.policy.reasoning_effort,
        codex_cli_version="test",
        codex_binary_sha256="4" * 64,
        policy=task.policy,
        instruction_sha256=task.instruction_sha256,
        initial_input_sha256=task.initial_input_sha256,
        tools_digest=task.tools_digest,
        started_wall_time_ns=9,
        completed_wall_time_ns=11,
        terminal_turn_id="turn_test",
        failure_code=None,
        failure_detail=None,
        usage=SessionUsage(
            model_requests=1,
            tool_calls=0,
            image_observations=0,
            input_tokens=5,
            cached_input_tokens=2,
            output_tokens=3,
            total_tokens=8,
        ),
        interactions_sha256=hashlib.sha256(raw).hexdigest(),
        interactions_size_bytes=len(raw),
        log_record_count=len(records),
        log_chain_root=records[-1].record_hash,
    )
    return manifest, records, raw


def test_model_manifest_closes_over_exact_interaction_log(tmp_path) -> None:
    manifest, records, raw = _completed_log(tmp_path)

    _validate_model_log_manifest(
        manifest=manifest,
        records=records,
        interactions_raw=raw,
    )

    with pytest.raises(ValueError, match="does not close"):
        _validate_model_log_manifest(
            manifest=manifest.model_copy(
                update={"interactions_sha256": "5" * 64}
            ),
            records=records,
            interactions_raw=raw,
        )


def test_model_authored_artifact_json_is_strict_and_canonicalized() -> None:
    encoded, parsed = _canonical_model_artifact_bytes(
        '[ {"frame_id": "frame.1", "defect_id": "defect.1"} ]'
    )

    assert parsed == [{"frame_id": "frame.1", "defect_id": "defect.1"}]
    assert encoded == b'[{"defect_id":"defect.1","frame_id":"frame.1"}]'
    with pytest.raises(ValueError, match="strict JSON"):
        _canonical_model_artifact_bytes('[{"frame_id":"a","frame_id":"b"}]')


def _tool_result(
    *,
    operation: str,
    operation_id: str,
    content: tuple[ToolTextContent | ToolImageContent, ...],
    observation_id: str | None = None,
    gateway_calls: tuple[GatewayCallAudit, ...] = (),
) -> ToolExecutionResult:
    at = SimulationTime(tick=1, sim_time_ns=1)
    return ToolExecutionResult(
        schema_version="aero-bench.agent-tool-execution-result/v1",
        success=True,
        content=content,
        audit=ToolExecutionAudit(
            operation_id=operation_id,
            operation=operation,
            command_id=None,
            query_id=None,
            observation_id=observation_id,
            issued_at=at,
            completed_at=at,
            result_digest=tool_visible_result_digest(success=True, content=content),
            gateway_calls=gateway_calls,
        ),
    )


def test_formal_model_proof_binds_outbound_image_and_authored_artifacts() -> None:
    image = b"\x89PNG\r\n\x1a\nsealed-frame"
    image_sha256 = hashlib.sha256(image).hexdigest()
    observation_digest = "6" * 64
    response_json = canonical_json_bytes(
        {
            "frame_id": "frame.1",
            "image_sha256": image_sha256,
            "payload_digest": observation_digest,
        }
    ).decode("utf-8")
    request_json = '{"observation_id":"observation.1"}'
    gateway = GatewayCallAudit(
        operation="observation.get",
        request_json=request_json,
        request_digest=hashlib.sha256(request_json.encode()).hexdigest(),
        response_json=response_json,
        response_digest=hashlib.sha256(response_json.encode()).hexdigest(),
        issued_at=SimulationTime(tick=1, sim_time_ns=1),
        completed_at=SimulationTime(tick=1, sim_time_ns=1),
    )
    image_content = (
        ToolTextContent(type="inputText", text='{"frame_id":"frame.1"}'),
        ToolImageContent(
            type="inputImage",
            imageUrl="data:image/png;base64," + base64.b64encode(image).decode(),
        ),
    )
    image_result = _tool_result(
        operation="read_observation_1",
        operation_id="operation.image",
        content=image_content,
        observation_id="observation.1",
        gateway_calls=(gateway,),
    )
    provenance = image_result.image_provenance()
    assert provenance is not None
    image_input = {
        "source_call_id": "call_image",
        "content_index": 1,
        "operation_id": image_result.audit.operation_id,
        "observation_id": "observation.1",
        **provenance,
        "media_type": "image/png",
        "size_bytes": len(image),
    }
    detection_content = "[]"
    report_content = '[{"schema_version":"aero-bench.inspection-report/v2"}]'
    detection_bytes = canonical_json_bytes([])
    report_bytes = canonical_json_bytes(
        [{"schema_version": "aero-bench.inspection-report/v2"}]
    )
    detection_write_content = (
        ToolTextContent(
            type="inputText",
            text=canonical_json_bytes(
                {
                    "artifact_id": "artifact.detection",
                    "sha256": hashlib.sha256(detection_bytes).hexdigest(),
                    "size_bytes": len(detection_bytes),
                }
            ).decode("utf-8"),
        ),
    )
    report_write_content = (
        ToolTextContent(
            type="inputText",
            text=canonical_json_bytes(
                {
                    "artifact_id": "artifact.report",
                    "sha256": hashlib.sha256(report_bytes).hexdigest(),
                    "size_bytes": len(report_bytes),
                }
            ).decode("utf-8"),
        ),
    )
    detection_result = _tool_result(
        operation="write_json_artifact",
        operation_id="operation.detection",
        content=detection_write_content,
    )
    report_result = _tool_result(
        operation="write_json_artifact",
        operation_id="operation.report",
        content=report_write_content,
    )
    records = (
        SimpleNamespace(
            record_type="tool.result",
            call_id="call_image",
            payload={"result": image_result.model_dump(mode="json")},
            sequence=4,
        ),
        SimpleNamespace(
            record_type="model.request",
            call_id=None,
            payload={"image_inputs": [image_input]},
            sequence=5,
        ),
        SimpleNamespace(
            record_type="tool.call",
            call_id="call_detection",
            payload={
                "name": "write_json_artifact",
                "arguments": {
                    "artifact_id": "artifact.detection",
                    "content_json": detection_content,
                },
            },
            sequence=7,
        ),
        SimpleNamespace(
            record_type="tool.result",
            call_id="call_detection",
            payload={"result": detection_result.model_dump(mode="json")},
            sequence=8,
        ),
        SimpleNamespace(
            record_type="tool.call",
            call_id="call_report",
            payload={
                "name": "write_json_artifact",
                "arguments": {
                    "artifact_id": "artifact.report",
                    "content_json": report_content,
                },
            },
            sequence=9,
        ),
        SimpleNamespace(
            record_type="tool.result",
            call_id="call_report",
            payload={"result": report_result.model_dump(mode="json")},
            sequence=10,
        ),
    )
    driver_requirements = (
        SimpleNamespace(
            artifact_type="model.session-manifest",
            artifact_id="artifact.model-session",
        ),
        SimpleNamespace(
            artifact_type="model.interactions",
            artifact_id="artifact.model-interactions",
        ),
    )
    run = SimpleNamespace(
        agents=(
            SimpleNamespace(
                agent_id="agent.1",
                driver=SimpleNamespace(
                    driver_id="driver.1",
                    artifact_requirements=driver_requirements,
                ),
            ),
        )
    )
    package = SimpleNamespace(
        actors=(SimpleNamespace(actor_id="agent.1", role="agent"),)
    )
    evidence = SimpleNamespace(
        model_session=SimpleNamespace(
            manifest=SimpleNamespace(
                status="completed",
                agent_id="agent.1",
                driver_id="driver.1",
            )
        ),
        model_interactions=SimpleNamespace(records=records),
        bindings=(
            SimpleNamespace(
                evidence_kind="model_session_manifest",
                artifact_id="artifact.model-session",
                producer_id="driver.1",
            ),
            SimpleNamespace(
                evidence_kind="model_interactions",
                artifact_id="artifact.model-interactions",
                producer_id="driver.1",
            ),
            SimpleNamespace(
                evidence_kind="detection",
                artifact_id="artifact.detection",
                producer_id="agent.1",
            ),
            SimpleNamespace(
                evidence_kind="report",
                artifact_id="artifact.report",
                producer_id="agent.1",
            ),
        ),
        sensor_frames=(
            SimpleNamespace(
                frame_id="frame.1",
                image_sha256=image_sha256,
                size_bytes=len(image),
                observation_id="observation.1",
                payload_digest=observation_digest,
            ),
        ),
        camera_frames=(SimpleNamespace(frame_id="frame.1", png_bytes=image),),
        observations=(
            SimpleNamespace(
                frame_id="frame.1",
                payload_digest=observation_digest,
            ),
        ),
        seal=SimpleNamespace(
            artifacts=(
                SimpleNamespace(
                    artifact_id="artifact.detection",
                    sha256=hashlib.sha256(detection_bytes).hexdigest(),
                    size_bytes=len(detection_bytes),
                ),
                SimpleNamespace(
                    artifact_id="artifact.report",
                    sha256=hashlib.sha256(report_bytes).hexdigest(),
                    size_bytes=len(report_bytes),
                ),
            )
        ),
    )

    presentations, artifact_sequences = _validate_model_session_evidence(
        package=package,
        evidence=evidence,
        run=run,
    )

    assert {(item.frame_id, item.request_sequence) for item in presentations} == {
        ("frame.1", 5)
    }
    assert artifact_sequences == (7, 8, 9, 10)

    evidence.sensor_frames = (
        SimpleNamespace(
            frame_id="frame.1",
            image_sha256="7" * 64,
            size_bytes=len(image),
            observation_id="observation.1",
            payload_digest=observation_digest,
        ),
    )
    with pytest.raises(ValueError, match="differs from sealed RGB"):
        _validate_model_session_evidence(
            package=package,
            evidence=evidence,
            run=run,
        )


def test_model_disarm_must_follow_verified_stopped_dwell() -> None:
    call_event = SimpleNamespace(
        event_id="event.disarm-call",
        time=SimulationTime(tick=20, sim_time_ns=2_000_000_000),
    )
    evidence = SimpleNamespace(
        model_session=SimpleNamespace(),
        event_ledger=SimpleNamespace(
            records=(SimpleNamespace(event=call_event),)
        ),
    )
    trace = SimpleNamespace(
        samples=(SimpleNamespace(sequence=20, sim_time_s=2.0),)
    )
    evaluation = SimpleNamespace(
        components=(
            SimpleNamespace(
                component_id="stopped_dwell",
                passed=True,
                witness=SimpleNamespace(sample_sequences=(0, 20)),
            ),
            SimpleNamespace(
                component_id="terminal_disarm",
                passed=True,
                witness=SimpleNamespace(sample_sequences=(20,)),
            ),
        )
    )
    witnesses = (
        SimpleNamespace(
            tool_id="flight.disarm",
            event_ids=(call_event.event_id,),
        ),
    )
    config = SimpleNamespace(stopped_dwell_s=2.0)

    _validate_model_disarm_after_stopped_dwell(
        evidence=evidence,
        trace=trace,
        evaluation=evaluation,
        witnesses=witnesses,
        config=config,
    )

    call_event.time = SimulationTime(tick=19, sim_time_ns=1_900_000_000)
    with pytest.raises(ValueError, match="after the verified stopped dwell"):
        _validate_model_disarm_after_stopped_dwell(
            evidence=evidence,
            trace=trace,
            evaluation=evaluation,
            witnesses=witnesses,
            config=config,
        )
