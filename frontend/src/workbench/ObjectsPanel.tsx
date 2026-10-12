import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { TemporalFeedStore } from '../feeds/temporal-store';
import { useI18n } from '../i18n/I18nProvider';
import { readableLabel } from '../pages/inspection-format';
import { entityId, resolveBinding } from '../viewport/bindings';
import { displayName } from '../viewport/semantic';
import { sameEntity } from './useRunSession';

const PALETTE = ['#1f7ae0', '#e8a317', '#1fae6b', '#b26bff', '#e0453a', '#38c6f4', '#ff7a45', '#5b6b7d'];

/** Every concrete object of the world, grouped by type; selecting here selects everywhere. */
export function ObjectsPanel({ store, revision, selected, selectionOrigin, onSelect, onClose }: { store: TemporalFeedStore; revision: number; selected?: EntityKey; selectionOrigin: 'manual' | 'director'; onSelect: (key: EntityKey, focus: boolean) => void; onClose: () => void }) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN', L = (en: string, cn: string) => zh ? cn : en;
  const [query, setQuery] = useState(''), [toggled, setToggled] = useState<Set<string>>(new Set()), [spatialOnly, setSpatialOnly] = useState(false);
  const panel = useRef<HTMLElement>(null), body = useRef<HTMLDivElement>(null), selectedId = selected && entityId(selected);
  const selectedPresent = !!selectedId && store.entities.has(selectedId);
  const revealSelection = selectionOrigin === 'manual' || !query.trim();
  useEffect(() => {
    // Live camera cues must not erase or move the list while someone searches.
    // Explicit selections from the graph/scene still reveal their object.
    if (!revealSelection) return;
    const entity = selectedId && store.entities.get(selectedId);
    if (!entity) return;
    const spatial = !!resolveBinding(store.header, entity.typeId);
    setToggled(previous => {
      const next = new Set(previous);
      if (spatial) next.delete(entity.typeId); else next.add(entity.typeId);
      return next;
    });
    if (!spatial) setSpatialOnly(false);
    const name = displayName(entity, store.header, locale);
    if (query && !`${name} ${entity.key.id} ${entity.typeId}`.toLowerCase().includes(query.trim().toLowerCase())) setQuery('');
  }, [store, selectedId, selectedPresent, selectionOrigin, revealSelection]);
  useEffect(() => { if (revealSelection) panel.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' }); }, [selectedId, toggled, selectionOrigin, revealSelection]);
  const reveal = useRef(revealSelection); reveal.current = revealSelection;
  useEffect(() => {
    const observer = new ResizeObserver(() => { if (reveal.current) body.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' }); });
    if (body.current) observer.observe(body.current);
    return () => observer.disconnect();
  }, []);
  const groups = useMemo(() => {
    const byType = new Map<string, Array<{ key: EntityKey; name: string; spatial: boolean }>>();
    const needle = query.trim().toLowerCase();
    for (const entity of store.entities.values()) {
      const name = displayName(entity, store.header, locale), spatial = !!resolveBinding(store.header, entity.typeId);
      if (spatialOnly && !spatial) continue;
      if (needle && !`${name} ${entity.key.id} ${entity.typeId}`.toLowerCase().includes(needle)) continue;
      const rows = byType.get(entity.typeId) ?? []; rows.push({ key: entity.key, name, spatial }); byType.set(entity.typeId, rows);
    }
    return [...byType.entries()].sort((a, b) => Number(b[1][0]?.spatial) - Number(a[1][0]?.spatial) || a[0].localeCompare(b[0]));
  }, [store, revision, query, locale, spatialOnly, store.entities.size]);
  const total = groups.reduce((sum, [, rows]) => sum + rows.length, 0);
  return <aside ref={panel} className="wb-panel wb-left wb-glass" aria-label={L('Objects', '对象')} data-testid="wb-objects">
    <div className="wb-panel-head"><h3>{L('Objects', '对象')} <span className="wb-tag">{total}</span></h3><button className="wb-x" aria-label={L('Hide objects', '隐藏对象列表')} onClick={onClose}>‹</button></div>
    <div style={{ padding: '0 14px 6px' }}>
      <input className="wb-search" placeholder={L('Search objects…', '搜索对象…')} value={query} onChange={event => setQuery(event.target.value)} aria-label={L('Search objects', '搜索对象')} />
      <label className="hint" style={{ display: 'flex', gap: 6, alignItems: 'center', marginTop: 6, fontSize: 12, color: 'var(--wb-muted)' }}><input type="checkbox" checked={spatialOnly} onChange={event => setSpatialOnly(event.target.checked)} />{L('Only objects in the 3D scene', '只看三维场景中的对象')}</label>
    </div>
    <div ref={body} className="wb-panel-body">
      {groups.map(([typeId, rows], index) => {
        const binding = resolveBinding(store.header, typeId) as { visual?: { color?: string } } | undefined;
        // Groups in the 3D scene start open; internal non-spatial groups start closed.
        const color = binding?.visual?.color ?? PALETTE[index % PALETTE.length], open = (!!binding !== toggled.has(typeId)) || !!query;
        return <div className="wb-group" key={typeId}>
          <button className="wb-group-toggle" aria-expanded={open} onClick={() => setToggled(previous => { const next = new Set(previous); if (next.has(typeId)) next.delete(typeId); else next.add(typeId); return next; })}>
            <span className="wb-dot" style={{ background: color }} />{readableLabel(typeId, locale)}<b>{rows.length}</b><span aria-hidden>{open ? '▾' : '▸'}</span>
          </button>
          {open && rows.slice(0, 300).map(row => <button key={entityId(row.key)} className="wb-row" aria-current={sameEntity(row.key, selected)} onClick={() => onSelect(row.key, row.spatial)} title={row.key.id}>
            <span className="wb-row-main"><div>{row.name}</div><small className="wb-mono">{row.key.id}</small></span>
            {!row.spatial && <span className="wb-tag" title={L('No position: inspect it in the graph and inspector', '无空间位置：在关系图和检查器中查看')}>{L('non-spatial', '非空间')}</span>}
          </button>)}
          {open && rows.length > 300 && <div className="hint" style={{ padding: '4px 8px' }}>{L(`+${rows.length - 300} more — search to narrow`, `还有 ${rows.length - 300} 个，请搜索缩小范围`)}</div>}
        </div>;
      })}
      {!total && <div className="wb-empty">{store.entities.size ? L('No object matches.', '没有匹配的对象。') : L('The world has no objects yet.', '世界中还没有对象。')}</div>}
    </div>
  </aside>;
}
