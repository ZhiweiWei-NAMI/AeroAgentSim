import { useRef, useState, type ReactNode } from 'react';
import { useI18n } from '../../../i18n/I18nProvider';
import type { Project, WorkbenchApi } from '../../api';
import { applyOperations, operationFreshnessKey, operationProblem, operationText, previewOperations, type DiffRow, type Operation, type OperationPreview } from '../operations';
import '../tasks.css';

export function problemMessage(problem: unknown, zh = false): string {
  return operationText(operationProblem(problem).message, zh);
}

export interface FormNavigation { onSection?: (section: string) => void }

export function Prerequisite({ children, section, action, onSection }: FormNavigation & { children: ReactNode; section: string; action: string }) {
  return <p className="hint wb-form-prerequisite" role="note">
    {children}{onSection && <button type="button" className="wb-btn small" onClick={() => onSection(section)}>{action}</button>}
  </p>;
}

const format = (value: unknown) => value === undefined ? '—' : typeof value === 'string' ? value : JSON.stringify(value);
const KIND_LABELS: Record<string, [string, string]> = {
  components: ['Components', '组件'], place: ['Place', '设施'], fleet: ['Fleet', '机群'],
  workload: ['Order stream', '订单流'], event: ['Event', '事件'], rule: ['Rule', '规则'],
  scene: ['Scene', '场景'], network: ['Road network', '路网'], run: ['Run settings', '运行设置'],
};
const LABELS: Record<string, [string, string]> = {
  'parcel order': ['Parcel order', '包裹订单'], 'meal order': ['Meal order', '餐食订单'],
  'urgent medical order': ['Urgent medical order', '紧急医疗订单'], 'inspection job': ['Inspection job', '巡检任务'],
  'survey job': ['Survey job', '测绘任务'], 'spraying job': ['Spraying job', '喷洒任务'],
  'passenger trip': ['Passenger trip', '乘客行程'], 'patrol round': ['Patrol round', '巡逻任务'],
  'incident response': ['Incident response', '事件响应'],
  'Person fell on the street': ['Person fell on the street', '行人在街道跌倒'],
  'Person falls': ['Person falls', '行人跌倒'],
  'Battery reserve': ['Battery reserve', '预留电量'], 'Safety distance': ['Safety distance', '安全间隔'],
  'Wind limit': ['Wind limit', '风速上限'], 'Medical preemption': ['Medical preemption', '医疗任务抢占'],
};

function rowLabel(row: DiffRow, zh: boolean, L: (en: string, cn: string) => string): string {
  if (zh && row.label_zh) return row.label_zh;
  if (row.label) {
    const known = LABELS[row.label];
    if (known) return L(...known);
    const gust = /^Wind gust: (.+) m\/s$/.exec(row.label);
    if (gust) return L(`Wind gust: ${gust[1]} m/s`, `阵风：${gust[1]} m/s`);
    return operationText(row.label, zh);
  }
  return '';
}

/** A single semantic list for every operation review, including destructive changes. */
export function DiffPreview({ preview, L, zh }: { preview: OperationPreview; L: (en: string, cn: string) => string; zh: boolean }) {
  const groups = [
    { key: 'added' as const, sign: '+', title: L('Added', '新增') },
    { key: 'changed' as const, sign: '~', title: L('Changed', '修改') },
    { key: 'removed' as const, sign: '−', title: L('Removed', '移除') },
  ];
  return <div className="wb-operation-diff">{groups.map(group => {
    const rows = preview.diff[group.key];
    if (!rows.length) return null;
    return <div key={group.key}>
      <p className="hint"><b>{group.title}</b> · {rows.length}</p>
      <ul className="wb-diff-list">
        {rows.map((row, index) => <li key={index}>
          <span aria-hidden>{group.sign}</span>{' '}
          {row.kind && <span>{KIND_LABELS[row.kind] ? L(...KIND_LABELS[row.kind]) : row.kind}</span>}{' '}
          {row.id && <span className="wb-mono">{row.id}</span>}{' '}{rowLabel(row, zh, L)}
          {row.fields?.length ? <ul className="wb-diff-fields">{row.fields.map((field, fieldIndex) => <li key={fieldIndex}>
            <span className="wb-mono">{field.name}</span>: {format(field.before)} → {format(field.after)}
          </li>)}</ul> : null}
        </li>)}
      </ul>
    </div>;
  })}
  {preview.notes.map((note, index) => {
    const text = typeof note === 'string' ? operationText(note, zh)
      : zh ? note.zh ?? operationText(note.text ?? note.en ?? '', true) : note.en ?? operationText(note.text ?? note.zh ?? '', false);
    return <p key={index} className="hint wb-preview-note">{text}</p>;
  })}</div>;
}

export interface OpApplyProps {
  api: WorkbenchApi; project: Project; onProject: (project: Project) => void;
  saveDraft: () => Promise<Project | undefined>;
  operations: Operation[] | undefined;
  label: string; labelZh?: string;
  danger?: boolean; primary?: boolean; small?: boolean;
  missing?: string;
  summary?: ReactNode;
  note?: string;
  onApplied?: () => void;
}

