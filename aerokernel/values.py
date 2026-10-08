"""Portable typed value trees, deep freezing and pinned stdlib JSON encoding."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, TypeAlias, cast

from .errors import KernelError, ResourceLimit

Value: TypeAlias = None | bool | int | float | str | list["Value"] | dict[str, "Value"]
FrozenValue: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["FrozenValue", ...]
    | Mapping[str, "FrozenValue"]
)


@dataclass(frozen=True)
class ResourceBudget:
    """Explicit limits, without modifying CPython process-global decimal policy."""

    integer_digits: int = 4096
    frame_bytes: int = 8 * 1024 * 1024
    nesting_depth: int = 128

    def __post_init__(self) -> None:
        for value in (self.integer_digits, self.frame_bytes, self.nesting_depth):
            if type(value) is not int or value <= 0:
                raise ResourceLimit("budgets must be positive integers")
        get_limit = getattr(sys, "get_int_max_str_digits", None)
        if get_limit is not None and 0 < get_limit() < self.integer_digits:
            raise ResourceLimit(
                "budget exceeds active CPython decimal conversion limit"
            )


def text(value: str) -> str:
    """Validate UTF-8 without Unicode normalization or surrogate substitution."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise KernelError("UNICODE", "surrogate code point") from exc
    return value


@lru_cache(maxsize=64)
def _digit_bound(digits: int) -> int:
    return cast(int, 10**digits)


@lru_cache(maxsize=4096)
def _string_bytes(value: str) -> int:
    return len(json.dumps(text(value), ensure_ascii=False).encode("utf-8"))


def normalize(
    value: object, budget: ResourceBudget | None = None, *, _check_bytes: bool = True
) -> Value:
    """Deep-copy only the strict wire tree; bool/int/float retain distinct types."""
    budget = ResourceBudget() if budget is None else budget
    active: set[int] = set()
    digit_bound = _digit_bound(budget.integer_digits)
    wire_bytes = 1  # terminal LF

    def account(count: int) -> None:
        nonlocal wire_bytes
        wire_bytes += count
        if wire_bytes > budget.frame_bytes:
            raise ResourceLimit("encoded frame bytes exceeded")

    def walk(v: object, depth: int) -> Value:
        if depth > budget.nesting_depth:
            raise ResourceLimit("nesting depth exceeded")
        if v is None or type(v) is bool:
            if _check_bytes:
                account(4 if v is None or v is True else 5)
            return cast(Value, v)
        if type(v) is int:
            number = v
            # Compare with the exact decimal bound without converting oversized ints.
            if abs(number) >= digit_bound:
                raise ResourceLimit("integer decimal digits exceeded")
            if _check_bytes:
                account(len(str(number)))
            return number
        if type(v) is float:
            f = v
            if not math.isfinite(f):
                raise KernelError("VALUE_FINITE", "nonfinite binary64 value")
            f = 0.0 if f == 0 else f
            if _check_bytes:
                account(len(json.dumps(f)))
            return f
        if type(v) is str:
            if _check_bytes:
                account(_string_bytes(v))
            else:
                text(v)
            return v
        if type(v) not in (list, dict):
            raise KernelError("VALUE_TYPE", "value is not a portable wire tree")
        identity = id(v)
        if identity in active:
            raise KernelError("VALUE_CYCLE", "cyclic value tree")
        active.add(identity)
        if _check_bytes:
            account(2 + max(0, len(cast(Any, v)) - 1))
        try:
            if type(v) is list:
                return [walk(item, depth + 1) for item in cast(list[object], v)]
            result = {}
            for k, item in cast(dict[object, object], v).items():
                if type(k) is not str:
                    raise KernelError("VALUE_KEY", "record keys must be strings")
                if _check_bytes:
                    account(1 + _string_bytes(k))
                else:
                    text(k)
                result[k] = walk(item, depth + 1)
            return result
        finally:
            active.remove(identity)

    return walk(value, 0)


def freeze(value: object, budget: ResourceBudget | None = None) -> FrozenValue:
    """Freeze a copied wire tree; no caller-owned containers remain reachable."""

    return freeze_normalized(normalize(value, budget))


def freeze_normalized(value: Value) -> FrozenValue:
    """Internal conversion after schema/resource validation of a detached tree."""
    if isinstance(value, list):
        return tuple(freeze_normalized(item) for item in value)
    if isinstance(value, dict):
        return MappingProxyType(
            {k: freeze_normalized(item) for k, item in value.items()}
        )
    return value


def thaw(value: FrozenValue) -> Value:
    """Return a detached portable tree from a kernel-frozen snapshot."""
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, Mapping):
        return {k: thaw(item) for k, item in value.items()}
    return value


def typed_equal(a: object, b: object) -> bool:
    """Type-sensitive structural equality with normalized signed float zero."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(
            typed_equal(x, y) for x, y in zip(a, b, strict=True)
        )
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(typed_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and bool(a == b)


def canonical_json(value: object, budget: ResourceBudget | None = None) -> bytes:
    """Canonical sorted UTF-8 JSON plus LF; integers never pass through binary64."""
    budget = ResourceBudget() if budget is None else budget
    tree = normalize(value, budget, _check_bytes=False)
    data = (
        json.dumps(
            tree,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(data) > budget.frame_bytes:
        raise ResourceLimit("encoded frame bytes exceeded")
    return data


def parse_json(data: bytes, budget: ResourceBudget | None = None) -> Value:
    """Strict lossless parser: reject duplicate keys, nonfinite tokens and bad UTF-8."""
    budget = ResourceBudget() if budget is None else budget
    if len(data) > budget.frame_bytes:
        raise ResourceLimit("frame bytes exceeded")
    if data.startswith(b"\xef\xbb\xbf"):
        raise KernelError("JSON_BOM", "UTF-8 BOM is forbidden")

    def parse_integer(token: str) -> int:
        if len(token.lstrip("-")) > budget.integer_digits:
            raise ResourceLimit("integer token digits exceeded")
        return int(token)

    def parse_constant(token: str) -> Any:
        raise KernelError("JSON_FINITE", f"nonfinite token {token}")

    def pairs(items: list[tuple[str, Value]]) -> dict[str, Value]:
        result: dict[str, Value] = {}
        for key, value in items:
            if key in result:
                raise KernelError("JSON_DUPLICATE", "duplicate record key")
            result[key] = value
        return result

    try:
        result = json.loads(
            data.decode("utf-8"),
            parse_int=parse_integer,
            parse_constant=parse_constant,
            object_pairs_hook=pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise KernelError("JSON_INVALID", "invalid UTF-8 JSON frame") from exc
    return normalize(result, budget)
