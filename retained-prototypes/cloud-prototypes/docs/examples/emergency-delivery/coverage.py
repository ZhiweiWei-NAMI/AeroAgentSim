"""Evidence-based coverage of one pinned authoring inventory, not native completeness."""
import hashlib,json
from collections import Counter
from pathlib import Path
INVENTORY=Path('/workspace/shared/aero-unified-graph-inventory-20261005.json')
ALIASES={
'INSTANCE_OF':['instance_of','instantiates_state'],'DECLARES':['declares_field'],'OWNS':['owns_state','provides_resource'],'CALLS':['calls_strategy'],'INPUT_TO':['input_to'],'OUTPUT_MUST_SATISFY':['output_must_satisfy'],'PRODUCES':['produces_decision'],'GENERATES':['generates_command'],'COMPOSES':['composes_behavior'],'EXECUTED_BY':['executed_by'],'CONSTRAINED_BY':['constrained_by'],'READS':['reads_fact'],'USES':['uses_constraint','uses_parameter'],'UPDATES':['updates_state','produces_fact'],'EVIDENCED_BY':['evidenced_by'],'FEEDS_BACK':['feeds_back'],'BASED_ON':['based_on','based_on_policy'],'WAITS_FOR':['waits_for'],'DEPENDS_ON':['depends_on'],'USES_RULE':['uses_rule'],'READS_DEFINITION':['reads_definition'],'DEFINES':['defines_target'],'HAS_EXPRESSION':['has_operator'],'STATE_INPUT':['references_state'],'PARAMETER_INPUT':['uses_parameter'],'ARGUMENT':['has_argument'],'TARGET_REFERENCE':['references_target'],'BINDS_PARAMETER':[],'CONTROL_INPUT':[],'SCOPES':[],'REQUIRES':['requires_capability','requires_resource'],'ENTITY_REFERENCE':['entity_reference'],'READ_SCOPE':[],'WRITE_SCOPE':[],'CONTROLS':['controls'],'OBSERVES':['observes'],'ACCEPTS_FIELD':[],'DECLARES_OUTPUT':[],'AVAILABLE_TO':[],'PROVIDED_BY':[],'ACTS_ON':['acts_on'],'SCOPED_TO':['scoped_to'],'REQUESTS_TASK':['requests_task'],'CITED_FROM':['cited_from'],'ISSUED_BY':['issued_by'],'IMPLEMENTS_TASK':['implements_task'],'EXECUTOR':['executor'],'DECLARES_COMMAND':[],'PURSUES':['pursues'],'ALLOCATES':[],'APPLIES_WHEN':[],'DECLARES_EVENT':[],'REFERENCES':[],'TARGETS':['targets'],'SCENARIO_INPUT':[],'PROPOSES_DECISION':[],'SELECTS_TASK':[],'REPLAYS_EVENT':[],'REPLAYS_COMMAND':[],'SCHEDULES_CHECK':[],'SCHEDULES_EVENT':[],'SCHEDULES_UPDATE':[],'CHECKS':['checks_behavior'],'HANDLES':['handles'],'RESPONDS_AS':['responds_as'],'AFFECTS':['affects'],'PROPOSES_REPLAN':['proposes_replan'],'RESULT_OF':['result_of'],'SUBJECT_TYPE':['subject_type'],'OBJECT_TYPE':['object_type'],'USES_FIELD':['uses_field'],'COVERS_TYPE':[],'ARBITRATES':[],'ARBITRATED_BY':[],'CONDITIONED_ON':[],'CONDITIONS_BEHAVIOR':['composes_behavior'],'MANAGED_BY':['managed_by'],'REQUESTED_BY':['requested_by'],'REQUESTS_ZONE':['requests_zone'],'DECIDES_REQUEST':['responds_to'],'APPROVED_BY':['approved_by'],'ALLOCATED_UNDER':['allocated_under'],'ALLOCATES_ZONE':['allocates'],'REQUIRES_ALLOCATION':['requires_allocation'],'REQUIRES_RECEIPT':['requires_receipt'],'RECEIPT_FOR':['receipt_for_message'],'SENT_BY':['sent_by'],'RECEIVED_BY':['received_by'],'SELECTS_LEASE_REQUEST':[],'SELECTS_LEASE_APPROVAL':[],'SELECTS_ALLOCATION':[],'SELECTS_RECEIPT':[]}
# Reversal is explicit where integration-document direction differs from editor projection.
REVERSED={'STATE_INPUT','PARAMETER_INPUT','TARGET_REFERENCE'}
NOT_APPLICABLE={'ARBITRATES':'No overlapping control scopes: UAV agent controls motion; edge agent allocates corridor time. No arbitration policy is invented.','ARBITRATED_BY':'Same disjoint authority scopes; arbitration is not invoked.','CONTROL_INPUT':'The selected gt/and rules have no temporal operator controls. Corridor windows are allocation data, not temporal AST controls.','SCOPES':'No target rebinding or operator-local scope is used in these authored rules.','BINDS_PARAMETER':'No bound-call parameter override is used; one explicitly declared lexical threshold is shown.'}

