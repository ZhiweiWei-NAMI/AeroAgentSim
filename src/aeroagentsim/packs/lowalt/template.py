"""Pure fleet expansion and the public, procedural parcel-delivery district."""

from __future__ import annotations

import copy
import importlib.resources
import json
from pathlib import Path
from typing import Any

from aeroagentsim.packs.common import finite, ns, vec

from .overlay import overlay
from .rules import apply_rules, configure_detection

SECOND = 1_000_000_000
SLOTS = ("allocation", "path_planning", "conflict_resolution", "charging")
DEFAULT_ALGORITHMS = {
    "allocation": "nearest",
    "path_planning": "corridor_astar",
    "conflict_resolution": "altitude_layers",
    "charging": "threshold_return",
}


def vehicle_facts() -> dict[str, Any]:
    """The standard parcel UAV initial state, also used by explicit fleets."""
    return {
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
        "lowalt.wind_limit_mps": 12.0,
        "lowalt.returning_to_charge": False,
    }


def components_document() -> dict[str, Any]:
    """Extract the executable base from the public template, without its district.

    A wind environment is required by the committed snapshot contract. There
    are no facilities, vehicles, zones, tasks or fleet generators in this base.
    """
    document = parcel_document(fleet_size=1, wind_speed=0.0)
    document["entities"] = [e for e in document["entities"] if e["type"] == "lowalt:Environment"]
    document["engines"]["tasks"]["config"]["generators"] = []
    del document["generators"]
    return document


def has_lowalt_components(document: dict[str, Any]) -> bool:
    engines = document.get("engines", {}).values()
    plugins = [engine.get("plugin") for engine in engines]
    return (
        all(plugins.count(plugin) == 1 for plugin in ("lowalt_tasks", "lowalt_airspace", "lowalt_energy", "behaviour"))
        and any(engine.get("plugin") == "kinematic" and engine.get("config", {}).get("type_id") == "lowalt:UAV" for engine in engines)
        and any(engine.get("plugin") == "environment" and any("lowalt.wind" in profile for profile in engine.get("config", {}).get("profiles", {}).values()) for engine in engines)
        and set(SLOTS) <= document.get("algorithms", {}).keys()
        and any(package.get("document", package).get("id") == "lowalt.rules" for package in document.get("behaviours", []))
    )


