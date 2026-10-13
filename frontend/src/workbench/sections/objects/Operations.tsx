import { useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import { applyOperations, operationProblem, previewOperations, type DiffField, type DiffRow, type Operation, type OperationPreview } from '../operations';

type Localize = (en: string, zh: string) => string;

/** C11 rows may carry their own kind label and preformatted field values; fall back only when absent. */
export type { DiffField } from '../operations';
export type RichDiffRow = DiffRow;
export type ObjectsDiff = OperationPreview['diff'] & { entities?: { added: string[]; changed: string[]; removed: string[] } };

/** C11 also reports explicit member deltas. Keep them in the review, including fleet shrinkage. */
export function operationChanges(diff: ObjectsDiff, entities: Array<{ id: string; label?: string; label_zh?: string }> = []): OperationPreview['diff'] {
  const changes = { added: [...diff.added] as RichDiffRow[], changed: [...diff.changed] as RichDiffRow[], removed: [...diff.removed] as RichDiffRow[] };
  const append = (group: keyof typeof changes, id: string) => {
    if (changes[group].some(row => row.kind === 'entity' && row.id === id)) return;
    const entity = entities.find(row => row.id === id);
    changes[group].push({ kind: 'entity', id, label: entity?.label, label_zh: entity?.label_zh });
  };
  for (const group of ['added', 'changed', 'removed'] as const) for (const id of diff.entities?.[group] ?? []) append(group, id);
  for (const row of diff.changed) for (const field of row.fields ?? []) {
    if (field.name !== 'members' || !Array.isArray(field.before) || !Array.isArray(field.after)) continue;
    for (const id of field.before) if (typeof id === 'string' && !field.after.includes(id)) append('removed', id);
    for (const id of field.after) if (typeof id === 'string' && !field.before.includes(id)) append('added', id);
  }
  return changes;
}

/** Select the active language from the backend's legacy bilingual messages. */
export function operationText(text: string, L: Localize): string {
  const split = text.search(/\s\/\s(?=[\u3400-\u9fff])/);
  return split < 0 ? text : L(text.slice(0, split), text.slice(split + 3));
}

function kindLabel(row: RichDiffRow, L: Localize) {
  const provided = operationText(L(row.kind_label ?? '', row.kind_label_zh ?? row.kind_label ?? ''), L);
  if (row.kind_label) return provided;
  const labels: Record<string, [string, string]> = {
    entity: ['Object', '对象'], place: ['Place', '设施'], facility: ['Place', '设施'],
    fleet: ['Fleet', '机群'], participant: ['Participant', '参与者'], engine: ['Engine', '引擎'],
    components: ['Components', '组件'], control: ['Setting', '设置'], binding: ['Binding', '绑定'],
    presentation: ['Visual binding', '视觉绑定'], relation: ['Relation', '关系'],
    behaviour: ['Behaviour', '行为'], generator: ['Generator', '生成器'], other: ['Reference', '引用'],
    workload: ['Orders', '订单'], task: ['Task', '任务'], event: ['Event', '事件'],
    region: ['Region', '区域'], environment: ['Environment', '环境'], algorithm: ['Algorithm', '算法'],
    scene: ['Scene', '场景'], network: ['Road network', '道路网络'], rule: ['Rule', '规则'], run: ['Run settings', '运行设置'],
  };
  const pair = row.kind && labels[row.kind];
  return pair ? L(...pair) : row.kind ?? '—';
}

/** Human field label: a row-provided label wins, else a known field, else a humanised name. */
function fieldLabel(name: string, L: Localize) {
  const labels: Record<string, [string, string]> = {
    'wind.speed_mps': ['Wind speed (m/s)', '风速（m/s）'], 'allocation': ['Allocation', '分配'],
    'count': ['Count', '数量'], 'capacity': ['Capacity', '容量'], 'battery_pct': ['Battery (%)', '电量 (%)'],
    'start': ['Start place', '起点'], 'speed_mps': ['Speed (m/s)', '速度（m/s）'],
  };
  const pair = labels[name];
  return pair ? L(...pair) : name.replace(/[._]/g, ' ').replace(/^./, character => character.toUpperCase());
}

/** Values read as prose: null is an em dash and strings carry no quotes. */
function fieldValue(value: unknown, L: Localize) {
  if (value === null || value === undefined) return L('—', '—');
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** "<label>: <before> → <after>", preferring the server-provided label and preformatted values. */
function fieldLine(field: DiffField, L: Localize) {
  const provided = field.label ?? field.label_zh;
  const label = provided ? operationText(L(provided, field.label_zh ?? provided), L) : fieldLabel(field.name, L);
  const before = field.before_text ?? fieldValue(field.before, L);
  const after = field.after_text ?? fieldValue(field.after, L);
  return `${label}: ${before} → ${after}`;
}

function rowLabel(row: DiffRow, L: Localize) {
  if (!row.label_zh && row.label) {
    const gust = /^Wind gust: (.+) m\/s$/.exec(row.label);
    if (gust) return L(row.label, `阵风：${gust[1]} m/s`);
    const labels: Record<string, string> = {
      'parcel order': '包裹订单', 'meal order': '餐食订单', 'urgent medical order': '紧急医疗订单',
      'inspection job': '巡检任务', 'survey job': '测绘任务', 'spraying job': '喷洒任务',
      'passenger trip': '乘客行程', 'patrol round': '巡逻任务', 'incident response': '事件响应',
      'Person fell on the street': '行人在街道跌倒', 'Person falls': '行人跌倒',
      'Battery reserve': '预留电量', 'Safety distance': '安全间隔', 'Wind limit': '风速上限', 'Medical preemption': '医疗任务抢占',
    };
    if (labels[row.label]) return L(row.label, labels[row.label]);
  }
  return operationText(L(row.label ?? row.id ?? '—', row.label_zh ?? row.label ?? row.id ?? '—'), L);
}

/** Shared, left-aligned review for all Objects operations; exported for other form owners. */
export function OperationDiff({ diff, notes = [], stale = false, L, entities }: {
  diff: ObjectsDiff; notes?: OperationPreview['notes']; stale?: boolean; L: Localize; entities?: Array<{ id: string; label?: string; label_zh?: string }>;
}) {
  const changes = operationChanges(diff, entities) as { added: RichDiffRow[]; changed: RichDiffRow[]; removed: RichDiffRow[] };
  const groups = [
    { key: 'added', rows: changes.added, sign: '+', title: L('Added', '新增') },
    { key: 'changed', rows: changes.changed, sign: '~', title: L('Changed', '修改') },
    { key: 'removed', rows: changes.removed, sign: '−', title: L('Removed', '移除') },
  ];
  return <div className="wb-operation-diff" role="group" aria-label={L('Review changes', '查看变更')}>
    {stale && <p className="hint wb-preview-stale">{L('Inputs or draft changed — preview again before applying.', '输入或草稿已变化，请重新预览后再应用。')}</p>}
    {groups.filter(group => group.rows.length > 0).map(group => <section key={group.key} aria-label={group.title}>
      <h5>{group.title} <span className="wb-tag">{group.rows.length}</span></h5>
      <ul className="wb-diff-list">
        {group.rows.map((row, index) => <li key={`${row.kind}:${row.id}:${index}`}>
          <div className="wb-diff-row">
            <span className={`wb-diff-sign wb-diff-sign-${group.key}`} aria-hidden>{group.sign}</span>
            <strong className="wb-diff-label">{rowLabel(row, L)}</strong>
            <span className="wb-diff-id" title={row.id}><span className="wb-tag">{kindLabel(row, L)}</span> <span className="wb-mono">{row.id ?? '—'}</span></span>
          </div>
          {row.fields?.map(field => <div key={field.name} className="wb-diff-field">{fieldLine(field, L)}</div>)}
        </li>)}
      </ul>
    </section>)}
    {groups.every(group => group.rows.length === 0) && <p className="hint">{L('No draft changes for these inputs.', '这些输入不会更改草稿。')}</p>}
    {notes.map((note, index) => <p key={index} className="hint">{typeof note === 'string' ? operationText(note, L)
      : operationText(L(note.en ?? note.text ?? note.zh ?? '', note.zh ?? note.text ?? note.en ?? ''), L)}</p>)}
  </div>;
}

/** Stable by research values, including nested keys; transient operation ids are excluded. */
export function operationKey(project: Pick<Project, 'id' | 'revision'>, operations: Operation[]): string {
  const sorted = (value: unknown): unknown => Array.isArray(value) ? value.map(sorted)
    : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, child]) => [key, sorted(child)])) : value;
  return JSON.stringify([project.id, project.revision, operations.map(({ op, params }) => ({ op, params: sorted(params) }))]);
}

