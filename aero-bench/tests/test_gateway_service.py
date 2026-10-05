from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from aero_bench.config.models import AgentSpec, NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.gateway import AgentCredentialStore, GatewayRpcService
from aero_bench.gateway.contracts import (
    AgentPrincipal,
    ObservationEnvelope,
    QueryRequest,
    QueryResult,
    ToolResult,
)
from aero_bench.providers import ProviderManifest
from aero_bench.providers.rpc import (
    JsonLineRpcServer,
    JsonLineRpcTransport,
    ProviderRemoteError,
    parse_json_object,
)
from aero_bench.runtime import (
    AgentTurnCompletion,
    AgentTurnDecision,
    CommandReceipt,
    EventLedger,
    SimulationTime,
    StepReceipt,
)
from aero_bench.runtime.harness import HarnessCoordinator, HarnessRuntimeError
from aero_bench.serialization import canonical_json_bytes
from tests.support import (
    build_bundle,
    fake_finalization_receipt,
    rebuild_run_agents,
    resolve_bundle,
    with_fixture_provider_stages,
)


TOKEN = "a" * 64
SECOND_TOKEN = "b" * 64


class _RecordingDispatcher:
    def __init__(self, run_id: str, observation_schema) -> None:
        self.run_id = run_id
        self.observation_schema = observation_schema
        self.commands: list[tuple[AgentPrincipal, object]] = []
        self.observations: list[tuple[AgentPrincipal, str, SimulationTime]] = []
        self.queries: list[tuple[AgentPrincipal, QueryRequest]] = []

    async def invoke(self, principal, request) -> ToolResult:
        self.commands.append((principal, request))
        return ToolResult(
            command_id=request.command_id,
            receipts=(
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="received",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="accepted",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="applied",
                    time=request.issued_at,
                ),
                CommandReceipt(
                    run_id=request.run_id,
                    command_id=request.command_id,
                    provider_id="flight",
                    phase="completed",
                    time=request.issued_at,
                ),
            ),
            response=(),
        )

    async def observe(
        self,
        principal,
        *,
        observation_id: str,
        requested_at: SimulationTime,
    ) -> ObservationEnvelope:
        self.observations.append((principal, observation_id, requested_at))
        return ObservationEnvelope(
            run_id=self.run_id,
            agent_id=principal.agent_id,
            observation_id=observation_id,
            time=requested_at,
            payload_schema=self.observation_schema,
            payload=(),
            payload_digest=hashlib.sha256(b"{}").hexdigest(),
        )

    async def query(self, principal, request: QueryRequest) -> QueryResult:
        self.queries.append((principal, request))
        payload = (NamedValue(name="work_order_count", value=1),)
        return QueryResult(
            run_id=request.run_id,
            query_id=request.query_id,
            agent_id=request.agent_id,
            query_type=request.query_type,
            provider_id="business",
            observed_at=request.issued_at,
            payload_schema=self.observation_schema,
            payload=payload,
            payload_digest=hashlib.sha256(
                canonical_json_bytes({"work_order_count": 1})
            ).hexdigest(),
        )


class _HarnessSession:
    def __init__(self, manifest: ProviderManifest, run_id: str) -> None:
        self.manifest = manifest
        self._run_id = run_id
        self.shutdown_calls = 0

    async def prepare(self) -> None:
        return None

    async def reset(self, *, seed: int) -> StepReceipt:
        return StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=SimulationTime(tick=0, sim_time_ns=0),
            state_digest=hashlib.sha256(f"reset:{seed}".encode()).hexdigest(),
        )

    async def step_to(self, request) -> StepReceipt:
        return StepReceipt(
            run_id=self._run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=hashlib.sha256(
                f"{self.manifest.provider_id}:{request.target.tick}".encode()
            ).hexdigest(),
        )

    async def handle_command(self, request):
        return ()

    async def finalize(self, request):
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(b"snapshot").hexdigest()

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def _real_coordinator(
    run: ResolvedRunSpec,
) -> tuple[HarnessCoordinator, dict[str, _HarnessSession], EventLedger]:
    sessions = {
        provider.provider_id: _HarnessSession(
            ProviderManifest(
                provider_id=provider.provider_id,
                adapter=provider.adapter,
                implementation=provider.workload.implementation,
                runtime_image=provider.workload.runtime.image,
                config_digest=provider.config.file.sha256,
                capabilities=provider.capabilities,
                protocol_schema=provider.protocol_schema,
                artifact_requirements=provider.artifact_requirements,
            ),
            run.run_id,
        )
        for provider in run.environment.providers
    }
    ledger = EventLedger(run_id=run.run_id)
    return (
        HarnessCoordinator(run=run, providers=sessions, ledger=ledger),
        sessions,
        ledger,
    )


