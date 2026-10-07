from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from aero_bench.config.models import Identifier, StrictModel
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.facility_geometry import facility_landing_pads
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.observation_ingress import (
    LogisticsObservationSpec,
)
from aero_bench.tasks.logistics.orders import (
    LogisticsIdentifier,
    OrderActorRole,
)
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
)
from aero_bench.tasks.logistics.runtime_bindings import LogisticsRuntimeBindings
from aero_bench.providers.logistics_business.scheduled_arrivals import ScheduledOrderCreation
from aero_bench.tasks.logistics.order_arrivals import validate_order_for_arrival
from aero_bench.tasks.logistics_arrivals.domain import LogisticsArrivalsDomain


LOGISTICS_BUSINESS_CONFIG_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-business/v3"
] = "aero-bench.logistics-business/v3"
LOGISTICS_OBSERVATION_CONFIG_SCHEMA_VERSION: Literal[
    "aero-bench.logistics-observation-config/v1"
] = "aero-bench.logistics-observation-config/v1"


class LogisticsBusinessObservationConfig(StrictModel):
    """Declared digest-bound physical-observation configuration (v2 schema).

    This is the *source declaration* for every non-null physical-observation
    fact the private ``logistics.observation.ingest`` surface admits.  It binds
    the exact authored aircraft -> native provider/vehicle + fleet/asset
    identity (``bindings``), the per-aircraft pose-reference calibration
    (``pose_references``, never inferred from body height), the presence
    tolerances (``tolerances``) and the observation plan (``spec``) into the
    provider config file, so the config digest / RunID file inputs pin all of
    them.  The runtime hook factory and the business workload both consume this
    exact declaration; a payload that disagrees with any one of these bound
    identities is rejected with zero journal mutation even when every content
    hash was recomputed over the forged content.

    ``spec.declared_aircraft_ids`` must equal the aircraft set of ``bindings``
    and of ``pose_references`` exactly.  Half body height is never inferred;
    a missing or extra pose-reference calibration is an exact config error.
    """

    schema_version: Literal["aero-bench.logistics-observation-config/v1"]
    bindings: LogisticsRuntimeBindings
    pose_references: tuple[DeclaredAircraftPoseReference, ...]
    tolerances: PresenceTolerances
    spec: LogisticsObservationSpec

    @model_validator(mode="after")
    def observation_config_binds_declared_identities(self) -> "LogisticsBusinessObservationConfig":
        bound_aircraft = tuple(
            sorted(binding.aircraft_id for binding in self.bindings.aircraft)
        )
        pose_aircraft = tuple(
            sorted(reference.aircraft_id for reference in self.pose_references)
        )
        spec_aircraft = tuple(sorted(self.spec.declared_aircraft_ids))
        if bound_aircraft != spec_aircraft:
            raise ValueError(
                "observation config bindings must name exactly the observation "
                f"plan aircraft: declared={spec_aircraft}, "
                f"bound={bound_aircraft}"
            )
        if pose_aircraft != spec_aircraft:
            raise ValueError(
                "observation config pose references must name exactly the "
                f"observation plan aircraft: declared={spec_aircraft}, "
                f"calibrated={pose_aircraft}"
            )
        if len(self.pose_references) != len(pose_aircraft):
            raise ValueError(
                "observation config pose references must be unique per aircraft"
            )
        return self


class LogisticsInfrastructurePrincipal(StrictModel):
    """Explicit (native principal, canonical actor) binding pair.

    The Harness Gateway attests ``CommandRequest.agent_id`` as a native
    infrastructure principal (a provider-network identity that must already be
    a lowercase ``Identifier`` and cannot contain the authored colon). Canonical
    logistics actor ids are authored as ``<entry_id>:<ordinal>`` for aircraft and
    are never assumed equal to a native principal.

    A single native principal may be bound to several canonical actors (for
    example one centralized agent controlling several declared aircraft), each
    as its own explicit ``(principal_id, actor_id)`` pair. A command must name
    the canonical ``actor_id`` it is acting as; the service looks up exactly the
    attested principal + requested actor pair and derives the actor role from the
    pinned ``OrderActorGrant`` table — never from the caller and never from an
    implicit first match on the principal alone.
    """

    principal_id: Identifier
    actor_id: LogisticsIdentifier
    role: OrderActorRole


