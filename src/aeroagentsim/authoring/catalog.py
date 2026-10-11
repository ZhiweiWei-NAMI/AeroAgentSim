"""Read-only AeroGraph browsing and selected registry compilation."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, cast

from aerokernel.values import thaw

from aeroagentsim.integrations.aerograph import Policy, Selection, compile_registry
from aeroagentsim.integrations.aerograph.source import Sources
from aeroagentsim.platform.plugins import EngineCatalog
from aeroagentsim.scenario.paths import source_path as resolve_source_path


class Catalog:
    """Search every persisted type/field; compile inheritance on explicit selection."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._sources: Sources | None = None
        self._details: dict[str, dict[str, Any]] = {}

    def sources(self) -> Sources:
        if self._sources is None:
            self._sources = Sources(self.root)
        return self._sources

    def search(self, query: str = "") -> dict[str, Any]:
        sources = self.sources()
        q = query.casefold()
        types = []
        fields = []
        for rows in sources.types.values():
            for record in rows:
                row = record.data
                if q in str(row).casefold():
                    types.append(
                        {
                            "id": row["id"],
                            "name": row.get("name"),
                            "parents": [row["parent"]] if row.get("parent") else [],
                            "abstract": row["abstract"],
                            "directory": row.get("navigation", {}).get("view"),
                            "parentStatus": row.get("parentStatus"),
                            "review": row.get("sourceReviewStatus"),
                        }
                    )
        for rows in sources.fields.values():
            for record in rows:
                if q in str(record.data).casefold():
                    fields.append({**record.data, "source": record.location()})
        return {"types": types, "fields": fields}

    def type_detail(self, type_id: str) -> dict[str, Any]:
        if type_id in self._details:
            return self._details[type_id]
        compiled = compile_registry(
            self.root, Selection((type_id,), relation_ids=()), Policy()
        )
        descriptor = next(t for t in compiled.registry.types if t.id == type_id)
        result = {
            "id": type_id,
            "parents": list(descriptor.parents),
            "abstract": descriptor.abstract,
            "fields": [
                {
                    "id": f.id,
                    "declaring_type": f.declaring_type,
                    "schema": thaw(f.schema),
                    "metadata": thaw(f.metadata),
                }
                for f in compiled.effective_fields(type_id)
            ],
            "schemas": compiled.registry.to_data()["schemas"],
            "provenance": thaw(compiled.provenance),
            "normalizations": thaw(compiled.normalizations),
        }
        self._details[type_id] = result
        return result


