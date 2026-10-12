"""Facility names and road causes come from committed facts and event links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp

from aeroagentsim.services.narratives import NarrativeProjector


def ref(identity: str, kind: str) -> EntityRef:
    return EntityRef("story", "epoch-0", identity, 0, kind)


def event(schema: str, payload: dict[str, Any], at: int) -> Message:
    return Message(
        id=f"{schema}/{at}", kind="event", schema_id=schema,
        source_kind="partition", source="tasks", sequence=at,
        at=Instant(at), available=Instant(at), payload=payload, causes=(),
    )


def write(entity: EntityRef, field: str, value: Any, at: int) -> FactWrite:
    return FactWrite(
        (entity, field), value, Stamp("canonical", at, 1, "canonical"),
        Interval(Instant(at), None),
    )


def record(*operations: FactWrite | Message) -> dict[str, Any]:
    items = []
    for operation in operations:
        if isinstance(operation, Message):
            items.append({
                "proposal": Emit("event", operation.schema_id, "feed", operation.at, operation.payload),
                "message": operation,
            })
        else:
            items.append({"proposal": operation})
    return encode({"index": 1, "instant": Instant(0), "items": items, "batches": []})


@pytest.fixture
def projector(tmp_path: Path) -> NarrativeProjector:
    vector = {"type": "vector", "length": 3, "items": {"type": "number"}}
    registry = MemoryRegistry(
        tuple(TypeDescriptor(kind) for kind in ("lowalt:Task", "lowalt:UAV", "aas:TrafficRoadVehicle", "aas:TrafficIncident")),
        (
            FieldDescriptor("lowalt.task_origin", "lowalt:Task", {"type": "string"}, {"role": "state"}),
            FieldDescriptor("lowalt.task_destination", "lowalt:Task", {"type": "string"}, {"role": "state"}),
            FieldDescriptor("lowalt.position", "lowalt:UAV", vector, {"unit": "m", "frame": "enu", "role": "state"}),
            FieldDescriptor("traffic.road.position_enu_m", "aas:TrafficRoadVehicle", vector, {"unit": "m", "frame": "enu", "role": "state"}),
        ),
    )
    (tmp_path / "runtime.registry.json").write_text(json.dumps(registry.to_data()))
    (tmp_path / "scenario.json").write_text(json.dumps({"entities": [
        {"id": "taxi-1", "label": "Air taxi 1"},
        {"id": "east-port", "label": "Vertiport East Roof"},
        {"id": "north-port", "label": "Vertiport North Roof"},
        {"id": "west-port", "label": "Vertiport West Plaza"},
    ]}))
    return NarrativeProjector.from_directory(tmp_path)


@pytest.mark.parametrize("restore", [False, True])
def test_pickup_uses_committed_diversion_after_creation(
    projector: NarrativeProjector, restore: bool, tmp_path: Path,
) -> None:
    projector.consume(record(event("lowalt.task.created", {
        "task": "trip-1", "kind": "passenger", "priority": 1,
        "origin": "east-port", "destination": "west-port",
    }, 1)))
    projector.consume(record(
        write(ref("trip-1", "lowalt:Task"), "lowalt.task_origin", "north-port", 61),
        event("lowalt.task.replanned", {"task": "trip-1", "vehicle": "taxi-1", "reason": "vertiport closed"}, 61),
    ))
    if restore:
        projector = NarrativeProjector.projection_context(
            tmp_path, json.loads(json.dumps(projector.cursor()))
        )
    pickup = projector.consume(record(
        write(ref("taxi-1", "lowalt:UAV"), "lowalt.position", [93.76, 104.22, 12.0], 178),
        event("lowalt.task.picked_up", {"task": "trip-1", "vehicle": "taxi-1"}, 178),
    ))[0]
    assert pickup["text"] == "Air taxi 1 picked up the passenger trip at Vertiport North Roof"
    assert "北侧屋顶起降场" in pickup["text_zh"]
    assert "East Roof" not in pickup["text"]
    assert pickup["position"] == [93.76, 104.22, 12.0]


def test_completion_uses_committed_destination_change(projector: NarrativeProjector) -> None:
    projector.consume(record(event("lowalt.task.created", {
        "task": "trip-1", "kind": "passenger", "priority": 1,
        "origin": "west-port", "destination": "east-port",
    }, 1)))
    completed = projector.consume(record(
        write(ref("trip-1", "lowalt:Task"), "lowalt.task_destination", "north-port", 61),
        event("lowalt.task.completed", {"task": "trip-1", "vehicle": "taxi-1", "latency_s": 180}, 181),
    ))[0]
    assert "Vertiport North Roof" in completed["text"]
    assert "北侧屋顶起降场" in completed["text_zh"]
    assert "East Roof" not in completed["text"]


@pytest.mark.parametrize("state,severity", [("blocked", "warning"), ("cleared", "notice")])
@pytest.mark.parametrize("actor_link", [False, True])
def test_occupancy_events_stay_neutral_before_and_after_accident(
    projector: NarrativeProjector, state: str, severity: str, actor_link: bool,
) -> None:
    actor = ref("road-1", "aas:TrafficRoadVehicle")
    # Road messages have no required members: both payloads are valid.
    payload = {"actor": {"$ref": actor.to_data()}} if actor_link else {}
    projector.consume(record(write(actor, "traffic.road.position_enu_m", [1.0, 2.0, 0.0], 0)))
    before = projector.consume(record(event("traffic.road." + state, payload, 1)))[0]
    incident = {"$ref": ref("incident-1", "aas:TrafficIncident").to_data()}
    projector.consume(record(event("traffic.incident.activated", {"incident": incident}, 8)))
    after = projector.consume(record(event("traffic.road." + state, payload, 9)))[0]
    for row in (before, after):
        assert row["text"] == "Road traffic conflict " + ("detected" if state == "blocked" else "cleared")
        assert "事故" not in row["text_zh"]
        assert row["category"] == "emergency"
        assert row["severity"] == severity
        if actor_link:
            assert row["entities"] == ["road-1"]
            assert row["position"] == [1.0, 2.0, 0.0]
        else:
            assert row["entities"] == []
            assert "position" not in row


@pytest.mark.parametrize("state,severity", [("blocked", "warning"), ("cleared", "notice")])
@pytest.mark.parametrize("cause", ["incident", "reason"])
def test_explicit_accident_events_keep_accident_wording(
    projector: NarrativeProjector, state: str, severity: str, cause: str,
) -> None:
    payload: dict[str, Any] = {"actor": {"$ref": ref("road-1", "aas:TrafficRoadVehicle").to_data()}}
    payload[cause] = (
        {"$ref": ref("incident-1", "aas:TrafficIncident").to_data()}
        if cause == "incident" else "traffic incident"
    )
    row = projector.consume(record(event("traffic.road." + state, payload, 9)))[0]
    assert row["text"] == f"Road {state} after the accident"
    assert "事故" in row["text_zh"]
    assert row["category"] == "emergency"
    assert row["severity"] == severity
