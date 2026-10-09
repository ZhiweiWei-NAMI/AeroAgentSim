"""Nonspatial enum and relation selectors evaluated over actual committed inputs."""

from __future__ import annotations

import copy
from pathlib import Path

from aeroagentsim.platform import Simulation
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.projector import project


def test_enum_entered_profile_over_actual_scanner_states() -> None:
    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    cfg = document["engines"]["threshold"]["config"]
    cfg["ast"]["op"] = "eq"
    cfg["ast"]["args"][0]["field"] = "review.scan_phase"
    cfg["parameters"]["limit_dbm"] = "finished"
    document["bindings"]["samples"][0]["parameters"]["limit_dbm"] = "finished"
    simulation = Simulation(load_scenario(document, base=Path("scenarios")))
    try:
        simulation.start()
        view = simulation.run_until(100_000_000)
        frames = view.sample_frames("review.power_limit")
        known = [f.frame.result["states"]["receiver"]["value"] for f in frames]
        assert known[0] is False and known[-1] is True
        events = [
            m
            for r in simulation.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == "review.spectrum.overlimit"
        ]
        assert len(events) == 1 and events[0]["at"]["ns"] == "2000000"
    finally:
        simulation.close()


def test_relation_presence_uses_actual_record_link() -> None:
    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    cfg = copy.deepcopy(document["engines"]["threshold"]["config"])
    cfg.update(
        context="review.record_link",
        event="review.linked",
        topic="audit",
        parameters={},
        ast={
            "op": "exists",
            "args": [
                {
                    "op": "relation",
                    "relation": "oo:relation:observation-subject",
                    "role": "subject",
                    "direction": "incoming",
                }
            ],
        },
    )
    document["registry"]["messages"].append(
        {
            "id": "review.linked",
            "kind": "event",
            "schema": {
                "type": "record",
                "members": {"subject": {"type": "string"}},
                "required": ["subject"],
                "extra": False,
            },
        }
    )
    document["engines"]["linkwatch"] = {"plugin": "threshold", "config": cfg}
    document["bindings"]["samples"].append(
        {
            "context": "review.record_link",
            "partition": "linkwatch",
            "upstream": ["scanner", "samples", "threshold"],
            "bindings": {"receiver": "receiver"},
            "sources": {"receiver": "samples"},
            "clocks": {"receiver": ["canonical", "canonical"]},
            "parameters": {},
        }
    )
    simulation = Simulation(load_scenario(document, base=Path("scenarios")))
    try:
        simulation.start()
        view = simulation.run_until(100_000_000)
        frames = view.sample_frames("review.record_link")
        assert frames[0].frame.result["states"]["receiver"]["value"] is False
        assert frames[-1].frame.result["states"]["receiver"]["value"] is True
        events = [
            m
            for r in simulation.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == "review.linked"
        ]
        assert len(events) == 1 and int(events[0]["at"]["ns"]) >= 21_000_000
    finally:
        simulation.close()


def test_wrong_native_source_is_diagnostic_not_false() -> None:
    document = load_scenario(Path("scenarios/p1-spectrum.yaml")).document
    document["bindings"]["samples"][0]["sources"]["receiver"] = "rule"
    simulation = Simulation(load_scenario(document, base=Path("scenarios")))
    try:
        simulation.start()
        view = simulation.run_until(100_000_000)
        frames = view.sample_frames("review.power_limit")
        assert frames and all(
            f.frame.result["states"]["receiver"]["status"] == "invalid_input"
            for f in frames
        )
        assert not [
            m
            for r in simulation.kernel.records
            for m in project(r)["messages"]
            if m["schemaId"] == "review.spectrum.overlimit"
        ]
    finally:
        simulation.close()
