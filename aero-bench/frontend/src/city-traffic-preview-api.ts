import type {
  TrafficPreviewJob,
  TrafficPreviewProfileCatalog,
  TrafficPreviewRequest,
} from "./generated/aero-bench-contracts";
import {
  assertAuthoringApiErrorResponse,
  assertTrafficPreviewJob,
  assertTrafficPreviewProfileCatalog,
  assertTrafficPreviewRequest,
} from "./generated/contract-validators";
import { parseStrictJson } from "./strict-json";
import { parseCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { readBoundedResponse } from "./verified-bytes";

const API = "/authoring/v1";
const SHA256 = /^[0-9a-f]{64}$/;
const MAX_CATALOG_BYTES = 512 * 1024;
const MAX_JOB_BYTES = 128 * 1024;
const MAX_TRACE_BYTES = 256 * 1024 * 1024;

export type TrafficPreviewProfile = TrafficPreviewProfileCatalog["profiles"][number];

export interface TrafficPreviewDraftSnapshot {
  /** Immutable browser-local comparison value. This is not the server workspace digest. */
  readonly localRevision: string;
  readonly draft: CityWorkspaceConfig;
}

export interface TrafficPreviewSubmission {
  readonly profile: TrafficPreviewProfile;
  readonly request: TrafficPreviewRequest;
  readonly draftSnapshot: TrafficPreviewDraftSnapshot;
  /** The POST response establishes the authoritative normalized workspace identity. */
  readonly acceptedJob: TrafficPreviewJob;
}

export interface TrafficPreviewDemandAuthoring {
  readonly schema_version: "aero-bench.city-traffic-preview-demand/v1";
  readonly workspace_schema_version: CityWorkspaceConfig["schema_version"];
  readonly workspace_sha256: string;
  readonly workspace_size_bytes: number;
  readonly seed: number;
  readonly traffic: {
    readonly vehicles: number;
    readonly pedestrians: number;
    readonly bicycles: number;
  };
}

export interface TrafficPreviewTraceDocument extends Readonly<Record<string, unknown>> {
  readonly schema_version: "aero-bench.city-sumo-preview/v2";
  readonly artifact_class: "offline-engineering-preview";
  readonly source_kind: "offline-sumo-engineering-preview";
  readonly seed: number;
  readonly duration_seconds: number;
  readonly demand_authoring: TrafficPreviewDemandAuthoring;
}

export interface VerifiedTrafficPreviewArtifact {
  readonly job: TrafficPreviewJob;
  readonly profile: TrafficPreviewProfile;
  readonly draftSnapshot: TrafficPreviewDraftSnapshot;
  readonly bytes: Uint8Array<ArrayBuffer>;
  /** The complete audited trace. This client does not remove actors or frames. */
  readonly trace: TrafficPreviewTraceDocument;
}

export type TrafficPreviewCatalogOutcome =
  | { readonly kind: "ok"; readonly catalog: TrafficPreviewProfileCatalog }
  | { readonly kind: "error"; readonly status: 503; readonly code: string; readonly detail: string };

export type SubmitTrafficPreviewOutcome =
  | { readonly kind: "accepted"; readonly submission: TrafficPreviewSubmission }
  | { readonly kind: "error"; readonly status: 400 | 409 | 415 | 503; readonly code: string; readonly detail: string };

export type FetchTrafficPreviewJobOutcome =
  | { readonly kind: "found"; readonly job: TrafficPreviewJob }
  | { readonly kind: "error"; readonly status: 404 | 503; readonly code: string; readonly detail: string };

interface JsonResponse {
  readonly status: number;
  readonly body: unknown;
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 格式无效`);
  }
  return value as Record<string, unknown>;
}

function exactObject(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  const item = object(value, label);
  if (Object.keys(item).length !== keys.length || keys.some(key => !Object.hasOwn(item, key))) {
    throw new Error(`${label} 字段不符合协议`);
  }
  return item;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} 必须是非负安全整数`);
  }
  return value;
}

