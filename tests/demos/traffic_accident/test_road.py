"""Real kernel road traces, swept gaps and source-recorded geometry comparisons."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pytest
from aerokernel import Journal
from aerokernel.state import Fact
from aerokernel.values import thaw

from aeroagentsim.packs.traffic_accident.geometry import Polyline, Pose, overlap
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from tests.demos.conftest import SCENARIO, domain_document


def test_polyline_rectangle_and_invalid_geometry() -> None:
    line = Polyline([[0.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
    assert line.at(5).position == (5, 0, 0)
    assert overlap(Pose((0, 0, 0), 0), Pose((4.9, 0, 0), 0))
    assert not overlap(Pose((0, 0, 0), 0), Pose((5.1, 0, 0), 0))
    with pytest.raises(ValueError, match="positive arc length"):
        Polyline([[0, 0, 0], [0, 0, 0]])
    with pytest.raises(ValueError, match="exceeds"):
        line.at(21)


def test_road_kernel_trace_and_deterministic_journal(tmp_path: Path) -> None:
    d = domain_document(
        roads=(
            "vehicle.reporter",
            "vehicle.incident.a",
            "vehicle.incident.b",
            "vehicle.001",
        )
    )
    traces: list[list[Any]] = []
    for iteration in range(2):
        sim = Simulation(
            load_scenario(d, base=SCENARIO),
            journal=Journal(tmp_path / f"{iteration}.jsonl"),
        )
        sim.start()
        refs = {r.id: r for r in sim.scenario.manifest.entities}
        samples: list[Any] = []
        for i in range(1, 16):
            view = sim.run_until(i * 66_666_667)
            pose = view.field(
                (refs["vehicle.reporter"], "traffic.road.position_enu_m"), view.instant
            )
            assert isinstance(pose, Fact)
            samples.append(thaw(pose.value))
        traces.append(samples)
        reporter = sim.kernel.view().field(
            (refs["vehicle.reporter"], "traffic.road.progress_m"),
            sim.kernel.view().instant,
        )
        assert isinstance(reporter, Fact)
        assert thaw(reporter.value) == pytest.approx(16.00000003)
        sim.close()
    assert traces[0] == traces[1]
    assert (tmp_path / "0.jsonl").read_bytes() == (tmp_path / "1.jsonl").read_bytes()
    legacy = json.loads((SCENARIO / "fixtures/legacy-trace.json").read_text())
    recorded: list[Any] = [s for s in legacy["samples"] if 0 < s["time_s"] <= 1]
    line = Polyline(d["engines"]["road_motion"]["config"]["routes"][-3]["points_enu_m"])
    # Source wall timestamps rounded to 3 decimals. Arc length motion compares
    # at those recorded timestamps rather than pretending the grids coincide.
    deviations = [
        math.dist(
            line.at(10 + 6 * s["time_s"]).position,
            s["road"]["vehicle.reporter"]["position_enu_m"],
        )
        for s in recorded
    ]
    assert max(deviations) <= 0.003


def test_occupied_route_physically_vetoes_motion_and_bypass() -> None:
    d = domain_document(roads=("vehicle.reporter", "vehicle.incident.a"))
    cfg = d["engines"]["road_motion"]["config"]
    # Place the second body 5.2m in front using actual authored geometry. Its
    # speed is explicitly zero; subsequent reporter steps must not tunnel.
    lane = next(r for r in cfg["routes"] if r["id"] == "lane.incident")
    line = Polyline(lane["points_enu_m"])
    incident = next(a for a in cfg["actors"] if a["id"] == "vehicle.incident.a")
    incident.update(progress_m=15.2, speed_mps=0.0, stop_progress_m=15.2)
    entity = next(e for e in d["entities"] if e["id"] == incident["id"])
    entity["facts"].update(
        {
            "traffic.road.position_enu_m": list(line.at(15.2).position),
            "traffic.road.progress_m": 15.2,
        }
    )
    d["bindings"]["commands"] = d["bindings"]["commands"][:1]
    sim = Simulation(load_scenario(d, base=SCENARIO))
    try:
        sim.start()
        sim.run_until(1_000_000_005)
        ref = next(
            r for r in sim.scenario.manifest.entities if r.id == "vehicle.reporter"
        )
        view = sim.kernel.view()
        progress = view.field((ref, "traffic.road.progress_m"), view.instant)
        blocked = view.field((ref, "traffic.road.blocked_by"), view.instant)
        assert isinstance(progress, Fact) and thaw(progress.value) == 10.0
        assert isinstance(blocked, Fact)
        bodies = thaw(blocked.value)
        assert isinstance(bodies, list) and len(bodies) == 1
    finally:
        sim.close()
