import { describe, expect, it } from "vitest";
import { createDefaultCityWorkspaceConfig, type CityFacility } from "./city-workspace-config";
import {
  nativeReferenceSpatialSource, renderCompiledHandoff, resolveRegisteredSource, savedNativeRegistration, translateStudioShell, validateStudioGeometry,
} from "./city-studio";
import studioHtml from "../city-studio.html?raw";
import { renderCityRuntimePanel } from "./city-runtime-panel";
import { setLanguage, t, tf } from "./i18n";
import type { AuthoringCatalog, NativeSceneRegistration } from "./city-authoring-api";
import { SHANGHAI_ORIGIN } from "./city-region-selector";
import type { SpatialMapData } from "./city-spatial-panel";
import { assertPublicScenario } from "./generated/contract-validators";
import { coordinate, identifier, scenario } from "./testing/trace-v3-fixture";
import {
  nativeReferenceHasVisualPresentation, type VerifiedNativeReferenceScene,
} from "./native-reference-scene";

const map: SpatialMapData = {
  origin: { latitude_deg: 31.2288, longitude_deg: 121.481 },
  extent: { minX: -50, maxX: 50, minZ: -50, maxZ: 50 },
  buildings: [{ id: "building-1:0", x: 0, z: 0, widthM: 8, depthM: 8, heightM: 12, rotationDeg: 0 }],
  roads: [{ id: "roadbed:0", outline: [
    { x: 15, z: 15 }, { x: 20, z: 15 }, { x: 20, z: 20 }, { x: 15, z: 20 },
  ], holes: [] }],
  roadClearance: {
    provenance: { source: "canonical-road-v3", roadSha256: "1".repeat(64),
      fixtureIdentity: "2".repeat(64), displayedSurfaceSha256: "3".repeat(64) },
    roadbed: [{ id: "roadbed:0", outline: [
      { x: 15, z: 15 }, { x: 20, z: 15 }, { x: 20, z: 20 }, { x: 15, z: 20 },
    ], holes: [] }],
    walkbed: [], crossings: [], fixtures: [],
  },
};

const facility: CityFacility = {
  id: "facility-1", name: "起降点一", kind: "vertiport", position: { x: -20, z: -20 },
  rotationDeg: 0, widthM: 6, depthM: 6, heightM: 1,
  capacity: 2, chargingPowerW: 0,
};

describe("compiled saved-configuration handoff", () => {
  it("renders English runtime controls while preserving model IDs and edited seed", () => {
    const root = document.createElement("div");
    const config = createDefaultCityWorkspaceConfig();
    const before = JSON.stringify(config);
    let edited: typeof config | null = null;
    setLanguage("en");
    renderCityRuntimePanel(root, config, next => { edited = next; });
    expect(root.textContent).toContain("Random seed");
    expect(root.querySelector('option[value="model:quadcopter-40-preview"]')?.textContent).toBe("Camera quadrotor");
    expect(root.querySelector('select[name="environment.precipitation"] option[value="snow"]')?.textContent).toBe("Snow");
    expect(JSON.stringify(config)).toBe(before);
    const seed = root.querySelector<HTMLInputElement>('[name="seed"]')!;
    seed.value = "20261003";
    seed.dispatchEvent(new Event("change"));
    expect(edited).toEqual({ ...config, seed: 20261003 });
    setLanguage("zh");
  });
  it("translates the actual shell while preserving the import control and edited name", () => {
    const doc = new DOMParser().parseFromString(studioHtml, "text/html");
    const root = doc.querySelector<HTMLElement>("#city-studio")!;
    const name = root.querySelector<HTMLInputElement>("#studio-name")!;
    const imported = root.querySelector<HTMLInputElement>("#studio-import")!;
    name.value = "authored identity unchanged";
    setLanguage("en");
    translateStudioShell(root);
    expect(root.querySelector("#studio-save")?.textContent).toBe("Save draft");
    expect(root.querySelector("[data-tab=runtime]")?.textContent).toContain("Scene and fleet");
    expect(root.querySelector("#studio-import")).toBe(imported);
    expect(name.value).toBe("authored identity unchanged");
    setLanguage("zh");
    translateStudioShell(root);
    expect(root.querySelector("#studio-save")?.textContent).toBe("保存草稿");
  });
  it.each(["zh", "en"] as const)("shows immutable identities without starting a run (%s)", language => {
    setLanguage(language);
    const root = document.createElement("div");
    const selection = {
      compilationId: "compilation.saved", registrationId: "scene.saved",
      draftSha256: "a".repeat(64), runIds: ["b".repeat(64), "c".repeat(64)],
    };
    renderCompiledHandoff(root, selection);
    expect(root.querySelector("h3")?.textContent).toBe(
      language === "en" ? "Compiled, not yet run" : "已编译，尚未运行",
    );
    for (const identity of [selection.compilationId, selection.registrationId,
      selection.draftSha256, ...selection.runIds]) expect(root.textContent).toContain(identity);
    expect(root.textContent).toContain(language === "en" ? "Saved configuration identity" : "已保存配置身份");
    expect(root.querySelector("a")?.getAttribute("href")).toBe("/");
    expect(root.querySelector("button")).toBeNull();
    setLanguage("zh");
  });
});

