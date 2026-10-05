from __future__ import annotations

import hashlib
import json
import asyncio

import pytest

from aero_bench.artifacts import seal_manifest
from aero_bench.runtime.contracts import StageBarrierDigest, SimulationTime
from aero_bench.runtime.barrier import ProviderBarrier
from aero_bench.providers.stages import ordered_enabled_stages
from aero_bench.runtime.evidence import (
    _parse_identifier_inventory, _parse_predecessor_barriers,
    _reconstruct_stage_barrier, _reconstruct_stage_receipt,
    load_sealed_event_ledger,
)
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from tests.support import as_formal_run, build_bundle, resolve_bundle
from tests.test_runner import (
    _make_runtime_seal, _runtime_scene, _stage_barrier, _named_values,
)
from aero_bench.config.resolver import resolve_suite
from aero_bench.providers.registry import ProviderRegistry
from aero_bench.runtime.harness import HarnessCoordinator
from aero_bench.tasks.registry import builtin_task_package_resolvers
from tests.runtime.test_harness_service import _ServiceSession
from tests.support import fixture_provider_registry


def stage_swapped_fixture(root):
    """Generate a new, fully hashed mechanical ledger with two stages reassigned."""
    bundle = build_bundle(root / "bundle")
    run = as_formal_run(resolve_bundle(bundle.suite, executor_kind="docker_reference")[0])
    scene, barriers = _runtime_scene(run)
    swapped = [barriers[0]]
    for old_barrier in barriers[1:]:
        ids = tuple(sorted(
            "business" if key == "network" else "network" if key == "business" else key
            for key in old_barrier.provider_ids
        ))
        barrier, _ = _stage_barrier(
            run, at=scene.at, stage=old_barrier.stage, provider_ids=ids,
            scene_state_digest=scene.scene_state_digest,
            predecessor_barriers=tuple(StageBarrierDigest(
                stage=prior.stage, barrier_digest=prior.barrier_digest
            ) for prior in swapped),
        )
        swapped.append(barrier)
    destination = root / "wrong-stage-seal"
    seal = _make_runtime_seal(destination, run, attempt_id="stage-swapped",
                              scene_and_barriers=(scene, tuple(swapped)))
    return run, destination, seal


def assert_all_stage_hashes_and_seal_are_consistent(root, seal):
    event_artifact = next(a for a in seal.artifacts if a.artifact_type == "event.log")
    ledger = EventLedger.read_jsonl(root / event_artifact.relative_path)
    ledger.verify()
    assert ledger.chain_root == seal.event_chain_root
    receipts = {}
    for record in ledger.records:
        payload = {item.name: item.value for item in record.event.payload}
        if record.event.event_type == "provider.step-receipt":
            receipt = _reconstruct_stage_receipt(payload)
            assert receipt.receipt_digest == payload["stage_receipt_digest"]
            receipts[(record.event.time.tick, receipt.provider_id)] = receipt
        elif record.event.event_type == "stage.barrier-closed":
            provider_ids = json.loads(payload["provider_ids"])
            barrier = _reconstruct_stage_barrier(payload, tuple(
                receipts[(record.event.time.tick, key)] for key in provider_ids))
            assert barrier.barrier_digest == payload["barrier_digest"]
    assert seal == seal_manifest(root=root, run_id=seal.run_id, attempt_id=seal.attempt_id,
                                execution_scope=seal.execution_scope,
                                event_chain_root=seal.event_chain_root, artifacts=seal.artifacts)
    for artifact in seal.artifacts:
        assert hashlib.sha256((root / artifact.relative_path).read_bytes()).hexdigest() == artifact.sha256


