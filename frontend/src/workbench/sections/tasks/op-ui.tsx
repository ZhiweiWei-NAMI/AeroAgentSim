import { useState, type ReactNode } from 'react';
import { useI18n } from '../../../i18n/I18nProvider';
import type { Project, WorkbenchApi } from '../../api';
import { applyOperations, operationProblem, previewOperations, type DiffRow, type Operation, type OperationPreview } from '../operations';

export function problemMessage(problem: unknown): string {
  return operationProblem(problem).message;
}

function rowText(row: DiffRow, zh: boolean): string {
  const label = (zh ? row.label_zh : undefined) ?? row.label ?? row.id ?? row.kind ?? '';
  const fields = (row.fields ?? []).map(field => `${field.name}: ${format(field.before)} → ${format(field.after)}`).join('; ');
  return fields ? `${label} (${fields})` : label;
}
const format = (value: unknown) => value === undefined ? '—' : typeof value === 'string' ? value : JSON.stringify(value);

function DiffPreview({ preview, L, zh }: { preview: OperationPreview; L: (en: string, cn: string) => string; zh: boolean }) {
  const groups: Array<{ key: 'added' | 'changed' | 'removed'; sign: string; title: string }> = [
    { key: 'added', sign: '+', title: L('Added', '新增') },
    { key: 'changed', sign: '~', title: L('Changed', '修改') },
    { key: 'removed', sign: '−', title: L('Removed', '移除') },
  ];
  return <>{groups.map(group => {
    const rows = preview.diff?.[group.key] ?? [];
    if (!rows.length) return null;
    return <div key={group.key} style={{ margin: '4px 0' }}>
      <p className="hint" style={{ margin: '2px 0' }}><b>{group.sign} {group.title}</b> · {rows.length}</p>
      <ul style={{ margin: '2px 0', paddingLeft: 18 }}>
        {rows.map((row, index) => <li key={index} className="wb-mono" style={{ fontSize: 11 }}>{rowText(row, zh)}</li>)}
      </ul>
    </div>;
  })}
  {(preview.notes ?? []).map((note, index) => {
    const text = typeof note === 'string' ? note : (zh ? note.zh : undefined) ?? note.text ?? note.en ?? '';
    return <p key={index} className="hint" style={{ color: 'var(--wb-warn)', margin: '2px 0' }}>ℹ {text}</p>;
  })}</>;
}

export interface OpApplyProps {
  api: WorkbenchApi; project: Project; onProject: (project: Project) => void;
  saveDraft: () => Promise<Project | undefined>;
  /** The staged operation(s); `undefined` keeps the button disabled. */
  operations: Operation[] | undefined;
  label: string; labelZh?: string;
  danger?: boolean; primary?: boolean; small?: boolean;
  /** Inline missing-prerequisite text; when set the action is disabled and the reason shows next to it. */
  missing?: string;
  /** Rendered above the confirmation buttons (e.g. the staged form summary). */
  summary?: ReactNode;
  /** Extra note under the confirm line. */
  note?: string;
  onApplied?: () => void;
}

/** Preview → confirm → apply through the C11 operations API. The preview is tied to the exact staged
 *  operations: changing any input marks it out of date and disables Apply until it is previewed again. */
