"""Rescue scenario plugin hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aeroagentsim.plugin_api import ScenarioPlugin


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


def plugin() -> ScenarioPlugin:
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


__all__ = ["plugin"]
