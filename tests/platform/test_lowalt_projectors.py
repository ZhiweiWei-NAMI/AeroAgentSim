"""Low-altitude projector coverage: cursor checkpoints, schema rendering, and a
real parcel-delivery journal compared against its own committed facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp
from aerokernel.values import freeze

from aeroagentsim.authoring.templates import TemplateRegistry
from aeroagentsim.packs.lowalt.overlay import EVENT_NARRATIVES as LOWALT_NARRATIVES
from aeroagentsim.packs.lowalt.overlay import TASK_KINDS, overlay
from aeroagentsim.scenario import load_scenario
from aeroagentsim.services.kpis import KPIProjector
from aeroagentsim.services.narratives import NarrativeProjector
from aeroagentsim.services.projector import project

RUN = "run-1"
EPOCH = "epoch-0"
BASE = Path(__file__).resolve().parents[2] / "scenarios/templates/parcel-delivery"


def ref(entity_id: str, generation: int = 0, type_id: str = "lowalt:UAV") -> EntityRef:
    return EntityRef(
        run_id=RUN, epoch=EPOCH, id=entity_id, generation=generation, type_id=type_id
    )


def stamp(ns: int) -> Stamp:
    return Stamp(clock_id="c", numerator=ns, denominator=1, mapping_id="m")


_MESSAGE_SEQ = iter(range(1, 10_000))


def message(schema_id: str, payload: dict[str, object], ns: int = 1) -> Message:
    return Message(
        id=f"msg-{schema_id}-{ns}-{next(_MESSAGE_SEQ)}",
        kind="event",
        schema_id=schema_id,
        source_kind="partition",
        source="p1",
        sequence=ns,
        at=Instant(ns),
        available=Instant(ns),
        payload=freeze(payload),
        causes=(),
    )


def emit_item(msg: Message) -> dict[str, object]:
    return {
        "kind": "operation",
        "partition": "p1",
        "proposal": encode(
            Emit(
                kind="event",
                schema_id=msg.schema_id,
                target_or_topic="feed",
                at=msg.at,
                payload=msg.payload,
            )
        ),
        "message": encode(msg),
    }


def record(index: int, at_ns: int, items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "kind": "commit",
        "index": index,
        "instant": encode(Instant(at_ns)),
        "items": items,
    }


def fact_item(
    ref_: EntityRef, field: str, value: object, at_ns: int
) -> dict[str, object]:
    at = Instant(at_ns)
    return {
        "kind": "operation",
        "partition": "p1",
        "proposal": encode(
            FactWrite(
                (ref_, field),
                value,
                stamp(at_ns),
                Interval(at, None),
            )
        ),
    }


def lowalt_registry() -> MemoryRegistry:
    data = overlay()
    types = tuple(TypeDescriptor(t["id"], tuple(t["parents"])) for t in data["types"])
    types = types + tuple(
        TypeDescriptor(t["id"], tuple(t["parents"]))
        for t in (
            {"id": "oo:ModelObject", "parents": ()},
            {"id": "oo:WorkObject", "parents": ("oo:ModelObject",)},
            {"id": "oo:PhysicalAsset", "parents": ("oo:ModelObject",)},
            {"id": "oo:Equipment", "parents": ("oo:PhysicalAsset",)},
            {"id": "oo:Vehicle", "parents": ("oo:Equipment",)},
            {"id": "oo:Aircraft", "parents": ("oo:Vehicle",)},
            {"id": "oo:UAV", "parents": ("oo:Aircraft",)},
            {"id": "oo:Facility", "parents": ("oo:PhysicalAsset",)},
            {"id": "oo:Task", "parents": ("oo:WorkObject",)},
        )
    )
    fields = tuple(
        FieldDescriptor(f["id"], f["type"], f["schema"], f["metadata"])
        for f in data["fields"]
    )
    return MemoryRegistry(types, fields)


# -- every shared lowalt schema renders plain, understandable text ------------


def expected_schemas() -> dict[str, dict[str, object]]:
    """Schemas the pack's engines emit, with representative real payloads."""
    return {
        "lowalt.task.created": {
            "task": "task-1",
            "kind": "parcel",
            "priority": 1,
            "origin": "vertiport-1",
            "destination": "customer-3",
        },
        "lowalt.task.assigned": {
            "task": "task-1",
            "vehicle": "drone-1",
            "algorithm": "nearest",
        },
        "lowalt.task.started": {"task": "task-1", "vehicle": "drone-1"},
        "lowalt.task.picked_up": {"task": "task-1", "vehicle": "drone-1"},
        "lowalt.task.completed": {
            "task": "task-1",
            "vehicle": "drone-1",
            "latency_s": 41.5,
        },
        "lowalt.task.failed": {
            "task": "task-2",
            "vehicle": "drone-2",
            "reason": "battery reserve",
        },
        "lowalt.airspace.conflict": {
            "a": "drone-1",
            "b": "drone-2",
            "horizontal_m": 15.0,
            "vertical_m": 4.0,
        },
        "lowalt.airspace.conflict_resolved": {
            "a": "drone-1",
            "b": "drone-2",
            "action": "altitude_layers",
        },
        "lowalt.airspace.geofence_entered": {
            "vehicle": "drone-3",
            "zone": "central-no-fly",
        },
        "lowalt.airspace.zone_activated": {
            "zone": "accident-zone",
            "reason": "incident",
        },
        "lowalt.airspace.zone_deactivated": {
            "zone": "accident-zone",
            "reason": "cleared",
        },
        "lowalt.energy.low": {"vehicle": "drone-4", "soc": 0.18},
        "lowalt.energy.charging_started": {
            "vehicle": "drone-4",
            "pad": "pad-1",
            "soc": 0.15,
        },
        "lowalt.energy.charging_finished": {
            "vehicle": "drone-4",
            "pad": "pad-1",
            "soc": 0.9,
        },
        "lowalt.weather.hold": {
            "vehicle": "drone-5",
            "zone": "gust-cell",
            "reason": "gust 14 m/s",
            "wind_mps": 14.0,
        },
        "lowalt.weather.release": {
            "vehicle": "drone-5",
            "zone": "gust-cell",
            "reason": "wind subsided",
            "wind_mps": 8.0,
        },
        "lowalt.task.preempted": {
            "task": "task-1",
            "vehicle": "drone-1",
            "urgent_task": "task-9",
        },
        "lowalt.task.resumed": {"task": "task-1", "vehicle": "drone-1"},
        "lowalt.task.replanned": {
            "task": "task-1",
            "vehicle": "drone-1",
            "reason": "zone closed",
            "zone": "accident-zone",
        },
        "lowalt.inspection.observed": {
            "task": "task-3",
            "vehicle": "drone-2",
            "position": [10.0, 20.0, 40.0],
            "distance_m": 25.0,
        },
        "lowalt.spraying.applied": {
            "task": "task-4",
            "vehicle": "drone-3",
            "position": [5.0, 6.0, 4.0],
            "distance_m": 30.0,
        },
        "lowalt.patrol.visited": {
            "task": "task-5",
            "vehicle": "drone-4",
            "position": [0.0, 0.0, 60.0],
        },
        "lowalt.site.closed": {"site": "vertiport-1"},
        "lowalt.mobility.pad_wait": {
            "task": "task-6",
            "vehicle": "taxi-1",
            "site": "vertiport-2",
        },
        "lowalt.energy.swap_started": {"vehicle": "drone-4", "pad": "pad-1"},
        "lowalt.energy.swap_finished": {
            "vehicle": "drone-4",
            "pad": "pad-1",
            "soc": 1.0,
        },
    }


