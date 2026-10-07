"""Explicit immutable logistics-to-native runtime binding contract (WP1).

Phase 4 WP1 delivers the static binding layer between the authored logistics
fleet identity and the concrete native runtime identities the current
EnvironmentSpec / AgentSpec / PX4-Gazebo config / ResolvedScenario actually
declare.  It is a *contract* for the future logistics runtime and verifier; it
never implements an executor, a Provider, or a fallback.

The authored aircraft identity produced by
``Fleet.expand_aircraft_units`` is ``<fleet_entry_id>:<ordinal>`` and may
contain uppercase letters and a colon (``LogisticsIdentifier``).  The native
runtime identifiers are strict ``Identifier`` values (lowercase only): the
``px4.gazebo`` Provider's ``provider_id``, the physical aircraft identity the
real PX4 config declares in its ``vehicles[].vehicle_id``, and the controlling
native ``AgentSpec.agent_id``.  A native ``AgentSpec.agent_id`` cannot even
*contain* the authored colon, so the mapping from an authored aircraft unit to
its native provider/vehicle/agent must be explicit and immutable.  Silent
renames, string substitution, and inference from identifiers are forbidden.

Every binding is validated against the real current contracts:

* ``EnvironmentSpec``: the binding names an existing Provider whose adapter is
  exactly ``px4.gazebo`` and which physically supplies ``flight.command``.
* the real PX4-Gazebo config: the Provider's schema-bound config file is first
  validated against the caller-declared JSON Schema, then parsed with the
  *current registered native* ``Px4GazeboConfig`` strict v3 model
  (``aero_bench/providers/px4_gazebo/config.py``).  Only a config the real
  ``px4.gazebo`` backend can actually materialize is accepted; stale v1 release
  documents are rejected.  The binding's ``vehicle_id`` must be declared by the
  typed ``vehicles[].vehicle_id`` set, and the typed ``provider_id`` must match
  the Provider.  The native model itself enforces uniqueness, so a document
  that sneaks a duplicate ``vehicle_id`` past a permissive caller schema (no
  ``uniqueItems``) is still rejected.
* ``AgentSpec``: the bound controlling agent exists and holds at least one real
  flight-control tool grant against *that same* Provider.  The allowlist covers
  every currently registered PX4 command tool: ``flight.arm``, ``flight.disarm``,
  ``flight.takeoff``, ``flight.land``, ``flight.goto``, ``flight.hold`` (all
  vehicle-scoped per the provider's ``TOOL_COMMAND_AUDIT_SPEC``).  Holding at
  least one of these grants establishes only that the agent is *declared*
  operable for that one operation authority; it is **not** a full flight-mission
  authorization (sequencing, refuelling, business gates, or any other grant the
  package declares).  A central dispatcher agent may control many aircraft
  through the Provider's vehicle-scoped tools; no one-agent-per-aircraft rule is
  invented.
* ``ResolvedScenario``: the ``vehicle_id`` resolves to a dynamic Gazebo-physics
  UAV entity owned by the bound Provider, and the Provider is itself resolved.

Non-aircraft logistics actor grants (``dispatcher``, ``business``) are mapped
through explicit principal bindings to a native ``AgentSpec.agent_id``.  Those
bindings check native **agent identity existence only** — they claim no tool
grant and no order-transition authority.  Actual order-tool authorization
remains the real business Provider's gate (``business.*``  provider), which this
static contract does not interpose on.  The business actor grants remain
declared in the package; the aircraft asset id (``visual_asset_id``,
``model:...``) is carried as a distinct field and is never conflated with the
native ``vehicle_id``.

Wrong, missing, duplicate, uncontrolled, or cross-Provider bindings are
rejected with a concrete message; no binding is invented, unsupported rules are
reported as exact limitations, and the returned records are immutable with a
canonical digest over every binding field.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    Identifier,
    SchemaBoundFile,
    StrictModel,
)
from aero_bench.providers.px4_gazebo.config import Px4GazeboConfig
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import LogisticsTaskPackage
from aero_bench.tasks.logistics.fleet import AssetIdentifier, FleetIdentifier
from aero_bench.tasks.logistics.orders import LogisticsIdentifier, OrderActorRole
from aero_bench.world.resolved import ResolvedScenario

LOGISTICS_BINDINGS_SCHEMA_VERSION: Literal["aero-bench.logistics-bindings/v1"] = (
    "aero-bench.logistics-bindings/v1"
)

#: Adapter id of the existing real PX4/Gazebo backend. A logistics execution
#: reuses exactly this backend (``releases/inspection-v1`` declares it), so a
#: binding must name a Provider using this adapter and nothing else.
LOGISTICS_FLIGHT_PROVIDER_ADAPTER: str = "px4.gazebo"

#: The current real flight-control tool ids whose request schemas address a
#: ``vehicle_id`` on the ``px4.gazebo`` Provider. These are exactly the six
#: PX4 command tools the registered provider backend actually registers
#: (``flights provider TOOL_COMMAND_AUDIT_SPEC``: arm/disarm/takeoff/land/goto/
#: hold); they are never invented here and there is no inspection-specific
#: subset.  Holding any one of them for an agent only establishes that the agent
#: is declared operable for that operation authority — it is not a full
#: flight-mission authorization (sequencing, business gates, and every other
#: operation remain under the package's real business provider gate).
LOGISTICS_FLIGHT_CONTROL_TOOLS: frozenset[str] = frozenset(
    {
        "flight.arm",
        "flight.disarm",
        "flight.takeoff",
        "flight.land",
        "flight.goto",
        "flight.hold",
    }
)

_LOGISTICS_BINDINGS_KEYS: frozenset[str] = frozenset(
    {"schema_version", "aircraft", "principals"}
)


class LogisticsBindingError(ValueError):
    """A strict logistics runtime-binding contract violation."""


class LogisticsAircraftBinding(StrictModel):
    """Bind one authored aircraft unit to its native runtime identities.

    Fields and their roles:

    * ``aircraft_id`` -- the authored fleet unit ``<fleet_entry_id>:<ordinal>``
      exactly as produced by ``Fleet.expand_aircraft_units``.  Uppercase letters
      and the colon are preserved verbatim; this is *not* a native identifier.
    * ``fleet_entry_id`` -- the authored fleet entry this unit expands from.
    * ``visual_asset_id`` -- the authored visual asset reference
      (``model:...``).  It remains distinct from ``vehicle_id``: the aircraft
      asset identity is never conflated with the physical vehicle identity.
    * ``provider_id`` -- native ``EnvironmentSpec.providers[].provider_id`` for
      an existing ``px4.gazebo`` Provider.
    * ``vehicle_id`` -- native physical aircraft identity declared by that
      Provider's real PX4-Gazebo config (``vehicles[].vehicle_id``).
    * ``controlling_agent_id`` -- native ``AgentSpec.agent_id`` that holds the
      flight-control tool grant against ``provider_id``.  One agent may control
      multiple aircraft; no one-agent-per-aircraft rule is applied.
    """

    aircraft_id: LogisticsIdentifier
    fleet_entry_id: FleetIdentifier
    visual_asset_id: AssetIdentifier
    provider_id: Identifier
    vehicle_id: Identifier
    controlling_agent_id: Identifier


class LogisticsPrincipalBinding(StrictModel):
    """Bind an authored non-aircraft actor grant to a native controlling agent.

    ``actor_id`` *must* match a package ``OrderActorGrant.actor_id`` declared
    with the same ``role``. The aircraft_agent authority is expressed by the
    aircraft binding's ``controlling_agent_id``, so principals only cover
    ``dispatcher`` / ``business`` roles. Business actor grants stay declared in
    the package; this record maps them to the concrete native agent identity.

    For ``dispatcher`` / ``business`` the binding checks native **agent identity
    existence only** — it validates that ``agent_id`` names a real
    ``AgentSpec.agent_id`` and claims no tool grant and no order-transition
    authority.  Actual order-tool authorization remains the real business
    Provider's gate (``business.*`` provider), which this static contract does
    not interpose on.
    """

    actor_id: LogisticsIdentifier
    role: OrderActorRole
    agent_id: Identifier


class LogisticsRuntimeBindings(StrictModel):
    """Immutable validated set of logistics-to-native runtime bindings.

    The canonical digest covers every field of every aircraft and principal
    binding (``schema_version``, ``aircraft`` with all six fields, ``principals``
    with all three fields), so the digest changes when any binding field changes.
    """

    schema_version: Literal["aero-bench.logistics-bindings/v1"]
    aircraft: tuple[LogisticsAircraftBinding, ...] = Field(min_length=1)
    principals: tuple[LogisticsPrincipalBinding, ...] = ()

    @model_validator(mode="after")
    def bindings_are_explicit_and_unique(self) -> "LogisticsRuntimeBindings":
        aircraft_ids = [binding.aircraft_id for binding in self.aircraft]
        if len(aircraft_ids) != len(set(aircraft_ids)):
            raise ValueError(
                "duplicate aircraft binding for a single authored aircraft"
            )
        vehicle_keys = [
            (binding.provider_id, binding.vehicle_id)
            for binding in self.aircraft
        ]
        if len(vehicle_keys) != len(set(vehicle_keys)):
            raise ValueError(
                "duplicate physical aircraft: two authored aircraft bind to the "
                "same native vehicle on the same Provider"
            )
        principal_keys = [
            (binding.actor_id, binding.role) for binding in self.principals
        ]
        if len(principal_keys) != len(set(principal_keys)):
            raise ValueError(
                "duplicate logistics principal binding for one actor grant"
            )
        aircraft_units = frozenset(aircraft_ids)
        for binding in self.principals:
            if binding.role != "aircraft_agent" and binding.actor_id in aircraft_units:
                raise ValueError(
                    f"principal role {binding.role} cannot name an aircraft "
                    f"identity: {binding.actor_id}; aircraft_agent authority is "
                    "expressed by the aircraft binding, never by a role statement"
                )
        return self

    def canonical_digest(self) -> str:
        """Deterministic SHA-256 over every binding field."""

        return hashlib.sha256(
            canonical_json_bytes(self.model_dump(mode="json"))
        ).hexdigest()


def _reject_non_list(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")


def compile_logistics_runtime_bindings(values: object) -> LogisticsRuntimeBindings:
    """Lower the authored bindings document into the immutable typed records.

    The document has exactly the top-level keys ``schema_version``,
    ``aircraft``, and ``principals``. Every field is authored explicitly; nothing
    is inferred from any other identity.
    """
    if not isinstance(values, dict):
        raise LogisticsBindingError(
            "logistics runtime bindings document must be a JSON object"
        )
    unknown = set(values) - _LOGISTICS_BINDINGS_KEYS
    if unknown:
        raise LogisticsBindingError(
            "logistics runtime bindings document declares unknown top-level "
            f"keys: {sorted(unknown)}"
        )
    missing = _LOGISTICS_BINDINGS_KEYS - set(values)
    if missing:
        raise LogisticsBindingError(
            "logistics runtime bindings document is missing required keys: "
            f"{sorted(missing)}"
        )
    if values.get("schema_version") != LOGISTICS_BINDINGS_SCHEMA_VERSION:
        raise LogisticsBindingError(
            "logistics runtime bindings schema_version must be "
            f"{LOGISTICS_BINDINGS_SCHEMA_VERSION}"
        )
    _reject_non_list(values.get("aircraft"), "logistics runtime bindings aircraft")
    _reject_non_list(values.get("principals"), "logistics runtime bindings principals")
    if not values["aircraft"]:
        raise LogisticsBindingError(
            "logistics runtime bindings must declare at least one aircraft binding"
        )
    return LogisticsRuntimeBindings(
        schema_version=LOGISTICS_BINDINGS_SCHEMA_VERSION,
        aircraft=tuple(
            LogisticsAircraftBinding.model_validate(item)
            for item in values["aircraft"]
        ),
        principals=tuple(
            LogisticsPrincipalBinding.model_validate(item)
            for item in values["principals"]
        ),
    )


def _load_px4_gazebo_config(
    *, reader: BundleReader, provider_config: SchemaBoundFile
) -> Px4GazeboConfig:
    """Load a Provider's config and parse it with the current native v3 model.

    Two layers run, in order:

    1. the caller-declared JSON Schema is applied through the BundleReader;
    2. the *current registered native* ``Px4GazeboConfig`` strict v3 model must
       be able to materialize the document.

    Layer 2 is the authority: a config the real ``px4.gazebo`` backend cannot
    build a session from must never be accepted (stale v1 release documents are
    rejected here even if the caller schema still admits them).  The returned
    value is the typed native model, so downstream checks use its typed
    ``provider_id`` and ``vehicles`` — no hand-rolled raw JSON traversal and no
    duplicate-id frozenset masking.  Any loader/model failure is surfaced as a
    ``LogisticsBindingError`` that retains the original cause and names the
    config path for a clear native-config diagnosis.
    """
    try:
        instance = reader.validate_schema_bound_file(provider_config)
    except ValueError as error:
        raise LogisticsBindingError(
            "px4.gazebo Provider config failed caller-declared schema or file "
            f"validation ({provider_config.file.path}): {error}"
        ) from error
    if not isinstance(instance, dict):
        raise LogisticsBindingError(
            "px4.gazebo Provider config must be a JSON object"
        )
    try:
        return Px4GazeboConfig.model_validate(instance)
    except (ValidationError, ValueError, TypeError) as error:
        raise LogisticsBindingError(
            "px4.gazebo Provider config cannot be materialized by the current "
            f"native Px4GazeboConfig strict v3 model ({provider_config.file.path}): "
            f"{error}"
        ) from error


def validate_logistics_runtime_bindings(
    *,
    bindings_document: object,
    reader: BundleReader,
    environment: EnvironmentSpec,
    agents: tuple[AgentSpec, ...],
    package: LogisticsTaskPackage,
    scenario: ResolvedScenario,
) -> LogisticsRuntimeBindings:
    """Validate the authored bindings against the real current runtime contracts.

    Every participating aircraft (``package.aircraft_units()``) must be bound
    exactly once to an existing ``px4.gazebo`` Provider whose PX4 config is a
    document the *current registered native* ``Px4GazeboConfig`` strict v3 model
    can materialize and which declares the physical ``vehicle_id``, to a
    resolved Gazebo-physics UAV entity owned by that Provider, and to a native
    controlling agent holding at least one real flight-control tool grant against
    the same Provider (the full current PX4 command allowlist).  Non-aircraft
    actor grants are mapped through explicit principal bindings whose identity
    existence is checked; an unbound grant or a principal/role mismatch is
    reported as an exact limitation. No binding is inferred, renamed, or
    invented.
    """
    if not isinstance(reader, BundleReader):
        raise TypeError("logistics runtime bindings require a BundleReader")
    if not isinstance(environment, EnvironmentSpec):
        raise TypeError("logistics runtime bindings require an EnvironmentSpec")
    if not isinstance(package, LogisticsTaskPackage):
        raise TypeError("logistics runtime bindings require a LogisticsTaskPackage")
    if not isinstance(scenario, ResolvedScenario):
        raise TypeError("logistics runtime bindings require a ResolvedScenario")

    bindings = compile_logistics_runtime_bindings(bindings_document)

    providers = {provider.provider_id: provider for provider in environment.providers}
    agents_by_id = {agent.agent_id: agent for agent in agents}
    if not agents_by_id:
        raise LogisticsBindingError(
            "logistics runtime bindings require at least one native AgentSpec"
        )

    units_by_id = {unit.aircraft_id: unit for unit in package.aircraft_units()}
    expanded_ids = frozenset(units_by_id)
    bound_ids = frozenset(binding.aircraft_id for binding in bindings.aircraft)
    missing_aircraft = expanded_ids - bound_ids
    extra_aircraft = bound_ids - expanded_ids
    if missing_aircraft or extra_aircraft:
        raise LogisticsBindingError(
            "aircraft bindings must cover exactly every participating aircraft "
            "from Fleet.expand_aircraft_units; "
            f"unbound aircraft={sorted(missing_aircraft) or 'none'}, "
            f"unknown aircraft={sorted(extra_aircraft) or 'none'}"
        )

    for binding in bindings.aircraft:
        unit = units_by_id[binding.aircraft_id]
        if binding.fleet_entry_id != unit.fleet_entry_id:
            raise LogisticsBindingError(
                f"aircraft binding {binding.aircraft_id} fleet_entry_id does not "
                "match the expanded fleet unit"
            )
        if binding.visual_asset_id != unit.visual_asset_id:
            raise LogisticsBindingError(
                f"aircraft binding {binding.aircraft_id} visual_asset_id does not "
                "match the expanded fleet unit; aircraft asset id is distinct "
                "from the native vehicle id"
            )

    aircraft_agent_actors = {
        grant.actor_id
        for grant in package.actor_grants
        if grant.role == "aircraft_agent"
    }
    unbound_aircraft_grant = aircraft_agent_actors - bound_ids
    if unbound_aircraft_grant:
        raise LogisticsBindingError(
            "aircraft_agent actor grants reference aircraft without a runtime "
            f"binding: {sorted(unbound_aircraft_grant)}"
        )

    scenario_entities = {entity.entity_id: entity for entity in scenario.entities}
    scenario_providers = {
        provider.provider_id for provider in scenario.providers
    }
    for binding in bindings.aircraft:
        provider = providers.get(binding.provider_id)
        if provider is None:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} names unknown native provider "
                f"{binding.provider_id}"
            )
        if provider.adapter != LOGISTICS_FLIGHT_PROVIDER_ADAPTER:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} provider {binding.provider_id} "
                f"uses adapter {provider.adapter}, not "
                f"{LOGISTICS_FLIGHT_PROVIDER_ADAPTER}"
            )
        if "flight.command" not in provider.capabilities:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} provider {binding.provider_id} "
                "does not physically supply flight.command"
            )

        config = _load_px4_gazebo_config(
            reader=reader, provider_config=provider.config
        )
        if config.provider_id != provider.provider_id:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} provider {binding.provider_id} "
                "config declares a different provider_id"
            )
        vehicles = {vehicle.vehicle_id for vehicle in config.vehicles}
        if binding.vehicle_id not in vehicles:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} vehicle {binding.vehicle_id} is "
                f"not declared by the px4.gazebo config of {binding.provider_id}"
            )

        agent = agents_by_id.get(binding.controlling_agent_id)
        if agent is None:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} names unknown controlling "
                f"native agent {binding.controlling_agent_id}"
            )
        control_tools = {
            tool.tool_id
            for tool in agent.tools
            if tool.provider_id == binding.provider_id
        }
        if not (control_tools & LOGISTICS_FLIGHT_CONTROL_TOOLS):
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} controlling agent "
                f"{binding.controlling_agent_id} lacks a flight-control tool "
                f"grant against {binding.provider_id} (control-grant mismatch)"
            )

        entity = scenario_entities.get(binding.vehicle_id)
        if entity is None:
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} vehicle {binding.vehicle_id} has "
                "no resolved scenario entity"
            )
        if (
            entity.kind != "uav"
            or entity.state != "dynamic"
            or entity.authority_kind != "gazebo_physics"
            or entity.owner_kind != "provider"
            or entity.owner_id != binding.provider_id
        ):
            raise LogisticsBindingError(
                f"aircraft {binding.aircraft_id} vehicle {binding.vehicle_id} is "
                f"not a dynamic Gazebo-physics UAV owned by {binding.provider_id} "
                "in the ResolvedScenario (cross-Provider or wrong-authority "
                "binding)"
            )
        if binding.provider_id not in scenario_providers:
            raise LogisticsBindingError(
                f"provider {binding.provider_id} is not resolved as a scenario "
                "authority"
            )

    grants = {(grant.actor_id, grant.role) for grant in package.actor_grants}
    principal_keys = {
        (binding.actor_id, binding.role) for binding in bindings.principals
    }
    for binding in bindings.principals:
        if (binding.actor_id, binding.role) not in grants:
            raise LogisticsBindingError(
                f"principal actor-role mismatch: package declares no "
                f"{binding.role} grant for actor {binding.actor_id}"
            )
        if binding.role == "aircraft_agent":
            raise LogisticsBindingError(
                "aircraft_agent authority is expressed by the aircraft binding's "
                "controlling_agent_id, not by a principal record"
            )
        if binding.role in ("dispatcher", "business"):
            if binding.actor_id in units_by_id:
                raise LogisticsBindingError(
                    f"principal role {binding.role} cannot name an aircraft "
                    f"identity: {binding.actor_id}"
                )
            if binding.agent_id not in agents_by_id:
                raise LogisticsBindingError(
                    f"principal for actor {binding.actor_id} ({binding.role}) "
                    f"names unknown native agent {binding.agent_id}"
                )

    non_aircraft_grants = {
        (grant.actor_id, grant.role)
        for grant in package.actor_grants
        if grant.role != "aircraft_agent"
    }
    unbound_grants = non_aircraft_grants - principal_keys
    if unbound_grants:
        raise LogisticsBindingError(
            "execution auth provides no native principal agent for logistics "
            f"actor grants: {sorted(unbound_grants)}"
        )

    return bindings


__all__ = [
    "LOGISTICS_BINDINGS_SCHEMA_VERSION",
    "LOGISTICS_FLIGHT_CONTROL_TOOLS",
    "LOGISTICS_FLIGHT_PROVIDER_ADAPTER",
    "LogisticsAircraftBinding",
    "LogisticsBindingError",
    "LogisticsPrincipalBinding",
    "LogisticsRuntimeBindings",
    "compile_logistics_runtime_bindings",
    "validate_logistics_runtime_bindings",
]