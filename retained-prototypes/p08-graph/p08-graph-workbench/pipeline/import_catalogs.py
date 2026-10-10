#!/usr/bin/env python3
"""Lossless, offline P02 -> P08 migration. Never executes or evaluates a fixture.

The immutable input snapshots are the reversal authority. Every canonical node
and edge names exact JSON-pointer occurrences in those snapshots. Label equality
is never an identity rule, and edge occurrences are never deduplicated.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path('/workspace/shared/p02_stage1')
INPUTS = {
 'agriculture': 'agriculture/agriculture-instances.json',
 'delivery': 'delivery/delivery-activity-instances.json',
 'city': 'city/city-activity-instances.json',
 'network': 'network/network-instance-catalog.json',
 'source_catalog': 'sources/original_activity_catalog_35.json',
 'delivery_source': 'delivery/extracted-emergency-source.json',
 'accepted_counts': 'review/artifact-counts.json',
}
COLLECTION_KIND = {'node_types':'node_type','entity_types':'entity_type','entities':'entity','fields':'field','facts':'fact','modules':'module','agents':'agent','strategies':'strategy','capabilities':'capability','behaviors':'behavior_definition','requests':'request','objectives':'objective','decisions':'decision','tasks':'task','resources':'resource','constraints':'constraint','predicates':'predicate','rules':'rule','events':'event_definition','commands':'command_definition','arbitrations':'arbitration','sources':'source','scenarios':'scenario','admission_checks':'admission_check','feedback_policies':'feedback_policy','predicate_results':'predicate_result','relation_definitions':'relation_definition','parameter_definitions':'parameter_definition','spatial_zones':'spatial_zone','lease_requests':'lease_request','lease_approvals':'lease_approval','space_time_allocations':'space_time_allocation','delivery_receipts':'delivery_receipt','expression_types':'expression_type','expressions':'expression'}
DEFINITION_COLLECTIONS = {'node_types','entity_types','fields','behaviors','commands','events','predicates','rules','constraints','capabilities','strategies','relation_definitions','parameter_definitions','expression_types','expressions','admission_checks','feedback_policies'}
CONTRACT = None
contract_path = ROOT/'spec/contract.py'
if contract_path.exists():
 spec = importlib.util.spec_from_file_location('p08_contract',contract_path)
 CONTRACT = importlib.util.module_from_spec(spec)
 sys.modules[spec.name] = CONTRACT
 spec.loader.exec_module(CONTRACT)

def digest(value: Any) -> str:
 return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def file_sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_json(path, data):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n')
def at_pointer(doc, pointer):
 cur=doc
 for part in pointer.lstrip('/').split('/') if pointer else []:
  key=part.replace('~1','/').replace('~0','~')
  cur=cur[int(key)] if isinstance(cur,list) else cur[key]
 return cur

def kind_for(collection,raw):
 kind=raw.get('proposed_record_kind') or raw.get('kind') or raw.get('record_kind')
 if CONTRACT and hasattr(CONTRACT,'canonical_kind'):
  return CONTRACT.canonical_kind(raw_kind=kind or (collection if collection and collection.endswith('_declaration') else None),source_collection=collection)
 if collection in COLLECTION_KIND:return COLLECTION_KIND[collection]
 return {'command':'command_attempt','command_instance':'command_attempt','rule_evaluation_binding':'evaluation_binding'}.get(kind,kind or ('source_declaration' if collection and collection.endswith('_declaration') else 'unmapped_record'))

def level_for(collection, raw, category):
 declared=raw.get('semantic_level',raw.get('level',raw.get('record_level','')))
 if CONTRACT and hasattr(CONTRACT,'semantic_level'):
  return CONTRACT.semantic_level(source_collection=collection,raw_kind=raw.get('proposed_record_kind',raw.get('kind')),raw_level=declared)
 if category=='runtime':return 'runtime_record'
 if collection in {'node_types','entity_types','expression_types','relation_definitions','parameter_definitions','fields'} or declared in {'definition','metatype_definition'}:return 'definition'
 if category=='source':return 'source_record'
 return 'configured_instance'

@dataclass
class Case:
 id: str
 label: str
 domain: str
 artifact: str
 pointer: str
 category: str = 'authored'
 activity_index: int|None = None
 nodes: list = field(default_factory=list)
 edges: list = field(default_factory=list)
 steps: list = field(default_factory=list)
 metadata: dict = field(default_factory=dict)

def extract_cases(docs):
 cases=[]
 def make(cid,label,dom,pointer,category='authored',activity=None,metadata=None):
  c=Case(cid,label,dom,INPUTS[dom],pointer,category,activity,metadata=metadata or {});cases.append(c);return c
 def ns(c,arr,ptr,collection=None,category='authoring',wrapped=False):
  for i,n in enumerate(arr):
   raw=n.get('record',n) if wrapped else n
   coll=collection or (n.get('collection') if wrapped else raw.get('collection')) or (n.get('kind') if wrapped else None)
   c.nodes.append({'pointer':ptr+'/'+str(i),'payload':n,'record':raw,'collection':coll,'category':category,'original_id':raw.get('id',n.get('source_record_id',n.get('id')))})
 def es(c,arr,ptr,category='authoring'):
  for i,e in enumerate(arr):c.edges.append({'pointer':ptr+'/'+str(i),'payload':e,'category':category})
 def step(c,s,ptr,idx,name):c.steps.append({'pointer':ptr,'index':idx,'name':name,'payload':s})
 a=docs['agriculture'];g=make('agriculture.contracts','Agriculture shared frozen contract references','agriculture','/contract_reference_nodes','contract')
 ns(g,a['contract_reference_nodes'],'/contract_reference_nodes')
 for i,s in enumerate(a['activities']):
  p=f'/activities/{i}';c=make(f"ag.a{s['activity_index']:02d}",s['source_activity_name'],'agriculture',p,activity=s['activity_index'],metadata={k:v for k,v in s.items() if k not in {'shared_nodes','shared_edges','steps','proposed_runtime_records','proposed_runtime_edges'}})
  ns(c,s['shared_nodes'],p+'/shared_nodes');es(c,s['shared_edges'],p+'/shared_edges')
  ns(c,s['proposed_runtime_records'],p+'/proposed_runtime_records',category='runtime');es(c,s['proposed_runtime_edges'],p+'/proposed_runtime_edges','runtime')
  for j,st in enumerate(s['steps']):
   sp=f'{p}/steps/{j}';ns(c,st['nodes'],sp+'/nodes');es(c,st['edges'],sp+'/edges');step(c,st,sp,st['step_index'],st['source_step_name'])
 for i,s in enumerate(docs['delivery']['scenarios']):
  p=f'/scenarios/{i}';c=make(s['scenario_id'],s['title'],'delivery',p,activity=s.get('catalog_index'),metadata={k:v for k,v in s.items() if k not in {'nodes','edges','runtime_records','runtime_edges','step_mappings'}})
  for key,cat in [('nodes','authoring'),('runtime_records','runtime')]:ns(c,s[key],p+'/'+key,category=cat)
  for key,cat in [('edges','authoring'),('runtime_edges','runtime')]:es(c,s[key],p+'/'+key,cat)
  for j,st in enumerate(s['step_mappings']):step(c,st,f'{p}/step_mappings/{j}',st['step_index'],st['exact_source_step'])
 for i,s in enumerate(docs['city']['workflow_instances']):
  p=f'/workflow_instances/{i}';c=make(s['workflow_id'],s['original_name'],'city',p,activity=s['catalog_index'],metadata={k:v for k,v in s.items() if k not in {'collections','edges','runtime_edges','supplemental_records','steps'}})
  for coll,arr in s['collections'].items():ns(c,arr,p+'/collections/'+coll,collection=coll)
  ns(c,s['supplemental_records'],p+'/supplemental_records',category='runtime');es(c,s['edges'],p+'/edges');es(c,s['runtime_edges'],p+'/runtime_edges','runtime')
  for j,st in enumerate(s['steps']):step(c,st,f'{p}/steps/{j}',st['source_step_index'],st['source_step_name'])
 for i,s in enumerate(docs['city']['crosscutting_city_scenarios']):
  p=f'/crosscutting_city_scenarios/{i}';c=make(s['id'],s['title'],'city',p,metadata={k:v for k,v in s.items() if k not in {'nodes','edges'}});ns(c,s['nodes'],p+'/nodes');es(c,s['edges'],p+'/edges')
 s=docs['city']['source_fixture'];c=make(s['id'],'Preserved city emergency fixture','city','/source_fixture','source',metadata={k:v for k,v in s.items() if k!='collections'})
 for coll,arr in s['collections'].items():ns(c,arr,'/source_fixture/collections/'+coll,collection=coll,category='source',wrapped=True)
 for key in ['original_activity_cases','authored_supplementary_activity_cases','machine_activity_cases']:
  for i,s in enumerate(docs['network'][key]):
   p=f'/{key}/{i}';c=make(s['id'],s['title'],'network',p,activity=s.get('catalog_activity_index'),metadata={k:v for k,v in s.items() if k not in {'nodes','relation_occurrences','source_step_mappings'}})
   for j,n in enumerate(s['nodes']):ns(c,[n],p+f'/nodes/{j}',category='runtime' if n.get('collection') is None else 'authoring');c.nodes[-1]['pointer']=p+f'/nodes/{j}'
   for j,e in enumerate(s['relation_occurrences']):es(c,[e],p+f'/relation_occurrences/{j}', 'authoring' if e.get('frozen_registry_relation') else 'runtime');c.edges[-1]['pointer']=p+f'/relation_occurrences/{j}'
   for j,st in enumerate(s.get('source_step_mappings',[])):step(c,st,f'{p}/source_step_mappings/{j}',st['source_step_index'],st['source_step_name'])
 for i,s in enumerate(docs['network']['source_backed_activity_cases']):
  p=f'/source_backed_activity_cases/{i}';c=make(s['id'],s['title'],'network',p,'source',metadata={k:v for k,v in s.items() if k not in {'source_occurrences','relation_occurrences'}})
  ns(c,s['source_occurrences'],p+'/source_occurrences',category='source',wrapped=True);es(c,s['relation_occurrences'],p+'/relation_occurrences','source')
 c=make('network.declarations','Preserved network source declarations','network','/source_declaration_occurrences','source')
 ns(c,docs['network']['source_declaration_occurrences'],'/source_declaration_occurrences',category='source',wrapped=True)
 d=docs['delivery_source'];c=Case('delivery.source','Preserved delivery emergency source','delivery_source',INPUTS['delivery_source'],'','source',metadata={k:v for k,v in d.items() if k not in {'nodes','reference_occurrences','explicit_typed_edges'}});cases.append(c)
 ns(c,d['nodes'],'/nodes',category='source',wrapped=True);es(c,d['reference_occurrences'],'/reference_occurrences','source');es(c,d['explicit_typed_edges'],'/explicit_typed_edges','source')
 return cases

def identity_projection(raw):
 p=raw.get('properties',{})
 declared={k:raw[k] for k in ['identity','ref','subject_ref','entity_ref','run_ref'] if k in raw}
 declared.update({f'properties.{k}':p[k] for k in ['identity','ref','subject_ref','entity_ref','run_id','epoch','generation'] if k in p})
 for k in ['run_id','epoch','generation','revision','version','role','entity_id','field_id','record_kind']:
  if k in raw:declared[k]=raw[k]
 return {'declared':declared,'missing_policy':'No identity, generation, version, role or scope is inferred from a label.'}

def semantics_projection(raw,kind):
 p=raw.get('properties',{}) if isinstance(raw.get('properties',{}),dict) else {}
 values={**raw,**p}
 out={'evidence_status':'source_preserved' if kind=='source_declaration' else 'authored_or_source_preserved_not_runtime_verified','execution_status':'not_executed_by_importer'}
 selected=['value_type','datatype','data_type','quantity','unit','frame','field_id','field_ref','entity_id','subject_ref','subject','producer_module_id','producer_module_ref','source_id','result','verdict','truth','truth_value','expected_truth','declared_fixture_expectation','actual_execution','execution_status','actual_runtime_status']
 out['declared']={k:values[k] for k in selected if k in values}
 if 'value' in values:
  v=values['value'];jtype='null' if v is None else 'boolean' if isinstance(v,bool) else 'integer' if isinstance(v,int) else 'number' if isinstance(v,float) else 'string' if isinstance(v,str) else 'array' if isinstance(v,list) else 'object'
  out['literal']={'value':v,'json_type':jtype,'declared_type':values.get('value_type',values.get('datatype',values.get('data_type'))),'unit':values.get('unit'),'quantity':values.get('quantity'),'frame':values.get('frame'),'is_null':v is None,'type_is_inferred':False,'json_type_is_structural':True}
 out['time']={k:v for k,v in values.items() if k in {'clock','clock_domain','availability_domain','valid_time_ns','available_time_ns','valid_until_ns','valid_from_ns','valid_to_ns','valid_time','available_time','validity','time','time_scope','scope','observed_at_ns','query_time_ns','availability_cutoff_ns','issued_at_ns','window','window_ns','time_window','history_window','valid_interval_s','available_at_s','valid_from_s','valid_until_s','valid_at_s','interval_s','at_ns','validity_policy'}}
 return out

def relation_for(raw,source,target,artifact,category):
 relation=raw.get('relation','unmapped_relation')
 frozen=raw.get('frozen_registry_relation')
 if not frozen and category in {'authoring','source'} and relation.isupper():frozen=relation
 if CONTRACT and hasattr(CONTRACT,'canonical_relation'):
  return CONTRACT.canonical_relation(relation,source_kind=source['raw_kind'],target_kind=target['raw_kind'],source_collection=source['source_collection'],target_collection=target['source_collection'],frozen_relation=frozen,artifact=artifact,role=raw.get('role'),payload=raw)
 return frozen or relation

def build(source_root:Path,out:Path):
 docs={};manifest_inputs=[]
 for owner,rel in INPUTS.items():
  src=source_root/rel;dst=out/'inputs'/rel
  sha=file_sha(src)
  if dst.exists() and file_sha(dst)!=sha:raise ValueError(f'Immutable snapshot mismatch: {rel}; use a new output directory/version')
  if not dst.exists():dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);dst.chmod(0o444)
  docs[owner]=json.loads(dst.read_text());manifest_inputs.append({'id':owner,'artifact':rel,'path':'inputs/'+rel,'sha256':sha,'bytes':dst.stat().st_size})
 sha_by_path={x['artifact']:x['sha256'] for x in manifest_inputs}
 registry_path=ROOT/'spec/relation-registry.json'
 registry=json.loads(registry_path.read_text()) if registry_path.exists() else {'variants':[]}
 signature_registry=defaultdict(list)
 for variant in registry['variants']:signature_registry[(variant['relation'],variant['source_kind'],variant['target_kind'])].append(variant['id'])
 cases=extract_cases(docs);nodes=[];edges=[];aliases=[];conflicts=[];unresolved_edges=[];node_by_id={};node_lookup=defaultdict(list);dedup={};case_nodes=defaultdict(set);case_edges=defaultdict(list);case_occurrences=Counter();relation_signatures=Counter();unmapped=Counter()
 def source_record(c,ptr,rawid):return {'artifact':c.artifact,'pointer':ptr,'source_id':rawid,'case_id':c.id,'sha256':sha_by_path[c.artifact]}
 for c in cases:
  for o in c.nodes:
   raw=o['record'];coll=o['collection'];rawid=o['original_id'];kind=kind_for(coll,raw);level=level_for(coll,raw,o['category']);sr=source_record(c,o['pointer'],rawid);oid='occ:'+digest([c.artifact,o['pointer']])[:24]
   if not isinstance(kind,str):raise TypeError(f'canonical_kind must return string, got {kind!r}')
   # Same declared ID AND exact semantic record bytes AND same immutable artifact
   # establish a repeated definition. A label, actor ID or endpoint pair never does.
   revision=raw.get('revision',raw.get('version'))
   scope=raw.get('scope',raw.get('definition_scope',raw.get('scenario_id')))
   candidate_identity={'namespace':c.artifact,'id':rawid,'revision':revision,'scope':scope,'definition':raw}
   candidate_definition={'kind':kind,'semantic_level':level,'semantic_identity':candidate_identity}
   proven_definition=CONTRACT.definition_merge_compatible(candidate_definition,candidate_definition) if CONTRACT else False
   key=(c.artifact,coll,rawid,digest(raw)) if proven_definition else None
   proof='same_immutable_artifact_declared_definition_id_revision_scope_and_exact_payload'
   if key and key in dedup:
    nid=dedup[key];node_by_id[nid]['source_records'].append(sr);node_by_id[nid]['occurrence_ids'].append(oid)
   else:
    nid='n:'+digest(['definition',*key] if key else ['occurrence',c.artifact,o['pointer']])[:24]
    n={'id':nid,'kind':kind,'semantic_level':level,'label':raw.get('label_zh') or raw.get('label') or raw.get('title') or rawid,'raw_kind':raw.get('proposed_record_kind',raw.get('kind',raw.get('record_kind'))),'source_collection':coll,'original_id':rawid,'provenance':raw.get('provenance',o['payload'].get('provenance',{'status':'see_immutable_source_context'})),'source_records':[sr],'payload':o['payload'],'identity':identity_projection(raw),'semantics':semantics_projection(raw,kind),'occurrence_ids':[oid]}
    if proven_definition:n['semantic_identity']={'namespace':c.artifact,'id':rawid,'revision':revision,'scope':scope,'definition':raw}
    nodes.append(n);node_by_id[nid]=n
    if key:dedup[key]=nid
   case_nodes[c.id].add(nid);case_occurrences[(c.id,'nodes')]+=1
   node_lookup[(c.id,rawid)].append(nid)
   aliases.append({'occurrence_id':oid,'canonical_id':nid,'record_type':'node','original_id':rawid,'source_record':sr,'raw_sha256':digest(o['payload']),'merge_basis':proof if key else 'none_missing_revision_or_scope' if level=='definition' else 'none_occurrence_identity','reversible':True})
   if kind.startswith('unmapped'):unmapped[kind]+=1
 network_source=next(x for x in docs['network']['sources'] if x['id']=='S01')
 delivery_source=next(x for x in docs['delivery_source']['source_manifest'] if x['id']=='emergency_config')
 primary_path=source_root/'primary/emergency-delivery-graph.config.json'
 if not primary_path.exists():primary_path=Path(network_source['path'])
 primary_sha=file_sha(primary_path)
 shared_primary_source=network_source['sha256']==delivery_source['sha256']==primary_sha
 primary_snapshot=out/'inputs/primary/emergency-delivery-graph.config.json'
 if primary_snapshot.exists() and file_sha(primary_snapshot)!=primary_sha:raise ValueError('Primary source immutable snapshot mismatch')
 if not primary_snapshot.exists():primary_snapshot.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(primary_path,primary_snapshot);primary_snapshot.chmod(0o444)
 primary_doc=json.loads(primary_snapshot.read_text())
 write_json(out/'primary-source-evidence.json',{'path':'inputs/primary/emergency-delivery-graph.config.json','sha256':primary_sha,'original_path':network_source['path'],'claimed_hashes_verified':shared_primary_source})
 endpoint_closures=[]
 global_nodes={rid:ids for (cid,rid),ids in node_lookup.items() if cid=='agriculture.contracts'}
 def resolve(c,endpoint,declared_coll=None):
  rid=endpoint.get('id') if isinstance(endpoint,dict) else endpoint
  coll=(endpoint.get('collection') or endpoint.get('kind')) if isinstance(endpoint,dict) else declared_coll
  ids=list(dict.fromkeys(node_lookup.get((c.id,rid),[]) or (global_nodes.get(rid,[]) if c.domain=='agriculture' else [])))
  if not ids and c.domain=='network' and c.category=='source' and coll=='node_types' and shared_primary_source:
   ids=list(dict.fromkeys(node_lookup.get(('delivery.source',rid),[])))
   if len(ids)==1:
    closure_node=node_by_id[ids[0]];wrapper=closure_node['payload'];primary_pointer=wrapper['source_pointer']['json_pointer'];primary_record=at_pointer(primary_doc,primary_pointer)
    if digest(primary_record)!=digest(wrapper['record']):raise ValueError('Primary source record mismatch: '+primary_pointer)
    endpoint_closures.append({'case_id':c.id,'original_id':rid,'canonical_id':ids[0],'proof':'verified_primary_source_bytes_sha256_and_exact_record_pointer','primary_source_sha256':primary_sha,'primary_pointer':primary_pointer,'primary_record_sha256':digest(primary_record),'primary_snapshot':'inputs/primary/emergency-delivery-graph.config.json','source_record':node_by_id[ids[0]]['source_records'][0]})
  if coll:matches=[nid for nid in ids if node_by_id[nid]['source_collection']==coll or node_by_id[nid]['kind']==coll];ids=matches or ids
  if len(ids)!=1:return None,{'raw_id':rid,'candidates':ids,'declared_collection':coll}
  return ids[0],None
 for c in cases:
  for o in c.edges:
   raw=o['payload'];src=raw.get('source',raw.get('source_record_ref'));dst=raw.get('target',raw.get('target_record_ref'));sid,se=resolve(c,src,raw.get('source_collection'));tid,te=resolve(c,dst,raw.get('target_collection'));sr=source_record(c,o['pointer'],raw.get('id'));eid='e:'+digest([c.artifact,o['pointer']])[:24];case_occurrences[(c.id,'edges')]+=1
   aliases.append({'occurrence_id':'occ:'+digest([c.artifact,o['pointer']])[:24],'canonical_id':eid,'record_type':'edge','original_id':raw.get('id'),'source_record':sr,'raw_sha256':digest(raw),'merge_basis':'none_parallel_edges_and_repeated_checks_preserved','reversible':True})
   if se or te:
    unresolved={'id':eid,'case_id':c.id,'source_error':se,'target_error':te,'source_records':[sr],'payload':raw};unresolved_edges.append(unresolved);conflicts.append({'category':'unresolved_endpoint','status':'quarantined_not_fabricated','edge_id':eid,'details':unresolved});continue
   s=node_by_id[sid];t=node_by_id[tid];relation=relation_for(raw,s,t,c.artifact,o['category'])
   lvl=CONTRACT.edge_level(s['semantic_level'],t['semantic_level']) if CONTRACT else 'source_record' if o['category']=='source' else 'runtime' if o['category']=='runtime' else 'definition' if s['semantic_level']==t['semantic_level']=='definition' else 'configuration' if s['semantic_level']==t['semantic_level']=='configured_instance' else 'cross_level'
   edge={'id':eid,'relation':relation,'raw_relation':raw.get('relation'),'source':sid,'target':tid,'source_kind':s['kind'],'target_kind':t['kind'],'role':raw.get('role',''),'semantic_level':lvl,'provenance':raw.get('provenance',{'status':'see_immutable_source_context'}),'source_records':[sr],'payload':raw,'original_id':raw.get('id'),'condition':raw.get('condition'),'order':raw.get('order'),'declared_time':raw.get('validity',raw.get('time_scope',raw.get('time'))),'case_id':c.id,'contract_status':'preserved_pending_registry_validation'}
   variants=signature_registry.get((relation,s['kind'],t['kind']),[]) or signature_registry.get((relation,'*','*'),[])
   edge['contract_variant_ids']=variants
   edge['contract_status']='typed_signature_valid' if variants else 'missing_registry_signature'
   if not variants:conflicts.append({'category':'missing_registry_signature','status':'preserved_not_cast','edge_id':eid,'relation':relation,'source_kind':s['kind'],'target_kind':t['kind']})
   edges.append(edge);case_edges[c.id].append(edge);case_nodes[c.id].update([sid,tid]);relation_signatures[(relation,s['kind'],t['kind'])]+=1
 # Explicit conflict inventory: shared labels are retained, not merged. This is a
 # collision diagnostic and does not claim that similarly named records are equal.
 by_literal=defaultdict(list)
 for n in nodes:by_literal[(n['source_collection'],n['original_id'])].append(n)
 for (coll,rid),same in by_literal.items():
  if len(same)>1 and any(n['semantic_level']=='definition' for n in same):
   conflicts.append({'category':'definition_identity_requires_review','status':'kept_distinct','source_collection':coll,'original_id':rid,'canonical_ids':[n['id'] for n in same],'reason':'Distinct artifact identities or non-identical source payloads; no label-based merge.'})
 coverage=[]
 for c in cases:
  if not c.activity_index:continue
  activity=docs['source_catalog']['activities'][c.activity_index-1]
  for st in c.steps:
   expected=activity['steps'][st['index']-1];refs=set()
   def collect(v):
    if isinstance(v,str):
     ids=node_lookup.get((c.id,v),[]) or (global_nodes.get(v,[]) if c.domain=='agriculture' else []);refs.update(ids)
    elif isinstance(v,list):
     for vv in v:collect(vv)
    elif isinstance(v,dict):
     for vv in v.values():collect(vv)
   collect(st['payload'])
   coverage.append({'activity_index':c.activity_index,'activity_name':activity['name'],'step_index':st['index'],'source_step':expected,'imported_step':st['name'],'source_match':st['name']==expected,'case_id':c.id,'source_catalog_pointer':f"/activities/{c.activity_index-1}/steps/{st['index']-1}",'mapping_source':source_record(c,st['pointer'],None),'node_ids':sorted(refs),'status':'mapped_static_authored_examples','execution_verified':False})
 coverage.sort(key=lambda x:(x['activity_index'],x['step_index']))
 before=[]
 for accepted in docs['accepted_counts']['counts']:
  dom=accepted['group'];domain_cases=[c for c in cases if c.domain==dom and c.category in {'authored','contract'}]
  ntypes=Counter(o['category'] for c in domain_cases for o in c.nodes);etypes=Counter(o['category'] for c in domain_cases for o in c.edges)
  actual={'authoring_node_occurrences':ntypes['authoring'],'runtime_record_occurrences':ntypes['runtime'],'frozen_signature_edge_occurrences':etypes['authoring'],'proposed_runtime_edge_occurrences':etypes['runtime']}
  mismatches={k:{'expected':accepted[k],'actual':v} for k,v in actual.items() if accepted[k]!=v}
  before.append({'domain':dom,'source_counts':actual,'accepted_sha256_match':accepted['sha256']==sha_by_path[INPUTS[dom]],'count_mismatches':mismatches})
 counts={'input_artifacts':len(manifest_inputs),'authored_cases':sum(c.category=='authored' for c in cases),'source_cases':sum(c.category=='source' for c in cases),'contract_cases':sum(c.category=='contract' for c in cases),'source_activities':len({x['activity_index'] for x in coverage}),'source_steps':len(coverage),'machine_examples':len(docs['network']['machine_activity_cases']),'before_node_occurrences':sum(len(c.nodes) for c in cases),'node_count':len(nodes),'edge_count':len(edges),'after_canonical_nodes':len(nodes),'merged_definition_occurrences':sum(len(c.nodes) for c in cases)-len(nodes),'before_edge_occurrences':sum(len(c.edges) for c in cases),'after_resolved_edges':len(edges),'quarantined_edges':len(unresolved_edges),'deduplicated_edges':0,'conflict_records':len(conflicts),'by_domain':before}
 if any(x['count_mismatches'] or not x['accepted_sha256_match'] for x in before):raise ValueError('Accepted input count/hash mismatch: '+str(before))
 graph={'schema_version':'p08.typed-graph/v1','contract_version':getattr(CONTRACT,'CONTRACT_VERSION','not_loaded'),'status':'static_authored_fixture_graph_not_executed','counts':counts,'nodes':nodes,'edges':edges,'unsupported':{'unresolved_edges':unresolved_edges,'native_execution':'not_available','real_host_connection':'not_verified','legal_applicability':'not_verified'},'provenance':{'inputs':manifest_inputs,'migration':'pipeline/import_catalogs.py','identity_policy':'occurrence-by-default; same artifact, declared definition ID, explicit revision/scope, and exact semantic payload required for definition reuse'},'coverage':coverage}
 write_json(out/'endpoint-closures.json',{'closures':endpoint_closures});write_json(out/'graph.json',graph);write_json(out/'aliases.json',{'schema_version':'p08.reversible-aliases/v1','aliases':aliases,'reverse_source':'inputs/{artifact} at source_record.pointer'});write_json(out/'conflicts.json',{'schema_version':'p08.conflicts/v1','conflicts':conflicts});write_json(out/'counts.json',counts);write_json(out/'coverage.json',{'source_activities':35,'source_steps':226,'steps':coverage});write_json(out/'input-manifest.json',{'inputs':manifest_inputs})
 chunks=[]
 for c in cases:
  path='scenarios/'+re.sub(r'[^A-Za-z0-9_.-]','_',c.id)+'.json';cn=[node_by_id[x] for x in sorted(case_nodes[c.id])];ce=case_edges[c.id]
  chunk={'schema_version':'p08.typed-graph/v1','id':c.id,'label':c.label,'domain':c.domain,'activity_index':c.activity_index,'category':c.category,'nodes':cn,'edges':ce,'metadata':c.metadata,'steps':[x for x in coverage if x['case_id']==c.id],'unsupported':{'execution':'not_executed_by_importer','unresolved_edges':[e for e in unresolved_edges if e['case_id']==c.id]},'counts':{'nodes':len(cn),'edges':len(ce),'node_occurrences':case_occurrences[c.id,'nodes'],'edge_occurrences':case_occurrences[c.id,'edges']}}
  write_json(out/path,chunk);chunks.append({'id':c.id,'label':c.label,'domain':c.domain,'activity_index':c.activity_index,'category':c.category,'path':path,'node_count':len(cn),'edge_count':len(ce),'node_occurrences':case_occurrences[c.id,'nodes'],'edge_occurrences':case_occurrences[c.id,'edges'],'execution_status':'not_executed_by_importer','truth_status':'source_values_retained_no_evaluation','unresolved_edge_count':len(chunk['unsupported']['unresolved_edges'])})
 search=[{'id':n['id'],'label':n['label'],'kind':n['kind'],'semantic_level':n['semantic_level'],'original_id':n['original_id'],'raw_ids':[n['original_id']],'case_ids':sorted({s['case_id'] for s in n['source_records']})} for n in nodes]
 write_json(out/'search-index.json',{'nodes':search});write_json(out/'relation-signatures.json',{'signatures':[{'relation':r,'source_kind':s,'target_kind':t,'occurrences':v} for (r,s,t),v in sorted(relation_signatures.items())]})
 write_json(out/'manifest.json',{'schema_version':'p08.manifest/v1','graph_schema':'p08.typed-graph/v1','title':'P08 lossless linked-graph workbench','counts':counts,'scenarios':chunks,'search_index':'search-index.json','files':{'graph':'graph.json','aliases':'aliases.json','conflicts':'conflicts.json','coverage':'coverage.json','search_index':'search-index.json','inputs':'input-manifest.json','relation_signatures':'relation-signatures.json'},'warnings':['All source examples retain their authored/unexecuted status.','Static graph validation is not native execution, legal applicability or real-host readiness.','Cross-artifact definition collisions are retained pending semantic proof.']})
 return counts

if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--source-root',type=Path,default=DEFAULT_SOURCE);ap.add_argument('--output',type=Path,default=ROOT/'data');args=ap.parse_args();print(json.dumps(build(args.source_root,args.output),ensure_ascii=False,indent=2))
