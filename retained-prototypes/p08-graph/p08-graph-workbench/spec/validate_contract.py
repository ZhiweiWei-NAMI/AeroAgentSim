"""Offline P08 graph contract validation. No rule evaluation or physical claims.

The complete schema is checked once; per-node level conditions use the identical
registry in O(1), avoiding an O(nodes*kinds) conditional scan. Scope-dependent
semantic requirements remain explicit readiness gaps, not invented source data.
"""
from pathlib import Path
import argparse,collections,copy,hashlib,json,re,math
from jsonschema import Draft202012Validator
from contract import edge_level, exact_ref_key
HERE=Path(__file__).resolve().parent

def validate(graph):
    schema=json.loads((HERE/'p08-graph.schema.json').read_text())
    kinds=json.loads((HERE/'node-type-registry.json').read_text())['kinds']
    relations=json.loads((HERE/'relation-registry.json').read_text())['variants']
    Draft202012Validator.check_schema(schema)
    # This is equivalent to the exported schema's conditional kind/level checks.
    per_record=copy.deepcopy(schema['$defs']);per_record['node'].pop('allOf',None)
    # json.loads has already constrained data to JSON primitives/containers.
    # Validate finite numbers once below rather than recursively re-running an
    # anyOf grammar for every preserved source value in a 100+ MB payload.
    per_record['json_value']={}
    nv=Draft202012Validator({'$ref':'#/$defs/node','$defs':per_record})
    ev=Draft202012Validator({'$ref':'#/$defs/edge','$defs':per_record})
    header=copy.deepcopy(schema);header['properties']['nodes']={'type':'array'};header['properties']['edges']={'type':'array'}
    errors=[];gaps=[];warnings=[];nodes={};edges={};levels={x['kind']:set(x['semantic_levels']) for x in kinds};rv=collections.defaultdict(list)
    for x in relations:rv[x['relation']].append(x)
    def issue(code,record_id,message,path=None):return {'code':code,'record_id':record_id,'message':message,'path':path}
    stack=[graph]
    while stack:
        value=stack.pop()
        if isinstance(value,dict): stack.extend(value.values())
        elif isinstance(value,list): stack.extend(value)
        elif isinstance(value,float) and not math.isfinite(value): errors.append(issue('NONFINITE_JSON_NUMBER',None,'JSON numbers must be finite; preserve unsupported representation as explicit source text.'))
    for e in Draft202012Validator(header).iter_errors(graph):errors.append(issue('GRAPH_SHAPE',None,e.message,list(e.path)))
    for i,n in enumerate(graph.get('nodes',[])):
        for e in nv.iter_errors(n):errors.append(issue('NODE_SHAPE',n.get('id'),e.message,['nodes',i]+list(e.path)))
        nid=n.get('id')
        if nid in nodes:errors.append(issue('DUPLICATE_NODE_ID',nid,'Storage node IDs must be unique.'))
        nodes[nid]=n
        if n.get('semantic_level') not in levels.get(n.get('kind'),set()):errors.append(issue('NODE_LEVEL',nid,'Canonical kind and semantic level do not match the registry.'))
        # Exact lifecycle identity is optional for authoring; malformed present
        # claims are readiness gaps. Never compare nulls as an identity proof.
        decl=n.get('identity',{}).get('declared',{})
        for key,value in decl.items():
            if isinstance(value,dict) and set(('run_id','epoch','id','generation','ref_type')) <= set(value) and exact_ref_key(value) is None:
                gaps.append(issue('INVALID_EXACT_REF',nid,'Declared lifecycle members are missing/null/invalid; no exact identity join is permitted.',['identity','declared',key]))
        if n.get('kind') in ('predicate_result','predicate_expectation','predicate_evaluation','event_occurrence','constraint_check_result','check_result'):
            gaps.append(issue('EXECUTION_EVIDENCE_NOT_ESTABLISHED',nid,'Imported authored record is not proof of native evaluation, actual occurrence, enforcement or physical effect.'))
    slots={};missing_slots=0
    for i,e in enumerate(graph.get('edges',[])):
        for x in ev.iter_errors(e):errors.append(issue('EDGE_SHAPE',e.get('id'),x.message,['edges',i]+list(x.path)))
        eid=e.get('id')
        if eid in edges:errors.append(issue('DUPLICATE_EDGE_ID',eid,'Parallel edges are allowed, duplicate edge storage IDs are not.'))
        edges[eid]=e;a=nodes.get(e.get('source'));b=nodes.get(e.get('target'))
        if a is None or b is None:
            errors.append(issue('UNRESOLVED_ENDPOINT',eid,'One or both endpoint IDs do not resolve.'));continue
        candidates=[v for v in rv.get(e.get('relation'),[]) if v['source_kind'] in ('*',a.get('kind')) and v['target_kind'] in ('*',b.get('kind'))]
        if not candidates:errors.append(issue('ENDPOINT_SIGNATURE',eid,f"{e.get('relation')}: {a.get('kind')} -> {b.get('kind')} is not a declared variant."))
        if e.get('semantic_level')!=edge_level(a.get('semantic_level'),b.get('semantic_level')):errors.append(issue('EDGE_LEVEL',eid,'Edge semantic level does not match actual endpoint levels.'))
        if e.get('relation')=='legacy_field_reference' and e.get('role')!='state_field':errors.append(issue('LEGACY_FIELD_ROLE',eid,'Legacy field-reference migration is restricted to state_field role.'))
        if e.get('role')=='unspecified':gaps.append(issue('UNSPECIFIED_ROLE',eid,'Source supplies no role; unspecified is not permission or a universal role.'))
        if e.get('relation') in ('has_argument','argument_of'):
            raw=e.get('payload',{});p=raw.get('properties',{})
            index=p.get('argument_index',raw.get('order',p.get('index')))
            if index is None:
                m=re.fullmatch(r'(?:operand_|argument_)?([0-9]+)',str(e.get('role','')))
                if m:index=int(m.group(1))
            if type(index) is not int or index<0:
                missing_slots+=1;gaps.append(issue('AST_OPERAND_SLOT_MISSING',eid,'No exact nonnegative source operand index; preserve edge but do not infer order from IDs.'));continue
            parent,child=(e['source'],e['target']) if e['relation']=='has_argument' else (e['target'],e['source'])
            k=(parent,index)
            if k in slots and slots[k]!=child:errors.append(issue('AST_OPERAND_SLOT_CONFLICT',eid,'The same exact parent expression/slot has distinct children.'))
            slots[k]=child
    # An ordered source AST is a finite tree/DAG. Target references are not
    # included here: their cycle legality depends on the unavailable native engine.
    children=collections.defaultdict(set)
    for (parent,index),child in slots.items(): children[parent].add(child)
    active=set();closed=set()
    def visit(node):
        if node in active:
            errors.append(issue('AST_ARGUMENT_CYCLE',node,'Source expression operand structure contains a cycle.'));return
        if node in closed:return
        active.add(node)
        for child in children.get(node,()):visit(child)
        active.remove(node);closed.add(node)
    for parent in list(children):visit(parent)
    gap_counts=collections.Counter(x['code'] for x in gaps)
    return {'schema_version':'p08.contract-validation/v1','counts':{'nodes':len(nodes),'edges':len(edges),'errors':len(errors),'warnings':len(warnings),'readiness_gap_records':len(gaps),'ast_ordered_slots':len(slots),'ast_missing_slot_edges':missing_slots},
      'preservation_contract_valid':not errors,'native_rule_execution':'not_run_unverified','real_backend':'not_run_unverified',
      'scope':'Graph shape, canonical kind/level, typed endpoint signatures, explicit roles and exact AST slot structure. No rule truth, legal applicability, real grant or physical outcome evaluated.',
      'errors':errors,'warnings':warnings,'readiness_gap_counts':dict(gap_counts),'readiness_gap_examples':gaps[:30]}

def main():
    p=argparse.ArgumentParser();p.add_argument('graph',type=Path);p.add_argument('--report',type=Path,default=HERE/'validation-report.json');args=p.parse_args()
    data=args.graph.read_bytes();result=validate(json.loads(data));result['graph_sha256']=hashlib.sha256(data).hexdigest()
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({**result['counts'],'valid':result['preservation_contract_valid']}));return 0 if result['preservation_contract_valid'] else 1
if __name__=='__main__':raise SystemExit(main())
