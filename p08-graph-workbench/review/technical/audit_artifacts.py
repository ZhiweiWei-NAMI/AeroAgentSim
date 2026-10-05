#!/usr/bin/env python3
"""Independent artifact-wide acceptance. Does not import production compiler."""
from pathlib import Path
from collections import Counter,defaultdict
import json,hashlib,time,sys
from source_oracle import occurrences,resolve,digest,STAGE1
ROOT=Path(__file__).resolve().parents[2];DATA=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'data'
def read(p):return json.loads(p.read_text())
def raw(n):return n['payload'].get('record',n['payload'])
def raw_endpoint(x):return x.get('id') if isinstance(x,dict) else x
start=time.perf_counter();graph=read(DATA/'graph.json');aliases=read(DATA/'aliases.json')['aliases'];manifest=read(DATA/'input-manifest.json')['inputs'];coverage=read(DATA/'coverage.json')['steps']
nodes={x['id']:x for x in graph['nodes']};edges={x['id']:x for x in graph['edges']};quarantine={x['id']:x for x in graph.get('unsupported',{}).get('unresolved_edges',[])}
docs={x['artifact']:read(DATA/x['path']) for x in manifest};shas={x['artifact']:x['sha256'] for x in manifest}
checks=[]
edge_targets={**edges,**quarantine}
def record(name,failures,covered,note=''):
 failures=list(failures);checks.append({'name':name,'status':'pass' if not failures else 'fail','covered':covered,'failures':len(failures),'examples':failures[:12],'note':note})
fail=[]
for m in manifest:
 p=DATA/m['path'];sha=hashlib.sha256(p.read_bytes()).hexdigest();original=STAGE1/m['artifact']
 if sha!=m['sha256'] or not original.exists() or p.read_bytes()!=original.read_bytes():fail.append(m['artifact'])
record('immutable_source_documents_byte_exact',fail,len(manifest),'Snapshot-backed reverse export, not a claim that normalized graph alone reconstructs omitted metadata.')
by_pointer=defaultdict(list)
for a in aliases:by_pointer[(a['source_record']['artifact'],a['source_record']['pointer'])].append(a)
expected=occurrences();fail=[]
for occurrence in expected:
 key=(occurrence['artifact'],occurrence['pointer']);group=by_pointer.get(key,[])
 if len(group)!=1:fail.append({'pointer':key,'alias_count':len(group)});continue
 a=group[0]
 if a['raw_sha256']!=occurrence['digest'] or a['record_type']!=occurrence['record_kind']:fail.append({'pointer':key,'reason':'digest_or_record_type'})
record('exhaustive_independent_stage1_occurrence_coverage',fail,len(expected),'20,004 node + 63,870 edge occurrences in four accepted inventories; extra extracted-source artifacts assessed separately.')
fail=[]
for a in aliases:
 sr=a['source_record']
 try:source=resolve(docs[sr['artifact']],sr['pointer'])
 except Exception as e:fail.append({'alias':a['occurrence_id'],'error':str(e)});continue
 if digest(source)!=a['raw_sha256'] or sr['sha256']!=shas[sr['artifact']]:fail.append({'alias':a['occurrence_id'],'error':'source_sha_mismatch'})
 target=(nodes if a['record_type']=='node' else edge_targets).get(a['canonical_id'])
 if target is None:fail.append({'alias':a['occurrence_id'],'error':'missing_canonical_or_quarantine_target'})
 elif sr not in target.get('source_records',[]):fail.append({'alias':a['occurrence_id'],'error':'canonical_target_lacks_occurrence_pointer'})
record('all_alias_pointers_resolve_and_reverse_exactly',fail,len(aliases),'Every occurrence reconstructable from packaged source snapshots using alias artifact/pointer; snapshot SHA verified.')
fail=[]
for kind,items in [('node',graph['nodes']),('edge',graph['edges'])]:
 for n in items:
  for sr in n['source_records']:
   try:source=resolve(docs[sr['artifact']],sr['pointer'])
   except Exception as e:fail.append({'id':n['id'],'error':str(e)});continue
   if sr['sha256']!=shas[sr['artifact']]:fail.append({'id':n['id'],'error':'source_sha'})
   # Canonical nodes may coalesce equal unwrapped definitions, but every original wrapper remains in snapshots.
   nraw=raw(n);sraw=source.get('record',source)
   if digest(nraw)!=digest(sraw):fail.append({'id':n['id'],'error':'canonical_payload_changed','pointer':sr['pointer']})
