import "./styles.css";
import "./city-studio.css";

import { AssetResolver } from "./asset-resolver";
import {
  buildingPlacement, fetchBuildingRenderManifest,
} from "./city-building-renders";
import {
  fetchAuthoringCatalog, fetchAuthoringJob, fetchAuthoringSource, fetchNativeSceneCatalog, loadReadyAuthoringPresentation,
  submitSceneSelection, type AuthoringCatalog, type AuthoringJob, type AuthoringSource,
  type NativeSceneRegistration, type VerifiedStaticPresentation,
} from "./city-authoring-api";
import { CityRegionSelector, type SceneSelection } from "./city-region-selector";
import { renderCityAlgorithmPanel } from "./city-algorithm-panel";
import { renderCityCompilePanel, type CityCompilePanelHandle, type CityCompiledSelection } from "./city-compile-panel";
import { renderCityEventPanel } from "./city-event-panel";
import { renderCityEventTimeline, type CityEventTimelineHandle } from "./city-event-timeline";
import { renderCityLandscapePanel } from "./city-landscape-panel";
import { renderCityRuntimePanel } from "./city-runtime-panel";
import {
  cityPreviewScenePresetForPath,
  createCityPreviewSceneDraft,
  renderCityPreviewScenePresetPanel,
  type CityPreviewSceneApplyResult,
  type CityPreviewScenePreset,
  type CityPreviewScenePresetPanelHandle,
} from "./city-preview-scenes";
import {
  renderCityTrafficPreviewPanel, type CityTrafficPreviewPanelHandle,
} from "./city-traffic-preview-panel";
import { parseCitySceneConfig } from "./city-scene-config";
import { loadVerifiedCityRoadAssets } from "./city-road-assets";
import {
  createSelectedDraftControls, createSelectedSceneDraft, exportSelectedSceneDraft,
  importSelectedSceneDraft, loadSelectedSceneDraft, restoreSelectedSceneDraft,
  saveSelectedSceneDraft, storedSelectedSceneDraftMatches, type SelectedDraftControlState,
  type SelectedDraftControls, type SelectedSceneDraft,
} from "./city-selected-draft";
import { renderCitySpatialPanel, type SpatialMapData } from "./city-spatial-panel";
import {
  evaluateSpatialRoadClearance,
  spatialCenterlineFootprints,
  spatialRoadClearanceFromCanonicalBinding,
} from "./city-spatial-road-clearance";
import {
  inspectNativeReferenceVisual, loadNativeReferenceScene, loadNativeReferenceVisual,
  nativeReferenceHasVisualPresentation, type VerifiedNativeReferenceScene,
} from "./native-reference-scene";
import type { LoadedNativeCityPresentation } from "./native-city-presentation";
import type { CitySceneConfig } from "./city-scene-config";
import { loadCitySelectedScenario, type CitySelectedScenario } from "./city-selected-scenario";
import { inspectCitySelectedPlacement, type SelectedPlacementInput } from "./city-selected-placement";
import {
  loadCitySelectedLogistics, loadCitySelectedLogisticsForRepair,
  parseCitySelectedLogistics, type CitySelectedLogistics,
} from "./city-selected-logistics-draft";
import {
  createDefaultCityWorkspaceConfig, loadCityWorkspaceConfig, parseCityWorkspaceConfig,
  saveCityWorkspaceConfig, type CityWorkspaceConfig,
} from "./city-workspace-config";
import { renderCityWorkspaceOrdersPanel } from "./city-workspace-orders-panel";
import {
  renderCityWorkspaceSelectedImportPanel, type CityWorkspaceSelectedImportPanelHandle,
} from "./city-workspace-selected-import-panel";
import type { PreparedSelectedLogisticsImport } from "./city-workspace-selected-import";
import {
  normalizeBuildingPlacements, normalizeRoadbed, validateAirspacePolygon,
  validateFacilityPlacement, type BuildingPlacementSource, type RoadbedSource,
} from "./city-workspace-geometry";
import { PublicTraceMap, type MapScene, type MapView } from "./map";
import { parseMeshPack } from "./osm2world/pack";
import { LayerState } from "./state/layers";
import type { TraceTarget } from "./state/target";
import { currentLanguage, initLanguage, setLanguage, t, tf, type I18nKey, type Language } from "./i18n";

type StudioTab = "runtime" | "spatial" | "algorithm" | "events" | "compile" | "region";
const TABS: readonly StudioTab[] = ["runtime", "spatial", "algorithm", "events", "compile", "region"];

/** Translate only explicit presentation bindings; inputs, IDs and source data stay intact. */
export function translateStudioShell(root: HTMLElement): void {
  for (const [binding, attribute] of [["data-studio-i18n", "textContent"],
    ["data-studio-aria", "aria-label"], ["data-studio-title", "title"]] as const) {
    for (const node of root.querySelectorAll<HTMLElement>(`[${binding}]`)) {
      const key = node.getAttribute(binding) as I18nKey;
      const label = t(key);
      if (label === undefined) throw new Error(`Unknown Studio presentation key: ${key}`);
      if (attribute === "textContent") node.textContent = label;
      else node.setAttribute(attribute, label);
    }
  }
  for (const button of root.querySelectorAll<HTMLButtonElement>("[data-studio-language]")) {
    button.setAttribute("aria-pressed", String(button.dataset.studioLanguage === currentLanguage()));
  }
}
const BLOCKING_FLIGHT_ISSUES = new Set([
  "building_collision", "facility_collision", "airspace_incursion",
  "ground_collision", "invalid_flight_segment", "aircraft_collision",
  "static_collision", "static_overlap", "building_overlap", "road_overlap", "facility_overlap",
]);
const EMPTY_SCENE: MapScene = {
  scenario: null, pack: null, osm: null, sceneState: null,
  trajectories: [], networkFrame: null, tick: null,
};
const EMPTY_VIEW: MapView = {
  layers: new LayerState().visibility(), hiddenEntities: new Set(), hiddenTrajectories: new Set(),
  isolate: null, selected: null, hovered: null,
};

export interface SpatialSource {
  readonly sceneName: string;
  readonly data: SpatialMapData;
}

function finiteRange(values: readonly number[], label: string): { readonly min: number; readonly max: number } {
  if (values.length === 0 || values.some(value => !Number.isFinite(value))) {
    throw new Error(`${label} 缺少有限坐标`);
  }
  return { min: Math.min(...values), max: Math.max(...values) };
}

/** Derive editor context directly from the verified PublicScenario. The conservative
 * building boxes and road footprints are visual placement aids, not road-v3 or physics evidence. */
export function nativeReferenceSpatialSource(reference: VerifiedNativeReferenceScene): SpatialSource {
  const { scenario, registration } = reference;
  const extent = scenario.frame_authority.spatial_extent;
  const buildings = scenario.buildings.map(building => {
    const vertices = [...building.base_vertices, ...building.top_vertices];
    const east = finiteRange(vertices.map(vertex => vertex.enu.east_m), `建筑 ${building.building_id}`);
    const north = finiteRange(vertices.map(vertex => vertex.enu.north_m), `建筑 ${building.building_id}`);
    const up = finiteRange(vertices.map(vertex => vertex.enu.up_m), `建筑 ${building.building_id}`);
    if (east.max <= east.min || north.max <= north.min || up.max <= up.min) {
      throw new Error(`建筑 ${building.building_id} 没有可测量的三维包络`);
    }
    return { id: building.building_id, x: (east.min + east.max) / 2,
      z: -(north.min + north.max) / 2, widthM: east.max - east.min,
      depthM: north.max - north.min, heightM: up.max - up.min,
      rotationDeg: 0, baseY: up.min };
  });
  const roads = spatialCenterlineFootprints(scenario.roads.map(road => ({
    id: road.road_id,
    points: road.terrain_points.map(point => ({ x: point.enu.east_m, z: -point.enu.north_m })),
    widthM: road.width_m,
  })));
  const sourceRegions = scenario.regions.map(region => ({
    id: region.region_id,
    kind: region.kind,
    polygon: region.lower_vertices.map(point => ({ x: point.enu.east_m, z: -point.enu.north_m })),
  }));
  return {
    sceneName: `${registration.registration_id} · ${scenario.world_id}`,
    data: {
      origin: { latitude_deg: scenario.frame_authority.origin.wgs84.latitude_deg,
        longitude_deg: scenario.frame_authority.origin.wgs84.longitude_deg },
      extent: { minX: extent.min_east_m, maxX: extent.max_east_m,
        minZ: -extent.max_north_m, maxZ: -extent.min_north_m },
      buildings, roads, sourceRegions,
      roadClearance: null,
      roadClearanceError: "原生参考场景提供公共建筑、道路与区域，但未提供 road v3 和有效街道设施净空；地面设施编辑已阻止。",
    },
  };
}

function requireElement<T extends HTMLElement>(selector: string): T {
  const element = document.querySelector<T>(selector);
  if (element === null) throw new Error(`City studio element is missing: ${selector}`);
  return element;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Structured verdict for restoring a selected-scene draft against the registered catalog. */
export type RegisteredSourceVerdict =
  | { readonly kind: "match"; readonly source: AuthoringSource }
  | { readonly kind: "missing"; readonly source_id: string }
  | { readonly kind: "hash_changed"; readonly source_id: string };

/** The registered source a draft binds must still be present with its exact pinned SHA-256.
 * A non-default source is recoverable on refresh; a removed or re-hashed source is not. */
export function resolveRegisteredSource(catalog: AuthoringCatalog, sourceId: string, sourceSha: string): RegisteredSourceVerdict {
  const source = catalog.sources.find(item => item.source_id === sourceId);
  if (source === undefined) return { kind: "missing", source_id: sourceId };
  if (source.sha256 !== sourceSha) return { kind: "hash_changed", source_id: sourceId };
  return { kind: "match", source };
}

async function localJson(path: string): Promise<unknown> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} 读取失败：HTTP ${response.status}`);
  return response.json() as Promise<unknown>;
}

async function localJsonWithDigest(path: string): Promise<{ value: unknown; sha256: string }> {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path} 读取失败：HTTP ${response.status}`);
  const bytes = await response.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const sha256 = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  return {
    value: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown,
    sha256,
  };
}

/** Read the selected scene's own projection and collision sources. Render scenes
 * load their canonical road v3 and effective fixtures through the same strict
 * verifier as the map; a failed digest or source binding rejects the spatial source. */
export async function loadSpatialSource(scenePath: string): Promise<SpatialSource> {
  if (!/^\/city-presentation\/[A-Za-z0-9_-]+\.json$/.test(scenePath)) {
    throw new Error("场景路径必须是 city-presentation 下的本地 JSON 文件");
  }
  const scene = parseCitySceneConfig(await localJson(scenePath));
  if (scene.building_render !== undefined) return loadRenderSceneSpatialSource(scene);
  const resolver = new AssetResolver({ baseHref: new URL(scene.mesh_pack.base_url, window.location.href).href });
  let manifest;
  try {
    const ref = scene.mesh_pack.manifest;
    const bytes = await resolver.fetchVerifiedBytes(`assets/${ref.sha256}`, {
      sha256: ref.sha256, sizeBytes: ref.size_bytes, mediaType: "application/json",
    });
    manifest = parseMeshPack(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown);
  } finally {
    resolver.dispose();
  }
  if (scene.assets === undefined) {
    throw new Error("该场景既不是建筑渲染场景，也不含工作台放置/道路资料");
  }
  const [buildingFile, rawRoads] = await Promise.all([
    localJsonWithDigest(scene.assets.building_placement), localJson(scene.assets.road),
  ]);
  const rawBuildings = buildingFile.value;
  if (rawBuildings === null || typeof rawBuildings !== "object" || Array.isArray(rawBuildings)
      || rawRoads === null || typeof rawRoads !== "object" || Array.isArray(rawRoads)) {
    throw new Error("城市建筑或道路资料格式无效");
  }
  const buildingSource = rawBuildings as Record<string, unknown>;
  const roadSource = rawRoads as Record<string, unknown>;
  if (buildingSource.schema_version !== "aero-bench.city-building-placement/v1"
      || buildingSource.mesh_pack_manifest_sha256 !== scene.mesh_pack.manifest.sha256
      || !Array.isArray(buildingSource.placements)) {
    throw new Error("建筑放置资料与当前城市网格包不匹配");
  }
  if (roadSource.schema_version !== "aero-bench.city-road-preview/v2"
      || roadSource.mesh_pack_source_sha256 !== manifest.source.sha256
      || roadSource.building_placement_sha256 !== buildingFile.sha256
      || !Array.isArray(roadSource.roadbed)) {
    throw new Error("道路资料与当前城市网格包或建筑放置资料不匹配");
  }
  const buildings = normalizeBuildingPlacements(buildingSource.placements as BuildingPlacementSource[]);
  const roads = normalizeRoadbed(roadSource.roadbed as RoadbedSource[]);
  const buildingsByOsmId = new Map<string, (typeof buildings)[number]>();
  for (const building of buildings) {
    const osmId = building.id.slice(0, building.id.lastIndexOf(":"));
    if (!buildingsByOsmId.has(osmId)) buildingsByOsmId.set(osmId, building);
  }
  const osmLandingSites = manifest.objects.flatMap(object => {
    const tagged = object.tags.aeroway === "helipad" || object.tags.aeroway === "heliport"
      || object.tags.amenity === "heliport";
    const placed = buildingsByOsmId.get(object.id);
    return tagged && placed !== undefined ? [{
      id: object.id, name: object.tags.name ?? object.id,
      position: { x: placed.x, z: placed.z },
    }] : [];
  });
  return {
    sceneName: scene.name,
    data: {
      origin: manifest.projection.origin,
      extent: {
        minX: manifest.extent.west, maxX: manifest.extent.east,
        minZ: -manifest.extent.north, maxZ: -manifest.extent.south,
      },
      buildings, roads, roadClearance: null,
      roadClearanceError: "此旧版场景只有 road v2 机动车道，没有摘要绑定的步道、路口与有效设施净空；地面设施编辑已阻止。",
      osmLandingSites,
    },
  };
}

