"""Concrete logistics RuntimeHook over closed Provider motion stages (WP1).

This module wires the *actual* ``RuntimeHook.on_stage_barriers_closed`` inputs
(the sealed motion ``aero-bench.scene-state/v1``, the closed-stage
``px4.state.v1`` ProviderEvents and the closed ``StageBarrier`` tuple at one
authoritative tick) through the accepted physical-observation adapter
(:func:`aero_bench.tasks.logistics.observation_ingress.derive_physical_observations`)
into the real Logistics Business provider workload over the private
``logistics.observation.ingest`` RPC operation.

The hook is deliberately NOT auto-enabled: nothing registers it in the generic
resolver/executor/harness registries and this file never flips
``LOGISTICS_RUNTIME_IMPLEMENTED``.  It exposes an explicit
:class:`LogisticsRuntimeHook` constructor and a
:class:`LogisticsRuntimeHookFactory` so a future native factory can pass the
authoritative declared business configuration (the digest-bound
``LogisticsBusinessConfig.observation`` declaration) and prove it against the
resolved run at the factory boundary.

Guarantees
----------

* Only closed physical motion evidence becomes an observation. Legitimate
  Gateway observations are acknowledged without creating physical presence;
  foreign or malformed input is rejected.
* ``expected_run_id`` and ``target`` are the run/tick the Harness is actually
  stepping; they are never inferred from the incoming closed batch.
* Every aircraft named by the observation plan must have an explicitly declared
  ``DeclaredAircraftPoseReference``; half body height is never inferred.
* Observations cross the actual Provider session wire (executed-issued session
  token included) and the provider workload atomically journals them; a
  foreign/stale/mismatched-config payload is rejected with zero mutation.
* Physical pickup/handoff/deliver/charge transitions are untouched and remain
  refused by the business workload until the later authoritative
  transition+dwell+sealed-verifier integration lands.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import NamedValue
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.contracts import ProviderSession
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.runtime.contracts import (
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.runtime.hooks import (
    RuntimeHook,
    RuntimeHookFactory,
    ValidatedObservation,
)
from aero_bench.runtime.ledger import EventLedger
from aero_bench.runtime.events import RunEventAudience
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.logistics.contracts import (
    LOGISTICS_PACKAGE_ID,
    LogisticsTaskPackage,
)
from aero_bench.tasks.logistics.facility_presence import PresenceTolerances
from aero_bench.tasks.logistics.integration import load_logistics_package
from aero_bench.tasks.logistics.observation_ingress import (
    LOGISTICS_OBSERVATION_INGEST_OPERATION,
    LogisticsObservationIngestClient,
    LogisticsObservationSpec,
    ObservationIngestResult,
    build_observation_batch,
    derive_physical_observations,
)
from aero_bench.tasks.logistics.physical_observations import (
    DeclaredAircraftPoseReference,
)
from aero_bench.tasks.logistics.runtime_bindings import (
    LogisticsRuntimeBindings,
    validate_logistics_runtime_bindings,
)
from aero_bench.tasks.logistics.runtime_airspace import ClosedMotionAirspaceTracker
from aero_bench.world.resolved import ResolvedScenario


class LogisticsRuntimeHookError(ValueError):
    """A strict logistics runtime-hook violation."""


class LogisticsRuntimeHook(RuntimeHook):
    """Real hook: closed motion stage minutes the business observation journal.

    The hook is constructed only with the immutable resolved run, the validated
    package/bindings/scenario, explicit pose-reference calibrations, the
    declared observation plan and the real business provider session.  It never
    invents providers, evidence, movement, yaw, landed state or a pose
    reference.
    """

    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        reader: BundleReader,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
        package: LogisticsTaskPackage,
        bindings: LogisticsRuntimeBindings,
        scenario: ResolvedScenario,
        tolerances: PresenceTolerances,
        observation_spec: LogisticsObservationSpec,
        pose_references: tuple[DeclaredAircraftPoseReference, ...],
        business_provider_id: str = "logistics.business",
    ):
        if not isinstance(run, ResolvedRunSpec):
            raise TypeError("logistics runtime hook requires a ResolvedRunSpec")
        if not isinstance(reader, BundleReader):
            raise TypeError("logistics runtime hook requires a BundleReader")
        if not isinstance(ledger, EventLedger):
            raise TypeError("logistics runtime hook requires an EventLedger")
        if not callable(runtime_hook_time):
            raise TypeError("logistics runtime hook requires the runtime hook time")
        if not isinstance(package, LogisticsTaskPackage):
            raise TypeError("logistics runtime hook requires a LogisticsTaskPackage")
        if not isinstance(bindings, LogisticsRuntimeBindings):
            raise TypeError(
                "logistics runtime hook requires validated LogisticsRuntimeBindings"
            )
        if not isinstance(scenario, ResolvedScenario):
            raise TypeError("logistics runtime hook requires a ResolvedScenario")
        if not isinstance(tolerances, PresenceTolerances):
            raise TypeError("logistics runtime hook requires PresenceTolerances")
        if not isinstance(observation_spec, LogisticsObservationSpec):
            raise TypeError(
                "logistics runtime hook requires a LogisticsObservationSpec"
            )
        if not isinstance(business_provider_id, str) or not business_provider_id:
            raise TypeError(
                "logistics runtime hook requires the business provider id"
            )
        if business_provider_id not in providers:
            raise LogisticsRuntimeHookError(
                f"logistics runtime hook cannot find the business provider "
                f"{business_provider_id!r} in the resolved provider sessions; the "
                "run is not materially bound to a logistics business workload"
            )
        business = providers[business_provider_id]
        if not callable(getattr(business, "ingest_observations", None)):
            raise LogisticsRuntimeHookError(
                f"provider {business_provider_id!r} does not implement the private "
                "observation-ingest client surface"
            )
        self._run = run
        self._reader = reader
        self._providers = providers
        self._ledger = ledger
        self._runtime_hook_time = runtime_hook_time
        self._package = package
        self._bindings = bindings
        self._scenario = scenario
        self._tolerances = tolerances
        self._observation_spec = observation_spec
        self._pose_references = pose_references
        self._business_provider_id = business_provider_id
        self._business: LogisticsObservationIngestClient = cast(
            LogisticsObservationIngestClient, business
        )
        self._airspace = ClosedMotionAirspaceTracker(
            run_id=run.run_id, scenario_digest=scenario.scenario_digest,
            step_ns=run.environment.clock.step_ns, package=package, bindings=bindings,
        )

    async def on_validated_observation(
        self, observation: ValidatedObservation
    ) -> None:
        """Acknowledge a legitimate native validated observation without minting presence.

        ``on_validated_observation`` is a *real* generic runtime callback: it also
        fires for legitimate camera/provider observations the Harness Gateway
        validated.  It is **not** a pose-injection API and it never minted
        physical presence.  A well-formed native
        :class:`~aero_bench.runtime.hooks.ValidatedObservation` for this run and
        the authoritative runtime-hook time is acknowledged (the Gateway keeps
        its legal camera observation flow intact) and no business journal or
        ledger event is produced.

        Malformed or foreign input remains an explicit error: an observation
        that is not a ``ValidatedObservation``, that belongs to another run, or
        that is not at the authoritative runtime-hook time is rejected so a
        spoofed or wrong-context camera frame can never be credited.
        """
        if not isinstance(observation, ValidatedObservation):
            raise LogisticsRuntimeHookError(
                "logistics runtime hook requires a typed ValidatedObservation "
                "from the Harness Gateway; a raw object cannot be acknowledged"
            )
        if observation.run_id != self._run.run_id:
            raise LogisticsRuntimeHookError(
                "validated observation belongs to another run"
            )
        if observation.time != self._runtime_hook_time():
            raise LogisticsRuntimeHookError(
                "validated observation time differs from the authoritative "
                "runtime hook time"
            )

    async def on_stage_barriers_closed(
        self,
        events: tuple[ProviderEvent, ...],
        *,
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
        target: SimulationTime,
    ) -> None:
        if not isinstance(events, tuple) or any(
            not isinstance(event, ProviderEvent) for event in events
        ):
            raise TypeError("logistics runtime hook requires ProviderEvents")
        if not isinstance(scene_state, SceneState):
            raise TypeError("logistics runtime hook requires a SceneState")
        if not isinstance(stage_barriers, tuple) or any(
            not isinstance(barrier, StageBarrier) for barrier in stage_barriers
        ):
            raise TypeError("logistics runtime hook requires StageBarriers")
        if not isinstance(target, SimulationTime):
            raise TypeError("logistics runtime hook requires the target time")
        if self._runtime_hook_time() != target:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook target differs from the Harness runtime "
                "hook time; the closed stage is not the authoritative stage"
            )
        if target != scene_state.at or target != scene_state.stage_barrier.at:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook target differs from the closed SceneState"
            )
        if scene_state.run_id != self._run.run_id:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook SceneState belongs to another run"
            )

        observations = derive_physical_observations(
            scene_state=scene_state,
            events=events,
            package=self._package,
            bindings=self._bindings,
            scenario=self._scenario,
            spec=self._observation_spec,
            pose_references=self._pose_references,
            tolerances=self._tolerances,
            stage_barriers=stage_barriers,
            expected_run_id=self._run.run_id,
            target=target,
        )
        batch = build_observation_batch(
            observations=observations,
            run_id=self._run.run_id,
            scenario_digest=scene_state.scenario_digest,
            at=target,
            source_scene_state_digest=scene_state.scene_state_digest,
            source_stage_barrier_digest=scene_state.stage_barrier.barrier_digest,
        )
        airspace = self._airspace.prepare(batch)
        result = await self._business.ingest_observations(batch)
        self._airspace.commit(airspace)
        self._record_ingested(result, at=target)
        for segment in airspace.segments:
            self._ledger.append_event(
                source="harness", source_kind="harness", event_type="logistics.airspace.segment.v1",
                time=target, visibility=(RunEventAudience(scope="private"), RunEventAudience(scope="verifier")),
                payload=(
                    NamedValue(name="segment_digest", value=segment.canonical_digest()),
                    NamedValue(name="segment_json", value=canonical_json_bytes(segment.model_dump(mode="json")).decode("utf-8")),
                ),
            )

    def _record_ingested(
        self, result: ObservationIngestResult, *, at: SimulationTime
    ) -> None:
        self._ledger.append_event(
            source=self._business_provider_id,
            event_type="logistics.observation.ingested",
            time=at,
            payload=(
                NamedValue(name="run_id", value=result.run_id),
                NamedValue(name="provider_id", value=result.provider_id),
                NamedValue(name="journal_digest", value=result.journal_digest),
                NamedValue(name="sequence_count", value=result.sequence_count),
                NamedValue(name="replayed", value=result.replayed),
            ),
            provider_id=self._business_provider_id,
        )


class LogisticsRuntimeHookFactory(RuntimeHookFactory):
    """Explicit factory usable by a native logistics factory later.

    ``package_id`` is the canonical logistics package id so that the generic
    :func:`aero_bench.runtime.hooks.resolve_runtime_hook` would select it for a
    logistics task package.  The factory itself is NOT registered anywhere, and
    this module never claims ``logistics.runtime_available``.

    The factory is constructed ONLY with the declared business configuration
    (the digest-bound ``LogisticsBusinessConfig`` whose ``observation`` branch
    pins the native bindings, pose-reference calibrations, tolerances and
    observation plan in the provider config file).  No package, bindings,
    calibration, tolerance, plan or scenario is injected independently: every
    one of those comes from the declared config and the resolved run.

    At :meth:`create` the factory **proves** the declared config matches the
    resolved run through the real digest-verified BundleReader contract: the
    run-pinned logistics business provider ``SchemaBoundFile`` is
    validated/loaded and its typed config must equal the declared config, the
    provider identity + adapter must match, and the authoritative
    ``run.task.package`` loaded/lowered via :func:`load_logistics_package` must
    equal the declared config's nested ``task_package`` with FULL canonical
    equality (orders, fleet, performance profiles, body dimensions,
    constraints — identity-only agreement is not enough).  It then re-runs the
    real strict
    :func:`~aero_bench.tasks.logistics.runtime_bindings.validate_logistics_runtime_bindings`
    against the resolved run's BundleReader, environment, agents, package and
    scenario — it never trusts an ``isinstance`` on a binding object.  A run
    whose declared business config disables observation ingress, a config that
    does not match the resolved run, a task package that diverges from the
    factory's nested task package, or bindings that fail the strict native
    validation cannot produce a hook.
    """

    def __init__(
        self,
        *,
        config: LogisticsBusinessConfig,
        business_provider_id: str = "logistics.business",
    ):
        if not isinstance(config, LogisticsBusinessConfig):
            raise TypeError(
                "logistics runtime hook factory requires the declared "
                "LogisticsBusinessConfig"
            )
        self._config = config
        self._business_provider_id = business_provider_id

    @property
    def package_id(self) -> str:
        return LOGISTICS_PACKAGE_ID

    def _prove_declared_config_matches_resolved_run(
        self, run: ResolvedRunSpec, reader: BundleReader
    ) -> None:
        """Bind the factory's declared config to the resolved run exactly.

        ``self._config`` must be THE config document the resolved run pins as
        the logistics business provider config file.  The proof uses the real
        digest-verified :class:`BundleReader` contract — never guessed
        canonical/newline hashes:

        * the run environment's declared logistics business ``ProviderRef``
          must exist with the matching provider identity and adapter;
        * ``reader.validate_schema_bound_file(provider.config)`` digests the
          declared config + schema file bytes against the run-pinned
          ``FileRef``s and validates the instance under the declared schema;
        * the typed ``LogisticsBusinessConfig`` loaded from those bytes must
          equal ``self._config`` exactly;
        * the authoritative ``run.task.package`` is loaded/lowered through the
          existing ``load_logistics_package(reader, task)`` and must equal the
          factory config's nested ``task_package`` with FULL canonical equality
          (orders, fleet, performance profiles, body dimensions, constraints —
          identity-only agreement is not enough);
        * the loaded package's scene must bind the resolved run scenario world.
        """
        if not isinstance(reader, BundleReader):
            raise TypeError(
                "logistics runtime hook factory requires a BundleReader"
            )
        config = self._config
        if config.task_package.package_id != LOGISTICS_PACKAGE_ID:
            raise LogisticsRuntimeHookError(
                f"logistics runtime hook factory config is not a logistics "
                f"package: {config.task_package.package_id!r}"
            )
        providers = {
            provider.provider_id: provider for provider in run.environment.providers
        }
        provider = providers.get(self._business_provider_id)
        if provider is None:
            raise LogisticsRuntimeHookError(
                f"the resolved run environment does not declare the logistics "
                f"business provider {self._business_provider_id!r}"
            )
        if provider.adapter != self._business_provider_id:
            raise LogisticsRuntimeHookError(
                f"the resolved run declares provider {self._business_provider_id!r} "
                f"with adapter {provider.adapter!r}; the logistics business "
                "native factory requires the provider identity AND adapter to "
                "match the declared business provider"
            )

        # 1. Digest-verified provider config/schema bytes.  The BundleReader
        # resolves the declared SchemaBoundFile paths and verifies each file's
        # actual sha256 against the run-pinned FileRef (a tampered or absent
        # file, or a guessed digest, fails here), then validates the instance
        # under the declared schema.
        try:
            reader.validate_schema_bound_file(provider.config)
            loaded_raw = reader.load_document(provider.config.file)
            loaded_config = LogisticsBusinessConfig.model_validate(loaded_raw)
        except (TypeError, ValueError, OSError) as exc:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook factory cannot digest-verify/type the "
                "declared provider config file the resolved run pins for "
                f"{self._business_provider_id!r}: {exc}"
            ) from exc
        if loaded_config != config:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook factory config does not equal the "
                "declared provider config file the resolved run pins for "
                f"{self._business_provider_id!r}; the typed loaded config must "
                "match the requested factory config exactly"
            )

        # 2. Authoritative run task package.  Load/lower the actual
        # TaskSpec package from the bundle and require full canonical equality
        # with the factory config's nested task_package.
        try:
            loaded_package = load_logistics_package(reader=reader, task=run.task)
        except (TypeError, ValueError, OSError) as exc:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook factory cannot load the resolved run "
                f"task package from the bundle: {exc}"
            ) from exc
        if loaded_package != config.task_package:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook factory task_package does not equal the "
                "resolved run task package loaded from the bundle (orders, "
                "fleet, performance profiles, body dimensions or constraints "
                "differ; identity-only agreement is not enough)"
            )
        if loaded_package.scene.scene_id != run.scenario.world_id:
            raise LogisticsRuntimeHookError(
                "logistics runtime hook factory package scene does not match "
                "the resolved run scenario world"
            )

    def create(
        self,
        *,
        run: ResolvedRunSpec,
        reader: BundleReader,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
    ) -> RuntimeHook:
        config = self._config
        if config.observation is None:
            raise LogisticsRuntimeHookError(
                "logistics observation runtime hook cannot be created for a run "
                "whose declared business config disables observation ingress "
                "(observation=null)"
            )
        self._prove_declared_config_matches_resolved_run(run, reader)
        # Reuse the real strict validator at the native factory boundary.  The
        # declared bindings must validate against the actual resolved run
        # environment/agents/scenario/package; an isinstance check alone is
        # never enough.
        validated = validate_logistics_runtime_bindings(
            bindings_document=config.observation.bindings.model_dump(mode="json"),
            reader=reader,
            environment=run.environment,
            agents=run.agents,
            package=config.task_package,
            scenario=run.scenario,
        )
        return LogisticsRuntimeHook(
            run=run,
            reader=reader,
            providers=providers,
            ledger=ledger,
            runtime_hook_time=runtime_hook_time,
            package=config.task_package,
            bindings=validated,
            scenario=run.scenario,
            tolerances=config.observation.tolerances,
            observation_spec=config.observation.spec,
            pose_references=config.observation.pose_references,
            business_provider_id=self._business_provider_id,
        )


__all__ = [
    "LOGISTICS_OBSERVATION_INGEST_OPERATION",
    "LogisticsRuntimeHook",
    "LogisticsRuntimeHookError",
    "LogisticsRuntimeHookFactory",
]
