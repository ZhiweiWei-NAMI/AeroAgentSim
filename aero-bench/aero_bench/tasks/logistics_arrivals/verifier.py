"""Independent replay and seal/barrier checks for non-physical arrivals."""

import hashlib
import json
from pathlib import Path

from aero_bench.artifacts.contracts import EvidenceReference, seal_manifest
from aero_bench.config.loader import BundleReader
from aero_bench.providers.rpc import parse_json_object
from aero_bench.providers.logistics_business.scheduled_arrivals import (
    SCHEDULED_ARRIVAL_EVENT_SCHEMA,
    ScheduledOrderApplication,
)
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.evidence import load_sealed_event_ledger
from aero_bench.runtime.scene_history import read_scene_state_history_jsonl
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.order_arrivals import (
    OrderArrivalCommand,
    OrderArrivalService,
    OrderArrivalSnapshot,
    replay_order_arrivals,
)
from aero_bench.tasks.logistics_arrivals.contracts import (
    METRIC_ID,
    LogisticsArrivalsVerifierConfig,
)
from aero_bench.tasks.logistics_arrivals.integration import load_context
from aero_bench.verifier.contracts import (
    GoalResult,
    MetricResult,
    VerificationReport,
    validate_report_against_run,
)


def arrival_metric_evidence(*, run, business_artifact_id):
    """Keep private authority and cite its independently checked public clock history."""
    histories = [
        item
        for item in run.artifact_requirements
        if item.artifact_type == "scene.state-history"
    ]
    if len(histories) != 1 or histories[0].visibility != "public":
        raise ValueError(
            "arrival metrics require exactly one public scene state history"
        )
    return (
        EvidenceReference(
            artifact_id=business_artifact_id, selector="scheduled_evidence"
        ),
        EvidenceReference(
            artifact_id=histories[0].artifact_id,
            selector=f"ticks/1-{run.environment.clock.max_steps}",
        ),
    )


def validate_arrival_public_history(*, run, states, stage_inputs, committed_digests):
    if len(states) != run.environment.clock.max_steps or len(stage_inputs) != len(
        states
    ):
        raise ValueError("public arrival clock history lacks complete stage coverage")
    for state, stage in zip(states, stage_inputs, strict=True):
        tick = state.at.tick
        if (
            state.run_id != run.run_id
            or state.scenario_digest != run.scenario.scenario_digest
            or state.at.sim_time_ns != tick * run.environment.clock.step_ns
            or stage["target"] != state.at.model_dump(mode="json")
            or stage["scene_state_digest"] != state.scene_state_digest
            or stage["motion_barrier_digest"] != state.stage_barrier.barrier_digest
            or committed_digests.get(tick) != state.scene_state_digest
        ):
            raise ValueError(
                "public scene history differs from the native Business input/commit"
            )


