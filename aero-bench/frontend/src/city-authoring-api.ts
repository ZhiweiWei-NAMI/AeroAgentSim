import { AssetResolver } from "./asset-resolver";
import { loadMeshPack, type LoadedMeshPack } from "./osm2world/pack-loader";
import { readBoundedResponse } from "./verified-bytes";
import type { SceneOrigin, SceneSelection } from "./city-region-selector";
import type { PackFile } from "./osm2world/pack";
import { validateStaticCityRoadPayload } from "./city-roads";
import { parseCityWorkspaceConfig, type CityWorkspaceConfig } from "./city-workspace-config";
import { assertCityCompilationResult, assertSceneRegistrationCatalog } from "./generated/contract-validators";
import type { SceneRegistrationCatalog } from "./generated/aero-bench-contracts";

const API = "/authoring/v1";
const SHA = /^[0-9a-f]{64}$/;
const SOURCE_ID = /^[a-z][a-z0-9_.-]*$/;
const MAX_CATALOG_BYTES = 128 * 1024;
const MAX_JOB_BYTES = 64 * 1024;
const MAX_NATIVE_CATALOG_BYTES = 512 * 1024;
const MAX_COMPILATION_BYTES = 512 * 1024;

export interface AuthoringSource {
  readonly source_id: string;
  readonly sha256: string;
  readonly size_bytes: number;
  readonly display_name: string;
  readonly origin: SceneOrigin;
  readonly bounds_wgs84: {
    readonly min_latitude_deg: number; readonly max_latitude_deg: number;
    readonly min_longitude_deg: number; readonly max_longitude_deg: number;
  };
  readonly data_url: string;
}
export interface AuthoringCatalog {
  readonly schema_version: "aero-bench.scene-source-catalog/v1";
  readonly sources: readonly AuthoringSource[];
}
export type AuthoringJobState = "queued" | "compiling" | "meshing" | "networking" | "surfaces" | "placing" | "auditing" | "ready" | "failed";
export interface StaticPresentationManifest {
  readonly schema_version: "aero-bench.city-static-presentation/v1";
  readonly job_id: string;
  readonly selection_sha256: string;
  readonly source_id: string;
  readonly raw_source_sha256: string;
  readonly effective_osm_sha256: string;
  readonly sumo_source_osm_sha256: string;
  readonly compiler_manifest_sha256: string;
  readonly origin: SceneOrigin;
  readonly pack_manifest: PackFile;
  readonly network: PackFile & { readonly projection: string; readonly sumo_image_id: string };
  readonly road: PackFile;
  readonly building_placement: PackFile;
  readonly signal_inventory: PackFile;
  readonly visual_assets: PackFile;
  readonly osm2world_style_tree_sha256: string;
  readonly bigcity_library_tree_sha256: string;
}
export interface StaticSignal {
  readonly id: string; readonly tls: string; readonly link: number;
  readonly x: number; readonly z: number; readonly heading: number;
}
export interface StaticSignalInventory {
  readonly schema_version: "aero-bench.city-static-signal-inventory/v1";
  readonly source_network_sha256: string;
  readonly mesh_pack_source_sha256: string;
  readonly signals: readonly StaticSignal[];
}
export interface VerifiedStaticPresentation {
  readonly pack: LoadedMeshPack;
  readonly manifest: StaticPresentationManifest;
  readonly buildingPlacement: unknown;
  readonly road: unknown;
  readonly signals: StaticSignalInventory;
  readonly visualAssets: VerifiedVisualAssets;
}
export interface AuthoringJob {
  readonly schema_version: "aero-bench.scene-build-job/v1";
  readonly job_id: string;
  readonly selection_sha256: string;
  readonly source_sha256: string;
  readonly state: AuthoringJobState;
  readonly compiler_manifest_sha256: string | null;
  readonly pack: { readonly base_url: string; readonly manifest: PackFile; readonly source_sha256: string } | null;
  readonly presentation: { readonly base_url: string; readonly manifest: PackFile } | null;
  readonly error: { readonly code: string; readonly message: string } | null;
}

