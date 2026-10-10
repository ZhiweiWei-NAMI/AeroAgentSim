"""Explicit lowering of current frontend order requests to the logistics domain.

This accepts only the current authoring field names, including the required,
nullable ``hubHandoffFacilityId`` (the frontend ``city-selected-logistics`` v2
contract). Every facility reference is validated against the explicit canonical
facility catalogue produced by ``compile_authored_facilities`` from the full
current frontend facility records: a hub is never trusted from an ID string or
silently inferred. Non-hub endpoints require a declared hub in the catalogue; an
endpoint hub is accepted as its own handoff; and the Vertiport-to-Vertiport
goods bypass is rejected. The lowered ``OrderRequest`` carries the
dispatch-visible canonical hub and the derived immutable itinerary legs. This
module does not release an order, attach a scenario, authorize actors, or
execute a transition; provider materialization still separately binds
facility/fleet identities and the run.
"""

from pydantic import Field

from aero_bench.tasks.logistics.facilities import FacilityCatalogue
from aero_bench.tasks.logistics.facility_catalog import resolve_canonical_hub
from aero_bench.tasks.logistics.orders import LogisticsIdentifier, OrderRequest


class AuthoredOrderRequest(OrderRequest):
    """Frontend field names with the same strict domain validators as OrderRequest.

    ``hubHandoffFacilityId`` is a required, nullable key exactly as the current
    frontend emits it: when an endpoint is a hub the value is ``null`` (that hub
    is its own handoff), and when neither endpoint is a hub the author must
    declare the hub midpoint.
    """

    order_id: LogisticsIdentifier = Field(validation_alias="id")
    origin_facility_id: LogisticsIdentifier = Field(validation_alias="sourceFacilityId")
    destination_facility_id: LogisticsIdentifier = Field(validation_alias="destinationFacilityId")
    hub_handoff_facility_id: LogisticsIdentifier | None = Field(validation_alias="hubHandoffFacilityId")
    cargo_mass_kg: float = Field(validation_alias="cargoKg")
    release_time_s: float = Field(validation_alias="releaseAtS")
    deadline_s: float = Field(validation_alias="deliverByS")


def compile_authored_orders(
    values: object, facility_catalogue: FacilityCatalogue
) -> tuple[OrderRequest, ...]:
    """Validate and lower a JSON order list against the canonical facility catalogue.

    The catalogue is the only valid referent: the explicit canonical
    ``FacilityCatalogue`` produced by ``compile_authored_facilities`` from the
    full current frontend facility records. A raw records array or mapping is
    never re-parsed here. Unknown endpoints, non cargo-transfer endpoints, an
    unknown or non-hub named handoff, and Vertiport-to-Vertiport bypasses are
    rejected before any order is lowered. The returned tuple inherits the
    ``OrderRequest`` strict validators and never mixes field names.
    """
    if not isinstance(facility_catalogue, FacilityCatalogue):
        raise ValueError(
            "compile_authored_orders requires the canonical FacilityCatalogue "
            "produced by compile_authored_facilities"
        )
    if not isinstance(values, list):
        raise ValueError("authoring orders must be a JSON array")
    orders: list[OrderRequest] = []
    for value in values:
        authored = AuthoredOrderRequest.model_validate(value)
        canonical_hub = resolve_canonical_hub(
            facility_catalogue,
            order_id=authored.order_id,
            origin_facility_id=authored.origin_facility_id,
            destination_facility_id=authored.destination_facility_id,
            hub_handoff_facility_id=authored.hub_handoff_facility_id,
            cargo_mass_kg=authored.cargo_mass_kg,
        )
        orders.append(
            OrderRequest(
                order_id=authored.order_id,
                origin_facility_id=authored.origin_facility_id,
                destination_facility_id=authored.destination_facility_id,
                hub_handoff_facility_id=canonical_hub,
                cargo_mass_kg=authored.cargo_mass_kg,
                release_time_s=authored.release_time_s,
                deadline_s=authored.deadline_s,
            )
        )
    if len({order.order_id for order in orders}) != len(orders):
        raise ValueError("authoring order ids must be unique")
    return tuple(orders)
