// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import {
  CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioFacility, type SelectedScenarioFleetEntry,
} from "./city-selected-scenario";
import {
  CITY_SELECTED_LOGISTICS_SCHEMA, parseCitySelectedLogistics,
  type CitySelectedLogistics, type FleetPerformanceProfile,
} from "./city-selected-logistics-draft";
import { renderSelectedLogisticsPanel } from "./city-selected-logistics-panel";
import { planSelectedDispatch, type DispatchPlan } from "./city-selected-dispatch";

/** Deterministic 64-hex digest-shaped string per seed; fixtures pin identity by value. */
function hex64(seed: string): string {
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index++) {
    hash = Math.imul(hash ^ seed.charCodeAt(index), 16777619);
  }
  const block = (hash >>> 0).toString(16).padStart(8, "0");
  return block.repeat(8);
}

function scene(job: string): SelectedSceneDraft {
  return {
    purpose: "selected-scene-authoring",
    schema_version: CITY_SELECTED_DRAFT_SCHEMA,
    selection: {
      schema_version: "aero-bench.scene-selection/v1",
      source_id: "test-city-osm-v1",
      source_sha256: hex64("raw-source"),
      origin: { latitude_deg: 31.23, longitude_deg: 121.47,
        ellipsoid_height_m: 50, geoid_undulation_m: 30, amsl_m: 20 },
      bounds_enu_m: { min_east_m: -500, max_east_m: 500, min_north_m: -400, max_north_m: 400 },
    },
    selection_sha256: hex64(`selection-${job}`),
    job_id: hex64(job),
    source_sha256: hex64("raw-source"),
    pack_manifest_sha256: hex64("pack"),
    presentation_manifest_sha256: hex64("presentation"),
  };
}

const selectedScene = scene("job-a");
const otherScene = scene("job-b");

const vertiport: SelectedScenarioFacility = {
  id: "fac-main", name: "主起降点", kind: "vertiport",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 0, z: 0 }, rotationDeg: 0,
  widthM: 12, depthM: 8, heightM: 4,
  landing: { parkingSlots: 1, movementsPerHour: 30 }, cargo: null,
  charging: { slots: 1, powerW: 50000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
};
const hub: SelectedScenarioFacility = {
  id: "fac-hub", name: "集散中转站", kind: "hub",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 200, z: 0 }, rotationDeg: 0,
  widthM: 16, depthM: 12, heightM: 6,
  landing: { parkingSlots: 2, movementsPerHour: 20 },
  cargo: { storageCapacityKg: 10000, throughputPerHourKg: 10000 }, charging: null,
};
const charger: SelectedScenarioFacility = {
  id: "fac-charge", name: "东侧充电站", kind: "charger",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: -200, z: 0 }, rotationDeg: 0,
  widthM: 10, depthM: 8, heightM: 4,
  landing: null, cargo: null,
  charging: { slots: 3, powerW: 8000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
};

const fleetEntry: SelectedScenarioFleetEntry = {
  id: "uav-1", assetId: "model:holybro-x500", count: 10,
  homeFacilityId: "fac-main", batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 2,
};
const secondEntry: SelectedScenarioFleetEntry = {
  id: "uav-2", assetId: "model:holybro-x500", count: 2,
  homeFacilityId: null, batteryWh: 400, reserveRatio: 0.25, maxPayloadKg: 1,
};

const fleetProfile: FleetPerformanceProfile = {
  fleetEntryId: fleetEntry.id,
  sourceLabel: "运营方",
  provenance: "厂商样张",
  aircraftBody: { xM: 1.2, yM: 0.3, zM: 1.2 },
  cruiseSpeedMps: 18,
  cruisePowerW: 420,
  hoverPowerW: 380,
  chargeEfficiency: 0.9,
};

const OCI = `registry.example/agent@sha256:${"a".repeat(64)}`;

function scenario(options: { fleet?: SelectedScenarioFleetEntry[];
                             scene?: SelectedSceneDraft } = {}): CitySelectedScenario {
  return parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene: options.scene ?? selectedScene,
    facilities: [vertiport, hub, charger],
    noFlyZones: [],
    fleet: options.fleet ?? [fleetEntry],
    demand: { vehicles: 10, pedestrians: 5, bicycles: 2 },
  });
}

type Commit = (next: CitySelectedLogistics) => Promise<string | null>;

