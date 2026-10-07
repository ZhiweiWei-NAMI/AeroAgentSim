from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aero_bench.agent.bridge import compile_tools
from aero_bench.agent.session_contracts import (
    AgentSessionManifest,
    GatewayCallAudit,
    InteractionRecord,
    ToolExecutionResult,
)
from aero_bench.config.models import FileRef
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.executor.contracts import AgentWorkloadContract
from aero_bench.gateway.contracts import (
    DecisionSummaryReceipt,
    DecisionSummaryRequest,
    ObservationEnvelope,
    QueryRequest,
    QueryResult,
    ToolResult,
)
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
from aero_bench.runtime.events import RunEvent
from aero_bench.runtime.ledger import LedgerRecord
from aero_bench.serialization import canonical_json_bytes
from aero_bench.world.resolved import scenario_assets_for_workload


_COMMAND_PRE_DISPATCH_FAILURES = frozenset(
    {
        "decision_summary_missing",
        "request_validation_failed",
        "provider_unavailable",
    }
)
_COMMAND_POST_DISPATCH_FAILURES = frozenset(
    {
        "provider_timeout",
        "provider_exception",
        "authority_advanced_during_call",
        "result_validation_failed",
    }
)
_QUERY_FAILURES = frozenset(
    {
        "request_validation_failed",
        "provider_unavailable",
        "provider_timeout",
        "provider_exception",
        "authority_advanced_during_call",
        "query_validation_failed",
    }
)
_OBSERVATION_FAILURES = frozenset(
    {
        "provider_unavailable",
        "provider_timeout",
        "provider_exception",
        "authority_advanced_during_call",
        "observation_validation_failed",
        "runtime_hook_failed",
    }
)
_GATEWAY_ERROR_RESPONSES = {
    "command.invoke": {
        "error": {"code": "dispatch.failed", "detail": "command dispatch failed"}
    },
    "command.status": {
        "error": {
            "code": "dispatch.failed",
            "detail": "command status lookup failed",
        }
    },
    "query.invoke": {
        "error": {"code": "dispatch.failed", "detail": "query dispatch failed"}
    },
    "observation.get": {
        "error": {
            "code": "dispatch.failed",
            "detail": "observation dispatch failed",
        }
    },
    "decision.summary": {
        "error": {
            "code": "dispatch.failed",
            "detail": "decision summary recording failed",
        }
    },
    "turn.complete": {
        "error": {"code": "turn.failed", "detail": "agent turn submission failed"}
    },
}
_AGENT_GATEWAY_EVENT_TYPES = frozenset(
    {
        "command.issued",
        "command.validated",
        "gateway.dispatch",
        "gateway.failure",
        "query.requested",
        "query.validated",
        "observation.requested",
        "observation.validated",
        "agent.decision-summary",
        "agent.turn-completion",
        "agent.turn-decision",
    }
)


