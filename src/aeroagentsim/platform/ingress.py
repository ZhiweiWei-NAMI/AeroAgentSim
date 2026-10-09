"""Strict live wire decoding and serialization of kernel admission receipts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from aerokernel import CommandRequest, IngressReceipt, Instant, Stamp
from aerokernel.errors import KernelError

from aeroagentsim.scenario.loader import ScenarioError, contract, integer, text

__all__ = ["IngressReceipt", "receipt_data", "source_stamp", "submission"]


def receipt_data(receipt: IngressReceipt) -> dict[str, Any]:
    """Admission follows WAL acknowledgment; it says nothing about execution."""
    return {"contract": "aeroagentsim.ingress-receipt/v2", **asdict(receipt)}


def source_stamp(body: Any, path: str) -> Stamp:
    spec = contract(body, path, {"clock_id", "numerator", "denominator", "mapping_id"})
    if type(spec["numerator"]) is not int:
        raise KernelError("TIME_INTEGER", path + ".numerator: integer required")
    return Stamp(
        text(spec["clock_id"], path + ".clock_id"),
        spec["numerator"],
        integer(spec["denominator"], path + ".denominator", 1),
        text(spec["mapping_id"], path + ".mapping_id"),
    )


def submission(body: Any) -> tuple[str | None, CommandRequest, Stamp, str | None]:
    """Keep Q1 engine addressing and allow explicit stream addressing."""
    spec = contract(
        body,
        "ingress",
        {"schema", "target", "at_ns", "payload", "source_stamp"},
        {"engine", "stream_id", "idempotency_key"},
    )
    if "engine" not in spec and "stream_id" not in spec:
        raise ScenarioError("ingress: engine or stream_id required")
    return (
        text(spec["engine"], "ingress.engine") if "engine" in spec else None,
        CommandRequest(
            text(spec["schema"], "ingress.schema"),
            text(spec["target"], "ingress.target"),
            Instant(integer(spec["at_ns"], "ingress.at_ns")),
            spec["payload"],
            text(spec["idempotency_key"], "ingress.idempotency_key")
            if "idempotency_key" in spec
            else None,
        ),
        source_stamp(spec["source_stamp"], "ingress.source_stamp"),
        text(spec["stream_id"], "ingress.stream_id") if "stream_id" in spec else None,
    )
