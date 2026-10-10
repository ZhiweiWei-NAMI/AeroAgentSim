from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
import yaml

from aero_bench.config.models import NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway import (
    AgentPrincipal,
    GatewayDispatchError,
    GatewayDispatcher,
    ObservationEnvelope,
    ProviderToolResult,
    ToolResult,
)
from aero_bench.runtime import (
    CommandReceipt,
    CommandRequest,
    EventLedger,
    ProviderEvent,
    SimulationTime,
)
from aero_bench.runtime.events import RunEventAudience
from aero_bench.serialization import canonical_json_bytes
from tests.support import build_bundle, digest, rebuild_run_agents, resolve_bundle


@pytest.mark.parametrize("interaction,payload", [
    ("agent.decision_summary.v1", {"decision_summary": "mission_phase=inspection"}),
    ("agent.tool_call.v1", {"tool_id": "flight.goto"}),
    ("agent.tool_result.v1", {"tool_id": "flight.goto", "outcome": "completed"}),
    ("agent.observation.v1", {}),
    ("agent.query_call.v1", {"query_type": "business.work-orders"}),
    (
        "agent.query_result.v1",
        {"query_type": "business.work-orders", "outcome": "succeeded"},
    ),
])
@pytest.mark.parametrize("provider_observation", [False, True])
def test_public_interaction_mirror_preserves_private_record_and_discloses_only_allowed_fields(interaction, payload, provider_observation):
    from types import SimpleNamespace
    from aero_bench.trace.projector import project_public_run_event
    from aero_bench.runtime.events import RunEventAudience

    dispatcher = object.__new__(GatewayDispatcher)
    dispatcher._run = SimpleNamespace(execution_scope="executor_validation", scenario=SimpleNamespace(task=SimpleNamespace(package_id="urban.uav-recovery-demo.v1")))
    dispatcher._ledger = EventLedger(run_id="a" * 64)
    dispatcher._sync_event_indexes = lambda: None
    is_provider = provider_observation and interaction in {
        "agent.observation.v1",
        "agent.query_result.v1",
    }
    event_id = dispatcher._append_event(
        source="flight" if is_provider else "uav.policy.01", source_kind="provider" if is_provider else "agent", workload_id="flight" if is_provider else "uav.policy.01",
        event_type="agent.activity", time=SimulationTime(tick=0, sim_time_ns=0),
        payload={**payload, "authentication_id": "secret-session", "arguments_json": "private-arguments", "response_json": "private-response"},
        interaction_type=interaction, payload_schema_id=interaction,
        agent_id="uav.policy.01", visibility=dispatcher._agent_private_visibility("uav.policy.01"),
        provider_id="flight",
        command_id="command.unit" if interaction in {"agent.tool_call.v1", "agent.tool_result.v1"} else None,
        query_id="query.unit" if interaction in {"agent.query_call.v1", "agent.query_result.v1"} else None,
        observation_id="flight.telemetry" if interaction == "agent.observation.v1" else None,
    )
    private, public = dispatcher._ledger.records
    assert event_id == private.event.event_id
    assert "authentication_id" in ledger_payload(private)
    assert public.event.visibility == (RunEventAudience(scope="public"),)
    projected = project_public_run_event(public.event, public_event_ids={public.event.event_id})
    assert {item.name: item.value for item in projected.public_payload} == payload
    assert projected.parent_event_id is None
    dispatcher._ledger.verify()


def principal(run, authentication_id: str = "transport.session") -> AgentPrincipal:
    return AgentPrincipal(
        run_id=run.run_id,
        agent_id="reference.agent",
        authentication_id=authentication_id,
    )


def idempotent_run(run: ResolvedRunSpec) -> ResolvedRunSpec:
    payload = run.agents[0].model_dump(mode="json")
    payload["tools"][0]["idempotent"] = True
    return rebuild_run_agents(run, (type(run.agents[0]).model_validate(payload),))


def ledger_payload(record) -> dict[str, object]:
    return {item.name: item.value for item in record.event.payload}


