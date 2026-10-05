from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from aero_bench.agent.bridge import compile_tools
from aero_bench.agent.session_contracts import (
    GatewayCallAudit,
    ToolExecutionAudit,
    ToolExecutionResult,
    ToolTextContent,
    tool_visible_result_digest,
)
from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentDriverSpec,
    ArtifactRequirement,
    FileRef,
    NamedValue,
    RuntimeSpec,
    SchemaBoundFile,
)
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.gateway.contracts import ObservationEnvelope, QueryRequest, QueryResult
from aero_bench.providers.px4_gazebo.observations import (
    FlightGnssObservation,
    FlightTelemetryObservation,
)
from aero_bench.runtime.contracts import (
    AgentTurnCompletion,
    AgentTurnDecision,
    CommandReceipt,
    CommandRequest,
    SimulationTime,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.inspection.model_gateway_evidence import (
    _VerifiedGatewayCall,
    _validate_command_audit,
    _validate_command_status_audit,
    _validate_observation_audit,
    _validate_query_audit,
    _validate_required_inspection_api_usage,
    _validate_turn_audit,
    validate_manifest_tools_digest,
    validate_model_gateway_evidence,
)
from aero_bench.world.resolved import scenario_assets_for_workload
from tests.support import build_bundle, resolve_bundle, runtime, write_text


RUN_ID = "1" * 64
AUTHENTICATION_ID = "authentication.test"
AGENT_ID = "agent.test"
PROVIDER_ID = "provider.test"
AT_ZERO = SimulationTime(tick=0, sim_time_ns=0)
AT_ONE = SimulationTime(tick=1, sim_time_ns=100)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _file(path: str, character: str) -> FileRef:
    return FileRef(path=path, sha256=character * 64)


def _payload(values: dict[str, object]):
    return tuple(
        SimpleNamespace(name=name, value=value) for name, value in values.items()
    )


def _event(
    event_id: str,
    event_type: str,
    *,
    payload: dict[str, object],
    time: SimulationTime = AT_ZERO,
    source: str = "gateway",
    source_kind: str = "gateway",
    workload_id: str = "gateway",
    correlation_id: str = "correlation.test",
    parent_event_id: str | None = None,
    causal_event_ids: tuple[str, ...] = (),
    agent_id: str | None = AGENT_ID,
    provider_id: str | None = PROVIDER_ID,
    command_id: str | None = None,
    query_id: str | None = None,
    observation_id: str | None = None,
    interaction_type: str | None = None,
):
    return SimpleNamespace(
        event_id=event_id,
        event_type=event_type,
        payload=_payload(payload),
        time=time,
        source=source,
        source_kind=source_kind,
        workload_id=workload_id,
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
        causal_event_ids=causal_event_ids,
        agent_id=agent_id,
        provider_id=provider_id,
        command_id=command_id,
        query_id=query_id,
        observation_id=observation_id,
        interaction=(
            None
            if interaction_type is None
            else SimpleNamespace(interaction_type=interaction_type)
        ),
        run_id=RUN_ID,
    )


def _records(*events):
    return tuple(
        SimpleNamespace(event=event, sequence=index)
        for index, event in enumerate(events)
    )


def _audit(
    operation: str,
    request: dict[str, object],
    response: dict[str, object],
    *,
    issued=AT_ZERO,
    completed=AT_ZERO,
) -> GatewayCallAudit:
    request_json = canonical_json_bytes(request).decode("utf-8")
    response_json = canonical_json_bytes(response).decode("utf-8")
    return GatewayCallAudit(
        operation=operation,
        request_json=request_json,
        request_digest=hashlib.sha256(request_json.encode()).hexdigest(),
        response_json=response_json,
        response_digest=hashlib.sha256(response_json.encode()).hexdigest(),
        issued_at=issued,
        completed_at=completed,
    )


def _outer(
    operation: str,
    operation_id: str,
    audit: GatewayCallAudit,
    *,
    command_id: str | None = None,
    query_id: str | None = None,
    observation_id: str | None = None,
    success: bool = True,
) -> ToolExecutionResult:
    content = (ToolTextContent(type="inputText", text=audit.response_json),)
    return ToolExecutionResult(
        schema_version="aero-bench.agent-tool-execution-result/v1",
        success=success,
        content=content,
        audit=ToolExecutionAudit(
            operation_id=operation_id,
            operation=operation,
            command_id=command_id,
            query_id=query_id,
            observation_id=observation_id,
            issued_at=audit.issued_at,
            completed_at=audit.completed_at,
            result_digest=tool_visible_result_digest(success=success, content=content),
            gateway_calls=(audit,),
        ),
    )


def _run(*, tools=(), queries=(), observations=(), provider_ids=(PROVIDER_ID,)):
    agent = SimpleNamespace(
        agent_id=AGENT_ID,
        driver=SimpleNamespace(driver_id="driver.test"),
        tools=tools,
        queries=queries,
        observations=observations,
    )
    return SimpleNamespace(
        run_id=RUN_ID,
        agents=(agent,),
        environment=SimpleNamespace(
            providers=tuple(
                SimpleNamespace(provider_id=provider_id) for provider_id in provider_ids
            )
        ),
    )


def test_command_and_status_audits_require_exact_ledger_result_and_receipt() -> None:
    request_schema = _file("schemas/command-request.json", "2")
    response_schema = _file("schemas/command-response.json", "3")
    grant = SimpleNamespace(
        tool_id="flight.goto",
        provider_id=PROVIDER_ID,
        request_schema=request_schema,
        response_schema=response_schema,
        timeout_ms=1000,
    )
    run = _run(tools=(grant,))
    command_id = "call.command"
    request = CommandRequest(
        run_id=RUN_ID,
        command_id=command_id,
        agent_id=AGENT_ID,
        tool_id=grant.tool_id,
        issued_at=AT_ZERO,
        arguments=(NamedValue(name="target_id", value="target.1"),),
    )
    receipt = CommandReceipt(
        run_id=RUN_ID,
        command_id=command_id,
        provider_id=PROVIDER_ID,
        phase="completed",
        time=AT_ZERO,
        detail="arrived",
    )
    result = {
        "command_id": command_id,
        "receipts": [receipt.model_dump(mode="json")],
        "response": [{"name": "accepted", "value": True}],
        "observation": None,
    }
    request_digest = _digest(request.model_dump(mode="json"))
    issued = _event(
        "event.command-issued",
        "command.issued",
        source=AGENT_ID,
        source_kind="agent",
        workload_id=AGENT_ID,
        correlation_id=command_id,
        command_id=command_id,
        interaction_type="agent.tool_call.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "command_id": command_id,
            "tool_id": grant.tool_id,
            "provider_id": PROVIDER_ID,
            "request_digest": request_digest,
            "request_schema_digest": request_schema.sha256,
            "response_schema_digest": response_schema.sha256,
            "arguments_json": canonical_json_bytes(
                [item.model_dump(mode="json") for item in request.arguments]
            ).decode(),
        },
    )
    dispatch = _event(
        "event.dispatch",
        "gateway.dispatch",
        correlation_id=command_id,
        parent_event_id=issued.event_id,
        causal_event_ids=(issued.event_id,),
        command_id=command_id,
        interaction_type="gateway.dispatch.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "command_id": command_id,
            "tool_id": grant.tool_id,
            "provider_id": PROVIDER_ID,
            "request_digest": request_digest,
            "timeout_ms": 1000,
        },
    )
    response_values = result["response"]
    receipt_event_id = "event.receipt"
    validated = _event(
        "event.command-validated",
        "command.validated",
        correlation_id=command_id,
        causal_event_ids=(issued.event_id, dispatch.event_id, receipt_event_id),
        command_id=command_id,
        interaction_type="agent.tool_result.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "command_id": command_id,
            "tool_id": grant.tool_id,
            "provider_id": PROVIDER_ID,
            "request_digest": request_digest,
            "result_digest": _digest(result),
            "response_digest": _digest(response_values),
            "response_schema_digest": response_schema.sha256,
            "response_json": canonical_json_bytes(response_values).decode(),
            "result_json": canonical_json_bytes(result).decode(),
            "outcome": "completed",
        },
    )
    receipt_event = _event(
        receipt_event_id,
        "command.receipt",
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        correlation_id=command_id,
        parent_event_id=dispatch.event_id,
        causal_event_ids=(dispatch.event_id,),
        command_id=command_id,
        interaction_type="provider.command.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "command_id": command_id,
            "tool_id": grant.tool_id,
            "provider_id": PROVIDER_ID,
            "request_digest": request_digest,
            "phase": "completed",
            "receipt_json": canonical_json_bytes(
                receipt.model_dump(mode="json")
            ).decode(),
        },
    )
    records = _records(issued, dispatch, receipt_event, validated)
    audit = _audit(
        "command.invoke",
        {"operation": "command.invoke", "command": request.model_dump(mode="json")},
        result,
    )
    outer = _outer(
        "action_flight_goto",
        command_id,
        audit,
        command_id=command_id,
    )

    consumed: set[str] = set()
    _validate_command_audit(
        audit,
        outer_result=outer,
        manifest=SimpleNamespace(agent_id=AGENT_ID),
        records=records,
        run=run,
        consumed=consumed,
    )
    assert consumed == {issued.event_id, dispatch.event_id, validated.event_id}

    status_audit = _audit(
        "command.status",
        {
            "operation": "command.status",
            "command_id": command_id,
            "requested_at": AT_ZERO.model_dump(mode="json"),
        },
        receipt.model_dump(mode="json"),
    )
    _validate_command_status_audit(
        status_audit,
        outer_result=_outer(
            "get_command_status",
            "call.status",
            status_audit,
            command_id=command_id,
        ),
        manifest=SimpleNamespace(agent_id=AGENT_ID),
        records=records,
        run=run,
    )

    forged_status = _audit(
        "command.status",
        {
            "operation": "command.status",
            "command_id": command_id,
            "requested_at": AT_ZERO.model_dump(mode="json"),
        },
        receipt.model_copy(update={"phase": "failed"}).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="receipt source"):
        _validate_command_status_audit(
            forged_status,
            outer_result=_outer(
                "get_command_status",
                "call.status-forged",
                forged_status,
                command_id=command_id,
            ),
            manifest=SimpleNamespace(agent_id=AGENT_ID),
            records=records,
            run=run,
        )