/** Building-render scene: load the exact render and pack manifests, then require the
 * accepted road/effective-fixture bundle before exposing placement geometry. */
async function loadRenderSceneSpatialSource(
  scene: Extract<CitySceneConfig, { building_render: NonNullable<CitySceneConfig["building_render"]> }>,
): Promise<SpatialSource> {
  const renderRef = scene.building_render;
  const renderResolver = new AssetResolver({ baseHref: new URL(renderRef.base_url, window.location.href).href });
  const packResolver = new AssetResolver({ baseHref: new URL(scene.mesh_pack.base_url, window.location.href).href });
  let manifest, packManifest;
  try {
    const packRef = scene.mesh_pack.manifest;
    const [loadedRender, packBytes] = await Promise.all([
      fetchBuildingRenderManifest(renderResolver, renderRef.manifest),
      packResolver.fetchVerifiedBytes(`assets/${packRef.sha256}`, {
        sha256: packRef.sha256, sizeBytes: packRef.size_bytes, mediaType: "application/json",
      }),
    ]);
    manifest = loadedRender;
    packManifest = parseMeshPack(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(packBytes)) as unknown);
  } finally {
    renderResolver.dispose();
    packResolver.dispose();
  }
  const baseData = {
    origin: manifest.scene.origin_wgs84,
    extent: {
      minX: Math.min(...manifest.blocks.map(block => block.min_e)),
      maxX: Math.max(...manifest.blocks.map(block => block.max_e)),
      minZ: -Math.max(...manifest.blocks.map(block => block.max_n)),
      maxZ: -Math.min(...manifest.blocks.map(block => block.min_n)),
    },
    buildings: normalizeBuildingPlacements(manifest.buildings.map(buildingPlacement)),
  };
  if (scene.road_assets === undefined) {
    return { sceneName: scene.name, data: { ...baseData, roads: [], roadClearance: null,
      roadClearanceError: "当前建筑渲染场景未声明摘要绑定的 road v3 与有效设施，地面设施编辑已阻止。" } };
  }
  const verified = await loadVerifiedCityRoadAssets(scene.road_assets,
    { manifest: packManifest, manifestSha256: scene.mesh_pack.manifest.sha256 },
    manifest, renderRef.manifest.sha256);
  try {
    const clearance = spatialRoadClearanceFromCanonicalBinding(verified.vegetationRoadBinding, {
      source: "canonical-road-v3",
      roadSha256: scene.road_assets.road.sha256,
      fixtureIdentity: scene.road_assets.effective_fixtures.sha256,
      displayedSurfaceSha256: verified.displayedSurfaceSha256,
    });
    return { sceneName: scene.name, data: { ...baseData, roads: clearance.roadbed, roadClearance: clearance } };
  } finally {
    verified.dispose();
  }
}

/** Geometry is checked again for whole-file imports and before persistence. */
export function validateStudioGeometry(config: CityWorkspaceConfig, data: SpatialMapData): string[] {
  const issues: string[] = [];
  const { minX, maxX, minZ, maxZ } = data.extent;
  for (const region of config.airspace) {
    for (const issue of validateAirspacePolygon(region)) issues.push(`禁飞区 ${region.name}：${issue.message}`);
  }
  if (issues.length) return issues;
  for (const facility of config.facilities) {
    const { x, z } = facility.position;
    if (x < minX || x > maxX || z < minZ || z > maxZ) {
      issues.push(`设施 ${facility.name}：坐标超出当前城市地图范围`);
      continue;
    }
    const others = config.facilities.filter(item => item.id !== facility.id);
    for (const issue of validateFacilityPlacement(facility, data.buildings, [], others, config.airspace)) {
      issues.push(`设施 ${facility.name}：${issue.message}`);
    }
    for (const issue of evaluateSpatialRoadClearance(facility, data.roadClearance)) {
      if (issue.severity === "block") issues.push(`设施 ${facility.name}：${issue.message}`);
    }
  }
  return [...new Set(issues)];
}

function selectedTab(): StudioTab {
  const tab = new URLSearchParams(window.location.search).get("tab");
  return TABS.find(value => value === tab) ?? "runtime";
}

/** Saved scenePath is a source selector, not a fetch URL for a native registration. */
export function savedNativeRegistration(
  scenePath: string, registrations: readonly NativeSceneRegistration[],
): NativeSceneRegistration | null {
  const matches = registrations.filter(item => item.scene_path === scenePath);
  if (matches.length > 1) throw new Error(`Multiple native registrations select ${scenePath}`);
  return matches[0] ?? null;
}

/** A compiled result is input for a run, not a run: Control loads the immutable compilation and
 * the viewer's run console starts it. Nothing here starts, polls or verifies a run. */
export function renderCompiledHandoff(root: HTMLElement, selection: CityCompiledSelection): void {
  const card = document.createElement("section");
  card.className = "studio-card";
  card.dataset.role = "compiled-handoff";
  const title = document.createElement("h3");
  title.textContent = t("compile.handoffTitle");
  const detail = document.createElement("p");
  detail.className = "studio-note";
  detail.textContent = `${t("compile.savedIdentity")}: ${selection.draftSha256} · `
    + tf("compile.handoffDetail", {
      compilation: selection.compilationId, registration: selection.registrationId,
      runs: selection.runIds.join(", "),
    });
  const link = document.createElement("a");
  link.className = "studio-button";
  link.href = "/";
  link.textContent = t("compile.handoffLink");
  card.append(title, detail, link);
  root.replaceChildren(card);
}

class CityStudio {
  private readonly root = requireElement<HTMLElement>("#city-studio");
  private readonly mapRoot = requireElement<HTMLElement>("#studio-map");
  private readonly panelRoot = requireElement<HTMLElement>("#studio-panel");
  private readonly sidebarTitle = requireElement<HTMLElement>("#studio-sidebar-title");
  private readonly sidebarSubtitle = requireElement<HTMLElement>("#studio-sidebar-subtitle");
  private readonly nameInput = requireElement<HTMLInputElement>("#studio-name");
  private readonly saveStatus = requireElement<HTMLElement>("#studio-save-status");
  private readonly previewStatus = requireElement<HTMLElement>("#studio-preview-state");
  private readonly previewMetrics = requireElement<HTMLElement>("#studio-preview-metrics");
  private readonly issuesRoot = requireElement<HTMLElement>("#studio-issues");
  private readonly followSelect = requireElement<HTMLSelectElement>("#studio-follow");
  private readonly playButton = requireElement<HTMLButtonElement>("#studio-play");
  private readonly timeInput = requireElement<HTMLInputElement>("#studio-time");
  private readonly clockOutput = requireElement<HTMLOutputElement>("#studio-clock");
  private readonly previewKind = requireElement<HTMLElement>("#studio-preview-kind");
  private readonly mapPlace = requireElement<HTMLElement>("#studio-map-place");
  private readonly mapMode = requireElement<HTMLElement>("#studio-map-mode");
  private readonly previewFootPrimary = requireElement<HTMLElement>("#studio-preview-foot-primary");
  private readonly previewFootSecondary = requireElement<HTMLElement>("#studio-preview-foot-secondary");
  private config: CityWorkspaceConfig;
  private compilePanel: CityCompilePanelHandle | null = null;
  private eventTimeline: CityEventTimelineHandle | null = null;
  private trafficPreviewPanel: CityTrafficPreviewPanelHandle | null = null;
  private scenePresetPanel: CityPreviewScenePresetPanelHandle | null = null;
  private selectedImportPanel: CityWorkspaceSelectedImportPanelHandle | null = null;
  private nativeReference: VerifiedNativeReferenceScene | null = null;
  private nativeReferencePresentation: LoadedNativeCityPresentation | null = null;
  private nativeReferenceAbort: AbortController | null = null;
  private nativeReferenceRevision = 0;
  private spatial: SpatialSource | null = null;
  private map: PublicTraceMap | null = null;
  private mapReady = false;
  private mapEpoch = 0;
  private applyRevision = 0;
  private appliedRevision = 0;
  private applyDue = false;
  private applyTimer: number | null = null;
  private applying = false;
  private previewApplied = false;
  private pendingImport: { fileName: string; config: CityWorkspaceConfig } | null = null;
  private importRevision = 0;
  private tab = selectedTab();
  private draftError: string | null = null;
  private previewError: string | null = null;
  private operationError: string | null = null;
  private previewIssues: readonly { path: string; code: string; message: string }[] = [];
  private basemapNote = "";
  private initialStorageError: string | null = null;
  private authoringMode = false;
  private authoringCatalog: AuthoringCatalog | null = null;
  private authoringSource: { readonly catalog: AuthoringSource; readonly bytes: Uint8Array<ArrayBuffer> } | null = null;
  private authoringSourceLoading = false;
  private authoringRequestedSourceId: string | null = null;
  private authoringSwitchRevision = 0;
  private regionSelectorLoading = false;
  private authoringSourceError: string | null = null;
  private regionSelector: CityRegionSelector | null = null;
  private authoringSelection: SceneSelection | null = null;
  private authoringJob: AuthoringJob | null = null;
  private authoringError: string | null = null;
  private authoringStage = "读取注册 OSM 来源";
  private authoringBusy = false;
  private authoringRevision = 0;
  private authoringMapKey = "selection-0";
  private authoringAbort: AbortController | null = null;
  private legacySaveStatus: { readonly text: string; readonly state: string | undefined } | null = null;
  private selectedDraft: SelectedSceneDraft | null = null;
  private selectedDraftSceneReady = false;
  private selectedDraftBusy = false;
  private selectedDraftError: string | null = null;
  private selectedDraftPendingImport: string | null = null;
  private selectedRestoreAttempted = false;
  private selectedDraftControls: SelectedDraftControls | null = null;
  private selectedPresentation: VerifiedStaticPresentation | null = null;
  private selectedScenario: CitySelectedScenario | null = null;
  private selectedScenarioError: string | null = null;
  private selectedScenarioRevision = 0;
  private selectedLogistics: CitySelectedLogistics | null = null;
  private selectedLogisticsError: string | null = null;
  private selectedLogisticsRevision = 0;

  constructor() {
    translateStudioShell(this.root);
    try {
      const loaded = loadCityWorkspaceConfig();
      this.config = loaded ?? createDefaultCityWorkspaceConfig();
      this.saveStatus.textContent = loaded === null ? t("studio.newDraft") : t("studio.loaded");
      this.saveStatus.dataset.state = loaded === null ? "dirty" : "saved";
    } catch (error) {
      this.initialStorageError = `本地草稿读取失败：${errorMessage(error)}。当前显示默认草稿，原存储未改动。`;
      this.config = createDefaultCityWorkspaceConfig();
      this.saveStatus.textContent = t("studio.storageError");
      this.saveStatus.dataset.state = "error";
    }
    this.nameInput.value = this.config.name;
    this.bindControls();
    if (new URL(window.location.href).searchParams.get("mode") === "selected") {
      this.enterAuthoringMode();
    }
    this.renderTab();
    this.renderSummary();
    if (this.authoringMode) this.startMap();
    else void this.loadInitialSpatial();
    const playbackTimer = window.setInterval(() => this.syncPlayback(), 180);
    window.addEventListener("beforeunload", () => {
      window.clearInterval(playbackTimer); this.authoringAbort?.abort(); this.nativeReferenceAbort?.abort();
      this.nativeReferencePresentation?.dispose(); this.nativeReferencePresentation = null;
      this.regionSelector?.destroy();
      this.compilePanel?.dispose(); this.eventTimeline?.dispose();
      this.trafficPreviewPanel?.dispose(); this.scenePresetPanel?.dispose();
      this.selectedImportPanel?.dispose();
    }, { once: true });
  }

