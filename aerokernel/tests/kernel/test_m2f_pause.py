"""Paused decision waits use bounded declared leases, never native calls."""

import threading

import pytest

from aerokernel import (
    BindingManifest,
    Instant,
    Kernel,
    KernelError,
    MemoryRegistry,
    Partition,
    PausePolicy,
    replay,
)
from aerokernel.journal import prefixes
from aerokernel.rpc import RemoteEngine
from aerokernel.sdk import SimpleEngine
from aerokernel.values import canonical_json
from tests.kernel.test_m2_rpc import service


def make(policy=None, server_policy=None):
    closed = threading.Event()
    native = []

    class Tracked(SimpleEngine):
        def integrate(self, view):
            native.append(view.instant.ns)
            return ()

        def close(self):
            closed.set()

    timeouts = dict.fromkeys(
        ("hello", "reset", "horizon", "advance", "react", "close"), 0.1
    )
    client, thread, failures = service(
        engine=Tracked(Partition("p", "e")),
        timeouts=timeouts,
        pause_policy=server_policy or policy,
    )
    remote = RemoteEngine(client, timeouts=timeouts, pause_policy=policy)
    kernel = Kernel(provenance="full")
    kernel.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    kernel.start()
    return kernel, client, thread, failures, closed, native


def finish(k, client, thread):
    k.close()
    client.close()
    thread.join(1)
    assert not thread.is_alive()


def test_default_silent_pause_outlives_operation_deadline():
    k, client, thread, failures, closed, native = make()
    try:
        assert not closed.wait(0.2)
        k.run_until(1)
        assert failures == []
        assert k._store.frontiers["p"][0].ns == 1
    finally:
        finish(k, client, thread)


def test_declared_hold_is_journaled_and_preserves_world_and_native_time():
    policy = PausePolicy(idle_timeout_s=0.15, max_hold_s=0.6)
    k, client, thread, failures, closed, native = make(policy)
    try:
        frontiers = k._store.frontiers.copy()
        k.hold_wall_clock(0.5, "LLM decision")
        assert not closed.wait(0.25)
        assert native == [] and k._store.frontiers == frontiers
        records = [r for r in k.records if r["type"] == "wall_clock_hold"]
        assert [r["status"] for r in records] == ["requested", "acknowledged"]
        assert records[1]["intent_index"] == records[0]["index"]
        assert records[1]["reason"] == "LLM decision"
        for prefix in prefixes(k.journal.bytes):
            restored = replay(prefix)
            assert restored._store.cut.index == len(restored.records)
            if restored.records and restored.records[-1]["type"] == "wall_clock_hold":
                assert restored.incomplete == (
                    restored.records[-1]["status"] == "requested"
                )
        k.run_until(1)
        assert failures == []
        assert replay(k.journal.bytes).records == k.records
    finally:
        finish(k, client, thread)


@pytest.mark.parametrize("duration", [0, -1, True, float("nan"), float("inf"), 0.7])
def test_invalid_hold_rejected_before_journal_or_transport(duration):
    k, client, thread, _, _, _ = make(PausePolicy(1, 0.6))
    try:
        before = k.view().cut
        with pytest.raises(KernelError, match="RPC_HOLD"):
            k.hold_wall_clock(duration, "decision")
        assert k.view().cut == before
    finally:
        finish(k, client, thread)


def test_lease_expires_without_advancement_or_fabricated_success():
    k, client, thread, failures, closed, native = make(PausePolicy(0.3, 0.4))
    try:
        k.hold_wall_clock(0.15, "decision")
        assert closed.wait(0.6)
        thread.join(1)
        assert not thread.is_alive()
        assert native == []
        assert failures[0].code == "RPC_TIMEOUT"
        with pytest.raises(KernelError, match="RPC_EOF"):
            k.run_until(1)
        assert replay(k.journal.bytes).records == k.records
    finally:
        finish(k, client, thread)


def test_unnegotiated_hold_and_corrupt_replay_are_rejected():
    k, client, thread, _, _, _ = make()
    try:
        with pytest.raises(KernelError, match="RPC_HOLD"):
            k.hold_wall_clock(1, "decision")
    finally:
        finish(k, client, thread)
    k, client, thread, _, _, _ = make(PausePolicy(1, 1))
    try:
        k.hold_wall_clock(0.5, "decision")
        records = list(k.records)
        records[-1] = {**records[-1], "intent_index": True}
        with pytest.raises(KernelError):
            replay(
                canonical_json(k.header) + b"".join(canonical_json(r) for r in records)
            )
        records[-1] = {**k.records[-1], "duration_s": 2}
        with pytest.raises(KernelError, match="RPC_HOLD"):
            replay(
                canonical_json(k.header) + b"".join(canonical_json(r) for r in records)
            )
    finally:
        finish(k, client, thread)


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_pause_policy_is_finite_and_positive(value):
    with pytest.raises(KernelError, match="RPC_POLICY"):
        PausePolicy(value, 1)
    with pytest.raises(KernelError, match="RPC_POLICY"):
        PausePolicy(1, value)


