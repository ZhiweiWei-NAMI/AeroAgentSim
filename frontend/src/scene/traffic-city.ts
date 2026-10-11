import * as T from 'three';
import { Assets, disposeObject } from '../viewport/assets';
import { buildLiteCity, isLiteCityScene } from '../viewport/procedural-traffic';

/**
 * Traffic demo city layer: the console serves the original traffic-demo city scene at
 * /v1/studio/demo-assets/scene.json (see authoring/demo.py). Buildings carry real
 * GLB meshes plus run-space x/y/z; roads carry exact ground polygons, markings and
 * arrows. Everything renders flat in render XZ at the demo renderer's own layer
 * elevations — no geometry is invented and no height is extrapolated.
 *
 * When the public clone cannot ship those meshes, the same URL serves the lite
 * bundle (traffic-city-lite/v1: real footprints + recorded heights only). That
 * shape is detected and rendered procedurally in buildLiteCity — no GLB loads.
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

/** Pack buildings have small static meshes but repeat their facade/roof materials.
 * Merge matching source materials after streaming, preserving recorded transforms. */
function batchBuildings(buildings: T.Object3D[]) {
  const batches = new Map<string, { material: T.MeshStandardMaterial; parts: T.BufferGeometry[] }>();
  for (const building of buildings) {
    building.updateMatrixWorld(true);
    building.traverse(node => {
      if (!(node instanceof T.Mesh)) return;
      if (!(node.material instanceof T.MeshStandardMaterial) || node instanceof T.SkinnedMesh) throw Error('Traffic building requires a static standard material');
      const material = node.material;
      const textureKey = (texture: T.Texture | null) => texture ? [texture.source.uuid,texture.colorSpace,texture.wrapS,texture.wrapT,texture.flipY,texture.channel,...texture.repeat.toArray(),...texture.offset.toArray(),texture.rotation] : null;
      const key = JSON.stringify([material.type,material.color.toArray(),material.emissive.toArray(),material.emissiveIntensity,material.roughness,material.metalness,material.opacity,material.transparent,material.side,material.alphaTest,material.normalScale.toArray(),textureKey(material.map),textureKey(material.normalMap),textureKey(material.emissiveMap)]);
      let batch = batches.get(key);
      if (!batch) { batch={material,parts:[]};batches.set(key,batch); }
      batch.parts.push(node.geometry.clone().applyMatrix4(node.matrixWorld));
    });
  }
  return [...batches.values()].map(({material,parts}) => {
    const mesh = new T.Mesh(merge(parts),material);mesh.castShadow=true;mesh.receiveShadow=true;
    mesh.name='city-buildings';mesh.geometry.computeBoundingSphere();
    for (const geometry of parts) geometry.dispose();
    return mesh;
  });
}

export interface CityProgress { phase: 'roads' | 'buildings' | 'complete'; loaded: number; total: number }
interface CityEntry { group: T.Group; task: Promise<T.Group>; listeners: Set<() => void>; progress: CityProgress }
export interface CityLoadOptions {
  /** Receives the same instance as each source chunk becomes available. */
  onUpdate?: (group: T.Group, progress: CityProgress) => void;
  priority?: [number, number, number];
}
const cityCache = new Map<string, CityEntry>();
export function clearTrafficCityCache() { cityCache.clear(); }
const yieldToBrowser = () => new Promise<void>(resolve => setTimeout(resolve, 0));
function markShared(root: T.Object3D) {
  root.traverse(node => {
    if (!(node instanceof T.Mesh || node instanceof T.Line)) return;
    node.geometry.userData.sharedAsset = true;
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) material.userData.sharedAsset = true;
  });
}

/**
 * Page-session cached: the city is downloaded and built once per URL, every caller gets a
 * clone sharing geometry/materials (never disposed per view). The shared build is not tied
 * to one caller's signal; an aborted caller simply drops its clone.
 */
export function loadTrafficCity(url: string, assets: Assets, signal: AbortSignal, options: CityLoadOptions = {}): Promise<T.Group> {
  let entry = cityCache.get(url);
  if (!entry) {
    const group = new T.Group(); group.name = 'traffic-city';
    const listeners = new Set<() => void>();
    const release = assets.retain?.();
    entry = { group, listeners, progress: {phase:'roads',loaded:0,total:0}, task: undefined! };
    const current = entry;
    current.task = buildTrafficCity(url, assets, group, progress => {
      current.progress = progress;
      markShared(group);
      for (const update of listeners) update();
    }, options.priority).finally(() => release?.());
    cityCache.set(url, current);
    void current.task.catch(() => { if (cityCache.get(url) === current) cityCache.delete(url); });
  }
  const instance = new T.Group(); instance.name = 'traffic-city';
  let copied = 0, generation = 0;
  const current = entry;
  const update = () => {
    if (signal.aborted) return;
    if (generation !== (current.group.userData.geometryRevision ?? 0)) { instance.clear();copied=0;generation=current.group.userData.geometryRevision; }
    for (; copied < current.group.children.length; copied++) instance.add(current.group.children[copied].clone(true));
    Object.assign(instance.userData, current.group.userData, {loading:current.progress.phase !== 'complete', progress:{...current.progress}});
    if (instance.children.length) options.onUpdate?.(instance, current.progress);
  };
  current.listeners.add(update); update();
  const unsubscribe = () => current.listeners.delete(update);
  signal.addEventListener('abort', unsubscribe, {once:true});
  return current.task.then(() => {
    if (signal.aborted) throw Error('Traffic city loading aborted');
    update(); return instance;
  }).finally(() => { unsubscribe(); signal.removeEventListener('abort', unsubscribe); });
}

