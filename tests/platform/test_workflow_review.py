"""State-independent child lifetime and explicit DES trigger policies."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from aerokernel.state import Fact

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario


def child_scenario(document: dict[str, Any]) -> tuple[dict[str, Any], str]:
    uav, order = (
        copy.deepcopy(document["entities"][0]),
        copy.deepcopy(document["entities"][5]),
    )
    order["facts"] = {"aas.p1.order_state": "submitted"}
    document["entities"] = [uav, order]
    document["engines"] = {
        "motion": document["engines"]["motion"],
        "operations": {
            "plugin": "workflow",
            "config": {
                "produces": ["aas.p1.order_state"],
                "consumes": [],
                "emits": ["aas.motion.move_to"],
                "subscribes": [],
                "targets": ["motion"],
                "lifecycle": True,
                "machines": [
                    {
                        "entity": order["id"],
                        "field": "aas.p1.order_state",
                        "initial": "submitted",
                        "states": {
                            "submitted": {
                                "on_enter": [
                                    {
                                        "kind": "command",
                                        "id": "move",
                                        "schema": "aas.motion.move_to",
                                        "target": "motion",
                                        "payload": {
                                            "entity": uav["id"],
                                            "machine": order["id"],
                                            "target": [1.0, 0.0, 10.0],
                                        },
                                    }
                                ],
                                "transitions": [
                                    {"to": "executing", "receipt": "accepted"}
                                ],
                            },
                            "executing": {
                                "transitions": [
                                    {"to": "accepted", "receipt": "succeeded"}
                                ]
                            },
                            "accepted": {},
                        },
                    }
                ],
            },
        },
    }
    document["bindings"] = {
        "rules": [
            document["bindings"]["rules"][0],
            {
                "writer": "operations",
                "type": "oo:Order",
                "fields": ["aas.p1.order_state"],
            },
        ],
        "lifecycle": [
            {"controller": "motion", "type": "oo:UAV"},
            {"controller": "operations", "type": "oo:Order"},
        ],
    }
    return document, order["id"]


def test_h1_receipt_lifetime_spans_states(document: dict[str, Any]) -> None:
    document, entity_id = child_scenario(document)
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(2_000_000_000)
        command = next(iter(view._store.actions.states))
        assert view.action(command).status == "succeeded"
        ref = next(
            ref for ref in simulation.scenario.manifest.entities if ref.id == entity_id
        )
        state = view.field((ref, "aas.p1.order_state"), view.instant)
        assert isinstance(state, Fact) and state.value == "accepted"
    finally:
        simulation.close()


def test_predicate_true_on_state_entry(document: dict[str, Any]) -> None:
    document, entity_id = child_scenario(document)
    cfg = document["engines"]["operations"]["config"]
    cfg["consumes"] = ["aas.p1.energy_j"]
    states = cfg["machines"][0]["states"]
    states["submitted"] = {"transitions": [{"to": "executing", "timer_ns": 1}]}
    states["executing"] = {
        "transitions": [
            {
                "to": "accepted",
                "predicate": {
                    "entity": "uav-1",
                    "field": "aas.p1.energy_j",
                    "op": "gte",
                    "value": 10.0,
                },
            }
        ]
    }
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(10)
        ref = next(
            ref for ref in simulation.scenario.manifest.entities if ref.id == entity_id
        )
        fact = view.field((ref, "aas.p1.order_state"), view.instant)
        assert isinstance(fact, Fact) and fact.value == "accepted"
    finally:
        simulation.close()


def test_trigger_categories_do_not_shadow_timer(document: dict[str, Any]) -> None:
    document, entity_id = child_scenario(document)
    cfg = document["engines"]["operations"]["config"]
    cfg["machines"][0]["states"]["submitted"] = {
        "transitions": [
            {"to": "accepted", "timer_ns": 1, "event": "aas.motion.arrived"}
        ]
    }
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(10)
        ref = next(
            ref for ref in simulation.scenario.manifest.entities if ref.id == entity_id
        )
        fact = view.field((ref, "aas.p1.order_state"), view.instant)
        assert isinstance(fact, Fact) and fact.value == "accepted"
    finally:
        simulation.close()


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "cancel", "command": "nonexistent"},
        {"kind": "set", "entity": "uav-1", "field": "aas.p1.energy_j", "value": 12.0},
        {
            "kind": "create",
            "entity": "new",
            "type": "oo:UAV",
            "facts": {"aas.p1.energy_j": "bad"},
        },
        {"kind": "remove", "entity": "uav-1"},
    ],
)
def test_actions_are_prevalidated(
    document: dict[str, Any], action: dict[str, Any]
) -> None:
    document, _ = child_scenario(document)
    document["engines"]["operations"]["config"]["machines"][0]["states"]["executing"][
        "on_enter"
    ] = [action]
    with pytest.raises(ValueError):
        Simulation(load_scenario(document))


def test_workflow_cancel_tracks_child_until_real_cleanup(
    document: dict[str, Any],
) -> None:
    document, entity_id = child_scenario(document)
    document["bindings"]["cancel_sources"] = ["operations"]
    cfg = document["engines"]["operations"]["config"]
    states = cfg["machines"][0]["states"]
    states["submitted"]["on_enter"][0]["payload"]["target"] = [100.0, 0.0, 10.0]
    states["executing"] = {
        "on_enter": [
            {"kind": "cancel", "command": "move", "timeout_ns": 1_000_000_000}
        ],
        "transitions": [
            {
                "to": "accepted",
                "receipt": {
                    "status": "canceled",
                    "children": ["move"],
                    "policy": "all",
                },
            }
        ],
    }
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(2_000_000_000)
        command = next(iter(view._store.actions.states))
        assert view.action(command).status == "canceled"
        ref = next(
            ref for ref in simulation.scenario.manifest.entities if ref.id == entity_id
        )
        state = view.field((ref, "aas.p1.order_state"), view.instant)
        assert isinstance(state, Fact) and state.value == "accepted"
        engine = simulation.kernel._engines["operations"]
        assert not engine.child_commands and not engine.machines[entity_id].timers
    finally:
        simulation.close()


def test_authored_order_resolves_competing_timer_triggers(
    document: dict[str, Any],
) -> None:
    document, entity_id = child_scenario(document)
    cfg = document["engines"]["operations"]["config"]
    cfg["simultaneous_policy"] = "authored_order"
    cfg["machines"][0]["states"]["submitted"] = {
        "transitions": [
            {"to": "accepted", "timer_ns": 1},
            {"to": "executing", "timer_ns": 1},
        ]
    }
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(10)
        ref = next(
            ref for ref in simulation.scenario.manifest.entities if ref.id == entity_id
        )
        fact = view.field((ref, "aas.p1.order_state"), view.instant)
        assert isinstance(fact, Fact) and fact.value == "accepted"
    finally:
        simulation.close()


def test_same_time_predicate_cycle_has_an_explicit_bound(
    document: dict[str, Any],
) -> None:
    document, _ = child_scenario(document)
    cfg = document["engines"]["operations"]["config"]
    cfg["max_entry_transitions"] = 2
    cfg["consumes"] = ["aas.p1.energy_j"]
    predicate = {
        "entity": "uav-1",
        "field": "aas.p1.energy_j",
        "op": "gte",
        "value": 10.0,
    }
    cfg["machines"][0]["states"] = {
        "submitted": {"transitions": [{"to": "executing", "predicate": predicate}]},
        "executing": {"transitions": [{"to": "submitted", "predicate": predicate}]},
    }
    simulation = Simulation(load_scenario(document))
    try:
        with pytest.raises(Exception, match="bounded same-time entry limit"):
            simulation.start()
        assert simulation.kernel._store.faulted
    finally:
        simulation.close()
