import { useState, type CSSProperties } from 'react';
import { useI18n } from '../i18n/I18nProvider';
import type { Diff, DiffItem, Operation, PlanVersion } from './api';
import { Icon } from './icons';
import { bi, pick } from './present';
import { effectiveOperations, operationName, operationParams, parameterName, parameterValue } from './OperationSummary';

const KIND: Record<string, [string, string, string]> = {
  fleet: ['drone', 'fleet', '机队'], vehicle: ['drone', 'UAV', '无人机'], place: ['customer', 'place', '地点'], workload: ['parcel', 'tasks', '任务'], task: ['parcel', 'task', '任务'],
  incident: ['incident', 'incident', '事件'], event: ['clock', 'event', '事件'], rule: ['rule', 'rule', '规则'], algorithm: ['spark', 'decision method', '决策方法'],
  scene: ['layers', 'scene', '场景'], cooperation: ['handoff', 'cooperation', '协作'], environment: ['wind', 'environment', '环境'], control: ['form', 'setting', '设置'], duration: ['clock', 'duration', '时长'],
  components:['layers','scene components','场景组件'], network:['layers','road network','道路网络'],
};

function Items({ items, sign, onHover }: { items: DiffItem[]; sign: '+' | '~' | '−'; onHover?: (id: string | undefined) => void }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  return <>{items.map((item, index) => { const kind = KIND[item.kind]; return <li key={`${sign}${item.id}`} className={`as-delta is-${sign === '+' ? 'add' : sign === '~' ? 'change' : 'remove'}`} style={{ '--i': index } as CSSProperties}
    onMouseEnter={() => onHover?.(item.id)} onMouseLeave={() => onHover?.(undefined)} data-testid="as-delta">
    <span className="as-delta-sign wb-mono">{sign}</span><Icon name={kind?.[0] ?? 'dot'} size={12} />
    <div className="as-delta-body"><div className="as-delta-heading"><small>{(zh ? item.kind_label_zh : item.kind_label) ?? (kind ? L(kind[1],kind[2]) : item.kind)}</small><span>{pick(item,zh)}</span><code className="wb-mono" title={item.id}>{item.id}</code></div>
      {item.fields?.length ? <dl>{item.fields.map(field => <div key={field.name}><dt>{pick(field,zh) || field.name.split('.').map(part => parameterName(part,L)).join(' › ')}:</dt><dd className="wb-mono"><s>{bi((zh ? field.before_text_zh : field.before_text) ?? parameterValue(field.name.split('.').slice(-1)[0],field.before,undefined,zh,L),zh)}</s> → <b>{bi((zh ? field.after_text_zh : field.after_text) ?? parameterValue(field.name.split('.').slice(-1)[0],field.after,undefined,zh,L),zh)}</b></dd></div>)}</dl> : null}
    </div>
  </li>; })}</>;
}

export function CompiledChanges({plan,onHover}: {plan: PlanVersion; onHover?: (id: string | undefined)=>void}) {
  const {locale}=useI18n(),L=(en: string,zh: string)=>locale==='zh-CN'?zh:en;
  return <section className="wb-form-card" data-testid="as-compiled-changes">
    <h5 className="wb-form-card-title">{L('What this changes','实际变更')}</h5>
    <p className="wb-form-card-sub hint">{L('The required drone scene components are included in these changes.','这些变更包含必需的无人机场景组件。')}</p>
    <DiffList diff={plan.diff_base} onHover={onHover} empty={L('No scene changes.','没有场景变更。')}/>
  </section>;
}

export function DiffList({ diff, onHover, empty }: { diff?: Diff | null; onHover?: (id: string | undefined) => void; empty: string }) {
  const {locale}=useI18n(),L=(en: string,zh: string)=>locale==='zh-CN'?zh:en;
  const total = (diff?.added.length ?? 0) + (diff?.changed.length ?? 0) + (diff?.removed.length ?? 0);
  if (!diff || !total) return <p className="as-quiet">{empty}</p>;
  const groups=[{title:L('Added','新增'),items:diff.added,sign:'+' as const},{title:L('Changed','修改'),items:diff.changed,sign:'~' as const},{title:L('Removed','移除'),items:diff.removed,sign:'−' as const}];
  return <div className="as-diff-groups">{groups.filter(group=>group.items.length).map(group=><section key={group.sign}><h5>{group.title} ({group.items.length})</h5><ul className="as-deltas"><Items items={group.items} sign={group.sign} onHover={onHover}/></ul></section>)}</div>;
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
    <h5 className="as-subhead">{L('How the plan is built', '方案如何构成')}<small>{L('Places, participants, tasks and settings in this plan','此方案中的地点、参与者、任务与设置')}</small></h5>
    <ol className="as-ops">{effectiveOperations(plan).map(op => <Op key={op.id} op={op} issue={plan.compile.errors.find(error => error.op_id === op.id)?.message} />)}</ol>
  </div>;
}

function Op({ op, issue }: { op: Operation; issue?: string }) {
  const { locale } = useI18n(), zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const { op: name, id, explain } = op;
  // Parameters arrive nested under params; show their top-level values and keep the full payload one click away.
  const args = operationParams(op);
  // Show the label in the reader's language only.
  const entries = Object.entries(args).filter(([key, value]) => value !== undefined && value !== null && !(zh ? key === 'label' && 'label_zh' in args : key.endsWith('_zh')));
  return <li className={`as-op${issue ? ' is-bad' : ''}`} data-testid="as-op">
    <span className="as-op-id wb-mono" title={L('Operation id — name it to revise this step', '操作标识，修改此步骤时可引用')}>{id}</span>
    <div><b className="as-op-title">{operationName(name,L)}</b>{explain && <p>{bi(explain,zh)}</p>}
      {entries.length > 0 && <span className="as-op-args">{entries.slice(0, 6).map(([key, value]) => <span key={key}><i>{parameterName(key,L)}</i> {parameterValue(key,value,undefined,zh,L)}</span>)}</span>}
      <details className="as-op-full"><summary>{L('Advanced','高级')}</summary><code>{name}</code><pre className="wb-mono">{JSON.stringify(args, null, 2)}</pre></details>
      {issue && <p className="as-bad">{bi(issue,zh)}</p>}
    </div>
  </li>;
}
