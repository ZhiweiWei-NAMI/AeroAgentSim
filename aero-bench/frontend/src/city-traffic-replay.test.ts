// @vitest-environment node
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseCitySceneConfig } from "./city-scene-config";
import { CityTrafficPreview } from "./city-presentation";
import { createCitySignalFixtureAssets, loadCityTrafficReplay } from "./city-traffic-replay";

const config = parseCitySceneConfig(JSON.parse(readFileSync(new URL(
  "../public/city-presentation/default-scene-v1.json", import.meta.url), "utf8")));
const ref = config.traffic_signal_model!;
const sourceBytes = new Uint8Array(readFileSync(new URL(`../public${ref.url}`, import.meta.url)));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("declared traffic signal model", () => {
  it("fails missing model identity before source reads or replay loading", async () => {
    const fetchSpy = vi.fn(); vi.stubGlobal("fetch", fetchSpy);
    const replay = vi.spyOn(CityTrafficPreview, "load");
    await expect(loadCityTrafficReplay("/traffic", "/flight", undefined, "http://localhost/", "all-source-signals"))
      .rejects.toThrow(/declared signal model byte identity/);
    expect(fetchSpy).not.toHaveBeenCalled(); expect(replay).not.toHaveBeenCalled();
  });

  it("verifies actual source GLB bytes and passes its owned blob reader", async () => {
    const fetchSpy = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response(new Uint8Array(sourceBytes)));
    vi.stubGlobal("fetch", fetchSpy);
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    let signalBlob = "";
    const preview = {} as CityTrafficPreview;
    const replay = vi.spyOn(CityTrafficPreview, "load").mockImplementation(async (_traffic, _flight, assets) => {
      signalBlob = await assets.url(ref.url);
      expect(signalBlob).toMatch(/^blob:/);
      return preview;
    });
    await expect(loadCityTrafficReplay("/traffic", "/flight", ref, "http://localhost/", "all-source-signals"))
      .resolves.toBe(preview);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(String(fetchSpy.mock.calls[0]![0])).toBe(`http://localhost${ref.url}`);
    expect(replay).toHaveBeenCalledTimes(1); expect(revoke).toHaveBeenCalledWith(signalBlob);
  });

  it("rejects a same-size corrupted model before replay or fleet loading", async () => {
    const corrupted = new Uint8Array(sourceBytes); corrupted[corrupted.length - 1]! ^= 1;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(corrupted)));
    const replay = vi.spyOn(CityTrafficPreview, "load");
    await expect(loadCityTrafficReplay("/traffic", "/flight", ref, "http://localhost/", "all-source-signals"))
      .rejects.toThrow(/differs from the declared digest/);
    expect(replay).not.toHaveBeenCalled();
  });

  it("cancels the actual source request when the owning scene is aborted", async () => {
    let requestSignal: AbortSignal | null | undefined;
    let started!: () => void; const ready = new Promise<void>(resolve => { started = resolve; });
    vi.stubGlobal("fetch", vi.fn((_input: unknown, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      requestSignal = init?.signal; started();
      requestSignal?.addEventListener("abort", () => reject(new DOMException("Scene replaced", "AbortError")), { once: true });
    })));
    const abort = new AbortController(), replay = vi.spyOn(CityTrafficPreview, "load");
    const pending = loadCityTrafficReplay("/traffic", "/flight", ref, "http://localhost/", "all-source-signals", abort.signal);
    await ready; abort.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true); expect(replay).not.toHaveBeenCalled();
  });

  it("releases a verified model blob when replay loading fails later", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(new Uint8Array(sourceBytes))));
    const revoke = vi.spyOn(URL, "revokeObjectURL");
    vi.spyOn(CityTrafficPreview, "load").mockRejectedValue(new Error("Traffic contract rejected"));
    await expect(loadCityTrafficReplay("/traffic", "/flight", ref, "http://localhost/", "all-source-signals"))
      .rejects.toThrow("Traffic contract rejected");
    expect(revoke).toHaveBeenCalledTimes(1);
  });

  it("rejects undeclared asset requests instead of returning a raw model URL", async () => {
    const assets = createCitySignalFixtureAssets(ref, "http://localhost/");
    try {
      await expect(assets.url("/models/incoming/furniture/glb/street_light_8.glb"))
        .rejects.toThrow(/未在已验证目录声明/);
    } finally { assets.dispose(); }
  });
});