def _query_fixture():
    request_schema = _file("schemas/query-request.json", "4")
    response_schema = _file("schemas/query-response.json", "5")
    grant = SimpleNamespace(
        query_type="business.work-order",
        provider_id=PROVIDER_ID,
        request_schema=request_schema,
        response_schema=response_schema,
    )
    run = _run(queries=(grant,))
    request = QueryRequest(
        run_id=RUN_ID,
        query_id="call.query",
        agent_id=AGENT_ID,
        query_type=grant.query_type,
        issued_at=AT_ZERO,
        arguments=(NamedValue(name="work_order_id", value="order.1"),),
    )
    payload = {"status": "assigned", "target_id": "target.1"}
    result = QueryResult(
        run_id=RUN_ID,
        query_id=request.query_id,
        agent_id=AGENT_ID,
        query_type=grant.query_type,
        provider_id=PROVIDER_ID,
        observed_at=AT_ZERO,
        payload_schema=response_schema,
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in payload.items()
        ),
        payload_digest=_digest(payload),
    )
    request_digest = _digest(request.model_dump(mode="json"))
    requested = _event(
        "event.query-requested",
        "query.requested",
        source=AGENT_ID,
        source_kind="agent",
        workload_id=AGENT_ID,
        correlation_id=request.query_id,
        query_id=request.query_id,
        interaction_type="agent.query_call.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "query_id": request.query_id,
            "query_type": grant.query_type,
            "provider_id": PROVIDER_ID,
            "request_schema_digest": request_schema.sha256,
            "response_schema_digest": response_schema.sha256,
            "request_digest": request_digest,
            "request_json": canonical_json_bytes(
                request.model_dump(mode="json")
            ).decode(),
        },
    )
    validated = _event(
        "event.query-validated",
        "query.validated",
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        correlation_id=request.query_id,
        parent_event_id=requested.event_id,
        causal_event_ids=(requested.event_id,),
        query_id=request.query_id,
        interaction_type="agent.query_result.v1",
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "query_id": request.query_id,
            "query_type": grant.query_type,
            "provider_id": PROVIDER_ID,
            "request_schema_digest": request_schema.sha256,
            "response_schema_digest": response_schema.sha256,
            "request_digest": request_digest,
            "payload_digest": result.payload_digest,
            "outcome": "succeeded",
            "result_json": canonical_json_bytes(
                result.model_dump(mode="json")
            ).decode(),
        },
    )
    audit = _audit(
        "query.invoke",
        {"operation": "query.invoke", "query": request.model_dump(mode="json")},
        result.model_dump(mode="json"),
    )
    outer = _outer(
        "query_business_work_order",
        request.query_id,
        audit,
        query_id=request.query_id,
    )
    return run, request, result, requested, validated, audit, outer


