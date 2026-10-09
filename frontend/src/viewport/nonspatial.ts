import type { EntityKey, TypeInfo } from '../contracts/viewer-feed';
import type { EntityState, FeedStore } from './feed-store';
import { entityId } from './bindings';

export interface GraphNode { id: string; entity: EntityState; x: number; y: number }
export interface GraphGroup { label: string; ancestry: string; x: number; y: number; width: number; height: number; nodes: GraphNode[] }
export interface GraphLayout { nodes: GraphNode[]; byId: Map<string, GraphNode>; groups: GraphGroup[]; width: number; height: number }

/** An ancestors list is a DAG closure, not an invented single inheritance chain. */
export function ancestryLabel(type: TypeInfo | undefined, types: Map<string, TypeInfo>): string {
  return type?.ancestors.length ? `Ancestors: ${type.ancestors.map(id => `${types.get(id)?.displayName ?? id} (${id})`).join(', ')}` : 'No declared ancestors';
}

/** Stable grouped grid; only entity nodes. Relations never participate in layout. */
export function layoutGraph(store: FeedStore): GraphLayout {
  const types = new Map(store.header.types.map(type => [type.typeId, type]));
  const buckets = new Map<string, EntityState[]>();
  for (const entity of store.entities.values()) {
    let bucket = buckets.get(entity.typeId);
    if (!bucket) { bucket = []; buckets.set(entity.typeId, bucket); }
    bucket.push(entity);
  }
  const ordered = [...buckets].sort(([a], [b]) => {
    const directory = (types.get(a)?.directory ?? '').localeCompare(types.get(b)?.directory ?? '');
    return directory || a.localeCompare(b);
  });
  const groups: GraphGroup[] = ordered.map(([typeId, entities]) => {
    entities.sort((a, b) => entityId(a.key).localeCompare(entityId(b.key)));
    const columns = Math.max(1, Math.ceil(Math.sqrt(entities.length * 1.3)));
    const type = types.get(typeId), width = Math.max(340, columns * 20 + 24), height = Math.ceil(entities.length / columns) * 20 + 70;
    const nodes = entities.map((entity, index) => ({ id: entityId(entity.key), entity, x: 16 + index % columns * 20, y: 58 + Math.floor(index / columns) * 20 }));
    return { label: `${type?.directory ?? 'Directory not recorded'} · ${type?.displayName ?? typeId} (${typeId})`, ancestry: ancestryLabel(type, types), x: 0, y: 0, width, height, nodes };
  });
  const columns = Math.max(1, Math.ceil(Math.sqrt(groups.length)));
  const cellWidth = Math.max(340, ...groups.map(group => group.width)) + 20;
  const cellHeight = Math.max(100, ...groups.map(group => group.height)) + 20;
  const nodes: GraphNode[] = [];
  groups.forEach((group, index) => {
    group.x = index % columns * cellWidth; group.y = Math.floor(index / columns) * cellHeight;
    for (const node of group.nodes) { node.x += group.x; node.y += group.y; nodes.push(node); }
  });
  return { nodes, groups, byId: new Map(nodes.map(node => [node.id, node])), width: columns * cellWidth, height: Math.max(1, Math.ceil(groups.length / columns)) * cellHeight };
}

export function pickNode(layout: GraphLayout, x: number, y: number): EntityKey | undefined {
  return layout.nodes.find(node => Math.hypot(node.x - x, node.y - y) <= 8)?.entity.key;
}

interface FieldIndex { length: number; types: Map<string, string>; fields: Map<string, Set<string>> }
const fieldIndexes = new WeakMap<FeedStore, FieldIndex>();

/** Columns retain recorded fields across retraction/rewind, without inventing values. */
export function recordedFields(store: FeedStore, typeId: string): string[] {
  let index = fieldIndexes.get(store);
  if (!index) { index = { length: 0, types: new Map(), fields: new Map() }; fieldIndexes.set(store, index); }
  for (const commit of store.commits.slice(index.length)) {
    for (const key of commit.created) index.types.set(entityId(key), key.typeId);
    for (const fact of commit.facts) {
      const type = index.types.get(entityId(fact.entity));
      if (type === undefined) throw Error(`Recorded field has no entity type: ${entityId(fact.entity)}`);
      let fields = index.fields.get(type);
      if (!fields) { fields = new Set(); index.fields.set(type, fields); }
      fields.add(fact.fieldId);
    }
  }
  index.length = store.commits.length;
  return [...(index.fields.get(typeId) ?? [])].sort();
}
