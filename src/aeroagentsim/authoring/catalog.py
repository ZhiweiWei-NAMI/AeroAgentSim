"""Read-only AeroGraph browsing and selected registry compilation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aerokernel.values import thaw

from aeroagentsim.integrations.aerograph import Policy, Selection, compile_registry
from aeroagentsim.integrations.aerograph.source import Sources
from aeroagentsim.platform.plugins import BUILTINS, EngineCatalog


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
            "digest": compiled.digest,
            "schemas": compiled.registry.to_data()["schemas"],
            "provenance": thaw(compiled.provenance),
            "normalizations": thaw(compiled.normalizations),
        }
        self._details[type_id] = result
        return result


def engines() -> list[dict[str, Any]]:
    """Report plugin availability without starting an external simulator."""
    catalog = EngineCatalog()
    choices = (
        "kinematic",
        "px4_gazebo",
        "sumo",
        "ns3",
        "workflow",
        "decision",
        "logistics",
        "inspection",
    )
    return [
        {
            "id": name,
            "available": name in BUILTINS or name in catalog.entries,
            "description": "installed plugin"
            if name in BUILTINS or name in catalog.entries
            else "requires engine entry point installation",
        }
        for name in choices
    ]
