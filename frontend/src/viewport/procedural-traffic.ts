import * as T from 'three';
import { decorateBuildings } from './city-materials';
import { surfaceMaterial } from './city-surfaces';
import type { PackedBatch, PackedObject, PackedRange } from './mesh-pack';

/**
 * Procedural (mesh-free) display for the lite traffic-city bundle
 * (scenarios/demos/traffic-accident/inputs/lite-city/scene.json, format
 * traffic-city-lite/v1, produced by tools/demos/generate_lite_city.py).
 *
 * The public clone cannot ship the original building GLBs, so the lite bundle
 * carries placement data only: real OSM footprints + recorded heights, and the
 * recorded incident/route lane polylines. Everything here renders that data
 * with existing procedural viewport materials — no geometry is invented:
 * - buildings: footprint polygon extruded to its recorded height_m, surfaced by
 *   the shared batched-facade material (city-materials), one draw call;
 * - roads: lane polylines swept by their declared width_m, surfaced by the
 *   shared procedural asphalt (city-surfaces);
 * - vehicles: the same primitive stand-ins the recorded standalone camera
 *   (packs/traffic_accident/camera.html) uses, matched here for both the viewer and the capture path.
 *
 * Render mapping matches worldPosition 'enu': x = east, y = up, z = -north.
 * Procedural vehicle assets use the reserved "procedural:" URL scheme
 * ("procedural:car" / "procedural:uav"); nothing is fetched for them.
 */

export const LITE_CITY_FORMAT = 'traffic-city-lite/v1';
export const PROCEDURAL_ASSET_SCHEME = 'procedural:';

export interface LiteCityBuilding {
  readonly id: string;
  readonly footprint: ReadonlyArray<readonly [number, number]>;
  readonly height_m: number;
}
export interface LiteCityRoad {
  readonly id: string;
  readonly points_enu_m: ReadonlyArray<readonly [number, number, number]>;
  readonly width_m: number;
}
export interface LiteCityScene {
  readonly format: typeof LITE_CITY_FORMAT;
  readonly frame: string;
  readonly buildings: ReadonlyArray<LiteCityBuilding>;
  readonly roads?: ReadonlyArray<LiteCityRoad>;
}

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

function validBuilding(value: unknown): value is LiteCityBuilding {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.id === 'string' && !!row.id && Array.isArray(row.footprint) && row.footprint.length >= 3
    && row.footprint.every(point => Array.isArray(point) && point.length === 2 && point.every(finite))
    && finite(row.height_m) && row.height_m > 0;
}
function validRoad(value: unknown): value is LiteCityRoad {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return typeof row.id === 'string' && !!row.id && Array.isArray(row.points_enu_m) && row.points_enu_m.length >= 2
    && row.points_enu_m.every(point => Array.isArray(point) && point.length === 3 && point.every(finite))
    && finite(row.width_m) && row.width_m > 0;
}

/** Contract check for the lite bundle; everything else stays on the GLB path. */
export function isLiteCityScene(value: unknown): value is LiteCityScene {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const scene = value as Record<string, unknown>;
  if (scene.format !== LITE_CITY_FORMAT || scene.frame !== 'enu' || !Array.isArray(scene.buildings)) return false;
  if (!scene.buildings.every(validBuilding)) return false;
  return scene.roads === undefined || (Array.isArray(scene.roads) && scene.roads.every(validRoad));
}

const PROCEDURAL_PACK = {
  vertices: 0, indices: 0,
  material: { color: [0.5, 0.5, 0.5], base_color_texture: null, normal_texture: null, orm_texture: null, opacity_texture: null, transparent: false, clamp: false },
} as unknown as PackedBatch;