def verify_native_arrivals(
    *,
    config,
    artifact,
    run_id,
    runtime_image,
    config_digest,
    seed,
    scenario_digest,
    step_ns,
    final_tick,
):
    """Check native evidence against inputs; this function makes no seal claim."""
    witness = artifact["scheduled_evidence"]
    current = SimulationTime(tick=final_tick, sim_time_ns=final_tick * step_ns)
    for key, expected in {
        "run_id": run_id,
        "provider_id": config.provider_id,
        "runtime_image": runtime_image,
        "config_digest": config_digest,
        "seed": seed,
        "scenario_digest": scenario_digest,
        "current_time": current.model_dump(mode="json"),
        "requested": [item.model_dump(mode="json") for item in config.scheduled_orders],
    }.items():
        if witness[key] != expected:
            raise ValueError(f"native scheduled evidence differs from pinned {key}")
    if artifact["initial_requests"] or artifact["initial_time"] != {
        "tick": 0,
        "sim_time_ns": 0,
    }:
        raise ValueError(
            "non-physical arrivals cannot substitute baseline orders for scheduled creation"
        )
    if (
        artifact["facilities"] != config.task_package.facilities.model_dump(mode="json")
        or artifact["principal_bindings"]
    ):
        raise ValueError(
            "native catalogue/principal binding differs from the arrivals profile"
        )
    observation = artifact["observation_journal"]
    if (
        observation["records"]
        or observation["run_id"] != run_id
        or observation["provider_id"] != config.provider_id
    ):
        raise ValueError(
            "non-physical arrivals cannot carry physical observation evidence"
        )
    from aero_bench.tasks.logistics.observation_ingress import ObservationJournal

    journal = ObservationJournal.model_validate(observation)
    if artifact["observation_journal_digest"] != journal.canonical_digest():
        raise ValueError("native observation journal digest changed")
    stages = witness["accepted_stage_inputs"]
    if len(stages) != final_tick:
        raise ValueError("arrival authority lacks complete Business stage coverage")
    for tick, stage in enumerate(stages, 1):
        if (
            stage["run_id"] != run_id
            or stage["scenario_digest"] != scenario_digest
            or stage["target"] != {"tick": tick, "sim_time_ns": tick * step_ns}
            or stage["predecessor_barriers"]
            != [{"stage": "motion", "barrier_digest": stage["motion_barrier_digest"]}]
        ):
            raise ValueError(
                "arrival stage input does not bind its exact native clock/motion predecessor"
            )
    actual = tuple(
        ScheduledOrderApplication.model_validate(value) for value in witness["applied"]
    )
    authority = OrderArrivalService(
        catalogue=config.task_package.facilities,
        fleet=config.task_package.fleet,
        actor_grants=config.task_package.actor_grants,
        initial_requests=(),
        initial_time=SimulationTime(tick=0, sim_time_ns=0),
    )
    expected = []
    for tick, stage in enumerate(stages, 1):
        at = SimulationTime(tick=tick, sim_time_ns=tick * step_ns)
        authority.advance_time(at)
        for requested in config.scheduled_orders:
            if requested.at_tick != tick:
                continue
            arrival = authority.submit(
                OrderArrivalCommand(
                    command_id=requested.event_id,
                    actor_id=requested.actor_id,
                    actor_role="business",
                    time=at,
                    source_identity=f"provider:{config.provider_id}:scheduled",
                    order=requested.order,
                )
            )
            expected.append(
                ScheduledOrderApplication(
                    schema_version="aero-bench.logistics-scheduled-application/v1",
                    requested=requested,
                    applied_at=at,
                    arrival=arrival,
                    arrival_snapshot_digest=authority.digest,
                    motion_barrier_digest=stage["motion_barrier_digest"],
                    scene_state_digest=stage["scene_state_digest"],
                )
            )
    if actual != tuple(expected) or len(actual) != len(config.scheduled_orders):
        raise ValueError(
            "scheduled application coverage/content differs from native arrival replay"
        )
    snapshot = OrderArrivalSnapshot.model_validate(artifact["arrival_snapshot"])
    replayed = replay_order_arrivals(
        catalogue=config.task_package.facilities,
        fleet=config.task_package.fleet,
        actor_grants=config.task_package.actor_grants,
        initial_requests=(),
        initial_time=SimulationTime(tick=0, sim_time_ns=0),
        events=snapshot.events,
        current_time=current,
    )
    if (
        snapshot != replayed
        or snapshot != authority.snapshot
        or artifact["arrival_snapshot_digest"] != snapshot.canonical_digest()
        or artifact["arrival"]
        != [event.model_dump(mode="json") for event in snapshot.events]
        or artifact["ledger"] != snapshot.ledger.model_dump(mode="json")
        or artifact["history"]
        != [record.model_dump(mode="json") for record in snapshot.ledger.history]
        or snapshot.ledger.history
        or artifact["logistics_history_root"] != "0" * 64
    ):
        raise ValueError(
            "sealed arrival/history authority differs from independent replay or claims a lifecycle transition"
        )
    state = {
        "run_id": run_id,
        "provider_id": config.provider_id,
        "config_digest": config_digest,
        "seed": seed,
        "scenario_digest": scenario_digest,
        "current_time": current.model_dump(mode="json"),
        "accepted_stage_inputs": stages,
        "logistics_history_root": artifact["logistics_history_root"],
        "history_length": len(artifact["history"]),
        "ledger": artifact["ledger"],
        "arrival_snapshot_digest": artifact["arrival_snapshot_digest"],
        "arrival_snapshot": artifact["arrival_snapshot"],
        "initial_requests": artifact["initial_requests"],
        "initial_time": artifact["initial_time"],
        "observation_journal": artifact["observation_journal"],
        "observation_journal_digest": artifact["observation_journal_digest"],
        "facilities": artifact["facilities"],
        "principal_bindings": artifact["principal_bindings"],
        "scheduled_applications": witness["applied"],
    }
    if (
        hashlib.sha256(canonical_json_bytes(state)).hexdigest()
        != witness["final_state_digest"]
    ):
        raise ValueError(
            "native final state digest differs from its sealed arrival/history inputs"
        )
    return actual


