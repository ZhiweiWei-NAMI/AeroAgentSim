"""Typed live admission receipts derived from acknowledged kernel records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

from aerokernel import CommandRequest, Instant, Journal, Stamp
from aerokernel.codec import decode_record
from aerokernel.errors import KernelError

from aeroagentsim.scenario.loader import contract, integer, text


@dataclass(frozen=True)
class IngressReceipt:
    disposition: Literal["accepted", "delayed", "rejected"]
    journal_index: int
    mapped_ns: int
    command_id: str | None
    boundary_ns: int | None
    activation_ns: int | None
    delay_ns: int | None
    code: str | None

    def to_data(self) -> dict[str, Any]:
        return {"contract": "aeroagentsim.ingress-receipt/v1", **asdict(self)}


class IngressJournal(Journal):
    """Delegate the WAL and observe only successful live reservation appends.

    Receipts cite the original record on keyed duplicate submissions. Reading a
    receipt does not scan/replay the journal or invent an admission decision.
    """

    def __init__(self, journal: Journal) -> None:
        self.delegate = journal
        self.sink = journal.sink
        self.budget = journal.budget
        self.durability = journal.durability
        self.receipts: dict[str, IngressReceipt] = {}
        self.last_rejection: IngressReceipt | None = None

    @property
    def failed(self) -> bool:  # type: ignore[override]
        return self.delegate.failed

    @property
    def acknowledged_bytes(self) -> int:  # type: ignore[override]
        return self.delegate.acknowledged_bytes

    def append(
        self, record: dict[str, Any], *, trusted_fact_rows: bool = False
    ) -> bytes:
        line = self.delegate.append(record, trusted_fact_rows=trusted_fact_rows)
        if record["type"] in {"live_ingress", "live_ingress_rejection"}:
            item = record["items"][0] if record["items"] else None
            request = decode_record(item["request"]) if item is not None else None
            receipt = IngressReceipt(
                cast(Literal["accepted", "delayed", "rejected"], record["disposition"]),
                record["index"],
                record["mapped_ns"],
                item["message_id"] if item is not None else None,
                item["boundary"] if item is not None else None,
                request.at.ns if request is not None else None,
                record["delay_ns"] if item is not None else None,
                record["code"] if item is None else None,
            )
            if receipt.command_id is not None:
                self.receipts[receipt.command_id] = receipt
            else:
                self.last_rejection = receipt
        return line

    @property
    def bytes(self) -> bytes:
        return self.delegate.bytes

    def close(self) -> None:
        self.delegate.close()


def submission(body: Any) -> tuple[str, CommandRequest, Stamp]:
    """Decode the HTTP wire contract without scalar coercion or default data."""
    spec = contract(
        body,
        "ingress",
        {"engine", "schema", "target", "at_ns", "payload", "source_stamp"},
        {"idempotency_key"},
    )
    stamp = contract(
        spec["source_stamp"],
        "ingress.source_stamp",
        {"clock_id", "numerator", "denominator", "mapping_id"},
    )
    if type(stamp["numerator"]) is not int:
        raise KernelError(
            "TIME_INTEGER", "ingress.source_stamp.numerator: integer required"
        )
    return (
        text(spec["engine"], "ingress.engine"),
        CommandRequest(
            text(spec["schema"], "ingress.schema"),
            text(spec["target"], "ingress.target"),
            Instant(integer(spec["at_ns"], "ingress.at_ns")),
            spec["payload"],
            text(spec["idempotency_key"], "ingress.idempotency_key")
            if "idempotency_key" in spec
            else None,
        ),
        Stamp(
            text(stamp["clock_id"], "ingress.source_stamp.clock_id"),
            stamp["numerator"],
            integer(stamp["denominator"], "ingress.source_stamp.denominator", 1),
            text(stamp["mapping_id"], "ingress.source_stamp.mapping_id"),
        ),
    )
