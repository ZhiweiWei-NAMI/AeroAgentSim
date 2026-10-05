import { getLocale } from './i18n.js';
import { GRAPH_COLLECTIONS, DEFAULT_GRAPH, graphNodes, graphEdges, validateGraph, inspectDefinitionAST } from './graph-config.js';
import { graphRelationLayers, graphDisplayEdges } from './graph-display.js';
import { executeGraphFixture } from './graph-runtime.js';

// Presentation state is ephemeral. The console's draft.graph is the only graph store.
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const copy = (zh, en) => getLocale() === 'zh-CN' ? zh : en;
const json = value => JSON.stringify(value, null, 2);
const list = value => Array.isArray(value) ? value : [];
const records = value => list(value).filter(item => item && typeof item === 'object' && !Array.isArray(item));
const badge = (text, tone = 'muted') => `<span class="badge ${tone}">${esc(text)}</span>`;
const empty = () => `<p class="gw-empty">${copy('未声明。保留为空，不推断。', 'Not declared. Kept empty, without inference.')}</p>`;
const labelMap = {
  aircraft: ['航空器', 'Aircraft'], 'ground-vehicle': ['地面车辆', 'Ground vehicle'], 'charging-station': ['充电站', 'Charging station'], parcel: ['包裹', 'Parcel'], 'compute-node': ['计算节点', 'Compute node'],
  'uav-alpha': ['无人机 Alpha', 'UAV Alpha'], 'uav-beta': ['无人机 Beta', 'UAV Beta'], 'vehicle-1': ['地面配送车 1', 'Ground courier 1'], 'station-west': ['西侧充电站', 'West charging station'], 'parcel-one': ['包裹 001', 'Parcel 001'], 'edge-west': ['西侧边缘计算节点', 'West edge computer'],
  position: ['位置', 'Position'], battery: ['电池荷电状态', 'Battery state of charge'], 'link-quality': ['观测链路质量', 'Observed link quality'], custody: ['货物保管关系', 'Cargo custody'], 'compute-load': ['计算负载', 'Compute load'],
  'mobility-module': ['移动模块', 'Motion fixture'], 'energy-module': ['能量模块', 'Energy fixture'], 'network-module': ['通信模块', 'Link fixture'], 'cargo-module': ['货物模块', 'Cargo fixture'], 'compute-module': ['计算模块', 'Compute fixture'],
  'alpha-autonomy': ['Alpha 自主智能体', 'Alpha autonomy'], 'fleet-dispatch': ['机队调度智能体', 'Fleet dispatch'], 'alpha-strategy': ['能量与链路响应策略', 'Energy / link response'], 'logistics-strategy': ['多实体调度策略', 'Multi-entity dispatch'],
  'charge-alpha': ['请求充电', 'Request charging'], 'deliver-parcel': ['请求包裹转运', 'Request parcel transfer'], 'return-alpha': ['请求返航', 'Request return'],
  charging: ['充电案例', 'Charging'], logistics: ['物流案例', 'Logistics'], 'comm-return': ['通信退化与返航', 'Communication degradation → return']
};
const collectionLabel = key => {
  if(key==='expressions') return copy('表达式节点（派生）','Expression nodes (derived)');
  if(key==='expression_types') return copy('表达式类型（派生）','Expression types (derived)');
  const collection = GRAPH_COLLECTIONS.find(item => item.key === key);
  return collection ? (getLocale() === 'zh-CN' ? collection.label_zh : collection.label) : key;
};
const nodeLabel = (node, collection) => {
  const original = list(DEFAULT_GRAPH[collection]).find(item => item.id === node.id);
  // A user's labels are never translated or replaced.
  return original?.label === node.label && labelMap[node.id] ? copy(...labelMap[node.id]) : node.label || node.id;
};
const relationshipLabel = relation => {
  const labels = {
    CALLS: ['调用策略', 'calls strategy'], INPUT_TO: ['作为输入', 'input to'], OUTPUT_MUST_SATISFY: ['输出必须满足', 'output must satisfy'], PRODUCES: ['产生决策', 'produces'], GENERATES: ['生成命令', 'generates'], COMPOSES: ['组合行为', 'composes'], EXECUTED_BY: ['执行模块', 'executed by'], CONSTRAINED_BY: ['受约束', 'constrained by'], READS: ['读取事实', 'reads'], READS_DEFINITION: ['读取定义', 'reads definition'], DEFINES: ['定义谓词', 'defines'], USES: ['使用', 'uses'], PERMITS: ['允许声明阶段', 'permits phase'], BLOCKS: ['阻止声明阶段', 'blocks phase'], UPDATES: ['更新事实', 'updates'], OWNS: ['拥有事实', 'owns'], EVIDENCED_BY: ['证据来源', 'evidenced by'], FEEDS_BACK: ['反馈智能体', 'feeds back'], BASED_ON: ['依据', 'based on'], WAITS_FOR: ['等待', 'waits for'], DEPENDS_ON: ['定义依赖', 'depends on'], INSTANCE_OF: ['类型实例', 'instance of'], REQUIRES: ['所需能力或资源', 'requires'], DECLARES: ['声明字段', 'declares'],
    type_id: ['所属类型', 'has type'], field_ids: ['声明字段', 'declares fields'], entity_id: ['事实所属实体', 'belongs to entity'], field_id: ['字段类型', 'field definition'], producer_module_id: ['生产模块', 'produced by'], source_id: ['来源', 'source'],
    strategy_id: ['策略绑定', 'strategy binding'], observable_fact_ids: ['观测输入', 'observes'], output_command_ids: ['命令输出', 'outputs command'], predicate_ids: ['依赖谓词', 'requires predicates'], rule_ids: ['依赖规则', 'requires rules'], behavior_ids: ['行为组合', 'composes behavior'], capability_ids: ['所需能力', 'requires capability']
  };
  return labels[relation] ? copy(...labels[relation]) : relation;
};
const chineseIssue = issue => {
  const known={
    E_SCHEMA:'记录结构不符合当前类型契约。请检查必填字段、值类型和允许的属性。',
    E_DUPLICATE_ID:'稳定 ID 重复。不同类型的记录也必须有唯一标识。',
    E_UNRESOLVED_REFERENCE:'引用未解析。请选用该类型中已声明的稳定 ID。',
    E_EVENT_COMMAND_CONFUSION:'事件定义与命令定义不能互换；观测反馈不能充当执行请求。',
    E_MISSING_STATE_PRODUCER:'动态状态缺少明确的生产模块，或生产者未声明所需写入范围。',
    E_EXCLUSIVE_SCOPE_OVERLAP:'独占控制范围冲突。必须声明适用的共同仲裁后才能重叠。',
    E_STRATEGY_INPUT_MISMATCH:'策略输入与可观测事实、字段类型、单位或坐标系不一致。',
    E_STRATEGY_OUTPUT_MISMATCH:'策略输出与决策、命令或智能体绑定不一致。',
    E_RESOURCE_OVERCOMMIT:'声明的资源需求超过容量。请检查并行行为和同时运行的任务。',
    E_MISSING_CONSTRAINT:'任务、行为或资源需要明确的适用约束。',
    E_COMMAND_SCOPE:'命令目标超出智能体声明的控制范围。',
    E_BEHAVIOR_COMMAND_SCOPE:'行为目标超出命令或智能体的声明范围。',
    E_CAPABILITY_SCOPE:'所选能力未覆盖该行为的实体和执行模块。',
    E_BEHAVIOR_BINDING:'行为需要明确的能力与执行模块绑定。',
    E_TIME_ORDER:'有效时间、可用时间或有效期顺序不一致。',
    W_LEGAL_UNVERIFIED:'法律元数据仅为声明，不能据此认定权威性或运行许可。',
    W_LEGAL_SOURCE_INCOMPLETE:'法律来源缺少经核实的发布者、生效期间、版本、条款或原始链接；许可保持 UNKNOWN。',
    W_FIXTURE_ONLY:'所有绑定仅为样例或占位。静态校验不等于案例执行，更不等于真实服务连接。',
    W_MISSING_FACT:'缺失事实保持 UNKNOWN，不会补成零或 false。',
    W_AUTHORED_ASSESSMENT:'人工填写的评估仅为样例输入；实际可行性、许可和适宜性仍分别保持 UNKNOWN。'
  };
  if(known[issue.code]) return known[issue.code];
  const code=issue.code||'';
  if(code.includes('AST')) return code.includes('UNSUPPORTED')?'此运算符或表达式控制尚不支持检查；保留声明，但不产生求值结果。':'表达式定义存在问题。请检查有序操作数、状态、参数、目标引用、单位与时间控制。';
  if(code.includes('EDGE')) return '类型边契约存在问题。请检查端点类型、唯一 ID、角色、条件、时态和作用范围。';
  if(code.includes('UNIT')) return '单位不匹配或未登记；不会进行隐式单位转换。';
  if(code.includes('FRAME')) return '坐标系不匹配或未声明；不会进行隐式坐标转换。';
  if(code.includes('REFERENCE')) return '引用缺失或类型不匹配。请检查记录路径对应的稳定 ID。';
  if(code.includes('TYPE')) return '声明类型不匹配。请检查实体类型、字段类型和实际值。';
  if(code.includes('SCOPE')) return '声明范围不一致。请检查实体、字段、控制权与读写权限。';
  if(code.includes('RESOURCE')) return '资源需求、容量、分配或单位不一致。';
  if(code.includes('SOURCE')||code.includes('LEGAL')) return '来源或法律引用不完整，或来源类型不适用于当前记录。';
  if(code.includes('BINDING')||code.includes('MISMATCH')) return '绑定关系不一致。请检查路径对应的输入、输出和任务声明。';
  return issue.severity==='warning'?'此项声明仍有检查限制。展开技术详情查看准确原因。':'此项声明未通过静态检查。展开技术详情查看准确原因。';
};

