# AeroGraph runtime-registry audit

Source: `/mnt/data2/weizhiwei/aeroagentsim/aerokernel/tests/.pytest_audit_delivery/test_v2_cli_selected_exit_poli0/source`. HEAD: `None`. Dirty: **None**.

Read-only audit: no upstream builds, imports, tests, normalization writes, simulation or observation acquisition. Findings describe this working tree, including uncommitted source edits; input SHA-256 inventory is in the JSON report. Stable finding IDs derive from content. No wall-clock timestamp is injected.

```text
(git status unavailable)
```

## Summary

| Severity | Finding records | Affected occurrences |
| --- | --- | --- |
| blocker | 0 | 0 |
| major | 0 | 0 |
| minor | 0 | 0 |
| info | 3 | 3 |

| Inventory | Recomputed |
| --- | --- |
| Entity identities | 1 |
| Source fields | 1 |
| Source + extension fields | 1 |
| Relations | 1 |
| Original predicates/events/root rules | 1/0/1 |
| Authored/domain + original rules/predicates/events | 1/1/0 |
| Capability mapping reconstruction | 1 |
| Types with mapping (includes unadopted) | 1 |
| Embedded samples / sampled original targets | 0/0 |
| Statically executable supplied targets | 0 |

Finding records and occurrence counts differ: a schema finding can include multiple failed keys; a shared inheritance conflict can affect multiple types. These are defects/requirements, not failed simulation runs. Historical 11,778 validations are preserved metadata, not results of this audit.

## Runtime-compilability by directory

| Directory | Types | (a) all fields typed + units resolved | (b) any bound writer | (c) any static predicate/event | No fields | Descriptor blockers |
| --- | --- | --- | --- | --- | --- | --- |
| physical | 1 | 0 | 1 | 0 | 0 | 0 |

Own fields + adopted actual inheritance only. Nonempty fields required for (a); typed/unit-resolved does not imply schema conformity, approved semantics, clocks or descriptor compilation. (b) requires bound status plus concrete producer identity. (c) is conservative static input/AST readiness with field applicability or explicit type applicability, potentially multiple independent role producers; not live evaluation, producer integration, semantic acceptance or proof of all native operator semantics.

Supplied native roots and authored/domain-pack contracts. Auto-generated leaf-state predicates are not materialized input and are excluded; no simulation, observation acquisition or event dispatch is performed.

Source-generated leaf-state contracts and browser `metadata.integration.expandedCoverage` are not persisted inputs. They are excluded rather than fabricated or counted by running upstream builders. An independent second semantic entity inventory is likewise unavailable: referenced identities are checked against the catalog that the generator reads.

## Capability and identity claims

| Mapping kind | Recomputed |
| --- | --- |
| source_hierarchy_reuse | 1 |

Audit-local reconstruction from current own fields, actual parent chains, literal EM_APPLICATIONS and explicit candidate prefix policy; not execution or verification of generated browser metadata.

Information identities: `None`. Actual roots/detached identities: `1`. Seven browsing directories and explicit cross-index membership remain separate from actual is-a. The 22 unattached profiles remain unadopted. Six EM candidates with suggested parents are not silently attached.

## Minimal vertical-slice fixes

The table selects one existing multi-role domain contract per requested type. It includes all adopted inherited fields/relations plus that contract’s additional inputs. It does not claim that registering descriptors binds producers. Fixing a globally shared root descriptor can unblock all three slices. Full field/relation IDs, blocker IDs and required actions are in JSON `metrics.vertical_slices`.

| Type | Inherited effective fields | Inherited endpoint relations | Additional role fields | Descriptor blocker records | Unbound selected field descriptors | Selected contract static readiness |
| --- | --- | --- | --- | --- | --- | --- |
| oo:UAV | 0 | 0 | 0 | 0 | 0 | None |
| oo:Order | 0 | 0 | 0 | 0 | 0 | None |
| oo:ObservationRecord | 0 | 0 | 0 | 0 | 0 | None |

### oo:UAV

Requested type is absent.
### oo:Order

Requested type is absent.
### oo:ObservationRecord

Requested type is absent.
## Selected compilation blockers

Own/adopted inherited fields, incident relations and contracts in the declared type/role scope; unscoped contracts are selected by field/relation consumption. Transitive inputs are included. Selection exposes contract defects, including gated or preserved definitions; it does not approve them or bind producers.

### `oo:ModelObject`

1 own/inherited fields; 1 endpoint relations; 2 contracts/dependencies; **1 blocker records**. 0 selected field descriptors lack concrete writer bindings.

| Blocker | Records | Occurrences | Example IDs |
| --- | --- | --- | --- |
| field.unit_unresolved | 1 | 1 | f |

Selected union: **1 unique blocker records**, 1 occurrences. Shared defects are counted once across types. Complete IDs, dependency lists, review gates and writer requirements are in JSON.

## Audit corrections (v2)