class ScenarioCatalog(Catalog):
    """The selected scenario registry, including its authored local declarations.

    Compile only the registry through the scenario loader. Unfinished entities,
    engines and rules must not prevent the editor from reading the types needed
    to repair them. This envelope contains no entities and is never executed.
    """

    def __init__(self, document: dict[str, Any], *, base: Path) -> None:
        from aeroagentsim.scenario import load_scenario

        registry = document.get("registry")
        if not isinstance(registry, dict):
            raise ValueError("scenario catalog: registry declaration required")
        self.snapshot = (
            resolve_source_path(str(registry["snapshot"]), base)
            if "snapshot" in registry
            else None
        )
        self.base = base
        declarations = copy.deepcopy(registry)
        if "behaviours" in document or any(
            isinstance(engine, dict) and engine.get("plugin") == "behaviour"
            for engine in document.get("engines", {}).values()
        ):
            from aeroagentsim.behaviours.records import overlay

            for category, rows in overlay().items():
                selected = declarations.setdefault(category, [])
                existing = {row["id"]: row for row in selected}
                for row in rows:
                    if row["id"] in existing:
                        if existing[row["id"]] != row:
                            raise ValueError(
                                f"registry.{category}.{row['id']}: conflicts with behaviour overlay"
                            )
                    else:
                        selected.append(row)
        loaded = load_scenario(
            {
                "format": "aeroagentsim.scenario/v1",
                "id": "authoring-registry",
                "registry": declarations,
                "entities": [],
                "bindings": {"lifecycle": []},
                "engines": {},
                "run": {"pacing": "fast", "seed": 0, "until_ns": 0, "advance_ns": 1},
                "outputs": {"durability": "flush"},
            },
            base=base,
        )
        self._compiled = loaded.compiled
        self._registry = loaded.registry
        self.root = base.resolve()
        self._sources = None
        self._details = {}

    def sources(self) -> Sources:
        raise ValueError(
            "This catalog contains the scenario registry; use the global catalog for AeroGraph sources"
        )

    def relations(self, query: str = "") -> list[dict[str, Any]]:
        return [
            row
            for row in self._registry.to_data().get("relations", [])
            if query.casefold() in str(row).casefold()
        ]

    def search(self, query: str = "") -> dict[str, Any]:
        """Browse the snapshot types/fields plus the scenario-local overlay."""
        registry = self._registry
        raw_types = self._compiled.details.get("raw_types", {})
        q = query.casefold()
        types = []
        for descriptor in registry.types:
            row = raw_types.get(descriptor.id)
            row = row if isinstance(row, dict) else {}
            haystack = row | {"id": descriptor.id, "parents": list(descriptor.parents)}
            if q not in str(haystack).casefold():
                continue
            types.append(
                {
                    "id": descriptor.id,
                    "name": row.get("name"),
                    "parents": list(descriptor.parents),
                    "abstract": descriptor.abstract,
                    "directory": (row.get("navigation") or {}).get("view"),
                    "parentStatus": row.get("parentStatus"),
                    "review": (row.get("sourceReviewStatus") or {}).get("reviewStatus"),
                }
            )
        fields = []
        for field_descriptor in registry.fields:
            metadata = cast(dict[str, Any], thaw(field_descriptor.metadata))
            row = metadata.get("raw")
            row = row if isinstance(row, dict) else {}
            haystack = row | {
                "id": field_descriptor.id,
                "declaringClass": field_descriptor.declaring_type,
                "valueSchema": thaw(field_descriptor.schema),
                "type": field_descriptor.declaring_type,
                "declaring_type": field_descriptor.declaring_type,
                "schema": thaw(field_descriptor.schema),
                "metadata": metadata,
            }
            if q not in str(haystack).casefold():
                continue
            fields.append(
                {
                    **haystack,
                    "source": {
                        **({"snapshot": self.snapshot.name} if self.snapshot else {}),
                        "kind": "scenario_overlay"
                        if field_descriptor.declaring_type not in raw_types
                        else "snapshot" if self.snapshot else "aerograph-source",
                    },
                }
            )
        return {
            "types": types,
            "fields": fields,
            "schemas": registry.to_data()["schemas"],
            "catalog_scope": "scenario-snapshot" if self.snapshot else "scenario-selection",
            "catalog_notice": (
                "Type catalog limited to this scenario's snapshot"
                if self.snapshot
                else "Types and fields selected by this scenario, including local declarations"
            ),
        }

    def type_detail(self, type_id: str) -> dict[str, Any]:
        """Ancestry, effective fields and relations through the snapshot shape."""
        registry = self._registry
        descriptor = next((row for row in registry.types if row.id == type_id), None)
        if descriptor is None:
            raise KeyError(f"Type {type_id!r} is not in this scenario snapshot")
        return {
            "id": type_id,
            "parents": list(descriptor.parents),
            "abstract": descriptor.abstract,
            "fields": [
                {
                    "id": f.id,
                    "declaring_type": f.declaring_type,
                    "schema": thaw(f.schema),
                    "metadata": thaw(f.metadata),
                }
                for f in registry.fields
                if registry.is_a(type_id, f.declaring_type)
            ],
            "schemas": registry.to_data()["schemas"],
            "relations": [
                row
                for row in self.relations()
                if registry.is_a(type_id, row["source_type"])
                or registry.is_a(type_id, row["target_type"])
            ],
            "provenance": thaw(self._compiled.provenance),
            "normalizations": thaw(self._compiled.normalizations),
        }


class SnapshotCatalog(ScenarioCatalog):
    """Scenario catalog backed by an explicitly pinned portable snapshot."""

    def __init__(self, document: dict[str, Any], *, base: Path) -> None:
        if "snapshot" not in document.get("registry", {}):
            raise ValueError("snapshot catalog: scenario must pin a registry snapshot")
        super().__init__(document, base=base)


def engines() -> list[dict[str, Any]]:
    """Report plugin availability without starting an external simulator."""
    catalog = EngineCatalog()
    result = []
    for name in catalog.names():
        item: dict[str, Any] = {
            "id": name,
            "available": True,
            "installed": True,
            "availability_scope": "plugin-code",
            "description": "installed plugin",
        }
        try:
            schema = catalog.config_schema(name)
        except Exception as exc:  # noqa: BLE001 - expose a broken plugin without hiding others
            item.update(available=False, error=f"{type(exc).__name__}: {exc}")
        else:
            if schema is not None:
                item["config_schema"] = schema
        result.append(item)
    return result
