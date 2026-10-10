"""Low-altitude survey planners: area coverage, asset survey and loop patrol.

All three planners implement the ``PathPlanning`` protocol: ``plan(snapshot,
start, goal)`` returns a full waypoint tuple whose last point is exactly
``goal``.  Waypoints come from explicit configuration (never inferred state)
and every full segment is validated against the active no-fly zones of the
committed snapshot; connector legs that would cross a zone are routed with the
built-in ``CorridorAstar``.
"""

from __future__ import annotations

import math
from itertools import product
from typing import Any, Sequence

from aeroagentsim.algorithms.builtins import CorridorAstar
from aeroagentsim.algorithms.protocols import PathPlanning, Point, Snapshot
from aeroagentsim.packs.common import distance, finite
from aeroagentsim.packs.geometry import segment_hits_box


def cruise_plan(
    planner: PathPlanning,
    snapshot: Snapshot,
    start: Point,
    goal: Point,
    altitude: float,
) -> tuple[Point, ...]:
    """Leave/approach a site vertically and traverse above the authored roof ceiling."""
    if start == goal:
        return (goal,)
    height = max(finite(altitude, "cruise altitude", 0), start[2], goal[2])
    departure = (start[0], start[1], height)
    arrival = (goal[0], goal[1], height)
    points: list[Point] = []
    prior = start
    for target in (departure, arrival, goal):
        if prior != target:
            points.extend(planner.plan(snapshot, prior, target))
        prior = target
    return tuple(points)


class NoFlyRouteUnavailable(ValueError):
    """A forbidden destination or bounded egress search requires safe waiting."""


def containing_no_fly(snapshot: Snapshot, point: Point) -> tuple[str, ...]:
    return tuple(
        z.id
        for z in snapshot.zones
        if z.active
        and z.kind == "no_fly"
        and all(z.lower[i] <= point[i] <= z.upper[i] for i in range(3))
    )


def route_avoids_no_fly(
    snapshot: Snapshot,
    start: Point,
    points: tuple[Point, ...],
    *,
    initial_exit: bool = False,
) -> bool:
    """Permit only a straight first exit from boxes containing this start.

    A convex box cannot be re-entered on that straight segment. Its endpoint
    must be outside EVERY start-containing box; all later segments and all
    initially noncontaining boxes retain the normal inclusive crossing guard.
    """
    containing = set(containing_no_fly(snapshot, start)) if initial_exit else set()
    exit_ok = bool(points) and not containing_no_fly(snapshot, points[0])
    return not any(
        z.active
        and z.kind == "no_fly"
        and segment_hits_box(a, b, z.lower, z.upper)
        and not (index == 0 and exit_ok and z.id in containing)
        for index, (a, b) in enumerate(zip((start,) + points, points))
        for z in snapshot.zones
    )


def plan_with_egress(
    planner: PathPlanning,
    snapshot: Snapshot,
    start: Point,
    goal: Point,
    altitude: float | None = None,
    clearance_m: float = 1.0,
) -> tuple[Point, ...]:
    """Plan unchanged outside starts; prepend one bounded emergency exit inside.

    The original planner operates on the original snapshot from a safe exit,
    not on globally deactivated zones. No route is promised for every possible
    obstacle arrangement: exhausted candidates explicitly request safe waiting.
    """
    if containing_no_fly(snapshot, goal):
        raise NoFlyRouteUnavailable("destination is inside an active no-fly zone")

    def onward(point: Point) -> tuple[Point, ...]:
        return (
            planner.plan(snapshot, point, goal)
            if altitude is None
            else cruise_plan(planner, snapshot, point, goal, altitude)
        )

    containing = set(containing_no_fly(snapshot, start))
    if not containing:
        points = onward(start)
        if not route_avoids_no_fly(snapshot, start, points):
            raise NoFlyRouteUnavailable("path proposal crosses an active no-fly zone")
        return points
    clearance = finite(clearance_m, "egress clearance", 1e-6)
    boxes = tuple(z for z in snapshot.zones if z.id in containing)
    lower = tuple(min(z.lower[i] for z in boxes) - clearance for i in range(3))
    upper = tuple(max(z.upper[i] for z in boxes) + clearance for i in range(3))
    height = max(start[2], altitude or start[2])
    above = (start[0], start[1], max(upper[2], height))
    candidates: set[Point] = {above}
    for axis in range(3):
        for edge in (lower[axis], upper[axis]):
            point = list(start)
            point[2] = height
            point[axis] = edge
            candidates.add((point[0], point[1], point[2]))
    candidates.update(
        (x, y, z)
        for x, y, z in product(
            (lower[0], upper[0]), (lower[1], upper[1]), (lower[2], upper[2])
        )
    )
    for exit_point in sorted(
        candidates,
        key=lambda p: (
            0 if altitude is not None and p == above else 1,
            distance(start, p),
            p,
        ),
    ):
        # With a roof cruise configured, do not choose a downward face/corner
        # below that clearance merely because it is a shorter emergency leg.
        if exit_point[2] < (
            height if altitude is not None else 0.0
        ) or not route_avoids_no_fly(snapshot, start, (exit_point,), initial_exit=True):
            continue
        try:
            rest = onward(exit_point)
        except ValueError:
            continue
        points = (exit_point,) + rest
        if (
            rest
            and rest[-1] == goal
            and route_avoids_no_fly(snapshot, start, points, initial_exit=True)
        ):
            return points
    raise NoFlyRouteUnavailable("no safe route from bounded no-fly egress candidates")


