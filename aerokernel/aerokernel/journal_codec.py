"""Decode-only journal admission and lossless independent compressed frames."""

from __future__ import annotations

import base64
import json
import sys
import zlib
from typing import Any, cast

from .errors import KernelError, ResourceLimit
from .values import ResourceBudget, normalize, parse_json

CODEC = "positional-deflate/v1"
LEAN_CODEC = "canonical-deflate/v1"


class JournalHeaderError(KernelError):
    """Malformed journal envelope, before any semantic replay checks."""

    def __init__(self, message: str, *, code: str = "JOURNAL_HEADER") -> None:
        super().__init__(code, message)


class UnsupportedJournalVersion(JournalHeaderError):
    """Unknown wire version, codec or provenance policy; never guessed."""


class JournalFrameError(KernelError):
    """Malformed wire frame; retain the original machine-readable error code."""


class JournalTruncatedError(JournalFrameError):
    """An unterminated final line, distinct from a corrupt complete frame."""

    def __init__(self) -> None:
        super().__init__("JOURNAL_TRUNCATED", "last record lacks LF")


def admit_header(header: dict[str, Any], *, allow_legacy_stub: bool = False) -> None:
    """Admit a journal's wire envelope, not its registry, authority or effects.

    Historical full 1.1–1.3/2.0 and lean 1.4/2.1 retain their explicit policy
    mappings. No semantic version or provenance is inferred from absent fields.
    Decode-only compatibility permits the exact budget-only and minimal 1.2
    diagnostic stubs; replay never accepts these as reconstructable headers.
    """
    from .codec import decode_record
    from .time import Instant

    raw_budget = header.get("budget")
    if not isinstance(raw_budget, dict):
        raise JournalHeaderError("header lacks resource budgets")
    if set(raw_budget) - {"integer_digits", "frame_bytes", "nesting_depth"}:
        raise JournalHeaderError("malformed resource budgets")
    if any(type(value) is not int or value <= 0 for value in raw_budget.values()):
        raise ResourceLimit("budgets must be positive integers")
    if allow_legacy_stub and set(header) == {"budget"}:
        return
    if header.get("type") != "header":
        raise JournalHeaderError("record zero must be a journal header")
    if header.get("format") != "aerokernel.journal":
        raise UnsupportedJournalVersion("unsupported journal format")
    major, minor = header.get("major"), header.get("minor")
    if type(major) is not int or type(minor) is not int:
        raise UnsupportedJournalVersion("unsupported journal version types")
    lean = (major, minor) in {(1, 4), (2, 1)}
    if lean:
        if (
            header.get("provenance") != "lean"
            or type(header.get("policy_version")) is not int
            or header["policy_version"] not in {2, 3}
        ):
            raise UnsupportedJournalVersion("unsupported lean journal policy")
    elif "provenance" in header or "policy_version" in header:
        raise UnsupportedJournalVersion("unsupported full journal policy")
    if major == 1:
        if (
            minor not in {1, 2, 3, 4}
            or "codec" in header
            or "semantic_version" in header
        ):
            raise UnsupportedJournalVersion("unsupported uncompressed journal version")
    elif major == 2:
        if (
            minor not in {0, 1}
            or header.get("codec") != (LEAN_CODEC if lean else CODEC)
            or type(header.get("semantic_version")) is not int
            or header["semantic_version"] not in ({4} if lean else {2, 3})
        ):
            raise UnsupportedJournalVersion("unsupported journal version/codec")
    else:
        raise UnsupportedJournalVersion("unsupported journal major version")
    policy = (
        header["policy_version"]
        if lean
        else header["semantic_version"]
        if major == 2
        else minor
    )
    required = {
        "type", "format", "major", "minor", "index", "instant", "time_origin_ns",
        "run_id", "epoch", "registry", "registry_digest", "manifest", "partitions",
        "mappings", "budget", "max_microsteps", "durability", "root_seed",
        "configuration", "serializer", "rng", "resolved_bindings", "engine_versions",
    }
    if policy in {2, 3}:
        required.update({"ingress_policy", "engine_profiles"})
    if policy == 3:
        required.add("ingress_streams")
    if major == 2:
        required.update({"codec", "semantic_version"})
    if lean:
        required.update({"provenance", "policy_version"})
    stub = (
        allow_legacy_stub
        and (major, minor) == (1, 2)
        and set(header)
        == {"type", "format", "major", "minor", "index", "budget", "instant"}
    )
    if not stub and set(header) != required:
        raise JournalHeaderError("malformed header fields")
    try:
        instant = decode_record(header["instant"])
    except ResourceLimit:
        raise
    except RecursionError as exc:
        raise ResourceLimit("decoder recursion limit exceeded") from exc
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise JournalHeaderError("malformed header instant") from exc
    if (
        type(header["index"]) is not int
        or header["index"] != 0
        or (not stub and type(header["time_origin_ns"]) is not int)
        or (not stub and header["time_origin_ns"] != 0)
        or not isinstance(instant, Instant)
        or instant != Instant(0)
    ):
        raise JournalHeaderError("header must start at index/time zero")


