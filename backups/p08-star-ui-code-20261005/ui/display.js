/** Read-only presentation helpers. Names never enter graph records or identities. */
import {kindOf,relationOf,textLabel} from './canonical-model.js';
import {STAGE_LABELS,ROLE_LABELS,RESOURCE_LABELS,CASE_LABELS,RELATION_LABELS} from './display-vocabulary.js';
const KIND_LABELS={entity:'实体',entity_type:'实体类型',owner_role:'声明角色',agent:'决策代理',resource:'资源',resource_allocation:'资源分配方案',space_time_allocation:'时空分配方案',lease_request:'通道申请',lease_approval:'审批记录',spatial_zone:'空间范围',scenario_definition:'案例',task:'任务',objective:'目标',request:'任务请求',decision:'任务决策',strategy_definition:'决策策略',strategy_invocation:'策略评估',arbitration_policy:'仲裁策略',authority_record:'权限证据声明',constraint_definition:'约束要求',constraint_check:'约束检查',constraint_check_result:'夹具检查结果',check_binding:'检查绑定',command_definition:'指令定义',command_attempt:'指令',behavior_definition:'行为定义',behavior_execution:'行为',module:'功能模块',module_execution:'模块记录',capability_declaration:'能力声明',fact:'状态样本',state_specification:'状态定义',state_definition:'状态定义',prepared_input:'准备输入',parameter_definition:'参数定义',time_context:'查询时间上下文',predicate_definition:'谓词定义',predicate_evaluation:'谓词求值',predicate_result:'谓词结果',predicate_expectation:'夹具预期',evaluation_binding:'求值绑定',rule_definition:'规则',expression:'表达式',event_definition:'事件定义',event_occurrence:'候选事件',evidence_source:'证据来源',relation_definition:'关系定义',relation_instance:'关系实例'};
const FIELD_LABELS={desired_target_enu_m:'期望目标（ENU）',commanded_target_enu_m:'指令目标（ENU）',actual_pose_enu_m:'模型位姿（ENU）',candidate_allocation_ledger:'候选分配账本',onboard_gpu:'机载 GPU',calibration_id:'标定版本',model_id:'模型版本',conditioning_scope:'条件范围',fall_probability:'疑似跌倒概率',ul_available_bps:'上行可用带宽',ul_demand_bps:'上行带宽需求',fx_px:'相机焦距',width_px:'图像宽度',height_px:'图像高度',angular_rate_rad_s:'相机角速度',exposure_s:'曝光时间',roi_pixels:'关注区域像素数',saturated_pixels:'饱和像素数',black_pixels:'暗像素数',wind_mps:'风速',payload_mass:'载荷质量',flight_control:'飞控槽',power_w:'功率预算',cpu_millicores:'CPU 算力预算',uplink_bps:'上行带宽预算'};
const contexts=new WeakMap();
function raw(value){return typeof value==='string'?value.trim():'';}
function humanToken(value){const v=raw(value);if(!v)return '';return STAGE_LABELS[v]||ROLE_LABELS[v]||RESOURCE_LABELS[v]||FIELD_LABELS[v]||v.replace(/[_-]+/g,' ');}
function facet(n,key){const x=n.facets?.[key]||n.semantics?.facets?.[key];return x?.status==='known'?x.value:undefined;}
function reference(value){return typeof value==='string'?value:value?.node_id||value?.id;}
function context(index){if(!index?.nodes)return{index,cache:new Map(),active:new Set(),spec:null};if(!contexts.has(index)){const scenario=[...index.nodes.values()].find(n=>kindOf(n)==='scenario_definition');contexts.set(index,{index,cache:new Map(),active:new Set(),spec:scenario?.payload?.case||null});}return contexts.get(index);}
function descriptive(value){const s=raw(textLabel(value));return s&&!/^\w+(?::[\w.-]+)+$/.test(s)&&!s.includes('_')&&!/^[A-Za-z]+\.[\w.]+$/.test(s)&&!Object.values(KIND_LABELS).includes(s)?s:'';}
function related(n,ctx,relations,kinds=null){for(const e of ctx.index?.adjacent.get(n.id)||[]){if(e.source!==n.id||!relations.includes(relationOf(e)))continue;const other=ctx.index.nodes.get(e.target);if(other&&(!kinds||kinds.includes(kindOf(other))))return other;}return null;}
function operation(n,ctx,seen=new Set()){
 if(!n||seen.has(n.id)||seen.size>4)return '';seen.add(n.id);const p=n.payload||{};
 const own=p.arguments?.operation||p.semantic_evidence?.arguments?.operation||p.operation||p.action||p.stage||p.modeled_operation;if(typeof own==='string')return own;
 const id=reference(facet(n,'definition'))||reference(p.semantic_evidence?.definition);if(id&&ctx.index?.nodes.has(id)){const result=operation(ctx.index.nodes.get(id),ctx,seen);if(result)return result;}
 const next=related(n,ctx,['instance_of','requested_by_attempt','execution_of_module','execution_module_binding','module_execution_for_behavior'],['behavior_definition','command_definition','command_attempt','module','behavior_execution']);if(next){const result=operation(next,ctx,seen);if(result)return result;}
 return '';
}
function meaningfulSource(n){const p=n.payload||{};for(const value of [p.name,p.title,n.label===n.id?null:n.label]){const s=descriptive(value);if(s&&!/^(task|objective|scenario|decision|strategy|request|normal|failure|unknown|recovery)$/i.test(s))return s;}return '';}
function caseName(spec){return spec?CASE_LABELS[spec.title]||spec.title||'编写任务':'';}
function fieldName(id){const leaf=String(id||'').split('.').at(-1);return FIELD_LABELS[leaf]||humanToken(leaf);}
function label(n,ctx){
 const p=n.payload||{},k=kindOf(n),type=KIND_LABELS[k]||humanToken(k),source=meaningfulSource(n);
 if(k==='scenario_definition')return '案例 · '+caseName(p.case||ctx.spec);
 if(k==='task')return p.order&&typeof p.order==='object'&&p.order.order_id?'任务 · '+humanToken(p.order.task)+' '+p.order.order_id:'任务 · '+(caseName(ctx.spec)||source||'编写任务');
 if(k==='objective')return '目标 · '+(CASE_LABELS[p.desired]||humanToken(p.description)||caseName(ctx.spec)||source||'待核对目标');
 if(k==='decision'){
  if(p.probe||p.decision_kind==='assessment_only')return '预算评估 · '+({admit:'可接纳',reject:'不接纳',hold:'暂缓',unknown:'信息不足'}[p.status]||humanToken(p.status)||'待定');
  return {preflight_ready:'拟定任务决策',preflight_blocked:'任务决策 · 技术受阻',preflight_unresolved:'任务决策 · 信息待定',preflight_unknown:'任务决策 · 信息待定'}[p.technical_status]||'任务决策';
 }
 if(k==='strategy_invocation')return /completion/.test(n.label||'')?'完成条件评估':'任务策略评估';
 if(k==='strategy_definition')return source||'任务处理策略';
 if(k==='arbitration_policy')return '任务仲裁规则';
 if(k==='agent'){const token=raw(n.label).split(':').at(-1);return ROLE_LABELS[p.role]||ROLE_LABELS[token]||{edge_agent:'边缘决策代理',onboard_agent:'机载决策代理',uav_agent:'无人机决策代理'}[token]||source||'决策代理';}
 if(['entity','entity_type','owner_role'].includes(k)){const token=p.scenario_actor||p.role||p.declared_owner_label||raw(n.label).split(':').at(-1);return ROLE_LABELS[token]||source||humanToken(token)||type;}
 if(k==='resource'){const dimension=p.dimension||facet(n,'capacity_dimensions')?.[0]?.dimension||p.semantic_evidence?.capacity_dimensions?.[0]?.dimension||raw(n.label).split(':').at(-1);return RESOURCE_LABELS[dimension]||humanToken(dimension)||'资源';}
 if(k==='resource_allocation'){const ref=reference(p.resource_ref)||reference(facet(n,'resource'));const target=ctx.index?.nodes.get(ref);return '分配方案 · '+(target?resolve(target,ctx):humanToken(p.amount?.dimension||facet(n,'amount')?.dimension)||'资源');}
 if(k==='space_time_allocation')return '通道时空分配方案';
 if(k==='lease_request')return '申请受限通道';
 if(k==='lease_approval')return '通道审批记录';
 if(k==='spatial_zone')return '受限通道范围';
 if(['command_attempt','command_definition','behavior_definition','behavior_execution','module','module_execution','capability_declaration'].includes(k)){const op=operation(n,ctx);if(op)return ({command_attempt:'指令',command_definition:'指令定义',behavior_definition:'行为定义',behavior_execution:'行为',module:'模块',module_execution:'模块记录',capability_declaration:'能力'}[k]||type)+' · '+humanToken(op);if(p.execution_context==='standalone_fixture_injection')return '夹具输入注入';if(p.not_native_atlas)return '供值规则求值器';return source||type;}
 if(['constraint_definition','constraint_check','constraint_check_result','check_binding','authority_record'].includes(k)){const axis=facet(n,'assessment_axis')||p.assessment_axis;const name={task_suitability:'任务适配',permission_compliance:'权限条件'}[axis]||humanToken(axis);return name?(k==='constraint_check_result'?'夹具检查 · ':type+' · ')+name:source||type;}
 if(k==='event_occurrence')return p.candidate_only||p.emitted===false||facet(n,'emitted')===false||/candidate/.test(p.status||'')?'候选事件':source||'事件记录';
 if(k==='event_definition')return source||'事件成立条件';
 if(k==='fact'||k==='prepared_input'){
  const field=p.field_id||p.source_field_id;if(field)return (k==='fact'?'样本 · ':'输入 · ')+fieldName(field);
  const parent=related(n,ctx,['state_input_to','instance_of'],['state_specification','state_definition']);if(parent)return '样本 · '+fieldName(parent.original_id);
  return source||type;
 }
 if(k==='state_specification'||k==='state_definition')return source||'字段 · '+fieldName(n.original_id||p.id||n.label);
 if(['predicate_evaluation','predicate_result','predicate_expectation','evaluation_binding'].includes(k)){const ref=reference(facet(n,'definition'))||reference(p.semantic_evidence?.definition);const target=ctx.index?.nodes.get(ref);return type+' · '+(target?resolve(target,ctx):fieldName(p.actual_result?.target_id||p.source_engine_result?.target_id)||'规则条件');}
 if(k==='parameter_definition')return '参数 · '+humanToken(p.name||raw(n.label).split(':').at(-1));
 if(k==='expression')return '表达式 · '+humanToken(p.op||p.operator||p.expression?.op||'规则项');
 if(k==='request')return '任务处理请求';
 if(k==='time_context')return '查询与可用性截止';
 return source||type;
}
function resolve(n,ctx){if(!n)return '';const useCache=n===ctx.index?.nodes.get(n.id);if(useCache&&ctx.cache.has(n.id))return ctx.cache.get(n.id);if(ctx.active.has(n.id))return KIND_LABELS[kindOf(n)]||kindOf(n);ctx.active.add(n.id);const value=label(n,ctx);ctx.active.delete(n.id);if(useCache)ctx.cache.set(n.id,value);return value;}
export function displayNodeLabel(n,index){return resolve(n,context(index));}
export function displayEdgeLabel(e){return RELATION_LABELS[relationOf(e)]||relationOf(e).replace(/_/g,' ');}
export function displayLabels(index){return new Map([...index.nodes.values()].map(n=>[n.id,displayNodeLabel(n,index)]));}
