"""Focused regressions for the closed-stage batch consumer (stage slice).

The observations and batches are constructed from the actual accepted helpers
(``_machine`` / ``_observation``) of the runtime module, mirroring its
``model_construct`` pattern for the batch envelope exactly as the journal
does.
"""

import pytest

from tests.tasks import test_native_parcel_runtime as h
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import assess_facility_presence
from aero_bench.tasks.logistics.native_parcel_runtime import (
    NativeParcelRuntimeError,
    SeekConsistencyError,
)
from aero_bench.tasks.logistics.native_parcel_stage import (
    NativeParcelStageConsumer,
)
from aero_bench.tasks.logistics.observation_ingress import (
    LogisticsObservationBatch,
)
from aero_bench.tasks.logistics.physical_observations import (
    observation_digest_value,
)


def _batch(*observations) -> LogisticsObservationBatch:
    """One typed stage batch from actual observations (batch-construct).

    Mirrors the ingress journal's ``model_construct`` envelope: the records
    are the actual typed observations and the envelope fields are bound from
    them exactly.
    """
    first = observations[0]
    return LogisticsObservationBatch.model_construct(
        schema_version="aero-bench.logistics-observation-batch/v1",
        run_id=first.run_id,
        scenario_digest=first.scenario_digest,
        at=first.at,
        source_scene_state_digest=first.source_scene_state_digest,
        source_stage_barrier_digest=first.source_stage_barrier_digest,
        observations=tuple(observations),
    )


def _rebind_stage_observation_pad(observation, *, pad, tolerances):
    """Re-assess the ONE shared carrier sample at another declared pad.

    Test-side re-assessment of one physical position: the sample, source
    event/evidence identity, measured attitude, frame origin and native
    carrier identity/profile are carried over unchanged; only the pad
    geometry, presence assessment and observation digest are re-derived by
    the accepted presence kernel.  No second position is ever minted.
    """
    assessment = assess_facility_presence(
        pad=pad,
        sample=observation.sample,
        profile=observation.profile,
        event=observation.event_binding,
        tolerances=tolerances,
    )
    candidate = observation.model_copy(
        update={
            "facility_id": pad.facility_id,
            "pad_index": pad.pad_index,
            "pad": pad,
            "assessment": assessment,
            "observation_digest": "0" * 64,
        }
    )
    return candidate.model_copy(
        update={"observation_digest": observation_digest_value(candidate)}
    )


def _carrier_stage_records(package, *, at, yaw_deg: float = 0.0):
    """One physical carrier position, honestly re-assessed at both pads.

    Builds ONE shared pickup-pad record through the actual ``_observation``
    helper and derives the dropoff record by re-assessing the SAME sample at
    the second declared pad — identical sample, source identity, measured
    attitude, frame origin and native carrier identity/profile, with only the
    per-pad geometry, assessment and digest re-derived.  No second physical
    position is ever minted.
    """
    pickup = h._observation(package, at=at, yaw_deg=yaw_deg)
    pad = facility_landing_pads(package.facilities.require(h._DROPOFF))[0]
    dropoff = _rebind_stage_observation_pad(
        pickup,
        pad=pad,
        tolerances=h._TOL,
    )
    return pickup, dropoff


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


def _consumer(package):
    machine = h._machine(package)
    return NativeParcelStageConsumer(machine)


def test_consumer_accepts_both_pad_orders_at_same_tick(package):
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    for ordered in ((pickup, dropoff), (dropoff, pickup)):
        consumer = _consumer(package)
        assert consumer.consume_batch(_batch(*ordered))
        assert consumer.machine.parcel_state.state == "awaiting_pickup"


def test_shared_carrier_source_fields_bind_both_pads(package):
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    # The SAME physical carrier observation: sample, source identity,
    # evidence path/hash, measured attitude, frame origin and native carrier
    # identity/profile are identical across both pad records.
    assert dropoff.sample == pickup.sample
    assert dropoff.source_event_id == pickup.source_event_id
    assert dropoff.source_state_sample_digest == (
        pickup.source_state_sample_digest
    )
    assert dropoff.source_evidence_path == pickup.source_evidence_path
    assert dropoff.source_evidence_sha256 == pickup.source_evidence_sha256
    assert dropoff.source_frame_origin == pickup.source_frame_origin
    assert dropoff.measured_roll_deg == pickup.measured_roll_deg
    assert dropoff.measured_pitch_deg == pickup.measured_pitch_deg
    assert dropoff.measured_yaw_deg == pickup.measured_yaw_deg
    assert dropoff.native_vehicle_id == pickup.native_vehicle_id
    assert dropoff.profile == pickup.profile
    assert dropoff.event_binding == pickup.event_binding
    # The expected per-pad divergence: pad identity/geometry, assessment and
    # the record digest.
    assert dropoff.facility_id == h._DROPOFF
    assert dropoff.pad.facility_id == h._DROPOFF
    assert dropoff.pad != pickup.pad
    assert dropoff.assessment != pickup.assessment
    assert dropoff.observation_digest != pickup.observation_digest