export function OpApply({ api, project, onProject, saveDraft, operations, label, labelZh, danger, primary, small, missing, summary, note, onApplied }: OpApplyProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => (zh ? cn : en);
  const [preview, setPreview] = useState<{ ops: Operation[]; data: OperationPreview; base: Project }>();
  const [busy, setBusy] = useState<'preview' | 'apply'>(), [error, setError] = useState<string>();
  const fresh = !!preview && !!operations && JSON.stringify(preview.ops) === JSON.stringify(operations);
  const staged = !!operations;
  const run = async (phase: 'preview' | 'apply', body: () => Promise<void>) => {
    setBusy(phase); setError(undefined);
    try { await body(); } catch (problem) { setError(problemMessage(problem)); } finally { setBusy(undefined); }
  };
  const previewNow = () => void run('preview', async () => {
    const saved = await saveDraft();
    if (!saved) throw new Error(L('Fix the highlighted input before applying.', '请先修正标记的输入后再应用。'));
    const ops = operations ?? [];
    setPreview({ ops, data: await previewOperations(api, saved, ops), base: saved });
  });
  const applyNow = () => void run('apply', async () => {
    if (!preview) return;
    onProject(await applyOperations(api, preview.base, preview.ops));
    setPreview(undefined);
    onApplied?.();
  });
  return <div className="wb-op-apply" style={{ margin: '6px 0' }}>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className={`wb-btn${small ? ' small' : ''}${danger ? ' danger' : ''}${primary ? ' primary' : ''}`}
        disabled={!staged || !!missing || !!busy}
        title={missing ?? undefined}
        onClick={previewNow}>
        {busy === 'preview' ? L('Checking…', '正在预览…') : label}
      </button>
      {missing && <span role="note" className="hint" style={{ color: 'var(--wb-warn)' }}>{missing}</span>}
      {!missing && !staged && <span role="note" className="hint" style={{ color: 'var(--wb-warn)' }}>{L('Complete the required fields first.', '请先填写必填项。')}</span>}
    </div>
    {error && <p role="alert" className="hint" style={{ color: 'var(--wb-bad)', margin: '4px 0 0' }}>{error}</p>}
    {preview && <div style={{ border: `1px solid ${danger ? 'var(--wb-bad)' : 'var(--wb-line)'}`, borderRadius: 10, padding: 10, margin: '8px 0' }} role="group" aria-label={L('Preview of the change', '变更预览')}>
      {summary}
      <DiffPreview preview={preview.data} L={L} zh={zh} />
      {fresh
        ? <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
            <button className={`wb-btn small ${danger ? 'danger' : 'primary'}`} disabled={busy === 'apply'} onClick={applyNow}>
              {busy === 'apply' ? L('Applying…', '正在应用…') : L('Confirm', '确认')}
            </button>
            <button className="wb-btn small ghost" disabled={busy === 'apply'} onClick={() => setPreview(undefined)}>{L('Cancel', '取消')}</button>
          </div>
        : <p className="hint" role="status" style={{ color: 'var(--wb-warn)', margin: '6px 0 0' }}>
            {L('Inputs changed since this preview — preview again before applying.', '输入已在此预览后发生变化——请重新预览后再应用。')}
            {' '}
            <button className="wb-btn small" disabled={!!busy} onClick={previewNow}>{L('Preview again', '重新预览')}</button>
          </p>}
      <p className="hint" style={{ margin: '6px 0 0' }}>{note ?? L('The preview shows exactly what the server will change; nothing outside this list is touched.', '预览显示服务器将修改的全部内容；列表之外不会改动。')}</p>
    </div>}
  </div>;
}

/** Staged non-negative number input: empty stays empty, invalid shows a field error and blocks the form. */
export function StagedNumber({ label, value, onChange, min, max, allowEmpty, integer }: {
  label: string; value: string; onChange: (text: string) => void; min?: number; max?: number; allowEmpty?: boolean; integer?: boolean;
}): JSX.Element {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => (zh ? cn : en);
  const problem = stagedNumberProblem(value, { min, max, allowEmpty, integer, zh });
  return <label style={{ display: 'block', margin: '2px 0' }}>{label}
    <input className="wb-search" style={{ height: 28 }} aria-label={label} aria-invalid={problem ? true : undefined} inputMode="decimal" value={value} onChange={event => onChange(event.target.value)} />
    {problem && <span role="alert" className="hint" style={{ color: 'var(--wb-bad)' }}>{problem}</span>}
  </label>;
}

/** One validation error message, or `undefined` when the staged text is acceptable (empty only when allowed). */
export function stagedNumberProblem(value: string, opts: { min?: number; max?: number; allowEmpty?: boolean; integer?: boolean; zh: boolean }): string | undefined {
  const zh = opts.zh, L = (en: string, cn: string) => (zh ? cn : en);
  const text = value.trim();
  if (text === '') return opts.allowEmpty ? undefined : L('Required.', '必填。');
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return L('Must be a finite number.', '必须是有限数字。');
  if (opts.integer && !Number.isInteger(parsed)) return L('Must be an integer.', '必须是整数。');
  if (opts.min !== undefined && parsed < opts.min) return L(`Cannot be below ${opts.min}.`, `不能小于 ${opts.min}。`);
  if (opts.max !== undefined && parsed > opts.max) return L(`Cannot exceed ${opts.max}.`, `不能大于 ${opts.max}。`);
  return undefined;
}
