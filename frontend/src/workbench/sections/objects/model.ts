import { useEffect, useState } from 'react';
import type { StudioApi, TypeRow } from '../../../studio/api';
import type { Scenario } from '../types';

export const STUDIO_POS = 'aas.studio.position_enu_m';
export const STUDIO_VEL = 'aas.studio.velocity_enu_m_s';
export const STUDIO_STATE = 'aas.studio.state';
export const STUDIO_AIRSPACE = 'aas:StudioAirspace';

/** Server defaults for a studio placement; real modeling assumptions, not UI decorations. */
export const KINEMATIC_DEFAULTS = { energy: 100000 };
export const WORKFLOW_DEFAULT_STATE = 'placed';

export type Card = { minimum: number; maximum: number | null };
export type RelationRow = {
  id: string; source_type: string; target_type: string;
  targets_per_source: Card; sources_per_target: Card; identity_policy?: string; metadata?: unknown;
  valid_writers?: string[];
  writer_options?: Array<{ writer: string; engine_id: string; plugin: string }>;
  binding_kind?: string;
};
export type PlacementKind = 'entity' | 'facility' | 'airspace';
export type CatalogType = TypeRow & { kind?: PlacementKind; engine?: 'kinematic' | 'workflow' };
export type Catalog = { types: CatalogType[]; fields?: Array<Record<string, any> & { id: string }>; relations?: RelationRow[] };

export const PLACE_ROLES = [
  { role: 'charging_pad', type: 'lowalt:ChargingPad', en: 'Charging pad', zh: '充电坪' },
  { role: 'vertiport', type: 'lowalt:Vertiport', en: 'Vertiport', zh: '起降场' },
  { role: 'depot', type: 'lowalt:Depot', en: 'Depot', zh: '仓库' },
  { role: 'kitchen', type: 'lowalt:Restaurant', en: 'Restaurant (kitchen)', zh: '餐厅（厨房）' },
  { role: 'customer', type: 'lowalt:Customer', en: 'Customer', zh: '客户' },
  { role: 'handoff_point', type: 'lowalt:HandoffPoint', en: 'Handoff point', zh: '交接点' },
  { role: 'incident_site', type: 'lowalt:Site', en: 'Incident site', zh: '事件地点' },
  { role: 'inspection_start', type: 'lowalt:Site', en: 'Inspection start', zh: '巡检起点' },
  { role: 'inspection_end', type: 'lowalt:Site', en: 'Inspection end', zh: '巡检终点' },
] as const;
export type PlaceRole = (typeof PLACE_ROLES)[number]['role'];
export type PlaceEditing = { id: string; role?: PlaceRole; type?: string; position?: unknown; label?: string; label_zh?: string; capacity?: number; preparation_s?: number; power_w?: number };

export function placeRoleOf(type: string | undefined): PlaceRole | undefined {
  const matches = PLACE_ROLES.filter(row => row.type === type);
  return matches.length === 1 ? matches[0].role : undefined;
}

export function placeEditingOf(entity: Record<string, any>): PlaceEditing {
  return {
    id: entity.id, type: entity.type,
    role: placeRoleOf(entity.type),
    position: entity.facts?.['lowalt.site_position'], label: entity.label, label_zh: entity.label_zh,
    capacity: entity.facts?.['lowalt.site_capacity'],
    preparation_s: entity.facts?.['lowalt.food_preparation_s'], power_w: entity.facts?.['lowalt.pad_power_w'],
  };
}

export function nextRoleId(role: string, ids: string[]): string {
  const prefix = role.replace(/_/g, '-');
  let index = 1;
  while (ids.includes(`${prefix}-${index}`) || ids.some(id => id.startsWith(`${prefix}-${index}-`))) index++;
  return `${prefix}-${index}`;
}

export const strictVector3 = (value: unknown): value is [number, number, number] =>
  Array.isArray(value) && value.length === 3 && value.every(part => typeof part === 'number' && Number.isFinite(part));

