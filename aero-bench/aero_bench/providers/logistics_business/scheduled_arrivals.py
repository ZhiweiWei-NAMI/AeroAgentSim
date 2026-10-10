"""Pinned, non-physical order creation at Business Provider barriers."""

from typing import Annotated, Literal

from pydantic import Field

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.order_arrivals import OrderArrivalEvent
from aero_bench.tasks.logistics.orders import LogisticsIdentifier, OrderRequest

SCHEDULED_ARRIVAL_CAPABILITY = "logistics.orders.scheduled-arrivals"
SCHEDULED_ARRIVAL_EVENT_SCHEMA = "logistics.business.scheduled-arrival.v1"


class ScheduledOrderCreation(StrictModel):
    event_id: Identifier
    at_tick: Annotated[int, Field(strict=True, ge=1)]
    actor_id: LogisticsIdentifier
    order: OrderRequest


class ScheduledOrderApplication(StrictModel):
    schema_version: Literal["aero-bench.logistics-scheduled-application/v1"]
    requested: ScheduledOrderCreation
    applied_at: SimulationTime
    arrival: OrderArrivalEvent
    arrival_snapshot_digest: Sha256
    motion_barrier_digest: Sha256
    scene_state_digest: Sha256


__all__ = [
    "SCHEDULED_ARRIVAL_CAPABILITY",
    "SCHEDULED_ARRIVAL_EVENT_SCHEMA",
    "ScheduledOrderCreation",
    "ScheduledOrderApplication",
]
