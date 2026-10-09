"""Scalar sampled guards and authored AST validation."""

from __future__ import annotations

from typing import Any

import pytest

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project


def test_sampled_scalar_lte_emits_real_entered_interval(
    document: dict[str, Any],
) -> None:
    cfg = document["engines"]["threshold"]["config"]
    cfg["field"] = cfg["ast"]["args"][0]["field"] = "aas.p1.energy_j"
    cfg["ast"]["args"][0]["path"] = []
    cfg["ast"]["op"] = "lte"
    parameter = cfg["ast"]["args"][1]["parameter"]
    cfg["parameters"][parameter] = 99995.0
    if "value" in cfg:
        cfg["value"] = 99995.0
    cfg.pop("index", None)
    document["bindings"]["samples"][0]["parameters"][parameter] = 99995.0
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
        simulation.run_until(500_000_000)
        events = [
            message
            for record in simulation.kernel.records
            for message in project(record)["messages"]
            if message["schemaId"] == cfg["event"]
        ]
        assert len(events) == 5
        assert all(
            message["payload"]["from_ns"] < message["payload"]["to_ns"]
            for message in events
        )
    finally:
        simulation.close()


def test_invalid_sampled_ast_is_rejected(document: dict[str, Any]) -> None:
    cfg = document["engines"]["threshold"]["config"]
    cfg["ast"]["args"][0]["op"] = "fabricated"
    with pytest.raises(ValueError, match="threshold"):
        Simulation(load_scenario(document))


def test_python_threshold_needs_no_native_source_pin(document: dict[str, Any]) -> None:
    document["engines"]["threshold"]["config"].pop("native_reference")
    simulation = Simulation(load_scenario(document))
    try:
        simulation.start()
    finally:
        simulation.close()
