"""Lifecycle, dispatch and routing checks independent of shared replay equality."""

from dataclasses import replace

import pytest

from aerokernel import (
    ABSENT,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Dependency,
    Emit,
    EntityRef,
    FieldDescriptor,
    Instant,
    ItemRef,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Receipt,
    Remove,
    RequestCancel,
    ScheduleTimer,
    Timing,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import decode_record
from aerokernel.sdk import SimpleEngine
from aerokernel.state import StateView
from aerokernel.values import canonical_json, parse_json


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("cross_type", (False, True))
@pytest.mark.parametrize("separate_batches", (False, True))
def test_same_text_id_remove_create_forbidden_in_both_orders_and_cohort_batches(
    reverse, cross_type, separate_batches
):
    old = EntityRef("r", "e", "same", 0, "Old")
    new = replace(old, generation=1, type_id="New" if cross_type else "Old")
    order = (Create(new), Remove(old)) if reverse else (Remove(old), Create(new))

    class Controller(SimpleEngine):
        def initialize(self, view):
            return (Create(old), ScheduleTimer("mutate", Instant(1), None))

        def on_react(self, view, inbox, dirty):
            if view.instant.ns != 1 or not any(d.kind == "timer" for d in dirty):
                return ()
            return order[:1] if separate_batches else order

    class Member(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            return order[1:] if separate_batches and view.instant.ns == 1 else ()

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry((TypeDescriptor("Old"), TypeDescriptor("New"))),
        BindingManifest(
            "r",
            "e",
            (old,),
            lifecycle=(LifecycleRule("a", "Old"), LifecycleRule("a", "New")),
            cohorts=(("a", "b"),),
        ),
        (
            Controller(Partition("a", "a", lifecycle=True)),
            Member(Partition("b", "b", lifecycle=True)),
        ),
    )
    before = k.start()
    with pytest.raises(KernelError, match="CREATE_REMOVE"):
        k.run_until(1)
    assert before.lifecycle(old).removed is None
    assert k.view().lifecycle(old).removed is None
    with pytest.raises(KernelError, match="ENTITY_UNKNOWN"):
        k.view().lifecycle(new)
    assert replay(k.journal.bytes).incomplete


@pytest.mark.parametrize("source", ("host", "partition"))
@pytest.mark.parametrize("fixed", (False, True))
def test_cancel_publication_eligibility_dispatch_apply_lag_then_latch(source, fixed):
    delivered = []

    class Target(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            out = []
            for d in inbox:
                delivered.append((d.message.kind, d.message.available, d.instant))
                if d.message.kind == "command":
                    out.append(
                        Receipt(
                            d.message.id,
                            "accepted",
                            causes=(d.dispatch_ref, view.action(d.message.id).head),
                        )
                    )
            return tuple(out)

    class Canceler(SimpleEngine):
        sent = False

        def initialize(self, view):
            return (ScheduleTimer("cancel", Instant(13 if fixed else 12), None),)

        def on_react(self, view, inbox, dirty):
            if self.sent or not any(d.kind == "timer" for d in dirty):
                return ()
            self.sent = True
            return (RequestCancel(command_id),)

    target = Target(
        Partition(
            "target",
            "target",
            commands=("do",),
            message_lag_ns=10,
            timing=Timing("fixed_step", 4) if fixed else Timing(),
        )
    )
    k = Kernel(provenance="full")
    engines = (
        (target,)
        if source == "host"
        else (
            target,
            Canceler(Partition("source", "source")),
        )
    )
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", cancel_support=True),)
        ),
        BindingManifest(
            "r", "e", cancel_sources=("source",) if source == "partition" else ()
        ),
        engines,
    )
    k.start()
    command_id = k.submit(CommandRequest("do", "target", Instant(1), None))
    k.run_until(12 if fixed else 11)
    if source == "host":
        assert k.cancel(command_id).queued
    k.run_until(24 if fixed else 23)
    command, cancel = delivered
    assert command[0] == "command" and command[1] == Instant(1)
    assert command[2].ns == (12 if fixed else 11)
    publication_ns = 13 if fixed else 12
    assert cancel[0] == "cancel" and cancel[1].ns == publication_ns
    assert cancel[2].ns == (24 if fixed else publication_ns + 10)
    enqueue = next(
        item
        for r in k.records
        for item in r["items"]
        if item.get("kind") == "enqueue"
        and k._store.messages[item["message"]].kind == "cancel"
    )
    assert decode_record(enqueue["eligible"]).ns == cancel[2].ns
    assert k.action(command_id).status == "accepted"  # delivery proves no cleanup