record('canonical_payloads_preserve_raw_record_exactly',fail,len(nodes)+len(edges),'Exact JSON structure comparison includes AST child order/literals, time domains, nanosecond strings, null/false/unknown, generation and roles.')
fail=[]
for e in graph['edges']:
 if e['source'] not in nodes or e['target'] not in nodes:fail.append({'id':e['id'],'error':'dangling'});continue
 p=e['payload'];a=raw_endpoint(p.get('source',p.get('source_record_ref')));b=raw_endpoint(p.get('target',p.get('target_record_ref')))
 for side,wanted in [('source',a),('target',b)]:
  n=nodes[e[side]]
  if wanted!=n.get('original_id'):fail.append({'id':e['id'],'error':'endpoint_reversed_or_changed','side':side,'expected':wanted,'actual':n.get('original_id')})
record('raw_edge_direction_and_endpoints_preserved',fail,len(edges))
edge_aliases=[a for a in aliases if a['record_type']=='edge'];counts=Counter(a['canonical_id'] for a in edge_aliases)
record('parallel_edge_occurrences_not_deduplicated',[{'id':k,'occurrences':v} for k,v in counts.items() if v!=1],len(edge_aliases))
fail=[];merged=[]
for n in graph['nodes']:
 if len(n['source_records'])<=1:continue
 merged.append(n);r=raw(n)
 if n.get('semantic_level')!='definition':fail.append({'id':n['id'],'reason':'merged_nondefinition'})
 if not any(r.get(k) is not None for k in ('revision','version','definition_revision','definition_version')):fail.append({'id':n['id'],'reason':'missing_explicit_definition_revision','kind':n['kind'],'original_id':n.get('original_id')})
record('merged_definition_aliases_have_explicit_revision',fail,len(merged),'No revision is inferred from repeated labels, equal bytes, or shared enclosing artifact alone.')
expected_steps={ (a['catalog_index'],i+1):(a['name'],s) for a in read(STAGE1/'sources/original_activity_catalog_35.json')['activities'] for i,s in enumerate(a['steps'])};seen=Counter();fail=[]
for s in coverage:
 k=(s['activity_index'],s['step_index']);seen[k]+=1
 if expected_steps.get(k)!=(s['activity_name'],s['source_step']) or s['source_step']!=s['imported_step']:fail.append({'key':k,'error':'source_name_or_order'})
 if not s.get('node_ids'):fail.append({'key':k,'error':'no_concrete_nodes'})
 if any(n not in nodes for n in s.get('node_ids',[])):fail.append({'key':k,'error':'unknown_node_ref'})
for k in expected_steps:
 if seen[k]!=1:fail.append({'key':k,'error':'mapping_count','actual':seen[k]})
record('exact_35_activity_226_step_coverage',fail,len(expected_steps))
# Known argument directions proven by the accepted source inventories. No endpoint reversal is permissible.
fail=[];args=[]
for e in graph['edges']:
 if e['payload'].get('relation')!='ARGUMENT':continue
 args.append(e);artifact=e['source_records'][0]['artifact'];expected_rel='has_argument' if artifact.startswith('delivery/') else 'argument_of'
 if e['relation']!=expected_rel:fail.append({'id':e['id'],'artifact':artifact,'actual':e['relation'],'expected':expected_rel})
record('argument_direction_semantics_mapped_by_verified_source_profile',fail,len(args))
fail=[];typed=[]
expected_kinds={'fields':'state_specification','facts':'fact','commands':'command_definition','behaviors':'behavior_definition','rules':'rule_definition'}
for n in graph['nodes']:
 coll=n.get('source_collection')
 if coll not in expected_kinds:continue
 typed.append(n)
 if n['kind']!=expected_kinds[coll]:fail.append({'id':n['id'],'collection':coll,'expected':expected_kinds[coll],'actual':n['kind']})
record('state_fact_and_definition_instance_type_distinction',fail,len(typed))
# JSON null is structural; no normalized field may fabricate a truth classification.
fail=[];literal_count=0
for n in graph['nodes']:
 literal=n.get('semantics',{}).get('literal')
 if literal is None:continue
 literal_count+=1;value=literal.get('value')
 if 'is_unknown' in literal or literal.get('is_null') is not (value is None):fail.append({'id':n['id'],'reason':'null_truth_conflation','literal':literal})
