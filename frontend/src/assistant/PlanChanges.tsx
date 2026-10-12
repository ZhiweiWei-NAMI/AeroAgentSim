import { useState, type CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Diff, DiffItem, Operation, PlanVersion } from './api';
import { Icon } from './icons';
import { valueText } from './present';

const KIND: Record<string, [string, string, string]> = {
  fleet: ['drone', 'fleet', '机队'], vehicle: ['drone', 'UAV', '无人机'], place: ['customer', 'place', '地点'], workload: ['parcel', 'tasks', '任务'], task: ['parcel', 'task', '任务'],
  incident: ['incident', 'incident', '事件'], event: ['clock', 'event', '事件'], rule: ['rule', 'rule', '规则'], algorithm: ['spark', 'decision method', '决策方法'],
  scene: ['layers', 'scene', '场景'], cooperation: ['handoff', 'cooperation', '协作'], environment: ['wind', 'environment', '环境'], control: ['form', 'setting', '设置'], duration: ['clock', 'duration', '时长'],
};

function Items({ items, sign, onHover }: { items: DiffItem[]; sign: '+' | '~' | '−'; onHover?: (id: string | undefined) => void }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN';
  return <>{items.map((item, index) => { const kind = KIND[item.kind]; return <li key={`${sign}${item.id}`} className={`as-delta is-${sign === '+' ? 'add' : sign === '~' ? 'change' : 'remove'}`} style={{ '--i': index } as CSSProperties}
    onMouseEnter={() => onHover?.(item.id)} onMouseLeave={() => onHover?.(undefined)} data-testid="as-delta">
    <span className="as-delta-sign wb-mono">{sign}</span><Icon name={kind?.[0] ?? 'dot'} size={12} />
    <div><span>{(zh ? item.label_zh : undefined) ?? item.label}</span> <small>{kind ? (zh ? kind[2] : kind[1]) : item.kind}</small>
      {item.fields?.length ? <dl>{item.fields.map(field => <div key={field.name}><dt>{field.name.split('.').map(part => part.replace(/_/g, ' ')).join(' › ')}</dt><dd className="wb-mono"><s>{valueText(field.before)}</s> → <b>{valueText(field.after)}</b></dd></div>)}</dl> : null}
    </div>
  </li>; })}</>;
}

export function DiffList({ diff, onHover, empty }: { diff?: Diff | null; onHover?: (id: string | undefined) => void; empty: string }) {
  const total = (diff?.added.length ?? 0) + (diff?.changed.length ?? 0) + (diff?.removed.length ?? 0);
  if (!diff || !total) return <p className="as-quiet">{empty}</p>;
  return <ul className="as-deltas"><Items items={diff.added} sign="+" onHover={onHover} /><Items items={diff.changed} sign="~" onHover={onHover} /><Items items={diff.removed} sign="−" onHover={onHover} /></ul>;
}

/** What this version does to the scene, what changed since the last version, and why — one entry per operation. */
export function PlanChanges({ plan, onHover }: { plan: PlanVersion; onHover?: (id: string | undefined) => void }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [since, setSince] = useState<'previous' | 'base'>(plan.diff_previous ? 'previous' : 'base');
  const diff = since === 'previous' ? plan.diff_previous : plan.diff_base;
  return <div className="as-changes" data-testid="as-changes">
    <div className="as-seg" role="tablist">
      <button role="tab" aria-selected={since === 'previous'} disabled={!plan.diff_previous} onClick={() => setSince('previous')}>{plan.parent ? L(`Since v${plan.parent}`, `相对 v${plan.parent}`) : L('Since last version', '相对上一版')}</button>
      <button role="tab" aria-selected={since === 'base'} onClick={() => setSince('base')}>{L(`Against ${plan.base_label ?? 'your scene'}`, `相对${plan.base_label ?? '当前场景'}`)}</button>
    </div>
    <DiffList diff={diff} onHover={onHover} empty={since === 'previous' ? L('Nothing changed in the scene since the previous version.', '与上一版相比场景没有变化。') : L('This plan leaves the scene as it is.', '此方案不改变场景。')} />
    <h5 className="as-subhead">{L('How the plan is built', '方案如何构成')}<small>{L('Each step is a real operation the simulator compiled', '每一步都是仿真器已编译的真实操作')}</small></h5>
    <ol className="as-ops">{plan.operations.map(op => <Op key={op.id} op={op} issue={plan.compile.errors.find(error => error.op_id === op.id)?.message} />)}</ol>
  </div>;
}

