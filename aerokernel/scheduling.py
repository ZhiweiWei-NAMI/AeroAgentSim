"""Private invocation grants, dispatch records and deterministic wave construction."""

from __future__ import annotations

from typing import Any

from .codec import encode
from .engine import Batch
from .errors import KernelError, MicrostepLimitExceeded
from .ids import ItemRef
from .ingress import safe_partitions
from .messages import Delivery
from .operations import Create, Remove
from .state import StateView, Store, Work
from .time import Cut, Instant
from .transactions import Candidate


def ready_partitions(store: Store, instant: Instant) -> tuple[str, ...]:
    """The unique lifecycle-prioritized, cohort-expanded reactive ready set."""
    ready = store.work.ready(instant) - set(store.sample_partitions)
    if store.ingress_dependencies:
        ready &= set(safe_partitions(store, instant.ns))
        ready = {p for p in ready if store.frontiers[p][0].ns == instant.ns}
    controllers = {p for p in ready if store.partitions[p].lifecycle}
    for cohort in store.manifest.cohorts:
        if ready & set(cohort) and any(store.partitions[p].lifecycle for p in cohort):
            controllers |= set(cohort)
    chosen = controllers if controllers else ready
    for cohort in store.manifest.cohorts:
        if chosen & set(cohort):
            chosen |= set(cohort)
    return tuple(sorted(chosen, key=lambda p: (store.partitions[p].engine_id, p)))