def verify_logistics_arrivals_sealed(*, bundle_root, run, seal, sealed_root):
    reader = BundleReader(bundle_root)
    LogisticsArrivalsVerifierConfig.model_validate(
        reader.validate_schema_bound_file(run.task.verifier.config)
    )
    _, provider, config = load_context(
        reader=reader, task=run.task, environment=run.environment, agents=run.agents
    )
    if (
        seal.run_id != run.run_id
        or seal.execution_scope != run.execution_scope
        or run.execution_scope != "formal_benchmark"
    ):
        raise ValueError("arrival seal does not bind this formal run")
    if (
        seal_manifest(
            root=sealed_root,
            run_id=run.run_id,
            attempt_id=seal.attempt_id,
            execution_scope=run.execution_scope,
            event_chain_root=seal.event_chain_root,
            artifacts=seal.artifacts,
            manifest_name="seal-manifest.json",
        )
        != seal
    ):
        raise ValueError("arrival runtime seal changed")
    if {record.artifact_id for record in seal.artifacts} != {
        item.artifact_id for item in run.artifact_requirements
    }:
        raise ValueError("arrival seal inventory differs from the declared run")
    for requirement in run.artifact_requirements:
        record = next(
            value
            for value in seal.artifacts
            if value.artifact_id == requirement.artifact_id
        )
        if (
            any(
                getattr(record, key) != getattr(requirement, key)
                for key in (
                    "artifact_type",
                    "producer_id",
                    "visibility",
                    "relative_path",
                )
            )
            or record.size_bytes > requirement.max_size_bytes
        ):
            raise ValueError(
                "arrival seal artifact violates its declared authority/size"
            )
    ledger = load_sealed_event_ledger(run=run, seal=seal, seal_root=sealed_root)
    terminal = ledger.records[-1].event
    if terminal.event_type != "run.completed" or terminal.time != SimulationTime(
        tick=run.environment.clock.max_steps,
        sim_time_ns=run.environment.clock.max_steps * run.environment.clock.step_ns,
    ):
        raise ValueError("arrivals must complete the exact declared horizon")
    requirement = provider.artifact_requirements[0]
    path = Path(sealed_root) / requirement.relative_path
    raw = path.read_bytes()
    artifact = parse_json_object(raw)
    if (
        canonical_json_bytes(artifact) != raw
        or artifact["schema_version"] != "aero-bench.logistics-business-state/v1"
        or artifact["source_artifact_id"] != requirement.artifact_id
        or artifact["event_chain_root"] != seal.event_chain_root
    ):
        raise ValueError(
            "native arrival artifact lacks its canonical finalization binding"
        )
    applications = verify_native_arrivals(
        config=config,
        artifact=artifact,
        run_id=run.run_id,
        runtime_image=provider.workload.runtime.image,
        config_digest=provider.config.file.sha256,
        seed=run.seed,
        scenario_digest=run.scenario.scenario_digest,
        step_ns=run.environment.clock.step_ns,
        final_tick=terminal.time.tick,
    )
    emitted, motion, business, committed = {}, {}, {}, {}
    for record in ledger.records:
        event = record.event
        payload = {item.name: item.value for item in event.payload}
        if event.payload_schema_id == SCHEDULED_ARRIVAL_EVENT_SCHEMA:
            app = ScheduledOrderApplication.model_validate(
                parse_json_object(payload["application_json"].encode())
            )
            if (
                app.requested.event_id in emitted
                or event.source_kind != "provider"
                or event.provider_id != provider.provider_id
                or event.source != provider.provider_id
                or event.workload_id != provider.provider_id
                or event.event_type != f"scheduled.{app.requested.event_id}"
                or event.time != app.applied_at
            ):
                raise ValueError(
                    "scheduled arrival event has invalid/duplicate Provider authority"
                )
            emitted[app.requested.event_id] = app
        elif event.event_type == "stage.barrier-closed":
            if payload["stage"] == "motion":
                motion[payload["target_tick"]] = payload["barrier_digest"]
            elif payload["stage"] == "business_environment":
                ids = json.loads(payload["provider_ids"])
                if ids != [provider.provider_id]:
                    raise ValueError("Business barrier lacks its sole native Provider")
                business[payload["target_tick"]] = payload
        elif event.event_type == "scene.state.committed":
            committed[event.time.tick] = payload["scene_state_digest"]
    if emitted != {item.requested.event_id: item for item in applications} or set(
        business
    ) != set(range(1, terminal.time.tick + 1)):
        raise ValueError(
            "arrival Provider events or closed Business barriers are incomplete"
        )
    for stage in artifact["scheduled_evidence"]["accepted_stage_inputs"]:
        tick = stage["target"]["tick"]
        if (
            motion.get(tick) != stage["motion_barrier_digest"]
            or business[tick]["scene_state_digest"] != stage["scene_state_digest"]
        ):
            raise ValueError(
                "native arrival input differs from the sealed motion/Business barrier"
            )
    evidence = arrival_metric_evidence(
        run=run, business_artifact_id=requirement.artifact_id
    )
    history_requirement = next(
        item
        for item in run.artifact_requirements
        if item.artifact_type == "scene.state-history"
    )
    states = read_scene_state_history_jsonl(
        Path(sealed_root) / history_requirement.relative_path
    )
    validate_arrival_public_history(
        run=run,
        states=states,
        stage_inputs=artifact["scheduled_evidence"]["accepted_stage_inputs"],
        committed_digests=committed,
    )
    report = VerificationReport(
        schema_version="aero-bench.verification/v1",
        run_id=run.run_id,
        execution_scope=run.execution_scope,
        status="passed",
        coverage_complete=True,
        goals=tuple(
            GoalResult(
                goal_id=goal.goal_id,
                passed=True,
                metrics=(
                    MetricResult(
                        metric_id=METRIC_ID, value=1.0, unit=None, evidence=evidence
                    ),
                ),
                failure_class=None,
            )
            for goal in run.task.goals
        ),
    )
    validate_report_against_run(report, run)
    return report
