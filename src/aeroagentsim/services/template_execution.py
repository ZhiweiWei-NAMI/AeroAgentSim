"""Resolve execution dependencies separately from pure template knob application."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def snapshot_template_scene(
    template_id: str, document: dict[str, Any], run: Path
) -> None:
    """Attach the shipped city used by the template's actual camera renderer."""
    if template_id != "traffic-accident" or document.get("city") != "huangpu" or (run / "studio-scene.json").is_file():
        return
    renderer = document["engines"]["capture"]["config"]["renderer"]
    if renderer["mode"] != "traffic_browser":
        return
    from aeroagentsim.authoring.demo import city_presentation

    scene = city_presentation()
    (run / "console-scene.json").write_text(json.dumps(scene), encoding="utf-8")
    (run / "scene-source.json").write_text(json.dumps({"kind": "fixed-city", "id": "huangpu", "attribution": scene["attribution"]}), encoding="utf-8")


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
            from aeroagentsim.authoring.demo import CITY_ASSET_ID, detailed_city

            city_id = CITY_ASSET_ID if detailed_city() else "traffic-lite-city/v1"
            renderer["asset_digest"] = city_id
            result["engines"]["capture_bridge"]["config"]["asset_digest"] = (
                city_id
            )
    return result
