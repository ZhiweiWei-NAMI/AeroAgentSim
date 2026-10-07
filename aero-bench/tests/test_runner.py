from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from tests.support import fixture_provider_registry
from aero_bench.providers.stages import PROVIDER_STAGES

from aero_bench.artifacts import ArtifactRecord, EvidenceReference, seal_manifest
from aero_bench.config.loader import sha256_file
from aero_bench.config.models import NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.executor.contracts import (
    ExecutionPlan,
    InlineInputPlan,
    PreflightReport,
    WorkloadPlan,
)
from aero_bench.runner import RunnerError, RunnerConfig, load_runner_config, run_suite
from aero_bench.runner import execution as runner_execution
from aero_bench.runner.cli import main as runner_main
from aero_bench.runtime import (
    EnuLinearVelocity,
    EventLedger,
    NedLinearVelocity,
    SceneContribution,
    SimulationTime,
    StageBarrier,
    StageBarrierDigest,
    StageReceipt,
    StateSample,
    StepReceipt,
    scene_contribution_digest_value,
    scene_contribution_payload_digest_value,
    scene_state_jsonl_bytes,
    stage_barrier_digest_value,
    stage_receipt_digest_value,
    state_sample_digest_value,
    step_receipt_digest_value,
)
from aero_bench.runtime.evidence import (
    harness_identity_payload,
    provider_identity_payload,
)
from aero_bench.runtime.events import RunEventAudience
from aero_bench.runtime.scene_state import SceneStateAssembler
from aero_bench.serialization import canonical_json_bytes
from aero_bench.verifier.contracts import (
    GoalResult,
    MetricResult,
    VerificationReport,
)
from aero_bench.verifier.output import ValidatedVerificationOutput
from tests.support import as_formal_run, build_bundle, resolve_bundle


KEEPER_IMAGE = "registry.invalid/keeper@sha256:" + "6" * 64


def _runner_config(output_root: Path) -> RunnerConfig:
    return RunnerConfig(
        schema_version="aero-bench.runner-config/v1",
        executor_kind="docker_reference",
        output_root=str(output_root),
        runtime_timeout_seconds=30,
        verifier_timeout_seconds=20,
        readiness_timeout_seconds=30,
        docker_binary="docker",
        input_mount_path="/run/aero-input",
        artifact_mount_path="/run/aero-artifacts",
        seal_mount_path="/run/aero-seal",
        volume_keeper_mount_path="/run/aero-held",
        input_volume_size_bytes=1_048_576,
        artifact_volume_size_bytes=4_194_304,
        scratch_size_bytes=1_048_576,
        pids_limit=64,
        workload_uid=65532,
        workload_gid=65532,
        provider_bind_host="0.0.0.0",
        gateway_bind_host="0.0.0.0",
        volume_keeper_image=KEEPER_IMAGE,
        volume_keeper_command=("/bin/sleep", "infinity"),
        volume_keeper_cpu_millicores=50,
        volume_keeper_memory_mib=64,
    )


def _write_runner_config(path: Path, config: RunnerConfig, **extra: object) -> None:
    payload = config.model_dump(mode="json")
    payload.update(extra)
    path.write_bytes(canonical_json_bytes(payload) + b"\n")


class _FakeHandle:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.attempt_id = f"attempt.{run_id[:16]}"


