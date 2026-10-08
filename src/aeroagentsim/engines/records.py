"""Capture real committed samples and release concrete records after modeled delay."""

from __future__ import annotations

from dataclasses import dataclass
from string import Formatter
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
        self.config = config = dict(build.config)
        allowed = {
            "trigger_schema",
            "topic",
            "subject_key",
            "source_field",
            "position_field",
            "type_id",
            "acquired_field",
            "available_field",
            "subject_field",
            "sample_field",
            "slots",
            "release_delay_ns",
            "relation_id",
            "id_template",
            "time_encoding",
            "clock_ref",
            "subject_lifetime",
        }
        if set(config) - allowed or ("source_field" in config) == (
            "position_field" in config
        ):
            raise ValueError(
                "records.config: unknown keys or select exactly one source_field"
            )
        source = config.get("source_field", config.get("position_field"))
        if not isinstance(source, str):
            raise TypeError("records.source_field: string selector required")
        self.source: str = source
        if "slots" in config:
            slots = config["slots"]
            if (
                not isinstance(slots, dict)
                or "sample" not in slots
                or set(slots) - {"sample", "subject", "acquired", "available"}
            ):
                raise ValueError(
                    "records.slots: sample and optional subject/acquired/available fields required"
                )
            for role in ("sample", "subject", "acquired", "available"):
                config[role + "_field"] = slots.get(role)
        self.encoding = config.get("time_encoding", "canonical_seconds")
        if self.encoding not in {"canonical_seconds", "nanoseconds", "stamp"}:
            raise ValueError(
                "records.time_encoding: canonical_seconds, nanoseconds or stamp required"
            )
        if config.get(
            "clock_ref", "canonical" if self.encoding != "stamp" else "source"
        ) != ("canonical" if self.encoding != "stamp" else "source"):
            raise ValueError(
                "records.clock_ref: must match actual encoding clock (canonical or source)"
            )
        self.lifetime = config.get("subject_lifetime", "require_live")
        self.relation = config.get("relation_id")
        if (
            self.lifetime not in {"require_live", "historical"}
            or self.lifetime == "historical"
            and self.relation is not None
        ):
            raise ValueError(
                "records.subject_lifetime: historical records cannot assert a live relation"
            )
        self.id_template = config.get("id_template", "obs/{subject}/{sequence:06d}")
        if type(self.id_template) is not str or not self.id_template:
            raise ValueError(
                "records.id_template: nonempty authored ID template required"
            )
        for _, name, fmt, conversion in Formatter().parse(self.id_template):
            if name is not None and (
                name not in {"subject", "sequence", "generation", "partition"}
                or conversion is not None
                or fmt
                and (
                    name not in {"sequence", "generation"}
                    or not fmt.endswith("d")
                    or not fmt[:-1].isdigit()
                )
            ):
                raise ValueError(
                    "records.id_template: unsupported identity selector/format"
                )
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
            if config.get(key) is not None
        )
        source_schema = build.registry.field(self.source).schema
        sample_schema = build.registry.field(config["sample_field"]).schema
        if source_schema.get("type") != sample_schema.get("type") or source_schema.get(
            "length"
        ) != sample_schema.get("length"):
            raise ValueError(
                "records.sample_field: source/sample schema shape mismatch"
            )
        for field in fields:
            descriptor = build.registry.field(field)
            if not build.registry.is_a(config["type_id"], descriptor.declaring_type):
                raise ValueError("records.slots: field does not apply to record type")
        trigger = build.registry.message(config["trigger_schema"])
        if trigger.kind != "event" or trigger.schema.get("type") != "record":
            raise ValueError("records.trigger_schema: typed event record required")
        subject_selector = trigger.schema.get("members", {}).get(config["subject_key"])
        if subject_selector is None or subject_selector.get("type") not in {
            "string",
            "ref",
        }:
            raise ValueError(
                "records.subject_key: declared string/ref event member required"
            )
        for role in ("acquired", "available"):
            slot = config.get(role + "_field")
            if slot is not None:
                time_schema = build.registry.field(slot).schema
                if self.encoding == "stamp":
                    expected = {
                        "clockRef": "string",
                        "mappingRef": "string",
                        "numerator": "integer",
                        "denominator": "integer",
                    }
                    members = time_schema.get("members", {})
                    if (
                        time_schema.get("type") != "record"
                        or any(
                            members.get(name, {}).get("type") != kind
                            for name, kind in expected.items()
                        )
                        or set(time_schema.get("required", ())) - set(expected)
                    ):
                        raise ValueError(
                            "records.slots: incompatible exact source-stamp schema"
                        )
                else:
                    build.registry.validate(
                        time_schema,
                        self._time(Stamp("canonical", 0, 1, "canonical"), 0),
                    )
        if self.relation is not None and not build.registry.is_a(
            config["type_id"], build.registry.relation(self.relation).source_type
        ):
            raise ValueError(
                "records.relation_id: record type is not a relation source"
            )
        super().__init__(
            Partition(
                build.id,
                build.id,
                produces=fields,
                consumes=(Dependency(self.source),),
                subscribes=(config["topic"],),
                lifecycle=True,
                relation_produces=() if self.relation is None else (self.relation,),
                obligation_produces=() if self.relation is None else (self.relation,),
                features=() if self.relation is None else ("relations",),
            ),
            policies=policies(fields),
        )
        self.pending: dict[str, Capture] = {}
        self.ready: list[Capture] = []
        self.sequence = 0

    def _time(self, stamp: Stamp, ns: int) -> Any:
        if self.encoding == "nanoseconds":
            return ns
        if self.encoding == "stamp":
            return {
                "clockRef": stamp.clock_id,
                "mappingRef": stamp.mapping_id,
                "numerator": stamp.numerator,
                "denominator": stamp.denominator,
            }
        return {"clockRef": "canonical", "value": ns / 1e9}

    def _subject(self, ctx: EngineContext, identity: Any) -> EntityRef:
        if isinstance(identity, dict):
            candidates = [EntityRef.from_data(identity["$ref"])]
        elif isinstance(identity, str):
            candidates = [
                ref
                for ref, life in ctx.view._store.lives.items()
                if ref.id == identity and life.created.index <= ctx.view.cut.index
            ]
        else:
            raise TypeError("records.subject: explicit ID or typed ref required")
        active = [
            ref
            for ref in candidates
            if ctx.view._store.alive(ref, ctx.view.cut, ctx.now)
        ]
        if len(active) != 1:
            raise ValueError("records.subject: no unique committed live generation")
        return active[0]

    def bootstrap(self, ctx: EngineContext) -> None:
        bootstrap_owned(ctx, self.build)

    def on_inputs(self, ctx: EngineContext) -> None:
        # Creation and fields occupy separate committed lifecycle/writer waves.
        for capture in self.ready:
            cfg = self.config
            valid = Interval(ctx.now, None)
            if cfg.get("acquired_field") is not None:
                ctx.set(
                    capture.ref,
                    cfg["acquired_field"],
                    self._time(capture.acquired, capture.acquired_ns),
                    acquired=capture.acquired,
                    valid=valid,
                )
            if cfg.get("available_field") is not None:
                ctx.set(
                    capture.ref,
                    cfg["available_field"],
                    self._time(
                        Stamp("canonical", capture.release_ns, 1, "canonical"),
                        capture.release_ns,
                    ),
                )
            if cfg.get("subject_field") is not None:
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
            if self.relation is not None:
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
            if self.lifetime == "require_live" and not ctx.view._store.alive(
                capture.subject, ctx.view.cut, ctx.now
            ):
                raise ValueError(
                    "records.release: subject removed during declared live-link delay"
                )
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
            subject = self._subject(ctx, data[self.config["subject_key"]])
            field = self.source
            sample = ctx.get(subject, field)
            fact = ctx.view.field((subject, field), ctx.now)
            if sample is ABSENT or not isinstance(fact, Fact):
                raise TypeError(
                    f"records.capture: no real valid source for {subject.id}.{field}"
                )
            self.build.registry.validate(
                self.build.registry.field(self.config["sample_field"]).schema,
                thaw(fact.value),
            )
            record_id = self.id_template.format(
                subject=subject.id,
                generation=subject.generation,
                sequence=self.sequence,
                partition=self.build.id,
            )
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
