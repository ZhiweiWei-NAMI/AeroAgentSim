import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HDRLoader } from "three/addons/loaders/HDRLoader.js";
import * as THREE from "three";
import { makeDaySkyTexture, meshMaterial, PublicTraceMap, trajectorySegments } from "./map";
import { createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { CITY_WEATHER_CLEAR } from "./city-weather";
import type { LoadedMeshPack } from "./osm2world/pack-loader";
import type { PublicTrajectory, ResolvedPose } from "./generated/aero-bench-contracts";
import { pose } from "./testing/trace-v3-fixture";
import type { O2WMesh } from "./osm2world/runtime";
import * as sceneLoader from "./city-scene-config";
import * as packLoader from "./osm2world/pack-loader";
import * as presentationLoader from "./city-presentation";
import * as renderLoader from "./city-building-renders";
import * as sourceContextLoader from "./city-building-source-context";

const frontendRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

function stubMesh(overrides: Partial<{ base: string | null; normal: string | null; color: [number, number, number] }> = {}): O2WMesh {
  return {
    elementId: () => "w1",
    modelClass: () => "Building",
    materialName: () => "BUILDING_DEFAULT",
    positions: () => [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: () => [0, 1, 2],
    normals: () => [0, 1, 0, 0, 1, 0, 0, 1, 0],
    uvs: () => [0, 0, 1, 0, 0, 1],
    color: () => overrides.color ?? [1, 0.9, 0.7],
    baseColorTexture: () => overrides.base ?? "./textures/cc0textures/Plaster002/Plaster002_Color.jpg",
    opacityTexture: () => null,
    normalTexture: () => overrides.normal ?? null,
    ormTexture: () => null,
    transparency: () => false,
    clampTextures: () => false,
  };
}

beforeEach(() => {
  vi.spyOn(THREE.TextureLoader.prototype, "load").mockImplementation(() => new THREE.Texture());
  vi.spyOn(HDRLoader.prototype, "load").mockImplementation(() => new THREE.DataTexture());
});

describe("OSM2World map renderer", () => {
  it("animates visual weather without inventing a traffic replay", () => {
    const request = vi.fn(() => 7);
    vi.stubGlobal("requestAnimationFrame", request);
    vi.spyOn(performance, "now").mockReturnValue(2000);
    const map = { previewAnimation: 0, destroyed: false, sourceKey: "default-pack", trafficPreview: null,
      renderSceneActive: true, weather: { requiresAnimation: true }, previewPlaying: true,
      cityVegetationLayer: null, cityWeatherSettings: CITY_WEATHER_CLEAR,
      previewSeconds: 34, previewLastFrameAt: 1000, previewPlaybackRate: 1,
      root: { dataset: {} as Record<string, string> }, renderStaticFrame: vi.fn(), renderPreviewFrame: vi.fn() };
    try {
      (PublicTraceMap.prototype as unknown as { animatePreview(): void }).animatePreview.call(map);
      expect(map.previewSeconds).toBe(35);
      expect(map.renderStaticFrame).toHaveBeenCalledOnce();
      expect(map.renderPreviewFrame).not.toHaveBeenCalled();
      expect(map.root.dataset.visualWeatherTimeS).toBe("35.000");
      expect(request).toHaveBeenCalledOnce();
    } finally { vi.unstubAllGlobals(); vi.restoreAllMocks(); }
  });

  it("keeps one weather animation loop and cancels it for clear weather or pause", () => {
    const request = vi.fn(() => 7), cancel = vi.fn();
    vi.stubGlobal("requestAnimationFrame", request); vi.stubGlobal("cancelAnimationFrame", cancel);
    const map = { destroyed: false, sourceKey: "default-pack", trafficPreview: null,
      renderSceneActive: true, weather: { requiresAnimation: true }, previewPlaying: true,
      cityVegetationLayer: null as object | null, cityWeatherSettings: CITY_WEATHER_CLEAR,
      previewAnimation: 0, previewLastFrameAt: 0, root: { dataset: {} },
      renderWeatherPause: { disabled: false, textContent: "" }, animatePreview: vi.fn() };
    const sync = (PublicTraceMap.prototype as unknown as { syncPreviewAnimation(): void }).syncPreviewAnimation;
    try {
      sync.call(map); sync.call(map);
      expect(request).toHaveBeenCalledOnce();
      map.previewPlaying = false; sync.call(map);
      expect(cancel).toHaveBeenCalledWith(7);
      expect(map.previewAnimation).toBe(0);
      map.weather.requiresAnimation = false; sync.call(map);
      expect(map.renderWeatherPause.disabled).toBe(true);
      // Wind alone animates a scene that carries a vegetation layer.
      map.cityVegetationLayer = {}; map.cityWeatherSettings = { ...CITY_WEATHER_CLEAR, windMps: 4 };
      sync.call(map);
      expect(map.renderWeatherPause.disabled).toBe(false);
    } finally { vi.unstubAllGlobals(); }
  });

  it("hosts workspace authoring on a loaded render scene without a traffic replay", () => {
    const requireReady = PublicTraceMap.prototype as unknown as { requireWorkspaceReady(): void };
    const base = { destroyed: false, sourceKey: "default-pack", trafficPreview: null,
      renderSceneActive: true, buildingPresentation: {}, weather: {}, localReflections: {},
      loadedCityScenePath: "/city-presentation/default-scene-v1.json",
      root: { dataset: { sceneReady: "true" } } };
    expect(() => requireReady.requireWorkspaceReady.call(base)).not.toThrow();
    expect(() => requireReady.requireWorkspaceReady.call({
      ...base, buildingPresentation: null })).toThrow(/render scene/);
    expect(() => requireReady.requireWorkspaceReady.call({
      ...base, root: { dataset: {} } })).toThrow(/not ready/);
    expect(() => requireReady.requireWorkspaceReady.call({
      ...base, renderSceneActive: false })).toThrow(/render scene/);
  });

  it("applies a workspace draft to a render scene through the static frame path", async () => {
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("Option", class { constructor(public text: string, public value: string) {} });
    try {
      const config = createDefaultCityWorkspaceConfig();
      config.environment.timeOfDay = "night";
      const map = {
        destroyed: false, sourceKey: "default-pack", trafficPreview: null, renderSceneActive: true,
        root: { dataset: {} as Record<string, string> },
        workspaceConfig: null as CityWorkspaceConfig | null,
        authoredLandscapeLayer: null,
        workspaceOperationIssues: [] as { path: string; code: string; message: string }[],
        workspaceApplyGeneration: 0,
        workspaceFollowId: null, workspaceObstacles: [] as never[], conversionGeneration: 1,
        loadedCityScenePath: "/city-presentation/default-scene-v1.json",
        previewFollowId: null, previewMoodButton: { hidden: false },
        previewFlightSelect: { setAttribute: vi.fn(), replaceChildren: vi.fn() },
        weather: { update: vi.fn(), requiresAnimation: false },
        localReflections: { configure: vi.fn(), invalidate: vi.fn() },
        operationsPreview: { group: { children: [] }, setViewCamera: vi.fn(),
          setConfig: vi.fn(async () => []), entityChoices: vi.fn(() => []),
          setHighDetailEntity: vi.fn() },
        setCityTimeOfDay: vi.fn(), setCityWeather: vi.fn(), updateSunIntensity: vi.fn(),
        renderStaticFrame: vi.fn(), renderPreviewFrame: vi.fn(),
        syncPreviewAnimation: vi.fn(), syncWorkspaceRenderBar: vi.fn(),
        requireWorkspaceReady: vi.fn(), validateWorkspaceSpatial: vi.fn(() => []),
        prepareWorkspaceVisuals: vi.fn(async () => undefined),
      };
      const apply = PublicTraceMap.prototype as unknown as {
        applyWorkspaceConfig(config: CityWorkspaceConfig): Promise<never[]>;
      };
      const issues = await apply.applyWorkspaceConfig.call(map, config);
      expect(issues).toEqual([]);
      expect(map.requireWorkspaceReady).toHaveBeenCalledOnce();
      expect(map.setCityTimeOfDay).toHaveBeenCalledWith("night", false);
      expect(map.setCityWeather).toHaveBeenCalledWith(expect.objectContaining({
        timeOfDay: "night", precipitation: "none", reflectionsEnabled: true }));
      expect(map.renderStaticFrame).toHaveBeenCalledOnce();
      expect(map.renderPreviewFrame).not.toHaveBeenCalled();
      expect(map.syncWorkspaceRenderBar).toHaveBeenCalledOnce();
      expect(map.previewMoodButton.hidden).toBe(true);
      expect(map.workspaceConfig?.environment.timeOfDay).toBe("night");
    } finally { vi.unstubAllGlobals(); }
  });

  it("rejects non-zero traffic demand on a render scene with no SUMO replay", async () => {
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    vi.stubGlobal("Option", class { constructor(public text: string, public value: string) {} });
    try {
      const config = createDefaultCityWorkspaceConfig();
      config.traffic = { vehicles: 2, bicycles: 0, pedestrians: 0 };
      const map = {
        destroyed: false, sourceKey: "default-pack", trafficPreview: null, renderSceneActive: true,
        root: { dataset: {} as Record<string, string> },
        workspaceConfig: null as CityWorkspaceConfig | null,
        workspaceOperationIssues: [] as { path: string; code: string; message: string }[],
        workspaceApplyGeneration: 0,
        loadedCityScenePath: "/city-presentation/default-scene-v1.json",
        requireWorkspaceReady: vi.fn(), validateWorkspaceSpatial: vi.fn(() => []),
      };
      const apply = PublicTraceMap.prototype as unknown as {
        applyWorkspaceConfig(config: CityWorkspaceConfig): Promise<
          { path: string; code: string; message: string }[]>;
      };
      const issues = await apply.applyWorkspaceConfig.call(map, config);
      expect(issues).toHaveLength(1);
      expect(issues[0]!.message).toContain("SUMO");
      expect(issues[0]).toMatchObject({ path: "traffic", code: "traffic-replay-unavailable" });
      expect(map.workspaceConfig).toBeNull();
    } finally { vi.unstubAllGlobals(); }
  });

  it("forwards render-bar edits of an applied draft to the draft owner without applying them itself", () => {
    const config = createDefaultCityWorkspaceConfig();
    const onEnvironmentEdit = vi.fn();
    const map = { workspaceConfig: config, callbacks: { onEnvironmentEdit }, applyWorkspaceConfig: vi.fn() };
    const apply = PublicTraceMap.prototype as unknown as {
      applyWorkspaceEnvironment(environment: CityWorkspaceConfig["environment"]): void;
    };
    const next = { ...config.environment, timeOfDay: "night" as const };
    apply.applyWorkspaceEnvironment.call(map, next);
    expect(onEnvironmentEdit).toHaveBeenCalledWith(next);
    expect(onEnvironmentEdit.mock.lastCall![0]).not.toBe(next);
    expect(map.applyWorkspaceConfig).not.toHaveBeenCalled();
    expect(config.environment.timeOfDay).toBe("day");
    expect(() => apply.applyWorkspaceEnvironment.call({ workspaceConfig: config, callbacks: {} }, next))
      .toThrow(/onEnvironmentEdit owner/);
  });

  it("labels the render bar as a workspace draft edit only while a draft is applied", () => {
    const sync = PublicTraceMap.prototype as unknown as { syncWorkspaceRenderBar(): void };
    const note = { textContent: "", dataset: {} as Record<string, string> };
    const element = { querySelector: vi.fn(() => note), setAttribute: vi.fn() };
    sync.syncWorkspaceRenderBar.call({ workspaceConfig: null, renderWeatherControls: { element } });
    expect(note.textContent).toBe("未保存的视觉预览");
    expect(note.dataset.mode).toBe("preview");
    expect(element.setAttribute).toHaveBeenLastCalledWith("aria-label", "视觉天气预览（不保存为仿真配置）");
    sync.syncWorkspaceRenderBar.call({ workspaceConfig: {}, renderWeatherControls: { element } });
    expect(note.textContent).toBe("草稿环境 · 修改计入工作区草稿");
    expect(note.dataset.mode).toBe("workspace");
    expect(element.setAttribute).toHaveBeenLastCalledWith("aria-label", "草稿环境编辑（修改计入工作区草稿，需在工作台保存）");
    expect(() => sync.syncWorkspaceRenderBar.call({ workspaceConfig: null, renderWeatherControls: null }))
      .not.toThrow();
  });

  it("releases completed trees when the source context fails after them", async () => {
    const config = sceneLoader.parseCitySceneConfig(JSON.parse(readFileSync(join(frontendRoot,
      "public/city-presentation/default-scene-v1.json"), "utf8")));
    const pack = { manifest: {}, batches: [{ batch: { layer: "buildings", material: { base_color_texture: null },
      ranges: [{ target: { kind: "building", id: "w1" }, end: 1 }] }, arrays: {
        positions: new Float32Array([0, 0, 0, 10, 0, 0, 0, 10, 0]),
        indices: new Uint32Array([0, 1, 2]),
      } }], dispose: vi.fn() } as unknown as LoadedMeshPack;
    const trees = new THREE.Group();
    const treeGeometry = new THREE.BoxGeometry(), treeMaterial = new THREE.MeshStandardMaterial();
    trees.add(new THREE.Mesh(treeGeometry, treeMaterial));
    const geometryDispose = vi.spyOn(treeGeometry, "dispose"), materialDispose = vi.spyOn(treeMaterial, "dispose");
    let contextReject!: (error: Error) => void;
    const contextPending = new Promise<never>((_resolve, reject) => { contextReject = reject; });
    let treesReady!: () => void;
    const completedTrees = new Promise<void>(resolve => { treesReady = resolve; });
    vi.spyOn(sceneLoader, "loadCitySceneConfig").mockResolvedValue(config);
    vi.spyOn(packLoader, "loadMeshPack").mockResolvedValue(pack);
    vi.spyOn(presentationLoader, "loadCityTrees").mockImplementation(async () => { treesReady(); return trees; });
    vi.spyOn(renderLoader, "fetchBuildingRenderManifest").mockResolvedValue({} as never);
    vi.spyOn(sourceContextLoader, "fetchBuildingRenderSourceContext").mockReturnValue(contextPending);
    vi.stubGlobal("window", { location: { href: "http://localhost/", search: "" } });
    const map = { destroyed: false, conversionGeneration: 1, updateSceneLoading: vi.fn(),
      failSceneLoading: vi.fn(), root: { dataset: {} }, callbacks: {}, sourceKey: "default-pack",
      renderStreamer: null, renderResolver: null, packedSceneLoadAbort: null,
      buildingPresentation: null, vegetationPresentation: null, roadPresentation: null,
      staticSignalPresentation: null, trafficPreview: null, packedScene: null, packResolver: null };
    const method = PublicTraceMap.prototype as unknown as { loadPackedScene(generation: number): Promise<void> };
    try {
      const pending = method.loadPackedScene.call(map, 1);
      await completedTrees;
      await Promise.resolve();
      contextReject(new Error("Source context digest failed"));
      await pending;
      expect(geometryDispose).toHaveBeenCalledTimes(1);
      expect(materialDispose).toHaveBeenCalledTimes(1);
      expect(trees.children).toHaveLength(0);
      expect(pack.dispose).toHaveBeenCalledTimes(1);
      expect(map.renderStreamer).toBeNull();
      expect(map.vegetationPresentation).toBeNull();
      expect(map.failSceneLoading).toHaveBeenCalledWith("Source context digest failed");
    } finally {
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    }
  });

  it("keeps the current render resources when an older scene load fails late", async () => {
    const oldPack = { manifest: {}, batches: [], dispose: vi.fn() } as unknown as LoadedMeshPack;
    const latestStreamer = { dispose: vi.fn() }, latestResolver = { dispose: vi.fn() };
    const latestLoad = new AbortController();
    const map = { destroyed: false, conversionGeneration: 1, updateSceneLoading: vi.fn(),
      renderStreamer: null as typeof latestStreamer | null, renderResolver: null as typeof latestResolver | null,
      packedSceneLoadAbort: null as AbortController | null };
    const method = PublicTraceMap.prototype as unknown as {
      loadPackedScene(generation: number, pack: LoadedMeshPack): Promise<void>;
    };
    const pending = method.loadPackedScene.call(map, 1, oldPack);
    map.conversionGeneration = 2;
    map.renderStreamer = latestStreamer; map.renderResolver = latestResolver;
    map.packedSceneLoadAbort = latestLoad;
    await pending;
    expect(oldPack.dispose).toHaveBeenCalledTimes(1);
    expect(latestStreamer.dispose).not.toHaveBeenCalled();
    expect(latestResolver.dispose).not.toHaveBeenCalled();
    expect(map.renderStreamer).toBe(latestStreamer);
    expect(map.renderResolver).toBe(latestResolver);
    expect(map.packedSceneLoadAbort).toBe(latestLoad);
    expect(latestLoad.signal.aborted).toBe(false);
  });

  it("builds the daylight sky used for the official OSM2World look", () => {
    const texture = makeDaySkyTexture();
    expect(texture).toBeInstanceOf(THREE.Texture);
    expect(texture.mapping).toBe(THREE.EquirectangularReflectionMapping);
    expect(texture.colorSpace).toBe(THREE.SRGBColorSpace);
    texture.dispose();
  });

  it("applies OSM2World mesh color and offline plaster texture onto the shipped material", () => {
    const material = meshMaterial(stubMesh(), () => undefined);
    expect(material).toBeInstanceOf(THREE.MeshStandardMaterial);
    expect(material.map).not.toBeNull();
    expect(material.color.r).toBe(1);
    const expected = new THREE.Color().setRGB(1, 0.9, 0.7, THREE.SRGBColorSpace);
    expect(material.color.g).toBeCloseTo(expected.g);
    expect(material.color.b).toBeCloseTo(expected.b);
    expect(material.map?.flipY).toBe(false);
    expect(material.side).toBe(THREE.FrontSide);
    material.dispose();
  });

  it("keeps missing trajectory ticks disconnected and excludes future samples", () => {
    const trajectory: PublicTrajectory = {
      entity_id: "uav.inspector",
      samples: [1, 2, 5, 6].map(tick => ({
        at: { tick, sim_time_ns: tick * 500_000_000 },
        pose: pose() as unknown as ResolvedPose,
        sample_digest: "a".repeat(64),
      })) as PublicTrajectory["samples"],
    };
    const ticks = (limit: number | null) => trajectorySegments(trajectory, limit).map(pair => pair.map(sample => sample.at.tick));
    expect(ticks(null)).toEqual([[1, 2], [5, 6]]);
    expect(ticks(5)).toEqual([[1, 2]]);
    expect(ticks(1)).toEqual([]);
  });

  it("has deleted the old custom ENU city-plan source", () => {
    expect(existsSync(join(frontendRoot, "public/osm2world/shanghai-hongqiao.osm2world.json"))).toBe(false);
    expect(existsSync(join(frontendRoot, "src/geometry.ts"))).toBe(false);
    expect(existsSync(join(frontendRoot, "public/scenes/shanghai-hongqiao.city.json"))).toBe(false);
  });
});
