"""Pack-owned effective-control readers for pinned traffic scenarios."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from aeroagentsim.presentation_controls import Known, Unavailable, known


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: Any) -> tuple[Any, ...] | list[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def read_accident_time(document: Mapping[str, Any]) -> Known | Unavailable:
    sources = []
    for index, package in enumerate(_rows(document.get("behaviours"))):
        package = _mapping(package)
        inline = _mapping(package.get("document", package))
        chain = _mapping(_mapping(inline.get("chains")).get("traffic.incident_report"))
        for transition in _rows(chain.get("transitions")):
            transition = _mapping(transition)
            if transition.get("id") != "schedule":
                continue
            for action in _rows(transition.get("actions")):
                action = _mapping(action)
                if action.get("id") == "accident-time":
                    sources.append((index, action.get("duration_ns")))
    if len(sources) != 1 or type(sources[0][1]) is not int:
        return Unavailable("A unique pinned accident-time schedule duration is required")
    index, duration = sources[0]
    return known(duration / 1e9, {
        "reader": "traffic.accident_time", "behaviour_index": index,
        "chain": "traffic.incident_report", "transition": "schedule",
        "action": "accident-time", "duration_ns": duration,
    })


def read_decision_mode(document: Mapping[str, Any]) -> Known | Unavailable:
    decisions = _mapping(_mapping(document.get("engines")).get("decisions"))
    config = _mapping(decisions.get("config"))
    if decisions.get("plugin") == "traffic_decisions" and config.get("mode") == "stub":
        return known("scripted", {"engine": "decisions", "plugin": "traffic_decisions", "mode": "stub"})
    if decisions.get("plugin") == "langgraph" and _mapping(config.get("provider")).get("mode") == "live":
        return known("live", {"engine": "decisions", "plugin": "langgraph", "provider_mode": "live"})
    return Unavailable("The pinned decision engine does not prove a supported decision mode")


def read_physics_hz(document: Mapping[str, Any]) -> Known | Unavailable:
    engines = _mapping(document.get("engines"))
    road = _mapping(_mapping(engines.get("road_motion")).get("config")).get("step_ns")
    air = _mapping(_mapping(engines.get("air_motion")).get("config")).get("step_ns")
    if type(road) is not int or type(air) is not int or road <= 0 or air != road:
        return Unavailable("Pinned road and air motion steps must be explicit, positive and equal")
    frequency = 1e9 / road
    rounded = round(frequency, 6)
    value = rounded if rounded > 0 and round(1e9 / rounded) == road else frequency
    return known(value, {"reader": "traffic.physics_hz", "road_step_ns": road, "air_step_ns": air})


def _wind(document: Mapping[str, Any], axis: int) -> Known | Unavailable:
    engines = _mapping(document.get("engines"))
    motion = _mapping(engines.get("air_motion"))
    model = _mapping(_mapping(motion.get("config")).get("consumption_model"))
    if motion.get("plugin") != "kinematic" or model.get("plugin") != "wind":
        return Unavailable("Pinned air motion does not consume the traffic wind profile")
    source = _mapping(model.get("config"))
    entity, field = source.get("entity"), source.get("field")
    if not isinstance(entity, str) or not entity or not isinstance(field, str) or not field:
        return Unavailable("The consumed wind entity and field are not explicitly pinned")
    profiles = []
    for name, engine in engines.items():
        engine = _mapping(engine)
        if engine.get("plugin") == "environment":
            fields = _mapping(_mapping(_mapping(engine.get("config")).get("profiles")).get(entity))
            if field in fields and fields[field] is not None:
                profiles.append((name, fields[field]))
    if len(profiles) != 1 or not isinstance(profiles[0][1], Mapping):
        return Unavailable("A unique pinned environment profile for the wind consumer is required")
    name, profile = profiles[0]
    mode = profile.get("mode")
    if not isinstance(mode, str) or mode not in {"calm", "constant", "gust"}:
        return Unavailable("The pinned wind profile mode is not supported")
    vector = profile.get("value")
    row = source.get("vector_index")
    if row is not None:
        if type(row) is not int or not isinstance(vector, (list, tuple)) or not 0 <= row < len(vector):
            return Unavailable("The consumed wind vector row is not readable")
        vector = vector[row]
    if not isinstance(vector, (list, tuple)) or len(vector) not in {2, 3} or any(
        type(component) not in (int, float) or not math.isfinite(component) for component in vector
    ):
        return Unavailable("The pinned wind source is not a finite ENU vector")
    return known(vector[axis], {
        "reader": "traffic.wind_east" if axis == 0 else "traffic.wind_north",
        "consumer": "air_motion", "consumption_model": {"plugin": "wind", "config": source},
        "environment": name, "entity": entity, "field": field, "profile": profile,
    })


def read_wind_east(document: Mapping[str, Any]) -> Known | Unavailable:
    return _wind(document, 0)


def read_wind_north(document: Mapping[str, Any]) -> Known | Unavailable:
    return _wind(document, 1)


CONTROL_READERS = {
    "traffic.accident_time": read_accident_time,
    "traffic.decision_mode": read_decision_mode,
    "traffic.physics_hz": read_physics_hz,
    "traffic.wind_east": read_wind_east,
    "traffic.wind_north": read_wind_north,
}


__all__ = ["CONTROL_READERS"]
