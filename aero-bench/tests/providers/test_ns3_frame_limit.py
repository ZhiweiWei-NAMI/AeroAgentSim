from __future__ import annotations

import asyncio
import inspect
import json

from containers.ns3 import server


class _EchoService:
    async def handle(self, operation, request):
        assert operation == "probe"
        return {"received": len(request["data"])}


def test_ns3_listener_uses_protocol_frame_bound() -> None:
    assert "limit=MAX_FRAME_BYTES + 1" in inspect.getsource(server.serve)


def test_ns3_accepts_scene_frames_larger_than_asyncio_default() -> None:
    async def run():
        listener = await asyncio.start_server(
            lambda reader, writer: server._serve_client(reader, writer, _EchoService()),
            "127.0.0.1", 0, limit=server.MAX_FRAME_BYTES + 1,
        )
        async with listener:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            try:
                writer.write(json.dumps({"operation": "probe", "data": "x" * 100_000}).encode() + b"\n")
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), 5))
                assert response == {"received": 100_000}
            finally:
                writer.close()
                await writer.wait_closed()
    asyncio.run(run())


def test_ns3_rejects_oversized_frames_without_unhandled_reader_exception(monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_FRAME_BYTES", 1024)

    async def run():
        listener = await asyncio.start_server(
            lambda reader, writer: server._serve_client(reader, writer, _EchoService()),
            "127.0.0.1", 0, limit=server.MAX_FRAME_BYTES + 1,
        )
        async with listener:
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.sockets[0].getsockname()[1])
            try:
                writer.write(b"x" * 2048 + b"\n")
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), 5))
                assert response["error"]["code"] == "frame.too-large"
                assert await reader.read() == b""
            finally:
                writer.close()
                await writer.wait_closed()
    asyncio.run(run())
