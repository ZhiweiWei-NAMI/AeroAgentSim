"""Pure fleet expansion and the public, procedural parcel-delivery district."""

from __future__ import annotations

import copy
from typing import Any

from aeroagentsim.packs.common import finite, ns, vec

from .overlay import overlay

SECOND = 1_000_000_000
SLOTS = ("allocation", "path_planning", "conflict_resolution", "charging")
DEFAULT_ALGORITHMS = {
    "allocation": "nearest",
    "path_planning": "corridor_astar",
    "conflict_resolution": "altitude_layers",
    "charging": "threshold_return",
}


def expand(document: dict[str, Any]) -> None:
    """Expand explicitly authored fleet prototypes before normal validation.

    The source declaration is retained in the journal; generated entities are
    marked by their authored prefix and recreated on load, including replay of
    a pinned scenario. No hidden defaults or external geometry is consulted.
    """
    if "algorithms" in document:
        algorithms = document["algorithms"]
        if not isinstance(algorithms, dict) or set(algorithms) - set(SLOTS):
            raise ValueError(f"algorithms: slots must be among {', '.join(SLOTS)}")
        # Unset slots take the documented defaults, written back so the pinned
        # scenario (and its journal) records every slot explicitly.
        algorithms = document["algorithms"] = {**DEFAULT_ALGORITHMS, **algorithms}
        if any(type(v) is not str or not v for v in algorithms.values()):
            raise ValueError("algorithms: nonempty plugin names required")
        for engine in document["engines"].values():
            if engine["plugin"] in {"lowalt_tasks", "lowalt_airspace", "lowalt_energy"}:
                engine["config"]["algorithms"] = copy.deepcopy(algorithms)
        if algorithms["allocation"] == "llm":
            from aeroagentsim.agents.langgraph import record_descriptor
            from aeroagentsim.algorithms.llm import engine_config

            if "lowalt_allocation" not in document["engines"]:
                task_config = next(
                    e["config"]
                    for e in document["engines"].values()
                    if e["plugin"] == "lowalt_tasks"
                )
                document["engines"]["lowalt_allocation"] = {
                    "plugin": "langgraph",
                    "config": engine_config(
                        task_config.get("algorithm_config", {}).get("allocation")
                    ),
                }
            messages = document["registry"].setdefault("messages", [])
            descriptor = record_descriptor()
            if not any(m["id"] == descriptor["id"] for m in messages):
                messages.append(descriptor)
    if "generators" not in document:
        return
    generators = document["generators"]
    if not isinstance(generators, dict) or set(generators) != {"fleet"}:
        raise ValueError("generators: supported lowalt fleet declaration required")
    cfg = generators["fleet"]
    if set(cfg) != {"count", "prefix", "type", "facts", "starts", "spacing_m"}:
        raise ValueError(
            "generators.fleet: declare count/prefix/type/facts/starts/spacing_m"
        )
    count = ns(cfg["count"], "fleet count", 1)
    if count > 1000:
        raise ValueError("fleet count exceeds 1000")
    if type(cfg["prefix"]) is not str or not cfg["prefix"]:
        raise ValueError("fleet prefix must be a nonempty string")
    if cfg["type"] != "lowalt:UAV":
        raise ValueError("fleet generator type must be lowalt:UAV")
    starts = tuple(vec(p) for p in cfg["starts"])
    if not starts:
        raise ValueError("fleet starts must be nonempty")
    spacing = finite(cfg["spacing_m"], "fleet spacing", 1)
    prefix = cfg["prefix"]
    document["entities"] = [
        e for e in document["entities"] if not e["id"].startswith(prefix)
    ]
    for index in range(count):
        start = starts[index % len(starts)]
        facts = copy.deepcopy(cfg["facts"])
        facts["lowalt.position"] = [
            start[0],
            start[1] + index // len(starts) * spacing,
            start[2],
        ]
        document["entities"].append(
            {"id": prefix + str(index + 1), "type": cfg["type"], "facts": facts}
        )


