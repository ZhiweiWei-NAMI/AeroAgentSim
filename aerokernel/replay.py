"""Offline semantic replay uses normalized descriptors and core validation only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .codec import decode_record, encode
from .control import boundary_control, publish_ingress, reserve
from .engine import Batch
from .errors import KernelError
from .journal import Journal
from .messages import Actions
from .scheduling import build_wave, wave_intents
from .state import Store
from .time import Cut, Instant
from .values import ResourceBudget, canonical_json

if TYPE_CHECKING:
    from .coordinator import Kernel


def from_header(header: dict[str, Any]) -> Kernel:
    from .coordinator import Kernel

    cls = Kernel
    from .binding import BindingManifest
    from .registry import MemoryRegistry

    required = {
        "type",
        "format",
        "major",
        "minor",
        "index",
        "instant",
        "time_origin_ns",
        "run_id",
        "epoch",
        "registry",
        "registry_digest",
        "manifest",
        "partitions",
        "mappings",
        "budget",
        "max_microsteps",
        "durability",
        "root_seed",
        "configuration",
        "serializer",
        "rng",
        "resolved_bindings",
        "engine_versions",
    }
    if (
        set(header) != required
        or header["type"] != "header"
        or header["format"] != "aerokernel.journal"
        or type(header["major"]) is not int
        or type(header["minor"]) is not int
        or type(header["index"]) is not int
        or header["time_origin_ns"] != 0
        or type(header["time_origin_ns"]) is not int
        or header["major"] != 1
        or header["minor"] != 0
        or header["index"] != 0
        or decode_record(header["instant"]) != Instant(0)
    ):
        raise KernelError("JOURNAL_HEADER", "unsupported or malformed header")
    registry = MemoryRegistry.from_data(header["registry"])
    if registry.digest != header["registry_digest"]:
        raise KernelError("REGISTRY_DIGEST", "descriptor digest mismatch")
    manifest = BindingManifest.from_data(header["manifest"])
    mappings = decode_record(header["mappings"])
    kernel = cls(
        root_seed=header["root_seed"],
        mappings=mappings,
        max_microsteps=header["max_microsteps"],
        budget=ResourceBudget(**header["budget"]),
        configuration=header["configuration"],
    )
    partitions = {p.id: p for p in decode_record(header["partitions"])}
    for p in partitions.values():
        kernel._validate_partition(p, registry)
    manifest.validate(registry, partitions)
    if (manifest.run_id, manifest.epoch) != (header["run_id"], header["epoch"]):
        raise KernelError("JOURNAL_NAMESPACE", "header/manifest namespace mismatch")
    from .ids import validate_text
    from .rng import derive_seed

    expected_bindings = [
        {
            "ref": encode(ref),
            "controller": manifest.controller(ref, registry, partitions),
            "cleanup_participants": list(manifest.participants(ref, registry)),
            "writers": manifest.resolve(ref, registry, partitions),
        }
        for ref in sorted(manifest.entities, key=lambda r: (r.id, r.generation))
    ]
    if expected_bindings != header["resolved_bindings"]:
        raise KernelError(
            "JOURNAL_BINDINGS", "header authority differs from compiled manifest"
        )
    engine_ids = {p.engine_id for p in partitions.values()}
    if set(header["engine_versions"]) != engine_ids:
        raise KernelError("JOURNAL_ENGINES", "engine version domains differ")
    for version in header["engine_versions"].values():
        validate_text(version)
    expected_seeds = {
        p.id: {
            name: derive_seed(
                header["root_seed"], p.engine_id, p.id, name, kernel.budget
            )
            for name in sorted(p.rng_streams)
        }
        for p in partitions.values()
    }
    if (
        expected_seeds != header["rng"]["seeds"]
        or header["rng"]["implementation"] != "random.Random/MT19937"
    ):
        raise KernelError(
            "JOURNAL_RNG", "recorded RNG streams do not match pinned derivation"
        )
    if header["serializer"]["encoder"] != "stdlib-json/v1":
        raise KernelError("JOURNAL_SERIALIZER", "unsupported serializer profile")
    kernel._store = Store(
        registry, manifest, partitions, Actions(registry, kernel.budget)
    )
    kernel.header = header
    kernel._bound = kernel._started = kernel._read_only = True
    return kernel


def replay_record(self: Kernel, record: dict[str, Any]) -> None:
    """Validate and reproduce each atomic record using the same candidate rules."""

    if self._store.faulted:
        raise KernelError(
            "JOURNAL_AFTER_FAULT", "state records follow a faulted prefix"
        )
    if type(record.get("index")) is not int:
        raise KernelError("JOURNAL_INDEX", "record index must be a strict integer")
    instant = decode_record(record.get("instant"))
    if (
        not isinstance(instant, Instant)
        or record.get("index") != self._store.cut.index + 1
        or instant < self._store.cut.instant
    ):
        raise KernelError("JOURNAL_INDEX", "noncontiguous index/retrograde time")
    kind = record.get("type")
    expected: dict[str, Any]
    if kind == "invocations":
        state, expected, _ = wave_intents(
            self._store, tuple(record["selected"]), instant, record["phase"]
        )
    elif kind == "transaction":
        candidate = self._candidate(instant)
        batches = []
        for entry in record["batches"]:
            batch = decode_record(entry["batch"])
            if not isinstance(batch, Batch):
                raise KernelError("JOURNAL_BATCH", "expected typed Batch")
            batches.append((entry["partition"], batch))
        expected = build_wave(self._store, instant, record["phase"], batches, candidate)
        if record["phase"] == "reset":
            for request in self._store.manifest.bootstrap_commands:
                publish_ingress(candidate, request)
        state = candidate.state
    elif kind == "boundary":
        candidate = self._candidate(instant)
        expected = boundary_control(candidate)
        state = candidate.state
    elif kind == "ingress":
        state, expected, _ = reserve(
            self._store, decode_record(record["request"]), self.budget
        )
    elif kind == "seal":
        state = self._store.clone()
        if any(w.eligible.ns <= instant.ns for w in state.work) or any(
            i["status"] == "pending" for i in state.intents.values()
        ):
            raise KernelError("JOURNAL_SEAL", "seal contains unsettled work")
        if any(
            t["state"] == "pending" and decode_record(t["due"]).ns <= instant.ns
            for t in state.timers.values()
        ) or any(d["boundary"] <= instant.ns for d in state.pending_ingress.values()):
            raise KernelError(
                "JOURNAL_SEAL", "seal leaves due timers/ingress unsettled"
            )
        if any(frontier[0].ns != instant.ns for frontier in state.frontiers.values()):
            raise KernelError(
                "JOURNAL_SEAL", "partition has not reached common boundary"
            )
        if record["physical_ns"] != instant.ns:
            raise KernelError("JOURNAL_SEAL", "seal time mismatch")
        state.sealed_ns = instant.ns
        if state.run_target == instant.ns:
            state.run_target = None
        cut = Cut(record["index"], instant)
        for p, (_, native) in state.frontiers.items():
            if native == instant.ns:
                state.native_cuts[p] = cut
        state.records.append(record)
        state.cuts.append(cut)
        expected = {
            "type": "seal",
            "index": cut.index,
            "instant": encode(instant),
            "physical_ns": instant.ns,
            "items": [],
        }
    elif kind in {"run_limit", "fault", "cancel"}:
        state = self._store.clone()
        if kind == "run_limit":
            if (
                type(record["limit_ns"]) is not int
                or state.sealed_ns is None
                or record["limit_ns"] <= state.sealed_ns
            ):
                raise KernelError("JOURNAL_RUN_LIMIT", "invalid run boundary")
            state.run_target = record["limit_ns"]
            expected = {
                "type": kind,
                "index": record["index"],
                "instant": encode(instant),
                "items": [],
                "limit_ns": record["limit_ns"],
            }
        elif kind == "fault":
            from .ids import validate_text

            validate_text(record["code"])
            pending = {
                ref
                for ref, intent in state.intents.items()
                if intent["status"] == "pending"
            }
            resolved = set()
            for item in record["items"]:
                ref = decode_record(item["intent"])
                if (
                    ref in resolved
                    or ref not in pending
                    or item["kind"] != "invocation_fault"
                    or item["status"] not in {"returned_unpublished", "not_returned"}
                ):
                    raise KernelError("JOURNAL_FAULT", "invalid invocation resolution")
                resolved.add(ref)
                state.intents[ref]["status"] = item["status"]
            if pending != resolved:
                raise KernelError(
                    "JOURNAL_FAULT", "fault must resolve every pending invocation"
                )
            state.faulted = True
            expected = {
                "type": "fault",
                "index": record["index"],
                "instant": encode(instant),
                "code": record["code"],
                "items": record["items"],
            }
        else:
            # Run the ordinary cancel policy without engines or journal effects.
            self._read_only = False
            previous_journal = self.journal
            self.journal = Journal(budget=self.budget)
            try:
                self.cancel(record["command_id"])
                state = self._store
                expected = state.records[-1]
            finally:
                self.journal = previous_journal
                self._read_only = True
            if canonical_json(expected, self.budget) != canonical_json(
                record, self.budget
            ):
                raise KernelError(
                    "JOURNAL_SEMANTICS", "cancel record differs from legal policy"
                )
            return
        state.records.append(record)
        state.cuts.append(Cut(record["index"], instant))
    else:
        raise KernelError("JOURNAL_RECORD", "unknown record type")
    if canonical_json(expected, self.budget) != canonical_json(record, self.budget):
        raise KernelError(
            "JOURNAL_SEMANTICS", "record differs from normalized legal effects"
        )
    state.action_snapshots[state.cut.index] = state.actions.clone()
    self._store = state
