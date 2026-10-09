"""Half-open interval sweep over the final merged relation graph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from .errors import KernelError
from .ids import EntityRef
from .relations import DIRECTIONS, Edge, Obligation, RelationDescriptor
from .time import Instant


def validate_graph(
    descriptor: RelationDescriptor,
    edges: Sequence[Edge],
    obligations: Sequence[Obligation],
) -> None:
    """Check both directional bounds and activated minima at every boundary."""
    events: dict[Instant, list[tuple[int, Edge | Obligation]]] = defaultdict(list)
    identities: dict[tuple[EntityRef, EntityRef], str] = {}
    for edge in edges:
        if edge.relation_id != descriptor.id:
            raise KernelError("RELATION_DECLARATION", "mixed relation graph")
        pair = (edge.source, edge.target)
        if descriptor.identity_policy == "endpoint_pair":
            if pair in identities and identities[pair] != edge.edge_id:
                raise KernelError("RELATION_PAIR", "endpoint pair identity reused")
            identities[pair] = edge.edge_id
        if edge.valid is not None:
            events[edge.valid.start].append((1, edge))
            if edge.valid.end is not None:
                events[edge.valid.end].append((-1, edge))
    for obligation in obligations:
        if (
            obligation.relation_id != descriptor.id
            or obligation.direction not in DIRECTIONS
        ):
            raise KernelError("RELATION_DECLARATION", "invalid obligation graph")
        if obligation.valid is not None:
            events[obligation.valid.start].append((1, obligation))
            if obligation.valid.end is not None:
                events[obligation.valid.end].append((-1, obligation))
    pairs: set[tuple[EntityRef, EntityRef]] = set()
    counts: dict[tuple[str, EntityRef], int] = defaultdict(int)
    active: set[tuple[str, EntityRef]] = set()
    for at, changes in sorted(events.items()):
        affected: set[tuple[str, EntityRef]] = set()
        for sign, row in sorted(changes, key=lambda item: item[0]):
            if isinstance(row, Edge):
                pair = (row.source, row.target)
                if sign == 1:
                    if pair in pairs:
                        raise KernelError("RELATION_PAIR", "overlapping pair", at=at)
                    pairs.add(pair)
                else:
                    pairs.remove(pair)
                for scope in (
                    ("targets_per_source", row.source),
                    ("sources_per_target", row.target),
                ):
                    counts[scope] += sign
                    affected.add(scope)
            else:
                scope = (row.direction, row.endpoint_ref)
                if sign == 1:
                    if scope in active:
                        raise KernelError("OBLIGATION_OVERLAP", "overlap", at=at)
                    active.add(scope)
                else:
                    active.remove(scope)
                affected.add(scope)
        for direction, endpoint in affected:
            bound = getattr(descriptor, direction)
            count = counts[(direction, endpoint)]
            if bound.maximum is not None and count > bound.maximum:
                raise KernelError(
                    "RELATION_MAXIMUM",
                    "directional maximum exceeded",
                    at=at,
                    direction=direction,
                    endpoint=endpoint,
                )
            if (direction, endpoint) in active and count < bound.minimum:
                raise KernelError(
                    "RELATION_MINIMUM",
                    "activated minimum uncovered",
                    at=at,
                    direction=direction,
                    endpoint=endpoint,
                )
