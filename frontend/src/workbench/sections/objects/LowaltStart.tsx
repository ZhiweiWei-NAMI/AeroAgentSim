import { useState } from 'react';
import { applyOperations, operation, operationProblem, previewOperations,
  type OperationPreview } from '../operations';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';

/** The Objects header when the workspace lacks the standard low-altitude components.
 * One C11 dry-run diff, shown before Apply; the buttons stay inside the server's vocabulary. */
export function LowaltStart({ api, project, draft, onProject, L }: {
  api: WorkbenchApi; project: Project; draft: Scenario; onProject: (project: Project) => void; L: (en: string, zh: string) => string;
}) {
  const [preview, setPreview] = useState<OperationPreview>();
  const [previewStale, setPreviewStale] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply' | undefined>();
  const [problem, setProblem] = useState<string>();

  const load = async () => {
    setBusy('preview'); setProblem(undefined);
    try {
      const answer = await previewOperations(api, project, [operation('add_lowalt_components')]);
      setPreview(answer); setPreviewStale(false);
    } catch (failure) { setProblem(operationProblem(failure).message); setPreview(undefined); }
    finally { setBusy(undefined); }
  };
  const apply = async () => {
    if (!preview) return;
    setBusy('apply'); setProblem(undefined);
    try {
      const updated = await applyOperations(api, project, [operation('add_lowalt_components')]);
      onProject(updated); setPreview(undefined); setPreviewStale(false);
    } catch (failure) {
      const named = operationProblem(failure);
      setProblem(named.message); setPreviewStale(true);
    } finally { setBusy(undefined); }
  };

  return <div className="wb-empty" data-testid="wb-lowalt-start">
    <strong>{L('Start with standard low-altitude components', '先安装标准低空组件')}</strong>
    <p style={{ margin: '4px 0' }}>{L(
      'This workspace has no drone fleets or service facilities yet. Searching "UAV" will not find one: drone fleets appear after the standard low-altitude components are installed. The button below asks the server for its own dry-run diff first — nothing is invented here.',
      '该工作区还没有无人机机群或服务设施。搜索 "UAV" 不会有结果：无人机机群要在安装标准低空组件之后才会出现。下面的按钮会先向服务器请求预演差异——这里不会虚构任何数据。')}</p>
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <button className="wb-btn primary" disabled={busy !== undefined} onClick={() => void load()}>
        {busy === 'preview' ? L('Preparing diff…', '正在生成差异…') : preview ? L('Re-preview', '重新预览') : L('Add standard low-altitude components', '添加标准低空组件')}</button>
      {preview && <button className="wb-btn go" disabled={busy !== undefined || previewStale} onClick={() => void apply()}>
        {busy === 'apply' ? L('Applying…', '应用中…') : L('Apply', '应用')}</button>}
      <button className="wb-btn ghost" disabled={busy !== undefined} onClick={() => setPreview(undefined)}>{L('Close', '收起')}</button>
    </div>
    {preview && <div style={{ margin: '8px 0 2px' }} role="status">
      <b>{L('Server dry-run diff', '服务器预演差异')}{previewStale ? ` — ${L('out of date; preview again', '已过期；请重新预览')}` : ''}</b>
      <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 12 }}>
        {preview.diff.added.map((row, index) => <li key={`a${index}`}><span className="wb-tag ok">+</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id} <span className="wb-mono" style={{ fontSize: 10, color: 'var(--wb-muted)' }}>{row.id}</span></li>)}
        {preview.diff.changed.map((row, index) => <li key={`c${index}`}><span className="wb-tag warn">~</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id}</li>)}
        {preview.diff.removed.map((row, index) => <li key={`r${index}`}><span className="wb-tag warn">−</span> <span className="wb-mono">{row.kind ?? ''}</span> {row.label ?? row.id}</li>)}
        {!preview.diff.added.length && !preview.diff.changed.length && !preview.diff.removed.length &&
          <li>{L('The server reports no draft changes for this operation.', '服务器报告此操作不会更改草稿。')}</li>}
      </ul>
      {preview.notes.map((note, index) => <p key={index} className="hint" style={{ margin: '2px 0' }}>
        {typeof note === 'string' ? note : note.en ?? note.zh ?? note.text}</p>)}
    </div>}
    {problem && <p role="alert" style={{ color: 'var(--wb-bad)', margin: '6px 0 0' }}>{problem}</p>}
  </div>;
}
