// @vitest-environment node
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import * as THREE from "three";
import { describe, expect, it } from "vitest";
import { BIGCITY_ENVIRONMENT_TREES, CITY_ENVIRONMENT_DEFAULTS, environmentDiskIntersects,
  generateCityEnvironment, parseCityEnvironmentSource, type CityEnvironmentAssets,
  type CityEnvironmentPlan, type CityEnvironmentSource, type EnvironmentPolygon,
  type EnvironmentTreeAsset } from "./city-environment";
import { type CityGroundCover, type CityGroundCoverKind } from "./city-ground-cover";
import { validateCanonicalCityRoadPayload } from "./city-roads";
import { planGrassClumps, type GrassClumpPlacement, type GrassClumpRejections } from "./city-grass-clumps";
import { displaySurfaceSha256 } from "./city-surface-identity";
import { cityVegetationInput } from "./city-vegetation-binding";
import { loadCityVegetationLayer, cityVegetationRoadBinding, CITY_VEGETATION_CLUMP_DEFAULTS,
  CITY_VEGETATION_CLUMP_DESIGN_ID, type CityVegetationEffectiveFixture,
  type CityVegetationLayerInput, type CityVegetationRoadBinding } from "./city-vegetation-layer";
import type { TerrainTransitionStats } from "./city-terrain-transitions";
import { CITY_WEATHER_PRESETS, CITY_WEATHER_CLEAR } from "./city-weather";
import { fakeTerrainTextureSets } from "./testing/terrain-surface-fixture";
import { loadVerifiedTerrainTextureSets, terrainTextureSetIds } from "./city-terrain-surfaces";
import { authoredLandscapeMaterial } from "./city-authored-landscape";
import type { GroundMaterialAssignment } from "./city-ground-material-rules";

const OSM_SHA = "b".repeat(64), OBJECTS_SHA = "c".repeat(64), BUILDING_SHA = "d".repeat(64);
const ORIGIN = { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50 };
const AUTHORITY = { osmSha256: OSM_SHA, objectsSha256: OBJECTS_SHA, origin: ORIGIN };

type LayerSource = Omit<CityEnvironmentSource, "inspection"> & {
  readonly groundCovers: readonly CityGroundCover[];
  readonly inspection: CityEnvironmentSource["inspection"] & {
    readonly groundCoverCount: number;
    readonly groundCoverCountsByKind: Readonly<Partial<Record<CityGroundCoverKind, number>>>;
    readonly groundCoverSourceAreaM2ByKind: Readonly<Partial<Record<CityGroundCoverKind, number>>>;
  };
};

function rect(x1: number, z1: number, x2: number, z2: number): EnvironmentPolygon {
  return { outline: [[x1, z1], [x2, z1], [x2, z2], [x1, z2]], holes: [] };
}
function groundCover(id: number, kind: CityGroundCoverKind, tags: Readonly<Record<string, string>>,
    polygon: EnvironmentPolygon): CityGroundCover {
  return { ...polygon, id: `osm:way:${id}:0`, kind, surface: tags.surface ?? null,
    provenance: { kind: "osm", sourceSha256: OSM_SHA, elementType: "way", elementId: String(id),
      sourceElementType: "way", sourceElementId: String(id), tags } };
}

function fixtureSource(): LayerSource {
  return {
    schemaVersion: "aero-bench.city-environment-source/v1",
    coordinateFrame: "x-east,y-up,z-south-meters",
    source: { osmSha256: OSM_SHA, objectsSha256: OBJECTS_SHA, projection: "WGS84->ECEF->ENU", origin: ORIGIN },
    buildings: [{ ...rect(30, 30, 34, 34), id: "bld-1", geometrySha256: BUILDING_SHA }],
    greens: [{ ...rect(-30, -30, 30, 30), id: "green-1", provenance: { kind: "osm", sourceSha256: OSM_SHA,
      elementType: "way", elementId: "1001", sourceElementId: "1001", sourceElementType: "way",
      tags: { landuse: "grass" } } }],
    groundCovers: [
      groundCover(2001, "parking", { amenity: "parking" }, rect(-70, 40, -60, 50)),
      groundCover(2002, "water", { natural: "water" }, rect(-55, 40, -45, 50)),
    ],
    sourceTrees: [],
    inspection: { taggedAreaCount: 3, greenAreaCount: 1, sourceTreeCount: 0, excludedTags: {},
      measurementStatus: "measured", groundCoverCount: 2,
      groundCoverCountsByKind: { parking: 1, water: 1 },
      groundCoverSourceAreaM2ByKind: { parking: 100, water: 100 } },
  };
}
function fakeAssets(): CityEnvironmentAssets {
  const geometries: THREE.BufferGeometry[] = [];
  const materials: THREE.Material[] = [];
  const trees = new Map<string, { asset: EnvironmentTreeAsset;
    parts: { near: THREE.BufferGeometry; far: THREE.BufferGeometry; material: THREE.MeshStandardMaterial }[] }>();
  for (const asset of BIGCITY_ENVIRONMENT_TREES.filter(a => a.id === "bigcity-tree-05" || a.id === "bigcity-tree-03")) {
    const near = new THREE.BoxGeometry(2, 4, 2), far = new THREE.BoxGeometry(1.5, 4, 1.5);
    const material = new THREE.MeshStandardMaterial();
    geometries.push(near, far); materials.push(material);
    trees.set(asset.id, { asset, parts: [{ near, far, material }] });
  }
  return { trees,
    dispose: () => { geometries.forEach(geometry => geometry.dispose()); materials.forEach(material => material.dispose()); } };
}
function encode(source: CityEnvironmentSource | LayerSource): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(source));
}
async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return createHash("sha256").update(bytes).digest("hex");
}
/** A small accepted road, placed clear of the fixture building and green so the
 * baseline vegetation assertions below are unaffected by its clearance. */
