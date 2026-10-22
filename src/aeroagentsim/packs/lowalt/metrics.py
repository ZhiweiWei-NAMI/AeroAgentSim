"""Builtin low-altitude, food, physical and rescue metric semantics (v1)."""

from __future__ import annotations

import json
import math
from typing import Any

from aeroagentsim.metrics_api import MetricPlugin
from aeroagentsim.runs import RecordedMessage

from ..metric_helpers import (
    EMPTY_COUNT,
    LegacyReducer,
    LegacyState,
    descriptor,
    identity,
    number,
    symbol,
)


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


class RescueState(LegacyState):
    def __init__(self) -> None:
        self.rescue_notices: dict[str, int] = {}
        self.rescue_seen = False
        self.rescue_values: dict[str, Any] = {name: (0 if unit in {"count", "B"} else None)
                              for name, (unit, _) in RESCUE_METRICS.items()}
        self.rescue_packets: set[str] = set()
        self.rescue_delivered_packets: set[str] = set()
        self.rescue_outcomes: set[str] = set()


_RESCUE_AGGREGATION = {
    "rescue_success": "Last first-per-incident outcome success as 0 or 1; NOT a multi-incident success rate.",
    "rescue_time_to_confirmation_s": "Latest measured report-receipt interval since notice; an outcome replaces it, including with None.",
    "rescue_time_to_delivery_s": "Latest measured delivery interval since notice; an outcome replaces it, including with None.",
    "rescue_plans": "Count plan_computed events.",
    "rescue_bids": "Count bid messages, including empty bids.",
    "rescue_candidates": "Sum role-list lengths in bid messages.",
    "rescue_radio_packets": "Count distinct rescue packet IDs sent.",
    "rescue_radio_delivered": "Count distinct rescue packet IDs delivered.",
    "rescue_radio_bytes": "Sum sizes of distinct rescue packet IDs sent.",
    "rescue_bus_messages": "Count partition-emitted rescue.* messages, including commands.",
    "rescue_bus_bytes": "Sum UTF-8 byte lengths of rescue.* data_json strings.",
    "rescue_model_calls": "Count model_call graph records whose decision_id starts rescue_.",
}


class RescueReducer(LegacyReducer):
    version = "2"
    state_type = RescueState
    input_schemas = ("rescue.", "adapters.ns3.send", "adapters.ns3.delivery", "aas.langgraph.record")
    descriptors = tuple(
        descriptor(
            name, unit, label, label.split(" / ", 1)[1], _RESCUE_AGGREGATION[name],
            "higher" if name == "rescue_success" else "lower" if unit == "s" else None,
            "None before rescue evidence; counters become measured zero once rescue is observed, outcomes/times remain None until measured.",
        ) for name, (unit, label) in RESCUE_METRICS.items()
    )

    def __init__(self, *, configured_report: bool = False) -> None:
        self._configured_report = configured_report

    def consume(self, state: RescueState, message: RecordedMessage) -> None:
        # The legacy service/report population is partition emissions, including
        # commands. Ingress is valid host evidence, but not this builtin's traffic.
        if message.source_kind != "partition":
            return
        schema, payload, at_ns = message.schema_id, message.payload, message.at_ns
        values = state.rescue_values
        if schema is None:
            return
        if schema.startswith("rescue."):
            state.rescue_seen = True
            encoded = payload["data_json"]
            data = json.loads(encoded)
            values["rescue_bus_messages"] += 1
            values["rescue_bus_bytes"] += len(encoded.encode("utf-8"))
            if schema == "rescue.bid":
                values["rescue_bids"] += 1
                values["rescue_candidates"] += len(data["roles"])
            elif schema == "rescue.event":
                if data["kind"] == "noticed":
                    state.rescue_notices[data["event"]] = at_ns
                elif data["kind"] in {"message_delivered", "delivery_completed"}:
                    metric = ("rescue_time_to_delivery_s" if data["kind"] == "delivery_completed" else
                              "rescue_time_to_confirmation_s" if data["message_kind"] == "report" else None)
                    if metric is not None:
                        values[metric] = (at_ns - state.rescue_notices[data["event"]]) / 1e9
                elif data["kind"] == "plan_computed":
                    values["rescue_plans"] += 1
                elif data["kind"] == "outcome" and data["event"] not in state.rescue_outcomes:
                    state.rescue_outcomes.add(data["event"])
                    if type(data["success"]) is not bool:
                        raise ValueError("rescue outcome success must be boolean")
                    values["rescue_success"] = int(data["success"])
                    for field in ("time_to_confirmation_s", "time_to_delivery_s"):
                        value = data[field]
                        values["rescue_" + field] = None if value is None else number(value, "rescue_" + field)
        elif schema == "adapters.ns3.send" and "/rescue/" in payload["packet_id"]:
            state.rescue_seen = True
            if payload["packet_id"] not in state.rescue_packets:
                state.rescue_packets.add(payload["packet_id"])
                values["rescue_radio_packets"] += 1
                values["rescue_radio_bytes"] += payload["size"]
        elif schema == "adapters.ns3.delivery" and "/rescue/" in payload["packet_id"]:
            state.rescue_seen = True
            if payload["packet_id"] not in state.rescue_delivered_packets:
                state.rescue_delivered_packets.add(payload["packet_id"])
                values["rescue_radio_delivered"] += 1
        elif schema == "aas.langgraph.record" and payload["decision_id"].startswith("rescue_") and payload["phase"] == "model_call":
            state.rescue_seen = True
            values["rescue_model_calls"] += 1

    def snapshot(self, state: RescueState) -> dict[str, Any]:
        # Only this shipped reducer owns the historical configured-empty zeros.
        # Installed replacements keep their own missing-data policy unchanged.
        return {
            name: value if state.rescue_seen or self._configured_report else None
            for name, value in state.rescue_values.items()
        }


