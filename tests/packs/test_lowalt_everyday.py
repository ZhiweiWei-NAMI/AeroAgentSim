"""Each beginner template performs its own measured business and replays."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from aerokernel import Fact, Journal, replay

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

BASE = Path(__file__).resolve().parents[2] / "scenarios/templates"


@pytest.mark.parametrize(
    "identity,event",
    [
        ("medical-delivery", "lowalt.task.preempted"),
        ("powerline-inspection", "lowalt.inspection.observed"),
        ("crop-spraying", "lowalt.energy.swap_finished"),
        ("air-taxi", "lowalt.mobility.pad_wait"),
        ("security-patrol", "lowalt.patrol.visited"),
    ],
)
def test_everyday_business_and_replay(
    tmp_path: Path, identity: str, event: str
) -> None:
    scenario = load_scenario(BASE / identity / "scenario.yaml")
    journal = tmp_path / "journal.jsonl"
    sim = Simulation(scenario, journal=Journal(journal))
    try:
        sim.start()
        sim.run_until(scenario.until_ns)
        messages = [m for r in sim.kernel.records for m in project(r)["messages"]]
        assert any(m["schemaId"] == event for m in messages)
        assert not any(m["schemaId"] == "lowalt.task.failed" for m in messages)
        assert any(m["schemaId"] == "lowalt.task.completed" for m in messages)
        if identity in {"powerline-inspection", "crop-spraying"}:
            holds = [
                int(m["at"]["ns"])
                for m in messages
                if m["schemaId"] == "lowalt.weather.hold"
            ]
            releases = [
                int(m["at"]["ns"])
                for m in messages
                if m["schemaId"] == "lowalt.weather.release"
            ]
            observation = (
                "lowalt.spraying.applied" if identity == "crop-spraying" else event
            )
            assert holds and releases
            assert not any(
                m["schemaId"] == observation
                and holds[0] < int(m["at"]["ns"]) < releases[0]
                for m in messages
            )
        if identity == "medical-delivery":
            assert any(m["schemaId"] == "lowalt.task.resumed" for m in messages)
            assert any(
                m["schemaId"] == "lowalt.task.completed"
                and m["payload"]["task"].startswith("urgent-")
                for m in messages
            )
        if identity == "security-patrol":
            assert any(
                m["schemaId"] == "lowalt.task.completed"
                and m["payload"]["task"] == "investigation-1"
                for m in messages
            )
        view = sim.kernel.view()
        expected = {
            (r, d.id): copy.deepcopy(f.value)
            for r in scenario.manifest.entities
            for d in scenario.registry.fields
            if scenario.registry.is_a(r.type_id, d.declaring_type)
            for f in (view.field((r, d.id), view.instant),)
            if isinstance(f, Fact)
        }
        instant = view.instant
    finally:
        sim.close()
    recorded = replay(journal).view()
    assert recorded.instant == instant
    assert {
        (r, field): recorded.field((r, field), instant).value for r, field in expected
    } == expected
