"""Native registry adapter for the ``logistics.business`` Provider.

This module is the dedicated session-builder bridge between the builtin
``ProviderRegistry`` and the real :class:`LogisticsBusinessProvider`. It owns
the materialization-time capability guard and the identity checks that the
registry cannot express: the registry resolves a registered adapter by name and
re-validates the strict config, but only the adapter knows which capabilities
this logistics business Provider actually supplies today.

The Provider supplies these non-physical business capabilities:

* ``logistics.orders.authority``
* ``logistics.facilities.state``
* ``logistics.orders.scheduled-arrivals``

The pending capabilities (``logistics.airspace.events``,
``logistics.delivery.observation``) and every flight capability served by the
shared PX4 Gazebo backend (``flight.command``, ``observation.capture``) have no
authoritative interface on this adapter. Declaring them in a ``ProviderRef``
must fail at materialization through the real registry path — never be
forwarded to the workload, and never be silently aliased onto the inspection
runtime or any other backend.
"""

from __future__ import annotations

from aero_bench.config.models import ClockSpec
from aero_bench.providers.contracts import ProviderManifest, ProviderSession
from aero_bench.providers.logistics_business.config import LogisticsBusinessConfig
from aero_bench.providers.logistics_business.scheduled_arrivals import SCHEDULED_ARRIVAL_CAPABILITY
from aero_bench.providers.logistics_business.provider import (
    LogisticsBusinessProvider,
)
from aero_bench.providers.registry import ProviderRegistryError, RuntimeEndpoint
from aero_bench.world.resolved import ResolvedScenario

#: The exact adapter id this registration binds to the builtin registry.
LOGISTICS_BUSINESS_ADAPTER = "logistics.business"

#: Capabilities the logistics business adapter actually supplies. This is the
#: exact supported set: every claimed capability must be a member, and the
#: pending airspace/delivery-observation or flight capabilities are never
#: accepted here.
SUPPORTED_CAPABILITIES: frozenset[str] = frozenset(
    {
        "logistics.facilities.state",
        "logistics.orders.authority",
        SCHEDULED_ARRIVAL_CAPABILITY,
    }
)


def build_logistics_business_session(
    config: LogisticsBusinessConfig,
    manifest: ProviderManifest,
    runtime_endpoint: RuntimeEndpoint,
    run_id: str,
    credential: str,
    scenario: ResolvedScenario,
    clock: ClockSpec,
) -> ProviderSession:
    """Materialize a real Logistics Business Provider session.

    The registry has already validated the ProviderRef, the digest-bound
    config, the runtime endpoint/port, the run id and the executor-issued
    credential. This adapter adds the checks that are specific to the
    logistics business implementation: the manifest must declare this
    adapter, the manifest and the strict config must agree on the native
    provider identity, and the declared capabilities must be a supported
    subset — no pending or flight capability may be claimed merely by
    including it in a ProviderRef.
    """

    if manifest.adapter != LOGISTICS_BUSINESS_ADAPTER:
        raise ProviderRegistryError(
            "logistics business session builder requires the "
            f"{LOGISTICS_BUSINESS_ADAPTER!r} adapter"
        )
    if manifest.provider_id != config.provider_id:
        raise ProviderRegistryError(
            "logistics business manifest/provider configuration IDs differ"
        )
    declared = frozenset(manifest.capabilities)
    unsupported = declared - SUPPORTED_CAPABILITIES
    if unsupported:
        raise ProviderRegistryError(
            "logistics.business adapter does not support claimed "
            f"capability ids: {', '.join(sorted(unsupported))}"
        )
    if config.scheduled_orders and SCHEDULED_ARRIVAL_CAPABILITY not in declared:
        raise ProviderRegistryError("scheduled orders require the declared scheduled-arrivals capability")
    for item in config.scheduled_orders:
        if item.at_tick > clock.max_steps or item.order.release_time_s * 1_000_000_000 < item.at_tick * clock.step_ns:
            raise ProviderRegistryError("scheduled order tick/release time exceeds the declared clock contract")
    return LogisticsBusinessProvider(
        config=config,
        manifest=manifest,
        runtime_endpoint=runtime_endpoint,
        run_id=run_id,
        session_token=credential,
        scenario=scenario,
    )


__all__ = [
    "LOGISTICS_BUSINESS_ADAPTER",
    "SUPPORTED_CAPABILITIES",
    "build_logistics_business_session",
]