def install_components(document: dict[str, Any], *, directory: Path) -> None:
    """Install the standard lowalt base while preserving existing authoring.

    Generic engine and entity ids can already be occupied in a blank project;
    remap the new component's references instead of replacing those objects.
    """
    if has_lowalt_components(document):
        return
    if any(engine.get("plugin") in {"lowalt_tasks", "lowalt_airspace", "lowalt_energy"} for engine in document["engines"].values()):
        raise ValueError("add_lowalt_components: existing lowalt components are incomplete; repair their configuration / 已有低空组件不完整，请修复其配置")
    base = components_document()
    registry = document["registry"]
    required_types = sorted({parent for row in base["registry"]["types"] for parent in row["parents"] if not parent.startswith("lowalt:")})
    if "compile" in registry:
        selected = registry["compile"]["types"]
        selected.extend(identity for identity in required_types if identity not in selected)
    else:
        # Snapshot-only installations use the same shipped, source-derived
        # ancestor closure as the public lowalt templates, never invented types.
        installed_path = importlib.resources.files("aeroagentsim") / "template_data" / "parcel-delivery" / "registry.snapshot.json"
        source = Path(str(installed_path)) if installed_path.is_file() else Path(__file__).resolve().parents[4] / "scenarios/templates/parcel-delivery/registry.snapshot.json"
        fragment = json.loads(source.read_text(encoding="utf-8"))
        snapshot_path = Path(registry["snapshot"])
        path = snapshot_path if snapshot_path.is_absolute() else directory / snapshot_path
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("add_lowalt_components: snapshot must be inside plan files / 快照须位于计划目录内")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        types = snapshot["registry"]["types"]
        known = {row["id"]: row for row in types}
        for row in fragment["registry"]["types"]:
            if row["id"] in known and known[row["id"]] != row:
                raise ValueError(f"add_lowalt_components: conflicting source type {row['id']} / 来源类型冲突")
            if row["id"] not in known:
                types.append(copy.deepcopy(row))
                details = snapshot.setdefault("details", {})
                details.setdefault("effective_fields", {})[row["id"]] = fragment["details"]["effective_fields"][row["id"]]
        snapshot.setdefault("details", {})["lowalt_components_source"] = copy.deepcopy(fragment["details"]["provenance"])
        if "types" in snapshot["details"].get("statistics", {}):
            snapshot["details"]["statistics"]["types"] = len(types)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")
    engines = document["engines"]
    behaviour_ids = [key for key, engine in engines.items() if engine["plugin"] == "behaviour"]
    if len(behaviour_ids) > 1:
        raise ValueError("add_lowalt_components: one behaviour engine required / 行为引擎须唯一")

    def free_id(preferred: str, used: set[str]) -> str:
        candidate = preferred
        index = 1
        while candidate in used:
            candidate = f"lowalt-{preferred}-{index}"
            index += 1
        used.add(candidate)
        return candidate

    used = set(engines)
    identities = {
        key: behaviour_ids[0] if key == "behaviour" and behaviour_ids else free_id(key, used)
        for key in base["engines"]
    }

    def references(value: Any) -> Any:
        if isinstance(value, list):
            return [references(item) for item in value]
        if isinstance(value, dict):
            return {key: identities.get(item, item) if key in {"target", "writer", "controller"} and isinstance(item, str) else references(item)
                    for key, item in value.items()}
        return value

    for category, rows in base["registry"].items():
        if category == "snapshot":
            continue
        current = document["registry"].setdefault(category, [])
        known = {row["id"]: row for row in current}
        for row in rows:
            if row["id"] in known and known[row["id"]] != row:
                raise ValueError(f"add_lowalt_components: conflicting {category} descriptor {row['id']} / 低空描述符冲突")
            if row["id"] not in known:
                current.append(copy.deepcopy(row))
    for key, engine in base["engines"].items():
        installed = references(engine)
        if key == "behaviour" and behaviour_ids:
            config = engines[identities[key]]["config"]
            for capability, declaration in installed["config"]["capabilities"].items():
                existing = config.setdefault("capabilities", {}).get(capability)
                if existing is not None and existing != declaration:
                    raise ValueError(f"add_lowalt_components: conflicting capability {capability} / 行为能力冲突")
                config["capabilities"][capability] = declaration
            document.setdefault("behaviours", []).extend(config.pop("packages", []))
        else:
            engines[identities[key]] = installed
    weather = base["entities"][0]
    weather_id = free_id(weather["id"], {entity["id"] for entity in document["entities"]})
    profiles = engines[identities["environment"]]["config"]["profiles"]
    profiles[weather_id] = profiles.pop(weather["id"])
    document["entities"].append({**weather, "id": weather_id})
    for category, rows in base["bindings"].items():
        document["bindings"].setdefault(category, []).extend(references(rows))
    document.setdefault("behaviours", []).extend(copy.deepcopy(base["behaviours"]))
    document["algorithms"] = copy.deepcopy(base["algorithms"])
    document["presentation"].extend(copy.deepcopy(base["presentation"]))


