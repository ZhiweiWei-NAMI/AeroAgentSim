from pathlib import Path
from collections import Counter
import json,time,hashlib
from jsonschema import Draft202012Validator
root=Path(__file__).resolve().parents[2];start=time.perf_counter();schema=json.loads((root/'spec/p08-graph.schema.json').read_text());graph=json.loads((root/'data/graph.json').read_text());Draft202012Validator.check_schema(schema)
errors=[];counts=Counter()
for err in Draft202012Validator(schema).iter_errors(graph):
 key='/'.join(str(x) for x in err.path);counts[err.validator]+=1
 if len(errors)<50:errors.append({'path':key,'error':err.message,'schema_path':'/'.join(str(x) for x in err.schema_path)})
report={'graph_sha256':hashlib.sha256((root/'data/graph.json').read_bytes()).hexdigest(),'schema_sha256':hashlib.sha256((root/'spec/p08-graph.schema.json').read_bytes()).hexdigest(),'schema':'spec/p08-graph.schema.json','nodes':len(graph['nodes']),'edges':len(graph['edges']),'error_count':sum(counts.values()),'errors_by_validator':dict(counts),'examples':errors,'elapsed_seconds':round(time.perf_counter()-start,3)}
(root/'review/technical/schema-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(bool(counts))
