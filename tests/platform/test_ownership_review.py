"""Per-instance model ownership and strict authoring counterexamples."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from aerokernel import CommandRequest, Instant
from aerokernel.state import Fact

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project

POS = "he.aircraft.position_enu_m"
VEL = "aas.p1.velocity_enu_m_s"
ENERGY = "aas.p1.energy_j"


def motion_only(document: dict[str, Any]) -> dict[str, Any]:
    document["entities"] = [e for e in document["entities"] if e["type"] == "oo:UAV"][
        :2
    ]
    document["engines"] = {"motion": document["engines"]["motion"]}
    document["bindings"] = {
        "rules": [{"writer": "motion", "type": "oo:UAV", "fields": [POS, VEL, ENERGY]}],
        "lifecycle": [{"controller": "motion", "type": "oo:UAV"}],
    }
    return document


def test_h3_two_engines_integrate_only_owned_instances(
    document: dict[str, Any],
) -> None:
    motion_only(document)
    config = document["engines"].pop("motion")
    document["engines"] = {
        name: copy.deepcopy(config) for name in ["motion-a", "motion-b"]
    }
    document["bindings"]["rules"] = [
        {"writer": owner, "type": "oo:UAV", "ids": entity, "fields": [POS, VEL, ENERGY]}
        for owner, entity in [("motion-a", "uav-1"), ("motion-b", "uav-2")]
    ]
    document["bindings"]["lifecycle"] = [
        {"controller": owner, "type": "oo:UAV", "ids": entity}
        for owner, entity in [("motion-a", "uav-1"), ("motion-b", "uav-2")]
    ]
    for entity in document["entities"]:
        entity["facts"][VEL] = [1.0, 0.0, 0.0]
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        view = simulation.run_until(1_000_000_000)
        for ref in simulation.scenario.manifest.entities:
            fact = view.field((ref, POS), view.instant)
            assert isinstance(fact, Fact)
            assert fact.value[0] == pytest.approx(1.0)  # type: ignore[index]
            assert fact.producer == {"uav-1": "motion-a", "uav-2": "motion-b"}[ref.id]
    finally:
        simulation.close()


@pytest.mark.parametrize(
    "key,value",
    [("max_speed_m_s", "10"), ("max_accel_m_s2", True), ("lifecycle", "false")],
)
def test_model_does_not_coerce_authored_contract(
    document: dict[str, Any], key: str, value: Any
) -> None:
    motion_only(document)["engines"]["motion"]["config"][key] = value
    with pytest.raises(ValueError, match="kinematic"):
        Simulation(load_scenario(document))


@pytest.mark.parametrize(
    "case",
    [
        "binding_boolean",
        "nested_unknown",
        "presentation_applicability",
        "visual_coercion",
    ],
)
def test_nested_scenario_contracts_are_checked(
    document: dict[str, Any], case: str
) -> None:
    if case == "binding_boolean":
        document["bindings"]["rules"][0]["priority"] = True
    elif case == "nested_unknown":
        document["run"]["quiet_typo"] = 1
    elif case == "presentation_applicability":
        document["presentation"][0]["typeId"] = "oo:Order"
    else:
        document["presentation"][0]["visual"]["scale"] = "2"
    with pytest.raises(ValueError):
        load_scenario(document)


def test_motion_cancellation_finishes_after_bounded_real_braking(
    document: dict[str, Any],
) -> None:
    motion_only(document)
    simulation = Simulation(load_scenario(document))
    simulation.start()
    command = simulation.kernel.submit(
        CommandRequest(
            "aas.motion.move_to",
            "motion",
            Instant(1),
            {"entity": "uav-1", "machine": "manual", "target": [60.0, 0.0, 10.0]},
        )
    )
    view = simulation.run_until(3_000_000_000)
    ref = simulation.scenario.manifest.entities[0]
    velocity = view.field((ref, VEL), view.instant)
    assert isinstance(velocity, Fact) and velocity.value == (10.0, 0.0, 0.0)
    cancellation = simulation.kernel.cancel(command)
    assert cancellation.queued
    simulation.run_until(3_100_000_000)
    assert simulation.kernel.action(command).status == "canceling"
    view = simulation.run_until(5_200_000_000)
    assert simulation.kernel.action(command).status == "canceled"
    velocity = view.field((ref, VEL), view.instant)
    assert isinstance(velocity, Fact) and velocity.value == (0.0, 0.0, 0.0)
    assert not any(
        m["schemaId"] == "aas.motion.arrived"
        for r in simulation.kernel.records
        for m in project(r)["messages"]
    )
    simulation.close()


def test_dynamic_motion_initialization_uses_generation_creation_time(
    document: dict[str, Any],
) -> None:
    document["entities"] = [
        {
            "id": "creator",
            "type": "oo:Order",
            "facts": {"aas.p1.order_state": "submitted"},
        }
    ]
    motion = document["engines"]["motion"]
    motion["config"]["lifecycle"] = False
    motion["config"]["initial_state"] = {
        "position": [0.0, 0.0, 0.0],
        "velocity": [1.0, 0.0, 0.0],
        "energy_j": 10000.0,
    }
    document["engines"] = {
        "motion": motion,
        "creator": {
            "plugin": "workflow",
            "config": {
                "produces": ["aas.p1.order_state"],
                "consumes": [],
                "emits": [],
                "subscribes": [],
                "targets": [],
                "lifecycle": True,
                "machines": [
                    {
                        "entity": "creator",
                        "field": "aas.p1.order_state",
                        "initial": "submitted",
                        "states": {
                            "submitted": {
                                "transitions": [
                                    {"timer_ns": 50_000_000, "to": "executing"}
                                ]
                            },
                            "executing": {
                                "on_enter": [
                                    {
                                        "kind": "create",
                                        "entity": "dynamic",
                                        "type": "oo:UAV",
                                        "facts": {},
                                    }
                                ]
                            },
                        },
                    }
                ],
            },
        },
    }
    document["bindings"] = {
        "rules": [
            {"writer": "motion", "type": "oo:UAV", "fields": [POS, VEL, ENERGY]},
            {"writer": "creator", "type": "oo:Order", "fields": ["aas.p1.order_state"]},
        ],
        "lifecycle": [
            {"controller": "creator", "type": "oo:Order"},
            {"controller": "creator", "type": "oo:UAV"},
        ],
    }
    simulation = Simulation(load_scenario(document))
    simulation.start()
    view = simulation.run_until(100_000_000)
    ref = next(ref for ref in view._store.lives if ref.id == "dynamic")
    position = view.field((ref, POS), view.instant)
    assert isinstance(position, Fact) and position.value[0] == pytest.approx(0.05)  # type: ignore[index]
    simulation.close()