class LowaltState(LegacyState):
    def __init__(self) -> None:
        self.counts = {name: 0 for name in (
            "separation_conflicts", "geofence_violations", "weather_holds",
            "incidents_occurred", "incidents_noticed", "incidents_photographed", "handoffs_completed",
        )}
        self.spraying_distance: float | None = None
        self.coop_occurrences: dict[str, int] = {}
        self.coop_noticed: set[str] = set()
        self.coop_photographed: set[str] = set()
        self.coop_handoffs: set[str] = set()
        self.coop_response_s: list[float] = []


class LowaltReducer(LegacyReducer):
    version = "2"
    state_type = LowaltState
    input_schemas = ("lowalt.",)
    descriptors = tuple(
        descriptor(name, "count", label, zh, aggregation, missing=EMPTY_COUNT)
        for name, label, zh, aggregation in (
            ("separation_conflicts", "Separation conflicts", "间隔冲突", "Count conflict events."),
            ("geofence_violations", "Geofence violations", "围栏违规", "Count geofence-entered events."),
            ("weather_holds", "Weather holds", "天气等待", "Count hold events, not releases."),
            ("incidents_occurred", "Cooperation incidents occurred / 协作事件已发生", "协作事件已发生", "Count distinct occurred cooperation incidents."),
            ("incidents_noticed", "Cooperation incidents noticed / 协作事件已发现", "协作事件已发现", "Count distinct noticed cooperation incidents."),
            ("incidents_photographed", "Cooperation incidents photographed / 协作事件已拍摄", "协作事件已拍摄", "Count distinct photographed cooperation incidents."),
            ("handoffs_completed", "Handoffs completed / 已完成交接", "已完成交接", "Count distinct transferred handoffs."),
        )
    ) + (
        descriptor("spraying_distance_m", "m", "Completed spraying segment distance / 已完成喷洒航段距离", "已完成喷洒航段距离", "Sum applied segment distances; exclude transit."),
        descriptor("incident_response_s", "s", "Mean cooperation incident response / 平均协作事件响应时间", "平均协作事件响应时间", "Mean first-photograph minus occurrence over incidents with both observations.", "lower"),
    )

    def consume(self, state: LowaltState, message: RecordedMessage) -> None:
        # Main's population was partition emissions, not ingress admissions.
        if message.source_kind != "partition" or message.kind != "event":
            return
        schema, payload, at_ns = message.schema_id, message.payload, message.at_ns
        if schema == "lowalt.incident.occurred":
            incident = identity(payload["incident"])
            if incident not in state.coop_occurrences:
                state.coop_occurrences[incident] = at_ns
                state.counts["incidents_occurred"] += 1
        elif schema == "lowalt.incident.noticed":
            incident = identity(payload["incident"])
            if incident not in state.coop_noticed:
                state.coop_noticed.add(incident)
                state.counts["incidents_noticed"] += 1
        elif schema == "lowalt.incident.photographed":
            incident = identity(payload["incident"])
            if incident not in state.coop_photographed:
                state.coop_photographed.add(incident)
                state.counts["incidents_photographed"] += 1
                if incident in state.coop_occurrences:
                    response = (at_ns - state.coop_occurrences[incident]) / 1e9
                    if response < 0:
                        raise ValueError("KPI incident response: photograph precedes occurrence")
                    state.coop_response_s.append(response)
        elif schema == "lowalt.handoff.transferred":
            handoff = identity(payload["handoff"])
            if handoff not in state.coop_handoffs:
                state.coop_handoffs.add(handoff)
                state.counts["handoffs_completed"] += 1
        elif schema == "lowalt.spraying.applied":
            distance = number(payload["distance_m"], "spraying_distance_m")
            if distance < 0:
                raise ValueError("KPI spraying distance must be nonnegative")
            state.spraying_distance = distance if state.spraying_distance is None else state.spraying_distance + distance
        elif schema in {"lowalt.airspace.conflict", "lowalt.airspace.geofence_entered", "lowalt.weather.hold"}:
            name = {
                "lowalt.airspace.conflict": "separation_conflicts",
                "lowalt.airspace.geofence_entered": "geofence_violations",
                "lowalt.weather.hold": "weather_holds",
            }[schema]
            state.counts[name] += 1

    def snapshot(self, state: LowaltState) -> dict[str, Any]:
        return {
            **state.counts,
            "spraying_distance_m": state.spraying_distance,
            "incident_response_s": sum(state.coop_response_s) / len(state.coop_response_s) if state.coop_response_s else None,
        }


