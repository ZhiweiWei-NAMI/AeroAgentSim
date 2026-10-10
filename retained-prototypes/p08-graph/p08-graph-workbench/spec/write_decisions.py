from pathlib import Path
import json,collections
P=Path(__file__).resolve().parent
m=json.loads((P/'migration-mappings.json').read_text());n=json.loads((P/'node-type-registry.json').read_text());r=json.loads((P/'relation-registry.json').read_text());a=json.loads((P/'ambiguity-ledger.json').read_text())
lines=['# P08 semantic decisions / 语义决策','',
'## Outcome / 结论','',
'The formal authoring contract preserves the full accepted source scope and every source occurrence. It unifies compatible vocabulary without pretending definitions, attempted execution, expected truth and observed outcomes are interchangeable. The graph is a directed typed multigraph; the vocabulary count is not a count of ontology layers or verified business outcomes.','',
'正式合同保留来源范围、原始对象与关系记录。只在身份、层级和语义兼容时统一名称；定义、配置实例、执行尝试、预期结果和实际证据保持分离。相同节点对上的不同角色、条件、时间与版本关系均保留。','',
'## Decisions that change implementation','',
'1. A state specification is a field declaration. A fact is one typed value record with exact subject, computing authority, validity, availability and provenance. No second physical value is introduced.',
'2. An agent remains independent of an entity. Multiple agents and multiple related entities are allowed within explicit operation/target/time scopes; overlap needs a declared arbitration policy, never a guessed priority.',
'3. Strategy definitions, invocations, decisions, command definitions, attempts, lifecycle receipts, behavior definitions/executions and module configurations/executions form distinct control-chain objects.',
'4. `module_execution → module` is `execution_of_module`, since the source module is configured and is not a reusable class to cast into. `module → field` declares selected computing authority; `module_execution → fact` carries production lineage.',
'5. Strategy input constraints, output requirements, behavior constraints, start checks, continuation checks and effect/review checks are not substitutes. A legal source or grant placeholder stays unverified.',
'6. Resource requirements retain dimension, quantity, unit, interval and purpose. Capacity, reservation, occupancy, consumption, reuse and release are independent. Review processes do not automatically consume execution resources.',
'7. Relation-instance nodes retain ordered role endpoints. Binary projection edges retain the same role and index; no generic cardinality may collapse passenger roles, custodians, equipment, material batches, slots or attempts.',
'8. Exact structured identity preserves the JSON types of epoch and generation. Unknown, null or empty identity fields never prove equality. A canonical display/storage ID cannot repair missing runtime identity.',
'9. Main rule truth and applicability scope status are independent. Unknown truth, missing evidence, execution-not-run and binding conflicts remain separate statuses.',
'10. AST expression forms, literal types, all nested operator operands and operand order, reference binding context, parameter defaults/scopes, main/applicability roots and temporal controls are retained. Native parity remains unverified.',
'11. ARGUMENT uses opposite source directions in delivery versus the other catalogs. Preserve endpoints and expose has_argument versus argument_of; do not reverse source edges or select ordering by label.',
'12. Classification into a legacy node_type is classified_as, not a cast. Fact→field is instantiates_state. Forty-two legacy source check field references labelled INSTANCE_OF are legacy_field_reference, with exact role state_field only.',
'13. Source-only references use source_field_reference. They do not become business meaning, authority or causality merely because the target resolves.',
'14. Only exact explicit definition identity/revision/scope/body permits deduplication. Missing revision or scope means distinct canonical records; a UI may group equivalent-looking source occurrences but must not call that semantic identity.',
'15. The current native Atlas catalog/operator/evaluator, real grants and qualifications, calibrated measurements, physical producers, durable custody authority and compute/energy couplings remain source/evidence gaps. This contract adds no law, threshold, priority or operational action.',
'', '## Complete runtime-kind decisions / 42 个原始类型', '',
'All fields in every original profile stay in payload. Canonical-family mapping is not occurrence merging.', '',
'| Raw kind | Canonical kind | Decision | Source occurrence count | Meaning |','|---|---|---|---:|---|']
for x in m['node_mappings']:
 if x['namespace']=='proposed_runtime_kind':lines.append(f"| `{x['source']}` | `{x['target']}` | {x['decision']} | {x['occurrences']} | {x['definition']} |")
lines+=['','## Frozen collection mappings / 冻结集合','', '| Collection | Canonical family | Allowed semantic levels |','|---|---|---|']
for x in n['kinds']:
 for c in x.get('source_collections',[]):lines.append(f"| `{c}` | `{x['kind']}` | {', '.join(x['semantic_levels'])} |")
lines+=['','## Relation migration / 关系迁移','',
'This table lists every raw name by its source namespace. Endpoints are never reversed; exact typed variants, roles and obligations are in relation-registry.json. Repeated canonical names across rows do not collapse edge identities.', '',
'| Namespace | Raw relation | Canonical relation(s) |','|---|---|---|']
groups=collections.defaultdict(set)
for v in r['variants']:groups[(v['source_namespace'],v['raw_relation'])].add(v['relation'])
for (ns,raw),cs in sorted(groups.items()):lines.append(f"| {ns} | `{raw}` | {', '.join('`'+c+'`' for c in sorted(cs))} |")
lines+=['','## Semantic requirements / 语义要求','',
'The following requirements are checked for readiness. When the source does not provide one, preserve the source and report the precise gap. Do not fabricate it to satisfy the schema.', '']
for x in n['kinds']:
 lines += [f"### {x['kind']}", '', x['definition'], '', 'Required meaning: '+', '.join(x.get('semantic_obligations',[]))+'.', '']
lines+=['## Ambiguity ledger / 歧义和缺口','']
for x in a['entries']:lines += [f"### {x['id']}: {x['topic']}", '',f"Status: {x['status']}", '',x['decision'],'']
lines+=['## Evidence boundary / 证据边界','',
'Authoring validity, lossless import/export, fixture exercise, native rule parity and real-backend readiness are reported separately. The accepted stage-one review covers its bounded examples and explicit source steps. It is not an exhaustive catalog of all future situations, native execution or proof of physical success. The private workbench may inspect and edit declarations and replay authored data; graph selection never dispatches commands.', '']
(P/'SEMANTIC_DECISIONS.md').write_text('\n'.join(lines))
