import { createHash, webcrypto } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrafficPreviewJob } from "./generated/aero-bench-contracts";
import type { TrafficPreviewProfile } from "./city-traffic-preview-api";
import { renderCityTrafficPreviewPanel } from "./city-traffic-preview-panel";
import { createDefaultCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";

const profile: TrafficPreviewProfile = {
  profile_id: "shanghai.sumo.authoring.v1",
  profile_sha256: "a".repeat(64),
  scene_path: "/city-presentation/default-scene-v1.json",
  scene_sha256: "b".repeat(64),
  source_license_status: "documented",
  preview_scope: "offline-engineering-preview",
};
const catalog = { schema_version: "aero-bench.traffic-preview-profile-catalog/v1", profiles: [profile] };
const jobId = "d".repeat(64);
const workspaceSha = "c".repeat(64);

function draft(): CityWorkspaceConfig {
  return { ...createDefaultCityWorkspaceConfig(), seed: 24427,
    traffic: { vehicles: 60, pedestrians: 36, bicycles: 12 } };
}

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
    error: state === "failed" ? { code: "sumo_failed", message: "SUMO 进程退出" } : null,
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

function selectProfile(root: HTMLElement): void {
  const select = root.querySelector("select");
  if (!(select instanceof HTMLSelectElement)) throw new Error("Missing traffic profile select");
  select.value = profile.profile_id;
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

function generateButton(root: HTMLElement): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll("button"))
    .find(item => item.textContent?.includes("交通预览"));
  if (!(button instanceof HTMLButtonElement)) throw new Error("Missing traffic preview button");
  return button;
}

function traceBytes(): { readonly bytes: Uint8Array<ArrayBuffer>; readonly digest: string } {
  const document = {
    schema_version: "aero-bench.city-sumo-preview/v2",
    artifact_class: "offline-engineering-preview",
    source_kind: "offline-sumo-engineering-preview",
    seed: 24427,
    duration_seconds: 120,
    demand_authoring: {
      schema_version: "aero-bench.city-traffic-preview-demand/v1",
      workspace_schema_version: "aero-bench.city-workspace/v3",
      workspace_sha256: workspaceSha,
      workspace_size_bytes: 4096,
      seed: 24427,
      traffic: { vehicles: 60, pedestrians: 36, bicycles: 12 },
    },
    demand: { authored: { sedan: 60, bicycle: 12 }, persons: 36, observed: {} },
    signals: [{ id: "signal.1" }],
    frames: [{ second: 0, vehicles: [], persons: [], tls: {} }],
  };
  const bytes = new TextEncoder().encode(JSON.stringify(document)) as Uint8Array<ArrayBuffer>;
  return { bytes, digest: createHash("sha256").update(bytes).digest("hex") };
}

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

