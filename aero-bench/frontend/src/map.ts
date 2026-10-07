import { cityStaticObstacles } from "./city-static-obstacles";
import { createFacilityVisual, disposeFacilityVisual, loadFacilityVisualAssets,
  toFacilityVisualSpec } from "./city-facility-models";
import { syncMeteredRoofVisibility } from "./city-building-facade";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { PublicBuilding, PublicNetworkFrame, PublicScenario, PublicTrafficLightFrame, PublicTrajectory, ResolvedCoordinate, SceneState } from "./generated/aero-bench-contracts";
import type { EntityKind, LayerVisibility } from "./state/layers";
import type { TraceTarget } from "./state/target";
import type { OsmEntityKind, OsmFixture, OsmTrafficSignal, OsmJson } from "./osm2world/source";
import { OSM2WORLD_STYLE_BASE, resolveTextureUrl, type O2WMesh } from "./osm2world/runtime";
import { projectGeographic } from "./osm2world/projection";
import { AssetResolver } from "./asset-resolver";
import { loadMeshPack, type LoadedMeshPack } from "./osm2world/pack-loader";
import { loadCitySceneConfig, type CitySceneConfig } from "./city-scene-config";
import { CITY_PREVIEW_SCENE_PRESETS } from "./city-preview-scenes";
import { cityTrafficProvenance, loadVerifiedCityRoadAssets, type VerifiedCityRoadAssets } from "./city-road-assets";
import { loadCityTrafficReplay } from "./city-traffic-replay";
import { verifyAuditedTrafficForScene } from "./city-audited-traffic";
import { trafficPreviewDraftMatches, type TrafficPreviewDraftSnapshot,
  type VerifiedTrafficPreviewArtifact } from "./city-traffic-preview-api";
import { assertNotAborted } from "./verified-bytes";
import { currentLanguage, subscribeLanguage, t, tf } from "./i18n";
import { BuildingRenderStreamer, buildingPlacement, fetchBuildingRenderManifest,
  setBuildingRenderLighting, type BuildingRenderProgress } from "./city-building-renders";
import { fetchBuildingRenderSourceContext, validateBuildingRenderContext } from "./city-building-source-context";
import type { MeshPackManifest, PackedBatch, PackedMaterial } from "./osm2world/pack";
import { loadEntityVisual } from "./entity-visuals";
import { CityTrafficPreview, disposePresentation, loadCityBuildings, loadCityTrees, loadStaticTrafficSignals, setCityBuildingCalibration, setCityBuildingLighting, setCollisionBoxesVisible, type CityBuilding, type CityTree } from "./city-presentation";
import { addCityAdvertising, setCityAdvertisingLighting, type AdvertisingLocation } from "./city-advertising";
import { createSidewalkHeightSampler } from "./city-sidewalk-height";
import { loadCityRoads, setCityRoadLighting } from "./city-roads";
import { cacheStaticTransforms, visiblePickObjects } from "./city-rendering";
import { CityWeather, CITY_WEATHER_CLEAR, type CityWeatherSettings } from "./city-weather";
import { createRenderWeatherControls } from "./city-render-weather-controls";
import { loadCityVegetationLayer, type CityVegetationLayer } from "./city-vegetation-layer";
import { CityPerfOverlay } from "./city-perf-overlay";
import { cityVegetationInput } from "./city-vegetation-binding";
import { createCityAuthoredLandscapeLayer, parseCityAuthoredLandscape, planCityAuthoredLandscape,
  type CityAuthoredLandscapeItem, type CityAuthoredLandscapeKind, type CityAuthoredLandscapePlan,
  type CityAuthoredLandscapeRenderer } from "./city-authored-landscape";
import { isTerrainCompletionItem, proposeTerrainCompletion } from "./city-terrain-completion";
import { createVegetationWindUniforms, setVegetationWind } from "./city-vegetation-wind";
import { applySurfaceWetness, createSurfaceWetnessUniforms, surfaceWetnessFromWeather } from "./city-surface-wetness";
import { centerCityDaySky, createCityDaySky, disposeCityDaySky,
  setCityDaySkySunDirection, setCityDaySkyWeather } from "./city-day-sky";
import { CityLocalReflections } from "./city-local-reflections";
import type { VerifiedStaticPresentation } from "./city-authoring-api";
import type { LoadedNativeCityPresentation } from "./native-city-presentation";
import type { SelectedScenarioFacility } from "./city-selected-scenario";
import { CITY_LIGHTING } from "./city-lighting";
import { setCityWindowLitFraction } from "./city-facade-window-lights";
import { CityCalibratedEnvironment, applyCityLightingCalibration, applyCitySkyCalibration, cityVisualSolar,
  focusCityCalibratedSun, sampleCityLighting, type CityLightingSample, type CityTimeOfDay } from "./city-lighting-calibration";
import { CityOperationsPreview, type PreviewValidationIssue } from "./city-operations-preview";
import { type CityDraftEnvironment } from "./city-draft-environment";
import { parseCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { normalizeBuildingPlacements, normalizeRoadbed, validateAirspacePolygon,
  validateFacilityPlacement, type BuildingPlacementSource, type PlacementBox, type RoadbedSource,
  type RoadPolygon } from "./city-workspace-geometry";
import { type SourceBuildingShape, type SourceBuildingTriangleRange } from "./city-building-shape";

import { applyMountedCamera, cameraFootprintOnPlane, type CameraMount } from "./observation-camera";
import { declaredSensorMount } from "./observation-camera-sensors";
import { mountOperationsMonitor, type OperationsMonitorHandle, type OperationsObject,
  type OperationsSnapshot, type OperationsPolygon, type OperationsBuildingSquare } from "./operations-monitor";
import { telemetryFromSample, publicOperationEvents, latestOperationTasks } from "./operations-data";
import { businessDestinationMarkers, businessParcelAnchor, compactCargoLabel, type BusinessIdentityView, type SelectedFrameBusinessView } from "./p02-entity-overlays";
import "./operations-monitor.css";

export type CameraMode = "free" | "chase" | "cockpit";
export interface MapScene { readonly pack?: LoadedMeshPack | null; readonly nativePresentation?: LoadedNativeCityPresentation | null; readonly osm?: OsmJson | null; readonly scenario: PublicScenario | null; readonly sceneState: SceneState | null; readonly trajectories: readonly PublicTrajectory[]; readonly networkFrame: PublicNetworkFrame | null; readonly trafficLightFrame?: PublicTrafficLightFrame | null; readonly tick?: number | null; readonly operationContext?: { readonly sourceLabel: string; readonly sourceKind?: "live" | "replay"; readonly runId?: string; readonly timeSeconds?: number; readonly connection?: string; readonly clockState: "playing" | "paused" | "stopped"; readonly events: readonly import("./trace").PublicRunEvent[] }; readonly trafficLights?: readonly OsmTrafficSignal[]; readonly authoring?: { readonly key: string; readonly presentation: VerifiedStaticPresentation | null }; }
export interface MapView { readonly layers: LayerVisibility; readonly hiddenEntities: ReadonlySet<string>; readonly hiddenTrajectories: ReadonlySet<string>; readonly isolate: TraceTarget | null; readonly selected: TraceTarget | null; readonly hovered: TraceTarget | null; }
export interface MapCallbacks { readonly onObservationModeChange?: (mode: CameraMode) => void; readonly onPick: (target: TraceTarget | null) => void; readonly onHover: (target: TraceTarget | null) => void; readonly onContextMenu: (target: TraceTarget | null, x: number, y: number) => void; readonly onUnavailable?: () => void; readonly onBasemapNote?: (note: string) => void; readonly onSceneStatus?: (scenarioDigest: string, status: "ready" | "failed", detail?: string) => void; readonly onEnvironmentEdit?: (environment: CityDraftEnvironment) => void; }
export interface CityPresentationOptions {
  readonly mood?: "day" | "dusk";
  /** Calibrated render scenes distinguish twilight from night; `mood: "dusk"` selects twilight. */
  readonly timeOfDay?: CityTimeOfDay;
  readonly weather?: CityWeatherSettings;
  readonly reflectionsEnabled?: boolean;
}

const NEXT_TIME_OF_DAY_LABEL: Readonly<Record<CityTimeOfDay, string>> = {
  day: "切换黄昏", twilight: "切换夜景", night: "切换日景",
};
const NEXT_TIME_OF_DAY: Readonly<Record<CityTimeOfDay, CityTimeOfDay>> = {
  day: "twilight", twilight: "night", night: "day",
};
/**
 * Surface albedo is independent of time of day under calibrated light. Unclassified ground keeps a
 * neutral tone and no pavement texture; these are display values, not surveyed reflectance.
 */
const CALIBRATED_GROUND_ALBEDO = { unclassified: 0x7e7c77, paving: 0xa3a099, grass: 0x6c8a52 } as const;
/** Display haze matched to the calibrated sky horizon; not a visibility measurement. */
const CALIBRATED_FOG: Readonly<Record<CityTimeOfDay, { color: number; near: number; far: number }>> = {
  day: { color: 0xa9bccd, near: 700, far: 5200 },
  twilight: { color: 0x5c5462, near: 380, far: 2600 },
  night: { color: 0x0a111d, near: 320, far: 2200 },
};

export type MappedGroundExtent = MeshPackManifest["extent"];
/** Mesh-pack northing becomes negative renderer Z; extent is not a surface classification. */
export function mappedGroundBounds(extent: MappedGroundExtent): THREE.Vector4 {
  return new THREE.Vector4(extent.west, extent.east, -extent.north, -extent.south);
}

export function outsideMappedExtentDistance(x: number, z: number, bounds: THREE.Vector4): number {
  return Math.hypot(Math.max(bounds.x - x, 0, x - bounds.y),
    Math.max(bounds.z - z, 0, z - bounds.w));
}

/** Display-only haze ramp, not a measured visibility or a surveyed ground surface. */
export const OUT_OF_EXTENT_FADE_METRES = 180;
export function createOutOfExtentGround(): THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial> {
  const material = new THREE.MeshBasicMaterial({ color: CALIBRATED_GROUND_ALBEDO.unclassified,
    depthWrite: false, fog: true });
  const uniforms = { uMappedBounds: { value: new THREE.Vector4() },
    uBoundaryFadeMetres: { value: OUT_OF_EXTENT_FADE_METRES } };
  material.userData.mappedExtentUniforms = uniforms;
  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec2 vMappedGroundXZ;")
      .replace("#include <begin_vertex>", `#include <begin_vertex>
  vMappedGroundXZ = (modelMatrix * vec4(transformed, 1.0)).xz;`);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", `#include <common>
varying vec2 vMappedGroundXZ;
uniform vec4 uMappedBounds;
uniform float uBoundaryFadeMetres;`)
      .replace("#include <clipping_planes_fragment>", `#include <clipping_planes_fragment>
  vec2 outsideDelta = max(max(vec2(uMappedBounds.x, uMappedBounds.z) - vMappedGroundXZ,
    vMappedGroundXZ - vec2(uMappedBounds.y, uMappedBounds.w)), vec2(0.0));
  float boundaryDistance = length(outsideDelta);
  if (boundaryDistance == 0.0) discard;`)
      .replace("#include <fog_fragment>", `#include <fog_fragment>
#ifdef USE_FOG
  gl_FragColor.rgb = mix(gl_FragColor.rgb, fogColor,
    smoothstep(0.0, uBoundaryFadeMetres, boundaryDistance));
#endif`);
  };
  material.customProgramCacheKey = () => "out-of-mapped-extent-ground-v1";
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(10000, 10000), material);
  ground.name = "out-of-mapped-extent-ground";
  ground.userData.presentationOnly = true;
  ground.userData.surfaceClassification = "out-of-mapped-extent";
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.03;
  ground.renderOrder = -101;
  ground.visible = false;
  // Fog can change through either calibrated lighting or weather. Read the active
  // scene at draw time, so no mood/weather branch can restore an unclassified plane.
  ground.onBeforeRender = (_renderer, scene) => {
    if (scene.fog === null) throw new Error("Out-of-extent ground requires the active horizon fog");
    ground.userData.activeFogColor = scene.fog.color.getHexString();
  };
  return ground;
}

const entityColors: Readonly<Record<EntityKind, number>> = { uav: 0x1f8fbc, ugv: 0xd08345, pedestrian: 0x226c3d, static_asset: 0x8d684a };

function gotoCityScene(path: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("city", path);
  window.location.assign(url.pathname + url.search + url.hash);
}

