// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import { CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioDemand, type SelectedScenarioFacility,
  type SelectedScenarioFleetEntry } from "./city-selected-scenario";
import { renderSelectedFleetPanel, type SelectedFleetAssetOption } from "./city-selected-fleet-panel";

/** Deterministic 64-hex digest-shaped string per seed; fixtures pin identity by value. */
function hex64(seed: string): string {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index++) {
    hash = Math.imul(hash ^ seed.charCodeAt(index), 16777619);
  }
  const block = (hash >>> 0).toString(16).padStart(8, "0");
  return block.repeat(8);
}

const sourceId = "test-city-osm-v1";
const sourceSha = hex64("raw-source");
const selection: SelectedSceneDraft["selection"] = {
  schema_version: "aero-bench.scene-selection/v1",
  source_id: sourceId,
  source_sha256: sourceSha,
  origin: { latitude_deg: 31.23, longitude_deg: 121.47,
    ellipsoid_height_m: 50, geoid_undulation_m: 30, amsl_m: 20 },
  bounds_enu_m: { min_east_m: -500, max_east_m: 500, min_north_m: -400, max_north_m: 400 },
};
const selectedScene: SelectedSceneDraft = {
  purpose: "selected-scene-authoring",
  schema_version: CITY_SELECTED_DRAFT_SCHEMA,
  selection,
  selection_sha256: hex64("selection"),
  job_id: hex64("job"),
  source_sha256: sourceSha,
  pack_manifest_sha256: hex64("pack"),
  presentation_manifest_sha256: hex64("presentation"),
};

const facility: SelectedScenarioFacility = {
  id: "fac-main", name: "主起降点", kind: "vertiport",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 0, z: 0 }, rotationDeg: 0,
  widthM: 12, depthM: 8, heightM: 4,
  landing: { parkingSlots: 2, movementsPerHour: 30 }, cargo: null,
  charging: { slots: 1, powerW: 50000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
};

const fleetEntry: SelectedScenarioFleetEntry = {
  id: "uav-1", assetId: "model:holybro-x500", count: 10,
  homeFacilityId: "fac-main", batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 2,
};

const assets: SelectedFleetAssetOption[] = [
  { id: "model:holybro-x500", title: "Holybro X500", subgroup: "多旋翼", preview: "四轴调研机" },
  { id: "model:custom-550", title: "Custom 550", subgroup: "多旋翼" },
];

function scenario(options: { fleet?: SelectedScenarioFleetEntry[];
                             demand?: SelectedScenarioDemand } = {}): CitySelectedScenario {
  return parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene,
    facilities: [facility],
    noFlyZones: [],
    fleet: options.fleet ?? [],
    demand: options.demand ?? { vehicles: 10, pedestrians: 5, bicycles: 2 },
  });
}

type Commit = (next: CitySelectedScenario) => Promise<string | null>;

function field(root: HTMLElement, label: string): HTMLLabelElement {
  for (const candidate of root.querySelectorAll<HTMLLabelElement>("label.studio-field")) {
    if (candidate.querySelector(".studio-field-label")?.textContent === label) return candidate;
  }
  throw new Error(`Missing field ${label}`);
}

function numberInput(root: HTMLElement, label: string): HTMLInputElement {
  const input = field(root, label).querySelector<HTMLInputElement>("input[type=number]");
  if (input === null) throw new Error(`Missing numeric input for ${label}`);
  return input;
}

function selectByLabel(root: HTMLElement, ariaLabel: string): HTMLSelectElement {
  const control = root.querySelector<HTMLSelectElement>(`select[aria-label="${ariaLabel}"]`);
  if (control === null) throw new Error(`Missing select ${ariaLabel}`);
  return control;
}

function buttonByText(root: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll<HTMLButtonElement>("button"))
    .find(candidate => candidate.textContent === text);
  if (button === undefined) throw new Error(`Missing button ${text}`);
  return button;
}

/** Flush the async commit chain (await onChange + re-render). */
async function settle(): Promise<void> {
  await new Promise(resolve => setTimeout(resolve, 0));
}