def _box(value: Sequence[float], name: str) -> tuple[float, float]:
    if len(value) < 2:
        raise ValueError(f"{name} needs two coordinates")
    low, high = float(value[0]), float(value[1])
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError(f"{name} must be finite")
    return low, high


def _point(value: Sequence[float], name: str, altitude: float) -> Point:
    if not 2 <= len(value) <= 3:
        raise ValueError(f"{name} waypoint needs two or three coordinates")
    x, y = _box(value, name)
    z = float(value[2]) if len(value) == 3 else altitude
    if not math.isfinite(z):
        raise ValueError(f"{name} waypoint altitude must be finite")
    return (x, y, z)


def _crosses(snapshot: Snapshot, a: Point, b: Point) -> bool:
    return any(
        z.active and z.kind == "no_fly" and segment_hits_box(a, b, z.lower, z.upper)
        for z in snapshot.zones
    )


class _Routing:
    """Shared validation and corridor connector for survey planners."""

    def __init__(
        self, clearance_m: float, cruise_altitude_m: float | None = None
    ) -> None:
        self.connector = CorridorAstar(clearance_m=clearance_m)
        self.cruise = (
            None
            if cruise_altitude_m is None
            else finite(cruise_altitude_m, "route cruise altitude", 0)
        )

    def connect(
        self, snapshot: Snapshot, a: Point, b: Point, label: str
    ) -> tuple[Point, ...]:
        """Waypoints from ``a`` to ``b``: direct when clear, else corridor A*."""
        if a == b:
            return ()
        if self.cruise is not None:
            return cruise_plan(self.connector, snapshot, a, b, self.cruise)
        if not _crosses(snapshot, a, b):
            return (b,)
        return self.connector.plan(snapshot, a, b)

    def inside(self, snapshot: Snapshot, point: Point, label: str) -> None:
        for zone in snapshot.zones:
            if (
                zone.active
                and zone.kind == "no_fly"
                and all(zone.lower[i] <= point[i] <= zone.upper[i] for i in range(3))
            ):
                raise ValueError(f"{label} lies inside active no-fly zone {zone.id}")