def test_offline_verifier_accepts_three_stages_and_multiple_providers_per_stage(tmp_path):
    bundle = build_bundle(tmp_path / "bundle")
    run = as_formal_run(resolve_bundle(bundle.suite, executor_kind="docker_reference")[0])
    destination = tmp_path / "positive-seal"
    seal = _make_runtime_seal(destination, run, attempt_id="stage-positive")
    ledger = load_sealed_event_ledger(run=run, seal=seal, seal_root=destination)
    stages = [{item.name: item.value for item in record.event.payload}["stage"]
              for record in ledger.records if record.event.event_type == "stage.barrier-closed"]
    assert stages == ["motion", "network", "business_environment"]
    assert_all_stage_hashes_and_seal_are_consistent(destination, seal)


def test_harness_uses_serialized_stage_despite_business_role_and_capability_text(tmp_path):
    bundle = build_bundle(tmp_path / "bundle")
    run = resolve_suite(str(bundle.suite), executor_kind="docker_reference",
                        task_package_resolvers=builtin_task_package_resolvers(),
                        provider_registry=fixture_provider_registry(stage_overrides={
                            "fixture.business-artifact-writer": "network"}))[0]
    ledger = EventLedger(run_id=run.run_id)
    sessions = {p.provider_id: _ServiceSession(ProviderRegistry._manifest_for(p),
                                             run.run_id, run.scenario)
                for p in run.environment.providers}
    coordinator = HarnessCoordinator(run=run, providers=sessions, ledger=ledger)

    async def advance():
        await coordinator.prepare()
        await coordinator._advance_provider_barrier()
        await coordinator.shutdown()

    asyncio.run(advance())
    closures = [{item.name: item.value for item in r.event.payload}
                for r in ledger.records if r.event.event_type == "stage.barrier-closed"]
    assert [(p["stage"], json.loads(p["provider_ids"])) for p in closures] == [
        ("motion", ["flight", "traffic"]),
        ("network", ["business", "network"]),
        ("business_environment", ["observation", "world"]),
    ]


def _rewrite_event_payloads(ledger, mutate):
    rewritten = EventLedger(run_id=ledger.run_id)
    for record in ledger.records:
        event = record.event
        payload = {item.name: item.value for item in event.payload}
        mutate(event, payload)
        rewritten.append_event(
            source=event.source, source_kind=event.source_kind,
            workload_id=event.workload_id, event_type=event.event_type,
            time=event.time, wall_time_ns=event.wall_time_ns,
            payload=_named_values(payload), payload_schema_id=event.payload_schema_id,
            correlation_id=event.correlation_id, parent_event_id=event.parent_event_id,
            causal_event_ids=event.causal_event_ids, visibility=event.visibility,
            provider_id=event.provider_id, entity_id=event.entity_id,
            agent_id=event.agent_id, observation_id=event.observation_id,
            command_id=event.command_id, query_id=event.query_id,
            frame_id=event.frame_id, vehicle_id=event.vehicle_id,
            interaction_type=(None if event.interaction is None else event.interaction.interaction_type),
        )
    return rewritten


