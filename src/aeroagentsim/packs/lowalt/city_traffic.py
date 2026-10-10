"""Compose the public traffic chain into one shared low-altitude document."""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.authoring.templates import demo_source


def _refs(value: Any, run_id: str, memo: dict[int, Any] | None = None) -> Any:
    cache: dict[int, Any] = {} if memo is None else memo
    if not isinstance(value, (dict, list)):
        return value
    if id(value) in cache:
        return cache[id(value)]
    if isinstance(value, dict):
        result: Any = {}
        cache[id(value)] = result
        for key, child in value.items():
            result[key] = (
                run_id
                if key == "run_id" and child == "traffic-accident"
                else _refs(child, run_id, cache)
            )
    else:
        result = []
        cache[id(value)] = result
        result.extend(_refs(child, run_id, cache) for child in value)
    return result


def _merge(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = {v["id"]: v for v in left}
    for item in right:
        if item["id"] in result and result[item["id"]] != item:
            raise ValueError(f"composition descriptor disagrees: {item['id']}")
        result[item["id"]] = item
    return list(result.values())


def merge_traffic(document: dict[str, Any], destination: Path) -> None:
    """Materialize public assets and merge owners without another runtime.

    Traffic UAVs stay outside the lowalt dispatcher. Their measured positions
    enter the shared separation detector as protected external aircraft; selected
    lowalt algorithms move the controlled fleet around them.
    """
    source = demo_source("traffic-accident")
    traffic = _refs(
        yaml.safe_load((source / "scenario.yaml").read_text()), document["id"]
    )
    destination.mkdir(parents=True, exist_ok=True)
    snapshot = json.loads((source / "registry.snapshot.json").read_text())
    portable = json.loads(
        Path(__file__).with_name("registry.snapshot.json").read_text()
    )
    for name in ("types", "fields", "messages"):
        snapshot["registry"][name] = _merge(
            snapshot["registry"][name], portable["registry"][name]
        )
        document["registry"][name] = _merge(
            document["registry"][name], traffic["registry"][name]
        )
    for name in ("relations",):
        document["registry"][name] = _merge(
            document["registry"].get(name, []), traffic["registry"][name]
        )
    snapshot.pop("snapshot_id", None)
    (destination / "registry.snapshot.json").write_text(
        json.dumps(snapshot, indent=2) + "\n"
    )
    behaviour = _refs(
        yaml.safe_load((source / "behaviours.yaml").read_text()), document["id"]
    )
    (destination / "traffic.behaviours.yaml").write_text(
        yaml.safe_dump(behaviour, sort_keys=False)
    )
    (destination / "fixtures").mkdir(exist_ok=True)
    shutil.copy(
        source / "fixtures/decisions.json", destination / "fixtures/decisions.json"
    )
    # Keep the full observed chain, including background road occupancy and
    # competing bids. Every entity and relation remains in this shared kernel.
    document["entities"].extend(
        e for e in traffic["entities"] if e["id"] != "wind.region"
    )
    for key, items in traffic["bindings"].items():
        document["bindings"].setdefault(key, []).extend(
            item
            for item in items
            if item.get("writer") != "weather" and item.get("controller") != "weather"
        )
    del traffic["engines"]["weather"]
    wind = traffic["engines"]["air_motion"]["config"]["consumption_model"]["config"]
    wind.update(entity="weather", field="lowalt.wind")
    del wind["vector_index"]
    traffic["engines"]["behaviour"]["config"]["packages"] = [
        {"path": "traffic.behaviours.yaml"}
    ]
    traffic["engines"]["decisions"]["config"]["fixture_path"] = os.path.relpath(
        destination / "fixtures/decisions.json", Path.cwd()
    )
    traffic["engines"]["capture_bridge"]["config"]["pose_fields"]["lowalt:UAV"] = (
        "lowalt.position"
    )
    # The existing real headless camera owner discovers installed Playwright
    # Chromium, or honors AEROAGENTSIM_CHROMIUM. There is no stub capture.
    traffic["engines"]["capture"]["plugin"] = "lowalt_city_capture"
    document["engines"].update(traffic["engines"])
    document["presentation"].extend(traffic["presentation"])
    document["ingress_streams"] = copy.deepcopy(traffic["ingress_streams"])
    air = document["engines"]["airspace"]["config"]
    air["external_vehicles"] = [
        {
            "id": e["id"],
            "position_field": "he.aircraft.position_enu_m",
            "velocity_field": "traffic.uav.velocity_enu_mps",
            "vehicle_class": "incident_response",
            "motion": {
                "target": "air_motion",
                "move_to": "traffic.air.move_to",
                "hold": "traffic.air.hold",
            },
        }
        for e in traffic["entities"]
        if e["type"] == "aas:TrafficUAV"
    ]
    air["zone_exemptions"] = {"incident-no-fly": ["uav.alpha", "uav.bravo"]}
    air["activation_events"].append(
        {
            "schema": "traffic.incident.detected",
            "topic": "traffic.incident.detected",
            "zone": "incident-no-fly",
            "active": True,
        }
    )
