import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  inspectNativeReferenceVisual, loadNativeReferencePack, loadNativeReferenceScene,
  loadNativeReferenceVisual, nativeReferenceHasVisualPresentation,
} from "./native-reference-scene";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";
import type { NativeSceneRegistration } from "./city-authoring-api";
import { assertPublicScenario } from "./generated/contract-validators";
import type { PublicScenario, PublicScenarioAsset } from "./generated/aero-bench-contracts";
import { coordinate, pose, scenario } from "./testing/trace-v3-fixture";

const scenePath = "/city-presentation/reference-native.json";
const draft = { ...createDefaultCityWorkspaceConfig(), scenePath, seed: 7 };
const bytes = new TextEncoder().encode(JSON.stringify(scenario()));
const registration: NativeSceneRegistration = {
  registration_id: "inspection.reference", registration_sha256: "a".repeat(64),
  scene_path: scenePath, scene_url: "/authoring/v1/native-scenes/inspection.reference/scene",
  scene_schema_version: "aero-bench.public-scenario/v1",
  scene_sha256: createHash("sha256").update(bytes).digest("hex"), scene_size_bytes: bytes.byteLength,
  world_id: "fixture.world", world_digest: "1".repeat(64), profile_id: "inspection.reference.v1",
  editable_execution_fields: ["/seed"], retained_authoring_fields: ["/name", "/environment", "/stateKeyframes"],
  reference_draft: draft,
};

function nativeCityScenario(): PublicScenario {
  const base = scenario() as unknown as PublicScenario;
  const asset = (assetId: string, mediaType: string, digit: string): PublicScenarioAsset => ({
    asset_id: assetId, selector: `${assetId}.bin`, sha256: digit.repeat(64), size_bytes: 16,
    media_type: mediaType, replay_path: `assets/${digit.repeat(64)}`,
    license_id: null, license_selector: null, license_sha256: null,
    license_size_bytes: null, license_replay_path: null,
  });
  const assets = [
    asset("native.osm", "application/json", "2"),
    asset("native.roads", "application/json", "3"),
    asset("native.entities", "application/json", "4"),
    asset("native.regions", "application/json", "5"),
    asset("native.building.glb", "model/gltf-binary", "6"),
  ];
  const point = (east: number, north: number, up: number) =>
    coordinate({ enu: { east_m: east, north_m: north, up_m: up } });
  return {
    ...base,
    assets: [...base.assets, ...assets],
    layers: (["osm_scene", "roads", "entities", "regions"] as const).map((kind, index) => ({
      layer_id: `native.layer.${kind}`, kind, asset_id: assets[index]!.asset_id,
      visibility: "public", default_visible: true,
    })),
    buildings: [{
      building_id: "native.building", entity_id: "native.building.entity",
      render_asset_id: "native.building.glb", anchor_east_m: 1, anchor_north_m: 1,
      base_vertices: [point(0, 0, 0), point(2, 0, 0), point(2, 2, 0), point(0, 2, 0)],
      top_vertices: [point(0, 0, 3), point(2, 0, 3), point(2, 2, 3), point(0, 2, 3)],
    }],
    entities: [...base.entities, {
      entity_id: "native.building.entity", kind: "static_asset", owner_kind: "scenario",
      owner_id: base.world_id, authority_kind: "scenario_static", state: "static",
      model_asset_id: "native.building.glb", initial_pose: pose(), selected_launch_override: false,
    }],
  } as unknown as PublicScenario;
}

