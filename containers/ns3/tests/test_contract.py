"""Contract checks run on the host with no Docker/ns-3 dependency."""

import asyncio
import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from service import config, rpc, wire
from service.runtime import NativeFailure, Runtime


def request(op="hello", payload=None, identity=1):
    return {
        "protocol": wire.PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": identity,
        "op": op,
        "payload": {} if payload is None else payload,
    }


def specification():
    return {
        "seed": 713,
        "nodes": [
            {"id": "station:α", "position_enu": [0, 0, 0]},
            {"id": "vehicle/mobile", "position_enu": [10, 0, 3]},
        ],
    }


@given(st.integers(min_value=1, max_value=2**100))
def test_integer_request_ids_lossless(value):
    assert wire.decode(wire.encode(request(identity=value)))["id"] == value


@pytest.mark.parametrize(
    "frame",
    [
        b"{}",
        b"{}\r\n",
        b"{}\n{}\n",
        b"\xff\n",
        b"\xef\xbb\xbf{}\n",
        b'{"id":1,"id":2}\n',
        b'{"x":NaN}\n',
        b'{"x":Infinity}\n',
        b'{"x":1e999}\n',
        b'{"x":"\\ud800"}\n',
        b"[]\n",
        b'{"x":' + b"[" * 129 + b"0" + b"]" * 129 + b"}\n",
        b'{"x":' + b"9" * 4097 + b"}\n",
    ],
)
def test_invalid_wire_frames_fail(frame):
    with pytest.raises((ValueError, UnicodeError)):
        wire.decode(frame)


def test_frame_limit_includes_lf():
    valid = wire.encode(request())
    padded = b" " * (wire.MAX_FRAME - len(valid)) + valid
    assert wire.decode(padded)["id"] == 1
    with pytest.raises(ValueError):
        wire.decode(b" " + padded)


@pytest.mark.parametrize(
    "key,value",
    [
        ("major", True),
        ("major", 2),
        ("minor", 1),
        ("minor", False),
        ("id", 0),
        ("id", 1.0),
        ("id", True),
        ("op", None),
        ("protocol", "aerokernel.rpc"),
        ("payload", None),
    ],
)
def test_invalid_envelopes(key, value):
    data = request()
    data[key] = value
    with pytest.raises(ValueError):
        wire.decode(wire.encode(data))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_response_rejected(value):
    with pytest.raises(ValueError):
        wire.encode({"result": {"x": value}})


def test_reset_general_ids_and_declared_defaults():
    original = specification()
    normalized = config.reset(original)
    assert normalized["nodes"] == original["nodes"]
    assert normalized["propagation"]["reference_loss_db"] == pytest.approx(46.734378583)
    assert "channel" not in original


@pytest.mark.parametrize(
    "change",
    [
        {"seed": 0},
        {"seed": True},
        {"seed": None},
        {"seed": 2**32},
        {"nodes": []},
        {"nodes": None},
        {"nodes": [{"id": "a", "position_enu": [0, 0, 0]}] * 2},
        {"channel": None},
        {"channel": {"number": 37}},
        {"channel": {"width_mhz": 40}},
        {"channel": {"tx_power_dbm": None}},
        {"propagation": {"exponent": False}},
        {
            "scene_volumes": [
                {"min_enu": [0, 0, 0], "max_enu": [0, 1, 1], "loss_db": 12}
            ]
        },
        {"scene_volumes": None},
        {"workload_identity": "no"},
    ],
)
def test_reset_rejects_missing_or_invalid_real_inputs(change):
    with pytest.raises(ValueError):
        config.reset({**specification(), **change})


def test_future_mobility_order_and_no_past_updates():
    nodes = {"a": 0, "b": 1}
    target, updates = config.advance(
        {
            "to_sim_ns": 2**53 + 50,
            "mobility": [
                {"node": "b", "sim_ns": 2**53 + 20, "position": [0, 1, 2]},
                {"node": "a", "sim_ns": 2**53 + 10, "position": [3, 4, 5]},
            ],
        },
        2**53,
        nodes,
    )
    assert target == 2**53 + 50
    assert [u[0] for u in updates] == [2**53 + 10, 2**53 + 20]
    with pytest.raises(ValueError):
        config.advance(
            {
                "to_sim_ns": 120,
                "mobility": [{"node": "a", "sim_ns": 99, "position": [0, 0, 0]}],
            },
            100,
            nodes,
        )


@pytest.mark.parametrize("target", [0, 99, 100, 100.0, True, 2**63])
def test_advance_must_increase_native_frontier(target):
    with pytest.raises(ValueError):
        config.advance({"to_sim_ns": target}, 100, {"a": 0, "b": 1})


def test_duplicate_node_time_update_rejected():
    update = {"node": "a", "sim_ns": 0, "position": [0, 0, 0]}
    with pytest.raises(ValueError):
        config.advance({"to_sim_ns": 100, "mobility": [update, update]}, 0, {"a": 0})


def send(**changes):
    return {
        "action": "send",
        "params": {
            "packet_id": "packet:α",
            "src": "a",
            "dst": "b",
            "size": 512,
            "payload_ref": "blob:sha256:external",
            **changes,
        },
    }


@pytest.mark.parametrize(
    "change",
    [
        {"size": 0},
        {"size": 61441},
        {"size": True},
        {"payload_ref": None},
        {"src": "unknown"},
        {"dst": "a"},
        {"lifetime_ns": -1},
        {"packet_id": ""},
    ],
)
def test_invalid_packets_rejected_before_native(change):
    runtime = Runtime()
    runtime.nodes = {"a": 0, "b": 1}

    async def exercise():
        result = await runtime.command(send(**change))
        assert result["status"] == "rejected"
        assert runtime.sequence == 0 and runtime.process is None

    asyncio.run(exercise())


