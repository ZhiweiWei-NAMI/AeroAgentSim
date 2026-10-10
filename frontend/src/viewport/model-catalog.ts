import * as T from 'three';

/**
 * Display-model catalog of the optional asset pack (v1.1 `models`, delivered as
 * `RunHeader.modelCatalog`). Everything here is presentation: which glTF to draw
 * for an entity, at what size, facing which way. None of it is simulated state.
 *
 * Without a catalog (public clone, v1.0 pack) selection returns undefined and the
 * viewport keeps the procedural or single-asset display it always had.
 */

export type CatalogKind = 'road_vehicle' | 'bicycle' | 'pedestrian' | 'uav' | 'furniture';
const KINDS: readonly CatalogKind[] = ['road_vehicle', 'bicycle', 'pedestrian', 'uav', 'furniture'];
export type Axis = '+x' | '-x' | '+y' | '-y' | '+z' | '-z';
const AXES: readonly Axis[] = ['+x', '-x', '+y', '-y', '+z', '-z'];

export interface CatalogModel {
  url: string;
  kind: CatalogKind;
  label: string;
  /** Target display bounding box in metres: [length (forward), width, height]. */
  sizeM: [number, number, number];
  /** glTF-local axes that point forward / up. */
  forward: Axis;
  up: Axis;
  lodUrl?: string;
  skinned?: boolean;
}
export type ModelCatalog = Readonly<Record<string, CatalogModel>>;

function record(value: unknown, context: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw Error(`Malformed model catalog: ${context} is not an object`);
  return value as Record<string, unknown>;
}

/** Strict parse; undefined/absent means "no catalog". Malformed data throws rather than guessing. */
export function parseModelCatalog(value: unknown): ModelCatalog {
  if (value === undefined || value === null) return {};
  const out: Record<string, CatalogModel> = {};
  for (const [id, raw] of Object.entries(record(value, 'modelCatalog'))) {
    const row = record(raw, id);
    if (typeof row.url !== 'string' || !row.url) throw Error(`Malformed model catalog: ${id} has no url`);
    if (!KINDS.includes(row.kind as CatalogKind)) throw Error(`Malformed model catalog: ${id} has an unknown kind`);
    const size = row.sizeM;
    if (!Array.isArray(size) || size.length !== 3 || !size.every(n => typeof n === 'number' && Number.isFinite(n) && n > 0)) {
      throw Error(`Malformed model catalog: ${id} needs sizeM [length, width, height] in metres`);
    }
    if (!AXES.includes(row.forward as Axis) || !AXES.includes(row.up as Axis) || (row.forward as string)[1] === (row.up as string)[1]) {
      throw Error(`Malformed model catalog: ${id} needs distinct forward and up axes`);
    }
    if (row.lodUrl !== undefined && (typeof row.lodUrl !== 'string' || !row.lodUrl)) throw Error(`Malformed model catalog: ${id} has an invalid lodUrl`);
    out[id] = {
      url: row.url, kind: row.kind as CatalogKind, label: typeof row.label === 'string' ? row.label : id,
      sizeM: [size[0], size[1], size[2]], forward: row.forward as Axis, up: row.up as Axis,
      ...(row.lodUrl ? { lodUrl: row.lodUrl as string } : {}), ...(row.skinned === true ? { skinned: true } : {}),
    };
  }
  return out;
}

/** What the scenario actually says about an entity; nothing here is guessed from type names. */
export interface EntityClassInfo {
  /** Entity key id; only seeds the display-variety hash. */
  id: string;
  /** `lowalt.vehicle_class` fact, else authored `vehicle_class` header metadata. */
  vehicleClass?: string;
  /** `traffic.actor.role` fact. */
  role?: string;
}

/** FNV-1a; stable across runs and platforms so a vehicle keeps its look. */
export function hash32(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  return h >>> 0;
}

// Explicit classes/roles a scenario may state, mapped to catalog model ids.
const ROAD_CLASSES: Readonly<Record<string, string>> = { sedan: 'sedan', car: 'sedan', taxi: 'taxi', police: 'police', bicycle: 'bicycle' };
const ROAD_ROLES: Readonly<Record<string, string>> = { police: 'police', responder: 'police', taxi: 'taxi' };
// Classes that say "some road vehicle" without choosing a body style.
const GENERIC_ROAD = new Set(['road_vehicle', 'road', 'vehicle', 'background', 'reporter', 'incident']);
/** Display variety only: share of otherwise unclassified cars drawn as taxis (per mille). */
export const TAXI_PER_MILLE = 220;

export interface Selection { id: string; reason: 'class' | 'role' | 'variety' }

/**
 * Road vehicle model. Explicit scenario class/role wins; unclassified vehicles get a deterministic
 * sedan/taxi variety from a hash of the entity id. The variety is a display choice, not simulated state.
 */
