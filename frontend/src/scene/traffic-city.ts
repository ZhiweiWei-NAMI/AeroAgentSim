import * as T from 'three';
import { Assets, disposeObject } from '../viewport/assets';

/**
 * Traffic demo city layer: the console serves the original aero-bench scene at
 * /v1/studio/demo-assets/scene.json (see authoring/demo.py). Buildings carry real
 * GLB meshes plus run-space x/y/z; roads carry exact ground polygons, markings and
 * arrows. Everything renders flat in render XZ at the demo renderer's own layer
 * elevations — no geometry is invented and no height is extrapolated.
 */

interface SceneBuilding { url: string; x: number; y: number; z: number; height: number }
type Points = [number, number][];
interface ScenePolygon { outline: Points; holes?: Points[] }
interface SceneMarking { color?: string; width_m?: number; shape: Points | { points: Points } }
interface SceneRoads {
  asphalt?: ScenePolygon[]; sidewalk?: ScenePolygon[]; concrete?: ScenePolygon[]; medians?: ScenePolygon[];
  markings?: SceneMarking[]; arrows?: ScenePolygon[];
}
interface TrafficScene { buildings?: SceneBuilding[]; roads?: SceneRoads }

/** Flat layer stack copied from the demo renderer's scene.js. */
const ROAD_LAYERS: Array<[keyof Omit<SceneRoads, 'markings' | 'arrows'>, number]> = [
  ['asphalt', 0.01], ['concrete', 0.025], ['sidewalk', 0.045], ['medians', 0.065],
];
const ROAD_COLORS: Record<string, number> = { asphalt: 0x34383d, sidewalk: 0x92938f, concrete: 0xb4b2aa, medians: 0x6f8060 };
const MARK_COLORS: Record<string, number> = { white: 0xe9e9df, yellow: 0xe7c84b };

function polygonShape(item: ScenePolygon, label: string): T.BufferGeometry {
  if (!Array.isArray(item.outline) || item.outline.length < 3) throw Error(`Traffic city ${label}: polygon needs >= 3 outline points`);
  const shape = new T.Shape(item.outline.map(([x, z]) => new T.Vector2(x, z)));
  for (const hole of item.holes ?? []) {
    if (!Array.isArray(hole) || hole.length < 3) throw Error(`Traffic city ${label}: hole needs >= 3 points`);
    shape.holes.push(new T.Path(hole.map(([x, z]) => new T.Vector2(x, z))));
  }
  const geometry = new T.ShapeGeometry(shape);
  geometry.rotateX(Math.PI / 2);
  return geometry;
}

/** Exact marking ribbon: polylines swept by their own width_m, nothing else. */
function markingGeometry(marking: SceneMarking): T.BufferGeometry {
  const points = Array.isArray(marking.shape) ? marking.shape : marking.shape?.points;
  if (!Array.isArray(points) || points.length < 2) throw Error('Traffic city marking needs a polyline of >= 2 points');
  const width = marking.width_m;
  if(typeof width!=='number'||!Number.isFinite(width)||width<=0)throw Error('Traffic marking requires its recorded positive width_m');
  const vertices: number[] = [];
  const indices: number[] = [];
  let base = 0;
  for (let i = 0; i < points.length - 1; i++) {
    const [x1, z1] = points[i];
    const [x2, z2] = points[i + 1];
    const length = Math.hypot(x2 - x1, z2 - z1);
    if (length < 0.01) continue;
    const nx = (-(z2 - z1) / length) * width * 0.5;
    const nz = ((x2 - x1) / length) * width * 0.5;
    vertices.push(x1 - nx, 0, z1 - nz, x1 + nx, 0, z1 + nz, x2 + nx, 0, z2 + nz, x2 - nx, 0, z2 - nz);
    indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
    base += 4;
  }
  if (!vertices.length) throw Error('Traffic city marking has no usable segments');
  const geometry = new T.BufferGeometry();
  geometry.setAttribute('position', new T.Float32BufferAttribute(vertices, 3));
  geometry.setIndex(indices);
  return geometry;
}

