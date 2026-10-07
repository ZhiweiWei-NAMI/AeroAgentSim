"""Hub-mediated order resolution against the canonical facility catalogue.

This module resolves the dispatch-visible canonical hub for one order against
the explicit canonical ``FacilityCatalogue`` produced by
:func:`aero_bench.tasks.logistics.facilities.compile_authored_facilities` from
the full current frontend v3 facility records. It deliberately does not define a
second facility shape: the frontend records are validated exactly once by that
lowering and never re-parsed or re-shaped here.

No ID string is trusted and no hub is silently inferred: every order endpoint
and every named handoff must resolve in the supplied canonical catalogue and
satisfy the stated cargo-transfer / hub / storage rules before an order is
lowered into the domain ``OrderRequest``. This module never fabricates
trajectories or provider success.
"""

from __future__ import annotations

from aero_bench.tasks.logistics.facilities import (
    FacilityCapabilities,
    FacilityCatalogue,
)
from aero_bench.tasks.logistics.orders import LogisticsIdentifier


def cargo_transfer_allowed(facility: FacilityCapabilities) -> bool:
    """A vertiport or hub can exchange cargo with an aircraft; a charger cannot."""
    return facility.kind in {"vertiport", "hub"}


def resolve_canonical_hub(
    catalog: FacilityCatalogue,
    *,
    order_id: LogisticsIdentifier,
    origin_facility_id: LogisticsIdentifier,
    destination_facility_id: LogisticsIdentifier,
    hub_handoff_facility_id: LogisticsIdentifier | None,
    cargo_mass_kg: float,
) -> LogisticsIdentifier:
    """Resolve the dispatch-visible canonical hub for one order.

    Rejects unknown endpoints, an endpoint that does not permit cargo transfer
    (a charger cannot exchange goods), a named handoff that is absent or not a
    ``hub`` in the supplied canonical catalogue, a named handoff equal to an
    endpoint, and the Vertiport-to-Vertiport goods bypass (no endpoint is a hub
    and no hub is declared). An endpoint hub is accepted as its own handoff.
    Every resolved hub must have enough storage capacity for the parcel.
    """
    source = catalog.get(origin_facility_id)
    if source is None:
        raise ValueError(
            f"order {order_id} references an unknown source facility: {origin_facility_id}"
        )
    destination = catalog.get(destination_facility_id)
    if destination is None:
        raise ValueError(
            f"order {order_id} references an unknown destination facility: {destination_facility_id}"
        )
    if not cargo_transfer_allowed(source):
        raise ValueError(
            f"order {order_id} source facility {origin_facility_id} does not permit cargo transfer"
        )
    if not cargo_transfer_allowed(destination):
        raise ValueError(
            f"order {order_id} destination facility {destination_facility_id} does not permit cargo transfer"
        )

    if hub_handoff_facility_id is not None:
        if (
            hub_handoff_facility_id == origin_facility_id
            or hub_handoff_facility_id == destination_facility_id
        ):
            raise ValueError(
                f"order {order_id} hub handoff must differ from the origin and destination facilities"
            )
        hub = catalog.get(hub_handoff_facility_id)
        if hub is None:
            raise ValueError(
                f"order {order_id} references an unknown hub facility: {hub_handoff_facility_id}"
            )
        if hub.kind != "hub":
            raise ValueError(
                f"order {order_id} handoff {hub_handoff_facility_id} is not a hub in the supplied catalog"
            )
        canonical = hub_handoff_facility_id
    elif source.kind == "hub":
        canonical = origin_facility_id
    elif destination.kind == "hub":
        canonical = destination_facility_id
    else:
        raise ValueError(
            f"order {order_id} lacks a hub handoff: direct vertiport-to-vertiport goods bypass is forbidden"
        )

    hub = catalog.require(canonical)
    if hub.cargo is None or cargo_mass_kg > hub.cargo.storage_capacity_kg:
        raise ValueError(
            f"order {order_id} cargo {cargo_mass_kg} kg exceeds hub {canonical} storage capacity"
        )
    return canonical


__all__ = [
    "cargo_transfer_allowed",
    "resolve_canonical_hub",
]