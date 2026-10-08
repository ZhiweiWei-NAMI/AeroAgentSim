# AeroGraph runtime-registry audit

Source: `/mnt/data2/weizhiwei/AeroGraph`. HEAD: `20da07f1599940eb2ea3d6997f61eb132ac6879c`. Dirty: **None**.

Read-only audit: no upstream builds, imports, tests, normalization writes, simulation or observation acquisition. Findings describe this working tree, including uncommitted source edits; input SHA-256 inventory is in the JSON report. Stable finding IDs derive from content. No wall-clock timestamp is injected.

```text
(git status unavailable)
```

## Summary

| Severity | Finding records | Affected occurrences |
| --- | --- | --- |
| blocker | 47 | 47 |
| major | 547 | 1768 |
| minor | 2501 | 3349 |
| info | 7679 | 11177 |

| Inventory | Recomputed |
| --- | --- |
| Entity identities | 970 |
| Source fields | 4872 |
| Source + extension fields | 5020 |
| Relations | 428 |
| Original predicates/events/root rules | 1686/965/2651 |
| Authored/domain + original rules/predicates/events | 2948/1972/1172 |
| Capability mapping reconstruction | 855 |
| Types with mapping (includes unadopted) | 970 |
| Embedded samples / sampled original targets | 50/25 |
| Statically executable supplied targets | 2899 |

Finding records and occurrence counts differ: a schema finding can include multiple failed keys; a shared inheritance conflict can affect multiple types. These are defects/requirements, not failed simulation runs. Historical 11,778 validations are preserved metadata, not results of this audit.

## Runtime-compilability by directory

| Directory | Types | (a) all fields typed + units resolved | (b) any bound writer | (c) any static predicate/event | No fields | Descriptor blockers |
| --- | --- | --- | --- | --- | --- | --- |
| 业务与运行 | 104 | 86 | 0 | 101 | 13 | 0 |
| 信息与记录 | 265 | 221 | 0 | 265 | 0 | 0 |
| 法规与运行约束 | 59 | 47 | 0 | 59 | 3 | 0 |
| 物理实体 | 339 | 302 | 0 | 339 | 4 | 0 |
| 环境与空间 | 65 | 63 | 0 | 65 | 1 | 0 |
| 电磁 | 4 | 0 | 0 | 4 | 4 | 0 |
| 网络与计算 | 133 | 110 | 0 | 132 | 3 | 0 |

Own fields + adopted actual inheritance only. Nonempty fields required for (a); typed/unit-resolved does not imply schema conformity, approved semantics, clocks or descriptor compilation. (b) requires bound status plus concrete producer identity. (c) is conservative static input/AST readiness with field applicability or explicit type applicability, potentially multiple independent role producers; not live evaluation, producer integration, semantic acceptance or proof of all native operator semantics.

Supplied native roots and authored/domain-pack contracts. Auto-generated leaf-state predicates are not materialized input and are excluded; no simulation, observation acquisition or event dispatch is performed.

Source-generated leaf-state contracts and browser `metadata.integration.expandedCoverage` are not persisted inputs. They are excluded rather than fabricated or counted by running upstream builders. An independent second semantic entity inventory is likewise unavailable: referenced identities are checked against the catalog that the generator reads.

## Capability and identity claims

| Mapping kind | Recomputed |
| --- | --- |
| explicit_configuration_capability | 8 |
| explicit_document_capability | 3 |
| explicit_protocol_capability | 3 |
| record_subject_adapter | 6 |
| source_hierarchy_reuse | 813 |
| unadopted_record_contract | 22 |

Audit-local reconstruction from current own fields, actual parent chains, literal EM_APPLICATIONS and explicit candidate prefix policy; not execution or verification of generated browser metadata.

Information identities: `{'primary': 265, 'associated': 62, 'visible': 327}`. Actual roots/detached identities: `29`. Seven browsing directories and explicit cross-index membership remain separate from actual is-a. The 22 unattached profiles remain unadopted. Six EM candidates with suggested parents are not silently attached.

## Minimal vertical-slice fixes

The table selects one existing multi-role domain contract per requested type. It includes all adopted inherited fields/relations plus that contract’s additional inputs. It does not claim that registering descriptors binds producers. Fixing a globally shared root descriptor can unblock all three slices. Full field/relation IDs, blocker IDs and required actions are in JSON `metrics.vertical_slices`.