/** Viewer navigation is separate from Studio's explicit draft replacement. */
export function createCityPreviewSceneButtons(
  navigate: (scenePath: string) => void = gotoCityScene,
): HTMLButtonElement[] {
  return CITY_PREVIEW_SCENE_PRESETS.map(preset => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${preset.placeLabel}工程预览`;
    button.dataset.scenePath = preset.scenePath;
    button.addEventListener("click", () => navigate(preset.scenePath));
    return button;
  });
}

const textureLoader = new THREE.TextureLoader();
const textureCache = new Map<string, THREE.Texture>();
const textureLoads = new Map<string, Promise<void>>();
const CITY_ASPHALT = "/models/incoming/urban-traffic/images/f7a11eed4c9d2e047af8297ed9751e31.webp";
const CITY_ASPHALT_NORMAL = "/models/incoming/urban-traffic/images/ca90eb41d99d42e45accb18fd983c769.webp";
const CITY_PAVING = "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp";
const CITY_PAVING_NORMAL = "/models/incoming/urban-traffic/images/1a3ac683b0c28874c80d05cfd398be67.webp";
const BIGCITY_FLOOR_ATLAS = "/models/bigcity/images/04363231810a6784e8b22031f291e87f.webp";

function cityPackTexturePaths(manifest: MeshPackManifest): ReadonlySet<string> {
  const paths = new Set<string>();
  const add = (path: string | null): void => { if (path !== null) paths.add(path); };
  for (const batch of manifest.batches) {
    if (batch.layer !== "roads") continue;
    const material = batch.material;
    const source = material.base_color_texture;
    const replacedSurface = source !== null && (source.includes("/Asphalt010/")
      || source.includes("/Concrete034/") || source.includes("/PavingStones072/"));
    const marking = source?.includes("/road_marking_") ?? false;
    const metal = source?.includes("/Metal002/") ?? false;
    if (!replacedSurface && !marking && !metal) add(source);
    if (!replacedSurface && !metal) add(material.normal_texture);
    if (!marking && !metal) add(material.opacity_texture);
    if (!replacedSurface && !metal) add(material.orm_texture);
  }
  return paths;
}

async function cityGrassTexture(atlasUrl = BIGCITY_FLOOR_ATLAS): Promise<THREE.CanvasTexture> {
  const atlas = await textureLoader.loadAsync(atlasUrl);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 320;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("City terrain texture canvas is unavailable");
  context.drawImage(atlas.image as CanvasImageSource, 900, 500, 320, 320, 0, 0, 320, 320);
  atlas.dispose();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.flipY = false;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = 8;
  return texture;
}

function loadTexture(path: string, onReady: () => void): THREE.Texture {
  const cached = textureCache.get(path);
  if (cached !== undefined) return cached;
  let loaded!: () => void, failed!: (error: Error) => void;
  const ready = new Promise<void>((resolve, reject) => { loaded = resolve; failed = reject; });
  textureLoads.set(path, ready);
  void ready.catch(() => undefined);
  const texture = textureLoader.load(path, () => { loaded(); onReady(); }, undefined, () => failed(new Error(`OSM2World texture failed: ${path}`)));
  textureCache.set(path, texture);
  return texture;
}

function enuPosition(east: number, north: number, up: number): THREE.Vector3 { return new THREE.Vector3(east, up, -north); }

/**
 * Aggregate the scenario's declared building anchors into deduplicated
 * minimap squares. Each building contributes its anchor footprint (metres,
 * ENU); anchors landing in the same grid cell collapse to one square, so
 * hundreds of building components become a cheap static background. No
 * geometry is invented: a building without usable base vertices falls back
 * to a small square at its declared anchor, or is skipped entirely.
 */
function aggregateOperationsBuildings(buildings: readonly PublicBuilding[]): OperationsBuildingSquare[] {
  const CELL_M = 8;
  const byCell = new Map<string, OperationsBuildingSquare>();
  for (const building of buildings) {
    if (building == null || typeof building !== "object") continue;
    const vertices = Array.isArray(building.base_vertices) ? building.base_vertices : [];
    const flat = vertices
      .map((vertex) => vertex?.enu ?? null)
      .filter((enu): enu is { east_m: number; north_m: number; up_m: number } =>
        enu != null && Number.isFinite(enu.east_m) && Number.isFinite(enu.north_m));
    const square: OperationsBuildingSquare | null = flat.length > 0
      ? {
        id: `building:${building.building_id}`,
        x: flat.reduce((sum, enu) => sum + enu.east_m, 0) / flat.length,
        z: -flat.reduce((sum, enu) => sum + enu.north_m, 0) / flat.length,
        sizeM: Math.max(4, Math.min(28, Math.sqrt(
          Math.max(...flat.map((enu) => enu.east_m)) - Math.min(...flat.map((enu) => enu.east_m))) * 0.5
          + Math.sqrt(Math.max(...flat.map((enu) => enu.north_m)) - Math.min(...flat.map((enu) => enu.north_m))) * 0.5) * 2) * 0.5,
      }
      : Number.isFinite(building.anchor_east_m) && Number.isFinite(building.anchor_north_m)
        ? { id: `building:${building.building_id}`, x: building.anchor_east_m, z: -building.anchor_north_m, sizeM: 6 }
        : null;
    if (square === null) continue;
    const cell = `cell:${Math.round(square.x / CELL_M)}:${Math.round(square.z / CELL_M)}`;
    const existing = byCell.get(cell);
    if (existing === undefined) byCell.set(cell, { ...square, id: cell });
  }
  return [...byCell.values()];
}
function disposeMaterial(value: THREE.Material | THREE.Material[]): void { for (const material of Array.isArray(value) ? value : [value]) material.dispose(); }
function clearGroup(group: THREE.Group): void {
  group.traverse((object) => {
    if (object instanceof THREE.Mesh) {
      object.geometry.dispose();
      if (object.userData.packedTextures) {
        for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
          if (material instanceof THREE.MeshStandardMaterial) for (const texture of new Set([material.map, material.normalMap, material.alphaMap, material.roughnessMap, material.metalnessMap])) texture?.dispose();
        }
      }
      disposeMaterial(object.material);
    }
  });
  group.clear();
}
function toEntityKind(kind: OsmEntityKind): EntityKind { return kind; }

export function makeDaySkyTexture(onReady: () => void = () => undefined): THREE.Texture {
  let texture!: THREE.Texture;
  texture = new THREE.TextureLoader().load(`${OSM2WORLD_STYLE_BASE}/textures/sky/DaySkyHDRI041B_4K_TONEMAPPED.jpg`, () => {
    const source = texture.image as HTMLImageElement;
    const canvas = document.createElement("canvas");
    const sourceWidth = source.naturalWidth || source.width;
    const sourceHeight = source.naturalHeight || source.height;
    const scale = Math.min(1, 2048 / sourceWidth);
    canvas.width = Math.round(sourceWidth * scale);
    canvas.height = Math.round(sourceHeight * scale);
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (context === null) throw new Error("Day sky grading canvas is unavailable");
    // The source is overcast. A compact canvas filter adds sky blue and cloud contrast
    // in one draw instead of walking millions of pixels during scene loading.
    context.filter = "saturate(1.55) contrast(1.06)";
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    context.filter = "none";
    texture.image = canvas;
    texture.needsUpdate = true;
    onReady();
  });
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  return texture;
}

function makeDuskSkyTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 1024; canvas.height = 512;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) throw new Error("City dusk sky canvas is unavailable");
  const gradient = context.createLinearGradient(0, 0, 0, canvas.height);
  gradient.addColorStop(0, "#233951");
  gradient.addColorStop(0.48, "#49627d");
  gradient.addColorStop(0.74, "#8292a3");
  gradient.addColorStop(1, "#c5afa1");
  context.fillStyle = gradient;
  context.fillRect(0, 0, canvas.width, canvas.height);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  return texture;
}

function paintDuskSky(day: THREE.Texture, dusk: THREE.CanvasTexture,
                      reflection: THREE.CanvasTexture): void {
  const canvas = dusk.image as HTMLCanvasElement;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  const reflectionCanvas = reflection.image as HTMLCanvasElement;
  const reflectionContext = reflectionCanvas.getContext("2d", { willReadFrequently: true });
  if (context === null || reflectionContext === null) throw new Error("City dusk sky canvas is unavailable");
  context.drawImage(day.image as CanvasImageSource, 0, 0, canvas.width, canvas.height);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
  for (let y = 0; y < canvas.height; y++) {
    const horizon = y / canvas.height;
    for (let x = 0; x < canvas.width; x++) {
      const offset = (y * canvas.width + x) * 4;
      pixels.data[offset] = 2 + pixels.data[offset]! * 0.06 + horizon * 8;
      pixels.data[offset + 1] = 5 + pixels.data[offset + 1]! * 0.08 + horizon * 12;
      pixels.data[offset + 2] = 15 + pixels.data[offset + 2]! * 0.14 + horizon * 18;
    }
  }
  context.putImageData(pixels, 0, 0);
  // Three caches equirectangular cubes/PMREM by texture identity. A canvas
  // upload alone leaves the earlier placeholder environment in those caches.
  dusk.dispose();
  dusk.needsUpdate = true;
  reflectionContext.drawImage(canvas, 0, 0, reflectionCanvas.width, reflectionCanvas.height);
  reflection.dispose();
  reflection.needsUpdate = true;
}

function makeReflectionSky(mood: "day" | "dusk"): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 512; canvas.height = 256;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (context === null) throw new Error("City reflection canvas is unavailable");
  const gradient = context.createLinearGradient(0, 0, 0, canvas.height);
  gradient.addColorStop(0, mood === "day" ? "#90b2cf" : "#283e58");
  gradient.addColorStop(0.5, mood === "day" ? "#d7e5eb" : "#62758c");
  gradient.addColorStop(1, mood === "day" ? "#abb9bb" : "#b4a7a4");
  context.fillStyle = gradient;
  context.fillRect(0, 0, canvas.width, canvas.height);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  return texture;
}

function makeEntity(fixture: OsmFixture): THREE.Group {
  const group = new THREE.Group();
  group.name = `entity-${fixture.id}`;
  group.userData.target = { kind: "entity", id: fixture.id } satisfies TraceTarget;
  group.userData.entityKind = toEntityKind(fixture.kind);
  return group;
}

function applyTexture(material: THREE.MeshStandardMaterial, slot: "map" | "normalMap" | "roughnessMap" | "metalnessMap" | "alphaMap", path: string | null, onReady: () => void, clamp: boolean): void {
  const resolved = resolveTextureUrl(path);
  if (resolved === null) return;
  const texture = loadTexture(resolved, onReady);
  texture.flipY = false;
  texture.colorSpace = slot === "map" ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  texture.wrapS = clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
  texture.wrapT = clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
  material[slot] = texture;
}

export function meshMaterial(mesh: O2WMesh, onTextureReady: () => void): THREE.MeshStandardMaterial {
  const rgb = mesh.color();
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace),
    roughness: 0.86,
    metalness: 0.02,
    envMapIntensity: 0.55,
    alphaTest: mesh.transparency() ? 0.5 : 0,
    polygonOffset: mesh.transparency(),
    polygonOffsetFactor: mesh.transparency() ? -2 : 0,
    polygonOffsetUnits: mesh.transparency() ? -2 : 0,
    side: THREE.FrontSide,
  });
  const clamp = mesh.clampTextures();
  applyTexture(material, "map", mesh.baseColorTexture(), onTextureReady, clamp);
  applyTexture(material, "normalMap", mesh.normalTexture(), onTextureReady, clamp);
  applyTexture(material, "alphaMap", mesh.opacityTexture(), onTextureReady, clamp);
  const orm = resolveTextureUrl(mesh.ormTexture());
  if (orm !== null) {
    const texture = loadTexture(orm, onTextureReady);
    texture.wrapS = clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
    texture.wrapT = clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
    texture.flipY = false;
    texture.colorSpace = THREE.NoColorSpace;
    material.metalnessMap = texture;
    material.roughnessMap = texture;
  }
  return material;
}

export function trajectorySegments(trajectory: PublicTrajectory, tick: number | null): readonly [PublicTrajectory["samples"][number], PublicTrajectory["samples"][number]][] {
  const segments: [PublicTrajectory["samples"][number], PublicTrajectory["samples"][number]][] = [];
  for (let index = 1; index < trajectory.samples.length; index++) {
    const previous = trajectory.samples[index - 1], current = trajectory.samples[index];
    if (previous === undefined || current === undefined) continue;
    if (tick !== null && current.at.tick > tick) break;
    if (current.at.tick === previous.at.tick + 1 && current.at.sim_time_ns > previous.at.sim_time_ns) segments.push([previous, current]);
  }
  return segments;
}

/** Precipitation, drifting clouds and wind-driven vegetation need a render loop. */
function visualAnimationRequired(weather: CityWeather | null, vegetation: CityVegetationLayer | null,
    settings: CityWeatherSettings): boolean {
  return (weather?.requiresAnimation ?? false) || (vegetation !== null && settings.windMps > 0);
}

/** Authored display cameras look along the body +X axis. */
const OBSERVATION_MOUNT_ROTATION = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -Math.PI / 2);

export class PublicTraceMap {
  readonly viewer = null;
  readonly isAvailable = true;
  private readonly root: HTMLElement;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(46, 1, 0.4, 14000);
  private readonly renderer: THREE.WebGLRenderer;
  private readonly softwareRenderer: boolean;
  private updatingPreviewCamera = false;
  private operationsMonitor: OperationsMonitorHandle | null = null;
  private observationSelection: TraceTarget | null = null;
  private observationScene: MapScene | null = null;
  private observationView: MapView | null = null;
  private observationSource = "";
  private overviewCamera: THREE.PerspectiveCamera | null = null;
  private overviewTarget = new THREE.Vector3();
  private readonly sensorCamera = new THREE.PerspectiveCamera();
  private secondaryTarget: THREE.WebGLRenderTarget | null = null;
  private secondaryCanvas: HTMLCanvasElement | null = null;
  private secondaryPixels: Uint8Array | null = null;
  private previewRequested = false;
  private lastMonitorUpdateMs = -Infinity;
  private lastSecondaryRenderMs = -Infinity;
  private lastSecondaryToken = "";
  private lastMonitorToken = "";
  private readonly controls: OrbitControls;
  private readonly raycaster = new THREE.Raycaster();
  private readonly pointer = new THREE.Vector2();
  private readonly callbacks: MapCallbacks;
  private readonly world = new THREE.Group();
  private readonly overlays = new THREE.Group();
  private readonly horizon = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
    new THREE.MeshStandardMaterial({ color: CITY_LIGHTING.day.groundColor, roughness: 1, metalness: 0 }));
  private readonly outOfExtentGround = createOutOfExtentGround();
  private mappedGroundIdentity: { extent: MappedGroundExtent; manifestSha256: string; sourceSha256: string } | null = null;
  private readonly sun = new THREE.DirectionalLight(CITY_LIGHTING.day.sunColor, CITY_LIGHTING.day.sunIntensity);
  private readonly hemisphere = new THREE.HemisphereLight(CITY_LIGHTING.day.skyFill,
    CITY_LIGHTING.day.groundFill, CITY_LIGHTING.day.hemisphereIntensity);
  private readonly ambient = new THREE.AmbientLight(CITY_LIGHTING.day.ambientColor, CITY_LIGHTING.day.ambientIntensity);
  private readonly cityDaySky = createCityDaySky();
  private readonly cityDaySkyCameraPosition = new THREE.Vector3();
  private readonly cityDaySunDirection = new THREE.Vector3();
  private readonly duskSky = makeDuskSkyTexture();
  private readonly daySky: THREE.Texture;
  private readonly dayReflection = makeReflectionSky("day");
  private readonly duskReflection = makeReflectionSky("dusk");
  private cityMood: "day" | "dusk" = "day";
  private cityTimeOfDay: CityTimeOfDay = "day";
  /** Verified HDR lighting for building-render scenes; absent for pack and workspace scenes. */
  private calibratedEnvironment: CityCalibratedEnvironment | null = null;
  private lightingSample: CityLightingSample | null = null;
  private readonly cityEnvelope = new THREE.Box3();
  private readonly dynamic = new Map<string, THREE.Object3D>();
  private readonly signals = new Map<string, THREE.Object3D>();
  private readonly trajectories = new Map<string, THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial>>();
  private readonly networks = new Map<string, THREE.Line>();
  private buildingPresentation: THREE.Group | null = null;
  private vegetationPresentation: THREE.Group | null = null;
  /** Source greens, derived trees and authored grass clumps of a render scene. */
  private cityVegetationLayer: CityVegetationLayer | null = null;
  private authoredLandscapeLayer: CityAuthoredLandscapeRenderer | null = null;
  private readonly authoredLandscapeWind = createVegetationWindUniforms();
  private cityWeatherSettings: CityWeatherSettings = CITY_WEATHER_CLEAR;
  /** Visual wet film on the presentation ground; driven by the preview weather only. */
  private readonly groundWetness = createSurfaceWetnessUniforms();
  private roadPresentation: THREE.Group | null = null;
  private staticSignalPresentation: THREE.Group | null = null;
  private readonly buildingBounds = new Map<string, THREE.Box3>();
  private readonly previewCityCenter = new THREE.Vector3();
  private readonly previewDistrictFocus = new THREE.Vector3();
  private readonly previewReflectionFocus = new THREE.Vector3();
  private previewBuildingObstacles: THREE.Box3[] = [];
  private readonly previewFlightCameraOffsets = new Map<string, THREE.Vector3>();
  private readonly previewFlightLookDirections = new Map<string, THREE.Vector3>();
  private trafficPreview: CityTrafficPreview | null = null;
  private verifiedCityRoadAssets: VerifiedCityRoadAssets | null = null;
  private loadedCitySceneConfig: CitySceneConfig | null = null;
  private auditedTrafficSnapshot: TrafficPreviewDraftSnapshot | null = null;
  private auditedTrafficStale = false;
  private auditedTrafficRevision = 0;
  private auditedTrafficAbort: AbortController | null = null;
  private weather: CityWeather | null = null;
  private localReflections: CityLocalReflections | null = null;
  private operationsPreview: CityOperationsPreview | null = null;
  private presentationOptions: CityPresentationOptions = { weather: CITY_WEATHER_CLEAR, reflectionsEnabled: true };
  private workspaceConfig: CityWorkspaceConfig | null = null;
  private workspaceOperationIssues: PreviewValidationIssue[] = [];
  private workspaceObstacles: PlacementBox[] = [];
  private workspaceRoadPolygons: RoadPolygon[] = [];
  private selectedStaticObstacles: PlacementBox[] = [];
  private selectedPresentation: VerifiedStaticPresentation | null = null;
  /** Building source triangle ranges of the currently loaded selected scene,
   * bound to `sourceKey`; null outside the static-presentation branch and cleared
   * whenever the scene changes. Rooftop support is measured from these actual
   * triangles, never from the envelope. */
  private selectedRooftopMesh: ReadonlyMap<string, SourceBuildingTriangleRange[]> | null = null;
  private selectedFacilityGroup: THREE.Group | null = null;
  private selectedFacilityRevision = 0;
  private workspaceFollowId: string | null = null;
  private workspaceApplyGeneration = 0;
  private loadedCityScenePath: string | null = null;
  private previewSeconds = 30;
  private previewLastFrameAt = 0;
  private readonly resetPreviewClock = (): void => { this.previewLastFrameAt = performance.now(); };
  private previewPlaying = true;
  private previewPlaybackRate = 1;
  private previewFollowId: string | null = null;
  private readonly previewGroundFocusIndex = { bicycle: 0, pedestrian: 0 };
  private previewAnimation = 0;
  private hoverAnimation = 0;
  private pendingHover: PointerEvent | null = null;
  private readonly previewControls = document.createElement("div");
  private renderStreamer: BuildingRenderStreamer | null = null;
  private renderResolver: AssetResolver | null = null;
  private renderSceneActive = false;
  private packedSceneLoadAbort: AbortController | null = null;
  private readonly renderControls = document.createElement("div");
  private readonly renderStatus = document.createElement("span");
  private readonly renderMoodButton = document.createElement("button");
  private readonly renderWeatherPause = document.createElement("button");
  private renderWeatherControls: ReturnType<typeof createRenderWeatherControls> | null = null;
  private readonly previewClock = document.createElement("span");
  private readonly previewCounts = document.createElement("span");
  private readonly previewSourceNote = document.createElement("span");
  private readonly previewTime = document.createElement("input");
  private readonly previewPlayButton = document.createElement("button");
  private readonly previewSpeedButton = document.createElement("button");
  private readonly previewMoodButton = document.createElement("button");
  private readonly previewFlightSelect = document.createElement("select");
  private readonly sceneLoading = document.createElement("div");
  private readonly sceneLoadingStage = document.createElement("span");
  private readonly sceneLoadingCount = document.createElement("span");
  private readonly sceneLoadingBar = document.createElement("progress");
  private collisionBoxesVisible = false;
  private mode: CameraMode = "free";
  private follow: TraceTarget | null = null;
  private destroyed = false;
  private sourceKey = "";
  private conversionGeneration = 0;
  private projectionOrigin = { latitude_deg: 0, longitude_deg: 0 };
  private packedScene: LoadedMeshPack | null = null;
  /** App or Studio owns these verified resources; the map only mounts their group. */
  private nativePresentation: LoadedNativeCityPresentation | null = null;
  private packResolver: AssetResolver | null = null;
  private staticView: MapView | null = null;
  private readonly selectionOutline = new THREE.Box3Helper(new THREE.Box3(), 0x2d8ca8);
  private readonly resizeObserver: ResizeObserver;
  private readonly unsubscribeGroundLanguage: () => void;

  // P02 persistent parcel-ID overlay: one sprite per carrier entity that an
  // explicit business record binds (p02.business-identities/v1). Rebuilt only
  // when the declared set changes; per-frame work is a position copy from
  // the already-tracked entity objects. Labels show the authored parcel id —
  // never a dynamic entity id; runs without business records get no labels.
  private readonly p02CargoLabels = new Map<string, THREE.Sprite>();
  private p02CargoLabelGroup: THREE.Group | null = null;
  private p02CargoLabelsVisible = false;
  private p02BusinessFrame: SelectedFrameBusinessView | null = null;
  private p02NativeParcels: readonly BusinessIdentityView[] = [];
  private readonly p02DestinationMarkers = new Map<string, THREE.Sprite>();
  private p02DestinationGroup: THREE.Group | null = null;
  private readonly p02AircraftMarkers = new Map<string, THREE.Sprite>();
  private p02AircraftGroup: THREE.Group | null = null;
  /** Minimap building-square cache, keyed by the scenario it was built from. */
  private p02BuildingSquares: OperationsBuildingSquare[] | null = null;
  private p02BuildingSquaresScenario: PublicScenario | null = null;

  // Rolling preview frame statistics; `?perf=1` shows it, F9 toggles it.
  private readonly perfOverlay = new CityPerfOverlay({
    visible: new URLSearchParams(window.location.search).get("perf") === "1" });
  private lastPreviewFrameAt: number | null = null;

  constructor(container: HTMLElement, callbacks: MapCallbacks) {
    this.root = container; this.callbacks = callbacks;
    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance", alpha: false });
      const gl = this.renderer.getContext();
      const rendererInfo = gl.getExtension("WEBGL_debug_renderer_info");
      const rendererName = String(gl.getParameter(rendererInfo?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
      this.softwareRenderer = /swiftshader|llvmpipe|software/i.test(rendererName);
      this.root.dataset.renderBackend = this.softwareRenderer ? "software" : "hardware";
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      // The scene root never moves; avoid forcing every descendant matrix each frame.
      this.scene.matrixAutoUpdate = false;
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = CITY_LIGHTING.day.exposure;
      this.renderer.shadowMap.enabled = !this.softwareRenderer;
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, this.softwareRenderer ? 1 : 1.5));
      document.addEventListener("visibilitychange", this.resetPreviewClock);
      container.append(this.renderer.domElement);
      this.perfOverlay.attach(container);
      window.addEventListener("keydown", event => { if (event.key === "F9") this.perfOverlay.toggle(); });
      this.sceneLoading.className = "city-scene-loading";
      this.sceneLoading.hidden = true;
      this.sceneLoading.setAttribute("role", "status");
      this.sceneLoadingStage.className = "city-scene-loading-stage";
      this.sceneLoadingCount.className = "city-scene-loading-count";
      this.sceneLoadingBar.max = 1;
      this.sceneLoadingBar.setAttribute("aria-label", "城市加载进度");
      const loadingHead = document.createElement("div");
      loadingHead.className = "city-scene-loading-head";
      loadingHead.append(this.sceneLoadingStage, this.sceneLoadingCount);
      this.sceneLoading.append(loadingHead, this.sceneLoadingBar);
      container.append(this.sceneLoading);
      this.previewControls.className = "city-preview-controls";
      this.previewControls.hidden = true;
      const playbackRow = document.createElement("div"); playbackRow.className = "city-preview-row";
      const cameraRow = document.createElement("div"); cameraRow.className = "city-preview-row";
      this.previewPlayButton.type = "button"; this.previewPlayButton.textContent = "暂停";
      this.previewPlayButton.addEventListener("click", () => {
        this.previewPlaying = !this.previewPlaying;
        this.previewLastFrameAt = performance.now();
        this.previewPlayButton.textContent = this.previewPlaying ? "暂停" : "播放";
        if (!this.previewPlaying) this.renderPreviewFrame();
      });
      this.previewSpeedButton.type = "button"; this.previewSpeedButton.textContent = "1×";
      this.previewSpeedButton.addEventListener("click", () => {
        this.previewPlaybackRate = this.previewPlaybackRate === 0.5 ? 1 : this.previewPlaybackRate === 1 ? 2 : 0.5;
        this.previewSpeedButton.textContent = `${this.previewPlaybackRate}×`;
      });
      this.previewTime.type = "range"; this.previewTime.min = "0"; this.previewTime.max = "120";
      this.previewTime.step = "0.1"; this.previewTime.value = "30";
      this.previewTime.setAttribute("aria-label", "城市演示时间");
      this.previewTime.addEventListener("input", () => {
        this.previewSeconds = Number(this.previewTime.value);
        if (this.workspaceConfig !== null) {
          this.previewPlaying = false;
          this.previewPlayButton.textContent = "播放";
        }
        this.previewLastFrameAt = performance.now();
        this.renderPreviewFrame();
      });
      const streetButton = document.createElement("button");
      streetButton.type = "button"; streetButton.textContent = "街道视角";
      streetButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewStreet(); this.renderPreviewFrame(); });
      const streetLightingButton = document.createElement("button");
      streetLightingButton.type = "button"; streetLightingButton.textContent = "路灯与信号灯";
      streetLightingButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewStreetLighting(); this.renderPreviewFrame(); });
      const signalButton = document.createElement("button");
      signalButton.type = "button"; signalButton.textContent = "信号灯近景";
      signalButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewSignal(); this.renderPreviewFrame(); });
      const vehicleLightsButton = document.createElement("button");
      vehicleLightsButton.type = "button"; vehicleLightsButton.textContent = "车灯近景";
      vehicleLightsButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewVehicleLights(); this.renderPreviewFrame(); });
      const advertisingButton = document.createElement("button");
      advertisingButton.type = "button"; advertisingButton.textContent = "灯光街区";
      advertisingButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewAdvertising(); this.renderPreviewFrame(); });
      const overviewButton = document.createElement("button");
      overviewButton.type = "button"; overviewButton.textContent = "城市总览";
      overviewButton.addEventListener("click", () => {
        this.previewFollowId = null;
        this.clearWorkspaceFollow();
        this.focusPreviewCityDistrict();
        this.renderPreviewFrame();
      });
      const collisionButton = document.createElement("button");
      collisionButton.type = "button"; collisionButton.textContent = "显示碰撞盒";
      collisionButton.addEventListener("click", () => {
        this.collisionBoxesVisible = !this.collisionBoxesVisible;
        collisionButton.textContent = this.collisionBoxesVisible ? "隐藏碰撞盒" : "显示碰撞盒";
        if (this.buildingPresentation !== null) setCollisionBoxesVisible(this.buildingPresentation, this.collisionBoxesVisible);
        this.trafficPreview?.setCollisionBoxesVisible(this.collisionBoxesVisible);
        this.renderPreviewFrame();
      });
      this.previewMoodButton.type = "button";
      this.previewMoodButton.textContent = "切换日景";
      this.previewMoodButton.addEventListener("click", () => {
        this.cycleTimeOfDay();
        if (this.workspaceConfig === null) this.renderPreviewFrame();
      });
      this.previewFlightSelect.setAttribute("aria-label", "跟随无人机");
      this.previewFlightSelect.addEventListener("change", () => {
        this.selectObservationTarget(this.previewFlightSelect.value ? { kind: "entity", id: this.previewFlightSelect.value } : null);
        if (this.workspaceConfig !== null) this.followWorkspaceEntity(this.previewFlightSelect.value || null);
        else if (this.previewFlightSelect.value) { this.focusPreviewFlight(this.previewFlightSelect.value); this.renderPreviewFrame(); }
      });
      const bicycleButton = document.createElement("button");
      bicycleButton.type = "button"; bicycleButton.textContent = "跟随自行车";
      bicycleButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewGroundEntity("bicycle"); this.renderPreviewFrame(); });
      const pedestrianButton = document.createElement("button");
      pedestrianButton.type = "button"; pedestrianButton.textContent = "跟随行人";
      pedestrianButton.addEventListener("click", () => { this.clearWorkspaceFollow(); this.focusPreviewGroundEntity("pedestrian"); this.renderPreviewFrame(); });
      playbackRow.append(this.previewPlayButton, this.previewSpeedButton, this.previewTime,
                         this.previewClock, this.previewCounts);
      cameraRow.append(streetButton, streetLightingButton, signalButton, vehicleLightsButton, advertisingButton,
                       this.previewFlightSelect, bicycleButton,
                       pedestrianButton, overviewButton, this.previewMoodButton, collisionButton);
      this.previewControls.append(playbackRow, cameraRow, this.previewSourceNote);
      container.append(this.previewControls);
      // Render scenes without a road replay use a slim camera bar.
      this.renderControls.className = "city-preview-controls";
      this.renderControls.hidden = true;
      const renderRow = document.createElement("div"); renderRow.className = "city-preview-row";
      this.renderMoodButton.type = "button"; this.renderMoodButton.textContent = "切换夜景";
      this.renderMoodButton.setAttribute("aria-label", "切换建筑渲染场景时段");
      this.renderMoodButton.addEventListener("click", () => { this.cycleTimeOfDay(); });
      const renderStreetButton = document.createElement("button");
      renderStreetButton.type = "button"; renderStreetButton.textContent = "街道视角";
      renderStreetButton.addEventListener("click", () => {
        this.focusRenderStreet(); this.renderStaticFrame(); this.renderStreamer?.update(this.camera);
      });
      const renderAerialButton = document.createElement("button");
      renderAerialButton.type = "button"; renderAerialButton.textContent = "航拍视角";
      renderAerialButton.addEventListener("click", () => {
        if (this.packedScene !== null) this.frameExtent(this.packedScene.manifest.extent);
        this.renderStaticFrame(); this.renderStreamer?.update(this.camera);
      });
      const defaultSceneButton = document.createElement("button");
      defaultSceneButton.type = "button"; defaultSceneButton.textContent = "切换默认场景";
      defaultSceneButton.addEventListener("click", () => {
        gotoCityScene("/city-presentation/default-scene-v1.json");
      });
      this.renderStatus.setAttribute("aria-label", "建筑渲染资产加载状态");
      renderRow.append(this.renderMoodButton, renderStreetButton, renderAerialButton,
                        defaultSceneButton, this.renderStatus);
      if (this.callbacks.onEnvironmentEdit === undefined) {
        renderRow.append(...createCityPreviewSceneButtons());
      }
      this.renderWeatherControls = createRenderWeatherControls(settings => {
        if (this.workspaceConfig !== null) {
          this.applyWorkspaceEnvironment({ ...this.workspaceConfig.environment, ...settings });
          return;
        }
        this.configureCityPresentation({ weather: settings });
      });
      this.renderWeatherPause.type = "button";
      this.renderWeatherPause.addEventListener("click", () => {
        this.previewPlaying = !this.previewPlaying;
        this.previewLastFrameAt = performance.now();
        this.syncPreviewAnimation();
      });
      this.renderWeatherControls.element.append(this.renderWeatherPause);
      this.renderControls.append(this.renderWeatherControls.element);
      this.renderControls.append(renderRow);
      container.append(this.renderControls);
      const renderSceneButton = document.createElement("button");
      renderSceneButton.type = "button"; renderSceneButton.textContent = "建筑渲染场景";
      renderSceneButton.addEventListener("click", () => {
        gotoCityScene("/city-presentation/building-render-scene-v1.json");
      });
      cameraRow.append(renderSceneButton);
      if (this.callbacks.onEnvironmentEdit === undefined) {
        cameraRow.append(...createCityPreviewSceneButtons());
      }
      // Unclassified ground has no invented pavement; verified walkbed owns its texture.
      this.horizon.rotation.x = -Math.PI / 2;
      this.horizon.position.y = -0.025;
      this.horizon.renderOrder = -100;
      this.horizon.material.depthWrite = false;
      this.horizon.name = "presentation-ground";
      this.horizon.visible = false;
      applySurfaceWetness(this.horizon.material, this.groundWetness, { maxDarkening: 0.35, minRoughness: 0.3 });
      this.horizon.receiveShadow = true;
      this.selectionOutline.visible = false;
      this.cityDaySky.onBeforeRender = (_renderer, _scene, camera) => {
        camera.getWorldPosition(this.cityDaySkyCameraPosition);
        centerCityDaySky(this.cityDaySky, this.cityDaySkyCameraPosition);
        this.cityDaySky.updateMatrixWorld(true);
      };
      this.scene.add(this.world, this.overlays, this.horizon, this.outOfExtentGround, this.cityDaySky, this.selectionOutline);
      this.scene.background = new THREE.Color(0x92a8b3);
      this.daySky = makeDaySkyTexture(() => {
        if (this.destroyed) { this.daySky.dispose(); return; }
        const canvas = this.dayReflection.image as HTMLCanvasElement;
        const context = canvas.getContext("2d");
        if (context === null) throw new Error("City reflection canvas is unavailable");
        context.drawImage(this.daySky.image as CanvasImageSource, 0, 0, canvas.width, canvas.height);
        this.dayReflection.dispose(); // Evict the PMREM made from the initial gradient.
        this.dayReflection.needsUpdate = true;
        paintDuskSky(this.daySky, this.duskSky, this.duskReflection);
        if (this.calibratedEnvironment === null) this.scene.background = this.cityMood === "day" ? null : this.duskSky;
        if (this.buildingPresentation !== null) setCityBuildingLighting(this.buildingPresentation,
          this.cityTimeOfDay, this.cityMood === "dusk" ? this.duskReflection : this.dayReflection);
        if (this.buildingPresentation !== null && this.renderSceneActive) this.lightRenderBuildings(this.buildingPresentation);
        this.localReflections?.invalidate();
        this.root.dataset.skyReady = "true";
        if (this.root.dataset.sceneReady === "true") {
          if (this.trafficPreview !== null) this.renderPreviewFrame();
          else this.renderStaticFrame();
        }
      });
      this.scene.backgroundIntensity = 1;
      this.scene.backgroundRotation.y = 0;
      this.scene.fog = new THREE.Fog(0xb7cbd9, 380, 3200);
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = false; this.controls.minDistance = 2.5; this.controls.maxDistance = 4500;
      this.controls.maxPolarAngle = Math.PI * 0.48;
      this.controls.addEventListener("start", () => {
        this.previewFollowId = null;
        this.clearWorkspaceFollow();
        this.root.dataset.previewFollowId = "";
        this.previewFlightSelect.value = "";
      });
      this.controls.addEventListener("end", () => {
        this.recenterCityReflections();
        if (this.renderStreamer !== null) this.renderStreamer.update(this.camera);
        if (this.root.dataset.sceneReady === "true") {
          if (this.trafficPreview !== null) this.renderPreviewFrame();
          else this.renderStaticFrame();
        }
      });
      this.controls.addEventListener("change", () => {
        if (this.updatingPreviewCamera) return;
        this.root.dataset.previewCameraX = this.camera.position.x.toFixed(3);
        this.root.dataset.previewCameraZ = this.camera.position.z.toFixed(3);
        this.focusSunShadow();
        this.weather?.update(this.previewSeconds);
        if (this.roadPresentation !== null) setCityRoadLighting(this.roadPresentation, this.camera, this.cityTimeOfDay);
        if (this.trafficPreview !== null) this.root.dataset.renderedTrafficSignalCount = String(
          this.trafficPreview.setSignalVisibility(this.camera, this.staticView?.layers.roads ?? true));
        if (this.trafficPreview !== null) this.root.dataset.headlightBeamCount = String(
          this.trafficPreview.setVehicleLighting(this.camera, this.cityTimeOfDay));
        if (this.root.dataset.sceneReady === "true") this.renderObservation();
      });
      this.scene.add(this.hemisphere, this.ambient);
      this.sun.castShadow = !this.softwareRenderer;
      this.sun.shadow.mapSize.set(2048, 2048);
      this.sun.shadow.bias = -0.0001;
      this.sun.shadow.normalBias = 0.025;
      this.scene.add(this.sun, this.sun.target);
      let pickStart: { x: number; y: number; pointerId: number } | null = null;
      this.renderer.domElement.addEventListener("pointerdown", (event) => {
        if (event.button === 0) pickStart = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
      });
      this.renderer.domElement.addEventListener("pointerup", (event) => {
        const start = pickStart; pickStart = null;
        if (start !== null && start.pointerId === event.pointerId
          && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 6) {
          this.selectObservationTarget(this.intersect(event));
        }
      });
      this.renderer.domElement.addEventListener("pointercancel", () => { pickStart = null; });
      this.renderer.domElement.addEventListener("pointermove", (event) => {
        if (event.buttons !== 0) return;
        this.pendingHover = event;
        if (this.hoverAnimation !== 0) return;
        this.hoverAnimation = requestAnimationFrame(() => {
          this.hoverAnimation = 0;
          const latest = this.pendingHover;
          this.pendingHover = null;
          if (!this.destroyed && latest !== null) this.callbacks.onHover(this.intersect(latest));
        });
      });
      this.renderer.domElement.addEventListener("pointerleave", () => {
        cancelAnimationFrame(this.hoverAnimation); this.hoverAnimation = 0; this.pendingHover = null;
        this.callbacks.onHover(null);
      });
      this.renderer.domElement.addEventListener("contextmenu", (event) => { event.preventDefault(); const rect = this.renderer.domElement.getBoundingClientRect(); this.callbacks.onContextMenu(this.intersect(event), event.clientX - rect.left, event.clientY - rect.top); });
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(container); this.resize();
      this.operationsMonitor = mountOperationsMonitor(container, {
        onSelect: target => this.selectObservationTarget(target),
        onNavigate: point => this.navigateObservation(point),
        onCameraMode: mode => { this.setCameraMode(mode); this.renderObservation(true); },
        onPreviewToggle: shown => { this.previewRequested = shown; this.renderObservation(true); },
        onSwap: () => { this.setCameraMode(this.mode === "cockpit" ? "free" : "cockpit"); this.renderObservation(true); },
      });
    } catch { this.callbacks.onUnavailable?.(); throw new Error("OSM2World viewer could not initialize"); }
    this.unsubscribeGroundLanguage = subscribeLanguage(() => {
      this.refreshMappedExtentLegend();
      this.renderObservation(true);
    });
  }
  private setCityMood(mood: "day" | "dusk", refreshReflections = true): void {
    this.setCityTimeOfDay(mood === "day" ? "day" : "twilight", refreshReflections);
  }
  private cycleTimeOfDay(): void {
    if (this.calibratedEnvironment !== null) {
      this.setTimeOfDay(NEXT_TIME_OF_DAY[this.cityTimeOfDay]);
      return;
    }
    // The uncalibrated pack preview only distinguishes day from dusk.
    this.setTimeOfDay(this.cityTimeOfDay === "day" ? "twilight" : "day");
  }
  /** Time-of-day switch shared by the render-bar button and workspace drafts. */
  private setTimeOfDay(timeOfDay: CityTimeOfDay): void {
    if (this.workspaceConfig !== null) {
      this.applyWorkspaceEnvironment({ ...this.workspaceConfig.environment, timeOfDay });
      return;
    }
    this.setCityTimeOfDay(timeOfDay);
    this.renderStaticFrame();
    this.renderStreamer?.update(this.camera);
  }
  /** With an applied workspace, the draft owner applies environment edits (and reports
   * their issues); the map only forwards the request. */
  private applyWorkspaceEnvironment(environment: CityDraftEnvironment): void {
    if (this.workspaceConfig === null) {
      throw new Error("Workspace environment edits require an applied workspace configuration");
    }
    const forward = this.callbacks.onEnvironmentEdit;
    if (forward === undefined) throw new Error("Workspace environment edits need an onEnvironmentEdit owner");
    forward(structuredClone(environment));
  }
  /** Move the existing row into the active dock without replacing its handlers. */
  private mountRenderWeatherControls(renderScene: boolean, trafficPresent: boolean): void {
    if (this.renderWeatherControls === null) return;
    const dock = renderScene && trafficPresent ? this.previewControls : this.renderControls;
    dock.append(this.renderWeatherControls.element);
  }
  /** Render-bar mode note: workspace draft edit vs unsaved visual preview. */
  private syncWorkspaceRenderBar(): void {
    const note = this.renderWeatherControls?.element.querySelector<HTMLSpanElement>(
      "span[data-role='render-weather-note']") ?? null;
    if (note === null) return;
    if (this.workspaceConfig === null) {
      note.textContent = "未保存的视觉预览";
      note.dataset.mode = "preview";
      this.renderWeatherControls!.element.setAttribute("aria-label", "视觉天气预览（不保存为仿真配置）");
    } else {
      note.textContent = "草稿环境 · 修改计入工作区草稿";
      note.dataset.mode = "workspace";
      this.renderWeatherControls!.element.setAttribute("aria-label", "草稿环境编辑（修改计入工作区草稿，需在工作台保存）");
    }
  }
  /** Day, twilight and night; subsystems without a twilight/night split receive the dusk mood. */
  private setCityTimeOfDay(timeOfDay: CityTimeOfDay, refreshReflections = true): void {
    const mood = timeOfDay === "day" ? "day" : "dusk";
    const lighting = CITY_LIGHTING[mood];
    this.cityMood = mood;
    this.cityTimeOfDay = timeOfDay;
    this.root.dataset.cityMood = mood;
    this.root.dataset.cityTimeOfDay = timeOfDay;
    const nextLabel = this.workspaceConfig !== null ? NEXT_TIME_OF_DAY_LABEL[timeOfDay]
      : this.calibratedEnvironment !== null ? NEXT_TIME_OF_DAY_LABEL[timeOfDay]
      : mood === "dusk" ? "切换日景" : "切换夜景";
    this.previewMoodButton.textContent = nextLabel;
    this.renderMoodButton.textContent = nextLabel;
    this.cityDaySky.visible = mood === "day";
    this.scene.background = mood === "dusk" ? this.duskSky : null;
    this.scene.environment = mood === "dusk" ? this.duskReflection : this.dayReflection;
    this.scene.environmentIntensity = lighting.environmentIntensity;
    this.scene.backgroundIntensity = lighting.backgroundIntensity;
    this.scene.fog = mood === "dusk"
      ? new THREE.Fog(0x111d31, 550, 2200) : new THREE.Fog(0xb7cbd9, 380, 3200);
    this.sun.color.setHex(lighting.sunColor);
    this.updateSunIntensity();
    this.focusSunShadow();
    this.hemisphere.color.setHex(lighting.skyFill);
    this.hemisphere.groundColor.setHex(lighting.groundFill);
    this.hemisphere.intensity = lighting.hemisphereIntensity;
    this.ambient.color.setHex(lighting.ambientColor);
    this.ambient.intensity = lighting.ambientIntensity;
    this.renderer.toneMappingExposure = lighting.exposure;
    // A selected OSM region contains gaps between surveyed surface polygons.
    // Keep those gaps visually neutral without classifying them as pavement.
    this.horizon.material.color.setHex(this.sourceKey.startsWith("authoring:")
      ? mood === "dusk" ? 0x3d4547 : 0x666b66 : lighting.groundColor);
    for (const object of this.world.children) {
      if (!(object instanceof THREE.Mesh) || object.userData.layer !== "terrain") continue;
      const material = Array.isArray(object.material) ? object.material[0] : object.material;
      if (!(material instanceof THREE.MeshStandardMaterial)) continue;
      const kind = material.userData.cityTerrainKind;
      material.color.setHex(mood === "dusk" ? kind === "grass" ? 0x455043 : 0x647078
        : kind === "grass" ? 0x70836d : 0xffffff);
    }
    if (this.buildingPresentation !== null) setCityBuildingLighting(this.buildingPresentation, timeOfDay,
      mood === "dusk" ? this.duskReflection : this.dayReflection);
    if (this.buildingPresentation !== null && this.renderSceneActive) this.lightRenderBuildings(this.buildingPresentation);
    const storefront = this.buildingPresentation?.userData.storefrontLights as THREE.InstancedMesh | undefined;
    if (storefront !== undefined && this.staticView !== null) {
      storefront.visible &&= this.staticView.layers.buildings
        && !this.staticView.hiddenEntities.has("building:visual-storefronts")
        && (this.staticView.isolate === null || (this.staticView.isolate.kind === "building"
          && this.staticView.isolate.id === "visual-storefronts"));
    }
    if (this.roadPresentation !== null) setCityRoadLighting(this.roadPresentation, this.camera, timeOfDay);
    if (this.trafficPreview !== null) this.root.dataset.headlightBeamCount = String(
      this.trafficPreview.setVehicleLighting(this.camera, timeOfDay));
    if (this.buildingPresentation !== null) setCityAdvertisingLighting(this.buildingPresentation, timeOfDay);
    this.weather?.setMood(mood);
    if (this.calibratedEnvironment !== null) this.applyCalibratedLighting();
    if (refreshReflections) {
      this.localReflections?.invalidate();
    }
  }
  /** One calibrated sample drives lights, exposure, sky, environment, fog, shadow and building materials. */
  private applyCalibratedLighting(): void {
    const environment = this.calibratedEnvironment!.textureFor(this.cityTimeOfDay);
    const sample = sampleCityLighting({ solar: cityVisualSolar(this.cityTimeOfDay),
      weather: { kind: "visual-settings", settings: this.presentationOptions.weather ?? CITY_WEATHER_CLEAR } });
    this.lightingSample = sample;
    setCityWindowLitFraction(this.cityTimeOfDay);
    applyCityLightingCalibration({ scene: this.scene, renderer: this.renderer, sun: this.sun,
      hemisphere: this.hemisphere, ambient: this.ambient }, sample, environment);
    applyCitySkyCalibration(this.cityDaySky, sample);
    this.scene.background = null;
    const fog = CALIBRATED_FOG[this.cityTimeOfDay];
    this.scene.fog = new THREE.Fog(fog.color, fog.near, fog.far);
    // Weather owns visibility and cloud/particle colour under the same lighting sample.
    this.weather?.setLighting(sample);
    this.focusSunShadow();
    this.horizon.material.color.setHex(CALIBRATED_GROUND_ALBEDO.unclassified);
    for (const object of this.world.children) {
      if (!(object instanceof THREE.Mesh) || object.userData.layer !== "terrain") continue;
      const material = Array.isArray(object.material) ? object.material[0] : object.material;
      if (!(material instanceof THREE.MeshStandardMaterial)) continue;
      material.color.setHex(material.userData.cityTerrainKind === "grass"
        ? CALIBRATED_GROUND_ALBEDO.grass : CALIBRATED_GROUND_ALBEDO.paving);
    }
    if (this.buildingPresentation !== null) this.lightRenderBuildings(this.buildingPresentation);
  }
  /** Streamed buildings attach later; each receives the current appearance once. */
  private lightRenderBuildings(root: THREE.Object3D): void {
    if (this.lightingSample !== null && this.calibratedEnvironment !== null) {
      setCityBuildingCalibration(root, this.lightingSample,
        this.calibratedEnvironment.textureFor(this.cityTimeOfDay));
    } else {
      setBuildingRenderLighting(root, this.cityTimeOfDay,
        this.cityMood === "dusk" ? this.duskReflection : this.dayReflection);
    }
  }
  private updateSunIntensity(): void {
    if (this.lightingSample !== null) {
      this.sun.intensity = this.lightingSample.sunIntensity;
      return;
    }
    this.sun.intensity = CITY_LIGHTING[this.cityMood].sunIntensity
      * (this.weather?.sunlightFactor ?? 1);
  }
  private setCityWeather(settings: CityWeatherSettings): void {
    this.cityWeatherSettings = settings;
    this.groundWetness.uWetness.value = surfaceWetnessFromWeather(settings);
    this.weather?.setWeather(settings);
    this.cityVegetationLayer?.setWeather(settings);
    setVegetationWind(this.authoredLandscapeWind, settings.windMps, settings.windDirectionDeg, this.previewSeconds);
    setCityDaySkyWeather(this.cityDaySky, settings);
    if (this.calibratedEnvironment !== null) this.applyCalibratedLighting();
    this.localReflections?.invalidate();
  }
  /** Spend the sun shadow map on the camera's street block, not the full city extent. */
  private focusSunShadow(): void {
    if (this.lightingSample !== null) {
      focusCityCalibratedSun(this.sun, this.lightingSample, this.cityEnvelope, this.controls.target, this.camera);
      return;
    }
    const focus = this.controls.target;
    const radius = THREE.MathUtils.clamp(this.camera.position.distanceTo(focus) * 0.7, 55, 750);
    this.sun.position.set(focus.x - 900, focus.y + (this.cityMood === "dusk" ? 380 : 620), focus.z + 700);
    this.sun.target.position.copy(focus);
    this.cityDaySunDirection.subVectors(this.sun.position, this.sun.target.position);
    setCityDaySkySunDirection(this.cityDaySky, this.cityDaySunDirection);
    Object.assign(this.sun.shadow.camera, {
      left: -radius, right: radius, top: radius, bottom: -radius, near: 10, far: 3000,
    });
    this.sun.shadow.camera.updateProjectionMatrix();
  }
  private resize(): void { const width = this.root.clientWidth || 640; const height = this.root.clientHeight || 480; this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false); this.renderObservation(); }
  private intersect(event: PointerEvent): TraceTarget | null {
    const rect = this.renderer.domElement.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    this.pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
    if (this.camera.userData.observationHorizontalMirror === true) this.pointer.x *= -1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const candidates = visiblePickObjects([...this.dynamic.values(), ...this.signals.values(), ...this.world.children,
      ...(this.buildingPresentation === null ? [] : [this.buildingPresentation]),
      ...(this.trafficPreview === null ? [] : [this.trafficPreview.group]),
      ...(this.operationsPreview === null ? [] : [this.operationsPreview.group])]);
    const hit = this.raycaster.intersectObjects(candidates, false)[0];
    if (hit?.instanceId !== undefined && hit.object.userData.roofInstances !== undefined) {
      return hit.object.userData.roofInstances[hit.instanceId].owner.userData.target as TraceTarget;
    }
    if (hit?.faceIndex !== undefined && hit.faceIndex !== null && hit.object.userData.ranges !== undefined) {
      const ranges = hit.object.userData.ranges as { end: number; target: TraceTarget | null }[];
      return ranges.find(range => hit.faceIndex! < range.end)?.target ?? null;
    }
    let object = hit?.object;
    while (object !== undefined && object !== null) {
      if (object.userData.target !== undefined) return object.userData.target as TraceTarget;
      object = object.parent ?? undefined;
    }
    return null;
  }
  setFollow(target: TraceTarget | null): void { this.follow = target; }
  setCameraMode(mode: CameraMode): void {
    if (mode === "cockpit" && this.observationMount(this.observationTarget(), this.camera.aspect) === null) {
      this.root.dataset.observationCameraState = "unavailable";
      this.callbacks?.onObservationModeChange?.(this.mode);
      return;
    }
    if (this.mode === "free" && mode !== "free") {
      this.overviewCamera = this.camera.clone();
      this.overviewTarget.copy(this.controls.target);
    }
    this.mode = mode;
    this.root.dataset.observationCameraMode = mode;
    this.controls.enabled = mode === "free";
    if (mode !== "cockpit") {
      this.camera.fov = 46; this.camera.near = 0.4; this.camera.far = 14000;
      this.camera.updateProjectionMatrix();
      delete this.root.dataset.observationCameraSource; delete this.root.dataset.observationSensorId;
    }
    if (mode === "free") {
      this.follow = null; this.previewFollowId = null; this.workspaceFollowId = null;
    }
    if (mode === "free") {
      if (this.overviewCamera !== null) {
        this.camera.position.copy(this.overviewCamera.position);
        this.camera.quaternion.copy(this.overviewCamera.quaternion);
        this.controls.target.copy(this.overviewTarget);
      }
      this.camera.scale.set(1, 1, 1); this.camera.userData.observationHorizontalMirror = false; this.camera.up.set(0, 1, 0);
      this.camera.fov = 46; this.camera.near = 0.4; this.camera.far = 14000;
      this.camera.updateProjectionMatrix();
      this.updatingPreviewCamera = true;
      try { this.controls.update(); } finally { this.updatingPreviewCamera = false; }
    }
    this.updateCamera();
    this.callbacks?.onObservationModeChange?.(this.mode);
  }
  private observationTarget(): THREE.Object3D | undefined {
    const selected = this.observationSelection ?? this.observationView?.selected ?? this.follow;
    const id = selected?.kind === "entity" ? selected.id : this.workspaceFollowId ?? this.previewFollowId;
    if (id === null || id === undefined) return undefined;
    return this.dynamic.get(`entity:${id}`)
      ?? this.operationsPreview?.group.children.find(child => child.userData.target?.id === id)
      ?? this.trafficPreview?.group.children.find(child => child.userData.target?.id === id);
  }
  private observationMount(target: THREE.Object3D | undefined, aspect: number): { mount: CameraMount; label: string; sensorId?: string } | null {
    if (target === undefined || !target.visible || target.userData.entityKind !== "uav") return null;
    const id = target.userData.target?.id as string;
    if (this.observationScene?.scenario !== null && this.observationScene?.scenario !== undefined) {
      const sensor = declaredSensorMount(this.observationScene.scenario, id, aspect);
      return sensor === null ? null : { mount: sensor.mount, label: "模拟 RGB · 声明传感器姿态 · 非记录帧", sensorId: sensor.sensorId };
    }
    // An authored display camera is explicitly separate from a Provider sensor.
    const localBounds = new THREE.Box3();
    target.updateWorldMatrix(true, true);
    const inverse = target.matrixWorld.clone().invert();
    const relative = new THREE.Matrix4(), box = new THREE.Box3();
    target.traverse(child => {
      if (!(child instanceof THREE.Mesh) || !child.visible) return;
      // Entity visual geometries are immutable after load, so measure each one once.
      if (child.geometry.boundingBox === null) child.geometry.computeBoundingBox();
      if (child.geometry.boundingBox !== null) localBounds.union(box.copy(child.geometry.boundingBox)
        .applyMatrix4(relative.multiplyMatrices(inverse, child.matrixWorld)));
    });
    if (localBounds.isEmpty()) return null;
    const rotation = OBSERVATION_MOUNT_ROTATION;
    return { mount: { kind: "rigid", offsetBodyM: [localBounds.max.x + 0.15, (localBounds.min.y + localBounds.max.y) / 2, 0],
      rotationBodyFromMountXyzw: [rotation.x, rotation.y, rotation.z, rotation.w], horizontalMirror: false,
      verticalFovDegrees: 60, aspect, nearM: 0.05, farM: 14000 }, label: "模拟 RGB · 作者刚性展示相机 · 非声明传感器" };
  }
  private updateCamera(): void {
    const target = this.observationTarget();
    if (target === undefined || !target.visible) {
      if (this.mode !== "free") {
        this.setCameraMode("free");
        this.root.dataset.observationCameraState = "unavailable";
      }
      return;
    }
    const center = target.getWorldPosition(new THREE.Vector3());
    const rotation = target.getWorldQuaternion(new THREE.Quaternion());
    if (this.mode === "cockpit") {
      const sensor = this.observationMount(target, this.camera.aspect);
      if (sensor === null) { this.setCameraMode("free"); this.root.dataset.observationCameraState = "unavailable"; return; }
      applyMountedCamera(this.camera, center, rotation, sensor.mount);
      this.root.dataset.observationCameraState = "ready";
      this.root.dataset.observationCameraSource = sensor.label;
      this.root.dataset.observationSensorId = sensor.sensorId ?? "";
    } else if (this.mode === "chase") {
      this.camera.scale.set(1, 1, 1); this.camera.userData.observationHorizontalMirror = false; this.camera.up.set(0, 1, 0);
      const size = new THREE.Box3().setFromObject(target).getSize(new THREE.Vector3()).length();
      const distance = Math.max(4, size * 2);
      this.camera.position.copy(center).add(new THREE.Vector3(-distance, distance * 0.6, distance).applyQuaternion(rotation));
      this.camera.lookAt(center);
    } else if (this.follow !== null) {
      this.camera.up.set(0, 1, 0);
      this.camera.position.add(center.clone().sub(this.controls.target));
      this.controls.target.copy(center); this.controls.update();
    }
  }
  private selectObservationTarget(target: TraceTarget | null): void {
    this.observationSelection = target;
    this.lastMonitorUpdateMs = -Infinity; this.lastSecondaryRenderMs = -Infinity;
    this.callbacks.onPick(target);
    this.renderObservation(true);
  }
  /** Map gestures change observation only; they never enter the Control command path. */
  private navigateObservation(point: { x: number; z: number }): void {
    if (!Number.isFinite(point.x) || !Number.isFinite(point.z)) throw new Error("Observation position must be finite");
    if (this.mode !== "free") this.setCameraMode("free");
    this.follow = null; this.previewFollowId = null; this.clearWorkspaceFollow();
    const delta = new THREE.Vector3(point.x - this.controls.target.x, 0, point.z - this.controls.target.z);
    this.camera.position.add(delta); this.controls.target.add(delta);
    this.updatingPreviewCamera = true;
    try { this.controls.update(); } finally { this.updatingPreviewCamera = false; }
    this.renderObservation(true);
  }
  private renderObservation(force = false): void {
    if (this.operationsMonitor !== null && this.operationsMonitor !== undefined) this.updateCamera();
    this.p02UpdateCargoLabelPositions();
    this.p02UpdateDestinationMarkers();
    this.p02UpdateAircraftMarkers();
    this.renderer.domElement.style.transform = this.camera.userData.observationHorizontalMirror === true ? "scaleX(-1)" : "";
    const mainStarted = performance.now();
    this.renderer.render(this.scene, this.camera);
    this.root.dataset.observationMainSubmitMs = (performance.now() - mainStarted).toFixed(3);
    this.root.dataset.observationMainCalls = String(this.renderer.info.render.calls);
    this.root.dataset.observationMainTriangles = String(this.renderer.info.render.triangles);
    if (this.operationsMonitor === null || this.operationsMonitor === undefined) return;
    const now = performance.now();
    const paused = this.observationScene?.scenario != null && this.observationScene.operationContext?.clockState !== undefined
      ? this.observationScene.operationContext.clockState !== "playing" : !this.previewPlaying;
    const monitorToken = `${this.observationSource}:${this.mode}:${this.observationSelection?.kind}:${this.observationSelection?.id}:${paused ? this.observationTime() : "playing"}`;
    if (monitorToken !== this.lastMonitorToken) force = true;
    if (force || now - this.lastMonitorUpdateMs >= 200) {
      const started = performance.now();
      this.operationsMonitor.update(this.operationsSnapshot());
      this.lastMonitorUpdateMs = now; this.lastMonitorToken = monitorToken;
      this.root.dataset.minimapUpdateMs = (performance.now() - started).toFixed(3);
      this.root.dataset.minimapBudget = "SVG; maximum 5 Hz during playback; immediate on interaction";
    }
    if (!this.previewRequested || (!force && now - this.lastSecondaryRenderMs < 200)) return;
    const target = this.observationTarget();
    const sensor = this.observationMount(target, 16 / 9);
    if (target === undefined || sensor === null) {
      if (this.secondaryCanvas !== null) this.secondaryCanvas.hidden = true;
      return;
    }
    if (this.secondaryTarget === null) {
      this.secondaryTarget = new THREE.WebGLRenderTarget(320, 180, { depthBuffer: true });
      this.secondaryPixels = new Uint8Array(320 * 180 * 4);
      this.secondaryCanvas = document.createElement("canvas");
      this.secondaryCanvas.width = 320; this.secondaryCanvas.height = 180;
      this.secondaryCanvas.setAttribute("aria-label", "模拟 RGB 相机预览");
      this.operationsMonitor.previewContainer.replaceChildren(this.secondaryCanvas);
    }
    this.secondaryCanvas!.hidden = false;
    const secondaryCamera = this.mode === "cockpit" && this.overviewCamera !== null ? this.overviewCamera : this.sensorCamera;
    if (secondaryCamera === this.sensorCamera) applyMountedCamera(this.sensorCamera,
      target.getWorldPosition(new THREE.Vector3()), target.getWorldQuaternion(new THREE.Quaternion()), sensor.mount);
    secondaryCamera.aspect = 16 / 9; secondaryCamera.updateProjectionMatrix();
    const token = `${this.mode}:${target.userData.target?.id}:${this.observationTime()}:${secondaryCamera.position.toArray()}:${secondaryCamera.quaternion.toArray()}`;
    if (token === this.lastSecondaryToken && now - this.lastSecondaryRenderMs < 200) return;
    const previousTarget = this.renderer.getRenderTarget();
    const started = performance.now();
    try {
      this.renderer.setRenderTarget(this.secondaryTarget);
      this.renderer.render(this.scene, secondaryCamera);
      this.renderer.readRenderTargetPixels(this.secondaryTarget, 0, 0, 320, 180, this.secondaryPixels!);
      const context = this.secondaryCanvas!.getContext("2d");
      if (context !== null) {
        const pixels = context.createImageData(320, 180);
        for (let row = 0; row < 180; row++) pixels.data.set(
          this.secondaryPixels!.subarray((179 - row) * 320 * 4, (180 - row) * 320 * 4), row * 320 * 4);
        context.putImageData(pixels, 0, 0);
      }
      this.root.dataset.secondaryCameraMs = (performance.now() - started).toFixed(3);
      this.root.dataset.secondaryCameraCalls = String(this.renderer.info.render.calls);
      this.root.dataset.secondaryCameraBudget = "320x180; maximum 5 Hz; shared renderer; CPU wall time includes readback";
      this.root.dataset.secondaryCameraTimeS = String(this.observationTime());
      this.secondaryCanvas!.style.transform = secondaryCamera.userData.observationHorizontalMirror === true ? "scaleX(-1)" : "";
      this.secondaryCanvas!.dataset.source = this.mode === "cockpit" ? "overview" : sensor.label;
    } finally { this.renderer.setRenderTarget(previousTarget); }
    this.lastSecondaryRenderMs = now; this.lastSecondaryToken = token;
  }
  private observationTime(): number {
    const scene = this.observationScene;
    if (scene?.scenario != null && scene.operationContext?.timeSeconds !== undefined) return scene.operationContext.timeSeconds;
    if (scene?.sceneState !== null && scene?.sceneState !== undefined) return scene.sceneState.at.sim_time_ns / 1e9;
    return scene?.scenario != null ? 0 : this.previewSeconds;
  }
  private operationsSnapshot(): OperationsSnapshot {
    const scene = this.observationScene;
    const time = this.observationTime();
    const lang = currentLanguage();
    const sourceKind = scene?.operationContext?.sourceKind ?? "replay";
    const sampleTelemetry = (sample: SceneState["samples"][number]): ReturnType<typeof telemetryFromSample> => {
      const telemetry = telemetryFromSample(sample, time, sourceKind);
      const source = t(sourceKind === "replay" ? "mode.replay" : "mode.live", lang);
      return { ...telemetry,
        activity: telemetry.activity?.state === "unknown" ? { ...telemetry.activity, label: t("p02.unknown", lang) } : telemetry.activity,
        connectivity: telemetry.connectivity?.state === "unknown" ? { ...telemetry.connectivity, label: t("p02.unknown", lang) } : telemetry.connectivity,
        health: telemetry.health === undefined ? undefined : { ...telemetry.health, label: t(sample.health?.healthy === true
          ? "monitor.healthy" : sample.health?.healthy === false ? "monitor.unhealthy" : "p02.unknown", lang) },
        freshness: telemetry.freshness === undefined ? undefined : { ...telemetry.freshness,
          label: tf(telemetry.freshness.state === "fresh" ? "monitor.sampleFresh"
            : telemetry.freshness.state === "stale" ? "monitor.sampleStale" : "monitor.sampleFuture",
          { source, age: (telemetry.freshness.ageSeconds ?? 0).toFixed(3) }, lang) },
      };
    };
    const operationPosition = (coordinate: ResolvedCoordinate): THREE.Vector3 => scene?.scenario != null && scene.pack == null
      ? enuPosition(coordinate.enu.east_m, coordinate.enu.north_m, coordinate.enu.up_m)
      : this.position(coordinate);
    const disconnected = scene?.operationContext?.sourceKind === "live"
      && ["closed", "error", "reconnecting"].includes(scene.operationContext.connection ?? "");
    const formal = scene?.scenario !== null && scene?.scenario !== undefined;
    const samples = new Map((scene?.sceneState?.samples ?? []).map(sample => [sample.entity_id, sample]));
    const nodes = formal ? [...this.dynamic.values()] : [
      ...(this.trafficPreview?.group.children ?? []), ...(this.operationsPreview?.group.children ?? []),
      ...(this.selectedFacilityGroup?.children ?? []),
    ];
    const events = formal ? scene?.operationContext?.events ?? [] : [];
    const tasks = latestOperationTasks(events, time);
    const objects: OperationsObject[] = [];
    for (const node of nodes) {
      const target = node.userData.target as TraceTarget | undefined;
      const kind = node.userData.entityKind as string | undefined;
      if (!node.visible || target?.kind !== "entity" || !["uav", "ugv", "pedestrian", "static_asset"].includes(kind ?? "")) continue;
      const position = node.getWorldPosition(new THREE.Vector3());
      const heading = new THREE.Vector3(1, 0, 0).applyQuaternion(node.getWorldQuaternion(new THREE.Quaternion()));
      const sample = samples.get(target.id);
      const sensor = kind === "uav" ? this.observationMount(node, this.camera.aspect) : null;
      const definition = scene?.scenario?.entities.find(entity => entity.entity_id === target.id);
      objects.push({ id: target.id, kind: kind === "static_asset" ? "facility" : kind as "uav" | "ugv" | "pedestrian",
        label: this.operationsPreview?.entityLabel(target.id) ?? definition?.entity_id ?? target.id,
        position, headingRad: Math.atan2(heading.x, -heading.z),
        phase: sample?.mode ?? (typeof node.userData.flightPhase === "string" ? node.userData.flightPhase : undefined),
        telemetry: sample === undefined ? undefined : sampleTelemetry(sample),
        task: tasks.get(target.id),
        camera: kind !== "uav" ? undefined : sensor === null
          ? { source: "unavailable", state: "unavailable", reason: t("monitor.missingCamera", lang) }
          : { source: "simulated_rgb", state: "ready", label: sensor.label, frameTimeSeconds: time },
      });
    }
    // Public telemetry remains inspectable when declared presentation assets fail.
    // A missing scene never becomes a replacement sensor image.
    if (formal) for (const sample of samples.values()) {
      if (objects.some(object => object.id === sample.entity_id)) continue;
      const definition = scene!.scenario!.entities.find(entity => entity.entity_id === sample.entity_id);
      if (definition === undefined || !["uav", "ugv", "pedestrian", "static_asset"].includes(definition.kind)) continue;
      const position = operationPosition(sample.pose.position);
      const q = sample.pose.orientation_enu;
      const heading = new THREE.Vector3(1, 0, 0).applyQuaternion(new THREE.Quaternion(q.qx, q.qz, -q.qy, q.qw));
      objects.push({ id: sample.entity_id, label: sample.entity_id,
        kind: definition.kind === "static_asset" ? "facility" : definition.kind as "uav" | "ugv" | "pedestrian",
        position, headingRad: Math.atan2(heading.x, -heading.z), phase: sample.mode ?? undefined,
        telemetry: sampleTelemetry(sample),
        task: tasks.get(sample.entity_id),
        camera: definition.kind !== "uav" ? undefined : { source: "unavailable", state: "unavailable",
          reason: t("monitor.missingScene", lang) },
      });
    }
    const routes = (scene?.trajectories ?? []).map(trajectory => ({ id: trajectory.entity_id,
      objectId: trajectory.entity_id, points: trajectory.samples.filter(sample => sample.at.sim_time_ns / 1e9 <= time)
        .map(sample => { const point = operationPosition(sample.pose.position); return { x: point.x, z: point.z }; }),
    }));
    const polygons: OperationsPolygon[] = (scene?.scenario?.regions ?? []).map(region => ({ id: region.region_id,
      kind: (region.kind === "no_fly" ? "restricted" : "other") as "restricted" | "other",
      label: region.kind, points: region.lower_vertices.map(vertex => {
        const point = operationPosition(vertex); return { x: point.x, z: point.z };
      }),
    }));
    if (this.mappedGroundIdentity !== null) {
      const extent = this.mappedGroundIdentity.extent;
      polygons.push({ id: "operating-area", kind: "other", label: t("monitor.operatingArea", lang),
        points: [{ x: extent.west, z: -extent.north }, { x: extent.east, z: -extent.north },
          { x: extent.east, z: -extent.south }, { x: extent.west, z: -extent.south }] });
    }
    const locatedEvents = publicOperationEvents(events, time, (id, tick) => {
      const sample = scene?.sceneState?.at.tick === tick ? samples.get(id) : undefined;
      const historical = scene?.trajectories.find(item => item.entity_id === id)?.samples.find(item => item.at.tick === tick);
      const pose = sample?.pose ?? historical?.pose;
      if (pose === undefined) return null;
      const point = operationPosition(pose.position); return { x: point.x, z: point.z };
    });
    if (disconnected) for (const object of objects) {
      if (object.camera !== undefined) Object.assign(object.camera, { state: "disconnected", reason: t("monitor.disconnectedPose", lang) });
      if (object.telemetry !== undefined) Object.assign(object.telemetry, { freshness: { state: "unknown", label: t("monitor.freshnessDisconnected", lang) } });
    }
    const mounted = this.observationMount(this.observationTarget(), this.camera.aspect);
    const mount: CameraMount = this.mode === "cockpit" && mounted !== null ? mounted.mount : {
      kind: "rigid", offsetBodyM: [0, 0, 0], rotationBodyFromMountXyzw: [0, 0, 0, 1], horizontalMirror: false,
      verticalFovDegrees: this.camera.fov, aspect: this.camera.aspect, nearM: this.camera.near, farM: this.camera.far,
    };
    const footprint = cameraFootprintOnPlane({ position: this.camera.position, quaternion: this.camera.quaternion }, mount, 0).polygon;
    const direction = this.camera.getWorldDirection(new THREE.Vector3());
    this.root.dataset.observationTimeS = String(time);
    // Static building context for the minimap: aggregated squares compiled
    // from the scenario's declared building anchors, grid-deduped to one
    // square per cell. Context only — they neither drive the fit bounds nor
    // any business highlight, and are cached until the scenario changes.
    if (this.p02BuildingSquares === null || this.p02BuildingSquaresScenario !== (scene?.scenario ?? null)) {
      this.p02BuildingSquaresScenario = scene?.scenario ?? null;
      this.p02BuildingSquares = scene?.scenario == null || scene.scenario.buildings == null
        ? [] : aggregateOperationsBuildings(scene.scenario.buildings);
    }
    const buildings = this.p02BuildingSquares ?? [];
    return { sourceKey: `${this.observationSource || this.sourceKey}:${this.sourceKey}:${this.root.dataset.sceneReady ?? "loading"}`,
      sourceLabel: t(disconnected ? "monitor.sourceDisconnected" : this.nativePresentation !== null && scene?.operationContext === undefined
        ? "monitor.sourceAuthored" : formal
        ? sourceKind === "replay" ? "monitor.sourceReplay" : "monitor.sourceLive"
        : this.workspaceConfig === null ? "monitor.sourceEngineering" : "monitor.sourceAuthored", lang),
      timeSeconds: time, clockState: this.nativePresentation !== null && scene?.operationContext === undefined ? "notrun" : (formal ? scene?.operationContext?.clockState : undefined) ?? (this.previewPlaying ? "playing" : "paused"),
      selected: this.observationSelection, objects, routes, polygons, events: locatedEvents, buildings,
      selectedBusiness: this.p02BusinessFrame,
      observer: { mode: this.mode, position: this.observationFocus(),
        headingRad: Math.atan2(direction.x, -direction.z), footprint: footprint ?? undefined } };
  }
  private observationFocus(): THREE.Vector3 {
    return this.mode === "free" ? this.controls.target : this.camera.position;
  }
  getCameraMode(): CameraMode { return this.mode; }
  private clearWorkspaceFollow(): void {
    this.workspaceFollowId = null;
    this.operationsPreview?.setHighDetailEntity(null);
  }
  /** Relocate on deliberate camera changes; capture after the frame updates its entities. */
  private recenterCityReflections(): void {
    if ((this.sourceKey !== "default-pack" && !this.sourceKey.startsWith("authoring:"))
      || this.localReflections === null || this.buildingPresentation === null) return;
    const focus = this.camera.position.clone();
    const bounds = new THREE.Box3();
    if (this.buildingPresentation.children.some(child => child.userData.collisionBox !== undefined
        && bounds.setFromObject(child).containsPoint(focus))) return;
    // CityLocalReflections places the capture camera five metres above its reference.
    focus.y -= 5;
    if (this.previewReflectionFocus.distanceTo(focus) < 8) return;
    this.previewReflectionFocus.copy(focus);
    const enabled = this.workspaceConfig?.environment.reflectionsEnabled
      ?? this.presentationOptions.reflectionsEnabled ?? true;
    this.localReflections.configure(this.buildingPresentation, focus, enabled);
  }

  private renderStaticFrame(): void {
    const started = performance.now();
    this.weather?.update(this.previewSeconds);
    this.cityVegetationLayer?.update(this.camera, this.previewSeconds);
    setVegetationWind(this.authoredLandscapeWind, this.cityWeatherSettings.windMps,
      this.cityWeatherSettings.windDirectionDeg, this.previewSeconds);
    if (this.localReflections?.refresh()) {
      this.root.dataset.staticReflectionCaptures = String(
        Number(this.root.dataset.staticReflectionCaptures ?? "0") + 1);
    }
    this.renderObservation();
    this.root.dataset.rendererDrawCalls = String(this.renderer.info.render.calls);
    this.root.dataset.rendererTriangles = String(this.renderer.info.render.triangles);
    this.root.dataset.frameRenderMs = (performance.now() - started).toFixed(2);
  }

  /** Deterministic street preset: the densest render block (lowest index wins ties). */
  private focusRenderStreet(): void {
    const manifest = this.renderStreamer?.manifest;
    const block = manifest === undefined || manifest === null ? null
      : manifest.blocks.reduce((best, candidate) =>
        candidate.object_ids.length > best.object_ids.length ? candidate : best, manifest.blocks[0]!);
    const centerX = block === null ? this.previewDistrictFocus.x : (block.min_e + block.max_e) / 2;
    const centerZ = block === null ? this.previewDistrictFocus.z : -(block.min_n + block.max_n) / 2;
    this.controls.target.set(centerX, 35, centerZ);
    this.camera.position.set(centerX, 12, centerZ + 95);
    this.controls.update();
    this.focusSunShadow();
    this.recenterCityReflections();
  }

  /** Optional appearance defaults for the ordinary city preview; never reads a workspace draft. */
  configureCityPresentation(options: CityPresentationOptions): void {
    if (this.destroyed || (this.sourceKey !== "" && this.sourceKey !== "default-pack")) {
      throw new Error("City presentation options require the ordinary city preview");
    }
    if (this.workspaceConfig !== null) throw new Error("Use applyWorkspaceConfig for workspace appearance");
    this.presentationOptions = { ...this.presentationOptions, ...options };
    if (this.root.dataset.sceneReady !== "true") return;
    if (options.timeOfDay !== undefined) {
      if (this.calibratedEnvironment === null && options.timeOfDay === "night") {
        throw new Error("Night requires the calibrated render-scene environment");
      }
      this.setCityTimeOfDay(options.timeOfDay, false);
    } else if (options.mood !== undefined) this.setCityMood(options.mood, false);
    if (options.weather !== undefined) this.setCityWeather(options.weather);
    this.renderWeatherControls?.sync(this.presentationOptions.weather ?? CITY_WEATHER_CLEAR);
    this.updateSunIntensity();
    this.weather?.update(this.previewSeconds);
    if (this.localReflections !== null && this.buildingPresentation !== null) {
      this.recenterCityReflections();
      this.localReflections.configure(this.buildingPresentation, this.previewReflectionFocus,
        this.presentationOptions.reflectionsEnabled ?? true);
      this.localReflections.invalidate();
    }
    // Render scenes without a traffic replay draw through the static path.
    if (this.trafficPreview !== null) this.renderPreviewFrame();
    else this.renderStaticFrame();
    this.syncPreviewAnimation();
  }

  private requireWorkspaceReady(): void {
    if (this.destroyed || this.sourceKey !== "default-pack") {
      throw new Error("City workspace preview is only available for the ordinary city scene");
    }
    if (this.root.dataset.sceneReady !== "true") {
      throw new Error("City workspace preview is not ready; wait for onSceneStatus('default-pack', 'ready')");
    }
    // A traffic pack replay or a building render scene both host workspace authoring;
    // the render path has no traffic replay, so it needs the loaded building presentation.
    if (this.trafficPreview !== null) {
      if (this.weather === null || this.localReflections === null || this.loadedCityScenePath === null) {
        throw new Error("City workspace preview is not ready; wait for onSceneStatus('default-pack', 'ready')");
      }
      return;
    }
    if (!this.renderSceneActive || this.buildingPresentation === null || this.weather === null
        || this.localReflections === null || this.loadedCityScenePath === null) {
      throw new Error("City workspace preview requires a ready traffic replay or a loaded render scene");
    }
  }

  /** Return the same building envelopes used by the rendered scene's placement checks. */
  workspaceBuildingObstacles(): readonly PlacementBox[] {
    this.requireWorkspaceReady();
    const placements = this.buildingPresentation!.userData.buildingPlacements as
      readonly BuildingPlacementSource[] | undefined;
    if (placements === undefined) throw new Error("Rendered city has no building collision envelopes");
    return normalizeBuildingPlacements(placements);
  }

  get terrainCompletionAvailable(): boolean {
    return this.cityVegetationLayer !== null;
  }

  /** Explicit authored suggestions; existing completion items are replaced by the panel. */
  proposeTerrainCompletion(kind: CityAuthoredLandscapeKind) {
    const layer = this.cityVegetationLayer;
    if (layer === null) throw new Error("Terrain completion requires the loaded city vegetation layer");
    const plan = this.authoredLandscapeLayer?.group.userData.authoredLandscapePlan as CityAuthoredLandscapePlan | undefined;
    // Regeneration stays idempotent: prior suggestions are not obstacles, manual
    // authored items and source ground remain occupied.
    const occupied = [
      ...layer.occupiedSourceTriangles,
      ...(plan?.items.flatMap(item =>
        isTerrainCompletionItem(item) ? [] : item.triangles) ?? []),
    ];
    return proposeTerrainCompletion({ ...layer.authoredLandscapeGeometry, occupied }, kind);
  }

  /** Mount design additions only against the currently verified city geometry. */
  applyAuthoredLandscape(items: readonly CityAuthoredLandscapeItem[]): CityAuthoredLandscapePlan {
    this.requireWorkspaceReady();
    const { plan, renderer } = this.prepareAuthoredLandscape(items);
    this.mountAuthoredLandscape(plan, renderer);
    if (this.trafficPreview === null) this.renderStaticFrame();
    else this.renderPreviewFrame();
    return plan;
  }

  private prepareAuthoredLandscape(items: readonly CityAuthoredLandscapeItem[]): {
    plan: CityAuthoredLandscapePlan; renderer: CityAuthoredLandscapeRenderer;
  } {
    const geometry = this.cityVegetationLayer?.authoredLandscapeGeometry;
    if (geometry === undefined) {
      throw new Error("Authored landscape requires the current verified road and environment source");
    }
    const plan = planCityAuthoredLandscape(parseCityAuthoredLandscape(items), geometry);
    const renderer = createCityAuthoredLandscapeLayer(plan, {
      wetness: this.groundWetness, wind: this.authoredLandscapeWind,
      surfaces: this.cityVegetationLayer!.terrainSurfaceKit,
    });
    return { plan, renderer };
  }

  private mountAuthoredLandscape(plan: CityAuthoredLandscapePlan,
    next: CityAuthoredLandscapeRenderer): void {
    this.clearAuthoredLandscape();
    this.authoredLandscapeLayer = next;
    this.scene.add(next.group);
    next.group.visible = this.staticView?.layers.terrain ?? true;
    cacheStaticTransforms(next.group, node => node instanceof THREE.Sprite);
    next.group.userData.authoredLandscapePlan = plan;
    this.root.dataset.cityAuthoredLandscape = JSON.stringify(plan);
    if (plan.items.length > 0) {
      const note = this.root.ownerDocument.createElement("span");
      note.className = "provenance-chip";
      note.dataset.role = "authored-landscape-provenance";
      note.dataset.provenance = "authored";
      note.textContent = `创作景观 · ${plan.items.length} 块 · ${plan.stats.drawnAreaM2.toFixed(1)} m²；非 OSM 来源`;
      (this.trafficPreview === null ? this.renderControls : this.previewControls).append(note);
    }
    this.localReflections?.invalidate();
  }

  private clearAuthoredLandscape(): void {
    if (this.authoredLandscapeLayer !== null) {
      this.scene.remove(this.authoredLandscapeLayer.group);
      this.authoredLandscapeLayer.dispose();
      this.authoredLandscapeLayer = null;
    }
    delete this.root.dataset.cityAuthoredLandscape;
    for (const note of this.root.querySelectorAll('[data-role="authored-landscape-provenance"]')) note.remove();
  }

  /** Measured world-space bounds of the visible selected city's trees, lamps and signals. */
  selectedSceneStaticObstacles(expectedKey: string): readonly PlacementBox[] {
    if (this.destroyed || this.sourceKey !== `authoring:${expectedKey}`
        || this.root.dataset.sceneReady !== "true"
        || this.root.dataset.sceneSource !== "selected-city-static-presentation") {
      throw new Error("选区城市尚未完成装配，不能读取街道设施碰撞盒");
    }
    return this.selectedStaticObstacles.map(box => ({ ...box }));
  }

  /** Source building triangles of the exactly loaded selected scene, bound to the
   * current authoring map key and invalidated on any scene change. Rooftop support
   * must re-measure from these actual triangles each time the scene is replaced. */
  selectedSceneRooftopMesh(expectedKey: string): ReadonlyMap<string, SourceBuildingTriangleRange[]> {
    if (this.destroyed || this.sourceKey !== `authoring:${expectedKey}`
        || this.root.dataset.sceneReady !== "true"
        || this.root.dataset.sceneSource !== "selected-city-static-presentation") {
      throw new Error("选区城市尚未完成装配，不能读取核验建筑来源网格");
    }
    const mesh = this.selectedRooftopMesh;
    if (mesh === null) throw new Error("选区城市缺少核验建筑来源网格，屋顶放置不受支持");
    return mesh;
  }

  /** Show accepted facility authoring geometry on the exact loaded selected city. */
  async applySelectedSceneFacilities(expectedKey: string,
                                     facilities: readonly SelectedScenarioFacility[]): Promise<void> {
    this.selectedSceneStaticObstacles(expectedKey);
    const presentation = this.selectedPresentation;
    if (presentation === null) throw new Error("选区城市缺少已验证的设施素材目录");
    const revision = ++this.selectedFacilityRevision;
    const next = new THREE.Group();
    next.name = "Selected-city facility authoring preview";
    try {
      const assets = facilities.some(facility => facility.kind === "vertiport")
        ? await loadFacilityVisualAssets(presentation.visualAssets) : undefined;
      if (revision !== this.selectedFacilityRevision || this.sourceKey !== `authoring:${expectedKey}`) return;
      for (const facility of facilities) next.add(createFacilityVisual(toFacilityVisualSpec(facility), assets));
      this.selectedSceneStaticObstacles(expectedKey);
      if (revision !== this.selectedFacilityRevision) {
        for (const visual of next.children.slice()) disposeFacilityVisual(visual as THREE.Group);
        return;
      }
      const previous = this.selectedFacilityGroup;
      this.selectedFacilityGroup = next;
      this.scene.add(next);
      this.root.dataset.selectedFacilityCount = String(next.children.length);
      for (const visual of previous?.children.slice() ?? []) disposeFacilityVisual(visual as THREE.Group);
      if (previous !== null) this.scene.remove(previous);
      this.renderObservation();
    } catch (error) {
      for (const visual of next.children.slice()) disposeFacilityVisual(visual as THREE.Group);
      if (revision !== this.selectedFacilityRevision) return;
      throw error;
    }
  }

  private validateWorkspaceSpatial(config: CityWorkspaceConfig): PreviewValidationIssue[] {
    const issues: PreviewValidationIssue[] = [];
    for (const [index, zone] of config.airspace.entries()) {
      for (const issue of validateAirspacePolygon(zone)) {
        issues.push({ path: `airspace[${index}]`, code: issue.code, message: issue.message });
      }
    }
    for (const [index, facility] of config.facilities.entries()) {
      for (const issue of validateFacilityPlacement(facility, this.workspaceObstacles,
        this.workspaceRoadPolygons, config.facilities.filter(other => other.id !== facility.id),
        config.airspace)) {
        issues.push({ path: `facilities[${index}]`, code: issue.code, message: issue.message });
      }
    }
    return issues;
  }

  private async prepareWorkspaceVisuals(operations: Pick<CityOperationsPreview, "group">): Promise<void> {
    if (operations.group.children.length === 0) return;
    const textures = new Set<THREE.Texture>();
    operations.group.traverse(node => {
      if (!(node instanceof THREE.Mesh || node instanceof THREE.Line || node instanceof THREE.Sprite)) return;
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        for (const value of Object.values(material)) if (value instanceof THREE.Texture) textures.add(value);
      }
    });
    for (const texture of textures) this.renderer.initTexture(texture);
    await this.renderer.compileAsync(operations.group, this.camera, this.scene);
  }

  /** Apply an authoring draft to the default preview (traffic replay or render scene). */
  async applyWorkspaceConfig(config: CityWorkspaceConfig): Promise<PreviewValidationIssue[]> {
    this.requireWorkspaceReady();
    const next = structuredClone(parseCityWorkspaceConfig(config));
    if (next.scenePath !== this.loadedCityScenePath) {
      throw new Error(`City workspace scenePath ${next.scenePath} does not match loaded scene ${this.loadedCityScenePath}`);
    }
    if (this.auditedTrafficSnapshot) {
      this.auditedTrafficStale = !trafficPreviewDraftMatches(this.auditedTrafficSnapshot, next);
      this.root.dataset.auditedTrafficStale = String(this.auditedTrafficStale);
    }
    const spatialIssues = this.validateWorkspaceSpatial(next);
    if (spatialIssues.length > 0) return spatialIssues;
    // Render scenes have no SUMO replay; a non-zero traffic demand can never be displayed.
    if (this.trafficPreview === null
        && (next.traffic.vehicles > 0 || next.traffic.bicycles > 0 || next.traffic.pedestrians > 0)) {
      return [{ path: "traffic", code: "traffic-replay-unavailable",
        message: "当前建筑渲染场景没有 SUMO 记录回放，无法显示交通需求；请将交通需求设为 0，或改用包含 SUMO 记录的场景" }];
    }
    const previous = this.workspaceConfig;
    const environmentChanged = previous === null
      || JSON.stringify(previous.environment) !== JSON.stringify(next.environment);
    const facilitiesChanged = previous === null
      || JSON.stringify(previous.facilities) !== JSON.stringify(next.facilities);
    const trafficChanged = previous === null
      || JSON.stringify(previous.traffic) !== JSON.stringify(next.traffic);
    const landscapeChanged = previous === null
      || JSON.stringify(previous.authoredLandscape) !== JSON.stringify(next.authoredLandscape);
    const visualsChanged = facilitiesChanged || previous === null
      || JSON.stringify(previous.fleet) !== JSON.stringify(next.fleet)
      || JSON.stringify(previous.airspace) !== JSON.stringify(next.airspace);
    const operationsChanged = visualsChanged || previous === null
      || JSON.stringify(previous.stateKeyframes) !== JSON.stringify(next.stateKeyframes);
    // Validate and allocate before changing the applied draft; a bad design must retain the old layer.
    const landscape = landscapeChanged && next.authoredLandscape.length > 0
      ? this.prepareAuthoredLandscape(next.authoredLandscape) : null;
    let landscapeMounted = false;
    try {
      this.workspaceConfig = next;
      this.previewFollowId = null;
      this.previewMoodButton.hidden = true;
      this.previewFlightSelect.setAttribute("aria-label", "跟随工作区对象");
      if (previous === null || previous.environment.timeOfDay !== next.environment.timeOfDay) {
        this.setCityTimeOfDay(next.environment.timeOfDay, false);
      }
      if (environmentChanged) this.setCityWeather(next.environment);
      if (trafficChanged && this.trafficPreview !== null && !this.auditedTrafficSnapshot) {
        this.trafficPreview.setDisplayLimits(next.traffic);
      }
      this.syncWorkspaceRenderBar();
      this.updateSunIntensity();
      this.weather!.update(this.previewSeconds);
      const operations = this.operationsPreview ?? new CityOperationsPreview(this.scene);
      this.operationsPreview = operations;
      operations.setViewCamera(this.camera);
      const generation = this.conversionGeneration;
      const application = ++this.workspaceApplyGeneration;
      const issues = operationsChanged
        ? await operations.setConfig(next, { obstacles: this.workspaceObstacles })
        : this.workspaceOperationIssues;
      if (this.destroyed || generation !== this.conversionGeneration || this.sourceKey !== "default-pack") {
        throw new Error("City scene changed while applying the workspace preview");
      }
      if (application !== this.workspaceApplyGeneration) return issues;
      this.workspaceOperationIssues = issues;
      if (visualsChanged) await this.prepareWorkspaceVisuals(operations);
      if (this.destroyed || generation !== this.conversionGeneration || this.sourceKey !== "default-pack") {
        throw new Error("City scene changed while preparing the workspace preview");
      }
      if (application !== this.workspaceApplyGeneration) return issues;
      if (landscapeChanged) {
        if (landscape !== null) {
          this.mountAuthoredLandscape(landscape.plan, landscape.renderer);
          landscapeMounted = true;
        } else if (this.authoredLandscapeLayer !== null) this.clearAuthoredLandscape();
      }
      this.localReflections!.configure(this.buildingPresentation!, this.previewReflectionFocus,
        next.environment.reflectionsEnabled);
      if (environmentChanged || facilitiesChanged) this.localReflections!.invalidate();
      const choices = operations.entityChoices();
      this.previewFlightSelect.replaceChildren(new Option("选择工作区对象", ""),
        ...choices.map(choice => new Option(choice.label, choice.id)));
      if (this.workspaceFollowId !== null && !choices.some(choice => choice.id === this.workspaceFollowId)) {
        this.clearWorkspaceFollow();
      }
      else operations.setHighDetailEntity(this.workspaceFollowId);
      // Render scenes have no traffic replay: the static path owns every applied frame.
      if (this.trafficPreview !== null) this.renderPreviewFrame();
      else this.renderStaticFrame();
      this.syncPreviewAnimation();
      return issues;
    } finally {
      if (!landscapeMounted) landscape?.renderer.dispose();
    }
  }

  /** Replace the recorded ground preview only after its bytes, audit and current draft are bound. */
  async applyAuditedTrafficPreview(artifact: VerifiedTrafficPreviewArtifact,
    getCurrentDraft: () => CityWorkspaceConfig): Promise<void> {
    this.requireWorkspaceReady();
    const roads = this.verifiedCityRoadAssets;
    const city = this.loadedCitySceneConfig;
    const recorded = this.trafficPreview;
    if (this.workspaceConfig === null || roads === null || city === null || recorded === null
        || this.roadPresentation === null || this.loadedCityScenePath === null) {
      throw new Error("当前场景没有可重新生成的已验证 SUMO 道路预览");
    }
    this.auditedTrafficAbort?.abort();
    const controller = new AbortController();
    this.auditedTrafficAbort = controller;
    const revision = ++this.auditedTrafficRevision;
    const generation = this.conversionGeneration;
    let next: CityTrafficPreview | null = null;
    let url: string | null = null;
    const assertCurrent = (snapshot: TrafficPreviewDraftSnapshot): void => {
      assertNotAborted(controller.signal);
      if (this.destroyed || generation !== this.conversionGeneration
          || revision !== this.auditedTrafficRevision || this.sourceKey !== "default-pack"
          || roads !== this.verifiedCityRoadAssets || recorded !== this.trafficPreview
          || !trafficPreviewDraftMatches(snapshot, getCurrentDraft())) {
        throw new Error("交通预览装配期间场景或草稿已经变更；请重新生成");
      }
    };
    try {
      const verified = await verifyAuditedTrafficForScene(artifact, getCurrentDraft(), {
        scenePath: this.loadedCityScenePath, scene: city, roads, recordedTraffic: recorded.data,
      }, controller.signal);
      assertCurrent(verified.draftSnapshot);
      url = URL.createObjectURL(new Blob([verified.bytes], { type: "application/json" }));
      next = await loadCityTrafficReplay(url, roads.flightUrl, city.traffic_signal_model,
        window.location.href, roads.fixtures.signalIds, controller.signal);
      assertCurrent(verified.draftSnapshot);
      next.setSidewalkHeightSampler(createSidewalkHeightSampler(this.roadPresentation!.userData.walkbed,
        this.roadPresentation!.userData.streetLayout.sidewalk_height_m,
        this.roadPresentation!.userData.streetLayout.road_height_m));
      next.setCollisionBoxesVisible(this.collisionBoxesVisible);
      await this.prepareWorkspaceVisuals(next);
      assertCurrent(verified.draftSnapshot);
      this.scene.remove(recorded.group);
      this.scene.add(next.group);
      this.trafficPreview = next;
      next = null;
      recorded.dispose();
      this.previewFollowId = null;
      this.auditedTrafficSnapshot = verified.draftSnapshot;
      this.auditedTrafficStale = false;
      this.previewGroundFocusIndex.bicycle = 0;
      this.previewGroundFocusIndex.pedestrian = 0;
      this.root.dataset.auditedTrafficStale = "false";
      this.root.dataset.auditedTrafficPreview = JSON.stringify({
        jobId: verified.job.job_id, profileSha256: verified.job.profile_sha256,
        workspaceSha256: verified.job.workspace_sha256, traceSha256: verified.job.trace!.sha256,
        auditSha256: verified.job.canonical_audit!.sha256, durationSeconds: verified.data.duration_seconds,
        previewScope: verified.job.preview_scope, formalProviderBound: false, executed: false, verified: false,
      });
      const provenance = cityTrafficProvenance(verified.data);
      this.root.dataset.sumoObservedVehicleCount = String(provenance.observedVehicles);
      this.root.dataset.sumoObservedBicycleCount = String(provenance.observedBicycles);
      this.root.dataset.sumoObservedPersonCount = String(provenance.observedPersons);
      this.root.dataset.previewRetainedVehicleCount = String(provenance.displayedVehicles);
      this.root.dataset.previewRetainedBicycleCount = String(provenance.displayedBicycles);
      this.root.dataset.previewRetainedPersonCount = String(provenance.displayedPersons);
      this.root.dataset.previewOmittedVehicleCount = String(provenance.omittedVehicles);
      this.previewSourceNote.textContent = `已重新生成并审计 · ${provenance.note}`
        + ` · ${this.previewFlightSourceNote()} · 工程预览，非正式 Provider 执行`;
      this.callbacks.onBasemapNote?.(`${city.name} · ${this.buildingPresentation!.userData.buildingCount} 栋建筑`
        + ` · 已重新生成并审计 ${provenance.note} · ${this.previewFlightSourceNote()} · 非正式执行`);
      for (const note of this.root.querySelectorAll('[data-role="audited-traffic-provenance"]')) note.remove();
      const note = this.root.ownerDocument.createElement("span");
      note.className = "provenance-chip";
      note.dataset.role = "audited-traffic-provenance";
      note.dataset.provenance = "recorded";
      note.textContent = `SUMO 实录 · 审计 PASS · ${verified.job.trace!.sha256.slice(0, 12)} · 非正式执行`;
      this.previewControls.append(note);
      this.localReflections!.invalidate();
      this.renderPreviewFrame();
      this.syncPreviewAnimation();
    } finally {
      next?.dispose();
      if (url !== null) URL.revokeObjectURL(url);
      if (this.auditedTrafficAbort === controller) this.auditedTrafficAbort = null;
    }
  }

  private previewFlightSourceNote(): string {
    if (this.trafficPreview === null) throw new Error("City preview has no flight provenance");
    return this.trafficPreview.flightData.source_kind === "planned-visual-flight"
      ? "双机规划航线可视化（非物理仿真）" : "PX4/Gazebo 双机工程记录回放（不计分）";
  }

  workspaceEntityChoices(): { id: string; label: string }[] {
    return this.workspaceConfig === null ? [] : this.operationsPreview?.entityChoices() ?? [];
  }

  followWorkspaceEntity(id: string | null): void {
    this.requireWorkspaceReady();
    if (this.workspaceConfig === null || this.operationsPreview === null) {
      throw new Error("Apply a workspace configuration before following its entities");
    }
    if (id !== null && !this.operationsPreview.entityChoices().some(choice => choice.id === id)) {
      throw new Error(`Unknown workspace preview entity: ${id}`);
    }
    this.previewFollowId = null;
    this.workspaceFollowId = id;
    this.operationsPreview.setHighDetailEntity(id);
    this.renderPreviewFrame();
  }

  setWorkspaceTime(seconds: number): void {
    this.requireWorkspaceReady();
    if (this.workspaceConfig === null) throw new Error("Apply a workspace configuration before seeking its time");
    if (!Number.isFinite(seconds) || seconds < 0) throw new RangeError("Workspace time must be finite and non-negative");
    this.previewSeconds = seconds;
    this.previewPlaying = false;
    this.previewPlayButton.textContent = "播放";
    this.previewLastFrameAt = performance.now();
    this.previewTime.max = String(Math.max(120, Math.ceil(seconds)));
    this.renderPreviewFrame();
  }

  setWorkspacePlaying(playing: boolean): void {
    this.requireWorkspaceReady();
    if (this.workspaceConfig === null) throw new Error("Apply a workspace configuration before playback");
    this.previewPlaying = playing;
    this.previewPlayButton.textContent = playing ? "暂停" : "播放";
    this.previewLastFrameAt = performance.now();
    if (!playing) this.renderPreviewFrame();
  }
  refreshCursorLabels(): void {}
  focus(target: TraceTarget, _scene: MapScene): boolean {
    const object = this.dynamic.get(`entity:${target.id}`) ?? this.signals.get(target.id)
      ?? this.selectedFacilityGroup?.children.find(child => child.userData.target?.id === target.id)
      ?? this.buildingPresentation?.children.find(child => child.userData.target?.id === target.id)
      ?? this.trafficPreview?.group.children.find(child => child.userData.target?.id === target.id);
    const bounds = object === undefined ? this.staticBounds(target) : new THREE.Box3().setFromObject(object);
    if (bounds.isEmpty()) return false;
    const center = bounds.getCenter(new THREE.Vector3());
    const distance = target.kind === "entity"
      ? Math.max(4, bounds.getSize(new THREE.Vector3()).length() * 3)
      : Math.max(45, bounds.getSize(new THREE.Vector3()).length());
    this.setCameraMode("free");
    this.controls.target.copy(center); this.camera.position.copy(center).add(new THREE.Vector3(distance, distance * 0.7, distance)); this.controls.update();
    this.recenterCityReflections();
    return true;
  }

  private unmountNativePresentation(): void {
    if (this.nativePresentation != null) {
      this.scene.remove(this.nativePresentation.group);
      this.nativePresentation = null;
    }
    delete this.root.dataset.nativeCityPresentation;
    delete this.root.dataset.nativeCityStats;
  }

  private clearScene(): void {
    this.unmountNativePresentation();
    this.packedSceneLoadAbort?.abort(); this.packedSceneLoadAbort = null;
    this.auditedTrafficAbort?.abort(); this.auditedTrafficAbort = null;
    this.auditedTrafficRevision++;
    this.auditedTrafficSnapshot = null;
    this.auditedTrafficStale = false;
    this.verifiedCityRoadAssets?.dispose(); this.verifiedCityRoadAssets = null;
    this.loadedCitySceneConfig = null;
    delete this.root.dataset.auditedTrafficPreview;
    delete this.root.dataset.auditedTrafficStale;
    for (const note of this.root.querySelectorAll('[data-role="audited-traffic-provenance"]')) note.remove();
    this.selectedFacilityRevision++;
    if (this.selectedFacilityGroup !== null) {
      for (const visual of this.selectedFacilityGroup.children.slice()) disposeFacilityVisual(visual as THREE.Group);
      this.scene.remove(this.selectedFacilityGroup);
      this.selectedFacilityGroup = null;
    }
    this.selectedPresentation = null;
    this.selectedRooftopMesh = null;
    delete this.root.dataset.selectedFacilityCount;
    cancelAnimationFrame(this.previewAnimation); this.previewAnimation = 0;
    this.previewControls.hidden = true;
    this.renderControls.hidden = true;
    this.renderStatus.textContent = "";
    this.previewSourceNote.textContent = "";
    this.renderSceneActive = false;
    this.previewMoodButton.hidden = false;
    this.previewFlightSelect.setAttribute("aria-label", "跟随无人机");
    this.previewTime.max = "120";
    this.previewFollowId = null;
    this.workspaceFollowId = null;
    this.workspaceConfig = null;
    this.workspaceOperationIssues = [];
    this.workspaceApplyGeneration++;
    this.syncWorkspaceRenderBar();
    this.loadedCityScenePath = null;
    this.workspaceObstacles = [];
    this.workspaceRoadPolygons = [];
    this.selectedStaticObstacles = [];
    delete this.root.dataset.selectedStaticObstacleCount;
    delete this.root.dataset.selectedLampObstacleCount;
    delete this.root.dataset.selectedSignalObstacleCount;
    delete this.root.dataset.workspaceUavCount;
    delete this.root.dataset.workspaceFacilityCount;
    delete this.root.dataset.workspaceTimeSeconds;
    delete this.root.dataset.workspaceFollowId;
    delete this.root.dataset.workspaceUavAltitudeM;
    delete this.root.dataset.workspaceFlightPhase;
    delete this.root.dataset.workspaceActiveLabel;
    delete this.root.dataset.workspacePrecipitation;
    delete this.root.dataset.workspaceVisibilityM;
    delete this.root.dataset.previewSource;
    delete this.root.dataset.previewVisibleVehicles;
    delete this.root.dataset.previewVisibleBicycles;
    delete this.root.dataset.previewVisiblePedestrians;
    delete this.root.dataset.previewPlaying;
    delete this.root.dataset.sceneSource;
    for (const key of ["roadAssetsVerified", "roadAssetsSourceScene", "roadAssetsTrafficSha256",
      "roadAssetsRoadSha256", "roadAssetsPlacementSha256", "roadAssetsSurfaceSha256",
      "sumoObservedVehicleCount", "sumoObservedBicycleCount", "sumoObservedPersonCount",
      "previewRetainedVehicleCount", "previewRetainedBicycleCount", "previewRetainedPersonCount",
      "previewOmittedVehicleCount"]) delete this.root.dataset[key];
    this.clearMappedGround();
    this.localReflections?.dispose(); this.localReflections = null;
    this.operationsPreview?.dispose(); this.operationsPreview = null;
    this.weather?.dispose(); this.weather = null;
    this.calibratedEnvironment?.dispose(); this.calibratedEnvironment = null;
    this.lightingSample = null; this.cityEnvelope.makeEmpty();
    this.setCityWeather(CITY_WEATHER_CLEAR);
    this.previewBuildingObstacles = [];
    this.previewFlightCameraOffsets.clear();
    this.previewFlightLookDirections.clear();
    this.setCityMood("day");
    delete this.root.dataset.advertisingCount;
    delete this.root.dataset.roadVisual;
    delete this.root.dataset.roadLaneCount;
    delete this.root.dataset.verifiedVisualBytes;
    delete this.root.dataset.verifiedVisualFiles;
    delete this.root.dataset.renderedTrafficSignalCount;
    delete this.root.dataset.staticReflectionCaptures;
    this.staticView = null;
    this.selectionOutline.visible = false;
    delete this.root.dataset.entityModelError;
    clearGroup(this.world); clearGroup(this.overlays); this.dynamic.clear(); this.signals.clear();
    if (this.buildingPresentation !== null) { this.scene.remove(this.buildingPresentation); disposePresentation(this.buildingPresentation); this.buildingPresentation = null; }
    if (this.vegetationPresentation !== null) { this.scene.remove(this.vegetationPresentation); disposePresentation(this.vegetationPresentation); this.vegetationPresentation = null; }
    this.clearAuthoredLandscape();
    if (this.cityVegetationLayer !== null) { this.scene.remove(this.cityVegetationLayer.group); this.cityVegetationLayer.dispose(); this.cityVegetationLayer = null; }
    delete this.root.dataset.cityVegetation;
    delete this.root.dataset.cityGroundCoverDrawnSet;
    for (const note of this.root.querySelectorAll('[data-role="ground-cover-provenance"]')) note.remove();
    if (this.roadPresentation !== null) { this.scene.remove(this.roadPresentation); disposePresentation(this.roadPresentation); this.roadPresentation = null; }
    if (this.staticSignalPresentation !== null) { this.scene.remove(this.staticSignalPresentation); disposePresentation(this.staticSignalPresentation); this.staticSignalPresentation = null; }
    this.buildingBounds.clear();
    if (this.trafficPreview !== null) { this.scene.remove(this.trafficPreview.group); this.trafficPreview.dispose(); this.trafficPreview = null; }
    this.packedScene?.dispose(); this.packedScene = null;
    this.packResolver?.dispose(); this.packResolver = null;
    this.renderStreamer?.dispose(); this.renderStreamer = null;
    this.renderResolver?.dispose(); this.renderResolver = null;
    delete this.root.dataset.buildingRenderTotal;
    delete this.root.dataset.buildingRenderLoaded;
    delete this.root.dataset.buildingRenderFailed;
    delete this.root.dataset.buildingRenderErrors;
    delete this.root.dataset.buildingRenderLastError;
    for (const line of this.trajectories.values()) { this.scene.remove(line); line.geometry.dispose(); disposeMaterial(line.material); } this.trajectories.clear();
    for (const line of this.networks.values()) { this.scene.remove(line); line.geometry.dispose(); disposeMaterial(line.material); } this.networks.clear();
    this.p02DisposeCargoLabels();
    this.p02DisposeDestinationMarkers();
    this.p02DisposeAircraftMarkers();
  }
  private attachEntityVisual(object: THREE.Object3D, kind: EntityKind, modelAssetId: string | null, generation: number): void {
    void loadEntityVisual(kind, modelAssetId).then(visual => {
      if (visual === null) return;
      if (this.destroyed || generation !== this.conversionGeneration) { clearGroup(visual); return; }
      object.add(visual);
      this.renderObservation();
    }).catch(error => {
      if (this.destroyed || generation !== this.conversionGeneration) return;
      const detail = error instanceof Error ? error.message : String(error);
      this.root.dataset.entityModelError = detail;
      this.callbacks.onBasemapNote?.(`Entity model failed: ${detail}`);
    });
  }
  private addScenarioOverlay(scenario: PublicScenario, native = false): void {
    const buildings = native ? new Set(scenario.buildings.map(building => building.entity_id)) : null;
    for (const entity of scenario.entities) {
      if (buildings?.has(entity.entity_id)) continue;
      const object = makeEntity({ id: entity.entity_id, kind: entity.kind, east_m: 0, north_m: 0, up_m: 0, heading_deg: 0, color: `#${entityColors[entity.kind].toString(16).padStart(6, "0")}` });
      object.position.copy(this.position(entity.initial_pose.position));
      this.dynamic.set(`entity:${entity.entity_id}`, object); this.overlays.add(object);
      // Native glyphs show recorded state. Undeclared global model URLs are not native assets.
      if (!native) this.attachEntityVisual(object, entity.kind, entity.model_asset_id, this.conversionGeneration);
    }
  }
  private beginSceneLoading(stage: string): void {
    this.sceneLoading.hidden = false;
    this.sceneLoading.classList.remove("is-error");
    this.sceneLoading.setAttribute("role", "status");
    this.sceneLoadingBar.removeAttribute("value");
    this.sceneLoadingCount.textContent = "";
    this.sceneLoadingStage.textContent = stage;
    this.root.dataset.sceneLoadStage = stage;
  }
  private updateSceneLoading(completed: number, total: number, stage: string): void {
    this.sceneLoadingBar.max = total;
    this.sceneLoadingBar.value = completed;
    this.sceneLoadingCount.textContent = `${completed}/${total} 项`;
    this.sceneLoadingStage.textContent = stage;
    this.root.dataset.sceneLoadStage = stage;
  }
  private failSceneLoading(message: string): void {
    this.sceneLoading.classList.add("is-error");
    this.sceneLoading.setAttribute("role", "alert");
    this.sceneLoadingStage.textContent = `城市加载失败：${message}`;
    this.root.dataset.sceneLoadStage = "failed";
  }
  private frameExtent(extent: { west: number; east: number; south: number; north: number }): void {
    const centerX = (extent.west + extent.east) / 2;
    const centerZ = -(extent.south + extent.north) / 2;
    this.previewCityCenter.set(centerX, 0, centerZ);
    const span = Math.max(extent.east - extent.west, extent.north - extent.south, 180);
    if (this.nativePresentation !== null) {
      // A registered native configuration has no running actor to follow.
      // Fit its actual asset bounds from above rather than use the default
      // street-height flight-preview camera, which can start inside a facade.
      const bounds = this.nativePresentation.bounds;
      const sphere = bounds.getBoundingSphere(new THREE.Sphere());
      const halfVertical = THREE.MathUtils.degToRad(this.camera.fov / 2);
      const halfHorizontal = Math.atan(Math.tan(halfVertical) * this.camera.aspect);
      const distance = sphere.radius / Math.sin(Math.min(halfVertical, halfHorizontal)) * 1.12;
      const direction = new THREE.Vector3(0.8, 1.1, 0.8).normalize();
      this.controls.target.copy(sphere.center);
      this.camera.position.copy(sphere.center).addScaledVector(direction, distance);
      this.root.dataset.nativePreviewCamera = "declared-asset-bounds-oblique";
    } else if (this.sourceKey.startsWith("authoring:")) {
      // A selected static region is inspected from above; the ordinary preview
      // switches to its moving flight camera after loading.
      this.camera.position.set(centerX + span * 0.55, Math.max(170, span * 0.58), centerZ + span * 0.55);
      this.controls.target.set(centerX, 20, centerZ);
    } else {
      const height = Math.min(140, Math.max(65, span * 0.055));
      this.camera.position.set(centerX + span * 0.3, height, centerZ + span * 0.3);
      this.controls.target.set(centerX - span * 0.12, height - span * 0.013, centerZ - span * 0.2);
    }
    this.focusSunShadow();
    this.controls.maxDistance = span * 2;
    this.camera.far = 100000;
    this.camera.updateProjectionMatrix();
    this.controls.update();
  }

  private configureMappedGround(pack: LoadedMeshPack): void {
    const extent = pack.manifest.extent;
    const bounds = mappedGroundBounds(extent);
    this.mappedGroundIdentity = { extent, manifestSha256: pack.manifestSha256,
      sourceSha256: pack.manifest.source.sha256 };
    this.horizon.scale.set(bounds.y - bounds.x, bounds.w - bounds.z, 1);
    this.horizon.position.set((bounds.x + bounds.y) / 2, -0.025, (bounds.z + bounds.w) / 2);
    this.horizon.visible = true;
    this.horizon.userData.surfaceClassification = "source-unclassified-in-mapped-extent";
    const uniforms = this.outOfExtentGround.material.userData.mappedExtentUniforms;
    uniforms.uMappedBounds.value.copy(bounds);
    this.outOfExtentGround.position.set(this.horizon.position.x, -0.03, this.horizon.position.z);
    this.outOfExtentGround.visible = true;
    this.root.dataset.mappedGroundExtent = JSON.stringify(extent);
    this.root.dataset.mappedGroundManifestSha256 = pack.manifestSha256;
    this.root.dataset.mappedGroundSourceSha256 = pack.manifest.source.sha256;
    this.root.dataset.outOfExtentGround = "presentation-only-horizon-fade";
    this.refreshMappedExtentLegend();
  }

  private clearMappedGround(): void {
    this.mappedGroundIdentity = null;
    this.horizon.visible = false;
    this.outOfExtentGround.visible = false;
    for (const key of ["mappedGroundExtent", "mappedGroundManifestSha256", "mappedGroundSourceSha256",
      "outOfExtentGround"]) delete this.root.dataset[key];
    this.refreshMappedExtentLegend();
  }

  /** Append provenance to the existing viewer legend; Studio has no viewer legend. */
  refreshMappedExtentLegend(): void {
    const legend = this.root.closest(".map")?.querySelector(".map-legend");
    if (legend === null || legend === undefined) return;
    let note = legend.querySelector<HTMLElement>('[data-role="mapped-extent-note"]');
    const identity = this.mappedGroundIdentity;
    if (identity === null) { note?.remove(); return; }
    if (note === null) {
      note = document.createElement("div"); note.className = "attribution-note";
      note.dataset.role = "mapped-extent-note";
      // Keep W6's existing disclosure semantics; do not append outside its content.
      const content = legend.querySelector(".viewer-chrome-content") ?? legend;
      content.append(note);
    }
    const { extent, manifestSha256, sourceSha256 } = identity;
    const boundsText = `E [${extent.west.toFixed(2)}, ${extent.east.toFixed(2)}] · N [${extent.south.toFixed(2)}, ${extent.north.toFixed(2)}] m`;
    note.textContent = currentLanguage() === "en"
      ? `Verified mapped extent: ${boundsText}. Mesh ${manifestSha256.slice(0, 12)} · Source ${sourceSha256.slice(0, 12)}. In-extent gaps: source-unclassified. Outside mapped extent: display-only horizon, not surveyed land.`
      : `已验证地图范围：${boundsText}。网格 ${manifestSha256.slice(0, 12)} · 源 ${sourceSha256.slice(0, 12)}。范围内空白：源数据未分类。地图范围外：仅用于显示的远景，不代表已测绘地表。`;
    note.title = `Mesh-pack SHA-256: ${manifestSha256}\nSource SHA-256: ${sourceSha256}`;
    note.dataset.manifestSha256 = manifestSha256; note.dataset.sourceSha256 = sourceSha256;
  }
  private focusPreviewCityDistrict(): void {
    const buildings = this.buildingPresentation?.children.filter(child => child.userData.collisionBox !== undefined);
    if (buildings === undefined || buildings.length === 0) {
      if (this.packedScene !== null) this.frameExtent(this.packedScene.manifest.extent);
      return;
    }
    this.previewLastFrameAt = performance.now();
    this.setCameraMode("free");
    const focus = this.previewDistrictFocus;
    this.controls.target.set(focus.x + 45, 35, focus.z - 80);
    this.camera.position.set(focus.x - 65, 220, focus.z + 90);
    this.controls.update();
    this.recenterCityReflections();
    this.root.dataset.overviewNearbyBuildings = String(buildings.filter(building =>
      Math.hypot(building.position.x - focus.x, building.position.z - focus.z) < 180).length);
  }
  osmProperties(id: string): Readonly<Record<string, string>> | null {
    return this.packedScene?.manifest.objects.find(item => item.id === id)?.tags ?? null;
  }
  private async packedMaterial(batch: PackedBatch, pack: LoadedMeshPack,
                               cache: Map<string, Promise<THREE.Texture>>,
                               staticPresentation?: VerifiedStaticPresentation): Promise<THREE.MeshStandardMaterial> {
    const value: PackedMaterial = batch.material;
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color().setRGB(...value.color, THREE.SRGBColorSpace), roughness: 0.86, metalness: 0.02,
      alphaTest: value.transparent ? 0.5 : 0, side: THREE.FrontSide,
      polygonOffset: value.transparent, polygonOffsetFactor: value.transparent ? -2 : 0, polygonOffsetUnits: value.transparent ? -2 : 0,
    });
    const load = async (path: string | null, color = false): Promise<THREE.Texture | null> => {
      if (path === null) return null;
      const url = pack.textureUrls.get(path);
      if (url === undefined) throw new Error("Verified pack texture is missing");
      const key = `${path}:${color}:${value.clamp}`;
      let pending = cache.get(key);
      if (pending === undefined) {
        pending = textureLoader.loadAsync(url).then(texture => {
          texture.flipY = false;
          texture.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
          texture.wrapS = texture.wrapT = value.clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
          return texture;
        });
        cache.set(key, pending);
      }
      return pending;
    };
    const cityTexture = async (path: string, color: boolean): Promise<THREE.Texture> => {
      let pending = cache.get(`city:${path}`);
      if (pending === undefined) {
        pending = (staticPresentation === undefined ? Promise.resolve(path)
          : staticPresentation.visualAssets.url(path)).then(url => textureLoader.loadAsync(url)).then(texture => {
          texture.colorSpace = color ? THREE.SRGBColorSpace : THREE.NoColorSpace;
          texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
          texture.anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
          return texture;
        });
        cache.set(`city:${path}`, pending);
      }
      return pending;
    };
    const isRoadSurface = batch.layer === "roads" && value.base_color_texture !== null
      && (value.base_color_texture.includes("/Asphalt010/") || value.base_color_texture.includes("/Concrete034/")
        || value.base_color_texture.includes("/PavingStones072/"));
    const asphalt = isRoadSurface && value.base_color_texture!.includes("/Asphalt010/");
    const marking = batch.layer === "roads" && value.base_color_texture?.includes("/road_marking_");
    const roadMetal = batch.layer === "roads" && value.base_color_texture?.includes("/Metal002/");
    const terrain = batch.layer === "terrain";
    const terrainGrass = terrain && value.base_color_texture !== null
      && (value.base_color_texture.includes("/Ground003/") || value.base_color_texture.includes("/Ground042/"));
    const terrainAsphalt = terrain && value.base_color_texture?.includes("/Asphalt010/");
    const terrainPaving = terrain && !terrainGrass && !terrainAsphalt;
    const grass = async (): Promise<THREE.Texture> => {
      let pending = cache.get("city:grass");
      if (pending === undefined) {
        pending = (staticPresentation === undefined ? Promise.resolve(BIGCITY_FLOOR_ATLAS)
          : staticPresentation.visualAssets.url(BIGCITY_FLOOR_ATLAS)).then(url => cityGrassTexture(url));
        cache.set("city:grass", pending);
      }
      return pending;
    };
    const [map, normal, opacity, orm] = await Promise.all([
      terrainGrass ? grass() : terrainAsphalt ? cityTexture(CITY_ASPHALT, true) : terrainPaving ? cityTexture(CITY_PAVING, true)
        : isRoadSurface ? cityTexture(asphalt ? CITY_ASPHALT : CITY_PAVING, true) : marking || roadMetal ? null : load(value.base_color_texture, true),
      terrain || roadMetal ? null : isRoadSurface ? cityTexture(asphalt ? CITY_ASPHALT_NORMAL : CITY_PAVING_NORMAL, false) : load(value.normal_texture),
      terrain || marking || roadMetal ? null : load(value.opacity_texture),
      terrain || isRoadSurface || roadMetal ? null : load(value.orm_texture),
    ]);
    material.map = map; material.normalMap = normal; material.alphaMap = opacity;
    material.roughnessMap = orm; material.metalnessMap = orm;
    if (terrain) {
      material.color.set(terrainGrass ? 0x70836d : 0xffffff);
      material.userData.cityTerrainKind = terrainGrass ? "grass" : "paving";
      material.roughness = 0.98;
      if (terrainGrass) {
        material.polygonOffset = true;
        material.polygonOffsetFactor = 2;
        material.polygonOffsetUnits = 2;
      }
    } else if (isRoadSurface) {
      material.color.set(asphalt ? 0x999e9f : 0xffffff);
      material.normalScale.set(0.55, 0.55);
      material.roughness = asphalt ? 0.96 : 0.9;
    } else if (marking) {
      material.color.set(0xf0f1e9);
      material.roughness = 0.95;
    } else if (roadMetal) {
      material.color.set(0x7f969b);
      material.metalness = 0.55;
      material.roughness = 0.46;
    }
    material.needsUpdate = true;
    return material;
  }

  private async loadPackedScene(generation: number, suppliedPack?: LoadedMeshPack,
                                staticPresentation?: VerifiedStaticPresentation): Promise<void> {
    if (this.destroyed || generation !== this.conversionGeneration) { suppliedPack?.dispose(); return; }
    const loadStarted = performance.now();
    let packFileTotal = 0;
    let resolver: AssetResolver | null = null;
    let pack: LoadedMeshPack | null = null;
    const objects: THREE.Mesh[] = [];
    let buildings: THREE.Group | null = null;
    let vegetation: THREE.Group | null = null;
    let vegetationLayer: CityVegetationLayer | null = null;
    let roads: THREE.Group | null = null;
    let staticSignals: THREE.Group | null = null;
    let traffic: CityTrafficPreview | null = null;
    let verifiedRoadAssets: VerifiedCityRoadAssets | null = null;
    let renderResolver: AssetResolver | null = null;
    let renderStreamer: BuildingRenderStreamer | null = null;
    const loadAbort = new AbortController();
    this.packedSceneLoadAbort = loadAbort;
    const cancelOwnedReads = (): void => { resolver?.dispose(); renderResolver?.dispose(); };
    loadAbort.signal.addEventListener("abort", cancelOwnedReads, { once: true });
    let reflectionFocus: THREE.Vector3 | null = null;
    try {
      const requestedCityPath = suppliedPack === undefined
        ? new URLSearchParams(window.location.search).get("city") ?? "/city-presentation/default-scene-v1.json"
        : null;
      const sceneConfig = suppliedPack === undefined ? await loadCitySceneConfig(loadAbort.signal) : null;
      assertNotAborted(loadAbort.signal);
      const enhanced = sceneConfig !== null || staticPresentation !== undefined;
      const renderScene = sceneConfig !== null && sceneConfig.building_render !== undefined;
      const buildTaskTotal = renderScene && sceneConfig?.road_assets === undefined ? 4 : enhanced ? 7 : 3;
      const progress = (completed: number, stage: string): void => {
        if (!this.destroyed && generation === this.conversionGeneration) {
          this.updateSceneLoading(packFileTotal + completed, packFileTotal + buildTaskTotal, stage);
        }
      };
      resolver = suppliedPack === undefined
        ? new AssetResolver({ baseHref: new URL(sceneConfig!.mesh_pack.base_url, window.location.href).href }) : null;
      pack = suppliedPack ?? await loadMeshPack(resolver!, sceneConfig!.mesh_pack.manifest,
        loadAbort.signal, undefined, (completed, total) => {
          packFileTotal = total;
          if (!this.destroyed && generation === this.conversionGeneration) {
            this.updateSceneLoading(completed, total + buildTaskTotal,
              `读取并校验城市网格 ${completed}/${total}`);
          }
        }, cityPackTexturePaths);
      const packLoaded = performance.now();
      progress(0, "装配地形与道路网格");
      await new Promise<void>(resolve => setTimeout(resolve, 0));
      const textures = new Map<string, Promise<THREE.Texture>>();
      const buildingBounds = new Map<string, THREE.Box3>();
      const buildingSourceRanges = new Map<string, SourceBuildingTriangleRange[]>();
      const treeLocations: CityTree[] = [];
      for (const { batch, arrays } of pack.batches) {
        if (batch.layer === "buildings") {
          let first = 0;
          const point = new THREE.Vector3();
          for (const range of batch.ranges) {
            if (range.target?.kind === "building") {
              let bounds = buildingBounds.get(range.target.id);
              if (bounds === undefined) { bounds = new THREE.Box3(); buildingBounds.set(range.target.id, bounds); }
              for (let index = first * 3; index < range.end * 3; index++) {
                const offset = arrays.indices[index]! * 3;
                point.set(arrays.positions[offset]!, arrays.positions[offset + 1]!, arrays.positions[offset + 2]!);
                bounds.expandByPoint(point);
              }
              if (enhanced) {
                let ranges = buildingSourceRanges.get(range.target.id);
                if (ranges === undefined) { ranges = []; buildingSourceRanges.set(range.target.id, ranges); }
                ranges.push({ positions: arrays.positions, normals: arrays.normals, indices: arrays.indices,
                  firstTriangle: first, endTriangle: range.end,
                  roofMaterial: (batch.material.base_color_texture ?? "").includes("/Roofing") });
              }
            }
            first = range.end;
          }
          if (enhanced) continue;
        }
        if (batch.layer === "terrain" && batch.material.base_color_texture?.includes("/arbaro_tree_")) {
          if (batch.vertices % 12 !== 0) throw new Error("OSM tree billboard geometry no longer matches the recorded tree layout");
          for (let first = 0; first < batch.vertices; first += 12) {
            let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity, height = 0;
            for (let index = first; index < first + 12; index++) {
              const offset = index * 3;
              minX = Math.min(minX, arrays.positions[offset]!); maxX = Math.max(maxX, arrays.positions[offset]!);
              minZ = Math.min(minZ, arrays.positions[offset + 2]!); maxZ = Math.max(maxZ, arrays.positions[offset + 2]!);
              height = Math.max(height, arrays.positions[offset + 1]!);
            }
            treeLocations.push({ x: (minX + maxX) / 2, z: (minZ + maxZ) / 2,
              width: Math.max(maxX - minX, maxZ - minZ), height,
              kind: batch.material.base_color_texture.includes("coniferous") ? "conifer" : "broad" });
          }
          continue;
        }
        if ((staticPresentation !== undefined || sceneConfig?.road_assets !== undefined) && batch.layer === "roads") continue;
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute("position", new THREE.BufferAttribute(arrays.positions, 3));
        geometry.setAttribute("normal", new THREE.BufferAttribute(arrays.normals, 3));
        geometry.setAttribute("uv", new THREE.BufferAttribute(arrays.uvs, 2));
        geometry.setIndex(new THREE.BufferAttribute(arrays.indices, 1));
        geometry.addGroup(0, arrays.indices.length, 0); geometry.computeBoundingSphere();
        const material = await this.packedMaterial(batch, pack, textures, staticPresentation);
        const object = new THREE.Mesh(geometry, [material]);
        object.castShadow = batch.layer === "buildings"; object.receiveShadow = true;
        object.userData.layer = batch.layer; object.userData.ranges = batch.ranges;
        object.userData.packedTextures = true;
        objects.push(object);
      }
      const cityBuildings: CityBuilding[] = [...buildingBounds].map(([id, bounds]) => {
        const ranges = buildingSourceRanges.get(id);
        const sourceShape: SourceBuildingShape | undefined = ranges === undefined ? undefined : { ranges };
        return sourceShape === undefined ? { id, bounds } : { id, bounds, sourceShape };
      });
      const packedMeshesBuilt = performance.now();
      if (cityBuildings.length === 0) throw new Error("Verified city pack has no building positions");
      progress(1, "载入建筑、交通与绿化素材");
      let cityTasksDone = 0;
      const cityTaskComplete = (): void => {
        cityTasksDone++;
        progress(1 + cityTasksDone, `城市素材已就绪 ${cityTasksDone}/${staticPresentation !== undefined ? 2 : sceneConfig === null ? 1 : renderScene && sceneConfig.road_assets === undefined ? 2 : 3} 组`);
      };
      if (staticPresentation !== undefined) {
        [buildings, vegetation] = await Promise.all([
          loadCityBuildings(cityBuildings, pack.manifest.source.sha256, pack.manifestSha256,
            { value: staticPresentation.buildingPlacement,
              sha256: staticPresentation.manifest.building_placement.sha256 }, staticPresentation.visualAssets)
            .then(value => { cityTaskComplete(); return value; }),
          loadCityTrees(treeLocations, staticPresentation.visualAssets)
            .then(value => { cityTaskComplete(); return value; }),
        ]);
      } else if (sceneConfig === null) {
        buildings = new THREE.Group();
        buildings.userData.buildingCount = cityBuildings.length;
        buildings.userData.visualPartCount = 0;
        buildings.userData.occludedSourceBuildings = [];
        vegetation = await loadCityTrees(treeLocations);
        cityTaskComplete();
      } else if (sceneConfig !== null && sceneConfig.building_render !== undefined) {
        const renderRef = sceneConfig.building_render;
        renderResolver = new AssetResolver({ baseHref: new URL(renderRef.base_url, window.location.href).href });
        const reportRenderProgress = (state: BuildingRenderProgress): void => {
          if (this.destroyed || generation !== this.conversionGeneration) return;
          this.root.dataset.buildingRenderTotal = String(state.total);
          this.root.dataset.buildingRenderLoaded = String(state.loaded);
          this.root.dataset.buildingRenderFailed = String(state.failed);
          this.root.dataset.buildingRenderErrors = JSON.stringify(state.errors.slice(0, 20));
          if (state.errors.length > 0) {
            this.root.dataset.buildingRenderLastError = state.errors[state.errors.length - 1]!.message;
          }
          this.renderStatus.textContent = `建筑渲染 ${state.loaded}/${state.total}`
            + (state.failed > 0 ? ` · 失败 ${state.failed}` : "");
        };
        const [renderManifest, renderSource, trees] = await Promise.all([
          fetchBuildingRenderManifest(renderResolver, renderRef.manifest, loadAbort.signal)
            .then(value => { cityTaskComplete(); return value; }),
          fetchBuildingRenderSourceContext(renderResolver, renderRef.source_context, loadAbort.signal),
          loadCityTrees(treeLocations).then(value => {
            if (loadAbort.signal.aborted) { disposePresentation(value); assertNotAborted(loadAbort.signal); }
            vegetation = value;
            cityTaskComplete(); return value;
          }),
        ]);
        vegetation = trees;
        assertNotAborted(loadAbort.signal);
        validateBuildingRenderContext(renderManifest, {
          expectedSceneId: renderRef.source_scene_id, pack, source: renderSource,
        });
        const streamedBuildings = new THREE.Group();
        buildings = streamedBuildings;
        buildings.userData.buildingPlacements = renderManifest.buildings.map(buildingPlacement);
        buildings.userData.buildingCount = renderManifest.counts.buildings;
        buildings.userData.visualPartCount = renderManifest.counts.buildings;
        buildings.userData.occludedSourceBuildings = [];
        buildings.userData.buildingStyle = "building-render-validated";
        for (const entry of renderManifest.buildings) {
          this.cityEnvelope.expandByPoint(new THREE.Vector3(entry.envelope.min_e, entry.envelope.base_up, -entry.envelope.max_n));
          this.cityEnvelope.expandByPoint(new THREE.Vector3(entry.envelope.max_e, entry.envelope.top_up, -entry.envelope.min_n));
        }
        renderStreamer = new BuildingRenderStreamer({
          manifest: renderManifest,
          resolver: renderResolver,
          group: buildings,
          anisotropy: Math.min(8, this.renderer.capabilities.getMaxAnisotropy()),
          onProgress: reportRenderProgress,
          onPlaced: visual => {
            this.lightRenderBuildings(visual);
            if (this.staticView !== null) visual.visible = this.buildingVisible(this.staticView, visual);
            setCollisionBoxesVisible(visual as THREE.Group, this.collisionBoxesVisible);
            // The reflection probe reads the group registry; calibration writes it per visual.
            const placed: unknown = visual.userData.windowMaterials;
            if (placed !== undefined) {
              if (!Array.isArray(placed)) throw new Error("Streamed building facade registry is invalid");
              const registry: unknown = streamedBuildings.userData.windowMaterials ?? [];
              if (!Array.isArray(registry)) throw new Error("City building facade registry is invalid");
              streamedBuildings.userData.windowMaterials = [...new Set([...registry, ...placed])];
            }
          },
          requestRender: () => {
            if (!this.destroyed && generation === this.conversionGeneration
                && this.renderStreamer === renderStreamer && this.root.dataset.sceneReady === "true") {
              if (this.trafficPreview === null) this.renderStaticFrame();
              else if (!this.previewPlaying) this.renderPreviewFrame();
            }
          },
        });
        reportRenderProgress(renderStreamer.progress);
        if (sceneConfig.road_assets !== undefined) {
          verifiedRoadAssets = await loadVerifiedCityRoadAssets(sceneConfig.road_assets, pack, renderManifest,
            renderRef.manifest.sha256,
            state => progress(3, `校验地面覆盖层 ${state.verifiedFiles}/${state.totalFiles} · `
              + `${(state.completedBytes / 1_000_000).toFixed(1)}/${(state.totalBytes / 1_000_000).toFixed(1)} MB`),
            loadAbort.signal);
          if (verifiedRoadAssets.fixtures.signalModelSha256 !== sceneConfig.traffic_signal_model?.sha256) {
            throw new Error("Effective signal fixtures were measured from a different signal model");
          }
          if (sceneConfig.environment_source !== undefined) {
            vegetationLayer = await loadCityVegetationLayer(cityVegetationInput(sceneConfig.environment_source,
              renderManifest.scene, pack.manifestSha256, pack.manifest.extent,
              verifiedRoadAssets.vegetationRoadBinding, loadAbort.signal),
              verifiedRoadAssets.displayedSurfaceSha256);
          }
          progress(3, "载入 SUMO 实录交通与独立飞行轨迹素材");
          traffic = await loadCityTrafficReplay(verifiedRoadAssets.trafficUrl, verifiedRoadAssets.flightUrl,
            sceneConfig.traffic_signal_model, window.location.href, verifiedRoadAssets.fixtures.signalIds,
            loadAbort.signal);
          cityTaskComplete();
        }
      } else {
        [buildings, vegetation, traffic] = await Promise.all([
          loadCityBuildings(cityBuildings, pack.manifest.source.sha256, pack.manifestSha256,
                            sceneConfig.assets.building_placement).then(value => { cityTaskComplete(); return value; }),
          loadCityTrees(treeLocations).then(value => { cityTaskComplete(); return value; }),
          loadCityTrafficReplay(sceneConfig.assets.traffic, sceneConfig.assets.flight,
            sceneConfig.traffic_signal_model, window.location.href, "all-source-signals", loadAbort.signal)
            .then(value => { cityTaskComplete(); return value; }),
        ]);
      }
      if (traffic !== null) {
        progress(4, "构建道路、路灯和交通信号");
        if (renderScene) {
          if (verifiedRoadAssets === null) throw new Error("Building render road replay has no verified road assets");
          roads = await loadCityRoads(traffic.data.source_network_sha256, pack.manifest.source.sha256,
            { sourceContext: verifiedRoadAssets.sourceContext,
              streetLampIndices: verifiedRoadAssets.fixtures.streetLampIndices },
            verifiedRoadAssets.displayedSurfaceSha256,
            verifiedRoadAssets.roadUrl, traffic.data, pack.manifest, sceneConfig!.mesh_pack.base_url, this.groundWetness);
        } else {
          roads = await loadCityRoads(traffic.data.source_network_sha256, pack.manifest.source.sha256,
            { placementSha256: buildings.userData.placementSha256 as string },
            buildings.userData.displayedSurfaceSha256 as string,
            sceneConfig!.assets!.road, traffic.data, pack.manifest, sceneConfig!.mesh_pack.base_url, this.groundWetness);
        }
        traffic.setSidewalkHeightSampler(createSidewalkHeightSampler(roads.userData.walkbed,
          roads.userData.streetLayout.sidewalk_height_m, roads.userData.streetLayout.road_height_m));
        progress(5, renderScene ? "布置街道灯光" : "布置夜景广告与街道灯光");
        const focal = this.preferredPreviewStreet(traffic, buildings,
          roads.userData.signalRoadCoverage as Readonly<Record<string, number>>);
        if (focal === undefined) throw new Error("SUMO preview has no moving street view on a road");
        reflectionFocus = new THREE.Vector3(focal.x, 0, focal.z);
        if (!renderScene) await addCityAdvertising(buildings, traffic.data.signals, focal);
      } else if (staticPresentation !== undefined) {
        progress(4, "构建本选区道路、步道、标线和路灯");
        roads = await loadCityRoads(staticPresentation.manifest.network.sha256, pack.manifest.source.sha256,
          { placementSha256: buildings.userData.placementSha256 as string },
          buildings.userData.displayedSurfaceSha256 as string,
          { value: staticPresentation.road,
            sourceOsmSha256: staticPresentation.manifest.sumo_source_osm_sha256,
            packManifestSha256: pack.manifestSha256,
            signalInventorySha256: staticPresentation.manifest.signal_inventory.sha256,
            signals: staticPresentation.signals }, null, pack.manifest,
          `/authoring/v1/scenes/${staticPresentation.manifest.job_id}/pack/`,
          this.groundWetness, staticPresentation.visualAssets);
        const focus = roads.userData.staticStreetFocus as readonly [number, number] | undefined;
        if (focus === undefined) throw new Error("本选区真实道路没有可用的反射街道焦点");
        reflectionFocus = new THREE.Vector3(focus[0], 0, focus[1]);
        staticSignals = await loadStaticTrafficSignals(staticPresentation.signals.signals,
          staticPresentation.visualAssets);
      }
      progress(buildTaskTotal - 1, "绘制城市首帧");
      const cityAssetsBuilt = performance.now();
      if (this.destroyed || generation !== this.conversionGeneration) {
        const temporary = new THREE.Group(); for (const object of objects) temporary.add(object); clearGroup(temporary);
        if (buildings !== null) disposePresentation(buildings);
        if (vegetation !== null) disposePresentation(vegetation);
        vegetationLayer?.dispose();
        if (roads !== null) disposePresentation(roads);
        if (staticSignals !== null) disposePresentation(staticSignals);
        traffic?.dispose();
        verifiedRoadAssets?.dispose();
        renderStreamer?.dispose(); renderStreamer = null;
        renderResolver?.dispose(); renderResolver = null;
        pack.dispose(); resolver?.dispose(); return;
      }
      this.packedScene = pack;
      this.configureMappedGround(pack);
      this.packResolver = resolver;
      this.renderStreamer = renderStreamer;
      this.renderResolver = renderResolver;
      this.renderSceneActive = renderScene;
      this.selectedPresentation = staticPresentation ?? null;
      // Rooftop support and volume proof may only consult geometry that is
      // actually rendered as a complete footprint source surface. Occluded and
      // non-complete buildings (template-facade or hidden geometry) never prove a
      // support: the mesh map is filtered to the placement payload's verified
      // complete-footprint set, which loadCityBuildings has already validated.
      this.selectedRooftopMesh = staticPresentation === undefined ? null
        : (() => {
          const placement = staticPresentation.buildingPlacement as
            { readonly complete_footprint_buildings?: unknown } | null | undefined;
          const completeIds = new Set(
            Array.isArray(placement?.complete_footprint_buildings)
              ? placement.complete_footprint_buildings.filter((id): id is string => typeof id === "string")
              : []);
          return new Map([...buildingSourceRanges].filter(([id]) => completeIds.has(id)));
        })();
      this.loadedCityScenePath = requestedCityPath;
      this.loadedCitySceneConfig = sceneConfig;
      this.verifiedCityRoadAssets = verifiedRoadAssets;
      this.projectionOrigin = pack.manifest.projection.origin;
      if (objects.length > 0) this.world.add(...objects);
      if (roads !== null) for (const object of objects) {
        if (object.userData.layer === "roads") object.visible = false;
      }
      this.buildingPresentation = buildings;
      if (renderScene && renderStreamer !== null) {
        const blocks = renderStreamer.manifest.blocks;
        const dense = blocks.reduce((best, candidate) =>
          candidate.object_ids.length > best.object_ids.length ? candidate : best, blocks[0]!);
        this.previewDistrictFocus.set((dense.min_e + dense.max_e) / 2, 0,
          -(dense.min_n + dense.max_n) / 2);
      } else if (traffic !== null) {
        const placed = buildings.children.filter(child => child.userData.collisionBox !== undefined);
        const focus = placed.map(candidate => ({ candidate, count: placed.filter(other =>
          Math.hypot(other.position.x - candidate.position.x,
                     other.position.z - candidate.position.z) < 180).length }))
          .sort((left, right) => right.count - left.count)[0]?.candidate;
        if (focus === undefined) throw new Error("City preview has no building district for its camera");
        this.previewDistrictFocus.copy(focus.position);
      }
      this.vegetationPresentation = vegetation;
      this.roadPresentation = roads;
      this.staticSignalPresentation = staticSignals;
      if (sceneConfig !== null || staticPresentation !== undefined) {
        const sourcePlacements = buildings.userData.buildingPlacements as readonly BuildingPlacementSource[] | undefined;
        const sourceRoadbed = roads?.userData.roadbed as readonly RoadbedSource[] | undefined;
        // Road clearance evidence stays separate from the rendered building placements.
        if (!Array.isArray(sourcePlacements) || (roads !== null && !Array.isArray(sourceRoadbed))) {
          throw new Error("City workspace collision geometry is missing from the verified city assets");
        }
        const measuredStatic = cityStaticObstacles(vegetation, roads, traffic?.group ?? staticSignals);
        if (staticPresentation !== undefined) {
          this.selectedStaticObstacles = measuredStatic;
          this.root.dataset.selectedStaticObstacleCount = String(measuredStatic.length);
          this.root.dataset.selectedLampObstacleCount = String(measuredStatic.filter(box => box.id.startsWith("lamp:")).length);
          this.root.dataset.selectedSignalObstacleCount = String(measuredStatic.filter(box => box.id.startsWith("signal:")).length);
        }
        else {
          this.workspaceObstacles = [...normalizeBuildingPlacements(sourcePlacements), ...measuredStatic];
          this.workspaceRoadPolygons = normalizeRoadbed(sourceRoadbed ?? []);
        }
      }
      this.buildingBounds.clear();
      for (const [id, bounds] of buildingBounds) this.buildingBounds.set(id, bounds);
      this.previewBuildingObstacles = buildings.children.filter(child => child.userData.collisionBox !== undefined)
        .map(child => new THREE.Box3().setFromObject(child));
      this.scene.add(buildings);
      this.scene.add(vegetation);
      if (vegetationLayer !== null) {
        this.cityVegetationLayer = vegetationLayer;
        this.scene.add(vegetationLayer.group);
        vegetationLayer.setWeather(this.cityWeatherSettings);
        const { summary } = vegetationLayer;
        this.root.dataset.cityVegetation = JSON.stringify({ greens: summary.greenCount, trees: summary.treeCount,
          grassAreaM2: Math.round(summary.grassAreaM2), clumps: summary.grassClumpCount,
          groundCovers: summary.groundCoverCount, groundCoverAreaM2: summary.groundCoverAreaM2,
          missing: summary.missing });
        this.root.dataset.cityGroundCoverDrawnSet = JSON.stringify(vegetationLayer.groundCoverDrawnSet);
        const groundNote = document.createElement("span");
        groundNote.className = "provenance-chip";
        groundNote.dataset.role = "ground-cover-provenance";
        groundNote.dataset.provenance = "osm";
        groundNote.textContent = `OSM 标签地表 · ${summary.groundCoverCount} 块；无来源区域保持未分类`;
        (traffic === null ? this.renderControls : this.previewControls).append(groundNote);
      }
      if (roads !== null) this.scene.add(roads);
      if (staticSignals !== null) this.scene.add(staticSignals);
      cacheStaticTransforms(this.world);
      cacheStaticTransforms(buildings);
      cacheStaticTransforms(vegetation);
      if (roads !== null) cacheStaticTransforms(roads,
        node => node instanceof THREE.Light || node instanceof THREE.Sprite);
      if (staticSignals !== null) cacheStaticTransforms(staticSignals);
      if (traffic !== null) {
        const loadedTraffic = traffic;
        this.trafficPreview = loadedTraffic; this.scene.add(loadedTraffic.group);
        this.previewFlightSelect.replaceChildren(new Option("选择跟随无人机", ""),
          ...loadedTraffic.flightData.vehicle_ids.map(id => new Option(loadedTraffic.aircraftLabel(id), id)));
        setCollisionBoxesVisible(buildings, this.collisionBoxesVisible);
        traffic.setCollisionBoxesVisible(this.collisionBoxesVisible);
        this.previewSeconds = 30;
        this.previewLastFrameAt = performance.now();
        this.previewPlaying = true;
        this.previewPlayButton.textContent = "暂停";
        this.previewPlaybackRate = 1;
        this.previewSpeedButton.textContent = "1×";
        this.previewControls.hidden = false;
      }
      if (renderScene && traffic === null) {
        this.renderControls.querySelector(".city-preview-row")!.append(this.renderStatus);
        this.renderControls.hidden = false;
      }
      if (renderScene && traffic !== null) this.previewControls.append(this.renderStatus);
      this.mountRenderWeatherControls(renderScene, traffic !== null);
      const view = this.staticView; this.staticView = null;
      if (view !== null) this.applyStaticView(view);
      this.root.dataset.texturesReady = "true";
      this.root.dataset.meshCount = String(pack.manifest.original_mesh_count);
      this.root.dataset.drawCalls = String(objects.length + buildings.children.length + vegetation.children.length
        + (roads?.children.length ?? 0));
      this.root.dataset.buildingReplacements = String(buildings.userData.buildingCount);
      this.root.dataset.buildingVisualParts = String(buildings.userData.visualPartCount);
      this.root.dataset.buildingStyle = String(buildings.userData.buildingStyle ?? "official-osm2world");
      this.root.dataset.buildingAssetCount = String(buildings.userData.assetCount ?? 0);
      this.root.dataset.buildingUsedAssets = (buildings.userData.usedAssetTitles as string[] | undefined)?.join(",") ?? "";
      this.root.dataset.occludedSourceBuildings = String(buildings.userData.occludedSourceBuildings.length);
      this.root.dataset.treeReplacements = String(vegetation.children.length);
      this.root.dataset.advertisingCount = String(buildings.userData.advertisingCount ?? 0);
      this.root.dataset.roadVisual = staticPresentation !== undefined ? "selected-sumo-topology-bigcity"
        : roads === null ? "official-osm2world" : "sumo-bigcity";
      this.root.dataset.roadLaneCount = String(roads?.userData.laneCount ?? 0);
      if (staticPresentation !== undefined) {
        this.root.dataset.verifiedVisualBytes = String(staticPresentation.visualAssets.stats.byteCount);
        this.root.dataset.verifiedVisualFiles = String(staticPresentation.visualAssets.stats.fileCount);
      }
      this.root.dataset.streetLampCount = String(roads?.userData.streetLampCount ?? 0);
      this.root.dataset.buildingWindowMaterialCount = String(buildings.userData.windowMaterialCount ?? 0);
      this.root.dataset.storefrontLightCount = String(buildings.userData.storefrontLightCount ?? 0);
      this.root.dataset.trafficSignalCount = String(staticPresentation?.signals.signals.length ?? traffic?.data.signals.length ?? 0);
      if (staticPresentation !== undefined) this.root.dataset.renderedTrafficSignalCount = String(staticSignals?.children.length ?? 0);
      this.root.dataset.flightSourceKind = traffic?.flightData.source_kind ?? "";
      this.root.dataset.vehicleLampMaterialCount = String(traffic?.group.userData.vehicleLampMaterialCount ?? 0);
      this.root.dataset.sceneSource = staticPresentation !== undefined ? "selected-city-static-presentation"
        : renderScene ? "building-render-414" : traffic === null ? "official-mesh-pack" : "osm-bigcity-sumo-preview";
      if (renderScene && renderStreamer !== null) {
        this.root.dataset.buildingRenderBytes = String(renderStreamer.manifest.counts.total_bytes);
        this.root.dataset.buildingRenderPackMissing = String(
          renderStreamer.manifest.counts.pack_missing_objects);
        this.root.dataset.buildingRenderBlocks = String(renderStreamer.manifest.counts.blocks);
        if (sceneConfig?.road_assets !== undefined) {
          this.root.dataset.roadAssetsVerified = "true";
          this.root.dataset.roadAssetsSourceScene = sceneConfig.road_assets.source_scene_id;
          this.root.dataset.roadAssetsTrafficSha256 = sceneConfig.road_assets.traffic.sha256;
          this.root.dataset.roadAssetsRoadSha256 = sceneConfig.road_assets.road.sha256;
          this.root.dataset.roadAssetsEffectiveFixturesSha256 = sceneConfig.road_assets.effective_fixtures.sha256;
          this.root.dataset.roadAssetsFlightSha256 = sceneConfig.road_assets.flight.sha256;
          this.root.dataset.roadAssetsSurfaceSha256 = sceneConfig.road_assets.displayed_surface_sha256;
        }
      }
      if (staticPresentation !== undefined) {
        this.horizon.visible = true;
        this.root.dataset.previewSource = "selected-city-static-not-running";
        this.root.dataset.previewVisibleVehicles = "0";
        this.root.dataset.previewVisibleBicycles = "0";
        this.root.dataset.previewVisiblePedestrians = "0";
        this.root.dataset.previewPlaying = "false";
        this.root.dataset.renderedUavCount = "0";
      }
      this.frameExtent(pack.manifest.extent);
      if (reflectionFocus !== null) this.previewReflectionFocus.copy(reflectionFocus);
      if (enhanced && renderScene) {
        const environment = new CityCalibratedEnvironment(this.renderer);
        this.calibratedEnvironment = environment;
        this.root.dataset.lightingSource = "verified-hdr-calibrated-display";
        await environment.load();
        if (this.destroyed || generation !== this.conversionGeneration) return;
      }
      const initialMood = enhanced ? this.presentationOptions.mood ?? sceneConfig?.initial_mood ?? "day" : "day";
      this.setCityTimeOfDay(enhanced && this.presentationOptions.timeOfDay !== undefined && this.calibratedEnvironment !== null
        ? this.presentationOptions.timeOfDay : initialMood === "day" ? "day" : "twilight");
      if (enhanced) {
        this.weather = new CityWeather(this.scene, this.camera);
        if (renderScene) this.weather.configureEnvelope(this.cityEnvelope);
        this.setCityWeather(this.presentationOptions.weather ?? CITY_WEATHER_CLEAR);
        if (this.lightingSample !== null) this.weather.setLighting(this.lightingSample);
        else this.weather.setMood(this.cityMood);
        this.renderWeatherControls?.sync(this.presentationOptions.weather ?? CITY_WEATHER_CLEAR);
        this.weather.update(this.previewSeconds);
        this.updateSunIntensity();
        this.localReflections = new CityLocalReflections(this.renderer, this.scene);
      }
      this.updateCamera();
      if (traffic !== null) this.focusPreviewFlight(traffic.flightData.vehicle_ids[1]!);
      if (renderStreamer !== null) {
        progress(buildTaskTotal - 1, "加载视野内建筑渲染资产");
        await renderStreamer.prime(this.camera);
        await renderStreamer.prepareRenderer(this.renderer, this.camera, this.scene);
        if (this.destroyed || generation !== this.conversionGeneration) return;
        this.previewBuildingObstacles = renderStreamer.manifest.buildings.map(entry => new THREE.Box3(
          new THREE.Vector3(entry.envelope.min_e, entry.envelope.base_up, -entry.envelope.max_n),
          new THREE.Vector3(entry.envelope.max_e, entry.envelope.top_up, -entry.envelope.min_n)));
        renderStreamer.update(this.camera);
      }
      if (traffic !== null) {
        progress(buildTaskTotal - 1, "准备城市光照与材质");
        traffic.update(this.previewSeconds, true, true, true, true, true);
        if (this.localReflections !== null) {
          this.localReflections.configure(buildings, this.previewReflectionFocus,
            this.presentationOptions.reflectionsEnabled ?? true);
        }
        this.focusSunShadow();
        await traffic.prepareRenderer(this.renderer, this.camera, this.scene);
        if (this.destroyed || generation !== this.conversionGeneration) return;
        this.renderPreviewFrame();
        this.previewLastFrameAt = performance.now();
      } else {
        this.focusSunShadow();
        if (this.localReflections !== null && reflectionFocus !== null) {
          this.localReflections.configure(buildings, reflectionFocus,
            this.presentationOptions.reflectionsEnabled ?? true);
        }
        this.renderStaticFrame();
      }
      const firstFrameRendered = performance.now();
      this.root.dataset.sceneReady = "true";
      this.syncPreviewAnimation();
      progress(buildTaskTotal, "城市已就绪");
      requestAnimationFrame(() => {
        if (!this.destroyed && generation === this.conversionGeneration) this.sceneLoading.hidden = true;
      });
      this.root.dataset.sceneLoadPhaseSeconds = JSON.stringify({
        pack: Number(((packLoaded - loadStarted) / 1000).toFixed(1)),
        meshes: Number(((packedMeshesBuilt - packLoaded) / 1000).toFixed(1)),
        cityAssets: Number(((cityAssetsBuilt - packedMeshesBuilt) / 1000).toFixed(1)),
        firstFrame: Number(((firstFrameRendered - cityAssetsBuilt) / 1000).toFixed(1)),
      });
      this.callbacks.onSceneStatus?.(this.sourceKey, "ready");
      const sourceOverlapNote = buildings.userData.occludedSourceBuildings.length > 0
        ? `；${buildings.userData.occludedSourceBuildings.length} 栋源建筑几乎全落在道路面，未放置模型` : "";
      const flightSourceNote = traffic === null ? "" : this.previewFlightSourceNote();
      const provenance = traffic === null ? null : cityTrafficProvenance(traffic.data);
      if (provenance !== null) {
        this.root.dataset.sumoObservedVehicleCount = String(provenance.observedVehicles);
        this.root.dataset.sumoObservedBicycleCount = String(provenance.observedBicycles);
        this.root.dataset.sumoObservedPersonCount = String(provenance.observedPersons);
        this.root.dataset.previewRetainedVehicleCount = String(provenance.displayedVehicles);
        this.root.dataset.previewRetainedBicycleCount = String(provenance.displayedBicycles);
        this.root.dataset.previewRetainedPersonCount = String(provenance.displayedPersons);
        this.root.dataset.previewOmittedVehicleCount = String(provenance.omittedVehicles);
        this.previewSourceNote.textContent = `${provenance.note} · ${flightSourceNote}`;
      }
      this.callbacks.onBasemapNote?.(staticPresentation !== undefined
        ? `本选区真实 SUMO 道路拓扑 · BigCity 建筑与街道材质 · ${buildings.userData.buildingCount} 栋建筑 · 静态呈现，尚未运行交通与无人机`
        : renderScene
        ? `${sceneConfig!.name} · ${buildings.userData.buildingCount} 栋实测渲染 GLB · ENU 实测锚点与足迹包络 · 视野区块懒加载`
          + (traffic === null ? " · 地面为演示底板，无移动交通"
            : ` · ground-only ${provenance!.note}；道路铺装与标线含设计补全，非实景测绘；${flightSourceNote}`)
        : traffic === null
        ? `OSM 坐标 · 官方场景网格 · ${buildings.userData.buildingCount} 栋源建筑`
        : `${sceneConfig!.name} · BigCity 建筑材质 · ${buildings.userData.buildingCount} 栋建筑 · ${provenance!.note}；道路铺装与标线含设计补全，非实景测绘；${flightSourceNote}${sourceOverlapNote}`);
    } catch (error) {
      loadAbort.abort();
      verifiedRoadAssets?.dispose();
      if (this.verifiedCityRoadAssets === verifiedRoadAssets) this.verifiedCityRoadAssets = null;
      const temporary = new THREE.Group(); for (const object of objects) temporary.add(object); clearGroup(temporary);
      if (buildings !== null && buildings !== this.buildingPresentation) disposePresentation(buildings);
      if (vegetation !== null && vegetation !== this.vegetationPresentation) disposePresentation(vegetation);
      if (vegetationLayer !== null && vegetationLayer !== this.cityVegetationLayer) vegetationLayer.dispose();
      if (roads !== null && roads !== this.roadPresentation) disposePresentation(roads);
      if (staticSignals !== null && staticSignals !== this.staticSignalPresentation) disposePresentation(staticSignals);
      if (traffic !== null && traffic !== this.trafficPreview) traffic.dispose();
      renderStreamer?.dispose(); renderResolver?.dispose();
      if (this.renderStreamer === renderStreamer) this.renderStreamer = null;
      if (this.renderResolver === renderResolver) this.renderResolver = null;
      pack?.dispose(); resolver?.dispose();
      if (this.packedScene === pack) this.packedScene = null;
      if (this.packResolver === resolver) this.packResolver = null;
      if (!this.destroyed && generation === this.conversionGeneration) {
        this.root.dataset.sceneError = "true";
        const message = error instanceof Error ? error.message : String(error);
        this.root.dataset.sceneErrorMessage = message;
        this.failSceneLoading(message);
        this.callbacks.onSceneStatus?.(this.sourceKey, "failed", message);
        this.callbacks.onBasemapNote?.(`OSM2World pack failed · ${message}`);
      }
    } finally {
      loadAbort.signal.removeEventListener("abort", cancelOwnedReads);
      if (this.packedSceneLoadAbort === loadAbort) this.packedSceneLoadAbort = null;
    }
  }

  private syncPreviewAnimation(): void {
    const animated = visualAnimationRequired(this.weather, this.cityVegetationLayer, this.cityWeatherSettings);
    this.renderWeatherPause.disabled = !animated;
    this.renderWeatherPause.textContent = this.previewPlaying ? "暂停天气" : "播放天气";
    this.root.dataset.visualWeatherAnimated = String(animated && this.previewPlaying);
    const needed = !this.destroyed && this.sourceKey === "default-pack"
      && (this.trafficPreview !== null || (this.renderSceneActive && animated && this.previewPlaying));
    if (!needed) {
      cancelAnimationFrame(this.previewAnimation); this.previewAnimation = 0;
    } else if (this.previewAnimation === 0) {
      this.previewLastFrameAt = performance.now();
      this.previewAnimation = requestAnimationFrame(() => this.animatePreview());
    }
  }

  private animatePreview(): void {
    this.previewAnimation = 0;
    if (this.destroyed || this.sourceKey !== "default-pack"
        || (this.trafficPreview === null && (!this.renderSceneActive || !this.previewPlaying
          || !visualAnimationRequired(this.weather, this.cityVegetationLayer, this.cityWeatherSettings)))) return;
    const now = performance.now();
    if (this.previewPlaying) {
      this.previewSeconds += Math.max(0, now - this.previewLastFrameAt) / 1000 * this.previewPlaybackRate;
      if (this.trafficPreview !== null) this.renderPreviewFrame();
      else {
        this.renderStaticFrame();
        this.root.dataset.visualWeatherTimeS = this.previewSeconds.toFixed(3);
      }
    }
    this.previewLastFrameAt = now;
    this.previewAnimation = requestAnimationFrame(() => this.animatePreview());
  }

  private previewFlightCameraPosition(id: string, center: THREE.Vector3): THREE.Vector3 {
    if (this.trafficPreview?.flightData.source_kind === "planned-visual-flight") {
      const inward = new THREE.Vector3(this.previewDistrictFocus.x - center.x, 0,
                                       this.previewDistrictFocus.z - center.z).normalize();
      if (inward.lengthSq() < 0.01) inward.set(0, 0, -1);
      this.previewFlightLookDirections.set(id, inward);
      return center.clone().addScaledVector(inward, -75).add(new THREE.Vector3(0, 65, 0));
    }
    const previous = this.previewFlightCameraOffsets.get(id);
    const hit = new THREE.Vector3();
    const clearToAircraft = (offset: THREE.Vector3): boolean => {
      const eye = center.clone().add(offset);
      if (this.previewBuildingObstacles.some(bounds => bounds.containsPoint(eye))) return false;
      const direction = center.clone().sub(eye);
      const distance = direction.length();
      const ray = new THREE.Ray(eye, direction.normalize());
      return !this.previewBuildingObstacles.some(bounds => {
        const intersection = ray.intersectBox(bounds, hit);
        return intersection !== null && intersection.distanceTo(eye) < distance - 0.5;
      });
    };
    if (previous !== undefined && clearToAircraft(previous)) return center.clone().add(previous);
    const inward = new THREE.Vector3(this.previewCityCenter.x - center.x, 0,
                                     this.previewCityCenter.z - center.z).normalize();
    if (inward.lengthSq() < 0.5) inward.set(0, 0, -1);
    const axis = new THREE.Vector3(0, 1, 0);
    const candidates = [0, -30, 30, -60, 60, -90, 90, -120, 120, -150, 150, 180].flatMap(degrees => {
      const direction = inward.clone().applyAxisAngle(axis, THREE.MathUtils.degToRad(degrees));
      const offset = direction.clone().multiplyScalar(-14); offset.y = 3;
      if (!clearToAircraft(offset)) return [];
      const across = new THREE.Vector3(-direction.z, 0, direction.x);
      const clearDistances = [-20, -10, 0, 10, 20].map(shift => {
        const origin = center.clone().addScaledVector(across, shift);
        origin.y += 4;
        const background = new THREE.Ray(origin, direction);
        let clearDistance = 80;
        for (const bounds of this.previewBuildingObstacles) {
          const intersection = background.intersectBox(bounds, hit);
          if (intersection !== null) clearDistance = Math.min(clearDistance, intersection.distanceTo(origin));
        }
        return clearDistance;
      });
      const openness = clearDistances.reduce((total, value) => total + value, 0) / clearDistances.length;
      return [{ direction, offset, score: openness + direction.dot(inward) * 15 }];
    }).sort((left, right) => right.score - left.score);
    const selected = candidates[0];
    if (selected !== undefined) {
      this.previewFlightCameraOffsets.set(id, selected.offset);
      this.previewFlightLookDirections.set(id, selected.direction);
      return center.clone().add(selected.offset);
    }
    throw new Error(`No unobstructed follow camera around recorded UAV ${id}`);
  }

  private previewFlightLookTarget(id: string, center: THREE.Vector3): THREE.Vector3 {
    const direction = this.previewFlightLookDirections.get(id);
    if (direction === undefined) throw new Error(`Follow camera has no view direction for recorded UAV ${id}`);
    return this.trafficPreview?.flightData.source_kind === "planned-visual-flight"
      ? center.clone().addScaledVector(direction, 135).add(new THREE.Vector3(0, -110, 0))
      : center.clone().addScaledVector(direction, 10).add(new THREE.Vector3(0, -1, 0));
  }

  private renderPreviewFrame(): void {
    if (this.destroyed || this.trafficPreview === null || this.sourceKey !== "default-pack") return;
    const frameStarted = performance.now();
    const view = this.staticView;
    const authoring = this.workspaceConfig !== null;
    const seconds = ((this.previewSeconds % this.trafficPreview.data.duration_seconds)
      + this.trafficPreview.data.duration_seconds) % this.trafficPreview.data.duration_seconds;
    this.trafficPreview.update(this.previewSeconds, view?.layers.ugv ?? true, view?.layers.pedestrian ?? true,
                               view?.layers.roads ?? true, !authoring && (view?.layers.uav ?? true),
                               !authoring && (view?.layers.trajectories ?? true));
    if (authoring) this.operationsPreview?.update(this.previewSeconds);
    if (view !== null) for (const child of this.trafficPreview.group.children) {
      const target = child.userData.target as TraceTarget | undefined;
      if (target === undefined) continue;
      child.visible &&= !view.hiddenEntities.has(`${target.kind}:${target.id}`)
        && (view.isolate === null || (view.isolate.kind === target.kind && view.isolate.id === target.id));
    }
    if (this.mode === "free" && authoring && this.workspaceFollowId !== null) {
      const center = this.operationsPreview?.entityPosition(this.workspaceFollowId) ?? null;
      if (center !== null) {
        this.controls.target.copy(center);
        const facility = this.workspaceConfig!.facilities.some(item => item.id === this.workspaceFollowId);
        this.camera.position.copy(center).add(facility ? new THREE.Vector3(12, 7, 12) : new THREE.Vector3(5, 3, 6));
        this.updatingPreviewCamera = true;
        try { this.controls.update(); } finally { this.updatingPreviewCamera = false; }
      }
    } else if (this.mode === "free" && this.previewFollowId !== null) {
      const center = this.trafficPreview.entityPosition(this.previewFollowId);
      if (center === null) this.previewFollowId = null;
      else {
        const aircraft = this.previewFollowId.startsWith("uav.");
        const offset = this.previewFollowId.startsWith("bicycle.") ? new THREE.Vector3(6, 2.5, 8)
            : new THREE.Vector3(3, 1.8, 4);
        const eye = aircraft ? this.previewFlightCameraPosition(this.previewFollowId, center) : center.clone().add(offset);
        this.controls.target.copy(aircraft ? this.previewFlightLookTarget(this.previewFollowId, center) : center);
        this.camera.position.copy(eye);
        this.updatingPreviewCamera = true;
        try { this.controls.update(); } finally { this.updatingPreviewCamera = false; }
      }
    }
    this.renderStreamer?.update(this.camera);
    this.focusSunShadow();
    const second = Math.floor(seconds);
    const timelineSecond = Math.floor(this.previewSeconds);
    const counts = this.trafficPreview.visibleCounts();
    this.root.dataset.previewSecond = String(authoring ? timelineSecond : second);
    this.root.dataset.previewSource = authoring ? "authoring-draft" : "recorded-preview";
    this.root.dataset.previewFollowId = authoring ? this.workspaceFollowId ?? this.previewFollowId ?? ""
      : this.previewFollowId ?? "";
    this.root.dataset.previewCameraX = this.camera.position.x.toFixed(3);
    this.root.dataset.previewCameraZ = this.camera.position.z.toFixed(3);
    this.previewFlightSelect.value = authoring ? this.workspaceFollowId ?? ""
      : this.previewFollowId?.startsWith("uav.") ? this.previewFollowId : "";
    this.root.dataset.previewUavTypes = authoring ? "authoring-draft" : "Holybro X500,相机四旋翼";
    this.root.dataset.previewVisibleVehicles = String(counts.vehicles);
    this.root.dataset.previewVisibleBicycles = String(counts.bicycles);
    this.root.dataset.previewVisiblePedestrians = String(counts.pedestrians);
    this.root.dataset.previewPlaying = String(this.previewPlaying);
    const workspaceUavs = authoring ? this.operationsPreview?.group.children
      .filter(child => child.visible && child.userData.entityKind === "uav") ?? [] : [];
    const workspaceUavCount = workspaceUavs.length;
    const activeWorkspaceObject = authoring && this.workspaceFollowId !== null
      ? this.operationsPreview?.group.children.find(child => child.userData.target?.id === this.workspaceFollowId)
      : workspaceUavs[0];
    const statusWorkspaceUav = activeWorkspaceObject?.userData.entityKind === "uav"
      ? activeWorkspaceObject : workspaceUavs[0];
    const workspaceActiveLabel = typeof activeWorkspaceObject?.userData.previewLabel === "string"
      ? activeWorkspaceObject.userData.previewLabel : "";
    this.root.dataset.renderedUavCount = String(authoring ? workspaceUavCount : counts.aircraft);
    if (authoring) {
      this.root.dataset.workspaceUavCount = String(workspaceUavCount);
      this.root.dataset.workspaceFacilityCount = String(this.operationsPreview?.group.children
        .filter(child => child.visible && child.userData.entityKind === "static_asset").length ?? 0);
      this.root.dataset.workspaceTimeSeconds = String(this.previewSeconds);
      this.root.dataset.workspaceFollowId = this.workspaceFollowId ?? "";
      this.root.dataset.workspaceUavAltitudeM = statusWorkspaceUav?.position.y.toFixed(3) ?? "";
      this.root.dataset.workspaceFlightPhase = String(statusWorkspaceUav?.userData.flightPhase ?? "");
      this.root.dataset.workspaceActiveLabel = workspaceActiveLabel;
      this.root.dataset.workspacePrecipitation = this.workspaceConfig!.environment.precipitation;
      this.root.dataset.workspaceVisibilityM = String(this.workspaceConfig!.environment.visibilityM);
    }
    this.previewTime.max = String(authoring ? Math.max(this.trafficPreview.data.duration_seconds,
      Math.ceil(this.previewSeconds)) : this.trafficPreview.data.duration_seconds);
    this.previewTime.value = String(authoring ? this.previewSeconds : seconds);
    this.previewClock.textContent = authoring
      ? `工作区 ${Math.floor(timelineSecond / 60)}:${String(timelineSecond % 60).padStart(2, "0")}`
      : `${Math.floor(second / 60)}:${String(second % 60).padStart(2, "0")} / `
        + `${Math.floor(this.trafficPreview.data.duration_seconds / 60)}:`
        + String(Math.floor(this.trafficPreview.data.duration_seconds % 60)).padStart(2, "0");
    this.previewCounts.textContent = authoring
      ? (this.auditedTrafficSnapshot
          ? `已审计 SUMO 记录需求 ${this.auditedTrafficSnapshot.draft.traffic.vehicles}/${this.auditedTrafficSnapshot.draft.traffic.bicycles}/${this.auditedTrafficSnapshot.draft.traffic.pedestrians}`
            + (this.auditedTrafficStale ? " · 草稿已变更，等待重新生成" : " · 与草稿匹配")
          : `SUMO记录显示上限 机动车 ${this.workspaceConfig!.traffic.vehicles} · 自行车 ${this.workspaceConfig!.traffic.bicycles} · 行人 ${this.workspaceConfig!.traffic.pedestrians}`)
        + `；当前显示 ${counts.vehicles}/${counts.bicycles}/${counts.pedestrians} · 作者机 ${workspaceUavCount}`
        + (workspaceActiveLabel ? ` · ${workspaceActiveLabel}` : "")
        + (this.softwareRenderer ? " · 软件渲染，帧率可能较低" : "")
      : `机动车 ${counts.vehicles} · 自行车 ${counts.bicycles} · 行人 ${counts.pedestrians} · 无人机 ${counts.aircraft}`
        + (this.softwareRenderer ? " · 软件渲染，帧率可能较低" : "");
    this.root.dataset.renderedTrafficSignalCount = String(this.trafficPreview.setSignalVisibility(
      this.camera, view?.layers.roads ?? true));
    this.root.dataset.headlightBeamCount = String(this.trafficPreview.setVehicleLighting(this.camera, this.cityTimeOfDay));
    if (this.roadPresentation !== null) setCityRoadLighting(this.roadPresentation, this.camera, this.cityTimeOfDay);
    this.weather?.update(this.previewSeconds);
    this.cityVegetationLayer?.update(this.camera, this.previewSeconds);
    setVegetationWind(this.authoredLandscapeWind, this.cityWeatherSettings.windMps,
      this.cityWeatherSettings.windDirectionDeg, this.previewSeconds);
    this.localReflections?.refresh(); // Only dirty captures, after pose and lighting updates.
    const renderStarted = performance.now();
    this.renderObservation();
    this.root.dataset.previewUpdateMs = (renderStarted - frameStarted).toFixed(1);
    this.root.dataset.previewRenderMs = (performance.now() - renderStarted).toFixed(1);
    this.root.dataset.previewRenderCalls = this.root.dataset.observationMainCalls ?? String(this.renderer.info.render.calls);
    // Frame time is the interval between consecutive preview frames, so FPS includes the GPU
    // and compositor wait, not only the CPU submit time measured above.
    if (this.lastPreviewFrameAt !== null && this.previewPlaying) {
      this.perfOverlay.sample({ info: this.renderer.info, frameMs: frameStarted - this.lastPreviewFrameAt });
    }
    this.lastPreviewFrameAt = this.previewPlaying ? frameStarted : null;
  }

  private preferredPreviewStreet(traffic: CityTrafficPreview, buildings: THREE.Group | null,
                                 roadCoverage: Readonly<Record<string, number>>):
      CityTrafficPreview["data"]["signals"][number] | undefined {
    const midpoint = traffic.data.duration_seconds / 2;
    const frame = traffic.data.frames[Math.round(midpoint / traffic.data.step_seconds)];
    const later = traffic.data.frames[Math.round(Math.min(traffic.data.duration_seconds,
      midpoint + 3) / traffic.data.step_seconds)];
    if (frame === undefined || later === undefined) return;
    const placements = buildings?.userData.buildingPlacements as readonly BuildingPlacementSource[] | undefined;
    if (!Array.isArray(placements)) throw new Error("Preview street requires verified rendered building placements");
    const laterVehicles = new Map(later.vehicles.map(vehicle => [vehicle[0], vehicle]));
    const signal = traffic.data.signals.map(location => {
      const roadFraction = roadCoverage[location.id];
      if (roadFraction === undefined) throw new Error(`Road coverage missing traffic signal ${location.id}`);
      return {
        location,
        roadFraction,
        nearby: frame.vehicles.filter(vehicle => Math.hypot(vehicle[1] - location.x, vehicle[2] - location.z) < 55),
        buildings: placements.filter(placement =>
          Math.hypot(placement.x - location.x, placement.z - location.z) < 100).length,
      };
    }).map(item => ({ ...item, moving: item.nearby.filter(vehicle => {
      const after = laterVehicles.get(vehicle[0]);
      return after !== undefined && Math.hypot(after[1] - vehicle[1], after[2] - vehicle[2]) > 3;
    }).length })).filter(item => item.moving > 0)
      .sort((left, right) =>
        (right.roadFraction * 30 + right.buildings * 2 + right.nearby.length * 15 + right.moving * 25)
        - (left.roadFraction * 30 + left.buildings * 2 + left.nearby.length * 15 + left.moving * 25))[0]?.location;
    return signal;
  }

  private focusPreviewStreet(): void {
    if (this.trafficPreview === null || this.roadPresentation === null) return;
    const signal = this.preferredPreviewStreet(this.trafficPreview, this.buildingPresentation,
      this.roadPresentation.userData.signalRoadCoverage as Readonly<Record<string, number>>);
    if (signal === undefined) return;
    const frame = this.trafficPreview.data.frames[Math.round(this.trafficPreview.data.duration_seconds
      / 2 / this.trafficPreview.data.step_seconds)];
    const vehicle = frame?.vehicles.filter(sample => Math.hypot(sample[1] - signal.x, sample[2] - signal.z) < 40)
      .sort((left, right) => {
        const rearDistance = (sample: typeof left): number => {
          const yaw = THREE.MathUtils.degToRad(sample[3]);
          return (sample[1] - signal.x) * -Math.sin(yaw) + (sample[2] - signal.z) * Math.cos(yaw);
        };
        return rearDistance(right) - rearDistance(left);
      })[0];
    if (vehicle === undefined) throw new Error("Street camera has no recorded vehicle on its road");
    this.previewFollowId = null;
    this.previewSeconds = frame!.second;
    this.previewLastFrameAt = performance.now();
    this.setCameraMode("free");
    const yaw = THREE.MathUtils.degToRad(vehicle[3]);
    this.controls.target.set(vehicle[1] + Math.sin(yaw) * 14, 1.7,
                             vehicle[2] - Math.cos(yaw) * 14);
    this.camera.position.set(vehicle[1] - Math.sin(yaw) * 38, 5,
                             vehicle[2] + Math.cos(yaw) * 38);
    this.controls.update();
    this.recenterCityReflections();
  }

  private focusPreviewVehicleLights(): void {
    if (this.trafficPreview === null || this.roadPresentation === null || this.buildingPresentation === null) return;
    const traffic = this.trafficPreview;
    const frame = traffic.data.frames[Math.round(traffic.data.duration_seconds / 2 / traffic.data.step_seconds)];
    if (frame === undefined) throw new Error("Recorded vehicle light view has no traffic frame");
    const focus = this.preferredPreviewStreet(traffic, this.buildingPresentation,
      this.roadPresentation.userData.signalRoadCoverage as Readonly<Record<string, number>>);
    if (focus === undefined) throw new Error("Recorded vehicle light view has no active street");
    const candidates = frame.vehicles.filter(sample => sample[4] !== "bicycle")
      .map(sample => ({ sample, distance: Math.hypot(sample[1] - focus.x, sample[2] - focus.z) }))
      .sort((left, right) => left.distance - right.distance);
    for (const { sample } of candidates) {
      const yaw = THREE.MathUtils.degToRad(sample[3]);
      const forward = new THREE.Vector3(Math.sin(yaw), 0, -Math.cos(yaw));
      const eye = new THREE.Vector3(sample[1], 2.2, sample[2]).addScaledVector(forward, 11);
      const target = new THREE.Vector3(sample[1], 0.9, sample[2]);
      if (this.previewBuildingObstacles.some(bounds => bounds.containsPoint(eye))) continue;
      const direction = target.clone().sub(eye);
      const distance = direction.length();
      const ray = new THREE.Ray(eye, direction.normalize());
      if (this.previewBuildingObstacles.some(bounds => {
        const hit = ray.intersectBox(bounds, new THREE.Vector3());
        return hit !== null && hit.distanceTo(eye) < distance - 0.5;
      })) continue;
      this.previewFollowId = null;
      this.previewSeconds = frame.second;
      this.previewLastFrameAt = performance.now();
      this.setCityMood("dusk");
      this.setCameraMode("free");
      this.controls.target.copy(target);
      this.camera.position.copy(eye);
      this.controls.update();
      this.recenterCityReflections();
      this.root.dataset.focusVehicleId = sample[0];
      return;
    }
    throw new Error("No unobstructed car front in the recorded city frame");
  }

  private focusPreviewStreetLighting(): void {
    if (this.trafficPreview === null || this.roadPresentation === null || this.buildingPresentation === null) return;
    const lamps = this.roadPresentation.userData.streetLampLocations as readonly {
      x: number; z: number; rotation_deg: number;
    }[];
    const frame = this.trafficPreview.data.frames[Math.round(this.trafficPreview.data.duration_seconds
      / 2 / this.trafficPreview.data.step_seconds)];
    if (frame === undefined) throw new Error("Recorded city lighting focus has no traffic frame");
    const obstacles = this.buildingPresentation.children.filter(child => child.userData.collisionBox !== undefined)
      .map(child => new THREE.Box3().setFromObject(child));
    const candidates = lamps.flatMap(lamp => {
      const vehicles = frame.vehicles.filter(vehicle => Math.hypot(vehicle[1] - lamp.x, vehicle[2] - lamp.z) < 65).length;
      if (vehicles === 0) return [];
      const buildings = this.buildingPresentation!.children.filter(child =>
        Math.hypot(child.position.x - lamp.x, child.position.z - lamp.z) < 90).length;
      const neighboringLamps = lamps.filter(other => Math.hypot(other.x - lamp.x, other.z - lamp.z) < 80).length;
      return this.trafficPreview!.data.signals.flatMap(signal => {
        const distance = Math.hypot(signal.x - lamp.x, signal.z - lamp.z);
        if (distance < 12 || distance > 35) return [];
        return [{ lamp, signal, score: vehicles * 10 + buildings * 2 + neighboringLamps * 5 + 45 - distance }];
      });
    }).sort((a, b) => b.score - a.score);
    for (const { lamp, signal } of candidates) {
      const center = new THREE.Vector3((lamp.x + signal.x) / 2, 4.8, (lamp.z + signal.z) / 2);
      const across = new THREE.Vector3(signal.z - lamp.z, 0, lamp.x - signal.x).normalize();
      for (const side of [1, -1]) {
        const eye = center.clone().addScaledVector(across, side * 24);
        eye.y = 6.2;
        if (obstacles.some(bounds => bounds.containsPoint(eye))) continue;
        const blocked = [new THREE.Vector3(lamp.x, 5.8, lamp.z),
                         new THREE.Vector3(signal.x, 4.6, signal.z)].some(target => {
          const direction = target.clone().sub(eye);
          const distance = direction.length();
          const ray = new THREE.Ray(eye, direction.normalize());
          return obstacles.some(bounds => {
            const hit = ray.intersectBox(bounds, new THREE.Vector3());
            return hit !== null && hit.distanceTo(eye) < distance - 0.5;
          });
        });
        if (blocked) continue;
        this.previewFollowId = null;
        this.previewSeconds = frame.second;
        this.previewLastFrameAt = performance.now();
        this.setCityMood("dusk");
        this.setCameraMode("free");
        this.controls.target.copy(center);
        this.camera.position.copy(eye);
        this.controls.update();
        this.recenterCityReflections();
        return;
      }
    }
    throw new Error("No unobstructed street lamp and traffic signal viewpoint in the recorded city");
  }

  private focusPreviewSignal(): void {
    if (this.trafficPreview === null || this.buildingPresentation === null) return;
    const frameIndex = Math.round(this.trafficPreview.data.duration_seconds / 2 / this.trafficPreview.data.step_seconds);
    const frame = this.trafficPreview.data.frames[frameIndex];
    const later = this.trafficPreview.data.frames[Math.min(this.trafficPreview.data.frames.length - 1,
      frameIndex + Math.round(5 / this.trafficPreview.data.step_seconds))];
    if (frame === undefined || later === undefined) throw new Error("Recorded traffic signal focus has no traffic frames");
    const candidates = this.trafficPreview.data.signals.map(signal => {
      const state = frame.tls[signal.tls]?.[signal.link]?.toLowerCase();
      const nextState = later.tls[signal.tls]?.[signal.link]?.toLowerCase();
      if (state === undefined || nextState === undefined) throw new Error(`Traffic signal state missing: ${signal.id}`);
      const vehicles = frame.vehicles.filter(vehicle => Math.hypot(vehicle[1] - signal.x, vehicle[2] - signal.z) < 45).length;
      const buildings = this.buildingPresentation!.children.filter(child =>
        Math.hypot(child.position.x - signal.x, child.position.z - signal.z) < 90).length;
      return { signal, score: (state !== nextState ? 100 : 0) + vehicles * 12 + buildings * 2 };
    }).sort((a, b) => b.score - a.score);
    for (const { signal } of candidates) {
      const model = this.trafficPreview.group.children.find(child =>
        (child.userData.signal as { id: string } | undefined)?.id === signal.id);
      if (model === undefined) continue;
      model.updateMatrixWorld(true);
      const redLensBounds = new THREE.Box3();
      const point = new THREE.Vector3();
      model.traverse(child => {
        if (!(child instanceof THREE.Mesh)) return;
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        const positions = child.geometry.getAttribute("position");
        const indices = child.geometry.index;
        const groups = child.geometry.groups.length > 0 ? child.geometry.groups
          : [{ start: 0, count: indices?.count ?? positions.count, materialIndex: 0 }];
        for (const group of groups) {
          if (!materials[group.materialIndex]?.name.endsWith("SG7")) continue;
          for (let index = group.start; index < group.start + group.count; index++) {
            point.fromBufferAttribute(positions, indices?.getX(index) ?? index).applyMatrix4(child.matrixWorld);
            redLensBounds.expandByPoint(point);
          }
        }
      });
      if (redLensBounds.isEmpty()) throw new Error(`Provided traffic signal has no red lens geometry: ${signal.id}`);
      const angle = -THREE.MathUtils.degToRad(signal.heading);
      const head = redLensBounds.getCenter(new THREE.Vector3());
      for (const side of [1, -1]) {
        const normal = new THREE.Vector3(0, 0, side).applyAxisAngle(new THREE.Vector3(0, 1, 0), angle);
        const eye = head.clone().addScaledVector(normal, 10);
        eye.y = 5.3;
        if (this.previewBuildingObstacles.some(bounds => bounds.containsPoint(eye))) continue;
        const direction = head.clone().sub(eye);
        const distance = direction.length();
        const modelHits = new THREE.Raycaster(eye, direction.clone().normalize(), 0, distance + 2)
          .intersectObject(model, true).filter(hit => hit.object instanceof THREE.Mesh);
        if (modelHits.length === 0) continue;
        const ray = new THREE.Ray(eye, direction.normalize());
        if (this.previewBuildingObstacles.some(bounds => {
          const hit = ray.intersectBox(bounds, new THREE.Vector3());
          return hit !== null && hit.distanceTo(eye) < distance - 0.5;
        })) continue;
        this.previewFollowId = null;
        this.previewSeconds = frame.second;
        this.previewLastFrameAt = performance.now();
        this.setCityMood("dusk");
        this.setCameraMode("free");
        this.controls.target.copy(head);
        this.camera.position.copy(eye);
        this.controls.update();
        this.recenterCityReflections();
        this.root.dataset.focusSignalId = signal.id;
        this.root.dataset.focusSignalHeadNdc = `${head.clone().project(this.camera).x.toFixed(3)},${head.clone().project(this.camera).y.toFixed(3)}`;
        this.root.dataset.focusSignalHeadPosition = `${head.x.toFixed(2)},${head.y.toFixed(2)},${head.z.toFixed(2)}`;
        this.root.dataset.focusSignalModelHit = modelHits[0]!.distance.toFixed(2);
        return;
      }
    }
    throw new Error("No unobstructed traffic signal close-up in the recorded city");
  }

  private focusPreviewAdvertising(): void {
    if (this.trafficPreview === null || this.buildingPresentation === null) return;
    const locations = this.buildingPresentation.userData.advertisingLocations as readonly AdvertisingLocation[];
    const obstacles = this.buildingPresentation.children.flatMap(object => {
      const placement = object.userData.collisionBox as { building_id: string; part: number } | undefined;
      return placement === undefined ? [] : [{ id: placement.building_id, part: placement.part,
                                                bounds: new THREE.Box3().setFromObject(object) }];
    });
    const location = locations.find(candidate => {
      const eye = new THREE.Vector3(candidate.x + candidate.normalX * 13,
                                    candidate.y + 2.5,
                                    candidate.z + candidate.normalZ * 13);
      if (obstacles.some(obstacle => (obstacle.id !== candidate.buildingId || obstacle.part !== candidate.buildingPart)
          && obstacle.bounds.containsPoint(eye))) return false;
      return [-0.5, 0, 0.5].every(across => {
        const target = new THREE.Vector3(candidate.x + candidate.normalZ * candidate.width * across,
                                          candidate.y,
                                          candidate.z - candidate.normalX * candidate.width * across);
        const direction = target.clone().sub(eye);
        const distance = direction.length();
        const ray = new THREE.Ray(eye, direction.normalize());
        return obstacles.every(obstacle => {
          if (obstacle.id === candidate.buildingId && obstacle.part === candidate.buildingPart) return true;
          const hit = ray.intersectBox(obstacle.bounds, new THREE.Vector3());
          return hit === null || hit.distanceTo(eye) >= distance - 0.5;
        });
      });
    });
    if (location === undefined) throw new Error("City advertising focus has no unobstructed facade");
    this.previewFollowId = null;
    this.previewSeconds = 60;
    this.previewLastFrameAt = performance.now();
    this.setCityMood("dusk");
    this.setCameraMode("free");
    this.controls.target.set(location.x, location.y, location.z);
    this.camera.position.set(location.x + location.normalX * 13,
                             location.y + 2.5,
                             location.z + location.normalZ * 13);
    this.controls.update();
    this.recenterCityReflections();
  }

  private focusPreviewFlight(id: string): void {
    if (this.trafficPreview === null) return;
    if (!this.trafficPreview.flightData.vehicle_ids.includes(id)) throw new Error(`UAV is absent from the city flight: ${id}`);
    const frame = this.trafficPreview.flightData.frames[Math.round(Math.min(30,
      this.trafficPreview.flightData.duration_seconds) / this.trafficPreview.flightData.step_seconds)];
    const aircraft = frame?.find(sample => sample[0] === id);
    if (aircraft === undefined) throw new Error(`UAV has no city flight pose at follow start: ${id}`);
    this.previewSeconds = 30;
    this.previewLastFrameAt = performance.now();
    this.setCameraMode("free");
    const center = new THREE.Vector3(aircraft[1], aircraft[3], aircraft[2]);
    const eye = this.previewFlightCameraPosition(id, center);
    this.controls.target.copy(this.previewFlightLookTarget(id, center));
    this.camera.position.copy(eye);
    this.updatingPreviewCamera = true;
    try { this.controls.update(); } finally { this.updatingPreviewCamera = false; }
    this.recenterCityReflections();
    this.previewFollowId = id;
  }

  private focusPreviewGroundEntity(kind: "bicycle" | "pedestrian"): void {
    if (this.trafficPreview === null) return;
    const midpoint = this.trafficPreview.data.duration_seconds / 2;
    const frame = this.trafficPreview.data.frames[Math.round(midpoint / this.trafficPreview.data.step_seconds)];
    const later = this.trafficPreview.data.frames[Math.round(Math.min(this.trafficPreview.data.duration_seconds,
      midpoint + 3) / this.trafficPreview.data.step_seconds)];
    if (frame === undefined || later === undefined) return;
    const samples = kind === "bicycle" ? frame.vehicles.filter(vehicle => vehicle[4] === "bicycle") : frame.persons;
    const laterSamples = new Map((kind === "bicycle" ? later.vehicles : later.persons)
      .map(sample => [sample[0], sample]));
    const candidates = samples.map(sample => {
      const after = laterSamples.get(sample[0]);
      const moved = after === undefined ? 0 : Math.hypot(after[1] - sample[1], after[2] - sample[2]);
      const nearbyMotorTraffic = kind === "bicycle" ? frame.vehicles.filter(vehicle =>
        vehicle[4] !== "bicycle" && Math.hypot(vehicle[1] - sample[1], vehicle[2] - sample[2]) < 50).length : 0;
      return { sample, moved, nearbyMotorTraffic };
    }).filter(item => item.moved > 2 && (kind === "pedestrian"
      || Math.hypot(item.sample[1], item.sample[2]) < 400))
      .sort((left, right) => (right.moved + right.nearbyMotorTraffic * 8)
        - (left.moved + left.nearbyMotorTraffic * 8));
    if (candidates.length === 0) return;
    const selected = candidates[this.previewGroundFocusIndex[kind] % candidates.length]!.sample;
    this.previewGroundFocusIndex[kind]++;
    this.previewSeconds = frame.second;
    this.previewLastFrameAt = performance.now();
    this.setCameraMode("free");
    const height = kind === "bicycle" ? 1 : 1.5;
    this.controls.target.set(selected[1], height, selected[2]);
    this.camera.position.set(selected[1] + (kind === "bicycle" ? 6 : 3),
                             height + (kind === "bicycle" ? 2.5 : 1.8),
                             selected[2] + (kind === "bicycle" ? 8 : 4));
    this.controls.update();
    this.recenterCityReflections();
    this.previewFollowId = selected[0];
  }

  private position(coordinate: ResolvedCoordinate, lift = 0): THREE.Vector3 {
    if (this.nativePresentation != null) {
      return enuPosition(coordinate.enu.east_m, coordinate.enu.north_m, coordinate.enu.up_m + lift);
    }
    const projected = projectGeographic(coordinate.wgs84.latitude_deg, coordinate.wgs84.longitude_deg, this.projectionOrigin);
    return enuPosition(projected.east, projected.north, coordinate.enu.up_m + lift);
  }

  render(scene: MapScene, view: MapView): void {
    if (this.operationsMonitor !== null && this.operationsMonitor !== undefined) {
      const identity = scene.operationContext?.runId ?? scene.sceneState?.run_id ?? scene.scenario?.scenario_digest ?? scene.authoring?.key ?? "preview";
      if (identity !== this.observationSource) {
        this.observationSource = identity; this.observationSelection = view.selected;
        this.previewRequested = false; this.overviewCamera = null;
        if (this.mode !== "free") this.setCameraMode("free");
        this.lastMonitorUpdateMs = -Infinity; this.lastSecondaryRenderMs = -Infinity;
      }
      if (view.selected !== this.observationView?.selected) this.observationSelection = view.selected;
      this.observationScene = scene; this.observationView = view;
    }
    if (this.destroyed) return;
    if (scene.scenario === null && scene.nativePresentation != null) {
      throw new Error("Native city presentation requires its declared public scenario");
    }
    this.root.dataset.sceneTick = scene.sceneState === null ? "" : String(scene.sceneState.at.tick);
    this.root.dataset.renderedUavCount = "0";
    if (scene.authoring !== undefined) {
      if (scene.scenario !== null || scene.sceneState !== null || scene.trajectories.length !== 0
        || scene.networkFrame !== null || scene.pack != null || scene.nativePresentation != null || scene.osm != null
        || scene.trafficLightFrame != null || (scene.trafficLights?.length ?? 0) > 0 || scene.tick != null) {
        throw new Error("作者静态预览不能包含正式场景或运行轨迹");
      }
      const key = `authoring:${scene.authoring.key}`;
      if (this.sourceKey !== key) {
        this.clearScene(); this.sourceKey = key; this.conversionGeneration++;
        this.root.dataset.sceneReady = "false"; this.root.dataset.texturesReady = "false";
        delete this.root.dataset.sceneError; delete this.root.dataset.sceneErrorMessage;
        this.horizon.visible = false;
        if (scene.authoring.presentation === null) {
          this.sceneLoading.hidden = true;
          this.callbacks.onBasemapNote?.("选区静态预览 · 等待完整城市呈现 · 尚未运行");
        } else {
          this.beginSceneLoading("装配本选区的已验证建筑与道路");
          void this.loadPackedScene(this.conversionGeneration,
            scene.authoring.presentation.pack, scene.authoring.presentation);
        }
      }
      this.applyStaticView(view);
      if (scene.authoring.presentation === null) this.renderObservation();
      return;
    }
    if (scene.scenario === null) {
      if (this.sourceKey !== "default-pack") {
        this.clearScene(); this.sourceKey = "default-pack"; this.conversionGeneration++;
        this.root.dataset.sceneReady = "false"; this.root.dataset.texturesReady = "false";
        delete this.root.dataset.sceneError;
        delete this.root.dataset.sceneErrorMessage;
        this.beginSceneLoading("读取城市配置");
        void this.loadPackedScene(this.conversionGeneration);
      }
      this.applyStaticView(view);
      if (this.trafficPreview === null || !this.previewPlaying) this.renderObservation();
      return;
    }
    if (scene.pack != null && scene.nativePresentation != null) {
      throw new Error("A native scenario must use exactly one declared presentation route");
    }
    if (scene.pack == null && scene.nativePresentation == null) {
      if (this.sourceKey !== "awaiting-pack") {
        this.clearScene(); this.sourceKey = "awaiting-pack"; this.conversionGeneration++;
        // The application reports presentation loading progress and failures; a second panel here
        // would claim an indefinite mesh-pack wait, including for routes that need no pack.
        this.sceneLoading.hidden = true;
      }
      this.root.dataset.sceneReady = "false";
      this.callbacks.onBasemapNote?.(scene.scenario.layers.some(layer => layer.kind === "osm_mesh") ? "Loading verified official mesh pack" : "Scene publication is missing its required official mesh pack");
      this.renderObservation();
      return;
    }
    const key = scene.scenario.scenario_digest;
    if (scene.nativePresentation != null) {
      const native = scene.nativePresentation;
      if (native.group.userData.scenarioDigest !== key
          || native.group.userData.worldDigest !== scene.scenario.world_digest) {
        throw new Error("Native city presentation identity differs from the active scenario");
      }
      if (key !== this.sourceKey || this.nativePresentation !== native) {
        this.clearScene(); this.sourceKey = key; this.conversionGeneration += 1;
        this.nativePresentation = native;
        this.projectionOrigin = scene.scenario.frame_authority.origin.wgs84;
        this.scene.add(native.group);
        for (const building of native.layers.buildings.children) {
          const entityId = building.userData.entityId;
          if (typeof entityId !== "string") throw new Error("Native building is missing its declared entity identity");
          building.userData.entityKind = "static_asset";
          this.dynamic.set(`entity:${entityId}`, building);
        }
        this.addScenarioOverlay(scene.scenario, true);
        this.cityEnvelope.copy(native.bounds);
        this.frameExtent({ west: native.bounds.min.x, east: native.bounds.max.x,
          south: -native.bounds.max.z, north: -native.bounds.min.z });
        this.root.dataset.nativeCityPresentation = "verified-declared-assets";
        this.root.dataset.nativeCityStats = JSON.stringify(native.stats);
        this.root.dataset.sceneReady = "true";
        this.root.dataset.texturesReady = "true";
        delete this.root.dataset.sceneError; delete this.root.dataset.sceneErrorMessage;
        this.sceneLoading.hidden = true;
        this.callbacks.onBasemapNote?.("Native city: verified declared building GLBs, road surfaces and fixture envelopes; public state only");
        this.callbacks.onSceneStatus?.(key, "ready");
      }
    } else if (key !== this.sourceKey) {
      this.sourceKey = key; this.conversionGeneration += 1;
      this.root.dataset.sceneReady = "false"; this.root.dataset.texturesReady = "false";
      delete this.root.dataset.sceneError;
      delete this.root.dataset.sceneErrorMessage;
      this.clearScene(); this.projectionOrigin = scene.pack!.manifest.projection.origin;
      this.beginSceneLoading("装配已验证的场景网格");
      this.addScenarioOverlay(scene.scenario); void this.loadPackedScene(this.conversionGeneration, scene.pack!);
    }
    const sampled = new Set<string>();
    for (const sample of scene.sceneState?.samples ?? []) {
      sampled.add(sample.entity_id); let object = this.dynamic.get(`entity:${sample.entity_id}`);
      if (object === undefined) { const definition = scene.scenario.entities.find(entity => entity.entity_id === sample.entity_id); const kind = (definition?.kind ?? "static_asset") as EntityKind; object = makeEntity({ id: sample.entity_id, kind, east_m: 0, north_m: 0, up_m: 0, heading_deg: 0, color: `#${entityColors[kind].toString(16).padStart(6, "0")}` }); this.dynamic.set(`entity:${sample.entity_id}`, object); this.overlays.add(object); if (definition !== undefined && this.nativePresentation === null) this.attachEntityVisual(object, kind, definition.model_asset_id, this.conversionGeneration); }
      object.position.copy(this.position(sample.pose.position));
      const q = sample.pose.orientation_enu;
      object.quaternion.set(q.qx, q.qz, -q.qy, q.qw);
      const kind = object.userData.entityKind as EntityKind; object.visible = this.entityVisible(kind, sample.entity_id, view);
    }
    for (const [keyName, object] of this.dynamic) { const id = keyName.slice("entity:".length); const kind = object.userData.entityKind as EntityKind; const definition = scene.scenario?.entities.find((entity) => entity.entity_id === id); object.visible = (definition?.state !== "dynamic" || sampled.has(id)) && this.entityVisible(kind, id, view); }
    for (const object of this.signals.values()) { const target = object.userData.target as TraceTarget; object.visible = view.layers.roads && (view.isolate === null || (view.isolate.kind === "traffic_signal" && view.isolate.id === target.id)); }
    this.applyStaticView(view);
    this.root.dataset.renderedUavCount = String([...this.dynamic.values()]
      .filter(object => object.visible && object.userData.entityKind === "uav").length);
    this.updateTrafficLights(scene.trafficLightFrame ?? null);
    this.updateCamera();
    this.renderTrajectories(scene.trajectories, scene.scenario, view, scene.tick ?? scene.sceneState?.at.tick ?? null); this.renderNetwork(scene.networkFrame, view); this.renderObservation();
  }
  private staticBounds(target: TraceTarget): THREE.Box3 {
    if (this.nativePresentation != null) {
      const visual = this.nativePresentation.layers.buildings.children.find(child =>
        target.kind === "building" ? child.userData.target?.id === target.id
          : target.kind === "entity" && child.userData.entityId === target.id);
      return visual === undefined ? new THREE.Box3() : new THREE.Box3().setFromObject(visual);
    }
    if (target.kind === "building") {
      const visuals = this.buildingPresentation?.children.filter(child => child.userData.target?.id === target.id) ?? [];
      if (visuals.length) {
        const bounds = new THREE.Box3();
        for (const visual of visuals) bounds.union(new THREE.Box3().setFromObject(visual));
        return bounds;
      }
      const source = this.buildingBounds.get(target.id);
      if (source !== undefined) return source.clone();
    }
    const bounds = new THREE.Box3();
    const point = new THREE.Vector3();
    for (const child of this.world.children) {
      const mesh = child as THREE.Mesh;
      const ranges = mesh.userData.ranges as { end: number; target: TraceTarget | null }[];
      const positions = mesh.geometry.getAttribute("position"), indices = mesh.geometry.index!;
      let start = 0;
      for (const range of ranges) {
        if (range.target?.kind === target.kind && range.target.id === target.id) {
          for (let i = start * 3; i < range.end * 3; i++) bounds.expandByPoint(point.fromBufferAttribute(positions, indices.getX(i)));
        }
        start = range.end;
      }
    }
    return bounds;
  }
  /** Per-building visibility rule shared by the view update and streamed placement. */
  private buildingVisible(view: MapView, building: THREE.Object3D): boolean {
    const target = building.userData.target as TraceTarget;
    return view.layers.buildings && !view.hiddenEntities.has(`building:${target.id}`)
      && (view.isolate === null || (view.isolate.kind === "building" && view.isolate.id === target.id))
      && (building.userData.cityStorefrontLight !== true || this.cityMood === "dusk");
  }
  private applyStaticView(view: MapView): void {
    const prior = this.staticView;
    this.staticView = view;
    if (this.nativePresentation != null) {
      this.nativePresentation.setLayerVisibility({ buildings: view.layers.buildings,
        roads: view.layers.roads, regions: view.layers.regions && view.layers.terrain,
        static_assets: view.layers.static_assets });
      for (const child of this.nativePresentation.layers.buildings.children) {
        const target = child.userData.target as TraceTarget;
        const entityId = child.userData.entityId as string;
        child.visible = view.layers.buildings && !view.hiddenEntities.has(`building:${target.id}`)
          && !view.hiddenEntities.has(`entity:${entityId}`)
          && (view.isolate === null || (view.isolate.kind === "building" && view.isolate.id === target.id)
            || (view.isolate.kind === "entity" && view.isolate.id === entityId));
      }
      this.nativePresentation.layers.roads.visible &&= view.isolate === null;
      this.nativePresentation.layers.fixtures.visible &&= view.isolate === null;
      this.nativePresentation.layers.regions.visible &&= view.isolate === null;
    }
    const selectedChanged = prior?.selected?.id !== view.selected?.id || prior?.selected?.kind !== view.selected?.kind;
    const isolatedChanged = prior?.isolate?.id !== view.isolate?.id || prior?.isolate?.kind !== view.isolate?.kind || prior?.hiddenEntities !== view.hiddenEntities;
    const sourceRoadsNeeded = view.isolate !== null || [...view.hiddenEntities].some(id => id.startsWith("road:"));
    const selectedStaticCity = this.sourceKey.startsWith("authoring:");
    const usePreviewRoads = this.roadPresentation !== null && (selectedStaticCity || !sourceRoadsNeeded);
    if (this.roadPresentation !== null) this.roadPresentation.visible = usePreviewRoads && view.layers.roads;
    if (this.staticSignalPresentation !== null) this.staticSignalPresentation.visible = view.layers.roads;
    for (const child of this.world.children) {
      const mesh = child as THREE.Mesh;
      mesh.visible = view.layers[mesh.userData.layer as "buildings" | "roads" | "terrain"];
      if ((usePreviewRoads || selectedStaticCity) && mesh.userData.layer === "roads") mesh.visible = false;
      if (isolatedChanged || prior === null) {
        mesh.geometry.clearGroups();
        const ranges = mesh.userData.ranges as { end: number; target: TraceTarget | null }[];
        if (view.isolate === null && view.hiddenEntities.size === 0) {
          mesh.geometry.addGroup(0, mesh.geometry.index!.count, 0);
          mesh.userData.isolatedVisible = true;
          continue;
        }
        let start = 0;
        let visibleCount = 0;
        for (const range of ranges) {
          const hidden = range.target !== null && view.hiddenEntities.has(`${range.target.kind}:${range.target.id}`);
          if (!hidden && (view.isolate === null || (range.target?.id === view.isolate.id && range.target.kind === view.isolate.kind))) {
            mesh.geometry.addGroup(start * 3, (range.end - start) * 3, 0);
            visibleCount += range.end - start;
          }
          start = range.end;
        }
        mesh.userData.isolatedVisible = visibleCount > 0;
      }
      mesh.visible &&= mesh.userData.isolatedVisible ?? true;
    }
    if (this.buildingPresentation !== null) for (const child of this.buildingPresentation.children) {
      if (child.userData.buildingRenderBatches === true) continue;
      if (child.userData.roofInstances !== undefined) {
        child.visible = view.layers.buildings;
        continue;
      }
      child.visible = this.buildingVisible(view, child);
    }
    this.renderStreamer?.batches.flush();
    if (this.buildingPresentation !== null) syncMeteredRoofVisibility(this.buildingPresentation);
    if (this.vegetationPresentation !== null) this.vegetationPresentation.visible = view.layers.terrain && view.isolate === null;
    if (this.authoredLandscapeLayer !== null) this.authoredLandscapeLayer.group.visible = view.layers.terrain && view.isolate === null;
    if (this.cityVegetationLayer !== null) this.cityVegetationLayer.group.visible = view.layers.terrain && view.isolate === null;
    if (selectedChanged || prior === null) {
      const target = view.selected;
      const bounds = target === null ? new THREE.Box3() : this.staticBounds(target);
      this.selectionOutline.box.copy(bounds);
    }
    const selected = view.selected;
    const layerVisible = selected?.kind === "building" ? view.layers.buildings : selected?.kind === "road" ? view.layers.roads : false;
    this.selectionOutline.visible = selected !== null && layerVisible && !this.selectionOutline.box.isEmpty()
      && !view.hiddenEntities.has(`${selected.kind}:${selected.id}`)
      && (view.isolate === null || (view.isolate.kind === selected.kind && view.isolate.id === selected.id));
  }
  private entityVisible(kind: EntityKind, id: string, view: MapView): boolean { const layer = kind === "static_asset" ? "static_assets" : kind; return view.layers[layer] && !view.hiddenEntities.has(`entity:${id}`) && (view.isolate === null || (view.isolate.kind === "entity" && view.isolate.id === id)); }
  private renderTrajectories(trajectories: readonly PublicTrajectory[], scenario: PublicScenario | null, view: MapView, tick: number | null): void { const active = new Set<string>(); for (const trajectory of trajectories) { active.add(trajectory.entity_id); const kind = (scenario?.entities.find((entity) => entity.entity_id === trajectory.entity_id)?.kind ?? "static_asset") as EntityKind; const points = trajectorySegments(trajectory, tick).flatMap(pair => pair.map(sample => this.position(sample.pose.position, 0.4))); let line = this.trajectories.get(trajectory.entity_id); if (line === undefined) { line = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: entityColors[kind], transparent: true, opacity: 0.9 })); line.userData.target = { kind: "entity", id: trajectory.entity_id } satisfies TraceTarget; this.trajectories.set(trajectory.entity_id, line); this.scene.add(line); } line.material.color.set(view.selected?.kind === "entity" && view.selected.id === trajectory.entity_id ? 0xffc857 : entityColors[kind]); line.material.opacity = view.selected?.kind === "entity" && view.selected.id === trajectory.entity_id ? 1 : 0.9; line.geometry.dispose(); line.geometry = new THREE.BufferGeometry().setFromPoints(points); line.visible = view.layers.trajectories && !view.hiddenTrajectories.has(trajectory.entity_id) && (view.isolate === null || (view.isolate.kind === "entity" && view.isolate.id === trajectory.entity_id)); } for (const [id, line] of this.trajectories) if (!active.has(id)) line.visible = false; }
  private renderNetwork(frame: PublicNetworkFrame | null, view: MapView): void { if (frame === null) { for (const line of this.networks.values()) line.visible = false; return; } const active = new Set<string>(); for (const link of frame.links) { active.add(link.link_id); const points = [link.source_pose.position, link.destination_pose.position].map((position) => this.position(position)); let line = this.networks.get(link.link_id); if (line === undefined) { line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x267d9a, transparent: true, opacity: 0.8 })); line.userData.target = { kind: "network_link", id: link.link_id } satisfies TraceTarget; this.networks.set(link.link_id, line); this.scene.add(line); } line.geometry.dispose(); line.geometry = new THREE.BufferGeometry().setFromPoints(points); line.visible = view.layers.network_links && (view.isolate === null || (view.isolate.kind === "network_link" && view.isolate.id === link.link_id)); } for (const [id, line] of this.networks) if (!active.has(id)) line.visible = false; }
  private updateTrafficLights(frame: PublicTrafficLightFrame | null): void {
    const states = new Map((frame?.states ?? []).map((state) => [state.signal_id, state]));
    for (const object of this.signals.values()) {
      const signal = object.userData.signal as OsmTrafficSignal;
      const lamps = object.userData.lamps as THREE.Mesh[];
      const observed = states.get(signal.id);
      const state = observed?.state.toLowerCase() ?? "";
      const active = state.includes("g") ? 2 : state.includes("y") ? 1 : 0;
      lamps.forEach((lamp, index) => {
        (lamp.material as THREE.MeshStandardMaterial).emissiveIntensity =
          observed === undefined ? 0.02 : index === active ? 0.95 : 0.08;
      });
      object.userData.trafficLightFrame = frame?.event_id ?? null;
      object.userData.trafficLightTelemetrySource = observed?.telemetry_source ?? null;
    }
  }
  /** P02 config readback: which scene the map actually assembled last. */
  sceneSourceLabel(): string {
    return this.root.dataset.sceneSource ?? this.sourceKey ?? "";
  }

  /** Pass through the application's exact current selector result without joining business records here. */
  setP02BusinessFrame(business: SelectedFrameBusinessView | null): void {
    if (this.destroyed || this.p02BusinessFrame === business) return;
    this.p02BusinessFrame = business;
    this.renderObservation(true);
  }

  /**
   * P02: move the free camera to an authored overview pose expressed in the
   * trace's declared frame-authority ENU metres. The pose comes from the
   * application, which fits only valid carrier/route positions; the map just
   * converts it through the current recorded coordinate authority. Packed
   * scenes anchor local ENU deltas to an actual sample projected to the pack
   * origin. A null pose is a no-op; the caller keeps
   * camera authority either way and following is not engaged.
   */
  frameP02Overview(pose: {
    position: { east: number; north: number; up: number };
    target: { east: number; north: number; up: number };
  } | null, currentFrame: SceneState | null = this.observationScene?.sceneState ?? null): boolean {
    if (this.destroyed || pose === null) return false;
    const anchor = currentFrame?.samples.filter(sample => {
      const enu = sample.pose.position.enu;
      return [enu.east_m, enu.north_m, enu.up_m].every(Number.isFinite);
    }).sort((left, right) => {
      const distance = (coordinate: ResolvedCoordinate): number => Math.hypot(
        coordinate.enu.east_m - pose.target.east, coordinate.enu.north_m - pose.target.north);
      return distance(left.pose.position) - distance(right.pose.position);
    })[0]?.pose.position;
    if (anchor === undefined) return false;
    const mappedAnchor = this.position(anchor);
    const mapPoint = (point: { east: number; north: number; up: number }): THREE.Vector3 =>
      mappedAnchor.clone().add(enuPosition(point.east - anchor.enu.east_m,
        point.north - anchor.enu.north_m, point.up - anchor.enu.up_m));
    const target = mapPoint(pose.target);
    const position = mapPoint(pose.position);
    if (![target.x, target.y, target.z, position.x, position.y, position.z].every(Number.isFinite)) return false;
    this.controls.target.copy(target);
    this.camera.position.copy(position);
    this.camera.lookAt(target);
    this.setFollow(null);
    this.controls.update();
    this.root.dataset.p02OverviewFrame = this.nativePresentation != null ? "declared-enu" : "recorded-wgs84-to-pack";
    this.root.dataset.p02OverviewAnchorEnu = [anchor.enu.east_m, anchor.enu.north_m, anchor.enu.up_m]
      .map(value => value.toFixed(3)).join(",");
    this.root.dataset.p02OverviewAnchorWorld = mappedAnchor.toArray().map(value => value.toFixed(3)).join(",");
    this.root.dataset.p02OverviewTarget = [target.x, target.y, target.z].map(value => value.toFixed(3)).join(",");
    this.root.dataset.p02OverviewPosition = [position.x, position.y, position.z].map(value => value.toFixed(3)).join(",");
    this.renderObservation();
    return true;
  }
  /**
   * P02: show persistent parcel-id labels above the carrier entities that
   * business records explicitly bind. `labels` maps carrier entity id ->
   * authored parcel id; entities without an entry keep their existing
   * presentation. Rebuilds only when the declared id set changes; every
   * later frame just copies positions.
   */
  setP02CargoLabels(labels: ReadonlyMap<string, string>, visible: boolean): void {
    if (this.destroyed) return;
    this.p02CargoLabelsVisible = visible;
    const declared = visible ? [...labels.entries()].sort(([left], [right]) => left.localeCompare(right))
      .map(([id, label]) => `${id}:${label}`).join("\u0000") : "";
    if (declared !== (this.root.dataset.p02CargoLabelSet ?? "")) {
      this.p02DisposeCargoLabels();
      this.root.dataset.p02CargoLabelSet = declared;
      if (visible && labels.size > 0) {
        const group = new THREE.Group();
        group.name = "P02 cargo id labels";
        group.userData.provenance = "authored-business-overlay";
        group.renderOrder = 5;
        for (const [entityId, text] of labels) {
          const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
            map: p02CargoLabelTexture(compactCargoLabel(text)), transparent: true, depthWrite: false, depthTest: false, sizeAttenuation: false,
          }));
          sprite.center.set(0, 0.5);
          sprite.userData.p02EntityId = entityId;
          sprite.userData.p02ParcelId = text;
          sprite.userData.visualOnly = true;
          const icon = new THREE.Sprite(new THREE.SpriteMaterial({ map: p02BusinessIconTexture("cargo"),
            transparent: true, depthWrite: false, depthTest: false, sizeAttenuation: false }));
          const leader = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({
            color: 0x1f7fd4, transparent: true, opacity: 0.85, depthTest: false, depthWrite: false }));
          sprite.userData.p02Icon = icon;
          sprite.userData.p02Leader = leader;
          group.add(leader, icon, sprite);
          this.p02CargoLabels.set(entityId, sprite);
        }
        this.p02CargoLabelGroup = group;
        this.scene.add(group);
      }
    }
    if (this.p02CargoLabelGroup !== null) this.p02CargoLabelGroup.visible = visible;
    this.p02UpdateCargoLabelPositions();
  }

  setP02NativeParcels(records: readonly BusinessIdentityView[]): void {
    this.p02NativeParcels = records;
  }

  /** Parcel position follows exact current custody authority, including explicit unavailable positions. */
  private p02UpdateCargoLabelPositions(): void {
    const group = this.p02CargoLabelGroup;
    if (group === null || !this.p02CargoLabelsVisible) return;
    for (const sprite of this.p02CargoLabels.values()) {
      const scene = this.observationScene;
      const anchor = businessParcelAnchor(sprite.userData.p02ParcelId as string, this.p02BusinessFrame,
        scene?.scenario ?? null, scene?.sceneState ?? null);
      const native = this.p02NativeParcels.find(record => record.parcelId === sprite.userData.p02ParcelId
        && record.tick === scene?.sceneState?.at.tick && record.parcelPositionEnu !== undefined);
      const pose = native?.parcelPositionEnu;
      const coordinate = scene?.sceneState?.samples[0]?.pose.position;
      const nativeWorld = pose === undefined || coordinate === undefined ? null
        : this.position(coordinate).add(enuPosition(pose.east_m - coordinate.enu.east_m,
            pose.north_m - coordinate.enu.north_m, pose.up_m - coordinate.enu.up_m));
      sprite.userData.p02PlacementState = nativeWorld !== null ? "native-parcel-projection"
        : anchor === null ? "unknown-custody-position" : "authored-custody-position";
      sprite.userData.p02CustodyHolderId = native?.custodyId ?? anchor?.holderId ?? null;
      const world = nativeWorld ?? (anchor === null ? null : this.position(anchor.position));
      if (world === null) {
        sprite.visible = false;
        (sprite.userData.p02Icon as THREE.Sprite).visible = false;
        (sprite.userData.p02Leader as THREE.Line).visible = false;
        continue;
      }
      const icon = sprite.userData.p02Icon as THREE.Sprite;
      const leader = sprite.userData.p02Leader as THREE.Line;
      const projected = world.clone().project(this.camera);
      const visible = projected.z >= -1 && projected.z <= 1;
      sprite.visible = icon.visible = leader.visible = visible;
      if (!visible) continue;
      const height = this.root.clientHeight || 480;
      const width = this.root.clientWidth || 640;
      const offset = (x: number, y: number): THREE.Vector3 => projected.clone()
        .add(new THREE.Vector3(x * 2 / width, y * 2 / height, 0)).unproject(this.camera);
      icon.position.copy(offset(24, 28));
      sprite.position.copy(offset(35, 28));
      const pixelScale = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) / height;
      icon.scale.set(16 * pixelScale, 16 * pixelScale, 1);
      sprite.scale.set(82 * pixelScale, 20 * pixelScale, 1);
      leader.geometry.dispose();
      leader.geometry = new THREE.BufferGeometry().setFromPoints([world, offset(24, 28)]);
    }
  }

  private p02DisposeCargoLabels(): void {
    if (this.p02CargoLabelGroup !== null) {
      this.scene.remove(this.p02CargoLabelGroup);
      for (const child of this.p02CargoLabelGroup.children) {
        if (child instanceof THREE.Sprite) { child.material.map?.dispose(); child.material.dispose(); }
        else if (child instanceof THREE.Line) { child.geometry.dispose(); disposeMaterial(child.material); }
      }
      this.p02CargoLabelGroup = null;
    }
    this.p02CargoLabels.clear();
    delete this.root.dataset.p02CargoLabelSet;
  }

  private p02UpdateDestinationMarkers(): void {
    const scene = this.observationScene;
    const markers = businessDestinationMarkers(this.p02BusinessFrame, scene?.scenario ?? null, scene?.sceneState ?? null);
    const active = new Set(markers.map(marker => marker.destinationId));
    this.root.dataset.p02DestinationState = markers.length === 0 ? "unknown" : "authored-entity-position";
    this.root.dataset.p02DestinationIds = [...active].join(",");
    for (const [id, sprite] of this.p02DestinationMarkers) if (!active.has(id)) sprite.visible = false;
    if (markers.length === 0) return;
    if (this.p02DestinationGroup === null) {
      this.p02DestinationGroup = new THREE.Group();
      this.p02DestinationGroup.userData.provenance = "authored-business-overlay";
      this.scene.add(this.p02DestinationGroup);
    }
    for (const marker of markers) {
      let sprite = this.p02DestinationMarkers.get(marker.destinationId);
      if (sprite === undefined) {
        sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: p02BusinessIconTexture("destination"),
          transparent: true, depthWrite: false, depthTest: false, sizeAttenuation: false }));
        sprite.userData.p02DestinationId = marker.destinationId;
        sprite.userData.visualOnly = true;
        this.p02DestinationGroup.add(sprite);
        this.p02DestinationMarkers.set(marker.destinationId, sprite);
      }
      sprite.position.copy(this.position(marker.position, 2));
      const pixelScale = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) / (this.root.clientHeight || 480);
      sprite.scale.set(20 * pixelScale, 20 * pixelScale, 1);
      sprite.visible = true;
    }
  }

  private p02DisposeDestinationMarkers(): void {
    if (this.p02DestinationGroup !== null) this.scene.remove(this.p02DestinationGroup);
    for (const sprite of this.p02DestinationMarkers.values()) { sprite.material.map?.dispose(); sprite.material.dispose(); }
    this.p02DestinationMarkers.clear();
    this.p02DestinationGroup = null;
  }

  private p02UpdateAircraftMarkers(): void {
    const scene = this.observationScene;
    const active = new Set<string>();
    if (this.p02CargoLabelsVisible && scene?.scenario != null && scene.sceneState != null) {
      for (const sample of scene.sceneState.samples) {
        const definition = scene.scenario.entities.find(entity => entity.entity_id === sample.entity_id);
        if (definition?.kind !== "uav" || sample.at.tick !== scene.sceneState.at.tick) continue;
        const object = this.dynamic.get(`entity:${sample.entity_id}`);
        if (object === undefined || !object.visible) continue;
        active.add(sample.entity_id);
        if (this.p02AircraftGroup === null) {
          this.p02AircraftGroup = new THREE.Group();
          this.p02AircraftGroup.name = "Recorded UAV screen markers";
          this.p02AircraftGroup.userData.provenance = "recorded-entity-position-marker";
          this.p02AircraftGroup.renderOrder = 6;
          this.scene.add(this.p02AircraftGroup);
        }
        let sprite = this.p02AircraftMarkers.get(sample.entity_id);
        if (sprite === undefined) {
          sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: p02AircraftIconTexture(), transparent: true,
            depthTest: false, depthWrite: false, sizeAttenuation: false }));
          sprite.userData.target = { kind: "entity", id: sample.entity_id } satisfies TraceTarget;
          sprite.userData.p02EntityId = sample.entity_id;
          sprite.userData.visualOnly = true;
          this.p02AircraftGroup.add(sprite);
          this.p02AircraftMarkers.set(sample.entity_id, sprite);
        }
        sprite.position.copy(this.position(sample.pose.position));
        const ndc = sprite.position.clone().project(this.camera);
        const scale = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) / (this.root.clientHeight || 480);
        sprite.scale.set(24 * scale, 24 * scale, 1);
        sprite.visible = ndc.z >= -1 && ndc.z <= 1;
      }
    }
    for (const [id, sprite] of this.p02AircraftMarkers) if (!active.has(id)) sprite.visible = false;
    this.root.dataset.p02AircraftMarkerIds = [...active].join(",");
    this.root.dataset.p02CarrierModelState = this.nativePresentation !== null ? "native-entity-glb-binding-unavailable" : "viewer-model-route";
  }

  private p02DisposeAircraftMarkers(): void {
    if (this.p02AircraftGroup !== null) this.scene.remove(this.p02AircraftGroup);
    for (const sprite of this.p02AircraftMarkers.values()) { sprite.material.map?.dispose(); sprite.material.dispose(); }
    this.p02AircraftMarkers.clear();
    this.p02AircraftGroup = null;
  }

  destroy(): void {
    this.unsubscribeGroundLanguage();
    document.removeEventListener("visibilitychange", this.resetPreviewClock);
    cancelAnimationFrame(this.hoverAnimation); this.hoverAnimation = 0; this.pendingHover = null;
    this.operationsMonitor?.dispose(); this.operationsMonitor = null;
    this.secondaryTarget?.dispose(); this.secondaryTarget = null;
    this.destroyed = true; this.resizeObserver.disconnect(); this.controls.dispose(); this.clearScene();
    this.p02DisposeCargoLabels();
    this.p02DisposeDestinationMarkers();
    this.p02DisposeAircraftMarkers();
    disposeCityDaySky(this.cityDaySky);
    this.daySky.dispose(); this.duskSky.dispose(); this.dayReflection.dispose(); this.duskReflection.dispose();
    this.horizon.geometry.dispose(); this.horizon.material.map?.dispose(); this.horizon.material.dispose();
    this.outOfExtentGround.geometry.dispose(); this.outOfExtentGround.material.dispose();
    this.selectionOutline.geometry.dispose(); disposeMaterial(this.selectionOutline.material);
    this.sun.shadow.dispose(); this.renderer.dispose(); this.root.replaceChildren();
  }
}

