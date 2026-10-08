"""Native return observations remain separate from logical acknowledgments."""

import pytest

from aerokernel import KernelError
from aerokernel.testing import FakeLockstepSimulator

MS = 1_000_000


def test_fake_native_exact_stops_and_buffered_early_occurrence():
    payload = {"value": [7]}
    sim = FakeLockstepSimulator(
        20 * MS, early_return_ns=3 * MS, outputs=((3 * MS, payload),)
    )
    payload["value"].append(99)
    first = sim.advance(20 * MS)
    assert first.reached_ns == sim.native_ns == 3 * MS
    assert first.outputs[0].at_ns == 3 * MS
    assert tuple(first.outputs[0].value["value"]) == (7,)
    final = sim.advance(20 * MS)
    assert final.reached_ns == 20 * MS and final.outputs == ()
    sim.apply({"input": 9})
    assert sim.inputs == [(20 * MS, {"input": 9})]
    assert sim.calls == [(0, 20 * MS, 3 * MS), (3 * MS, 20 * MS, 20 * MS)]
    with pytest.raises(KernelError, match="SIMULATOR_STOP"):
        sim.advance(21 * MS)
    with pytest.raises(KernelError, match="SIMULATOR_TIME"):
        sim.advance(20 * MS)
    sim.close()
    sim.close()
    with pytest.raises(KernelError, match="SIMULATOR_CLOSED"):
        sim.advance(40 * MS)
    with pytest.raises(KernelError, match="SIMULATOR_CLOSED"):
        sim.apply(1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stop_granularity_ns": False},
        {"stop_granularity_ns": 0},
        {"early_return_ns": True},
        {"early_return_ns": -1},
        {"outputs": ((False, 1),)},
        {"outputs": ((-1, 1),)},
    ],
)
def test_fake_rejects_invalid_native_coordinates(kwargs):
    with pytest.raises(KernelError):
        FakeLockstepSimulator(**kwargs)


def test_exact_fake_preserves_authored_output_order_at_equal_time():
    sim = FakeLockstepSimulator(outputs=((5, "late"), (3, "first"), (3, "second")))
    result = sim.advance(3)
    assert result.reached_ns == 3
    assert [output.value for output in result.outputs] == ["first", "second"]
    assert sim.advance(5).outputs[0].value == "late"
