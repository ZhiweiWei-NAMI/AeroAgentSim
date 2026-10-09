"""Stable real-ancestry/entity/task/relation tuple selection."""

from __future__ import annotations

import hashlib
from itertools import product
from typing import Any

from aerokernel import EntityRef, MemoryRegistry
from aerokernel.sdk import EngineContext
from aerokernel.state import Absent
from aerokernel.values import canonical_json, thaw


def stable_id(value: object, prefix: str = "behaviour") -> str:
    return prefix + ":" + hashlib.sha256(canonical_json(value)).hexdigest()


def live_refs(ctx: EngineContext) -> tuple[EntityRef, ...]:
    return tuple(
        sorted(
            (
                r
                for r, life in ctx.view._store.lives.items()
                if life.created.index <= ctx.view.cut.index
                and ctx.view._store.alive(r, ctx.view.cut, ctx.now)
            ),
            key=lambda r: (r.id, r.generation, r.type_id),
        )
    )


def tuples(
    ctx: EngineContext,
    registry: MemoryRegistry,
    roles: dict[str, str],
    match: dict[str, Any],
    *,
    live: tuple[EntityRef, ...] | None = None,
    eligible: dict[
        tuple[str, str | None, str | None], tuple[EntityRef, ...]
    ] | None = None,
) -> list[tuple[dict[str, EntityRef], tuple[object, ...]]]:
    names = sorted(roles)
    if live is None:
        live = live_refs(ctx)
    choices = []
    for name in names:
        selector = match.get(name, {})
        candidates = []
        key = (roles[name], selector.get("is_a"), selector.get("entity"))
        pool = None if eligible is None else eligible.get(key)
        if pool is None:
            pool = tuple(
                ref
                for ref in live
                if registry.is_a(ref.type_id, key[0])
                and (key[1] is None or registry.is_a(ref.type_id, key[1]))
                and (key[2] is None or ref.id == key[2])
            )
            if eligible is not None:
                eligible[key] = pool
        for ref in pool:
            if "field" in selector:
                value = ctx.get(ref, selector["field"])
                if isinstance(value, Absent):
                    continue
                actual = thaw(value)
                if (
                    "equals" in selector
                    and actual != selector["equals"]
                    or "in" in selector
                    and actual not in selector["in"]
                ):
                    continue
            candidates.append(ref)
        choices.append(candidates)
    candidates_by_role = dict(zip(names, choices, strict=True))
    selections: list[tuple[dict[str, EntityRef], tuple[object, ...]]] = [({}, ())]
    selectors = {
        (selector["relation"], selector["source_role"], selector["target_role"])
        for selector in match.values()
        if "relation" in selector
    }
    for relation, source_role, target_role in sorted(selectors):
        edges = ctx.relations(relation)
        joined: list[tuple[dict[str, EntityRef], tuple[object, ...]]] = []
        for selected, versions in selections:
            for edge in edges:
                if (
                    edge.source not in candidates_by_role[source_role]
                    or edge.target not in candidates_by_role[target_role]
                ):
                    continue
                if (
                    source_role in selected
                    and selected[source_role] != edge.source
                    or target_role in selected
                    and selected[target_role] != edge.target
                ):
                    continue
                joined.append(
                    (
                        {
                            **selected,
                            source_role: edge.source,
                            target_role: edge.target,
                        },
                        (
                            *versions,
                            (
                                edge.edge_id,
                                edge.version.record_index,
                                edge.version.item_index,
                            ),
                        ),
                    )
                )
        selections = joined
    result: list[tuple[dict[str, EntityRef], tuple[object, ...]]] = []
    for selected, versions in selections:
        remaining = [name for name in names if name not in selected]
        for combination in product(*(candidates_by_role[name] for name in remaining)):
            result.append(
                (
                    {**selected, **dict(zip(remaining, combination, strict=True))},
                    versions,
                )
            )
    return sorted(
        result,
        key=lambda item: tuple(
            (role, ref.id, ref.generation) for role, ref in sorted(item[0].items())
        ),
    )