function object(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 格式无效`);
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) throw new Error(`${label} 字段不符合协议`);
  return result;
}
function string(value: unknown, label: string): string {
  if (typeof value !== "string" || !value) throw new Error(`${label} 无效`);
  return value;
}
function sha(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA.test(value)) throw new Error(`${label} 不是 SHA-256`);
  return value;
}
function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new Error(`${label} 必须是有限数值`);
  return value;
}
/** A FileRef is a bundle-relative path plus its content digest; unlike PackFile it has no size_bytes. */
function file(value: unknown): PackFile {
  const item = object(value, ["sha256", "size_bytes"], "网格 manifest 引用");
  const size = number(item.size_bytes, "网格 manifest 大小");
  if (!Number.isSafeInteger(size) || size < 1 || size > MAX_CATALOG_BYTES * 1024) throw new Error("网格 manifest 大小无效");
  return { sha256: sha(item.sha256, "网格 manifest 哈希"), size_bytes: size };
}
function origin(value: unknown): SceneOrigin {
  const item = object(value, ["latitude_deg", "longitude_deg", "ellipsoid_height_m", "geoid_undulation_m", "amsl_m"], "来源原点");
  const result: SceneOrigin = {
    latitude_deg: number(item.latitude_deg, "原点纬度"), longitude_deg: number(item.longitude_deg, "原点经度"),
    ellipsoid_height_m: number(item.ellipsoid_height_m, "原点椭球高"),
    geoid_undulation_m: number(item.geoid_undulation_m, "原点大地水准面起伏"), amsl_m: number(item.amsl_m, "原点海拔"),
  };
  if (result.latitude_deg <= -90 || result.latitude_deg >= 90 || result.longitude_deg < -180 || result.longitude_deg >= 180
    || Math.abs(result.ellipsoid_height_m - result.geoid_undulation_m - result.amsl_m) > 1e-9) throw new Error("来源原点坐标或高程关系无效");
  return result;
}
export function parseAuthoringCatalog(value: unknown): AuthoringCatalog {
  const doc = object(value, ["schema_version", "sources"], "OSM 来源目录");
  if (doc.schema_version !== "aero-bench.scene-source-catalog/v1" || !Array.isArray(doc.sources) || !doc.sources.length) throw new Error("OSM 来源目录版本或列表无效");
  const sources = doc.sources.map(raw => {
    const item = object(raw, ["source_id", "sha256", "size_bytes", "display_name", "origin", "bounds_wgs84", "data_url"], "OSM 来源");
    const sourceId = string(item.source_id, "OSM 来源 ID");
    if (!SOURCE_ID.test(sourceId)) throw new Error("OSM 来源 ID 无效");
    const size = number(item.size_bytes, "OSM 来源大小");
    if (!Number.isSafeInteger(size) || size < 1 || size > 512 * 1024 * 1024) throw new Error("OSM 来源大小无效");
    const bounds = object(item.bounds_wgs84, ["min_latitude_deg", "max_latitude_deg", "min_longitude_deg", "max_longitude_deg"], "OSM 经纬度边界");
    const b = { min_latitude_deg: number(bounds.min_latitude_deg, "最小纬度"), max_latitude_deg: number(bounds.max_latitude_deg, "最大纬度"),
      min_longitude_deg: number(bounds.min_longitude_deg, "最小经度"), max_longitude_deg: number(bounds.max_longitude_deg, "最大经度") };
    if (!(b.min_latitude_deg < b.max_latitude_deg && b.min_longitude_deg < b.max_longitude_deg)) throw new Error("OSM 经纬度边界无效");
    const dataUrl = string(item.data_url, "OSM 数据 URL");
    if (dataUrl !== `${API}/sources/${sourceId}`) throw new Error("OSM 数据 URL 与注册来源不一致");
    return { source_id: sourceId, sha256: sha(item.sha256, "OSM 来源哈希"), size_bytes: size,
      display_name: string(item.display_name, "OSM 来源名称"), origin: origin(item.origin), bounds_wgs84: b, data_url: dataUrl };
  });
  if (new Set(sources.map(item => item.source_id)).size !== sources.length) throw new Error("OSM 来源 ID 重复");
  return { schema_version: "aero-bench.scene-source-catalog/v1", sources };
}
export function parseAuthoringJob(value: unknown): AuthoringJob {
  const doc = object(value, ["schema_version", "job_id", "selection_sha256", "source_sha256", "state", "compiler_manifest_sha256", "pack", "presentation", "error"], "构建任务");
  if (doc.schema_version !== "aero-bench.scene-build-job/v1") throw new Error("构建任务版本无效");
  const jobId = sha(doc.job_id, "任务 ID");
  const state = doc.state;
  if (state !== "queued" && state !== "compiling" && state !== "meshing" && state !== "networking"
    && state !== "surfaces" && state !== "placing" && state !== "auditing" && state !== "ready" && state !== "failed") throw new Error("构建任务状态无效");
  const compiler = doc.compiler_manifest_sha256 === null ? null : sha(doc.compiler_manifest_sha256, "编译 manifest 哈希");
  let pack: AuthoringJob["pack"] = null;
  if (doc.pack !== null) {
    const item = object(doc.pack, ["base_url", "manifest", "source_sha256"], "构建网格包");
    const base = string(item.base_url, "网格包路径");
    if (base !== `${API}/scenes/${jobId}/pack/`) throw new Error("网格包路径与任务 ID 不一致");
    pack = { base_url: base, manifest: file(item.manifest), source_sha256: sha(item.source_sha256, "effective OSM 哈希") };
  }
  let presentation: AuthoringJob["presentation"] = null;
  if (doc.presentation !== null) {
    const item = object(doc.presentation, ["base_url", "manifest"], "静态呈现引用");
    const base = string(item.base_url, "静态呈现路径");
    if (base !== `${API}/scenes/${jobId}/presentation/`) throw new Error("静态呈现路径与任务 ID 不一致");
    presentation = { base_url: base, manifest: file(item.manifest) };
  }
  let error: AuthoringJob["error"] = null;
  if (doc.error !== null) {
    const item = object(doc.error, ["code", "message"], "任务错误");
    const code = string(item.code, "任务错误代码");
    if (!SOURCE_ID.test(code)) throw new Error("任务错误代码无效");
    error = { code, message: string(item.message, "任务错误原因") };
  }
  if ((state === "ready") !== (pack !== null) || (state === "ready") !== (presentation !== null)
    || (state === "failed") !== (error !== null)
    || (state === "ready" && compiler === null)
    || (["meshing", "networking", "surfaces", "placing", "auditing"].includes(state) && compiler === null)
    || ((state === "queued" || state === "compiling") && compiler !== null)) throw new Error("构建任务状态字段不一致");
  return { schema_version: "aero-bench.scene-build-job/v1", job_id: jobId,
    selection_sha256: sha(doc.selection_sha256, "选区哈希"), source_sha256: sha(doc.source_sha256, "原始 OSM 哈希"),
    state, compiler_manifest_sha256: compiler, pack, presentation, error };
}
export interface AuthoringApiErrorDetail {
  readonly code: string;
  readonly message: string;
}
function parseAuthoringApiErrorDetail(value: unknown): AuthoringApiErrorDetail {
  const doc = object(value, ["schema_version", "error"], "作者 API 错误");
  if (doc.schema_version !== "aero-bench.authoring-error/v1") throw new Error("作者 API 错误版本无效");
  const issue = object(doc.error, ["code", "message"], "作者 API 错误内容");
  const code = string(issue.code, "错误代码");
  if (!SOURCE_ID.test(code)) throw new Error("错误代码无效");
  return { code, message: string(issue.message, "错误原因") };
}
function parseAuthoringHttpError(value: unknown): string {
  const detail = parseAuthoringApiErrorDetail(value);
  return `${detail.code}：${detail.message}`;
}
async function requestJson(path: string, init: RequestInit, maxBytes: number, expectedStatus: number, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { ...init, signal, redirect: "error" });
  if (!response.headers.get("Content-Type")?.startsWith("application/json")) throw new Error("作者 API 未返回 JSON");
  const bytes = await readBoundedResponse(response, maxBytes, "作者 API", signal);
  const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  if (response.status !== expectedStatus) throw new Error(`作者 API HTTP ${response.status}：${parseAuthoringHttpError(value)}`);
  return value;
}
/** For routes whose contract maps several distinct status codes to different result shapes. */
async function requestJsonMultiStatus(
  path: string, init: RequestInit, maxBytes: number, signal?: AbortSignal,
): Promise<{ readonly status: number; readonly body: unknown }> {
  const response = await fetch(path, { ...init, signal, redirect: "error" });
  if (!response.headers.get("Content-Type")?.startsWith("application/json")) throw new Error("作者 API 未返回 JSON");
  const bytes = await readBoundedResponse(response, maxBytes, "作者 API", signal);
  const body: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  return { status: response.status, body };
}
export async function fetchAuthoringCatalog(signal?: AbortSignal): Promise<AuthoringCatalog> {
  return parseAuthoringCatalog(await requestJson(`${API}/sources`, { method: "GET" }, MAX_CATALOG_BYTES, 200, signal));
}
export async function fetchAuthoringSource(source: AuthoringSource, signal?: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  const response = await fetch(source.data_url, { signal, redirect: "error" });
  if (!response.ok) throw new Error(`OSM 来源读取失败：HTTP ${response.status}：${parseAuthoringHttpError(await response.json())}`);
  const bytes = await readBoundedResponse(response, source.size_bytes, "OSM 原始来源", signal, source.size_bytes);
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), byte => byte.toString(16).padStart(2, "0")).join("");
  if (digest !== source.sha256) throw new Error("OSM 原始来源 SHA-256 与 catalog 不一致");
  const etag = response.headers.get("ETag");
  if (etag !== `"${source.sha256}"`) throw new Error("OSM 原始来源 ETag 与 catalog 不一致");
  return new Uint8Array(bytes);
}
export async function submitSceneSelection(selection: SceneSelection, signal?: AbortSignal): Promise<AuthoringJob> {
  const job = parseAuthoringJob(await requestJson(`${API}/scenes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) }, MAX_JOB_BYTES, 202, signal));
  if (job.source_sha256 !== selection.source_sha256) throw new Error("构建任务的原始 OSM 哈希与选区不一致");
  return job;
}
export async function fetchAuthoringJob(jobId: string, signal?: AbortSignal): Promise<AuthoringJob> {
  if (!SHA.test(jobId)) throw new Error("任务 ID 无效");
  const job = parseAuthoringJob(await requestJson(`${API}/scenes/${jobId}`, { method: "GET" }, MAX_JOB_BYTES, 200, signal));
  if (job.job_id !== jobId) throw new Error("构建任务 ID 与请求不一致");
  return job;
}
export async function loadReadyAuthoringPack(job: AuthoringJob, selection: SceneSelection, signal?: AbortSignal): Promise<LoadedMeshPack> {
  if (job.state !== "ready" || job.pack === null || job.presentation === null) throw new Error("构建任务尚未发布完整城市呈现");
  if (job.source_sha256 !== selection.source_sha256) throw new Error("构建任务的原始 OSM 哈希与选区不一致");
  const pack = job.pack;
  const baseHref = new URL(pack.base_url, window.location.href);
  if (baseHref.origin !== window.location.origin) throw new Error("网格包必须来自同源作者 API");
  // AssetResolver verifies its content-addressed path. The authoring API publishes
  // the manifest at manifest.json, so map only that one digest-addressed request.
  const fetchPack: typeof fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), baseHref);
    if (url.pathname === `${pack.base_url}assets/${pack.manifest.sha256}`) return fetch(new URL("manifest.json", baseHref), init);
    return fetch(input, init);
  };
  const resolver = new AssetResolver({ baseHref: baseHref.href, fetch: fetchPack });
  try {
    const loaded = await loadMeshPack(resolver, pack.manifest, signal, manifest => {
      if (manifest.source.sha256 !== pack.source_sha256) throw new Error("网格包来源与编译后 effective OSM 哈希不一致");
      if (manifest.projection.origin.latitude_deg !== selection.origin.latitude_deg
        || manifest.projection.origin.longitude_deg !== selection.origin.longitude_deg) {
        throw new Error("网格包投影原点与当前选区不一致");
      }
    });
    return { ...loaded, dispose: () => { loaded.dispose(); resolver.dispose(); } };
  } catch (error) { resolver.dispose(); throw error; }
}

