import { beforeAll, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { webcrypto } from "node:crypto";
import {
  CityRegionSelector, SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  enuBoundsForGeographic, geodeticToEnu, loadRegionSource, sceneSelectionForBounds,
  type LoadedRegionSource, type RegionSource,
} from "./city-region-selector";

const bytes = Uint8Array.from(readFileSync(resolve(process.cwd(), "public/osm2world/shanghai-hongqiao.osm.json")));
const source: RegionSource = { source_id: SHANGHAI_SOURCE_ID, source_sha256: SHANGHAI_SOURCE_SHA256, origin: SHANGHAI_ORIGIN, bytes };
let loaded: LoadedRegionSource;

beforeAll(async () => {
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
  loaded = await loadRegionSource(source);
});

describe("published OSM region source", () => {
  it("hashes original bytes and renders actual road and building ways", () => {
    expect(loaded.source_sha256).toBe(SHANGHAI_SOURCE_SHA256);
    expect(bytes.byteLength).toBe(4_868_022);
    expect(loaded.bounds).toEqual({ minlat: 31.2228, minlon: 121.4636, maxlat: 31.2371, maxlon: 121.4868 });
    const roads = loaded.features.filter(feature => feature.kind === "road");
    const buildings = loaded.features.filter(feature => feature.kind === "building");
    expect(roads.length).toBe(1_207);
    expect(buildings.length).toBe(6_415);
    expect(roads[0]?.points.length).toBeGreaterThan(1);
    expect(buildings[0]?.points.length).toBeGreaterThan(2);
  });

  it("rejects changed bytes before parsing", async () => {
    const changed = bytes.slice(); changed[100] = changed[100]! ^ 1;
    await expect(loadRegionSource({ ...source, bytes: changed })).rejects.toThrow(/SHA-256 mismatch/);
  });
});

describe("selection coordinates", () => {
  it("matches the Python WGS84/ECEF/ENU reference values", () => {
    const positive = geodeticToEnu(31.232, 121.476, SHANGHAI_ORIGIN);
    expect(positive.east).toBeCloseTo(219.12792433650512, 6);
    expect(positive.north).toBeCloseTo(177.40119096448234, 6);
    expect(positive.up).toBeCloseTo(-0.00623779185498563, 6);
    const negative = geodeticToEnu(31.229, 121.471, SHANGHAI_ORIGIN);
    expect(negative.east).toBeCloseTo(-257.24525548429654, 6);
    expect(negative.north).toBeCloseTo(-155.22086791161922, 6);
  });

  it("includes the south-edge north minimum missed by four corners", () => {
    const bounds = enuBoundsForGeographic(loaded.bounds, SHANGHAI_ORIGIN);
    const southwest = geodeticToEnu(31.2228, 121.4636, SHANGHAI_ORIGIN);
    const southeast = geodeticToEnu(31.2228, 121.4868, SHANGHAI_ORIGIN);
    expect(bounds.min_north_m).toBeCloseTo(-842.6442195417069, 6);
    expect(bounds.min_north_m).toBeLessThan(Math.min(southwest.north, southeast.north) - 0.04);
  });

  it("rejects nonfinite, degenerate, crossing, and outside selections", () => {
    const valid = { minlat: 31.228, maxlat: 31.232, minlon: 121.47, maxlon: 121.478 };
    expect(() => sceneSelectionForBounds(loaded, { ...valid, minlat: NaN })).toThrow(/finite/);
    expect(() => sceneSelectionForBounds(loaded, { ...valid, maxlat: valid.minlat })).toThrow(/latitude/);
    expect(() => sceneSelectionForBounds(loaded, { ...valid, minlon: valid.maxlon })).toThrow(/longitude/);
    expect(() => sceneSelectionForBounds(loaded, { ...valid, maxlon: 121.5 })).toThrow(/source bounds/);
  });

  it("returns only the shared SceneSelection contract fields", () => {
    const selection = sceneSelectionForBounds(loaded, { minlat: 31.228, maxlat: 31.232, minlon: 121.47, maxlon: 121.478 });
    expect(Object.keys(selection).sort()).toEqual(["bounds_enu_m", "origin", "schema_version", "source_id", "source_sha256"]);
    expect(selection.schema_version).toBe("aero-bench.scene-selection/v1");
    expect(selection.source_id).toBe(SHANGHAI_SOURCE_ID);
    expect(selection.source_sha256).toBe(SHANGHAI_SOURCE_SHA256);
    expect(selection.origin).toEqual(SHANGHAI_ORIGIN);
    expect(selection.bounds_enu_m.min_east_m).toBeLessThan(selection.bounds_enu_m.max_east_m);
    expect(selection.bounds_enu_m.min_north_m).toBeLessThan(selection.bounds_enu_m.max_north_m);
  });

  it("rejects all four map-edge rectangles before emitting an ENU crop", () => {
    const rectangles = [
      { minlat: 31.225, maxlat: 31.231, minlon: 121.4636, maxlon: 121.47 },
      { minlat: 31.225, maxlat: 31.231, minlon: 121.48, maxlon: 121.4868 },
      { minlat: 31.2228, maxlat: 31.228, minlon: 121.47, maxlon: 121.478 },
      { minlat: 31.232, maxlat: 31.2371, minlon: 121.47, maxlon: 121.478 },
      loaded.bounds,
    ];
    for (const bounds of rectangles) expect(() => sceneSelectionForBounds(loaded, bounds)).toThrow(/ENU 外包越过 OSM 数据边界/);
  });
});

describe("mounted selector", () => {
  it("emits the verified contract and geographic bounds from the component", async () => {
    class ResizeObserverStub {
      observe(): void { /* layout is not available in jsdom */ }
      disconnect(): void { /* layout is not available in jsdom */ }
    }
    Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const container = document.createElement("div");
    const onSelection = vi.fn();
    const component = await CityRegionSelector.mount(container, { source, onSelection });
    expect(() => component.setSelection({ minlat: 31.225, maxlat: 31.231, minlon: 121.4636, maxlon: 121.47 })).toThrow(/ENU 外包/);
    expect(onSelection).not.toHaveBeenCalled();
    expect(container.textContent).toContain("请向内侧留出余量");
    const bounds = { minlat: 31.228, maxlat: 31.232, minlon: 121.47, maxlon: 121.478 };
    const result = component.setSelection(bounds);
    expect(onSelection).toHaveBeenCalledExactlyOnceWith(result, bounds);
    expect(container.textContent).toContain("ENU 外包");
    expect(container.textContent).toContain("SHA-256 已核验");
    component.destroy();
    expect(container.childElementCount).toBe(0);
    vi.restoreAllMocks();
  });

  it("shows a restored ENU crop without changing its identity or emitting another selection", async () => {
    class ResizeObserverStub {
      observe(): void { /* layout is not available in jsdom */ }
      disconnect(): void { /* layout is not available in jsdom */ }
    }
    Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: ResizeObserverStub });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({} as CanvasRenderingContext2D);
    const container = document.createElement("div");
    const onSelection = vi.fn();
    const component = await CityRegionSelector.mount(container, { source, onSelection });
    const restored = {
      schema_version: "aero-bench.scene-selection/v1" as const,
      source_id: SHANGHAI_SOURCE_ID, source_sha256: SHANGHAI_SOURCE_SHA256,
      origin: SHANGHAI_ORIGIN,
      bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
    };
    component.showRestoredSelection(restored);
    expect(component.getSelection()).toBeNull();
    expect(onSelection).not.toHaveBeenCalled();
    expect(container.textContent).toContain("已恢复选区 · ENU 东向 300.0–780.0 m");
    expect(() => component.showRestoredSelection({ ...restored, source_sha256: "0".repeat(64) })).toThrow(/OSM 来源/);
    const newBounds = { minlat: 31.228, maxlat: 31.232, minlon: 121.47, maxlon: 121.478 };
    component.setSelection(newBounds);
    expect(container.textContent).not.toContain("已恢复选区");
    expect(onSelection).toHaveBeenCalledOnce();
    component.destroy();
    vi.restoreAllMocks();
  });
});
