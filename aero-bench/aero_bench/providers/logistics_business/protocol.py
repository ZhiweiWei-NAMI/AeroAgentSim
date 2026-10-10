from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcTransport, ProviderRpcError


LogisticsBusinessTransportError = ProviderRpcError

# v1 is the acknowledged development checkpoint. It implements only the
# non-physical order transitions whose OrderActorRole the canonical domain
# permits without an authoritative physical-evidence interface. Physical
# pickup/handoff/deliver/charge transitions are explicitly refused until the
# later authoritative physical-evidence pipeline lands.
PROTOCOL_VERSION = "aero-bench.logistics-business-rpc/v1"

#: Authenticated online-order creation tool served through the command surface.
#: The workload binds the on-wire OrderRequest to the attested native principal
#: and the canonical business grant before the arrival journal mutates.
LOGISTICS_ORDER_CREATE_TOOL = "logistics.order.create"


class LogisticsBusinessTransport(Protocol):
    """The single RPC surface between the session and the provider workload."""

    async def request(
        self, operation: str, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    async def close(self) -> None: ...


async def connect_runtime(endpoint: RuntimeEndpoint) -> JsonLineRpcTransport:
    """Connect to the materializer-owned endpoint for a production session."""

    return await JsonLineRpcTransport.connect(endpoint, component="logistics-business")


__all__ = [
    "LOGISTICS_ORDER_CREATE_TOOL",
    "PROTOCOL_VERSION",
    "LogisticsBusinessTransport",
    "LogisticsBusinessTransportError",
    "connect_runtime",
]
