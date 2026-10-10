"""Tests for the business event narrative projector."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from string import Formatter
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import Create, FactWrite, Remove
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp

from aeroagentsim.authoring.templates import demo_source
from aeroagentsim.packs.lowalt.overlay import EVENT_NARRATIVES as LOWALT_NARRATIVES
from aeroagentsim.packs.lowalt.overlay import TASK_KINDS
from aeroagentsim.packs.traffic_accident.narratives import EVENT_NARRATIVES
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.narratives import (
    LOWALT_DEFAULTS,
    NarrativeProjector,
    validate_declarations,
)

RUN = "run-1"
EPOCH = "epoch-0"


def ref(entity_id: str, generation: int = 0, type_id: str = "aas:Vehicle") -> EntityRef:
    return EntityRef(
        run_id=RUN, epoch=EPOCH, id=entity_id, generation=generation, type_id=type_id
    )


def stamp(ns: int) -> Stamp:
    return Stamp(clock_id="c", numerator=ns, denominator=1, mapping_id="m")


def message(
    schema_id: str,
    payload: dict[str, Any],
    ns: int = 1,
    message_id: str | None = None,
) -> Message:
    return Message(
        id=message_id or f"msg-{schema_id}-{ns}",
        kind="event",
        schema_id=schema_id,
        source_kind="partition",
        source="p1",
        sequence=ns,
        at=Instant(ns),
        available=Instant(ns),
        payload=payload,
        causes=(),
    )


def record_with(*items: dict[str, Any], index: int = 1) -> dict[str, Any]:
    encoded = encode(
        {
            "index": index,
            "instant": Instant(index),
            "items": list(items),
            "batches": [],
        }
    )
    assert isinstance(encoded, dict)
    return encoded


def emit_item(msg: Message) -> dict[str, Any]:
    return {
        "kind": "operation",
        "partition": "p1",
        "proposal": Emit(
            kind="event",
            schema_id=msg.schema_id,
            target_or_topic="feed",
            at=msg.at,
            payload=msg.payload,
        ),
        "message": msg,
    }


def item_entity(item: object) -> dict[str, Any]:
    return {"kind": "operation", "partition": "p1", "proposal": item, "causes": ()}


@pytest.fixture(autouse=True)
def pinned_projection_registry(tmp_path: Path) -> None:
    vector = {"type": "vector", "length": 3, "items": {"type": "number"}}
    registry = MemoryRegistry(
        (TypeDescriptor("aas:Vehicle"), TypeDescriptor("aas:Task")),
        tuple(
            FieldDescriptor(
                name,
                "aas:Vehicle",
                vector,
                {"unit": "m", "frame": "enu", "role": "state"},
            )
            for name in ("traffic.road.position_enu_m", "position_enu_m")
        )
        + (
            FieldDescriptor(
                "position_ned_m",
                "aas:Vehicle",
                vector,
                {"unit": "m", "frame": "ned", "role": "state"},
            ),
            FieldDescriptor(
                "velocity_enu_m_s",
                "aas:Vehicle",
                vector,
                {"unit": "m/s", "frame": "enu", "role": "derived"},
            ),
        ),
    )
    (tmp_path / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (tmp_path / "scenario.json").write_text("{}")


def tmp_scenario(tmp_path: Path, **scenario: Any) -> Path:
    (tmp_path / "scenario.json").write_text(json.dumps(scenario))
    return tmp_path


def test_lowalt_task_category_mapping(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    event = projector.consume(
        record_with(
            emit_item(
                message(
                    "lowalt.task.created",
                    {
                        "task": {"$ref": ref("task-7", type_id="aas:Task").to_data()},
                        "kind": "medical",
                        "priority": 2,
                    },
                )
            )
        )
    )
    assert len(event) == 1
    row = event[0]
    assert row["category"] == "emergency"
    assert row["severity"] == "info"
    assert row["text"] == "New urgent medical order for its destination"
    assert row["entities"] == ["task-7"]
    assert "position" not in row  # no stored position yet


def test_missing_required_placeholder_raises(tmp_path: Path) -> None:
    scenario = tmp_scenario(
        tmp_path,
        event_narratives={
            "custom.event": {
                "template": "{entity} did {detail}",
                "category": "mixed",
                "severity": "info",
            }
        },
    )
    projector = NarrativeProjector.from_directory(scenario)
    with pytest.raises(ValueError, match="missing placeholder"):
        projector.consume(
            record_with(
                emit_item(
                    message(
                        "custom.event",
                        {"entity": "e-1"},
                    )
                )
            )
        )


def test_assigned_started_completed_with_position(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    events = projector.consume(
        record_with(
            item_entity(
                FactWrite(
                    key=(ref("veh-1"), "traffic.road.position_enu_m"),
                    value=[10.0, 20.0, 5.0],
                    acquired=stamp(0),
                    valid=Interval(start=Instant(0), end=None),
                )
            ),
            emit_item(
                message(
                    "lowalt.task.assigned",
                    {"task": "task-1", "vehicle": "veh-1", "algorithm": "nearest"},
                )
            ),
        )
    )
    assert [e["text"] for e in events] == [
        "veh-1 was assigned the task for its destination"
    ]
    assert events[0]["position"] == [10.0, 20.0, 5.0]
    assert events[0]["entities"] == ["task-1", "veh-1"]


def test_completed_template(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    events = projector.consume(
        record_with(
            emit_item(
                message(
                    "lowalt.task.completed",
                    {
                        "task": "task-2",
                        "vehicle": "veh-2",
                        "latency_s": 12.5,
                    },
                )
            )
        )
    )
    assert events[0]["text"] == ("veh-2 completed the task at its destination (12.5 s)")


def test_optional_entity_links_omitted_when_absent(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    events = projector.consume(
        record_with(
            emit_item(message("lowalt.weather.hold", {"reason": "crosswind 12 m/s"}))
        )
    )
    assert events[0]["entities"] == []
    assert events[0]["text"] == (
        LOWALT_NARRATIVES["lowalt.weather.hold"]["text"].format(
            reason="crosswind 12 m/s"
        )
    )
    assert events[0]["category"] == "weather"


def test_duplicate_delivery_ignored(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    msg = message("lowalt.energy.low", {"vehicle": "veh-9", "soc": 0.18})
    record = record_with(emit_item(msg))
    assert len(projector.consume(record)) == 1
    # Redelivery of the same committed message (same id) narrates nothing.
    assert projector.consume(record) == []
    assert projector.consume(record_with(emit_item(msg), index=2)) == []


def test_remove_clears_position(tmp_path: Path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    projector.consume(
        record_with(
            item_entity(Create(ref=ref("veh-1"))),
            item_entity(
                FactWrite(
                    key=(ref("veh-1"), "position_enu_m"),
                    value=[1.0, 2.0, 3.0],
                    acquired=stamp(0),
                    valid=Interval(start=Instant(0), end=None),
                )
            ),
            item_entity(Remove(ref=ref("veh-1"))),
            emit_item(message("lowalt.energy.low", {"vehicle": "veh-1", "soc": 0.1})),
        )
    )
    # The removed generation has no position; nothing is fabricated.


def test_traffic_coverage() -> None:
    import yaml

    scenario = yaml.safe_load(
        open("scenarios/demos/traffic-accident/scenario.yaml", encoding="utf-8")
    )
    event_ids = {
        m["id"]
        for m in scenario["registry"]["messages"]
        if m.get("kind") == "event" and m["id"].startswith("traffic.")
    }
    validate_declarations(EVENT_NARRATIVES)
    assert event_ids <= set(EVENT_NARRATIVES), event_ids - set(EVENT_NARRATIVES)


def test_validate_declarations_errors() -> None:
    validate_declarations(
        {"x.e": {"template": "{a}", "category": "logistics", "severity": "info"}}
    )
    with pytest.raises(ValueError):
        validate_declarations({})
    with pytest.raises(ValueError):
        validate_declarations(
            {"x.e": {"template": "{a}", "category": "nope", "severity": "info"}}
        )
    with pytest.raises(ValueError):
        validate_declarations(
            {
                "x.e": {
                    "template": "{a}",
                    "category": "logistics",
                    "severity": "info",
                    "template2": "y",
                }
            }
        )
    with pytest.raises(ValueError):
        validate_declarations(
            {"x.e": {"template": "{a!r}", "category": "logistics", "severity": "info"}}
        )
    with pytest.raises(ValueError):
        validate_declarations({"x.e": {"category": "logistics", "severity": "info"}})


def test_custom_declarations_and_override(tmp_path: Path) -> None:
    behaviour = {
        "packages": [
            {
                "document": {
                    "event_narratives": {
                        "custom.patrol.done": {
                            "template": "{vehicle} finished patrol",
                            "category": "security",
                            "severity": "notice",
                            "entities": ["vehicle"],
                        }
                    }
                }
            }
        ]
    }
    (tmp_path / "behaviour.ir.json").write_text(json.dumps(behaviour))
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "event_narratives": {
                    "lowalt.task.created": {
                        "template": "New {task} of kind {kind}",
                        "category": "logistics",
                        "severity": "notice",
                    }
                }
            }
        )
    )
    projector = NarrativeProjector.from_directory(tmp_path)
    events = projector.consume(
        record_with(
            emit_item(message("custom.patrol.done", {"vehicle": "veh-3"})),
            emit_item(
                message(
                    "lowalt.task.created",
                    {"task": "task-4", "kind": "parcel"},
                )
            ),
        )
    )
    assert events[0]["text"] == "veh-3 finished patrol"
    assert events[0]["category"] == "security"
    assert events[1]["text"] == "New task-4 of kind parcel"
    assert events[1]["severity"] == "notice"


def test_conflicting_package_declarations_raise(tmp_path: Path) -> None:
    (tmp_path / "behaviour.ir.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "document": {
                            "event_narratives": {
                                "lowalt.task.created": {
                                    "template": "clash {task}",
                                    "category": "logistics",
                                    "severity": "info",
                                }
                            }
                        }
                    },
                    {
                        "document": {
                            "event_narratives": {
                                "lowalt.task.created": {
                                    "template": "different {task}",
                                    "category": "logistics",
                                    "severity": "info",
                                }
                            }
                        }
                    },
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="conflicting declaration"):
        NarrativeProjector.from_directory(tmp_path)


def test_projection_context_restores_dedup(tmp_path: Path) -> None:
    base = tmp_scenario(tmp_path)
    projector = NarrativeProjector.from_directory(base)
    record = record_with(
        emit_item(message("lowalt.energy.low", {"vehicle": "veh-5", "soc": 0.2}))
    )
    events = projector.consume(record)
    assert len(events) == 1
    restored = NarrativeProjector.projection_context(base, projector.cursor())
    assert restored.consume(record) == []


def test_task_category_and_earlier_position_persist_without_using_velocity(
    tmp_path: Path,
) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    projector.consume(
        record_with(
            item_entity(
                FactWrite(
                    (ref("veh-1"), "position_enu_m"),
                    [10, 20, 30],
                    stamp(0),
                    Interval(Instant(0), None),
                )
            ),
            index=1,
        )
    )
    projector.consume(
        record_with(
            emit_item(
                message(
                    "lowalt.task.created",
                    {"task": "task-1", "kind": "medical", "priority": 1},
                    ns=2,
                )
            ),
            index=2,
        )
    )
    rows = projector.consume(
        record_with(
            item_entity(
                FactWrite(
                    (ref("veh-1"), "velocity_enu_m_s"),
                    [100, 200, 300],
                    stamp(3),
                    Interval(Instant(3), None),
                )
            ),
            item_entity(
                FactWrite(
                    (ref("veh-1"), "position_ned_m"),
                    [100, 200, 300],
                    stamp(3),
                    Interval(Instant(3), None),
                )
            ),
            emit_item(
                message(
                    "lowalt.task.completed",
                    {
                        "task": "task-1",
                        "vehicle": {"$ref": ref("veh-1").to_data()},
                        "latency_s": 1,
                    },
                    ns=3,
                )
            ),
            index=3,
        )
    )
    assert rows[0]["category"] == "emergency"
    assert rows[0]["position"] == [10, 20, 30]
    assert rows[0]["text_zh"] == "veh-1在目的地送达紧急医疗物资（耗时 1 秒）"


def placeholder_names(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def test_all_narrative_translations_preserve_placeholders() -> None:
    for declarations, english, chinese in (
        (LOWALT_NARRATIVES, "text", "text_zh"),
        (EVENT_NARRATIVES, "template", "template_zh"),
        (LOWALT_DEFAULTS, "template", "template_zh"),
    ):
        for schema, declaration in declarations.items():
            text, text_zh = declaration[english], declaration[chinese]
            assert text_zh.strip() == text_zh and text_zh != text, schema
            assert any("\u4e00" <= char <= "\u9fff" for char in text_zh), schema
            assert placeholder_names(text_zh) == placeholder_names(text), schema
    validate_declarations(EVENT_NARRATIVES)
    validate_declarations(LOWALT_DEFAULTS)


@pytest.mark.parametrize(
    "translated",
    [None, "", "  ", 42, "{actor!r}完成任务", "{actor.name}完成任务", "{actor:>3}完成任务", "{other}完成任务", "任务完成"],
)
def test_invalid_chinese_narrative_templates_fail_validation(translated: Any) -> None:
    with pytest.raises(ValueError, match="template_zh"):
        validate_declarations({
            "custom.done": {
                "template": "{actor} finished",
                "template_zh": translated,
                "category": "mixed",
                "severity": "info",
            }
        })


@pytest.mark.parametrize(
    "kind,noun,done",
    [
        ("parcel", "包裹配送任务", "完成包裹投递"),
        ("medical", "紧急医疗配送任务", "送达紧急医疗物资"),
        ("inspection", "巡检任务", "完成巡检"),
        ("survey", "测绘任务", "完成测绘"),
        ("spraying", "喷洒任务", "完成喷洒"),
        ("passenger", "载客任务", "送达乘客"),
        ("patrol", "巡逻任务", "完成巡逻"),
        ("incident_response", "事故响应任务", "完成事故响应"),
    ],
)
def test_task_chinese_context_and_cursor_interpolation(
    tmp_path: Path, kind: str, noun: str, done: str
) -> None:
    assert kind in TASK_KINDS
    base = tmp_scenario(tmp_path, entities=[
        {"id": "drone-1", "label": "Drone One"},
        {"id": "origin-1", "label": "East Depot"},
        {"id": "destination-1", "label": "West Site"},
    ])
    projector = NarrativeProjector.from_directory(base)
    created = projector.consume(record_with(emit_item(message(
        "lowalt.task.created", {
            "task": "task-1", "kind": kind, "priority": 1,
            "origin": "origin-1", "destination": "destination-1",
        }, ns=1,
    ))))[0]
    assert created["text_zh"] == f"新增前往West Site的{noun}"
    # A fresh projector must obtain Chinese phrasing from the committed cursor,
    # not a new or guessed task kind. Authored labels remain intact.
    restored = NarrativeProjector.projection_context(base, json.loads(json.dumps(projector.cursor())))
    started = restored.consume(record_with(emit_item(message(
        "lowalt.task.started", {"task": "task-1", "vehicle": "drone-1"}, ns=2
    )), index=2))[0]
    assert started["text_zh"] == f"Drone One前往East Depot执行{noun}"
    finished = restored.consume(record_with(emit_item(message(
        "lowalt.task.completed", {
            "task": "task-1", "vehicle": {"$ref": ref("drone-1").to_data()},
            "latency_s": 12.5,
        }, ns=3,
    )), index=3))[0]
    assert finished["text_zh"] == f"Drone One在West Site{done}（耗时 12.5 秒）"
    assert finished["entities"] == ["task-1", "drone-1"]
    assert finished["category"] == created["category"]


def sample_schema(schema: Mapping[str, Any], name: str = "sample") -> Any:
    """Typed minimal payloads from actual traffic message schemas, not placeholders."""
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema["type"]
    if kind == "ref":
        return {
            "$ref": ref(name + "-1", type_id=schema.get("target_type", "oo:Vehicle")).to_data()
        }
    if kind == "record":
        return {
            field: sample_schema(schema["members"][field], field)
            for field in schema["required"]
        }
    if kind in {"array", "vector"}:
        return [sample_schema(schema["items"], name) for _ in range(schema.get("length", 1))]
    if kind == "string":
        return name + "-value"
    if kind == "integer":
        return max(1, schema.get("minimum", 0))
    if kind == "number":
        return max(12.5, schema.get("minimum", 0.0))
    if kind == "boolean":
        return True
    raise AssertionError(f"Untested schema shape: {schema}")


def test_every_traffic_translation_interpolates_actual_message_schema(tmp_path: Path) -> None:
    scenario = load_scenario(demo_source("traffic-accident") / "scenario.yaml")
    messages = {item.id: item for item in scenario.registry.messages if item.kind == "event"}
    assert len(EVENT_NARRATIVES) == 24
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    for index, (schema_id, declaration) in enumerate(EVENT_NARRATIVES.items(), 1):
        descriptor = messages[schema_id]
        fields = placeholder_names(declaration["template_zh"])
        assert fields <= set(descriptor.schema["required"]), schema_id
        payload = sample_schema(descriptor.schema)
        scenario.registry.validate(descriptor.schema, payload)
        record = record_with(emit_item(message(schema_id, payload, ns=index)), index=index)
        rows = projector.consume(record)
        if declaration.get("hidden"):
            assert rows == []
            visible = NarrativeProjector({schema_id: {**declaration, "hidden": False}})
            rows = visible.consume(record)  # Interpolate even the hidden telemetry copy.
        assert len(rows) == 1, schema_id
        row = rows[0]
        assert row["text_zh"] != row["text"], schema_id
        assert any("\u4e00" <= char <= "\u9fff" for char in row["text_zh"]), schema_id
        for token in ("{", "}", "$ref", "None", "nan"):
            assert token not in row["text_zh"], (schema_id, row["text_zh"])
        for field in fields:
            value = payload[field]
            if isinstance(value, dict) and "$ref" in value:
                rendered = value["$ref"]["id"]
            elif isinstance(value, list):
                rendered = value[0]["$ref"]["id"]
            else:
                rendered = str(value)
            assert rendered in row["text_zh"], (schema_id, field)
        assert row["category"] == declaration["category"]
        assert row["severity"] == declaration["severity"]


def test_custom_bilingual_rendering_and_retry_does_not_hide_errors(tmp_path: Path) -> None:
    base = tmp_scenario(tmp_path, event_narratives={
        "custom.event": {
            "template": "{actor} saw {places}: {detail} ({missing})",
            "template_zh": "{actor}看到{places}：{detail}（{missing}）",
            "category": "inspection", "severity": "info", "entities": ["actor"],
        }
    })
    projector = NarrativeProjector.from_directory(base)
    payload: dict[str, Any] = {
        "actor": {"$ref": ref("drone-1").to_data()},
        "places": ["A", "B", "C", "D"], "detail": {"value": 1},
    }
    broken = record_with(emit_item(message("custom.event", payload)))
    for _ in range(2):
        with pytest.raises(ValueError, match="missing placeholder missing"):
            projector.consume(broken)
        assert projector.cursor()["seen"] == []
    payload["missing"] = None
    row = projector.consume(record_with(emit_item(message("custom.event", payload))))[0]
    assert row["text"] == "drone-1 saw A, B, C and 1 more: details (unknown)"
    assert row["text_zh"] == "drone-1看到A、B、C，另有 1 项：详情（未知）"
    assert row["entities"] == ["drone-1"]
    assert projector.consume(record_with(emit_item(message("custom.event", payload)))) == []