Decisions checked against raw AeroGraph JSON and semantic-directory/README.md (lines 36, 46, 48), HANDOFF.md (lines 7, 15), and relation.schema.json. Baseline is the saved v1 audit; current counts are recomputed from this input snapshot. Corpus severity respects review gates; selected compilation severity exposes defects within the dependency closure.

| Severity | Before records | After records | Before occurrences | After occurrences |
| --- | --- | --- | --- | --- |
| blocker | 431 | 0 | 431 | 0 |
| major | 9832 | 0 | 17015 | 0 |
| minor | 0 | 0 | 0 | 0 |
| info | 1034 | 3 | 1034 | 3 |

| Correction | Decision | Reason / implementation |
| --- | --- | --- |
| Additional source-backed time/identity typing | Accepted after raw verification | Incident occurrence time fields explicitly say 所有秒值使用明确来源时钟 (part-003.json /595); the upstream adapter attaches seconds to atS/startS/endS/firstObservedAtS from that quotation. Mirror that annotation with unitBasis, never suffix guessing. Pure string/full-InstanceRef wrappers need no sample time. Typed contracts for unrelated roles are excluded from selection despite shared identity fields. |
| 1. Directional cardinality | Accepted | Explicit null maximum means unbounded in snake/camel directional bounds, independently of scope prose. Missing maximum remains invalid. Keep one dialect finding and remove its duplicate min/max schema errors. |
| 2. Count compatibility | Accepted with limits | count and 1, count/s and 1/s are compatible at equal physical dimensions, scale and flavor. Different counting identities (packet/person) remain incompatible; opaque/log units are not dimensionless. |
| 3a. norm typing | Accepted | Quaternion norm is dimensionless; ordinary vector norm uses numeric component units. No general exemption for arbitrary opaque representations. |
| 3b. contains typing | Accepted | Record members/items do not inherit a container scalar count unit; structural membership still validates every numeric leaf. |
| 4. memberUnits | Accepted | Read both unit.members and unit.memberUnits and retain explicitly declared leaf units. |
| 5. Integer identities | Accepted with limits | Dimensionless integer identity/revision leaves require declared reference/identity context. Reject treating all integers or all not_applicable parents as dimensionless: ordinary numeric quantities still require units. |
| 6. Double severity | Accepted | Numeric not_applicable declarations produce one major numeric_unit_not_applicable finding rather than an additional unresolved-unit blocker, including structured numeric members. |
| 7. Dynamic units | Accepted with limits | A declared sibling quantity-unit context is a dynamic_unit_context requirement, not a static-unit blocker. A dynamic value does not excuse unrelated untyped numeric members. |
| 8. Temporal dialects | Accepted with limits | Read bindingPolicy, clockRef/interval/age members, clock-binding writer prose and configuration lifetime. Report alternate timing dialects as major; identity/spec/config roles need no outer sample time. Reject interpreting an arbitrary per-observation lifetime as a clock contract. |
| 9. Candidate quarantine | Accepted | proposal IDs, proposed/conflict reviews and explicit quarantined/unaccepted dispositions remain visible as info in the corpus. Selection restores their contract severity; selection does not approve them. Raw data has 4,715/4,872 source fields and all 428 relations marked proposed, wider than the verifier's 80-blocker proposal-ID subset. |
| 10. temporal_clock_binding | Accepted | Evaluate clock declarations through transitive rule references before emitting clockless-rule findings. Preserve one corpus history-binding info; declarations are not concrete instances. |
| 11. Preserved severity cap | Accepted; conflicting recommendation rejected | Cap original_graph corpus blockers at major and mark preserved_source. Reject the per-check recommendation to keep the archived null root a corpus blocker: README explicitly preserves it. It still fails type/readiness checks and becomes a compilation blocker if selected. |
| 12. Findings vs occurrences | Accepted | Native defaults are minor annotation debt, grouped by (rule, parameter name); count retains all AST occurrences, with pointers and contextual suggestions. Suggestions never rewrite source units or defaults. |
| Empty enum | Accepted | Keep contract-level blocker for the three empty enums and expose vocabularyStatus/closed evidence; open vocabulary without supplied values cannot be compiled as a closed enum. |
| Frame heuristic | Accepted with limits | Require numeric contents for path heuristics; explanatory not_applicable suppresses non-vector label/joint arrays. Reject unconditional suppression for explicit spatial vectors: prose cannot supply a missing frame identity. |
| Writer checks | Accepted / retained | Declared and alias-only writer findings are factual integration requirements. Add disposition/requiredWhen splits, and list selected fields requiring concrete producer bindings. |
| Verifier sample accounting | Rejected factual claim | samples_raw.json contains 58 samples, not 66. All 58 evidence pointers and raw payloads were independently re-resolved and matched. Estimated false-positive rates and projected removal counts are not substituted for the rerun. |
| Source operations | Adjusted to task constraints | Read Git metadata files for HEAD without running Git; dirty status remains unmeasured. No upstream imports, builders or writes. |

