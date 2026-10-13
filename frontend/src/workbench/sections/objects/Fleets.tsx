import { useState } from 'react';
import type { Project, WorkbenchApi } from '../../api';
import type { Scenario } from '../types';
import { AddFleet, fleetMembers } from './AddFleet';
import { RemoveEntity } from './RemoveEntity';
import { pointer } from '../../form';

/** Fleets are server structures (C11 add_fleet / set_fleet, generated members, or draft generators).
 * This list never mutates the draft itself: adding and editing go through the operations API, and
 * removal goes through the server's own dependency preview (C3) per member — never a client wipe. */
export function Fleets({ api, studioApi, project, draft, onProject, L, onRemoved }: {
  api: WorkbenchApi; studioApi: { request<T = unknown>(path: string, body?: unknown): Promise<T> };
  project: Project; draft: Scenario; onProject: (project: Project) => void;
  L: (en: string, zh: string) => string; onRemoved?: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<{ id: string; count: number; battery_pct?: number; start?: string | number[] }>();
  const [removing, setRemoving] = useState<{ fleet: string; member: string }>();

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
    {removing && <div style={{ border: '1px solid var(--wb-warn)', borderRadius: 10, padding: 10, margin: '6px 0' }}>
      <p style={{ margin: '0 0 6px' }} role="status">{L(
        `The operation vocabulary has no whole-fleet removal, so removal goes member by member with the server's dependency preview. Removing ${removing.member} removes only its own references.`,
        '操作词汇表不支持整体移除机群，因此逐个成员移除，并使用服务器的依赖预览。移除 ' + removing.member + ' 只会移除其自身的引用。')}</p>
      <RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject}
        L={L} entityId={removing.member} onDone={() => { setRemoving(undefined); onRemoved?.(); }} />
    </div>}
  </>;
}

/** Kept for existing imports: the draft pointer of a generator entry (read-only display use). */
export const generatorPath = (name: string) => pointer('generators', name);
