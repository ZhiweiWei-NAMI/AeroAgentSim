#!/usr/bin/env python3
"""Independent static checks, never a simulator or domain rule evaluator.
Run from any CWD. Does not alter stage-one or pipeline/spec/UI source files.
"""
import copy, hashlib, importlib.util, json, pathlib, sys, math
OUT=pathlib.Path(__file__).resolve().parent; WORK=OUT.parents[1]; SRC=pathlib.Path('/workspace/shared/p02_stage1')
T=[]; F=[]
def load(p):return json.loads(pathlib.Path(p).read_text())
def exact(a,b):
 if type(a)!=type(b):return False
 if isinstance(a,dict):return a.keys()==b.keys() and all(exact(a[k],b[k]) for k in a)
 if isinstance(a,list):return len(a)==len(b) and all(exact(x,y) for x,y in zip(a,b))
 return a==b

def check(test,ok,evidence,scope='static_source_semantics'):
 T.append({'id':test,'status':'PASS' if ok else 'FAIL','scope':scope,'evidence':evidence})
def fixture(fid,description,baseline,mutation,expected):F.append({'id':fid,'description':description,'baseline':baseline,'mutation':mutation,'required_outcome':expected,'execution_scope':'static oracle only; not native runtime evaluation'})
def nodes(a):
 if 'collections' in a:return {n['id']:n for arr in a['collections'].values() for n in arr}
 ns=a.get('nodes',[])+a.get('runtime_records',[])+a.get('shared_nodes',[])+a.get('proposed_runtime_records',[])
 ns += [n for s in a.get('steps',[]) for n in s.get('nodes',[])]
 return {n['id']:n for n in ns}
A=load(SRC/'agriculture/agriculture-instances.json');C=load(SRC/'city/city-activity-instances.json');D=load(SRC/'delivery/delivery-activity-instances.json');N=load(SRC/'network/network-instance-catalog.json')
aa={a['activity_index']:a for a in A['activities']}; cc={a['catalog_index']:a for a in C['workflow_instances']};dd={a['catalog_index']:a for a in D['scenarios']};nn={a['id']:a for a in N['original_activity_cases']+N['machine_activity_cases']+N['authored_supplementary_activity_cases']}
# Historical corrections, freshly inspected rather than trusting earlier PASS flags.
mix=aa[1]['steps'][2];mn=nodes(aa[1]);p=mix['participants']
check('D01_physical_mixing_not_terminal',p['controlled_device']=='ag.a01.s03.entity.hardware.mixer' and p['request_interface']=='ag.a01.entity.terminal',p)
bad=[]
for a in A['activities']:
 for s in a['steps']:
  lookup={n['id']:n for n in s['nodes']}
  for e in s['edges']:
   if e['source'].endswith('.behavior.verify') and e['relation']=='REQUIRES' and lookup.get(e['target'],{}).get('collection')=='resources':bad.append(e['id'])
