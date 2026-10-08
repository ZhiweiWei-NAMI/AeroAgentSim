"""Compile-scope dependency closure, separate from corpus review findings."""

from collections import Counter


def summarize(audit, types):
    unknown = sorted(set(types) - audit.entities.keys())
    if unknown:
        raise ValueError("Selected entity types do not exist: " + ", ".join(unknown))
    inputs = audit.metrics.get("semantic_inputs", {})
    registry = {**audit.rules, **audit.predicates, **audit.events}
    slices = {}
    union_blockers = {}
    for typ in sorted(set(types)):
        ancestry = audit.ancestors(typ)
        fields = {r.id for r in audit.effective_fields(typ)}
        relations = {
            r.id
            for r in audit.relations.values()
            if r.data.get("sourceClass") in ancestry
            or r.data.get("targetClass") in ancestry
        }
        contracts = set()
        for identity, row in registry.items():
            applicability = set(row.data.get("entityTypeIds", []))
            role_types = {
                t for role in row.data.get("roles", []) for t in role.get("typeIds", [])
            }
            typed_scope = applicability | role_types
            if typed_scope:
                # Shared support-role identity fields do not make a contract
                # about a different type apply to this selected type.
                relevant = bool(ancestry & typed_scope)
            else:
                relevant = bool(
                    fields.intersection(inputs.get(identity, {}).get("fields", []))
                    or relations.intersection(
                        inputs.get(identity, {}).get("relations", [])
                    )
                )
            if relevant:
                contracts.add(identity)
        for identity in list(contracts):
            contracts.update(inputs.get(identity, {}).get("dependencies", []))
        support_fields = {
            f
            for identity in contracts
            for f in inputs.get(identity, {}).get("fields", [])
        }
        support_relations = {
            r
            for identity in contracts
            for r in inputs.get(identity, {}).get("relations", [])
        }
        scope = (
            ancestry
            | fields
            | relations
            | contracts
            | support_fields
            | support_relations
        )
        findings = []
        for finding in audit.findings:
            affected = finding.get("details")
            shared = isinstance(affected, dict) and bool(
                ancestry.intersection(affected.get("affected_types", []))
            )
            # Snapshot failures apply to every compilation; global archival
            # statistics and unrelated contract defects do not.
            global_input = (
                finding["check"].startswith("input.")
                and finding["contract_severity"] == "blocker"
            )
            if finding["object_id"] in scope or shared or global_input:
                findings.append(
                    {
                        **finding,
                        "corpus_severity": finding["severity"],
                        "severity": finding["contract_severity"],
                    }
                )
        blockers = [f for f in findings if f["severity"] == "blocker"]
        union_blockers.update({f["id"]: f for f in blockers})
        slices[typ] = {
            "ancestors": sorted(ancestry),
            "effective_field_ids": sorted(fields),
            "endpoint_relation_ids": sorted(relations),
            "contract_ids": sorted(contracts),
            "additional_contract_field_ids": sorted(support_fields - fields),
            "additional_contract_relation_ids": sorted(support_relations - relations),
            "finding_ids": [f["id"] for f in findings],
            "severity_records": dict(
                sorted(Counter(f["severity"] for f in findings).items())
            ),
            "blockers": blockers,
            "unbound_writer_field_ids": sorted(
                f
                for f in fields | support_fields
                if not audit.metrics.get("field_status", {})
                .get(f, {})
                .get("writer_bound")
            ),
            "gated_finding_ids": [f["id"] for f in findings if f.get("gated")],
        }
    audit.metrics["selection"] = {
        "types": sorted(set(types)),
        "slices": slices,
        "blocker_records": len(union_blockers),
        "blocker_occurrences": sum(f["count"] for f in union_blockers.values()),
        "blockers": sorted(union_blockers.values(), key=lambda f: f["id"]),
        "method": "Own/adopted inherited fields, incident relations and contracts "
        "in the declared type/role scope; unscoped contracts are selected by "
        "field/relation consumption. Transitive inputs are included. "
        "Selection exposes contract defects, including gated or preserved "
        "definitions; it does not approve them or bind producers.",
    }
