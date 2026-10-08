"""Exercise actual kernel temporal gaps/conflicts required by record relations."""

from __future__ import annotations

import pytest
from aerokernel import EntityRef, Instant, Interval, Stamp
from aerokernel.errors import KernelError
from aerokernel.ids import ItemRef
from aerokernel.relation_sweep import validate_graph
from aerokernel.relations import Cardinality, Edge, Obligation, RelationDescriptor

REFS = [EntityRef("test", "0", name, 0, "thing") for name in ("a", "b", "c")]
DESCRIPTOR = RelationDescriptor(
    "rel", "thing", "thing", Cardinality(1, 1), Cardinality(0, 1)
)


def edge(identity: str, source: int, target: int, start: int, end: int | None) -> Edge:
    return Edge(
        identity,
        "rel",
        REFS[source],
        REFS[target],
        Interval(Instant(start), None if end is None else Instant(end)),
        Stamp("canonical", start, 1, "canonical"),
        Instant(0),
        "owner",
        ItemRef(1, 0),
    )


def obligation(start: int, end: int | None) -> Obligation:
    return Obligation(
        "minimum",
        "rel",
        "targets_per_source",
        REFS[0],
        Interval(Instant(start), None if end is None else Instant(end)),
        Instant(0),
        "owner",
        ItemRef(1, 1),
    )


def test_contiguous_replacement_covers_minimum_and_open_tail() -> None:
    validate_graph(
        DESCRIPTOR,
        [edge("one", 0, 1, 0, 10), edge("two", 0, 2, 10, None)],
        [obligation(0, None)],
    )


@pytest.mark.parametrize(
    "edges,obligations",
    [
        ([edge("one", 0, 1, 0, 10)], [obligation(0, None)]),
        (
            [edge("one", 0, 1, 0, 10), edge("two", 0, 2, 11, None)],
            [obligation(0, None)],
        ),
        ([edge("one", 0, 1, 0, 20), edge("two", 0, 2, 10, 30)], []),
        ([edge("one", 0, 1, 0, None), edge("two", 2, 1, 10, None)], []),
        ([edge("one", 0, 1, 0, None), edge("two", 0, 1, 10, None)], []),
        ([edge("one", 0, 1, 0, None)], [obligation(0, None), obligation(10, None)]),
    ],
)
def test_future_overlap_and_uncovered_minimum_are_rejected(
    edges: list[Edge], obligations: list[Obligation]
) -> None:
    with pytest.raises(KernelError):
        validate_graph(DESCRIPTOR, edges, obligations)
