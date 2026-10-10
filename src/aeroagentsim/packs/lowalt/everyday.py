"""Authored everyday missions sharing the same low-altitude engine contracts."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .rules import apply_rules, declare_package, set_reserve
from .template import SECOND, parcel_document

WIND_LIMITS = {"parcel": 12.0, "inspection": 6.0, "spraying": 5.0, "passenger": 15.0}


def _site(
    doc: dict[str, Any],
    identity: str,
    point: list[float],
    kind: str = "Site",
    capacity: int = 1,
) -> None:
    facts: dict[str, Any] = {
        "lowalt.site_position": point,
        "lowalt.site_open": True,
        "lowalt.site_capacity": capacity,
    }
    if kind == "ChargingPad":
        facts["lowalt.pad_power_w"] = 5000.0
    doc["entities"].append({"id": identity, "type": "lowalt:" + kind, "facts": facts})


def _group(
    doc: dict[str, Any],
    kind: str,
    prefix: str,
    times: list[int],
    origins: list[str],
    destinations: list[str],
    priority: int = 1,
    payload: float = 1.0,
) -> None:
    ids = [f"{prefix}-{i + 1}" for i in range(len(times))]
    for identity in ids:
        doc["entities"].append(
            {
                "id": identity,
                "type": "lowalt:Task",
                "facts": {
                    "lowalt.task_state": "unreleased",
                    "lowalt.task_kind": kind,
                    "lowalt.task_priority": priority,
                    "lowalt.task_origin": origins[0],
                    "lowalt.task_destination": destinations[0],
                    "lowalt.task_created_ns": 0,
                    "lowalt.task_vehicle": "",
                    "lowalt.task_payload_kg": payload,
                },
            }
        )
    doc["engines"]["tasks"]["config"]["generators"].append(
        {
            "kind": kind,
            "tasks": ids,
            "origins": origins,
            "destinations": destinations,
            "priority": priority,
            "payload_kg": payload,
            "arrivals": {"mode": "scheduled", "times_ns": [t * SECOND for t in times]},
        }
    )


def _base(identity: str, vehicle_class: str, fleet_size: int = 2) -> dict[str, Any]:
    doc = parcel_document(fleet_size=fleet_size)
    doc["id"] = identity
    doc["entities"] = [e for e in doc["entities"] if e["id"] == "weather"]
    doc["generators"]["fleet"]["facts"]["lowalt.vehicle_class"] = vehicle_class
    doc["generators"]["fleet"]["starts"] = [[-240.0, -100.0, 60.0]]
    cfg = doc["engines"]["tasks"]["config"]
    cfg["generators"] = []
    cfg["work_routes"] = {}
    cfg["vehicle_kinds"] = {
        vehicle_class: [
            "parcel",
            "medical",
            "inspection",
            "spraying",
            "passenger",
            "patrol",
            "incident_response",
        ]
    }
    cfg["pad_dwell_ns"] = 10 * SECOND
    for rule in doc["bindings"]["rules"]:
        if rule["writer"] == "tasks" and rule["type"] == "lowalt:Site":
            rule["fields"] = list(
                dict.fromkeys(
                    rule["fields"] + ["lowalt.site_open", "lowalt.site_capacity"]
                )
            )
    doc["generators"]["fleet"]["facts"]["lowalt.wind_limit_mps"] = 12.0
    _site(doc, "home", [-240.0, -100.0, 60.0], "Vertiport")
    _site(doc, "home-pad", [-240.0, -100.0, 60.0], "ChargingPad")
    return doc


def _gust(doc: dict[str, Any], start: int, end: int, speed: float) -> None:
    doc["engines"]["environment"]["config"]["profiles"]["weather"][
        "lowalt.wind"
    ].update(
        mode="gust",
        gusts=[
            {
                "start_ns": start * SECOND,
                "end_ns": end * SECOND,
                "value": [speed, 0.0, 0.0],
            }
        ],
    )


def _operators(
    doc: dict[str, Any], points: dict[str, str], commands: list[dict[str, Any]]
) -> None:
    package = {
        "format": "aeroagentsim.behaviour-package/v1",
        "id": "lowalt.operator",
        "revision": 1,
        "registry": {"snapshot": "registry.snapshot.json"},
        "evaluator": {"version": "aerograph-predicate/1", "dialect": "original"},
        "budgets": {
            "max_transitions_per_instance_per_ns": 64,
            "max_instances": 100,
            "max_timers_per_instance": 8,
            "max_creations_per_instance": 8,
        },
        "predicates": {},
        "chains": {},
        "bindings": [],
        "injection_points": [
            {
                "id": name,
                "stream_id": "operator",
                "command": "aas.runtime.inject_event",
                "target": "behaviour",
                "emits": schema,
                "topic": "lowalt.events",
            }
            for name, schema in points.items()
        ],
    }
    declare_package(doc, package)
    if "ingress_streams" not in doc:
        doc["ingress_streams"] = [
            {
                "id": "operator",
                "engine_ids": ["behaviour"],
                "mapping_id": "canonical",
                "initial_watermark_ns": doc["run"]["until_ns"],
                "lateness": "reject",
                "timeout_s": 10.0,
            }
        ]
    for command in commands:
        doc["bindings"]["commands"].append(
            {"schema": "aas.runtime.inject_event", "target": "behaviour", **command}
        )


def medical_document() -> dict[str, Any]:
    doc = _base("medical-delivery", "parcel", 2)
    _site(doc, "hospital", [-140.0, -100.0, 60.0], "Vertiport")
    _site(doc, "clinic", [200.0, 150.0, 60.0], "Customer")
    _site(doc, "customer", [240.0, -100.0, 60.0], "Customer")
    _group(doc, "parcel", "routine", [0, 0, 90, 150, 210], ["home"], ["customer"])
    _group(doc, "medical", "urgent", [8, 110, 200], ["hospital"], ["clinic"], 100, 0.5)
    doc["engines"]["tasks"]["config"]["medical_preemption"] = True
    return doc


def inspection_document() -> dict[str, Any]:
    doc = _base("powerline-inspection", "inspection", 1)
    _site(doc, "line-start", [-240.0, 0.0, 60.0])
    _site(doc, "line-end", [240.0, 0.0, 60.0])
    _group(
        doc,
        "inspection",
        "line-survey",
        [0, 130, 220],
        ["line-start"],
        ["line-end"],
        payload=0.0,
    )
    doc["engines"]["tasks"]["config"]["work_routes"] = {
        "inspection": {
            "algorithm": "survey",
            "config": {
                "waypoints": [
                    [-120.0, 0.0, 60.0],
                    [0.0, 0.0, 60.0],
                    [120.0, 0.0, 60.0],
                    [240.0, 0.0, 60.0],
                ]
            },
        }
    }
    doc["engines"]["motion"]["config"]["max_speed_m_s"] = 8.0
    doc["generators"]["fleet"]["facts"]["lowalt.wind_limit_mps"] = 6.0
    _gust(doc, 35, 75, 9.0)
    _zone(
        doc,
        "powerline-corridor",
        "corridor",
        [-260.0, -20.0, 40.0],
        [260.0, 20.0, 80.0],
    )
    return doc


def spraying_document() -> dict[str, Any]:
    doc = _base("crop-spraying", "spraying", 1)
    doc["run"]["until_ns"] = 400 * SECOND
    # The battery rack is at the field edge, not at a remote urban depot.
    for e in doc["entities"]:
        if e["id"] in {"home", "home-pad"}:
            e["facts"]["lowalt.site_position"] = [-100.0, -60.0, 20.0]
    doc["generators"]["fleet"]["starts"] = [[-100.0, -60.0, 20.0]]
    _site(doc, "field-entry", [-80.0, -60.0, 20.0])
    _site(doc, "field-exit", [80.0, 60.0, 20.0])
    _group(doc, "spraying", "field", [0], ["field-entry"], ["field-exit"], payload=2.0)
    doc["engines"]["tasks"]["config"]["work_routes"] = {
        "spraying": {
            "algorithm": "coverage",
            "config": {
                "lower": [-80.0, -60.0],
                "upper": [80.0, 60.0],
                "spacing_m": 30.0,
            },
        }
    }
    doc["engines"]["motion"]["config"]["max_speed_m_s"] = 10.0
    facts = doc["generators"]["fleet"]["facts"]
    facts["lowalt.capacity_j"] = facts["lowalt.motion_energy_j"] = 20000.0
    cfg = doc["engines"]["energy"]["config"]
    cfg.update(
        swap_pads=["home-pad"],
        swap_duration_ns=10 * SECOND,
        charging_target_soc=0.9,
    )
    set_reserve(doc, 45.0)
    doc["generators"]["fleet"]["facts"]["lowalt.wind_limit_mps"] = 5.0
    _gust(doc, 90, 125, 8.0)
    return doc


def _zone(
    doc: dict[str, Any],
    identity: str,
    kind: str,
    lower: list[float],
    upper: list[float],
    start: int = 0,
    end: int = 300,
    dynamic: bool = False,
) -> None:
    doc["entities"].append(
        {
            "id": identity,
            "type": "lowalt:AirspaceZone",
            "facts": {
                "lowalt.zone_kind": kind,
                "lowalt.zone_lower": lower,
                "lowalt.zone_upper": upper,
                "lowalt.zone_start_ns": start * SECOND,
                "lowalt.zone_end_ns": end * SECOND,
                "lowalt.zone_dynamic": dynamic,
                "lowalt.zone_active": start == 0,
            },
        }
    )


def taxi_document() -> dict[str, Any]:
    doc = _base("air-taxi", "passenger", 3)
    _site(doc, "east-port", [240.0, -100.0, 60.0], "Vertiport")
    _site(doc, "north-port", [0.0, 200.0, 60.0], "Vertiport")
    _site(doc, "east-pad", [240.0, -100.0, 60.0], "ChargingPad")
    _site(doc, "north-pad", [0.0, 200.0, 60.0], "ChargingPad")
    facts = doc["generators"]["fleet"]["facts"]
    facts["lowalt.payload_capacity_kg"] = 150.0
    # Starts close enough to reveal genuine separation/layering decisions.
    doc["generators"]["fleet"]["spacing_m"] = 5.0
    _group(
        doc,
        "passenger",
        "flight",
        [0, 0, 0, 80, 80, 150, 150, 220],
        ["home", "east-port", "north-port"],
        ["home", "east-port", "north-port"],
        payload=80.0,
    )
    _zone(
        doc,
        "east-west-corridor",
        "corridor",
        [-260.0, -125.0, 40.0],
        [260.0, -75.0, 120.0],
    )
    _zone(
        doc, "north-corridor", "corridor", [-30.0, -125.0, 40.0], [30.0, 220.0, 120.0]
    )
    doc["engines"]["tasks"]["config"]["work_routes"] = {
        "passenger": {
            "algorithm": "survey",
            "config": {
                "waypoints": [
                    [0.0, -100.0, 60.0],
                    [0.0, 200.0, 60.0],
                    [0.0, -100.0, 60.0],
                ]
            },
        }
    }
    return doc


def patrol_document() -> dict[str, Any]:
    doc = _base("security-patrol", "patrol", 1)
    _site(doc, "guard-return", [-240.0, -100.0, 60.0])
    _site(doc, "incident-site", [80.0, 80.0, 60.0])
    _group(
        doc, "patrol", "patrol", [0, 100, 200], ["home"], ["guard-return"], payload=0.0
    )
    _group(
        doc,
        "incident_response",
        "investigation",
        [10000],
        ["home"],
        ["incident-site"],
        100,
        0.0,
    )
    doc["engines"]["tasks"]["config"]["work_routes"] = {
        "patrol": {
            "algorithm": "patrol",
            "config": {
                "waypoints": [
                    [-240.0, 100.0, 60.0],
                    [-40.0, 100.0, 60.0],
                    [-40.0, -100.0, 60.0],
                    [-240.0, -100.0, 60.0],
                ]
            },
        }
    }
    _operators(
        doc,
        {"investigate": "lowalt.operator.investigate"},
        [
            {
                "at_ns": 55 * SECOND,
                "payload": {
                    "injection_point": "investigate",
                    "payload": {
                        "task": "investigation-1",
                        "origin": "home",
                        "destination": "incident-site",
                    },
                },
            }
        ],
    )
    return doc


def city_document(destination: Path) -> dict[str, Any]:
    from .city_traffic import merge_traffic

    doc = _base("city-day", "parcel", 3)
    doc["run"]["until_ns"] = 300 * SECOND
    doc["algorithms"]["conflict_resolution"] = "hold_and_go"
    # Shared ENU district aligned with the public traffic incident at (92,-93).
    _site(doc, "hospital", [-180.0, -100.0, 60.0], "Vertiport")
    _site(doc, "clinic", [260.0, 80.0, 60.0], "Customer")
    _site(doc, "customer-east", [320.0, -100.0, 60.0], "Customer")
    _site(doc, "east-port", [320.0, -100.0, 60.0], "Vertiport")
    _site(doc, "north-port", [0.0, 200.0, 60.0], "Vertiport")
    _site(doc, "line-start", [-220.0, 180.0, 60.0])
    _site(doc, "line-end", [280.0, 180.0, 60.0])
    _group(
        doc,
        "parcel",
        "parcel",
        [0, 0, 0, 55, 100, 160, 220],
        ["home"],
        ["customer-east"],
    )
    _site(doc, "field-start", [-80.0, 250.0, 20.0])
    _site(doc, "field-end", [80.0, 350.0, 20.0])
    _group(
        doc, "spraying", "city-field", [0], ["field-start"], ["field-end"], payload=2.0
    )
    _group(doc, "medical", "medical", [8, 145], ["hospital"], ["clinic"], 100, 0.5)
    _group(
        doc,
        "inspection",
        "inspection",
        [0, 140],
        ["line-start"],
        ["line-end"],
        payload=0.0,
    )
    _group(
        doc,
        "passenger",
        "taxi",
        [0, 35, 90, 170],
        ["hospital"],
        ["east-port"],
        payload=80.0,
    )
    _group(
        doc, "medical", "operator-order", [10000], ["hospital"], ["clinic"], 100, 0.5
    )
    cfg = doc["engines"]["tasks"]["config"]
    cfg.update(
        medical_preemption=True,
        vehicle_kinds={
            "parcel": ["parcel", "medical"],
            "inspection": ["inspection"],
            "passenger": ["passenger"],
            "spraying": ["spraying"],
        },
        site_diversions={
            "east-port": "north-port",
            "home": "north-port",
            "north-port": "home",
        },
    )
    cfg["work_routes"] = {
        "inspection": {
            "algorithm": "survey",
            "config": {
                "waypoints": [
                    [-100.0, 180.0, 60.0],
                    [0.0, 180.0, 60.0],
                    [120.0, 180.0, 60.0],
                    [280.0, 180.0, 60.0],
                ]
            },
        }
    }
    cfg["work_routes"]["spraying"] = {
        "algorithm": "coverage",
        "config": {"lower": [-80.0, 250.0], "upper": [80.0, 350.0], "spacing_m": 50.0},
    }
    prototype = doc["generators"]["fleet"]["facts"]
    for identity, vehicle_class, start in (
        ("taxi-vehicle", "passenger", [-180.0, -100.0, 60.0]),
        ("line-vehicle", "inspection", [-220.0, 180.0, 60.0]),
        ("field-vehicle", "spraying", [-225.0, 180.0, 60.0]),
    ):
        facts = copy.deepcopy(prototype)
        facts.update(
            {
                "lowalt.position": start,
                "lowalt.vehicle_class": vehicle_class,
                "lowalt.payload_capacity_kg": 150.0
                if vehicle_class == "passenger"
                else 3.0,
                "lowalt.wind_limit_mps": WIND_LIMITS[vehicle_class],
            }
        )
        doc["entities"].append({"id": identity, "type": "lowalt:UAV", "facts": facts})
    doc["engines"]["motion"]["config"]["max_speed_m_s"] = 10.0
    _gust(doc, 65, 105, 9.0)
    _zone(
        doc,
        "incident-no-fly",
        "no_fly",
        [60.0, -125.0, 0.0],
        [125.0, -65.0, 180.0],
        0,
        110,
        True,
    )
    _zone(
        doc, "main-corridor", "corridor", [-260.0, -150.0, 40.0], [350.0, -50.0, 180.0]
    )
    doc["engines"]["airspace"]["config"]["event_only_zones"] = ["incident-no-fly"]
    next(e for e in doc["entities"] if e["id"] == "incident-no-fly")["facts"][
        "lowalt.zone_active"
    ] = False
    merge_traffic(doc, destination)
    # The composed behaviour engine now holds the traffic package; add the
    # shared rules next to it (the operator package is appended below).
    apply_rules(doc)
    doc["bindings"]["rules"].append(
        {
            "writer": "airspace",
            "type": "lowalt:AirspaceZone",
            "ids": "incident-no-fly",
            "priority": 10,
            "fields": ["lowalt.zone_lower", "lowalt.zone_upper"],
        }
    )
    doc["engines"]["airspace"]["config"]["dynamic_zone_centers"] = {
        "incident-no-fly": {
            "entity": "incident.01",
            "field": "traffic.incident.position_enu_m",
            "dimensions_m": [65.0, 60.0, 180.0],
        }
    }
    _operators(
        doc,
        {
            "close-vertiport": "lowalt.operator.close_vertiport",
            "add-urgent-order": "lowalt.operator.add_urgent_order",
        },
        [
            {
                "at_ns": 120 * SECOND,
                "payload": {
                    "injection_point": "close-vertiport",
                    "payload": {"site": "east-port"},
                },
            },
            {
                "at_ns": 160 * SECOND,
                "payload": {
                    "injection_point": "add-urgent-order",
                    "payload": {
                        "task": "operator-order-1",
                        "origin": "hospital",
                        "destination": "clinic",
                    },
                },
            },
        ],
    )
    # The traffic profile uses a finite operator watermark; one shared horizon.
    for stream in doc["ingress_streams"]:
        stream["initial_watermark_ns"] = doc["run"]["until_ns"]
    return doc


BUILDERS = {
    "medical-delivery": medical_document,
    "powerline-inspection": inspection_document,
    "crop-spraying": spraying_document,
    "air-taxi": taxi_document,
    "security-patrol": patrol_document,
}
