// @vitest-environment node
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssetResolver } from "./asset-resolver";
import { VerifiedVisualAssets, compileCityDraft, fetchAuthoringSource, fetchCompilation, fetchNativeSceneCatalog,
  loadReadyAuthoringPack, parseAuthoringCatalog, parseAuthoringJob, parseCityCompilationResult, parseNativeSceneCatalog,
  parseStaticPresentationManifest, parseStaticSignalInventory, parseVisualAssetInventory, submitSceneSelection,
  type CityCompilationResult } from "./city-authoring-api";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256, type SceneSelection } from "./city-region-selector";
import { createDefaultCityWorkspaceConfig } from "./city-workspace-config";

const source = {
  source_id: SHANGHAI_SOURCE_ID, sha256: SHANGHAI_SOURCE_SHA256,
  size_bytes: 4_868_022, display_name: "上海中心 OSM", origin: SHANGHAI_ORIGIN,
  bounds_wgs84: { min_latitude_deg: 31.2228, max_latitude_deg: 31.2371,
    min_longitude_deg: 121.4636, max_longitude_deg: 121.4868 },
  data_url: `/authoring/v1/sources/${SHANGHAI_SOURCE_ID}`,
};
const catalog = { schema_version: "aero-bench.scene-source-catalog/v1", sources: [source] };
const jobId = "a".repeat(64);
const queued = { schema_version: "aero-bench.scene-build-job/v1", job_id: jobId,
  selection_sha256: "b".repeat(64), source_sha256: SHANGHAI_SOURCE_SHA256,
  state: "queued", compiler_manifest_sha256: null, pack: null, presentation: null, error: null };
const selection: SceneSelection = { schema_version: "aero-bench.scene-selection/v1",
  source_id: SHANGHAI_SOURCE_ID, source_sha256: SHANGHAI_SOURCE_SHA256, origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: -300, max_east_m: 300, min_north_m: -300, max_north_m: 300 } };

afterEach(() => vi.unstubAllGlobals());