@dataclass(frozen=True, slots=True)
class _VerifiedGatewayCall:
    proposal_sequence: int
    interaction_sequence: int
    call_index: int
    operation: str
    request: dict[str, Any]
    response: dict[str, Any]

    @property
    def order(self) -> tuple[int, int]:
        return self.interaction_sequence, self.call_index

    def consumed_before(self, other: "_VerifiedGatewayCall") -> bool:
        return self.interaction_sequence < other.proposal_sequence


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _json_object(value: str, *, label: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not JSON") from error
    if not isinstance(result, dict):
        raise ValueError(f"{label} is not a JSON object")
    return result


def _event_payload(event: RunEvent) -> dict[str, object]:
    payload = {item.name: item.value for item in event.payload}
    if len(payload) != len(event.payload):
        raise ValueError("authoritative Gateway event repeats a payload field")
    return payload


def _events(
    records: tuple[LedgerRecord, ...],
    event_type: str,
    *,
    agent_id: str | None = None,
) -> tuple[RunEvent, ...]:
    return tuple(
        record.event
        for record in records
        if record.event.event_type == event_type
        and (agent_id is None or record.event.agent_id == agent_id)
    )


def _one_event(
    records: tuple[LedgerRecord, ...],
    event_type: str,
    predicate: Callable[[RunEvent], bool],
    *,
    label: str,
) -> RunEvent:
    matches = tuple(event for event in _events(records, event_type) if predicate(event))
    if len(matches) != 1:
        raise ValueError(f"{label} does not have one authoritative RunEvent")
    return matches[0]


def _consume(consumed: set[str], *events: RunEvent) -> None:
    for event in events:
        if event.event_id in consumed:
            raise ValueError("one authoritative Gateway event proves multiple calls")
        consumed.add(event.event_id)


def _is_ancestor(
    ancestor_id: str,
    event: RunEvent,
    records: tuple[LedgerRecord, ...],
) -> bool:
    by_id = {record.event.event_id: record.event for record in records}
    pending = list(event.causal_event_ids)
    visited: set[str] = set()
    while pending:
        event_id = pending.pop()
        if event_id == ancestor_id:
            return True
        if event_id in visited:
            continue
        visited.add(event_id)
        ancestor = by_id.get(event_id)
        if ancestor is None:
            raise ValueError("Gateway event causality names an unknown RunEvent")
        pending.extend(ancestor.causal_event_ids)
    return False


def _failure_time_is_valid(
    *,
    failure_class: object,
    event_time: SimulationTime,
    audit: GatewayCallAudit,
) -> bool:
    if failure_class == "authority_advanced_during_call":
        return (event_time.tick, event_time.sim_time_ns) >= (
            audit.completed_at.tick,
            audit.completed_at.sim_time_ns,
        )
    return event_time == audit.completed_at


def _principal_authentication_id(event: RunEvent, *, agent_id: str) -> str:
    value = _event_payload(event).get("authentication_id")
    if not isinstance(value, str) or not value or event.agent_id != agent_id:
        raise ValueError("Gateway event has no authoritative Agent authentication")
    return value


def _grant_for_command(run: ResolvedRunSpec, request: CommandRequest):
    agent = next(
        (item for item in run.agents if item.agent_id == request.agent_id),
        None,
    )
    if agent is None:
        raise ValueError("Gateway command names an undeclared Agent")
    matches = tuple(item for item in agent.tools if item.tool_id == request.tool_id)
    if len(matches) != 1:
        raise ValueError("Gateway command does not name one resolved tool grant")
    return matches[0]


def _grant_for_query(run: ResolvedRunSpec, request: QueryRequest):
    agent = next(
        (item for item in run.agents if item.agent_id == request.agent_id),
        None,
    )
    if agent is None:
        raise ValueError("Gateway query names an undeclared Agent")
    matches = tuple(
        item for item in agent.queries if item.query_type == request.query_type
    )
    if len(matches) != 1:
        raise ValueError("Gateway query does not name one resolved query grant")
    return matches[0]


def _grant_for_observation(run: ResolvedRunSpec, agent_id: str, observation_id: str):
    agent = next((item for item in run.agents if item.agent_id == agent_id), None)
    if agent is None:
        raise ValueError("Gateway observation names an undeclared Agent")
    matches = tuple(
        item for item in agent.observations if item.observation_id == observation_id
    )
    if len(matches) != 1:
        raise ValueError("Gateway observation does not name one resolved grant")
    return matches[0]


def validate_manifest_tools_digest(
    *,
    manifest: AgentSessionManifest,
    run: ResolvedRunSpec,
    read_file: Callable[[FileRef], bytes],
) -> None:
    agent = next(
        (item for item in run.agents if item.agent_id == manifest.agent_id),
        None,
    )
    if (
        agent is None
        or agent.driver is None
        or agent.driver.driver_id != manifest.driver_id
    ):
        raise ValueError("model manifest does not name one resolved Agent Driver")
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
    tools = compile_tools(contract, read_file)
    expected = _sha256([item.model_dump(mode="json") for item in tools])
    if manifest.tools_digest != expected:
        raise ValueError("model manifest tools differ from resolved Agent grants")


def _command_requested_event(
    *,
    audit: GatewayCallAudit,
    request: CommandRequest,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
) -> RunEvent:
    grant = _grant_for_command(run, request)
    request_digest = _sha256(request.model_dump(mode="json"))
    event = _one_event(
        records,
        "command.issued",
        lambda candidate: candidate.command_id == request.command_id
        and candidate.agent_id == request.agent_id,
        label="command.invoke request",
    )
    authentication_id = _principal_authentication_id(event, agent_id=request.agent_id)
    expected_payload = {
        "run_id": run.run_id,
        "agent_id": request.agent_id,
        "authentication_id": authentication_id,
        "command_id": request.command_id,
        "tool_id": request.tool_id,
        "provider_id": grant.provider_id,
        "request_digest": request_digest,
        "request_schema_digest": grant.request_schema.sha256,
        "response_schema_digest": grant.response_schema.sha256,
        "arguments_json": canonical_json_bytes(
            [item.model_dump(mode="json") for item in request.arguments]
        ).decode("utf-8"),
    }
    interaction = event.interaction
    if (
        event.source != request.agent_id
        or event.source_kind != "agent"
        or event.workload_id != request.agent_id
        or event.provider_id != grant.provider_id
        or event.time != request.issued_at
        or event.time != audit.issued_at
        or event.correlation_id != request.command_id
        or interaction is None
        or interaction.interaction_type != "agent.tool_call.v1"
        or _event_payload(event) != expected_payload
    ):
        raise ValueError("command.invoke request differs from command.issued")
    return event


def _validate_command_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    manifest: AgentSessionManifest,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    consumed: set[str],
) -> None:
    request_document = _json_object(audit.request_json, label="command.invoke request")
    if (
        set(request_document) != {"operation", "command"}
        or request_document.get("operation") != "command.invoke"
    ):
        raise ValueError("command.invoke audit request has an invalid field set")
    request = CommandRequest.model_validate(request_document["command"])
    if (
        request.run_id != run.run_id
        or request.agent_id != manifest.agent_id
        or outer_result.audit.operation_id != request.command_id
        or outer_result.audit.command_id != request.command_id
        or audit.completed_at != audit.issued_at
    ):
        raise ValueError("command.invoke audit identity is inconsistent")
    response_document = _json_object(
        audit.response_json, label="command.invoke response"
    )
    request_digest = _sha256(request.model_dump(mode="json"))
    grant = _grant_for_command(run, request)
    issued_matches = tuple(
        event
        for event in _events(records, "command.issued", agent_id=request.agent_id)
        if event.command_id == request.command_id
    )
    failures = tuple(
        event
        for event in _events(records, "gateway.failure", agent_id=request.agent_id)
        if event.command_id == request.command_id
        and event.interaction is not None
        and event.interaction.interaction_type == "agent.tool_result.v1"
    )
    if "error" in response_document:
        if response_document != _GATEWAY_ERROR_RESPONSES["command.invoke"]:
            raise ValueError("command.invoke failure response is not canonical")
        observation_hook_failures = tuple(
            event
            for event in _events(records, "gateway.failure", agent_id=request.agent_id)
            if event.command_id is None
            and event.observation_id is not None
            and event.correlation_id == request.command_id
            and _event_payload(event).get("failure_class") == "runtime_hook_failed"
        )
        if not failures and len(observation_hook_failures) == 1:
            validated = _one_event(
                records,
                "command.validated",
                lambda candidate: candidate.command_id == request.command_id
                and candidate.agent_id == request.agent_id,
                label="failed command observation result",
            )
            result_json = _event_payload(validated).get("result_json")
            if not isinstance(result_json, str):
                raise ValueError("failed command observation omits ToolResult")
            proved_response = _json_object(
                result_json,
                label="failed command observation ToolResult",
            )
            proved_response_json = canonical_json_bytes(proved_response).decode("utf-8")
            proved_audit = audit.model_copy(
                update={
                    "response_json": proved_response_json,
                    "response_digest": hashlib.sha256(
                        proved_response_json.encode("utf-8")
                    ).hexdigest(),
                }
            )
            _validate_command_audit(
                proved_audit,
                outer_result=outer_result,
                manifest=manifest,
                records=records,
                run=run,
                consumed=consumed,
            )
            result = ToolResult.model_validate(proved_response)
            if result.observation is None:
                raise ValueError("failed command has no validated observation")
            observation_event = _validated_observation_event(
                agent_id=request.agent_id,
                envelope=result.observation,
                request_digest=request_digest,
                correlation_id=request.command_id,
                parent_event_id=validated.event_id,
                records=records,
                run=run,
            )
            failure = observation_hook_failures[0]
            observation_grant = _grant_for_observation(
                run,
                request.agent_id,
                result.observation.observation_id,
            )
            authentication_id = _principal_authentication_id(
                failure,
                agent_id=request.agent_id,
            )
            if (
                failure.source != "gateway"
                or failure.source_kind != "gateway"
                or failure.provider_id != observation_grant.provider_id
                or failure.parent_event_id != observation_event.event_id
                or _event_payload(failure)
                != {
                    "run_id": run.run_id,
                    "agent_id": request.agent_id,
                    "authentication_id": authentication_id,
                    "observation_id": result.observation.observation_id,
                    "provider_id": observation_grant.provider_id,
                    "schema_digest": observation_grant.schema_file.sha256,
                    "request_digest": request_digest,
                    "failure_class": "runtime_hook_failed",
                }
            ):
                raise ValueError(
                    "failed command observation differs from authoritative ledger"
                )
            _consume(consumed, failure)
            return
        if len(failures) != 1:
            raise ValueError("failed command.invoke has no unique failure ledger event")
        failure = failures[0]
        payload = _event_payload(failure)
        failure_class = payload.get("failure_class")
        if failure_class not in (
            _COMMAND_PRE_DISPATCH_FAILURES | _COMMAND_POST_DISPATCH_FAILURES
        ):
            raise ValueError("command.invoke failure class is not recognized")
        authentication_id = _principal_authentication_id(
            failure, agent_id=request.agent_id
        )
        if payload != {
            "run_id": run.run_id,
            "agent_id": request.agent_id,
            "authentication_id": authentication_id,
            "command_id": request.command_id,
            "tool_id": request.tool_id,
            "provider_id": grant.provider_id,
            "request_digest": request_digest,
            "failure_class": failure_class,
        } or not _failure_time_is_valid(
            failure_class=failure_class,
            event_time=failure.time,
            audit=audit,
        ):
            raise ValueError("command.invoke failure differs from authoritative ledger")
        if (
            failure.source != "gateway"
            or failure.source_kind != "gateway"
            or failure.provider_id != grant.provider_id
            or failure.correlation_id != request.command_id
            or failure.interaction is None
            or failure.interaction.interaction_type != "agent.tool_result.v1"
        ):
            raise ValueError("command.invoke failure has invalid ledger identity")
        if failure_class in _COMMAND_PRE_DISPATCH_FAILURES:
            if issued_matches:
                raise ValueError(
                    "pre-dispatch command failure has a command.issued event"
                )
            _consume(consumed, failure)
            return
        issued = _command_requested_event(
            audit=audit,
            request=request,
            records=records,
            run=run,
        )
        dispatch = _command_dispatch_event(
            request=request,
            issued=issued,
            records=records,
            run=run,
        )
        if not _is_ancestor(issued.event_id, failure, records):
            raise ValueError("post-dispatch command failure omits command causality")
        _consume(consumed, issued, dispatch, failure)
        return

    issued = _command_requested_event(
        audit=audit,
        request=request,
        records=records,
        run=run,
    )
    dispatch = _command_dispatch_event(
        request=request,
        issued=issued,
        records=records,
        run=run,
    )
    authentication_id = _principal_authentication_id(
        issued,
        agent_id=request.agent_id,
    )
    result = ToolResult.model_validate(response_document)
    if (
        result.command_id != request.command_id
        or not result.receipts
        or any(
            receipt.run_id != run.run_id
            or receipt.command_id != request.command_id
            or receipt.provider_id != grant.provider_id
            for receipt in result.receipts
        )
    ):
        raise ValueError("command.invoke result identity is inconsistent")
    receipt_events = tuple(
        event
        for event in _events(records, "command.receipt", agent_id=request.agent_id)
        if event.command_id == request.command_id
    )
    if len(receipt_events) != len(result.receipts):
        raise ValueError("command.invoke receipts differ from authoritative ledger")
    receipt_parent = dispatch.event_id
    for receipt, event in zip(result.receipts, receipt_events, strict=True):
        if (
            event.source != grant.provider_id
            or event.source_kind != "provider"
            or event.workload_id != grant.provider_id
            or event.provider_id != grant.provider_id
            or event.time != receipt.time
            or event.correlation_id != request.command_id
            or event.parent_event_id != receipt_parent
            or event.interaction is None
            or event.interaction.interaction_type != "provider.command.v1"
            or _event_payload(event)
            != {
                "run_id": run.run_id,
                "agent_id": request.agent_id,
                "authentication_id": authentication_id,
                "command_id": request.command_id,
                "tool_id": request.tool_id,
                "provider_id": grant.provider_id,
                "request_digest": request_digest,
                "phase": receipt.phase,
                "receipt_json": canonical_json_bytes(
                    receipt.model_dump(mode="json")
                ).decode("utf-8"),
            }
        ):
            raise ValueError("command.invoke receipt differs from authoritative ledger")
        receipt_parent = event.event_id
    validated = _one_event(
        records,
        "command.validated",
        lambda candidate: candidate.command_id == request.command_id
        and candidate.agent_id == request.agent_id,
        label="command.invoke result",
    )
    response_values = [item.model_dump(mode="json") for item in result.response]
    expected_payload: dict[str, object] = {
        "run_id": run.run_id,
        "agent_id": request.agent_id,
        "authentication_id": authentication_id,
        "command_id": request.command_id,
        "tool_id": request.tool_id,
        "provider_id": grant.provider_id,
        "request_digest": request_digest,
        "result_digest": _sha256(result.model_dump(mode="json")),
        "response_digest": _sha256(response_values),
        "response_schema_digest": grant.response_schema.sha256,
        "response_json": canonical_json_bytes(response_values).decode("utf-8"),
        "result_json": canonical_json_bytes(result.model_dump(mode="json")).decode(
            "utf-8"
        ),
        "outcome": result.receipts[-1].phase,
    }
    if result.observation is not None:
        expected_payload["observation_id"] = result.observation.observation_id
    interaction = validated.interaction
    if (
        validated.source != "gateway"
        or validated.source_kind != "gateway"
        or validated.provider_id != grant.provider_id
        or validated.time != request.issued_at
        or validated.time != audit.completed_at
        or validated.correlation_id != request.command_id
        or interaction is None
        or interaction.interaction_type != "agent.tool_result.v1"
        or _event_payload(validated) != expected_payload
        or issued.event_id not in validated.causal_event_ids
        or dispatch.event_id not in validated.causal_event_ids
        or any(
            event.event_id not in validated.causal_event_ids for event in receipt_events
        )
    ):
        raise ValueError("command.invoke response differs from command.validated")
    _consume(consumed, issued, dispatch, validated)
    if result.observation is not None:
        observation_event = _validated_observation_event(
            agent_id=request.agent_id,
            envelope=result.observation,
            request_digest=request_digest,
            correlation_id=request.command_id,
            parent_event_id=validated.event_id,
            records=records,
            run=run,
        )
        _consume(consumed, observation_event)


