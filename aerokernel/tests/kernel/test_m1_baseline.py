"""Independent, public-API counterexamples."""

from dataclasses import replace

import pytest

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Dependency,
    Emit,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    ItemRef,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Receipt,
    Remove,
    ResourceBudget,
    ScheduleTimer,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import encode
from aerokernel.sdk import SimpleEngine
from aerokernel.values import canonical_json, parse_json


def replace_same_wave():
    old = EntityRef("r", "e", "id", 0, "T")
    new = replace(old, generation=1)

    class E(SimpleEngine):
        done = False

        def initialize(self, view):
            return (Create(old),)

        def on_react(self, view, inbox, dirty):
            if not self.done:
                self.done = True
                return (Remove(old), Create(new))
            return ()

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry((TypeDescriptor("T"),)),
        BindingManifest("r", "e", (old,), lifecycle=(LifecycleRule("p", "T"),)),
        (E(Partition("p", "e", lifecycle=True)),),
    )
    k.start()
    return {
        "removed": encode(k.view().lifecycle(old).removed),
        "created": encode(k.view().lifecycle(new).created),
        "replay_accepts": replay(k.journal.bytes).view().lifecycle(new).ref == new,
    }


def cancel_lag():
    class Target(SimpleEngine):
        def __init__(self):
            super().__init__(Partition("p", "e", commands=("do",), message_lag_ns=10))
            self.delivered = []

        def on_react(self, view, inbox, dirty):
            out = []
            for d in inbox:
                self.delivered.append(
                    (d.message.kind, d.message.available.ns, d.instant.ns)
                )
                if d.message.kind == "command":
                    out.append(
                        Receipt(
                            d.message.id,
                            "accepted",
                            causes=(d.dispatch_ref, view.action(d.message.id).head),
                        )
                    )
            return tuple(out)

    e = Target()
    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("do", "command", cancel_support=True),)
        ),
        BindingManifest("r", "e"),
        (e,),
    )
    k.start()
    mid = k.submit(CommandRequest("do", "p", Instant(1), None))
    k.run_until(11)
    k.cancel(mid)
    k.run_until(22)
    return {"deliveries": e.delivered, "cancel_expected_ns": 22}


def timer_namespace():
    class E(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("my_timer", Instant(1), {"input": 7}),)

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(()), BindingManifest("r", "e"), (E(Partition("kernel", "e")),)
    )
    k.start()
    try:
        k.run_until(1)
    except Exception as exc:
        return {
            "exception": type(exc).__name__,
            "detail": str(exc),
            "last_record": k.records[-1],
        }
    return "delivered"


def lag_dynamic_creation():
    ref = EntityRef("r", "e", "later", 0, "T")

    class Owner(SimpleEngine):
        done = False

        def initialize(self, view):
            return (ScheduleTimer("create", Instant(10), None),)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns == 10 and not self.done:
                self.done = True
                return (Create(ref),)
            return ()

    class Reader(SimpleEngine):
        seen = None

        def on_react(self, view, inbox, dirty):
            if any(d.kind == "lifecycle" for d in dirty):
                self.seen = view.field((ref, "x"), Instant(5))
            return ()

    owner = Owner(Partition("owner", "a", produces=("x",), lifecycle=True))
    reader = Reader(
        Partition(
            "reader", "b", consumes=(Dependency("x", lag_ns=5),), lifecycle_reads=("T",)
        )
    )
    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
        ),
        BindingManifest(
            "r",
            "e",
            rules=(BindingRule("owner", "T", ("x",)),),
            lifecycle=(LifecycleRule("owner", "T"),),
        ),
        (owner, reader),
    )
    k.start()
    try:
        k.run_until(10)
    except KernelError as exc:
        return {
            "code": exc.code,
            "detail": str(exc),
            "host_absent_at5": k.view().field((ref, "x"), Instant(5)) is ABSENT,
            "expected": "ABSENT at lag-cut preceding creation",
        }
    return {"seen": reader.seen}


