// @vitest-environment node
import * as THREE from "three";
import { describe, expect, it, vi } from "vitest";
import { PublicTraceMap, type MapScene, type MapView } from "./map";
import type { LoadedNativeCityPresentation } from "./native-city-presentation";
import type { PublicScenario, ResolvedCoordinate } from "./generated/aero-bench-contracts";
import { coordinate, scenario } from "./testing/trace-v3-fixture";

const view: MapView = {
  layers: { imagery: true, terrain: true, buildings: true, roads: true, weather: true,
    regions: true, uav: true, ugv: true, pedestrian: true, static_assets: true,
    trajectories: true, network_links: true },
  hiddenEntities: new Set(), hiddenTrajectories: new Set(), isolate: null, selected: null, hovered: null,
};

function fixture() {
  const source = scenario() as unknown as PublicScenario;
  const buildings = new THREE.Group();
  const building = new THREE.Group();
  building.userData = { target: { kind: "building", id: "building.unit" }, entityId: "building.entity" };
  building.add(new THREE.Mesh(new THREE.BoxGeometry(2, 3, 2), new THREE.MeshStandardMaterial()));
  buildings.add(building);
  const native: LoadedNativeCityPresentation = {
    group: new THREE.Group(),
    layers: { buildings, roads: new THREE.Group(), fixtures: new THREE.Group(), regions: new THREE.Group() },
    bounds: new THREE.Box3(new THREE.Vector3(-10, 0, -20), new THREE.Vector3(30, 40, 50)),
    stats: { buildingCount: 1, roadbedPolygonCount: 1, walkbedPolygonCount: 1,
      fixtureCount: 1, omittedFixtureInteriorRingCount: 0, greenCount: 1, groundCoverCount: 1,
      osmElementCount: 1, verifiedBytes: 100 },
    setLayerVisibility: vi.fn(), dispose: vi.fn(),
  };
  native.group.userData.scenarioDigest = source.scenario_digest;
  native.group.userData.worldDigest = source.world_digest;
  native.group.add(buildings, native.layers.roads, native.layers.fixtures, native.layers.regions);
  const map = {
    destroyed: false, sourceKey: "", conversionGeneration: 1,
    nativePresentation: null as LoadedNativeCityPresentation | null,
    root: { dataset: {} as Record<string, string> }, scene: new THREE.Scene(),
    sceneLoading: { hidden: false }, cityEnvelope: new THREE.Box3(),
    projectionOrigin: { latitude_deg: 0, longitude_deg: 0 },
    dynamic: new Map<string, THREE.Object3D>(), signals: new Map(),
    callbacks: { onSceneStatus: vi.fn(), onBasemapNote: vi.fn() },
    frameExtent: vi.fn(), addScenarioOverlay: vi.fn(), loadPackedScene: vi.fn(),
    renderObservation: vi.fn(), renderer: { render: vi.fn() }, camera: new THREE.Camera(),
    applyStaticView: vi.fn(), entityVisible: vi.fn(() => true),
    updateTrafficLights: vi.fn(), updateCamera: vi.fn(), renderTrajectories: vi.fn(), renderNetwork: vi.fn(),
    clearScene: vi.fn(function () {
      if (map.nativePresentation !== null) map.scene.remove(map.nativePresentation.group);
      map.nativePresentation = null; map.dynamic.clear();
    }),
  };
  const input: MapScene = { scenario: source, nativePresentation: native,
    sceneState: null, trajectories: [], networkFrame: null };
  const render = (current = input) => PublicTraceMap.prototype.render.call(map as unknown as PublicTraceMap, current, view);
  return { map, native, source, building, input, render };
}