def _command_dispatch_event(
    *,
    request: CommandRequest,
    issued: RunEvent,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
) -> RunEvent:
    grant = _grant_for_command(run, request)
    request_digest = _sha256(request.model_dump(mode="json"))
    event = _one_event(
        records,
        "gateway.dispatch",
        lambda candidate: candidate.command_id == request.command_id
        and candidate.agent_id == request.agent_id,
        label="command Gateway dispatch",
    )
    interaction = event.interaction
    if (
        event.source != "gateway"
        or event.source_kind != "gateway"
        or event.provider_id != grant.provider_id
        or event.time != request.issued_at
        or event.parent_event_id != issued.event_id
        or interaction is None
        or interaction.interaction_type != "gateway.dispatch.v1"
        or _event_payload(event)
        != {
            "run_id": run.run_id,
            "agent_id": request.agent_id,
            "command_id": request.command_id,
            "tool_id": request.tool_id,
            "provider_id": grant.provider_id,
            "request_digest": request_digest,
            "timeout_ms": grant.timeout_ms,
        }
    ):
        raise ValueError("command Gateway dispatch differs from command request")
    return event


def _validated_observation_event(
    *,
    agent_id: str,
    envelope: ObservationEnvelope,
    request_digest: str,
    correlation_id: str,
    parent_event_id: str,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
) -> RunEvent:
    grant = _grant_for_observation(run, agent_id, envelope.observation_id)
    if (
        envelope.run_id != run.run_id
        or envelope.agent_id != agent_id
        or envelope.payload_schema != grant.schema_file
    ):
        raise ValueError("observation envelope identity differs from resolved grant")
    event = _one_event(
        records,
        "observation.validated",
        lambda candidate: candidate.agent_id == agent_id
        and candidate.observation_id == envelope.observation_id
        and candidate.correlation_id == correlation_id
        and candidate.parent_event_id == parent_event_id,
        label="validated observation",
    )
    authentication_id = _principal_authentication_id(event, agent_id=agent_id)
    payload = {item.name: item.value for item in envelope.payload}
    expected = {
        "run_id": run.run_id,
        "agent_id": agent_id,
        "authentication_id": authentication_id,
        "observation_id": envelope.observation_id,
        "provider_id": grant.provider_id,
        "schema_digest": grant.schema_file.sha256,
        "schema_path": grant.schema_file.path,
        "request_digest": request_digest,
        "payload_digest": envelope.payload_digest,
        "payload_json": canonical_json_bytes(payload).decode("utf-8"),
        "envelope_json": canonical_json_bytes(envelope.model_dump(mode="json")).decode(
            "utf-8"
        ),
    }
    if (
        event.source != grant.provider_id
        or event.source_kind != "provider"
        or event.workload_id != grant.provider_id
        or event.provider_id != grant.provider_id
        or event.time != envelope.time
        or event.parent_event_id != parent_event_id
        or event.interaction is None
        or event.interaction.interaction_type != "agent.observation.v1"
        or _event_payload(event) != expected
    ):
        raise ValueError("observation envelope differs from authoritative ledger")
    return event


