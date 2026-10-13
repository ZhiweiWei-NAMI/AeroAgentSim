"""Recover admission and execution evidence from one acknowledged journal prefix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aerokernel.codec import decode_record
from aerokernel.ingress import receipt_for

from aeroagentsim.platform.ingress import receipt_data

from .projector import instant, lossless
from .storage import RunStorage


def admissions(path: Path) -> list[dict[str, Any]]:
    """Times are recorded simulation instants; target_cut is first receipt cut.

    No host submission timestamp or future journal index exists in the WAL.
    Preserve admission boundaries separately and leave unobserved outcomes open.
    """
    storage = RunStorage(path)
    storage.refresh_committed()
    terminal = storage.metadata()["status"] in {"completed", "stopped", "faulted", "interrupted", "input_timeout"}
    # Freeze even headless readers to the index visible when this query began.
    storage._live_snapshot = True
    end = storage.entries[-1]["index"] + 1 if storage.entries else 0
    rows: dict[str, dict[str, Any]] = {}
    receipts: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    cursor = 1
    while cursor < end:
        records = storage.records(cursor, min(256, end - cursor))
        if not records:
            raise ValueError("Admission journal prefix is missing / 受理日志前缀缺失")
        for record in records:
            if record["type"] in {"live_ingress", "live_ingress_rejection", "ingress"}:
                request = decode_record(record.get("original_request", record.get("request")))
                live = record["type"] != "ingress"
                receipt = receipt_data(receipt_for(record)) if live else {
                    "command_id": record["items"][0]["message_id"],
                    "journal_index": record["index"],
                    "boundary_ns": record["items"][0]["boundary"],
                    "activation_ns": request.at.ns,
                }
                identity = receipt["command_id"] or f"{path.name}:admission:{record['index']}"
                rows[identity] = {
                    **receipt, "id": identity,
                    "submitted_at": instant(decode_record(record["instant"])),
                    "target_cut": None, "kind": request.schema_id,
                    "status": "rejected" if record["type"] == "live_ingress_rejection" else "admitted",
                    "idempotency_key": request.idempotency_key,
                    "requested_at": instant(request.at), "target": request.target,
                }
                if receipt.get("code") is not None:
                    rows[identity]["detail"] = receipt["code"]
            for item in record["items"]:
                receipt = item if item.get("kind") == "receipt" else item.get("receipt")
                if receipt is not None and receipt["command_id"] in rows:
                    receipts.setdefault(receipt["command_id"], []).append((record["index"], receipt))
        cursor = records[-1]["index"] + 1
    for identity, row in rows.items():
        history = receipts.get(identity, [])
        if history:
            cut, outcome = history[-1]
            row["target_cut"] = history[0][0]
            row["execution_cut"] = cut
            row["execution_status"] = outcome["status"]
            status = {"succeeded": "applied", "rejected": "rejected", "failed": "failed", "canceled": "failed"}
            row["status"] = status.get(outcome["status"], row["status"])
            if outcome.get("result") is not None:
                row["execution_result"] = lossless(outcome["result"])
                row["detail"] = json.dumps(row["execution_result"], ensure_ascii=False)
        if terminal and row["status"] == "admitted":
            row["detail"] = "Admitted; no terminal execution receipt observed / 已受理，未观察到执行终态回执"
    return list(rows.values())
