"""Native-adapter transport unit tests: ``JsonLinesClient`` LF framing and fault semantics.

Every case drives a real threaded one-shot TCP fake server, so socket framing,
the absolute per-operation deadline and the connection-tainting rules are
exercised against the current implementation rather than a mock of it.
Assertions pin the behavior the implementation actually exhibits.
"""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections.abc import Callable

import pytest
from aerokernel import KernelError
from aerokernel.values import canonical_json

from aeroagentsim.adapters.lockstep_base import TIMEOUTS, JsonLinesClient

PROTOCOL = "e1/1"
HOST = "127.0.0.1"


def envelope(request: dict, result: object) -> bytes:
    """A well-formed response frame for ``request`` carrying ``result``."""
    body = {
        "protocol": PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": request["id"],
        "result": result,
    }
    return (json.dumps(body, sort_keys=True) + "\n").encode()


def raw_result_frame(request: dict, raw_result: bytes) -> bytes:
    """A well-formed response frame whose result is pre-encoded raw JSON."""
    return b'{"id":%d,"major":1,"minor":0,"protocol":"%s","result":%s}\n' % (
        request["id"],
        PROTOCOL.encode(),
        raw_result,
    )


def raw_envelope(request: dict, **fields: object) -> bytes:
    """A response frame with caller-chosen envelope members."""
    complete: dict[str, object] = {
        "protocol": PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": request["id"],
        "result": {},
    }
    complete.update(fields)
    parts = [
        f"{json.dumps(key)}:{json.dumps(value)}" for key, value in complete.items()
    ]
    return ("{" + ",".join(parts) + "}\n").encode()


class FakeServer(threading.Thread):
    """One-shot TCP server: accepts a single connection, runs the handler, exits."""

    def __init__(self, handler: Callable[[FakeServer, socket.socket], None]) -> None:
        super().__init__(daemon=True, name="fake-adapter-server")
        self.handler = handler
        self.received: list[bytes] = []
        self.error: BaseException | None = None
        self.port = -1
        self._listening = threading.Event()

    def run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, 0))
            listener.listen(1)
            self.port = listener.getsockname()[1]
            self._listening.set()
            connection, _ = listener.accept()
        except OSError as exc:  # bind/accept failure before the handler ran
            self.error = exc
            listener.close()
            return
        with connection:
            try:
                self.handler(self, connection)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client aborted; that is part of several scenarios
            except BaseException as exc:  # noqa: BLE001 - surface thread handler bugs
                self.error = exc
        listener.close()

    def wait_port(self) -> int:
        if not self._listening.wait(timeout=10.0):
            raise AssertionError("fake server never started listening")
        return self.port


def read_request(server: FakeServer, connection: socket.socket) -> dict:
    """Read exactly one complete LF-framed request from the connection."""
    buffer = bytearray()
    while b"\n" not in buffer:
        chunk = connection.recv(65536)
        if not chunk:
            raise AssertionError("connection closed before a complete request")
        buffer.extend(chunk)
    frame, _, trailing = bytes(buffer).partition(b"\n")
    assert not trailing, "client sent more than one unsolicited frame"
    server.received.append(frame + b"\n")  # store the frame with its LF
    return json.loads(frame)


