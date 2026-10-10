// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import {
  CITY_FLEET_ASSETS, CITY_WORKSPACE_LEGACY_SCHEMA, CITY_WORKSPACE_LEGACY_STORAGE_KEY,
  CITY_WORKSPACE_SCHEMA, CITY_WORKSPACE_STORAGE_KEY, CITY_WORKSPACE_V2_SCHEMA,
  CITY_WORKSPACE_V2_STORAGE_KEY, createDefaultCityWorkspaceConfig, createDefaultCityWorkspaceV2Config,
  exportCityWorkspaceConfig, importCityWorkspaceConfig, loadCityWorkspaceConfig, migrateCityWorkspaceV1,
  migrateCityWorkspaceV2ToV3, parseCityWorkspaceConfig, parseCityWorkspaceV2Config,
  saveCityWorkspaceConfig,
  type CityAirspace, type CityFacility, type CityWorkspaceConfig,
} from "./city-workspace-config";

function facility(id = "pad.1", kind: CityFacility["kind"] = "vertiport"): CityFacility {
  return {
    id, name: id, kind, position: { x: -12, z: 18 }, rotationDeg: -30,
    widthM: 20, depthM: 14, heightM: 5, capacity: 2, chargingPowerW: 0,
  };
}

function airspace(id = "zone.1"): CityAirspace {
  return {
    id, name: id, polygon: [{ x: 0, z: 0 }, { x: 10, z: 0 }, { x: 10, z: 10 }, { x: 0, z: 10 }],
    floorM: 10, ceilingM: 100, startsAtS: 0, endsAtS: null,
    source: { kind: "manual", label: "editor" },
  };
}

beforeEach(() => window.localStorage.clear());

