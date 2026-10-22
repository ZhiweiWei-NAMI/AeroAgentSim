"""Committed-deadline delivery KPIs; missing deadlines are never inferred."""

from __future__ import annotations

from typing import Any

from aeroagentsim.metrics_api import MetricPlugin
from aeroagentsim.runs import RecordedMessage

from .lowalt.metrics import consume_food
from .metric_helpers import LegacyReducer, LegacyState, descriptor


class DeliveryState(LegacyState):
    tuple_values = ("delivered",)

    def __init__(self) -> None:
        self.deadlines: dict[str, int] = {}
        self.delivered: dict[str, tuple[int, bool | None]] = {}
        self.unmeasured_deliveries: set[str] = set()


class DeliveryReducer(LegacyReducer):
    state_type = DeliveryState
    input_schemas = ("packs.logistics.transition", "lowalt.food.delivered")
    descriptors = (
        descriptor("on_time_rate", "ratio", "On-time rate over deliveries with a committed deadline", "有已提交截止时间的投递准时率", "On-time first deliveries / first deliveries with committed deadlines; exclude unmeasured deliveries.", "higher"),
    )

    def consume(self, state: DeliveryState, message: RecordedMessage) -> None:
        if message.kind != "event":
            return
        payload, at_ns = message.payload, message.at_ns
        if message.schema_id == "lowalt.food.delivered":
            consume_food(state, message)
        elif message.schema_id == "packs.logistics.transition":
            order = payload.get("order")
            if not isinstance(order, str) or not order:
                raise ValueError("KPI logistics transition: order id required")
            if isinstance(payload.get("deadline_ns"), int):
                state.deadlines.setdefault(order, payload["deadline_ns"])
            if payload.get("state") == "delivered":
                if order in state.deadlines:
                    if order not in state.delivered:
                        state.delivered[order] = (at_ns, at_ns <= state.deadlines[order])
                elif order not in state.delivered:
                    state.delivered[order] = (at_ns, None)
                    state.unmeasured_deliveries.add(order)

    def snapshot(self, state: DeliveryState) -> dict[str, Any]:
        return {"on_time_rate": (
            sum(1 for _, on_time in state.delivered.values() if on_time)
            / (len(state.delivered) - len(state.unmeasured_deliveries))
            if len(state.delivered) > len(state.unmeasured_deliveries) else None
        )}


def plugin() -> MetricPlugin:
    return MetricPlugin("logistics", (DeliveryReducer(),), api_version=1)
