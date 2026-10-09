import { useEffect, useMemo, useRef, useState } from 'react';
import type { EntityKey } from '../contracts/viewer-feed';
import type { EntityState, FeedStore } from '../viewport/feed-store';
import { entityId } from '../viewport/bindings';
import './run-graph.css';
import { readableLabel } from '../pages/inspection-format';

interface EntityGraphProps { store: FeedStore; selected?: EntityKey; onSelect: (key: EntityKey) => void }
interface GraphNode { id: string; entity: EntityState; label: string; color: string; x: number; y: number }
interface GraphLayout { nodes: GraphNode[]; byId: Map<string, GraphNode>; edges: Array<{ edgeId: string; relationId: string; source: GraphNode; target: GraphNode }>; width: number; height: number }

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
  const parts = entity.key.id.split(/[-_/.]+/).filter(Boolean);
  return parts.length ? parts.map(part => part[0].toUpperCase() + part.slice(1)).join(' ') : entity.key.id;
}

/** Stable positions keep labels legible and prevent motion on every timeline cut. */
function computeLayout(store: FeedStore): GraphLayout {
  const nodes: GraphNode[] = [...store.entities.values()].map(entity => ({
    id: entityId(entity.key), entity, label: humanLabel(entity), color: typeColor(store, entity.typeId), x: 0, y: 0,
  })).sort((a, b) => a.entity.typeId.localeCompare(b.entity.typeId) || a.id.localeCompare(b.id));
  const byId = new Map(nodes.map(node => [node.id, node]));
  const edges: GraphLayout['edges'] = [];
  for (const edge of store.edges.values()) {
    const source = byId.get(entityId(edge.source)), target = byId.get(entityId(edge.target));
    if (source && target) edges.push({ edgeId: edge.edgeId, relationId: edge.relationId, source, target });
  }
  const columns = nodes.length <= 8 ? 2 : 3;
  nodes.forEach((node, index) => { node.x = 20 + index % columns * 180; node.y = 24 + Math.floor(index / columns) * 64; });
  return { nodes, byId, edges, width: columns * 180 + 20, height: Math.max(200, Math.ceil(nodes.length / columns) * 64 + 24) };
}

interface Transform { scale: number; x: number; y: number }
const truncate = (label: string) => label.length > 13 ? `${label.slice(0, 12)}…` : label;

