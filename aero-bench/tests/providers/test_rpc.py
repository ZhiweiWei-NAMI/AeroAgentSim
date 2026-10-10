from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from aero_bench.providers.rpc import (
    JsonLineRpcServer,
    JsonLineRpcTransport,
    ProviderRemoteError,
    ProviderRpcError,
    canonical_json_line,
    parse_json_object,
)


class Endpoint:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port


def test_canonical_json_line_has_stable_order_and_rejects_nonfinite_values() -> None:
    assert canonical_json_line({"z": 1, "a": "世界"}) == (
        b'{"a":"\xe4\xb8\x96\xe7\x95\x8c","z":1}\n'
    )
    with pytest.raises(ProviderRpcError, match="not canonical JSON"):
        canonical_json_line({"value": float("nan")})


@pytest.mark.parametrize(
    "frame",
    (
        b'{"key":1,"key":2}\n',
        b'{"value":NaN}\n',
        b"[1,2,3]\n",
        b"\xff\n",
    ),
)
def test_parse_json_object_rejects_non_strict_frames(frame: bytes) -> None:
    with pytest.raises(ProviderRpcError):
        parse_json_object(frame)


def test_transport_rejects_operation_override_before_writing() -> None:
    async def scenario() -> None:
        reader = asyncio.StreamReader()

        class Writer:
            def write(self, data: bytes) -> None:
                raise AssertionError(f"unexpected write: {data!r}")

        transport = JsonLineRpcTransport(
            reader,
            Writer(),  # type: ignore[arg-type]
            component="test",
        )
        with pytest.raises(ValueError, match="cannot redefine"):
            await transport.request("prepare", {"operation": "shutdown"})
        with pytest.raises(ValueError, match="must match"):
            await transport.request("bad operation", {})

    asyncio.run(scenario())


def test_server_transport_round_trip_and_structured_remote_error() -> None:
    asyncio.run(_server_transport_round_trip_and_structured_remote_error())


async def _server_transport_round_trip_and_structured_remote_error() -> None:
    async def handler(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if operation == "echo":
            return {"operation_seen": operation, "payload": dict(payload)}
        raise ValueError(f"unsupported operation: {operation}")

    rpc_server = JsonLineRpcServer(handler)
    server = await rpc_server.start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    endpoint = Endpoint("127.0.0.1", int(sockets[0].getsockname()[1]))
    try:
        transport = await JsonLineRpcTransport.connect(endpoint, component="test")
        assert await transport.request("echo", {"tick": 7}) == {
            "operation_seen": "echo",
            "payload": {"tick": 7},
        }
        with pytest.raises(ProviderRemoteError) as raised:
            await transport.request("unknown", {})
        assert raised.value.code == "request.invalid"
        assert "unsupported operation" in raised.value.detail
        await transport.close()
    finally:
        server.close()
        await server.wait_closed()


def test_server_rejects_duplicate_keys_and_incomplete_frame() -> None:
    asyncio.run(_server_rejects_duplicate_keys_and_incomplete_frame())


async def _server_rejects_duplicate_keys_and_incomplete_frame() -> None:
    async def handler(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError((operation, payload))

    rpc_server = JsonLineRpcServer(handler)
    server = await rpc_server.start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    port = int(sockets[0].getsockname()[1])
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"operation":"echo","operation":"shutdown"}\n')
        await writer.drain()
        duplicate_error = parse_json_object(await reader.readline())
        assert duplicate_error["error"]["code"] == "request.invalid"
        writer.close()
        await writer.wait_closed()

        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"operation":"echo"}')
        await writer.drain()
        writer.write_eof()
        incomplete_error = parse_json_object(await reader.readline())
        assert incomplete_error["error"]["code"] == "frame.incomplete"
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


def test_server_graceful_close_waits_for_concurrent_response_drains() -> None:
    asyncio.run(_server_graceful_close_waits_for_concurrent_response_drains())