def test_every_lowalt_schema_renders_plain_text(tmp_path: Path) -> None:
    registry_path = tmp_path / "runtime.registry.json"
    registry_path.write_text(json.dumps(lowalt_registry().to_data()))
    (tmp_path / "scenario.json").write_text("{}")
    projector = NarrativeProjector.from_directory(tmp_path)
    payloads = expected_schemas()
    # Every schema the pack declares is rendered (no schema silently skipped).
    for schema in LOWALT_NARRATIVES:
        if schema not in payloads:
            raise AssertionError(f"untested declared schema {schema}")
    events: list[dict[str, object]] = []
    for at_ns, (schema, payload) in enumerate(payloads.items(), start=1):
        events.extend(
            projector.consume(
                record(at_ns, at_ns, [emit_item(message(schema, payload, at_ns))])
            )
        )
    texts = [str(event["text"]) for event in events]
    assert len(texts) == len(payloads)
    for schema, text in zip(payloads, texts):
        assert text and text.strip() == text, (schema, text)
        for token in ("{", "}", "None", "nan"):
            assert token not in text, (schema, text)
    # Spot-check the rendered wording uses actual committed values.
    assert "task-1" in texts[0]
    assert "drone-1" in texts[1]
    # Pack completed template is intentionally terse; the latency value is a
    # KPI, not a narrative placeholder. Check it is genuinely dropped.
    assert "41.5" not in texts[4]
    assert "central-no-fly" in texts[8]
    assert "drone-4" in texts[11]
    # Task kind drives category, not a flat logistics default.
    created = events[0]
    assert created["category"] == "logistics"  # kind parcel
    kinds = {kind: i for i, kind in enumerate(TASK_KINDS, start=1)}
    medical = projector.consume(
        record(
            100,
            100,
            [
                emit_item(
                    message(
                        "lowalt.task.created",
                        {"task": "task-m", "kind": "medical", "priority": 2},
                        100,
                    )
                )
            ],
        )
    )[0]
    assert medical["category"] == "emergency"
    del kinds


