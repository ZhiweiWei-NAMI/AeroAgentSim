"""Capture source identity is distinct from later storage/acceptance availability."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from aerokernel import Cut, EntityRef, Stamp
from aerokernel.time import cut_data, cut_from
from aerokernel.values import canonical_json

from aeroagentsim.platform.ingress import source_stamp
from aeroagentsim.scenario.loader import contract, integer, text


def digest(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected lowercase SHA-256 digest")
    return value


def content_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stamp_data(stamp: Stamp) -> dict[str, Any]:
    return {
        "clock_id": stamp.clock_id,
        "numerator": stamp.numerator,
        "denominator": stamp.denominator,
        "mapping_id": stamp.mapping_id,
    }


@dataclass(frozen=True)
class CaptureRequest:
    request_id: str
    run_id: str
    actor: EntityRef
    source_cut: Cut
    camera: dict[str, Any]
    asset_digest: str
    acquired: Stamp
    width: int
    height: int
    timeout_s: float

    def __post_init__(self) -> None:
        text(self.request_id, "capture.request_id")
        if text(self.run_id, "capture.run_id") != self.actor.run_id:
            raise ValueError("capture actor belongs to a different run")
        digest(self.asset_digest)
        if not isinstance(self.camera, dict) or not self.camera:
            raise ValueError("capture.camera: explicit camera manifest required")
        canonical_json(self.camera)
        for name, value in (("width", self.width), ("height", self.height)):
            integer(value, "capture." + name, 1)
            if value > 8192:
                raise ValueError("capture dimensions exceed 8192")
        if (
            isinstance(self.timeout_s, bool)
            or not isinstance(self.timeout_s, (int, float))
            or not math.isfinite(self.timeout_s)
            or not 0 < self.timeout_s <= 300
        ):
            raise ValueError("capture.timeout_s: finite (0,300] deadline required")

    @property
    def camera_digest(self) -> str:
        return content_digest(canonical_json(self.camera))

    def to_data(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "run_id": self.run_id,
            "actor": self.actor.to_data(),
            "source_cut": cut_data(self.source_cut),
            "camera": self.camera,
            "asset_digest": self.asset_digest,
            "acquired": stamp_data(self.acquired),
            "width": self.width,
            "height": self.height,
            "timeout_s": self.timeout_s,
        }

    def to_command_data(self) -> dict[str, Any]:
        data = self.to_data()
        data["actor"] = {"$ref": self.actor.to_data()}
        return data

    @classmethod
    def from_command_data(cls, value: Any) -> CaptureRequest:
        if not isinstance(value, dict):
            raise TypeError("capture command requires a record")
        data = dict(value)
        actor = contract(data["actor"], "capture.actor", {"$ref"})
        data["actor"] = actor["$ref"]
        return cls.from_data(data)

    @classmethod
    def from_data(cls, value: Any) -> CaptureRequest:
        fields = {
            "request_id",
            "run_id",
            "actor",
            "source_cut",
            "camera",
            "asset_digest",
            "acquired",
            "width",
            "height",
            "timeout_s",
        }
        data = contract(value, "capture.request", fields)
        return cls(
            data["request_id"],
            data["run_id"],
            EntityRef.from_data(data["actor"]),
            cut_from(data["source_cut"]),
            data["camera"],
            data["asset_digest"],
            source_stamp(data["acquired"], "capture.acquired"),
            data["width"],
            data["height"],
            data["timeout_s"],
        )


@dataclass(frozen=True)
class CaptureReceipt:
    status: Literal["succeeded", "failed", "timeout"]
    request_id: str
    record: dict[str, Any] | None = None
    reason: str | None = None

    def to_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "contract": "aeroagentsim.capture-receipt/v1",
            "status": self.status,
            "request_id": self.request_id,
        }
        if self.record is not None:
            result["record"] = self.record
        if self.reason is not None:
            result["reason"] = self.reason
        return result


def browser_encode(value: Any) -> Any:
    """Use the feed's reserved tags when JSON numbers would lose integer bits."""
    if type(value) is int and abs(value) >= 2**53:
        return {"$integer": str(value)}
    if isinstance(value, dict):
        encoded = {key: browser_encode(child) for key, child in value.items()}
        if len(value) == 1 and set(value) & {"$integer", "$record"}:
            return {"$record": encoded}
        return encoded
    if isinstance(value, (list, tuple)):
        return [browser_encode(child) for child in value]
    return value


def browser_decode(value: Any) -> Any:
    """Decode metadata returned by the viewer without coercing ordinary values."""
    if isinstance(value, dict):
        if set(value) == {"$integer"}:
            scalar = value["$integer"]
            if (
                not isinstance(scalar, str)
                or re.fullmatch(r"-?(0|[1-9][0-9]*)", scalar) is None
            ):
                raise ValueError("invalid browser integer tag")
            return int(scalar)
        if set(value) == {"$record"}:
            record = value["$record"]
            if not isinstance(record, dict):
                raise ValueError("invalid browser record escape")
            return {key: browser_decode(child) for key, child in record.items()}
        return {key: browser_decode(child) for key, child in value.items()}
    if isinstance(value, list):
        return [browser_decode(child) for child in value]
    return value
