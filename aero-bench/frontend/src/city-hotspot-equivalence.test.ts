// Equivalence evidence for the T22 hot-spot rewrites. The "old" helpers below
// are the pre-change implementations kept verbatim; the "new" side imports the current
// modules. Every compared number must be bit-identical (`Object.is`), not just close.
// @vitest-environment node
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import * as THREE from "three";

import { validateCanonicalCityRoadPayload } from "./city-roads";
import { generateCityEnvironment, parseCityEnvironmentSource,
  BIGCITY_ENVIRONMENT_TREES, CITY_ENVIRONMENT_DEFAULTS, type CityEnvironmentConfig,
  type CityEnvironmentInput, type EnvironmentPoint } from "./city-environment";
import { planGrassClumps, type GrassClumpPlacement } from "./city-grass-clumps";
import { cityStaticObstacles } from "./city-static-obstacles";
import { cityVegetationRoadBinding, type CityVegetationEffectiveFixture } from "./city-vegetation-layer";

// ---------------------------------------------------------------------------
// Old implementations, verbatim from before the T22 change.
// ---------------------------------------------------------------------------

// Old bounds helper (city-ground-cover.ts:295, city-environment.ts:293,
// city-authored-landscape.ts:423 — same shape in all three).
interface OldBounds { readonly minX: number; readonly maxX: number; readonly minZ: number; readonly maxZ: number; }
function oldBounds(points: readonly EnvironmentPoint[]): OldBounds {
  return { minX: Math.min(...points.map(point => point[0])), maxX: Math.max(...points.map(point => point[0])),
    minZ: Math.min(...points.map(point => point[1])), maxZ: Math.max(...points.map(point => point[1])) };
}
/** The single-pass loop the three modules now use for the same value. It reproduces the
 * spread helper's NaN poisoning by folding every value through Math.min/Math.max. */
function newBoundsLoop(points: readonly EnvironmentPoint[]): OldBounds {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (let i = 0; i < points.length; i++) {
    const point = points[i]!;
    minX = Math.min(minX, point[0]);
    maxX = Math.max(maxX, point[0]);
    minZ = Math.min(minZ, point[1]);
    maxZ = Math.max(maxZ, point[1]);
  }
  return { minX, maxX, minZ, maxZ };
}

