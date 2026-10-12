import type { FeedCommit, EntityKey, RunHeader } from '../contracts/viewer-feed';
import type { EntityState } from './feed-store';
import { localized, translate } from '../i18n/translate';
import { displayLabel, localizedLabel } from '../pages/inspection-format';

/**
 * Newcomer semantic overlay extraction.
 *
 * Everything here reads actual committed fields (lowalt.*) and registered
 * schemas/metadata. No state is forged: when the data does not say what a
 * business semantic is, the result is `undefined` and the caller renders
 * nothing rather than a guess. Malformed zone data throws — it is never
 * silently repaired.
 */

export type ZoneKind = 'no_fly' | 'restricted' | 'altitude_band' | 'corridor';

export interface SemanticZone {
  id: string;
  key: EntityKey;
  kind: ZoneKind;
  lower: [number, number, number];
  upper: [number, number, number];
  active: boolean;
  dynamic?: boolean;
  startNs?: string;
  endNs?: string;
}

export interface SemanticSite {
  id: string;
  key: EntityKey;
  typeId: string;
  position: [number, number, number];
  /** Vertiport/Depot only, per the registered type hierarchy. */
  isVertiport: boolean;
  isPad: boolean;
  isCustomer: boolean;
}

export interface SemanticVehicle {
  id: string;
  key: EntityKey;
  vehicleClass?: string;
  /** Task business kind via the current-task reference (task_vehicle), never invented. */
  taskKind?: string;
  soc?: number;
  charging?: boolean;
  weatherHeld?: boolean;
}