function sameOriginBase(path: string): URL {
  const url = new URL(path, window.location.href);
  if (url.origin !== window.location.origin || url.pathname !== path || !path.endsWith("/")) {
    throw new Error("静态呈现必须来自同源作者 API");
  }
  return url;
}

function sameSceneOrigin(left: SceneOrigin, right: SceneOrigin): boolean {
  return left.latitude_deg === right.latitude_deg && left.longitude_deg === right.longitude_deg
    && left.ellipsoid_height_m === right.ellipsoid_height_m
    && left.geoid_undulation_m === right.geoid_undulation_m && left.amsl_m === right.amsl_m;
}

function sameOriginDocumentResolver(basePath: string, manifest: PackFile): AssetResolver {
  const base = sameOriginBase(basePath);
  const fetchDocument: typeof fetch = (input, init) => {
    const url = new URL(input instanceof Request ? input.url : String(input), base);
    if (url.pathname === `${basePath}assets/${manifest.sha256}`) return fetch(new URL("manifest.json", base), init);
    return fetch(input, init);
  };
  return new AssetResolver({ baseHref: base.href, fetch: fetchDocument });
}

async function verifiedJson(resolver: AssetResolver, ref: PackFile, label: string, signal?: AbortSignal): Promise<unknown> {
  const bytes = await resolver.fetchVerifiedBytes(`assets/${ref.sha256}`, {
    sha256: ref.sha256, sizeBytes: ref.size_bytes, mediaType: "application/json",
  }, signal);
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
  } catch (error) {
    throw new Error(`${label} JSON 无效：${error instanceof Error ? error.message : String(error)}`);
  }
}

