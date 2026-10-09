"""Out-of-process engines with scoped views and atomic proposal publication."""

from __future__ import annotations

import hashlib
import threading
from bisect import bisect_right
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from fnmatch import fnmatchcase
from types import MappingProxyType
from typing import Any, cast

from .binding import BindingManifest
from .codec import decode_record, encode
from .engine import Batch, Engine, Horizon, Partition, RunContext
from .errors import KernelError
from .messages import Actions, Delivery, Dirty
from .registry import MemoryRegistry
from .rng import RNGStreams
from .rpc_transport import (
    MAJOR,
    MINOR,
    PROTOCOL,
    PausePolicy,
    RPCConnection,
    bounded,
    serve_requests,
)
from .sampling import RecordedFrame
from .state import Fact, FactVersions, Retraction, StateView, Store
from .storage import AppendList
from .time import Cut, Instant
from .values import ResourceBudget, canonical_json, freeze, thaw, typed_equal


def _projection(view: StateView, owners: tuple[Partition, ...]) -> dict[str, Any]:
    """Export only declared histories, identities and actions."""
    store, cap = view._store, view.cut
    owned = {p.id for p in owners}
    fields = {f for p in owners for f in (*p.produces, *(d.field for d in p.consumes))}
    relations = {
        r
        for p in owners
        for r in (
            *p.relation_produces,
            *p.obligation_produces,
            *(d.relation_id for d in p.relation_consumes),
        )
    }
    messages = {m for p in owners for m in (*p.commands, *p.emits)}
    # A subscription names a topic; its delivered schemas are already normalized
    # in the inbox. The target's declarations and actual action schemas suffice.
    keys = []
    facts: list[Fact | Retraction] = []
    identities = set()
    for key, writer in store.writers.items():
        limits = []
        for p in owners:
            matches = tuple(
                d
                for d in p.consumes
                if d.field == key[1]
                and fnmatchcase(key[0].id, d.id_pattern)
                and (
                    d.type_id is None or store.registry.is_a(key[0].type_id, d.type_id)
                )
            )
            if matches:
                lag = min(d.lag_ns for d in matches)
                if view.instant.ns >= lag:
                    limits.append(store.cut_at(view.instant.ns - lag, cap))
            elif writer == p.id:
                limits.append(cap)
        if not limits:
            continue
        limit = max(limits, key=lambda c: c.index)
        keys.append((key, writer))
        identities.add(key[0])
        facts.extend(
            row
            for row in store.facts.get(key, ())
            if row.version.record_index <= limit.index
        )
    edge_rows = []
    for rows in store.edges.values():
        head = rows[0]
        limits = []
        for p in owners:
            matches_r = tuple(
                dep
                for dep in p.relation_consumes
                if dep.relation_id == head.relation_id
                and fnmatchcase(head.source.id, dep.id_pattern)
                and (
                    dep.source_type is None
                    or store.registry.is_a(head.source.type_id, dep.source_type)
                )
            )
            if matches_r:
                lag = min(dep.lag_ns for dep in matches_r)
                if view.instant.ns >= lag:
                    limits.append(store.cut_at(view.instant.ns - lag, cap))
            elif head.producer == p.id:
                limits.append(cap)
        if limits:
            limit = max(limits, key=lambda c: c.index)
            visible = tuple(
                row for row in rows if row.version.record_index <= limit.index
            )
            if visible:
                edge_rows.append(visible)
                identities.update((head.source, head.target))
    obligation_rows = []
    for ob_rows in store.obligations.values():
        ob_head = ob_rows[0]
        if ob_head.relation_id in relations and any(
            ob_head.producer == p.id
            or any(
                dep.relation_id == ob_head.relation_id for dep in p.relation_consumes
            )
            for p in owners
        ):
            lags = [
                dep.lag_ns
                for p in owners
                for dep in p.relation_consumes
                if dep.relation_id == ob_head.relation_id
            ]
            limit_ns = view.instant.ns - (
                min(lags) if lags and ob_head.producer not in owned else 0
            )
            ob_visible = (
                ()
                if limit_ns < 0
                else tuple(
                    row
                    for row in ob_rows
                    if row.version.record_index <= store.cut_at(limit_ns, cap).index
                )
            )
            if ob_visible:
                obligation_rows.append(ob_visible)
                identities.add(ob_head.endpoint_ref)
    frames: list[RecordedFrame] = []
    for spec in store.manifest.samples:
        if spec.partition in owned:
            for context in (spec.context_id, *spec.frame_inputs):
                frames.extend(
                    row
                    for row in store.frames.get(context, ())
                    if row.version.record_index <= cap.index
                )
                for row in store.frames.get(context, ()):
                    if row.version.record_index <= cap.index:
                        identities.update(row.frame.bindings.values())
    for ref, life in store.lives.items():
        if life.created.index <= cap.index and any(
            p.lifecycle
            or any(store.registry.is_a(ref.type_id, t) for t in p.lifecycle_reads)
            for p in owners
        ):
            identities.add(ref)
    lives = [
        store.lives[ref]
        for ref in identities
        if ref in store.lives and store.lives[ref].created.index <= cap.index
    ]
    lives = [
        type(life)(
            life.ref,
            life.created,
            life.removed
            if life.removed is not None and life.removed.index <= cap.index
            else None,
        )
        for life in lives
    ]
    snapshot = store.action_snapshots[
        store.action_snapshot_indices[
            bisect_right(store.action_snapshot_indices, cap.index) - 1
        ]
    ]
    actions = [
        snapshot.action(mid)
        for mid, state in snapshot.states.items()
        if state.target in owned
        or (state.source_kind == "partition" and state.source in owned)
        or bool(owned & set(store.manifest.cancel_sources))
    ]
    messages.update(
        snapshot.schemas[mid]
        for mid in (a.command_id for a in actions)
        if mid in snapshot.schemas
    )
    descriptors = [f for f in store.registry.fields if f.id in fields]
    relation_descriptors = [r for r in store.registry.relations if r.id in relations]
    types = {life.ref.type_id for life in lives} | {
        f.declaring_type for f in descriptors
    }
    types.update(t for p in owners for t in p.lifecycle_reads)
    types.update(d.type_id for p in owners for d in p.consumes if d.type_id is not None)
    types.update(
        d.source_type
        for p in owners
        for d in p.relation_consumes
        if d.source_type is not None
    )
    types.update(
        t for r in relation_descriptors for t in (r.source_type, r.target_type)
    )
    # Reset declarations include bootstrap identities before creation publication.
    types.update(
        ref.type_id
        for ref in store.manifest.entities
        if any(
            rule.partition in owned and store.registry.is_a(ref.type_id, rule.type_id)
            for rule in store.manifest.rules
        )
    )
    # Traverse schema positions in the normalized grammar, never enum values
    # or field payloads (whose $ref tags represent entities, not named schemas).
    schemas: dict[str, Any] = {}

    def schema_refs(node: Mapping[str, Any]) -> None:
        if "schema_ref" in node:
            name = node["schema_ref"]
            if name not in schemas:
                schemas[name] = thaw(store.registry.schemas[name])
                schema_refs(schemas[name])
            return
        kind = node["type"]
        if kind == "ref" and "target_type" in node:
            types.add(node["target_type"])
        if kind in {"array", "vector", "matrix"}:
            schema_refs(node["items"])
        elif kind in {"record", "union"}:
            for child in node["members" if kind == "record" else "cases"].values():
                schema_refs(child)

    msg_descriptors = tuple(m for m in store.registry.messages if m.id in messages)
    for f in descriptors:
        schema_refs(f.schema)
    for m in msg_descriptors:
        for node in (m.schema, m.result_schema, m.feedback_schema):
            if node is not None:
                schema_refs(node)
    pending = list(types)
    type_index = {t.id: t for t in store.registry.types}
    while pending:
        for parent in type_index[pending.pop()].parents:
            if parent not in types:
                types.add(parent)
                pending.append(parent)
    registry = MemoryRegistry(
        tuple(type_index[t] for t in sorted(types)),
        tuple(descriptors),
        msg_descriptors,
        schemas,
        store.registry.revision,
        relations=tuple(relation_descriptors),
    )
    return {
        **({"provenance": "lean"} if view.provenance == "lean" else {}),
        "registry": registry.to_data(),
        "manifest": store.manifest.to_data(),
        "partitions": encode(tuple(store.partitions.values())),
        "cuts": encode(tuple(store.cuts[: cap.index + 1])),
        "lives": encode(lives),
        "writers": encode(keys),
        "facts": encode(facts),
        "edges": encode(edge_rows),
        "obligations": encode(obligation_rows),
        "frames": encode(frames),
        "committed_operations": encode(
            tuple(
                (ref, intent["partition"], intent["operation_refs"])
                for ref, intent in store.intents.items()
                if intent["partition"] in owned
                and intent["status"] == "returned"
                and (
                    not intent["operation_refs"]
                    or intent["operation_refs"][-1].record_index
                    <= view.transaction_base_cut.index
                )
            )
        ),
        "actions": encode(actions),
        "action_schemas": {
            a.command_id: snapshot.schemas[a.command_id]
            for a in actions
            if a.command_id in snapshot.schemas
        },
        "metadata": encode(
            (
                view.cut,
                view.transaction_base_cut,
                view.partition,
                view.instant,
                view.native_input_cut,
                view.native_reached_ns,
                view.invocation_ref,
                view.phase,
                view.logical_before,
                view.native_before,
            )
        ),
    }


