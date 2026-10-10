// @vitest-environment node
import { readFileSync } from "node:fs";
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { BIGCITY_ENVIRONMENT_TREES, CITY_ENVIRONMENT_DEFAULTS, createCityEnvironment,
  environmentDiskIntersects, generateCityEnvironment, measureEnvironmentStations,
  measureInstancedEnvironmentStations, parseCityEnvironmentSource,
  type CityEnvironmentAssets, type CityEnvironmentConfig, type CityEnvironmentInput,
  type CityEnvironmentSource, type EnvironmentGreen, type EnvironmentPolygon,
  type EnvironmentTreeAsset } from "./city-environment";
import { terrainSurfacesForPlan } from "./testing/terrain-surface-fixture";

const sha = "a".repeat(64);
function rect(x1: number, z1: number, x2: number, z2: number): EnvironmentPolygon {
  return { outline: [[x1, z1], [x2, z1], [x2, z2], [x1, z2]], holes: [] };
}
function green(polygon: EnvironmentPolygon, id = "green", tags: Record<string, string> = { landuse: "grass" }): EnvironmentGreen {
  return { ...polygon, id, provenance: { kind: "osm", sourceSha256: sha, elementType: "way",
    elementId: "900719925474099300", sourceElementId: "123", sourceElementType: "way", tags } };
}
const testAsset: EnvironmentTreeAsset = { id: "metered-tree", title: "Contract test geometry",
  modelUrl: "/fixture.fbx", heightM: 4, widthM: 2, depthM: 2, sourceHeight: 100 };
function config(overrides: Partial<CityEnvironmentConfig> = {}): CityEnvironmentConfig {
  return { ...CITY_ENVIRONMENT_DEFAULTS, species: [testAsset.id], density: 1,
    spacingM: 5, scaleRange: [1, 1], ...overrides };
}
function input(overrides: Partial<CityEnvironmentInput> = {}): CityEnvironmentInput {
  return { geometryId: "loaded-contract-geometry", roadGeometry: "published", roadbed: [rect(-40, -40, -35, 40)],
    walkbed: [rect(-35, -40, -33, 40)], buildings: [{ ...rect(35, 35, 38, 38), id: "building" }],
    crossings: [], junctions: [], lamps: [], signals: [], extent: rect(-50, -50, 50, 50),
    greens: [green(rect(-20, -20, 20, 20))], sourceTrees: [], ...overrides };
}
const plan = (data: CityEnvironmentInput, settings = config()) => generateCityEnvironment(data, settings, [testAsset]);
const source = JSON.parse(readFileSync("public/city-presentation/shanghai-source-environment-v1.json", "utf8")) as CityEnvironmentSource;

describe("city vegetation source authority", () => {
  it("keeps all 414 raw building footprints and the inspected green provenance", () => {
    const parsed = parseCityEnvironmentSource(source, source.source);
    expect(parsed.buildings).toHaveLength(414);
    expect(parsed.greens).toHaveLength(8);
    expect(parsed.inspection.taggedAreaCount).toBe(33);
    expect(parsed.greens.filter(g => g.provenance.kind === "osm" && g.provenance.tags.natural === "wood")).toHaveLength(1);
    expect(parsed.sourceTrees).toEqual([]);
    expect(parsed.source.osmSha256).toBe("6948a5a2611145a4c3f4283d756d580fbd8155b049379de91d80bccd8bc2465b");
    expect(parsed.source.objectsSha256).toBe("c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc");
    expect(parsed.buildings.some(b => b.outline.length > 20)).toBe(true);
  });
  it.each(["osmSha256", "objectsSha256"] as const)("rejects %s authority drift", key => {
    expect(() => parseCityEnvironmentSource(source, { ...source.source, [key]: sha })).toThrow(/authority/);
  });
  it("rejects origin drift and forged per-green source pins", () => {
    expect(() => parseCityEnvironmentSource(source, { ...source.source,
      origin: { ...source.source.origin, longitude_deg: 122 } })).toThrow(/authority/);
    const changed = structuredClone(source);
    (changed.greens[0]!.provenance as { sourceSha256: string }).sourceSha256 = sha;
    expect(() => parseCityEnvironmentSource(changed, source.source)).toThrow(/provenance/);
  });
  it("supports every supplied Tree 01 through Tree 12 as a selectable metered asset", () => {
    expect(BIGCITY_ENVIRONMENT_TREES).toHaveLength(12);
    expect(BIGCITY_ENVIRONMENT_TREES.map(a => a.title)).toEqual(Array.from({ length: 12 }, (_, i) => `Tree ${String(i + 1).padStart(2, "0")}`));
    expect(new Set(BIGCITY_ENVIRONMENT_TREES.map(a => a.modelUrl)).size).toBe(12);
  });
});

