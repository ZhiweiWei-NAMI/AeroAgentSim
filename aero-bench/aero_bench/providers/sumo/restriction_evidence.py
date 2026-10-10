"""Typed native permission and route witnesses for scheduled SUMO restrictions."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator

from aero_bench.config.models import StrictModel
from aero_bench.providers.sumo.config import SumoTrafficRestriction


Text = Annotated[str, Field(min_length=1)]
Index = Annotated[int, Field(ge=0, strict=True)]
NativeRouteIndex = Annotated[int, Field(strict=True)]


class LanePermissionWitness(StrictModel):
    lane_id: Text
    before_disallowed: tuple[Text, ...]
    requested_disallowed: tuple[Text, ...]
    after_disallowed: tuple[Text, ...]
    traci_acknowledged: StrictBool

    @model_validator(mode="after")
    def canonical_permissions(self):
        for values in (self.before_disallowed, self.requested_disallowed, self.after_disallowed):
            if values != tuple(sorted(set(values))):
                raise ValueError("SUMO permission classes must be sorted and unique")
        if self.traci_acknowledged is not True:
            raise ValueError("SUMO lane permission lacks a native TraCI acknowledgment")
        return self


class VehicleRouteWitness(StrictModel):
    vehicle_id: Text
    vehicle_class: Text
    lifecycle: Literal["active", "pending"]
    before_route: tuple[Text, ...] = Field(min_length=1)
    before_route_index: NativeRouteIndex
    before_road_id: str
    after_route: tuple[Text, ...] = Field(min_length=1)
    after_route_index: NativeRouteIndex
    after_road_id: str
    traci_acknowledged: StrictBool

    @model_validator(mode="after")
    def native_route_indices(self):
        if self.lifecycle == "active":
            if not (0 <= self.before_route_index < len(self.before_route)
                    and 0 <= self.after_route_index < len(self.after_route)
                    and self.before_road_id and self.after_road_id):
                raise ValueError("active SUMO route index or road identity is invalid")
        elif (self.before_route_index != -1073741824 or self.after_route_index != -1073741824
                or self.before_road_id or self.after_road_id):
            raise ValueError("pending SUMO route must retain native invalid indices and empty roads")
        if self.traci_acknowledged is not True or self.before_road_id != self.after_road_id:
            raise ValueError("SUMO reroute acknowledgment or stationary barrier identity is invalid")
        return self


class SumoRestrictionApplication(StrictModel):
    schema_version: Literal["sumo.traffic.restricted.v2"]
    request: SumoTrafficRestriction
    native_sim_time_ns: Index
    permissions: tuple[LanePermissionWitness, ...] = Field(min_length=1)
    route_effects: tuple[VehicleRouteWitness, ...]


def validate_restriction_application(
    application: SumoRestrictionApplication,
    *,
    expected: SumoTrafficRestriction,
    step_ns: int,
    require_route_effect: bool,
) -> None:
    if application.request != expected or application.native_sim_time_ns != expected.at_tick * step_ns:
        raise ValueError("SUMO restriction does not bind its scheduled request and native tick")
    lanes = tuple(item.lane_id for item in application.permissions)
    vehicles = tuple(item.vehicle_id for item in application.route_effects)
    if lanes != tuple(sorted(set(lanes))) or vehicles != tuple(sorted(set(vehicles))):
        raise ValueError("SUMO restriction witnesses must be sorted and unique")
    changed = False
    for witness in application.permissions:
        requested = tuple(sorted(set(witness.before_disallowed) | set(expected.disallowed_classes)))
        if witness.requested_disallowed != requested or witness.after_disallowed != requested:
            raise ValueError("SUMO restriction did not preserve and apply native lane permissions")
        changed |= witness.before_disallowed != witness.after_disallowed
    if not changed:
        raise ValueError("SUMO restriction has no observed permission change")
    if require_route_effect and not any(item.lifecycle == "active" for item in application.route_effects):
        raise ValueError("SUMO restriction has no observed affected active vehicle route")
    for witness in application.route_effects:
        before_remaining = witness.before_route[witness.before_route_index + 1:] if witness.lifecycle == "active" else witness.before_route
        after_remaining = witness.after_route[witness.after_route_index + 1:] if witness.lifecycle == "active" else witness.after_route
        if (
            witness.vehicle_class not in expected.disallowed_classes
            or expected.edge_id not in before_remaining
            or expected.edge_id in after_remaining
            or witness.before_route[-1] != witness.after_route[-1]
            or witness.before_route == witness.after_route
            or witness.before_road_id == expected.edge_id
        ):
            raise ValueError("SUMO restriction lacks the specified native vehicle reroute effect")
