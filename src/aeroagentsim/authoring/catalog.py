"""Read-only AeroGraph browsing and selected registry compilation."""

from __future__ import annotations

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


class SnapshotCatalog(Catalog):
    """Read-only browse of an already committed registry snapshot plus overlay.

    It serves the same HTTP shape as the source-based :class:`Catalog` but is
    derived from a scenario document whose ``registry`` section carries a
    committed snapshot: loading it goes through the real
    ``load_scenario`` compiler path with no AeroGraph source dependency.
    """

    def __init__(self, document: dict[str, Any], *, base: Path) -> None:
        from aeroagentsim.scenario import load_scenario

        registry = document.get("registry")
        if not isinstance(registry, dict) or "snapshot" not in registry:
            raise ValueError(
                "snapshot catalog: scenario must pin a committed registry snapshot"
            )
        self.snapshot = resolve_source_path(str(registry["snapshot"]), base)
        self.base = base
        # Compiling once through the actual loader applies the snapshot, the
        # scenario-local overlay types/fields/relations and every kernel
        # schema constraint; nothing is invented beyond those declarations.
        loaded = load_scenario(document, base=base)
        self._compiled = loaded.compiled
        self._registry = loaded.registry
        self.root = base.resolve()
        self._sources = None
        self._details = {}

    def sources(self) -> Sources:
        raise ValueError(
            "Type catalog limited to this scenario's snapshot; no AeroGraph source configured"
        )

    def relations(self, query: str = "") -> list[dict[str, Any]]:
        return [
            row
            for row in self._registry.to_data()["relations"]
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
            }
            if q not in str(haystack).casefold():
                continue
            fields.append(
                {
                    **haystack,
                    "source": {
                        "snapshot": self.snapshot.name,
                        "kind": "snapshot_overlay"
                        if field_descriptor.declaring_type not in raw_types
                        else "snapshot",
                    },
                }
            )
        return {
            "types": types,
            "fields": fields,
            "catalog_scope": "scenario-snapshot",
            "catalog_notice": "Type catalog limited to this scenario's snapshot",
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


def engines() -> list[dict[str, Any]]:
    """Report plugin availability without starting an external simulator."""
    catalog = EngineCatalog()
    result = []
    for name in catalog.names():
        item: dict[str, Any] = {
            "id": name,
            "available": True,
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
