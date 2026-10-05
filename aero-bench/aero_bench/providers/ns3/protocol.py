from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from aero_bench.providers.rpc import JsonLineRpcTransport, ProviderRpcError, RpcEndpoint


PROTOCOL_VERSION = "aero-bench.ns3-rpc/v5"
STATE_SCHEMA = "ns3.state.v4"
DELIVERY_EVENT_SCHEMA = "inspection.network-delivery.v1"
MAILBOX_OBSERVATION_PREFIX = "network.mailbox."
MAILBOX_OBSERVATION_SCHEMA = "aero-bench.network-mailbox-observation/v1"
MAX_NETWORK_PAYLOAD_BYTES = 60 * 1024
MAX_MAILBOX_DELIVERIES_PER_BARRIER = 4096
MAX_MAILBOX_OBSERVATION_BYTES = 6 * 1024 * 1024
Ns3TransportError = ProviderRpcError


class Ns3Transport(Protocol):
    async def request(
        self, operation: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    async def close(self) -> None: ...


class JsonLineTransport(JsonLineRpcTransport):
    @classmethod
    async def connect(cls, endpoint: RpcEndpoint) -> JsonLineTransport:
        return await super().connect(endpoint, component="ns-3")
