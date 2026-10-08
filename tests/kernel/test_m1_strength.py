"""Independent prefix/work expectations and post-call publication failures."""

from __future__ import annotations

import io

import pytest

from aerokernel import (
    BindingManifest,
    Emit,
    Instant,
    Journal,
    Kernel,
    KernelError,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    replay,
)
from aerokernel.journal import prefixes
from aerokernel.sdk import SimpleEngine


def test_transaction_flush_failure_after_actual_stateful_call():
    class Sink(io.BytesIO):
        fail = False

        def flush(self):
            if self.fail:
                raise OSError("transaction flush acknowledgment lost")
            super().flush()

    sink = Sink()

    class Writer(SimpleEngine):
        called = 0

        def initialize(self, view):
            self.wakeup_ns = 1
            return ()

        def integrate(self, view):
            self.called += 1
            self.wakeup_ns = None
            sink.fail = True
            return (Emit("event", "event", "empty", view.instant, 7),)

    writer = Writer(Partition("p", "e", emits=("event",), message_targets=("empty",)))
    k = Kernel(journal=Journal(sink))
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("event", schema={"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (writer,),
    )
    k.start()
    with pytest.raises(KernelError, match="JOURNAL_IO"):
        k.run_until(1)
    assert writer.called == 1
    assert not k._store.messages
    assert k.records[-1]["type"] == "invocations"
    assert k._store.pending_intents == {
        "p": next(iter(k._store.pending_intents.values()))
    }
    assert replay(k.journal.bytes).incomplete
    # The storage line whose acknowledgment was lost is authoritative on recovery.
    recovered = replay(sink.getvalue())
    assert recovered.incomplete
    assert next(iter(recovered._store.messages.values())).payload == 7
    with pytest.raises(KernelError):
        k.run_until(2)
    assert writer.called == 1


def test_every_prefix_pending_and_authorized_work_matches_authored_schedule():
    class Publisher(SimpleEngine):
        def initialize(self, view):
            return (Emit("event", "event", "topic", Instant(0), 7),)

    engines = (
        Publisher(
            Partition(
                "publisher", "publisher", emits=("event",), message_targets=("topic",)
            )
        ),
        SimpleEngine(Partition("a", "a", subscribes=("topic",), message_lag_ns=2)),
        SimpleEngine(Partition("b", "b", subscribes=("topic",), message_lag_ns=7)),
    )
    k = Kernel()
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("event", schema={"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        engines,
    )
    snapshots = {}
    publish = k._publish

    def capture(state, record):
        publish(state, record)
        snapshots[state.cut.index] = (
            [(w.recipient, w.eligible.ns, w.eligible.microstep) for w in state.work],
            {ref: intent["status"] for ref, intent in state.intents.items()},
        )

    k._publish = capture
    snapshots[0] = ([], {})
    k.start()
    k.run_until(7)
    published = False
    dispatched = set()
    expected_statuses = {}
    for prefix in prefixes(k.journal.bytes):
        recovered = replay(prefix)
        index = recovered.view().cut.index
        if index:
            record = recovered.records[-1]
            if record["type"] == "transaction" and record["phase"] == "reset":
                published = True
            if record["type"] == "invocations":
                if record["phase"] == "react":
                    recipient = "a" if recovered.view().cut.instant.ns == 2 else "b"
                    assert record["selected"] == [recipient]
                    dispatched.add(recipient)
                # Intent identities are coordinates; expected status progression is
                # authored independently of replay's status update implementation.
                from aerokernel import ItemRef

                for item_index, item in enumerate(record["items"]):
                    if item["kind"] == "intent":
                        expected_statuses[ItemRef(index, item_index)] = "pending"
            elif record["type"] == "transaction":
                expected_statuses = {ref: "returned" for ref in expected_statuses}
        expected_queue = (
            [(p, lag, 0) for p, lag in (("a", 2), ("b", 7)) if p not in dispatched]
            if published
            else []
        )
        actual = (
            [
                (w.recipient, w.eligible.ns, w.eligible.microstep)
                for w in recovered._store.work
            ],
            {ref: intent["status"] for ref, intent in recovered._store.intents.items()},
        )
        assert actual == (expected_queue, expected_statuses)
        assert actual == snapshots[index]
