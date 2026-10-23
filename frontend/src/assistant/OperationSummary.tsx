import { useI18n } from '../i18n/I18nProvider';
import { readableLabel } from '../pages/inspection-format';
import type { Operation, OperationDescriptors, PlanVersion, Release, SceneSummary } from './api';
import { bi, kindNoun, pick, placeRole, releaseText, valueText, type Lang } from './present';

/** Only canonical IDs and catalogue-declared aliases identify an operation; never guess by suffix. */
export function operationDescriptor(op: string, descriptors: OperationDescriptors | undefined) {
  const operations=descriptors?.families.flatMap(family=>family.operations) ?? [];
  return operations.find(row=>row.id===op) ?? operations.find(row=>row.aliases?.includes(op));
}
export function operationName(op: string, L: Lang, descriptors?: OperationDescriptors): string {
  const row=operationDescriptor(op,descriptors);
  return row ? L(row.title.en,row.title.zh) : `${L('Unknown operation','未知操作')} · ${op}`;
}
export function operationSummary(op: string, L: Lang, descriptors?: OperationDescriptors): string | undefined {
  const summary=operationDescriptor(op,descriptors)?.summary;
  return summary ? L(summary.en,summary.zh) : undefined;
}

export function OperationCopyNotice({plan}: {plan: PlanVersion}) {
  const {locale}=useI18n(),L: Lang=(en,zh)=>locale==='zh-CN'?zh:en;
  return plan.operationCopy?.error ? <p className="as-quiet" role="alert">{L('Operation descriptions are unavailable; IDs are shown instead.','操作说明不可用，暂显示操作标识。')} {bi(plan.operationCopy.error,locale==='zh-CN')}</p> : null;
}

const PARAMS: Record<string,[string,string]> = {
  index: ['Gust index','阵风索引'], id: ['ID','标识'], count: ['Count','数量'], capacity: ['Capacity','容量'], power_w: ['Charging power (W)','充电功率（瓦）'],
  battery_pct: ['Battery (%)','电量（%）'], payload_capacity_kg: ['Payload capacity (kg)','载荷容量（千克）'], payload_kg: ['Payload (kg)','载荷（千克）'],
  speed_mps: ['Wind speed (m/s)','风速（米/秒）'], start_s: ['Start (s)','开始（秒）'], end_s: ['End (s)','结束（秒）'], seconds: ['Duration (s)','时长（秒）'],
  at: ['Position','位置'], start: ['Starting point','出发点'], from: ['From','起点'], to: ['To','终点'], release: ['Release','发布时刻'],
  kind: ['Task kind','任务类型'], role: ['Role','角色'], fleet: ['Fleet','机队'], vehicle: ['UAV','无人机'], task: ['Task','任务'],
  priority: ['Priority','优先级'], delivery_window_s: ['Delivery window (s)','送达时限（秒）'], preparation_s: ['Preparation time (s)','准备时间（秒）'],
  extract: ['Map source','地图来源'], bounds: ['Geographic bounds','地理范围'], alt: ['Origin altitude (m)','原点高度（米）'], level_height_m: ['Floor height (m)','层高（米）'],
  label: ['Name','名称'], label_zh: ['Name','名称'], rule: ['Rule','规则'], value: ['Value','值'], mode: ['Mode','模式'],
  slot: ['Decision method','决策方法'], control: ['Setting','设置'], allocation: ['Allocation','任务分配'], path_planning: ['Path planning','路径规划'],
  conflict_resolution: ['Conflict resolution','冲突消解'], charging: ['Charging','充电'], time_s: ['Time (s)','时刻（秒）'], at_s: ['Time (s)','时刻（秒）'],
};
export function parameterName(key: string, L: Lang): string {
  const row=PARAMS[key];
  if(row)return L(...row);
  const wind=key.match(/^(.*)_limit_mps$/);
  if(wind)return L(`${kindNoun(wind[1],2,L)} limit (m/s)`,`${kindNoun(wind[1],2,L)}风速上限（米/秒）`);
  return readableLabel(key,L('en-US','zh-CN'));
}
export function operationParams(op: Operation): Record<string,unknown> {
  if(op.params && typeof op.params==='object' && !Array.isArray(op.params))return op.params as Record<string,unknown>;
  const {id,op:name,explain,...params}=op;
  return params;
}
const ROLES: Record<string,[string,string]> = {
  meal_courier: ['Meal courier','送餐无人机'], parcel_drone: ['Parcel drone','包裹无人机'], medical_drone: ['Medical drone','医疗无人机'],
  inspection_drone: ['Inspection drone','巡检无人机'], patrol_drone: ['Patrol drone','巡逻无人机'], air_taxi: ['Air taxi','空中出租车'], sprayer: ['Sprayer','喷洒无人机'],
};
export function parameterValue(key: string, value: unknown, scene: SceneSummary | undefined, zh: boolean, L: Lang): string {
  if((key==='lat'||key==='lon') && typeof value==='number')return valueText(value,L('°','度'),6);
  if(key==='bounds' && Array.isArray(value))return valueText(value,L('°','度'),6);
  if(key==='release' && value && typeof value==='object')return releaseText(value as Release,L);
  if(key==='role' && typeof value==='string')return ROLES[value] ? L(...ROLES[value]) : placeRole(value,L);
  if(key==='kind' && typeof value==='string')return kindNoun(value,1,L);
  if(key==='mode' && (value==='scheduled'||value==='interactive'))return value==='scheduled'?L('Scheduled','按计划'):L('Interactive','交互式');
  const name=(id: string)=>pick(scene?.places?.find(row=>row.id===id),zh)||id;
  if((key==='at'||key==='start') && typeof value==='string')return ['center','centre','middle','中心','中间'].includes(value)?L('Centre','中心'):name(value);
  if((key==='at'||key==='start') && Array.isArray(value))return `${valueText(value)} m`;
  if((key==='from'||key==='to') && Array.isArray(value))return value.map(id=>typeof id==='string'?name(id):valueText(id)).join(', ');
  if(value && typeof value==='object' && !Array.isArray(value))return Object.entries(value).map(([child,v])=>`${parameterName(child,L)}: ${parameterValue(child,v,scene,zh,L)}`).join('; ');
  if(typeof value==='boolean')return value?L('Yes','是'):L('No','否');
  return bi(valueText(value),zh);
}

