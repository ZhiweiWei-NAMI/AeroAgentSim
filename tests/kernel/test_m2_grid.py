"""Nonzero native origins and explicitly declared exact splitting."""

from aerokernel import (
    BindingManifest,
    CommandRequest,
    Instant,
    Kernel,
    MemoryRegistry,
    MessageDescriptor,
    Partition,
    Timing,
    replay,
)
from aerokernel.sdk import SimpleEngine


def test_nonzero_grid_origin_holds_until_first_declared_native_boundary():
    calls = []

    class Grid(SimpleEngine):
        def integrate(self, view):
            calls.append((view.instant.ns, view.native_input_cut.instant.ns))
            return ()

    engine = Grid(Partition("p", "e", timing=Timing("fixed_step", 20, origin_ns=10)))
    k = Kernel()
    k.bind(MemoryRegistry(()), BindingManifest("r", "e"), (engine,))
    k.start()
    k.run_until(3)
    assert engine.native_ns == 0 and calls == []
    k.run_until(10)
    k.run_until(20)
    k.run_until(30)
    assert calls == [(10, 0), (30, 10)]
    assert engine.native_ns == 30
    assert replay(k.journal.bytes).records == k.records


def test_exact_split_integrates_to_input_boundary_then_reacts_then_next_grid():
    calls = []

    class Split(SimpleEngine):
        def integrate(self, view):
            calls.append(
                (
                    "integrate",
                    self.native_ns,
                    view.instant.ns,
                    view.native_input_cut.instant.ns,
                )
            )
            return ()  # No sample promised before the declared grid output bound.

        def on_react(self, view, inbox, dirty):
            calls.append(
                ("react", self.native_ns, view.instant.ns, inbox[0].message.payload)
            )
            return ()

    engine = Split(
        Partition(
            "p", "e", commands=("input",), timing=Timing("fixed_step", 20, latch=False)
        )
    )
    k = Kernel()
    k.bind(
        MemoryRegistry(
            (), messages=(MessageDescriptor("input", "command", {"type": "integer"}),)
        ),
        BindingManifest("r", "e"),
        (engine,),
    )
    k.start()
    k.submit(CommandRequest("input", "p", Instant(3), 9, ingress_at_ns=3))
    k.run_until(20)
    assert calls == [
        ("integrate", 0, 3, 0),
        ("react", 3, 3, 9),
        ("integrate", 3, 20, 3),
    ]
    assert replay(k.journal.bytes).records == k.records
