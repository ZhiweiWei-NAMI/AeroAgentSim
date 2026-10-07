import * as THREE from "three";
import { BIGCITY_ENVIRONMENT_TREES, CITY_ENVIRONMENT_DEFAULTS, createCityEnvironment,
  generateCityEnvironment, loadCityEnvironmentAssets, parseCityEnvironmentSource,
  type CityEnvironmentAssets, type CityEnvironmentPlan, type CityEnvironmentRenderer,
  type CityEnvironmentSource, type EnvironmentCrossing, type EnvironmentFootprint,
  type EnvironmentPoint, type EnvironmentPolygon, type EnvironmentStation } from "./city-environment";
import { validateCanonicalCityRoadPayload } from "./city-roads";
import { planGrassClumps, createGrassClumps } from "./city-grass-clumps";
import { applyVegetationWind, createVegetationWindDepthMaterial, createVegetationWindUniforms,
  setVegetationWind } from "./city-vegetation-wind";
import { assertNotAborted, nextTask, readBoundedResponse } from "./verified-bytes";
import { createSurfaceWetnessUniforms, surfaceWetnessFromWeather } from "./city-surface-wetness";
import { createWaterSurfaceUniforms, setWaterSurfaceTime, setWaterSurfaceWeather,
  type WaterSurfaceUniforms } from "./city-water-surface";
import { groundMaterialInputFromCover, groundMaterialInputFromGreen, summarizeGroundMaterialAssignments,
  type GroundMaterialAssignment, type GroundMaterialSummary } from "./city-ground-material-rules";
import { assignCityGroundMaterial, createTerrainSurfaceKit, loadVerifiedTerrainTextureSets,
  terrainTextureSetIds, type TerrainSurfaceKit } from "./city-terrain-surfaces";
import type { TerrainTextureSets } from "./city-terrain-materials";
import { createCityGroundCoverLayer, parseCityGroundCovers, planCityGroundCovers,
  type CityGroundCoverDrawnSetReport, type CityGroundCoverPlan,
  type CityGroundCoverRenderer } from "./city-ground-cover";
import { createTerrainTransitionLayer, planTerrainTransitions,
  type TerrainTransitionSurface, type TerrainTransitionStats } from "./city-terrain-transitions";
import { displaySurfaceSha256 } from "./city-surface-identity";
import { checkCityWeatherSettings, CITY_WEATHER_CLEAR, type CityWeatherSettings } from "./city-weather";
import { authoredLandscapeMaterial, CITY_AUTHORED_LANDSCAPE_KINDS, type VerifiedAuthoredLandscapeGeometry } from "./city-authored-landscape";

/**
 * One verified-source vegetation facade: loads the presentation environment JSON,
 * verifies its bytes against the declared digest, builds the automatic vegetation
 * plan cleared against the accepted published road geometry, renders trees and grass,
 * and adds authored grass clumps with wind response. The map integration mounts
 * `group` once and drives `setWeather`/`update` from the scene loop.
 */

export const CITY_VEGETATION_CLUMP_DESIGN_ID = "aero-bench.authored-grass-clumps/v1";
/** Authored presentation parameters; seed is fixed so runs are reproducible. */
export const CITY_VEGETATION_CLUMP_DEFAULTS: Readonly<{
  seed: number; densityPerM2: number; radiusM: number; maxCount: number;
}> = Object.freeze({ seed: 20260930, densityPerM2: 0.4, radiusM: 0.3, maxCount: 24000 });

export interface CityVegetationLayerSummary {
  readonly sourceSha256: string;
  readonly greenCount: number;
  readonly treeCount: number;
  readonly grassAreaM2: number;
  readonly grassClumpCount: number;
  readonly groundCoverCount: number;
  readonly groundCoverAreaM2: number;
  readonly woodlandFloorAreaM2: number;
  /** Material family, preset and basis of every source ground cover and green, by drawn area. */
  readonly groundMaterials: GroundMaterialSummary;
  readonly missing: readonly string[];
  readonly provenance: {
    readonly osmGreens: number;
    readonly osmGroundCovers: number;
    readonly derivedTrees: number;
    readonly authoredClumps: number;
  };
}

