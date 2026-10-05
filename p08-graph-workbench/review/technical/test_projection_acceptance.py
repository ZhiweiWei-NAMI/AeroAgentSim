import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline'))
from import_catalogs import semantics_projection
class ProjectionAcceptance(unittest.TestCase):
 def test_json_null_is_structural_not_semantic_unknown(self):
  p=semantics_projection({'value':None},'fact');self.assertIs(p['literal']['is_null'],True);self.assertNotIn('is_unknown',p['literal']);self.assertIsNone(p['literal']['value']);self.assertEqual(p['literal']['json_type'],'null')
 def test_false_and_unknown_string_are_distinct_from_null(self):
  for value in [False,0,'UNKNOWN',[],{}]:
   p=semantics_projection({'value':value},'fact');self.assertIs(p['literal']['is_null'],False);self.assertNotIn('is_unknown',p['literal']);self.assertEqual(type(p['literal']['value']),type(value))
 def test_missing_value_is_not_fabricated_null(self):
  p=semantics_projection({'truth':'UNKNOWN'},'fact');self.assertNotIn('literal',p);self.assertEqual(p['declared']['truth'],'UNKNOWN')
if __name__=='__main__':unittest.main(verbosity=2)
