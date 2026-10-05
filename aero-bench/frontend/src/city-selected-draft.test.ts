// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AuthoringCatalog, AuthoringJob, VerifiedStaticPresentation } from "./city-authoring-api";
import { SHANGHAI_ORIGIN, SHANGHAI_SOURCE_ID, SHANGHAI_SOURCE_SHA256,
  type SceneSelection } from "./city-region-selector";
import { createSelectedSceneDraft, importSelectedSceneDraft, parseSelectedSceneDraft,
  restoreSelectedSceneDraft, selectedDraftStatus, selectionDigest, verifySelectedSceneDraft,
  verifySelectedSceneSource } from "./city-selected-draft";

const selection: SceneSelection = {
  schema_version: "aero-bench.scene-selection/v1",
  source_id: SHANGHAI_SOURCE_ID,
  source_sha256: SHANGHAI_SOURCE_SHA256,
  origin: SHANGHAI_ORIGIN,
  bounds_enu_m: { min_east_m: 300, max_east_m: 780, min_north_m: 410, max_north_m: 700 },
};
// Published by the real authoring service for the selection above.
const selectionSha = "7d5904868d1f437c377574e267962a7152193fb38b8e1d80b89f6b822abcb09c";
const jobId = "1532a422347d1d24dd88f92dc020c0a95e91520e251b4950755f851cd3fac191";
const packSha = "1f1d461f4eafad099b78c50d6a356cb3ad83c3ab23d4ed1d105e64e8ebe5a8b4";
const presentationSha = "b56679941fef38c1097f8abd0c3b1d0dcbe7c13bd09d73d5e1e465aea59514cf";
const job: AuthoringJob = {
  schema_version: "aero-bench.scene-build-job/v1", job_id: jobId,
  selection_sha256: selectionSha, source_sha256: SHANGHAI_SOURCE_SHA256,
  state: "ready", compiler_manifest_sha256: "a".repeat(64), error: null,
  pack: { base_url: `/authoring/v1/scenes/${jobId}/pack/`,
    manifest: { sha256: packSha, size_bytes: 142572 }, source_sha256: "b".repeat(64) },
  presentation: { base_url: `/authoring/v1/scenes/${jobId}/presentation/`,
    manifest: { sha256: presentationSha, size_bytes: 1794 } },
};
const catalog: AuthoringCatalog = {
  schema_version: "aero-bench.scene-source-catalog/v1",
  sources: [{ source_id: SHANGHAI_SOURCE_ID, sha256: SHANGHAI_SOURCE_SHA256,
    size_bytes: 4868022, display_name: "上海中心 OSM", origin: SHANGHAI_ORIGIN,
    bounds_wgs84: { min_latitude_deg: 31.2228, max_latitude_deg: 31.2371,
      min_longitude_deg: 121.4636, max_longitude_deg: 121.4868 },
    data_url: `/authoring/v1/sources/${SHANGHAI_SOURCE_ID}` }],
};

afterEach(() => vi.unstubAllGlobals());

describe("selected city draft identity", () => {
  it("matches the service's real canonical selection SHA, including float-valued integer coordinates", async () => {
    expect(await selectionDigest(selection)).toBe(selectionSha);
    const draft = await createSelectedSceneDraft(selection, job);
    expect(draft.selection_sha256).toBe(selectionSha);
    expect(draft.pack_manifest_sha256).toBe(packSha);
    expect(draft.presentation_manifest_sha256).toBe(presentationSha);
  });

  it("rejects modified imported bounds before fetching a job", async () => {
    const draft = await createSelectedSceneDraft(selection, job);
    const changed = { ...draft, selection: { ...selection,
      bounds_enu_m: { ...selection.bounds_enu_m, max_east_m: 779 } } };
    await expect(importSelectedSceneDraft(JSON.stringify(changed))).rejects.toThrow(/SceneSelection/);
    const fetchCatalog = vi.fn(async () => catalog);
    const fetchJob = vi.fn(async () => job);
    const loadPresentation = vi.fn();
    const result = await restoreSelectedSceneDraft(changed, { fetchCatalog, fetchJob, loadPresentation });
    expect(result.ok).toBe(false);
    expect(fetchCatalog).not.toHaveBeenCalled();
    expect(fetchJob).not.toHaveBeenCalled();
    expect(loadPresentation).not.toHaveBeenCalled();
  });

  it("rejects a changed source, job, pack or presentation before display", async () => {
    const draft = await createSelectedSceneDraft(selection, job);
    expect(verifySelectedSceneSource(draft, { ...catalog, sources: [] })).toHaveLength(1);
    expect(verifySelectedSceneSource(draft, { ...catalog, sources: [{ ...catalog.sources[0]!, sha256: "c".repeat(64) }] })).toHaveLength(1);
    expect(verifySelectedSceneDraft(draft, { ...job, job_id: "d".repeat(64) })).toHaveLength(1);
    expect(verifySelectedSceneDraft(draft, { ...job, pack: { ...job.pack!, manifest: { ...job.pack!.manifest,
      sha256: "e".repeat(64) } } })).toHaveLength(1);
    expect(verifySelectedSceneDraft(draft, { ...job, presentation: { ...job.presentation!,
      manifest: { ...job.presentation!.manifest, sha256: "f".repeat(64) } } })).toHaveLength(1);
    expect(verifySelectedSceneDraft(draft, { ...job, state: "failed", error: { code: "failed", message: "failed" },
      pack: null, presentation: null })).not.toHaveLength(0);
  });

  it("loads only a bound ready presentation and refuses a presentation mismatch", async () => {
    const draft = await createSelectedSceneDraft(selection, job);
    const dispose = vi.fn();
    const presentation = { pack: { dispose }, manifest: { job_id: jobId,
      selection_sha256: selectionSha, raw_source_sha256: SHANGHAI_SOURCE_SHA256,
      pack_manifest: { sha256: packSha } } } as unknown as VerifiedStaticPresentation;
    const deps = { fetchCatalog: vi.fn(async () => catalog), fetchJob: vi.fn(async () => job),
      loadPresentation: vi.fn(async () => presentation) };
    const restored = await restoreSelectedSceneDraft(draft, deps);
    expect(restored.ok).toBe(true);
    expect(deps.loadPresentation).toHaveBeenCalledWith(job, selection);
    deps.loadPresentation.mockResolvedValueOnce({ ...presentation, manifest: {
      ...presentation.manifest, selection_sha256: "c".repeat(64),
    } });
    const rejected = await restoreSelectedSceneDraft(draft, deps);
    expect(rejected.ok).toBe(false);
    expect(dispose).toHaveBeenCalledTimes(1);
  });

  it("keeps the old workspace schema out and waits for browser sceneReady", async () => {
    const draft = await createSelectedSceneDraft(selection, job);
    expect(() => parseSelectedSceneDraft({ ...draft, scenePath: "/city-presentation/default-scene-v1.json" })).toThrow(/字段/);
    expect(selectedDraftStatus({ identity: draft, sceneReady: false, busy: false,
      saved: false, error: null }).state).toBe("dirty");
    expect(selectedDraftStatus({ identity: draft, sceneReady: true, busy: false,
      saved: true, error: null }).state).toBe("saved");
  });
});
