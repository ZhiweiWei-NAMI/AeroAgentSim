"""Small explicit numerical and state helpers shared by domain packs."""

from __future__ import annotations

import math
from typing import Any

from aerokernel import EntityRef
from aerokernel.sdk import EngineContext
from aerokernel.state import Absent
from aerokernel.values import thaw


def finite(value: Any, name: str, minimum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"{name}: finite number required")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ValueError(f"{name}: must be >= {minimum}")
    return result


def ns(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name}: integer >= {minimum} required")
    return int(value)


def vec(value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("pose: ENU three-vector required")
    return (finite(value[0], "east"), finite(value[1], "north"), finite(value[2], "up"))


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def read(ctx: EngineContext, ref: EntityRef, field: str) -> Any:
    value = ctx.get(ref, field)
    if isinstance(value, Absent):
        raise TypeError(f"pack input {ref.id}.{field}: no valid source observation")
    return thaw(value)
