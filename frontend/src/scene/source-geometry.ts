/** SUMO geometry and explicitly supplied OSM/tree geometry in run-local ENU metres. */
import * as THREE from 'three';

const SUMO_DEFAULT_LANE_WIDTH = 3.2;
const DASH_LEN = 3, DASH_GAP = 3;

async function fetchText(url: string, signal: AbortSignal, stage: string): Promise<string> {
  if (signal.aborted) throw new DOMException(`Aborted before ${stage}`, 'AbortError');
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status} ${res.statusText}`);
  return res.text();
}

function parseShape(attr: string): number[] {
  const out: number[] = [];
  for (const pair of attr.trim().split(/\s+/)) {
    const c = pair.split(',');
    const x = Number(c[0]), y = Number(c[1]);
    if (c.length < 2 || !Number.isFinite(x) || !Number.isFinite(y)) throw Error('Invalid SUMO lane coordinate');
    const n = out.length;
    if (n >= 2 && out[n - 2] === x && out[n - 1] === y) continue;
    out.push(x, y);
  }
  return out;
}

interface LanePolyline { pts: number[]; width: number }

function laneStripGeometry(lanes: LanePolyline[]): THREE.BufferGeometry {
  const pos: number[] = [], idx: number[] = [];
  for (const { pts, width } of lanes) {
    const n = pts.length / 2, half = width / 2, base = pos.length / 3;
    for (let i = 0; i < n; i++) {
      const a = Math.max(0, i - 1), b = Math.min(n - 1, i + 1);
      let dx = pts[2 * b] - pts[2 * a], dy = pts[2 * b + 1] - pts[2 * a + 1];
      const len = Math.hypot(dx, dy);
      if (len > 1e-9) { dx /= len; dy /= len; } else { dx = 1; dy = 0; }
      const px = -dy * half, py = dx * half; // ENU left normal
      pos.push(pts[2 * i] + px, 0, -(pts[2 * i + 1] + py)); // left vertex
      pos.push(pts[2 * i] - px, 0, -(pts[2 * i + 1] - py)); // right vertex
    }
    for (let i = 0; i < n - 1; i++) {
      const v = base + 2 * i; // L_i, R_i, L_i+1, R_i+1
      idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2); // +Y winding
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

function dashCenterGeometry(lanes: LanePolyline[]): THREE.BufferGeometry {
  const pos: number[] = [];
  for (const { pts } of lanes) {
    const n = pts.length / 2;
    let dash = true, left = DASH_LEN; // metres left in current dash/gap phase
    for (let i = 1; i < n; i++) {
      let sx = pts[2 * i - 2], sy = pts[2 * i - 1];
      const tx = pts[2 * i], ty = pts[2 * i + 1];
      let seg = Math.hypot(tx - sx, ty - sy);
      while (seg >= left) {
        const t = left / seg, nx = sx + (tx - sx) * t, ny = sy + (ty - sy) * t;
        if (dash) pos.push(sx, 0.02, -sy, nx, 0.02, -ny);
        sx = nx; sy = ny; seg -= left; dash = !dash;
        left = dash ? DASH_LEN : DASH_GAP;
      }
      if (dash && seg > 1e-9) pos.push(sx, 0.02, -sy, tx, 0.02, -ty);
      left -= seg;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  return geo;
}

export interface SumoRoadsOptions { dashCenterLine?: boolean }

export async function loadSumoRoads(url: string, signal: AbortSignal, options: SumoRoadsOptions = {}): Promise<THREE.Group> {
  const text = await fetchText(url, signal, 'parse');
  if (typeof DOMParser === 'undefined') throw new Error('DOMParser unavailable (browser environment required)');
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (signal.aborted) throw new DOMException('Aborted during parse', 'AbortError');
  const bad = doc.querySelector('parsererror');
  if (bad) throw new Error(`${url}: XML parse error: ${(bad.textContent ?? '').trim().slice(0, 160)}`);
  const net = doc.getElementsByTagName('net')[0];
  if (!net) throw new Error(`${url}: not a SUMO net.xml (missing <net> root)`);

  const lanes: LanePolyline[] = [];
  const diag = {
    source: 'sumo-net.xml', url, edgeCount: 0, laneCount: 0, lanePointsTotal: 0,
    internalLanesSkipped: 0, defaultWidthLanes: 0, missingShapeLanes: 0, degenerateLanes: 0,
  };
  const loc = net.getElementsByTagName('location')[0];
  if (loc) Object.assign(diag, { netOffset: loc.getAttribute('netOffset') });

  for (const edge of Array.from(net.getElementsByTagName('edge'))) {
    if (edge.getAttribute('function') === 'internal') { // explicit internal marker only
      diag.internalLanesSkipped += edge.getElementsByTagName('lane').length;
      continue;
    }
    diag.edgeCount++;
    for (const lane of Array.from(edge.getElementsByTagName('lane'))) {
      const shapeAttr = lane.getAttribute('shape');
      if (!shapeAttr) { diag.missingShapeLanes++; continue; }
      const pts = parseShape(shapeAttr);
      if (pts.length < 4) { diag.degenerateLanes++; continue; }
      const widthAttr = lane.getAttribute('width');
      if (widthAttr !== null && (!Number.isFinite(Number(widthAttr)) || Number(widthAttr) <= 0)) throw Error('Invalid SUMO lane width');
      const width = widthAttr === null ? SUMO_DEFAULT_LANE_WIDTH : Number(widthAttr);
      if (widthAttr === null) diag.defaultWidthLanes++;
      lanes.push({ pts, width });
      diag.laneCount++;
      diag.lanePointsTotal += pts.length / 2;
    }
  }

  const group = new THREE.Group();
  group.name = 'sumo-roads';
  if (lanes.length > 0) {
    const strips = new THREE.Mesh(laneStripGeometry(lanes),
      new THREE.MeshStandardMaterial({ color: 0x3c4046, roughness: 0.95, metalness: 0 }));
    strips.name = 'lane-strips';
    strips.receiveShadow = true;
    group.add(strips);
    if (options.dashCenterLine) {
      const dashes = new THREE.LineSegments(dashCenterGeometry(lanes), new THREE.LineBasicMaterial({ color: 0xf0f0f0 }));
      dashes.name = 'dash-center-lines';
      group.add(dashes);
    }
  }
  group.userData = diag;
  return group;
}

const TRUNK_HEIGHT = 2.4, TRUNK_RADIUS = 0.18, CANOPY_RADIUS = 1.7, CANOPY_HEIGHT = 3.4;

export function createTrees(positions: ReadonlyArray<readonly [number, number, number]>): THREE.Group {
  const group = new THREE.Group();
  group.name = 'trees';
  group.userData = {
    source: 'authored-tree-positions', treeCount: positions.length,
    trunkHeight: TRUNK_HEIGHT, canopyHeight: CANOPY_HEIGHT,
  };
  if (positions.length === 0) return group;
  const matrix = new THREE.Matrix4();
  const trunks = new THREE.InstancedMesh(
    new THREE.CylinderGeometry(TRUNK_RADIUS, TRUNK_RADIUS * 1.5, TRUNK_HEIGHT, 8),
    new THREE.MeshStandardMaterial({ color: 0x6b4b2a, roughness: 1 }), positions.length);
  const canopies = new THREE.InstancedMesh(
    new THREE.ConeGeometry(CANOPY_RADIUS, CANOPY_HEIGHT, 9),
    new THREE.MeshStandardMaterial({ color: 0x3e7c3a, roughness: 0.9 }), positions.length);
  positions.forEach(([east, north, up], i) => {
    if (![east, north, up].every(Number.isFinite)) throw Error('Invalid authored tree position');
    matrix.setPosition(east, up + TRUNK_HEIGHT / 2, -north);
    trunks.setMatrixAt(i, matrix);
    matrix.setPosition(east, up + TRUNK_HEIGHT + CANOPY_HEIGHT / 2, -north);
    canopies.setMatrixAt(i, matrix);
  });
  trunks.computeBoundingSphere();
  canopies.computeBoundingSphere();
  trunks.name = 'trunks';
  canopies.name = 'canopies';
  trunks.castShadow = canopies.castShadow = true;
  group.add(trunks, canopies);
  return group;
}

export interface EnuOrigin { lat: number; lon: number; alt: number }

function enuProject(lon: number, lat: number, origin: EnuOrigin): [number, number] {
  const rad = Math.PI / 180, a = 6378137, e2 = 6.69437999014e-3;
  const s = Math.sin(origin.lat * rad);
  const northPerRad = a * (1 - e2) / (1 - e2 * s * s) ** 1.5; // meridional radius of curvature
  const eastPerRad = (a / Math.sqrt(1 - e2 * s * s)) * Math.cos(origin.lat * rad); // prime vertical radius
  return [(lon - origin.lon) * rad * eastPerRad, (lat - origin.lat) * rad * northPerRad];
}

function buildingHeight(props: Record<string, unknown>): { height: number; estimated: boolean } | null {
  const raw = props.height;
  if (raw !== undefined && raw !== null) {
    const height = typeof raw === 'number' ? raw : typeof raw === 'string' ? Number(raw) : NaN;
    if (!Number.isFinite(height) || height <= 0) throw Error('Invalid supplied OSM height');
    return { height, estimated: false };
  }
  const rawLevels = props['building:levels'];
  if (rawLevels === undefined || rawLevels === null) return null;
  const levels = typeof rawLevels === 'number' ? rawLevels : typeof rawLevels === 'string' ? Number(rawLevels) : NaN;
  if (!Number.isFinite(levels) || levels <= 0) throw Error('Invalid supplied OSM building levels');
  return { height: levels * 3, estimated: true };
}

interface GeoJsonFeature {
  id?: string | number;
  properties?: Record<string, unknown> | null;
  geometry?: { type: string; coordinates: number[][][] } | null;
}
interface GeoJsonCollection { type: string; features?: GeoJsonFeature[] }

function footprintShape(rings: number[][][], origin: EnuOrigin, diag: { skippedDegenerateFootprint: number }): THREE.Shape | null {
  const project = (ring: number[][]): THREE.Vector2[] => {
    const out: THREE.Vector2[] = [];
    for (const c of ring) {
      const lon = c?.[0], lat = c?.[1];
      if (!Number.isFinite(lon) || !Number.isFinite(lat)) throw Error('Invalid GeoJSON footprint coordinate');
      const [x, y] = enuProject(lon, lat, origin);
      const last = out[out.length - 1];
      if (last && last.x === x && last.y === y) continue;
      out.push(new THREE.Vector2(x, y));
    }
    return out;
  };
  const outer = rings?.length ? project(rings[0]) : [];
  if (outer.length < 3 || Math.abs(THREE.ShapeUtils.area(outer)) < 1e-6) { diag.skippedDegenerateFootprint++; return null; }
  const shape = new THREE.Shape(outer);
  for (let r = 1; r < rings.length; r++) {
    const hole = project(rings[r]);
    if (hole.length < 3 || Math.abs(THREE.ShapeUtils.area(hole)) < 1e-6) { diag.skippedDegenerateFootprint++; return null; }
    shape.holes.push(new THREE.Path(hole));
  }
  return shape;
}

export async function loadOsmBuildings(url: string, origin: EnuOrigin, signal: AbortSignal): Promise<THREE.Group> {
  const text = await fetchText(url, signal, 'parse');
  if (signal.aborted) throw new DOMException('Aborted during parse', 'AbortError');
  let geo: GeoJsonCollection;
  try { geo = JSON.parse(text) as GeoJsonCollection; } catch (e) { throw new Error(`${url}: invalid JSON (${(e as Error).message})`); }
  if (geo?.type !== 'FeatureCollection' || !Array.isArray(geo.features)) throw new Error(`${url}: expected a GeoJSON FeatureCollection`);

  const diag = {
    source: 'osm-buildings', url, origin: { ...origin }, featureCount: geo.features.length,
    buildingCount: 0, estimatedHeightCount: 0, skippedNonPolygon: 0,
    skippedMissingHeight: 0, skippedDegenerateFootprint: 0,
  };
  const group = new THREE.Group();
  group.name = 'osm-buildings';
  const material = new THREE.MeshStandardMaterial({ color: 0xb9b2a7, roughness: 0.9 });
  for (const feature of geo.features) {
    const g = feature?.geometry;
    if (!g || g.type !== 'Polygon') { diag.skippedNonPolygon++; continue; }
    const h = buildingHeight(feature.properties ?? {});
    if (!h) { diag.skippedMissingHeight++; continue; }
    const shape = footprintShape(g.coordinates, origin, diag);
    if (!shape) continue;
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: h.height, bevelEnabled: false });
    geometry.rotateX(-Math.PI / 2); // shape (east, north, h) -> renderer (east, h, -north)
    const mesh = new THREE.Mesh(geometry, material);
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.userData = {
      osmId: feature.id ?? feature.properties?.id ?? feature.properties?.['@id'] ?? null,
      height: h.height, estimatedHeight: h.estimated,
    };
    group.add(mesh);
    diag.buildingCount++;
    if (h.estimated) diag.estimatedHeightCount++;
  }
  group.userData = diag;
  return group;
}