class RecordingToolEndpoint:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.requests: list[CommandRequest] = []

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        self.requests.append(request)
        receipts = tuple(
            CommandReceipt(
                run_id=request.run_id,
                command_id=request.command_id,
                provider_id=self.provider_id,
                phase=phase,
                time=request.issued_at,
                detail=None,
            )
            for phase in ("received", "accepted", "applied", "completed")
        )
        return ProviderToolResult(
            result=ToolResult(
                command_id=request.command_id,
                receipts=receipts,
                response=(NamedValue(name="accepted", value=True),),
                observation=None,
            )
        )


class CommandEventToolEndpoint(RecordingToolEndpoint):
    def __init__(
        self,
        provider_id: str,
        *,
        event_id: str = "provider.command-event",
        event_provider_id: str | None = None,
        event_time: SimulationTime | None = None,
    ) -> None:
        super().__init__(provider_id)
        self.event_id = event_id
        self.event_provider_id = event_provider_id or provider_id
        self.event_time = event_time

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        outcome = await super().invoke(request)
        event = ProviderEvent(
            provider_id=self.event_provider_id,
            event_id=self.event_id,
            time=self.event_time or request.issued_at,
            payload_schema_id="test.command-event.v1",
            payload=(
                NamedValue(name="command_id", value=request.command_id),
                NamedValue(name="schema_id", value="test.command-event/v1"),
            ),
        )
        return ProviderToolResult(result=outcome.result, events=(event,))


class BlockingReservationToolEndpoint(RecordingToolEndpoint):
    def __init__(self, provider_id: str) -> None:
        super().__init__(provider_id)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return await super().invoke(request)


class SideEffectThenFailEndpoint:
    def __init__(self) -> None:
        self.calls = 0

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        self.calls += 1
        raise RuntimeError("response channel failed after the side effect")


class AdvancingToolEndpoint(RecordingToolEndpoint):
    def __init__(self, provider_id: str, current, ledger: EventLedger) -> None:
        super().__init__(provider_id)
        self.current = current
        self.ledger = ledger

    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        self.current[0] = SimulationTime(tick=1, sim_time_ns=100)
        self.ledger.append_event(
            source="flight",
            event_type="provider.advance",
            time=self.current[0],
        )
        return await super().invoke(request)