def _parse_wire(data: bytes, budget: ResourceBudget, *, header: bool = False) -> Any:
    """Normalize parser diagnostics without collapsing resource failures."""
    try:
        return parse_json(data, budget)
    except ResourceLimit:
        raise
    except KernelError as exc:
        if isinstance(exc.__cause__, RecursionError):
            raise ResourceLimit("decoder recursion limit exceeded") from exc
        if header:
            raise JournalHeaderError(
                "invalid UTF-8 JSON header", code=exc.code
            ) from exc
        raise JournalFrameError(exc.code, "invalid UTF-8 JSON frame") from exc
    except (ValueError, RecursionError) as exc:
        raise ResourceLimit("decoder conversion limit exceeded") from exc


def decode_journal_line(
    line: bytes, budget: ResourceBudget | None, header: dict[str, Any] | None
) -> tuple[dict[str, Any], ResourceBudget]:
    """Decode/admit one complete line; never verify recorded semantic effects.

    Legacy diagnostic stubs remain supported for decode-only callers. These are
    not reconstructable journal headers and are never admitted by semantic replay.
    Explicit budgets override the declared decoding limits, as before.
    """
    if not line.endswith(b"\n") or b"\r" in line[:-2]:
        raise JournalFrameError("JOURNAL_FRAME", "record must be LF-terminated")
    if header is None and budget is None:
        # Discover the declared policy, then parse again under that policy.
        # Do not modify CPython's process-global decimal or recursion limits.
        digit_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        bootstrap = ResourceBudget(
            digit_limit or max(4096, len(line)), len(line), sys.getrecursionlimit()
        )
        tree = _parse_wire(line, bootstrap, header=True)
        if not isinstance(tree, dict) or not isinstance(tree.get("budget"), dict):
            raise JournalHeaderError("header lacks resource budgets")
        try:
            budget = ResourceBudget(**tree["budget"])
        except (TypeError, KeyError) as exc:
            raise JournalHeaderError("malformed resource budgets") from exc
    assert budget is not None
    frame_budget = budget
    if header is not None and header.get("provenance") == "lean":
        frame_budget = ResourceBudget(
            budget.integer_digits, budget.frame_bytes, budget.nesting_depth * 2 + 2
        )
    record = _parse_wire(line, frame_budget, header=header is None)
    if not isinstance(record, dict):
        if header is None:
            raise JournalHeaderError("header must be an object")
        raise JournalFrameError("JOURNAL_RECORD", "record must be an object")
    if header is None:
        admit_header(record, allow_legacy_stub=True)
    elif header.get("major") == 2:
        record = decode_frame(record, budget, lean=header.get("provenance") == "lean")
    return record, budget


