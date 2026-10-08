"""Lossless fact rows: proposals, returns and versions reference one payload.

The version's publication, item coordinate and producer are derived from its
transaction/item. Tables belong to one atomic record and need no external codec
state. Diagnostic records expand to the original closed dataclass vocabulary.
"""

from __future__ import annotations

from typing import Any, cast

from .codec import decode_record, encode
from .errors import KernelError
from .ids import EntityRef, ItemRef
from .operations import FactWrite, RetractFact
from .state import Fact, Retraction
from .time import Interval, Stamp
from .values import Value, freeze_normalized, thaw


class FactRows:
    """Intern repeated identity/time metadata once per atomic transaction."""

    def __init__(self) -> None:
        self.tables: dict[str, list[Any]] = {
            name: [] for name in ("entities", "fields", "stamps", "intervals")
        }
        self.indices: dict[str, dict[Any, int]] = {name: {} for name in self.tables}

    def intern(self, table: str, value: Any, encoded: Any) -> int:
        """Return a deterministic first-use table index."""
        index = self.indices[table].get(value)
        if index is None:
            index = len(self.tables[table])
            self.indices[table][value] = index
            self.tables[table].append(encoded)
        return index

    def row(
        self,
        partition: str,
        op: FactWrite | RetractFact,
        fact: Fact | Retraction,
        causes: Any,
    ) -> dict[str, Any]:
        """Store one value/time row shared by returned proposal and version."""
        ref, field = op.key
        entity = self.indices["entities"].get(ref)
        if entity is None:
            entity = self.intern(
                "entities",
                ref,
                [ref.run_id, ref.epoch, ref.id, ref.generation, ref.type_id],
            )
        field_index = self.intern("fields", field, field)
        interval = self.indices["intervals"].get(op.valid)
        if interval is None:
            interval = self.intern("intervals", op.valid, encode(op.valid))
        raw = encode(op.causes) if op.causes else []
        if isinstance(op, FactWrite):
            stamp = self.indices["stamps"].get(op.acquired)
            if stamp is None:
                stamp = self.intern("stamps", op.acquired, encode(op.acquired))
            assert isinstance(fact, Fact)
            value = fact.value
            # Frozen scalar values are already portable and immutable. Only
            # compound values need recursive tuple/mapping conversion.
            payload = (
                value
                if value is None or type(value) in (bool, int, float, str)
                else thaw(value)
            )
            return {
                "$fact": [
                    partition,
                    entity,
                    field_index,
                    payload,
                    stamp,
                    interval,
                    raw,
                    causes,
                    fact.mapped_ns,
                ]
            }
        return {
            "$retract": [
                partition,
                entity,
                field_index,
                op.reason,
                interval,
                raw,
                causes,
            ]
        }


def proposal(record: dict[str, Any], item_index: int) -> FactWrite | RetractFact:
    """Decode a referenced row for shared live/replay proposal validation."""
    try:
        item = record["items"][item_index]
        tables = record["fact_tables"]
        row = item.get("$fact", item.get("$retract"))
        entity = EntityRef(*tables["entities"][row[1]])
        key = (entity, tables["fields"][row[2]])
        if "$fact" in item:
            stamp: Stamp = decode_record(tables["stamps"][row[4]])
            valid: Interval = decode_record(tables["intervals"][row[5]])
            return FactWrite(key, row[3], stamp, valid, decode_record(row[6]))
        valid = decode_record(tables["intervals"][row[4]])
        return RetractFact(key, valid, row[3], decode_record(row[5]))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise KernelError("JOURNAL_FACT_ROW", "malformed fact row reference") from exc


def expand_item(record: dict[str, Any], index: int) -> dict[str, Any]:
    """Expand exactly one fact/retraction; ordinary items require no decoding."""
    item: dict[str, Any] = record["items"][index]
    if "$fact" not in item and "$retract" not in item:
        return item
    at = decode_record(record["instant"])
    op = proposal(record, index)
    row: Any = item.get("$fact", item.get("$retract"))
    version = ItemRef(record["index"], index)
    if isinstance(op, FactWrite):
        fact: Fact | Retraction = Fact(
            op.key,
            freeze_normalized(cast(Value, op.value)),
            op.acquired,
            row[8],
            at,
            op.valid,
            row[0],
            version,
        )
        causes = row[7]
    else:
        fact = Retraction(op.key, op.valid, op.reason, at, row[0], version)
        causes = row[6]
    return {
        "kind": "operation",
        "partition": row[0],
        "proposal": encode(op),
        "version": encode(fact),
        "causes": causes,
    }


def compact_operations(record: dict[str, Any]) -> dict[str, Any]:
    """Reference every returned proposal once using the existing item codec.

    Receipt/event/lifecycle proposals used to be repeated verbatim in batches.
    Empty fact tables allow the same standalone expansion for receipt-only waves.
    """
    if record.get("type") != "transaction":
        return record
    if not any(item.get("kind") == "operation" for item in record["items"]):
        # Fact-only batches already use item references. Leave their dense
        # native publication path intact rather than rebuilding every wrapper.
        return record
    result = dict(record)
    result.setdefault("fact_tables", FactRows().tables)
    batches = []
    for entry in record["batches"]:
        partition = entry["partition"]
        indices = [
            i
            for i, item in enumerate(record["items"])
            if (item.get("kind") == "operation" and item.get("partition") == partition)
            or (
                ("$fact" in item or "$retract" in item)
                and item.get("$fact", item.get("$retract"))[0] == partition
            )
        ]
        batch = entry["batch"]
        batches.append(
            {
                **entry,
                "batch": {
                    **batch,
                    "fields": {
                        **batch["fields"],
                        "operations": [{"$item": i} for i in indices],
                    },
                },
            }
        )
    result["batches"] = batches
    return result


def expand_record(record: dict[str, Any]) -> dict[str, Any]:
    """Expand compact rows into ordinary diagnostic record trees."""
    if "fact_tables" not in record:
        return record
    result = {key: value for key, value in record.items() if key != "fact_tables"}
    items = [expand_item(record, i) for i in range(len(record["items"]))]
    result["items"] = items
    batches = []
    for entry in record["batches"]:
        batch = dict(entry["batch"])
        fields = dict(batch["fields"])
        fields["operations"] = [
            items[op["$item"]]["proposal"]
            if isinstance(op, dict) and "$item" in op
            else op
            for op in fields["operations"]
        ]
        batch["fields"] = fields
        batches.append({"partition": entry["partition"], "batch": batch})
    result["batches"] = batches
    return result
