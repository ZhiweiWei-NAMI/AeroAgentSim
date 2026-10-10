"""Canonical SHA-256 identity for displayed roadbed and walkbed polygons."""

from __future__ import annotations

import hashlib
import math
import numbers
import struct
from collections.abc import Mapping, Sequence
from typing import Any


_DOMAIN = b"AERO-BENCH-DISPLAY-SURFACE\0v1\0"
_UINT32_MAX = 0xFFFF_FFFF


def _count(value: int, label: str) -> None:
    if value < 0 or value > _UINT32_MAX:
        raise ValueError(f"{label} count must fit in an unsigned 32-bit integer")


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TypeError(f"{label} must contain exactly [x, z]")
    coordinates: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, numbers.Real):
            raise TypeError(f"{label} coordinates must be finite numbers")
        try:
            coordinate = float(item)
        except (OverflowError, ValueError) as error:
            raise TypeError(f"{label} coordinates must be finite numbers") from error
        if not math.isfinite(coordinate):
            raise TypeError(f"{label} coordinates must be finite numbers")
        coordinates.append(0.0 if coordinate == 0.0 else coordinate)
    return coordinates[0], coordinates[1]


def _array(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be an array")
    return value


def _ring(value: Any, label: str) -> Sequence[Any]:
    points = _array(value, label)
    if len(points) < 3:
        raise ValueError(f"{label} must contain at least three points")
    _count(len(points), f"{label} point")
    for index, point in enumerate(points):
        _point(point, f"{label}[{index}]")
    return points


def _polygons(value: Any, label: str) -> Sequence[Mapping[str, Any]]:
    polygons = _array(value, label)
    _count(len(polygons), f"{label} polygon")
    for index, polygon in enumerate(polygons):
        if not isinstance(polygon, Mapping):
            raise TypeError(f"{label}[{index}] must be a polygon object")
        _ring(polygon.get("outline"), f"{label}[{index}].outline")
        holes = _array(polygon.get("holes"), f"{label}[{index}].holes")
        _count(len(holes), f"{label}[{index}] hole")
        for hole_index, hole in enumerate(holes):
            _ring(hole, f"{label}[{index}].holes[{hole_index}]")
    return polygons


def _write_ring(stream: bytearray, points: Sequence[Any]) -> None:
    stream.extend(struct.pack("<I", len(points)))
    for index, point in enumerate(points):
        x, z = _point(point, f"surface point {index}")
        stream.extend(struct.pack("<dd", x, z))


def _write_polygons(stream: bytearray, polygons: Sequence[Mapping[str, Any]]) -> None:
    stream.extend(struct.pack("<I", len(polygons)))
    for polygon in polygons:
        outline = polygon["outline"]
        holes = polygon["holes"]
        _write_ring(stream, outline)
        stream.extend(struct.pack("<I", len(holes)))
        for hole in holes:
            _write_ring(stream, hole)


def displayed_surface_sha256(roadbed: Any, walkbed: Any) -> str:
    """Return lowercase SHA-256 for ordered roadbed and walkbed x/z polygons.

    The binary stream is the domain/version tag followed by two polygon lists
    (roadbed then walkbed). All counts are uint32 little-endian; each ring is
    a point count followed by x/z Float64 little-endian pairs. Input ordering
    is preserved and negative zero is canonicalized to positive zero.
    """
    checked_roadbed = _polygons(roadbed, "roadbed")
    checked_walkbed = _polygons(walkbed, "walkbed")
    stream = bytearray(_DOMAIN)
    _write_polygons(stream, checked_roadbed)
    _write_polygons(stream, checked_walkbed)
    return hashlib.sha256(stream).hexdigest()