// Old planGrassClumps (city-grass-clumps.ts) — the whole pre-change planner.
function oldHash(text: string): number {
  let value = 2166136261;
  for (let i = 0; i < text.length; i++) value = Math.imul(value ^ text.charCodeAt(i), 16777619);
  value ^= value >>> 16; value = Math.imul(value, 0x85ebca6b);
  value ^= value >>> 13; value = Math.imul(value, 0xc2b2ae35);
  value ^= value >>> 16;
  return value >>> 0;
}
function oldRandom(seed: number, id: string, purpose: string): number {
  return oldHash(`${seed}:${id}:${purpose}`) / 4294967296;
}
function oldSignedArea(ring: readonly EnvironmentPoint[]): number {
  let area = 0;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i]!, b = ring[(i + 1) % ring.length]!;
    area += a[0] * b[1] - b[0] * a[1];
  }
  return area / 2;
}
function oldSegmentDistance(point: EnvironmentPoint, a: EnvironmentPoint, b: EnvironmentPoint): number {
  const dx = b[0] - a[0], dz = b[1] - a[1], lengthSq = dx * dx + dz * dz;
  const t = lengthSq > 0 ? Math.max(0, Math.min(1,
    ((point[0] - a[0]) * dx + (point[1] - a[1]) * dz) / lengthSq)) : 0;
  return Math.hypot(point[0] - a[0] - dx * t, point[1] - a[1] - dz * t);
}
interface OldPatchGeometry {
  readonly id: string;
  readonly triangles: readonly (readonly EnvironmentPoint[])[];
  readonly boundary: readonly (readonly EnvironmentPoint[])[];
  readonly areaM2: number;
}
function oldBoundaryEdges(triangles: readonly (readonly EnvironmentPoint[])[]):
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
function oldBuildPatchGeometry(plan: CityEnvironmentPlanLike): OldPatchGeometry[] {
  return plan.grass.map(patch => {
    const boundary = oldBoundaryEdges(patch.triangles);
    const areaM2 = patch.triangles.reduce((sum, triangle) => sum + Math.abs(oldSignedArea(triangle)), 0);
    if (boundary.length === 0 || areaM2 <= 0) throw new Error(`Grass patch has no drawable area: ${patch.id}`);
    return { id: patch.id, triangles: patch.triangles, boundary, areaM2 };
  });
}
const OLD_GRASS_CLUMP_TRUNK_RADIUS_M = 0.35;
function oldPlanGrassClumps(plan: CityEnvironmentPlanLike, options: { seed: number; designId: string;
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
  const patches = oldBuildPatchGeometry(plan);
  if (!Number.isFinite(patches.reduce((sum, patch) => sum + patch.areaM2, 0))) {
    throw new Error("Grass clump plan area is invalid");
  }
  const trunkIndex = new Map<string, { x: number; z: number; radiusM: number }[]>();
  const trunkCell = 16;
  for (const tree of plan.trees) {
    const key = `${Math.floor(tree.x / trunkCell)}:${Math.floor(tree.z / trunkCell)}`;
    if (!trunkIndex.has(key)) trunkIndex.set(key, []);
    trunkIndex.get(key)!.push({ x: tree.x, z: tree.z, radiusM: OLD_GRASS_CLUMP_TRUNK_RADIUS_M * tree.scale });
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
  const requested = patches.reduce((sum, patch) => sum + Math.round(patch.areaM2 * densityPerM2), 0);
  for (const patch of patches) {
    const target = Math.round(patch.areaM2 * densityPerM2);
    for (let index = 0; index < target; index++) {
      if (placements.length >= maxCount) break;
      const id = `${patch.id}:${index}`;
      let threshold = oldRandom(seed, id, "triangle") * patch.areaM2, chosen = patch.triangles[0]!;
      for (const triangle of patch.triangles) {
        threshold -= Math.abs(oldSignedArea(triangle));
        if (threshold <= 0) { chosen = triangle; break; }
      }
      let r1 = oldRandom(seed, id, "bary-a"), r2 = oldRandom(seed, id, "bary-b");
      if (r1 + r2 > 1) { r1 = 1 - r1; r2 = 1 - r2; }
      const point: EnvironmentPoint = [
        chosen[0]![0] * (1 - r1 - r2) + chosen[1]![0] * r1 + chosen[2]![0] * r2,
        chosen[0]![1] * (1 - r1 - r2) + chosen[1]![1] * r1 + chosen[2]![1] * r2];
      if (patch.boundary.some(edge => oldSegmentDistance(point, edge[0]!, edge[1]!) <= radiusM)) { rejected.boundary++; continue; }
      if (nearTrunk(point[0], point[1])) { rejected.tree++; continue; }
      placements.push({ id: `clump:${id}`, patchId: patch.id, x: point[0], z: point[1],
        yaw: oldRandom(seed, id, "yaw") * Math.PI * 2,
        scale: 0.8 + oldRandom(seed, id, "scale") * 0.5, radiusM,
        provenance: { kind: "authored-grass-clump", designId, patchId: patch.id } });
    }
  }
  rejected.cap = Math.max(0, requested - placements.length - rejected.boundary - rejected.tree);
  return { placements, rejected };
}

// Old obstacle measurement (city-static-obstacles.ts) — recomputes each geometry's
// bounding box from its vertices for every signal mesh visit. Returns the measured
// PlacementBox values without the `kind` tag so the two sides compare like for like.
function oldCityStaticObstacles(trees: THREE.Group, roads: THREE.Group | null,
                               traffic: THREE.Group | null): { id: string; x: number; z: number;
                               widthM: number; depthM: number; heightM: number; baseY: number }[] {
  const result: { id: string; x: number; z: number; widthM: number; depthM: number;
    heightM: number; baseY: number }[] = [];
  const add = (bounds: THREE.Box3, id: string): void => {
    const size = bounds.getSize(new THREE.Vector3()), centre = bounds.getCenter(new THREE.Vector3());
    if (bounds.isEmpty() || Math.min(size.x, size.y, size.z) <= 0) throw new Error(`Empty static obstacle: ${id}`);
    result.push({ id, x: centre.x, z: centre.z, widthM: size.x,
      depthM: size.z, heightM: size.y, baseY: bounds.min.y });
  };
  trees.updateWorldMatrix(true, true);
  trees.children.forEach((tree, index) => add(new THREE.Box3().setFromObject(tree), `tree:${index}`));
  roads?.updateWorldMatrix(true, true);
  const local = new THREE.Matrix4(), world = new THREE.Matrix4();
  roads?.traverse(node => {
    if (!(node instanceof THREE.InstancedMesh) || !node.name.startsWith("street_light_8 ")) return;
    node.geometry.computeBoundingBox();
    for (let index = 0; index < node.count; index++) {
      node.getMatrixAt(index, local);
      world.multiplyMatrices(node.matrixWorld, local);
      add(node.geometry.boundingBox!.clone().applyMatrix4(world), `lamp:${index}:${node.name}`);
    }
  });
  traffic?.updateWorldMatrix(true, true);
  traffic?.children.forEach(node => {
    if (node.userData.target?.kind !== "traffic_signal") return;
    node.traverse(part => {
      if (!(part instanceof THREE.Mesh)) return;
      part.geometry.computeBoundingBox();
      add(part.geometry.boundingBox!.clone().applyMatrix4(part.matrixWorld),
        `signal:${node.userData.target.id}:${part.name}`);
    });
  });
  return result;
}

interface CityEnvironmentPlanLike {
  readonly grass: readonly { readonly id: string; readonly triangles: readonly (readonly EnvironmentPoint[])[] }[];
  readonly trees: readonly { readonly x: number; readonly z: number; readonly scale: number }[];
}

// ---------------------------------------------------------------------------
// Real city data, built exactly the way the shipped load path does
// (canonical road v3 + effective fixtures + environment source, like the E1 test).
// ---------------------------------------------------------------------------

const realRoad = validateCanonicalCityRoadPayload(JSON.parse(readFileSync(
  resolve(process.cwd(), "public/city-presentation/huangpu-canonical-road-v3.json"), "utf8")));
const realFixtures = JSON.parse(readFileSync(
  resolve(process.cwd(), "public/city-presentation/huangpu-canonical-effective-fixtures-v1.json"),
  "utf8")) as { effective_fixtures: readonly CityVegetationEffectiveFixture[] };
const realSource = parseCityEnvironmentSource(JSON.parse(readFileSync(
  resolve(process.cwd(), "public/city-presentation/shanghai-source-environment-v1.json"), "utf8")),
  { osmSha256: "6948a5a2611145a4c3f4283d756d580fbd8155b049379de91d80bccd8bc2465b",
    objectsSha256: "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc",
    origin: { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50.0 } });
const realBinding = cityVegetationRoadBinding(realRoad, realFixtures.effective_fixtures);
let realMinX = Infinity, realMaxX = -Infinity, realMinZ = Infinity, realMaxZ = -Infinity;
for (const polygon of [...realSource.buildings, ...realSource.greens,
  ...realBinding.roadbed, ...realBinding.walkbed]) {
  for (const [x, z] of polygon.outline) {
    if (x < realMinX) realMinX = x;
    if (x > realMaxX) realMaxX = x;
    if (z < realMinZ) realMinZ = z;
    if (z > realMaxZ) realMaxZ = z;
  }
}
const realExtent = { outline: [[realMinX, realMinZ], [realMaxX, realMinZ], [realMaxX, realMaxZ],
  [realMinX, realMaxZ]] as [number, number][], holes: [] };
const realInput: CityEnvironmentInput = { geometryId: "city-presentation-vegetation-v1",
  roadGeometry: "published", roadbed: realBinding.roadbed, walkbed: realBinding.walkbed,
  crossings: realBinding.crossings, junctions: realBinding.junctions,
  lamps: realBinding.lamps, signals: realBinding.signals,
  buildings: realSource.buildings, extent: realExtent, greens: realSource.greens,
  sourceTrees: realSource.sourceTrees };
const realConfig: CityEnvironmentConfig = { ...CITY_ENVIRONMENT_DEFAULTS };
const realPlan = generateCityEnvironment(realInput, realConfig, BIGCITY_ENVIRONMENT_TREES);
// The published road clears one green entirely, so 7 grass patches remain (E1 evidence).
expect(realPlan.grass.length).toBeGreaterThan(5);

// ---------------------------------------------------------------------------
// Deep equality that distinguishes -0/0 and NaN payloads, number by number.
// ---------------------------------------------------------------------------

function deepSame(a: unknown, b: unknown, path: string): void {
  if (typeof a === "number" && typeof b === "number") {
    if (!Object.is(a, b)) throw new Error(`number differs at ${path}: ${a} vs ${b}`);
    return;
  }
  if (a === null || b === null || a === undefined || b === undefined || typeof a !== "object"
    || typeof b !== "object") {
    if (a !== b) throw new Error(`value differs at ${path}: ${String(a)} vs ${String(b)}`);
    return;
  }
  const left = a as Record<string, unknown>, right = b as Record<string, unknown>;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
      throw new Error(`array shape differs at ${path}`);
    }
    left.forEach((item, index) => deepSame(item, right[index], `${path}[${index}]`));
    return;
  }
  const keys = new Set([...Object.keys(left), ...Object.keys(right)]);
  for (const key of keys) deepSame(left[key], right[key], `${path}.${key}`);
}
function assertSameArray(left: readonly unknown[], right: readonly unknown[], label: string): void {
  if (left.length !== right.length) throw new Error(`${label}: length ${left.length} vs ${right.length}`);
  left.forEach((item, index) => deepSame(item, right[index], `${label}[${index}]`));
}

