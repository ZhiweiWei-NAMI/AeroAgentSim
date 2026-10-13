"""Built-in allocators differ on hand-made batches and respect the cost model."""

from __future__ import annotations

from aeroagentsim.algorithms import load_algorithm
from aeroagentsim.algorithms.protocols import Pad, Snapshot, Task, Vehicle


def drone(identity: str, x: float, **extra: float) -> Vehicle:
    return Vehicle(identity, (x, 0.0, 50.0), 1.0, 0.0, 3.0, True, **extra)  # type: ignore[arg-type]


def order(identity: str, x: float, priority: int = 1) -> Task:
    # Same drop-off leg for every drone, so only the pickup distance differs.
    return Task(identity, "parcel", priority, (x, 0.0, 50.0), (x, 100.0, 50.0), 1.0)


def pairs(name: str, state: Snapshot) -> dict[str, str]:
    result = load_algorithm("allocation", name).allocate(state)
    return {a.task: a.vehicle for a in result}


def total_pickup(state: Snapshot, chosen: dict[str, str]) -> float:
    vehicles = {v.id: v for v in state.vehicles}
    tasks = {t.id: t for t in state.tasks}
    return sum(
        abs(vehicles[v].position[0] - tasks[t].origin[0]) for t, v in chosen.items()
    )


def test_greedy_is_not_globally_optimal_but_hungarian_is() -> None:
    # t1 grabs the closer drone b, which leaves t2 a 14 m reach: 4 + 14 = 18.
    state = Snapshot(
        0, (drone("a", 0), drone("b", 10)), (order("t1", 6), order("t2", 14))
    )
    greedy = pairs("nearest", state)
    assert greedy == {"t1": "b", "t2": "a"}
    assert pairs("auction", state) == greedy
    optimal = pairs("hungarian", state)
    assert optimal == {"t1": "a", "t2": "b"}
    assert total_pickup(state, optimal) == 10 < total_pickup(state, greedy) == 18


def test_auction_awards_the_cheapest_bid_first() -> None:
    # Nearest serves t1 first (a, 9 m) and strands t2 on b (19 m).
    state = Snapshot(
        0, (drone("a", 0), drone("b", 20)), (order("t1", 9), order("t2", 1))
    )
    assert pairs("nearest", state) == {"t1": "a", "t2": "b"}
    assert pairs("auction", state) == {"t2": "a", "t1": "b"}
    assert pairs("hungarian", state) == {"t2": "a", "t1": "b"}


def test_completion_time_counts_the_remaining_current_leg() -> None:
    # The near drone is busy for 100 s: it is the nearest, but not the fastest.
    state = Snapshot(
        0, (drone("near", 0, busy_s=100.0), drone("far", 50)), (order("t", 10),)
    )
    assert pairs("nearest", state) == {"t": "near"}
    assert pairs("auction", state) == {"t": "far"}
    assert pairs("hungarian", state) == {"t": "far"}


def test_speed_changes_the_estimated_completion_time() -> None:
    state = Snapshot(
        0,
        (drone("slow", 0, speed_m_s=5.0), drone("quick", 30, speed_m_s=30.0)),
        (order("t", 10),),
    )
    assert pairs("nearest", state) == {"t": "slow"}
    assert pairs("hungarian", state) == {"t": "quick"}


def test_energy_infeasible_pairs_are_excluded_by_every_allocator() -> None:
    # 1 kJ battery, 12 J/m: the 100 m drop-off alone drains more than it holds.
    weak = drone("weak", 0, capacity_j=1000.0)
    strong = drone("strong", 90, capacity_j=240000.0)
    state = Snapshot(0, (weak, strong), (order("t", 1),))
    for name in ("nearest", "auction", "hungarian"):
        assert pairs(name, state) == {"t": "strong"}, name
    only = Snapshot(0, (weak,), (order("t", 1),))
    for name in ("nearest", "auction", "hungarian"):
        assert pairs(name, only) == {}, name


def test_return_reserve_counts_the_flight_home() -> None:
    # Drop-off 100 m from the pad, battery for exactly the outbound leg only.
    pad = Pad("pad", (0.0, 400.0, 50.0), 5000.0, 1, 0, 0)
    tight = Vehicle("tight", (0.0, 0.0, 50.0), 0.25, 0.0, 3.0, True, capacity_j=24000.0)
    task = order("t", 0)
    far_pad = Snapshot(0, (tight,), (task,), pads=(pad,))
    near_pad = Snapshot(0, (tight,), (task,), pads=(Pad("p", (0, 100, 50), 1.0, 1, 0, 0),))
    assert pairs("nearest", far_pad) == {}
    assert pairs("nearest", near_pad) == {"t": "tight"}


def test_auction_bids_are_inflated_by_low_remaining_energy() -> None:
    state = Snapshot(
        0,
        (
            Vehicle("a-low", (4.0, 0.0, 50.0), 0.3, 0.0, 3.0, True, capacity_j=1e5),
            drone("b-full", -10, capacity_j=1e5),
        ),
        (order("t", 0),),
    )
    # Nearest only looks at the pickup distance; the auction charges for the
    # battery the trip would burn on a drone that is already low.
    assert pairs("nearest", state) == {"t": "a-low"}
    assert pairs("auction", state) == {"t": "b-full"}


def test_priority_beats_cost_in_every_allocator() -> None:
    state = Snapshot(
        0, (drone("a", 0),), (order("cheap", 0, priority=1), order("urgent", 40, 9))
    )
    for name in ("nearest", "auction", "hungarian"):
        assert pairs(name, state) == {"urgent": "a"}, name


def test_allocators_are_deterministic() -> None:
    state = Snapshot(
        0,
        tuple(drone(f"v{i}", i * 7.0) for i in range(5)),
        tuple(order(f"t{i}", i * 11.0) for i in range(4)),
    )
    for name in ("nearest", "auction", "hungarian"):
        assert pairs(name, state) == pairs(name, state)
