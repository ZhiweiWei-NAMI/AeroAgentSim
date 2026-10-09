"""Platform message subject declarations, independent of kernel payload semantics."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aerokernel.registry import MemoryRegistry

Path = tuple[str | int, ...]


@dataclass(frozen=True)
class SubjectBinding:
    path: Path
    type_id: str
    generation_path: Path | None = None


def _path(value: Any, location: str) -> Path:
    if not isinstance(value, list) or any(
        not (isinstance(part, str) or type(part) is int and part >= 0) for part in value
    ):
        raise ValueError(f"{location}: expected a list of member names/array indices")
    return tuple(value)


def _resolve(registry: MemoryRegistry, schema: Any) -> Mapping[str, Any]:
    while isinstance(schema, str):
        schema = registry.schemas[schema]
    if not isinstance(schema, Mapping):
        raise TypeError("message subjects: invalid payload schema")
    return schema


def _node(registry: MemoryRegistry, schema: Any, path: Path) -> Mapping[str, Any]:
    node = _resolve(registry, schema)
    for part in path:
        if node["type"] == "record" and isinstance(part, str):
            if part not in node["members"]:
                raise ValueError(f"unknown payload member {part!r}")
            node = _resolve(registry, node["members"][part])
        elif node["type"] in {"array", "vector"} and (type(part) is int or part == "*"):
            node = _resolve(registry, node["items"])
        else:
            raise ValueError(f"payload path component {part!r} does not match schema")
    return node


def declarations(
    registry: MemoryRegistry, document: Mapping[str, Any]
) -> dict[str, tuple[SubjectBinding, ...]]:
    """Validate inline descriptors and overlays for pinned registry messages."""
    spec = document["registry"]
    raw = spec.get("message_subjects", {})
    if not isinstance(raw, dict):
        raise TypeError("registry.message_subjects: expected a mapping")
    raw = dict(raw)
    for item in spec.get("messages", []):
        if "subjects" in item:
            if item["id"] in raw:
                raise ValueError(
                    f"registry.messages.{item['id']}.subjects: duplicate declaration"
                )
            raw[item["id"]] = item["subjects"]
    result = {}
    for message_id, items in raw.items():
        location = f"registry.messages.{message_id}.subjects"
        try:
            descriptor = registry.message(message_id)
            if not isinstance(items, list):
                raise TypeError("expected a list")
            bindings = []
            for item in items:
                if (
                    not isinstance(item, dict)
                    or set(item) - {"path", "type_id", "generation_path"}
                    or not {"path", "type_id"} <= set(item)
                ):
                    raise ValueError(
                        "expected path/type_id and optional generation_path"
                    )
                path = _path(item["path"], location)
                type_id = item["type_id"]
                registry.is_a(type_id, type_id)
                node = _node(registry, descriptor.schema, path)
                if node["type"] not in {"string", "ref"}:
                    raise ValueError("subject path must have string or ref schema")
                generation = None
                if "generation_path" in item:
                    generation = _path(item["generation_path"], location)
                    if node["type"] != "string" or "*" in path or "*" in generation:
                        raise ValueError(
                            "generation_path requires a scalar string subject"
                        )
                    if (
                        _node(registry, descriptor.schema, generation)["type"]
                        != "integer"
                    ):
                        raise ValueError("generation_path must have integer schema")
                bindings.append(SubjectBinding(path, type_id, generation))
            result[message_id] = tuple(bindings)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{location}: {exc}") from exc
    return result


def payload_values(
    registry: MemoryRegistry, schema: Any, payload: Any, path: Path
) -> list[Any]:
    """Only schema-declared optional/nullable members may omit a subject."""
    node = _resolve(registry, schema)
    if payload is None and node.get("nullable") is True:
        return []
    if not path:
        return [payload]
    part, rest = path[0], path[1:]
    if node["type"] == "record":
        if part not in payload and part not in node["required"]:
            return []
        return payload_values(registry, node["members"][part], payload[part], rest)
    if part == "*":
        return [
            value
            for child in payload
            for value in payload_values(registry, node["items"], child, rest)
        ]
    return payload_values(registry, node["items"], payload[part], rest)
