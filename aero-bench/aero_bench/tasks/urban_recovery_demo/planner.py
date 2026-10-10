"""Deterministic, Agent-owned recovery path planning for the urban demo.

The planner consumes only the typed public constraints supplied to a UAV Agent.
It has no provider access and does not emit runtime events or telemetry.  A caller
must submit the returned waypoints through its granted flight tool and wait for
physical completion before requesting the next action.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from aero_bench.tasks.urban_recovery_demo.contracts import DemoVector


class RecoveryPlanningError(ValueError):
    """A path cannot be proven to avoid the supplied constraints."""


Point2 = tuple[float, float]
Polygon = tuple[Point2, ...]


@dataclass(frozen=True, slots=True)
class _Grid:
    min_x: int
    max_x: int
    min_y: int
    max_y: int
    cell_size_m: float

    def point(self, node: tuple[int, int]) -> Point2:
        return (node[0] * self.cell_size_m, node[1] * self.cell_size_m)


_EPSILON = 1e-9
_NEIGHBORS: tuple[tuple[int, int], ...] = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


def _finite_point(value: Sequence[float], *, label: str) -> Point2:
    if len(value) != 2:
        raise RecoveryPlanningError(f"{label} must contain two coordinates")
    point = (float(value[0]), float(value[1]))
    if not all(math.isfinite(component) for component in point):
        raise RecoveryPlanningError(f"{label} must be finite")
    return point


def _cross(a: Point2, b: Point2, c: Point2) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_segment_distance(point: Point2, start: Point2, end: Point2) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= _EPSILON:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    projection = min(1.0, max(0.0, projection))
    nearest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _inside_polygon(point: Point2, polygon: Polygon) -> bool:
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if _point_segment_distance(point, start, end) <= _EPSILON:
            return True
        if (start[1] > point[1]) != (end[1] > point[1]):
            crossing_x = (end[0] - start[0]) * (point[1] - start[1]) / (
                end[1] - start[1]
            ) + start[0]
            if point[0] < crossing_x:
                inside = not inside
    return inside


def _segment_clear(start: Point2, end: Point2, polygons: Sequence[Polygon], margin: float) -> bool:
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    samples = max(1, math.ceil(length / max(margin * 0.5, 0.25)))
    for index in range(samples + 1):
        fraction = index / samples
        point = (
            start[0] + (end[0] - start[0]) * fraction,
            start[1] + (end[1] - start[1]) * fraction,
        )
        if any(
            _inside_polygon(point, polygon)
            or any(
                _point_segment_distance(point, polygon[i], polygon[(i + 1) % len(polygon)])
                <= margin
                for i in range(len(polygon))
            )
            for polygon in polygons
        ):
            return False
    return True


def _normalise_polygons(polygons: Iterable[Sequence[Sequence[float]]]) -> tuple[Polygon, ...]:
    result: list[Polygon] = []
    for index, raw_polygon in enumerate(polygons):
        polygon = tuple(_finite_point(point, label=f"constraint polygon {index}") for point in raw_polygon)
        if len(polygon) < 3:
            raise RecoveryPlanningError(f"constraint polygon {index} has fewer than three points")
        result.append(polygon)
    return tuple(result)


class RecoveryPlanner:
    """Run a bounded deterministic A* search over an ENU horizontal grid."""

    def __init__(
        self,
        *,
        vehicle_radius_m: float,
        obstacle_margin_m: float,
        cell_size_m: float = 5.0,
        bounds: tuple[float, float, float, float] = (-500.0, 500.0, -500.0, 500.0),
        max_expansions: int = 200_000,
    ) -> None:
        values = (vehicle_radius_m, obstacle_margin_m, cell_size_m, *bounds)
        if not all(math.isfinite(float(value)) for value in values):
            raise RecoveryPlanningError("planner parameters must be finite")
        if vehicle_radius_m <= 0 or obstacle_margin_m <= 0 or cell_size_m <= 0:
            raise RecoveryPlanningError("planner radius, margin, and cell size must be positive")
        if bounds[0] >= bounds[1] or bounds[2] >= bounds[3]:
            raise RecoveryPlanningError("planner bounds must be ordered")
        if max_expansions <= 0:
            raise RecoveryPlanningError("planner max_expansions must be positive")
        self._clearance_m = vehicle_radius_m + obstacle_margin_m
        self._cell_size_m = cell_size_m
        self._bounds = bounds
        self._max_expansions = max_expansions

    def plan(
        self,
        *,
        start_enu_m: Sequence[float],
        goal_enu_m: Sequence[float],
        forbidden_polygons: Iterable[Sequence[Sequence[float]]],
        altitude_m: float,
        allow_blocked_start: bool = False,
    ) -> tuple[DemoVector, ...]:
        """Return a bounded path; emergency recovery may explicitly exit a blocked start.

        The default remains fail-closed for blocked starts.  ``allow_blocked_start``
        is reserved for a provider-confirmed incident: the first emitted segment
        leaves the current clearance region, while every later search edge uses
        the normal inflated-obstacle checks.
        """
        start = _finite_point(start_enu_m, label="planner start")
        goal = _finite_point(goal_enu_m, label="planner goal")
        if not math.isfinite(float(altitude_m)) or altitude_m <= 0:
            raise RecoveryPlanningError("planner altitude must be positive and finite")
        polygons = _normalise_polygons(forbidden_polygons)
        grid = _Grid(
            min_x=math.ceil(self._bounds[0] / self._cell_size_m),
            max_x=math.floor(self._bounds[1] / self._cell_size_m),
            min_y=math.ceil(self._bounds[2] / self._cell_size_m),
            max_y=math.floor(self._bounds[3] / self._cell_size_m),
            cell_size_m=self._cell_size_m,
        )
        start_node = (round(start[0] / self._cell_size_m), round(start[1] / self._cell_size_m))
        goal_node = (round(goal[0] / self._cell_size_m), round(goal[1] / self._cell_size_m))
        for label, node in (("start", start_node), ("goal", goal_node)):
            if not (grid.min_x <= node[0] <= grid.max_x and grid.min_y <= node[1] <= grid.max_y):
                raise RecoveryPlanningError(f"planner {label} is outside ENU bounds")

        def blocked(node: tuple[int, int]) -> bool:
            point = grid.point(node)
            return any(
                _inside_polygon(point, polygon)
                or any(
                    _point_segment_distance(point, polygon[i], polygon[(i + 1) % len(polygon)])
                    <= self._clearance_m
                    for i in range(len(polygon))
                )
                for polygon in polygons
            )

        start_blocked = blocked(start_node)
        if start_blocked and not allow_blocked_start:
            raise RecoveryPlanningError("planner start is inside a forbidden clearance")
        if blocked(goal_node):
            raise RecoveryPlanningError("planner goal is inside a forbidden clearance")

        # A recovery alert may arrive after the vehicle has already crossed the
        # boundary.  In that explicitly opted-in case the first segment exits
        # the current clearance region; all subsequent A* edges remain strict.
        search_start_node = start_node
        prefix: list[Point2] = []
        if start_blocked:
            candidates = [
                node
                for x in range(grid.min_x, grid.max_x + 1)
                for y in range(grid.min_y, grid.max_y + 1)
                for node in ((x, y),)
                if not blocked(node)
            ]
            if not candidates:
                raise RecoveryPlanningError("planner has no clear recovery exit")
            search_start_node = min(
                candidates,
                key=lambda node: (
                    math.hypot(node[0] - start_node[0], node[1] - start_node[1]),
                    node[0],
                    node[1],
                ),
            )
            prefix = [start, grid.point(search_start_node)]

        def heuristic(node: tuple[int, int]) -> float:
            return math.hypot(node[0] - goal_node[0], node[1] - goal_node[1])

        frontier: list[tuple[float, float, tuple[int, int]]] = [(heuristic(search_start_node), 0.0, search_start_node)]
        costs = {search_start_node: 0.0}
        previous: dict[tuple[int, int], tuple[int, int]] = {}
        expansions = 0
        while frontier:
            _, cost, current = heapq.heappop(frontier)
            if cost != costs.get(current):
                continue
            expansions += 1
            if expansions > self._max_expansions:
                raise RecoveryPlanningError("planner expansion bound was exhausted")
            if current == goal_node:
                nodes = [current]
                while nodes[-1] != search_start_node:
                    nodes.append(previous[nodes[-1]])
                nodes.reverse()
                points = [grid.point(node) for node in nodes]
                if start_blocked:
                    points = prefix + points[1:]
                else:
                    points[0] = start
                points[-1] = goal
                return tuple(
                    DemoVector(x=point[0], y=point[1], z=float(altitude_m))
                    for point in points
                )
            for delta_x, delta_y in _NEIGHBORS:
                neighbour = (current[0] + delta_x, current[1] + delta_y)
                if not (
                    grid.min_x <= neighbour[0] <= grid.max_x
                    and grid.min_y <= neighbour[1] <= grid.max_y
                ):
                    continue
                current_point = grid.point(current)
                neighbour_point = grid.point(neighbour)
                if not _segment_clear(current_point, neighbour_point, polygons, self._clearance_m):
                    continue
                next_cost = cost + math.hypot(delta_x, delta_y)
                if next_cost >= costs.get(neighbour, math.inf):
                    continue
                costs[neighbour] = next_cost
                previous[neighbour] = current
                heapq.heappush(frontier, (next_cost + heuristic(neighbour), next_cost, neighbour))
        raise RecoveryPlanningError("no collision-free recovery path exists")


__all__ = ["RecoveryPlanner", "RecoveryPlanningError"]
