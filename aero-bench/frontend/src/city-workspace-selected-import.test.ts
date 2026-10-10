import { describe, expect, it } from "vitest";
import { CITY_SELECTED_DRAFT_SCHEMA, type SelectedSceneDraft } from "./city-selected-draft";
import {
  CITY_SELECTED_LOGISTICS_SCHEMA,
  parseCitySelectedLogistics,
  type CitySelectedLogistics,
} from "./city-selected-logistics-draft";
import {
  CITY_SELECTED_SCENARIO_SCHEMA,
  parseCitySelectedScenario,
  type CitySelectedScenario,
} from "./city-selected-scenario";
import {
  SELECTED_LOGISTICS_IMPORT_DISCLOSURE,
  prepareSelectedLogisticsOnlyImport,
  type CityWorkspaceLogisticsImportTarget,
} from "./city-workspace-selected-import";
import {
  SHANGHAI_ORIGIN,
  SHANGHAI_SOURCE_ID,
  SHANGHAI_SOURCE_SHA256,
  type SceneSelection,
} from "./city-region-selector";

const selection: SceneSelection = {
  schema_version: "aero-bench.scene-selection/v1",
  source_id: SHANGHAI_SOURCE_ID,
  source_sha256: SHANGHAI_SOURCE_SHA256,
  origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
};
const selectedScene: SelectedSceneDraft = {
  purpose: "selected-scene-authoring",
  schema_version: CITY_SELECTED_DRAFT_SCHEMA,
  selection,
  selection_sha256: "7d5904868d1f437c377574e267962a7152193fb38b8e1d80b89f6b822abcb09c",
  job_id: "1".repeat(64),
  source_sha256: SHANGHAI_SOURCE_SHA256,
  pack_manifest_sha256: "2".repeat(64),
  presentation_manifest_sha256: "3".repeat(64),
};

function scenario(): CitySelectedScenario {
  return parseCitySelectedScenario({
    purpose: "selected-scenario-authoring",
    schema_version: CITY_SELECTED_SCENARIO_SCHEMA,
    executable: false,
    selectedScene,
    facilities: [
      {
        id: "vp.01", name: "vertiport", kind: "vertiport", placement: "ground",
        buildingId: null, supportHeightM: null, position: { x: 10, z: -20 }, rotationDeg: 0,
        widthM: 20, depthM: 20, heightM: 5,
        landing: { parkingSlots: 3, movementsPerHour: 30 }, cargo: null, charging: null,
      },
      {
        id: "hub.01", name: "hub", kind: "hub", placement: "ground",
        buildingId: null, supportHeightM: null, position: { x: 30, z: -40 }, rotationDeg: 0,
        widthM: 18, depthM: 14, heightM: 5.2,
        landing: { parkingSlots: 2, movementsPerHour: 20 },
        cargo: { storageCapacityKg: 100, throughputPerHourKg: 50 }, charging: null,
      },
    ],
    noFlyZones: [],
    fleet: [{ id: "uav.01", assetId: "model:holybro-x500", count: 1,
      homeFacilityId: "vp.01", batteryWh: 500, reserveRatio: 0.2, maxPayloadKg: 2 }],
    demand: { vehicles: 0, pedestrians: 0, bicycles: 0 },
  });
}

function logistics(boundScenario = scenario()): CitySelectedLogistics {
  return parseCitySelectedLogistics({
    purpose: "selected-logistics-authoring",
    schema_version: CITY_SELECTED_LOGISTICS_SCHEMA,
    executable: false,
    selectedScene: boundScenario.selectedScene,
    orders: [{ id: "order.01", sourceFacilityId: "vp.01", destinationFacilityId: "hub.01",
      hubHandoffFacilityId: null, cargoKg: 0.5, releaseAtS: 10, deliverByS: 300 }],
    performanceProfiles: [{ fleetEntryId: "uav.01", sourceLabel: "operator",
      provenance: "user-declared estimate", aircraftBody: { xM: 0.5, yM: 0.2, zM: 0.5 },
      cruiseSpeedMps: 12, cruisePowerW: 120, hoverPowerW: 85, chargeEfficiency: 0.9 }],
    algorithms: { mode: "distributed", assignment: "external", routing: "external",
      charging: "external", externalImageRef: `registry.example/agent@sha256:${"a".repeat(64)}`,
      parameters: { policy: "reference", retries: 2 } },
    orderGeneration: { seed: 42, maxOrders: 3, startAtS: 0, endAtS: 600,
      cargoMinKg: 0.1, cargoMaxKg: 1, deadlineLeadS: 120 },
  }, boundScenario);
}

