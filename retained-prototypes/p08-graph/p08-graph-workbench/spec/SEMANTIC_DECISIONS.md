# P08 semantic decisions / 语义决策

## Outcome / 结论

The formal authoring contract preserves the full accepted source scope and every source occurrence. It unifies compatible vocabulary without pretending definitions, attempted execution, expected truth and observed outcomes are interchangeable. The graph is a directed typed multigraph; the vocabulary count is not a count of ontology layers or verified business outcomes.

正式合同保留来源范围、原始对象与关系记录。只在身份、层级和语义兼容时统一名称；定义、配置实例、执行尝试、预期结果和实际证据保持分离。相同节点对上的不同角色、条件、时间与版本关系均保留。

## Decisions that change implementation

1. A state specification is a field declaration. A fact is one typed value record with exact subject, computing authority, validity, availability and provenance. No second physical value is introduced.
2. An agent remains independent of an entity. Multiple agents and multiple related entities are allowed within explicit operation/target/time scopes; overlap needs a declared arbitration policy, never a guessed priority.
3. Strategy definitions, invocations, decisions, command definitions, attempts, lifecycle receipts, behavior definitions/executions and module configurations/executions form distinct control-chain objects.
4. `module_execution → module` is `execution_of_module`, since the source module is configured and is not a reusable class to cast into. `module → field` declares selected computing authority; `module_execution → fact` carries production lineage.
5. Strategy input constraints, output requirements, behavior constraints, start checks, continuation checks and effect/review checks are not substitutes. A legal source or grant placeholder stays unverified.
6. Resource requirements retain dimension, quantity, unit, interval and purpose. Capacity, reservation, occupancy, consumption, reuse and release are independent. Review processes do not automatically consume execution resources.
7. Relation-instance nodes retain ordered role endpoints. Binary projection edges retain the same role and index; no generic cardinality may collapse passenger roles, custodians, equipment, material batches, slots or attempts.
8. Exact structured identity preserves the JSON types of epoch and generation. Unknown, null or empty identity fields never prove equality. A canonical display/storage ID cannot repair missing runtime identity.
9. Main rule truth and applicability scope status are independent. Unknown truth, missing evidence, execution-not-run and binding conflicts remain separate statuses.
10. AST expression forms, literal types, all nested operator operands and operand order, reference binding context, parameter defaults/scopes, main/applicability roots and temporal controls are retained. Native parity remains unverified.
11. ARGUMENT uses opposite source directions in delivery versus the other catalogs. Preserve endpoints and expose has_argument versus argument_of; do not reverse source edges or select ordering by label.
12. Classification into a legacy node_type is classified_as, not a cast. Fact→field is instantiates_state. Forty-two legacy source check field references labelled INSTANCE_OF are legacy_field_reference, with exact role state_field only.
13. Source-only references use source_field_reference. They do not become business meaning, authority or causality merely because the target resolves.
14. Only exact explicit definition identity/revision/scope/body permits deduplication. Missing revision or scope means distinct canonical records; a UI may group equivalent-looking source occurrences but must not call that semantic identity.
15. The current native Atlas catalog/operator/evaluator, real grants and qualifications, calibrated measurements, physical producers, durable custody authority and compute/energy couplings remain source/evidence gaps. This contract adds no law, threshold, priority or operational action.

## Complete runtime-kind decisions / 42 个原始类型

All fields in every original profile stay in payload. Canonical-family mapping is not occurrence merging.