/** Readable SVG node-link view of the active entities and relations at the current cut. */
export function EntityGraph({ store, selected, onSelect }: EntityGraphProps) {
  const topology = [...store.entities].map(([id,entity]) => `${id}/${entity.typeId}/${humanLabel(entity)}`).join('|') + [...store.edges.values()].map(edge=>`${edge.edgeId}/${entityId(edge.source)}/${entityId(edge.target)}`).join('|');
  const layout = useMemo(() => computeLayout(store), [topology]);
  const svg = useRef<SVGSVGElement>(null);
  const [transform, setTransform] = useState<Transform>({ scale: 1, x: 0, y: 0 });
  const [query, setQuery] = useState('');
  const drag = useRef<{ x: number; y: number; tx: number; ty: number; moved: boolean }>();

  const selectedId = selected && entityId(selected);
  const neighbors = useMemo(() => {
    const set = new Set<string>();
    if (!selectedId) return set;
    set.add(selectedId);
    for (const edge of layout.edges) if (edge.source.id === selectedId || edge.target.id === selectedId) { set.add(edge.source.id); set.add(edge.target.id); }
    return set;
  }, [layout, selectedId]);
  const fit = () => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect?.width || !rect?.height) return;
    const scale = Math.min((rect.width - 24) / layout.width, (rect.height - 24) / layout.height, 2.5);
    setTransform({ scale, x: (rect.width - layout.width * scale) / 2, y: (rect.height - layout.height * scale) / 2 });
  };
  // Fit when the recorded membership changes, including the initial async feed load.
  const membership = layout.nodes.map(node => node.id).join('\u0000');
  useEffect(fit, [membership]);
  useEffect(() => {
    const observer = new ResizeObserver(fit);
    if (svg.current) observer.observe(svg.current);
    return () => observer.disconnect();
  }, [layout.width,layout.height]);
  const zoom = (factor: number, px: number, py: number) => setTransform(previous => {
    const scale = Math.max(.05, Math.min(8, previous.scale * factor)), ratio = scale / previous.scale;
    return { scale, x: px - (px - previous.x) * ratio, y: py - (py - previous.y) * ratio };
  });
  useEffect(() => {
    const node = svg.current;
    if (!node) return;
    const wheel = (event: WheelEvent) => { event.preventDefault(); const rect = node.getBoundingClientRect(); zoom(Math.exp(-event.deltaY * .002), event.clientX - rect.left, event.clientY - rect.top); };
    node.addEventListener('wheel', wheel, { passive: false });
    return () => node.removeEventListener('wheel', wheel);
  }, []);
  const matches = layout.nodes.filter(node => node.label.toLowerCase().includes(query.toLowerCase()) || node.id.toLowerCase().includes(query.toLowerCase())).slice(0, 100);
  const selectedNode = selectedId && layout.byId.get(selectedId);
  const labelNodes = layout.nodes;
  return <div className="eg-root" data-testid="entity-graph" data-node-count={layout.nodes.length} data-edge-count={layout.edges.length}>
    <div className="eg-controls">
      <b>{layout.nodes.length} entities · {layout.edges.length} active relations</b>
      <button type="button" onClick={fit}>Fit graph</button>
      <button type="button" aria-label="Zoom in graph" onClick={() => zoom(1.4, 200, 150)}>+</button>
      <button type="button" aria-label="Zoom out graph" onClick={() => zoom(1 / 1.4, 200, 150)}>−</button>
      <input aria-label="Search graph entities" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search entities" />
      <select aria-label="Graph entity" value={selectedId ?? ''} onChange={event => { const node = layout.byId.get(event.target.value); if (node) onSelect(node.entity.key); }}>
        <option value="">Select entity</option>
        {selectedNode && !matches.some(node => node.id === selectedId) && <option value={selectedNode.id}>{selectedNode.label} · g{selectedNode.entity.key.generation}</option>}
        {matches.map(node => <option key={node.id} value={node.id}>{node.label} · g{node.entity.key.generation}</option>)}
      </select>
      {!layout.nodes.length && <span>No active entities at this cut.</span>}
    </div>
    <div className="eg-legend" aria-label="Entity type colors">
      {[...new Map(layout.nodes.map(node => [node.entity.typeId, node.color]))].map(([typeId, color]) => {
        const type = store.header.types.find(row => row.typeId === typeId);
        return <span key={typeId} className="eg-legend-item"><i style={{ background: color }} />{readableLabel(type?.displayName === typeId ? typeId : type?.displayName ?? typeId)}</span>;
      })}
    </div>
    <div className="eg-canvas">
      <svg ref={svg} role="img" aria-label="Entity relation graph; drag to pan, scroll to zoom" className="eg-svg"
        onPointerDown={event => { if ((event.target as Element).closest('.eg-node')) return; drag.current = { x: event.clientX, y: event.clientY, tx: transform.x, ty: transform.y, moved: false }; event.currentTarget.setPointerCapture?.(event.pointerId); }}
        onPointerMove={event => { const start = drag.current; if (!start) return; const dx = event.clientX - start.x, dy = event.clientY - start.y; if (Math.hypot(dx, dy) > 3) start.moved = true; if (start.moved) setTransform({ ...transform, x: start.tx + dx, y: start.ty + dy }); }}
        onPointerUp={() => { drag.current = undefined; }}
        onPointerCancel={() => { drag.current = undefined; }}>
        <defs>
          <marker id="eg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#93a8b8" />
          </marker>
          <marker id="eg-arrow-active" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#e78123" />
          </marker>
        </defs>
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}>
          {layout.edges.map(edge => {
            const dx = edge.target.x - edge.source.x, dy = edge.target.y - edge.source.y, distance = Math.hypot(dx, dy) || 1;
            const ux = dx / distance, uy = dy / distance, active = neighbors.has(edge.source.id) && neighbors.has(edge.target.id);
            return <line key={edge.edgeId} className={`eg-edge${active ? ' eg-edge-active' : ''}`} markerEnd={active ? 'url(#eg-arrow-active)' : 'url(#eg-arrow)'}
              x1={edge.source.x + ux * 8} y1={edge.source.y + uy * 8} x2={edge.target.x - ux * 12} y2={edge.target.y - uy * 12}>
              <title>{edge.relationId}</title>
            </line>;
          })}
          {layout.nodes.map(node => {
            const isSelected = node.id === selectedId;
            return <g key={node.id} className="eg-node" role="button" tabIndex={0}
              aria-pressed={isSelected} aria-label={`${node.label}, ${node.entity.typeId}, generation ${node.entity.key.generation}`}
              onClick={event => { event.stopPropagation(); if (!drag.current?.moved) onSelect(node.entity.key); }}
              onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.entity.key); } }}>
              <rect x={node.x - 10} y={node.y - 16} width={170} height={32} fill="transparent" pointerEvents="all" />
              {isSelected && <circle cx={node.x} cy={node.y} r={12} className="eg-node-ring" />}
              <circle cx={node.x} cy={node.y} r={7} fill={node.color} className="eg-node-dot">
                <title>{node.label} · {node.entity.typeId} · generation {node.entity.key.generation}</title>
              </circle>
              {(isSelected || labelNodes.includes(node)) && <text x={node.x + 11} y={node.y + 4} className={`eg-label${isSelected ? ' eg-label-selected' : ''}`} style={{fontSize:`${12 / transform.scale}px`}}>{truncate(node.label)}</text>}
            </g>;
          })}
        </g>
      </svg>
    </div>
  </div>;
}
