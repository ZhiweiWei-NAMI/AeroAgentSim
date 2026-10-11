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


def test_number_knob_accepts_whole_json_number_as_kernel_number() -> None:
    from aeroagentsim.authoring.templates import TemplateRegistry

    prepared = TemplateRegistry().apply("parcel-delivery", {"wind_speed": 4})
    assert prepared.knobs["wind_speed"] == 4.0
    assert isinstance(prepared.knobs["wind_speed"], float)
    pinned = prepared.run_metadata()
    assert pinned["title_zh"] == "城市包裹配送"
    assert pinned["title"] == prepared.metadata["title"]
    pinned["knobs"]["wind_speed"] = 9
    assert prepared.knobs["wind_speed"] == 4.0
    assert (
        prepared.document["engines"]["environment"]["config"]["profiles"]["weather"][
            "lowalt.wind"
        ]["value"][0]
        == 4.0
    )


@pytest.mark.parametrize("identifier", [
    "air-taxi", "city-day", "crop-spraying", "medical-delivery", "food-delivery",
    "parcel-delivery", "powerline-inspection", "security-patrol",
])
def test_fleet_expands_both_authored_labels_without_changing_template(
    identifier: str,
) -> None:
    from aeroagentsim.packs.lowalt.template import expand

    prepared = TemplateRegistry().apply(identifier, {"fleet_size": 2})
    cfg = prepared.document["generators"]["fleet"]
    cfg.update(label="Drone {index}", label_zh="无人机 {index}")
    expand(prepared.document)
    fleet = [
        entity for entity in prepared.document["entities"]
        if entity["id"].startswith(cfg["prefix"])
    ]
    assert [entity["label"] for entity in fleet] == ["Drone 1", "Drone 2"]
    assert [entity["label_zh"] for entity in fleet] == ["无人机 1", "无人机 2"]
    assert cfg["label_zh"] == "无人机 {index}"
    assert prepared.document["generators"]["fleet"]["label"] == "Drone {index}"


def test_all_shipped_template_metadata_is_bilingual_and_copied() -> None:
    registry = TemplateRegistry(Path(__file__).resolve().parents[2] / "scenarios/templates")
    templates = registry.list()
    assert {item["id"] for item in templates} == {
        "traffic-accident", "parcel-delivery", "medical-delivery", "food-delivery",
        "powerline-inspection", "crop-spraying", "air-taxi", "security-patrol", "city-day",
    }
    translated_bullets = translated_labels = 0
    for item in templates:
        TemplateRegistry._validate(item, item["id"])
        for key in ("title", "summary"):
            assert item[key + "_zh"] != item[key]
            assert any("\u4e00" <= char <= "\u9fff" for char in item[key + "_zh"])
        for key in ("observe", "try"):
            assert len(item[key + "_zh"]) == len(item[key])
            for original, translated in zip(item[key], item[key + "_zh"]):
                assert translated.strip() and translated != original
                assert any("\u4e00" <= char <= "\u9fff" for char in translated)
                translated_bullets += 1
        for knob in item["knobs"]:
            assert any("\u4e00" <= char <= "\u9fff" for char in knob["label_zh"])
            assert knob["label_zh"] != knob["label"]
            translated_labels += 1
    assert (translated_bullets, translated_labels) == (46, 55)
    scheduled = registry.apply("food-delivery")
    interactive = registry.apply("food-delivery", {"run_mode": "interactive"})
    assert scheduled.knobs["run_mode"] == "scheduled"
    assert scheduled.document["ingress_streams"][0]["initial_watermark_ns"] == scheduled.document["run"]["until_ns"]
    assert interactive.document["ingress_streams"][0]["initial_watermark_ns"] == 0
    assert "timeout_s" not in interactive.document["ingress_streams"][0]
    assert scheduled.document["ingress_streams"][0]["timeout_s"] == 10.0
    interactive.document["ingress_streams"][0]["initial_watermark_ns"] = scheduled.document["run"]["until_ns"]
    interactive.document["ingress_streams"][0]["timeout_s"] = 10.0
    assert interactive.document == scheduled.document
    traffic = registry.get("traffic-accident")
    choice = next(knob for knob in traffic["knobs"] if knob["id"] == "decision_mode")
    assert choice["options"] == ["scripted", "live"]
    assert choice["options_zh"] == ["预设决策", "实时模型"]
    traffic["observe_zh"][0] = "changed"
    choice["options_zh"][0] = "changed"
    assert registry.get("traffic-accident")["observe_zh"][0] != "changed"
    assert registry.get("traffic-accident")["knobs"][1]["options_zh"][0] == "预设决策"


@pytest.mark.parametrize(
    "path,value",
    [
        (("title_zh",), ""),
        (("summary_zh",), None),
        (("title_zh",), "  "),
        (("observe_zh",), ["观察"]),
        (("try_zh",), "试试"),
        (("try_zh",), ["试试", "", "试试"]),
        (("knobs", 0, "label_zh"), 123),
        (("knobs", 0, "options_zh"), ["一"]),
        (("knobs", 1, "options_zh"), ["预设"]),
        (("knobs", 1, "options_zh"), ["预设", "  "]),
        (("knobs", 1, "description_zh"), "响应模式"),
    ],
)
def test_template_registry_rejects_invalid_translation_shapes(
    path: tuple[Any, ...], value: Any
) -> None:
    metadata = TemplateRegistry().get("traffic-accident")
    target = metadata
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=str(path[-1])):
        TemplateRegistry._validate(metadata, "traffic-accident")


def test_template_translations_are_optional_and_descriptions_are_supported() -> None:
    metadata = TemplateRegistry().get("traffic-accident")
    for key in list(metadata):
        if key.endswith("_zh"):
            del metadata[key]
    for knob in metadata["knobs"]:
        for key in list(knob):
            if key.endswith("_zh"):
                del knob[key]
    TemplateRegistry._validate(metadata, "traffic-accident")
    metadata["knobs"][0].update(
        description="Seconds after the run starts.",
        description_zh="从仿真开始到事故发生的秒数。",
    )
    TemplateRegistry._validate(metadata, "traffic-accident")
