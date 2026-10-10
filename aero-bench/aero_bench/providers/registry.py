from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar, cast

from pydantic import Field, StrictInt, StrictStr, TypeAdapter

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import (
    ClockSpec,
    Identifier,
    ProviderRef,
    Sha256,
    StrictModel,
)
from aero_bench.providers.contracts import ProviderManifest, ProviderSession
from aero_bench.providers.stages import ProviderStage, parse_runtime_stage
from aero_bench.world.resolved import ResolvedProvider, ResolvedScenario


ConfigModelT = TypeVar("ConfigModelT", bound=StrictModel)

_FORBIDDEN_CONFIG_FIELDS = frozenset(
    {
        "runtime_image",
        "endpoint",
        "port",
        "protocol",
        "protocol_version",
        "deployment",
    }
)


class ProviderRegistryError(ValueError):
    """A Provider registration or materialization contract violation."""


class RuntimeEndpoint(StrictModel):
    """The materializer-owned endpoint for a Provider workload."""

    host: Annotated[StrictStr, Field(min_length=1, pattern=r"^[A-Za-z0-9_.-]+$")]
    port: Annotated[StrictInt, Field(ge=1024, le=65535)]


SessionBuilder = Callable[
    [
        ConfigModelT,
        ProviderManifest,
        RuntimeEndpoint,
        str,
        str,
        ResolvedScenario,
        ClockSpec,
    ],
    ProviderSession,
]


@dataclass(frozen=True, slots=True)
class ProviderRegistration(Generic[ConfigModelT]):
    """One explicit adapter/config/session/runtime-stage binding.

    ``runtime_stage`` is a required strict scalar and the single authority for
    where the adapter closes inside a tick. It is never inferred from roles,
    capabilities, or adapter naming, and there is no unknown-adapter fallback.
    """

    adapter: str
    runtime_stage: ProviderStage
    config_model: type[ConfigModelT]
    session_builder: SessionBuilder[ConfigModelT]

    def __post_init__(self) -> None:
        try:
            TypeAdapter(Identifier).validate_python(self.adapter)
        except (TypeError, ValueError) as error:
            raise ProviderRegistryError(
                "provider registration adapter is invalid"
            ) from error
        try:
            parse_runtime_stage(self.runtime_stage)
        except ValueError as error:
            raise ProviderRegistryError(
                "provider registration runtime_stage is invalid"
            ) from error
        if not isinstance(self.config_model, type) or not issubclass(
            self.config_model, StrictModel
        ):
            raise ProviderRegistryError(
                "provider registration config_model must subclass StrictModel"
            )
        if self.config_model is StrictModel:
            raise ProviderRegistryError(
                "provider registration config_model must be concrete"
            )
        model_config = self.config_model.model_config
        if (
            model_config.get("extra") != "forbid"
            or model_config.get("frozen") is not True
            or model_config.get("strict") is not True
        ):
            raise ProviderRegistryError(
                "provider registration config_model must be strict, frozen, and extra-forbid"
            )
        if "provider_id" not in self.config_model.model_fields:
            raise ProviderRegistryError(
                "provider registration config_model must declare provider_id"
            )
        forbidden = _FORBIDDEN_CONFIG_FIELDS.intersection(
            self.config_model.model_fields
        )
        if forbidden:
            raise ProviderRegistryError(
                "provider registration config_model must not declare runtime_image, "
                "endpoint, port, protocol, or deployment fields"
            )
        if not callable(self.session_builder):
            raise ProviderRegistryError(
                "provider registration session_builder must be callable"
            )


