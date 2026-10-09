"""Offline semantic replay uses normalized descriptors and core validation only."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .codec import decode_record, encode
from .compact import compact_operations, expand_record
from .control import boundary_control, publish_ingress, reserve
from .engine import Batch, Partition
from .errors import KernelError, MicrostepLimitExceeded
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
    if header.get("minor") in {2, 3}:
        required.update({"ingress_policy", "engine_profiles"})
    if header.get("minor") == 3:
        required.add("ingress_streams")
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
        or header["minor"] not in {1, 2, 3}
        or header["index"] != 0
        or decode_record(header["instant"]) != Instant(0)
    ):
        raise KernelError("JOURNAL_HEADER", "unsupported or malformed header")
    if header["minor"] in {2, 3}:
        import hashlib

        from .rpc_transport import (
            MAJOR,
            MINOR,
            PROTOCOL,
            pause_policy_from_data,
            timeout_policy,
        )

        manifest_digest = hashlib.sha256(
            canonical_json(header["manifest"], ResourceBudget(**header["budget"]))[:-1]
        ).hexdigest()
        profiles = header["engine_profiles"]
        if not isinstance(profiles, dict) or set(profiles) - set(
            header["engine_versions"]
        ):
            raise KernelError("JOURNAL_RPC", "unknown engine transport profile")
        for profile in profiles.values():
            if "pause_policy" in profile:
                pause_policy_from_data(profile["pause_policy"])
            if (
                set(profile)
                != {
                    "protocol",
                    "major",
                    "minor",
                    "timeouts",
                    "budget",
                    "contract",
                }
                | ({"pause_policy"} if "pause_policy" in profile else set())
                or type(profile["major"]) is not int
                or type(profile["minor"]) is not int
                or (profile["protocol"], profile["major"], profile["minor"])
                != (
                    PROTOCOL,
                    MAJOR,
                    MINOR,
                )
            ):
                raise KernelError("JOURNAL_RPC", "unsupported pinned RPC profile")
            if (
                timeout_policy(profile["timeouts"]) != profile["timeouts"]
                or decode_record(profile["budget"])
                != ResourceBudget(**header["budget"])
                or profile["contract"]
                != {
                    "registry_digest": header["registry_digest"],
                    "manifest_digest": manifest_digest,
                }
            ):
                raise KernelError("JOURNAL_RPC", "RPC binding/policy mismatch")
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
        ingress_policy=decode_record(header["ingress_policy"])
        if header["minor"] in {2, 3}
        else None,
        ingress_streams=decode_record(header["ingress_streams"])
        if header["minor"] == 3
        else (),
    )
    descriptors = decode_record(header["partitions"])
    if not isinstance(descriptors, tuple) or any(
        not isinstance(p, Partition) for p in descriptors
    ):
        raise KernelError("JOURNAL_PARTITIONS", "expected partition descriptors")
    partitions = {p.id: p for p in descriptors}
    if len(partitions) != len(descriptors):
        raise KernelError("PARTITION_ID", "duplicate header partition ID")
    for p in partitions.values():
        kernel._validate_partition(p, registry)
    manifest.validate(registry, partitions)
    kernel._check_cycles(partitions, registry, manifest)
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
    from .sampling import compile_samples

    compile_samples(kernel._store, kernel._dependency_graph, kernel.mappings)
    kernel._store.max_microsteps = kernel.max_microsteps
    if header["minor"] == 3:
        kernel._initialize_ingress()
    elif kernel.ingress_policy is not None:
        kernel._store.watermark_ns = kernel.ingress_policy.initial_watermark_ns
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
    if instant.microstep > self.max_microsteps:
        raise MicrostepLimitExceeded(
            "record exceeds pinned microstep bound", instant=instant
        )
    kind = record.get("type")
    if self._store.pending_wall_clock_hold is not None and kind not in {
        "wall_clock_hold",
        "fault",
    }:
        raise KernelError("JOURNAL_HOLD", "lease intent needs acknowledgment or fault")
    expected: dict[str, Any]
    if kind == "invocations":
        state, expected, _ = wave_intents(
            self._store, tuple(record["selected"]), instant, record["phase"]
        )
    elif kind == "transaction":
        candidate = self._candidate(instant)
        batches = []
        for entry in expand_record(record)["batches"]:
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
    elif kind in {"live_ingress", "live_ingress_rejection"}:
        from .ingress import reserve_live

        legacy = self.header["minor"] < 3
        stream_id = "default" if legacy else record["stream_id"]
        policy = self._policy(stream_id)
        if decode_record(record["policy"]) != policy:
            raise KernelError("JOURNAL_INGRESS", "live policy differs from the header")
        state, expected, _ = reserve_live(
            self._store,
            decode_record(record["original_request"]),
            decode_record(record["source_stamp"]),
            policy,
            self.mappings,
            self.budget,
            stream=self.ingress_streams.get(stream_id),
            legacy=legacy,
        )
    elif kind == "wall_clock_hold":
        from .pause import validate_hold

        if instant != self._store.cut.instant:
            raise KernelError(
                "JOURNAL_HOLD", "wall-clock lease cannot advance simulated time"
            )
        peers = validate_hold(self, record["duration_s"], record["reason"])
        fields = {
            "duration_s": record["duration_s"],
            "reason": record["reason"],
            "engines": peers,
        }
        status = record["status"]
        if status == "acknowledged":
            if (
                type(record["intent_index"]) is not int
                or record["intent_index"] != self._store.pending_wall_clock_hold
            ):
                raise KernelError(
                    "JOURNAL_HOLD", "matching lease intent index required"
                )
            prior = self._store.records[-1]
            if prior != {
                "type": kind,
                "index": record["intent_index"],
                "instant": encode(instant),
                "items": [],
                **fields,
                "status": "requested",
            }:
                raise KernelError(
                    "JOURNAL_HOLD", "acknowledgment needs matching lease intent"
                )
            fields["intent_index"] = record["intent_index"]
        elif status != "requested" or self._store.pending_wall_clock_hold is not None:
            raise KernelError("JOURNAL_HOLD", "invalid lease state")
        expected = {
            "type": kind,
            "index": record["index"],
            "instant": encode(instant),
            "items": [],
            **fields,
            "status": status,
        }
        state = self._store.clone()
        state.pending_wall_clock_hold = (
            record["index"] if status == "requested" else None
        )
        state.records.append(expected)
        state.cuts.append(Cut(record["index"], instant))
    elif kind in {"watermark", "source_progress"} and self.header["minor"] == 3:
        stream_id = record["stream_id"]
        policy = self._policy(stream_id)
        watermark = record["watermark_ns"]
        state = self._store.clone()
        fields = {"stream_id": stream_id, "watermark_ns": watermark}
        if type(watermark) is not int or self._store.pending_intents:
            raise KernelError("JOURNAL_WATERMARK", "invalid prefix advance")
        current = state.watermarks[stream_id]
        if kind == "source_progress":
            from .time import Stamp

            stamp = decode_record(record["source_stamp"])
            stream = self.ingress_streams.get(stream_id)
            if (
                not isinstance(stamp, Stamp)
                or stamp.mapping_id not in self.mappings
                or (stream is not None and stamp.mapping_id != stream.mapping_id)
            ):
                raise KernelError(
                    "JOURNAL_WATERMARK", "source progress mapping differs"
                )
            progress = self.mappings[stamp.mapping_id].map(stamp)
            bound = (
                0 if policy.allowed_lateness_ns is None else policy.allowed_lateness_ns
            )
            previous = state.source_progress.get(stream_id)
            if (
                watermark != progress - bound
                or watermark < current
                or (previous is not None and progress <= previous)
            ):
                raise KernelError(
                    "JOURNAL_WATERMARK", "invalid source progress assertion"
                )
            fields.update({"source_stamp": encode(stamp), "progress_ns": progress})
            state.source_progress[stream_id] = progress
        elif watermark <= current:
            raise KernelError("JOURNAL_WATERMARK", "nonincreasing closed prefix")
        if instant != state.cut.instant:
            raise KernelError(
                "JOURNAL_WATERMARK", "closure cannot advance simulated time"
            )
        state.watermarks[stream_id] = watermark
        if stream_id == "default" and self.ingress_policy is not None:
            state.watermark_ns = watermark
        expected = {
            "type": kind,
            "index": record["index"],
            "instant": encode(instant),
            "items": [],
            **fields,
        }
        state.records.append(expected)
        state.cuts.append(Cut(record["index"], instant))
    elif kind == "watermark":
        if (
            self.ingress_policy is None
            or self._store.watermark_ns is None
            or type(record["watermark_ns"]) is not int
            or record["watermark_ns"] <= self._store.watermark_ns
            or self._store.pending_intents
        ):
            raise KernelError("JOURNAL_WATERMARK", "invalid closed-prefix advance")
        state = self._store.clone()
        state.watermark_ns = record["watermark_ns"]
        expected = {
            "type": "watermark",
            "index": record["index"],
            "instant": encode(instant),
            "items": [],
            "watermark_ns": state.watermark_ns,
        }
        state.records.append(expected)
        state.cuts.append(Cut(record["index"], instant))
    elif kind == "seal":
        state = self._store.clone()
        if self.header["minor"] == 3 and instant != state.cut.instant:
            raise KernelError("JOURNAL_SEAL", "seal must cite the settled instant")
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
        from .ingress import safe_partitions

        if len(safe_partitions(state, instant.ns)) != len(state.partitions) or (
            state.watermark_ns is not None and state.watermark_ns < instant.ns
        ):
            raise KernelError("JOURNAL_WATERMARK", "seal exceeds the closed prefix")
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
                state.intents[ref] = {**state.intents[ref], "status": item["status"]}
            state.pending_intents.clear()
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
            if "rpc" in record:
                if not isinstance(record["rpc"], dict) or set(record["rpc"]) - {
                    "op",
                    "request_id",
                    "timeout_s",
                    "policy",
                }:
                    raise KernelError("JOURNAL_RPC", "invalid recorded transport fault")
                rpc = record["rpc"]
                if (
                    set(rpc) != {"op", "request_id", "timeout_s", "policy"}
                    or rpc["op"]
                    not in {
                        "hello",
                        "reset",
                        "horizon",
                        "advance",
                        "react",
                        "close",
                        "hold",
                    }
                    or type(rpc["request_id"]) is not int
                    or rpc["request_id"] < 1
                ):
                    raise KernelError(
                        "JOURNAL_RPC", "missing request/policy coordinates"
                    )
                pinned = [
                    {key: value for key, value in profile.items() if key != "contract"}
                    for profile in self.header["engine_profiles"].values()
                ]
                if (
                    rpc["policy"] not in pinned
                    or rpc["timeout_s"]
                    != rpc["policy"]["timeouts"][
                        "horizon" if rpc["op"] == "hold" else rpc["op"]
                    ]
                ):
                    raise KernelError(
                        "JOURNAL_RPC", "fault policy differs from pinned profile"
                    )
                expected["rpc"] = rpc
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
    actual_bytes = canonical_json(record, self.budget)
    historical = expected if "fact_tables" in record else expand_record(expected)
    if canonical_json(historical, self.budget) != actual_bytes:
        # Both issued encodings are lossless and fully reproduced from validated
        # proposals: historical inline operations and standalone item references.
        expected = compact_operations(expected)
        if canonical_json(expected, self.budget) != actual_bytes:
            raise KernelError(
                "JOURNAL_SEMANTICS", "record differs from normalized legal effects"
            )
    state.records.freeze_tail(canonical_json(expected, self.budget))
    if state.actions.states.writes:
        state.action_snapshots[state.cut.index] = state.actions
        state.action_snapshot_indices.append(state.cut.index)
    self._store = state
