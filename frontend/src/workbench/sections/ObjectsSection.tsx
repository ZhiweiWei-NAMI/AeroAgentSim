import { Fragment, useEffect, useMemo, useState, type ComponentProps } from 'react';
import { pointer, TreeForm } from '../form';
import type { SectionProps } from './types';
import './objects.css';
import { AddObjects } from './objects/AddObjects';
import { AddPlace } from './objects/AddPlace';
import { Fleets } from './objects/Fleets';
import { LowaltStart } from './objects/LowaltStart';
import { RemoveEntity } from './objects/RemoveEntity';
import { capabilities } from './operations';
import { instancePositionField, placeEditingOf } from './objects/model';
import { PositionField } from './objects/PositionField';

/** Placement binds each authored fact explicitly; a field name does not identify its writer. */
function fieldOwnership(draft: SectionProps['draft'], entity: string, field: string, L: SectionProps['L']) {
  const bindings = draft.bindings as { exact?: Array<{ entity: string; field: string; writer: string }>; rules?: unknown[] } | undefined;
  const exact = bindings?.exact?.filter(binding => binding.entity === entity && binding.field === field) ?? [];
  if (exact.length > 1) return L('Conflicting writer bindings', '写入方绑定冲突');
  if (!exact.length) return bindings?.rules?.length
    ? L('Authored initial value · see type bindings in Execution components', '作者提供的初始值 · 类型绑定见执行组件')
    : L('Authored initial value', '作者提供的初始值');
  const writer = exact[0].writer;
  const engine = (draft.engines as Record<string, { plugin?: string }> | undefined)?.[writer];
  return engine?.plugin
    ? L(`Authored initial value · runtime writer: ${writer} (${engine.plugin})`, `作者提供的初始值 · 运行时写入方：${writer}（${engine.plugin}）`)
    : L(`Authored initial value · configured writer is missing: ${writer}`, `作者提供的初始值 · 缺少已绑定的写入组件：${writer}`);
}

