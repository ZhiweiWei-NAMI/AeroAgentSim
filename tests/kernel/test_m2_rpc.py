"""Real socket transport, strict frames, engine replay and uncertain publication."""

import io
import socket
import threading
import time
from dataclasses import replace

import pytest

from aerokernel import (
    BindingManifest,
    Kernel,
    KernelError,
    MemoryRegistry,
    Partition,
    replay,
)
from aerokernel.rpc import RemoteEngine, serve_engine
from aerokernel.rpc_transport import Framer, RPCConnection, envelope, serve_requests
from aerokernel.sdk import SimpleEngine
from aerokernel.values import ResourceBudget


def service(handler=None, engine=None, **kwargs):
    client, server = socket.socketpair()
    failures = []

    def run():
        try:
            if engine is not None:
                serve_engine(engine, server, **kwargs)
            else:
                serve_requests(handler, server, **kwargs)
        except Exception as error:
            failures.append(error)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return client, thread, failures


@pytest.mark.parametrize(
    "line",
    [
        b'{"a":1,"a":2}\n',
        b'{"n":NaN}\n',
        b'{"n":Infinity}\n',
        b'{"x":"\xff"}\n',
        b"\xef\xbb\xbf{}\n",
        b"{}\r\n",
        b"[]\n",
        b"\n",
        b"{}",
        b'{"x": 1}\n',
    ],
)
def test_strict_framing(line):
    if line == b'{"x": 1}\n':
        assert Framer(io.BytesIO(line)).read() == {"x": 1}
    else:
        with pytest.raises(KernelError):
            Framer(io.BytesIO(line)).read()


def test_framing_size_includes_lf_and_preserves_big_integers_and_successors():
    n = 2**3000 + 123
    stream = io.BytesIO()
    framer = Framer(stream)
    framer.write({"n": n})
    stream.seek(0)
    assert framer.read()["n"] == n
    assert (
        Framer(io.BytesIO(b"{}\n{}\n"), budget=ResourceBudget(frame_bytes=3)).read()
        == {}
    )
    with pytest.raises(KernelError):
        Framer(io.BytesIO(b"{}\n"), budget=ResourceBudget(frame_bytes=2)).read()
    with pytest.raises(KernelError):
        Framer(io.BytesIO(b"xxxxx"), budget=ResourceBudget(frame_bytes=3)).read()
    with pytest.raises(KernelError, match="RPC_FRAMING"):
        Framer(io.StringIO("{}\n")).read()


def test_fragmented_response_and_total_deadline_wrong_ids_eof_oversize():
    for fault in (
        "fragment",
        "id",
        "version",
        "eof",
        "timeout",
        "oversize",
        "shape",
        "error",
    ):
        client, server = socket.socketpair()

        def respond(fault=fault, server=server):
            try:
                request = Framer(server).read()
                response = {
                    k: request[k] for k in ("protocol", "major", "minor", "id", "op")
                }
                response["result"] = {"n": 2**100}
                if fault == "id":
                    response["id"] += 1
                elif fault == "version":
                    response["minor"] += 1
                elif fault == "eof":
                    server.sendall(b"{")
                    return
                elif fault == "timeout":
                    time.sleep(0.12)
                    return
                elif fault == "oversize":
                    server.sendall(b"x" * 1024)
                    return
                elif fault == "shape":
                    response["extra"] = 1
                elif fault == "error":
                    response.pop("result")
                    response["error"] = {"code": "NATIVE", "message": "actual failure"}
                from aerokernel.values import canonical_json

                line = canonical_json(response)
                for byte in line:
                    server.sendall(bytes([byte]))
            finally:
                server.close()

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        connection = RPCConnection(
            client, budget=ResourceBudget(frame_bytes=512), timeouts={"hello": 0.06}
        )
        if fault == "fragment":
            assert connection.call("hello", {}) == {"n": 2**100}
            assert connection.state == "hello"
        else:
            with pytest.raises(KernelError):
                connection.call("hello", {})
            assert connection.state == "tainted"
            with pytest.raises(KernelError, match="RPC_STATE"):
                connection.call("hello", {})
            assert connection.request_id == 1
        thread.join(0.2)
        client.close()