function fixtureRoadBinding(): CityVegetationRoadBinding {
  return {
    roadbed: [rect(50, -10, 60, 10)],
    walkbed: [rect(60, -10, 65, 10)],
    crossings: [{ id: "crossing-1", shape: [[70, -10], [70, 10]], width: 2 }],
    junctions: [{ id: "junction-1", ...rect(74, -4, 78, 4) }],
    lamps: [{ id: "lamp:0", x: 55, z: -12, radiusM: 1, groundRadiusM: 1 }],
    signals: [{ id: "signal:0", x: 55, z: 12, radiusM: 1.5, groundRadiusM: 1.5 }],
  };
}
async function layerInput(overrides: Partial<CityVegetationLayerInput> = {},
    source: LayerSource = fixtureSource()): Promise<CityVegetationLayerInput> {
  const bytes = encode(source);
  return {
    source: { url: "test://city-source.json", sha256: await sha256Hex(bytes), sizeBytes: bytes.byteLength },
    authority: AUTHORITY, extent: rect(-80, -80, 80, 80), roadBinding: fixtureRoadBinding(),
    fetchBytes: async () => bytes,
    loadAssets: async () => fakeAssets(),
    loadTerrain: async setIds => fakeTerrainTextureSets(setIds),
    ...overrides,
  };
}

async function loadLayer(input: CityVegetationLayerInput, surfaceSha256?: string) {
  const digest = surfaceSha256
    ?? await displaySurfaceSha256(input.roadBinding.roadbed, input.roadBinding.walkbed);
  return loadCityVegetationLayer(input, digest);
}

describe("city vegetation layer loading", () => {
  it("rejects a sha256 mismatch before parsing", async () => {
    const input = await layerInput({ source: { url: "test://city-source.json",
      sha256: "e".repeat(64), sizeBytes: encode(fixtureSource()).byteLength } });
    await expect(loadLayer(input)).rejects.toThrow(/sha256 mismatch/);
  });
  it("rejects a byte-size mismatch", async () => {
    const input = await layerInput();
    await expect(loadLayer({ ...input,
      source: { ...input.source, sizeBytes: input.source.sizeBytes + 1 } })).rejects.toThrow(/size mismatch/);
  });
  it("rejects an authority mismatch", async () => {
    const input = await layerInput({}, fixtureSource());
    await expect(loadLayer({ ...input,
      authority: { ...AUTHORITY, osmSha256: "f".repeat(64) } })).rejects.toThrow(/authority mismatch/);
  });
  it("rejects an already-aborted signal and honours an abort after the fetch", async () => {
    const input = await layerInput();
    const controller = new AbortController(); controller.abort();
    await expect(loadLayer({ ...input, signal: controller.signal })).rejects.toThrow(/aborted/);
    // A fetchBytes that aborts before returning still fails the post-fetch gate.
    const midFlight = new AbortController();
    await expect(loadLayer({ ...input,
      fetchBytes: async () => { midFlight.abort(); return encode(fixtureSource()); },
      signal: midFlight.signal })).rejects.toThrow(/aborted/);
  });
  it("refuses malformed declared digests and sizes up front", async () => {
    const input = await layerInput();
    await expect(loadLayer({ ...input,
      source: { ...input.source, sha256: "not-hex" } })).rejects.toThrow(/sha256 is invalid/);
    await expect(loadLayer({ ...input,
      source: { ...input.source, sizeBytes: 0 } })).rejects.toThrow(/sizeBytes is invalid/);
    await expect(loadCityVegetationLayer(input, "not-hex")).rejects.toThrow(/displayed surface sha256 is invalid/);
  });
  it("binds clipping to the verified displayed road and walk polygons", async () => {
    const input = await layerInput();
    await expect(loadLayer(input, "0".repeat(64))).rejects.toThrow(/differ from the verified displayed surface/);
  });
});

