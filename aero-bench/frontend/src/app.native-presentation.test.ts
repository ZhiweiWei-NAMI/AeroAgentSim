import * as THREE from "three";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn(); focus = vi.fn(() => true); setFollow = vi.fn();
  refreshCursorLabels = vi.fn(); destroy = vi.fn();
} }));
vi.mock("./native-city-presentation", () => ({
  inspectNativeCityPresentation: vi.fn(), loadNativeCityPresentation: vi.fn(),
}));
vi.mock("./osm2world/assets", () => ({ loadScenarioMeshPack: vi.fn() }));

import { PublicTraceApp } from "./app";
import { inspectNativeCityPresentation, loadNativeCityPresentation,
  type LoadedNativeCityPresentation, type NativeCityPresentationLoadOptions,
  type NativeCityPresentationPlan } from "./native-city-presentation";
import { loadScenarioMeshPack } from "./osm2world/assets";
import type { PublicScenario } from "./generated/aero-bench-contracts";
import type { PublicTrace } from "./trace";
import { publicTrace } from "./testing/trace-v3-fixture";

interface AppBoundary {
  nativePresentation: LoadedNativeCityPresentation | null;
  nativePresentationAbort: AbortController | null;
  assetResolutions: readonly { assetId: string; state: string }[];
  liveAssetScenario: string;
  session: { publicAsset: ReturnType<typeof vi.fn>; dispose: ReturnType<typeof vi.fn> } | null;
  resolveDeclaredAssets(trace: Readonly<PublicTrace>, baseHref: string): Promise<void>;
  resolveLiveScene(scenario: PublicScenario): Promise<void>;
  resetAssetResolution(): void;
}

let app: PublicTraceApp;
let state: AppBoundary;
let root: HTMLElement;
let trace: PublicTrace;
let plan: NativeCityPresentationPlan;

function presentation(): LoadedNativeCityPresentation {
  const group = new THREE.Group();
  return {
    group,
    layers: { buildings: new THREE.Group(), roads: new THREE.Group(),
      fixtures: new THREE.Group(), regions: new THREE.Group() },
    bounds: new THREE.Box3(new THREE.Vector3(-10, 0, -10), new THREE.Vector3(10, 20, 10)),
    stats: { buildingCount: 1, roadbedPolygonCount: 1, walkbedPolygonCount: 1,
      fixtureCount: 1, omittedFixtureInteriorRingCount: 0, greenCount: 1, groundCoverCount: 1,
      osmElementCount: 1, verifiedBytes: 1 },
    setLayerVisibility: vi.fn(), dispose: vi.fn(),
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  document.body.replaceChildren();
  root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "replay"); state = app as unknown as AppBoundary;
  trace = publicTrace() as unknown as PublicTrace;
  const renderAsset = { ...trace.scenario.assets[0]!, asset_id: "fixture.building.render",
    media_type: "model/gltf-binary" };
  trace.scenario.assets = [renderAsset];
  // This unit-only binding exercises integration ownership, not native geometry or formal evidence.
  trace.scenario.buildings = [{ render_asset_id: renderAsset.asset_id }] as PublicScenario["buildings"];
  plan = { kind: "native-city-assets", scenarioDigest: trace.scenario.scenario_digest,
    buildings: [{ asset: renderAsset }] } as unknown as NativeCityPresentationPlan;
  vi.mocked(inspectNativeCityPresentation).mockReturnValue({ kind: "native-city", plan });
});
afterEach(() => { app.dispose(); vi.unstubAllGlobals(); });