/** Plain names for the typed operations in assistant/operations.py. */
const OPS: Record<string, [string, string]> = {
  set_control: ['Set a starting-point control', '设置起点控件'], set_duration: ['Set the run length', '设置运行时长'], set_run_mode: ['Choose how operator input arrives', '选择操作员输入方式'],
  set_wind: ['Set the background wind', '设置常态风'], add_gust: ['Add a wind gust', '添加阵风'], set_rule: ['Change a safety rule', '修改安全规则'], set_algorithm: ['Choose a decision method', '选择决策方法'],
  add_place: ['Add a place', '添加地点'], update_place: ['Change a place', '修改地点'], remove_place: ['Remove a place', '移除地点'],
  add_fleet: ['Add a group of UAVs', '添加无人机编队'], set_fleet: ['Change a UAV group', '修改无人机编队'], set_vehicle: ['Adjust one UAV', '调整单架无人机'],
  add_orders: ['Add tasks', '添加任务'], set_orders: ['Change tasks', '修改任务'], remove_orders: ['Remove tasks', '移除任务'], set_task: ['Change one task', '修改单个任务'],
  add_incident: ['Schedule an incident', '安排事件'], set_incident: ['Change an incident', '修改事件'], remove_incident: ['Remove an incident', '移除事件'],
  schedule_event: ['Schedule an operator event', '安排操作员事件'], set_cooperation: ['Set who notices, decides and helps', '设置发现、决策与协助'], remove_cooperation: ['Remove cooperation', '移除协作'],
};
const brief = (value: unknown): string => Array.isArray(value) ? (value.every(item => typeof item !== 'object' || item === null) ? valueText(value) : `${value.length} items`)
  : value && typeof value === 'object' ? Object.keys(value).join(', ') : valueText(value);

function Op({ op, issue }: { op: Operation; issue?: string }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { op: name, id, explain, ...rest } = op;
  // Parameters arrive nested under params; show their top-level values and keep the full payload one click away.
  const args = (rest.params && typeof rest.params === 'object' && !Array.isArray(rest.params) ? rest.params : rest) as Record<string, unknown>;
  // Show the label in the reader's language only.
  const entries = Object.entries(args).filter(([key, value]) => value !== undefined && value !== null && !(zh ? key === 'label' && 'label_zh' in args : key.endsWith('_zh')));
  const title = OPS[name];
  return <li className={`as-op${issue ? ' is-bad' : ''}`} data-testid="as-op">
    <span className="as-op-id wb-mono" title={L('Operation id — name it to revise this step', '操作标识，修改此步骤时可引用')}>{id}</span>
    <div><b className="as-op-title">{title ? (zh ? title[1] : title[0]) : name}</b> <code>{name}</code>{explain && <p>{explain}</p>}
      {entries.length > 0 && <span className="as-op-args">{entries.slice(0, 6).map(([key, value]) => <span key={key}><i>{key.replace(/_/g, ' ')}</i> {brief(value)}</span>)}</span>}
      {entries.some(([, value]) => value && typeof value === 'object') && <details className="as-op-full"><summary>{L('All parameters', '全部参数')}</summary><pre className="wb-mono">{JSON.stringify(args, null, 2)}</pre></details>}
      {issue && <p className="as-bad">{issue}</p>}
    </div>
  </li>;
}