describe("city studio import geometry gate", () => {
  it("accepts an unbound fleet as an editable draft", () => {
    expect(validateStudioGeometry(createDefaultCityWorkspaceConfig(), map)).toEqual([]);
  });

  it("reports the exact facility when an imported file overlaps a building or leaves the scene", () => {
    const config = createDefaultCityWorkspaceConfig();
    config.facilities = [
      { ...facility, position: { x: 0, z: 0 } },
      { ...facility, id: "facility-2", name: "起降点二", position: { x: 60, z: 0 } },
    ];
    const issues = validateStudioGeometry(config, map);
    expect(issues).toEqual([
      "设施 起降点一：Facility overlaps building building-1:0",
      "设施 起降点二：坐标超出当前城市地图范围",
    ]);
  });

  it("checks imported no-fly areas against the placed vertiport", () => {
    const config = createDefaultCityWorkspaceConfig();
    config.facilities = [facility];
    config.airspace = [{
      id: "airspace-1", name: "临时禁飞", polygon: [
        { x: -25, z: -25 }, { x: -15, z: -25 }, { x: -15, z: -15 }, { x: -25, z: -15 },
      ],
      floorM: 0, ceilingM: 20, startsAtS: 0, endsAtS: null,
      source: { kind: "manual", label: "用户绘制" },
    }];
    expect(validateStudioGeometry(config, map)).toContain("设施 起降点一：Vertiport overlaps no-fly area 临时禁飞");
  });

  it("blocks verified road clearance and an unavailable clearance input", () => {
    const config = createDefaultCityWorkspaceConfig();
    config.facilities = [{ ...facility, position: { x: 17.5, z: 17.5 } }];
    expect(validateStudioGeometry(config, map)).toContain(
      "设施 起降点一：地面起降点足迹占用已验证机动车道 roadbed:0",
    );
    expect(validateStudioGeometry(config, { ...map, roadClearance: null })).toContain(
      "设施 起降点一：地面起降点缺少已验证道路、人行铺装、人行横道与有效街道设施净空，不能放置或保存",
    );
  });
});

describe("native reference spatial source", () => {
  it("resolves the saved exact source selector without guessing URLs or choosing another registration", () => {
    const registration = { registration_id: "inspection.saved", scene_path: "/native/saved.json",
      scene_url: "/authoring/v1/native-scenes/inspection.saved/scene" } as NativeSceneRegistration;
    expect(savedNativeRegistration("/native/saved.json", [registration])).toBe(registration);
    expect(savedNativeRegistration("/native/other.json", [registration])).toBeNull();
    expect(() => savedNativeRegistration("/native/saved.json", [registration,
      { ...registration, registration_id: "inspection.ambiguous" }])).toThrow("Multiple native registrations");
  });

  it("derives building, road and region context directly from PublicScenario", () => {
    const at = (east: number, north: number, up: number) => coordinate({
      enu: { east_m: east, north_m: north, up_m: up },
    });
    const value = scenario({
      buildings: [{ building_id: identifier("building"), entity_id: identifier("building-entity"),
        render_asset_id: identifier("building-asset"), anchor_east_m: 0, anchor_north_m: 0,
        base_vertices: [at(0, 0, 0), at(10, 0, 0), at(10, 10, 0), at(0, 10, 0)],
        top_vertices: [at(0, 0, 20), at(10, 0, 20), at(10, 10, 20), at(0, 10, 20)] }],
      roads: [{ road_id: identifier("road"), kind: "service_road", width_m: 4,
        terrain_points: [at(-10, 0, 0), at(10, 0, 0)] }],
      regions: [{ region_id: identifier("region"), kind: "no_fly", anchor_east_m: 0,
        anchor_north_m: 0, communications_shadow_attenuation_db: null,
        lower_vertices: [at(0, 0, 0), at(5, 0, 0), at(0, 5, 0)],
        upper_vertices: [at(0, 0, 30), at(5, 0, 30), at(0, 5, 30)] }],
    });
    assertPublicScenario(value);
    const draft = { ...createDefaultCityWorkspaceConfig(), scenePath: "/native/reference.json" };
    const registration: NativeSceneRegistration = {
      registration_id: "inspection.reference", registration_sha256: "a".repeat(64),
      scene_path: draft.scenePath, scene_url: "/authoring/v1/native-scenes/inspection.reference/scene",
      scene_schema_version: "aero-bench.public-scenario/v1", scene_sha256: "b".repeat(64),
      scene_size_bytes: 100, world_id: value.world_id, world_digest: value.world_digest,
      profile_id: "inspection.reference.v1", editable_execution_fields: ["/seed", "/deployment/executor"],
      retained_authoring_fields: ["/name", "/environment", "/stateKeyframes"], reference_draft: draft,
    };
    const reference: VerifiedNativeReferenceScene = { registration, scenario: value, draft };
    expect(nativeReferenceHasVisualPresentation(reference)).toBe(false);
    const spatial = nativeReferenceSpatialSource(reference);
    expect(spatial.data.buildings).toEqual([expect.objectContaining({
      id: identifier("building"), x: 5, z: -5, widthM: 10, depthM: 10, heightM: 20,
    })]);
    expect(spatial.data.roads).toHaveLength(1);
    expect(spatial.data.sourceRegions).toEqual([expect.objectContaining({
      id: identifier("region"), kind: "no_fly",
    })]);
    expect(spatial.data.roadClearance).toBeNull();
    expect(spatial.data.roadClearanceError).toContain("未提供 road v3");
  });

  it("recognizes a declared OSM mesh as one native visual route", () => {
    const base = scenario();
    assertPublicScenario(base);
    const draft = { ...createDefaultCityWorkspaceConfig(), scenePath: "/native/reference.json" };
    const registration = {
      registration_id: "inspection.reference", registration_sha256: "a".repeat(64),
      scene_path: draft.scenePath, scene_url: "/authoring/v1/native-scenes/inspection.reference/scene",
      scene_schema_version: "aero-bench.public-scenario/v1", scene_sha256: "b".repeat(64),
      scene_size_bytes: 100, world_id: base.world_id, world_digest: base.world_digest,
      profile_id: "inspection.reference.v1", editable_execution_fields: ["/seed"],
      retained_authoring_fields: ["/name"], reference_draft: draft,
    } as NativeSceneRegistration;
    const meshScenario = scenario({
      layers: [{ layer_id: identifier("mesh"), kind: "osm_mesh", asset_id: identifier("geoid"),
        visibility: "public", default_visible: true }],
    });
    assertPublicScenario(meshScenario);
    expect(nativeReferenceHasVisualPresentation({ registration, scenario: meshScenario, draft })).toBe(true);
  });
});

