"""Registered native-parcel task resolution over the real provider and hook.

The single-parcel slice uses its own package ID and capability requirements.
Its runtime hook composes closed motion observations with native parcel
authority; the dedicated sealed verifier evaluates the resulting artifacts.
"""

from __future__ import annotations

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    AgentSpec,
    EnvironmentSpec,
    FeasibilityAssessment,
    ProviderRef,
    TaskSpec,
)
from aero_bench.tasks.logistics.contracts import (
    LogisticsTaskPackage,
    lower_logistics_task_package,
)
from aero_bench.tasks.logistics.integration import (
    _assessment_bounds,
    _physical_logistics_capabilities,
    _validate_package_identity,
    _validate_scene_binding,
)
from aero_bench.tasks.logistics.native_parcel_hook import NativeParcelRuntimeHook
from aero_bench.providers.logistics_business.native_parcel import (
    NATIVE_PARCEL_ADAPTER,
    NATIVE_PARCEL_CAPABILITY,
    NativeParcelBusinessConfig,
)
from aero_bench.tasks.logistics.runtime_hook import LogisticsRuntimeHookError
from aero_bench.world.resolved import (
    ResolvedScenario,
    ResolvedTaskScenarioProjection,
)

#: Explicit package id that selects the native parcel slice.  It is distinct
#: from the base ``logistics.task.v1`` id on purpose: the registry maps
#: ``resolver.package_id -> resolver`` and the runtime hook resolver rejects
#: duplicate factory ids, so claiming the base id would overwrite or collide
#: with the pending base logistics entries instead of adding a slice.
NATIVE_PARCEL_PACKAGE_ID = "logistics.task.native-parcel.v1"

#: Default provider id the native slice binds to.  The declared config's
#: ``provider_id`` must equal the resolved provider's id exactly.
NATIVE_BUSINESS_PROVIDER_ID = "logistics.native-business"

NATIVE_PARCEL_REQUIRED_CAPABILITIES = frozenset({
    "flight.command", "logistics.facilities.state", "logistics.orders.authority",
    NATIVE_PARCEL_CAPABILITY,
})


class NativeParcelPackageResolutionError(ValueError):
    """A strict native-parcel slice violation, distinct from infeasibility."""


def load_native_parcel_package(
    *,
    reader: BundleReader,
    task: TaskSpec,
) -> LogisticsTaskPackage:
    """Digest-verify, load and lower the explicitly selected native package.

    The base loader
    (:func:`aero_bench.tasks.logistics.integration.load_logistics_package`)
    pins the base ``logistics.task.v1`` package id, so the native slice runs
    this same strict chain against the explicit native package id instead:
    the digest-pinned package ``SchemaBoundFile`` is verified through the real
    ``BundleReader`` contract and the document is lowered by the real shared
    lowerer.  Nothing about the base logistics runtime changes.
    """
    if task.package.package_id != NATIVE_PARCEL_PACKAGE_ID:
        raise NativeParcelPackageResolutionError(
            "the native parcel resolver received another task package: "
            f"expected package_id {NATIVE_PARCEL_PACKAGE_ID!r}, received "
            f"{task.package.package_id!r}"
        )
    reader.validate_schema_bound_file(task.package.config)
    return lower_logistics_task_package(
        reader.load_document(task.package.config.file)
    )


