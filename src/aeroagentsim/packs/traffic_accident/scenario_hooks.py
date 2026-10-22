"""Traffic-accident scenario plugin hooks."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.plugin_api import ScenarioPlugin
from aeroagentsim.scenario.loader import UniqueLoader


def _accident_time(document: dict[str, Any], base: Path, value: Any) -> None:
    packages = document["behaviours"]
    package = packages[0]
    if "path" in package:
        package = yaml.load((base / package["path"]).read_bytes(), Loader=UniqueLoader)
    elif "document" in package:
        package = package["document"]
    package = copy.deepcopy(package)
    transitions = package["chains"]["traffic.incident_report"]["transitions"]
    action = next(
        action
        for transition in transitions
        if transition["id"] == "schedule"
        for action in transition["actions"]
        if action["id"] == "accident-time"
    )
    duration_ns = round(value * 1e9)
    if duration_ns >= document["run"]["until_ns"]:
        raise ValueError(
            "knob accident_time_s: accident must occur before the run horizon"
        )
    action["duration_ns"] = duration_ns
    document["behaviours"] = [package, *packages[1:]]


def _decision_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    if value == "live":
        from aeroagentsim.authoring.demo import live_decisions

        live_decisions(document, {"profile": "live-llm"})
    else:
        document["engines"]["decisions"]["config"]["fixture_path"] = str(
            base / "fixtures/decisions.json"
        )


def _physics_hz(document: dict[str, Any], base: Path, value: Any) -> None:
    step_ns = round(1e9 / value)
    document["engines"]["road_motion"]["config"]["step_ns"] = step_ns
    document["engines"]["air_motion"]["config"]["step_ns"] = step_ns
    document["run"]["advance_ns"] = step_ns


def _wind_east(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 0, value)


def _wind_north(document: dict[str, Any], base: Path, value: Any) -> None:
    from aeroagentsim.packs.traffic_accident.knobs import set_wind

    set_wind(document, 1, value)


def plugin() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="traffic",
        templates=(),
        apply_hooks={
            "traffic.accident_time": _accident_time,
            "traffic.decision_mode": _decision_mode,
            "traffic.physics_hz": _physics_hz,
            "traffic.wind_east": _wind_east,
            "traffic.wind_north": _wind_north,
        },
    )


__all__ = ["plugin"]