// ---------------------------------------------------------------------------
// Equivalence on real inputs.
// ---------------------------------------------------------------------------

describe("T22 bounds helper equivalence on real city polygons", () => {
  // Every polygon the three modules feed through the bounds helper on the real load:
  // grass clip triangles and intersections, road obstacles, building footprints, greens.
  const obstacles = [...realBinding.roadbed, ...realBinding.walkbed, ...realSource.buildings];
  const polygons: readonly (readonly EnvironmentPoint[])[] = [
    ...realPlan.grass.flatMap(patch => patch.triangles),
    ...obstacles.map(polygon => polygon.outline),
    ...obstacles.flatMap(polygon => polygon.holes),
    ...realSource.greens.map(item => item.outline),
    ...realSource.greens.flatMap(item => item.holes),
    realExtent.outline,
  ];
  it(`loop bounds agrees with the spread helper on ${polygons.length} real polygons`, () => {
    expect(polygons.length).toBeGreaterThan(100);
    let checked = 0;
    for (const polygon of polygons) {
      deepSame(oldBounds(polygon), newBoundsLoop(polygon), `polygon[${checked}]`);
      checked++;
    }
    expect(checked).toBe(polygons.length);
    console.log(`[t22] bounds: compared ${checked} real polygons (min/max per axis, Object.is)`);
  });
  it("keeps the spread helper's empty-array, NaN and signed-zero semantics", () => {
    deepSame(oldBounds([]), newBoundsLoop([]), "empty");
    expect(Object.is(newBoundsLoop([]).minX, Infinity)).toBe(true);
    expect(Object.is(newBoundsLoop([]).maxX, -Infinity)).toBe(true);
    // A NaN value poisons exactly the axes it feeds, in both implementations: the spread
    // helper returns NaN because Math.min/max propagate it, and the single-pass loop must
    // do the same, so it folds every value through Math.min/Math.max (city-ground-cover.ts).
    const nanPoints: EnvironmentPoint[] = [[1, 2], [NaN, 0]];
    const nanOld = oldBounds(nanPoints), nanNew = newBoundsLoop(nanPoints);
    deepSame(nanOld, nanNew, "nan");
    for (const key of ["minX", "maxX"] as const) expect(Object.is(nanNew[key], NaN)).toBe(true);
    expect(nanNew.minZ).toBe(0);
    expect(nanNew.maxZ).toBe(2);
    // Math.min(-0) === -0 and the single-pass Math.min/Math.max fold keeps the same
    // signed zeros as the spread helper (both go through the identical builtin).
    const zero: OldBounds = newBoundsLoop([[3, -0]]);
    expect(Object.is(zero.minZ, -0)).toBe(true);
    expect(Object.is(zero.maxZ, -0)).toBe(true);
    deepSame(oldBounds([[3, -0]]), zero, "negative zero");
  });
});