check('D02_readonly_review_does_not_reconsume_material',not bad,{'steps_inspected':67,'violating_edges':bad})
bad=[e['id'] for a in C['workflow_instances'] for e in a['edges'] if e['relation'] in ['REQUIRES','ALLOCATES'] and e['target'].endswith(('.resource.pad','.resource.energy','.resource.workflow'))]
check('D03_no_blanket_city_pad_energy_resources',not bad,{'steps_inspected':90,'violating_edges':bad})
n=nodes(dd[26]);r=n['DLV-A26-001.step03.delivery_receipt']['properties'];q=n['DLV-A26-001.step06.delivery_receipt']['properties']
check('D04_medical_loading_direction',r['sender_ref']=='DLV-A26-001.entity.sender_26' and r['receiver_ref']=='DLV-A26-001.entity.med_uav_26',r)
check('D05_professional_quality_remains_unknown',q['status']=='Unknown' and q['received_at_s'] is None and q['author_entity_ref']=='DLV-A26-001.entity.receiver_26',q)
fixture('F-CUSTODY','Physical unloading does not prove custody/quality.',{'ground_contact':True,'sling_supports_load':True,'sender':'sender26','receiver':'carrier26','quality':'Unknown'},{'derived_custody':'receiver26','derived_quality':'True'},'Reject unsupported derivation; preserve support/contact/custody/professional evidence separately.')
n=nodes(dd[7]);support=[x for x in n.values() if x.get('properties',{}).get('semantic_name')=='sling_supports_load'];contact=[x for x in n.values() if x.get('properties',{}).get('semantic_name')=='ground_contact']
check('D06_positive_support_distinct_ground_contact',len(support)==1 and len(contact)>=1 and support[0]['id'] not in [x['id'] for x in contact],[x['id'] for x in support+contact])
review=next(r for r in cc[15]['supplemental_records'] if r.get('kind')=='engineering_review_record')
check('D07_threshold_not_engineer_approval',review['approval'] is None and review['signed_at_ns'] is None and review['standard_uncertainty']['value']==4,review)
fixture('F-APPROVAL','Numeric suitability is not engineering approval.',{'uncertainty_m3':4,'illustrative_limit_m3':5,'engineering_approval':None},{'engineering_approval':True},'Reject inferred approval; retain missing signed review.')
lineage=[r for r in aa[1]['proposed_runtime_records'] if r.get('proposed_record_kind')=='material_transformation'];mixr=next(r for r in lineage if r['process']=='mixing');filtr=next(r for r in lineage if r['process']=='filtration')
check('D08_material_input_intermediate_output_lineage',len(mixr['input_lot_refs'])==2 and mixr['output_lot_refs']==filtr['input_lot_refs'] and len(filtr['output_lot_refs'])==2 and len(set(mixr['input_lot_refs']+mixr['output_lot_refs']+filtr['output_lot_refs']))==5,[mixr,filtr])
n=nodes(aa[12]);vals={k:n[f'ag.a12.s04.fact.domain-{k}']['value'] for k in ['loaded','released','remaining','cleanout']};residual=vals['loaded']-sum(vals[k] for k in ['released','remaining','cleanout'])
check('D09_authored_feed_balance_only',math.isclose(residual,0,abs_tol=1e-9),{'values_kg':vals,'residual_kg':residual,'scope':'Arithmetic consistency of authored quantities, not observed conservation or fish consumption.'})
fixture('F-MATERIAL','Residual cannot be copied after a quantity changes.',vals,{'released':19.9,'declared_residual':0},'Recompute 20-(19.9+0.3+0.1)=-0.3 kg; flag inconsistent authored balance. Do not claim a native validator ran.')
n=nodes(aa[13]);spec=[x for x in n if '.entity.specimen.' in x];bottles=[x for x in n if '.entity.bottle.' in x]
check('D10_sample_content_container_are_independent',len(spec)==9 and len(bottles)==9 and not set(spec)&set(bottles),{'specimens':spec,'bottles':bottles})
show=next(x for x in C['crosscutting_city_scenarios'] if x['id'].endswith('show-concurrency'));ns=nodes(show);actual=[x for x in ns.values() if x.get('collection')=='facts' and x['id'].endswith('_actual_position')];desired=[x for x in ns.values() if x.get('collection')=='facts' and '_desired_formation_position' in x['id']]
check('D11_desired_pose_not_actual_pose',len(actual)==3 and len(desired)==3 and all(x['value'] is None and x['producer_module_id'].endswith('physics_observation') for x in actual) and all(x['value'] is not None and x['producer_module_id'].endswith('script_authority') for x in desired),[x['id'] for x in actual+desired])
fixture('F-POSE','Commanded formation does not fabricate observed pose.',{'desired_pose':[5,20,25],'actual_pose':None,'takeoff_ack':True},{'actual_pose':[5,20,25]},'Reject copy-from-goal derivation; unknown actual telemetry remains unknown.')
fac=next(x for x in C['crosscutting_city_scenarios'] if x['id'].endswith('facility-contention'));ns=nodes(fac)
phys=ns['city.branch.facility-contention.fact.physical_attachment'];cust=ns['city.branch.facility-contention.fact.custodian'];power=ns['city.branch.facility-contention.fact.available_power_outage']
check('D12_occupancy_contact_custody_power_separate',phys['field_id']!=cust['field_id'] and phys['value'] is None and cust['value'] is not None and power['value']==0,{'physical_attachment':phys,'custody':cust,'power':power})
fixture('F-SLOT','Direction/time are part of permission scope.',{'grant':{'direction':'outbound','window_s':[16,22]}},{'request':{'direction':'return','window_s':[44,78]}},'Grant not applicable to requested direction/time; absence of a matching grant is not invented legal prohibition.')
fixture('F-NETWORK','Provider pending aggregate cannot be summed per link.',{'provider_pending':3,'links':['L1','L2']},{'link_pending':[3,3],'sum':6},'Preserve aggregate scope and mark per-link source missing.')
fixture('F-COMPUTE','CPU/GPU capacity cannot stand in for full bundle.',{'free_cpu':4,'free_gpu':1,'free_memory_gb':0,'request_memory_gb':8},{'admit':True},'Known memory deficit blocks resource admission while unrelated effect may remain Unknown.')
fixture('F-ARBITRATION','Conflicting systems need scoped command authority.',{'target':'uav1','scope':'flight-controller','time_window':[10,12],'commands':['land','continue']},{'winner':'latest_arrival'},'No latest-arrival winner without explicit scoped arbitration policy/evidence.')
fixture('F-UNCERTAINTY','A candidate estimate does not erase uncertainty.',{'candidate_confidence':0.91,'person_identity':None,'location_error_m':3},{'person_verified':True},'Keep identification/position/authorization separate; do not threshold into identity truth.')
fixture('F-LEGAL','Authored site policy is not verified applicable law.',{'capability':True,'site_policy':'synthetic','legal_source':None},{'law_compliant':True},'Preserve legal evidence gap; do not invent legal prohibition or compliance.')
fixture('F-RETRY','Cancellation and late result must retain attempt identity.',{'current_attempt':2,'cancelled_attempt':1},{'late_result_attempt':1,'current_complete':True},'Record late result for attempt1; do not complete attempt2 or overwrite cancellation.')
# Machine protocol distinctions: explicit examples are not runtime outcomes.
mi3=nodes(nn['MI03']);mi4=nodes(nn['MI04']);v=mi4['MI04.conflict.context_fact01']['properties']['value']
check('D13_delayed_close_cannot_close_new_generation',v['received_close_exact_ref']['generation']==1 and v['protocol_alias_binding']['exact_entity_ref']['generation']==2,v)
check('D14_authentication_recovery_session_new',mi3['MI03.recovery.context_fact01']['properties']['value']['session_ref']=='MI03.session02','MI03.recovery.context_fact01')
check('D15_all_machine_phases_concrete_unexecuted',len(N['machine_information_activity_inventory'])==20 and all(set(m['lifecycle'])=={'baseline','degradation','conflict','recovery'} and m['concrete_instance']['execution_status']=='not_executed' for m in N['machine_information_activity_inventory']),{'machine_cases':20})
normal=set(s['normal_branch'] for a in C['workflow_instances'] for s in a['steps'])
check('D16_branch_template_does_not_count_as_domain_test',len(normal)==1,{'city_steps':90,'unique_normal_descriptions':len(normal),'classification':'generic_template; independent domain challenge designs supplied separately'})
# Contract helper adversarial unit cases.
cp=WORK/'spec/contract.py'
if cp.exists():
 spec=importlib.util.spec_from_file_location('independent_p08_contract',cp);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 ref={'run_id':'run1','epoch':0,'id':'sensor1','generation':1,'ref_type':'entity'}
 ref_str={**ref,'generation':'1'};ref_bool={**ref,'generation':True};null_ref={k:None for k in ref}
 check('C01_identity_preserves_generation_type',m.exact_ref_key(ref)!=m.exact_ref_key(ref_str) and m.exact_ref_key(ref)!=m.exact_ref_key(ref_bool),[ref,ref_str,ref_bool],'contract_helper_unit')
 check('C02_unknown_identity_is_not_equivalence',m.exact_ref_key(null_ref) is None and m.exact_ref_key({k:v for k,v in ref.items() if k!='generation'}) is None,[null_ref],'contract_helper_unit')
 base={'semantic_level':'definition','kind':'state_specification','semantic_identity':{'namespace':'a','id':'f','revision':'v1','scope':{'global':True},'definition':{'unit':'m'}}}
 changed=copy.deepcopy(base);changed['semantic_identity']['definition']['unit']='cm'
 missing=copy.deepcopy(base);missing['semantic_identity']['scope']=None
 unknown_id=copy.deepcopy(base);unknown_id['semantic_identity']['id']=None
 check('C03_definition_revision_scope_units_exact',m.definition_merge_compatible(base,copy.deepcopy(base)) and not m.definition_merge_compatible(base,changed),[base,changed],'contract_helper_unit')
 check('C04_unknown_scope_prevents_definition_merge',not m.definition_merge_compatible(missing,copy.deepcopy(missing)) and not m.definition_merge_compatible(unknown_id,copy.deepcopy(unknown_id)),[missing,unknown_id],'contract_helper_unit')
 check('C05_metatype_container_authoritative',m.canonical_kind(raw_kind='entity',source_collection='node_types')=='node_type',{'raw_kind':'entity','source_collection':'node_types'},'contract_helper_unit')
 fixture('F-IDENTITY','Equal display name and raw ID do not establish lifecycle identity.',ref,ref_str,'Do not merge integer generation1 with string generation"1"; null or incomplete identity cannot prove equality.')
 fixture('F-DEF-SCOPE','Identical definition bytes without known scope/revision are not semantic equivalence.',missing,copy.deepcopy(missing),'Keep occurrences distinct or explicitly source-occurrence aliases; no proven semantic merge.')