describe("city workspace authoring contract", () => {
  it("starts with an explicit authoring draft and only verified presentation models", () => {
    const draft = createDefaultCityWorkspaceConfig();
    expect(parseCityWorkspaceConfig(draft)).toEqual(draft);
    expect(draft).toMatchObject({
      purpose: "scenario-authoring", schema_version: "aero-bench.city-workspace/v3",
      scenePath: "/city-presentation/default-scene-v1.json", deployment: { imageRef: "" },
      facilities: [], airspace: [], events: [], orders: [], performanceProfiles: [], authoredLandscape: [],
    });
    expect(draft.environment).toMatchObject({
      cloudCover: 0, precipitation: "none", visibilityM: 10_000,
      timeOfDay: "day", reflectionsEnabled: true,
    });
    expect(draft.fleet).toHaveLength(2);
    expect(draft.fleet.map(craft => craft.assetId)).toEqual(CITY_FLEET_ASSETS.map(asset => asset.id));
    expect(draft.fleet.every(craft => craft.homeFacilityId === null)).toBe(true);
    for (const asset of CITY_FLEET_ASSETS) {
      expect(existsSync(resolve("public", asset.url.slice(1)))).toBe(true);
      expect(existsSync(resolve("public", asset.previewLodUrl.slice(1)))).toBe(true);
    }
    expect(CITY_FLEET_ASSETS[0]!.sizeM).toEqual({ x: 0.65, y: 1.585831 / 3.1 * 0.65, z: 0.65 });
    expect(CITY_FLEET_ASSETS[1]!.sizeM).toEqual({
      x: 2.552401 / 3.1 * 0.85, y: 1.034362 / 3.1 * 0.85, z: 0.85,
    });
  });

  it("rejects extra fields, wrong schema, invalid demand, unverified assets and legacy environment fields", () => {
    const draft = createDefaultCityWorkspaceConfig();
    expect(() => parseCityWorkspaceConfig({ ...draft, runId: "made-up" })).toThrow(/additional properties/);
    expect(() => parseCityWorkspaceConfig({ ...draft, purpose: "run-state" })).toThrow(/purpose/);
    expect(() => parseCityWorkspaceConfig({ ...draft, schema_version: CITY_WORKSPACE_LEGACY_SCHEMA })).toThrow(/schema_version/);
    expect(() => parseCityWorkspaceConfig({ ...draft, schema_version: CITY_WORKSPACE_V2_SCHEMA })).toThrow(/schema_version/);
    expect(() => parseCityWorkspaceConfig({ ...draft, traffic: { ...draft.traffic, vehicles: -1 } })).toThrow(/vehicles/);
    expect(() => parseCityWorkspaceConfig({ ...draft, seed: 1.5 })).toThrow(/seed/);
    expect(() => parseCityWorkspaceConfig({ ...draft, environment: { ...draft.environment, windMps: -1 } })).toThrow(/windMps/);
    expect(() => parseCityWorkspaceConfig({ ...draft, environment: { ...draft.environment, visibilityM: 0 } })).toThrow(/visibilityM/);
    expect(() => parseCityWorkspaceConfig({ ...draft, environment: { ...draft.environment, timeOfDay: "dusk" } }))
      .toThrow(/timeOfDay/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      environment: { ...draft.environment, mood: "day" } })).toThrow(/additional properties|environment/);
    expect(() => parseCityWorkspaceConfig({ ...draft, fleet: [{ ...draft.fleet[0], assetId: "model:missing" }] })).toThrow(/assetId/);
    for (const scenePath of [
      "/city-presentation/../secret.json", "/city-presentation/nested/scene.json",
      "/city-presentation/scene..json", "/city-presentation/scene.json/next.json",
    ]) expect(() => parseCityWorkspaceConfig({ ...draft, scenePath })).toThrow(/scenePath/);
    expect(() => parseCityWorkspaceConfig({ ...draft, fleet: [draft.fleet[0], draft.fleet[0]] })).toThrow(/duplicate id/);
    expect(() => parseCityWorkspaceConfig({ ...draft, deployment: { ...draft.deployment, imageRef: "registry/app" } }))
      .toThrow(/imageRef/);
    expect(() => parseCityWorkspaceConfig({
      ...draft, facilities: [facility()],
      fleet: [{ ...draft.fleet[0], homeFacilityId: "pad.9" }],
    })).toThrow(/unknown home facility/);
    expect(() => parseCityWorkspaceConfig({ ...draft, airspace: [{ ...airspace(), ceilingM: 10 }] }))
      .toThrow(/ceiling/);
    expect(() => parseCityWorkspaceConfig({ ...draft, airspace: [{ ...airspace(), endsAtS: -1 }] }))
      .toThrow(/endsAtS/);
    expect(() => parseCityWorkspaceConfig({ ...draft, events: [
      { id: "event.1", atS: 10, type: "airspace.activated", targetId: "zone.404", payload: {} },
    ] })).toThrow(/unknown airspace/);
    expect(() => parseCityWorkspaceConfig({ ...draft, events: [
      { id: "event.1", atS: 10, type: "charger.outage", targetId: "pad.404", payload: {} },
    ] })).toThrow(/unknown charger/);
  });

  it("persists and imports only valid drafts, keeping night across export and import", () => {
    expect(loadCityWorkspaceConfig()).toBeNull();
    const draft = createDefaultCityWorkspaceConfig();
    draft.name = "Saved draft";
    draft.environment = { ...draft.environment, timeOfDay: "night", precipitation: "rain",
      precipitationRateMmPerH: 6, cloudCover: 0.88, visibilityM: 1800, windMps: 7 };
    saveCityWorkspaceConfig(draft);
    expect(window.localStorage.getItem(CITY_WORKSPACE_STORAGE_KEY)).toContain("Saved draft");
    expect(window.localStorage.getItem(CITY_WORKSPACE_STORAGE_KEY)).toContain("aero-bench.city-workspace/v3");
    expect(loadCityWorkspaceConfig()).toEqual(draft);
    const exported = exportCityWorkspaceConfig(draft);
    expect(exported).toContain("aero-bench.city-workspace/v3");
    const roundTripped = importCityWorkspaceConfig(exported);
    expect(roundTripped.environment.timeOfDay).toBe("night");
    expect(roundTripped).toEqual(draft);
    expect(() => importCityWorkspaceConfig("{bad json")).toThrow(SyntaxError);
    window.localStorage.setItem(CITY_WORKSPACE_STORAGE_KEY, JSON.stringify({ ...draft, unknown: true }));
    expect(() => loadCityWorkspaceConfig()).toThrow(/additional properties/);
    expect(() => saveCityWorkspaceConfig({ ...draft, seed: -1 } as CityWorkspaceConfig)).toThrow(/seed/);
  });

  it("migrates a v1 draft explicitly to v2, mapping dusk to twilight", () => {
    const legacy = JSON.parse(JSON.stringify(createDefaultCityWorkspaceV2Config())) as Record<string, unknown>;
    legacy.schema_version = CITY_WORKSPACE_LEGACY_SCHEMA;
    legacy.environment = { ...(legacy.environment as Record<string, unknown>), mood: "dusk" };
    const migrated = migrateCityWorkspaceV1(legacy);
    expect(migrated.schema_version).toBe(CITY_WORKSPACE_V2_SCHEMA);
    expect(migrated.environment).toMatchObject({
      timeOfDay: "twilight", cloudCover: 0, precipitation: "none", reflectionsEnabled: true,
    });
    expect("mood" in migrated.environment).toBe(false);
    expect(parseCityWorkspaceV2Config(migrated)).toBe(migrated);
    // A day mood keeps day; nothing else is invented during migration.
    const day = migrateCityWorkspaceV1({ ...legacy, environment: { ...(legacy.environment as object), mood: "day" } });
    expect(day.environment.timeOfDay).toBe("day");
    // An already-migrated draft re-enters parse unchanged; migration itself stays a v1-only path.
    expect(parseCityWorkspaceV2Config(migrated)).toBe(migrated);
    expect(() => migrateCityWorkspaceV1(migrated)).toThrow(/expects aero-bench.city-workspace\/v1/);
  });

  it("rejects non-v1 documents, tampered migration inputs and invalid legacy environments", () => {
    const draft = createDefaultCityWorkspaceV2Config();
    expect(() => migrateCityWorkspaceV1({ ...draft, schema_version: CITY_WORKSPACE_V2_SCHEMA }))
      .toThrow(/expects aero-bench.city-workspace\/v1/);
    expect(() => migrateCityWorkspaceV1({ ...draft, schema_version: CITY_WORKSPACE_LEGACY_SCHEMA, purpose: "run-state" }))
      .toThrow(/scenario-authoring/);
    expect(() => migrateCityWorkspaceV1(null)).toThrow(/must be an object/);
    expect(() => migrateCityWorkspaceV1({ schema_version: CITY_WORKSPACE_LEGACY_SCHEMA,
      purpose: "scenario-authoring", environment: { mood: "night" } })).toThrow(/mood/);
    expect(() => migrateCityWorkspaceV1({ schema_version: CITY_WORKSPACE_LEGACY_SCHEMA,
      purpose: "scenario-authoring", environment: { ...createDefaultCityWorkspaceV2Config().environment,
        mood: "day", cloudCover: 2 } })).toThrow(RangeError);
  });

  it("migrates the stored v1 draft on load, saves it as v2, and keeps the v1 entry untouched", () => {
    const legacy = JSON.parse(JSON.stringify(createDefaultCityWorkspaceV2Config())) as Record<string, unknown>;
    legacy.schema_version = CITY_WORKSPACE_LEGACY_SCHEMA;
    legacy.environment = { ...(legacy.environment as Record<string, unknown>), mood: "dusk" };
    window.localStorage.setItem(CITY_WORKSPACE_LEGACY_STORAGE_KEY, JSON.stringify(legacy));
    const loaded = loadCityWorkspaceConfig();
    expect(loaded?.schema_version).toBe(CITY_WORKSPACE_SCHEMA);
    expect(loaded?.environment.timeOfDay).toBe("twilight");
    // The v1 entry is preserved verbatim; v2 remains as the migration source and v3 is active.
    expect(window.localStorage.getItem(CITY_WORKSPACE_LEGACY_STORAGE_KEY)).toBe(JSON.stringify(legacy));
    expect(window.localStorage.getItem(CITY_WORKSPACE_V2_STORAGE_KEY)).toContain(CITY_WORKSPACE_V2_SCHEMA);
    expect(window.localStorage.getItem(CITY_WORKSPACE_STORAGE_KEY)).toContain(CITY_WORKSPACE_SCHEMA);
    // A second load reads the v3 key and no longer touches either old entry.
    expect(loadCityWorkspaceConfig()).toEqual(loaded);
    expect(window.localStorage.getItem(CITY_WORKSPACE_LEGACY_STORAGE_KEY)).toBe(JSON.stringify(legacy));
  });

  it("prefers the current v3 key when older drafts also exist", () => {
    const draft = createDefaultCityWorkspaceConfig();
    draft.name = "Current v3 draft";
    saveCityWorkspaceConfig(draft);
    const v2 = createDefaultCityWorkspaceV2Config();
    v2.name = "Stale v2 draft";
    window.localStorage.setItem(CITY_WORKSPACE_V2_STORAGE_KEY, JSON.stringify(v2));
    window.localStorage.setItem(CITY_WORKSPACE_LEGACY_STORAGE_KEY,
      JSON.stringify({ ...v2,
        schema_version: CITY_WORKSPACE_LEGACY_SCHEMA, name: "Stale v1 draft",
        environment: { ...draft.environment, timeOfDay: undefined, mood: "day" } }));
    expect(loadCityWorkspaceConfig()?.name).toBe("Current v3 draft");
  });
});

