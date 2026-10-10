"""Derive the scenario page from validated canonical authoring output.

The renderer has no authoring schema or evaluator of its own. Configuration,
node IDs, AST expression IDs, and definition edges are consumed unchanged.
Only explicitly typed synthetic runtime records are overlaid.
"""
from pathlib import Path
import json, hashlib, sys
ROOT=Path(__file__).resolve().parent.parent
canonical_file=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/workspace/shared/AeroAgentSim-workbench/frontend/console-prototype/examples/emergency-delivery.graph-export.json')
canonical=json.loads(canonical_file.read_text())
base=json.loads((ROOT/'emergency-delivery-graph.fixture.json').read_text())
config=canonical.get('config') or canonical.get('configuration')
assert config and isinstance(config,dict),'Canonical config required'
graph=config.get('graph') or canonical.get('graph')
assert graph and graph.get('schema_version')=='aeroagentsim.unified-graph/v1','Canonical unified graph required'
source_nodes=canonical['nodes'];source_edges=canonical['edges']
assert len({n['id'] for n in source_nodes})==len(source_nodes)
NS=base['namespace']; old={n['id']:n for n in base['nodes']}
collections={
'node_types':('node_type','节点类型定义','Node-kind definition','world'),'entity_types':('entity_type','实体类型','Entity type','world'),'entities':('entity','实体','Entity','world'),
'fields':('state_spec','状态规格','State specification','facts'),'facts':('fact','事实记录','Fact record','facts'),'modules':('module','模块','Module','execution'),
'agents':('agent','独立代理','Agent','decision'),'strategies':('strategy','策略','Strategy','decision'),'capabilities':('capability','能力','Capability','execution'),
'behaviors':('behavior','行为定义','Behavior definition','execution'),'requests':('request','任务请求','Task request','decision'),'objectives':('objective','目标','Objective','decision'),
'decisions':('decision','决策','Decision','decision'),'tasks':('task','任务','Task','decision'),'resources':('resource','资源','Resource','execution'),
'constraints':('constraint','约束','Constraint','rules'),'predicates':('predicate','谓词定义','Predicate definition','rules'),'rules':('rule','规则表达式','Rule expression','rules'),
'events':('event_def','事件定义','Event definition','rules'),'commands':('command_definition','指令定义','Command definition','execution'),'arbitrations':('arbitration','控制仲裁','Control arbitration','decision'),
'sources':('policy_source','规则/证据来源','Policy/evidence source','rules'),'scenarios':('scenario','场景定义','Scenario definition','decision'),
'admission_checks':('constraint_check','执行约束检查','Constraint check','execution'),'feedback_policies':('feedback_policy','反馈处理策略','Feedback policy','decision'),
'predicate_results':('predicate_result','谓词结果','Predicate result','feedback'),'relation_definitions':('relation_definition','关系定义','Relation definition','rules'),
'parameter_definitions':('parameter','参数定义','Parameter definition','rules'),'spatial_zones':('spatial_zone','空域/航道','Airspace/corridor','world'),
'lease_requests':('lease_request','航道申请','Corridor request','decision'),'lease_approvals':('lease_approval','航道审批','Corridor approval','decision'),
'space_time_allocations':('space_time_allocation','空间-时间分配','Space-time allocation','rules'),'delivery_receipts':('delivery_receipt','通信送达回执','Message delivery receipt','feedback'),
'expression_types':('expression_type','表达式类型','Expression kind','rules'),'expressions':('expression','AST 表达式','AST expression','rules')}
kr=dict(base['kind_registry']);nodes=[]
for item in source_nodes:
 col=item['collection']; rec=item['node']; kind,zh,en,family=collections[col]
 kr[kind]={'zh':zh,'en':en,'family':family,'workbench_collection':col}
 prev=old.get(item['id'],{})
 label={'zh':rec.get('label_zh') or prev.get('label',{}).get('zh') or item['label'],'en':rec.get('label') or item['label']}
 desc=rec.get('description') or rec.get('meaning')
 if not isinstance(desc,str):desc='Canonical '+col+' record, derived directly from the validated configuration.'
 n={'id':item['id'],'kind':kind,'label':label,'group':family,'description':{'zh':prev.get('description',{}).get('zh',desc),'en':desc},'revision':graph['schema_version'],'provenance':'canonical_authoring_configuration','source_collection':col,'source_record':rec,'source_path':col,'source_configuration_sha256':canonical.get('config_sha256'),'projection_only':True}
 # Runtime display readouts reference canonical values, never supply missing authored fields.
 for key in ['value','unit','truth','valid_time_ns','available_time_ns','definition_ast','applicability_ast','operator']:
  if key in rec:n[key]=rec[key]
 if col=='expressions':n['description']={'zh':'从规范配置中的源 AST 自动提取；节点标识、参数顺序和依赖边原样保留。','en':'Derived from the canonical source AST. Exact IDs, operand order and dependency edges are retained.'}
 nodes.append(n)