afterEach(() => vi.unstubAllGlobals());
describe("explicit native reference scene selection", () => {
  it("verifies the scene and returns an independent unsaved authoring draft", async () => {
    const fetch = vi.fn(async () => new Response(bytes));
    vi.stubGlobal("fetch", fetch);
    const loaded = await loadNativeReferenceScene(registration);
    expect(loaded.scenario.schema_version).toBe("aero-bench.public-scenario/v1");
    expect(loaded.draft.scenePath).toBe(scenePath);
    loaded.draft.name = "edited";
    expect(registration.reference_draft.name).not.toBe("edited");
    expect(fetch).toHaveBeenCalledWith(registration.scene_url, expect.objectContaining({ redirect: "error" }));
  });
  it("rejects wrong hashes, sizes and world identities", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes)));
    await expect(loadNativeReferenceScene({ ...registration, scene_sha256: "b".repeat(64) })).rejects.toThrow("digest");
    await expect(loadNativeReferenceScene({ ...registration, scene_size_bytes: bytes.byteLength + 1 })).rejects.toThrow();
    await expect(loadNativeReferenceScene({ ...registration, world_digest: "b".repeat(64) })).rejects.toThrow("identity");
  });
  it("rejects a different draft binding and a failed public scene request", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 404 })));
    await expect(loadNativeReferenceScene({ ...registration,
      reference_draft: { ...draft, scenePath: "/city-presentation/other.json" } })).rejects.toThrow("does not select");
    await expect(loadNativeReferenceScene(registration)).rejects.toThrow("404");
  });
  it("verifies declared bytes and retains absent mesh publication explicitly", async () => {
    const assetBytes = new TextEncoder().encode('{"source":"unit fixture"}');
    const sha256 = createHash("sha256").update(assetBytes).digest("hex");
    const publicScenario = { ...scenario(), layers: [], assets: [{ asset_id: "fixture.asset",
      replay_path: `assets/${sha256}`, sha256, size_bytes: assetBytes.byteLength,
      media_type: "application/json", selector: "unit-fixture.json", license_id: null,
      license_selector: null, license_sha256: null, license_replay_path: null,
      license_size_bytes: null }] };
    assertPublicScenario(publicScenario);
    vi.stubGlobal("window", { location: { href: "http://127.0.0.1:5392/city-studio.html" } });
    const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(assetBytes));
    vi.stubGlobal("fetch", fetch);
    await expect(loadNativeReferencePack({ registration, scenario: publicScenario, draft })).resolves.toBeNull();
    expect(String(fetch.mock.calls[0]![0])).toBe(
      `http://127.0.0.1:5392/authoring/v1/native-scenes/inspection.reference/assets/${sha256}`);
    fetch.mockImplementationOnce(async () => new Response(new Uint8Array(assetBytes.byteLength)));
    await expect(loadNativeReferencePack({ registration, scenario: publicScenario, draft })).rejects.toThrow("digest");
  });

  it("routes complete native-city declarations without promoting incomplete registrations", async () => {
    const complete = { registration, scenario: nativeCityScenario(), draft };
    const inspection = inspectNativeReferenceVisual(complete);
    expect(inspection.kind).toBe("native-city");
    expect(inspection.kind === "native-city" && inspection.plan.buildings).toHaveLength(1);
    expect(nativeReferenceHasVisualPresentation(complete)).toBe(true);

    const incomplete = { registration, scenario: scenario() as unknown as PublicScenario, draft };
    expect(inspectNativeReferenceVisual(incomplete)).toEqual({
      kind: "unavailable",
      reason: "Native city assets route supports exactly osm_scene, roads, entities, and regions layers",
    });
    expect(nativeReferenceHasVisualPresentation(incomplete)).toBe(false);
  });

  it("verifies an incomplete registration before returning its explicit unavailable route", async () => {
    const assetBytes = new TextEncoder().encode('{"source":"incomplete registration"}');
    const sha256 = createHash("sha256").update(assetBytes).digest("hex");
    const publicScenario = { ...scenario(), layers: [], assets: [{ asset_id: "fixture.asset",
      replay_path: `assets/${sha256}`, sha256, size_bytes: assetBytes.byteLength,
      media_type: "application/json", selector: "unit-fixture.json", license_id: null,
      license_selector: null, license_sha256: null, license_replay_path: null,
      license_size_bytes: null }] } as unknown as PublicScenario;
    vi.stubGlobal("window", { location: { href: "http://127.0.0.1:5392/city-studio.html" } });
    const fetch = vi.fn(async () => new Response(assetBytes));
    vi.stubGlobal("fetch", fetch);
    const loaded = await loadNativeReferenceVisual({ registration, scenario: publicScenario, draft });
    expect(loaded.kind).toBe("unavailable");
    expect(loaded.unavailableReason).toContain("supports exactly");
    expect(fetch).toHaveBeenCalledTimes(1);
    loaded.dispose();
  });
});