export function parseStaticPresentationManifest(value: unknown): StaticPresentationManifest {
  const item = object(value, ["schema_version", "job_id", "selection_sha256", "source_id", "raw_source_sha256",
    "effective_osm_sha256", "sumo_source_osm_sha256", "compiler_manifest_sha256", "origin", "pack_manifest",
    "network", "road", "building_placement", "signal_inventory", "visual_assets", "osm2world_style_tree_sha256",
    "bigcity_library_tree_sha256"], "静态城市呈现 manifest");
  if (item.schema_version !== "aero-bench.city-static-presentation/v1") throw new Error("静态城市呈现版本无效");
  const network = object(item.network, ["sha256", "size_bytes", "projection", "sumo_image_id"], "SUMO 网络引用");
  const image = string(network.sumo_image_id, "SUMO 镜像身份");
  if (!/^sha256:[0-9a-f]{64}$/.test(image)) throw new Error("SUMO 镜像未固定 digest");
  return {
    schema_version: "aero-bench.city-static-presentation/v1",
    job_id: sha(item.job_id, "城市任务 ID"), selection_sha256: sha(item.selection_sha256, "选区哈希"),
    source_id: string(item.source_id, "OSM 来源 ID"), raw_source_sha256: sha(item.raw_source_sha256, "注册 OSM 哈希"),
    effective_osm_sha256: sha(item.effective_osm_sha256, "effective OSM 哈希"),
    sumo_source_osm_sha256: sha(item.sumo_source_osm_sha256, "SUMO OSM 哈希"),
    compiler_manifest_sha256: sha(item.compiler_manifest_sha256, "编译 manifest 哈希"),
    origin: origin(item.origin), pack_manifest: file(item.pack_manifest),
    network: { ...file({ sha256: network.sha256, size_bytes: network.size_bytes }),
      projection: string(network.projection, "SUMO 投影"), sumo_image_id: image },
    road: file(item.road), building_placement: file(item.building_placement), signal_inventory: file(item.signal_inventory),
    visual_assets: file(item.visual_assets),
    osm2world_style_tree_sha256: sha(item.osm2world_style_tree_sha256, "OSM2World 样式树哈希"),
    bigcity_library_tree_sha256: sha(item.bigcity_library_tree_sha256, "BigCity 模型树哈希"),
  };
}

export function parseStaticSignalInventory(value: unknown): StaticSignalInventory {
  const item = object(value, ["schema_version", "source_network_sha256", "mesh_pack_source_sha256", "signals"], "静态信号目录");
  if (item.schema_version !== "aero-bench.city-static-signal-inventory/v1" || !Array.isArray(item.signals)) {
    throw new Error("静态信号目录版本或列表无效");
  }
  const signals = item.signals.map(raw => {
    const entry = object(raw, ["id", "tls", "link", "x", "z", "heading"], "真实网络信号");
    const link = number(entry.link, "信号 link");
    if (!Number.isSafeInteger(link) || link < 0) throw new Error("信号 link 无效");
    return { id: string(entry.id, "信号 ID"), tls: string(entry.tls, "信号 TLS"), link,
      x: number(entry.x, "信号 X"), z: number(entry.z, "信号 Z"), heading: number(entry.heading, "信号朝向") };
  });
  if (new Set(signals.map(entry => entry.id)).size !== signals.length) throw new Error("真实网络信号 ID 重复");
  return { schema_version: "aero-bench.city-static-signal-inventory/v1",
    source_network_sha256: sha(item.source_network_sha256, "信号网络哈希"),
    mesh_pack_source_sha256: sha(item.mesh_pack_source_sha256, "信号网格来源哈希"), signals };
}