def test_scenario_override_pins_template_over_pack(tmp_path: Path) -> None:
    registry_path = tmp_path / "runtime.registry.json"
    registry_path.write_text(json.dumps(lowalt_registry().to_data()))
    (tmp_path / "scenario.json").write_text(
        json.dumps(
            {
                "event_narratives": {
                    "lowalt.task.completed": {
                        "template": "Delivery done: {task} by {vehicle}",
                        "category": "logistics",
                        "severity": "notice",
                    }
                }
            }
        )
    )
    projector = NarrativeProjector.from_directory(tmp_path)
    events = projector.consume(
        record(
            1,
            1,
            [
                emit_item(
                    message(
                        "lowalt.task.completed",
                        {"task": "t", "vehicle": "v", "latency_s": 5},
                    )
                )
            ],
        )
    )
    assert events[0]["text"] == "Delivery done: t by v"
    assert events[0]["severity"] == "notice"


# -- cursor checkpoints restore ALL state -------------------------------------


def test_kpi_cursor_roundtrip_preserves_counters_dedup_and_baseline() -> None:
    reg = lowalt_registry()
    reg = MemoryRegistry(
        reg.types + (TypeDescriptor("logistics:Order", ("oo:Task",)),),
        reg.fields,
    )
    air = ref("uav")
    frame = record(
        1,
        0,
        [
            emit_item(
                message(
                    "lowalt.task.created",
                    {"task": "t1", "kind": "parcel", "priority": 1},
                )
            ),
            fact_item(air, "lowalt.energy_used_j", 5000.0, 0),
            fact_item(air, "lowalt.position", [0.0, 0.0, 50.0], 0),
        ],
    )
    projector = KPIProjector(reg)
    projector.consume(frame)
    projector.consume(
        record(
            2,
            5_000_000_000,
            [
                emit_item(
                    message(
                        "packs.logistics.transition",
                        {
                            "order": "order-1",
                            "state": "queued",
                            "deadline_ns": 30_000_000_000,
                        },
                    )
                )
            ],
        )
    )
    cursor = projector.cursor()
    blob = json.loads(json.dumps(cursor))
    assert blob["version"] == 1
    assert blob["counts"]["tasks_created"] == 1
    assert blob["deadlines"] == {"order-1": 30_000_000_000}
    assert ["uav", 0, "lowalt.energy_used_j", 5000.0] in blob["energy"]

    restored = KPIProjector.from_cursor(reg, blob)
    assert restored.counts == projector.counts
    assert restored.created == projector.created
    assert restored.deadlines == projector.deadlines
    assert restored.energy == projector.energy
    assert restored.energy_used == pytest.approx(projector.energy_used)
    assert restored.messages == projector.messages
    # Dedup survives: replaying the same committed message adds nothing.
    restored.consume(frame)
    assert restored.counts == projector.counts
    assert restored.energy_used == pytest.approx(projector.energy_used)
    # Energy baseline survives: the 5000->3000 J discharge is measured once,
    # exactly as the never-interrupted projector would have measured it.
    restored.consume(
        record(
            3,
            10_000_000_000,
            [fact_item(air, "lowalt.energy_used_j", 3000.0, 10_000_000_000)],
        )
    )
    assert restored.energy_used == pytest.approx(2000.0)
    assert restored.snapshot()["energy_used_kj"]["value"] == pytest.approx(2.0)
    # Instance-method path is equivalent to the classmethod.
    instance = KPIProjector(reg)
    instance.restore_cursor(blob)
    assert instance.counts == projector.counts


