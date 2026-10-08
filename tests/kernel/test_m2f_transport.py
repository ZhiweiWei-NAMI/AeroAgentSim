"""Independent request ordering, terminal taint and paused host contracts."""

import io
import socket
import threading

import pytest

from aerokernel import KernelError
from aerokernel.rpc_transport import Framer, RPCConnection, envelope, serve_requests
from aerokernel.values import canonical_json


@pytest.mark.parametrize("blocked_op", ["hello", "reset"])
def test_concurrent_rejection_is_terminal_even_after_original_response(blocked_op):
    client, server = socket.socketpair()
    entered, release = threading.Event(), threading.Event()
    first_errors = []

    def respond():
        try:
            request = Framer(server).read()
            entered.set()
            assert release.wait(2)
            response = {
                k: request[k] for k in ("protocol", "major", "minor", "id", "op")
            }
            Framer(server).write({**response, "result": {}})
        finally:
            server.close()

    c = RPCConnection(client)
    if blocked_op == "reset":
        c.state = "hello"

    def original():
        try:
            c.call(blocked_op, {})
        except KernelError as error:
            first_errors.append(error.code)

    responder = threading.Thread(target=respond)
    caller = threading.Thread(target=original)
    responder.start()
    caller.start()
    try:
        assert entered.wait(2)
        with pytest.raises(KernelError, match="RPC_IN_FLIGHT"):
            c.call(blocked_op, {})
        release.set()
        caller.join(2)
        responder.join(2)
        assert not caller.is_alive() and not responder.is_alive()
        assert c.state == "tainted"
        assert first_errors == ["RPC_STATE"]
        with pytest.raises(KernelError, match="RPC_STATE"):
            c.call("reset" if blocked_op == "hello" else "advance", {})
        assert c.request_id == 1
    finally:
        release.set()
        client.close()


@pytest.mark.parametrize("ids", [(1, 7, 2**200), (2, 9, 100)])
def test_server_accepts_positive_increasing_ids_and_echoes_actual_id(ids):
    calls = []
    requests = b"".join(
        canonical_json(envelope(i, op, {}))
        for i, op in zip(ids, ("hello", "reset", "close"), strict=True)
    )
    output = io.BytesIO()
    serve_requests(lambda op, _: calls.append(op), io.BytesIO(requests), output)
    assert calls == ["hello", "reset", "close"]
    output.seek(0)
    framer = Framer(output)
    assert [framer.read()["id"] for _ in ids] == list(ids)


@pytest.mark.parametrize("bad", [1, 0, -1, True, 7.0])
def test_server_rejects_duplicate_decreasing_noninteger_ids(bad):
    calls = []
    requests = canonical_json(envelope(1, "hello", {})) + canonical_json(
        envelope(bad, "reset", {})
    )
    with pytest.raises(KernelError, match="RPC_IDENTITY"):
        serve_requests(
            lambda op, _: calls.append(op), io.BytesIO(requests), io.BytesIO()
        )
    assert calls == ["hello"]