class _RecordingCoordinator:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.current = SimulationTime(tick=0, sim_time_ns=0)
        self.completions: list[AgentTurnCompletion] = []
        self.failed = False

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        self.completions.append(completion)
        return AgentTurnDecision(
            schema_version="aero-bench.agent-turn-decision/v1",
            run_id=self.run_id,
            status="terminated",
            at=self.current,
            active_agent_ids=(),
            missing_agent_ids=(),
        )


class _SequenceCoordinator:
    def __init__(self, decisions: tuple[AgentTurnDecision, ...]) -> None:
        self.current = SimulationTime(tick=0, sim_time_ns=0)
        self.decisions = list(decisions)
        self.completions: list[AgentTurnCompletion] = []
        self.failed = False

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        self.completions.append(completion)
        if not self.decisions:
            raise AssertionError("unexpected extra turn completion")
        decision = self.decisions.pop(0)
        self.current = decision.at
        return decision


class _TimelineDispatcher(_RecordingDispatcher):
    def __init__(self, run_id: str, observation_schema) -> None:
        super().__init__(run_id, observation_schema)
        self.command_started = asyncio.Event()
        self.command_release = asyncio.Event()
        self.timeline: list[str] = []

    async def invoke(self, principal, request) -> ToolResult:
        self.timeline.append("command.start")
        self.command_started.set()
        await self.command_release.wait()
        self.timeline.append("command.end")
        return await super().invoke(principal, request)


class _TimelineCoordinator(_RecordingCoordinator):
    def __init__(self, run_id: str, timeline: list[str]) -> None:
        super().__init__(run_id)
        self.timeline = timeline

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        self.timeline.append("turn.call")
        return await super().submit_agent_turn(completion)


class _FailedCoordinator(_RecordingCoordinator):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.failed = False

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        self.failed = True
        raise HarnessRuntimeError("private coordinator failure")


class _CancelledCoordinator(_RecordingCoordinator):
    def __init__(self, run_id: str) -> None:
        super().__init__(run_id)
        self.started = asyncio.Event()

    async def submit_agent_turn(
        self, completion: AgentTurnCompletion
    ) -> AgentTurnDecision:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.failed = True
            raise
        raise AssertionError("cancelled coordinator unexpectedly resumed")


