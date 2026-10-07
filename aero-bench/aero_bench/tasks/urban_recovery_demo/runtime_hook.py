"""Runtime ownership and exact-horizon checks for urban recovery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import TypeVar

from pydantic import ValidationError

from aero_bench.config.loader import BundleReader
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.contracts import ProviderSession
from aero_bench.runtime.contracts import (
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.runtime.hooks import RuntimeHook, ValidatedObservation
from aero_bench.runtime.ledger import EventLedger
from aero_bench.serialization import canonical_json_bytes
from aero_bench.tasks.urban_recovery_demo.contracts import (
    PACKAGE_ID,
    STEP_NS,
    AirspaceTransition,
    DemoTaskPackage,
)
from aero_bench.tasks.urban_recovery_demo.integration import (
    UrbanRecoveryResolutionError,
    UrbanRecoveryTaskPackageResolver,
    load_urban_recovery_package,
)


class UrbanRecoveryRuntimeHookError(RuntimeError):
    """A runtime observation or barrier violates the urban demo contract."""


_ContractModel = TypeVar("_ContractModel")
_STAGE_PROVIDER_FIELD = {
    "motion": "flight_provider_id",
    "network": "network_provider_id",
    "business_environment": "traffic_provider_id",
}
_AIRSPACE_TRANSITION_SCHEMA = "gazebo.airspace-transition.v1"


class UrbanRecoveryRuntimeHook(RuntimeHook):
    """Validate the provider-only runtime projection without synthesizing evidence.

    Flight, network, and traffic behavior remains authoritative in their external
    Providers. This hook only checks ownership, causal stage closure, and the
    exact logical horizon; it never injects a message, telemetry sample, or route.
    """

    def __init__(
        self,
        *,
        run: ResolvedRunSpec,
        package: DemoTaskPackage,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
    ) -> None:
        if run.task.package.package_id != PACKAGE_ID or package.package_id != PACKAGE_ID:
            raise UrbanRecoveryRuntimeHookError("runtime hook received another package")
        if package.execution_profile == "engineering" and run.execution_scope != "executor_validation":
            raise UrbanRecoveryRuntimeHookError("engineering runs must remain unscored executor_validation")
        if not callable(runtime_hook_time):
            raise TypeError("urban recovery runtime hook time callback must be callable")
        if not isinstance(ledger, EventLedger):
            raise TypeError("urban recovery runtime hook requires EventLedger")
        required_provider_ids = {
            package.flight_provider_id,
            package.network_provider_id,
            package.traffic_provider_id,
        }
        if set(providers) != required_provider_ids:
            raise UrbanRecoveryRuntimeHookError(
                "urban recovery runtime providers do not close over the package"
            )
        if run.scenario.task.package_id != PACKAGE_ID:
            raise UrbanRecoveryRuntimeHookError(
                "ResolvedScenario is not bound to the urban recovery package"
            )

        expected_observations: dict[tuple[str, str], str] = {}
        for binding in run.scenario.task.observations:
            key = (binding.agent_id, binding.observation_id)
            if key in expected_observations:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery observation binding is duplicated"
                )
            expected_observations[key] = binding.endpoint_id
        roles = {role.agent_id: role for role in package.roles}
        for agent in run.agents:
            if agent.agent_id not in roles:
                raise UrbanRecoveryRuntimeHookError(
                    f"urban recovery Agent is not declared by the package: {agent.agent_id}"
                )

        self._run_id = run.run_id
        self._scenario_digest = run.scenario.scenario_digest
        self._world_digest = run.scenario.world_digest
        self._airspace_origin_ns: int | None = None
        self._package = package
        self._providers = frozenset(required_provider_ids)
        self._roles = roles
        self._observations = expected_observations
        self._runtime_hook_time = runtime_hook_time
        self._last_tick = 0
        self._airspace_transitions: list[AirspaceTransition] = []
        self._airspace_sequences: set[int] = set()
        self._lock = asyncio.Lock()

    async def on_validated_observation(self, observation: ValidatedObservation) -> None:
        async with self._lock:
            self._require_time(observation.time)
            if observation.run_id != self._run_id:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery observation belongs to another run"
                )
            role = self._roles.get(observation.agent_id)
            if role is None:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery observation came from an undeclared Agent"
                )
            expected_provider = self._observations.get(
                (observation.agent_id, observation.observation_id)
            )
            if expected_provider is None or expected_provider != observation.provider_id:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery observation is not an authorized binding"
                )
            if role.role == "groundstation":
                if observation.observation_id != role.mailbox_observation_id:
                    raise UrbanRecoveryRuntimeHookError(
                        "groundstation may observe only its declared ns-3 mailbox"
                    )
                if observation.provider_id != self._package.network_provider_id:
                    raise UrbanRecoveryRuntimeHookError(
                        "groundstation mailbox must come from ns-3"
                    )
                return

            uav_observation_ids = {
                role.telemetry_observation_id,
                role.safety_observation_id,
            }
            if observation.observation_id in uav_observation_ids:
                if observation.provider_id != self._package.flight_provider_id:
                    raise UrbanRecoveryRuntimeHookError(
                        "UAV telemetry and safety observations must come from PX4"
                    )
            elif observation.observation_id == role.mailbox_observation_id:
                if observation.provider_id != self._package.network_provider_id:
                    raise UrbanRecoveryRuntimeHookError(
                        "UAV mailbox must come from ns-3"
                    )
            else:
                raise UrbanRecoveryRuntimeHookError(
                    "UAV observed an undeclared task observation"
                )

    async def on_stage_barriers_closed(
        self,
        events: tuple[ProviderEvent, ...],
        *,
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
        target: SimulationTime,
    ) -> None:
        async with self._lock:
            self._require_time(target)
            canonical_target = self._canonical(
                target, SimulationTime, "urban recovery target"
            )
            if canonical_target.tick != self._last_tick + 1:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery barriers must advance by exactly one tick"
                )
            if canonical_target.tick > self._package.final_tick or canonical_target.sim_time_ns != canonical_target.tick * STEP_NS:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery barrier is outside its declared horizon"
                )
            canonical_scene = self._canonical(scene_state, SceneState, "SceneState")
            if (
                canonical_scene.run_id != self._run_id
                or canonical_scene.scenario_digest != self._scenario_digest
                or canonical_scene.at != canonical_target
            ):
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery SceneState identity is invalid"
                )
            canonical_barriers = tuple(
                self._canonical(barrier, StageBarrier, "StageBarrier")
                for barrier in stage_barriers
            )
            expected_stages = ("motion", "network", "business_environment")
            if tuple(barrier.stage for barrier in canonical_barriers) != expected_stages:
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery requires motion, network, and traffic barriers in order"
                )
            for index, barrier in enumerate(canonical_barriers):
                if (
                    barrier.run_id != self._run_id
                    or barrier.scenario_digest != self._scenario_digest
                    or barrier.at != canonical_target
                ):
                    raise UrbanRecoveryRuntimeHookError(
                        "urban recovery StageBarrier identity is invalid"
                    )
                expected_predecessors = tuple(
                    (item.stage, item.barrier_digest)
                    for item in canonical_barriers[:index]
                )
                actual_predecessors = tuple(
                    (item.stage, item.barrier_digest)
                    for item in barrier.predecessor_barriers
                )
                if actual_predecessors != expected_predecessors:
                    raise UrbanRecoveryRuntimeHookError(
                        "urban recovery StageBarrier predecessor chain is invalid"
                    )
                expected_provider = getattr(
                    self._package, _STAGE_PROVIDER_FIELD[barrier.stage]
                )
                if barrier.provider_ids != (expected_provider,):
                    raise UrbanRecoveryRuntimeHookError(
                        f"urban recovery {barrier.stage} barrier has the wrong Provider"
                    )
            closed_provider_ids = {
                provider_id
                for barrier in canonical_barriers
                for provider_id in barrier.provider_ids
            }
            if not isinstance(events, tuple):
                raise UrbanRecoveryRuntimeHookError(
                    "urban recovery Provider events must be an ordered tuple"
                )
            for event in events:
                canonical_event = self._canonical(
                    event, ProviderEvent, "ProviderEvent"
                )
                if canonical_event.provider_id not in closed_provider_ids:
                    raise UrbanRecoveryRuntimeHookError(
                        "urban recovery event came from a Provider outside the closed stages"
                    )
                if (
                    canonical_event.time.tick > canonical_target.tick
                    or canonical_event.time.sim_time_ns > canonical_target.sim_time_ns
                ):
                    raise UrbanRecoveryRuntimeHookError(
                        "urban recovery event occurs after its committed barrier"
                    )
                if canonical_event.payload_schema_id == _AIRSPACE_TRANSITION_SCHEMA:
                    self._accept_airspace_event(canonical_event, canonical_target)
            if canonical_target.tick == self._package.final_tick and self._package.execution_profile == "formal":
                transition_kinds = {
                    transition.transition for transition in self._airspace_transitions
                }
                if transition_kinds != {"entered", "exited"}:
                    raise UrbanRecoveryRuntimeHookError(
                        "urban recovery exact-horizon run lacks engine-generated no-fly entry and exit"
                    )
            self._last_tick = canonical_target.tick

    def _accept_airspace_event(self, event: ProviderEvent, target: SimulationTime) -> None:
        if event.provider_id != self._package.flight_provider_id or event.time != target:
            raise UrbanRecoveryRuntimeHookError("airspace transition is not authoritative PX4/Gazebo evidence")
        payload = {item.name: item.value for item in event.payload}
        try:
            if set(payload) != {"transition_json", "logical_origin_engine_ns"}:
                raise ValueError("airspace event fields are not exact")
            raw = payload["transition_json"]
            origin = payload["logical_origin_engine_ns"]
            if not isinstance(raw, str) or type(origin) is not int or origin < 0:
                raise ValueError("airspace event epoch or JSON type is invalid")
            transition = AirspaceTransition.model_validate(json.loads(raw))
            if canonical_json_bytes(transition.model_dump(mode="json")).decode("utf-8") != raw:
                raise ValueError("airspace event JSON is not canonical")
        except (TypeError, ValueError, ValidationError) as error:
            raise UrbanRecoveryRuntimeHookError("airspace transition payload is invalid") from error
        logical_time = transition.engine_sim_time_ns - origin
        if self._package.execution_profile == "engineering":
            if (
                transition.vehicle_id not in {role.vehicle_id for role in self._roles.values() if role.role == "uav"}
                or transition.region_id != self._package.recovery.incident_region_id
                or transition.world_sha256 != self._world_digest
                or event.event_id != f"gazebo.airspace.{transition.transition}"
                or not target.sim_time_ns - STEP_NS < logical_time <= target.sim_time_ns
                or (self._airspace_origin_ns is not None and origin != self._airspace_origin_ns)
            ):
                raise UrbanRecoveryRuntimeHookError("airspace transition has a foreign identity or engine window")
            # Record actual incidents, including early/repeated or unrecovered ones.
            # Mission success is not a prerequisite for an engineering recording.
            self._airspace_origin_ns = origin
            self._airspace_transitions.append(transition)
            return
        if (
            transition.vehicle_id != self._package.recovery.incident_vehicle_id
            or transition.region_id != self._package.recovery.incident_region_id
            or transition.world_sha256 != self._world_digest
            or transition.sequence != len(self._airspace_transitions) + 1
            or event.event_id != f"gazebo.airspace.{transition.transition}"
            or (self._airspace_origin_ns is not None and origin != self._airspace_origin_ns)
        ):
            raise UrbanRecoveryRuntimeHookError("airspace transition identity, epoch, or sequence is invalid")
        logical_time = transition.engine_sim_time_ns - origin
        if not target.sim_time_ns - STEP_NS < logical_time <= target.sim_time_ns or logical_time < self._package.recovery.injection_start_ns:
            raise UrbanRecoveryRuntimeHookError("airspace transition is outside its declared engine window")
        expected = "entered" if not self._airspace_transitions else "exited"
        if len(self._airspace_transitions) >= 2 or transition.transition != expected:
            raise UrbanRecoveryRuntimeHookError("airspace transition order is not entered then exited")
        self._airspace_origin_ns = origin
        self._airspace_sequences.add(transition.sequence)
        self._airspace_transitions.append(transition)

    def _require_time(self, expected: SimulationTime) -> None:
        try:
            current = self._runtime_hook_time()
        except Exception as error:
            raise UrbanRecoveryRuntimeHookError(
                "urban recovery runtime hook time is unavailable"
            ) from error
        current = self._canonical(current, SimulationTime, "runtime hook time")
        if current != expected:
            raise UrbanRecoveryRuntimeHookError(
                "urban recovery runtime hook time differs from callback time"
            )

    @staticmethod
    def _canonical(
        value: object, contract_type: type[_ContractModel], label: str
    ) -> _ContractModel:
        if not isinstance(value, contract_type):
            raise UrbanRecoveryRuntimeHookError(f"{label} has the wrong contract type")
        try:
            canonical = contract_type.model_validate(value.model_dump(mode="json"))
        except (AttributeError, TypeError, ValueError, ValidationError) as error:
            raise UrbanRecoveryRuntimeHookError(
                f"{label} fails strict revalidation"
            ) from error
        if canonical != value:
            raise UrbanRecoveryRuntimeHookError(f"{label} is not canonical")
        return canonical


class UrbanRecoveryRuntimeHookFactory:
    @property
    def package_id(self) -> str:
        return PACKAGE_ID

    def create(
        self,
        *,
        run: ResolvedRunSpec,
        reader: BundleReader,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
    ) -> UrbanRecoveryRuntimeHook:
        try:
            package = load_urban_recovery_package(reader=reader, task=run.task)
            UrbanRecoveryTaskPackageResolver().resolve_runtime(
                reader=reader,
                task=run.task,
                environment=run.environment,
                agents=run.agents,
                scenario=run.scenario,
            )
            return UrbanRecoveryRuntimeHook(
                run=run,
                package=package,
                providers=providers,
                ledger=ledger,
                runtime_hook_time=runtime_hook_time,
            )
        except UrbanRecoveryResolutionError as error:
            raise UrbanRecoveryRuntimeHookError(
                "urban recovery runtime package revalidation failed"
            ) from error


__all__ = [
    "UrbanRecoveryRuntimeHook",
    "UrbanRecoveryRuntimeHookError",
    "UrbanRecoveryRuntimeHookFactory",
]