def _validate_query_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    manifest: AgentSessionManifest,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    consumed: set[str],
) -> None:
    request_document = _json_object(audit.request_json, label="query.invoke request")
    if (
        set(request_document) != {"operation", "query"}
        or request_document.get("operation") != "query.invoke"
    ):
        raise ValueError("query.invoke audit request has an invalid field set")
    request = QueryRequest.model_validate(request_document["query"])
    grant = _grant_for_query(run, request)
    if (
        request.run_id != run.run_id
        or request.agent_id != manifest.agent_id
        or outer_result.audit.operation_id != request.query_id
        or outer_result.audit.query_id != request.query_id
        or request.issued_at != audit.issued_at
        or audit.completed_at != audit.issued_at
    ):
        raise ValueError("query.invoke audit identity is inconsistent")
    request_digest = _sha256(request.model_dump(mode="json"))
    requested = _one_event(
        records,
        "query.requested",
        lambda candidate: candidate.query_id == request.query_id
        and candidate.agent_id == request.agent_id,
        label="query.invoke request",
    )
    authentication_id = _principal_authentication_id(
        requested, agent_id=request.agent_id
    )
    expected_requested = {
        "run_id": run.run_id,
        "agent_id": request.agent_id,
        "authentication_id": authentication_id,
        "query_id": request.query_id,
        "query_type": request.query_type,
        "provider_id": grant.provider_id,
        "request_schema_digest": grant.request_schema.sha256,
        "response_schema_digest": grant.response_schema.sha256,
        "request_digest": request_digest,
        "request_json": canonical_json_bytes(request.model_dump(mode="json")).decode(
            "utf-8"
        ),
    }
    if (
        requested.source != request.agent_id
        or requested.source_kind != "agent"
        or requested.workload_id != request.agent_id
        or requested.provider_id != grant.provider_id
        or requested.time != audit.issued_at
        or requested.correlation_id != request.query_id
        or requested.interaction is None
        or requested.interaction.interaction_type != "agent.query_call.v1"
        or _event_payload(requested) != expected_requested
    ):
        raise ValueError("query.invoke request differs from query.requested")
    response_document = _json_object(audit.response_json, label="query.invoke response")
    if "error" in response_document:
        if response_document != _GATEWAY_ERROR_RESPONSES["query.invoke"]:
            raise ValueError("query.invoke failure response is not canonical")
        failure = _one_event(
            records,
            "gateway.failure",
            lambda candidate: candidate.query_id == request.query_id
            and candidate.agent_id == request.agent_id,
            label="query.invoke failure",
        )
        payload = _event_payload(failure)
        failure_class = payload.get("failure_class")
        if failure_class not in _QUERY_FAILURES or payload != {
            "run_id": run.run_id,
            "agent_id": request.agent_id,
            "authentication_id": authentication_id,
            "query_id": request.query_id,
            "query_type": request.query_type,
            "provider_id": grant.provider_id,
            "request_schema_digest": grant.request_schema.sha256,
            "response_schema_digest": grant.response_schema.sha256,
            "request_digest": request_digest,
            "failure_class": failure_class,
            "outcome": "failed",
        }:
            raise ValueError("query.invoke failure differs from authoritative ledger")
        if (
            failure.source != "gateway"
            or failure.source_kind != "gateway"
            or failure.provider_id != grant.provider_id
            or not _failure_time_is_valid(
                failure_class=failure_class,
                event_time=failure.time,
                audit=audit,
            )
            or failure.parent_event_id != requested.event_id
            or failure.interaction is None
            or failure.interaction.interaction_type != "agent.query_result.v1"
        ):
            raise ValueError("query.invoke failure has invalid ledger causality")
        _consume(consumed, requested, failure)
        return
    result = QueryResult.model_validate(response_document)
    if (
        result.run_id != run.run_id
        or result.query_id != request.query_id
        or result.agent_id != request.agent_id
        or result.query_type != request.query_type
        or result.provider_id != grant.provider_id
        or result.observed_at != request.issued_at
    ):
        raise ValueError("query.invoke result identity is inconsistent")
    validated = _one_event(
        records,
        "query.validated",
        lambda candidate: candidate.query_id == request.query_id
        and candidate.agent_id == request.agent_id,
        label="query.invoke result",
    )
    expected_result = {
        "run_id": run.run_id,
        "agent_id": request.agent_id,
        "authentication_id": authentication_id,
        "query_id": request.query_id,
        "query_type": request.query_type,
        "provider_id": grant.provider_id,
        "request_schema_digest": grant.request_schema.sha256,
        "response_schema_digest": grant.response_schema.sha256,
        "request_digest": request_digest,
        "payload_digest": result.payload_digest,
        "outcome": "succeeded",
        "result_json": canonical_json_bytes(result.model_dump(mode="json")).decode(
            "utf-8"
        ),
    }
    if (
        validated.source != grant.provider_id
        or validated.source_kind != "provider"
        or validated.workload_id != grant.provider_id
        or validated.provider_id != grant.provider_id
        or validated.time != audit.completed_at
        or validated.correlation_id != request.query_id
        or validated.parent_event_id != requested.event_id
        or validated.interaction is None
        or validated.interaction.interaction_type != "agent.query_result.v1"
        or _event_payload(validated) != expected_result
    ):
        raise ValueError("query.invoke result differs from query.validated")
    _consume(consumed, requested, validated)


def _observation_request_digest(
    *,
    run_id: str,
    agent_id: str,
    authentication_id: str,
    observation_id: str,
    requested_at: SimulationTime,
) -> str:
    return _sha256(
        {
            "run_id": run_id,
            "agent_id": agent_id,
            "authentication_id": authentication_id,
            "observation_id": observation_id,
            "requested_at": requested_at.model_dump(mode="json"),
        }
    )


