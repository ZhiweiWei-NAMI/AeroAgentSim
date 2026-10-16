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


METRICS = {
    "tasks_created": ("count", "Tasks created"),
    "tasks_assigned": ("count", "Tasks assigned"),
    "tasks_completed": ("count", "Tasks completed"),
    "tasks_failed": ("count", "Tasks failed"),
    "completion_rate": ("ratio", "Completed tasks / created tasks"),
    "mean_task_latency_s": ("s", "Mean task latency"),
    "p95_task_latency_s": ("s", "95th percentile task latency"),
    "medical_mean_task_latency_s": ("s", "Mean medical task latency / 医疗任务平均耗时"),
    "medical_p95_task_latency_s": (
        "s",
        "95th percentile medical task latency / 医疗任务耗时第95百分位",
    ),
    "on_time_rate": (
        "ratio",
        "On-time rate over deliveries with a committed deadline",
    ),
    "energy_used_kj": ("kJ", "Energy used"),
    "distance_flown_km": ("km", "Distance flown"),
    "spraying_distance_m": ("m", "Completed spraying segment distance / 已完成喷洒航段距离"),
    "battery_swaps": ("count", "Battery swaps completed / 已完成换电"),
    "inspection_observations": ("count", "Inspection observations / 巡检观测次数"),
    "patrol_visits": ("count", "Patrol visits / 巡逻到访次数"),
    "separation_conflicts": ("count", "Separation conflicts"),
    "geofence_violations": ("count", "Geofence violations"),
    "weather_holds": ("count", "Weather holds"),
    "accident_reported": ("count", "Accidents reported"),
    "award_committed": ("count", "Awards committed"),
    "captures_stored": ("count", "Captures stored"),
    "uploads_accepted": ("count", "Uploads accepted"),
    "incidents_occurred": ("count", "Cooperation incidents occurred / 协作事件已发生"),
    "incidents_noticed": ("count", "Cooperation incidents noticed / 协作事件已发现"),
    "incidents_photographed": ("count", "Cooperation incidents photographed / 协作事件已拍摄"),
    "incident_response_s": ("s", "Mean cooperation incident response / 平均协作事件响应时间"),
    "handoffs_completed": ("count", "Handoffs completed / 已完成交接"),
}


RESCUE_METRICS = {
    "rescue_success": ("ratio", "Rescue success / 救援成功"),
    "rescue_time_to_confirmation_s": ("s", "Time to report received / 报告送达耗时"),
    "rescue_time_to_delivery_s": ("s", "Time to delivery completed / 投送完成耗时"),
    "rescue_plans": ("count", "Rescue plans / 救援规划次数"),
    "rescue_bids": ("count", "Rescue bids / 救援提议次数"),
    "rescue_candidates": ("count", "Proposed role candidates / 提议角色候选总数"),
    "rescue_radio_packets": ("count", "Radio packets sent / 无线发包数"),
    "rescue_radio_delivered": ("count", "Radio packets delivered / 无线送达包数"),
    "rescue_radio_bytes": ("B", "Radio bytes sent / 无线发送字节"),
    "rescue_bus_messages": ("count", "Rescue bus messages / 救援总线消息数"),
    "rescue_bus_bytes": ("B", "Rescue bus UTF-8 bytes / 救援总线 UTF-8 字节"),
    "rescue_model_calls": ("count", "Rescue model calls / 救援模型调用数"),
}
METRICS.update(RESCUE_METRICS)