# Optional normalized graph preservation checks. Every selected raw payload must
# match the immutable source pointer structurally, including null/false/type.
gp=WORK/'data/graph.json'
if gp.exists():
 g=load(gp);index={}
 for n in g['nodes']:
  index.setdefault(n.get('original_id'),[]).append(n)
 expected={'ag.a01.s03.command':'command_definition','ag.a01.s03.command-attempt':'command_attempt','ag.a01.s03.mix-lineage':'material_transformation','city.w15.review.engineer15.vol15-v2':'engineering_review_record','DLV-A26-001.step06.delivery_receipt':'delivery_receipt','A29.step01.command01':'command_attempt'}
 for rid,kind in expected.items():
  found=index.get(rid,[]);check('G-kind-'+rid,bool(found) and all(n['kind']==kind for n in found),{'original_id':rid,'expected_kind':kind,'actual':[n['kind'] for n in found]},'normalized_graph_semantics')
 selected=['ag.a12.s04.fact.domain-loaded','ag.a12.s04.fact.domain-released','ag.a12.s04.fact.domain-remaining','ag.a12.s04.fact.domain-cleanout','ag.a01.s03.mix-lineage','DLV-A26-001.step03.delivery_receipt','DLV-A26-001.step06.delivery_receipt','city.w15.review.engineer15.vol15-v2','MI04.conflict.context_fact01','city.branch.show-concurrency.fact.U3_actual_position','city.branch.facility-contention.fact.available_power_outage']
 for rid in selected:
  found=index.get(rid,[]);ok=bool(found);ev=[]
  for n in found:
   for sr in n['source_records']:
    v=load(SRC/sr['artifact'])
    for p in sr['pointer'].lstrip('/').split('/'):
     p=p.replace('~1','/').replace('~0','~');v=v[int(p)] if isinstance(v,list) else v[p]
    ok &= exact(v,n['payload']);ev.append(sr)
  check('G-preserve-'+rid,ok,ev,'normalized_graph_payload_preservation')
