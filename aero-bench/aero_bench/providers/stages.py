"""Provider runtime stage: the single, cycle-free definition of tick ordering.

A Provider's ``runtime_stage`` is an explicit registration scalar. It is the one
authority for where a Provider closes inside a tick (motion -> network ->
business_environment). Roles and capabilities describe scenario semantics and
ownership; they never decide execution stage. Adapter names, capability prefixes,
and unknown-adapter fallbacks are not stage authorities.

This module is a deliberate leaf. It imports nothing from the rest of
``aero_bench`` so that ``world.resolved`` and ``runtime.contracts`` can both
depend on the one ``ProviderStage`` definition without an import cycle.
``runtime.contracts`` re-exports ``ProviderStage`` for existing callers.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, TypeAlias

ProviderStage: TypeAlias = Literal["motion", "network", "business_environment"]

PROVIDER_STAGES: tuple[ProviderStage, ...] = (
    "motion",
    "network",
    "business_environment",
)

MOTION: ProviderStage = "motion"
NETWORK: ProviderStage = "network"
BUSINESS_ENVIRONMENT: ProviderStage = "business_environment"


def parse_runtime_stage(value: object) -> ProviderStage:
    """Validate one strict scalar runtime stage.

    Rejects missing values, unknown strings, non-strings, and lists/sequences.
    There is no default, no role/capability/adapter inference, and no fallback.
    """
    if isinstance(value, (list, tuple, set, dict)) or not isinstance(value, str):
        raise ValueError(
            "provider runtime_stage must be one strict scalar of "
            f"{list(PROVIDER_STAGES)}; got {type(value).__name__}"
        )
    if value not in PROVIDER_STAGES:
        raise ValueError(
            f"provider runtime_stage must be one of {list(PROVIDER_STAGES)}; got {value!r}"
        )
    return value  # type: ignore[return-value]


def ordered_enabled_stages(
    stage_provider_ids: Mapping[ProviderStage, Sequence[str]],
) -> tuple[ProviderStage, ...]:
    """Return enabled stages in the fixed barrier order.

    The motion stage is always enabled, even when its Provider set is empty.
    network and business_environment are enabled only when their set is non-empty.
    """
    enabled: list[ProviderStage] = [MOTION]
    if tuple(stage_provider_ids.get(NETWORK, ())):
        enabled.append(NETWORK)
    if tuple(stage_provider_ids.get(BUSINESS_ENVIRONMENT, ())):
        enabled.append(BUSINESS_ENVIRONMENT)
    return tuple(enabled)


__all__ = [
    "BUSINESS_ENVIRONMENT",
    "MOTION",
    "NETWORK",
    "PROVIDER_STAGES",
    "ProviderStage",
    "ordered_enabled_stages",
    "parse_runtime_stage",
]