def _validate_observation_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    manifest: AgentSessionManifest,
    consumed: set[str],
) -> None:
    request_document = _json_object(audit.request_json, label="observation.get request")
    if set(request_document) != {"operation", "observation_id", "requested_at"}:
        raise ValueError("observation.get audit request has an invalid field set")
    if request_document.get("operation") != "observation.get":
        raise ValueError("observation.get audit operation is invalid")
    observation_id = request_document.get("observation_id")
    if not isinstance(observation_id, str):
        raise ValueError("observation.get audit has no observation identity")
    requested_at = SimulationTime.model_validate(request_document["requested_at"])
    grant = _grant_for_observation(run, manifest.agent_id, observation_id)
    if (
        outer_result.audit.observation_id != observation_id
        or requested_at != audit.issued_at
        or audit.completed_at != audit.issued_at
    ):
        raise ValueError("observation.get audit identity is inconsistent")
    requested = _one_event(
        records,
        "observation.requested",
        lambda candidate: candidate.agent_id == manifest.agent_id
        and candidate.observation_id == observation_id
        and candidate.time == requested_at
        and candidate.event_id not in consumed,
        label="observation.get request",
    )
    authentication_id = _principal_authentication_id(
        requested, agent_id=manifest.agent_id
    )
    request_digest = _observation_request_digest(
        run_id=run.run_id,
        agent_id=manifest.agent_id,
        authentication_id=authentication_id,
        observation_id=observation_id,
        requested_at=requested_at,
    )
    correlation_id = f"observation.{request_digest}"
    if (
        requested.source != manifest.agent_id
        or requested.source_kind != "agent"
        or requested.provider_id != grant.provider_id
        or requested.correlation_id != correlation_id
        or _event_payload(requested)
        != {
            "run_id": run.run_id,
            "agent_id": manifest.agent_id,
            "authentication_id": authentication_id,
            "observation_id": observation_id,
            "provider_id": grant.provider_id,
            "schema_digest": grant.schema_file.sha256,
            "request_digest": request_digest,
        }
    ):
        raise ValueError("observation.get request differs from authoritative ledger")
    response_document = _json_object(
        audit.response_json, label="observation.get response"
    )
    if "error" in response_document:
        if response_document != _GATEWAY_ERROR_RESPONSES["observation.get"]:
            raise ValueError("observation.get failure response is not canonical")
        failure = _one_event(
            records,
            "gateway.failure",
            lambda candidate: candidate.agent_id == manifest.agent_id
            and candidate.observation_id == observation_id
            and candidate.correlation_id == correlation_id
            and candidate.event_id not in consumed,
            label="observation.get failure",
        )
        payload = _event_payload(failure)
        failure_class = payload.get("failure_class")
        if failure_class not in _OBSERVATION_FAILURES or payload != {
            "run_id": run.run_id,
            "agent_id": manifest.agent_id,
            "authentication_id": authentication_id,
            "observation_id": observation_id,
            "provider_id": grant.provider_id,
            "schema_digest": grant.schema_file.sha256,
            "request_digest": request_digest,
            "failure_class": failure_class,
        }:
            raise ValueError(
                "observation.get failure differs from authoritative ledger"
            )
        expected_parent = requested.event_id
        validated: RunEvent | None = None
        if failure_class == "runtime_hook_failed":
            candidates = tuple(
                event
                for event in _events(records, "observation.validated")
                if event.agent_id == manifest.agent_id
                and event.observation_id == observation_id
                and event.correlation_id == correlation_id
                and event.parent_event_id == requested.event_id
                and event.event_id not in consumed
            )
            if len(candidates) != 1:
                raise ValueError("runtime-hook failure omits validated observation")
            validated = candidates[0]
            envelope_json = _event_payload(validated).get("envelope_json")
            if not isinstance(envelope_json, str):
                raise ValueError("runtime-hook failure omits observation envelope")
            envelope = ObservationEnvelope.model_validate(
                _json_object(envelope_json, label="failed observation envelope")
            )
            _validated_observation_event(
                agent_id=manifest.agent_id,
                envelope=envelope,
                request_digest=request_digest,
                correlation_id=correlation_id,
                parent_event_id=requested.event_id,
                records=records,
                run=run,
            )
            expected_parent = validated.event_id
        if (
            failure.source != "gateway"
            or failure.source_kind != "gateway"
            or failure.provider_id != grant.provider_id
            or not _failure_time_is_valid(
                failure_class=failure_class,
                event_time=failure.time,
                audit=audit,
            )
            or failure.parent_event_id != expected_parent
        ):
            raise ValueError("observation.get failure has invalid ledger causality")
        _consume(
            consumed,
            requested,
            *((validated,) if validated is not None else ()),
            failure,
        )
        return
    envelope = ObservationEnvelope.model_validate(response_document)
    if (
        envelope.run_id != run.run_id
        or envelope.agent_id != manifest.agent_id
        or envelope.observation_id != observation_id
        or envelope.payload_schema != grant.schema_file
        or envelope.time != audit.completed_at
    ):
        raise ValueError("observation.get envelope identity is inconsistent")
    validated = _validated_observation_event(
        agent_id=manifest.agent_id,
        envelope=envelope,
        request_digest=request_digest,
        correlation_id=correlation_id,
        parent_event_id=requested.event_id,
        records=records,
        run=run,
    )
    _consume(consumed, requested, validated)


def _validate_probe_audit(
    audit: GatewayCallAudit,
    *,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
) -> None:
    request = _json_object(audit.request_json, label="probe request")
    response = _json_object(audit.response_json, label="probe response")
    if request != {"operation": "probe"} or set(response) != {
        "schema_version",
        "status",
        "run_id",
        "current",
    }:
        raise ValueError("probe Gateway audit has an invalid field set")
    current = SimulationTime.model_validate(response["current"])
    if (
        response["schema_version"] != "aero-bench.gateway-probe/v1"
        or response["status"] != "ready"
        or response["run_id"] != run.run_id
        or current != audit.issued_at
        or current != audit.completed_at
    ):
        raise ValueError("probe Gateway audit differs from authoritative run clock")
    if current.tick == 0:
        proofs = tuple(
            event
            for event in _events(records, "barrier.ready")
            if event.time == current and event.source == "harness"
        )
    else:
        proofs = tuple(
            event
            for event in _events(records, "barrier.committed")
            if event.time == current and event.source == "harness"
        )
    if len(proofs) != 1:
        raise ValueError("probe clock has no authoritative Provider barrier")


def _receipt_candidates(
    records: tuple[LedgerRecord, ...],
    *,
    command_id: str,
    requested_at: SimulationTime,
    agent_id: str,
) -> tuple[CommandReceipt, ...]:
    candidates: list[CommandReceipt] = []
    explicit_detail: str | None = None
    for record in records:
        event = record.event
        if (
            event.command_id != command_id
            or event.agent_id != agent_id
            or event.provider_id is None
        ):
            continue
        payload = _event_payload(event)
        phase = payload.get("phase")
        if phase not in {"received", "accepted", "applied", "completed", "failed"}:
            continue
        if (event.time.tick, event.time.sim_time_ns) > (
            requested_at.tick,
            requested_at.sim_time_ns,
        ):
            continue
        if event.event_type == "command.receipt":
            receipt_json = payload.get("receipt_json")
            if isinstance(receipt_json, str):
                receipt = CommandReceipt.model_validate(
                    _json_object(receipt_json, label="receipt_json")
                )
                candidates.append(receipt)
                explicit_detail = receipt.detail
                continue
        detail = payload.get("detail")
        if isinstance(detail, str):
            explicit_detail = detail
        candidates.append(
            CommandReceipt(
                run_id=event.run_id,
                command_id=command_id,
                provider_id=event.provider_id,
                phase=phase,
                time=event.time,
                detail=explicit_detail,
            )
        )
    return tuple(candidates)


def _validate_command_status_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    manifest: AgentSessionManifest,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
) -> None:
    request = _json_object(audit.request_json, label="command.status request")
    if (
        set(request) != {"operation", "command_id", "requested_at"}
        or request.get("operation") != "command.status"
    ):
        raise ValueError("command.status audit request has an invalid field set")
    command_id = request.get("command_id")
    if not isinstance(command_id, str) or outer_result.audit.command_id != command_id:
        raise ValueError("command.status audit identity is inconsistent")
    requested_at = SimulationTime.model_validate(request["requested_at"])
    if requested_at != audit.issued_at or audit.completed_at != audit.issued_at:
        raise ValueError("command.status audit time is inconsistent")
    response = _json_object(audit.response_json, label="command.status response")
    if "error" in response:
        raise ValueError("failed command.status has no authoritative receipt source")
    receipt = CommandReceipt.model_validate(response)
    if receipt.run_id != run.run_id or receipt.command_id != command_id:
        raise ValueError("command.status response identity is inconsistent")
    candidates = _receipt_candidates(
        records,
        command_id=command_id,
        requested_at=requested_at,
        agent_id=manifest.agent_id,
    )
    if not candidates or receipt != candidates[-1]:
        raise ValueError("command.status response has no authoritative receipt source")


