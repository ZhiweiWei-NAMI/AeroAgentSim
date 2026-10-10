"""S01.03 lossless structural text: JSONL round-trip and compact-table encoding.

Two reversible encodings over the same canonical records, plus graph/typed
dictionary views for downstream consumers:

- ``encode_jsonl`` / ``decode_jsonl``: canonical record -> one JSON object per
  line.  Lossless by construction (JSON round-trip of the exact record).
- ``encode_compact`` / ``decode_compact``: per-record-kind columnar tables with
  per-column type tags and explicit non-present kind markers.  Values are
  stored as their exact source JSON text; no rounding or float re-formatting.
  Field order inside records is normalized (sorted keys) so permuting
  entity/field order decodes to identical records; list order is preserved and
  is semantically significant (vector axes, bindings order in source).

Lossless definition here: decode(encode(x)) == x for every canonical record,
compared with exact equality on ints, strings, bools and the *binary64* float
values parsed from source text; no epsilon is introduced.  Tolerance therefore
comes from the original data's JSON precision, not from an arbitrary setting.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from p01v4.contracts.schema import VALUE_KINDS, SchemaError

# Column layout per record kind is uniform (see _COMPACT_COLUMNS below).

_NON_PRESENT_PREFIX = "!"  # marker columns encode kind as "!missing" etc.


def canonicalize(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the canonical in-memory form with sorted field keys.

    Validation is NOT re-run here; callers must import through
    p01v4.data.import_episode (which validates) or call
    p01v4.contracts.schema.validate_record themselves.
    """
    out = {
        "record_kind": record["record_kind"],
        "id": record["id"],
        "episode_id": record.get("episode_id"),
        "tick": record.get("tick"),
        "source": record.get("source"),
        "entity_id": record.get("entity_id"),
        "fields": {k: record["fields"][k] for k in sorted(record["fields"])},
    }
    return {k: v for k, v in out.items() if v is not None or k in ("tick", "episode_id")}


# ------------------------------ JSONL ------------------------------------

def encode_jsonl(records: Iterable[Mapping[str, Any]]) -> str:
    lines = []
    for rec in records:
        lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")))
    return "\n".join(lines) + ("\n" if lines else "")


def decode_jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line]


# --------------------------- compact table -------------------------------

_COMPACT_COLUMNS = ["id", "episode_id", "tick", "entity_id", "source", "field_names", "field_cells"]


def _encode_cell(value: Any) -> Any:
    """Cell -> compact cell.  Frame-tagged vectors become {f, v} with exact
    float passthrough; non-present kinds become '!kind' strings."""
    if isinstance(value, Mapping) and "kind" in value and "value" not in value:
        return _NON_PRESENT_PREFIX + value["kind"]
    if isinstance(value, Mapping) and "frame" in value and "value" in value:
        return {"f": value["frame"], "v": value["value"]}
    return value


def _decode_cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_NON_PRESENT_PREFIX):
        kind = value[len(_NON_PRESENT_PREFIX):]
        if kind not in VALUE_KINDS:
            raise SchemaError(f"compact cell: unknown non-present kind '{kind}'")
        return {"kind": kind}
    if isinstance(value, Mapping) and set(value) == {"f", "v"}:
        return {"frame": value["f"], "value": value["v"]}
    return value


