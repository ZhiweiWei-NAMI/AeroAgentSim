"""The reusable engine facade is a production SDK API."""

import pytest

from aerokernel import (
    ABSENT,
    BindingManifest,
    BindingRule,
    CommandRequest,
    EntityRef,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.sdk import (
    Command,
    ContextEngine,
    EngineContext,
    FactPolicy,
    dispatch_commands,
    handles,
)


def test_public_sdk_facade():
    from aerokernel.sdk import (
        ContextEngine,
        EngineContext,
        FactPolicy,
        handles,
    )

    assert all(
        callable(cls)
        for cls in (Command, ContextEngine, EngineContext, FactPolicy, handles)
    )


def test_committed_operation_resolves_before_echo_with_ownership_and_cut_bounds():
    from aerokernel.ids import LocalCause
    from aerokernel.rpc import _projection, _view
    from aerokernel.state import StateView
    from aerokernel.values import ResourceBudget

    class Evidence(ContextEngine):
        def bootstrap(self, ctx):
            self.invocation = ctx.view.invocation_ref
            self.local = ctx.emit("event", 7, target="p")
            self.before = ctx.view.cut
            ctx.wake_at(1)

        def on_inputs(self, ctx):
            if any(d.kind == "timer" for d in ctx.dirty):
                assert not ctx.inbox  # The event echo is still ten ns away.
                self.origin = ctx.view.committed_operation(self.invocation, self.local)
                remote = _view(_projection(ctx.view, self.partitions), ResourceBudget())
                assert (
                    remote.committed_operation(self.invocation, self.local)
                    == self.origin
                )
                with pytest.raises(KernelError, match="CAUSE_LOCAL"):
                    ctx.view.committed_operation(self.invocation, LocalCause(100))
                ctx.inputs = [self.origin]
                ctx.emit("event", 8, target="p")

    engine = Evidence(
        Partition("p", "e", emits=("event",), message_targets=("p",), message_lag_ns=10)
    )
    k = bind(engine, fields=False)
    k.start()
    k.run_until(1)
    emissions = [
        item
        for record in k.records
        for item in record["items"]
        if item["kind"] == "operation" and "message" in item
    ]
    from aerokernel.codec import decode_record

    assert decode_record(emissions[0]["message"]).origin == engine.origin
    assert decode_record(emissions[1]["causes"]) == (engine.origin,)
    with pytest.raises(KernelError, match="CAUSE_FUTURE"):
        k.view(engine.before).committed_operation(engine.invocation, engine.local)
    with pytest.raises(KernelError, match="CAUSE_STATE_SCOPE"):
        StateView(k._store, partition="other").committed_operation(
            engine.invocation, engine.local
        )
    rebuilt = replay(k.journal.bytes)
    assert (
        rebuilt.view().committed_operation(engine.invocation, engine.local)
        == engine.origin
    )


REF = EntityRef("sdk", "e", "entity", 0, "T")
POLICY = FactPolicy(
    lambda now: Stamp("canonical", now.ns, 1, "canonical"),
    lambda now: Interval(now, None),
)
INTEGER = {"type": "integer"}


def bind(engine, *, commands=(), fields=True, seed=7):
    k = Kernel(root_seed=seed)
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        (FieldDescriptor("x", "T", INTEGER),) if fields else (),
        (
            MessageDescriptor(
                "do",
                "command",
                INTEGER,
                result_schema=INTEGER,
                feedback_schema=INTEGER,
                cancel_support=True,
            ),
            MessageDescriptor("event", schema=INTEGER),
        ),
    )
    manifest = BindingManifest(
        "sdk",
        "e",
        (REF,) if fields else (),
        rules=(BindingRule("p", "T", ("x",)),) if fields else (),
        lifecycle=(LifecycleRule("p", "T"),) if fields else (),
        bootstrap_commands=commands,
    )
    k.bind(registry, manifest, (engine,))
    return k


class Lifecycle(ContextEngine):
    def __init__(self, **kwargs):
        super().__init__(
            Partition(
                "p",
                "e",
                produces=("x",),
                lifecycle=True,
                commands=("do",),
                rng_streams=("s",),
                emits=("event",),
                message_targets=("empty",),
            ),
            policies={"x": POLICY},
            **kwargs,
        )
        self.draw = None
        self.observed = []

    def bootstrap(self, ctx):
        self.draw = ctx.rng("s").random()
        ctx.create(REF)
        ctx.set(REF, "x", 7)
        ctx.wake_at(1, {"nested": [7]})

    def on_inputs(self, ctx):
        self.observed.append(ctx.get(REF, "x"))
        if ctx.now.ns == 1:
            ctx.retract(REF, "x", Interval(ctx.now, None), "real absence")
            ctx.wake_at(2)
        elif ctx.now.ns == 2 and any(d.kind == "timer" for d in ctx.dirty):
            ctx.remove(REF)


