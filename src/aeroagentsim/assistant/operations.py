"""Generic strict parameter admission and catalogue-backed operation lookup."""

from __future__ import annotations

import copy
import math
import re
from typing import Any


def obj(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def number(minimum: float = 0, maximum: float | None = None, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "minimum": minimum, **({"maximum": maximum} if maximum is not None else {}), **extra}


def enum(values: Any) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


STRING = {"type": "string", "minLength": 1}


class OperationError(ValueError):
    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


def _json_equal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_parameters(value: Any, schema: dict[str, Any], field: str = "params", *, tool_input: bool = False) -> None:
    """Shared schema traversal with the existing tool transport and operation boundaries."""
    for choice in schema.get("allOf", []):
        validate_parameters(value, choice, field, tool_input=tool_input)
    if "not" in schema:
        try:
            validate_parameters(value, schema["not"], field, tool_input=tool_input)
        except OperationError:
            pass
        else:
            raise OperationError(field, "Forbidden parameter shape / 参数结构不允许")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise OperationError(field, f"Expected {schema['const']!r} / 需要指定值")
    kind = schema.get("type")
    if isinstance(kind, (list, tuple)):
        validate_parameters(value, {"anyOf": [{**schema, "type": choice} for choice in kind]}, field, tool_input=tool_input)
        return
    if "anyOf" in schema and "oneOf" in schema:
        validate_parameters(value, {"oneOf": schema["oneOf"]}, field, tool_input=tool_input)
        schema = {key: item for key, item in schema.items() if key != "oneOf"}
    branches = schema.get("anyOf", schema.get("oneOf"))
    if branches is not None:
        if tool_input and isinstance(value, dict):
            tags = {b["properties"]["op"]["enum"][0]: b for b in branches if len(b.get("properties", {}).get("op", {}).get("enum", [])) == 1}
            if tags and "oneOf" not in schema and len(tags) == len(branches):
                if value.get("op") not in tags:
                    raise OperationError(field + ".op", f"Choose from {sorted(tags)} / 请使用列出的操作")
                validate_parameters(value, tags[value["op"]], field, tool_input=True)
                validate_parameters(value, {key: item for key, item in schema.items() if key != "anyOf"}, field, tool_input=True)
                return
        failures, matches = [], 0
        for choice in branches:
            try:
                validate_parameters(value, choice, field, tool_input=tool_input)
                matches += 1
            except OperationError as error:
                failures.append(error)
        if matches:
            if "oneOf" in schema and matches != 1:
                raise OperationError(field, "Exactly one parameter shape required / 参数须恰好符合一种结构")
            remaining = {key: item for key, item in schema.items() if key not in {"anyOf", "oneOf"}}
            validate_parameters(value, remaining, field, tool_input=tool_input)
            return
        # Preserve the field/options from a matching discriminator instead of hiding them.
        if isinstance(value, dict):
            for choice, error in zip(branches, failures):
                if any(key in value and value[key] in spec.get("enum", []) for key, spec in choice.get("properties", {}).items()):
                    raise error
        raise OperationError(field, "Allowed shapes: " + "; ".join(f"{e.field}: {e}" for e in failures))
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
             "number": type(value) in (int, float), "integer": type(value) is int, "boolean": type(value) is bool,
             "null": value is None}
    if kind is not None and not valid.get(kind, False):
        raise OperationError(field, f"Expected {kind} / 需要 {kind}")
    if "enum" in schema and not any(_json_equal(value, option) for option in schema["enum"]):
        raise OperationError(field, f"Choose from {schema['enum']} / 请从这些选项中选择")
    if kind in {"number", "integer"} or kind is None and type(value) in (int, float):
        if not math.isfinite(value):
            raise OperationError(field, "Finite number required / 需要有限数值")
        bounds = [("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b)]
        bounds.append(("exclusiveMinimum", lambda a, b: a <= b))
        bounds.append(("exclusiveMaximum", lambda a, b: a >= b))
        if "multipleOf" in schema and (schema["multipleOf"] <= 0 or not math.isclose(value / schema["multipleOf"], round(value / schema["multipleOf"]), rel_tol=0, abs_tol=1e-9)):
            raise OperationError(field, "Invalid numeric multiple / 数值不符合倍数约束")
        for bound, comparison in bounds:
            if bound in schema and comparison(value, schema[bound]):
                raise OperationError(field, f"Must satisfy {bound} {schema[bound]} / 数值必须满足此边界")
    if kind == "string" or kind is None and isinstance(value, str):
        length = len(value)
        minimum = schema.get("minLength", 0)
        if length < minimum or minimum > 0 and not tool_input and not value.strip():
            raise OperationError(field, "Nonempty text required / 需要非空文本")
        if length > schema.get("maxLength", 16000 if tool_input and kind == "string" else math.inf):
            raise OperationError(field, "Text length outside bounds / 文本长度超出范围")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise OperationError(field, "Text does not match pattern / 文本格式不符合约束")
    if kind == "array" or kind is None and isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000 if tool_input and kind == "array" else math.inf):
            raise OperationError(field, "Invalid array length / 列表长度无效")
        if schema.get("uniqueItems") and any(_json_equal(item, previous) for index, item in enumerate(value) for previous in value[:index]):
            raise OperationError(field, "Duplicate entries / 存在重复项")
        for i, item in enumerate(value):
            validate_parameters(item, schema.get("items", {}), f"{field}[{i}]", tool_input=tool_input)
    if kind == "object" or kind is None and isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0) or len(value) > schema.get("maxProperties", math.inf):
            raise OperationError(field, "Invalid property count / 参数数量无效")
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        extra = value.keys() - properties.keys() if schema.get("additionalProperties") is False else set()
        if tool_input and (missing or extra):
            raise OperationError(field, f"missing {sorted(missing)}, unknown {sorted(extra)}; valid fields: {list(properties)} / 参数缺失或未声明；允许字段见列表")
        if extra:
            raise OperationError(f"{field}.{sorted(extra)[0]}", f"Unknown parameter; valid fields: {list(properties)} / 未知参数；允许字段见列表")
        if missing:
            raise OperationError(f"{field}.{missing[0]}", "Required parameter / 缺少必需参数")
        for key, item in value.items():
            additional = schema.get("additionalProperties", {})
            child = properties.get(key, additional if isinstance(additional, dict) else {})
            validate_parameters(item, child, f"{field}.{key}", tool_input=tool_input)


