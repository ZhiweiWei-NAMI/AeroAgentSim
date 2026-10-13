import { useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { AddFleet, fleetMembers } from './AddFleet';
import { RemoveEntity } from './RemoveEntity';
import { applyOperations, operation, operationProblem, previewOperations } from '../operations';
import { pointer } from '../../form';

/** Fleets are server structures (C11 add_fleet / set_fleet / remove_fleet, generated members, or draft
 * generators). This list never mutates the draft itself: adding and editing go through the operations
 * API, whole-fleet removal goes through the C11 remove_fleet dry run (member/dependent list → confirm →
 * apply), and only if the server rejects the op (422 unknown op) does removal fall back to the
 * server's own per-member dependency preview (C3) — never a client wipe. */
export function Fleets({ api, studioApi, project, draft, onProject, L, onRemoved }: {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onRemoved?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<{ id: string; count: number; battery_pct?: number; start?: string | number[] }>();
  const [removing, setRemoving] = useState<{ fleet: string; member?: string }>();

  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const uavs = entities.filter(entity => entity.type === 'lowalt:UAV' || String(entity.type).endsWith(':UAV'));
  // Explicit plan fleets: groups of `<id>-<n>` UAV members written by earlier add_fleet operations.
  const generatorPrefix = String((draft.generators as Record<string, any> | undefined)?.fleet?.prefix ?? '')
    .replace(/-\d*$/, '');
  const fleetIds = Array.from(new Set(uavs
    .map(entity => String(entity.id).replace(/-\d+$/, ''))
    .filter(prefix => prefix && prefix !== generatorPrefix)));
  const generator = (draft.generators as Record<string, any> | undefined)?.fleet as Record<string, any> | undefined;

  return <>
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '4px 0' }}>
      <h4 style={{ margin: 0 }}>{L('Fleets', '机群')}</h4>
      <button className="wb-btn small" aria-expanded={adding} aria-pressed={adding}
        onClick={() => { setAdding(value => !value); setEditing(undefined); }}>
        {adding ? L('Close add fleet', '收起添加机群') : L('Add fleet', '添加机群')}</button>
    </div>
    <p className="hint">{L('Fleets are created and changed by server operations; members and counts below come from the server draft.', '机群由服务器操作创建和修改；下方的成员与数量均来自服务器草稿。')}</p>
    {adding && <AddFleet api={api} project={project} draft={draft} onProject={onProject} L={L} onClose={() => setAdding(false)} />}
    {editing && <AddFleet api={api} project={project} draft={draft} onProject={onProject} L={L} editing={editing} onClose={() => setEditing(undefined)} />}
    {fleetIds.map(fleetId => {
      const members = fleetMembers(entities, fleetId);
      return <div key={fleetId} style={{ border: '1px solid var(--wb-line)', borderRadius: 10, padding: '8px 10px', marginBottom: 8 }} data-testid="wb-fleet">
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <b className="wb-mono">{fleetId}</b>
          <span className="wb-tag">{L(`${members.length} member(s)`, `${members.length} 名成员`)}</span>
          <button className="wb-btn small" onClick={() => { setEditing({ id: fleetId, count: members.length }); setAdding(false); }}>{L('Edit', '编辑')}</button>
          <button className="wb-btn small danger" data-testid="wb-fleet-remove" onClick={() => { setRemoving({ fleet: fleetId }); setEditing(undefined); setAdding(false); }}>{L('Remove fleet', '移除机群')}</button>
        </div>
        <div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{members.join(', ') || L('no members', '无成员')}</div>
      </div>;
    })}
    {generator && <div style={{ border: '1px dashed var(--wb-line)', borderRadius: 10, padding: '8px 10px', marginBottom: 8 }} data-testid="wb-fleet-generated">
      <b className="wb-mono">{generatorPrefix || 'fleet'}</b>{' '}
      <span className="wb-tag warn">{L(`will generate ${generator.count ?? '?'}`, `将生成 ${generator.count ?? '?'}`)}</span>
      <div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{String(generator.type ?? '')}</div>
      <p className="wb-add-note" style={{ margin: '4px 0 0' }}>{L(
        'A generated fleet is a starting-point rule owned by the template; resize it through the controls that author it, or remove members individually below (server dependency preview).',
        '生成机群是模板拥有的起点规则；请通过创建它的控件调整规模，或在下方逐个移除成员（服务器依赖预览）。')}</p>
      {Array.from({ length: typeof generator.count === 'number' ? generator.count : 0 }, (_, index) => `${generator.prefix ?? 'fleet-'}${index + 1}`).map(member => <div key={member} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11.5 }}>
        <span className="wb-mono">{member}</span>
        <button className="wb-btn small danger" onClick={() => { setRemoving({ fleet: generatorPrefix || 'fleet', member }); setEditing(undefined); setAdding(false); }}>{L('Remove', '移除')}</button>
      </div>)}
    </div>}
    {!fleetIds.length && !generator && !adding && !editing &&
      <div className="wb-empty">{L('No fleets yet. "Add fleet" creates one through a server operation with a dry-run diff.', '还没有机群。“添加机群”会通过服务器操作创建，并先展示预演差异。')}</div>}
    {removing && (removing.member
      ? <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
          <p style={{ margin: '0 0 6px' }} role="status">{L(
            `Removal goes member by member with the server's dependency preview. Removing ${removing.member} removes only its own references.`,
            '逐个成员移除，并使用服务器的依赖预览。移除 ' + removing.member + ' 只会移除其自身的引用。')}</p>
          <RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject}
            L={L} entityId={removing.member} onDone={() => { setRemoving(undefined); onRemoved?.(); }} />
        </div>
      : <RemoveFleet api={api} project={project} draft={draft} onProject={onProject} studioApi={studioApi} L={L}
          fleet={removing.fleet} onDone={() => { setRemoving(undefined); onRemoved?.(); }} />)}
  </>;
}