def test_context_lifecycle_retract_timers_rng_and_replay():
    engine = Lifecycle()
    k = bind(engine)
    old = k.start()
    k.run_until(2)
    assert engine.draw is not None
    assert engine.observed == [7, 7, ABSENT, ABSENT]
    assert old.field((REF, "x"), Instant(1)).value == 7
    assert k.view().field((REF, "x"), Instant(2, 100)) is ABSENT
    assert replay(k.journal.bytes).records == k.records


@pytest.mark.parametrize("terminal", ["succeed", "fail", "reject", "canceled"])
def test_handlers_ordered_heads_feedback_and_actual_cancel_cleanup(terminal):
    class Target(ContextEngine):
        retained = None

        @handles("do", lambda value: int(value))
        def do(self, ctx, command):
            self.retained = command
            if terminal == "reject":
                ctx.reject(command, 0)
                return
            ctx.accept(command)
            ctx.emit("event", 7, topic="empty")  # interleave a nonreceipt operation
            ctx.execute(command)
            ctx.feedback(command, command.payload)
            if terminal == "canceled":
                return
            ctx.wake_at(1)

        def on_inputs(self, ctx):
            dispatch_commands(
                self, ctx, tuple(d for d in ctx.inbox if d.message.kind == "command")
            )
            if self.retained is None:
                return
            for delivery in ctx.inbox:
                if delivery.message.kind == "cancel":
                    ctx.decide_cancel(self.retained, delivery, True)
                    ctx.wake_at(2)
            if ctx.now.ns == 1 and terminal in {"succeed", "fail"}:
                getattr(ctx, terminal)(self.retained, 42)
            if ctx.now.ns == 2 and terminal == "canceled":
                ctx.canceled(self.retained)

    target = Target(
        Partition(
            "p", "e", commands=("do",), emits=("event",), message_targets=("empty",)
        )
    )
    k = bind(target, commands=(CommandRequest("do", "p", Instant(0), 7),), fields=False)
    k.start()
    mid = target.retained.id
    if terminal == "canceled":
        assert k.cancel(mid).queued
    k.run_until(2)
    expected = {
        "succeed": "succeeded",
        "fail": "failed",
        "reject": "rejected",
        "canceled": "canceled",
    }[terminal]
    assert k.action(mid).status == expected
    assert replay(k.journal.bytes).action(mid) == k.action(mid)


def test_explicit_stamps_no_policy_and_no_hidden_authority():
    class Producer(ContextEngine):
        def bootstrap(self, ctx):
            ctx.create(REF)
            ctx.set(
                REF,
                "x",
                7,
                acquired=Stamp("canonical", 0, 1, "canonical"),
                valid=Interval(Instant(0), None),
            )

    k = bind(
        Producer(Partition("p", "e", lifecycle=True, produces=("x",), commands=("do",)))
    )
    k.start()
    ctx = EngineContext(k.view())
    with pytest.raises(KernelError, match="SDK_FACT_POLICY"):
        ctx.set(REF, "x", 9)
    with pytest.raises(KernelError, match="SDK_RNG_CONTEXT"):
        ctx.rng("s")
    with pytest.raises(KeyError):
        ctx.command("an undispatched correlation id")
    with pytest.raises(KernelError, match="SDK_ROUTE"):
        ctx.emit("event", 7)
    with pytest.raises(KernelError, match="SDK_ROUTE"):
        ctx.emit("event", 7, topic="a", target="b")
    with pytest.raises(KernelError, match="SDK_COMMAND_OPTIONS"):
        ctx.submit(
            CommandRequest("do", "p", Instant(1), 7, idempotency_key="host-only")
        )
    from types import SimpleNamespace

    mid = k.submit(CommandRequest("do", "p", Instant(1), 7))
    pending_ctx = EngineContext(k.view())
    with pytest.raises(KernelError, match="SDK_ACTION_HEAD"):
        pending_ctx._head(SimpleNamespace(id=mid))


def test_decorators_cannot_extend_partition_command_authority():
    class Undeclared(ContextEngine):
        @handles("do")
        def do(self, ctx, command):
            ctx.accept(command)

    with pytest.raises(KernelError, match="SDK_HANDLER"):
        Undeclared(Partition("p", "e"))