def test_query_audit_is_exact_and_missing_api_event_is_rejected() -> None:
    run, _, _, requested, validated, audit, outer = _query_fixture()
    records = _records(requested, validated)
    consumed: set[str] = set()
    _validate_query_audit(
        audit,
        outer_result=outer,
        manifest=SimpleNamespace(agent_id=AGENT_ID),
        records=records,
        run=run,
        consumed=consumed,
    )
    assert consumed == {requested.event_id, validated.event_id}

    proposal = SimpleNamespace(
        record_type="model.response",
        sequence=1,
        payload={"function_calls": [{"call_id": "call.query"}]},
    )
    interaction = SimpleNamespace(
        record_type="tool.result",
        sequence=2,
        call_id="call.query",
        payload={"result": outer.model_dump(mode="json")},
    )
    manifest = SimpleNamespace(agent_id=AGENT_ID, status="failed")
    validate_model_gateway_evidence(
        manifest=manifest,
        interactions=(proposal, interaction),
        ledger_records=records,
        run=run,
        required_work_order_ids=(),
        required_vehicle_ids=(),
    )
    with pytest.raises(ValueError, match="cover every"):
        validate_model_gateway_evidence(
            manifest=manifest,
            interactions=(),
            ledger_records=records,
            run=run,
            required_work_order_ids=(),
            required_vehicle_ids=(),
        )


