"""Socket contract tests with an instrumented native owner, no Docker/TraCI."""

import asyncio
import json
import sys
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.rpc import Server
from service.wire import PROTOCOL, encode


class Native:
    instances: ClassVar[list] = []

    def __init__(self):
        self.calls = []
        self.__class__.instances.append(self)

    def abort(self):
        self.calls.append(("abort",))

    def version(self):
        return "instrumented test engine"

    def reset(self, payload):
        self.calls.append(("reset", payload))
        return {"reached_sim_ns": 0}

    def advance(self, payload):
        self.calls.append(("advance", payload))
        if payload["to_sim_ns"] == 666:
            raise RuntimeError("native frontier fault")
        return {"reached_sim_ns": payload["to_sim_ns"]}

    def command(self, payload):
        return {"status": "accepted"}

    def stop(self):
        self.calls.append(("stop",))


def request(ident, op, payload=None):
    return {
        "protocol": PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": ident,
        "op": op,
        "payload": {} if payload is None else payload,
    }


async def exchange(reader, writer, req):
    writer.write(encode(req))
    await writer.drain()
    return json.loads(await asyncio.wait_for(reader.readline(), 2))


async def connected(test):
    Native.instances.clear()
    handler = Server(Native)
    listener = await asyncio.start_server(handler.client, "127.0.0.1", 0)
    port = listener.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await test(handler, reader, writer, port)
        finally:
            writer.close()
            await writer.wait_closed()
        # Let disconnect cleanup finish before closing the listener.
        for _ in range(100):
            if not handler.connections:
                break
            await asyncio.sleep(0.001)
        assert not handler.connections
    finally:
        listener.close()
        await listener.wait_closed()


def test_lifecycle_exact_integer_and_close():
    async def case(handler, reader, writer, port):
        assert (
            "capabilities"
            in (await exchange(reader, writer, request(1, "hello")))["result"]
        )
        assert (await exchange(reader, writer, request(2, "reset", {"seed": 7})))[
            "result"
        ]
        target = 2**80 + 1
        result = await exchange(
            reader, writer, request(3, "advance", {"to_sim_ns": target})
        )
        assert result["result"]["reached_sim_ns"] == target
        assert "error" not in result
        assert (await exchange(reader, writer, request(4, "close")))["result"] == {
            "closed": True
        }
        assert await reader.readline() == b""

    asyncio.run(connected(case))


def test_fault_taints_and_never_retries_native():
    async def case(handler, reader, writer, port):
        await exchange(reader, writer, request(1, "hello"))
        await exchange(reader, writer, request(2, "reset"))
        result = await exchange(
            reader, writer, request(3, "advance", {"to_sim_ns": 666})
        )
        assert "result" not in result
        assert result["error"]["state"] == "TAINTED"
        result = await exchange(
            reader, writer, request(4, "advance", {"to_sim_ns": 777})
        )
        assert result["error"]["state"] == "TAINTED"
        assert [c for c in Native.instances[0].calls if c[0] == "advance"] == [
            ("advance", {"to_sim_ns": 666})
        ]
        await exchange(reader, writer, request(5, "close"))

    asyncio.run(connected(case))


def test_repeated_request_id_taints():
    async def case(handler, reader, writer, port):
        await exchange(reader, writer, request(1, "hello"))
        response = await exchange(reader, writer, request(1, "reset"))
        assert response["error"]["state"] == "TAINTED"
        assert not [c for c in Native.instances[0].calls if c[0] == "reset"]
        await exchange(reader, writer, request(2, "close"))

    asyncio.run(connected(case))


def test_single_owner_and_malformed_frame_disconnect():
    async def case(handler, reader, writer, port):
        await exchange(reader, writer, request(1, "hello"))
        other_reader, other_writer = await asyncio.open_connection("127.0.0.1", port)
        assert await asyncio.wait_for(other_reader.readline(), 2) == b""
        other_writer.close()
        await other_writer.wait_closed()
        writer.write(b'{"protocol":"wrong","protocol":"duplicate"}\n')
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), 2) == b""

    asyncio.run(connected(case))


def test_advance_before_reset_taints():
    async def case(handler, reader, writer, port):
        response = await exchange(
            reader, writer, request(1, "advance", {"to_sim_ns": 1})
        )
        assert response["error"]["state"] == "TAINTED"
        await exchange(reader, writer, request(2, "close"))

    asyncio.run(connected(case))


def test_operation_timeout_taints_and_aborts_without_retry(monkeypatch):
    import threading

    from service import rpc

    class Blocking(Native):
        def __init__(self):
            super().__init__()
            self.cancel = threading.Event()

        def advance(self, payload):
            self.calls.append(("advance", payload))
            self.cancel.wait(2)
            raise RuntimeError("cancelled native integration")

        def abort(self):
            self.cancel.set()
            super().abort()

    async def case():
        handler = Server(Blocking)
        listener = await asyncio.start_server(handler.client, "127.0.0.1", 0)
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", listener.sockets[0].getsockname()[1]
        )
        try:
            await exchange(reader, writer, request(1, "hello"))
            await exchange(reader, writer, request(2, "reset"))
            response = await exchange(
                reader, writer, request(3, "advance", {"to_sim_ns": 1})
            )
            assert response["error"]["state"] == "TAINTED"
            assert "result" not in response
            assert Blocking.instances[-1].cancel.is_set()
            await exchange(reader, writer, request(4, "close"))
            assert (
                len([c for c in Blocking.instances[-1].calls if c[0] == "advance"]) == 1
            )
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            await listener.wait_closed()
            for _ in range(100):
                if not handler.connections:
                    break
                await asyncio.sleep(0.001)
            assert not handler.connections

    monkeypatch.setitem(rpc.TIMEOUTS, "advance", 0.01)
    asyncio.run(case())
