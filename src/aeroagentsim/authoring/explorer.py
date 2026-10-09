"""Read-only research explorer joining the native AeroGraph catalog with a draft.

The endpoint reports actual persisted ontology sources and the current
workspace's declared writers, entities and authored behaviour packages. It
never evaluates predicates, synthesizes writers for undeclared fields, or
claims counts without a selected workspace.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aeroagentsim.integrations.aerograph.source import Sources

from .catalog import SnapshotCatalog
from .workspace import WorkspaceStore

MARKER = '<script id="semantic-data" type="application/json">'

_FIELD_METADATA_KEYS = (
    "unit",
    "frame",
    "role",
    "domain",
    "propertyPath",
    "requiredWhen",
)


def _index_definitions(root: Path) -> dict[str, dict[str, Any]]:
    """Parse the persisted semantic-directory payload; no build or runtime runs."""
    page = (root / "semantic-directory/index.html").read_text(encoding="utf-8")
    start = page.index(MARKER) + len(MARKER)
    data = json.loads(page[start : page.index("</script>", start)])
    definitions = data["predicateFormat"]["definitions"]
    if not isinstance(definitions, dict):
        raise TypeError("semantic-directory: predicate definitions mapping required")
    return {
        str(key): row
        for key, row in definitions.items()
        if isinstance(row, dict) and row.get("kind") in {"predicate", "event"}
    }


class _DefinitionCache:
    """Cache the parsed index per ontology root keyed by size and mtime."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.stamp: tuple[int, int] | None = None
        self.definitions: dict[str, dict[str, Any]] | None = None

    def get(self, root: Path) -> dict[str, dict[str, Any]]:
        path = root / "semantic-directory/index.html"
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        if self.path == path and self.stamp == stamp and self.definitions is not None:
            return self.definitions
        definitions = _index_definitions(root)
        self.path, self.stamp, self.definitions = path, stamp, definitions
        return definitions


_cache = _DefinitionCache()


def _engine_declared_fields(engine: dict[str, Any]) -> list[str]:
    """Fields an engine partition actually declares, from its real config keys."""
    config = engine.get("config", {})
    if not isinstance(config, dict):
        raise TypeError("engine.config: mapping required")
    declared: list[str] = []
    produces = config.get("produces")
    if isinstance(produces, list):
        declared.extend(f for f in produces if isinstance(f, str))
    for key in ("position_field", "velocity_field", "energy_field"):
        field = config.get(key)
        if isinstance(field, str):
            declared.append(field)
    return declared


def _collect_writers(
    scenario: dict[str, Any],
    ancestors: dict[str, set[str]],
) -> dict[str, list[dict[str, Any]]]:
    """Declared field writers from the current workspace scenario only.

    ``bindings.exact`` names entity/field/writer rows; ``bindings.rules``
    declare writer fields over a type domain, and rule entity lists carry the
    scenario entities of exactly that declared type. An engine config declares
    its own fields. A field with no binding or declaration has no writer row.
    """
    engines = scenario.get("engines", {})
    if not isinstance(engines, dict):
        raise TypeError("scenario.engines: mapping required")
    plugin: dict[str, str] = {}
    declared: dict[str, set[str]] = {}
    for engine_id, engine in engines.items():
        if not isinstance(engine, dict):
            raise TypeError(f"engines.{engine_id}: mapping required")
        name = engine.get("plugin")
        if isinstance(name, str):
            plugin[engine_id] = name
        for field in _engine_declared_fields(engine):
            declared.setdefault(field, set()).add(engine_id)

    entity_type = {
        entity["id"]: entity.get("type")
        for entity in scenario.get("entities", [])
        if isinstance(entity, dict) and isinstance(entity.get("id"), str)
    }
    writers: dict[str, dict[str, dict[str, Any]]] = {}

    def add(field: str, engine_id: str, entities: list[str] | None) -> None:
        if not isinstance(field, str) or not field:
            return
        row = writers.setdefault(field, {}).setdefault(
            engine_id,
            {"engine_id": engine_id, "plugin": plugin.get(engine_id), "entities": []},
        )
        if entities is not None:
            known = {e for e in entities if e in entity_type}
            row["entities"] = sorted(set(row["entities"]) | known)

    for item in scenario.get("bindings", {}).get("exact", []):
        if isinstance(item, dict):
            add(item.get("field", ""), item.get("writer", ""), [item.get("entity", "")])
    for item in scenario.get("bindings", {}).get("rules", []):
        if isinstance(item, dict):
            matched = [
                entity_id
                for entity_id, type_id in entity_type.items()
                if (
                    type_id == item.get("type")
                    or item.get("type") in ancestors.get(str(type_id), set())
                )
                and (item.get("ids", "*") == "*" or entity_id in item["ids"])
            ]
            for field in (
                item.get("fields", []) if isinstance(item.get("fields"), list) else []
            ):
                add(field, item.get("writer", ""), matched)
    for field, engine_ids in declared.items():
        for engine_id in sorted(engine_ids):
            add(field, engine_id, None)
    return {field: list(rows.values()) for field, rows in writers.items()}


