// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { generateCityEnvironment, type CityEnvironmentInput, type CityEnvironmentPlan,
  type EnvironmentGreen, type EnvironmentPolygon } from "./city-environment";
import { CITY_ENVIRONMENT_DEFAULTS, BIGCITY_ENVIRONMENT_TREES } from "./city-environment";
import { createGrassClumps, planGrassClumps, GRASS_CLUMP_TRUNK_RADIUS_M,
  GRASS_CLUMP_BLADE_COUNT, GRASS_CLUMP_BLADE_SEGMENTS, GRASS_CLUMP_MAX_DISTANCE_M } from "./city-grass-clumps";
import { createVegetationWindUniforms } from "./city-vegetation-wind";

const sha = "a".repeat(64);
function rect(x1: number, z1: number, x2: number, z2: number): EnvironmentPolygon {
  return { outline: [[x1, z1], [x2, z1], [x2, z2], [x1, z2]], holes: [] };
}
function green(polygon: EnvironmentPolygon, id = "green"): EnvironmentGreen {
  return { ...polygon, id, provenance: { kind: "osm", sourceSha256: sha, elementType: "way",
    elementId: "900719925474099300", sourceElementId: "123", sourceElementType: "way", tags: { landuse: "grass" } } };
}
function input(overrides: Partial<CityEnvironmentInput> = {}): CityEnvironmentInput {
  return { geometryId: "clump-test-geometry", roadGeometry: "pending-native-gate",
    roadbed: [], walkbed: [], buildings: [{ ...rect(35, 35, 38, 38), id: "building" }],
    crossings: [], junctions: [], lamps: [], signals: [], extent: rect(-50, -50, 50, 50),
    greens: [green(rect(-20, -20, 20, 20)), green(rect(22, -20, 42, 20), "green2")],
    sourceTrees: [], ...overrides };
}
const asset = BIGCITY_ENVIRONMENT_TREES[0]!;
function plan(overrides: Partial<CityEnvironmentInput> = {}, greenTrees = true): CityEnvironmentPlan {
  return generateCityEnvironment(input(overrides),
    { ...CITY_ENVIRONMENT_DEFAULTS, species: [asset.id], density: 1, spacingM: 5, scaleRange: [1, 1],
      greenTrees },
    [asset]);
}
const OPTIONS = { seed: 7, designId: "aero-bench.authored-grass-clumps/v1",
  densityPerM2: 0.4, radiusM: 0.3, maxCount: 100000 };

