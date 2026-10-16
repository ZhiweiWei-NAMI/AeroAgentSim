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
  rescueRole?: string;
  rescueTask?: string;
  rescueStatus?: string;
}

export interface SemanticFlood {
  lower: [number, number, number];
  upper: [number, number, number];
}
export interface SemanticFloodedRoad {
  start: [number, number, number];
  end: [number, number, number];
}
export interface SemanticIncident {
  key: EntityKey;
  position: [number, number, number];
  state: string;
}

export interface SemanticMarker {
  id: string;
  kind: 'inspect' | 'relay' | 'command';
  position: [number, number, number];
}

export type RescueLinkKind = 'report' | 'delivery_ack' | 'relay_coverage';
export type RescueLinkState = 'sending' | 'delivered' | 'failed' | 'holding';

export interface SemanticLink {
  id: string;
  from: string;
  to: string;
  kind: RescueLinkKind;
  state: RescueLinkState;
}

const MARKER_KINDS: readonly SemanticMarker['kind'][] = ['inspect', 'relay', 'command'];
const LINK_KINDS: readonly RescueLinkKind[] = ['report', 'delivery_ack', 'relay_coverage'];
const LINK_STATES: readonly RescueLinkState[] = ['sending', 'delivered', 'failed', 'holding'];

/** Committed values are immutable. Keep derived overlays until their inputs change,
 * including removals/backward seeks; display clock revisions alone do not matter. */
class SemanticProjection<Result> {
  private inputs: unknown[] = [];
  private result?: Result;
  private header?: RunHeader;
  constructor(private fields: readonly string[], private derive: (store: SemanticStore, header?: RunHeader) => Result, private includeType?: string) {}
  private inputIndex = 0;
  private inputsChanged = false;
  get(store: SemanticStore, header?: RunHeader): Result {
    this.inputIndex = 0;
    this.inputsChanged = this.header !== header;
    store.entities.forEach(this.collect, this);
    this.inputsChanged = this.inputs.length !== this.inputIndex || this.inputsChanged;
    this.inputs.length = this.inputIndex;
    this.header = header;
    if (this.inputsChanged || this.result === undefined) {
      this.result = undefined; // A malformed new cut must continue to fail.
      this.result = this.derive(store, header);
    }
    return this.result;
  }
  private collect(entity: EntityState) {
    let relevant = entity.typeId === this.includeType;
    for (let field = 0; !relevant && field < this.fields.length; field++) relevant = entity.fields.get(this.fields[field])?.value !== undefined;
    if (!relevant) return;
    this.inputsChanged = this.remember(this.inputIndex++, entity.key.id) || this.inputsChanged;
    this.inputsChanged = this.remember(this.inputIndex++, entity.key.generation) || this.inputsChanged;
    this.inputsChanged = this.remember(this.inputIndex++, entity.typeId) || this.inputsChanged;
    for (let field = 0; field < this.fields.length; field++) {
      this.inputsChanged = this.remember(this.inputIndex++, entity.fields.get(this.fields[field])?.value) || this.inputsChanged;
    }
  }
  private remember(index: number, value: unknown): boolean {
    const changed = !sameCommittedValue(this.inputs[index], value);
    this.inputs[index] = value;
    return changed;
  }
}

/** A new fact version can carry the same JSON value. Compare only that case;
 * unchanged references and primitives take the allocation-free fast path. */
function sameCommittedValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!sameCommittedValue(a[i], b[i])) return false;
    return true;
  }
  if (!isRecord(a) || !isRecord(b)) return false;
  let sizeA = 0, sizeB = 0;
  for (const key in a) { sizeA++; if (!Object.prototype.hasOwnProperty.call(b, key) || !sameCommittedValue(a[key], b[key])) return false; }
  for (const _key in b) sizeB++;
  return sizeA === sizeB;
}

type SemanticStore = { entities: Map<string, EntityState> };
function projection<Result>(fields: readonly string[], derive: (store: SemanticStore, header?: RunHeader) => Result, includeType?: string) {
  const stores = new WeakMap<SemanticStore, SemanticProjection<Result>>();
  return (store: SemanticStore, header?: RunHeader): Result => {
    let cache = stores.get(store);
    if (!cache) { cache = new SemanticProjection(fields, derive, includeType); stores.set(store, cache); }
    return cache.get(store, header);
  };
}

/** `rescue.markers` records from C1, validated, never repaired. */
export const semanticMarkers = projection(['rescue.markers'], deriveSemanticMarkers);