| Raw kind | Canonical kind | Decision | Source occurrence count | Meaning |
|---|---|---|---:|---|
| `accounting_record` | `accounting_record` | preserve_distinct_kind | 2 | 能量或资源核算条目与区间，保存贡献者和计算假设，区别于实测电量/实际消费。 |
| `artifact_change_record` | `artifact_change_record` | preserve_distinct_kind | 1 | 经指定审核把输入版本变为输出版本的变更记录；缺失变更集或批准不得视为已发布。 |
| `artifact_version_binding` | `artifact_version_binding` | preserve_distinct_kind | 1 | 相关计划、模型与采集工件的精确版本绑定，声明对齐和审核的输入上下文。 |
| `authority_record` | `authority_record` | preserve_distinct_kind | 27 | 面向具体行为参与方、目标、操作与有效范围的权利声明；合成声明不建立真实权限。 |
| `baseline_revision_acceptance_requirement` | `baseline_revision_acceptance_requirement` | preserve_distinct_kind | 1 | 基线选择必须满足的身份、修订、摘要、批准、有效范围、坐标与撤销条件；年龄仅是独立偏好。 |
| `behavior_execution` | `behavior_execution` | preserve_distinct_kind | 386 | 行为定义的一次执行上下文，保留命令、模块、检查和完成证据要求；未执行状态保持明确。 |
| `business_order` | `business_order` | preserve_distinct_kind | 8 | 业务订单/工作请求的范围、参与者、任务尝试与截止要求；独立于执行效果。 |
| `capability_claim` | `capability_claim` | preserve_distinct_kind | 8 | 某实体在明确范围的能力声明，保持未认证/不可用状态；不是指令权限。 |
| `check_result` | `check_result` | preserve_distinct_kind | 171 | 检查上下文的预期或运行结果，明确结果范围、证据与未知/阻塞的区别。 |
| `command` | `command_attempt` | compatible_alias | 129 | 此候选类型仅表示运行时命令尝试，不能当作冻结commands集合中的command_definition。 |
| `command_attempt` | `command_attempt` | preserve_distinct_kind | 67 | 某命令定义的一次具体请求，绑定目标、任务尝试、参数与发出时刻；不等于已接受或已执行。 |
| `command_instance` | `command_attempt` | compatible_alias | 144 | 具体命令请求实例，绑定定义、任务尝试与目标；与接口回执、物理结果分离。 |
| `command_lifecycle_receipt` | `command_lifecycle_receipt` | preserve_distinct_kind | 67 | 针对具体命令尝试的请求、接受等生命周期回执；只证明明示的控制接口阶段。 |
| `command_receipt` | `command_receipt` | preserve_distinct_kind | 273 | 某次具体命令的生命周期记录及证明范围；接受或已记录并不证明任务完成。 |
| `compute_job_resource_binding` | `compute_job_resource_binding` | preserve_distinct_kind | 11 | 计算任务、输入输出工件、模型版本与多维资源要求的绑定；预留和实际占用分别记录。 |
| `constraint_check` | `constraint_check` | preserve_distinct_kind | 132 | 具体开始或继续执行条件检查上下文，保留检查时刻、负责模块与当前所需证据。 |
| `constraint_check_result` | `constraint_check_result` | preserve_distinct_kind | 937 | 一次精确检查的结果记录，保留检查定义、执行对象、输入证据、三项评估及未知或冲突处理；结果不自行授权动作。 |
| `custody_and_responsibility_handover` | `custody_and_responsibility_handover` | preserve_distinct_kind | 1 | 责任或样品保管交接，绑定发送者、接收者、对象和证据；报告送达不等于样品保管转移。 |
| `effect_evaluation_result` | `effect_evaluation_result` | preserve_distinct_kind | 90 | 针对某任务尝试的效果核验，分别保留期望目标、接受目标和实际效果证据。 |
| `engineering_review_record` | `engineering_review_record` | preserve_distinct_kind | 1 | 工程审核人对指定版本计算产物的专业审核记录；数值非负不替代审核批准。 |
| `evaluation_binding` | `evaluation_binding` | preserve_distinct_kind | 160 | 具体规则/谓词评估的主体角色、字段投影、参数和时间上下文。 |
| `event_occurrence` | `event_occurrence` | preserve_distinct_kind | 340 | 事件定义的一次候选或发生记录；是否发出、发生策略、参与者、时间及证据必须保持，不由真值自动推断。 |
| `future_followup_attempt` | `future_followup_attempt` | preserve_distinct_kind | 1 | 未来时窗内的新随访尝试，引用既有任务，但需要当前授权、检查与新证据。 |
| `human_actor` | `human_actor` | preserve_distinct_kind | 14 | 实际人员的决策/操作角色声明，区别于软件智能体；资格、认证和权利仍需具体来源。 |
| `human_review_record` | `human_review_record` | preserve_distinct_kind | 1 | 具体审核者对指定候选与输入版本的审核结果要求，未签署结果保持未知。 |
| `longitudinal_observation` | `longitudinal_observation` | preserve_distinct_kind | 1 | 跨日随访观测，保持样本身份、各观测时窗和指标的区别。 |
| `material_transformation` | `material_transformation` | preserve_distinct_kind | 2 | 混合或过滤等物料转换，绑定输入批次、输出批次、中间批次、设备和数量证据；不凭批次名推导守恒或化学效果。 |
| `migration_record` | `migration_record` | preserve_distinct_kind | 1 | 任务从源执行器到目标执行器的检查点、尝试、提交和清理上下文；传输回执不足以证明迁移完成。 |
| `module_execution` | `module_execution` | preserve_distinct_kind | 525 | 模块定义的一次计算或执行上下文，引用输入与输出事实；合成记录不证明后端曾运行。 |
| `paired_observation` | `paired_observation` | preserve_distinct_kind | 3 | 同对象或匹配样点的前后测量，保存配对键、时间与对照证据；不自动推断因果。 |
| `passenger_itinerary` | `passenger_itinerary` | preserve_distinct_kind | 2 | 同一乘客行程的预定、航段、备降与接续结构，保留任务和尝试身份。 |
| `passenger_leg` | `passenger_leg` | preserve_distinct_kind | 2 | 行程中的具体航段或接续段，绑定起讫、参与者和尝试；备降不等于原联程完成。 |
| `passenger_manifest_entry` | `passenger_manifest_entry` | preserve_distinct_kind | 3 | 一名乘客与飞机、座位、行程、航段、尝试的绑定；人数统计不替代登乘证据。 |
| `predicate_evaluation` | `predicate_evaluation` | preserve_distinct_kind | 252 | 在明确绑定和时间截断下的一次谓词评估上下文；未连接引擎时保持未执行。 |
| `predicate_result` | `predicate_expectation` | semantic_disambiguation | 108 | 此候选类型保存明示的合成预期真值，不冒充冻结集合中的原生求值结果。 |
| `prepared_input` | `prepared_input` | preserve_distinct_kind | 90 | 规则调用所准备的事实快照，记录输入身份、投影、可用性与缺失原因。 |
| `relation_instance` | `relation_instance` | preserve_distinct_kind | 49 | 带有序角色端点、定义引用、时空或有效范围的关系实例；保管、物理连接等不可互换。 |
| `review_signature_record` | `review_signature_record` | preserve_distinct_kind | 3 | 某版本工件的审核与签名证据要求，明确审核人、版本与摘要；未签署不能变为已批准。 |
| `rule_evaluation_binding` | `evaluation_binding` | compatible_alias | 67 | 规则求值绑定，将具体主体、字段事实、参数、历史与时刻连接到固定规则和结果。 |
| `sample_collection_record` | `sample_collection_record` | preserve_distinct_kind | 9 | 一次采样操作记录，区分样品内容与容器、封签、体积、来源位置时间和保管责任。 |
| `strategy_invocation` | `strategy_invocation` | preserve_distinct_kind | 340 | 一次策略调用上下文，绑定智能体、策略修订、可见事实、请求、目标、约束和时间截断；与策略定义分开。 |
| `time_context` | `time_context` | preserve_distinct_kind | 16 | 求值或历史所需的查询时间、时间域、窗口和可用性截断上下文。 |

## Frozen collection mappings / 冻结集合

