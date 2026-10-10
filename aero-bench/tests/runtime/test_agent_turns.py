from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aero_bench.config.models import AgentSpec, EnvironmentSpec, NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers import ProviderManifest
from aero_bench.runtime import (
    AgentTurnCompletion,
    AgentTurnDecision,
    BusinessEnvironmentStageResult,
    EnuLinearVelocity,
    EventLedger,
    MotionStageResult,
    NedLinearVelocity,
    NetworkStageResult,
    ProviderEvent,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.serialization import canonical_json_bytes
from aero_bench.runtime.harness import HarnessCoordinator, HarnessRuntimeError
from tests.support import (
    as_formal_run,
    build_bundle,
    fake_finalization_receipt,
    rebuild_run_agents,
    resolve_bundle,
    with_fixture_provider_stages,
)


class _CountingSession:
    def __init__(self, manifest: ProviderManifest, run: ResolvedRunSpec) -> None:
        self.manifest = manifest
        self._run = run
        self.step_calls = 0
        self.finalize_calls = 0
        self.finalization_requests: list[object] = []
        self.shutdown_calls = 0
        self.fail_prepare = False
        self.fail_step = False
        self.fail_finalize = False
        self.fail_shutdown = False
        self.step_events: tuple[ProviderEvent, ...] = ()
        self.shutdown_started: asyncio.Event | None = None
        self.shutdown_release: asyncio.Event | None = None

    async def prepare(self) -> None:
        if self.fail_prepare:
            raise RuntimeError("prepare backend failed")
        return None

    async def reset(self, *, seed: int) -> StepReceipt:
        return StepReceipt(
            run_id=self._run.run_id,
            provider_id=self.manifest.provider_id,
            reached=SimulationTime(tick=0, sim_time_ns=0),
            state_digest=hashlib.sha256(f"reset:{seed}".encode()).hexdigest(),
        )

    async def step_stage(self, request):
        self.step_calls += 1
        if self.fail_step:
            raise RuntimeError("step backend failed")

        samples = tuple(
            _state_sample(self._run, entity, request.target)
            for entity in self._run.scenario.entities
            if request.stage == "motion"
            and entity.state == "dynamic"
            and entity.owner_id == self.manifest.provider_id
        )
        contribution_fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": self._run.run_id,
            "scenario_digest": self._run.scenario.scenario_digest,
            "at": request.target,
            "stage": request.stage,
            "provider_id": self.manifest.provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        unsigned_contribution = SceneContribution.model_construct(
            **contribution_fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(
            unsigned_contribution
        )
        contribution = SceneContribution(
            **contribution_fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(
                unsigned_contribution.model_copy(
                    update={"payload_digest": payload_digest}
                )
            ),
        )
        receipt = StepReceipt(
            run_id=self._run.run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=hashlib.sha256(
                f"{self.manifest.provider_id}:{request.stage}:{request.target.tick}".encode()
            ).hexdigest(),
            events=self.step_events,
        )
        result_fields = {
            "schema_version": "aero-bench.provider-stage-result/v1",
            "run_id": self._run.run_id,
            "scenario_digest": self._run.scenario.scenario_digest,
            "provider_id": self.manifest.provider_id,
            "target": request.target,
            "stage": request.stage,
            "step_receipt": receipt,
            "step_receipt_digest": step_receipt_digest_value(receipt),
            "contribution": contribution,
            "predecessor_barriers": (
                () if request.stage == "motion" else request.predecessor_barriers
            ),
        }
        if request.stage == "motion":
            return MotionStageResult(**result_fields)
        result_fields["input_scene_state_digest"] = request.scene_state_digest
        if request.stage == "network":
            return NetworkStageResult(**result_fields)
        return BusinessEnvironmentStageResult(**result_fields)

    async def handle_command(self, request):
        return ()

    async def finalize(self, request):
        self.finalize_calls += 1
        self.finalization_requests.append(request)
        if self.fail_finalize:
            raise RuntimeError("finalize backend failed")
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(b"snapshot").hexdigest()

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.shutdown_started is not None:
            self.shutdown_started.set()
        if self.shutdown_release is not None:
            await self.shutdown_release.wait()
        if self.fail_shutdown:
            raise RuntimeError("shutdown backend failed")


def _state_sample(run: ResolvedRunSpec, entity, at: SimulationTime) -> StateSample:
    fields = {
        "schema_version": "aero-bench.state-sample/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "at": at,
        "stage": "motion",
        "entity_id": entity.entity_id,
        "provider_id": entity.owner_id,
        "sample_kind": "dynamic",
        "pose": entity.initial_pose,
        "linear_velocity_enu": EnuLinearVelocity(
            east_mps=0.0,
            north_mps=0.0,
            up_mps=0.0,
        ),
        "linear_velocity_ned": NedLinearVelocity(
            north_mps=0.0,
            east_mps=0.0,
            down_mps=0.0,
        ),
    }
    unsigned = StateSample.model_construct(**fields, sample_digest="0" * 64)
    return StateSample(
        **fields,
        sample_digest=state_sample_digest_value(unsigned),
    )


def _coordinator(
    root: Path,
    *,
    agent_ids: tuple[str, ...] = ("reference.agent",),
    max_steps: int | None = None,
    formal: bool = False,
) -> tuple[
    HarnessCoordinator, EventLedger, ResolvedRunSpec, dict[str, _CountingSession]
]:
    run = with_fixture_provider_stages(
        resolve_bundle(
            build_bundle(root).suite,
            executor_kind="docker_reference",
        )[0]
    )
    if formal:
        run = as_formal_run(run)
    environment = run.environment
    if max_steps is not None:
        clock = environment.clock.model_copy(update={"max_steps": max_steps})
        environment = environment.model_copy(update={"clock": clock})
        run = _rebuild_run(run, environment=environment)
    if tuple(agent.agent_id for agent in run.agents) != agent_ids:
        template = run.agents[0]
        agents = tuple(
            _agent_for(
                template,
                agent_id,
                preserve_artifacts=index == 0,
            )
            for index, agent_id in enumerate(agent_ids)
        )
        run = _rebuild_run(run, agents=agents)
    sessions = {
        provider.provider_id: _CountingSession(
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
            run,
        )
        for provider in run.environment.providers
    }
    ledger = EventLedger(run_id=run.run_id)
    coordinator = HarnessCoordinator(
        run=run,
        providers=sessions,
        ledger=ledger,
    )
    return coordinator, ledger, run, sessions


def _rebuild_run(
    run: ResolvedRunSpec,
    *,
    environment: EnvironmentSpec | None = None,
    agents: tuple[AgentSpec, ...] | None = None,
) -> ResolvedRunSpec:
    if environment is not None:
        payload = run.model_dump(mode="json", exclude={"run_id"})
        payload["environment"] = environment.model_dump(mode="json")
        run_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        run = ResolvedRunSpec.model_validate({"run_id": run_id, **payload})
    if agents is not None:
        run = rebuild_run_agents(run, agents)
    return run


def _agent_for(
    template: AgentSpec,
    agent_id: str,
    *,
    preserve_artifacts: bool,
) -> AgentSpec:
    payload = template.model_dump(mode="json")
    payload["agent_id"] = agent_id
    payload["workload"]["implementation"]["component_id"] = agent_id
    if preserve_artifacts:
        for requirement in payload["artifact_requirements"]:
            requirement["producer_id"] = agent_id
    else:
        payload["tools"] = []
        payload["observations"] = []
        payload["artifact_requirements"] = []
    return AgentSpec.model_validate(payload)


def _completion(
    coordinator: HarnessCoordinator,
    run_id: str,
    agent_id: str,
    completion_id: str,
    *,
    disposition: str = "advance",
    at: SimulationTime | None = None,
    command_ids: tuple[str, ...] = (),
    observation_ids: tuple[str, ...] = (),
) -> AgentTurnCompletion:
    return AgentTurnCompletion(
        schema_version="aero-bench.agent-turn-completion/v1",
        run_id=run_id,
        agent_id=agent_id,
        completion_id=completion_id,
        at=coordinator.current if at is None else at,
        disposition=disposition,
        command_ids=command_ids,
        observation_ids=observation_ids,
    )


def test_models_are_strict_and_references_are_explicit() -> None:
    completion = AgentTurnCompletion(
        schema_version="aero-bench.agent-turn-completion/v1",
        run_id="a" * 64,
        agent_id="agent.one",
        completion_id="completion.one",
        at=SimulationTime(tick=0, sim_time_ns=0),
        disposition="finished",
        command_ids=(),
        observation_ids=(),
    )
    assert completion.model_config["frozen"] is True
    with pytest.raises(ValueError, match="command_ids must be unique"):
        AgentTurnCompletion(
            schema_version=completion.schema_version,
            run_id=completion.run_id,
            agent_id=completion.agent_id,
            completion_id=completion.completion_id,
            at=completion.at,
            disposition=completion.disposition,
            command_ids=("command.one", "command.one"),
            observation_ids=(),
        )

    decision = AgentTurnDecision(
        schema_version="aero-bench.agent-turn-decision/v1",
        run_id="a" * 64,
        status="waiting",
        at=SimulationTime(tick=0, sim_time_ns=0),
        active_agent_ids=("agent.one",),
        missing_agent_ids=("agent.one",),
    )
    assert decision.model_config["extra"] == "forbid"


def test_harness_coordinator_requires_resolved_run() -> None:
    with pytest.raises(TypeError, match="run must be ResolvedRunSpec"):
        HarnessCoordinator(
            run=object(), providers={}, ledger=EventLedger(run_id="a" * 64)
        )


def test_two_agents_wait_until_all_submit_then_advance_once(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(
        tmp_path, agent_ids=("reference.agent", "agent.two")
    )

    async def scenario() -> None:
        await coordinator.prepare()
        first = await coordinator.submit_agent_turn(
            _completion(coordinator, run.run_id, "reference.agent", "completion.one")
        )
        assert first.status == "waiting"
        assert first.at == SimulationTime(tick=0, sim_time_ns=0)
        assert first.active_agent_ids == ("agent.two", "reference.agent")
        assert first.missing_agent_ids == ("agent.two",)
        assert sum(session.step_calls for session in sessions.values()) == 0

        second = await coordinator.submit_agent_turn(
            _completion(coordinator, run.run_id, "agent.two", "completion.two")
        )
        assert second.status == "advanced"
        assert second.at.tick == 1
        assert all(session.step_calls == 1 for session in sessions.values())

    asyncio.run(scenario())
    ledger.verify()


def test_provider_step_accepts_event_within_barrier_interval(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)
    event_time = SimulationTime(
        tick=1,
        sim_time_ns=run.environment.clock.step_ns,
    )
    sessions["flight"].step_events = (
        ProviderEvent(
            provider_id="flight",
            event_id="flight.telemetry",
            time=event_time,
            payload_schema_id="flight.telemetry.v1",
            payload=(NamedValue(name="sample", value=1),),
        ),
    )

    async def scenario() -> None:
        await coordinator.prepare()
        decision = await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "reference.agent",
                "completion.one",
            )
        )
        assert decision.status == "advanced"

    asyncio.run(scenario())
    matching = tuple(
        record
        for record in ledger.records
        if record.event.event_type == "flight.telemetry"
    )
    assert len(matching) == 1
    assert matching[0].event.source == "flight"
    assert matching[0].event.time == event_time
    ledger.verify()


@pytest.mark.parametrize("event_id", ("run.completed", "run.aborted"))
def test_provider_step_rejects_reserved_terminal_event(
    tmp_path: Path,
    event_id: str,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)
    event_time = SimulationTime(
        tick=1,
        sim_time_ns=run.environment.clock.step_ns,
    )
    sessions["flight"].step_events = (
        ProviderEvent(
            provider_id="flight",
            event_id=event_id,
            time=event_time,
            payload_schema_id="malicious.terminal.v1",
        ),
    )

    async def scenario() -> None:
        await coordinator.prepare()
        with pytest.raises(HarnessRuntimeError, match="reserved event type"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.one",
                )
            )

    asyncio.run(scenario())
    assert not any(record.event.event_type == event_id for record in ledger.records)
    ledger.verify()


