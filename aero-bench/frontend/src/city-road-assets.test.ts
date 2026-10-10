// @vitest-environment node
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseCitySceneConfig } from "./city-scene-config";
import { cityTrafficProvenance, loadVerifiedCityRoadAssets, verifyCityRoadAssetBindings, type CityRoadAssetPayloads,
  type CityRoadAssetsProgress } from "./city-road-assets";
import type { BuildingRenderManifest } from "./city-building-renders";
import type { MeshPackManifest } from "./osm2world/pack";

const read = (path: string): string => readFileSync(new URL(`../public/${path}`, import.meta.url), "utf8");
const scene = parseCitySceneConfig(JSON.parse(read("city-presentation/building-render-scene-v1.json")));
if (scene.road_assets === undefined) throw new Error("Test scene must declare road_assets");
const ref = scene.road_assets;
const renderDigest = scene.building_render!.manifest.sha256;
const pack = {
  manifest: JSON.parse(read("osm2world/packs/shanghai-huangpu-east-v1/manifest.json")) as MeshPackManifest,
  manifestSha256: scene.mesh_pack.manifest.sha256,
};
const render = JSON.parse(read("building-renders/shanghai-huangpu-east-v1/manifest.json")) as BuildingRenderManifest;
const texts = new Map([ref.road, ref.effective_fixtures, ref.traffic, ref.flight]
  .map(file => [file.url, read(file.url.slice(1))]));
