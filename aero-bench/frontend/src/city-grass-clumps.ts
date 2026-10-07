import * as THREE from "three";
import type { CityEnvironmentPlan, EnvironmentPoint } from "./city-environment";
import { applyVegetationWind, type VegetationWindUniforms } from "./city-vegetation-wind";

/**
 * Authored grass clumps are a presentation design distributed over the inspected green
 * plan; they are not surveyed vegetation and carry no OSM provenance.
 */
export interface GrassClumpPlacement {
  readonly id: string;
  readonly patchId: string;
  readonly x: number;
  readonly z: number;
  readonly yaw: number;
  readonly scale: number;
  readonly radiusM: number;
  readonly provenance: {
    readonly kind: "authored-grass-clump";
    readonly designId: string;
    readonly patchId: string;
  };
}
export interface GrassClumpRejections {
  /** Clump disk crossed the outer boundary (edge used by exactly one triangle) of its patch. */
  readonly boundary: number;
  /** Clump disk reached a planned tree trunk disk. */
  readonly tree: number;
  /** Sampling stopped because the plan already reached maxCount accepted clumps. */
  readonly cap: number;
}

/** Documented trunk clearance: trunk radius assumed for planned trees without surveyed stems. */
export const GRASS_CLUMP_TRUNK_RADIUS_M = 0.35;

/** Deterministic FNV-1a over the key, then the MurmurHash3 fmix32 finaliser. Without the
 * finaliser, keys that differ only in their last character ("bary-a"/"bary-b") hash to
 * values a near-constant offset apart, which correlates the variates and lines up samples. */
function hash(text: string): number {
  let value = 2166136261;
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619);
  value ^= value >>> 16; value = Math.imul(value, 0x85ebca6b);
  value ^= value >>> 13; value = Math.imul(value, 0xc2b2ae35);
  value ^= value >>> 16;
  return value >>> 0;
}
function random(seed: number, id: string, purpose: string): number {
  return hash(`${seed}:${id}:${purpose}`) / 4294967296;
}

function signedArea(ring: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i]!, b = ring[(i + 1) % ring.length]!;
    area += a[0] * b[1] - b[0] * a[1];
  }
  return area / 2;
}
/** A boundary edge with its point-independent terms precomputed once per patch. */
interface BoundaryEdge {
  readonly a: EnvironmentPoint;
  readonly b: EnvironmentPoint;
  readonly dx: number;
  readonly dz: number;
  readonly lengthSq: number;
  /** Bounds of the edge expanded by the clump radius: a centre outside them is
   * strictly farther than radiusM from the whole segment, so the edge cannot reject. */
  readonly nearMinX: number;
  readonly nearMaxX: number;
  readonly nearMinZ: number;
  readonly nearMaxZ: number;
}
function buildBoundaryEdge(a: EnvironmentPoint, b: EnvironmentPoint, radiusM: number): BoundaryEdge {
  const dx = b[0] - a[0], dz = b[1] - a[1];
  const lengthSq = dx * dx + dz * dz;
  const reachX = radiusM + Math.abs(dx) * 0.5 + Math.hypot(dx, dz) * 0.5;
  const reachZ = radiusM + Math.abs(dz) * 0.5 + Math.hypot(dx, dz) * 0.5;
  return { a, b, dx, dz, lengthSq,
    nearMinX: Math.min(a[0], b[0]) - reachX, nearMaxX: Math.max(a[0], b[0]) + reachX,
    nearMinZ: Math.min(a[1], b[1]) - reachZ, nearMaxZ: Math.max(a[1], b[1]) + reachZ };
}
function edgeDistance(point: EnvironmentPoint, edge: BoundaryEdge): number {
  const t = edge.lengthSq > 0 ? Math.max(0, Math.min(1,
    ((point[0] - edge.a[0]) * edge.dx + (point[1] - edge.a[1]) * edge.dz) / edge.lengthSq)) : 0;
  return Math.hypot(point[0] - edge.a[0] - edge.dx * t, point[1] - edge.a[1] - edge.dz * t);
}

interface PatchGeometry {
  readonly id: string;
  readonly triangles: readonly (readonly EnvironmentPoint[])[];
  /** Area of each triangle, in the same order; the sampler subtracts it per candidate. */
  readonly triangleAreas: readonly number[];
  /** Edges used by exactly one triangle: the true outer boundary of the clipped patch,
   * each carrying a radius-expanded box for the cheap per-edge pre-test. */
  readonly boundary: readonly BoundaryEdge[];
  readonly areaM2: number;
}

/**
 * Boundary edges of a triangle soup are those appearing in exactly one triangle with one
 * direction; interior shared edges cancel in the directed-edge multiset.
 */