def _fixture(
    tmp_path: Path,
    *,
    coordinator=None,
    run_override: ResolvedRunSpec | None = None,
    credential_tokens: dict[str, str] | None = None,
    dispatcher=None,
):
    bundle = build_bundle(tmp_path)
    run = (
        run_override
        or resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    observation_schema = next(
        grant.schema_file
        for grant in run.agents[0].observations
        if grant.observation_id == "inspection.camera"
    )
    if dispatcher is None:
        dispatcher = _RecordingDispatcher(run.run_id, observation_schema)
    if coordinator is None:
        coordinator = _RecordingCoordinator(run.run_id)
    credentials = AgentCredentialStore.from_canonical_json(
        run,
        canonical_json_bytes(credential_tokens or {"reference.agent": TOKEN}),
    )
    terminated = asyncio.Event()
    service = GatewayRpcService(
        run=run,
        credential_store=credentials,
        dispatcher=dispatcher,
        coordinator=coordinator,
        termination_requested=terminated,
    )
    return run, service, dispatcher, coordinator, terminated


def _two_agent_run(run: ResolvedRunSpec) -> ResolvedRunSpec:
    second_agent_payload = run.agents[0].model_dump(mode="json")
    second_agent_payload["agent_id"] = "agent.two"
    second_agent_payload["workload"]["implementation"]["component_id"] = "agent.two"
    second_agent_payload["artifact_requirements"] = []
    second_agent = AgentSpec.model_validate(second_agent_payload)
    return rebuild_run_agents(run, (run.agents[0], second_agent))


def _command(
    run,
    *,
    run_id: str | None = None,
    agent_id: str = "reference.agent",
    command_id: str = "command.one",
    issued_at: SimulationTime | None = None,
) -> dict[str, object]:
    current = SimulationTime(tick=0, sim_time_ns=0) if issued_at is None else issued_at
    return {
        "run_id": run.run_id if run_id is None else run_id,
        "command_id": command_id,
        "agent_id": agent_id,
        "tool_id": "flight.goto",
        "issued_at": current.model_dump(mode="json"),
        "arguments": [],
    }


def _completion(
    run,
    *,
    run_id: str | None = None,
    agent_id: str = "reference.agent",
    completion_id: str = "completion.one",
    at: SimulationTime | None = None,
    disposition: str = "finished",
) -> dict[str, object]:
    current = SimulationTime(tick=0, sim_time_ns=0) if at is None else at
    return {
        "schema_version": "aero-bench.agent-turn-completion/v1",
        "run_id": run.run_id if run_id is None else run_id,
        "agent_id": agent_id,
        "completion_id": completion_id,
        "at": current.model_dump(mode="json"),
        "disposition": disposition,
        "command_ids": [],
        "observation_ids": [],
    }


def _query(run, *, agent_id: str = "reference.agent") -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "query_id": "query.one",
        "agent_id": agent_id,
        "query_type": "business.work-orders",
        "issued_at": {"tick": 0, "sim_time_ns": 0},
        "arguments": [],
    }