def _view(data: Any, budget: ResourceBudget) -> StateView:
    """Reconstruct the same read API over the supplied scoped immutable history."""
    registry = MemoryRegistry.from_data(data["registry"])
    manifest = BindingManifest.from_data(data["manifest"])
    partitions = {p.id: p for p in decode_record(data["partitions"])}
    actions = Actions(registry, budget)
    for action in decode_record(data["actions"]):
        actions.states[action.command_id] = action
    actions.schemas.update(data["action_schemas"])
    store = Store(registry, manifest, partitions, actions)
    for ref, partition, refs in decode_record(data["committed_operations"]):
        store.intents[ref] = {
            "partition": partition,
            "status": "returned",
            "operation_refs": refs,
        }
    provenance = data.get("provenance", "full")
    if provenance not in {"lean", "full"}:
        raise KernelError("PROVENANCE", "unsupported RPC provenance level")
    store.provenance = actions.provenance = provenance
    store.cuts = AppendList(list(decode_record(data["cuts"])))
    store.action_snapshots[store.cut.index] = actions
    store.action_snapshot_indices.append(store.cut.index)
    for life in decode_record(data["lives"]):
        store.lives[life.ref] = life
    for key, writer in decode_record(data["writers"]):
        store.writers[key] = writer
    for fact in decode_record(data["facts"]):
        versions = store.facts.get(fact.key)
        versions = FactVersions(fact.key) if versions is None else versions
        if isinstance(fact, Fact):
            fact = replace(fact, value=freeze(fact.value, budget))
        store.facts[fact.key] = versions.with_version(fact, fact.version.record_index)
    for rows in decode_record(data["edges"]):
        head = rows[0]
        store.edges[head.edge_id] = rows
        store.relation_edges[head.relation_id] = store.relation_edges.get(
            head.relation_id, frozenset()
        ) | {head.edge_id}
    for rows in decode_record(data["obligations"]):
        head = rows[0]
        store.obligations[head.obligation_id] = rows
    store.sample_specs = MappingProxyType({s.context_id: s for s in manifest.samples})
    store.sample_partitions = MappingProxyType(
        {s.partition: s.context_id for s in manifest.samples}
    )
    for frame in decode_record(data["frames"]):
        frame = replace(
            frame, frame=replace(frame.frame, result=freeze(frame.frame.result, budget))
        )
        context = frame.frame.context_id
        store.frames[context] = (*store.frames.get(context, ()), frame)
    return StateView(store, *decode_record(data["metadata"]))