/** The catalogue's region record determines which geometry controls can be authored. */
export function airspaceFieldOf(fields: Array<{ id: string; schema?: any }>) {
  return fields.find(field => {
    const members = field.schema?.members ?? field.schema?.properties;
    return members?.polygon?.type === 'array' && members.polygon.items?.type === 'vector'
      && members.polygon.items.length === 2 && members.polygon.items.items?.type === 'number'
      && members.floor_m?.type === 'number' && members.ceiling_m?.type === 'number'
      && (!Array.isArray(field.schema.required) || field.schema.required.every((key: string) => ['polygon', 'floor_m', 'ceiling_m'].includes(key)));
  });
}

/** Known lowalt structures belong to C11, not the generic placement endpoint. */
export const isLowaltType = (row: TypeRow) => row.id.startsWith('lowalt:') || row.parents.some(parent => parent.startsWith('lowalt:'));

/** The server rejects anything outside this shape before touching the draft. */
export const ID_PATTERN = /^[A-Za-z0-9_.:/-]+$/;

/** Workspace type catalog; `relations` is absent on snapshot sources without relation rows. */
export function useCatalog(studioApi: StudioApi, workspaceId: string, enabled: boolean) {
  const [catalog, setCatalog] = useState<Catalog>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    if (!enabled || catalog) return;
    setError(undefined);
    void studioApi.request<Catalog>(`/v1/studio/types?workspace=${encodeURIComponent(workspaceId)}`)
      .then(setCatalog).catch(problem => setError(String(problem)));
  }, [enabled, catalog, studioApi, workspaceId]);
  return { catalog, catalogError: error };
}

/** The presentation position field for a type; the studio default as fallback. */
export function positionFieldOf(draft: Scenario, typeId: string): string {
  const presentation = Array.isArray(draft.presentation) ? draft.presentation as Array<Record<string, any>> : [];
  return presentation.find(item => item.typeId === typeId)?.positionField ?? STUDIO_POS;
}

/** Never append a studio position to an instance with a different (or absent) position contract. */
export function instancePositionField(draft: Scenario, entity: Record<string, any>): string | undefined {
  const presentation = Array.isArray(draft.presentation) ? draft.presentation as Array<Record<string, any>> : [];
  const declared = presentation.find(item => item.typeId === entity.type)?.positionField;
  if (typeof declared === 'string') return declared;
  return ['lowalt.site_position', 'lowalt.position', STUDIO_POS].find(field => field in (entity.facts ?? {}));
}

export type Dependency = { path: string; kind: string; detail: string };
/** Group a C3 dependency preview by kind, keeping the server's order. */
export function groupDependencies(deps: Dependency[]): Array<[string, Dependency[]]> {
  const order: string[] = [], groups: Record<string, Dependency[]> = {};
  for (const dep of deps) {
    if (!groups[dep.kind]) { groups[dep.kind] = []; order.push(dep.kind); }
    groups[dep.kind].push(dep);
  }
  return order.map(kind => [kind, groups[kind]]);
}

export const num = (raw: string): number | undefined => {
  const text = raw.trim();
  return text !== '' && /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(text) && Number.isFinite(Number(text)) ? Number(text) : undefined;
};

/** Parse one user input according to a contract schema; undefined means "leave the field out". */
export function parseFact(schema: any, raw: string): { value?: unknown; error?: string } {
  const text = raw.trim();
  if (text === '') return {};
  const kind = schema?.type;
  if (kind === 'number' || kind === 'integer') {
    const value = num(text);
    if (value === undefined) return { error: 'number' };
    if (kind === 'integer' && !Number.isInteger(value)) return { error: 'integer' };
    return { value };
  }
  if (kind === 'boolean') return text === 'true' ? { value: true } : text === 'false' ? { value: false } : { error: 'true | false' };
  if (kind === 'vector') {
    const parts = text.split(',').map(part => num(part));
    if (parts.some(part => part === undefined)) return { error: 'numbers' };
    const vector = parts as number[];
    if (typeof schema.length === 'number' && vector.length !== schema.length) return { error: `${schema.length} values` };
    return { value: vector };
  }
  return { value: raw };
}