describe("authored grass clump planning", () => {
  it("is deterministic for a fixed seed and differs for another seed", () => {
    const p = plan();
    expect(p.grass.length).toBeGreaterThan(0);
    const a = planGrassClumps(p, OPTIONS), b = planGrassClumps(p, OPTIONS);
    expect(a).toEqual(b);
    expect(a.placements.length).toBeGreaterThan(50);
    const other = planGrassClumps(p, { ...OPTIONS, seed: 8 });
    expect(other.placements).not.toEqual(a.placements);
  });
  it("keeps every accepted disk inside its patch and clear of every planned trunk", () => {
    const p = plan();
    const { placements } = planGrassClumps(p, OPTIONS);
    expect(placements.length).toBeGreaterThan(0);
    const patches = new Map(p.grass.map(patch => [patch.id, patch]));
    for (const clump of placements) {
      const patch = patches.get(clump.patchId);
      expect(patch).toBeDefined();
      for (const triangle of patch!.triangles) {
        for (const [x, z] of triangle) {
          expect(Math.hypot(clump.x - x, clump.z - z)).toBeGreaterThan(clump.radiusM - 1e-6);
        }
      }
      for (const tree of p.trees) {
        expect(Math.hypot(clump.x - tree.x, clump.z - tree.z))
          .toBeGreaterThan(clump.radiusM + GRASS_CLUMP_TRUNK_RADIUS_M * tree.scale - 1e-6);
      }
    }
  });
  it("samples a patch uniformly by area rather than along triangle edges", () => {
    const p = plan({ greens: [green(rect(-20, -20, 20, 20))] }, false);
    const { placements } = planGrassClumps(p, { ...OPTIONS, densityPerM2: 2 });
    // 4 x 4 cells of 10 m: every cell holds about 1/16 of the samples (boundary rejection
    // only trims a 0.3 m rim, well inside the tolerance).
    const counts = new Array<number>(16).fill(0);
    for (const clump of placements) {
      const column = Math.min(3, Math.floor((clump.x + 20) / 10)), row = Math.min(3, Math.floor((clump.z + 20) / 10));
      counts[row * 4 + column]!++;
    }
    const mean = placements.length / 16;
    expect(placements.length).toBeGreaterThan(3000);
    for (const count of counts) expect(Math.abs(count - mean) / mean).toBeLessThan(0.2);
  });
  it("labels every placement with the authored design provenance", () => {
    const { placements } = planGrassClumps(plan(), OPTIONS);
    for (const clump of placements) {
      expect(clump.provenance).toEqual({ kind: "authored-grass-clump",
        designId: "aero-bench.authored-grass-clumps/v1", patchId: clump.patchId });
      expect(clump.id).toContain(clump.patchId);
      expect(clump.radiusM).toBe(OPTIONS.radiusM);
      expect(clump.scale).toBeGreaterThanOrEqual(0.8);
      expect(clump.scale).toBeLessThanOrEqual(1.3);
      expect(clump.yaw).toBeGreaterThanOrEqual(0);
      expect(clump.yaw).toBeLessThan(Math.PI * 2);
    }
  });
  it("respects the cap and reports the remainder", () => {
    const p = plan();
    const full = planGrassClumps(p, OPTIONS);
    const capped = planGrassClumps(p, { ...OPTIONS, maxCount: 10 });
    expect(capped.placements).toHaveLength(10);
    expect(capped.placements).toEqual(full.placements.slice(0, 10));
    expect(capped.rejected.cap).toBeGreaterThan(0);
  });
  it("returns zero placements when the cap is zero without sampling", () => {
    const p = plan({}, false);
    expect(p.trees).toEqual([]);
    const capped = planGrassClumps(p, { ...OPTIONS, maxCount: 0 });
    expect(capped.placements).toEqual([]);
    expect(capped.rejected.cap).toBeGreaterThan(0);
    expect(capped.rejected.boundary).toBe(0);
    expect(capped.rejected.tree).toBe(0);
    const uncapped = planGrassClumps(p, OPTIONS);
    expect(uncapped.placements.length).toBeGreaterThan(0);
    expect(uncapped.rejected.tree).toBe(0);
    expect(uncapped.rejected.cap).toBe(0);
    expect(uncapped.placements.length + uncapped.rejected.boundary).toBeGreaterThan(0);
  });
  it("rejects boundary-crossing and trunk-touching samples", () => {
    // A disk as wide as the whole 40x40 patch forces boundary rejections.
    const wide = planGrassClumps(plan(), { ...OPTIONS, radiusM: 19, densityPerM2: 0.05 });
    expect(wide.placements).toEqual([]);
    expect(wide.rejected.boundary).toBeGreaterThan(0);
    // Dense clumping around one tree forces trunk rejections.
    const dense = planGrassClumps(plan(), { ...OPTIONS, radiusM: 3, densityPerM2: 0.5 });
    expect(dense.rejected.tree).toBeGreaterThan(0);
  });
  it("returns zero placements for an empty plan and rejects invalid options", () => {
    const empty = plan({ greens: [] });
    expect(empty.grass).toEqual([]);
    const result = planGrassClumps(empty, OPTIONS);
    expect(result).toEqual({ placements: [], rejected: { boundary: 0, tree: 0, cap: 0 } });
    const p = plan();
    expect(() => planGrassClumps(p, { ...OPTIONS, radiusM: 0 })).toThrow(/invalid/);
    expect(() => planGrassClumps(p, { ...OPTIONS, densityPerM2: -1 })).toThrow(/invalid/);
    expect(() => planGrassClumps(p, { ...OPTIONS, maxCount: -1 })).toThrow(/invalid/);
    expect(() => planGrassClumps(p, { ...OPTIONS, designId: "" })).toThrow(/design ID/);
  });
});

