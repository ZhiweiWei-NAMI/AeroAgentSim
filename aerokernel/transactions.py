"""Whole-wave candidate validation shared by execution and offline replay."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from fnmatch import fnmatchcase
from typing import Any

from .codec import decode_record, encode
from .compact import FactRows
from .errors import KernelError
from .ids import EntityRef, ItemRef, LocalCause, message_id, validate_text
from .messages import (
    CancelDecision,
    Dirty,
    Emit,
    Feedback,
    Message,
    Receipt,
    RequestCancel,
)
from .operations import (
    Activate,
    ActivateObligation,
    AssertEdge,
    CancelEdge,
    CancelObligation,
    CancelTimer,
    CloseEdge,
    Create,
    EndObligation,
    FactWrite,
    LifecycleReady,
    Remove,
    RetractFact,
    SampleFrame,
    ScheduleTimer,
    UnsupportedOperation,
)
from .relations import Edge
from .state import ABSENT, Fact, FactVersions, Life, Retraction, Store, Work
from .time import ClockMapping, Cut, Instant
from .values import ResourceBudget, freeze, freeze_normalized, normalize, typed_equal


def coordinates(ref: ItemRef) -> dict[str, int]:
    """Portable item coordinates for action/journal items."""
    return {"record_index": ref.record_index, "item_index": ref.item_index}


def item_at(store: Store, ref: ItemRef) -> dict[str, Any]:
    """Resolve a strictly existing journal item."""
    if ref.record_index <= 0 or ref.record_index > len(store.records):
        raise KernelError("CAUSE_UNKNOWN", "unknown record reference")
    try:
        return store.records.item(ref.record_index - 1, ref.item_index)
    except IndexError as exc:
        raise KernelError("CAUSE_UNKNOWN", "unknown item reference") from exc


def eligibility(
    store: Store, recipient: str, requested: Instant, available: Instant, lag: int = 0
) -> Instant:
    """Route once, then latch; eligibility is separate from reactive dispatch."""
    ns = max(requested.ns, available.ns) + lag
    microstep = (
        max(requested.microstep, available.microstep + 1) if ns == available.ns else 0
    )
    timing = store.partitions[recipient].timing
    if timing.mode in {"fixed_step", "lockstep"} and timing.latch:
        latched = timing.boundary(ns)
        if latched != ns:
            microstep = 0
        ns = latched
    return Instant(ns, microstep)


def dirty_matches(
    store: Store, recipient: str, ref: EntityRef, field: str
) -> list[Any]:
    p = store.partitions[recipient]
    return [
        d
        for d in p.consumes
        if d.field == field
        and fnmatchcase(ref.id, d.id_pattern)
        and (d.type_id is None or store.registry.is_a(ref.type_id, d.type_id))
    ]


class Candidate:
    """Private transaction builder. All effects stay invisible until WAL flush."""

    def __init__(
        self,
        store: Store,
        instant: Instant,
        index: int,
        mappings: Mapping[str, ClockMapping],
        budget: ResourceBudget,
    ) -> None:
        self.before = store
        self.state = store.clone()
        self.instant = instant
        self.index = index
        self.mappings = mappings
        self.budget = budget
        self.items: list[dict[str, Any]] = []
        self.fact_rows = FactRows()
        self.stamps: dict[Any, Any] = {}
        self.mapped_stamps: dict[Any, int] = {}
        self.live_cache: set[EntityRef] = set()
        self.ready_keys = set(store.ready)
        self.scalar_values: dict[tuple[str, type, Any], Any] = {}
        self.intervals: dict[Any, Any] = {}
        self.mutated: set[tuple[EntityRef, str]] = set()
        self.creating: set[str] = set()
        self.removing: set[EntityRef] = set()
        self.relation_mutated: set[tuple[str, str]] = set()
        self.relation_affected: set[str] = set()
        self.relation_causes: dict[str, ItemRef] = {}
        self.relation_changes: list[tuple[Edge, Edge]] = []
        self.cause_contexts: dict[
            str, tuple[dict[str, Any], Cut, frozenset[ItemRef], set[ItemRef]]
        ] = {}
        self.learned_inboxes: dict[str, dict[str, Any]] = {}

    def add(self, item: dict[str, Any]) -> ItemRef:
        ref = ItemRef(self.index, len(self.items))
        self.items.append(item)
        return ref

    def cause_refs(
        self,
        raw: tuple[ItemRef | LocalCause, ...],
        local: list[ItemRef],
        intent: dict[str, Any],
    ) -> tuple[ItemRef, ...]:
        if not raw:
            return ()
        result = []
        partition = intent["partition"]
        context = self.cause_contexts.get(partition)
        if context is None or context[0] is not intent:
            context = (
                intent,
                decode_record(intent["read_cut"]),
                frozenset(decode_record(v) for v in intent["authorized"]),
                set(),
            )
            self.cause_contexts[partition] = context
        _, read, authorized, validated = context
        for cause in raw:
            if isinstance(cause, LocalCause):
                if cause.index < 0 or cause.index >= len(local):
                    raise KernelError(
                        "CAUSE_LOCAL", "local cause must precede operation"
                    )
                result.append(local[cause.index])
            elif isinstance(cause, ItemRef):
                # The prestate, read cut and input authority are fixed throughout
                # this invocation. Reusing its successful check preserves every
                # scope/lag/dispatch rule; other intents have distinct caches.
                if cause in validated:
                    result.append(cause)
                    continue
                if cause in authorized:
                    item_at(self.before, cause)
                elif cause.record_index <= read.index:
                    existing = item_at(self.before, cause)
                    kind = existing.get("kind")
                    if kind in {
                        "enqueue",
                        "intent",
                        "reservation",
                        "ingress_publish",
                        "offline_request",
                        "cancel_reservation",
                        "dirty_dispatch",
                        "timer_fire",
                    }:
                        raise KernelError(
                            "CAUSE_UNDISPATCHED",
                            "input cause is not authorized for this invocation",
                        )
                    partition = intent["partition"]
                    if kind == "dispatch":
                        delivery = decode_record(existing["delivery"])
                        if (
                            delivery.recipient != partition
                            or delivery.message.kind not in {"command", "cancel"}
                        ):
                            raise KernelError(
                                "CAUSE_DISPATCH_SCOPE",
                                "dispatch belongs to another recipient/invocation",
                            )
                    if (
                        kind == "operation"
                        and "message" in existing
                        and (
                            existing["partition"] != partition
                            or decode_record(existing["message"]).kind == "command"
                        )
                    ):
                        raise KernelError(
                            "CAUSE_UNDISPATCHED",
                            "another producer's emission is not a recipient dispatch",
                        )
                    if kind == "operation" and "version" in existing:
                        key = decode_record(existing["proposal"]).key
                        dependencies = dirty_matches(self.before, partition, *key)
                        if (
                            not dependencies
                            and self.before.writers.get(key) != partition
                        ):
                            raise KernelError(
                                "CAUSE_STATE_SCOPE",
                                "state cause is outside declared reads",
                            )
                        lag = min(d.lag_ns for d in dependencies) if dependencies else 0
                        if (
                            self.before.cuts[cause.record_index].instant.ns
                            > self.instant.ns - lag
                        ):
                            raise KernelError(
                                "CAUSE_STATE_LAG", "state cause exceeds declared lag"
                            )
                    if (
                        kind in {"bootstrap_create", "operation"}
                        and "version" not in existing
                        and "message" not in existing
                    ):
                        proposal = decode_record(existing["proposal"])
                        if isinstance(proposal, (Create, Remove, LifecycleReady)):
                            p = self.before.partitions[partition]
                            if existing["partition"] != partition and not (
                                p.lifecycle or proposal.ref.type_id in p.lifecycle_reads
                            ):
                                raise KernelError(
                                    "CAUSE_STATE_SCOPE", "undeclared lifecycle cause"
                                )
                        elif isinstance(proposal, (Receipt, Feedback, CancelDecision)):
                            from .state import StateView

                            StateView(self.before, read, partition=partition).action(
                                proposal.command_id
                            )
                        elif isinstance(proposal, (AssertEdge, CloseEdge, CancelEdge)):
                            edge = decode_record(existing["edge"])
                            p = self.before.partitions[partition]
                            dependencies = [
                                d
                                for d in p.relation_consumes
                                if d.relation_id == edge.relation_id
                                and fnmatchcase(edge.source.id, d.id_pattern)
                                and (
                                    d.source_type is None
                                    or self.before.registry.is_a(
                                        edge.source.type_id, d.source_type
                                    )
                                )
                            ]
                            owner = self.before.manifest.edge_writer(
                                edge.relation_id,
                                edge.source,
                                self.before.registry,
                                self.before.partitions,
                            )
                            if not dependencies and owner != partition:
                                raise KernelError(
                                    "CAUSE_STATE_SCOPE", "undeclared relation cause"
                                )
                            lag = (
                                min(d.lag_ns for d in dependencies)
                                if dependencies
                                else 0
                            )
                            if (
                                self.before.cuts[cause.record_index].instant.ns
                                > self.instant.ns - lag
                            ):
                                raise KernelError(
                                    "CAUSE_STATE_LAG",
                                    "relation cause exceeds declared lag",
                                )
                        elif isinstance(
                            proposal,
                            (ActivateObligation, EndObligation, CancelObligation),
                        ):
                            from .state import StateView

                            obligation = decode_record(existing["obligation"])
                            rows = StateView(
                                self.before,
                                read,
                                partition=partition,
                                instant=self.instant,
                            ).obligation_history(obligation.obligation_id)
                            if not any(row.version == cause for row in rows):
                                raise KernelError(
                                    "CAUSE_STATE_LAG",
                                    "obligation cause exceeds declared read scope",
                                )
                        elif isinstance(proposal, SampleFrame):
                            from .state import StateView

                            StateView(
                                self.before,
                                read,
                                partition=partition,
                                instant=self.instant,
                            ).sample_frames(proposal.context_id)
                        elif existing["partition"] != partition:
                            raise KernelError(
                                "CAUSE_STATE_SCOPE",
                                "another partition's private operation",
                            )
                    elif kind == "receipt":
                        from .state import StateView

                        action = StateView(
                            self.before, read, partition=partition
                        ).action(existing["command_id"])
                        if (
                            existing["status"] == "submitted"
                            and not (
                                action.source_kind == "partition"
                                and action.source == partition
                            )
                            and existing["command_id"]
                            not in self.before.actions.dispatches
                        ):
                            raise KernelError(
                                "CAUSE_UNDISPATCHED",
                                "submitted status is not target dispatch",
                            )
                    elif kind not in {"operation", "bootstrap_create", "dispatch"}:
                        raise KernelError(
                            "CAUSE_UNDISPATCHED",
                            "control/publication is not a visible state cause",
                        )
                else:
                    raise KernelError(
                        "CAUSE_FUTURE", "cause exceeds read cut/authorized inbox"
                    )
                validated.add(cause)
                result.append(cause)
            else:
                raise KernelError("CAUSE_TYPE", "typed cause required")
        return tuple(result)

    def reference(self, ref: EntityRef, bootstrap: bool) -> None:
        source = self.state if bootstrap else self.before
        source.known(ref, source.cut if not bootstrap else self.before.cut)

    def live(self, ref: EntityRef) -> None:
        if ref in self.live_cache:
            return
        life = self.state.lives.get(ref)
        if life is None or life.removed is not None:
            raise KernelError("ENTITY_STALE", "not a live created generation")
        if (ref.run_id, ref.epoch) != (
            self.state.manifest.run_id,
            self.state.manifest.epoch,
        ):
            raise KernelError("NAMESPACE", "foreign entity reference")
        self.live_cache.add(ref)

    def create(self, producer: str, op: Create, out: ItemRef, bootstrap: bool) -> None:
        ref = op.ref
        if ref.id in self.creating or ref.id in self.state.live_ids:
            raise KernelError("CREATE_DUPLICATE", "duplicate live/create ID")
        if (ref.run_id, ref.epoch) != (
            self.state.manifest.run_id,
            self.state.manifest.epoch,
        ):
            raise KernelError("NAMESPACE", "foreign create")
        if ref.generation != self.state.generations.get(ref.id, -1) + 1:
            raise KernelError("GENERATION", "generation must advance exactly by one")
        self.state.registry.is_a(ref.type_id, ref.type_id)
        descriptor = next(t for t in self.state.registry.types if t.id == ref.type_id)
        if descriptor.abstract:
            raise KernelError("TYPE_ABSTRACT", "abstract type cannot instantiate")
        controller = self.state.manifest.controller(
            ref, self.state.registry, self.state.partitions
        )
        if producer != controller:
            raise KernelError("LIFECYCLE_OWNER", "create requires bound controller")
        if bootstrap and ref not in self.state.manifest.entities:
            raise KernelError("BOOTSTRAP_REF", "bootstrap ref was not predeclared")
        resolved = self.state.manifest.resolve(
            ref, self.state.registry, self.state.partitions
        )
        self.creating.add(ref.id)
        self.state.live_ids[ref.id] = ref
        self.state.generations[ref.id] = ref.generation
        self.state.lives[ref] = Life(ref, Cut(self.index, self.instant))
        self.state.controllers[ref] = controller
        for field, writer in resolved.items():
            self.state.writers[(ref, field)] = writer
        self.lifecycle_dirty(
            ref, out, tuple(sorted(set(resolved.values()) | {controller}))
        )

    def lifecycle_dirty(
        self, ref: EntityRef, cause: ItemRef, owners: tuple[str, ...]
    ) -> None:
        for recipient in sorted(self.state.partitions):
            p = self.state.partitions[recipient]
            if recipient not in owners and ref.type_id not in p.lifecycle_reads:
                continue
            at = eligibility(self.state, recipient, self.instant, self.instant)
            dirty = Dirty(
                None,
                "lifecycle",
                at,
                cause,
                True,
                freeze({"$ref": ref.to_data()}, self.budget),
                self.budget,
            )
            self.state.work.append(Work(recipient, at, cause, dirty=dirty))
            self.add(
                {
                    "kind": "dirty",
                    "recipient": recipient,
                    "ref": encode(ref),
                    "notification": encode(dirty),
                    "eligible": encode(at),
                }
            )

    def enqueue(self, message: Message, target: str, origin: ItemRef) -> None:
        if target in self.state.partitions:
            recipients: tuple[str, ...] = (target,)
        elif message.kind == "event":
            recipients = tuple(
                p
                for p in sorted(self.state.partitions)
                if target in self.state.partitions[p].subscribes
            )
        else:
            raise KernelError("ROUTE", "command target is not a partition")
        descriptor = self.state.registry.message(message.schema_id)
        if descriptor.kind != message.kind:
            raise KernelError("MESSAGE_KIND", "schema kind mismatch")
        if message.kind == "command":
            p = self.state.partitions[target]
            if message.schema_id not in p.commands:
                raise KernelError(
                    "COMMAND_UNSUPPORTED", "target does not accept command schema"
                )
        self.state.messages[message.id] = message
        for recipient in recipients:
            at = eligibility(
                self.state,
                recipient,
                message.at,
                message.available,
                self.state.partitions[recipient].message_lag_ns,
            )
            enqueue_ref = self.add(
                {
                    "kind": "enqueue",
                    "message": message.id,
                    "recipient": recipient,
                    "eligible": encode(at),
                    "origin": encode(origin),
                }
            )
            self.state.work.append(Work(recipient, at, enqueue_ref, message.id))
        if not recipients:
            self.add({"kind": "fanout_empty", "message": message.id, "topic": target})
        if message.kind == "command":
            self.state.actions.pending(
                message.id, target, message.source_kind, message.source, origin
            )
            receipt_ref = ItemRef(self.index, len(self.items))
            submitted = self.state.actions.submit(message, receipt_ref)
            submitted["causes"] = encode((enqueue_ref,))
            self.add(submitted)
            self.route_receipt(message.id, receipt_ref, submitted)

    def route_receipt(
        self, command_id: str, cause: ItemRef, payload: dict[str, Any]
    ) -> None:
        action = self.state.actions.action(command_id)
        if action.source_kind != "partition":
            return
        recipient = action.source
        at = eligibility(
            self.state,
            recipient,
            self.instant,
            self.instant,
            self.state.partitions[recipient].message_lag_ns,
        )
        dirty = Dirty(
            None, "receipt", at, cause, True, freeze(payload, self.budget), self.budget
        )
        self.state.work.append(Work(recipient, at, cause, dirty=dirty))
        self.add(
            {
                "kind": "receipt_route",
                "recipient": recipient,
                "command_id": command_id,
                "notification": encode(dirty),
                "payload": payload,
            }
        )

    def stamp(self, stamp: Any) -> int:
        if stamp in self.mapped_stamps:
            return self.mapped_stamps[stamp]
        mapping = self.mappings.get(stamp.mapping_id)
        if mapping is None:
            raise KernelError("CLOCK_MAPPING", "unknown pinned mapping")
        ns = mapping.map(stamp)
        if ns > self.instant.ns:
            raise KernelError(
                "ACQUISITION_FUTURE", "acquisition cannot follow publication"
            )
        self.mapped_stamps[stamp] = ns
        return ns

    def operation(
        self,
        partition: str,
        op: object,
        intent: dict[str, Any],
        local: list[ItemRef],
        bootstrap: bool,
    ) -> None:
        raw = getattr(op, "causes", ())
        causes = self.cause_refs(raw, local, intent)
        out = self.add(
            {}
            if isinstance(op, (FactWrite, RetractFact))
            else {
                "kind": "operation",
                "partition": partition,
                "proposal": encode(op),
                "causes": encode(causes) if causes else [],
            }
        )
        phase = intent["phase"]
        if isinstance(op, UnsupportedOperation):
            raise NotImplementedError(
                f"M2_{op.feature.upper()}: unsupported bound operation"
            )
        if isinstance(op, Create):
            if phase == "advance":
                raise KernelError("PHYSICAL_LIFECYCLE", "physical calls cannot create")
            # Creates were installed in a preliminary pass for bootstrap only.
            if not bootstrap:
                self.create(partition, op, out, False)
        elif isinstance(op, Remove):
            if phase == "advance":
                raise KernelError("PHYSICAL_LIFECYCLE", "physical calls cannot remove")
            self.live(op.ref)
            if op.ref.id in self.creating:
                raise KernelError(
                    "CREATE_REMOVE", "create/remove ID in one transaction"
                )
            if self.state.controllers[op.ref] != partition:
                raise KernelError(
                    "LIFECYCLE_OWNER", "remove requires lifecycle controller"
                )
            cleanup = self.cause_refs(op.cleanup_refs, local, intent)
            participants = self.state.manifest.participants(op.ref, self.state.registry)
            for participant in participants:
                if self.state.ready.get((op.ref, participant)) not in cleanup:
                    raise KernelError(
                        "CLEANUP_REQUIRED",
                        "actual visible cleanup acknowledgment required",
                    )
            self.state.live_ids.pop(op.ref.id)
            self.live_cache.discard(op.ref)
            self.state.lives[op.ref] = replace(
                self.state.lives[op.ref], removed=Cut(self.index, self.instant)
            )
            self.lifecycle_dirty(
                op.ref,
                out,
                tuple(
                    sorted(
                        {w for (r, _), w in self.state.writers.items() if r == op.ref}
                    )
                ),
            )
        elif isinstance(op, LifecycleReady):
            self.live(op.ref)
            if op.ref not in self.before.lives and not bootstrap:
                raise KernelError(
                    "PROVISIONAL_CREATE", "cleanup follows committed creation"
                )
            participants = self.state.manifest.participants(op.ref, self.state.registry)
            if partition not in participants or (op.ref, partition) in self.ready_keys:
                raise KernelError(
                    "CLEANUP_OWNER", "unexpected or duplicate cleanup acknowledgment"
                )
            self.items[out.item_index]["native_reached_ns"] = intent["expected_native"]
            self.state.ready[(op.ref, partition)] = out
            self.ready_keys.add((op.ref, partition))
        elif isinstance(op, (FactWrite, RetractFact)):
            self.live(op.key[0])
            if op.key[0] in self.removing or (op.key[0], partition) in self.ready_keys:
                raise KernelError(
                    "WRITE_FROZEN", "removed or cleanup-frozen generation"
                )
            if self.state.writers.get(op.key) != partition:
                raise KernelError(
                    "FIELD_OWNER", "not the resolved per-instance-field writer"
                )
            if op.key in self.mutated:
                raise KernelError("FACT_DUPLICATE", "duplicate key mutation in wave")
            if not bootstrap and op.key[0].id in self.creating:
                raise KernelError(
                    "PROVISIONAL_CREATE", "dynamic writes follow committed creation"
                )
            self.mutated.add(op.key)
            if isinstance(op, FactWrite):
                descriptor = self.state.registry.field(op.key[1])

                def validate_ref(ref: EntityRef) -> None:
                    if bootstrap and ref in self.state.lives:
                        return
                    self.before.known(ref, decode_record(intent["read_cut"]))

                scalar = type(op.value) in (type(None), bool, int, float, str)
                cache_key = (op.key[1], type(op.value), op.value) if scalar else None
                if cache_key is not None and cache_key in self.scalar_values:
                    frozen_value = self.scalar_values[cache_key]
                else:
                    portable = normalize(op.value, self.budget)
                    self.state.registry._validate(
                        descriptor.schema, portable, validate_ref
                    )
                    frozen_value = freeze_normalized(portable)
                    if cache_key is not None:
                        self.scalar_values[cache_key] = frozen_value
                fact: Fact | Retraction = Fact(
                    op.key,
                    frozen_value,
                    self.stamps.setdefault(op.acquired, op.acquired),
                    self.stamp(op.acquired),
                    self.instant,
                    self.intervals.setdefault(op.valid, op.valid),
                    partition,
                    out,
                )
            else:
                validate_text(op.reason)
                fact = Retraction(
                    op.key, op.valid, op.reason, self.instant, partition, out
                )
            versions = self.state.facts.get(op.key)
            if versions is None:
                versions = FactVersions(op.key)
            self.state.facts[op.key] = versions.with_version(fact, self.index)
            self.items[out.item_index] = self.fact_rows.row(
                partition, op, fact, encode(causes) if causes else []
            )
            self.notify_field(op.key, out)
            for boundary in (op.valid.start, op.valid.end):
                if boundary is not None and boundary > self.instant:
                    key: tuple[str, ...] = (
                        "validity",
                        "kernel",
                        f"valid:{self.index}:{out.item_index}:{boundary.ns}:{boundary.microstep}",
                    )
                    self.state.timers[key] = {
                        "kind": "validity",
                        "due": encode(boundary),
                        "state": "pending",
                        "key": encode(op.key),
                        "cause": encode(out),
                    }
                    self.state.timer_queue.add(key, boundary)
                    self.add(
                        {
                            "kind": "validity_timer",
                            "id": list(key),
                            "data": self.state.timers[key],
                        }
                    )
        elif isinstance(
            op,
            (
                AssertEdge,
                CloseEdge,
                CancelEdge,
                ActivateObligation,
                EndObligation,
                CancelObligation,
            ),
        ):
            from .relations import apply_relation

            apply_relation(self, partition, op, intent, out, bootstrap)
        elif isinstance(op, SampleFrame):
            from .sampling import publish_frame

            publish_frame(self, partition, op, out, phase)
        elif isinstance(op, Emit):
            p = self.state.partitions[partition]
            if op.target_or_topic not in p.message_targets:
                raise KernelError(
                    "MESSAGE_TARGET", "message escapes pinned target/topic domain"
                )
            if op.schema_id not in p.emits:
                raise KernelError("EMIT_UNDECLARED", "schema not declared by source")
            self.state.registry.validate(
                self.state.registry.message(op.schema_id).schema,
                op.payload,
                budget=self.budget,
            )
            if op.kind == "event" and op.at > self.instant:
                raise KernelError(
                    "EVENT_FUTURE", "event occurrence follows publication"
                )
            if op.source_stamp is not None:
                self.items[out.item_index]["mapped_source_ns"] = self.stamp(
                    op.source_stamp
                )
            source = ("partition", partition)
            sequence = self.state.sequences.get(source, 0)
            self.state.sequences[source] = sequence + 1
            mid = message_id(
                self.state.manifest.run_id, self.state.manifest.epoch, *source, sequence
            )
            msg = Message(
                mid,
                op.kind,
                op.schema_id,
                *source,
                sequence,
                op.at,
                self.instant,
                freeze(op.payload, self.budget),
                causes,
                op.source_stamp,
                out,
                self.budget,
            )
            self.items[out.item_index]["message"] = encode(msg)
            self.enqueue(msg, op.target_or_topic, out)
        elif isinstance(op, (Receipt, Feedback, CancelDecision)):
            if self.learned_inboxes.get(partition) is not intent:
                inbox = tuple(decode_record(d) for d in intent["inbox"])
                self.state.actions._learn(inbox)
                self.learned_inboxes[partition] = intent
            # Receipt ownership/dispatch remains authorized after its original call.
            if isinstance(op, Receipt):
                item = self.state.actions.receipt(partition, op, out, (), causes)
            elif isinstance(op, Feedback):
                item = self.state.actions.feedback(partition, op, out, (), causes)
            else:
                item = self.state.actions.cancel_decision(
                    partition, op, out, (), causes
                )
            item["causes"] = encode(causes)
            self.items[out.item_index]["receipt"] = item
            self.route_receipt(op.command_id, out, item)
            if isinstance(op, CancelDecision):
                cancel = self.state.messages[op.cancel_message_id]
                original = self.state.actions.action(op.command_id)
                if cancel.source_kind == "partition" and (
                    cancel.source_kind,
                    cancel.source,
                ) != (original.source_kind, original.source):
                    recipient = cancel.source
                    at = eligibility(
                        self.state,
                        recipient,
                        self.instant,
                        self.instant,
                        self.state.partitions[recipient].message_lag_ns,
                    )
                    dirty = Dirty(
                        None,
                        "cancel_decision",
                        at,
                        out,
                        True,
                        freeze(item, self.budget),
                        self.budget,
                    )
                    self.state.work.append(Work(recipient, at, out, dirty=dirty))
                    self.add(
                        {
                            "kind": "cancel_decision_route",
                            "recipient": recipient,
                            "notification": encode(dirty),
                        }
                    )
        elif isinstance(op, ScheduleTimer):
            validate_text(op.timer_id)
            key = (partition, op.timer_id)
            if not op.timer_id or key in self.state.timers or op.due <= self.instant:
                raise KernelError(
                    "TIMER_SCHEDULE", "timer requires unique ID and strictly later due"
                )
            normalize(op.payload, self.budget)
            self.state.timers[key] = {
                "kind": "partition",
                "due": encode(op.due),
                "state": "pending",
                "payload": normalize(op.payload, self.budget),
                "cause": encode(out),
            }
            self.state.timer_queue.add(key, op.due)
        elif isinstance(op, CancelTimer):
            validate_text(op.timer_id)
            timer = self.state.timers.get((partition, op.timer_id))
            if timer is None or timer["state"] != "pending":
                raise KernelError("TIMER_CANCEL", "timer not pending")
            self.state.timers[(partition, op.timer_id)] = {**timer, "state": "canceled"}
            self.state.timer_queue.cancel((partition, op.timer_id))
        elif isinstance(op, Activate):
            if op.partition != partition:
                raise KernelError(
                    "ACTIVATE_SCOPE", "explicit activation is partition-owned"
                )
            at = eligibility(self.state, partition, self.instant, self.instant)
            dirty = Dirty(None, "activation", at, out, True)
            self.state.work.append(Work(partition, at, out, dirty=dirty))
        elif isinstance(op, RequestCancel):
            self.cancel_request(partition, "partition", op.command_id, out)
        else:
            raise KernelError("OPERATION", "unknown proposal type")
        local.append(out)

    def notify_field(
        self,
        key: tuple[EntityRef, str],
        cause: ItemRef,
        validity_boundary: bool = False,
    ) -> None:
        if key[1] not in self.state.dependency_fields:
            return
        # Candidate reads use the new publication cut, without issuing it live.
        temp_cut = Cut(self.index, self.instant)
        if not self.state.cuts or self.state.cuts[-1] != temp_cut:
            self.state.cuts.append(temp_cut)
        old: Fact | Any = ABSENT
        if key[0] in self.before.lives:
            old = (
                self.before.field_before(key, self.instant, self.before.cut)
                if validity_boundary
                else self.before.field(key, self.instant, self.before.cut)
            )
        new = self.state.field(key, self.instant, temp_cut)
        changed = (old is ABSENT) != (new is ABSENT) or (
            isinstance(old, Fact)
            and isinstance(new, Fact)
            and not typed_equal(old.value, new.value)
        )
        for recipient in sorted(self.state.partitions):
            for dep in dirty_matches(self.state, recipient, *key):
                if dep.value_only and not changed:
                    continue
                at = eligibility(
                    self.state, recipient, self.instant, self.instant, dep.lag_ns
                )
                dirty = Dirty(
                    key,
                    "validity" if validity_boundary else "field",
                    at,
                    cause,
                    changed,
                )
                self.state.work.append(Work(recipient, at, cause, dirty=dirty))
                self.add(
                    {
                        "kind": "dirty",
                        "recipient": recipient,
                        "notification": encode(dirty),
                    }
                )

    def cancel_request(
        self, source: str, source_kind: str, command_id: str, origin: ItemRef
    ) -> str | None:
        action = self.state.actions.action(command_id)
        allowed = (action.source_kind, action.source) == (
            source_kind,
            source,
        ) or source in self.state.manifest.cancel_sources
        message = self.state.messages.get(command_id)
        descriptor = (
            self.state.registry.message(message.schema_id)
            if message is not None
            else None
        )
        reason = None
        if not allowed:
            reason = "unauthorized"
        elif action.status not in {"accepted", "executing"}:
            reason = "status"
        elif descriptor is None or not descriptor.cancel_support:
            reason = "unsupported"
        if reason:
            self.add(
                {
                    "kind": "cancel_rejection",
                    "command_id": command_id,
                    "reason": reason,
                    "origin": encode(origin),
                }
            )
            return None
        seq_key = (source_kind, source)
        seq = self.state.sequences.get(seq_key, 0)
        self.state.sequences[seq_key] = seq + 1
        mid = message_id(
            self.state.manifest.run_id,
            self.state.manifest.epoch,
            source_kind,
            source,
            seq,
        )
        # Reserved cancel requests are nonrecursive command envelopes.
        assert message is not None
        msg = Message(
            mid,
            "cancel",
            message.schema_id,
            source_kind,
            source,
            seq,
            self.instant,
            self.instant,
            freeze({"command_id": command_id}),
            (origin,),
            None,
            origin,
            self.budget,
        )
        self.state.messages[mid] = msg
        at = eligibility(
            self.state,
            action.target,
            self.instant,
            self.instant,
            self.state.partitions[action.target].message_lag_ns,
        )
        enqueue = self.add(
            {
                "kind": "enqueue",
                "message": mid,
                "recipient": action.target,
                "eligible": encode(at),
                "origin": encode(origin),
            }
        )
        self.state.work.append(Work(action.target, at, enqueue, mid))
        return mid