describe("T22 grass clump plan equivalence on the published city plan", () => {
  // The production design (city-vegetation-layer defaults) plus two more seeds and a
  // second design ID, per the ticket.
  const seeds = [20260930, 7, 2026];
  const designIds = ["aero-bench.authored-grass-clumps/v1", "t22-equivalence-design"];
  const productionOptions = { seed: 20260930, designId: "aero-bench.authored-grass-clumps/v1",
    densityPerM2: 0.4, radiusM: 0.3, maxCount: 24000 };
  it(`old and new planners agree on ${seeds.length} seeds x ${designIds.length} designs`, () => {
    expect(realPlan.grass.length).toBeGreaterThan(0);
    let placementsCompared = 0, plansCompared = 0;
    for (const seed of seeds) for (const designId of designIds) {
      const options = { ...productionOptions, seed, designId };
      const oldResult = oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, options);
      const newResult = planGrassClumps(realPlan, options);
      assertSameArray(oldResult.placements, newResult.placements,
        `placements seed=${seed} design=${designId}`);
      deepSame(oldResult.rejected, newResult.rejected, `rejected seed=${seed} design=${designId}`);
      placementsCompared += newResult.placements.length;
      plansCompared++;
      // The capped variant exercises the cap accounting path.
      const cappedOptions = { ...options, maxCount: 10 };
      const oldCapped = oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, cappedOptions);
      const newCapped = planGrassClumps(realPlan, cappedOptions);
      assertSameArray(oldCapped.placements, newCapped.placements, `capped placements seed=${seed}`);
      deepSame(oldCapped.rejected, newCapped.rejected, `capped rejected seed=${seed}`);
      plansCompared++;
    }
    expect(plansCompared).toBe(seeds.length * designIds.length * 2);
    expect(placementsCompared).toBeGreaterThan(1000);
    console.log(`[t22] grass plan: compared ${plansCompared} plans, ${placementsCompared} placements`
      + ` (production seed ${productionOptions.seed}, ${realPlan.grass.length} patches)`);
  }, 60000);
  it("throws the same errors for invalid input", () => {
    const options = { seed: 7, designId: "design", densityPerM2: 0.4, radiusM: 0.3, maxCount: 10 };
    expect(() => oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, { ...options, designId: "" }))
      .toThrow(/design ID/);
    expect(() => planGrassClumps(realPlan, { ...options, designId: "" })).toThrow(/design ID/);
    expect(() => oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, { ...options, radiusM: 0 }))
      .toThrow(/invalid/);
    expect(() => planGrassClumps(realPlan, { ...options, radiusM: 0 })).toThrow(/invalid/);
    expect(() => oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, { ...options, maxCount: -1 }))
      .toThrow(/invalid/);
    expect(() => planGrassClumps(realPlan, { ...options, maxCount: -1 })).toThrow(/invalid/);
    expect(() => oldPlanGrassClumps(realPlan as unknown as CityEnvironmentPlanLike, { ...options, seed: NaN }))
      .toThrow(/invalid/);
    expect(() => planGrassClumps(realPlan, { ...options, seed: NaN })).toThrow(/invalid/);
  });
});