describe("native city App asset ownership", () => {
  it("loads declared native assets without fabricating a mesh pack or refetching building models", async () => {
    const native = presentation();
    vi.mocked(loadNativeCityPresentation).mockResolvedValue(native);
    const network = vi.fn(() => { throw new Error("already verified building must not be refetched"); });
    vi.stubGlobal("fetch", network);
    await state.resolveDeclaredAssets(trace, "./replay/");
    expect(state.nativePresentation).toBe(native);
    expect(loadScenarioMeshPack).not.toHaveBeenCalled();
    expect(loadNativeCityPresentation).toHaveBeenCalledWith(plan, expect.objectContaining({
      baseHref: new URL("./replay/", window.location.href).href, signal: expect.any(AbortSignal),
    }));
    expect(state.assetResolutions).toEqual([expect.objectContaining({
      assetId: "fixture.building.render", state: "resolved",
    })]);
    expect(network).not.toHaveBeenCalled();
    state.resetAssetResolution();
    expect(native.dispose).toHaveBeenCalledOnce();
    expect(state.nativePresentation).toBeNull();
  });

  it("aborts superseded work and disposes its late result without replacing the current city", async () => {
    let finish!: (value: LoadedNativeCityPresentation) => void;
    let oldSignal!: AbortSignal;
    const old = presentation(), current = presentation();
    vi.mocked(loadNativeCityPresentation)
      .mockImplementationOnce((_plan, options) => {
        oldSignal = options.signal!;
        return new Promise(resolve => { finish = resolve; });
      }).mockResolvedValueOnce(current);
    const pending = state.resolveDeclaredAssets(trace, "./replay/");
    await state.resolveDeclaredAssets(trace, "./replay/");
    expect(oldSignal.aborted).toBe(true);
    finish(old); await pending;
    expect(old.dispose).toHaveBeenCalledOnce();
    expect(current.dispose).not.toHaveBeenCalled();
    expect(state.nativePresentation).toBe(current);
  });

  it("does not publish a failed native loader result or use the mesh route", async () => {
    vi.mocked(loadNativeCityPresentation).mockRejectedValue(new Error("declared GLB digest differs"));
    await state.resolveDeclaredAssets(trace, "./replay/");
    expect(state.nativePresentation).toBeNull();
    expect(loadScenarioMeshPack).not.toHaveBeenCalled();
    expect(root.textContent).toContain("declared GLB digest differs");
  });

  it("retains incomplete registrations as explicitly unavailable instead of borrowing a city", async () => {
    vi.mocked(inspectNativeCityPresentation).mockReturnValue({ kind: "unavailable",
      reason: "registration has no declared native presentation" });
    await state.resolveDeclaredAssets(trace, "./replay/");
    expect(loadNativeCityPresentation).not.toHaveBeenCalled();
    expect(loadScenarioMeshPack).not.toHaveBeenCalled();
    expect(state.nativePresentation).toBeNull();
    expect(root.textContent).toContain("no declared native presentation");
  });

  it("cancels disposal-time work and cannot resurrect its presentation", async () => {
    let finish!: (value: LoadedNativeCityPresentation) => void;
    let signal!: AbortSignal;
    vi.mocked(loadNativeCityPresentation).mockImplementation((_plan, options) => {
      signal = options.signal!;
      return new Promise(resolve => { finish = resolve; });
    });
    const pending = state.resolveDeclaredAssets(trace, "./replay/");
    app.dispose();
    expect(signal.aborted).toBe(true);
    const stale = presentation(); finish(stale); await pending;
    expect(stale.dispose).toHaveBeenCalledOnce();
    expect(state.nativePresentation).toBeNull();
  });

  it("uses only the live session's authenticated asset closure for native layers and GLBs", async () => {
    const session = { publicAsset: vi.fn(async () => new Response("verified by loader")), dispose: vi.fn() };
    state.session = session; state.liveAssetScenario = trace.scenario.scenario_digest;
    const native = presentation();
    let options!: NativeCityPresentationLoadOptions;
    vi.mocked(loadNativeCityPresentation).mockImplementation(async (_plan, supplied) => {
      options = supplied;
      await supplied.fetch!(new URL(`assets/${"a".repeat(64)}`, supplied.baseHref), { signal: supplied.signal });
      return native;
    });
    await state.resolveLiveScene(trace.scenario);
    expect(state.nativePresentation).toBe(native);
    expect(session.publicAsset).toHaveBeenCalledWith("a".repeat(64), options.signal);
    expect(loadScenarioMeshPack).not.toHaveBeenCalled();
  });

  it("disposes a live presentation returned after its session was replaced", async () => {
    const session = { publicAsset: vi.fn(), dispose: vi.fn() };
    state.session = session; state.liveAssetScenario = trace.scenario.scenario_digest;
    let finish!: (value: LoadedNativeCityPresentation) => void;
    vi.mocked(loadNativeCityPresentation).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const pending = state.resolveLiveScene(trace.scenario);
    state.session = { publicAsset: vi.fn(), dispose: vi.fn() };
    const stale = presentation(); finish(stale); await pending;
    expect(stale.dispose).toHaveBeenCalledOnce();
    expect(state.nativePresentation).toBeNull();
  });
});
