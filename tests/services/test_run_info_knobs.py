"""R5-FE: comparison settings are projected from the run's pinned configuration."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel import BindingManifest, Kernel, MemoryRegistry
from fastapi.testclient import TestClient

from aeroagentsim.services.app import create_app
from aeroagentsim.services.storage import RunStorage

WIND = "oo:digital_twin.wind.horizontalVelocity"


def traffic_document(value: Any, *, mode: str = "constant", row: int | None = 0) -> dict[str, Any]:
    profile = {"mode": mode, "value": value}
    if mode == "gust":
        profile["gusts"] = [{"start_ns": 10_000_000_000, "end_ns": 15_000_000_000, "value": [[9, 0]]}]
    return {
        "id": "traffic-accident", "registry": {"messages": []},
        "run": {"until_ns": 90_000_000_000},
        "engines": {
            "air_motion": {"plugin": "kinematic", "config": {
                "step_ns": 66_666_667, "consumption_model": {"plugin": "wind", "config": {
                    "entity": "wind.region", "field": WIND, **({"vector_index": row} if row is not None else {}),
                }},
            }},
            "road_motion": {"config": {"step_ns": 66_666_667}},
            "weather": {"plugin": "environment", "config": {"profiles": {"wind.region": {WIND: profile}}}},
        },
    }


def pinned_run(root: Path, name: str, document: dict[str, Any], *, template: str = "traffic-accident", knobs: dict[str, Any] | None = None) -> Path:
    """Create a small real committed journal; no worker/server or research run is used."""
    path = root / name
    path.mkdir(parents=True)
    (path / "scenario.json").write_text(json.dumps(document))
    registry = MemoryRegistry(())
    (path / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    kernel = Kernel()
    kernel.bind(registry, BindingManifest(name, "0"), ())
    kernel.start()
    kernel.run_until(1)
    kernel.close()
    (path / "journal.jsonl").write_bytes(kernel.journal.bytes)
    (path / "index.json").write_text("[]")
    (path / "manifest.json").write_text(json.dumps({
        "id": name, "status": "completed", "template": {
            "id": template, "title": "Pinned template", "knobs": knobs if knobs is not None else {"wind_east_mps": 3, "wind_north_mps": 0, "physics_hz": 1},
        },
    }))
    RunStorage(path).publish_prefix(*kernel.acknowledged_prefix)
    return path


@pytest.mark.parametrize("mode", ["constant", "gust"])
@pytest.mark.parametrize("row,value", [(0, [[5, 2]]), (None, [5, 2, 0])])
def test_t2_r5_002_reports_pinned_wind_for_record_list_and_comparison(tmp_path: Path, mode: str, row: int | None, value: Any) -> None:
    root = tmp_path / "records"
    document = traffic_document(value, mode=mode, row=row)
    pinned_run(root, "run-a", document)
    pinned_run(root, "run-b", traffic_document([[7, 0]]))
    unchanged = copy.deepcopy(document)
    with TestClient(create_app(root), base_url="http://localhost") as client:
        record = client.get("/v1/runs/run-a").json()
        assert record["knobs"]["wind_east_mps"] == 5
        assert record["knobs"]["wind_north_mps"] == 2
        assert record["knobs"]["physics_hz"] == 15
        records = client.get("/v1/runs", params={"include_kpis": False}).json()
        assert next(row for row in records if row["id"] == "run-a")["knobs"]["wind_east_mps"] == 5
        response = client.get("/v1/runs/compare", params={"ids": "run-a,run-b"})
        assert response.status_code == 200, response.text
        comparison = response.json()
        assert comparison["runs"][0]["knobs"]["wind_east_mps"] == 5
        wind = next(row for row in comparison["differences"]["knobs"] if row["name"] == "wind_east_mps")
        assert wind["values"] == {"run-a": 5, "run-b": 7}
    assert json.loads((root / "run-a" / "scenario.json").read_text()) == unchanged
    assert json.loads((root / "run-a" / "manifest.json").read_text())["template"]["knobs"]["wind_east_mps"] == 3


@pytest.mark.parametrize("value", [None, [[None, 2]], [[5]], [["invalid", 2]]])
def test_unreadable_pinned_wind_never_becomes_the_stale_template_value(tmp_path: Path, value: Any) -> None:
    root = tmp_path / "records"
    pinned_run(root, "run-a", traffic_document(value))
    with TestClient(create_app(root), base_url="http://localhost") as client:
        response = client.get("/v1/runs/run-a")
        assert response.status_code == 200
        assert "wind_east_mps" not in response.json()["knobs"]
        assert "wind_north_mps" not in response.json()["knobs"]


def test_changed_runtime_consumer_omits_unverifiable_wind_and_preserves_other_actual_controls(tmp_path: Path) -> None:
    root = tmp_path / "records"
    document = traffic_document([[5, 2]])
    document["engines"]["air_motion"]["config"]["consumption_model"] = {"plugin": "constant"}
    document["ingress_streams"] = [{"id": "operator", "initial_watermark_ns": 0}]
    document["engines"]["decisions"] = {"plugin": "traffic_decisions", "config": {"mode": "stub"}}
    document["behaviours"] = [{"document": {"chains": {"traffic.incident_report": {"transitions": [{
        "id": "schedule", "actions": [{"id": "accident-time", "duration_ns": 25_000_000_000}],
    }]}}}}]
    pinned_run(root, "run-a", document, knobs={"wind_east_mps": 3, "accident_time_s": 20, "run_mode": "scheduled", "decision_mode": "live"})
    with TestClient(create_app(root), base_url="http://localhost") as client:
        knobs = client.get("/v1/runs/run-a").json()["knobs"]
        assert "wind_east_mps" not in knobs
        assert knobs["accident_time_s"] == 25
        assert knobs["run_mode"] == "interactive"
        assert knobs["decision_mode"] == "scripted"


def test_pointer_controls_follow_the_pinned_values_including_zero(tmp_path: Path) -> None:
    root = tmp_path / "records"
    document = {"id": "food-delivery", "registry": {"messages": []}, "algorithms": {"allocation": "auction"}, "engines": {
        "environment": {"config": {"profiles": {"weather": {"lowalt.wind": {"mode": "constant", "value": [0, 0, 0]}}}}},
    }}
    pinned_run(root, "run-a", document, template="food-delivery", knobs={"allocator": "nearest", "fleet_size": 4, "wind_speed": 2})
    with TestClient(create_app(root), base_url="http://localhost") as client:
        record = client.get("/v1/runs/run-a").json()
        assert record["knobs"]["allocator"] == "auction"
        # This source is absent in the pinned document, so its old count is not reported.
        assert "fleet_size" not in record["knobs"]
        assert record["knobs"]["wind_speed"] == 0
        assert record["algorithms"]["allocation"] == "auction"
