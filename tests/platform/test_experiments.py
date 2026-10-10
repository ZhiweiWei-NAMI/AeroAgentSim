"""Factorial runs, matched seeds, typed validation, and honest aggregation."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from aeroagentsim.authoring.templates import TemplateRegistry, demo_source
from aeroagentsim.services.experiments import (
    ExperimentError,
    ExperimentManager,
    parse_vary,
)


@pytest.fixture
def registry(tmp_path: Path) -> TemplateRegistry:
    source = tmp_path / "templates" / "probe"
    source.mkdir(parents=True)
    document = {
        "format": "aeroagentsim.scenario/v1",
        "id": "probe",
        "registry": {
            "snapshot": str(demo_source("traffic-accident") / "registry.snapshot.json")
        },
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {},
        "run": {
            "seed": 42,
            "pacing": "realtime",
            "until_ns": 200_000_000,
            "advance_ns": 50_000_000,
        },
        "outputs": {"durability": "flush"},
    }
    metadata = {
        "format": "aeroagentsim.template/v1",
        "id": "probe",
        "title": "Probe",
        "summary": "Engine-free factorial probe.",
        "category": "mixed",
        "difficulty": "beginner",
        "duration_s": 0.2,
        "scenario": "scenario.yaml",
        "observe": ["Committed cuts.", "Matched seeds."],
        "try": ["Change stepping.", "Change duration."],
        "kpis": [],
        "knobs": [
            {
                "id": "step_ns",
                "label": "Step",
                "type": "integer",
                "default": 50_000_000,
                "min": 1,
                "pointer": "/run/advance_ns",
            },
            {
                "id": "duration_ns",
                "label": "Duration",
                "type": "integer",
                "default": 200_000_000,
                "min": 1,
                "pointer": "/run/until_ns",
            },
        ],
    }
    (source / "scenario.yaml").write_text(yaml.safe_dump(document))
    (source / "template.yaml").write_text(yaml.safe_dump(metadata))
    return TemplateRegistry(source.parent)


def test_factorial_matched_seeds_pinned_runs_and_reports(
    registry: TemplateRegistry, tmp_path: Path
) -> None:
    root = tmp_path / "runs"
    manager = ExperimentManager(root, registry)
    try:
        created = manager.create(
            "probe",
            vary={
                "step_ns": [50_000_000, 100_000_000],
                "duration_ns": [100_000_000, 200_000_000],
            },
            seeds=2,
            jobs=2,
        )
        final = manager.wait(created["id"])
    finally:
        manager.close()
    assert final["status"] == "completed" and final["completed"] == final["total"] == 8
    assert final["failed"] == 0 and len(final["table"]) == 4
    for run in final["runs"]:
        path = Path(run["run"])
        manifest = json.loads((path / "manifest.json").read_text())
        scenario = json.loads((path / "scenario.json").read_text())
        assert scenario["run"]["seed"] == run["seed"] == manifest["experiment"]["seed"]
        assert scenario["run"]["pacing"] == "fast"
        assert scenario["run"]["advance_ns"] == manifest["template"]["knobs"]["step_ns"]
        assert (
            scenario["run"]["until_ns"] == manifest["template"]["knobs"]["duration_ns"]
        )
        assert path.parent == root
    assert {run["seed"] for run in final["runs"]} == {0, 1}
    for row in final["table"]:
        assert row["metrics"]["tasks_completed"]["n"] == 2
        assert row["metrics"]["tasks_completed"]["std"] == 0
        assert row["metrics"]["energy_used_kj"]["mean"] is None
        assert row["metrics"]["energy_used_kj"]["n"] == 0
    directory = root / ".experiments" / final["id"]
    assert (
        "configuration,knobs,metric,mean,std,n,unit,best"
        in (directory / "experiment.csv").read_text()
    )
    assert "**0**" in (directory / "experiment.md").read_text()
    assert ExperimentManager(root, registry).get(final["id"])["table"] == final["table"]
    stored = json.loads((directory / "experiment.json").read_text())
    stored["status"] = "running"
    (directory / "experiment.json").write_text(json.dumps(stored))
    assert ExperimentManager(root, registry).get(final["id"])["status"] == "interrupted"


def test_typed_axes_and_validation(registry: TemplateRegistry, tmp_path: Path) -> None:
    assert parse_vary(["allocator=nearest,auction", "fleet_size=4,8"]) == {
        "allocator": ["nearest", "auction"],
        "fleet_size": [4, 8],
    }
    with pytest.raises(ExperimentError, match="duplicate"):
        parse_vary(["a=1", "a=2"])
    manager = ExperimentManager(tmp_path / "runs", registry)
    for kwargs in ({"seeds": 0}, {"jobs": True}, {"horizon_s": float("inf")}):
        with pytest.raises(ExperimentError):
            manager.create("probe", vary={}, **kwargs)
    with pytest.raises(ValueError, match="Unknown knob"):
        manager.create("probe", vary={"missing": [1]})
    with pytest.raises(ValueError, match="integer"):
        manager.create("probe", vary={"step_ns": ["wrong"]})
    created = manager.create("probe", vary={}, seeds=1, horizon_s=0.05)
    final = manager.wait(created["id"])
    manager.close()
    assert final["status"] == "completed"
    assert final["table"][0]["metrics"]["tasks_completed"]["std"] is None
    scenario = json.loads((Path(final["runs"][0]["run"]) / "scenario.json").read_text())
    assert scenario["run"]["until_ns"] == 50_000_000


def test_sample_std_best_and_failed_samples_excluded(
    registry: TemplateRegistry, tmp_path: Path
) -> None:
    manager = ExperimentManager(tmp_path / "runs", registry)
    report = {
        "runs": [
            {
                "config_id": "c0",
                "status": "completed",
                "kpis": {"mean_task_latency_s": 2},
                "kpi_meta": {},
            },
            {
                "config_id": "c0",
                "status": "completed",
                "kpis": {"mean_task_latency_s": 4},
                "kpi_meta": {},
            },
            {
                "config_id": "c1",
                "status": "completed",
                "kpis": {"mean_task_latency_s": 5},
                "kpi_meta": {},
            },
            {
                "config_id": "c1",
                "status": "faulted",
                "kpis": {"mean_task_latency_s": 0},
                "kpi_meta": {},
            },
        ],
        "table": [{"config_id": "c0", "knobs": {}}, {"config_id": "c1", "knobs": {}}],
    }
    manager._aggregate_locked({"report": report})
    first, second = [row["metrics"]["mean_task_latency_s"] for row in report["table"]]
    assert (
        first["mean"] == 3
        and first["std"] == pytest.approx(math.sqrt(2))
        and first["best"]
    )
    assert (
        second["mean"] == 5
        and second["std"] is None
        and second["n"] == 1
        and not second["best"]
    )


def test_worker_failure_keeps_experiment_faulted(
    registry: TemplateRegistry, tmp_path: Path
) -> None:
    source = registry.root / "probe/scenario.yaml"
    document = yaml.safe_load(source.read_text())
    document["engines"] = {"broken": {"plugin": "records", "config": {}}}
    source.write_text(yaml.safe_dump(document))
    manager = ExperimentManager(tmp_path / "failed-runs", registry)
    try:
        created = manager.create("probe", vary={}, seeds=1, jobs=1)
        final = manager.wait(created["id"])
    finally:
        manager.close()
    assert final["status"] == "faulted"
    assert final["failed"] == 1 and final["completed"] == 0
    assert final["error"] and final["runs"][0]["status"] == "faulted"
    assert all(
        metric["n"] == 0 and metric["mean"] is None
        for metric in final["table"][0]["metrics"].values()
    )
