"""Cumulative business KPIs from committed journal events and physical facts."""

from __future__ import annotations

import copy
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, cast

from aerokernel.codec import decode_record
from aerokernel.compact import expand_record
from aerokernel.ids import EntityRef
from aerokernel.messages import Emit
from aerokernel.operations import AssertEdge, FactWrite, RetractFact
from aerokernel.registry import MemoryRegistry
from aerokernel.values import FrozenValue, thaw

from .storage import RunStorage

METRICS = {
    "tasks_created": ("count", "Tasks created"),
    "tasks_assigned": ("count", "Tasks assigned"),
    "tasks_completed": ("count", "Tasks completed"),
    "tasks_failed": ("count", "Tasks failed"),
    "mean_task_latency_s": ("s", "Mean task latency"),
    "p95_task_latency_s": ("s", "95th percentile task latency"),
    "energy_used_kj": ("kJ", "Energy used"),
    "distance_flown_km": ("km", "Distance flown"),
    "separation_conflicts": ("count", "Separation conflicts"),
    "geofence_violations": ("count", "Geofence violations"),
    "weather_holds": ("count", "Weather holds"),
    "accident_reported": ("count", "Accidents reported"),
    "award_committed": ("count", "Awards committed"),
    "captures_stored": ("count", "Captures stored"),
    "uploads_accepted": ("count", "Uploads accepted"),
}