def test_provider_step_rejects_spoofed_harness_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)
    session = sessions["flight"]
    step_stage = session.step_stage

    async def malicious_step(request):
        result = await step_stage(request)
        spoofed = ProviderEvent(
            provider_id="harness",
            event_id="flight.telemetry",
            time=request.target,
            payload_schema_id="malicious.source.v1",
        )
        receipt = result.step_receipt.model_copy(update={"events": (spoofed,)})
        return result.model_copy(
            update={
                "step_receipt": receipt,
                "step_receipt_digest": step_receipt_digest_value(receipt),
            }
        )

    monkeypatch.setattr(session, "step_stage", malicious_step)

    async def scenario() -> None:
        await coordinator.prepare()
        with pytest.raises(HarnessRuntimeError, match="strict revalidation"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.one",
                )
            )

    asyncio.run(scenario())
    assert not any(
        record.event.source == "harness"
        and record.event.event_type == "flight.telemetry"
        for record in ledger.records
    )
    ledger.verify()


def test_provider_step_rejects_event_before_previous_barrier(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path, max_steps=2)

    async def scenario() -> None:
        await coordinator.prepare()
        first = await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "reference.agent",
                "completion.one",
            )
        )
        assert first.status == "advanced"
        sessions["flight"].step_events = (
            ProviderEvent(
                provider_id="flight",
                event_id="flight.stale-telemetry",
                time=SimulationTime(tick=0, sim_time_ns=0),
                payload_schema_id="flight.telemetry.v1",
            ),
        )
        with pytest.raises(HarnessRuntimeError, match="stage target"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.two",
                )
            )

    asyncio.run(scenario())
    assert not any(
        record.event.event_type == "flight.stale-telemetry" for record in ledger.records
    )
    ledger.verify()


