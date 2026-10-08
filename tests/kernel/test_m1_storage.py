"""Prefix isolation, indexed interval selection and lossless WAL encoding."""

import io

import pytest

from aerokernel import (
    ABSENT,
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    EntityRef,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Journal,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    ResourceBudget,
    ResourceLimit,
    Stamp,
    TypeDescriptor,
    replay,
)
from aerokernel.compact import proposal
from aerokernel.ids import ItemRef
from aerokernel.queues import TimerQueue, WorkQueue
from aerokernel.sdk import SimpleEngine
from aerokernel.state import Fact, FactVersions, Retraction, Work
from aerokernel.storage import AppendList, Overlay, RecordLog
from aerokernel.values import canonical_json, parse_json

REF = EntityRef("r", "e", "id", 0, "T")
KEY = (REF, "x")


def test_version_prefix_forks_isolate_abandoned_candidates_and_full_history():
    prefix = FactVersions(KEY)
    authored = []
    versions = []
    for index in range(9):
        start = Instant(index % 3, index % 2)
        end = None if index % 2 else Instant(index % 3 + 2)
        item = ItemRef(index + 1, 2)
        version = (
            Fact(
                KEY,
                index,
                Stamp("canonical", -index, 1, "canonical"),
                -index,
                Instant(index),
                Interval(start, end),
                "p",
                item,
            )
            if index % 3
            else Retraction(
                KEY, Interval(start, end), "missing", Instant(index), "p", item
            )
        )
        authored.append(
            (
                index + 1,
                (start.ns, start.microstep),
                None if end is None else (end.ns, end.microstep),
            )
        )
        versions.append(version)
        prefix = prefix.with_version(version, index + 1)
    assert list(prefix) == versions
    assert prefix[-1] == versions[-1] and prefix[1:4] == versions[1:4]
    with pytest.raises(IndexError):
        _ = prefix[9]
    # Expectations use integer tuples and authored intervals, never production
    # interval predicates or another Store.field/replay query.
    for known in range(10):
        for ns in range(6):
            for microstep in range(3):
                at = (ns, microstep)
                for left in (False, True):
                    expected = -1
                    for i, (publication, start, end) in enumerate(authored):
                        eligible = start < at if left else start <= at
                        inside_end = end is None or (at <= end if left else at < end)
                        if publication <= known and eligible and inside_end:
                            expected = i
                    assert prefix.select(Instant(*at), known, left=left) == expected
    old = FactVersions(KEY)
    first = old.with_version(versions[0], 1)
    abandoned = first.with_version(versions[1], 2)
    replacement = first.with_version(versions[2], 3)
    assert len(old) == 0 and list(first) == versions[:1]
    assert list(abandoned) == versions[:2]
    assert list(replacement) == [versions[0], versions[2]]


def test_append_overlays_and_pending_heaps_have_stable_divergent_prefixes():
    base = Overlay({"none": None, "deleted": 7})
    branch = base.fork()
    del branch["deleted"]
    assert base["deleted"] == 7 and "deleted" not in branch
    branch["deleted"] = 9
    assert branch.get("none", 7) is None and branch.get("missing", 8) == 8
    with pytest.raises(KeyError):
        del branch["missing"]
    for i in range(40):
        branch = branch.fork()
        branch[str(i)] = i
    assert branch["deleted"] == 9 and base["deleted"] == 7
    old = AppendList([1])
    a, b = old.fork(), old.fork()
    a.append(2)
    b.append(3)
    assert list(old) == [1] and list(a) == [1, 2] and list(b) == [1, 3]
    assert a[-1] == 2 and a[:] == [1, 2]
    with pytest.raises(IndexError):
        _ = old[1]
    queue = WorkQueue()
    for recipient, deadline in (("a", 5), ("b", 2), ("a", 1)):
        queue.append(Work(recipient, Instant(deadline), ItemRef(1, deadline)))
    fork = queue.fork()
    assert fork.take("a", Instant(3)) == [queue[-1]]
    assert len(queue) == 3 and len(fork) == 2
    assert queue[:2] == list(queue)[:2]
    assert fork.earliest() == Instant(2) and queue.earliest() == Instant(1)
    timers = TimerQueue()
    timers.add(("a", "one"), Instant(1))
    timers.add(("b", "two"), Instant(2))
    canceled = timers.fork()
    canceled.cancel(("a", "one"))
    assert canceled.earliest() == Instant(2) and timers.earliest() == Instant(1)
    assert canceled.take(Instant(2)) == [("b", "two")]
    assert canceled.earliest() is None
    assert timers.take(Instant(2)) == [("a", "one"), ("b", "two")]


