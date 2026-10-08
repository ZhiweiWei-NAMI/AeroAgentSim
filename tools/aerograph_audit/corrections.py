"""Reviewable v2 rule decisions and a source-owner repair queue."""

BASELINE_RECORDS = {"blocker": 431, "major": 9832, "minor": 0, "info": 1034}
BASELINE_OCCURRENCES = {"blocker": 431, "major": 17015, "minor": 0, "info": 1034}

DECISIONS = [
    (
        "Additional source-backed time/identity typing",
        "Accepted after raw verification",
        "Incident occurrence time fields explicitly say 所有秒值使用明确来源时钟 "
        "(part-003.json /595); the upstream adapter attaches seconds to "
        "atS/startS/endS/firstObservedAtS from that quotation. Mirror that "
        "annotation with unitBasis, never suffix guessing. Pure string/full-"
        "InstanceRef wrappers need no sample time. Typed contracts for unrelated "
        "roles are excluded from selection despite shared identity fields.",
    ),
    (
        "1. Directional cardinality",
        "Accepted",
        "Explicit null maximum means unbounded in snake/camel directional bounds, "
        "independently of scope prose. Missing maximum remains invalid. Keep one "
        "dialect finding and remove its duplicate min/max schema errors.",
    ),
    (
        "2. Count compatibility",
        "Accepted with limits",
        "count and 1, count/s and 1/s are compatible at equal physical dimensions, "
        "scale and flavor. Different counting identities (packet/person) remain "
        "incompatible; opaque/log units are not dimensionless.",
    ),
    (
        "3a. norm typing",
        "Accepted",
        "Quaternion norm is dimensionless; ordinary vector norm uses numeric "
        "component units. No general exemption for arbitrary opaque "
        "representations.",
    ),
    (
        "3b. contains typing",
        "Accepted",
        "Record members/items do not inherit a container scalar count unit; "
        "structural membership still validates every numeric leaf.",
    ),
    (
        "4. memberUnits",
        "Accepted",
        "Read both unit.members and unit.memberUnits and retain "
        "explicitly declared "
        "leaf units.",
    ),
    (
        "5. Integer identities",
        "Accepted with limits",
        "Dimensionless integer identity/revision leaves require declared "
        "reference/identity context. Reject treating all integers or all "
        "not_applicable parents as dimensionless: ordinary numeric "
        "quantities still "
        "require units.",
    ),
    (
        "6. Double severity",
        "Accepted",
        "Numeric not_applicable declarations produce one major "
        "numeric_unit_not_applicable finding rather than an additional "
        "unresolved-unit blocker, including structured numeric members.",
    ),
    (
        "7. Dynamic units",
        "Accepted with limits",
        "A declared sibling quantity-unit context is a dynamic_unit_context "
        "requirement, not a static-unit blocker. A dynamic value does not excuse "
        "unrelated untyped numeric members.",
    ),
    (
        "8. Temporal dialects",
        "Accepted with limits",
        "Read bindingPolicy, clockRef/interval/age members, clock-binding writer "
        "prose and configuration lifetime. Report alternate timing dialects as "
        "major; identity/spec/config roles need no outer sample time. Reject "
        "interpreting an arbitrary per-observation lifetime as a clock contract.",
    ),
    (
        "9. Candidate quarantine",
        "Accepted",
        "proposal IDs, proposed/conflict reviews and explicit "
        "quarantined/unaccepted "
        "dispositions remain visible as info in the corpus. Selection "
        "restores their "
        "contract severity; selection does not approve them. Raw data has "
        "4,715/4,872 source fields and all 428 relations marked "
        "proposed, wider than "
        "the verifier's 80-blocker proposal-ID subset.",
    ),
    (
        "10. temporal_clock_binding",
        "Accepted",
        "Evaluate clock declarations through transitive rule references before "
        "emitting clockless-rule findings. Preserve one corpus "
        "history-binding info; "
        "declarations are not concrete instances.",
    ),
    (
        "11. Preserved severity cap",
        "Accepted; conflicting recommendation rejected",
        "Cap original_graph corpus blockers at major and mark preserved_source. "
        "Reject the per-check recommendation to keep the archived null "
        "root a corpus "
        "blocker: README explicitly preserves it. It still fails type/readiness "
        "checks and becomes a compilation blocker if selected.",
    ),
    (
        "12. Findings vs occurrences",
        "Accepted",
        "Native defaults are minor annotation debt, grouped by (rule, parameter "
        "name); count retains all AST occurrences, with pointers and contextual "
        "suggestions. Suggestions never rewrite source units or defaults.",
    ),
    (
        "Empty enum",
        "Accepted",
        "Keep contract-level blocker for the three empty enums and expose "
        "vocabularyStatus/closed evidence; open vocabulary without supplied values "
        "cannot be compiled as a closed enum.",
    ),
    (
        "Frame heuristic",
        "Accepted with limits",
        "Require numeric contents for path heuristics; explanatory not_applicable "
        "suppresses non-vector label/joint arrays. Reject unconditional "
        "suppression "
        "for explicit spatial vectors: prose cannot supply a missing "
        "frame identity.",
    ),
    (
        "Writer checks",
        "Accepted / retained",
        "Declared and alias-only writer findings are factual integration "
        "requirements. Add disposition/requiredWhen splits, and list "
        "selected fields "
        "requiring concrete producer bindings.",
    ),
    (
        "Verifier sample accounting",
        "Rejected factual claim",
        "samples_raw.json contains 58 samples, not 66. All 58 evidence "
        "pointers and "
        "raw payloads were independently re-resolved and matched. Estimated "
        "false-positive rates and projected removal counts are not substituted for "
        "the rerun.",
    ),
    (
        "Source operations",
        "Adjusted to task constraints",
        "Read Git metadata files for HEAD without running Git; dirty "
        "status remains "
        "unmeasured. No upstream imports, builders or writes.",
    ),
]


