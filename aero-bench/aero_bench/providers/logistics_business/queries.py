from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.runtime.contracts import SimulationTime
from aero_bench.tasks.logistics.orders import (
    LogisticsIdentifier,
    OrderState,
)

#: Gateway-visible query types this checkpoint provider actually implements.
LOGISTICS_QUERY_ORDERS = "logistics.orders"
LOGISTICS_QUERY_ORDER_DETAIL = "logistics.order"
LOGISTICS_QUERY_FACILITIES = "logistics.facilities"
LOGISTICS_QUERY_TYPES: frozenset[str] = frozenset(
    {
        LOGISTICS_QUERY_ORDERS,
        LOGISTICS_QUERY_ORDER_DETAIL,
        LOGISTICS_QUERY_FACILITIES,
    }
)


class LogisticsOrderView(StrictModel):
    """One authoritative order projection returned by the provider workload.

    This is a read-only snapshot of the canonical ``OrderState`` the workload
    owns; it is not delivery evidence and it never claims a physical result.
    """

    order_id: LogisticsIdentifier
    status: str
    version: int
    origin_facility_id: LogisticsIdentifier
    destination_facility_id: LogisticsIdentifier
    hub_handoff_facility_id: LogisticsIdentifier
    cargo_mass_kg: float
    release_time_s: float
    deadline_s: float
    last_time_s: float
    assignee_id: LogisticsIdentifier | None = None
    capacity_kg: float | None = None
    evidence_ref: LogisticsIdentifier | None = None
    handoff_evidence_ref: LogisticsIdentifier | None = None
    hub_handoff_version: int | None = None
    failure_reason: LogisticsIdentifier | None = None


class LogisticsFacilityView(StrictModel):
    """One facility-catalogue projection owned by the provider workload."""

    facility_id: LogisticsIdentifier
    name: str
    kind: str
    placement: str
    building_id: LogisticsIdentifier | None = None
    position_x: float
    position_z: float
    parking_slots: int | None = None
    movements_per_hour: float | None = None
    storage_capacity_kg: float | None = None
    charging_slots: int | None = None
    charging_power_w: float | None = None
    charging_price_amount: float | None = None
    charging_price_currency: str | None = None


class LogisticsBusinessQuery(StrictModel):
    """A read-only business-environment query bound to the provider run."""

    run_id: Sha256
    query_id: Identifier
    kind: Literal["orders", "order", "facilities"]
    issued_at: SimulationTime
    order_id: LogisticsIdentifier | None = None

    @model_validator(mode="after")
    def order_query_binds_one_order(self) -> "LogisticsBusinessQuery":
        if (self.kind == "order") != (self.order_id is not None):
            raise ValueError(
                "logistics order-detail query requires exactly one order_id"
            )
        if self.kind != "order" and self.order_id is not None:
            raise ValueError("logistics summary queries cannot name an order_id")
        return self


class LogisticsBusinessQueryResult(StrictModel):
    """The workload's authoritative answer to one logistics business query."""

    run_id: Sha256
    query_id: Identifier
    observed_at: SimulationTime
    orders: tuple[LogisticsOrderView, ...] = ()
    facilities: tuple[LogisticsFacilityView, ...] = ()

    @model_validator(mode="after")
    def unique_projection_ids(self) -> "LogisticsBusinessQueryResult":
        if len(self.orders) != len({view.order_id for view in self.orders}):
            raise ValueError("query result order ids must be unique")
        if len(self.facilities) != len({view.facility_id for view in self.facilities}):
            raise ValueError("query result facility ids must be unique")
        return self


def order_view(order_state: OrderState) -> LogisticsOrderView:
    """Build the read-only projection from a canonical OrderState."""

    return LogisticsOrderView(
        order_id=order_state.order_id,
        status=order_state.status.value,
        version=order_state.version,
        origin_facility_id=order_state.origin_facility_id,
        destination_facility_id=order_state.destination_facility_id,
        hub_handoff_facility_id=order_state.hub_handoff_facility_id,
        cargo_mass_kg=order_state.cargo_mass_kg,
        release_time_s=order_state.release_time_s,
        deadline_s=order_state.deadline_s,
        last_time_s=order_state.last_time_s,
        assignee_id=order_state.assignee_id,
        capacity_kg=order_state.capacity_kg,
        evidence_ref=order_state.evidence_ref,
        handoff_evidence_ref=order_state.handoff_evidence_ref,
        hub_handoff_version=order_state.hub_handoff_version,
        failure_reason=order_state.failure_reason,
    )


__all__ = [
    "LOGISTICS_QUERY_FACILITIES",
    "LOGISTICS_QUERY_ORDER_DETAIL",
    "LOGISTICS_QUERY_ORDERS",
    "LOGISTICS_QUERY_TYPES",
    "LogisticsBusinessQuery",
    "LogisticsBusinessQueryResult",
    "LogisticsFacilityView",
    "LogisticsOrderView",
    "order_view",
]