def test_complete_lifecycle_big_integer_and_identifiable_server_timeout():
    client, thread, failures = service(lambda op, value: value)
    c = RPCConnection(client)
    for op in ("hello", "reset", "horizon", "advance", "react", "close"):
        assert c.call(op, {"n": 2**3000}) == {"n": 2**3000}
    thread.join(1)
    assert failures == [] and c.state == "closed"
    client.close()
    client, thread, failures = service(
        lambda op, value: time.sleep(0.1), timeouts={"hello": 0.01}
    )
    c = RPCConnection(client)
    with pytest.raises(KernelError, match="RPC_REMOTE"):
        c.call("hello", {})
    thread.join(1)
    assert failures[0].code == "RPC_TIMEOUT"
    client.close()


def test_sdk_engine_runs_through_remote_proxy_and_replays_without_server():
    client, thread, failures = service(engine=SimpleEngine(Partition("p", "e")))
    remote = RemoteEngine(client)
    k = Kernel()
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    k.start()
    k.run_until(20)
    assert replay(k.journal.bytes).records == k.records
    k.close()
    k.close()
    thread.join(1)
    assert failures == [] and remote.connection.state == "closed"
    client.close()


def test_wrong_batch_native_cut_taints_and_never_partially_publishes():
    class Bad(SimpleEngine):
        def advance(self, partition, to, view):
            return replace(super().advance(partition, to, view), native_reached_ns=3)

    client, thread, failures = service(engine=Bad(Partition("p", "e")))
    remote = RemoteEngine(client)
    k = Kernel()
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    k.start()
    before = k.view().cut
    with pytest.raises(KernelError, match="RPC_BATCH"):
        k.run_until(20)
    assert remote.connection.state == "tainted"
    assert not any(
        r["type"] == "transaction" and r["phase"] == "advance" for r in k.records
    )
    assert k._store.frontiers["p"][1] == 0 and k.view().cut.index > before.index
    assert replay(k.journal.bytes).records == k.records
    client.close()
    thread.join(1)


@pytest.mark.parametrize(
    "policy", [{"hello": 0}, {"hello": float("nan")}, {"invalid": 1}, {"hello": True}]
)
def test_invalid_deadlines(policy):
    with pytest.raises(KernelError, match="RPC_POLICY"):
        RPCConnection(io.BytesIO(), timeouts=policy)


def test_pre_handshake_state_fault_short_write_and_invalid_server_envelope():
    c = RPCConnection(io.BytesIO())
    with pytest.raises(KernelError, match="RPC_STATE"):
        c.call("advance", {})

    class Broken:
        def write(self, data):
            return 0

    with pytest.raises(KernelError, match="RPC_WRITE"):
        Framer(io.BytesIO(), Broken()).write({})
    for request in (
        envelope(2, "hello", {}),
        envelope(1, "unknown", {}),
        {**envelope(1, "hello", {}), "extra": 1},
        envelope(1, "advance", {}),
    ):
        from aerokernel.values import canonical_json

        with pytest.raises(KernelError):
            serve_requests(
                lambda op, x: x, io.BytesIO(canonical_json(request)), io.BytesIO()
            )


