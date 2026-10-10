"""Deterministic, dependency-free proposals for all four algorithm slots."""

from __future__ import annotations

import heapq
import math
from typing import Any

from aeroagentsim.packs.common import distance, finite
from aeroagentsim.packs.geometry import segment_hits_box

from .protocols import (
    Assignment,
    Charge,
    Conflict,
    Point,
    Resolution,
    Snapshot,
    Task,
    Vehicle,
)


def eligible(task: Task, vehicle: Vehicle) -> bool:
    return (
        vehicle.available
        and vehicle.soc > 0
        and (not vehicle.task_kinds or task.kind in vehicle.task_kinds)
        and task.payload_kg <= vehicle.payload_capacity_kg
    )


def trip_metres(vehicle: Vehicle, task: Task) -> float:
    """Straight-line vehicle -> origin -> destination distance."""
    return distance(vehicle.position, task.origin) + distance(
        task.origin, task.destination
    )


def completion_s(vehicle: Vehicle, task: Task) -> float:
    """Estimated seconds until delivery: remaining leg, then pickup and drop."""
    speed = max(vehicle.speed_m_s, 1e-6)
    return vehicle.busy_s + trip_metres(vehicle, task) / speed


def soc_after(vehicle: Vehicle, task: Task, snapshot: Snapshot) -> float | None:
    """State of charge after the trip plus the return to the nearest pad.

    None when the battery capacity is unknown, so no energy claim can be made.
    """
    if vehicle.capacity_j <= 0:
        return None
    back = min(
        (distance(task.destination, p.position) for p in snapshot.pads), default=0.0
    )
    used = (trip_metres(vehicle, task) + back) * vehicle.energy_per_m_j
    return vehicle.soc - used / vehicle.capacity_j


def feasible(
    task: Task, vehicle: Vehicle, snapshot: Snapshot, reserve_soc: float
) -> bool:
    """Eligible, and the battery still holds the reserve after trip and return."""
    if not eligible(task, vehicle):
        return False
    left = soc_after(vehicle, task, snapshot)
    return left is None or left >= reserve_soc


def _reserve(config: dict[str, Any], name: str) -> float:
    reserve = finite(config.pop("reserve_soc", 0.2), "reserve_soc", 0)
    if reserve >= 1:
        raise ValueError(f"{name} reserve_soc must be below one")
    if config:
        raise ValueError(f"{name} accepts only reserve_soc (and energy_weight)")
    return reserve


class Nearest:
    description = (
        "Greedy baseline: highest priority first, each task takes the closest "
        "free drone that can finish the trip with its battery reserve intact."
    )

    def __init__(self, **config: Any) -> None:
        self.reserve_soc = _reserve(dict(config), "nearest")

    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]:
        remaining = {v.id: v for v in snapshot.vehicles}
        result = []
        for task in sorted(snapshot.tasks, key=lambda t: (-t.priority, t.id)):
            vehicles = [
                v
                for v in remaining.values()
                if feasible(task, v, snapshot, self.reserve_soc)
            ]
            if vehicles:
                best = min(
                    vehicles, key=lambda v: (distance(v.position, task.origin), v.id)
                )
                result.append(Assignment(task.id, best.id))
                remaining.pop(best.id)
        return tuple(result)


class Auction:
    description = (
        "Drones bid their estimated completion time, inflated when the trip "
        "would drain the battery; each round awards the lowest bid."
    )

    def __init__(self, **config: Any) -> None:
        config = dict(config)
        self.energy_weight = finite(
            config.pop("energy_weight", 1.0), "energy_weight", 0
        )
        self.reserve_soc = _reserve(config, "auction")

    def bid(self, snapshot: Snapshot, task: Task, vehicle: Vehicle) -> float:
        left = soc_after(vehicle, task, snapshot)
        if left is None:
            left = vehicle.soc
        # A fully charged drone bids its pure completion time; a drone that
        # would end near the reserve asks up to (1 + energy_weight) times more.
        scarcity = min(1.0, max(0.0, 1.0 - left))
        return completion_s(vehicle, task) * (1.0 + self.energy_weight * scarcity)

    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]:
        tasks = {t.id: t for t in snapshot.tasks}
        vehicles = {v.id: v for v in snapshot.vehicles}
        result = []
        while tasks and vehicles:
            bids = [
                (-t.priority, self.bid(snapshot, t, v), t.id, v.id)
                for t in tasks.values()
                for v in vehicles.values()
                if feasible(t, v, snapshot, self.reserve_soc)
            ]
            if not bids:
                break
            # Priority dominates; within it the lowest bid wins the round.
            _, _, task, vehicle = min(bids)
            result.append(Assignment(task, vehicle))
            tasks.pop(task)
            vehicles.pop(vehicle)
        return tuple(result)


