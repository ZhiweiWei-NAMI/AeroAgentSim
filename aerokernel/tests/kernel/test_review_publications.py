"""Own publications remain resolvable while independent ingress delays seals."""

import pytest

from aerokernel import (
    BindingManifest,
    ClockMapping,
    IngressPolicy,
    IngressStream,
    Kernel,
    KernelError,
    LocalCause,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Timing,
    replay,
)
from aerokernel.codec import decode_record
from aerokernel.rpc import _projection, _view
from aerokernel.sdk import ContextEngine
from aerokernel.state import StateView


@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_own_operation_resolution_between_global_seals_including_rpc(provenance):
    class Fast(ContextEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "fast",
                    "fast",
                    timing=Timing("real_time"),
                    emits=("E",),
                    message_targets=("topic",),
                )
            )
            self.wakeup_ns = 5
            self.pending = None
            self.resolved = None

        def step(self, ctx):
            if self.pending is None:
                self.pending = (
                    ctx.view.invocation_ref,
                    ctx.emit("E", 1, topic="topic"),
                )
                self.before = ctx.view
                self.wakeup_ns = 7
            else:
                self.resolved = ctx.view.committed_operation(*self.pending)
                assert ctx.view.cut.index < self.resolved.record_index
                assert ctx.view.transaction_base_cut.index >= self.resolved.record_index
                remote = _view(_projection(ctx.view, self.partitions), kernel.budget)
                assert remote.cut == ctx.view.cut
                assert remote.native_input_cut == ctx.view.native_input_cut
                assert remote.committed_operation(*self.pending) == self.resolved
                self.resolving_view = ctx.view
                for view, code in ((ctx.view, "READ_FUTURE"), (remote, "CUT")):
                    with pytest.raises(KernelError, match=code):
                        view._cut(view.transaction_base_cut)
                    with pytest.raises(KernelError, match="CAUSE_LOCAL"):
                        view.committed_operation(self.pending[0], LocalCause(99))
                ctx.inputs = [self.resolved]
                ctx.emit("E", 2, topic="topic")
                self.wakeup_ns = None

    fast = Fast()
    slow = ContextEngine(Partition("slow", "slow", timing=Timing("real_time")))
    kernel = Kernel(
        provenance=provenance,
        ingress_streams=tuple(
            IngressStream(n, IngressPolicy(0), n + "-clock", (n,))
            for n in ("fast", "slow")
        ),
        mappings=tuple(
            ClockMapping(n + "-clock", n + "-native") for n in ("fast", "slow")
        ),
    )
    kernel.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("E", "event", {"type": "integer"}),)
        ),
        BindingManifest("operation-cut", "0"),
        (fast, slow),
    )
    try:
        kernel.start()
        kernel.advance_watermark(5, stream_id="fast")
        waits = []

        def wait(state):
            if state is None:
                return True
            waits.append(state)
            if len(waits) == 1:
                kernel.advance_watermark(7, stream_id="fast")
                return True
            return False

        kernel.run_until(10, on_wait=wait)
        assert fast.resolved is not None and kernel.sealed_ns == 0
        invocation, local = fast.pending
        with pytest.raises(KernelError, match="CAUSE_UNKNOWN"):
            fast.before.committed_operation(invocation, local)
        with pytest.raises(KernelError, match="CAUSE_FUTURE"):
            StateView(fast.resolving_view._store, fast.before.cut).committed_operation(
                invocation, local
            )
        with pytest.raises(KernelError, match="CAUSE_STATE_SCOPE"):
            StateView(fast.resolving_view._store, partition="slow").committed_operation(
                invocation, local
            )
        messages = sorted(kernel._store.messages.values(), key=lambda m: m.payload)
        assert messages[0].origin == fast.resolved
        if provenance == "full":
            assert messages[1].causes == (fast.resolved,)
        else:
            assert messages[1].causes == (
                kernel._store.latest_returned[("fast", "advance")],
            )
        restored = replay(kernel.journal.bytes)
        assert restored.records == kernel.records
        assert restored._store.messages == kernel._store.messages
        assert not restored._store.faulted
        # Full mode preserves the cited explicit cause on replay as well.
        if provenance == "full":
            item = next(
                i
                for r in kernel.records
                for i in r["items"]
                if i.get("kind") == "operation"
                and "message" in i
                and decode_record(i["message"]).payload == 2
            )
            assert decode_record(item["causes"]) == (fast.resolved,)
    finally:
        kernel.close()