def test_two_independent_positions_in_one_batch_are_rejected(package):
    # The dishonest shape: two independently minted positions at one closed
    # stage.  The records are individually valid, but one batch binds its pad
    # records to the SAME physical carrier observation, so this is rejected
    # before any mutation.
    consumer = _consumer(package)
    two_positions = _batch(
        h._observation(package, at=h._dwell_tick(1)),
        h._observation(package, at=h._dwell_tick(1), facility_id=h._DROPOFF),
    )
    before = consumer.machine.snapshot()
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(two_positions)
    assert consumer.machine.snapshot() == before


def test_one_pad_batch_is_rejected_without_partial_state(package):
    # A single-record batch covers only one declared pad of the closed
    # stage: it is rejected before ANY mutation — regardless of the batch
    # length — and the machine snapshot is exactly unchanged.
    consumer = _consumer(package)
    one_pad = _batch(h._observation(package, at=h._dwell_tick(1)))
    before = consumer.machine.snapshot()
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(one_pad)
    assert consumer.machine.snapshot() == before
    # The batch was not tombstoned: the honest stage batch is still consumed.
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    assert consumer.consume_batch(_batch(pickup, dropoff))
    assert consumer.machine.parcel_state.state == "awaiting_pickup"


def test_seek_check_runs_once_not_per_pad(package):
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    consumer = _consumer(package)
    consumer.consume_batch(_batch(pickup, dropoff))
    pickup2, dropoff2 = _carrier_stage_records(package, at=h._dwell_tick(2))
    # tick 2 batch: only the FIRST record is seek-checked; if the check ran
    # per pad, the second record (same tick) would raise.
    consumer.consume_batch(_batch(pickup2, dropoff2))


def test_skipped_stage_raises_and_rejects_batch(package):
    consumer = _consumer(package)
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    consumer.consume_batch(_batch(pickup, dropoff))
    # A complete two-pad batch that skips stage tick 2: the seek check on the
    # first record rejects the skipped closed stage.
    skipped_pickup, skipped_dropoff = _carrier_stage_records(
        package, at=h._dwell_tick(3)
    )
    with pytest.raises(SeekConsistencyError):
        consumer.consume_batch(_batch(skipped_pickup, skipped_dropoff))


def test_malformed_second_record_leaves_snapshot_unchanged(package):
    consumer = _consumer(package)
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    consumer.consume_batch(_batch(pickup, dropoff))
    before = consumer.machine.snapshot()
    pickup2 = h._observation(package, at=h._dwell_tick(2))
    forged = pickup2.model_copy(update={"aircraft_id": "uav.foreign"})
    dropoff2 = _rebind_stage_observation_pad(
        pickup2,
        pad=facility_landing_pads(package.facilities.require(h._DROPOFF))[0],
        tolerances=h._TOL,
    )
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(_batch(pickup2, dropoff2, forged))
    assert consumer.machine.snapshot() == before


def test_duplicate_batch_is_rejected(package):
    consumer = _consumer(package)
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    assert consumer.consume_batch(_batch(pickup, dropoff))
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(_batch(pickup, dropoff))


def test_foreign_run_batch_is_rejected(package):
    consumer = _consumer(package)
    pickup = h._observation(package, at=h._dwell_tick(1))
    batch = _batch(pickup).model_copy(update={"run_id": h._OTHER_RUN_ID})
    with pytest.raises(NativeParcelRuntimeError):
        consumer.consume_batch(batch)


def test_both_pad_restore_then_next_stage(package):
    consumer = _consumer(package)
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    stage = _batch(pickup, dropoff)
    consumer.consume_batch(stage)
    restored = NativeParcelStageConsumer(h._machine(package))
    restored.machine.restore(consumer.machine.snapshot())
    restored.restore(consumer.snapshot())
    # The next closed stage is consumed exactly once on the restored
    # consumer: the stage tombstone frontier carried over.
    pickup2, dropoff2 = _carrier_stage_records(package, at=h._dwell_tick(2))
    consumer.consume_batch(_batch(pickup2, dropoff2))
    restored.consume_batch(_batch(pickup2, dropoff2))
    assert restored.machine.parcel_state.state == "awaiting_pickup"


def test_repeated_batch_after_restore_is_rejected(package):
    consumer = _consumer(package)
    pickup, dropoff = _carrier_stage_records(package, at=h._dwell_tick(1))
    stage = _batch(pickup, dropoff)
    consumer.consume_batch(stage)
    restored = NativeParcelStageConsumer(h._machine(package))
    restored.machine.restore(consumer.machine.snapshot())
    restored.restore(consumer.snapshot())
    with pytest.raises(NativeParcelRuntimeError):
        restored.consume_batch(_batch(pickup, dropoff))


def test_partial_restore_second_pad_seek_still_accepted(package):
    # After a partial snapshot restore (only the pickup pad absorbed), the
    # machine's own seek path still accepts the second declared pad's record
    # of the exact frontier time; the consumer class is not involved.
    machine = h._machine(package)
    machine.observe(h._observation(package, at=h._dwell_tick(1)))
    machine.restore(machine.snapshot())
    assert machine.check_seek_forward(
        h._observation(
            package, at=h._dwell_tick(1), facility_id=h._DROPOFF
        )
    ) is None
    assert machine.observe(
        h._observation(package, at=h._dwell_tick(1), facility_id=h._DROPOFF)
    ) is True
