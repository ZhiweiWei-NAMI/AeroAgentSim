import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { EntityState, FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
import './run-graph.css';
import { readableLabel, displayLabel } from '../pages/inspection-format';

interface EntityGraphProps {
  store: FeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void;
  /** Extra entities to emphasise (for example the ones in a cause trace); always shown, even inside a collapsed group. */
  highlights?: EntityKey[];
}
export interface GraphNode { id: string; entity: EntityState; label: string; color: string; x: number; y: number }
export interface GraphCluster {
  typeId: string; title: string; color: string; members: GraphNode[]; visible: GraphNode[]; collapsible: boolean; expanded: boolean;
  x: number; y: number; w: number; h: number; chip?: { x: number; y: number; w: number; h: number }; anchor: { x: number; y: number };
}
export interface GraphEdge { key: string; relationId: string; count: number; a: { x: number; y: number; node?: GraphNode; cluster: GraphCluster }; b: { x: number; y: number; node?: GraphNode; cluster: GraphCluster } }
export interface GraphLayout { clusters: GraphCluster[]; nodes: GraphNode[]; byId: Map<string, GraphNode>; edges: GraphEdge[]; width: number; height: number }

/** Groups with more members than this start collapsed. */
export const COLLAPSE_ABOVE = 8;
const COL_W = 170, ROW_H = 22, HEADER_H = 30, PAD = 12, GAP = 22, CHIP_H = 28, MAX_ROW_W = 760;

/** Type colors come from the run's presentation bindings, else a stable hash into a light-theme palette. */
const PALETTE = ['#2563eb', '#0d9488', '#b45309', '#6d28d9', '#be123c', '#047857', '#be185d', '#0e7490', '#4d7c0f', '#c2410c'];
function typeColor(store: FeedStore, typeId: string): string {
  const presented = store.header.presentation.find(row => row.typeId === typeId)?.visual.color;
  if (presented) return presented;
  let hash = 0;
  for (const character of typeId) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}

const NAME_FIELDS = ['name', 'label', 'title', 'displayName', 'display_name'];
/** Human label from an actual recorded entity field, else a humanized entity id. Never a fabricated value. */
function humanLabel(entity: EntityState): string {
  for (const [fieldId, fact] of entity.fields) {
    if (typeof fact.value !== 'string' || !fact.value.trim()) continue;
    if (NAME_FIELDS.includes(fieldId) || /name|label|title/i.test(fieldId)) return fact.value.trim();
  }
  return readableLabel(entity.key.id);
}
const plural = (word: string, count: number) => count === 1 || /s$/i.test(word) ? word : /[^aeiou]y$/i.test(word) ? `${word.slice(0, -1)}ies` : `${word}s`;
const natural = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true });

/**
 * Cluster layout: one labelled box per entity type, boxes packed in rows with the
 * most-connected types adjacent. Large groups collapse into one chip unless a
 * member is pinned visible (selected, highlighted or searched).
 */