ids={n['id'] for n in nodes}
runtime_labels={
'authored_decision_resolution':('决策解析记录','Decision resolution','decision'),
'fixture_command_dispatch':('指令分派记录','Command dispatch','execution'),
'fixture_behavior_plan':('行为计划记录','Behavior plan','execution'),
'fixture_admission_result':('执行检查结果','Admission result','feedback'),
'fixture_binding_resolution':('模块/策略绑定记录','Binding resolution','execution'),
'authored_strategy_invocation':('策略调用记录','Strategy invocation','decision'),
'fixture_event_occurrence':('事件记录','Event occurrence','feedback'),
'unexecuted_predicate_result':('未执行的谓词结果','Unexecuted predicate result','feedback')}
runtime=canonical['runtime_overlay']
for r in runtime['nodes']:
 zh,en,family=runtime_labels[r['kind']];kr[r['kind']]={'zh':zh,'en':en,'family':family,'workbench_collection':None}
 target=next((n for n in nodes if n['id']==r['definition_id']),None)
 title=(target['label'] if target else {'zh':r['definition_id'].removeprefix(NS),'en':r['definition_id'].removeprefix(NS)})
 rec=r['record'];suffix=rec.get('verdict') or rec.get('status') or rec.get('truth') or 'fixture'
 nodes.append({'id':r['id'],'kind':r['kind'],'label':{'zh':title['zh']+' · '+str(suffix),'en':title['en']+' · '+str(suffix)},'group':family,'description':{'zh':'从规范配置的运行夹具输出派生；这是计划、绑定或检查记录，不是物理执行证据。','en':'Derived from the canonical fixture run: a plan, binding or check record, not physical execution evidence.'},'revision':'fixture-result/v1','provenance':'canonical_fixture_run','source_record':r,'source_path':r['source_path'],'definition_id':r['definition_id'],'native_execution_proven':False})
edges=[]
for e in source_edges:
 edges.append({'id':e['id'],'source':e['source'],'target':e['target'],'type':e['relation'],'role':e['role'],'direction':'source_to_target','revision':e['version'],'level':'definition','provenance':'canonical_graphEdges','source_record':e,'condition':e.get('condition'),'source_path':e.get('path'),'order':e.get('order')})
for e in runtime['edges']:
 edges.append({'id':e['id'],'source':e['source'],'target':e['target'],'type':e['relation'],'role':e['role'],'direction':'source_to_target','revision':'fixture-result/v1','level':'runtime','provenance':'canonical_fixture_run','source_record':e,'source_path':e['source_path']})
