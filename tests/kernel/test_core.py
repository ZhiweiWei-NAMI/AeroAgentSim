"""End-to-end invariants: no public mutation path, live/replay shared validation."""

from __future__ import annotations

import io

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    CommandRequest,
    Create,
    Cut,
    EntityRef,
    Fact,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Journal,
    Kernel,
    KernelError,
    LifecycleRule,
    MemoryRegistry,
    MessageDescriptor,
    MicrostepLimitExceeded,
    Partition,
    Remove,
    RetractFact,
    Stamp,
    Timing,
    replay,
)
from aerokernel.codec import encode
from aerokernel.journal import prefixes
from aerokernel.testing import SimpleEngine
from aerokernel.values import canonical_json, parse_json

REF = EntityRef("test", "epoch", "entity", 0, "Thing")


def write(view, value, start=None, end=None):
    return FactWrite(
        (REF, "value"),
        value,
        Stamp("canonical", view.instant.ns, 1, "canonical"),
        Interval(view.instant if start is None else start, end),
    )


def contracts(*, messages=(), rules=None, entities=(REF,), cohorts=()):
    registry = MemoryRegistry(
        (__import__("aerokernel").TypeDescriptor("Thing"),),
        (FieldDescriptor("value", "Thing", {"type": "integer", "nullable": True}),),
        messages,
    )
    manifest = BindingManifest(
        "test",
        "epoch",
        entities,
        rules=(BindingRule("owner", "Thing", ("value",)),) if rules is None else rules,
        lifecycle=(LifecycleRule("owner", "Thing"),),
        cohorts=cohorts,
    )
    return registry, manifest


class Script(SimpleEngine):
    def __init__(self, script=(), initial=0):
        super().__init__(
            Partition("owner", "engine", produces=("value",), lifecycle=True)
        )
        self.script = list(script)
        self.initial = initial
        self.wakeup_ns = self.script[0][0] if self.script else None
        self.reads = []

    def initialize(self, view):
        return (Create(REF), write(view, self.initial))

    def integrate(self, view):
        ns, operation = self.script.pop(0)
        assert ns == view.instant.ns
        self.wakeup_ns = self.script[0][0] if self.script else None
        self.reads.append(view.cut)
        return tuple(operation(view))


def make(engine=None, **kwargs):
    k = Kernel(**kwargs)
    k.bind(*contracts(), (Script() if engine is None else engine,))
    return k


def test_prefix_cuts_immutability_null_absent_and_history():
    engine = Script(
        (
            (1, lambda v: (write(v, None),)),
            (
                2,
                lambda v: (
                    RetractFact(
                        (REF, "value"), Interval(v.instant, None), "unavailable"
                    ),
                ),
            ),
        )
    )
    k = make(engine)
    old = k.start()
    cut = old.cut
    assert old.field((REF, "value"), Instant(0)).value == 0
    null_view = k.run_until(1)
    assert null_view.field((REF, "value"), Instant(1)).value is None
    k.run_until(2)
    assert k.view().field((REF, "value"), Instant(2)) is ABSENT
    assert old.field((REF, "value"), Instant(2)).value == 0
    assert k.view(cut).field((REF, "value"), Instant(2)).value == 0
    assert len(k.view().history((REF, "value"), Instant(0), Instant(3))) == 3
    with pytest.raises(KernelError):
        k.view(Cut(cut.index, Instant(999)))
    with pytest.raises(KernelError):
        old.field((REF, "inactive"), Instant(0))
    assert not hasattr(k, "commit")


def test_invalid_last_operation_and_journal_failure_are_atomic():
    k = make(Script(((1, lambda v: (write(v, 1), write(v, 2))),)))
    old = k.start()
    with pytest.raises(KernelError):
        k.run_until(1)
    assert k.view().field((REF, "value"), Instant(1)).value == 0
    assert old.field((REF, "value"), Instant(1)).value == 0
    assert k.records[-1]["type"] == "fault"
    recovered = replay(k.journal.bytes)
    assert recovered.view().field((REF, "value"), Instant(1)).value == 0
    with pytest.raises(KernelError):
        k.run_until(2)

    class FailingSink(io.BytesIO):
        fail = False

        def flush(self):
            if self.fail:
                raise OSError("authored failure")

    sink = FailingSink()
    k = make(journal=Journal(sink))
    k.start()
    before = k.view().cut
    sink.fail = True
    with pytest.raises(KernelError):
        k.run_until(1)
    assert k.view().cut == before
    with pytest.raises(KernelError):
        k.run_until(2)


