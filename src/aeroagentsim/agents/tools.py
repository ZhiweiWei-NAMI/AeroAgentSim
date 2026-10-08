"""Strict function tools compiled from granted portable registry schemas."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aerokernel import MemoryRegistry
from aerokernel.errors import KernelError
from aerokernel.values import thaw


def strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"nonfinite value: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def validate_strict(schema: Mapping[str, Any], value: Any) -> None:
    """Validate the emitted tool subset before optional-null wire translation."""
    if "anyOf" in schema:
        for branch in schema["anyOf"]:
            try:
                validate_strict(branch, value)
                return
            except ValueError:
                pass
        raise ValueError("value matches no tool schema branch")
    kind = schema["type"]
    valid = {
        "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }[kind]
    if not valid:
        raise ValueError(f"tool schema expects {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("tool value excluded by enum")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError(
                "strict tool requires every declared property and no extras"
            )
        for key, child in value.items():
            validate_strict(schema["properties"][key], child)
    if kind == "array":
        if (
            len(value) < schema.get("minItems", 0)
            or "maxItems" in schema
            and len(value) > schema["maxItems"]
        ):
            raise ValueError("tool array length constraint")
        for child in value:
            validate_strict(schema["items"], child)
    if kind == "string" and (
        len(value) < schema.get("minLength", 0)
        or "maxLength" in schema
        and len(value) > schema["maxLength"]
    ):
        raise ValueError("tool string length constraint")
    if kind in {"integer", "number"} and (
        "minimum" in schema
        and value < schema["minimum"]
        or "maximum" in schema
        and value > schema["maximum"]
    ):
        raise ValueError("tool numeric bound constraint")


def json_schema(schema: Mapping[str, Any], registry: MemoryRegistry) -> dict[str, Any]:
    if "schema_ref" in schema:
        return json_schema(registry.schemas[schema["schema_ref"]], registry)
    kind = schema["type"]
    if kind == "record":
        if schema["extra"]:
            raise ValueError("strict tools require closed record schemas")
        properties = {
            name: json_schema(child, registry)
            for name, child in schema["members"].items()
        }
        for name in set(properties) - set(schema["required"]):
            properties[name] = {"anyOf": [properties[name], {"type": "null"}]}
        result = object_schema(properties)
    elif kind == "ref":
        result = object_schema(
            {
                "$ref": object_schema(
                    {
                        "run_id": {"type": "string"},
                        "epoch": {"type": "string"},
                        "id": {"type": "string"},
                        "generation": {"type": "integer", "minimum": 0},
                        "type_id": {"type": "string"},
                    }
                )
            }
        )
    elif kind == "union":
        result = {
            "anyOf": [
                object_schema(
                    {
                        "$case": {"type": "string", "enum": [name]},
                        "value": json_schema(child, registry),
                    }
                )
                for name, child in schema["cases"].items()
            ]
        }
    elif kind in {"array", "vector", "matrix"}:
        child = json_schema(schema["items"], registry)
        if kind == "matrix":
            child = {
                "type": "array",
                "items": child,
                "minItems": schema["columns"],
                "maxItems": schema["columns"],
            }
        result = {"type": "array", "items": child}
        if kind in {"vector", "matrix"}:
            length = schema["length" if kind == "vector" else "rows"]
            result.update(minItems=length, maxItems=length)
        else:
            for source, target in (
                ("min_length", "minItems"),
                ("max_length", "maxItems"),
            ):
                if source in schema:
                    result[target] = schema[source]
    else:
        result = {"type": kind}
        for source, target in (
            ("minimum", "minimum"),
            ("maximum", "maximum"),
            ("min_length", "minLength"),
            ("max_length", "maxLength"),
        ):
            if source in schema:
                result[target] = schema[source]
    if "enum" in schema:
        result["enum"] = thaw(schema["enum"])
    if schema.get("nullable"):
        result = {"anyOf": [result, {"type": "null"}]}
    return result


def portable_payload(
    schema: Mapping[str, Any], value: Any, registry: MemoryRegistry
) -> Any:
    """Remove only explicit optional-null placeholders; preserve native numeric types."""
    if "schema_ref" in schema:
        return portable_payload(registry.schemas[schema["schema_ref"]], value, registry)
    if value is None:
        return None
    kind = schema["type"]
    if kind == "number" and type(value) in (int, float):
        return float(
            value
        )  # JSON Schema numbers include integer tokens; kernel floats are strict.
    if kind == "record" and isinstance(value, dict):
        return {
            name: portable_payload(schema["members"][name], child, registry)
            if name in schema["members"]
            else child
            for name, child in value.items()
            if not (
                name in schema["members"]
                and name not in schema["required"]
                and child is None
                and not schema["members"][name].get("nullable")
                and schema["members"][name].get("type") != "null"
            )
        }
    if kind in {"array", "vector"} and isinstance(value, list):
        return [portable_payload(schema["items"], child, registry) for child in value]
    if kind == "matrix" and isinstance(value, list):
        return [
            [portable_payload(schema["items"], child, registry) for child in row]
            for row in value
        ]
    if (
        kind == "union"
        and isinstance(value, dict)
        and set(value) == {"$case", "value"}
        and value.get("$case") in schema["cases"]
    ):
        return {
            "$case": value["$case"],
            "value": portable_payload(
                schema["cases"][value["$case"]], value["value"], registry
            ),
        }
    return value


@dataclass(frozen=True)
class ToolGrant:
    schema: str
    target: str
    allowed: dict[str, list[Any]]

    @property
    def name(self) -> str:
        return (
            "command_"
            + hashlib.sha256((self.schema + ":" + self.target).encode()).hexdigest()[
                :16
            ]
        )


class ToolCatalog:
    def __init__(self, registry: MemoryRegistry, grants: tuple[ToolGrant, ...]) -> None:
        self.registry = registry
        self.grants = {grant.name: grant for grant in grants}
        if len(self.grants) != len(grants):
            raise ValueError("duplicate tool grants")
        self.tools: list[dict[str, Any]] = []
        summary = {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "Short public reason for this decision.",
        }
        for grant in grants:
            descriptor = registry.message(grant.schema)
            if descriptor.kind != "command":
                raise ValueError("tool grant must reference a command")
            parameters = json_schema(descriptor.schema, registry)
            if (
                parameters.get("type") != "object"
                or "decision_summary" in parameters["properties"]
            ):
                raise ValueError(
                    "command tool requires a record without reserved decision_summary"
                )
            for key, values in grant.allowed.items():
                if key not in parameters["properties"] or not values:
                    raise ValueError(
                        "grant constraint must name a command member with allowed values"
                    )
                parameters["properties"][key]["enum"] = values
            parameters["properties"]["decision_summary"] = summary
            parameters["required"].append("decision_summary")
            self.tools.append(
                self._tool(
                    grant.name,
                    f"Issue {grant.schema} to {grant.target}; observe actual receipts for completion.",
                    parameters,
                )
            )
        for name in ("wait", "noop"):
            properties = {"decision_summary": summary}
            if name == "wait":
                properties["duration_ns"] = {"type": "integer", "minimum": 1}
            self.tools.append(
                self._tool(
                    name, "End this decision explicitly.", object_schema(properties)
                )
            )

    @staticmethod
    def _tool(
        name: str, description: str, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "strict": True,
                "parameters": parameters,
            },
        }

    def validate(
        self, name: str, arguments: str
    ) -> tuple[ToolGrant | None, dict[str, Any], str]:
        value = strict_json(arguments)
        tool = next(
            (tool for tool in self.tools if tool["function"]["name"] == name), None
        )
        if tool is None:
            raise ValueError("ungranted command tool")
        validate_strict(tool["function"]["parameters"], value)
        if not isinstance(value, dict):
            raise TypeError("tool arguments must be an object")
        summary = value.pop("decision_summary", None)
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 4096:
            raise ValueError("required nonblank decision_summary (max 4096)")
        if name in {"wait", "noop"}:
            if (
                name == "noop"
                and value
                or name == "wait"
                and (
                    set(value) != {"duration_ns"}
                    or type(value["duration_ns"]) is not int
                    or value["duration_ns"] <= 0
                )
            ):
                raise ValueError("invalid wait/noop arguments")
            return None, value, summary
        if name not in self.grants:
            raise ValueError("ungranted command tool")
        grant = self.grants[name]
        schema = self.registry.message(grant.schema).schema
        payload = portable_payload(schema, value, self.registry)
        try:
            self.registry.validate(schema, payload)
        except KernelError as exc:
            raise ValueError(str(exc)) from exc
        for key, allowed in grant.allowed.items():
            if payload.get(key) not in allowed:
                raise ValueError(f"ungranted argument: {key}")
        return grant, payload, summary