class Coverage:
    """Boustrophedon lawn-mower rows over an explicit XY ENU box.

    Configuration: ``lower``/``upper`` (two floats each, the survey box),
    ``spacing_m`` (row separation, positive) and optional ``clearance_m``
    (corridor connector clearance).  The flight altitude is taken from the
    goal's z, so a task drives both the survey height and the return point.
    """

    description = "Fly spaced boustrophedon rows over the configured survey box."

    def __init__(
        self,
        lower: Sequence[float],
        upper: Sequence[float],
        spacing_m: float = 10.0,
        clearance_m: float = 1.0,
        **config: Any,
    ) -> None:
        if config:
            raise ValueError("coverage accepts only lower/upper/spacing/clearance")
        if len(lower) != 2 or len(upper) != 2:
            raise ValueError("coverage box corners need exactly two coordinates")
        self.lower = _box(lower, "coverage lower corner")
        self.upper = _box(upper, "coverage upper corner")
        if self.lower[0] >= self.upper[0] or self.lower[1] >= self.upper[1]:
            raise ValueError("coverage box lower corner must be below upper corner")
        self.spacing = finite(spacing_m, "coverage row spacing", 1e-6)
        self.routing = _Routing(finite(clearance_m, "coverage clearance", 0.0))

    def rows(self, altitude: float) -> tuple[Point, ...]:
        """Full boustrophedon waypoint sequence at the given altitude."""
        x0, x1 = self.lower[0], self.upper[0]
        y0, y1 = self.lower[1], self.upper[1]
        count = int(math.floor((y1 - y0) / self.spacing)) + 1
        points: list[Point] = []
        for index in range(count):
            y = min(y0 + index * self.spacing, y1)
            leg = ((x0, y, altitude), (x1, y, altitude))
            points.extend(leg if index % 2 == 0 else leg[::-1])
        return tuple(points)

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        altitude = goal[2]
        route = self.rows(altitude)
        for point in (start, *route, goal):
            self.routing.inside(snapshot, point, "coverage waypoint")
        first, last = route[0], route[-1]
        inbound = self.routing.connect(snapshot, start, first, "coverage entry")
        outbound = self.routing.connect(snapshot, last, goal, "coverage exit")
        middle: list[Point] = []
        for a, b in zip(route, route[1:]):
            if _crosses(snapshot, a, b):
                raise ValueError(
                    "coverage row crosses an active no-fly zone; "
                    "the survey box overlaps a restriction"
                )
            middle.append(b)
        return (start, *inbound, *middle, *outbound, goal)


class SurveyRoute:
    """Follow the authored asset polyline, configured as explicit waypoints.

    Configuration: ``waypoints`` (an ordered sequence of two- or three-coordinate
    points; two-coordinate points fly at the goal altitude) and optional
    ``clearance_m``.  The trajectory enters at the first waypoint, visits every
    waypoint in order and ends exactly at the goal.
    """

    description = "Visit each configured asset waypoint in order, then reach the goal."

    def __init__(
        self,
        waypoints: Sequence[Sequence[float]],
        clearance_m: float = 1.0,
        cruise_altitude_m: float | None = None,
        **config: Any,
    ) -> None:
        if config:
            raise ValueError("survey accepts only waypoints/clearance/cruise altitude")
        if not waypoints:
            raise ValueError("survey needs at least one waypoint")
        self.waypoints = waypoints
        self.routing = _Routing(
            finite(clearance_m, "survey clearance", 0.0), cruise_altitude_m
        )

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        route = tuple(
            _point(w, f"survey waypoint {index}", goal[2])
            for index, w in enumerate(self.waypoints)
        )
        for point in (start, *route, goal):
            self.routing.inside(snapshot, point, "survey waypoint")
        result: list[Point] = [start]
        previous = start
        for target in (*route, goal):
            result.extend(self.routing.connect(snapshot, previous, target, "survey"))
            previous = target
        return tuple(result)


class PatrolRoute:
    """One full pass around the configured patrol loop, then return to the goal.

    Configuration: ``waypoints`` (the ordered loop corners, two- or
    three-coordinate points; two-coordinate points fly at the goal altitude),
    optional ``close`` (default true, fly back to the first waypoint so the loop
    is complete) and ``clearance_m``.
    """

    description = "Fly one complete circuit of the configured patrol loop."

    def __init__(
        self,
        waypoints: Sequence[Sequence[float]],
        close: bool = True,
        clearance_m: float = 1.0,
        cruise_altitude_m: float | None = None,
        **config: Any,
    ) -> None:
        if config:
            raise ValueError(
                "patrol accepts only waypoints/close/clearance/cruise altitude"
            )
        if len(waypoints) < 2:
            raise ValueError("patrol needs at least two loop waypoints")
        self.waypoints = waypoints
        self.close = bool(close)
        self.routing = _Routing(
            finite(clearance_m, "patrol clearance", 0.0), cruise_altitude_m
        )

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        route = tuple(
            _point(w, f"patrol waypoint {index}", goal[2])
            for index, w in enumerate(self.waypoints)
        )
        for point in (start, *route, goal):
            self.routing.inside(snapshot, point, "patrol waypoint")
        circuit = route + (route[:1] if self.close else ())
        result: list[Point] = [start]
        previous = start
        for target in (*circuit, goal):
            result.extend(self.routing.connect(snapshot, previous, target, "patrol"))
            previous = target
        return tuple(result)
