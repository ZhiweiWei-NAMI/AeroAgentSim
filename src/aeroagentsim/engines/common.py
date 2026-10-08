"""Explicit canonical model policies and owner-authored initialization."""

from __future__ import annotations

from collections.abc import Iterable

from aerokernel import Interval, Stamp
from aerokernel.sdk import EngineContext, FactPolicy

from aeroagentsim.platform.plugins import EngineBuild


def policies(fields: Iterable[str]) -> dict[str, FactPolicy]:
    return {
        field: FactPolicy(
            acquired=lambda now: Stamp("canonical", now.ns, 1, "canonical"),
            valid=lambda now: Interval(now, None),
        )
        for field in fields
    }


def bootstrap_owned(ctx: EngineContext, build: EngineBuild) -> None:
    partitions = build.partitions
    for ref in build.entities:
        if build.manifest.controller(ref, build.registry, partitions) == build.id:
            ctx.create(ref)
        owners = build.manifest.resolve(ref, build.registry, partitions)
        for field, value in build.initial[ref.id].items():
            if owners.get(field) == build.id:
                ctx.set(ref, field, value)