function target(): CityWorkspaceLogisticsImportTarget {
  return {
    facilities: [{ id: "vp.01", kind: "vertiport" }, { id: "hub.01", kind: "hub" }],
    fleet: [{ id: "uav.01" }],
    deployment: { executor: "docker_reference", imageRef: "" },
  };
}

describe("selected logistics-only workspace import", () => {
  it("maps only the supported logistics fields and discloses retained selected-scene data", async () => {
    const sourceScenario = scenario();
    const sourceLogistics = logistics(sourceScenario);
    const result = await prepareSelectedLogisticsOnlyImport(target(), sourceScenario, sourceLogistics);

    expect(result.patch).toMatchObject({
      orders: sourceLogistics.orders,
      performanceProfiles: sourceLogistics.performanceProfiles,
      orderGeneration: sourceLogistics.orderGeneration,
      algorithms: { mode: "distributed", assignment: "external", routing: "external", energy: "external",
        parameters: { policy: "reference", retries: 2 } },
      deployment: { executor: "docker_reference", imageRef: sourceLogistics.algorithms.externalImageRef },
    });
    expect(result.source).toEqual({ jobId: selectedScene.job_id,
      selectionSha256: selectedScene.selection_sha256, sourceSha256: selectedScene.source_sha256 });
    expect(result.retainedSourceFields).toEqual([
      "/selectedScene", "/facilities", "/noFlyZones", "/fleet", "/demand",
    ]);
    expect(result.disclosure).toBe(SELECTED_LOGISTICS_IMPORT_DISCLOSURE);
    expect(result.disclosure).toContain("未导入工作区");
    expect(result.patch.orders).not.toBe(sourceLogistics.orders);
    expect(result.patch.performanceProfiles[0]?.aircraftBody)
      .not.toBe(sourceLogistics.performanceProfiles[0]?.aircraftBody);
  });

  it.each([
    ["assignment", "nearest_feasible"],
    ["routing", "grid_astar"],
    ["charging", "reserve_threshold"],
  ] as const)("rejects a non-external %s algorithm instead of guessing", async (field, value) => {
    const sourceScenario = scenario();
    const sourceLogistics = logistics(sourceScenario);
    const changed = { ...sourceLogistics, algorithms: { ...sourceLogistics.algorithms, [field]: value } };
    await expect(prepareSelectedLogisticsOnlyImport(target(), sourceScenario, changed))
      .rejects.toThrow(/only allows external|only .*external|external → external|仅允许 external/);
  });

  it("rejects a foreign or tampered selected-scene binding", async () => {
    const sourceScenario = scenario();
    const sourceLogistics = logistics(sourceScenario);
    const foreign = { ...sourceLogistics, selectedScene: { ...sourceLogistics.selectedScene,
      job_id: "4".repeat(64) } };
    await expect(prepareSelectedLogisticsOnlyImport(target(), sourceScenario, foreign))
      .rejects.toThrow(/不一致/);

    const tamperedScene = { ...sourceScenario, selectedScene: { ...sourceScenario.selectedScene,
      selection: { ...sourceScenario.selectedScene.selection, bounds_enu_m: {
        ...sourceScenario.selectedScene.selection.bounds_enu_m, max_east_m: 779,
      } } } };
    const tamperedLogistics = { ...sourceLogistics, selectedScene: tamperedScene.selectedScene };
    await expect(prepareSelectedLogisticsOnlyImport(target(), tamperedScene, tamperedLogistics))
      .rejects.toThrow(/SceneSelection/);
  });

  it("rejects missing or differently typed target facilities and missing target fleet IDs", async () => {
    const sourceScenario = scenario();
    const sourceLogistics = logistics(sourceScenario);
    await expect(prepareSelectedLogisticsOnlyImport({ ...target(),
      facilities: [{ id: "vp.01", kind: "vertiport" }] }, sourceScenario, sourceLogistics))
      .rejects.toThrow(/hub\.01.*不存在/);
    await expect(prepareSelectedLogisticsOnlyImport({ ...target(), facilities: [
      { id: "vp.01", kind: "charger" }, { id: "hub.01", kind: "hub" },
    ] }, sourceScenario, sourceLogistics)).rejects.toThrow(/vp\.01.*类型/);
    await expect(prepareSelectedLogisticsOnlyImport({ ...target(), fleet: [] }, sourceScenario, sourceLogistics))
      .rejects.toThrow(/uav\.01.*不存在/);
  });
});