def test_failed_query_is_accepted_only_with_exact_failure_ledger() -> None:
    run, request, _, requested, _, _, _ = _query_fixture()
    failure_response = {
        "error": {"code": "dispatch.failed", "detail": "query dispatch failed"}
    }
    audit = _audit(
        "query.invoke",
        {"operation": "query.invoke", "query": request.model_dump(mode="json")},
        failure_response,
    )
    outer = _outer(
        "query_business_work_order",
        request.query_id,
        audit,
        query_id=request.query_id,
        success=False,
    )
    requested_payload = {
        item.name: item.value
        for item in requested.payload
        if item.name != "request_json"
    }
    failure = _event(
        "event.query-failed",
        "gateway.failure",
        correlation_id=request.query_id,
        parent_event_id=requested.event_id,
        causal_event_ids=(requested.event_id,),
        query_id=request.query_id,
        interaction_type="agent.query_result.v1",
        payload={
            **requested_payload,
            "failure_class": "provider_timeout",
            "outcome": "failed",
        },
    )
    consumed: set[str] = set()
    _validate_query_audit(
        audit,
        outer_result=outer,
        manifest=SimpleNamespace(agent_id=AGENT_ID),
        records=_records(requested, failure),
        run=run,
        consumed=consumed,
    )
    assert consumed == {requested.event_id, failure.event_id}

    failure.payload = _payload(
        {**requested_payload, "failure_class": "invented", "outcome": "failed"}
    )
    with pytest.raises(ValueError, match="failure differs"):
        _validate_query_audit(
            audit,
            outer_result=outer,
            manifest=SimpleNamespace(agent_id=AGENT_ID),
            records=_records(requested, failure),
            run=run,
            consumed=set(),
        )


def test_observation_audit_binds_complete_non_rgb_envelope() -> None:
    schema = _file("schemas/telemetry.json", "6")
    grant = SimpleNamespace(
        observation_id="flight.telemetry",
        provider_id=PROVIDER_ID,
        schema_file=schema,
    )
    run = _run(observations=(grant,))
    manifest = SimpleNamespace(agent_id=AGENT_ID)
    payload = {
        "latitude_deg": 31.2,
        "longitude_deg": 121.5,
        "relative_altitude_m": 8.0,
        "velocity_enu_json": '{"east_mps":1.0,"north_mps":0.0,"up_mps":0.0}',
    }
    envelope = ObservationEnvelope(
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        observation_id=grant.observation_id,
        time=AT_ZERO,
        payload_schema=schema,
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in payload.items()
        ),
        payload_digest=_digest(payload),
    )
    request_base = {
        "run_id": RUN_ID,
        "agent_id": AGENT_ID,
        "authentication_id": AUTHENTICATION_ID,
        "observation_id": grant.observation_id,
        "requested_at": AT_ZERO.model_dump(mode="json"),
    }
    request_digest = _digest(request_base)
    correlation_id = f"observation.{request_digest}"
    requested = _event(
        "event.observation-requested",
        "observation.requested",
        source=AGENT_ID,
        source_kind="agent",
        workload_id=AGENT_ID,
        correlation_id=correlation_id,
        observation_id=grant.observation_id,
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "authentication_id": AUTHENTICATION_ID,
            "observation_id": grant.observation_id,
            "provider_id": PROVIDER_ID,
            "schema_digest": schema.sha256,
            "request_digest": request_digest,
        },
    )
    validated_payload = {
        "run_id": RUN_ID,
        "agent_id": AGENT_ID,
        "authentication_id": AUTHENTICATION_ID,
        "observation_id": grant.observation_id,
        "provider_id": PROVIDER_ID,
        "schema_digest": schema.sha256,
        "schema_path": schema.path,
        "request_digest": request_digest,
        "payload_digest": envelope.payload_digest,
        "payload_json": canonical_json_bytes(payload).decode(),
        "envelope_json": canonical_json_bytes(
            envelope.model_dump(mode="json")
        ).decode(),
    }
    validated = _event(
        "event.observation-validated",
        "observation.validated",
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        correlation_id=correlation_id,
        parent_event_id=requested.event_id,
        causal_event_ids=(requested.event_id,),
        observation_id=grant.observation_id,
        interaction_type="agent.observation.v1",
        payload=validated_payload,
    )
    audit = _audit(
        "observation.get",
        {
            "operation": "observation.get",
            "observation_id": grant.observation_id,
            "requested_at": AT_ZERO.model_dump(mode="json"),
        },
        envelope.model_dump(mode="json"),
    )
    outer = _outer(
        "read_flight_telemetry",
        "call.telemetry",
        audit,
        observation_id=grant.observation_id,
    )
    _validate_observation_audit(
        audit,
        outer_result=outer,
        records=_records(requested, validated),
        run=run,
        manifest=manifest,
        consumed=set(),
    )

    forged = _event(
        "event.observation-forged",
        "observation.validated",
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        correlation_id=correlation_id,
        parent_event_id=requested.event_id,
        observation_id=grant.observation_id,
        interaction_type="agent.observation.v1",
        payload={**validated_payload, "envelope_json": "{}"},
    )
    with pytest.raises(ValueError, match="envelope differs"):
        _validate_observation_audit(
            audit,
            outer_result=outer,
            records=_records(requested, forged),
            run=run,
            manifest=manifest,
            consumed=set(),
        )


