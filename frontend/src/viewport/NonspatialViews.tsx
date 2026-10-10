import { useEffect, useRef, useState } from 'react';
import type { EntityKey, TypeInfo } from '../contracts/viewer-feed';
import type { FeedStore } from './feed-store';
import { entityId } from './bindings';
import { exactValue } from '../feeds/format';
import { layoutGraph, pickNode, recordedFields } from './nonspatial';
import { useI18n } from '../i18n/I18nProvider';
import { translate } from '../i18n/translate';
import { displayLabel } from '../pages/inspection-format';
import './nonspatial.css';

interface Props { store: FeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void; view: 'graph' | 'state' }
interface Transform { scale: number; x: number; y: number }

function RelationGraph({ store, selected, onSelect }: Omit<Props, 'view'>) {
  const { locale, t } = useI18n();
  const types = new Map(store.header.types.map(type => [type.typeId, type]));
  const ancestry = (type?: TypeInfo) => type?.ancestors.length
    ? t('Ancestors: {ancestors}', { ancestors: type.ancestors.map(id => `${types.get(id)?.displayName ? displayLabel(types.get(id)!.displayName, id, locale) : id} (${id})`).join(', ') })
    : t('No declared ancestors');
  const canvas = useRef<HTMLCanvasElement>(null), layout = layoutGraph(store);
  const [transform, setTransform] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [query, setQuery] = useState('');
  const [canvasError, setCanvasError] = useState(false);
  const [, resize] = useState(0);
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean }>();
  const base = useRef<{ store: FeedStore; signature: string; canvas: HTMLCanvasElement }>();
  const selectedId = selected && entityId(selected);
  const fit = () => {
    if (!canvas.current) return;
    const { width, height } = canvas.current.getBoundingClientRect();
    if (!width || !height) return;
    const scale = Math.max(.05, Math.min((width - 20) / layout.width, (height - 20) / layout.height, 2));
    setTransform({ scale, x: (width - layout.width * scale) / 2, y: (height - layout.height * scale) / 2 });
  };
  // Fit when recorded membership changes, including the initial async feed load.
  const membership = layout.nodes.map(node => node.id).join('\u0000');
  useEffect(fit, [store, membership]);
  useEffect(() => {
    const refresh = () => resize(value => value + 1);
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', refresh);
      return () => window.removeEventListener('resize', refresh);
    }
    const observer = new ResizeObserver(refresh);
    observer.observe(canvas.current!);
    return () => observer.disconnect();
  }, []);
  const zoom = (factor: number, px: number, py: number) => setTransform(previous => {
    const scale = Math.max(.05, Math.min(8, previous.scale * factor)), ratio = scale / previous.scale;
    return { scale, x: px - (px - previous.x) * ratio, y: py - (py - previous.y) * ratio };
  });
  useEffect(() => {
    const node = canvas.current!;
    const wheel = (event: WheelEvent) => { event.preventDefault(); const rect = node.getBoundingClientRect(); zoom(Math.exp(-event.deltaY * .002), event.clientX - rect.left, event.clientY - rect.top); };
    node.addEventListener('wheel', wheel, { passive: false });
    return () => node.removeEventListener('wheel', wheel);
  }, []);
  const zoomAtCenter = (factor: number) => {
    const rect = canvas.current?.getBoundingClientRect();
    if (rect) zoom(factor, rect.width / 2, rect.height / 2);
  };
  useEffect(() => {
    const node = canvas.current!;
    const started = performance.now();
    let context: CanvasRenderingContext2D | null = null;
    try { context = node.getContext('2d', { alpha: false, willReadFrequently: true }); } catch { /* Keep the accessible selection fallback usable. */ }
    if (!context) { setCanvasError(true); return; }
    const rect = node.getBoundingClientRect(), ratio = Math.min(devicePixelRatio, 2);
    if (node.width !== Math.floor(rect.width * ratio)) node.width = rect.width * ratio;
    if (node.height !== Math.floor(rect.height * ratio)) node.height = rect.height * ratio;
    context.setTransform(ratio, 0, 0, ratio, 0, 0); context.fillStyle = '#ffffff'; context.fillRect(0, 0, rect.width, rect.height);
    context.translate(transform.x, transform.y); context.scale(transform.scale, transform.scale);
    const edges = [...store.edges.values()];
    const pairs = edges.map(edge => {
      const source = layout.byId.get(entityId(edge.source)), target = layout.byId.get(entityId(edge.target));
      if (!source || !target) throw Error(`Active relation has absent graph endpoint: ${edge.edgeId}`);
      return { edge, source, target };
    });
    const signature = locale + membership + JSON.stringify(pairs.map(({ edge, source, target }) => [edge.edgeId, source.id, target.id]));
    if (base.current?.store !== store || base.current.signature !== signature) {
      const image = document.createElement('canvas');
      const rasterScale = Math.min(1, 2048 / layout.width, 2048 / layout.height);
      image.width = Math.ceil(layout.width * rasterScale); image.height = Math.ceil(layout.height * rasterScale);
      let paint: CanvasRenderingContext2D | null = null;
      try { paint = image.getContext('2d', { alpha: false, willReadFrequently: true }); } catch { /* Render fallback details instead. */ }
      if (!paint) { setCanvasError(true); return; }
      paint.fillStyle = '#ffffff'; paint.fillRect(0, 0, image.width, image.height); paint.scale(rasterScale, rasterScale); paint.font = '12px system-ui';
      for (const group of layout.groups) {
        paint.fillStyle = '#edf3f8'; paint.fillRect(group.x, group.y, group.width, group.height); paint.fillStyle = '#25475e';
        paint.save(); paint.beginPath(); paint.rect(group.x + 8, group.y + 4, group.width - 16, 40); paint.clip();
        const typeId = group.nodes[0]?.entity.typeId, type = typeId ? types.get(typeId) : undefined;
        const label = `${type?.directory ? translate(locale, type.directory) : t('Directory not recorded')} · ${type?.displayName && typeId ? displayLabel(type.displayName, typeId, locale) : typeId} (${typeId})`;
        paint.fillText(label, group.x + 10, group.y + 20); paint.fillText(ancestry(type), group.x + 10, group.y + 36); paint.restore();
      }
      // Batch edges into one path. Reuse the bitmap while panning/zooming;
      // invalidate only for recorded membership or endpoint changes.
      paint.strokeStyle = '#8ca7ba55'; paint.lineWidth = .7; paint.beginPath();
      for (const { source, target } of pairs) { paint.moveTo(source.x, source.y); paint.lineTo(target.x, target.y); }
      paint.stroke(); paint.fillStyle = '#8ca7ba88'; paint.beginPath();
      for (const { source, target } of pairs) {
        const angle = Math.atan2(target.y - source.y, target.x - source.x), x = target.x - 6 * Math.cos(angle), y = target.y - 6 * Math.sin(angle);
        paint.moveTo(x, y); paint.lineTo(x - 5 * Math.cos(angle - .5), y - 5 * Math.sin(angle - .5)); paint.lineTo(x - 5 * Math.cos(angle + .5), y - 5 * Math.sin(angle + .5)); paint.closePath();
      }
      paint.fill(); paint.fillStyle = '#29668a'; paint.beginPath();
      for (const item of layout.nodes) { paint.moveTo(item.x + 4, item.y); paint.arc(item.x, item.y, 4, 0, Math.PI * 2); }
      paint.fill(); base.current = { store, signature, canvas: image };
    }
    context.drawImage(base.current.canvas, 0, 0, layout.width, layout.height); context.font = '12px system-ui';
    context.strokeStyle = '#da8836'; context.fillStyle = '#da8836'; context.lineWidth = 1.5;
    for (const { source, target } of pairs) {
      if (source.id !== selectedId && target.id !== selectedId) continue;
      context.beginPath(); context.moveTo(source.x, source.y); context.lineTo(target.x, target.y); context.stroke();
      const angle = Math.atan2(target.y - source.y, target.x - source.x), tipX = target.x - 6 * Math.cos(angle), tipY = target.y - 6 * Math.sin(angle);
      context.beginPath(); context.moveTo(tipX, tipY); context.lineTo(tipX - 5 * Math.cos(angle - .5), tipY - 5 * Math.sin(angle - .5)); context.lineTo(tipX - 5 * Math.cos(angle + .5), tipY - 5 * Math.sin(angle + .5)); context.fill();
    }
    let labels = 0;
    for (const item of layout.nodes) {
      const chosen = item.id === selectedId;
      if (chosen) { context.fillStyle = '#e78123'; context.beginPath(); context.arc(item.x, item.y, 7, 0, Math.PI * 2); context.fill(); }
      if (chosen || transform.scale > 1 && labels++ < 80) { context.fillStyle = '#163b52'; context.fillText(item.entity.key.id, item.x + 8, item.y - 5); }
    }
    setCanvasError(false);
    node.dataset.renderMs = String(performance.now() - started);
    node.dataset.nodeCount = String(layout.nodes.length); node.dataset.edgeCount = String(edges.length);
  });
  const matches = layout.nodes.filter(node => node.entity.key.id.toLowerCase().includes(query.toLowerCase()));
  const related = selectedId ? [...store.edges.values()].filter(edge => entityId(edge.source) === selectedId || entityId(edge.target) === selectedId) : [];
  const selectedNode = selectedId && layout.byId.get(selectedId);
  return <div className="ns-root" data-testid="relation-graph" data-node-count={layout.nodes.length} data-edge-count={store.edges.size}>
    <div className="ns-controls"><b>{t('{entities} entities · {relations} active relations', { entities: layout.nodes.length, relations: store.edges.size })}</b>
      <button onClick={fit}>{t('Fit graph')}</button><button aria-label={t('Zoom in graph')} onClick={() => zoomAtCenter(1.4)}>+</button><button aria-label={t('Zoom out graph')} onClick={() => zoomAtCenter(1 / 1.4)}>−</button>
      <input aria-label={t('Search graph entities')} placeholder={t('Search entity IDs')} type="search" value={query} onChange={event => setQuery(event.target.value)} />
      <select aria-label={t('Graph entity')} value={selectedNode ? selectedId : ''} onChange={event => { const node = layout.byId.get(event.target.value); if (node) onSelect(node.entity.key); }}>
        <option value="">{t('Select entity')}</option>{selectedNode && !matches.slice(0, 100).some(node => node.id === selectedId) && <option value={selectedNode.id}>{selectedNode.entity.key.id}</option>}
        {matches.slice(0, 100).map(node => <option key={node.id} value={node.id}>{node.entity.key.id} · g{node.entity.key.generation}</option>)}
      </select>{matches.length > 100 && <span>{t('Showing 100 matches; refine search')}</span>}
    </div>
    {selectedNode && <div className="ns-ancestry">{types.get(selectedNode.entity.typeId)?.directory ? translate(locale, types.get(selectedNode.entity.typeId)!.directory!) : t('Directory not recorded')} · {selectedNode.entity.typeId} · {ancestry(types.get(selectedNode.entity.typeId))}</div>}
    {canvasError && <p role="alert">{t('Canvas 2D is unavailable. Entity selection and recorded relation details remain available below.')}</p>}
    <div className="ns-canvas-wrap"><canvas ref={canvas} data-testid="topology-canvas" aria-label={t('Relation topology; drag to pan, scroll to zoom, use Graph entity to select')} role="img"
      onPointerDown={event => { drag.current = { x: event.clientX, y: event.clientY, tx: transform.x, ty: transform.y, moved: false }; event.currentTarget.setPointerCapture?.(event.pointerId); }}
      onPointerMove={event => { const start = drag.current; if (!start) return; const dx = event.clientX - start.x, dy = event.clientY - start.y; if (Math.hypot(dx, dy) > 3) start.moved = true; if (start.moved) setTransform({ ...transform, x: start.tx + dx, y: start.ty + dy }); }}
      onPointerUp={event => { if (drag.current && !drag.current.moved) { const rect = event.currentTarget.getBoundingClientRect(); const key = pickNode(layout, (event.clientX - rect.left - transform.x) / transform.scale, (event.clientY - rect.top - transform.y) / transform.scale); if (key) onSelect(key); } drag.current = undefined; }}
      onPointerCancel={() => { drag.current = undefined; }} /></div>
    <details className="ns-relations" open={related.length > 0}><summary>{t('Selected entity · {count} active relations at replay cut', { count: related.length })}</summary>
      {related.slice(0, 100).map(edge => <div key={edge.edgeId} className="ns-relation"><code>{edge.edgeId} · {edge.relationId}</code>
        <div><button onClick={() => onSelect(edge.source)}>{edge.source.id}</button> → <button onClick={() => onSelect(edge.target)}>{edge.target.id}</button></div>
        <small>{t('Valid from {from} · valid to {to} · available {available}', { from: edge.validFrom === undefined ? t('Not recorded') : exactValue(edge.validFrom), to: edge.validTo === undefined ? t('Not recorded') : exactValue(edge.validTo), available: edge.available === undefined ? t('Not recorded') : exactValue(edge.available) })}</small>
      </div>)}{related.length > 100 && <p>{t('Showing first 100 relations of {count}.', { count: related.length })}</p>}
    </details>
  </div>;
}

