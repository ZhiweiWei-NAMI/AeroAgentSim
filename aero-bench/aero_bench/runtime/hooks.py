from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from aero_bench.config.loader import BundleReader
from aero_bench.config.models import Identifier, Sha256, StrictModel
from aero_bench.config.resolver import ResolvedRunSpec
from aero_bench.providers.contracts import ProviderSession
from aero_bench.runtime.contracts import (
    ProviderEvent,
    SceneState,
    SimulationTime,
    StageBarrier,
)
from aero_bench.runtime.ledger import EventLedger


class ValidatedObservation(StrictModel):
    """A Gateway observation after grant, schema, identity, and digest validation."""

    run_id: Sha256
    agent_id: Identifier
    provider_id: Identifier
    observation_id: Identifier
    time: SimulationTime
    request_digest: Sha256
    payload_digest: Sha256


class RuntimeHook(Protocol):
    async def on_validated_observation(
        self, observation: ValidatedObservation
    ) -> None: ...

    async def on_stage_barriers_closed(
        self,
        events: tuple[ProviderEvent, ...],
        *,
        scene_state: SceneState,
        stage_barriers: tuple[StageBarrier, ...],
        target: SimulationTime,
    ) -> None: ...


class RuntimeHookFactory(Protocol):
    @property
    def package_id(self) -> str: ...

    def create(
        self,
        *,
        run: ResolvedRunSpec,
        reader: BundleReader,
        providers: Mapping[str, ProviderSession],
        ledger: EventLedger,
        runtime_hook_time: Callable[[], SimulationTime],
    ) -> RuntimeHook: ...


def resolve_runtime_hook(
    *,
    run: ResolvedRunSpec,
    reader: BundleReader,
    providers: Mapping[str, ProviderSession],
    ledger: EventLedger,
    runtime_hook_time: Callable[[], SimulationTime],
    factories: tuple[RuntimeHookFactory, ...],
) -> RuntimeHook | None:
    by_id: dict[str, RuntimeHookFactory] = {}
    for factory in factories:
        if factory.package_id in by_id:
            raise ValueError(f"duplicate runtime hook factory: {factory.package_id}")
        by_id[factory.package_id] = factory
    factory = by_id.get(run.task.package.package_id)
    if factory is None:
        return None
    return factory.create(
        run=run,
        reader=reader,
        providers=providers,
        ledger=ledger,
        runtime_hook_time=runtime_hook_time,
    )


__all__ = [
    "RuntimeHook",
    "RuntimeHookFactory",
    "ValidatedObservation",
    "resolve_runtime_hook",
]
