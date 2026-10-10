"""Parcel district routing, forced conflicts and journal-only replay."""

from __future__ import annotations

import copy
import time
from pathlib import Path

import yaml
from aerokernel import Fact, Journal, replay

from aeroagentsim.packs.geometry import segment_hits_box
from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def test_parcel_120s_conflicts_geofence_and_replay(tmp_path: Path) -> None:
    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    # Generated drones start together: force a real separation violation.
    document["generators"]["fleet"]["starts"] = [[-240.0, -220.0, 50.0]]
    document["engines"]["airspace"]["config"]["separation_horizontal_m"] = 40.0
    document["engines"]["tasks"]["config"]["batch_window_s"] = 0.0
    scenario = load_scenario(document, base=BASE)
    journal = tmp_path / "journal.jsonl"
    sim = Simulation(scenario, journal=Journal(journal))
    began = time.monotonic()
    try:
        sim.start()
        sim.run_until(120_000_000_000)
        elapsed = time.monotonic() - began
        messages = [
            m for record in sim.kernel.records for m in project(record)["messages"]
        ]
        assert any(m["schemaId"] == "lowalt.task.completed" for m in messages)
        assert any(m["schemaId"] == "lowalt.airspace.conflict" for m in messages)
        assert not any(
            m["schemaId"] == "lowalt.airspace.geofence_entered"
            and m["payload"]["zone"] == "central-no-fly"
            for m in messages
        )
        refs = tuple(r for r in scenario.manifest.entities if r.type_id == "lowalt:UAV")
        # Check committed motion, including complete segments between samples.
        previous = {
            r.id: tuple(scenario.initial[r.id]["lowalt.position"]) for r in refs
        }
        for record in sim.kernel.records:
            for fact in project(record)["facts"]:
                if fact["fieldId"] == "lowalt.position":
                    identity = fact["entity"]["id"]
                    assert not segment_hits_box(
                        previous[identity],
                        tuple(fact["value"]),
                        (-55, -55, 0),
                        (55, 55, 120),
                    )
                    previous[identity] = tuple(fact["value"])
        end = sim.kernel.view()
        expected = {
            (r, f): copy.deepcopy(end.field((r, f), end.instant).value)
            for r in scenario.manifest.entities
            for descriptor in scenario.registry.fields
            if scenario.registry.is_a(r.type_id, descriptor.declaring_type)
            for f in (descriptor.id,)
            if isinstance(end.field((r, f), end.instant), Fact)
        }
        assert elapsed < 120.0, f"120 simulated seconds took {elapsed:.2f}s"
    finally:
        sim.close()
    recorded = replay(journal)
    view = recorded.view()
    assert view.instant == end.instant
    assert {
        (r, f): view.field((r, f), view.instant).value for r, f in expected
    } == expected


def test_template_knobs_expand_and_seeded_rerun() -> None:
    document = yaml.safe_load((BASE / "scenario.yaml").read_text())
    document["generators"]["fleet"]["count"] = 2
    document["algorithms"]["allocation"] = "auction"
    document["engines"]["environment"]["config"]["profiles"]["weather"]["lowalt.wind"][
        "value"
    ][0] = 3.0
    scenario = load_scenario(document, base=BASE)
    assert (
        len([r for r in scenario.manifest.entities if r.type_id == "lowalt:UAV"]) == 2
    )
    assert scenario.engines["tasks"]["config"]["algorithms"]["allocation"] == "auction"
    runs = []
    for _ in range(2):
        sim = Simulation(scenario)
        try:
            sim.start()
            sim.run_until(20_000_000_000)
            weather = next(r for r in scenario.manifest.entities if r.id == "weather")
            view = sim.kernel.view()
            assert view.field((weather, "lowalt.wind"), view.instant).value == (
                3.0,
                0.0,
                0.0,
            )
            runs.append([project(r) for r in sim.kernel.records])
        finally:
            sim.close()
    assert runs[0] == runs[1]