interface VisualAssetEntry extends PackFile {
  readonly path: string;
  readonly media_type: string;
}

export function parseVisualAssetInventory(value: unknown): readonly VisualAssetEntry[] {
  const doc = object(value, ["schema_version", "assets"], "城市视觉素材目录");
  if (doc.schema_version !== "aero-bench.city-visual-assets/v1" || !Array.isArray(doc.assets)
      || doc.assets.length === 0) throw new Error("城市视觉素材目录版本或列表无效");
  const allowed = new Map([
    [".fbx", "application/octet-stream"], [".glb", "model/gltf-binary"],
    [".webp", "image/webp"], [".png", "image/png"], [".json", "application/json"],
  ]);
  const assets = doc.assets.map(raw => {
    const entry = object(raw, ["path", "sha256", "size_bytes", "media_type"], "城市视觉素材");
    const path = string(entry.path, "素材路径");
    const extension = path.match(/\.[a-z0-9]+$/)?.[0];
    const mediaType = string(entry.media_type, "素材类型");
    if ((!path.startsWith("/models/bigcity/")
      && path !== "/models/incoming/urban-traffic/images/64d1d14365d8479458d03808ea6b95d5.webp"
      && path !== "/models/incoming/urban-traffic/images/f7a11eed4c9d2e047af8297ed9751e31.webp"
      && path !== "/models/incoming/furniture/glb/street_light_8.glb"
      && path !== "/models/incoming/furniture/glb/traffic_light_4.glb"
      && path !== "/models/incoming/furniture/glb/bus_stop_4.glb")
      || path.includes("..") || path.includes("//") || /[^/a-zA-Z0-9_.-]/.test(path)
      || extension === undefined || allowed.get(extension) !== mediaType) {
      throw new Error(`城市视觉素材路径或类型无效：${path}`);
    }
    return { path, ...file({ sha256: entry.sha256, size_bytes: entry.size_bytes }), media_type: mediaType };
  });
  if (assets.some((entry, index) => index > 0 && assets[index - 1]!.path >= entry.path)) {
    throw new Error("城市视觉素材目录路径未排序或重复");
  }
  return assets;
}

/** Lazily verifies each actual visual byte before a Three.js loader sees its blob URL. */
export class VerifiedVisualAssets {
  private readonly entries: ReadonlyMap<string, VisualAssetEntry>;
  private readonly pending = new Map<string, Promise<string>>();
  private readonly jsonPending = new Map<string, Promise<unknown>>();
  private readonly glbUrls = new Set<string>();
  private readonly verifiedDigests = new Set<string>();
  private verifiedBytes = 0;
  private active = 0;
  private readonly waiting: Array<() => void> = [];
  constructor(entries: readonly VisualAssetEntry[], private readonly resolver: AssetResolver,
              private readonly signal?: AbortSignal) {
    this.entries = new Map(entries.map(entry => [entry.path, entry]));
  }
  private entry(path: string): VisualAssetEntry {
    const entry = this.entries.get(path);
    if (entry === undefined) throw new Error(`静态城市视觉素材未在已验证目录声明：${path}`);
    return entry;
  }
  private async limited<T>(work: () => Promise<T>): Promise<T> {
    if (this.active >= 5) await new Promise<void>(resolve => this.waiting.push(resolve));
    this.active++;
    try { return await work(); }
    finally { this.active--; this.waiting.shift()?.(); }
  }
  private count(entry: VisualAssetEntry): void {
    if (this.verifiedDigests.has(entry.sha256)) return;
    this.verifiedDigests.add(entry.sha256);
    this.verifiedBytes += entry.size_bytes;
  }
  get stats(): { readonly fileCount: number; readonly byteCount: number } {
    return { fileCount: this.verifiedDigests.size, byteCount: this.verifiedBytes };
  }
  async url(path: string): Promise<string> {
    const entry = this.entry(path);
    let pending = this.pending.get(path);
    if (pending === undefined) {
      pending = this.limited(async () => {
        if (entry.media_type === "model/gltf-binary") {
          const bytes = await this.resolver.fetchVerifiedBytes(`assets/${entry.sha256}`, {
            sha256: entry.sha256, sizeBytes: entry.size_bytes, mediaType: entry.media_type,
          }, this.signal);
          const view = new DataView(bytes);
          if (bytes.byteLength < 20 || view.getUint32(0, true) !== 0x46546c67
            || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== bytes.byteLength
            || view.getUint32(16, true) !== 0x4e4f534a || 20 + view.getUint32(12, true) > bytes.byteLength) {
            throw new Error(`已验证 GLB 结构无效：${path}`);
          }
          const gltf = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(
            new Uint8Array(bytes, 20, view.getUint32(12, true)))) as Record<string, unknown>;
          const embedded = (value: unknown): boolean => {
            if (Array.isArray(value)) return value.every(embedded);
            if (value === null || typeof value !== "object") return true;
            return Object.entries(value).every(([key, child]) => key === "uri"
              ? typeof child === "string" && child.startsWith("data:") : embedded(child));
          };
          if (gltf === null || typeof gltf !== "object" || Array.isArray(gltf)
            || (gltf.asset as { version?: unknown } | undefined)?.version !== "2.0" || !embedded(gltf)) {
            throw new Error(`GLB 引用未验证的外部材质：${path}`);
          }
          const url = URL.createObjectURL(new Blob([bytes], { type: entry.media_type }));
          this.glbUrls.add(url);
          this.count(entry);
          return url;
        }
        const asset = await this.resolver.fetchVerified(`assets/${entry.sha256}`, {
          sha256: entry.sha256, sizeBytes: entry.size_bytes, mediaType: entry.media_type,
        }, this.signal);
        this.count(entry);
        return asset.url;
      });
      this.pending.set(path, pending);
    }
    return pending;
  }
  cachedUrl(path: string): string {
    const pending = this.pending.get(path);
    if (pending === undefined) throw new Error(`静态城市视觉素材尚未验证：${path}`);
    // URL modifiers are synchronous, so callers must preload every declared dependency.
    const cached = this.resolved.get(path);
    if (cached === undefined) throw new Error(`静态城市视觉素材尚未就绪：${path}`);
    return cached;
  }
  private readonly resolved = new Map<string, string>();
  async preload(paths: readonly string[]): Promise<void> {
    await Promise.all(paths.map(async path => { this.resolved.set(path, await this.url(path)); }));
  }
  async json(path: string): Promise<unknown> {
    const entry = this.entry(path);
    if (entry.media_type !== "application/json") throw new Error(`视觉素材不是 JSON：${path}`);
    let pending = this.jsonPending.get(path);
    if (pending === undefined) {
      pending = this.limited(async () => {
        const value = await verifiedJson(this.resolver, entry, `视觉素材 ${path}`, this.signal);
        this.count(entry);
        return value;
      });
      this.jsonPending.set(path, pending);
    }
    return pending;
  }
  dispose(): void {
    this.resolver.dispose();
    for (const url of this.glbUrls) URL.revokeObjectURL(url);
    this.glbUrls.clear(); this.pending.clear(); this.jsonPending.clear(); this.resolved.clear();
  }
}

