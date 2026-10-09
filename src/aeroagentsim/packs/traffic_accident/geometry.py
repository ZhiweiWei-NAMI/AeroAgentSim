"""Local ENU road geometry, arc length and complete rectangle occupancy."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from aeroagentsim.packs.common import finite, vec

Point = tuple[float, float, float]


@dataclass(frozen=True)
class Pose:
    position: Point
    heading: float  # ENU yaw about up, forward along east at zero

    @property
    def quaternion(self) -> list[float]:
        return [0.0, 0.0, math.sin(self.heading / 2), math.cos(self.heading / 2)]


class Polyline:
    """Nondegenerate planar metre polyline; no guessed missing coordinates."""

    def __init__(self, points: Sequence[Sequence[float]]) -> None:
        self.points = tuple(vec(p) for p in points)
        if len(self.points) < 2 or any(p[2] != self.points[0][2] for p in self.points):
            raise ValueError("road polyline: at least two coplanar ENU points required")
        self.distances = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.distances.append(self.distances[-1] + math.dist(a, b))
        self.length = self.distances[-1]
        if self.length <= 1e-6:
            raise ValueError("road polyline: positive arc length required")

    def at(self, progress: float) -> Pose:
        s = finite(progress, "route progress", 0)
        if s > self.length + 1e-9:
            raise ValueError("route progress exceeds polyline length")
        for i, (a, b) in enumerate(zip(self.points, self.points[1:])):
            length = self.distances[i + 1] - self.distances[i]
            if length <= 1e-6:
                continue
            if s <= self.distances[i + 1] + 1e-9:
                ratio = min(1.0, (s - self.distances[i]) / length)
                coordinates = [a[j] + (b[j] - a[j]) * ratio for j in range(3)]
                return Pose(
                    (coordinates[0], coordinates[1], coordinates[2]),
                    math.atan2(b[1] - a[1], b[0] - a[0]),
                )
        raise ValueError("route has no terminal segment")


def overlap(a: Pose, b: Pose, clearance: float = 0.55) -> bool:
    """Legacy 4.5 x 1.8 m separating-axis footprints in the ENU plane."""
    gap = finite(clearance, "occupancy clearance", 0)
    # Each 4.5 x 1.8 m rectangle projects at most 2.25 + 0.9 m
    # onto either world axis, at every heading. This conservative broad phase
    # avoids SAT work for distant cars; the exact existing test handles contacts.
    reach = 2 * (2.25 + 0.9) + gap
    if (
        abs(b.position[0] - a.position[0]) > reach
        or abs(b.position[1] - a.position[1]) > reach
    ):
        return False
    af = (math.cos(a.heading), math.sin(a.heading))
    ar = (-af[1], af[0])
    bf = (math.cos(b.heading), math.sin(b.heading))
    br = (-bf[1], bf[0])
    delta = (b.position[0] - a.position[0], b.position[1] - a.position[1])
    for axis in (af, ar, bf, br):
        center = abs(sum(delta[i] * axis[i] for i in range(2)))
        ra = 2.25 * abs(sum(af[i] * axis[i] for i in range(2)))
        ra += 0.9 * abs(sum(ar[i] * axis[i] for i in range(2)))
        rb = 2.25 * abs(sum(bf[i] * axis[i] for i in range(2)))
        rb += 0.9 * abs(sum(br[i] * axis[i] for i in range(2)))
        if center >= ra + rb + gap:
            return False
    return True


def blockers(
    identity: str, candidate: Pose, poses: dict[str, Pose], clearance: float
) -> tuple[str, ...]:
    return tuple(
        other
        for other in sorted(poses)
        if other != identity and overlap(candidate, poses[other], clearance)
    )