class RecordingObservationEndpoint:
    def __init__(self, schema) -> None:
        self.schema = schema
        self.calls = 0

    async def observe(
        self,
        *,
        run_id: str,
        agent_id: str,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self.calls += 1
        payload = (NamedValue(name="available", value=True),)
        return ObservationEnvelope(
            run_id=run_id,
            agent_id=agent_id,
            observation_id=observation_id,
            time=requested_at,
            payload_schema=self.schema,
            payload=payload,
            payload_digest=hashlib.sha256(
                canonical_json_bytes({"available": True})
            ).hexdigest(),
        )


class SlowObservationEndpoint(RecordingObservationEndpoint):
    async def observe(self, **arguments) -> ObservationEnvelope:
        self.calls += 1
        await asyncio.sleep(0.1)
        return await super().observe(**arguments)


class AdvancingObservationEndpoint(RecordingObservationEndpoint):
    def __init__(self, schema, current, ledger: EventLedger) -> None:
        super().__init__(schema)
        self.current = current
        self.ledger = ledger

    async def observe(self, **arguments) -> ObservationEnvelope:
        self.current[0] = SimulationTime(tick=1, sim_time_ns=100)
        self.ledger.append_event(
            source="observation",
            event_type="provider.advance",
            time=self.current[0],
        )
        return await super().observe(**arguments)


def test_gateway_authorizes_and_preserves_command_phases(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = RecordingToolEndpoint("flight")
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-1",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(NamedValue(name="target", value="asset-1"),),
    )

    result = asyncio.run(dispatcher.invoke(authenticated, request))

    assert [receipt.phase for receipt in result.receipts] == [
        "received",
        "accepted",
        "applied",
        "completed",
    ]
    assert endpoint.requests == [request]

    events = dispatcher._ledger.records
    assert [record.event.event_type for record in events] == [
        "command.issued",
        "gateway.dispatch",
        "command.receipt",
        "command.receipt",
        "command.receipt",
        "command.receipt",
        "command.validated",
    ]
    assert [ledger_payload(record)["phase"] for record in events[2:6]] == [
        "received",
        "accepted",
        "applied",
        "completed",
    ]
    assert "target" not in ledger_payload(events[0])
    assert "detail" not in ledger_payload(events[1])
    dispatcher._ledger.verify()


def test_command_status_indexes_external_provider_lifecycle_events(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    current = SimulationTime(tick=0, sim_time_ns=0)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": RecordingToolEndpoint("flight")},
        observation_endpoints={},
        authoritative_time=lambda: current,
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-indexed-status",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=current,
        arguments=(NamedValue(name="target", value="asset-1"),),
    )

    asyncio.run(dispatcher.invoke(principal(run), request))
    indexed_count = dispatcher._indexed_record_count
    ledger.append_event(
        source="flight",
        event_type="provider.command-proof",
        time=current,
        payload=(
            NamedValue(name="detail", value="physical proof"),
            NamedValue(name="phase", value="completed"),
        ),
        provider_id="flight",
        command_id=request.command_id,
    )

    assert dispatcher._indexed_record_count == indexed_count
    status = dispatcher.command_status(
        principal(run), command_id=request.command_id, requested_at=current
    )
    assert status.phase == "completed"
    assert status.detail == "physical proof"
    assert dispatcher._indexed_record_count == ledger.record_count


def test_latest_agent_observation_index_ingests_external_ledger_suffix(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    current = SimulationTime(tick=0, sim_time_ns=0)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={},
        authoritative_time=lambda: current,
        ledger=ledger,
    )
    ledger.append_event(
        source="observation",
        event_type="observation.validated",
        interaction_type="agent.observation.v1",
        time=current,
        payload=(NamedValue(name="observation_id", value="inspection.camera"),),
        agent_id="reference.agent",
        provider_id="observation",
        observation_id="inspection.camera",
    )

    event_id = dispatcher._latest_agent_observation_event_id(
        agent_id="reference.agent", observation_id="inspection.camera"
    )
    assert event_id == ledger.latest_record.event.event_id
    assert ledger.event_by_id(event_id) is ledger.latest_record.event
    assert dispatcher._indexed_record_count == ledger.record_count


def test_provider_command_events_are_private_and_appended_after_validation(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = CommandEventToolEndpoint("flight")
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-private-event",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(NamedValue(name="target", value="asset-1"),),
    )

    result = asyncio.run(dispatcher.invoke(principal(run), request))

    assert set(result.model_dump(mode="json")) == {
        "command_id",
        "receipts",
        "response",
        "observation",
    }
    assert [record.event.event_type for record in ledger.records] == [
        "command.issued",
        "gateway.dispatch",
        "command.receipt",
        "command.receipt",
        "command.receipt",
        "command.receipt",
        "provider.command-event",
        "command.validated",
    ]
    event = ledger.records[-2].event
    assert event.source == "flight"
    assert ledger_payload(ledger.records[-2]) == {
        "command_id": request.command_id,
        "schema_id": "test.command-event/v1",
    }
    ledger.verify()


def test_invalid_provider_command_event_is_not_partially_appended(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={
            "flight": CommandEventToolEndpoint("flight", event_provider_id="network")
        },
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-wrong-event-source",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(NamedValue(name="target", value="asset-1"),),
    )

    with pytest.raises(GatewayDispatchError, match="wrong Provider"):
        asyncio.run(dispatcher.invoke(principal(run), request))

    assert [record.event.event_type for record in ledger.records] == [
        "command.issued",
        "gateway.dispatch",
        "gateway.failure",
    ]
    assert ledger_payload(ledger.records[-1])["failure_class"] == (
        "result_validation_failed"
    )
    ledger.verify()


@pytest.mark.parametrize("event_id", ("run.completed", "run.aborted"))
def test_provider_cannot_inject_terminal_command_event(tmp_path, event_id: str) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={
            "flight": CommandEventToolEndpoint("flight", event_id=event_id)
        },
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id=f"cmd-reserved-{event_id}",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(NamedValue(name="target", value="asset-1"),),
    )

    with pytest.raises(GatewayDispatchError, match="reserved event type"):
        asyncio.run(dispatcher.invoke(principal(run), request))

    assert [record.event.event_type for record in ledger.records] == [
        "command.issued",
        "gateway.dispatch",
        "gateway.failure",
    ]
    ledger.verify()


