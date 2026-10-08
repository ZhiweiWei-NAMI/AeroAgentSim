"""I9/I10: actual dispatch causes, authorized ordered receipts and cancellation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aerokernel import (
    BindingManifest,
    CancelDecision,
    CommandRequest,
    Delivery,
    Feedback,
    Instant,
    ItemRef,
    Kernel,
    KernelError,
    LocalCause,
    MemoryRegistry,
    Message,
    MessageDescriptor,
    Partition,
    Receipt,
    freeze,
    replay,
)
from aerokernel.messages import Actions
from aerokernel.testing import SimpleEngine


def registry():
    return MemoryRegistry(
        (),
        messages=(
            MessageDescriptor(
                "do",
                "command",
                {"type": "integer"},
                result_schema={"type": "integer"},
                feedback_schema={"type": "string"},
                cancel_support=True,
            ),
            MessageDescriptor("event", "event", {"type": "integer"}),
        ),
    )


def submitted():
    actions = Actions(registry())
    origin = ItemRef(1, 0)
    msg = Message(
        "command",
        "command",
        "do",
        "ingress",
        "host",
        0,
        Instant(0),
        Instant(0),
        freeze(1),
        (),
        origin=origin,
    )
    actions.pending(msg.id, "target", "ingress", "host", origin)
    actions.submit(msg, ItemRef(1, 2))
    delivery = Delivery(msg, "target", Instant(0, 1), ItemRef(1, 1), ItemRef(2, 0))
    actions.record_dispatch(delivery)
    return actions, delivery


def transition(actions, delivery, status, index, result=None):
    head = actions.action("command").head
    return actions.receipt(
        "target",
        Receipt("command", status, result),
        ItemRef(index, 0),
        (delivery,),
        (delivery.dispatch_ref, head),
    )


def test_full_action_feedback_failure_before_executing_and_immutable_history():
    actions, delivery = submitted()
    transition(actions, delivery, "accepted", 3)
    transition(actions, delivery, "executing", 4)
    head = actions.action("command").head
    item = actions.feedback(
        "target",
        Feedback("command", "progress"),
        ItemRef(5, 0),
        (),
        (delivery.dispatch_ref, head),
    )
    assert item["status"] == "executing" and actions.action("command").head == head
    transition(actions, delivery, "succeeded", 6, 99)
    state = actions.action("command")
    with pytest.raises(TypeError):
        state.history[-1]["result"] = 0
    assert actions.action("command").history[-1]["result"] == 99
    for status in ("accepted", "executing", "succeeded", "failed", "canceled"):
        with pytest.raises(KernelError):
            transition(actions, delivery, status, 7, 1)
    restored = Actions.from_data(actions.registry, actions.to_data())
    assert restored.to_data() == actions.to_data()
    other, delivery = submitted()
    transition(other, delivery, "accepted", 3)
    transition(other, delivery, "failed", 4)
    assert other.action("command").status == "failed"


@given(
    st.sampled_from(
        [
            "accepted",
            "executing",
            "canceling",
            "succeeded",
            "failed",
            "rejected",
            "canceled",
        ]
    )
)
def test_submitted_only_accepts_decision_and_target_dispatch_head(status):
    actions, delivery = submitted()
    head = actions.action("command").head
    if status in {"accepted", "rejected"}:
        transition(actions, delivery, status, 3)
    else:
        with pytest.raises(KernelError):
            transition(actions, delivery, status, 3, 1)
    for partition, causes in (
        ("forged", (delivery.dispatch_ref, head)),
        ("target", (head,)),
        ("target", (delivery.dispatch_ref, ItemRef(0, 0))),
    ):
        fresh, delivered = submitted()
        with pytest.raises(KernelError):
            fresh.receipt(
                partition,
                Receipt("command", "accepted"),
                ItemRef(3, 0),
                (delivered,),
                causes,
            )
    with pytest.raises(KernelError):
        actions.action("missing")


def test_cancel_acceptance_excludes_success_and_canceled_requires_actual_head():
    actions, delivery = submitted()
    transition(actions, delivery, "accepted", 3)
    transition(actions, delivery, "executing", 4)
    cancel = Message(
        "cancel",
        "cancel",
        "do",
        "ingress",
        "host",
        1,
        Instant(1),
        Instant(1),
        freeze({"command_id": "command"}),
        (),
    )
    dispatched = Delivery(cancel, "target", Instant(1, 1), ItemRef(5, 0), ItemRef(6, 0))
    actions.record_dispatch(dispatched)
    head = actions.action("command").head
    decision = actions.cancel_decision(
        "target",
        CancelDecision("command", "cancel", True),
        ItemRef(7, 0),
        (dispatched,),
        (dispatched.dispatch_ref, head),
    )
    assert decision["status"] == "canceling"
    accepted_head = actions.action("command").head
    with pytest.raises(KernelError):
        actions.receipt(
            "target",
            Receipt("command", "succeeded", 10),
            ItemRef(8, 0),
            (),
            (accepted_head,),
        )
    with pytest.raises(KernelError):
        actions.receipt(
            "target", Receipt("command", "canceled"), ItemRef(8, 0), (), (head,)
        )
    actions.receipt(
        "target", Receipt("command", "canceled"), ItemRef(8, 0), (), (accepted_head,)
    )
    assert actions.action("command").status == "canceled"
    with pytest.raises(KernelError):
        actions.cancel_decision(
            "target",
            CancelDecision("command", "cancel", False),
            ItemRef(9, 0),
            (),
            (dispatched.dispatch_ref, actions.action("command").head),
        )


def test_cancel_rejection_preserves_head_result_feedback_validation():
    actions, delivery = submitted()
    transition(actions, delivery, "accepted", 3)
    transition(actions, delivery, "executing", 4)
    head = actions.action("command").head
    cancel = Message(
        "cancel",
        "cancel",
        "do",
        "partition",
        "control",
        0,
        Instant(1),
        Instant(1),
        freeze({"command_id": "command"}),
        (),
    )
    d = Delivery(cancel, "target", Instant(1, 1), ItemRef(5, 0), ItemRef(6, 0))
    actions.record_dispatch(d)
    actions.cancel_decision(
        "target",
        CancelDecision("command", "cancel", False, "too late"),
        ItemRef(7, 0),
        (d,),
        (d.dispatch_ref, head),
    )
    assert (
        actions.action("command").head == head
        and actions.action("command").status == "executing"
    )
    with pytest.raises(KernelError):
        actions.feedback(
            "target",
            Feedback("command", False),
            ItemRef(8, 0),
            (),
            (delivery.dispatch_ref, head),
        )
    with pytest.raises(KernelError):
        transition(actions, delivery, "succeeded", 8, True)
    with pytest.raises(KernelError):
        actions.record_dispatch(d)
    with pytest.raises(KernelError):
        actions.pending("command", "target", "ingress", "host", ItemRef(1, 0))


def test_message_delivery_codec_and_bad_headers():
    actions, delivery = submitted()
    assert Delivery.from_data(delivery.to_data()) == delivery
    assert Message.from_data(delivery.message.to_data()) == delivery.message
    for kwargs in (
        {"source_kind": "foreign"},
        {"kind": "unknown"},
        {"sequence": True},
        {"kind": "event", "at": Instant(100)},
    ):
        with pytest.raises(KernelError):
            replace(delivery.message, **kwargs)
    with pytest.raises(KernelError):
        Delivery.from_data(delivery.message.to_data())
    with pytest.raises(KernelError):
        Message.from_data(delivery.to_data())
    with pytest.raises(KernelError):
        CommandRequest("do", "target", Instant(0), 1, ingress_at_ns=True)
    with pytest.raises(KernelError):
        CommandRequest("do", "target", 0, 1)


class Target(SimpleEngine):
    def __init__(self, complete_at=None):
        super().__init__(Partition("target", "target", commands=("do",)))
        self.command = None
        self.dispatch = None
        self.complete_at = complete_at

    def on_react(self, view, inbox, dirty):
        ops = []
        for d in inbox:
            if d.message.kind == "command":
                self.command, self.dispatch = d.message.id, d.dispatch_ref
                self.wakeup_ns = self.complete_at
                ops.extend(
                    (
                        Receipt(
                            self.command,
                            "accepted",
                            causes=(self.dispatch, view.action(self.command).head),
                        ),
                        Receipt(
                            self.command,
                            "executing",
                            causes=(self.dispatch, LocalCause(len(ops))),
                        ),
                    )
                )
            elif d.message.kind == "cancel":
                head = view.action(self.command).head
                accepted = view.action(self.command).status in {"accepted", "executing"}
                ops.append(
                    CancelDecision(
                        self.command,
                        d.message.id,
                        accepted,
                        causes=(d.dispatch_ref, head),
                    )
                )
                if accepted:
                    ops.append(
                        Receipt(
                            self.command, "canceled", causes=(LocalCause(len(ops) - 1),)
                        )
                    )
        return tuple(ops)

    def integrate(self, view):
        self.wakeup_ns = None
        return (
            Receipt(
                self.command,
                "succeeded",
                10,
                (self.dispatch, view.action(self.command).head),
            ),
        )


def kernel(target, **manifest_args):
    k = Kernel()
    manifest = BindingManifest(
        "r",
        "e",
        bootstrap_commands=(CommandRequest("do", "target", Instant(0), 1),),
        **manifest_args,
    )
    k.bind(registry(), manifest, (target,))
    k.start()
    mid = next(iter(k._store.actions.states))
    return k, mid


def test_host_cancel_reservation_cleanup_and_historical_action_prefix():
    k, mid = kernel(Target())
    before = k.view().cut
    result = k.cancel(mid)
    assert result.queued and result.message_id is not None
    assert k.action(mid).status == "executing"
    assert replay(k.journal.bytes).action(mid).status == "executing"
    k.run_until(1)
    assert k.action(mid).status == "canceled"
    assert k.view(before).action(mid).status == "executing"
    assert replay(k.journal.bytes).action(mid).status == "canceled"
    rejected = k.cancel(mid)
    assert (
        not rejected.queued
        and rejected.message_id is None
        and rejected.rejection_ref is not None
    )
    replay(k.journal.bytes)


def test_internal_completion_precedes_new_boundary_cancel_dispatch():
    k, mid = kernel(Target(1))
    assert k.cancel(mid).queued
    k.run_until(1)
    assert k.action(mid).status == "succeeded"
    assert k.action(mid).history[-1]["accepted"] is False
    assert replay(k.journal.bytes).action(mid).status == "succeeded"


def test_unauthorized_and_pending_cancel_rejected_without_queue_status():
    k, mid = kernel(Target(), control_source="controller")
    assert not k.cancel(mid).queued
    assert k.action(mid).status == "executing"
    pending = k.submit(CommandRequest("do", "target", Instant(3), 1))
    assert not k.cancel(pending).queued
    assert k.action(pending).status == "pending"
    replay(k.journal.bytes)


def test_cancel_decision_routes_to_partition_control_even_when_name_matches_ingress():
    from aerokernel import RequestCancel, ScheduleTimer
    from aerokernel.ids import message_id

    class Control(SimpleEngine):
        def __init__(self):
            super().__init__(Partition("host", "host"))
            self.received = []

        def initialize(self, view):
            return (ScheduleTimer("request", Instant(0, 3), None),)

        def on_react(self, view, inbox, dirty):
            self.received.extend(d.kind for d in dirty)
            if any(d.kind == "timer" for d in dirty):
                mid = message_id("r", "e", "ingress", "host", 0)
                assert view.action(mid).status == "executing"
                return (RequestCancel(mid),)
            return ()

    control = Control()
    k = Kernel()
    manifest = BindingManifest(
        "r",
        "e",
        bootstrap_commands=(CommandRequest("do", "target", Instant(0), 1),),
        cancel_sources=("host",),
    )
    k.bind(registry(), manifest, (Target(), control))
    k.start()
    assert "cancel_decision" in control.received
    assert (
        replay(k.journal.bytes)._store.actions.to_data() == k._store.actions.to_data()
    )
