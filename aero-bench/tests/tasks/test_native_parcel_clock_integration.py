"""Actual-package regressions for the independent integration clock review."""
import pytest

from tests.tasks import test_native_parcel_runtime as h


@pytest.fixture
def package():
    return h.lower_logistics_task_package(h._package_document())


def ready(package):
    machine = h._machine(package)
    for tick in (1, 2, 3):
        machine.observe(h._observation(package, at=h._dwell_tick(tick)))
    return machine


def test_earlier_rx_tick_with_future_nanoseconds_is_rejected(package):
    machine = ready(package)
    before = machine.snapshot()
    action = h._action(received_at=h.SimulationTime(tick=2, sim_time_ns=100_000_000_000))
    with pytest.raises(h.NativeParcelRuntimeError):
        machine.admit_action(action, now=h._dwell_tick(3))
    assert machine.snapshot() == before


@pytest.mark.parametrize("facility", [h._PICKUP, h._DROPOFF])
def test_same_tick_changed_ns_never_creates_an_observation_phase(package, facility):
    machine = h._machine(package)
    machine.observe(h._observation(package, at=h._dwell_tick(1)))
    before = machine.snapshot()
    with pytest.raises(h.NativeParcelRuntimeError):
        machine.observe(h._observation(package, facility_id=facility,
            at=h.SimulationTime(tick=1, sim_time_ns=2_000_000_000)))
    assert machine.snapshot() == before


def test_later_tick_with_backwards_ns_is_rejected(package):
    machine = h._machine(package)
    machine.observe(h._observation(package, at=h._dwell_tick(1)))
    before = machine.snapshot()
    with pytest.raises(h.NativeParcelRuntimeError):
        machine.observe(h._observation(package,
            at=h.SimulationTime(tick=2, sim_time_ns=500_000_000)))
    assert machine.snapshot() == before


@pytest.mark.parametrize("first,second", [(h._PICKUP,h._DROPOFF),(h._DROPOFF,h._PICKUP)])
def test_restore_partial_stage_can_consume_unseen_second_pad(package, first, second):
    machine = h._machine(package)
    one = h._observation(package, at=h._dwell_tick(1), facility_id=first)
    machine.observe(one)
    restored = h._machine(package)
    restored.restore(h.NativeParcelSnapshot.model_validate_json(machine.snapshot().model_dump_json()))
    two = h._observation(package, at=h._dwell_tick(1), facility_id=second)
    restored.check_seek_forward(two)
    assert restored.observe(two)
    frontiers = dict(restored.snapshot().facility_frontiers)
    assert frontiers[first] == h._dwell_tick(1)
    assert frontiers[second] == h._dwell_tick(1)


def test_same_pad_same_time_does_not_count_twice(package):
    machine = h._machine(package)
    one = h._observation(package, at=h._dwell_tick(1))
    assert machine.observe(one)
    before = machine.snapshot()
    assert not machine.observe(one)
    assert machine.snapshot() == before
    with pytest.raises(h.SeekConsistencyError):
        machine.check_seek_forward(one)


def test_seek_still_rejects_tick_jump(package):
    machine = h._machine(package)
    machine.observe(h._observation(package, at=h._dwell_tick(1)))
    with pytest.raises(h.SeekConsistencyError):
        machine.check_seek_forward(h._observation(package, at=h._dwell_tick(3)))