describe("map declared native city route", () => {
  it("mounts the verified group and records ready without loading a foreign mesh pack", () => {
    const { map, native, source, building, render } = fixture();
    render();
    expect(map.nativePresentation).toBe(native);
    expect(map.scene.children).toContain(native.group);
    expect(map.dynamic.get("entity:building.entity")).toBe(building);
    expect(map.addScenarioOverlay).toHaveBeenCalledWith(source, true);
    expect(map.loadPackedScene).not.toHaveBeenCalled();
    expect(map.root.dataset.sceneReady).toBe("true");
    expect(map.root.dataset.nativeCityPresentation).toBe("verified-declared-assets");
    expect(map.frameExtent).toHaveBeenCalledWith({ west: -10, east: 30, south: -50, north: 20 });
    expect(map.callbacks.onSceneStatus).toHaveBeenCalledExactlyOnceWith(source.scenario_digest, "ready");
    render();
    expect(map.clearScene).toHaveBeenCalledOnce();
    expect(native.dispose).not.toHaveBeenCalled();
  });

  it("leaves presentation loading progress to the application while no presentation is bound", () => {
    const { map, input, render } = fixture();
    render({ ...input, nativePresentation: null });
    expect(map.sourceKey).toBe("awaiting-pack");
    expect(map.sceneLoading.hidden).toBe(true);
    expect(map.root.dataset.sceneReady).toBe("false");
    expect(map.renderObservation).toHaveBeenCalled();
  });

  it("rejects a mismatched presentation before replacing the active scene", () => {
    const { map, native, render } = fixture();
    native.group.userData.worldDigest = "f".repeat(64);
    expect(render).toThrow("identity differs");
    expect(map.clearScene).not.toHaveBeenCalled();
    expect(map.scene.children).toEqual([]);
    expect(map.callbacks.onSceneStatus).not.toHaveBeenCalled();
  });

  it("rejects simultaneous mesh-pack and native-city routes", () => {
    const { input, render, map } = fixture();
    expect(() => render({ ...input, pack: {} as NonNullable<MapScene["pack"]> })).toThrow("exactly one");
    expect(map.clearScene).not.toHaveBeenCalled();
  });

  it("exposes a missing public scenario instead of falling back to the ordinary city", () => {
    const { input, render, map } = fixture();
    expect(() => render({ ...input, scenario: null })).toThrow("requires its declared public scenario");
    expect(map.clearScene).not.toHaveBeenCalled();
    expect(map.loadPackedScene).not.toHaveBeenCalled();
  });

  it("replaces a same-scenario group when the caller has loaded a new generation", () => {
    const { input, map, native, render } = fixture(); render();
    const group = new THREE.Group(); group.userData = { ...native.group.userData };
    const next = { ...native, group };
    render({ ...input, nativePresentation: next });
    expect(map.clearScene).toHaveBeenCalledTimes(2);
    expect(map.scene.children).toEqual([group]);
    expect(map.nativePresentation).toBe(next);
    expect(native.dispose).not.toHaveBeenCalled();
  });

  it("uses exact recorded ENU for native state rather than reprojecting geographic coordinates", () => {
    const { map, native } = fixture(); map.nativePresentation = native;
    const resolved = coordinate({ enu: { east_m: 7.25, north_m: -3.5, up_m: 19.75 } }) as unknown as ResolvedCoordinate;
    const method = PublicTraceMap.prototype as unknown as { position(value: ResolvedCoordinate, lift?: number): THREE.Vector3 };
    expect(method.position.call(map, resolved, 0.4).toArray()).toEqual([7.25, 20.15, 3.5]);
  });

  it("detaches borrowed native resources without disposing the caller's assets", () => {
    const { map, native } = fixture(); map.nativePresentation = native; map.scene.add(native.group);
    map.root.dataset.nativeCityPresentation = "verified-declared-assets";
    map.root.dataset.nativeCityStats = "old stats";
    const method = PublicTraceMap.prototype as unknown as { unmountNativePresentation(): void };
    method.unmountNativePresentation.call(map);
    expect(map.scene.children).toEqual([]);
    expect(map.nativePresentation).toBeNull();
    expect(map.root.dataset.nativeCityStats).toBeUndefined();
    expect(native.dispose).not.toHaveBeenCalled();
  });
});

describe("map frameP02Overview", () => {
  function frameRig() {
    const setFollow = vi.fn();
    const renderObservation = vi.fn();
    const rig = {
      destroyed: false,
      nativePresentation: {},
      position: (value: ResolvedCoordinate) => new THREE.Vector3(value.enu.east_m, value.enu.up_m, -value.enu.north_m),
      observationScene: { sceneState: { samples: [{ pose: { position: coordinate({ enu: { east_m: -450, north_m: -437, up_m: 0.1 } }) } }] } },
      root: { dataset: {} },
      controls: { target: new THREE.Vector3(99, 99, 99), update: vi.fn() },
      camera: new THREE.PerspectiveCamera(60, 1.6, 0.1, 4000),
      setFollow,
      renderObservation,
    };
    const frame = PublicTraceMap.prototype.frameP02Overview;
    return { rig, setFollow, renderObservation, frame: frame.bind(rig as unknown as PublicTraceMap) };
  }

  it("converts declared frame-authority ENU metres with the identity used by every recorded state", () => {
    const { rig, setFollow, renderObservation, frame } = frameRig();
    const ok = frame({
      position: { east: -510, north: -450, up: 20.1 },
      target: { east: -450, north: -437, up: 0.1 },
    });
    expect(ok).toBe(true);
    // enuPosition identity: world = (east, up, -north). No offset, no shift.
    expect(rig.camera.position.toArray()).toEqual([-510, 20.1, 450]);
    expect(rig.controls.target.toArray()).toEqual([-450, 0.1, 437]);
    // The overview never engages following; it is a plain free-camera frame.
    expect(setFollow).toHaveBeenCalledWith(null);
    expect(renderObservation).toHaveBeenCalled();
  });

  it("is a no-op for a null pose or non-finite coordinates", () => {
    const { rig, setFollow, renderObservation, frame } = frameRig();
    expect(frame(null)).toBe(false);
    expect(frame({
      position: { east: Number.NaN, north: 0, up: 0 },
      target: { east: 0, north: 0, up: 0 },
    })).toBe(false);
    expect(rig.controls.target.toArray()).toEqual([99, 99, 99]);
    expect(setFollow).not.toHaveBeenCalled();
    expect(renderObservation).not.toHaveBeenCalled();
  });
});
