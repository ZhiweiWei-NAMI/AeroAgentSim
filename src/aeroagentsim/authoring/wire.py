"""Restore declared floating quantities after JavaScript JSON serialization.

JSON has one numeric category; the kernel intentionally has distinct integer
and floating schemas. Only present integer numeric values in declared number
slots are converted. Missing, null, Boolean and string values remain unchanged
and are rejected by the real loader when invalid.
"""

from __future__ import annotations

import copy
from typing import Any

from .catalog import Catalog


def _value(value: Any, schema: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(schema, str):
        schema = definitions[schema]
    if not isinstance(schema, dict):
        return value
    if "schema_ref" in schema:
        return _value(value, definitions[schema["schema_ref"]], definitions)
    kind = schema.get("type")
    if kind == "number" and type(value) is int:
        return float(value)
    if kind in {"vector", "array"} and isinstance(value, list):
        return [_value(item, schema["items"], definitions) for item in value]
    if kind == "matrix" and isinstance(value, list):
        return [
            [_value(item, schema["items"], definitions) for item in row]
            if isinstance(row, list)
            else row
            for row in value
        ]
    if kind == "record" and isinstance(value, dict):
        return {
            key: _value(item, schema["members"][key], definitions)
            if key in schema["members"]
            else item
            for key, item in value.items()
        }
    if kind == "union" and isinstance(value, dict):
        case = schema.get("cases", {}).get(value.get(schema.get("discriminator")))
        if case is not None:
            return _value(value, case, definitions)
    return value


def normalize_wire(document: dict[str, Any], catalog: Catalog) -> dict[str, Any]:
    result = copy.deepcopy(document)
    registry = result.get("registry", {})
    if not isinstance(registry, dict):
        return result
    fields = {
        f["id"]: f["schema"]
        for f in registry.get("fields", [])
        if isinstance(f, dict) and "id" in f and "schema" in f
    }
    definitions: dict[str, Any] = {}
    entities = result.get("entities", [])
    if not isinstance(entities, list):
        return result
    custom = {
        t["id"] for t in registry.get("types", []) if isinstance(t, dict) and "id" in t
    }
    source_types = catalog.sources().types
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        typ = entity.get("type")
        if isinstance(typ, str) and typ not in custom and typ in source_types:
            detail = catalog.type_detail(typ)
            definitions.update(detail["schemas"])
            for field in detail["fields"]:
                fields.setdefault(field["id"], field["schema"])
        facts = entity.get("facts")
        if isinstance(facts, dict):
            entity["facts"] = {
                key: _value(value, fields[key], definitions) if key in fields else value
                for key, value in facts.items()
            }
    messages = {
        item["id"]: item["schema"]
        for item in registry.get("messages", [])
        if isinstance(item, dict) and "id" in item and "schema" in item
    }
    bindings = result.get("bindings", {})
    if isinstance(bindings, dict):
        for command in bindings.get("commands", []):
            if (
                isinstance(command, dict)
                and command.get("schema") in messages
                and "payload" in command
            ):
                command["payload"] = _value(
                    command["payload"], messages[command["schema"]], definitions
                )
    engines = result.get("engines", {})
    if isinstance(engines, dict):
        for engine in engines.values():
            if not isinstance(engine, dict) or not isinstance(
                engine.get("config"), dict
            ):
                continue
            for machine in engine["config"].get("machines", []):
                for state in machine.get("states", {}).values():
                    for action in state.get("on_enter", []):
                        if (
                            action.get("kind") in {"command", "emit"}
                            and action.get("schema") in messages
                        ):
                            action["payload"] = _value(
                                action["payload"],
                                messages[action["schema"]],
                                definitions,
                            )
                        if (
                            action.get("kind") == "set"
                            and action.get("field") in fields
                        ):
                            action["value"] = _value(
                                action["value"], fields[action["field"]], definitions
                            )
    return result