## Upstream fix list for AeroGraph maintainers

Counts below use contract severity before quarantine/corpus caps, so candidate defects stay actionable without implying approval. Records and occurrences are reported separately. Selection is a compilation audit; producer counts are binding requirements, not observed executions.

| Priority | Fix group | Records | Occurrences | Example IDs | Concrete action |
| --- | --- | --- | --- | --- | --- |
| P0 | field.unit_unresolved | 1 | 1 | f | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |

## Recommended normalization rules for the kernel's AeroGraph loader

| Loader may normalize from explicit source evidence | Must be supplied/fixed upstream or in the selected run manifest |
| --- | --- |
| configuration → config; specification → spec; retain raw role and provenance | Unknown roles, incompatible declarations and duplicate IDs require reviewed resolution. |
| Resolve local #/$defs refs with JSON Pointer escaping; object → record; enum/oneOf/anyOf retain branches and nullability | Missing/cyclic schema refs, empty enums and absent reference targets require source contracts. |
| unit.status explicit → exact only with an explicit usable symbol; canonicalize exact equivalent aliases (byte/By, Cel/degC) | Unresolved units, different scales, affine/log quantities and dynamic mixed-unit records need explicit conversions/context; no missing → dimensionless. |
| String frame → a preserved declaration requiring instance frame reference; do not invent a frame | Concrete frame identity, datum/origin, transform revision and time mapping must be bound. |
| Normalize targets_per_source minimum/maximum and sources_per_target independently | An explicitly present null upper bound is unbounded in directional dialects; a missing bound is invalid. Enforce forward/inverse cardinalities during valid-time intervals. |
| Keep seven browsing categories, cross-indexes, actual parents, suggested parents and original proposed fields distinct | Do not attach the 22 profiles or six suggested EM parents without a reviewed identity/contract change. |
| Keep Own/Oi producer aliases and scoped p defaults; b executes inline AST while r executes canonical target | Bind actual producers/subject instances. A kind named bound_source_producer or prose description is insufficient. Never use sample/default values as observations. |
| Preserve explicit null branches and unknown-valued source enums; flag an entirely null root without fabricating truth | Compile specialized geometry/graph/time operators with their actual semantics; declare windows, clocks, sampling gaps and same-identity history. |
| Retain review, quarantine, original ASTs, source citations and historical validation records | Explicit research selection is required for unadopted contracts; historical validation totals are not current acceptance evidence. |

## Findings by check

| Check | Severity distribution | Records | Occurrences |
| --- | --- | --- | --- |
| cross.semantic_materialization | info:1 | 1 | 1 |
| field.unit_unresolved | info:1 | 1 | 1 |
| input.git_unavailable | info:1 | 1 | 1 |

### cross.semantic_materialization

- **info** `semantic-directory/src/build_semantics.py`: No separate materialized semantic entity JSON; generator reads entity-directory/data/concepts.json. Referenced classes are checked against this catalog; second identity set cannot be independently compared. Evidence: [semantic-directory/src/build_semantics.py](/mnt/data2/weizhiwei/aeroagentsim/aerokernel/tests/.pytest_audit_delivery/test_v2_cli_selected_exit_poli0/source/semantic-directory/src/build_semantics.py) `/`. ID `AG-fc0a0147cfdad629`; count 1.

### field.unit_unresolved

- **info** `f`: Unit unresolved or numeric member declarations incomplete: {'status': 'unresolved', 'symbol': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/aeroagentsim/aerokernel/tests/.pytest_audit_delivery/test_v2_cli_selected_exit_poli0/source/semantic-directory/data/definitions/part-001.json) `/0/unit`. ID `AG-bacf170e65d6d29a`; count 1.

### input.git_unavailable

- **info** `.git`: No Git metadata at source root; parent repository is not attributed to this fixture Evidence: [.git](/mnt/data2/weizhiwei/aeroagentsim/aerokernel/tests/.pytest_audit_delivery/test_v2_cli_selected_exit_poli0/source/.git) `/`. ID `AG-8f39950328417bd7`; count 1.

## Method and limits

All JSON inputs are loaded with stdlib JSON; the tool never imports upstream Python/JavaScript. The bundled unit-inference helper is an audit-owned copy of the upstream pure-stdlib `expanded_units.py`, identified in the tool README; it is used for supplied object ASTs. Native AST checks inspect operands, index references, applicability, scoped defaults and canonical execution cycles. Null in a guarded native branch remains an unknown result, not a broken registry. Complex native operators without a complete audit-owned proof are marked unproven and excluded from static readiness. No type is counted as live-executable solely because it has a profile or field.

V2 GLM review: concurrent WorkBuddy DSH streams used `workbuddy/glm-5.3-flash`, maxTokens `131072`, no effort argument, with disjoint unit and state/cardinality responsibilities. The sessions produced rule analysis but no mergeable patch; the primary agent checked the proposals against raw JSON and implemented and tested the corrections locally. See glm-v2-review.md for actual session outcomes.