def _native_fields(sources: Sources) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for records in sources.fields.values():
        for record in records:
            field_id = record.data["id"]
            if field_id in seen:
                continue
            seen.add(field_id)
            data = record.data
            rows.append(
                {
                    "id": field_id,
                    "name": {
                        "en": data["sourceDisplayName"],
                        "zh": data.get("displayName"),
                    }
                    if isinstance(data.get("sourceDisplayName"), str)
                    else data.get("displayName"),
                    "description": data.get("meaning"),
                    "declaring_type": data.get("declaringClass"),
                    "schema": data.get("valueSchema"),
                    "metadata": {
                        key: data[key] for key in _FIELD_METADATA_KEYS if key in data
                    },
                    "writers": [],
                    "origin": "native",
                    "source": record.location(),
                }
            )
    rows.sort(key=lambda row: row["id"])
    return rows


def _workspace_fields(
    scenario: dict[str, Any], writers: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    registry = scenario.get("registry", {})
    if not isinstance(registry, dict):
        raise TypeError("scenario.registry: mapping required")
    rows: list[dict[str, Any]] = []
    declared = registry.get("fields", [])
    if not isinstance(declared, list):
        raise TypeError("registry.fields: list required")
    for field in declared:
        if not isinstance(field, dict) or not isinstance(field.get("id"), str):
            raise TypeError("registry.fields[]: mapping with id required")
        rows.append(
            {
                "id": field["id"],
                "name": None,
                "description": None,
                "declaring_type": field.get("type"),
                "schema": field.get("schema"),
                "metadata": field.get("metadata")
                if isinstance(field.get("metadata"), dict)
                else {},
                "writers": writers.get(field["id"], []),
                "origin": "workspace",
            }
        )
    rows.sort(key=lambda row: row["id"])
    return rows


def _workspace_types(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    registry = scenario.get("registry", {})
    if not isinstance(registry, dict):
        raise TypeError("scenario.registry: mapping required")
    rows: list[dict[str, Any]] = []
    declared = registry.get("types", [])
    if not isinstance(declared, list):
        raise TypeError("registry.types: list required")
    for row in declared:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise TypeError("registry.types[]: mapping with id required")
        parents = row.get("parents", [])
        if not isinstance(parents, list):
            raise TypeError("registry.types[].parents: list required")
        rows.append(
            {
                "id": row["id"],
                "name": None,
                "description": None,
                "parents": parents,
                "abstract": row.get("abstract"),
                "directory": None,
                "origin": "workspace",
            }
        )
    return rows


def _packages(
    store: WorkspaceStore, identifier: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Loaded authored behaviour packages plus real read failures, never invented ones."""
    draft = store.get(identifier)
    entries = draft["scenario"].get("behaviours", [])
    packages: list[dict[str, Any]] = []
    errors: list[str] = []
    if not isinstance(entries, list):
        raise TypeError("scenario.behaviours: list required")
    for index, _ in enumerate(entries):
        try:
            package = store._behaviour_package(identifier, index)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            errors.append(f"behaviours[{index}]: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(package, dict):
            raise TypeError(f"behaviours[{index}]: mapping required")
        packages.append(package)
    return packages, errors


def _references(
    packages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    predicates: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    for index, package in enumerate(packages):
        for kind, target in (("predicates", predicates), ("chains", chains)):
            section = package.get(kind, {})
            if not isinstance(section, dict):
                raise TypeError(f"behaviours[{index}].{kind}: mapping required")
            for key, row in section.items():
                if isinstance(row, dict):
                    target.append(
                        {
                            "id": key,
                            "name": row.get("name"),
                            "description": row.get("description"),
                            "definition": row,
                            "package": index,
                        }
                    )
    return predicates, chains


def _type_closure(parents: dict[str, list[str]]) -> dict[str, set[str]]:
    descendants: dict[str, set[str]] = {}

    def walk(type_id: str, seen: frozenset[str]) -> set[str]:
        if type_id in descendants:
            return descendants[type_id]
        result: set[str] = set()
        for parent in parents.get(type_id, []):
            if parent in seen:
                continue
            result.add(parent)
            result |= walk(parent, seen | {parent})
        if seen == frozenset({type_id}):
            descendants[type_id] = result
        return result

    for type_id in parents:
        walk(type_id, frozenset({type_id}))
    return descendants


def explorer_payload(store: WorkspaceStore, workspace_id: str | None) -> dict[str, Any]:
    """Assemble the explorer catalog; the current workspace owns writers/counts."""
    # Recorded update order is authoritative; filesystem timestamps can tie
    # even when two sequential API writes have distinct update timestamps.
    drafts = store.list()
    if workspace_id is None:
        workspace_id = drafts[0]["id"] if drafts else None
    draft = store.get(workspace_id) if workspace_id is not None else None
    scenario = draft["scenario"] if draft is not None else {}
    catalog = store.catalog_for(workspace_id)
    sources = None if isinstance(catalog, SnapshotCatalog) else catalog.sources()
    snapshot = catalog.search() if sources is None else None
    concepts = (
        snapshot["types"]
        if snapshot is not None
        else sources.read("entity-directory/data/concepts.json")["concepts"]
        if sources is not None
        else []
    )
    if not isinstance(concepts, list):
        raise TypeError("catalog: concepts array required")

    workspace_types = _workspace_types(scenario) if scenario else []
    packages, package_errors = (
        _packages(store, workspace_id) if workspace_id else ([], [])
    )
    predicates, chains = _references(packages)
    entities = (
        [
            {
                "id": entity["id"],
                "type": entity.get("type"),
                "label": entity["id"]
                if not isinstance(entity.get("facts"), dict)
                or not isinstance(entity["facts"].get("name"), str)
                else entity["facts"]["name"],
            }
            for entity in scenario.get("entities", [])
            if isinstance(entity, dict) and isinstance(entity.get("id"), str)
        ]
        if scenario
        else []
    )

    parents: dict[str, list[str]] = {}
    types: list[dict[str, Any]] = []
    seen_types: set[str] = set()
    for row in concepts:
        if not isinstance(row, dict):
            continue
        type_id = row["id"]
        if type_id in seen_types:
            continue
        seen_types.add(type_id)
        parent = row.get("parent")
        parents[type_id] = (
            list(row["parents"])
            if snapshot is not None
            else [parent]
            if isinstance(parent, str)
            else []
        )
        navigation = row.get("navigation")
        types.append(
            {
                "id": type_id,
                "name": row.get("name"),
                "description": row.get("description"),
                "parents": parents[type_id],
                "abstract": row.get("abstract"),
                "directory": row.get("directory")
                if snapshot is not None
                else (navigation.get("view") if isinstance(navigation, dict) else None),
                "origin": "snapshot" if snapshot is not None else "native",
            }
        )
    indexed_types = {row["id"]: row for row in types}
    for row in workspace_types:
        parents[row["id"]] = [p for p in row["parents"] if isinstance(p, str)]
        if row["id"] in indexed_types:
            existing = indexed_types[row["id"]]
            row["name"], row["description"], row["directory"] = (
                existing["name"],
                existing["description"],
                existing["directory"],
            )
        indexed_types[row["id"]] = row
    types = list(indexed_types.values())

    closure = _type_closure(parents)
    entity_types = [entity["type"] for entity in entities if entity["type"]]
    if draft is not None:
        for row in types:
            row["count"] = sum(
                1
                for type_id in entity_types
                if type_id == row["id"] or row["id"] in closure.get(type_id, set())
            )
    else:
        for row in types:
            row["count"] = None

    writers = _collect_writers(scenario, closure) if scenario else {}
    workspace_fields = _workspace_fields(scenario, writers) if scenario else []

    # Native field rows first; workspace declarations replace the same ID and
    # carry the authoritative writers for this draft.
    native_fields = (
        _native_fields(sources)
        if sources is not None
        else [
            {
                "id": row["id"],
                "name": row.get("name"),
                "description": row.get("description"),
                "declaring_type": row["declaringClass"],
                "schema": row["valueSchema"],
                "metadata": {
                    key: row[key] for key in _FIELD_METADATA_KEYS if key in row
                },
                "origin": "snapshot",
            }
            for row in snapshot["fields"]
        ]
        if snapshot is not None
        else []
    )
    fields = {row["id"]: row for row in native_fields}
    for row in workspace_fields:
        native_field = fields.get(row["id"])
        if native_field is not None:
            row["name"] = native_field["name"]
            row["description"] = native_field["description"]
            row["metadata"] = {**native_field["metadata"], **row["metadata"]}
        fields[row["id"]] = row

    for field_id, row in fields.items():
        row["writers"] = writers.get(field_id, [])
        row["metadata"] = {
            key: row["metadata"][key]
            for key in _FIELD_METADATA_KEYS
            if key in row["metadata"]
        }

    definitions = _cache.get(catalog.root) if sources is not None else {}
    native_predicates = [
        {
            "id": row["id"],
            "name": row.get("name"),
            "description": row.get("description"),
            "definition": {
                "kind": row.get("kind"),
                "expression": row.get("expression"),
                "inputs": row.get("inputs"),
                "entityTypeIds": row.get("entityTypeIds"),
                "domains": row.get("domains"),
                "nativeDialect": (row.get("execution") or {}).get("nativeDialect")
                if isinstance(row.get("execution"), dict)
                else None,
                "hasApplicability": row.get("applicability") is not None,
            },
            "origin": "native",
        }
        for row in definitions.values()
        if row.get("kind") == "predicate"
    ]
    native_predicates.sort(key=lambda row: row["id"])
    native_events = [row for row in definitions.values() if row.get("kind") == "event"]
    return {
        "workspace": {"id": draft["id"], "name": draft["name"]}
        if draft is not None
        else None,
        "catalog_scope": "scenario-snapshot"
        if snapshot is not None
        else "aerograph-source",
        "catalog_notice": snapshot["catalog_notice"] if snapshot is not None else None,
        "types": types,
        "fields": list(fields.values()),
        "relations": catalog.relations()
        if isinstance(catalog, SnapshotCatalog)
        else [
            {
                key: record.data[key]
                for key in (
                    "id",
                    "displayName",
                    "description",
                    "sourceClass",
                    "targetClass",
                    "kind",
                    "domain",
                )
                if key in record.data
            }
            for rows in (sources.relations.values() if sources is not None else [])
            for record in rows
        ],
        "workspace_relations": scenario.get("registry", {}).get("relations", []),
        "predicates": native_predicates + predicates,
        "events": [
            {
                "id": row["id"],
                "name": row.get("name"),
                "description": row.get("description"),
                "definition": {
                    "kind": row.get("kind"),
                    "expression": row.get("expression"),
                    "inputs": row.get("inputs"),
                    "nativeDialect": (row.get("execution") or {}).get("nativeDialect")
                    if isinstance(row.get("execution"), dict)
                    else None,
                },
                "origin": "native",
            }
            for row in native_events
        ],
        "chains": chains,
        "entities": entities,
        "package_errors": package_errors,
        "workspaces": [{"id": item["id"], "name": item["name"]} for item in drafts],
    }
