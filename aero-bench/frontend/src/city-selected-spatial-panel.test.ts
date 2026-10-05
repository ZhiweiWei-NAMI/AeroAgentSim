// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CitySelectedScenario, SelectedScenarioFacility, SelectedScenarioNoFlyZone } from "./city-selected-scenario";
import { CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario } from "./city-selected-scenario";
import { CITY_SELECTED_DRAFT_SCHEMA, selectionDigest } from "./city-selected-draft";
import type { SceneSelection } from "./city-region-selector";
import { renderSelectedSpatialPanel, type SelectedSpatialMapData } from "./city-selected-spatial-panel";

/** Deterministic 64-hex digest-shaped string per seed; fixtures pin identity by value. */
function hex64(seed: string): string {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index++) {
    hash = Math.imul(hash ^ seed.charCodeAt(index), 16777619);
  }
  const block = (hash >>> 0).toString(16).padStart(8, "0");
  return block.repeat(8);
}

const origin = {
  latitude_deg: 31.23, longitude_deg: 121.47,
  ellipsoid_height_m: 50, geoid_undulation_m: 30, amsl_m: 20,
};

async function makeScenario(
  facilities: SelectedScenarioFacility[] = [],
  noFlyZones: SelectedScenarioNoFlyZone[] = [],
): Promise<CitySelectedScenario> {
  const selection: SceneSelection = {
    schema_version: "aero-bench.scene-selection/v1",
    source_id: "test-city-osm-v1",
    source_sha256: hex64("raw-source"),
    origin,
    bounds_enu_m: { min_east_m: -500, max_east_m: 500, min_north_m: -400, max_north_m: 400 },
  };
  const draft = {
    purpose: "selected-scene-authoring",
    schema_version: CITY_SELECTED_DRAFT_SCHEMA,
    selection,
    selection_sha256: await selectionDigest(selection),
    job_id: hex64("job"),
    source_sha256: hex64("raw-source"),
    pack_manifest_sha256: hex64("pack"),
    presentation_manifest_sha256: hex64("presentation"),
  };
  return parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene: draft,
    facilities,
    noFlyZones,
    fleet: [],
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
}

const data: SelectedSpatialMapData = {
  origin: { latitude_deg: 31.23, longitude_deg: 121.47 },
  extent: { minX: 0, maxX: 100, minZ: 0, maxZ: 100 },
  buildings: [{ id: "building-1", x: 50, z: 50, widthM: 20, depthM: 20, heightM: 20, rotationDeg: 0 }],
  motorRoads: [],
  roadClearance: {
    provenance: { source: "selected-static-road-v1", roadSha256: "1".repeat(64),
      fixtureIdentity: "measured-static-obstacles:0", displayedSurfaceSha256: "3".repeat(64) },
    roadbed: [], walkbed: [], crossings: [], fixtures: [],
  },
  roofSupports: [],
};

function mockRect(width = 100, height = 100): void {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
    left: 0, top: 0, right: width, bottom: height, width, height,
    x: 0, y: 0, toJSON: () => ({}),
  });
}

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

