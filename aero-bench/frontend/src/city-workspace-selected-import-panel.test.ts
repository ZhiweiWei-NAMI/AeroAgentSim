// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import {
  CITY_SELECTED_LOGISTICS_SCHEMA, parseCitySelectedLogistics, type CitySelectedLogistics,
} from "./city-selected-logistics-draft";
import {
  CITY_SELECTED_SCENARIO_SCHEMA, parseCitySelectedScenario, type CitySelectedScenario,
} from "./city-selected-scenario";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  type SceneSelection } from "./city-region-selector";
import type { PreparedSelectedLogisticsImport } from "./city-workspace-selected-import";
import { renderCityWorkspaceSelectedImportPanel } from "./city-workspace-selected-import-panel";

const selection: SceneSelection = {
  schema_version: "aero-bench.scene-selection/v1", source_id: SHANGHAI_SOURCE_ID,
  source_sha256: SHANGHAI_SOURCE_SHA256, origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
};
const selectedScene: SelectedSceneDraft = {
  purpose: "selected-scene-authoring", schema_version: CITY_SELECTED_DRAFT_SCHEMA, selection,
  selection_sha256: "7d5904868d1f437c377574e267962a7152193fb38b8e1d80b89f6b822abcb09c",
  job_id: "1".repeat(64), source_sha256: SHANGHAI_SOURCE_SHA256,
  pack_manifest_sha256: "2".repeat(64), presentation_manifest_sha256: "3".repeat(64),
};

function source(): { readonly scenario: CitySelectedScenario; readonly logistics: CitySelectedLogistics } {
  const scenario = parseCitySelectedScenario({
    purpose: "selected-scenario-authoring", schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false, selectedScene,
    facilities: [{ id: "hub.01", name: "hub", kind: "hub", placement: "ground",
      buildingId: null, supportHeightM: null, position: { x: 0, z: 0 }, rotationDeg: 0,
      widthM: 18, depthM: 14, heightM: 5.2,
      landing: { parkingSlots: 2, movementsPerHour: 20 },
      cargo: { storageCapacityKg: 100, throughputPerHourKg: 50 }, charging: null }],
    noFlyZones: [],
    fleet: [{ id: "uav.01", assetId: "model:holybro-x500", count: 1,
      homeFacilityId: "hub.01", batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 2 }],
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
  const logistics = parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring", schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false, selectedScene,
    orders: [], performanceProfiles: [],
    algorithms: { mode: "centralized", assignment: "external", routing: "external",
      charging: "external", externalImageRef: `registry.example/agent@sha256:${"a".repeat(64)}`,
      parameters: {} },
    orderGeneration: { seed: 1, maxOrders: 0, startAtS: 0, endAtS: 3600,
      cargoMinKg: 0.1, cargoMaxKg: 1, deadlineLeadS: 600 },
  }, scenario);
  return { scenario, logistics };
}

function context(overrides: Partial<Parameters<typeof renderCityWorkspaceSelectedImportPanel>[1]> = {}):
    Parameters<typeof renderCityWorkspaceSelectedImportPanel>[1] {
  const values = source();
  return {
    target: { facilities: [{ id: "hub.01", kind: "hub" }], fleet: [{ id: "uav.01" }],
      deployment: { executor: "docker_reference", imageRef: "" } },
    scenario: values.scenario, logistics: values.logistics,
    scenarioError: null, logisticsError: null, ...overrides,
  };
}

describe("read-only selected logistics import panel", () => {
  it("shows source identity and retained fields, then performs only the explicit logistics import", async () => {
    const root = document.createElement("div");
    const onImport = vi.fn<(prepared: PreparedSelectedLogisticsImport) => string>(
      () => "已导入到 v3 草稿");
    renderCityWorkspaceSelectedImportPanel(root, context(), onImport);
    expect(root.textContent).toContain("选区来源（只读）");
    expect(root.querySelector('[data-role="selected-import-disclosure"]')?.textContent)
      .toContain("未导入工作区");
    expect(root.querySelector('[data-role="selected-import-retained"]')?.textContent)
      .toContain("/selectedScene");
    root.querySelector<HTMLButtonElement>("button")!.click();
    await vi.waitFor(() => expect(onImport).toHaveBeenCalledOnce());
    expect(onImport.mock.calls[0]![0].patch.algorithms).toMatchObject({
      assignment: "external", routing: "external", energy: "external",
    });
    expect(root.querySelector('[data-role="selected-import-result"]')?.textContent).toBe("已导入到 v3 草稿");
  });

  it("keeps missing or invalid sources explicit and never calls the importer callback", async () => {
    const root = document.createElement("div");
    const onImport = vi.fn();
    renderCityWorkspaceSelectedImportPanel(root, context({ logistics: null,
      logisticsError: "物流来源与当前选区不一致" }), onImport);
    expect(root.textContent).toContain("物流来源与当前选区不一致");
    expect(root.querySelector<HTMLButtonElement>("button")?.disabled).toBe(true);
    expect(onImport).not.toHaveBeenCalled();

    const recoverable = context({ logisticsError: "物流来源含有无效引用；原文件保持只读" });
    renderCityWorkspaceSelectedImportPanel(root, recoverable, onImport);
    expect(root.querySelector('[role="alert"]')?.textContent).toContain("无效引用");
    expect(root.querySelector<HTMLButtonElement>("button")?.disabled).toBe(true);
    expect(onImport).not.toHaveBeenCalled();

    const bad = context();
    const logistics = { ...bad.logistics!, algorithms: { ...bad.logistics!.algorithms,
      routing: "grid_astar" as const, externalImageRef: null } };
    renderCityWorkspaceSelectedImportPanel(root, { ...bad, logistics }, onImport);
    root.querySelector<HTMLButtonElement>("button")!.click();
    await vi.waitFor(() => expect(root.querySelector('[data-role="selected-import-result"]')?.textContent)
      .toContain("导入被拒绝"));
    expect(onImport).not.toHaveBeenCalled();
  });

  it("disposes without allowing a pending local verification to update the detached panel", () => {
    const root = document.createElement("div");
    const handle = renderCityWorkspaceSelectedImportPanel(root, context(), vi.fn());
    handle.dispose();
    handle.dispose();
    expect(root.childElementCount).toBe(0);
  });
});