_SHAPES: dict[str, tuple[str, ...]] = {
    "Absent": (),
    "ActionState": (
        "command_id",
        "status",
        "target",
        "source_kind",
        "source",
        "head",
        "history",
        "origin",
    ),
    "Activate": ("partition",),
    "ActivateObligation": (
        "obligation_id",
        "relation_id",
        "direction",
        "endpoint_ref",
        "valid",
        "causes",
    ),
    "AssertEdge": (
        "edge_id",
        "relation_id",
        "source",
        "target",
        "valid",
        "acquired",
        "causes",
    ),
    "Batch": (
        "reached",
        "transaction_base_cut",
        "read_cut",
        "native_reached_ns",
        "native_input_cut",
        "operations",
    ),
    "CancelDecision": (
        "command_id",
        "cancel_message_id",
        "accepted",
        "reason",
        "causes",
    ),
    "CancelEdge": ("edge_id", "causes"),
    "CancelObligation": ("obligation_id", "causes"),
    "CancelRequestResult": ("queued", "message_id", "rejection_ref"),
    "CancelTimer": ("timer_id",),
    "Cardinality": ("minimum", "maximum"),
    "ClockMapping": ("mapping_id", "clock_id", "offset_ns", "p", "q", "rounding"),
    "CloseEdge": ("edge_id", "causes"),
    "CommandRequest": (
        "schema_id",
        "target",
        "at",
        "payload",
        "idempotency_key",
        "ingress_at_ns",
    ),
    "Create": ("ref", "causes"),
    "Cut": ("index", "instant"),
    "Delivery": ("message", "recipient", "instant", "enqueue_ref", "dispatch_ref"),
    "Dependency": (
        "field",
        "type_id",
        "id_pattern",
        "lag_ns",
        "value_only",
        "require_coverage",
    ),
    "Dirty": (
        "key",
        "kind",
        "instant",
        "cause",
        "value_changed",
        "payload",
        "resource_budget",
    ),
    "Edge": (
        "edge_id",
        "relation_id",
        "source",
        "target",
        "valid",
        "acquired",
        "available",
        "producer",
        "version",
    ),
    "Emit": (
        "kind",
        "schema_id",
        "target_or_topic",
        "at",
        "payload",
        "causes",
        "source_stamp",
    ),
    "EndObligation": ("obligation_id", "causes"),
    "EntityRef": ("run_id", "epoch", "id", "generation", "type_id"),
    "Fact": (
        "key",
        "value",
        "acquired",
        "mapped_ns",
        "available",
        "valid",
        "producer",
        "version",
    ),
    "FactWrite": ("key", "value", "acquired", "valid", "causes"),
    "Feedback": ("command_id", "payload", "causes"),
    "FramePrefix": ("context_id", "count", "through"),
    "Horizon": (
        "reached",
        "native_reached_ns",
        "next_wakeup_ns",
        "output_lb_ns",
        "grant_limit_ns",
        "input_cut",
    ),
    "IngressPolicy": (
        "initial_watermark_ns",
        "lateness",
        "timeout_s",
        "speed_ratio",
        "allowed_lateness_ns",
    ),
    "IngressReceipt": (
        "stream_id",
        "disposition",
        "journal_index",
        "mapped_ns",
        "command_id",
        "boundary_ns",
        "activation_ns",
        "delay_ns",
        "code",
    ),
    "IngressStream": ("id", "policy", "mapping_id", "engine_ids"),
    "Instant": ("ns", "microstep"),
    "Interval": ("start", "end"),
    "ItemRef": ("record_index", "item_index"),
    "Life": ("ref", "created", "removed"),
    "LifecycleReady": ("ref",),
    "LocalCause": ("index",),
    "Message": (
        "id",
        "kind",
        "schema_id",
        "source_kind",
        "source",
        "sequence",
        "at",
        "available",
        "payload",
        "causes",
        "source_stamp",
        "origin",
        "resource_budget",
    ),
    "Obligation": (
        "obligation_id",
        "relation_id",
        "direction",
        "endpoint_ref",
        "valid",
        "available",
        "producer",
        "version",
    ),
    "ObligationRule": (
        "partition",
        "relation_id",
        "direction",
        "endpoint_type",
        "id_pattern",
        "priority",
    ),
    "Partition": (
        "id",
        "engine_id",
        "produces",
        "consumes",
        "commands",
        "emits",
        "subscribes",
        "timing",
        "reactive",
        "lifecycle",
        "rng_streams",
        "lifecycle_reads",
        "features",
        "message_targets",
        "message_lag_ns",
        "relation_produces",
        "relation_consumes",
        "obligation_produces",
    ),
    "Receipt": ("command_id", "status", "result", "causes"),
    "RecordedFrame": ("frame", "available", "producer", "version"),
    "RelationDependency": (
        "relation_id",
        "source_type",
        "id_pattern",
        "lag_ns",
        "value_only",
    ),
    "RelationDescriptor": (
        "id",
        "source_type",
        "target_type",
        "targets_per_source",
        "sources_per_target",
        "identity_policy",
        "metadata",
    ),
    "RelationRule": (
        "partition",
        "relation_id",
        "source_type",
        "id_pattern",
        "priority",
    ),
    "Remove": ("ref", "cleanup_refs", "causes"),
    "RequestCancel": ("command_id", "causes"),
    "ResourceBudget": ("integer_digits", "frame_bytes", "nesting_depth"),
    "RetractFact": ("key", "valid", "reason", "causes"),
    "Retraction": ("key", "valid", "reason", "available", "producer", "version"),
    "RunContext": ("root_seed", "run_id", "epoch", "rng", "configuration"),
    "SampleFrame": (
        "context_id",
        "physical_ns",
        "cut",
        "bindings",
        "clocks",
        "result",
        "sources",
        "causes",
    ),
    "SampleSpec": (
        "context_id",
        "partition",
        "upstream",
        "bindings",
        "sources",
        "clocks",
        "triggers",
        "frame_inputs",
        "parameters",
    ),
    "ScheduleTimer": ("timer_id", "due", "payload", "causes"),
    "Stamp": ("clock_id", "numerator", "denominator", "mapping_id"),
    "Timing": ("mode", "step_ns", "origin_ns", "latch", "exact_stop", "certified_hold"),
    "UnsupportedOperation": ("feature",),
    "Work": ("recipient", "eligible", "cause", "message_id", "dirty", "timer_key"),
}


