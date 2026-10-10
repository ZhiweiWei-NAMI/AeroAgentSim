"""Independent source denominator. Never imports the production pipeline."""
from pathlib import Path
import hashlib, json
STAGE1=Path('/workspace/shared/p02_stage1')
FILES={
 'agriculture': 'agriculture/agriculture-instances.json',
 'city': 'city/city-activity-instances.json',
 'delivery': 'delivery/delivery-activity-instances.json',
 'network': 'network/network-instance-catalog.json',
}
def digest(x):
 return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def resolve(doc,pointer):
 if not pointer:return doc
 assert pointer.startswith('/'),pointer
 for token in pointer[1:].split('/'):
  token=token.replace('~1','/').replace('~0','~')
  doc=doc[int(token)] if isinstance(doc,list) else doc[token]
 return doc
def occurrences():
 out=[]
 def add(domain,doc,pointer,kind,case):
  raw=resolve(doc,pointer)
  out.append(dict(domain=domain,artifact=FILES[domain],pointer=pointer,record_kind=kind,case=case,source_id=raw.get('id'),digest=digest(raw),raw=raw))
 def arr(domain,doc,pointer,kind,case):
  for i in range(len(resolve(doc,pointer))):add(domain,doc,pointer+'/'+str(i),kind,case)
 for domain,file in FILES.items():
  doc=json.loads((STAGE1/file).read_text())
  if domain=='agriculture':
   arr(domain,doc,'/contract_reference_nodes','node','contract_references')
   for i,case in enumerate(doc['activities']):
    p='/activities/'+str(i);cid=case['activity_index']
    for key,kind in [('shared_nodes','node'),('shared_edges','edge'),('proposed_runtime_records','node'),('proposed_runtime_edges','edge')]:
     arr(domain,doc,p+'/'+key,kind,cid)
    for j,step in enumerate(case['steps']):
     for key,kind in [('nodes','node'),('edges','edge')]:arr(domain,doc,p+'/steps/'+str(j)+'/'+key,kind,cid)
  elif domain=='city':
   for i,case in enumerate(doc['workflow_instances']):
    p='/workflow_instances/'+str(i);cid=case['workflow_id']
    for k in case['collections']:arr(domain,doc,p+'/collections/'+k,'node',cid)
    for key,kind in [('edges','edge'),('supplemental_records','node'),('runtime_edges','edge')]:arr(domain,doc,p+'/'+key,kind,cid)
   for i,case in enumerate(doc['crosscutting_city_scenarios']):
    for key,kind in [('nodes','node'),('edges','edge')]:arr(domain,doc,'/crosscutting_city_scenarios/'+str(i)+'/'+key,kind,case['id'])
   for k in doc['source_fixture']['collections']:arr(domain,doc,'/source_fixture/collections/'+k,'node','source_fixture')
  elif domain=='delivery':
   for i,case in enumerate(doc['scenarios']):
    for key,kind in [('nodes','node'),('edges','edge'),('runtime_records','node'),('runtime_edges','edge')]:arr(domain,doc,'/scenarios/'+str(i)+'/'+key,kind,case['scenario_id'])
  elif domain=='network':
   arr(domain,doc,'/source_declaration_occurrences','node','source_declarations')
   for key in ['source_backed_activity_cases','authored_supplementary_activity_cases','original_activity_cases','machine_activity_cases']:
    for i,case in enumerate(doc[key]):
     for nk,kind in [('source_occurrences' if key=='source_backed_activity_cases' else 'nodes','node'),('relation_occurrences','edge')]:arr(domain,doc,'/'+key+'/'+str(i)+'/'+nk,kind,case['id'])
 return out
if __name__=='__main__':
 from collections import Counter
 records=occurrences()
 data={'authority':'independent accepted stage-one occurrence traversal','source_files':[{ 'artifact':f,'sha256':hashlib.sha256((STAGE1/f).read_bytes()).hexdigest()} for f in FILES.values()], 'counts':dict(Counter(x['record_kind'] for x in records)), 'domain_counts':{d:dict(Counter(x['record_kind'] for x in records if x['domain']==d)) for d in FILES},'occurrences':[{k:v for k,v in r.items() if k!='raw'} for r in records]}
 path=Path(__file__).with_name('source-denominator.json');path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n'); print(json.dumps({k:v for k,v in data.items() if k!='occurrences'},indent=2))