describe("city workspace v3 logistics and landscape contract", () => {
  it("uses strict v3 defaults as the active contract", () => {
    expect(CITY_WORKSPACE_SCHEMA).toBe("aero-bench.city-workspace/v3");
    expect(CITY_WORKSPACE_STORAGE_KEY).toBe("aero-bench.city-workspace.v3");
    expect(CITY_WORKSPACE_V2_SCHEMA).toBe("aero-bench.city-workspace/v2");
    expect(CITY_WORKSPACE_V2_STORAGE_KEY).toBe("aero-bench.city-workspace.v2");
    const draft = createDefaultCityWorkspaceConfig();
    expect(parseCityWorkspaceConfig(draft)).toEqual(draft);
    expect(draft).toMatchObject({
      schema_version: CITY_WORKSPACE_SCHEMA,
      orders: [], performanceProfiles: [], authoredLandscape: [],
      orderGeneration: { seed: draft.seed, maxOrders: 0 },
    });
    expect(() => parseCityWorkspaceV2Config(draft)).toThrow(/additional properties|schema_version/);
  });

  it("migrates v2 explicitly without changing its object or inventing authoring content", () => {
    const v2 = createDefaultCityWorkspaceV2Config();
    v2.seed = 83;
    const before = JSON.stringify(v2);
    const migrated = migrateCityWorkspaceV2ToV3(v2);
    expect(JSON.stringify(v2)).toBe(before);
    expect(migrated.schema_version).toBe(CITY_WORKSPACE_SCHEMA);
    expect(migrated.orders).toEqual([]);
    expect(migrated.performanceProfiles).toEqual([]);
    expect(migrated.authoredLandscape).toEqual([]);
    expect(migrated.orderGeneration).toEqual({
      seed: 83, maxOrders: 0, startAtS: 0, endAtS: 3_600,
      cargoMinKg: 0.1, cargoMaxKg: 1, deadlineLeadS: 600,
    });
    expect(importCityWorkspaceConfig(exportCityWorkspaceConfig(migrated))).toEqual(migrated);
    expect(() => migrateCityWorkspaceV2ToV3({ ...migrated })).toThrow(/schema_version/);
  });

  it("migrates v2 storage without changing its bytes and then prefers v3", () => {
    const v2 = createDefaultCityWorkspaceV2Config();
    v2.name = "v2 source kept verbatim";
    window.localStorage.setItem(CITY_WORKSPACE_V2_STORAGE_KEY, JSON.stringify(v2));
    const v2Bytes = window.localStorage.getItem(CITY_WORKSPACE_V2_STORAGE_KEY);
    const migrated = loadCityWorkspaceConfig();
    expect(migrated?.name).toBe(v2.name);
    expect(migrated?.schema_version).toBe(CITY_WORKSPACE_SCHEMA);
    expect(window.localStorage.getItem(CITY_WORKSPACE_V2_STORAGE_KEY)).toBe(v2Bytes);
    expect(window.localStorage.getItem(CITY_WORKSPACE_STORAGE_KEY)).toContain(CITY_WORKSPACE_SCHEMA);
    const edited = { ...migrated!, name: "saved v3 wins" };
    saveCityWorkspaceConfig(edited);
    expect(loadCityWorkspaceConfig()?.name).toBe("saved v3 wins");
    expect(window.localStorage.getItem(CITY_WORKSPACE_V2_STORAGE_KEY)).toBe(v2Bytes);
  });

  it("accepts exact orders, generation, profiles and authored visual landscape", () => {
    const draft = createDefaultCityWorkspaceConfig();
    draft.facilities = [facility("vp.1"), facility("hub.1", "hub")];
    draft.fleet = [{ ...draft.fleet[0]!, homeFacilityId: "vp.1" }];
    draft.orders = [{ id: "order.1", sourceFacilityId: "vp.1", destinationFacilityId: "hub.1",
      hubHandoffFacilityId: null, cargoKg: 0.5, releaseAtS: 5, deliverByS: 60 }];
    draft.orderGeneration = { seed: 7, maxOrders: 4, startAtS: 0, endAtS: 120,
      cargoMinKg: 0.1, cargoMaxKg: 1.2, deadlineLeadS: 30 };
    draft.performanceProfiles = [{ fleetEntryId: draft.fleet[0]!.id, sourceLabel: "operator",
      provenance: "user-declared estimate", aircraftBody: { xM: 0.5, yM: 0.2, zM: 0.5 },
      cruiseSpeedMps: 12, cruisePowerW: 120, hoverPowerW: 85, chargeEfficiency: 0.9 }];
    draft.authoredLandscape = [{ id: "landscape.1", label: "courtyard", provenance: "authored",
      kind: "plaza", polygon: [{ x: 0, z: 0 }, { x: 5, z: 0 }, { x: 0, z: 5 }] }];
    expect(parseCityWorkspaceConfig(draft)).toEqual(draft);
  });

  it("rejects invalid cross-references, algorithm additions and malformed visual polygons", () => {
    const draft = createDefaultCityWorkspaceConfig();
    draft.facilities = [facility("vp.1"), facility("hub.1", "hub")];
    const order = { id: "order.1", sourceFacilityId: "vp.1", destinationFacilityId: "hub.1",
      hubHandoffFacilityId: null, cargoKg: 0.5, releaseAtS: 5, deliverByS: 60 };
    expect(() => parseCityWorkspaceConfig({ ...draft, orders: [{ ...order,
      destinationFacilityId: "missing" }] })).toThrow(/invalid destination/);
    expect(() => parseCityWorkspaceConfig({ ...draft, facilities: [facility("vp.1"), facility("vp.2")],
      orders: [{ ...order, destinationFacilityId: "vp.2" }] })).toThrow(/hub handoff/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      performanceProfiles: [{ fleetEntryId: "uav.missing", sourceLabel: "operator",
        provenance: "estimate", aircraftBody: { xM: 1, yM: 1, zM: 1 },
        cruiseSpeedMps: 1, cruisePowerW: 1, hoverPowerW: 1, chargeEfficiency: 1 }] }))
      .toThrow(/unknown fleet/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      orderGeneration: { ...draft.orderGeneration, endAtS: 0 } })).toThrow(/end must follow/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      orderGeneration: { ...draft.orderGeneration, maxOrders: 10_001 } })).toThrow(/maxOrders/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      orders: [{ ...order, id: "bad order" }] })).toThrow(/orders/);
    expect(() => parseCityWorkspaceConfig({ ...draft,
      algorithms: { ...draft.algorithms, selectedRouting: "grid_astar" } })).toThrow(/additional properties/);
    expect(() => parseCityWorkspaceConfig({ ...draft, authoredLandscape: [{
      id: "bad", label: "crossed", provenance: "authored", kind: "green",
      polygon: [{ x: 0, z: 0 }, { x: 4, z: 4 }, { x: 0, z: 4 }, { x: 4, z: 0 }],
    }] })).toThrow(/self-intersects/);
  });
});
