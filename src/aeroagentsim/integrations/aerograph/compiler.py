"""Compile an explicit ontology slice without importing the upstream project."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aerokernel.errors import KernelError
from aerokernel.registry import FieldDescriptor, MemoryRegistry, TypeDescriptor
from aerokernel.values import canonical_json

from .model import COMPILER_VERSION, CompiledRegistry, CompileError, Policy, Selection
from .normalize import Normalizer, cardinality
from .source import (
    PROPOSAL_PREFIX,
    Record,
    Sources,
    proposal_candidate,
    review_gated,
    review_status,
)


def review_admission(
    id: str, data: dict[str, Any], policy: Policy, kind: str
) -> tuple[bool, str, bool]:
    """Decide compile admission from explicit source review state.

    ``reviewed`` and ``proposed`` definitions are admitted by default.
    ``reviewStatus: conflict`` is excluded with reason ``review conflict``
    until individually admitted, and unknown statuses always fail. IDs in
    the ``proposal:`` namespace stay excluded unless ``--admit-proposed`` or
    an individual ``--admit`` admits them. ``--strict-reviewed`` admits only
    definitions declaring ``reviewed``; types that declare no review state
    remain structural ancestry, while fields and relations must declare
    ``reviewed``. The returned gate flag reports source review markers that
    admission must preserve; it never grants authority.
    """
    status = review_status(data)
    candidate = proposal_candidate(data)
    if candidate or status not in (None, "reviewed", "proposed"):
        gate = review_gated(data)
        if id in policy.admitted_ids:
            return True, "", gate
        if candidate and policy.admit_proposed:
            return True, "", gate
        if candidate:
            return False, "proposal namespace candidate", gate
        if status == "conflict":
            return False, "review conflict", gate
        return False, "unsupported review status " + str(status), gate
    if policy.strict_reviewed:
        blocked = status == "proposed" or (
            status is None and kind in ("field", "relation")
        )
        if blocked:
            if id in policy.admitted_ids:
                return True, "", False
            return False, "review status " + (status or "undeclared"), False
    return True, "", review_gated(data)


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
    review_counts: dict[str, int] = {"reviewed": 0, "proposed": 0, "undeclared": 0}

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
        if row is None:
            return True
        admitted, reason, gate = review_admission(id, row.data, policy, kind)
        if not admitted:
            excluded(id, kind, reason + "; explicit research admission required", row)
            return False
        if gate:
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
        return True

    def auxiliary_type_allowed(tid: str) -> bool:
        """May a compiled definition depend on auxiliary type ``tid``?

        A ``proposal:`` candidate target of an admitted active definition is
        recorded as an exclusion instead of failing the slice; the consuming
        field/relation must then exclude itself. Any other unresolved or
        excluded dependency still fails through ``add_type``.
        """
        if not tid.startswith(PROPOSAL_PREFIX):
            return True
        if tid in types:
            return True
        if tid in policy.admitted_ids or policy.admit_proposed:
            return True
        if ("type", tid) not in exclusions:
            try:
                row = Sources.get(sources.types, tid, "type")
            except CompileError:
                return False
            excluded(tid, "type", "proposal namespace candidate", row)
        return False

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
            metadata["research_admitted"] = review_gated(row.data)
            metadata["reviewStatus"] = review_status(row.data)
            # A proposal candidate target stays out of the registry unless
            # admitted; the kernel rejects dangling ref targets, so the
            # consuming field excludes itself with an explicit reason.
            blocked = next(
                (
                    t
                    for t in sorted(normalizer.references)
                    if not auxiliary_type_allowed(t)
                ),
                None,
            )
            if blocked is not None:
                excluded(
                    fid,
                    "field",
                    f"requires unadmitted proposal candidate type {blocked!r}",
                    row,
                )
                admissions[:] = [
                    a
                    for a in admissions
                    if not (a["id"] == fid and a["kind"] == "field")
                ]
                continue
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
            fid_status = review_status(row.data)
            review_counts[
                fid_status if fid_status in ("reviewed", "proposed") else "undeclared"
            ] += 1
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
            blocked = next(
                (t for t in (source, target) if not auxiliary_type_allowed(t)), None
            )
            if blocked is not None:
                excluded(
                    rid,
                    "relation",
                    f"requires unadmitted proposal candidate type {blocked!r}",
                    row,
                )
                admissions[:] = [
                    a
                    for a in admissions
                    if not (a["id"] == rid and a["kind"] == "relation")
                ]
                continue
            add_type(source)
            add_type(target)
            relations.append(
                {
                    "id": rid,
                    "source_type": source,
                    "target_type": target,
                    "cardinality": bounds,
                    "raw": row.data,
                    "research_admitted": review_gated(row.data),
                }
            )
            provenance["relations"][rid] = row.location()
            rid_status = review_status(row.data)
            review_counts[
                rid_status if rid_status in ("reviewed", "proposed") else "undeclared"
            ] += 1
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
            "exclusions": exclusion_list,
            "admissions": admissions,
            "review": {
                "admitted_by_status": dict(sorted(review_counts.items())),
                "admitted_unreviewed": (
                    review_counts.get("proposed", 0)
                    + review_counts.get("undeclared", 0)
                ),
            },
            "provenance": {
                "descriptors": provenance,
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
                "admitted_reviewed_definitions": review_counts["reviewed"],
                "admitted_proposed_definitions": review_counts["proposed"],
                "admitted_unreviewed_definitions": (
                    review_counts["proposed"] + review_counts["undeclared"]
                ),
                "compile_blockers": 0,
            },
        },
    )
