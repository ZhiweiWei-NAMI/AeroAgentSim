"""Resolve execution dependencies separately from pure template knob application."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


def execution_document(
    template_id: str, document: dict[str, Any], base: Path
) -> dict[str, Any]:
    """Use the shipped traffic camera on committed city/actor geometry."""
    result = copy.deepcopy(document)
    if template_id == "traffic-accident":
        from aeroagentsim.packs.traffic_accident.narratives import EVENT_NARRATIVES
        from aeroagentsim.services.node_deps import node_modules_root

        from .demo import browser_path

        result["event_narratives"] = {
            **copy.deepcopy(EVENT_NARRATIVES),
            **result.get("event_narratives", {}),
        }
        decisions = result["engines"]["decisions"]
        if decisions["plugin"] == "traffic_decisions":
            decisions["config"]["fixture_path"] = str(base / "fixtures/decisions.json")
        renderer = result["engines"]["capture"]["config"]["renderer"]
        if renderer["mode"] == "traffic_browser":
            modules = node_modules_root()
            renderer["node_modules"] = str(modules)
            renderer["browser_executable"] = browser_path(modules)
            renderer["asset_digest"] = "traffic-lite-city/v1"
            result["engines"]["capture_bridge"]["config"]["asset_digest"] = (
                "traffic-lite-city/v1"
            )
    return result
