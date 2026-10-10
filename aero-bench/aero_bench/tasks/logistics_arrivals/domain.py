"""Canonical catalogue, payload fleet and grants for non-physical arrivals."""

from typing import Literal

from pydantic import Field, model_validator

from aero_bench.config.models import Identifier, StrictModel
from aero_bench.tasks.logistics.contracts import LogisticsSceneBinding
from aero_bench.tasks.logistics.facilities import FacilityCatalogue
from aero_bench.tasks.logistics.fleet import Fleet
from aero_bench.tasks.logistics.orders import OrderActorGrant, OrderRequest


class LogisticsArrivalsDomain(StrictModel):
    schema_version: Literal["aero-bench.logistics-arrivals-domain/v1"]
    task_id: Identifier
    verifier_id: Identifier
    scene: LogisticsSceneBinding
    facilities: FacilityCatalogue
    fleet: Fleet
    actor_grants: tuple[OrderActorGrant, ...] = Field(min_length=1)
    orders: tuple[OrderRequest, ...] = Field(max_length=0)

    @model_validator(mode="after")
    def canonical_domain(self):
        if self.fleet.catalogue != self.facilities or not self.fleet.entries:
            raise ValueError("arrival payload fleet must bind the canonical catalogue")
        actors = [item.actor_id for item in self.actor_grants]
        if len(actors) != len(set(actors)) or any(
            item.role != "business" for item in self.actor_grants
        ):
            raise ValueError("arrival domain permits unique Business grants only")
        if set(actors) & {unit.aircraft_id for unit in self.aircraft_units()}:
            raise ValueError("Business actor cannot impersonate a payload fleet unit")
        return self

    def aircraft_units(self):
        return self.fleet.expand_aircraft_units()
