"""Independent selector/activation oracle over rule orders, types and identities."""

import itertools

import pytest

from aerokernel import (
    BindingManifest,
    BindingRule,
    EntityRef,
    ExactBinding,
    FieldDescriptor,
    KernelError,
    MemoryRegistry,
    Partition,
    TypeDescriptor,
)


def test_selector_orders_priorities_types_patterns_and_exact_activation():
    registry = MemoryRegistry(
        (
            TypeDescriptor("Root"),
            TypeDescriptor("Leaf", ("Root",)),
            TypeDescriptor("Peer"),
        ),
        (
            FieldDescriptor("x", "Root", {"type": "integer"}),
            FieldDescriptor("optional", "Peer", {"type": "integer"}),
        ),
    )
    parts = {
        pid: Partition(pid, pid, produces=("x", "optional"))
        for pid in ("base", "leaf", "capability", "peer")
    }
    refs = [
        EntityRef("r", "e", identity, generation, kind)
        for identity, generation, kind in (
            ("unit-a", 0, "Leaf"),
            ("unit-b", 0, "Leaf"),
            ("unit-a", 0, "Root"),
            ("other", 0, "Leaf"),
            ("unit-a", 0, "Peer"),
            ("other", 1, "Leaf"),
        )
    ]
    cases = 0
    for a, b, c in itertools.product((-1, 0, 1), repeat=3):
        for restricted, override in itertools.product((False, True), repeat=2):
            rules = (
                BindingRule("base", "Root", ("x",), "unit-*" if restricted else "*", a),
                BindingRule("leaf", "Leaf", ("x",), "unit-*", b),
                # Explicitly applying a capability field does not add inheritance.
                BindingRule("capability", "Leaf", ("optional",), "unit-a", c),
                BindingRule("peer", "Peer", ("x",), "unit-a", 100),
            )
            exact = (ExactBinding(refs[3], "x", "capability"),) if override else ()
            for order in itertools.permutations(rules):
                manifest = BindingManifest("r", "e", rules=order, exact=exact)
                for i, ref in enumerate(refs):
                    # Applicability is authored by case, not calculated with the
                    # production type DAG or pattern/priority selector functions.
                    options = (
                        [(a, "base"), (b, "leaf")]
                        if i in (0, 1)
                        else [(a, "base")]
                        if i == 2 or (i in (3, 5) and not restricted)
                        else [(100, "peer")]
                        if i == 4
                        else []
                    )
                    expected = {"optional": "capability"} if i == 0 else {}
                    if override and i == 3:
                        expected["x"] = "capability"
                    elif options:
                        best = max(priority for priority, _ in options)
                        winners = [pid for priority, pid in options if priority == best]
                        if len(winners) != 1:
                            with pytest.raises(KernelError, match="BINDING_AMBIGUOUS"):
                                manifest.resolve(ref, registry, parts)
                            cases += 1
                            continue
                        expected["x"] = winners[0]
                    assert manifest.resolve(ref, registry, parts) == expected
                    cases += 1
    assert cases == 27 * 4 * 24 * 6
