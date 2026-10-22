"""Internal helpers for the builtin legacy metric adapters (not a plugin API)."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Literal

from aeroagentsim.metrics_api import MetricDescriptor


def identity(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        return f"{ref['id']}@{ref['generation']}"
    raise ValueError("KPI event: expected an entity id or typed reference")


def symbol(value: Any) -> Any:
    return value.get("symbol", value.get("convention")) if hasattr(value, "get") else value


def number(value: Any, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f"KPI {name}: expected finite numeric measurement")
    return float(value)


def descriptor(
    id: str, unit: str, label: str, label_zh: str, aggregation: str,
    direction: Literal["higher", "lower"] | None = None,
    missing: str = "None until the required committed measurement exists.",
) -> MetricDescriptor:
    return MetricDescriptor(id, label, label_zh, unit, direction, aggregation, missing)


EMPTY_COUNT = "Legacy population counter: zero for an empty committed event population, not an inferred outcome."


class LegacyState:
    """JSON checkpoint support for the historical pack-owned accumulator fields."""

    tuple_keys: tuple[str, ...] = ()
    tuple_values: tuple[str, ...] = ()

    def cursor(self) -> dict[str, Any]:
        result = {}
        for name, value in vars(self).items():
            if name.startswith("_"):
                continue
            if name in self.tuple_keys:
                result[name] = [[*key, list(item) if isinstance(item, tuple) else item]
                                for key, item in value.items()]
            elif isinstance(value, set):
                result[name] = sorted(value)
            else:
                result[name] = deepcopy(value)
        return result

    def restore_cursor(self, cursor: dict[str, Any]) -> None:
        for name, original in vars(self).copy().items():
            if name.startswith("_"):
                continue
            value = deepcopy(cursor[name])
            if name in self.tuple_keys:
                value = {tuple(row[:-1]): tuple(row[-1]) if name in self.tuple_values else row[-1]
                         for row in value}
            elif name in self.tuple_values:
                value = {key: tuple(item) for key, item in value.items()}
            elif isinstance(original, set):
                value = set(value)
            elif type(original) is bool and type(value) is not bool:
                raise ValueError(f"KPI cursor: {name} must be a boolean")
            setattr(self, name, value)


class LegacyReducer:
    """Private compatibility views let the host retain old cursor/attribute tests."""

    version = "1"
    state_type: type[LegacyState]

    def initial(self) -> LegacyState:
        return self.state_type()

    def checkpoint(self, state: LegacyState) -> dict[str, Any]:
        return state.cursor()

    def restore(self, checkpoint: dict[str, Any]) -> LegacyState:
        state = self.initial()
        state.restore_cursor(checkpoint)
        return state

    def legacy_cursor(self, state: LegacyState) -> dict[str, Any]:
        return state.cursor()

    def legacy_attributes(self, state: LegacyState) -> dict[str, Any]:
        return {name: value for name, value in vars(state).items() if not name.startswith("_")}