def test_gateway_rejects_ungranted_and_reused_non_idempotent_commands(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = RecordingToolEndpoint("flight")
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-2",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )
    asyncio.run(dispatcher.invoke(authenticated, request))

    with pytest.raises(GatewayDispatchError, match="non-idempotent"):
        asyncio.run(dispatcher.invoke(authenticated, request))

    ungranted = request.model_copy(
        update={"command_id": "cmd-3", "tool_id": "flight.delete"}
    )
    with pytest.raises(GatewayDispatchError, match="not granted"):
        asyncio.run(dispatcher.invoke(authenticated, ungranted))


def test_non_idempotent_command_is_consumed_before_provider_response(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = SideEffectThenFailEndpoint()
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-response-loss",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )

    with pytest.raises(GatewayDispatchError, match="failed after command dispatch"):
        asyncio.run(dispatcher.invoke(authenticated, request))
    with pytest.raises(GatewayDispatchError, match="non-idempotent"):
        asyncio.run(dispatcher.invoke(authenticated, request))
    assert endpoint.calls == 1


def test_gateway_binds_commands_and_observations_to_authoritative_time(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    current = SimulationTime(tick=4, sim_time_ns=400_000_000)
    grant = run.agents[0].observations[0]
    endpoint = RecordingObservationEndpoint(grant.schema_file)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": RecordingToolEndpoint("flight")},
        observation_endpoints={"observation": endpoint},
        authoritative_time=lambda: current,
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)

    envelope = asyncio.run(
        dispatcher.observe(
            authenticated,
            observation_id="inspection.camera",
            requested_at=current,
        )
    )
    assert envelope.time == current

    stale = current.model_copy(update={"tick": 3, "sim_time_ns": 300_000_000})
    with pytest.raises(GatewayDispatchError, match="authoritative simulation time"):
        asyncio.run(
            dispatcher.observe(
                authenticated,
                observation_id="inspection.camera",
                requested_at=stale,
            )
        )
    assert endpoint.calls == 1

    stale_command = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-stale",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=stale,
        arguments=(),
    )
    with pytest.raises(GatewayDispatchError, match="authoritative simulation time"):
        asyncio.run(dispatcher.invoke(authenticated, stale_command))


def test_observation_call_has_an_explicit_timeout(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    agent_path = bundle.root / "agents/reference.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["observations"][0]["timeout_ms"] = 1
    agent_path.write_text(yaml.safe_dump(agent, sort_keys=False), encoding="utf-8")
    suite = yaml.safe_load(bundle.suite.read_text(encoding="utf-8"))
    suite["cases"][0]["agents"][0]["sha256"] = digest(agent_path)
    bundle.suite.write_text(yaml.safe_dump(suite, sort_keys=False), encoding="utf-8")
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    current = SimulationTime(tick=0, sim_time_ns=0)
    endpoint = SlowObservationEndpoint(run.agents[0].observations[0].schema_file)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={"observation": endpoint},
        authoritative_time=lambda: current,
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)

    with pytest.raises(GatewayDispatchError, match="1 ms contract timeout"):
        asyncio.run(
            dispatcher.observe(
                authenticated,
                observation_id="inspection.camera",
                requested_at=current,
            )
        )
    assert endpoint.calls == 1


def test_principal_cannot_spoof_agent_or_cross_run(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    endpoint = RecordingToolEndpoint("flight")
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-principal",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )

    with pytest.raises(GatewayDispatchError, match="does not match agent principal"):
        asyncio.run(
            dispatcher.invoke(
                AgentPrincipal(
                    run_id=run.run_id,
                    agent_id="spoofed.agent",
                    authentication_id="transport.session",
                ),
                request,
            )
        )
    with pytest.raises(GatewayDispatchError, match="another run"):
        asyncio.run(
            dispatcher.invoke(
                AgentPrincipal(
                    run_id="a" * 64,
                    agent_id="reference.agent",
                    authentication_id="transport.session",
                ),
                request,
            )
        )
    assert endpoint.requests == []
    assert ledger.records == ()


def test_idempotent_exact_retry_returns_cache_without_new_audit(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    original_run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    run = idempotent_run(original_run)
    endpoint = RecordingToolEndpoint("flight")
    ledger = EventLedger(run_id=run.run_id)
    current = [SimulationTime(tick=0, sim_time_ns=0)]
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: current[0],
        ledger=ledger,
    )
    authenticated = principal(run)
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-idempotent",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=current[0],
        arguments=(),
    )

    first = asyncio.run(dispatcher.invoke(authenticated, request))
    audited_count = len(ledger.records)
    current[0] = SimulationTime(tick=1, sim_time_ns=100)
    second = asyncio.run(dispatcher.invoke(authenticated, request))

    assert second is first
    assert endpoint.requests == [request]
    assert len(ledger.records) == audited_count
    ledger.verify()


def test_command_id_reservation_is_atomic_before_provider_await(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = BlockingReservationToolEndpoint("flight")
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=EventLedger(run_id=run.run_id),
    )
    authenticated = principal(run)
    request = CommandRequest(
        run_id=run.run_id,
        command_id="command.concurrent",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )

    async def scenario() -> ToolResult:
        first = asyncio.create_task(dispatcher.invoke(authenticated, request))
        await endpoint.started.wait()
        second = asyncio.create_task(dispatcher.invoke(authenticated, request))
        with pytest.raises(GatewayDispatchError, match="non-idempotent"):
            await second
        endpoint.release.set()
        return await first

    result = asyncio.run(scenario())

    assert result.command_id == request.command_id
    assert endpoint.calls == 1
    dispatcher._ledger.verify()


class WrongTimeToolEndpoint(RecordingToolEndpoint):
    async def invoke(self, request: CommandRequest) -> ProviderToolResult:
        self.requests.append(request)
        receipts = tuple(
            CommandReceipt(
                run_id=request.run_id,
                command_id=request.command_id,
                provider_id=self.provider_id,
                phase=phase,
                time=SimulationTime(
                    tick=request.issued_at.tick + 1,
                    sim_time_ns=request.issued_at.sim_time_ns,
                ),
                detail="private provider detail must not be recorded",
            )
            for phase in ("received", "accepted", "applied", "completed")
        )
        return ProviderToolResult(
            result=ToolResult(
                command_id=request.command_id,
                receipts=receipts,
                response=(NamedValue(name="accepted", value=True),),
                observation=None,
            )
        )


def test_provider_validation_failure_is_audited_without_exception_text(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    endpoint = WrongTimeToolEndpoint("flight")
    ledger = EventLedger(run_id=run.run_id)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-bad-receipt",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=SimulationTime(tick=0, sim_time_ns=0),
        arguments=(),
    )

    with pytest.raises(GatewayDispatchError, match="authoritative simulation time"):
        asyncio.run(dispatcher.invoke(principal(run), request))

    assert [record.event.event_type for record in ledger.records] == [
        "command.issued",
        "gateway.dispatch",
        "gateway.failure",
    ]
    failure = ledger_payload(ledger.records[-1])
    assert failure["failure_class"] == "result_validation_failed"
    assert "private provider detail" not in str(failure)
    ledger.verify()


def test_observation_audit_records_canonical_private_envelope(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    grant = run.agents[0].observations[0]
    endpoint = RecordingObservationEndpoint(grant.schema_file)
    ledger = EventLedger(run_id=run.run_id)
    current = SimulationTime(tick=2, sim_time_ns=200)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={"observation": endpoint},
        authoritative_time=lambda: current,
        ledger=ledger,
    )

    envelope = asyncio.run(
        dispatcher.observe(
            principal(run),
            observation_id="inspection.camera",
            requested_at=current,
        )
    )

    assert (
        envelope.payload_digest == ledger_payload(ledger.records[-1])["payload_digest"]
    )
    assert [record.event.event_type for record in ledger.records] == [
        "observation.requested",
        "observation.validated",
    ]
    validated = ledger_payload(ledger.records[-1])
    assert validated["payload_json"] == '{"available":true}'
    assert json.loads(validated["envelope_json"])["payload"] == [
        {"name": "available", "value": True}
    ]
    assert ledger.records[-1].event.visibility == (
        RunEventAudience(scope="agent", audience_id="reference.agent"),
        RunEventAudience(scope="private", audience_id=None),
    )
    ledger.verify()


def test_dispatcher_requires_the_harness_ledger_instance_type(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    with pytest.raises(TypeError, match="must be EventLedger"):
        GatewayDispatcher(
            run=run,
            bundle_root=bundle.root,
            tool_endpoints={},
            observation_endpoints={},
            authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
            ledger=object(),
        )


def test_dispatcher_requires_resolved_run_instance(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    with pytest.raises(TypeError, match="run must be ResolvedRunSpec"):
        GatewayDispatcher(
            run=object(),
            bundle_root=bundle.root,
            tool_endpoints={},
            observation_endpoints={},
            authoritative_time=lambda: SimulationTime(tick=0, sim_time_ns=0),
            ledger=EventLedger(run_id="a" * 64),
        )


def test_command_result_at_old_time_is_rejected_after_authority_advances(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    current = [SimulationTime(tick=0, sim_time_ns=0)]
    endpoint = AdvancingToolEndpoint("flight", current, ledger)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={"flight": endpoint},
        observation_endpoints={},
        authoritative_time=lambda: current[0],
        ledger=ledger,
    )
    request = CommandRequest(
        run_id=run.run_id,
        command_id="cmd-authority-advanced",
        agent_id="reference.agent",
        tool_id="flight.goto",
        issued_at=current[0],
        arguments=(),
    )

    with pytest.raises(GatewayDispatchError, match="advanced during Provider call"):
        asyncio.run(dispatcher.invoke(principal(run), request))

    assert [record.event.event_type for record in ledger.records] == [
        "command.issued",
        "gateway.dispatch",
        "provider.advance",
        "gateway.failure",
    ]
    assert ledger.records[-1].event.time == SimulationTime(tick=1, sim_time_ns=100)
    assert ledger_payload(ledger.records[-1])["failure_class"] == (
        "authority_advanced_during_call"
    )
    ledger.verify()


def test_observation_result_at_old_time_is_rejected_after_authority_advances(
    tmp_path,
) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    current = [SimulationTime(tick=0, sim_time_ns=0)]
    grant = run.agents[0].observations[0]
    endpoint = AdvancingObservationEndpoint(grant.schema_file, current, ledger)
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={"observation": endpoint},
        authoritative_time=lambda: current[0],
        ledger=ledger,
    )

    with pytest.raises(GatewayDispatchError, match="advanced during Observation call"):
        asyncio.run(
            dispatcher.observe(
                principal(run),
                observation_id="inspection.camera",
                requested_at=current[0],
            )
        )

    assert [record.event.event_type for record in ledger.records] == [
        "observation.requested",
        "provider.advance",
        "gateway.failure",
    ]
    assert ledger.records[-1].event.time == SimulationTime(tick=1, sim_time_ns=100)
    assert ledger_payload(ledger.records[-1])["failure_class"] == (
        "authority_advanced_during_call"
    )
    ledger.verify()


def test_gateway_event_append_rejects_time_before_ledger_tail(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    ledger = EventLedger(run_id=run.run_id)
    tail = SimulationTime(tick=1, sim_time_ns=100)
    ledger.append_event(
        source="harness", event_type="barrier.committed", time=tail
    )
    dispatcher = GatewayDispatcher(
        run=run,
        bundle_root=bundle.root,
        tool_endpoints={},
        observation_endpoints={},
        authoritative_time=lambda: tail,
        ledger=ledger,
    )

    with pytest.raises(GatewayDispatchError, match="moved backwards"):
        dispatcher._append_event(
            event_type="command.receipt",
            time=SimulationTime(tick=0, sim_time_ns=0),
            payload={},
        )
    assert len(ledger.records) == 1
    ledger.verify()