function StateTable({ store, selected, onSelect }: Omit<Props, 'view'>) {
  const { locale, t } = useI18n();
  const [typeId, setTypeId] = useState<string>(), [query, setQuery] = useState(''), [page, setPage] = useState(0);
  const selectedId = selected && entityId(selected);
  useEffect(() => {
    const entity = selectedId ? store.entities.get(selectedId) : undefined;
    if (!entity) return;
    setTypeId(entity.typeId); setQuery('');
    const peers = [...store.entities.values()].filter(peer => peer.typeId === entity.typeId).sort((a, b) => entityId(a.key).localeCompare(entityId(b.key)));
    setPage(Math.floor(peers.findIndex(peer => entityId(peer.key) === selectedId) / 50));
  }, [store, selectedId]);
  const activeTypes = new Set([...store.entities.values()].map(entity => entity.typeId));
  const types = store.header.types.filter(type => activeTypes.has(type.typeId));
  const chosen = types.find(type => type.typeId === typeId) ?? types.find(type => type.typeId === (selectedId ? store.entities.get(selectedId)?.typeId : undefined)) ?? types[0];
  const rows = [...store.entities.values()].filter(entity => entity.typeId === chosen?.typeId && entity.key.id.toLowerCase().includes(query.toLowerCase())).sort((a, b) => entityId(a.key).localeCompare(entityId(b.key)));
  const columns = chosen ? recordedFields(store, chosen.typeId) : [];
  const pages = Math.max(1, Math.ceil(rows.length / 50)), current = Math.min(page, pages - 1);
  const fields = new Map(store.header.fields.map(field => [field.fieldId, field]));
  return <div className="ns-root" data-testid="state-view">
    <div className="ns-controls"><label>{t('Recorded type')} <select aria-label={t('State type')} value={chosen?.typeId ?? ''} onChange={event => { setTypeId(event.target.value); setPage(0); }}>
      {types.map(type => <option key={type.typeId} value={type.typeId}>{type.directory ? translate(locale, type.directory) + ' · ' : ''}{displayLabel(type.displayName, type.typeId, locale)} ({type.typeId})</option>)}
    </select></label><input type="search" aria-label={t('Search state entities')} placeholder={t('Search entity IDs')} value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></div>
    <p className="ns-note">{t('Current committed values at replay cut. Select a row for full fact stamps. Absent means no committed fact at this cut.')}</p>
    <div className="ns-table-wrap"><table aria-label={t('Recorded entity state')}><thead><tr><th scope="col">{t('Entity / generation')}</th>{columns.map(id => <th scope="col" key={id} title={id}>{fields.get(id)?.displayName ? displayLabel(fields.get(id)!.displayName, id, locale) : id}{fields.get(id)?.unit && ` · ${fields.get(id)!.unit}`}</th>)}</tr></thead>
      <tbody>{rows.slice(current * 50, (current + 1) * 50).map(entity => <tr key={entityId(entity.key)} aria-selected={selectedId === entityId(entity.key)}>
        <th scope="row"><button onClick={() => onSelect(entity.key)}>{entity.key.id} · g{entity.key.generation}</button></th>
        {columns.map(id => { const fact = entity.fields.get(id), missing = t('Not recorded'); return <td key={id} title={fact ? t('{producer} · valid from {from} · valid to {to} · available {available} · version {version}', { producer: fact.producer, from: exactValue(fact.validFrom), to: fact.validTo === undefined ? missing : exactValue(fact.validTo), available: fact.available === undefined ? missing : exactValue(fact.available), version: fact.version === undefined ? missing : exactValue(fact.version) }) : t('No committed fact at this cut')}><code>{fact ? exactValue(fact.value) : t('Absent')}</code></td>; })}
      </tr>)}</tbody></table>{!rows.length && <p>{query ? t('No entities match this search.') : t('No entities at this cut.')}</p>}</div>
    <div className="ns-controls"><button aria-label={t('Previous state page')} disabled={current === 0} onClick={() => setPage(current - 1)}>{t('Previous')}</button>
      <span>{t('{count} entities · page {page} / {pages}', { count: rows.length, page: current + 1, pages })}</span><button aria-label={t('Next state page')} disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>{t('Next')}</button></div>
  </div>;
}

export function NonspatialViews(props: Props) { return props.view === 'graph' ? <RelationGraph {...props} /> : <StateTable {...props} />; }