describe("city vegetation layer facade", () => {
  it("builds plan, renderer, clumps and summary from a verified source", async () => {
    const seenAssets: EnvironmentTreeAsset[][] = [];
    const input = await layerInput({ loadAssets: async assets => {
      seenAssets.push([...assets]); return fakeAssets(); } });
    const displayedSurfaceSha256 = await displaySurfaceSha256(input.roadBinding.roadbed, input.roadBinding.walkbed);
    const layer = await loadCityVegetationLayer(input, displayedSurfaceSha256);
    expect(seenAssets[0]).toEqual(BIGCITY_ENVIRONMENT_TREES);
    const plan = layer.group.userData.environmentPlan as CityEnvironmentPlan;
    expect(plan.roadGeometry).toBe("published");
    expect(layer.summary.missing).not.toContain("road-clearance-pending-native-gate");
    expect(layer.summary.greenCount).toBeGreaterThan(0);
    expect(layer.summary.treeCount).toBeGreaterThan(0);
    expect(layer.summary.grassAreaM2).toBeGreaterThan(0);
    expect(layer.summary.grassClumpCount).toBeGreaterThan(0);
    expect(layer.summary.sourceSha256).toBe(input.source.sha256);
    expect(layer.summary.groundCoverCount).toBe(2);
    expect(layer.summary.groundCoverAreaM2).toBeCloseTo(200, 8);
    expect(layer.summary.provenance).toEqual({ osmGreens: 1, osmGroundCovers: 2, derivedTrees: layer.summary.treeCount,
      authoredClumps: layer.summary.grassClumpCount });
    expect(layer.group.userData.environmentMissing).toEqual(layer.summary.missing);
    expect(layer.group.userData.environmentRoadGeometry).toBe("published");
    expect(layer.group.userData.vegetationSummary).toBe(layer.summary);
    expect(layer.groundCoverDrawnSet).toMatchObject({ status: "pass", omission_count: 0,
      geometry_id: displayedSurfaceSha256, parser_accepted_ids: ["osm:way:2001:0", "osm:way:2002:0"] });
    expect(layer.group.userData.groundCoverDrawnSet).toBe(layer.groundCoverDrawnSet);
    expect(layer.authoredLandscapeGeometry).toEqual({
      displayedSurfaceSha256, roadGeometry: "published", extent: input.extent,
      roadbed: input.roadBinding.roadbed, walkbed: input.roadBinding.walkbed,
      buildings: fixtureSource().buildings,
    });
    expect(layer.group.children[0]!.name).toBe("OSM source-supported ground covers");
    const firstGroundMesh = layer.group.children[0]!.children[0] as THREE.Mesh<THREE.BufferGeometry>;
    const groundPositions = firstGroundMesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    expect(Math.max(...Array.from({ length: groundPositions.count }, (_, index) => groundPositions.getY(index))))
      .toBeLessThan(plan.config.grassY);
    expect(layer.group.children.some(child => child.name === "Authored grass clumps (presentation design)")).toBe(true);
    expect(layer.group.children.some(child => child.name === "Automatic city vegetation with source and authored provenance")).toBe(true);
    // Tree materials carry the tree wind profile; the summary plan matches the renderer plan.
    const rendererGroup = layer.group.children.find(child => child.name === "Automatic city vegetation with source and authored provenance")!;
    expect(rendererGroup.userData.environmentRoadGeometry).toBe("published");
    layer.dispose();
    expect(layer.group.children.length).toBe(0);
  });
  it("applies wind to every tree material and honours weather updates", async () => {
    const assets = fakeAssets();
    const input = await layerInput({ loadAssets: async () => assets });
    const layer = await loadLayer(input);
    for (const template of assets.trees.values()) {
      for (const part of template.parts) {
        expect((part.material.userData.vegetationWind as { profile: string }).profile).toBe("tree");
        expect(part.material.customProgramCacheKey()).toBe("city-vegetation-wind-v2:tree");
      }
    }
    // The lawn carries the wet-film patch; rain raises its bound uniform, clear sky resets it.
    const vegetation = layer.group.children.find(child => child.name === "Automatic city vegetation with source and authored provenance")!;
    const lawn = vegetation.children.find(child => child.name === "Grass clipped to labelled green areas") as THREE.Mesh;
    const lawnMaterial = lawn.material as THREE.MeshStandardMaterial;
    expect(lawnMaterial.userData.surfaceWetness).toEqual({ marker: "city-surface-wetness/v2" });
    expect(lawnMaterial.customProgramCacheKey()).toBe("city-surface-wetness-v2:0.300000:0.550000|city-terrain-detail-v3:2:0.85,1");
    const shader = { uniforms: {} as Record<string, { value: number }>,
      vertexShader: THREE.ShaderLib.standard.vertexShader,
      fragmentShader: THREE.ShaderLib.standard.fragmentShader };
    lawnMaterial.onBeforeCompile(shader as never, undefined as never);
    const groundGroup = layer.group.children.find(child => child.name === "OSM source-supported ground covers")!;
    const parking = groundGroup.children.find(child => child.userData.groundCoverKind === "parking") as THREE.Mesh;
    const water = groundGroup.children.find(child => child.userData.groundCoverKind === "water") as THREE.Mesh;
    const parkingMaterial = parking.material as THREE.MeshStandardMaterial;
    const waterMaterial = water.material as THREE.MeshStandardMaterial;
    expect(parkingMaterial.userData.surfaceWetness).toEqual({ marker: "city-surface-wetness/v2" });
    expect(parkingMaterial.customProgramCacheKey()).toBe("city-surface-wetness-v2:0.400000:0.220000");
    expect(waterMaterial.userData.surfaceWetness).toBeUndefined();
    const parkingShader = { uniforms: {} as Record<string, { value: number }>,
      vertexShader: THREE.ShaderLib.standard.vertexShader,
      fragmentShader: THREE.ShaderLib.standard.fragmentShader };
    parkingMaterial.onBeforeCompile(parkingShader as never, undefined as never);
    // The water ground cover carries the T8b ripple patch and the layer's shared
    // uniforms; reading them through onBeforeCompile is the least invasive path
    // (the patch keeps no separate accessor and the uniforms are compile-time bound).
    expect(waterMaterial.userData.waterSurface).toMatchObject({ version: "city-water-surface-v2",
      presetId: "water-generic", typeResponse: 0.8 });
    const waterShader = { uniforms: {} as Record<string, { value: unknown }>,
      vertexShader: "#include <common>\n#include <project_vertex>",
      fragmentShader: "#include <common>\n#include <normal_fragment_maps>" };
    waterMaterial.onBeforeCompile!(waterShader as never, undefined as never);
    expect(waterShader.uniforms.uWaterWindMps).toBeDefined();
    const shared = waterShader.uniforms as unknown as {
      uWaterTime: { value: number }; uWaterWind: { value: THREE.Vector2 };
      uWaterWindMps: { value: number }; uWaterRain: { value: number } };
    expect(shared.uWaterWindMps.value).toBe(0);
    expect(shared.uWaterRain.value).toBe(0);
    layer.setWeather(CITY_WEATHER_PRESETS.rain);
    expect(shared.uWaterWindMps.value).toBe(CITY_WEATHER_PRESETS.rain.windMps);
    expect(shared.uWaterRain.value).toBeGreaterThan(0.5);
    expect(shared.uWaterWind.value.x).toBeCloseTo(Math.sin(CITY_WEATHER_PRESETS.rain.windDirectionDeg * Math.PI / 180), 12);
    expect(shared.uWaterWind.value.y).toBeCloseTo(-Math.cos(CITY_WEATHER_PRESETS.rain.windDirectionDeg * Math.PI / 180), 12);
    layer.update(new THREE.PerspectiveCamera(), 12.5);
    expect(shared.uWaterTime.value).toBe(12.5);
    layer.setWeather(CITY_WEATHER_CLEAR);
    expect(shared.uWaterWindMps.value).toBe(0);
    expect(shared.uWaterRain.value).toBe(0);
    layer.setWeather(CITY_WEATHER_PRESETS.rain);
    expect(shader.uniforms.uSurfaceWetness!.value).toBeGreaterThan(0.5);
    expect(parkingShader.uniforms.uSurfaceWetness).toBe(shader.uniforms.uSurfaceWetness);
    layer.setWeather(CITY_WEATHER_CLEAR);
    expect(shader.uniforms.uSurfaceWetness!.value).toBe(0);
    layer.setWeather(CITY_WEATHER_PRESETS.rain);
    const camera = new THREE.PerspectiveCamera();
    layer.update(camera, 2.5);
    layer.update(camera, 3);
    expect(() => layer.update(camera, -1)).toThrow(/time is invalid/);
    expect(() => layer.setWeather({ ...CITY_WEATHER_CLEAR, windMps: -2 })).toThrow(RangeError);
    layer.dispose();
  });
  it("assigns versioned materials, loads only the needed verified sets and releases them", async () => {
    const requested: string[][] = [];
    let textures: ReturnType<typeof fakeTerrainTextureSets> | null = null;
    const input = await layerInput({ loadTerrain: async setIds => {
      requested.push([...setIds]); textures = fakeTerrainTextureSets(setIds); return textures; } });
    const layer = await loadLayer(input);
    expect(requested).toEqual([["lawn-grass001", "mulch-ground048"]]);
    const assignments = layer.group.userData.groundMaterialAssignments as GroundMaterialAssignment[];
    expect(assignments.map(item => [item.polygonId, item.family, item.preset, item.basis]).sort()).toEqual([
      ["green-1", "grass", "lawn-maintained", "cover-tag"],
      ["osm:way:2001:0", "paving_asphalt", "parking-generic", "use-preset"],
      ["osm:way:2002:0", "water", "water-generic", "cover-tag"],
    ].sort());
    expect(assignments.every(item => item.ruleSetId === "aero-bench.ground-material-rules" && item.ruleVersion === 1)).toBe(true);
    for (const kind of ["green", "plaza", "planting_strip"] as const) {
      expect(layer.terrainSurfaceKit.material(authoredLandscapeMaterial({ id: "design", kind }))).toBeDefined();
    }
    const environment = layer.group.userData.environmentPlan as CityEnvironmentPlan;
    const covers = layer.group.userData.groundCoverPlan as import("./city-ground-cover").CityGroundCoverPlan;
    expect(layer.occupiedSourceTriangles).toEqual([...environment.grass, ...environment.woodlandFloor, ...covers.covers]
      .flatMap(patch => patch.triangles));
    expect(layer.summary.groundMaterials.count).toBe(3);
    expect(layer.summary.groundMaterials.byFamily.grass!.areaM2).toBeCloseTo(layer.summary.grassAreaM2, 6);
    expect(layer.summary.woodlandFloorAreaM2).toBe(0);
    const disposed: string[] = [];
    textures!.textures.forEach(texture => texture.addEventListener("dispose", () => { disposed.push(texture.uuid); }));
    layer.dispose();
    expect(disposed.sort()).toEqual(textures!.textures.map(texture => texture.uuid).sort());
  });
  it("releases loaded terrain textures when the layer fails after loading them", async () => {
    let textures: ReturnType<typeof fakeTerrainTextureSets> | null = null;
    const input = await layerInput({ clumpRadiusM: -1, loadTerrain: async setIds => {
      textures = fakeTerrainTextureSets(setIds); return textures; } });
    const disposed: string[] = [];
    const wrapped = { ...input, loadTerrain: async (setIds: readonly string[]) => {
      const sets = await input.loadTerrain!(setIds);
      textures!.textures.forEach(texture => texture.addEventListener("dispose", () => { disposed.push(texture.uuid); }));
      return sets;
    } };
    await expect(loadLayer(wrapped)).rejects.toThrow();
    expect(disposed).toHaveLength(textures!.textures.length);
  });
  it("disposes the fulfilled side when only the terrain textures reject", async () => {
    const assets = fakeAssets();
    let assetsDisposals = 0;
    const wrappedDispose = assets.dispose.bind(assets);
    assets.dispose = () => { wrappedDispose(); assetsDisposals++; };
    const input = await layerInput({ loadAssets: async () => {
      await new Promise(resolve => setImmediate(resolve));
      return assets;
    }, loadTerrain: async () => { throw new Error("injected terrain failure"); } });
    await expect(loadLayer(input)).rejects.toThrow("injected terrain failure");
    await new Promise(resolve => setImmediate(resolve));
    expect(assetsDisposals).toBe(1);
  });
  it("disposes the fulfilled terrain textures when only the assets reject", async () => {
    const textureUuids: string[] = [], disposed: string[] = [];
    const input = await layerInput({ loadAssets: async () => { throw new Error("injected asset failure"); },
      loadTerrain: async setIds => {
        await new Promise(resolve => setImmediate(resolve));
        const sets = fakeTerrainTextureSets(setIds);
        textureUuids.push(...sets.textures.map(texture => texture.uuid));
        sets.textures.forEach(texture => texture.addEventListener("dispose",
          () => disposed.push(texture.uuid)));
        return sets;
      } });
    await expect(loadLayer(input)).rejects.toThrow("injected asset failure");
    await new Promise(resolve => setImmediate(resolve));
    expect(disposed.sort()).toEqual([...textureUuids].sort());
  });
  it("stops at the next planning phase after an abort and releases what it built", async () => {
    const controller = new AbortController();
    const assets = fakeAssets();
    let assetsDisposals = 0;
    const wrappedDispose = assets.dispose.bind(assets);
    assets.dispose = () => { wrappedDispose(); assetsDisposals++; };
    const input = await layerInput({ signal: controller.signal, loadAssets: async () => {
      // Abort after the source gates pass; only the phase boundaries can observe it.
      setTimeout(() => controller.abort(), 0);
      await new Promise(resolve => setImmediate(resolve));
      return assets;
    } });
    await expect(loadLayer(input)).rejects.toThrow(/aborted/);
    expect(assetsDisposals).toBe(1);
  });
  it("reports unavailable authored clumps when the cap is zero", async () => {
    const input = await layerInput({ clumpMaxCount: 0 });
    const layer = await loadLayer(input);
    expect(layer.summary.grassClumpCount).toBe(0);
    expect(layer.summary.missing).toContain("authored-grass-clumps-unavailable");
    expect(layer.group.children.some(child => child.name === "Authored grass clumps (presentation design)")).toBe(false);
    layer.dispose();
  });
  it("keeps the authored clump design contract", () => {
    expect(CITY_VEGETATION_CLUMP_DESIGN_ID).toBe("aero-bench.authored-grass-clumps/v1");
    expect(CITY_VEGETATION_CLUMP_DEFAULTS.seed).toBe(20260930);
    expect(CITY_VEGETATION_CLUMP_DEFAULTS.densityPerM2).toBeGreaterThan(0);
    expect(CITY_VEGETATION_CLUMP_DEFAULTS.radiusM).toBeGreaterThan(0);
    expect(CITY_WEATHER_CLEAR.precipitation).toBe("none");
  });
});

