"""The shared ``lowalt.rules`` behaviour package: four operational rules.

Every everyday low-altitude template carries this one package, inline under
its single ``plugin: behaviour`` engine. Engines still own their fields and
execute commands; the rules below only decide, and the journal records every
predicate evaluation and chain step with its causes.

* battery reserve  - predicate ``battery_below_reserve`` -> ``lowalt.energy.low``
  and a ``lowalt.energy.return_to_pad`` command, done when charging finishes.
* no-fly zone      - predicate ``inside_no_fly_zone`` -> ``geofence_entered`` and
  a re-route command (``lowalt.airspace.flight_control``).
* separation       - predicate ``closer_than_safety_distance`` -> ``conflict``,
  the conflict-resolution slot's command, ``conflict_resolved`` on its receipt.
* wind limit       - predicate ``wind_above_limit`` -> ``weather.hold``, hold,
  then ``weather.release`` when the predicate exits.
"""

from __future__ import annotations

import copy
from typing import Any

PACKAGE_ID = "lowalt.rules"
UAV = "lowalt:UAV"
TOPIC = "lowalt.events"

CAPABILITIES = {
    "lowalt.energy.return_to_pad": {
        "schema": "lowalt.energy.return_to_pad",
        "target": "energy",
    },
    "lowalt.airspace.resolve_conflict": {
        "schema": "lowalt.airspace.resolve_conflict",
        "target": "airspace",
    },
    "lowalt.airspace.flight_control": {
        "schema": "lowalt.airspace.flight_control",
        "target": "airspace",
    },
}
# Fields the rules package owns (one writer per field).
OWNED_BY_RULES = (
    "lowalt.weather_held",
    "lowalt.wind_limit_mps",
    "lowalt.returning_to_charge",
)
# Detections the airspace engine now publishes instead of emitting events.
DETECTIONS = (
    "lowalt.wind_mps",
    "lowalt.breached_zones",
    "lowalt.conflict_partner",
    "lowalt.conflict_gap_m",
)
FAILED = ["failed", "rejected", "canceled"]
WIND_LIMITS = {
    "parcel": 12.0,
    "medical": 12.0,
    "inspection": 6.0,
    "survey": 6.0,
    "spraying": 5.0,
    "passenger": 15.0,
    "patrol": 12.0,
    "incident_response": 14.0,
}


def _field(name: str, *path: Any) -> dict[str, Any]:
    return {"field": name, "role": "uav", "path": list(path)}


def _vehicle() -> dict[str, Any]:
    return {"$id": "uav"}


def _emit(identifier: str, schema: str, **payload: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": "emit",
        "schema": schema,
        "topic": TOPIC,
        "payload": payload,
    }


def _command(identifier: str, capability: str, **payload: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "kind": "command",
        "capability": capability,
        "payload": payload,
    }


def _transition(
    identifier: str,
    source: str,
    target: str,
    on: dict[str, Any],
    actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "from": source,
        "on": on,
        "to": target,
        "priority": 0,
        "actions": actions or [],
    }


def _chain(
    states: list[str],
    transitions: list[dict[str, Any]],
    *,
    roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "roles": roles if roles is not None else {"uav": UAV},
        "trigger": {"instance": "activated"},
        "preconditions": [],
        "states": states,
        "initial": states[0],
        "terminal": [],
        "transitions": [
            # Arm the instance: it then waits in its first state for a predicate.
            _transition("armed", states[0], states[0], {"instance": "activated"}),
            *transitions,
        ],
    }