@pytest.mark.parametrize("value", (None, True, 7, 0.0, {"nested": [7, None, "é"]}))
def test_compact_trusted_encoding_equals_canonical_encoding_and_replays(value):
    class Writer(SimpleEngine):
        def initialize(self, view):
            return (
                Create(REF),
                FactWrite(
                    KEY,
                    value,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(0), None),
                ),
            )

    schema = {"type": "integer", "nullable": True}
    if isinstance(value, bool):
        schema = {"type": "boolean"}
    elif isinstance(value, float):
        schema = {"type": "number"}
    elif isinstance(value, dict):
        schema = {"type": "record", "members": {}, "required": [], "extra": True}
    k = Kernel()
    k.bind(
        MemoryRegistry((TypeDescriptor("T"),), (FieldDescriptor("x", "T", schema),)),
        BindingManifest(
            "r",
            "e",
            (REF,),
            rules=(BindingRule("p", "T", ("x",)),),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (Writer(Partition("p", "e", produces=("x",), lifecycle=True)),),
    )
    k.start()
    lines = k.journal.bytes.splitlines(keepends=True)
    for line in lines:
        record = parse_json(line)
        assert canonical_json(record) == line
        if "fact_tables" in record:
            assert Journal().append(record, trusted_fact_rows=True) == line
    actual = replay(k.journal.bytes).view().field(KEY, Instant(0)).value
    if isinstance(value, dict):
        assert set(actual) == {"nested"} and actual["nested"] == (7, None, "é")
    else:
        assert type(actual) is type(value) and actual == value
    compact = next(
        parse_json(line) for line in lines if "fact_tables" in parse_json(line)
    )
    with pytest.raises(ResourceLimit):
        Journal(budget=ResourceBudget(frame_bytes=20)).append(
            compact, trusted_fact_rows=True
        )
    for depth in (1, 3, 5):
        with pytest.raises(ResourceLimit):
            Journal(budget=ResourceBudget(nesting_depth=depth)).append(
                compact, trusted_fact_rows=True
            )
    fact_index = next(i for i, item in enumerate(compact["items"]) if "$fact" in item)
    compact["items"][fact_index]["$fact"][1] = 100
    with pytest.raises(KernelError, match="JOURNAL_FACT_ROW"):
        proposal(compact, fact_index)


def test_file_and_unreadable_sink_do_not_duplicate_or_lose_acknowledged_lines(tmp_path):
    path = tmp_path / "wal.jsonl"
    journal = Journal(path)
    first = journal.append({"first": 7})
    second = journal.append({"next": 9})
    assert journal.bytes == path.read_bytes() == first + second
    assert journal._retained is None
    journal.close()
    assert journal.bytes == first + second

    class Sink(io.BytesIO):
        def readable(self):
            return False

    sink = Sink()
    external = Journal(sink)
    external.append({"first": 7})
    assert external.bytes == first
    external.close()
    assert not sink.closed
    log = RecordLog(budget=ResourceBudget())
    log.append({"first": 7})
    frozen = log.fork()
    log.freeze_tail(first)
    assert list(frozen) == [{"first": 7}] and log[:] == [{"first": 7}]


def test_value_only_future_valid_start_and_expiry_after_reactive_publication():
    observed = []

    class Writer(SimpleEngine):
        written = False

        def initialize(self, view):
            return (Create(REF),)

        def on_react(self, view, inbox, dirty):
            if self.written:
                return ()
            self.written = True
            return (
                FactWrite(
                    KEY,
                    7,
                    Stamp("canonical", 0, 1, "canonical"),
                    Interval(Instant(2), Instant(4)),
                ),
            )

    class Observer(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            observed.append(
                (
                    view.instant.ns,
                    view.field(KEY, view.instant),
                    [d.value_changed for d in dirty],
                )
            )
            return ()

    k = Kernel()
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),), (FieldDescriptor("x", "T", {"type": "integer"}),)
        ),
        BindingManifest(
            "r",
            "e",
            (REF,),
            rules=(BindingRule("p", "T", ("x",)),),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (
            Writer(Partition("p", "p", produces=("x",), lifecycle=True)),
            Observer(Partition("o", "o", consumes=(Dependency("x", value_only=True),))),
        ),
    )
    k.start()
    k.run_until(4)
    assert [ns for ns, _, _ in observed] == [2, 4]
    assert observed[0][1].value == 7 and observed[1][1] is ABSENT
    assert all(flags == [True] for _, _, flags in observed)


@pytest.mark.parametrize("malformation", ("missing", "due", "kind", "payload"))
def test_malformed_timer_state_raises_contextual_kernel_error(malformation):
    from aerokernel import ScheduleTimer
    from aerokernel.control import boundary_control

    class Timer(SimpleEngine):
        def initialize(self, view):
            return (ScheduleTimer("t", Instant(1), 7),)

    k = Kernel()
    k.bind(
        MemoryRegistry(()),
        BindingManifest("r", "e"),
        (Timer(Partition("kernel", "e")),),
    )
    k.start()
    candidate = k._candidate(Instant(1))
    key = ("kernel", "t")
    record = dict(candidate.state.timers[key])
    if malformation == "missing":
        del candidate.state.timers[key]
    else:
        if malformation == "due":
            record["due"] = {"$type": "Instant", "fields": {"ns": True, "microstep": 0}}
        elif malformation == "kind":
            record["kind"] = "validity"
        else:
            del record["payload"]
        candidate.state.timers[key] = record
    with pytest.raises(KernelError, match="TIMER_RECORD") as caught:
        boundary_control(candidate)
    assert caught.value.context["timer_key"] == key
    assert k._store.timers[key]["state"] == "pending"
