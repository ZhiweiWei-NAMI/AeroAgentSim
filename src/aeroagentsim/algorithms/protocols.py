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
