"""Delivery-clock unit inputs; they are not native network or formal evidence."""

from types import SimpleNamespace

import pytest

from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.inspection.verifier import _validate_time_series


def _delivery(tick: int, sim_time_ns: int):
    return SimpleNamespace(
        delivered_at=SimulationTime(tick=tick, sim_time_ns=sim_time_ns)
    )


def test_completion_ordering_validates_delivery_records_not_bare_times():
    records = (_delivery(514, 256_510_360_090), _delivery(514, 256_510_386_355))
    _validate_time_series(records, "delivery completion", time_field="delivered_at")


@pytest.mark.parametrize(
    "second, message",
    [
        (_delivery(513, 256_510_386_355), "tick moved backwards"),
        (_delivery(514, 256_510_350_000), "simulation time moved backwards"),
    ],
)
def test_completion_ordering_rejects_backwards_delivery(second, message):
    with pytest.raises(ValueError, match=message):
        _validate_time_series(
            (_delivery(514, 256_510_360_090), second),
            "delivery completion",
            time_field="delivered_at",
        )


def test_completion_ordering_rejects_a_missing_first_clock():
    with pytest.raises(ValueError, match="record has no simulation time"):
        _validate_time_series(
            (SimpleNamespace(delivered_at=None),),
            "delivery completion",
            time_field="delivered_at",
        )
