"""Console wiring for the shipped traffic demo and explicitly configured city assets."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from aerokernel.values import canonical_json

from .templates import demo_source


def city_assets() -> tuple[Path, dict[str, dict[str, Any]]]:
    setting = os.environ.get("AEROAGENTSIM_TRAFFIC_ASSET_ROOT")
    if not setting:
        raise FileNotFoundError(
            "Set AEROAGENTSIM_TRAFFIC_ASSET_ROOT to the demo's original web/assets directory for city capture"
        )
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
        raise FileNotFoundError("Asset is absent from the pinned demo inventory")
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Demo asset escapes configured root")
    data = path.read_bytes()
    row = files[name]
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ValueError(f"Demo asset differs from its pinned source: {name}")
    return path


def capture_manifest() -> bytes:
    _, files = city_assets()
    scene = json.loads(city_file("scene.json").read_bytes())
    required = {
        "scene.json",
        *(row["url"].removeprefix("/assets/") for row in scene["buildings"]),
    }
    return canonical_json(
        {
            "format": "aeroagentsim.capture-assets/v1",
            "environment": "viewer-default/v1",
            "files": [
                {
                    "url": "/v1/studio/demo-assets/" + name,
                    "sha256": files[name]["sha256"],
                    "byte_count": files[name]["bytes"],
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
    document["engines"]["capture"]["config"]["renderer"]["node_modules"] = str(
        Path(__file__).resolve().parents[3] / "frontend/node_modules"
    )
    if primitive:
        return {"capture_mode": "primitive-test", "city_available": False}
    manifest = capture_manifest()
    digest = hashlib.sha256(manifest).hexdigest()
    console = os.environ.get(
        "AEROAGENTSIM_CONSOLE_URL", "http://127.0.0.1:8002"
    ).rstrip("/")
    capture = document["engines"]["capture"]["config"]
    capture["renderer"] = {
        "mode": "browser",
        "viewer_url": console
        + "/runs?capture=1&capture_assets="
        + console
        + "/v1/studio/demo-capture-assets",
        "node_modules": capture["renderer"]["node_modules"],
        "browser_executable": capture["renderer"]["browser_executable"],
        "timeout_s": 120.0,
    }
    bridge = document["engines"]["capture_bridge"]["config"]
    bridge.update(asset_digest=digest, width=1024, height=768, timeout_s=120.0)
    bridge["camera"] = {
        "revision": "traffic-city-nadir/v1",
        "preset": "actor-nadir",
        "frame": "enu",
        "fov": 55.0,
        "near": 0.15,
        "far": 16000.0,
        "provenance": "committed actor pose; pinned original traffic city meshes",
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
            "attribution": "Original demo city geometry; see pinned city-manifest.json for provenance",
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
