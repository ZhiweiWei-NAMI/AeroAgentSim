"""Deterministic JSON and Markdown reports; compilation readiness is never runtime evidence."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

from .core import Audit

SLICE_CONTRACTS = {
    "oo:UAV": "exp.contract.physical.uav_propulsor_operating_window",
    "oo:Order": "exp.contract.foundation.work_accepted_output",
    "oo:ObservationRecord": "exp.contract.governance.observation_delivery_latency",
}


def summarize(audit: Audit) -> None:
    statuses = audit.metrics.get("field_status", {})
    executable = audit.metrics.get("semantic_executable", {})
    inputs = audit.metrics.get("semantic_inputs", {})
    targets = {**audit.predicates, **audit.events}
    by_directory = []
    target_types = defaultdict(set)
    fields_by_type = {typ: audit.effective_fields(typ) for typ in audit.entities}
    types_by_field = defaultdict(set)
    descendants = defaultdict(set)
    for typ, fields in fields_by_type.items():
        for field in fields:
            types_by_field[field.id].add(typ)
        for parent in audit.ancestors(typ):
            descendants[parent].add(typ)
    for identity, row in targets.items():
        compatible = set()
        for field in inputs.get(identity, {}).get("fields", []):
            compatible.update(types_by_field[field])
        for parent in row.data.get("entityTypeIds", []):
            compatible.update(descendants[parent])
        for typ in compatible:
            target_types[typ].add(identity)
    for directory, ids in audit.metrics.get("directories", {}).items():
        if directory == "技术支持（不作业务分类）":
            continue
        typed = bound = predicates = no_fields = structurally_blocked = 0
        for typ in ids:
            rows = fields_by_type[typ]
            missing_own = any(
                f not in audit.fields
                for t in audit.ancestors(typ)
                for f in audit.entities[t].data.get("ownFieldIds", [])
            )
            if not rows:
                no_fields += 1
            if (
                rows
                and not missing_own
                and all(statuses.get(f.id, {}).get("typed_unit_resolved") for f in rows)
            ):
                typed += 1
            if any(statuses.get(f.id, {}).get("writer_bound") for f in rows):
                bound += 1
            if any(executable.get(t) for t in target_types[typ]):
                predicates += 1
            lineage = audit.ancestors(typ)
            field_ids = {f.id for f in rows}
            if any(
                f["severity"] == "blocker" and f["object_id"] in lineage | field_ids
                for f in audit.findings
            ):
                structurally_blocked += 1
        by_directory.append(
            {
                "directory": audit.metrics.get("directory_labels", {}).get(
                    directory, directory
                ),
                "types": len(ids),
                "all_effective_fields_typed_unit_resolved": typed,
                "at_least_one_writer_bound": bound,
                "at_least_one_statically_executable_predicate_event": predicates,
                "no_effective_fields": no_fields,
                "types_with_direct_descriptor_blockers": structurally_blocked,
            }
        )
    audit.metrics["runtime_directories"] = by_directory
    slices = {}
    for typ, contract in SLICE_CONTRACTS.items():
        if typ not in audit.entities:
            slices[typ] = {"present": False}
            continue
        ancestors = audit.ancestors(typ)
        fields = {f.id for f in audit.effective_fields(typ)}
        relations = {
            r.id
            for r in audit.relations.values()
            if r.data.get("sourceClass") in ancestors
            or r.data.get("targetClass") in ancestors
        }
        pred = "expanded:predicate:" + contract
        event = "expanded:event:" + contract + ":entered"
        support_fields = set(inputs.get(pred, {}).get("fields", []))
        support_relations = set(inputs.get(pred, {}).get("relations", []))
        ids = (
            ancestors
            | fields
            | relations
            | support_fields
            | support_relations
            | {pred, event, "expanded:rule:" + contract}
        )
        blockers = [
            f
            for f in audit.findings
            if f["severity"] == "blocker" and f["object_id"] in ids
        ]
        normalizations = [
            f
            for f in audit.findings
            if f["severity"] == "major"
            and f["object_id"] in ids
            and f["check"]
            in (
                "field.schema_conformance",
                "field.role_drift",
                "relation.schema_conformance",
                "relation.cardinality_dialect",
                "field.clock_missing",
            )
        ]
        unbound = sorted(
            f
            for f in fields | support_fields
            if not statuses.get(f, {}).get("writer_bound")
        )
        needs_review = sorted(
            f
            for f in fields | support_fields
            if audit.fields[f].data.get("integrationDisposition")
            in ("quarantined", "candidate_not_accepted")
            or audit.fields[f].data.get("reviewStatus") in ("proposed", "conflict")
        )
        slices[typ] = {
            "present": True,
            "ancestors": sorted(ancestors),
            "effective_field_ids": sorted(fields),
            "inherited_relation_ids": sorted(relations),
            "selected_contract": contract,
            "selected_predicate_id": pred,
            "selected_event_id": event,
            "selected_contract_present": pred in targets,
            "selected_contract_statically_executable": executable.get(pred, False),
            "additional_role_field_ids": sorted(support_fields - fields),
            "additional_role_relation_ids": sorted(support_relations - relations),
            "descriptor_blocker_finding_ids": [f["id"] for f in blockers],
            "normalization_finding_ids": [f["id"] for f in normalizations],
            "unbound_writer_field_ids": unbound,
            "fields_requiring_explicit_review_policy": needs_review,
            "minimal_actions": [
                "Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.",
                "Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.",
                f"Bind one real producer per selected instance-field ({len(unbound)} field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.",
                "Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.",
                "Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.",
                "Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.",
            ],
        }
    audit.metrics["vertical_slices"] = slices
    audit.metrics["runtime_method"] = (
        "Own fields + adopted actual inheritance only. Nonempty fields required for (a); typed/unit-resolved does not imply schema conformity, approved semantics, clocks or descriptor compilation. (b) requires bound status plus concrete producer identity. (c) is conservative static input/AST readiness with field applicability or explicit type applicability, potentially multiple independent role producers; not live evaluation, producer integration, semantic acceptance or proof of all native operator semantics."
    )


def table(headers, rows):
    result = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    result.extend(
        "| "
        + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in row)
        + " |"
        for row in rows
    )
    return "\n".join(result)


def markdown(audit: Audit, git: dict, examples: int = 3) -> str:
    severities = Counter(f["severity"] for f in audit.findings)
    checks = defaultdict(list)
    for finding in audit.findings:
        checks[finding["check"]].append(finding)
    original = audit.metrics.get("original", {})
    semantic = audit.metrics.get("semantic", {})
    profiles = audit.metrics.get("capability_profiles", {})
    lines = [
        "# AeroGraph runtime-registry audit",
        "",
        f"Source: `{audit.root}`. HEAD: `{git.get('head')}`. Dirty: **{git.get('dirty')}**.",
        "",
        "Read-only audit: no upstream builds, imports, tests, normalization writes, simulation or observation acquisition. Findings describe this working tree, including uncommitted source edits; input SHA-256 inventory is in the JSON report. Stable finding IDs derive from content. No wall-clock timestamp is injected.",
        "",
        "```text",
        git.get("status_porcelain")
        if git.get("status_porcelain")
        else "(clean)"
        if git.get("dirty") is False
        else "(git status unavailable)",
        "```",
        "",
        "## Summary",
        "",
        table(
            ["Severity", "Finding records", "Affected occurrences"],
            [
                (
                    s,
                    severities[s],
                    sum(f["count"] for f in audit.findings if f["severity"] == s),
                )
                for s in ("blocker", "major", "minor", "info")
            ],
        ),
        "",
        table(
            ["Inventory", "Recomputed"],
            [
                ("Entity identities", len(audit.entities)),
                ("Source fields", audit.metrics.get("source_field_count")),
                ("Source + extension fields", len(audit.fields)),
                ("Relations", len(audit.relations)),
                (
                    "Original predicates/events/root rules",
                    f"{original.get('predicates')}/{original.get('events')}/{original.get('root_rules')}",
                ),
                (
                    "Authored/domain + original rules/predicates/events",
                    f"{semantic.get('rules')}/{semantic.get('predicates')}/{semantic.get('events')}",
                ),
                ("Capability mapping reconstruction", profiles.get("count")),
                (
                    "Types with mapping (includes unadopted)",
                    profiles.get("covered_types"),
                ),
                (
                    "Embedded samples / sampled original targets",
                    f"{original.get('embedded_samples')}/{original.get('sampled_targets')}",
                ),
                (
                    "Statically executable supplied targets",
                    semantic.get("statically_executable_targets"),
                ),
            ],
        ),
        "",
        "Finding records and occurrence counts differ: a schema finding can include multiple failed keys; a shared inheritance conflict can affect multiple types. These are defects/requirements, not failed simulation runs. Historical 11,778 validations are preserved metadata, not results of this audit.",
        "",
        "## Runtime-compilability by directory",
        "",
        table(
            [
                "Directory",
                "Types",
                "(a) all fields typed + units resolved",
                "(b) any bound writer",
                "(c) any static predicate/event",
                "No fields",
                "Descriptor blockers",
            ],
            [
                (
                    r["directory"],
                    r["types"],
                    r["all_effective_fields_typed_unit_resolved"],
                    r["at_least_one_writer_bound"],
                    r["at_least_one_statically_executable_predicate_event"],
                    r["no_effective_fields"],
                    r["types_with_direct_descriptor_blockers"],
                )
                for r in audit.metrics["runtime_directories"]
            ],
        ),
        "",
        audit.metrics["runtime_method"],
        "",
        semantic.get("scope", ""),
        "",
        "Source-generated leaf-state contracts and browser `metadata.integration.expandedCoverage` are not persisted inputs. They are excluded rather than fabricated or counted by running upstream builders. An independent second semantic entity inventory is likewise unavailable: referenced identities are checked against the catalog that the generator reads.",
        "",
        "## Capability and identity claims",
        "",
        table(
            ["Mapping kind", "Recomputed"], sorted(profiles.get("kinds", {}).items())
        ),
        "",
        profiles.get("method", ""),
        "",
        f"Information identities: `{audit.metrics.get('information_identities')}`. Actual roots/detached identities: `{len(audit.metrics.get('hierarchy', {}).get('roots', []))}`. Seven browsing directories and explicit cross-index membership remain separate from actual is-a. The 22 unattached profiles remain unadopted. Six EM candidates with suggested parents are not silently attached.",
        "",
        "## Minimal vertical-slice fixes",
        "",
        "The table selects one existing multi-role domain contract per requested type. It includes all adopted inherited fields/relations plus that contract’s additional inputs. It does not claim that registering descriptors binds producers. Fixing a globally shared root descriptor can unblock all three slices. Full field/relation IDs, blocker IDs and required actions are in JSON `metrics.vertical_slices`.",
        "",
        table(
            [
                "Type",
                "Inherited effective fields",
                "Inherited endpoint relations",
                "Additional role fields",
                "Descriptor blocker records",
                "Unbound selected field descriptors",
                "Selected contract static readiness",
            ],
            [
                (
                    t,
                    len(v.get("effective_field_ids", [])),
                    len(v.get("inherited_relation_ids", [])),
                    len(v.get("additional_role_field_ids", [])),
                    len(v.get("descriptor_blocker_finding_ids", [])),
                    len(v.get("unbound_writer_field_ids", [])),
                    v.get("selected_contract_statically_executable"),
                )
                for t, v in audit.metrics["vertical_slices"].items()
            ],
        ),
        "",
    ]
    findings_by_id = {f["id"]: f for f in audit.findings}
    for typ, slice_ in audit.metrics["vertical_slices"].items():
        lines.extend([f"### {typ}", ""])
        if not slice_.get("present"):
            lines.append("Requested type is absent.")
            continue
        lines.append(
            f"Selected contract: `{slice_['selected_contract']}`. Actual ancestry: "
            + ", ".join(f"`{t}`" for t in slice_["ancestors"])
            + "."
        )
        lines.append("")
        grouped = defaultdict(list)
        for identity in slice_["descriptor_blocker_finding_ids"]:
            f = findings_by_id[identity]
            grouped[f["check"]].append(f)
        if grouped:
            lines.append(
                table(
                    ["Fix upstream / explicit binding", "Affected records", "Examples"],
                    [
                        (
                            k,
                            len(v),
                            "; ".join(
                                f"{f['object_id']}: {f['message']}"
                                for f in v[:examples]
                            ),
                        )
                        for k, v in sorted(grouped.items())
                    ],
                )
            )
            lines.append("")
        lines.extend(
            str(i + 1) + ". " + action
            for i, action in enumerate(slice_["minimal_actions"])
        )
        lines.append("")
    lines.extend(
        [
            "## Recommended normalization rules for the kernel's AeroGraph loader",
            "",
            table(
                [
                    "Loader may normalize from explicit source evidence",
                    "Must be supplied/fixed upstream or in the selected run manifest",
                ],
                [
                    (
                        "configuration → config; specification → spec; retain raw role and provenance",
                        "Unknown roles, incompatible declarations and duplicate IDs require reviewed resolution.",
                    ),
                    (
                        "Resolve local #/$defs refs with JSON Pointer escaping; object → record; enum/oneOf/anyOf retain branches and nullability",
                        "Missing/cyclic schema refs, empty enums and absent reference targets require source contracts.",
                    ),
                    (
                        "unit.status explicit → exact only with an explicit usable symbol; canonicalize exact equivalent aliases (byte/By, Cel/degC)",
                        "Unresolved units, different scales, affine/log quantities and dynamic mixed-unit records need explicit conversions/context; no missing → dimensionless.",
                    ),
                    (
                        "String frame → a preserved declaration requiring instance frame reference; do not invent a frame",
                        "Concrete frame identity, datum/origin, transform revision and time mapping must be bound.",
                    ),
                    (
                        "Normalize targets_per_source minimum/maximum and sources_per_target independently",
                        "A null maximum is unbounded only where source scope explicitly states that meaning; otherwise unresolved. Enforce forward/inverse cardinalities during valid-time intervals.",
                    ),
                    (
                        "Keep seven browsing categories, cross-indexes, actual parents, suggested parents and original proposed fields distinct",
                        "Do not attach the 22 profiles or six suggested EM parents without a reviewed identity/contract change.",
                    ),
                    (
                        "Keep Own/Oi producer aliases and scoped p defaults; b executes inline AST while r executes canonical target",
                        "Bind actual producers/subject instances. A kind named bound_source_producer or prose description is insufficient. Never use sample/default values as observations.",
                    ),
                    (
                        "Preserve explicit null branches and unknown-valued source enums; flag an entirely null root without fabricating truth",
                        "Compile specialized geometry/graph/time operators with their actual semantics; declare windows, clocks, sampling gaps and same-identity history.",
                    ),
                    (
                        "Retain review, quarantine, original ASTs, source citations and historical validation records",
                        "Explicit research selection is required for unadopted contracts; historical validation totals are not current acceptance evidence.",
                    ),
                ],
            ),
            "",
            "## Findings by check",
            "",
            table(
                ["Check", "Severity distribution", "Records", "Occurrences"],
                [
                    (
                        k,
                        ", ".join(
                            f"{s}:{n}"
                            for s, n in sorted(
                                Counter(f["severity"] for f in v).items()
                            )
                        ),
                        len(v),
                        sum(f["count"] for f in v),
                    )
                    for k, v in sorted(checks.items())
                ],
            ),
            "",
        ]
    )
    for check, rows in sorted(checks.items()):
        lines.extend([f"### {check}", ""])
        for finding in rows[:examples]:
            evidence = finding["evidence"]
            path = audit.root / evidence["path"]
            lines.append(
                f"- **{finding['severity']}** `{finding['object_id'] or finding['artifact']}`: {finding['message']} Evidence: [{evidence['path']}]({path}) `{evidence['pointer'] or '/'}`. ID `{finding['id']}`; count {finding['count']}."
            )
        lines.append("")
    lines.extend(
        [
            "## Method and limits",
            "",
            "All JSON inputs are loaded with stdlib JSON; the tool never imports upstream Python/JavaScript. The bundled unit-inference helper is an audit-owned copy of the upstream pure-stdlib `expanded_units.py`, identified in the tool README; it is used for supplied object ASTs. Native AST checks inspect operands, index references, applicability, scoped defaults and canonical execution cycles. Null in a guarded native branch remains an unknown result, not a broken registry. Complex native operators without a complete audit-owned proof are marked unproven and excluded from static readiness. No type is counted as live-executable solely because it has a profile or field.",
            "",
            "GLM delegation for this implementation: two concurrent WorkBuddy DSH sessions were started with `workbuddy/glm-5.3-flash`, maxTokens `131072`, no effort argument; both exited with TRANSPORT errors before producing artifacts. The audit results and tests were completed locally.",
            "",
        ]
    )
    return "\n".join(lines)


def payload(audit: Audit, git: dict) -> dict:
    metrics = {k: v for k, v in audit.metrics.items() if k != "resolved_schemas"}
    return {
        "format": "aerograph-runtime-audit/v1",
        "source_root": str(audit.root),
        "git": git,
        "inventory": dict(sorted(audit.inventory.items())),
        "summary": {
            "finding_records": len(audit.findings),
            "severity_records": dict(
                sorted(Counter(f["severity"] for f in audit.findings).items())
            ),
            "severity_occurrences": {
                s: sum(f["count"] for f in audit.findings if f["severity"] == s)
                for s in ("blocker", "major", "minor", "info")
            },
        },
        "metrics": metrics,
        "findings": audit.findings,
    }


def write(audit: Audit, git: dict, output, examples: int = 3) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "aerograph-audit.json").write_text(
        json.dumps(payload(audit, git), ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    (output / "aerograph-audit.md").write_text(
        markdown(audit, git, examples), encoding="utf-8"
    )