def rules_package(
    reserve_percent: float = 20.0, safety_distance: float = 20.0
) -> dict[str, Any]:
    """The package document, with the battery reserve as a predicate parameter."""
    predicates = {
        "battery_below_reserve": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV},
            "parameters": {"reserve_percent": float(reserve_percent)},
            "expression": {
                "op": "lt",
                "args": [
                    {"op": "mul", "args": [_field("lowalt.soc"), {"literal": 100.0}]},
                    {"parameter": "reserve_percent"},
                ],
            },
        },
        "inside_no_fly_zone": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV, "zone": "lowalt:AirspaceZone"},
            "expression": {
                "op": "contains",
                "args": [
                    _field("lowalt.breached_zones"),
                    {"field": "lowalt.zone_id", "role": "zone", "path": []},
                ],
            },
        },
        "closer_than_safety_distance": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV},
            "parameters": {"horizontal_m": float(safety_distance)},
            "expression": {
                "op": "and",
                "args": [
                    {
                        "op": "ne",
                        "args": [_field("lowalt.conflict_partner"), {"literal": ""}],
                    },
                    {
                        "op": "lt",
                        "args": [
                            _field("lowalt.conflict_gap_m", 0),
                            {"parameter": "horizontal_m"},
                        ],
                    },
                ],
            },
        },
        "wind_above_limit": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV},
            "parameters": {
                f"{kind}_limit_mps": limit for kind, limit in WIND_LIMITS.items()
            },
            "expression": {
                "op": "or",
                "args": [
                    {
                        "op": "and",
                        "args": [
                            {
                                "op": "eq",
                                "args": [
                                    _field("lowalt.vehicle_class"),
                                    {"literal": kind},
                                ],
                            },
                            {
                                "op": "gt",
                                "args": [
                                    _field("lowalt.wind_mps"),
                                    {"parameter": f"{kind}_limit_mps"},
                                ],
                            },
                        ],
                    }
                    for kind in WIND_LIMITS
                ],
            },
        },
    }
    chains = {
        "battery_reserve": _chain(
            ["monitoring", "returning", "waiting"],
            [
                _transition(
                    "low",
                    "monitoring",
                    "returning",
                    {"predicate": "battery_below_reserve", "edge": "while"},
                    [
                        {
                            "id": "recalling",
                            "kind": "set",
                            "entity": {"$role": "uav"},
                            "field": "lowalt.returning_to_charge",
                            "value": True,
                        },
                        _emit(
                            "battery-low",
                            "lowalt.energy.low",
                            vehicle=_vehicle(),
                            soc=_field("lowalt.soc"),
                        ),
                        _command(
                            "recall",
                            "lowalt.energy.return_to_pad",
                            vehicle=_vehicle(),
                            reason="battery below reserve",
                        ),
                    ],
                ),
                _transition(
                    "charged",
                    "returning",
                    "monitoring",
                    {"receipt": "recall", "status": "succeeded"},
                    [
                        {
                            "id": "ready",
                            "kind": "set",
                            "entity": {"$role": "uav"},
                            "field": "lowalt.returning_to_charge",
                            "value": False,
                        }
                    ],
                ),
                _transition(
                    "recall-failed",
                    "returning",
                    "waiting",
                    {"receipt": "recall", "status": FAILED},
                    [
                        {
                            "id": "recall-rejected",
                            "kind": "set",
                            "entity": {"$role": "uav"},
                            "field": "lowalt.returning_to_charge",
                            "value": False,
                        }
                    ],
                ),
                _transition(
                    "recharged",
                    "waiting",
                    "monitoring",
                    {"predicate": "battery_below_reserve", "edge": "exited"},
                ),
            ],
        ),
        "no_fly_zone": _chain(
            ["monitoring", "rerouting"],
            [
                _transition(
                    "entered-zone",
                    "monitoring",
                    "rerouting",
                    {"predicate": "inside_no_fly_zone", "edge": "entered"},
                    [
                        _emit(
                            "geofence",
                            "lowalt.airspace.geofence_entered",
                            vehicle=_vehicle(),
                            zone={"$id": "zone"},
                        ),
                        _command(
                            "reroute",
                            "lowalt.airspace.flight_control",
                            vehicle=_vehicle(),
                            action="reroute",
                            reason="no-fly zone",
                        ),
                    ],
                ),
                _transition(
                    "left-zone",
                    "rerouting",
                    "monitoring",
                    {"predicate": "inside_no_fly_zone", "edge": "exited"},
                ),
            ],
            roles={"uav": UAV, "zone": "lowalt:AirspaceZone"},
        ),
        "separation": _chain(
            ["monitoring", "resolving"],
            [
                _transition(
                    "too-close",
                    "monitoring",
                    "resolving",
                    {"predicate": "closer_than_safety_distance", "edge": "while"},
                    [
                        _emit(
                            "conflict",
                            "lowalt.airspace.conflict",
                            a=_vehicle(),
                            b=_field("lowalt.conflict_partner"),
                            horizontal_m=_field("lowalt.conflict_gap_m", 0),
                            vertical_m=_field("lowalt.conflict_gap_m", 1),
                        ),
                        _command(
                            "resolve",
                            "lowalt.airspace.resolve_conflict",
                            a=_vehicle(),
                            b=_field("lowalt.conflict_partner"),
                        ),
                    ],
                ),
                _transition(
                    "separated",
                    "resolving",
                    "monitoring",
                    {"receipt": "resolve", "status": "succeeded"},
                    [
                        _emit(
                            "resolved",
                            "lowalt.airspace.conflict_resolved",
                            a={"$trigger": "result.a"},
                            b={"$trigger": "result.b"},
                            action={"$trigger": "result.action"},
                        )
                    ],
                ),
                _transition(
                    "resolve-failed",
                    "resolving",
                    "monitoring",
                    {"receipt": "resolve", "status": FAILED},
                ),
            ],
        ),
        "wind_limit": _chain(
            ["flying", "held"],
            [
                _transition(
                    "too-windy",
                    "flying",
                    "held",
                    {"predicate": "wind_above_limit", "edge": "while"},
                    [
                        {
                            "id": "grounded",
                            "kind": "set",
                            "entity": {"$role": "uav"},
                            "field": "lowalt.weather_held",
                            "value": True,
                        },
                        _emit(
                            "weather-hold",
                            "lowalt.weather.hold",
                            vehicle=_vehicle(),
                            reason="wind exceeds vehicle limit",
                            wind_mps=_field("lowalt.wind_mps"),
                        ),
                        _command(
                            "hold",
                            "lowalt.airspace.flight_control",
                            vehicle=_vehicle(),
                            action="hold",
                            reason="weather",
                        ),
                    ],
                ),
                _transition(
                    "calm",
                    "held",
                    "flying",
                    {"predicate": "wind_above_limit", "edge": "exited"},
                    [
                        {
                            "id": "cleared",
                            "kind": "set",
                            "entity": {"$role": "uav"},
                            "field": "lowalt.weather_held",
                            "value": False,
                        },
                        _emit(
                            "weather-release",
                            "lowalt.weather.release",
                            vehicle=_vehicle(),
                            reason="wind within vehicle limit",
                            wind_mps=_field("lowalt.wind_mps"),
                        ),
                        _command(
                            "release",
                            "lowalt.airspace.flight_control",
                            vehicle=_vehicle(),
                            action="release",
                            reason="weather",
                        ),
                    ],
                ),
            ],
        ),
    }
    return {
        "format": "aeroagentsim.behaviour-package/v1",
        "id": PACKAGE_ID,
        "revision": 5,
        "registry": {"snapshot": "registry.snapshot.json"},
        "evaluator": {"version": "aerograph-predicate/1", "dialect": "original"},
        "budgets": {
            "max_transitions_per_instance_per_ns": 64,
            "max_instances": 4000,
            "max_timers_per_instance": 8,
            "max_creations_per_instance": 8,
        },
        "predicates": predicates,
        "chains": chains,
        "bindings": [
            {
                "id": name.replace("_", "-"),
                "chain": name,
                "match": {
                    role: (
                        {
                            "is_a": type_id,
                            "field": "lowalt.zone_kind",
                            "in": ["no_fly", "restricted"],
                        }
                        if role == "zone"
                        else {"is_a": type_id}
                    )
                    for role, type_id in chains[name]["roles"].items()
                },
                "variables": {},
                "multiplicity": "once_per_entity",
                "on_unbind": "retain_until_terminal",
            }
            for name in chains
        ],
    }