function deriveSemanticMarkers(store: { entities: Map<string, EntityState> }): SemanticMarker[] {
  const markers: SemanticMarker[] = [];
  for (const entity of store.entities.values()) {
    const raw = entity.fields.get('rescue.markers')?.value;
    if (raw === undefined) continue;
    if (!Array.isArray(raw)) throw Error(`Malformed rescue.markers for ${entity.key.id}`);
    for (const item of raw) {
      if (!isRecord(item) || typeof item.id !== 'string' || !item.id) throw Error(`Malformed rescue marker for ${entity.key.id}`);
      if (!MARKER_KINDS.includes(item.kind as SemanticMarker['kind'])) throw Error(`Unknown rescue marker kind on ${item.id}: ${String(item.kind)}`);
      markers.push({ id: item.id, kind: item.kind as SemanticMarker['kind'], position: vector3(item.position, `rescue marker ${item.id} position`) });
    }
  }
  return markers;
}

/** `rescue.links` records from C1, validated, never repaired. */
export const semanticLinks = projection(['rescue.links'], deriveSemanticLinks);

function deriveSemanticLinks(store: { entities: Map<string, EntityState> }): SemanticLink[] {
  const links: SemanticLink[] = [];
  for (const entity of store.entities.values()) {
    const raw = entity.fields.get('rescue.links')?.value;
    if (raw === undefined) continue;
    if (!Array.isArray(raw)) throw Error(`Malformed rescue.links for ${entity.key.id}`);
    for (const item of raw) {
      if (!isRecord(item) || typeof item.id !== 'string' || !item.id) throw Error(`Malformed rescue link for ${entity.key.id}`);
      if (typeof item.from !== 'string' || !item.from || typeof item.to !== 'string' || !item.to) throw Error(`Malformed rescue link endpoints on ${item.id}`);
      if (!LINK_KINDS.includes(item.kind as RescueLinkKind)) throw Error(`Unknown rescue link kind on ${item.id}: ${String(item.kind)}`);
      if (!LINK_STATES.includes(item.state as RescueLinkState)) throw Error(`Unknown rescue link state on ${item.id}: ${String(item.state)}`);
      links.push({ id: item.id, from: item.from, to: item.to, kind: item.kind as RescueLinkKind, state: item.state as RescueLinkState });
    }
  }
  return links;
}

/** Legend colour per link kind/state; relay coverage uses the relay role colour. */
export function rescueLinkColor(link: Pick<SemanticLink, 'kind' | 'state'>): string {
  if (link.state === 'failed') return '#e5484d';
  if (link.kind === 'delivery_ack') return '#3fb67a';
  if (link.kind === 'relay_coverage') return '#cc79a7';
  return '#f0a020';
}

/**
 * Person-facing UAV rescue label: entity id, role and status words only —
 * never action ids such as `R7/a4`. `rescue.task` is committed data and stays
 * out of the label. Equal role/status words collapse to one word.
 */
export function rescueUavLabel(vehicle: SemanticVehicle, locale = 'en-US'): string {
  const zh = locale.startsWith('zh');
  const role = vehicle.rescueRole ? rescueLabel(vehicle.rescueRole, locale) : undefined;
  const status = vehicle.rescueStatus ? rescueLabel(vehicle.rescueStatus, locale) : undefined;
  const parts: string[] = [vehicle.id];
  if (role && role !== status) parts.push(role);
  if (status) parts.push(status);
  if (vehicle.rescueRole === 'committed' && !role) parts.push(rescueLabel('committed', locale));
  return parts.filter(Boolean).join(' · ');
}

/** Scenario-authored flood geometry and the incident's current committed state. */
export const semanticFlooding = projection(['rescue.flood_bounds', 'rescue.flooded_roads', 'rescue.incident_state', 'lowalt.site_position'], deriveSemanticFlooding);

function deriveSemanticFlooding(store: { entities: Map<string, EntityState> }) {
  const areas: SemanticFlood[] = [], roads: SemanticFloodedRoad[] = [], incidents: SemanticIncident[] = [];
  for (const entity of store.entities.values()) {
    const bounds = entity.fields.get('rescue.flood_bounds')?.value;
    if (bounds !== undefined) {
      if (!Array.isArray(bounds) || bounds.length !== 6 || !bounds.every(value => typeof value === 'number' && Number.isFinite(value))) {
        throw Error(`Malformed flood bounds for ${entity.key.id}`);
      }
      const lower = vector3(bounds.slice(0, 3), 'flood lower bounds'), upper = vector3(bounds.slice(3), 'flood upper bounds');
      if (upper.some((value, index) => value <= lower[index])) throw Error(`Flood ${entity.key.id} has non-positive bounds extent`);
      areas.push({ lower, upper });
    }
    const segments = entity.fields.get('rescue.flooded_roads')?.value;
    if (segments !== undefined) {
      if (!Array.isArray(segments)) throw Error(`Malformed flooded roads for ${entity.key.id}`);
      for (const segment of segments) {
        if (!isRecord(segment)) throw Error(`Malformed flooded road for ${entity.key.id}`);
        roads.push({ start: vector3(segment.start, 'flooded road start'), end: vector3(segment.end, 'flooded road end') });
      }
    }
    const state = entity.fields.get('rescue.incident_state')?.value;
    if (state !== undefined) {
      if (typeof state !== 'string') throw Error(`Malformed incident state for ${entity.key.id}`);
      incidents.push({ key: entity.key, position: vector3(entity.fields.get('lowalt.site_position')?.value, 'rescue site position'), state });
    }
  }
  return { areas, roads, incidents };
}

