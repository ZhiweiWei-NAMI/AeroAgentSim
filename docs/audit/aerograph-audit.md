# AeroGraph runtime-registry audit

Source: `/mnt/data2/weizhiwei/AeroGraph`. HEAD: `20da07f1599940eb2ea3d6997f61eb132ac6879c`. Dirty: **True**.

Read-only audit: no upstream builds, imports, tests, normalization writes, simulation or observation acquisition. Findings describe this working tree, including uncommitted source edits; input SHA-256 inventory is in the JSON report. Stable finding IDs derive from content. No wall-clock timestamp is injected.

```text
 M README.md
?? research/source-packages/
```

## Summary

| Severity | Finding records | Affected occurrences |
| --- | --- | --- |
| blocker | 431 | 431 |
| major | 9832 | 17015 |
| minor | 0 | 0 |
| info | 1034 | 1034 |

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
| Statically executable supplied targets | 2867 |

Finding records and occurrence counts differ: a schema finding can include multiple failed keys; a shared inheritance conflict can affect multiple types. These are defects/requirements, not failed simulation runs. Historical 11,778 validations are preserved metadata, not results of this audit.

## Runtime-compilability by directory

| Directory | Types | (a) all fields typed + units resolved | (b) any bound writer | (c) any static predicate/event | No fields | Descriptor blockers |
| --- | --- | --- | --- | --- | --- | --- |
| 业务与运行 | 104 | 66 | 0 | 101 | 13 | 25 |
| 信息与记录 | 265 | 218 | 0 | 265 | 0 | 49 |
| 法规与运行约束 | 59 | 47 | 0 | 59 | 3 | 9 |
| 物理实体 | 339 | 309 | 0 | 339 | 4 | 25 |
| 环境与空间 | 65 | 63 | 0 | 65 | 1 | 2 |
| 电磁 | 4 | 0 | 0 | 4 | 4 | 0 |
| 网络与计算 | 133 | 105 | 0 | 132 | 3 | 25 |

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
| oo:UAV | 20 | 122 | 4 | 7 | 24 | True |
| oo:Order | 7 | 91 | 1 | 5 | 8 | False |
| oo:ObservationRecord | 11 | 92 | 0 | 6 | 11 | True |

### oo:UAV

Selected contract: `exp.contract.physical.uav_propulsor_operating_window`. Actual ancestry: `oo:Aircraft`, `oo:Equipment`, `oo:ModelObject`, `oo:PhysicalAsset`, `oo:UAV`, `oo:Vehicle`.

