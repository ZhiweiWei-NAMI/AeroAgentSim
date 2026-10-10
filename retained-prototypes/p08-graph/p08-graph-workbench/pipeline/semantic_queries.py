"""Offline selection and three-valued truth helpers, not native rule evaluation.

These functions never change observations or infer a value from a receipt,
permission, definition, missing field, null, stale fact, or unknown clock.
"""
from enum import Enum
import re
from import_catalogs import CONTRACT

class Truth(str, Enum):
 TRUE='TRUE'
 FALSE='FALSE'
 UNKNOWN='UNKNOWN'

def truth(value):
 if value is True or value in ('TRUE','True','true'):return Truth.TRUE
 if value is False or value in ('FALSE','False','false'):return Truth.FALSE
 return Truth.UNKNOWN

def conjunction(values):
 vv=[truth(x) for x in values]
 if Truth.FALSE in vv:return Truth.FALSE
 if not vv or Truth.UNKNOWN in vv:return Truth.UNKNOWN
 return Truth.TRUE

def disjunction(values):
 vv=[truth(x) for x in values]
 if Truth.TRUE in vv:return Truth.TRUE
 if not vv or Truth.UNKNOWN in vv:return Truth.UNKNOWN
 return Truth.FALSE

def negate(value):
 v=truth(value)
 return Truth.FALSE if v==Truth.TRUE else Truth.TRUE if v==Truth.FALSE else Truth.UNKNOWN

def ns(value):
 # No floating point conversion of nanoseconds, no bool->1 and no seconds guess.
 if isinstance(value,bool):return None
 if isinstance(value,int):return value
 if isinstance(value,str) and re.fullmatch(r'-?[0-9]+',value):return int(value)
 return None

def select_fact(facts, *, subject, field, valid_at_ns, available_at_ns,
                clock_domain, availability_domain, exact_identity=None):
 """Select only explicit, available, valid exact-subject facts, without evaluation.

 Missing interval ends are never interpreted as infinite freshness. Conflicting
 simultaneous values yield UNKNOWN; input order never establishes precedence.
 """
 valid_at=ns(valid_at_ns);available_at=ns(available_at_ns)
 if valid_at is None or available_at is None or not clock_domain or not availability_domain:
  return {'status':'UNKNOWN','value':None,'reason':'missing_query_time_or_clock','fact_ids':[]}
 identity_key=CONTRACT.exact_ref_key(exact_identity) if exact_identity is not None and CONTRACT else None
 if exact_identity is not None and identity_key is None:
  return {'status':'UNKNOWN','value':None,'reason':'incomplete_exact_identity','fact_ids':[]}
 candidates=[];reasons=[]
 for fact in facts:
  raw=fact.get('payload',fact)
  raw=raw.get('record',raw)
  props=raw.get('properties',{});f={**raw,**props}
  subj=f.get('entity_id',f.get('subject_ref',f.get('subject')))
  if isinstance(subj,dict):subj=subj.get('id')
  fld=f.get('field_id',f.get('field_ref'))
  if isinstance(fld,dict):fld=fld.get('id')
  if subj!=subject or fld!=field:continue
  if f.get('clock_domain')!=clock_domain or f.get('availability_domain')!=availability_domain:
   reasons.append('missing_or_different_clock');continue
  if exact_identity is not None:
   identity=f.get('identity',f.get('ref',{}))
   if CONTRACT.exact_ref_key(identity)!=identity_key:
    reasons.append('different_or_missing_identity_generation');continue
  start=ns(f.get('valid_time_ns',f.get('valid_from_ns')));end=ns(f.get('valid_until_ns',f.get('valid_to_ns')));available=ns(f.get('available_time_ns'))
  if start is None or end is None or available is None:
   reasons.append('missing_time_or_expiration');continue
  if not start<=valid_at<end:reasons.append('not_valid_at_query');continue
  if available>available_at:reasons.append('not_available_at_cutoff');continue
  if 'value' not in f or f['value'] is None:reasons.append('missing_value');continue
  candidates.append((fact.get('id',raw.get('id')),f['value']))
 if not candidates:return {'status':'UNKNOWN','value':None,'reason':sorted(set(reasons)) or ['no_fact'],'fact_ids':[]}
 # Values must agree with exact JSON types; True is not 1.
 value=candidates[0][1]
 import json
 encoded=json.dumps(value,sort_keys=True,separators=(',',':'))
 if any(type(v) is not type(value) or json.dumps(v,sort_keys=True,separators=(',',':'))!=encoded for _,v in candidates):
  return {'status':'UNKNOWN','value':None,'reason':'conflicting_facts_no_last_arrival_wins','fact_ids':[i for i,_ in candidates]}
 return {'status':'AVAILABLE_VALUE','value':value,'reason':'explicit_time_and_scope_match_not_native_truth','fact_ids':[i for i,_ in candidates]}