const ZONE_KINDS: readonly ZoneKind[] = ['no_fly', 'restricted', 'altitude_band', 'corridor'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** A committed scalar fact value: exactly one number/boolean/string, no wrapping. */
function scalar(value: unknown): number | string | boolean | undefined {
  if (typeof value === 'number' || typeof value === 'string' || typeof value === 'boolean') return value;
  return undefined;
}

function vector3(value: unknown, context: string): [number, number, number] {
  if (!Array.isArray(value) || value.length !== 3 || !value.every(item => typeof item === 'number' && Number.isFinite(item))) {
    throw Error(`Malformed ${context}: expected a finite 3-vector`);
  }
  return [value[0], value[1], value[2]];
}

function kindOf(entity: EntityState): string {
  const key = entity.key;
  return `${key.id}:${key.generation}`;
}

export const entityIdOf = (entity: EntityState): string => kindOf(entity);

/**
 * Registered field metadata lookup. Returns the declared schema metadata for
 * a field id so zone kinds are validated against the registry, not a constant.
 */
export function zoneKindEnum(header: RunHeader): string[] | undefined {
  const field = header.fields.find(field => field.fieldId === 'lowalt.zone_kind');
  if (!field) return undefined;
  const schema = field.schema;
  if (isRecord(schema) && Array.isArray(schema['enum'])) return schema['enum'].filter(item => typeof item === 'string');
  return undefined;
}

function declaredZoneKinds(header: RunHeader): readonly string[] {
  return zoneKindEnum(header) ?? ZONE_KINDS;
}

/**
 * Active airspace zones from actual committed facts. Throws on zone facts
 * that violate the registered schema (missing kind/bounds or unknown kind):
 * a broken feed is surfaced, not hidden behind a default.
 */
export function activeZones(header: RunHeader, store: { entities: Map<string, EntityState> }): SemanticZone[] {
  const allowed = declaredZoneKinds(header);
  const zones: SemanticZone[] = [];
  for (const entity of store.entities.values()) {
    if (entity.typeId !== 'lowalt:AirspaceZone') continue;
    const kind = scalar(entity.fields.get('lowalt.zone_kind')?.value);
    if (typeof kind !== 'string') throw Error(`Zone ${entity.key.id} has no committed lowalt.zone_kind string`);
    if (!allowed.includes(kind)) throw Error(`Zone ${entity.key.id} declares unknown zone kind: ${kind}`);
    const lowerRaw = entity.fields.get('lowalt.zone_lower')?.value;
    const upperRaw = entity.fields.get('lowalt.zone_upper')?.value;
    if (lowerRaw === undefined || upperRaw === undefined) throw Error(`Zone ${entity.key.id} is missing committed zone bounds`);
    const active = entity.fields.get('lowalt.zone_active')?.value;
    if (typeof active !== 'boolean') throw Error(`Zone ${entity.key.id} has no committed lowalt.zone_active boolean`);
    if (!active) continue;
    zones.push({
      id: entity.key.id,
      key: entity.key,
      kind: kind as ZoneKind,
      lower: vector3(lowerRaw, `zone ${entity.key.id} lower bounds`),
      upper: vector3(upperRaw, `zone ${entity.key.id} upper bounds`),
      active,
      dynamic: scalar(entity.fields.get('lowalt.zone_dynamic')?.value) === true ? true : undefined,
      startNs: typeof entity.fields.get('lowalt.zone_start_ns')?.value === 'string' ? entity.fields.get('lowalt.zone_start_ns')!.value as string : undefined,
      endNs: typeof entity.fields.get('lowalt.zone_end_ns')?.value === 'string' ? entity.fields.get('lowalt.zone_end_ns')!.value as string : undefined,
    });
  }
  return zones;
}

/** Pads and vertiports/customers from committed `lowalt.site_position` facts. */
export function semanticSites(header: RunHeader, store: { entities: Map<string, EntityState> }): SemanticSite[] {
  const sites: SemanticSite[] = [];
  const siteTypes = new Set(['lowalt:Site', ...header.types.filter(type => type.ancestors.includes('lowalt:Site') || type.ancestors.some(a => header.types.some(parent => parent.typeId === a && parent.ancestors.includes('lowalt:Site')))).map(type => type.typeId)]);
  for (const entity of store.entities.values()) {
    if (!siteTypes.has(entity.typeId)) continue;
    const raw = entity.fields.get('lowalt.site_position')?.value;
    if (raw === undefined) continue;
    const position = vector3(raw, `site ${entity.key.id} position`);
    sites.push({
      id: entity.key.id,
      key: entity.key,
      typeId: entity.typeId,
      position,
      isVertiport: entity.typeId === 'lowalt:Vertiport' || entity.typeId === 'lowalt:Depot',
      isPad: entity.typeId === 'lowalt:ChargingPad',
      isCustomer: entity.typeId === 'lowalt:Customer',
    });
  }
  return sites;
}

/** Human-readable display name. Authored names win; otherwise a sensible identity. */
export function displayName(entity: EntityState, header: RunHeader, locale = 'en-US'): string {
  for (const fieldId of ['traffic.actor.label','oo:name', 'lowalt.label', 'name', 'label', 'display_name']) {
    const chinese = locale === 'zh-CN' ? scalar(entity.fields.get(`${fieldId}_zh`)?.value) : undefined;
    const value = typeof chinese === 'string' && chinese.trim() ? chinese : scalar(entity.fields.get(fieldId)?.value);
    if (typeof value === 'string' && value.trim()) return localizedLabel(value.trim(), locale);
  }
  const authored = entity.authoredLabel ?? header.entities?.find(item => item.id === entity.key.id);
  const authoredName = localized(authored, 'label', locale);
  if (typeof authoredName === 'string' && authoredName.trim()) return localizedLabel(authoredName.trim(), locale);
  const type = header.types.find(type => type.typeId === entity.typeId);
  const fallbackName = humanizeId(entity.key.id, displayLabel(type?.displayName, entity.typeId, locale), locale);
  return entity.typeId === 'lowalt:UAV' ? fallbackName.replace(/^Uav\b/i, translate(locale, 'Drone')) : fallbackName;
}

/** `vertiport-1` → `Vertiport 1`; `vertiport-north` → `Vertiport North`. */
export function humanizeId(id: string, typeName: string, locale = 'en-US'): string {
  const words = id.split(/[^a-zA-Z0-9]+/).filter(Boolean);
  const readable = words.length > 1 && words.every(word => /^[a-zA-Z0-9]+$/.test(word));
  const wordLabel = (word: string) => {
    switch (word.toLowerCase()) {
      case 'uav': return translate(locale, 'UAV');
      case 'drone': return translate(locale, 'Drone');
      case 'vertiport': return translate(locale, 'Vertiport');
      case 'charging': return translate(locale, 'Charging');
      case 'pad': return translate(locale, 'Pad');
      case 'depot': return translate(locale, 'Depot');
      case 'customer': return translate(locale, 'Customer');
      case 'vehicle': return translate(locale, 'Vehicle');
      case 'car': return translate(locale, 'Car');
      case 'truck': return translate(locale, 'Truck');
      case 'bus': return translate(locale, 'Bus');
      case 'north': return translate(locale, 'North');
      case 'south': return translate(locale, 'South');
      case 'east': return translate(locale, 'East');
      case 'west': return translate(locale, 'West');
      case 'central': return translate(locale, 'Central');
      default: return /^\d+$/.test(word) ? word : word[0].toUpperCase() + word.slice(1);
    }
  };
  if (readable) return words.map(wordLabel).join(' ');
  const short = id.length > 14 ? `${id.slice(0, 12)}…` : id;
  return `${typeName.split(':').pop() ?? typeName} ${short}`;
}

/**
 * Current task per vehicle from actual fields: the task's committed
 * `lowalt.task_vehicle` reference, or a `current_task`/`assigned_task` graph
 * edge when the scenario authors one. Only real references are used.
 */
export function isLowaltTask(entity: EntityState, header: Pick<RunHeader, 'types'>): boolean {
  return entity.typeId === 'lowalt:Task' || header.types.some(type => type.typeId === entity.typeId && type.ancestors.includes('lowalt:Task'));
}

export function taskByVehicle(store: { header: RunHeader; entities: Map<string, EntityState>; edges: Map<string, { relationId: string; source: EntityKey; target: EntityKey; op: string }> }): Map<string, EntityState> {
  const map = new Map<string, EntityState>();
  const taskTypes = new Set(['lowalt:Task', ...store.header.types.filter(type => type.ancestors.includes('lowalt:Task')).map(type => type.typeId)]);
  for (const task of store.entities.values()) {
    if (!taskTypes.has(task.typeId)) continue;
    if(['completed','failed','canceled','unreleased','queued','awaiting_preparation','preparing'].includes(String(task.fields.get('lowalt.task_state')?.value)))continue;
    const vehicle = scalar(task.fields.get('lowalt.task_vehicle')?.value);
    if (typeof vehicle !== 'string' || !vehicle) continue;
    const entity = store.entities.get(`${vehicle}:0`);
    const assign=(id:string)=>{const previous=map.get(id);if(!previous||previous.fields.get('lowalt.task_state')?.value==='paused'||task.fields.get('lowalt.task_state')?.value!=='paused')map.set(id,task);};
    if (entity) assign(`${vehicle}:0`);
    else for (const [id, candidate] of store.entities) if (candidate.key.id === vehicle) assign(id);
  }
  for (const edge of store.edges.values()) {
    if (edge.op !== 'assert') continue;
    const relation = edge.relationId.split('/').pop() ?? edge.relationId;
    if (relation !== 'current_task' && relation !== 'assigned_task') continue;
    const vehicle = store.entities.get(`${edge.source.id}:${edge.source.generation}`);
    const task = store.entities.get(`${edge.target.id}:${edge.target.generation}`);
    if (vehicle && task && taskTypes.has(task.typeId)) map.set(`${edge.source.id}:${edge.source.generation}`, task);
  }
  return map;
}

/** Vehicle business semantics from actual fields; task kind only via real reference. */
export function semanticVehicle(entity: EntityState, task?: EntityState): SemanticVehicle {
  const soc = scalar(entity.fields.get('lowalt.soc')?.value);
  return {
    id: entity.key.id,
    key: entity.key,
    vehicleClass: typeof scalar(entity.fields.get('lowalt.vehicle_class')?.value) === 'string' ? entity.fields.get('lowalt.vehicle_class')!.value as string : undefined,
    taskKind: task && typeof scalar(task.fields.get('lowalt.task_kind')?.value) === 'string' ? task.fields.get('lowalt.task_kind')!.value as string : undefined,
    soc: typeof soc === 'number' ? soc : undefined,
    charging: scalar(entity.fields.get('lowalt.charging')?.value) === true ? true : undefined,
    weatherHeld: scalar(entity.fields.get('lowalt.weather_held')?.value) === true ? true : undefined,
  };
}

export interface BusinessStyle { glyph: 'package' | 'cross' | 'eye' | 'person' | 'road' | 'shield' | 'spray' | 'unknown'; color: string; label: string }

/**
 * Glyph + colorblind-safe palette per actual business kind. Anything not
 * declared in the data is `unknown`, never a fabricated classification.
 */
export function businessStyle(vehicle: SemanticVehicle, locale = 'en-US'): BusinessStyle {
  const kind = vehicle.taskKind ?? vehicle.vehicleClass;
  switch (kind) {
    case 'parcel': return { glyph: 'package', color: '#e69f00', label: translate(locale, 'Parcel') };
    case 'food': return { glyph: 'package', color: '#d55e00', label: translate(locale, 'Food') };
    case 'medical': return { glyph: 'cross', color: '#cc79a7', label: translate(locale, 'Medical') };
    case 'inspection':
    case 'survey': return { glyph: 'eye', color: '#56b4e9', label: translate(locale, 'Inspection') };
    case 'passenger': return { glyph: 'person', color: '#009e73', label: translate(locale, 'Passenger') };
    case 'patrol': return {glyph:'shield',color:'#0072b2',label:translate(locale, 'Security')};
    case 'spraying': return {glyph:'spray',color:'#009e73',label:translate(locale, 'Agriculture')};
    case 'incident_response': return {glyph:'cross',color:'#d55e00',label:translate(locale, 'Emergency')};
    case 'road': return { glyph: 'road', color: '#f0e442', label: translate(locale, 'Ground ops') };
    default: return { glyph: 'unknown', color: '#8fa3b8', label: '' };
  }
}

/** One-line friendly label from actual fields only. */
export function semanticLabel(entity: EntityState, task?: EntityState, header?: RunHeader, locale = 'en-US'): string {
  const name = displayName(entity, header ?? { types: [], presentation: [], fields: [] } as unknown as RunHeader, locale);
  const vehicle = semanticVehicle(entity, task);
  const parts: string[] = [name];
  const style = businessStyle(vehicle, locale);
  if (style.label) parts.push(style.label);
  if (vehicle.charging) parts.push(translate(locale, 'Charging'));
  else if (vehicle.weatherHeld) parts.push(translate(locale, 'Weather hold'));
  if (vehicle.soc !== undefined) parts.push(translate(locale, 'SoC {percent}%', { percent: Math.round(vehicle.soc * 100) }));
  return parts.join(' · ');
}

/** Recently event-involved entity ids within the look-back window. */
export function recentlyInvolved(messages: FeedCommit['messages'], lookback: number, nowNs: string): Set<string> {
  const ids = new Set<string>();
  const now = BigInt(nowNs);
  for (const message of messages) {
    if (message.kind !== 'event') continue;
    let at: bigint;
    try { at = BigInt(message.at.ns); } catch { throw Error(`Malformed message timestamp: ${message.at.ns}`); }
    if (now < at || now - at > BigInt(lookback)) continue;
    for (const subject of message.subjects ?? []) ids.add(`${subject.id}:${subject.generation}`);
  }
  return ids;
}

/** Who should show a label in the SIMPLE default (not show-all) mode. */
export function labelInterest(entity: EntityState, selected?: EntityKey, involved?: Set<string>, hasActiveTask?: boolean, alerts?:Set<string>): boolean {
  if (selected && selected.id === entity.key.id && String(selected.generation) === String(entity.key.generation)) return true;
  // Authored background traffic emits routine motion/occupancy updates every
  // tick and each routine actor carries a looped task that never finishes, so
  // a dense city run would label every road vehicle. Keep them unlabelled in
  // the default view unless selected, hovering (handled by the caller) or
  // part of an actual alert; only non-background actors earn task/event labels.
  if(entity.fields.get('traffic.actor.role')?.value==='background')return alerts?.has(`${entity.key.id}:${entity.key.generation}`)===true;
  if(hasActiveTask)return true;
  if (involved?.has(`${entity.key.id}:${entity.key.generation}`)) return true;
  return !!hasActiveTask;
}