function positiveInteger(value: unknown, label: string): number {
  const result = nonNegativeInteger(value, label);
  if (result === 0) throw new Error(`${label} 必须大于零`);
  return result;
}

function stableJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("交通预览草稿包含非有限数值");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    return `{${Object.keys(item).sort().map(key => `${JSON.stringify(key)}:${stableJson(item[key])}`).join(",")}}`;
  }
  throw new Error("交通预览草稿包含非 JSON 值");
}

function deepFreezeJson<T>(value: T): T {
  if (value === null || typeof value !== "object" || Object.isFrozen(value)) return value;
  const children: readonly unknown[] = Array.isArray(value)
    ? value : Object.values(value as Record<string, unknown>);
  for (const child of children) deepFreezeJson(child);
  return Object.freeze(value);
}

/** Clone and validate the active strict v3 draft. No legacy migration occurs on this path. */
export function snapshotTrafficPreviewDraft(value: CityWorkspaceConfig): TrafficPreviewDraftSnapshot {
  const valid = parseCityWorkspaceConfig(value);
  const cloned = deepFreezeJson(parseCityWorkspaceConfig(parseStrictJson(JSON.stringify(valid))));
  return { localRevision: stableJson(cloned), draft: cloned };
}

export function trafficPreviewDraftMatches(
  snapshot: TrafficPreviewDraftSnapshot,
  current: CityWorkspaceConfig,
): boolean {
  try {
    return stableJson(parseCityWorkspaceConfig(current)) === snapshot.localRevision;
  } catch {
    return false;
  }
}

function parseJsonBytes(bytes: ArrayBuffer, label: string): unknown {
  let text: string;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    throw new Error(`${label} 不是有效 UTF-8：${error instanceof Error ? error.message : String(error)}`);
  }
  try {
    return parseStrictJson(text);
  } catch (error) {
    throw new Error(`${label} JSON 无效：${error instanceof Error ? error.message : String(error)}`);
  }
}

async function requestJson(path: string, init: RequestInit, maxBytes: number,
                           signal?: AbortSignal): Promise<JsonResponse> {
  const response = await fetch(path, { ...init, signal, redirect: "error" });
  if (response.headers.get("Content-Type")?.split(";", 1)[0]?.trim() !== "application/json") {
    void response.body?.cancel().catch(() => undefined);
    throw new Error("交通预览 API 未返回 application/json");
  }
  const bytes = await readBoundedResponse(response, maxBytes, "交通预览 API", signal);
  return { status: response.status, body: parseJsonBytes(bytes, "交通预览 API") };
}

function apiError(value: unknown): { readonly code: string; readonly detail: string } {
  assertAuthoringApiErrorResponse(value);
  return { code: value.error.code, detail: value.error.message };
}

export function parseTrafficPreviewCatalog(value: unknown): TrafficPreviewProfileCatalog {
  assertTrafficPreviewProfileCatalog(value);
  const ids = value.profiles.map(profile => profile.profile_id);
  if (new Set(ids).size !== ids.length) throw new Error("交通预览档案 ID 重复");
  return value;
}

function assertJobState(job: TrafficPreviewJob): void {
  const ready = job.state === "ready";
  const failed = job.state === "failed";
  if (ready) {
    if (job.trace === null || job.trace === undefined
      || job.canonical_audit === null || job.canonical_audit === undefined || job.error !== null) {
      throw new Error("ready 交通预览缺少轨迹、PASS 审计或包含错误");
    }
    const expected = `${API}/traffic-previews/${job.job_id}/assets/${job.trace.sha256}`;
    if (job.trace.url !== expected) throw new Error("交通预览轨迹 URL 未绑定任务和内容哈希");
  } else if (failed) {
    if (job.error === null || job.error === undefined || job.trace !== null || job.canonical_audit !== null) {
      throw new Error("failed 交通预览的错误或产物字段不一致");
    }
  } else if (job.trace !== null || job.canonical_audit !== null || job.error !== null) {
    throw new Error("未完成的交通预览提前发布了产物或错误");
  }
  if (job.preview_scope !== "offline-engineering-preview"
    || job.formal_provider_bound !== false || job.executed !== false || job.verified !== false) {
    throw new Error("交通预览被错误标记为正式执行或验证结果");
  }
}