def sections(audit, table):
    from collections import Counter

    now = Counter(f["severity"] for f in audit.findings)
    lines = [
        "## Audit corrections (v2)",
        "",
        "Decisions checked against raw AeroGraph JSON and "
        "semantic-directory/README.md (lines 36, 46, 48), HANDOFF.md "
        "(lines 7, 15), "
        "and relation.schema.json. Baseline is the saved v1 audit; current counts "
        "are recomputed from this input snapshot. Corpus severity respects review "
        "gates; selected compilation severity exposes defects within "
        "the dependency "
        "closure.",
        "",
        table(
            [
                "Severity",
                "Before records",
                "After records",
                "Before occurrences",
                "After occurrences",
            ],
            [
                (
                    s,
                    BASELINE_RECORDS[s],
                    now[s],
                    BASELINE_OCCURRENCES[s],
                    sum(f["count"] for f in audit.findings if f["severity"] == s),
                )
                for s in BASELINE_RECORDS
            ],
        ),
        "",
        table(["Correction", "Decision", "Reason / implementation"], DECISIONS),
        "",
        "## Upstream fix list for AeroGraph maintainers",
        "",
        "Counts below use contract severity before quarantine/corpus caps, so "
        "candidate defects stay actionable without implying approval. Records and "
        "occurrences are reported separately. Selection is a compilation audit; "
        "producer counts are binding requirements, not observed executions.",
        "",
    ]
    priorities = [
        (
            "P0",
            (
                "field.unit_unresolved",
                "field.frame_missing",
                "field.empty_enum",
                "field.time_missing",
                "semantic.operator_type_unit",
                "semantic.null_expression",
            ),
            "Supply source-backed quantity/component units, frame/clock identities "
            "and vocabularies. Preserve archived ASTs; fix their "
            "explicitly selected "
            "adapter contracts rather than rewriting original evidence.",
        ),
        (
            "P1",
            (
                "field.writer_missing",
                "field.writer_declared",
                "field.writer_alias_only",
            ),
            "Bind one concrete producer per selected instance-field and "
            "split source "
            "roles from subject identities. Start with the three selected slice "
            "field lists.",
        ),
        (
            "P1",
            (
                "field.time_dialect",
                "field.clock_missing",
                "semantic.temporal_clock_binding",
                "field.dynamic_unit_context",
                "field.numeric_unit_not_applicable",
            ),
            "Normalize alternate timing declarations into machine-readable binding "
            "contracts; bind dynamic quantity units and sampling clocks without "
            "fabricated defaults.",
        ),
        (
            "P2",
            (
                "field.schema_conformance",
                "field.role_drift",
                "relation.cardinality_dialect",
                "relation.schema_conformance",
            ),
            "Align state role vocabulary and structured schema dialects. Normalize "
            "both directional cardinalities and explicit null-as-unbounded, "
            "retaining scopes and inverse bounds.",
        ),
        (
            "P2",
            ("semantic.parameter_default_unit",),
            "Add scoped parameter-unit metadata beside preserved native "
            "expressions "
            "using the contextual suggestions; preserve every authored default and "
            "occurrence.",
        ),
    ]
    rows = []
    for priority, checks, action in priorities:
        for check in checks:
            findings = [f for f in audit.findings if f["check"] == check]
            if findings:
                rows.append(
                    (
                        priority,
                        check,
                        len(findings),
                        sum(f["count"] for f in findings),
                        "; ".join(str(f["object_id"]) for f in findings[:3]),
                        action,
                    )
                )
    lines.extend(
        [
            table(
                [
                    "Priority",
                    "Fix group",
                    "Records",
                    "Occurrences",
                    "Example IDs",
                    "Concrete action",
                ],
                rows,
            ),
            "",
        ]
    )
    return lines