export function computeLayout(store: FeedStore, expanded: ReadonlySet<string>, pinned: ReadonlySet<string>, maxRowWidth = MAX_ROW_W): GraphLayout {
  const nodes: GraphNode[] = [...store.entities.values()].map(entity => ({ id: entityId(entity.key), entity, label: humanLabel(entity), color: typeColor(store, entity.typeId), x: 0, y: 0 }));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const byType = new Map<string, GraphNode[]>();
  for (const node of nodes) { const rows = byType.get(node.entity.typeId); if (rows) rows.push(node); else byType.set(node.entity.typeId, [node]); }
  // Order clusters so strongly related types sit next to each other.
  const weight = new Map<string, number>();
  for (const edge of store.edges.values()) {
    const a = byId.get(entityId(edge.source))?.entity.typeId, b = byId.get(entityId(edge.target))?.entity.typeId;
    if (!a || !b || a === b) continue;
    const key = [a, b].sort().join('|'); weight.set(key, (weight.get(key) ?? 0) + 1);
  }
  const link = (a: string, b: string) => weight.get([a, b].sort().join('|')) ?? 0;
  const remaining = [...byType.keys()].sort(natural), ordered: string[] = [];
  const degree = (type: string) => remaining.reduce((sum, other) => sum + link(type, other), 0);
  while (remaining.length) {
    let best = 0, score = -1;
    remaining.forEach((type, index) => {
      const value = ordered.length ? ordered.reduce((sum, placed) => sum + link(type, placed), 0) * 1000 + degree(type) : degree(type);
      if (value > score) { score = value; best = index; }
    });
    ordered.push(remaining.splice(best, 1)[0]);
  }
  const clusters: GraphCluster[] = ordered.map(typeId => {
    const members = byType.get(typeId)!.sort((a, b) => natural(a.label, b.label) || natural(a.id, b.id));
    const info = store.header.types.find(row => row.typeId === typeId);
    const name = displayLabel(info?.displayName, typeId), collapsible = members.length > COLLAPSE_ABOVE, open = !collapsible || expanded.has(typeId);
    const visible = open ? members : members.filter(node => pinned.has(node.id));
    const cols = visible.length > 24 ? 3 : visible.length > 8 ? 2 : 1, rows = Math.ceil(visible.length / cols);
    const chipH = collapsible && !open ? CHIP_H : 0;
    const w = PAD * 2 + Math.max(cols * COL_W, 190), h = HEADER_H + chipH + rows * ROW_H + PAD;
    return { typeId, title: `${plural(name, members.length)} (${members.length})`, color: members[0].color, members, visible, collapsible, expanded: open, x: 0, y: 0, w, h, chip: chipH ? { x: PAD, y: HEADER_H, w: w - PAD * 2, h: CHIP_H - 4 } : undefined, anchor: { x: 0, y: 0 } };
  });
  let x = 0, y = 0, rowH = 0, width = 0;
  for (const cluster of clusters) {
    if (x > 0 && x + cluster.w > maxRowWidth) { x = 0; y += rowH + GAP; rowH = 0; }
    cluster.x = x; cluster.y = y; x += cluster.w + GAP; rowH = Math.max(rowH, cluster.h); width = Math.max(width, cluster.x + cluster.w);
    const cols = cluster.visible.length > 24 ? 3 : cluster.visible.length > 8 ? 2 : 1, top = cluster.y + HEADER_H + (cluster.chip ? CHIP_H : 0);
    const rows = Math.ceil(cluster.visible.length / cols);
    cluster.visible.forEach((node, index) => { node.x = cluster.x + PAD + 8 + Math.floor(index / rows) * COL_W; node.y = top + (index % rows) * ROW_H + ROW_H / 2; });
    cluster.anchor = cluster.chip ? { x: cluster.x + cluster.w / 2, y: cluster.y + cluster.chip.y + cluster.chip.h / 2 } : { x: cluster.x + cluster.w / 2, y: cluster.y + HEADER_H / 2 };
  }
  const height = y + rowH;
  const clusterOf = new Map<string, GraphCluster>(); for (const cluster of clusters) for (const node of cluster.members) clusterOf.set(node.id, cluster);
  const shown = new Set(clusters.flatMap(cluster => cluster.visible.map(node => node.id)));
  const merged = new Map<string, GraphEdge>();
  for (const edge of store.edges.values()) {
    const sourceId = entityId(edge.source), targetId = entityId(edge.target), s = byId.get(sourceId), t = byId.get(targetId);
    if (!s || !t) continue;
    const sc = clusterOf.get(sourceId)!, tc = clusterOf.get(targetId)!;
    const end = (node: GraphNode, cluster: GraphCluster) => shown.has(node.id) ? { x: node.x, y: node.y, node, cluster } : { x: cluster.anchor.x, y: cluster.anchor.y, cluster };
    const a = end(s, sc), b = end(t, tc);
    if (a.node && a.node === b.node) continue;
    if (sc === tc && !(a.node && b.node)) continue;
    const key = `${a.node ? sourceId : sc.typeId}|${b.node ? targetId : tc.typeId}|${edge.relationId}`;
    const existing = merged.get(key);
    if (existing) existing.count++; else merged.set(key, { key: a.node && b.node ? edge.edgeId : key, relationId: edge.relationId, count: 1, a, b });
  }
  return { clusters, nodes, byId, edges: [...merged.values()], width: Math.max(width, 240), height: Math.max(height, 120) };
}