function boundaryEdges(triangles: readonly (readonly EnvironmentPoint[])[]):
    readonly (readonly EnvironmentPoint[])[] {
  const count = new Map<string, number>();
  const edges = new Map<string, readonly EnvironmentPoint[]>();
  for (const triangle of triangles) for (let i = 0; i < 3; i++) {
    const a = triangle[i]!, b = triangle[(i + 1) % 3]!;
    const key = a[0] === b[0] && a[1] === b[1] ? `${a[0]}:${a[1]}` :
      [a, b].map(point => `${point[0]}:${point[1]}`).sort().join("|");
    count.set(key, (count.get(key) ?? 0) + 1);
    edges.set(key, [a, b]);
  }
  return [...edges.entries()].filter(([key]) => count.get(key) === 1).map(([, edge]) => edge);
}

function buildPatchGeometry(plan: CityEnvironmentPlan, radiusM: number): PatchGeometry[] {
  return plan.grass.map(patch => {
    const boundaryPoints = boundaryEdges(patch.triangles);
    if (boundaryPoints.length === 0) throw new Error(`Grass patch has no drawable area: ${patch.id}`);
    const boundary = boundaryPoints.map(edge => buildBoundaryEdge(edge[0]!, edge[1]!, radiusM));
    const triangleAreas = patch.triangles.map(triangle => Math.abs(signedArea(triangle)));
    const areaM2 = triangleAreas.reduce((sum, area) => sum + area, 0);
    if (areaM2 <= 0) throw new Error(`Grass patch has no drawable area: ${patch.id}`);
    return { id: patch.id, triangles: patch.triangles, triangleAreas, boundary, areaM2 };
  });
}

/**
 * Deterministic area-weighted sampling of authored clump disks inside the plan's grass
 * triangles. A clump is rejected when its disk crosses the patch's outer boundary or
 * reaches a planned tree trunk disk; maxCount caps accepted placements.
 */
export function planGrassClumps(plan: CityEnvironmentPlan, options: { seed: number; designId: string;
    densityPerM2: number; radiusM: number; maxCount: number }): {
    placements: GrassClumpPlacement[]; rejected: { boundary: number; tree: number; cap: number } } {
  const { seed, designId, radiusM, maxCount } = options;
  const { densityPerM2 } = options;
  if (!designId) throw new Error("Grass clump plan needs a design ID");
  if (![seed, densityPerM2, radiusM, maxCount].every(value => Number.isFinite(value)) || seed < 0
    || densityPerM2 < 0 || radiusM <= 0 || maxCount < 0 || !Number.isSafeInteger(maxCount)) {
    throw new Error("Grass clump plan options are invalid");
  }
  const rejected = { boundary: 0, tree: 0, cap: 0 };
  if (plan.grass.length === 0) return { placements: [], rejected };
  const patches = buildPatchGeometry(plan, radiusM);
  if (!Number.isFinite(patches.reduce((sum, patch) => sum + patch.areaM2, 0))) {
    throw new Error("Grass clump plan area is invalid");
  }
  const trunkIndex = new Map<string, { x: number; z: number; radiusM: number }[]>();
  const trunkCell = 16;
  for (const tree of plan.trees) {
    const key = `${Math.floor(tree.x / trunkCell)}:${Math.floor(tree.z / trunkCell)}`;
    if (!trunkIndex.has(key)) trunkIndex.set(key, []);
    trunkIndex.get(key)!.push({ x: tree.x, z: tree.z, radiusM: GRASS_CLUMP_TRUNK_RADIUS_M * tree.scale });
  }
  const nearTrunk = (x: number, z: number): boolean => {
    for (let cx = Math.floor((x - radiusM - 1) / trunkCell); cx <= Math.floor((x + radiusM + 1) / trunkCell); cx++) {
      for (let cz = Math.floor((z - radiusM - 1) / trunkCell); cz <= Math.floor((z + radiusM + 1) / trunkCell); cz++) {
        for (const trunk of trunkIndex.get(`${cx}:${cz}`) ?? []) {
          if (Math.hypot(x - trunk.x, z - trunk.z) <= radiusM + trunk.radiusM) return true;
        }
      }
    }
    return false;
  };
  const placements: GrassClumpPlacement[] = [];
  // Total requested samples across the plan; the cap is global.
  const requested = patches.reduce((sum, patch) => sum + Math.round(patch.areaM2 * densityPerM2), 0);
  for (const patch of patches) {
    const target = Math.round(patch.areaM2 * densityPerM2);
    // Exact per-edge pre-test: a centre outside an edge's radius-expanded box is strictly
    // farther than radiusM from that whole segment, so that edge cannot reject the point
    // and its distance is never computed. Only distances that could fail are evaluated,
    // so the accept/reject decision for every candidate is unchanged.
    const nearBoundary = (point: EnvironmentPoint): boolean =>
      patch.boundary.some(edge => point[0] >= edge.nearMinX && point[0] <= edge.nearMaxX
        && point[1] >= edge.nearMinZ && point[1] <= edge.nearMaxZ
        && edgeDistance(point, edge) <= radiusM);
    for (let index = 0; index < target; index++) {
      if (placements.length >= maxCount) break;
      const id = `${patch.id}:${index}`;
      // Area-weighted triangle choice then uniform barycentric sample within the triangle.
      let threshold = random(seed, id, "triangle") * patch.areaM2, chosen = patch.triangles[0]!;
      for (let triangleIndex = 0; triangleIndex < patch.triangles.length; triangleIndex++) {
        threshold -= patch.triangleAreas[triangleIndex]!;
        if (threshold <= 0) { chosen = patch.triangles[triangleIndex]!; break; }
      }
      // Parallelogram fold: two uniform variates, reflected back into the triangle when
      // they fall in the other half. (A square-root warp must not be combined with it.)
      let r1 = random(seed, id, "bary-a"), r2 = random(seed, id, "bary-b");
      if (r1 + r2 > 1) { r1 = 1 - r1; r2 = 1 - r2; }
      const point: EnvironmentPoint = [
        chosen[0]![0] * (1 - r1 - r2) + chosen[1]![0] * r1 + chosen[2]![0] * r2,
        chosen[0]![1] * (1 - r1 - r2) + chosen[1]![1] * r1 + chosen[2]![1] * r2];
      if (nearBoundary(point)) { rejected.boundary++; continue; }
      if (nearTrunk(point[0], point[1])) { rejected.tree++; continue; }
      placements.push({ id: `clump:${id}`, patchId: patch.id, x: point[0], z: point[1],
        yaw: random(seed, id, "yaw") * Math.PI * 2,
        scale: 0.8 + random(seed, id, "scale") * 0.5, radiusM,
        provenance: { kind: "authored-grass-clump", designId, patchId: patch.id } });
    }
  }
  // Samples neither accepted nor rejected for geometry were dropped by the cap.
  rejected.cap = Math.max(0, requested - placements.length - rejected.boundary - rejected.tree);
  return { placements, rejected };
}

