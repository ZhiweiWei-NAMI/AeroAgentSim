"""Cumulative business KPIs from committed journal events and physical facts."""

from __future__ import annotations

import json
import math
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
    "completion_rate": ("ratio", "Completed tasks / created tasks"),
    "mean_task_latency_s": ("s", "Mean task latency"),
    "p95_task_latency_s": ("s", "95th percentile task latency"),
    "on_time_rate": (
        "ratio",
        "On-time rate over deliveries with a committed deadline",
    ),
    "energy_used_kj": ("kJ", "Energy used"),
    "distance_flown_km": ("km", "Distance flown"),
    "separation_conflicts": ("count", "Separation conflicts"),
    "geofence_violations": ("count", "Geofence violations"),
    "weather_holds": ("count", "Weather holds"),
    "accident_reported": ("count", "Accidents reported"),
    "award_committed": ("count", "Awards committed"),
    "captures_stored": ("count", "Captures stored"),
    "uploads_accepted": ("count", "Uploads accepted"),
    "incidents_occurred": ("count", "Incidents occurred / 已发生事件"),
    "incidents_noticed": ("count", "Incidents noticed / 已发现事件"),
    "incidents_photographed": ("count", "Incidents photographed / 已拍摄事件"),
    "incident_response_s": ("s", "Mean incident response / 平均事件响应时间"),
    "handoffs_completed": ("count", "Handoffs completed / 已完成交接"),
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
        self.coop_occurrences: dict[str, int] = {}
        self.coop_noticed: set[str] = set()
        self.coop_photographed: set[str] = set()
        self.coop_handoffs: set[str] = set()
        self.coop_response_s: list[float] = []
        # On-time measurement uses only deadlines committed in the journal
        # (e.g. logistics transition events carrying deadline_ns). Tasks whose
        # deadline was never committed stay unmeasured instead of being inferred.
        self.deadlines: dict[str, int] = {}
        self.delivered: dict[str, tuple[int, bool | None]] = {}
        self.unmeasured_deliveries: set[str] = set()
        # Classification caches: registry/type answers never change per run, so
        # they are resolved once per (type, field) instead of per fact write.
        self._aircraft_types: dict[str, bool] = {}
        self._position_fields: dict[tuple[str, str], bool] = {}

    @classmethod
    def from_directory(cls, path: Path) -> KPIProjector:
        registry = MemoryRegistry.from_data(
            json.loads((path / "runtime.registry.json").read_text())
        )
        scenario = json.loads((path / "scenario.json").read_text())
        return cls(registry, scenario.get("presentation", []))

    def cursor(self) -> dict[str, Any]:
        """Plain-JSON checkpoint of every counter, baseline and dedup state."""
        return {
            "version": 1,
            "counts": dict(self.counts),
            "created": dict(self.created),
            "assigned": sorted(self.assigned),
            "completed": sorted(self.completed),
            "failed": sorted(self.failed),
            "latencies": list(self.latencies),
            "messages": sorted(self.messages),
            "energy": [
                [entity, generation, field, value]
                for (entity, generation, field), value in self.energy.items()
            ],
            "positions": [
                [entity, generation, field, list(value)]
                for (entity, generation, field), value in self.positions.items()
            ],
            "energy_used": self.energy_used,
            "distance": self.distance,
            "energy_observed": self.energy_observed,
            "position_observed": self.position_observed,
            "capture_incidents": dict(self.capture_incidents),
            "actor_incidents": dict(self.actor_incidents),
            "coop_occurrences": dict(self.coop_occurrences),
            "coop_noticed": sorted(self.coop_noticed),
            "coop_photographed": sorted(self.coop_photographed),
            "coop_handoffs": sorted(self.coop_handoffs),
            "coop_response_s": list(self.coop_response_s),
            "deadlines": dict(self.deadlines),
            "delivered": {
                order: [at_ns, on_time]
                for order, (at_ns, on_time) in self.delivered.items()
            },
            "unmeasured_deliveries": sorted(self.unmeasured_deliveries),
        }

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        """Restore state produced by :meth:`cursor`; unknown versions raise."""
        if not isinstance(cursor, dict) or cursor.get("version") != 1:
            raise ValueError("KPI cursor: unsupported checkpoint version")
        self.counts = {
            name: int(cursor["counts"][name]) for name in self.counts
            if name in cursor["counts"]
        }
        # Older checkpoints predate cooperation and have no such events.
        for name in ("incidents_occurred", "incidents_noticed", "incidents_photographed", "handoffs_completed"):
            if name not in self.counts:
                self.counts[name] = 0
        self.created = {str(k): int(v) for k, v in cursor["created"].items()}
        self.assigned = set(cursor["assigned"])
        self.completed = set(cursor["completed"])
        self.failed = set(cursor["failed"])
        self.latencies = [float(v) for v in cursor["latencies"]]
        self.messages = set(cursor["messages"])
        self.energy = {}
        self.positions = {}
        for entity, generation, field, value in cursor["energy"]:
            self.energy[(entity, int(generation), field)] = float(value)
        for entity, generation, field, value in cursor["positions"]:
            self.positions[(entity, int(generation), field)] = tuple(
                float(v) for v in value
            )
        self.energy_used = float(cursor["energy_used"])
        self.distance = float(cursor["distance"])
        for name in ("energy_observed", "position_observed"):
            if type(cursor[name]) is not bool:
                raise ValueError(f"KPI cursor: {name} must be a boolean")
        self.energy_observed = cursor["energy_observed"]
        self.position_observed = cursor["position_observed"]
        self.capture_incidents = {
            str(k): str(v) for k, v in cursor["capture_incidents"].items()
        }
        self.actor_incidents = {
            str(k): str(v) for k, v in cursor["actor_incidents"].items()
        }
        if "coop_occurrences" in cursor:
            self.coop_occurrences = {str(k): int(v) for k, v in cursor["coop_occurrences"].items()}
            self.coop_noticed = set(cursor["coop_noticed"])
            self.coop_photographed = set(cursor["coop_photographed"])
            self.coop_handoffs = set(cursor["coop_handoffs"])
            self.coop_response_s = [float(v) for v in cursor["coop_response_s"]]
        self.deadlines = {str(k): int(v) for k, v in cursor["deadlines"].items()}
        self.delivered = {
            str(order): (int(at_ns), on_time)
            for order, (at_ns, on_time) in cursor["delivered"].items()
        }
        self.unmeasured_deliveries = set(cursor["unmeasured_deliveries"])

    @classmethod
    def from_cursor(
        cls,
        registry: MemoryRegistry,
        cursor: dict[str, Any],
        presentation: list[dict[str, Any]] | None = None,
    ) -> KPIProjector:
        """Rebuild a projector from a registry plus a :meth:`cursor` snapshot."""
        projector = cls(registry, presentation)
        projector.restore_cursor(cursor)
        return projector

    def _aircraft(self, ref: EntityRef) -> bool:
        # Registry/type answers are run-invariant: classify each type once.
        cached = self._aircraft_types.get(ref.type_id)
        if cached is None:
            names = tuple(
                name
                for name in ("oo:Aircraft", "oo:UAV")
                if any(t.id == name for t in self.registry.types)
            )
            cached = any(self.registry.is_a(ref.type_id, name) for name in names)
            self._aircraft_types[ref.type_id] = cached
        return cached

    def _position_field(self, ref: EntityRef, field: str) -> bool:
        key = (ref.type_id, field)
        cached = self._position_fields.get(key)
        if cached is not None:
            return cached
        matches = [
            value
            for kind, value in self.position_bindings.items()
            if self.registry.is_a(ref.type_id, kind)
        ]
        if matches:
            result = bool(field == matches[0])
        else:
            descriptor = self.fields[field]
            result = (
                descriptor.schema.get("type") == "vector"
                and descriptor.schema.get("length") == 3
                and symbol(descriptor.metadata.get("unit")) == "m"
                and symbol(descriptor.metadata.get("frame")) == "enu"
                and descriptor.metadata.get("role") != "config"
                and "position" in field.lower()
            )
        self._position_fields[key] = result
        return result

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
        if schema == "lowalt.incident.occurred":
            incident = identity(payload["incident"])
            if incident not in self.coop_occurrences:
                self.coop_occurrences[incident] = at_ns
                self.counts["incidents_occurred"] += 1
        elif schema == "lowalt.incident.noticed":
            incident = identity(payload["incident"])
            if incident not in self.coop_noticed:
                self.coop_noticed.add(incident)
                self.counts["incidents_noticed"] += 1
        elif schema == "lowalt.incident.photographed":
            incident = identity(payload["incident"])
            if incident not in self.coop_photographed:
                self.coop_photographed.add(incident)
                self.counts["incidents_photographed"] += 1
                if incident in self.coop_occurrences:
                    response = (at_ns - self.coop_occurrences[incident]) / 1e9
                    if response < 0:
                        raise ValueError("KPI incident response: photograph precedes occurrence")
                    self.coop_response_s.append(response)
        elif schema == "lowalt.handoff.transferred":
            handoff = identity(payload["handoff"])
            if handoff not in self.coop_handoffs:
                self.coop_handoffs.add(handoff)
                self.counts["handoffs_completed"] += 1
        elif schema == "lowalt.task.created":
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
        elif schema == "lowalt.food.delivered":
            order = "lowalt:" + identity(payload["task"])
            deadline = payload["deadline_ns"]
            if type(deadline) is not int or deadline < 0:
                raise ValueError("KPI food deadline: nonnegative committed timestamp required")
            self.deadlines[order] = deadline
            self.delivered.setdefault(order, (at_ns, at_ns <= deadline))
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
        elif schema in ("lowalt.weather.release", "weather.release"):
            pass  # Holds were already counted; a release is not a second metric.
        elif schema == "packs.logistics.transition":
            # Deadline data committed by the logistics engine; never inferred.
            order = payload.get("order")
            state = payload.get("state")
            if not isinstance(order, str) or not order:
                raise ValueError("KPI logistics transition: order id required")
            if isinstance(payload.get("deadline_ns"), int):
                self.deadlines.setdefault(order, payload["deadline_ns"])
            if state == "delivered":
                if order in self.deadlines:
                    if order not in self.delivered:
                        self.delivered[order] = (
                            at_ns,
                            at_ns <= self.deadlines[order],
                        )
                elif order not in self.delivered:
                    # Delivered without a committed deadline: remains unmeasured
                    # rather than inferred or counted late.
                    self.delivered[order] = (at_ns, None)
                    self.unmeasured_deliveries.add(order)
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
        values["incident_response_s"] = sum(self.coop_response_s) / len(self.coop_response_s) if self.coop_response_s else None
        values["completion_rate"] = (
            self.counts["tasks_completed"] / self.counts["tasks_created"]
            if self.counts["tasks_created"]
            else None
        )
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
        # On-time rate counts only observed completions that carried a committed
        # deadline; deliveries without one are excluded from the denominator and
        # reported alongside as unmeasured, not late.
        values["on_time_rate"] = (
            sum(1 for _, on_time in self.delivered.values() if on_time)
            / (len(self.delivered) - len(self.unmeasured_deliveries))
            if len(self.delivered) > len(self.unmeasured_deliveries)
            else None
        )
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


def project_run(path: Path, *, include_series: bool = True) -> dict[str, Any]:
    """Read committed KPIs through the same persistent index used by the feed."""
    from .feed_index import kpi_result

    return kpi_result(path, include_series=include_series)
