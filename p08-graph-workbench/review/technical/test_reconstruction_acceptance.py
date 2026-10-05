import unittest,sys,json,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pipeline'))
from reconstruct import recover_artifact,recover_occurrence
from import_catalogs import digest
class ReconstructionAcceptance(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'inputs').mkdir();self.doc={'a/b':{'~key':[False,None,{'generation':'1','timestamp_ns':'9007199254740993','args':[1,0,False]}]}};self.raw=(json.dumps(self.doc,indent=2)+'\n').encode();self.path=self.root/'inputs/a.json';self.path.write_bytes(self.raw);self.sha=hashlib.sha256(self.raw).hexdigest();(self.root/'input-manifest.json').write_text(json.dumps({'inputs':[{'artifact':'a.json','path':'inputs/a.json','sha256':self.sha}]}))
 def tearDown(self):self.tmp.cleanup()
 def alias(self,pointer,obj):return {'source_record':{'artifact':'a.json','pointer':pointer,'sha256':self.sha},'raw_sha256':digest(obj)}
 def test_recover_artifact_is_byte_exact(self):self.assertEqual(recover_artifact('a.json',self.root),self.raw)
 def test_pointer_escaping_and_ordered_literal_types_roundtrip(self):
  val=self.doc['a/b']['~key'][2];self.assertEqual(recover_occurrence(self.alias('/a~1b/~0key/2',val),self.root),val)
 def test_explicit_null_and_false_are_distinct(self):
  self.assertIs(recover_occurrence(self.alias('/a~1b/~0key/0',False),self.root),False);self.assertIsNone(recover_occurrence(self.alias('/a~1b/~0key/1',None),self.root))
 def test_tampered_snapshot_is_rejected(self):
  self.path.write_bytes(self.raw+b' ')
  with self.assertRaises(ValueError):recover_artifact('a.json',self.root)
 def test_tampered_alias_digest_is_rejected(self):
  with self.assertRaises(ValueError):recover_occurrence(self.alias('/a~1b/~0key/0',True),self.root)
 def test_alias_snapshot_hash_is_verified(self):
  alias=self.alias('/a~1b/~0key/0',False);alias['source_record']['sha256']='0'*64
  with self.assertRaises(ValueError):recover_occurrence(alias,self.root)
 def test_missing_pointer_is_rejected(self):
  with self.assertRaises((KeyError,IndexError)):recover_occurrence(self.alias('/does_not_exist',None),self.root)
if __name__=='__main__':unittest.main(verbosity=2)
