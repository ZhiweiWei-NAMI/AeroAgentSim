"""Capture real committed samples and release concrete records after modeled delay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aerokernel import ABSENT, Activate, Dependency, EntityRef, Interval, Partition
from aerokernel.operations import ActivateObligation, AssertEdge
from aerokernel.sdk import ContextEngine, EngineContext
from aerokernel.state import Fact
from aerokernel.time import Stamp
from aerokernel.values import thaw

from aeroagentsim.platform.plugins import EngineBuild

from .common import bootstrap_owned, policies


@dataclass
class Capture:
    ref: EntityRef
    subject: EntityRef
    sample: Any
    acquired: Stamp
    acquired_ns: int
    release_ns: int


class Records(ContextEngine):
    def __init__(self, build: EngineBuild) -> None:
        self.build = build
        self.config = config = build.config
        self.delay = config["release_delay_ns"]
        if type(self.delay) is not int or self.delay <= 0:
            raise ValueError("records.release_delay_ns: positive integer required")
        type_descriptor = next(
            t for t in build.registry.types if t.id == config["type_id"]
        )
        if type_descriptor.abstract:
            raise ValueError("records.type_id: select a concrete record subtype")
        fields = tuple(
            config[key]
            for key in (
                "acquired_field",
                "available_field",
                "subject_field",
                "sample_field",
            )
        )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                consumes=(Dependency(config["position_field"]),),
                subscribes=(config["topic"],),
                lifecycle=True,
                relation_produces=(config["relation_id"],),
                obligation_produces=(config["relation_id"],),
                features=("relations",),
            ),
            policies=policies(fields),
        )
        self.subjects = {ref.id: ref for ref in build.entities}
        self.pending: dict[str, Capture] = {}
        self.ready: list[Capture] = []
        self.sequence = 0

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def on_inputs(self, ctx: EngineContext) -> None:
        # Creation and fields occupy separate committed lifecycle/writer waves.
        for capture in self.ready:
            cfg = self.config
            valid = Interval(ctx.now, None)
            ctx.set(
                capture.ref,
                cfg["acquired_field"],
                {
                    "clockRef": "canonical",
                    "value": capture.acquired_ns / 1e9,
                },
                acquired=capture.acquired,
                valid=valid,
            )
            ctx.set(
                capture.ref,
                cfg["available_field"],
                {"clockRef": "canonical", "value": capture.release_ns / 1e9},
            )
            ctx.set(
                capture.ref,
                cfg["subject_field"],
                {"$ref": capture.subject.to_data()},
                acquired=capture.acquired,
                valid=valid,
            )
            ctx.set(
                capture.ref,
                cfg["sample_field"],
                capture.sample,
                acquired=capture.acquired,
                valid=valid,
            )
            ctx.ops.append(
                AssertEdge(
                    "edge/" + capture.ref.id,
                    cfg["relation_id"],
                    capture.ref,
                    capture.subject,
                    valid,
                    capture.acquired,
                    tuple(ctx.inputs),
                )
            )
            ctx.ops.append(
                ActivateObligation(
                    "subject/" + capture.ref.id,
                    cfg["relation_id"],
                    "targets_per_source",
                    capture.ref,
                    valid,
                    tuple(ctx.inputs),
                )
            )
        self.ready.clear()
        for dirty in ctx.dirty:
            if dirty.kind != "timer":
                continue
            data = thaw(dirty.payload)
            if not isinstance(data, dict) or "record" not in data:
                raise TypeError("records.timer: expected a capture identity")
            capture = self.pending.pop(str(data["record"]))
            if ctx.now.ns != capture.release_ns:
                raise ValueError("records.release: unexpected timer availability")
            ctx.create(capture.ref)
            self.ready.append(capture)
            ctx.ops.append(Activate(self.partition.id))
        for delivery in ctx.inbox:
            if delivery.message.schema_id != self.config["trigger_schema"]:
                raise ValueError(
                    "records.trigger: unexpected schema on configured topic"
                )
            data = thaw(delivery.message.payload)
            if not isinstance(data, dict):
                raise TypeError("records.trigger: expected a typed record")
            subject = self.subjects[str(data[self.config["subject_key"]])]
            field = self.config["position_field"]
            sample = ctx.get(subject, field)
            fact = ctx.view.field((subject, field), ctx.now)
            if sample is ABSENT or not isinstance(fact, Fact):
                raise TypeError(
                    f"records.capture: no real valid source for {subject.id}.{field}"
                )
            record_id = f"obs/{subject.id}/{self.sequence:06d}"
            self.sequence += 1
            ref = EntityRef(
                self.build.manifest.run_id,
                self.build.manifest.epoch,
                record_id,
                0,
                self.config["type_id"],
            )
            capture = Capture(
                ref,
                subject,
                thaw(fact.value),
                fact.acquired,
                fact.mapped_ns,
                ctx.now.ns + self.delay,
            )
            self.pending[record_id] = capture
            ctx.wake_at(capture.release_ns, {"record": record_id})


def build(context: EngineBuild) -> Records:
    return Records(context)
