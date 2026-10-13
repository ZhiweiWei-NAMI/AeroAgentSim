import { useEffect, useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { AddFleet, fleetEditingOf, fleetMembers, type FleetEditing } from './AddFleet';
import { RemoveEntity } from './RemoveEntity';
import { operation } from '../operations';
import { OperationDiff, operationText, useOperationsReview } from './Operations';
import { pointer } from '../../form';

/** Fleets are server structures (C11 add_fleet / set_fleet / remove_fleet, generated members, or draft
 * generators). This list never mutates the draft itself: adding and editing go through the operations
 * API, whole-fleet removal goes through the C11 remove_fleet dry run (member/dependent list → confirm →
 * apply), and only if the server rejects the op (422 unknown op) does removal fall back to the
 * server's own per-member dependency preview (C3) — never a client wipe. */
export function Fleets({ api, studioApi, project, draft, onProject, L, onRemoved, onAddPlace }: {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onRemoved?: () => void; onAddPlace?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<FleetEditing>();
  const [removing, setRemoving] = useState<{ fleet: string; member?: string }>();

  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const uavs = entities.filter(entity => entity.type === 'lowalt:UAV');
  // Explicit plan fleets: groups of `<id>-<n>` UAV members written by earlier add_fleet operations.
  const generatorPrefix = String((draft.generators as Record<string, any> | undefined)?.fleet?.prefix ?? '')
    .replace(/-\d*$/, '');
  const fleetIds = Array.from(new Set(uavs
    .map(entity => String(entity.id).replace(/-\d+$/, ''))
    .filter(prefix => prefix && prefix !== generatorPrefix)));
  const generator = (draft.generators as Record<string, any> | undefined)?.fleet as Record<string, any> | undefined;
  const generatorMembers = generator && typeof generator.prefix === 'string' && generator.prefix !== ''
    && typeof generator.count === 'number' && Number.isInteger(generator.count) && generator.count >= 0 && generator.count <= 1000
    ? Array.from({ length: generator.count }, (_, index) => `${generator.prefix}${index + 1}`) : undefined;

  return <>
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '4px 0' }}>
      <h4 style={{ margin: 0 }}>{L('Fleets', '机群')}</h4>
      <button className="wb-btn small" aria-expanded={adding} aria-pressed={adding}
        onClick={() => { setAdding(value => !value); setEditing(undefined); }}>
        {adding ? L('Close add fleet', '收起添加机群') : L('Add fleet', '添加机群')}</button>
    </div>
    <p className="hint">{L('Add drones to a fleet, or adjust an existing fleet after reviewing the changes.', '添加无人机群，或查看变更后调整现有机群。')}</p>
    {adding && <AddFleet api={api} project={project} draft={draft} onProject={onProject} L={L} onClose={() => setAdding(false)} onAddPlace={onAddPlace} />}
    {editing && <AddFleet key={editing.id} api={api} project={project} draft={draft} onProject={onProject} L={L} editing={editing} onClose={() => setEditing(undefined)} onAddPlace={onAddPlace} />}
    {fleetIds.map(fleetId => {
      const members = fleetMembers(entities, fleetId);
      return <div key={fleetId} style={{ border: '1px solid var(--wb-line)', borderRadius: 10, padding: '8px 10px', marginBottom: 8 }} data-testid="wb-fleet">
        <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <b className="wb-mono">{fleetId}</b>
          <span className="wb-tag">{L(`${members.length} member(s)`, `${members.length} 名成员`)}</span>
          <button className="wb-btn small" onClick={() => { setEditing(fleetEditingOf(entities, fleetId)); setAdding(false); }}>{L('Edit', '编辑')}</button>
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
      {!generatorMembers && <p className="wb-field-error" role="alert">{L('The generation rule needs a valid count and member prefix. Edit it in File (YAML).', '生成规则需要有效的数量和成员前缀，请在“文件（YAML）”中修正。')}</p>}
      {generatorMembers?.map(member => <div key={member} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11.5 }}>
        <span className="wb-mono">{member}</span>
        <button className="wb-btn small danger" onClick={() => { setRemoving({ fleet: generatorPrefix || 'fleet', member }); setEditing(undefined); setAdding(false); }}>{L('Remove', '移除')}</button>
      </div>)}
    </div>}
    {!fleetIds.length && !generator && !adding && !editing &&
      <div className="wb-empty">{L('No fleets yet. Add a fleet to place drones at a depot.', '还没有机群。添加机群以将无人机放置在仓库。')}</div>}
    {removing && (removing.member
      ? <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
          <p style={{ margin: '0 0 6px' }} role="status">{L(
            `Removal goes member by member with the server's dependency preview. Removing ${removing.member} removes only its own references.`,
            '逐个成员移除，并使用服务器的依赖预览。移除 ' + removing.member + ' 只会移除其自身的引用。')}</p>
          <RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject}
            L={L} entityId={removing.member} onDone={() => { setRemoving(undefined); onRemoved?.(); }} />
        </div>
      : <RemoveFleet api={api} project={project} draft={draft} onProject={onProject} studioApi={studioApi} L={L}
          fleet={removing.fleet} onRemoved={onRemoved} onDone={() => { setRemoving(undefined); onRemoved?.(); }} />)}
  </>;
}