def test_mixed_finished_and_advance_keeps_only_active_agents(tmp_path: Path) -> None:
    coordinator, _, run, sessions = _coordinator(
        tmp_path, agent_ids=("reference.agent", "agent.two")
    )

    async def scenario() -> None:
        await coordinator.prepare()
        waiting = await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "reference.agent",
                "completion.one",
                disposition="finished",
            )
        )
        assert waiting.status == "waiting"
        assert waiting.active_agent_ids == ("agent.two",)
        assert waiting.missing_agent_ids == ("agent.two",)

        advanced = await coordinator.submit_agent_turn(
            _completion(coordinator, run.run_id, "agent.two", "completion.two")
        )
        assert advanced.status == "advanced"
        assert advanced.active_agent_ids == ("agent.two",)
        assert all(session.step_calls == 1 for session in sessions.values())

        with pytest.raises(HarnessRuntimeError, match="finished Agent"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.three",
                )
            )

    asyncio.run(scenario())


def test_all_finished_shuts_down_once_and_terminates(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(
        tmp_path, agent_ids=("reference.agent", "agent.two")
    )

    async def scenario() -> None:
        await coordinator.prepare()
        await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "reference.agent",
                "completion.one",
                disposition="finished",
            )
        )
        decision = await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "agent.two",
                "completion.two",
                disposition="finished",
            )
        )
        assert decision.status == "terminated"
        assert decision.at == SimulationTime(tick=0, sim_time_ns=0)
        assert decision.active_agent_ids == ()
        assert decision.missing_agent_ids == ()
        assert all(session.shutdown_calls == 1 for session in sessions.values())
        assert (
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "agent.two",
                    "completion.two",
                    disposition="finished",
                )
            )
            == decision
        )

    asyncio.run(scenario())
    ledger.verify()
    assert ledger.records[-1].event.event_type == "run.completed"
    for session in sessions.values():
        assert session.finalize_calls == 1
        request = session.finalization_requests[0]
        assert request.event_chain_root == ledger.chain_root
        assert request.terminal_time == SimulationTime(tick=0, sim_time_ns=0)


