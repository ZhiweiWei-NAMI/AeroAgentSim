from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import (
    JsonLineRpcTransport,
    ProviderRpcError,
)


InspectionBusinessTransportError = ProviderRpcError


# v2 replaces the legacy generic time-advance RPC with typed staged
# business_environment requests/results for provider time advancement.
PROTOCOL_VERSION = "aero-bench.inspection-business-rpc/v2"


class InspectionBusinessTransport(Protocol):
    """The single RPC surface between the session and the provider workload."""

    async def request(
        self, operation: str, payload: Mapping[str, object]
    ) -> Mapping[str, object]: ...

    async def close(self) -> None: ...


async def connect_runtime(endpoint: RuntimeEndpoint) -> JsonLineRpcTransport:
    """Connect to the materializer-owned endpoint for a production session."""

    return await JsonLineRpcTransport.connect(endpoint, component="inspection-business")


__all__ = [
    "PROTOCOL_VERSION",
    "InspectionBusinessTransport",
    "InspectionBusinessTransportError",
    "connect_runtime",
]