def test_remove_historical_view_and_reuse_new_type_generation():
    class Removing(Script):
        removed = False

        def on_react(self, view, inbox, dirty):
            if view.instant.ns == 1 and not self.removed:
                self.removed = True
                return (Remove(REF),)
            return ()

        def integrate(self, view):
            self.wakeup_ns = None
            return (Activate("owner"),)

    engine = Removing(((1, None),))
    k = make(engine)
    old = k.start()
    k.run_until(1)
    assert k.view().field((REF, "value"), Instant(1, 100)) is ABSENT
    assert old.field((REF, "value"), Instant(1, 100)).value == 0
    assert k.view(old.cut).lifecycle(REF).removed is None
    assert (
        replay(k.journal.bytes).view().field((REF, "value"), Instant(1, 100)) is ABSENT
    )


def test_zero_work_deadlock_and_global_microstep_storm():
    class Storm(Script):
        def on_react(self, view, inbox, dirty):
            return (Activate("owner"),)

    k = make(Storm(), max_microsteps=4)
    with pytest.raises(MicrostepLimitExceeded):
        k.start()
    assert k.records[-1]["type"] == "fault"

    class Stalled(Script):
        def horizon(self, partition, cut):
            from aerokernel import Horizon

            return Horizon(
                self.logical,
                self.native_ns,
                self.logical.ns,
                self.logical.ns,
                None,
                cut,
            )

    k = make(Stalled())
    from aerokernel import SynchronizationDeadlock

    with pytest.raises(SynchronizationDeadlock):
        k.start()


@settings(max_examples=25, deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 20),
            st.integers(1, 30),
            st.one_of(st.none(), st.integers(-10, 10)),
        ),
        min_size=1,
        max_size=8,
    )
)
def test_bitemporal_oracle_and_every_prefix_replay(specs):
    operations = []
    intervals = []
    for publication, (start, duration, value) in enumerate(specs, 1):
        end = start + duration
        intervals.append((publication, start, end, value))
        operations.append(
            (
                publication,
                lambda view, start=start, end=end, value=value: (
                    write(view, value, Instant(start), Instant(end)),
                ),
            )
        )
    k = make(Script(operations))
    k.start()
    k.run_until(len(specs))
    for valid in range(25):
        expected = 0
        for _publication, start, end, value in intervals:
            if start <= valid < end:
                expected = value
        actual = k.view().field((REF, "value"), Instant(valid))
        assert isinstance(actual, Fact) and actual.value == expected
    for prefix in prefixes(k.journal.bytes):
        reconstructed = replay(prefix)
        for valid in (0, 7, 19):
            cut = reconstructed.view().cut
            if any(r["type"] == "transaction" for r in reconstructed.records):
                expected = k.view(cut).field((REF, "value"), Instant(valid))
                actual = reconstructed.view().field((REF, "value"), Instant(valid))
                assert encode(actual) == encode(expected)


def test_truncation_and_complete_corruption_fail_closed():
    k = make()
    k.start()
    data = k.journal.bytes
    with pytest.raises(KernelError):
        replay(data[:-1])
    result = replay(data[:-1], recover_truncated=True)
    assert result.incomplete
    records = [parse_json(line) for line in data.splitlines(keepends=True)]
    records[-1]["index"] += 1
    with pytest.raises(KernelError):
        replay(b"".join(canonical_json(r) for r in records))
    with pytest.raises(KernelError):
        replay(data + b'{"type":"bad"}\n')


def test_fixed_grid_latches_command_after_completed_interval():
    seen = []

    class Fixed(SimpleEngine):
        def __init__(self):
            super().__init__(
                Partition(
                    "owner",
                    "engine",
                    produces=("value",),
                    lifecycle=True,
                    commands=("set",),
                    timing=Timing("fixed_step", 20),
                )
            )
            self.input = 0

        def initialize(self, view):
            return (Create(REF), write(view, 0))

        def integrate(self, view):
            seen.append(
                (
                    "integrate",
                    view.instant.ns,
                    self.input,
                    view.cut,
                    view.native_input_cut,
                )
            )
            return (write(view, self.input),)

        def on_react(self, view, inbox, dirty):
            if inbox:
                seen.append(("react", view.instant.ns))
                self.input = 9
            return ()

    registry, manifest = contracts(
        messages=(MessageDescriptor("set", "command", {"type": "integer"}),)
    )
    k = Kernel()
    k.bind(registry, manifest, (Fixed(),))
    initial = k.start().cut
    mid = k.submit(CommandRequest("set", "owner", Instant(3), 9, ingress_at_ns=3))
    assert k.action(mid).status == "pending"
    k.run_until(3)
    assert k.action(mid).status == "submitted"
    assert k.view().field((REF, "value"), Instant(3)).value == 0
    k.run_until(20)
    assert seen[0] == ("integrate", 20, 0, initial, initial)
    assert seen[1] == ("react", 20)
    k.run_until(40)
    assert seen[2][2] == 9
    replay(k.journal.bytes)