export function parseTrafficPreviewJob(value: unknown): TrafficPreviewJob {
  assertTrafficPreviewJob(value);
  assertJobState(value);
  return value;
}

function assertJobMatchesSubmission(job: TrafficPreviewJob, submission: TrafficPreviewSubmission): void {
  const accepted = submission.acceptedJob;
  if (job.job_id !== accepted.job_id
    || job.profile_id !== accepted.profile_id
    || job.profile_sha256 !== accepted.profile_sha256
    || job.workspace_sha256 !== accepted.workspace_sha256
    || job.workspace_size_bytes !== accepted.workspace_size_bytes
    || job.duration_seconds !== accepted.duration_seconds) {
    throw new Error("交通预览任务身份在提交后发生变化");
  }
}

/** Load the explicit offline-preview profile catalog. No profile is selected here. */
export async function fetchTrafficPreviewProfiles(signal?: AbortSignal): Promise<TrafficPreviewCatalogOutcome> {
  const response = await requestJson(`${API}/traffic-preview-profiles`, { method: "GET" }, MAX_CATALOG_BYTES, signal);
  if (response.status === 200) return { kind: "ok", catalog: parseTrafficPreviewCatalog(response.body) };
  if (response.status === 503) {
    const issue = apiError(response.body);
    if (issue.code !== "traffic_preview_unconfigured") {
      throw new Error(`交通预览目录 HTTP 503 返回未声明的错误：${issue.code}`);
    }
    return { kind: "error", status: 503, ...issue };
  }
  throw new Error(`交通预览目录返回未声明的 HTTP 状态码：${response.status}`);
}

/**
 * Submit one explicit profile and one immutable v3 draft. The response's normalized
 * workspace SHA/size becomes authoritative; the browser does not reproduce Python floats.
 */
export async function submitTrafficPreview(
  profile: TrafficPreviewProfile,
  durationSeconds: number,
  draft: CityWorkspaceConfig,
  signal?: AbortSignal,
): Promise<SubmitTrafficPreviewOutcome> {
  const checkedProfile = parseTrafficPreviewCatalog({
    schema_version: "aero-bench.traffic-preview-profile-catalog/v1",
    profiles: [profile],
  }).profiles[0]!;
  const draftSnapshot = snapshotTrafficPreviewDraft(draft);
  if (checkedProfile.scene_path !== draftSnapshot.draft.scenePath) {
    throw new Error("所选交通预览档案与当前草稿场景路径不一致");
  }
  const request: unknown = {
    schema_version: "aero-bench.traffic-preview-request/v1",
    profile_id: checkedProfile.profile_id,
    profile_sha256: checkedProfile.profile_sha256,
    duration_seconds: durationSeconds,
    draft: draftSnapshot.draft,
  };
  assertTrafficPreviewRequest(request);
  const response = await requestJson(`${API}/traffic-previews`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  }, MAX_JOB_BYTES, signal);
  if (response.status === 202) {
    const job = parseTrafficPreviewJob(response.body);
    if (job.profile_id !== checkedProfile.profile_id
      || job.profile_sha256 !== checkedProfile.profile_sha256
      || job.duration_seconds !== request.duration_seconds) {
      throw new Error("交通预览任务与所选档案或时长不一致");
    }
    return { kind: "accepted", submission: {
      profile: checkedProfile, request, draftSnapshot, acceptedJob: job,
    } };
  }
  if (response.status === 400 || response.status === 409 || response.status === 415 || response.status === 503) {
    const issue = apiError(response.body);
    if (response.status === 409 && issue.code !== "traffic_preview_profile_conflict") {
      throw new Error(`交通预览提交 HTTP 409 返回未声明的错误：${issue.code}`);
    }
    return { kind: "error", status: response.status, ...issue };
  }
  throw new Error(`交通预览提交返回未声明的 HTTP 状态码：${response.status}`);
}