def _validate_decision_summary_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    manifest: AgentSessionManifest,
    consumed: set[str],
) -> None:
    request_document = _json_object(
        audit.request_json, label="decision.summary request"
    )
    if (
        set(request_document) != {"operation", "summary"}
        or request_document.get("operation") != "decision.summary"
    ):
        raise ValueError("decision.summary audit request has an invalid field set")
    request = DecisionSummaryRequest.model_validate(request_document["summary"])
    if (
        request.run_id != run.run_id
        or request.agent_id != manifest.agent_id
        or request.summary_id != outer_result.audit.operation_id
        or request.at != audit.issued_at
        or audit.completed_at != audit.issued_at
    ):
        raise ValueError("decision.summary audit identity is inconsistent")
    response_document = _json_object(
        audit.response_json, label="decision.summary response"
    )
    if "error" in response_document:
        raise ValueError("failed decision.summary has no authoritative ledger record")
    receipt = DecisionSummaryReceipt.model_validate(response_document)
    event = _one_event(
        records,
        "agent.decision-summary",
        lambda candidate: candidate.event_id == receipt.event_id,
        label="decision.summary",
    )
    authentication_id = _principal_authentication_id(event, agent_id=manifest.agent_id)
    expected = {
        "run_id": run.run_id,
        "agent_id": manifest.agent_id,
        "authentication_id": authentication_id,
        "summary_id": request.summary_id,
        "command_id": request.command_id,
        "observation_ids_json": canonical_json_bytes(
            list(request.observation_ids)
        ).decode("utf-8"),
        "decision_summary": request.decision_summary,
        "request_digest": _sha256(request.model_dump(mode="json")),
    }
    if (
        receipt.run_id != run.run_id
        or receipt.agent_id != manifest.agent_id
        or receipt.summary_id != request.summary_id
        or receipt.at != audit.completed_at
        or event.source != manifest.agent_id
        or event.source_kind != "agent"
        or event.time != request.at
        or _event_payload(event) != expected
    ):
        raise ValueError("decision.summary differs from authoritative ledger")
    _consume(consumed, event)


def _validate_turn_audit(
    audit: GatewayCallAudit,
    *,
    outer_result: ToolExecutionResult,
    records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    manifest: AgentSessionManifest,
    consumed: set[str],
) -> None:
    request_document = _json_object(audit.request_json, label="turn.complete request")
    if (
        set(request_document) != {"operation", "completion"}
        or request_document.get("operation") != "turn.complete"
    ):
        raise ValueError("turn.complete audit request has an invalid field set")
    completion = AgentTurnCompletion.model_validate(request_document["completion"])
    if (
        completion.run_id != run.run_id
        or completion.agent_id != manifest.agent_id
        or not completion.completion_id.startswith(
            f"{outer_result.audit.operation_id}.tick."
        )
        or completion.at != audit.issued_at
    ):
        raise ValueError("turn.complete audit identity is inconsistent")
    completion_event = _one_event(
        records,
        "agent.turn-completion",
        lambda candidate: candidate.agent_id == manifest.agent_id
        and candidate.correlation_id == completion.completion_id,
        label="turn.complete submission",
    )
    expected_completion = {
        "run_id": run.run_id,
        "agent_id": manifest.agent_id,
        "completion_id": completion.completion_id,
        "disposition": completion.disposition,
        "command_ids": canonical_json_bytes(list(completion.command_ids)).decode(
            "utf-8"
        ),
        "observation_ids": canonical_json_bytes(
            list(completion.observation_ids)
        ).decode("utf-8"),
    }
    if (
        completion_event.source != manifest.agent_id
        or completion_event.source_kind != "agent"
        or completion_event.workload_id != manifest.agent_id
        or completion_event.time != completion.at
        or _event_payload(completion_event) != expected_completion
    ):
        raise ValueError("turn.complete differs from agent.turn-completion")
    expected_completion_causes: set[str] = set()
    for command_id in completion.command_ids:
        candidates = tuple(
            event
            for event in _events(
                records, "command.validated", agent_id=manifest.agent_id
            )
            if event.command_id == command_id and event.time == completion.at
        )
        if len(candidates) != 1:
            raise ValueError("turn completion references an unvalidated command")
        expected_completion_causes.add(candidates[0].event_id)
    for observation_id in completion.observation_ids:
        candidates = tuple(
            event
            for event in _events(
                records,
                "observation.validated",
                agent_id=manifest.agent_id,
            )
            if event.observation_id == observation_id and event.time == completion.at
        )
        if not candidates:
            raise ValueError("turn completion references an unavailable observation")
        expected_completion_causes.add(candidates[-1].event_id)
    if set(completion_event.causal_event_ids) != expected_completion_causes or (
        completion_event.parent_event_id
        != (max(expected_completion_causes) if expected_completion_causes else None)
    ):
        raise ValueError("turn completion has invalid API causality")
    response_document = _json_object(
        audit.response_json, label="turn.complete response"
    )
    if "error" in response_document:
        raise ValueError("failed turn.complete has no authoritative decision record")
    decision = AgentTurnDecision.model_validate(response_document)
    if decision.status == "waiting":
        raise ValueError("formal single-session turn cannot remain waiting")
    decision_event = _one_event(
        records,
        "agent.turn-decision",
        lambda candidate: candidate.time == decision.at
        and candidate.event_id not in consumed
        and completion_event.event_id in candidate.causal_event_ids,
        label="turn.complete decision",
    )
    expected_decision = {
        "run_id": run.run_id,
        "status": decision.status,
        "active_agent_ids": canonical_json_bytes(
            list(decision.active_agent_ids)
        ).decode("utf-8"),
        "missing_agent_ids": canonical_json_bytes(
            list(decision.missing_agent_ids)
        ).decode("utf-8"),
    }
    if (
        decision.run_id != run.run_id
        or decision.at != audit.completed_at
        or decision_event.source != "harness"
        or decision_event.source_kind != "harness"
        or decision_event.workload_id != "harness"
        or decision_event.correlation_id != f"turn.{decision.at.tick}"
        or _event_payload(decision_event) != expected_decision
    ):
        raise ValueError("turn.complete response differs from runtime turn decision")
    if decision.status == "advanced":
        barrier = _one_event(
            records,
            "barrier.committed",
            lambda candidate: candidate.time == decision.at,
            label="turn.complete Provider barrier",
        )
        provider_receipts = tuple(
            event
            for event in _events(records, "provider.step-receipt")
            if event.time == decision.at
        )
        expected_provider_ids = {
            provider.provider_id for provider in run.environment.providers
        }
        if (
            set(decision_event.causal_event_ids)
            != {completion_event.event_id, barrier.event_id}
            or decision_event.parent_event_id != barrier.event_id
            or barrier.source != "harness"
            or barrier.source_kind != "harness"
            or barrier.correlation_id != f"tick.{decision.at.tick}"
            or {event.provider_id for event in provider_receipts}
            != expected_provider_ids
            or len(provider_receipts) != len(expected_provider_ids)
        ):
            raise ValueError("turn decision does not close every Provider barrier")
    elif decision.status == "terminated" and decision_event.causal_event_ids != (
        completion_event.event_id,
    ):
        raise ValueError("terminated turn decision has invalid completion causality")
    _consume(consumed, completion_event, decision_event)