def test_mismatched_declaration_fails_handshake_before_reset():
    client, thread, failures = service(
        engine=SimpleEngine(Partition("p", "e")), pause_policy=PausePolicy(1, 1)
    )
    try:
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            RemoteEngine(client, pause_policy=PausePolicy(1, 2))
        thread.join(1)
        assert failures[0].code == "RPC_POLICY"
    finally:
        client.close()


def test_hold_wire_fault_leaves_unacknowledged_intent_and_replays(monkeypatch):
    k, client, thread, _, _, native = make(PausePolicy(1, 1))
    remote = k._engines["e"]
    original = remote.connection.call

    def altered(op, payload):
        if op == "hold":
            payload = {**payload, "contract": {"registry_digest": "changed"}}
        return original(op, payload)

    monkeypatch.setattr(remote.connection, "call", altered)
    try:
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            k.hold_wall_clock(0.5, "decision")
        assert native == []
        assert [r["status"] for r in k.records if r["type"] == "wall_clock_hold"] == [
            "requested"
        ]
        assert replay(k.journal.bytes).records == k.records
    finally:
        finish(k, client, thread)


def test_host_abort_unblocks_timed_out_native_work_before_serial_cleanup():
    active, release, aborted, closed = (threading.Event() for _ in range(4))
    overlap = []

    class Blocked(SimpleEngine):
        def reset(self, context, view):
            active.set()
            assert release.wait(2)
            active.clear()
            return super().reset(context, view)

        def close(self):
            overlap.append(active.is_set())
            closed.set()

    def abort():
        aborted.set()
        release.set()

    client, thread, failures = service(
        engine=Blocked(Partition("p", "e")),
        timeouts={"reset": 0.05, "close": 0.5},
        abort=abort,
        abort_timeout_s=0.1,
    )
    remote = RemoteEngine(client)
    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    try:
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            k.start()
        assert aborted.wait(1) and closed.wait(1)
        thread.join(1)
        assert not thread.is_alive()
        assert overlap == [False]
        assert active.is_set() is False
        assert failures[0].code == "RPC_TIMEOUT"
    finally:
        release.set()
        finish(k, client, thread)


def test_abort_failure_is_reported_and_cleanup_still_runs():
    closed = threading.Event()

    class Broken(SimpleEngine):
        def reset(self, context, view):
            raise KernelError("NATIVE", "actual reset failure")

        def close(self):
            closed.set()

    def abort():
        raise KernelError("ABORT_FAILED", "actual process stop failure")

    client, thread, failures = service(
        engine=Broken(Partition("p", "e")), abort=abort, abort_timeout_s=0.1
    )
    remote = RemoteEngine(client)
    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (remote,))
    try:
        with pytest.raises(KernelError, match="RPC_REMOTE"):
            k.start()
        thread.join(1)
        assert failures[0].code == "ABORT_FAILED" and closed.is_set()
    finally:
        finish(k, client, thread)


def test_partial_frame_uses_drain_budget_instead_of_idle_or_hold_budget():
    client, thread, failures = service(
        engine=SimpleEngine(Partition("p", "e")),
        pause_policy=PausePolicy(idle_timeout_s=1, max_hold_s=1, frame_timeout_s=0.05),
    )
    try:
        client.sendall(b"{")
        thread.join(0.5)
        assert not thread.is_alive()
        assert failures[0].code == "RPC_TIMEOUT"
        assert failures[0].context["op"] == "drain"
    finally:
        client.close()
        thread.join(1)


def test_hold_replay_rejects_time_advance_and_unacknowledged_interruption():
    from aerokernel.codec import encode

    k, client, thread, _, _, _ = make(PausePolicy(1, 1))
    try:
        k.hold_wall_clock(0.5, "decision")
        original = list(k.records)
        records = [
            *original[:-1],
            {**original[-1], "instant": encode(Instant(1))},
        ]
        with pytest.raises(KernelError, match="JOURNAL_HOLD"):
            replay(
                canonical_json(k.header) + b"".join(canonical_json(r) for r in records)
            )
        records = [*original[:-1], {**original[-1], "type": "run_limit", "limit_ns": 1}]
        with pytest.raises(KernelError, match="JOURNAL_HOLD"):
            replay(
                canonical_json(k.header) + b"".join(canonical_json(r) for r in records)
            )
        k.hold_wall_clock(0.4, "renewed decision lease")
        assert replay(k.journal.bytes).records == k.records
    finally:
        finish(k, client, thread)
