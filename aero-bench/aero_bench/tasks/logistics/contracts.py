"""Strict logistics task package: scene binding and canonical domain assembly.

Phase 4 WP1 defines the immutable package that binds one selected scene
identity/origin/asset provenance, the canonical facility catalogue, the
explicitly declared fleet and performance profiles, hub-mediated orders, no-fly
zones, and declared actor grants into one self-contained strict model. Every
semantically relevant field enters the deterministic ``canonical_digest``, which
the resolver publishes into the ResolvedRun feasibility bounds so the lowered
package identity is part of the resolved run.

The package consumes the canonical logistics domain models directly and their
frontend lowerers (``compile_authored_facilities``,
``compile_authored_fleet``, ``compile_authored_performance_profiles``,
``compile_authored_orders``, ``compile_no_fly_zones``); it never defines a
second facility/fleet/order shape with drifting aliases. Reference validation is
performed here: unknown facility/fleet references, aircraft-actor identity,
performance-profile membership, hub-mediated routing (including rejection of the
Vertiport-to-Vertiport goods bypass), no-fly zone identity/frame consistency,
and payload coverage by a declared aircraft are all rejected at package
lowering.

Spatial proof is deliberately out of scope: the package carries the declared
facility dimensions/positions and the scene origin, but landing-pad and
collision feasibility are a separate geometry-module concern
(``facility_landing_pads(FacilityCapabilities)`` is implemented elsewhere and
must never be imported or invented here).
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import field_validator, model_validator

from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.airspace_events import (
    NO_FLY_FRAME,
    NoFlyFrame,
    NoFlyZone,
    compile_no_fly_zones,
)
from aero_bench.tasks.logistics.authoring import compile_authored_orders
from aero_bench.tasks.logistics.facilities import (
    FacilityCatalogue,
    compile_authored_facilities,
)
from aero_bench.tasks.logistics.facility_catalog import resolve_canonical_hub
from aero_bench.tasks.logistics.fleet import (
    Fleet,
    FleetPerformanceProfile,
    compile_authored_fleet,
    compile_authored_performance_profiles,
)
from aero_bench.tasks.logistics.orders import (
    OrderActorGrant,
    OrderRequest,
)

LOGISTICS_PACKAGE_ID = "logistics.task.v1"
LOGISTICS_SCHEMA_VERSION: Literal["aero-bench.logistics-task/v1"] = (
    "aero-bench.logistics-task/v1"
)

#: Flight/observation capabilities reused verbatim from the existing PX4 Gazebo
#: backend. ``releases/inspection-v1`` already declares ``flight.command`` and
#: ``observation.capture`` against the real ``px4.gazebo`` Provider, so a
#: logistics execution must name exactly those capability ids — never an
#: invented parallel ``logistics.flight.*`` name that would contradict the
#: shared flight backend.
LOGISTICS_FLIGHT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "flight.command",
        "observation.capture",
    }
)

#: New logistics business capabilities the benchmark may claim only once a real
#: logistics business Provider adapter and execution/verifier runtime land. WP1
#: implements neither, so these stay pending today.
LOGISTICS_BUSINESS_CAPABILITIES: frozenset[str] = frozenset(
    {
        "logistics.facilities.state",
        "logistics.orders.authority",
        "logistics.airspace.events",
        "logistics.delivery.observation",
    }
)

#: Capabilities a real logistics Provider must physically supply before the
#: benchmark may claim a runnable logistics execution: the existing PX4 Gazebo
#: flight command/observation capabilities plus the new logistics business
#: capabilities. No real logistics runtime is implemented in WP1, so the
#: resolver always reports the pending state today instead of fabricating a
#: fallback.
LOGISTICS_REQUIRED_CAPABILITIES: frozenset[str] = frozenset(
    LOGISTICS_FLIGHT_CAPABILITIES | LOGISTICS_BUSINESS_CAPABILITIES
)

#: Exact top-level authoring keys the strict package lowerer accepts. The
#: lowerer is the real entrypoint: it must reject unknown or missing keys
#: itself rather than rely on a caller-supplied JSON Schema that may be looser
#: than the package contract.
_LOGISTICS_TASK_PACKAGE_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "package_id",
        "task_id",
        "verifier_id",
        "scene",
        "facilities",
        "fleet",
        "performanceProfiles",
        "orders",
        "noFlyZones",
        "actors",
    }
)


class LogisticsSceneBinding(StrictModel):
    """Selected-scene identity, WGS84 origin, and asset provenance binding.

    ``scene_source_sha256`` is the provenance digest of the scene source the
    package is authored against; the resolver binds it to the ResolvedScenario
    world asset digest. ``coordinate_frame`` is the closed literal shared with
    the no-fly domain (``scene_east_south_m``: X east, Z south, Y up; seconds).
    """

    scene_id: Identifier
    coordinate_frame: NoFlyFrame = NO_FLY_FRAME
    origin_latitude_deg: float
    origin_longitude_deg: float
    origin_altitude_m: float
    scene_source_sha256: Sha256

    @field_validator(
        "origin_latitude_deg",
        "origin_longitude_deg",
        "origin_altitude_m",
    )
    @classmethod
    def finite_origin(cls, value: object, info) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{info.field_name} must be a finite number")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError(f"{info.field_name} must be finite")
        return numeric

    @model_validator(mode="after")
    def wgs84_origin_is_in_range(self) -> "LogisticsSceneBinding":
        if not -90.0 < self.origin_latitude_deg < 90.0:
            raise ValueError("scene origin latitude must be in (-90, 90)")
        if not -180.0 <= self.origin_longitude_deg < 180.0:
            raise ValueError("scene origin longitude must be in [-180, 180)")
        return self


class LogisticsTaskPackage(StrictModel):
    """Immutable strict logistics task package.

    All sections are canonical logistics domain models lowered from the current
    frontend authoring shape. Model validation re-checks every cross-reference
    against the canonical models (never an ID string) so the package cannot
    drift from the domain contracts it binds.
    """

    schema_version: Literal["aero-bench.logistics-task/v1"]
    package_id: Literal["logistics.task.v1"]
    task_id: Identifier
    verifier_id: Identifier
    scene: LogisticsSceneBinding
    facilities: FacilityCatalogue
    fleet: Fleet
    performance_profiles: tuple[FleetPerformanceProfile, ...]
    orders: tuple[OrderRequest, ...]
    no_fly_zones: tuple[NoFlyZone, ...]
    actor_grants: tuple[OrderActorGrant, ...]

    @model_validator(mode="after")
    def package_references_are_consistent(self) -> "LogisticsTaskPackage":
        if self.fleet.catalogue != self.facilities:
            raise ValueError(
                "logistics fleet must be bound to the package's canonical "
                "facility catalogue"
            )
        if not self.fleet.entries:
            raise ValueError("logistics package must declare at least one fleet entry")
        if not self.performance_profiles:
            raise ValueError(
                "logistics package must declare at least one performance profile"
            )
        for profile in self.performance_profiles:
            self.fleet.require(profile.fleet_entry_id)
        if not self.orders:
            raise ValueError("logistics package must declare at least one order")

        aircraft_units = self.fleet.expand_aircraft_units()
        aircraft_ids = {unit.aircraft_id for unit in aircraft_units}
        seen_actors: set[str] = set()
        for grant in self.actor_grants:
            if grant.actor_id in seen_actors:
                raise ValueError(
                    f"duplicate logistics actor grant: {grant.actor_id}"
                )
            seen_actors.add(grant.actor_id)
            if grant.role == "aircraft_agent":
                if grant.actor_id not in aircraft_ids:
                    raise ValueError(
                        "aircraft_agent grant must name an expanded aircraft "
                        f"identity; {grant.actor_id} is not a declared aircraft"
                    )
            elif grant.actor_id in aircraft_ids:
                raise ValueError(
                    "non-aircraft actor identity collides with a declared "
                    f"aircraft identity: {grant.actor_id}"
                )

        order_ids = [order.order_id for order in self.orders]
        if len(order_ids) != len(set(order_ids)):
            raise ValueError("logistics order ids must be unique")
        for order in self.orders:
            declared_hub = order.hub_handoff_facility_id
            input_hub = (
                None
                if declared_hub
                in {order.origin_facility_id, order.destination_facility_id}
                else declared_hub
            )
            canonical_hub = resolve_canonical_hub(
                self.facilities,
                order_id=order.order_id,
                origin_facility_id=order.origin_facility_id,
                destination_facility_id=order.destination_facility_id,
                hub_handoff_facility_id=input_hub,
                cargo_mass_kg=order.cargo_mass_kg,
            )
            if canonical_hub != declared_hub:
                raise ValueError(
                    f"order {order.order_id} canonical hub {canonical_hub} does "
                    f"not match the declared hub {declared_hub}"
                )
            if not any(
                unit.max_payload_kg >= order.cargo_mass_kg
                for unit in aircraft_units
            ):
                raise ValueError(
                    f"order {order.order_id} cargo {order.cargo_mass_kg} kg has "
                    "no declared aircraft with sufficient payload"
                )

        zone_ids = [zone.zone_id for zone in self.no_fly_zones]
        if len(zone_ids) != len(set(zone_ids)):
            raise ValueError("no-fly zone ids must be unique")
        for zone in self.no_fly_zones:
            if zone.frame != self.scene.coordinate_frame:
                raise ValueError(
                    f"no-fly zone {zone.zone_id} frame does not match the "
                    "logistics package scene frame"
                )
        return self

    def canonical_digest(self) -> str:
        """Deterministic SHA-256 over every canonical package field."""

        return hashlib.sha256(
            canonical_json_bytes(self.model_dump(mode="json"))
        ).hexdigest()

    def aircraft_units(self):
        """Expanded aircraft identities matching the frontend planner ids."""

        return self.fleet.expand_aircraft_units()


def lower_logistics_task_package(values: object) -> LogisticsTaskPackage:
    """Lower the current frontend authoring document into the strict package.

    The document is the full current authoring shape: ``tasks`` sections with
    camelCase capability fields, ``fleet``/``performanceProfiles``,
    ``orders`` (``hubHandoffFacilityId`` nullable), ``noFlyZones``, ``actors``,
    and the explicit ``scene`` binding. Every section is lowered by the shared
    canonical lowerers and then collectively re-validated by the package model.
    """
    if not isinstance(values, dict):
        raise ValueError("logistics task package document must be a JSON object")
    unknown = set(values) - _LOGISTICS_TASK_PACKAGE_KEYS
    if unknown:
        raise ValueError(
            "logistics task package document declares unknown top-level keys: "
            f"{sorted(unknown)}"
        )
    missing = _LOGISTICS_TASK_PACKAGE_KEYS - set(values)
    if missing:
        raise ValueError(
            "logistics task package document is missing required keys: "
            f"{sorted(missing)}"
        )
    if values.get("schema_version") != LOGISTICS_SCHEMA_VERSION:
        raise ValueError(
            "logistics package schema_version must be "
            f"{LOGISTICS_SCHEMA_VERSION}"
        )
    if values.get("package_id") != LOGISTICS_PACKAGE_ID:
        raise ValueError(
            f"logistics package package_id must be {LOGISTICS_PACKAGE_ID}"
        )
    task_id = values.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("logistics package task_id must be a non-empty string")
    verifier_id = values.get("verifier_id")
    if not isinstance(verifier_id, str) or not verifier_id:
        raise ValueError(
            "logistics package verifier_id must be a non-empty string"
        )

    scene = LogisticsSceneBinding.model_validate(values.get("scene"))
    facilities = compile_authored_facilities(values.get("facilities"))
    fleet = compile_authored_fleet(values.get("fleet"), facilities)
    profiles = compile_authored_performance_profiles(
        values.get("performanceProfiles"), fleet
    )
    orders = compile_authored_orders(values.get("orders"), facilities)
    no_fly_zones = compile_no_fly_zones(values.get("noFlyZones"))
    actors_value = values.get("actors")
    if not isinstance(actors_value, list):
        raise ValueError("logistics package actors must be a JSON array")
    actors = tuple(
        OrderActorGrant.model_validate(item) for item in actors_value
    )
    return LogisticsTaskPackage(
        schema_version=LOGISTICS_SCHEMA_VERSION,
        package_id=LOGISTICS_PACKAGE_ID,
        task_id=task_id,
        verifier_id=verifier_id,
        scene=scene,
        facilities=facilities,
        fleet=fleet,
        performance_profiles=profiles,
        orders=orders,
        no_fly_zones=no_fly_zones,
        actor_grants=actors,
    )


__all__ = [
    "LOGISTICS_BUSINESS_CAPABILITIES",
    "LOGISTICS_FLIGHT_CAPABILITIES",
    "LOGISTICS_PACKAGE_ID",
    "LOGISTICS_REQUIRED_CAPABILITIES",
    "LOGISTICS_SCHEMA_VERSION",
    "LogisticsSceneBinding",
    "LogisticsTaskPackage",
    "lower_logistics_task_package",
]