function fieldInput(card: HTMLElement, label: string): HTMLInputElement {
  const field = [...card.querySelectorAll("label")].find(item => item.textContent?.includes(label));
  if (field === undefined) throw new Error(`Missing field ${label}`);
  const input = field.querySelector("input");
  if (input === null) throw new Error(`Missing input for ${label}`);
  return input;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("selected city spatial panel", () => {
  it("blocks an edit onto verified selected-city walkbed before the async accept gate", async () => {
    const facility: SelectedScenarioFacility = {
      id: "facility-1", name: "充电站", kind: "charger", position: { x: 20, z: 20 },
      placement: "ground", buildingId: null, supportHeightM: null,
      rotationDeg: 0, widthM: 10, depthM: 8, heightM: 3.2,
      landing: null, cargo: null,
      charging: { slots: 1, powerW: 5000, priceAmount: 1, priceCurrency: "CNY", priceUnit: "kWh" },
    };
    const blockedData: SelectedSpatialMapData = {
      ...data,
      roadClearance: { ...data.roadClearance, walkbed: [{ id: "walkbed:selected", outline: [
        { x: 10, z: 40 }, { x: 30, z: 40 }, { x: 30, z: 60 }, { x: 10, z: 60 },
      ], holes: [] }] },
    };
    const root = document.createElement("div");
    const onChange = vi.fn(async () => null);
    renderSelectedSpatialPanel(root, await makeScenario([facility]), onChange, blockedData);
    const zInput = fieldInput(root.querySelector<HTMLElement>("[data-facility-id]")!, "Z / m");
    zInput.value = "50";
    zInput.dispatchEvent(new Event("change", { bubbles: true }));
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("已验证人行铺装 walkbed:selected");
  });

  it("maps a map click into local metre coordinates and chooses the facility centre", async () => {
    mockRect();
    const root = document.createElement("div");
    const scenario = await makeScenario();
    const onChange = vi.fn(async (_next: CitySelectedScenario) => null);
    renderSelectedSpatialPanel(root, scenario, onChange, data);

    clickButton(root, "放置起降点");
    clickMap(root, 25, 75);
    clickButton(root, "确认放置");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));

    const next = onChange.mock.calls[0]![0] as CitySelectedScenario;
    const facility = next.facilities[0]!;
    expect(facility.position).toEqual({ x: 25, z: 75 });
    expect(facility.kind).toBe("vertiport");
    expect(facility.rotationDeg).toBe(0);
    expect(facility.widthM).toBeGreaterThanOrEqual(12);
    expect(facility.depthM).toBeGreaterThanOrEqual(8);
  });

  it("adds a facility inside the selected bounds with human-scale kind defaults", async () => {
    mockRect();
    const root = document.createElement("div");
    const scenario = await makeScenario();
    const onChange = vi.fn(async (_next: CitySelectedScenario) => null);
    renderSelectedSpatialPanel(root, scenario, onChange, data);

    clickButton(root, "放置物流中转站");
    clickMap(root, 80, 20);
    clickButton(root, "确认放置");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));

    const next = onChange.mock.calls[0]![0] as CitySelectedScenario;
    expect(next.facilities).toHaveLength(1);
    expect(next.facilities[0]).toMatchObject({
      kind: "hub", position: { x: 80, z: 20 }, rotationDeg: 0,
      widthM: 18, depthM: 14, heightM: 5.2,
      landing: { parkingSlots: 2, movementsPerHour: 20 },
      cargo: { storageCapacityKg: 500, throughputPerHourKg: 1000 },
      charging: null,
    });

    // The caller accepts the edit, so a parent rerender commits it visibly.
    renderSelectedSpatialPanel(root, next, onChange, data);
    expect(root.querySelectorAll("[data-facility-id]")).toHaveLength(1);
    expect(root.querySelector("svg rect[stroke]")).not.toBeNull();
  });

  it("offers camera focus for a displayed facility", async () => {
    const root = document.createElement("div");
    const facility: SelectedScenarioFacility = {
      id: "facility-1", name: "主起降点", kind: "vertiport", position: { x: 25, z: 75 },
      placement: "ground", buildingId: null, supportHeightM: null,
      rotationDeg: 0, widthM: 14, depthM: 10, heightM: 4.5,
      landing: { parkingSlots: 1, movementsPerHour: 30 }, cargo: null, charging: null,
    };
    const onFocus = vi.fn(() => true);
    renderSelectedSpatialPanel(root, await makeScenario([facility]), async () => null, data, onFocus);
    clickButton(root, "在三维视图定位");
    expect(onFocus).toHaveBeenCalledWith("facility-1");
  });

  it("rejects a colliding edit from the async callback and keeps the committed draft", async () => {
    mockRect();
    const root = document.createElement("div");
    const scenario = await makeScenario();
    const onChange = vi.fn(async (next: CitySelectedScenario) => {
      const suspect = next.facilities.find(item => item.id === "facility-1" && item.position.z === 50);
      return suspect === undefined ? null : "与已核验建筑 building-1:0 重叠";
    });
    renderSelectedSpatialPanel(root, scenario, onChange, data);

    // First an accepted placement clears the way.
    clickButton(root, "放置充电站");
    clickMap(root, 20, 20);
    clickButton(root, "确认放置");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const accepted = onChange.mock.calls[0]![0] as CitySelectedScenario;
    renderSelectedSpatialPanel(root, accepted, onChange, data);
    expect(root.querySelectorAll("[data-facility-id]")).toHaveLength(1);
    await new Promise(resolve => setTimeout(resolve, 0));

    // Move the facility over the verified building at Z=50; callback rejects.
    const card = root.querySelector<HTMLElement>("[data-facility-id]")!;
    const zInput = fieldInput(card, "Z / m");
    zInput.value = "50";
    zInput.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(2));
    expect(onChange.mock.calls[1]![0].facilities[0]!.position.z).toBe(50);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("building-1:0");

    // The committed draft is unchanged: the edited Z never takes hold.
    const cardAfter = root.querySelector<HTMLElement>("[data-facility-id]")!;
    expect(fieldInput(cardAfter, "Z / m").value).toBe("20");
    expect(root.querySelectorAll("[data-facility-id]")).toHaveLength(1);
  });

  it("completes a manual no-fly polygon with explicit metadata and synthetic vertices", async () => {
    mockRect();
    const root = document.createElement("div");
    const scenario = await makeScenario();
    const onChange = vi.fn(async (_next: CitySelectedScenario) => null);
    renderSelectedSpatialPanel(root, scenario, onChange, data);

    clickButton(root, "绘制禁飞区");
    for (const [x, z] of [[10, 10], [30, 10], [30, 40], [10, 40]]) clickMap(root, x!, z!);

    const editor = root.querySelector<HTMLElement>(".studio-spatial-polygon-editor")!;
    const nameInput = fieldInput(editor, "手工区域名称");
    nameInput.value = "医院净空";
    nameInput.dispatchEvent(new Event("change", { bubbles: true }));
    const floorInput = fieldInput(editor, "下限 / m");
    floorInput.value = "15";
    floorInput.dispatchEvent(new Event("change", { bubbles: true }));

    clickButton(root, "完成多边形");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));

    const next = onChange.mock.calls[0]![0] as CitySelectedScenario;
    expect(next.noFlyZones).toHaveLength(1);
    const zone = next.noFlyZones[0]!;
    expect(zone.polygon).toEqual([
      { x: 10, z: 10 }, { x: 30, z: 10 }, { x: 30, z: 40 }, { x: 10, z: 40 },
    ]);
    expect(zone.source).toEqual({ kind: "manual", label: "用户手工标绘", uri: null });
    expect(zone.name).toBe("医院净空");
    expect(zone.floorM).toBe(15);
    expect(zone.ceilingM).toBe(120);
    expect(zone.startsAtS).toBe(0);
    expect(zone.endsAtS).toBeNull();
  });

  it("imports user-supplied WGS84 GeoJSON with source URL and passes it to the accept gate", async () => {
    mockRect();
    const root = document.createElement("div");
    const scenario = await makeScenario();
    const onChange = vi.fn(async (_next: CitySelectedScenario) => null);
    renderSelectedSpatialPanel(root, scenario, onChange, data);
    const url = "https://example.org/airspace.geojson";
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({
      type: "Feature", properties: { name: "医院净空" }, geometry: { type: "Polygon", coordinates: [[
        [121.47, 31.23], [121.4701, 31.23], [121.4701, 31.2299],
        [121.47, 31.2299], [121.47, 31.23],
      ]] },
    }) })));
    const input = root.querySelector<HTMLInputElement>('input[aria-label="禁飞区 GeoJSON URL"]')!;
    input.value = url;
    clickButton(root, "从 URL 导入");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    const zone = onChange.mock.calls[0]![0].noFlyZones[0]!;
    expect(zone.name).toBe("医院净空");
    expect(zone.source).toEqual({ kind: "geojson", label: url, uri: url });
    expect(zone.polygon).toHaveLength(4);
    expect(zone.polygon[1]!.x).toBeGreaterThan(zone.polygon[0]!.x);
  });
});