describe("selected city source restoration", () => {
  const catalog: AuthoringCatalog = {
    schema_version: "aero-bench.scene-source-catalog/v1",
    sources: ["central", "jingan"].map((name, index) => ({
      source_id: name, sha256: String(index + 1).repeat(64), size_bytes: 100,
      display_name: name, origin: SHANGHAI_ORIGIN,
      bounds_wgs84: { min_latitude_deg: 31.22, max_latitude_deg: 31.23,
        min_longitude_deg: 121.44, max_longitude_deg: 121.45 },
      data_url: `/authoring/v1/sources/${name}`,
    })),
  };

  it("finds the exact non-default source bound to a saved draft", () => {
    const verdict = resolveRegisteredSource(catalog, "jingan", "2".repeat(64));
    expect(verdict.kind).toBe("match");
    if (verdict.kind === "match") expect(verdict.source.source_id).toBe("jingan");
  });

  it("reports removed and changed sources without selecting another city", () => {
    expect(resolveRegisteredSource(catalog, "other", "3".repeat(64))).toEqual({
      kind: "missing", source_id: "other",
    });
    expect(resolveRegisteredSource(catalog, "jingan", "0".repeat(64))).toEqual({
      kind: "hash_changed", source_id: "jingan",
    });
  });
});

describe("native preview status localization", () => {
  it.each(["zh", "en"] as const)("resolves every new status key in both languages (%s)", language => {
    setLanguage(language);
    const keys = [
      "studio.nativeLayers", "studio.nativeAssembling", "studio.nativeVerifiedNoMesh",
      "studio.nativeNoMeshNote", "studio.nativeAssembleFailed", "studio.authoringStaticPreview",
      "studio.nativeCityAssetsFailed", "studio.nativeSceneFailed", "studio.nativeSceneReady",
      "studio.cityLoadFailed", "studio.importUnsavedFailed", "studio.cityReady",
      "studio.mapStartFailed", "studio.unknownReason", "studio.authoringReady",
    ] as const;
    for (const key of keys) {
      expect(typeof t(key)).toBe("string");
      expect(t(key).length).toBeGreaterThan(0);
    }
    expect(tf("studio.nativeLayers", { completed: 2, total: 5 }))
      .toContain(language === "en" ? "2/5" : "2/5");
    setLanguage("zh");
  });

  it("keeps raw error details and scene identities intact when composing localized messages", () => {
    setLanguage("en");
    const detail = "mesh fetch failed";
    const digest = "f".repeat(64);
    const composed = `${t("studio.nativeAssembleFailed")}${detail ?? t("studio.unknownReason")}`;
    expect(composed).toContain(detail);
    expect(composed).not.toContain("undefined");
    const missingDetail = null as string | null;
    const fallback = `${t("studio.nativeCityAssetsFailed")}${missingDetail ?? t("studio.unknownReason")}`;
    expect(fallback).toContain(t("studio.unknownReason"));
    expect(tf("studio.nativeFooter", { registration: digest })).toContain(digest);
    setLanguage("zh");
    expect(`${t("studio.nativeAssembleFailed")}${detail}`).toContain(detail);
    expect(t("studio.nativeAssembleFailed")).toContain("：");
    setLanguage("zh");
  });
});