def parcel_document(
    fleet_size: int = 6,
    order_rate: float = 0.08,
    allocator: str = "nearest",
    path_planner: str = "corridor_astar",
    wind_speed: float = 2.0,
) -> dict[str, Any]:
    """Authored district in ENU metres, with no dependency on private assets."""
    ns(fleet_size, "fleet size", 1)
    finite(order_rate, "order rate", 1e-12)
    finite(wind_speed, "wind speed", 0)
    algorithms = {
        **DEFAULT_ALGORITHMS,
        "allocation": allocator,
        "path_planning": path_planner,
    }
    common: dict[str, Any] = {
        "tick_ns": SECOND,
        "motion": {
            "target": "motion",
            "move_to": "lowalt.motion.move_to",
            "hold": "lowalt.motion.hold",
            "stop": "lowalt.motion.stop",
        },
        "algorithm_config": {
            "path_planning": {"grid_m": 20.0},
            "charging": {"threshold": 0.2},
        },
    }
    entities: list[dict[str, Any]] = []

    def entity(identity: str, type_name: str, facts: dict[str, Any]) -> None:
        if type_name in {"Site", "Customer", "Vertiport", "Depot", "ChargingPad"}:
            facts = {**facts, "lowalt.site_open": True, "lowalt.site_capacity": 1}
        entities.append({"id": identity, "type": "lowalt:" + type_name, "facts": facts})

    for index, x in enumerate((-240.0, 240.0), 1):
        entity(
            f"vertiport-{index}",
            "Vertiport",
            {"lowalt.site_position": [x, -220.0, 50.0]},
        )
        entity(
            f"pad-{index}",
            "ChargingPad",
            {"lowalt.site_position": [x, -220.0, 50.0], "lowalt.pad_power_w": 5000.0},
        )
    customers = []
    for xindex in range(5):
        for yindex in range(5):
            identity = f"customer-{xindex * 5 + yindex + 1}"
            customers.append(identity)
            # Keep customer endpoints outside the protected central square.
            x = -240.0 + 120 * xindex
            y = -120.0 + 80 * yindex
            if abs(x) < 60 and abs(y) < 60:
                y += 100.0
            entity(identity, "Customer", {"lowalt.site_position": [x, y, 50.0]})
    task_ids = [f"task-{i + 1}" for i in range(100)]
    for identity in task_ids:
        entity(
            identity,
            "Task",
            {
                "lowalt.task_state": "unreleased",
                "lowalt.task_kind": "parcel",
                "lowalt.task_priority": 1,
                "lowalt.task_origin": "vertiport-1",
                "lowalt.task_destination": "customer-1",
                "lowalt.task_created_ns": 0,
                "lowalt.task_vehicle": "",
                "lowalt.task_payload_kg": 1.0,
            },
        )
    entity(
        "central-no-fly",
        "AirspaceZone",
        {
            "lowalt.zone_kind": "no_fly",
            "lowalt.zone_lower": [-55.0, -55.0, 0.0],
            "lowalt.zone_upper": [55.0, 55.0, 120.0],
            "lowalt.zone_start_ns": 0,
            "lowalt.zone_end_ns": 300 * SECOND,
            "lowalt.zone_dynamic": False,
            "lowalt.zone_active": True,
        },
    )
    entity("weather", "Environment", {})
    bindings: dict[str, Any] = {"rules": [], "lifecycle": [], "commands": []}

    def rule(owner: str, type_name: str, fields: list[str]) -> None:
        bindings["rules"].append(
            {
                "writer": owner,
                "type": "lowalt:" + type_name,
                "fields": ["lowalt." + f for f in fields],
            }
        )

    rule("motion", "UAV", ["position", "velocity"])
    rule(
        "energy",
        "UAV",
        ["soc", "charging", "energy_used_j", "distance_m", "motion_energy_j"],
    )
    rule("airspace", "UAV", ["membership", "conflicts", "weather_held"])
    rule(
        "tasks",
        "UAV",
        ["capacity_j", "payload_kg", "payload_capacity_kg", "vehicle_class"],
    )
    rule("tasks", "Site", ["site_position", "site_open", "site_capacity"])
    rule("tasks", "ChargingPad", ["pad_power_w"])
    rule(
        "tasks",
        "Task",
        [
            "task_state",
            "task_kind",
            "task_priority",
            "task_origin",
            "task_destination",
            "task_created_ns",
            "task_vehicle",
            "task_payload_kg",
        ],
    )
    rule(
        "tasks",
        "AirspaceZone",
        [
            "zone_kind",
            "zone_lower",
            "zone_upper",
            "zone_start_ns",
            "zone_end_ns",
            "zone_dynamic",
        ],
    )
    rule("airspace", "AirspaceZone", ["zone_active"])
    rule("environment", "Environment", ["wind"])
    for type_name in ("UAV", "Site", "AirspaceZone", "Task"):
        bindings["lifecycle"].append(
            {"controller": "tasks", "type": "lowalt:" + type_name}
        )
    bindings["lifecycle"].append(
        {"controller": "environment", "type": "lowalt:Environment"}
    )
    return {
        "format": "aeroagentsim.scenario/v1",
        "id": "parcel-delivery",
        "registry": {"snapshot": "registry.snapshot.json", **overlay()},
        "entities": entities,
        "bindings": bindings,
        "algorithms": algorithms,
        "generators": {
            "fleet": {
                "count": fleet_size,
                "prefix": "drone-",
                "type": "lowalt:UAV",
                "starts": [[-240.0, -220.0, 50.0], [240.0, -220.0, 50.0]],
                "spacing_m": 35.0,
                "facts": {
                    "lowalt.velocity": [0.0, 0.0, 0.0],
                    "lowalt.motion_energy_j": 240000.0,
                    "lowalt.capacity_j": 240000.0,
                    "lowalt.soc": 1.0,
                    "lowalt.payload_kg": 0.0,
                    "lowalt.payload_capacity_kg": 3.0,
                    "lowalt.vehicle_class": "parcel",
                    "lowalt.membership": [],
                    "lowalt.conflicts": [],
                    "lowalt.weather_held": False,
                    "lowalt.charging": False,
                    "lowalt.energy_used_j": 0.0,
                    "lowalt.distance_m": 0.0,
                },
            }
        },
        "engines": {
            "motion": {
                "plugin": "kinematic",
                "config": {
                    "model": "enu_point_mass",
                    "type_id": "lowalt:UAV",
                    "position_field": "lowalt.position",
                    "velocity_field": "lowalt.velocity",
                    "energy_field": "lowalt.motion_energy_j",
                    "max_speed_m_s": 20.0,
                    "max_accel_m_s2": 8.0,
                    "energy": {"idle_w": 50.0, "per_m_j": 12.0, "capacity_j": 240000.0},
                    "commands": {
                        "move_to": "lowalt.motion.move_to",
                        "hold": "lowalt.motion.hold",
                        "stop": "lowalt.motion.stop",
                    },
                    "arrival_schema": "lowalt.motion.arrived",
                    "arrival_topic": "lowalt.events",
                    "arrival_payload": {"entity": "$entity", "position": "$position"},
                    "result_fields": {
                        "entity": "entity",
                        "position": "position",
                        "reason": "reason",
                    },
                    "step_ns": SECOND,
                    "lifecycle": False,
                    "frame": {
                        "convention": "enu",
                        "unit": "m",
                        "transform_revision": "lowalt.district/v1",
                    },
                },
            },
            "airspace": {
                "plugin": "lowalt_airspace",
                "config": {
                    **copy.deepcopy(common),
                    "separation_horizontal_m": 20.0,
                    "separation_vertical_m": 8.0,
                    "wind_limits": {"parcel": 12.0},
                    "activation_events": [],
                },
            },
            "energy": {
                "plugin": "lowalt_energy",
                "config": {
                    **copy.deepcopy(common),
                    "idle_w": 50.0,
                    "per_m_j": 12.0,
                    "speed_w_per_mps": 2.0,
                    "payload_w_per_kg": 8.0,
                    "headwind_w_per_mps": 5.0,
                    "low_threshold": 0.2,
                    "charge_radius_m": 2.0,
                    "charging_target_soc": 0.9,
                },
            },
            "tasks": {
                "plugin": "lowalt_tasks",
                "config": {
                    **copy.deepcopy(common),
                    "arrival_radius_m": 0.5,
                    "dispatch_min_soc": 0.2,
                    "charging_resume_soc": 0.9,
                    "generators": [
                        {
                            "kind": "parcel",
                            "tasks": task_ids,
                            "origins": ["vertiport-1", "vertiport-2"],
                            "destinations": customers,
                            "priority": 1,
                            "payload_kg": 1.0,
                            "arrivals": {
                                "mode": "poisson",
                                "rate_per_s": order_rate,
                                "start_ns": 0,
                            },
                        }
                    ],
                },
            },
            "environment": {
                "plugin": "environment",
                "config": {
                    "produces": ["lowalt.wind"],
                    "lifecycle": True,
                    "profiles": {
                        "weather": {
                            "lowalt.wind": {
                                "mode": "constant",
                                "value": [wind_speed, 0.0, 0.0],
                            }
                        }
                    },
                },
            },
        },
        "outputs": {"durability": "flush"},
        "run": {
            "seed": 42,
            "until_ns": 300 * SECOND,
            "advance_ns": 10 * SECOND,
            "pacing": "fast",
        },
        "presentation": [
            {
                "typeId": "lowalt:UAV",
                "positionField": "lowalt.position",
                "frame": "enu",
                "visual": {"kind": "marker", "color": "#50c9ff", "scale": 2.0},
            },
            {
                "typeId": "lowalt:Site",
                "positionField": "lowalt.site_position",
                "frame": "enu",
                "visual": {"kind": "marker", "color": "#ffc658", "scale": 3.0},
            },
        ],
    }