def closed_port() -> int:
    """A bound-then-closed port: connects are refused."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((HOST, 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


@pytest.fixture
def fake_server():
    """Start fake servers on demand and join every one of them at the end."""
    servers: list[FakeServer] = []

    def factory(handler) -> FakeServer:
        server = FakeServer(handler)
        server.start()
        server.wait_port()
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.join(timeout=10.0)
        assert not server.is_alive(), "fake server thread did not terminate"
        if server.error is not None:  # surface handler-side failures
            raise server.error


def finish(client: JsonLinesClient | None, server: FakeServer) -> None:
    """Disconnect the client, then check the server thread settled cleanly."""
    if client is not None:
        client.disconnect()
    server.join(timeout=10.0)
    assert not server.is_alive(), "fake server thread did not terminate"
    if server.error is not None:
        raise server.error


# ---------------------------------------------------------------------------
# construction: positive finite timeouts
# ---------------------------------------------------------------------------


def test_default_timeouts_are_positive_and_finite() -> None:
    assert set(TIMEOUTS) == {"hello", "reset", "advance", "command", "close"}
    for seconds in TIMEOUTS.values():
        assert type(seconds) is float and 0 < seconds < math.inf


def test_timeouts_override_keeps_other_defaults() -> None:
    client = JsonLinesClient(HOST, 1, PROTOCOL, timeouts={"advance": 2.5})
    assert client.timeouts["advance"] == 2.5
    for operation, default in TIMEOUTS.items():
        if operation != "advance":
            assert client.timeouts[operation] == default


@pytest.mark.parametrize(
    "seconds", [0, -1.0, math.inf, -math.inf, math.nan, "1", True, None]
)
def test_non_positive_or_non_numeric_timeouts_rejected(seconds) -> None:
    with pytest.raises(ValueError):
        JsonLinesClient(HOST, 1, PROTOCOL, timeouts={"hello": seconds})


@pytest.mark.parametrize("operation", sorted(TIMEOUTS))
def test_zero_timeout_rejected_for_every_operation(operation: str) -> None:
    with pytest.raises(ValueError):
        JsonLinesClient(HOST, 1, PROTOCOL, timeouts={operation: 0.0})


# ---------------------------------------------------------------------------
# framing: fragmented response, lossless integers
# ---------------------------------------------------------------------------


def test_fragmented_response_is_reassembled_byte_by_byte(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = envelope(request, {"ok": True})
        for index in range(len(frame)):  # one byte per segment
            connection.sendall(frame[index : index + 1])
            time.sleep(0.001)

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, timeouts={"hello": 20.0})
    try:
        assert client.request("hello", {}) == {"ok": True}
    finally:
        finish(client, server)


def test_integer_beyond_two_pow_53_is_lossless(fake_server) -> None:
    up, down = 2**53 + 1, -(2**53) - 1

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(envelope(request, {"n": up, "list": [up, down], "f": 0.1}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    try:
        result = client.request("hello", {})
    finally:
        finish(client, server)
    assert type(result["n"]) is int and result["n"] == 9007199254740993
    assert result["list"] == [9007199254740993, -9007199254740993]
    assert type(result["list"][0]) is int and type(result["list"][1]) is int
    assert result["f"] == 0.1 and type(result["f"]) is float


def test_integer_beyond_decimal_budget_rejected(fake_server) -> None:
    digits = "9" * 5000  # default integer_digits budget is 4096

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_result_frame(request, b'{"n":%s}' % digits.encode()))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "RESOURCE_LIMIT"
    assert client.tainted is True
    finish(None, server)


# ---------------------------------------------------------------------------
# framing: duplicate keys, nonfinite tokens, exponent overflow
# ---------------------------------------------------------------------------


def test_duplicate_keys_inside_result_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_result_frame(request, b'{"a":1,"a":2}'))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "JSON_DUPLICATE"
    finish(None, server)


def test_duplicate_envelope_keys_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(
            (
                f'{{"id":{request["id"]},"id":{request["id"]},"major":1,"minor":0,"protocol":"{PROTOCOL}","result":{{}}}}\n'
            ).encode()
        )

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "JSON_DUPLICATE"
    finish(None, server)


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonfinite_literal_tokens_rejected(fake_server, token: bytes) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_result_frame(request, b'{"x":%s}' % token))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "JSON_FINITE"
    finish(None, server)


def test_positive_exponent_overflow_rejected(fake_server) -> None:
    """1e400 overflows binary64; the lossless parser must refuse the frame."""

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_result_frame(request, b'{"x":1e400}'))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "VALUE_FINITE"
    finish(None, server)


def test_negative_exponent_underflow_pins_to_signed_zero(fake_server) -> None:
    """1e-999 underflows: the parser yields +0.0 rather than refusing."""

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_result_frame(request, b'{"x":1e-999}'))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    try:
        result = client.request("hello", {})
    finally:
        finish(client, server)
    assert result == {"x": 0.0} and type(result["x"]) is float
    assert math.copysign(1.0, result["x"]) == 1.0  # -1e-999 would keep the sign


def test_utf8_bom_frame_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(b"\xef\xbb\xbf" + envelope(request, {}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "JSON_BOM"
    finish(None, server)


# ---------------------------------------------------------------------------
# framing: invalid UTF-8, incomplete EOF
# ---------------------------------------------------------------------------


def test_invalid_utf8_bytes_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(
            b'{"id":%d,"major":1,"minor":0,"protocol":"%s","result":{"s":"\xff\xfe"}}\n'
            % (request["id"], PROTOCOL.encode())
        )

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "JSON_INVALID"
    finish(None, server)


def test_incomplete_frame_then_eof_raises_adapter_eof(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        read_request(server, connection)
        connection.sendall(b'{"id":1,"major":1,"minor":0')  # no LF, then EOF
        connection.shutdown(socket.SHUT_WR)

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_EOF"
    assert client.tainted is True and client.socket is None
    finish(None, server)


def test_eof_without_any_response_bytes_raises_adapter_eof(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        read_request(server, connection)
        connection.shutdown(socket.SHUT_WR)

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_EOF"
    finish(None, server)


# ---------------------------------------------------------------------------
# framing: frame-size budget, the LF included
# ---------------------------------------------------------------------------


def bounded_frame(request: dict, limit: int, extra: int = 0) -> bytes:
    head = b'{"id":%d,"major":1,"minor":0,"protocol":"%s","result":{"pad":"' % (
        request["id"],
        PROTOCOL.encode(),
    )
    tail = b'"}}\n'  # close pad string, result object, envelope; LF is counted
    filler = limit - len(head) - len(tail) + extra
    assert filler >= 0
    return head + b"x" * filler + tail


@pytest.mark.parametrize(
    ("extra", "expected_code"),
    [(-1, None), (0, None), (1, "ADAPTER_FRAME")],
    ids=["one-under-limit", "exactly-at-limit-lf-included", "one-over-limit"],
)
def test_frame_budget_boundary_includes_lf(
    fake_server, extra: int, expected_code: str | None
) -> None:
    limit = 4096

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(bounded_frame(request, limit, extra))
        connection.recv(1024)  # drain or observe the client-side abort

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, max_frame_bytes=limit)
    if expected_code is None:
        try:
            result = client.request("hello", {})
            assert set(result) == {"pad"}
            assert client.tainted is False
        finally:
            finish(client, server)
    else:
        with pytest.raises(KernelError) as excinfo:
            client.request("hello", {})
        assert excinfo.value.code == expected_code
        assert client.tainted is True
        finish(None, server)


def test_oversized_frame_rejected_mid_stream(fake_server) -> None:
    limit = 1024 * 1024

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(bounded_frame(request, limit, extra=64))
        connection.recv(1024)

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, max_frame_bytes=limit)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_FRAME"
    assert client.tainted is True
    finish(None, server)


def test_oversized_request_rejected_and_taints(fake_server) -> None:
    """A request frame over the budget fails before any bytes reach the wire."""

    def handler(server: FakeServer, connection: socket.socket) -> None:
        return  # accept, then close: no request should ever be readable

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, max_frame_bytes=64)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {"pad": "x" * 128})
    assert excinfo.value.code == "RESOURCE_LIMIT"
    assert client.tainted is True
    assert server.received == []  # nothing was ever sent
    finish(client, server)


# ---------------------------------------------------------------------------
# envelope identity: wrong id / protocol / major / minor, booleans and floats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": 999},
        {"id": 1.0},
        {"id": True},
        {"protocol": "other/1"},
        {"major": 2},
        {"major": 1.0},
        {"major": True},
        {"minor": 1},
        {"minor": False},
    ],
)
def test_identity_mismatch_raises_adapter_identity_and_taints(
    fake_server, overrides
) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_envelope(request, **overrides))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_IDENTITY"
    assert client.tainted is True and client.socket is None
    finish(None, server)


@pytest.mark.parametrize("missing", ["protocol", "major", "minor", "id"])
def test_envelope_missing_required_member_rejected(fake_server, missing: str) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        frame.pop(missing)
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_ENVELOPE"
    finish(None, server)


def test_envelope_with_result_and_error_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        frame["error"] = {"code": "X"}
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_ENVELOPE"
    finish(None, server)


def test_envelope_with_unknown_member_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        frame["extra"] = 1
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_ENVELOPE"
    finish(None, server)


# ---------------------------------------------------------------------------
# framing: unsolicited trailing bytes
# ---------------------------------------------------------------------------


def test_unsolicited_trailing_frame_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(envelope(request, {}) + envelope(request, {}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_FRAME"
    assert client.tainted is True
    finish(None, server)


def test_unsolicited_trailing_partial_bytes_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(envelope(request, {}) + b'{"garbage":')

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_FRAME"
    finish(None, server)


# ---------------------------------------------------------------------------
# backend faults and result shapes
# ---------------------------------------------------------------------------


def test_backend_error_maps_to_adapter_backend_with_context(fake_server) -> None:
    backend_error = {"code": "E_NATIVE", "message": "native fault"}

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        del frame["result"]
        frame["error"] = backend_error
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_BACKEND"
    assert excinfo.value.context["operation"] == "hello"
    assert excinfo.value.context["backend"] == backend_error
    assert client.tainted is True
    finish(None, server)


def test_non_object_error_member_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        del frame["result"]
        frame["error"] = "boom"
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_SHAPE"
    finish(None, server)


def test_non_object_result_rejected(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_envelope(request, result=[1, 2]))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_SHAPE"
    finish(None, server)


def test_null_result_rejected_as_shape(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(raw_envelope(request, result=None))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_SHAPE"
    finish(None, server)


# ---------------------------------------------------------------------------
# request framing: canonical bytes, LF termination, sequence ids
# ---------------------------------------------------------------------------


def test_request_frame_is_canonical_and_lf_terminated(fake_server) -> None:
    payload = {"z": 1, "a": [1, 2], "m": {"b": 1, "a": 2}}

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(envelope(request, {}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    try:
        client.request("hello", payload)
    finally:
        finish(client, server)
    (raw,) = server.received
    assert raw.endswith(b"\n")
    expected = {
        "protocol": PROTOCOL,
        "major": 1,
        "minor": 0,
        "id": 1,
        "op": "hello",
        "payload": payload,
    }
    assert raw == canonical_json(expected, client.budget)  # sorted, no spaces


def test_sequence_ids_increment_across_requests(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        first = read_request(server, connection)
        assert first["op"] == "hello"
        connection.sendall(envelope(first, {"n": 1}))
        second = read_request(server, connection)
        assert second["id"] == first["id"] + 1
        assert second["op"] == "advance"
        connection.sendall(envelope(second, {"n": 2}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    try:
        assert client.request("hello", {}) == {"n": 1}
        assert client.request("advance", {}) == {"n": 2}
        assert client.sequence == 2
        assert client.tainted is False
    finally:
        finish(client, server)


# ---------------------------------------------------------------------------
# timeouts: per-operation deadlines are absolute
# ---------------------------------------------------------------------------


def test_stalled_backend_raises_adapter_timeout_quickly(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        read_request(server, connection)
        time.sleep(2.0)  # far beyond the 0.2s deadline
        try:
            connection.sendall(b"{}\n")
        except OSError:
            pass

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, timeouts={"hello": 0.2})
    start = time.monotonic()
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    elapsed = time.monotonic() - start
    assert excinfo.value.code == "ADAPTER_TIMEOUT"
    assert excinfo.value.context["operation"] == "hello"
    assert 0.1 < elapsed < 5.0
    assert client.tainted is True
    finish(None, server)


def test_slow_drip_hits_absolute_operation_deadline(fake_server) -> None:
    """Bytes keep arriving, so each recv succeeds yet the deadline still fires."""

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(b'{"id":%d,"major":1,"minor":0' % request["id"])
        for _ in range(60):  # 60 * 0.1s of dribble against a 0.5s deadline
            time.sleep(0.1)
            try:
                connection.sendall(b" ")
            except OSError:
                return

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL, timeouts={"hello": 0.5})
    start = time.monotonic()
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    elapsed = time.monotonic() - start
    assert excinfo.value.code == "ADAPTER_TIMEOUT"
    assert 0.4 < elapsed < 5.0  # bounded by the deadline, not the dribble
    assert client.tainted is True
    finish(None, server)


def test_unaccepted_listener_still_yields_adapter_timeout() -> None:
    """A listening-but-never-accepting backend exhausts the hello deadline."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind((HOST, 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        client = JsonLinesClient(HOST, port, PROTOCOL, timeouts={"hello": 0.2})
        start = time.monotonic()
        with pytest.raises(KernelError) as excinfo:
            client.request("hello", {})
        assert excinfo.value.code == "ADAPTER_TIMEOUT"
        assert time.monotonic() - start < 5.0
        assert client.tainted is True and client.socket is None
    finally:
        listener.close()