class ProviderRegistry:
    """Resolve declared Provider adapters without implicit substitutions."""

    def __init__(
        self,
        registrations: Iterable[ProviderRegistration[StrictModel]] = (),
    ) -> None:
        self._registrations: dict[str, ProviderRegistration[StrictModel]] = {}
        for registration in registrations:
            self.register(registration)

    @property
    def adapters(self) -> tuple[str, ...]:
        return tuple(sorted(self._registrations))

    def runtime_stage_for(self, adapter: str) -> ProviderStage:
        """Return the registered runtime stage for one exact adapter.

        Unknown adapters fail here. There is no fallback and no inference from
        roles, capabilities, or adapter naming.
        """
        registration = self._registrations.get(adapter)
        if registration is None:
            raise ProviderRegistryError(
                f"no Provider adapter is registered: {adapter}"
            )
        return registration.runtime_stage

    def runtime_stages(self) -> dict[str, ProviderStage]:
        """Project every registered adapter to its runtime stage."""
        return {
            adapter: registration.runtime_stage
            for adapter, registration in sorted(self._registrations.items())
        }

    def verify_runtime_stage_binding(
        self,
        *,
        environment_providers: Sequence[ProviderRef],
        resolved_providers: Sequence["ResolvedProvider"],
    ) -> None:
        """Compare ``adapter -> registered stage -> serialized stage`` before any side effect.

        For each declared Provider this checks that the adapter is registered,
        that its registered stage equals the frozen
        ``ResolvedProvider.runtime_stage``, and that the declared and resolved
        Provider sets match exactly. Missing registrations, extra Providers, and
        stage mismatches fail here.
        """
        env_by_id = {item.provider_id: item for item in environment_providers}
        res_by_id = {item.provider_id: item for item in resolved_providers}
        if len(env_by_id) != len(environment_providers) or len(res_by_id) != len(
            resolved_providers
        ):
            raise ProviderRegistryError("Provider stage binding contains duplicate IDs")
        if set(env_by_id) != set(res_by_id):
            raise ProviderRegistryError(
                "Provider set drift between environment and scenario: "
                f"environment={sorted(env_by_id)}, resolved={sorted(res_by_id)}"
            )
        for provider_id in sorted(env_by_id):
            adapter = env_by_id[provider_id].adapter
            registered = self.runtime_stage_for(adapter)
            serialized = res_by_id[provider_id].runtime_stage
            if registered != serialized:
                raise ProviderRegistryError(
                    f"Provider {provider_id} runtime stage drift: registered "
                    f"{registered!r} != serialized {serialized!r}"
                )

    def register(self, registration: ProviderRegistration[ConfigModelT]) -> None:
        if not isinstance(registration, ProviderRegistration):
            raise ProviderRegistryError(
                "provider registry accepts ProviderRegistration values only"
            )
        if registration.adapter in self._registrations:
            raise ProviderRegistryError(
                f"provider adapter is already registered: {registration.adapter}"
            )
        self._registrations[registration.adapter] = cast(
            ProviderRegistration[StrictModel], registration
        )

    def build_session(
        self,
        *,
        provider: ProviderRef,
        bundle: BundleReader,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ) -> ProviderSession:
        if not isinstance(provider, ProviderRef):
            raise ProviderRegistryError("provider must be a ProviderRef")
        try:
            validated_provider = ProviderRef.model_validate(
                provider.model_dump(mode="json")
            )
        except (TypeError, ValueError) as error:
            raise ProviderRegistryError(
                "ProviderRef failed strict revalidation"
            ) from error
        if validated_provider != provider:
            raise ProviderRegistryError("ProviderRef was not canonically serialized")
        if not isinstance(runtime_endpoint, RuntimeEndpoint):
            raise ProviderRegistryError("runtime_endpoint must be a RuntimeEndpoint")
        if not isinstance(bundle, BundleReader):
            raise ProviderRegistryError("bundle must be a BundleReader")
        if not isinstance(scenario, ResolvedScenario):
            raise ProviderRegistryError("scenario must be a ResolvedScenario")
        if not isinstance(clock, ClockSpec):
            raise ProviderRegistryError("clock must be a ClockSpec")
        scenario_provider = next(
            (
                item
                for item in scenario.providers
                if item.provider_id == provider.provider_id
            ),
            None,
        )
        if (
            scenario_provider is None
            or scenario_provider.capability_ids
            != tuple(sorted(provider.capabilities))
        ):
            raise ProviderRegistryError(
                "ProviderRef differs from its ResolvedScenario provider projection"
            )
        try:
            validated_run_id = TypeAdapter(Sha256).validate_python(run_id)
        except (TypeError, ValueError) as error:
            raise ProviderRegistryError("run_id must be a SHA-256 digest") from error
        if validated_run_id == "0" * 64:
            raise ProviderRegistryError("run_id cannot be a placeholder digest")
        try:
            validated_credential = TypeAdapter(Sha256).validate_python(credential)
        except (TypeError, ValueError) as error:
            raise ProviderRegistryError(
                "provider credential must be a SHA-256 digest"
            ) from error
        if validated_credential == "0" * 64:
            raise ProviderRegistryError(
                "provider credential cannot be a placeholder digest"
            )
        registration = self._registrations.get(provider.adapter)
        if registration is None:
            raise ProviderRegistryError(
                f"no Provider adapter is registered: {provider.adapter}"
            )
        if scenario_provider.runtime_stage != registration.runtime_stage:
            raise ProviderRegistryError("Provider runtime stage differs from its registry")
        if runtime_endpoint.port != provider.port:
            raise ProviderRegistryError(
                "runtime endpoint port does not match ProviderRef.port"
            )

        raw_config = bundle.validate_schema_bound_file(provider.config)
        try:
            config = registration.config_model.model_validate(raw_config)
        except (TypeError, ValueError) as error:
            raise ProviderRegistryError(
                f"Provider config does not match adapter {provider.adapter}: "
                f"{provider.config.file.path}"
            ) from error

        self._validate_config_identity(config, provider)
        manifest = self._manifest_for(provider)
        try:
            session = registration.session_builder(
                config,
                manifest,
                runtime_endpoint,
                validated_run_id,
                validated_credential,
                scenario,
                clock,
            )
        except Exception as error:
            raise ProviderRegistryError(
                f"Provider session builder failed for {provider.provider_id}"
            ) from error
        self._validate_session(session, manifest, provider.provider_id)
        return session

    @staticmethod
    def _validate_config_identity(
        config: StrictModel,
        provider: ProviderRef,
    ) -> None:
        dumped = config.model_dump()
        if dumped.get("provider_id") != provider.provider_id:
            raise ProviderRegistryError(
                "Provider config provider_id does not match ProviderRef"
            )

    @staticmethod
    def _manifest_for(provider: ProviderRef) -> ProviderManifest:
        return ProviderManifest(
            provider_id=provider.provider_id,
            adapter=provider.adapter,
            implementation=provider.workload.implementation,
            runtime_image=provider.workload.runtime.image,
            config_digest=provider.config.file.sha256,
            capabilities=provider.capabilities,
            protocol_schema=provider.protocol_schema,
            artifact_requirements=provider.artifact_requirements,
        )

    @staticmethod
    def _validate_session(
        session: ProviderSession,
        manifest: ProviderManifest,
        provider_id: str,
    ) -> None:
        session_manifest = getattr(session, "manifest", None)
        if not isinstance(session_manifest, ProviderManifest):
            raise ProviderRegistryError(
                f"Provider session for {provider_id} did not expose ProviderManifest"
            )
        if session_manifest != manifest:
            raise ProviderRegistryError(
                f"Provider session manifest does not match {provider_id} declaration"
            )
        required_methods = (
            "prepare",
            "reset",
            "step_stage",
            "handle_command",
            "finalize",
            "snapshot_digest",
            "shutdown",
        )
        if any(
            not callable(getattr(session, method, None)) for method in required_methods
        ):
            raise ProviderRegistryError(
                f"Provider session for {provider_id} does not implement ProviderSession"
            )