/** Publish a city only after every declared static document and the pack have verified. */
export async function loadReadyAuthoringPresentation(job: AuthoringJob, selection: SceneSelection,
                                                     signal?: AbortSignal): Promise<VerifiedStaticPresentation> {
  if (job.state !== "ready" || job.pack === null || job.presentation === null
    || job.compiler_manifest_sha256 === null) throw new Error("构建任务尚未发布完整城市呈现");
  const presentation = job.presentation;
  const resolver = sameOriginDocumentResolver(presentation.base_url, presentation.manifest);
  let pack: LoadedMeshPack | null = null;
  try {
    const manifest = parseStaticPresentationManifest(await verifiedJson(resolver, presentation.manifest,
      "静态城市 manifest", signal));
    if (manifest.job_id !== job.job_id || manifest.selection_sha256 !== job.selection_sha256
      || manifest.source_id !== selection.source_id || manifest.raw_source_sha256 !== selection.source_sha256
      || manifest.raw_source_sha256 !== job.source_sha256
      || manifest.effective_osm_sha256 !== job.pack.source_sha256
      || manifest.compiler_manifest_sha256 !== job.compiler_manifest_sha256
      || manifest.pack_manifest.sha256 !== job.pack.manifest.sha256
      || manifest.pack_manifest.size_bytes !== job.pack.manifest.size_bytes
      || !sameSceneOrigin(manifest.origin, selection.origin)) {
      throw new Error("静态城市呈现与当前选区、编译结果或网格包不一致");
    }
    const [buildingPlacement, road, rawSignals, rawVisualAssets] = await Promise.all([
      verifiedJson(resolver, manifest.building_placement, "建筑放置", signal),
      verifiedJson(resolver, manifest.road, "城市道路", signal),
      verifiedJson(resolver, manifest.signal_inventory, "网络信号", signal),
      verifiedJson(resolver, manifest.visual_assets, "城市视觉素材", signal),
    ]);
    const signals = parseStaticSignalInventory(rawSignals);
    const roadDocument = validateStaticCityRoadPayload(road);
    if (buildingPlacement === null || typeof buildingPlacement !== "object" || Array.isArray(buildingPlacement)) {
      throw new Error("建筑放置资料格式无效");
    }
    const placementDocument = buildingPlacement as Record<string, unknown>;
    if (placementDocument.schema_version !== "aero-bench.city-building-placement/v1"
      || placementDocument.mesh_pack_manifest_sha256 !== manifest.pack_manifest.sha256
      || placementDocument.mesh_pack_source_sha256 !== manifest.effective_osm_sha256
      || placementDocument.displayed_surface_sha256 !== roadDocument.displayed_surface_sha256
      || roadDocument.source_network_sha256 !== manifest.network.sha256
      || roadDocument.mesh_pack_source_sha256 !== manifest.effective_osm_sha256
      || roadDocument.building_placement_sha256 !== manifest.building_placement.sha256) {
      throw new Error("静态城市道路与建筑放置不属于同一网格和网络");
    }
    const roadIdentity = road as Record<string, unknown>;
    if (roadIdentity.source_osm_sha256 !== manifest.sumo_source_osm_sha256
      || roadIdentity.mesh_pack_manifest_sha256 !== manifest.pack_manifest.sha256
      || roadIdentity.signal_inventory_sha256 !== manifest.signal_inventory.sha256) {
      throw new Error("静态城市道路与编译来源或信号目录不一致");
    }
    const visualEntries = parseVisualAssetInventory(rawVisualAssets);
    const bigcityTree = visualEntries.filter(entry => entry.path.startsWith("/models/bigcity/"))
      .map(entry => ({ path: entry.path, sha256: entry.sha256, size_bytes: entry.size_bytes }));
    if (bigcityTree.length === 0) throw new Error("城市视觉素材目录缺少 BigCity 模型树");
    const treeBytes = new TextEncoder().encode(JSON.stringify(bigcityTree));
    const treeHash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", treeBytes)),
      byte => byte.toString(16).padStart(2, "0")).join("");
    if (treeHash !== manifest.bigcity_library_tree_sha256) throw new Error("BigCity 视觉素材树哈希与静态呈现不一致");
    const visualAssets = new VerifiedVisualAssets(visualEntries, resolver, signal);
    pack = await loadReadyAuthoringPack(job, selection, signal);
    if (pack.manifest.source.sha256 !== manifest.effective_osm_sha256
      || signals.source_network_sha256 !== manifest.network.sha256
      || signals.mesh_pack_source_sha256 !== manifest.effective_osm_sha256) {
      throw new Error("已验证静态信号、网络与城市网格来源不一致");
    }
    const loadedPack = pack;
    return { pack: { ...loadedPack, dispose: () => { loadedPack.dispose(); visualAssets.dispose(); } },
      manifest, buildingPlacement, road, signals, visualAssets };
  } catch (error) { pack?.dispose(); resolver.dispose(); throw error; }
}

