"""Observed-work counters, preserving the unmeasured/observed-zero distinction."""

from __future__ import annotations

from typing import Any

from aeroagentsim.metrics_api import MetricPlugin
from aeroagentsim.runs import RecordedMessage

from .metric_helpers import LegacyReducer, LegacyState, descriptor


class InspectionState(LegacyState):
    def __init__(self) -> None:
        self.counts = {name: 0 for name in ("battery_swaps", "inspection_observations", "patrol_visits")}
        self.observed_counts: set[str] = set()


class InspectionReducer(LegacyReducer):
    version = "2"
    state_type = InspectionState
    input_schemas = ("lowalt.task.created", "lowalt.energy.swap_", "lowalt.inspection.observed", "lowalt.patrol.visited")
    descriptors = tuple(
        descriptor(name, "count", label, zh, aggregation, missing=missing)
        for name, label, zh, aggregation, missing in (
            ("battery_swaps", "Battery swaps completed / 已完成换电", "已完成换电", "Count swap_finished events.", "None until a swap starts or finishes; a start establishes observed zero."),
            ("inspection_observations", "Inspection observations / 巡检观测次数", "巡检观测次数", "Count observed events, including revisits.", "None until an inspection/survey task is created or an observation is committed."),
            ("patrol_visits", "Patrol visits / 巡逻到访次数", "巡逻到访次数", "Count visited events, including revisits.", "None until a patrol task is created or a visit is committed."),
        )
    )

    def consume(self, state: InspectionState, message: RecordedMessage) -> None:
        # Main's population was partition emissions, not ingress admissions.
        if message.source_kind != "partition" or message.kind != "event":
            return
        schema, payload = message.schema_id, message.payload
        if schema == "lowalt.task.created":
            if payload["kind"] in {"inspection", "survey"}:
                state.observed_counts.add("inspection_observations")
            elif payload["kind"] == "patrol":
                state.observed_counts.add("patrol_visits")
        elif schema == "lowalt.energy.swap_started":
            state.observed_counts.add("battery_swaps")
        elif schema in {"lowalt.energy.swap_finished", "lowalt.inspection.observed", "lowalt.patrol.visited"}:
            name = {
                "lowalt.energy.swap_finished": "battery_swaps",
                "lowalt.inspection.observed": "inspection_observations",
                "lowalt.patrol.visited": "patrol_visits",
            }[schema]
            state.observed_counts.add(name)
            state.counts[name] += 1

    def snapshot(self, state: InspectionState) -> dict[str, Any]:
        return {name: count if name in state.observed_counts else None for name, count in state.counts.items()}


def plugin() -> MetricPlugin:
    return MetricPlugin("inspection", (InspectionReducer(),), api_version=1)
