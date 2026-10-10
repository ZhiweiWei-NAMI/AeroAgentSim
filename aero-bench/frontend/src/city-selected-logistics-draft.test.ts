// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  type SceneSelection } from "./city-region-selector";
import { CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario,
  type CitySelectedScenario, type SelectedScenarioFacility, type SelectedScenarioFleetEntry } from "./city-selected-scenario";
import {
  CITY_SELECTED_LOGISTICS_SCHEMA, CITY_SELECTED_LOGISTICS_STORAGE_KEY,
  createDefaultCitySelectedLogistics, exportCitySelectedLogistics, importCitySelectedLogistics,
  loadCitySelectedLogistics, missingPerformanceProfiles, parseCitySelectedLogistics,
  parseCitySelectedLogisticsForRepair,
  saveCitySelectedLogistics, type CitySelectedLogistics,
  type FleetPerformanceProfile, type LogisticsAlgorithms, type LogisticsOrderGeneration,
  type LogisticsOrderRequest,
} from "./city-selected-logistics-draft";

const selection: SceneSelection = {
  schema_version: "aero-bench.scene-selection/v1",
  source_id: SHANGHAI_SOURCE_ID,
  source_sha256: SHANGHAI_SOURCE_SHA256,
  origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
};
// Published by the real authoring service for the selection above.
const selectionSha = "7d5904868d1f437c377574e267962a7152193fb38b8e1d80b89f6b822abcb09c";
const draft: SelectedSceneDraft = {
  purpose: "selected-scene-authoring",
  schema_version: CITY_SELECTED_DRAFT_SCHEMA,
  selection,
  selection_sha256: selectionSha,
  job_id: "1".repeat(64),
  source_sha256: SHANGHAI_SOURCE_SHA256,
  pack_manifest_sha256: "2".repeat(64),
  presentation_manifest_sha256: "3".repeat(64),
};

const vertiport: SelectedScenarioFacility = {
  id: "vp.01", name: "vertiport 1", kind: "vertiport",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 10, z: -20 }, rotationDeg: 0, widthM: 20, depthM: 20, heightM: 5,
  landing: { parkingSlots: 3, movementsPerHour: 30 }, cargo: null,
  charging: { slots: 2, powerW: 6000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
};
const hub: SelectedScenarioFacility = {
  id: "hub.01", name: "cargo hub 1", kind: "hub",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 30, z: -40 }, rotationDeg: 0, widthM: 18, depthM: 14, heightM: 5.2,
  landing: { parkingSlots: 2, movementsPerHour: 20 },
  cargo: { storageCapacityKg: 10000, throughputPerHourKg: 10000 }, charging: null,
};
const charger: SelectedScenarioFacility = {
  id: "ch.01", name: "charger 1", kind: "charger",
  placement: "ground", buildingId: null, supportHeightM: null,
  position: { x: 50, z: -60 }, rotationDeg: 0, widthM: 10, depthM: 8, heightM: 3.2,
  landing: null, cargo: null,
  charging: { slots: 1, powerW: 5000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" },
};
const fleet: SelectedScenarioFleetEntry[] = [
  { id: "uav.01", assetId: "model:holybro-x500", count: 2, homeFacilityId: "vp.01",
    batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 1.5 },
  { id: "uav.02", assetId: "model:holybro-x500", count: 1, homeFacilityId: "hub.01",
    batteryWh: 1200, reserveRatio: 0.25, maxPayloadKg: 5 },
];

const scenario: CitySelectedScenario = parseCitySelectedScenario({
  purpose: "selected-scenario-authoring",
  schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
  executable: false,
  selectedScene: draft,
  facilities: [vertiport, hub, charger],
  noFlyZones: [],
  fleet,
  demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
});

const defaultAlgorithms: LogisticsAlgorithms = {
  mode: "centralized", assignment: "nearest_feasible", routing: "grid_astar",
  charging: "reserve_threshold", externalImageRef: null, parameters: {},
};
const defaultGeneration: LogisticsOrderGeneration = {
  seed: 42, maxOrders: 24, startAtS: 0, endAtS: 7200, cargoMinKg: 0.1, cargoMaxKg: 1.5,
  deadlineLeadS: 900,
};
const orderLight: LogisticsOrderRequest = {
  id: "ord.01", sourceFacilityId: "vp.01", destinationFacilityId: "hub.01",
  hubHandoffFacilityId: null, cargoKg: 0.8, releaseAtS: 0, deliverByS: 1800,
};
const orderHeavy: LogisticsOrderRequest = {
  id: "ord.02", sourceFacilityId: "hub.01", destinationFacilityId: "vp.01",
  hubHandoffFacilityId: null, cargoKg: 3.2, releaseAtS: 120, deliverByS: 3600,
};
const profileLight: FleetPerformanceProfile = {
  fleetEntryId: "uav.01", sourceLabel: "操作员声明", provenance: "按厂商样张估算，未经实飞验证",
  aircraftBody: { xM: 0.52, yM: 0.24, zM: 0.52 },
  cruiseSpeedMps: 12, cruisePowerW: 120, hoverPowerW: 85, chargeEfficiency: 0.9,
};

function contentDoc(overrides: Partial<CitySelectedLogistics> = {}): CitySelectedLogistics {
  return parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring",
    schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false,
    selectedScene: draft,
    orders: [orderLight, orderHeavy],
    performanceProfiles: [profileLight],
    algorithms: defaultAlgorithms,
    orderGeneration: defaultGeneration,
    ...overrides,
  }, scenario);
}