async def _server_graceful_close_waits_for_concurrent_response_drains() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def handler(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal calls
        assert operation == "slow"
        calls += 1
        started.set()
        await release.wait()
        return {"request": payload["request"]}

    rpc_server = JsonLineRpcServer(handler)
    server = await rpc_server.start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    endpoint = Endpoint("127.0.0.1", int(sockets[0].getsockname()[1]))
    first = await JsonLineRpcTransport.connect(endpoint, component="test")
    second = await JsonLineRpcTransport.connect(endpoint, component="test")
    first_request = asyncio.create_task(first.request("slow", {"request": "first"}))
    second_request = asyncio.create_task(second.request("slow", {"request": "second"}))
    try:
        await started.wait()
        while calls != 2:
            await asyncio.sleep(0)
        closing = asyncio.create_task(server.graceful_close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        assert await first_request == {"request": "first"}
        assert await second_request == {"request": "second"}
        await closing
        assert rpc_server.stopping
    finally:
        release.set()
        if not first_request.done():
            first_request.cancel()
        if not second_request.done():
            second_request.cancel()
        await asyncio.gather(first_request, second_request, return_exceptions=True)
        await first.close()
        await second.close()


def test_server_graceful_close_force_closes_clients_after_deadline() -> None:
    asyncio.run(_server_graceful_close_force_closes_clients_after_deadline())


async def _server_graceful_close_force_closes_clients_after_deadline() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        assert operation == "stuck"
        started.set()
        await release.wait()
        return {"status": "never"}

    rpc_server = JsonLineRpcServer(handler)
    server = await rpc_server.start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    endpoint = Endpoint("127.0.0.1", int(sockets[0].getsockname()[1]))
    transport = await JsonLineRpcTransport.connect(endpoint, component="test")
    request = asyncio.create_task(transport.request("stuck", {}))
    try:
        await started.wait()
        with pytest.raises(ProviderRpcError, match="deadline"):
            await server.graceful_close(timeout_seconds=0.01)
        with pytest.raises(ProviderRpcError):
            await asyncio.wait_for(request, timeout=1)
        assert request.done()
        assert rpc_server.stopping
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await transport.close()


def test_server_records_response_drain_evidence_and_callback() -> None:
    asyncio.run(_server_records_response_drain_evidence_and_callback())


async def _server_records_response_drain_evidence_and_callback() -> None:
    callbacks: list[tuple[str, bool]] = []

    def response_drained(
        operation: str, response: Mapping[str, Any], drained: bool
    ) -> None:
        callbacks.append((operation, drained))

    async def handler(operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"status": "ok"}

    rpc_server = JsonLineRpcServer(handler, response_drained=response_drained)
    server = await rpc_server.start(host="127.0.0.1", port=0)
    sockets = server.sockets
    assert sockets is not None and len(sockets) == 1
    endpoint = Endpoint("127.0.0.1", int(sockets[0].getsockname()[1]))
    transport = await JsonLineRpcTransport.connect(endpoint, component="test")
    try:
        assert await transport.request("probe", {}) == {"status": "ok"}
        assert callbacks == [("probe", True)]
        assert rpc_server.response_drain_events[-1].drained
    finally:
        await transport.close()
        await server.graceful_close(timeout_seconds=1)


def test_server_records_failed_response_drain() -> None:
    async def scenario() -> None:
        async def handler(
            operation: str, payload: Mapping[str, Any]
        ) -> Mapping[str, Any]:
            return {}

        rpc_server = JsonLineRpcServer(handler)

        class FailingWriter:
            def is_closing(self) -> bool:
                return False

            def write(self, encoded: bytes) -> None:
                assert encoded

            async def drain(self) -> None:
                raise ConnectionError("client disconnected")

        with pytest.raises(ProviderRpcError, match="response drain failed"):
            await rpc_server._write_response(
                FailingWriter(),
                operation="turn.complete",
                response={"status": "terminated"},
                encoded=b'{"status":"terminated"}\n',
            )
        assert rpc_server.response_drain_events[-1].drained is False

    asyncio.run(scenario())