def test_turn_completion_requires_decision_and_every_provider_receipt() -> None:
    run = _run(provider_ids=("flight", "business"))
    manifest = SimpleNamespace(agent_id=AGENT_ID)
    completion = AgentTurnCompletion(
        schema_version="aero-bench.agent-turn-completion/v1",
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        completion_id="call.wait.tick.1",
        at=AT_ZERO,
        disposition="advance",
        command_ids=(),
        observation_ids=(),
    )
    decision = AgentTurnDecision(
        schema_version="aero-bench.agent-turn-decision/v1",
        run_id=RUN_ID,
        status="advanced",
        at=AT_ONE,
        active_agent_ids=(AGENT_ID,),
        missing_agent_ids=(AGENT_ID,),
    )
    completion_event = _event(
        "event.turn-completion",
        "agent.turn-completion",
        source=AGENT_ID,
        source_kind="agent",
        workload_id=AGENT_ID,
        correlation_id=completion.completion_id,
        provider_id=None,
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "completion_id": completion.completion_id,
            "disposition": "advance",
            "command_ids": "[]",
            "observation_ids": "[]",
        },
    )
    barrier = _event(
        "event.barrier",
        "barrier.committed",
        time=AT_ONE,
        source="harness",
        source_kind="harness",
        workload_id="harness",
        correlation_id="tick.1",
        agent_id=None,
        provider_id=None,
        payload={},
    )
    receipts = tuple(
        _event(
            f"event.receipt-{provider_id}",
            "provider.step-receipt",
            time=AT_ONE,
            source=provider_id,
            source_kind="provider",
            workload_id=provider_id,
            agent_id=None,
            provider_id=provider_id,
            payload={},
        )
        for provider_id in ("flight", "business")
    )
    decision_event = _event(
        "event.turn-decision",
        "agent.turn-decision",
        time=AT_ONE,
        source="harness",
        source_kind="harness",
        workload_id="harness",
        correlation_id="turn.1",
        parent_event_id=barrier.event_id,
        causal_event_ids=(completion_event.event_id, barrier.event_id),
        agent_id=None,
        provider_id=None,
        payload={
            "run_id": RUN_ID,
            "status": "advanced",
            "active_agent_ids": canonical_json_bytes([AGENT_ID]).decode(),
            "missing_agent_ids": canonical_json_bytes([AGENT_ID]).decode(),
        },
    )
    audit = _audit(
        "turn.complete",
        {
            "operation": "turn.complete",
            "completion": completion.model_dump(mode="json"),
        },
        decision.model_dump(mode="json"),
        issued=AT_ZERO,
        completed=AT_ONE,
    )
    records = _records(completion_event, *receipts, barrier, decision_event)
    outer = _outer("wait_duration", "call.wait", audit)
    _validate_turn_audit(
        audit,
        outer_result=outer,
        records=records,
        run=run,
        manifest=manifest,
        consumed=set(),
    )
    with pytest.raises(ValueError, match="every Provider"):
        _validate_turn_audit(
            audit,
            outer_result=outer,
            records=_records(completion_event, receipts[0], barrier, decision_event),
            run=run,
            manifest=manifest,
            consumed=set(),
        )