def test_credential_store_is_strict_and_derives_a_nonsecret_principal(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    store = AgentCredentialStore(
        run,
        canonical_json_bytes({"reference.agent": TOKEN}),
    )

    principal = store.authenticate(TOKEN)

    assert principal.run_id == run.run_id
    assert principal.agent_id == "reference.agent"
    assert principal.authentication_id == (
        "auth." + hashlib.sha256(TOKEN.encode("ascii")).hexdigest()
    )
    assert TOKEN not in repr(principal)
    with pytest.raises(Exception, match="authentication failed"):
        store.authenticate("b" * 64)

    for raw in (
        b'{"reference.agent":"'
        + TOKEN.encode()
        + b'", "extra":"'
        + TOKEN.encode()
        + b'"}',
        b'{"reference.agent":NaN}',
        b'{"reference.agent":"' + (b"0" * 64) + b'"}',
        b'{"reference.agent":"' + TOKEN.encode() + b'" }',
    ):
        with pytest.raises(Exception):
            AgentCredentialStore(run, raw)


def test_loopback_gateway_server_handles_probe_and_authenticated_calls(
    tmp_path,
) -> None:
    asyncio.run(_loopback_gateway_server(tmp_path))


async def _loopback_gateway_server(tmp_path: Path) -> None:
    run, service, dispatcher, coordinator, terminated = _fixture(tmp_path)
    server = await JsonLineRpcServer(service).start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    port = int(sockets[0].getsockname()[1])
    transport = await JsonLineRpcTransport.connect(
        type("Endpoint", (), {"host": "127.0.0.1", "port": port})(),
        component="gateway",
    )
    try:
        probe = await transport.request("probe", {})
        assert probe == {
            "current": {"sim_time_ns": 0, "tick": 0},
            "run_id": run.run_id,
            "schema_version": "aero-bench.gateway-probe/v1",
            "status": "ready",
        }
        assert "reference.agent" not in json.dumps(probe)
        assert "flight" not in json.dumps(probe)
        assert TOKEN not in json.dumps(probe)

        command = await transport.request(
            "command.invoke", {"token": TOKEN, "command": _command(run)}
        )
        assert command["command_id"] == "command.one"
        assert len(dispatcher.commands) == 1
        assert dispatcher.commands[0][0].authentication_id.startswith("auth.")
        assert TOKEN not in json.dumps(command)

        observation = await transport.request(
            "observation.get",
            {
                "token": TOKEN,
                "observation_id": "inspection.camera",
                "requested_at": {"tick": 0, "sim_time_ns": 0},
            },
        )
        assert observation["observation_id"] == "inspection.camera"
        assert len(dispatcher.observations) == 1

        query = await transport.request(
            "query.invoke", {"token": TOKEN, "query": _query(run)}
        )
        assert query["query_id"] == "query.one"
        assert query["provider_id"] == "business"
        assert len(dispatcher.queries) == 1
        assert TOKEN not in json.dumps(query)

        turn = await transport.request(
            "turn.complete", {"token": TOKEN, "completion": _completion(run)}
        )
        assert turn["status"] == "terminated"
        assert terminated.is_set()
        assert len(coordinator.completions) == 1
    finally:
        await transport.close()
        server.close()
        await server.wait_closed()


def test_turn_waits_for_slow_gateway_operation_and_preserves_ledger_order(
    tmp_path: Path,
) -> None:
    source_bundle = build_bundle(tmp_path / "source")
    run = resolve_bundle(source_bundle.suite, executor_kind="docker_reference")[0]
    observation_schema = run.agents[0].observations[0].schema_file
    dispatcher = _TimelineDispatcher(run.run_id, observation_schema)
    coordinator = _TimelineCoordinator(run.run_id, dispatcher.timeline)
    _, service, _, _, _ = _fixture(
        tmp_path / "service",
        run_override=run,
        dispatcher=dispatcher,
        coordinator=coordinator,
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        command_task = asyncio.create_task(
            service("command.invoke", {"token": TOKEN, "command": _command(run)})
        )
        await dispatcher.command_started.wait()
        turn_task = asyncio.create_task(
            service(
                "turn.complete",
                {
                    "token": TOKEN,
                    "completion": _completion(run),
                },
            )
        )
        dispatcher.command_release.set()
        return await asyncio.gather(command_task, turn_task)

    command_response, turn_response = asyncio.run(scenario())

    assert command_response["command_id"] == "command.one"
    assert turn_response["status"] == "terminated"
    assert dispatcher.timeline.index("command.end") < dispatcher.timeline.index(
        "turn.call"
    )


def test_round_gate_blocks_submitted_agent_and_reopens_after_advance(
    tmp_path: Path,
) -> None:
    source_bundle = build_bundle(tmp_path / "source")
    source_run = resolve_bundle(source_bundle.suite, executor_kind="docker_reference")[
        0
    ]
    run = _two_agent_run(source_run)
    zero = SimulationTime(tick=0, sim_time_ns=0)
    one = SimulationTime(tick=1, sim_time_ns=run.environment.clock.step_ns)
    coordinator = _SequenceCoordinator(
        (
            AgentTurnDecision(
                schema_version="aero-bench.agent-turn-decision/v1",
                run_id=run.run_id,
                status="waiting",
                at=zero,
                active_agent_ids=("reference.agent", "agent.two"),
                missing_agent_ids=("agent.two",),
            ),
            AgentTurnDecision(
                schema_version="aero-bench.agent-turn-decision/v1",
                run_id=run.run_id,
                status="advanced",
                at=one,
                active_agent_ids=("reference.agent", "agent.two"),
                missing_agent_ids=(),
            ),
            AgentTurnDecision(
                schema_version="aero-bench.agent-turn-decision/v1",
                run_id=run.run_id,
                status="terminated",
                at=one,
                active_agent_ids=(),
                missing_agent_ids=(),
            ),
        )
    )
    _, service, dispatcher, _, _ = _fixture(
        tmp_path / "service",
        run_override=run,
        coordinator=coordinator,
        credential_tokens={"reference.agent": TOKEN, "agent.two": SECOND_TOKEN},
    )

    async def scenario() -> None:
        first_command = await service(
            "command.invoke", {"token": TOKEN, "command": _command(run)}
        )
        assert first_command["command_id"] == "command.one"

        first = await service(
            "turn.complete",
            {
                "token": TOKEN,
                "completion": _completion(run, completion_id="completion.first"),
            },
        )
        assert first["status"] == "waiting"

        blocked_command = await service(
            "command.invoke",
            {"token": TOKEN, "command": _command(run, command_id="command.blocked")},
        )
        assert blocked_command["error"]["code"] == "turn.submitted"

        blocked_observation = await service(
            "observation.get",
            {
                "token": TOKEN,
                "observation_id": "inspection.camera",
                "requested_at": zero.model_dump(mode="json"),
            },
        )
        assert blocked_observation["error"]["code"] == "turn.submitted"

        other_command = await service(
            "command.invoke",
            {
                "token": SECOND_TOKEN,
                "command": _command(
                    run, agent_id="agent.two", command_id="command.two"
                ),
            },
        )
        assert other_command["command_id"] == "command.two"

        await service(
            "observation.get",
            {
                "token": SECOND_TOKEN,
                "observation_id": "inspection.camera",
                "requested_at": zero.model_dump(mode="json"),
            },
        )
        advanced = await service(
            "turn.complete",
            {
                "token": SECOND_TOKEN,
                "completion": _completion(
                    run, agent_id="agent.two", completion_id="completion.second"
                ),
            },
        )
        assert advanced["status"] == "advanced"

        next_command = await service(
            "command.invoke",
            {
                "token": TOKEN,
                "command": _command(run, command_id="command.three", issued_at=one),
            },
        )
        assert next_command["command_id"] == "command.three"

        stale_turn = await service(
            "turn.complete",
            {"token": TOKEN, "completion": _completion(run)},
        )
        assert stale_turn["error"]["code"] == "turn.stale"

        terminated = await service(
            "turn.complete",
            {
                "token": TOKEN,
                "completion": _completion(
                    run, completion_id="completion.final", at=one
                ),
            },
        )
        assert terminated["status"] == "terminated"

        closed = await service(
            "command.invoke",
            {
                "token": TOKEN,
                "command": _command(run, command_id="command.closed", issued_at=one),
            },
        )
        assert closed["error"]["code"] == "gateway.closed"
        assert (await service("probe", {}))["status"] == "ready"

    asyncio.run(scenario())
    assert len(dispatcher.commands) == 3
    assert len(dispatcher.observations) == 1


def test_failed_coordinator_closes_gateway_for_new_operations(tmp_path: Path) -> None:
    source_bundle = build_bundle(tmp_path / "source")
    run = resolve_bundle(source_bundle.suite, executor_kind="docker_reference")[0]
    coordinator = _FailedCoordinator(run.run_id)
    _, service, dispatcher, _, _ = _fixture(
        tmp_path / "service",
        run_override=run,
        coordinator=coordinator,
    )

    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        failed_turn = await service(
            "turn.complete",
            {"token": TOKEN, "completion": _completion(run)},
        )
        closed_command = await service(
            "command.invoke",
            {
                "token": TOKEN,
                "command": _command(run, command_id="command.closed"),
            },
        )
        return failed_turn, closed_command

    failed_turn, closed_command = asyncio.run(scenario())

    assert failed_turn == {
        "error": {"code": "turn.failed", "detail": "agent turn submission failed"}
    }
    assert closed_command["error"]["code"] == "gateway.closed"
    assert not dispatcher.commands


def test_cancelled_turn_closes_gateway_without_leaving_gate_in_progress(
    tmp_path: Path,
) -> None:
    source_bundle = build_bundle(tmp_path / "source")
    run = resolve_bundle(source_bundle.suite, executor_kind="docker_reference")[0]
    coordinator = _CancelledCoordinator(run.run_id)
    _, service, dispatcher, _, terminated = _fixture(
        tmp_path / "service",
        run_override=run,
        coordinator=coordinator,
    )

    async def scenario() -> dict[str, object]:
        submission = asyncio.create_task(
            service(
                "turn.complete",
                {"token": TOKEN, "completion": _completion(run)},
            )
        )
        await coordinator.started.wait()
        submission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submission

        closed_command = await service(
            "command.invoke",
            {"token": TOKEN, "command": _command(run, command_id="command.closed")},
        )
        assert not dispatcher.commands
        return closed_command

    closed_command = asyncio.run(scenario())

    assert closed_command["error"]["code"] == "gateway.closed"
    assert terminated.is_set()


def test_cancelled_gate_after_terminal_harness_decision_fails_closed(
    tmp_path: Path,
) -> None:
    source_bundle = build_bundle(tmp_path / "source")
    run = with_fixture_provider_stages(
        resolve_bundle(source_bundle.suite, executor_kind="docker_reference")[0]
    )
    coordinator, sessions, ledger = _real_coordinator(run)
    _, service, dispatcher, _, terminated = _fixture(
        tmp_path / "service",
        run_override=run,
        coordinator=coordinator,
    )
    end_turn_started = asyncio.Event()
    end_turn_release = asyncio.Event()
    original_end_turn = service._round_gate.end_turn

    async def controllable_end_turn(**kwargs):
        end_turn_started.set()
        await end_turn_release.wait()
        await original_end_turn(**kwargs)

    service._round_gate.end_turn = controllable_end_turn

    async def scenario() -> None:
        await coordinator.prepare()
        submission = asyncio.create_task(
            service(
                "turn.complete",
                {"token": TOKEN, "completion": _completion(run)},
            )
        )
        await end_turn_started.wait()
        assert coordinator.shutdown_complete
        assert not coordinator.failed

        submission.cancel()
        with pytest.raises(asyncio.CancelledError):
            await submission

        closed_command = await service(
            "command.invoke",
            {"token": TOKEN, "command": _command(run, command_id="command.closed")},
        )
        assert closed_command["error"]["code"] == "gateway.closed"
        assert not dispatcher.commands

    asyncio.run(scenario())
    assert terminated.is_set()
    assert not coordinator.failed
    assert coordinator.shutdown_complete
    assert all(session.shutdown_calls == 1 for session in sessions.values())
    assert [record.event.event_type for record in ledger.records].count(
        "run.aborted"
    ) == 0
    assert ledger.records[-1].event.event_type == "run.completed"
    ledger.verify()


def test_loopback_gateway_rejects_forgery_cross_run_and_extra_fields(tmp_path) -> None:
    asyncio.run(_loopback_gateway_rejections(tmp_path))


async def _loopback_gateway_rejections(tmp_path: Path) -> None:
    run, service, dispatcher, _, _ = _fixture(tmp_path)
    server = await JsonLineRpcServer(service).start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    port = int(sockets[0].getsockname()[1])
    transport = await JsonLineRpcTransport.connect(
        type("Endpoint", (), {"host": "127.0.0.1", "port": port})(),
        component="gateway",
    )
    try:
        forged = "f" * 64
        with pytest.raises(ProviderRemoteError) as raised:
            await transport.request(
                "command.invoke", {"token": forged, "command": _command(run)}
            )
        assert raised.value.code == "authentication.failed"
        assert forged not in raised.value.detail

        with pytest.raises(ProviderRemoteError) as raised:
            await transport.request(
                "command.invoke",
                {"token": TOKEN, "command": _command(run, run_id="b" * 64)},
            )
        assert raised.value.code == "authorization.denied"
        assert not dispatcher.commands

        with pytest.raises(ProviderRemoteError) as raised:
            await transport.request(
                "observation.get",
                {
                    "token": TOKEN,
                    "observation_id": "inspection.camera",
                    "requested_at": {"tick": 0, "sim_time_ns": 0},
                    "extra": "rejected",
                },
            )
        assert raised.value.code == "request.invalid"
        assert TOKEN not in raised.value.detail
    finally:
        await transport.close()
        server.close()
        await server.wait_closed()


def test_shared_loopback_boundary_rejects_duplicate_and_nonfinite_frames(
    tmp_path,
) -> None:
    asyncio.run(_loopback_shared_rejections(tmp_path))


async def _loopback_shared_rejections(tmp_path: Path) -> None:
    _, service, _, _, _ = _fixture(tmp_path)
    server = await JsonLineRpcServer(service).start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    port = int(sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"operation":"probe","operation":"probe"}\n')
        await writer.drain()
        duplicate = parse_json_object(await reader.readline())
        assert duplicate["error"]["code"] == "request.invalid"
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"operation":"probe","value":NaN}\n')
        await writer.drain()
        nonfinite = parse_json_object(await reader.readline())
        assert nonfinite["error"]["code"] == "request.invalid"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
