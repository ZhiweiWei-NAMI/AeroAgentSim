"""Portable generic schemas; packs choose IDs and camera vocabulary explicitly."""

from __future__ import annotations

from typing import Any


def record(members: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "record",
        "members": members,
        "required": list(members),
        "extra": False,
    }


TEXT = {"type": "string"}
INTEGER = {"type": "integer"}
CUT = record(
    {"index": INTEGER, "instant": {"type": "vector", "length": 2, "items": INTEGER}}
)
STAMP = record(
    {"clock_id": TEXT, "numerator": INTEGER, "denominator": INTEGER, "mapping_id": TEXT}
)
REF = {"type": "ref"}
CAMERA = {"type": "record", "members": {}, "required": [], "extra": True}
REQUEST = record(
    {
        "request_id": TEXT,
        "run_id": TEXT,
        "actor": REF,
        "source_cut": CUT,
        "camera": CAMERA,
        "asset_digest": TEXT,
        "acquired": STAMP,
        "width": INTEGER,
        "height": INTEGER,
        "timeout_s": {"type": "number"},
    }
)
STORAGE_RESULT = record({"request_id": TEXT, "digest": TEXT})
UPLOAD_VERIFIED = record(
    {
        "request_id": TEXT,
        "actor": REF,
        "source_cut": CUT,
        "digest": TEXT,
        "byte_count": INTEGER,
        "record": REF,
    }
)
METADATA_SCHEMAS = {
    "request_id": TEXT,
    "actor": REF,
    "source_cut": CUT,
    "acquired": STAMP,
    "camera_digest": TEXT,
    "asset_digest": TEXT,
    "byte_count": INTEGER,
    "digest": TEXT,
    "storage_status": TEXT,
    "renderer_mode": TEXT,
}

CAPTURE_RECEIPT = {
    "type": "record",
    "members": {
        "contract": TEXT,
        "status": TEXT,
        "request_id": TEXT,
        "reason": TEXT,
        "record": {"type": "record", "members": {}, "required": [], "extra": True},
    },
    "required": ["contract", "status", "request_id"],
    "extra": False,
}
UPLOAD_RECEIPT = {
    "type": "record",
    "members": {"status": TEXT, "digest": TEXT, "reason": TEXT},
    "required": ["status"],
    "extra": False,
}