  private bindControls(): void {
    for (const button of this.root.querySelectorAll<HTMLButtonElement>("[data-studio-language]")) {
      button.addEventListener("click", () => {
        setLanguage(button.dataset.studioLanguage as Language);
        translateStudioShell(this.root);
        document.documentElement.lang = currentLanguage();
        if (this.saveStatus.dataset.state === "saved") this.saveStatus.textContent = t("studio.loaded");
        if (this.saveStatus.dataset.state === "dirty") this.saveStatus.textContent = t("studio.dirty");
        // Re-render presentation from this same in-memory draft, without a reload or save.
        this.renderTab();
        this.renderSummary();
      });
    }
    this.nameInput.addEventListener("change", () => {
      this.changeDraft({ ...this.config, name: this.nameInput.value.trim() });
    });
    requireElement<HTMLButtonElement>("#studio-save").addEventListener("click", () => this.save());
    requireElement<HTMLButtonElement>("#studio-export").addEventListener("click", () => this.export());
    requireElement<HTMLInputElement>("#studio-import").addEventListener("change", event => {
      const input = event.currentTarget as HTMLInputElement;
      const file = input.files?.[0];
      if (file !== undefined) void this.import(file);
      input.value = "";
    });
    requireElement<HTMLElement>("#studio-tabs").addEventListener("click", event => {
      const link = (event.target as Element).closest<HTMLAnchorElement>("a[data-tab]");
      if (link === null) return;
      event.preventDefault();
      this.activateTab(link.dataset.tab as StudioTab, true);
    });
    window.addEventListener("popstate", () => this.activateTab(selectedTab(), false));
    const collapse = requireElement<HTMLButtonElement>("#studio-collapse");
    const open = requireElement<HTMLButtonElement>("#studio-open-sidebar");
    collapse.addEventListener("click", () => this.setCollapsed(true));
    open.addEventListener("click", () => this.setCollapsed(false));
    this.followSelect.addEventListener("change", () => {
      try { this.map?.followWorkspaceEntity(this.followSelect.value || null); }
      catch (error) { this.previewError = `跟随视角失败：${errorMessage(error)}`; this.renderSummary(); }
    });
    this.playButton.addEventListener("click", () => {
      try {
        this.map?.setWorkspacePlaying(this.mapRoot.dataset.previewPlaying !== "true");
        this.syncPlayback();
      } catch (error) {
        this.previewError = `播放失败：${errorMessage(error)}`;
        this.renderSummary();
      }
    });
    this.timeInput.addEventListener("input", () => {
      try {
        this.map?.setWorkspaceTime(Number(this.timeInput.value));
        this.syncPlayback();
      } catch (error) {
        this.previewError = `时间跳转失败：${errorMessage(error)}`;
        this.renderSummary();
      }
    });
    window.addEventListener("beforeunload", () => this.map?.destroy(), { once: true });
  }