def builtin_provider_registry() -> ProviderRegistry:
    """Create the production registry for implemented external Providers.

    The registry intentionally names only Providers with a real external runtime
    authority. Compiler-owned static scene declarations are projected through
    ``ResolvedScenario`` and have no registered world-scene Provider authority.
    Each builder receives the materializer-owned endpoint, resolved run ID,
    and executor-issued session credential. The digest-bound configuration
    remains unchanged and is never used as a deployment connection target.
    """

    from aero_bench.providers.inspection_business import (
        InspectionBusinessConfig,
        InspectionBusinessProvider,
    )
    from aero_bench.providers.logistics_business import LogisticsBusinessConfig
    from aero_bench.providers.logistics_business.adapter import (
        build_logistics_business_session,
    )
    from aero_bench.providers.ns3 import Ns3Config, Ns3Provider
    from aero_bench.providers.px4_gazebo import Px4GazeboConfig, Px4GazeboProvider
    from aero_bench.providers.sumo import SumoConfig, SumoProvider

    def build_ns3(
        config: Ns3Config,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ) -> ProviderSession:
        return Ns3Provider(
            config=config,
            manifest=manifest,
            runtime_endpoint=runtime_endpoint,
            run_id=run_id,
            session_token=credential,
            scenario=scenario,
        )

    def build_px4(
        config: Px4GazeboConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ) -> ProviderSession:
        return Px4GazeboProvider(
            config=config,
            manifest=manifest,
            runtime_endpoint=runtime_endpoint,
            run_id=run_id,
            session_token=credential,
            scenario=scenario,
            clock=clock,
        )

    def build_sumo(
        config: SumoConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ) -> ProviderSession:
        return SumoProvider(
            config=config,
            manifest=manifest,
            runtime_endpoint=runtime_endpoint,
            run_id=run_id,
            session_token=credential,
            scenario=scenario,
            clock=clock,
        )

    def build_inspection_business(
        config: InspectionBusinessConfig,
        manifest: ProviderManifest,
        runtime_endpoint: RuntimeEndpoint,
        run_id: str,
        credential: str,
        scenario: ResolvedScenario,
        clock: ClockSpec,
    ) -> ProviderSession:
        return InspectionBusinessProvider(
            config=config,
            manifest=manifest,
            runtime_endpoint=runtime_endpoint,
            run_id=run_id,
            session_token=credential,
            scenario=scenario,
        )

    return ProviderRegistry(
        (
            ProviderRegistration(
                adapter="inspection.business",
                runtime_stage="business_environment",
                config_model=InspectionBusinessConfig,
                session_builder=build_inspection_business,
            ),
            ProviderRegistration(
                adapter="logistics.business",
                runtime_stage="business_environment",
                config_model=LogisticsBusinessConfig,
                session_builder=build_logistics_business_session,
            ),
            ProviderRegistration(
                adapter="ns3.rpc",
                runtime_stage="network",
                config_model=Ns3Config,
                session_builder=build_ns3,
            ),
            ProviderRegistration(
                adapter="px4.gazebo",
                runtime_stage="motion",
                config_model=Px4GazeboConfig,
                session_builder=build_px4,
            ),
            ProviderRegistration(
                adapter="sumo.traci",
                runtime_stage="motion",
                config_model=SumoConfig,
                session_builder=build_sumo,
            ),
        )
    )


__all__ = [
    "ProviderRegistration",
    "ProviderRegistry",
    "ProviderRegistryError",
    "RuntimeEndpoint",
    "SessionBuilder",
    "builtin_provider_registry",
]
