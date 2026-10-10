// @vitest-environment node
import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PublicTraceMap } from "./map";
import * as landscape from "./city-authored-landscape";
import { createSurfaceWetnessUniforms } from "./city-surface-wetness";
import { createVegetationWindUniforms } from "./city-vegetation-wind";
import { createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";

function polygon(minX: number, maxX: number) {
  return { outline: [[minX, 0], [maxX, 0], [maxX, 10], [minX, 10]] as const, holes: [] };
}

function fixture() {
  const previous = { group: new THREE.Group(), materials: [], dispose: vi.fn() };
  const note = { className: "", dataset: {} as Record<string, string>, textContent: "" };
  const map = {
    requireWorkspaceReady: vi.fn(),
    cityVegetationLayer: { authoredLandscapeGeometry: {
      displayedSurfaceSha256: "a".repeat(64), roadGeometry: "published" as const,
      extent: polygon(0, 10), roadbed: [polygon(0, 2)],
      walkbed: [polygon(2, 4)], buildings: [polygon(4, 6)],
    } },
    authoredLandscapeLayer: previous as landscape.CityAuthoredLandscapeRenderer | null,
    authoredLandscapeWind: createVegetationWindUniforms(), groundWetness: createSurfaceWetnessUniforms(),
    scene: new THREE.Scene(), trafficPreview: null,
    staticView: null,
    root: { dataset: {} as Record<string, string>, querySelectorAll: vi.fn(() => []),
      ownerDocument: { createElement: vi.fn(() => note) } },
    renderControls: { append: vi.fn() }, previewControls: { append: vi.fn() },
    localReflections: { invalidate: vi.fn() },
    renderStaticFrame: vi.fn(), renderPreviewFrame: vi.fn(),
    clearAuthoredLandscape() {
      (PublicTraceMap.prototype as unknown as { clearAuthoredLandscape(): void })
        .clearAuthoredLandscape.call(this);
    },
    prepareAuthoredLandscape(items: readonly landscape.CityAuthoredLandscapeItem[]) {
      return (PublicTraceMap.prototype as unknown as {
        prepareAuthoredLandscape(items: readonly landscape.CityAuthoredLandscapeItem[]): {
          plan: landscape.CityAuthoredLandscapePlan; renderer: landscape.CityAuthoredLandscapeRenderer;
        };
      }).prepareAuthoredLandscape.call(this, items);
    },
    mountAuthoredLandscape(plan: landscape.CityAuthoredLandscapePlan,
      renderer: landscape.CityAuthoredLandscapeRenderer) {
      (PublicTraceMap.prototype as unknown as {
        mountAuthoredLandscape(plan: landscape.CityAuthoredLandscapePlan,
          renderer: landscape.CityAuthoredLandscapeRenderer): void;
      }).mountAuthoredLandscape.call(this, plan, renderer);
    },
  };
  map.scene.add(previous.group);
  return { map, previous, note };
}

const item: landscape.CityAuthoredLandscapeItem = {
  id: "design-1", label: "Research courtyard", provenance: "authored", kind: "plaza",
  polygon: [{ x: 0, z: 0 }, { x: 10, z: 0 }, { x: 10, z: 10 }, { x: 0, z: 10 }],
};

beforeEach(() => vi.stubGlobal("Option", class {
  constructor(public text: string, public value: string) {}
}));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

function workspaceFixture() {
  const base = fixture();
  const config = createDefaultCityWorkspaceConfig();
  config.authoredLandscape = [item];
  const map = Object.assign(base.map, {
    destroyed: false, sourceKey: "default-pack", conversionGeneration: 1,
    loadedCityScenePath: config.scenePath, workspaceConfig: config,
    workspaceApplyGeneration: 0, workspaceOperationIssues: [], workspaceObstacles: [],
    workspaceFollowId: null, previewSeconds: 0, auditedTrafficSnapshot: null,
    validateWorkspaceSpatial: vi.fn(() => []),
    previewMoodButton: { hidden: false },
    previewFlightSelect: { setAttribute: vi.fn(), replaceChildren: vi.fn() },
    weather: { update: vi.fn() },
    operationsPreview: { group: new THREE.Group(), setViewCamera: vi.fn(),
      setConfig: vi.fn(async () => []), entityChoices: vi.fn(() => []), setHighDetailEntity: vi.fn() },
    localReflections: { configure: vi.fn(), invalidate: vi.fn() },
    setCityTimeOfDay: vi.fn(), setCityWeather: vi.fn(), updateSunIntensity: vi.fn(),
    syncWorkspaceRenderBar: vi.fn(), prepareWorkspaceVisuals: vi.fn(async () => undefined),
    syncPreviewAnimation: vi.fn(),
  });
  const apply = (next: CityWorkspaceConfig) =>
    PublicTraceMap.prototype.applyWorkspaceConfig.call(map as unknown as PublicTraceMap, next);
  return { ...base, map, config, apply };
}

describe("map authored landscape binding", () => {
  it("clips to the verified source geometry and replaces only the authored layer", () => {
    const { map, previous, note } = fixture();
    const next = { group: new THREE.Group(), materials: [], dispose: vi.fn() };
    const renderer = vi.spyOn(landscape, "createCityAuthoredLandscapeLayer").mockReturnValue(next);
    const plan = PublicTraceMap.prototype.applyAuthoredLandscape.call(map as unknown as PublicTraceMap, [item]);
    expect(plan.stats).toMatchObject({ sourceAreaM2: 100, drawnAreaM2: 40, removedAreaM2: 60 });
    expect(renderer).toHaveBeenCalledWith(plan, {
      wetness: map.groundWetness, wind: map.authoredLandscapeWind, surfaces: undefined,
    });
    expect(previous.dispose).toHaveBeenCalledOnce();
    expect(map.scene.children).toEqual([next.group]);
    expect(JSON.parse(map.root.dataset.cityAuthoredLandscape!)).toEqual(plan);
    expect(note.dataset.provenance).toBe("authored");
    expect(note.textContent).toContain("非 OSM 来源");
    expect(map.localReflections.invalidate).toHaveBeenCalledOnce();
    expect(map.renderStaticFrame).toHaveBeenCalledOnce();
    expect(map.renderPreviewFrame).not.toHaveBeenCalled();
  });

  it("retains the mounted design when a new input fails validation", () => {
    const { map, previous } = fixture();
    const renderer = vi.spyOn(landscape, "createCityAuthoredLandscapeLayer");
    expect(() => PublicTraceMap.prototype.applyAuthoredLandscape.call(map as unknown as PublicTraceMap,
      [{ ...item, provenance: "osm" } as unknown as landscape.CityAuthoredLandscapeItem]))
      .toThrow(/provenance/);
    expect(renderer).not.toHaveBeenCalled();
    expect(previous.dispose).not.toHaveBeenCalled();
    expect(map.authoredLandscapeLayer).toBe(previous);
  });

  it("rejects unavailable geometry instead of drawing against an unbound city", () => {
    const { map, previous } = fixture();
    expect(() => PublicTraceMap.prototype.applyAuthoredLandscape.call(
      { ...map, cityVegetationLayer: null } as unknown as PublicTraceMap, [item]))
      .toThrow(/verified road and environment/);
    expect(previous.dispose).not.toHaveBeenCalled();
  });

  it("clears every authored resource and provenance record when changing scenes", () => {
    const { map, previous } = fixture();
    const remove = vi.fn();
    map.root.querySelectorAll.mockReturnValue([ { remove } ] as never);
    map.root.dataset.cityAuthoredLandscape = "old plan";
    map.clearAuthoredLandscape();
    expect(previous.dispose).toHaveBeenCalledOnce();
    expect(map.scene.children).toEqual([]);
    expect(map.authoredLandscapeLayer).toBeNull();
    expect(map.root.dataset.cityAuthoredLandscape).toBeUndefined();
    expect(remove).toHaveBeenCalledOnce();
  });
});

describe("workspace authored landscape application", () => {
  it("mounts changed design after workspace preparation without inventing source classification", async () => {
    const { map, config, previous, apply } = workspaceFixture();
    const renderer = { group: new THREE.Group(), materials: [], dispose: vi.fn() };
    vi.spyOn(landscape, "createCityAuthoredLandscapeLayer").mockReturnValue(renderer);
    const changed = { ...config, authoredLandscape: [{ ...item, label: "Edited plaza" }] };
    await expect(apply(changed)).resolves.toEqual([]);
    expect(map.workspaceConfig.authoredLandscape).toEqual(changed.authoredLandscape);
    expect(map.authoredLandscapeLayer).toBe(renderer);
    expect(previous.dispose).toHaveBeenCalledOnce();
    expect(renderer.dispose).not.toHaveBeenCalled();
    expect(JSON.parse(map.root.dataset.cityAuthoredLandscape!).items[0].provenance).toBe("authored");
    expect(map.renderStaticFrame).toHaveBeenCalledOnce();
  });

  it("does not recreate unchanged geometry on another draft edit", async () => {
    const { map, config, previous, apply } = workspaceFixture();
    const create = vi.spyOn(landscape, "createCityAuthoredLandscapeLayer");
    await expect(apply({ ...config, name: "Renamed draft" })).resolves.toEqual([]);
    expect(create).not.toHaveBeenCalled();
    expect(previous.dispose).not.toHaveBeenCalled();
    expect(map.authoredLandscapeLayer).toBe(previous);
  });

  it("clears removed design without requiring unavailable source geometry", async () => {
    const { map, config, previous, apply } = workspaceFixture();
    Object.assign(map, { cityVegetationLayer: null });
    await expect(apply({ ...config, authoredLandscape: [] })).resolves.toEqual([]);
    expect(map.workspaceConfig.authoredLandscape).toEqual([]);
    expect(previous.dispose).toHaveBeenCalledOnce();
    expect(map.authoredLandscapeLayer).toBeNull();
    expect(map.root.dataset.cityAuthoredLandscape).toBeUndefined();
  });

  it("retains the old applied draft and layer when nonempty design has no verified geometry", async () => {
    const { map, config, previous, apply } = workspaceFixture();
    Object.assign(map, { cityVegetationLayer: null });
    await expect(apply({ ...config, authoredLandscape: [{ ...item, label: "New" }] }))
      .rejects.toThrow(/verified road and environment/);
    expect(map.workspaceConfig).toBe(config);
    expect(map.authoredLandscapeLayer).toBe(previous);
    expect(previous.dispose).not.toHaveBeenCalled();
    expect(map.operationsPreview.setConfig).not.toHaveBeenCalled();
  });

  it("disposes an unmounted candidate when a newer workspace application supersedes it", async () => {
    const { map, config, previous, apply } = workspaceFixture();
    const renderer = { group: new THREE.Group(), materials: [], dispose: vi.fn() };
    vi.spyOn(landscape, "createCityAuthoredLandscapeLayer").mockReturnValue(renderer);
    let finish!: () => void;
    const pending = new Promise<never[]>(resolve => { finish = () => resolve([]); });
    map.operationsPreview.setConfig.mockReturnValueOnce(pending);
    const next = { ...config, fleet: [], authoredLandscape: [{ ...item, label: "Superseded" }] };
    const application = apply(next);
    map.workspaceApplyGeneration++;
    finish();
    await application;
    expect(renderer.dispose).toHaveBeenCalledOnce();
    expect(previous.dispose).not.toHaveBeenCalled();
    expect(map.authoredLandscapeLayer).toBe(previous);
  });
});

describe("map terrain completion", () => {
  it("throws a specific error before vegetation is loaded", () => {
    expect(() => PublicTraceMap.prototype.proposeTerrainCompletion.call(
      { cityVegetationLayer: null } as unknown as PublicTraceMap, "green"))
      .toThrow("Terrain completion requires the loaded city vegetation layer");
    const available = Object.getOwnPropertyDescriptor(PublicTraceMap.prototype, "terrainCompletionAvailable")!.get!;
    expect(available.call({ cityVegetationLayer: null })).toBe(false);
  });

  it("uses source triangles and the current authored plan without mutating either", () => {
    const extent = { outline: [[0, 0], [40, 0], [40, 40], [0, 40]] as const, holes: [] };
    const source = [[[0, 0], [8, 0], [8, 40]], [[0, 0], [8, 40], [0, 40]]] as const;
    const authored = { id: "manual-plaza", triangles: [[[30, 0], [40, 0], [40, 40]], [[30, 0], [40, 40], [30, 40]]] as const };
    const layer = { authoredLandscapeGeometry: { extent, roadbed: [], walkbed: [], buildings: [] },
      occupiedSourceTriangles: source };
    const group = new THREE.Group();
    group.userData.authoredLandscapePlan = { items: [authored] };
    const before = JSON.stringify({ layer, authored });
    const result = PublicTraceMap.prototype.proposeTerrainCompletion.call({ cityVegetationLayer: layer,
      authoredLandscapeLayer: { group } } as unknown as PublicTraceMap, "plaza");
    expect(result.items[0]!.polygon).toEqual([
      { x: 10, z: 0 }, { x: 28, z: 0 }, { x: 28, z: 40 }, { x: 10, z: 40 },
    ]);
    expect(JSON.stringify({ layer, authored })).toBe(before);
  });

  it("regenerates identically after applying proposals, while manual items stay occupied", () => {
    const extent = { outline: [[0, 0], [40, 0], [40, 40], [0, 40]] as const, holes: [] };
    const layer = { authoredLandscapeGeometry: { extent, roadbed: [], walkbed: [], buildings: [] },
      occupiedSourceTriangles: [] };
    const group = new THREE.Group();
    const map = { cityVegetationLayer: layer, authoredLandscapeLayer: { group } };
    const propose = () => PublicTraceMap.prototype.proposeTerrainCompletion.call(
      map as unknown as PublicTraceMap, "green");
    const first = propose();
    // Applying the proposals mounts them in the plan; the next proposal of the
    // same kind must be identical instead of proposing into the leftover gaps.
    group.userData.authoredLandscapePlan = {
      items: first.items.map(item => ({ ...item, triangles: [item.polygon.map(point =>
        [point.x, point.z] as const)] })),
    };
    expect(propose()).toEqual(first);
    // A manual authored item (no completion id prefix) keeps blocking its area.
    group.userData.authoredLandscapePlan = { items: [{
      ...first.items[0]!, id: "hand-drawn-green",
      triangles: [first.items[0]!.polygon.map(point => [point.x, point.z] as const)],
    }] };
    const blocked = propose();
    expect(blocked.items.every(item2 => item2.id !== first.items[0]!.id)).toBe(true);
    expect(blocked.stats.residualCells).toBeLessThan(first.stats.residualCells);
  });

  it("passes the loaded kit to authored surfaces", () => {
    const { map } = fixture();
    const kit = { material: vi.fn(), attributes: vi.fn(), materials: [], dispose: vi.fn() };
    Object.assign(map.cityVegetationLayer, { terrainSurfaceKit: kit });
    const create = vi.spyOn(landscape, "createCityAuthoredLandscapeLayer")
      .mockReturnValue({ group: new THREE.Group(), materials: [], dispose: vi.fn() });
    map.prepareAuthoredLandscape([item]);
    expect(create.mock.lastCall![1].surfaces).toBe(kit);
  });
});
