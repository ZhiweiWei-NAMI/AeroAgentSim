"""Template KPI contracts and arithmetic over synthetic committed events."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from aerokernel.codec import encode
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit, Message
from aerokernel.operations import FactWrite
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.time import Instant, Interval, Stamp
from aerokernel.values import freeze

from aeroagentsim.services.kpis import METRICS, KPIProjector

TEMPLATES = Path(__file__).resolve().parents[2] / "scenarios/templates"
NEW_METRICS = (
    "medical_mean_task_latency_s",
    "medical_p95_task_latency_s",
    "spraying_distance_m",
    "battery_swaps",
    "inspection_observations",
    "patrol_visits",
)


def registry() -> MemoryRegistry:
    return MemoryRegistry(
        (TypeDescriptor("oo:Aircraft"),),
        (
            FieldDescriptor(
                "position",
                "oo:Aircraft",
                {"type": "vector", "length": 3, "items": {"type": "number"}},
                {"unit": "m", "frame": "enu", "role": "state"},
            ),
        ),
    )


def event(
    schema: str, payload: dict[str, Any], at_s: int, identifier: str
) -> dict[str, Any]:
    at = Instant(at_s * 1_000_000_000)
    message = Message(
        identifier, "event", schema, "partition", "business", 0,
        at, at, freeze(payload), (),
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


def position(value: list[float], at_s: int) -> dict[str, Any]:
    at = Instant(at_s * 1_000_000_000)
    ref = EntityRef("run", "0", "uav", 0, "oo:Aircraft")
    return {
        "proposal": encode(
            FactWrite(
                (ref, "position"), value,
                Stamp(
                    clock_id="canonical", mapping_id="canonical",
                    numerator=at.ns, denominator=1,
                ),
                Interval(at, None),
            )
        )
    }


@pytest.mark.parametrize(
    "template", sorted(TEMPLATES.glob("*/template.yaml")),
    ids=lambda path: path.parent.name,
)
def test_every_declared_template_kpi_is_registered_and_exposed(template: Path) -> None:
    declared = yaml.safe_load(template.read_text(encoding="utf-8"))["kpis"]
    assert declared
    snapshot = KPIProjector(registry()).snapshot()
    for name in declared:
        assert name in METRICS, f"{template.parent.name}: {name}"
        assert name in snapshot
        assert snapshot[name]["unit"] == METRICS[name][0]
        assert snapshot[name]["label"] == METRICS[name][1]


def test_unrelated_work_leaves_template_metrics_unmeasured() -> None:
    projector = KPIProjector(registry())
    assert all(projector.snapshot()[name]["value"] is None for name in NEW_METRICS)
    final = projector.consume(record(1, 10, [
        event("lowalt.task.created", {
            "task": "medical-in-name-only", "kind": "parcel", "priority": 1,
        }, 0, "created"),
        event("lowalt.task.completed", {
            "task": "medical-in-name-only", "vehicle": "uav", "latency_s": 10,
        }, 10, "completed"),
        event("lowalt.energy.charging_finished", {
            "vehicle": "uav", "pad": "pad", "soc": 0.9,
        }, 10, "charged"),
    ]))
    assert final["mean_task_latency_s"]["value"] == 10
    assert all(final[name]["value"] is None for name in NEW_METRICS)
    restored = KPIProjector.from_cursor(
        registry(), json.loads(json.dumps(projector.cursor()))
    )
    assert restored.snapshot() == final


def test_medical_latency_joins_creation_kind_to_completion_and_deduplicates() -> None:
    projector = KPIProjector(registry())
    projector.consume(record(1, 5, [
        event("lowalt.task.created", {
            "task": task, "kind": kind, "priority": 1,
        }, at_s, "created-" + task)
        for task, kind, at_s in (
            ("m1", "medical", 0), ("m2", "medical", 5),
            ("unfinished", "medical", 0), ("parcel", "parcel", 0),
        )
    ]))
    assert projector.snapshot()["medical_mean_task_latency_s"]["value"] is None
    final = projector.consume(record(2, 40, [
        event("lowalt.task.completed", {
            "task": task, "vehicle": "uav", "latency_s": latency,
        }, at_s, "completed-" + task)
        for task, latency, at_s in (("m1", 10, 10), ("m2", 30, 35), ("parcel", 40, 40))
    ]))
    assert final["tasks_completed"]["value"] == 3
    assert final["mean_task_latency_s"]["value"] == pytest.approx(80 / 3)
    assert final["medical_mean_task_latency_s"]["value"] == 20
    assert final["medical_p95_task_latency_s"]["value"] == pytest.approx(29)
    duplicate = projector.consume(record(3, 41, [
        event("lowalt.task.completed", {
            "task": "m1", "vehicle": "uav", "latency_s": 999,
        }, 41, "another-completion-m1"),
    ]))
    assert duplicate == final


@pytest.mark.parametrize("latency", [0, 7.5])
def test_single_medical_completion_has_matching_mean_and_p95(latency: float) -> None:
    projector = KPIProjector(registry())
    final = projector.consume(record(1, 10, [
        event("lowalt.task.created", {
            "task": "medical", "kind": "medical", "priority": 100,
        }, 0, "created"),
        event("lowalt.task.completed", {
            "task": "medical", "vehicle": "uav", "latency_s": latency,
        }, 10, "completed"),
    ]))
    assert final["medical_mean_task_latency_s"]["value"] == latency
    assert final["medical_p95_task_latency_s"]["value"] == latency


def test_battery_swaps_count_finishes_and_preserve_repeated_swaps_at_same_pad() -> None:
    projector = KPIProjector(registry())
    pending = projector.consume(record(1, 0, [
        event("lowalt.energy.swap_started", {
            "vehicle": "uav", "pad": "rack",
        }, 0, "started-0"),
    ]))
    assert pending["battery_swaps"]["value"] == 0
    finishes = []
    for index in range(3):
        finishes.append(event("lowalt.energy.swap_finished", {
            "vehicle": "uav", "pad": "rack", "soc": 0.9,
        }, (index + 1) * 10, f"finished-{index}"))
    frame = record(2, 31, finishes + [
        event("lowalt.energy.swap_started", {
            "vehicle": "uav", "pad": "rack",
        }, 31, "still-pending"),
    ])
    final = projector.consume(frame)
    assert final["battery_swaps"]["value"] == 3
    assert projector.consume(frame) == final


@pytest.mark.parametrize("kind,schema,metric,total", [
    ("inspection", "lowalt.inspection.observed", "inspection_observations", 15),
    ("survey", "lowalt.inspection.observed", "inspection_observations", 2),
    ("patrol", "lowalt.patrol.visited", "patrol_visits", 24),
])
def test_work_counts_measure_events_even_at_revisited_positions(
    kind: str, schema: str, metric: str, total: int,
) -> None:
    projector = KPIProjector(registry())
    queued = projector.consume(record(1, 0, [
        event("lowalt.task.created", {
            "task": "work", "kind": kind, "priority": 1,
        }, 0, "created"),
    ]))
    assert queued[metric]["value"] == 0
    payload = {"task": "work", "vehicle": "uav", "position": [3, 4, 20]}
    if kind != "patrol":
        payload["distance_m"] = 0.0
    observations = [event(schema, payload, index + 1, f"observed-{index}")
                    for index in range(total)]
    frame = record(2, total, observations + [observations[0]])
    final = projector.consume(frame)
    assert final[metric]["value"] == total
    assert projector.consume(frame) == final


def test_spraying_distance_uses_applied_segments_and_excludes_transit() -> None:
    projector = KPIProjector(registry())
    transit = projector.consume(record(1, 1, [
        position([0, 0, 20], 0), position([1000, 0, 20], 1),
        event("lowalt.task.created", {
            "task": "field", "kind": "spraying", "priority": 1,
        }, 0, "created"),
    ]))
    assert transit["distance_flown_km"]["value"] == 1
    assert transit["spraying_distance_m"]["value"] is None
    segments = [event("lowalt.spraying.applied", {
        "task": "field", "vehicle": "uav", "position": point, "distance_m": moved,
    }, index + 2, f"applied-{index}") for index, (point, moved) in enumerate((
        ([1003, 4, 20], 5.0), ([2000, 0, 20], 0.0), ([2012.5, 0, 20], 12.5),
    ))]
    frame = record(2, 5, [
        *segments, segments[0], position([3000, 0, 20], 5),
        event("lowalt.inspection.observed", {
            "task": "line", "vehicle": "uav", "position": [3000, 0, 20],
            "distance_m": 100,
        }, 5, "inspection"),
    ])
    final = projector.consume(frame)
    assert final["spraying_distance_m"]["value"] == 17.5
    assert final["spraying_distance_m"]["unit"] == "m"
    assert "segment" in final["spraying_distance_m"]["label"]
    assert final["distance_flown_km"]["value"] == 3
    assert projector.consume(record(3, 5, segments)) == final


def test_zero_applied_distance_is_a_real_measurement() -> None:
    projector = KPIProjector(registry())
    final = projector.consume(record(1, 1, [
        event("lowalt.spraying.applied", {
            "task": "field", "vehicle": "uav", "position": [0, 0, 20],
            "distance_m": 0.0,
        }, 1, "connector-arrival"),
    ]))
    assert final["spraying_distance_m"]["value"] == 0


@pytest.mark.parametrize("distance", [None, True, -1, "5"])
def test_invalid_spraying_measurements_are_not_replaced_by_zero(distance: Any) -> None:
    projector = KPIProjector(registry())
    with pytest.raises(ValueError, match="KPI spraying"):
        projector.consume(record(1, 1, [
            event("lowalt.spraying.applied", {
                "task": "field", "vehicle": "uav", "position": [0, 0, 20],
                "distance_m": distance,
            }, 1, "invalid"),
        ]))
    assert projector.snapshot()["spraying_distance_m"]["value"] is None


def test_template_measurements_and_message_dedup_survive_cursor_restore() -> None:
    reg = registry()
    projector = KPIProjector(reg)
    prefix = record(1, 1, [
        event("lowalt.task.created", {
            "task": "m1", "kind": "medical", "priority": 100,
        }, 0, "created-m1"),
        event("lowalt.task.created", {
            "task": "m2", "kind": "medical", "priority": 100,
        }, 0, "created-m2"),
        event("lowalt.task.completed", {
            "task": "m1", "vehicle": "uav", "latency_s": 1,
        }, 1, "completed-m1"),
        event("lowalt.energy.swap_started", {
            "vehicle": "uav", "pad": "rack",
        }, 1, "started"),
        event("lowalt.spraying.applied", {
            "task": "field", "vehicle": "uav", "position": [3, 4, 20],
            "distance_m": 5,
        }, 1, "applied"),
        event("lowalt.inspection.observed", {
            "task": "line", "vehicle": "uav", "position": [3, 4, 20],
            "distance_m": 5,
        }, 1, "inspected"),
        event("lowalt.patrol.visited", {
            "task": "patrol", "vehicle": "uav", "position": [3, 4, 20],
        }, 1, "visited"),
    ])
    before = projector.consume(prefix)
    restored = KPIProjector.from_cursor(reg, json.loads(json.dumps(projector.cursor())))
    assert restored.snapshot() == before
    assert restored.consume(prefix) == before
    final = restored.consume(record(2, 10, [
        event("lowalt.task.completed", {
            "task": "m2", "vehicle": "uav", "latency_s": 10,
        }, 10, "completed-m2"),
        event("lowalt.energy.swap_finished", {
            "vehicle": "uav", "pad": "rack", "soc": 0.9,
        }, 10, "finished"),
        event("lowalt.spraying.applied", {
            "task": "field", "vehicle": "uav", "position": [10, 4, 20],
            "distance_m": 7,
        }, 10, "applied-next"),
    ]))
    assert final["medical_mean_task_latency_s"]["value"] == 5.5
    assert final["medical_p95_task_latency_s"]["value"] == pytest.approx(9.55)
    assert final["battery_swaps"]["value"] == 1
    assert final["spraying_distance_m"]["value"] == 12
    assert final["inspection_observations"]["value"] == 1
    assert final["patrol_visits"]["value"] == 1


def test_traffic_completion_survives_template_kpi_cursor_restore() -> None:
    reg = registry()
    projector = KPIProjector(reg)
    incident = {"$ref": EntityRef(
        "run", "0", "incident.01", 0, "aas:TrafficIncident"
    ).to_data()}
    actor = {"$ref": EntityRef(
        "run", "0", "uav.bravo", 0, "oo:Aircraft"
    ).to_data()}
    # Traffic lifecycle events have their own schemas and carry no task kind.
    prefix = record(1, 9, [
        event("traffic.report.sent", {
            "incident": incident, "report": "report", "actor": "reporter",
        }, 8, "reported"),
        event("traffic.award.committed", {
            "incident": incident, "actor": actor,
        }, 9, "awarded"),
    ])
    pending = projector.consume(prefix)
    assert pending["tasks_created"]["value"] == 1
    assert pending["tasks_assigned"]["value"] == 1
    assert pending["tasks_completed"]["value"] == 0
    restored = KPIProjector.from_cursor(reg, json.loads(json.dumps(projector.cursor())))
    assert restored.snapshot() == pending
    assert restored.consume(prefix) == pending

    completion = record(2, 13, [
        event("traffic.capture.stored", {
            "incident": incident, "actor": actor, "capture": "capture",
        }, 10, "stored"),
        event("traffic.capture.accepted", {
            "incident": incident, "actor": actor, "capture": "capture",
        }, 13, "accepted"),
    ])
    final = restored.consume(completion)
    assert final["tasks_created"]["value"] == 1
    assert final["tasks_assigned"]["value"] == 1
    assert final["tasks_completed"]["value"] == 1
    assert final["captures_stored"]["value"] == 1
    assert final["uploads_accepted"]["value"] == 1
    assert final["completion_rate"]["value"] == 1
    assert final["mean_task_latency_s"]["value"] == 5
    assert final["p95_task_latency_s"]["value"] == 5
    assert all(final[name]["value"] is None for name in NEW_METRICS)
    assert restored.consume(completion) == final
    assert KPIProjector.from_cursor(reg, restored.cursor()).snapshot() == final


def test_failed_traffic_capture_does_not_complete_the_reported_task() -> None:
    projector = KPIProjector(registry())
    final = projector.consume(record(1, 13, [
        event("traffic.report.sent", {
            "incident": "accident", "report": "report", "actor": "reporter",
        }, 8, "reported"),
        event("traffic.award.committed", {
            "incident": "accident", "actor": "uav",
        }, 9, "awarded"),
        event("traffic.capture.failed", {
            "incident": "accident", "actor": "uav", "request_id": "request",
            "source_cut": 1, "reason": "camera needs the built console",
        }, 13, "failed"),
    ]))
    assert final["tasks_created"]["value"] == 1
    assert final["tasks_assigned"]["value"] == 1
    assert final["tasks_completed"]["value"] == 0
    assert final["captures_stored"]["value"] == 0
    assert final["uploads_accepted"]["value"] == 0
    assert final["mean_task_latency_s"]["value"] is None


def test_incident_labels_make_cooperation_scope_explicit() -> None:
    projector = KPIProjector(registry())
    final = projector.consume(record(1, 0, [
        event("traffic.report.sent", {
            "incident": "accident", "report": "report", "actor": "reporter",
        }, 0, "reported"),
    ]))
    assert final["accident_reported"]["value"] == 1
    for name in ("incidents_occurred", "incidents_noticed", "incidents_photographed"):
        assert final[name]["value"] == 0
        assert "Cooperation incidents" in final[name]["label"]
        assert "协作事件" in final[name]["label"]
    assert "cooperation incident" in final["incident_response_s"]["label"]
