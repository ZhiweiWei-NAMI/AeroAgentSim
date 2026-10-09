"""Authored interval-list oracle, including retractions/null and both time axes."""

import random

import pytest

from aerokernel import (
    ABSENT,
    Activate,
    BindingManifest,
    BindingRule,
    Create,
    Dependency,
    EntityRef,
    Fact,
    FactWrite,
    FieldDescriptor,
    Instant,
    Interval,
    Kernel,
    LifecycleRule,
    MemoryRegistry,
    Partition,
    Remove,
    RetractFact,
    Stamp,
    Timing,
    TypeDescriptor,
    replay,
)
from aerokernel.codec import decode_record
from aerokernel.journal import prefixes
from aerokernel.sdk import SimpleEngine

ref = EntityRef("r", "e", "id", 0, "T")
missing = object()


@pytest.mark.parametrize("seed", range(6))
def test_two_axis_interval_list_oracle_with_lag_native_and_death(seed):
    checks = 0
    reads = []
    rng = random.Random(seed)
    plans = []
    for publication in range(1, 7):
        start = (rng.randrange(0, 9), rng.randrange(0, 4))
        end = (
            None
            if rng.randrange(3) == 0
            else (start[0] + rng.randrange(1, 5), rng.randrange(0, 4))
        )
        plans.append(
            (
                publication,
                start,
                end,
                rng.choice(("fact", "retract")),
                rng.choice((None, -3, 4)),
            )
        )

    class E(SimpleEngine):
        cursor = 0
        removed = False

        def initialize(self, view):
            self.wakeup_ns = 1
            return (
                Create(ref),
                FactWrite(
                    (ref, "x"),
                    0,
                    Stamp("canonical", -1, 1, "canonical"),
                    Interval(Instant(0), None),
                ),
            )

        def integrate(self, view):
            reads.append((view.instant, view.cut, view.field((ref, "x"), view.instant)))
            if view.instant.ns == 7:
                self.wakeup_ns = None
                return (Activate("p"),)
            publication, start, end, kind, value = plans[self.cursor]
            self.cursor += 1
            self.wakeup_ns = self.cursor + 1
            valid = Interval(Instant(*start), None if end is None else Instant(*end))
            op = (
                FactWrite(
                    (ref, "x"),
                    value,
                    Stamp("canonical", publication - 10, 1, "canonical"),
                    valid,
                )
                if kind == "fact"
                else RetractFact((ref, "x"), valid, "authored unavailable")
            )
            return (op,)

        def on_react(self, view, inbox, dirty):
            if view.instant.ns == 7 and not self.removed:
                self.removed = True
                return (Remove(ref),)
            return ()

    class Reader(SimpleEngine):
        def on_react(self, view, inbox, dirty):
            at = Instant(max(0, view.instant.ns - 2))
            reads.append((at, view.cut, view.field((ref, "x"), at), view.instant))
            return ()

    k = Kernel(provenance="full")
    k.bind(
        MemoryRegistry(
            (TypeDescriptor("T"),),
            (FieldDescriptor("x", "T", {"type": "integer", "nullable": True}),),
        ),
        BindingManifest(
            "r",
            "e",
            (ref,),
            rules=(BindingRule("p", "T", ("x",)),),
            lifecycle=(LifecycleRule("p", "T"),),
        ),
        (
            E(
                Partition(
                    "p",
                    "e",
                    produces=("x",),
                    lifecycle=True,
                    timing=Timing("fixed_step", 1),
                )
            ),
            Reader(
                Partition("reader", "reader", consumes=(Dependency("x", lag_ns=2),))
            ),
        ),
    )
    k.start()
    k.run_until(7)
    publication_indices = {
        decode_record(r["instant"]).ns: r["index"]
        for r in k.records
        if r["type"] == "transaction" and r["phase"] in ("reset", "advance")
    }
    versions = [(publication_indices[0], (0, 0), None, "fact", 0)] + [
        (publication_indices[p], start, end, kind, value)
        for p, start, end, kind, value in plans
    ]
    death_record = next(
        r
        for r in k.records
        if r["type"] == "transaction"
        and any(
            item.get("proposal", {}).get("$type") == "Remove" for item in r["items"]
        )
    )
    death_index = death_record["index"]
    death_instant = decode_record(death_record["instant"])
    death_at = (death_instant.ns, death_instant.microstep)
    for prefix in prefixes(k.journal.bytes):
        reconstructed = replay(prefix)
        cut = reconstructed.view().cut
        if cut.index < publication_indices[0]:
            continue
        removed = cut.index >= death_index
        for ns in range(11):
            for microstep in (0, 1, 3):
                at = (ns, microstep)
                expected = missing
                for index, start, end, kind, value in versions:
                    if index <= cut.index and start <= at and (end is None or at < end):
                        expected = missing if kind == "retract" else value
                if removed and at >= death_at:
                    expected = missing
                for view in (k.view(cut), reconstructed.view()):
                    actual = view.field((ref, "x"), Instant(*at))
                    if expected is missing:
                        assert actual is ABSENT, (seed, cut, at, actual)
                    else:
                        assert (
                            isinstance(actual, Fact)
                            and type(actual.value) is type(expected)
                            and actual.value == expected
                        )
                    checks += 1
    for entry in reads:
        at, cap, actual = entry[:3]
        knowledge = cap.index
        if len(entry) == 4:
            limit = entry[3].ns - 2
            knowledge = max(
                (
                    r["index"]
                    for r in k.records
                    if r["index"] <= cap.index
                    and decode_record(r["instant"]).ns <= limit
                ),
                default=0,
            )
        else:
            assert cap.instant.ns == at.ns - 1
        expected = missing
        for index, start, end, kind, value in versions:
            point = (at.ns, at.microstep)
            if index <= knowledge and start <= point and (end is None or point < end):
                expected = missing if kind == "retract" else value
        if knowledge >= death_index and at >= death_instant:
            expected = missing
        if expected is missing:
            assert actual is ABSENT
        else:
            assert (
                isinstance(actual, Fact)
                and type(actual.value) is type(expected)
                and actual.value == expected
            )
    assert checks > 1000
