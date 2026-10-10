"""P01 v4 canonical record schema (S01.01).

Executable single-writer schema for canonical episode records.  All field
families, units, coordinate frames, value types and missing-value semantics are
declared here and serialized to contracts/schema-v1.json by ``write_schema``.
Import, serialization and windowing code must validate against that contract.

Source of truth for units, coordinate frames, and missing rules is the current
executable declaration set in Dataset/world_model/graph/field_contract.py
(UNIT_DEFINITIONS, FORMAL_COORDINATE_CONTRACT_ID, EXACT_MISSING_RULES); the
sensor-side camera frame name is declared by capture producers
(``sensor_pose_position_body_ned_m`` in aeroworld_lidar_sample_v2).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields as dc_fields, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_ID = "p01v4/canonical-record"
SCHEMA_VERSION = "1.0.0"
CONTRACT_VERSION = 1

# Value kinds: exactly one per stored value.  "present" carries ``value``.
# The four explicit non-present kinds separate the three mandatory cases plus
# the source's own "unknown" sentinel; zero is always a present value.
VALUE_KINDS = ("present", "missing", "inapplicable", "absent", "source_unknown")

# Canonical record kinds and the field families each may carry.  A value whose
# family is not listed for the record kind is a schema error; registration of a
# new family is an explicit contract edit plus fixtures, never automatic.
RECORD_KINDS = ("episode", "entity", "frame", "edge", "event", "observation")

# Family keys of the canonical field dictionary (canonical dotted paths).
STATIC_FIELD_FAMILIES = ("identity", "roster", "pose", "motion", "annotations")
STATE_FIELD_FAMILIES = ("pose", "motion", "annotations")
EDGE_FIELD_FAMILIES = ("relation",)
EVENT_FIELD_FAMILIES = ("event",)
OBSERVATION_FIELD_FAMILIES = ("observation",)

# --------------------------- units -------------------------------------
UNIT_DIMENSIONS: dict[str, str] = {
    "simulation_tick": "simulation_time_step",
    "s": "time",
    "ms": "time",
    "m": "length",
    "m/s": "speed",
    "m/s^2": "acceleration",
    "deg": "plane_angle",
    "deg/simulation_tick": "angular_rate_per_simulation_tick",
    "count": "discrete_count",
    "index": "ordinal_index",
    "ratio": "dimensionless_ratio",
    "percent": "percentage",
    "pixel": "image_axis_pixel_count",
    "Hz": "frequency",
    "Mbps": "data_rate",
    "MB": "data_size",
    "lux": "illuminance",
    "degC": "temperature",
    "ppm": "parts_per_million",
    "bitmask": "discrete_bitmask",
    "cpu_core": "processor_capacity",
    "gpu_unit": "accelerator_capacity",
    "service_slot": "service_capacity",
    "source_frame": "source_frame_count",
    "vehicle": "entity_count",
    "aircraft": "entity_count",
    "none": "dimensionless_non_quantity",
}

# Axis-role tags used to pin vector components and reject cross-frame mixing.
AXIS_ROLES = ("position", "velocity", "acceleration", "direction", "sensor_point")

# Coordinate frames.  The world frame is the producer's declared external
# ENU contract; the camera frame is the lidar producer's body-NED mount frame.
COORDINATE_FRAMES: dict[str, dict[str, Any]] = {
    "world_enu_m": {
        "contract_id": "coord.external_enu_m.v1",
        "axes": ["e", "n", "u"],
        "units": "m",
        "basis": "Dataset/world_model/graph/field_contract.py:FORMAL_COORDINATE_CONTRACT_ID",
    },
    "sensor_body_ned": {
        "contract_id": "sensor_body_ned_m.v1",
        "axes": ["n", "e", "d"],
        "units": "m",
        "basis": "aeroworld_lidar_sample_v2:points_sensor_ned_m",
    },
}

# --------------------------- field families -----------------------------
# Each entry: dtype, unit (None => none), frame (None unless a pose/vector
# family), value kinds allowed beyond "present", and applicability per record
# kind.  ``dtype`` restricts the stored JSON value for present kind.


def _f(dtype: str, unit: str | None, *, frame: str | None = None,
       axis_role: str | None = None, non_present: Sequence[str] = ("missing", "inapplicable"),
       records: Sequence[str] = RECORD_KINDS, enum: Sequence[str] | None = None,
       bounded: tuple[float, float] | None = None, circular: bool = False,
       vector_len: int | None = None, ragged: bool = False,
       description: str = "") -> dict[str, Any]:
    return {
        "dtype": dtype,
        "unit": unit,
        "frame": frame,
        "axis_role": axis_role,
        "non_present_kinds": list(non_present),
        "records": list(records),
        "enum": list(enum) if enum else None,
        "bounded": list(bounded) if bounded else None,
        "circular": circular,
        "vector_length": vector_len,
        "ragged": ragged,
        "description": description,
    }


FIELD_FAMILIES: dict[str, dict[str, Any]] = {
    # -- identity (static per episode) --
    "identity.episode_id": _f("string", None, non_present=(), records=("episode",),
                              description="Source episode identifier"),
    "identity.scenario_id": _f("string", None, non_present=(), records=("episode",),
                               description="Source scenario identifier"),
    "identity.capture_view_id": _f("string", None, non_present=("missing", "absent"),
                                   records=("episode",),
                                   description="UE capture view identifier when a capture view is bound"),
    # -- roster (static per entity) --
    "roster.entity_id": _f("string", None, non_present=(), records=("entity",),
                           description="Roster entity identifier, unique within episode"),
    "roster.entity_category": _f("string", None, non_present=(), records=("entity",),
                                 description="Producer entity category"),
    "roster.entity_kind": _f("string", None, non_present=(), records=("entity",),
                             description="Producer entity kind"),
    "roster.observer_role": _f("string", None, non_present=("missing",), records=("entity",),
                               enum=("observer", "subject", "background"),
                               description="Identity role: observer, subject or background"),
    # -- pose (per tick, world ENU) --
    "pose.position_enu_m": _f("real", "m", frame="world_enu_m", axis_role="position",
                              vector_len=3, non_present=(), records=("frame", "entity"),
                              description="World ENU position at tick, declared contract coord.external_enu_m.v1; mandatory when the entity record exists"),
    "pose.velocity_enu_mps": _f("real", "m/s", frame="world_enu_m", axis_role="velocity",
                                vector_len=3, records=("frame", "entity"),
                                description="World ENU velocity at tick"),
    "pose.yaw_deg": _f("real", "deg", frame="world_enu_m", circular=True,
                       records=("frame", "entity"),
                       description="Yaw angle, circular on (-180, 180]"),
    # -- motion scalar facets (per tick) --
    "motion.speed_mps": _f("real", "m/s", bounded=(0.0, None), records=("frame",),
                           description="Scalar speed facet from the source frame annotations"),
    # -- annotations (per tick, producer facets; enum-typed) --
    "annotations.state": _f("string", None, records=("frame",), ragged=True,
                            description="Producer activity/state facet"),
    "annotations.activity_type": _f("string", None, records=("frame",), ragged=True,
                                    description="Producer activity facet"),
    "annotations.visibility_state": _f("string", None, records=("frame",), ragged=True,
                                       enum=("visible", "occluded", "out_of_view"),
                                       description="Render/visibility state; never consumed as future input"),
    # -- relation (edge records) --
    "relation.predicate_id": _f("string", None, non_present=(), records=("edge",),
                                description="World/semantic predicate identifier of the asserted relation"),
    "relation.tuple_id": _f("string", None, non_present=(), records=("edge",),
                            description="Source predicate tuple identifier"),
    "relation.value": _f("string", None, non_present=(), records=("edge",),
                         enum=("true", "false", "unknown", "out_of_scope"),
                         description="Truth value carried by the relation assertion"),
    "relation.bindings": _f("object", None, non_present=(), records=("edge",), ragged=True,
                            description="Role->entity binding map of the predicate"),
    # -- event (event records) --
    "event.event_id": _f("string", None, non_present=(), records=("event",),
                         description="Source objective event identifier"),
    "event.event_family_id": _f("string", None, non_present=(), records=("event",),
                                description="Producer event family identifier"),
    "event.detection_tick": _f("int", "simulation_tick", non_present=(), records=("event",),
                               bounded=(0, None),
                               description="Tick at which the source detected the event"),
    "event.end_tick": _f("int", "simulation_tick", non_present=("missing", "source_unknown"),
                         records=("event",), bounded=(0, None),
                         description="Tick at which the event ends; missing while ongoing, source_unknown when the source cannot determine it"),
    "event.bindings": _f("object", None, non_present=(), records=("event",), ragged=True,
                         description="Role->entity binding map of the event"),
    # -- observation (per-tick capture-path records; ragged per observer) --
    "observation.path": _f("string", None, non_present=(), records=("observation",),
                           description="Relative path of the source capture file"),
    "observation.modality": _f("string", None, non_present=(), records=("observation",),
                               enum=("rgb", "lidar"),
                               description="Capture modality declared by the producer"),
    "observation.tick": _f("int", "simulation_tick", non_present=(), records=("observation",),
                           bounded=(0, None),
                           description="Simulation tick of the capture frame"),
    "observation.observer_entity_id": _f("string", None, non_present=(), records=("observation",),
                                         description="Observer entity that produced the capture"),
    "observation.record_count": _f("int", "count", non_present=(), records=("observation",),
                                   bounded=(0, None),
                                   description="Point/record count inside the capture file"),
}

# Field paths a value may take inside one record: canonical dotted families.
# Alias, not a copy: runtime registration must be visible immediately.
FIELD_REGISTRY: dict[str, dict[str, Any]] = FIELD_FAMILIES

# --------------------------- validation ---------------------------------

# Record kinds that must carry an integer simulation tick.
TIME_INDEXED_KINDS = ("frame", "edge", "observation", "event")


class SchemaError(ValueError):
    """Raised for any canonical-record schema violation, with field path."""


def _require_frame(family: str, decl: Mapping[str, Any], value: Any, path: str) -> None:
    frame = decl.get("frame")
    got = value.get("frame") if isinstance(value, Mapping) else None
    if frame is not None and got != frame:
        raise SchemaError(
            f"{path}: coordinate frame mismatch: family '{family}' requires frame "
            f"'{frame}' (contract {COORDINATE_FRAMES[frame]['contract_id']}), got '{got}'")


def _check_number(family: str, decl: Mapping[str, Any], v: Any, path: str) -> None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise SchemaError(f"{path}: expected real number, got {type(v).__name__}")
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        raise SchemaError(f"{path}: NaN/Inf not representable in canonical records")
    bounded = decl.get("bounded")
    if bounded is not None:
        lo, hi = bounded
        if lo is not None and f < lo:
            raise SchemaError(f"{path}: value {f} below bounded minimum {lo}")
        if hi is not None and f > hi:
            raise SchemaError(f"{path}: value {f} above bounded maximum {hi}")


def validate_value(family: str, value: Any, path: str,
                   *, record_kind: str | None = None) -> None:
    """Validate one canonical value cell against its family declaration.

    ``value`` is either a plain JSON scalar (implicit present kind, typed by
    family dtype) or a mapping with a ``kind`` key carrying an explicit
    non-present marker.  Raises SchemaError naming ``path`` on violation.
    """
    try:
        decl = FIELD_REGISTRY[family]
    except KeyError:
        raise SchemaError(
            f"{path}: unknown field family '{family}'; register it in "
            "p01v4.contracts.schema.FIELD_FAMILIES with its unit/frame/dtype") from None
    if record_kind is not None and record_kind not in decl["records"]:
        raise SchemaError(
            f"{path}: family '{family}' is not applicable to record kind '{record_kind}'")
    if isinstance(value, Mapping) and "kind" in value and "value" not in value:
        kind = value["kind"]
        if kind not in VALUE_KINDS:
            raise SchemaError(f"{path}: unknown value kind '{kind}'")
        if kind == "present":
            raise SchemaError(f"{path}: present kind must carry a 'value' key")
        if kind not in decl["non_present_kinds"]:
            raise SchemaError(
                f"{path}: kind '{kind}' not declared for family '{family}' "
                f"(allowed: {decl['non_present_kinds']})")
        return
    if isinstance(value, Mapping) and "value" in value:
        # frame-tagged vector cell: {"frame": ..., "value": [...]}
        _require_frame(family, decl, value, path)
        if not isinstance(value["value"], list):
            raise SchemaError(f"{path}: frame-tagged 'value' must be a list")
        return validate_value(family, value["value"], path, record_kind=record_kind)
    # present value, typed by family dtype
    dtype = decl["dtype"]
    if dtype == "real":
        if isinstance(value, list):
            if decl.get("vector_length") is not None and len(value) != decl["vector_length"]:
                raise SchemaError(
                    f"{path}: vector length {len(value)} != declared {decl['vector_length']}")
            if decl.get("frame") is None:
                raise SchemaError(f"{path}: vector family '{family}' must declare a frame")
            for i, c in enumerate(value):
                _check_number(family, decl, c, f"{path}[{i}]")
            return
        _check_number(family, decl, value, path)
        if decl.get("circular"):
            f = float(value)
            if not (-180.0 < f <= 180.0):
                raise SchemaError(f"{path}: circular value {f} outside (-180, 180]")
        if decl.get("enum"):
            raise SchemaError(f"{path}: enum family '{family}' requires a string value")
        return
    if dtype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaError(f"{path}: expected int, got {type(value).__name__}")
        _check_number(family, decl, value, path)
        return
    if dtype == "string":
        if decl.get("enum") and value not in decl["enum"]:
            raise SchemaError(f"{path}: '{value}' not in enum {decl['enum']}")
        if not isinstance(value, str):
            raise SchemaError(f"{path}: expected string, got {type(value).__name__}")
        return
    if dtype == "bool":
        if not isinstance(value, bool):
            raise SchemaError(f"{path}: expected bool, got {type(value).__name__}")
        return
    if dtype == "object":
        if not isinstance(value, Mapping):
            raise SchemaError(f"{path}: expected object, got {type(value).__name__}")
        return
    raise SchemaError(f"{path}: family '{family}' has undeclared dtype '{dtype}'")


def validate_record(record: Mapping[str, Any]) -> dict[str, str]:
    """Validate one canonical record; returns its family->path map.

    Canonical record shape::

        {"record_kind": one of RECORD_KINDS,
         "tick": int|{"kind": ...} for time-indexed kinds,
         "fields": {family: value, ...}}

    Raises SchemaError on any violation; returns the family->path map so
    callers can pin later corrections to exact field paths.
    """
    kind = record.get("record_kind")
    if kind not in RECORD_KINDS:
        raise SchemaError(f"record_kind '{kind}' not in {RECORD_KINDS}")
    if kind in TIME_INDEXED_KINDS and not isinstance(record.get("tick"), int):
        raise SchemaError(f"{kind}: missing or non-integer simulation tick")
    fields = record.get("fields")
    if not isinstance(fields, Mapping):
        raise SchemaError(f"{kind}: 'fields' must be an object")
    paths: dict[str, str] = {}
    for family, value in fields.items():
        path = f"{kind}#{record.get('id', '?')}.{family}"
        validate_value(family, value, path, record_kind=kind)
        paths[family] = path
    return paths


def schema_document() -> dict[str, Any]:
    """Full contract document: value kinds, records, families, units, frames."""
    families = {k: {kk: vv for kk, vv in v.items()} for k, v in FIELD_FAMILIES.items()}
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "value_kinds": list(VALUE_KINDS),
        "zero_semantics": "zero is a present value; it is never a missing marker",
        "missing_semantics": {
            "missing": "source declared the field but produced no value at this tick",
            "inapplicable": "field family does not apply to this record kind or instance",
            "absent": "value not carried by this source record grammar instance",
            "source_unknown": "source's own unknown sentinel (e.g. compute_state deadline)",
        },
        "record_kinds": list(RECORD_KINDS),
        "record_field_families": {
            "episode": ["identity"],
            "entity": ["roster"],
            "frame": ["pose", "motion", "annotations"],
            "edge": ["relation"],
            "event": ["event"],
            "observation": ["observation"],
        },
        "axis_roles": list(AXIS_ROLES),
        "units": dict(UNIT_DIMENSIONS),
        "coordinate_frames": COORDINATE_FRAMES,
        "field_families": families,
    }


def write_schema(path: Path) -> dict[str, Any]:
    """Serialize the executable schema to contracts/schema-v1.json."""
    doc = schema_document()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    return doc