function generatePlanFor(source: CityEnvironmentSource): CityEnvironmentPlan {
  // The source carries no extent field; measure it from the loaded footprints and greens
  // exactly as the map integration does from the published city geometry.
  const extent = measuredExtent(source);
  return generateCityEnvironment({
    geometryId: "city-presentation-vegetation-v1", roadGeometry: "pending-native-gate",
    roadbed: [], walkbed: [], crossings: [], junctions: [], lamps: [], signals: [],
    buildings: source.buildings, extent,
    greens: source.greens, sourceTrees: source.sourceTrees,
  }, { ...CITY_ENVIRONMENT_DEFAULTS }, BIGCITY_ENVIRONMENT_TREES);
}
function measuredExtent(source: CityEnvironmentSource): EnvironmentPolygon {
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  for (const polygon of [...source.buildings, ...source.greens]) {
    for (const [x, z] of polygon.outline) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
  }
  if (![minX, maxX, minZ, maxZ].every(Number.isFinite)) {
    throw new Error("Source has no measurable extent");
  }
  return { outline: [[minX, minZ], [maxX, minZ], [maxX, maxZ], [minX, maxZ]], holes: [] };
}
function clumpOptions(): { seed: number; densityPerM2: number; radiusM: number; maxCount: number } {
  return { seed: CITY_VEGETATION_CLUMP_DEFAULTS.seed,
    densityPerM2: CITY_VEGETATION_CLUMP_DEFAULTS.densityPerM2,
    radiusM: CITY_VEGETATION_CLUMP_DEFAULTS.radiusM, maxCount: CITY_VEGETATION_CLUMP_DEFAULTS.maxCount };
}
function planGrassClumpsFor(plan: CityEnvironmentPlan): {
  placements: GrassClumpPlacement[]; rejected: GrassClumpRejections } {
  return planGrassClumps(plan, { designId: CITY_VEGETATION_CLUMP_DESIGN_ID, ...clumpOptions() });
}