class _FakeExecutor:
    def __init__(
        self,
        *,
        blocked: bool = False,
        startup_failure: bool = False,
        runtime_failure: bool = False,
        verifier_failure: bool = False,
        cleanup_failure: bool = False,
        cleanup_run_failure: bool = False,
        verification_status: str | None = None,
        metric_value: object | None = None,
        tamper_runtime_seal: bool = False,
        wrong_report_goal: bool = False,
    ) -> None:
        self.blocked = blocked
        self.startup_failure = startup_failure
        self.runtime_failure = runtime_failure
        self.verifier_failure = verifier_failure
        self.cleanup_failure = cleanup_failure
        self.cleanup_run_failure = cleanup_run_failure
        self.verification_status = verification_status
        self.metric_value = metric_value
        self.tamper_runtime_seal = tamper_runtime_seal
        self.wrong_report_goal = wrong_report_goal
        self.calls: list[tuple[str, str]] = []

    def materialize(
        self, run: ResolvedRunSpec, *, bundle_root: str | Path
    ) -> ExecutionPlan:
        self.calls.append(("materialize", run.run_id))
        contract_bytes = b"{}\n"
        contract = InlineInputPlan(
            destination="contract.json",
            content_utf8=contract_bytes.decode("utf-8"),
            sha256=hashlib.sha256(contract_bytes).hexdigest(),
        )
        return ExecutionPlan(
            run=run,
            executor_kind="docker_reference",
            bundle_root=str(Path(bundle_root).resolve()),
            runtime_workloads=(
                WorkloadPlan(
                    workload_id="harness",
                    role="harness",
                    phase="runtime",
                    workload=run.environment.harness,
                    contract=contract,
                    bundle_inputs=(),
                ),
            ),
            verifier_workload=WorkloadPlan(
                workload_id=run.task.verifier.verifier_id,
                role="verifier",
                phase="verification",
                workload=run.task.verifier.workload,
                contract=contract,
                bundle_inputs=(),
            ),
        )

    def preflight(self, plan: ExecutionPlan) -> PreflightReport:
        self.calls.append(("preflight", plan.run.run_id))
        return PreflightReport(
            run_id=plan.run.run_id,
            executor_kind="docker_reference",
            execution_scope=plan.run.execution_scope,
            ready=not self.blocked,
            blockers=({"code": "fake.blocked", "detail": "blocked by fake"},)
            if self.blocked
            else (),
        )

    def start_runtime(self, plan: ExecutionPlan) -> _FakeHandle:
        self.calls.append(("start_runtime", plan.run.run_id))
        if self.startup_failure:
            raise RuntimeError("fake startup failure")
        return _FakeHandle(plan.run.run_id)

    def wait_runtime(self, handle: _FakeHandle, *, timeout_seconds: int) -> None:
        self.calls.append(("wait_runtime", handle.run_id))
        if self.runtime_failure:
            raise RuntimeError("fake runtime failure")

    def collect_failure_outputs(
        self,
        plan: ExecutionPlan,
        handle: _FakeHandle,
        *,
        destination_root: Path,
    ) -> None:
        self.calls.append(("collect_failure_outputs", plan.run.run_id))
        destination_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def collect_and_seal(
        self,
        plan: ExecutionPlan,
        handle: _FakeHandle,
        *,
        destination_root: Path,
    ) -> Any:
        self.calls.append(("collect_and_seal", plan.run.run_id))
        seal = _make_runtime_seal(
            destination_root,
            plan.run,
            attempt_id=handle.attempt_id,
        )
        if self.tamper_runtime_seal:
            requirement = next(
                requirement
                for requirement in plan.run.artifact_requirements
                if requirement.artifact_type == "event.log"
            )
            with (destination_root / requirement.relative_path).open("ab") as stream:
                stream.write(b"{}\n")
        return seal

    def start_verifier(self, plan: ExecutionPlan, seal):
        self.calls.append(("start_verifier", plan.run.run_id))
        return _FakeHandle(plan.run.run_id)

    def wait_verifier(self, handle: _FakeHandle, *, timeout_seconds: int) -> None:
        self.calls.append(("wait_verifier", handle.run_id))
        if self.verifier_failure:
            raise RuntimeError("fake verifier failure")

    def collect_verification_outputs(
        self,
        plan: ExecutionPlan,
        handle: _FakeHandle,
        *,
        destination_root: Path,
    ) -> ValidatedVerificationOutput:
        run = plan.run
        self.calls.append(("collect_verification_outputs", run.run_id))
        status = self.verification_status or (
            "passed" if run.execution_scope == "formal_benchmark" else "invalid"
        )
        goal_results = tuple(
            GoalResult(
                goal_id=("wrong.goal" if self.wrong_report_goal else goal.goal_id),
                passed=status == "passed",
                failure_class=None if status == "passed" else "fake.invalid",
                metrics=(
                    MetricResult(
                        metric_id=goal.metric_id,
                        value=(
                            self.metric_value
                            if self.metric_value is not None
                            else (1.0 if status == "passed" else 0.0)
                        ),
                        unit="ratio",
                        evidence=(
                            EvidenceReference(
                                artifact_id=next(
                                    requirement.artifact_id
                                    for requirement in run.artifact_requirements
                                    if requirement.visibility == "public"
                                ),
                                selector="result",
                            ),
                        ),
                    ),
                ),
            )
            for goal in run.task.goals
        )
        report = VerificationReport(
            schema_version="aero-bench.verification/v1",
            run_id=run.run_id,
            execution_scope=run.execution_scope,
            status=status,
            goals=goal_results,
            coverage_complete=True,
        )
        report_path = destination_root / "verification.json"
        destination_root.mkdir(mode=0o700)
        report_path.write_bytes(canonical_json_bytes(report.model_dump(mode="json")))
        payload = report_path.read_bytes()
        artifact = ArtifactRecord(
            artifact_id="artifact.verification",
            artifact_type="verification.report",
            producer_id=run.task.verifier.verifier_id,
            visibility="public",
            relative_path="verification.json",
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        output_seal = seal_manifest(
            root=destination_root,
            run_id=run.run_id,
            attempt_id=handle.attempt_id,
            execution_scope=run.execution_scope,
            event_chain_root="b" * 64,
            artifacts=(artifact,),
        )
        return ValidatedVerificationOutput(seal=output_seal, report=report)

    def cleanup(self, handle: _FakeHandle) -> None:
        self.calls.append(("cleanup", handle.run_id))
        if self.cleanup_failure:
            raise RuntimeError("fake cleanup failure")

    def cleanup_run(self, run_id: str) -> None:
        self.calls.append(("cleanup_run", run_id))
        if self.cleanup_run_failure:
            raise RuntimeError("fake startup cleanup failure")



@pytest.fixture(autouse=True)
def _runner_fixture_registry(monkeypatch):
    monkeypatch.setattr(runner_execution, "builtin_provider_registry", fixture_provider_registry)


def _run_with_executor(
    monkeypatch: pytest.MonkeyPatch,
    bundle,
    config_path: Path,
    executor: _FakeExecutor,
):
    monkeypatch.setattr(
        runner_execution,
        "build_docker_executor",
        lambda _config, *, provider_registry: executor,
    )
    return run_suite(bundle.suite, config_path)


def _named_values(values: dict[str, object]) -> tuple[NamedValue, ...]:
    return tuple(
        NamedValue(name=name, value=value) for name, value in sorted(values.items())
    )


def _json_value(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


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
        "angular_velocity_body": None,
        "mode": None,
        "armed": None,
        "battery": None,
        "health": None,
        "contacts": (),
        "attributes": (),
    }
    unsigned = StateSample.model_construct(**fields, sample_digest="0" * 64)
    return StateSample(**fields, sample_digest=state_sample_digest_value(unsigned))


def _stage_barrier(
    run: ResolvedRunSpec,
    *,
    at: SimulationTime,
    stage: str,
    provider_ids: tuple[str, ...],
    scene_state_digest: str | None = None,
    predecessor_barriers: tuple[StageBarrierDigest, ...] = (),
) -> tuple[StageBarrier, tuple[SceneContribution, ...]]:
    contributions = []
    receipts = []
    for provider_id in provider_ids:
        samples = tuple(
            _state_sample(run, entity, at)
            for entity in run.scenario.entities
            if stage == "motion"
            and entity.state == "dynamic"
            and entity.owner_id == provider_id
        )
        contribution_fields = {
            "schema_version": "aero-bench.scene-contribution/v1",
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "at": at,
            "stage": stage,
            "provider_id": provider_id,
            "samples": samples,
            "attribute_updates": (),
        }
        unsigned_contribution = SceneContribution.model_construct(
            **contribution_fields,
            payload_digest="0" * 64,
            contribution_digest="0" * 64,
        )
        payload_digest = scene_contribution_payload_digest_value(unsigned_contribution)
        contribution = SceneContribution(
            **contribution_fields,
            payload_digest=payload_digest,
            contribution_digest=scene_contribution_digest_value(
                unsigned_contribution.model_copy(
                    update={"payload_digest": payload_digest}
                )
            ),
        )
        contributions.append(contribution)
        step_receipt = StepReceipt(
            run_id=run.run_id,
            provider_id=provider_id,
            reached=at,
            state_digest=hashlib.sha256(
                f"{provider_id}:{stage}:state".encode()
            ).hexdigest(),
        )
        receipt_fields = {
            "schema_version": "aero-bench.stage-receipt/v1",
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "at": at,
            "stage": stage,
            "provider_id": provider_id,
            "state_digest": step_receipt.state_digest,
            "step_receipt_digest": step_receipt_digest_value(step_receipt),
            "contribution_digest": contribution.contribution_digest,
            "payload_digest": contribution.payload_digest,
            "input_scene_state_digest": scene_state_digest,
            "predecessor_barriers": predecessor_barriers,
        }
        unsigned_receipt = StageReceipt.model_construct(
            **receipt_fields,
            receipt_digest="0" * 64,
        )
        receipts.append(
            StageReceipt(
                **receipt_fields,
                receipt_digest=stage_receipt_digest_value(unsigned_receipt),
            )
        )
    barrier_fields = {
        "schema_version": "aero-bench.stage-barrier/v1",
        "run_id": run.run_id,
        "scenario_digest": run.scenario.scenario_digest,
        "at": at,
        "stage": stage,
        "input_scene_state_digest": scene_state_digest,
        "predecessor_barriers": predecessor_barriers,
        "provider_ids": provider_ids,
        "receipts": tuple(receipts),
        "receipt_digests": tuple(receipt.receipt_digest for receipt in receipts),
    }
    unsigned_barrier = StageBarrier.model_construct(
        **barrier_fields,
        barrier_digest="0" * 64,
    )
    return (
        StageBarrier(
            **barrier_fields,
            barrier_digest=stage_barrier_digest_value(unsigned_barrier),
        ),
        tuple(contributions),
    )


def _runtime_scene(run: ResolvedRunSpec):
    at = SimulationTime(tick=1, sim_time_ns=run.environment.clock.step_ns)
    motion_provider_ids = tuple(
        provider.provider_id for provider in run.scenario.providers
        if provider.runtime_stage == "motion"
    )
    motion_barrier, contributions = _stage_barrier(
        run,
        at=at,
        stage="motion",
        provider_ids=motion_provider_ids,
    )
    scene_state = SceneStateAssembler(run.scenario).assemble(
        run_id=run.run_id,
        at=at,
        barrier=motion_barrier,
        contributions=contributions,
    )
    barriers = [motion_barrier]
    for stage in PROVIDER_STAGES[1:]:
        provider_ids = tuple(provider.provider_id for provider in run.scenario.providers
                             if provider.runtime_stage == stage)
        if not provider_ids:
            continue
        predecessors = tuple(StageBarrierDigest(
            stage=prior.stage, barrier_digest=prior.barrier_digest
        ) for prior in barriers)
        barrier, _ = _stage_barrier(
            run, at=at, stage=stage, provider_ids=provider_ids,
            scene_state_digest=scene_state.scene_state_digest,
            predecessor_barriers=predecessors,
        )
        barriers.append(barrier)
    return scene_state, tuple(barriers)


def _stage_receipt_payload(
    run: ResolvedRunSpec,
    barrier: StageBarrier,
    receipt: StageReceipt,
    scene_state_digest: str,
) -> tuple[NamedValue, ...]:
    return _named_values(
        {
            "barrier_digest": barrier.barrier_digest,
            "contribution_digest": receipt.contribution_digest,
            "contribution_payload_digest": receipt.payload_digest,
            "predecessor_barriers": _json_value(
                [item.model_dump(mode="json") for item in barrier.predecessor_barriers]
            ),
            "provider_id": receipt.provider_id,
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "scene_state_digest": scene_state_digest,
            "stage": barrier.stage,
            "stage_receipt_digest": receipt.receipt_digest,
            "state_digest": receipt.state_digest,
            "step_receipt_digest": receipt.step_receipt_digest,
            "target_sim_time_ns": barrier.at.sim_time_ns,
            "target_tick": barrier.at.tick,
        }
    )


def _stage_barrier_payload(
    run: ResolvedRunSpec,
    barrier: StageBarrier,
    scene_state_digest: str,
) -> tuple[NamedValue, ...]:
    return _named_values(
        {
            "barrier_digest": barrier.barrier_digest,
            "contribution_digests": _json_value(
                [receipt.contribution_digest for receipt in barrier.receipts]
            ),
            "predecessor_barriers": _json_value(
                [item.model_dump(mode="json") for item in barrier.predecessor_barriers]
            ),
            "provider_ids": _json_value(list(barrier.provider_ids)),
            "receipt_digests": _json_value(list(barrier.receipt_digests)),
            "run_id": run.run_id,
            "scenario_digest": run.scenario.scenario_digest,
            "scene_state_digest": scene_state_digest,
            "stage": barrier.stage,
            "step_receipt_digests": _json_value(
                [receipt.step_receipt_digest for receipt in barrier.receipts]
            ),
            "target_sim_time_ns": barrier.at.sim_time_ns,
            "target_tick": barrier.at.tick,
        }
    )


def _append_closed_process_streams(
    ledger: EventLedger,
    run: ResolvedRunSpec,
    at: SimulationTime,
) -> None:
    roles = {
        "harness": "harness",
        **{provider.provider_id: "provider" for provider in run.environment.providers},
        **{agent.agent_id: "agent" for agent in run.agents},
        **{
            agent.driver.driver_id: "agent_driver"
            for agent in run.agents
            if agent.driver is not None
        },
    }
    empty_digest = hashlib.sha256(b"").hexdigest()
    for workload_id, role in sorted(roles.items()):
        for stream in ("stderr", "stdout"):
            ledger.append_event(
                source=workload_id,
                source_kind="executor" if role == "agent_driver" else role,
                workload_id=workload_id,
                event_type=f"process.{stream}",
                time=at,
                wall_time_ns=0,
                payload=_named_values(
                    {
                        "byte_offset": 0,
                        "captured_wall_time_ns": 0,
                        "content_class": (
                            "explicit_output" if role == "agent" else "system_log"
                        ),
                        "final": True,
                        "payload_base64": "",
                        "payload_sha256": empty_digest,
                        "payload_size_bytes": 0,
                        "run_id": run.run_id,
                        "sequence": 0,
                        "stream": stream,
                        "truncated": False,
                        "workload_id": workload_id,
                        "workload_role": role,
                    }
                ),
                payload_schema_id=f"process.{stream}.v1",
                interaction_type=f"process.{stream}.v1",
                correlation_id=f"process.{workload_id}.{stream}",
                visibility=(RunEventAudience(scope="private", audience_id=None),),
                agent_id=workload_id if role == "agent" else None,
                provider_id=workload_id if role == "provider" else None,
            )


def _runtime_ledger(run: ResolvedRunSpec, scene_state, barriers) -> EventLedger:
    ledger = EventLedger(run_id=run.run_id)
    zero = SimulationTime(tick=0, sim_time_ns=0)
    if run.execution_scope == "executor_validation":
        ledger.append_event(
            source="harness",
            event_type="validation.scope",
            time=zero,
            payload=(NamedValue(name="execution_scope", value="executor_validation"),),
        )
    ledger.append_event(
        source="harness",
        event_type="run.started",
        time=zero,
        payload=(NamedValue(name="execution_scope", value=run.execution_scope),),
    )
    ledger.append_event(
        source="harness",
        event_type="runtime.identity",
        time=zero,
        payload=harness_identity_payload(run),
    )
    for provider in sorted(
        run.environment.providers, key=lambda item: item.provider_id
    ):
        ledger.append_event(
            source=provider.provider_id,
            event_type="runtime.identity",
            time=zero,
            payload=provider_identity_payload(provider),
        )
    for provider in sorted(
        run.environment.providers, key=lambda item: item.provider_id
    ):
        ledger.append_event(
            source=provider.provider_id,
            event_type="provider.reset",
            time=zero,
            payload=(
                NamedValue(
                    name="state_digest",
                    value=hashlib.sha256(
                        f"{provider.provider_id}:reset".encode()
                    ).hexdigest(),
                ),
            ),
        )
    ledger.append_event(
        source="harness",
        event_type="barrier.ready",
        time=zero,
        payload=(NamedValue(name="seed", value=run.seed),),
    )
    final_time = scene_state.at
    for barrier in barriers:
        for receipt in barrier.receipts:
            ledger.append_event(
                source=receipt.provider_id,
                event_type="provider.step-receipt",
                time=final_time,
                payload=_stage_receipt_payload(
                    run,
                    barrier,
                    receipt,
                    scene_state.scene_state_digest,
                ),
                provider_id=receipt.provider_id,
            )
        ledger.append_event(
            source="harness",
            event_type="stage.barrier-closed",
            time=final_time,
            payload=_stage_barrier_payload(
                run,
                barrier,
                scene_state.scene_state_digest,
            ),
        )
        if barrier.stage == "motion":
            ledger.append_event(
                source="harness",
                event_type="scene.state.committed",
                time=final_time,
                payload=_named_values(
                    {
                        "barrier_digest": barrier.barrier_digest,
                        "contribution_digests": _json_value(
                            list(scene_state.contribution_digests)
                        ),
                        "provider_ids": _json_value(list(barrier.provider_ids)),
                        "receipt_digests": _json_value(list(barrier.receipt_digests)),
                        "run_id": run.run_id,
                        "scenario_digest": run.scenario.scenario_digest,
                        "scene_state_digest": scene_state.scene_state_digest,
                        "stage": "motion",
                        "target_sim_time_ns": final_time.sim_time_ns,
                        "target_tick": final_time.tick,
                    }
                ),
            )
    ledger.append_event(
        source="harness",
        event_type="barrier.committed",
        time=final_time,
        payload=_named_values(
            {
                "provider_result_count": sum(
                    len(barrier.receipts) for barrier in barriers
                ),
                "run_id": run.run_id,
                "scenario_digest": run.scenario.scenario_digest,
                "scene_state_digest": scene_state.scene_state_digest,
                "stage": "tick",
                "stage_barriers": _json_value(
                    [
                        {
                            "barrier_digest": barrier.barrier_digest,
                            "contribution_digests": [
                                receipt.contribution_digest
                                for receipt in barrier.receipts
                            ],
                            "predecessor_barriers": [
                                predecessor.model_dump(mode="json")
                                for predecessor in barrier.predecessor_barriers
                            ],
                            "provider_ids": list(barrier.provider_ids),
                            "receipt_digests": list(barrier.receipt_digests),
                            "stage": barrier.stage,
                            "step_receipt_digests": [
                                receipt.step_receipt_digest
                                for receipt in barrier.receipts
                            ],
                        }
                        for barrier in barriers
                    ]
                ),
                "target_sim_time_ns": final_time.sim_time_ns,
                "target_tick": final_time.tick,
            }
        ),
    )
    if run.execution_scope == "formal_benchmark":
        _append_closed_process_streams(ledger, run, final_time)
    ledger.append_event(
        source="harness",
        event_type="run.completed",
        time=final_time,
        payload=(NamedValue(name="execution_scope", value=run.execution_scope),),
    )
    return ledger


def _make_runtime_seal(
    root: Path,
    run: ResolvedRunSpec,
    *,
    attempt_id: str,
    scene_and_barriers=None,
):
    root.mkdir(mode=0o700)
    scene_state, barriers = _runtime_scene(run) if scene_and_barriers is None else scene_and_barriers
    requirement = next(
        requirement
        for requirement in run.artifact_requirements
        if requirement.artifact_type == "event.log"
    )
    path = root / requirement.relative_path
    ledger = _runtime_ledger(run, scene_state, barriers)
    ledger.write_jsonl(path)
    payload = path.read_bytes()
    artifact = ArtifactRecord(
        artifact_id=requirement.artifact_id,
        artifact_type=requirement.artifact_type,
        producer_id=requirement.producer_id,
        visibility=requirement.visibility,
        relative_path=requirement.relative_path,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    public_requirement = next(
        requirement
        for requirement in run.artifact_requirements
        if requirement.visibility == "public"
    )
    public_path = root / public_requirement.relative_path
    public_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_payload = b'{"result":1}\n'
    public_path.write_bytes(public_payload)
    public_artifact = ArtifactRecord(
        artifact_id=public_requirement.artifact_id,
        artifact_type=public_requirement.artifact_type,
        producer_id=public_requirement.producer_id,
        visibility=public_requirement.visibility,
        relative_path=public_requirement.relative_path,
        sha256=hashlib.sha256(public_payload).hexdigest(),
        size_bytes=len(public_payload),
    )
    scene_requirement = next(
        requirement
        for requirement in run.artifact_requirements
        if requirement.artifact_type == "scene.state-history"
    )
    scene_path = root / scene_requirement.relative_path
    scene_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    scene_payload = scene_state_jsonl_bytes((scene_state,))
    scene_path.write_bytes(scene_payload)
    scene_artifact = ArtifactRecord(
        artifact_id=scene_requirement.artifact_id,
        artifact_type=scene_requirement.artifact_type,
        producer_id=scene_requirement.producer_id,
        visibility=scene_requirement.visibility,
        relative_path=scene_requirement.relative_path,
        sha256=hashlib.sha256(scene_payload).hexdigest(),
        size_bytes=len(scene_payload),
    )
    return seal_manifest(
        root=root,
        run_id=run.run_id,
        attempt_id=attempt_id,
        execution_scope=run.execution_scope,
        event_chain_root=ledger.chain_root,
        artifacts=(artifact, public_artifact, scene_artifact),
    )


def test_runner_executes_every_matrix_run_in_declared_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor()

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert summary.run_count == 4
    assert summary.execution_complete_count == 4
    assert summary.passed_count == 0
    assert {item.status for item in summary.runs} == {"invalid"}
    for item in summary.runs:
        assert item.public_trace is not None
        trace_path = (
            Path(config.output_root) / item.run_id / item.public_trace.relative_path
        )
        payload = trace_path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item.public_trace.sha256
        trace = json.loads(payload)
        assert trace["schema_version"] == "aero-bench.public-trace/v3"
        assert trace["phase"] == "sealed"
        assert trace["verifier_public"] is None
        assert trace["event_chain_root"] == item.public_trace.event_chain_root
    assert len([call for call in executor.calls if call[0] == "materialize"]) == 4
    assert len([call for call in executor.calls if call[0] == "cleanup"]) == 4
    assert (Path(config.output_root) / "runner-summary.json").read_bytes() == (
        canonical_json_bytes(summary.model_dump(mode="json")) + b"\n"
    )


def test_runner_preflight_block_never_starts_or_cleans_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(blocked=True)

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert summary.run_count == 4
    assert {item.status for item in summary.runs} == {"blocked"}
    assert {item.failure_classes for item in summary.runs} == {("preflight_blocked",)}
    assert not any(call[0] in {"start_runtime", "cleanup"} for call in executor.calls)


@pytest.mark.parametrize("failure", ["runtime_failure", "verifier_failure"])
def test_runner_cleans_handle_after_runtime_or_verifier_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(**{failure: True})

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert all(item.status == "error" for item in summary.runs)
    expected_failure = {
        "runtime_failure": "wait_runtime_failed",
        "verifier_failure": "wait_verifier_failed",
    }[failure]
    assert all(item.failure_classes == (expected_failure,) for item in summary.runs)
    assert len([call for call in executor.calls if call[0] == "cleanup"]) == 4
    assert (
        len([call for call in executor.calls if call[0] == "collect_failure_outputs"])
        == 4
    )
    for run_id in {run_id for _, run_id in executor.calls}:
        failure_output = ("collect_failure_outputs", run_id)
        cleanup = ("cleanup", run_id)
        if failure_output in executor.calls:
            assert executor.calls.index(failure_output) < executor.calls.index(cleanup)
    assert not any(call[0] == "cleanup_run" for call in executor.calls)
    if failure == "verifier_failure":
        assert all(item.public_trace is not None for item in summary.runs)
        for item in summary.runs:
            assert item.public_trace is not None
            trace = json.loads(
                (
                    Path(config.output_root)
                    / item.run_id
                    / item.public_trace.relative_path
                ).read_bytes()
            )
            assert trace["phase"] == "sealed"
            assert trace["verifier_public"] is None
    else:
        assert all(item.public_trace is None for item in summary.runs)


@pytest.mark.parametrize(
    ("cleanup_run_failure", "failure_classes"),
    [
        (False, ("start_runtime_failed",)),
        (True, ("start_runtime_failed", "cleanup_failed")),
    ],
)
def test_runner_retries_cleanup_after_start_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cleanup_run_failure: bool,
    failure_classes: tuple[str, ...],
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(
        startup_failure=True,
        cleanup_run_failure=cleanup_run_failure,
    )

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert all(item.status == "error" for item in summary.runs)
    assert all(item.failure_classes == failure_classes for item in summary.runs)
    assert len([call for call in executor.calls if call[0] == "cleanup_run"]) == 4
    assert not any(call[0] == "cleanup" for call in executor.calls)


def test_runner_surfaces_cleanup_failure_in_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(cleanup_failure=True)

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert all(item.status == "error" for item in summary.runs)
    assert all(item.failure_classes == ("cleanup_failed",) for item in summary.runs)


def test_runner_reraises_interrupt_after_cleanup_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor()

    def interrupted_runtime(handle: _FakeHandle, *, timeout_seconds: int) -> None:
        executor.calls.append(("wait_runtime", handle.run_id))
        raise KeyboardInterrupt("injected interrupt")

    executor.wait_runtime = interrupted_runtime

    with pytest.raises(KeyboardInterrupt, match="injected interrupt"):
        _run_with_executor(monkeypatch, bundle, config_path, executor)

    started = [run_id for name, run_id in executor.calls if name == "start_runtime"]
    assert len(started) == 1
    assert ("cleanup_run", started[0]) in executor.calls
    assert executor.calls.index(("wait_runtime", started[0])) < executor.calls.index(
        ("cleanup_run", started[0])
    )
    assert len([call for call in executor.calls if call[0] == "materialize"]) == 1
    assert not (Path(config.output_root) / "runner-summary.json").exists()


def test_runner_rejects_a_tampered_sealed_event_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(tamper_runtime_seal=True)

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert all(item.status == "error" for item in summary.runs)
    assert all(
        item.failure_classes
        == ("collect_and_seal_failed", "public_trace_projection_failed")
        for item in summary.runs
    )
    assert all(item.public_trace is None for item in summary.runs)


def test_runner_rejects_a_formal_report_not_bound_to_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    formal_run = as_formal_run(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    monkeypatch.setattr(
        runner_execution,
        "resolve_suite",
        lambda *_args, **_kwargs: (formal_run,),
    )
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(wrong_report_goal=True)

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert summary.runs[0].status == "error"
    assert summary.runs[0].verification is not None
    assert summary.runs[0].public_trace is None
    assert summary.runs[0].failure_classes == ("public_trace_projection_failed",)


def test_runner_never_publishes_a_partial_public_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor()
    original_publish = runner_execution._rename_directory_noreplace

    def fail_publication(source: Path, destination: Path) -> None:
        if destination.name == "public":
            raise OSError("injected publication failure")
        original_publish(source, destination)

    monkeypatch.setattr(
        runner_execution,
        "_rename_directory_noreplace",
        fail_publication,
    )

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert all(item.status == "error" for item in summary.runs)
    assert all(
        item.failure_classes == ("public_trace_projection_failed",)
        for item in summary.runs
    )
    for item in summary.runs:
        public_root = Path(config.output_root) / item.run_id / "public"
        assert not (public_root / "public-trace.json").exists()
        assert not (public_root / ".public-trace.json.tmp").exists()


def test_runner_config_is_strict_and_output_root_is_fresh(tmp_path: Path) -> None:
    output_root = tmp_path / "existing"
    config = _runner_config(output_root)
    output_root.mkdir()
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config, unexpected=True)

    with pytest.raises(RunnerError, match="RunnerConfig is invalid"):
        load_runner_config(config_path)

    clean_config_path = tmp_path / "runner-clean.json"
    _write_runner_config(
        clean_config_path,
        config.model_copy(update={"output_root": str(tmp_path / "new")}),
    )
    loaded = load_runner_config(clean_config_path)
    assert loaded.executor_kind == "docker_reference"
    assert loaded.output_root == str(tmp_path / "new")


def test_runner_rejects_zero_volume_keeper_digest(tmp_path: Path) -> None:
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner-zero.json"
    _write_runner_config(
        config_path,
        config,
        volume_keeper_image="registry.invalid/keeper@sha256:" + "0" * 64,
    )

    with pytest.raises(RunnerError, match="RunnerConfig is invalid"):
        load_runner_config(config_path)


def test_runner_config_requires_explicit_readiness_timeout(tmp_path: Path) -> None:
    config = _runner_config(tmp_path / "results")
    payload = config.model_dump(mode="json")
    payload.pop("readiness_timeout_seconds")
    config_path = tmp_path / "runner-missing-readiness.json"
    config_path.write_bytes(canonical_json_bytes(payload) + b"\n")

    with pytest.raises(RunnerError, match="RunnerConfig is invalid"):
        load_runner_config(config_path)


def test_runner_does_not_create_output_root_when_executor_construction_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    output_root = tmp_path / "results"
    config = _runner_config(output_root)
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)

    def fail_construction(_config, *, provider_registry):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(runner_execution, "build_docker_executor", fail_construction)
    with pytest.raises(RunnerError, match="could not be constructed"):
        run_suite(bundle.suite, config_path)
    assert not output_root.exists()


def test_runner_rejects_existing_output_root_only_at_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    output_root = tmp_path / "results"
    config = _runner_config(output_root)
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    output_root.mkdir()
    executor = _FakeExecutor(blocked=True)
    monkeypatch.setattr(
        runner_execution, "build_docker_executor", lambda _config, *, provider_registry: executor
    )

    with pytest.raises(RunnerError, match="output_root must be fresh"):
        run_suite(bundle.suite, config_path)


def test_runner_summary_binds_input_digests_and_permissions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(blocked=True)

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert summary.suite_sha256 == sha256_file(bundle.suite)
    assert summary.runner_config_sha256 == sha256_file(config_path)
    assert summary.execution_complete_count == 0
    summary_path = Path(config.output_root) / "runner-summary.json"
    assert summary_path.stat().st_mode & 0o777 == 0o600
    assert Path(config.output_root).stat().st_mode & 0o777 == 0o700


def test_runner_rejects_second_execution_after_first_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(blocked=True)
    monkeypatch.setattr(
        runner_execution, "build_docker_executor", lambda _config, *, provider_registry: executor
    )

    _run_with_executor(monkeypatch, bundle, config_path, executor)
    with pytest.raises(RunnerError, match="output_root must be fresh"):
        run_suite(bundle.suite, config_path)


def test_runner_public_api_has_no_executor_factory_parameter() -> None:
    assert "executor_factory" not in inspect.signature(run_suite).parameters


def test_runner_cli_returns_zero_for_completed_formal_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    formal_run = as_formal_run(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    monkeypatch.setattr(
        runner_execution,
        "resolve_suite",
        lambda *_args, **_kwargs: (formal_run,),
    )
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(verification_status="failed")
    monkeypatch.setattr(
        runner_execution,
        "build_docker_executor",
        lambda _config, *, provider_registry: executor,
    )

    assert runner_main((str(bundle.suite), str(config_path))) == 0
    summary = json.loads(
        (Path(config.output_root) / "runner-summary.json").read_text(encoding="utf-8")
    )
    assert summary["execution_complete_count"] == 1
    assert summary["passed_count"] == 0
    assert summary["runs"][0]["status"] == "failed"
    trace_identity = summary["runs"][0]["public_trace"]
    assert trace_identity is not None
    trace = json.loads(
        (
            Path(config.output_root)
            / formal_run.run_id
            / trace_identity["relative_path"]
        ).read_bytes()
    )
    assert trace["phase"] == "verified"
    assert trace["verifier_public"]["status"] == "failed"


def test_runner_cannot_count_a_non_json_metric_as_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    formal_run = as_formal_run(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    monkeypatch.setattr(
        runner_execution,
        "resolve_suite",
        lambda *_args, **_kwargs: (formal_run,),
    )
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(metric_value="NaN")

    summary = _run_with_executor(monkeypatch, bundle, config_path, executor)

    assert summary.passed_count == 0
    assert summary.runs[0].status == "error"
    assert summary.runs[0].failure_classes == ("collect_verification_outputs_failed",)


def test_runner_cli_returns_zero_for_completed_executor_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor()
    monkeypatch.setattr(
        runner_execution,
        "build_docker_executor",
        lambda _config, *, provider_registry: executor,
    )

    assert runner_main((str(bundle.suite), str(config_path))) == 0
    summary = json.loads(
        (Path(config.output_root) / "runner-summary.json").read_text(encoding="utf-8")
    )
    assert summary["execution_complete_count"] == 4
    assert summary["passed_count"] == 0
    assert {run["status"] for run in summary["runs"]} == {"invalid"}


def test_runner_cli_rejects_incomplete_formal_invalid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = build_bundle(tmp_path / "bundle")
    formal_run = as_formal_run(
        resolve_bundle(bundle.suite, executor_kind="docker_reference")[0]
    )
    monkeypatch.setattr(
        runner_execution,
        "resolve_suite",
        lambda *_args, **_kwargs: (formal_run,),
    )
    config = _runner_config(tmp_path / "results")
    config_path = tmp_path / "runner.json"
    _write_runner_config(config_path, config)
    executor = _FakeExecutor(verification_status="invalid")
    monkeypatch.setattr(
        runner_execution,
        "build_docker_executor",
        lambda _config, *, provider_registry: executor,
    )

    assert runner_main((str(bundle.suite), str(config_path))) == 1
    summary = json.loads(
        (Path(config.output_root) / "runner-summary.json").read_text(encoding="utf-8")
    )
    assert summary["execution_complete_count"] == 0
    assert summary["passed_count"] == 0
    assert summary["runs"][0]["status"] == "error"
    assert summary["runs"][0]["failure_classes"] == ["public_trace_projection_failed"]


def test_runner_rejects_kubernetes_config_without_compatibility_path(
    tmp_path: Path,
) -> None:
    config = _runner_config(tmp_path / "results")
    payload = config.model_dump(mode="json")
    payload["executor_kind"] = "kubernetes_cluster"
    config_path = tmp_path / "runner-k8s.json"
    config_path.write_bytes(canonical_json_bytes(payload) + b"\n")

    with pytest.raises(RunnerError, match="RunnerConfig is invalid"):
        load_runner_config(config_path)