/** Merge same-layout indexed geometries (position, optional normal/uv) into one draw call. */
function merge(parts: T.BufferGeometry[]): T.BufferGeometry {
  const count = parts.reduce((sum, part) => sum + part.getAttribute('position').count, 0);
  const position = new Float32Array(count * 3);
  const normal = parts.every(part => part.getAttribute('normal')) ? new Float32Array(count * 3) : null;
  const uv = parts.every(part => part.getAttribute('uv')) ? new Float32Array(count * 2) : null;
  const indices = parts.reduce((sum, part) => sum + (part.index?.count ?? part.getAttribute('position').count), 0);
  const index = new Uint32Array(indices);
  let offset = 0;
  let base = 0;
  for (const part of parts) {
    const size = part.getAttribute('position').count;
    position.set(part.getAttribute('position').array as Float32Array, offset * 3);
    if (normal) normal.set(part.getAttribute('normal').array as Float32Array, offset * 3);
    if (uv) uv.set(part.getAttribute('uv').array as Float32Array, offset * 2);
    if (part.index) for (let i = 0; i < part.index.count; i++) index[base++] = part.index.getX(i) + offset;
    else for (let i = 0; i < size; i++) index[base++] = i + offset;
    offset += size;
  }
  const merged = new T.BufferGeometry();
  merged.setAttribute('position', new T.BufferAttribute(position, 3));
  if (normal) merged.setAttribute('normal', new T.BufferAttribute(normal, 3));
  if (uv) merged.setAttribute('uv', new T.BufferAttribute(uv, 2));
  merged.setIndex(new T.BufferAttribute(index, 1));
  return merged;
}

export async function loadTrafficCity(url: string, assets: Assets, signal: AbortSignal): Promise<T.Group> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw Error(`Traffic scene HTTP ${response.status}: ${url}`);
  const scene = await response.json() as TrafficScene;
  const group = new T.Group();
  group.name = 'traffic-city';
  try {
    if(!scene.roads||!Array.isArray(scene.buildings))throw Error('Traffic city requires roads and buildings');
    const roads = scene.roads;
    for (const [layer, elevation] of ROAD_LAYERS) {
      const parts = (roads[layer] ?? []).map(item => polygonShape(item, layer));
      if (!parts.length) continue;
      const mesh = new T.Mesh(merge(parts), new T.MeshStandardMaterial({ color: ROAD_COLORS[layer], side: T.DoubleSide, roughness: 0.92, metalness: 0 }));
      for (const part of parts) part.dispose();
      mesh.position.y = elevation;
      mesh.receiveShadow = true;
      group.add(mesh);
    }
    if (roads.arrows?.length) {
      const parts = roads.arrows.map(item => polygonShape(item, 'arrow'));
      const mesh = new T.Mesh(merge(parts), new T.MeshBasicMaterial({ color: 0xf1f0d9, side: T.DoubleSide }));
      for (const part of parts) part.dispose();
      mesh.position.y = 0.08;
      group.add(mesh);
    }
    const markingParts = new Map<string, T.BufferGeometry[]>();
    for (const marking of roads.markings ?? []) {
      const key = marking.color === 'yellow' ? 'yellow' : 'white';
      const parts = markingParts.get(key) ?? [];
      parts.push(markingGeometry(marking));
      markingParts.set(key, parts);
    }
    for (const [key, parts] of markingParts) {
      const mesh = new T.Mesh(merge(parts), new T.MeshBasicMaterial({ color: MARK_COLORS[key], side: T.DoubleSide }));
      for (const part of parts) part.dispose();
      mesh.position.y = 0.075;
      group.add(mesh);
    }
    // Building URLs in the scene are service-relative (e.g. /assets/buildings/…);
    // resolve them against the scene service root, the directory of scene.json.
    const base = url.startsWith('blob:') ? location.href : new URL('.', new URL(url, location.href)).href;
    const buildings = scene.buildings!;
    let cursor = 0;
    const worker = async () => {
      while (cursor < buildings.length) {
        if (signal.aborted) throw Error('Traffic city loading aborted');
        const building = buildings[cursor++];
        // In-flight GLTF loads cannot be cancelled through Assets; abort is
        // enforced at each step boundary and by disposing the group on failure.
        const model = await assets.model(new URL(building.url.replace(/^\/assets\//,''), base).href);
        if (signal.aborted) throw Error('Traffic city loading aborted');
        model.position.set(building.x, building.y, building.z);
        group.add(model);
      }
    };
    await Promise.all(Array.from({ length: Math.min(8, buildings.length) }, worker));
    return group;
  } catch (error) {
    disposeObject(group);
    throw error;
  }
}