export interface CityVegetationLayer {
  readonly group: THREE.Group;
  readonly summary: CityVegetationLayerSummary;
  /** Measured from the instantiated meshes, ready for the C6 omission evidence export. */
  readonly groundCoverDrawnSet: CityGroundCoverDrawnSetReport;
  /** Exact verified road, extent and source footprints for separate authored surfaces. */
  readonly authoredLandscapeGeometry: VerifiedAuthoredLandscapeGeometry;
  readonly terrainSurfaceKit: TerrainSurfaceKit;
  /** All drawn source grass, woodland floor and ground-cover triangles. */
  readonly occupiedSourceTriangles: readonly (readonly EnvironmentPoint[])[];
  setWeather(settings: Readonly<CityWeatherSettings>): void;
  update(camera: THREE.Camera, visualTimeS: number): void;
  dispose(): void;
}

/** The road v3 geometry that `generateCityEnvironment` needs, as returned by the canonical
 * road payload validator (`city-roads.ts`). The road document's rings stay open; the planner
 * closes and triangulates them itself. */
type CityVegetationRoadGeometry = Pick<ReturnType<typeof validateCanonicalCityRoadPayload>,
  "roadbed" | "walkbed" | "crossings" | "junctions">;

/**
 * One effective (displayed-only) street fixture, exactly as published in the road v3
 * effective-fixtures document (`aero-bench.city-effective-fixture-geometry/v1`,
 * `effective_fixtures[]`). Omitted fixtures are not in this list and never reach the
 * planner: `footprints` is the fixture's own measured plan-view envelope (pole, head, arm
 * or signal assembly) at its displayed location, already unioned from the source GLB.
 */
export interface CityVegetationEffectiveFixture {
  readonly kind: "signal" | "street_lamp";
  readonly id: string;
  readonly source_location: { readonly x: number; readonly z: number };
  readonly footprints: readonly EnvironmentPolygon[];
  /** Displayed geometry clipped to the declared motor band; the fixture's ground-level part. */
  readonly motion_footprints: readonly EnvironmentPolygon[];
}

/** The planner's road-derived inputs, bound to the accepted road geometry and the
 * effective fixture selection. `roadGeometry: "published"` always follows from this. */
export interface CityVegetationRoadBinding {
  readonly roadbed: readonly EnvironmentPolygon[];
  readonly walkbed: readonly EnvironmentPolygon[];
  readonly crossings: readonly EnvironmentCrossing[];
  readonly junctions: readonly EnvironmentFootprint[];
  readonly lamps: readonly EnvironmentStation[];
  readonly signals: readonly EnvironmentStation[];
}

function fixtureEnvelopeRadiusM(fixture: CityVegetationEffectiveFixture,
    footprints: readonly EnvironmentPolygon[]): number {
  const { x, z } = fixture.source_location;
  let radius = 0;
  for (const polygon of footprints) {
    for (const ring of [polygon.outline, ...polygon.holes]) {
      for (const point of ring) radius = Math.max(radius, Math.hypot(point[0] - x, point[1] - z));
    }
  }
  if (!(radius > 0)) throw new Error(`City vegetation effective fixture has no measurable footprint: ${fixture.id}`);
  return radius;
}

function fixtureStations(effectiveFixtures: readonly CityVegetationEffectiveFixture[],
    kind: "signal" | "street_lamp"): EnvironmentStation[] {
  return effectiveFixtures.filter(fixture => fixture.kind === kind).map(fixture => {
    const { x, z } = fixture.source_location;
    if (!fixture.id || !Number.isFinite(x) || !Number.isFinite(z)
        || !Array.isArray(fixture.footprints) || fixture.footprints.length === 0
        || !Array.isArray(fixture.motion_footprints) || fixture.motion_footprints.length === 0) {
      throw new Error(`City vegetation effective fixture is missing its location or footprint: ${kind}`);
    }
    // The motor-band footprint (0 to 3.41 m) is the measured ground-level part of the fixture.
    return { id: fixture.id, x, z, radiusM: fixtureEnvelopeRadiusM(fixture, fixture.footprints),
      groundRadiusM: fixtureEnvelopeRadiusM(fixture, fixture.motion_footprints) };
  });
}