/** Whole-fleet removal through C11 `remove_fleet {fleet}`: the dry run lists exactly which members and
 * dependents disappear, confirmation is required, and apply returns the server draft. If this server
 * predates the op (422 "unknown operation"), the flow falls back to the per-member dialog instead of
 * guessing the effect client-side. */
export function RemoveFleet({ api, studioApi, project, draft, onProject, L, fleet, onDone, onRemoved }: {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; fleet: string; onDone: () => void; onRemoved?: () => void;
}) {
  const [fallback, setFallback] = useState(false), [fallbackMember, setFallbackMember] = useState<string>();
  const members = fleetMembers((Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>, fleet);
  const review = useOperationsReview(api, project, [operation('remove_fleet', { fleet })]);
  useEffect(() => {
    if (review.failure instanceof Error && /HTTP 422:/.test(review.failure.message) && /(?:unknown|unsupported) operation/i.test(review.problem?.message ?? '')) {
      setFallback(true); setFallbackMember(members[0]);
    }
  }, [review.failure]);
  const apply = async () => { const updated = await review.apply(); if (updated) { onProject(updated); onDone(); } };

  if (fallback) return <div className="wb-object-form" data-testid="wb-fleet-fallback">
    <p className="hint" role="status">{L('This server does not support whole-fleet removal. Review and remove each member separately.', '此服务器不支持整体移除机群。请逐个查看并确认移除成员。')}</p>
    {fallbackMember ? <RemoveEntity key={fallbackMember} api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject}
      L={L} entityId={fallbackMember} onDone={() => {
        const rest = members.filter(member => member !== fallbackMember);
        if (rest.length) { setFallbackMember(rest[0]); onRemoved?.(); } else onDone();
      }} /> : <button className="wb-btn small" onClick={onDone}>{L('Close', '关闭')}</button>}
  </div>;

  return <div className="wb-object-form" data-testid="wb-fleet-remove-flow">
    <p className="hint">{L(`Remove fleet ${fleet}? Review the members and references below before confirming.`, `移除机群 ${fleet}？请先查看下方成员与引用，再确认移除。`)}</p>
    {review.preview && <OperationDiff {...review.preview} entities={draft.entities as Array<{ id: string }> | undefined} stale={review.stale} L={L} />}
    {review.problem && <p className="wb-field-error" role="alert">{operationText(review.problem.message, L)}</p>}
    <div className="wb-object-actions">
      <button className="wb-btn small" disabled={!!review.busy} onClick={() => void review.load()}>
        {review.busy === 'preview' ? L('Checking…', '正在检查…') : review.preview ? L('Preview again', '重新预览') : L('Preview removal', '预览移除')}</button>
      {review.preview && <button className="wb-btn small danger" disabled={review.stale || !!review.busy} onClick={() => void apply()}>
        {review.busy === 'apply' ? L('Removing…', '移除中…') : L('Confirm removal', '确认移除')}</button>}
      <button className="wb-btn small ghost" disabled={!!review.busy} onClick={onDone}>{L('Cancel', '取消')}</button>
    </div>
  </div>;
}

/** Kept for existing imports: the draft pointer of a generator entry (read-only display use). */
export const generatorPath = (name: string) => pointer('generators', name);
