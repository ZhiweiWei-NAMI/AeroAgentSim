"""Versioned JSONL write-ahead journal and engine-free prefix replay."""

from __future__ import annotations

import io
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast

from .errors import KernelError
from .values import ResourceBudget, canonical_json, parse_json

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
    ) -> None:
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
        self._lines: list[bytes] = []
        self.failed = False

    def append(self, record: dict[str, Any]) -> None:
        """A failed append taints the writer and leaves the live prefix unchanged."""
        if self.failed:
            raise KernelError("JOURNAL_TAINTED", "journal has already failed")
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
        self._lines.append(line)

    @property
    def bytes(self) -> bytes:
        """Acknowledged canonical journal bytes, useful for deterministic tests."""
        return b"".join(self._lines)

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
    records = []
    active_budget = initial_budget
    for line in lines:
        if not line.endswith(b"\n"):
            raise KernelError("JOURNAL_FRAME", "record must be LF-terminated")
        record = parse_json(line, active_budget)
        if not isinstance(record, dict):
            raise KernelError("JOURNAL_RECORD", "record must be an object")
        records.append(record)
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

    raw = data.read_bytes() if isinstance(data, Path) else data
    records, incomplete = read_records(raw, recover_truncated=recover_truncated)
    result = Kernel._from_header(records[0])
    for record in records[1:]:
        result._replay_record(record)
    result.incomplete = (
        incomplete
        or result._store.faulted
        or result._store.run_target is not None
        or result._store.sealed_ns is None
        or any(i["status"] == "pending" for i in result._store.intents.values())
        or bool(result._store.pending_ingress)
    )
    return result


def prefixes(data: bytes) -> Iterable[bytes]:
    """Enumerate every complete LF-terminated prefix, including header-only."""
    offset = 0
    for line in data.splitlines(keepends=True):
        offset += len(line)
        if line.endswith(b"\n"):
            yield data[:offset]