describe("selected-city fleet and demand panel", () => {
  beforeEach(() => document.body.replaceChildren());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("accepts adding a fleet entry with an available model and facility", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario(), onChange, assets);

    root.querySelector<HTMLButtonElement>('button[aria-label="添加机队条目"]')!.click();

    // Pick a different available model and a facility before confirming.
    const model = selectByLabel(root, "机型");
    model.value = "model:custom-550";
    model.dispatchEvent(new Event("change"));
    const home = selectByLabel(root, "驻地设施");
    home.value = facility.id;
    home.dispatchEvent(new Event("change"));

    buttonByText(root, "确认添加机队条目").click();
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].fleet).toHaveLength(1);
    expect(onChange.mock.calls[0]![0].fleet[0]).toMatchObject({
      assetId: "model:custom-550",
      count: 1,
      homeFacilityId: facility.id,
      batteryWh: 500,
      reserveRatio: 0.2,
      maxPayloadKg: 0,
    });

    // The add form is closed and the accepted entry is rendered in place.
    expect(root.querySelector('[data-fleet-add="form"]')).toBeNull();
    expect(root.textContent).toContain("机队 (1)");
    expect(selectByLabel(root, "机型").value).toBe("model:custom-550");
    expect(selectByLabel(root, "驻地设施").value).toBe(facility.id);
  });

  it("accepts editing an existing entry's model to another available asset", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario({ fleet: [fleetEntry] }), onChange, assets);

    const model = selectByLabel(root, "机型");
    model.value = "model:custom-550";
    model.dispatchEvent(new Event("change"));
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].fleet[0]).toMatchObject({
      assetId: "model:custom-550", count: 10, homeFacilityId: facility.id, batteryWh: 500,
    });
    expect(selectByLabel(root, "机型").value).toBe("model:custom-550");
  });

  it("keeps the prior count and surfaces the caller error when an edit is rejected", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => "机队数量超出运行库容量");
    renderSelectedFleetPanel(root, scenario({ fleet: [fleetEntry] }), onChange, assets);

    const count = numberInput(root, "数量");
    count.value = "5";
    count.dispatchEvent(new Event("change"));
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    const alert = root.querySelector<HTMLElement>('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert!.textContent).toBe("机队数量超出运行库容量");
    // The previously displayed scenario is retained.
    expect(numberInput(root, "数量").value).toBe("10");
  });

  it("explicitly rejects an empty numeric edit instead of silently normalising it", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario({ fleet: [fleetEntry] }), onChange, assets);

    const battery = numberInput(root, "电池电量 / Wh");
    battery.value = "";
    battery.dispatchEvent(new Event("change"));
    await settle();

    // parseCitySelectedScenario rejects NaN before the caller is ever consulted.
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')).not.toBeNull();
    expect(root.textContent).toContain("必须是有限数值");
    expect(numberInput(root, "电池电量 / Wh").value).toBe("500");
  });

  it("explicitly rejects a non-integer fleet count", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario({ fleet: [fleetEntry] }), onChange, assets);

    const count = numberInput(root, "数量");
    count.value = "2.5";
    count.dispatchEvent(new Event("change"));
    await settle();

    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("必须");
    expect(numberInput(root, "数量").value).toBe("10");
  });

  it("flags a saved entry whose model is missing without substituting another model", () => {
    const root = document.createElement("div");
    const ghost = { ...fleetEntry, assetId: "model:legacy-phantom", count: 3 };
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario({ fleet: [ghost] }), onChange, assets);

    // The saved assetId is surfaced as a mismatch, not replaced with a local asset.
    expect(root.textContent).toContain("model:legacy-phantom（素材缺失）");
    expect(root.textContent).toContain("不在本次本地可用素材中");
    expect(selectByLabel(root, "机型").value).toBe("model:legacy-phantom");
    const placeholder = Array.from(selectByLabel(root, "机型").querySelectorAll("option"))
      .find(option => option.textContent?.includes("素材缺失"));
    expect(placeholder?.value).toBe("model:legacy-phantom");

    // The panel exposes only the locally available assets — no fabricated model option.
    const offered = Array.from(selectByLabel(root, "机型").querySelectorAll("option"))
      .filter(option => option.value !== "model:legacy-phantom");
    expect(offered.map(option => option.value).sort()).toEqual(["model:custom-550", "model:holybro-x500"]);
  });

  it("commits asynchronous vehicle, pedestrian and bicycle count edits as they are accepted", async () => {
    const root = document.createElement("div");
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedFleetPanel(root, scenario(), onChange, assets);

    const vehicles = numberInput(root, "背景车辆数");
    vehicles.value = "12";
    vehicles.dispatchEvent(new Event("change"));
    await settle();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].demand.vehicles).toBe(12);
    expect(numberInput(root, "背景车辆数").value).toBe("12");

    const pedestrians = numberInput(root, "背景行人数量");
    pedestrians.value = "7";
    pedestrians.dispatchEvent(new Event("change"));
    await settle();
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange.mock.calls[1]![0].demand).toMatchObject({ vehicles: 12, pedestrians: 7, bicycles: 2 });

    const bicycles = numberInput(root, "背景自行车数量");
    bicycles.value = "4";
    bicycles.dispatchEvent(new Event("change"));
    await settle();
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(onChange.mock.calls[2]![0].demand).toMatchObject({ vehicles: 12, pedestrians: 7, bicycles: 4 });
    expect(numberInput(root, "背景自行车数量").value).toBe("4");
  });
});