def test_kpi_cursor_restores_latency_and_traffic_attribution() -> None:
    reg = lowalt_registry()
    projector = KPIProjector(reg)
    projector.consume(
        record(
            1,
            2_000_000_000,
            [
                emit_item(
                    message(
                        "lowalt.task.created",
                        {"task": "t", "kind": "parcel", "priority": 1},
                    )
                ),
                emit_item(
                    message(
                        "lowalt.task.completed",
                        {"task": "t", "vehicle": "u", "latency_s": 12.0},
                    )
                ),
                emit_item(
                    message("traffic.report.sent", {"incident": "i1", "report": "r1"})
                ),
                emit_item(message("traffic.capture.stored", {"capture": "c1"})),
                {
                    "kind": "edge",
                    "edge": encode(
                        {
                            "relation": "traffic.capture-incident",
                            "source": ref("c1", 0, "traffic:Capture").to_data(),
                            "target": ref("i1", 0, "traffic:Incident").to_data(),
                        }
                    ),
                },
            ],
        )
    )
    restored = KPIProjector.from_cursor(reg, json.loads(json.dumps(projector.cursor())))
    assert restored.latencies == [12.0]
    assert restored.capture_incidents == projector.capture_incidents
    assert restored.actor_incidents == projector.actor_incidents
    assert restored.snapshot() == projector.snapshot()


def test_kpi_cursor_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        KPIProjector.from_cursor(lowalt_registry(), {"version": 99})


def test_narrative_cursor_roundtrip_restores_dedup_positions_kinds(
    tmp_path: Path,
) -> None:
    (tmp_path / "runtime.registry.json").write_text(
        json.dumps(lowalt_registry().to_data())
    )
    (tmp_path / "scenario.json").write_text("{}")
    projector = NarrativeProjector.from_directory(tmp_path)
    msg = message(
        "lowalt.task.created", {"task": "t1", "kind": "parcel", "priority": 1}
    )
    frame = record(
        1,
        1,
        [
            fact_item(ref("uav"), "lowalt.position", [3.0, 4.0, 20.0], 1),
            emit_item(msg),
        ],
    )
    events = projector.consume(frame)
    assert events[0]["category"] == "logistics"
    cursor = json.loads(json.dumps(projector.cursor()))
    assert cursor["task_kinds"] == {"t1": "parcel"}
    assert cursor["seen"] == [msg.id]

    restored = NarrativeProjector.from_directory(tmp_path)
    restored.restore_cursor(cursor)
    # Deduped message narrates nothing after restore.
    assert restored.consume(frame) == []
    # Task-kind memory drives the category of later lifecycle events.
    assigned = restored.consume(
        record(
            2,
            2,
            [
                emit_item(
                    message(
                        "lowalt.task.assigned",
                        {"task": "t1", "vehicle": "uav", "algorithm": "nearest"},
                    )
                )
            ],
        )
    )[0]
    assert assigned["category"] == "logistics"
    # Restored stored position is attached to the vehicle entity event.
    assert assigned["position"] == [3.0, 4.0, 20.0]
    # Wrapper classmethod stays equivalent.
    wrapped = NarrativeProjector.projection_context(tmp_path, cursor)
    assert wrapped.consume(frame) == []


# -- on_time_rate: only committed deadlines count ------------------------------


def test_on_time_rate_uses_committed_deadlines_only() -> None:
    reg = MemoryRegistry(
        lowalt_registry().types + (TypeDescriptor("logistics:Order", ("oo:Task",)),),
        lowalt_registry().fields,
    )
    projector = KPIProjector(reg)

    def transition(
        order: str, state: str, at_s: int, deadline: int | None
    ) -> dict[str, object]:
        payload: dict[str, object] = {"order": order, "state": state}
        if deadline is not None:
            payload["deadline_ns"] = deadline
        return emit_item(message("packs.logistics.transition", payload, at_s))

    # order-1: deadline committed before delivery, on time.
    # order-2: deadline committed, delivered late.
    # order-3: delivered with NO committed deadline -> stays unmeasured.
    projector.consume(
        record(
            1,
            0,
            [
                transition("order-1", "queued", 0, 60_000_000_000),
                transition("order-2", "queued", 0, 10_000_000_000),
                transition("order-3", "queued", 0, None),
            ],
        )
    )
    assert projector.snapshot()["on_time_rate"]["value"] is None
    projector.consume(
        record(
            2,
            20_000_000_000,
            [
                transition("order-1", "delivered", 20_000_000_000, None),
                transition("order-2", "delivered", 20_000_000_000, None),
                transition("order-3", "delivered", 20_000_000_000, None),
            ],
        )
    )
    final = projector.snapshot()
    # Only the two deliveries with a committed deadline are measured;
    # order-3 stays in the unmeasured set instead of diluting the rate.
    assert final["on_time_rate"]["value"] == pytest.approx(1 / 2)
    assert final["on_time_rate"]["unit"] == "ratio"
    assert projector.unmeasured_deliveries == {"order-3"}
    assert projector.cursor()["unmeasured_deliveries"] == ["order-3"]


