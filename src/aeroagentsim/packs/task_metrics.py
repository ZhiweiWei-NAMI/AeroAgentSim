"""Legacy pooled task KPIs shared by lowalt and traffic evidence adapters.

One reducer owns these IDs so mixed-domain runs pool samples, never average
per-plugin means or silently overwrite another provider's task metrics.
"""

from __future__ import annotations

import math
from typing import Any

from aeroagentsim.metrics_api import MetricPlugin
from aeroagentsim.runs import RecordedMessage

from .lowalt.metrics import consume_task as lowalt_task
from .metric_helpers import EMPTY_COUNT, LegacyReducer, LegacyState, descriptor
from .traffic_accident.metrics import consume_task as traffic_task


class TaskState(LegacyState):
    def __init__(self) -> None:
        self.counts = {name: 0 for name in ("tasks_created", "tasks_assigned", "tasks_completed", "tasks_failed")}
        self.created: dict[str, int] = {}
        self.assigned: set[str] = set()
        self.completed: set[str] = set()
        self.failed: set[str] = set()
        self.latencies: list[float] = []
        self.medical_tasks: set[str] = set()
        self.medical_latencies: list[float] = []
        self.capture_incidents: dict[str, str] = {}
        self.actor_incidents: dict[str, str] = {}

    def created_task(self, task: str, at_ns: int) -> None:
        if task not in self.created:
            self.created[task] = at_ns
            self.counts["tasks_created"] += 1

    def completed_task(self, task: str, latency: float) -> None:
        if latency < 0:
            raise ValueError("KPI task latency must be nonnegative")
        if task not in self.completed:
            self.completed.add(task)
            self.counts["tasks_completed"] += 1
            self.latencies.append(latency)
            if task in self.medical_tasks:
                self.medical_latencies.append(latency)


class TaskReducer(LegacyReducer):
    version = "2"
    state_type = TaskState
    input_schemas = ("lowalt.task.", "traffic.", "aeroagentsim.metrics.edge/v1")
    descriptors = tuple(
        descriptor(name, "count", label, zh, "Count distinct namespaced tasks with the corresponding committed lifecycle evidence.", missing=EMPTY_COUNT)
        for name, label, zh in (
            ("tasks_created", "Tasks created", "已创建任务"),
            ("tasks_assigned", "Tasks assigned", "已分配任务"),
            ("tasks_completed", "Tasks completed", "已完成任务"),
            ("tasks_failed", "Tasks failed", "失败任务"),
        )
    ) + (
        descriptor("completion_rate", "ratio", "Completed tasks / created tasks", "任务完成率", "Distinct completed tasks / distinct created tasks, pooled across lowalt and traffic.", "higher"),
        descriptor("mean_task_latency_s", "s", "Mean task latency", "任务平均耗时", "Mean of first-completion latency samples, pooled across lowalt and traffic.", "lower"),
        descriptor("p95_task_latency_s", "s", "95th percentile task latency", "任务耗时第95百分位", "Linear interpolation at (n-1)*0.95 over pooled first-completion latencies.", "lower"),
        descriptor("medical_mean_task_latency_s", "s", "Mean medical task latency / 医疗任务平均耗时", "医疗任务平均耗时", "Mean latency of completions preceded by medical-kind creation.", "lower"),
        descriptor("medical_p95_task_latency_s", "s", "95th percentile medical task latency / 医疗任务耗时第95百分位", "医疗任务耗时第95百分位", "Linear interpolation at (n-1)*0.95 over medical first-completion latencies.", "lower"),
    )

    def consume(self, state: TaskState, message: RecordedMessage) -> None:
        # Keep main's emitted-message population plus host-only relation evidence.
        if message.kind != "evidence" and message.source_kind != "partition":
            return
        lowalt_task(state, message)
        traffic_task(state, message)

    def snapshot(self, state: TaskState) -> dict[str, Any]:
        values: dict[str, Any] = dict(state.counts)
        values["completion_rate"] = state.counts["tasks_completed"] / state.counts["tasks_created"] if state.counts["tasks_created"] else None
        for prefix, latencies in (("", state.latencies), ("medical_", state.medical_latencies)):
            values[prefix + "mean_task_latency_s"] = sum(latencies) / len(latencies) if latencies else None
            if latencies:
                ordered = sorted(latencies)
                index = (len(ordered) - 1) * 0.95
                lo, hi = math.floor(index), math.ceil(index)
                values[prefix + "p95_task_latency_s"] = ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)
            else:
                values[prefix + "p95_task_latency_s"] = None
        return values


def plugin() -> MetricPlugin:
    return MetricPlugin("tasks", (TaskReducer(),), api_version=1)