class RemoteEngine:
    """Kernel-side proxy; transport or metadata faults taint instead of retrying."""

    def __init__(
        self,
        stream: Any,
        output: Any = None,
        *,
        budget: ResourceBudget | None = None,
        timeouts: Mapping[str, float] | None = None,
        pause_policy: PausePolicy | None = None,
    ) -> None:
        self.connection = RPCConnection(
            stream, output, budget=budget, timeouts=timeouts, pause_policy=pause_policy
        )
        self.budget = self.connection.framer.budget
        self._contract: dict[str, str] | None = None
        self._queries: dict[str, object] = {}
        hello = self.connection.call(
            "hello",
            {
                "protocol": PROTOCOL,
                "major": MAJOR,
                "minors": [MINOR],
                "required_features": []
                if pause_policy is None
                else ["wall_clock_hold/v1"],
                **({"pause_policy": asdict(pause_policy)} if pause_policy else {}),
            },
        )
        try:
            if (
                set(hello)
                != {"minor", "partitions", "engine_version", "features", "budget"}
                | ({"pause_policy"} if pause_policy else set())
                or type(hello["minor"]) is not int
                or hello["minor"] != MINOR
                or hello["features"]
                != ([] if pause_policy is None else ["wall_clock_hold/v1"])
                or (
                    pause_policy is not None
                    and hello.get("pause_policy") != asdict(pause_policy)
                )
                or decode_record(hello["budget"]) != self.budget
            ):
                raise KernelError("RPC_HELLO", "incompatible hello response")
            self.partitions = cast(
                tuple[Partition, ...], decode_record(hello["partitions"])
            )
            if not self.partitions or any(
                not isinstance(p, Partition) for p in self.partitions
            ):
                raise KernelError("RPC_HELLO", "partition declarations required")
            self.version = hello["engine_version"]
            if not isinstance(self.version, str):
                raise KernelError("RPC_HELLO", "engine version must be text")
        except Exception:
            self.connection.taint()
            raise

    def _bind_contract(
        self, registry_digest: str, manifest_digest: str, budget: ResourceBudget
    ) -> None:
        if self.budget != budget:
            raise KernelError("RPC_BUDGET", "remote/kernel budgets must agree")
        self._contract = {
            "registry_digest": registry_digest,
            "manifest_digest": manifest_digest,
        }

    def _kernel_query(
        self, partition: str, cut: Cut, logical: Instant, native: int, native_cut: Cut
    ) -> None:
        self._queries[partition] = encode((cut, logical, native, native_cut))

    @property
    def rpc_profile(self) -> dict[str, Any]:
        """Negotiated version and pinned per-operation deadlines."""
        return {**self.connection.profile, "contract": self._contract}

    def _fault(self, code: str, message: str, op: str) -> KernelError:
        self.connection.taint()
        return KernelError(
            code,
            message,
            op=op,
            request_id=self.connection.request_id,
            timeout_s=self.connection.timeouts["horizon" if op == "hold" else op],
            policy=self.connection.profile,
        )

    def _call(self, op: str, payload: dict[str, Any]) -> Any:
        token = hashlib.sha256(
            canonical_json(
                {
                    "id": self.connection.request_id + 1,
                    "op": op,
                    "coordinates": payload.get("coordinates"),
                },
                self.budget,
            )
        ).hexdigest()
        result = self.connection.call(
            op, {**payload, "token": token, "contract": self._contract}
        )
        try:
            if (
                not isinstance(result, dict)
                or set(result) != {"token", "value", "contract"}
                or result["token"] != token
                or result["contract"] != self._contract
            ):
                raise KernelError("RPC_TOKEN", "response invocation token mismatch")
            return decode_record(result["value"])
        except Exception as error:
            code = error.code if isinstance(error, KernelError) else "RPC_DECODE"
            raise self._fault(code, str(error), op) from error

    def reset(self, context: RunContext, view: StateView) -> tuple[Batch, ...]:
        """Reset once with pinned seeds and the authorized bootstrap view."""
        data = {
            "root_seed": context.root_seed,
            "run_id": context.run_id,
            "epoch": context.epoch,
            "configuration": thaw(context.configuration),
        }
        result = self._call(
            "reset",
            {
                "context": data,
                "view": _projection(view, self.partitions),
                "coordinates": encode((view.instant, view.cut)),
            },
        )
        if (
            not isinstance(result, tuple)
            or len(result) != len(self.partitions)
            or any(
                not isinstance(b, Batch)
                or (
                    b.reached,
                    b.transaction_base_cut,
                    b.read_cut,
                    b.native_reached_ns,
                    b.native_input_cut,
                )
                != (
                    view.instant,
                    view.transaction_base_cut,
                    view.cut,
                    view.native_reached_ns,
                    view.native_input_cut,
                )
                for b in result
            )
        ):
            raise self._fault("RPC_BATCH", "reset batch metadata mismatch", "reset")
        return result

    def horizon(self, partition: str, cut: Cut) -> Horizon:
        """Request a cut-specific promise without another view transfer."""
        result = self._call(
            "horizon",
            {
                "partition": partition,
                "expected": self._queries.get(partition),
                "query_ref": self.connection.request_id + 1,
                "cut": encode(cut),
                "coordinates": encode((partition, cut)),
            },
        )
        if not isinstance(result, Horizon) or result.input_cut != cut:
            raise self._fault(
                "RPC_HORIZON", "horizon did not echo the input cut", "horizon"
            )
        query = self._queries.get(partition)
        if query is not None:
            _, logical, native, _ = decode_record(query)
            if result.reached != logical or result.native_reached_ns != native:
                self.connection.taint()
                raise KernelError(
                    "RPC_HORIZON", "promise differs from recorded prestate"
                )
        return result

    def _batch(
        self,
        op: str,
        partition: str,
        to: Instant,
        view: StateView,
        inbox: tuple[Delivery, ...] = (),
        dirty: tuple[Dirty, ...] = (),
    ) -> Batch:
        declarations = tuple(p for p in self.partitions if p.id == partition)
        if not declarations:
            raise KernelError("RPC_PARTITION", "unknown remote partition")
        result = self._call(
            op,
            {
                "partition": partition,
                "to": encode(to),
                "view": _projection(view, declarations),
                "inbox": encode(inbox),
                "dirty": encode(dirty),
                "coordinates": encode(
                    (
                        partition,
                        to,
                        view.cut,
                        view.transaction_base_cut,
                        view.native_input_cut,
                        view.native_reached_ns,
                    )
                ),
            },
        )
        if not isinstance(result, Batch) or (
            result.reached,
            result.transaction_base_cut,
            result.read_cut,
            result.native_input_cut,
            result.native_reached_ns,
        ) != (
            to,
            view.transaction_base_cut,
            view.cut,
            view.native_input_cut,
            view.native_reached_ns,
        ):
            raise self._fault("RPC_BATCH", "batch invocation metadata mismatch", op)
        return result

    def advance(self, partition: str, to: Instant, view: StateView) -> Batch:
        """Complete the actual grant; publish only after full batch validation."""
        return self._batch("advance", partition, to, view)

    def react(
        self,
        partition: str,
        to: Instant,
        view: StateView,
        inbox: tuple[Delivery, ...],
        dirty: tuple[Dirty, ...],
    ) -> Batch:
        """Deliver the declared inbox separately from interval integration."""
        return self._batch("react", partition, to, view, inbox, dirty)

    def hold_wall_clock(self, duration_s: float, reason: str) -> None:
        """Acknowledge a negotiated arrival lease; never invoke native state work."""
        policy = self.connection.pause_policy
        if policy is None:
            raise KernelError("RPC_HOLD", "wall_clock_hold/v1 must be negotiated")
        policy.check_hold(duration_s)
        if not isinstance(reason, str) or not reason:
            raise KernelError("RPC_HOLD", "hold reason required")
        value = self._call("hold", {"duration_s": duration_s, "reason": reason})
        if not typed_equal(value, {"duration_s": duration_s, "reason": reason}):
            raise self._fault("RPC_HOLD", "lease acknowledgment mismatch", "hold")

    def close(self) -> None:
        """Idempotent acknowledged shutdown; faults are never retried."""
        if self.connection.state == "closed":
            return
        if self.connection.state == "tainted":
            bounded(
                self.connection.framer.stream.close,
                self.connection.timeouts["close"],
                op="cleanup",
            )
            if self.connection.framer.output is not self.connection.framer.stream:
                bounded(
                    self.connection.framer.output.close,
                    self.connection.timeouts["close"],
                    op="cleanup",
                )
            self.connection.state = "closed"
            return
        self._call("close", {"coordinates": None})