| Type | Inherited effective fields | Inherited endpoint relations | Additional role fields | Descriptor blocker records | Unbound selected field descriptors | Selected contract static readiness |
| --- | --- | --- | --- | --- | --- | --- |
| oo:UAV | 20 | 122 | 4 | 0 | 24 | True |
| oo:Order | 7 | 91 | 1 | 0 | 8 | False |
| oo:ObservationRecord | 11 | 92 | 0 | 0 | 11 | True |

### oo:UAV

Selected contract: `exp.contract.physical.uav_propulsor_operating_window`. Actual ancestry: `oo:Aircraft`, `oo:Equipment`, `oo:ModelObject`, `oo:PhysicalAsset`, `oo:UAV`, `oo:Vehicle`.

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (24 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

### oo:Order

Selected contract: `exp.contract.foundation.work_accepted_output`. Actual ancestry: `oo:ModelObject`, `oo:Order`, `oo:WorkObject`.

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (8 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

### oo:ObservationRecord

Selected contract: `exp.contract.governance.observation_delivery_latency`. Actual ancestry: `oo:ModelObject`, `oo:ObservationRecord`.

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (11 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

## Selected compilation blockers

Own/adopted inherited fields, incident relations and contracts in the declared type/role scope; unscoped contracts are selected by field/relation consumption. Transitive inputs are included. Selection exposes contract defects, including gated or preserved definitions; it does not approve them or bind producers.

### `oo:ObservationRecord`

11 own/inherited fields; 92 endpoint relations; 17 contracts/dependencies; **0 blocker records**. 22 selected field descriptors lack concrete writer bindings.

No descriptor blockers in this selection.

### `oo:Order`

7 own/inherited fields; 91 endpoint relations; 17 contracts/dependencies; **0 blocker records**. 19 selected field descriptors lack concrete writer bindings.

No descriptor blockers in this selection.

### `oo:UAV`

20 own/inherited fields; 122 endpoint relations; 68 contracts/dependencies; **0 blocker records**. 80 selected field descriptors lack concrete writer bindings.

No descriptor blockers in this selection.

Selected union: **0 unique blocker records**, 0 occurrences. Shared defects are counted once across types. Complete IDs, dependency lists, review gates and writer requirements are in JSON.

## Audit corrections (v2)

Decisions checked against raw AeroGraph JSON and semantic-directory/README.md (lines 36, 46, 48), HANDOFF.md (lines 7, 15), and relation.schema.json. Baseline is the saved v1 audit; current counts are recomputed from this input snapshot. Corpus severity respects review gates; selected compilation severity exposes defects within the dependency closure.

| Severity | Before records | After records | Before occurrences | After occurrences |
| --- | --- | --- | --- | --- |
| blocker | 431 | 47 | 431 | 47 |
| major | 9832 | 547 | 17015 | 1768 |
| minor | 0 | 2501 | 0 | 3349 |
| info | 1034 | 7679 | 1034 | 11177 |

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
| P0 | field.unit_unresolved | 104 | 104 | dt.airx.aircraft.fleet_size; dt.airx.asset.coverage; dt.airx.asset.measurement_quality | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P0 | field.frame_missing | 13 | 13 | dt.live.position; dt.localization.current_position; dt.localization.previous_position | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P0 | field.empty_enum | 3 | 3 | he.facility_geometry.coverage_actor_class; he.facility_geometry.prohibited_actor_class; he.facility_geometry.coverage_quality | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P0 | field.time_missing | 40 | 40 | exp.field.environment.obstacleClearanceMargin; exp.field.environment.smokeExtinctionSpectrum; exp.field.environment.heatFluxVector | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P0 | semantic.operator_type_unit | 7 | 7 | original:rule:he.p.em.eirp; original:rule:he.p.em.link_budget; original:rule:he.p.em.adjacent_leakage | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P0 | semantic.null_expression | 1 | 1 | original:rule:dt.legacy.business.binding.predicate.unknown | Supply source-backed quantity/component units, frame/clock identities and vocabularies. Preserve archived ASTs; fix their explicitly selected adapter contracts rather than rewriting original evidence. |
| P1 | field.writer_missing | 270 | 270 | agp:state:MobileActor.velocity; agp:state:MobileActor.position_covariance; agp:state:EnergyBus.supply_state | Bind one concrete producer per selected instance-field and split source roles from subject identities. Start with the three selected slice field lists. |
| P1 | field.writer_declared | 3659 | 3659 | cap.em.radio.frequencyHz; cap.em.radio.bandwidthHz; cap.net.ipsec.endpoint.addresses | Bind one concrete producer per selected instance-field and split source roles from subject identities. Start with the three selected slice field lists. |
| P1 | field.writer_alias_only | 1091 | 1091 | dt.air.tracking_age_s; dt.air.valid_from_s; dt.air.valid_to_s | Bind one concrete producer per selected instance-field and split source roles from subject identities. Start with the three selected slice field lists. |
| P1 | field.time_dialect | 106 | 106 | proposal:R01.materialBatchRef; proposal:R01.classificationEntries; proposal:R01.compositionDisclosure | Normalize alternate timing declarations into machine-readable binding contracts; bind dynamic quantity units and sampling clocks without fabricated defaults. |
| P1 | field.clock_missing | 303 | 303 | cap.em.radio.bandwidthHz; cap.net.ipsec.endpoint.addresses; cap.net.ipsec.endpoint.namespaceId | Normalize alternate timing declarations into machine-readable binding contracts; bind dynamic quantity units and sampling clocks without fabricated defaults. |
| P1 | field.dynamic_unit_context | 28 | 28 | dt.metric.clear_threshold; dt.metric.error_bound; hu.wp.application.dose_lower | Normalize alternate timing declarations into machine-readable binding contracts; bind dynamic quantity units and sampling clocks without fabricated defaults. |
| P1 | field.numeric_unit_not_applicable | 32 | 32 | oo:digital_twin.model.modelVersion; oo:digital_twin.model.irVersion; oo:digital_twin.model.opsetImports | Normalize alternate timing declarations into machine-readable binding contracts; bind dynamic quantity units and sampling clocks without fabricated defaults. |
| P2 | field.schema_conformance | 876 | 5546 | cap.em.radio.frequencyHz; cap.em.radio.bandwidthHz; cap.net.ipsec.endpoint.addresses | Align state role vocabulary and structured schema dialects. Normalize both directional cardinalities and explicit null-as-unbounded, retaining scopes and inverse bounds. |
| P2 | field.role_drift | 306 | 306 | proposal:R01.materialBatchRef; proposal:R01.classificationEntries; proposal:R01.compositionDisclosure | Align state role vocabulary and structured schema dialects. Normalize both directional cardinalities and explicit null-as-unbounded, retaining scopes and inverse bounds. |
| P2 | relation.cardinality_dialect | 77 | 77 | agp:relation:carries; agp:relation:attachment_host; agp:relation:attachment_payload | Align state role vocabulary and structured schema dialects. Normalize both directional cardinalities and explicit null-as-unbounded, retaining scopes and inverse bounds. |
| P2 | relation.schema_conformance | 7 | 7 | proposal:taxonomy:T01.relation.VehicleWithinFacility; proposal:taxonomy:T02.relation.HardwareDataPath; proposal:taxonomy:T02.relation.PowerSourceFeedsBus | Align state role vocabulary and structured schema dialects. Normalize both directional cardinalities and explicit null-as-unbounded, retaining scopes and inverse bounds. |
| P2 | semantic.parameter_default_unit | 2501 | 3349 | original:rule:machine_network.connectivity.heartbeat_late; original:rule:machine_network.connectivity.tx_buffer_low; original:rule:machine_network.connectivity.carrier_down | Add scoped parameter-unit metadata beside preserved native expressions using the contextual suggestions; preserve every authored default and occurrence. |

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
| cross.historical_provenance_path | info:7 | 7 | 7 |
| cross.semantic_materialization | info:1 | 1 | 1 |
| cross.source_path | info:4 | 4 | 4 |
| entity.cross_directory_inheritance | info:37 | 37 | 37 |
| entity.multiple_roots | major:1 | 1 | 29 |
| entity.unreachable_root | info:6, major:22 | 28 | 28 |
| field.clock_missing | info:303 | 303 | 303 |
| field.dynamic_unit_context | info:25, major:3 | 28 | 28 |
| field.empty_enum | info:3 | 3 | 3 |
| field.frame_binding | info:23 | 23 | 23 |
| field.frame_missing | blocker:2, info:11 | 13 | 13 |
| field.lifetime_missing | info:52, major:96 | 148 | 148 |
| field.numeric_unit_not_applicable | info:31, major:1 | 32 | 32 |
| field.role_drift | info:306 | 306 | 306 |
| field.schema_conformance | info:780, major:96 | 876 | 5546 |
| field.time_dialect | info:54, major:52 | 106 | 106 |
| field.time_missing | blocker:40 | 40 | 40 |
| field.unit_unresolved | blocker:2, info:102 | 104 | 104 |
| field.writer_alias_only | info:1091 | 1091 | 1091 |
| field.writer_declared | info:3498, major:161 | 3659 | 3659 |
| field.writer_missing | info:270 | 270 | 270 |
| profile.subject_projection | info:20 | 20 | 20 |
| profile.unattached | major:1 | 1 | 22 |
| relation.cardinality_dialect | info:77 | 77 | 77 |
| relation.schema_conformance | info:7 | 7 | 7 |
| semantic.event_unresolved | major:109 | 109 | 109 |
| semantic.null_expression | major:1 | 1 | 1 |
| semantic.operator_type_unit | blocker:3, major:4 | 7 | 7 |
| semantic.parameter_default_unit | minor:2501 | 2501 | 3349 |
| semantic.preserved_temporal_inputs | info:1 | 1 | 1 |
| semantic.specialized_inference | info:60 | 60 | 60 |
| semantic.unused_field | info:910 | 910 | 910 |

### cross.historical_provenance_path

- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/relations/definitions.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/0`. ID `AG-3ceaa1a722d8a57e`; count 1.
- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/states/definitions.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/1`. ID `AG-2f0ef0d64ecd9cee`; count 1.
- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/states/definitions/part-001.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/2`. ID `AG-236974b5410a43af`; count 1.

### cross.semantic_materialization

- **info** `semantic-directory/src/build_semantics.py`: No separate materialized semantic entity JSON; generator reads entity-directory/data/concepts.json. Referenced classes are checked against this catalog; second identity set cannot be independently compared. Evidence: [semantic-directory/src/build_semantics.py](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/build_semantics.py) `/`. ID `AG-fc0a0147cfdad629`; count 1.

### cross.source_path

- **info** `src:catalog:626ee487696df4877cf75146998448a943d82b651f45ccba3efc14f8d7dbc367`: Source snapshot path 'native-inputs/digital_twin/state_fields.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/10`. ID `AG-49b56568898792c2`; count 1.
- **info** `src:catalog:931eed2033ec1441136054d60868afcd0ead307450c527f6492963e6a73deeb5`: Source snapshot path 'native-inputs/runtime/graph_model.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/7`. ID `AG-0a8f0183fb999bcb`; count 1.
- **info** `src:catalog:9dee97d7c80fd075189af232d4b0e9313d9165a666c95869cf6f4966e5799883`: Source snapshot path 'native-inputs/digital_twin/predicates.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/8`. ID `AG-74c16963be43dbee`; count 1.

### entity.cross_directory_inheritance

- **info** `oo:ControlCommand`: Browsing directory '业务与运行' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/134`. ID `AG-22a6598d3cf4d71c`; count 1.
- **info** `oo:CoordinateFrame`: Browsing directory '网络与计算' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/136`. ID `AG-e0729da57b02e86a`; count 1.
- **info** `oo:EquipmentModel`: Browsing directory '网络与计算' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/167`. ID `AG-d6160d0bb4881c14`; count 1.

### entity.multiple_roots

- **major** `entity-directory/data/concepts.json`: 29 roots/detached identities; adopted root is oo:ModelObject Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/`. ID `AG-85cdbb9534988a08`; count 29.

### entity.unreachable_root

- **major** `agp:type:ActivityRestriction`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/1`. ID `AG-30fb6559eca7a639`; count 1.
- **major** `agp:type:CoordinationRecord`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/10`. ID `AG-8d0e544790693a08`; count 1.
- **major** `agp:type:CrowdObservation`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/12`. ID `AG-269a13a6432c961c`; count 1.

### field.clock_missing

- **info** `cap.em.radio.bandwidthHz`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1/time`. ID `AG-882695b879e0e478`; count 1.
- **info** `cap.net.ipsec.endpoint.addresses`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10/time`. ID `AG-3fbe76cfd4b421f7`; count 1.
- **info** `cap.net.ipsec.endpoint.namespaceId`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/11/time`. ID `AG-ad920554eca5ae92`; count 1.

### field.dynamic_unit_context

- **info** `dt.metric.clear_threshold`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/153/unit`. ID `AG-0308cf406ec166ba`; count 1.
- **info** `dt.metric.error_bound`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/155/unit`. ID `AG-750c95e2e4b3cce2`; count 1.
- **info** `hu.wp.application.dose_lower`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-003.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-003.json) `/337/unit`. ID `AG-994c28728f58aa37`; count 1.

### field.empty_enum

- **info** `he.facility_geometry.coverage_actor_class`: Enum has no values; vocabularyStatus='source_enum_values_not_supplied', closed=False Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/769/valueSchema`. ID `AG-2299883c7533c87d`; count 1.
- **info** `he.facility_geometry.prohibited_actor_class`: Enum has no values; vocabularyStatus='source_enum_values_not_supplied', closed=False Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/770/valueSchema`. ID `AG-47f92a4a1848a289`; count 1.
- **info** `he.facility_geometry.coverage_quality`: Enum has no values; vocabularyStatus='source_enum_values_not_supplied', closed=False Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/774/valueSchema`. ID `AG-ab3f4fba4c492bdd`; count 1.

### field.frame_binding

- **info** `dt.airx.aircraft.position`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/119/frame`. ID `AG-612c5883cfbc36dd`; count 1.
- **info** `dt.airx.aircraft.velocity`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/122/frame`. ID `AG-c2e5b62ef3b0985d`; count 1.
- **info** `dt.airx.other.position`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/229/frame`. ID `AG-1d608ec2574214b4`; count 1.

### field.frame_missing

- **info** `dt.live.position`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/132/frame`. ID `AG-05d838f9cf9c5dfd`; count 1.
- **info** `dt.localization.current_position`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/136/frame`. ID `AG-70dea067244099cb`; count 1.
- **info** `dt.localization.previous_position`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/139/frame`. ID `AG-81f3226ac05f6419`; count 1.

### field.lifetime_missing

- **info** `cap.em.radio.frequencyHz`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0`. ID `AG-7b025f8798887da5`; count 1.
- **info** `cap.em.radio.bandwidthHz`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-a52da418023e0279`; count 1.
- **info** `cap.net.ipsec.endpoint.addresses`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-e74c158d782bd343`; count 1.

### field.numeric_unit_not_applicable

- **info** `oo:digital_twin.model.modelVersion`: Numeric quantity needs a physical unit or an explicit dimensionless declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/365/unit`. ID `AG-d042d238c9d86dfb`; count 1.
- **info** `oo:digital_twin.model.irVersion`: Numeric quantity needs a physical unit or an explicit dimensionless declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/366/unit`. ID `AG-739ded3890a3f5e6`; count 1.
- **info** `oo:digital_twin.model.opsetImports`: Numeric quantity needs a physical unit or an explicit dimensionless declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/367/unit`. ID `AG-f6d288e7b7ad34d1`; count 1.

### field.role_drift

- **info** `proposal:R01.materialBatchRef`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/183/role`. ID `AG-78b189741545c203`; count 1.
- **info** `proposal:R01.classificationEntries`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/184/role`. ID `AG-4ff88616779dcadb`; count 1.
- **info** `proposal:R01.compositionDisclosure`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/185/role`. ID `AG-92545023b6a0926a`; count 1.

### field.schema_conformance

- **info** `cap.em.radio.frequencyHz`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0`. ID `AG-c6dbbc0c174a329d`; count 7.
- **info** `cap.em.radio.bandwidthHz`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-aa6a3d0bf3479730`; count 7.
- **info** `cap.net.ipsec.endpoint.addresses`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-18b1ab0ac1142318`; count 7.

### field.time_dialect

- **info** `proposal:R01.materialBatchRef`: Temporal declaration exists outside time; normalize its clock/validity binding without inventing timestamps Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/183`. ID `AG-4c1af11579135078`; count 1.
- **info** `proposal:R01.classificationEntries`: Temporal declaration exists outside time; normalize its clock/validity binding without inventing timestamps Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/184`. ID `AG-c4896eb04db7f8bf`; count 1.
- **info** `proposal:R01.compositionDisclosure`: Temporal declaration exists outside time; normalize its clock/validity binding without inventing timestamps Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/185`. ID `AG-2c103b41706ccadf`; count 1.

### field.time_missing

- **blocker** `exp.field.environment.obstacleClearanceMargin`: No temporal contract declaration Evidence: [semantic-directory/data/expanded/environment.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/expanded/environment.json) `/fields/10`. ID `AG-39a10f52ea2764ad`; count 1.
- **blocker** `exp.field.environment.smokeExtinctionSpectrum`: No temporal contract declaration Evidence: [semantic-directory/data/expanded/environment.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/expanded/environment.json) `/fields/2`. ID `AG-145d7c81c84a2464`; count 1.
- **blocker** `exp.field.environment.heatFluxVector`: No temporal contract declaration Evidence: [semantic-directory/data/expanded/environment.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/expanded/environment.json) `/fields/3`. ID `AG-1f2bf428db6a1417`; count 1.

### field.unit_unresolved

- **info** `dt.airx.aircraft.fleet_size`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/113/unit`. ID `AG-f4fbcd14c41b7617`; count 1.
- **info** `dt.airx.asset.coverage`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/129/unit`. ID `AG-6206c290f413e5a8`; count 1.
- **info** `dt.airx.asset.measurement_quality`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/131/unit`. ID `AG-11ad13ca90b85bc4`; count 1.

### field.writer_alias_only

- **info** `dt.air.tracking_age_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/100/writer`. ID `AG-c70c3f5aabb774bc`; count 1.
- **info** `dt.air.valid_from_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/101/writer`. ID `AG-43d79e5ce8f210ac`; count 1.
- **info** `dt.air.valid_to_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/102/writer`. ID `AG-b8bca081d9e83dc2`; count 1.

### field.writer_declared

- **info** `cap.em.radio.frequencyHz`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0/writer`. ID `AG-5779cd7ccdd191a5`; count 1.
- **info** `cap.em.radio.bandwidthHz`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1/writer`. ID `AG-bb4a3eb754058890`; count 1.
- **info** `cap.net.ipsec.endpoint.addresses`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10/writer`. ID `AG-39fbc8b1610ae311`; count 1.

### field.writer_missing

- **info** `agp:state:MobileActor.velocity`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/0`. ID `AG-50617ef40d22b971`; count 1.
- **info** `agp:state:MobileActor.position_covariance`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/1`. ID `AG-3dcb501cc317411b`; count 1.
- **info** `agp:state:EnergyBus.supply_state`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/10`. ID `AG-a5e1b6674e8dc59a`; count 1.

### profile.subject_projection

- **info** `cap.profile.explicit.proposal:em:EMInterferenceEvent`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/531`. ID `AG-6392b53fd12bcd05`; count 1.
- **info** `cap.profile.explicit.proposal:em:EMPropagationChannel`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/532`. ID `AG-04fea619c1e08dd6`; count 1.
- **info** `cap.profile.explicit.proposal:em:ElectromagneticField`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/533`. ID `AG-be8bd8c8ff07eee6`; count 1.

### profile.unattached

- **major** `entity-directory/data/concepts.json`: 22 unattached identities require reviewed identity/owner bindings; no implicit parent repair Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/`. ID `AG-4b6bdcd8f25c649c`; count 22.

### relation.cardinality_dialect

- **info** `agp:relation:carries`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/219/cardinality`. ID `AG-72a6b7497a5eaf92`; count 1.
- **info** `agp:relation:attachment_host`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/220/cardinality`. ID `AG-de0eda2d0ebbbde6`; count 1.
- **info** `agp:relation:attachment_payload`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/221/cardinality`. ID `AG-2a2e4d6e4320b0f2`; count 1.

### relation.schema_conformance

- **info** `proposal:taxonomy:T01.relation.VehicleWithinFacility`: 1 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/333`. ID `AG-ccedf5db86e9a3db`; count 1.
- **info** `proposal:taxonomy:T02.relation.HardwareDataPath`: 1 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/334`. ID `AG-8cce1587f301ff97`; count 1.
- **info** `proposal:taxonomy:T02.relation.PowerSourceFeedsBus`: 1 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/335`. ID `AG-b21710461044033a`; count 1.

### semantic.event_unresolved

- **major** `dt.event.joint_payload_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1692`. ID `AG-819842681419b35e`; count 1.
- **major** `dt.event.fusion_innovation_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1697`. ID `AG-8a678d149782e413`; count 1.
- **major** `dt.event.twin_projection_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1706`. ID `AG-c59f9cd9b032595c`; count 1.

### semantic.null_expression

- **major** `original:rule:dt.legacy.business.binding.predicate.unknown`: Root null constant cannot establish executable truth; semantic-directory/README.md documents this preserved artifact Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/230`. ID `AG-04c8ff7f88b6d0c2`; count 1.

### semantic.operator_type_unit

- **major** `original:rule:he.p.em.eirp`: add incompatible declared units ['dBm', 'dBi'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/334`. ID `AG-e4ff553faf4ff4e5`; count 1.
- **major** `original:rule:he.p.em.link_budget`: add incompatible declared units ['dBm', 'dBi', 'dBi'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/336`. ID `AG-cc113b544811ed39`; count 1.
- **major** `original:rule:he.p.em.adjacent_leakage`: sub incompatible declared units ['dBm', 'dB'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/344`. ID `AG-66d7a10dfbf155c0`; count 1.

### semantic.parameter_default_unit

- **minor** `original:rule:machine_network.connectivity.heartbeat_late`: Native parameter 'heartbeat_late.limit' default lacks declared unit; source default and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1163/2`. ID `AG-28de7145b8f52ebc`; count 1.
- **minor** `original:rule:machine_network.connectivity.tx_buffer_low`: Native parameter 'tx_buffer_low.limit' default lacks declared unit; source default and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1164/2`. ID `AG-3cbb827e8dccb65d`; count 1.
- **minor** `original:rule:machine_network.connectivity.carrier_down`: Native parameter 'carrier_down.limit' default lacks declared unit; source default and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1165/2`. ID `AG-80e7aa17f53f6d33`; count 1.

### semantic.preserved_temporal_inputs

- **info** `research/original-graph/decoded.json`: Preserved temporal rules consume declared history; clock declarations do not prove concrete instance bindings Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/`. ID `AG-813c0291095cdf4d`; count 1.

### semantic.specialized_inference

- **info** `original:rule:dt.predicate.localization_jump_fault`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/100`. ID `AG-44a6085e5823a958`; count 1.
- **info** `original:rule:dt.predicate.localization_protection_margin_ready`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/101`. ID `AG-6738c5b22a5551b0`; count 1.
- **info** `original:rule:dt.predicate.localization_protection_margin_fault`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/102`. ID `AG-e99405e5e839a561`; count 1.

### semantic.unused_field

- **info** `cap.em.radio.bandwidthHz`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-ac5c90653d86cf13`; count 1.
- **info** `cap.net.ipsec.endpoint.addresses`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-dec8b12d2ca857d3`; count 1.
- **info** `cap.net.ipsec.sa.destinationAddress`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/14`. ID `AG-a5e41367f3b32859`; count 1.

## Method and limits

All JSON inputs are loaded with stdlib JSON; the tool never imports upstream Python/JavaScript. The bundled unit-inference helper is an audit-owned copy of the upstream pure-stdlib `expanded_units.py`, identified in the tool README; it is used for supplied object ASTs. Native AST checks inspect operands, index references, applicability, scoped defaults and canonical execution cycles. Null in a guarded native branch remains an unknown result, not a broken registry. Complex native operators without a complete audit-owned proof are marked unproven and excluded from static readiness. No type is counted as live-executable solely because it has a profile or field.

V2 GLM review: concurrent WorkBuddy DSH streams used `workbuddy/glm-5.3-flash`, maxTokens `131072`, no effort argument, with disjoint unit and state/cardinality responsibilities. The sessions produced rule analysis but no mergeable patch; the primary agent checked the proposals against raw JSON and implemented and tested the corrections locally. See glm-v2-review.md for actual session outcomes.
