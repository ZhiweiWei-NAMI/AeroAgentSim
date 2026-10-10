"""Immutable algorithm inputs and proposals; the runtime retains write authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

Point = tuple[float, float, float]


@dataclass(frozen=True)
class Vehicle:
    id: str
    position: Point
    soc: float
    payload_kg: float
    payload_capacity_kg: float
    available: bool
    task_kinds: tuple[str, ...] = ()
    # Cost-model inputs; the defaults keep hand-made snapshots working.
    speed_m_s: float = 15.0  # cruise speed used for completion-time estimates
    energy_per_m_j: float = 12.0  # distance-proportional energy per metre
    capacity_j: float = 0.0  # battery capacity; 0 means unknown (no energy check)
    busy_s: float = 0.0  # remaining time on the current leg before it is free


@dataclass(frozen=True)
class Task:
    id: str
    kind: str
    priority: int
    origin: Point
    destination: Point
    payload_kg: float


@dataclass(frozen=True)
class Zone:
    id: str
    kind: str
    lower: Point
    upper: Point
    active: bool


@dataclass(frozen=True)
class Pad:
    id: str
    position: Point
    power_w: float


@dataclass(frozen=True)
class Snapshot:
    at_ns: int
    vehicles: tuple[Vehicle, ...]
    tasks: tuple[Task, ...] = ()
    zones: tuple[Zone, ...] = ()
    pads: tuple[Pad, ...] = ()
    wind: Point = (0.0, 0.0, 0.0)
    monitored_positions: tuple[tuple[str, Point], ...] = ()


@dataclass(frozen=True)
class Assignment:
    task: str
    vehicle: str


@dataclass(frozen=True)
class Conflict:
    a: str
    b: str
    horizontal_m: float
    vertical_m: float


@dataclass(frozen=True)
class Resolution:
    vehicle: str
    action: str
    altitude_m: float | None = None
    hold_s: float = 0.0


@dataclass(frozen=True)
class Charge:
    vehicle: str
    pad: str


class Allocation(Protocol):
    def allocate(self, snapshot: Snapshot) -> tuple[Assignment, ...]: ...


class PathPlanning(Protocol):
    def plan(
        self, snapshot: Snapshot, start: Point, goal: Point
    ) -> tuple[Point, ...]: ...


class ConflictResolution(Protocol):
    def resolve(
        self, snapshot: Snapshot, conflicts: tuple[Conflict, ...]
    ) -> tuple[Resolution, ...]: ...


class Charging(Protocol):
    def charge(self, snapshot: Snapshot) -> tuple[Charge, ...]: ...
