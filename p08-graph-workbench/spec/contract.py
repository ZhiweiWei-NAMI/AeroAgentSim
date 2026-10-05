"""P08 semantic mapping. Pure, lossless, offline; no evaluation or dispatch.

The source container is authoritative over a node's internal collection member.
Frozen and proposed runtime relation namespaces are selected explicitly, never by
case-folding. All source direction/role/value data stays in the payload.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json

SCHEMA_VERSION = 'p08.typed-graph/v1'
CONTRACT_VERSION = '1.0.0'
COLLECTION_KINDS = {
 'node_types':'node_type','entity_types':'entity_type','entities':'entity',
 'fields':'state_specification','facts':'fact','modules':'module','agents':'agent',
 'strategies':'strategy_definition','capabilities':'capability_declaration',
 'behaviors':'behavior_definition','requests':'request','objectives':'objective',
 'decisions':'decision','tasks':'task','resources':'resource','constraints':'constraint_definition',
 'predicates':'predicate_definition','rules':'rule_definition','events':'event_definition',
 'commands':'command_definition','arbitrations':'arbitration_policy','sources':'evidence_source',
 'scenarios':'scenario_definition','admission_checks':'check_binding','feedback_policies':'feedback_policy',
 'predicate_results':'predicate_result','relation_definitions':'relation_definition',
 'parameter_definitions':'parameter_definition','spatial_zones':'spatial_zone',
 'lease_requests':'lease_request','lease_approvals':'lease_approval',
 'space_time_allocations':'space_time_allocation','delivery_receipts':'delivery_receipt',
 'expression_types':'expression_type','expressions':'expression',
}
RUNTIME_ALIASES = {
 'command':'command_attempt', 'command_instance':'command_attempt',
 'rule_evaluation_binding':'evaluation_binding',
 'predicate_result':'predicate_expectation',
}
DEFINITION_COLLECTIONS = {'node_types','entity_types','fields','strategies','capabilities','behaviors',
 'constraints','predicates','rules','events','commands','arbitrations','scenarios','feedback_policies',
 'relation_definitions','parameter_definitions','expression_types','expressions'}
RUNTIME_COLLECTIONS = {'facts','decisions','predicate_results','delivery_receipts','lease_approvals'}
NODE_LEVELS = ('definition','configured_instance','runtime_record','source_record')
EDGE_LEVELS = ('definition','configuration','runtime','cross_level','source_record')

def canonical_kind(raw_kind=None, source_collection=None, *, collection=None):
    """Return kind; pass the actual enclosing collection, not a meta-type's field.

    Runtime kinds remain distinct unless RUNTIME_ALIASES contains the raw kind.
    Unknown kinds are preserved as ``unresolved:<raw>`` for diagnosis, not cast.
    """
    source_collection = source_collection if source_collection is not None else collection
    if source_collection in ('radio_profile_declaration','compute_profile_declaration'):
        return 'source_record'
    if source_collection == 'link_configuration':
        return 'link_configuration'
    if source_collection in COLLECTION_KINDS:
        return COLLECTION_KINDS[source_collection]
    if raw_kind is None:
        return 'unresolved:missing_kind'
    if raw_kind in COLLECTION_KINDS:
        return COLLECTION_KINDS[raw_kind]
    return RUNTIME_ALIASES.get(raw_kind, raw_kind)

def semantic_level(source_collection=None, raw_kind=None, raw_level=None):
    if source_collection in ('link_configuration','radio_profile_declaration','compute_profile_declaration'): return 'source_record'
    if source_collection == 'capabilities' and raw_level in ('authored_instance','configured_instance','instance'):
        return 'configured_instance'
    if source_collection in DEFINITION_COLLECTIONS:
        return 'definition'
    if source_collection in RUNTIME_COLLECTIONS:
        return 'runtime_record'
    if source_collection == 'sources':
        return 'source_record'
    if source_collection in COLLECTION_KINDS:
        return 'configured_instance'
    if raw_level == 'definition':
        return 'definition'
    return 'runtime_record'

def edge_level(source_level, target_level):
    if source_level == target_level == 'definition': return 'definition'
    if source_level == target_level == 'runtime_record': return 'runtime'
    if source_level == target_level == 'source_record': return 'source_record'
    if source_level == target_level == 'configured_instance': return 'configuration'
    return 'cross_level'

FROZEN_SIMPLE = {
 'DECLARES':'declares_state','CALLS':'selects_strategy','INPUT_TO':'input_to_strategy',
 'OUTPUT_MUST_SATISFY':'output_must_satisfy','PRODUCES':'declares_decision_output',
 'GENERATES':'selects_command_definition','COMPOSES':'composes_behavior_definition',
 'EXECUTED_BY':'execution_module_binding','CONSTRAINED_BY':'constrained_by',
 'FEEDS_BACK':'declares_event_feedback','WAITS_FOR':'declares_execution_dependency',
 'DEPENDS_ON':'depends_on_definition','USES_RULE':'uses_rule',
 'READS_DEFINITION':'reads_definition','DEFINES':'defines_target','HAS_EXPRESSION':'has_expression',
 'STATE_INPUT':'state_input_to','PARAMETER_INPUT':'parameter_input_to',
 'TARGET_REFERENCE':'target_result_input_to','BINDS_PARAMETER':'parameter_binding_input_to',
 'CONTROL_INPUT':'control_input_to','SCOPES':'applicability_expression_of',
 'ENTITY_REFERENCE':'value_references_entity','READ_SCOPE':'declares_read_scope',
 'WRITE_SCOPE':'declares_write_scope','CONTROLS':'controls','OBSERVES':'observes_fact',
 'ACCEPTS_FIELD':'accepts_state_signature','DECLARES_OUTPUT':'declares_command_output',
 'AVAILABLE_TO':'capability_available_to','PROVIDED_BY':'capability_provided_by',
 'ACTS_ON':'behavior_subject','SCOPED_TO':'scoped_to','REQUESTS_TASK':'requests_task',
 'CITED_FROM':'cited_from','ISSUED_BY':'issued_by','IMPLEMENTS_TASK':'implements_task',
 'EXECUTOR':'assigned_decision_agent','DECLARES_COMMAND':'declares_command_definition',
 'PURSUES':'pursues_objective','ALLOCATES':'declares_resource_allocation',
 'APPLIES_WHEN':'applies_when','DECLARES_EVENT':'declares_event','REFERENCES':'references_predicate',
 'TARGETS':'command_target_binding','SCENARIO_INPUT':'scenario_input',
 'PROPOSES_DECISION':'scenario_proposes_decision','SELECTS_TASK':'selects_task',
 'REPLAYS_EVENT':'replays_event_definition','REPLAYS_COMMAND':'replays_command_definition',
 'SCHEDULES_CHECK':'schedules_check_binding','SCHEDULES_EVENT':'schedules_event_definition',
 'SCHEDULES_UPDATE':'schedules_fact','CHECKS':'checks_behavior_definition',
 'HANDLES':'handles_event_definition','RESPONDS_AS':'feedback_agent_binding',
 'AFFECTS':'feedback_behavior_binding','PROPOSES_REPLAN':'proposes_replan',
 'RESULT_OF':'result_of_predicate','SUBJECT_TYPE':'declares_subject_type',
 'OBJECT_TYPE':'declares_object_type','USES_FIELD':'relation_uses_field',
 'COVERS_TYPE':'source_covers_entity_type','ARBITRATES':'arbitrates_agent',
 'ARBITRATED_BY':'arbitrated_by','CONDITIONED_ON':'conditioned_on_predicate',
 'CONDITIONS_BEHAVIOR':'conditions_behavior','MANAGED_BY':'managed_by',
 'REQUESTED_BY':'requested_by_agent','REQUESTS_ZONE':'requests_zone',
 'DECIDES_REQUEST':'decides_lease_request','APPROVED_BY':'approved_by_agent',
 'ALLOCATED_UNDER':'allocated_under_approval','ALLOCATES_ZONE':'allocates_zone',
 'REQUIRES_ALLOCATION':'requires_allocation','REQUIRES_RECEIPT':'requires_delivery_receipt',
 'RECEIPT_FOR':'delivery_receipt_for','SENT_BY':'sent_by','RECEIVED_BY':'received_by',
 'SELECTS_LEASE_REQUEST':'selects_lease_request','SELECTS_LEASE_APPROVAL':'selects_lease_approval',
 'SELECTS_ALLOCATION':'selects_allocation','SELECTS_RECEIPT':'selects_delivery_receipt',
}
RUNTIME_SIMPLE = {
 'DECLARES_COMMAND':'declares_command_attempt','IMPLEMENTS_TASK':'implements_task',
 'ISSUED_BY':'issued_by','RECEIPT_FOR':'delivery_receipt_for','RECEIVED_BY':'received_by',
 'REPLAYS_COMMAND':'replays_command_attempt','RESULT_OF':'expectation_of_predicate','SENT_BY':'sent_by',
 'based_on':'based_on_fact','calls_strategy':'starts_strategy_invocation',
 'candidate_instance_of':'candidate_occurrence_of','constrained_by':'constrained_by',
 'input_to':'input_to_strategy','invokes_strategy':'invocation_of_strategy',
 'proposes_decision':'proposes_decision','requested_by':'requested_by_request',
 'runtime_instance_of':'instance_of','receipt_for_attempt':'lifecycle_record_for_attempt',
 'produces_authored_fact':'declares_authored_fact_output',
}

def canonical_relation(raw_relation, source_kind=None, target_kind=None, *,
                       source_collection=None, target_collection=None,
                       frozen_relation=None, artifact=None, role=None, payload=None):
    """Resolve a semantic relation without reversing endpoints or dropping roles.

    ``frozen_relation`` is the explicit capitalized frozen registry identifier.
    Pass it only for a frozen-compatible source edge. Otherwise it is a proposed
    runtime edge. Artifact is needed for the verified ARGUMENT direction split.
    Returns a string. Unknown mapping remains explicitly unresolved.
    """
    sk = canonical_kind(source_kind, source_collection)
    tk = canonical_kind(target_kind, target_collection)
    if raw_relation == 'source_field_reference': return 'source_field_reference'
    if frozen_relation:
        r = frozen_relation
        if r == 'INSTANCE_OF':
            if tk == 'node_type': return 'classified_as'
            if sk == 'fact' and tk == 'state_specification': return 'instantiates_state'
            if sk == 'entity' and tk == 'entity_type': return 'instance_of'
            if sk == 'expression' and tk == 'expression_type': return 'expression_kind_of'
            if sk == 'check_binding' and tk == 'state_specification' and role == 'state_field':
                return 'legacy_field_reference'
            return 'unresolved:instance_of_signature'
        if r == 'OWNS': return {'fact':'subject_of_fact','state_specification':'owns_state','resource':'owns_resource'}.get(tk,'unresolved:owns_signature')
        if r == 'READS': return 'reads_state_signature' if tk == 'state_specification' else 'declares_fact_input'
        if r == 'USES': return 'uses_parameter_definition' if tk == 'parameter_definition' else 'uses_constraint'
        if r == 'UPDATES': return 'updates_state' if tk == 'state_specification' else 'declares_fact_output'
        if r == 'EVIDENCED_BY': return 'declares_event_evidence' if sk == 'event_definition' else 'evidenced_by'
        if r == 'BASED_ON': return 'constraint_basis' if sk == 'constraint_definition' else 'based_on_fact'
        if r == 'REQUIRES': return 'requires_capability' if tk == 'capability_declaration' else 'requires_resource'
        if r == 'ARGUMENT':
            a = str(artifact or '').replace('\\','/')
            if '/delivery/' in '/'+a or a.startswith('delivery/'):
                return 'has_argument'
            if any('/'+x+'/' in '/'+a for x in ('agriculture','city','network')):
                return 'argument_of'
            return 'unresolved:argument_direction'
        return FROZEN_SIMPLE.get(r, 'unresolved:'+r)
    r = raw_relation
    if r in ('instance_of','runtime_instance_of'):
        if sk == 'module_execution' and tk == 'module':
            return 'execution_of_module'
        if sk == 'material_transformation' and tk == 'relation_definition':
            return 'conforms_to_transformation_relation'
        return 'instance_of'
    if r == 'composes_behavior':
        return 'requests_behavior_definition' if tk == 'behavior_definition' else 'composes_behavior_execution'
    if r == 'executed_by':
        return 'execution_module_binding' if tk == 'module' else 'realized_by_module_execution'
    if r == 'permits_or_blocks':
        return 'declares_admission_for_behavior' if tk == 'behavior_definition' else 'permits_or_blocks'
    if r == 'permits_or_blocks_execution': return 'permits_or_blocks'
    if r == 'receipt_for':
        return 'delivery_receipt_for' if sk == 'delivery_receipt' else 'command_receipt_for'
    if r == 'produces_result' and sk == 'evaluation_binding': return 'binding_result_reference'
    return RUNTIME_SIMPLE.get(r, r)


def relation_variant_id(namespace, raw_relation, source_key, target_key, canonical):
    identity=[namespace,raw_relation,source_key,target_key,canonical]
    return 'p08:rv:'+hashlib.sha256(json.dumps(identity,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:24]

def exact_json_equal(a,b):
    """JSON structural equality without Python bool/int or int/float coercion."""
    if type(a) is not type(b): return False
    if isinstance(a,dict): return a.keys()==b.keys() and all(exact_json_equal(a[k],b[k]) for k in a)
    if isinstance(a,list): return len(a)==len(b) and all(exact_json_equal(x,y) for x,y in zip(a,b))
    return a==b

def exact_ref_key(ref):
    fields=('run_id','epoch','id','generation','ref_type')
    if not isinstance(ref,dict) or any(k not in ref for k in fields): return None
    if any(not isinstance(ref[k],str) or not ref[k].strip() for k in ('run_id','id','ref_type')): return None
    for k in ('epoch','generation'):
        value=ref[k]
        if type(value) not in (int,str) or (isinstance(value,str) and not value.strip()): return None
        if type(value) is int and value < 0: return None
    return tuple((k,type(ref[k]).__name__,json.dumps(ref[k],ensure_ascii=False,separators=(',',':'))) for k in fields)

def definition_merge_compatible(a,b):
    """Conservative identity proof; absent revision never creates cross-source equivalence.

    Caller supplies explicit semantic_identity with namespace, id, revision,
    scope and exact definition body. This does not deduplicate on its own.
    """
    if a.get('semantic_level')!='definition' or b.get('semantic_level')!='definition': return False
    if a.get('kind')!=b.get('kind'): return False
    ia=a.get('semantic_identity');ib=b.get('semantic_identity')
    if not isinstance(ia,dict) or not isinstance(ib,dict): return False
    if any(k not in ia or k not in ib for k in ('namespace','id','revision','scope','definition')): return False
    for identity in (ia,ib):
        if any(not isinstance(identity[k],str) or not identity[k].strip() for k in ('namespace','id')): return False
        if type(identity['revision']) not in (int,str) or identity['revision'] == '': return False
        if type(identity['scope']) not in (dict,list,str) or not identity['scope']: return False
        if identity['definition'] is None or identity['definition'] == '' or identity['definition'] == {} or identity['definition'] == []: return False
    return exact_json_equal(ia,ib)
