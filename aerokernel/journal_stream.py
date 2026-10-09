"""Streaming strict JSONL admission with legacy truncated-tail recovery."""

from __future__ import annotations

import io
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, cast

from .errors import KernelError, ResourceLimit
from .values import ResourceBudget, parse_json


class RecordReader(Iterator[dict[str, Any]]):
    """Own one bounded reader; retain only the current decoded record."""

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
                raise KernelError("JOURNAL_HEADER", "missing complete journal header")
            raise StopIteration
        if not line.endswith(b"\n"):
            self.finished = True
            self.incomplete = True
            if not self.recover_truncated:
                raise KernelError("JOURNAL_TRUNCATED", "last record lacks LF")
            if self.header is None:
                raise KernelError("JOURNAL_HEADER", "missing complete journal header")
            raise StopIteration
        if self.header is None and self.budget is None:
            digit_limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
            bootstrap = ResourceBudget(
                digit_limit or max(4096, len(line)), len(line), sys.getrecursionlimit()
            )
            tree = parse_json(line, bootstrap)
            if not isinstance(tree, dict) or not isinstance(tree.get("budget"), dict):
                raise KernelError("JOURNAL_HEADER", "header lacks resource budgets")
            try:
                self.budget = ResourceBudget(**cast(dict[str, Any], tree["budget"]))
            except (TypeError, KeyError) as exc:
                raise KernelError(
                    "JOURNAL_HEADER", "malformed resource budgets"
                ) from exc
        record = parse_json(line, self.budget)
        self.last_line = line
        if not isinstance(record, dict):
            raise KernelError("JOURNAL_RECORD", "record must be an object")
        if self.header is None:
            if record.get("major") == 2:
                from .journal_codec import CODEC

                if (
                    type(record["major"]) is not int
                    or type(record.get("minor")) is not int
                    or record["minor"] != 0
                    or record.get("codec") != CODEC
                    or type(record.get("semantic_version")) is not int
                    or record["semantic_version"] not in {2, 3}
                ):
                    raise KernelError("JOURNAL_HEADER", "unsupported journal 2.0 codec")
            self.header = record
        elif self.header.get("major") == 2:
            from .journal_codec import decode_frame

            assert self.budget is not None
            record = decode_frame(record, self.budget)
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
    """Expand and validate one wire record at a time; no engine calls."""
    with RecordReader(
        data, recover_truncated=recover_truncated, budget=budget
    ) as reader:
        yield from reader
