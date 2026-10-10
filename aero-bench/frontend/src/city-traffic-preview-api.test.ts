import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrafficPreviewJob } from "./generated/aero-bench-contracts";
import {
  fetchTrafficPreviewJob,
  fetchTrafficPreviewProfiles,
  loadReadyTrafficPreview,
  parseTrafficPreviewJob,
  parseTrafficPreviewTrace,
  snapshotTrafficPreviewDraft,
  submitTrafficPreview,
  trafficPreviewDraftMatches,
  type TrafficPreviewProfile,
} from "./city-traffic-preview-api";
import { createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";

const profile: TrafficPreviewProfile = {
  profile_id: "shanghai.sumo.authoring.v1",
  profile_sha256: "a".repeat(64),
  scene_path: "/city-presentation/default-scene-v1.json",
  scene_sha256: "b".repeat(64),
  source_license_status: "documented",
  preview_scope: "offline-engineering-preview",
};
const catalog = {
  schema_version: "aero-bench.traffic-preview-profile-catalog/v1",
  profiles: [profile],
};
const workspaceSha = "c".repeat(64);
const jobId = "d".repeat(64);

function job(state: TrafficPreviewJob["state"], overrides: Partial<TrafficPreviewJob> = {}): TrafficPreviewJob {
  return {
    schema_version: "aero-bench.traffic-preview-job/v1",
    job_id: jobId,
    profile_id: profile.profile_id,
    profile_sha256: profile.profile_sha256,
    workspace_sha256: workspaceSha,
    workspace_size_bytes: 4096,
    duration_seconds: 120,
    state,
    trace: null,
    canonical_audit: null,
    error: state === "failed" ? { code: "sumo_failed", message: "SUMO exited" } : null,
    preview_scope: "offline-engineering-preview",
    formal_provider_bound: false,
    executed: false,
    verified: false,
    ...overrides,
  } as TrafficPreviewJob;
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function apiError(code: string, message: string): unknown {
  return { schema_version: "aero-bench.authoring-error/v1", error: { code, message } };
}

function draft(): CityWorkspaceConfig {
  return { ...createDefaultCityWorkspaceConfig(), seed: 24427,
    traffic: { vehicles: 60, pedestrians: 36, bicycles: 12 } };
}

function trace(workspace = workspaceSha, workspaceSize = 4096): Record<string, unknown> {
  return {
    schema_version: "aero-bench.city-sumo-preview/v2",
    artifact_class: "offline-engineering-preview",
    source_kind: "offline-sumo-engineering-preview",
    seed: 24427,
    duration_seconds: 120,
    demand_authoring: {
      schema_version: "aero-bench.city-traffic-preview-demand/v1",
      workspace_schema_version: "aero-bench.city-workspace/v3",
      workspace_sha256: workspace,
      workspace_size_bytes: workspaceSize,
      seed: 24427,
      traffic: { vehicles: 60, pedestrians: 36, bicycles: 12 },
    },
    demand: { authored: { sedan: 60, bicycle: 12 }, persons: 36, observed: {} },
    signals: [],
    frames: [],
  };
}

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("traffic preview API", () => {
  it("loads the registered catalog without choosing a default profile", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, catalog));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchTrafficPreviewProfiles()).resolves.toEqual({ kind: "ok", catalog });
    expect(fetchMock).toHaveBeenCalledWith("/authoring/v1/traffic-preview-profiles",
      expect.objectContaining({ method: "GET", redirect: "error" }));
  });

  it("returns only the declared unconfigured catalog response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503,
      apiError("traffic_preview_unconfigured", "未配置 SUMO 预览档案"))));
    await expect(fetchTrafficPreviewProfiles()).resolves.toEqual({
      kind: "error", status: 503, code: "traffic_preview_unconfigured", detail: "未配置 SUMO 预览档案",
    });

    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503, apiError("wrong_code", "wrong"))));
    await expect(fetchTrafficPreviewProfiles()).rejects.toThrow(/未声明的错误/);
  });

  it("submits one strict v3 snapshot and accepts the POST workspace identity as authoritative", async () => {
    const accepted = job("queued");
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(202, accepted));
    vi.stubGlobal("fetch", fetchMock);
    const outcome = await submitTrafficPreview(profile, 120, draft());
    expect(outcome.kind).toBe("accepted");
    if (outcome.kind !== "accepted") throw new Error("submission was not accepted");
    expect(outcome.submission.acceptedJob.workspace_sha256).toBe(workspaceSha);
    expect(outcome.submission.draftSnapshot.localRevision).not.toBe(workspaceSha);
    expect(Object.isFrozen(outcome.submission.draftSnapshot.draft)).toBe(true);
    expect(Object.isFrozen(outcome.submission.draftSnapshot.draft.traffic)).toBe(true);
    const request = fetchMock.mock.calls[0]![1];
    expect(request).toBeDefined();
    expect(JSON.parse(String(request!.body))).toMatchObject({
      schema_version: "aero-bench.traffic-preview-request/v1",
      profile_id: profile.profile_id,
      profile_sha256: profile.profile_sha256,
      duration_seconds: 120,
      draft: { schema_version: "aero-bench.city-workspace/v3", scenePath: profile.scene_path },
    });
    const wrongCensus = trace();
    (wrongCensus.demand as { authored: Record<string, number> }).authored.sedan = 59;
    expect(() => parseTrafficPreviewTrace(wrongCensus, outcome.submission, accepted))
      .toThrow(/机动车或自行车需求/);
    const legacyWorkspace = trace();
    (legacyWorkspace.demand_authoring as { workspace_schema_version: string }).workspace_schema_version =
      "aero-bench.city-workspace/v2";
    expect(() => parseTrafficPreviewTrace(legacyWorkspace, outcome.submission, accepted))
      .toThrow(/工作区需求身份/);
  });

  it("uses the backend's zero default when an exact zero bicycle census omits that key", async () => {
    const accepted = job("queued");
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(202, accepted)));
    const current = draft();
    current.traffic.bicycles = 0;
    const outcome = await submitTrafficPreview(profile, 120, current);
    if (outcome.kind !== "accepted") throw new Error("submission was not accepted");
    const payload = trace();
    (payload.demand_authoring as { traffic: { bicycles: number } }).traffic.bicycles = 0;
    delete (payload.demand as { authored: Record<string, number> }).authored.bicycle;
    expect(parseTrafficPreviewTrace(payload, outcome.submission, accepted).demand_authoring.traffic.bicycles).toBe(0);
  });

  it("rejects scene/profile mismatch before POST and detects local draft staleness", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const current = draft();
    const snapshot = snapshotTrafficPreviewDraft(current);
    expect(trafficPreviewDraftMatches(snapshot, current)).toBe(true);
    current.traffic.vehicles += 1;
    expect(trafficPreviewDraftMatches(snapshot, current)).toBe(false);
    await expect(submitTrafficPreview({ ...profile, scene_path: "/city-presentation/other.json" }, 120, draft()))
      .rejects.toThrow(/场景路径不一致/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns the declared stale profile conflict instead of treating it as an unknown status", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(409,
      apiError("traffic_preview_profile_conflict", "档案哈希已更新"))));
    await expect(submitTrafficPreview(profile, 120, draft())).resolves.toEqual({
      kind: "error", status: 409, code: "traffic_preview_profile_conflict", detail: "档案哈希已更新",
    });
  });

  it("pins job, profile, workspace, and duration identities across polling", async () => {
    const responses = [job("queued"), job("recording"), job("auditing", { workspace_sha256: "e".repeat(64) })];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, responses.shift());
      return jsonResponse(200, responses.shift());
    });
    vi.stubGlobal("fetch", fetchMock);
    const submitted = await submitTrafficPreview(profile, 120, draft());
    if (submitted.kind !== "accepted") throw new Error("submission was not accepted");
    await expect(fetchTrafficPreviewJob(submitted.submission)).resolves.toMatchObject({
      kind: "found", job: { state: "recording", workspace_sha256: workspaceSha },
    });
    await expect(fetchTrafficPreviewJob(submitted.submission)).rejects.toThrow(/身份在提交后发生变化/);
  });

  it("rejects state-inconsistent jobs that JSON Schema model validators cannot express", () => {
    expect(() => parseTrafficPreviewJob(job("ready"))).toThrow(/ready/);
    expect(() => parseTrafficPreviewJob(job("recording", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${"f".repeat(64)}`,
        sha256: "f".repeat(64), size_bytes: 10, media_type: "application/json" },
    }))).toThrow(/提前发布/);
    expect(() => parseTrafficPreviewJob(job("failed", { error: null }))).toThrow(/failed/);
    expect(() => parseTrafficPreviewJob(job("queued", { verified: true as false }))).toThrow(/verified|constant/);
  });

  it("loads exact ready bytes and preserves the complete audited trace", async () => {
    const payload = trace();
    const bytes = new TextEncoder().encode(JSON.stringify(payload));
    const digest = createHash("sha256").update(bytes).digest("hex");
    const ready = job("ready", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${digest}`,
        sha256: digest, size_bytes: bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: "f".repeat(64), size_bytes: 2048 },
      error: null,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, ready);
      return new Response(bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(bytes.byteLength), ETag: `"${digest}"`,
      } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const submitted = await submitTrafficPreview(profile, 120, draft());
    if (submitted.kind !== "accepted") throw new Error("submission was not accepted");
    const artifact = await loadReadyTrafficPreview(submitted.submission, ready);
    expect(Array.from(artifact.bytes)).toEqual(Array.from(bytes));
    expect(artifact.profile).toEqual(profile);
    expect(artifact.draftSnapshot).toBe(submitted.submission.draftSnapshot);
    expect(artifact.trace).toMatchObject(payload);
    expect(artifact.trace.frames).toEqual([]);
  });

  it("rejects artifact metadata and demand identities before handing a trace to the caller", async () => {
    const payload = trace("9".repeat(64));
    const bytes = new TextEncoder().encode(JSON.stringify(payload));
    const digest = createHash("sha256").update(bytes).digest("hex");
    const ready = job("ready", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${digest}`,
        sha256: digest, size_bytes: bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: "f".repeat(64), size_bytes: 2048 }, error: null,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, ready);
      return new Response(bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(bytes.byteLength), ETag: `"${digest}"`,
      } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const submitted = await submitTrafficPreview(profile, 120, draft());
    if (submitted.kind !== "accepted") throw new Error("submission was not accepted");
    await expect(loadReadyTrafficPreview(submitted.submission, ready)).rejects.toThrow(/工作区需求身份/);

    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, ready);
      return new Response(bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(bytes.byteLength), ETag: `"${"0".repeat(64)}"`,
      } });
    }));
    const second = await submitTrafficPreview(profile, 120, draft());
    if (second.kind !== "accepted") throw new Error("submission was not accepted");
    await expect(loadReadyTrafficPreview(second.submission, ready)).rejects.toThrow(/ETag/);
  });
});
