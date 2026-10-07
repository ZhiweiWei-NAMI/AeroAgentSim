from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from aero_bench.providers.rpc import JsonLineRpcTransport, ProviderRpcError, RpcEndpoint


PROTOCOL_VERSION = "aero-bench.sumo-traci/v3"
SumoTransportError = ProviderRpcError


class SumoTransport(Protocol):
    async def request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    async def close(self) -> None: ...


class JsonLineTransport(JsonLineRpcTransport):
    """The only transport for a SUMO provider workload."""

    @classmethod
    async def connect(cls, endpoint: RpcEndpoint) -> JsonLineTransport:
        return await super().connect(endpoint, component="SUMO/TraCI")
