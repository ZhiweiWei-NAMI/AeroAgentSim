"""Closed-form geometry and stochastic process checks independent of engines."""

from __future__ import annotations

import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aeroagentsim.packs.arrivals import arrival_times
from aeroagentsim.packs.geometry import (
    area,
    clip,
    contains,
    footprint,
    segment_hits_box,
    union_area,
)
from aeroagentsim.packs.logistics import trajectory_seconds


def test_scheduled_and_poisson_are_reproducible() -> None:
    config = {"mode": "poisson", "rate_per_s": 2.0, "start_ns": 4}
    a = arrival_times(config, 30, random.Random(42))
    assert a == arrival_times(config, 30, random.Random(42))
    assert a != arrival_times(config, 30, random.Random(43))
    assert all(x < y for x, y in zip((4,) + a, a))
    assert arrival_times(
        {"mode": "scheduled", "times_ns": [0, 2, 2]}, 3, random.Random(1)
    ) == (0, 2, 2)
    with pytest.raises(ValueError, match="nondecreasing"):
        arrival_times({"mode": "scheduled", "times_ns": [3, 1]}, 2, random.Random(1))
    with pytest.raises(ValueError, match="positive"):
        arrival_times({**config, "rate_per_s": 0}, 1, random.Random(1))


def test_camera_nadir_yaw_tilt_and_horizon() -> None:
    polygon = footprint((0, 0, 10), (0, 0, 0), 90, 90, 0)
    assert [v for p in polygon for v in p] == pytest.approx(
        [-10, -10, 10, -10, 10, 10, -10, 10]
    )
    assert area(polygon) == pytest.approx(400)
    assert contains(polygon, (0, 0)) and contains(polygon, (10, 0))
    assert not contains(polygon, (11, 0))
    yaw = footprint((0, 0, 10), (90, 0, 0), 90, 60, 0)
    assert max(p[0] for p in yaw) == pytest.approx(10 / math.sqrt(3))
    assert max(p[1] for p in yaw) == pytest.approx(10)
    tilted = footprint((0, 0, 10), (0, 20, 0), 60, 60, 0)
    assert area(tilted) > area(footprint((0, 0, 10), (0, 0, 0), 60, 60, 0))
    for tilt in (45, 60, 90):
        with pytest.raises(ValueError, match="horizon"):
            footprint((0, 0, 10), (0, tilt, 0), 90, 90, 0)


@given(st.floats(min_value=1, max_value=1000, allow_nan=False, allow_infinity=False))
def test_nadir_area_scales_with_height_squared(height: float) -> None:
    assert area(footprint((0, 0, height), (0, 0, 0), 90, 90, 0)) == pytest.approx(
        4 * height**2
    )


def test_continuous_area_union_does_not_double_count() -> None:
    a = ((0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0))
    b = ((1.0, 0.0), (3.0, 0.0), (3.0, 2.0), (1.0, 2.0))
    assert area(clip(a, b)) == pytest.approx(2)
    assert union_area((a, a, b)) == pytest.approx(6)
    diamond = ((0.0, 1.0), (1.0, 0.0), (2.0, 1.0), (1.0, 2.0))
    assert union_area((diamond, a)) == pytest.approx(4)


def test_restriction_crossing_includes_boundary_contact() -> None:
    lower, upper = (1.0, -1.0, 0.0), (2.0, 1.0, 10.0)
    assert segment_hits_box((0, 0, 5), (3, 0, 5), lower, upper)
    assert segment_hits_box((1, 1, 5), (2, 1, 5), lower, upper)
    assert not segment_hits_box((0, 0, 11), (3, 0, 11), lower, upper)
    assert trajectory_seconds(40, 10, 5) == pytest.approx(6)
    assert trajectory_seconds(5, 10, 5) == pytest.approx(2)