describe("cityVegetationRoadBinding", () => {
  function fixtureFixture(kind: "signal" | "street_lamp", id: string, x: number, z: number,
      halfSpan: number): CityVegetationEffectiveFixture {
    // A square footprint of the given half-span centred on (x, z): the conversion must
    // recover a circumscribed radius equal to the footprint's own half-diagonal.
    // The motor-band footprint is the inner half of that square: the ground-contact radius.
    const square = (h: number) => [{ outline: [[x - h, z - h], [x + h, z - h], [x + h, z + h],
      [x - h, z + h]] as [number, number][], holes: [] }];
    return { kind, id, source_location: { x, z }, footprints: square(halfSpan),
      motion_footprints: square(halfSpan / 2) };
  }
  it("converts road geometry and effective fixtures into the planner's road inputs", () => {
    const road = {
      roadbed: [rect(0, 0, 10, 5)], walkbed: [rect(10, 0, 15, 5)],
      crossings: [{ id: "c1", shape: [[5, 0], [5, -5]] as [number, number][], width: 2 }],
      junctions: [{ id: "j1", kind: "motor" as const,
        shape: [[20, 0], [25, 0], [25, 5], [20, 5]] as [number, number][] }],
    };
    const fixtures = [fixtureFixture("street_lamp", "lamp-0", 12, 2, 2),
      fixtureFixture("signal", "sig-0", 5, -2, 3)];
    const binding = cityVegetationRoadBinding(road, fixtures);
    expect(binding.roadbed).toEqual(road.roadbed);
    expect(binding.walkbed).toEqual(road.walkbed);
    expect(binding.crossings).toEqual(road.crossings);
    expect(binding.junctions).toEqual([{ id: "j1", outline: road.junctions[0]!.shape, holes: [] }]);
    expect(binding.lamps).toEqual([{ id: "lamp-0", x: 12, z: 2, radiusM: Math.hypot(2, 2),
      groundRadiusM: Math.hypot(1, 1) }]);
    expect(binding.signals).toEqual([{ id: "sig-0", x: 5, z: -2, radiusM: Math.hypot(3, 3),
      groundRadiusM: Math.hypot(1.5, 1.5) }]);
  });
  it("rejects an effective fixture with no measurable footprint or location", () => {
    const road = { roadbed: [], walkbed: [], crossings: [], junctions: [] };
    expect(() => cityVegetationRoadBinding(road,
      [{ kind: "street_lamp", id: "", source_location: { x: 0, z: 0 }, footprints: [],
        motion_footprints: [] }]))
      .toThrow(/missing its location or footprint/);
    expect(() => cityVegetationRoadBinding(road,
      [{ kind: "signal", id: "s", source_location: { x: NaN, z: 0 }, footprints: [{ outline: [[1, 1]], holes: [] }],
        motion_footprints: [{ outline: [[1, 1]], holes: [] }] }]))
      .toThrow(/missing its location or footprint/);
    expect(() => cityVegetationRoadBinding(road,
      [{ kind: "signal", id: "s", source_location: { x: 0, z: 0 },
        footprints: [{ outline: [[0, 0]], holes: [] }],
        motion_footprints: [{ outline: [[0, 0]], holes: [] }] }])).toThrow(/no measurable footprint/);
    expect(() => cityVegetationRoadBinding(road,
      [{ kind: "signal", id: "s", source_location: { x: 0, z: 0 },
        footprints: [{ outline: [[1, 1]], holes: [] }], motion_footprints: [] }]))
      .toThrow(/missing its location or footprint/);
  });
});

