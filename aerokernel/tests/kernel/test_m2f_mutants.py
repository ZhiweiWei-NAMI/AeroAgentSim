"""Behavioral witnesses for three previously surviving protocol/profile mutants."""

import threading

import pytest

from aerokernel import (
    BindingManifest,
    Cut,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    Partition,
    SampleFrame,
)
from aerokernel.profiles.sampled import Evaluation, entered
from aerokernel.rpc import RemoteEngine
from aerokernel.sdk import SimpleEngine
from tests.kernel.test_m2_rpc import service


def frame(ns, value, applicable):
    return SampleFrame(
        "c",
        ns,
        Cut(ns, Instant(ns)),
        {},
        {},
        Evaluation("known", value, applicability=applicable).to_data(),
        {},
    )


def test_entered_requires_applicable_prior_false_frame():
    current = frame(1, True, True)
    unresolved = entered(current, (frame(0, False, False),))
    assert unresolved.status == "unresolved" and unresolved.value is None
    control = entered(current, (frame(0, False, True),))
    assert control.status == "known" and control.value is True


def test_changed_contract_on_actual_advance_prevents_native_call(monkeypatch):
    native = []

    class Tracked(SimpleEngine):
        def integrate(self, view):
            native.append(view.instant.ns)
            self.wakeup_ns = None
            return ()

    engine = Tracked(Partition("p", "e"))
    engine.wakeup_ns = 1
    client, thread, failures = service(engine=engine)
    remote = RemoteEngine(client)
    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    try:
        k.start()
        original = remote.connection.call
        altered_calls = []

        def altered(op, payload):
            if op == "advance":
                altered_calls.append(op)
                payload = {
                    **payload,
                    "contract": {
                        "registry_digest": "changed",
                        "manifest_digest": "changed",
                    },
                }
            return original(op, payload)

        monkeypatch.setattr(remote.connection, "call", altered)
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            k.run_until(1)
        assert altered_calls == ["advance"] and native == []
        thread.join(1)
        assert failures[0].code == "RPC_CONTRACT"
        assert remote.connection.state == "tainted"
    finally:
        k.close()
        client.close()
        thread.join(1)
        assert not thread.is_alive()


def test_cleanup_waits_behind_timed_out_native_reset(monkeypatch):
    active, release, close_started, cleanup_started = (
        threading.Event() for _ in range(4)
    )
    overlap = []

    class Blocked(SimpleEngine):
        def reset(self, context, view):
            active.set()
            assert release.wait(2)
            active.clear()
            return super().reset(context, view)

        def close(self):
            overlap.append(active.is_set())
            close_started.set()

    import aerokernel.rpc as rpc

    original = rpc.bounded

    def tracked(call, timeout, **context):
        if context.get("op") == "cleanup":
            cleanup_started.set()
        return original(call, timeout, **context)

    monkeypatch.setattr(rpc, "bounded", tracked)
    client, thread, failures = service(
        engine=Blocked(Partition("p", "e")), timeouts={"reset": 0.05, "close": 0.5}
    )
    remote = RemoteEngine(client)
    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    try:
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            k.start()
        assert active.is_set() and cleanup_started.wait(1)
        assert not close_started.wait(0.05), "cleanup must wait for the executing reset"
        assert active.is_set()
        release.set()
        thread.join(1)
        assert close_started.is_set() and overlap == [False]
        assert failures[0].code == "RPC_TIMEOUT"
    finally:
        release.set()
        k.close()
        client.close()
        thread.join(1)
        assert not thread.is_alive()