| Collection | Canonical family | Allowed semantic levels |
|---|---|---|
| `node_types` | `node_type` | definition |
| `entity_types` | `entity_type` | definition |
| `entities` | `entity` | configured_instance |
| `fields` | `state_specification` | definition |
| `facts` | `fact` | runtime_record |
| `modules` | `module` | configured_instance |
| `agents` | `agent` | configured_instance |
| `strategies` | `strategy_definition` | definition |
| `capabilities` | `capability_declaration` | definition, configured_instance |
| `behaviors` | `behavior_definition` | definition |
| `requests` | `request` | configured_instance |
| `objectives` | `objective` | configured_instance |
| `decisions` | `decision` | runtime_record |
| `tasks` | `task` | configured_instance |
| `resources` | `resource` | configured_instance |
| `constraints` | `constraint_definition` | definition |
| `predicates` | `predicate_definition` | definition |
| `rules` | `rule_definition` | definition |
| `events` | `event_definition` | definition |
| `commands` | `command_definition` | definition |
| `arbitrations` | `arbitration_policy` | definition |
| `sources` | `evidence_source` | source_record |
| `scenarios` | `scenario_definition` | definition |
| `admission_checks` | `check_binding` | configured_instance |
| `feedback_policies` | `feedback_policy` | definition |
| `predicate_results` | `predicate_result` | runtime_record |
| `relation_definitions` | `relation_definition` | definition |
| `parameter_definitions` | `parameter_definition` | definition |
| `spatial_zones` | `spatial_zone` | configured_instance |
| `lease_requests` | `lease_request` | configured_instance |
| `lease_approvals` | `lease_approval` | runtime_record |
| `space_time_allocations` | `space_time_allocation` | configured_instance |
| `delivery_receipts` | `delivery_receipt` | runtime_record |
| `expression_types` | `expression_type` | definition |
| `expressions` | `expression` | definition |

## Relation migration / 关系迁移

This table lists every raw name by its source namespace. Endpoints are never reversed; exact typed variants, roles and obligations are in relation-registry.json. Repeated canonical names across rows do not collapse edge identities.

