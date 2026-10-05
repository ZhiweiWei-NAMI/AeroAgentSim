// @vitest-environment node
import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { verifyAuditedTrafficForScene, type AuditedTrafficSceneBinding } from "./city-audited-traffic";
import { parseCityTrafficData } from "./city-presentation";
import { parseCitySceneConfig } from "./city-scene-config";
import { snapshotTrafficPreviewDraft, type VerifiedTrafficPreviewArtifact } from "./city-traffic-preview-api";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";

const digest = (value: string | Uint8Array): string => createHash("sha256").update(value).digest("hex");
const sha = (character: string): string => character.repeat(64);

/** Synthetic contract fixtures are module tests, not SUMO execution or audit evidence. */
function fixture(changeTrace?: (trace: Record<string, any>) => void) {
  const path = "/city-presentation/test-city.json";
  const draft = { ...createDefaultCityWorkspaceConfig(), scenePath: path, seed: 3,
    traffic: { vehicles: 1, bicycles: 0, pedestrians: 1 } };
  const context = { scene_id: "test-city", building_geometry: { count: 1 },
    source_network_sha256: sha("a") };
  const asset = (name: string, hash: string) => ({
    url: `/city-presentation/${name}.json`, sha256: hash, size_bytes: 1,
  });
  const scene = parseCitySceneConfig({
    schema_version: "aero-bench.city-scene-preview/v1", name: "Contract test city", initial_mood: "day",
    mesh_pack: { base_url: "/test-pack/", road_surface_texture: "/road.png",
      manifest: { sha256: sha("d"), size_bytes: 1 } },
    traffic_signal_model: { url: "/models/signal.glb", sha256: sha("1"), size_bytes: 1 },
    building_render: { base_url: "/test-buildings/", source_scene_id: "test-city",
      manifest: { sha256: sha("2"), size_bytes: 1 }, source_context: { sha256: sha("3"), size_bytes: 1 } },
    road_assets: {
      schema_version: "aero-bench.city-road-assets/v2", source_scene_id: "test-city",
      source_network_sha256: sha("a"), source_osm_sha256: sha("b"),
      mesh_pack_source_sha256: sha("c"), mesh_pack_manifest_sha256: sha("d"),
      displayed_surface_sha256: sha("e"), road: asset("road", sha("4")),
      effective_fixtures: asset("fixtures", sha("f")),
      traffic: asset("traffic", sha("5")), flight: asset("flight", sha("6")),
    },
  });
  const sceneBytes = JSON.stringify(scene);
  const baseline = {
    schema_version: "aero-bench.city-sumo-preview/v2", artifact_class: "offline-engineering-preview",
    source_kind: "offline-sumo-engineering-preview", source_network_sha256: sha("a"),
    source_osm_sha256: sha("b"), mesh_pack_source_sha256: sha("c"), source_context: context,
    vehicle_position_reference: "center-derived-from-native-TraCI-front-bumper-and-length",
    visual_obstacle_basis: { policy: "canonical-rendered-building-footprints-and-effective-fixtures",
      route_obstacle_basis: "canonical-rendered-building-footprints-and-effective-fixtures",
      effective_fixture_geometry_sha256: sha("f"), rendered_footprint_count: 1 },
    seed: 3, duration_seconds: 30, step_seconds: 0.25,
    signals: [{ id: "signal.1", tls: "tls.1", link: 0, x: 2, z: 3, heading: 90 }],
    demand: { authored: { sedan: 1 }, persons: 1, observed: { vehicles: 1, bicycle: 0, persons: 1 } },
    frames: Array.from({ length: 121 }, (_, index) => ({ second: index * 0.25,
      vehicles: [["vehicle.1", index, 2, 90, "sedan"]],
      persons: [["person.1", index, 3, 0]], tls: { "tls.1": "G" } })),
    demand_authoring: { schema_version: "aero-bench.city-traffic-preview-demand/v1",
      workspace_schema_version: draft.schema_version, workspace_sha256: sha("7"), workspace_size_bytes: 4096,
      seed: 3, traffic: draft.traffic },
  };
  const trace = structuredClone(baseline);
  changeTrace?.(trace);
  const bytes = new TextEncoder().encode(JSON.stringify(trace));
  const hash = digest(bytes);
  const artifact: VerifiedTrafficPreviewArtifact = {
    profile: { profile_id: "test.sumo.v1", profile_sha256: sha("8"), scene_path: path,
      scene_sha256: digest(sceneBytes), source_license_status: "documented",
      preview_scope: "offline-engineering-preview" },
    draftSnapshot: snapshotTrafficPreviewDraft(draft),
    job: { schema_version: "aero-bench.traffic-preview-job/v1", job_id: sha("9"),
      profile_id: "test.sumo.v1", profile_sha256: sha("8"), workspace_sha256: sha("7"),
      workspace_size_bytes: 4096, duration_seconds: 30, state: "ready",
      trace: { url: `/authoring/v1/traffic-previews/${sha("9")}/assets/${hash}`,
        sha256: hash, size_bytes: bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: sha("a"), size_bytes: 1 }, error: null,
      preview_scope: "offline-engineering-preview", formal_provider_bound: false, executed: false, verified: false },
    bytes, trace: trace as unknown as VerifiedTrafficPreviewArtifact["trace"],
  };
  const binding: AuditedTrafficSceneBinding = {
    scenePath: path, scene, recordedTraffic: parseCityTrafficData(baseline),
    roads: { roadUrl: "blob:road", trafficUrl: "blob:traffic", flightUrl: "blob:flight",
      sourceContext: context, displayedSurfaceSha256: sha("e"),
      fixtures: { signalIds: new Set(["signal.1"]), streetLampIndices: new Set(), signalModelSha256: sha("1") },
      vegetationRoadBinding: {} as never, dispose: vi.fn() },
  };
  const fetch = vi.fn(async () => new Response(sceneBytes));
  vi.stubGlobal("fetch", fetch);
  return { draft, artifact, binding, fetch, sceneBytes };
}

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("audited traffic scene binding", () => {
  it("reconstructs the complete trace from immutable checked bytes and pins the scene", async () => {
    const { draft, artifact, binding, fetch } = fixture();
    const pending = verifyAuditedTrafficForScene(artifact, draft, binding);
    artifact.bytes.fill(0);
    const verified = await pending;
    expect(verified.data.frames).toHaveLength(121);
    expect(verified.data.frames[0]!.vehicles).toHaveLength(1);
    expect(verified.bytes).not.toBe(artifact.bytes);
    expect(verified.job.formal_provider_bound).toBe(false);
    expect(fetch).toHaveBeenCalledExactlyOnceWith(binding.scenePath,
      expect.objectContaining({ method: "GET", redirect: "error" }));
  });

  it("rejects stale drafts and changed trace bytes before rendering or fetching", async () => {
    const { draft, artifact, binding, fetch } = fixture();
    await expect(verifyAuditedTrafficForScene(artifact, { ...draft, name: "Changed" }, binding))
      .rejects.toThrow(/草稿已经变更/);
    artifact.bytes[0] = 0;
    await expect(verifyAuditedTrafficForScene(artifact, draft, binding)).rejects.toThrow(/SHA-256/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["source_network_sha256", "source_osm_sha256", "mesh_pack_source_sha256"])(
    "rejects a byte-valid recording from a different %s", async key => {
      const { draft, artifact, binding, fetch } = fixture(trace => { trace[key] = sha("0"); });
      await expect(verifyAuditedTrafficForScene(artifact, draft, binding)).rejects.toThrow(/当前场景不一致/);
      expect(fetch).not.toHaveBeenCalled();
    });

  it.each(["source", "signal", "fixtures"])("rejects changed %s geometry", async kind => {
    const { draft, artifact, binding } = fixture(trace => {
      if (kind === "source") trace.source_context.building_geometry.count = 2;
      if (kind === "signal") trace.signals[0].x += 1;
      if (kind === "fixtures") trace.visual_obstacle_basis.effective_fixture_geometry_sha256 = sha("0");
    });
    await expect(verifyAuditedTrafficForScene(artifact, draft, binding)).rejects.toThrow(/当前场景不一致/);
  });

  it("rejects malformed frame grids even when the digest and job are valid", async () => {
    const { draft, artifact, binding } = fixture(trace => { trace.frames[1].second = 0.1; });
    await expect(verifyAuditedTrafficForScene(artifact, draft, binding)).rejects.toThrow(/frame 1/);
  });

  it("rejects changed profile or ready-state claims", async () => {
    const { draft, artifact, binding } = fixture();
    await expect(verifyAuditedTrafficForScene({ ...artifact,
      profile: { ...artifact.profile, profile_sha256: sha("0") } }, draft, binding))
      .rejects.toThrow(/档案身份/);
    await expect(verifyAuditedTrafficForScene({ ...artifact,
      job: { ...artifact.job, state: "recording" } }, draft, binding)).rejects.toThrow(/未完成/);
  });

  it("rejects republished scene bytes, including a newly parsed but different scene", async () => {
    const { draft, artifact, binding, fetch, sceneBytes } = fixture();
    fetch.mockImplementation(async () => new Response(`${sceneBytes}\n`));
    await expect(verifyAuditedTrafficForScene(artifact, draft, binding)).rejects.toThrow(/场景字节/);
    const changedBinding = { ...binding, scene: { ...binding.scene, name: "Different loaded city" } };
    fetch.mockImplementation(async () => new Response(sceneBytes));
    await expect(verifyAuditedTrafficForScene(artifact, draft, changedBinding)).rejects.toThrow(/场景字节/);
  });

  it("honors cancellation and refuses an unbound road scene", async () => {
    const { draft, artifact, binding, fetch } = fixture();
    const controller = new AbortController(); controller.abort();
    await expect(verifyAuditedTrafficForScene(artifact, draft, binding, controller.signal))
      .rejects.toMatchObject({ name: "AbortError" });
    await expect(verifyAuditedTrafficForScene(artifact, draft,
      { ...binding, scenePath: "/city-presentation/another-city.json" })).rejects.toThrow(/道路场景/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