async function buildTrafficCity(url: string, assets: Assets, group: T.Group, publish: (progress: CityProgress) => void, priority?: [number, number, number]): Promise<T.Group> {
  const response = await fetch(url);
  if (!response.ok) throw Error(`Traffic scene HTTP ${response.status}: ${url}`);
  const scene = await response.json() as TrafficScene;
  try {
    // Lite bundle (public clone, no GLBs): recorded footprints/heights only,
    // rendered with existing procedural viewport materials.
    if (isLiteCityScene(scene)) {
      await yieldToBrowser();
      buildLiteCity(scene, group);
      publish({phase:'complete',loaded:scene.buildings.length,total:scene.buildings.length});
      return group;
    }
    if(!scene.roads||!Array.isArray(scene.buildings))throw Error('Traffic city requires roads and buildings');
    const roads = scene.roads;
    const total = scene.buildings.length;
    group.userData.displayStyle = 'huangpu-source-models';
    group.userData.buildingCount = total;
    const polygons = async (items: ScenePolygon[], label: string) => {
      const parts: T.BufferGeometry[] = [];
      for (let index = 0; index < items.length; index++) {
        parts.push(polygonShape(items[index], label));
        if (index % 80 === 79) await yieldToBrowser();
      }
      return parts;
    };
    for (const [layer, elevation] of ROAD_LAYERS) {
      const parts = await polygons(roads[layer] ?? [], layer);
      if (!parts.length) continue;
      const mesh = new T.Mesh(merge(parts), new T.MeshStandardMaterial({ color: ROAD_COLORS[layer], side: T.DoubleSide, roughness: 0.92, metalness: 0 }));
      for (const part of parts) part.dispose();
      mesh.position.y = elevation;
      mesh.receiveShadow = true;
      group.add(mesh); publish({phase:'roads',loaded:0,total});
      await yieldToBrowser();
    }
    if (roads.arrows?.length) {
      const parts = await polygons(roads.arrows, 'arrow');
      const mesh = new T.Mesh(merge(parts), new T.MeshBasicMaterial({ color: 0xf1f0d9, side: T.DoubleSide }));
      for (const part of parts) part.dispose();
      mesh.position.y = 0.08;
      group.add(mesh);
    }
    const markingParts = new Map<string, T.BufferGeometry[]>();
    let markingCount = 0;
    for (const marking of roads.markings ?? []) {
      const key = marking.color === 'yellow' ? 'yellow' : 'white';
      const parts = markingParts.get(key) ?? [];
      parts.push(markingGeometry(marking));
      markingParts.set(key, parts);
      if (++markingCount % 150 === 0) await yieldToBrowser();
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
    publish({phase:'buildings',loaded:0,total});
    await yieldToBrowser();
    const [focusX, , focusZ] = priority ?? [0,0,0];
    const buildings = [...scene.buildings!].sort((a,b) => Math.hypot(a.x-focusX,a.z-focusZ)-Math.hypot(b.x-focusX,b.z-focusZ));
    const models: T.Object3D[] = [];
    let cursor = 0, loaded = 0;
    const worker = async () => {
      while (cursor < buildings.length) {
        const building = buildings[cursor++];
        const model = await assets.model(new URL(building.url.replace(/^\/assets\//,''), base).href);
        model.position.set(building.x, building.y, building.z);
        group.add(model);models.push(model);
        loaded++;
        if (loaded % 8 === 0 || loaded === total) publish({phase:'buildings',loaded,total});
        await yieldToBrowser();
      }
    };
    await Promise.all(Array.from({ length: Math.min(3, buildings.length) }, worker));
    const batches=batchBuildings(models);
    if(batches.length){for(const model of models)group.remove(model);group.add(...batches);group.userData.geometryRevision=1;}
    group.userData.buildingDrawCalls=batches.length;
    publish({phase:'complete',loaded,total});
    return group;
  } catch (error) {
    disposeObject(group);
    throw error;
  }
}
