"""Explicit domain-only harness; it does not stand in for behaviour execution."""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
import yaml
from aerokernel import EntityRef

from aeroagentsim.platform.plugins import BUILTINS

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios/demos/traffic-accident"


@pytest.fixture(autouse=True)
def domain_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, module in (
        ("traffic_road_motion", "road_motion"),
        ("traffic_route_inputs", "route_inputs"),
        ("traffic_assessment", "traffic_assessment"),
    ):
        monkeypatch.setitem(
            BUILTINS, name, "aeroagentsim.packs.traffic_accident." + module
        )


def document() -> dict[str, Any]:
    return cast(
        dict[str, Any], yaml.safe_load((SCENARIO / "scenario.yaml").read_text())
    )


def inputs() -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((SCENARIO / "inputs/inputs.json").read_text())
    )


def q6() -> ModuleType:
    source = os.environ.get("AEROAGENTSIM_Q6_AST")
    if source is None:
        try:
            return importlib.import_module("aeroagentsim.engines.predicate_ast")
        except ModuleNotFoundError:
            pytest.skip(
                "Q6 not integrated; set AEROAGENTSIM_Q6_AST to its pinned source for this gate"
            )
    spec = importlib.util.spec_from_file_location("traffic_test_q6", source)
    if spec is None or spec.loader is None:
        raise ValueError("explicit Q6 module source cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ref(identity: str, type_id: str) -> dict[str, Any]:
    return {"$ref": EntityRef("traffic-accident", "0", identity, 0, type_id).to_data()}


def domain_document(
    *, roads: tuple[str, ...] = (), air: tuple[str, ...] = (), assessment: bool = False
) -> dict[str, Any]:
    """Select real domain inputs and explicit test-authored target producer.

    Drop all behaviour fields/relations rather than fabricate their transitions.
    Caller supplies physical commands as bootstrap inputs. The capture target is
    the actual frozen authored 70 m target, not a surrogate runtime outcome.
    """
    d = copy.deepcopy(document())
    d.pop("behaviours")
    selected_engines = {"route_inputs", "weather"}
    if roads:
        selected_engines.add("road_motion")
    if air:
        selected_engines.add("air_motion")
    if assessment:
        selected_engines.update({"traffic_assessment", "domain_targets"})
        target_field = "traffic.task.target_enu_m"
        target = next(e for e in d["entities"] if e["id"] == "incident-capture-01")[
            "facts"
        ][target_field]
        d["engines"]["domain_targets"] = {
            "plugin": "environment",
            "config": {
                "produces": [target_field],
                "lifecycle": True,
                "profiles": {
                    "incident-capture-01": {
                        target_field: {"mode": "constant", "value": target}
                    }
                },
            },
        }
        d["bindings"]["rules"].append(
            {
                "writer": "domain_targets",
                "type": "aas:TrafficTask",
                "fields": [target_field],
            }
        )
        d["bindings"]["lifecycle"].append(
            {"controller": "domain_targets", "type": "aas:TrafficTask"}
        )
        subjects = d["engines"]["traffic_assessment"]["config"]["subjects"]
        d["engines"]["traffic_assessment"]["config"]["subjects"] = {
            k: v for k, v in subjects.items() if k in air
        }
    d["engines"] = {k: v for k, v in d["engines"].items() if k in selected_engines}
    selected_entities = []
    for e in d["entities"]:
        identity = e["id"]
        if (
            e["type"] == "aas:TrafficRoute"
            or identity == "wind.region"
            or identity in roads
            or identity in air
        ):
            e["facts"] = {
                k: v
                for k, v in e["facts"].items()
                if not k.startswith("traffic.actor.")
            }
            selected_entities.append(e)
        elif assessment and identity == "incident-capture-01":
            e["facts"] = {
                "traffic.task.target_enu_m": e["facts"]["traffic.task.target_enu_m"]
            }
            selected_entities.append(e)
        elif (
            assessment
            and identity.startswith("assessment.01.")
            and "uav." + identity.rsplit(".", 1)[-1] in air
        ):
            selected_entities.append(e)
    d["entities"] = selected_entities
    for kind, owner_key in (("rules", "writer"), ("lifecycle", "controller")):
        d["bindings"][kind] = [
            row for row in d["bindings"][kind] if row[owner_key] in selected_engines
        ]
    d["bindings"]["relations"] = []
    d["presentation"] = []
    if roads:
        cfg = d["engines"]["road_motion"]["config"]
        cfg["actors"] = [a for a in cfg["actors"] if a["id"] in roads]
        cfg["bypass_routes"] = {
            k: v for k, v in cfg["bypass_routes"].items() if k in roads
        }
        for a in cfg["actors"]:
            d["bindings"]["commands"].append(
                {
                    "schema": "traffic.road.follow",
                    "target": "road_motion",
                    "at_ns": 0,
                    "payload": {
                        "actor": ref(a["id"], "aas:TrafficRoadVehicle"),
                        "route_id": a["route_id"],
                        **{
                            k: float(a[k])
                            for k in ("progress_m", "stop_progress_m", "speed_mps")
                        },
                        "loop": a["loop"],
                    },
                }
            )
    return d