describe("road clearance against the accepted road geometry", () => {
  it("places no tree and no grass triangle inside the published roadbed, walkbed or crossings", async () => {
    // A green spanning the whole extent, cut across by a road, a sidewalk and a crosswalk:
    // every automatic placement must respect generateCityEnvironment's own clearances.
    const source: LayerSource = { ...fixtureSource(),
      buildings: [{ ...rect(-90, -90, -85, -85), id: "bld-1", geometrySha256: BUILDING_SHA }],
      greens: [{ ...rect(-80, -80, 80, 80), id: "green-1", provenance: { kind: "osm", sourceSha256: OSM_SHA,
        elementType: "way", elementId: "1001", sourceElementId: "1001", sourceElementType: "way",
        tags: { landuse: "grass" } } }],
    };
    const roadBinding: CityVegetationRoadBinding = {
      roadbed: [rect(-5, -80, 5, 80)], walkbed: [rect(5, -80, 10, 80), rect(-10, -80, -5, 80)],
      crossings: [{ id: "crossing-1", shape: [[7.5, -40], [-7.5, -40]], width: 3 }],
      junctions: [{ id: "junction-1", ...rect(-5, 38, 5, 42) }],
      lamps: [{ id: "lamp:0", x: 0, z: 60, radiusM: 2, groundRadiusM: 2 }],
      signals: [{ id: "signal:0", x: 0, z: -60, radiusM: 2, groundRadiusM: 2 }],
    };
    const input = await layerInput({ roadBinding }, source);
    const layer = await loadLayer(input);
    const plan = layer.group.userData.environmentPlan as CityEnvironmentPlan;
    expect(plan.roadGeometry).toBe("published");
    expect(plan.trees.length).toBeGreaterThan(0);
    const hardZones = [...roadBinding.roadbed, ...roadBinding.walkbed];
    for (const tree of plan.trees) {
      for (const zone of hardZones) {
        expect(environmentDiskIntersects([tree.x, tree.z], tree.envelopeRadiusM, zone)).toBe(false);
      }
    }
    // No grass triangle vertex falls inside the hard road/walk polygons: the planner clips
    // grass against roadbed and walkbed (and the other hard obstacles) before returning it.
    const insideHardZone = (point: readonly [number, number]): boolean => hardZones.some(zone =>
      environmentDiskIntersects(point, 0, zone));
    for (const patch of plan.grass) for (const triangle of patch.triangles) {
      const centroid: [number, number] = [(triangle[0]![0] + triangle[1]![0] + triangle[2]![0]) / 3,
        (triangle[0]![1] + triangle[1]![1] + triangle[2]![1]) / 3];
      expect(insideHardZone(centroid)).toBe(false);
    }
    expect(plan.stats.rejected.road + plan.stats.rejected.walk).toBeGreaterThan(0);
    layer.dispose();
  });
});