def hungarian(cost: list[list[float]]) -> tuple[int, ...]:
    """Shortest augmenting path Hungarian solver for n <= m rectangular costs."""
    n = len(cost)
    if not n:
        return ()
    m = len(cost[0])
    if m < n or any(len(row) != m for row in cost):
        raise ValueError("Hungarian matrix must be rectangular with rows <= columns")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minimum, used = [math.inf] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], math.inf, 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                if cur < minimum[j]:
                    minimum[j], way[j] = cur, j0
                if minimum[j] < delta:
                    delta, j1 = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return tuple(result)


class Hungarian:
    description = (
        "Minimise the total estimated completion time of the whole batch "
        "(globally optimal pairing of free drones and pending orders)."
    )

    def __init__(self, **config: Any) -> None:
        self.reserve_soc = _reserve(dict(config), "hungarian")

    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]:
        tasks = sorted(snapshot.tasks, key=lambda t: (-t.priority, t.id))
        vehicles = sorted(
            (v for v in snapshot.vehicles if v.available), key=lambda v: v.id
        )
        if not tasks or not vehicles:
            return ()
        ok = {
            (v.id, t.id): feasible(t, v, snapshot, self.reserve_soc)
            for v in vehicles
            for t in tasks
        }
        # Explicit unassigned columns permit infeasible or surplus vehicles.
        largest = (
            max(
                (completion_s(v, t) for v in vehicles for t in tasks if ok[v.id, t.id]),
                default=0.0,
            )
            + 1
        )
        priority = max(t.priority for t in tasks)
        dummy = largest * (len(tasks) + 1) * (priority + 2)
        blocked = dummy * (len(vehicles) + 1)
        cost = [
            [
                completion_s(v, t)
                + (priority - t.priority) * largest * (len(vehicles) + 1)
                if ok[v.id, t.id]
                else blocked
                for t in tasks
            ]
            + [dummy] * len(vehicles)
            for v in vehicles
        ]
        columns = hungarian(cost)
        return tuple(
            Assignment(tasks[j].id, v.id)
            for v, j in zip(vehicles, columns)
            if j < len(tasks) and ok[v.id, tasks[j].id]
        )


class Straight:
    description = "Propose one direct segment between the requested endpoints."

    def __init__(self, **config: Any) -> None:
        if config:
            raise ValueError("straight accepts no configuration")

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        return (goal,)


