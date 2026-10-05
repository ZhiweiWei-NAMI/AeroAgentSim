// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";
import { renderCitySpatialPanel, type SpatialMapData } from "./city-spatial-panel";

const data: SpatialMapData = {
  origin: { latitude_deg: 31.2, longitude_deg: 121.5 },
  extent: { minX: 0, maxX: 100, minZ: 0, maxZ: 100 },
  buildings: [{ id: "building-1", x: 50, z: 50, widthM: 20, depthM: 20, heightM: 20, rotationDeg: 30 }],
  roads: [],
  roadClearance: {
    provenance: { source: "canonical-road-v3", roadSha256: "1".repeat(64),
      fixtureIdentity: "2".repeat(64), displayedSurfaceSha256: "3".repeat(64) },
    roadbed: [], walkbed: [], crossings: [], fixtures: [],
  },
};

function clickButton(root: HTMLElement, label: string): void {
  const control = [...root.querySelectorAll("button")].find(item => item.textContent === label);
  if (control === undefined) throw new Error(`Missing button ${label}`);
  control.click();
}

function clickMap(root: HTMLElement, x: number, z: number): void {
  const map = root.querySelector("svg.studio-spatial-map");
  if (map === null) throw new Error("Missing city map");
  map.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: x, clientY: z }));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("city spatial panel", () => {
  it("shows verified clearance provenance and blocks a facility on walkbed", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100,
      x: 0, y: 0, toJSON: () => ({}),
    });
    const root = document.createElement("div");
    const onChange = vi.fn();
    const walkbed = { id: "walkbed:0", outline: [
      { x: 10, z: 10 }, { x: 30, z: 10 }, { x: 30, z: 30 }, { x: 10, z: 30 },
    ], holes: [] };
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, {
      ...data, roadClearance: { ...data.roadClearance!, walkbed: [walkbed] },
    });
    expect(root.querySelector('[data-role="spatial-road-clearance"]')?.textContent)
      .toContain("人行铺装 1");
    clickButton(root, "放置充电站");
    clickMap(root, 20, 20);
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("已验证人行铺装 walkbed:0");
  });

  it("reports missing clearance and never silently accepts a ground facility", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100,
      x: 0, y: 0, toJSON: () => ({}),
    });
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, {
      ...data, roadClearance: null, roadClearanceError: "测试净空输入缺失",
    });
    expect(root.querySelector('[data-role="spatial-road-clearance"]')?.textContent)
      .toContain("测试净空输入缺失");
    clickButton(root, "放置起降点");
    clickMap(root, 20, 20);
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("缺少已验证道路");
  });

  it.each([
    ["放置起降点", { widthM: 14, depthM: 10, heightM: 4.5 }, 12, 4],
    ["放置物流中转站", { widthM: 18, depthM: 14, heightM: 5.2 }, 14, 5],
    ["放置充电站", { widthM: 10, depthM: 8, heightM: 3.2 }, 10, 3.2],
  ] as const)("%s uses human-scale dimensions and rejects a footprint below the model minimum", (tool, expected, minWidth, minHeight) => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100,
      x: 0, y: 0, toJSON: () => ({}),
    });
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, data);
    clickButton(root, tool);
    clickMap(root, 20, 20);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].facilities[0]).toMatchObject(expected);

    const widthField = [...root.querySelectorAll("[data-facility-id] label")]
      .find(item => item.textContent?.includes("宽 / m"))!;
    const input = widthField.querySelector("input")!;
    expect(input.min).toBe(String(minWidth));
    input.value = String(minWidth - 0.1);
    input.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(`宽 ${minWidth} m`);
    expect(root.querySelector("[data-facility-id]")?.textContent).toContain("最小可容纳尺寸");

    const heightField = [...root.querySelectorAll("[data-facility-id] label")]
      .find(item => item.textContent?.includes("高 / m"))!;
    const heightInput = heightField.querySelector("input")!;
    expect(heightInput.min).toBe(String(minHeight));
    heightInput.value = String(minHeight - 0.1);
    heightInput.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain(`高 ${minHeight} m`);
  });

  it("places a facility from a map click and rejects a building collision without changing the draft", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100,
      x: 0, y: 0, toJSON: () => ({}),
    });
    const root = document.createElement("div");
    const config = createDefaultCityWorkspaceConfig();
    const onChange = vi.fn();
    renderCitySpatialPanel(root, config, onChange, data);

    clickButton(root, "放置充电站");
    clickMap(root, 20, 20);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].facilities).toMatchObject([
      { kind: "charger", position: { x: 20, z: 20 }, chargingPowerW: 5000 },
    ]);
    expect(root.textContent).toMatch(/31\.\d{7}° N, 121\.\d{7}° E/);

    clickMap(root, 50, 50);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("building-1");
    expect(root.querySelectorAll("[data-facility-id]")).toHaveLength(1);

    const changeCoordinate = (label: string, value: number) => {
      const card = root.querySelector<HTMLElement>("[data-facility-id]")!;
      const field = [...card.querySelectorAll("label")].find(item => item.textContent?.includes(label))!;
      const input = field.querySelector("input")!;
      input.value = String(value);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    };
    changeCoordinate("Z / m", 50);
    expect(onChange).toHaveBeenCalledTimes(2);
    changeCoordinate("X / m", 50);
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(root.querySelector("[data-facility-id]")?.textContent).toContain("building-1");
    expect(root.querySelector('svg rect[stroke="#ef4444"]')).not.toBeNull();
  });

  it("shows GeoJSON import errors and fetches a URL only after the user requests it", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        type: "Feature", properties: {}, geometry: {
          type: "Polygon", coordinates: [[[121.5, 31.2], [121.501, 31.2], [121.501, 31.201]]],
        },
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, data);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(root.querySelector('input[type="file"]')).not.toBeNull();
    const url = root.querySelector<HTMLInputElement>('input[aria-label="GeoJSON URL"]')!;
    url.value = "https://example.org/airspace.geojson";
    clickButton(root, "从 URL 导入");
    await vi.waitFor(() => expect(root.querySelector('[role="alert"]')?.textContent).toContain("GeoJSON"));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("imports a user URL as local WGS84 geometry without interpreting its name as HTML", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => ({
        type: "Feature", properties: { name: '<img src=x onerror="alert(1)">' }, geometry: {
          type: "Polygon", coordinates: [[
            [121.50005, 31.19995], [121.50015, 31.19995],
            [121.50015, 31.19985], [121.50005, 31.19985], [121.50005, 31.19995],
          ]],
        },
      }),
    })));
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, data);
    root.querySelector<HTMLInputElement>('input[aria-label="GeoJSON URL"]')!.value = "https://example.org/local.geojson";
    clickButton(root, "从 URL 导入");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const airspace = onChange.mock.calls[0]![0].airspace[0];
    expect(airspace.source).toEqual({ kind: "geojson", label: "https://example.org/local.geojson",
      uri: "https://example.org/local.geojson" });
    expect(airspace.polygon[0].x).toBeGreaterThan(4);
    expect(airspace.polygon[0].z).toBeGreaterThan(4);
    expect(root.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(root.querySelector("img")).toBeNull();
  });

  it("rejects a self-intersecting hand-drawn no-fly polygon", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100,
      x: 0, y: 0, toJSON: () => ({}),
    });
    const root = document.createElement("div");
    const onChange = vi.fn();
    renderCitySpatialPanel(root, createDefaultCityWorkspaceConfig(), onChange, data);
    clickButton(root, "绘制禁飞区");
    for (const [x, z] of [[10, 10], [90, 80], [10, 90], [70, 10]]) clickMap(root, x!, z!);
    clickButton(root, "完成多边形");
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toMatch(/intersect|self/i);
  });
});
