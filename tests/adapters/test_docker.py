"""Measured real native integration; explicitly opt in with -m docker."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from aerokernel import CommandRequest, Fact, Instant, Journal, replay

from aeroagentsim.adapters.runner import AdapterRun, observed_flight
from aeroagentsim.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[2]
MEASUREMENTS = ROOT / "tests" / "adapters" / "measurements.json"


def measure(name: str, data: dict[str, Any]) -> None:
    records = json.loads(MEASUREMENTS.read_text()) if MEASUREMENTS.exists() else {}
    records[name] = data
    MEASUREMENTS.write_text(json.dumps(records, indent=2) + "\n")


def outcome(run: AdapterRun, data: bytes) -> dict[str, Any]:
    # Replay occurs after the native connections/containers have been removed.
    offline = replay(data)
    assert not offline.incomplete
    assert offline.view().instant == run.kernel.view().instant
    live_store = run.kernel.view()._store
    replay_store = offline.view()._store
    assert dict(live_store.lives) == dict(replay_store.lives)
    assert dict(live_store.messages) == dict(replay_store.messages)
    for key in live_store.facts:
        assert tuple(live_store.facts[key]) == tuple(replay_store.facts[key])
    return {
        "simulated_ns": offline.view().instant.ns,
        "journal_bytes": len(data),
        "offline_replay": "passed",
    }


@pytest.mark.docker
def test_px4_takeoff_goto_land_replay(tmp_path: Path) -> None:
    scenario = load_scenario(ROOT / "scenarios/adapters/px4-flight.yaml")
    journal = Journal(tmp_path / "px4.jsonl")
    startup = time.perf_counter()
    with AdapterRun(scenario, containers=True, journal=journal) as run:
        run.start()
        startup_s = time.perf_counter() - startup
        start = time.perf_counter()
        phases = observed_flight(run)
        wall_s = time.perf_counter() - start
        view = run.kernel.view()
        ref = scenario.manifest.entities[0]
        armed = view.field((ref, "e1.px4.armed"), view.instant)
        landed = view.field((ref, "e1.px4.landed"), view.instant)
        assert isinstance(armed, Fact) and armed.value is False
        assert isinstance(landed, Fact) and landed.value == "ON_GROUND"
        simulated_ns = view.instant.ns
    data = (tmp_path / "px4.jsonl").read_bytes()
    report = {
        **outcome(run, data),
        "startup_s": startup_s,
        "wall_s": wall_s,
        "rtf": simulated_ns / 1e9 / wall_s,
        "phases": phases,
    }
    measure("px4-flight", report)
    print(json.dumps(report))


@pytest.mark.docker
def test_sumo_grid_60s_reroute_replay(tmp_path: Path) -> None:
    scenario = load_scenario(ROOT / "scenarios/adapters/sumo-grid.yaml")
    journal = Journal(tmp_path / "sumo.jsonl")
    startup = time.perf_counter()
    with AdapterRun(scenario, containers=True, journal=journal) as run:
        run.start()
        startup_s = time.perf_counter() - startup
        start = time.perf_counter()
        run.run_until(60_000_000_000)
        wall_s = time.perf_counter() - start
        view = run.kernel.view()
        vehicles = [ref for ref in view._store.lives if ref.type_id == "e1:RoadVehicle"]
        assert len(vehicles) == 50
        reroutes = [
            m
            for m in view._store.messages.values()
            if m.schema_id == "adapters.sumo.reroute"
        ]
        assert len(reroutes) == 1
        assert view.action(reroutes[0].id).status == "succeeded"
    data = (tmp_path / "sumo.jsonl").read_bytes()
    report = {
        **outcome(run, data),
        "startup_s": startup_s,
        "wall_s": wall_s,
        "rtf": 60 / wall_s,
        "vehicles_created": len(vehicles),
        "reroute": "succeeded",
    }
    measure("sumo-grid", report)
    print(json.dumps(report))


@pytest.mark.docker
def test_coupled_30s_real_mobility_packets_replay(tmp_path: Path) -> None:
    scenario = load_scenario(ROOT / "scenarios/adapters/coupled.yaml")
    journal = Journal(tmp_path / "coupled.jsonl")
    startup = time.perf_counter()
    with AdapterRun(scenario, containers=True, journal=journal) as run:
        run.start()
        startup_s = time.perf_counter() - startup
        start = time.perf_counter()
        for action, params in (("arm", {}), ("takeoff", {"altitude_m": 5.0})):
            current = run.kernel.view().instant.ns
            mid = run.kernel.submit(
                CommandRequest(
                    f"adapters.px4_gazebo.{action}",
                    "flight",
                    Instant(current + 1),
                    {"entity": "aircraft", **params},
                )
            )
            while run.kernel.view().action(mid).status not in {
                "succeeded",
                "failed",
                "rejected",
            }:
                current = run.kernel.view().instant.ns
                assert current < 30_000_000_000, (
                    f"{action} did not finish before coupled horizon"
                )
                run.run_until(current + 200_000_000)
            assert run.kernel.view().action(mid).status == "succeeded"
        run.run_until(30_000_000_000)
        wall_s = time.perf_counter() - start
        view = run.kernel.view()
        events = list(view._store.messages.values())
        packets = [m for m in events if m.schema_id == "adapters.ns3.send"]
        deliveries = [m for m in events if m.schema_id == "adapters.ns3.delivery"]
        drops = [m for m in events if m.schema_id == "adapters.ns3.drop"]
        assert len(packets) == 29
        assert len(deliveries) + len(drops) == 29
        assert deliveries
        assert any(m.at.ns < m.available.ns for m in deliveries)
        for packet in packets:
            assert view.action(packet.id).status in {"succeeded", "failed"}
        links = [m for m in events if m.schema_id == "adapters.ns3.link"]
        assert len(links) == 150
        assert links[-1].payload["distance_m"] != links[0].payload["distance_m"]
        ref = scenario.manifest.entities[0]
        position = view.field((ref, "e1.px4.position"), view.instant)
        assert isinstance(position, Fact) and position.value[2] > 3.0
        vehicles = [ref for ref in view._store.lives if ref.type_id == "e1:RoadVehicle"]
        assert len(vehicles) == 50
    data = (tmp_path / "coupled.jsonl").read_bytes()
    report = {
        **outcome(run, data),
        "startup_s": startup_s,
        "wall_s": wall_s,
        "rtf": 30 / wall_s,
        "deliveries": len(deliveries),
        "drops": len(drops),
        "link_samples": len(links),
        "consumer_lag_ns": 200_000_000,
        "vehicles_created": len(vehicles),
    }
    measure("coupled", report)
    print(json.dumps(report))