kr['expected_effect']={'zh':'已声明的预期效果','en':'Authored expected effect','family':'feedback','workbench_collection':None}
for effect in canonical['expected_effects']:
 id=NS+'expected_effect.'+effect['branch']
 nodes.append({'id':id,'kind':'expected_effect','label':{'zh':{'success':'预期：恢复配送','failure':'预期：另获批后返航','unknown':'预期：暂停并限时等待'}[effect['branch']],'en':{'success':'Expected: resume delivery','failure':'Expected: separately approved return','unknown':'Expected: bounded wait'}[effect['branch']]},'group':'feedback','description':{'zh':'规范配置中的预期结果，物理效果尚未执行验证；不是已经到达或完成的事实。','en':'Authored expected outcome in the canonical config. Physical effects are not executed or verified.'},'source_record':effect,'source_path':effect['source_path'],'provenance':'canonical_authored_expectation','revision':'fixture-v1','actual_effect_established':False})
 edges.append({'id':id+'.source','source':id,'target':NS+'business','type':'EXPECTED_EFFECT_DECLARED_IN','role':effect['source_path'],'direction':'source_to_target','level':'definition','revision':'fixture-v1','provenance':'canonical_authored_expectation'})
kr['prerequisite_probe']={'zh':'时隙/回执检查记录','en':'Lease/receipt check','family':'feedback','workbench_collection':None}
all_probes=[*canonical['prerequisite_probes'],*[{'name':r['name'],**r['result'],'counterfactual':r} for r in canonical.get('counterfactual_probes',[])]]
for probe in all_probes:
 id=NS+'probe.'+probe['name']
 nodes.append({'id':id,'kind':'prerequisite_probe','label':{'zh':'时隙检查 · '+probe['verdict'],'en':'Lease check · '+probe['verdict']},'group':'feedback','description':{'zh':'运行夹具中的时隙/回执检查。总体真实可行性、权限和任务适合性仍为 UNKNOWN。','en':'Fixture lease/receipt check. Overall real feasibility, permission and suitability remain UNKNOWN.'},'source_record':probe,'provenance':'canonical_prerequisite_probe','revision':'fixture-v1'})
 edges.append({'id':id+'.behavior','source':id,'target':probe['behavior_id'],'type':'PREREQUISITE_RESULT_FOR','role':'fixture_only_not_actual_permission','direction':'source_to_target','level':'runtime','revision':'fixture-v1','provenance':'canonical_prerequisite_probe'})
ids={n['id'] for n in nodes}
assert all(e['source'] in ids and e['target'] in ids for e in edges)
aliases={n['id'].removeprefix(NS):n['id'] for n in nodes if n['id'].startswith(NS)}
aliases.update({'command':NS+'command.definition','exec.approach':NS+'behavior.approach','exec.capture':NS+'behavior.capture','exec.upload':NS+'behavior.upload','exec.hold':NS+'behavior.hold','exec.resume':NS+'behavior.resume','exec.return':NS+'behavior.return','check':NS+'check.permission','check_result':NS+'probe.in_window','module_execution':NS+'ns3','receipt':NS+'upload_success_receipt','outcome':NS+'expected_effect.success'})
for alias,id in aliases.items():
 if id in ids:next(n for n in nodes if n['id']==id)['ui_alias']=alias
branch_aliases={b:{'outcome':NS+'expected_effect.'+b,'fact.upload':NS+{'success':'fact.upload','failure':'fact.upload_failure','unknown':'fact.upload_unknown'}[b],'receipt':NS+'upload_'+b+'_receipt'} for b in ['success','failure','unknown']}
for aliases_for_branch in branch_aliases.values():
 for alias,id in aliases_for_branch.items():
  if id in ids:next(n for n in nodes if n['id']==id)['ui_alias']=alias
for n in nodes:
 if n['id'].startswith(NS+'probe.'):n['ui_alias']='check_result'