/** A confirmation uses the exact operations and base revision that produced its review. */
export function useOperationsReview(api: WorkbenchApi, project: Project, operations: Operation[]) {
  const key = operationKey(project, operations);
  const [receipt, setReceipt] = useState<{ preview: OperationPreview; key: string; base: Project; operations: Operation[] }>();
  const [rejected, setRejected] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply'>();
  const [problem, setProblem] = useState<ReturnType<typeof operationProblem>>();
  const [failure, setFailure] = useState<unknown>();
  const stale = !!receipt && (receipt.key !== key || rejected);
  const load = async () => {
    if (busy) return;
    const snapshot = JSON.parse(JSON.stringify(operations)) as Operation[];
    const base = { ...project };
    setBusy('preview'); setProblem(undefined); setFailure(undefined);
    try {
      const preview = await previewOperations(api, base, snapshot);
      setReceipt({ preview, key, base, operations: snapshot }); setRejected(false);
    } catch (failure) { setFailure(failure); setProblem(operationProblem(failure)); setReceipt(undefined); }
    finally { setBusy(undefined); }
  };
  const apply = async () => {
    if (!receipt || stale || busy) return;
    setBusy('apply'); setProblem(undefined); setFailure(undefined);
    try {
      const updated = await applyOperations(api, receipt.base, receipt.operations);
      setReceipt(undefined);
      return updated;
    } catch (failure) { setFailure(failure); setProblem(operationProblem(failure)); setRejected(true); }
    finally { setBusy(undefined); }
  };
  return { preview: receipt?.preview, stale, busy, problem, failure, load, apply, clear: () => { setReceipt(undefined); setProblem(undefined); setFailure(undefined); } };
}