def _resolved_native_provider(
    *,
    environment: EnvironmentSpec,
    business_provider_id: str,
) -> ProviderRef:
    """Resolve the explicitly declared native parcel business provider.

    The native slice is selected by the provider's exact identity, its exact
    native adapter and its explicitly declared parcel authority capability.
    Nothing is inferred from roles, naming or the base ``logistics.business``
    adapter.
    """
    from aero_bench.providers.logistics_business.adapter import (
        SUPPORTED_CAPABILITIES,
    )

    if not isinstance(environment, EnvironmentSpec):
        raise TypeError("native parcel resolution requires an EnvironmentSpec")
    providers = {
        provider.provider_id: provider for provider in environment.providers
    }
    provider = providers.get(business_provider_id)
    if provider is None:
        raise NativeParcelPackageResolutionError(
            "the compiled environment does not declare the native parcel "
            f"business provider {business_provider_id!r}"
        )
    if provider.adapter != NATIVE_PARCEL_ADAPTER:
        raise NativeParcelPackageResolutionError(
            f"provider {business_provider_id!r} declares adapter "
            f"{provider.adapter!r}, not the native parcel adapter "
            f"{NATIVE_PARCEL_ADAPTER!r}; the native slice is selected by an "
            "explicit adapter, never inferred"
        )
    declared = set(provider.capabilities)
    unsupported = declared - (SUPPORTED_CAPABILITIES | {NATIVE_PARCEL_CAPABILITY})
    if unsupported:
        raise NativeParcelPackageResolutionError(
            f"native parcel provider {business_provider_id!r} declares "
            f"capabilities its adapter does not supply: {sorted(unsupported)}"
        )
    if NATIVE_PARCEL_CAPABILITY not in declared:
        raise NativeParcelPackageResolutionError(
            "native parcel authority must be explicitly declared: provider "
            f"{business_provider_id!r} lacks {NATIVE_PARCEL_CAPABILITY!r}"
        )
    return provider


def _load_declared_native_config(
    *,
    reader: BundleReader,
    provider: ProviderRef,
    environment: EnvironmentSpec,
) -> NativeParcelBusinessConfig:
    """Digest-verify and type the declared native provider config.

    The config bytes are exactly the ones the resolved environment pins in the
    provider's ``SchemaBoundFile``; the strict native model cross-validates
    them against its nested task package, declared pads, observation plan and
    principal grant.  The declared session run bound must equal the resolved
    environment clock exactly (the same equality the provider session builder
    enforces at materialization time).
    """
    if not isinstance(reader, BundleReader):
        raise TypeError("native parcel resolution requires a BundleReader")
    try:
        reader.validate_schema_bound_file(provider.config)
        document = reader.load_document(provider.config.file)
        config = NativeParcelBusinessConfig.model_validate(document)
    except (TypeError, ValueError, OSError) as exc:
        raise NativeParcelPackageResolutionError(
            "the native parcel business provider config pinned by the "
            f"resolved environment for {provider.provider_id!r} is not a "
            f"materializable native declaration: {exc}"
        ) from exc
    if config.provider_id != provider.provider_id:
        raise NativeParcelPackageResolutionError(
            "the declared native parcel config names provider "
            f"{config.provider_id!r} while the resolved environment declares "
            f"{provider.provider_id!r}"
        )
    session = config.native_parcel
    clock = environment.clock
    if session.step_ns != clock.step_ns or session.max_steps != clock.max_steps:
        raise NativeParcelPackageResolutionError(
            "the declared native parcel session run bound "
            f"(step_ns={session.step_ns}, max_steps={session.max_steps}) "
            "differs from the resolved environment clock "
            f"(step_ns={clock.step_ns}, max_steps={clock.max_steps})"
        )
    return config


def _package_difference_fields(
    declared: LogisticsTaskPackage,
    resolved: LogisticsTaskPackage,
) -> str:
    """Name the exact differing top-level fields of two lowered packages.

    Diagnostic only: the full canonical equality requirement in
    :func:`_require_config_binds_package` is unchanged.  This exists so a
    config-vs-resolved-package disagreement exposes its concrete fields
    instead of failing as an opaque inequality.
    """
    declared_dump = declared.model_dump(mode="json")
    resolved_dump = resolved.model_dump(mode="json")
    differing = sorted(
        key
        for key in set(declared_dump) | set(resolved_dump)
        if declared_dump.get(key) != resolved_dump.get(key)
    )
    details: list[str] = []
    for key in differing:
        declared_value = declared_dump.get(key)
        resolved_value = resolved_dump.get(key)
        if isinstance(declared_value, dict) and isinstance(resolved_value, dict):
            sub_fields = sorted(
                sub_key
                for sub_key in set(declared_value) | set(resolved_value)
                if declared_value.get(sub_key) != resolved_value.get(sub_key)
            )
            details.append(f"{key} ({', '.join(sub_fields)})" if sub_fields else key)
        elif (
            isinstance(declared_value, list)
            and isinstance(resolved_value, list)
            and declared_value
            and all(isinstance(item, dict) for item in declared_value)
        ):
            sub_fields: list[str] = []
            for index, item in enumerate(declared_value):
                if index >= len(resolved_value):
                    sub_fields.append(f"[{index}] missing in resolved")
                    break
                other = resolved_value[index]
                if isinstance(other, dict):
                    sub_fields.extend(
                        f"[{index}].{sub_key}"
                        for sub_key in sorted(set(item) | set(other))
                        if item.get(sub_key) != other.get(sub_key)
                    )
                elif other != item:
                    sub_fields.append(f"[{index}]")
            details.append(f"{key} ({', '.join(sub_fields)})" if sub_fields else key)
        else:
            details.append(key)
    return "; ".join(details)