class CorridorAstar:
    description = (
        "A* over obstacle corners and corridor points, avoiding active no-fly boxes."
    )

    def __init__(self, grid_m: float = 20.0, clearance_m: float = 1.0) -> None:
        self.grid = finite(grid_m, "path grid", 1e-6)
        self.clearance = max(
            finite(clearance_m, "path clearance", 1e-6), self.grid * 1e-3
        )

    def plan(self, snapshot: Snapshot, start: Point, goal: Point) -> tuple[Point, ...]:
        boxes = tuple(z for z in snapshot.zones if z.active and z.kind == "no_fly")

        def visible(a: Point, b: Point) -> bool:
            return not any(segment_hits_box(a, b, z.lower, z.upper) for z in boxes)

        if not visible(start, start) or not visible(goal, goal):
            raise ValueError("path endpoint lies inside an active no-fly zone")
        if visible(start, goal):
            return (goal,)
        nodes: list[Point] = [start, goal]
        for zone in boxes:
            for z in sorted({start[2], goal[2]}):
                if zone.lower[2] <= z <= zone.upper[2]:
                    for x in (
                        zone.lower[0] - self.clearance,
                        zone.upper[0] + self.clearance,
                    ):
                        for y in (
                            zone.lower[1] - self.clearance,
                            zone.upper[1] + self.clearance,
                        ):
                            nodes.append((x, y, z))
        for zone in snapshot.zones:
            if zone.active and zone.kind == "corridor":
                nodes.append(tuple((a + b) / 2 for a, b in zip(zone.lower, zone.upper)))  # type: ignore[arg-type]
        frontier = [(distance(start, goal), 0.0, 0)]
        scores: dict[int, float] = {0: 0.0}
        parents: dict[int, int] = {}
        while frontier:
            _, cost, index = heapq.heappop(frontier)
            if cost != scores[index]:
                continue
            if index == 1:
                path = [goal]
                while parents[index] != 0:
                    index = parents[index]
                    path.append(nodes[index])
                return tuple(reversed(path))
            for neighbor, point in enumerate(nodes):
                if neighbor == index or not visible(nodes[index], point):
                    continue
                candidate = cost + distance(nodes[index], point)
                if neighbor not in scores or candidate < scores[neighbor]:
                    scores[neighbor], parents[neighbor] = candidate, index
                    heapq.heappush(
                        frontier,
                        (candidate + distance(point, goal), candidate, neighbor),
                    )
        raise ValueError("no route exists around the active no-fly zones")


class AltitudeLayers:
    description = "Move conflicting drones to deterministic separate altitude layers."

    def __init__(
        self, layer_spacing_m: float = 12.0, minimum_altitude_m: float | None = None
    ) -> None:
        self.spacing = finite(layer_spacing_m, "altitude layer spacing", 1e-6)
        self.minimum = (
            None
            if minimum_altitude_m is None
            else finite(minimum_altitude_m, "minimum altitude layer", 0)
        )

    def resolve(
        self, snapshot: Snapshot, conflicts: tuple[Conflict, ...]
    ) -> tuple[Resolution, ...]:
        vehicles = {v.id: v for v in snapshot.vehicles}
        affected = sorted({identity for c in conflicts for identity in (c.a, c.b)})
        if not affected:
            return ()
        external = dict(snapshot.monitored_positions)
        if external:
            base = max(p[2] for p in external.values()) + self.spacing
            affected = [i for i in affected if i not in external]
        else:
            base = min(vehicles[i].position[2] for i in affected)
        if self.minimum is not None:
            base = max(base, self.minimum)
        return tuple(
            Resolution(identity, "altitude", base + index * self.spacing)
            for index, identity in enumerate(affected)
            if abs(vehicles[identity].position[2] - (base + index * self.spacing))
            > 1e-6
        )


class HoldAndGo:
    description = "Hold the later drone in each pair while the earlier one moves."

    def __init__(self, hold_s: float = 3.0) -> None:
        self.hold_s = finite(hold_s, "conflict hold", 1e-6)

    def resolve(
        self, snapshot: Snapshot, conflicts: tuple[Conflict, ...]
    ) -> tuple[Resolution, ...]:
        return tuple(
            Resolution(identity, "hold", hold_s=self.hold_s)
            for identity in sorted(
                {
                    max(
                        i
                        for i in (c.a, c.b)
                        if i not in dict(snapshot.monitored_positions)
                    )
                    for c in conflicts
                    if any(
                        i not in dict(snapshot.monitored_positions) for i in (c.a, c.b)
                    )
                }
            )
        )


class ThresholdReturn:
    description = "Return low-battery drones to the nearest charging pad."

    def __init__(self, threshold: float = 0.2) -> None:
        self.threshold = finite(threshold, "charging threshold", 0)
        if self.threshold > 1:
            raise ValueError("charging threshold exceeds one")

    def charge(self, snapshot: Snapshot) -> tuple[Charge, ...]:
        result = []
        for vehicle in sorted(snapshot.vehicles, key=lambda v: v.id):
            if vehicle.soc <= self.threshold and snapshot.pads:
                pad = min(
                    snapshot.pads,
                    key=lambda p: (distance(vehicle.position, p.position), p.id),
                )
                result.append(Charge(vehicle.id, pad.id))
        return tuple(result)