describe("deterministic geometry based city vegetation", () => {
  it("records design parameters, repeats placements, and leaves source inputs intact", () => {
    const data = input(), before = JSON.stringify(data), a = plan(data), b = plan(data);
    expect(a).toEqual(b); expect(a.trees.length).toBeGreaterThan(20);
    expect(a.config.seed).toBe(CITY_ENVIRONMENT_DEFAULTS.seed);
    expect(a.trees.every(tree => tree.provenance.kind === "derived-osm-green")).toBe(true);
    expect(JSON.stringify(data)).toBe(before);
    expect(plan(data, config({ seed: 5 })).trees).not.toEqual(a.trees);
  });
  it("requires explicit missing observations and never paints all vacant ground", () => {
    const absent = plan(input({ greens: null, sourceTrees: null }));
    expect(absent.grass).toEqual([]); expect(absent.trees).toEqual([]);
    expect(absent.missing).toEqual(["source-green-input-unavailable", "source-tree-input-unavailable"]);
    const inspected = plan(input({ greens: [], sourceTrees: [] }));
    expect(inspected.missing).toEqual(["source-has-no-tagged-greens", "source-has-no-tagged-tree-nodes"]);
    expect(() => plan({ ...input(), greens: undefined } as unknown as CityEnvironmentInput)).toThrow(/explicit/);
  });
  it("keeps the complete crown clear of each hard geometry type", () => {
    const candidates = [[0, 0], [10, 0], [20, 0], [0, 15], [15, 15], [-15, 15], [-15, -15], [10, -15]] as const;
    const data = input({ roadbed: [rect(-1, -2, 1, 2)], walkbed: [rect(9, -2, 11, 2)],
      buildings: [{ ...rect(19, -2, 21, 2), id: "real-building" }],
      crossings: [{ id: "crossing", shape: [[-2, 15], [2, 15]], width: 2 }],
      junctions: [{ ...rect(14, 14, 16, 16), id: "junction" }],
      lamps: [{ id: "lamp-full-arm", x: -15, z: 15, radiusM: 1, groundRadiusM: 1 }],
      signals: [{ id: "signal-full-head", x: -15, z: -15, radiusM: 1, groundRadiusM: 1 }], greens: [],
      sourceTrees: candidates.map(([x, z], i) => ({ id: String(i), x, z, sourceSha256: sha, sourceElementId: String(i) })) });
    const result = plan(data);
    expect(result.trees).toHaveLength(1); expect(result.trees[0]!.x).toBe(10);
    expect(result.trees[0]!.envelopeRadiusM).toBeCloseTo(Math.sqrt(2));
    for (const reason of ["road", "walk", "building", "crossing", "junction", "lamp", "signal"] as const) {
      expect(result.stats.rejected[reason]).toBe(1);
    }
    expect(result.stats.candidates).toBe(8);
  });
  it("uses full crown radius at a building edge, extent, green boundary, and other tree", () => {
    const result = plan(input({ greens: [], buildings: [{ ...rect(0, -10, 1, 10), id: "edge" }],
      sourceTrees: [3, 4, 49].map((x, i) => ({ id: String(i), x, z: 0, sourceSha256: sha, sourceElementId: String(i) })) }));
    expect(result.trees).toHaveLength(1);
    expect(result.stats.rejected.building).toBe(1); expect(result.stats.rejected.extent).toBe(1);
    const packed = plan(input({ greens: [], sourceTrees: [10, 11].map((x, i) => ({ id: String(i), x, z: 0,
      sourceSha256: sha, sourceElementId: String(i) })) }));
    expect(packed.stats.rejected.tree).toBe(1);
    const narrow = plan(input({ greens: [green(rect(-.5, -20, .5, 20))] }));
    expect(narrow.trees).toEqual([]); expect(narrow.stats.rejected["green-boundary"]).toBeGreaterThan(0);
  });
  it("handles holes and concave footprints without missing contact between sampled points", () => {
    const courtyard = { ...rect(0, 0, 10, 10), holes: [rect(3, 3, 7, 7).outline] };
    expect(environmentDiskIntersects([5, 5], 1, courtyard)).toBe(false);
    expect(environmentDiskIntersects([5, 5], 2.01, courtyard)).toBe(true);
    const concave = { outline: [[0, 0], [10, 0], [10, 2], [2, 2], [2, 10], [0, 10]] as const, holes: [] };
    expect(environmentDiskIntersects([5, 5], 2, concave)).toBe(false);
    expect(environmentDiskIntersects([5, 5], 3.1, concave)).toBe(true);
  });
  it("clips grass at source holes, road, walk, buildings, and the exact extent", () => {
    const labelled = green({ ...rect(-2, -2, 12, 12), holes: [rect(8, 8, 9, 9).outline] });
    const data = input({ greens: [labelled], extent: rect(0, 0, 10, 10),
      roadbed: [rect(0, 0, 2, 10)], walkbed: [rect(2, 0, 3, 10)],
      buildings: [{ ...rect(4, 4, 6, 6), id: "building" }] });
    const result = plan(data, config({ greenTrees: false }));
    expect(result.stats.grassAreaM2).toBeCloseTo(65, 6);
    expect(result.grass[0]!.sourceAreaM2).toBeCloseTo(195, 6);
    expect(result.stats.grassRemovedAreaM2).toBeCloseTo(130, 6);
    for (const tri of result.grass[0]!.triangles) {
      for (const [x, z] of tri) { expect(x).toBeGreaterThanOrEqual(3 - 1e-7); expect(x).toBeLessThanOrEqual(10 + 1e-7);
        expect(z).toBeGreaterThanOrEqual(-1e-7); expect(z).toBeLessThanOrEqual(10 + 1e-7); }
      const center = [tri.reduce((sum, p) => sum + p[0], 0) / 3, tri.reduce((sum, p) => sum + p[1], 0) / 3] as const;
      expect(environmentDiskIntersects(center, 0, data.buildings[0]!)).toBe(false);
      expect(environmentDiskIntersects(center, 0, rect(8, 8, 9, 9))).toBe(false);
    }
  });
  it("excludes signal and lamp assemblies, crossings, and junction polygons from grass", () => {
    const result = plan(input({ greens: [green(rect(0, 0, 20, 20))],
      lamps: [{ id: "lamp", x: 10, z: 10, radiusM: 2, groundRadiusM: 2 }], signals: [{ id: "signal", x: 4, z: 4, radiusM: 1, groundRadiusM: 1 }],
      crossings: [{ id: "crossing", shape: [[0, 18], [20, 18]], width: 2 }],
      junctions: [{ ...rect(0, 0, 2, 2), id: "junction" }] }), config({ greenTrees: false }));
    expect(result.stats.grassAreaM2).toBeLessThan(400 - 40 - 4 - Math.PI * 5);
    for (const tri of result.grass[0]!.triangles) {
      const p: readonly [number, number] = [tri.reduce((sum, a) => sum + a[0], 0) / 3,
        tri.reduce((sum, a) => sum + a[1], 0) / 3];
      expect(Math.hypot(p[0] - 10, p[1] - 10)).toBeGreaterThan(2 - 1e-7);
      expect(Math.hypot(p[0] - 4, p[1] - 4)).toBeGreaterThan(1 - 1e-7);
    }
  });
  it("keeps lawn under an overhead arm while trees still clear the full assembly", () => {
    // Pole on the ground (0.3 m) under a 4 m arm: the lawn keeps the ground under the arm.
    const lamps = [{ id: "lamp", x: 10, z: 10, radiusM: 4, groundRadiusM: 0.3 }];
    const result = plan(input({ greens: [green(rect(0, 0, 20, 20))], lamps,
      sourceTrees: [{ id: "under-arm", x: 12, z: 10, sourceSha256: sha, sourceElementId: "t" }] }),
    config({ greenTrees: false }));
    expect(result.stats.grassAreaM2).toBeGreaterThan(400 - Math.PI * 0.35 ** 2 - 1e-6);
    expect(result.stats.grassAreaM2).toBeLessThan(400 - Math.PI * 0.3 ** 2 + 1e-6);
    expect(result.trees).toEqual([]);
    expect(() => plan(input({ lamps: [{ id: "bad", x: 0, z: 0, radiusM: 1, groundRadiusM: 2 }] })))
      .toThrow(/station envelope is invalid/);
  });
  it("deduplicates overlapping green ground surfaces and leaves woodland without lawn", () => {
    const result = plan(input({ greens: [green(rect(0, 0, 10, 10), "a"), green(rect(5, 0, 15, 10), "b"),
      green(rect(-20, -20, -5, -5), "wood", { natural: "wood" })] }));
    expect(result.stats.grassAreaM2).toBeCloseTo(150, 6);
    expect(result.grass.map(g => g.id)).toEqual(["a", "b"]);
    expect(result.trees.some(t => t.provenance.kind === "derived-osm-green" && t.provenance.greenId === "wood")).toBe(true);
    expect(() => plan(input({ greens: [green(rect(0, 0, 10, 10), "retail", { landuse: "retail" })] }))).toThrow(/Unmarked/);
  });
  it("draws woodland as a separate woodland floor in the ground left by lawn, never as lawn", () => {
    const lawnOnly = plan(input({ greens: [green(rect(0, 0, 10, 10), "a")] }));
    const result = plan(input({ greens: [green(rect(0, 0, 10, 10), "a"),
      green(rect(5, 5, 20, 20), "a-wood-overlapping", { natural: "wood" })] }));
    expect(result.grass.map(g => g.id)).toEqual(["a"]);
    expect(result.stats.grassAreaM2).toBeCloseTo(lawnOnly.stats.grassAreaM2, 6);
    expect(result.woodlandFloor.map(g => g.id)).toEqual(["a-wood-overlapping"]);
    // 15 x 15 wood minus the 5 x 5 lawn overlap; the wood sorts first by id but is clipped last.
    expect(result.stats.woodlandFloorAreaM2).toBeCloseTo(200, 6);
    expect(result.woodlandFloor[0]!.sourceAreaM2).toBeCloseTo(225, 6);
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset,
      parts: [{ near: new THREE.BoxGeometry(2, 4, 2), far: new THREE.BoxGeometry(1.5, 4, 1.5),
        material: new THREE.MeshStandardMaterial() }] }]]), dispose: vi.fn() };
    const surfaces = terrainSurfacesForPlan(result);
    const renderer = createCityEnvironment(result, assets, surfaces);
    const floor = renderer.group.children.find(g => g.name === "Woodland floor clipped to labelled woodland") as THREE.Mesh;
    const lawn = renderer.group.children.find(g => g.name === "Grass clipped to labelled green areas") as THREE.Mesh;
    expect(floor.userData).toMatchObject({ terrainPreset: "woodland-floor", greenIds: ["a-wood-overlapping"] });
    expect(lawn.userData).toMatchObject({ terrainPreset: "lawn-maintained", greenIds: ["a"] });
    expect(floor.material).not.toBe(lawn.material);
    expect(renderer.group.userData.woodlandFloorAreaM2).toBeCloseTo(200, 6);
    renderer.dispose(); surfaces.kit.dispose();
  });
  it("plants a small wood on the closed-canopy lattice that the park lattice would leave empty", () => {
    // A 12 m wood between 14 m lattice lines: no park lattice point keeps a ~8.5 m crown inside.
    const wood = green(rect(15, 15, 27, 27), "small-wood", { natural: "wood" });
    const park = green(rect(15, 15, 27, 27), "small-park");
    const production = (greens: EnvironmentGreen[]) => generateCityEnvironment(input({ greens }),
      config({ spacingM: 14 }), [{ ...testAsset, widthM: 6, depthM: 6 }]);
    expect(production([park]).trees).toEqual([]);
    const trees = production([wood]).trees;
    expect(trees.length).toBeGreaterThan(0);
    for (const tree of trees) {
      expect(tree.provenance).toMatchObject({ kind: "derived-osm-green", greenId: "small-wood" });
      expect(tree.x - tree.envelopeRadiusM).toBeGreaterThanOrEqual(15);
      expect(tree.x + tree.envelopeRadiusM).toBeLessThanOrEqual(27);
    }
  });
  it("places authored street trees only with an explicit design and loaded clearance geometry", () => {
    const data = input({ greens: [], roadbed: [rect(-40, -10, 40, -5)], walkbed: [rect(-40, -5, 40, -3)] });
    expect(plan(data).trees).toEqual([]);
    const result = plan(data, config({ streetTrees: { kind: "authored", designId: "test-street-design", maxRoadDistanceM: 15 } }));
    expect(result.trees.length).toBeGreaterThan(0); expect(result.grass).toEqual([]);
    expect(result.trees.every(t => t.provenance.kind === "authored-street-tree" && t.provenance.designId === "test-street-design")).toBe(true);
    for (const t of result.trees) for (const obstacle of [...data.roadbed, ...data.walkbed]) {
      expect(environmentDiskIntersects([t.x, t.z], t.envelopeRadiusM, obstacle)).toBe(false);
    }
  });
  it.each([{ density: 2 }, { seed: NaN }, { scaleRange: [2, 1] as const }, { spacingM: 0 }])("rejects invalid configuration %j", bad => {
    expect(() => plan(input(), config(bad))).toThrow(/invalid/);
  });
});