def wave_intents(
    store: Store, selected: tuple[str, ...], instant: Instant, phase: str
) -> tuple[Store, dict[str, Any], dict[str, StateView]]:
    """Authorize all calls before execution, atomically dispatching ready inboxes."""
    if instant.microstep > store.max_microsteps:
        raise MicrostepLimitExceeded(
            "grant exceeds pinned microstep bound", instant=instant
        )
    if phase not in {"reset", "advance", "react", "sample"}:
        raise KernelError("INVOCATION_PHASE", "unknown M1 call phase")
    canonical = tuple(
        sorted(store.partitions, key=lambda p: (store.partitions[p].engine_id, p))
    )
    if len(set(selected)) != len(selected) or any(
        p not in store.partitions for p in selected
    ):
        raise KernelError("INVOCATION_PARTITIONS", "invalid invocation partition set")
    if selected != tuple(
        sorted(selected, key=lambda p: (store.partitions[p].engine_id, p))
    ):
        raise KernelError("INVOCATION_ORDER", "noncanonical partition call order")
    if store.pending_intents:
        raise KernelError("INVOCATION_OVERLAP", "prior wave has unresolved calls")
    if phase == "reset":
        if instant != Instant(0) or store.records or selected != canonical:
            raise KernelError("RESET_ONCE", "reset must be one complete initial wave")
    elif phase == "advance":
        expected = (
            canonical
            if not store.ingress_dependencies
            else tuple(
                p
                for p in canonical
                if p in safe_partitions(store, instant.ns)
                and store.frontiers[p][0].ns < instant.ns
            )
        )
        if (
            selected != expected
            or not selected
            or (not store.ingress_dependencies and instant.microstep != 0)
            or instant < store.cut.instant
            or store.sealed_ns is None
            or instant.ns <= store.sealed_ns
            or store.run_target is None
            or instant.ns > store.run_target
        ):
            raise KernelError(
                "ADVANCE_GRANT", "physical wave requires a strictly later common grant"
            )
        for pid in selected:
            p = store.partitions[pid]
            if (
                p.timing.mode in {"fixed_step", "lockstep"}
                and not p.timing.exact_stop
                and p.timing.step_ns is not None
                and instant.ns > p.timing.boundary(store.frontiers[pid][1] + 1)
            ):
                raise KernelError("ADVANCE_GRID", "grant skips a native boundary")
    elif phase == "sample":
        from .sampling import ordinary_earliest, sample_ready

        ordinary = ordinary_earliest(store)
        if (
            instant.ns != store.cut.instant.ns
            or instant <= store.cut.instant
            or selected != sample_ready(store, instant.ns)
            or (ordinary is not None and ordinary.ns <= instant.ns)
        ):
            raise KernelError(
                "SAMPLE_READY",
                "sample grant requires the settled whole cone and next level",
            )
    else:
        if (
            instant.ns != store.cut.instant.ns
            or instant <= store.cut.instant
            or not selected
            or selected != ready_partitions(store, instant)
        ):
            raise KernelError(
                "REACTIVE_READY", "reactive grant differs from canonical ready cohorts"
            )
    base = store.cut
    index = base.index + 1
    state = store.clone()
    items: list[dict[str, Any]] = []
    views = {}
    for partition in selected:
        p = store.partitions[partition]
        read = base
        native_cut = store.native_cuts[partition]
        logical, native = store.frontiers[partition]
        if phase == "advance":
            read = native_cut
        ready = (
            []
            if phase not in {"react", "sample"}
            else state.work.take(partition, instant)
        )
        if ready and instant <= logical:
            raise KernelError(
                "DISPATCH_FRONTIER", "delivery must follow recipient frontier"
            )
        inbox = []
        dirty = []
        authorized = []
        for work in sorted(ready, key=lambda w: work_order(store, w, instant)):
            ref = ItemRef(index, len(items))
            if work.message_id is not None:
                delivery = Delivery(
                    store.messages[work.message_id], partition, instant, work.cause, ref
                )
                items.append({"kind": "dispatch", "delivery": encode(delivery)})
                inbox.append(delivery)
                state.actions.record_dispatch(delivery)
            else:
                if work.dirty is None:
                    raise KernelError("WORK", "queued work has no notification")
                notification = type(work.dirty)(
                    work.dirty.key,
                    work.dirty.kind,
                    instant,
                    ref,
                    work.dirty.value_changed,
                    work.dirty.payload,
                    work.dirty.resource_budget,
                )
                items.append(
                    {
                        "kind": "dirty_dispatch",
                        "notification": encode(notification),
                        "origin": encode(work.cause),
                        "recipient": partition,
                    }
                )
                dirty.append(notification)
            authorized.append(ref)
        intent_ref = ItemRef(index, len(items))
        expected_native = native
        if phase == "advance":
            if (
                p.timing.mode == "des"
                or p.timing.exact_stop
                or not p.timing.latch
                or p.timing.boundary(instant.ns) == instant.ns
            ):
                expected_native = instant.ns
        intent = {
            "kind": "intent",
            "partition": partition,
            "phase": phase,
            "instant": encode(instant),
            "transaction_base_cut": encode(base),
            "read_cut": encode(read),
            "native_input_cut": encode(native_cut),
            "logical_before": encode(logical),
            "native_before": native,
            "expected_native": expected_native,
            "inbox": encode(inbox),
            "dirty": encode(dirty),
            "authorized": encode(authorized),
            "status": "pending",
        }
        items.append(intent)
        state.intents[intent_ref] = intent.copy()
        state.pending_intents[partition] = intent_ref
        views[partition] = StateView(
            store,
            read,
            base,
            partition,
            instant,
            native_cut,
            expected_native,
            intent_ref,
            phase,
            logical,
            native,
        )
    record = {
        "type": "invocations",
        "index": index,
        "instant": encode(instant),
        "phase": phase,
        "selected": list(selected),
        "items": items,
    }
    state.records.append(record)
    state.cuts.append(Cut(index, instant))
    return state, record, views


def work_order(store: Store, work: Work, instant: Instant) -> tuple[Any, ...]:
    if work.message_id is None:
        return (
            instant.ns,
            instant.microstep,
            "",
            -1,
            "",
            work.cause.record_index,
            work.cause.item_index,
        )
    msg = store.messages[work.message_id]
    return (
        instant.ns,
        instant.microstep,
        msg.source_kind + ":" + msg.source,
        msg.sequence,
        msg.id,
        work.recipient,
    )