def identity(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        return f"{ref['id']}@{ref['generation']}"
    raise ValueError("KPI event: expected an entity id or typed reference")


def symbol(value: Any) -> Any:
    return (
        value.get("symbol", value.get("convention")) if hasattr(value, "get") else value
    )


def number(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"KPI {name}: expected finite numeric measurement")
    return float(value)


class KPIProjector:
    def __init__(
        self, registry: MemoryRegistry, presentation: list[dict[str, Any]] | None = None
    ) -> None:
        self.registry = registry
        self.fields = {field.id: field for field in registry.fields}
        self.position_bindings = {
            item["typeId"]: item["positionField"] for item in presentation or []
        }
        self.counts = {
            name: 0 for name, (unit, _) in METRICS.items() if unit == "count"
        }
        self.created: dict[str, int] = {}
        self.assigned: set[str] = set()
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.latencies: list[float] = []
        self.messages: set[str] = set()
        self.energy: dict[tuple[str, int, str], float] = {}
        self.positions: dict[tuple[str, int, str], tuple[float, ...]] = {}
        self.energy_used = 0.0
        self.distance = 0.0
        self.energy_observed = False
        self.position_observed = False
        self.capture_incidents: dict[str, str] = {}
        self.actor_incidents: dict[str, str] = {}

    @classmethod
    def from_directory(cls, path: Path) -> KPIProjector:
        registry = MemoryRegistry.from_data(
            json.loads((path / "runtime.registry.json").read_text())
        )
        scenario = json.loads((path / "scenario.json").read_text())
        return cls(registry, scenario.get("presentation", []))

    def _aircraft(self, ref: EntityRef) -> bool:
        return any(
            self.registry.is_a(ref.type_id, name)
            for name in ("oo:Aircraft", "oo:UAV")
            if any(t.id == name for t in self.registry.types)
        )

    def _position_field(self, ref: EntityRef, field: str) -> bool:
        matches = [
            value
            for kind, value in self.position_bindings.items()
            if self.registry.is_a(ref.type_id, kind)
        ]
        if matches:
            return bool(field == matches[0])
        descriptor = self.fields[field]
        return (
            descriptor.schema.get("type") == "vector"
            and descriptor.schema.get("length") == 3
            and symbol(descriptor.metadata.get("unit")) == "m"
            and symbol(descriptor.metadata.get("frame")) == "enu"
            and descriptor.metadata.get("role") != "config"
            and "position" in field.lower()
        )

    def _fact(self, op: FactWrite) -> None:
        ref, field = op.key
        descriptor = self.fields[field]
        key = (ref.id, ref.generation, field)
        unit = symbol(descriptor.metadata.get("unit"))
        if (
            unit in {"J", "kJ"}
            and descriptor.schema.get("type") in {"number", "integer"}
            and descriptor.metadata.get("role") != "config"
        ):
            energy = number(op.value, field) * (1000 if unit == "kJ" else 1)
            if key in self.energy:
                self.energy_used += max(0.0, self.energy[key] - energy)
            self.energy[key] = energy
            self.energy_observed = True
        if self._aircraft(ref) and self._position_field(ref, field):
            value = thaw(cast(FrozenValue, op.value))
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"KPI {field}: expected an ENU position triple")
            position = tuple(number(v, field) for v in value)
            if key in self.positions:
                self.distance += math.dist(position, self.positions[key])
            self.positions[key] = position
            self.position_observed = True

    def _created(self, task: str, at_ns: int) -> None:
        if task not in self.created:
            self.created[task] = at_ns
            self.counts["tasks_created"] += 1

    def _completed(self, task: str, latency: float) -> None:
        if latency < 0:
            raise ValueError("KPI task latency must be nonnegative")
        if task not in self.completed:
            self.completed.add(task)
            self.counts["tasks_completed"] += 1
            self.latencies.append(latency)

    def _traffic_incident(self, payload: dict[str, Any]) -> str:
        if "incident" in payload:
            return identity(payload["incident"])
        if (
            "capture" in payload
            and identity(payload["capture"]) in self.capture_incidents
        ):
            return self.capture_incidents[identity(payload["capture"])]
        if "actor" in payload and identity(payload["actor"]) in self.actor_incidents:
            return self.actor_incidents[identity(payload["actor"])]
        active = [
            task
            for task in self.created
            if task.startswith("traffic:") and task not in self.completed
        ]
        if len(active) == 1:
            return active[0].removeprefix("traffic:")
        raise ValueError(
            "KPI traffic event cannot be attributed to a reported incident"
        )

    def _event(self, schema: str, payload: dict[str, Any], at_ns: int) -> None:
        if schema == "lowalt.task.created":
            self._created("lowalt:" + identity(payload["task"]), at_ns)
        elif schema == "lowalt.task.assigned":
            task = "lowalt:" + identity(payload["task"])
            if task not in self.assigned:
                self.assigned.add(task)
                self.counts["tasks_assigned"] += 1
        elif schema == "lowalt.task.completed":
            self._completed(
                "lowalt:" + identity(payload["task"]),
                number(payload["latency_s"], "latency_s"),
            )
        elif schema == "lowalt.task.failed":
            task = "lowalt:" + identity(payload["task"])
            if task not in self.failed:
                self.failed.add(task)
                self.counts["tasks_failed"] += 1
        elif schema in {
            "lowalt.airspace.conflict",
            "lowalt.airspace.geofence_entered",
            "lowalt.weather.hold",
        }:
            name = {
                "lowalt.airspace.conflict": "separation_conflicts",
                "lowalt.airspace.geofence_entered": "geofence_violations",
                "lowalt.weather.hold": "weather_holds",
            }[schema]
            self.counts[name] += 1
        elif schema == "traffic.report.sent":
            incident = identity(payload["incident"])
            self._created("traffic:" + incident, at_ns)
            self.counts["accident_reported"] += 1
        elif schema == "traffic.award.committed":
            self.counts["award_committed"] += 1
            # Suspension awards can be committed before a report and do not create tasks.
            if not any(task.startswith("traffic:") for task in self.created):
                return
            incident = self._traffic_incident(payload)
            task = "traffic:" + incident
            if task not in self.assigned:
                self.assigned.add(task)
                self.counts["tasks_assigned"] += 1
            if "actor" in payload:
                self.actor_incidents[identity(payload["actor"])] = incident
        elif schema == "traffic.capture.stored":
            self.counts["captures_stored"] += 1
        elif schema == "traffic.capture.accepted":
            incident = self._traffic_incident(payload)
            task = "traffic:" + incident
            if task not in self.created:
                raise ValueError(
                    "KPI accepted traffic upload has no committed incident report"
                )
            self._completed(task, (at_ns - self.created[task]) / 1e9)
            self.counts["uploads_accepted"] += 1

    def consume(self, raw_record: dict[str, Any]) -> dict[str, Any]:
        record = expand_record(raw_record)
        for item in record.get("items", []):
            if "proposal" not in item:
                continue
            op = decode_record(item["proposal"])
            if isinstance(op, FactWrite):
                self._fact(op)
            elif isinstance(op, RetractFact):
                key = (op.key[0].id, op.key[0].generation, op.key[1])
                self.energy.pop(key, None)
                self.positions.pop(key, None)
            elif isinstance(op, AssertEdge) and "edge" in item:
                edge = decode_record(item["edge"])
                if edge.relation_id == "traffic.capture-incident":
                    self.capture_incidents[
                        f"{edge.source.id}@{edge.source.generation}"
                    ] = f"{edge.target.id}@{edge.target.generation}"
            elif isinstance(op, Emit) and op.kind == "event" and "message" in item:
                message = decode_record(item["message"])
                if message.id in self.messages:
                    continue
                self.messages.add(message.id)
                payload = thaw(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("KPI event payload must be a mapping")
                self._event(message.schema_id, payload, message.at.ns)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        values: dict[str, Any] = dict(self.counts)
        values["energy_used_kj"] = (
            self.energy_used / 1000 if self.energy_observed else None
        )
        values["distance_flown_km"] = (
            self.distance / 1000 if self.position_observed else None
        )
        values["mean_task_latency_s"] = (
            sum(self.latencies) / len(self.latencies) if self.latencies else None
        )
        if self.latencies:
            ordered = sorted(self.latencies)
            index = (len(ordered) - 1) * 0.95
            lo, hi = math.floor(index), math.ceil(index)
            values["p95_task_latency_s"] = ordered[lo] + (ordered[hi] - ordered[lo]) * (
                index - lo
            )
        else:
            values["p95_task_latency_s"] = None
        return {
            name: {"value": values[name], "unit": unit, "label": label}
            for name, (unit, label) in METRICS.items()
        }


def projection_context(path: Path, cursor: int) -> KPIProjector:
    projector = KPIProjector.from_directory(path)
    storage = RunStorage(path)
    start = 1
    while start < cursor:
        records = storage.records(start, min(1024, cursor - start))
        if not records:
            break
        for record in records:
            projector.consume(record)
        start = records[-1]["index"] + 1
    return projector


_LOCK = threading.RLock()
_CACHE: OrderedDict[
    Path, tuple[KPIProjector, list[dict[str, Any]], int, tuple[int, int]]
] = OrderedDict()


def project_run(path: Path, *, include_series: bool = True) -> dict[str, Any]:
    path = path.resolve()
    with _LOCK:
        info = (path / "runtime.registry.json").stat()
        identity_key = (info.st_ino, info.st_mtime_ns)
        storage = RunStorage(path)
        cached = _CACHE.get(path)
        series: list[dict[str, Any]]
        if (
            cached is None
            or cached[3] != identity_key
            or (storage.entries and cached[2] > storage.entries[-1]["index"] + 1)
        ):
            projector, series, cursor = KPIProjector.from_directory(path), [], 1
        else:
            projector, series, cursor, _ = cached
        while True:
            records = storage.records(cursor, 1024)
            if not records:
                break
            for record in records:
                kpis = projector.consume(record)
                at = decode_record(record["instant"])
                series.append(
                    {"commitIndex": record["index"], "at_ns": str(at.ns), "kpis": kpis}
                )
            cursor = records[-1]["index"] + 1
        _CACHE[path] = (projector, series, cursor, identity_key)
        _CACHE.move_to_end(path)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)
        return copy.deepcopy(
            {
                "contract": "aeroagentsim.kpi/v1",
                "final": projector.snapshot(),
                "series": series if include_series else [],
            }
        )
