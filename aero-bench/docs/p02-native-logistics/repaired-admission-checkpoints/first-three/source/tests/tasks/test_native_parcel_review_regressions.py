"""Independent regressions for the exact native parcel review; synthetic module inputs."""
import pytest

from tests.tasks import test_native_parcel_runtime as h


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


def fresh(package):
    machine = h._machine(package)
    for tick in (1, 2, 3):
        machine.observe(h._observation(package, at=h._dwell_tick(tick)))
    return machine


def rejected_or_unconfirmed(machine, action, now):
    before = machine.parcel_state
    try:
        outcome = machine.admit_action(action, now=now)
    except h.NativeParcelRuntimeError:
        pass
    else:
        assert outcome.status != "admitted"
    assert machine.parcel_state == before


def test_admission_requires_explicit_time(package):
    machine = fresh(package)
    with pytest.raises(TypeError):
        machine.admit_action(h._action())


def test_old_dwell_cannot_authorize_now100(package):
    rejected_or_unconfirmed(fresh(package), h._action(), h._dwell_tick(100))


def test_future_received_at_cannot_authorize_now3(package):
    rejected_or_unconfirmed(fresh(package), h._action(received_at=h._dwell_tick(500)), h._dwell_tick(3))


@pytest.mark.parametrize("bad", [dict(speed_east=0.3), dict(landed=False, in_air=True), dict(speed_up=0.3)])
def test_bad_sample_resets_then_valid_suffix_qualifies(package, bad):
    machine = h._machine(package)
    machine.observe(h._observation(package, at=h._dwell_tick(1), **bad))
    for tick in (2, 3, 4):
        machine.observe(h._observation(package, at=h._dwell_tick(tick)))
    action = h._action(received_at=h._dwell_tick(4), rx_stage_barrier_digest=f"{4:04d}" * 16)
    outcome = machine.admit_action(action, now=h._dwell_tick(4))
    assert outcome.status == "admitted"
    assert machine.parcel_state.transfers[-1].at == h._dwell_tick(4)


def test_second_pad_at_same_tick_is_absorbed(package):
    machine = h._machine(package)
    assert machine.observe(h._observation(package, at=h._dwell_tick(1)))
    assert machine.observe(h._observation(package, at=h._dwell_tick(1), facility_id=h._DROPOFF))
    assert not machine.observe(h._observation(package, at=h._dwell_tick(1), facility_id=h._DROPOFF))


def test_same_action_key_different_payload_is_rejected(package):
    machine = fresh(package)
    action = h._action()
    assert machine.admit_action(action, now=h._dwell_tick(3)).status == "admitted"
    for tick in (4, 5, 6):
        machine.observe(h._observation(package, at=h._dwell_tick(tick), facility_id=h._DROPOFF))
    with pytest.raises(h.NativeParcelRuntimeError):
        machine.admit_action(h._action(kind="dropoff", action_id=action.action_id,
            received_at=h._dwell_tick(6), rx_stage_barrier_digest=f"{6:04d}" * 16), now=h._dwell_tick(6))
    assert len(machine.parcel_state.transfers) == 1


def test_foreign_principal_cannot_authorize_carrier_action(package):
    with pytest.raises(h.NativeParcelRuntimeError):
        fresh(package).admit_action(h._action(principal_id="uav.foreign"), now=h._dwell_tick(3))


def test_foreign_run_cannot_authorize_action(package):
    with pytest.raises(h.NativeParcelRuntimeError):
        fresh(package).admit_action(h._action(run_id=h._OTHER_RUN_ID), now=h._dwell_tick(3))


def test_restore_pre_pickup_preserves_qualifying_dwell(package):
    original = fresh(package)
    restored = h._machine(package)
    snapshot = h.NativeParcelSnapshot.model_validate_json(original.snapshot().model_dump_json())
    restored.restore(snapshot)
    expected = original.admit_action(h._action(), now=h._dwell_tick(3))
    actual = restored.admit_action(h._action(), now=h._dwell_tick(3))
    assert actual == expected
    assert actual.status == "admitted"


def test_restore_admitted_returns_original_outcome(package):
    original = fresh(package)
    action = h._action()
    expected = original.admit_action(action, now=h._dwell_tick(3))
    restored = h._machine(package)
    restored.restore(h.NativeParcelSnapshot.model_validate_json(original.snapshot().model_dump_json()))
    assert restored.admit_action(action, now=h._dwell_tick(3)) == expected
    assert len(restored.parcel_state.transfers) == 1