describe("authoring API wire contract", () => {
  it("reads the exact catalog object and refuses a path or field drift", () => {
    expect(parseAuthoringCatalog(catalog).sources[0]).toEqual(source);
    expect(() => parseAuthoringCatalog([source])).toThrow();
    expect(() => parseAuthoringCatalog({ ...catalog, sources: [{ ...source, data_url: "/other-source" }] })).toThrow(/数据 URL/);
    expect(() => parseAuthoringCatalog({ ...catalog, extra: true })).toThrow(/字段/);
  });

  it("requires explicit fields for every job state and the effective OSM hash", () => {
    expect(parseAuthoringJob(queued).state).toBe("queued");
    const ready = { ...queued, state: "ready", compiler_manifest_sha256: "c".repeat(64),
      pack: { base_url: `/authoring/v1/scenes/${jobId}/pack/`, manifest: { sha256: "d".repeat(64), size_bytes: 12345 }, source_sha256: "e".repeat(64) },
      presentation: { base_url: `/authoring/v1/scenes/${jobId}/presentation/`,
        manifest: { sha256: "f".repeat(64), size_bytes: 321 } } };
    expect(parseAuthoringJob(ready).pack?.source_sha256).toBe("e".repeat(64));
    expect(() => parseAuthoringJob({ ...ready, pack: { ...ready.pack, base_url: "/old-pack/" } })).toThrow(/路径/);
    expect(() => parseAuthoringJob({ ...ready, presentation: null })).toThrow(/状态字段/);
    expect(parseAuthoringJob({ ...queued, state: "networking", compiler_manifest_sha256: "c".repeat(64) }).state).toBe("networking");
    expect(() => parseAuthoringJob({ ...queued, state: "ready" })).toThrow(/状态字段/);
    expect(() => parseAuthoringJob({ ...queued, error: { code: "error", message: "failed" } })).toThrow(/状态字段/);
    expect(() => parseAuthoringJob({ ...queued, source_sha256: undefined })).toThrow(/SHA-256/);
  });

  it("checks original OSM bytes, declared size and ETag", async () => {
    const bytes = readFileSync(resolve(process.cwd(), "public/osm2world/shanghai-hongqiao.osm.json"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, {
      status: 200, headers: { "Content-Length": String(bytes.length), ETag: `"${SHANGHAI_SOURCE_SHA256}"` },
    })));
    const fetched = await fetchAuthoringSource(source);
    expect(fetched.byteLength).toBe(4_868_022);
    expect(fetch).toHaveBeenCalledWith(source.data_url, expect.objectContaining({ redirect: "error" }));
    const wrong = Buffer.from(bytes); wrong[100] = wrong[100]! ^ 1;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(wrong, {
      status: 200, headers: { "Content-Length": String(wrong.length), ETag: `"${SHANGHAI_SOURCE_SHA256}"` },
    })));
    await expect(fetchAuthoringSource(source)).rejects.toThrow(/SHA-256/);
  });

  it("shows declared HTTP errors without inventing a job", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      schema_version: "aero-bench.authoring-error/v1", error: { code: "invalid_scene_selection", message: "选区越界" },
    }), { status: 400, headers: { "Content-Type": "application/json" } })));
    await expect(submitSceneSelection(selection)).rejects.toThrow(/invalid_scene_selection：选区越界/);
  });

  it("rejects a verified pack manifest whose projection origin differs from the selection", async () => {
    const bytes = readFileSync(resolve(process.cwd(), "public/osm2world/packs/shanghai-huangpu-east-v1/manifest.json"));
    const manifest = JSON.parse(bytes.toString("utf8")) as { source: { sha256: string } };
    const digest = createHash("sha256").update(bytes).digest("hex");
    const ready = parseAuthoringJob({ ...queued, state: "ready", compiler_manifest_sha256: "c".repeat(64),
      pack: { base_url: `/authoring/v1/scenes/${jobId}/pack/`,
        manifest: { sha256: digest, size_bytes: bytes.length }, source_sha256: manifest.source.sha256 },
      presentation: { base_url: `/authoring/v1/scenes/${jobId}/presentation/`,
        manifest: { sha256: "f".repeat(64), size_bytes: 321 } } });
    vi.stubGlobal("window", { location: { href: "http://localhost/city-studio.html", origin: "http://localhost" } });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(bytes, {
      status: 200, headers: { "Content-Length": String(bytes.length) },
    })));
    const displaced = { ...selection, origin: { ...selection.origin, longitude_deg: selection.origin.longitude_deg + 0.01 } };
    await expect(loadReadyAuthoringPack(ready, displaced)).rejects.toThrow(/投影原点/);
    expect(fetch).toHaveBeenCalledWith(new URL(`${ready.pack!.base_url}manifest.json`, "http://localhost"), expect.anything());
  });

  it("requires exact static bundle identities and an ordered, pinned visual inventory", () => {
    const ref = { sha256: "a".repeat(64), size_bytes: 12 };
    const raw = { schema_version: "aero-bench.city-static-presentation/v1", job_id: jobId,
      selection_sha256: "b".repeat(64), source_id: SHANGHAI_SOURCE_ID,
      raw_source_sha256: SHANGHAI_SOURCE_SHA256, effective_osm_sha256: "c".repeat(64),
      sumo_source_osm_sha256: "d".repeat(64), compiler_manifest_sha256: "e".repeat(64),
      origin: SHANGHAI_ORIGIN, pack_manifest: ref,
      network: { ...ref, projection: "+proj=aeqd +lat_0=31.2304 +lon_0=121.4737", sumo_image_id: `sha256:${"f".repeat(64)}` },
      road: ref, building_placement: ref, signal_inventory: ref, visual_assets: ref,
      osm2world_style_tree_sha256: "1".repeat(64), bigcity_library_tree_sha256: "2".repeat(64) };
    expect(parseStaticPresentationManifest(raw).network.projection).toContain("aeqd");
    expect(() => parseStaticPresentationManifest({ ...raw, visual_assets: undefined })).toThrow(/网格 manifest|大小/);
    expect(() => parseStaticPresentationManifest({ ...raw, network: { ...raw.network, sumo_image_id: "latest" } })).toThrow(/digest/);
    expect(parseStaticSignalInventory({ schema_version: "aero-bench.city-static-signal-inventory/v1",
      source_network_sha256: "a".repeat(64), mesh_pack_source_sha256: "b".repeat(64), signals: [] }).signals).toHaveLength(0);
    expect(() => parseStaticSignalInventory({ schema_version: "aero-bench.city-static-signal-inventory/v1",
      source_network_sha256: "a".repeat(64), mesh_pack_source_sha256: "b".repeat(64),
      signals: [{ id: "s", tls: "t", link: "0", x: 0, z: 0, heading: 0 }] })).toThrow(/有限数值/);
    const visual = { schema_version: "aero-bench.city-visual-assets/v1", assets: [
      { path: "/models/bigcity/manifest.json", sha256: "a".repeat(64), size_bytes: 12, media_type: "application/json" },
      { path: "/models/incoming/furniture/glb/street_light_8.glb", sha256: "b".repeat(64), size_bytes: 19, media_type: "model/gltf-binary" },
    ] };
    expect(parseVisualAssetInventory(visual)).toHaveLength(2);
    expect(() => parseVisualAssetInventory({ ...visual, assets: [...visual.assets].reverse() })).toThrow(/排序/);
    expect(() => parseVisualAssetInventory({ ...visual, assets: [{ ...visual.assets[0], path: "/models/bigcity/../secret.json" }] })).toThrow(/路径/);
  });

  it("serves only content-verified visual bytes and counts each used digest once", async () => {
    const path = "/models/bigcity/manifest.json";
    const body = Buffer.from('{"entries":[]}');
    const digest = createHash("sha256").update(body).digest("hex");
    let delivered = body;
    const resolver = new AssetResolver({ baseHref: "http://localhost/presentation/",
      fetch: vi.fn(async () => new Response(delivered, {
        status: 200, headers: { "Content-Length": String(delivered.length) },
      })) as typeof fetch,
      createObjectUrl: () => "blob:test-verified-manifest" });
    const assets = new VerifiedVisualAssets([{ path, sha256: digest, size_bytes: body.length,
      media_type: "application/json" }], resolver);
    expect(await assets.json(path)).toEqual({ entries: [] });
    await assets.preload([path, path]);
    expect(assets.cachedUrl(path)).toBe("blob:test-verified-manifest");
    expect(assets.stats).toEqual({ fileCount: 1, byteCount: body.length });
    await expect(assets.url("/models/bigcity/unknown.fbx")).rejects.toThrow(/未在已验证目录/);
    assets.dispose();
    delivered = Buffer.from(body); delivered[3] = delivered[3]! ^ 1;
    const corrupt = new VerifiedVisualAssets([{ path, sha256: digest, size_bytes: body.length,
      media_type: "application/json" }], new AssetResolver({ baseHref: "http://localhost/presentation/",
      fetch: vi.fn(async () => new Response(delivered, { status: 200 })) as typeof fetch }));
    await expect(corrupt.json(path)).rejects.toThrow(/digest|size/);
    corrupt.dispose();
  });
});