/**
 * Pure conversion from the published road v3 geometry and the effective (displayed-only)
 * fixtures into the vegetation planner's road inputs. `generateCityEnvironment` requires
 * only a non-degenerate ring (>= 3 distinct points, nonzero signed area) for roadbed,
 * walkbed and junction polygons, and a >= 2-point centerline for crossings; it closes and
 * triangulates rings itself, so the road v3 payload's open rings pass through unchanged --
 * no re-closing or re-orientation happens here, and no validation is loosened.
 *
 * `VerifiedCityRoadAssets` (`city-road-assets.ts`) verifies the road and effective-fixtures
 * documents' bytes but, as implemented, does not retain their parsed geometry: it exposes
 * only object URLs for the road/traffic/flight bytes and the selected signal IDs/lamp
 * indices (`fixtures.signalIds`/`fixtures.streetLampIndices`). Callers must keep the
 * `validateCanonicalCityRoadPayload(...)` result and the effective-fixtures document's own
 * `effective_fixtures` array from that same verified load to call this function; this
 * module cannot derive them from `VerifiedCityRoadAssets` alone.
 */
export function cityVegetationRoadBinding(road: CityVegetationRoadGeometry,
    effectiveFixtures: readonly CityVegetationEffectiveFixture[]): CityVegetationRoadBinding {
  return {
    roadbed: road.roadbed, walkbed: road.walkbed, crossings: road.crossings,
    junctions: road.junctions.map(junction => ({ id: junction.id, outline: junction.shape, holes: [] })),
    lamps: fixtureStations(effectiveFixtures, "street_lamp"),
    signals: fixtureStations(effectiveFixtures, "signal"),
  };
}