def validate_operation(operation: dict[str, Any], *, catalogue=None) -> tuple[str, dict[str, Any]]:
    if not isinstance(operation, dict):
        raise OperationError("operation", "Operation must be an object / 操作必须是对象")
    names = [k for k in ("op", "name", "operation") if k in operation]
    if len(names) != 1 or set(operation) - {names[0], "id", "explain", "params"}:
        raise OperationError("operation", "Supply id, op, params and optional explain / 提供 id、op、params 和可选 explain")
    name = operation[names[0]]
    if catalogue is None:
        from .catalogue import OperationCatalogue
        catalogue = OperationCatalogue()
    spec = catalogue.resolve(name)
    for key in ("id", "explain"):
        if key in operation:
            validate_parameters(operation[key], STRING, key)
    params = operation.get("params", {})
    from aeroagentsim.assistant_api import thaw
    validate_parameters(params, thaw(spec.parameters))
    return name, copy.deepcopy(params)


def operation_catalogue(topic: str | None = None) -> list[dict[str, Any]]:
    from .catalogue import OperationCatalogue

    rows = OperationCatalogue().display().values()
    return [row for row in rows if (topic is not None or not row.get("assistant_only")) and (topic is None or row["topic"] == topic)]


def __getattr__(name: str) -> Any:
    # Compatibility imports are derived snapshots, never a second operation table.
    if name == "OPERATION_SPECS":
        from .catalogue import OperationCatalogue
        return OperationCatalogue().display()
    if name in {"ROLES", "PLACE_TYPES"}:
        from .catalogue import OperationCatalogue
        for plugin in OperationCatalogue().plugins:
            if name in plugin.metadata:
                from aeroagentsim.assistant_api import thaw
                return thaw(plugin.metadata[name])
        return {}
    raise AttributeError(name)
