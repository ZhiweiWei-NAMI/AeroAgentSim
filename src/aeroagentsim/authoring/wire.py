"""Restore declared floating quantities after JavaScript JSON serialization.

JSON has one numeric category; the kernel intentionally has distinct integer
and floating schemas. Only present integer numeric values in declared number
slots are converted. Missing, null, Boolean and string values remain unchanged
and are rejected by the real loader when invalid.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from aerokernel.values import thaw

from aeroagentsim.integrations.aerograph import read_snapshot
from aeroagentsim.scenario.paths import source_path

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


def normalize_wire(
    document: dict[str, Any], catalog: Catalog, *, base: Path | None = None
) -> dict[str, Any]:
    result = copy.deepcopy(document)
    registry = result.get("registry", {})
    if not isinstance(registry, dict):
        return result
    registry.pop("digest", None)
    fields = {
        f["id"]: f["schema"]
        for f in registry.get("fields", [])
        if isinstance(f, dict) and "id" in f and "schema" in f
    }
    definitions: dict[str, Any] = {}
    snapshot_bound = "snapshot" in registry
    if snapshot_bound:
        if base is None:
            raise ValueError("Registry snapshot normalization requires its document base")
        compiled = read_snapshot(source_path(registry["snapshot"], base))
        definitions.update(compiled.registry.to_data()["schemas"])
        for field in compiled.registry.fields:
            fields.setdefault(field.id, thaw(field.schema))

    def typed_schema(schema: Any) -> None:
        if isinstance(schema, dict):
            if isinstance(schema.get("enum"), list):
                plain = {key: value for key, value in schema.items() if key != "enum"}
                schema["enum"] = [
                    _value(value, plain, definitions) for value in schema["enum"]
                ]
            for child in schema.values():
                typed_schema(child)
        elif isinstance(schema, list):
            for child in schema:
                typed_schema(child)

    # Schema enum literals have the same declared numeric kinds as fact values.
    for declaration in registry.get("fields", []) + registry.get("messages", []):
        for key in ("schema", "result_schema", "feedback_schema"):
            if key in declaration:
                typed_schema(declaration[key])
    entities = result.get("entities", [])
    if not isinstance(entities, list):
        return result
    custom = {
        t["id"] for t in registry.get("types", []) if isinstance(t, dict) and "id" in t
    }
    source_types = {} if snapshot_bound else catalog.sources().types
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
            if engine.get("plugin") == "traffic_capture_bridge":
                config = engine["config"]
                if "timeout_s" in config:
                    # This configuration value is copied into the declared render
                    # command. Restore its wire kind from that actual message schema.
                    config["timeout_s"] = _value(
                        {"timeout_s": config["timeout_s"]},
                        messages[config["request_schema"]],
                        definitions,
                    )["timeout_s"]
            if engine.get("plugin") == "environment":
                for profiles in engine["config"].get("profiles", {}).values():
                    for field, profile in profiles.items():
                        if field in fields:
                            for key in ("value", "base"):
                                if key in profile:
                                    profile[key] = _value(
                                        profile[key], fields[field], definitions
                                    )
                            for gust in profile.get("gusts", []):
                                if "value" in gust:
                                    gust["value"] = _value(
                                        gust["value"], fields[field], definitions
                                    )
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
