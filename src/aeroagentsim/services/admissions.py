"""Recover admission and execution evidence from one acknowledged journal prefix."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.ingress import receipt_for

from aeroagentsim.platform.ingress import receipt_data

from .projector import instant, lossless
from .storage import RunStorage, committed_prefix


class AdmissionCursorError(ValueError):
    """A cursor must refer to this run's acknowledged history."""


class _AdmissionHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.storage = RunStorage(path)
        self.lock = Lock()
        self.scanned = 0
        self.cursor = 0
        self.rows: dict[str, dict[str, Any]] = {}
        self.changed: dict[str, int] = {}

    def refresh(self) -> None:
        # Keep live offsets between reads. Headless journals publish index.json
        # instead; loading its offsets still does not re-decode old WAL records.
        if committed_prefix(self.path) is None:
            self.storage = RunStorage(self.path)
        self.storage.refresh_committed()
        self.storage._live_snapshot = True
        last = self.storage.entries[-1]["index"] if self.storage.entries else 0
        if last < self.scanned:
            raise ValueError("Acknowledged admission history moved backwards / 已确认的受理日志发生回退")
        while self.scanned < last:
            records = self.storage.records(self.scanned + 1, min(256, last - self.scanned))
            if not records:
                raise ValueError("Admission journal prefix is missing / 受理日志前缀缺失")
            for record in records:
                self.apply(record)
                self.scanned = record["index"]
        metadata = self.storage.metadata()
        terminal = metadata["status"] in {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
        # Final metadata may be published just ahead of the live WAL coordinate.
        terminal = terminal and last + 1 >= metadata.get("final_cursor", last + 1)
        # Even sequences are WAL coordinates; the odd sequence records the
        # terminal disclosure at that same coordinate, including after restart.
        self.cursor = last * 2 + int(terminal)
        if terminal:
            detail = "Admitted; no terminal execution receipt observed / 已受理，未观察到执行终态回执"
            for identity, row in self.rows.items():
                if row["status"] == "admitted" and row.get("detail") != detail:
                    row["detail"] = detail
                    self.changed[identity] = self.cursor

    def apply(self, record: dict[str, Any]) -> None:
        if record["type"] in {"live_ingress", "live_ingress_rejection", "ingress"}:
            request = decode_record(record.get("original_request", record.get("request")))
            live = record["type"] != "ingress"
            receipt = receipt_data(receipt_for(record)) if live else {
                "command_id": record["items"][0]["message_id"],
                "journal_index": record["index"],
                "boundary_ns": record["items"][0]["boundary"],
                "activation_ns": request.at.ns,
            }
            identity = receipt["command_id"] or f"{self.path.name}:admission:{record['index']}"
            self.rows[identity] = {
                **receipt, "id": identity,
                "submitted_at": instant(decode_record(record["instant"])),
                "target_cut": None, "kind": request.schema_id,
                "status": "rejected" if record["type"] == "live_ingress_rejection" else "admitted",
                "idempotency_key": request.idempotency_key,
                "requested_at": instant(request.at), "target": request.target,
            }
            self.changed[identity] = record["index"] * 2
            if receipt.get("code") is not None:
                self.rows[identity]["detail"] = receipt["code"]
        for item in record["items"]:
            receipt = item if item.get("kind") == "receipt" else item.get("receipt")
            if receipt is not None and receipt["command_id"] in self.rows:
                identity = receipt["command_id"]
                row = self.rows[identity]
                if row["target_cut"] is None:
                    row["target_cut"] = record["index"]
                row["execution_cut"] = record["index"]
                row["execution_status"] = receipt["status"]
                status = {"succeeded": "applied", "rejected": "rejected", "failed": "failed", "canceled": "failed"}
                row["status"] = status.get(receipt["status"], row["status"])
                if receipt.get("result") is not None:
                    row["execution_result"] = lossless(receipt["result"])
                    row["detail"] = json.dumps(row["execution_result"], ensure_ascii=False)
                self.changed[identity] = record["index"] * 2


@lru_cache(maxsize=32)
def _history(path: Path, journal_identity: tuple[int, int] | None) -> _AdmissionHistory:
    return _AdmissionHistory(path)


_cache_lock = Lock()


def admissions(path: Path, after: int | None = None) -> list[dict[str, Any]] | dict[str, Any]:
    """Recover real admission/execution evidence without rescanning old records.

    Without a cursor, retain the full-history list contract. With ``after``, return
    ``{cursor, receipts}`` containing only new or changed rows. Cursor sequences
    derive from acknowledged WAL coordinates and terminal state, not process ids.
    No host submission timestamp or future journal index is inferred.
    """
    if after is not None and (type(after) is not int or after < 0):
        raise AdmissionCursorError("after must be a nonnegative integer / after 必须为非负整数")
    path = path.resolve()
    journal = path / "journal.jsonl"
    stat = journal.stat() if journal.exists() else None
    with _cache_lock:
        history = _history(path, (stat.st_dev, stat.st_ino) if stat is not None else None)
    with history.lock:
        history.refresh()
        if after is None:
            return deepcopy(list(history.rows.values()))
        if after > history.cursor:
            raise AdmissionCursorError("after is beyond the acknowledged history / after 超出已确认的日志范围")
        return {"cursor": history.cursor, "receipts": deepcopy([
            row for identity, row in history.rows.items() if history.changed[identity] > after
        ])}