def big_header():
    k = Kernel(
        provenance="full",
        budget=ResourceBudget(frame_bytes=16 * 1024 * 1024),
        configuration="x" * (9 * 1024 * 1024),
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (SimpleEngine(Partition("p", "e")),),
    )
    k.start()
    header_bytes = len(k.journal._lines[0])
    try:
        replay(k.journal.bytes)
    except KernelError as exc:
        return {
            "live_started": True,
            "header_bytes": header_bytes,
            "replay_code": exc.code,
            "detail": str(exc),
        }
    return "replayed"


def deep_header():
    value = 1
    for _ in range(140):
        value = [value]
    k = Kernel(
        provenance="full", budget=ResourceBudget(nesting_depth=200), configuration=value
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (SimpleEngine(Partition("p", "e")),),
    )
    k.start()
    try:
        replay(k.journal.bytes)
    except KernelError as exc:
        return {"live_started": True, "replay_code": exc.code, "detail": str(exc)}
    return "replayed"


def integer_header():
    k = Kernel(
        provenance="full",
        root_seed=10**4100,
        budget=ResourceBudget(integer_digits=4200),
    )
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (SimpleEngine(Partition("p", "e")),),
    )
    k.start()
    try:
        replay(k.journal.bytes)
    except KernelError as exc:
        return {"live_started": True, "replay_code": exc.code, "detail": str(exc)}
    return "replayed"


def rng_scope():
    class Thief(SimpleEngine):
        def reset(self, context, view):
            context.rng["victim"].stream("s").random()
            return super().reset(context, view)

    class Victim(SimpleEngine):
        def reset(self, context, view):
            self.value = context.rng["victim"].stream("s").random()
            return super().reset(context, view)

    def run(steal):
        victim = Victim(Partition("victim", "b", rng_streams=("s",)))
        first = (Thief if steal else SimpleEngine)(Partition("thief", "a"))
        k = Kernel(provenance="full", root_seed=7)
        k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (first, victim))
        k.start()
        return victim.value

    return {"without_foreign_access": run(False), "with_foreign_access": run(True)}


def mutable_dirty():
    class E(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("t", Instant(1), {"input": [7]}),)

        def on_react(self, view, inbox, dirty):
            for d in dirty:
                if d.kind == "timer":
                    d.payload["input"][0] = 99
            return ()

    k = Kernel(provenance="full")
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (E(Partition("p", "e")),))
    k.start()
    k.run_until(1)
    r = replay(k.journal.bytes)

    def last_payload(kernel):
        intents = [
            i
            for record in kernel.records
            for i in record["items"]
            if i.get("kind") == "intent" and i["phase"] == "react"
        ]
        return intents[-1]["dirty"][0]["fields"]["payload"]

    return {
        "live_recorded_payload": last_payload(k),
        "replay_recorded_payload": last_payload(r),
        "records_equal": k.records == r.records,
        "intent_indexes_equal": k._store.intents == r._store.intents,
    }


def unpublished_dispatch_cause():
    class E(SimpleEngine):
        delivered = []

        def initialize(self, view):
            return (Activate("target"),)

        def on_react(self, view, inbox, dirty):
            self.delivered.extend(d.instant.ns for d in inbox)
            if any(d.kind == "activation" for d in dirty):
                # Bootstrap's recorded ingress publication; no command dispatch yet.
                return (
                    Emit(
                        "event",
                        "response",
                        "topic",
                        view.instant,
                        None,
                        (ItemRef(2, 3),),
                    ),
                )
            return ()

    e = E(
        Partition(
            "target",
            "e",
            commands=("do",),
            emits=("response",),
            message_targets=("topic",),
            message_lag_ns=10,
        )
    )
    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (),
            messages=(
                MessageDescriptor("do", "command"),
                MessageDescriptor("response"),
            ),
        ),
        BindingManifest(
            "r",
            "e",
            bootstrap_commands=(CommandRequest("do", "target", Instant(0), None),),
        ),
        (e,),
    )
    k.start()
    source = next(
        i
        for r in k.records
        for i in r["items"]
        if "message" in i and i.get("kind") == "operation"
    )
    event_available = source["message"]["fields"]["available"]
    dispatches_at_start = e.delivered.copy()
    k.run_until(10)
    return {
        "event_available": event_available,
        "command_dispatched_at_start": dispatches_at_start,
        "actual_command_dispatch_ns": e.delivered,
        "replay_accepts": replay(k.journal.bytes).view().cut == k.view().cut,
    }