def test_remote_toy_fields_actions_feedback_dirty_and_native_cuts_match_local():
    from examples.two_engine_toy import ITEM, MS, ORDER, make_toy

    reference = make_toy(seed=123)
    reference.start()
    reference.run_until(13 * MS)
    unstarted = make_toy(seed=123)
    clients, threads, errors, engines = [], [], [], []
    for engine in unstarted._engines.values():
        client, thread, faults = service(engine=engine)
        clients.append(client)
        threads.append(thread)
        errors.append(faults)
        engines.append(RemoteEngine(client))
    remote = Kernel(root_seed=123, configuration=dict(unstarted.configuration))
    remote.bind(unstarted._store.registry, unstarted._store.manifest, tuple(engines))
    remote.start()
    remote.run_until(13 * MS)
    for key in ((ITEM, "position_truth"), (ITEM, "in_zone"), (ORDER, "order_status")):
        assert remote.view().field(
            key, remote.view().cut.instant
        ) == reference.view().field(key, reference.view().cut.instant)
    assert remote._store.actions.states == reference._store.actions.states
    assert remote.records == reference.records  # Transport is outside state semantics.
    assert replay(remote.journal.bytes).records == remote.records
    remote.close()
    for client, thread, faults in zip(clients, threads, errors, strict=True):
        thread.join(1)
        client.close()
        assert faults == []


def test_real_subprocess_binary_stdio_is_protocol_only():
    import subprocess
    import sys

    source = (
        "import sys; from aerokernel.rpc import serve_engine; "
        "from aerokernel.sdk import SimpleEngine; from aerokernel import Partition; "
        'serve_engine(SimpleEngine(Partition("p", "e")), '
        "sys.stdin.buffer, sys.stdout.buffer)"
    )
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    remote = RemoteEngine(process.stdout, process.stdin)
    k = Kernel()
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    k.start()
    k.run_until(3)
    k.close()
    assert process.wait(timeout=2) == 0
    assert process.stderr.read() == b""
    assert replay(k.journal.bytes).records == k.records
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close()