beforeEach(() => window.localStorage.clear());

describe("selected city logistics authoring contract", () => {
  it("roundtrips authoring content bound to exactly one selected scene", async () => {
    const empty = await createDefaultCitySelectedLogistics(scenario);
    expect(empty.selectedScene).toEqual(draft);
    expect(empty.orders).toEqual([]);
    expect(empty.performanceProfiles).toEqual([]);
    expect(empty.executable).toBe(false);
    expect(empty.schema_version).toBe(CITY_SELECTED_LOGISTICS_SCHEMA);
    expect(missingPerformanceProfiles(empty, scenario)).toEqual(["uav.01", "uav.02"]);

    const doc = contentDoc();
    expect(missingPerformanceProfiles(doc, scenario)).toEqual(["uav.02"]);
    const json = exportCitySelectedLogistics(doc, scenario);
    expect(JSON.parse(json)).toMatchObject({ purpose: "selected-logistics-authoring", executable: false });
    const imported = await importCitySelectedLogistics(json, scenario);
    expect(imported).toEqual(doc);
    await saveCitySelectedLogistics(imported, scenario);
    expect(window.localStorage.getItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY)).not.toBeNull();
    expect(await loadCitySelectedLogistics(scenario)).toEqual(imported);
  });

  it("rejects a tampered selected scene digest and a foreign selected job", async () => {
    // A scenario whose selectedScene identity still matches but whose bound
    // digest no longer verifies must fail import, save and load.
    const bogusScenario = parseCitySelectedScenario({
      purpose: "selected-scenario-authoring",
      schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
      executable: false,
      selectedScene: { ...draft, selection_sha256: "0".repeat(64) },
      facilities: [vertiport, hub, charger], noFlyZones: [], fleet,
      demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
    });
    const tampered = contentDoc();
    const tamperedDoc = parseCitySelectedLogistics({ ...tampered,
      selectedScene: bogusScenario.selectedScene }, bogusScenario);
    await expect(importCitySelectedLogistics(JSON.stringify(tamperedDoc), bogusScenario))
      .rejects.toThrow(/SceneSelection/);
    await expect(saveCitySelectedLogistics(tamperedDoc, bogusScenario)).rejects.toThrow(/SceneSelection/);
    window.localStorage.setItem(CITY_SELECTED_LOGISTICS_STORAGE_KEY, JSON.stringify(tamperedDoc));
    await expect(loadCitySelectedLogistics(bogusScenario)).rejects.toThrow(/SceneSelection/);

    // A document bound to a different selected city/job must never be reused.
    const foreign = { ...contentDoc(), selectedScene: { ...draft, job_id: "9".repeat(64) } };
    expect(() => parseCitySelectedLogistics(foreign, scenario)).toThrow(/与当前城市不一致/);
    await expect(importCitySelectedLogistics(JSON.stringify(foreign), scenario))
      .rejects.toThrow(/不一致/);
  });

  it("rejects the old workspace schema, wrong purpose and executable documents", async () => {
    const doc = contentDoc();
    const raw = JSON.parse(exportCitySelectedLogistics(doc, scenario)) as Record<string, unknown>;
    raw.schema_version = "aero-bench.city-workspace/v1";
    raw.scenePath = "/city-presentation/default-scene-v1.json";
    await expect(importCitySelectedLogistics(JSON.stringify(raw), scenario)).rejects.toThrow(/字段不符合协议|版本无效/);
    const renamed = { ...doc, purpose: "selected-scenario-authoring" };
    expect(() => parseCitySelectedLogistics(renamed, scenario)).toThrow(/purpose 无效/);
    const execution = { ...doc, executable: true };
    expect(() => parseCitySelectedLogistics(execution, scenario)).toThrow(/不可执行/);
    const extra = { ...doc, trajectories: [] };
    expect(() => parseCitySelectedLogistics(extra, scenario)).toThrow(/字段不符合协议/);
  });

  it("rejects duplicate order ids, unknown facilities and non-transfer facilities", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [orderLight, { ...orderLight, id: "ord.01" }] }, scenario)).toThrow(/订单 ID 重复/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, sourceFacilityId: "vp.404" }] }, scenario)).toThrow(/未知来源设施/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, destinationFacilityId: "vp.404" }] }, scenario)).toThrow(/未知目的设施/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, destinationFacilityId: "vp.01" }] }, scenario)).toThrow(/起终点必须不同/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, destinationFacilityId: "ch.01" }] }, scenario)).toThrow(/不允许货物交接/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, sourceFacilityId: "ch.01" }] }, scenario)).toThrow(/不允许货物交接/);
  });

  it("keeps status and evidence fields out of authoring order requests", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, status: "accepted" as never }] }, scenario)).toThrow(/字段不符合协议/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, evidence: {} as never }] }, scenario)).toThrow(/字段不符合协议/);
  });

  it("requires cargo to fit at least one configured fleet entry", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderHeavy, cargoKg: 5.2 }] }, scenario)).toThrow(/超出所有配置机队/);
    const noFleet = parseCitySelectedScenario({
      purpose: "selected-scenario-authoring", schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
      executable: false, selectedScene: draft,
      facilities: [vertiport, hub], noFlyZones: [], fleet: [],
      demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
    });
    expect(() => parseCitySelectedLogistics({ ...contentDoc(), selectedScene: draft,
      orders: [orderLight] }, noFleet)).toThrow(/超出所有配置机队/);
  });

  it("rejects non-finite and out-of-unit order fields", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, cargoKg: 0 }] }, scenario)).toThrow(/货物重量/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, deliverByS: 0 }] }, scenario)).toThrow(/交付期限/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, releaseAtS: Number.NaN }] }, scenario)).toThrow(/有限数值/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orders: [{ ...orderLight, deliverByS: "900" as never }] }, scenario)).toThrow(/有限数值/);
  });

  it("validates performance profiles and exposes missing profiles", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, fleetEntryId: "uav.404" }] }, scenario))
      .toThrow(/未知机队条目/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [profileLight, { ...profileLight, provenance: "另一份声明" }] }, scenario))
      .toThrow(/机队条目重复/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, chargeEfficiency: 0 }] }, scenario)).toThrow(/充电效率/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, chargeEfficiency: 1.2 }] }, scenario)).toThrow(/充电效率/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, cruiseSpeedMps: 0 }] }, scenario)).toThrow(/巡航速度/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, hoverPowerW: -1 }] }, scenario)).toThrow(/悬停功率/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(), performanceProfiles: [{
      ...profileLight, aircraftBody: { ...profileLight.aircraftBody, xM: 0 } }] }, scenario))
      .toThrow(/机体长度/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      performanceProfiles: [{ ...profileLight, sourceLabel: "" }] }, scenario)).toThrow(/来源标签/);
  });

  it("validates algorithm choices and digest-pinned external image references", async () => {
    const external = { ...contentDoc(), algorithms: { ...defaultAlgorithms, mode: "distributed" } };
    expect(parseCitySelectedLogistics(external, scenario).algorithms.mode).toBe("distributed");

    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, assignment: "external" } }, scenario)).toThrow(/外部镜像引用/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, routing: "external" } }, scenario)).toThrow(/外部镜像引用/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, charging: "external" } }, scenario)).toThrow(/外部镜像引用/);

    const pinned = "registry.example/aero-bench/planner:v1@sha256:"
      + "a".repeat(64);
    const withImage = { ...contentDoc(), algorithms: { ...defaultAlgorithms, routing: "external",
      externalImageRef: pinned, parameters: { gridStepM: 5 } } };
    expect(parseCitySelectedLogistics(withImage, scenario).algorithms.externalImageRef).toBe(pinned);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, routing: "external",
        externalImageRef: "registry.example/planner:v1" } }, scenario)).toThrow(/OCI 引用/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, parameters: { nest: { a: 1 } } } }, scenario))
      .toThrow(/有限原始值/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      algorithms: { ...defaultAlgorithms, parameters: { step: Number.NaN } } }, scenario))
      .toThrow(/有限原始值/);
  });

  it("validates the deterministic order-generation configuration", async () => {
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orderGeneration: { ...defaultGeneration, endAtS: 0 } }, scenario)).toThrow(/结束时间/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orderGeneration: { ...defaultGeneration, seed: -1 } }, scenario)).toThrow(/随机种子/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orderGeneration: { ...defaultGeneration, maxOrders: 2.5 } }, scenario)).toThrow(/最大订单数/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orderGeneration: { ...defaultGeneration, cargoMaxKg: 0.05 } }, scenario)).toThrow(/最大货物重量/);
    expect(() => parseCitySelectedLogistics({ ...contentDoc(),
      orderGeneration: { ...defaultGeneration, deadlineLeadS: 0 } }, scenario)).toThrow(/交付期限超前/);
  });

  it("keeps same-city invalid references editable after a facility change", () => {
    const changed = parseCitySelectedScenario({ ...scenario,
      facilities: scenario.facilities.filter(facility => facility.id !== orderLight.destinationFacilityId),
      fleet: scenario.fleet.map(entry => entry.homeFacilityId === orderLight.destinationFacilityId
        ? { ...entry, homeFacilityId: vertiport.id } : entry) });
    const draftWithOrder = { ...contentDoc(), orders: [orderLight] };
    expect(() => parseCitySelectedLogistics(draftWithOrder, changed)).toThrow(/未知目的设施/);
    const repair = parseCitySelectedLogisticsForRepair(draftWithOrder, changed);
    expect(repair.orders).toEqual([orderLight]);
    expect(parseCitySelectedLogistics({ ...repair, orders: [] }, changed).orders).toEqual([]);
    expect(() => parseCitySelectedLogisticsForRepair({ ...draftWithOrder,
      selectedScene: { ...draft, job_id: "9".repeat(64) } }, changed)).toThrow(/场景与当前城市不一致/);
  });
});