describe("published ground-cover source integration", () => {
  it("instantiates every drawable published source polygon against the accepted surface", async () => {
    const publicAsset = (url: string) => resolve(process.cwd(), "public", url.replace(/^\/+/, ""));
    const scene = JSON.parse(readFileSync(publicAsset("/city-presentation/default-scene-v1.json"), "utf8"));
    const renderManifest = JSON.parse(readFileSync(
      publicAsset(`${scene.building_render.base_url}manifest.json`), "utf8"));
    const pack = JSON.parse(readFileSync(publicAsset(`${scene.mesh_pack.base_url}manifest.json`), "utf8"));
    const road = validateCanonicalCityRoadPayload(JSON.parse(readFileSync(
      publicAsset(scene.road_assets.road.url), "utf8")));
    const fixtures = JSON.parse(readFileSync(publicAsset(scene.road_assets.effective_fixtures.url), "utf8"));
    const roadBinding = cityVegetationRoadBinding(road, fixtures.effective_fixtures);
    const input = cityVegetationInput(scene.environment_source, renderManifest.scene,
      scene.mesh_pack.manifest.sha256, pack.extent, roadBinding);
    const sourceBytes = new Uint8Array(readFileSync(publicAsset(scene.environment_source.url)));
    const displayedSurface = await displaySurfaceSha256(roadBinding.roadbed, roadBinding.walkbed);
    expect(displayedSurface).toBe(scene.road_assets.displayed_surface_sha256);
    // Terrain textures go through the real verified loader over the published library bytes;
    // only image decoding is replaced, because node has no image decoder.
    const decoded: number[] = [];
    const loadTerrain = (setIds: readonly string[]) => loadVerifiedTerrainTextureSets({ setIds, maxAnisotropy: 8,
      fetchBytes: async url => new Uint8Array(readFileSync(publicAsset(url))),
      decodeTexture: async bytes => { decoded.push(bytes.byteLength); return new THREE.Texture(); } });
    const layer = await loadCityVegetationLayer({ ...input, fetchBytes: async () => sourceBytes,
      loadAssets: async () => fakeAssets(), loadTerrain, clumpMaxCount: 0 }, displayedSurface);
    const assignments = layer.group.userData.groundMaterialAssignments as GroundMaterialAssignment[];
    expect(decoded).toHaveLength(terrainTextureSetIds([...assignments,
      ...(["green", "plaza", "planting_strip"] as const).map(kind => authoredLandscapeMaterial({ id: "design", kind }))]).length * 3);
    expect(layer.summary.groundMaterials.count).toBe(assignments.length);
    expect(layer.summary.groundMaterials.byFamily.unclassified).toBeUndefined();
    expect(layer.summary.sourceSha256).toBe(scene.environment_source.sha256);
    expect(layer.summary.groundCoverCount).toBe(19);
    expect(layer.summary.groundCoverAreaM2).toBeCloseTo(126493.4371, 3);
    expect(layer.groundCoverDrawnSet).toMatchObject({ status: "pass", geometry_id: displayedSurface,
      omission_count: 0 });
    expect(layer.groundCoverDrawnSet.parser_accepted_ids).toHaveLength(19);
    expect(layer.groundCoverDrawnSet.drawable_ids).toHaveLength(19);
    expect(layer.groundCoverDrawnSet.drawn_ids).toHaveLength(19);
    // Terrain transitions: the facade must publish the planner stats and mount one
    // blended mesh per surface height with at least one classified band piece.
    const transitions = layer.group.userData.terrainTransitions as TerrainTransitionStats;
    expect(transitions.pieces).toBeGreaterThan(0);
    expect(transitions.byClass.building + transitions.byClass.water + transitions.byClass.paved
      + transitions.byClass.open).toBe(transitions.pieces);
    const transitionGroup = layer.group.children.find(child =>
      child.name === "Terrain transition bands and tree bases")!;
    expect(transitionGroup).toBeDefined();
    expect(transitionGroup.userData.terrainTransitions).toBe(transitions);
    const transitionMeshes = transitionGroup.children as unknown as THREE.Mesh[];
    expect(transitionMeshes.length).toBeGreaterThan(0);
    let transitionVertices = 0;
    for (const mesh of transitionMeshes) {
      expect(mesh.name).toMatch(/^Terrain transitions y=/);
      expect((mesh.material as THREE.ShaderMaterial).side).toBe(THREE.FrontSide);
      transitionVertices += (mesh.geometry.getAttribute("position").count);
    }
    // One fan triangle per clipped side of each emitted convex polygon: the merged
    // transition geometry must cover every classified band piece plus the tree bases.
    expect(transitionVertices).toBeGreaterThanOrEqual(3 * transitions.pieces);
    // Disposing the layer releases the transition geometries and materials.
    const transitionMeshCount = transitionMeshes.length;
    expect(transitionMeshCount).toBeGreaterThan(0);
    let disposedGeometries = 0, disposedMaterials = 0;
    for (const mesh of transitionMeshes) {
      mesh.geometry.addEventListener("dispose", () => { disposedGeometries++; });
      (mesh.material as THREE.Material).addEventListener("dispose", () => { disposedMaterials++; });
    }
    layer.dispose();
    expect(disposedGeometries).toBe(transitionMeshCount);
    expect(disposedMaterials).toBe(transitionMeshCount);
    expect(transitionGroup.children).toHaveLength(0);
  }, 30000);
});

