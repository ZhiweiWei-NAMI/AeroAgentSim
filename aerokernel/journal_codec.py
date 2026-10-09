"""Journal 2.0: lossless positional records in independently compressed frames."""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any, cast

from .errors import KernelError, ResourceLimit
from .values import ResourceBudget, normalize, parse_json

CODEC = "positional-deflate/v1"
LEAN_CODEC = "canonical-deflate/v1"


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
        raise KernelError("JOURNAL_CODEC", "invalid positional container")
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
                raise KernelError("JOURNAL_CODEC", "invalid positional mapping")
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
    raise KernelError("JOURNAL_CODEC", "unknown positional record shape")


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
    """Bound decompression before allocating or interpreting the expanded tree."""
    if set(wire) not in ({"type", "index", "data"}, {"type", "index", "data", "phase"}):
        raise KernelError("JOURNAL_CODEC", "unexpected frame fields")
    if type(wire["data"]) is not str:
        raise KernelError("JOURNAL_CODEC", "compressed data must be base64 text")
    try:
        compressed = base64.b64decode(wire["data"], validate=True)
        inflater = zlib.decompressobj()
        packed = inflater.decompress(compressed, budget.frame_bytes + 1)
        if len(packed) > budget.frame_bytes or inflater.unconsumed_tail:
            raise ResourceLimit("expanded codec frame bytes exceeded")
        if not inflater.eof or inflater.unused_data:
            raise KernelError("JOURNAL_CODEC", "invalid compressed frame boundary")
    except (ValueError, zlib.error) as exc:
        raise KernelError("JOURNAL_CODEC", "invalid compressed frame") from exc
    # Positional wrapping can add container depth; its resource ceiling is
    # derived from the semantic budget, then the expanded tree is checked again.
    packed_budget = ResourceBudget(
        budget.integer_digits, budget.frame_bytes, budget.nesting_depth * 2 + 2
    )
    if lean:
        record = parse_json(packed, packed_budget)
    else:
        record = _unpack(parse_json(packed, packed_budget))
        record = normalize(record, budget, _check_bytes=False)
    if (
        not isinstance(record, dict)
        or record.get("type") != wire["type"]
        or type(wire["index"]) is not int
        or record.get("index") != wire["index"]
        or record.get("phase") != wire.get("phase")
    ):
        raise KernelError("JOURNAL_CODEC", "wire and expanded coordinates differ")
    return cast(dict[str, Any], record)