/** Translation only; unknown authored role/status/task strings remain intact. */
export function rescueLabel(value: string, locale = 'en-US'): string {
  const labels: Record<string, [string, string]> = {
    inspect: ['Inspect', '复查'], report: ['Report', '报告'], relay: ['Relay', '中继'], deliver: ['Deliver', '投送'],
    command: ['Command', '指挥'], committed: ['On existing mission', '执行原任务'], pending: ['Pending', '待处理'], suspected: ['Suspected', '疑似求援'],
    observing: ['Observing', '复查中'], confirmed: ['Confirmed', '已确认'], negative: ['Ruled out', '已排除'],
    uncertain: ['Uncertain', '尚未确定'], delivering: ['Delivering', '投送中'], delivered: ['Delivered', '已交付'],
    blocked: ['Blocked', '受阻'], closed: ['Closed', '已关闭'], idle: ['Idle', '待命'], waiting: ['Waiting', '等待'],
    assigned: ['Assigned', '已指派'], enroute: ['En route', '飞行中'], completed: ['Completed', '已完成'],
    continuing: ['Continuing', '执行中'], holding: ['Holding', '驻留'], waiting_report: ['Awaiting report', '待报告送达'],
  };
  return labels[value]?.[locale.startsWith('zh') ? 1 : 0] ?? value;
}

export function rescueIncidentColor(state: string): string {
  if (state === 'delivered' || state === 'closed') return '#35d69c';
  if (state === 'negative') return '#a8b5c5';
  if (state === 'blocked') return '#ff645f';
  return '#ffb74b';
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

/**
 * Registered field metadata lookup. Returns the declared schema metadata for
 * a field id so zone kinds are validated against the registry, not a constant.
 */
function zoneKindEnum(header: RunHeader): string[] | undefined {
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
const cachedActiveZones = projection(['lowalt.zone_kind', 'lowalt.zone_lower', 'lowalt.zone_upper', 'lowalt.zone_active', 'lowalt.zone_dynamic', 'lowalt.zone_start_ns', 'lowalt.zone_end_ns'], (store, header) => deriveActiveZones(header!, store), 'lowalt:AirspaceZone');
export function activeZones(header: RunHeader, store: SemanticStore) { return cachedActiveZones(store, header); }

function deriveActiveZones(header: RunHeader, store: { entities: Map<string, EntityState> }): SemanticZone[] {
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
const cachedSemanticSites = projection(['lowalt.site_position'], (store, header) => deriveSemanticSites(header!, store));
export function semanticSites(header: RunHeader, store: SemanticStore) { return cachedSemanticSites(store, header); }

function deriveSemanticSites(header: RunHeader, store: { entities: Map<string, EntityState> }): SemanticSite[] {
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
    rescueRole: typeof entity.fields.get('rescue.role')?.value === 'string' ? entity.fields.get('rescue.role')!.value as string : undefined,
    rescueTask: typeof entity.fields.get('rescue.task')?.value === 'string' ? entity.fields.get('rescue.task')!.value as string : undefined,
    rescueStatus: typeof entity.fields.get('rescue.status')?.value === 'string' ? entity.fields.get('rescue.status')!.value as string : undefined,
  };
}

export interface BusinessStyle { glyph: 'package' | 'cross' | 'eye' | 'person' | 'road' | 'shield' | 'spray' | 'unknown'; color: string; label: string }

/**
 * Glyph + colorblind-safe palette per actual business kind. Anything not
 * declared in the data is `unknown`, never a fabricated classification.
 */
export function businessStyle(vehicle: SemanticVehicle, locale = 'en-US'): BusinessStyle {
  if (vehicle.rescueRole) {
    const role = vehicle.rescueRole;
    return { glyph: role === 'inspect' ? 'eye' : role === 'deliver' || role === 'committed' ? 'package' : 'unknown',
      color: role === 'inspect' ? '#56b4e9' : role === 'relay' ? '#cc79a7' : role === 'deliver' ? '#e69f00' : '#8fa3b8',
      label: rescueLabel(role, locale) };
  }
  const kind = vehicle.taskKind ?? vehicle.vehicleClass;
  // The parcel airframe also flies meals; without a current task its class says "delivery", not which kind.
  if (!vehicle.taskKind && kind === 'parcel') return { glyph: 'package', color: '#e69f00', label: translate(locale, 'Delivery') };
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
  if (vehicle.rescueRole !== undefined) {
    // Keep the authored identity even when roles and assignments change.
    return rescueUavLabel(vehicle, locale);
  }
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
  if (entity.fields.has('rescue.role')) return true;
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