def test_turn_completion_uses_latest_same_tick_observation_event() -> None:
    run = _run()
    manifest = SimpleNamespace(agent_id=AGENT_ID)
    observation_id = "flight.telemetry.uav.inspector"
    completion = AgentTurnCompletion(
        schema_version="aero-bench.agent-turn-completion/v1",
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        completion_id="call.wait.tick.1",
        at=AT_ZERO,
        disposition="advance",
        command_ids=(),
        observation_ids=(observation_id,),
    )
    decision = AgentTurnDecision(
        schema_version="aero-bench.agent-turn-decision/v1",
        run_id=RUN_ID,
        status="advanced",
        at=AT_ONE,
        active_agent_ids=(AGENT_ID,),
        missing_agent_ids=(AGENT_ID,),
    )
    observations = tuple(
        _event(
            f"event.observation-validated-{index}",
            "observation.validated",
            observation_id=observation_id,
            interaction_type="agent.observation.v1",
            payload={},
        )
        for index in range(2)
    )
    completion_event = _event(
        "event.turn-completion",
        "agent.turn-completion",
        source=AGENT_ID,
        source_kind="agent",
        workload_id=AGENT_ID,
        correlation_id=completion.completion_id,
        parent_event_id=observations[-1].event_id,
        causal_event_ids=(observations[-1].event_id,),
        provider_id=None,
        payload={
            "run_id": RUN_ID,
            "agent_id": AGENT_ID,
            "completion_id": completion.completion_id,
            "disposition": "advance",
            "command_ids": "[]",
            "observation_ids": canonical_json_bytes([observation_id]).decode(),
        },
    )
    receipt = _event(
        "event.receipt-provider",
        "provider.step-receipt",
        time=AT_ONE,
        source=PROVIDER_ID,
        source_kind="provider",
        workload_id=PROVIDER_ID,
        agent_id=None,
        provider_id=PROVIDER_ID,
        payload={},
    )
    barrier = _event(
        "event.barrier",
        "barrier.committed",
        time=AT_ONE,
        source="harness",
        source_kind="harness",
        workload_id="harness",
        correlation_id="tick.1",
        agent_id=None,
        provider_id=None,
        payload={},
    )
    decision_event = _event(
        "event.turn-decision",
        "agent.turn-decision",
        time=AT_ONE,
        source="harness",
        source_kind="harness",
        workload_id="harness",
        correlation_id="turn.1",
        parent_event_id=barrier.event_id,
        causal_event_ids=(completion_event.event_id, barrier.event_id),
        agent_id=None,
        provider_id=None,
        payload={
            "run_id": RUN_ID,
            "status": "advanced",
            "active_agent_ids": canonical_json_bytes([AGENT_ID]).decode(),
            "missing_agent_ids": canonical_json_bytes([AGENT_ID]).decode(),
        },
    )
    audit = _audit(
        "turn.complete",
        {
            "operation": "turn.complete",
            "completion": completion.model_dump(mode="json"),
        },
        decision.model_dump(mode="json"),
        issued=AT_ZERO,
        completed=AT_ONE,
    )
    _validate_turn_audit(
        audit,
        outer_result=_outer("wait_duration", "call.wait", audit),
        records=_records(
            *observations,
            completion_event,
            receipt,
            barrier,
            decision_event,
        ),
        run=run,
        manifest=manifest,
        consumed=set(),
    )


def test_manifest_tools_digest_is_recompiled_from_current_workload_grants(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    driver_schema = write_text(
        tmp_path,
        "schemas/driver-config.json",
        '{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}',
    )
    driver_config = write_text(
        tmp_path,
        "agents/driver-config.json",
        '{"schema_version":"aero-bench.agent-driver-config/v1"}',
    )
    tool_schema = write_text(
        tmp_path,
        "schemas/driver-tool-request.json",
        canonical_json_bytes(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"target_id": {"type": "string", "minLength": 1}},
                "required": ["target_id"],
                "additionalProperties": False,
            }
        ).decode(),
    )
    driver = AgentDriverSpec(
        driver_id="driver.test",
        workload=RuntimeSpec.model_validate(runtime("a", "driver.test")),
        config=SchemaBoundFile(
            file=FileRef.model_validate(driver_config),
            schema_file=FileRef.model_validate(driver_schema),
        ),
        bridge_port=18432,
        artifact_requirements=(
            ArtifactRequirement(
                artifact_id="artifact.model-session",
                artifact_type="model.session-manifest",
                producer_id="driver.test",
                visibility="private",
                relative_path="driver/session.json",
                max_size_bytes=1024 * 1024,
                source_asset_id=None,
            ),
            ArtifactRequirement(
                artifact_id="artifact.model-interactions",
                artifact_type="model.interactions",
                producer_id="driver.test",
                visibility="private",
                relative_path="driver/interactions.jsonl",
                max_size_bytes=64 * 1024 * 1024,
                source_asset_id=None,
            ),
        ),
    )
    agent = run.agents[0].model_copy(
        update={
            "driver": driver,
            "tools": tuple(
                grant.model_copy(
                    update={"request_schema": FileRef.model_validate(tool_schema)}
                )
                for grant in run.agents[0].tools
            ),
        }
    )
    run = run.model_copy(update={"agents": (agent,)})
    reader = BundleReader(tmp_path)
    contract = AgentWorkloadContract(
        schema_version="aero-bench.workload-contract/v5",
        role="agent",
        run_id=run.run_id,
        seed=run.seed,
        clock=run.environment.clock,
        workload_id=agent.agent_id,
        task_id=run.task.task_id,
        instruction=run.task.instruction,
        gateway=run.environment.gateway,
        agent=agent,
        scenario_digest=run.scenario.scenario_digest,
        scenario=run.scenario,
        scenario_assets=scenario_assets_for_workload(
            run.scenario,
            role="agent",
            workload_id=agent.agent_id,
        ),
    )

    def read_file(reference):
        return reader.resolve_file(reference).read_bytes()

    expected = _digest(
        [item.model_dump(mode="json") for item in compile_tools(contract, read_file)]
    )
    manifest = SimpleNamespace(
        agent_id=agent.agent_id,
        driver_id=driver.driver_id,
        tools_digest=expected,
    )
    validate_manifest_tools_digest(
        manifest=manifest,
        run=run,
        read_file=read_file,
    )

    changed_agent = agent.model_copy(update={"tools": ()})
    changed_run = run.model_copy(update={"agents": (changed_agent,)})
    with pytest.raises(ValueError, match="tools differ"):
        validate_manifest_tools_digest(
            manifest=manifest,
            run=changed_run,
            read_file=read_file,
        )