describe("shared vegetation instances and measured fixture bounds", () => {
  it("measures all physical parts per instanced lamp without covering the whole street", () => {
    const ids = ["lamp-a", "lamp-b"], pole = new THREE.InstancedMesh(new THREE.BoxGeometry(.4, 8, .4), new THREE.MeshBasicMaterial(), 2);
    const arm = new THREE.InstancedMesh(new THREE.BoxGeometry(4, .3, .5), new THREE.MeshBasicMaterial(), 2);
    for (let i = 0; i < 2; i++) { pole.setMatrixAt(i, new THREE.Matrix4().makeTranslation(i * 50, 4, 0));
      arm.setMatrixAt(i, new THREE.Matrix4().makeTranslation(i * 50 + 1.5, 7.8, 0)); }
    const bounds = measureInstancedEnvironmentStations([pole, arm], ids);
    expect(bounds[0]!.radiusM).toBeGreaterThan(2); expect(bounds[0]!.radiusM).toBeLessThan(3);
    expect(bounds[1]!.x - bounds[0]!.x).toBeCloseTo(50);
    expect(() => measureInstancedEnvironmentStations([pole], ["one"])).toThrow(/inventory/);
    expect(() => measureInstancedEnvironmentStations([pole], [])).toThrow(/inventory/);
  });
  it("includes a signal arm and rejects missing fixture geometry", () => {
    const signal = new THREE.Group(); signal.add(new THREE.Mesh(new THREE.BoxGeometry(.4, 5, .4), new THREE.MeshBasicMaterial()));
    const arm = new THREE.Mesh(new THREE.BoxGeometry(4, .2, .3), new THREE.MeshBasicMaterial()); arm.position.set(1.8, 2.5, 0); signal.add(arm);
    expect(measureEnvironmentStations([{ id: "signal", object: signal }])[0]!.radiusM).toBeGreaterThan(2);
    expect(() => measureEnvironmentStations([{ id: "missing", object: new THREE.Group() }])).toThrow(/missing/);
  });
  it("shares geometry and material across instances, swaps LOD, and releases owned resources", () => {
    const result = plan(input()), near = new THREE.BoxGeometry(2, 4, 2), far = new THREE.BoxGeometry(1.5, 4, 1.5);
    const material = new THREE.MeshStandardMaterial();
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset,
      parts: [{ near, far, material }] }]]), dispose: vi.fn() };
    const nearDisposal = vi.spyOn(near, "dispose"), materialDisposal = vi.spyOn(material, "dispose");
    const surfaces = terrainSurfacesForPlan(result);
    const renderer = createCityEnvironment(result, assets, surfaces, { cellSizeM: 80, nearDistanceM: 50, maxDistanceM: 200 });
    const instances: THREE.InstancedMesh[] = [];
    renderer.group.traverse(o => { if (o instanceof THREE.InstancedMesh) instances.push(o); });
    expect(instances.every(mesh => mesh.material === material && (mesh.geometry === near || mesh.geometry === far))).toBe(true);
    expect(instances.reduce((sum, mesh) => sum + mesh.instanceMatrix.count, 0)).toBe(result.trees.length * 2);
    const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 2, 0); renderer.updateLod(camera);
    expect(renderer.group.children.filter(g => g.name.endsWith(":near")).every(g => g.visible)).toBe(true);
    camera.position.set(0, 2, 120); renderer.updateLod(camera);
    expect(instances.filter(mesh => mesh.name.includes(":far:")).some(mesh => mesh.visible && mesh.count > 0)).toBe(true);
    camera.position.set(0, 2, 1000); renderer.updateLod(camera);
    expect(renderer.group.children.filter(g => g.name.endsWith(":near") || g.name.includes(":far:")).every(g => !g.visible)).toBe(true);
    const grass = renderer.group.children.find(g => g.name === "Grass clipped to labelled green areas") as THREE.Mesh;
    expect(grass.geometry.getAttribute("normal").getY(0)).toBeGreaterThan(.99);
    expect(grass.material).toBe(surfaces.kit.material(surfaces.assignments.get(result.grass[0]!.id)!));
    expect(grass.userData.terrainPreset).toBe("lawn-maintained");
    expect(grass.geometry.getAttribute("uv")).toBeDefined(); expect(grass.geometry.getAttribute("color")).toBeDefined();
    const grassDisposal = vi.spyOn(grass.geometry, "dispose"), kitDisposal = vi.spyOn(grass.material as THREE.Material, "dispose");
    renderer.dispose();
    expect(renderer.group.children).toEqual([]); expect(grassDisposal).toHaveBeenCalledOnce();
    expect(nearDisposal).not.toHaveBeenCalled(); expect(materialDisposal).not.toHaveBeenCalled();
    expect(kitDisposal).not.toHaveBeenCalled(); expect(assets.dispose).not.toHaveBeenCalled();
    surfaces.kit.dispose();
  });
  it("packs visible far instances across cells while preserving near cells and shadow casters", () => {
    const generated = plan(input()), trees = [generated.trees[0]!, generated.trees[0]!].map((tree, i) => ({
      ...tree, id: `tree-${i}`, x: i * 100, z: 0,
    }));
    const result = { ...generated, trees }, near = new THREE.BoxGeometry(2, 4, 2), far = new THREE.BoxGeometry(2, 4, 2);
    const material = new THREE.MeshStandardMaterial();
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset, parts: [{ near, far, material }] }]]),
      dispose: vi.fn() };
    const renderer = createCityEnvironment(result, assets, terrainSurfacesForPlan(result), { cellSizeM: 80, nearDistanceM: 30, maxDistanceM: 500 });
    const distant = renderer.group.children.find(o => o instanceof THREE.InstancedMesh && !o.castShadow) as THREE.InstancedMesh;
    const camera = new THREE.PerspectiveCamera(70, 2, .1, 1000); camera.position.set(50, 2, 150); camera.lookAt(50, 2, 0);
    renderer.updateLod(camera);
    expect(distant.count).toBe(2); expect(distant.geometry).toBe(far); expect(distant.castShadow).toBe(false);
    const matrix = new THREE.Matrix4(); distant.getMatrixAt(0, matrix); expect(matrix.elements[12]).toBeCloseTo(0);
    distant.getMatrixAt(1, matrix); expect(matrix.elements[12]).toBeCloseTo(100);
    camera.lookAt(50, 2, 300); renderer.updateLod(camera); expect(distant.count).toBe(0); expect(distant.visible).toBe(false);
    camera.position.set(0, 2, 0); camera.lookAt(0, 2, -100); renderer.updateLod(camera);
    const nearby = renderer.group.children.find(g => g.name === `${testAsset.id}:0:0:near`) as THREE.Group;
    expect(nearby.visible).toBe(true); expect((nearby.children[0] as THREE.InstancedMesh).castShadow).toBe(true);
    renderer.dispose();
  });
});

