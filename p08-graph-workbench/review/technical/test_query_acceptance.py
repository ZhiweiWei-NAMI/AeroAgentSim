import unittest,sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline'))
import semantic_queries as q
class QueryAcceptance(unittest.TestCase):
 def fact(self,**updates):
  f={'id':'f1','entity_id':'e','field_id':'speed','value':False,'clock_domain':'sim','availability_domain':'recorded','valid_time_ns':'0','valid_until_ns':'100','available_time_ns':'5','identity':{'run_id':'r','epoch':0,'id':'f1','generation':1,'ref_type':'fact'}};f.update(updates);return f
 def select(self,facts,**updates):
  args={'subject':'e','field':'speed','valid_at_ns':'10','available_at_ns':'10','clock_domain':'sim','availability_domain':'recorded'};args.update(updates);return q.select_fact(facts,**args)
 def test_missing_null_unknown_are_not_false(self):
  for v in [None,'UNKNOWN',0,1,'',{},[]]:self.assertEqual(q.truth(v),q.Truth.UNKNOWN)
  self.assertEqual(q.truth(False),q.Truth.FALSE)
 def test_exact_nanoseconds_never_float(self):
  self.assertEqual(q.ns('9007199254740993'),9007199254740993)
  for v in [True,1.5,'1.5','1e3',None]:self.assertIsNone(q.ns(v))
 def test_malformed_time_produces_unknown_without_exception(self):
  for v in ['--1','²','-²','--0']:
   self.assertIsNone(q.ns(v),v)
 def test_explicit_false_value_survives_without_becoming_unknown(self):
  s=self.select([self.fact()]);self.assertEqual(s['status'],'AVAILABLE_VALUE');self.assertIs(s['value'],False)
 def test_missing_value_end_or_clock_is_unknown(self):
  for k in ['value','valid_until_ns','clock_domain','availability_domain','available_time_ns']:
   f=self.fact();del f[k];self.assertEqual(self.select([f])['status'],'UNKNOWN',k)
 def test_stale_future_and_late_arrivals_are_unknown(self):
  for f in [self.fact(valid_until_ns='10'),self.fact(valid_time_ns='11'),self.fact(available_time_ns='11')]:self.assertEqual(self.select([f])['status'],'UNKNOWN')
 def test_conflicting_type_values_never_last_arrival_win(self):
  a=self.fact(value=True);b=self.fact(id='f2',value=1)
  for facts in [[a,b],[b,a]]:self.assertEqual(self.select(facts)['status'],'UNKNOWN')
 def test_generation_and_epoch_type_changes_do_not_match(self):
  identity=self.fact()['identity']
  for k,v in [('generation','1'),('epoch','0'),('run_id','other')]:
   f=self.fact();f['identity'][k]=v;self.assertEqual(self.select([f],exact_identity=identity)['status'],'UNKNOWN')
 def test_complete_exact_ref_required_for_exact_identity_query(self):
  self.assertEqual(self.select([self.fact()],exact_identity={})['status'],'UNKNOWN')
  self.assertEqual(self.select([self.fact()],exact_identity={'id':'f1'})['status'],'UNKNOWN')
 def test_invalid_exact_reference_is_unknown_even_if_both_sides_match(self):
  for key,value in [('run_id',''),('epoch',False),('generation',{}),('generation',-1),('ref_type','')]:
   f=self.fact();f['identity'][key]=value
   self.assertEqual(self.select([f],exact_identity=f['identity'])['status'],'UNKNOWN',(key,value))
 def test_unknown_conjunction_does_not_assert_truth(self):
  self.assertEqual(q.conjunction([True,None]),q.Truth.UNKNOWN);self.assertEqual(q.disjunction([False,None]),q.Truth.UNKNOWN);self.assertEqual(q.negate(None),q.Truth.UNKNOWN)
if __name__=='__main__':unittest.main(verbosity=2)
