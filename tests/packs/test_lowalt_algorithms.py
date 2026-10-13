"""Algorithm proposals use immutable inputs and real geometry."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from importlib.metadata import EntryPoint

import pytest

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import (
    Charge,
    Conflict,
    Pad,
    Snapshot,
    Task,
    Vehicle,
    Zone,
)
from aeroagentsim.packs.common import distance
from aeroagentsim.packs.geometry import segment_hits_box


def vehicle(
    identity: str, x: float, capacity: float = 3.0, soc: float = 0.8
) -> Vehicle:
    return Vehicle(identity, (x, 0.0, 50.0), soc, 0.0, capacity, True)


def task(identity: str, x: float, payload: float = 1.0) -> Task:
    return Task(identity, "parcel", 1, (x, 0.0, 50.0), (x, 30.0, 50.0), payload)


@pytest.mark.parametrize("name", ["nearest", "auction", "hungarian"])
def test_allocation_unique_payload_feasible(name: str) -> None:
    state = Snapshot(
        0,
        (vehicle("light", 0, 2), vehicle("heavy", 30, 5)),
        (task("heavy-task", 1, 4), task("light-task", 0, 1)),
    )
    proposals = load_algorithm("allocation", name).allocate(state)
    assert {(a.task, a.vehicle) for a in proposals} == {
        ("heavy-task", "heavy"),
        ("light-task", "light"),
    }
    with pytest.raises(FrozenInstanceError):
        state.at_ns = 9  # type: ignore[misc]


def test_hungarian_global_distance_minimum() -> None:
    state = Snapshot(
        0, (vehicle("a", 0), vehicle("b", 5)), (task("first", 4), task("second", 6))
    )

    def cost(name: str) -> float:
        ts, vs = {t.id: t for t in state.tasks}, {v.id: v for v in state.vehicles}
        return sum(
            distance(vs[a.vehicle].position, ts[a.task].origin)
            for a in load_algorithm("allocation", name).allocate(state)
        )

    assert cost("hungarian") == 5.0
    assert cost("nearest") == 7.0


def test_astar_checks_complete_segments_and_active_zones() -> None:
    zone = Zone("protected", "no_fly", (4.0, -1.0, 0.0), (6.0, 3.0, 100.0), True)
    start, goal = (0.0, 1.0, 50.0), (10.0, 1.0, 50.0)
    state = Snapshot(0, (), zones=(zone,))
    assert load_algorithm("path_planning", "straight").plan(state, start, goal) == (
        goal,
    )
    path = load_algorithm("path_planning", "corridor_astar").plan(state, start, goal)
    assert path[-1] == goal
    assert len(path) > 1
    assert all(
        not segment_hits_box(a, b, zone.lower, zone.upper)
        for a, b in zip((start,) + path, path)
    )
    inactive = Zone(zone.id, zone.kind, zone.lower, zone.upper, False)
    assert load_algorithm("path_planning", "corridor_astar").plan(
        Snapshot(0, (), zones=(inactive,)), start, goal
    ) == (goal,)


@pytest.mark.parametrize("name", ["altitude_layers", "hold_and_go"])
def test_conflict_proposals(name: str) -> None:
    state = Snapshot(0, (vehicle("a", 0), vehicle("b", 1)))
    algorithm = load_algorithm("conflict_resolution", name)
    proposals = algorithm.resolve(state, (Conflict("a", "b", 1.0, 0.0),))
    assert proposals
    assert any(
        p.action == "altitude"
        and p.altitude_m is not None
        and p.altitude_m != 50
        or p.action == "hold"
        and p.hold_s > 0
        for p in proposals
    )
    assert algorithm.resolve(state, ()) == ()


def test_charging_threshold_selects_nearest_pad() -> None:
    state = Snapshot(
        0,
        (vehicle("low", 0, soc=0.1), vehicle("healthy", 2)),
        pads=(
            Pad("far", (100.0, 0.0, 50.0), 1000, 1, 0, 0),
            Pad("near", (1.0, 0.0, 50.0), 1000, 1, 0, 0),
        ),
    )
    assert load_algorithm("charging", "threshold_return").charge(state) == (
        Charge("low", "near"),
    )


@pytest.mark.parametrize("occupancy,reserved", [(1, 0), (0, 1)])
def test_charging_uses_a_free_pad_before_an_occupied_or_reserved_pad(
    occupancy: int, reserved: int,
) -> None:
    state = Snapshot(
        0, (vehicle("low", 0, soc=0.1),),
        pads=(
            Pad("near", (1.0, 0.0, 50.0), 1000, 1, occupancy, reserved),
            Pad("free", (100.0, 0.0, 50.0), 1000, 1, 0, 0),
        ),
    )
    assert load_algorithm("charging", "threshold_return").charge(state) == (
        Charge("low", "free"),
    )


def test_charging_batch_reserves_slots_and_queues_when_all_are_full() -> None:
    state = Snapshot(
        0, tuple(vehicle(identity, 0, soc=0.1) for identity in ("a", "b", "c", "d")),
        pads=(
            Pad("near", (1.0, 0.0, 50.0), 1000, 2, 1, 0),
            Pad("far", (100.0, 0.0, 50.0), 1000, 1, 0, 0),
        ),
    )
    assert load_algorithm("charging", "threshold_return").charge(state) == (
        Charge("a", "near"), Charge("b", "far"),
        Charge("c", "near"), Charge("d", "near"),
    )


@pytest.mark.parametrize("capacity", [None, 0, -1, 1.5, True])
def test_charging_pad_requires_authored_integer_capacity(capacity: object) -> None:
    with pytest.raises(ValueError, match="capacity"):
        Pad("invalid", (0.0, 0.0, 0.0), 1000, capacity, 0, 0)  # type: ignore[arg-type]


def test_module_and_entrypoint_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        type(
            load_algorithm("allocation", "aeroagentsim.algorithms.builtins:Nearest")
        ).__name__
        == "Nearest"
    )
    entry = EntryPoint(
        "allocation.custom",
        "aeroagentsim.algorithms.builtins:Auction",
        "aeroagentsim.algorithms",
    )
    monkeypatch.setattr("aeroagentsim.algorithms.entry_points", lambda **kw: [entry])
    assert type(load_algorithm("allocation", "custom")).__name__ == "Auction"