def rescue_kpis(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """CLI projection of the same accumulator used by the live KPI index."""
    projector = KPIProjector(MemoryRegistry((), ()))
    for message in messages:
        projector._rescue_event(message["schemaId"], message["payload"], int(message["at"]["ns"]))
    return projector.rescue_values


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
            name: 0 for name, (unit, _) in METRICS.items() if unit == "count" and name not in RESCUE_METRICS
        }
        self.rescue_notices: dict[str, int] = {}
        self.rescue_seen = False
        self.rescue_values = {name: (0 if unit in {"count", "B"} else None)
                              for name, (unit, _) in RESCUE_METRICS.items()}
        self.rescue_packets: set[str] = set()
        self.rescue_delivered_packets: set[str] = set()
        self.rescue_outcomes: set[str] = set()
        self.created: dict[str, int] = {}
        self.assigned: set[str] = set()
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.latencies: list[float] = []
        self.medical_tasks: set[str] = set()
        self.medical_latencies: list[float] = []
        self.observed_counts: set[str] = set()
        self.spraying_distance: float | None = None
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
            "rescue_seen": self.rescue_seen,
            "rescue_notices": dict(self.rescue_notices),
            "rescue_values": dict(self.rescue_values),
            "rescue_packets": sorted(self.rescue_packets),
            "rescue_delivered_packets": sorted(self.rescue_delivered_packets),
            "rescue_outcomes": sorted(self.rescue_outcomes),
            "counts": dict(self.counts),
            "created": dict(self.created),
            "assigned": sorted(self.assigned),
            "completed": sorted(self.completed),
            "failed": sorted(self.failed),
            "latencies": list(self.latencies),
            "medical_tasks": sorted(self.medical_tasks),
            "medical_latencies": list(self.medical_latencies),
            "observed_counts": sorted(self.observed_counts),
            "spraying_distance": self.spraying_distance,
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
        if "rescue_values" in cursor:
            self.rescue_seen = cursor["rescue_seen"]
            self.rescue_notices = dict(cursor["rescue_notices"])
            self.rescue_values = dict(cursor["rescue_values"])
            self.rescue_packets = set(cursor["rescue_packets"])
            self.rescue_delivered_packets = set(cursor["rescue_delivered_packets"])
            self.rescue_outcomes = set(cursor["rescue_outcomes"])
        # The feed signature rebuilds older cursors from the committed journal.
        cooperation_counts = {"incidents_occurred", "incidents_noticed", "incidents_photographed", "handoffs_completed"}
        self.counts = {name: int(cursor["counts"][name]) for name in self.counts if name not in cooperation_counts}
        # Older checkpoints predate cooperation and have no such events.
        for name in cooperation_counts:
            self.counts[name] = int(cursor["counts"][name]) if "coop_occurrences" in cursor else 0
        self.created = {str(k): int(v) for k, v in cursor["created"].items()}
        self.assigned = set(cursor["assigned"])
        self.completed = set(cursor["completed"])
        self.failed = set(cursor["failed"])
        self.latencies = [float(v) for v in cursor["latencies"]]
        self.medical_tasks = set(cursor["medical_tasks"])
        self.medical_latencies = [float(v) for v in cursor["medical_latencies"]]
        self.observed_counts = set(cursor["observed_counts"])
        self.spraying_distance = (
            number(cursor["spraying_distance"], "spraying_distance_m")
            if cursor["spraying_distance"] is not None
            else None
        )
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
            if task in self.medical_tasks:
                self.medical_latencies.append(latency)

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

    def _rescue_event(self, schema: str, payload: dict[str, Any], at_ns: int) -> None:
        values = self.rescue_values
        if schema.startswith("rescue."):
            self.rescue_seen = True
            encoded = payload["data_json"]
            data = json.loads(encoded)
            values["rescue_bus_messages"] += 1
            values["rescue_bus_bytes"] += len(encoded.encode("utf-8"))
            if schema == "rescue.bid":
                values["rescue_bids"] += 1
                values["rescue_candidates"] += len(data["roles"])
            elif schema == "rescue.event":
                if data["kind"] == "noticed":
                    self.rescue_notices[data["event"]] = at_ns
                elif data["kind"] in {"message_delivered", "delivery_completed"}:
                    metric = ("rescue_time_to_delivery_s" if data["kind"] == "delivery_completed" else
                              "rescue_time_to_confirmation_s" if data["message_kind"] == "report" else None)
                    if metric is not None:
                        values[metric] = (at_ns - self.rescue_notices[data["event"]]) / 1e9
                elif data["kind"] == "plan_computed":
                    values["rescue_plans"] += 1
                elif data["kind"] == "outcome" and data["event"] not in self.rescue_outcomes:
                    self.rescue_outcomes.add(data["event"])
                    if type(data["success"]) is not bool:
                        raise ValueError("rescue outcome success must be boolean")
                    values["rescue_success"] = int(data["success"])
                    for field in ("time_to_confirmation_s", "time_to_delivery_s"):
                        value = data[field]
                        values["rescue_" + field] = None if value is None else number(value, "rescue_" + field)
        elif schema == "adapters.ns3.send" and "/rescue/" in payload["packet_id"]:
            self.rescue_seen = True
            if payload["packet_id"] not in self.rescue_packets:
                self.rescue_packets.add(payload["packet_id"])
                values["rescue_radio_packets"] += 1
                values["rescue_radio_bytes"] += payload["size"]
        elif schema == "adapters.ns3.delivery" and "/rescue/" in payload["packet_id"]:
            self.rescue_seen = True
            if payload["packet_id"] not in self.rescue_delivered_packets:
                self.rescue_delivered_packets.add(payload["packet_id"])
                values["rescue_radio_delivered"] += 1
        elif schema == "aas.langgraph.record" and payload["decision_id"].startswith("rescue_") and payload["phase"] == "model_call":
            self.rescue_seen = True
            values["rescue_model_calls"] += 1

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
            task = "lowalt:" + identity(payload["task"])
            self._created(task, at_ns)
            kind = payload["kind"]
            if kind == "medical":
                self.medical_tasks.add(task)
            elif kind in {"inspection", "survey"}:
                self.observed_counts.add("inspection_observations")
            elif kind == "patrol":
                self.observed_counts.add("patrol_visits")
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
        elif schema == "lowalt.energy.swap_started":
            self.observed_counts.add("battery_swaps")
        elif schema in {
            "lowalt.energy.swap_finished",
            "lowalt.inspection.observed",
            "lowalt.patrol.visited",
        }:
            name = {
                "lowalt.energy.swap_finished": "battery_swaps",
                "lowalt.inspection.observed": "inspection_observations",
                "lowalt.patrol.visited": "patrol_visits",
            }[schema]
            self.observed_counts.add(name)
            self.counts[name] += 1
        elif schema == "lowalt.spraying.applied":
            distance = number(payload["distance_m"], "spraying_distance_m")
            if distance < 0:
                raise ValueError("KPI spraying distance must be nonnegative")
            self.spraying_distance = (
                distance
                if self.spraying_distance is None
                else self.spraying_distance + distance
            )
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
            elif isinstance(op, Emit) and "message" in item:
                message = decode_record(item["message"])
                if message.id in self.messages:
                    continue
                self.messages.add(message.id)
                payload = thaw(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("KPI event payload must be a mapping")
                self._rescue_event(message.schema_id, payload, message.at.ns)
                if op.kind == "event":
                    self._event(message.schema_id, payload, message.at.ns)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        values: dict[str, Any] = dict(self.counts)
        values.update({name: value if self.rescue_seen else None for name, value in self.rescue_values.items()})
        for name in ("battery_swaps", "inspection_observations", "patrol_visits"):
            if name not in self.observed_counts:
                values[name] = None
        values["spraying_distance_m"] = self.spraying_distance
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
        for prefix, latencies in (
            ("", self.latencies), ("medical_", self.medical_latencies)
        ):
            values[prefix + "mean_task_latency_s"] = (
                sum(latencies) / len(latencies) if latencies else None
            )
            if latencies:
                ordered = sorted(latencies)
                index = (len(ordered) - 1) * 0.95
                lo, hi = math.floor(index), math.ceil(index)
                values[prefix + "p95_task_latency_s"] = (
                    ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)
                )
            else:
                values[prefix + "p95_task_latency_s"] = None
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


def project_run(path: Path, *, include_series: bool = True) -> dict[str, Any]:
    """Read committed KPIs through the same persistent index used by the feed."""
    from .feed_index import kpi_result

    return kpi_result(path, include_series=include_series)