export const GRASS_CLUMP_BLADE_COUNT = 14;
/** Segments per blade; each blade is a tapered, forward-curving strip. */
export const GRASS_CLUMP_BLADE_SEGMENTS = 3;
const BLADE_HEIGHT_M = [0.26, 0.46] as const;
const BLADE_BASE_WIDTH_M = 0.028;
/**
 * Authored presentation palette measured from the lawn texture the clumps stand on
 * (`textures/terrain-v1/lawn-grass001/color.webp`, linear Rec.709 luma): the root is the
 * mean colour of the 25th-percentile texels, the tip the 97th-percentile colour x1.25 in
 * linear light for sunlit, translucent tips. Script:
 * `validation/terrain-realism-20261001/T7/measure-blade-palette.py`.
 */
const BLADE_ROOT = new THREE.Color(0x3e5423), BLADE_TIP = new THREE.Color(0x617f39);
/** Clumps farther than this are not drawn; the textured grass surface carries distance views. */
export const GRASS_CLUMP_MAX_DISTANCE_M = 180;
export const GRASS_CLUMP_CELL_M = 48;

/** Deterministic clump geometry: blades fan around the origin, curve outward and taper to a tip. */
export function grassClumpGeometry(): THREE.BufferGeometry {
  const positions: number[] = [], colors: number[] = [], index: number[] = [];
  const color = new THREE.Color();
  for (let blade = 0; blade < GRASS_CLUMP_BLADE_COUNT; blade++) {
    const r = (purpose: string): number => random(0, `blade:${blade}`, purpose);
    const yaw = (blade + r("yaw") * 0.8) / GRASS_CLUMP_BLADE_COUNT * Math.PI * 2;
    const height = BLADE_HEIGHT_M[0] + (BLADE_HEIGHT_M[1] - BLADE_HEIGHT_M[0]) * r("height");
    const lean = 0.18 + 0.32 * r("lean"), offset = 0.03 + 0.06 * r("offset");
    const outward = new THREE.Vector2(Math.cos(yaw), Math.sin(yaw));
    // The blade face is perpendicular to its lean so it reads from the side and above.
    const side = new THREE.Vector2(-outward.y, outward.x);
    const first = positions.length / 3;
    for (let segment = 0; segment <= GRASS_CLUMP_BLADE_SEGMENTS; segment++) {
      const t = segment / GRASS_CLUMP_BLADE_SEGMENTS;
      const reach = offset + lean * height * t * t;
      const y = height * (t - 0.18 * lean * t * t);
      const halfWidth = BLADE_BASE_WIDTH_M * (1 - t * 0.92) / 2;
      const cx = outward.x * reach, cz = outward.y * reach;
      positions.push(cx - side.x * halfWidth, y, cz - side.y * halfWidth,
        cx + side.x * halfWidth, y, cz + side.y * halfWidth);
      color.copy(BLADE_ROOT).lerp(BLADE_TIP, Math.pow(t, 0.8) * (0.75 + 0.25 * r("tint")));
      colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
      if (segment > 0) {
        const a = first + (segment - 1) * 2;
        index.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(index);
  geometry.computeVertexNormals();
  // Blade normals lean toward the sky so thin strips shade like a canopy rather than cards.
  const normal = geometry.getAttribute("normal") as THREE.BufferAttribute;
  for (let i = 0; i < normal.count; i++) {
    const n = new THREE.Vector3().fromBufferAttribute(normal, i).lerp(new THREE.Vector3(0, 1, 0), 0.6).normalize();
    normal.setXYZ(i, n.x, n.y, n.z);
  }
  geometry.computeBoundingBox(); geometry.computeBoundingSphere();
  return geometry;
}

export interface GrassClumpRenderer {
  readonly group: THREE.Group;
  readonly instanceCount: number;
  /** Show cells within GRASS_CLUMP_MAX_DISTANCE_M of the camera; returns visible instances. */
  update(camera: THREE.Camera): number;
  dispose(): void;
}

/**
 * One shared clump geometry instanced per spatial cell, so frustum and distance culling
 * work per cell. Per-instance tint varies the palette without textures.
 */
export function createGrassClumps(placements: readonly GrassClumpPlacement[],
    wind: VegetationWindUniforms, options: { readonly groundY: number }): GrassClumpRenderer {
  if (placements.length === 0) throw new Error("Grass clump mesh needs at least one placement");
  if (!Number.isFinite(options.groundY)) throw new Error("Grass clump ground height is invalid");
  const geometry = grassClumpGeometry();
  const material = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.86, metalness: 0,
    side: THREE.DoubleSide });
  material.name = "authored-grass-clump";
  try {
    applyVegetationWind(material, wind, "grass");
  } catch (error) { geometry.dispose(); material.dispose(); throw error; }
  const group = new THREE.Group();
  group.name = "Authored grass clumps (presentation design)";
  const cells = new Map<string, GrassClumpPlacement[]>();
  for (const placement of placements) {
    const key = `${Math.floor(placement.x / GRASS_CLUMP_CELL_M)}:${Math.floor(placement.z / GRASS_CLUMP_CELL_M)}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key)!.push(placement);
  }
  const meshes: { mesh: THREE.InstancedMesh; box: THREE.Box3 }[] = [];
  const matrix = new THREE.Matrix4(), quaternion = new THREE.Quaternion(), axis = new THREE.Vector3(0, 1, 0);
  const origin = new THREE.Vector3(), scale = new THREE.Vector3(), tint = new THREE.Color();
  for (const [key, cell] of cells) {
    const mesh = new THREE.InstancedMesh(geometry, material, cell.length);
    mesh.name = `authored-grass-clumps:${key}`;
    mesh.castShadow = false; mesh.receiveShadow = true;
    mesh.instanceMatrix.setUsage(THREE.StaticDrawUsage);
    cell.forEach((placement, index) => {
      origin.set(placement.x, options.groundY, placement.z);
      quaternion.setFromAxisAngle(axis, placement.yaw);
      scale.setScalar(placement.scale);
      mesh.setMatrixAt(index, matrix.compose(origin, quaternion, scale));
      const shade = random(0, placement.id, "tint");
      tint.setRGB(0.86 + 0.2 * shade, 0.9 + 0.14 * shade, 0.82 + 0.12 * (1 - shade));
      mesh.setColorAt(index, tint);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor !== null) mesh.instanceColor.needsUpdate = true;
    mesh.computeBoundingBox(); mesh.computeBoundingSphere();
    mesh.visible = false;
    group.add(mesh);
    meshes.push({ mesh, box: mesh.boundingBox!.clone() });
  }
  group.userData.grassClumpCount = placements.length;
  group.userData.grassClumpCells = meshes.length;
  const cameraPosition = new THREE.Vector3();
  return { group, instanceCount: placements.length,
    update: camera => {
      camera.getWorldPosition(cameraPosition);
      let visible = 0;
      for (const { mesh, box } of meshes) {
        mesh.visible = box.distanceToPoint(cameraPosition) <= GRASS_CLUMP_MAX_DISTANCE_M;
        if (mesh.visible) visible += mesh.count;
      }
      group.userData.visibleGrassClumps = visible;
      return visible;
    },
    dispose: () => {
      for (const { mesh } of meshes) mesh.dispose();
      geometry.dispose(); material.dispose(); group.clear();
    } };
}