const payloads = (): CityRoadAssetPayloads => ({
  road: JSON.parse(texts.get(ref.road.url)!),
  effective_fixtures: JSON.parse(texts.get(ref.effective_fixtures.url)!),
  traffic: JSON.parse(texts.get(ref.traffic.url)!),
  flight: JSON.parse(texts.get(ref.flight.url)!),
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("recording and replay counts", () => {
  it("preserves 71 SUMO identities and 12 bicycles while describing 65 retained identities and 12 bicycles", () => {
    const data = payloads().traffic as Parameters<typeof cityTrafficProvenance>[0];
    expect(cityTrafficProvenance(data)).toMatchObject({ observedVehicles: 71, observedBicycles: 12,
      observedPersons: 36, displayedVehicles: 65, displayedBicycles: 12, displayedPersons: 36, omittedVehicles: 6 });
  });

  it("counts retained identities once across repeated frames", () => {
    const frame = { vehicles: [["a", 0, 0, 0, "sedan"], ["b", 0, 0, 0, "bicycle"]] as const,
      persons: [["p", 0, 0, 0]] as const };
    expect(cityTrafficProvenance({ demand: { observed: { vehicles: 3, bicycle: 2, persons: 2 } },
      frames: [frame, frame] })).toMatchObject({ displayedVehicles: 2, displayedBicycles: 1,
      displayedPersons: 1, omittedVehicles: 1 });
  });

  it("exposes missing observed counts and recording drift", () => {
    expect(() => cityTrafficProvenance({ demand: { observed: { vehicles: 2, persons: 0 } }, frames: [] }))
      .toThrow(/observed bicycle count/);
    expect(() => cityTrafficProvenance({ demand: { observed: { vehicles: 0, bicycle: 0, persons: 0 } },
      frames: [{ vehicles: [["a", 0, 0, 0, "sedan"]], persons: [] }] }))
      .toThrow(/replay identities exceed observed demand/);
  });
});

describe("canonical ground road assets", () => {
  it("binds road, effective fixtures, traffic and flight to the 414 render source context", async () => {
    const data = payloads();
    const selection = await verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest);
    const fixtures = data.effective_fixtures as { counts: Record<string, number> };
    expect(selection.signalIds.size).toBe(fixtures.counts.effective_signals);
    expect(selection.streetLampIndices.size).toBe(fixtures.counts.effective_street_lamps);
    expect(selection.signalModelSha256).toBe(scene.traffic_signal_model!.sha256);
    expect((data.flight as { source_kind: string }).source_kind).toBe("planned-visual-flight");
  });

  it("rejects a building render source mismatch", async () => {
    await expect(verifyCityRoadAssetBindings(ref, pack,
      { ...render, scene: { ...render.scene, mesh_pack_source_sha256: "a".repeat(64) } }, payloads(), renderDigest))
      .rejects.toThrow(/share the verified mesh pack source/);
    await expect(verifyCityRoadAssetBindings({ ...ref, source_scene_id: "other-city" }, pack, render, payloads(), renderDigest))
      .rejects.toThrow(/share the verified mesh pack source/);
  });

  it("blocks an unchanged road after the actual render manifest bytes change", async () => {
    await expect(verifyCityRoadAssetBindings(ref, pack, render, payloads(), "c".repeat(64)))
      .rejects.toThrow(/source context differs from the verified rendered buildings/);
  });

  it.each(["objects_json_sha256", "source_network_sha256", "mesh_pack_manifest_sha256"])(
    "rejects road source-context drift in %s", async key => {
      const data = payloads();
      (data.road as { source_context: Record<string, unknown> }).source_context[key] = "a".repeat(64);
      await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest))
        .rejects.toThrow(/source context differs from the verified rendered buildings/);
    });

  it("rejects a road without physical clearance PASS or with an elevated scope", async () => {
    const data = payloads();
    (data.road as { physical_clearance: Record<string, unknown> }).physical_clearance.status = "BLOCKED";
    await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest))
      .rejects.toThrow(/physical clearance PASS/);
    const scoped = payloads();
    (scoped.road as Record<string, unknown>).road_scope = "all-levels";
    await expect(verifyCityRoadAssetBindings(ref, pack, render, scoped, renderDigest))
      .rejects.toThrow(/physical clearance PASS/);
  });

  it.each(["effective_fixtures", "traffic", "flight"] as const)(
    "rejects %s bound to another source context", async kind => {
      const data = payloads();
      const context = (data[kind] as { source_context: Record<string, unknown> }).source_context;
      context.building_geometry = { ...(context.building_geometry as object), count: 413 };
      await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest)).rejects.toThrow();
    });

  it("rejects traffic recorded against other effective fixture bytes or obstacle policy", async () => {
    const data = payloads();
    const basis = (data.traffic as { visual_obstacle_basis: Record<string, unknown> }).visual_obstacle_basis;
    basis.effective_fixture_geometry_sha256 = "a".repeat(64);
    await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest))
      .rejects.toThrow(/canonical recording of this road and fixture geometry/);
    const policy = payloads();
    (policy.traffic as { visual_obstacle_basis: Record<string, unknown> }).visual_obstacle_basis.policy
      = "placement-proxies-union-true-rendered-footprints";
    await expect(verifyCityRoadAssetBindings(ref, pack, render, policy, renderDigest))
      .rejects.toThrow(/canonical recording of this road and fixture geometry/);
  });

  it("rejects a fixture inventory that drops or duplicates a source fixture", async () => {
    const dropped = payloads();
    (dropped.effective_fixtures as { omitted_street_lamps: unknown[] }).omitted_street_lamps.pop();
    await expect(verifyCityRoadAssetBindings(ref, pack, render, dropped, renderDigest))
      .rejects.toThrow(/do not cover every source fixture/);
    const duplicated = payloads();
    const effective = (duplicated.effective_fixtures as { effective_fixtures: unknown[] }).effective_fixtures;
    effective.push(effective[0]);
    await expect(verifyCityRoadAssetBindings(ref, pack, render, duplicated, renderDigest))
      .rejects.toThrow(/invalid or duplicated/);
  });

  it("rehashes the actual displayed surface instead of trusting the JSON field", async () => {
    const data = payloads();
    const road = data.road as { roadbed: { outline: number[][] }[] };
    road.roadbed[0]!.outline[0]![0]! += 0.25;
    await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest))
      .rejects.toThrow(/polygons differ from the declared displayed surface/);
  });

  it("rejects a flight physical-simulation claim", async () => {
    const data = payloads();
    (data.flight as Record<string, unknown>).physical_simulation = true;
    await expect(verifyCityRoadAssetBindings(ref, pack, render, data, renderDigest))
      .rejects.toThrow(/declared city source and provenance/);
  });
});