// Real-data check: loads the canonical road v3 payload, its effective fixtures and the
// verified presentation environment source, and asserts the published plan has zero
// vegetation/road overlaps -- the E1 acceptance evidence.
describe("real road and environment source clearance (E1 acceptance)", () => {
  it("clears every automatic placement against the published huangpu road, within budget", () => {
    const roadPath = resolve(process.cwd(), "public/city-presentation/huangpu-canonical-road-v3.json");
    const fixturesPath = resolve(process.cwd(),
      "public/city-presentation/huangpu-canonical-effective-fixtures-v1.json");
    const sourcePath = resolve(process.cwd(), "public/city-presentation/shanghai-source-environment-v1.json");
    for (const path of [roadPath, fixturesPath, sourcePath]) {
      expect(statSync(path).size).toBeGreaterThan(0);
    }
    const started = Date.now();
    const road = validateCanonicalCityRoadPayload(JSON.parse(readFileSync(roadPath, "utf8")));
    const fixturesDoc = JSON.parse(readFileSync(fixturesPath, "utf8")) as
      { effective_fixtures: readonly CityVegetationEffectiveFixture[] };
    const sourceBytes = new Uint8Array(readFileSync(sourcePath));
    const source = parseCityEnvironmentSource(JSON.parse(new TextDecoder().decode(sourceBytes)), {
      osmSha256: "6948a5a2611145a4c3f4283d756d580fbd8155b049379de91d80bccd8bc2465b",
      objectsSha256: "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc",
      origin: { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50.0 },
    });
    const binding = cityVegetationRoadBinding(road, fixturesDoc.effective_fixtures);
    // The extent must cover every loaded footprint, not just the buildings and greens.
    let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (const polygon of [...source.buildings, ...source.greens, ...binding.roadbed, ...binding.walkbed]) {
      for (const [x, z] of polygon.outline) {
        minX = Math.min(minX, x); maxX = Math.max(maxX, x);
        minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
      }
    }
    const extent: EnvironmentPolygon = { outline: [[minX, minZ], [maxX, minZ], [maxX, maxZ], [minX, maxZ]], holes: [] };
    const plan = generateCityEnvironment({
      geometryId: "city-presentation-vegetation-v1", roadGeometry: "published",
      roadbed: binding.roadbed, walkbed: binding.walkbed, crossings: binding.crossings,
      junctions: binding.junctions, lamps: binding.lamps, signals: binding.signals,
      buildings: source.buildings, extent, greens: source.greens, sourceTrees: source.sourceTrees,
    }, { ...CITY_ENVIRONMENT_DEFAULTS }, BIGCITY_ENVIRONMENT_TREES);
    const clumpPlan = planGrassClumpsFor(plan);
    expect(plan.roadGeometry).toBe("published");
    expect(plan.missing).not.toContain("road-clearance-pending-native-gate");
    const hardZones = [...binding.roadbed, ...binding.walkbed];
    for (const tree of plan.trees) for (const zone of hardZones) {
      expect(environmentDiskIntersects([tree.x, tree.z], tree.envelopeRadiusM, zone)).toBe(false);
    }
    for (const patch of plan.grass) for (const triangle of patch.triangles) {
      const centroid: [number, number] = [(triangle[0]![0] + triangle[1]![0] + triangle[2]![0]) / 3,
        (triangle[0]![1] + triangle[1]![1] + triangle[2]![1]) / 3];
      expect(hardZones.some(zone => environmentDiskIntersects(centroid, 0, zone))).toBe(false);
    }
    // Previous pending-mode plan (validation/frontend-opus-20260930/visual/plan-summary.json,
    // measured without any road clearance): 7 greens, 157 trees, 18236 grass clump placements.
    const previous = { greens: 7, trees: 157, clumpPlacements: 18236 };
    // eslint-disable-next-line no-console
    console.log("E1 road-bound vegetation vs. the previous pending plan", {
      greens: plan.grass.length, treesBefore: previous.trees, treesAfter: plan.trees.length,
      treesRemoved: previous.trees - plan.trees.length,
      clumpsBefore: previous.clumpPlacements, clumpsAfter: clumpPlan.placements.length,
      clumpsRemoved: previous.clumpPlacements - clumpPlan.placements.length,
      elapsedMs: Date.now() - started,
    });
    expect(plan.grass.length).toBe(previous.greens);
    expect(plan.trees.length).toBeLessThanOrEqual(previous.trees);
    expect(clumpPlan.placements.length).toBeLessThanOrEqual(previous.clumpPlacements);
    expect(Date.now() - started).toBeLessThan(28000);
  }, 30000);
});

// Env-gated real-source report: run with AERO_VEGETATION_PLAN_SUMMARY=1 to regenerate
// validation/frontend-opus-20260930/visual/plan-summary.json from the verified source.
describe("real source plan summary (env-gated)", () => {
  it("writes plan-summary.json from the verified presentation source", () => {
    if (process.env.AERO_VEGETATION_PLAN_SUMMARY !== "1") return;
    const sourcePath = resolve(process.cwd(), "public/city-presentation/shanghai-source-environment-v1.json");
    const bytes = new Uint8Array(readFileSync(sourcePath));
    const expectedSha = "c1277200aebf59c728cd4c5eb8cb91421cf3b65b2510dfadde8c592494f6f590";
    const actualSha = createHash("sha256").update(bytes).digest("hex");
    expect(actualSha).toBe(expectedSha);
    expect(bytes.byteLength).toBe(172205);
    const source = parseCityEnvironmentSource(JSON.parse(new TextDecoder().decode(bytes)), {
      osmSha256: "6948a5a2611145a4c3f4283d756d580fbd8155b049379de91d80bccd8bc2465b",
      objectsSha256: "c763d63177aff410a4c5e6154c98dc5c7f9873b17b024bc8c93fda9918c918dc",
      origin: { latitude_deg: 31.2288, longitude_deg: 121.481, ellipsoid_height_m: 50.0 },
    });
    const plan = generatePlanFor(source);
    const clumpPlan = planGrassClumpsFor(plan);    const summary = {
      source: { path: "public/city-presentation/shanghai-source-environment-v1.json",
        sha256: actualSha, sizeBytes: bytes.byteLength, buildings: source.buildings.length,
        sourceGreens: source.greens.length, sourceTrees: source.sourceTrees.length },
      plan: { roadGeometry: plan.roadGeometry, greenCount: plan.grass.length, treeCount: plan.trees.length,
        grassAreaM2: plan.stats.grassAreaM2, grassRemovedAreaM2: plan.stats.grassRemovedAreaM2,
        extentOutline: measuredExtent(source).outline, rejected: plan.stats.rejected, missing: plan.missing },
      clumps: { designId: CITY_VEGETATION_CLUMP_DESIGN_ID, ...clumpOptions(), ...clumpPlan.rejected,
        placements: clumpPlan.placements.length },
      perGreen: plan.grass.map(patch => ({ id: patch.id, sourceAreaM2: patch.sourceAreaM2,
        areaM2: patch.areaM2 })),
    };
    const outDir = resolve(process.cwd(), "../validation/frontend-opus-20260930/visual");
    mkdirSync(outDir, { recursive: true });
    writeFileSync(resolve(outDir, "plan-summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  });
});
