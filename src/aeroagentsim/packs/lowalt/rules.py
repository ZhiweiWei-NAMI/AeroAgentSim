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
OWNED_BY_RULES = ("lowalt.weather_held", "lowalt.wind_limit_mps")
# Detections the airspace engine now publishes instead of emitting events.
DETECTIONS = (
    "lowalt.wind_mps",
    "lowalt.breached_zones",
    "lowalt.conflict_partner",
    "lowalt.conflict_gap_m",
)
FAILED = ["failed", "rejected", "canceled"]


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


def _chain(states: list[str], transitions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "roles": {"uav": UAV},
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


def rules_package(reserve_percent: float = 20.0) -> dict[str, Any]:
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
            "roles": {"uav": UAV},
            "expression": {
                "op": "gt",
                "args": [
                    {"op": "len", "args": [_field("lowalt.breached_zones")]},
                    {"literal": 0},
                ],
            },
        },
        "closer_than_safety_distance": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV},
            "expression": {
                "op": "ne",
                "args": [_field("lowalt.conflict_partner"), {"literal": ""}],
            },
        },
        "wind_above_limit": {
            "profile": "committed_reactive/v1",
            "roles": {"uav": UAV},
            "expression": {
                "op": "gt",
                "args": [_field("lowalt.wind_mps"), _field("lowalt.wind_limit_mps")],
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
                ),
                _transition(
                    "recall-failed",
                    "returning",
                    "waiting",
                    {"receipt": "recall", "status": FAILED},
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
                            zone=_field("lowalt.breached_zones", 0),
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
                    {"predicate": "wind_above_limit", "edge": "entered"},
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
        "revision": 1,
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
                "match": {"uav": {"is_a": UAV}},
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
    engines["tasks"]["config"]["dispatch_min_soc"] = 0.05
    rules = document["bindings"]["rules"]
    for rule in rules:
        if rule["writer"] == "airspace" and rule["type"] == UAV:
            rule["fields"] = [
                f for f in rule["fields"] if f != "lowalt.weather_held"
            ] + [f for f in DETECTIONS if f not in rule["fields"]]
    if not any(r["writer"] == "behaviour" and r["type"] == UAV for r in rules):
        rules.append(
            {"writer": "behaviour", "type": UAV, "fields": list(OWNED_BY_RULES)}
        )
    if existing is None:
        specs.insert(0, rules_package(reserve))
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
