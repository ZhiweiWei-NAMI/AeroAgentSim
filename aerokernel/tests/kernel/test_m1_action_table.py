"""Independent complete action transition matrix and cancellation races."""

from dataclasses import replace

import pytest

from aerokernel import (
    CancelDecision,
    Delivery,
    Feedback,
    Instant,
    ItemRef,
    KernelError,
    MemoryRegistry,
    Message,
    MessageDescriptor,
    Receipt,
    freeze,
)
from aerokernel.messages import Actions

STATUS = (
    "submitted",
    "accepted",
    "executing",
    "canceling",
    "rejected",
    "succeeded",
    "failed",
    "canceled",
)
ALLOWED = {
    "submitted": {"accepted", "rejected"},
    "accepted": {"executing", "failed", "canceling"},
    "executing": {"succeeded", "failed", "canceling"},
    "canceling": {"canceled", "failed"},
    "rejected": set(),
    "succeeded": set(),
    "failed": set(),
    "canceled": set(),
}
PATH = {
    "submitted": (),
    "accepted": ("accepted",),
    "executing": ("accepted", "executing"),
    "canceling": ("accepted", "canceling"),
    "rejected": ("rejected",),
    "succeeded": ("accepted", "executing", "succeeded"),
    "failed": ("accepted", "failed"),
    "canceled": ("accepted", "canceling", "canceled"),
}


def fixture():
    registry = MemoryRegistry(
        (),
        messages=(
            MessageDescriptor(
                "do",
                "command",
                {"type": "integer"},
                feedback_schema={"type": "integer"},
                cancel_support=True,
            ),
        ),
    )
    actions = Actions(registry)
    msg = Message(
        "command",
        "command",
        "do",
        "ingress",
        "host",
        0,
        Instant(0),
        Instant(0),
        freeze(7),
        (),
        origin=ItemRef(1, 0),
    )
    actions.pending(msg.id, "target", "ingress", "host", msg.origin)
    actions.submit(msg, ItemRef(1, 2))
    delivery = Delivery(msg, "target", Instant(0, 1), ItemRef(1, 1), ItemRef(2, 0))
    actions.record_dispatch(delivery)
    return actions, delivery


def change(actions, delivery, status, index):
    head = actions.action("command").head
    if status == "canceling":
        msg = replace(
            delivery.message,
            id=f"cancel:{index}",
            kind="cancel",
            payload=freeze({"command_id": "command"}),
        )
        cancel = Delivery(
            msg, "target", Instant(index), ItemRef(index, 1), ItemRef(index, 2)
        )
        actions.record_dispatch(cancel)
        return actions.cancel_decision(
            "target",
            CancelDecision("command", msg.id, True),
            ItemRef(index, 3),
            (cancel,),
            (cancel.dispatch_ref, head),
        )
    return actions.receipt(
        "target",
        Receipt("command", status),
        ItemRef(index, 0),
        (delivery,),
        (delivery.dispatch_ref, head),
    )


@pytest.mark.parametrize("source", STATUS)
@pytest.mark.parametrize("target", STATUS)
def test_complete_status_matrix(source, target):
    actions, delivery = fixture()
    for index, status in enumerate(PATH[source], 3):
        change(actions, delivery, status, index)
    assert actions.action("command").status == source
    old = actions.action("command")
    if target in ALLOWED[source]:
        change(actions, delivery, target, 20)
        assert actions.action("command").status == target
    else:
        with pytest.raises(KernelError):
            change(actions, delivery, target, 20)
        assert actions.action("command") == old


def test_feedback_preserves_head_and_stale_receipts_fail_after_cancel_decision():
    actions, delivery = fixture()
    change(actions, delivery, "accepted", 3)
    stale = actions.action("command").head
    change(actions, delivery, "executing", 4)
    head = actions.action("command").head
    actions.feedback(
        "target",
        Feedback("command", 7),
        ItemRef(5, 0),
        (),
        (delivery.dispatch_ref, head),
    )
    assert actions.action("command").head == head
    with pytest.raises(KernelError, match="ACTION_HEAD"):
        actions.receipt(
            "target",
            Receipt("command", "failed"),
            ItemRef(6, 0),
            (),
            (delivery.dispatch_ref, stale),
        )
    change(actions, delivery, "canceling", 7)
    with pytest.raises(KernelError, match="ACTION_CANCEL_HEAD"):
        actions.receipt(
            "target",
            Receipt("command", "canceled"),
            ItemRef(8, 0),
            (),
            (delivery.dispatch_ref, head),
        )
    change(actions, delivery, "canceled", 9)


def test_multiple_dispatched_cancels_do_not_prove_cleanup_or_allow_second_acceptance():
    actions, delivery = fixture()
    change(actions, delivery, "accepted", 3)
    old = actions.action("command").head
    cancels = [
        Delivery(
            replace(
                delivery.message,
                id=f"cancel:{i}",
                kind="cancel",
                payload=freeze({"command_id": "command"}),
            ),
            "target",
            Instant(4),
            ItemRef(4, i),
            ItemRef(5, i),
        )
        for i in (0, 1)
    ]
    for cancel in cancels:
        actions.record_dispatch(cancel)
    first = cancels[0]
    actions.cancel_decision(
        "target",
        CancelDecision("command", first.message.id, True),
        ItemRef(6, 0),
        (),
        (first.dispatch_ref, old),
    )
    assert actions.action("command").status == "canceling"
    second = cancels[1]
    head = actions.action("command").head
    with pytest.raises(KernelError, match="CANCEL_STATUS"):
        actions.cancel_decision(
            "target",
            CancelDecision("command", second.message.id, True),
            ItemRef(7, 0),
            (),
            (second.dispatch_ref, head),
        )
    actions.cancel_decision(
        "target",
        CancelDecision("command", second.message.id, False, "already canceling"),
        ItemRef(7, 0),
        (),
        (second.dispatch_ref, head),
    )
    assert actions.action("command").status == "canceling"
    assert actions.action("command").head == head
    with pytest.raises(KernelError, match="CANCEL_DECISION"):
        actions.cancel_decision(
            "target",
            CancelDecision("command", second.message.id, False),
            ItemRef(8, 0),
            (),
            (second.dispatch_ref, head),
        )


def test_missing_receipts_remain_submitted_after_dispatch():
    actions, delivery = fixture()
    state = actions.action(delivery.message.id)
    assert state.status == "submitted" and len(state.history) == 1
    assert actions.clone().action(delivery.message.id) == state
