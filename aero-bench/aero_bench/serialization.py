from __future__ import annotations

import json


def canonical_json_bytes(value: object) -> bytes:
    """Serialize JSON data with the single canonical encoding used by AERO-BENCH."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = ["canonical_json_bytes"]