def _require_config_binds_package(
    *,
    config: NativeParcelBusinessConfig,
    package: object,
) -> None:
    """Require full canonical equality between config package and resolved package.

    The native declaration binds ONE canonical order of ONE exact logistics
    package.  Identity-only agreement is not enough: orders, fleet,
    facilities, profiles, body dimensions and constraints must all be equal.
    On mismatch the error names the exact differing top-level package fields
    (diagnostic only; the equality requirement itself is unchanged).
    """
    if config.task_package != package:
        raise NativeParcelPackageResolutionError(
            "the declared native parcel business config does not bind the "
            "exact resolved logistics package (orders, fleet, facilities, "
            "profiles, body dimensions or constraints differ; identity-only "
            "agreement is not enough); differing top-level package fields: "
            f"{_package_difference_fields(config.task_package, package)}"
        )


def prove_declared_native_config_matches_resolved_run(
    *, run, reader: BundleReader,
    business_provider_id: str = NATIVE_BUSINESS_PROVIDER_ID,
) -> NativeParcelBusinessConfig:
    """Read-only native package/config proof shared by runtime and sealed verifier."""
    package = load_native_parcel_package(reader=reader, task=run.task)
    _validate_package_identity(package=package, task=run.task)
    _validate_scene_binding(package=package, scenario=run.scenario)
    provider = _resolved_native_provider(
        environment=run.environment, business_provider_id=business_provider_id,
    )
    config = _load_declared_native_config(
        reader=reader, provider=provider, environment=run.environment,
    )
    _require_config_binds_package(config=config, package=package)
    return config


class NativeParcelTaskPackageResolver:
    """Resolve the single-parcel declarations against available providers."""

    def __init__(self, *, business_provider_id: str = NATIVE_BUSINESS_PROVIDER_ID):
        if not isinstance(business_provider_id, str) or not business_provider_id:
            raise TypeError(
                "native parcel resolver requires the business provider id"
            )
        self._business_provider_id = business_provider_id

    @property
    def package_id(self) -> str:
        return NATIVE_PARCEL_PACKAGE_ID

    @property
    def runtime_available(self) -> bool:
        return True

    def scenario_projection(
        self,
        *,
        reader: BundleReader,
        task: TaskSpec,
        environment: EnvironmentSpec,
        agents: tuple[AgentSpec, ...],
    ) -> ResolvedTaskScenarioProjection:
        package = load_native_parcel_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task)
        provider = _resolved_native_provider(
            environment=environment,
            business_provider_id=self._business_provider_id,
        )
        config = _load_declared_native_config(
            reader=reader,
            provider=provider,
            environment=environment,
        )
        _require_config_binds_package(config=config, package=package)
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
        package = load_native_parcel_package(reader=reader, task=task)
        _validate_package_identity(package=package, task=task)
        _validate_scene_binding(package=package, scenario=scenario)
        provider = _resolved_native_provider(
            environment=environment,
            business_provider_id=self._business_provider_id,
        )
        config = _load_declared_native_config(
            reader=reader,
            provider=provider,
            environment=environment,
        )
        _require_config_binds_package(config=config, package=package)

        from aero_bench.providers.logistics_business.adapter import SUPPORTED_CAPABILITIES

        supplied = set(_physical_logistics_capabilities(environment))
        supplied.update(set(provider.capabilities) & (SUPPORTED_CAPABILITIES | {NATIVE_PARCEL_CAPABILITY}))
        required = NATIVE_PARCEL_REQUIRED_CAPABILITIES | set(task.required_capabilities)
        missing = required - supplied
        failed_conditions = tuple(sorted(missing))
        covered = any(unit.max_payload_kg >= package.orders[0].cargo_mass_kg
                      for unit in package.aircraft_units())
        return FeasibilityAssessment(
            package_id=NATIVE_PARCEL_PACKAGE_ID,
            feasible=not failed_conditions,
            success_upper_bound=float(covered) if not failed_conditions else 0.0,
            failed_conditions=failed_conditions,
            bounds=_assessment_bounds(package),
        )