describe("city environment road geometry status", () => {
  it("carries the published status into the plan and the renderer group", () => {
    const result = plan(input());
    expect(result.roadGeometry).toBe("published");
    expect(result.missing).not.toContain("road-clearance-pending-native-gate");
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset,
      parts: [{ near: new THREE.BoxGeometry(2, 4, 2), far: new THREE.BoxGeometry(1.5, 4, 1.5),
        material: new THREE.MeshStandardMaterial() }] }]]),
      dispose: vi.fn() };
    const renderer = createCityEnvironment(result, assets, terrainSurfacesForPlan(result));
    expect(renderer.group.userData.environmentRoadGeometry).toBe("published");
    renderer.dispose();
  });
  it("marks pending-native-gate in missing and keeps it through the renderer", () => {
    const result = plan(input({ roadGeometry: "pending-native-gate", roadbed: [], walkbed: [] }));
    expect(result.missing).toContain("road-clearance-pending-native-gate");
    expect(result.roadGeometry).toBe("pending-native-gate");
    expect(result.stats.rejected.road).toBe(0);
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset,
      parts: [{ near: new THREE.BoxGeometry(2, 4, 2), far: new THREE.BoxGeometry(1.5, 4, 1.5),
        material: new THREE.MeshStandardMaterial() }] }]]),
      dispose: vi.fn() };
    const renderer = createCityEnvironment(result, assets, terrainSurfacesForPlan(result));
    expect(renderer.group.userData.environmentRoadGeometry).toBe("pending-native-gate");
    renderer.dispose();
  });
  it.each([
    ["non-empty roadbed", { roadbed: [rect(-40, -40, -35, 40)] }, {}],
    ["non-empty walkbed", { walkbed: [rect(-35, -40, -33, 40)] }, {}],
    ["non-empty crossings", { crossings: [{ id: "crossing", shape: [[-2, 15], [2, 15]], width: 2 }] }, {}],
    ["non-empty junctions", { junctions: [{ ...rect(14, 14, 16, 16), id: "junction" }] }, {}],
    ["authored street trees", {}, { streetTrees: { kind: "authored" as const, designId: "d", maxRoadDistanceM: 10 } }],
  ] as const)("rejects pending-native-gate with %s", (_label, overrides, configOverrides) => {
    expect(() => plan(input({ roadGeometry: "pending-native-gate", roadbed: [], walkbed: [],
      crossings: [], junctions: [], ...overrides }), config(configOverrides))).toThrow(/pending-native-gate/);
  });
  it("rejects an unknown roadGeometry status instead of guessing", () => {
    expect(() => plan({ ...input(), roadGeometry: "claimed" } as unknown as CityEnvironmentInput))
      .toThrow(/roadGeometry/);
    expect(() => plan(input({ roadGeometry: undefined } as unknown as Partial<CityEnvironmentInput>)))
      .toThrow(/roadGeometry/);
  });
  it("still requires a published plan to carry real roadbed geometry", () => {
    expect(() => plan(input({ roadGeometry: "published", roadbed: [] }))).toThrow(/roadbed/);
  });
});

