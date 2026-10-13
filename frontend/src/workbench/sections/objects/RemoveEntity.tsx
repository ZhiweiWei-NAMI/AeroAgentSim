import { useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import { pointer } from '../../form';
import type { Scenario } from '../types';
import { groupDependencies, type Dependency } from './model';

const KIND_LABELS: Record<string, (L: (en: string, zh: string) => string) => string> = {
  binding: L => L('Field binding', '字段绑定'),
  relation: L => L('Relation', '关系'),
  behaviour: L => L('Behaviour / rule', '行为 / 规则'),
  presentation: L => L('Visual binding', '视觉呈现'),
  engine: L => L('Engine machine', '引擎状态机'),
  generator: L => L('Generated fleet', '批量生成'),
  other: L => L('Other reference', '其他引用'),
};

/** Per-row "Remove": C3 dry-run dependency preview, then an atomic server removal. */
export function RemoveEntity({ api, studioApi, project, draft, onProject, L, entityId, onDone }: {
  api: { save: WorkbenchApi['save'] }; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: { id: string; revision: number }; draft: Scenario;
  onProject: (project: Project) => void; L: (en: string, zh: string) => string; entityId: string; onDone: () => void;
}) {
  const [preview, setPreview] = useState<Dependency[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [gone, setGone] = useState(false);

  const loadPreview = async () => {
    setBusy(true); setError(undefined);
    try {
      const saved = await api.save(project.id, draft as Record<string, unknown>, project.revision);
      onProject(saved);
      const answer = await studioApi.request<{ dependencies: Dependency[] }>(
        `/v1/studio/workspaces/${encodeURIComponent(project.id)}/remove`, { id: entityId, revision: saved.revision, dry_run: true });
      setPreview(answer.dependencies ?? []);
    } catch (problem) {
      // StudioApi errors carry only the message; match the status from it.
      const text = problem instanceof Error ? problem.message : String(problem);
      setError(/HTTP (404|501):/.test(text)
        ? L('This server does not offer removal yet, so the instance cannot be deleted from here. The draft is unchanged.', '当前服务器尚不支持删除，因此无法在此移除该实例。草稿保持不变。')
        : text);
    } finally { setBusy(false); }
  };

  const confirm = async () => {
    setBusy(true); setError(undefined);
    try {
      const saved = await api.save(project.id, draft as Record<string, unknown>, project.revision);
      onProject(saved);
      const updated = await studioApi.request<Project>(
        `/v1/studio/workspaces/${encodeURIComponent(project.id)}/remove`, { id: entityId, revision: saved.revision });
      setGone(true); onProject(updated);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally { setBusy(false); }
  };

  if (gone) return <p className="hint" role="status">{L(`Removed ${entityId} and its dependencies.`, `已移除 ${entityId} 及其依赖。`)}</p>;
  if (preview === undefined) return <div style={{ margin: '6px 0' }}>
    <button className="wb-btn small danger" disabled={busy} onClick={() => void loadPreview()}>{busy ? L('Checking…', '检查中…') : L('Remove', '移除')}</button>
    {error && <p role="alert" style={{ color: 'var(--wb-bad)', margin: '4px 0 0' }}>{error}</p>}
  </div>;
  const groups = groupDependencies(preview);
  return <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
    <p style={{ margin: '0 0 4px' }} role="status">{preview.length
      ? L(`Removing ${entityId} also removes ${preview.length} reference(s):`, `移除 ${entityId} 还会移除 ${preview.length} 处引用：`)
      : L(`No other draft part references ${entityId}.`, `草稿中没有其他部分引用 ${entityId}。`)}</p>
    {groups.map(([kind, rows]) => <details key={kind} open><summary><b>{KIND_LABELS[kind]?.(L) ?? kind}</b> <span className="wb-tag">{rows.length}</span></summary>
      <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
        {rows.map((row, index) => <li key={row.path + index}><span className="wb-mono" style={{ fontSize: 11 }}>{row.path}</span>{row.detail ? ` — ${row.detail}` : ''}</li>)}
      </ul></details>)}
    {error && <p role="alert" style={{ color: 'var(--wb-bad)' }}>{error}</p>}
    <div style={{ display: 'flex', gap: 8 }}>
      <button className="wb-btn small primary" disabled={busy} onClick={() => void confirm()}>{busy ? L('Removing…', '移除中…') : L('Confirm removal', '确认移除')}</button>
      <button className="wb-btn small ghost" disabled={busy} onClick={onDone}>{L('Cancel', '取消')}</button>
    </div>
    <p className="hint" style={{ margin: '4px 0 0' }}>{L('The server removes dependents atomically; a failure preserves the draft.', '服务器会原子地移除全部依赖项；失败时草稿保持不变。')}</p>
  </div>;
}