// --- Native scene catalog and draft compilation (I1) --------------------------------------
//
// This client never substitutes a WorldPackage, picks an executor profile, or infers a
// registration from a display name. The explicit public-scenario reference document served
// at a registration's `scene_url` must never be passed to `parseCitySceneConfig`: it is the
// backend's `aero-bench.public-scenario/v1` renderer contract, not a city-preview config
// (B-011). A compiled result is resolved input only; `executed` and `verified` are always
// `false` here, and this module never starts a run.

export type NativeEditableExecutionField = "/seed" | "/deployment/executor";
export type NativeRetainedAuthoringField = "/name" | "/environment" | "/stateKeyframes";

export interface NativeSceneRegistration {
  readonly registration_id: string;
  readonly registration_sha256: string;
  readonly scene_path: string;
  readonly scene_url: string;
  readonly scene_schema_version: "aero-bench.public-scenario/v1";
  readonly scene_sha256: string;
  readonly scene_size_bytes: number;
  readonly world_id: string;
  readonly world_digest: string;
  readonly profile_id: string;
  readonly editable_execution_fields: readonly NativeEditableExecutionField[];
  readonly retained_authoring_fields: readonly NativeRetainedAuthoringField[];
  readonly reference_draft: CityWorkspaceConfig;
}
export interface NativeSceneCatalog {
  readonly schema_version: "aero-bench.city-scene-registration-catalog/v1";
  readonly registrations: readonly NativeSceneRegistration[];
}

export function parseNativeSceneRegistration(value: unknown): NativeSceneRegistration {
  assertSceneRegistrationCatalog({ schema_version: "aero-bench.city-scene-registration-catalog/v1", registrations: [value] });
  const item = value as SceneRegistrationCatalog["registrations"][number];
  // Cross-field rules the schema cannot express.
  if (item.scene_url !== `${API}/native-scenes/${item.registration_id}/scene`) throw new Error("场景 URL 与注册 ID 不一致");
  return { ...item, reference_draft: parseCityWorkspaceConfig(item.reference_draft) } as NativeSceneRegistration;
}

export function parseNativeSceneCatalog(value: unknown): NativeSceneCatalog {
  assertSceneRegistrationCatalog(value);
  // An empty catalog is an explicit, valid state (no registration is published yet).
  const registrations = value.registrations.map(parseNativeSceneRegistration);
  if (new Set(registrations.map(item => item.registration_id)).size !== registrations.length) {
    throw new Error("原生场景注册 ID 重复");
  }
  return { schema_version: value.schema_version, registrations };
}

export type NativeSceneCatalogOutcome =
  | { readonly kind: "ok"; readonly catalog: NativeSceneCatalog }
  | { readonly kind: "error"; readonly status: number; readonly code: string; readonly detail: string };

/** `GET /authoring/v1/native-scenes`: 200 the explicit catalog, 503 the compiler is unconfigured. */
export async function fetchNativeSceneCatalog(signal?: AbortSignal): Promise<NativeSceneCatalogOutcome> {
  const { status, body } = await requestJsonMultiStatus(`${API}/native-scenes`, { method: "GET" }, MAX_NATIVE_CATALOG_BYTES, signal);
  if (status === 200) return { kind: "ok", catalog: parseNativeSceneCatalog(body) };
  if (status === 503) {
    const detail = parseAuthoringApiErrorDetail(body);
    return { kind: "error", status, code: detail.code, detail: detail.message };
  }
  throw new Error(`原生场景目录返回未声明的 HTTP 状态码：${status}`);
}

