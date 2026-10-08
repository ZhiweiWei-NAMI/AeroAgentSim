"""Real kinematic samples, delayed record availability and journal verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aerokernel import Journal, replay

from aeroagentsim.packs.metrics import compute, metrics
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

from .conftest import SCENARIOS, document


def test_inspection_scenario_coverage_and_replay(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIOS / "inspection-small.yaml")
    sim = Simulation(scenario, journal=Journal(tmp_path / "journal.jsonl"))
    try:
        sim.start()
        sim.run_until(scenario.until_ns)
        report = compute(sim.kernel)["inspection"]
        assert report["observations"] == 19 and report["invalid_observations"] == 0
        assert report["coverage_pct"] == pytest.approx(100)
        assert report["targets"]["origin"]["max_revisit_s"] > 1
        observations = [
            m["payload"]
            for r in sim.kernel.records[1:]
            for m in project(r)["messages"]
            if m["schemaId"] == "packs.inspection.observation"
        ]
        assert len(observations) == 19
        assert all(
            o["available_ns"] - o["acquired_ns"] == 100_000_000 for o in observations
        )
        positions = {tuple(o["sample"]["position_enu_m"]) for o in observations}
        assert len(positions) > 3
        assert not any("image" in o["sample"] for o in observations)
    finally:
        sim.close()
    assert metrics(tmp_path)["inspection"] == report
    offline = replay(tmp_path / "journal.jsonl")
    assert offline.view().relations(
        "packs.inspection.subject", offline.view().instant
    ) == sim.kernel.view().relations(
        "packs.inspection.subject", sim.kernel.view().instant
    )


def test_horizon_reports_unsuccessful_sensor_samples() -> None:
    d = document("inspection-small")
    d["run"]["until_ns"] = 2_000_000_000
    next(e for e in d["entities"] if e["id"] == "camera-1")["facts"][
        "packs.camera.attitude_deg"
    ] = [0.0, 60.0, 0.0]
    sim = Simulation(load_scenario(d, base=SCENARIOS))
    try:
        sim.start()
        sim.run_until(2_000_000_000)
        report = compute(sim.kernel)["inspection"]
        assert report["observations"] == report["invalid_observations"] == 1
        assert report["coverage_pct"] == 0
        assert all(t["mean_revisit_s"] is None for t in report["targets"].values())
    finally:
        sim.close()


def test_offline_cli_and_incomplete_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from aeroagentsim.services.cli import main

    d: dict[str, Any] = document("inspection-small")
    d["run"]["until_ns"] = 2_000_000_000
    sim = Simulation(
        load_scenario(d, base=SCENARIOS), journal=Journal(tmp_path / "journal.jsonl")
    )
    sim.start()
    sim.run_until(2_000_000_000)
    sim.close()
    # Replay must build no engines, including plugins with unavailable backends.
    monkeypatch.setattr(
        Simulation,
        "__init__",
        lambda *a, **k: pytest.fail("offline metrics built an engine"),
    )
    monkeypatch.setattr("sys.argv", ["aeroagentsim", "metrics", str(tmp_path)])
    main()
    assert json.loads(capsys.readouterr().out)["inspection"]["observations"] == 1
    path = tmp_path / "journal.jsonl"
    data = path.read_bytes()
    path.write_bytes(data[:-5])
    with pytest.raises(ValueError, match="JOURNAL_TRUNCATED|complete journal"):
        metrics(tmp_path)