describe("traffic preview panel", () => {
  it("shows every catalog profile but requires an explicit user selection", async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, catalog));
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const handle = renderCityTrafficPreviewPanel(root, draft, vi.fn());
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    expect(root.textContent).toContain("必须显式选择档案");
    expect(generateButton(root).disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    selectProfile(root);
    expect(generateButton(root).disabled).toBe(false);
    expect(root.textContent).toContain(profile.profile_sha256);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    handle.dispose();
  });

  it("keeps a scene-mismatched profile visible and blocks it with a specific reason", async () => {
    const other = { ...profile, scene_path: "/city-presentation/other.json" };
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, { ...catalog, profiles: [other] })));
    const root = document.createElement("div");
    renderCityTrafficPreviewPanel(root, draft, vi.fn());
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    const select = root.querySelector("select")!;
    select.value = other.profile_id;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    expect(root.textContent).toContain("场景不匹配");
    expect(root.textContent).toContain(other.scene_path);
    expect(generateButton(root).disabled).toBe(true);
  });

  it("preserves an explicit catalog failure instead of relabelling it as an empty catalog", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(503, {
      schema_version: "aero-bench.authoring-error/v1",
      error: { code: "traffic_preview_unconfigured", message: "未配置交通预览档案" },
    })));
    const root = document.createElement("div");
    renderCityTrafficPreviewPanel(root, draft, vi.fn());

    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='failed']")).not.toBeNull());
    expect(root.textContent).toContain("traffic_preview_unconfigured");
    expect(root.textContent).toContain("未配置交通预览档案");
    expect(root.textContent).not.toContain("档案目录为空");
    expect(generateButton(root).disabled).toBe(true);
  });

  it("renders queued/recording/auditing work and hands over only the complete verified trace", async () => {
    const artifact = traceBytes();
    const ready = job("ready", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${artifact.digest}`,
        sha256: artifact.digest, size_bytes: artifact.bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: "f".repeat(64), size_bytes: 512 }, error: null,
    });
    const polls = [job("recording"), job("auditing"), ready];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/traffic-preview-profiles")) return jsonResponse(200, catalog);
      if (url.endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, job("queued"));
      if (url === `/authoring/v1/traffic-previews/${jobId}`) return jsonResponse(200, polls.shift());
      if (url.includes("/assets/")) return new Response(artifact.bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(artifact.bytes.byteLength),
        ETag: `"${artifact.digest}"`,
      } });
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const onReady = vi.fn();
    const handle = renderCityTrafficPreviewPanel(root, draft, onReady, { pollIntervalMs: 0 });
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    selectProfile(root);
    generateButton(root).click();
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='ready']")).not.toBeNull());
    expect(root.textContent).toContain("非正式运行");
    expect(root.textContent).toContain("完整轨迹已交给调用方");
    expect(onReady).toHaveBeenCalledTimes(1);
    const delivered = onReady.mock.calls[0]![0];
    expect(delivered.trace.frames).toHaveLength(1);
    expect(delivered.trace.signals).toHaveLength(1);
    handle.dispose();
  });

  it("rechecks the draft after an asynchronous ready handoff before showing ready", async () => {
    const artifact = traceBytes();
    const ready = job("ready", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${artifact.digest}`,
        sha256: artifact.digest, size_bytes: artifact.bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: "f".repeat(64), size_bytes: 512 }, error: null,
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/traffic-preview-profiles")) return jsonResponse(200, catalog);
      if (url.endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, ready);
      if (url.includes("/assets/")) return new Response(artifact.bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(artifact.bytes.byteLength),
        ETag: `"${artifact.digest}"`,
      } });
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    let resolveHandoff!: () => void;
    const handoff = new Promise<void>(resolve => { resolveHandoff = resolve; });
    const current = draft();
    const root = document.createElement("div");
    const onReady = vi.fn(async () => handoff);
    renderCityTrafficPreviewPanel(root, () => current, onReady, { pollIntervalMs: 0 });
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    selectProfile(root);
    generateButton(root).click();
    await vi.waitFor(() => expect(onReady).toHaveBeenCalledTimes(1));

    current.traffic.vehicles += 1;
    resolveHandoff();

    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='stale']")).not.toBeNull());
    expect(root.querySelector("[data-state='ready']")).toBeNull();
    expect(root.textContent).toContain("草稿已变化");
  });

  it("marks an already mounted audited trace stale without claiming it was never displayed", async () => {
    const artifact = traceBytes();
    const ready = job("ready", {
      trace: { url: `/authoring/v1/traffic-previews/${jobId}/assets/${artifact.digest}`,
        sha256: artifact.digest, size_bytes: artifact.bytes.byteLength, media_type: "application/json" },
      canonical_audit: { status: "PASS", sha256: "f".repeat(64), size_bytes: 512 }, error: null,
    });
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/traffic-preview-profiles")) return jsonResponse(200, catalog);
      if (url.endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, ready);
      if (url.includes("/assets/")) return new Response(artifact.bytes, { status: 200, headers: {
        "Content-Type": "application/json", "Content-Length": String(artifact.bytes.byteLength),
        ETag: `"${artifact.digest}"`,
      } });
      throw new Error(`Unexpected fetch ${url}`);
    }));
    const current = draft();
    const root = document.createElement("div");
    const onReady = vi.fn();
    const handle = renderCityTrafficPreviewPanel(root, () => current, onReady, { pollIntervalMs: 0 });
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    selectProfile(root);
    generateButton(root).click();
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='ready']")).not.toBeNull());

    current.seed += 1;
    handle.refresh();

    expect(root.querySelector<HTMLElement>("[data-state='stale']")).not.toBeNull();
    expect(root.textContent).toContain("已装配轨迹已标记为过时");
    expect(root.textContent).not.toContain("未载入、未显示该轨迹");
    expect(onReady).toHaveBeenCalledTimes(1);
  });

  it("marks a changed draft stale and never polls or displays the accepted old job", async () => {
    let resolvePost!: (response: Response) => void;
    const post = new Promise<Response>(resolve => { resolvePost = resolve; });
    const current = draft();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/traffic-preview-profiles")) return jsonResponse(200, catalog);
      if (url.endsWith("/traffic-previews") && init?.method === "POST") return post;
      throw new Error(`Old traffic preview should not be polled: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const onReady = vi.fn();
    renderCityTrafficPreviewPanel(root, () => current, onReady, { pollIntervalMs: 0 });
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    selectProfile(root);
    generateButton(root).click();
    current.traffic.vehicles = 61;
    resolvePost(jsonResponse(202, job("queued")));
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='stale']")).not.toBeNull());
    expect(root.textContent).toContain("未载入、未显示该轨迹");
    expect(onReady).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("surfaces a failed job and never invents an artifact", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.endsWith("/traffic-preview-profiles")) return jsonResponse(200, catalog);
      if (url.endsWith("/traffic-previews") && init?.method === "POST") return jsonResponse(202, job("failed"));
      throw new Error(`Unexpected fetch ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const root = document.createElement("div");
    const onReady = vi.fn();
    renderCityTrafficPreviewPanel(root, draft, onReady, { pollIntervalMs: 0 });
    await vi.waitFor(() => expect(root.querySelector("select")?.options.length).toBe(2));
    selectProfile(root);
    generateButton(root).click();
    await vi.waitFor(() => expect(root.querySelector<HTMLElement>("[data-state='failed']")).not.toBeNull());
    expect(root.textContent).toContain("sumo_failed");
    expect(root.textContent).toContain("SUMO 进程退出");
    expect(onReady).not.toHaveBeenCalled();
  });

  it("aborts a pending catalog request and empties the panel on dispose", async () => {
    let aborted = false;
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        aborted = true;
        reject(new DOMException("aborted", "AbortError"));
      });
    })));
    const root = document.createElement("div");
    const handle = renderCityTrafficPreviewPanel(root, draft, vi.fn());
    handle.dispose();
    await vi.waitFor(() => expect(aborted).toBe(true));
    expect(root.children).toHaveLength(0);
  });
});