describe("road asset byte verification", () => {
  it("does not start reads for an already cancelled scene", async () => {
    const fetcher = serve();
    const abort = new AbortController(); abort.abort();
    await expect(loadVerifiedCityRoadAssets(ref, pack, render, renderDigest, undefined, abort.signal))
      .rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("cancels the other reads when one road asset request fails", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, options: RequestInit) => {
      const signal = options.signal!; signals.push(signal);
      if (url === ref.road.url) return Promise.resolve(new Response("", { status: 503 }));
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    }));
    const publish = vi.spyOn(URL, "createObjectURL");
    await expect(loadVerifiedCityRoadAssets(ref, pack, render, renderDigest)).rejects.toThrow(/request failed: 503/);
    expect(signals).toHaveLength(4);
    expect(signals.every(signal => signal.aborted)).toBe(true);
    expect(publish).not.toHaveBeenCalled();
  });

  it("cancels all four pending reads when the scene is replaced", async () => {
    const signals: AbortSignal[] = [];
    vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => {
      const signal = options.signal!; signals.push(signal);
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    }));
    const controller = new AbortController();
    const pending = loadVerifiedCityRoadAssets(ref, pack, render, renderDigest, undefined, controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(signals).toHaveLength(4);
    expect(signals.every(signal => signal.aborted)).toBe(true);
  });

  const serve = (overrides = new Map<string, string>()): ReturnType<typeof vi.fn> => {
    const fetcher = vi.fn(async (url: string | URL) => {
      const text = overrides.get(String(url)) ?? texts.get(String(url));
      if (text === undefined) throw new Error(`Unexpected road asset fetch ${url}`);
      return new Response(text);
    });
    vi.stubGlobal("fetch", fetcher);
    return fetcher;
  };

  it("publishes only frozen road, traffic and flight bytes and revokes them after use", async () => {
    const fetcher = serve();
    const progress: CityRoadAssetsProgress[] = [];
    const verified = await loadVerifiedCityRoadAssets(ref, pack, render, renderDigest, state => progress.push(state));
    expect(fetcher.mock.calls.map(call => call[0])).toEqual([
      ref.road.url, ref.effective_fixtures.url, ref.traffic.url, ref.flight.url,
    ]);
    expect(verified.sourceContext).toEqual((payloads().road as { source_context: unknown }).source_context);
    expect(verified.displayedSurfaceSha256).toBe(ref.displayed_surface_sha256);
    expect(progress.at(-1)).toMatchObject({ completedBytes: progress.at(-1)!.totalBytes, verifiedFiles: 4 });
    expect(verified.roadUrl).toMatch(/^blob:/);
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    verified.dispose();
    expect(revoke).toHaveBeenCalledTimes(3);
  });

  it("rejects changed bytes before publishing any replay URL", async () => {
    serve(new Map([[ref.traffic.url, texts.get(ref.traffic.url)!.replace("offline-sumo", "offlinf-sumo")]]));
    const publish = vi.spyOn(URL, "createObjectURL");
    await expect(loadVerifiedCityRoadAssets(ref, pack, render, renderDigest)).rejects.toThrow(/traffic digest differs/);
    expect(publish).not.toHaveBeenCalled();
  });

  it("rejects a truncated file before publishing any replay URL", async () => {
    serve(new Map([[ref.road.url, texts.get(ref.road.url)!.slice(0, -1)]]));
    const publish = vi.spyOn(URL, "createObjectURL");
    await expect(loadVerifiedCityRoadAssets(ref, pack, render, renderDigest)).rejects.toThrow(/declared size/);
    expect(publish).not.toHaveBeenCalled();
  });
});