@pytest.mark.parametrize(
    "kind", ("offline_request", "reservation", "ingress_publish", "enqueue", "receipt")
)
def test_every_kernel_command_origin_kind_cannot_replace_actual_target_dispatch(kind):
    class Target(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("unrelated", Instant(1), None),)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns != 1:
                return ()
            ref = next(
                ItemRef(r["index"], i)
                for r in k.records
                for i, item in enumerate(r["items"])
                if item.get("kind") == kind
            )
            return (Emit("event", "event", "empty", view.instant, None, causes=(ref,)),)

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (),
            messages=(MessageDescriptor("do", "command"), MessageDescriptor("event")),
        ),
        BindingManifest(
            "r",
            "e",
            bootstrap_commands=(CommandRequest("do", "target", Instant(0), None),),
        ),
        (
            Target(
                Partition(
                    "target",
                    "e",
                    commands=("do",),
                    emits=("event",),
                    message_targets=("empty",),
                    message_lag_ns=10,
                )
            ),
        ),
    )
    k.start()
    if kind == "reservation":
        k.submit(CommandRequest("do", "target", Instant(20), None))
    with pytest.raises(KernelError, match="CAUSE_UNDISPATCHED"):
        k.run_until(1)
    assert all(m.kind != "event" for m in k._store.messages.values())


def test_nested_timer_lifecycle_receipt_dirty_and_retained_records_are_immutable():
    entity = EntityRef("r", "e", "id", 0, "T")
    mutated = []

    class Target(SimpleEngine):
        def initialize(self, view):
            return (Create(entity), ScheduleTimer("t", Instant(1), {"nested": [7]}))

        def on_react(self, view, inbox, dirty):
            for d in dirty:
                if d.kind == "lifecycle":
                    with pytest.raises(TypeError):
                        d.payload["$ref"]["id"] = "fake"
                    mutated.append(d.kind)
                if d.kind == "timer":
                    with pytest.raises(TypeError):
                        d.payload["nested"][0] = 99
                    mutated.append(d.kind)
            return tuple(
                Receipt(
                    d.message.id,
                    "accepted",
                    {"nested": [7]},
                    (d.dispatch_ref, view.action(d.message.id).head),
                )
                for d in inbox
            )

    class Origin(SimpleEngine):
        def initialize(self, view):
            return (Emit("command", "do", "p", view.instant, None),)

        def on_react(self, view, inbox, dirty):
            for d in dirty:
                if d.kind == "receipt" and d.payload["result"] is not None:
                    with pytest.raises(TypeError):
                        d.payload["result"]["nested"][0] = 99
                    mutated.append(d.kind)
            return ()

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            messages=(
                MessageDescriptor(
                    "do",
                    "command",
                    result_schema={
                        "type": "record",
                        "members": {
                            "nested": {"type": "array", "items": {"type": "integer"}}
                        },
                        "required": ["nested"],
                        "extra": False,
                    },
                ),
            ),
        ),
        BindingManifest(
            "r",
            "e",
            (entity,),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (
            Target(Partition("p", "e", lifecycle=True, commands=("do",))),
            Origin(
                Partition("origin", "origin", emits=("do",), message_targets=("p",))
            ),
        ),
    )
    old = k.start()
    old_records = k.records
    k.run_until(1)
    assert set(mutated) == {"timer", "lifecycle", "receipt"}
    recovered = replay(k.journal.bytes)
    assert recovered.records == k.records
    assert dict(recovered._store.intents) == dict(k._store.intents)
    assert k.records[: len(old_records)] == old_records
    assert old.lifecycle(entity).removed is None
    # Diagnostic copies also cannot lend containers back to the canonical log.
    old_records[0]["items"].clear()
    assert k.records[0]["items"]


def test_lagged_history_is_empty_but_explicit_precreation_cut_errors():
    entity = EntityRef("r", "e", "id", 0, "T")
    k = Kernel(provenance="full")

    class Controller(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("create", Instant(10), None),)

        def on_react(self, view, inbox, dirty):
            return (Create(entity),) if any(d.kind == "timer" for d in dirty) else ()

    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
        ),
        BindingManifest(
            "r",
            "e",
            rules=(BindingRule("a", "T", ("x",)),),
            lifecycle=(LifecycleRule("a", "T"),),
        ),
        (
            Controller(Partition("a", "a", produces=("x",), lifecycle=True)),
            SimpleEngine(Partition("b", "b", consumes=(Dependency("x", lag_ns=5),))),
        ),
    )
    old = k.start().cut
    k.run_until(10)
    scoped = StateView(k._store, partition="b", instant=Instant(10))
    assert scoped.field((entity, "x"), Instant(5)) is ABSENT
    assert scoped.history((entity, "x"), Instant(4), Instant(5)) == ()
    with pytest.raises(KernelError, match="ENTITY_UNKNOWN"):
        scoped.field((entity, "x"), Instant(5), old)
    with pytest.raises(KernelError, match="ENTITY_UNKNOWN"):
        scoped.history((entity, "x"), Instant(4), Instant(5), old)