def _tool_gateway_shape(
    result: ToolExecutionResult,
    *,
    action_names: set[str],
    query_names: set[str],
    observation_names: set[str],
) -> None:
    name = result.audit.operation
    operations = tuple(call.operation for call in result.audit.gateway_calls)
    if name in action_names:
        allowed = ("decision.summary", "command.invoke")
        if not result.success and operations in {(), ("decision.summary",)}:
            return
    elif name in query_names:
        allowed = ("query.invoke",)
    elif name in observation_names:
        allowed = ("observation.get",)
    elif name == "get_run_status":
        allowed = ("probe",)
    elif name == "get_command_status":
        allowed = ("command.status",)
    elif name == "record_decision_summary":
        allowed = ("decision.summary",)
    elif name == "finish":
        allowed = ("turn.complete",)
    elif name == "wait_duration":
        if any(operation != "turn.complete" for operation in operations):
            raise ValueError("wait_duration contains an undeclared Gateway operation")
        if result.success and not operations:
            raise ValueError("successful wait_duration omits Provider barrier calls")
        return
    elif name == "wait_for_command":
        for index, operation in enumerate(operations):
            expected = "command.status" if index % 2 == 0 else "turn.complete"
            if operation != expected:
                raise ValueError("wait_for_command Gateway sequence is invalid")
        if result.success and not operations:
            raise ValueError("successful wait_for_command omits command status")
        return
    elif name in {"read_public_asset", "write_json_artifact"}:
        if operations:
            raise ValueError("local bridge tool contains a Gateway operation")
        return
    else:
        raise ValueError("interaction log names a tool outside the compiled bridge")
    if operations not in {(), allowed}:
        raise ValueError("bridge tool contains an invalid Gateway call inventory")
    if not operations and result.success:
        raise ValueError("successful bridge tool omits its required Gateway call")


