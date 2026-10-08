"""Serial central barrier, private commits and read-only deterministic replay."""

from __future__ import annotations

import platform
import threading
from collections.abc import Mapping
from dataclasses import asdict
from types import MappingProxyType
from typing import Any

from .binding import BindingManifest
from .codec import decode_record, encode
from .control import boundary_control, publish_ingress, reserve
from .engine import Batch, Engine, Partition, RunContext
from .errors import KernelError, MicrostepLimitExceeded, SynchronizationDeadlock
from .ids import ItemRef, message_id, validate_text
from .ingress import IngressPolicy
from .journal import Journal
from .messages import Actions, ActionState, CancelRequestResult, CommandRequest
from .registry import MemoryRegistry, Registry
from .rng import RNGStreams
from .sampling import compile_samples, ordinary_earliest, sample_ready
from .scheduling import build_wave, ready_partitions, wave_intents
from .state import StateView, Store
from .time import ClockMapping, Cut, Instant, Stamp
from .transactions import Candidate
from .values import FrozenValue, ResourceBudget, canonical_json, freeze, thaw


class Kernel:
    """Conservative kernel with publication accessible only through execution."""

    def __init__(
        self,
        *,
        root_seed: int = 0,
        journal: Journal | None = None,
        mappings: tuple[ClockMapping, ...] | None = None,
        max_microsteps: int = 1024,
        budget: ResourceBudget | None = None,
        configuration: object = None,
        ingress_policy: IngressPolicy | None = None,
    ) -> None:
        if type(root_seed) is not int:
            raise KernelError("SEED", "root seed must be an integer")
        if type(max_microsteps) is not int or max_microsteps < 1:
            raise KernelError("MICROSTEP_BUDGET", "positive global bound required")
        if ingress_policy is not None and not isinstance(ingress_policy, IngressPolicy):
            raise KernelError("INGRESS_POLICY", "typed ingress policy required")
        self.ingress_policy = ingress_policy
        self._ingress_condition = threading.Condition(threading.RLock())
        self._pace_origin: float | None = None
        self.root_seed = root_seed
        self.budget = ResourceBudget() if budget is None else budget
        self.configuration: FrozenValue = freeze(configuration, self.budget)
        self.journal = Journal(budget=self.budget) if journal is None else journal
        if self.journal.budget != self.budget:
            raise KernelError(
                "RESOURCE_POLICY", "journal/kernel resource budgets differ"
            )
        chosen = (
            (ClockMapping("canonical", "canonical"),) if mappings is None else mappings
        )
        self.mappings = MappingProxyType({m.mapping_id: m for m in chosen})
        if len(self.mappings) != len(chosen):
            raise KernelError("CLOCK_MAPPING", "mapping IDs must be unique")
        self.max_microsteps = max_microsteps
        self._bound = False
        self._started = False
        self._closed = False
        self._read_only = False
        self.incomplete = False
        self._engines: dict[str, Engine] = {}
        self._by_partition: dict[str, Engine] = {}
        self._trace: list[dict[str, Any]] = []

    def bind(
        self, registry: Registry, manifest: BindingManifest, engines: tuple[Engine, ...]
    ) -> None:
        """Pin normalized contracts and validate all selected authorities once."""
        if self._bound:
            raise KernelError("BIND_ONCE", "kernel is already bound")
        registry = MemoryRegistry.from_data(registry.to_data())
        partitions: dict[str, Partition] = {}
        for engine in engines:
            if not engine.partitions:
                raise KernelError(
                    "ENGINE_PARTITIONS", "engine needs declared partitions"
                )
            eid = engine.partitions[0].engine_id
            validate_text(eid)
            if eid in self._engines:
                raise KernelError("ENGINE_DUPLICATE", "engine IDs must be unique")
            version = getattr(engine, "version", None)
            if not isinstance(version, str):
                raise KernelError("ENGINE_VERSION", "engine must pin a version string")
            validate_text(version)
            self._engines[eid] = engine
            for p in engine.partitions:
                validate_text(p.id)
                if p.id in partitions or p.engine_id != eid:
                    raise KernelError(
                        "PARTITION_ID",
                        "globally unique partition/consistent engine required",
                    )
                self._validate_partition(p, registry)
                partitions[p.id] = p
                self._by_partition[p.id] = engine
        manifest = type(manifest).from_data(manifest.to_data())
        manifest.validate(registry, partitions)
        self._check_cycles(partitions, registry, manifest)
        self._store = Store(
            registry, manifest, partitions, Actions(registry, self.budget)
        )
        compile_samples(self._store, self._dependency_graph, self.mappings)
        self._store.max_microsteps = self.max_microsteps
        if self.ingress_policy is not None:
            self._store.watermark_ns = self.ingress_policy.initial_watermark_ns
        streams = {
            p.id: RNGStreams(
                self.root_seed, p.engine_id, p.id, p.rng_streams, self.budget
            )
            for p in partitions.values()
        }
        self.context = RunContext(
            self.root_seed,
            manifest.run_id,
            manifest.epoch,
            MappingProxyType(streams),
            self.configuration,
        )
        import hashlib

        manifest_digest = hashlib.sha256(
            canonical_json(manifest.to_data(), self.budget)[:-1]
        ).hexdigest()
        for engine in self._engines.values():
            bind_contract = getattr(engine, "_bind_contract", None)
            if bind_contract is not None:
                bind_contract(registry.digest, manifest_digest, self.budget)
        header = {
            "type": "header",
            "format": "aerokernel.journal",
            "major": 1,
            "minor": 2,
            "index": 0,
            "instant": encode(Instant(0)),
            "time_origin_ns": 0,
            "run_id": manifest.run_id,
            "epoch": manifest.epoch,
            "registry": registry.to_data(),
            "registry_digest": registry.digest,
            "manifest": manifest.to_data(),
            "partitions": [encode(partitions[p]) for p in sorted(partitions)],
            "mappings": encode(tuple(self.mappings[k] for k in sorted(self.mappings))),
            "budget": asdict(self.budget),
            "max_microsteps": self.max_microsteps,
            "durability": self.journal.durability,
            "root_seed": self.root_seed,
            "configuration": thaw(self.configuration),
            "ingress_policy": encode(self.ingress_policy),
            "engine_profiles": {
                eid: engine.rpc_profile
                for eid, engine in sorted(self._engines.items())
                if hasattr(engine, "rpc_profile")
            },
            "serializer": {
                "implementation": platform.python_implementation(),
                "python": platform.python_version(),
                "encoder": "stdlib-json/v1",
            },
            "rng": {
                "implementation": "random.Random/MT19937",
                "python": platform.python_version(),
                "seeds": {p: streams[p].seeds for p in sorted(streams)},
            },
            "engine_versions": {
                eid: self._engines[eid].version for eid in sorted(self._engines)
            },
            "resolved_bindings": [
                {
                    "ref": encode(ref),
                    "controller": manifest.controller(ref, registry, partitions),
                    "cleanup_participants": list(manifest.participants(ref, registry)),
                    "writers": manifest.resolve(ref, registry, partitions),
                }
                for ref in sorted(manifest.entities, key=lambda r: (r.id, r.generation))
            ],
        }
        self.journal.append(header)
        self.header = header
        self._bound = True

    def _validate_partition(self, p: Partition, registry: MemoryRegistry) -> None:
        for feature in p.features:
            if feature not in {"sampled", "relations", "rpc"}:
                raise KernelError("PARTITION_FEATURE", "unknown active capability")
        if p.timing.mode == "real_time" and self.ingress_policy is None:
            raise KernelError(
                "INGRESS_POLICY", "real_time requires a declared watermark policy"
            )
        if p.timing.mode not in {"des", "fixed_step", "lockstep", "real_time"}:
            raise KernelError("TIMING_MODE", "unknown timing mode")
        if p.timing.mode == "lockstep":
            if any(
                type(flag) is not bool
                for flag in (p.timing.exact_stop, p.timing.certified_hold)
            ):
                raise KernelError(
                    "LOCKSTEP_CONTRACT", "explicit bool capabilities required"
                )
            if p.timing.step_ns is not None and (
                type(p.timing.step_ns) is not int or p.timing.step_ns <= 0
            ):
                raise KernelError("TIMING_STEP", "positive communication step required")
            if not p.timing.exact_stop and (
                p.timing.step_ns is None or not p.timing.certified_hold
            ):
                raise KernelError(
                    "LOCKSTEP_CONTRACT",
                    "declare exact stops or a native grid with certified holds",
                )
            if not p.timing.latch and not p.timing.exact_stop:
                raise KernelError(
                    "LOCKSTEP_CONTRACT", "off-grid inputs require exact splitting"
                )
        if type(p.timing.latch) is not bool:
            raise KernelError("TIMING_LATCH", "latch policy must be explicit bool")
        if p.timing.mode == "des" and p.timing.step_ns is not None:
            raise KernelError("TIMING_STEP", "DES does not declare a native grid step")
        if (
            type(p.timing.origin_ns) is not int
            or p.timing.origin_ns < 0
            or (p.timing.mode == "des" and p.timing.origin_ns != 0)
        ):
            raise KernelError("TIMING_ORIGIN", "invalid native grid origin")
        if p.timing.mode == "fixed_step":
            if type(p.timing.step_ns) is not int or p.timing.step_ns <= 0:
                raise KernelError("TIMING_STEP", "positive integer fixed step required")
        if type(p.message_lag_ns) is not int or p.message_lag_ns < 0:
            raise KernelError(
                "ROUTE_LAG", "nonnegative integer recipient route lag required"
            )
        if p.emits and not p.message_targets:
            raise KernelError(
                "MESSAGE_TARGETS", "emitting partitions must pin target/topic domains"
            )
        for target in p.message_targets:
            validate_text(target)
        for d in p.consumes:
            if type(d.lag_ns) is not int or d.lag_ns < 0:
                raise KernelError("DEPENDENCY_LAG", "nonnegative integer lag required")
            registry.field(d.field)
            if d.type_id is not None:
                registry.is_a(d.type_id, d.type_id)
        for relation_id in (*p.relation_produces, *p.obligation_produces):
            registry.relation(relation_id)
        for dependency in p.relation_consumes:
            registry.relation(dependency.relation_id)
            if (
                type(dependency.lag_ns) is not int
                or dependency.lag_ns < 0
                or type(dependency.value_only) is not bool
            ):
                raise KernelError(
                    "RELATION_DEPENDENCY", "invalid relation lag/value-only declaration"
                )
            validate_text(dependency.id_pattern)
            if dependency.source_type is not None:
                registry.is_a(dependency.source_type, dependency.source_type)
        for field in p.produces:
            registry.field(field)
        for schema in (*p.commands, *p.emits):
            registry.message(schema)
        for schema in p.commands:
            if registry.message(schema).kind != "command":
                raise KernelError("COMMAND_KIND", "commands must use command schemas")

    def _check_cycles(
        self,
        partitions: Mapping[str, Partition],
        registry: MemoryRegistry,
        manifest: BindingManifest,
    ) -> None:
        edges: dict[str, set[str]] = {p: set() for p in partitions}
        selected: dict[str, set[str]] = {p: set() for p in partitions}
        for rule in manifest.rules:
            selected[rule.partition].update(rule.fields)
        for exact in manifest.exact:
            selected[exact.partition].add(exact.field)
        consumers: dict[str, set[str]] = {}
        subscribers: dict[str, set[str]] = {}
        for target, p in partitions.items():
            for dep in p.consumes:
                if dep.lag_ns == 0:
                    consumers.setdefault(dep.field, set()).add(target)
            if p.message_lag_ns == 0:
                for topic in p.subscribes:
                    subscribers.setdefault(topic, set()).add(target)
        for source, p in partitions.items():
            for field in selected[source]:
                edges[source].update(consumers.get(field, ()))
            for destination in p.message_targets:
                if destination in partitions:
                    if partitions[destination].message_lag_ns == 0:
                        edges[source].add(destination)
                else:
                    edges[source].update(subscribers.get(destination, ()))
        relation_readers: dict[str, set[str]] = {}
        for target, p in partitions.items():
            for dependency in p.relation_consumes:
                if dependency.lag_ns == 0:
                    relation_readers.setdefault(dependency.relation_id, set()).add(
                        target
                    )
        for source, p in partitions.items():
            for relation_id in (*p.relation_produces, *p.obligation_produces):
                edges[source].update(relation_readers.get(relation_id, ()))
        context_partitions = {s.context_id: s.partition for s in manifest.samples}
        for spec in manifest.samples:
            if spec.partition not in partitions:
                raise KernelError("SAMPLE_DECLARATION", "unknown evaluator partition")
            for context in spec.frame_inputs:
                if context not in context_partitions:
                    raise KernelError("SAMPLE_INPUT", "unknown frame dependency")
                edges[context_partitions[context]].add(spec.partition)
        # Kosaraju visits each node/edge once, rather than enumerating DAG paths.
        graph = {p: tuple(sorted(targets)) for p, targets in edges.items()}
        reverse: dict[str, list[str]] = {p: [] for p in partitions}
        for source, targets in graph.items():
            for target in targets:
                reverse[target].append(source)
        seen: set[str] = set()
        finished: list[str] = []
        for root in sorted(partitions):
            if root in seen:
                continue
            seen.add(root)
            stack = [(root, iter(graph[root]))]
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    finished.append(node)
                    stack.pop()
                elif child not in seen:
                    seen.add(child)
                    stack.append((child, iter(graph[child])))
        seen.clear()
        components = []
        for root in reversed(finished):
            if root in seen:
                continue
            component = []
            pending = [root]
            seen.add(root)
            while pending:
                node = pending.pop()
                component.append(node)
                for target in reverse[node]:
                    if target not in seen:
                        seen.add(target)
                        pending.append(target)
            members = tuple(sorted(component))
            cyclic = len(members) > 1 or root in edges[root]
            if cyclic and any(not partitions[p].reactive for p in members):
                raise KernelError(
                    "CYCLE_REENTRY", "unsupported reaction cycle", path=members
                )
            components.append(members)
        self._dependency_graph = graph
        self._sccs = tuple(components)

    def _guard(self, started: bool = True) -> None:
        if not self._bound or (started and not self._started):
            raise KernelError("RUN_STATE", "operation requires bound/started kernel")
        if self._read_only:
            raise KernelError("REPLAY_READ_ONLY", "offline replay exposes reads only")
        if self._closed or self._store.faulted or self.journal.failed:
            raise KernelError("RUN_FAULTED", "run is closed or faulted")

    def _publish(self, state: Store, record: dict[str, Any]) -> None:
        line = self.journal.append(record, trusted_fact_rows="fact_tables" in record)
        state.records.freeze_tail(line)
        if state.actions.states.writes:
            state.action_snapshots[state.cut.index] = state.actions
            state.action_snapshot_indices.append(state.cut.index)
        self._store = state

    def _candidate(self, instant: Instant) -> Candidate:
        return Candidate(
            self._store, instant, self._store.cut.index + 1, self.mappings, self.budget
        )

    def _fault(self, exc: Exception, returned: tuple[str, ...] = ()) -> None:
        if isinstance(exc, KernelError):
            exc.context = MappingProxyType(
                {
                    "instant": self._store.cut.instant,
                    "cut": self._store.cut,
                    **exc.context,
                }
            )
        self._store.faulted = True
        if self.journal.failed:
            return
        state = self._store.clone()
        code = getattr(exc, "code", "ENGINE_EXCEPTION")
        items = []
        for ref, intent in state.intents.items():
            if intent["status"] == "pending":
                status = (
                    "returned_unpublished"
                    if intent["partition"] in returned
                    else "not_returned"
                )
                state.intents[ref] = {**intent, "status": status}
                items.append(
                    {
                        "kind": "invocation_fault",
                        "intent": encode(ref),
                        "status": status,
                    }
                )
        state.pending_intents.clear()
        record = {
            "type": "fault",
            "index": state.cut.index + 1,
            "instant": encode(state.cut.instant),
            "code": code,
            "items": items,
        }
        if isinstance(exc, KernelError) and {
            "op",
            "request_id",
            "timeout_s",
            "policy",
        }.issubset(exc.context):
            record["rpc"] = {
                key: encode(exc.context[key])
                for key in ("op", "request_id", "timeout_s", "policy")
                if key in exc.context
            }
        state.records.append(record)
        state.cuts.append(Cut(record["index"], state.cut.instant))
        self._publish(state, record)

    def _wave(
        self,
        selected: tuple[str, ...],
        instant: Instant,
        phase: str,
        horizons: dict[str, Any] | None = None,
    ) -> None:
        selected = tuple(
            sorted(selected, key=lambda p: (self._store.partitions[p].engine_id, p))
        )
        state, intent_record, views = wave_intents(
            self._store, selected, instant, phase
        )
        self._publish(state, intent_record)
        batches: list[tuple[str, Batch]] = []
        returned: list[str] = []
        try:
            if phase == "reset":
                for eid in sorted(self._engines):
                    engine = self._engines[eid]
                    pids = tuple(p.id for p in engine.partitions)
                    view = views[pids[0]]
                    context = RunContext(
                        self.root_seed,
                        self.context.run_id,
                        self.context.epoch,
                        MappingProxyType({p: self.context.rng[p] for p in pids}),
                        self.configuration,
                    )
                    results = engine.reset(context, view)
                    returned.extend(pids)
                    if len(results) != len(pids):
                        raise KernelError(
                            "RESET_RETURNS",
                            "reset needs one batch per declared partition",
                        )
                    batches.extend(zip(pids, results, strict=True))
            else:
                for partition in selected:
                    engine = self._by_partition[partition]
                    intent = next(
                        i
                        for i in intent_record["items"]
                        if i["kind"] == "intent" and i["partition"] == partition
                    )
                    if phase == "advance":
                        self._input_coverage(partition, views[partition])
                        batch = engine.advance(partition, instant, views[partition])
                    else:
                        batch = engine.react(
                            partition,
                            instant,
                            views[partition],
                            decode_record(intent["inbox"]),
                            decode_record(intent["dirty"]),
                        )
                    returned.append(partition)
                    batches.append((partition, batch))
            if horizons is not None:
                for partition, batch in batches:
                    if any(
                        type(op).__name__
                        in {"FactWrite", "RetractFact", "Emit", "Receipt", "Feedback"}
                        for op in batch.operations
                    ):
                        h = horizons[partition]
                        if h.output_lb_ns is None or instant.ns < h.output_lb_ns:
                            raise KernelError(
                                "OUTPUT_BOUND", "output violates uninvalidated horizon"
                            )
            candidate = self._candidate(instant)
            record = build_wave(self._store, instant, phase, batches, candidate)
            if phase == "reset":
                for request in self._store.manifest.bootstrap_commands:
                    publish_ingress(candidate, request)
                # Generated ingress is part of this same atomic record.
                record["items"] = candidate.items
            self._publish(candidate.state, record)
            self._trace.append(
                {
                    "instant": encode(instant),
                    "phase": phase,
                    "partitions": list(selected),
                }
            )
        except Exception as exc:
            self._fault(exc, tuple(returned))
            raise

    def _input_coverage(self, partition: str, view: StateView) -> None:
        from fnmatch import fnmatchcase

        from .state import Fact

        native_start = self._store.frontiers[partition][1]
        if view.native_reached_ns <= native_start:
            return
        for dependency in self._store.partitions[partition].consumes:
            if not dependency.require_coverage:
                continue
            start_ns = native_start - dependency.lag_ns
            end_ns = view.native_reached_ns - dependency.lag_ns
            if start_ns < 0:
                raise KernelError(
                    "INPUT_COVERAGE", "required input predates run origin"
                )
            keys = [
                key
                for key in self._store.writers
                if key[1] == dependency.field
                and fnmatchcase(key[0].id, dependency.id_pattern)
                and (
                    dependency.type_id is None
                    or self._store.registry.is_a(key[0].type_id, dependency.type_id)
                )
            ]
            if not keys:
                raise KernelError(
                    "INPUT_COVERAGE", "required input scope has no active source"
                )
            for key in keys:
                start, end = Instant(start_ns), Instant(end_ns)
                cut = self._store.cut_at(start_ns, view.cut)
                history = self._store.history(key, start, end, cut)
                boundaries = {start}
                for version in history:
                    if start <= version.valid.start < end:
                        boundaries.add(version.valid.start)
                    if (
                        version.valid.end is not None
                        and start <= version.valid.end < end
                    ):
                        boundaries.add(version.valid.end)
                if any(
                    not isinstance(self._store.field(key, at, cut), Fact)
                    for at in boundaries
                ):
                    raise KernelError(
                        "INPUT_COVERAGE",
                        "required input has an uncovered native interval",
                        key=key,
                    )

    def start(self) -> StateView:
        """Reset once, atomically bootstrap, settle all t=0 work and seal it."""
        self._guard(False)
        if self._started:
            raise KernelError("START_ONCE", "run already started")
        self._started = True
        try:
            self._wave(tuple(self._store.partitions), Instant(0), "reset")
            self._settle(0)
        except Exception as exc:
            if not self._store.faulted:
                self._fault(exc)
            raise
        if (
            self.ingress_policy is not None
            and self.ingress_policy.speed_ratio is not None
        ):
            import time

            self._pace_origin = time.monotonic()
        return self.view()

    def submit(self, command: CommandRequest) -> str:
        """Reserve later ingress; keyed duplicates retain identity."""
        self._guard()
        state, record, mid = reserve(self._store, command, self.budget)
        if record:
            self._publish(state, record)
        return mid

    def advance_watermark(self, ns: int) -> None:
        """Record closure of the declared ingress prefix and wake bounded waits."""
        with self._ingress_condition:
            self._guard()
            if (
                self.ingress_policy is None
                or type(ns) is not int
                or self._store.watermark_ns is None
                or ns < self._store.watermark_ns
                or self._store.pending_intents
            ):
                raise KernelError(
                    "INGRESS_WATERMARK", "invalid or undeclared prefix closure"
                )
            if ns != self._store.watermark_ns:
                self._record_control("watermark", {"watermark_ns": ns})
            self._ingress_condition.notify_all()

    def submit_live(self, command: CommandRequest, stamp: Stamp) -> str:
        """Reserve actual stamped ingress under the pinned reject/delay policy."""
        from .ingress import reserve_live

        with self._ingress_condition:
            self._guard()
            if self.ingress_policy is None:
                raise KernelError(
                    "INGRESS_POLICY", "live ingress needs a pinned policy"
                )
            state, record, mid = reserve_live(
                self._store,
                command,
                stamp,
                self.ingress_policy,
                self.mappings,
                self.budget,
            )
            if record:
                self._publish(state, record)
                self._ingress_condition.notify_all()
            if mid is None:
                raise KernelError(
                    "LATE_INGRESS", "input falls in a closed ingress prefix"
                )
            return mid

    def _wait_watermark(self, ns: int) -> None:
        if self.ingress_policy is None:
            return
        import time

        if self._store.watermark_ns is None:
            raise KernelError("INGRESS_WATERMARK", "declared stream lost its watermark")
        deadline = time.monotonic() + self.ingress_policy.timeout_s
        while self._store.watermark_ns < ns:
            self._guard()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KernelError(
                    "WATERMARK_TIMEOUT",
                    "declared closed-prefix wait timed out",
                    target_ns=ns,
                    watermark_ns=self._store.watermark_ns,
                    timeout_s=self.ingress_policy.timeout_s,
                )
            self._ingress_condition.wait(remaining)

    def _pace_to(self, ns: int) -> None:
        policy = self.ingress_policy
        if policy is None or policy.speed_ratio is None:
            return
        import time

        if self._pace_origin is None:
            raise KernelError(
                "PACING_ORIGIN", "pacing requires a started wall-clock origin"
            )
        deadline = self._pace_origin + ns / (1_000_000_000 * policy.speed_ratio)
        while True:
            self._guard()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._ingress_condition.wait(min(remaining, 0.05))

    def cancel(self, command_id: str) -> CancelRequestResult:
        """Reserve cancellation or record an explicit rejection."""
        self._guard()
        state = self._store.clone()
        action = state.actions.action(command_id)
        source = state.manifest.control_source
        allowed = (
            action.source_kind == "ingress" and source == action.source
        ) or source in state.manifest.cancel_sources
        message = state.messages.get(command_id)
        support = (
            message is not None
            and state.registry.message(message.schema_id).cancel_support
        )
        reason = (
            "unauthorized"
            if not allowed
            else "status"
            if action.status not in {"accepted", "executing"}
            else "unsupported"
            if not support
            else None
        )
        index = state.cut.index + 1
        ref = ItemRef(index, 0)
        mid = None
        if reason is None:
            assert state.sealed_ns is not None
            seq_key = ("ingress", source)
            seq = state.sequences.get(seq_key, 0)
            state.sequences[seq_key] = seq + 1
            mid = message_id(state.manifest.run_id, state.manifest.epoch, *seq_key, seq)
            data = {
                "kind": "cancel_reservation",
                "cancel": True,
                "command_id": command_id,
                "message_id": mid,
                "sequence": seq,
                "boundary": state.sealed_ns + 1,
                "origin": encode(ref),
            }
            state.pending_ingress[mid] = data
        else:
            data = {
                "kind": "cancel_rejection",
                "command_id": command_id,
                "reason": reason,
            }
        record = {
            "type": "cancel",
            "index": index,
            "instant": encode(state.cut.instant),
            "command_id": command_id,
            "items": [data],
        }
        state.records.append(record)
        state.cuts.append(Cut(index, state.cut.instant))
        self._publish(state, record)
        return CancelRequestResult(
            mid is not None, mid, ref if reason is not None else None
        )

    def _horizons(self) -> dict[str, Any]:
        result = {}
        for pid in sorted(self._store.partitions):
            engine = self._by_partition[pid]
            query = getattr(engine, "_kernel_query", None)
            if query is not None:
                logical, native = self._store.frontiers[pid]
                query(
                    pid, self._store.cut, logical, native, self._store.native_cuts[pid]
                )
            h = engine.horizon(pid, self._store.cut)
            logical, native = self._store.frontiers[pid]
            if (
                h.reached != logical
                or h.native_reached_ns != native
                or h.input_cut != self._store.cut
            ):
                raise KernelError(
                    "HORIZON_FRONTIER", "horizon does not match recorded prestate"
                )
            for value in (h.next_wakeup_ns, h.output_lb_ns, h.grant_limit_ns):
                if value is not None and (type(value) is not int or value < logical.ns):
                    raise KernelError(
                        "HORIZON_BOUND", "invalid/retrograde horizon bound"
                    )
            result[pid] = h
        return result

    def _settle(self, ns: int) -> None:
        while True:
            now = self._store.cut.instant
            earliest = (
                ordinary_earliest(self._store)
                if self._store.sample_specs
                else self._store.work.earliest()
            )
            due = earliest if earliest is not None and earliest.ns <= ns else None
            first_timer = self._store.timer_queue.earliest()
            timers = (
                [first_timer]
                if first_timer is not None and first_timer.ns <= ns
                else []
            )
            if not due and not timers:
                sampled = (
                    sample_ready(self._store, ns) if self._store.sample_specs else ()
                )
                if not sampled:
                    break
                instant = Instant(
                    ns,
                    max(
                        now.microstep + 1,
                        max(
                            w.eligible.microstep
                            for w in self._store.work
                            if w.recipient in sampled and w.eligible.ns <= ns
                        ),
                    ),
                )
                self._check_microstep(instant)
                self._wave(sampled, instant, "sample")
                continue
            next_steps = [] if due is None else [due.microstep]
            next_steps.extend(t.microstep for t in timers)
            instant = Instant(ns, max(now.microstep + 1, min(next_steps)))
            self._check_microstep(instant)
            if any(t <= instant for t in timers):
                candidate = self._candidate(instant)
                record = boundary_control(candidate)
                self._publish(candidate.state, record)
                continue
            ready = self._store.work.ready(instant)
            if not ready:
                raise SynchronizationDeadlock("no eligible same-time work")
            chosen = ready_partitions(self._store, instant)
            if any(not self._store.partitions[p].reactive for p in chosen):
                raise KernelError("REENTRY_UNSUPPORTED", "ready partition cannot react")
            self._wave(tuple(chosen), instant, "react")
        horizons = self._horizons()
        for p, h in horizons.items():
            if any(
                v is not None and v <= ns
                for v in (h.next_wakeup_ns, h.output_lb_ns, h.grant_limit_ns)
            ):
                raise SynchronizationDeadlock("unsettled/stalled bound", partition=p)
        state = self._store.clone()
        index = state.cut.index + 1
        record = {
            "type": "seal",
            "index": index,
            "instant": encode(state.cut.instant),
            "physical_ns": ns,
            "items": [],
        }
        state.sealed_ns = ns
        if state.run_target == ns:
            state.run_target = None
        cut = Cut(index, state.cut.instant)
        state.cuts.append(cut)
        state.records.append(record)
        for p, (_, native) in state.frontiers.items():
            if native == ns:
                state.native_cuts[p] = cut
        self._publish(state, record)

    def _check_microstep(self, instant: Instant) -> None:
        if instant.microstep > self.max_microsteps:
            raise MicrostepLimitExceeded(
                "same-time work exhausted global bound",
                instant=instant,
                trace=self._trace,
            )

    def run_until(self, ns: int) -> StateView:
        """Settle an exact limit, waiting only under a declared live policy."""
        with self._ingress_condition:
            return self._run_until(ns)

    def _run_until(self, ns: int) -> StateView:
        self._guard()
        if (
            type(ns) is not int
            or self._store.sealed_ns is None
            or ns < self._store.sealed_ns
        ):
            raise KernelError(
                "RUN_BACKWARD", "run limit must be an integer at/after seal"
            )
        if ns == self._store.sealed_ns:
            return self.view()
        self._record_control("run_limit", {"limit_ns": ns})
        try:
            while self._store.sealed_ns < ns:
                horizons = self._horizons()
                current = self._store.sealed_ns
                boundaries = [ns]
                for h in horizons.values():
                    boundaries.extend(
                        v
                        for v in (h.next_wakeup_ns, h.output_lb_ns, h.grant_limit_ns)
                        if v is not None
                    )
                queued = self._store.work.earliest()
                if queued is not None:
                    boundaries.append(queued.ns)
                boundaries.extend(
                    data["boundary"] for data in self._store.pending_ingress.values()
                )
                timer_due = self._store.timer_queue.earliest()
                if timer_due is not None:
                    boundaries.append(timer_due.ns)
                to = min(boundaries)
                if to <= current:
                    raise SynchronizationDeadlock("no safe physical progress")
                pre_wait_cut = self._store.cut
                self._wait_watermark(to)
                self._pace_to(to)
                if pre_wait_cut != self._store.cut:
                    continue
                self._wave(
                    tuple(self._store.partitions), Instant(to), "advance", horizons
                )
                if any(
                    d["boundary"] <= to for d in self._store.pending_ingress.values()
                ) or (timer_due is not None and timer_due <= Instant(to)):
                    candidate = self._candidate(Instant(to))
                    record = boundary_control(candidate)
                    self._publish(candidate.state, record)
                self._settle(to)
        except Exception as exc:
            if not self._store.faulted:
                self._fault(exc)
            raise
        return self.view()

    def _record_control(self, kind: str, fields: dict[str, Any]) -> None:
        state = self._store.clone()
        record = {
            "type": kind,
            "index": state.cut.index + 1,
            "instant": encode(state.cut.instant),
            "items": [],
            **fields,
        }
        state.records.append(record)
        state.cuts.append(Cut(record["index"], state.cut.instant))
        if kind == "run_limit":
            state.run_target = fields["limit_ns"]
        elif kind == "watermark":
            state.watermark_ns = fields["watermark_ns"]
        self._publish(state, record)

    def view(self, cut: Cut | None = None) -> StateView:
        """Read the current or an issued historical prefix, including faulted runs."""
        if not self._bound:
            raise KernelError("RUN_STATE", "kernel is not bound")
        return StateView(self._store, cut)

    def action(self, command_id: str) -> ActionState:
        """Read pending submission or receipt-derived action status."""
        return self.view().action(command_id)

    @property
    def records(self) -> tuple[dict[str, Any], ...]:
        """Detached diagnostic record trees; mutating them cannot alter kernel state."""
        import copy

        return tuple(copy.deepcopy(record) for record in self._store.records)

    def close(self) -> None:
        """Close engines idempotently and report cleanup errors."""
        if self._closed:
            return
        with self._ingress_condition:
            self._closed = True
            self._ingress_condition.notify_all()
        errors = []
        for eid in sorted(self._engines):
            try:
                self._engines[eid].close()
            except Exception as exc:
                errors.append(exc)
        self.journal.close()
        if errors:
            raise KernelError("CLOSE_FAILED", "engine cleanup failed") from errors[0]

    @classmethod
    def _from_header(cls, header: dict[str, Any]) -> Kernel:
        from .replay import from_header

        return from_header(header)

    def _replay_record(self, record: dict[str, Any]) -> None:
        from .replay import replay_record

        replay_record(self, record)