class LogisticsBusinessConfig(StrictModel):
    """Strict domain declaration for the logistics business Provider.

    The declaration carries domain identity, the immutable canonical physical
    task package or non-physical arrivals domain, native-principal bindings,
    the explicit observation branch and the required scheduled-order tuple.
    Principal ids
    may repeat (a central agent bound to several canonical actors), but every
    ``(principal_id, actor_id)`` pair must be unique and the declared role must
    equal the exact canonical ``OrderActorGrant`` role pinned in the task
    package. The runtime image, endpoint, protocol version and deployment
    parameters are deliberately absent: the materializer owns the runtime
    endpoint and the manifest pins the image/digest, so no configuration field
    can repoint or re-version a declared run.

    v3 requires ``scheduled_orders`` (explicitly empty for an unscheduled
    business workload). It rejects earlier config versions. The non-physical
    arrivals domain cannot enable physical observation ingress. All config
    bytes, actor grants and schedule inputs are pinned by the manifest/Run ID.
    """

    schema_version: Literal["aero-bench.logistics-business/v3"]
    provider_id: Identifier
    task_package: LogisticsTaskPackage | LogisticsArrivalsDomain
    principal_bindings: tuple[LogisticsInfrastructurePrincipal, ...]
    observation: LogisticsBusinessObservationConfig | None
    scheduled_orders: tuple[ScheduledOrderCreation, ...]

    @model_validator(mode="after")
    def bindings_name_canonical_grants(self) -> "LogisticsBusinessConfig":
        canonical_roles = {
            grant.actor_id: grant.role for grant in self.task_package.actor_grants
        }
        pairs = [
            (binding.principal_id, binding.actor_id)
            for binding in self.principal_bindings
        ]
        if len(pairs) != len(set(pairs)):
            raise ValueError(
                "logistics principal bindings must have unique "
                "(principal_id, actor_id) pairs"
            )
        for binding in self.principal_bindings:
            canonical_role = canonical_roles.get(binding.actor_id)
            if canonical_role is None:
                raise ValueError(
                    "logistics principal binding names an actor without a "
                    f"canonical OrderActorGrant: {binding.actor_id}"
                )
            if binding.role != canonical_role:
                raise ValueError(
                    "logistics principal binding role does not match the "
                    f"canonical OrderActorGrant: {binding.actor_id} expected "
                    f"{canonical_role!r}, got {binding.role!r}"
                )
        if self.observation is not None:
            if isinstance(self.task_package, LogisticsArrivalsDomain):
                raise ValueError("non-physical arrivals cannot enable physical observation ingress")
            self._validate_observation_against_task_package()
        event_ids = [item.event_id for item in self.scheduled_orders]
        order_ids = [item.order.order_id for item in self.scheduled_orders]
        if len(set(event_ids)) != len(event_ids) or len(set(order_ids)) != len(order_ids):
            raise ValueError("scheduled order event and order IDs must be unique")
        if set(order_ids) & {item.order_id for item in self.task_package.orders}:
            raise ValueError("scheduled order IDs collide with baseline orders")
        if self.scheduled_orders != tuple(sorted(self.scheduled_orders, key=lambda item: (item.at_tick, item.event_id))):
            raise ValueError("scheduled orders must be sorted by tick and event ID")
        for item in self.scheduled_orders:
            if canonical_roles.get(item.actor_id) != "business":
                raise ValueError("scheduled order actor must hold the canonical business grant")
            validate_order_for_arrival(self.task_package.facilities, self.task_package.fleet, item.order)
        return self

    def _validate_observation_against_task_package(self) -> None:
        """Bind the declared observation plan to the source task package.

        Every (aircraft, facility, pad) item in the declared plan must name a
        participating expanded fleet aircraft and a canonical facility landing
        pad of the pinned task package.  Only the facilities the plan actually
        names are pad-materialized (the pad geometry gate is then exactly the
        declared facility pad the adapter will use); a plan that names an
        undeclared aircraft, facility or pad never survives config load.
        """
        assert self.observation is not None
        aircraft_units = frozenset(
            unit.aircraft_id for unit in self.task_package.aircraft_units()
        )
        planned_facilities = frozenset(
            item.facility_id for item in self.observation.spec.items
        )
        occupied_pads: dict[str, tuple[int, ...]] = {
            facility.facility_id: tuple(
                pad.pad_index for pad in facility_landing_pads(facility)
            )
            for facility in self.task_package.facilities.facilities
            if facility.facility_id in planned_facilities
        }
        for item in self.observation.spec.items:
            if item.aircraft_id not in aircraft_units:
                raise ValueError(
                    "observation plan names an aircraft that is not a declared "
                    f"participating fleet aircraft: {item.aircraft_id}"
                )
            pads = occupied_pads.get(item.facility_id)
            if pads is None:
                raise ValueError(
                    "observation plan names a facility that is not a canonical "
                    f"package facility: {item.facility_id}"
                )
            if item.pad_index not in pads:
                raise ValueError(
                    f"observation plan names pad index {item.pad_index} that is "
                    f"not a declared landing pad of facility {item.facility_id!r}"
                )


__all__ = [
    "LOGISTICS_BUSINESS_CONFIG_SCHEMA_VERSION",
    "LOGISTICS_OBSERVATION_CONFIG_SCHEMA_VERSION",
    "LogisticsBusinessConfig",
    "LogisticsBusinessObservationConfig",
    "LogisticsInfrastructurePrincipal",
]
