import { Fragment, useEffect, useMemo, useState } from 'react';
import { Leaf, pointer, TreeForm } from '../form';
import type { SectionProps } from './types';
import './objects.css';
import { AddObjects } from './objects/AddObjects';
import { AddPlace } from './objects/AddPlace';
import { Fleets } from './objects/Fleets';
import { LowaltStart } from './objects/LowaltStart';
import { RemoveEntity } from './objects/RemoveEntity';
import { capabilities } from './operations';
import { STUDIO_POS, STUDIO_VEL } from './objects/model';

/** The field ids a studio placement's engines write; shown read-only as ownership. */
const WRITER_FIELD_HINT = (L: (en: string, zh: string) => string) => ({
  [STUDIO_POS]: L('written by the motion/operations engine each step', '由运动/操作引擎每步写入'),
  [STUDIO_VEL]: L('written by the motion engine (kinematic)', '由运动引擎写入（kinematic）'),
  'aas.studio.energy_j': L('written by the motion engine energy model', '由运动引擎的能量模型写入'),
  'aas.studio.state': L('written by the operations engine lifecycle', '由操作引擎的生命周期写入'),
});

export function ObjectsSection({ api, studioApi, project, draft, edit, focus, L, onProject, onInspectContract, saveDraft }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const [entityFilter, setEntityFilter] = useState('');
  const shownEntities = useMemo(() => entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => !entityFilter || `${entity.id} ${entity.label ?? ''} ${entity.type}`.toLowerCase().includes(entityFilter.toLowerCase())), [entities, entityFilter]);
  const [openEntity, setOpenEntity] = useState<number>();
  const [adding, setAdding] = useState(false);
  const [addingPlace, setAddingPlace] = useState(false);
  const [editingPlace, setEditingPlace] = useState<Record<string, any>>();
  const [removing, setRemoving] = useState<string>();
  // A jump to an instance clears the filter so the row is visible, and opens it.
  useEffect(() => { const match = /^\/entities\/(\d+)/.exec(focus ?? ''); if (match) { setEntityFilter(''); setOpenEntity(Number(match[1])); } }, [focus]);
  const generated = Object.values((draft.generators ?? {}) as Record<string, { count?: number }>).reduce((total, generator) => total + (typeof generator.count === 'number' ? generator.count : 0), 0);
  const writerHint = WRITER_FIELD_HINT(L);
  // Capability gate: without the standard low-altitude components the section opens with the
  // server-owned install flow instead of dead-ending a "UAV" search (T4-11).
  const lowalt = capabilities(project)?.lowalt ?? false;
  const places = entities.filter(entity => entity.facts && 'lowalt.site_position' in entity.facts);
  return <>
    {!lowalt && <LowaltStart api={api} project={project} draft={draft} onProject={onProject} L={L} />}
    <p className="hint">{L('Concrete instances in this world. Each has an identity, a contract type, an initial state and a visual binding.', '世界中的具体实例：每个都有身份、契约类型、初始状态和视觉绑定。')}</p>
    <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', marginBottom: 8 }}>
      <span className="wb-tag">{L(`Explicit instances ${entities.length}`, `显式实例 ${entities.length}`)}</span>
      <span className="wb-tag warn">{L(`Will generate ${generated}`, `将生成 ${generated}`)}</span>
      <span className="wb-tag info">{L(`Expected total at t=0 ${entities.length + generated}`, `t=0 预期总数 ${entities.length + generated}`)}</span>
    </div>
    {!entities.length && !generated && !adding && <div className="wb-empty" data-testid="wb-objects-empty">
      <strong>{L('The world has no objects yet', '世界还没有任何对象')}</strong>
      {L('Zero objects is a valid world. Add the first one below — nothing is invented for you.', '零对象也是合法的世界。请在下方添加第一个对象——不会为你虚构任何数据。')}
      <div style={{ marginTop: 8 }}><button className="wb-btn primary" onClick={() => setAdding(true)}>{L('Add objects', '添加对象')}</button></div>
    </div>}
    {lowalt && <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap', margin: '6px 0' }}>
      <button className="wb-btn primary" aria-expanded={addingPlace} aria-pressed={addingPlace}
        onClick={() => { setAddingPlace(value => !value); setEditingPlace(undefined); }}>
        {addingPlace && !editingPlace ? L('Close add place', '收起添加设施') : L('Add place', '添加设施')}</button>
      <button className="wb-btn" aria-expanded={adding} aria-pressed={adding} onClick={() => setAdding(value => !value)}>{adding ? L('Close add objects', '收起添加对象') : L('Add objects (advanced, generic contract)', '添加对象（高级，通用契约）')}</button>
    </div>}
    {lowalt && addingPlace && !editingPlace && <AddPlace api={api} project={project} draft={draft} project_detail={project as never} onProject={onProject} L={L} onClose={() => setAddingPlace(false)} />}
    {lowalt && editingPlace && <AddPlace api={api} project={project} draft={draft} project_detail={project as never} onProject={onProject} L={L}
      onClose={() => setEditingPlace(undefined)}
      editing={{
        id: String(editingPlace.id),
        ...(editingPlace.capacity !== undefined ? { capacity: editingPlace.capacity } : {}),
        ...(editingPlace.preparation_s !== undefined ? { preparation_s: editingPlace.preparation_s } : {}),
        ...(editingPlace.power_w !== undefined ? { power_w: editingPlace.power_w } : {}),
      }} />}
    {adding && <AddObjects api={api} studioApi={studioApi} project={project} draft={draft} project_detail={project as never} onProject={onProject} L={L} onClose={() => setAdding(false)} />}
    <Fleets api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject} L={L} />
    {lowalt && !!places.length && <>
      <h4>{L('Places', '设施')} <span className="wb-tag">{places.length}</span></h4>
      <table className="wb-table"><thead><tr><th>{L('Place', '设施')}</th><th>{L('Type', '类型')}</th><th /></tr></thead><tbody>
        {places.map((entity, index) => <tr key={entity.id}>
          <td><b>{entity.label ?? entity.id}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
          <td>
            <button className="wb-btn small" onClick={() => { setEditingPlace({
              id: entity.id,
              ...(typeof entity.facts?.lowalt?.site_capacity === 'number' ? { capacity: entity.facts.lowalt.site_capacity } : {}),
              ...(typeof entity.facts?.lowalt?.food_preparation_s === 'number' ? { preparation_s: entity.facts.lowalt.food_preparation_s } : {}),
              ...(typeof entity.facts?.lowalt?.pad_power_w === 'number' ? { power_w: entity.facts.lowalt.pad_power_w } : {}),
            }); setAddingPlace(false); }}>{L('Edit', '编辑')}</button>
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
          <td><b>{entity.label ?? entity.id}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
          <td><button className="wb-btn small" onClick={() => { setOpenEntity(openEntity === index ? undefined : index); setRemoving(undefined); }}>{openEntity === index ? L('Close', '收起') : L('Edit', '编辑')}</button>
            {' '}<button className="wb-btn small danger" onClick={() => { setRemoving(entity.id); setOpenEntity(undefined); }}>{L('Remove', '移除')}</button></td>
        </tr>
        {removing === entity.id && <tr><td colSpan={3}><RemoveEntity api={api} studioApi={studioApi} project={project} draft={draft} onProject={onProject} L={L} entityId={entity.id} onDone={() => setRemoving(undefined)} /></td></tr>}
        {openEntity === index && <tr><td colSpan={3}>
          {entity.facts?.['aas.studio.airspace']
            ? <p className="hint">{L('An airspace keeps its polygon, floor and ceiling under aas.studio.airspace — it has no single moving point.', '空域在 aas.studio.airspace 下保存多边形、下限与上限——没有单一的移动点。')}</p>
            : <p className="hint">{L('position is a local ENU metre vector [east, north, up] relative to the workspace origin.', 'position 是相对工作区原点的本地 ENU 米制向量 [东, 北, 天]。')}</p>}
          <TreeForm value={{ ...entity, facts: Object.fromEntries(Object.entries(entity.facts ?? {}).filter(([key]) => key !== STUDIO_POS)) }} path={pointer('entities', index)} onChange={edit} focus={focus} />
          {entity.facts?.['aas.studio.airspace']
            ? <Leaf path={pointer('entities', index, 'facts', 'aas.studio.airspace')} label={L('airspace: polygon, floor_m, ceiling_m', '空域：多边形、下限、上限')} value={entity.facts['aas.studio.airspace']} onChange={edit} focus={focus} />
            : <Leaf path={pointer('entities', index, 'facts', STUDIO_POS)} label={L('position ENU [east, north, up] (m)', '位置 ENU [东, 北, 天]（米）')} value={entity.facts?.[STUDIO_POS] ?? []} onChange={edit} focus={focus} />}
          <p className="hint" style={{ margin: '4px 0' }}>{L('Field ownership (who writes each value at run time):', '字段所有权（运行时由谁写入）：')}</p>
          {Object.entries(entity.facts ?? {}).map(([key, value]) => <div key={key} style={{ fontSize: 11.5 }}>
            <span className="wb-mono">{key}</span> <span className="wb-tag">{(writerHint as Record<string, string>)[key] ?? L('authored initial value', '作者提供的初始值')}</span>
            {value !== null && typeof value === 'object' && <span className="wb-tag wb-mono">{JSON.stringify(value).slice(0, 60)}</span>}
          </div>)}
          {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
        </td></tr>}
      </Fragment>)}
    </tbody></table>
    {shownEntities.length > 400 && <p className="hint">{L(`Showing 400 of ${shownEntities.length}; filter to find others.`, `显示前 400 个（共 ${shownEntities.length} 个），请筛选查找其余。`)}</p>}
  </>;
}