def add_relations(NS,node,edge,N,E,kinds):
 kinds['request']=('任务请求','Task request','decision','requests')
 kinds['feedback_policy']=('反馈处理策略','Feedback policy','decision','feedback_policies')
 node('task_request','request','记录异常的任务请求','Incident-recording task request','decision','向任务代理提出在安全获批后记录异常；航道时隙申请是另一个专用请求。','Ask the mission agent to record the incident after safe authorization. The corridor lease application is a separate specialized request.',entity_ids=[NS+'uav',NS+'parcel'],task_ids=[NS+'task'],source_id=NS+'policy')
 node('feedback_policy','feedback_policy','异常反馈：暂停并重规划','Incident feedback: pause + replan','decision','收到新异常报告后暂停配送、申请时隙并重规划；不会仅凭事件直接执行相机或飞行。','On a new incident report, pause delivery, request a slot and replan. The event never directly executes the camera or flight.',event_ids=[NS+'event_def'],agent_id=NS+'agent',behavior_ids=[NS+'behavior.approach',NS+'behavior.capture',NS+'behavior.upload'],response='replan',decision_ids=[NS+'decision'])
 edge('task_request','strategy','input_to','request');edge('task_request','task','requests_task','incident_recording_with_delivery_retained');edge('task_request','policy','cited_from','authored_exercise_request');edge('task_request','uav','scoped_to','request_actor');edge('feedback_policy','event_def','handles','incident_report');edge('feedback_policy','agent','responds_as','decision_owner');edge('feedback_policy','decision','proposes_replan','explicit_replanning');
 for b in ['approach','capture','upload']:edge('feedback_policy','behavior.'+b,'affects','replan_behavior')
 kinds['relation_definition']=('关系定义','Relation definition','rules','relation_definitions')
 kinds['predicate_result']=('谓词结果','Predicate result','feedback','predicate_results')
 for id,zh,en,field in [('custody','责任保管关系','Responsible custody relation','custody'),('attachment','物理挂载关系','Physical attachment relation','attachment')]:
  node('relation.'+id,'relation_definition',zh,en,'rules','显式源角色为无人机，目标角色为包裹；保管与物理挂载不是同一关系。','Source role is UAV, target role is parcel. Custody and physical attachment remain distinct.',subject_type=NS+'type.uav',object_type=NS+'type.parcel',field_ref=NS+'state.'+field)
  edge('relation.'+id,'type.uav','subject_type','custodian_or_carrier');edge('relation.'+id,'type.parcel','object_type','parcel');edge('relation.'+id,'state.'+field,'uses_field','relation_state')
 edge('task','goal','pursues','delivery_objective');edge('task','agent','executor','task_agent');edge('command','uav','targets','command_subject',level='runtime');edge('decision','agent','issued_by','decision_actor',level='runtime');edge('decision','task','implements_task','retained_delivery_goal',level='runtime')
 edge('agent','fact.incident','observes','authorized_observation',level='runtime');edge('fact.custody','uav','entity_reference','recorded_custodian',level='runtime');edge('fact.attachment','uav','entity_reference','recorded_attachment',level='runtime')
 for s in ['approach','capture','upload','return','resume','hold']:edge('behavior.'+s,'uav','acts_on','controlled_entity')
 for zone in ['corridor','no_fly']:edge(zone,'edge_agent','managed_by','scenario_airspace_authority');edge(zone,'policy','cited_from','authored_policy')
 for prefix in ['', 'return_']:
  request='request' if not prefix else 'return_request';approval='approval' if not prefix else 'return_approval';allocation='allocation' if not prefix else 'return_allocation'
  edge(request,'agent','requested_by','requesting_agent',level='runtime');edge(request,'corridor','requests_zone','requested_corridor',level='runtime');edge(approval,'edge_agent','approved_by','approval_authority',level='runtime');edge(allocation,approval,'allocated_under','approval_record',level='runtime');edge(allocation,'uav','scoped_to','allocated_vehicle',level='runtime')
 for receipt,sender,receiver in [('report_receipt','uav','edge_server'),('grant_receipt','edge_server','uav'),('return_grant_receipt','edge_server','uav')]:edge(receipt,sender,'sent_by','message_sender',level='runtime');edge(receipt,receiver,'received_by','message_recipient',level='runtime');edge(receipt,'evidence','evidenced_by','transport_fixture',level='runtime')
 edge('behavior.approach','grant_receipt','requires_receipt','received_corridor_grant');edge('behavior.return','return_grant_receipt','requires_receipt','received_return_grant');edge('behavior.return','return_allocation','requires_allocation','return_time_window')
 for s in ['approach','capture','upload','hold']:edge('check','behavior.'+s,'checks_behavior','declared_start_continue_guard')

