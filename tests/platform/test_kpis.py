"""KPI math against synthetic committed journal frames, with no engines."""

from __future__ import annotations

from typing import Any

import pytest
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp
from aerokernel.values import freeze

from aeroagentsim.services.kpis import KPIProjector


def registry() -> MemoryRegistry:
    return MemoryRegistry(
        (
            TypeDescriptor("oo:Vehicle"),
            TypeDescriptor("oo:Aircraft", ("oo:Vehicle",)),
            TypeDescriptor("Car", ("oo:Vehicle",)),
        ),
        (
            FieldDescriptor(
                "position",
                "oo:Vehicle",
                {"type": "vector", "length": 3, "items": {"type": "number"}},
                {"unit": "m", "frame": "enu", "role": "state"},
            ),
            FieldDescriptor(
                "energy",
                "oo:Vehicle",
                {"type": "number"},
                {"unit": {"symbol": "J"}, "role": "state"},
            ),
        ),
    )


def event(
    schema: str, payload: dict[str, Any], at_s: int, identifier: str
) -> dict[str, Any]:
    at = Instant(at_s * 1_000_000_000)
    message = Message(
        identifier,
        "event",
        schema,
        "partition",
        "business",
        0,
        at,
        at,
        freeze(payload),
        (),
    )
    return {
        "proposal": encode(Emit("event", schema, schema, at, payload)),
        "message": encode(message),
    }


def record(index: int, at_s: int, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "commit",
        "index": index,
        "instant": encode(Instant(at_s * 1_000_000_000)),
        "items": items,
    }


def fact(ref: EntityRef, field: str, value: Any, at_s: int) -> dict[str, Any]:
    at = Instant(at_s * 1_000_000_000)
    return {
        "proposal": encode(
            FactWrite(
                (ref, field),
                value,
                Stamp(clock_id="canonical", mapping_id="canonical", numerator=at.ns, denominator=1),
                Interval(at, None),
            )
        )
    }


def test_synthetic_journal_math_counts_discharge_and_aircraft_distance() -> None:
    projector = KPIProjector(registry())
    initial = projector.snapshot()
    assert initial["tasks_created"]["value"] == 0
    assert initial["energy_used_kj"]["value"] is None
    assert initial["distance_flown_km"]["value"] is None
    assert initial["mean_task_latency_s"]["value"] is None
    air = EntityRef("run", "0", "uav", 0, "oo:Aircraft")
    ground = EntityRef("run", "0", "car", 0, "Car")
    created = event(
        "lowalt.task.created",
        {"task": "a", "kind": "parcel", "priority": 1},
        0,
        "created-a",
    )
    journal = [
        record(
            1,
            0,
            [
                created,
                event(
                    "lowalt.task.created",
                    {"task": "b", "kind": "medical", "priority": 1},
                    0,
                    "created-b",
                ),
                fact(air, "energy", 10000, 0),
                fact(air, "position", [0, 0, 0], 0),
                fact(ground, "position", [0, 0, 0], 0),
            ],
        ),
        record(
            2,
            10,
            [
                event(
                    "lowalt.task.completed",
                    {"task": "a", "vehicle": "uav", "latency_s": 10},
                    10,
                    "completed-a",
                ),
                fact(air, "energy", 9000, 10),
                fact(air, "position", [600, 800, 0], 10),
                fact(ground, "position", [10000, 0, 0], 10),
                event(
                    "lowalt.airspace.conflict",
                    {"a": "uav", "b": "other", "horizontal_m": 1, "vertical_m": 1},
                    10,
                    "conflict",
                ),
                event(
                    "lowalt.airspace.geofence_entered",
                    {"vehicle": "uav", "zone": "zone"},
                    10,
                    "geofence",
                ),
                event(
                    "lowalt.weather.hold",
                    {"reason": "Wind", "vehicle": "uav"},
                    10,
                    "hold",
                ),
            ],
        ),
        record(
            3,
            20,
            [
                fact(air, "energy", 12000, 20),
                created,
                {"kind": "delivery", "message": created["message"]},
            ],
        ),
        record(
            4,
            30,
            [
                fact(air, "energy", 10000, 30),
                fact(air, "position", [1200, 1600, 0], 30),
                event(
                    "lowalt.task.completed",
                    {"task": "b", "vehicle": "uav", "latency_s": 30},
                    30,
                    "completed-b",
                ),
            ],
        ),
    ]
    for frame in journal:
        final = projector.consume(frame)
    assert final["tasks_created"]["value"] == final["tasks_completed"]["value"] == 2
    assert final["mean_task_latency_s"]["value"] == 20
    assert final["p95_task_latency_s"]["value"] == pytest.approx(29)
    assert final["energy_used_kj"]["value"] == 3
    assert final["distance_flown_km"]["value"] == 2
    assert final["separation_conflicts"]["value"] == 1
    assert final["geofence_violations"]["value"] == 1
    assert final["weather_holds"]["value"] == 1


def test_traffic_latency_uses_report_to_accepted_upload_not_request_id() -> None:
    projector = KPIProjector(registry())
    projector.consume(
        record(
            1,
            3,
            [
                event(
                    "traffic.report.sent",
                    {"incident": "incident", "report": "report", "actor": "reporter"},
                    3,
                    "reported",
                )
            ],
        )
    )
    projector.consume(
        record(2, 4, [event("traffic.award.committed", {"actor": "uav"}, 4, "awarded")])
    )
    final = projector.consume(
        record(
            3,
            13,
            [
                event(
                    "traffic.capture.accepted",
                    {"actor": "uav", "request_id": "capture/episode-0"},
                    13,
                    "upload",
                )
            ],
        )
    )
    assert (
        final["tasks_created"]["value"]
        == final["tasks_assigned"]["value"]
        == final["tasks_completed"]["value"]
        == 1
    )
    assert final["mean_task_latency_s"]["value"] == 10