function logistics(scn: CitySelectedScenario,
                   options: { orders?: CitySelectedLogistics["orders"];
                              profiles?: CitySelectedLogistics["performanceProfiles"];
                              algorithms?: CitySelectedLogistics["algorithms"];
                              orderGeneration?: CitySelectedLogistics["orderGeneration"] } = {}): CitySelectedLogistics {
  return parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring",
    schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false,
    selectedScene: scn.selectedScene,
    orders: options.orders ?? [],
    performanceProfiles: options.profiles ?? [],
    algorithms: options.algorithms ?? {
      mode: "centralized", assignment: "nearest_feasible", routing: "grid_astar",
      charging: "reserve_threshold", externalImageRef: null, parameters: {},
    },
    orderGeneration: options.orderGeneration ?? {
      seed: 42, maxOrders: 0, startAtS: 0, endAtS: 3600, cargoMinKg: 0.1, cargoMaxKg: 1,
      deadlineLeadS: 600,
    },
  }, scn);
}

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

function setSelect(select: HTMLSelectElement, value: string): void {
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

function setNumber(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event("change"));
}

function setText(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event("change"));
}

/** Flush the async commit chain (await onChange + re-render). */
async function settle(): Promise<void> {
  await new Promise(resolve => setTimeout(resolve, 0));
}