export function ObjectsSection({ api, studioApi, project, draft, edit, focus, L, onProject, onInspectContract, reportValidity, saveDraft, goTo }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const [entityFilter, setEntityFilter] = useState('');
  const shownEntities = useMemo(() => entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => !entityFilter || `${entity.id} ${entity.label ?? ''} ${entity.type}`.toLowerCase().includes(entityFilter.toLowerCase())), [entities, entityFilter]);
  const [openEntity, setOpenEntity] = useState<number>();
  const [adding, setAdding] = useState(false);
  const [addingPlace, setAddingPlace] = useState(false);
  const [editingPlace, setEditingPlace] = useState<ComponentProps<typeof AddPlace>['editing']>();
  const [removing, setRemoving] = useState<string>();
  useEffect(() => {
    if (focus !== 'add-place') return;
    setAddingPlace(true); setEditingPlace(undefined); setOpenEntity(undefined);
    goTo?.('objects');
  }, [focus]);
  // A jump to an instance clears the filter so the row is visible, and opens it.
  useEffect(() => { const match = /^\/entities\/(\d+)/.exec(focus ?? ''); if (match) { setEntityFilter(''); setOpenEntity(Number(match[1])); } }, [focus]);
  const generators = Object.values((draft.generators ?? {}) as Record<string, { count?: number }>);
  const generated = generators.every(generator => generator && typeof generator.count === 'number' && Number.isInteger(generator.count) && generator.count >= 0)
    ? generators.reduce((total, generator) => total + generator.count!, 0) : undefined;
  // Capability gate: without the standard low-altitude components the section opens with the
  // server-owned install flow instead of dead-ending a "UAV" search (T4-11).
  const lowalt = capabilities(project)?.lowalt ?? false;
  const places = entities.filter(entity => entity.facts && 'lowalt.site_position' in entity.facts);
  const bootstrap = <LowaltStart api={api} project={project} draft={draft} onProject={onProject} L={L}
    onAddObjects={() => setAdding(value => !value)} genericOpen={adding} />;
  const genericForm = adding && <AddObjects api={api} studioApi={studioApi} project={project} draft={draft} project_detail={project} onProject={onProject} L={L} onClose={() => setAdding(false)} saveDraft={saveDraft} />;
  // The generic action lives in the bootstrap card too, including after the first placement.
  if (!lowalt && !entities.length && generated === 0) return <>{bootstrap}{genericForm}</>;
  return <>
    {!lowalt && bootstrap}
    <p className="hint">{L('Places, fleets and other objects in this world.', '这个世界中的地点、机队与其他对象。')}</p>
    <p className="wb-objects-count">
      {generated === undefined ? L('Generation count is incomplete', '生成数量不完整')
        : L(`${entities.length} object${entities.length === 1 ? '' : 's'} · ${generated} generated at start`, `${entities.length} 个对象 · 开始时生成 ${generated} 个`)}
    </p>
    {lowalt && !entities.length && generated === 0 && !adding && <div className="wb-empty" data-testid="wb-objects-empty">
      <strong>{L('The world has no objects yet', '世界还没有任何对象')}</strong>
      {L('Add a place, fleet or object to start.', '添加设施、机群或对象以开始。')}
    </div>}
    {lowalt && <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '6px 0' }}>
      <button className="wb-btn primary" aria-expanded={addingPlace} aria-pressed={addingPlace}
        onClick={() => { setAddingPlace(value => !value); setEditingPlace(undefined); }}>
        {addingPlace && !editingPlace ? L('Close add place', '收起添加设施') : L('Add place', '添加设施')}</button>
      <button className="wb-btn" aria-expanded={adding} aria-pressed={adding} onClick={() => setAdding(value => !value)}>{adding ? L('Close add objects', '收起添加对象') : L('Add objects (advanced)', '添加对象（高级）')}</button>
    </div>}
    {lowalt && addingPlace && !editingPlace && <AddPlace api={api} project={project} draft={draft} project_detail={project as never} onProject={onProject} L={L} onClose={() => setAddingPlace(false)} />}
    {lowalt && editingPlace && <AddPlace key={editingPlace.id} api={api} project={project} draft={draft} project_detail={project} onProject={onProject} L={L}
      onClose={() => setEditingPlace(undefined)}
      editing={editingPlace} />}
    {genericForm}
    {lowalt && <Fleets api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject} L={L}
      onAddPlace={() => { setAddingPlace(true); setEditingPlace(undefined); }} />}
    {lowalt && !!places.length && <>
      <h4>{L('Places', '设施')} <span className="wb-tag">{places.length}</span></h4>
      <table className="wb-table"><thead><tr><th>{L('Place', '设施')}</th><th>{L('Type', '类型')}</th><th /></tr></thead><tbody>
        {places.map(entity => <tr key={entity.id}>
          <td><b>{L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id)}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
          <td>
            <button className="wb-btn small" onClick={() => { setEditingPlace(placeEditingOf(entity)); setAddingPlace(false); setOpenEntity(undefined); }}>{L('Edit', '编辑')}</button>
            {' '}<button className="wb-btn small danger" onClick={() => { setRemoving(entity.id); setEditingPlace(undefined); setOpenEntity(undefined); }}>{L('Remove', '移除')}</button>
          </td>
        </tr>)}
      </tbody></table>
    </>}
    <h4>{L('Instances', '实例')} <span className="wb-tag">{entities.length}</span></h4>
    <input className="wb-search" aria-label={L('Filter instances', '筛选实例')} placeholder={L('Filter by id, label or type', '按 id、名称或类型筛选')} value={entityFilter} onChange={event => setEntityFilter(event.target.value)} />
    <table className="wb-table" style={{ marginTop: 8 }}><thead><tr><th>{L('Instance', '实例')}</th><th>{L('Type', '类型')}</th><th /></tr></thead><tbody>
      {shownEntities.slice(0, 400).map(({ entity, index }) => <Fragment key={index}>
        <tr data-path={pointer('entities', index)} style={focus?.startsWith(pointer('entities', index)) ? { background: 'rgba(224,69,58,.08)' } : undefined}>
          <td><b>{L(entity.label ?? entity.id, entity.label_zh ?? entity.label ?? entity.id)}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
          <td><button className="wb-btn small" onClick={() => { setOpenEntity(openEntity === index ? undefined : index); setRemoving(undefined); setEditingPlace(undefined); }}>{openEntity === index ? L('Close', '收起') : L('Edit', '编辑')}</button>
            {' '}<button className="wb-btn small danger" onClick={() => { setRemoving(entity.id); setOpenEntity(undefined); }}>{L('Remove', '移除')}</button></td>
        </tr>
        {removing === entity.id && <tr><td colSpan={3}><RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject} L={L} entityId={entity.id} onDone={() => setRemoving(undefined)} /></td></tr>}
        {openEntity === index && <tr><td colSpan={3}>
          {'lowalt.site_position' in (entity.facts ?? {})
            ? <AddPlace key={entity.id} api={api} project={project} draft={draft} project_detail={project} onProject={onProject} L={L}
                editing={placeEditingOf(entity)} onClose={() => setOpenEntity(undefined)} />
            : <>
              {entity.facts?.['aas.studio.airspace']
                ? <p className="hint">{L('An airspace has a polygon, floor and ceiling, with no single moving point.', '空域包含多边形、下限与上限，没有单一的移动点。')}</p>
                : instancePositionField(draft, entity) && <PositionField value={entity.facts?.[instancePositionField(draft, entity)!]}
                    path={pointer('entities', index, 'facts', instancePositionField(draft, entity)!)} onChange={edit} reportValidity={reportValidity} focus={focus} L={L} />}
              <details open={focus?.startsWith(pointer('entities', index)) || undefined}><summary>{L('Advanced', '高级')}</summary>
                <TreeForm value={{ ...entity, facts: Object.fromEntries(Object.entries(entity.facts ?? {}).filter(([key]) => key !== instancePositionField(draft, entity))) }}
                  path={pointer('entities', index)} onChange={edit} focus={focus} />
              </details>
            </>}
          <p className="hint" style={{ margin: '4px 0' }}>{L('Field ownership (who writes each value at run time):', '字段所有权（运行时由谁写入）：')}</p>
          {Object.entries(entity.facts ?? {}).map(([key, value]) => <div key={key} style={{ fontSize: 11.5 }}>
            <span className="wb-mono">{key}</span> <span className="wb-tag">{fieldOwnership(draft, entity.id, key, L)}</span>
            {value !== null && typeof value === 'object' && <span className="wb-tag wb-mono">{JSON.stringify(value).slice(0, 60)}</span>}
          </div>)}
          {goTo && <button className="wb-btn small ghost" onClick={() => goTo('components', '/bindings')}>{L('View runtime field bindings', '查看运行时字段绑定')}</button>}
          {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
        </td></tr>}
      </Fragment>)}
    </tbody></table>
    {shownEntities.length > 400 && <p className="hint">{L(`Showing 400 of ${shownEntities.length}; filter to find others.`, `显示前 400 个（共 ${shownEntities.length} 个），请筛选查找其余。`)}</p>}
  </>;
}
