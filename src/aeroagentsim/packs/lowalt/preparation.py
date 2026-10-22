"""Named, revisioned compatibility for historical lowalt scenario/v1 inputs."""

from __future__ import annotations

import copy
import json
from typing import Any

from aeroagentsim.preparation_api import (
    PreparedScenario,
    PreparerPlugin,
    ReadOnlyResources,
    resource_manifest,
)

from .template import SLOTS, expand

LEGACY_PREPARER_NAME = "lowalt-legacy"
LEGACY_PREPARER_REVISION = "lowalt-legacy/v1"


def prepare(
    authored_document: dict[str, Any], final_seed: int,
    read_only_resources: ReadOnlyResources,
) -> PreparedScenario:
    """Keep old admission/expansion policy in its owning pack, without I/O.

    The caller has already resolved final_seed. Always start from the authored
    input, not a previously expanded preview whose fleet has already sampled.
    Rescue expansion remains here for B1; its extraction belongs to B2.
    """
    document = copy.deepcopy(authored_document)
    format_id = document["format"]
    selection = copy.deepcopy(document.get("preparation"))
    document["run"]["seed"] = final_seed
    task_extensions = any(
        isinstance(engine, dict) and engine.get("plugin") == "lowalt_tasks"
        and any(key in (engine.get("config") or {}) for key in ("cooperation", "vehicle_task_kinds"))
        for engine in (document.get("engines") or {}).values()
    )
    if "algorithms" in document or "generators" in document or task_extensions:
        snapshot = read_only_resources.get("registry.snapshot")
        registry_snapshot = (
            {"registry": {"types": []}} if snapshot is None else json.loads(snapshot)
        )
        expand(document, registry_snapshot=registry_snapshot)
    # The legacy scene-base materializer replaces the document with a pack
    # base. Preserve the opt-in envelope if this adapter was selected by v2.
    document["format"] = format_id
    if selection is not None:
        document["preparation"] = selection
    if "city" in document and document["city"] != "huangpu":
        raise ValueError("city: expected huangpu")
    if "algorithms" in document:
        algorithms = document["algorithms"]
        if not isinstance(algorithms, dict):
            raise ValueError("algorithms: expected a mapping")
        if set(algorithms) - set(SLOTS):
            raise ValueError("algorithms: unknown algorithm slot")
        for slot, algorithm in algorithms.items():
            if not isinstance(algorithm, str) or not algorithm:
                raise ValueError(f"algorithms.{slot}: expected a nonempty string")
    for index, entity in enumerate(document.get("entities", [])):
        if not isinstance(entity, dict):
            continue  # Generic shape validation supplies the admission path.
        facts = entity.get("facts", {})
        if (
            "vehicle_class" in entity and isinstance(facts, dict)
            and "lowalt.vehicle_class" in facts
            and entity["vehicle_class"] != facts["lowalt.vehicle_class"]
        ):
            raise ValueError(
                f"entities[{index}].vehicle_class: must agree with lowalt.vehicle_class"
            )
    return PreparedScenario(
        document, resource_manifest=resource_manifest(read_only_resources)
    )


def plugin() -> PreparerPlugin:
    return PreparerPlugin(
        LEGACY_PREPARER_NAME, prepare, revision=LEGACY_PREPARER_REVISION,
        entry_point="aeroagentsim.packs.lowalt.preparation:plugin",
    )