export interface CityVegetationLayerInput {
  /** The presentation environment source; bytes are verified before parsing. */
  readonly source: { readonly url: string; readonly sha256: string; readonly sizeBytes: number };
  /** Expected authority of the source document; a mismatch rejects the load. */
  readonly authority: {
    readonly osmSha256: string; readonly objectsSha256: string;
    readonly origin: { readonly latitude_deg: number; readonly longitude_deg: number;
      readonly ellipsoid_height_m: number };
  };
  readonly extent: EnvironmentPolygon;
  /** The accepted road geometry and effective fixtures; vegetation always clears the
   * published road, never a pending or partial one. Build this with
   * `cityVegetationRoadBinding`. */
  readonly roadBinding: CityVegetationRoadBinding;
  readonly signal?: AbortSignal;
  /** Injectable byte fetch for tests and alternative transports. */
  readonly fetchBytes?: (url: string, signal?: AbortSignal) => Promise<Uint8Array>;
  /** Injectable asset loader so tests do not load FBX models from disk or network. */
  readonly loadAssets?: (assets: typeof BIGCITY_ENVIRONMENT_TREES) => Promise<CityEnvironmentAssets>;
  /** Injectable terrain texture loader; the default verifies every file against the pinned library. */
  readonly loadTerrain?: (setIds: readonly string[], signal?: AbortSignal) => Promise<TerrainTextureSets>;
  /** Override the authored clump design defaults. */
  readonly clumpSeed?: number;
  readonly clumpDensityPerM2?: number;
  readonly clumpRadiusM?: number;
  readonly clumpMaxCount?: number;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes.buffer.slice(bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function defaultFetchBytes(url: string, maxBytes: number,
    signal?: AbortSignal): Promise<Uint8Array> {
  assertNotAborted(signal);
  const response = await fetch(url, { signal, headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`City vegetation source request failed: ${url} status ${response.status}`);
  return new Uint8Array(await readBoundedResponse(response, maxBytes, "City vegetation source", signal));
}

export async function loadCityVegetationLayer(input: CityVegetationLayerInput,
    displayedSurfaceSha256: string): Promise<CityVegetationLayer> {
  const { source, authority, extent, roadBinding, signal } = input;
  if (!/^[a-f0-9]{64}$/.test(source.sha256)) throw new Error("City vegetation source sha256 is invalid");
  if (!/^[a-f0-9]{64}$/.test(displayedSurfaceSha256)) {
    throw new Error("City vegetation displayed surface sha256 is invalid");
  }
  if (!Number.isSafeInteger(source.sizeBytes) || source.sizeBytes <= 0) {
    throw new Error("City vegetation source sizeBytes is invalid");
  }
  assertNotAborted(signal);
  const bytes = await (input.fetchBytes ?? ((url: string, abort?: AbortSignal) =>
    defaultFetchBytes(url, source.sizeBytes, abort)))(source.url, signal);
  assertNotAborted(signal);
  if (bytes.byteLength !== source.sizeBytes) {
    throw new Error(`City vegetation source size mismatch: expected ${source.sizeBytes} bytes, received ${bytes.byteLength}`);
  }
  const digest = await sha256Hex(bytes);
  if (digest !== source.sha256) {
    throw new Error(`City vegetation source sha256 mismatch: expected ${source.sha256}, received ${digest}`);
  }
  const actualSurfaceSha256 = await displaySurfaceSha256(roadBinding.roadbed, roadBinding.walkbed);
  if (actualSurfaceSha256 !== displayedSurfaceSha256) {
    throw new Error("City vegetation road polygons differ from the verified displayed surface sha256");
  }
  const raw: unknown = JSON.parse(new TextDecoder().decode(bytes));
  const verified = parseCityEnvironmentSource(raw, {
    osmSha256: authority.osmSha256, objectsSha256: authority.objectsSha256, origin: authority.origin });
  const groundCovers = parseCityGroundCovers(raw, {
    osmSha256: authority.osmSha256, objectsSha256: authority.objectsSha256, origin: authority.origin });
  const assignments = new Map<string, GroundMaterialAssignment>();
  for (const material of [...groundCovers.map(groundMaterialInputFromCover),
    ...verified.greens.map(green => groundMaterialInputFromGreen(green))].map(assignCityGroundMaterial)) {
    if (assignments.has(material.polygonId)) throw new Error(`City ground polygon id is not unique: ${material.polygonId}`);
    assignments.set(material.polygonId, material);
  }
  const kitAssignments = [...assignments.values(), ...CITY_AUTHORED_LANDSCAPE_KINDS.map(kind =>
    authoredLandscapeMaterial({ id: `authored-preset-${kind}`, kind }))];
  const loadTerrain = input.loadTerrain ?? ((setIds: readonly string[], abort?: AbortSignal) =>
    loadVerifiedTerrainTextureSets({ setIds, maxAnisotropy: 8, signal: abort }));
  const loadAssets = input.loadAssets ?? loadCityEnvironmentAssets;
  // Both loads run in parallel and neither may leak the other's result: settle both first,
  // dispose whichever fulfilled, then throw the assets rejection (first if both rejected).
  const [assetsSettled, terrainSettled] = await Promise.all([
    Promise.allSettled([loadAssets(BIGCITY_ENVIRONMENT_TREES)]),
    Promise.allSettled([loadTerrain(terrainTextureSetIds(kitAssignments), signal)]),
  ]);
  const assetsRejected = assetsSettled[0]!.status === "rejected";
  const terrainRejected = terrainSettled[0]!.status === "rejected";
  if (assetsRejected || terrainRejected) {
    if (!assetsRejected) (assetsSettled[0] as PromiseFulfilledResult<CityEnvironmentAssets>).value.dispose();
    if (!terrainRejected) (terrainSettled[0] as PromiseFulfilledResult<TerrainTextureSets>).value.dispose();
    throw (assetsRejected
      ? (assetsSettled[0] as PromiseRejectedResult).reason
      : (terrainSettled[0] as PromiseRejectedResult).reason);
  }
  const assets = (assetsSettled[0] as PromiseFulfilledResult<CityEnvironmentAssets>).value;
  const terrainTextures = (terrainSettled[0] as PromiseFulfilledResult<TerrainTextureSets>).value;
  // Wet ground darkens and gains some sheen by physical family; it never becomes a mirror.
  const wetness = createSurfaceWetnessUniforms();
  // Shared wind-ripple response of the water materials (T8b); one set per layer.
  const waterSurface: WaterSurfaceUniforms = createWaterSurfaceUniforms();
  let kit: TerrainSurfaceKit | null = null;
  let renderer: CityEnvironmentRenderer | null = null;
  let groundCoverRenderer: CityGroundCoverRenderer | null = null;
  let transitionRendererRef: ReturnType<typeof createTerrainTransitionLayer> | null = null;
  let clumps: ReturnType<typeof createGrassClumps> | null = null;
  const depthMaterials: THREE.MeshDepthMaterial[] = [];
  try {
    const plan = generateCityEnvironment({
      geometryId: displayedSurfaceSha256, roadGeometry: "published",
      roadbed: roadBinding.roadbed, walkbed: roadBinding.walkbed, crossings: roadBinding.crossings,
      junctions: roadBinding.junctions, lamps: roadBinding.lamps, signals: roadBinding.signals,
      buildings: verified.buildings, extent, greens: verified.greens, sourceTrees: verified.sourceTrees,
    }, { ...CITY_ENVIRONMENT_DEFAULTS }, BIGCITY_ENVIRONMENT_TREES);
    const wind = createVegetationWindUniforms();
    kit = createTerrainSurfaceKit(kitAssignments, terrainTextures, wetness, waterSurface);
    renderer = createCityEnvironment(plan, assets, { kit, assignments });
    // Each planning phase takes up to a few hundred ms; run the phases in separate tasks.
    await nextTask(signal);
    const groundCoverPlan = planCityGroundCovers(groundCovers, {
      geometryId: displayedSurfaceSha256, roadGeometry: "published",
      roadbed: roadBinding.roadbed, walkbed: roadBinding.walkbed, buildings: verified.buildings,
    });
    groundCoverRenderer = createCityGroundCoverLayer(groundCoverPlan, assignments, kit);
    await nextTask(signal);
    // Soft-edge transition bands and tree bases, planned from the drawn surfaces only.
    const transitionSurfaces: TerrainTransitionSurface[] = [
      ...[...plan.grass, ...plan.woodlandFloor].map(patch => ({
        id: patch.id, family: assignments.get(patch.id)!.family,
        surfaceY: plan.config.grassY, triangles: patch.triangles })),
      ...groundCoverPlan.covers.filter(cover => cover.triangles.length > 0).map(cover => ({
        id: cover.id, family: assignments.get(cover.id)!.family, surfaceY: 0.012,
        triangles: cover.triangles })),
    ];
    const transitionPlan = planTerrainTransitions({
      surfaces: transitionSurfaces,
      buildings: verified.buildings,
      roadbed: roadBinding.roadbed, walkbed: roadBinding.walkbed, trees: plan.trees,
    });
    const transitionRenderer = createTerrainTransitionLayer(transitionPlan);
    transitionRendererRef = transitionRenderer;
    await nextTask(signal);
    const treeMaterials = new Set<THREE.Material>();
    for (const template of assets.trees.values()) for (const part of template.parts) treeMaterials.add(part.material);
    for (const material of treeMaterials) applyVegetationWind(material, wind, "tree");
    // Shadow casters use a depth pass; give it the same wind so shadows follow the crowns.
    const depthByMaterial = new Map<THREE.Material, THREE.MeshDepthMaterial>();
    for (const material of treeMaterials) {
      const textured = material as THREE.Material & { map?: THREE.Texture | null };
      const depth = createVegetationWindDepthMaterial(wind, "tree",
        { map: textured.map ?? null, alphaTest: material.alphaTest });
      depthMaterials.push(depth); depthByMaterial.set(material, depth);
    }
    renderer.group.traverse(node => {
      if (node instanceof THREE.InstancedMesh && node.castShadow && !Array.isArray(node.material)) {
        const depth = depthByMaterial.get(node.material);
        if (depth === undefined) throw new Error(`City vegetation shadow material is missing: ${node.name}`);
        node.customDepthMaterial = depth;
      }
    });
    // Clumps grow only on green areas whose assigned physical cover is grass.
    const clumpPlan = planGrassClumps({ ...plan,
      grass: plan.grass.filter(patch => assignments.get(patch.id)!.family === "grass") }, {
      seed: input.clumpSeed ?? CITY_VEGETATION_CLUMP_DEFAULTS.seed,
      designId: CITY_VEGETATION_CLUMP_DESIGN_ID,
      densityPerM2: input.clumpDensityPerM2 ?? CITY_VEGETATION_CLUMP_DEFAULTS.densityPerM2,
      radiusM: input.clumpRadiusM ?? CITY_VEGETATION_CLUMP_DEFAULTS.radiusM,
      maxCount: input.clumpMaxCount ?? CITY_VEGETATION_CLUMP_DEFAULTS.maxCount,
    });
    clumps = clumpPlan.placements.length > 0 ? createGrassClumps(clumpPlan.placements, wind, { groundY: plan.config.grassY }) : null;
    const missing = [...plan.missing];
    if (plan.grass.length > 0 && clumps === null) missing.push("authored-grass-clumps-unavailable");
    const group = new THREE.Group();
    group.name = "City terrain detail layer (source ground covers + vegetation + authored clumps)";
    // Ground covers sit at y=0.012; grass is y=0.018. Mount ground first so the
    // group hierarchy matches the physical vertical order used by the renderer.
    group.add(groundCoverRenderer.group, renderer.group);
    group.add(transitionRenderer.group);
    if (clumps !== null) group.add(clumps.group);
    const summary: CityVegetationLayerSummary = {
      sourceSha256: digest, greenCount: plan.grass.length, treeCount: plan.trees.length,
      grassAreaM2: plan.stats.grassAreaM2, grassClumpCount: clumpPlan.placements.length,
      groundCoverCount: groundCoverPlan.sourceCount, groundCoverAreaM2: groundCoverPlan.stats.drawnAreaM2,
      woodlandFloorAreaM2: plan.stats.woodlandFloorAreaM2,
      groundMaterials: summarizeGroundMaterialAssignments([...assignments.values()], Object.fromEntries([
        ...[...assignments.keys()].map(id => [id, 0] as const),
        ...groundCoverPlan.covers.map(cover => [cover.id, cover.drawnAreaM2] as const),
        ...[...plan.grass, ...plan.woodlandFloor].map(patch => [patch.id, patch.areaM2] as const)])),
      missing,
      provenance: { osmGreens: verified.greens.length, osmGroundCovers: groundCovers.length, derivedTrees: plan.trees.length,
        authoredClumps: clumpPlan.placements.length },
    };
    group.userData.vegetationSummary = summary;
    group.userData.environmentMissing = missing;
    group.userData.environmentRoadGeometry = plan.roadGeometry;
    group.userData.environmentPlan = plan;
    group.userData.groundCoverPlan = groundCoverPlan;
    group.userData.groundCoverDrawnSet = groundCoverRenderer.drawnSet;
    group.userData.groundMaterialAssignments = [...assignments.values()];
    group.userData.terrainTransitions = transitionPlan.stats satisfies TerrainTransitionStats;
    let weather: Readonly<CityWeatherSettings> = CITY_WEATHER_CLEAR;
    let timeS = 0;
    const layer: CityVegetationLayer = {
      terrainSurfaceKit: kit,
      occupiedSourceTriangles: [...plan.grass, ...plan.woodlandFloor, ...groundCoverPlan.covers]
        .flatMap(patch => patch.triangles),
      group, summary, groundCoverDrawnSet: groundCoverRenderer.drawnSet,
      authoredLandscapeGeometry: {
        displayedSurfaceSha256, roadGeometry: "published", extent,
        roadbed: roadBinding.roadbed, walkbed: roadBinding.walkbed, buildings: verified.buildings,
      },
      setWeather: settings => {
        checkCityWeatherSettings(settings);
        weather = settings;
        wetness.uWetness.value = surfaceWetnessFromWeather(settings);
        setVegetationWind(wind, weather.windMps, weather.windDirectionDeg, timeS);
        setWaterSurfaceWeather(waterSurface, settings);
      },
      update: (camera, visualTimeS) => {
        if (!Number.isFinite(visualTimeS) || visualTimeS < 0) {
          throw new Error("City vegetation layer update time is invalid");
        }
        timeS = visualTimeS;
        setVegetationWind(wind, weather.windMps, weather.windDirectionDeg, timeS);
        setWaterSurfaceTime(waterSurface, timeS);
        renderer!.updateLod(camera);
        clumps?.update(camera);
      },
      dispose: () => {
        for (const depth of depthMaterials) depth.dispose();
        clumps?.dispose();
        transitionRendererRef?.dispose();
        groundCoverRenderer?.dispose();
        renderer?.dispose();
        kit?.dispose();
        assets.dispose();
        group.clear();
      },
    };
    return layer;
  } catch (error) {
    for (const depth of depthMaterials) depth.dispose();
    clumps?.dispose();
    transitionRendererRef?.dispose();
    groundCoverRenderer?.dispose();
    renderer?.dispose();
    if (kit !== null) kit.dispose(); else terrainTextures.dispose();
    assets.dispose();
    throw error;
  }
}

/** Re-exported so the map integration needs no direct planner import. */
export type { CityEnvironmentPlan, CityEnvironmentSource };
export type { CityGroundCoverPlan };
