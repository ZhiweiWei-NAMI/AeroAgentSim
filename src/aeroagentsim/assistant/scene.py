"""Host read composition, visible diffs and evidence-neutral scene explanations."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from aeroagentsim.assistant_api import CompileContext, freeze, thaw, validate_expectations
from aeroagentsim.scenario.loader import UniqueLoader

from .catalogue import OperationCatalogue, callbacks, metadata, position_fields
from .diff_display import enrich_diff

SECOND = 1_000_000_000
_COLLECTIONS = {"fleets", "places", "workloads", "events", "rules", "routine"}


class _SceneSummary(dict):
    """Keep read ownership out of the serialized, backwards-compatible scene."""

    def __init__(self, value: dict) -> None:
        super().__init__(value)
        self.contributions: dict[str, dict] = {}


def _family_summary(summary: dict, provider: str) -> dict:
    result = copy.deepcopy(dict(summary))
    if isinstance(summary, _SceneSummary):
        own = summary.contributions.get(provider, {})
        for section in _COLLECTIONS:
            result[section] = copy.deepcopy(own.get(section, []))
    return result


def _registry_types(document: dict, directory: Path | None) -> dict[str, dict]:
    types = {}
    registry = document.get("registry", {})
    if directory is not None and "snapshot" in registry:
        path = Path(registry["snapshot"])
        snapshot = json.loads((path if path.is_absolute() else directory / path).read_text())
        types.update({r["id"]: r for r in snapshot["registry"]["types"]})
    types.update({r["id"]: r for r in registry.get("types", [])})
    return types


def _position(entity: dict, *, catalogue: OperationCatalogue | None = None) -> list | None:
    facts = entity.get("facts", {})
    return next((facts[key] for key in position_fields(catalogue=catalogue) if key in facts), None)


def _read_inputs(document: dict, directory: Path | None, catalog=None, *, catalogue: OperationCatalogue | None = None) -> dict:
    types = _registry_types(document, directory)
    if catalog is not None:
        pending = [entity["type"] for entity in document.get("entities", [])]
        pending.extend(row["type"] for row in document.get("generators", {}).values() if isinstance(row, dict) and "type" in row)
        seen = set()
        while pending:
            identity = pending.pop(0)
            if identity in seen:
                continue
            seen.add(identity)
            types[identity] = catalog.type_detail(identity)
            pending.extend(types[identity].get("parents", []))
    packages = [package.get("document", package) for package in document.get("behaviours", [])]
    if directory is not None:
        packages = [yaml.load((directory / package["path"]).read_bytes(), Loader=UniqueLoader) if "path" in package else package for package in packages]
    return {"types": types, "packages": packages, "position_fields": position_fields(catalogue=catalogue)}


def summarize(document: dict, *, directory: Path | None = None, catalog=None, controls: dict | None = None,
              catalogue: OperationCatalogue | None = None) -> dict:
    catalogue = OperationCatalogue() if catalogue is None else catalogue
    duration = document.get("run", {}).get("until_ns")
    summary = {"title": document.get("title", document.get("id")), "city": document.get("city"),
               "duration_s": duration / SECOND if duration is not None else None, "run_mode": None,
               "environment": {}, "fleets": [], "places": [], "workloads": [], "events": [], "rules": [], "routine": [],
               "algorithms": copy.deepcopy(document.get("algorithms", {})), "cooperation": None,
               "controls": copy.deepcopy(controls) if controls is not None else {}, "origin": copy.deepcopy(document.get("origin")),
               **metadata("summary_defaults", {}, catalogue=catalogue), "entity_ids": sorted(entity["id"] for entity in document.get("entities", [])),
               "entity_facts": {entity["id"]: hashlib.sha256(json.dumps(entity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                                for entity in document.get("entities", [])}}
    context = CompileContext(document, data=_read_inputs(document, directory, catalog, catalogue=catalogue), controls=controls or {})
    contributions = []
    for plugin in catalogue.plugins:
        if plugin.read is not None and plugin.read.summarize is not None:
            row = thaw(freeze(plugin.read.summarize(context)))
            if not isinstance(row, dict):
                raise TypeError(f"{plugin.name}: scene summary must be a mapping")
            contributions.append((plugin, row))
    summary = _SceneSummary(summary)
    summary.contributions = {plugin.name: row for plugin, row in contributions}
    collections = _COLLECTIONS
    for plugin, row in contributions:
        for key, value in row.items():
            if key not in collections:
                if isinstance(summary.get(key), dict) and isinstance(value, dict):
                    summary[key].update(value)
                else:
                    summary[key] = value
    for key in collections:
        rows, owners = [], {}
        for plugin, contribution in sorted(contributions, key=lambda item: item[0].read.priority.get(key, 0)):
            for row in contribution.get(key, []):
                if isinstance(row, dict) and "id" in row:
                    identity = row["id"]
                    if identity in owners and owners[identity] != plugin.name:
                        raise ValueError(f"Conflicting assistant summary ids in {key}")
                    owners[identity] = plugin.name
                # Legacy projections can repeat a gust ID across wind profiles.
                # Keep every row; only a different provider is an ownership collision.
                rows.append(row)
        summary[key] = rows
    return summary


def _ordered_projection(read, section: str, rows: list[dict], summary: dict) -> list[tuple[int, dict]]:
    callback = read._callbacks.get("projection_order")
    priorities = callback(section, freeze(rows), freeze(summary)) if callback is not None else [read.priority.get(section, 0)] * len(rows)
    if len(priorities) != len(rows) or any(type(priority) is not int for priority in priorities):
        raise TypeError("assistant projection order: one integer per row required")
    return list(zip(priorities, rows))


def validate_scene_expectations(rows: list[dict], summary: dict, *, catalogue: OperationCatalogue) -> None:
    # Preserve the shipped multi-profile weather identities and rows, without
    # relaxing admission of public CompileResult expectations.
    repeated = frozenset(identity for callback in callbacks("repeated_expectation_ids", catalogue=catalogue)
                         for identity in callback(copy.deepcopy(summary)))
    validate_expectations(rows, _repeated_ids=repeated)


def expectations(document: dict, summary: dict, *, catalogue: OperationCatalogue | None = None) -> list[dict]:
    catalogue = OperationCatalogue() if catalogue is None else catalogue
    rows, owners = [], {}
    for plugin in catalogue.plugins:
        if plugin.read is not None and plugin.read.expectations is not None:
            projected = thaw(freeze(plugin.read.expectations(copy.deepcopy(document), _family_summary(summary, plugin.name))))
            # Exceptions are provider-local: another family cannot reuse a legacy ID.
            duplicate_ids = plugin.read._callbacks.get("repeated_expectation_ids", lambda summary: ())
            validate_expectations(projected, _repeated_ids=frozenset(duplicate_ids(copy.deepcopy(summary))))
            for row in projected:
                if row["id"] in owners and owners[row["id"]] != plugin.name:
                    raise ValueError("assistant expectations: unique nonempty ids required")
                owners[row["id"]] = plugin.name
            rows.extend(_ordered_projection(plugin.read, "expectations", projected, _family_summary(summary, plugin.name)))
    result = [row for _, row in sorted(rows, key=lambda item: item[0])]
    validate_scene_expectations(result, summary, catalogue=catalogue)
    return result


def interactions(summary: dict, *, catalogue: OperationCatalogue | None = None) -> dict:
    catalogue = OperationCatalogue() if catalogue is None else catalogue
    projected = {"lanes": [], "steps": []}
    for plugin in catalogue.plugins:
        if plugin.read is not None and plugin.read.interactions is not None:
            own = _family_summary(summary, plugin.name)
            contribution = thaw(freeze(plugin.read.interactions(own)))
            for section in projected:
                projected[section].extend(_ordered_projection(plugin.read, section, contribution.get(section, []), own))
    lanes, steps = [], []
    for _, lane in sorted(projected["lanes"], key=lambda item: item[0]):
        if not any(existing["id"] == lane["id"] for existing in lanes):
            lanes.append(lane)
    steps.extend(row for _, row in sorted(projected["steps"], key=lambda item: item[0]))
    return {"lanes": lanes, "steps": steps}


def explain_participant(document: dict, summary: dict, entity_id: str) -> dict:
    for callback in callbacks("participant"):
        result = callback(copy.deepcopy(document), copy.deepcopy(summary), entity_id)
        if result is not None:
            return result
    entity = next((row for row in document.get("entities", []) if row["id"] == entity_id), None)
    if entity is None:
        raise ValueError(f"Unknown participant {entity_id} / 参与对象不存在")
    return {"id": entity_id, "entity": copy.deepcopy(entity), "fleet": None, "place": None,
            "responsibilities": [], "workloads": [], "rules": copy.deepcopy(summary.get("rules", [])),
            "text": "Configured facts; no assignment or completion is implied.", "text_zh": "已配置事实；不代表任务分配或完成。"}


def explain_relation(document: dict, summary: dict, a: str, b: str) -> dict:
    participants = [explain_participant(document, summary, identity) for identity in (a, b)]
    members = {p["id"]: set(p["fleet"]["members"]) if p["fleet"] and p["id"] == p["fleet"]["id"] else {p["id"]} for p in participants}
    steps = [s for s in interactions(summary)["steps"] if s["from"] in members[a] | {a} and s["to"] in members[b] | {b} or s["from"] in members[b] | {b} and s["to"] in members[a] | {a}]
    workloads = [w for w in summary.get("workloads", []) if a in w["from"] + w["to"] and b in w["from"] + w["to"]]
    return {"a": a, "b": b, "steps": steps, "workloads": copy.deepcopy(workloads),
            "text": "Configured possible interactions; recorded events determine what happened." if steps or workloads else "No configured direct relation.",
            "text_zh": "这是已配置的可能交互；实际结果由记录事件决定。" if steps or workloads else "没有已配置的直接关系。"}


def _leaf_changes(before: Any, after: Any, prefix: str = "", *, expand=(), indexed=()) -> list[dict]:
    """Changed leaves as dotted names; display labels are derived and never a change of their own."""
    if isinstance(before, dict) and isinstance(after, dict) or prefix.startswith(expand) and (isinstance(before, dict) and after is None or before is None and isinstance(after, dict)):
        before, after = before if before is not None else {}, after if after is not None else {}
        return [row for key in sorted(before.keys() | after.keys()) if key != "labels"
                for row in _leaf_changes(before.get(key), after.get(key), f"{prefix}.{key}" if prefix else key, expand=expand, indexed=indexed)]
    if prefix.startswith(indexed) and (isinstance(before, list) or isinstance(after, list)) and (before is None or after is None or isinstance(before, list) and isinstance(after, list) and len(before) == len(after)):
        before = before if before is not None else [None] * len(after)
        after = after if after is not None else [None] * len(before)
        return [row for index, (old, new) in enumerate(zip(before, after), 1)
                for row in _leaf_changes(old, new, f"{prefix}.{index}", expand=expand, indexed=indexed)]
    return [] if before == after else [{"name": prefix, "before": before, "after": after}]


def diff(before: dict, after: dict) -> dict:
    if "run" in before:
        before = summarize(before)
    if "run" in after:
        after = summarize(after)
    result: dict = {"added": [], "changed": [], "removed": [], "entities": {"added": [], "changed": [], "removed": []}}
    for section, kind in (("fleets", "fleet"), ("places", "place"), ("workloads", "workload"), ("events", "event"), ("rules", "rule")):
        old = {r["id"]: r for r in before.get(section, [])}
        new = {r["id"]: r for r in after.get(section, [])}
        for identity in sorted(new.keys() - old.keys()):
            result["added"].append({"kind": kind, "id": identity, "label": new[identity].get("label", identity), **({"label_zh": new[identity]["label_zh"]} if "label_zh" in new[identity] else {})})
        for identity in sorted(old.keys() - new.keys()):
            result["removed"].append({"kind": kind, "id": identity, "label": old[identity].get("label", identity), **({"label_zh": old[identity]["label_zh"]} if "label_zh" in old[identity] else {})})
        for identity in sorted(old.keys() & new.keys()):
            fields = [{"name": k, "before": old[identity].get(k), "after": new[identity].get(k)} for k in sorted(old[identity].keys() | new[identity].keys()) if old[identity].get(k) != new[identity].get(k)]
            if fields:
                result["changed"].append({"kind": kind, "id": identity, "label": new[identity].get("label", identity), **({"label_zh": new[identity]["label_zh"]} if "label_zh" in new[identity] else {}), "fields": fields})
    catalogue = OperationCatalogue()
    section_labels = metadata("section_labels", {}, catalogue=catalogue)
    mirrored_sections = metadata("mirrored", {}, catalogue=catalogue)
    expand = tuple(metadata("leaf_expand_prefixes", [], catalogue=catalogue))
    indexed = tuple(metadata("indexed_leaf_prefixes", [], catalogue=catalogue))
    for section in section_labels:
        old_value, new_value = before.get(section), after.get(section)
        if old_value == new_value:
            continue
        en, zh = section_labels[section]
        if old_value is None or new_value is None:
            # A whole section appearing or disappearing is one row; the scene cards show its content.
            result["added" if old_value is None else "removed"].append({"kind": "scene", "id": section, "label": en, "label_zh": zh})
            continue
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            mirrored = set(mirrored_sections.get(section, []))
            fields = _leaf_changes({k: v for k, v in old_value.items() if k not in mirrored}, {k: v for k, v in new_value.items() if k not in mirrored}, expand=expand, indexed=indexed)
        else:
            fields = [{"name": section, "before": old_value, "after": new_value}]
        if fields:
            result["changed"].append({"kind": "scene", "id": section, "label": en, "label_zh": zh, "fields": fields})
    old_ids, new_ids = set(before.get("entity_ids", [])), set(after.get("entity_ids", []))
    result["entities"]["added"] = sorted(new_ids - old_ids)
    result["entities"]["removed"] = sorted(old_ids - new_ids)
    result["entities"]["changed"] = sorted(identity for identity in old_ids & new_ids if before.get("entity_facts", {}).get(identity) != after.get("entity_facts", {}).get(identity))
    return enrich_diff(result)