| Namespace | Raw relation | Canonical relation(s) |
|---|---|---|
| frozen | `ACCEPTS_FIELD` | `accepts_state_signature` |
| frozen | `ACTS_ON` | `behavior_subject` |
| frozen | `AFFECTS` | `feedback_behavior_binding` |
| frozen | `ALLOCATED_UNDER` | `allocated_under_approval` |
| frozen | `ALLOCATES` | `declares_resource_allocation` |
| frozen | `ALLOCATES_ZONE` | `allocates_zone` |
| frozen | `APPLIES_WHEN` | `applies_when` |
| frozen | `APPROVED_BY` | `approved_by_agent` |
| frozen | `ARBITRATED_BY` | `arbitrated_by` |
| frozen | `ARBITRATES` | `arbitrates_agent` |
| frozen | `ARGUMENT` | `argument_of`, `has_argument` |
| frozen | `AVAILABLE_TO` | `capability_available_to` |
| frozen | `BASED_ON` | `based_on_fact`, `constraint_basis` |
| frozen | `BINDS_PARAMETER` | `parameter_binding_input_to` |
| frozen | `CALLS` | `selects_strategy` |
| frozen | `CHECKS` | `checks_behavior_definition` |
| frozen | `CITED_FROM` | `cited_from` |
| frozen | `COMPOSES` | `composes_behavior_definition` |
| frozen | `CONDITIONED_ON` | `conditioned_on_predicate` |
| frozen | `CONDITIONS_BEHAVIOR` | `conditions_behavior` |
| frozen | `CONSTRAINED_BY` | `constrained_by` |
| frozen | `CONTROLS` | `controls` |
| frozen | `CONTROL_INPUT` | `control_input_to` |
| frozen | `COVERS_TYPE` | `source_covers_entity_type` |
| frozen | `DECIDES_REQUEST` | `decides_lease_request` |
| frozen | `DECLARES` | `declares_state` |
| frozen | `DECLARES_COMMAND` | `declares_command_definition` |
| frozen | `DECLARES_EVENT` | `declares_event` |
| frozen | `DECLARES_OUTPUT` | `declares_command_output` |
| frozen | `DEFINES` | `defines_target` |
| frozen | `DEPENDS_ON` | `depends_on_definition` |
| frozen | `ENTITY_REFERENCE` | `value_references_entity` |
| frozen | `EVIDENCED_BY` | `declares_event_evidence`, `evidenced_by` |
| frozen | `EXECUTED_BY` | `execution_module_binding` |
| frozen | `EXECUTOR` | `assigned_decision_agent` |
| frozen | `FEEDS_BACK` | `declares_event_feedback` |
| frozen | `GENERATES` | `selects_command_definition` |
| frozen | `HANDLES` | `handles_event_definition` |
| frozen | `HAS_EXPRESSION` | `has_expression` |
| frozen | `IMPLEMENTS_TASK` | `implements_task` |
| frozen | `INPUT_TO` | `input_to_strategy` |
| frozen | `INSTANCE_OF` | `classified_as`, `expression_kind_of`, `instance_of`, `instantiates_state` |
| frozen | `ISSUED_BY` | `issued_by` |
| frozen | `MANAGED_BY` | `managed_by` |
| frozen | `OBJECT_TYPE` | `declares_object_type` |
| frozen | `OBSERVES` | `observes_fact` |
| frozen | `OUTPUT_MUST_SATISFY` | `output_must_satisfy` |
| frozen | `OWNS` | `owns_resource`, `owns_state`, `subject_of_fact` |
| frozen | `PARAMETER_INPUT` | `parameter_input_to` |
| frozen | `PRODUCES` | `declares_decision_output` |
| frozen | `PROPOSES_DECISION` | `scenario_proposes_decision` |
| frozen | `PROPOSES_REPLAN` | `proposes_replan` |
| frozen | `PROVIDED_BY` | `capability_provided_by` |
| frozen | `PURSUES` | `pursues_objective` |
| frozen | `READS` | `declares_fact_input`, `reads_state_signature` |
| frozen | `READS_DEFINITION` | `reads_definition` |
| frozen | `READ_SCOPE` | `declares_read_scope` |
| frozen | `RECEIPT_FOR` | `delivery_receipt_for` |
| frozen | `RECEIVED_BY` | `received_by` |
| frozen | `REFERENCES` | `references_predicate` |
| frozen | `REPLAYS_COMMAND` | `replays_command_definition` |
| frozen | `REPLAYS_EVENT` | `replays_event_definition` |
| frozen | `REQUESTED_BY` | `requested_by_agent` |
| frozen | `REQUESTS_TASK` | `requests_task` |
| frozen | `REQUESTS_ZONE` | `requests_zone` |
| frozen | `REQUIRES` | `requires_capability`, `requires_resource` |
| frozen | `REQUIRES_ALLOCATION` | `requires_allocation` |
| frozen | `REQUIRES_RECEIPT` | `requires_delivery_receipt` |
| frozen | `RESPONDS_AS` | `feedback_agent_binding` |
| frozen | `RESULT_OF` | `result_of_predicate` |
| frozen | `SCENARIO_INPUT` | `scenario_input` |
| frozen | `SCHEDULES_CHECK` | `schedules_check_binding` |
| frozen | `SCHEDULES_EVENT` | `schedules_event_definition` |
| frozen | `SCHEDULES_UPDATE` | `schedules_fact` |
| frozen | `SCOPED_TO` | `scoped_to` |
| frozen | `SCOPES` | `applicability_expression_of` |
| frozen | `SELECTS_ALLOCATION` | `selects_allocation` |
| frozen | `SELECTS_LEASE_APPROVAL` | `selects_lease_approval` |
| frozen | `SELECTS_LEASE_REQUEST` | `selects_lease_request` |
| frozen | `SELECTS_RECEIPT` | `selects_delivery_receipt` |
| frozen | `SELECTS_TASK` | `selects_task` |
| frozen | `SENT_BY` | `sent_by` |
| frozen | `STATE_INPUT` | `state_input_to` |
| frozen | `SUBJECT_TYPE` | `declares_subject_type` |
| frozen | `TARGETS` | `command_target_binding` |
| frozen | `TARGET_REFERENCE` | `target_result_input_to` |
| frozen | `UPDATES` | `declares_fact_output`, `updates_state` |
| frozen | `USES` | `uses_constraint`, `uses_parameter_definition` |
| frozen | `USES_FIELD` | `relation_uses_field` |
| frozen | `USES_RULE` | `uses_rule` |
| frozen | `WAITS_FOR` | `declares_execution_dependency` |
| frozen | `WRITE_SCOPE` | `declares_write_scope` |
| legacy_source | `INSTANCE_OF` | `legacy_field_reference` |
| runtime | `DECLARES_COMMAND` | `declares_command_attempt` |
| runtime | `IMPLEMENTS_TASK` | `implements_task` |
| runtime | `ISSUED_BY` | `issued_by` |
| runtime | `RECEIPT_FOR` | `delivery_receipt_for` |
| runtime | `RECEIVED_BY` | `received_by` |
| runtime | `REPLAYS_COMMAND` | `replays_command_attempt` |
| runtime | `RESULT_OF` | `expectation_of_predicate` |
| runtime | `SENT_BY` | `sent_by` |
| runtime | `based_on` | `based_on_fact` |
| runtime | `baseline_evidence` | `baseline_evidence` |
| runtime | `binds_role` | `binds_role` |
| runtime | `calls_strategy` | `starts_strategy_invocation` |
| runtime | `candidate_instance_of` | `candidate_occurrence_of` |
| runtime | `composes_behavior` | `composes_behavior_execution`, `requests_behavior_definition` |
| runtime | `constrained_by` | `constrained_by` |
| runtime | `consumes_input_lot` | `consumes_input_lot` |
| runtime | `continues_task` | `continues_task` |
| runtime | `control_evidence` | `control_evidence` |
| runtime | `evaluates_predicate` | `evaluates_predicate` |
| runtime | `evaluates_rule` | `evaluates_rule` |
| runtime | `evidenced_by` | `evidenced_by` |
| runtime | `executed_by` | `execution_module_binding`, `realized_by_module_execution` |
| runtime | `feeds_back` | `feeds_back` |
| runtime | `follows_transformation` | `follows_transformation` |
| runtime | `followup_evidence` | `followup_evidence` |
| runtime | `generates_command` | `generates_command` |
| runtime | `input_to` | `input_to_strategy` |
| runtime | `instance_of` | `execution_of_module`, `instance_of` |
| runtime | `invoked_by` | `invoked_by` |
| runtime | `invokes_strategy` | `invocation_of_strategy` |
| runtime | `leg_of` | `leg_of` |
| runtime | `manifest_for` | `manifest_for` |
| runtime | `message_evidence_only` | `message_evidence_only` |
| runtime | `permits_or_blocks` | `declares_admission_for_behavior`, `permits_or_blocks` |
| runtime | `permits_or_blocks_execution` | `permits_or_blocks` |
| runtime | `produces_authored_fact` | `declares_authored_fact_output` |
| runtime | `produces_check_result` | `produces_check_result` |
| runtime | `produces_decision` | `produces_decision` |
| runtime | `produces_evaluation_result` | `produces_evaluation_result` |
| runtime | `produces_fact` | `produces_fact` |
| runtime | `produces_output_lot` | `produces_output_lot` |
| runtime | `produces_result` | `binding_result_reference` |
| runtime | `proposes_decision` | `proposes_decision` |
| runtime | `reads_fact` | `reads_fact` |
| runtime | `reads_input` | `reads_input` |
| runtime | `realizes_execution` | `realizes_execution` |
| runtime | `receipt_for` | `command_receipt_for`, `delivery_receipt_for` |
| runtime | `receipt_for_attempt` | `lifecycle_record_for_attempt` |
| runtime | `references_prior_review` | `references_prior_review` |
| runtime | `requested_by` | `requested_by_request` |
| runtime | `requested_by_attempt` | `requested_by_attempt` |
| runtime | `requires_check` | `requires_check` |
| runtime | `requires_evidence` | `requires_evidence` |
| runtime | `requires_fresh_check_definition` | `requires_fresh_check_definition` |
| runtime | `requires_resource` | `requires_resource` |
| runtime | `result_of_check` | `result_of_check` |
| runtime | `review_digest_evidence` | `review_digest_evidence` |
| runtime | `review_version_evidence` | `review_version_evidence` |
| runtime | `runtime_instance_of` | `conforms_to_transformation_relation`, `execution_of_module`, `instance_of` |
| runtime | `sample_custody_evidence` | `sample_custody_evidence` |
| runtime | `sample_seal_evidence` | `sample_seal_evidence` |
| runtime | `sample_volume_evidence` | `sample_volume_evidence` |
| runtime | `seat_requested` | `seat_requested` |
| runtime | `uses_binding` | `uses_binding` |
| runtime | `uses_constraint` | `uses_constraint` |
| runtime | `uses_equipment` | `uses_equipment` |
| runtime | `uses_history` | `uses_history` |
| runtime | `waits_for` | `waits_for` |
| runtime | `waits_for_transfer` | `waits_for_transfer` |
| source | `source_field_reference` | `source_field_reference` |