class NativeParcelTaskRuntimeHookFactory:
    """Runtime-hook factory entry for the explicitly selected native slice.

    Zero-argument constructible for ``aero_bench.tasks.registry``.  ``create``
    proves the exact same strict chain the resolver proves (explicit native
    package, package identity, scene binding, explicit provider adapter and
    authority capability, digest-pinned native config, exact-package config
    binding, clock run bound), then constructs the real runtime hook from the
    same validated ingredients the base
    :class:`aero_bench.tasks.logistics.runtime_hook.LogisticsRuntimeHookFactory`
    tail uses: the strict runtime-binding validator over the declared
    observation bindings, and the wrapped
    :class:`aero_bench.tasks.logistics.native_parcel_hook.NativeParcelRuntimeHook`
    projection.  The base factory's own proof head pins the base
    ``logistics.task.v1`` package id, so the entry performs the config↔run
    proof itself and never routes a native run through that base head.  A
    foreign run (any other package id) is an explicit error, never a silently
    skipped hook.
    """

    def __init__(self, *, business_provider_id: str = NATIVE_BUSINESS_PROVIDER_ID):
        if not isinstance(business_provider_id, str) or not business_provider_id:
            raise TypeError(
                "native parcel runtime hook factory requires the business "
                "provider id"
            )
        self._business_provider_id = business_provider_id

    @property
    def package_id(self) -> str:
        return NATIVE_PARCEL_PACKAGE_ID

    def create(
        self,
        *,
        run,
        reader: BundleReader,
        providers,
        ledger,
        runtime_hook_time,
    ):
        config = prove_declared_native_config_matches_resolved_run(
            run=run, reader=reader, business_provider_id=self._business_provider_id,
        )
        package = config.task_package
        # The base proof's remaining ingredients, over the same declared
        # config the run pins: binding the strict runtime-binding validator to
        # the exact declared observation plan, and the business session
        # identity to the resolved provider sessions.
        if self._business_provider_id not in providers:
            raise LogisticsRuntimeHookError(
                "native parcel runtime hook cannot find the business provider "
                f"{self._business_provider_id!r} in the resolved provider "
                "sessions; the run is not materially bound to a native parcel "
                "business workload"
            )
        business = providers[self._business_provider_id]
        if not callable(getattr(business, "ingest_observations", None)):
            raise LogisticsRuntimeHookError(
                f"provider {self._business_provider_id!r} does not implement "
                "the private observation-ingest client surface"
            )
        from aero_bench.tasks.logistics.runtime_bindings import (
            validate_logistics_runtime_bindings,
        )
        from aero_bench.tasks.logistics.runtime_hook import LogisticsRuntimeHook

        validated = validate_logistics_runtime_bindings(
            bindings_document=config.observation.bindings.model_dump(mode="json"),
            reader=reader,
            environment=run.environment,
            agents=run.agents,
            package=config.task_package,
            scenario=run.scenario,
        )
        observation_hook = LogisticsRuntimeHook(
            run=run,
            reader=reader,
            providers=providers,
            ledger=ledger,
            runtime_hook_time=runtime_hook_time,
            package=package,
            bindings=validated,
            scenario=run.scenario,
            tolerances=config.observation.tolerances,
            observation_spec=config.observation.spec,
            pose_references=config.observation.pose_references,
            business_provider_id=self._business_provider_id,
        )
        return NativeParcelRuntimeHook(
            observation_hook=observation_hook,
            business=business,
            ledger=ledger,
        )


__all__ = [
    "NATIVE_BUSINESS_PROVIDER_ID",
    "NATIVE_PARCEL_PACKAGE_ID",
    "NATIVE_PARCEL_REQUIRED_CAPABILITIES",
    "NativeParcelPackageResolutionError",
    "NativeParcelTaskPackageResolver",
    "NativeParcelTaskRuntimeHookFactory",
]