describe("selected-city logistics authoring panel", () => {
  beforeEach(() => document.body.replaceChildren());
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("allows successive repairs when more than one order refers to a removed facility", async () => {
    const root = document.createElement("div");
    const original = scenario();
    const doc = logistics(original, { orders: ["a", "b"].map(id => ({ id,
      sourceFacilityId: vertiport.id, destinationFacilityId: hub.id, hubHandoffFacilityId: null,
      cargoKg: 1, releaseAtS: 0, deliverByS: 600 })) });
    const changed = { ...original, facilities: original.facilities.filter(site => site.id !== hub.id) };
    const commit = vi.fn<Commit>(async () => null);
    renderSelectedLogisticsPanel(root, changed, doc, commit);
    buttonByText(root, "删除订单").click();
    await settle();
    expect(commit).toHaveBeenCalledTimes(1);
    expect(commit.mock.calls[0]![0].orders.map(order => order.id)).toEqual(["b"]);
    expect(root.textContent).toContain("修复进度已保存");
    buttonByText(root, "删除订单").click();
    await settle();
    expect(commit).toHaveBeenCalledTimes(2);
    expect(commit.mock.calls[1]![0].orders).toEqual([]);
  });

  it("invalidates a displayed plan after an accepted edit and never overwrites another tab", async () => {
    const root = document.createElement("div");
    const scn = scenario(), doc = logistics(scn);
    const plan = planSelectedDispatch({ scenario: scn, logistics: doc, units: [],
      routeEstimator: () => { throw new Error("No route should be requested for empty orders"); } });
    renderSelectedLogisticsPanel(root, scn, doc, async () => null, async () => plan);
    buttonByText(root, "预览派单规划").click();
    await settle();
    expect(root.textContent).toContain("规划：0 单可分配");
    setNumber(numberInput(root, "随机种子 seed"), "123");
    await settle();
    expect(root.textContent).not.toContain("规划：0 单可分配");
    let complete!: (value: DispatchPlan) => void;
    renderSelectedLogisticsPanel(root, scn, doc, async () => null,
      () => new Promise(resolve => { complete = resolve; }));
    buttonByText(root, "预览派单规划").click();
    root.replaceChildren(document.createTextNode("other tab"));
    complete(plan);
    await settle();
    expect(root.textContent).toBe("other tab");
  });

  it("accepts adding a manual cargo-transfer order through the acceptance gate", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scn, logistics(scn), onChange);

    root.querySelector<HTMLButtonElement>('button[aria-label="添加手动订单"]')!.click();
    const form = root.querySelector<HTMLElement>('[data-order-add="form"]');
    expect(form).not.toBeNull();
    setSelect(selectByLabel(form!, "来源设施"), vertiport.id);
    setSelect(selectByLabel(form!, "目的设施"), hub.id);
    setNumber(numberInput(form!, "货物重量 / kg"), "1.5");
    setNumber(numberInput(form!, "释放时间 / s"), "0");
    setNumber(numberInput(form!, "交付期限 / s"), "900");
    buttonByText(form!, "确认添加订单").click();
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    const accepted = onChange.mock.calls[0]![0];
    expect(accepted.orders).toHaveLength(1);
    expect(accepted.orders[0]).toMatchObject({
      id: "order-1",
      sourceFacilityId: vertiport.id,
      destinationFacilityId: hub.id,
      cargoKg: 1.5,
      releaseAtS: 0,
      deliverByS: 900,
    });
    // The add form is closed and the accepted entry is rendered in place.
    expect(root.querySelector('[data-order-add="form"]')).toBeNull();
    expect(root.textContent).toContain("订单请求 (1)");
  });

  it("accepts a complete manual performance profile and persists it", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scn, logistics(scn), onChange);

    // The missing-profile notice is visible before the profile exists.
    expect(root.textContent).toContain("尚无性能档案");
    buttonByText(root, "添加性能档案").click();
    const card = root.querySelector<HTMLElement>('[data-fleet-entry-id="uav-1"]')!;
    setText(field(card, "来源标签").querySelector<HTMLInputElement>("input")!, "运营方");
    setText(field(card, "出处").querySelector<HTMLInputElement>("input")!, "厂商样张");
    setNumber(numberInput(card, "机体长度 x / m"), "1.2");
    setNumber(numberInput(card, "机体高度 y / m"), "0.3");
    setNumber(numberInput(card, "机体宽度 z / m"), "1.2");
    setNumber(numberInput(card, "巡航速度 / (m/s)"), "18");
    setNumber(numberInput(card, "巡航功率 / W"), "420");
    setNumber(numberInput(card, "悬停功率 / W"), "380");
    setNumber(numberInput(card, "充电效率 / 0–1"), "0.9");
    buttonByText(card, "确认添加性能档案").click();
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0]![0].performanceProfiles).toHaveLength(1);
    expect(onChange.mock.calls[0]![0].performanceProfiles[0]).toMatchObject({
      fleetEntryId: "uav-1",
      sourceLabel: "运营方",
      provenance: "厂商样张",
      aircraftBody: { xM: 1.2, yM: 0.3, zM: 1.2 },
      cruiseSpeedMps: 18,
      cruisePowerW: 420,
      hoverPowerW: 380,
      chargeEfficiency: 0.9,
    });
    expect(root.textContent).toContain("已建档");
    expect(buttonByText(root, "删除性能档案")).toBeDefined();
  });

  it("keeps a profile absent until the operator supplies every field", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scn, logistics(scn), onChange);

    buttonByText(root, "添加性能档案").click();
    const card = root.querySelector<HTMLElement>('[data-fleet-entry-id="uav-1"]')!;
    setText(field(card, "来源标签").querySelector<HTMLInputElement>("input")!, "运营方");
    // Leave provenance and every numeric field empty: the parser must reject the
    // partial profile instead of the panel pretending it completed.
    buttonByText(card, "确认添加性能档案").click();
    await settle();

    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')).not.toBeNull();
    expect(root.textContent).toContain("尚无性能档案");
  });

  it("accepts algorithm choice edits and requires a pinned OCI reference for external choices", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scn, logistics(scn), onChange);

    setSelect(selectByLabel(root, "调度模式"), "distributed");
    await settle();
    expect(onChange.mock.calls[0]![0].algorithms.mode).toBe("distributed");

    // Selecting an external choice with no pinned reference is rejected and the
    // prior assignment choice is retained.
    setSelect(selectByLabel(root, "订单分配"), "external");
    await settle();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("外部镜像引用");
    expect(selectByLabel(root, "订单分配").value).toBe("nearest_feasible");

    // Providing a digest-pinned reference makes the external choice valid.
    setText(field(root, "外部算法镜像引用").querySelector<HTMLInputElement>("input")!, OCI);
    await settle();
    setSelect(selectByLabel(root, "订单分配"), "external");
    await settle();
    expect(onChange).toHaveBeenCalledTimes(3);
    expect(onChange.mock.calls[2]![0].algorithms).toMatchObject({
      assignment: "external",
      externalImageRef: OCI,
      mode: "distributed",
    });
  });

  it("preserves the prior document and shows the returned message verbatim when a mutation is rejected", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const base = logistics(scn, {
      orders: [{ id: "order-1", sourceFacilityId: vertiport.id, destinationFacilityId: hub.id,
        hubHandoffFacilityId: null, cargoKg: 1.5, releaseAtS: 0, deliverByS: 900 }],
    });
    const onChange = vi.fn<Commit>(async _next => "业务方已锁定该订单");
    renderSelectedLogisticsPanel(root, scn, base, onChange);

    setNumber(numberInput(root.querySelector<HTMLElement>('[data-order-id="order-1"]')!, "货物重量 / kg"), "2");
    await settle();

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(root.querySelector('[role="alert"]')!.textContent).toBe("业务方已锁定该订单");
    expect(numberInput(root.querySelector<HTMLElement>('[data-order-id="order-1"]')!, "货物重量 / kg").value).toBe("1.5");
  });

  it("never offers a charger as a cargo order source or destination", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    renderSelectedLogisticsPanel(root, scn, logistics(scn), vi.fn<Commit>(async _next => null));

    root.querySelector<HTMLButtonElement>('button[aria-label="添加手动订单"]')!.click();
    const form = root.querySelector<HTMLElement>('[data-order-add="form"]')!;
    const sourceValues = Array.from(selectByLabel(form, "来源设施").querySelectorAll("option")).map(option => option.value);
    const destinationValues = Array.from(selectByLabel(form, "目的设施").querySelectorAll("option")).map(option => option.value);
    expect(sourceValues).not.toContain(charger.id);
    expect(destinationValues).not.toContain(charger.id);
    expect(sourceValues.sort()).toEqual([hub.id, vertiport.id].sort());
  });

  it("surfaces missing-performance-profile notices for fleet entries without a profile", () => {
    const root = document.createElement("div");
    const scn = scenario({ fleet: [fleetEntry, secondEntry] });
    const doc = logistics(scn, { profiles: [fleetProfile] });
    renderSelectedLogisticsPanel(root, scn, doc, vi.fn<Commit>(async _next => null));

    // The whole-list notice names the unprofiled entry.
    expect(root.textContent).toContain(secondEntry.id);
    expect(root.textContent).toContain("尚无性能档案");
    // The unprofiled card offers to create a profile; the profiled card edits instead.
    const missingCard = root.querySelector<HTMLElement>(`[data-fleet-entry-id="${secondEntry.id}"]`)!;
    expect(buttonByText(missingCard, "添加性能档案")).toBeDefined();
    const profiledCard = root.querySelector<HTMLElement>(`[data-fleet-entry-id="${fleetEntry.id}"]`)!;
    expect(buttonByText(profiledCard, "删除性能档案")).toBeDefined();
  });

  it("appends deterministic generated order requests and rejects duplicate generated IDs", async () => {
    const root = document.createElement("div");
    const scn = scenario();
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scn, logistics(scn), onChange);

    setNumber(numberInput(root, "最大订单数"), "3");
    await settle();
    expect(onChange.mock.calls[0]![0].orderGeneration.maxOrders).toBe(3);

    buttonByText(root, "生成订单请求").click();
    await settle();
    expect(onChange).toHaveBeenCalledTimes(2);
    const accepted = onChange.mock.calls[1]![0];
    expect(accepted.orders).toHaveLength(3);
    expect(accepted.orders.map(order => order.id)).toEqual(["generated-42-1", "generated-42-2", "generated-42-3"]);
    for (const order of accepted.orders) {
      expect(order.sourceFacilityId).not.toBe(charger.id);
      expect(order.destinationFacilityId).not.toBe(charger.id);
    }

    // Regenerating with the same seed would recreate the same IDs: rejected.
    buttonByText(root, "生成订单请求").click();
    await settle();
    expect(onChange).toHaveBeenCalledTimes(2);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("冲突");
  });

  it("refuses to save a logistics edit against a changed selected city", async () => {
    const root = document.createElement("div");
    const scnA = scenario();
    const scnB = scenario({ scene: otherScene });
    const onChange = vi.fn<Commit>(async _next => null);
    renderSelectedLogisticsPanel(root, scnA, logistics(scnA), onChange);

    // The caller swaps to a different selected city while reusing the same root.
    renderSelectedLogisticsPanel(root, scnB, logistics(scnA), onChange);
    setNumber(numberInput(root, "最大订单数"), "2");
    await settle();

    // The binding check rejects before the caller is consulted; no stale save.
    expect(onChange).not.toHaveBeenCalled();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("与当前城市不一致");
  });
});
