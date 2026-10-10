"""Unit evidence wiring; closed-stage fixtures are not native/formal execution."""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.runtime.ledger import EventLedger
from aero_bench.tasks.logistics.runtime_airspace import ClosedMotionAirspaceTracker
from aero_bench.trace.projector import PublicProjectorError, project_public_run_event
from tests.tasks.test_logistics_runtime_hook import (
    AT, _build_hook, _closed_stage, _derive_observation_batch, _running_stack, build_fixture,
)


ZONE = {
    "id": "zone.facility-window", "name": "Timed pad zone",
    "polygon": [{"x": -100, "z": -100}, {"x": 100, "z": -100},
                {"x": 100, "z": 100}, {"x": -100, "z": 100}],
    "floorM": 0, "ceilingM": 100, "startsAtS": 1.5, "endsAtS": 20,
    "source": {"kind": "manual", "label": "Unit authored zone", "uri": None},
}


@pytest.fixture(scope="module")
def fixture(tmp_path_factory):
    return build_fixture(tmp_path_factory.mktemp("closed-airspace"), no_fly_zones=(ZONE,))


def tracker(fixture):
    return ClosedMotionAirspaceTracker(
        run_id=fixture.resolved_run.run_id, scenario_digest=fixture.scenario.scenario_digest,
        step_ns=fixture.environment.clock.step_ns, package=fixture.package, bindings=fixture.bindings,
    )


def test_timed_zone_is_derived_from_two_closed_native_witnesses(fixture):
    detector = tracker(fixture)
    first = detector.prepare(_derive_observation_batch(fixture))
    assert first.segments == ()
    detector.commit(first)
    second = detector.prepare(_derive_observation_batch(fixture, at=SimulationTime(tick=2, sim_time_ns=2_000_000_000)))
    assert len(second.segments) == 1
    segment = second.segments[0]
    assert len(segment.witnesses) == segment.report.sample_count == 2
    assert segment.report.violations[0].event_type == "stationary_activation"
    assert segment.report.violations[0].start_time_s == 1.5
    assert segment.report.interpolation_assumed is True
    assert segment.report.body_clearance == "none"
    assert segment.provenance_verified is False
    assert segment.witnesses[-1].source_event_id == "state.uav.alpha.2"
    detector.commit(second)
    replay = detector.prepare(_derive_observation_batch(fixture, at=second.at))
    assert replay.replayed and replay.segments == ()
    detector.commit(replay)


def test_prepare_does_not_advance_until_business_acknowledgment(fixture):
    detector = tracker(fixture)
    first = detector.prepare(_derive_observation_batch(fixture))
    second_batch = _derive_observation_batch(fixture, at=SimulationTime(tick=2, sim_time_ns=2_000_000_000))
    with pytest.raises(ValueError, match="consecutive barrier"):
        detector.prepare(second_batch)
    detector.commit(first)
    second = detector.prepare(second_batch)
    assert second.segments
    other = tracker(fixture)
    with pytest.raises(ValueError, match="another tracker"):
        other.commit(second)


@pytest.mark.parametrize("at", [SimulationTime(tick=2, sim_time_ns=2_000_000_000),
                               SimulationTime(tick=1, sim_time_ns=3_000_000_000)])
def test_missing_initial_or_changed_tick_duration_is_rejected(fixture, at):
    with pytest.raises(ValueError, match="consecutive barrier"):
        tracker(fixture).prepare(_derive_observation_batch(fixture, at=at))


def test_foreign_run_and_conflicting_committed_barrier_are_rejected(fixture):
    detector = tracker(fixture)
    with pytest.raises(ValueError, match="another run"):
        detector.prepare(_derive_observation_batch(fixture, run_id="c" * 64))
    first = detector.prepare(_derive_observation_batch(fixture))
    detector.commit(first)
    with pytest.raises(ValueError, match="conflicts"):
        detector.prepare(_derive_observation_batch(fixture, up_m_offset=0.01))


def test_declared_fleet_cannot_be_omitted(fixture):
    with pytest.raises(ValueError, match="exact declared native fleet"):
        ClosedMotionAirspaceTracker(
            run_id=fixture.resolved_run.run_id, scenario_digest=fixture.scenario.scenario_digest,
            step_ns=fixture.environment.clock.step_ns, package=fixture.package,
            bindings=fixture.bindings.model_copy(update={"aircraft": ()}),
        )


def test_hook_records_private_airspace_evidence_only_after_real_rpc_ack(fixture, tmp_path):
    # This exercises the actual Business service over loopback; its motion
    # records remain explicit unit fixtures, not native Gazebo measurements.
    active_zone = {**ZONE, "startsAtS": 0}
    active = build_fixture(tmp_path / "active", no_fly_zones=(active_zone,))
    rpc_root = tmp_path / "rpc"
    rpc_root.mkdir()

    async def run():
        async with _running_stack(active, rpc_root) as (service, client):
            ledger = EventLedger(run_id=active.resolved_run.run_id)
            hook = _build_hook(active, client, ledger)
            _, scene, event, _ = _closed_stage(active)
            await hook.on_stage_barriers_closed((event,), scene_state=scene,
                                                stage_barriers=(scene.stage_barrier,), target=AT)
            assert service._observations.last_sequence == 1
            assert ledger.records[-2].event.event_type == "logistics.observation.ingested"
            airspace = ledger.records[-1].event
            assert airspace.event_type == "logistics.airspace.segment.v1"
            assert airspace.source_kind == "harness"
            assert {item.scope for item in airspace.visibility} == {"private", "verifier"}
            with pytest.raises(PublicProjectorError, match="not explicitly public"):
                project_public_run_event(airspace, public_event_ids=set())
            payload = {item.name: item.value for item in airspace.payload}
            assert hashlib.sha256(payload["segment_json"].encode()).hexdigest() == payload["segment_digest"]
            assert json.loads(payload["segment_json"])["provenance_verified"] is False
            await hook.on_stage_barriers_closed((event,), scene_state=scene,
                                                stage_barriers=(scene.stage_barrier,), target=AT)
            assert sum(item.event.event_type == "logistics.airspace.segment.v1" for item in ledger.records) == 1

    asyncio.run(run())