def build_wave(
    store: Store,
    instant: Instant,
    phase: str,
    batches: list[tuple[str, Batch]],
    candidate: Candidate,
) -> dict[str, Any]:
    """Validate one merged candidate with private intent and exact frontier checks."""
    intent_for = {
        p: (ref, store.intents[ref]) for p, ref in store.pending_intents.items()
    }
    if set(intent_for) != {p for p, _ in batches} or len(batches) != len(intent_for):
        raise KernelError("INVOCATION_RETURNS", "every intent needs exactly one return")
    ordered = sorted(batches, key=lambda pb: (store.partitions[pb[0]].engine_id, pb[0]))
    candidate.removing = {
        op.ref for _, b in ordered for op in b.operations if isinstance(op, Remove)
    }
    creating_ids = {
        op.ref.id
        for _, batch in ordered
        for op in batch.operations
        if isinstance(op, Create)
    }
    removing_ids = {ref.id for ref in candidate.removing}
    if creating_ids & removing_ids:
        raise KernelError("CREATE_REMOVE", "create/remove ID in one transaction")
    bootstrap = phase == "reset"
    if bootstrap:
        candidate.state.cuts.append(Cut(candidate.index, instant))
        for partition, batch in ordered:
            for op in batch.operations:
                if isinstance(op, Create):
                    ref = candidate.add(
                        {
                            "kind": "bootstrap_create",
                            "partition": partition,
                            "proposal": encode(op),
                        }
                    )
                    candidate.create(partition, op, ref, True)
        if set(candidate.state.lives) != set(store.manifest.entities):
            raise KernelError(
                "BOOTSTRAP_CREATE", "all predeclared refs must be created"
            )
    encoded_batches = []
    for partition, batch in ordered:
        ref, intent = intent_for[partition]
        if intent["phase"] != phase:
            raise KernelError(
                "RETURN_PHASE", "transaction phase differs from invocation"
            )
        if (
            batch.reached != instant
            or encode(batch.transaction_base_cut) != intent["transaction_base_cut"]
            or encode(batch.read_cut) != intent["read_cut"]
            or encode(batch.native_input_cut) != intent["native_input_cut"]
            or batch.native_reached_ns != intent["expected_native"]
        ):
            raise KernelError(
                "INVOCATION_FRONTIER", "batch does not echo granted cuts/frontiers"
            )
        local: list[ItemRef] = []
        for op in batch.operations:
            candidate.operation(partition, op, intent, local, bootstrap)
        from dataclasses import fields

        encoded_batch: dict[str, Any] = {
            "$type": "Batch",
            "fields": {
                f.name: encode(getattr(batch, f.name))
                for f in fields(batch)
                if f.name != "operations"
            },
        }
        encoded_batch["fields"]["operations"] = [
            {"$item": ref.item_index}
            if "$fact" in candidate.items[ref.item_index]
            or "$retract" in candidate.items[ref.item_index]
            else candidate.items[ref.item_index]["proposal"]
            for ref in local
        ]
        encoded_batches.append({"partition": partition, "batch": encoded_batch})
        candidate.add(
            {
                "kind": "return",
                "intent": encode(ref),
                "partition": partition,
                "reached": encode(batch.reached),
                "native_reached_ns": batch.native_reached_ns,
                "native_interval": [intent["native_before"], batch.native_reached_ns],
                "native_input_cut": intent["native_input_cut"],
                "read_cut": intent["read_cut"],
            }
        )
        candidate.state.frontiers[partition] = (instant, batch.native_reached_ns)
        candidate.state.intents[ref] = {
            **candidate.state.intents[ref],
            "status": "returned",
        }
        candidate.state.pending_intents.pop(partition)
    from .relations import validate_relations
    from .sampling import validate_sample_work

    validate_relations(candidate)
    validate_sample_work(candidate.state, instant.ns)
    record = {
        "type": "transaction",
        "index": candidate.index,
        "instant": encode(instant),
        "phase": phase,
        "batches": encoded_batches,
        "items": candidate.items,
    }
    if candidate.fact_rows.tables["entities"]:
        record["fact_tables"] = candidate.fact_rows.tables
    candidate.state.records.append(record)
    cut = Cut(candidate.index, instant)
    if candidate.state.cut != cut:
        candidate.state.cuts.append(cut)
    return record
