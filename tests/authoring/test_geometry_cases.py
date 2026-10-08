"""Parametrized geometry cases for aeroagentsim.authoring.scene helpers."""

import pytest

from aeroagentsim.authoring.scene import _bounds, _contains, _segment


@pytest.mark.parametrize(
    ("a", "b", "box", "expected"),
    [
        # WGS84 rectangle order is west, south, east, north.
        pytest.param(
            (-2.0, 0.0),
            (2.0, 0.0),
            [-1.0, -1.0, 1.0, 1.0],
            True,
            id="crossing-endpoints-outside",
        ),
        pytest.param(
            (1.0, 1.0),
            (2.0, 2.0),
            [-1.0, -1.0, 1.0, 1.0],
            True,
            id="tangent-at-corner",
        ),
        pytest.param(
            (-2.0, 2.0),
            (2.0, 2.0),
            [-1.0, -1.0, 1.0, 1.0],
            False,
            id="parallel-disjoint-above",
        ),
        pytest.param(
            (0.0, 0.0),
            (0.5, 0.5),
            [-1.0, -1.0, 1.0, 1.0],
            True,
            id="segment-fully-inside",
        ),
    ],
)
def test_segment_inclusive_clipping(a, b, box, expected):
    assert _segment(a, b, box) is expected


@pytest.mark.parametrize(
    ("points", "point", "expected"),
    [
        pytest.param(
            [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)],
            (2.0, 2.0),
            True,
            id="point-inside-ring",
        ),
        pytest.param(
            [
                (0.0, 0.0),
                (6.0, 0.0),
                (6.0, 6.0),
                (3.0, 6.0),
                (3.0, 3.0),
                (0.0, 3.0),
                (0.0, 0.0),
            ],
            (1.5, 1.5),
            True,
            id="point-enclosed-by-concave-ring",
        ),
        pytest.param(
            [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0), (0.0, 0.0)],
            (5.0, 5.0),
            False,
            id="point-outside-ring",
        ),
    ],
)
def test_contains_closed_ring_ray_cast(points, point, expected):
    assert _contains(points, point) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(
            [0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 2.0, 3.0], id="valid-bbox-floats"
        ),
        pytest.param([0, 1, 2, 3], [0.0, 1.0, 2.0, 3.0], id="valid-bbox-ints-coerced"),
        pytest.param(
            [-1.0, -1.0, 1.0, 1.0],
            [-1.0, -1.0, 1.0, 1.0],
            id="valid-bbox-negative",
        ),
    ],
)
def test_bounds_valid(value, expected):
    result = _bounds(value)
    assert result == expected
    assert all(type(coord) is float for coord in result)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([0.0, float("nan"), 2.0, 3.0], id="nan-coordinate"),
        pytest.param(None, id="null-value"),
        pytest.param(True, id="boolean-value"),
        pytest.param([True, True, True, True], id="boolean-coordinates"),
        pytest.param([1.0, 0.0, 0.0, 1.0], id="reversed-bbox"),
    ],
)
def test_bounds_invalid(value):
    with pytest.raises((TypeError, ValueError)):
        _bounds(value)