/** Polls and render identities cannot expire a review: only operations/params or its base revision can. */
export function OpApply({ api, project, onProject, saveDraft, operations, label, danger, primary, small, missing, summary, note, onApplied }: OpApplyProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => (zh ? cn : en);
  const [preview, setPreview] = useState<{ ops: Operation[]; key: string; data: OperationPreview; base: Project }>();
  const [busy, setBusy] = useState<'preview' | 'apply'>(), [error, setError] = useState<unknown>();
  const pending = useRef(false);
  const staged = !!operations?.length;
  const fresh = !!preview && !!operations && !missing && preview.base.id === project.id
    && preview.key === operationFreshnessKey(operations, project.revision);
  const run = async (phase: 'preview' | 'apply', body: () => Promise<void>) => {
    if (pending.current) return;
    pending.current = true; setBusy(phase); setError(undefined);
    try { await body(); } catch (problem) { setError(problem); }
    finally { pending.current = false; setBusy(undefined); }
  };
  const previewNow = () => {
    if (!operations?.length || missing) return;
    const ops = structuredClone(operations);
    void run('preview', async () => {
      setPreview(undefined);
      const saved = await saveDraft();
      if (!saved) throw new Error(L('Fix the highlighted input before previewing.', '请先修正标记的输入后再预览。'));
      const data = await previewOperations(api, saved, ops);
      setPreview({ ops, data, base: saved, key: operationFreshnessKey(ops, saved.revision) });
    });
  };
  const applyNow = () => {
    if (!preview || !fresh) return;
    const reviewed = preview;
    void run('apply', async () => {
      // A failed response cannot leave a repeatable confirmation of a possibly committed request.
      setPreview(undefined);
      onProject(await applyOperations(api, reviewed.base, reviewed.ops));
      onApplied?.();
    });
  };
  return <div className="wb-op-apply">
    {!preview && <div className="wb-form-actions">
      <button type="button" className={`wb-btn${small ? ' small' : ''}${danger ? ' danger' : ''}${primary ? ' primary' : ''}`}
        disabled={!staged || !!missing || !!busy} title={missing} onClick={previewNow}>
        {busy === 'preview' ? L('Checking…', '正在预览…') : busy === 'apply' ? L('Applying…', '正在应用…') : label}
      </button>
      {missing && <span role="note" className="hint wb-form-prerequisite">{missing}</span>}
    </div>}
    {error !== undefined && <p role="alert" className="hint wb-field-error">{problemMessage(error, zh)}</p>}
    {preview && <div className={`wb-operation-review${danger ? ' is-danger' : ''}`} role="group" aria-label={L('Preview of the change', '变更预览')}>
      {summary}
      <DiffPreview preview={preview.data} L={L} zh={zh} />
      <div className="wb-form-actions">
        {fresh ? <button type="button" className={`wb-btn small ${danger ? 'danger' : 'primary'}`} disabled={!!busy} onClick={applyNow}>{L('Confirm', '确认')}</button>
          : <><p className="hint wb-form-prerequisite" role="status">{L('Out of date — preview again.', '已过期——请重新预览。')}</p>
            <button type="button" className="wb-btn small" disabled={!staged || !!missing || !!busy} onClick={previewNow}>{L('Preview again', '重新预览')}</button></>}
        <button type="button" className="wb-btn small ghost" disabled={!!busy} onClick={() => setPreview(undefined)}>{L('Cancel', '取消')}</button>
      </div>
      {missing && <p className="hint wb-form-prerequisite" role="note">{missing}</p>}
      <p className="hint">{note ?? L('Review these changes before applying them to the draft.', '请核对这些变更，再应用到草稿。')}</p>
    </div>}
  </div>;
}

export interface NumberOptions { min?: number; max?: number; exclusiveMin?: number; allowEmpty?: boolean; integer?: boolean }

/** Empty and invalid text have no numeric value; hexadecimal, Infinity and NaN are rejected. */
export function parseStagedNumber(raw: string): number | undefined {
  const text = raw.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return undefined;
  const value = Number(text);
  return Number.isFinite(value) ? value : undefined;
}

/** Raw text is owned by the form; validation never rewrites intermediate input such as "1.". */
export function StagedNumber({ label, value, onChange, min, max, exclusiveMin, allowEmpty, integer, problem: suppliedProblem, showError, disabled }: NumberOptions & {
  label: string; value: string; onChange: (text: string) => void; problem?: string; showError?: boolean; disabled?: boolean;
}): JSX.Element {
  const { locale } = useI18n();
  const [touched, setTouched] = useState(false);
  const problem = suppliedProblem ?? stagedNumberProblem(value, { min, max, exclusiveMin, allowEmpty, integer, zh: locale === 'zh-CN' });
  const shown = touched || showError ? problem : undefined;
  return <label className="wb-form-field">{label}
    <input className="wb-search" aria-label={label} disabled={disabled} aria-invalid={shown ? true : undefined} inputMode={integer ? 'numeric' : 'decimal'} value={value}
      onChange={event => { setTouched(true); onChange(event.target.value); }} onBlur={() => setTouched(true)} />
    {shown && <span role="alert" className="hint wb-field-error">{shown}</span>}
  </label>;
}

export function stagedNumberProblem(value: string, opts: NumberOptions & { zh: boolean }): string | undefined {
  const L = (en: string, cn: string) => opts.zh ? cn : en;
  if (value.trim() === '') return opts.allowEmpty ? undefined : L('Required.', '必填。');
  const parsed = parseStagedNumber(value);
  if (parsed === undefined) return L('Must be a finite number.', '必须是有限数字。');
  if (opts.integer && !Number.isSafeInteger(parsed)) return L('Must be a safe integer.', '必须是可精确表示的整数。');
  if (opts.min !== undefined && parsed < opts.min) return L(`Cannot be below ${opts.min}.`, `不能小于 ${opts.min}。`);
  if (opts.max !== undefined && parsed > opts.max) return L(`Cannot exceed ${opts.max}.`, `不能大于 ${opts.max}。`);
  if (opts.exclusiveMin !== undefined && parsed <= opts.exclusiveMin) return L(`Must be above ${opts.exclusiveMin}.`, `必须大于 ${opts.exclusiveMin}。`);
  return undefined;
}
