import copy,json,sys,unittest
from pathlib import Path
from jsonschema import Draft202012Validator
from contract import *
P=Path(__file__).resolve().parent
class ContractTests(unittest.TestCase):
 def test_exact_runtime_ref(self):
  r={'run_id':'r','epoch':0,'id':'a.b','generation':1,'ref_type':'entity'}
  self.assertIsNotNone(exact_ref_key(r))
  for field in r:
   bad=dict(r);bad[field]=None;self.assertIsNone(exact_ref_key(bad))
  for val in [False,True,1.0,[],{},-1,'']:
   bad=dict(r,generation=val);self.assertIsNone(exact_ref_key(bad))
  self.assertNotEqual(exact_ref_key(r),exact_ref_key(dict(r,generation='1')))
 def test_definition_scope_never_guessed(self):
  n={'kind':'rule_definition','semantic_level':'definition','semantic_identity':{'namespace':'test','id':'r','revision':1,'scope':{'global':True},'definition':{'ast':False}}}
  self.assertTrue(definition_merge_compatible(n,copy.deepcopy(n)))
  for field in ['namespace','id','revision','scope','definition']:
   for v in [None,'']:
    b=copy.deepcopy(n);b['semantic_identity'][field]=v;self.assertFalse(definition_merge_compatible(b,copy.deepcopy(b)))
 def test_aliases_only_runtime(self):
  for k in ['command','command_instance','command_attempt']:self.assertEqual(canonical_kind(k),'command_attempt')
  self.assertEqual(canonical_kind('command',source_collection='commands'),'command_definition')
  self.assertNotEqual(canonical_kind('command_lifecycle_receipt'),canonical_kind('command_receipt'))
  self.assertNotEqual(canonical_kind('check_result'),canonical_kind('constraint_check_result'))
  self.assertNotEqual(canonical_kind('predicate_result'),canonical_kind(source_collection='predicate_results'))
 def test_city_meta_collection(self):
  self.assertEqual(canonical_kind('fields',source_collection='node_types'),'node_type')
 def test_all_source_kind_mappings(self):
  m=json.loads((P/'migration-mappings.json').read_text())
  rt=[x for x in m['node_mappings'] if x['namespace']=='proposed_runtime_kind'];self.assertEqual(len(rt),42);self.assertEqual(len({x['target'] for x in rt}),39)
  self.assertEqual(sum(x['occurrences'] for x in rt),4446)
 def test_all_relation_names_accounted(self):
  r=json.loads((P/'relation-registry.json').read_text())['variants']
  self.assertEqual(len({x['raw_relation'] for x in r if x['source_namespace']=='frozen'}),92)
  self.assertEqual(len({x['raw_relation'] for x in r if x['source_namespace']=='runtime'}),69)
  self.assertEqual(sum(x.get('occurrence_count',0) for x in r if x['source_namespace']=='runtime'),12157)
  self.assertEqual(len({x['id'] for x in r}),len(r))
 def test_argument_direction(self):
  for a,wanted in [('agriculture','argument_of'),('city','argument_of'),('network','argument_of'),('delivery','has_argument')]:self.assertEqual(canonical_relation('ARGUMENT','expression','expression',frozen_relation='ARGUMENT',artifact=a+'/catalog.json'),wanted)
 def test_legacy_field_not_class_cast(self):
  kw={'source_collection':'admission_checks','target_collection':'fields','frozen_relation':'INSTANCE_OF'}
  self.assertEqual(canonical_relation('INSTANCE_OF',role='state_field',**kw),'legacy_field_reference')
  self.assertEqual(canonical_relation('INSTANCE_OF',role='wrong_role',**kw),'unresolved:instance_of_signature')
 def test_module_execution_not_definition_cast(self):
  self.assertEqual(canonical_relation('instance_of','module_execution','modules',target_collection='modules'),'execution_of_module')
 def test_schema_documents_well_formed(self):
  for f in P.glob('*.schema.json'):Draft202012Validator.check_schema(json.loads(f.read_text()))
 def test_native_ast_all_forms_lossless(self):
  ast={'op':'unverified_native_op','args':[True,1,1.0,None,{'op':'literal','value':'x'},{'op':'unknown'},{'predicate':'exact.target','bindings':{'actor':'A'}},{'state':'exact.field'},{'param':'exact.param'},{'const':False}], 'window_s':1,'duration_seconds':2,'max_gap_s':0.25,'scope':{'unverified':'preserve'},'enter_s':0,'clear_s':3,'unrecognized_property':{'nested':[2,1]}}
  env={'source_pointer':'/targets/0/rule','root_role':'main','native_source_ast':ast,'native_compatibility':'unverified'}
  Draft202012Validator(json.loads((P/'ast-preservation.schema.json').read_text())).validate(env)
  clone=json.loads(json.dumps(env));self.assertTrue(exact_json_equal(env,clone));clone['native_source_ast']['args'].reverse();self.assertFalse(exact_json_equal(env,clone))
 def test_null_fact_value_not_missing_slot(self):
  schema=json.loads((P/'semantic-facets.schema.json').read_text())
  body=next(x['then']['properties']['facets'] for x in schema['allOf'] if x['if']['properties']['kind']['const']=='fact')
  f={k:{'status':'missing','reason':'source unavailable'} for k in body['required']};f['value']={'status':'known','value':None}
  Draft202012Validator(schema).validate({'kind':'fact','facets':f})
  del f['value']['value'];self.assertTrue(list(Draft202012Validator(schema).iter_errors({'kind':'fact','facets':f})))
if __name__=='__main__':unittest.main(verbosity=2)