describe("native scene catalog and draft compilation (I1)", () => {
  const registrationId = "inspection.reference.explicit";
  const registration = {
    registration_id: registrationId,
    registration_sha256: "a".repeat(64),
    scene_path: "/city-presentation/default-scene-v1.json",
    scene_url: `/authoring/v1/native-scenes/${registrationId}/scene`,
    scene_schema_version: "aero-bench.public-scenario/v1",
    scene_sha256: "b".repeat(64),
    scene_size_bytes: 157730,
    world_id: "world.inspection-reference",
    world_digest: "c".repeat(64),
    profile_id: "inspection.reference.v1",
    editable_execution_fields: ["/seed"],
    retained_authoring_fields: ["/name", "/environment", "/stateKeyframes"],
    reference_draft: createDefaultCityWorkspaceConfig(),
  };
  const catalog = { schema_version: "aero-bench.city-scene-registration-catalog/v1", registrations: [registration] };
  const draft = createDefaultCityWorkspaceConfig();
  const compiledResult: CityCompilationResult = {
    schema_version: "aero-bench.city-compilation-result/v1",
    compilation_id: "d".repeat(64), draft_sha256: "e".repeat(64),
    registration_id: registrationId, registration_sha256: "a".repeat(64),
    status: "compiled", blockers: [],
    suite: { path: "compiled/suite.json", sha256: "f".repeat(64) },
    runs: [{ run_id: "1".repeat(64), scenario_digest: "2".repeat(64), world_id: "world.inspection-reference",
      world_digest: "c".repeat(64), executor_kind: "docker_reference", feasible: true }],
    executed: false, verified: false,
  };
  const blockedResult: CityCompilationResult = {
    schema_version: "aero-bench.city-compilation-result/v1",
    compilation_id: "d".repeat(64), draft_sha256: "e".repeat(64),
    registration_id: registrationId, registration_sha256: "a".repeat(64),
    status: "blocked",
    blockers: [{ code: "unsupported_field", field: "/fleet/1/assetId", message: "preview-only airframe cannot be lowered" }],
    suite: null, runs: [], executed: false, verified: false,
  };
  const apiError = (code: string, message: string) => ({
    schema_version: "aero-bench.authoring-error/v1", error: { code, message },
  });
  const jsonResponse = (status: number, body: unknown) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

  it("parses the catalog, including the explicit empty-registration state", () => {
    expect(parseNativeSceneCatalog(catalog).registrations[0]?.registration_id).toBe(registrationId);
    expect(parseNativeSceneCatalog({ ...catalog, registrations: [] }).registrations).toHaveLength(0);
    expect(() => parseNativeSceneCatalog({ ...catalog, schema_version: "wrong" })).toThrow(/SceneRegistrationCatalog validation failed/);
    expect(() => parseNativeSceneCatalog({ ...catalog,
      registrations: [{ ...registration, scene_url: "/authoring/v1/native-scenes/other/scene" }] })).toThrow(/场景 URL/);
    expect(() => parseNativeSceneCatalog({ ...catalog,
      registrations: [registration, registration] })).toThrow(/重复/);
    expect(() => parseNativeSceneCatalog({ ...catalog,
      registrations: [{ ...registration, scene_schema_version: "aero-bench.city-scene-preview/v1" }] }))
      .toThrow(/SceneRegistrationCatalog validation failed/);
  });

  it("fetches the catalog across every declared status code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, catalog)));
    const ok = await fetchNativeSceneCatalog();
    expect(ok).toEqual({ kind: "ok", catalog: parseNativeSceneCatalog(catalog) });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503, apiError("compiler_unconfigured", "未配置编译器"))));
    const unconfigured = await fetchNativeSceneCatalog();
    expect(unconfigured).toEqual({ kind: "error", status: 503, code: "compiler_unconfigured", detail: "未配置编译器" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(500, apiError("authoring_internal_error", "boom"))));
    await expect(fetchNativeSceneCatalog()).rejects.toThrow(/未声明的 HTTP 状态码/);
  });

  it("validates compilation-result consistency the same way the backend does", () => {
    expect(parseCityCompilationResult(compiledResult)).toEqual(compiledResult);
    expect(parseCityCompilationResult(blockedResult)).toEqual(blockedResult);
    expect(() => parseCityCompilationResult({ ...compiledResult, blockers: [blockedResult.blockers[0]] }))
      .toThrow(/不一致/);
    expect(() => parseCityCompilationResult({ ...blockedResult, suite: compiledResult.suite })).toThrow(/不一致/);
    expect(() => parseCityCompilationResult({ ...compiledResult, executed: true })).toThrow(/executed/);
    expect(() => parseCityCompilationResult({ ...compiledResult, verified: true })).toThrow(/verified/);
    expect(() => parseCityCompilationResult({ ...blockedResult,
      blockers: [{ ...blockedResult.blockers[0], field: "no-leading-slash" }] })).toThrow(/CityCompilationResult validation failed/);
  });

  it("compiles across every declared response code", async () => {
    const fetchMock = vi.fn(async (_input?: RequestInfo | URL, _init?: RequestInit) => jsonResponse(201, compiledResult));
    vi.stubGlobal("fetch", fetchMock);
    const compiled = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(compiled).toEqual({ kind: "compiled", result: compiledResult });
    expect(fetchMock).toHaveBeenCalledWith("/authoring/v1/compilations", expect.objectContaining({
      method: "POST", headers: { "Content-Type": "application/json" },
    }));
    const sentBody = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body)) as Record<string, unknown>;
    expect(sentBody).toMatchObject({
      schema_version: "aero-bench.city-compile-request/v1",
      registration_id: registrationId, registration_sha256: "a".repeat(64),
    });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(422, blockedResult)));
    const blocked = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(blocked).toEqual({ kind: "blocked", result: blockedResult });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(400, apiError("invalid_compile_request", "草稿字段无效"))));
    const invalid = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(invalid).toEqual({ kind: "error", status: 400, code: "invalid_compile_request", detail: "草稿字段无效" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(415, apiError("invalid_content_type", "需要 JSON"))));
    const unsupported = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(unsupported).toEqual({ kind: "error", status: 415, code: "invalid_content_type", detail: "需要 JSON" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503, apiError("compiler_unconfigured", "未配置编译器"))));
    const serviceUnavailable = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(serviceUnavailable).toEqual({ kind: "error", status: 503, code: "compiler_unconfigured", detail: "未配置编译器" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(500, apiError("authoring_internal_error", "boom"))));
    const internalError = await compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft });
    expect(internalError).toEqual({ kind: "error", status: 500, code: "authoring_internal_error", detail: "boom" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(409, apiError("conflict", "boom"))));
    await expect(compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft }))
      .rejects.toThrow(/未声明的 HTTP 状态码/);
  });

  it("rejects a compiled body that disagrees with its own HTTP status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(201, blockedResult)));
    await expect(compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft }))
      .rejects.toThrow(/HTTP 201/);
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(422, compiledResult)));
    await expect(compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft }))
      .rejects.toThrow(/HTTP 422/);
  });

  it("rejects a compilation result bound to a different registration than requested", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(201, { ...compiledResult, registration_id: "other.registration" })));
    await expect(compileCityDraft({ registration_id: registrationId, registration_sha256: "a".repeat(64), draft }))
      .rejects.toThrow(/注册身份/);
  });

  it("fetches a published compilation across every declared response code", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, compiledResult)));
    expect(await fetchCompilation(compiledResult.compilation_id)).toEqual({ kind: "found", result: compiledResult });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(404, apiError("unknown_compilation", "未找到"))));
    expect(await fetchCompilation(compiledResult.compilation_id)).toEqual({ kind: "error", status: 404, code: "unknown_compilation", detail: "未找到" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(500, apiError("authoring_internal_error", "boom"))));
    expect(await fetchCompilation(compiledResult.compilation_id)).toEqual({ kind: "error", status: 500, code: "authoring_internal_error", detail: "boom" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503, apiError("compiler_unconfigured", "未配置编译器"))));
    expect(await fetchCompilation(compiledResult.compilation_id)).toEqual({ kind: "error", status: 503, code: "compiler_unconfigured", detail: "未配置编译器" });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(302, {})));
    await expect(fetchCompilation(compiledResult.compilation_id)).rejects.toThrow(/未声明的 HTTP 状态码/);

    await expect(fetchCompilation("not-a-digest")).rejects.toThrow(/编译 ID/);
  });

  it("rejects a non-JSON response body as an explicit error, not a silent empty result", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html></html>", {
      status: 200, headers: { "Content-Type": "text/html" },
    })));
    await expect(fetchNativeSceneCatalog()).rejects.toThrow(/JSON/);
  });
});