const layers = [
  { title: ['世界与事实', 'World & facts'], keys: ['node_types','entity_types','entities','fields','facts','modules'] },
  { title: ['自主决策', 'Autonomy'], keys: ['agents','strategies','requests','objectives','decisions'] },
  { title: ['执行声明', 'Execution declarations'], keys: ['tasks','commands','behaviors','capabilities','arbitrations'] },
  { title: ['资源与约束', 'Resources & constraints'], keys: ['resources','constraints','admission_checks','check_results','feedback_policies','sources'] },
  { title: ['解释与案例', 'Interpretation & cases'], keys: ['expressions','expression_types','state_definitions','relation_definitions','parameter_definitions','predicates','rules','predicate_results','events','scenarios'] }
];

export function createGraphWorkbench(hooks) {
  const ui = { selectedId: null, collection: 'entities', query: '', scenarioId: 'charging', relationLayer: 'all', edgeId: null, loopCommandId: null, result: null, resultGraph: null, editor: null, notice: null };
  let cache = null;
  function model() {
    const config = hooks.getConfig(), graph = config.graph;
    if (!graph) return null;
    // No graph audit, edge rebuild or fixture execution on replay-cursor renders.
    if (cache?.graph === graph && cache.config === config) return cache;
    const nodes = graphNodes(graph).filter(entry=>entry&&entry.node&&typeof entry.node==='object');
    cache = {graph,config,nodes,byId:new Map(nodes.map(node => [node.id,node])),edges:graphEdges(graph),validation:validateGraph(graph,config),definitionReports:new Map()};
    cache.displayEdges=graphDisplayEdges({...graph,strategies:records(graph.strategies),decisions:records(graph.decisions)},cache.edges);
    cache.relationLayers=graphRelationLayers(cache.edges);
    if (!cache.byId.has(ui.selectedId)) ui.selectedId = records(graph.entities)[0]?.id || nodes[0]?.id || null;
    return cache;
  }
  function nodeButton(id, extra = '', relation = '') {
    const entry = model()?.byId.get(id);
    if (!entry) return `<span class="gw-missing mono">${esc(id)}</span>`;
    return `<button type="button" class="gw-node ${entry.id === ui.selectedId ? 'selected' : ''} ${extra}" data-gw-action="select" data-id="${esc(id)}" aria-pressed="${entry.id === ui.selectedId}"><span class="gw-node-type">${esc(collectionLabel(entry.collection))}</span><strong>${esc(nodeLabel(entry.node,entry.collection))}</strong><code>${esc(id)}</code>${relation ? `<span class="gw-relation">${esc(relationshipLabel(relation))}</span>` : ''}</button>`;
  }
  function refs(ids) {
    const unique = [...new Set(list(ids))];
    return unique.length ? `<div class="gw-reference-list">${unique.map(id=>nodeButton(id,'compact')).join('')}</div>` : empty();
  }
  function scopeRows(scopes, fieldMode = true) {
    return list(scopes).length ? `<div class="gw-scopes">${scopes.map(scope=>`<div class="gw-scope"><div>${refs(scope.entity_ids)}</div>${fieldMode ? `<div>${refs(scope.field_ids)}</div>` : `<div>${badge(scope.mode)}${scope.arbitration_id ? refs([scope.arbitration_id]) : `<small>${copy('无仲裁引用', 'No arbitration reference')}</small>`}</div>`}</div>`).join('')}</div>` : empty();
  }
  const section = (title, body, detail='') => `<section class="gw-detail-section"><h3>${esc(title)}</h3>${detail ? `<p class="field-hint">${esc(detail)}</p>` : ''}${body}</section>`;
  function entityDetails(entry) {
    const {graph}=model(), entity=entry.node, type=records(graph.entity_types).find(item=>item.id===entity.type_id);
    const facts=records(graph.facts).filter(fact=>fact.entity_id===entity.id);
    const fieldRows=list(type?.field_ids).map(id=>{
      const field=records(graph.fields).find(item=>item.id===id);
      const producers=records(graph.modules).filter(module=>list(module.writes).some(scope=>list(scope.entity_ids).includes(entity.id)&&list(scope.field_ids).includes(id)));
      const readers=records(graph.modules).filter(module=>list(module.reads).some(scope=>list(scope.entity_ids).includes(entity.id)&&list(scope.field_ids).includes(id)));
      const fieldFacts=facts.filter(fact=>fact.field_id===id);
      return `<tr><td>${nodeButton(id,'compact')}<small>${esc(field?.value_type)} · ${esc(field?.unit)} · ${esc(field?.frame)}</small></td><td>${refs(producers.map(item=>item.id))}<small>${copy('读取模块', 'Read by')}: ${esc(readers.map(item=>nodeLabel(item,'modules')).join(', ')||'—')}</small></td><td>${fieldFacts.length ? fieldFacts.map(fact=>`<button class="gw-text-link" data-gw-action="select" data-id="${esc(fact.id)}">${esc(json(fact.value))} ${esc(fact.unit)}</button><small>${copy('有效时间 / 可用时间', 'Valid / available time')}: ${esc(fact.valid_time_ns)} / ${esc(fact.available_time_ns)} ns</small>`).join('') : badge(copy('未知 · 无事实', 'Unknown · no fact'))}</td></tr>`;
    }).join('');
    const agents=records(graph.agents).filter(agent=>list(agent.controls).some(scope=>list(scope.entity_ids).includes(entity.id)));
    const strategies=records(graph.strategies).filter(strategy=>agents.some(agent=>agent.strategy_id===strategy.id));
    const commands=records(graph.commands).filter(command=>list(command.target_entity_ids).includes(entity.id));
    const factIds=new Set(facts.map(fact=>fact.id));
    const predicateIdsFromFacts=new Set(model().edges.filter(edge=>factIds.has(edge.target)&&edge.relation==='READS').map(edge=>edge.source));
    const predicates=records(graph.predicates).filter(predicate=>predicateIdsFromFacts.has(predicate.id));
    const predicateIds=new Set(predicates.map(item=>item.id));
    const rules=records(graph.rules).filter(rule=>predicateIds.has(rule.output_predicate_id)||predicates.some(predicate=>list(predicate.rule_ids).includes(rule.id)));
    const events=records(graph.events).filter(event=>list(event.entity_ids).includes(entity.id));
    return section(copy('声明字段、事实与模块所有权','Declared fields, facts & module ownership'), `<div class="table-wrap"><table class="data-table compact-table gw-fields"><thead><tr><th>${copy('字段 / 类型 / 单位','Field / type / unit')}</th><th>${copy('写入所有者','Write owner')}</th><th>${copy('声明事实（非实时状态）','Declared facts (not live state)')}</th></tr></thead><tbody>${fieldRows||`<tr><td colspan="3">${copy('该类型未声明字段。','No fields declared for this type.')}</td></tr>`}</tbody></table></div>`)
      + section(copy('独立智能体 → 策略 → 命令','Independent agent → strategy → command'), `<div class="gw-chain"><div><h4>${copy('控制范围','Control scope')}</h4>${refs(agents.map(item=>item.id))}</div><span aria-hidden="true">→</span><div><h4>${copy('策略输入 / 输出','Strategy input / output')}</h4>${refs(strategies.map(item=>item.id))}</div><span aria-hidden="true">→</span><div><h4>${copy('声明命令','Declared commands')}</h4>${refs(commands.map(item=>item.id))}</div></div>`,copy('智能体与实体独立建模；控制权必须通过显式范围声明。','Agents are independent of entities; control requires an explicit scope.'))
      + section(copy('定义规则、谓词与事件','Definition rules, predicates & events'), `<div class="gw-chain"><div><h4>${copy('定义规则','Definition rules')}</h4>${refs(rules.map(item=>item.id))}</div><span aria-hidden="true">·</span><div><h4>${copy('谓词定义','Predicate definitions')}</h4>${refs(predicates.map(item=>item.id))}</div><span aria-hidden="true">·</span><div><h4>${copy('独立事件定义','Independent event definitions')}</h4>${refs(events.map(item=>item.id))}</div></div>`,copy('这些是声明依赖。Atlas 尚未执行，事件不能直接视为命令。','These are declared dependencies. Atlas has not executed; events are not actuator commands.'));
  }
  function composition(command) {
    const plan=command.behavior_plan || command.composition || command.behaviors || command.behavior_ids || [];
    const stages=Array.isArray(plan) ? plan : plan.steps || plan.stages || plan.behavior_ids || [];
    const mode=plan.mode || 'sequence';
    const modeText={sequence:copy('顺序组合','Sequential composition'),parallel:copy('并行组合','Parallel composition'),conditional:copy('条件组合','Conditional composition')}[mode]||mode;
    return section(copy('命令的行为组合','Command behavior composition'),`<p><strong>${esc(modeText)}</strong></p><p class="field-hint">${copy('顺序、并行与所需能力均来自声明；不执行物理动作。','Ordering, parallelism and capabilities come from declarations; no physical action executes.')}</p><div class="gw-behavior-plan">${list(stages).map((stage,index)=>{
      const ids=typeof stage==='string'?[stage]: stage.behavior_ids || (stage.behavior_id?[stage.behavior_id]:stage.steps?.map(step=>step.behavior_id).filter(Boolean)) || [];
      return `<div class="gw-behavior-step"><span class="gw-step-number">${mode==='parallel'?'∥':index+1}</span><div><strong>${esc(typeof stage==='object' ? stage.mode || stage.kind || modeText : modeText)}</strong>${refs(ids)}${list(plan.conditions).filter(condition=>ids.includes(condition.behavior_id)).map(condition=>`<p class="field-hint">${esc(condition.predicate_id)} = ${esc(condition.expected)}</p>`).join('')}</div></div>`;
    }).join('') || empty()}</div>`);
  }
  function typedDetails(entry) {
    const node=entry.node;
    switch(entry.collection) {
      case 'entities': return entityDetails(entry);
      case 'expressions': return section(copy('派生表达式节点（只读）','Derived expression node (read only)'),`<p class="field-hint">${copy('此节点属于规范配置图，来源为规则 AST。请编辑所属规则，不单独创建表达式存储。','This node belongs to the canonical graph and is derived from a rule AST. Edit the owning rule; expressions have no separate store.')}</p><ul class="gw-ast">${astTree(node.expression,'expression')}</ul><p>${copy('类型 / 单位','Type / unit')}: ${esc(node.value_type)} / ${esc(node.unit)} · ${copy('真值','Truth')}: UNKNOWN</p>`);
      case 'expression_types': return section(copy('派生表达式类型（只读）','Derived expression type (read only)'),`<p class="field-hint">${copy('此类型登记来自当前表达式契约，不能在记录编辑器中独立改写。','This type registry comes from the current expression contract and cannot be independently rewritten in the record editor.')}</p>`);
      case 'predicates': case 'rules': return definitionDetails(entry);
      case 'modules': return section(copy('写入所有权','Write ownership'),scopeRows(node.writes))+section(copy('读取依赖','Read dependencies'),scopeRows(node.reads));
      case 'agents': return section(copy('声明控制范围','Declared control scope'),scopeRows(node.controls,false))+section(copy('独立策略绑定','Independent strategy binding'),refs([node.strategy_id]))+section(copy('可观测事实','Observable facts'),refs(node.observable_fact_ids));
      case 'strategies': return section(copy('策略输入契约','Strategy input contract'),`<pre class="gw-json">${esc(json(node.input_fields))}</pre>`)+section(copy('可观测事实 / 请求 / 目标','Observed facts / requests / objectives'),refs([...list(node.observable_fact_ids),...list(node.request_ids),...list(node.objective_ids)]))+section(copy('决策 / 命令输出','Decision / command outputs'),refs([...list(node.decision_ids),...list(node.output_command_ids)]))+section(copy('约束引用','Constraint references'),refs(node.constraint_ids));
      case 'commands': return composition(node)+section(copy('目标实体 / 智能体 / 任务','Target entities / agent / task'),refs([...list(node.target_entity_ids),node.agent_id,node.task_id].filter(Boolean)));
      case 'behaviors': return section(copy('所需能力','Required capabilities'),refs(node.capability_ids || node.required_capability_ids))+section(copy('模块与资源依赖','Module & resource dependencies'),refs([...list(node.module_ids),...list(node.required_module_ids),...list(node.resource_ids),...list(node.resource_claims).map(item=>item.resource_id)]))+section(copy('约束与合法性引用','Constraint & legality references'),refs(node.constraint_ids));
      case 'capabilities': return section(copy('能力声明与适用实体','Capability declaration & applicable entities'),refs([...list(node.entity_ids),...list(node.entity_type_ids),...list(node.module_ids)]));
      case 'constraints': return section(copy('约束适用范围','Constraint scope'),`<div class="gw-record-status">${badge(node.enforcement==='hard'?copy('硬约束','Hard constraint'):copy('软约束','Soft constraint'),'amber')}${badge(node.kind)}</div>`+refs([...list(node.entity_ids),...list(node.resource_ids)]))+section(copy('法律 / 规范来源','Legal / normative source'),refs([node.source_id].filter(Boolean)),copy('可行性、许可与适宜性分别报告。来源引用不等于法律合规认证。','Feasibility, permission and suitability are independent. A source reference is not legal certification.'));
      case 'facts': return section(copy('事实的时间与来源','Fact timing & provenance'),`<dl class="gw-values"><dt>${copy('值 / 单位 / 坐标系','Value / unit / frame')}</dt><dd>${esc(json(node.value))} · ${esc(node.unit)} · ${esc(node.frame)}</dd><dt>${copy('有效时间','Valid time')}</dt><dd>${esc(node.valid_time_ns)} ns</dd><dt>${copy('可用时间','Available time')}</dt><dd>${esc(node.available_time_ns)} ns</dd></dl>${refs([node.entity_id,node.field_id,node.producer_module_id,node.source_id])}`);
      default: return '';
    }
  }
  function reviewEdges() {
    return model().displayEdges.filter(edge=>ui.relationLayer==='all'||edge.review_layer===ui.relationLayer);
  }
  function relationReview() {
    const current=model(), selected=current.relationLayers.find(layer=>layer.id===ui.relationLayer);
    const unknown=current.displayEdges.filter(edge=>edge.review_layer==='unmapped').length;
    return `<section class="panel gw-relation-review" aria-labelledby="gw-relation-review-title"><header class="panel-header"><div><h2 class="panel-title" id="gw-relation-review-title">${copy('五层关系审阅','Five-layer relation review')}</h2><p class="panel-description">${copy('按关系用途筛选依赖图与路径边。分别报告每层的契约类型、当前出现类型和规范边数量。','Filter dependency and path edges by relation purpose. Each layer reports its contracted types, currently present types and canonical edges separately.')}</p></div><button class="button small ${ui.relationLayer==='all'?'primary':''}" data-gw-action="relation-layer" data-key="all" aria-pressed="${ui.relationLayer==='all'}">${copy('显示全部层','Show all layers')}</button></header><div class="panel-body"><div class="gw-relation-layers">${current.relationLayers.map(layer=>`<button class="gw-relation-layer ${ui.relationLayer===layer.id?'selected':''}" data-gw-action="relation-layer" data-key="${esc(layer.id)}" aria-pressed="${ui.relationLayer===layer.id}" data-layer-id="${esc(layer.id)}" data-contract-count="${layer.declared_type_count}" data-present-count="${layer.present_relations.length}" data-edge-count="${layer.edge_count}"><strong>${esc(getLocale()==='zh-CN'?layer.label_zh:layer.label)}</strong><span><b>${layer.declared_type_count}</b> ${copy('契约类型','contracted types')}</span><span><b>${layer.present_relations.length}</b> ${copy('当前出现类型','present types')}</span><span><b>${layer.edge_count}</b> ${copy('规范边','canonical edges')}</span></button>`).join('')}</div><p class="field-hint">${copy('仅将已核对的 PRODUCES 双向声明镜像在显示层合并；点开后可查看两个原始 ID 与全部元数据。其他多重边仍分别保留。','Only audited reciprocal PRODUCES declarations are bundled in the display. Open a bundle to inspect both original IDs and complete metadata. Other multiedges stay separate.')}</p>${selected?`<div class="gw-selected-layer"><strong>${copy('当前筛选','Current filter')}: ${esc(getLocale()==='zh-CN'?selected.label_zh:selected.label)}</strong><div class="gw-relation-types">${selected.relations.map(relation=>`<span class="${selected.present_relations.includes(relation)?'is-present':''}">${esc(relation)} ${selected.present_relations.includes(relation)?'•':'○'}</span>`).join('')}</div><p class="field-hint">${copy('实点：当前图出现；空点：仅在契约中声明。此分层不改变端点类型、角色、条件、时态或来源。','Filled dot: present in this graph. Open dot: declared in the contract only. Layers do not change endpoint types, roles, conditions, time or provenance.')}</p>${Object.keys(selected.variants).length?`<details class="gw-relation-variants"><summary>${copy('同名关系的已审阅端点语义','Audited endpoint semantics for shared relation names')}</summary>${Object.entries(selected.variants).map(([relation,variants])=>`<div><code>${esc(relation)}</code><ul>${variants.map(variant=>`<li>${esc(variant)}</li>`).join('')}</ul></div>`).join('')}</details>`:''}</div>`:''}${unknown?`<p class="error-text">${copy('未归类的显示边','Unmapped display edges')}: ${unknown}</p>`:''}</div></section>`;
  }

  function edgeCard(edge, direction) {
    const endpoint=direction==='incoming'?edge.source:edge.target;
    return `<div class="gw-edge-card" data-edge-id="${esc(edge.id)}" data-review-layer="${esc(edge.review_layer||'')}" data-display-bundle="${edge.display_bundle===true}">${nodeButton(endpoint,'compact')}<button class="gw-edge-label" data-gw-action="edge" data-id="${esc(edge.id)}"><strong>${esc(edge.relation)}</strong><span>${esc(relationshipLabel(edge.relation))}</span>${edge.role?`<small>${esc(edge.role)}</small>`:''}${edge.display_bundle?`<small class="gw-bundle-label">${copy('2 个原始声明 · 仅显示合并','2 source declarations · display bundle')}</small>`:''}<span aria-hidden="true">↗</span></button></div>`;
  }
  function edgeInspector() {
    const current=model();
    const edge=current.displayEdges.find(item=>item.id===ui.edgeId)||current.edges.find(item=>item.id===ui.edgeId);
    if(!edge) return '';
    const metadata=[['role','角色','Role'],['condition','条件','Condition'],['time','时间','Time'],['scope','作用范围','Scope'],['version','版本','Version'],['source_id','来源','Source'],['origin','边的来源类型','Edge origin'],['path','声明路径','Declaration path'],['order','组合顺序索引','Composition order index']];
    return `<section class="gw-edge-inspector" aria-labelledby="gw-edge-title"><div class="gw-actions"><h3 id="gw-edge-title">${copy('有向类型边','Directed typed edge')} · ${esc(edge.relation)}</h3><button class="button small" data-gw-action="close-edge">${copy('关闭','Close')}</button></div><code>${esc(edge.id)}</code><div class="gw-edge-endpoints">${nodeButton(edge.source,'compact')}<span aria-hidden="true">→</span>${nodeButton(edge.target,'compact')}</div><dl class="gw-values">${metadata.map(([key,zh,en])=>`<dt>${copy(zh,en)}</dt><dd>${edge[key]===null||edge[key]===undefined?copy('未声明','Not declared'):esc(typeof edge[key]==='object'?json(edge[key]):edge[key])}</dd>`).join('')}</dl>${edge.display_bundle?`<section class="gw-bundle-origins"><h4>${copy('显示合并的两个原始声明','Two original declarations in this display bundle')}</h4><p>${copy('规范图和导出未改变；以下角色、路径、条件、时间、范围、版本和来源分别保留。','The canonical graph and export are unchanged. Each original retains its role, path, condition, time, scope, version and provenance.')}</p>${edge.origin_edges.map((origin,index)=>`<details open data-canonical-edge-id="${esc(origin.id)}"><summary>${index+1}. ${esc(origin.role)} · ${esc(origin.path)}</summary><code>${esc(edge.canonical_edge_ids[index])}</code><pre class="gw-json">${esc(json(origin))}</pre></details>`).join('')}</section>`:''}<p class="field-hint">${copy('每条边保留独立 ID。相同端点之间的不同条件和时态关系不会合并。仅审阅确认的声明镜像可在显示中折叠。','Every canonical edge keeps its own ID. Distinct conditions and temporal relationships are never merged. Only audited declaration mirrors may be collapsed for display.')}</p></section>`;
  }
  function astTree(value, key='root') {
    if(value===null||value===undefined) return `<li><span>${esc(key)}: ${copy('未知','Unknown')}</span></li>`;
    if(typeof value!=='object') return `<li><span><code>${esc(key)}</code> · ${esc(value)}</span></li>`;
    const operator=value.op || value.operator || value.kind || value.type || key;
    const reference=value.state || value.param || value.predicate || value.event || value.ref || value.field_id || value.parameter_id || value.predicate_id || value.event_id;
    const children=Object.entries(value).filter(([name])=>!['op','operator','kind','type'].includes(name));
    return `<li><div class="gw-ast-node"><small>${esc(key)}</small><strong>${esc(operator)}</strong>${typeof reference==='string'&&model().byId.has(reference)?nodeButton(reference,'compact'):''}</div>${children.length?`<ul>${children.map(([name,child])=>Array.isArray(child)?`<li><span>${esc(name)}</span><ul>${child.map((item,index)=>astTree(item,`${name}[${index}]`)).join('')}</ul></li>`:astTree(child,name)).join('')}</ul>`:''}</li>`;
  }
  function definitionReport(ruleId) {
    const current=model();
    if(!current.definitionReports.has(ruleId)) current.definitionReports.set(ruleId,inspectDefinitionAST(current.graph,ruleId));
    return current.definitionReports.get(ruleId);
  }
  function definitionDetails(entry) {
    const {graph,edges,byId}=model(), node=entry.node;
    const rules=entry.collection==='rules'?[node]:records(graph.rules).filter(rule=>rule.output_predicate_id===node.id||list(node.rule_ids).includes(rule.id));
    const dependencies=reviewEdges().filter(edge=>edge.source===entry.id&&['DEPENDS_ON','READS_DEFINITION','USES'].includes(edge.relation));
    const results=records(graph.predicate_results).filter(result=>result.predicate_id===node.id||result.definition_id===node.id);
    return section(copy('定义依赖与表达式分支','Definition dependencies & expression branches'),`${dependencies.length?`<div class="gw-definition-refs">${dependencies.map(edge=>edgeCard(edge,'outgoing')).join('')}</div>`:''}${rules.map(rule=>`<div class="gw-rule-tree"><h4>${esc(nodeLabel(rule,'rules'))} · ${esc(rule.operator||rule.definition_ast?.op||rule.expression?.op||rule.ast?.op||'')}</h4><div class="gw-definition-inputs"><div><h4>${copy('状态 / 关系定义','State / relation definitions')}</h4>${refs([...list(definitionReport(rule.id).requirements),...list(rule.state_field_ids),...list(rule.relation_definition_ids)])}</div><div><h4>${copy('参数定义','Parameter definitions')}</h4>${refs(rule.parameter_ids)}</div><div><h4>${copy('目标依赖（子谓词 / 事件）','Target dependencies (subpredicates / events)')}</h4>${refs([...list(definitionReport(rule.id).dependencies),...list(rule.subpredicate_ids),...list(rule.event_ids)])}</div></div><ul class="gw-ast">${astTree(rule.definition_ast,'definition_ast')}</ul>${definitionReport(rule.id).warnings.length||definitionReport(rule.id).errors.length?diagnostics(definitionReport(rule.id)):''}${rule.applicability_ast?`<h4>${copy('适用性表达式（独立分支）','Applicability expression (separate branch)')}</h4><ul class="gw-ast">${astTree(rule.applicability_ast)}</ul>`:''}</div>`).join('')||empty()}`,copy('状态、关系、参数分别作为规则输入；复合谓词保留子谓词与运算符。这里只检查定义，绝不执行替代 Atlas 真值。','State, relation and parameter definitions are separate rule inputs. Composite predicates keep their subpredicates and operators. Definitions are inspected only; no substitute Atlas truth is evaluated.'))+section(copy('绑定事实与结果实例','Bound facts & result instances'),refs([...list(node.input_fact_ids),...results.map(result=>result.id)]),copy('定义依赖不等于运行时事实依赖。谓词结果引用实际事实，未连接时保持 UNKNOWN。','Definition dependencies differ from runtime fact dependencies. Predicate results reference actual facts and remain UNKNOWN without a connected evaluator.'));
  }
  function closedLoop() {
    const {graph,edges,byId}=model(), selected=byId.get(ui.selectedId);
    const command=selected?.collection==='commands'?selected.node:records(graph.commands).find(item=>list(item.target_entity_ids).includes(ui.selectedId)) || records(graph.commands).find(item=>item.id===ui.loopCommandId) || records(graph.commands)[0];
    if(!command) return '';
    ui.loopCommandId=command.id;
    const agent=byId.get(command.agent_id)?.node, strategy=byId.get(agent?.strategy_id)?.node, task=byId.get(command.task_id)?.node;
    const decisions=records(graph.decisions).filter(decision=>list(decision.command_ids).includes(command.id));
    const behaviors=list(command.composition?.behavior_ids).map(id=>byId.get(id)?.node).filter(Boolean);
    const moduleIds=[...new Set(behaviors.flatMap(behavior=>list(behavior.module_ids)))];
    const constraintIds=[...new Set([...list(strategy?.constraint_ids),...list(task?.constraint_ids),...behaviors.flatMap(behavior=>list(behavior.constraint_ids))])];
    const checks=records(graph.admission_checks).filter(check=>constraintIds.includes(check.constraint_id)||list(check.behavior_ids).some(id=>behaviors.some(behavior=>behavior.id===id))||behaviors.some(behavior=>behavior.id===check.behavior_id));
    const updatedFacts=records(graph.facts).filter(fact=>moduleIds.includes(fact.producer_module_id)&&list(command.target_entity_ids).includes(fact.entity_id));
    const feedbackEdges=edges.filter(edge=>edge.relation==='FEEDS_BACK'&&edge.target===agent?.id);
    const pathNodeIds=new Set([agent?.id,strategy?.id,command.id,...decisions.map(item=>item.id),...behaviors.map(item=>item.id),...moduleIds,...constraintIds,...checks.map(item=>item.id),...updatedFacts.map(item=>item.id),...list(strategy?.observable_fact_ids),...list(strategy?.objective_ids),...list(strategy?.request_ids),...feedbackEdges.map(edge=>edge.source)]);
    const pathEdges=reviewEdges().filter(edge=>pathNodeIds.has(edge.source)&&pathNodeIds.has(edge.target)&&['CALLS','INPUT_TO','OUTPUT_MUST_SATISFY','PRODUCES','GENERATES','COMPOSES','EXECUTED_BY','CONSTRAINED_BY','READS','USES','PERMITS','BLOCKS','UPDATES','OWNS','EVIDENCED_BY','FEEDS_BACK','BASED_ON','WAITS_FOR'].includes(edge.relation));
    const stage=(title,ids,relation,tone='')=>`<div class="gw-loop-stage ${tone}"><h4>${title}</h4>${refs(ids)}${relation?`<div class="gw-loop-verb">${esc(relation)}</div>`:''}</div>`;
    return `<section class="panel gw-loop"><header class="panel-header"><div><h2 class="panel-title">${copy('决策、约束准入与反馈闭环','Decision, constraint admission & feedback loop')}</h2><p class="panel-description">${copy('当前路径按命令引用展开；箭头显示声明的关系语义，未声明的反馈不补造。','The selected command expands its declared references. Arrows describe relation semantics; undeclared feedback is not invented.')}</p></div><code>${esc(command.id)}</code></header><div class="panel-body"><div class="gw-loop-inputs"><div><h4>${copy('观测事实与请求','Observed facts & requests')} · INPUT_TO</h4>${refs([...list(strategy?.observable_fact_ids),...list(strategy?.request_ids)])}</div><div><h4>${copy('软目标','Soft objectives')} · INPUT_TO</h4>${refs(strategy?.objective_ids)}</div><div class="gw-hard-constraints"><h4>${copy('适用约束 / 输出必须满足','Applicable constraints / output requirements')} · INPUT_TO / OUTPUT_MUST_SATISFY</h4>${refs(constraintIds)}</div></div><div class="gw-loop-path">${stage(copy('智能体','Agent'),[agent?.id].filter(Boolean),'CALLS')}${stage(copy('策略','Strategy'),[strategy?.id].filter(Boolean),'PRODUCES')}${stage(copy('决策','Decision'),decisions.map(item=>item.id),'GENERATES')}${stage(copy('命令','Command'),[command.id],'COMPOSES')}${stage(copy('行为组合','Behaviors'),behaviors.map(item=>item.id),'CONSTRAINED_BY')}${stage(copy('约束准入检查','Constraint admission'),checks.map(item=>item.id),'PERMITS / BLOCKS','gw-loop-gate')}${stage(copy('执行模块','Executing modules'),moduleIds,'UPDATES')}${stage(copy('更新事实声明','Updated fact declarations'),updatedFacts.map(item=>item.id),'BASED_ON')}${stage(copy('事件反馈','Event feedback'),feedbackEdges.map(edge=>edge.source),'FEEDS_BACK')}</div><details class="gw-path-edges"><summary>${copy('此路径的实际有向类型边','Actual directed typed edges in this path')} · ${pathEdges.length}</summary>${pathEdges.map(edge=>`<button class="gw-path-edge" data-gw-action="edge" data-id="${esc(edge.id)}"><code>${esc(edge.source)}</code><span>${esc(edge.relation)} →</span><code>${esc(edge.target)}</code><small>${esc(edge.role||'')}</small></button>`).join('')}</details><p class="gw-loop-note">${copy('硬约束不能用软目标覆盖。准入检查与谓词解释是独立环节；执行中约束变化的暂停、终止或重规划由反馈策略显式声明。','Hard constraints cannot be overridden by soft objectives. Admission checks and predicate interpretation are independent; pause, abort or replan after a changed constraint must be explicitly declared in a feedback policy.')}</p><div class="gw-feedback-policies">${refs(records(graph.feedback_policies).filter(policy=>policy.agent_id===agent?.id).map(policy=>policy.id))}</div></div></section>`;
  }

  function inspection() {
    const current=model(), entry=current.byId.get(ui.selectedId);
    if(!entry) return empty();
    const visible=reviewEdges();
    const incoming=visible.filter(edge=>edge.target===entry.id), outgoing=visible.filter(edge=>edge.source===entry.id);
    const relevant=current.validation.errors.filter(issue=>issue.node_id===entry.id||((entry.derived||entry.read_only)&&issue.node_id===(entry.owner_rule_id||entry.node.owner_rule_id||entry.node.rule_id)));
    const selection=hooks.getSelection?.() || {};
    const bridge=entry.collection==='entities' && entry.node.scene_entity_id;
    const readonly=entry.derived===true||entry.read_only===true;
    const ownerRuleId=entry.owner_rule_id||entry.node.owner_rule_id||entry.node.rule_id;
    const editId=readonly?ownerRuleId:entry.id;
    return `<section class="panel gw-inspection" aria-labelledby="gw-inspect-title"><header class="panel-header"><div><div class="eyebrow">${esc(collectionLabel(entry.collection))}</div><h2 id="gw-inspect-title">${esc(nodeLabel(entry.node,entry.collection))}</h2><code>${esc(entry.id)}</code></div><div class="gw-actions">${bridge ? `<button class="button small" data-gw-action="bridge" data-id="${esc(bridge)}" ${selection.locked&&selection.entityId!==bridge?'disabled':''}>${copy('同步场景选择','Select in scene')}</button>` : ''}${editId?`<button class="button small primary" data-gw-action="edit" data-id="${esc(editId)}">${readonly?copy('编辑所属规则','Edit owning rule'):copy('编辑记录','Edit record')}</button>`:''}</div></header><div class="panel-body"><div class="gw-record-status">${badge(readonly?copy('派生 · 只读','Derived · read only'):copy('已声明','Declared'),'blue')}${badge(relevant.length?copy('静态检查待修复','Static checks need repair'):copy('静态检查已完成','Static checked'),relevant.length?'red':'green')}${entry.node.binding ? badge(`${entry.node.binding.mode} · ${entry.node.binding.adapter}`) : ''}</div>${bridge ? `<p class="field-hint">${copy('场景显式映射','Explicit scene mapping')}: ${esc(bridge)} · ${selection.locked?copy('共享选择已锁定','Shared selection is locked'):copy('沿用现有共享选择与游标','Uses the existing shared selection and cursor')} · ${Number(selection.cursor||0).toFixed(1)} s</p>` : ''}${typedDetails(entry)}${section(copy('直接依赖图','Direct dependency graph'),`<div class="gw-dependency-map"><div class="gw-edge-column"><h4>${copy('引用本记录','Refers to this record')} <span>${incoming.length}</span></h4>${incoming.map(edge=>edgeCard(edge,'incoming')).join('')||empty()}</div><div class="gw-focus-node"><span class="gw-connector" aria-hidden="true">→</span>${nodeButton(entry.id,'central')}<span class="gw-connector" aria-hidden="true">→</span></div><div class="gw-edge-column"><h4>${copy('本记录引用','This record refers to')} <span>${outgoing.length}</span></h4>${outgoing.map(edge=>edgeCard(edge,'outgoing')).join('')||empty()}</div></div>`)}${edgeInspector()}<details class="gw-raw"><summary>${copy('完整类型化记录 / 绑定与来源','Complete typed record / bindings & sources')}</summary><pre class="gw-json">${esc(json(entry.node))}</pre></details></div></section>`;
  }
  function diagnostics(validation) {
    const issues=[...validation.errors,...validation.warnings];
    return `<details class="gw-diagnostics" ${validation.errors.length?'open':''}><summary>${copy('静态诊断','Static diagnostics')} · ${validation.errors.length} ${copy('错误','errors')} / ${validation.warnings.length} ${copy('提示','notices')}</summary>${issues.length ? issues.map(issue=>`<div class="gw-issue ${issue.severity==='error'?'error-text':''}"><code>${esc(issue.code)} · ${esc(issue.path)}</code><p>${esc(getLocale()==='zh-CN'?chineseIssue(issue):issue.message)}</p>${getLocale()==='zh-CN'?`<details><summary>${copy('技术详情（原始诊断）','Technical detail (original diagnostic)')}</summary><p>${esc(issue.message)}</p></details>`:''}${issue.node_id ? `<button class="gw-text-link" data-gw-action="select" data-id="${esc(issue.node_id)}">${copy('定位记录','Inspect record')} ${esc(issue.node_id)}</button>`:''}</div>`).join('') : `<p>${copy('类型、引用及声明范围检查通过。','Type, reference and declared-scope checks passed.')}</p>`}</details>`;
  }
  function fixtureEvidence(result) {
    const phaseLabel=phase=>phase==='start'?copy('开始准入','Start admission'):phase==='continue'?copy('持续准入','Continue admission'):phase;
    const checks=records(result.check_results);
    const admissions=checks.length?section(copy('约束变化与行为准入','Constraint change & behavior admission'),`<div class="gw-admission-results">${checks.map(check=>`<article><div class="gw-record-status">${badge(check.verdict==='PERMIT'?copy('样例允许 · PERMIT','Fixture permit · PERMIT'):check.verdict==='BLOCK'?copy('样例阻止 · BLOCK','Fixture block · BLOCK'):copy('未知 · UNKNOWN','Unknown · UNKNOWN'),check.verdict==='PERMIT'?'green':'amber')}<span>${esc(phaseLabel(check.phase))}</span></div><strong>${esc(check.at_ns)} ns</strong><p>${esc(check.check_id)} · ${esc(check.constraint_id)}</p><p>${copy('观测值 / 期望值','Observed / expected')}: ${esc(json(check.observed))} / ${esc(json(check.expected))}</p>${refs(records(check.evidence).map(item=>item.fact_id))}</article>`).join('')}</div><div class="gw-fixture-feedback">${records(result.feedback).map(feedback=>`<div><strong>${copy('声明反馈','Declared feedback')}: ${esc(feedback.response)}</strong><span>${esc(feedback.agent_id)} · ${esc(feedback.policy_id)} · ${esc(feedback.at_ns)} ns</span>${refs(feedback.behavior_ids)}</div>`).join('')}</div>`,copy('这里的 PERMIT / BLOCK 仅由受限的本地样例检查产生，不能建立真实飞行许可或原生谓词真值。','PERMIT / BLOCK comes only from the bounded local fixture check. It establishes neither real operational permission nor native predicate truth.')):'';
    const behaviors=section(copy('行为计划解析与准入状态','Behavior plan resolution & admission state'),`<div class="gw-behavior-results">${records(result.behaviors).map(behavior=>`<div>${nodeButton(behavior.id,'compact')}<span>${esc(behavior.status)}</span>${badge(copy('无物理效果','No physical effect'))}${behavior.admission?`<div class="gw-behavior-admission"><strong>${copy('整体准入','Aggregate admission')}: ${esc(behavior.admission.verdict)}</strong><p>${copy('尚未检查的约束','Unchecked constraints')}: ${esc(list(behavior.admission.unchecked_constraint_ids).join(', ')||'—')}</p></div>`:''}</div>`).join('')||empty()}</div>`);
    const predicates=section(copy('谓词结果与实际事实依据','Predicate results & actual fact basis'),`<div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>${copy('谓词定义','Predicate definition')}</th><th>${copy('真值 / 范围','Truth / scope')}</th><th>${copy('事实证据 / 历史','Fact evidence / history')}</th></tr></thead><tbody>${records(result.predicate_results).map(record=>`<tr><td>${nodeButton(record.predicate_id,'compact')}</td><td>${esc(record.truth)} / ${esc(record.scope_status)}</td><td>${refs(records(record.evidence).map(item=>item.fact_id))}<small>${copy('历史','History')}: ${esc(record.history_status)} · ${esc(record.at_ns)} ns</small></td></tr>`).join('')}</tbody></table></div>`);
    const stages=section(copy('各阶段真实状态','Stage-specific status'),`<div class="gw-stage-statuses">${Object.entries(result.stages||{}).map(([stage,status])=>`<div><strong>${esc(stage)}</strong><span>${esc(status)}</span></div>`).join('')}</div>`,copy('样例成功只表示声明解析和受限检查完成。策略为人工决策回放；模块、谓词和执行器的执行状态分别列出。','A successful fixture means declaration resolution and bounded checks completed. Strategy is authored decision replay; module, predicate and actuator execution states are reported separately.'));
    return stages+admissions+behaviors+predicates;
  }
  function traceTimeline(result) {
    const labels={fixture_started:['开始样例解析','Fixture resolution started'],strategy_called:['智能体调用策略','Agent calls strategy'],decision_resolved:['解析声明决策','Declared decision resolved'],command_composed:['组合命令行为','Command behaviors composed'],behavior_bound:['检查行为绑定','Behavior bindings checked'],event_emitted:['产生样例事件实例','Fixture event occurrence emitted'],admission_checked:['受限样例准入检查','Bounded fixture admission check'],fixture_state_updated:['更新样例事实','Fixture facts updated'],agent_feedback:['反馈到智能体','Feedback reaches agent'],fixture_finished:['样例解析完成','Fixture resolution completed']};
    return `<ol class="gw-trace-list">${records(result.trace).map(step=>`<li><span class="gw-trace-index">${Number(step.step)+1}</span><div><strong>${labels[step.kind]?copy(...labels[step.kind]):esc(step.kind)}</strong><p><code>${esc(step.node_id)}</code> · ${esc(step.at_ns)} ns</p>${list(step.evidence_ids).length?`<small>${copy('依据','Evidence')}: ${esc(step.evidence_ids.join(', '))}</small>`:''}<details><summary>${copy('机器记录','Machine record')}</summary><pre class="gw-json">${esc(json(step))}</pre></details></div></li>`).join('')}</ol>`;
  }

  function fixturePanel() {
    const {graph,validation}=model(), current=ui.result && ui.resultGraph===graph;
    const result=current?ui.result:null;
    return `<section class="panel gw-fixture"><header class="panel-header"><div><h2 class="panel-title">${copy('同一配置图的案例执行','Case execution from one configuration graph')}</h2><p class="panel-description">${copy('只执行本地声明解析与确定性样例，不启动 BENCH、Atlas 或物理动作。','Runs local declared-graph resolution and deterministic fixtures only; no BENCH, Atlas or physical actuation.')}</p></div></header><div class="panel-body"><p class="field-hint">${copy('案例按声明 observed_at_ns 执行，与回放查看游标独立；不会伪造游标与事实同步。','Cases execute at their declared observed_at_ns, independently of the replay view cursor; facts are not presented as cursor-synchronized evidence.')}</p><div class="gw-fixture-controls"><label for="gw-scenario">${copy('案例','Case')}</label><select id="gw-scenario" data-gw-field="scenario">${records(graph.scenarios).map(scenario=>`<option value="${esc(scenario.id)}" ${ui.scenarioId===scenario.id?'selected':''}>${esc(nodeLabel(scenario,'scenarios'))}</option>`).join('')}</select><button class="button primary" data-gw-action="execute" ${validation.valid?'':'disabled'}>${copy('运行本地案例','Run local case')}</button></div>${ui.result&&!current?`<div class="notice warning">${copy('配置已变更。上次案例结果已失效，请重新执行。','The configuration changed. The previous case result is stale; run it again.')}</div>`:''}${result?`<div class="gw-result" role="status"><h3>${copy('已执行案例','Executed case')}: ${esc(result.scenario_id)} · ${esc(result.at_ns)} ns</h3><div class="gw-record-status">${badge(copy('样例已执行','Fixture executed'),'green')}${badge(copy('谓词真值 UNKNOWN','Predicate truth UNKNOWN'),'amber')}${badge(copy('真实服务未连接','Real services disconnected'))}</div><div class="gw-result-summary"><div><strong>${list(result.decisions).length}</strong><span>${copy('决策记录','Decision records')}</span></div><div><strong>${list(result.events).length}</strong><span>${copy('事件记录','Event records')}</span></div><div><strong>${list(result.commands).length}</strong><span>${copy('命令声明','Command declarations')}</span></div><div><strong>${list(result.resource_usage).length}</strong><span>${copy('资源记录','Resource records')}</span></div></div><div class="gw-assessments">${['feasibility','permission','suitability'].map((key,index)=>`<span>${[copy('可行性','Feasibility'),copy('许可','Permission'),copy('适宜性','Suitability')][index]}: <strong>${esc(result.assessments?.[key]||'UNKNOWN')}</strong></span>`).join('')}</div>${fixtureEvidence(result)}<details open><summary>${copy('执行轨迹','Execution trace')}</summary>${traceTimeline(result)}</details><details><summary>${copy('完整结果（含行为、资源、事件与命令）','Complete result (behaviors, resources, events & commands)')}</summary><pre class="gw-json">${esc(json(result))}</pre></details></div>`:`<p class="field-hint">${copy('尚未执行当前配置。声明状态和静态检查不会自动升级为执行结果。','This graph has not been executed. Declarations and static checks do not become execution evidence automatically.')}</p>`}</div></section>`;
  }
  function editorPanel() {
    const editor=ui.editor;
    if(!editor) return '';
    const changed=editor.review?.diff||[];
    return `<section class="panel gw-editor" aria-labelledby="gw-editor-title"><header class="panel-header"><div><h2 class="panel-title" id="gw-editor-title">${copy('结构化记录编辑','Structured record editor')} · ${esc(editor.id)}</h2><p class="panel-description">${copy('可编辑绑定、模块读写范围、智能体控制范围、策略输入输出、行为组合与法律约束引用。稳定 ID 保持不变。','Edit bindings, module read/write scopes, agent control scopes, strategy I/O, behavior composition and legal references. The stable ID is unchanged.')}</p></div><button class="button small" data-gw-action="cancel-edit">${copy('取消编辑','Cancel editing')}</button></header><div class="panel-body"><div class="field"><label for="gw-record-json">${copy('类型化记录 JSON','Typed record JSON')} <span>${esc(collectionLabel(editor.collection))}</span></label><textarea id="gw-record-json" data-gw-field="record" class="mono" rows="17" spellcheck="false" aria-describedby="gw-edit-hint">${esc(editor.text)}</textarea><p class="field-hint" id="gw-edit-hint">${copy('编辑内容在“应用”前不会写入草稿。先检查类型、引用、单位、控制权和读写冲突。','Pending edits do not change the draft until Apply. Review type, reference, unit, control-scope and read/write checks first.')}</p></div>${editor.error?`<div class="notice warning" role="alert">${esc(editor.error)}</div>`:''}${editor.validation?diagnostics(editor.validation):''}<div class="gw-actions"><button class="button" data-gw-action="review">${copy('静态校验并查看差异','Validate & review changes')}</button>${editor.review?`<button class="button primary" data-gw-action="apply" ${changed.length?'':'disabled'}>${copy('应用到共享草稿','Apply to shared draft')}</button>`:''}</div>${editor.review?`<div class="gw-change-review"><h3>${copy('应用前差异','Changes before Apply')} · ${changed.length}</h3>${changed.length ? changed.map(change=>`<div class="gw-diff"><code>${esc(change.path)}</code><div><span>− ${esc(json(change.before)??'(unset)')}</span><span>+ ${esc(json(change.after)??'(unset)')}</span></div></div>`).join('') : `<p>${copy('没有变更。','No changes.')}</p>`}</div>`:''}</div></section>`;
  }
  function render() {
    const current=model();
    if(!current) return `<section class="panel"><div class="panel-body"><h2>${copy('统一配置图','Unified configuration graph')}</h2><p>${copy('当前草稿没有统一配置图。添加独立样例后可在同一份草稿中保存、校验和导出。','This draft has no unified graph. Add the independent example to save, validate and export it with this same draft.')}</p><button class="button primary" data-gw-action="initialize">${copy('添加样例配置图','Add example graph')}</button></div></section>`;
    const {graph,nodes,validation}=current;
    const collections=[...GRAPH_COLLECTIONS,...[...new Set(nodes.map(node=>node.collection))].filter(key=>!GRAPH_COLLECTIONS.some(item=>item.key===key)).map(key=>({key}))];
    const filtered=nodes.filter(entry=>(ui.collection==='all'||entry.collection===ui.collection) && `${entry.id} ${entry.node.label} ${nodeLabel(entry.node,entry.collection)}`.toLowerCase().includes(ui.query.toLowerCase()));
    const executed=ui.result&&ui.resultGraph===graph;
    return `<div class="graph-workbench" data-graph-workbench><section class="gw-overview"><div><div class="eyebrow">UNIFIED CONFIGURATION GRAPH</div><h2>${copy('从事实到决策，审阅每一条依赖','Review every dependency, from fact to decision')}</h2><p>${copy('与场景表单共用一份草稿、版本和导出。点击类型或记录，追踪来源、模块、策略、行为与约束。','One draft, version history and export shared with the scene forms. Select a type or record to inspect sources, modules, strategies, behaviors and constraints.')}</p></div><span class="gw-node-total"><strong>${nodes.length}</strong>${copy('个配置图节点','configuration graph nodes')}</span></section><div class="gw-readiness" aria-label="${copy('证据成熟度','Evidence readiness')}"><div>${badge(copy('已声明','Declared'),'blue')}<span>${nodes.length} ${copy('条记录','records')}</span></div><div>${badge(copy('静态检查','Static checked'),validation.valid?'green':'red')}<span>${validation.errors.length} ${copy('错误','errors')}</span></div><div>${badge(copy('样例执行','Fixture executed'),executed?'green':'muted')}<span>${executed?esc(ui.result.scenario_id):copy('未执行当前配置','Current graph not executed')}</span></div><div>${badge(copy('真实连接','Real connected'))}<span>${copy('未连接 · 0','Disconnected · 0')}</span></div></div><div class="gw-layer-map">${[...layers,{title:['其他类型','Other types'],keys:collections.map(item=>item.key).filter(key=>!layers.some(layer=>layer.keys.includes(key)))}].filter(layer=>layer.keys.some(key=>collections.some(item=>item.key===key))).map(layer=>`<section><h3>${copy(...layer.title)}</h3>${layer.keys.filter(key=>collections.some(collection=>collection.key===key)).map(key=>`<button class="gw-collection-chip ${ui.collection===key?'active':''}" data-gw-action="collection" data-key="${key}" aria-pressed="${ui.collection===key}"><span>${esc(collectionLabel(key))}</span><strong>${nodes.filter(entry=>entry.collection===key).length}</strong></button>`).join('')}</section>`).join('')}</div><div class="gw-shared-selection"><strong>${copy('共享场景选择','Shared scene selection')}: ${esc(hooks.getSelection?.().entityId||copy('未选择','None'))}</strong><span>${copy('查看游标','View cursor')}: ${Number(hooks.getSelection?.().cursor||0).toFixed(1)} s</span><span>${hooks.getSelection?.().locked?copy('选择已锁定；配置检查不改变跟踪实体','Selection locked; configuration inspection does not change the tracked entity'):copy('点击场景关联实体同步选择','Select a scene-linked entity to update the shared selection')}</span></div><p class="gw-boundary-note">${copy('来源：独立人工样例。仅包含当前声明的目录子集；Atlas 未加载，不生成替代布尔真值。BENCH 仍是物理时钟与生产视图。','Source: independently authored fixtures. This is only the declared catalog subset; Atlas is not loaded and no substitute Boolean truth is computed. BENCH remains the physical clock and production view.')}</p>${ui.notice?`<div class="notice info" role="status">${esc(ui.notice)}</div>`:''}${editorPanel()}${relationReview()}${closedLoop()}<div class="gw-review-grid"><aside class="panel gw-inventory"><div class="panel-header"><h2 class="panel-title">${esc(ui.collection==='all'?copy('全部记录','All records'):collectionLabel(ui.collection))} <span>${filtered.length}</span></h2></div><div class="panel-body"><div class="field"><label for="gw-query">${copy('查找记录','Find a record')}</label><input id="gw-query" data-gw-field="query" type="search" value="${esc(ui.query)}" placeholder="${copy('名称或稳定 ID','Name or stable ID')}"></div><button class="gw-text-link" data-gw-action="collection" data-key="all">${copy('查看全部类型','Browse all types')}</button><div class="gw-node-list">${filtered.map(entry=>nodeButton(entry.id)).join('')||empty()}</div></div></aside>${inspection()}</div>${diagnostics(validation)}${fixturePanel()}</div>`;
  }
  function captureEditor() {
    const input=globalThis.document?.querySelector('#gw-record-json');
    if(ui.editor&&input&&ui.editor.text!==input.value) {ui.editor.text=input.value;ui.editor.review=null;ui.editor.validation=null;ui.editor.error=null;}
  }
  function reviewEdit() {
    captureEditor();
    const editor=ui.editor, {graph,config}=model();
    if(!editor) return;
    editor.review=null;editor.validation=null;editor.error=null;
    try {
      if(json(graph)!==editor.baseGraph) throw new Error(copy('草稿在编辑期间已改变。请取消后重新打开该记录，以免覆盖新改动。','The draft changed while this editor was open. Cancel and reopen the record to avoid overwriting newer changes.'));
      const candidate=JSON.parse(editor.text);
      const unsafe=value=>value&&typeof value==='object'&&(Object.keys(value).some(key=>['__proto__','constructor','prototype'].includes(key))||Object.values(value).some(unsafe));
      if(!candidate || Array.isArray(candidate) || typeof candidate!=='object' || unsafe(candidate)) throw new Error(copy('请输入不含危险键的 JSON 对象。','Use a JSON object without unsafe keys.'));
      if(candidate.id!==editor.id) throw new Error(copy('稳定 ID 不可在此更改；引用关系必须保持明确。','The stable ID cannot be changed here; references must stay explicit.'));
      const next=structuredClone(graph), index=next[editor.collection].findIndex(node=>node.id===editor.id);
      next[editor.collection][index]=candidate;
      editor.validation=validateGraph(next,{...config,graph:next});
      if(!editor.validation.valid) return;
      const before=graph[editor.collection][index];
      const diff=[];
      const visit=(oldValue,newValue,path)=>{
        if(json(oldValue)===json(newValue)) return;
        if(oldValue&&newValue&&typeof oldValue==='object'&&typeof newValue==='object'&&!Array.isArray(oldValue)&&!Array.isArray(newValue)) for(const key of new Set([...Object.keys(oldValue),...Object.keys(newValue)])) visit(oldValue[key],newValue[key],`${path}.${key}`);
        else diff.push({path,before:oldValue,after:newValue});
      };
      visit(before,candidate,`${editor.collection}.${editor.id}`);
      editor.review={graph:next,diff,text:editor.text};
    } catch(error) {editor.error=error instanceof SyntaxError?copy('JSON 格式无效，请检查引号、逗号与括号。','Invalid JSON. Check quotes, commas and brackets.'):error.message;}
  }
  function handleClick(event) {
    const element=event.target.closest?.('[data-gw-action]');
    if(!element) return false;
    captureEditor();
    const action=element.dataset.gwAction, current=model();
    ui.notice=null;
    if(action==='initialize') {hooks.commitGraph(structuredClone(DEFAULT_GRAPH));return true;}
    if(!current) return true;
    if(action==='select') {
      ui.selectedId=element.dataset.id;
      const entry=current.byId.get(ui.selectedId), selection=hooks.getSelection?.()||{};
      if(entry?.collection==='entities'&&entry.node.scene_entity_id&&!selection.locked) {hooks.selectEntity?.(entry.node.scene_entity_id);return true;}
    }
    else if(action==='edge') {ui.edgeId=element.dataset.id;const edge=current.displayEdges.find(item=>item.id===ui.edgeId)||current.edges.find(item=>item.id===ui.edgeId);if(edge&&edge.source!==ui.selectedId&&edge.target!==ui.selectedId)ui.selectedId=edge.source;}
    else if(action==='relation-layer') {ui.relationLayer=element.dataset.key;ui.edgeId=null;}
    else if(action==='close-edge') ui.edgeId=null;
    else if(action==='collection') {ui.collection=element.dataset.key;ui.query='';}
    else if(action==='bridge') {hooks.selectEntity?.(element.dataset.id);return true;}
    else if(action==='edit') {
      if(ui.editor&&ui.editor.id!==element.dataset.id) {ui.notice=copy('请先应用或取消正在编辑的记录。','Apply or cancel the open record before editing another.');}
      else if(!ui.editor) {
        const entry=current.byId.get(element.dataset.id);
        if(!entry||entry.derived===true||entry.read_only===true||!Array.isArray(current.graph[entry.collection])) {ui.notice=copy('派生节点只读；请编辑所属规则。','Derived nodes are read only; edit the owning rule.');hooks.requestRender();return true;}
        ui.editor={id:entry.id,collection:entry.collection,text:json(entry.node),baseGraph:json(current.graph),review:null,error:null,validation:null};
      }
    } else if(action==='cancel-edit') ui.editor=null;
    else if(action==='review') reviewEdit();
    else if(action==='apply') {
      const editor=ui.editor;
      if(editor?.review&&editor.text===editor.review.text&&editor.baseGraph===json(current.graph)) {
        const next=editor.review.graph;
        ui.editor=null;ui.notice=copy('记录已应用到共享草稿；请保存版本以保留快照。','Record applied to the shared draft. Save a version to keep a snapshot.');
        hooks.commitGraph(next);return true;
      }
      if(editor) {editor.review=null;editor.error=copy('记录或草稿已改变，请重新校验和审阅。','The record or draft changed. Validate and review it again.');}
    } else if(action==='execute') {
      try {
        ui.result=executeGraphFixture(current.graph,ui.scenarioId,{config:current.config});
        ui.resultGraph=ui.result.status==='fixture_executed'?current.graph:null;
        if(ui.result.status!=='fixture_executed') {ui.result=null;ui.notice=copy('当前配置未通过案例检查；未执行。','The current configuration did not pass case checks; nothing executed.');}
      }
      catch(error) {ui.notice=error.message;ui.result=null;ui.resultGraph=null;}
    }
    hooks.requestRender();
    return true;
  }
  function handleInput(event) {
    const field=event.target.dataset?.gwField;
    if(!field) return false;
    if(field==='record') {captureEditor();return true;}
    if(field==='query') {
      const position=event.target.selectionStart;
      ui.query=event.target.value;hooks.requestRender();
      const input=document.querySelector('#gw-query');input?.focus();if(typeof position==='number'&&input?.type!=='search')input.setSelectionRange(position,position);
    }
    if(field==='scenario') {ui.scenarioId=event.target.value;hooks.requestRender();}
    return true;
  }
  return {render,handleClick,handleInput,captureEditor,invalidate(){cache=null;if(ui.result)ui.resultGraph=null;if(ui.editor){ui.editor.review=null;ui.editor.validation=null;}},getState:()=>ui};
}
