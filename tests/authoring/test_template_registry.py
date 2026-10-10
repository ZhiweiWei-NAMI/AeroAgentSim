"""Pure template knobs, structural hooks and shipped traffic configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from aeroagentsim.authoring.templates import TemplateRegistry, demo_source


def test_traffic_knobs_change_real_schedule_physics_and_preserve_source() -> None:
    registry = TemplateRegistry()
    source = demo_source("traffic-accident")
    before = {
        name: (source / name).read_bytes()
        for name in ("scenario.yaml", "behaviours.yaml")
    }
    prepared = registry.apply(
        "traffic-accident", {"accident_time_s": 5, "physics_hz": 2}
    )
    assert prepared.knobs == {
        "accident_time_s": 5,
        "decision_mode": "scripted",
        "physics_hz": 2,
    }
    package = prepared.document["behaviours"][0]
    actions = [
        action
        for transition in package["chains"]["traffic.incident_report"]["transitions"]
        if transition["id"] == "schedule"
        for action in transition["actions"]
    ]
    assert (
        next(action for action in actions if action["id"] == "accident-time")[
            "duration_ns"
        ]
        == 5_000_000_000
    )
    assert (
        prepared.document["engines"]["air_motion"]["config"]["step_ns"] == 500_000_000
    )
    assert (
        prepared.document["engines"]["road_motion"]["config"]["step_ns"] == 500_000_000
    )
    assert prepared.document["run"]["advance_ns"] == 500_000_000
    assert {name: (source / name).read_bytes() for name in before} == before
    with pytest.raises(ValueError, match="expected number"):
        registry.apply("traffic-accident", {"physics_hz": True})


def test_json_pointer_escaping_and_explicit_custom_hook(tmp_path: Path) -> None:
    source = tmp_path / "custom"
    source.mkdir()
    scenario = {
        "format": "aeroagentsim.scenario/v1",
        "id": "custom",
        "registry": {
            "snapshot": str(demo_source("traffic-accident") / "registry.snapshot.json")
        },
        "entities": [],
        "bindings": {"exact": [], "lifecycle": []},
        "engines": {"test": {"plugin": "test", "config": {"a/b": {"~rate": [1]}}}},
        "run": {
            "pacing": "fast",
            "seed": 1,
            "until_ns": 1_000_000_000,
            "advance_ns": 1_000_000_000,
        },
        "outputs": {"durability": "flush"},
    }
    metadata = {
        "format": "aeroagentsim.template/v1",
        "id": "custom",
        "title": "Custom",
        "summary": "Tune a rate.",
        "category": "mixed",
        "difficulty": "beginner",
        "duration_s": 1,
        "scenario": "scenario.yaml",
        "observe": ["Rate changes.", "Seed changes."],
        "try": ["Change the rate.", "Change the seed."],
        "kpis": [],
        "knobs": [
            {
                "id": "rate",
                "label": "Rate",
                "type": "integer",
                "default": 1,
                "pointer": "/engines/test/config/a~1b/~0rate/0",
            },
            {
                "id": "seed",
                "label": "Seed",
                "type": "integer",
                "default": 1,
                "apply": "research.seed",
            },
        ],
    }
    (source / "scenario.yaml").write_text(yaml.safe_dump(scenario))
    (source / "template.yaml").write_text(yaml.safe_dump(metadata))
    registry = TemplateRegistry(tmp_path)
    with pytest.raises(ValueError, match="unregistered apply hook"):
        registry.apply("custom")

    def hook(document: dict[str, Any], base: Path, value: Any) -> None:
        assert base == source
        document["run"]["seed"] = value

    registry.register_apply_hook("research.seed", hook)
    prepared = registry.apply("custom", {"rate": 4, "seed": 9})
    assert prepared.document["engines"]["test"]["config"]["a/b"]["~rate"] == [4]
    assert prepared.document["run"]["seed"] == 9
    assert yaml.safe_load((source / "scenario.yaml").read_text()) == scenario
