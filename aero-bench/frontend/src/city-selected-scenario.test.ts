// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  type SceneSelection } from "./city-region-selector";
import { CITY_SELECTED_SCENARIO_SCHEMA, CITY_SELECTED_SCENARIO_STORAGE_KEY,
  createDefaultCitySelectedScenario, exportCitySelectedScenario, importCitySelectedScenario,
  loadCitySelectedScenario, parseCitySelectedScenario, saveCitySelectedScenario } from "./city-selected-scenario";

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

const content = {
  facilities: [{
    id: "vp.01", name: "vertiport 1", kind: "vertiport" as const,
    placement: "ground" as const, buildingId: null, supportHeightM: null,
    position: { x: 10, z: -20 }, rotationDeg: 0, widthM: 20, depthM: 20, heightM: 5,
    landing: { parkingSlots: 3, movementsPerHour: 30 }, cargo: null,
    charging: { slots: 1, powerW: 6000, priceAmount: 1.2, priceCurrency: "CNY", priceUnit: "kWh" as const },
  }],
  noFlyZones: [{
    id: "nfz.01", name: "hospital",
    polygon: [{ x: 0, z: 0 }, { x: 50, z: 0 }, { x: 50, z: 50 }, { x: 0, z: 50 }],
    floorM: 0, ceilingM: 120,
    startsAtS: 0, endsAtS: null,
    source: { kind: "manual" as const, label: "手工绘制", uri: null },
  }],
  fleet: [{
    id: "uav.01", assetId: "model:holybro-x500", count: 2, homeFacilityId: "vp.01",
    batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 1.5,
  }],
  demand: { vehicles: 12, pedestrians: 40, bicycles: 5 },
};

beforeEach(() => window.localStorage.clear());