else:T.append({'id':'G-graph-checks','status':'NOT_RUN','scope':'normalized_graph_semantics','evidence':'data/graph.json not yet available'})
result={'schema':'p08.independent-domain-regressions/v1','scope':'Static structure/identity/source semantics. No fixture was executed by a native simulator/rule engine.','unrun_validation_stages':{'native_predicate_engine':'not_run','simulator_domain_execution':'not_run','live_host_physical_evidence':'not_run','real_legal_applicability':'not_verified'},'status':'FAIL' if any(x['status']=='FAIL' for x in T) else 'PASS_WITH_UNRUN_NATIVE_DOMAIN_TESTS','artifact_sha256':{str(p.relative_to(WORK)) if p.is_relative_to(WORK) else str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [pathlib.Path(__file__),cp,WORK/'pipeline/import_catalogs.py',gp] if p.exists()},'tests':T,'counts':{s:sum(t['status']==s for t in T) for s in ['PASS','FAIL','NOT_RUN']}}
(OUT/'regression-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');(OUT/'minimal-regression-fixtures.json').write_text(json.dumps({'schema':'p08.domain-minimal-fixtures/v1','fixtures':F},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':result['status'],'counts':result['counts'],'failures':[x for x in T if x['status']=='FAIL']},ensure_ascii=False,indent=2))
raise SystemExit(1 if result['counts']['FAIL'] else 0)
