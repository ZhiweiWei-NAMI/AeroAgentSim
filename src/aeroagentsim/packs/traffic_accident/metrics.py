"""Traffic counters and the legacy report-to-accepted-upload task adapter."""

from __future__ import annotations

from typing import Any

from aeroagentsim.metrics_api import MetricPlugin
from aeroagentsim.runs import RecordedMessage

from ..metric_helpers import (
    EMPTY_COUNT,
    LegacyReducer,
    LegacyState,
    descriptor,
    identity,
)


class TrafficState(LegacyState):
    def __init__(self) -> None:
        self.counts = {name: 0 for name in (
            "accident_reported", "award_committed", "captures_stored", "uploads_accepted",
        )}


class TrafficReducer(LegacyReducer):
    state_type = TrafficState
    input_schemas = ("traffic.",)
    descriptors = tuple(
        descriptor(name, "count", label, zh, "Count committed events after message-ID deduplication.", missing=EMPTY_COUNT)
        for name, label, zh in (
            ("accident_reported", "Accidents reported", "已报告事故"),
            ("award_committed", "Awards committed", "已提交授标"),
            ("captures_stored", "Captures stored", "已存储拍摄"),
            ("uploads_accepted", "Uploads accepted", "已接受上传"),
        )
    )

    def consume(self, state: TrafficState, message: RecordedMessage) -> None:
        if message.kind != "event":
            return
        metric = {
            "traffic.report.sent": "accident_reported",
            "traffic.award.committed": "award_committed",
            "traffic.capture.stored": "captures_stored",
            "traffic.capture.accepted": "uploads_accepted",
        }.get(message.schema_id)
        if metric is not None:
            state.counts[metric] += 1

    def snapshot(self, state: TrafficState) -> dict[str, Any]:
        return dict(state.counts)


def _incident(state: Any, payload: dict[str, Any]) -> str:
    if "incident" in payload:
        return identity(payload["incident"])
    if "capture" in payload and identity(payload["capture"]) in state.capture_incidents:
        return state.capture_incidents[identity(payload["capture"])]
    if "actor" in payload and identity(payload["actor"]) in state.actor_incidents:
        return state.actor_incidents[identity(payload["actor"])]
    active = [task for task in state.created if task.startswith("traffic:") and task not in state.completed]
    if len(active) == 1:
        return active[0].removeprefix("traffic:")
    raise ValueError("KPI traffic event cannot be attributed to a reported incident")


def consume_task(state: Any, message: RecordedMessage) -> None:
    """Adapt traffic evidence into the shared legacy task population, not new IDs."""
    payload, schema, at_ns = message.payload, message.schema_id, message.at_ns
    if schema == "aeroagentsim.metrics.edge/v1":
        if payload["relation_id"] == "traffic.capture-incident":
            source, target = payload["source"], payload["target"]
            state.capture_incidents[f"{source['id']}@{source['generation']}"] = f"{target['id']}@{target['generation']}"
    elif message.kind != "event":
        return
    elif schema == "traffic.report.sent":
        state.created_task("traffic:" + identity(payload["incident"]), at_ns)
    elif schema == "traffic.award.committed":
        # Suspension awards can precede any report and do not create tasks.
        if not any(task.startswith("traffic:") for task in state.created):
            return
        incident = _incident(state, payload)
        task = "traffic:" + incident
        if task not in state.assigned:
            state.assigned.add(task)
            state.counts["tasks_assigned"] += 1
        if "actor" in payload:
            state.actor_incidents[identity(payload["actor"])] = incident
    elif schema == "traffic.capture.accepted":
        task = "traffic:" + _incident(state, payload)
        if task not in state.created:
            raise ValueError("KPI accepted traffic upload has no committed incident report")
        state.completed_task(task, (at_ns - state.created[task]) / 1e9)


def plugin() -> MetricPlugin:
    return MetricPlugin("traffic_accident", (TrafficReducer(),), api_version=1)
