"""Logistics task package resolver integration for the ResolvedRun path.

The resolver follows the current inspection/urban integration pattern
(``TaskPackageResolver``): ``scenario_projection`` validates the package and
produces a provider-safe projection, then ``resolve`` validates the scene
binding against the compiled ``ResolvedScenario`` and returns an explicit
``FeasibilityAssessment``.

The registered logistics.business adapter supplies order authority and facility
state. Airspace events, physical delivery observations, and the complete
execution/verifier path remain pending. Recognizing implemented capabilities
never enables the incomplete runtime. PX4 flight capabilities require the
actual px4.gazebo adapter; arbitrary capability declarations cannot supply them.
The canonical package digest and declared planning quantities enter the
feasibility bounds.
"""

from __future__ import annotations

import math

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FeasibilityAssessment,
    NamedValue,
    TaskSpec,
)
from aero_bench.tasks.logistics.airspace_events import NO_FLY_FRAME
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_FLIGHT_CAPABILITIES,
    LOGISTICS_PACKAGE_ID,
    LOGISTICS_REQUIRED_CAPABILITIES,
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.world.resolved import (
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
)

#: Adapter id of the existing PX4 Gazebo Provider that physically supplies the
#: flight command/observation capabilities a logistics execution reuses. This
#: is the current environment-contract adapter (``releases/inspection-v1``),
#: not an invented logistics adapter.
LOGISTICS_FLIGHT_PROVIDER_ADAPTERS: frozenset[str] = frozenset({"px4.gazebo"})

#: The logistics execution/verifier runtime is not implemented in Phase 4 WP1.
LOGISTICS_RUNTIME_IMPLEMENTED = False

#: The compiled ResolvedScenario WGS84 origin is the frame-math round-trip of
#: the authored origin and differs by ~1e-14 deg / ~1e-9 m, so the package
#: scene binding compares with an explicit tolerance.
_SCENE_ORIGIN_TOLERANCE_DEG = 1e-9
_SCENE_ORIGIN_TOLERANCE_M = 1e-6


class LogisticsPackageResolutionError(ValueError):
    """A strict logistics package contract violation, distinct from infeasibility."""


def load_logistics_package(
    *,
    reader: BundleReader,
    task: TaskSpec,
) -> LogisticsTaskPackage:
    """Load the schema-bound package document and lower it strictly."""

    if task.package.package_id != LOGISTICS_PACKAGE_ID:
        raise LogisticsPackageResolutionError(
            "logistics resolver received another task package"
        )
    reader.validate_schema_bound_file(task.package.config)
    return lower_logistics_task_package(
        reader.load_document(task.package.config.file)
    )


def _validate_package_identity(
    *,
    package: LogisticsTaskPackage,
    task: TaskSpec,
) -> None:
    if package.task_id != task.task_id:
        raise LogisticsPackageResolutionError(
            "logistics package task_id does not match TaskSpec"
        )
    if package.verifier_id != task.verifier.verifier_id:
        raise LogisticsPackageResolutionError(
            "logistics package verifier_id does not match TaskSpec"
        )