export interface CompilationBlocker {
  readonly code: string;
  readonly field: string;
  readonly message: string;
}
export interface CompiledRunIdentity {
  readonly run_id: string;
  readonly scenario_digest: string;
  readonly world_id: string;
  readonly world_digest: string;
  readonly executor_kind: "docker_reference" | "kubernetes_cluster";
  readonly feasible: boolean;
}
export interface CityCompilationResult {
  readonly schema_version: "aero-bench.city-compilation-result/v1";
  readonly compilation_id: string;
  readonly draft_sha256: string;
  readonly registration_id: string;
  readonly registration_sha256: string;
  readonly status: "blocked" | "compiled";
  readonly blockers: readonly CompilationBlocker[];
  readonly suite: { readonly path: string; readonly sha256: string } | null;
  readonly runs: readonly CompiledRunIdentity[];
  readonly executed: false;
  readonly verified: false;
}

export function parseCityCompilationResult(value: unknown): CityCompilationResult {
  assertCityCompilationResult(value);
  const { status, blockers, runs, suite } = value;
  // Mirrors the backend's cross-field invariant (CityCompilationResult.result_state_is_consistent):
  // a blocked result carries no executable output, and a compiled one always carries both.
  if (status === "blocked"
    ? (blockers.length === 0 || runs.length !== 0 || suite !== null)
    : (blockers.length !== 0 || runs.length === 0 || suite === null)) {
    throw new Error("编译结果状态与阻断、运行或 Suite 字段不一致");
  }
  return value as CityCompilationResult;
}

export interface CityCompileDraftRequest {
  readonly registration_id: string;
  readonly registration_sha256: string;
  readonly draft: CityWorkspaceConfig;
}
export type CompileCityDraftOutcome =
  | { readonly kind: "compiled"; readonly result: CityCompilationResult }
  | { readonly kind: "blocked"; readonly result: CityCompilationResult }
  | { readonly kind: "error"; readonly status: number; readonly code: string; readonly detail: string };

/**
 * `POST /authoring/v1/compilations`: 201 compiled (resolved input only, not execution), 422
 * blocked with field-pointer blockers, 400/415/500/503 explicit API errors. The registration
 * is selected by the caller (its exact `registration_id` and `registration_sha256`); this
 * function never substitutes a default or display-name-matched registration.
 */
export async function compileCityDraft(request: CityCompileDraftRequest, signal?: AbortSignal): Promise<CompileCityDraftOutcome> {
  if (!SOURCE_ID.test(request.registration_id)) throw new Error("注册 ID 无效");
  if (!SHA.test(request.registration_sha256)) throw new Error("注册哈希无效");
  const draft = parseCityWorkspaceConfig(request.draft);
  const body = JSON.stringify({
    schema_version: "aero-bench.city-compile-request/v1",
    registration_id: request.registration_id, registration_sha256: request.registration_sha256, draft,
  });
  const { status, body: responseBody } = await requestJsonMultiStatus(`${API}/compilations`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body }, MAX_COMPILATION_BYTES, signal);
  if (status === 201 || status === 422) {
    const result = parseCityCompilationResult(responseBody);
    if (status === 201 && result.status !== "compiled") throw new Error("编译结果状态与 HTTP 201 不一致");
    if (status === 422 && result.status !== "blocked") throw new Error("编译结果状态与 HTTP 422 不一致");
    if (result.registration_id !== request.registration_id || result.registration_sha256 !== request.registration_sha256) {
      throw new Error("编译结果与请求的注册身份不一致");
    }
    return status === 201 ? { kind: "compiled", result } : { kind: "blocked", result };
  }
  if (status === 400 || status === 415 || status === 500 || status === 503) {
    const detail = parseAuthoringApiErrorDetail(responseBody);
    return { kind: "error", status, code: detail.code, detail: detail.message };
  }
  throw new Error(`草稿编译返回未声明的 HTTP 状态码：${status}`);
}

export type FetchCompilationOutcome =
  | { readonly kind: "found"; readonly result: CityCompilationResult }
  | { readonly kind: "error"; readonly status: number; readonly code: string; readonly detail: string };

/** `GET /authoring/v1/compilations/{id}`: 200 the published immutable result, 404/500/503 explicit API errors. */
export async function fetchCompilation(compilationId: string, signal?: AbortSignal): Promise<FetchCompilationOutcome> {
  if (!SHA.test(compilationId)) throw new Error("编译 ID 无效");
  const { status, body } = await requestJsonMultiStatus(`${API}/compilations/${compilationId}`, { method: "GET" }, MAX_COMPILATION_BYTES, signal);
  if (status === 200) {
    const result = parseCityCompilationResult(body);
    if (result.compilation_id !== compilationId) throw new Error("编译结果 ID 与请求不一致");
    return { kind: "found", result };
  }
  if (status === 404 || status === 500 || status === 503) {
    const detail = parseAuthoringApiErrorDetail(body);
    return { kind: "error", status, code: detail.code, detail: detail.message };
  }
  throw new Error(`获取编译结果返回未声明的 HTTP 状态码：${status}`);
}
