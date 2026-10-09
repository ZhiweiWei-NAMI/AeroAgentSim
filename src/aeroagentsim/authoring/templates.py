"""Explicit, editable point-mass and passive-workflow authoring templates."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

POS = "aas.studio.position_enu_m"
VEL = "aas.studio.velocity_enu_m_s"
ENERGY = "aas.studio.energy_j"
STATE = "aas.studio.state"
VECTOR = {"type": "vector", "items": {"type": "number"}, "length": 3}


def starter(identifier: str, ontology: Path) -> dict[str, Any]:
    """A blank draft; parameters below are declared simulation assumptions."""
    return {
        "format": "aeroagentsim.scenario/v1",
        "id": identifier,
        "registry": {
            "compile": {
                "root": str(ontology),
                "types": ["oo:ModelObject"],
                "fields": [],
                "relations": [],
            },
            "types": [
                {
                    "id": "aas:StudioFacility",
                    "parents": ["oo:ModelObject"],
                    "abstract": False,
                },
                {
                    "id": "aas:StudioAirspace",
                    "parents": ["oo:ModelObject"],
                    "abstract": False,
                },
            ],
            "fields": [
                {
                    "id": POS,
                    "type": "oo:ModelObject",
                    "schema": VECTOR,
                    "metadata": {
                        "unit": "m",
                        "frame": "enu",
                        "transform_revision": "studio-local-1",
                        "role": "state",
                        "clock": "canonical",
                    },
                },
                {
                    "id": VEL,
                    "type": "oo:ModelObject",
                    "schema": VECTOR,
                    "metadata": {
                        "unit": "m/s",
                        "frame": "enu",
                        "role": "derived",
                        "clock": "canonical",
                    },
                },
                {
                    "id": ENERGY,
                    "type": "oo:ModelObject",
                    "schema": {"type": "number"},
                    "metadata": {"unit": "J", "role": "state", "clock": "canonical"},
                },
                {
                    "id": STATE,
                    "type": "oo:ModelObject",
                    "schema": {"type": "string"},
                    "metadata": {"role": "state", "clock": "canonical"},
                },
                {
                    "id": "aas.studio.airspace",
                    "type": "aas:StudioAirspace",
                    "schema": {
                        "type": "record",
                        "members": {
                            "polygon": {
                                "type": "array",
                                "items": {
                                    "type": "vector",
                                    "length": 2,
                                    "items": {"type": "number"},
                                },
                            },
                            "floor_m": {"type": "number"},
                            "ceiling_m": {"type": "number"},
                        },
                        "required": ["polygon", "floor_m", "ceiling_m"],
                        "extra": False,
                    },
                    "metadata": {"unit": "m", "frame": "enu", "role": "config"},
                },
            ],
            "messages": [],
        },
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "presentation": [],
        "run": {
            "pacing": "fast",
            "seed": 1,
            "until_ns": 2_000_000_000,
            "advance_ns": 100_000_000,
        },
        "outputs": {"durability": "flush"},
    }


def workflow() -> dict[str, Any]:
    return {
        "plugin": "workflow",
        "config": {
            "produces": [],
            "consumes": [],
            "emits": [],
            "subscribes": [],
            "targets": [],
            "lifecycle": True,
            "machines": [],
        },
    }


def motion(type_id: str) -> dict[str, Any]:
    return {
        "plugin": "kinematic",
        "config": {
            "type_id": type_id,
            "position_field": POS,
            "velocity_field": VEL,
            "energy_field": ENERGY,
            "step_ns": 100_000_000,
            "frame": {
                "convention": "enu",
                "unit": "m",
                "transform_revision": "studio-local-1",
            },
            "max_speed_m_s": 10.0,
            "max_accel_m_s2": 5.0,
            "energy": {"capacity_j": 100_000.0, "idle_w": 20.0, "per_m_j": 5.0},
            "commands": {
                "move_to": "aas.studio.move_to",
                "hold": "aas.studio.hold",
                "stop": "aas.studio.stop",
            },
            "arrival_schema": "aas.studio.arrived",
            "arrival_topic": "studio-arrival",
            "lifecycle": True,
            "model": "enu_point_mass",
            "arrival_payload": {"entity": "$entity", "position": "$position"},
            "result_fields": {
                "entity": "entity",
                "position": "position",
                "reason": "reason",
            },
        },
    }


def motion_messages() -> list[dict[str, Any]]:
    result = {
        "type": "record",
        "members": {
            "entity": {"type": "string"},
            "position": VECTOR,
            "reason": {"type": "string"},
        },
        "required": [],
        "extra": False,
    }
    messages = []
    for action in ("move_to", "hold", "stop"):
        members: dict[str, Any] = {"entity": {"type": "string"}}
        if action == "move_to":
            members["target"] = VECTOR
        messages.append(
            {
                "id": "aas.studio." + action,
                "kind": "command",
                "schema": {
                    "type": "record",
                    "members": members,
                    "required": list(members),
                    "extra": False,
                },
                "result_schema": result,
                "feedback_schema": result,
                "cancel_support": True,
            }
        )
    messages.append(
        {
            "id": "aas.studio.arrived",
            "kind": "event",
            "schema": {
                "type": "record",
                "members": {"entity": {"type": "string"}, "position": VECTOR},
                "required": ["entity", "position"],
                "extra": False,
            },
        }
    )
    return messages


def demo_source(name: str) -> Path:
    """Locate shipped demo inputs: installed package data first, then the repo."""
    if name != "traffic-accident":
        raise ValueError("template: unknown demo")
    resources = importlib.resources.files("aeroagentsim") / "demo_data" / name
    if (resources / "scenario.yaml").is_file():
        return Path(str(resources))
    # Editable/source checkout: the repository tree is the real resource root.
    source = Path(__file__).resolve().parents[3] / "scenarios" / "demos" / name
    if (source / "scenario.yaml").is_file():
        return source
    raise FileNotFoundError("traffic-accident template inputs are not installed")
