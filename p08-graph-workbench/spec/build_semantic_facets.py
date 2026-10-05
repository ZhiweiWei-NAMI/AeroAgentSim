"""Strict optional authoring facets. Unknown source fields are explicit slots."""
from pathlib import Path
import json
P=Path(__file__).resolve().parent
NE={'type':'string','minLength':1,'pattern':r'\S'}
REF={'type':'object','required':['node_id'],'properties':{'node_id':NE,'revision':{'type':['string','integer']},'source_pointer':{'type':'string'}},'additionalProperties':False}
EXACT={'type':'object','required':['run_id','epoch','id','generation','ref_type'],'properties':{'run_id':NE,'epoch':{'oneOf':[NE,{'type':'integer','minimum':0}]},'id':NE,'generation':{'oneOf':[NE,{'type':'integer','minimum':0}]},'ref_type':NE},'additionalProperties':False}
JSON={'anyOf':[{'type':['null','boolean','number','string']},{'type':'array','items':{'$ref':'#/$defs/json_value'}},{'type':'object','additionalProperties':{'$ref':'#/$defs/json_value'}}]}
def slot(shape):return {'oneOf':[{'type':'object','required':['status','value'],'properties':{'status':{'const':'known'},'value':shape,'source_pointers':{'type':'array','items':NE}},'additionalProperties':False},{'type':'object','required':['status','reason'],'properties':{'status':{'enum':['missing','unverified','not_applicable','unsupported']},'reason':NE,'source_pointers':{'type':'array','items':NE}},'additionalProperties':False}]}
def obj(fields):return {'type':'object','required':list(fields),'properties':{k:slot(v) for k,v in fields.items()},'additionalProperties':False}
REFS={'type':'array','items':{'$ref':'#/$defs/definition_ref'}}
QUANTITY={'type':'object','required':['dimension','unit','value'],'properties':{'dimension':NE,'unit':NE,'value':{'type':['number','string']},'uncertainty':{'type':['number','string','null']}},'additionalProperties':False}
TIME={'type':'object','required':['clock_domain','value','unit'],'properties':{'clock_domain':NE,'value':{'type':['string','number']},'unit':{'enum':['ns','us','ms','s','tick','source_native']}},'allOf':[{'if':{'properties':{'unit':{'const':'ns'}}},'then':{'properties':{'value':{'type':'string','pattern':'^-?[0-9]+$'}}}}],'additionalProperties':False}
INTERVAL={'type':'object','required':['start','end','boundary'],'properties':{'start':{'$ref':'#/$defs/time_point'},'end':{'$ref':'#/$defs/time_point'},'boundary':{'enum':['closed','open','left_closed','right_closed','source_native']}},'additionalProperties':False}
ROLES={'type':'array','items':{'type':'object','required':['role','index','ref'],'properties':{'role':NE,'index':{'type':'integer','minimum':0},'ref':{'$ref':'#/$defs/exact_ref'}},'additionalProperties':False}}
SCOPE={'type':'object','minProperties':1,'additionalProperties':{'$ref':'#/$defs/json_value'}}
TRUTH={'enum':['True','False','Unknown']}
POLICY={'type':'object','required':['on_false','on_unknown','on_conflict'],'properties':{'on_false':NE,'on_unknown':NE,'on_conflict':NE},'additionalProperties':False}
facets={
'entity':obj({'lifecycle_ref':{'$ref':'#/$defs/exact_ref'},'entity_type':REF,'lifecycle_interval':INTERVAL,'capability_claims':REFS}),
'objective':obj({'desired_outcome':SCOPE,'deadline':TIME,'quality_conditions':SCOPE,'preference_or_weight':SCOPE,'task':REF,'completion_evidence':REFS}),
'agent':obj({'identity':{'$ref':'#/$defs/exact_ref'},'observation_scope':SCOPE,'strategies':REFS,'controlled_entity_roles':ROLES,'authority_refs':REFS,'arbitration_ref':REF}),
'strategy_definition':obj({'revision':{'type':['string','integer']},'inputs':{'type':'array','items':SCOPE},'outputs':SCOPE,'parameter_signature':SCOPE,'permitted_observations':SCOPE}),
'strategy_invocation':obj({'agent':REF,'strategy':REF,'input_facts':REFS,'requests':REFS,'objectives':REFS,'applicable_constraints':REFS,'query_time':TIME,'available_cutoff':TIME,'decision':REF,'execution_status':NE}),
'command_definition':obj({'target_signature':SCOPE,'argument_signature':SCOPE,'receipt_contract':SCOPE,'effect_contract':SCOPE,'composition':{'$ref':'#/$defs/composition'}}),
'command_attempt':obj({'definition':REF,'target':{'$ref':'#/$defs/exact_ref'},'issuer':REF,'attempt_id':NE,'task':REF,'arguments':SCOPE,'issued_time':TIME,'status':NE}),
'command_receipt':obj({'command_attempt':REF,'phase':NE,'status':NE,'received_time':TIME,'evidence':REFS,'proof_scope':NE}),
'behavior_definition':obj({'capabilities':REFS,'resources':{'type':'array','items':{'$ref':'#/$defs/resource_requirement'}},'module_routes':REFS,'constraints':REFS,'composition':{'$ref':'#/$defs/composition'}}),
'behavior_execution':obj({'definition':REF,'command_attempt':REF,'attempt_id':NE,'start_checks':REFS,'continuation_checks':REFS,'module_executions':REFS,'status':NE,'actual_interval':INTERVAL,'completion_evidence':REFS}),
'module':obj({'interface':SCOPE,'backend_mode':{'enum':['fixture','stub','simulated','real','unbound']},'input_signature':SCOPE,'output_fields':REFS,'authority_scope':SCOPE}),
'module_execution':obj({'module':REF,'behavior_execution':REF,'inputs':REFS,'outputs':REFS,'execution_status':NE,'source_evidence':REFS}),
'resource':obj({'identity':{'$ref':'#/$defs/exact_ref'},'capacity_dimensions':{'type':'array','items':QUANTITY},'owner':REF,'reservation_semantics':SCOPE,'occupancy_source':REF,'validity':INTERVAL}),
'state_specification':obj({'revision':{'type':['string','integer']},'value_type':{'enum':['boolean','integer','number','string','object','array','reference','source_native']},'quantity':NE,'unit':NE,'frame':NE,'subject_signature':SCOPE,'temporal_contract':SCOPE,'authority_scope':SCOPE}),
'fact':obj({'subject':{'$ref':'#/$defs/exact_ref'},'state_specification':REF,'value':{'$ref':'#/$defs/json_value'},'validity':INTERVAL,'availability':TIME,'availability_commit':{'type':['string','integer']},'authority':REF,'source':REF,'revision':{'type':['string','integer']},'quality':SCOPE,'quantity':NE,'unit':NE,'frame':NE}),
'rule_definition':obj({'revision':{'type':['string','integer']},'main_ast':{'$ref':'#/$defs/json_value'},'applicability_ast':{'$ref':'#/$defs/json_value'},'parameter_scope':SCOPE,'native_catalog_revision':NE,'native_evaluator_revision':NE,'temporal_contract':SCOPE}),
'predicate_definition':obj({'revision':{'type':['string','integer']},'rule':REF,'binding_signature':SCOPE}),
'predicate_evaluation':obj({'definition':REF,'binding':REF,'rule_revision':{'type':['string','integer']},'query_time':TIME,'available_cutoff':TIME,'prepared_inputs':REFS,'history':SCOPE,'engine_revision':NE,'main_truth':TRUTH,'applicability':{'enum':['applicable','not_applicable','applicability_unknown']},'execution_status':NE,'diagnostics':{'type':'array','items':SCOPE}}),
'event_definition':obj({'revision':{'type':['string','integer']},'rule':REF,'occurrence_policy':SCOPE,'participant_signature':SCOPE,'evidence_requirements':SCOPE}),
'event_occurrence':obj({'definition':REF,'occurrence_identity':NE,'participants':ROLES,'occurrence_time':TIME,'availability':TIME,'evidence':REFS,'emitted':{'type':'boolean'},'policy_revision':{'type':['string','integer']}}),
'constraint_definition':obj({'revision':{'type':['string','integer']},'assessment_axis':{'enum':['physical','permission_compliance','task_suitability']},'hard_or_soft':{'const':'hard_constraint'},'requirement':SCOPE,'applicability':SCOPE,'source':REF,'enforcement_module':REF,'effective_interval':INTERVAL,'jurisdiction':SCOPE}),
'constraint_check':obj({'constraints':REFS,'behavior_execution':REF,'check_scope':{'enum':['pre_start','in_execution','effect_confirmation','review']},'query_time':TIME,'available_cutoff':TIME,'required_inputs':SCOPE,'actual_inputs':REFS,'enforcement_module':REF,'policy':POLICY}),
'constraint_check_result':obj({'check':REF,'execution_attempt':REF,'assessment_axis':{'enum':['physical','permission_compliance','task_suitability']},'truth':TRUTH,'applicability':{'enum':['applicable','not_applicable','applicability_unknown']},'decision':{'enum':['permitted','blocked','unresolved','not_applicable']},'evidence':REFS,'policy':POLICY,'actual_enforcement':NE}),
'evaluation_binding':obj({'target':REF,'rule':REF,'roles':ROLES,'field_map':SCOPE,'parameters':SCOPE,'query_time':TIME,'available_cutoff':TIME,'history':SCOPE}),
'relation_instance':obj({'definition':REF,'roles':ROLES,'scope':SCOPE,'validity':INTERVAL,'authority':REF,'claim_status':NE}),
}
COMPOSITION={'type':'array','items':{'type':'object','required':['group','mode','member','index'],'properties':{'group':NE,'mode':{'enum':['ordered','parallel','conditional']},'member':REF,'index':{'type':'integer','minimum':0},'condition':slot(REF),'unknown_policy':slot(NE)},'additionalProperties':False}}
RESOURCE_REQ={'type':'object','required':['resource','amount','interval','purpose'],'properties':{'resource':REF,'amount':QUANTITY,'interval':INTERVAL,'purpose':NE},'additionalProperties':False}
d={'$schema':'https://json-schema.org/draft/2020-12/schema','$id':'urn:aeroagentsim:p08:semantic-facets:v1','title':'Optional strict authoring facets; unresolved fields explicit','description':'Use for normalized authoring projections. Never populate a known slot by guessing. Original source payload remains lossless authority. Passing this shape does not prove native or physical execution.','type':'object','required':['kind','facets'],'properties':{'kind':{'enum':list(facets)},'facets':{'type':'object'}},'additionalProperties':False,'allOf':[{'if':{'properties':{'kind':{'const':k}}},'then':{'properties':{'facets':v}}} for k,v in facets.items()],'$defs':{'json_value':JSON,'exact_ref':EXACT,'definition_ref':REF,'time_point':TIME,'interval':INTERVAL,'quantity':QUANTITY,'composition':COMPOSITION,'resource_requirement':RESOURCE_REQ}}
(P/'semantic-facets.schema.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':
 from jsonschema import Draft202012Validator
 Draft202012Validator.check_schema(d)
 for kind,body in facets.items():
  example={'kind':kind,'facets':{k:{'status':'missing','reason':'Not supplied by the imported source; no value inferred.'} for k in body['required']}}
  Draft202012Validator(d).validate(example)
 print(f'{len(facets)} strict normalized facet families validated with explicit missing slots')