def test_max_steps_rejects_advance_without_implicit_termination(tmp_path: Path) -> None:
    coordinator, _, run, sessions = _coordinator(tmp_path, max_steps=1)

    async def scenario() -> None:
        await coordinator.prepare()
        first = await coordinator.submit_agent_turn(
            _completion(coordinator, run.run_id, "reference.agent", "completion.one")
        )
        assert first.status == "advanced"
        with pytest.raises(HarnessRuntimeError, match="max_steps"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.two",
                )
            )
        assert all(session.shutdown_calls == 0 for session in sessions.values())

    asyncio.run(scenario())


def test_provider_advance_failure_permanently_fails_harness(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        for session in sessions.values():
            session.fail_step = True
        completion = _completion(
            coordinator,
            run.run_id,
            "reference.agent",
            "completion.one",
        )
        with pytest.raises(HarnessRuntimeError, match="Provider motion-step-1"):
            await coordinator.submit_agent_turn(completion)
        with pytest.raises(HarnessRuntimeError, match="permanently failed"):
            await coordinator.submit_agent_turn(completion)
        with pytest.raises(HarnessRuntimeError, match="permanently failed"):
            await coordinator.shutdown()

    asyncio.run(scenario())
    assert not any(
        record.event.event_type == "agent.turn-decision" for record in ledger.records
    )
    ledger.verify()


def test_provider_finalization_failure_prevents_run_completion(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)
    sessions["flight"].fail_finalize = True

    async def scenario() -> None:
        await coordinator.prepare()
        completion = _completion(
            coordinator,
            run.run_id,
            "reference.agent",
            "completion.one",
            disposition="finished",
        )
        with pytest.raises(HarnessRuntimeError, match="Provider finalize"):
            await coordinator.submit_agent_turn(completion)
        assert coordinator.failed
        assert not coordinator.shutdown_complete
        assert coordinator.process_streams_closed
        assert all(session.shutdown_calls == 0 for session in sessions.values())

    asyncio.run(scenario())
    assert any(
        record.event.event_type == "agent.turn-decision" for record in ledger.records
    )
    assert not any(
        record.event.event_type == "run.completed" for record in ledger.records
    )
    ledger.verify()


def test_provider_shutdown_failure_is_nonfatal_after_terminal_commit(
    tmp_path: Path,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        for session in sessions.values():
            session.fail_shutdown = True
        completion = _completion(
            coordinator,
            run.run_id,
            "reference.agent",
            "completion.one",
            disposition="finished",
        )
        decision = await coordinator.submit_agent_turn(completion)
        assert decision.status == "terminated"
        assert not coordinator.failed
        assert coordinator.shutdown_complete
        assert coordinator.provider_shutdown_failures == tuple(
            sorted(sessions)
        )
        assert await coordinator.submit_agent_turn(completion) == decision
        with pytest.raises(HarnessRuntimeError, match="already shut down"):
            await coordinator.shutdown()

    asyncio.run(scenario())
    assert ledger.records[-1].event.event_type == "run.completed"
    ledger.verify()


def test_cancelled_provider_shutdown_is_nonfatal_after_terminal_event(
    tmp_path: Path,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)
    shutdown_started = asyncio.Event()
    shutdown_release = asyncio.Event()
    for session in sessions.values():
        session.shutdown_started = shutdown_started
        session.shutdown_release = shutdown_release

    async def scenario() -> None:
        await coordinator.prepare()
        completion = _completion(
            coordinator,
            run.run_id,
            "reference.agent",
            "completion.one",
            disposition="finished",
        )
        submission = asyncio.create_task(coordinator.submit_agent_turn(completion))
        await shutdown_started.wait()
        assert ledger.records[-1].event.event_type == "run.completed"

        submission.cancel()
        decision = await submission

        assert decision.status == "terminated"
        assert not coordinator.failed
        assert coordinator.shutdown_complete
        assert coordinator.provider_shutdown_failures == tuple(
            sorted(sessions)
        )
        assert coordinator.active_agent_ids == ()
        assert coordinator.missing_agent_ids == ()
        assert await coordinator.submit_agent_turn(completion) == decision

    asyncio.run(scenario())
    assert ledger.records[-1].event.event_type == "run.completed"
    ledger.verify()


def test_abort_is_idempotent_and_never_records_normal_completion(
    tmp_path: Path,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        sessions["flight"].fail_shutdown = True
        report = await coordinator.abort(failure_class="runtime_failed")
        retry = await coordinator.abort(failure_class="runtime_failed")
        assert retry == report
        assert report.failure_class == "runtime_failed"
        assert report.provider_failures == ("flight",)
        assert coordinator.failed
        assert coordinator.shutdown_complete

    asyncio.run(scenario())
    assert ledger.records[-1].event.event_type == "run.aborted"
    assert not any(
        record.event.event_type == "run.completed" for record in ledger.records
    )
    ledger.verify()


def test_abort_rejects_a_run_that_already_completed(tmp_path: Path) -> None:
    coordinator, ledger, run, _ = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        await coordinator.submit_agent_turn(
            _completion(
                coordinator,
                run.run_id,
                "reference.agent",
                "completion.one",
                disposition="finished",
            )
        )
        with pytest.raises(HarnessRuntimeError, match="already completed"):
            await coordinator.abort(failure_class="runtime_failed")

    asyncio.run(scenario())
    assert ledger.records[-1].event.event_type == "run.completed"


def test_abort_closes_every_provider_after_partial_prepare_failure(
    tmp_path: Path,
) -> None:
    coordinator, ledger, _, sessions = _coordinator(tmp_path)
    sessions["flight"].fail_prepare = True

    async def scenario() -> None:
        with pytest.raises(HarnessRuntimeError, match="Provider prepare"):
            await coordinator.prepare()
        report = await coordinator.abort(failure_class="prepare_failed")
        assert report.provider_failures == ()
        assert all(session.shutdown_calls == 1 for session in sessions.values())

    asyncio.run(scenario())
    assert ledger.records[-1].event.event_type == "run.aborted"
    assert not any(
        record.event.event_type == "run.completed" for record in ledger.records
    )
    ledger.verify()


def _startup_call_coordinator() -> HarnessCoordinator:
    coordinator = object.__new__(HarnessCoordinator)
    coordinator._provider_order = ("flight", "network", "traffic")
    coordinator._run = SimpleNamespace(
        environment=SimpleNamespace(
            clock=SimpleNamespace(provider_timeout_ms=1_000)
        )
    )
    return coordinator


def test_startup_provider_progress_names_each_ordered_prepare_and_reset(
    capsys: pytest.CaptureFixture[str],
) -> None:
    coordinator = _startup_call_coordinator()

    async def complete(provider_id: str) -> str:
        await asyncio.sleep(0)
        return provider_id

    async def scenario() -> None:
        for operation in ("prepare", "reset"):
            results = await coordinator._await_provider_calls(
                tuple(complete(provider_id) for provider_id in coordinator._provider_order),
                operation=operation,
            )
            assert results == coordinator._provider_order

    asyncio.run(scenario())

    records = [
        json.loads(line)
        for line in capsys.readouterr().err.splitlines()
        if line.strip()
    ]
    assert {record["operation"] for record in records} == {"prepare", "reset"}
    for operation in ("prepare", "reset"):
        operation_records = [
            record for record in records if record["operation"] == operation
        ]
        assert [
            record["provider_id"]
            for record in operation_records
            if record["state"] == "started"
        ] == list(coordinator._provider_order)
        assert {
            record["provider_id"]
            for record in operation_records
            if record["state"] == "complete"
        } == set(coordinator._provider_order)
        assert not any(record["state"] == "failed" for record in operation_records)
    assert all(
        set(record)
        == {"elapsed_ms", "kind", "operation", "provider_id", "state"}
        for record in records
    )


def test_startup_provider_progress_reports_failure_type_without_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    coordinator = _startup_call_coordinator()
    failed_provider = coordinator._provider_order[0]

    async def fail_selected(provider_id: str) -> str:
        await asyncio.sleep(0)
        if provider_id == failed_provider:
            raise RuntimeError("private backend detail")
        return provider_id

    with pytest.raises(HarnessRuntimeError, match="Provider prepare"):
        asyncio.run(
            coordinator._await_provider_calls(
                tuple(
                    fail_selected(provider_id)
                    for provider_id in coordinator._provider_order
                ),
                operation="prepare",
            )
        )

    stderr = capsys.readouterr().err
    records = [
        json.loads(line)
        for line in stderr.splitlines()
        if line.strip()
    ]
    failures = [record for record in records if record["state"] == "failed"]
    assert len(failures) == 1
    assert failures[0] == {
        "elapsed_ms": failures[0]["elapsed_ms"],
        "error_type": "RuntimeError",
        "kind": "harness-provider-startup-progress",
        "operation": "prepare",
        "provider_id": failed_provider,
        "state": "failed",
    }
    assert "private backend detail" not in stderr


def test_provider_call_cancellation_retrieves_all_gathered_tasks(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _ = _coordinator(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()

    async def hanging_call() -> None:
        started.set()
        await release.wait()

    async def scenario() -> None:
        task = asyncio.create_task(
            coordinator._await_provider_calls(
                (hanging_call(),),
                operation="test",
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()

    asyncio.run(scenario())


def test_formal_benchmark_cannot_terminate_at_tick_zero(tmp_path: Path) -> None:
    coordinator, ledger, run, _ = _coordinator(tmp_path, formal=True)

    async def scenario() -> None:
        await coordinator.prepare()
        with pytest.raises(HarnessRuntimeError, match="one Provider barrier"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.one",
                    disposition="finished",
                )
            )

    asyncio.run(scenario())
    assert not any(
        record.event.event_type == "agent.turn-decision" for record in ledger.records
    )
    assert coordinator.active_agent_ids == ("reference.agent",)
    ledger.verify()


def test_wrong_run_time_and_agent_fail_closed(tmp_path: Path) -> None:
    coordinator, _, run, _ = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        with pytest.raises(HarnessRuntimeError, match="another run"):
            await coordinator.submit_agent_turn(
                _completion(coordinator, "b" * 64, "reference.agent", "wrong.run")
            )
        with pytest.raises(HarnessRuntimeError, match="authoritative Harness time"):
            await coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "wrong.time",
                    at=SimulationTime(tick=1, sim_time_ns=1),
                )
            )
        with pytest.raises(HarnessRuntimeError, match="not declared"):
            await coordinator.submit_agent_turn(
                _completion(coordinator, run.run_id, "unknown.agent", "wrong.agent")
            )

    asyncio.run(scenario())


def test_duplicate_exact_retry_is_idempotent_but_conflict_fails(tmp_path: Path) -> None:
    coordinator, ledger, run, sessions = _coordinator(tmp_path)

    async def scenario() -> None:
        await coordinator.prepare()
        ledger.append_event(
            source="gateway",
            event_type="command.validated",
            time=coordinator.current,
            interaction_type="agent.tool_result.v1",
            agent_id="reference.agent",
            command_id="command.one",
        )
        ledger.append_event(
            source="gateway",
            event_type="observation.validated",
            time=coordinator.current,
            interaction_type="agent.observation.v1",
            agent_id="reference.agent",
            observation_id="observation.one",
        )
        completion = _completion(
            coordinator,
            run.run_id,
            "reference.agent",
            "completion.one",
            command_ids=("command.one",),
            observation_ids=("observation.one",),
        )
        first = await coordinator.submit_agent_turn(completion)
        retry = await coordinator.submit_agent_turn(completion)
        assert retry == first
        assert sum(session.step_calls for session in sessions.values()) == len(sessions)
        with pytest.raises(HarnessRuntimeError, match="different canonical content"):
            await coordinator.submit_agent_turn(
                completion.model_copy(update={"disposition": "finished"})
            )

    asyncio.run(scenario())
    completion_events = tuple(
        record
        for record in ledger.records
        if record.event.event_type == "agent.turn-completion"
    )
    assert len(completion_events) == 1
    ledger.verify()


def test_concurrent_submissions_have_one_barrier_call_and_valid_chain(
    tmp_path: Path,
) -> None:
    coordinator, ledger, run, sessions = _coordinator(
        tmp_path, agent_ids=("reference.agent", "agent.two")
    )

    async def scenario() -> tuple[AgentTurnDecision, AgentTurnDecision]:
        await coordinator.prepare()
        first, second = await asyncio.gather(
            coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "reference.agent",
                    "completion.one",
                )
            ),
            coordinator.submit_agent_turn(
                _completion(
                    coordinator,
                    run.run_id,
                    "agent.two",
                    "completion.two",
                )
            ),
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert {first.status, second.status} == {"waiting", "advanced"}
    assert all(session.step_calls == 1 for session in sessions.values())
    ledger.verify()
