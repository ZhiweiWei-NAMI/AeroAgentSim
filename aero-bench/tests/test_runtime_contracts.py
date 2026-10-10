from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from aero_bench.artifacts import ArtifactRecord, seal_manifest
from aero_bench.config.models import NamedValue
from aero_bench.providers import ProviderManifest
from aero_bench.runtime import (
    AgentTurnCompletion,
    BusinessEnvironmentStageResult,
    EnuLinearVelocity,
    EventLedger,
    LedgerRecord,
    MotionStageResult,
    NedLinearVelocity,
    NetworkStageResult,
    ProviderBarrier,
    RunEvent,
    RunLifecycle,
    RunPhase,
    SceneContribution,
    SimulationTime,
    StateSample,
    StepReceipt,
    run_event_digest_value,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.runtime.harness import HarnessCoordinator
from aero_bench.runtime.ledger import ledger_jsonl_bytes
from aero_bench.runtime.evidence import (
    load_sealed_event_ledger,
    validate_authoritative_runtime_ledger,
)
from aero_bench.serialization import canonical_json_bytes
from tests.support import (
    as_formal_run,
    build_bundle,
    fake_finalization_receipt,
    resolve_bundle,
    with_fixture_provider_stages,
)


RUN_ID = "a" * 64
SCENARIO_DIGEST = "d" * 64
SCENE_STATE_DIGEST = "e" * 64


def _stage_result(
    *,
    run_id: str,
    scenario_digest: str,
    provider_id: str,
    stage: str,
    target: SimulationTime,
    predecessor_barriers=(),
    scene_state_digest: str | None = None,
    samples: tuple[StateSample, ...] = (),
):
    contribution_fields = {
        "schema_version": "aero-bench.scene-contribution/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "at": target,
        "stage": stage,
        "provider_id": provider_id,
        "samples": samples,
        "attribute_updates": (),
    }
    unsigned = SceneContribution.model_construct(
        **contribution_fields,
        payload_digest="0" * 64,
        contribution_digest="0" * 64,
    )
    payload_digest = scene_contribution_payload_digest_value(unsigned)
    contribution = SceneContribution(
        **contribution_fields,
        payload_digest=payload_digest,
        contribution_digest=scene_contribution_digest_value(
            unsigned.model_copy(update={"payload_digest": payload_digest})
        ),
    )
    receipt = StepReceipt(
        run_id=run_id,
        provider_id=provider_id,
        reached=target,
        state_digest=hashlib.sha256(
            f"{provider_id}:{stage}:{target.tick}".encode()
        ).hexdigest(),
    )
    fields = {
        "schema_version": "aero-bench.provider-stage-result/v1",
        "run_id": run_id,
        "scenario_digest": scenario_digest,
        "provider_id": provider_id,
        "target": target,
        "stage": stage,
        "step_receipt": receipt,
        "step_receipt_digest": step_receipt_digest_value(receipt),
        "contribution": contribution,
        "predecessor_barriers": predecessor_barriers,
    }
    if stage == "motion":
        return MotionStageResult(**fields)
    fields["input_scene_state_digest"] = scene_state_digest
    if stage == "network":
        return NetworkStageResult(**fields)
    return BusinessEnvironmentStageResult(**fields)


def test_provider_barrier_cannot_commit_before_every_receipt() -> None:
    barrier = ProviderBarrier(
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        step_ns=100,
        enabled_stages=("motion", "business_environment"),
        provider_ids_by_stage={
            "motion": (),
            "business_environment": ("flight", "network"),
        },
    )
    target = SimulationTime(tick=1, sim_time_ns=100)
    barrier.begin_tick(target)
    assert barrier.begin_stage("motion") == ()
    barrier.close_stage()
    requests = barrier.begin_stage(
        "business_environment",
        input_scene_state_digest=SCENE_STATE_DIGEST,
    )

    assert len(requests) == 2
    barrier.submit(
        _stage_result(
            run_id=RUN_ID,
            scenario_digest=SCENARIO_DIGEST,
            provider_id="flight",
            stage="business_environment",
            target=target,
            predecessor_barriers=barrier.active_predecessor_barriers,
            scene_state_digest=SCENE_STATE_DIGEST,
        )
    )
    with pytest.raises(RuntimeError, match="missing"):
        barrier.close_stage()
    barrier.submit(
        _stage_result(
            run_id=RUN_ID,
            scenario_digest=SCENARIO_DIGEST,
            provider_id="network",
            stage="business_environment",
            target=target,
            predecessor_barriers=barrier.active_predecessor_barriers,
            scene_state_digest=SCENE_STATE_DIGEST,
        )
    )

    barrier.close_stage()
    commit = barrier.commit_tick()
    assert commit.time == target
    assert barrier.current == target


def test_provider_barrier_rejects_time_mismatch() -> None:
    barrier = ProviderBarrier(
        run_id=RUN_ID,
        scenario_digest=SCENARIO_DIGEST,
        step_ns=10,
        enabled_stages=("motion", "business_environment"),
        provider_ids_by_stage={
            "motion": (),
            "business_environment": ("flight",),
        },
    )
    target = SimulationTime(tick=1, sim_time_ns=10)
    barrier.begin_tick(target)
    barrier.begin_stage("motion")
    barrier.close_stage()
    barrier.begin_stage(
        "business_environment",
        input_scene_state_digest=SCENE_STATE_DIGEST,
    )

    with pytest.raises(ValueError, match="binding is inconsistent"):
        barrier.submit(
            _stage_result(
                run_id=RUN_ID,
                scenario_digest=SCENARIO_DIGEST,
                provider_id="flight",
                stage="business_environment",
                target=SimulationTime(tick=2, sim_time_ns=20),
                predecessor_barriers=barrier.active_predecessor_barriers,
                scene_state_digest=SCENE_STATE_DIGEST,
            )
        )


def test_event_ledger_is_deterministic_and_tamper_evident(tmp_path) -> None:
    ledger = EventLedger(run_id=RUN_ID)
    first = ledger.append_event(
        source="harness",
        event_type="run.started",
        time=SimulationTime(tick=0, sim_time_ns=0),
        payload=(NamedValue(name="seed", value=7),),
    )
    second = ledger.append_event(
        source="flight",
        event_type="flight.completed",
        time=SimulationTime(tick=1, sim_time_ns=100),
    )

    ledger.verify()
    assert first.event_hash != second.event_hash
    assert second.previous_hash == first.event_hash
    destination = tmp_path / "ledger" / "events.jsonl"
    ledger.write_jsonl(destination)
    assert destination.read_text(encoding="utf-8").count("\n") == 2
    assert ledger.chain_root == second.event_hash
    replayed = EventLedger.read_jsonl(destination)
    assert replayed.records == ledger.records
    assert replayed.chain_root == ledger.chain_root
    assert ledger_jsonl_bytes(ledger.records) == b"".join(
        canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
        for record in ledger.records
    ) == destination.read_bytes()
    changed = second.event.model_copy(update={"source": "other-provider"})
    with pytest.raises(ValueError, match="content hash"):
        ledger_jsonl_bytes((first, second.model_copy(update={"event": changed})))
    with pytest.raises(FileExistsError):
        ledger.write_jsonl(destination)


def test_event_ledger_prospective_terminal_root_is_exact_and_non_mutating() -> None:
    ledger = EventLedger(run_id=RUN_ID)
    ledger.append_event(
        source="harness",
        event_type="run.started",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )
    terminal = ledger.new_event(
        source="harness",
        event_type="run.completed",
        time=SimulationTime(tick=1, sim_time_ns=100),
    )
    records_before = ledger.records
    root_before = ledger.chain_root

    prospective = ledger.prospective_chain_root(terminal)

    assert ledger.records == records_before
    assert ledger.chain_root == root_before
    assert ledger.append(terminal).event_hash == prospective
    assert ledger.chain_root == prospective
    with pytest.raises(RuntimeError, match="already terminal"):
        ledger.append_event(
            source="harness",
            event_type="event.after-terminal",
            time=terminal.time,
        )
    with pytest.raises(RuntimeError, match="already terminal"):
        ledger.prospective_chain_root(terminal)


def test_event_ledger_rejects_provider_terminal_authority(tmp_path) -> None:
    ledger = EventLedger(run_id=RUN_ID)
    event = ledger.new_event(
        source="provider.one",
        event_type="run.completed",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )
    with pytest.raises(ValueError, match="must originate from the Harness"):
        ledger.prospective_chain_root(event)
    with pytest.raises(ValueError, match="must originate from the Harness"):
        ledger.append(event)

    record = LedgerRecord(
        sequence=event.sequence,
        event=event,
        previous_hash=event.previous_event_digest,
        event_hash=event.event_digest,
    )
    source = tmp_path / "provider-terminal.jsonl"
    source.write_bytes(canonical_json_bytes(record.model_dump(mode="json")) + b"\n")
    with pytest.raises(ValueError, match="must originate from the Harness"):
        EventLedger.read_jsonl(source)


def test_event_ledger_rejects_validly_hashed_post_terminal_jsonl(tmp_path) -> None:
    ledger = EventLedger(run_id=RUN_ID)
    terminal = ledger.append_event(
        source="harness",
        event_type="run.aborted",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )
    event_fields = terminal.event.model_dump(mode="python", exclude={"event_digest"})
    event_fields.update(
        {
            "sequence": 1,
            "event_id": "event.0000000000000001",
            "source_kind": "provider",
            "source": "provider.one",
            "workload_id": "provider.one",
            "event_type": "provider.shutdown",
            "correlation_id": "event.0000000000000001",
            "payload_schema_id": "provider.shutdown.v1",
            "previous_event_digest": terminal.event_hash,
        }
    )
    candidate = RunEvent.model_construct(
        **event_fields,
        event_digest="0" * 64,
    )
    event = RunEvent(
        **event_fields,
        event_digest=run_event_digest_value(candidate),
    )
    forged = LedgerRecord(
        sequence=1,
        event=event,
        previous_hash=terminal.event_hash,
        event_hash=event.event_digest,
    )
    source = tmp_path / "post-terminal.jsonl"
    source.write_bytes(
        canonical_json_bytes(terminal.model_dump(mode="json"))
        + b"\n"
        + canonical_json_bytes(forged.model_dump(mode="json"))
        + b"\n"
    )

    with pytest.raises(ValueError, match="post-terminal"):
        EventLedger.read_jsonl(source)


def test_event_ledger_rejects_noncanonical_and_tampered_jsonl(tmp_path) -> None:
    ledger = EventLedger(run_id=RUN_ID)
    ledger.append_event(
        source="harness",
        event_type="run.started",
        time=SimulationTime(tick=0, sim_time_ns=0),
    )
    ledger.append_event(
        source="business",
        event_type="business.transition",
        time=SimulationTime(tick=1, sim_time_ns=100),
    )

    noncanonical = tmp_path / "noncanonical.jsonl"
    noncanonical.write_text(
        "\n".join(
            json.dumps(record.model_dump(mode="json")) for record in ledger.records
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not canonical JSON"):
        EventLedger.read_jsonl(noncanonical)

    tampered = tmp_path / "tampered.jsonl"
    raw_records = [record.model_dump(mode="json") for record in ledger.records]
    event_fields = ledger.records[1].event.model_dump(
        mode="python", exclude={"event_digest"}
    )
    event_fields["previous_event_digest"] = "d" * 64
    candidate = RunEvent.model_construct(
        **event_fields,
        event_digest="0" * 64,
    )
    event = RunEvent(
        **event_fields,
        event_digest=run_event_digest_value(candidate),
    )
    raw_records[1] = LedgerRecord(
        sequence=1,
        event=event,
        previous_hash="d" * 64,
        event_hash=event.event_digest,
    ).model_dump(mode="json")
    tampered.write_bytes(
        b"".join(canonical_json_bytes(record) + b"\n" for record in raw_records)
    )
    with pytest.raises(ValueError, match="hash chain is broken"):
        EventLedger.read_jsonl(tampered)

    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="non-empty canonical JSONL"):
        EventLedger.read_jsonl(empty)


def test_lifecycle_rejects_skipped_phase() -> None:
    lifecycle = RunLifecycle()

    with pytest.raises(RuntimeError, match="invalid lifecycle"):
        lifecycle.transition(RunPhase.RUNNING)
    assert lifecycle.transition(RunPhase.VALIDATED) is RunPhase.VALIDATED


class DeterministicProviderSession:
    def __init__(self, *, run, manifest: ProviderManifest):
        self.run = run
        run_id = run.run_id
        self.run_id = run_id
        self.manifest = manifest
        self.shutdown_called = False

    async def prepare(self) -> None:
        return None

    async def reset(self, *, seed: int) -> StepReceipt:
        digest = hashlib.sha256(f"reset:{seed}".encode()).hexdigest()
        return StepReceipt(
            run_id=self.run_id,
            provider_id=self.manifest.provider_id,
            reached=SimulationTime(tick=0, sim_time_ns=0),
            state_digest=digest,
        )

    async def step_to(self, request) -> StepReceipt:
        digest = hashlib.sha256(str(request.target.model_dump()).encode()).hexdigest()
        return StepReceipt(
            run_id=self.run_id,
            provider_id=self.manifest.provider_id,
            reached=request.target,
            state_digest=digest,
        )

    async def step_stage(self, request):
        samples = tuple(
            self._dynamic_sample(entity, request.target)
            for entity in self.run.scenario.entities
            if request.stage == "motion"
            and entity.state == "dynamic"
            and entity.owner_id == self.manifest.provider_id
        )
        return _stage_result(
            run_id=self.run_id,
            scenario_digest=request.scenario_digest,
            provider_id=self.manifest.provider_id,
            stage=request.stage,
            target=request.target,
            predecessor_barriers=getattr(request, "predecessor_barriers", ()),
            scene_state_digest=getattr(request, "scene_state_digest", None),
            samples=samples,
        )

    def _dynamic_sample(self, entity, at: SimulationTime) -> StateSample:
        fields = {
            "schema_version": "aero-bench.state-sample/v1",
            "run_id": self.run_id,
            "scenario_digest": self.run.scenario.scenario_digest,
            "at": at,
            "stage": "motion",
            "entity_id": entity.entity_id,
            "provider_id": self.manifest.provider_id,
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
            "angular_velocity_body": None,
            "mode": None,
            "armed": None,
            "battery": None,
            "health": None,
            "contacts": (),
            "attributes": (),
        }
        unsigned = StateSample.model_construct(
            **fields,
            sample_digest="0" * 64,
        )
        return StateSample(
            **fields,
            sample_digest=state_sample_digest_value(unsigned),
        )

    async def handle_command(self, request):
        raise AssertionError("the coordinator does not route tool commands")

    async def finalize(self, request):
        return fake_finalization_receipt(self.manifest, request)

    async def snapshot_digest(self) -> str:
        return hashlib.sha256(b"snapshot").hexdigest()

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _provider_sessions(run) -> dict[str, DeterministicProviderSession]:
    return {
        provider.provider_id: DeterministicProviderSession(
            run=run,
            manifest=ProviderManifest(
                provider_id=provider.provider_id,
                adapter=provider.adapter,
                implementation=provider.workload.implementation,
                runtime_image=provider.workload.runtime.image,
                config_digest=provider.config.file.sha256,
                capabilities=provider.capabilities,
                protocol_schema=provider.protocol_schema,
                artifact_requirements=provider.artifact_requirements,
            ),
        )
        for provider in run.environment.providers
    }


def test_harness_coordinator_commits_only_real_provider_receipts(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = with_fixture_provider_stages(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    sessions = _provider_sessions(run)
    ledger = EventLedger(run_id=run.run_id)
    coordinator = HarnessCoordinator(
        run=run,
        providers=sessions,
        ledger=ledger,
    )

    asyncio.run(coordinator.prepare())
    decision = asyncio.run(
        coordinator.submit_agent_turn(
            AgentTurnCompletion(
                schema_version="aero-bench.agent-turn-completion/v1",
                run_id=run.run_id,
                agent_id=run.agents[0].agent_id,
                completion_id="turn-1",
                at=coordinator.current,
                disposition="advance",
                command_ids=(),
                observation_ids=(),
            )
        )
    )
    asyncio.run(coordinator.shutdown())

    assert decision.status == "advanced"
    assert decision.at == SimulationTime(
        tick=1,
        sim_time_ns=run.environment.clock.step_ns,
    )
    assert coordinator.current == decision.at
    assert all(session.shutdown_called for session in sessions.values())
    ledger.verify()
    validate_authoritative_runtime_ledger(run, ledger)


def test_sealed_event_ledger_loader_binds_requirement_bytes_and_paths(tmp_path) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    run = with_fixture_provider_stages(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    ledger = EventLedger(run_id=run.run_id)
    coordinator = HarnessCoordinator(
        run=run,
        providers=_provider_sessions(run),
        ledger=ledger,
    )
    asyncio.run(coordinator.prepare())
    asyncio.run(
        coordinator.submit_agent_turn(
            AgentTurnCompletion(
                schema_version="aero-bench.agent-turn-completion/v1",
                run_id=run.run_id,
                agent_id=run.agents[0].agent_id,
                completion_id="turn-1",
                at=coordinator.current,
                disposition="advance",
                command_ids=(),
                observation_ids=(),
            )
        )
    )
    asyncio.run(coordinator.shutdown())

    requirement = next(
        requirement
        for requirement in run.artifact_requirements
        if requirement.artifact_type == "event.log"
    )
    seal_root = tmp_path / "seal"
    seal_root.mkdir()
    event_path = seal_root / requirement.relative_path
    ledger.write_jsonl(event_path)
    payload = event_path.read_bytes()
    artifact = ArtifactRecord(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        relative_path=requirement.relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    seal = seal_manifest(
        root=seal_root,
        run_id=run.run_id,
        attempt_id="attempt.test",
        execution_scope=run.execution_scope,
        event_chain_root=ledger.chain_root,
        artifacts=(artifact,),
    )

    replayed = load_sealed_event_ledger(run=run, seal=seal, seal_root=seal_root)
    assert replayed.records == ledger.records

    outside = tmp_path / "outside-event.log"
    outside.write_bytes(payload)
    event_path.unlink()
    event_path.symlink_to(outside)
    with pytest.raises(ValueError, match="symbolic link"):
        load_sealed_event_ledger(run=run, seal=seal, seal_root=seal_root)


def test_formal_runtime_ledger_requires_every_provider_receipt(tmp_path) -> None:
    bundle = build_bundle(tmp_path)
    run = as_formal_run(
        with_fixture_provider_stages(
            resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
        )
    )
    ledger = EventLedger(run_id=run.run_id)
    coordinator = HarnessCoordinator(
        run=run,
        providers=_provider_sessions(run),
        ledger=ledger,
    )
    asyncio.run(coordinator.prepare())
    asyncio.run(
        coordinator.submit_agent_turn(
            AgentTurnCompletion(
                schema_version="aero-bench.agent-turn-completion/v1",
                run_id=run.run_id,
                agent_id=run.agents[0].agent_id,
                completion_id="turn-1",
                at=coordinator.current,
                disposition="advance",
                command_ids=(),
                observation_ids=(),
            )
        )
    )
    asyncio.run(coordinator.shutdown())
    validate_authoritative_runtime_ledger(run, ledger)

    incomplete = EventLedger(run_id=run.run_id)
    event_id_map: dict[str, str] = {}
    for record in ledger.records:
        event = record.event
        if event.event_type == "provider.step-receipt" and event.source == "network":
            continue
        parent_event_id = (
            event_id_map.get(event.parent_event_id)
            if event.parent_event_id is not None
            else None
        )
        replayed = incomplete.append_event(
            source=event.source,
            source_kind=event.source_kind,
            workload_id=event.workload_id,
            event_type=event.event_type,
            time=event.time,
            payload=event.payload,
            payload_schema_id=event.payload_schema_id,
            interaction_type=(
                event.interaction.interaction_type
                if event.interaction is not None
                else None
            ),
            correlation_id=event.correlation_id,
            parent_event_id=parent_event_id,
            causal_event_ids=tuple(
                event_id_map[event_id]
                for event_id in event.causal_event_ids
                if event_id in event_id_map
            ),
            visibility=event.visibility,
            frame_id=event.frame_id,
            agent_id=event.agent_id,
            provider_id=event.provider_id,
            vehicle_id=event.vehicle_id,
            entity_id=event.entity_id,
            command_id=event.command_id,
            observation_id=event.observation_id,
        )
        event_id_map[event.event_id] = replayed.event.event_id
    with pytest.raises(ValueError, match="one receipt per Provider"):
        validate_authoritative_runtime_ledger(run, incomplete)
