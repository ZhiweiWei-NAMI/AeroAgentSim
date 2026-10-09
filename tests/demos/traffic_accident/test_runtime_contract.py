"""Representative generic selection contract used by the demo award."""

from __future__ import annotations

from pathlib import Path

import pytest

from aeroagentsim.platform import RunSession, Simulation
from aeroagentsim.scenario import load_scenario
from tests.behaviours.test_runtime import BASE, document, emitted
from tests.demos.traffic_accident.test_end_to_end import assert_replay


def test_zero_call_replay_compares_header_and_all_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = RunSession(
        load_scenario(document(injected=True), base=BASE), tmp_path / "run"
    )
    session.run()
    assert_replay(session.storage.directory, monkeypatch)


def test_known_minimum_wins_and_missing_rank_is_recorded() -> None:
    doc = document()
    doc["registry"]["fields"].append(
        {
            "id": "example.task.rank",
            "type": "example:Task",
            "schema": {"type": "number"},
            "metadata": {"role": "task"},
        }
    )
    doc["bindings"]["rules"][0]["fields"].append("example.task.rank")
    doc["registry"]["messages"].append(
        {
            "id": "example.selected",
            "kind": "event",
            "schema": {
                "type": "record",
                "members": {"actor": {"type": "ref"}},
                "required": ["actor"],
                "extra": False,
            },
        }
    )
    doc["entities"].extend(
        [
            {
                "id": "known",
                "type": "example:Task",
                "facts": {"example.task.phase": "completed", "example.task.rank": 5.0},
            },
            {
                "id": "missing",
                "type": "example:Task",
                "facts": {"example.task.phase": "completed"},
            },
        ]
    )
    package = doc["behaviours"][0]
    chain = package["chains"]["queue"]
    chain["roles"]["worker"] = "example:Task"
    chain["transitions"][0]["actions"].append(
        {
            "id": "winner",
            "kind": "emit",
            "schema": "example.selected",
            "topic": "example.selected",
            "payload": {"actor": {"$role": "worker"}},
        }
    )
    binding = package["bindings"][0]
    binding["match"] = {
        "task": {"entity": "task-1"},
        "worker": {"field": "example.task.phase", "equals": "completed"},
    }
    binding["selection"] = {
        "policy": "minimum",
        "field": "example.task.rank",
        "role": "worker",
        "group_roles": ["task"],
        "tie_break": "EntityRef",
        "limit": 1,
    }
    sim = Simulation(load_scenario(doc, base=BASE))
    try:
        sim.start()
        assert [p["actor"]["$ref"]["id"] for p in emitted(sim, "example.selected")] == [
            "known"
        ]
        waiting = [
            p
            for p in emitted(sim, "aas.behaviour.created")
            if p["op"] == "assert" and p["roles"]["worker"]["$ref"]["id"] == "missing"
        ]
        assert len(waiting) == 1 and waiting[0]["status"] == "waiting_inputs"
        assert waiting[0]["requiredInputs"][0]["field"] == "example.task.rank"
    finally:
        sim.close()
