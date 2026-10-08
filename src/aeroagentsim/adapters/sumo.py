"""SUMO dynamic identity, traffic lights, lifecycle evidence and typed controls."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from aerokernel import (
    Activate,
    Create,
    EntityRef,
    FactWrite,
    Instant,
    KernelError,
    LocalCause,
    Partition,
    ScheduleTimer,
    Timing,
)
from aerokernel.messages import Delivery, Dirty
from aerokernel.sdk import Command, EngineContext
from aerokernel.state import StateView

if TYPE_CHECKING:
    from aeroagentsim.platform.plugins import EngineBuild

from .lockstep_base import LockstepEngine, integer, obj, records

ACTIONS = (
    "set_speed",
    "reroute",
    "change_target",
    "lane_restriction",
    "tls_phase",
    "add_vehicle",
    "remove_vehicle",
)
EVENTS = (
    "departed",
    "arrived",
    "removed",
    "teleported_start",
    "teleported_end",
    "collisions",
)
ENTITY_FIELDS = (
    "position",
    "angle",
    "speed",
    "lane",
    "edge",
    "type",
    "route",
    "route_id",
    "route_index",
    "signals",
)
TLS_FIELDS = ("state", "phase", "program", "next_switch_s")


class SumoEngine(LockstepEngine):
    backend = "sumo"
    protocol = "aeroagentsim.sumo/v1"
    quantum_key = "step_quantum_ns"
    actions = ACTIONS

    def __init__(self, context: EngineBuild) -> None:
        config = context.config
        self.kinds = obj(config["kinds"])
        if set(self.kinds) != {"vehicle", "person", "tls"}:
            raise ValueError(
                "kinds must explicitly configure vehicle, person and tls types/prefixes/fields"
            )
        produces: set[str] = set()
        for kind, raw in self.kinds.items():
            fields = obj(obj(raw)["fields"])
            allowed = TLS_FIELDS if kind == "tls" else ENTITY_FIELDS
            if set(fields) - set(allowed):
                raise ValueError(f"unsupported {kind} field mapping")
            produces.update(fields.values())
            context.registry.is_a(raw["type_id"], raw["type_id"])
        self.active: dict[tuple[str, str], EntityRef] = {}
        self.generations: dict[tuple[str, str], int] = {}
        self.staged: tuple[object, ...] = ()
        self.stage_number = 0
        super().__init__(
            context,
            Partition(
                context.id,
                context.id,
                produces=tuple(sorted(produces)),
                lifecycle=True,
                commands=tuple(f"adapters.sumo.{action}" for action in ACTIONS),
                emits=tuple(f"adapters.sumo.{event}" for event in EVENTS),
                message_targets=(config["event_topic"],),
                timing=Timing(
                    "lockstep",
                    integer(config["step_ns"], "step_ns", 1),
                    certified_hold=True,
                ),
            ),
        )

    def initialize(self, view: StateView) -> tuple[object, ...]:
        operations = super().initialize(view)
        initial = [
            i
            for i, op in enumerate(operations)
            if (isinstance(op, Create) and op.ref in self.build.entities)
            or (isinstance(op, FactWrite) and op.key[0] in self.build.entities)
        ]
        pending = [i for i in range(len(operations)) if i not in initial]
        self.staged = self.reindex(
            operations, {old: new for new, old in enumerate(pending)}
        )
        bootstrap = self.reindex(
            operations, {old: new for new, old in enumerate(initial)}
        )
        return bootstrap + ((Activate(self.partition.id),) if self.staged else ())

    @staticmethod
    def reindex(
        operations: tuple[object, ...], indices: dict[int, int]
    ) -> tuple[object, ...]:
        """Preserve SDK local receipt chains when separating lifecycle waves."""
        result: list[object] = []
        for index, operation in enumerate(operations):
            if index not in indices:
                continue
            causes = tuple(
                LocalCause(indices[c.index]) if isinstance(c, LocalCause) else c
                for c in cast(Any, operation).causes
            )
            result.append(replace(cast(Any, operation), causes=causes))
        return tuple(result)

    def integrate(self, view: StateView) -> tuple[object, ...]:
        def stage() -> tuple[object, ...]:
            if self.staged:
                raise KernelError("ADAPTER_LIFECYCLE", "unpublished native snapshot")
            ctx = EngineContext(view, commands=self.commands)
            result = self.client.request("advance", self.advance_payload(ctx))
            reached = integer(result["reached_sim_ns"], "native frontier")
            if reached != ctx.now.ns or reached <= self.confirmed_ns:
                raise KernelError(
                    "ADAPTER_TIME", "native stop differs from granted boundary"
                )
            # Validate the entire output before even the lifecycle wakeup publishes.
            self.project(ctx, result, bootstrap=False)
            self.staged = tuple(ctx.ops)
            self.confirmed_ns = reached
            self.stage_number += 1
            return (
                ScheduleTimer(
                    f"snapshot/{self.stage_number}/0",
                    Instant(ctx.now.ns, ctx.now.microstep + 1),
                    None,
                ),
            )

        return self.guarded("advance", stage)

    def on_react(
        self, view: StateView, inbox: tuple[Delivery, ...], dirty: tuple[Dirty, ...]
    ) -> tuple[object, ...]:
        # The kernel forbids lifecycle mutations in physical calls and field
        # writes to provisional identities. Creation commits in one reaction;
        # validated snapshot fields/events/removals follow in the next reaction.
        operations = self.staged
        creates = [i for i, op in enumerate(operations) if isinstance(op, Create)]
        if creates:
            first = self.reindex(
                operations, {old: new for new, old in enumerate(creates)}
            )
            rest = [i for i in range(len(operations)) if i not in creates]
            self.staged = self.reindex(
                operations, {old: new for new, old in enumerate(rest)}
            )
            first += (
                ScheduleTimer(
                    f"snapshot/{self.stage_number}/1",
                    Instant(view.instant.ns, view.instant.microstep + 1),
                    None,
                ),
            )
        else:
            first, self.staged = operations, ()
        controls = super().on_react(view, inbox, dirty)
        shifted = self.reindex(
            controls, {i: i + len(first) for i in range(len(controls))}
        )
        return first + shifted

    def check_reset(self, result: dict[str, Any]) -> None:
        if result["step_length_ns"] != self.partition.timing.step_ns:
            raise KernelError(
                "ADAPTER_BOUNDARY", "SUMO step differs from communication step"
            )

    def reset_payload(self) -> dict[str, Any]:
        payload = super().reset_payload()
        step = self.partition.timing.step_ns
        if "step_length_ns" in payload and payload["step_length_ns"] != step:
            raise KernelError(
                "ADAPTER_BOUNDARY", "reset step differs from declared timing"
            )
        payload["step_length_ns"] = step
        return payload

    def ensure(self, ctx: EngineContext, kind: str, name: str) -> EntityRef:
        key = kind, name
        if key not in self.active:
            config = obj(self.kinds[kind])
            generation = self.generations.get(key, -1) + 1
            ref = EntityRef(
                self.build.manifest.run_id,
                self.build.manifest.epoch,
                config["prefix"] + name,
                generation,
                config["type_id"],
            )
            ctx.create(ref)
            if not set(obj(config["fields"]).values()) <= set(
                self.build.owned_fields(ref)
            ):
                raise KernelError(
                    "ADAPTER_MAPPING", "dynamic fields lack authored writer binding"
                )
            self.generations[key] = generation
            self.active[key] = ref
        return self.active[key]

    def project(
        self, ctx: EngineContext, result: dict[str, Any], *, bootstrap: bool
    ) -> None:
        samples = records(result["entities"])
        tls = records(result["traffic_lights"])
        present: set[tuple[str, str]] = set()
        ended: set[tuple[str, str]] = set()
        if not bootstrap:
            # Departures create identities even for objects that also arrive in
            # this batch. Availability is the boundary, occurrence stays native.
            for departure in records(result["departed"]):
                self.ensure(ctx, departure["kind"], departure["id"])
            for event in EVENTS:
                for record in records(result[event]):
                    self.emit(ctx, event, record, record["sim_ns"])
                    if event in ("arrived", "removed"):
                        key = record["kind"], record["id"]
                        if key in ended:
                            raise KernelError(
                                "ADAPTER_LIFECYCLE",
                                "duplicate terminal lifecycle evidence",
                            )
                        ended.add(key)
        for kind, sample in [(s["kind"], s) for s in samples] + [
            ("tls", s) for s in tls
        ]:
            key = kind, sample["id"]
            if key in present or key in ended:
                raise KernelError(
                    "ADAPTER_LIFECYCLE", "duplicate or terminal entity in snapshot"
                )
            present.add(key)
            ref = self.ensure(ctx, kind, sample["id"])
            at = (
                ctx.now.ns
                if kind == "tls"
                else integer(sample["sim_ns"], "sample time")
            )
            if at != ctx.now.ns:
                raise KernelError("ADAPTER_TIME", "stale SUMO sample")
            for native, field in sorted(obj(self.kinds[kind])["fields"].items()):
                self.write(ctx, ref, field, sample[native], at)
        for key in sorted(ended):
            if key not in self.active:
                raise KernelError(
                    "ADAPTER_LIFECYCLE", "terminal event for unknown identity"
                )
            ctx.remove(self.active.pop(key))
        if set(self.active) != present:
            raise KernelError(
                "ADAPTER_LIFECYCLE",
                "snapshot lost identity without arrival/removal evidence",
            )
        if not bootstrap:
            self.command_updates(ctx, result["command_updates"])

    def command_payload(
        self, command: Command[dict[str, Any]], backend_id: str
    ) -> dict[str, Any]:
        return {
            "command_id": backend_id,
            "action": command.delivery.message.schema_id.rsplit(".", 1)[1],
            "params": command.payload,
        }


def build(context: EngineBuild) -> SumoEngine:
    return SumoEngine(context)