def _verified_call(
    sequence: int,
    operation: str,
    *,
    request: dict[str, object],
    response: dict[str, object] | None = None,
) -> _VerifiedGatewayCall:
    return _VerifiedGatewayCall(
        proposal_sequence=sequence,
        interaction_sequence=sequence,
        call_index=0,
        operation=operation,
        request={"operation": operation, **request},
        response={} if response is None else response,
    )


def _flight_payloads() -> tuple[dict[str, object], dict[str, object]]:
    telemetry = FlightTelemetryObservation(
        schema_version="aero-bench.observation.flight-telemetry/v1",
        vehicle_id="uav.inspector",
        observation_status="available",
        simulation_time_ns=0,
        source_timestamp_status="missing",
        source_timestamp_us=None,
        source_to_sim_time_mapping="unavailable",
        freshness_basis="latest_sample_received_before_barrier",
        sample_received_monotonic_ns=10,
        observation_monotonic_ns=11,
        receipt_age_ms=0.1,
        position_source="mavsdk.telemetry.position",
        longitude_deg=121.5,
        latitude_deg=31.2,
        absolute_altitude_m=50.0,
        absolute_altitude_reference="AMSL",
        relative_altitude_m=0.0,
        velocity_source="mavsdk.telemetry.position_velocity_ned",
        north_m_s=0.0,
        east_m_s=0.0,
        down_m_s=0.0,
        attitude_source="mavsdk.telemetry.attitude_euler",
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        flight_mode="HOLD",
        armed=False,
        in_air=False,
        landed=True,
        landed_state="ON_GROUND",
        battery_percent=90.0,
        health_source="mavsdk.telemetry.health",
        health_json='{"global_position_ok":true}',
    )
    gnss = FlightGnssObservation(
        schema_version="aero-bench.observation.flight-gnss/v1",
        vehicle_id="uav.inspector",
        observation_status="available",
        simulation_time_ns=0,
        source="mavsdk.telemetry.raw_gps+gps_info",
        source_timestamp_stream="raw_gps",
        source_timestamp_us=1,
        source_time_basis="unix_epoch_or_autopilot_boot_unspecified",
        gps_info_timestamp_status="missing",
        gps_info_timestamp_us=None,
        source_to_sim_time_mapping="unavailable",
        freshness_basis="latest_sample_received_before_barrier",
        sample_received_monotonic_ns=10,
        observation_monotonic_ns=11,
        receipt_age_ms=0.1,
        position_status="available",
        latitude_deg=31.2,
        longitude_deg=121.5,
        absolute_altitude_m=50.0,
        absolute_altitude_reference="AMSL",
        fix_type="FIX_3D",
        num_satellites=12,
        hdop_status="available",
        hdop=0.8,
        vdop_status="available",
        vdop=1.1,
        velocity_status="missing",
        velocity_m_s=None,
        course_over_ground_status="missing",
        course_over_ground_deg=None,
        ellipsoid_altitude_status="missing",
        altitude_ellipsoid_m=None,
        horizontal_uncertainty_status="missing",
        horizontal_uncertainty_m=None,
        vertical_uncertainty_status="missing",
        vertical_uncertainty_m=None,
        velocity_uncertainty_status="missing",
        velocity_uncertainty_m_s=None,
        heading_uncertainty_status="missing",
        heading_uncertainty_deg=None,
        yaw_status="missing",
        yaw_deg=None,
    )
    return telemetry.model_dump(mode="json"), gnss.model_dump(mode="json")


