"""WAL-only truth/chain feed extensions, including the real Q6 sampled partition."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

import pytest
from aerokernel.errors import KernelError
from aerokernel.journal import replay

from aeroagentsim.engines.predicate import prepare
from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import header, project

BASE = Path("scenarios/behaviours").resolve()


def minimal() -> dict[str, Any]:
    return copy.deepcopy(load_scenario(BASE / "minimal.yaml").document)


def test_recorded_extensions_preserve_intervals_and_replay(tmp_path: Path) -> None:
    session = RunSession(load_scenario(BASE / "minimal.yaml"), tmp_path / "run")
    session.run_until(20)
    session.close()
    records = [project(record) for record in session.simulation.kernel.records]
    truths = [entry for record in records for entry in record.get("predicateTruth", [])]
    chains = [entry for record in records for entry in record.get("chainInstances", [])]
    assert truths and chains
    assert all(
        entry["roles"]["task"] == {"id": "task-1", "generation": 0}
        for entry in truths + chains
    )
    assert all(entry["version"] and entry["causes"] for entry in truths + chains)
    assert {entry["op"] for entry in truths} == {"assert", "close"}
    for close in (entry for entry in truths if entry["op"] == "close"):
        previous = next(
            entry
            for entry in truths
            if entry["contextId"] == close["contextId"]
            and entry["op"] == "assert"
            and entry["validFrom"] == close["validFrom"]
        )
        assert previous["validTo"] is None
        assert close["validTo"] == close["available"]
        assert close["version"] != previous["version"]
    assert all(
        record.get("predicateTruth", []) == []
        and record.get("chainInstances", []) == []
        for record in records
        if not record["messages"]
    )
    assert [
        project(record)
        for record in replay(session.storage.directory / "journal.jsonl").records
    ] == records
    info = header(session.storage.directory)
    assert info["epoch"] == session.scenario.manifest.epoch
    assert info["behaviour"]["extensions"] == [
        "predicate-truth/v1",
        "chain-instance/v1",
    ]
    assert (
        info["behaviour"]["packages"][0]["packageDigest"]
        == session.scenario.engines["behaviour"]["config"]["packages"][0]["package_id"]
    )


def test_unknown_is_recorded_with_diagnostics_without_fabricated_edges() -> None:
    doc = minimal()
    doc["entities"][0]["facts"] = {}
    doc["bindings"]["commands"] = []
    sim = Simulation(load_scenario(doc, base=BASE))
    sim.start()
    truths = [
        entry
        for record in sim.kernel.records
        for entry in project(record).get("predicateTruth", [])
    ]
    assert truths
    assert all(
        entry["status"] == "required_input"
        and entry["value"] is None
        and entry["diagnostics"]
        for entry in truths
    )
    assert not [
        entry
        for record in sim.kernel.records
        for entry in project(record).get("chainInstances", [])
        if entry["lifecycle"] == "transitioned"
    ]
    sim.close()


def sampled_document() -> dict[str, Any]:
    doc = copy.deepcopy(load_scenario("scenarios/predicates-demo.yaml").document)
    role = "original:owner-role:97"
    config = doc["engines"]["predicate"]["config"]
    package = cast(dict[str, Any], minimal()["behaviours"][0]["document"])
    package["id"] = "example.sampled"
    package["predicates"] = {
        "stationary": {
            "profile": "aerograph_sampled/v1",
            "roles": {role: "oo:ActorObservation"},
            "parameters": doc["bindings"]["samples"][0]["parameters"],
            "expression": prepare(config),
            "adapter": {"context": "stationary", "event": "demo.stationary.entered"},
        }
    }
    package["chains"] = {
        "observe": {
            "roles": {"observation": "oo:ActorObservation"},
            "predicate_roles": {"stationary": {role: "observation"}},
            "trigger": {"predicate": "stationary", "edge": "while"},
            "initial": "waiting",
            "states": ["waiting", "done"],
            "terminal": ["done"],
            "transitions": [
                {
                    "id": "complete",
                    "from": "waiting",
                    "on": {"instance": "activated"},
                    "to": "done",
                    "actions": [],
                }
            ],
        }
    }
    package["bindings"] = [
        {
            "id": "observe",
            "chain": "observe",
            "match": {"observation": {"entity": "observation-1"}},
            "multiplicity": "once_per_entity",
            "on_unbind": "retain_until_terminal",
        }
    ]
    package["conflicts"] = []
    package["injection_points"] = []
    doc["behaviours"] = [package]
    doc["engines"]["behaviour"] = {
        "plugin": "behaviour",
        "config": {"capabilities": {}},
    }
    return doc


def test_q6_sampled_adapter_uses_committed_frames() -> None:
    doc = sampled_document()
    sim = Simulation(load_scenario(doc, base=Path("scenarios")))
    sim.start()
    sim.run_until(7_000_000_000)
    frames = sim.kernel.view().sample_frames("stationary")
    truth = [
        entry
        for record in sim.kernel.records
        for entry in project(record).get("predicateTruth", [])
        if entry["op"] == "assert" and entry["contextId"] == "stationary"
    ]
    assert len(truth) == len(frames)
    assert all(entry["profile"] == "aerograph_sampled/v1" for entry in truth)
    assert (
        len(
            [
                entry
                for record in sim.kernel.records
                for entry in project(record).get("chainInstances", [])
                if entry["lifecycle"] == "completed" and entry["op"] == "assert"
            ]
        )
        == 1
    )
    sim.close()


def test_sampled_guard_on_later_timer_retains_committed_evidence() -> None:
    doc = sampled_document()
    # The evidence notification remains pending when the timer reads the guard.
    doc["engines"]["behaviour"]["config"]["message_lag_ns"] = 1_000_000_000
    chain = doc["behaviours"][0]["chains"]["observe"]
    chain["trigger"] = {"timer": "release", "after_ns": 5_500_000_000}
    chain["transitions"][0]["guard"] = "stationary"
    sim = Simulation(load_scenario(doc, base=Path("scenarios")))
    try:
        sim.start()
        # The guard uses the 4-second sample in a separate timer invocation.
        sim.run_until(5_600_000_000)
        completed = [
            entry
            for record in sim.kernel.records
            for entry in project(record).get("chainInstances", [])
            if entry["lifecycle"] == "completed" and entry["op"] == "assert"
        ]
        assert len(completed) == 1
        assert completed[0]["state"] == "done"
        assert completed[0]["available"]["ns"] == "5500000000"
    finally:
        sim.close()


def test_sampled_feedback_requires_declared_positive_return_lag() -> None:
    doc = sampled_document()
    doc["engines"].pop("observations")
    doc["bindings"]["rules"][0]["writer"] = "behaviour"
    doc["bindings"]["lifecycle"][0]["controller"] = "behaviour"
    spec = doc["bindings"]["samples"][0]
    spec["sources"]["original:owner-role:97"] = "behaviour"
    spec["upstream"] = ["behaviour"]
    doc["engines"]["behaviour"]["config"]["produces"] = ["hu.actor.speed_mps"]
    with pytest.raises(KernelError, match="SAMPLE_CYCLE"):
        Simulation(load_scenario(doc, base=Path("scenarios")))
    doc["engines"]["behaviour"]["config"]["message_lag_ns"] = 1
    sim = Simulation(load_scenario(doc, base=Path("scenarios")))
    sim.start()
    sim.close()