## Semantic requirements / 语义要求

The following requirements are checked for readiness. When the source does not provide one, preserve the source and report the precise gap. Do not fabricate it to satisfy the schema.

### node_type

Legacy meta-type declaration; its represented collection is not the enclosing storage collection.

Required meaning: identity, scope, version_or_missing, provenance.

### entity_type

Reusable entity class with applicability; separate from entity identity.

Required meaning: identity, scope, version_or_missing, provenance.

### entity

Identified physical, social, informational or infrastructural subject. Its agent is not an identity member.

Required meaning: exact_lifecycle_ref, entity_type, lifecycle_interval, capability_claims.

### state_specification

Typed attribute or relation field. Defines quantity/unit/frame/subject and temporal semantics, not a runtime value.

Required meaning: value_type, quantity, unit_if_quantified, frame_if_spatial, subject_applicability, temporal_validity, authority_selection_scope.

### fact

Immutable value record for one exact subject, state specification and validity/availability context.

Required meaning: subject_ref, state_specification_ref, typed_value_or_unavailable_reason, valid_time, availability_time, clock_domains, source_record_and_revision, computing_authority, quality_and_uncertainty.

### module

Configured reusable computation or execution boundary; runtime execution has its own kind.

Required meaning: interface, backend_mode, input_signature, output_fields, selected_authority_scope.

### agent

Independent decision participant with explicit observations, strategy, authority and target relations.

Required meaning: observation_scope, strategy_references, authority_operation_target_validity, arbitration_if_overlapping.

### strategy_definition

Versioned decision procedure signature; invocation is a separate record.

Required meaning: version, input_output_signature, parameter_signature, allowed_observations.

### capability_declaration

Frozen capability-family declaration. Source definition versus scoped configured claim is retained in semantic_level and payload; neither grants permission.

Required meaning: capability_meaning, applicability_or_claim_subject, version, verification_status.

### behavior_definition

Reusable execution process and declared composition requirements; no execution is inferred from a source level label.

Required meaning: capability_requirements, resources_with_dimension_amount_interval, modules, constraint_bindings, composition.

### request

Identified work or information request, separate from acceptance and outcome.

Required meaning: identity, scope, version_or_missing, provenance.

### objective

Desired outcome, deadline or quality, separate from hard constraints and actual result.

Required meaning: identity, scope, version_or_missing, provenance.

### decision

Authored or recorded decision output for an explicit context; no automatic command authority.

Required meaning: identity, scope, version_or_missing, provenance.

### task

Scoped task/attempt with objectives, participants, lifecycle and completion evidence.

Required meaning: identity, scope, version_or_missing, provenance.

### resource

Identified capacity-bearing, consumable or reservable supply; capacity, reservation and occupancy are separate.

Required meaning: capacity_dimensions, unit, ownership, reservation_semantics, occupancy_source, valid_scope.

### constraint_definition

Declarative physical/resource/permission/suitability restriction with applicability and separate enforcement binding.

Required meaning: requirement, assessment_axis, hard_or_soft, applicability, version, source, enforcement_binding_or_gap.

### predicate_definition

Named proposition computed by its fixed rule under exact bindings. Not a result.

Required meaning: rule_reference, binding_signature, three_valued_result_signature.

### rule_definition

Versioned full expression, main/applicability roots, parameters and native semantics requirements.

Required meaning: version, main_ast, separate_applicability_ast_if_present, parameter_scope, native_evaluator_requirement, all_temporal_controls.

### event_definition

Definition of change/occurrence with temporal and duplicate policy; truth alone is not an occurrence.

Required meaning: rule_reference, occurrence_policy, participant_signature, evidence_and_time_requirements.

### command_definition

Reusable interface/request template and composition; separate from each issued or hypothetical attempt.

Required meaning: target_signature, argument_signature, receipt_contract, effect_contract, behavior_composition.

### arbitration_policy

Explicit scope-conflict policy declaration; does not invent priority or a grant.

Required meaning: identity, scope, version_or_missing, provenance.

### evidence_source

Traceable source with version and proof scope. May be synthetic, documentary, measured or simulated.

Required meaning: identity, scope, version_or_missing, provenance.

### scenario_definition

Versioned authoring/fixture configuration and replay selection; not a live run.

Required meaning: identity, scope, version_or_missing, provenance.

### check_binding