def expand(document: dict[str, Any]) -> None:
    """Expand explicitly authored fleet prototypes before normal validation.

    The source declaration is retained in the journal; generated entities are
    marked by their authored prefix and recreated on load, including replay of
    a pinned scenario. No hidden defaults or external geometry is consulted.
    """
    # Old pinned scenarios keep their business configuration while gaining the
    # optional journal-only lineage contract used by current task engines.
    messages = document["registry"].get("messages", [])
    if any(item["id"].startswith("lowalt.") for item in messages):
        wanted = {item["id"]: item for item in overlay()["messages"]}
        types = {item["id"] for item in document["registry"].get("types", [])}
        # Food-only descriptors also exist in other lowalt registries. Their
        # uninhabited restaurant type must not become a subject declaration.
        for descriptor in wanted.values():
            if "subjects" in descriptor:
                descriptor["subjects"] = [row for row in descriptor["subjects"] if row["type_id"] in types]
        for item in messages:
            descriptor = wanted.get(item["id"], {})
            schema = descriptor.get("schema", {})
            if "provenance" in schema.get("members", {}):
                item["schema"]["members"]["provenance"] = copy.deepcopy(schema["members"]["provenance"])
            if "subjects" in descriptor:
                item["subjects"] = copy.deepcopy(descriptor["subjects"])
        known = {item["id"] for item in messages}
        messages.extend(copy.deepcopy(item) for key, item in wanted.items() if key not in known)
    if "generators" in document or any(p.get("document", p).get("id") == "lowalt.rules" for p in document.get("behaviours", [])):
        configure_detection(document)
    # In live operator mode the source supplies these events. Retaining the
    # scheduled demonstration as well would dispatch the same one-shot task
    # twice (e.g. investigate at 55 s after a live investigation).
    demonstrations = {
        "security-patrol": {(55 * SECOND, "investigate")},
        "city-day": {(120 * SECOND, "close-vertiport"), (160 * SECOND, "add-urgent-order")},
    }.get(document["id"], set())
    if demonstrations and any(stream["id"] == "operator" and stream["initial_watermark_ns"] == 0 for stream in document.get("ingress_streams", [])):
        document["bindings"]["commands"] = [command for command in document["bindings"].get("commands", []) if not (
            command["schema"] == "aas.runtime.inject_event" and command["target"] == "behaviour"
            and (command["at_ns"], command["payload"].get("injection_point")) in demonstrations
        )]
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
        from .cooperation import expand as expand_cooperation

        expand_cooperation(document)
        return
    generators = document["generators"]
    if not isinstance(generators, dict) or set(generators) != {"fleet"}:
        raise ValueError("generators: supported lowalt fleet declaration required")
    cfg = generators["fleet"]
    optional = {"label", "label_zh", "business", "vehicle_class"}
    if not isinstance(cfg, dict) or set(cfg) - optional != {
        "count",
        "prefix",
        "type",
        "facts",
        "starts",
        "spacing_m",
    }:
        raise ValueError(
            "generators.fleet: declare count/prefix/type/facts/starts/spacing_m "
            "with optional label/label_zh/business/vehicle_class"
        )
    for name in optional & set(cfg):
        if type(cfg[name]) is not str or not cfg[name]:
            raise ValueError(f"generators.fleet.{name} must be a nonempty string")
    # The motion/energy class stays the authored lowalt.vehicle_class fact; an
    # optional metadata class must agree with it instead of overriding it.
    if (
        "vehicle_class" in cfg
        and cfg["facts"].get("lowalt.vehicle_class") != cfg["vehicle_class"]
    ):
        raise ValueError(
            "generators.fleet.vehicle_class: must agree with the authored "
            "lowalt.vehicle_class fact"
        )
    for name in ("label", "label_zh"):
        if name in cfg:
            try:
                cfg[name].format(index=1)
            except (IndexError, KeyError, ValueError) as exc:
                raise ValueError(
                    f"generators.fleet.{name}: {{index}} format string required ({exc})"
                ) from exc
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
    # Re-expansion regenerates the fleet in the slot it already occupied, so
    # entities appended later (cooperation sites, handoffs) keep their order.
    slot = next(
        (i for i, e in enumerate(document["entities"]) if e["id"].startswith(prefix)),
        len(document["entities"]),
    )
    document["entities"] = [
        e for e in document["entities"] if not e["id"].startswith(prefix)
    ]
    fleet: list[dict[str, Any]] = []
    for index in range(count):
        start = starts[index % len(starts)]
        facts = copy.deepcopy(cfg["facts"])
        facts["lowalt.position"] = [
            start[0],
            start[1] + index // len(starts) * spacing,
            start[2],
        ]
        generated = {
            "id": prefix + str(index + 1),
            "type": cfg["type"],
            "facts": facts,
        }
        for name in optional & set(cfg):
            # {index} is 1-based; no other field is substituted or defaulted.
            value = cfg[name]
            if name in {"label", "label_zh"}:
                value = value.format(index=index + 1)
            generated[name] = value
        fleet.append(generated)
    document["entities"][slot:slot] = fleet

    from .cooperation import expand as expand_cooperation

    expand_cooperation(document)


def parcel_document(
    fleet_size: int = 8,
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
    return apply_rules({
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
                "facts": vehicle_facts(),
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
                                "end_ns": 180 * SECOND,
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
    })
