"""Built-in scenario hooks and explicitly listed transitional templates."""

from __future__ import annotations

import copy
import importlib.resources
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.plugin_api import ScenarioPlugin, TemplateSource
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


def _operator_mode(document: dict[str, Any], base: Path, value: Any) -> None:
    operator = next(
        stream for stream in document["ingress_streams"] if stream["id"] == "operator"
    )
    operator["initial_watermark_ns"] = {
        "scheduled": document["run"]["until_ns"], "interactive": 0,
    }[value]
    if value == "interactive":
        # Omission is the ingress schema's explicit unlimited-wait policy;
        # null is not a valid numeric timeout. Stop remains available.
        operator.pop("timeout_s", None)


def _rescue_config(document: dict[str, Any]) -> dict[str, Any]:
    return next(row["config"]["rescue"] for row in document["engines"].values()
                if row["plugin"] == "lowalt_tasks" and "rescue" in row["config"])


def _rescue_participation(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    cfg["participation"] = {**cfg.get("participation", {}), "mode": value}
    if value == "filter":
        # This is an actual exported artifact supplied by the experiment owner.
        # No demonstration weights or synthetic substitute are shipped.
        path = Path(cfg["participation"]["filter"])
        cfg["participation"]["filter"] = str((base / path).resolve())


def _rescue_observation(document: dict[str, Any], base: Path, value: Any) -> None:
    import json

    cfg = _rescue_config(document)
    cfg["observation"] = value
    provider = cfg["provider"]
    if provider.get("mode") == "stub":
        responses = provider["responses"]["propose_plan"]
        for response in responses[1:]:
            call = response["message"]["tool_calls"][0]["function"]
            call["arguments"] = json.dumps({"actions": ([{"id": "a3", "role": "relay", "after": []},
                {"id": "a4", "role": "deliver", "after": ["a3"]}] if value == "positive" else []),
                "reason": "Authored stub response to received " + value + " report"})


def _rescue_radio_port(document: dict[str, Any], base: Path, value: Any) -> None:
    _rescue_config(document)["radio"]["port"] = value


def _rescue_radio(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    if value == "off":
        del cfg["radio"]


def _rescue_deadline(document: dict[str, Any], base: Path, value: Any) -> None:
    cfg = _rescue_config(document)
    if value <= cfg["at_s"]:
        raise ValueError("rescue deadline_s must follow the notice")
    cfg["deadline_s"] = value
    # Include the expiry tick, even when no report ever arrives.
    document["run"]["until_ns"] = round(value * 1e9) + document["run"]["advance_ns"]


def _rescue_fleet_variation(document: dict[str, Any], base: Path, value: Any) -> None:
    # Materialization happens after the experiment runner supplies its seed.
    _rescue_config(document)["fleet_variation"] = value


def _template_directory(identifier: str) -> Path:
    """Locate one shipped template, preferring installed package data."""
    installed = importlib.resources.files("aeroagentsim") / "template_data" / identifier
    if (installed / "template.yaml").is_file():
        return Path(str(installed)).resolve()
    source = (
        Path(__file__).resolve().parents[3] / "scenarios" / "templates" / identifier
    )
    if (source / "template.yaml").is_file():
        return source
    raise FileNotFoundError(f"{identifier} template inputs are not installed")


def lowalt() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="lowalt",
        templates=(),
        apply_hooks={"lowalt.operator_mode": _operator_mode},
    )


def traffic() -> ScenarioPlugin:
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


def rescue() -> ScenarioPlugin:
    return ScenarioPlugin(
        name="rescue",
        templates=(),
        apply_hooks={
            "rescue.participation": _rescue_participation,
            "rescue.observation": _rescue_observation,
            "rescue.radio_port": _rescue_radio_port,
            "rescue.radio": _rescue_radio,
            "rescue.deadline_s": _rescue_deadline,
            "rescue.fleet_variation": _rescue_fleet_variation,
        },
    )


def builtin_templates() -> ScenarioPlugin:
    from aeroagentsim.authoring.templates import demo_source
    from aeroagentsim.packs.traffic_accident.knobs import weather_knobs

    # Keep per-id descriptors explicit so templates can move to owning plugins.
    return ScenarioPlugin(
        name="builtin-templates",
        templates=(
            TemplateSource(directory=_template_directory("air-taxi")),
            TemplateSource(directory=_template_directory("city-day")),
            TemplateSource(directory=_template_directory("crop-spraying")),
            TemplateSource(directory=_template_directory("flood-rescue")),
            TemplateSource(directory=_template_directory("food-delivery")),
            TemplateSource(directory=_template_directory("medical-delivery")),
            TemplateSource(directory=_template_directory("parcel-delivery")),
            TemplateSource(directory=_template_directory("powerline-inspection")),
            TemplateSource(directory=_template_directory("security-patrol")),
            TemplateSource(
                directory=_template_directory("traffic-accident"),
                scenario=demo_source("traffic-accident") / "scenario.yaml",
                extra_knobs=weather_knobs,
            ),
        ),
        apply_hooks={},
    )