/** Poll one immutable job and reject any profile, workspace, duration, or ID drift. */
export async function fetchTrafficPreviewJob(
  submission: TrafficPreviewSubmission,
  signal?: AbortSignal,
): Promise<FetchTrafficPreviewJobOutcome> {
  const jobId = submission.acceptedJob.job_id;
  if (!SHA256.test(jobId)) throw new Error("交通预览任务 ID 无效");
  const response = await requestJson(`${API}/traffic-previews/${jobId}`, { method: "GET" }, MAX_JOB_BYTES, signal);
  if (response.status === 200) {
    const job = parseTrafficPreviewJob(response.body);
    assertJobMatchesSubmission(job, submission);
    return { kind: "found", job };
  }
  if (response.status === 404 || response.status === 503) {
    return { kind: "error", status: response.status, ...apiError(response.body) };
  }
  throw new Error(`交通预览查询返回未声明的 HTTP 状态码：${response.status}`);
}

function parseDemandAuthoring(value: unknown, submission: TrafficPreviewSubmission,
                              job: TrafficPreviewJob): TrafficPreviewDemandAuthoring {
  const source = exactObject(value, ["schema_version", "workspace_schema_version", "workspace_sha256",
    "workspace_size_bytes", "seed", "traffic"], "交通预览 demand_authoring");
  const traffic = exactObject(source.traffic, ["vehicles", "pedestrians", "bicycles"],
    "交通预览 demand_authoring.traffic");
  const parsed: TrafficPreviewDemandAuthoring = {
    schema_version: source.schema_version as TrafficPreviewDemandAuthoring["schema_version"],
    workspace_schema_version: source.workspace_schema_version as TrafficPreviewDemandAuthoring["workspace_schema_version"],
    workspace_sha256: String(source.workspace_sha256),
    workspace_size_bytes: positiveInteger(source.workspace_size_bytes, "交通预览工作区字节数"),
    seed: nonNegativeInteger(source.seed, "交通预览种子"),
    traffic: {
      vehicles: nonNegativeInteger(traffic.vehicles, "交通预览机动车数量"),
      pedestrians: nonNegativeInteger(traffic.pedestrians, "交通预览行人数量"),
      bicycles: nonNegativeInteger(traffic.bicycles, "交通预览自行车数量"),
    },
  };
  const draft = parseCityWorkspaceConfig(submission.request.draft);
  if (parsed.schema_version !== "aero-bench.city-traffic-preview-demand/v1"
    || parsed.workspace_schema_version !== draft.schema_version
    || !trafficPreviewDraftMatches(submission.draftSnapshot, draft)
    || !SHA256.test(parsed.workspace_sha256)
    || parsed.workspace_sha256 !== job.workspace_sha256
    || parsed.workspace_size_bytes !== job.workspace_size_bytes
    || parsed.seed !== draft.seed
    || parsed.traffic.vehicles !== draft.traffic.vehicles
    || parsed.traffic.pedestrians !== draft.traffic.pedestrians
    || parsed.traffic.bicycles !== draft.traffic.bicycles) {
    throw new Error("交通预览轨迹的工作区需求身份与已接受任务不一致");
  }
  return parsed;
}

