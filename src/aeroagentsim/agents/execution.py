"""Pure, read-side views of committed agent evidence; no engines or WAL rewrites."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

AGENT_EXECUTION_VERSION = 1

ExecutionMode = Literal["function_tools", "structured_json", "scripted"]
ExecutionOutcome = Literal["succeeded", "partial", "failed"]
RecordInput = Mapping[str, Any] | Iterable[Mapping[str, Any]]

# Historical, explicitly authored provider model identifiers, not content heuristics.
_SCRIPTED_MODELS = {"stub", "scripted", "lowalt-scripted-allocation/v1"}
_REQUEST_MODES = {"function_tools", "structured_json"}


@dataclass(frozen=True)
class AgentValidation:
    valid: bool | None
    code: str | None
    call_id: str | None
    data: dict[str, Any] | None
    message_id: str | None


@dataclass(frozen=True)
class AgentCall:
    """One model-client attempt (possibly preflight), not a tool or command."""

    identity: list[Any] | None = None
    mode: ExecutionMode | None = None
    provider_mode: str | None = None
    provider_profile: str | None = None
    model: str | None = None
    transport_transformations: list[str] | None = None
    round: int | None = None
    attempt: int | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    latency_s: float | None = None
    valid: bool | None = None
    failure_code: str | None = None
    error: dict[str, Any] | None = None
    validations: list[AgentValidation] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentOutput:
    """An output/proposal record; message_id identifies evidence, not completion."""

    kind: Literal["command", "event", "fact"] | None
    data: dict[str, Any] | None
    message_id: str | None


@dataclass(frozen=True)
class AgentReceipt:
    data: dict[str, Any] | None
    message_id: str | None


@dataclass(frozen=True)
class AgentExecution:
    """Detached evidence for one decision; missing evidence stays None.

    Freezing prevents field reassignment. Lists and raw JSON mappings are copied
    from the input, but are not recursively immutable or automatically redacted.
    """

    engine: Literal["decision", "langgraph"]
    engine_id: str | None
    decision_id: str | None
    invocation_id: str | None
    observation_cut: dict[str, Any] | None
    observation: dict[str, Any] | None
    mode: ExecutionMode | None
    provider_mode: str | None
    provider_profile: str | None
    model: str | None
    transport_transformations: list[str] | None
    graph_pin: dict[str, Any] | None
    calls: list[AgentCall]
    outputs: list[AgentOutput]
    receipts: list[AgentReceipt]
    outcome: ExecutionOutcome | None
    outcome_code: str | None
    failure: dict[str, Any] | None
    message_ids: list[str]
    version: int = field(default=AGENT_EXECUTION_VERSION, init=False)


@dataclass(frozen=True)
class _Record:
    phase: str | None
    data: dict[str, Any] | None
    code: str | None
    decision_id: str | None
    engine_id: str | None
    invocation_id: str | None
    message_id: str | None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping(value: Any) -> dict[str, Any] | None:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else None


def _integer(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _latency(value: Any) -> float | None:
    if type(value) not in (int, float) or value < 0:
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _records(
    payload: RecordInput, schema: str
) -> tuple[list[_Record], Mapping[str, Any]]:
    envelope: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    if "records" in envelope:
        items = envelope["records"]
    elif isinstance(payload, Mapping):
        items = [payload]
    else:
        items = payload
    rows: list[_Record] = []
    seen: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("execution records must be mappings")
        supplied_schema = item.get("schemaId")
        if supplied_schema is not None and supplied_schema != schema:
            raise ValueError("use original records from the selected agent schema")
        body = item.get("payload", item)
        if not isinstance(body, Mapping):
            raise TypeError("execution record payload must be a mapping")
        data = _mapping(body.get("data"))
        if isinstance(body.get("data_json"), str):
            try:
                data = _mapping(json.loads(body["data_json"]))
            except (ValueError, TypeError):
                data = None
        if data is not None and data.get("source_schema") == "aas.langgraph.record":
            # Console aliases are not additional decisions, calls or commands.
            raise ValueError(
                "use original aas.langgraph.record evidence, not console aliases"
            )
        message_id = _text(item.get("id")) or _text(item.get("message_id"))
        if message_id is not None:
            if message_id in seen:
                if seen[message_id] != body:
                    raise ValueError("conflicting evidence for the same message id")
                continue
            seen[message_id] = body
        rows.append(
            _Record(
                phase=_text(body.get("phase")),
                data=data,
                code=_text(body.get("code")) or _text((data or {}).get("code")),
                decision_id=_text(body.get("decision_id")),
                engine_id=_text(item.get("source"))
                or _text(item.get("engine_id"))
                or _text(body.get("engine_id")),
                invocation_id=_text(item.get("invocation_id"))
                or _text(body.get("invocation_id")),
                message_id=message_id,
            )
        )
    decisions = {row.decision_id for row in rows if row.decision_id is not None}
    if _text(envelope.get("decision_id")) is not None:
        decisions.add(envelope["decision_id"])
    if len(decisions) > 1:
        raise ValueError("group execution records by decision_id before adapting")
    return rows, envelope


def _metadata(rows: list[_Record]) -> list[dict[str, Any]]:
    sources = []
    for row in rows:
        data = row.data or {}
        sources.append(data)
        for key in ("provider", "request"):
            if isinstance(data.get(key), dict):
                sources.append(data[key])
    return sources


def _first(sources: list[dict[str, Any]], key: str) -> Any:
    return next(
        (source[key] for source in sources if source.get(key) is not None), None
    )


def _mode(
    sources: list[dict[str, Any]],
    engine: str,
    provider_mode: str | None,
    model: str | None,
) -> ExecutionMode | None:
    if provider_mode in {"stub", "scripted"} or (
        provider_mode is None and model in _SCRIPTED_MODELS
    ):
        return "scripted"
    explicit = [
        source["execution_mode"] for source in sources if "execution_mode" in source
    ]
    if explicit:
        value = explicit[0]
        if (
            isinstance(value, str)
            and value in _REQUEST_MODES
            and all(x == value for x in explicit)
        ):
            return cast(ExecutionMode, value)
        return None
    if engine == "decision":
        return (
            "function_tools"
            if any(isinstance(source.get("messages"), list) for source in sources)
            else None
        )
    # Inspect the recorded API request, never natural-language response content.
    inferred = {
        "function_tools" if source["tools"] else "structured_json"
        for source in sources
        if isinstance(source.get("tools"), list)
    }
    if len(inferred) == 1:
        return cast(ExecutionMode, inferred.pop())
    return None


def _provider_fields(sources: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    provider_mode = _text(_first(sources, "provider_mode"))
    if provider_mode is None:
        provider_mode = _text(_first(sources, "mode"))
    if provider_mode not in {"stub", "scripted", "live"}:
        provider_mode = None
    model = _text(_first(sources, "model"))
    transformations = _first(sources, "transport_transformations")
    return {
        "mode": _mode(sources, engine, provider_mode, model),
        "provider_mode": provider_mode,
        "provider_profile": _text(_first(sources, "provider_profile"))
        or _text(_first(sources, "profile")),
        "model": model,
        "transport_transformations": (
            list(transformations)
            if isinstance(transformations, (list, tuple))
            and all(_text(item) is not None for item in transformations)
            else None
        ),
    }


def _ids(row: _Record) -> list[str]:
    return [] if row.message_id is None else [row.message_id]


def _validation(row: _Record) -> AgentValidation:
    data = row.data or {}
    call = data.get("call")
    return AgentValidation(
        valid=data.get("valid") if type(data.get("valid")) is bool else None,
        code=row.code,
        call_id=(
            _text(call.get("id"))
            if isinstance(call, dict)
            else _text(data.get("call_id"))
        ),
        data=row.data,
        message_id=row.message_id,
    )


def _tool_validation_outcome(
    response: dict[str, Any] | None, validations: list[AgentValidation]
) -> bool | None:
    if any(v.valid is False for v in validations):
        return False
    message = _mapping((response or {}).get("message")) or {}
    expected = message.get("tool_calls")
    if not isinstance(expected, list) or not expected:
        return None
    call_ids = [
        _text(call.get("id")) if isinstance(call, dict) else None for call in expected
    ]
    if (
        all(v.valid is True for v in validations)
        and None not in call_ids
        and len(validations) == len(call_ids) == len(set(call_ids))
        and {v.call_id for v in validations} == set(call_ids)
    ):
        return True
    return None


def _decision_calls(rows: list[_Record]) -> list[AgentCall]:
    calls: list[AgentCall] = []
    for row in rows:
        data = row.data or {}
        if row.phase == "prompt":
            calls.append(
                AgentCall(
                    **_provider_fields(_metadata([row]), "decision"),
                    round=_integer(data.get("round")),
                    attempt=_integer(data.get("attempt")),
                    request=row.data,
                    message_ids=_ids(row),
                )
            )
        elif row.phase == "response":
            if not calls or calls[-1].response is not None:
                calls.append(AgentCall())
            calls[-1] = replace(
                calls[-1],
                response=row.data,
                usage=_mapping(data.get("usage")),
                latency_s=_latency(data.get("latency_s")),
                message_ids=calls[-1].message_ids + _ids(row),
            )
        elif row.phase == "validation" and calls:
            validation = _validation(row)
            validations = calls[-1].validations + [validation]
            valid = _tool_validation_outcome(calls[-1].response, validations)
            calls[-1] = replace(
                calls[-1],
                validations=validations,
                valid=valid,
                failure_code=(
                    validation.code
                    if validation.valid is False
                    else calls[-1].failure_code
                ),
                message_ids=calls[-1].message_ids + _ids(row),
            )
        elif (
            row.phase == "failure"
            and calls
            and (calls[-1].response is None or not calls[-1].validations)
        ):
            # Retry/round exhaustion and next-round preflight failures are not
            # failures of an earlier exchange whose tool validations were recorded.
            calls[-1] = replace(
                calls[-1],
                failure_code=calls[-1].failure_code or row.code,
                error=row.data,
                message_ids=calls[-1].message_ids + _ids(row),
            )
    return calls


def _graph_calls(rows: list[_Record]) -> list[AgentCall]:
    calls = []
    for row in rows:
        if row.phase != "model_call":
            continue
        data = row.data or {}
        identity = data.get("identity")
        identity = deepcopy(identity) if isinstance(identity, list) else None
        response = _mapping(data.get("response"))
        error = _mapping(data.get("error"))
        rejection = _mapping(data.get("rejection"))
        validation = _mapping(data.get("validation"))
        if rejection is not None:
            validation = {**rejection, "valid": False}
        valid = (validation or {}).get("valid")
        valid = valid if type(valid) is bool else None
        validations = (
            []
            if validation is None
            else [
                AgentValidation(
                    valid=valid,
                    code=_text(validation.get("code")),
                    call_id=None,
                    data=validation,
                    message_id=row.message_id,
                )
            ]
        )
        calls.append(
            AgentCall(
                **_provider_fields(_metadata([row]), "langgraph"),
                identity=identity,
                attempt=_integer(data.get("attempt"))
                if "attempt" in data
                else _integer(identity[3])
                if identity is not None and len(identity) == 4
                else None,
                request=_mapping(data.get("request")),
                response=response,
                usage=_mapping((response or {}).get("usage")),
                latency_s=_latency(data.get("latency_s")),
                valid=valid,
                failure_code=_text(data.get("failure_code"))
                or _text((error or {}).get("code"))
                or _text((rejection or {}).get("code"))
                or (_text((validation or {}).get("code")) if valid is False else None),
                error=error,
                validations=validations,
                message_ids=_ids(row),
            )
        )
    return calls


def _execution(
    payload: RecordInput, engine: Literal["decision", "langgraph"]
) -> AgentExecution:
    schema = "aas.agent.record" if engine == "decision" else "aas.langgraph.record"
    rows, envelope = _records(payload, schema)
    decision_id = _text(envelope.get("decision_id")) or next(
        (row.decision_id for row in rows if row.decision_id is not None), None
    )
    engine_id = _text(envelope.get("engine_id")) or next(
        (row.engine_id for row in rows if row.engine_id is not None), None
    )
    if engine_id is None and decision_id is not None:
        prefix, separator, sequence = decision_id.rpartition("/")
        if separator and prefix and sequence.isdecimal():
            # The engines record decision IDs as <partition>/<sequence>.
            engine_id = prefix
    invocation_id = _text(envelope.get("invocation_id")) or next(
        (row.invocation_id for row in rows if row.invocation_id is not None), None
    )
    initial = next((row.data for row in rows if row.phase == "observation"), None)
    observation = initial
    if engine == "langgraph":
        graph_initial = _mapping((initial or {}).get("initial")) or {}
        observation = _mapping(graph_initial.get("observation"))
    sources = _metadata(rows)
    provider_fields = _provider_fields(sources, engine)
    if engine == "langgraph":
        call_rows = [row for row in rows if row.phase == "model_call"]
        if call_rows:
            # Invocation metadata describes the default API; actual exchanges
            # can use function tools instead. Mixed modes remain unknown here.
            provider_fields["mode"] = _mode(
                _metadata(call_rows),
                engine,
                provider_fields["provider_mode"],
                provider_fields["model"],
            )
    outputs = []
    receipts = []
    terminal: _Record | None = None
    for row in rows:
        if row.phase == ("command" if engine == "decision" else "output"):
            kind = (
                "command"
                if engine == "decision"
                else _text((row.data or {}).get("kind"))
            )
            outputs.append(
                AgentOutput(
                    kind=kind if kind in {"command", "event", "fact"} else None,
                    data=row.data,
                    message_id=row.message_id,
                )
            )
        elif row.phase == "receipt":
            receipts.append(AgentReceipt(data=row.data, message_id=row.message_id))
        if row.phase in {"finished", "failure"}:
            terminal = row
    outcome: ExecutionOutcome | None = None
    if terminal is not None:
        outcome = (
            "succeeded"
            if terminal.phase == "finished"
            else "partial"
            if outputs
            else "failed"
        )
    return AgentExecution(
        engine=engine,
        engine_id=engine_id,
        decision_id=decision_id,
        invocation_id=invocation_id,
        observation_cut=_mapping((observation or {}).get("known_at")),
        observation=observation,
        **provider_fields,
        graph_pin=_mapping((initial or {}).get("pin")),
        calls=_decision_calls(rows) if engine == "decision" else _graph_calls(rows),
        outputs=outputs,
        receipts=receipts,
        outcome=outcome,
        outcome_code=(
            terminal.code
            if terminal is not None and terminal.phase == "failure"
            else None
        ),
        failure=(
            terminal.data
            if terminal is not None and terminal.phase == "failure"
            else None
        ),
        message_ids=[row.message_id for row in rows if row.message_id is not None],
    )


def from_agent_record(payload: RecordInput) -> AgentExecution:
    """Adapt one aas.agent.record payload or ordered records for one decision.

    An optional {decision_id, engine_id, invocation_id, records: [...]} envelope
    supplies retained identity when phase-only rows are used. Full message
    envelopes retain evidence message IDs; bare payloads cannot invent them.
    """
    return _execution(payload, "decision")


def from_langgraph_records(payloads: RecordInput) -> AgentExecution:
    """Adapt original aas.langgraph.record payloads/messages for one decision."""
    return _execution(payloads, "langgraph")