def _required_api_calls() -> tuple[_VerifiedGatewayCall, ...]:
    telemetry, gnss = _flight_payloads()
    telemetry_envelope = ObservationEnvelope(
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        observation_id="flight.telemetry.uav.inspector",
        time=AT_ZERO,
        payload_schema=_file("schemas/flight-telemetry.json", "8"),
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in telemetry.items()
        ),
        payload_digest=_digest(telemetry),
    )
    gnss_envelope = ObservationEnvelope(
        run_id=RUN_ID,
        agent_id=AGENT_ID,
        observation_id="flight.gnss.uav.inspector",
        time=AT_ZERO,
        payload_schema=_file("schemas/flight-gnss.json", "9"),
        payload=tuple(
            NamedValue(name=name, value=value) for name, value in gnss.items()
        ),
        payload_digest=_digest(gnss),
    )

    def query(sequence: int, query_type: str, payload, arguments=()):
        request = QueryRequest(
            run_id=RUN_ID,
            query_id=f"query.{sequence}",
            agent_id=AGENT_ID,
            query_type=query_type,
            issued_at=AT_ZERO,
            arguments=arguments,
        )
        result = QueryResult(
            run_id=RUN_ID,
            query_id=request.query_id,
            agent_id=AGENT_ID,
            query_type=query_type,
            provider_id=PROVIDER_ID,
            observed_at=AT_ZERO,
            payload_schema=_file(f"schemas/query-{sequence}.json", "7"),
            payload=tuple(
                NamedValue(name=name, value=value) for name, value in payload.items()
            ),
            payload_digest=_digest(payload),
        )
        return _verified_call(
            sequence,
            "query.invoke",
            request={"query": request.model_dump(mode="json")},
            response=result.model_dump(mode="json"),
        )

    def command(sequence: int, tool_id: str, arguments):
        request = CommandRequest(
            run_id=RUN_ID,
            command_id=f"command.{sequence}",
            agent_id=AGENT_ID,
            tool_id=tool_id,
            issued_at=AT_ZERO,
            arguments=arguments,
        )
        receipt = CommandReceipt(
            run_id=RUN_ID,
            command_id=request.command_id,
            provider_id=PROVIDER_ID,
            phase="accepted" if tool_id == "flight.arm" else "completed",
            time=AT_ZERO,
            detail=None,
        )
        response = {
            "command_id": request.command_id,
            "receipts": [receipt.model_dump(mode="json")],
            "response": [],
            "observation": None,
        }
        return _verified_call(
            sequence,
            "command.invoke",
            request={"command": request.model_dump(mode="json")},
            response=response,
        )

    return (
        query(
            1,
            "business.work-orders",
            {
                "work_order_count": 1,
                "work_orders_json": canonical_json_bytes(
                    [
                        {
                            "claimed_by": None,
                            "last_tick": 0,
                            "last_time_ns": 0,
                            "status": "created",
                            "target_id": "target.1",
                            "version": 0,
                            "work_order_id": "order.1",
                        }
                    ]
                ).decode(),
            },
        ),
        query(
            2,
            "business.work-order",
            {"work_order_id": "order.1", "target_id": "target.1"},
            (NamedValue(name="work_order_id", value="order.1"),),
        ),
        _verified_call(
            3,
            "observation.get",
            request={},
            response=telemetry_envelope.model_dump(mode="json"),
        ),
        _verified_call(
            4,
            "observation.get",
            request={},
            response=gnss_envelope.model_dump(mode="json"),
        ),
        command(
            5,
            "business.claim",
            (NamedValue(name="work_order_id", value="order.1"),),
        ),
        command(
            6,
            "flight.arm",
            (NamedValue(name="vehicle_id", value="uav.inspector"),),
        ),
    )


def test_required_business_and_gnss_api_usage() -> None:
    manifest = SimpleNamespace(status="completed")
    calls = _required_api_calls()
    _validate_required_inspection_api_usage(
        manifest=manifest,
        calls=calls,
        required_work_order_ids=("order.1",),
        required_vehicle_ids=("uav.inspector",),
    )

    with pytest.raises(ValueError, match="telemetry and valid raw GNSS"):
        _validate_required_inspection_api_usage(
            manifest=manifest,
            calls=tuple(call for call in calls if call.interaction_sequence != 4),
            required_work_order_ids=("order.1",),
            required_vehicle_ids=("uav.inspector",),
        )
    with pytest.raises(ValueError, match="detail or claim"):
        _validate_required_inspection_api_usage(
            manifest=manifest,
            calls=tuple(call for call in calls if call.interaction_sequence != 2),
            required_work_order_ids=("order.1",),
            required_vehicle_ids=("uav.inspector",),
        )

    summary = QueryResult.model_validate(calls[0].response)
    foreign_rows = {
        "work_order_count": 1,
        "work_orders_json": '[{"work_order_id":"order.foreign"}]',
    }
    foreign_summary = summary.model_copy(
        update={
            "payload": tuple(
                NamedValue(name=name, value=value)
                for name, value in foreign_rows.items()
            ),
            "payload_digest": _digest(foreign_rows),
        }
    )
    forged_discovery = _VerifiedGatewayCall(
        proposal_sequence=calls[0].proposal_sequence,
        interaction_sequence=calls[0].interaction_sequence,
        call_index=0,
        operation=calls[0].operation,
        request=calls[0].request,
        response=foreign_summary.model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="exactly cover"):
        _validate_required_inspection_api_usage(
            manifest=manifest,
            calls=(forged_discovery, *calls[1:]),
            required_work_order_ids=("order.1",),
            required_vehicle_ids=("uav.inspector",),
        )