def encode_compact(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Records -> one columnar table per record kind.

    Repeated strings and structures (source provenance dicts, field-name
    lists) are interned into a per-table ``pool`` of unique values; rows hold
    pool indices.  This is a standard columnar dictionary encoding and stays
    exactly lossless: decode resolves every index back to its value.
    """
    tables: dict[str, dict[str, Any]] = {}
    for rec in records:
        kind = rec["record_kind"]
        table = tables.setdefault(kind, {
            "columns": list(_COMPACT_COLUMNS), "pool": [], "_index": {}, "rows": []})
        pool = table["pool"]
        index = table["_index"]

        def ref(obj: Any) -> int:
            key = json.dumps(obj, ensure_ascii=False, sort_keys=True)
            i = index.get(key)
            if i is None:
                i = len(pool)
                index[key] = i
                pool.append(obj)
            return i

        names = sorted(rec["fields"])
        table["rows"].append([
            rec["id"],
            ref(rec.get("episode_id")) if rec.get("episode_id") is not None else None,
            rec.get("tick"),
            ref(rec.get("entity_id")) if rec.get("entity_id") is not None else None,
            ref(rec.get("source")) if rec.get("source") is not None else None,
            ref(names),
            [_encode_cell(rec["fields"][n]) for n in names],
        ])
    for table in tables.values():
        table.pop("_index")
    return tables


def decode_compact(tables: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for kind, table in tables.items():
        cols = table["columns"]
        if cols != _COMPACT_COLUMNS:
            raise SchemaError(f"compact table '{kind}': unexpected column layout {cols}")
        pool = table["pool"]
        for row in table["rows"]:
            if len(row) != len(cols):
                raise SchemaError(f"compact table '{kind}': ragged row width")
            cid, episode_ref, tick, entity_ref, source_ref, names_ref, cells = row
            if not isinstance(names_ref, int) or names_ref >= len(pool):
                raise SchemaError(f"compact table '{kind}': bad field_names pool ref")
            names = pool[names_ref]
            if len(names) != len(cells):
                raise SchemaError(f"compact table '{kind}': names/cells width mismatch")
            rec: dict[str, Any] = {
                "record_kind": kind,
                "id": cid,
                "episode_id": pool[episode_ref] if episode_ref is not None else None,
                "tick": tick,
                "fields": {n: _decode_cell(c) for n, c in zip(names, cells)},
            }
            if source_ref is not None:
                rec["source"] = pool[source_ref]
            if entity_ref is not None:
                rec["entity_id"] = pool[entity_ref]
            records.append(rec)
    return records


# --------------------------- typed / graph views -------------------------

def to_typed_input(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Typed dictionaries required by graph/typed consumers, from the same
    canonical records: entities, per-tick entity states, edges, events."""
    entities: dict[str, dict[str, Any]] = {}
    states: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for rec in records:
        kind = rec["record_kind"]
        if kind == "entity":
            entities[rec["id"]] = {
                "entity_id": rec["fields"]["roster.entity_id"],
                "entity_category": rec["fields"].get("roster.entity_category"),
                "entity_kind": rec["fields"].get("roster.entity_kind"),
                "observer_role": rec["fields"].get("roster.observer_role"),
            }
        elif kind == "frame":
            states.append({
                "tick": rec["tick"],
                "entity_id": rec.get("entity_id"),
                "position_enu_m": rec["fields"].get("pose.position_enu_m"),
                "velocity_enu_mps": rec["fields"].get("pose.velocity_enu_mps"),
                "yaw_deg": rec["fields"].get("pose.yaw_deg"),
                "speed_mps": rec["fields"].get("motion.speed_mps"),
                "visibility_state": rec["fields"].get("annotations.visibility_state"),
                "source": rec.get("source"),
            })
        elif kind == "edge":
            edges.append({
                "tick": rec["tick"],
                "predicate_id": rec["fields"]["relation.predicate_id"],
                "tuple_id": rec["fields"]["relation.tuple_id"],
                "value": rec["fields"]["relation.value"],
                "bindings": rec["fields"]["relation.bindings"],
                "source": rec.get("source"),
            })
        elif kind == "event":
            events.append({
                "event_id": rec["fields"]["event.event_id"],
                "family": rec["fields"]["event.event_family_id"],
                "detection_tick": rec["fields"]["event.detection_tick"],
                "end_tick": rec["fields"]["event.end_tick"],
                "bindings": rec["fields"]["event.bindings"],
                "source": rec.get("source"),
            })
    return {"entities": entities, "states": states, "edges": edges, "events": events}


# ----------------------------- comparison --------------------------------

def records_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Exact equality of canonical records (no epsilon)."""
    return a == b


def semantic_equivalent(a: Iterable[Mapping[str, Any]],
                        b: Iterable[Mapping[str, Any]]) -> bool:
    """Order-independent equivalence: compare as multisets of canonical JSON."""
    def key(rec: Mapping[str, Any]) -> str:
        return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sorted(map(key, a)) == sorted(map(key, b))