Configured start/continue/effect/review check context. The check scope determines semantics; not all checks admit execution.

Required meaning: check_scope, constraint_refs, required_input_slots, query_and_cutoff, enforcement_module, unknown_false_conflict_policy.

### feedback_policy

Declared response to evidence; graph traversal/replay does not dispatch responses.

Required meaning: identity, scope, version_or_missing, provenance.

### predicate_result

A target result record, retaining executed/not-executed/expected provenance and diagnostic status.

Required meaning: identity, scope, version_or_missing, provenance.

### relation_definition

Declared relation schema with ordered roles, direction and permitted endpoints; not a relation occurrence.

Required meaning: role_schema, endpoint_types, direction, validity_and_authority_requirements, role_local_cardinality.

### parameter_definition

Declared parameter identity, lexical scope, type/unit and explicit source default, if any.

Required meaning: identity, scope, version_or_missing, provenance.

### spatial_zone

Identified geometry and coordinate/altitude datum; not a universal permission.

Required meaning: identity, scope, version_or_missing, provenance.

### lease_request

Scoped request for resource/airspace use; separate from an approval.

Required meaning: identity, scope, version_or_missing, provenance.

### lease_approval

Versioned decision about a lease request with authority and evidence status; synthetic grants have no real legal effect.

Required meaning: exact_request, granting_authority, validity, version, status, revocation.

### space_time_allocation

Explicit zone/resource/time allocation declaration; not observed occupancy or physical exclusivity.

Required meaning: approval, zone, participant, interval, coordinate_and_clock_domains, status.

### delivery_receipt

Information delivery record with exact sender, receiver, message/attempt and proof scope; no custody or effect implication.

Required meaning: message_or_command_attempt, sender, receiver, phase_or_delivery_status, proof_scope, actual_time_or_missing_reason.

### expression_type

Expression-kind declaration used by the inspection projection; not a verified native operator inventory.

Required meaning: identity, scope, version_or_missing, provenance.

### expression

Source-derived AST expression including scalar, constant, parameter, state, target, operator, temporal and unknown forms.

Required meaning: source_rule_identity, source_ast_path, unchanged_source_form, ordered_operands, operator_support_status.

### accounting_record

能量或资源核算条目与区间，保存贡献者和计算假设，区别于实测电量/实际消费。

Required meaning: assumptions, contributor_ids, counting_rule, intervals, measurement_status, quantity_units.

### artifact_change_record

经指定审核把输入版本变为输出版本的变更记录；缺失变更集或批准不得视为已发布。

Required meaning: approval_status, change_set_or_missing_reason, input_version, output_version, publication_status, review.

### artifact_version_binding

相关计划、模型与采集工件的精确版本绑定，声明对齐和审核的输入上下文。

Required meaning: alignment_scope, exact_artifact_versions, input_digests_or_missing_reason.

### authority_record

面向具体行为参与方、目标、操作与有效范围的权利声明；合成声明不建立真实权限。

Required meaning: actor, issuer_or_missing_reason, operations, synthetic_or_real_status, target, valid_scope.

### baseline_revision_acceptance_requirement

基线选择必须满足的身份、修订、摘要、批准、有效范围、坐标与撤销条件；年龄仅是独立偏好。

Required meaning: age_preference_separate, approval, effective_scope, frame_and_datum, revision_and_digest, revocation_status, target.

### behavior_execution

行为定义的一次执行上下文，保留命令、模块、检查和完成证据要求；未执行状态保持明确。

Required meaning: actual_start_finish_or_missing, attempt, completion_evidence, definition, module_route, requesting_command, required_checks, status.

### business_order

业务订单/工作请求的范围、参与者、任务尝试与截止要求；独立于执行效果。

Required meaning: attempt, deadline, participants, release, request, task.

### capability_claim

某实体在明确范围的能力声明，保持未认证/不可用状态；不是指令权限。

Required meaning: capability, capacity_if_claimed, entity, validity, verification.

### check_result

检查上下文的预期或运行结果，明确结果范围、证据与未知/阻塞的区别。

Required meaning: check, evidence_or_missing, executed_or_expectation, policy, proof_scope, verdict.

### command_attempt

此候选类型仅表示运行时命令尝试，不能当作冻结commands集合中的command_definition。 某命令定义的一次具体请求，绑定目标、任务尝试、参数与发出时刻；不等于已接受或已执行。 具体命令请求实例，绑定定义、任务尝试与目标；与接口回执、物理结果分离。

Required meaning: arguments, attempt, definition, issue_time_or_missing, issuer, operation, status, target, task.

### command_lifecycle_receipt

针对具体命令尝试的请求、接受等生命周期回执；只证明明示的控制接口阶段。

Required meaning: command_attempt, evidence_or_missing, ordered_lifecycle_states, physical_completion_status, proof_scope.

### command_receipt

某次具体命令的生命周期记录及证明范围；接受或已记录并不证明任务完成。

Required meaning: command_attempt, phase, proof_scope, receipt_time_or_missing, source, status.

### compute_job_resource_binding

计算任务、输入输出工件、模型版本与多维资源要求的绑定；预留和实际占用分别记录。

Required meaning: actual_occupancy_separate, enqueue_finish_or_missing, input_artifacts_and_digests, job_and_attempt, model_version, multi_dimension_resource_requests, output_artifact.

### constraint_check

具体开始或继续执行条件检查上下文，保留检查时刻、负责模块与当前所需证据。

Required meaning: check_scope, constraint_refs, cutoff, enforcement_module, execution_status, query_time, required_input_slots.

### constraint_check_result

一次精确检查的结果记录，保留检查定义、执行对象、输入证据、三项评估及未知或冲突处理；结果不自行授权动作。

Required meaning: actual_enforcement_status, assessment_axis_or_separate_physical_permission_suitability, behavior_attempt, check, check_scope, evidence_or_missing, unknown_conflict_policy, verdict.

