"""Independent semantic contract acceptance, with explicit counterexamples."""
import copy, sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'spec'))
import contract

class ContractAcceptance(unittest.TestCase):
 def definition(self):
  return {'kind':'state_specification','semantic_level':'definition','semantic_identity':{'namespace':'example','id':'state.speed','revision':1,'scope':{'clock':'sim'},'definition':{'unit':'m/s','value_type':'number'}}}
 def test_definition_equal_explicit_semantics_can_merge(self):
  a=self.definition();self.assertTrue(contract.definition_merge_compatible(a,copy.deepcopy(a)))
 def test_definition_near_misses_never_merge(self):
  a=self.definition()
  changes=[('kind','fact'),('semantic_level','runtime_record')]
  for key,value in changes:
   b=copy.deepcopy(a);b[key]=value;self.assertFalse(contract.definition_merge_compatible(a,b),(key,value))
  for key,value in [('namespace','other'),('revision','1'),('scope',{'clock':'other'}),('definition',{'unit':'km/h','value_type':'number'}),('id','state.other')]:
   b=copy.deepcopy(a);b['semantic_identity'][key]=value;self.assertFalse(contract.definition_merge_compatible(a,b),(key,value))
 def test_missing_or_null_revision_never_proves_equivalence(self):
  for mode in ['missing','null']:
   a=self.definition()
   if mode=='missing':del a['semantic_identity']['revision']
   else:a['semantic_identity']['revision']=None
   self.assertFalse(contract.definition_merge_compatible(a,copy.deepcopy(a)))
 def test_absent_identity_never_proves_equivalence(self):
  for key in ['namespace','id','definition']:
   a=self.definition();a['semantic_identity'][key]=None
   self.assertFalse(contract.definition_merge_compatible(a,copy.deepcopy(a)),key)
 def test_json_values_are_type_strict(self):
  for a,b in [(1,True),(1,1.0),(1,'1'),({'a':False},{'a':0}),([1],['1']),({'a':None},{})]:
   self.assertFalse(contract.exact_json_equal(a,b))
 def test_exact_lifecycle_reference_includes_all_axes_without_coercion(self):
  a={'run_id':'run','epoch':0,'id':'x','generation':1,'ref_type':'fact'}
  baseline=contract.exact_ref_key(a);self.assertIsNotNone(baseline)
  for key in a:
   b=copy.deepcopy(a);del b[key];self.assertIsNone(contract.exact_ref_key(b))
  for key,value in [('run_id','other'),('epoch','0'),('id','y'),('generation','1'),('ref_type','entity')]:
   b=copy.deepcopy(a);b[key]=value;self.assertNotEqual(contract.exact_ref_key(b),baseline)
 def test_state_fact_and_definition_attempt_kinds_are_distinct(self):
  self.assertEqual(contract.canonical_kind(source_collection='fields'),'state_specification')
  self.assertEqual(contract.canonical_kind(source_collection='facts'),'fact')
  self.assertEqual(contract.canonical_kind('command_instance'),'command_attempt')
  self.assertEqual(contract.canonical_kind(source_collection='commands'),'command_definition')
 def test_argument_direction_profiles_are_not_casefolded(self):
  for domain in ['agriculture','city','network']:
   self.assertEqual(contract.canonical_relation('ARGUMENT','expression','expression',frozen_relation='ARGUMENT',artifact=domain+'/source.json'),'argument_of')
  self.assertEqual(contract.canonical_relation('ARGUMENT','expression','expression',frozen_relation='ARGUMENT',artifact='delivery/source.json'),'has_argument')
  self.assertEqual(contract.canonical_relation('ARGUMENT','expression','expression',frozen_relation='ARGUMENT',artifact='unknown/source.json'),'unresolved:argument_direction')
 def test_runtime_and_definition_admission_relations_differ(self):
  self.assertEqual(contract.canonical_relation('permits_or_blocks',target_kind='behavior_definition'),'declares_admission_for_behavior')
  self.assertEqual(contract.canonical_relation('permits_or_blocks',target_kind='behavior_execution'),'permits_or_blocks')
 def test_unknown_relation_is_not_truth_or_permission(self):
  self.assertEqual(contract.canonical_relation('new_native_operator',frozen_relation='new_native_operator'),'unresolved:new_native_operator')

if __name__=='__main__':unittest.main(verbosity=2)