def serve_engine(
    engine: Engine,
    stream: Any,
    output: Any = None,
    *,
    budget: ResourceBudget | None = None,
    timeouts: Mapping[str, float] | None = None,
    pause_policy: PausePolicy | None = None,
    abort: Callable[[], None] | None = None,
    abort_timeout_s: float = 5.0,
) -> None:
    """Run any SDK engine over binary streams or a connected socket."""
    from .rpc_transport import timeout_policy

    timeout_policy({"close": abort_timeout_s})
    limits = ResourceBudget() if budget is None else budget

    contract: object = None
    engine_lock = threading.RLock()
    negotiated_hold = False
    pauses = PausePolicy() if pause_policy is None else pause_policy

    def handle(op: str, data: Any) -> Any:
        nonlocal contract, negotiated_hold
        if op == "hello":
            if (
                not isinstance(data, dict)
                or set(data)
                not in (
                    {"protocol", "major", "minors", "required_features"},
                    {
                        "protocol",
                        "major",
                        "minors",
                        "required_features",
                        "pause_policy",
                    },
                )
                or (
                    data["protocol"] != PROTOCOL
                    or type(data["major"]) is not int
                    or data["major"] != MAJOR
                    or not isinstance(data["minors"], list)
                    or any(type(minor) is not int for minor in data["minors"])
                    or MINOR not in data["minors"]
                    or data["required_features"] not in ([], ["wall_clock_hold/v1"])
                )
            ):
                raise KernelError("RPC_HELLO", "unsupported protocol/features")
            negotiated_hold = data["required_features"] == ["wall_clock_hold/v1"]
            if negotiated_hold:
                if data.get("pause_policy") != asdict(pauses):
                    raise KernelError(
                        "RPC_POLICY", "client/server pause declarations differ"
                    )
            elif "pause_policy" in data:
                raise KernelError(
                    "RPC_POLICY", "pause declaration needs negotiated feature"
                )
            return {
                "minor": MINOR,
                "partitions": encode(engine.partitions),
                "engine_version": engine.version,
                "features": ["wall_clock_hold/v1"] if negotiated_hold else [],
                **({"pause_policy": asdict(pauses)} if negotiated_hold else {}),
                "budget": encode(limits),
            }
        if not isinstance(data, dict) or "token" not in data:
            raise KernelError("RPC_TOKEN", "invocation token required")
        if op == "reset":
            contract = data["contract"]
        elif data["contract"] != contract:
            raise KernelError("RPC_CONTRACT", "binding contract changed")
        if op == "reset":
            ctx = data["context"]
            context = RunContext(
                ctx["root_seed"],
                ctx["run_id"],
                ctx["epoch"],
                MappingProxyType(
                    {
                        p.id: RNGStreams(
                            ctx["root_seed"], p.engine_id, p.id, p.rng_streams, limits
                        )
                        for p in engine.partitions
                    }
                ),
                freeze(ctx["configuration"], limits),
            )
            value: object = engine.reset(context, _view(data["view"], limits))
        elif op == "horizon":
            value = engine.horizon(data["partition"], decode_record(data["cut"]))
        elif op in {"advance", "react"}:
            view = _view(data["view"], limits)
            to = decode_record(data["to"])
            if op == "advance":
                value = engine.advance(data["partition"], to, view)
            else:
                value = engine.react(
                    data["partition"],
                    to,
                    view,
                    decode_record(data["inbox"]),
                    decode_record(data["dirty"]),
                )
        elif op == "hold":
            if not negotiated_hold or set(data) != {
                "token",
                "contract",
                "duration_s",
                "reason",
            }:
                raise KernelError("RPC_HOLD", "negotiated explicit lease required")
            pauses.check_hold(data["duration_s"])
            if not isinstance(data["reason"], str) or not data["reason"]:
                raise KernelError("RPC_HOLD", "hold reason required")
            value = {"duration_s": data["duration_s"], "reason": data["reason"]}
        elif op == "close":
            engine.close()
            value = None
        else:
            raise KernelError("RPC_OPERATION", "unknown engine operation")
        return {"token": data["token"], "value": encode(value), "contract": contract}

    def serial_handle(op: str, data: Any) -> Any:
        with engine_lock:
            return handle(op, data)

    def cleanup() -> None:
        with engine_lock:
            engine.close()

    try:
        serve_requests(
            serial_handle,
            stream,
            output,
            budget=limits,
            timeouts=timeouts,
            pause_policy=pauses,
        )
    except Exception:
        if abort is not None:
            bounded(abort, abort_timeout_s, op="abort")
        raise
    finally:
        bounded(cleanup, timeout_policy(timeouts)["close"], op="cleanup")
