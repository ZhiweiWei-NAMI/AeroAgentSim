from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from aero_bench.providers.registry import RuntimeEndpoint
from aero_bench.providers.rpc import JsonLineRpcTransport, ProviderRpcError


WorldSceneTransportError = ProviderRpcError


class WorldSceneTransport(Protocol):
    async def request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    async def close(self) -> None: ...


class JsonLineTransport(JsonLineRpcTransport):
    @classmethod
    async def connect(cls, endpoint: RuntimeEndpoint) -> JsonLineTransport:
        return await super().connect(endpoint, component="world-scene")