def _pack(value: Any) -> Any:
    if isinstance(value, list):
        return [0, *(_pack(v) for v in value)]
    if not isinstance(value, dict):
        return value
    if (
        set(value) == {"$type", "fields"}
        and type(value["$type"]) is str
        and isinstance(value["fields"], dict)
    ):
        names = _SHAPES.get(value["$type"])
        if names is not None:
            if set(value["fields"]) == set(names):
                return [2, value["$type"], *(_pack(value["fields"][n]) for n in names)]
    return [1, *([k, _pack(value[k])] for k in sorted(value))]


def _unpack(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    if not value or type(value[0]) is not int:
        raise JournalFrameError("JOURNAL_CODEC", "invalid positional container")
    if value[0] == 0:
        return [_unpack(v) for v in value[1:]]
    if value[0] == 1:
        result = {}
        for pair in value[1:]:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or type(pair[0]) is not str
                or pair[0] in result
            ):
                raise JournalFrameError("JOURNAL_CODEC", "invalid positional mapping")
            result[pair[0]] = _unpack(pair[1])
        return result
    if value[0] == 2 and len(value) >= 2 and type(value[1]) is str:
        names = _SHAPES.get(value[1])
        if names is not None:
            if len(value) == len(names) + 2:
                return {
                    "$type": value[1],
                    "fields": {
                        n: _unpack(v) for n, v in zip(names, value[2:], strict=True)
                    },
                }
    raise JournalFrameError("JOURNAL_CODEC", "unknown positional record shape")