@pytest.mark.parametrize("change, message", [
    ("receipt_barrier", "does not bind its stage closure"),
    ("scene_provider_ids", "SceneState commit does not bind"),
    ("final_commit_inventory", "do not match the closed stages"),
    ("closure_provider_ids", "do not match its resolved stage set"),
    ("closure_receipt_inventory", "do not bind its stage receipts"),
    ("closure_order", "do not follow the runtime barrier order"),
])
def test_rehashed_ledger_refuses_wrong_closure_scene_and_tick_bindings(tmp_path, change, message):
    bundle = build_bundle(tmp_path / "bundle")
    run = as_formal_run(resolve_bundle(bundle.suite, executor_kind="docker_reference")[0])
    destination = tmp_path / "changed-seal"
    seal = _make_runtime_seal(destination, run, attempt_id="binding-negative")
    event_artifact = next(a for a in seal.artifacts if a.artifact_type == "event.log")
    event_path = destination / event_artifact.relative_path
    original = EventLedger.read_jsonl(event_path)

    def mutate(event, payload):
        if change == "receipt_barrier" and event.event_type == "provider.step-receipt":
            payload["barrier_digest"] = "a" * 64
        if change == "scene_provider_ids" and event.event_type == "scene.state.committed":
            payload["provider_ids"] = "[]"
        if change == "final_commit_inventory" and event.event_type == "barrier.committed":
            payload["stage_barriers"] = "[]"
        if event.event_type == "stage.barrier-closed":
            if change == "closure_provider_ids":
                payload["provider_ids"] = "[]"
            if change == "closure_receipt_inventory":
                payload["receipt_digests"] = "[]"
            if change == "closure_order":
                payload["stage"] = {"motion": "network", "network": "motion",
                                    "business_environment": "business_environment"}[payload["stage"]]

    changed = _rewrite_event_payloads(original, mutate)
    changed.verify()
    event_path.unlink()
    changed.write_jsonl(event_path)
    updated_artifact = event_artifact.model_copy(update={
        "sha256": hashlib.sha256(event_path.read_bytes()).hexdigest(),
        "size_bytes": event_path.stat().st_size,
    })
    new_seal = seal_manifest(root=destination, run_id=run.run_id, attempt_id=seal.attempt_id,
                             execution_scope=run.execution_scope, event_chain_root=changed.chain_root,
                             artifacts=tuple(updated_artifact if a == event_artifact else a
                                             for a in seal.artifacts))
    with pytest.raises(ValueError, match=message):
        load_sealed_event_ledger(run=run, seal=new_seal, seal_root=destination)


def test_fully_rehashed_and_resealed_stage_swap_is_semantically_rejected(tmp_path):
    run, destination, seal = stage_swapped_fixture(tmp_path)
    assert_all_stage_hashes_and_seal_are_consistent(destination, seal)
    with pytest.raises(ValueError, match="does not match its resolved stage"):
        load_sealed_event_ledger(run=run, seal=seal, seal_root=destination)


@pytest.mark.parametrize("value", [
    '["z","a"]', '["a","a"]', '["a",1]', '[ "a" ]', '["a"]\n',
    {"a": "a"}, '{"unexpected":[]}',
])
def test_stage_provider_inventory_rejects_noncanonical_order_duplicates_and_types(value):
    with pytest.raises(ValueError):
        _parse_identifier_inventory(value, field="provider_ids")


def test_empty_motion_inventory_and_predecessors_are_valid():
    assert _parse_identifier_inventory("[]", field="provider_ids") == ()
    assert _parse_predecessor_barriers("[]") == ()
    stages = ordered_enabled_stages({"motion": (), "network": (), "business_environment": ()})
    assert stages == ("motion",)
    barrier = ProviderBarrier(
        run_id="a" * 64, scenario_digest="b" * 64, step_ns=100,
        enabled_stages=stages, provider_ids_by_stage={"motion": ()},
    )
    target = SimulationTime(tick=1, sim_time_ns=100)
    barrier.begin_tick(target)
    assert barrier.begin_stage("motion") == ()
    closed = barrier.close_stage()
    closure = {
        "run_id": closed.run_id, "scenario_digest": closed.scenario_digest,
        "target_tick": target.tick, "target_sim_time_ns": target.sim_time_ns,
        "stage": "motion", "scene_state_digest": "c" * 64,
        "predecessor_barriers": "[]", "provider_ids": "[]",
    }
    assert _reconstruct_stage_barrier(closure, ()) == closed
    committed = barrier.commit_tick()
    assert committed.time == target
    assert committed.stage_barriers == (closed,)


@pytest.mark.parametrize("item", [
    {"stage": "motion", "barrier_digest": "a" * 64, "extra": True},
    {"stage": ["motion"], "barrier_digest": "a" * 64},
    {"stage": "motion", "barrier_digest": True},
])
def test_predecessor_inventory_rejects_extra_keys_and_invalid_types(item):
    with pytest.raises(ValueError):
        _parse_predecessor_barriers(canonical_json_bytes([item]).decode())