describe("selected city scenario authoring contract", () => {
  it("roundtrips authoring content bound to exactly one selected scene", async () => {
    const empty = await createDefaultCitySelectedScenario(draft);
    expect(empty.selectedScene).toEqual(draft);
    expect(empty.facilities).toEqual([]);
    expect(empty.noFlyZones).toEqual([]);
    expect(empty.fleet).toEqual([]);
    expect(empty.demand).toEqual({ vehicles: 0, pedestrians: 0, bicycles: 0 });
    expect(empty.executable).toBe(false);
    expect(empty.schema_version).toBe(CITY_SELECTED_SCENARIO_SCHEMA);

    const scenario = parseCitySelectedScenario({ ...empty, ...content });
    const json = exportCitySelectedScenario(scenario);
    expect(JSON.parse(json)).toMatchObject({ purpose: "selected-scenario-authoring", executable: false });
    const imported = await importCitySelectedScenario(json);
    expect(imported).toEqual(scenario);
    await saveCitySelectedScenario(imported);
    expect(window.localStorage.getItem(CITY_SELECTED_SCENARIO_STORAGE_KEY)).not.toBeNull();
    expect(await loadCitySelectedScenario()).toEqual(imported);
  });

  it("rejects a tampered selected scene and never falls back to the old workspace schema", async () => {
    const scenario = parseCitySelectedScenario({ ...(await createDefaultCitySelectedScenario(draft)), ...content });
    const tampered = JSON.parse(exportCitySelectedScenario(scenario)) as typeof scenario;
    tampered.selectedScene = { ...tampered.selectedScene,
      selection: { ...tampered.selectedScene.selection, bounds_enu_m: {
        ...tampered.selectedScene.selection.bounds_enu_m, max_east_m: 779 } } };
    await expect(importCitySelectedScenario(JSON.stringify(tampered))).rejects.toThrow(/SceneSelection/);
    await expect(saveCitySelectedScenario(tampered)).rejects.toThrow(/SceneSelection/);
    window.localStorage.setItem(CITY_SELECTED_SCENARIO_STORAGE_KEY, JSON.stringify(tampered));
    await expect(loadCitySelectedScenario()).rejects.toThrow(/SceneSelection/);

    const oldWorkspace = JSON.parse(exportCitySelectedScenario(scenario)) as Record<string, unknown>;
    oldWorkspace.schema_version = "aero-bench.city-workspace/v1";
    oldWorkspace.scenePath = "/city-presentation/default-scene-v1.json";
    await expect(importCitySelectedScenario(JSON.stringify(oldWorkspace))).rejects.toThrow(/字段不符合协议|版本无效/);
    const fakeExecution = JSON.parse(exportCitySelectedScenario(scenario)) as Record<string, unknown>;
    fakeExecution.trajectories = [];
    await expect(importCitySelectedScenario(JSON.stringify(fakeExecution))).rejects.toThrow(/字段不符合协议/);
  });

  it("rejects invalid cross references and duplicate ids", async () => {
    const base = await createDefaultCitySelectedScenario(draft);
    const scenario = { ...base, ...content } as ReturnType<typeof parseCitySelectedScenario>;
    expect(() => parseCitySelectedScenario({ ...scenario,
      fleet: [{ ...content.fleet[0]!, homeFacilityId: "vp.404" }] })).toThrow(/未知设施/);
    expect(() => parseCitySelectedScenario({ ...scenario,
      fleet: [{ ...content.fleet[0]!, assetId: "bad asset" }] })).toThrow(/资产 ID 无效/);
    expect(parseCitySelectedScenario({ ...scenario,
      fleet: [{ ...content.fleet[0]!, assetId: "model:another-uav" }] }).fleet[0]?.assetId)
      .toBe("model:another-uav");
    expect(() => parseCitySelectedScenario({ ...scenario,
      facilities: [content.facilities[0]!, { ...content.facilities[0]!, name: "vertiport 2" }] })).toThrow(/ID 重复/);
    expect(() => parseCitySelectedScenario({ ...scenario,
      selectedScene: { ...draft, scenePath: "/city-presentation/default-scene-v1.json" } as never }))
      .toThrow(/字段不符合协议/);
  });

  it("rejects non-finite and out-of-unit numeric fields", async () => {
    const base = await createDefaultCitySelectedScenario(draft);
    const scenario = { ...base, ...content } as ReturnType<typeof parseCitySelectedScenario>;
    const withFleet = (fleet: unknown): unknown => ({ ...scenario, fleet });
    expect(() => parseCitySelectedScenario(withFleet([{ ...content.fleet[0]!, batteryWh: 0 }]))).toThrow(/电池电量/);
    expect(() => parseCitySelectedScenario(withFleet([{ ...content.fleet[0]!, reserveRatio: 1.5 }]))).toThrow(/备用比例/);
    expect(() => parseCitySelectedScenario(withFleet([{ ...content.fleet[0]!, count: 2.5 }]))).toThrow(/机队数量/);
    expect(() => parseCitySelectedScenario(withFleet([{ ...content.fleet[0]!, maxPayloadKg: -1 }]))).toThrow(/最大载荷/);
    expect(() => parseCitySelectedScenario({ ...scenario,
      demand: { vehicles: -1, pedestrians: 0, bicycles: 0 } })).toThrow(/背景车辆数/);
    expect(() => parseCitySelectedScenario({ ...scenario, facilities: [{ ...content.facilities[0]!,
      position: { x: Number.NaN, z: 0 } }] })).toThrow(/有限数值/);
    expect(() => parseCitySelectedScenario({ ...scenario, facilities: [{ ...content.facilities[0]!,
      widthM: 1 }] })).toThrow(/最小尺寸/);
    expect(() => parseCitySelectedScenario({ ...scenario, noFlyZones: [{ ...content.noFlyZones[0]!,
      ceilingM: 0 }] })).toThrow(/禁飞区/);
    expect(() => parseCitySelectedScenario({ ...scenario, noFlyZones: [{ ...content.noFlyZones[0]!,
      polygon: [{ x: 0, z: 0 }, { x: 50, z: 0 }] }] })).toThrow(/禁飞区/);
    expect(() => parseCitySelectedScenario({ ...scenario, noFlyZones: [{ ...content.noFlyZones[0]!,
      startsAtS: 10, endsAtS: 9 }] })).toThrow(/结束时间/);
    expect(() => parseCitySelectedScenario({ ...scenario, noFlyZones: [{ ...content.noFlyZones[0]!,
      source: { kind: "unknown", label: "x", uri: null } }] })).toThrow(/来源类型/);
  });
});