| Fix upstream / explicit binding | Affected records | Examples |
| --- | --- | --- |
| field.time_missing | 1 | exp.field.physical.propulsor.axialThrustN: No temporal contract declaration |
| relation.cardinality | 6 | proposal:taxonomy:T01.relation.VehicleWithinFacility: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T01.relation.VehicleWithinFacility: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T04.relation.LocatedInRegion: Invalid cardinality {'minimum': 0, 'maximum': None} |

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (24 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

### oo:Order

Selected contract: `exp.contract.foundation.work_accepted_output`. Actual ancestry: `oo:ModelObject`, `oo:Order`, `oo:WorkObject`.

| Fix upstream / explicit binding | Affected records | Examples |
| --- | --- | --- |
| field.time_missing | 1 | exp.foundation.work.fulfilment: No temporal contract declaration |
| relation.cardinality | 4 | proposal:taxonomy:T04.relation.LocatedInRegion: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T04.relation.LocatedInRegion: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T04.relation.ObstacleRepresentsObject: Invalid cardinality {'minimum': 0, 'maximum': None} |

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (8 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

### oo:ObservationRecord

Selected contract: `exp.contract.governance.observation_delivery_latency`. Actual ancestry: `oo:ModelObject`, `oo:ObservationRecord`.

| Fix upstream / explicit binding | Affected records | Examples |
| --- | --- | --- |
| relation.cardinality | 6 | proposal:taxonomy:T04.relation.LocatedInRegion: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T04.relation.LocatedInRegion: Invalid cardinality {'minimum': 0, 'maximum': None}; proposal:taxonomy:T04.relation.ObstacleRepresentsObject: Invalid cardinality {'minimum': 0, 'maximum': None} |

1. Normalize only source-backed schema dialects while retaining raw contracts and review/provenance.
2. Resolve the enumerated descriptor blockers; preserve adopted inheritance and separate record/subject roles.
3. Bind one real producer per selected instance-field (11 field descriptors currently lack concrete writers), plus endpoint identities and valid relation intervals.
4. Bind frame/transform revisions and acquisition, availability, validity clocks; do not substitute one timestamp for another.
5. Select explicit research disposition for proposed/quarantined contracts. Do not infer acceptance, order completion or observations from physical motion.
6. Supply current/previous same-identity, same-clock frames for entered transitions; then compile and execute the selected contract with actual producer input.

## Recommended normalization rules for the kernel's AeroGraph loader

| Loader may normalize from explicit source evidence | Must be supplied/fixed upstream or in the selected run manifest |
| --- | --- |
| configuration → config; specification → spec; retain raw role and provenance | Unknown roles, incompatible declarations and duplicate IDs require reviewed resolution. |
| Resolve local #/$defs refs with JSON Pointer escaping; object → record; enum/oneOf/anyOf retain branches and nullability | Missing/cyclic schema refs, empty enums and absent reference targets require source contracts. |
| unit.status explicit → exact only with an explicit usable symbol; canonicalize exact equivalent aliases (byte/By, Cel/degC) | Unresolved units, different scales, affine/log quantities and dynamic mixed-unit records need explicit conversions/context; no missing → dimensionless. |
| String frame → a preserved declaration requiring instance frame reference; do not invent a frame | Concrete frame identity, datum/origin, transform revision and time mapping must be bound. |
| Normalize targets_per_source minimum/maximum and sources_per_target independently | A null maximum is unbounded only where source scope explicitly states that meaning; otherwise unresolved. Enforce forward/inverse cardinalities during valid-time intervals. |
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
| entity.unreachable_root | major:28 | 28 | 28 |
| field.clock_missing | major:282 | 282 | 282 |
| field.dynamic_unit_context | major:17 | 17 | 17 |
| field.empty_enum | blocker:3 | 3 | 3 |
| field.frame_binding | major:23 | 23 | 23 |
| field.frame_missing | blocker:17 | 17 | 17 |
| field.lifetime_missing | major:148 | 148 | 148 |
| field.numeric_unit_not_applicable | major:2 | 2 | 2 |
| field.role_drift | major:306 | 306 | 306 |
| field.schema_conformance | major:876 | 876 | 5546 |
| field.time_missing | blocker:155 | 155 | 155 |
| field.unit_unresolved | blocker:169 | 169 | 169 |
| field.writer_alias_only | major:1091 | 1091 | 1091 |
| field.writer_declared | major:3659 | 3659 | 3659 |
| field.writer_missing | major:270 | 270 | 270 |
| profile.subject_projection | info:20 | 20 | 20 |
| profile.unattached | major:1 | 1 | 22 |
| relation.cardinality | blocker:28 | 28 | 28 |
| relation.cardinality_dialect | major:77 | 77 | 77 |
| relation.schema_conformance | major:77 | 77 | 161 |
| semantic.event_unresolved | major:99 | 99 | 99 |
| semantic.null_expression | blocker:1 | 1 | 1 |
| semantic.operator_type_unit | blocker:58 | 58 | 58 |
| semantic.parameter_default_unit | major:969 | 969 | 3349 |
| semantic.specialized_inference | info:55 | 55 | 55 |
| semantic.temporal_clock_binding | major:1906 | 1906 | 1906 |
| semantic.unused_field | info:910 | 910 | 910 |

### cross.historical_provenance_path

- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/relations/definitions.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/0`. ID `AG-02f607cde9b46414`; count 1.
- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/states/definitions.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/1`. ID `AG-08ebdedd6b9ff046`; count 1.
- **info** `semantic-directory/data/provenance.json`: Historical source snapshot 'data/states/definitions/part-001.json' not a current checkout file Evidence: [semantic-directory/data/provenance.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/provenance.json) `/sourceFiles/2`. ID `AG-bef0a12c659f82b0`; count 1.

### cross.semantic_materialization

- **info** `semantic-directory/src/build_semantics.py`: No separate materialized semantic entity JSON; generator reads entity-directory/data/concepts.json. Referenced classes are checked against this catalog; second identity set cannot be independently compared. Evidence: [semantic-directory/src/build_semantics.py](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/src/build_semantics.py) `/`. ID `AG-7138562af6002dff`; count 1.

### cross.source_path

- **info** `src:catalog:626ee487696df4877cf75146998448a943d82b651f45ccba3efc14f8d7dbc367`: Source snapshot path 'native-inputs/digital_twin/state_fields.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/10`. ID `AG-067b7cebe65be568`; count 1.
- **info** `src:catalog:931eed2033ec1441136054d60868afcd0ead307450c527f6492963e6a73deeb5`: Source snapshot path 'native-inputs/runtime/graph_model.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/7`. ID `AG-e3a4abaaa9f3961c`; count 1.
- **info** `src:catalog:9dee97d7c80fd075189af232d4b0e9313d9165a666c95869cf6f4966e5799883`: Source snapshot path 'native-inputs/digital_twin/predicates.json' not present locally; historical citation, not a current input Evidence: [semantic-directory/data/sources.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/sources.json) `/8`. ID `AG-e607dfe91112f803`; count 1.

### entity.cross_directory_inheritance

- **info** `oo:ControlCommand`: Browsing directory '业务与运行' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/134`. ID `AG-074c538aae7a4f59`; count 1.
- **info** `oo:CoordinateFrame`: Browsing directory '网络与计算' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/136`. ID `AG-fa1d7d4b1e207b2e`; count 1.
- **info** `oo:EquipmentModel`: Browsing directory '网络与计算' differs from actual parent's '信息跨域视图'; categories are not is-a edges Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/167`. ID `AG-376fc91520d9fb6f`; count 1.

### entity.multiple_roots

- **major** `entity-directory/data/concepts.json`: 29 roots/detached identities; adopted root is oo:ModelObject Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/`. ID `AG-4f7b12dd3507360b`; count 29.

### entity.unreachable_root

- **major** `agp:type:ActivityRestriction`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/1`. ID `AG-7ca9e7ffa79205e2`; count 1.
- **major** `agp:type:CoordinationRecord`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/10`. ID `AG-77261f7d84f3ea0d`; count 1.
- **major** `agp:type:CrowdObservation`: No adopted is-a path to oo:ModelObject; original/suggested parent is not promoted Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/12`. ID `AG-91f498ddef25b7b8`; count 1.

### field.clock_missing

- **major** `cap.em.radio.frequencyHz`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0/time`. ID `AG-bb9a171013983a9a`; count 1.
- **major** `cap.em.radio.bandwidthHz`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1/time`. ID `AG-f75c53761ea54bf4`; count 1.
- **major** `cap.net.ipsec.endpoint.addresses`: Time declaration has no explicit clock requirement/reference Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10/time`. ID `AG-ff0b571848efd4ba`; count 1.

### field.dynamic_unit_context

- **major** `dt.metric.clear_threshold`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/153/unit`. ID `AG-14c2a6723ab7725f`; count 1.
- **major** `dt.metric.error_bound`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/155/unit`. ID `AG-06e9a365ea6629e6`; count 1.
- **major** `hu.wp.application.dose_lower`: Numeric unit depends on an explicit instance quantity/unit context; not counted as statically unit-resolved Evidence: [semantic-directory/data/definitions/part-003.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-003.json) `/337/unit`. ID `AG-35c8c025f7cfb8a8`; count 1.

### field.empty_enum

- **blocker** `he.facility_geometry.coverage_actor_class`: Enum has no values Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/769/valueSchema`. ID `AG-23a9994be72f2bc6`; count 1.
- **blocker** `he.facility_geometry.prohibited_actor_class`: Enum has no values Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/770/valueSchema`. ID `AG-34429e6f78cb98d3`; count 1.
- **blocker** `he.facility_geometry.coverage_quality`: Enum has no values Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/774/valueSchema`. ID `AG-be99aeb7d58c318b`; count 1.

### field.frame_binding

- **major** `dt.airx.aircraft.position`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/119/frame`. ID `AG-64b3ba46ac11495b`; count 1.
- **major** `dt.airx.aircraft.velocity`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/122/frame`. ID `AG-163bf18b9955c81c`; count 1.
- **major** `dt.airx.other.position`: Frame delegated to record; concrete reference and transform revision required at binding Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/229/frame`. ID `AG-03968b6bfd44582a`; count 1.

### field.frame_missing

- **blocker** `dt.joint.positions`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/661/frame`. ID `AG-e2ee96f7ea758dcd`; count 1.
- **blocker** `dt.live.position`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/132/frame`. ID `AG-dc460812f7bb980c`; count 1.
- **blocker** `dt.localization.current_position`: Spatial vector/pose-like contract lacks a usable frame declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/136/frame`. ID `AG-fad86ee88c34fa18`; count 1.

### field.lifetime_missing

- **major** `cap.em.radio.frequencyHz`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0`. ID `AG-7a3d5a4d4152faed`; count 1.
- **major** `cap.em.radio.bandwidthHz`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-11e3a6677ac43368`; count 1.
- **major** `cap.net.ipsec.endpoint.addresses`: Lifetime declaration missing Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-3e4ca51bf9d4578d`; count 1.

### field.numeric_unit_not_applicable

- **major** `oo:digital_twin.model.modelVersion`: Numeric quantity needs a physical unit or an explicit dimensionless declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/365/unit`. ID `AG-5935f380a8fdf460`; count 1.
- **major** `oo:digital_twin.model.irVersion`: Numeric quantity needs a physical unit or an explicit dimensionless declaration Evidence: [semantic-directory/data/definitions/part-002.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-002.json) `/366/unit`. ID `AG-124a275c38e5d0c7`; count 1.

### field.role_drift

- **major** `proposal:R01.materialBatchRef`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/183/role`. ID `AG-024ad8cfd953391c`; count 1.
- **major** `proposal:R01.classificationEntries`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/184/role`. ID `AG-57d9020057adf455`; count 1.
- **major** `proposal:R01.compositionDisclosure`: Role 'configuration' must map to config Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/185/role`. ID `AG-76d85ef666f2e8ae`; count 1.

### field.schema_conformance

- **major** `cap.em.radio.frequencyHz`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0`. ID `AG-ad78ecc92b06385b`; count 7.
- **major** `cap.em.radio.bandwidthHz`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-c610442ad356de03`; count 7.
- **major** `cap.net.ipsec.endpoint.addresses`: 7 violations of state.schema.json; source and extension dialects require explicit normalization Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-8a5c662daa431a63`; count 7.

### field.time_missing

- **blocker** `proposal:R01.materialBatchRef`: No temporal contract declaration Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/183`. ID `AG-c2a141f72e5e22d2`; count 1.
- **blocker** `proposal:R01.classificationEntries`: No temporal contract declaration Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/184`. ID `AG-14e90bc74f213de8`; count 1.
- **blocker** `proposal:R01.compositionDisclosure`: No temporal contract declaration Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/185`. ID `AG-1f46543d596f8885`; count 1.

### field.unit_unresolved

- **blocker** `dt.airx.aircraft.fleet_size`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/113/unit`. ID `AG-3f8acd50bed0ba06`; count 1.
- **blocker** `dt.airx.asset.coverage`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/129/unit`. ID `AG-6597f4170258a2f6`; count 1.
- **blocker** `dt.airx.asset.measurement_quality`: Unit unresolved or numeric member declarations incomplete: {'symbol': None, 'status': 'unresolved', 'sourceDeclaration': None} Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/131/unit`. ID `AG-ad9cf0b095fc1995`; count 1.

### field.writer_alias_only

- **major** `dt.air.tracking_age_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/100/writer`. ID `AG-e793ee904ff398ef`; count 1.
- **major** `dt.air.valid_from_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/101/writer`. ID `AG-c35d37db707efd77`; count 1.
- **major** `dt.air.valid_to_s`: Producer alias only; no concrete instance writer Evidence: [semantic-directory/data/definitions/part-001.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-001.json) `/102/writer`. ID `AG-6ab4a247be1286dd`; count 1.

### field.writer_declared

- **major** `cap.em.radio.frequencyHz`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/0/writer`. ID `AG-9b39b06cc2f701f5`; count 1.
- **major** `cap.em.radio.bandwidthHz`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1/writer`. ID `AG-652fba14b32106a8`; count 1.
- **major** `cap.net.ipsec.endpoint.addresses`: Producer requirement declared; no concrete instance writer Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10/writer`. ID `AG-d5a871ab9b935716`; count 1.

### field.writer_missing

- **major** `agp:state:MobileActor.velocity`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/0`. ID `AG-ef6ee0b8b2051ebf`; count 1.
- **major** `agp:state:MobileActor.position_covariance`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/1`. ID `AG-31f59c33932aa1c1`; count 1.
- **major** `agp:state:EnergyBus.supply_state`: Writer declaration missing Evidence: [semantic-directory/data/definitions/part-005.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/definitions/part-005.json) `/10`. ID `AG-8bdf8335639077d2`; count 1.

### profile.subject_projection

- **info** `cap.profile.explicit.proposal:em:EMInterferenceEvent`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/531`. ID `AG-7f68c323038698e1`; count 1.
- **info** `cap.profile.explicit.proposal:em:EMPropagationChannel`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/532`. ID `AG-8db287765893c8c3`; count 1.
- **info** `cap.profile.explicit.proposal:em:ElectromagneticField`: Explicit subject capability application; source producer record fields remain separate from effective inheritance Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/concepts/533`. ID `AG-df6fe3ad95113b89`; count 1.

### profile.unattached

- **major** `entity-directory/data/concepts.json`: 22 unattached identities require reviewed identity/owner bindings; no implicit parent repair Evidence: [entity-directory/data/concepts.json](/mnt/data2/weizhiwei/AeroGraph/entity-directory/data/concepts.json) `/`. ID `AG-db45dc040f88d8e8`; count 22.

### relation.cardinality

- **blocker** `proposal:taxonomy:T01.relation.VehicleWithinFacility`: Invalid cardinality {'minimum': 0, 'maximum': None} Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/333/cardinality/sources_per_target`. ID `AG-54605bc85139ebab`; count 1.
- **blocker** `proposal:taxonomy:T01.relation.VehicleWithinFacility`: Invalid cardinality {'minimum': 0, 'maximum': None} Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/333/cardinality/targets_per_source`. ID `AG-c74be282ce3d244c`; count 1.
- **blocker** `proposal:taxonomy:T04.relation.LocatedInRegion`: Invalid cardinality {'minimum': 0, 'maximum': None} Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/389/cardinality/sources_per_target`. ID `AG-b11309a10757c9e1`; count 1.

### relation.cardinality_dialect

- **major** `agp:relation:carries`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/219/cardinality`. ID `AG-e4949da3e87ce069`; count 1.
- **major** `agp:relation:attachment_host`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/220/cardinality`. ID `AG-bf2b1ce1961d3269`; count 1.
- **major** `agp:relation:attachment_payload`: Bidirectional minimum/maximum dialect requires normalization; preserve inverse cardinality and explicit null-as-unbounded source semantics Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/221/cardinality`. ID `AG-944a688f04915a5c`; count 1.

### relation.schema_conformance

- **major** `agp:relation:carries`: 2 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/219`. ID `AG-31cabe0fe128944c`; count 2.
- **major** `agp:relation:attachment_host`: 2 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/220`. ID `AG-b3a9308d714d127f`; count 2.
- **major** `agp:relation:attachment_payload`: 2 relation schema violations Evidence: [semantic-directory/data/relations.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/relations.json) `/221`. ID `AG-e4a6e0e466e932fd`; count 2.

### semantic.event_unresolved

- **major** `dt.event.acquisition_completeness_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1687`. ID `AG-791b6036b4f5cb7e`; count 1.
- **major** `dt.event.joint_payload_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1692`. ID `AG-a9281cc796c4ec06`; count 1.
- **major** `dt.event.coordinate_alignment_recovered`: Event cannot establish a fully typed, unit-resolved predicate dependency closure; no synthetic transition Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/0/1695`. ID `AG-d0e6a9d304f67c30`; count 1.

### semantic.null_expression

- **blocker** `original:rule:dt.legacy.business.binding.predicate.unknown`: Root null constant cannot establish executable truth Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/230`. ID `AG-2d3a5313aacc9d8a`; count 1.

### semantic.operator_type_unit

- **blocker** `original:rule:machine_network.legacy.beam_switch_rate`: sub incompatible declared units ['count/s', '1/s'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1510`. ID `AG-22da89fce8a65bbd`; count 1.
- **blocker** `original:rule:machine_network.legacy.ho_preparation_failure`: sub incompatible declared units ['count', '1'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1513`. ID `AG-e57407caa4af3a52`; count 1.
- **blocker** `original:rule:machine_network.legacy.ho_pingpong`: sub incompatible declared units ['count', '1'] Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1516`. ID `AG-2176295f03542483`; count 1.

### semantic.parameter_default_unit

- **major** `original:rule:machine_network.connectivity.heartbeat_late`: 1 native parameter defaults lack declared units; defaults and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1163`. ID `AG-83739759f99917da`; count 1.
- **major** `original:rule:machine_network.connectivity.tx_buffer_low`: 1 native parameter defaults lack declared units; defaults and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1164`. ID `AG-a32c252562e239a1`; count 1.
- **major** `original:rule:machine_network.connectivity.carrier_down`: 1 native parameter defaults lack declared units; defaults and scope preserved Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1165`. ID `AG-8946053b6cc9aa49`; count 1.

### semantic.specialized_inference

- **info** `original:rule:dt.predicate.localization_jump_fault`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/100`. ID `AG-38eb7438e35343e8`; count 1.
- **info** `original:rule:dt.predicate.localization_protection_margin_ready`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/101`. ID `AG-90b3a374e249aaeb`; count 1.
- **info** `original:rule:dt.predicate.localization_protection_margin_fault`: Specialized geometry/graph/temporal operator needs a dedicated compiler; dependency audit complete, static type/unit proof unavailable Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/102`. ID `AG-9aaa86fca66a9c59`; count 1.

### semantic.temporal_clock_binding

- **major** `original:rule:machine_network.connectivity.rx_errors_grow`: delta uses external event-time history; concrete clock identity/mapping and sampling gap require binding Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1351/1`. ID `AG-2af4c4e8daf28073`; count 1.
- **major** `original:rule:machine_network.connectivity.tx_errors_grow`: delta uses external event-time history; concrete clock identity/mapping and sampling gap require binding Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1352/1`. ID `AG-f988454b19206c8c`; count 1.
- **major** `original:rule:machine_network.connectivity.rx_drop_grows`: delta uses external event-time history; concrete clock identity/mapping and sampling gap require binding Evidence: [research/original-graph/decoded.json](/mnt/data2/weizhiwei/AeroGraph/research/original-graph/decoded.json) `/model/TC/4/1353/1`. ID `AG-da7f1f071238afee`; count 1.

### semantic.unused_field

- **info** `cap.em.radio.bandwidthHz`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/1`. ID `AG-e034bd9f502c4202`; count 1.
- **info** `cap.net.ipsec.endpoint.addresses`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/10`. ID `AG-e1396d95cd6fa1cd`; count 1.
- **info** `cap.net.ipsec.sa.destinationAddress`: Field not consumed by supplied original/pilot/capability/domain contracts; schema-generated conditions are outside this count Evidence: [semantic-directory/data/capability-definitions.json](/mnt/data2/weizhiwei/AeroGraph/semantic-directory/data/capability-definitions.json) `/fields/14`. ID `AG-7e226d71c66c8920`; count 1.

## Method and limits

All JSON inputs are loaded with stdlib JSON; the tool never imports upstream Python/JavaScript. The bundled unit-inference helper is an audit-owned copy of the upstream pure-stdlib `expanded_units.py`, identified in the tool README; it is used for supplied object ASTs. Native AST checks inspect operands, index references, applicability, scoped defaults and canonical execution cycles. Null in a guarded native branch remains an unknown result, not a broken registry. Complex native operators without a complete audit-owned proof are marked unproven and excluded from static readiness. No type is counted as live-executable solely because it has a profile or field.

GLM delegation for this implementation: two concurrent WorkBuddy DSH sessions were started with `workbuddy/glm-5.3-flash`, maxTokens `131072`, no effort argument; both exited with TRANSPORT errors before producing artifacts. The audit results and tests were completed locally.
