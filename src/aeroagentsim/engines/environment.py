"""Typed, scenario-authored environmental profiles with exact DES boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from aerokernel import EntityRef, Partition
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.values import thaw

from aeroagentsim.platform.plugins import EngineBuild

from .common import bootstrap_owned, policies


@dataclass(frozen=True)
class Profile:
    ref: EntityRef
    field: str
    base: Any
    gusts: tuple[tuple[int, int, Any], ...]

    def value_at(self, ns: int) -> Any:
        for start, end, value in self.gusts:
            if start <= ns < end:
                return value
        return self.base

    @property
    def boundaries(self) -> tuple[int, ...]:
        return tuple(
            sorted({t for start, end, _ in self.gusts for t in (start, end) if t > 0})
        )


class Environment(ContextEngine):
    """Publish authored values; unit/frame/schema authority stays in the registry.

    Calm and constant retain their explicit initial values. Gusts are disjoint
    half-open intervals over the explicit base value. Timers publish at actual
    boundaries, retaining timer causes, without a polling/integration loop.
    """

    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        c = build.config
        if set(c) != {"produces", "lifecycle", "profiles"}:
            raise ValueError(
                "environment.config: declare produces, lifecycle, profiles"
            )
        if type(c["lifecycle"]) is not bool:
            raise TypeError("environment.lifecycle: bool required")
        fields = c["produces"]
        if (
            not isinstance(fields, list)
            or not fields
            or any(type(f) is not str or not f for f in fields)
        ):
            raise ValueError("environment.produces: nonempty field ID list required")
        if len(set(fields)) != len(fields):
            raise ValueError("environment.produces: duplicate field IDs")
        if not isinstance(c["profiles"], dict) or not c["profiles"]:
            raise ValueError("environment.profiles: nonempty mapping required")
        refs = {r.id: r for r in build.entities}
        self.profiles: dict[tuple[str, str], Profile] = {}
        for identity, slots in c["profiles"].items():
            if identity not in refs or not isinstance(slots, dict) or not slots:
                raise ValueError(
                    "environment.profiles: bind an actual entity and its fields"
                )
            ref = refs[identity]
            for field, authored in slots.items():
                if field not in fields or field not in build.owned_fields(ref):
                    raise ValueError(
                        f"environment.{identity}.{field}: profile must select its field writer"
                    )
                descriptor = build.registry.field(field)
                if not build.registry.is_a(ref.type_id, descriptor.declaring_type):
                    raise ValueError(
                        "environment.profile: field does not apply to entity type"
                    )
                if not isinstance(authored, dict) or authored.get("mode") not in {
                    "calm",
                    "constant",
                    "gust",
                }:
                    raise ValueError(
                        "environment.profile: declare calm, constant or gust mode"
                    )
                required = (
                    {"mode", "value", "gusts"}
                    if authored["mode"] == "gust"
                    else {"mode", "value"}
                )
                if set(authored) != required:
                    raise ValueError(
                        "environment.profile: unknown or missing profile keys"
                    )
                build.registry.validate(descriptor.schema, authored["value"])
                intervals: list[tuple[int, int, Any]] = []
                if authored["mode"] == "gust":
                    gusts = authored["gusts"]
                    if not isinstance(gusts, list) or not gusts:
                        raise ValueError(
                            "environment.gusts: nonempty interval list required"
                        )
                    for gust in gusts:
                        if not isinstance(gust, dict) or set(gust) != {
                            "start_ns",
                            "end_ns",
                            "value",
                        }:
                            raise ValueError(
                                "environment.gust: declare start_ns, end_ns, value"
                            )
                        start, end = gust["start_ns"], gust["end_ns"]
                        if (
                            type(start) is not int
                            or type(end) is not int
                            or not 0 <= start < end
                        ):
                            raise ValueError(
                                "environment.gust: nonempty nonnegative integer interval required"
                            )
                        build.registry.validate(descriptor.schema, gust["value"])
                        intervals.append((start, end, gust["value"]))
                    intervals.sort(key=lambda g: g[0])
                    if any(a[1] > b[0] for a, b in pairwise(intervals)):
                        raise ValueError("environment.gusts: overlapping intervals")
                profile = Profile(ref, field, authored["value"], tuple(intervals))
                initial = build.initial[ref.id]
                if field in initial and initial[field] != profile.value_at(0):
                    raise ValueError(
                        f"environment.{identity}.{field}: initial fact must match profile at zero"
                    )
                self.profiles[(identity, field)] = profile
        expected = {
            (r.id, f)
            for r in build.entities
            for f in build.owned_fields(r)
            if f in fields
        }
        if set(self.profiles) != expected or {f for _, f in expected} != set(fields):
            raise ValueError("environment.profiles: cover every selected owned field")
        self.current = {key: p.value_at(0) for key, p in self.profiles.items()}
        super().__init__(
            Partition(
                build.id, build.id, produces=tuple(fields), lifecycle=c["lifecycle"]
            ),
            policies=policies(fields),
        )

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)
        # Scheduling all distinct boundaries is finite, deterministic and allows
        # adjacent intervals to switch once at their shared endpoint.
        for key, profile in sorted(self.profiles.items()):
            if profile.field not in self.build.initial[profile.ref.id]:
                # The explicitly selected profile is the actual generating
                # source; an identical copy in initial facts is unnecessary.
                ctx.set(profile.ref, profile.field, profile.value_at(0))
            for ns in profile.boundaries:
                ctx.wake_at(ns, {"entity": key[0], "field": key[1]})

    def on_inputs(self, ctx: EngineContext) -> None:
        seen: set[tuple[str, str]] = set()
        for dirty in ctx.dirty:
            if dirty.kind != "timer":
                continue
            data = thaw(dirty.payload)
            if (
                not isinstance(data, dict)
                or set(data) != {"entity", "field"}
                or any(type(v) is not str for v in data.values())
            ):
                raise ValueError(
                    "environment.timer: typed profile coordinates required"
                )
            key = (str(data["entity"]), str(data["field"]))
            if key in seen:
                continue
            seen.add(key)
            profile = self.profiles[key]
            value = profile.value_at(ctx.now.ns)
            if value != self.current[key]:
                ctx.inputs = [dirty.cause]
                ctx.set(profile.ref, profile.field, value)
                self.current[key] = value


def build(context: EngineBuild) -> Environment:
    return Environment(context)