describe("T22 static obstacle measurement equivalence on real signal meshes", () => {
  it("agrees per signal part for the loaded traffic_light_4 model across 12 signal clones",
    async () => {
    const bytes = readFileSync(
      resolve(process.cwd(), "public/models/incoming/furniture/glb/traffic_light_4.glb"));
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const gltf = await new Promise<{ scene: THREE.Group }>((resolvePromise, reject) => {
      void import("three/examples/jsm/loaders/GLTFLoader.js").then(({ GLTFLoader }) => {
        new GLTFLoader().parse(buffer, "", value => resolvePromise(value as { scene: THREE.Group }),
          error => reject(error instanceof Error ? error : new Error(String(error))));
      });
    });
    const lamp = gltf.scene;
    const group = new THREE.Group();
    // Real placements and poses from the effective fixture inventory, transformed the way
    // CityTrafficPreview poses each signal (city-presentation.ts).
    const signals = (realFixtures as unknown as {
      source_inventories: { signals: { id: string; x: number; z: number; heading: number }[] };
    }).source_inventories.signals.slice(0, 12);
    expect(signals.length).toBe(12);
    for (const location of signals) {
      const object = new THREE.Group();
      object.add(lamp.clone(true));
      object.position.set(location.x, 0, location.z);
      object.rotation.y = -THREE.MathUtils.degToRad(location.heading);
      object.userData.target = { kind: "traffic_signal", id: location.id };
      group.add(object);
    }
    const oldBoxes = oldCityStaticObstacles(new THREE.Group(), null, group);
    const newBoxes = cityStaticObstacles(new THREE.Group(), null, group)
      .map(box => ({ id: box.id, x: box.x, z: box.z, widthM: box.widthM, depthM: box.depthM,
        heightM: box.heightM, baseY: box.baseY }));
    expect(oldBoxes.length).toBeGreaterThan(1000);
    expect(newBoxes).toHaveLength(oldBoxes.length);
    let checked = 0;
    for (const [index, oldBox] of oldBoxes.entries()) {
      deepSame({ ...oldBox }, newBoxes[index], `obstacle[${index}] ${oldBox.id}`);
      checked++;
    }
    console.log(`[t22] static obstacles: compared ${checked} boxes over ${signals.length} signal meshes`);
    expect(checked).toBe(oldBoxes.length);
  }, 120000);
});