def replay_microstep_bound():
    class E(SimpleEngine):
        def initialize(self, view):
            return (Activate("p"),)

    k = Kernel(provenance="full", max_microsteps=1)
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (E(Partition("p", "e")),))
    k.start()
    records = [parse_json(line) for line in k.journal.bytes.splitlines(keepends=True)]

    def shift(value):
        if isinstance(value, dict):
            if value.get("$type") == "Instant" and value["fields"] == {
                "ns": 0,
                "microstep": 1,
            }:
                value["fields"]["microstep"] = 1025
            for child in value.values():
                shift(child)
        elif isinstance(value, list):
            for child in value:
                shift(child)

    for r in records[3:]:
        shift(r)
    r = replay(b"".join(canonical_json(record) for record in records))
    return {
        "pinned_max": r.max_microsteps,
        "accepted_cut": encode(r.view().cut),
        "incomplete": r.incomplete,
    }


def replay_duplicate_partition():
    import copy

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (SimpleEngine(Partition("p", "e")),),
    )
    k.start()
    records = [parse_json(line) for line in k.journal.bytes.splitlines(keepends=True)]
    records[0]["partitions"].append(copy.deepcopy(records[0]["partitions"][0]))
    r = replay(b"".join(canonical_json(record) for record in records))
    return {
        "header_partition_count": len(r.header["partitions"]),
        "actual_partition_count": len(r._store.partitions),
        "incomplete": r.incomplete,
    }


def expiry_after_microstep_write():
    ref = EntityRef("r", "e", "id", 0, "T")

    class Owner(SimpleEngine):
        written = False

        def initialize(self, view):
            return (Create(ref),)

        def on_react(self, view, inbox, dirty):
            if not self.written:
                self.written = True
                return (
                    FactWrite(
                        (ref, "x"),
                        1,
                        Stamp("canonical", 0, 1, "canonical"),
                        Interval(view.instant, Instant(1)),
                    ),
                )
            return ()

    class Observer(SimpleEngine):
        def __init__(self, partition):
            super().__init__(partition)
            self.seen = []

        def on_react(self, view, inbox, dirty):
            self.seen.append(
                (view.instant.ns, [(d.kind, d.value_changed) for d in dirty])
            )
            return ()

    owner = Owner(Partition("owner", "a", produces=("x",), lifecycle=True))
    observer = Observer(
        Partition("observer", "b", consumes=(Dependency("x", value_only=True),))
    )
    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
        ),
        BindingManifest(
            "r",
            "e",
            (ref,),
            rules=(BindingRule("owner", "T", ("x",)),),
            lifecycle=(LifecycleRule("owner", "T"),),
        ),
        (owner, observer),
    )
    k.start()
    before = k.view().field((ref, "x"), k.view().cut.instant).value
    k.run_until(1)
    return {
        "value_before": before,
        "absent_after": k.view().field((ref, "x"), Instant(1)) is ABSENT,
        "observer_notifications": observer.seen,
        "expiry_notifications_at_1": [s for s in observer.seen if s[0] == 1],
    }


@pytest.mark.parametrize("probe", [replace_same_wave, unpublished_dispatch_cause])
def test_rejects_illegal_live_waves(probe):
    with pytest.raises(KernelError):
        probe()


@pytest.mark.parametrize("probe", [big_header, deep_header, integer_header])
def test_live_headers_replay(probe):
    assert probe() == "replayed"


@pytest.mark.parametrize("probe", [replay_microstep_bound, replay_duplicate_partition])
def test_replay_rejects_consistent_corruption(probe):
    with pytest.raises(KernelError):
        probe()


def test_host_cancel_respects_route_lag():
    assert cancel_lag()["deliveries"][-1][2] == 22


def test_legal_kernel_partition_timer():
    assert timer_namespace() == "delivered"


def test_dynamic_lag_absence():
    assert lag_dynamic_creation()["seen"] is ABSENT


def test_rng_foreign_stream_denied():
    with pytest.raises((KeyError, KernelError)):
        rng_scope()


def test_dirty_cannot_mutate_published_intent():
    with pytest.raises(TypeError):
        mutable_dirty()


def test_expiry_after_reactive_write_notifies():
    assert expiry_after_microstep_write()["expiry_notifications_at_1"]