describe("authored grass clump mesh", () => {
  it("instances one shared curved-blade geometry per cell, on the grass plane, with wind", () => {
    const p = plan();
    const { placements } = planGrassClumps(p, OPTIONS);
    const wind = createVegetationWindUniforms();
    const renderer = createGrassClumps(placements, wind, { groundY: 0.018 });
    expect(renderer.group.name).toBe("Authored grass clumps (presentation design)");
    const meshes = renderer.group.children as THREE.InstancedMesh[];
    expect(meshes.length).toBeGreaterThan(0);
    expect(meshes.reduce((sum, mesh) => sum + mesh.count, 0)).toBe(placements.length);
    expect(new Set(meshes.map(mesh => mesh.geometry)).size).toBe(1);
    const material = meshes[0]!.material as THREE.MeshStandardMaterial;
    expect(material.vertexColors).toBe(true);
    expect(material.customProgramCacheKey()).toBe("city-vegetation-wind-v2:grass");
    const positions = meshes[0]!.geometry.getAttribute("position");
    expect(positions.count).toBe(GRASS_CLUMP_BLADE_COUNT * (GRASS_CLUMP_BLADE_SEGMENTS + 1) * 2);
    const box = meshes[0]!.geometry.boundingBox!;
    expect(box.min.y).toBeGreaterThanOrEqual(0);
    expect(box.max.y).toBeGreaterThan(0.2); expect(box.max.y).toBeLessThan(0.5);
    const seen = new Set<string>(), matrix = new THREE.Matrix4();
    for (const mesh of meshes) for (let i = 0; i < mesh.count; i++) {
      mesh.getMatrixAt(i, matrix);
      expect(matrix.elements[13]).toBeCloseTo(0.018, 6);
      seen.add(`${matrix.elements[12].toFixed(2)}:${matrix.elements[14].toFixed(2)}`);
    }
    expect(seen).toEqual(new Set(placements.map(item => `${Math.fround(item.x).toFixed(2)}:${Math.fround(item.z).toFixed(2)}`)));
    const geometryDisposal = vi.spyOn(meshes[0]!.geometry, "dispose");
    const materialDisposal = vi.spyOn(material, "dispose");
    renderer.dispose();
    expect(geometryDisposal).toHaveBeenCalledOnce();
    expect(materialDisposal).toHaveBeenCalledOnce();
  });
  it("draws only cells within the clump distance", () => {
    const { placements } = planGrassClumps(plan(), OPTIONS);
    const renderer = createGrassClumps(placements, createVegetationWindUniforms(), { groundY: 0 });
    const camera = new THREE.PerspectiveCamera();
    camera.position.set(placements[0]!.x, 2, placements[0]!.z); camera.updateMatrixWorld();
    expect(renderer.update(camera)).toBeGreaterThan(0);
    camera.position.set(1e5, 2, 1e5); camera.updateMatrixWorld();
    expect(renderer.update(camera)).toBe(0);
    expect(GRASS_CLUMP_MAX_DISTANCE_M).toBeGreaterThan(0);
    renderer.dispose();
  });
  it("rejects an empty placement list and an invalid ground height", () => {
    expect(() => createGrassClumps([], createVegetationWindUniforms(), { groundY: 0 })).toThrow(/placement/);
    const { placements } = planGrassClumps(plan(), OPTIONS);
    expect(() => createGrassClumps(placements, createVegetationWindUniforms(), { groundY: NaN })).toThrow(/ground/);
  });
});