# ---------------------------------------------------------------------------
# connect failure and taint: no stateful retry
# ---------------------------------------------------------------------------


def test_refused_connect_raises_adapter_transport_and_taints() -> None:
    port = closed_port()
    client = JsonLinesClient(HOST, port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_TRANSPORT"
    assert excinfo.value.context["operation"] == "hello"
    assert isinstance(excinfo.value.__cause__, OSError)
    assert client.tainted is True and client.socket is None


def test_tainted_client_refuses_retry_without_connecting() -> None:
    port = closed_port()
    client = JsonLinesClient(HOST, port, PROTOCOL)
    with pytest.raises(KernelError) as first:
        client.request("hello", {})
    assert first.value.code == "ADAPTER_TRANSPORT"

    with pytest.raises(KernelError) as second:
        client.request("hello", {})
    assert second.value.code == "ADAPTER_TAINTED"
    assert second.value.context["operation"] == "hello"
    assert client.socket is None  # no third connection attempt happened


def test_abort_marks_tainted_and_blocks_requests() -> None:
    client = JsonLinesClient(HOST, 1, PROTOCOL)
    client.abort()
    assert client.tainted is True and client.socket is None
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_TAINTED"


def test_disconnect_closes_without_tainting(fake_server) -> None:
    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        connection.sendall(envelope(request, {}))

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    client.request("hello", {})
    assert client.tainted is False
    client.disconnect()
    assert client.socket is None and client.tainted is False
    finish(None, server)


def test_after_any_fault_retry_is_adapter_tainted(fake_server) -> None:
    """A backend fault also taints: the next request must not hit the socket."""

    def handler(server: FakeServer, connection: socket.socket) -> None:
        request = read_request(server, connection)
        frame = json.loads(envelope(request, {}))
        del frame["result"]
        frame["error"] = {"code": "E"}
        connection.sendall((json.dumps(frame) + "\n").encode())

    server = fake_server(handler)
    client = JsonLinesClient(HOST, server.port, PROTOCOL)
    with pytest.raises(KernelError) as excinfo:
        client.request("hello", {})
    assert excinfo.value.code == "ADAPTER_BACKEND"
    assert client.buffer == bytearray()  # frame bytes were consumed before abort

    with pytest.raises(KernelError) as retry:
        client.request("hello", {})
    assert retry.value.code == "ADAPTER_TAINTED"
    finish(None, server)