### custody_and_responsibility_handover

责任或样品保管交接，绑定发送者、接收者、对象和证据；报告送达不等于样品保管转移。

Required meaning: custody_status, independent_transfer_evidence, investigation_responsibility, legal_liability_status, physical_sample_location, recipient, sample_subject, sender.

### effect_evaluation_result

针对某任务尝试的效果核验，分别保留期望目标、接受目标和实际效果证据。

Required meaning: accepted_target, actual_effect_evidence_or_missing, desired_target, task_attempt, verdict.

### engineering_review_record

工程审核人对指定版本计算产物的专业审核记录；数值非负不替代审核批准。

Required meaning: approval_status, quantity_uncertainty, reviewed_artifact_and_version, reviewer_role, signature_or_missing.

### evaluation_binding

具体规则/谓词评估的主体角色、字段投影、参数和时间上下文。 规则求值绑定，将具体主体、字段事实、参数、历史与时刻连接到固定规则和结果。

Required meaning: field_map, history, history_context, parameter_scope, parameters, query_and_cutoff, result_reference_not_execution, role_bindings, subject_roles, target_rule.

### event_occurrence

事件定义的一次候选或发生记录；是否发出、发生策略、参与者、时间及证据必须保持，不由真值自动推断。

Required meaning: availability, emitted_status, event_definition, evidence_or_missing, occurrence_policy, occurrence_time_or_missing, participants.

### future_followup_attempt

未来时窗内的新随访尝试，引用既有任务，但需要当前授权、检查与新证据。

Required meaning: current_permission, fresh_checks, new_time_window, no_implicit_reuse_of_old_grant, subject, task.

### human_actor

实际人员的决策/操作角色声明，区别于软件智能体；资格、认证和权利仍需具体来源。

Required meaning: authentication_status, authorization_status, no_agent_token_borrowing, person_entity, role.

### human_review_record

具体审核者对指定候选与输入版本的审核结果要求，未签署结果保持未知。

Required meaning: authentication_status, candidate_and_version, decision_or_missing, input_binding, reviewer.

### longitudinal_observation

跨日随访观测，保持样本身份、各观测时窗和指标的区别。

Required meaning: distinct_time_windows, fact_refs, no_implicit_causal_attribution, sample_identity.

### material_transformation

混合或过滤等物料转换，绑定输入批次、输出批次、中间批次、设备和数量证据；不凭批次名推导守恒或化学效果。

Required meaning: equipment, input_lots, input_output_evidence, mass_balance_status, material_fate, output_lots, process, quantity_units.

### migration_record

任务从源执行器到目标执行器的检查点、尝试、提交和清理上下文；传输回执不足以证明迁移完成。

Required meaning: attempt_generation, checkpoint_commit, cleanup, destination_executor, job, source_executor, status.

### module_execution

模块定义的一次计算或执行上下文，引用输入与输出事实；合成记录不证明后端曾运行。

Required meaning: behavior_context, execution_status, input_facts, module, output_facts, source_or_missing.

### paired_observation

同对象或匹配样点的前后测量，保存配对键、时间与对照证据；不自动推断因果。

Required meaning: before_after_facts, before_after_times, controls_if_claimed, no_implicit_causality, stable_pair_identity, uncertainty.

### passenger_itinerary

同一乘客行程的预定、航段、备降与接续结构，保留任务和尝试身份。

Required meaning: attempt, itinerary, legs, original_route, passenger_scope, status.

### passenger_leg

行程中的具体航段或接续段，绑定起讫、参与者和尝试；备降不等于原联程完成。

Required meaning: attempt, diversion_or_onward_role, itinerary, leg, origin_destination, participants, transfer_evidence.

### passenger_manifest_entry

一名乘客与飞机、座位、行程、航段、尝试的绑定；人数统计不替代登乘证据。

Required meaning: aircraft, attempt, individual_boarding_evidence, itinerary, leg, passenger, seat.

### predicate_evaluation

在明确绑定和时间截断下的一次谓词评估上下文；未连接引擎时保持未执行。

Required meaning: binding, engine_revision_or_missing, executed_status, prepared_inputs, query_and_cutoff, target_rule_revision, truth_and_applicability_separate.

### predicate_expectation

此候选类型保存明示的合成预期真值，不冒充冻结集合中的原生求值结果。

Required meaning: expected_truth, input_fact_refs, native_executed_false, truth_scope.

### prepared_input

规则调用所准备的事实快照，记录输入身份、投影、可用性与缺失原因。

Required meaning: available_at_query, binding, fact_or_missing, history_coverage, projection, slot.

### relation_instance

带有序角色端点、定义引用、时空或有效范围的关系实例；保管、物理连接等不可互换。

Required meaning: authority_or_missing, claim_status, definition, exact_scope, ordered_role_endpoints, validity.

### review_signature_record

某版本工件的审核与签名证据要求，明确审核人、版本与摘要；未签署不能变为已批准。

Required meaning: artifact, authority_status, digest_evidence, reviewer, signature_or_missing, version_evidence.

### sample_collection_record

一次采样操作记录，区分样品内容与容器、封签、体积、来源位置时间和保管责任。

Required meaning: container, custody_evidence, sample_contents, sample_time, sampling_point, seal_evidence, source_water_area, volume_evidence.

### strategy_invocation

一次策略调用上下文，绑定智能体、策略修订、可见事实、请求、目标、约束和时间截断；与策略定义分开。

Required meaning: agent, constraints, decision_or_missing, execution_status, objectives, permitted_observations, query_and_cutoff, request, strategy_revision.

### time_context

求值或历史所需的查询时间、时间域、窗口和可用性截断上下文。

Required meaning: availability_cutoff, clock_domain, history_requirement, history_samples_or_missing, query_time.

### link_configuration

Preserved source network link configuration; not per-link telemetry or proof of message delivery.

Required meaning: source_pointer, exact_payload.

