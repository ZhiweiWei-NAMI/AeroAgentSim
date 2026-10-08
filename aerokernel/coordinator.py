"""Serial central barrier, private commits and read-only deterministic replay."""

from __future__ import annotations

import platform
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
from .journal import Journal
from .messages import Actions, ActionState, CancelRequestResult, CommandRequest
from .registry import MemoryRegistry, Registry
from .rng import RNGStreams
from .scheduling import build_wave, ready_partitions, wave_intents
from .state import StateView, Store
from .time import ClockMapping, Cut, Instant
from .transactions import Candidate
from .values import FrozenValue, ResourceBudget, freeze, thaw


class Kernel:
    """General-purpose M1 kernel. Publication is accessible only through execution."""

    def __init__(
        self,
        *,
        root_seed: int = 0,
        journal: Journal | None = None,
        mappings: tuple[ClockMapping, ...] | None = None,
        max_microsteps: int = 1024,
        budget: ResourceBudget | None = None,
        configuration: object = None,
    ) -> None:
        if type(root_seed) is not int:
            raise KernelError("SEED", "root seed must be an integer")
        if type(max_microsteps) is not int or max_microsteps < 1:
            raise KernelError("MICROSTEP_BUDGET", "positive global bound required")
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
        self._store.max_microsteps = self.max_microsteps
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
        header = {
            "type": "header",
            "format": "aerokernel.journal",
            "major": 1,
            "minor": 1,
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
            raise NotImplementedError(f"M2_{feature.upper()}: active capability")
        if p.timing.mode in {"lockstep", "real_time"}:
            raise NotImplementedError(f"M2_{p.timing.mode.upper()}: timing mode")
        if p.timing.mode not in {"des", "fixed_step"}:
            raise KernelError("TIMING_MODE", "unknown timing mode")
        if type(p.timing.latch) is not bool:
            raise KernelError("TIMING_LATCH", "latch policy must be explicit bool")
        if p.timing.mode == "des" and p.timing.step_ns is not None:
            raise KernelError("TIMING_STEP", "DES does not declare a native grid step")
        if type(p.timing.origin_ns) is not int or p.timing.origin_ns != 0:
            raise KernelError(
                "TIMING_ORIGIN", "M1 native grid origin must equal run origin"
            )
        if p.timing.mode == "fixed_step":
            if type(p.timing.step_ns) is not int or p.timing.step_ns <= 0:
                raise KernelError("TIMING_STEP", "positive integer fixed step required")
            if not p.timing.latch:
                raise NotImplementedError(
                    "M2_EXACT_SPLIT: fixed-step nonlatching profile"
                )
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
        return self.view()

    def submit(self, command: CommandRequest) -> str:
        """Reserve later ingress; keyed duplicates retain identity."""
        self._guard()
        state, record, mid = reserve(self._store, command, self.budget)
        if record:
            self._publish(state, record)
        return mid

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
            h = self._by_partition[pid].horizon(pid, self._store.cut)
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
            earliest = self._store.work.earliest()
            due = earliest if earliest is not None and earliest.ns <= ns else None
            first_timer = self._store.timer_queue.earliest()
            timers = (
                [first_timer]
                if first_timer is not None and first_timer.ns <= ns
                else []
            )
            if not due and not timers:
                break
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
        """Settle an exact limit; equal limits preserve the existing cut."""
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
        self._closed = True
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