base['node_aliases']=aliases;base['branch_node_aliases']=branch_aliases
base['prerequisite_probes']=all_probes;base['counterfactual_probes']=canonical.get('counterfactual_probes',[]);base['expected_effects']=canonical['expected_effects']
# Presentation metadata does not mutate the canonical authoring config.
base['schema_version']='aeroagentsim.canonical-graph-presentation/v1';base['canonical_configuration']=config;base['canonical_validation']=canonical.get('validation') or canonical.get('diagnostics');base['canonical_runtime']=canonical.get('fixture_runs') or canonical.get('runtime');base['canonical_config_digest']=canonical['config_digest'];base['canonical_graph_digest']=canonical['graph_digest'];base['canonical_roundtrip']=canonical['roundtrip'];base['canonical_source_sha256']=hashlib.sha256(canonical_file.read_bytes()).hexdigest();base['kind_registry']=kr;base['nodes']=nodes;base['edges']=edges
base['branches']={k:{'zh':v['zh'],'en':v['en']} for k,v in base['branches'].items()}
base['airspace_branches']={k:{'zh':v['zh'],'en':v['en']} for k,v in base['airspace_branches'].items()}
base['airspace_branches']['denied']={'zh':'反例：边缘拒绝','en':'Counterfactual: edge denial'}
base['airspace_branches']['missing']={'zh':'反例：批准消息未到','en':'Counterfactual: missing grant'}
base['review_projection']=canonical.get('review_projection')
base['mapping']={'authority':'canonical_configuration is the only authoring source. All definition nodes/edges are exact graphNodes/graphEdges projections.','runtime':'Authored trace overlay is separate from definitions, with explicit instance links. It demonstrates expected outcomes, not native execution.','export':'Primary JSON export writes canonical_configuration unchanged. No display schema is used as an authoring import.'}
inv=canonical.get('inventory') or json.loads(Path('/workspace/shared/aero-unified-graph-inventory-20261005.json').read_text())
rows=canonical['coverage']['rows']
layer_map=canonical['review_projection']
layer_by_relation={relation:layer['id'] for layer in layer_map['layers'] for relation in layer['relations']}
def convert(rows):
 return [{'name':r['key'],'status':r['status'] if r['status']!='unrepresentable' else r.get('root_cause','implementation_missing'),'evidence':r.get('canonical_ids',[]),'note':r['explanation'],'root_cause':r.get('root_cause'),'layer':layer_by_relation.get(r['key'])} for r in rows]
base['coverage_inventory']={'schema_version':'aeroagentsim.scenario-coverage/v2','scope':'Canonical configuration validation and exact derived graph coverage. All inventory items are accounted for, which is not 100% semantic execution coverage. Full native catalog remains unverified.','node_types':convert([r for r in rows if r['category']=='collection']),'relations':convert([r for r in rows if r['category']=='relation']),'ast_forms':convert([r for r in rows if r['category']=='definition_operator']),'runtime_capabilities':convert([r for r in rows if r['category']=='runtime_capability']),'input_mapping_gaps':convert([r for r in rows if r['category']=='scenario_input_mapping']),'field_level_gaps':[],'canonical_validation':base['canonical_validation'],'full_source_coverage':canonical['coverage'],'relation_layer_inventory':layer_map,'native_catalog':'unavailable'}
from collections import Counter
base['coverage_inventory']['counts']={k:dict(Counter(r['status'] for r in base['coverage_inventory'][k])) for k in ['node_types','relations','ast_forms','runtime_capabilities','input_mapping_gaps']}
base['coverage_inventory']['per_layer_counts']={layer:dict(Counter(r['status'] for r in base['coverage_inventory']['relations'] if r['layer']==layer)) for layer in [x['id'] for x in layer_map['layers']]}
base['coverage']={'node_count':len(nodes),'edge_count':len(edges),'kind_count':len(set(n['kind'] for n in nodes)),'relation_count':len(set(e['type'] for e in edges)),'claim':'Canonical definition graph plus separately labeled synthetic runtime trace. Not full native catalog coverage.'}
(ROOT/'emergency-delivery-graph.config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
(ROOT/'emergency-delivery-graph.fixture.json').write_text(json.dumps(base,ensure_ascii=False,indent=2)+'\n')
(ROOT/'emergency-delivery-graph.coverage.json').write_text(json.dumps(base['coverage_inventory'],ensure_ascii=False,indent=2)+'\n')
template=(Path(__file__).parent/'page.template.html').read_text()
(ROOT/'emergency-delivery-graph.html').write_text(template.replace('__GRAPH_JSON__',json.dumps(base,ensure_ascii=False,separators=(',',':')).replace('</','<\\/')))
print(base['coverage']);print(base['coverage_inventory']['counts'])
