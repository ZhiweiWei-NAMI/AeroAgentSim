"""R2-J structural growth probes, separate from mandatory semantic regressions.

All helpers are local: importing a benchmark must not be required for collection.
Counters wrap real calls only during the operation under test; fixture construction
and diagnostic reads are outside that scope. They measure the named call boundary,
not elapsed time, allocated bytes, or C-level tuple-copy work. The xfails describe
unimplemented independence properties, not measured latency/speedup budgets.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest

import aerokernel.messages as messages_module
import aerokernel.transactions as transactions_module
from aerokernel import (
    ABSENT,
    BindingManifest,
    CommandRequest,
    Delivery,
    Dependency,
    EntityRef,
    Feedback,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    KernelError,
    LocalCause,
    MemoryRegistry,
    Message,
    MessageDescriptor,
    Partition,
    Receipt,
    ResourceBudget,
    Stamp,
    StateView,
    TypeDescriptor,
    freeze,
    replay,
    thaw,
)
from aerokernel.messages import Actions
from aerokernel.sdk import SimpleEngine
from aerokernel.state import Fact, FactVersions, Life, Store
from aerokernel.transactions import Candidate


def _payload(index):
    return {"samples": [index]}


def _registry():
    progress = {
        "type": "record",
        "members": {"samples": {"type": "array", "items": {"type": "integer"}}},
        "required": ["samples"],
        "extra": False,
    }
    return MemoryRegistry(
        (),
        messages=(
            MessageDescriptor(
                "do",
                "command",
                {"type": "integer"},
                result_schema=progress,
                feedback_schema=progress,
            ),
        ),
    )


def _command(command_id="command"):
    return Message(
        command_id,
        "command",
        "do",
        "ingress",
        "host",
        0,
        Instant(0),
        Instant(0),
        freeze(1),
        (),
        origin=ItemRef(1, 0),
    )


def _receipt(actions, delivery, status, index, result=None):
    # Reading the stored head avoids adding a test-authored action() traversal.
    head = actions.states[delivery.message.id].head
    return actions.receipt(
        "target",
        Receipt(delivery.message.id, status, result),
        ItemRef(index, 0),
        (),
        (delivery.dispatch_ref, head),
    )


def _feedback(actions, delivery, index, payload=None):
    head = actions.states[delivery.message.id].head
    return actions.feedback(
        "target",
        Feedback(delivery.message.id, _payload(index) if payload is None else payload),
        ItemRef(5 + index, 0),
        (),
        (delivery.dispatch_ref, head),
    )


def _executing(history_size=0, *, actions=None, command_id="command"):
    actions = Actions(_registry()) if actions is None else actions
    message = _command(command_id)
    actions.pending(message.id, "target", "ingress", "host", message.origin)
    actions.submit(message, ItemRef(1, 2))
    delivery = Delivery(
        message, "target", Instant(0, 1), ItemRef(1, 1), ItemRef(2, 0)
    )
    actions.record_dispatch(delivery)
    _receipt(actions, delivery, "accepted", 3)
    _receipt(actions, delivery, "executing", 4)
    for index in range(history_size):
        _feedback(actions, delivery, index)
    return actions, delivery


@contextmanager
def _message_calls(monkeypatch):
    """Count module-local calls, not normalization inside registry/values helpers.

    In action(), each messages.thaw/freeze call processes one history entry.
    messages.normalize is the separate normalization of the newly appended value;
    it deliberately does not include values.normalize invoked by deep freezing.
    replace counts ActionState replacement requests, not all Python allocations.
    """
    counts = Counter(action=0, thaw=0, freeze=0, normalize=0, replace=0)

    def counted(name, original):
        def call(*args, **kwargs):
            counts[name] += 1
            return original(*args, **kwargs)

        return call

    with monkeypatch.context() as patch:
        for name in ("thaw", "freeze", "normalize", "replace"):
            patch.setattr(
                messages_module, name, counted(name, getattr(messages_module, name))
            )
        patch.setattr(Actions, "action", counted("action", Actions.action))
        yield counts


@pytest.mark.parametrize(
    "unrelated_commands,history_size", [(0, 0), (1, 128), (32, 8)]
)
def test_new_pending_and_submit_do_not_traverse_unrelated_histories(
    monkeypatch, unrelated_commands, history_size
):
    actions = Actions(_registry())
    for index in range(unrelated_commands):
        _executing(history_size, actions=actions, command_id=f"old:{index}")
    old = actions.to_data()
    message = _command("new")  # Message payload freezing is not submission work.

    with _message_calls(monkeypatch) as pending_calls:
        actions.pending(message.id, "target", "ingress", "host", message.origin)
    with _message_calls(monkeypatch) as submit_calls:
        actions.submit(message, ItemRef(1000, 0))

    assert dict(pending_calls) == dict(
        action=0, thaw=0, freeze=0, normalize=0, replace=0
    )
    assert submit_calls["action"] <= 1
    assert submit_calls["replace"] <= 2
    assert all(submit_calls[name] == 0 for name in ("thaw", "freeze", "normalize"))
    assert actions.action("new").status == "submitted"
    after = actions.to_data()
    for command_id, state in old["states"].items():
        assert after["states"][command_id] == state


@pytest.mark.parametrize("history_size", [0, 16, 128])
def test_feedback_payload_normalization_and_state_replacement_stay_bounded(
    monkeypatch, history_size
):
    actions, delivery = _executing(history_size)
    old = actions.action("command")
    with _message_calls(monkeypatch) as calls:
        item = _feedback(actions, delivery, history_size)

    # This is NOT a claim that history deep freezing or tuple append is constant.
    assert calls["normalize"] == 1
    # Two immutable view replacements and one appended-state replacement today.
    # This bounds only that operation, not the amount of data in each history.
    assert calls["replace"] <= 3
    current = actions.action("command")
    assert item["payload"] == _payload(history_size)
    assert current.status == old.status == "executing"
    assert current.head == old.head
    assert current.history[:-1] == old.history
    assert thaw(current.history[-1]["payload"]) == _payload(history_size)


@pytest.mark.parametrize("history_size", [16, 128])
@pytest.mark.xfail(
    strict=False,
    raises=AssertionError,
    reason=(
        "Actions.action rebuilds every history entry with messages.thaw/freeze; "
        "lookup deep-copy work is not yet independent of feedback history length"
    ),
)
def test_action_lookup_history_copy_calls_do_not_grow(monkeypatch, history_size):
    observed = []
    for size in (0, history_size):
        actions, _ = _executing(size)
        with _message_calls(monkeypatch) as calls:
            actions.action("command")
        observed.append((calls["thaw"], calls["freeze"]))
    # A measured short-history baseline, not an estimated operation count.
    assert all(
        grown <= baseline
        for grown, baseline in zip(observed[1], observed[0], strict=True)
    ), observed


@pytest.mark.parametrize("history_size", [16, 128])
@pytest.mark.xfail(
    strict=False,
    raises=AssertionError,
    reason=(
        "Actions.feedback reads action history directly and through _authorized; "
        "both reads thaw/freeze the old prefix before appending one feedback item"
    ),
)
def test_feedback_append_history_copy_calls_do_not_grow(monkeypatch, history_size):
    observed = []
    for size in (0, history_size):
        actions, delivery = _executing(size)
        with _message_calls(monkeypatch) as calls:
            _feedback(actions, delivery, size)
        observed.append((calls["thaw"], calls["freeze"]))
    assert all(
        grown <= baseline
        for grown, baseline in zip(observed[1], observed[0], strict=True)
    ), observed


@pytest.mark.parametrize("history_size", [1, 64])
def test_old_action_and_nested_history_remain_immutable_after_append(history_size):
    actions, delivery = _executing(history_size)
    old = actions.action("command")
    expected = thaw(old.history)
    payload = _payload(history_size)
    _feedback(actions, delivery, history_size, payload)
    payload["samples"][0] = -1  # No retained alias to a caller-owned input tree.
    result = _payload(999)
    _receipt(actions, delivery, "succeeded", history_size + 10, result)
    result["samples"].append(-1)

    with pytest.raises(FrozenInstanceError):
        old.status = "failed"
    with pytest.raises(TypeError):
        old.history[-1]["payload"]["samples"] = ()
    with pytest.raises(TypeError):
        old.history[-1]["payload"]["samples"][0] = -1
    detached = thaw(old.history)
    detached[-1]["payload"]["samples"][0] = -1

    current = actions.action("command")
    assert old.status == "executing"
    assert thaw(old.history) == expected
    assert current.history[: len(old.history)] == old.history
    assert thaw(current.history[-2]["payload"]) == _payload(history_size)
    assert thaw(current.history[-1]["result"]) == _payload(999)
    assert current.status == "succeeded" and current.head != old.head


@pytest.mark.parametrize("history_size", [0, 64])
@pytest.mark.parametrize("operation", ["feedback", "receipt"])
@pytest.mark.parametrize(
    "violation,code",
    [
        ("wrong_target", "ACTION_TARGET"),
        ("stale_head", "ACTION_HEAD"),
        ("missing_dispatch", "ACTION_DISPATCH"),
    ],
)
def test_long_history_does_not_weaken_receipt_or_feedback_authority(
    history_size, operation, violation, code
):
    actions, delivery = _executing(history_size)
    old = actions.action("command")
    before = actions.to_data()
    partition = "intruder" if violation == "wrong_target" else "target"
    head = ItemRef(3, 0) if violation == "stale_head" else old.head
    causes = (
        (head,) if violation == "missing_dispatch" else (delivery.dispatch_ref, head)
    )
    with pytest.raises(KernelError, match=code):
        if operation == "feedback":
            actions.feedback(
                partition,
                Feedback("command", _payload(999)),
                ItemRef(999, 0),
                (),
                causes,
            )
        else:
            actions.receipt(
                partition,
                Receipt("command", "succeeded", _payload(999)),
                ItemRef(999, 0),
                (),
                causes,
            )
    assert actions.action("command") == old
    assert actions.to_data() == before


@pytest.mark.parametrize("history_size", [0, 64])
def test_cloned_action_branches_preserve_the_base_and_each_other(
    monkeypatch, history_size
):
    actions, delivery = _executing(history_size)
    old = actions.action("command")
    before = actions.to_data()
    with _message_calls(monkeypatch) as calls:
        abandoned = actions.clone()
        replacement = actions.clone()
        independent = actions.clone()
    assert dict(calls) == dict(action=0, thaw=0, freeze=0, normalize=0, replace=0)

    _feedback(abandoned, delivery, history_size, _payload(111))
    abandoned_view = abandoned.action("command")
    _feedback(replacement, delivery, history_size, _payload(222))
    _receipt(replacement, delivery, "succeeded", history_size + 10, _payload(333))
    # Committed ancestors stay untouched; each divergent suffix owns its fork.
    _feedback(independent, delivery, history_size, _payload(444))

    assert actions.action("command") == old
    assert actions.to_data() == before
    assert abandoned.action("command") == abandoned_view
    assert abandoned_view.history[:-1] == old.history
    assert thaw(abandoned_view.history[-1]["payload"]) == _payload(111)
    assert thaw(replacement.action("command").history[-2]["payload"]) == _payload(222)
    assert replacement.action("command").status == "succeeded"
    assert thaw(independent.action("command").history[-1]["payload"]) == _payload(444)
    assert old.status == "executing" and len(old.history) == history_size + 3


def _routing_candidate(unrelated_partitions):
    """Build real Store/Candidate/Fact objects, without scheduling a benchmark."""
    registry = MemoryRegistry(
        (TypeDescriptor("T"),),
        tuple(
            FieldDescriptor(field, "T", {"type": "integer"})
            for field in ("x", "y", "quiet")
        ),
    )
    # Deliberately not recipient-sorted; notification order must be canonical.
    partitions = {
        "z-reader": Partition(
            "z-reader", "z-reader", consumes=(Dependency("x", lag_ns=3),)
        ),
        "writer": Partition("writer", "writer", produces=("x", "y", "quiet")),
    }
    for index in range(unrelated_partitions):
        name = f"unrelated:{index:04d}"
        partitions[name] = Partition(name, name, consumes=(Dependency("y"),))
    partitions["a-reader"] = Partition(
        "a-reader", "a-reader", consumes=(Dependency("x", id_pattern="entity"),)
    )
    store = Store(
        registry, BindingManifest("growth", "0"), partitions, Actions(registry)
    )
    entity = EntityRef("growth", "0", "entity", 0, "T")
    store.lives[entity] = Life(entity, store.cut)
    store.live_ids[entity.id] = entity
    for field in ("x", "y", "quiet"):
        store.writers[entity, field] = "writer"
    candidate = Candidate(store, Instant(1), 1, {}, ResourceBudget())
    key, cause = (entity, "x"), ItemRef(1, 0)
    fact = Fact(
        key,
        1,
        Stamp("canonical", 1, 1, "canonical"),
        1,
        Instant(1),
        Interval(Instant(1), None),
        "writer",
        cause,
    )
    candidate.state.facts[key] = FactVersions(key).with_version(fact, 1)
    return store, candidate, key, cause


@contextmanager
def _dirty_match_calls(monkeypatch):
    recipients = []
    original = transactions_module.dirty_matches

    def counted(store, recipient, ref, field):
        recipients.append(recipient)
        return original(store, recipient, ref, field)

    with monkeypatch.context() as patch:
        patch.setattr(transactions_module, "dirty_matches", counted)
        yield recipients


@pytest.mark.parametrize("unrelated_partitions", [8, 64])
@pytest.mark.xfail(
    strict=False,
    raises=AssertionError,
    reason=(
        "Candidate.notify_field sorts every partition and calls dirty_matches for "
        "each; unrelated partitions are not yet excluded by a dependency index"
    ),
)
def test_dirty_matching_calls_do_not_grow_with_unrelated_partitions(
    monkeypatch, unrelated_partitions
):
    observed = []
    for size in (0, unrelated_partitions):
        _, candidate, key, cause = _routing_candidate(size)
        with _dirty_match_calls(monkeypatch) as calls:
            candidate.notify_field(key, cause)
        observed.append(len(calls))
    assert observed[1] <= observed[0], observed


@pytest.mark.parametrize("unrelated_partitions", [0, 8, 64])
def test_unconsumed_field_skips_matching_even_with_many_partitions(
    monkeypatch, unrelated_partitions
):
    store, candidate, key, cause = _routing_candidate(unrelated_partitions)
    with _dirty_match_calls(monkeypatch) as calls:
        candidate.notify_field((key[0], "quiet"), cause)
    assert calls == []
    assert candidate.items == [] and not candidate.state.work
    assert candidate.state.cut == store.cut


@pytest.mark.parametrize("unrelated_partitions", [0, 8, 64])
def test_dirty_routing_order_lag_and_candidate_isolation(unrelated_partitions):
    store, candidate, key, cause = _routing_candidate(unrelated_partitions)
    candidate.notify_field(key, cause)
    sibling = Candidate(store, Instant(1), 1, {}, ResourceBudget())

    assert [item["recipient"] for item in candidate.items] == ["a-reader", "z-reader"]
    work = sorted(candidate.state.work, key=lambda item: item.recipient)
    assert [(item.recipient, item.eligible) for item in work] == [
        ("a-reader", Instant(1, 1)),
        ("z-reader", Instant(4)),
    ]
    for item in work:
        assert item.cause == cause
        assert item.dirty.key == key and item.dirty.value_changed is True
    assert candidate.state.field(key, Instant(1), candidate.state.cut).value == 1
    assert store.field(key, Instant(1), store.cut) is ABSENT
    assert sibling.state.field(key, Instant(1), sibling.state.cut) is ABSENT
    assert not store.work and not sibling.state.work
    assert store.cut == sibling.state.cut and store.cut != candidate.state.cut


class _FeedbackTarget(SimpleEngine):
    def __init__(self, feedback_count, fail_at=None):
        super().__init__(Partition("target", "target", commands=("do",)))
        self.feedback_count = feedback_count
        self.fail_at = fail_at
        self.step = 0
        self.command = None
        self.dispatch = None

    def on_react(self, view, inbox, dirty):
        if not inbox:
            return ()
        delivery = inbox[0]
        self.command = delivery.message.id
        self.dispatch = delivery.dispatch_ref
        self.wakeup_ns = 1
        return (
            Receipt(
                self.command,
                "accepted",
                causes=(self.dispatch, view.action(self.command).head),
            ),
            Receipt(self.command, "executing", causes=(self.dispatch, LocalCause(0))),
        )

    def integrate(self, view):
        self.step += 1
        causes = (self.dispatch, view.action(self.command).head)
        if self.step <= self.feedback_count:
            self.wakeup_ns = self.step + 1
            operations = (Feedback(self.command, _payload(self.step), causes),)
            if self.step == self.fail_at:
                # The valid first operation must not escape a rejected whole wave.
                operations += (Receipt(self.command, "accepted", causes=causes),)
            return operations
        self.wakeup_ns = None
        return (
            Receipt(self.command, "succeeded", _payload(self.feedback_count), causes),
        )


def _kernel(feedback_count, provenance="full", fail_at=None):
    target = _FeedbackTarget(feedback_count, fail_at)
    kernel = Kernel(provenance=provenance)
    kernel.bind(
        _registry(),
        BindingManifest(
            "growth",
            "0",
            bootstrap_commands=(CommandRequest("do", "target", Instant(0), 1),),
        ),
        (target, SimpleEngine(Partition("bystander", "bystander"))),
    )
    kernel.start()
    return kernel, target.command


@pytest.mark.parametrize("feedback_count", [1, 16, 64])
@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_long_run_is_deterministic_replayable_and_keeps_old_views(
    monkeypatch, feedback_count, provenance
):
    kernels = []
    try:
        for _ in range(2):
            kernel, command = _kernel(feedback_count, provenance)
            kernels.append(kernel)
            kernel.run_until(feedback_count // 2)
        first, second = kernels
        old_view = first.view()
        old_action = old_view.action(command)
        old_history = thaw(old_action.history)
        assert old_action.status == "executing"
        assert len(old_action.history) == 3 + feedback_count // 2
        with pytest.raises(KernelError, match="READ_UNDECLARED"):
            StateView(first._store, partition="bystander").action(command)

        for kernel in kernels:
            kernel.run_until(feedback_count + 1)
        assert first.journal.bytes == second.journal.bytes
        final = first.action(command)
        assert final.status == "succeeded"
        assert [
            thaw(item["payload"])
            for item in final.history
            if item["kind"] == "feedback"
        ] == [_payload(index) for index in range(1, feedback_count + 1)]
        assert len(final.history) == feedback_count + 4
        assert thaw(final.history[-1]["result"]) == _payload(feedback_count)
        assert old_view.action(command) == old_action
        assert thaw(old_action.history) == old_history
        assert first.view(old_view.cut).action(command) == old_action
        with pytest.raises(KernelError, match="READ_UNDECLARED"):
            StateView(first._store, partition="bystander").action(command)

        def forbidden(*args, **kwargs):
            raise AssertionError("replay must not invoke an engine")

        with monkeypatch.context() as patch:
            for method in ("reset", "advance", "react", "horizon"):
                patch.setattr(SimpleEngine, method, forbidden)
            restored = replay(first.journal.bytes)
            kernels.append(restored)
        assert restored.records == first.records
        assert restored.action(command) == final
        assert restored.view(old_view.cut).action(command) == old_action
        assert restored._store.actions.to_data() == first._store.actions.to_data()
        assert not restored.incomplete
    finally:
        for kernel in kernels:
            kernel.close()


@pytest.mark.parametrize("history_size", [0, 32])
@pytest.mark.parametrize("provenance", ["lean", "full"])
def test_rejected_candidate_cannot_publish_feedback_or_change_old_snapshots(
    history_size, provenance
):
    kernel, command = _kernel(history_size + 1, provenance, fail_at=history_size + 1)
    kernels = [kernel]
    try:
        kernel.run_until(history_size)
        old_view = kernel.view()
        old_action = old_view.action(command)
        before = kernel._store.actions.to_data()
        with pytest.raises(KernelError, match="ACTION_TRANSITION"):
            kernel.run_until(history_size + 1)

        assert kernel._store.faulted
        assert kernel.action(command) == old_action
        assert kernel._store.actions.to_data() == before
        assert old_view.action(command) == old_action
        assert kernel.view(old_view.cut).action(command) == old_action
        assert len(old_action.history) == history_size + 3
        assert [
            thaw(item["payload"])
            for item in kernel.action(command).history
            if item["kind"] == "feedback"
        ] == [_payload(index) for index in range(1, history_size + 1)]
        assert any(
            item.get("status") == "returned_unpublished"
            for record in kernel.records
            for item in record["items"]
        )
        assert any(
            intent["partition"] == "target"
            and intent["status"] == "returned_unpublished"
            for intent in kernel._store.intents.values()
        )
        restored = replay(kernel.journal.bytes)
        kernels.append(restored)
        assert restored.incomplete and restored._store.faulted
        assert restored.records == kernel.records
        assert restored.action(command) == old_action
        assert restored.view(old_view.cut).action(command) == old_action
        assert restored._store.actions.to_data() == before
    finally:
        for kernel in kernels:
            kernel.close()