# -- real parcel-delivery run: journal counts vs projector arithmetic ----------


def test_parcel_delivery_run_journal_matches_kpi_and_narratives(tmp_path: Path) -> None:
    from aeroagentsim.platform.simulation import RunSession
    from aeroagentsim.services.storage import RunStorage

    prepared = TemplateRegistry().apply("parcel-delivery", {"fleet_size": 2})
    prepared.document["run"]["until_ns"] = 150_000_000_000
    path = tmp_path / "run"
    with RunSession(
        load_scenario(prepared.document, base=prepared.base), path
    ) as session:
        session.run()
    assert session.storage.metadata()["status"] == "completed"
    storage = RunStorage(path)
    records = storage.records(1, len(storage.entries))
    kpis = KPIProjector.from_directory(path)
    narratives = NarrativeProjector.from_directory(path)
    counts = {
        name: 0
        for name in (
            "tasks_created",
            "tasks_completed",
            "separation_conflicts",
            "geofence_violations",
            "weather_holds",
        )
    }
    schemas = {
        "lowalt.task.created": "tasks_created",
        "lowalt.task.completed": "tasks_completed",
        "lowalt.airspace.conflict": "separation_conflicts",
        "lowalt.airspace.geofence_entered": "geofence_violations",
        "lowalt.weather.hold": "weather_holds",
    }
    energy_last = {}
    energy_used = 0.0
    latencies = []
    halfway = len(records) // 2
    checkpoint = None
    narrative_checkpoint = None
    expected_suffix = []
    for index, raw in enumerate(records):
        projected = project(raw)
        for message_row in projected["messages"]:
            schema = message_row["schemaId"]
            if schema in schemas:
                counts[schemas[schema]] += 1
            if schema == "lowalt.task.completed":
                latencies.append(message_row["payload"]["latency_s"])
        for fact in projected["facts"]:
            if fact["fieldId"] == "lowalt.motion_energy_j":
                key = (fact["entity"]["id"], fact["entity"]["generation"])
                if key in energy_last:
                    energy_used += max(0, energy_last[key] - fact["value"])
                energy_last[key] = fact["value"]
        kpis.consume(raw)
        events = narratives.consume(raw)
        assert all(event["text"].strip() for event in events)
        if index >= halfway:
            expected_suffix.extend(events)
        if index + 1 == halfway:
            checkpoint = kpis.cursor()
            narrative_checkpoint = narratives.cursor()
    final = kpis.snapshot()
    assert counts["tasks_created"] > 0 and counts["tasks_completed"] > 0
    for name, value in counts.items():
        assert final[name]["value"] == value
    assert final["energy_used_kj"]["value"] == pytest.approx(energy_used / 1000)
    assert final["mean_task_latency_s"]["value"] == pytest.approx(
        sum(latencies) / len(latencies)
    )
    assert (
        final["completion_rate"]["value"]
        == counts["tasks_completed"] / counts["tasks_created"]
    )
    assert final["on_time_rate"]["value"] is None  # Parcel schema commits no deadlines.
    restored = KPIProjector.from_directory(path)
    restored_narratives = NarrativeProjector.from_directory(path)
    restored.restore_cursor(json.loads(json.dumps(checkpoint)))
    restored_narratives.restore_cursor(json.loads(json.dumps(narrative_checkpoint)))
    suffix = []
    for raw in records[halfway:]:
        restored.consume(raw)
        suffix.extend(restored_narratives.consume(raw))
    assert restored.snapshot() == final
    assert suffix == expected_suffix
