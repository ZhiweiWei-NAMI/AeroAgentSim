"""Pinhole ground footprints and convex clipping; no imagery is synthesized."""

from __future__ import annotations

import math
from itertools import pairwise

from .common import finite, vec

Point = tuple[float, float]
Polygon = tuple[Point, ...]


def footprint(
    position: tuple[float, float, float],
    attitude_deg: tuple[float, float, float],
    horizontal_fov_deg: float,
    vertical_fov_deg: float,
    ground_z_m: float,
) -> Polygon:
    """Nadir camera rays rotated Rz(yaw) Ry(tilt) Rx(roll), all ENU degrees.

    Tilt zero looks down; horizon-crossing/above-ground invalidity is explicit.
    This bounded model has a flat ground plane and no occlusion or lens distortion.
    """
    x, y, z = vec(position)
    yaw, tilt, roll = (math.radians(v) for v in vec(attitude_deg))
    hf, vf = (
        finite(horizontal_fov_deg, "horizontal FOV"),
        finite(vertical_fov_deg, "vertical FOV"),
    )
    ground = finite(ground_z_m, "ground height")
    if not 0 < hf < 180 or not 0 < vf < 180 or z <= ground:
        raise ValueError("camera: FOV in (0,180), camera above ground required")
    tx, ty = math.tan(math.radians(hf / 2)), math.tan(math.radians(vf / 2))
    points: list[Point] = []
    for a, b in ((-tx, -ty), (tx, -ty), (tx, ty), (-tx, ty)):
        # Camera's optical axis is -Z at nadir.
        ry, rz = (
            b * math.cos(roll) + math.sin(roll),
            b * math.sin(roll) - math.cos(roll),
        )
        rx, rz = (
            a * math.cos(tilt) + rz * math.sin(tilt),
            -a * math.sin(tilt) + rz * math.cos(tilt),
        )
        rx, ry = (
            rx * math.cos(yaw) - ry * math.sin(yaw),
            rx * math.sin(yaw) + ry * math.cos(yaw),
        )
        if rz >= -1e-12:
            raise ValueError(
                "camera footprint intersects horizon; bounded footprint unavailable"
            )
        scale = (ground - z) / rz
        points.append((x + scale * rx, y + scale * ry))
    return tuple(points)


def contains(polygon: Polygon, point: Point) -> bool:
    return all(
        (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0]) >= -1e-9
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
    )


def area(polygon: Polygon) -> float:
    return (
        abs(
            sum(
                a[0] * b[1] - a[1] * b[0]
                for a, b in zip(polygon, polygon[1:] + polygon[:1])
            )
        )
        / 2
    )


def clip(subject: Polygon, window: Polygon) -> Polygon:
    """Intersect two counterclockwise convex polygons (Sutherland–Hodgman)."""
    output = list(subject)
    for a, b in zip(window, window[1:] + window[:1]):
        old, output = output, []
        if not old:
            break

        def side(p: Point, a: Point = a, b: Point = b) -> float:
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        previous = old[-1]
        for current in old:
            sp, sc = side(previous), side(current)
            if (sp >= 0) != (sc >= 0):
                t = sp / (sp - sc)
                output.append(
                    (
                        previous[0] + t * (current[0] - previous[0]),
                        previous[1] + t * (current[1] - previous[1]),
                    )
                )
            if sc >= 0:
                output.append(current)
            previous = current
    return tuple(output)


def union_area(polygons: tuple[Polygon, ...]) -> float:
    """Exact slab integration of a union of convex polygons, without pixel grids."""
    edges = [
        (a, b)
        for polygon in polygons
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
    ]
    cuts = {p[0] for polygon in polygons for p in polygon}
    for i, (a, b) in enumerate(edges):
        for c, d in edges[i + 1 :]:
            ux, uy, vx, vy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
            determinant = ux * vy - uy * vx
            if abs(determinant) <= 1e-12:
                continue
            t = ((c[0] - a[0]) * vy - (c[1] - a[1]) * vx) / determinant
            s = ((c[0] - a[0]) * uy - (c[1] - a[1]) * ux) / determinant
            if 0 < t < 1 and 0 < s < 1:
                cuts.add(a[0] + t * ux)

    def height(x: float) -> float:
        spans: list[tuple[float, float]] = []
        for polygon in polygons:
            ys = [
                a[1] + (x - a[0]) / (b[0] - a[0]) * (b[1] - a[1])
                for a, b in zip(polygon, polygon[1:] + polygon[:1])
                if min(a[0], b[0]) < x < max(a[0], b[0])
            ]
            if ys:
                spans.append((min(ys), max(ys)))
        total, right = 0.0, -math.inf
        for a, b in sorted(spans):
            total += max(0.0, b - max(a, right))
            right = max(right, b)
        return total

    boundaries = sorted(cuts)
    # A slab's union height is linear after all edge crossings are split.
    # Interior quadrature avoids ambiguous vertical edges at slab boundaries.
    return sum(
        (b - a) * (height(a + (b - a) / 3) + height(a + 2 * (b - a) / 3)) / 2
        for a, b in pairwise(boundaries)
    )


def segment_hits_box(
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    lower: tuple[float, float, float],
    upper: tuple[float, float, float],
) -> bool:
    """Closed 3D slab intersection, including endpoint and boundary contact."""
    lo, hi = 0.0, 1.0
    for a, b, minimum, maximum in zip(start, end, lower, upper):
        if minimum > maximum:
            raise ValueError("restriction box: lower exceeds upper")
        delta = b - a
        if delta == 0:
            if a < minimum or a > maximum:
                return False
        else:
            u, v = (minimum - a) / delta, (maximum - a) / delta
            lo, hi = max(lo, min(u, v)), min(hi, max(u, v))
            if lo > hi:
                return False
    return True