/** Extruded recorded footprints merged into one batch; ranges keep building identity for the facade material. */
function buildingMesh(buildings: ReadonlyArray<LiteCityBuilding>): T.Mesh | undefined {
  const position: number[] = [], normal: number[] = [];
  const ranges: PackedRange[] = [], objects: PackedObject[] = [];
  let vertices = 0;
  for (const building of buildings) {
    const shape = new T.Shape(building.footprint.map(([east, north]) => new T.Vector2(east, north)));
    const part = new T.ExtrudeGeometry(shape, { depth: building.height_m, bevelEnabled: false });
    part.rotateX(-Math.PI / 2); // footprint y=north -> z=-north; extrusion depth -> up
    const count = part.getAttribute('position').count;
    if (!count) { part.dispose(); continue; }
    const pos = part.getAttribute('position'), nrm = part.getAttribute('normal');
    for (let i = 0; i < count; i++) {
      position.push(pos.getX(i), pos.getY(i), pos.getZ(i));
      normal.push(nrm.getX(i), nrm.getY(i), nrm.getZ(i));
    }
    vertices += count;
    // Ranges partition triangles (see mesh-pack): end is the cumulative triangle count.
    ranges.push({ end: vertices / 3, target: { kind: 'building', id: building.id } });
    objects.push({ id: building.id, tags: {} });
    part.dispose();
  }
  if (!vertices) return undefined;
  const geometry = new T.BufferGeometry();
  geometry.setAttribute('position', new T.Float32BufferAttribute(position, 3));
  geometry.setAttribute('normal', new T.Float32BufferAttribute(normal, 3));
  const batch = { ...PROCEDURAL_PACK, vertices, indices: 0, layer: 'buildings' as const, ranges } as PackedBatch;
  const material = decorateBuildings(geometry, batch, objects, false);
  const mesh = new T.Mesh(geometry, material);
  mesh.name = 'lite-buildings';
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

/** Recorded lane polylines swept by their declared width_m; shared procedural asphalt finish. */
function roadMesh(roads: ReadonlyArray<LiteCityRoad>): T.Mesh | undefined {
  const position: number[] = [], uv: number[] = [], normal: number[] = [], indices: number[] = [];
  let base = 0;
  for (const road of roads) {
    const half = road.width_m / 2, points = road.points_enu_m;
    let distance = 0;
    for (let i = 0; i < points.length; i++) {
      const a = points[Math.max(0, i - 1)], b = points[Math.min(points.length - 1, i + 1)];
      let de = b[0] - a[0], dn = b[1] - a[1];
      const length = Math.hypot(de, dn);
      if (length < 1e-9) { de = 1; dn = 0; } else { de /= length; dn /= length; }
      const x = points[i][0], y = points[i][2] + 0.02, z = -points[i][1];
      position.push(x + dn * half, y, z + de * half, x - dn * half, y, z - de * half);
      normal.push(0, 1, 0, 0, 1, 0);
      uv.push(0, distance, 1, distance);
      distance += i > 0 ? Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]) : 0;
    }
    for (let i = 0; i < points.length - 1; i++) {
      const v = base + 2 * i;
      indices.push(v, v + 2, v + 1, v + 1, v + 2, v + 3);
    }
    base += 2 * points.length;
  }
  if (!indices.length) return undefined;
  const geometry = new T.BufferGeometry();
  geometry.setAttribute('position', new T.Float32BufferAttribute(position, 3));
  geometry.setAttribute('normal', new T.Float32BufferAttribute(normal, 3));
  geometry.setAttribute('uv', new T.Float32BufferAttribute(uv, 2));
  geometry.setIndex(indices);
  const material = surfaceMaterial({ ...PROCEDURAL_PACK, layer: 'roads' as const,
    material: { ...PROCEDURAL_PACK.material, base_color_texture: '/procedural/asphalt.png' } } as PackedBatch);
  const mesh = new T.Mesh(geometry, material);
  mesh.name = 'lite-roads';
  mesh.receiveShadow = true;
  return mesh;
}

/** Build the lite display group; throws on contract violations instead of fabricating geometry. */
export function buildLiteCity(scene: LiteCityScene, group: T.Group): void {
  if (!isLiteCityScene(scene)) throw Error('Traffic city lite scene does not match traffic-city-lite/v1');
  const buildings = buildingMesh(scene.buildings);
  if (buildings) group.add(buildings);
  const roads = roadMesh(scene.roads ?? []);
  if (roads) group.add(roads);
  if (!buildings && !roads) throw Error('Traffic city lite scene has no renderable geometry');
  group.userData = {
    displayStyle: 'procedural-lite-city',
    buildingCount: scene.buildings.length,
    roadCount: scene.roads?.length ?? 0,
    displayEstimate: 'extruded recorded footprints and declared lane widths; facade finish is a display effect',
  };
}

function part(dimensions: [number, number, number], color: number): T.Mesh {
  const mesh = new T.Mesh(new T.BoxGeometry(...dimensions),
    new T.MeshStandardMaterial({ color, roughness: 0.8, metalness: 0.05 }));
  mesh.castShadow = true;
  return mesh;
}

/**
 * Shared vehicle stand-ins, identical in shape and colour to the recorded
 * standalone camera (packs/traffic_accident/camera.html): car body
 * 2.2 x 1.4 x 4 (render x/y/z, length along render z), UAV body 2 x 0.5 x 2
 * with two crossing 4 m rotor bars. Positioned by the caller at the committed
 * pose; no asset fetch.
 */
export function proceduralCar(): T.Group {
  const car = new T.Group();
  car.name = 'procedural-car';
  car.add(part([2.2, 1.4, 4], 0xe8a444));
  return car;
}
export function proceduralUav(): T.Group {
  const uav = new T.Group();
  uav.name = 'procedural-uav';
  const body = part([2, 0.5, 2], 0x50c9ff);
  body.name = 'procedural-uav-body';
  uav.add(body);
  const rotors = new T.Group();
  rotors.name = 'procedural-uav-rotors';
  rotors.add(part([4, 0.16, 0.2], 0x37434a), part([0.2, 0.16, 4], 0x37434a));
  uav.add(rotors);
  return uav;
}

/** Resolves reserved procedural asset URLs; undefined leaves the asset to the normal loader. */
export function proceduralModel(url: string): T.Object3D | undefined {
  if (!url.startsWith(PROCEDURAL_ASSET_SCHEME)) return undefined;
  switch (url.slice(PROCEDURAL_ASSET_SCHEME.length)) {
    case 'car': return proceduralCar();
    case 'uav': return proceduralUav();
    default: return undefined;
  }
}
