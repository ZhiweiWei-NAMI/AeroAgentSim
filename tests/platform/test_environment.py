"""Finite, authored weather profiles publish real typed facts at DES boundaries."""

from __future__ import annotations

from typing import Any

import pytest
from aerokernel import Fact, replay

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import ScenarioError, load_scenario
from aeroagentsim.services.projector import project

FIELD = "test.wind"
SECOND = 1_000_000_000


def environment_document(
    document: dict[str, Any], mode: str = "calm"
) -> dict[str, Any]:
    document["registry"]["types"].append(
        {"id": "test:Environment", "parents": [], "abstract": False}
    )
    document["registry"]["fields"].append(
        {
            "id": FIELD,
            "type": "test:Environment",
            "schema": {"type": "vector", "length": 3, "items": {"type": "number"}},
            "metadata": {"unit": "m/s", "frame": "enu", "clock": "canonical"},
        }
    )
    value = [-10.0, 0.0, 0.0] if mode == "constant" else [0.0, 0.0, 0.0]
    profile: dict[str, Any] = {"mode": mode, "value": value}
    if mode == "gust":
        profile["gusts"] = [
            {"start_ns": SECOND, "end_ns": 2 * SECOND, "value": [-10.0, 0.0, 0.0]}
        ]
    document["entities"] = [
        {"id": "weather", "type": "test:Environment", "facts": {FIELD: value}}
    ]
    document["engines"] = {
        "weather": {
            "plugin": "environment",
            "config": {
                "produces": [FIELD],
                "lifecycle": True,
                "profiles": {"weather": {FIELD: profile}},
            },
        }
    }
    document["bindings"] = {
        "rules": [{"writer": "weather", "type": "test:Environment", "fields": [FIELD]}],
        "lifecycle": [{"controller": "weather", "type": "test:Environment"}],
    }
    document["presentation"] = []
    document["run"].update(until_ns=3 * SECOND, advance_ns=SECOND)
    return document


def value(sim: Simulation) -> tuple[float, ...]:
    view = sim.kernel.view()
    ref = sim.scenario.manifest.entities[0]
    fact = view.field((ref, FIELD), view.instant)
    assert isinstance(fact, Fact) and isinstance(fact.value, tuple)
    values: list[float] = []
    for v in fact.value:
        assert isinstance(v, (int, float)) and not isinstance(v, bool)
        values.append(float(v))
    return tuple(values)


@pytest.mark.parametrize("mode", ["calm", "constant"])
def test_steady_profiles_hold_with_no_polling_and_replay(
    document: dict[str, Any], mode: str
) -> None:
    d = environment_document(document, mode)
    scenario = load_scenario(d)
    sim = Simulation(scenario)
    sim.start()
    initial = value(sim)
    sim.run_until(3 * SECOND)
    assert value(sim) == initial
    sim.close()
    facts = [f for record in sim.kernel.records for f in project(record)["facts"]]
    assert len(facts) == 1
    assert not any(
        item.get("kind") == "timer_fire"
        for r in sim.kernel.records
        for item in r["items"]
    )
    again = Simulation(scenario)
    again.start()
    again.run_until(3 * SECOND)
    again.close()
    data = sim.kernel.journal.bytes
    assert data == again.kernel.journal.bytes
    offline = replay(data)
    assert not offline.incomplete
    ref = scenario.manifest.entities[0]
    assert offline.view().field(
        (ref, FIELD), offline.view().instant
    ) == sim.kernel.view().field((ref, FIELD), sim.kernel.view().instant)


def test_gust_boundaries_and_timer_version_causes(document: dict[str, Any]) -> None:
    sim = Simulation(load_scenario(environment_document(document, "gust")))
    sim.start()
    assert value(sim) == (0.0, 0.0, 0.0)
    sim.run_until(SECOND - 1)
    assert value(sim) == (0.0, 0.0, 0.0)
    sim.run_until(SECOND)
    assert value(sim) == (-10.0, 0.0, 0.0)
    sim.run_until(2 * SECOND)
    assert value(sim) == (0.0, 0.0, 0.0)
    sim.close()
    facts = [f for record in sim.kernel.records for f in project(record)["facts"]]
    assert len(facts) == 3 and all(f["causes"] for f in facts[1:])


def test_initial_value_is_generated_from_real_profile(document: dict[str, Any]) -> None:
    d = environment_document(document, "constant")
    d["entities"][0]["facts"] = {}
    sim = Simulation(load_scenario(d))
    sim.start()
    assert value(sim) == (-10.0, 0.0, 0.0)
    sim.close()


def test_adjacent_intervals_and_gust_starting_at_zero(document: dict[str, Any]) -> None:
    d = environment_document(document, "gust")
    d["entities"][0]["facts"] = {}
    d["engines"]["weather"]["config"]["profiles"]["weather"][FIELD]["gusts"] = [
        {"start_ns": 0, "end_ns": SECOND, "value": [-5.0, 0.0, 0.0]},
        {"start_ns": SECOND, "end_ns": 2 * SECOND, "value": [-10.0, 0.0, 0.0]},
    ]
    sim = Simulation(load_scenario(d))
    sim.start()
    assert value(sim) == (-5.0, 0.0, 0.0)
    sim.run_until(SECOND)
    assert value(sim) == (-10.0, 0.0, 0.0)
    sim.run_until(2 * SECOND)
    assert value(sim) == (0.0, 0.0, 0.0)
    sim.close()
    facts = [f for record in sim.kernel.records for f in project(record)["facts"]]
    assert len(facts) == 3


@pytest.mark.parametrize(
    "case",
    [
        "overlap",
        "writer",
        "initial_mismatch",
        "malformed_value",
        "boolean_time",
        "uncovered_slot",
    ],
)
def test_invalid_profile_contracts_are_rejected(
    document: dict[str, Any], case: str
) -> None:
    d = environment_document(document, "gust")
    c = d["engines"]["weather"]["config"]
    profile = c["profiles"]["weather"][FIELD]
    if case == "overlap":
        profile["gusts"].append(
            {"start_ns": SECOND + 1, "end_ns": 3 * SECOND, "value": [-20.0, 0.0, 0.0]}
        )
    elif case == "writer":
        d["bindings"]["rules"][0]["writer"] = "other"
    elif case == "initial_mismatch":
        d["entities"][0]["facts"][FIELD] = [1.0, 0.0, 0.0]
    elif case == "malformed_value":
        profile["gusts"][0]["value"] = "unknown"
    elif case == "boolean_time":
        profile["gusts"][0]["start_ns"] = True
    else:
        c["profiles"]["weather"] = {}
    with pytest.raises(ScenarioError):
        Simulation(load_scenario(d))
