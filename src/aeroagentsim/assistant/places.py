"""Named-place search over contributed, host-read catalogue resources."""

from __future__ import annotations

import copy
import math
from typing import Any

from .catalogue import callbacks


def _distance(a: list[float], b: list[float]) -> float:
    return math.dist(a[:2], b[:2])


def place_catalogue() -> list[dict[str, Any]]:
    result = []
    for callback in callbacks("places"):
        result.extend(callback())
    keys = [row["key"] for row in result]
    if len(keys) != len(set(keys)):
        raise ValueError("Conflicting assistant place keys / 助手地点标识冲突")
    return copy.deepcopy(result)


def find_places(query: str | None = None, near: str | list[float] | dict[str, Any] | None = None,
                role: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer / limit 必须为正整数")
    rows = place_catalogue()
    position: list[float] | None = None
    if isinstance(near, str):
        found = next((r for r in rows if r["key"] == near), None)
        if found is None:
            raise ValueError("Resolve entity ids to a position before finding places / 查找前请将实体标识解析为坐标")
        position = found["position"]
    elif near is not None:
        position = near["position"] if isinstance(near, dict) else near
        if not isinstance(position, (list, tuple)) or len(position) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) for v in position):
            raise ValueError("near requires an ENU position / near 需要 ENU 坐标")
    q = (query or "").casefold()
    rows = [r for r in rows if (role is None or role in r["suits"]) and any(q in str(r[k]).casefold() for k in ("key", "label", "label_zh"))]
    if position is not None:
        for row in rows:
            row["distance_m"] = round(_distance(row["position"], position), 3)
        rows.sort(key=lambda r: (r["distance_m"], r["key"]))
    return rows[:limit]
