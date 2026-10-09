"""Console wiring for the shipped traffic demo and explicitly configured city assets."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aerokernel.values import canonical_json

from aeroagentsim.services.node_deps import node_modules_root

from .templates import demo_source

CITY_ASSET_ID = "traffic-city-assets/v1"


def city_assets() -> tuple[Path, dict[str, dict[str, Any]]]:
    setting = os.environ.get("AEROAGENTSIM_TRAFFIC_ASSET_ROOT")
    if not setting:
        root = demo_source("traffic-accident") / "inputs/lite-city"
        return root, {
            name: {"asset_id": "traffic-lite-city/v1"}
            for name in ("scene.json", "ATTRIBUTION.txt")
        }
    root = Path(setting).resolve()
    inventory = json.loads(
        (demo_source("traffic-accident") / "inputs/city-manifest.json").read_bytes()
    )
    files = {
        row["path"].removeprefix("web/assets/"): row
        for row in inventory["assets"]
        if row["path"].startswith("web/assets/")
    }
    return root, files


def city_file(name: str) -> Path:
    root, files = city_assets()
    if name not in files:
        raise FileNotFoundError("Asset is absent from the demo inventory")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Demo asset escapes configured root")
    if not path.is_file():
        raise FileNotFoundError(f"Demo asset is missing: {name}")
    return path


def capture_manifest() -> bytes:
    _, files = city_assets()
    scene = json.loads(city_file("scene.json").read_bytes())
    required = {
        "scene.json",
        *(row["url"].removeprefix("/assets/") for row in scene["buildings"] if "url" in row),
    }
    for name in sorted(required):
        city_file(name)
    return canonical_json(
        {
            "format": "aeroagentsim.capture-assets/v1",
            "asset_id": CITY_ASSET_ID,
            "environment": "viewer-default/v1",
            "files": [
                {
                    "url": "/v1/studio/demo-assets/" + name,
                    "asset_id": files[name]["asset_id"],
                }
                for name in sorted(required)
            ],
        }
    )


def configure_console(
    document: dict[str, Any], directory: Path, *, primitive: bool = False
) -> dict[str, Any]:
    """Keep physical assumptions; select the real viewer or an explicit primitive test camera."""
    config = document["engines"]["decisions"]["config"]
    config["fixture_path"] = str(directory / "fixtures/decisions.json")
    capture = document["engines"]["capture"]["config"]
    capture["renderer"]["node_modules"] = str(node_modules_root())
    if primitive:
        return {"capture_mode": "primitive-test", "city_available": False}
    capture_manifest()
    console = os.environ.get(
        "AEROAGENTSIM_CONSOLE_URL", "http://127.0.0.1:8002"
    ).rstrip("/")
    capture = document["engines"]["capture"]["config"]
    lite = not os.environ.get("AEROAGENTSIM_TRAFFIC_ASSET_ROOT")
    if lite:
        # The standalone photo camera uses the same committed footprint data.
        capture["renderer"]["asset_digest"] = "traffic-lite-city/v1"
        for binding in document["presentation"]:
            model = "car" if binding["typeId"] == "aas:TrafficRoadVehicle" else "uav" if binding["typeId"] == "aas:TrafficUAV" else None
            if model is not None:
                binding["visual"] = {"kind": "model", "asset": "procedural:" + model, "scale": 1.0}
        document["engines"]["capture_bridge"]["config"]["asset_digest"] = "traffic-lite-city/v1"
        return {
            "capture_mode": "city", "city_available": True, "city_detail": "lite",
            "catalog_notice": "Type catalog limited to this scenario's snapshot",
            "scene": {
                "id": "Traffic accident · lite city",
                "city": {"kind": "traffic-city", "url": console + "/v1/studio/demo-assets/scene.json"},
                "camera": {"position": [280, 220, 300], "target": [91.95, 0, 93.04]},
                "attribution": "© OpenStreetMap contributors, ODbL 1.0 · procedural buildings and vehicles",
            },
        }
    capture["renderer"] = {
        "mode": "browser",
        "viewer_url": console
        + "/runs?capture=1&capture_assets="
        + console
        + "/v1/studio/demo-capture-assets",
        "node_modules": capture["renderer"]["node_modules"],
        **(
            {"browser_executable": capture["renderer"]["browser_executable"]}
            if "browser_executable" in capture["renderer"]
            else {}
        ),
        "timeout_s": 120.0,
    }
    bridge = document["engines"]["capture_bridge"]["config"]
    bridge.update(asset_digest=CITY_ASSET_ID, width=1024, height=768, timeout_s=120.0)
    bridge["camera"] = {
        "revision": "traffic-city-nadir/v1",
        "preset": "actor-nadir",
        "frame": "enu",
        "fov": 55.0,
        "near": 0.15,
        "far": 16000.0,
        "provenance": "committed actor pose; original traffic city meshes",
    }
    return {
        "capture_mode": "city",
        "city_available": True,
        "scene": {
            "id": "Traffic accident · original city",
            "city": {
                "kind": "traffic-city",
                "url": console + "/v1/studio/demo-assets/scene.json",
            },
            "camera": {"position": [280, 220, 300], "target": [91.95, 0, 93.04]},
            "attribution": "Original demo city geometry; see city-manifest.json for attribution",
        },
    }


def live_decisions(document: dict[str, Any], provider: dict[str, Any]) -> None:
    """Grant model proposals only, using observations of actual authored actors/tasks."""
    from aeroagentsim.agents.langgraph import record_descriptor

    if set(provider) != {"base_url", "model", "api_key_env"} or any(
        not isinstance(value, str) or not value for value in provider.values()
    ):
        raise ValueError("Live provider requires explicit base_url/model/api_key_env")
    candidates = [
        row
        for row in document["entities"]
        if row["facts"].get("traffic.actor.role") == "candidate"
    ]
    old = document["engines"]["decisions"]["config"]
    task_id = old["task_id"] if "task_id" in old else old["options"]["task_id"]
    task_ids = {
        task_id,
        *(
            row["facts"]["traffic.actor.current_task"]["$ref"]["id"]
            for row in candidates
        ),
    }
    tasks = [row for row in document["entities"] if row["id"] in task_ids]
    grants = [
        {"entity": row["id"], "field": field}
        for row in candidates + tasks
        for field in row["facts"]
        if field
        in {
            "traffic.actor.current_task",
            "traffic.task.interruptible",
            "traffic.task.capture_altitude_m",
            "he.aircraft.position_enu_m",
            "he.aircraft.energy_j",
        }
    ]
    document["engines"]["decisions"] = {
        "plugin": "langgraph",
        "config": {
            "factory": "aeroagentsim.authoring.traffic_graph:build_graph",
            "options": {
                "candidates": [row["id"] for row in candidates],
                "task_id": task_id,
            },
            "triggers": [
                {"schema": name, "topic": name}
                for name in ("traffic.incident.detected", "traffic.broadcast")
            ],
            "grants": {
                "fields": grants,
                "relations": [],
                "commands": [],
                "facts": [],
                "events": [
                    {"schema": name, "topic": name}
                    for name in ("traffic.proposal.report", "traffic.proposal.bid")
                ],
            },
            "budget": {
                "wall_timeout_s": 120,
                "sim_deadline_ns": 1000000000,
                "max_calls": 4,
                "max_tokens": 8000,
                "max_retries": 1,
                "max_prompt_bytes": 100000,
                "recursion_limit": 10,
            },
            "provider": {"mode": "live", **provider},
        },
    }
    messages = document["registry"]["messages"]
    if not any(row["id"] == "aas.langgraph.record" for row in messages):
        messages.append(record_descriptor())