def test_packet_expiry_must_fit_int64_time():
    with pytest.raises(ValueError):
        config.command(send(), config.MAX_NS - 10, {"a": 0, "b": 1})


class FakeRuntime:
    def __init__(self):
        self.calls = []
        self.aborts = 0
        self.now = 0

    async def reset(self, payload):
        self.calls.append("reset")
        return {"reached_sim_ns": 0}

    async def command(self, payload):
        self.calls.append("command")
        return {"status": "accepted"}

    async def advance(self, payload):
        if payload.get("slow"):
            await asyncio.sleep(1)
        self.now = payload["to_sim_ns"]
        self.calls.append("advance")
        return {
            "reached_sim_ns": self.now,
            "deliveries": [],
            "drops": [],
            "link_stats": [],
        }

    def abort(self):
        self.aborts += 1

    async def stop(self):
        self.calls.append("stop")


async def conversation(actions):
    runtime = FakeRuntime()
    server = rpc.Server(lambda: runtime)
    listener = await asyncio.start_server(
        server.client, "127.0.0.1", 0, limit=wire.MAX_FRAME
    )
    address = listener.sockets[0].getsockname()
    reader, writer = await asyncio.open_connection(*address)
    replies = []
    try:
        for action in actions:
            frame = action if isinstance(action, bytes) else wire.encode(action)
            # Fragment a valid request to test stream framing, not write framing.
            writer.write(frame[:7])
            await writer.drain()
            writer.write(frame[7:])
            await writer.drain()
            data = await asyncio.wait_for(reader.readline(), 1)
            replies.append(json.loads(data) if data else None)
            if not data:
                break
    finally:
        writer.close()
        await writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await asyncio.sleep(0.01)
    return runtime, replies


def test_lifecycle_echo_and_real_ns_precision():
    actions = [
        request(),
        request("reset", specification(), 2),
        request("advance", {"to_sim_ns": 2**53 + 1}, 3),
        request("close", {}, 4),
    ]
    runtime, replies = asyncio.run(conversation(actions))
    assert runtime.calls == ["reset", "advance", "stop", "stop"]
    for action, reply in zip(actions, replies):
        assert {key: reply[key] for key in ("protocol", "major", "minor", "id")} == {
            key: action[key] for key in ("protocol", "major", "minor", "id")
        }
        assert "result" in reply and "error" not in reply
    assert replies[2]["result"]["reached_sim_ns"] == 2**53 + 1


@pytest.mark.parametrize(
    "fault",
    [
        request("advance", {"to_sim_ns": 10}, 2),
        request("hello", {}, 1),
        request("reset", {}, 2),
        request("unknown", {}, 2),
    ],
)
def test_fault_taints_and_close_cleans_up(fault):
    actions = [request(), fault, request("command", send(), 3), request("close", {}, 4)]
    # reset in HELLO is valid; exercise a second reset in READY instead.
    if fault["op"] == "reset":
        actions.insert(1, request("reset", specification(), 2))
        fault["id"] = 3
        actions[-2]["id"], actions[-1]["id"] = 4, 5
    runtime, replies = asyncio.run(conversation(actions))
    assert replies[-3]["error"]["state"] == "TAINTED"
    assert replies[-2]["error"]["code"] == "INVALID_REQUEST"
    assert replies[-1]["result"] == {"closed": True}
    assert "command" not in runtime.calls


@pytest.mark.parametrize("frame", [b'{"id":1,"id":2}\n', b"{}\n", b"\xff\n"])
def test_untrusted_frames_close_without_fabricated_identity(frame):
    runtime, replies = asyncio.run(conversation([frame]))
    assert replies == [None]
    assert runtime.calls == ["stop"] and runtime.aborts == 1


def test_timeout_has_stable_code_and_does_not_retry(monkeypatch):
    monkeypatch.setitem(wire.TIMEOUTS, "advance", 0.02)
    runtime, replies = asyncio.run(
        conversation(
            [
                request(),
                request("reset", {}, 2),
                request("advance", {"to_sim_ns": 10, "slow": True}, 3),
                request("close", {}, 4),
            ]
        )
    )
    assert replies[2]["error"]["code"] == "TIMEOUT"
    assert runtime.now == 0 and "advance" not in runtime.calls


def test_second_owner_rejected():
    async def exercise():
        runtime = FakeRuntime()
        server = rpc.Server(lambda: runtime)
        listener = await asyncio.start_server(server.client, "127.0.0.1", 0)
        address = listener.sockets[0].getsockname()
        first_reader, first_writer = await asyncio.open_connection(*address)
        first_writer.write(wire.encode(request()))
        await first_writer.drain()
        await first_reader.readline()
        second_reader, second_writer = await asyncio.open_connection(*address)
        assert await asyncio.wait_for(second_reader.read(), 1) == b""
        second_writer.close()
        first_writer.close()
        await second_writer.wait_closed()
        await first_writer.wait_closed()
        listener.close()
        await listener.wait_closed()
        await asyncio.sleep(0.01)

    asyncio.run(exercise())


def test_native_frontier_mismatch_publishes_no_batch():
    async def exercise():
        runtime = Runtime()
        runtime.nodes, runtime.ids = {"a": 0, "b": 1}, ["a", "b"]

        async def wrong(*args):
            return ["OK", "STEP", "101"]

        runtime.request = wrong
        with pytest.raises(NativeFailure, match="frontier mismatch"):
            await runtime.advance({"to_sim_ns": 100})
        assert runtime.now == 0

    asyncio.run(exercise())