export function effectiveOperations(plan: PlanVersion): Operation[] {
  return plan.compile.ok && plan.compile.operations ? plan.compile.operations : plan.operations;
}

export function hasCompiledExpansion(plan: PlanVersion): boolean {
  return plan.compile.ok && effectiveOperations(plan).some(op=>op.op==='add_lowalt_components') &&
    !plan.operations.some(op=>op.op==='add_lowalt_components');
}

export function isUnchangedPlan(plan: PlanVersion): boolean {
  const diff=plan.diff_base;
  return plan.compile.ok && (typeof plan.base.workspace==='string' || !!plan.variant) && !!diff &&
    ![...diff.added,...diff.changed,...diff.removed].length &&
    !Object.values(diff.entities ?? {}).some(ids=>ids.length>0);
}

/** The compiler's revision summaries sometimes contain a full operation dump. */
export function PlanSummary({ plan }: { plan: PlanVersion }) {
  const {locale}=useI18n(), zh=locale==='zh-CN', L: Lang=(en,cn)=>zh?cn:en;
  if(isUnchangedPlan(plan))return <><OperationCopyNotice plan={plan}/><p>{L('This proposal makes no changes to the current scene.','此提议没有改变当前场景。')}</p></>;
  const machineSummary=plan.operations.some(op=>plan.summary.includes(`${op.op}:`)) && /[{}]/.test(plan.summary);
  if(!machineSummary && !hasCompiledExpansion(plan))return <><OperationCopyNotice plan={plan}/>{plan.summary?<p>{bi(plan.summary,zh)}</p>:null}</>;
  const descriptors=plan.operationCopy?.descriptors;
  return <><OperationCopyNotice plan={plan}/><ul className="as-arrangement" data-testid="as-arrangement">{effectiveOperations(plan).map(op=>{
    const params=operationParams(op), summary=operationSummary(op.op,L,descriptors);
    const entries=Object.entries(params).filter(([key])=>!(zh ? key==='label' && typeof params.label_zh==='string' : key==='label_zh'));
    return <li key={op.id}><strong>{operationName(op.op,L,descriptors)}</strong>
      {summary && <p className="hint">{summary}</p>}
      <dl>{entries.map(([key,value])=><div key={key}><dt>{parameterName(key,L)}</dt><dd>{parameterValue(key,value,plan.scene,zh,L)}</dd></div>)}</dl>
    </li>;
  })}</ul></>;
}