def _validate_scene_binding(
    *,
    package: LogisticsTaskPackage,
    scenario: ResolvedScenario,
) -> None:
    """Bind scene identity, asset provenance, WGS84 origin, and local frame."""
    if not isinstance(scenario, ResolvedScenario):
        raise TypeError("logistics resolution requires a ResolvedScenario")
    if package.scene.scene_id != scenario.world_id:
        raise LogisticsPackageResolutionError(
            "logistics scene_id does not match the ResolvedScenario world_id"
        )
    if package.scene.scene_source_sha256 != scenario.source_asset_digest:
        raise LogisticsPackageResolutionError(
            "logistics scene source digest does not match the ResolvedScenario "
            "world asset provenance"
        )
    origin = scenario.frame_authority.origin.wgs84
    if not math.isclose(
        origin.longitude_deg,
        package.scene.origin_longitude_deg,
        abs_tol=_SCENE_ORIGIN_TOLERANCE_DEG,
        rel_tol=0.0,
    ) or not math.isclose(
        origin.latitude_deg,
        package.scene.origin_latitude_deg,
        abs_tol=_SCENE_ORIGIN_TOLERANCE_DEG,
        rel_tol=0.0,
    ):
        raise LogisticsPackageResolutionError(
            "logistics scene WGS84 origin does not match the ResolvedScenario "
            "frame authority"
        )
    if not math.isclose(
        origin.ellipsoid_height_m,
        package.scene.origin_altitude_m,
        abs_tol=_SCENE_ORIGIN_TOLERANCE_M,
        rel_tol=0.0,
    ):
        raise LogisticsPackageResolutionError(
            "logistics scene origin altitude does not match the ResolvedScenario "
            "frame authority"
        )
    if package.scene.coordinate_frame != NO_FLY_FRAME:
        raise LogisticsPackageResolutionError(
            "logistics package does not use the scene_east_south_m local frame"
        )


def _physical_logistics_capabilities(environment: EnvironmentSpec) -> frozenset[str]:
    """Recognize only capabilities supported by the declared real adapter."""
    # Import after registry initialization: the adapter itself imports registry
    # endpoint types, while registry construction loads task contracts.
    from aero_bench.providers.logistics_business.adapter import (
        LOGISTICS_BUSINESS_ADAPTER,
        SUPPORTED_CAPABILITIES,
    )

    supplied: set[str] = set()
    for provider in environment.providers:
        if provider.adapter in LOGISTICS_FLIGHT_PROVIDER_ADAPTERS:
            supplied.update(
                capability
                for capability in provider.capabilities
                if capability in LOGISTICS_FLIGHT_CAPABILITIES
            )
        if provider.adapter == LOGISTICS_BUSINESS_ADAPTER:
            supplied.update(set(provider.capabilities) & SUPPORTED_CAPABILITIES)
    return frozenset(supplied)


def _missing_logistics_provider_prerequisites(
    *,
    task: TaskSpec,
    environment: EnvironmentSpec,
) -> tuple[str, ...]:
    """Declared logistics capabilities with no physically-supplying Provider.

    Only capabilities the TaskSpec honestly declares (which must be inside the
    current ``LOGISTICS_REQUIRED_CAPABILITIES`` capability model) are checked.
    A task that declares none is never faulted here; ``resolve`` still reports
    the runtime as pending.
    """

    declared = set(task.required_capabilities) & LOGISTICS_REQUIRED_CAPABILITIES
    if not declared:
        return ()
    missing = declared - _physical_logistics_capabilities(environment)
    return tuple(sorted(missing))


def _assessment_bounds(package: LogisticsTaskPackage) -> tuple[NamedValue, ...]:
    aircraft_units = package.aircraft_units()
    return (
        NamedValue(
            name="logistics.package.digest",
            value=package.canonical_digest(),
        ),
        NamedValue(name="logistics.orders.count", value=len(package.orders)),
        NamedValue(name="logistics.fleet.units", value=len(aircraft_units)),
        NamedValue(name="logistics.nofly.zones", value=len(package.no_fly_zones)),
        NamedValue(
            name="logistics.cargo.total-kg",
            value=sum(order.cargo_mass_kg for order in package.orders),
        ),
    )


def _infeasible_assessment(
    package: LogisticsTaskPackage,
    failed_conditions: tuple[str, ...],
) -> FeasibilityAssessment:
    return FeasibilityAssessment(
        package_id=LOGISTICS_PACKAGE_ID,
        feasible=False,
        success_upper_bound=0.0,
        failed_conditions=failed_conditions,
        bounds=_assessment_bounds(package),
    )