describe("construction failure and ground-role labelling", () => {
  it.each(["computeBoundingBox", "computeBoundingSphere"] as const)(
    "disposes every created far and near instance when near %s throws", boundsMethod => {
      const generated = plan(input());
      const result = { ...generated, trees: generated.trees.slice(0, 1), grass: [], woodlandFloor: [] };
      const parts = [0, 1].map(() => ({ near: new THREE.BoxGeometry(2, 4, 2),
        far: new THREE.BoxGeometry(1.5, 4, 1.5), material: new THREE.MeshStandardMaterial() }));
      const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset, parts }]]), dispose: vi.fn() };
      const surfaces = terrainSurfacesForPlan(result);
      const created = new Set<THREE.InstancedMesh>(), disposed: THREE.InstancedMesh[] = [];
      const add = THREE.Object3D.prototype.add;
      const attachment = vi.spyOn(THREE.Object3D.prototype, "add").mockImplementation(function(this: THREE.Object3D, ...objects) {
        objects.forEach(object => { if (object instanceof THREE.InstancedMesh) created.add(object); });
        return add.apply(this, objects);
      });
      const setMatrixAt = THREE.InstancedMesh.prototype.setMatrixAt;
      const fill = vi.spyOn(THREE.InstancedMesh.prototype, "setMatrixAt").mockImplementation(function(this: THREE.InstancedMesh, index, matrix) {
        created.add(this);
        return setMatrixAt.call(this, index, matrix);
      });
      const failure = new Error(`injected near ${boundsMethod} failure`);
      const computeBounds = THREE.InstancedMesh.prototype[boundsMethod];
      const bounds = vi.spyOn(THREE.InstancedMesh.prototype, boundsMethod).mockImplementation(function(this: THREE.InstancedMesh) {
        if (this.geometry === parts[1]!.near) throw failure;
        return computeBounds.call(this);
      });
      const dispose = THREE.InstancedMesh.prototype.dispose;
      const disposal = vi.spyOn(THREE.InstancedMesh.prototype, "dispose").mockImplementation(function(this: THREE.InstancedMesh) {
        disposed.push(this);
        return dispose.call(this);
      });
      const sharedDisposals = parts.flatMap(part => [vi.spyOn(part.near, "dispose"),
        vi.spyOn(part.far, "dispose"), vi.spyOn(part.material, "dispose")]);
      try {
        let caught: unknown;
        try { createCityEnvironment(result, assets, surfaces); } catch (error) { caught = error; }
        expect(caught).toBe(failure);
        expect(created.size).toBe(4);
        expect([...created].filter(mesh => mesh.geometry === parts[0]!.far || mesh.geometry === parts[1]!.far)).toHaveLength(2);
        for (const mesh of created) expect(disposed.filter(item => item === mesh)).toHaveLength(1);
        expect(disposed).toHaveLength(created.size);
        sharedDisposals.forEach(spy => expect(spy).not.toHaveBeenCalled());
        expect(assets.dispose).not.toHaveBeenCalled();
      } finally {
        attachment.mockRestore(); fill.mockRestore(); bounds.mockRestore(); disposal.mockRestore();
        sharedDisposals.forEach(spy => spy.mockRestore());
        surfaces.kit.dispose();
        parts.forEach(part => { part.near.dispose(); part.far.dispose(); part.material.dispose(); });
      }
    });
  it("labels lawn and woodland-floor meshes with the plan list they came from", () => {
    // Tags whose physical preset contradicts the planner category: the planner files
    // `natural=wood` under the woodland floor, but the explicit surface gives the asphalt
    // preset; `leisure=park` stays a lawn patch while `landuse=forest` gives it the
    // woodland-floor preset. The mesh role must follow the plan list, not the preset.
    const result = plan(input({ greens: [
      green(rect(0, 0, 10, 10), "wood-asphalt", { natural: "wood", surface: "asphalt" }),
      green(rect(5, 5, 20, 20), "park-forest", { leisure: "park", landuse: "forest" }),
    ] }), config({ greenTrees: false }));
    expect(result.grass.map(g => g.id)).toEqual(["park-forest"]);
    expect(result.woodlandFloor.map(g => g.id)).toEqual(["wood-asphalt"]);
    const assets: CityEnvironmentAssets = { trees: new Map(), dispose: vi.fn() };
    const renderer = createCityEnvironment(result, assets, terrainSurfacesForPlan(result));
    const lawn = renderer.group.children.find(g => g.userData.groundRole === "lawn") as THREE.Mesh;
    const floor = renderer.group.children.find(g => g.userData.groundRole === "woodland-floor") as THREE.Mesh;
    expect(lawn.userData.greenIds).toEqual(["park-forest"]);
    expect(lawn.userData.terrainPreset).toBe("woodland-floor");
    expect(lawn.name).toBe("Green-area ground woodland-floor clipped to labelled green areas");
    expect(floor.userData.greenIds).toEqual(["wood-asphalt"]);
    expect(floor.userData.terrainPreset).toBe("asphalt");
    expect(floor.name).toBe("Woodland floor clipped to labelled woodland");
    renderer.dispose();
  });
  it("disposes every instance buffer and ground geometry exactly once when construction throws", () => {
    const result = plan(input({ greens: [green(rect(0, 0, 10, 10), "a"),
      green(rect(-20, -20, -10, -10), "wood", { natural: "wood" })] }));
    const near = new THREE.BoxGeometry(2, 4, 2), far = new THREE.BoxGeometry(1.5, 4, 1.5);
    const assets: CityEnvironmentAssets = { trees: new Map([[testAsset.id, { asset: testAsset,
      parts: [{ near, far, material: new THREE.MeshStandardMaterial() }] }]]), dispose: vi.fn() };
    const surfaces = terrainSurfacesForPlan(result);
    // The lawn geometry is built first; the woodland lookup then fails, as in the
    // verifier's outer-loader probe (kit material lookup throws after geometry exists).
    const kit = surfaces.kit;
    const realMaterial = kit.material.bind(kit);
    const brokenKit = new Proxy(kit, { get(target, property, receiver) {
      if (property === "material") {
        return (assignment: { preset: string }) => assignment.preset === "woodland-floor"
          ? (() => { throw new Error("injected woodland material failure"); })()
          : realMaterial(assignment as never);
      }
      return Reflect.get(target, property, receiver);
    } });
    const instanceDisposals: string[] = [], geometryDisposals: string[] = [];
    const disposeMesh = THREE.InstancedMesh.prototype.dispose;
    vi.spyOn(THREE.InstancedMesh.prototype, "dispose").mockImplementation(function(this: THREE.InstancedMesh) {
      instanceDisposals.push(this.uuid); return disposeMesh.call(this);
    });
    const disposeGeometry = THREE.BufferGeometry.prototype.dispose;
    vi.spyOn(THREE.BufferGeometry.prototype, "dispose").mockImplementation(function(this: THREE.BufferGeometry) {
      geometryDisposals.push(this.uuid); return disposeGeometry.call(this);
    });
    try {
      expect(() => createCityEnvironment(result, assets, { kit: brokenKit, assignments: surfaces.assignments }))
        .toThrow(/injected woodland material failure/);
    } finally {
      vi.restoreAllMocks();
    }
    // Far and near instance buffers were created before the throw and are disposed once;
    // the lawn geometry created before the throw is disposed once.
    expect(instanceDisposals.length).toBeGreaterThan(0);
    expect(new Set(instanceDisposals).size).toBe(instanceDisposals.length);
    expect(geometryDisposals.length).toBe(1);
    surfaces.kit.dispose();
  });
});