def test_duplicate_handlers_and_unsupported_inbox_fail_explicitly():
    class Duplicate(ContextEngine):
        @handles("do")
        def first(self, ctx, command):
            pass

        @handles("do")
        def second(self, ctx, command):
            pass

    with pytest.raises(KernelError, match="SDK_HANDLER"):
        Duplicate(Partition("p", "e", commands=("do",)))

    class Source(ContextEngine):
        def bootstrap(self, ctx):
            ctx.emit("event", 7, target="p")

    target = ContextEngine(Partition("p", "e"))
    source = Source(
        Partition("source", "source", emits=("event",), message_targets=("p",))
    )
    k = Kernel()
    k.bind(
        MemoryRegistry((), messages=(MessageDescriptor("event", schema=INTEGER),)),
        BindingManifest("sdk", "e"),
        (source, target),
    )
    with pytest.raises(KernelError, match="SDK_HANDLER"):
        k.start()
    assert replay(k.journal.bytes).incomplete


def test_context_remember_and_cancel_require_this_real_invocations_deliveries():
    captured = []

    class Target(ContextEngine):
        @handles("do")
        def do(self, ctx, command):
            captured.append(command)
            ctx.accept(command)

    k = bind(
        Target(Partition("p", "e", commands=("do",))),
        commands=(CommandRequest("do", "p", Instant(0), 7),),
        fields=False,
    )
    k.start()
    command = captured[0]
    ctx = EngineContext(k.view())
    with pytest.raises(KernelError, match="SDK_DISPATCH"):
        ctx.remember(command.delivery, lambda value: value)
    with pytest.raises(KernelError, match="SDK_DISPATCH"):
        ctx.decide_cancel(command, command.delivery, True)
    assert ctx.batch().transaction_base_cut == k.view().cut
    assert ctx.batch().operations == ()


def test_child_command_identity_is_learned_from_real_receipt_notifications():
    learned = []

    class Source(ContextEngine):
        def bootstrap(self, ctx):
            ctx.submit(CommandRequest("do", "target", ctx.now, 7))

        def on_inputs(self, ctx):
            for dirty in ctx.dirty:
                if dirty.kind == "receipt":
                    learned.append(
                        (dirty.payload["command_id"], dirty.payload["status"])
                    )

    class Target(ContextEngine):
        @handles("do")
        def do(self, ctx, command):
            assert ctx.command(command.id) == command
            ctx.accept(command)
            ctx.execute(command)
            ctx.succeed(command, command.payload)

    k = Kernel()
    k.bind(
        MemoryRegistry(
            (),
            messages=(
                MessageDescriptor("do", "command", INTEGER, result_schema=INTEGER),
            ),
        ),
        BindingManifest("sdk", "e"),
        (
            Source(
                Partition(
                    "source", "source", emits=("do",), message_targets=("target",)
                )
            ),
            Target(Partition("target", "target", commands=("do",))),
        ),
    )
    k.start()
    assert [status for _, status in learned] == [
        "submitted",
        "accepted",
        "executing",
        "succeeded",
    ]
    assert len({mid for mid, _ in learned}) == 1
    assert k.action(learned[0][0]).status == "succeeded"
    assert replay(k.journal.bytes).records == k.records


def test_context_preserves_each_explicit_time_axis_and_kernel_writer_authority():
    class Owner(ContextEngine):
        def bootstrap(self, ctx):
            ctx.create(REF)
            acquired = Stamp("canonical", -3, 1, "canonical")
            ctx.set(REF, "x", 7, acquired=acquired)
            assert ctx.ops[-1].acquired is acquired

    k = bind(
        Owner(
            Partition("p", "e", lifecycle=True, produces=("x",)), policies={"x": POLICY}
        )
    )
    k.start()
    assert k.view().field((REF, "x"), Instant(0)).acquired.numerator == -3
    ctx = EngineContext(k.view(), policies={"x": POLICY})
    interval = Interval(Instant(0), Instant(3))
    ctx.set(REF, "x", 7, valid=interval)
    assert ctx.ops[-1].valid is interval

    class Intruder(ContextEngine):
        def bootstrap(self, ctx):
            ctx.set(REF, "x", 99)

    owner = Owner(
        Partition("p", "e", lifecycle=True, produces=("x",)), policies={"x": POLICY}
    )
    thief = Intruder(Partition("q", "q", produces=("x",)), policies={"x": POLICY})
    invalid = Kernel()
    invalid.bind(k._store.registry, k._store.manifest, (owner, thief))
    with pytest.raises(KernelError, match="FIELD_OWNER"):
        invalid.start()