/**
 * P02 compact parcel label: full authored identity stays in inspector and metadata.
 * Independent implementation (no external host
 * code); one canvas per record, disposed with the label group.
 */
function p02CargoLabelTexture(text: string): THREE.CanvasTexture {
  if (typeof document === "undefined") {
    throw new Error("P02 cargo labels require a browser document");
  }
  const canvas = document.createElement("canvas");
  canvas.width = 344;
  canvas.height = 84;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("P02 cargo label canvas is unavailable");
  context.clearRect(0, 0, canvas.width, canvas.height);
  const radius = 22;
  context.beginPath();
  context.moveTo(radius, 4);
  context.arcTo(canvas.width - 4, 4, canvas.width - 4, canvas.height - 4, radius);
  context.arcTo(canvas.width - 4, canvas.height - 4, 4, canvas.height - 4, radius);
  context.arcTo(4, canvas.height - 4, 4, 4, radius);
  context.arcTo(4, 4, canvas.width - 4, 4, radius);
  context.closePath();
  context.fillStyle = "rgba(255, 255, 255, 0.92)";
  context.fill();
  context.lineWidth = 6;
  context.strokeStyle = "#1f7fd4";
  context.stroke();
  context.fillStyle = "#123a5c";
  context.font = "600 42px Inter, 'Noto Sans SC', system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(text, canvas.width / 2, canvas.height / 2 + 2, canvas.width - 40);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function p02BusinessIconTexture(kind: "cargo" | "destination"): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("P02 business icon canvas is unavailable");
  context.fillStyle = "#ffffff";
  context.strokeStyle = "#1f7fd4";
  context.lineWidth = 5;
  context.lineJoin = "round";
  context.beginPath();
  if (kind === "cargo") {
    context.moveTo(10, 18); context.lineTo(32, 7); context.lineTo(54, 18);
    context.lineTo(54, 46); context.lineTo(32, 58); context.lineTo(10, 46); context.closePath();
    context.fill(); context.stroke();
    context.beginPath(); context.moveTo(10, 18); context.lineTo(32, 30); context.lineTo(54, 18);
    context.moveTo(32, 30); context.lineTo(32, 58); context.stroke();
  } else {
    context.moveTo(32, 58); context.bezierCurveTo(24, 43, 11, 36, 11, 24);
    context.arc(32, 24, 21, Math.PI, 0); context.bezierCurveTo(53, 36, 40, 43, 32, 58);
    context.fill(); context.stroke();
    context.beginPath(); context.arc(32, 24, 7, 0, Math.PI * 2); context.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function p02AircraftIconTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas"); canvas.width = canvas.height = 64;
  const context = canvas.getContext("2d");
  if (context === null) throw new Error("P02 aircraft marker canvas is unavailable");
  context.fillStyle = "rgba(255,255,255,.94)";
  context.beginPath(); context.arc(32, 32, 31, 0, Math.PI * 2); context.fill();
  context.strokeStyle = "#1264b0"; context.lineWidth = 5;
  context.beginPath(); context.moveTo(17, 17); context.lineTo(47, 47);
  context.moveTo(47, 17); context.lineTo(17, 47); context.stroke();
  for (const [x, y] of [[17, 17], [47, 17], [17, 47], [47, 47]]) {
    context.beginPath(); context.arc(x!, y!, 9, 0, Math.PI * 2); context.stroke();
  }
  context.fillStyle = "#1264b0"; context.fillRect(26, 23, 12, 18);
  const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