def test_consistently_shifted_dispatch_behind_seal_is_not_replayable():
    class Target(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("tick", Instant(2), None),)

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry((), messages=(MessageDescriptor("do", "command"),)),
        BindingManifest("r", "e"),
        (Target(Partition("p", "e", commands=("do",))),),
    )
    k.start()
    k.submit(CommandRequest("do", "p", Instant(3), None))
    k.run_until(3)
    records = [parse_json(line) for line in k.journal.bytes.splitlines(keepends=True)]

    # Shift every coordinate local to the target dispatch/return together. Its
    # previous physical advance/frontier remains at 3; this is a scheduling error.
    def shift(value):
        if isinstance(value, dict):
            if value.get("$type") == "Instant" and value["fields"]["ns"] == 3:
                value["fields"]["ns"] = 2
            else:
                for child in value.values():
                    shift(child)
        elif isinstance(value, list):
            for child in value:
                shift(child)

    for record in records:
        if record.get("phase") == "react" and decode_record(record["instant"]).ns == 3:
            shift(record)
    with pytest.raises(KernelError):
        replay(b"".join(canonical_json(record) for record in records))


@pytest.mark.parametrize(
    "kind", ("lifecycle", "receipt", "feedback", "cancel_decision")
)
def test_historical_lifecycle_action_causes_require_declared_scope(kind):
    from aerokernel import CancelDecision, Feedback, LocalCause

    entity = EntityRef("r", "e", "other", 0, "T")

    class Owner(SimpleEngine):
        def initialize(self, view):
            return (Create(entity),)

        def on_react(self, view, inbox, dirty):
            operations = []
            for delivery in inbox:
                head = view.action(
                    delivery.message.id
                    if delivery.message.kind == "command"
                    else delivery.message.payload["command_id"]
                ).head
                if delivery.message.kind == "command":
                    operations.extend(
                        (
                            Receipt(
                                delivery.message.id,
                                "accepted",
                                causes=(delivery.dispatch_ref, head),
                            ),
                            Receipt(
                                delivery.message.id,
                                "executing",
                                causes=(delivery.dispatch_ref, LocalCause(0)),
                            ),
                            Feedback(
                                delivery.message.id,
                                7,
                                (delivery.dispatch_ref, LocalCause(1)),
                            ),
                        )
                    )
                else:
                    operations.append(
                        CancelDecision(
                            delivery.message.payload["command_id"],
                            delivery.message.id,
                            True,
                            causes=(delivery.dispatch_ref, head),
                        )
                    )
            return tuple(operations)

    class Probe(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("probe", Instant(3), None),)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns != 3:
                return ()
            proposal_type = {
                "lifecycle": "Create",
                "receipt": "Receipt",
                "feedback": "Feedback",
                "cancel_decision": "CancelDecision",
            }[kind]
            cause = next(
                ItemRef(record["index"], index)
                for record in k.records
                for index, item in enumerate(record["items"])
                if item.get("proposal", {}).get("$type") == proposal_type
            )
            return (Emit("event", "event", "empty", view.instant, None, (cause,)),)

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            messages=(
                MessageDescriptor(
                    "do",
                    "command",
                    feedback_schema={"type": "integer"},
                    cancel_support=True,
                ),
                MessageDescriptor("event"),
            ),
        ),
        BindingManifest(
            "r",
            "e",
            (entity,),
            lifecycle=(LifecycleRule("owner", "T"),),
            bootstrap_commands=(CommandRequest("do", "owner", Instant(0), None),),
        ),
        (
            Owner(Partition("owner", "owner", lifecycle=True, commands=("do",))),
            Probe(
                Partition(
                    "probe", "probe", emits=("event",), message_targets=("empty",)
                )
            ),
        ),
    )
    k.start()
    if kind == "cancel_decision":
        command_id = next(iter(k._store.actions.states))
        assert k.cancel(command_id).queued
    with pytest.raises(KernelError, match="CAUSE_STATE_SCOPE|READ_UNDECLARED"):
        k.run_until(3)


def test_own_historical_command_publication_is_not_a_self_dispatch():
    class E(SimpleEngine):
        def initialize(self, view):
            return (
                Emit("command", "do", "p", view.instant, None),
                ScheduleTimer("t", Instant(1), None),
            )

        def on_react(self, view, inbox, dirty):
            if view.instant.ns != 1:
                return ()
            origin = next(
                message.origin
                for message in k._store.messages.values()
                if message.kind == "command"
            )
            return (Emit("event", "event", "empty", view.instant, None, (origin,)),)

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (),
            messages=(MessageDescriptor("do", "command"), MessageDescriptor("event")),
        ),
        BindingManifest("r", "e"),
        (
            E(
                Partition(
                    "p",
                    "e",
                    commands=("do",),
                    emits=("do", "event"),
                    message_targets=("p", "empty"),
                    message_lag_ns=10,
                )
            ),
        ),
    )
    k.start()
    with pytest.raises(KernelError, match="CAUSE_UNDISPATCHED"):
        k.run_until(1)