export function selectRoadVehicle(info: EntityClassInfo, catalog: ModelCatalog): Selection | undefined {
  const has = (id: string | undefined): id is string => id !== undefined && catalog[id]?.kind === (id === 'bicycle' ? 'bicycle' : 'road_vehicle');
  const byClass = info.vehicleClass === undefined ? undefined : ROAD_CLASSES[info.vehicleClass];
  if (has(byClass)) return { id: byClass, reason: 'class' };
  const byRole = info.role === undefined ? undefined : ROAD_ROLES[info.role];
  if (has(byRole)) return { id: byRole, reason: 'role' };
  // A stated class we have no body for (bus, truck, ...) must not be drawn as a sedan.
  if (info.vehicleClass !== undefined && !GENERIC_ROAD.has(info.vehicleClass)) return undefined;
  const pool = has('sedan') ? 'sedan' : Object.keys(catalog).find(id => catalog[id].kind === 'road_vehicle');
  if (!pool) return undefined;
  if (has('taxi') && hash32(info.id) % 1000 < TAXI_PER_MILLE) return { id: 'taxi', reason: 'variety' };
  return { id: pool, reason: 'variety' };
}

// UAV vehicle classes (`lowalt.vehicle_class`) to catalog ids. Anything else is a small multirotor.
const UAV_CLASSES: Readonly<Record<string, string>> = {
  passenger: 'air-taxi', spraying: 'quadcopter-40', inspection: 'quadcopter-40', patrol: 'quadcopter-40',
};
export function selectUav(info: EntityClassInfo, catalog: ModelCatalog): Selection | undefined {
  const named = info.vehicleClass === undefined ? undefined : UAV_CLASSES[info.vehicleClass];
  const isUav = (id: string | undefined): id is string => id !== undefined && catalog[id]?.kind === 'uav';
  if (isUav(named)) return { id: named, reason: 'class' };
  if (isUav('x500')) return { id: 'x500', reason: 'variety' };
  const any = Object.keys(catalog).find(id => catalog[id].kind === 'uav');
  return any ? { id: any, reason: 'variety' } : undefined;
}

export function selectCatalogModel(kind: string, info: EntityClassInfo, catalog: ModelCatalog): Selection | undefined {
  if (kind === 'road_vehicle') return selectRoadVehicle(info, catalog);
  if (kind === 'uav') return selectUav(info, catalog);
  return undefined;
}

const AXIS_VECTORS: Readonly<Record<Axis, readonly [number, number, number]>> = {
  '+x': [1, 0, 0], '-x': [-1, 0, 0], '+y': [0, 1, 0], '-y': [0, -1, 0], '+z': [0, 0, 1], '-z': [0, 0, -1],
};

export interface ModelFit {
  /** Applies to model-space geometry: forward -> +x, left -> +y, up -> +z, uniform scale, ground/centre offset. */
  matrix: T.Matrix4;
  scale: number;
  size: T.Vector3;
}

/**
 * Normalise a model to its catalog size in the body frame the entity quaternion rotates
 * (ENU body convention: x forward, y left, z up; the viewport adds the ENU -> render basis).
 * The scale is uniform so source proportions survive; it is the largest that fits the target box.
 * Ground-aligned kinds sit on z = 0 (wheels on the road); UAVs are centred on their position.
 */
export function fitModel(source: T.Box3, model: Pick<CatalogModel, 'forward' | 'up' | 'sizeM' | 'kind'>): ModelFit {
  if (source.isEmpty()) throw Error('Catalog model has no geometry to fit');
  const f = new T.Vector3(...AXIS_VECTORS[model.forward]), u = new T.Vector3(...AXIS_VECTORS[model.up]);
  const left = new T.Vector3().crossVectors(u, f); // so that (forward, left, up) is right-handed
  const toBody = new T.Matrix4().makeBasis(f, left, u).transpose(); // model -> body (orthonormal inverse)
  const box = new T.Box3();
  const corner = new T.Vector3();
  for (const x of [source.min.x, source.max.x]) for (const y of [source.min.y, source.max.y]) for (const z of [source.min.z, source.max.z]) {
    box.expandByPoint(corner.set(x, y, z).applyMatrix4(toBody));
  }
  const size = box.getSize(new T.Vector3());
  const [length, width, height] = model.sizeM;
  const scale = Math.min(length / size.x, width / size.y, height / size.z);
  if (!Number.isFinite(scale) || scale <= 0) throw Error('Catalog model has degenerate bounds');
  const centre = box.getCenter(new T.Vector3());
  const ground = model.kind !== 'uav';
  const offset = new T.Vector3(-centre.x, -centre.y, ground ? -box.min.z : -centre.z);
  const matrix = new T.Matrix4().makeScale(scale, scale, scale)
    .multiply(new T.Matrix4().makeTranslation(offset.x, offset.y, offset.z)).multiply(toBody);
  return { matrix, scale, size: size.multiplyScalar(scale) };
}

/** Heading quaternion (display frame) from an ENU horizontal velocity; undefined when nearly stationary. */
export function yawFromVelocity(velocity: readonly number[], minSpeed = 0.4): number | undefined {
  if (velocity.length < 2 || !Number.isFinite(velocity[0]) || !Number.isFinite(velocity[1])) return undefined;
  return Math.hypot(velocity[0], velocity[1]) < minSpeed ? undefined : Math.atan2(velocity[1], velocity[0]);
}