/** Whole-fleet removal through C11 `remove_fleet {fleet}`: the dry run lists exactly which members and
 * dependents disappear, confirmation is required, and apply returns the server draft. If this server
 * predates the op (422 "unknown operation"), the flow falls back to the per-member dialog instead of
 * guessing the effect client-side. */
export function RemoveFleet({ api, studioApi, project, draft, onProject, L, fleet, onDone }: {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; fleet: string; onDone: () => void;
}) {
  const [preview, setPreview] = useState<{ added: number; changed: number; removed: Array<{ label?: string; label_zh?: string; id?: string; kind?: string }>; notes: string[] }>();
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'apply'>(), [error, setError] = useState<string>();
  const [fallback, setFallback] = useState(false), [fallbackMember, setFallbackMember] = useState<string>();
  const members = fleetMembers((Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>, fleet);

  const previewNow = async () => {
    setBusy('preview'); setError(undefined);
    try {
      const answer = await previewOperations(api, project, [operation('remove_fleet', { fleet })]);
      setPreview({ added: answer.diff.added.length, changed: answer.diff.changed.length, removed: answer.diff.removed, notes: (answer.notes ?? []).map(note => typeof note === 'string' ? note : note.zh ?? note.text ?? note.en ?? '') });
      setStale(false);
    } catch (problem) {
      const detail = operationProblem(problem);
      // Unknown op (this server has no remove_fleet yet): offer the per-member path instead.
      if (problem instanceof Error && /HTTP 422:/.test(problem.message)) {
        setFallback(true);
        setFallbackMember(members[0]);
        return;
      }
      setError(detail.message);
    } finally { setBusy(undefined); }
  };
  const applyNow = async () => {
    if (!preview) return;
    setBusy('apply'); setError(undefined);
    try {
      onProject(await applyOperations(api, project, [operation('remove_fleet', { fleet })]));
      onDone();
    } catch (problem) {
      const detail = operationProblem(problem);
      if (/HTTP 422:/.test(problem instanceof Error ? problem.message : '')) { setFallback(true); setPreview(undefined); setFallbackMember(members[0]); return; }
      setError(detail.message);
      if (problem instanceof Error && /HTTP 409:/.test(problem.message)) setStale(true);
    } finally { setBusy(undefined); }
  };

  if (fallback) return <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }} data-testid="wb-fleet-fallback">
    <p style={{ margin: '0 0 6px' }} role="status">{L(
      'This server does not support whole-fleet removal yet, so removal falls back to member-by-member removal with the server dependency preview.',
      '此服务器尚不支持整体移除机群，因此改为逐个成员移除，并使用服务器依赖预览。')}</p>
    {fallbackMember
      ? <RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject}
          L={L} entityId={fallbackMember} onDone={() => {
            const rest = members.filter(member => member !== fallbackMember);
            if (rest.length) { setFallbackMember(rest[0]); onRemoved?.(); } else { onDone(); }
          }} />
      : <button className="wb-btn small" onClick={onDone}>{L('Close', '关闭')}</button>}
  </div>;

  return <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }} data-testid="wb-fleet-remove-flow">
    <p style={{ margin: '0 0 6px' }} role="status">{L(
      `Remove the whole fleet ${fleet}? The dry run lists exactly which members and dependents the server will delete; nothing outside that list changes.`,
      `移除整个机群 ${fleet}？预演会精确列出服务器将删除的成员与依赖项；列表之外不会改动。`)}</p>
    {!preview && <button className="wb-btn small danger" disabled={busy === 'preview'} onClick={() => void previewNow()}>
      {busy === 'preview' ? L('Checking…', '正在检查…') : L('Preview removal', '预览移除')}</button>}
    {preview && <div>
      <p className="hint" style={{ margin: '2px 0' }}><b>− {L('Removed', '移除')}</b> · {preview.removed.length}</p>
      <ul style={{ margin: '2px 0', paddingLeft: 18 }}>
        {preview.removed.map((row, index) => <li key={index} className="wb-mono" style={{ fontSize: 11 }}>{row.label ?? row.id ?? row.kind ?? '—'}</li>)}
        {preview.removed.length === 0 && <li className="hint">{L('Nothing — the server reports no removals.', '无——服务器报告不会移除任何内容。')}</li>}
      </ul>
      {preview.notes.map((note, index) => <p key={index} className="hint" style={{ color: 'var(--wb-warn)', margin: '2px 0' }}>ℹ {note}</p>)}
      {stale && <p className="hint" role="status" style={{ color: 'var(--wb-warn)' }}>{L('The draft changed since this preview — preview again.', '草稿在此预览后已变化——请重新预览。')}</p>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button className="wb-btn small danger" disabled={stale || busy === 'apply'} onClick={() => void applyNow()}>
          {busy === 'apply' ? L('Removing…', '移除中…') : L('Confirm removal', '确认移除')}</button>
        <button className="wb-btn small ghost" disabled={busy === 'apply'} onClick={onDone}>{L('Cancel', '取消')}</button>
      </div>
    </div>}
    {error && <p role="alert" style={{ color: 'var(--wb-bad)', margin: '4px 0 0' }}>{error}</p>}
    <p className="hint" style={{ margin: '4px 0 0' }}>{L('Members are <fleet>-1, <fleet>-2, …; dependent references are removed with them atomically.', '成员为 <机群>-1、<机群>-2…；依赖引用会随之被原子移除。')}</p>
  </div>;
}

/** Kept for existing imports: the draft pointer of a generator entry (read-only display use). */
export const generatorPath = (name: string) => pointer('generators', name);
