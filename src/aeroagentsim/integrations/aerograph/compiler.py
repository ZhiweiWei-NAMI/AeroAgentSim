"""Compile an explicit ontology slice without importing the upstream project."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aerokernel.errors import KernelError
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.values import canonical_json

from .model import COMPILER_VERSION, CompiledRegistry, CompileError, Policy, Selection
from .normalize import Normalizer, cardinality
from .source import Record, Sources, gated


def compile_registry(
    aerograph_root: Path | str,
    selection: Selection | Iterable[str],
    policy: Policy | None = None,
) -> CompiledRegistry:
    """Compile actual inheritance, effective fields and incident relations.

    Missing selected IDs, duplicate definitions and unsupported selected
    contracts fail with source locations. Unselected schema defects do not
    affect the slice. No writers, frames or live observations are synthesized.
    """
    if isinstance(selection, str):
        raise TypeError("Pass Selection or an iterable of full type IDs, not a string")
    selection = (
        selection if isinstance(selection, Selection) else Selection(tuple(selection))
    )
    policy = Policy() if policy is None else policy
    sources = Sources(aerograph_root)
    normalizations: list[dict[str, Any]] = []
    exclusions: dict[tuple[str, str], dict[str, Any]] = {}
    admissions: list[dict[str, Any]] = []
    provenance: dict[str, dict[str, Any]] = {"types": {}, "fields": {}, "relations": {}}
    raw_types: dict[str, Any] = {}
    hints: dict[str, Any] = {}
    types: dict[str, TypeDescriptor] = {}
    ancestors: dict[str, set[str]] = {}
    fields: dict[str, FieldDescriptor] = {}
    field_rows: dict[str, Record] = {}
    effective: dict[str, list[str]] = {}
    relations: list[dict[str, Any]] = []
    diagnostics: list[str] = []

    def excluded(id: str, kind: str, reason: str, row: Record | None = None) -> None:
        exclusions[kind, id] = {
            "id": id,
            "kind": kind,
            "reason": reason,
            "source": None if row is None else row.location(),
        }

    def permitted(id: str, kind: str, row: Record | None = None) -> bool:
        if id in policy.excluded_ids:
            excluded(id, kind, "explicit policy exclusion", row)
            return False
        if row is not None and gated(row.data):
            if policy.admit_proposed or id in policy.admitted_ids:
                admission = {
                    "id": id,
                    "kind": kind,
                    "source": row.location(),
                    "research_admitted": True,
                    "reviewStatus": row.data.get("reviewStatus"),
                    "integrationDisposition": row.data.get("integrationDisposition"),
                }
                if admission not in admissions:
                    admissions.append(admission)
            else:
                excluded(
                    id,
                    kind,
                    "quarantined source; explicit research admission required",
                    row,
                )
                return False
        return True

    def log_for(row: Record) -> Any:
        def log(rule: str, pointer: str, before: Any, after: Any) -> None:
            normalizations.append(
                {
                    "id": row.data["id"],
                    "rule": rule,
                    "file": row.file,
                    "pointer": row.pointer + pointer,
                    "before": before,
                    "after": after,
                }
            )

        return log

    def add_type(id: str, trail: tuple[str, ...] = ()) -> set[str]:
        if id in trail:
            raise CompileError([f"Inheritance cycle: {' -> '.join((*trail, id))}"])
        if id in ancestors:
            return ancestors[id]
        row = Sources.get(sources.types, id, "type")
        if not permitted(id, "type", row):
            raise row.error(
                f"Type {id!r} is excluded/quarantined but required by an active definition; "
                "exclude the consuming definition or explicitly admit the dependency"
            )
        data = row.data
        if type(data.get("abstract")) is not bool:
            raise row.error("Type requires an explicit boolean abstract flag")
        if "parents" in data and "parent" in data:
            raise row.error("Specify parent or parents, not both")
        if "parent" not in data and "parents" not in data:
            raise row.error(
                "Type requires explicit parent (null for a root) or parents"
            )
        parents = data.get(
            "parents", [data["parent"]] if data.get("parent") is not None else []
        )
        if not isinstance(parents, list) or any(
            not isinstance(p, str) or not p for p in parents
        ):
            raise row.error("Actual parents must be explicit type IDs")
        if data.get("parentStatus") == "suggested":
            log_for(row)(
                "type.suggested_parent_excluded",
                "/parent" if "parent" in data else "/parents",
                data.get("parent", data.get("parents")),
                None,
            )
            parents = []
        closure = {id}
        for parent in sorted(parents):
            closure.update(add_type(parent, trail + (id,)))
        descriptor = TypeDescriptor(id, tuple(sorted(parents)), data["abstract"])
        types[id], ancestors[id] = descriptor, closure
        provenance["types"][id] = row.location()
        raw_types[id] = data
        return closure

    selected_types: list[str] = []
    for id in selection.type_ids:
        if not permitted(id, "type"):
            continue
        try:
            row = Sources.get(sources.types, id, "type")
            if permitted(id, "type", row):
                add_type(id)
                selected_types.append(id)
        except CompileError as exc:
            diagnostics.extend(exc.diagnostics)
    if diagnostics:
        raise CompileError(diagnostics)
    field_scope: set[str] = set().union(*(ancestors[id] for id in selected_types))
    applicable: dict[str, set[str]] = {}
    # ownFieldIds is the adopted inventory. Unattached profiles and capability
    # suggestions are never activated through a declaringClass match alone.
    for id in selected_types:
        ids: set[str] = set()
        for ancestor in sorted(ancestors[id]):
            row = Sources.get(sources.types, ancestor, "type")
            own = row.data.get("ownFieldIds")
            if not isinstance(own, list) or any(not isinstance(f, str) for f in own):
                diagnostics.append(
                    f"{row.file}#{row.pointer}/ownFieldIds: expected field ID array"
                )
                continue
            ids.update(own)
        if selection.field_ids is not None:
            ids &= set(selection.field_ids)
            # Explicit allow-list may activate persisted additional fields of
            # an actual ancestor, but cannot attach a profile/capability.
            for fid in selection.field_ids:
                rows = sources.fields.get(fid, [])
                if (
                    len(rows) == 1
                    and rows[0].data.get("declaringClass") in ancestors[id]
                ):
                    ids.add(fid)
        applicable[id] = ids
    candidates = set().union(*applicable.values())
    if selection.field_ids is not None:
        for fid in set(selection.field_ids) - candidates:
            if permitted(fid, "field"):
                diagnostics.append(
                    f"Selected field {fid!r} is absent or not declared by the selected actual ancestry; "
                    "supply a definition or list it in Policy.excluded_ids"
                )
    for fid in sorted(candidates):
        if not permitted(fid, "field"):
            continue
        try:
            row = Sources.get(sources.fields, fid, "field")
            if not permitted(fid, "field", row):
                continue
            declaring = row.data.get("declaringClass")
            if declaring not in field_scope:
                raise row.error(
                    f"declaringClass {declaring!r} is outside actual selected ancestry"
                )
            for id, ids in applicable.items():
                if fid in ids and declaring not in ancestors[id]:
                    raise row.error(
                        f"Field listed by {id!r} but its declaringClass is not an ancestor"
                    )
            normalizer = Normalizer(row, sources.definitions, log_for(row))
            schema = normalizer.schema(row.data.get("valueSchema"))
            metadata = normalizer.metadata(schema)
            metadata["research_admitted"] = gated(row.data)
            for reference_type in sorted(normalizer.references):
                add_type(reference_type)
            fields[fid] = FieldDescriptor(fid, declaring, schema, metadata)
            field_rows[fid] = row
            provenance["fields"][fid] = row.location()
            hint = {
                k: row.data[k]
                for k in (
                    "writer",
                    "ownerSubject",
                    "roleAliases",
                    "roleAlias",
                    "producerAliases",
                    "producerRole",
                    "sourceRole",
                    "aliases",
                    "legacyRefs",
                )
                if k in row.data
            }
            hints[fid] = {
                "authority": False,
                "source": row.location(),
                "descriptions": hint,
            }
        except (CompileError, KernelError) as exc:
            diagnostics.extend(
                exc.diagnostics
                if isinstance(exc, CompileError)
                else [f"Field {fid!r}: kernel rejected contract: {exc}"]
            )
    # Resolve each effective property slot. A different field ID is not an
    # implicit override; override intent and real ancestry must be explicit.
    for id in selected_types:
        by_path: dict[str, list[str]] = {}
        for fid in sorted(applicable[id] & fields.keys()):
            path = field_rows[fid].data.get("propertyPath", fid)
            if not isinstance(path, str) or not path:
                diagnostics.append(
                    f"Field {fid!r}: propertyPath must be a nonempty string"
                )
                continue
            by_path.setdefault(path, []).append(fid)
        result = []
        for path, slot_ids in sorted(by_path.items()):
            survivors = set(slot_ids)
            for fid in slot_ids:
                overrides = field_rows[fid].data.get("overrides", [])
                overrides = [overrides] if isinstance(overrides, str) else overrides
                if not isinstance(overrides, list) or any(
                    not isinstance(x, str) for x in overrides
                ):
                    diagnostics.append(f"Field {fid!r}: overrides requires field IDs")
                    continue
                for old in overrides:
                    if (
                        old not in slot_ids
                        or fields[old].declaring_type == fields[fid].declaring_type
                        or fields[old].declaring_type
                        not in ancestors[fields[fid].declaring_type]
                    ):
                        diagnostics.append(
                            f"Field {fid!r}: invalid override {old!r} for effective property {path!r}"
                        )
                    else:
                        survivors.discard(old)
                        log_for(field_rows[fid])(
                            "field.explicit_override",
                            "/overrides",
                            old,
                            {"effective_type": id, "field": fid},
                        )
            if len(survivors) != 1:
                diagnostics.append(
                    f"Type {id!r}: conflicting property {path!r}: {sorted(survivors)}; "
                    "author an explicit overrides field ID or exclude the unwanted definition"
                )
            result.extend(sorted(survivors))
        effective[id] = sorted(result)
    # Match endpoints against the selected actual ancestry, before auxiliary
    # types are added. Auxiliary endpoint fields never expand the slice.
    relation_candidates = {
        rid
        for rid, rows in sources.relations.items()
        if any(
            r.data.get("sourceClass") in field_scope
            or r.data.get("targetClass") in field_scope
            for r in rows
        )
    }
    if selection.relation_ids is not None:
        for rid in set(selection.relation_ids) - relation_candidates:
            if permitted(rid, "relation"):
                diagnostics.append(
                    f"Selected relation {rid!r} is absent or does not intersect selected ancestry; "
                    "supply endpoints or explicitly exclude it"
                )
        relation_candidates &= set(selection.relation_ids)
    for rid in sorted(relation_candidates):
        if not permitted(rid, "relation"):
            continue
        try:
            row = Sources.get(sources.relations, rid, "relation")
            if not permitted(rid, "relation", row):
                continue
            source, target = row.data.get("sourceClass"), row.data.get("targetClass")
            if not isinstance(source, str) or not isinstance(target, str):
                raise row.error(
                    "Relation requires explicit sourceClass/targetClass IDs"
                )
            if not row.data.get("validTime"):
                raise row.error("Relation requires a validity-time declaration")
            bounds = cardinality(row, log_for(row))
            add_type(source)
            add_type(target)
            relations.append(
                {
                    "id": rid,
                    "source_type": source,
                    "target_type": target,
                    "cardinality": bounds,
                    "raw": row.data,
                    "research_admitted": gated(row.data),
                }
            )
            provenance["relations"][rid] = row.location()
        except (CompileError, KernelError) as exc:
            diagnostics.extend(
                exc.diagnostics
                if isinstance(exc, CompileError)
                else [f"Relation {rid!r}: {exc}"]
            )
    if diagnostics:
        raise CompileError(diagnostics)
    try:
        registry = MemoryRegistry(
            tuple(types.values()),
            tuple(fields.values()),
            revision="aerograph-compiler/" + COMPILER_VERSION,
        )
    except KernelError as exc:
        raise CompileError(
            [
                (
                    f"Kernel schema compilation rejected selected descriptors: {exc}; "
                    "check the source valueSchema or explicitly exclude the field"
                )
            ]
        ) from exc
    normalizations.sort(key=lambda n: canonical_json(n))
    admissions.sort(key=lambda n: (n["kind"], n["id"]))
    exclusion_list = [exclusions[key] for key in sorted(exclusions)]
    return CompiledRegistry(
        registry,
        {
            "selection": selection.to_data(),
            "policy": policy.to_data(),
            "selected_types": selected_types,
            "effective_fields": effective,
            "relations": relations,
            "raw_types": raw_types,
            "producer_hints": hints,
            "normalizations": normalizations,
            "normalization_digest": hashlib.sha256(
                canonical_json(normalizations)[:-1]
            ).hexdigest(),
            "exclusions": exclusion_list,
            "admissions": admissions,
            "provenance": {
                "descriptors": provenance,
                "input_sha256": dict(sorted(sources.hashes.items())),
                "git": sources.git_provenance(),
            },
            "statistics": {
                "selected_types": len(selected_types),
                "types": len(types),
                "ancestry_types": len(field_scope),
                "fields": len(fields),
                "relations": len(relations),
                "normalizations": len(normalizations),
                "exclusions": len(exclusion_list),
                "research_admissions": len(admissions),
                "compile_blockers": 0,
            },
        },
    )