def _add_missing(
    current: list[dict[str, Any]], wanted: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep placed descriptors as they are; add only the ones a template lacks."""
    known = {item["id"] for item in current}
    return current + [item for item in wanted if item["id"] not in known]


def apply_rules(document: dict[str, Any]) -> dict[str, Any]:
    """Move a lowalt scenario's operational rules into the shared package.

    Idempotent: a document that already carries the package only has its
    registry descriptors refreshed. Returns the document for chaining.
    """
    from .overlay import overlay

    wanted = overlay()
    registry = document["registry"]
    for name in ("fields", "messages"):
        registry[name] = _add_missing(registry.get(name, []), wanted[name])
    engines = document["engines"]
    air = engines["airspace"]["config"]
    energy = engines["energy"]["config"]
    limits = air.pop("wind_limits", None)
    reserve = round(float(energy.pop("low_threshold", 0.2)) * 100.0, 6)
    behaviour = engines.setdefault(
        "behaviour",
        {"plugin": "behaviour", "config": {"produces": [], "capabilities": {}}},
    )
    config = behaviour["config"]
    config.setdefault("capabilities", {})
    config["capabilities"].update(copy.deepcopy(CAPABILITIES))
    # Packages live in the scenario's top-level ``behaviours`` list, where
    # Studio and Inspect read them (the loader prefers it to config.packages).
    specs: list[Any] = document.setdefault("behaviours", [])
    for package in config.pop("packages", []):
        if package not in specs:
            specs.append(package)
    existing = _package(specs)
    if existing is not None:
        reserve = float(
            existing["predicates"]["battery_below_reserve"]["parameters"][
                "reserve_percent"
            ]
        )
    if existing is not None and existing["revision"] < 5:
        replacement = rules_package(reserve, air["separation_horizontal_m"])
        for name, predicate in replacement["predicates"].items():
            parameters = predicate.get("parameters", {})
            previous = existing["predicates"].get(name, {}).get("parameters", {})
            parameters.update(
                {key: previous[key] for key in parameters if key in previous}
            )
        existing.clear()
        existing.update(replacement)
    if limits is not None:
        facts = [e["facts"] for e in document["entities"] if e["type"] == UAV]
        facts.append(document["generators"]["fleet"]["facts"])
        for fact in facts:
            fact.setdefault(
                "lowalt.wind_limit_mps", limits[fact["lowalt.vehicle_class"]]
            )
    for engine in engines.values():
        charging = engine.get("config", {}).get("algorithm_config", {}).get("charging")
        if charging is not None:
            # The reserve predicate decides *when* to recall an aircraft; the
            # charging algorithm is asked only *where* to send it.
            charging["threshold"] = 1.0
    # Dispatch must not race the reactive recall flag's following microstep.
    # Eligibility uses the same authored reserve; only the chain recalls/emits.
    engines["tasks"]["config"]["dispatch_min_soc"] = reserve / 100.0
    rules = document["bindings"]["rules"]
    for rule in rules:
        if rule["writer"] == "airspace" and rule["type"] == UAV:
            rule["fields"] = [
                f for f in rule["fields"] if f != "lowalt.weather_held"
            ] + [f for f in DETECTIONS if f not in rule["fields"]]
    for rule in rules:
        if rule["writer"] == "airspace" and rule["type"] == "lowalt:AirspaceZone":
            rule["fields"] = list(dict.fromkeys([*rule["fields"], "lowalt.zone_id"]))
    if not any(r["writer"] == "behaviour" and r["type"] == UAV for r in rules):
        rules.append(
            {"writer": "behaviour", "type": UAV, "fields": list(OWNED_BY_RULES)}
        )
    for rule in rules:
        if rule["writer"] == "behaviour" and rule["type"] == UAV:
            rule["fields"] = list(dict.fromkeys([*rule["fields"], *OWNED_BY_RULES]))
    facts = [e["facts"] for e in document["entities"] if e["type"] == UAV]
    facts.append(document["generators"]["fleet"]["facts"])
    for fact in facts:
        fact.setdefault("lowalt.returning_to_charge", False)
    if existing is None:
        specs.insert(0, rules_package(reserve, air["separation_horizontal_m"]))
    return document


def _package(specs: list[Any]) -> dict[str, Any] | None:
    for package in specs:
        if isinstance(package, dict) and package.get("id") == PACKAGE_ID:
            return package
    return None


def declare_package(document: dict[str, Any], package: dict[str, Any]) -> None:
    """Add a behaviour package after the shared rules (one behaviour engine)."""
    document.setdefault("behaviours", []).append(package)


def set_reserve(document: dict[str, Any], percent: float) -> None:
    """Author the battery reserve (percent of full charge) on the package."""
    package = _package(document["behaviours"])
    assert package is not None  # noqa: S101 - apply_rules ran first
    package["predicates"]["battery_below_reserve"]["parameters"]["reserve_percent"] = (
        float(percent)
    )


def configure_detection(document: dict[str, Any]) -> None:
    """Use the authored predicate radius for the spatial hash candidate search.

    The hash is cheap detection, not a second policy: the predicate still reads
    committed pair distances and makes the decision. Pinned documents retain
    the derived engine radius so journal replay needs no configuration code.
    """
    package = _package(document.get("behaviours", []))
    if package is not None:
        from aeroagentsim.packs.common import finite

        reserve = package["predicates"]["battery_below_reserve"]["parameters"][
            "reserve_percent"
        ]
        tasks = next(engine["config"] for engine in document["engines"].values() if engine["plugin"] == "lowalt_tasks")
        airspace = next(engine["config"] for engine in document["engines"].values() if engine["plugin"] == "lowalt_airspace")
        tasks["dispatch_min_soc"] = (
            finite(reserve, "battery reserve", 0) / 100.0
        )
        # Three per-UAV policies plus one no-fly instance per protected zone.
        fleet = document.get("generators", {}).get("fleet")
        fleet_size = (fleet["count"] if fleet is not None else 0) + sum(
            entity["type"] == UAV and (fleet is None or not entity["id"].startswith(fleet["prefix"]))
            for entity in document["entities"]
        )
        zones = sum(
            entity["type"] == "lowalt:AirspaceZone"
            and entity["facts"]["lowalt.zone_kind"] in {"no_fly", "restricted"}
            for entity in document["entities"]
        )
        package["budgets"]["max_instances"] = max(
            package["budgets"]["max_instances"], fleet_size * (3 + zones)
        )
        radius = package["predicates"]["closer_than_safety_distance"]["parameters"][
            "horizontal_m"
        ]
        airspace["separation_horizontal_m"] = finite(
            radius, "safety distance", 1e-9
        )
        limits = {
            name: finite(limit, name, 0)
            for name, limit in package["predicates"]["wind_above_limit"][
                "parameters"
            ].items()
        }
        # Expose the actual authored class limit as committed configuration,
        # rather than leave the obsolete WIP fact inconsistent with the knob.
        facts = [e["facts"] for e in document["entities"] if e["type"] == UAV]
        if fleet is not None:
            facts.append(fleet["facts"])
        for fact in facts:
            fact["lowalt.wind_limit_mps"] = limits[
                f"{fact['lowalt.vehicle_class']}_limit_mps"
            ]