def _named_values(values: tuple[Any, ...], *, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in values:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or name in result:
            raise ValueError(f"{label} has an invalid named-value inventory")
        result[name] = item.value
    return result


def _successful_query(call: _VerifiedGatewayCall) -> QueryRequest | None:
    if call.operation != "query.invoke" or "error" in call.response:
        return None
    return QueryRequest.model_validate(call.request.get("query"))


def _successful_command(call: _VerifiedGatewayCall) -> CommandRequest | None:
    if call.operation != "command.invoke" or "error" in call.response:
        return None
    result = ToolResult.model_validate(call.response)
    if not result.receipts or result.receipts[-1].phase == "failed":
        return None
    return CommandRequest.model_validate(call.request.get("command"))


def _successful_observation(
    call: _VerifiedGatewayCall,
) -> ObservationEnvelope | None:
    if call.operation != "observation.get" or "error" in call.response:
        return None
    return ObservationEnvelope.model_validate(call.response)


def _validate_required_inspection_api_usage(
    *,
    manifest: AgentSessionManifest,
    calls: tuple[_VerifiedGatewayCall, ...],
    required_work_order_ids: tuple[str, ...],
    required_vehicle_ids: tuple[str, ...],
) -> None:
    if manifest.status != "completed":
        return
    if (
        not required_work_order_ids
        or len(required_work_order_ids) != len(set(required_work_order_ids))
        or not required_vehicle_ids
        or len(required_vehicle_ids) != len(set(required_vehicle_ids))
    ):
        raise ValueError("inspection model API requirements are incomplete")

    queries = tuple(
        (call, request)
        for call in calls
        if (request := _successful_query(call)) is not None
    )
    commands = tuple(
        (call, request)
        for call in calls
        if (request := _successful_command(call)) is not None
    )
    observations = tuple(
        (call, envelope)
        for call in calls
        if (envelope := _successful_observation(call)) is not None
    )
    required_orders = set(required_work_order_ids)
    summary_discoveries: list[tuple[_VerifiedGatewayCall, set[str]]] = []
    for call, request in queries:
        if request.query_type != "business.work-orders":
            continue
        result = QueryResult.model_validate(call.response)
        payload = _named_values(result.payload, label="business.work-orders result")
        rows_json = payload.get("work_orders_json")
        count = payload.get("work_order_count")
        if (
            not isinstance(rows_json, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
        ):
            raise ValueError("work-order summary result has an invalid inventory")
        try:
            rows = json.loads(rows_json)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("work-order summary inventory is not JSON") from error
        if (
            not isinstance(rows, list)
            or len(rows) != count
            or any(not isinstance(row, dict) for row in rows)
        ):
            raise ValueError("work-order summary result has an invalid inventory")
        if canonical_json_bytes(rows).decode("utf-8") != rows_json:
            raise ValueError("work-order summary inventory is not canonical JSON")
        discovered_values = [row.get("work_order_id") for row in rows]
        if (
            any(not isinstance(item, str) for item in discovered_values)
            or len(set(discovered_values)) != len(discovered_values)
            or set(discovered_values) != required_orders
        ):
            raise ValueError(
                "work-order summary does not exactly cover the Inspection package"
            )
        summary_discoveries.append((call, {str(item) for item in discovered_values}))
    if not summary_discoveries:
        raise ValueError(
            "completed inspection session omitted work-order summary query"
        )

    detail_calls_by_work_order: dict[str, list[_VerifiedGatewayCall]] = {
        work_order_id: [] for work_order_id in required_work_order_ids
    }
    for call, request in queries:
        if request.query_type != "business.work-order":
            continue
        query_work_order_id = _named_values(
            request.arguments,
            label="business.work-order query",
        ).get("work_order_id")
        result = QueryResult.model_validate(call.response)
        result_work_order_id = _named_values(
            result.payload,
            label="business.work-order result",
        ).get("work_order_id")
        if query_work_order_id != result_work_order_id:
            raise ValueError("work-order detail result differs from its query")
        if not any(
            summary.consumed_before(call) and query_work_order_id in discovered
            for summary, discovered in summary_discoveries
        ):
            raise ValueError("work-order detail was not selected from prior discovery")
        if not isinstance(query_work_order_id, str) or query_work_order_id not in (
            detail_calls_by_work_order
        ):
            raise ValueError("model queried a work order outside prior discovery")
        detail_calls_by_work_order[query_work_order_id].append(call)

    for work_order_id in required_work_order_ids:
        detail_calls = detail_calls_by_work_order[work_order_id]
        claim_calls = tuple(
            call
            for call, request in commands
            if request.tool_id == "business.claim"
            and _named_values(
                request.arguments,
                label="business.claim command",
            ).get("work_order_id")
            == work_order_id
        )
        if not detail_calls or not claim_calls:
            raise ValueError(
                "completed inspection session omitted required work-order detail or claim"
            )
        first_detail = min(detail_calls, key=lambda item: item.order)
        first_claim = min(claim_calls, key=lambda item: item.order)
        if not first_detail.consumed_before(first_claim):
            raise ValueError(
                "work-order summary and detail must precede business.claim"
            )

    for call, request in commands:
        if request.tool_id != "business.claim":
            continue
        work_order_id = _named_values(
            request.arguments,
            label="business.claim command",
        ).get("work_order_id")
        if work_order_id not in required_orders:
            raise ValueError(
                "model claimed a work order outside the Inspection package"
            )

    for vehicle_id in required_vehicle_ids:
        arm_calls = tuple(
            (call, request)
            for call, request in commands
            if request.tool_id == "flight.arm"
            and _named_values(request.arguments, label="flight.arm command").get(
                "vehicle_id"
            )
            == vehicle_id
        )
        if not arm_calls:
            raise ValueError("completed inspection session omitted required flight.arm")
        first_arm_call, first_arm_request = min(
            arm_calls,
            key=lambda item: item[0].order,
        )
        telemetry_calls: list[_VerifiedGatewayCall] = []
        gnss_calls: list[_VerifiedGatewayCall] = []
        for call, envelope in observations:
            if (
                not call.consumed_before(first_arm_call)
                or envelope.time != first_arm_request.issued_at
            ):
                continue
            payload = _named_values(envelope.payload, label="flight observation")
            schema_version = payload.get("schema_version")
            if schema_version == "aero-bench.observation.flight-telemetry/v1":
                telemetry = FlightTelemetryObservation.model_validate(payload)
                if telemetry.vehicle_id == vehicle_id:
                    telemetry_calls.append(call)
            elif schema_version == "aero-bench.observation.flight-gnss/v1":
                gnss = FlightGnssObservation.model_validate(payload)
                if (
                    gnss.vehicle_id == vehicle_id
                    and gnss.observation_status == "available"
                    and gnss.position_status == "available"
                    and gnss.fix_type not in {"NO_GPS", "NO_FIX"}
                ):
                    gnss_calls.append(call)
        if not telemetry_calls or not gnss_calls:
            raise ValueError(
                "flight.arm lacks prior same-barrier telemetry and valid raw GNSS"
            )


def validate_model_gateway_evidence(
    *,
    manifest: AgentSessionManifest,
    interactions: tuple[InteractionRecord, ...],
    ledger_records: tuple[LedgerRecord, ...],
    run: ResolvedRunSpec,
    required_work_order_ids: tuple[str, ...],
    required_vehicle_ids: tuple[str, ...],
) -> None:
    agent = next(
        (item for item in run.agents if item.agent_id == manifest.agent_id),
        None,
    )
    if agent is None or agent.driver is None or len(run.agents) != 1:
        raise ValueError(
            "inspection model Gateway evidence requires one resolved Agent Driver"
        )
    action_names = {
        "action_" + re.sub(r"[^a-z0-9_]", "_", item.tool_id) for item in agent.tools
    }
    query_names = {
        "query_" + re.sub(r"[^a-z0-9_]", "_", item.query_type) for item in agent.queries
    }
    observation_names = {
        "read_" + re.sub(r"[^a-z0-9_]", "_", item.observation_id)
        for item in agent.observations
    }
    consumed: set[str] = set()
    authentications: set[str] = set()
    verified_calls: list[_VerifiedGatewayCall] = []
    model_arguments_by_call_id = {
        record.call_id: record.payload.get("arguments")
        for record in interactions
        if record.record_type == "tool.call"
    }
    proposal_sequence_by_call_id: dict[str, int] = {}
    for record in interactions:
        if record.record_type != "model.response":
            continue
        function_calls = record.payload.get("function_calls")
        if not isinstance(function_calls, list):
            raise ValueError("model response has no function call inventory")
        for function_call in function_calls:
            if not isinstance(function_call, dict):
                raise ValueError("model response function call is malformed")
            call_id = function_call.get("call_id")
            if not isinstance(call_id, str) or call_id in proposal_sequence_by_call_id:
                raise ValueError("model response function call identity is invalid")
            proposal_sequence_by_call_id[call_id] = record.sequence
    for record in interactions:
        if record.record_type != "tool.result":
            continue
        result = ToolExecutionResult.model_validate(record.payload.get("result"))
        if record.call_id is None or record.call_id not in proposal_sequence_by_call_id:
            raise ValueError("tool result lacks its model proposal sequence")
        _tool_gateway_shape(
            result,
            action_names=action_names,
            query_names=query_names,
            observation_names=observation_names,
        )
        if result.audit.operation in action_names and result.audit.gateway_calls:
            summary = DecisionSummaryRequest.model_validate(
                _json_object(
                    result.audit.gateway_calls[0].request_json,
                    label="action decision summary",
                ).get("summary")
            )
            arguments = model_arguments_by_call_id.get(record.call_id)
            if (
                not isinstance(arguments, dict)
                or summary.decision_summary != arguments.get("decision_summary")
                or summary.command_id != result.audit.operation_id
                or summary.command_id != result.audit.command_id
                or summary.at != result.audit.issued_at
            ):
                raise ValueError(
                    "action summary is not bound to its model-authored command"
                )
            if len(result.audit.gateway_calls) == 2:
                command = CommandRequest.model_validate(
                    _json_object(
                        result.audit.gateway_calls[1].request_json,
                        label="action command",
                    ).get("command")
                )
                if (
                    command.command_id != summary.command_id
                    or command.issued_at != summary.at
                ):
                    raise ValueError("action summary and command identities differ")
        for index, audit in enumerate(result.audit.gateway_calls):
            if "error" in _json_object(audit.response_json, label="Gateway response"):
                if index != len(result.audit.gateway_calls) - 1 or result.success:
                    raise ValueError(
                        "failed Gateway call does not terminate its tool result"
                    )
            if audit.operation == "command.invoke":
                _validate_command_audit(
                    audit,
                    outer_result=result,
                    manifest=manifest,
                    records=ledger_records,
                    run=run,
                    consumed=consumed,
                )
            elif audit.operation == "query.invoke":
                _validate_query_audit(
                    audit,
                    outer_result=result,
                    manifest=manifest,
                    records=ledger_records,
                    run=run,
                    consumed=consumed,
                )
            elif audit.operation == "observation.get":
                _validate_observation_audit(
                    audit,
                    outer_result=result,
                    records=ledger_records,
                    run=run,
                    manifest=manifest,
                    consumed=consumed,
                )
            elif audit.operation == "command.status":
                _validate_command_status_audit(
                    audit,
                    outer_result=result,
                    manifest=manifest,
                    records=ledger_records,
                    run=run,
                )
            elif audit.operation == "probe":
                _validate_probe_audit(audit, records=ledger_records, run=run)
            elif audit.operation == "decision.summary":
                _validate_decision_summary_audit(
                    audit,
                    outer_result=result,
                    records=ledger_records,
                    run=run,
                    manifest=manifest,
                    consumed=consumed,
                )
            elif audit.operation == "turn.complete":
                _validate_turn_audit(
                    audit,
                    outer_result=result,
                    records=ledger_records,
                    run=run,
                    manifest=manifest,
                    consumed=consumed,
                )
            else:
                raise ValueError("interaction log contains an unsupported Gateway call")
            verified_calls.append(
                _VerifiedGatewayCall(
                    proposal_sequence=proposal_sequence_by_call_id[record.call_id],
                    interaction_sequence=record.sequence,
                    call_index=index,
                    operation=audit.operation,
                    request=_json_object(audit.request_json, label="Gateway request"),
                    response=_json_object(
                        audit.response_json, label="Gateway response"
                    ),
                )
            )

    expected: set[str] = set()
    for record in ledger_records:
        event = record.event
        if event.event_type not in _AGENT_GATEWAY_EVENT_TYPES:
            continue
        if event.event_type == "agent.turn-decision":
            expected.add(event.event_id)
            continue
        if event.agent_id != manifest.agent_id:
            continue
        expected.add(event.event_id)
        authentication_id = _event_payload(event).get("authentication_id")
        if isinstance(authentication_id, str):
            authentications.add(authentication_id)
    if expected != consumed:
        raise ValueError(
            "model interaction log does not cover every Agent Gateway event"
        )
    if len(authentications) > 1:
        raise ValueError(
            "Agent Gateway authentication changed within one model session"
        )
    _validate_required_inspection_api_usage(
        manifest=manifest,
        calls=tuple(verified_calls),
        required_work_order_ids=required_work_order_ids,
        required_vehicle_ids=required_vehicle_ids,
    )


__all__ = [
    "validate_manifest_tools_digest",
    "validate_model_gateway_evidence",
]