export function parseTrafficPreviewTrace(
  value: unknown,
  submission: TrafficPreviewSubmission,
  job: TrafficPreviewJob,
): TrafficPreviewTraceDocument {
  assertJobMatchesSubmission(job, submission);
  const trace = object(value, "交通预览轨迹");
  if (trace.schema_version !== "aero-bench.city-sumo-preview/v2"
    || trace.artifact_class !== "offline-engineering-preview"
    || trace.source_kind !== "offline-sumo-engineering-preview") {
    throw new Error("交通预览轨迹版本、产物类别或来源类型无效");
  }
  const authoring = parseDemandAuthoring(trace.demand_authoring, submission, job);
  if (nonNegativeInteger(trace.seed, "交通预览轨迹种子") !== authoring.seed
    || positiveInteger(trace.duration_seconds, "交通预览轨迹时长") !== job.duration_seconds) {
    throw new Error("交通预览轨迹的种子或时长与任务不一致");
  }
  const demand = object(trace.demand, "交通预览轨迹 demand");
  if (nonNegativeInteger(demand.persons, "交通预览轨迹行人需求") !== authoring.traffic.pedestrians) {
    throw new Error("交通预览轨迹的行人需求与工作区不一致");
  }
  const authored = object(demand.authored, "交通预览轨迹 demand.authored");
  let motorVehicles = 0;
  let bicycles = 0;
  for (const [kind, value] of Object.entries(authored)) {
    const count = nonNegativeInteger(value, `交通预览轨迹 ${kind} 需求`);
    if (kind === "bicycle") bicycles = count;
    else motorVehicles += count;
  }
  if (bicycles !== authoring.traffic.bicycles || motorVehicles !== authoring.traffic.vehicles) {
    throw new Error("交通预览轨迹的机动车或自行车需求与工作区不一致");
  }
  return { ...trace, demand_authoring: authoring } as TrafficPreviewTraceDocument;
}

async function sha256(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

/** Fetch and verify the complete ready trace. A renderer may consume it later, but this path never filters it. */
export async function loadReadyTrafficPreview(
  submission: TrafficPreviewSubmission,
  job: TrafficPreviewJob,
  signal?: AbortSignal,
): Promise<VerifiedTrafficPreviewArtifact> {
  assertJobState(job);
  assertJobMatchesSubmission(job, submission);
  if (job.state !== "ready" || job.trace === null || job.canonical_audit === null) {
    throw new Error("交通预览任务尚未发布完整轨迹和 PASS 审计");
  }
  if (job.trace.size_bytes > MAX_TRACE_BYTES) throw new Error("交通预览轨迹超过浏览器字节上限");
  const target = new URL(job.trace.url, window.location.href);
  if (target.origin !== window.location.origin || target.pathname !== job.trace.url
    || target.search !== "" || target.hash !== "") {
    throw new Error("交通预览轨迹必须来自同源内容地址");
  }
  const response = await fetch(job.trace.url, { method: "GET", signal, redirect: "error" });
  if (response.status !== 200) {
    if (![404, 409, 503].includes(response.status)) {
      void response.body?.cancel().catch(() => undefined);
      throw new Error(`交通预览轨迹返回未声明的 HTTP 状态码：${response.status}`);
    }
    if (response.headers.get("Content-Type")?.split(";", 1)[0]?.trim() !== "application/json") {
      void response.body?.cancel().catch(() => undefined);
      throw new Error("交通预览轨迹错误响应不是 application/json");
    }
    const errorBytes = await readBoundedResponse(response, MAX_JOB_BYTES, "交通预览轨迹错误", signal);
    const issue = apiError(parseJsonBytes(errorBytes, "交通预览轨迹错误"));
    throw new Error(`交通预览轨迹 HTTP ${response.status}：[${issue.code}] ${issue.detail}`);
  }
  if (response.headers.get("Content-Type")?.split(";", 1)[0]?.trim() !== job.trace.media_type) {
    void response.body?.cancel().catch(() => undefined);
    throw new Error("交通预览轨迹媒体类型与任务声明不一致");
  }
  if (response.headers.get("ETag") !== `"${job.trace.sha256}"`) {
    void response.body?.cancel().catch(() => undefined);
    throw new Error("交通预览轨迹 ETag 与任务声明不一致");
  }
  const raw = await readBoundedResponse(response, job.trace.size_bytes, "交通预览轨迹", signal,
    job.trace.size_bytes);
  if (await sha256(raw) !== job.trace.sha256) throw new Error("交通预览轨迹 SHA-256 与任务声明不一致");
  const trace = parseTrafficPreviewTrace(parseJsonBytes(raw, "交通预览轨迹"), submission, job);
  return { job, profile: submission.profile, draftSnapshot: submission.draftSnapshot,
    bytes: new Uint8Array(raw), trace };
}