def encode_frame(record: dict[str, Any], budget: ResourceBudget) -> bytes:
    """No inter-frame state; each acknowledged line is independently decodable."""
    tree = normalize(record, budget, _check_bytes=False)
    packed = json.dumps(
        _pack(tree), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return compress_frame(record, packed, budget)


def compress_frame(
    record: dict[str, Any], packed: bytes, budget: ResourceBudget, *, level: int = 1
) -> bytes:
    """Compress already encoded bytes; lean omits the positional tree copy."""
    if len(packed) > budget.frame_bytes:
        raise ResourceLimit("expanded codec frame bytes exceeded")
    wire = {
        "type": record["type"],
        "index": record["index"],
        "data": base64.b64encode(zlib.compress(packed, level=level)).decode("ascii"),
    }
    if "phase" in record:
        wire["phase"] = record["phase"]
    line = (json.dumps(wire, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(line) > budget.frame_bytes:
        raise ResourceLimit("encoded codec frame bytes exceeded")
    return line


def decode_frame(
    wire: dict[str, Any], budget: ResourceBudget, *, lean: bool = False
) -> dict[str, Any]:
    """Decode a bounded frame, not a journal header or semantic verification.

    Standalone callers must choose lean/full from an admitted header. Resource
    failures remain ResourceLimit (also KernelError/ValueError compatible).
    """
    try:
        return _decode_frame(wire, budget, lean=lean)
    except ResourceLimit:
        raise
    except JournalFrameError:
        raise
    except KernelError as exc:
        raise JournalFrameError(exc.code, "invalid expanded frame") from exc
    except RecursionError as exc:
        raise ResourceLimit("decoder recursion limit exceeded") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise JournalFrameError("JOURNAL_CODEC", "invalid compressed frame") from exc


def _decode_frame(
    wire: dict[str, Any], budget: ResourceBudget, *, lean: bool
) -> dict[str, Any]:
    if not isinstance(wire, dict):
        raise JournalFrameError("JOURNAL_CODEC", "frame must be an object")
    if set(wire) not in ({"type", "index", "data"}, {"type", "index", "data", "phase"}):
        raise JournalFrameError("JOURNAL_CODEC", "unexpected frame fields")
    if type(wire["data"]) is not str:
        raise JournalFrameError("JOURNAL_CODEC", "compressed data must be base64 text")
    try:
        compressed = base64.b64decode(wire["data"], validate=True)
        inflater = zlib.decompressobj()
        packed = inflater.decompress(compressed, budget.frame_bytes + 1)
        if len(packed) > budget.frame_bytes or inflater.unconsumed_tail:
            raise ResourceLimit("expanded codec frame bytes exceeded")
        if not inflater.eof or inflater.unused_data:
            raise JournalFrameError(
                "JOURNAL_CODEC", "invalid compressed frame boundary"
            )
    except KernelError:
        # KernelError is a ValueError too: do not hide expansion limits or the
        # more precise compressed-boundary diagnostic in the broad catch below.
        raise
    except (ValueError, zlib.error) as exc:
        raise JournalFrameError("JOURNAL_CODEC", "invalid compressed frame") from exc
    # Positional wrapping can add container depth; its resource ceiling is
    # derived from the semantic budget, then the expanded tree is checked again.
    packed_budget = ResourceBudget(
        budget.integer_digits, budget.frame_bytes, budget.nesting_depth * 2 + 2
    )
    if lean:
        record = _parse_wire(packed, packed_budget)
    else:
        record = _unpack(_parse_wire(packed, packed_budget))
        record = normalize(record, budget, _check_bytes=False)
    if (
        not isinstance(record, dict)
        or record.get("type") != wire["type"]
        or type(wire["index"]) is not int
        or type(record.get("index")) is not int
        or record["index"] != wire["index"]
        or record.get("phase") != wire.get("phase")
    ):
        raise JournalFrameError("JOURNAL_CODEC", "wire and expanded coordinates differ")
    return cast(dict[str, Any], record)