class PhysicalState(LegacyState):
    tuple_keys = ("energy", "positions")
    tuple_values = ("positions",)

    def __init__(self) -> None:
        self.energy: dict[tuple[str, int, str], float] = {}
        self.positions: dict[tuple[str, int, str], tuple[float, ...]] = {}
        self.energy_used = 0.0
        self.distance = 0.0
        self.energy_observed = False
        self.position_observed = False
        self._position_fields: dict[tuple[str, str], bool] = {}


class PhysicalReducer(LegacyReducer):
    state_type = PhysicalState
    input_schemas = ("aeroagentsim.metrics.fact/v1", "aeroagentsim.metrics.retract/v1")
    descriptors = (
        descriptor("energy_used_kj", "kJ", "Energy used", "能耗", "Sum positive decreases in non-config J/kJ numeric facts; charging is not negative use.", "lower"),
        descriptor("distance_flown_km", "km", "Distance flown", "飞行距离", "Sum Euclidean ENU segments for aircraft/UAV generations; retraction resets baselines."),
    )

    def consume(self, state: PhysicalState, message: RecordedMessage) -> None:
        payload = message.payload
        ref, field = payload["entity"], payload["field_id"]
        key = (ref["id"], ref["generation"], field)
        if message.schema_id == "aeroagentsim.metrics.retract/v1":
            state.energy.pop(key, None)
            state.positions.pop(key, None)
            return
        schema, metadata = payload["schema"], payload["metadata"]
        unit = symbol(metadata.get("unit"))
        if unit in {"J", "kJ"} and schema.get("type") in {"number", "integer"} and metadata.get("role") != "config":
            energy = number(payload["value"], field) * (1000 if unit == "kJ" else 1)
            if key in state.energy:
                state.energy_used += max(0.0, state.energy[key] - energy)
            state.energy[key] = energy
            state.energy_observed = True
        if not {"oo:Aircraft", "oo:UAV"}.intersection(payload["types"]):
            return
        cache_key = (ref["type_id"], field)
        if cache_key not in state._position_fields:
            matches = [item["positionField"] for item in payload["presentation"] if item["typeId"] in payload["types"]]
            state._position_fields[cache_key] = bool(field == matches[0]) if matches else (
                schema.get("type") == "vector" and schema.get("length") == 3
                and unit == "m" and symbol(metadata.get("frame")) == "enu"
                and metadata.get("role") != "config" and "position" in field.lower()
            )
        if state._position_fields[cache_key]:
            value = payload["value"]
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"KPI {field}: expected an ENU position triple")
            position = tuple(number(v, field) for v in value)
            if key in state.positions:
                state.distance += math.dist(position, state.positions[key])
            state.positions[key] = position
            state.position_observed = True

    def snapshot(self, state: PhysicalState) -> dict[str, Any]:
        return {
            "energy_used_kj": state.energy_used / 1000 if state.energy_observed else None,
            "distance_flown_km": state.distance / 1000 if state.position_observed else None,
        }


def consume_task(state: Any, message: RecordedMessage) -> None:
    """Lowalt evidence adapter for the shared legacy task population."""
    schema, payload, at_ns = message.schema_id, message.payload, message.at_ns
    if message.kind != "event":
        return
    if schema == "lowalt.task.created":
        task = "lowalt:" + identity(payload["task"])
        state.created_task(task, at_ns)
        if payload["kind"] == "medical":
            state.medical_tasks.add(task)
    elif schema == "lowalt.task.assigned":
        task = "lowalt:" + identity(payload["task"])
        if task not in state.assigned:
            state.assigned.add(task)
            state.counts["tasks_assigned"] += 1
    elif schema == "lowalt.task.completed":
        state.completed_task("lowalt:" + identity(payload["task"]), number(payload["latency_s"], "latency_s"))
    elif schema == "lowalt.task.failed":
        task = "lowalt:" + identity(payload["task"])
        if task not in state.failed:
            state.failed.add(task)
            state.counts["tasks_failed"] += 1


def consume_food(state: Any, message: RecordedMessage) -> None:
    payload, at_ns = message.payload, message.at_ns
    order = "lowalt:" + identity(payload["task"])
    deadline = payload["deadline_ns"]
    if type(deadline) is not int or deadline < 0:
        raise ValueError("KPI food deadline: nonnegative committed timestamp required")
    state.deadlines[order] = deadline
    state.delivered.setdefault(order, (at_ns, at_ns <= deadline))


def plugin() -> MetricPlugin:
    return MetricPlugin("lowalt", (LowaltReducer(), PhysicalReducer(), RescueReducer()), api_version=1)
