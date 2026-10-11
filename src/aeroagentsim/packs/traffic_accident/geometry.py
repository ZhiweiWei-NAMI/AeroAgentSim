"""Local ENU road geometry, arc length and complete rectangle occupancy."""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Iterator, Mapping, Sequence
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
        self._segments = [
            i
            for i in range(len(self.points) - 1)
            if self.distances[i + 1] - self.distances[i] > 1e-6
        ]
        # Keep the original boundary tolerance, including its floating-point
        # addition, and exclude exactly the same degenerate segments.
        self._ends = [self.distances[i + 1] + 1e-9 for i in self._segments]

    def at(self, progress: float) -> Pose:
        s = finite(progress, "route progress", 0)
        if s > self.length + 1e-9:
            raise ValueError("route progress exceeds polyline length")
        position = bisect_left(self._ends, s)
        if position < len(self._segments):
            i = self._segments[position]
            a, b = self.points[i], self.points[i + 1]
            length = self.distances[i + 1] - self.distances[i]
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


class PoseIndex(Mapping[str, Pose]):
    """Mutable broad phase for the same complete, ordered occupancy query.

    Only distant footprints are excluded. Every candidate still passes through
    ``overlap``; updating an actor immediately updates subsequent fleet queries.
    """

    _cell_size = 8.0

    def __init__(self, poses: Mapping[str, Pose]) -> None:
        self._poses: dict[str, Pose] = {}
        self._cells: dict[tuple[int, int], set[str]] = {}
        for identity, pose in poses.items():
            self[identity] = pose

    def _cell(self, pose: Pose) -> tuple[int, int]:
        return (
            math.floor(pose.position[0] / self._cell_size),
            math.floor(pose.position[1] / self._cell_size),
        )

    def __setitem__(self, identity: str, pose: Pose) -> None:
        previous = self._poses.get(identity)
        cell = self._cell(pose)
        if previous is not None:
            old = self._cell(previous)
            if old != cell:
                members = self._cells[old]
                members.remove(identity)
                if not members:
                    del self._cells[old]
        self._poses[identity] = pose
        self._cells.setdefault(cell, set()).add(identity)

    def __getitem__(self, identity: str) -> Pose:
        return self._poses[identity]

    def __iter__(self) -> Iterator[str]:
        return iter(self._poses)

    def __len__(self) -> int:
        return len(self._poses)

    def candidates(self, pose: Pose, clearance: float) -> Iterator[str]:
        reach = 2 * (2.25 + 0.9) + finite(clearance, "occupancy clearance", 0)
        x, y = pose.position[:2]
        xs = range(
            math.floor((x - reach) / self._cell_size),
            math.floor((x + reach) / self._cell_size) + 1,
        )
        ys = range(
            math.floor((y - reach) / self._cell_size),
            math.floor((y + reach) / self._cell_size) + 1,
        )
        for cx in xs:
            for cy in ys:
                yield from self._cells.get((cx, cy), ())


def blockers(
    identity: str, candidate: Pose, poses: Mapping[str, Pose], clearance: float
) -> tuple[str, ...]:
    identities = (
        poses.candidates(candidate, clearance)
        if isinstance(poses, PoseIndex)
        else poses
    )
    return tuple(
        other
        for other in sorted(identities)
        if other != identity and overlap(candidate, poses[other], clearance)
    )