@pytest.mark.parametrize(
    "fault", ["id", "eof", "oversize", "timeout", "token", "duplicate"]
)
def test_wire_fault_after_real_native_work_keeps_all_facts_unpublished(fault):
    import json

    from aerokernel import (
        BindingRule,
        Create,
        EntityRef,
        FactWrite,
        FieldDescriptor,
        Interval,
        LifecycleRule,
        Stamp,
        TypeDescriptor,
    )

    ref = EntityRef("r", "e", "entity", 0, "T")

    class Native(SimpleEngine):
        def __init__(self):
            super().__init__(Partition("p", "e", produces=("x",), lifecycle=True))
            self.wakeup_ns = 20
            self.actual = []

        def write(self, view, value):
            return FactWrite(
                (ref, "x"),
                value,
                Stamp("canonical", view.instant.ns, 1, "canonical"),
                Interval(view.instant, None),
            )

        def initialize(self, view):
            return (Create(ref), self.write(view, 0))

        def integrate(self, view):
            self.actual.append(view.instant.ns)
            self.wakeup_ns = None
            return (self.write(view, 9),)

    client, server = socket.socketpair()

    class FaultWire:
        def recv(self, count):
            return server.recv(count)

        def sendall(self, data):
            value = json.loads(data)
            if value["op"] == "advance":
                if fault == "id":
                    value["id"] += 1
                elif fault == "token":
                    value["result"]["token"] = "wrong"
                elif fault == "duplicate":
                    server.sendall(
                        b'{"id":' + str(value["id"]).encode("ascii") + b"," + data[1:]
                    )
                    return
                elif fault == "eof":
                    server.sendall(data[: len(data) // 2])
                    server.shutdown(socket.SHUT_RDWR)
                    return
                elif fault == "oversize":
                    server.sendall(b"x" * 8192)
                    return
                else:
                    time.sleep(0.12)
                from aerokernel.values import canonical_json

                data = canonical_json(value)
            server.sendall(data)

    native = Native()
    errors = []

    def run():
        try:
            serve_engine(native, FaultWire(), budget=ResourceBudget(frame_bytes=8192))
        except Exception as error:
            errors.append(error)
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    remote = RemoteEngine(
        client, budget=ResourceBudget(frame_bytes=8192), timeouts={"advance": 0.04}
    )
    k = Kernel(budget=ResourceBudget(frame_bytes=8192))
    registry = MemoryRegistry(
        (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
    )
    manifest = BindingManifest(
        "r",
        "e",
        (ref,),
        rules=(BindingRule("p", "T", ("x",)),),
        lifecycle=(LifecycleRule("p", "T"),),
    )
    k.bind(registry, manifest, (remote,))
    k.start()
    with pytest.raises(KernelError):
        k.run_until(20)
    assert native.actual == [20]
    assert k.view().field((ref, "x"), k.view().cut.instant).value == 0
    assert k._store.frontiers["p"][1] == 0
    assert k.records[-1]["rpc"]["op"] == "advance"
    assert replay(k.journal.bytes).records == k.records
    k.close()  # Tainted connections permit bounded resource cleanup only.
    thread.join(0.3)
    assert remote.connection.state == "closed"


def test_projection_excludes_undeclared_fields_and_freezes_frame_history():
    from aerokernel.rpc import _projection, _view
    from aerokernel.state import StateView
    from tests.kernel.test_m2_sampling import setup

    k, evaluator, _ = setup(child=True)
    k.start()
    k.run_until(1)
    view = StateView(k._store, partition="eval")
    projection = _projection(view, (evaluator.partition,))
    assert [d["id"] for d in projection["registry"]["fields"]] == ["y"]
    received = _view(projection, k.budget)
    frames = received.sample_frames("parent")
    assert frames == view.sample_frames("parent")
    with pytest.raises(TypeError):
        frames[-1].frame.result["value"] = 0
    from tests.kernel.test_m2_sampling import REF

    with pytest.raises(KernelError):
        received.field((REF, "x"), received.instant)
    with pytest.raises(KernelError, match="READ_UNDECLARED"):
        received.sample_frames("child")


def test_relation_projection_retains_heads_closed_versions_and_obligations():
    from aerokernel import CloseEdge, EndObligation, Instant
    from aerokernel.rpc import _projection, _view
    from aerokernel.state import StateView
    from tests.kernel.test_m2_relations import edge, obligation, setup

    k, owner, _ = setup((edge(), obligation()), (CloseEdge("a"), EndObligation("o")))
    k.start()
    k.run_until(3)
    view = StateView(k._store, partition="owner")
    reconstructed = _view(_projection(view, (owner.partition,)), k.budget)
    assert reconstructed.relation_history("a") == view.relation_history("a")
    assert reconstructed.obligation_history("o") == view.obligation_history("o")
    assert reconstructed.relations("R", Instant(3)) == ()


def test_malformed_error_types_and_pinned_profile_corruption_are_rejected():
    from aerokernel.rpc_transport import PROTOCOL
    from aerokernel.values import canonical_json

    for malformed in (
        {"code": None, "message": "failure"},
        {"code": "NATIVE", "message": 0},
    ):
        response = {
            "protocol": PROTOCOL,
            "major": 1,
            "minor": 0,
            "id": 1,
            "op": "hello",
            "error": malformed,
        }
        c = RPCConnection(io.BytesIO(canonical_json(response)), io.BytesIO())
        with pytest.raises(KernelError, match="RPC_RESPONSE"):
            c.call("hello", {})
        assert c.state == "tainted"
    client, thread, faults = service(engine=SimpleEngine(Partition("p", "e")))
    engine = RemoteEngine(client)
    k = Kernel()
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (engine,))
    k.start()
    for key, value in (
        ("major", True),
        ("minor", False),
        ("contract", {}),
        ("timeouts", {}),
    ):
        import copy

        header = copy.deepcopy(k.header)
        header["engine_profiles"]["e"][key] = value
        with pytest.raises(KernelError, match="JOURNAL_RPC"):
            Kernel._from_header(header)
    k.close()
    thread.join(1)
    client.close()
    assert faults == []
