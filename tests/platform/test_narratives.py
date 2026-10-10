"""Tests for the business event narrative projector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import Create, FactWrite, Remove
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp

from aeroagentsim.packs.lowalt.overlay import EVENT_NARRATIVES as LOWALT_NARRATIVES
from aeroagentsim.packs.traffic_accident.narratives import EVENT_NARRATIVES
from aeroagentsim.services.narratives import (
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
    return encode(
        {
            "index": index,
            "instant": Instant(index),
            "items": list(items),
            "batches": [],
        }
    )


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


def item_entity(item: dict[str, Any]) -> dict[str, Any]:
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


def test_lowalt_task_category_mapping(tmp_path) -> None:
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
    assert row["text"] == LOWALT_NARRATIVES["lowalt.task.created"]["text"].format(
        task="task-7"
    )
    assert row["entities"] == ["task-7"]
    assert "position" not in row  # no stored position yet


def test_missing_required_placeholder_raises(tmp_path) -> None:
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


def test_assigned_started_completed_with_position(tmp_path) -> None:
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
        LOWALT_NARRATIVES["lowalt.task.assigned"]["text"].format(
            task="task-1", vehicle="veh-1"
        )
    ]
    assert events[0]["position"] == [10.0, 20.0, 5.0]
    assert events[0]["entities"] == ["task-1", "veh-1"]


def test_completed_template(tmp_path) -> None:
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
    assert events[0]["text"] == (
        LOWALT_NARRATIVES["lowalt.task.completed"]["text"].format(
            task="task-2", vehicle="veh-2", latency_s=12.5
        )
    )


def test_optional_entity_links_omitted_when_absent(tmp_path) -> None:
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


def test_duplicate_delivery_ignored(tmp_path) -> None:
    projector = NarrativeProjector.from_directory(tmp_scenario(tmp_path))
    msg = message("lowalt.energy.low", {"vehicle": "veh-9", "soc": 0.18})
    record = record_with(emit_item(msg))
    assert len(projector.consume(record)) == 1
    # Redelivery of the same committed message (same id) narrates nothing.
    assert projector.consume(record) == []
    assert projector.consume(record_with(emit_item(msg), index=2)) == []


def test_remove_clears_position(tmp_path) -> None:
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
