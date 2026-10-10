"""Fast static tests over every imported occurrence; no simulator or native run."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from import_catalogs import at_pointer,digest,INPUTS
from reconstruct import recover_artifact
from semantic_queries import Truth,conjunction,disjunction,negate,select_fact,truth,ns

DATA=Path(__file__).resolve().parents[1]/'data'
class PipelineTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.graph=json.loads((DATA/'graph.json').read_text());cls.aliases=json.loads((DATA/'aliases.json').read_text())['aliases'];cls.manifest=json.loads((DATA/'manifest.json').read_text());cls.nodes={n['id']:n for n in cls.graph['nodes']};cls.edges={e['id']:e for e in cls.graph['edges']};cls.inputs=json.loads((DATA/'input-manifest.json').read_text())['inputs'];cls.docs={x['artifact']:json.loads((DATA/x['path']).read_text()) for x in cls.inputs}
 def test_01_all_original_input_bytes_immutable(self):
  for m in self.inputs:
   data=recover_artifact(m['artifact'],DATA)
   self.assertEqual(hashlib.sha256(data).hexdigest(),m['sha256'])
   self.assertEqual(len(data),m['bytes'])
   original=Path('/workspace/shared/p02_stage1')/m['artifact']
   if original.exists():self.assertEqual(data,original.read_bytes())
 def test_02_every_occurrence_roundtrips_exactly(self):
  for a in self.aliases:
   sr=a['source_record'];raw=at_pointer(self.docs[sr['artifact']],sr['pointer']);canonical=(self.nodes if a['record_type']=='node' else self.edges)[a['canonical_id']]
   self.assertEqual(digest(raw),a['raw_sha256'])
   self.assertEqual(digest(raw),digest(canonical['payload']))
   self.assertIn(sr,canonical['source_records'])
 def test_03_complete_four_catalog_denominator(self):
  primary={INPUTS[x] for x in ['agriculture','delivery','city','network']};rows=[a for a in self.aliases if a['source_record']['artifact'] in primary]
  self.assertEqual(sum(a['record_type']=='node' for a in rows),20004)
  self.assertEqual(sum(a['record_type']=='edge' for a in rows),63870)
  self.assertEqual(len({(a['source_record']['artifact'],a['source_record']['pointer']) for a in rows}),83874)
  self.assertEqual(len(self.graph['nodes']),20201);self.assertEqual(len(self.graph['edges']),64978)
 def test_04_exact_35_activities_226_steps(self):
  coverage=self.graph['coverage'];catalog=self.docs[INPUTS['source_catalog']]
  expected={(a['catalog_index'],i+1,s) for a in catalog['activities'] for i,s in enumerate(a['steps'])}
  actual={(x['activity_index'],x['step_index'],x['source_step']) for x in coverage}
  self.assertEqual(expected,actual);self.assertEqual(len(coverage),226)
  for x in coverage:
   self.assertTrue(x['source_match']);self.assertTrue(x['node_ids']);self.assertFalse(x['execution_verified'])
   self.assertEqual(at_pointer(catalog,x['source_catalog_pointer']),x['imported_step'])
   self.assertTrue(set(x['node_ids'])<=self.nodes.keys())
 def test_05_exact_twenty_machine_examples(self):
  machine=[s for s in self.manifest['scenarios'] if s['id'].startswith('MI')]
  self.assertEqual({s['id'] for s in machine},{f'MI{i:02d}' for i in range(1,21)})
  for s in machine:self.assertGreater(s['node_count'],0);self.assertGreater(s['edge_count'],0)
 def test_06_unique_stable_ids_and_no_orphan_endpoints(self):
  self.assertEqual(len(self.nodes),len(self.graph['nodes']));self.assertEqual(len(self.edges),len(self.graph['edges']))
  for n in self.nodes.values():
   if len(n['source_records'])==1 and 'semantic_identity' not in n:
    s=n['source_records'][0];self.assertEqual(n['id'],'n:'+digest(['occurrence',s['artifact'],s['pointer']])[:24])
  for e in self.edges.values():
   self.assertIn(e['source'],self.nodes);self.assertIn(e['target'],self.nodes)
   self.assertEqual(e['source_kind'],self.nodes[e['source']]['kind']);self.assertEqual(e['target_kind'],self.nodes[e['target']]['kind'])
   s=e['source_records'][0];self.assertEqual(e['id'],'e:'+digest([s['artifact'],s['pointer']])[:24])
  self.assertEqual(self.graph['unsupported']['unresolved_edges'],[])
 def test_07_direction_role_condition_order_not_collapsed(self):
  lookup={(n['source_records'][0]['case_id'],n['original_id']):n['id'] for n in self.nodes.values()}
  parallel={}
  for e in self.edges.values():
   p=e['payload'];src=p.get('source',p.get('source_record_ref'));tgt=p.get('target',p.get('target_record_ref'));src=src.get('id') if isinstance(src,dict) else src;tgt=tgt.get('id') if isinstance(tgt,dict) else tgt
   self.assertEqual(self.nodes[e['source']]['original_id'],src);self.assertEqual(self.nodes[e['target']]['original_id'],tgt);self.assertEqual(e['role'],p.get('role',''));self.assertEqual(e['condition'],p.get('condition'));self.assertEqual(e['order'],p.get('order'))
   key=(e['source'],e['target']);parallel.setdefault(key,[]).append(e['id'])
  self.assertTrue(any(len(v)>1 for v in parallel.values()))
  self.assertEqual(self.graph['counts']['deduplicated_edges'],0)
 def test_08_no_unproven_or_runtime_merge(self):
  for n in self.nodes.values():
   if len(n['source_records'])>1:
    self.assertEqual(n['semantic_level'],'definition');self.assertIn('semantic_identity',n);self.assertIsNotNone(n['semantic_identity']['revision']);self.assertIsNotNone(n['semantic_identity']['scope'])
  # Repeated native-looking IDs from different source contexts stay distinguishable.
  shared=[n for n in self.nodes.values() if n['original_id']=='entity-type-type'];self.assertGreater(len(shared),1)
 def test_09_typed_literals_units_time_and_identity_preserved(self):
  for n in self.nodes.values():
   raw=n['payload'].get('record',n['payload']);p=raw.get('properties',{});source={**raw,**p} if isinstance(p,dict) else raw;sem=n['semantics']
   if 'value' in source:
    self.assertEqual(digest(sem['literal']['value']),digest(source['value']))
    self.assertEqual(sem['literal']['unit'],source.get('unit'));self.assertEqual(sem['literal']['quantity'],source.get('quantity'));self.assertEqual(sem['literal']['frame'],source.get('frame'))
    self.assertEqual(sem['literal']['is_null'],source['value'] is None)
    self.assertNotIn('is_unknown',sem['literal'])
   for k,v in sem['time'].items():self.assertEqual(digest(v),digest(source[k]))
   for k,v in sem['declared'].items():self.assertEqual(digest(v),digest(source[k]))
 def test_10_continuous_same_entity_different_facts_and_generations(self):
  facts=[n for n in self.nodes.values() if n['kind']=='fact'];subjects={}
  for n in facts:
   r=n['payload'].get('record',n['payload']);r={**r,**r.get('properties',{})};s=r.get('entity_id',r.get('subject_ref'))
   if s is not None:subjects.setdefault(digest(s),[]).append(n['id'])
  self.assertTrue(any(len(ids)>3 for ids in subjects.values()))
  mi04=[n for n in facts if n['original_id']=='MI04.conflict.context_fact01'][0]['payload']['properties']['value']
  self.assertEqual(mi04['received_close_exact_ref']['generation'],1);self.assertEqual(mi04['protocol_alias_binding']['exact_entity_ref']['generation'],2)
 def test_11_argument_orientation_profiles_preserved(self):
  aa=[e for e in self.edges.values() if e['raw_relation']=='ARGUMENT']
  self.assertTrue(aa)
  for e in aa:self.assertEqual(e['relation'],'has_argument' if e['source_records'][0]['artifact'].startswith('delivery/') else 'argument_of')
 def test_12_no_manufactured_execution_truth(self):
  self.assertEqual(self.graph['status'],'static_authored_fixture_graph_not_executed')
  for n in self.nodes.values():self.assertEqual(n['semantics']['execution_status'],'not_executed_by_importer')
  for s in self.manifest['scenarios']:self.assertEqual(s['execution_status'],'not_executed_by_importer')
 def test_13_source_closure_uses_hash_evidence(self):
  closures=json.loads((DATA/'endpoint-closures.json').read_text())['closures'];self.assertEqual(len(closures),63)
  net=next(s for s in self.docs[INPUTS['network']]['sources'] if s['id']=='S01');dlv=next(s for s in self.docs[INPUTS['delivery_source']]['source_manifest'] if s['id']=='emergency_config')
  self.assertEqual(net['sha256'],dlv['sha256'])
  for c in closures:self.assertEqual(c['primary_source_sha256'],net['sha256']);self.assertIn(c['canonical_id'],self.nodes)

class UnknownAndTimeTests(unittest.TestCase):
 def test_truth_does_not_coerce_null_zero_or_conflict(self):
  for v in [None,0,1,{},[], 'UNKNOWN','CONFLICT','not_executed']:self.assertEqual(truth(v),Truth.UNKNOWN)
  self.assertEqual(conjunction([True,None]),Truth.UNKNOWN);self.assertEqual(conjunction([False,None]),Truth.FALSE)
  self.assertEqual(disjunction([False,None]),Truth.UNKNOWN);self.assertEqual(disjunction([True,None]),Truth.TRUE);self.assertEqual(negate(None),Truth.UNKNOWN)
 def test_nanosecond_precision_and_boolean_rejection(self):
  self.assertEqual(ns('9007199254740993'),9007199254740993);self.assertIsNone(ns(0.5));self.assertIsNone(ns(True))
 def test_timed_facts_unknown_conflict_and_generation(self):
  f={'id':'f1','entity_id':'subject','field_id':'field','value':4,'valid_time_ns':'10','valid_until_ns':'20','available_time_ns':'12','clock_domain':'sim','availability_domain':'commit','identity':{'run_id':'r','epoch':0,'id':'subject','generation':1,'ref_type':'entity'}}
  q={'subject':'subject','field':'field','valid_at_ns':'15','available_at_ns':'15','clock_domain':'sim','availability_domain':'commit','exact_identity':{'run_id':'r','epoch':0,'id':'subject','generation':1,'ref_type':'entity'}}
  self.assertEqual(select_fact([f],**q)['value'],4)
  self.assertEqual(select_fact([f],**{**q,'available_at_ns':'11'})['status'],'UNKNOWN')
  self.assertEqual(select_fact([f],**{**q,'valid_at_ns':'20'})['status'],'UNKNOWN')
  self.assertEqual(select_fact([f],**{**q,'clock_domain':'wall'})['status'],'UNKNOWN')
  self.assertEqual(select_fact([f],**{**q,'exact_identity':{'generation':2}})['status'],'UNKNOWN')
  self.assertEqual(select_fact([{**f,'valid_until_ns':None}],**q)['status'],'UNKNOWN')
  self.assertEqual(select_fact([{**f,'value':None}],**q)['status'],'UNKNOWN')
  conflict={**f,'id':'f2','value':5}
  self.assertEqual(select_fact([f,conflict],**q),select_fact([conflict,f],**q)|{'fact_ids':['f1','f2']})
  self.assertEqual(select_fact([f,conflict],**q)['status'],'UNKNOWN')

if __name__=='__main__':unittest.main(verbosity=2)