interface Transform { scale: number; x: number; y: number }
const truncate = (label: string, max = 22) => label.length > max ? `${label.slice(0, max - 1)}…` : label;

/** Readable SVG cluster view of the active entities and relations at the current cut. */
export function EntityGraph({ store, selected, onSelect, highlights = [] }: EntityGraphProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [query, setQuery] = useState('');
  const [boxWidth, setBoxWidth] = useState(0);
  const [canvasHeight, setCanvasHeight] = useState(340);
  const selectedId = selected && entityId(selected);
  const highlightIds = useMemo(() => new Set(highlights.map(entityId)), [highlights.map(entityId).join('|')]); // eslint-disable-line react-hooks/exhaustive-deps
  const needle = query.trim().toLowerCase();
  const topology = [...store.entities].map(([id, entity]) => `${id}/${entity.typeId}/${humanLabel(entity)}`).join('|') + [...store.edges.values()].map(edge => `${edge.edgeId}/${entityId(edge.source)}/${entityId(edge.target)}`).join('|');
  const pinnedKey = [selectedId ?? '', ...highlightIds, needle].join('\u0000');
  const layout = useMemo(() => {
    const pinned = new Set<string>(highlightIds);
    if (selectedId) pinned.add(selectedId);
    if (needle) for (const [id, entity] of store.entities) if (humanLabel(entity).toLowerCase().includes(needle) || id.toLowerCase().includes(needle)) pinned.add(id);
    // Pack groups to the pane width so labels stay at a readable size instead of shrinking to fit one wide sheet.
    return computeLayout(store, expanded, pinned, boxWidth ? Math.max(400, Math.min(900, boxWidth / .9)) : MAX_ROW_W);
  }, [topology, expanded, pinnedKey, Math.round(boxWidth / 40)]); // eslint-disable-line react-hooks/exhaustive-deps
  const svg = useRef<SVGSVGElement>(null);
  const [transform, setTransform] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean }>();

  const neighbors = useMemo(() => {
    const set = new Set<string>(highlightIds);
    if (!selectedId) return set;
    set.add(selectedId);
    for (const edge of layout.edges) if (edge.a.node?.id === selectedId || edge.b.node?.id === selectedId) { if (edge.a.node) set.add(edge.a.node.id); if (edge.b.node) set.add(edge.b.node.id); }
    return set;
  }, [layout, selectedId, highlightIds]);
  const fit = () => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect?.width) return;
    setBoxWidth(rect.width);
    const scale = Math.min((rect.width - 24) / layout.width, 1);
    setCanvasHeight(Math.max(300, Math.ceil(layout.height * scale + 28)));
    setTransform({ scale, x: (rect.width - layout.width * scale) / 2, y: 12 });
  };
  // Fit when the recorded membership or the open groups change, including the initial async feed load.
  const shape = layout.clusters.map(cluster => `${cluster.typeId}:${cluster.expanded}:${cluster.visible.length}`).join('|');
  useEffect(fit, [shape, layout.width, layout.height]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const observer = new ResizeObserver(fit);
    if (svg.current) observer.observe(svg.current);
    return () => observer.disconnect();
  }, [layout.width, layout.height]); // eslint-disable-line react-hooks/exhaustive-deps
  const zoom = (factor: number, px: number, py: number) => setTransform(previous => {
    const scale = Math.max(.1, Math.min(4, previous.scale * factor)), ratio = scale / previous.scale;
    return { scale, x: px - (px - previous.x) * ratio, y: py - (py - previous.y) * ratio };
  });
  useEffect(() => {
    const node = svg.current;
    if (!node) return;
    const wheel = (event: WheelEvent) => { event.preventDefault(); const rect = node.getBoundingClientRect(); zoom(Math.exp(-event.deltaY * .002), event.clientX - rect.left, event.clientY - rect.top); };
    node.addEventListener('wheel', wheel, { passive: false });
    return () => node.removeEventListener('wheel', wheel);
  }, []);
  const toggle = (typeId: string) => setExpanded(previous => { const next = new Set(previous); if (next.has(typeId)) next.delete(typeId); else next.add(typeId); return next; });
  const collapsible = layout.clusters.filter(cluster => cluster.collapsible);
  const matches = layout.nodes.filter(node => node.label.toLowerCase().includes(needle) || node.id.toLowerCase().includes(needle)).slice(0, 100);
  const selectedNode = selectedId && layout.byId.get(selectedId);
  const relationCount = new Set(layout.edges.map(edge => edge.relationId)).size;
  return <div className="eg-root" data-testid="entity-graph" data-node-count={layout.nodes.length} data-edge-count={layout.edges.length}>
    <div className="eg-controls">
      <b>{layout.nodes.length} entities in {layout.clusters.length} groups · {layout.edges.length} links{relationCount ? ` (${relationCount} kinds)` : ''}</b>
      <button type="button" onClick={fit}>Fit graph</button>
      {collapsible.length > 0 && <button type="button" onClick={() => setExpanded(collapsible.every(cluster => cluster.expanded) ? new Set() : new Set(collapsible.map(cluster => cluster.typeId)))}>{collapsible.every(cluster => cluster.expanded) ? 'Collapse big groups' : 'Expand all groups'}</button>}
      <button type="button" aria-label="Zoom in graph" onClick={() => zoom(1.4, 200, 150)}>+</button>
      <button type="button" aria-label="Zoom out graph" onClick={() => zoom(1 / 1.4, 200, 150)}>−</button>
      <input aria-label="Search graph entities" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search entities" />
      <select aria-label="Graph entity" value={selectedId ?? ''} onChange={event => { const node = layout.byId.get(event.target.value); if (node) onSelect(node.entity.key); }}>
        <option value="">Select entity</option>
        {selectedNode && !matches.some(node => node.id === selectedId) && <option value={selectedNode.id}>{selectedNode.label}</option>}
        {matches.map(node => <option key={node.id} value={node.id}>{node.label}</option>)}
      </select>
      {!layout.nodes.length && <span>No active entities at this cut.</span>}
    </div>
    <p className="eg-hint">Entities are grouped by type. Click a group title to open or close it; selected and highlighted entities always stay visible.</p>
    <div className="eg-canvas">
      <svg ref={svg} role="img" aria-label="Entity relation graph; drag to pan, scroll to zoom" className="eg-svg" style={{ height: canvasHeight }}
        onPointerDown={event => { if ((event.target as Element).closest('.eg-node,.eg-cluster-title')) return; drag.current = { x: event.clientX, y: event.clientY, tx: transform.x, ty: transform.y, moved: false }; event.currentTarget.setPointerCapture?.(event.pointerId); }}
        onPointerMove={event => { const start = drag.current; if (!start) return; const dx = event.clientX - start.x, dy = event.clientY - start.y; if (Math.hypot(dx, dy) > 3) start.moved = true; if (start.moved) setTransform({ ...transform, x: start.tx + dx, y: start.ty + dy }); }}
        onPointerUp={() => { drag.current = undefined; }}
        onPointerCancel={() => { drag.current = undefined; }}>
        <defs>
          <marker id="eg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#93a8b8" /></marker>
          <marker id="eg-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#e78123" /></marker>
        </defs>
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}>
          {layout.clusters.map(cluster => <g key={cluster.typeId} className="eg-cluster">
            <rect x={cluster.x} y={cluster.y} width={cluster.w} height={cluster.h} rx={8} className="eg-cluster-box" style={{ stroke: cluster.color }} />
            <g className="eg-cluster-title" role={cluster.collapsible ? 'button' : undefined} tabIndex={cluster.collapsible ? 0 : undefined} aria-expanded={cluster.collapsible ? cluster.expanded : undefined}
              aria-label={cluster.collapsible ? `${cluster.expanded ? 'Collapse' : 'Expand'} ${cluster.title}` : undefined}
              onClick={() => cluster.collapsible && toggle(cluster.typeId)} onKeyDown={event => { if (cluster.collapsible && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); toggle(cluster.typeId); } }}>
              <rect x={cluster.x} y={cluster.y} width={cluster.w} height={HEADER_H} rx={8} fill={cluster.color} opacity={.14} />
              <text x={cluster.x + PAD} y={cluster.y + 19} className="eg-cluster-label">{cluster.collapsible ? (cluster.expanded ? '▾ ' : '▸ ') : ''}{cluster.title}</text>
            </g>
            {cluster.chip && <g className="eg-cluster-title eg-chip" onClick={() => toggle(cluster.typeId)}>
              <rect x={cluster.x + cluster.chip.x} y={cluster.y + cluster.chip.y} width={cluster.chip.w} height={cluster.chip.h} rx={5} className="eg-chip-box" />
              <text x={cluster.x + cluster.chip.x + 10} y={cluster.y + cluster.chip.y + 16} className="eg-chip-label">Show all {cluster.members.length}{cluster.visible.length ? ` (${cluster.visible.length} pinned below)` : ''}</text>
            </g>}
          </g>)}
          {layout.edges.map(edge => {
            const dx = edge.b.x - edge.a.x, dy = edge.b.y - edge.a.y, distance = Math.hypot(dx, dy) || 1;
            const ux = dx / distance, uy = dy / distance, active = (!!edge.a.node && neighbors.has(edge.a.node.id) && !!edge.b.node && neighbors.has(edge.b.node.id));
            const midX = (edge.a.x + edge.b.x) / 2, midY = (edge.a.y + edge.b.y) / 2;
            return <g key={edge.key}>
              <line className={`eg-edge${active ? ' eg-edge-active' : ''}${edge.a.node && edge.b.node ? '' : ' eg-edge-group'}`} markerEnd={active ? 'url(#eg-arrow-active)' : 'url(#eg-arrow)'}
                x1={edge.a.x + ux * 7} y1={edge.a.y + uy * 7} x2={edge.b.x - ux * 10} y2={edge.b.y - uy * 10}>
                <title>{readableLabel(edge.relationId)}{edge.count > 1 ? ` × ${edge.count}` : ''}</title>
              </line>
              {((active && !!selectedId && (edge.a.node?.id === selectedId || edge.b.node?.id === selectedId)) || edge.count > 1 && !edge.a.node && !edge.b.node) && <text x={midX} y={midY - 3} textAnchor="middle" className="eg-edge-label">{readableLabel(edge.relationId)}{edge.count > 1 ? ` × ${edge.count}` : ''}</text>}
            </g>;
          })}
          {layout.clusters.flatMap(cluster => cluster.visible).map(node => {
            const isSelected = node.id === selectedId, isHighlighted = highlightIds.has(node.id);
            return <g key={node.id} className={`eg-node${isHighlighted ? ' eg-node-highlight' : ''}`} role="button" tabIndex={0}
              aria-pressed={isSelected} aria-label={`${node.label}, ${node.entity.typeId}, generation ${node.entity.key.generation}`}
              onClick={event => { event.stopPropagation(); if (!drag.current?.moved) onSelect(node.entity.key); }}
              onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.entity.key); } }}>
              <rect x={node.x - 10} y={node.y - ROW_H / 2} width={COL_W - 8} height={ROW_H} fill={isHighlighted ? 'rgba(231,129,35,.14)' : 'transparent'} rx={4} pointerEvents="all" />
              {(isSelected || isHighlighted) && <circle cx={node.x} cy={node.y} r={9} className="eg-node-ring" />}
              <circle cx={node.x} cy={node.y} r={5.5} fill={node.color} className="eg-node-dot"><title>{node.label} · {node.entity.typeId} · generation {node.entity.key.generation}</title></circle>
              <text x={node.x + 11} y={node.y + 4} className={`eg-label${isSelected ? ' eg-label-selected' : ''}`}>{truncate(node.label)}</text>
            </g>;
          })}
        </g>
      </svg>
    </div>
  </div>;
}
