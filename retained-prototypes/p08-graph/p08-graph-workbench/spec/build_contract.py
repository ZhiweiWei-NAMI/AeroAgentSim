"""Rebuild the source-pinned contract and schemas. Writes only this spec directory."""
from pathlib import Path
import json,hashlib,itertools,collections
from contract import *
HERE=Path(__file__).resolve().parent
STAGE=Path('/workspace/shared/p02_stage1')
INV_PATH=Path('/workspace/shared/aero-unified-graph-inventory-20261005.json')
LEDGER_PATH=STAGE/'review/proposed-runtime-contract-ledger.json'
INV=json.loads(INV_PATH.read_text());LEDGER=json.loads(LEDGER_PATH.read_text())
write=lambda name,x:(HERE/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

BASE_DESCRIPTIONS={
'node_type':'Legacy meta-type declaration; its represented collection is not the enclosing storage collection.',
'entity_type':'Reusable entity class with applicability; separate from entity identity.',
'entity':'Identified physical, social, informational or infrastructural subject. Its agent is not an identity member.',
'state_specification':'Typed attribute or relation field. Defines quantity/unit/frame/subject and temporal semantics, not a runtime value.',
'fact':'Immutable value record for one exact subject, state specification and validity/availability context.',
'module':'Configured reusable computation or execution boundary; runtime execution has its own kind.',
'agent':'Independent decision participant with explicit observations, strategy, authority and target relations.',
'strategy_definition':'Versioned decision procedure signature; invocation is a separate record.',
'capability_declaration':'Frozen capability-family declaration. Source definition versus scoped configured claim is retained in semantic_level and payload; neither grants permission.',
'behavior_definition':'Reusable execution process and declared composition requirements; no execution is inferred from a source level label.',
'request':'Identified work or information request, separate from acceptance and outcome.',
'objective':'Desired outcome, deadline or quality, separate from hard constraints and actual result.',
'decision':'Authored or recorded decision output for an explicit context; no automatic command authority.',
' task':'Scoped task/attempt with objectives, participants, lifecycle and completion evidence.',
'resource':'Identified capacity-bearing, consumable or reservable supply; capacity, reservation and occupancy are separate.',
'constraint_definition':'Declarative physical/resource/permission/suitability restriction with applicability and separate enforcement binding.',
'predicate_definition':'Named proposition computed by its fixed rule under exact bindings. Not a result.',
'rule_definition':'Versioned full expression, main/applicability roots, parameters and native semantics requirements.',
'event_definition':'Definition of change/occurrence with temporal and duplicate policy; truth alone is not an occurrence.',
'command_definition':'Reusable interface/request template and composition; separate from each issued or hypothetical attempt.',
'arbitration_policy':'Explicit scope-conflict policy declaration; does not invent priority or a grant.',
'evidence_source':'Traceable source with version and proof scope. May be synthetic, documentary, measured or simulated.',
'scenario_definition':'Versioned authoring/fixture configuration and replay selection; not a live run.',
'check_binding':'Configured start/continue/effect/review check context. The check scope determines semantics; not all checks admit execution.',
'feedback_policy':'Declared response to evidence; graph traversal/replay does not dispatch responses.',
'predicate_result':'A target result record, retaining executed/not-executed/expected provenance and diagnostic status.',
'relation_definition':'Declared relation schema with ordered roles, direction and permitted endpoints; not a relation occurrence.',
'parameter_definition':'Declared parameter identity, lexical scope, type/unit and explicit source default, if any.',
'spatial_zone':'Identified geometry and coordinate/altitude datum; not a universal permission.',
'lease_request':'Scoped request for resource/airspace use; separate from an approval.',
'lease_approval':'Versioned decision about a lease request with authority and evidence status; synthetic grants have no real legal effect.',
'space_time_allocation':'Explicit zone/resource/time allocation declaration; not observed occupancy or physical exclusivity.',
'delivery_receipt':'Information delivery record with exact sender, receiver, message/attempt and proof scope; no custody or effect implication.',
'expression_type':'Expression-kind declaration used by the inspection projection; not a verified native operator inventory.',
'expression':'Source-derived AST expression including scalar, constant, parameter, state, target, operator, temporal and unknown forms.',
}
BASE_DESCRIPTIONS['task']=BASE_DESCRIPTIONS.pop(' task')
BASE_OBLIGATIONS={
'entity':['exact_lifecycle_ref','entity_type','lifecycle_interval','capability_claims'],
'state_specification':['value_type','quantity','unit_if_quantified','frame_if_spatial','subject_applicability','temporal_validity','authority_selection_scope'],
'fact':['subject_ref','state_specification_ref','typed_value_or_unavailable_reason','valid_time','availability_time','clock_domains','source_record_and_revision','computing_authority','quality_and_uncertainty'],
'agent':['observation_scope','strategy_references','authority_operation_target_validity','arbitration_if_overlapping'],
'strategy_definition':['version','input_output_signature','parameter_signature','allowed_observations'],
'module':['interface','backend_mode','input_signature','output_fields','selected_authority_scope'],
'capability_declaration':['capability_meaning','applicability_or_claim_subject','version','verification_status'],
'behavior_definition':['capability_requirements','resources_with_dimension_amount_interval','modules','constraint_bindings','composition'],
'command_definition':['target_signature','argument_signature','receipt_contract','effect_contract','behavior_composition'],
'constraint_definition':['requirement','assessment_axis','hard_or_soft','applicability','version','source','enforcement_binding_or_gap'],
'rule_definition':['version','main_ast','separate_applicability_ast_if_present','parameter_scope','native_evaluator_requirement','all_temporal_controls'],
'predicate_definition':['rule_reference','binding_signature','three_valued_result_signature'],
'event_definition':['rule_reference','occurrence_policy','participant_signature','evidence_and_time_requirements'],
'resource':['capacity_dimensions','unit','ownership','reservation_semantics','occupancy_source','valid_scope'],
'check_binding':['check_scope','constraint_refs','required_input_slots','query_and_cutoff','enforcement_module','unknown_false_conflict_policy'],
'relation_definition':['role_schema','endpoint_types','direction','validity_and_authority_requirements','role_local_cardinality'],
'expression':['source_rule_identity','source_ast_path','unchanged_source_form','ordered_operands','operator_support_status'],
'delivery_receipt':['message_or_command_attempt','sender','receiver','phase_or_delivery_status','proof_scope','actual_time_or_missing_reason'],
'lease_approval':['exact_request','granting_authority','validity','version','status','revocation'],
'space_time_allocation':['approval','zone','participant','interval','coordinate_and_clock_domains','status'],
}
RT_OBLIGATIONS={
'accounting_record':['contributor_ids','intervals','quantity_units','counting_rule','assumptions','measurement_status'],
'artifact_change_record':['input_version','output_version','review','change_set_or_missing_reason','approval_status','publication_status'],
'artifact_version_binding':['exact_artifact_versions','input_digests_or_missing_reason','alignment_scope'],
'authority_record':['actor','target','operations','valid_scope','issuer_or_missing_reason','synthetic_or_real_status'],
'baseline_revision_acceptance_requirement':['target','revision_and_digest','approval','effective_scope','frame_and_datum','revocation_status','age_preference_separate'],
'behavior_execution':['definition','attempt','requesting_command','required_checks','module_route','status','actual_start_finish_or_missing','completion_evidence'],
'business_order':['request','task','attempt','participants','release','deadline'],
'capability_claim':['entity','capability','validity','verification','capacity_if_claimed'],
'check_result':['check','verdict','evidence_or_missing','proof_scope','policy','executed_or_expectation'],
'command':['target','issuer','attempt','task','operation','issue_time_or_missing','status'],
'command_attempt':['definition','target','attempt','arguments','issue_time_or_missing','status'],
'command_instance':['definition','target','attempt','arguments','issue_time_or_missing','status'],
'command_lifecycle_receipt':['command_attempt','ordered_lifecycle_states','evidence_or_missing','proof_scope','physical_completion_status'],
'command_receipt':['command_attempt','phase','status','receipt_time_or_missing','source','proof_scope'],
'compute_job_resource_binding':['job_and_attempt','model_version','input_artifacts_and_digests','output_artifact','multi_dimension_resource_requests','actual_occupancy_separate','enqueue_finish_or_missing'],
'constraint_check':['query_time','check_scope','constraint_refs','required_input_slots','cutoff','enforcement_module','execution_status'],
'constraint_check_result':['check','behavior_attempt','check_scope','assessment_axis_or_separate_physical_permission_suitability','evidence_or_missing','verdict','unknown_conflict_policy','actual_enforcement_status'],
'custody_and_responsibility_handover':['sender','recipient','sample_subject','physical_sample_location','custody_status','investigation_responsibility','legal_liability_status','independent_transfer_evidence'],
'effect_evaluation_result':['task_attempt','desired_target','accepted_target','actual_effect_evidence_or_missing','verdict'],
'engineering_review_record':['reviewer_role','reviewed_artifact_and_version','approval_status','signature_or_missing','quantity_uncertainty'],
'evaluation_binding':['target_rule','role_bindings','field_map','parameter_scope','query_and_cutoff','history_context'],
'event_occurrence':['event_definition','occurrence_policy','participants','occurrence_time_or_missing','availability','emitted_status','evidence_or_missing'],
'future_followup_attempt':['task','subject','new_time_window','fresh_checks','current_permission','no_implicit_reuse_of_old_grant'],
'human_actor':['person_entity','role','authentication_status','authorization_status','no_agent_token_borrowing'],
'human_review_record':['reviewer','candidate_and_version','input_binding','decision_or_missing','authentication_status'],
'longitudinal_observation':['sample_identity','distinct_time_windows','fact_refs','no_implicit_causal_attribution'],
'material_transformation':['process','equipment','input_lots','output_lots','input_output_evidence','quantity_units','mass_balance_status','material_fate'],
'migration_record':['job','source_executor','destination_executor','attempt_generation','checkpoint_commit','cleanup','status'],
'module_execution':['module','behavior_context','input_facts','output_facts','execution_status','source_or_missing'],
'paired_observation':['stable_pair_identity','before_after_times','before_after_facts','controls_if_claimed','uncertainty','no_implicit_causality'],
'passenger_itinerary':['passenger_scope','itinerary','attempt','original_route','legs','status'],
'passenger_leg':['itinerary','leg','attempt','participants','origin_destination','diversion_or_onward_role','transfer_evidence'],
'passenger_manifest_entry':['passenger','aircraft','seat','itinerary','leg','attempt','individual_boarding_evidence'],
'predicate_evaluation':['binding','target_rule_revision','query_and_cutoff','prepared_inputs','engine_revision_or_missing','executed_status','truth_and_applicability_separate'],
'predicate_result':['expected_truth','input_fact_refs','truth_scope','native_executed_false'],
'prepared_input':['binding','slot','fact_or_missing','available_at_query','history_coverage','projection'],
'relation_instance':['definition','ordered_role_endpoints','exact_scope','validity','authority_or_missing','claim_status'],
'review_signature_record':['reviewer','artifact','version_evidence','digest_evidence','signature_or_missing','authority_status'],
'rule_evaluation_binding':['target_rule','subject_roles','field_map','parameters','query_and_cutoff','history','result_reference_not_execution'],
'sample_collection_record':['sample_contents','container','source_water_area','sampling_point','sample_time','volume_evidence','seal_evidence','custody_evidence'],
'strategy_invocation':['agent','strategy_revision','permitted_observations','request','objectives','constraints','query_and_cutoff','decision_or_missing','execution_status'],
'time_context':['query_time','availability_cutoff','clock_domain','history_requirement','history_samples_or_missing'],
}
assert set(RT_OBLIGATIONS)=={x['kind'] for x in LEDGER['type_definitions']}
node_types=[]
for collection,kind in COLLECTION_KINDS.items():
    levels=['definition'] if collection in DEFINITION_COLLECTIONS else ['configured_instance']
    if collection in RUNTIME_COLLECTIONS:levels=['runtime_record']
    if collection=='sources':levels=['source_record']
    if collection=='capabilities':levels=['definition','configured_instance']
    node_types.append({'kind':kind,'source_collections':[collection],'semantic_levels':levels,'definition':BASE_DESCRIPTIONS[kind],
      'semantic_obligations':BASE_OBLIGATIONS.get(kind,['identity','scope','version_or_missing','provenance']),
      'missing_obligation_policy':'Preserve source and emit readiness gap. Missing semantic evidence does not corrupt otherwise lossless authoring import.'})
for kind,grp in itertools.groupby(sorted(LEDGER['type_definitions'],key=lambda x:canonical_kind(x['kind'])),key=lambda x:canonical_kind(x['kind'])):
    xs=list(grp)
    node_types.append({'kind':kind,'source_runtime_kinds':[x['kind'] for x in xs], 'semantic_levels':['definition','runtime_record'] if kind=='authority_record' else ['runtime_record'],
       'definition':' '.join(x['definition'] for x in xs),
       'semantic_obligations':sorted(set(sum([RT_OBLIGATIONS[x['kind']] for x in xs],[]))),
       'raw_profiles':[{'raw_kind':x['kind'],'occurrence_count':x['occurrence_count'],'observed_common_fields':x['observed_common_fields'],'observed_variant_fields':x['observed_optional_or_variant_fields']} for x in xs],
       'missing_obligation_policy':'No inferred metadata or status promotion; diagnose unresolved execution/evidence requirements.'})
# Sources beyond the frozen collections are preservation-only objects when needed.
node_types += [{'kind':'link_configuration','semantic_levels':['source_record'],'definition':'Preserved source network link configuration; not per-link telemetry or proof of message delivery.','semantic_obligations':['source_pointer','exact_payload']}, {'kind':'source_record','semantic_levels':['source_record'],'definition':'Opaque preserved source object; no invented ontology or execution semantics.','semantic_obligations':['source_pointer','exact_payload']},
 {'kind':'rule_ast_expression','semantic_levels':['definition'],'definition':'Derived complete AST inspection node, not an authored alternative rule.','semantic_obligations':['source_rule_id','source_ast_pointer','unchanged_value','root_role']},
 {'kind':'activity','semantic_levels':['definition'],'definition':'Source-backed business workflow with exact ordered steps and authored scenario mappings.','semantic_obligations':['catalog_index','source_name','ordered_steps','source_pointer']},
 {'kind':'activity_step','semantic_levels':['definition'],'definition':'One exact source workflow step and ordered position; mapping is not completion.','semantic_obligations':['activity','step_index','exact_name','source_pointer']}]
write('node-type-registry.json',{'schema_version':'p08.node-type-registry/v1','contract_version':CONTRACT_VERSION,'kinds':node_types})

# Expand frozen signatures by typed endpoints. INSTANCE_OF is deliberately narrowed.
variants=[]
for r in INV['relations']:
    name=r['relation']
    if name=='INSTANCE_OF':
        pairs=[(c,'node_types') for c in COLLECTION_KINDS if c not in ('node_types','expression_types')]+[('entities','entity_types'),('facts','fields'),('expressions','expression_types')]
    else:pairs=list(itertools.product(r['from'],r['to']))
    for s,t in pairs:
        arts=['agriculture/agriculture-instances.json','delivery/delivery-activity-instances.json'] if name=='ARGUMENT' else [None]
        for art in arts:
            canonical=canonical_relation(name,s,t,source_collection=s,target_collection=t,frozen_relation=name,artifact=art)
            variants.append({'id':relation_variant_id('frozen',name,s,t,canonical),'source_namespace':'frozen','raw_relation':name,
                'source_key':s,'target_key':t,'source_kind':COLLECTION_KINDS[s],'target_kind':COLLECTION_KINDS[t],
                'relation':canonical,'direction':'preserve_source_to_target','roles_observed':r.get('roles',[]),
                'role_policy':'Preserve source role. Absence is explicit unspecified, never a wildcard grant.',
                'source_artifact_family':('delivery' if 'delivery' in art else 'agriculture_city_network') if art else None,
                'support':'declaration_or_fixture_only','semantic_limit':'Signature compatibility does not establish native evaluation, physical effect, legal authority or admission.'})
for r in LEDGER['relation_definitions']:
    for v in r['variants']:
        s,t=v['source_kind'],v['target_kind']
        canonical=canonical_relation(r['relation'],s,t,source_collection=s if s in COLLECTION_KINDS else None,target_collection=t if t in COLLECTION_KINDS else None)
        variants.append({'id':relation_variant_id('runtime',r['relation'],s,t,canonical),'source_namespace':'runtime','raw_relation':r['relation'],
          'source_key':s,'target_key':t,'source_kind':canonical_kind(s,s if s in COLLECTION_KINDS else None),'target_kind':canonical_kind(t,t if t in COLLECTION_KINDS else None),
          'relation':canonical,'direction':'preserve_source_to_target','definition':r['definition'],'roles_observed':v['roles_observed'],
          'occurrence_count':len(v['all_occurrence_refs']),'example_source':{k:v['example'][k] for k in ('artifact','case','id')},
          'role_policy':'Role remains occurrence-specific; observed roles are not a closed vocabulary.',
          'support':'authored_runtime_record_only','semantic_limit':'Candidate, expected, missing and not_executed states are not successful execution.'})
# Preserve the actual legacy classification of proposed command attempts without casting them into definitions.
variants.append({'id':relation_variant_id('frozen','INSTANCE_OF','command','node_types','classified_as'),'source_namespace':'frozen','raw_relation':'INSTANCE_OF','source_key':'command','target_key':'node_types','source_kind':'command_attempt','target_kind':'node_type','relation':'classified_as','direction':'preserve_source_to_target','definition':'Legacy meta-classification only; not an instance_of command_definition cast.'})
variants.append({'id':relation_variant_id('frozen','INSTANCE_OF','predicate_result','node_types','classified_as'),'source_namespace':'frozen','raw_relation':'INSTANCE_OF','source_key':'predicate_result','target_key':'node_types','source_kind':'predicate_expectation','target_kind':'node_type','relation':'classified_as','direction':'preserve_source_to_target','definition':'Legacy result-family meta-classification only; an authored expectation does not become an executed native result.'})
variants.append({'id':'p08:rv:legacy-check-field-reference','source_namespace':'legacy_source','raw_relation':'INSTANCE_OF','source_key':'admission_checks','target_key':'fields','source_kind':'check_binding','target_kind':'state_specification','relation':'legacy_field_reference','direction':'preserve_source_to_target','definition':'Legacy emergency extractor labelled field_id as INSTANCE_OF. Preserve explicit field reference only; do not cast the check into a state specification.','required_role':'state_field','role_policy':'Only exact source state_field reference.'})
variants.append({'id':'p08:rv:source-field-reference','source_namespace':'source','raw_relation':'source_field_reference','source_key':'*','target_key':'*','source_kind':'*','target_kind':'*','relation':'source_field_reference','direction':'preserve_source_to_target','definition':'An explicit source reference field resolves to the target; no additional semantic relationship, causality or authority is inferred.','required_metadata':['source_pointer','source_field_path'],'role_policy':'Exact source field path and array index are retained.'})
REL_OBLIGATIONS={
 'controls':['actor','target','operation_scope','authority_ref','validity','arbitration_if_overlap'],
 'updates_state':['selected_computing_authority','exact_run_subject_generation_field_validity_scope'],
 'requires_resource':['capacity_dimension','amount','unit','use_or_reservation_interval','purpose_role'],
 'requires_capability':['capability_applicability','verification_or_unknown'],
 'instance_of':['matching_definition_family','definition_revision','instance_scope'],
 'classified_as':['exact_legacy_meta_type','no_definition_or_instance_cast'],
 'instantiates_state':['typed_value_compatible_with_field','exact_subject_scope'],
 'argument_of':['child_to_parent','operand_index','source_ast_path','same_rule_root_context'],
 'has_argument':['parent_to_child','operand_index','source_ast_path','same_rule_root_context'],
 'has_expression':['main_or_applicability_role','source_root_path'],
 'applicability_expression_of':['independent_scope_status','not_main_truth'],
 'binds_role':['role_label','exact_ref','declared_endpoint_order','binding_scope'],
 'permits_or_blocks':['start_or_continue_scope','exact_execution_attempt','verdict','enforcement_policy','evidence_scope'],
 'uses_constraint':['applicability_status','constraint_revision','check_scope'],
 'waits_for':['exact_predecessor_attempt','required_completion_condition','unknown_policy'],
 'waits_for_transfer':['individual_passenger_handover_evidence','exact_legs_and_attempts'],
 'reads_fact':['input_slot','validity','availability_cutoff','permitted_visibility','exact_subject_generation'],
 'produces_fact':['executing_module','field_authority','source_revision','validity_and_availability'],
 'composes_behavior_execution':['composition_mode','group','operand_order_or_condition','exact_attempts'],
 'composes_behavior_definition':['composition_mode','group','operand_order_or_condition'],
 'constraint_basis':['source_kind','version','proof_scope','jurisdiction_effective_scope_if_regulatory','no_invented_law'],
 'evidenced_by':['exact_source_revision','proof_scope','evidence_availability'],
 'source_field_reference':['exact_source_field_path','ordered_index_if_array','evidence_only'],
}
for v in variants:v['semantic_obligations']=REL_OBLIGATIONS.get(v['relation'],['exact_endpoints','role','scope','version_or_missing','source_evidence'])
write('relation-registry.json',{'schema_version':'p08.relation-registry/v1','contract_version':CONTRACT_VERSION,
 'policy':{'directed_multigraph':True,'edge_identity':'stable occurrence ID, never endpoint pair','role_cardinality':'Declared role and exact binding/attempt/validity scope only; no blanket node-degree bounds','native_execution':False},
 'counts':{'frozen_source_relation_names':len(INV['relations']),'runtime_source_relation_names':len(LEDGER['relation_definitions']),'canonical_relation_names':len({v['relation'] for v in variants}),'typed_variants':len(variants)},
 'variants':variants})

node_mappings=[]
for c,k in COLLECTION_KINDS.items():node_mappings.append({'namespace':'frozen_collection','source':c,'target':k,'policy':'actual enclosing collection, never internal represented-type field'})
for extra in ('link_configuration','radio_profile_declaration','compute_profile_declaration'):
    node_mappings.append({'namespace':'source_preservation','source':extra,'target':canonical_kind(source_collection=extra),'decision':'preserve_source_configuration_no_runtime_claim'})
for x in LEDGER['type_definitions']:
    raw=x['kind'];can=canonical_kind(raw)
    decision='compatible_alias' if raw in ('command','command_instance','rule_evaluation_binding') else 'semantic_disambiguation' if raw=='predicate_result' else 'preserve_distinct_kind'
    node_mappings.append({'namespace':'proposed_runtime_kind','source':raw,'target':can,'decision':decision,'definition':x['definition'],'occurrences':x['occurrence_count'],'lossless_fields':'all original fields and source kind preserved; missing canonical semantic requirements remain gaps'})
write('migration-mappings.json',{'schema_version':'p08.migration-mappings/v1','contract_version':CONTRACT_VERSION,
 'node_mappings':node_mappings,'relation_mappings':[{'variant_id':v['id'],'namespace':v['source_namespace'],'raw_relation':v['raw_relation'],'source_key':v['source_key'],'target_key':v['target_key'],'target_relation':v['relation'],'source_artifact_family':v.get('source_artifact_family'),'reverse_endpoints':False} for v in variants],
 'runtime_alias_decisions':[
  {'raw_kinds':['command','command_attempt','command_instance'],'canonical_kind':'command_attempt','basis':'All represent one specific execution request/attempt; arguments, target, attempt and lifecycle remain in raw profiles. No merging of occurrence identities.'},
  {'raw_kinds':['evaluation_binding','rule_evaluation_binding'],'canonical_kind':'evaluation_binding','basis':'Both bind fixed rules to exact role, field, parameter and time context. A result reference in one profile does not turn the binding into a performed evaluation.'}],
 'preserved_distinctions':[
  {'kinds':['command_lifecycle_receipt','command_receipt'],'reason':'A multi-state lifecycle record is not a single phase receipt.'},
  {'kinds':['check_result','constraint_check_result','effect_evaluation_result'],'reason':'Generic, constraint-scoped and effect-scoped proof obligations differ.'},
  {'kinds':['predicate_expectation','predicate_result','predicate_evaluation'],'reason':'Expected truth, result record and evaluation execution are distinct. Not-executed results stay not executed.'},
  {'kinds':['human_review_record','engineering_review_record','review_signature_record'],'reason':'General review, professional artifact review and signature/version evidence have different roles and proof requirements.'}],
 'export_policy':'Round-trip exact source JSON using preserved occurrence pointers and payloads. Export canonical graph separately. No implicit legacy rewrite, source reordering or operator normalization.'})

# Draft 2020-12 exchange schema. Domain obligations are separately validated, never invented.
nonempty={'type':'string','minLength':1}
source_record={'type':'object','required':['artifact','pointer','source_id'],'properties':{'artifact':nonempty,'pointer':{'type':'string','pattern':'^(|/.*)$'},'source_id':{'type':['string','integer','null']},'case_id':{'type':['string','null']},'sha256':{'type':'string','pattern':'^[a-f0-9]{64}$'}},'additionalProperties':True}
ref={'type':'object','required':['run_id','epoch','id','generation','ref_type'],'properties':{'run_id':nonempty,'epoch':{'type':['string','integer']},'id':nonempty,'generation':{'type':['string','integer']},'ref_type':nonempty},'additionalProperties':False}
json_value={'anyOf':[{'type':['null','boolean','string','number']},{'type':'array','items':{'$ref':'#/$defs/json_value'}},{'type':'object','additionalProperties':{'$ref':'#/$defs/json_value'}}]}
timepoint={'type':'object','required':['clock_domain','value','unit'],'properties':{'clock_domain':nonempty,'value':{'type':['string','number']},'unit':{'enum':['ns','us','ms','s','tick','source_native']},'source_path':{'type':'string'}},'allOf':[{'if':{'properties':{'unit':{'const':'ns'}}},'then':{'properties':{'value':{'type':'string','pattern':'^-?[0-9]+$'}}}}],'additionalProperties':True}
readiness={'type':'object','properties':{k:{'enum':['valid','invalid','ready','not_ready','unverified','not_executed','not_applicable']} for k in ['authoring','preservation','fixture_execution','native_rule','real_backend']},'additionalProperties':True}
provenance={'type':['object','string'],'description':'Source provenance is preserved. A synthetic fixture cannot be relabelled as measured/native/live evidence.'}
node={'type':'object','required':['id','kind','semantic_level','label','provenance','source_records','payload'],'properties':{'id':nonempty,'kind':{'enum':sorted({n['kind'] for n in node_types})},'semantic_level':{'enum':list(NODE_LEVELS)},'label':{'type':'string'},'raw_kind':{'type':['string','null']},'source_collection':{'type':['string','null']},'provenance':provenance,'source_records':{'type':'array','minItems':1,'items':{'$ref':'#/$defs/source_record'}},'payload':{'$ref':'#/$defs/json_value'},'identity':{'type':'object'},'semantics':{'type':'object'},'readiness':{'$ref':'#/$defs/readiness'}},'additionalProperties':True,'allOf':[]}
for n in node_types:node['allOf'].append({'if':{'properties':{'kind':{'const':n['kind']}},'required':['kind']},'then':{'properties':{'semantic_level':{'enum':n['semantic_levels']}}}})
edge={'type':'object','required':['id','relation','source','target','role','semantic_level','provenance','source_records','payload'],'properties':{'id':nonempty,'relation':{'enum':sorted({v['relation'] for v in variants})},'source':nonempty,'target':nonempty,'role':nonempty,'semantic_level':{'enum':list(EDGE_LEVELS)},'raw_relation':nonempty,'contract_variant':{'type':['string','null']},'provenance':provenance,'source_records':{'type':'array','minItems':1,'items':{'$ref':'#/$defs/source_record'}},'payload':{'$ref':'#/$defs/json_value'},'semantics':{'type':'object'}},'additionalProperties':True}
schema={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:aeroagentsim:p08:typed-graph:v1','title':'P08 lossless typed authoring graph','description':'Shape and preservation validity are distinct from semantic readiness and native/live execution. Payloads retain original unsupported constructs.','type':'object','required':['schema_version','nodes','edges'],'properties':{'schema_version':{'const':SCHEMA_VERSION},'contract_version':{'type':'string'},'nodes':{'type':'array','items':{'$ref':'#/$defs/node'}},'edges':{'type':'array','items':{'$ref':'#/$defs/edge'}},'readiness':{'$ref':'#/$defs/readiness'},'diagnostics':{'type':'array','items':{'type':'object'}}},'additionalProperties':True,'$defs':{'node':node,'edge':edge,'source_record':source_record,'exact_ref':ref,'json_value':json_value,'timepoint':timepoint,'readiness':readiness,'truth':{'enum':['True','False','Unknown']},'applicability':{'enum':['applicable','not_applicable','applicability_unknown']}}}
write('p08-graph.schema.json',schema)
write('ast-preservation.schema.json',{'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:aeroagentsim:p08:ast-preservation:v1','title':'Lossless source AST envelope, not a native grammar','type':'object','required':['source_pointer','root_role','native_source_ast','native_compatibility'],'properties':{'source_pointer':nonempty,'root_role':{'enum':['main','applicability','source_expression']},'native_source_ast':{'$ref':'#/$defs/json_value'},'native_compatibility':{'enum':['unverified','unsupported','verified_at_pinned_revision']},'diagnostics':{'type':'array'}},'additionalProperties':True,'$defs':{'json_value':json_value}})

amb=[
('AMB-001','ARGUMENT direction differs','resolved_by_explicit_variant','Delivery source stores parent→child; agriculture/city/network store child→parent. Preserve endpoints, map to has_argument/argument_of and verify AST order. Source bytes remain unchanged.'),
('AMB-002','Runtime command spelling overlaps definition family','resolved_by_semantic_profile','Runtime command, command_instance and command_attempt map to command_attempt only in runtime namespace. Frozen commands stay command_definition.'),
('AMB-003','Predicate expected truth vs evaluated result','resolved_by_distinct_kind','Raw runtime predicate_result becomes predicate_expectation. Frozen predicate_results remains predicate_result with execution provenance. Neither may gain native-executed status.'),
('AMB-004','Generic INSTANCE_OF signature was too broad','resolved_by_typed_variants','Meta-type classification is classified_as, fact→field is instantiates_state, expression→expression_type is expression_kind_of. Instance/definition family compatibility is checked separately.'),
('AMB-005','Unspecified or conflicting AST controls','unresolved_native_semantics','Preserve both window_s and duration_seconds when present, all control values and absence. No alias precedence or native temporal interpretation is invented.'),
('AMB-006','Missing native Atlas source and evaluator','unresolved_source_gap','The exact full catalog, operator registry, rule IDs, grammar and native round-trip/evaluation parity remain unverified. Source AST preservation is still testable.'),
('AMB-007','Legacy capability declarations mix templates and claims','resolved_without_forced_merge','Use capability_declaration for frozen family and preserve semantic_level. Explicit runtime capability_claim remains distinct. Authored claim scope does not prove a reusable verified capability or authority.'),
('AMB-008','Admission collection includes effect/review checks','resolved_by_scope','check_binding preserves original scope and not_admission. Start, continue, effect_confirmation and review checks cannot be substituted for each other.'),
('AMB-009','Collection name inside city node-type record','resolved_by_container','Actual enclosing collections.node_types determines node_type. Internal collection denotes represented family and remains unchanged in payload.'),
('AMB-010','Resource and control cardinality depends on role','resolved_by_scoped_validation','No blanket one-per-node degree constraint. Exact attempt/binding/role/interval defines local cardinality; parallel operations and repeated receipts remain possible.'),
('AMB-011','Absent real permission/legal evidence','unresolved_evidence_gap','Synthetic grants, legal placeholders and engineering thresholds are retained as source examples, never a verified real permission or legal rule.'),
('AMB-012','Graph canonical ID vs structured lifecycle Ref','resolved_by_separation','Storage IDs do not repair or replace missing lifecycle members. Preserve typed epoch/generation and source precision.'),
('AMB-013','Cross-source definition equivalence','conservative_nonmerge','Matching name or type is insufficient. Require explicit same namespace/id/revision/scope and exact semantic body. Missing revision blocks semantic cross-source merge.'),
('AMB-014','Source-only emergency reference edges','resolved_as_evidence_only','source_field_reference preserves exact source field/path/index. It cannot imply authority, causal execution, legal applicability or physical effect.'),
('AMB-015','Bindings containing result references','resolved_without_execution_claim','rule_evaluation_binding is compatible with evaluation_binding, but result_ref is a lineage reference, not evidence that native evaluation occurred.'),
('AMB-016','Lifecycle aggregate vs single receipt','resolved_without_merge','command_lifecycle_receipt contains an ordered state sequence. It stays separate from command_receipt; neither proves task completion.'),
('AMB-017','Material transformation relation typing','resolved_by_distinct_relation','A transformation references a relation schema via conforms_to_transformation_relation; it is not cast into a generic physical custody relation instance.'),
('AMB-019','Legacy emergency check field labelled INSTANCE_OF','resolved_as_evidence_only','Network source extraction contains 42 check_binding→state_specification state_field references labelled INSTANCE_OF. Retain endpoints and source payload, map only that precise role/signature to legacy_field_reference; never a type cast.'),
('AMB-022','JSON null versus semantic Unknown','resolved_by_structural_projection','Literal is_null describes JSON structure only. Unknown truth and missing/unavailable evidence require explicit semantic statuses; neither null, false nor zero may substitute for one another.'),
('AMB-021','Authority record includes declared scope','resolved_by_semantic_level','The 27 network authority_record entries explicitly have definition level and real_authority=false. Retain their declared level; they are scoped authorization declarations, not observed grants. Runtime authority records remain independently typed by semantic_level.'),
('AMB-020','Unmodelled source link configuration','resolved_as_preservation_kind','Source D02 link_configuration is preserved as source-record configuration, not coerced into a link entity or observed link fact.'),
('AMB-018','Validity and availability clocks','unresolved_where_source_missing','No clock mapping, commit cutoff, freshness, interpolation or future-evidence admission is fabricated. Explicit unavailable diagnostics block execution readiness.'),
]
write('ambiguity-ledger.json',{'schema_version':'p08.ambiguity-ledger/v1','entries':[{'id':i,'topic':t,'status':s,'decision':d} for i,t,s,d in amb]})
paths=[INV_PATH,LEDGER_PATH,STAGE/'review/STAGE1_REVIEW.md',STAGE/'sources/original_activity_catalog_35.json']+sorted(Path('/workspace/shared/AeroAgentSim/docs/integration').glob('*.md'))+[STAGE/p for p in ['agriculture/agriculture-instances.json','delivery/delivery-activity-instances.json','city/city-activity-instances.json','network/network-instance-catalog.json']]
write('source-manifest.json',{'schema_version':'p08.contract-sources/v1','sources':[{'path':str(p),'sha256':sha(p),'bytes':p.stat().st_size} for p in paths],'scope':{'source_workflows':35,'ordered_source_steps':226,'machine_examples':20,'runtime_raw_kinds':42,'runtime_raw_relations':69,'runtime_record_occurrences':4446,'runtime_relation_occurrences':12157},'native_source_inspected':False,'real_execution':False})
print(json.dumps({'node_kinds':len(node_types),'canonical_runtime_kinds':len({canonical_kind(x['kind']) for x in LEDGER['type_definitions']}),'relation_variants':len(variants),'canonical_relation_names':len({v['relation'] for v in variants})}))