def inventory(D):
 raw=INVENTORY.read_bytes(); inv=json.loads(raw); nodes={n['id']:n for n in D['nodes']}; kr=D['kind_registry']
 def collection(n):return kr[n['kind']]['workbench_collection'] or ('expressions' if n['kind'] in ['operator','state_ref','constant','target_ref'] or '.ast.' in n['id'] else None)
 rows=[]
 for c in inv['collections']:
  key=c['key']; evidence=[n['id'] for n in D['nodes'] if collection(n)==key]
  if evidence: status='supported';note='Concrete scenario nodes; read-only projection. Runtime records retain their own kinds and are not automatically importable definitions.'
  elif key=='node_types':status='supported';note='Represented by the embedded kind_registry used by every hover label; not padded with display-only type nodes.';evidence=['#/kind_registry']
  elif key=='expression_types':status='supported';note='Expression kinds are explicitly declared in kind_registry; AST nodes derive from definition_ast.';evidence=['#/kind_registry/operator','#/kind_registry/state_ref','#/kind_registry/constant','#/kind_registry/target_ref']
  elif key=='scenarios':status='supported';note='One scenario manifest with airspace and post-upload branch traces is represented as metadata, not a dummy node.';evidence=['#/branches','#/airspace_branches']
  elif key=='arbitrations':status='not_applicable';note='Authority scopes are disjoint; no arbitration is required or invented.'
  else:status='unrepresentable';note='No concrete standalone '+key+' record in this projection. Related concepts may be present, but schema-field completeness is not claimed.'
  rows.append({'name':key,'status':status,'evidence':evidence,'note':note,'unfilled_required_fields':[] if evidence else (c.get('schema',{}).get('required',[]) if isinstance(c.get('schema'),dict) else [])})
 relations=[]
 for r in inv['relations']:
  name=r['relation']; aliases=ALIASES.get(name,[]);exact=[];partial=[]
  for e in D['edges']:
   if e['type'] not in aliases:continue
   src,tgt=collection(nodes[e['source']]),collection(nodes[e['target']])
   if name in REVERSED:src,tgt=tgt,src
   froms=r['from'];tos=r['to']
   ok=(froms=='*' or src in froms) and tgt in tos
   # Runtime-only executions cannot be quietly cast to their reusable definitions.
   if nodes[e['source']]['kind'] in ['behavior_execution','strategy_invocation'] or nodes[e['target']]['kind']=='behavior_execution':ok=False
   (exact if ok else partial).append(e['id'])
  if exact:status='supported';note='Mapped by declared alias'+(' with explicitly reversed display direction' if name in REVERSED else '')+'; source/target collection signature checked. This is conceptual coverage, not native runtime parity.'
  elif name in NOT_APPLICABLE:status='not_applicable';note=NOT_APPLICABLE[name]
  else:status='unrepresentable';note='No signature-compatible concrete edge in this scenario. '+('Related runtime/display edges exist, but must not be cast to the canonical definition-level contract.' if partial else 'Not filled by a placeholder or unrelated branch.')
  relations.append({'name':name,'status':status,'evidence':exact,'related_noncanonical_edges':partial,'aliases':aliases,'direction_mapping':'reversed' if name in REVERSED else 'same','signature':{'from':r['from'],'to':r['to']},'note':note})
 ast=[]
 for op in inv.get('definition_operators',[]):
  if isinstance(op,dict):name=op.get('id') or op.get('operator') or op.get('name') or str(op)
  else:name=op
  evidence=[n['id'] for n in D['nodes'] if n['kind']=='operator' and n.get('operator')==name]
  ast.append({'name':name,'status':'supported' if evidence else 'not_applicable','evidence':evidence,'note':'Authored AST retained; native evaluation unverified.' if evidence else 'Not used by the selected scenario rule. No arbitrary expression added to inflate coverage.'})
 cov={'schema_version':'aeroagentsim.scenario-coverage/v1','inventory_sha256':hashlib.sha256(raw).hexdigest(),'inventory_scope':inv['inventory_scope'],'scope':'Pinned implemented authoring inventory only. Every entry is audited; supported, unrepresentable and not_applicable are separate. No 100% semantic coverage or full native-catalog claim.','node_types':rows,'relations':relations,'ast_forms':ast,'regulatory_source':{'status':'not_applicable','note':'No legal requirement asserted; scenario policy is authored and non-regulatory.'},'field_mapping_limit':'This is a read-only graph projection, not a unified-graph/v1 file. Its quantities, time scopes and provenance are preserved in node properties. Kind-level mapping is not required-field completeness or import validation.','native_catalog':'unavailable; exhaustive type/operator/edge coverage cannot be verified'}
 # Field-level projection audit: missing authoring keys remain explicit, not disguised as schema compatibility.
 schema=json.loads(Path('/workspace/shared/AeroAgentSim-workbench/frontend/console-prototype/src/schemas/unified-graph-v1.schema.json').read_text())
 fields=[]
 for n in D['nodes']:
  col=collection(n)
  if col not in schema.get('properties',{}):continue
  ref=schema['properties'][col].get('items',{}).get('$ref','').split('/')[-1]
  definition=schema.get('$defs',{}).get(ref,{})
  required=definition.get('required',[])
  missing=[k for k in required if k not in n]
  incompatible=[]
  for k in required:
   if k in n and definition.get('properties',{}).get(k,{}).get('type')=='string' and not isinstance(n[k],str):incompatible.append(k)
  if missing or incompatible:fields.append({'node_id':n['id'],'collection':col,'missing_authoring_fields':missing,'representation_mismatch_fields':incompatible,'note':'Read-only display projection. Bilingual label/description objects are not canonical string fields; no automatic import is claimed.'})
 cov['field_level_gaps']=fields
 cov['counts']={k:dict(Counter(x['status'] for x in cov[k])) for k in ['node_types','relations','ast_forms']}
 return cov
