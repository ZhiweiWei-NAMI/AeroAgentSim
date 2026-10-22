"""Streaming decode-only JSONL admission with legacy truncated-tail recovery."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .errors import ResourceLimit
from .journal_codec import (
    JournalHeaderError,
    JournalTruncatedError,
    decode_journal_line,
)
from .values import ResourceBudget


class RecordReader(Iterator[dict[str, Any]]):
    """Decode/admit wire records, not semantic effects; retain only one record.

    This reads disk, not a live acknowledged cut. Use journal.replay for semantic
    verification; neither operation authenticates the source or proves success.
    """

    def __init__(
        self,
        data: bytes | Path,
        *,
        recover_truncated: bool = False,
        budget: ResourceBudget | None = None,
    ) -> None:
        self.stream: BinaryIO = (
            data.open("rb") if isinstance(data, Path) else io.BytesIO(data)
        )
        self.budget = budget
        self.recover_truncated = recover_truncated
        self.incomplete = False
        self.header: dict[str, Any] | None = None
        self.finished = False
        self.last_line = b""
        self.last_offset = 0

    def __iter__(self) -> RecordReader:
        return self

    def __next__(self) -> dict[str, Any]:
        if self.finished:
            raise StopIteration
        # The bootstrap header declares its own frame budget, as in 1.x.
        cap = -1 if self.budget is None else self.budget.frame_bytes + 1
        self.last_offset = self.stream.tell()
        line = self.stream.readline(cap)
        if self.budget is not None and len(line) > self.budget.frame_bytes:
            # Determine whether this is a corrupt complete frame or the final
            # incomplete tail, without allocating the rest of an oversized line.
            while not line.endswith(b"\n"):
                tail = self.stream.readline(self.budget.frame_bytes + 1)
                if not tail:
                    break
                line = tail
            if line.endswith(b"\n"):
                raise ResourceLimit("frame bytes exceeded")
        if not line:
            self.finished = True
            if self.header is None:
                raise JournalHeaderError("missing complete journal header")
            raise StopIteration
        if not line.endswith(b"\n"):
            self.finished = True
            self.incomplete = True
            if not self.recover_truncated:
                raise JournalTruncatedError()
            if self.header is None:
                raise JournalHeaderError("missing complete journal header")
            raise StopIteration
        record, self.budget = decode_journal_line(line, self.budget, self.header)
        self.last_line = line
        if self.header is None:
            self.header = record
        return record

    def close(self) -> None:
        """Release the source, including when a consumer stops before EOF."""
        self.finished = True
        self.stream.close()

    def __enter__(self) -> RecordReader:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def iter_records(
    data: bytes | Path,
    *,
    recover_truncated: bool = False,
    budget: ResourceBudget | None = None,
) -> Iterator[dict[str, Any]]:
    """Decode/admit one wire record at a time, without semantic replay checks."""
    with RecordReader(
        data, recover_truncated=recover_truncated, budget=budget
    ) as reader:
        yield from reader
