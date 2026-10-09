"""JSONL WAL and engine-free 1.1/1.2/1.3 replay, including partial live cuts."""

from __future__ import annotations

import builtins
import io
import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from .errors import KernelError
from .journal_stream import iter_records
from .values import ResourceBudget, canonical_json, normalize, parse_json

__all__ = ["Journal", "iter_records", "read_records", "replay", "prefixes"]

if TYPE_CHECKING:
    from .coordinator import Kernel


class Journal:
    """Append and flush before a candidate becomes visible; never retry writes."""

    def __init__(
        self,
        sink: BinaryIO | Path | None = None,
        *,
        durability: str = "flush",
        budget: ResourceBudget | None = None,
        codec: str = "json",
    ) -> None:
        if codec not in {"json", "positional-deflate"}:
            raise KernelError("JOURNAL_CODEC", "unsupported journal codec")
        self.codec = codec
        if durability not in {"flush", "fsync"}:
            raise KernelError("DURABILITY", "unsupported durability policy")
        self.budget = ResourceBudget() if budget is None else budget
        self.durability = durability
        self._owns_sink = isinstance(sink, Path)
        self.sink: BinaryIO = (
            sink.open("xb")
            if isinstance(sink, Path)
            else io.BytesIO()
            if sink is None
            else sink
        )
        self._path = sink if isinstance(sink, Path) else None
        self._retained: list[bytes] | None = (
            [] if self._path is None and not self.sink.readable() else None
        )
        self.acknowledged_bytes = 0
        self.failed = False

    def append(
        self, record: dict[str, Any], *, trusted_fact_rows: bool = False
    ) -> bytes:
        """A failed append taints the writer and leaves the live prefix unchanged."""
        if self.failed:
            raise KernelError("JOURNAL_TAINTED", "journal has already failed")
        if self.codec == "positional-deflate" and record.get("type") != "header":
            from .journal_codec import encode_frame

            line = encode_frame(record, self.budget)
        elif trusted_fact_rows:
            # Only the coordinator supplies this flag, after fact/schema/value
            # validation. Validate the outer codec/tables once; rows reference
            # those validated identities/stamps and normalized detached values.
            shell = dict(record)
            shell["items"] = [
                None if "$fact" in item or "$retract" in item else item
                for item in record["items"]
            ]
            # Fact references are generated item coordinates, not new payloads.
            # Avoid recursively copying the same one-key wrapper for every fact;
            # check the complete reference shape and its largest integer once.
            batch_shells = []
            reference_batches: list[tuple[int, Any, list[int]]] = []
            largest_reference: int | None = None
            for batch_index, entry in enumerate(record["batches"]):
                batch = entry["batch"]
                operations = batch["fields"]["operations"]
                references: list[Any] = []
                positions: list[int] = []
                for position, operation in enumerate(operations):
                    if (
                        isinstance(operation, dict)
                        and set(operation) == {"$item"}
                        and type(operation["$item"]) is int
                        and operation["$item"] >= 0
                    ):
                        largest_reference = (
                            operation["$item"]
                            if largest_reference is None
                            else max(largest_reference, operation["$item"])
                        )
                        references.append(None)
                        positions.append(position)
                    else:
                        references.append(operation)
                if positions:
                    batch_shells.append(
                        {
                            **entry,
                            "batch": {
                                **batch,
                                "fields": {**batch["fields"], "operations": references},
                            },
                        }
                    )
                    reference_batches.append((batch_index, operations, positions))
                else:
                    batch_shells.append(entry)
            if largest_reference is not None:
                if self.budget.nesting_depth < 7:
                    from .errors import ResourceLimit

                    raise ResourceLimit("nesting depth exceeded")
                normalize(largest_reference, self.budget, _check_bytes=False)
            shell["batches"] = batch_shells
            # Entity identities and field names were already budget-validated in
            # their committed creation/header. Referencing them adds no new value;
            # validate dynamic stamps/intervals here and the complete frame below.
            tables = dict(record["fact_tables"])
            shell["fact_tables"] = {**tables, "entities": [], "fields": []}
            tree = cast(
                dict[str, Any], normalize(shell, self.budget, _check_bytes=False)
            )
            if tables["entities"] and self.budget.nesting_depth < 4:
                from .errors import ResourceLimit

                raise ResourceLimit("nesting depth exceeded")
            for batch_index, operations, positions in reference_batches:
                normalized_operations = tree["batches"][batch_index]["batch"]["fields"][
                    "operations"
                ]
                for position in positions:
                    normalized_operations[position] = operations[position]
            tree["fact_tables"]["entities"] = tables["entities"]
            tree["fact_tables"]["fields"] = tables["fields"]
            for index, item in enumerate(record["items"]):
                if "$fact" in item or "$retract" in item:
                    row = item.get("$fact", item.get("$retract"))
                    if self.budget.nesting_depth < 4:
                        from .errors import ResourceLimit

                        raise ResourceLimit("nesting depth exceeded")
                    if isinstance(row[3], (list, dict)):
                        self._row_depth(row[3], 4)
                    for causes in row[6:8] if "$fact" in item else row[5:7]:
                        if causes:
                            self._row_depth(causes, 4)
                    tree["items"][index] = item
            line = (
                json.dumps(
                    tree,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            if len(line) > self.budget.frame_bytes:
                from .errors import ResourceLimit

                raise ResourceLimit("encoded frame bytes exceeded")
        else:
            line = canonical_json(record, self.budget)
        try:
            written = self.sink.write(line)
            if written != len(line):
                raise OSError("short journal write")
            self.sink.flush()
            if self.durability == "fsync":
                os.fsync(self.sink.fileno())
        except (OSError, ValueError) as exc:
            self.failed = True
            raise KernelError("JOURNAL_IO", "WAL append/flush failed") from exc
        self.acknowledged_bytes += len(line)
        if self._retained is not None:
            self._retained.append(line)
        return line

    def _row_depth(self, value: Any, depth: int) -> None:
        # Scalars in a row were normalized already; only wrapper depth is new.
        if depth > self.budget.nesting_depth:
            from .errors import ResourceLimit

            raise ResourceLimit("nesting depth exceeded")
        if isinstance(value, dict):
            for child in value.values():
                self._row_depth(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                self._row_depth(child, depth + 1)

    @property
    def bytes(self) -> bytes:
        """Acknowledged canonical journal bytes, useful for deterministic tests."""
        if self._retained is not None:
            return b"".join(self._retained)
        if self._path is not None:
            with self._path.open("rb") as source:
                return source.read(self.acknowledged_bytes)
        position = self.sink.tell()
        try:
            self.sink.seek(0)
            return self.sink.read(self.acknowledged_bytes)
        finally:
            self.sink.seek(position)

    @property
    def _lines(self) -> list[builtins.bytes]:
        """Diagnostic compatibility accessor; encoded lines are not retained twice."""
        return self.bytes.splitlines(keepends=True)

    def close(self) -> None:
        """Close only resources opened by this journal."""
        if self._owns_sink:
            self.sink.close()


def read_records(
    data: bytes,
    *,
    recover_truncated: bool = False,
    budget: ResourceBudget | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Never skip a corrupt complete line; recover only an incomplete final line."""
    initial_budget = ResourceBudget() if budget is None else budget
    incomplete = bool(data and not data.endswith(b"\n"))
    if incomplete and not recover_truncated:
        raise KernelError("JOURNAL_TRUNCATED", "last record lacks LF")
    lines = data.splitlines(keepends=True)
    if incomplete:
        lines.pop()
    records: list[dict[str, Any]] = []
    active_budget = initial_budget
    for line in lines:
        if not line.endswith(b"\n"):
            raise KernelError("JOURNAL_FRAME", "record must be LF-terminated")
        if not records and budget is None:
            # Discover only the bootstrap policy; then validate the entire header
            # again under that policy. CPython's active decimal/recursion limits
            # remain untouched, matching supported in-process configurations.
            digit_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
            bootstrap = ResourceBudget(
                digit_limit or max(4096, len(line)), len(line), sys.getrecursionlimit()
            )
            header_tree = parse_json(line, bootstrap)
            if not isinstance(header_tree, dict) or not isinstance(
                header_tree.get("budget"), dict
            ):
                raise KernelError("JOURNAL_HEADER", "header lacks resource budgets")
            try:
                active_budget = ResourceBudget(
                    **cast(dict[str, Any], header_tree["budget"])
                )
            except (TypeError, KeyError) as exc:
                raise KernelError(
                    "JOURNAL_HEADER", "malformed resource budgets"
                ) from exc
        record = parse_json(line, active_budget)
        if not isinstance(record, dict):
            raise KernelError("JOURNAL_RECORD", "record must be an object")
        if records and records[0].get("major") == 2:
            from .journal_codec import decode_frame

            record = decode_frame(record, active_budget)
        records.append(record)
        if len(records) == 1 and record.get("major") == 2:
            from .journal_codec import CODEC

            if record.get("codec") != CODEC:
                raise KernelError("JOURNAL_HEADER", "unsupported journal 2.0 codec")
        if len(records) == 1 and budget is None:
            raw_budget = record.get("budget")
            if not isinstance(raw_budget, dict):
                raise KernelError("JOURNAL_HEADER", "header lacks resource budgets")
            active_budget = ResourceBudget(
                cast(int, raw_budget["integer_digits"]),
                cast(int, raw_budget["frame_bytes"]),
                cast(int, raw_budget["nesting_depth"]),
            )
    if not records:
        raise KernelError("JOURNAL_HEADER", "missing complete journal header")
    return records, incomplete


def replay(data: bytes | Path, *, recover_truncated: bool = False) -> Kernel:
    """Reconstruct a read-only valid prefix without engines, clocks or RNG calls."""
    from .coordinator import Kernel
    from .journal_stream import RecordReader

    with RecordReader(data, recover_truncated=recover_truncated) as reader:
        result = Kernel._from_header(next(reader))
        for record in reader:
            result._replay_record(record)
            if isinstance(data, Path):
                result._store.records.file_tail(
                    data, reader.last_offset, reader.last_line
                )
        incomplete = reader.incomplete
    result.incomplete = (
        incomplete
        or result._store.faulted
        or result._store.run_target is not None
        or result._store.sealed_ns is None
        or any(i["status"] == "pending" for i in result._store.intents.values())
        or bool(result._store.pending_ingress)
        or result._store.pending_wall_clock_hold is not None
    )
    return result


def prefixes(data: bytes) -> Iterable[bytes]:
    """Enumerate every complete LF-terminated prefix, including header-only."""
    offset = 0
    for line in data.splitlines(keepends=True):
        offset += len(line)
        if line.endswith(b"\n"):
            yield data[:offset]
