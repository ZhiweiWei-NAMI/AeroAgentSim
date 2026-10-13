import { Fragment, useEffect, useMemo, useState } from 'react';
import { Leaf, pointer, TreeForm } from '../form';
import type { SectionProps } from './types';

export function ObjectsSection({ draft, edit, focus, L, onInspectContract }: SectionProps) {
  const entities = (Array.isArray(draft.entities) ? draft.entities : []) as Array<Record<string, any>>;
  const [entityFilter, setEntityFilter] = useState('');
  const shownEntities = useMemo(() => entities.map((entity, index) => ({ entity, index })).filter(({ entity }) => !entityFilter || `${entity.id} ${entity.label ?? ''} ${entity.type}`.toLowerCase().includes(entityFilter.toLowerCase())), [entities, entityFilter]);
  const [openEntity, setOpenEntity] = useState<number>();
  // A jump to an instance clears the filter so the row is visible, and opens it.
  useEffect(() => { const match = /^\/entities\/(\d+)/.exec(focus ?? ''); if (match) { setEntityFilter(''); setOpenEntity(Number(match[1])); } }, [focus]);
  return <>
    <p className="hint">{L('Concrete instances in this world. Each has an identity, a contract type, an initial state and a visual binding.', '世界中的具体实例：每个都有身份、契约类型、初始状态和视觉绑定。')}</p>
    {draft.generators && typeof draft.generators === 'object' && <><h4>{L('Generated fleets', '批量生成的机队')}</h4>
      {Object.entries(draft.generators as Record<string, any>).map(([name, generator]) => <div key={name}>
        <Leaf path={pointer('generators', name, 'count')} label={`${name} · ${L('count', '数量')}`} value={generator.count} onChange={edit} focus={focus} />
        <Leaf path={pointer('generators', name, 'type')} label={`${name} · ${L('type', '类型')}`} value={generator.type} onChange={edit} focus={focus} readOnly />
        <details open={focus?.startsWith(pointer('generators', name))}><summary>{L('Initial state & positions', '初始状态与位置')}</summary>
          <TreeForm value={Object.fromEntries(Object.entries(generator).filter(([key]) => key !== 'count' && key !== 'type'))} path={pointer('generators', name)} onChange={edit} focus={focus} />
        </details>
        {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(generator.type)}>{L('Inspect type contract', '查看类型契约')}</button>}
      </div>)}</>}
    <h4>{L('Instances', '实例')} <span className="wb-tag">{entities.length}</span></h4>
    <input className="wb-search" aria-label={L('Filter instances', '筛选实例')} placeholder={L('Filter by id, label or type', '按 id、名称或类型筛选')} value={entityFilter} onChange={event => setEntityFilter(event.target.value)} />
    <table className="wb-table" style={{ marginTop: 8 }}><thead><tr><th>{L('Instance', '实例')}</th><th>{L('Type', '类型')}</th><th /></tr></thead><tbody>
      {shownEntities.slice(0, 400).map(({ entity, index }) => <Fragment key={index}>
        <tr data-path={pointer('entities', index)} style={focus?.startsWith(pointer('entities', index)) ? { background: 'rgba(224,69,58,.08)' } : undefined}>
          <td><b>{entity.label ?? entity.id}</b><div className="wb-mono" style={{ fontSize: 11, color: 'var(--wb-muted)' }}>{entity.id}</div></td>
          <td className="wb-mono" style={{ fontSize: 11 }}>{entity.type}</td>
          <td><button className="wb-btn small" onClick={() => setOpenEntity(openEntity === index ? undefined : index)}>{openEntity === index ? L('Close', '收起') : L('Edit', '编辑')}</button></td>
        </tr>
        {openEntity === index && <tr><td colSpan={3}><TreeForm value={entity} path={pointer('entities', index)} onChange={edit} focus={focus} />
          {onInspectContract && <button className="wb-btn small ghost" onClick={() => onInspectContract(entity.type)}>{L('Inspect type contract', '查看类型契约')}</button>}</td></tr>}
      </Fragment>)}
    </tbody></table>
    {shownEntities.length > 400 && <p className="hint">{L(`Showing 400 of ${shownEntities.length}; filter to find others.`, `显示前 400 个（共 ${shownEntities.length} 个），请筛选查找其余。`)}</p>}
  </>;
}