  private syncPlayback(): void {
    const source = this.mapRoot.querySelector<HTMLInputElement>(".city-preview-controls input[type=range]");
    const seconds = source === null ? Number(this.mapRoot.dataset.previewSecond ?? "0") : Number(source.value);
    if (!Number.isFinite(seconds)) return;
    this.eventTimeline?.update(this.config.events, Math.max(0, seconds));
    if (!this.previewApplied) return;
    const maxAuthoringTime = Math.max(120,
      ...this.config.events.map(item => item.atS),
      ...this.config.stateKeyframes.map(item => item.atS));
    this.timeInput.max = String(Math.max(maxAuthoringTime, Number(source?.max ?? 120)));
    if (document.activeElement !== this.timeInput) this.timeInput.value = String(seconds);
    const whole = Math.floor(seconds);
    this.clockOutput.value = `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
    const playing = this.mapRoot.dataset.previewPlaying === "true";
    this.playButton.textContent = playing ? t("control.pause") : t("studio.play");
    this.playButton.setAttribute("aria-label", `${playing ? t("control.pause") : t("studio.play")} · ${t("studio.preview")}`);
  }

  private setCollapsed(collapsed: boolean): void {
    this.root.classList.toggle("studio-sidebar-collapsed", collapsed);
    requireElement<HTMLButtonElement>("#studio-collapse").setAttribute("aria-expanded", String(!collapsed));
    const open = requireElement<HTMLButtonElement>("#studio-open-sidebar");
    open.hidden = !collapsed;
    open.setAttribute("aria-expanded", String(!collapsed));
  }

  private activateTab(tab: StudioTab, push: boolean): void {
    if (!TABS.includes(tab)) return;
    if (push) {
      const url = new URL(window.location.href);
      url.searchParams.set("tab", tab);
      if (this.authoringMode) url.searchParams.set("mode", "selected");
      else url.searchParams.delete("mode");
      window.history.pushState(null, "", url);
    }
    if (tab === this.tab && this.panelRoot.childElementCount > 0) return;
    this.tab = tab;
    this.renderTab();
    this.renderSummary();
  }

  private renderTab(): void {
    if (this.authoringMode) {
      const heading: Record<StudioTab, string> = {
        runtime: "机队与背景需求", spatial: "设施与空域", algorithm: "调度算法",
        events: "事件与标签", compile: "编译与运行", region: "区域选取",
      };
      this.sidebarTitle.textContent = heading[this.tab];
      this.sidebarSubtitle.textContent = "已保存的选区场景与物流配置只读；仅允许显式导入工作区可表示的物流字段。";
    }
    for (const link of document.querySelectorAll<HTMLAnchorElement>("#studio-tabs a[data-tab]")) {
      const active = link.dataset.tab === this.tab;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    this.compilePanel?.dispose();
    this.compilePanel = null;
    this.eventTimeline?.dispose();
    this.eventTimeline = null;
    this.trafficPreviewPanel?.dispose();
    this.trafficPreviewPanel = null;
    this.scenePresetPanel?.dispose();
    this.scenePresetPanel = null;
    this.selectedImportPanel?.dispose();
    this.selectedImportPanel = null;
    this.panelRoot.replaceChildren();
    this.selectedDraftControls = null;
    if (this.tab === "region") {
      this.enterAuthoringMode();
      this.renderRegionTab();
    } else if (this.authoringMode) {
      this.renderSelectedConfigTab();
    } else if (this.tab === "spatial") {
      const editor = document.createElement("div");
      const landscape = document.createElement("div");
      this.panelRoot.append(editor, landscape);
      if (this.spatial === null) {
        const loading = document.createElement("p");
        loading.className = "studio-panel-loading";
        loading.textContent = this.draftError?.startsWith("场景几何资料加载失败")
          ? this.draftError : "正在读取场景网格、建筑和道路资料…";
        editor.append(loading);
      } else renderCitySpatialPanel(editor, this.config, next => this.changeDraft(next), this.spatial.data);
      if (this.nativeReference === null) {
        renderCityLandscapePanel(landscape, this.config.authoredLandscape,
          items => this.changeDraft({ ...this.config, authoredLandscape: [...items] }),
          this.map?.terrainCompletionAvailable
            ? { propose: kind => this.map!.proposeTerrainCompletion(kind) }
            : null);
      } else {
        const note = document.createElement("p");
        note.className = "studio-note";
        note.dataset.role = "native-landscape-unavailable";
        note.textContent = "原生参考场景未绑定默认城市的已验证道路与环境几何，不能预览或编辑创作景观；未自动切换场景。";
        landscape.append(note);
      }
    } else if (this.tab === "runtime") {
      const scenePreset = document.createElement("div");
      const editor = document.createElement("div");
      const traffic = document.createElement("div");
      this.panelRoot.append(scenePreset, editor, traffic);
      this.scenePresetPanel = renderCityPreviewScenePresetPanel(scenePreset, this.config.scenePath,
        preset => this.applyScenePreset(preset));
      renderCityRuntimePanel(editor, this.config, next => this.changeDraft(next));
      if (this.nativeReference === null) {
        this.trafficPreviewPanel = renderCityTrafficPreviewPanel(traffic, () => this.config,
          async artifact => {
            const map = this.map;
            if (this.authoringMode || this.nativeReference !== null || !this.mapReady || map === null) {
              throw new Error("当前不是已验证默认城市，不能装配审计交通预览");
            }
            await map.applyAuditedTrafficPreview(artifact, () => this.config);
            this.previewStatus.textContent = "完整审计交通预览已载入";
            this.previewStatus.dataset.state = "ready";
            this.renderSummary();
          });
      } else {
        const note = document.createElement("p");
        note.className = "studio-note";
        note.textContent = t("studio.nativeNoTraffic");
        traffic.append(note);
      }
    } else if (this.tab === "algorithm") {
      const algorithms = document.createElement("div");
      const orders = document.createElement("div");
      this.panelRoot.append(algorithms, orders);
      renderCityAlgorithmPanel(algorithms, this.config, next => this.changeDraft(next));
      renderCityWorkspaceOrdersPanel(orders, this.config,
        fields => this.changeDraft({ ...this.config, ...fields }));
    } else if (this.tab === "compile") {
      const host = document.createElement("div");
      const handoff = document.createElement("div");
      this.panelRoot.append(host, handoff);
      this.compilePanel = renderCityCompilePanel(host, () => this.config,
        selection => {
          renderCompiledHandoff(handoff, selection);
          this.previewStatus.textContent = t("compile.handoffTitle");
          this.previewStatus.dataset.state = "ready";
        },
        registration => this.selectNativeReference(registration));
    } else {
      const editor = document.createElement("div");
      const timeline = document.createElement("div");
      this.panelRoot.append(editor, timeline);
      renderCityEventPanel(editor, this.config, next => this.changeDraft(next));
      const seconds = Number(this.mapRoot.dataset.previewSecond ?? "0");
      this.eventTimeline = renderCityEventTimeline(timeline, this.config.events,
        Number.isFinite(seconds) ? Math.max(0, seconds) : 0);
    }
  }

  /** An explicit catalog action replaces the editable draft only after the public
   * scenario and every declared presentation asset have passed their pin checks. */
  private async selectNativeReference(registration: NativeSceneRegistration): Promise<string | null> {
    const revision = ++this.nativeReferenceRevision;
    this.nativeReferenceAbort?.abort();
    const controller = new AbortController();
    this.nativeReferenceAbort = controller;
    try {
      const reference = await loadNativeReferenceScene(registration, controller.signal);
      const spatial = nativeReferenceSpatialSource(reference);
      const visual = await loadNativeReferenceVisual(reference, { signal: controller.signal });
      const map = this.map;
      if (revision !== this.nativeReferenceRevision || controller.signal.aborted
          || this.authoringMode || map === null) {
        visual.dispose();
        throw new Error("参考场景选择已被较新的操作替代");
      }
      const previousPresentation = this.nativeReferencePresentation;
      try {
        map.render({ ...EMPTY_SCENE, scenario: reference.scenario, pack: visual.pack,
          nativePresentation: visual.nativePresentation }, EMPTY_VIEW);
      } catch (error) {
        visual.dispose();
        throw error;
      }
      if (this.applyTimer !== null) window.clearTimeout(this.applyTimer);
      this.applyTimer = null;
      this.applyDue = false;
      this.applyRevision++;
      this.appliedRevision = this.applyRevision;
      this.config = reference.draft;
      this.nativeReference = reference;
      this.nativeReferencePresentation = visual.nativePresentation;
      previousPresentation?.dispose();
      this.spatial = spatial;
      this.pendingImport = null;
      this.previewIssues = [];
      this.previewApplied = false;
      this.previewError = null;
      this.operationError = null;
      this.playButton.disabled = true;
      this.timeInput.disabled = true;
      this.renderFollowChoices();
      this.mapReady = visual.kind === "native-city";
      this.basemapNote = visual.kind === "native-city"
        ? t("studio.nativeAssets")
        : visual.kind === "unavailable" ? "已注册 PublicScenario 未发布三维网格" : "";
      this.draftError = this.validateDraft();
      this.nameInput.value = this.config.name;
      this.mapPlace.textContent = t("studio.nativeSource");
      this.mapMode.textContent = t("studio.referenceMode");
      this.saveStatus.textContent = "参考草稿已载入，尚未保存";
      this.saveStatus.dataset.state = this.draftError === null ? "dirty" : "error";
      if (visual.kind === "unavailable") {
        // A registered PublicScenario remains valid compiler input without a complete
        // visual declaration. Never replace it with another city's geometry.
        this.previewStatus.textContent = "原生参考已校验；未发布三维网格";
        this.previewStatus.dataset.state = "warning";
      } else if (visual.kind === "native-city") {
        this.previewStatus.textContent = t("studio.nativeReady");
        this.previewStatus.dataset.state = "ready";
      } else {
        this.previewStatus.textContent = "正在装配已验证原生场景";
        this.previewStatus.dataset.state = "loading";
      }
      this.renderSummary();
      return `已显式载入 ${registration.registration_id} 的参考草稿和公共场景；`
        + (visual.kind === "unavailable" ? "该注册未发布三维网格；"
          : visual.kind === "native-city" ? "完整原生城市呈现已验证；" : "官方三维网格已验证；")
        + "未自动保存，也未启动运行。";
    } finally {
      if (this.nativeReferenceAbort === controller) this.nativeReferenceAbort = null;
    }
  }

  private renderSelectedConfigTab(): void {
    if (!this.authoringMode || this.tab === "region") return;
    this.selectedImportPanel?.dispose();
    this.selectedImportPanel = renderCityWorkspaceSelectedImportPanel(this.panelRoot, {
      target: this.config,
      scenario: this.selectedScenario,
      logistics: this.selectedLogistics,
      scenarioError: this.selectedScenarioError,
      logisticsError: this.selectedLogisticsError,
    }, prepared => this.applySelectedLogisticsImport(prepared));
  }

  private applySelectedLogisticsImport(prepared: PreparedSelectedLogisticsImport): string {
    const next = parseCityWorkspaceConfig({ ...this.config, ...prepared.patch });
    const source = prepared.source.jobId.slice(0, 12);
    this.leaveAuthoringMode();
    this.changeDraft(next);
    this.activateTab("algorithm", true);
    this.saveStatus.textContent = `已从只读选区 ${source} 导入可表示的物流字段；来源未修改，当前工作区尚未保存`;
    this.saveStatus.dataset.state = "dirty";
    this.operationError = null;
    this.renderSummary();
    return `已导入工作区；选区 ${source} 保持只读且未修改。`;
  }

  private clearSelectedScenarioState(): void {
    this.selectedScenarioRevision++;
    this.selectedPresentation = null;
    this.selectedScenario = null;
    this.selectedScenarioError = null;
    this.selectedLogisticsRevision++;
    this.selectedLogistics = null;
    this.selectedLogisticsError = null;
  }

  private selectedPlacementInput(): SelectedPlacementInput {
    const presentation = this.selectedPresentation;
    const job = this.authoringJob;
    if (presentation === null || job?.presentation === null || job?.presentation === undefined
        || this.map === null || !this.selectedDraftSceneReady) {
      throw new Error("当前选区的已验证城市呈现尚未就绪");
    }
    return { manifest: presentation.manifest, buildingPlacement: presentation.buildingPlacement,
      road: presentation.road, signals: presentation.signals,
      presentationManifestSha256: job.presentation.manifest.sha256,
      staticObstacles: this.map.selectedSceneStaticObstacles(this.authoringMapKey),
      rooftopMesh: this.map.selectedSceneRooftopMesh(this.authoringMapKey) };
  }

  private async activateSelectedScenario(scenario: CitySelectedScenario): Promise<void> {
    const revision = ++this.selectedScenarioRevision;
    const draft = this.selectedDraft;
    if (draft === null || scenario.selectedScene.job_id !== draft.job_id
        || scenario.selectedScene.presentation_manifest_sha256 !== draft.presentation_manifest_sha256) {
      throw new Error("设施配置绑定了其他选区，拒绝接入当前城市");
    }
    const placement = this.selectedPlacementInput();
    const inspection = await inspectCitySelectedPlacement(scenario, placement);
    if (revision !== this.selectedScenarioRevision) throw new Error("当前选区已变化，操作未保存");
    if (inspection.geometry === null || inspection.issues.length > 0) {
      throw new Error(inspection.issues.map(issue => `${issue.path}：${issue.message}`).join("；")
        || "设施配置缺少当前选区的碰撞资料");
    }
    await this.map!.applySelectedSceneFacilities(this.authoringMapKey, scenario.facilities);
    if (revision !== this.selectedScenarioRevision) throw new Error("当前选区已变化，操作未保存");
    this.selectedScenario = scenario;
    this.selectedScenarioError = null;
    if (this.selectedLogistics !== null) {
      try {
        this.selectedLogistics = parseCitySelectedLogistics(this.selectedLogistics, scenario);
        this.selectedLogisticsError = null;
      } catch (error) {
        this.selectedLogisticsError = `设施或机队变更后，原物流配置需要修正：${errorMessage(error)}。原文件仍保留在本地。`;
      }
    } else if (this.selectedLogisticsError === null) {
      void this.initializeSelectedLogisticsForScenario(scenario);
    }
    this.renderSelectedConfigTab();
    this.renderSummary();
  }

  private async initializeSelectedScenarioForReadyScene(): Promise<void> {
    const revision = ++this.selectedScenarioRevision;
    const draft = this.selectedDraft;
    if (draft === null) return;
    try {
      const stored = await loadCitySelectedScenario();
      if (revision !== this.selectedScenarioRevision) return;
      if (stored !== null && (stored.selectedScene.job_id !== draft.job_id
          || stored.selectedScene.presentation_manifest_sha256 !== draft.presentation_manifest_sha256)) {
        this.selectedScenarioError = "本地场景配置绑定了其他选区；只读来源不会重绑或覆盖该文件。";
        this.selectedScenario = null;
        this.renderSelectedConfigTab();
        this.renderSummary();
        return;
      }
      if (stored === null) {
        this.selectedScenario = null;
        this.selectedScenarioError = "当前选区没有已保存的场景配置；只读来源不会自动新建配置。";
        this.renderSelectedConfigTab();
        this.renderSummary();
        return;
      }
      await this.activateSelectedScenario(stored);
    } catch (error) {
      if (this.selectedDraft !== draft || !this.selectedDraftSceneReady) return;
      this.selectedScenario = null;
      this.selectedScenarioError = `设施配置无法加载：${errorMessage(error)}`;
      this.renderSelectedConfigTab();
      this.renderSummary();
    }
  }

  private async initializeSelectedLogisticsForScenario(scenario: CitySelectedScenario): Promise<void> {
    const revision = ++this.selectedLogisticsRevision;
    try {
      const stored = await loadCitySelectedLogistics(scenario);
      if (revision !== this.selectedLogisticsRevision || this.selectedScenario !== scenario) return;
      this.selectedLogistics = stored;
      this.selectedLogisticsError = stored === null
        ? "当前选区没有已保存的物流配置；只读来源不会自动新建配置。" : null;
    } catch (error) {
      if (revision !== this.selectedLogisticsRevision || this.selectedScenario !== scenario) return;
      try {
        this.selectedLogistics = await loadCitySelectedLogisticsForRepair(scenario);
        if (revision !== this.selectedLogisticsRevision || this.selectedScenario !== scenario) return;
        this.selectedLogisticsError = `物流配置需要修正：${errorMessage(error)}。原文件仍保留在本地。`;
      } catch (repairError) {
        if (revision !== this.selectedLogisticsRevision || this.selectedScenario !== scenario) return;
        this.selectedLogistics = null;
        this.selectedLogisticsError = `物流配置无法加载：${errorMessage(repairError)}`;
      }
    }
    if (this.authoringMode && this.tab !== "region") this.renderSelectedConfigTab();
    this.renderSummary();
  }

  private authoringScene(): MapScene {
    return { ...EMPTY_SCENE, authoring: { key: this.authoringMapKey, presentation: null } };
  }

  private enterAuthoringMode(): void {
    if (this.authoringMode) return;
    this.nativeReferenceAbort?.abort(); this.nativeReferenceAbort = null;
    this.nativeReferenceRevision++;
    this.authoringMode = true;
    this.operationError = null;
    this.legacySaveStatus = { text: this.saveStatus.textContent ?? "", state: this.saveStatus.dataset.state };
    this.saveStatus.textContent = "选区草稿是只读导入来源";
    this.saveStatus.dataset.state = "dirty";
    this.root.classList.add("studio-authoring-mode");
    this.sidebarTitle.textContent = "区域选取";
    this.sidebarSubtitle.textContent = "真实 OSM 选区用于生成或恢复来源；已保存的场景与物流配置不会在此处修改。";
    this.previewKind.textContent = "／ 内部静态预览，尚未运行";
    this.mapPlace.textContent = "注册 OSM 来源";
    this.mapMode.textContent = "静态作者场景";
    this.previewFootPrimary.textContent = "本选区完整城市呈现需要真实 SUMO 道路拓扑、BigCity 建筑放置与可核验静态素材；没有车辆、行人、无人机或订单运行。";
    this.previewFootSecondary.textContent = "内部阶段性预览；设施按已验证建筑、道路与街道设施碰撞盒校验。机队参数仅为配置，正式执行与最终视觉尚未验收。";
    this.nameInput.disabled = true;
    requireElement<HTMLInputElement>("#studio-import").disabled = true;
    this.playButton.disabled = true; this.timeInput.disabled = true;
    this.mapReady = false; this.previewApplied = false;
    this.selectedDraftSceneReady = false;
    this.map?.render(this.authoringScene(), EMPTY_VIEW);
    this.nativeReferencePresentation?.dispose();
    this.nativeReferencePresentation = null;
    this.renderSummary();
    if (this.selectedDraft !== null) {
      // The viewer was torn down since this identity verified; re-fetch and re-verify the same job.
      void this.restoreSelectedDraft(this.selectedDraft, null);
    } else if (!this.selectedRestoreAttempted) {
      this.selectedRestoreAttempted = true;
      void this.restoreStoredSelectedDraft();
    }
  }

  private leaveAuthoringMode(): void {
    this.clearSelectedScenarioState();
    this.operationError = null;
    this.authoringAbort?.abort(); this.authoringAbort = null;
    this.authoringRevision++; this.authoringBusy = false;
    this.selectedDraftBusy = false;
    this.selectedDraftSceneReady = false;
    this.selectedDraftPendingImport = null;
    this.authoringMode = false;
    if (this.legacySaveStatus !== null) {
      this.saveStatus.textContent = this.legacySaveStatus.text;
      if (this.legacySaveStatus.state === undefined) delete this.saveStatus.dataset.state;
      else this.saveStatus.dataset.state = this.legacySaveStatus.state;
      this.legacySaveStatus = null;
    }
    this.root.classList.remove("studio-authoring-mode");
    this.sidebarTitle.textContent = "场景配置";
    this.sidebarSubtitle.textContent = "按模块编辑，同一份草稿随时可导出。";
    this.syncSceneBackdropLabels();
    this.mapPlace.textContent = cityPreviewScenePresetForPath(this.config.scenePath)?.placeLabel
      ?? this.spatial?.sceneName ?? t("studio.ordinaryPreview");
    this.mapMode.textContent = t("studio.mode");
    this.previewFootSecondary.textContent = t("studio.footer");
    this.nameInput.disabled = false;
    requireElement<HTMLInputElement>("#studio-import").disabled = false;
    this.activateTab("runtime", true);
    this.startMap();
    if (this.nativeReference === null) void this.loadInitialSpatial();
  }

  private renderRegionTab(): void {
    const wrapper = document.createElement("section"); wrapper.className = "studio-authoring-panel";
    const heading = document.createElement("div"); heading.className = "studio-card";
    const title = document.createElement("h2"); title.textContent = "OSM 区域选取";
    const help = document.createElement("p"); help.className = "studio-note";
    help.textContent = "从注册 OSM 框选并生成建筑、道路、步道与路灯的完整静态呈现，尚未仿真运行。选区草稿与旧草稿完全分开：浏览器装配完成后可保存或导出，刷新后按同一任务恢复，不会静默改用其他城市。";
    const sourceInfo = document.createElement("div"); sourceInfo.className = "studio-authoring-source"; sourceInfo.id = "studio-authoring-source";
    const sourceControl = this.renderAuthoringSourceControl();
    heading.append(title, help, sourceControl, sourceInfo);
    const mapHost = document.createElement("div"); mapHost.id = "studio-region-map";
    const control = document.createElement("div"); control.className = "studio-card";
    const selected = document.createElement("div"); selected.className = "studio-authoring-selection"; selected.id = "studio-authoring-selection";
    const job = document.createElement("div"); job.className = "studio-authoring-job"; job.id = "studio-authoring-job";
    const issue = document.createElement("div"); issue.className = "studio-authoring-error"; issue.id = "studio-authoring-error"; issue.setAttribute("role", "alert");
    const actions = document.createElement("div"); actions.className = "studio-authoring-actions";
    const build = document.createElement("button"); build.type = "button"; build.className = "studio-button studio-button-primary"; build.id = "studio-authoring-build"; build.textContent = "生成城市静态呈现";
    build.addEventListener("click", () => void this.buildAuthoringSelection());
    const retry = document.createElement("button"); retry.type = "button"; retry.className = "studio-button"; retry.textContent = "重读 OSM 来源";
    retry.addEventListener("click", () => {
      this.clearSelectedScenarioState();
      this.authoringAbort?.abort(); this.authoringAbort = null;
      this.authoringRevision++; this.authoringBusy = false;
      this.selectedDraftBusy = false; this.selectedDraftSceneReady = false;
      this.selectedDraftPendingImport = null; this.selectedDraft = null; this.selectedDraftError = null;
      this.authoringSource = null; this.authoringSourceError = null;
      this.authoringCatalog = null; this.authoringRequestedSourceId = null;
      this.authoringSwitchRevision++; this.authoringSourceLoading = false;
      this.regionSelector?.destroy(); this.regionSelector = null; this.regionSelectorLoading = false;
      this.authoringSelection = null; this.authoringJob = null; this.authoringError = null;
      this.authoringStage = "读取注册 OSM 来源";
      this.authoringMapKey = `selection-${this.authoringRevision}`;
      this.map?.render(this.authoringScene(), EMPTY_VIEW);
      this.renderRegionTab();
    });
    const legacy = document.createElement("button"); legacy.type = "button"; legacy.className = "studio-button"; legacy.textContent = "返回已有城市场景";
    legacy.addEventListener("click", () => this.leaveAuthoringMode());
    actions.append(build, retry, legacy);
    const draft = createSelectedDraftControls({
      onSave: () => void this.saveSelectedDraft(),
      onExport: () => this.exportSelectedDraft(),
      onImport: file => void this.importSelectedDraft(file),
    });
    this.selectedDraftControls = draft;
    control.append(selected, job, issue, actions, draft.root);
    wrapper.append(heading, mapHost, control); this.panelRoot.replaceChildren(wrapper);
    if (this.regionSelector !== null) mapHost.append(this.regionSelector.element);
    else if (this.authoringSource !== null && this.authoringSourceError === null && !this.regionSelectorLoading) void this.mountRegionSelector(mapHost);
    else if (this.authoringCatalog === null && !this.authoringSourceLoading && this.authoringSourceError === null) void this.loadAuthoringSourceCatalog();
    this.updateRegionDetails();
  }

  private updateRegionDetails(): void {
    const info = this.panelRoot.querySelector<HTMLElement>("#studio-authoring-source");
    const selected = this.panelRoot.querySelector<HTMLElement>("#studio-authoring-selection");
    const job = this.panelRoot.querySelector<HTMLElement>("#studio-authoring-job");
    const issue = this.panelRoot.querySelector<HTMLElement>("#studio-authoring-error");
    const build = this.panelRoot.querySelector<HTMLButtonElement>("#studio-authoring-build");
    if (info !== null) info.textContent = this.authoringSource === null
      ? this.authoringSourceLoading ? "正在读取注册目录与原始 OSM 字节…" : (this.authoringCatalog === null ? "OSM 来源尚未就绪" : "OSM 来源字节尚未就绪")
      : `${this.authoringSource.catalog.display_name}（${this.authoringSource.catalog.source_id}） · 原始 SHA-256 ${this.authoringSource.catalog.sha256} · ${this.authoringSource.catalog.size_bytes.toLocaleString()} bytes`;
    if (selected !== null) selected.textContent = this.authoringSelection === null ? "尚未框选区域" :
      `ENU 东 ${this.authoringSelection.bounds_enu_m.min_east_m.toFixed(2)}…${this.authoringSelection.bounds_enu_m.max_east_m.toFixed(2)} m · 北 ${this.authoringSelection.bounds_enu_m.min_north_m.toFixed(2)}…${this.authoringSelection.bounds_enu_m.max_north_m.toFixed(2)} m`;
    if (job !== null) {
      const compiler = this.authoringJob?.compiler_manifest_sha256;
      job.textContent = this.authoringJob === null ? this.authoringStage
        : `${this.authoringStage} · 任务 ${this.authoringJob.job_id.slice(0, 16)}… · 编译 manifest ${compiler == null ? "未生成" : `${compiler.slice(0, 16)}…`}`;
      job.title = compiler ?? "";
    }
    if (issue !== null) issue.textContent = this.authoringError ?? this.authoringSourceError ?? "";
    if (build !== null) build.disabled = this.authoringSelection === null || this.authoringSource === null || this.authoringBusy;
    this.selectedDraftControls?.update(this.selectedDraftControlState());
    this.renderSummary();
  }

  /** Source picker for the region tab; the active source is always a registered catalog entry. */
  private renderAuthoringSourceControl(): HTMLElement {
    const wrap = document.createElement("div");
    wrap.className = "studio-authoring-sources studio-field";
    wrap.style.margin = "7px 0 0";
    const label = document.createElement("span");
    label.className = "studio-field-label";
    label.textContent = "OSM 数据来源";
    const select = document.createElement("select");
    select.id = "studio-authoring-source-select";
    select.setAttribute("aria-label", "OSM 数据来源");
    const catalog = this.authoringCatalog;
    if (catalog === null) {
      const option = new Option(this.authoringSourceLoading ? "正在读取来源目录…" : "来源目录未就绪", "");
      option.disabled = true; select.append(option); select.disabled = true;
    } else {
      for (const source of catalog.sources) {
        const bytes = source.size_bytes >= 1024 * 1024
          ? `${(source.size_bytes / (1024 * 1024)).toFixed(1)} MB`
          : `${(source.size_bytes / 1024).toFixed(0)} KB`;
        select.append(new Option(
          `${source.display_name} · ${source.source_id} · sha256 ${source.sha256.slice(0, 12)}… · ${bytes}`,
          source.source_id));
      }
      select.value = this.authoringSource?.catalog.source_id ?? "";
      select.disabled = catalog.sources.length === 0;
      select.addEventListener("change", () => {
        if (select.value !== "" && select.value !== this.authoringSource?.catalog.source_id) {
          void this.switchAuthoringSource(select.value);
        }
      });
    }
    wrap.append(label, select);
    return wrap;
  }

  /** Loads the registered source catalog and, unless restore already chose one, the default source. */
  private async loadAuthoringSourceCatalog(): Promise<void> {
    if (this.authoringSourceLoading) return;
    const request = ++this.authoringSwitchRevision;
    this.authoringSourceLoading = true; this.updateRegionDetails();
    try {
      const catalog = await fetchAuthoringCatalog();
      if (request !== this.authoringSwitchRevision) return;
      this.authoringCatalog = catalog;
      this.authoringSourceError = null;
      if (this.authoringSource === null && this.authoringRequestedSourceId === null) {
        const source = catalog.sources[0]!;
        await this.selectAuthoringSource(source);
      }
    } catch (error) {
      if (request !== this.authoringSwitchRevision) return;
      this.authoringSourceError = `读取 OSM 来源失败：${errorMessage(error)}`;
    } finally {
      if (request === this.authoringSwitchRevision) {
        this.authoringSourceLoading = false;
        if (this.tab === "region") this.renderRegionTab();
      }
    }
  }

  private async switchAuthoringSource(sourceId: string): Promise<void> {
    const catalog = this.authoringCatalog;
    const source = catalog?.sources.find(item => item.source_id === sourceId);
    if (source === undefined || source.source_id === this.authoringSource?.catalog.source_id) return;
    await this.selectAuthoringSource(source);
  }

  /** Drop every display and in-flight build for the old source, then fetch and mount the chosen one.
   * A stale fetch is never applied: the switch revision guards every continuation. */
  private async selectAuthoringSource(source: AuthoringSource): Promise<void> {
    if (source.source_id === this.authoringSource?.catalog.source_id) return;
    this.clearSelectedScenarioState();
    const request = ++this.authoringSwitchRevision;
    this.authoringSourceLoading = true;
    this.authoringSource = null;
    this.regionSelector?.destroy(); this.regionSelector = null;
    this.regionSelectorLoading = false;
    this.authoringAbort?.abort(); this.authoringAbort = null;
    this.authoringRevision++;
    this.authoringBusy = false;
    this.authoringSelection = null; this.authoringJob = null; this.authoringError = null;
    this.selectedDraft = null; this.selectedDraftSceneReady = false; this.selectedDraftBusy = false;
    this.selectedDraftPendingImport = null; this.selectedDraftError = null;
    this.authoringStage = `读取 OSM 来源 · ${source.display_name}`;
    this.authoringMapKey = `selection-${this.authoringRevision}`;
    this.map?.render(this.authoringScene(), EMPTY_VIEW);
    this.updateRegionDetails();
    try {
      const bytes = await fetchAuthoringSource(source);
      if (request !== this.authoringSwitchRevision) return;
      this.authoringSource = { catalog: source, bytes };
      this.authoringSourceError = null;
    } catch (error) {
      if (request !== this.authoringSwitchRevision) return;
      this.authoringSourceError = `读取 OSM 来源失败：${errorMessage(error)}`;
    } finally {
      if (request === this.authoringSwitchRevision) {
        this.authoringSourceLoading = false;
        if (this.tab === "region") this.renderRegionTab();
      }
    }
  }

  /** A draft may display only when the registered source exactly matches its bound identity. */
  private async ensureAuthoringSourceMatches(sourceId: string, sourceSha: string): Promise<boolean> {
    const active = this.authoringSource;
    if (active !== null && active.catalog.source_id === sourceId && active.catalog.sha256 === sourceSha) return true;
    let catalog = this.authoringCatalog;
    if (catalog === null) {
      try {
        catalog = await fetchAuthoringCatalog();
      } catch (error) {
        this.authoringSourceError = `读取 OSM 来源注册失败：${errorMessage(error)}`;
        this.updateRegionDetails();
        return false;
      }
      this.authoringCatalog = catalog;
    }
    const verdict = resolveRegisteredSource(catalog, sourceId, sourceSha);
    if (verdict.kind !== "match") {
      const message = verdict.kind === "missing"
        ? `注册 OSM 来源已移除：${verdict.source_id}，拒绝恢复选区草稿`
        : "注册 OSM 来源 SHA-256 已变化，拒绝恢复选区草稿";
      this.authoringSourceError = message; this.selectedDraftError = message;
      this.updateRegionDetails();
      return false;
    }
    this.authoringRequestedSourceId = sourceId;
    try {
      await this.selectAuthoringSource(verdict.source);
    } finally {
      if (this.authoringRequestedSourceId === sourceId) this.authoringRequestedSourceId = null;
    }
    const after = this.authoringSource;
    return after !== null && after.catalog.source_id === sourceId && after.catalog.sha256 === sourceSha;
  }

  private async mountRegionSelector(host: HTMLElement): Promise<void> {
    const source = this.authoringSource;
    if (source === null) return;
    this.regionSelectorLoading = true;
    try {
      const selector = await CityRegionSelector.mount(host, {
        source: { source_id: source.catalog.source_id, source_sha256: source.catalog.sha256,
          bytes: source.bytes, origin: source.catalog.origin },
        onSelection: selection => this.selectAuthoringRegion(selection),
      });
      const bounds = selector.source.bounds, declared = source.catalog.bounds_wgs84;
      if (bounds.minlat !== declared.min_latitude_deg || bounds.maxlat !== declared.max_latitude_deg
        || bounds.minlon !== declared.min_longitude_deg || bounds.maxlon !== declared.max_longitude_deg) {
        selector.destroy(); throw new Error("catalog 的经纬度边界与原始 OSM 不一致");
      }
      if (this.authoringSource !== source) { selector.destroy(); return; }
      this.regionSelector = selector;
      if (this.selectedDraft !== null) selector.showRestoredSelection(this.selectedDraft.selection);
      if (this.tab === "region" && !host.isConnected) this.renderRegionTab();
    } catch (error) {
      if (this.authoringSource !== source) return;
      this.authoringSourceError = `地图选区加载失败：${errorMessage(error)}`;
      this.updateRegionDetails();
    } finally {
      if (this.authoringSource === source) this.regionSelectorLoading = false;
    }
  }

  private selectAuthoringRegion(selection: SceneSelection): void {
    this.clearSelectedScenarioState();
    this.authoringAbort?.abort(); this.authoringAbort = null;
    this.authoringRevision++; this.authoringBusy = false;
    this.selectedDraftBusy = false; this.selectedDraftSceneReady = false;
    this.selectedDraftPendingImport = null; this.selectedDraft = null; this.selectedDraftError = null;
    this.authoringSelection = selection; this.authoringJob = null; this.authoringError = null;
    this.authoringStage = "选区已确定，等待生成静态场景";
    this.authoringMapKey = `selection-${this.authoringRevision}`;
    this.map?.render(this.authoringScene(), EMPTY_VIEW);
    this.updateRegionDetails();
  }

  private async buildAuthoringSelection(): Promise<void> {
    if (this.authoringSelection === null || this.authoringBusy) return;
    this.clearSelectedScenarioState();
    const selection = this.authoringSelection;
    const revision = ++this.authoringRevision;
    this.authoringAbort?.abort();
    const controller = new AbortController(); this.authoringAbort = controller;
    this.authoringBusy = true; this.authoringError = null; this.authoringJob = null;
    this.selectedDraft = null; this.selectedDraftSceneReady = false;
    this.selectedDraftPendingImport = null; this.selectedDraftError = null; this.selectedDraftBusy = false;
    this.authoringStage = "queued · 正在提交选区";
    this.authoringMapKey = `selection-${revision}`;
    this.map?.render(this.authoringScene(), EMPTY_VIEW);
    this.updateRegionDetails();
    try {
      let current = await submitSceneSelection(selection, controller.signal);
      const jobId = current.job_id, selectionHash = current.selection_sha256;
      while (true) {
        if (revision !== this.authoringRevision || controller.signal.aborted) return;
        if (current.source_sha256 !== selection.source_sha256 || current.job_id !== jobId || current.selection_sha256 !== selectionHash) throw new Error("构建任务身份在轮询期间改变");
        this.authoringJob = current;
        this.authoringStage = ({ queued: "queued · 排队中", compiling: "compiling · 编译 OSM",
          meshing: "meshing · 生成官方网格", networking: "networking · 构建真实 SUMO 网络",
          surfaces: "surfaces · 生成道路与步道", placing: "placing · 放置建筑与街道设施",
          auditing: "auditing · 校验静态城市资产", ready: "ready · 验证并装配完整城市呈现",
          failed: "failed · 构建失败" })[current.state];
        this.updateRegionDetails();
        if (current.state === "failed") throw new Error(`${current.error!.code}：${current.error!.message}`);
        if (current.state === "ready") {
          const draft = await createSelectedSceneDraft(selection, current);
          const presentation = await loadReadyAuthoringPresentation(current, selection, controller.signal);
          if (revision !== this.authoringRevision || controller.signal.aborted) { presentation.pack.dispose(); return; }
          this.selectedDraft = draft; this.selectedDraftSceneReady = false;
          this.selectedPresentation = presentation;
          this.selectedDraftPendingImport = null; this.selectedDraftError = null; this.selectedDraftBusy = false;
          this.authoringMapKey = `${jobId}:${draft.presentation_manifest_sha256}`;
          if (this.map === null) { presentation.pack.dispose(); throw new Error("三维视图未就绪"); }
          this.map.render({ ...EMPTY_SCENE, authoring: { key: this.authoringMapKey, presentation } }, EMPTY_VIEW);
          this.authoringBusy = false; this.updateRegionDetails();
          return;
        }
        await new Promise<void>((resolve, reject) => {
          const timer = window.setTimeout(() => { controller.signal.removeEventListener("abort", abort); resolve(); }, 1200);
          const abort = (): void => { window.clearTimeout(timer); reject(new DOMException("构建任务已取消", "AbortError")); };
          controller.signal.addEventListener("abort", abort, { once: true });
        });
        current = await fetchAuthoringJob(jobId, controller.signal);
      }
    } catch (error) {
      if (revision !== this.authoringRevision || controller.signal.aborted) return;
      this.clearSelectedScenarioState();
      this.authoringBusy = false;
      this.authoringStage = "构建失败";
      this.authoringError = `静态场景生成失败：${errorMessage(error)}`;
      this.updateRegionDetails();
    }
  }

  private selectedDraftControlState(): SelectedDraftControlState {
    return {
      identity: this.selectedDraft,
      sceneReady: this.selectedDraftSceneReady,
      busy: this.selectedDraftBusy || this.authoringBusy,
      saved: storedSelectedSceneDraftMatches(this.selectedDraft),
      error: this.selectedDraftError,
    };
  }

  /** Saving or exporting needs the browser's sceneReady for this identity, not merely API ready. */
  private async saveSelectedDraft(): Promise<void> {
    const draft = this.selectedDraft;
    if (draft === null || !this.selectedDraftSceneReady || this.selectedDraftBusy) return;
    try {
      await saveSelectedSceneDraft(draft);
      this.selectedDraftError = null;
    } catch (error) {
      this.selectedDraftError = `保存选区草稿失败：${errorMessage(error)}`;
    }
    this.updateRegionDetails();
  }

  private exportSelectedDraft(): void {
    const draft = this.selectedDraft;
    if (draft === null || !this.selectedDraftSceneReady || this.selectedDraftBusy) return;
    try {
      const content = exportSelectedSceneDraft(draft) + "\n";
      const file = new Blob([content], { type: "application/json" });
      const url = URL.createObjectURL(file);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `selected-city-${draft.job_id.slice(0, 16)}.json`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      this.selectedDraftError = null;
    } catch (error) {
      this.selectedDraftError = `导出选区草稿失败：${errorMessage(error)}`;
    }
    this.updateRegionDetails();
  }

  private async importSelectedDraft(file: File): Promise<void> {
    if (this.authoringBusy || this.selectedDraftBusy) return;
    this.selectedDraftError = null;
    let draft: SelectedSceneDraft;
    try {
      // An unprovable imported selection is rejected here, before any fetch or display.
      draft = await importSelectedSceneDraft(await file.text());
    } catch (error) {
      this.selectedDraftError = `导入选区草稿失败：${errorMessage(error)}`;
      this.updateRegionDetails();
      return;
    }
    if (!await this.ensureAuthoringSourceMatches(draft.selection.source_id, draft.source_sha256)) return;
    await this.restoreSelectedDraft(draft, file.name);
  }

  /** Re-fetch and verify one bound job. A failure never rebuilds or shows another city. */
  private async restoreSelectedDraft(draft: SelectedSceneDraft, importedFrom: string | null): Promise<void> {
    this.clearSelectedScenarioState();
    const revision = ++this.authoringRevision;
    this.authoringAbort?.abort();
    const controller = new AbortController(); this.authoringAbort = controller;
    this.authoringBusy = true; this.selectedDraftBusy = true;
    this.authoringError = null; this.selectedDraftError = null;
    this.selectedDraft = null; this.selectedDraftSceneReady = false;
    this.selectedDraftPendingImport = importedFrom;
    this.authoringJob = null;
    this.authoringStage = "恢复选区草稿 · 核验任务与来源身份";
    this.authoringMapKey = `selection-${revision}`;
    this.map?.render(this.authoringScene(), EMPTY_VIEW);
    this.updateRegionDetails();
    const result = await restoreSelectedSceneDraft(draft, {
      fetchCatalog: () => fetchAuthoringCatalog(controller.signal),
      fetchJob: jobId => fetchAuthoringJob(jobId, controller.signal),
      loadPresentation: (job, selection) => loadReadyAuthoringPresentation(job, selection, controller.signal),
    });
    if (revision !== this.authoringRevision || controller.signal.aborted) {
      if (result.ok) result.presentation.pack.dispose();
      return;
    }
    this.authoringBusy = false; this.selectedDraftBusy = false;
    if (!result.ok) {
      this.selectedDraftPendingImport = null;
      this.selectedDraftSceneReady = false;
      this.authoringStage = "选区草稿恢复失败";
      this.authoringError = result.message;
      this.selectedDraftError = result.message;
      this.updateRegionDetails();
      return;
    }
    try {
      this.authoringJob = result.job;
      this.authoringSelection = draft.selection;
      this.selectedDraft = result.draft;
      this.selectedPresentation = result.presentation;
      this.regionSelector?.showRestoredSelection(result.draft.selection);
      this.authoringMapKey = `${result.job.job_id}:${result.draft.presentation_manifest_sha256}`;
      if (this.map === null) throw new Error("三维视图未就绪");
      this.map.render({ ...EMPTY_SCENE, authoring: { key: this.authoringMapKey, presentation: result.presentation } }, EMPTY_VIEW);
      this.authoringStage = "ready · 正在装配完整静态城市呈现";
    } catch (error) {
      result.presentation.pack.dispose();
      this.clearSelectedScenarioState();
      this.selectedDraft = null; this.selectedDraftSceneReady = false;
      this.selectedDraftPendingImport = null;
      this.authoringStage = "选区草稿恢复失败";
      this.authoringError = `选区草稿恢复失败：${errorMessage(error)}`;
      this.selectedDraftError = this.authoringError;
    }
    this.updateRegionDetails();
  }

  /** The one stored selected-scene draft, restored after refresh. Never the old scenePath draft.
   * A stored draft binds a registered source; a non-default source is selected automatically so
   * the refresh can recover it. A removed or re-hashed source shows an explicit error instead. */
  private async restoreStoredSelectedDraft(): Promise<void> {
    let stored: SelectedSceneDraft | null;
    try {
      stored = await loadSelectedSceneDraft();
    } catch (error) {
      this.selectedDraftError = `本地选区草稿读取失败：${errorMessage(error)}`;
      this.updateRegionDetails();
      return;
    }
    if (stored === null) return;
    if (!await this.ensureAuthoringSourceMatches(stored.selection.source_id, stored.source_sha256)) return;
    await this.restoreSelectedDraft(stored, null);
  }

  /** A completed import is saved only after the browser reports sceneReady for the verified identity. */
  private async finishPendingSelectedImport(): Promise<void> {
    const pending = this.selectedDraftPendingImport;
    const draft = this.selectedDraft;
    if (pending === null || draft === null || !this.selectedDraftSceneReady) return;
    this.selectedDraftPendingImport = null;
    try {
      await saveSelectedSceneDraft(draft);
      this.selectedDraftError = null;
    } catch (error) {
      this.selectedDraftError = `导入未保存：${errorMessage(error)}`;
    }
    this.updateRegionDetails();
  }

  /** The backdrop label follows the collision source exposed to the editor. */
  private syncSceneBackdropLabels(): void {
    if (this.authoringMode) return;
    if (this.nativeReference !== null) {
      const inspection = inspectNativeReferenceVisual(this.nativeReference);
      this.mapPlace.textContent = t("studio.nativeSource");
      this.mapMode.textContent = t("studio.referenceMode");
      const hasVisual = inspection.kind !== "unavailable";
      this.previewKind.textContent = hasVisual
        ? inspection.kind === "native-city" ? t("studio.verifiedCity") : t("studio.verifiedReference")
        : t("studio.noMesh");
      this.previewFootPrimary.textContent = tf("studio.nativeFooter", { registration: this.nativeReference.registration.registration_id });
    } else if (this.spatial === null) {
      this.mapPlace.textContent = cityPreviewScenePresetForPath(this.config.scenePath)?.placeLabel
        ?? t("studio.ordinaryPreview");
      this.previewKind.textContent = "／ 场景资料加载中";
      this.previewFootPrimary.textContent = "正在读取场景几何资料。";
    } else if (this.spatial.data.roadClearance === null) {
      this.mapPlace.textContent = cityPreviewScenePresetForPath(this.config.scenePath)?.placeLabel
        ?? this.spatial.sceneName;
      this.previewKind.textContent = "／ 道路净空未验证";
      this.previewFootPrimary.textContent = this.spatial.data.roadClearanceError
        ?? "当前场景缺少道路与有效设施净空；地面设施编辑已阻止。";
    } else {
      this.mapPlace.textContent = cityPreviewScenePresetForPath(this.config.scenePath)?.placeLabel
        ?? this.spatial.sceneName;
      this.previewKind.textContent = "／ SUMO 记录背景";
      this.previewFootPrimary.textContent = "基线交通数量只筛选当前记录；“SUMO 交通预览”面板可按显式档案重新生成并审计离线工程轨迹。两者均不等于正式 SUMO Provider 运行。";
    }
  }

  private async loadInitialSpatial(): Promise<void> {
    try {
      const scenePath = this.config.scenePath;
      if (cityPreviewScenePresetForPath(scenePath) === null) {
        const outcome = await fetchNativeSceneCatalog();
        if (outcome.kind === "error") throw new Error(`[${outcome.code}] ${outcome.detail}`);
        if (this.authoringMode || this.config.scenePath !== scenePath) return;
        const registration = savedNativeRegistration(scenePath, outcome.catalog.registrations);
        if (registration !== null) {
          const reference = await loadNativeReferenceScene(registration);
          if (this.authoringMode || this.config.scenePath !== scenePath) return;
          // Keep the saved edited draft. The registered reference supplies verified
          // public geometry; it must not replace saved seed/name/business inputs.
          this.nativeReference = reference;
          this.spatial = nativeReferenceSpatialSource(reference);
          this.draftError = this.validateDraft();
          this.startMap();
          if (this.tab === "spatial" || this.tab === "runtime") this.renderTab();
          this.renderSummary();
          return;
        }
      }
      this.startMap();
      this.spatial = await loadSpatialSource(scenePath);
      this.syncRenderedBuildingObstacles();
      this.draftError = this.validateDraft();
      if (this.tab === "spatial") this.renderTab();
      if (!this.authoringMode) this.requestApply();
    } catch (error) {
      this.draftError = `场景几何资料加载失败：${errorMessage(error)}`;
      if (this.tab === "spatial") this.renderTab();
    }
    this.renderSummary();
  }

  /** The map's verified source envelopes also govern the editable 2D overlay. */
  private syncRenderedBuildingObstacles(): void {
    if (this.nativeReference !== null || !this.mapReady || this.map === null || this.spatial === null) return;
    this.spatial = { ...this.spatial,
      data: { ...this.spatial.data, buildings: this.map.workspaceBuildingObstacles() } };
  }

  private validateDraft(): string | null {
    try {
      parseCityWorkspaceConfig(this.config);
      if (this.spatial === null) return "场景几何资料尚未就绪";
      const geometryIssues = validateStudioGeometry(this.config, this.spatial.data);
      return geometryIssues.length ? geometryIssues.join("；") : null;
    } catch (error) {
      return errorMessage(error);
    }
  }

  private changeDraft(next: CityWorkspaceConfig): void {
    const previous = this.config;
    if (JSON.stringify(next) === JSON.stringify(previous)) return;
    const sceneChanged = (["scenePath", "environment", "fleet", "traffic", "facilities", "airspace",
      "stateKeyframes", "authoredLandscape"] as const)
      .some(key => JSON.stringify(next[key]) !== JSON.stringify(previous[key]));
    const currentPreview = this.previewApplied && this.appliedRevision === this.applyRevision;
    this.config = next;
    this.pendingImport = null;
    if (this.applyTimer !== null) window.clearTimeout(this.applyTimer);
    this.applyTimer = null;
    this.applyDue = false;
    this.applyRevision++;
    this.draftError = this.validateDraft();
    if (sceneChanged) this.previewIssues = [];
    this.previewError = null;
    this.operationError = null;
    this.saveStatus.textContent = this.draftError === null ? t("studio.dirty") : t("studio.needsFix");
    this.saveStatus.dataset.state = this.draftError === null ? "dirty" : "error";
    this.nameInput.value = this.config.name;
    this.trafficPreviewPanel?.refresh();
    const timelineSecond = Number(this.mapRoot.dataset.previewSecond ?? "0");
    this.eventTimeline?.update(this.config.events,
      Number.isFinite(timelineSecond) ? Math.max(0, timelineSecond) : 0);
    // Names, algorithm drafts and event declarations do not change collision geometry
    // or the rendered scene. Keep the already-validated preview, including its issues.
    if (!sceneChanged && currentPreview && this.draftError === null) this.appliedRevision = this.applyRevision;
    this.renderSummary();
    if (this.nativeReference !== null) {
      this.previewStatus.textContent = this.draftError === null
        ? t("studio.nativeChanged") : t("studio.nativeFix");
      this.previewStatus.dataset.state = this.draftError === null ? "warning" : "error";
      return;
    }
    if (!sceneChanged && currentPreview && this.draftError === null) return;
    if (this.draftError === null) {
      if (next.facilities !== previous.facilities || next.airspace !== previous.airspace) {
        this.applyDue = true;
        if (this.mapReady) void this.applyLatest();
      } else {
        this.applyTimer = window.setTimeout(() => {
          this.applyTimer = null;
          this.applyDue = true;
          if (this.mapReady) void this.applyLatest();
        }, 180);
      }
    }
  }

  /** A render-bar environment edit is an ordinary draft edit: it marks the draft
   * dirty and is applied through the same path as panel edits; saving stays explicit. */
  private applyEnvironmentEdit(environment: CityWorkspaceConfig["environment"]): void {
    this.changeDraft({ ...this.config, environment });
    if (this.tab === "runtime") this.renderTab();
  }

  private async reloadNativeReferenceMap(reference: VerifiedNativeReferenceScene, epoch: number): Promise<void> {
    this.nativeReferenceAbort?.abort();
    const controller = new AbortController();
    this.nativeReferenceAbort = controller;
    try {
      const visual = await loadNativeReferenceVisual(reference, {
        signal: controller.signal,
        onProgress: progress => {
          if (epoch !== this.mapEpoch || this.nativeReference !== reference
              || this.nativeReferenceAbort !== controller) return;
          this.previewStatus.textContent = progress.phase === "layers"
            ? tf("studio.nativeLayers", { completed: progress.completed, total: progress.total })
            : tf("studio.nativeAssembling", { completed: progress.completed, total: progress.total });
          this.previewStatus.dataset.state = "loading";
        },
      });
      if (epoch !== this.mapEpoch || this.nativeReference !== reference || this.map === null
          || this.authoringMode || controller.signal.aborted) {
        visual.dispose();
        return;
      }
      try {
        this.map.render({ ...EMPTY_SCENE, scenario: reference.scenario, pack: visual.pack,
          nativePresentation: visual.nativePresentation }, EMPTY_VIEW);
      } catch (error) {
        visual.dispose();
        throw error;
      }
      this.nativeReferencePresentation = visual.nativePresentation;
      if (visual.kind === "unavailable") {
        this.mapReady = false;
        this.previewApplied = false;
        this.previewError = null;
        this.basemapNote = t("studio.nativeNoMeshNote");
        this.previewStatus.textContent = t("studio.nativeVerifiedNoMesh");
        this.previewStatus.dataset.state = "warning";
        this.renderSummary();
      } else if (visual.kind === "native-city") {
        this.mapReady = true;
        this.previewError = null;
        this.basemapNote = t("studio.nativeAssets");
        this.previewStatus.textContent = t("studio.nativeReady");
        this.previewStatus.dataset.state = "ready";
        this.renderSummary();
      }
    } catch (error) {
      if (epoch !== this.mapEpoch || this.nativeReference !== reference || controller.signal.aborted) return;
      this.mapReady = false;
      this.previewError = `${t("studio.nativeAssembleFailed")}${errorMessage(error)}`;
      this.renderSummary();
    } finally {
      if (this.nativeReferenceAbort === controller) this.nativeReferenceAbort = null;
    }
  }

  private startMap(): void {
    const url = new URL(window.location.href);
    if (!this.authoringMode && this.nativeReference === null) url.searchParams.set("city", this.config.scenePath);
    else url.searchParams.delete("city");
    window.history.replaceState(null, "", url);
    this.nativeReferenceAbort?.abort(); this.nativeReferenceAbort = null;
    this.mapEpoch++;
    const epoch = this.mapEpoch;
    this.map?.destroy();
    this.nativeReferencePresentation?.dispose();
    this.nativeReferencePresentation = null;
    this.mapRoot.replaceChildren();
    this.mapReady = false;
    this.previewApplied = false;
    this.playButton.disabled = true;
    this.timeInput.disabled = true;
    this.previewError = null;
    this.previewStatus.textContent = this.authoringMode ? t("studio.authoringStaticPreview") : t("studio.cityLoading");
    this.previewStatus.dataset.state = "loading";
    try {
      this.map = new PublicTraceMap(this.mapRoot, {
        onPick: target => this.onPick(target),
        onHover: () => undefined,
        onContextMenu: () => undefined,
        onBasemapNote: note => { if (epoch === this.mapEpoch) { this.basemapNote = note; this.renderSummary(); } },
        onEnvironmentEdit: environment => {
          if (epoch !== this.mapEpoch || this.authoringMode) return;
          this.applyEnvironmentEdit(environment);
        },
        onSceneStatus: (digest, status, detail) => {
          if (epoch !== this.mapEpoch) return;
          if (this.authoringMode && digest === `authoring:${this.authoringMapKey}`) {
            if (status === "failed") {
              this.clearSelectedScenarioState();
              this.authoringBusy = false;
              this.selectedDraftBusy = false;
              this.selectedDraftSceneReady = false;
              this.selectedDraftPendingImport = null;
              this.authoringStage = "城市呈现装配失败";
              this.authoringError = `${t("studio.nativeCityAssetsFailed")}${detail ?? t("studio.unknownReason")}`;
            } else {
              this.authoringStage = t("studio.authoringReady");
              this.authoringError = null;
              this.selectedDraftSceneReady = true;
              void this.finishPendingSelectedImport();
              void this.initializeSelectedScenarioForReadyScene();
            }
            this.updateRegionDetails();
            return;
          }
          const native = this.nativeReference;
          if (!this.authoringMode && native !== null && digest === native.scenario.scenario_digest) {
            if (status === "failed") {
              this.mapReady = false;
              this.previewError = `${t("studio.nativeAssembleFailed")}${detail ?? t("studio.unknownReason")}`;
              this.previewStatus.textContent = t("studio.nativeSceneFailed");
              this.previewStatus.dataset.state = "error";
            } else if (!nativeReferenceHasVisualPresentation(native)) {
              // An incomplete visual declaration remains valid compiler input, but a
              // renderer callback must not promote it to a visual-ready state.
              this.mapReady = false;
              this.previewApplied = false;
              this.previewError = null;
              this.basemapNote = t("studio.nativeNoMeshNote");
              this.previewStatus.textContent = t("studio.nativeVerifiedNoMesh");
              this.previewStatus.dataset.state = "warning";
            } else {
              this.mapReady = true;
              this.previewError = null;
              this.previewStatus.textContent = inspectNativeReferenceVisual(native).kind === "native-city"
                ? t("studio.nativeReady") : t("studio.nativeSceneReady");
              this.previewStatus.dataset.state = "ready";
            }
            this.renderSummary();
            return;
          }
          if (digest !== "default-pack" || this.authoringMode) return;
          if (status === "failed") {
            this.mapReady = false;
            this.previewError = `${t("studio.cityLoadFailed")}${detail ?? t("studio.unknownReason")}`;
            if (this.pendingImport !== null) {
              this.saveStatus.textContent = t("studio.importUnsavedFailed");
              this.saveStatus.dataset.state = "error";
              this.operationError = this.previewError;
              this.pendingImport = null;
            }
            this.renderSummary();
            return;
          }
          this.mapReady = true;
          this.syncRenderedBuildingObstacles();
          this.draftError = this.validateDraft();
          if (this.tab === "spatial") this.renderTab();
          this.previewStatus.textContent = t("studio.cityReady");
          this.previewStatus.dataset.state = "ready";
          this.requestApply();
          this.renderSummary();
        },
      });
      if (this.authoringMode) this.map.render(this.authoringScene(), EMPTY_VIEW);
      else if (this.nativeReference === null) this.map.render(EMPTY_SCENE, EMPTY_VIEW);
      else void this.reloadNativeReferenceMap(this.nativeReference, epoch);
    } catch (error) {
      this.mapReady = false;
      this.previewError = `${t("studio.mapStartFailed")}${errorMessage(error)}`;
      this.renderSummary();
    }
  }

  private onPick(target: TraceTarget | null): void {
    if (target === null) return;
    const facility = this.config.facilities.find(item => item.id === target.id);
    const craft = this.map?.workspaceEntityChoices().find(item => item.id === target.id);
    if (facility !== undefined) {
      this.activateTab("spatial", true);
      this.previewMetrics.textContent = `已选中设施 ${facility.name} · X ${facility.position.x.toFixed(1)} m / Z ${facility.position.z.toFixed(1)} m`;
    } else if (craft !== undefined) {
      try {
        this.followSelect.value = target.id;
        this.map?.followWorkspaceEntity(target.id);
      } catch (error) {
        this.previewError = `跟随视角失败：${errorMessage(error)}`;
        this.renderSummary();
      }
    }
  }

  private requestApply(): void {
    if (this.authoringMode || this.nativeReference !== null) return;
    if (this.applyTimer !== null) window.clearTimeout(this.applyTimer);
    this.applyTimer = null;
    this.applyRevision++;
    this.applyDue = true;
    if (this.mapReady && this.spatial !== null && this.draftError === null) void this.applyLatest();
  }

  private async applyLatest(): Promise<void> {
    if (this.applying) return;
    this.applying = true;
    try {
      while (this.appliedRevision < this.applyRevision && this.applyDue && this.mapReady && this.draftError === null) {
        const revision = this.applyRevision;
        const epoch = this.mapEpoch;
        const map = this.map;
        if (map === null) break;
        const config = this.config;
        this.previewStatus.textContent = "正在应用编排";
        this.previewStatus.dataset.state = "loading";
        try {
          const issues = await map.applyWorkspaceConfig(config);
          if (epoch !== this.mapEpoch || revision !== this.applyRevision) continue;
          this.previewIssues = issues;
          this.previewError = null;
          this.previewApplied = true;
          this.playButton.disabled = false;
          this.timeInput.disabled = false;
          this.appliedRevision = revision;
          this.previewStatus.textContent = issues.length ? "预览需补全" : "预览已更新";
          this.previewStatus.dataset.state = issues.length ? "warning" : "ready";
          this.renderFollowChoices();
          this.syncPlayback();
          this.finishPendingImport();
          this.renderSummary();
        } catch (error) {
          if (epoch !== this.mapEpoch || revision !== this.applyRevision) continue;
          this.previewError = `预览应用失败：${errorMessage(error)}`;
          this.previewApplied = false;
          this.playButton.disabled = true;
          this.timeInput.disabled = true;
          if (this.pendingImport?.config === config) {
            this.saveStatus.textContent = "导入未保存：预览核验失败";
            this.saveStatus.dataset.state = "error";
            this.operationError = this.previewError;
            this.pendingImport = null;
          }
          this.appliedRevision = revision;
          this.renderSummary();
        }
      }
    } finally {
      this.applying = false;
      if (this.appliedRevision < this.applyRevision && this.applyDue && this.mapReady && this.draftError === null) {
        void this.applyLatest();
      }
    }
  }

  private renderFollowChoices(): void {
    const selected = this.followSelect.value;
    const options = this.map?.workspaceEntityChoices() ?? [];
    this.followSelect.replaceChildren(new Option(t("studio.overview"), ""),
      ...options.map(item => new Option(item.label, item.id)));
    this.followSelect.value = options.some(item => item.id === selected) ? selected : "";
  }

  private collisionIssues(): readonly { path: string; code: string; message: string }[] {
    return this.previewIssues.filter(issue => BLOCKING_FLIGHT_ISSUES.has(issue.code));
  }

  private finishPendingImport(): void {
    const pending = this.pendingImport;
    if (pending === null || pending.config !== this.config) return;
    this.pendingImport = null;
    const collisions = this.collisionIssues();
    if (collisions.length) {
      this.saveStatus.textContent = `导入未保存：${collisions.length} 处飞行碰撞`;
      this.saveStatus.dataset.state = "error";
      this.operationError = `导入未保存：${collisions.map(issue => `${issue.path}：${issue.message}`).join("；")}`;
      return;
    }
    try {
      saveCityWorkspaceConfig(this.config);
      this.saveStatus.textContent = `${pending.fileName} 已导入并保存`;
      this.saveStatus.dataset.state = "saved";
      this.operationError = null;
    } catch (error) {
      this.saveStatus.textContent = `导入未保存：${errorMessage(error)}`;
      this.saveStatus.dataset.state = "error";
      this.operationError = `导入未保存：${errorMessage(error)}`;
    }
  }

  private renderSummary(): void {
    if (this.authoringMode) {
      for (const selector of ["#studio-save", "#studio-export"]) {
        const button = requireElement<HTMLButtonElement>(selector);
        button.disabled = true;
        button.title = "选区场景与物流配置是只读导入来源；不会在此处修改或覆盖";
      }
      const importControl = requireElement<HTMLInputElement>("#studio-import");
      importControl.disabled = true;
      importControl.title = "选区来源只读；返回工作区后可导入 v3 工作区 JSON";
      const selection = this.authoringSelection;
      const scenario = this.selectedScenario;
      const counts = scenario === null ? "" : ` · ${scenario.facilities.length} 处设施 / ${scenario.noFlyZones.length} 处禁飞区`
        + ` / ${scenario.fleet.reduce((sum, item) => sum + item.count, 0)} 架无人机`
        + ` / 背景车辆 ${scenario.demand.vehicles}、行人 ${scenario.demand.pedestrians}、自行车 ${scenario.demand.bicycles}`;
      const orderCount = this.selectedLogistics === null ? ""
        : ` / ${this.selectedLogistics.orders.length} 单人工需求`;
      this.previewMetrics.textContent = selection === null
        ? "等待注册 OSM 选区 · 尚未运行"
        : `原始 OSM ${selection.source_sha256.slice(0, 16)}… · ENU 东 ${selection.bounds_enu_m.min_east_m.toFixed(1)}…${selection.bounds_enu_m.max_east_m.toFixed(1)} m / 北 ${selection.bounds_enu_m.min_north_m.toFixed(1)}…${selection.bounds_enu_m.max_north_m.toFixed(1)} m${counts}${orderCount} · 尚未运行`;
      const failure = this.authoringError ?? this.authoringSourceError;
      this.previewStatus.textContent = failure === null ? this.authoringStage : "静态预览失败";
      this.previewStatus.dataset.state = failure === null ? this.authoringJob?.state === "ready" ? "ready" : "loading" : "error";
      this.issuesRoot.replaceChildren(...[failure, this.operationError].filter((message): message is string => message !== null)
        .map(message => {
          const item = document.createElement("p"); item.textContent = message; return item;
        }));
      this.issuesRoot.hidden = this.issuesRoot.childElementCount === 0;
      this.issuesRoot.dataset.state = "error";
      return;
    }
    const needsPreview = this.config.facilities.length > 0 || this.config.stateKeyframes.length > 0
      || this.config.fleet.some(item => item.homeFacilityId !== null);
    const checking = this.spatial === null || (this.nativeReference === null && needsPreview
      && (!this.previewApplied || this.appliedRevision !== this.applyRevision));
    for (const selector of ["#studio-save", "#studio-export"]) {
      const button = requireElement<HTMLButtonElement>(selector);
      button.disabled = checking;
      button.title = checking ? "正在核验当前配置，完成后可保存或导出" : "";
    }
    this.syncSceneBackdropLabels();
    const unbound = this.config.fleet.filter(item => item.homeFacilityId === null);
    const counts = tf("studio.counts", { fleet: this.config.fleet.reduce((sum, item) => sum + item.count, 0), facilities: this.config.facilities.length, airspace: this.config.airspace.length });
    this.previewMetrics.textContent = this.basemapNote ? `${counts} · ${this.basemapNote}` : counts;
    if (this.previewError !== null) {
      this.previewStatus.textContent = t("studio.previewFailed");
      this.previewStatus.dataset.state = "error";
    } else if (this.draftError !== null && this.mapReady) {
      this.previewStatus.textContent = t("studio.needsFix");
      this.previewStatus.dataset.state = "warning";
    }
    const messages: string[] = [];
    if (this.initialStorageError !== null) messages.push(this.initialStorageError);
    if (this.draftError !== null) messages.push(`草稿校验：${this.draftError}。三维视图仍显示上次有效配置。`);
    if (this.previewError !== null) messages.push(this.previewError);
    if (this.operationError !== null && this.operationError !== this.previewError) messages.push(this.operationError);
    if (unbound.length && this.nativeReference === null) {
      messages.push(`${unbound.length} 个机群未绑定起降点。先在“设施与空域”放置起降点，再为机群指定驻地；草稿可继续编辑。`);
    }
    // The unbound-fleet summary above already states every missing home facility.
    for (const issue of this.previewIssues) {
      if (issue.code !== "missing_home") messages.push(`${issue.path}：${issue.message}`);
    }
    this.issuesRoot.replaceChildren(...messages.map(message => {
      const item = document.createElement("p");
      item.textContent = message;
      return item;
    }));
    this.issuesRoot.hidden = messages.length === 0;
    this.issuesRoot.dataset.state = this.draftError !== null || this.previewError !== null
      || this.operationError !== null ? "error" : "warning";
  }

  private checkedConfig(): CityWorkspaceConfig {
    if (this.spatial === null) throw new Error("场景几何资料尚未就绪，无法核验设施位置");
    const valid = parseCityWorkspaceConfig(this.config);
    const issues = validateStudioGeometry(valid, this.spatial.data);
    if (issues.length) throw new Error(issues.join("；"));
    const hasFlightPath = valid.facilities.length > 0 || valid.fleet.some(item => item.homeFacilityId !== null)
      || valid.stateKeyframes.length > 0;
    if (this.nativeReference === null && hasFlightPath
        && (!this.previewApplied || this.appliedRevision !== this.applyRevision)) {
      throw new Error("飞行路径预览尚未完成碰撞核验");
    }
    const collisions = this.collisionIssues();
    if (collisions.length) throw new Error(collisions.map(issue => `${issue.path}：${issue.message}`).join("；"));
    return valid;
  }

  private save(): void {
    if (this.authoringMode) {
      this.operationError = "选区场景与物流配置是只读来源；未写入任何选区文件。";
      this.renderSummary();
      return;
    }
    try {
      saveCityWorkspaceConfig(this.checkedConfig());
      this.initialStorageError = null;
      this.operationError = null;
      this.saveStatus.textContent = t("studio.saved");
      this.saveStatus.dataset.state = "saved";
      this.renderSummary();
    } catch (error) {
      this.operationError = `保存失败：${errorMessage(error)}`;
      this.saveStatus.textContent = this.operationError;
      this.saveStatus.dataset.state = "error";
      this.renderSummary();
    }
  }

  private export(): void {
    if (this.authoringMode) {
      this.operationError = "选区来源不从工作区工具栏导出；请在区域选取页导出选区任务身份。";
      this.renderSummary();
      return;
    }
    try {
      const content = JSON.stringify(this.checkedConfig(), null, 2) + "\n";
      const file = new Blob([content], { type: "application/json" });
      const url = URL.createObjectURL(file);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${this.config.name.trim().replace(/[^\p{L}\p{N}._-]+/gu, "-") || "city-workspace"}.json`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      this.saveStatus.textContent = "JSON 已下载";
      this.saveStatus.dataset.state = "saved";
      this.operationError = null;
      this.renderSummary();
    } catch (error) {
      this.operationError = `导出失败：${errorMessage(error)}`;
      this.saveStatus.textContent = this.operationError;
      this.saveStatus.dataset.state = "error";
      this.renderSummary();
    }
  }

  private async import(file: File): Promise<void> {
    if (this.authoringMode) {
      this.operationError = `${file.name} 未导入：选区来源只读；请返回工作区导入 v3 工作区 JSON。`;
      this.renderSummary();
      return;
    }
    const readRevision = ++this.importRevision;
    try {
      const text = await file.text();
      if (readRevision !== this.importRevision) return;
      await this.importText(file.name, text);
    } catch (error) {
      if (readRevision !== this.importRevision) return;
      this.operationError = `${file.name} 读取失败：${errorMessage(error)}。当前草稿与本地存储未改动。`;
      this.saveStatus.textContent = this.operationError;
      this.saveStatus.dataset.state = "error";
      this.renderSummary();
    }
  }

  private async applyScenePreset(preset: CityPreviewScenePreset): Promise<CityPreviewSceneApplyResult> {
    const draft = createCityPreviewSceneDraft(preset);
    return this.importText(`${preset.optionLabel} 地区预设`, JSON.stringify(draft));
  }

  /** The shared strict import/reload gate. It verifies the complete incoming
   * geometry before replacing the active in-memory draft; persistence still
   * waits for the ordinary three-dimensional preview gate. */
  private async importText(fileName: string, text: string): Promise<CityPreviewSceneApplyResult> {
    if (this.authoringMode) {
      const message = `${fileName} 未导入：选区来源只读；请返回工作区后重试。`;
      this.operationError = message;
      this.renderSummary();
      return { ok: false, message };
    }
    const revision = ++this.importRevision;
    this.pendingImport = null;
    this.operationError = null;
    this.saveStatus.textContent = `正在核验 ${fileName}`;
    this.saveStatus.dataset.state = "dirty";
    try {
      const incoming = parseCityWorkspaceConfig(JSON.parse(text) as unknown);
      let spatial = await loadSpatialSource(incoming.scenePath);
      if (incoming.scenePath === this.config.scenePath && this.mapReady && this.map !== null) {
        spatial = { ...spatial,
          data: { ...spatial.data, buildings: this.map.workspaceBuildingObstacles() } };
      }
      const issues = validateStudioGeometry(incoming, spatial.data);
      if (issues.length) throw new Error(issues.join("；"));
      if (revision !== this.importRevision) {
        return { ok: false, message: `${fileName} 已被较新的导入操作替代。` };
      }
      const changedScene = this.nativeReference !== null || incoming.scenePath !== this.config.scenePath;
      this.nativeReferenceRevision++;
      this.nativeReferenceAbort?.abort(); this.nativeReferenceAbort = null;
      this.nativeReference = null;
      this.config = incoming;
      this.spatial = spatial;
      this.draftError = null;
      this.previewError = null;
      this.previewIssues = [];
      this.previewApplied = false;
      this.pendingImport = { fileName, config: incoming };
      this.initialStorageError = null;
      this.nameInput.value = incoming.name;
      this.renderTab();
      if (changedScene) this.startMap();
      else this.requestApply();
      this.saveStatus.textContent = `${fileName} 已导入，等待预览核验`;
      this.saveStatus.dataset.state = "dirty";
      this.renderSummary();
      return { ok: true,
        message: `${fileName} 的场景清单与空间资料已核验；全新草稿已载入，等待三维预览门后保存。` };
    } catch (error) {
      if (revision !== this.importRevision) {
        return { ok: false, message: `${fileName} 已被较新的导入操作替代。` };
      }
      this.operationError = `${fileName} 核验失败：${errorMessage(error)}。当前草稿与本地存储未改动。`;
      this.saveStatus.textContent = this.operationError;
      this.saveStatus.dataset.state = "error";
      this.renderSummary();
      return { ok: false, message: this.operationError };
    }
  }
}

if (typeof document !== "undefined" && document.querySelector("#city-studio") !== null) {
  initLanguage();
  document.documentElement.lang = currentLanguage();
  new CityStudio();
}