class LogisticsTaskPackageResolver:
    @property
    def package_id(self) -> str:
        return LOGISTICS_PACKAGE_ID

    @property
    def runtime_available(self) -> bool:
        """Explicit answer to "does this resolver claim a runnable runtime?"."""

        return LOGISTICS_RUNTIME_IMPLEMENTED

    def scenario_projection(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
    ) -> ResolvedTaskScenarioProjection:
        """Validate package semantics and provide a provider-safe projection.

        WP1 asserts no task-local logical endpoint: flight, facilities, orders,
        airspace, and delivery evidence must come from real Providers, so
        nothing is claimed as task-local. A TaskSpec that honestly declares
        logistics required capabilities is accepted (not an input error) and,
        when a declared capability has no physically-supplying real Provider
        (the existing ``px4.gazebo`` flight backend for ``flight.command`` /
        ``observation.capture``, or a registered logistics business Provider
        for the new ``logistics.*`` capabilities), ``scenario_projection``
        raises a clear :class:`LogisticsPackageResolutionError` naming the
        missing real prerequisites instead of returning a projection that the
        generic scenario compiler would reject with an unrelated invariant
        error. When nothing is physically provided, ``resolve`` still reports
        explicit infeasibility for the pending runtime.
        """
        package = load_logistics_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task)
        missing_prerequisites = _missing_logistics_provider_prerequisites(
            task=task, environment=environment
        )
        if missing_prerequisites:
            raise LogisticsPackageResolutionError(
                "logistics scenario projection requires real logistics "
                "Providers physically supplying the declared capabilities "
                "below; none is registered in the compiled environment "
                "(logistics Provider adapters and runtime are pending "
                "implementation, not an input error), so no runnable "
                f"projection is returned: {missing_prerequisites}"
            )
        return ResolvedTaskScenarioProjection(
            logical_endpoint_ids=(),
            logical_capability_ids=(),
            observations=(),
        )

    def resolve(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
        scenario: ResolvedScenario,
    ) -> FeasibilityAssessment:
        """Validate the run contract and report explicit logistics feasibility.

        Semantic/reference or scene-binding violations raise
        :class:`LogisticsPackageResolutionError`. The existing PX4 Gazebo
        flight capabilities (``flight.command``, ``observation.capture``) are
        recognized when the real ``px4.gazebo`` Provider is physically declared;
        the new ``logistics.*`` business capabilities and the pending logistics
        runtime still produce an explicit infeasible assessment. The resolver
        never fabricates a successful run.
        """
        package = load_logistics_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task)
        _validate_scene_binding(package=package, scenario=scenario)

        missing = LOGISTICS_REQUIRED_CAPABILITIES - (
            _physical_logistics_capabilities(environment)
        )
        failed_conditions: list[str] = []
        if missing:
            failed_conditions.append("logistics.provider.pending")
            failed_conditions.extend(sorted(missing))
        if not LOGISTICS_RUNTIME_IMPLEMENTED:
            failed_conditions.append("logistics.runtime.pending")
        if failed_conditions:
            return _infeasible_assessment(
                package,
                failed_conditions=tuple(sorted(failed_conditions)),
            )

        # Unreachable until a real logistics runtime and verifier land. The
        # planning upper bound is the declared-parameter fraction of orders with
        # a payload-capable declared aircraft; no telemetry is invented.
        aircraft_units = package.aircraft_units()
        covered = sum(
            1
            for order in package.orders
            if any(
                unit.max_payload_kg >= order.cargo_mass_kg
                for unit in aircraft_units
            )
        )
        upper_bound = covered / len(package.orders) if package.orders else 1.0
        return FeasibilityAssessment(
            package_id=LOGISTICS_PACKAGE_ID,
            feasible=upper_bound > 0.0,
            success_upper_bound=upper_bound,
            failed_conditions=(
                ()
                if upper_bound > 0.0
                else ("logistics.planner.no-capacity",)
            ),
            bounds=_assessment_bounds(package),
        )


__all__ = [
    "LOGISTICS_FLIGHT_PROVIDER_ADAPTERS",
    "LOGISTICS_PACKAGE_ID",
    "LOGISTICS_RUNTIME_IMPLEMENTED",
    "LogisticsPackageResolutionError",
    "LogisticsTaskPackageResolver",
    "load_logistics_package",
]