### source_record

Opaque preserved source object; no invented ontology or execution semantics.

Required meaning: source_pointer, exact_payload.

### rule_ast_expression

Derived complete AST inspection node, not an authored alternative rule.

Required meaning: source_rule_id, source_ast_pointer, unchanged_value, root_role.

### activity

Source-backed business workflow with exact ordered steps and authored scenario mappings.

Required meaning: catalog_index, source_name, ordered_steps, source_pointer.

### activity_step

One exact source workflow step and ordered position; mapping is not completion.

Required meaning: activity, step_index, exact_name, source_pointer.

## Ambiguity ledger / 歧义和缺口

### AMB-001: ARGUMENT direction differs

Status: resolved_by_explicit_variant

Delivery source stores parent→child; agriculture/city/network store child→parent. Preserve endpoints, map to has_argument/argument_of and verify AST order. Source bytes remain unchanged.

### AMB-002: Runtime command spelling overlaps definition family

Status: resolved_by_semantic_profile

Runtime command, command_instance and command_attempt map to command_attempt only in runtime namespace. Frozen commands stay command_definition.

### AMB-003: Predicate expected truth vs evaluated result

Status: resolved_by_distinct_kind

Raw runtime predicate_result becomes predicate_expectation. Frozen predicate_results remains predicate_result with execution provenance. Neither may gain native-executed status.

### AMB-004: Generic INSTANCE_OF signature was too broad

Status: resolved_by_typed_variants

Meta-type classification is classified_as, fact→field is instantiates_state, expression→expression_type is expression_kind_of. Instance/definition family compatibility is checked separately.

### AMB-005: Unspecified or conflicting AST controls

Status: unresolved_native_semantics

Preserve both window_s and duration_seconds when present, all control values and absence. No alias precedence or native temporal interpretation is invented.

### AMB-006: Missing native Atlas source and evaluator

Status: unresolved_source_gap

The exact full catalog, operator registry, rule IDs, grammar and native round-trip/evaluation parity remain unverified. Source AST preservation is still testable.

### AMB-007: Legacy capability declarations mix templates and claims

Status: resolved_without_forced_merge

Use capability_declaration for frozen family and preserve semantic_level. Explicit runtime capability_claim remains distinct. Authored claim scope does not prove a reusable verified capability or authority.

### AMB-008: Admission collection includes effect/review checks

Status: resolved_by_scope

check_binding preserves original scope and not_admission. Start, continue, effect_confirmation and review checks cannot be substituted for each other.

### AMB-009: Collection name inside city node-type record

Status: resolved_by_container

Actual enclosing collections.node_types determines node_type. Internal collection denotes represented family and remains unchanged in payload.

### AMB-010: Resource and control cardinality depends on role

Status: resolved_by_scoped_validation

No blanket one-per-node degree constraint. Exact attempt/binding/role/interval defines local cardinality; parallel operations and repeated receipts remain possible.

### AMB-011: Absent real permission/legal evidence

Status: unresolved_evidence_gap

Synthetic grants, legal placeholders and engineering thresholds are retained as source examples, never a verified real permission or legal rule.

### AMB-012: Graph canonical ID vs structured lifecycle Ref

Status: resolved_by_separation

Storage IDs do not repair or replace missing lifecycle members. Preserve typed epoch/generation and source precision.

### AMB-013: Cross-source definition equivalence

Status: conservative_nonmerge

Matching name or type is insufficient. Require explicit same namespace/id/revision/scope and exact semantic body. Missing revision blocks semantic cross-source merge.

### AMB-014: Source-only emergency reference edges

Status: resolved_as_evidence_only

source_field_reference preserves exact source field/path/index. It cannot imply authority, causal execution, legal applicability or physical effect.

### AMB-015: Bindings containing result references

Status: resolved_without_execution_claim

rule_evaluation_binding is compatible with evaluation_binding, but result_ref is a lineage reference, not evidence that native evaluation occurred.

### AMB-016: Lifecycle aggregate vs single receipt

Status: resolved_without_merge

command_lifecycle_receipt contains an ordered state sequence. It stays separate from command_receipt; neither proves task completion.

### AMB-017: Material transformation relation typing

Status: resolved_by_distinct_relation

A transformation references a relation schema via conforms_to_transformation_relation; it is not cast into a generic physical custody relation instance.

### AMB-019: Legacy emergency check field labelled INSTANCE_OF

Status: resolved_as_evidence_only

Network source extraction contains 42 check_binding→state_specification state_field references labelled INSTANCE_OF. Retain endpoints and source payload, map only that precise role/signature to legacy_field_reference; never a type cast.

### AMB-022: JSON null versus semantic Unknown

Status: resolved_by_structural_projection

Literal is_null describes JSON structure only. Unknown truth and missing/unavailable evidence require explicit semantic statuses; neither null, false nor zero may substitute for one another.

### AMB-021: Authority record includes declared scope

Status: resolved_by_semantic_level

The 27 network authority_record entries explicitly have definition level and real_authority=false. Retain their declared level; they are scoped authorization declarations, not observed grants. Runtime authority records remain independently typed by semantic_level.

### AMB-020: Unmodelled source link configuration

Status: resolved_as_preservation_kind

Source D02 link_configuration is preserved as source-record configuration, not coerced into a link entity or observed link fact.

### AMB-018: Validity and availability clocks

Status: unresolved_where_source_missing

No clock mapping, commit cutoff, freshness, interpolation or future-evidence admission is fabricated. Explicit unavailable diagnostics block execution readiness.

## Evidence boundary / 证据边界

Authoring validity, lossless import/export, fixture exercise, native rule parity and real-backend readiness are reported separately. The accepted stage-one review covers its bounded examples and explicit source steps. It is not an exhaustive catalog of all future situations, native execution or proof of physical success. The private workbench may inspect and edit declarations and replay authored data; graph selection never dispatches commands.