record('normalized_json_null_is_not_semantic_unknown',fail,literal_count,'Structural is_null flag only; source truth fields retain their independently declared values.')
# Hyperedges retain declared named-role incidence and n-ary transformation membership.
adjacent=defaultdict(list)
for e in graph['edges']:adjacent[e['source']].append(e)
fail=[];hyperedges=[]
for n in graph['nodes']:
 if n['kind'] not in ('relation_instance','material_transformation'):continue
 hyperedges.append(n);payload=raw(n);outgoing=adjacent[n['id']]
 if n['kind']=='relation_instance' and isinstance(payload.get('roles'),dict):
  wanted=Counter((role,raw_endpoint(ref)) for role,ref in payload['roles'].items())
  actual=Counter((e['role'],nodes[e['target']]['original_id']) for e in outgoing if e['relation']=='binds_role')
  if actual!=wanted:fail.append({'id':n['id'],'reason':'role_incidence_or_arity','expected':list(wanted.items()),'actual':list(actual.items())})
 if n['kind']=='material_transformation':
  for key,rel in [('input_lot_refs','consumes_input_lot'),('output_lot_refs','produces_output_lot'),('equipment_refs','uses_equipment')]:
   wanted=Counter(payload.get(key,[]));actual=Counter(nodes[e['target']]['original_id'] for e in outgoing if e['relation']==rel)
   if actual!=wanted:fail.append({'id':n['id'],'reason':'transformation_incidence','role':key,'expected':dict(wanted),'actual':dict(actual)})
record('hyperedge_named_roles_and_nary_arity_preserved',fail,len(hyperedges),'Checks all declared role bindings and transformation member multiplicity; source list ordering separately covered by exact raw payload checks.')
# Cross-artifact endpoint closure is accepted only through exact primary-source proof.
closures=read(DATA/'endpoint-closures.json')['closures'];fail=[]
network_sources=docs['network/network-instance-catalog.json']['sources']
extract=docs['delivery/extracted-emergency-source.json']
primary_refs={m['id']:m for m in extract['source_manifest']};primary_cache={}
for c in closures:
 sr=c['source_record'];wrapper=resolve(docs[sr['artifact']],sr['pointer']);pointer=wrapper.get('source_pointer',{});ref=primary_refs.get(pointer.get('source_id'))
 if not ref or ref['sha256']!=c['primary_source_sha256'] or not any(x.get('sha256')==ref['sha256'] for x in network_sources):
  fail.append({'closure':c,'reason':'primary_source_sha_not_shared'});continue
 if ref['path'] not in primary_cache:
  path=Path(ref['path']);primary_cache[ref['path']]=(hashlib.sha256(path.read_bytes()).hexdigest(),read(path))
 sha,primary=primary_cache[ref['path']]
 if sha!=ref['sha256'] or digest(resolve(primary,pointer['json_pointer']))!=digest(wrapper['record']):fail.append({'closure':c,'reason':'primary_record_not_exact'})
 if c['canonical_id'] not in nodes or nodes[c['canonical_id']]['original_id']!=c['original_id']:fail.append({'closure':c,'reason':'node_id_mismatch'})
record('cross_artifact_endpoint_closures_have_exact_primary_proof',fail,len(closures),'Closure uses existing source-exact records; it does not create new runtime entities or equate authored scenarios.')
report={'scope':'Independent code/data acceptance; browser rendering not asserted.','graph_sha256':hashlib.sha256((DATA/'graph.json').read_bytes()).hexdigest(),'counts':{'graph_nodes':len(nodes),'graph_edges':len(edges),'quarantined_edges':len(quarantine),'aliases':len(aliases),'merged_node_groups':len(merged),'independent_source_occurrences':len(expected)},'checks':checks,'all_data_checks_pass':all(c['status']=='pass' for c in checks),'elapsed_seconds':round(time.perf_counter()-start,3)}
output=Path(__file__).with_name('artifact-audit.json');output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='checks'},ensure_ascii=False,indent=2))
for c in checks:print(c['status'].upper(),c['name'],'covered=',c['covered'],'failures=',c['failures'])
raise SystemExit(0 if report['all_data_checks_pass'] else 1)
