/** Strict identity draft for one completed OSM selected-city job.
 *
 * A selected-scene draft only binds the exact SceneSelection and the
 * content-addressed identities of the ready job that published the city.
 * It never carries scenario content, never touches the old city-workspace/v1
 * scenePath draft, and never lets a failed restore substitute another city. */
import type {
  AuthoringCatalog, AuthoringJob, StaticPresentationManifest, VerifiedStaticPresentation,
} from "./city-authoring-api";
import type { SceneSelection } from "./city-region-selector";
import { parseStrictJson } from "./strict-json";

export const CITY_SELECTED_DRAFT_SCHEMA = "aero-bench.city-selected-scene-draft/v1" as const;
export const CITY_SELECTED_DRAFT_STORAGE_KEY = "aero-bench.city-selected-scene-draft.v1";

const SHA = /^[0-9a-f]{64}$/;
const SOURCE_ID = /^[a-z][a-z0-9_.-]*$/;

export interface SelectedSceneDraft {
  readonly purpose: "selected-scene-authoring";
  readonly schema_version: typeof CITY_SELECTED_DRAFT_SCHEMA;
  /** The exact SceneSelection submitted for job_id. Separate from the old scenePath preview. */
  readonly selection: SceneSelection;
  /** Server-side SHA-256 of that exact selection document, as reported by the ready job. */
  readonly selection_sha256: string;
  readonly job_id: string;
  /** Registered raw OSM SHA-256 of the selection source; equals selection.source_sha256. */
  readonly source_sha256: string;
  readonly pack_manifest_sha256: string;
  readonly presentation_manifest_sha256: string;
}

function object(value: unknown, keys: readonly string[], label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 格式无效`);
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) {
    throw new Error(`${label} 字段不符合协议`);
  }
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

/** CPython float.__repr__, the number spelling json.dumps uses in server-side hashes. */
export function pythonFloatRepr(value: number): string {
  if (!Number.isFinite(value)) throw new Error("选区坐标必须是有限数值");
  if (Object.is(value, -0)) return "-0.0";
  if (value === 0) return "0.0";
  const negative = value < 0;
  // Shortest round-trip digits and the Python decimal point position for the absolute value.
  const [mantissa, exponentText] = Math.abs(value).toExponential().split("e");
  const digits = mantissa!.replace(".", "");
  const point = Number(exponentText) + 1;
  let body: string;
  if (point > -4 && point <= 16) {
    body = point <= 0 ? `0.${"0".repeat(-point)}${digits}`
      : point >= digits.length ? `${digits}${"0".repeat(point - digits.length)}.0`
      : `${digits.slice(0, point)}.${digits.slice(point)}`;
  } else {
    const exponent = point - 1;
    const sign = exponent < 0 ? "-" : "+";
    body = (digits.length === 1 ? digits : `${digits[0]}.${digits.slice(1)}`)
      + `e${sign}${String(Math.abs(exponent)).padStart(2, "0")}`;
  }
  return negative ? `-${body}` : body;
}

type CanonicalValue = string | number | { readonly [key: string]: CanonicalValue };

function canonicalJsonText(value: CanonicalValue): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") return pythonFloatRepr(value);
  return `{${Object.keys(value).sort()
    .map(key => `${JSON.stringify(key)}:${canonicalJsonText(value[key]!)}`).join(",")}}`;
}

/** Exact server-side canonical text (sorted keys, compact separators, CPython float repr). */
export function canonicalSelectionText(selection: SceneSelection): string {
  return canonicalJsonText({
    schema_version: selection.schema_version,
    source_id: selection.source_id,
    source_sha256: selection.source_sha256,
    origin: {
      latitude_deg: selection.origin.latitude_deg,
      longitude_deg: selection.origin.longitude_deg,
      ellipsoid_height_m: selection.origin.ellipsoid_height_m,
      geoid_undulation_m: selection.origin.geoid_undulation_m,
      amsl_m: selection.origin.amsl_m,
    },
    bounds_enu_m: {
      min_east_m: selection.bounds_enu_m.min_east_m,
      max_east_m: selection.bounds_enu_m.max_east_m,
      min_north_m: selection.bounds_enu_m.min_north_m,
      max_north_m: selection.bounds_enu_m.max_north_m,
    },
  });
}

/** The selection digest the authoring server computes for a submitted SceneSelection. */
export async function selectionDigest(selection: SceneSelection): Promise<string> {
  const bytes = new TextEncoder().encode(canonicalSelectionText(selection));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
}

/** Proof that the draft's exact selection document is the one bound by selection_sha256.
 * An imported selection whose bounds were edited while keeping the stored digest is unprovable
 * and must be rejected — never displayed, never re-bound to a different city. */
export async function verifySelectedSceneDigest(draft: SelectedSceneDraft): Promise<string[]> {
  const digest = await selectionDigest(draft.selection);
  return digest === draft.selection_sha256 ? []
    : ["选区草稿的 SceneSelection 与绑定的 selection_sha256 不一致（选区文档被改动或哈希无法重算），拒绝使用"];
}

/** Strict SceneSelection reader for drafts; the selection document itself is protocol data. */
export function parseSceneSelection(value: unknown): SceneSelection {
  const item = object(value, ["schema_version", "source_id", "source_sha256", "origin", "bounds_enu_m"], "选区");
  if (item.schema_version !== "aero-bench.scene-selection/v1") throw new Error("选区版本无效");
  const sourceId = string(item.source_id, "选区来源 ID");
  if (!SOURCE_ID.test(sourceId)) throw new Error("选区来源 ID 无效");
  const origin = object(item.origin,
    ["latitude_deg", "longitude_deg", "ellipsoid_height_m", "geoid_undulation_m", "amsl_m"], "选区原点");
  const result: SceneSelection = {
    schema_version: "aero-bench.scene-selection/v1",
    source_id: sourceId,
    source_sha256: sha(item.source_sha256, "选区来源哈希"),
    origin: {
      latitude_deg: number(origin.latitude_deg, "原点纬度"), longitude_deg: number(origin.longitude_deg, "原点经度"),
      ellipsoid_height_m: number(origin.ellipsoid_height_m, "原点椭球高"),
      geoid_undulation_m: number(origin.geoid_undulation_m, "原点大地水准面起伏"),
      amsl_m: number(origin.amsl_m, "原点海拔"),
    },
    bounds_enu_m: (() => {
      const bounds = object(item.bounds_enu_m,
        ["min_east_m", "max_east_m", "min_north_m", "max_north_m"], "选区 ENU 外包");
      const box = {
        min_east_m: number(bounds.min_east_m, "最小东向"), max_east_m: number(bounds.max_east_m, "最大东向"),
        min_north_m: number(bounds.min_north_m, "最小北向"), max_north_m: number(bounds.max_north_m, "最大北向"),
      };
      if (!(box.min_east_m < box.max_east_m && box.min_north_m < box.max_north_m)) throw new Error("选区 ENU 外包退化");
      return box;
    })(),
  };
  if (result.origin.latitude_deg <= -90 || result.origin.latitude_deg >= 90
    || result.origin.longitude_deg < -180 || result.origin.longitude_deg >= 180) throw new Error("选区原点坐标无效");
  if (Math.abs(result.origin.ellipsoid_height_m - result.origin.geoid_undulation_m - result.origin.amsl_m) > 1e-9) {
    throw new Error("选区原点高程关系无效");
  }
  return result;
}

/** Validate one selected-scene draft. The five identity bindings are checked together. */
export function parseSelectedSceneDraft(value: unknown): SelectedSceneDraft {
  const item = object(value, ["purpose", "schema_version", "selection", "selection_sha256", "job_id",
    "source_sha256", "pack_manifest_sha256", "presentation_manifest_sha256"], "选区草稿");
  if (item.purpose !== "selected-scene-authoring") throw new Error("选区草稿 purpose 无效");
  if (item.schema_version !== CITY_SELECTED_DRAFT_SCHEMA) throw new Error("选区草稿版本无效");
  const draft: SelectedSceneDraft = {
    purpose: "selected-scene-authoring",
    schema_version: CITY_SELECTED_DRAFT_SCHEMA,
    selection: parseSceneSelection(item.selection),
    selection_sha256: sha(item.selection_sha256, "选区哈希"),
    job_id: sha(item.job_id, "任务 ID"),
    source_sha256: sha(item.source_sha256, "原始 OSM 哈希"),
    pack_manifest_sha256: sha(item.pack_manifest_sha256, "网格包 manifest SHA-256"),
    presentation_manifest_sha256: sha(item.presentation_manifest_sha256, "城市呈现 manifest SHA-256"),
  };
  if (draft.selection.source_sha256 !== draft.source_sha256) {
    throw new Error("选区草稿的原始 OSM 哈希与 SceneSelection 不一致");
  }
  return draft;
}

export async function importSelectedSceneDraft(json: string): Promise<SelectedSceneDraft> {
  const draft = parseSelectedSceneDraft(parseStrictJson(json));
  const issues = await verifySelectedSceneDigest(draft);
  if (issues.length) throw new Error(issues.join("；"));
  return draft;
}

export function exportSelectedSceneDraft(draft: SelectedSceneDraft): string {
  return JSON.stringify(parseSelectedSceneDraft(draft), null, 2);
}

/** Bind a ready job only after its identities match the selection it was built from. */
export async function createSelectedSceneDraft(selection: SceneSelection, job: AuthoringJob): Promise<SelectedSceneDraft> {
  if (job.state !== "ready" || job.pack === null || job.presentation === null) {
    throw new Error("只有发布完整城市呈现的 ready 任务才能建立选区草稿");
  }
  if (job.source_sha256 !== selection.source_sha256) {
    throw new Error("任务的原始 OSM 哈希与 SceneSelection 不一致");
  }
  const draft = parseSelectedSceneDraft({
    purpose: "selected-scene-authoring",
    schema_version: CITY_SELECTED_DRAFT_SCHEMA,
    selection,
    selection_sha256: job.selection_sha256,
    job_id: job.job_id,
    source_sha256: job.source_sha256,
    pack_manifest_sha256: job.pack.manifest.sha256,
    presentation_manifest_sha256: job.presentation.manifest.sha256,
  });
  const issues = await verifySelectedSceneDigest(draft);
  if (issues.length) throw new Error(`任务绑定的选区哈希与选区文档不一致：${issues.join("；")}`);
  return draft;
}

export function sameSelectedSceneDraft(left: SelectedSceneDraft | null, right: SelectedSceneDraft): boolean {
  if (left === null) return false;
  return left.job_id === right.job_id && left.selection_sha256 === right.selection_sha256
    && left.source_sha256 === right.source_sha256
    && left.pack_manifest_sha256 === right.pack_manifest_sha256
    && left.presentation_manifest_sha256 === right.presentation_manifest_sha256
    && left.selection.source_id === right.selection.source_id
    && left.selection.source_sha256 === right.selection.source_sha256
    && left.selection.origin.latitude_deg === right.selection.origin.latitude_deg
    && left.selection.origin.longitude_deg === right.selection.origin.longitude_deg
    && left.selection.origin.ellipsoid_height_m === right.selection.origin.ellipsoid_height_m
    && left.selection.origin.geoid_undulation_m === right.selection.origin.geoid_undulation_m
    && left.selection.origin.amsl_m === right.selection.origin.amsl_m
    && left.selection.bounds_enu_m.min_east_m === right.selection.bounds_enu_m.min_east_m
    && left.selection.bounds_enu_m.max_east_m === right.selection.bounds_enu_m.max_east_m
    && left.selection.bounds_enu_m.min_north_m === right.selection.bounds_enu_m.min_north_m
    && left.selection.bounds_enu_m.max_north_m === right.selection.bounds_enu_m.max_north_m;
}

/** Status-display comparison with the local draft only; restore paths always re-verify the digest. */
export function storedSelectedSceneDraftMatches(draft: SelectedSceneDraft | null): boolean {
  if (draft === null) return false;
  const stored = window.localStorage.getItem(CITY_SELECTED_DRAFT_STORAGE_KEY);
  if (stored === null) return false;
  try {
    return sameSelectedSceneDraft(parseSelectedSceneDraft(parseStrictJson(stored)), draft);
  } catch {
    return false;
  }
}

/** Identity mismatches between a draft and a fetched job. Empty means the job may be displayed. */
export function verifySelectedSceneDraft(draft: SelectedSceneDraft, job: AuthoringJob): string[] {
  const issues: string[] = [];
  if (job.job_id !== draft.job_id) issues.push("任务 ID 与选区草稿绑定不一致");
  if (job.state !== "ready") issues.push(`任务状态为 ${job.state}，只有 ready 任务可恢复选区草稿`);
  if (job.source_sha256 !== draft.source_sha256) issues.push("任务的原始 OSM 哈希与选区草稿绑定不一致");
  if (job.selection_sha256 !== draft.selection_sha256) issues.push("任务的选区哈希与草稿绑定的 SceneSelection 不一致");
  if (job.pack === null || job.pack.manifest.sha256 !== draft.pack_manifest_sha256) {
    issues.push("任务的网格包 manifest SHA-256 与选区草稿绑定不一致");
  }
  if (job.presentation === null || job.presentation.manifest.sha256 !== draft.presentation_manifest_sha256) {
    issues.push("任务的城市呈现 manifest SHA-256 与选区草稿绑定不一致");
  }
  return issues;
}

/** The registered OSM source must still be the exact source the draft was built from. */
export function verifySelectedSceneSource(draft: SelectedSceneDraft, catalog: AuthoringCatalog): string[] {
  const source = catalog.sources.find(item => item.source_id === draft.selection.source_id);
  if (source === undefined) return [`注册 OSM 来源已移除：${draft.selection.source_id}`];
  return source.sha256 === draft.source_sha256 ? []
    : ["注册 OSM 来源 SHA-256 已变化，拒绝恢复选区草稿"];
}

/** Cross-check the content-verified presentation against the draft before any display. */
export function verifySelectedScenePresentation(draft: SelectedSceneDraft,
                                                manifest: StaticPresentationManifest): string[] {
  const issues: string[] = [];
  if (manifest.job_id !== draft.job_id) issues.push("城市呈现的任务 ID 与选区草稿绑定不一致");
  if (manifest.selection_sha256 !== draft.selection_sha256) issues.push("城市呈现的选区哈希与选区草稿绑定不一致");
  if (manifest.raw_source_sha256 !== draft.source_sha256) issues.push("城市呈现的原始 OSM 哈希与选区草稿绑定不一致");
  if (manifest.pack_manifest.sha256 !== draft.pack_manifest_sha256) {
    issues.push("城市呈现的网格包 manifest SHA-256 与选区草稿绑定不一致");
  }
  return issues;
}

export interface SelectedSceneRestoreDeps {
  readonly fetchCatalog: () => Promise<AuthoringCatalog>;
  readonly fetchJob: (jobId: string) => Promise<AuthoringJob>;
  /** Must verify every content-addressed byte before it returns, e.g. loadReadyAuthoringPresentation. */
  readonly loadPresentation: (job: AuthoringJob, selection: SceneSelection) => Promise<VerifiedStaticPresentation>;
}

export type SelectedSceneRestoreResult =
  | { readonly ok: true; readonly draft: SelectedSceneDraft; readonly job: AuthoringJob;
      readonly presentation: VerifiedStaticPresentation }
  | { readonly ok: false; readonly message: string };

/** Re-fetch and verify one bound job. Failures never rebuild, resubmit, or substitute another city. */
export async function restoreSelectedSceneDraft(draft: SelectedSceneDraft,
                                               deps: SelectedSceneRestoreDeps): Promise<SelectedSceneRestoreResult> {
  // The exact selection document must be proven against its bound digest before any fetch or display.
  const digestIssues = await verifySelectedSceneDigest(draft);
  if (digestIssues.length) return { ok: false, message: digestIssues.join("；") };
  let catalog: AuthoringCatalog;
  try {
    catalog = await deps.fetchCatalog();
  } catch (error) {
    return { ok: false, message: `读取 OSM 来源注册失败：${error instanceof Error ? error.message : String(error)}` };
  }
  const sourceIssues = verifySelectedSceneSource(draft, catalog);
  if (sourceIssues.length) return { ok: false, message: sourceIssues.join("；") };
  let job: AuthoringJob;
  try {
    job = await deps.fetchJob(draft.job_id);
  } catch (error) {
    return { ok: false, message: `选区任务不可用：${error instanceof Error ? error.message : String(error)}` };
  }
  const jobIssues = verifySelectedSceneDraft(draft, job);
  if (jobIssues.length) return { ok: false, message: jobIssues.join("；") };
  let presentation: VerifiedStaticPresentation;
  try {
    presentation = await deps.loadPresentation(job, draft.selection);
  } catch (error) {
    return { ok: false, message: `选区城市呈现验证失败：${error instanceof Error ? error.message : String(error)}` };
  }
  const presentationIssues = verifySelectedScenePresentation(draft, presentation.manifest);
  if (presentationIssues.length) {
    presentation.pack.dispose();
    return { ok: false, message: presentationIssues.join("；") };
  }
  return { ok: true, draft, job, presentation };
}

export function loadSelectedSceneDraft(): Promise<SelectedSceneDraft | null> {
  const stored = window.localStorage.getItem(CITY_SELECTED_DRAFT_STORAGE_KEY);
  return stored === null ? Promise.resolve(null) : importSelectedSceneDraft(stored);
}

export async function saveSelectedSceneDraft(draft: SelectedSceneDraft): Promise<void> {
  const valid = parseSelectedSceneDraft(draft);
  const issues = await verifySelectedSceneDigest(valid);
  if (issues.length) throw new Error(issues.join("；"));
  window.localStorage.setItem(CITY_SELECTED_DRAFT_STORAGE_KEY, JSON.stringify(valid));
}

export interface SelectedDraftControlState {
  /** Identity of the city currently displayed; null until one verifies. */
  readonly identity: SelectedSceneDraft | null;
  /** Browser sceneReady for that identity. API ready alone never permits saving. */
  readonly sceneReady: boolean;
  readonly busy: boolean;
  /** localStorage already holds exactly this identity. */
  readonly saved: boolean;
  readonly error: string | null;
}

export function selectedDraftStatus(state: SelectedDraftControlState):
    { readonly text: string; readonly state: "idle" | "busy" | "dirty" | "saved" | "error" } {
  if (state.error !== null) return { text: state.error, state: "error" };
  if (state.busy) return { text: "选区任务进行中，完成后才能保存或导出选区草稿。", state: "busy" };
  if (state.identity === null) {
    return { text: "完成的选区任务可保存为本地选区草稿；旧草稿不受影响。", state: "idle" };
  }
  if (!state.sceneReady) {
    return { text: "任务已就绪，等待浏览器完成静态城市装配后才能保存或导出。", state: "dirty" };
  }
  return state.saved
    ? { text: "选区草稿已保存到本地浏览器", state: "saved" }
    : { text: "选区草稿未保存", state: "dirty" };
}

export interface SelectedDraftControls {
  readonly root: HTMLElement;
  update(state: SelectedDraftControlState): void;
}

/** Separate selected-scene draft controls; the old workspace draft controls stay untouched. */
export function createSelectedDraftControls(handlers: {
  readonly onSave: () => void;
  readonly onExport: () => void;
  readonly onImport: (file: File) => void;
}): SelectedDraftControls {
  const root = document.createElement("section");
  root.className = "studio-authoring-draft";
  root.id = "studio-selected-draft";
  const status = document.createElement("div");
  status.className = "studio-authoring-draft-status";
  status.id = "studio-selected-draft-status";
  status.setAttribute("role", "status");
  const actions = document.createElement("div");
  actions.className = "studio-authoring-actions";
  const save = document.createElement("button");
  save.type = "button"; save.className = "studio-button"; save.id = "studio-selected-save";
  save.textContent = "保存选区草稿";
  save.addEventListener("click", handlers.onSave);
  const exportButton = document.createElement("button");
  exportButton.type = "button"; exportButton.className = "studio-button"; exportButton.id = "studio-selected-export";
  exportButton.textContent = "导出选区 JSON";
  exportButton.addEventListener("click", handlers.onExport);
  const importLabel = document.createElement("label");
  importLabel.className = "studio-button studio-import-label";
  importLabel.htmlFor = "studio-selected-import";
  importLabel.textContent = "导入选区 JSON";
  const importInput = document.createElement("input");
  importInput.id = "studio-selected-import";
  importInput.type = "file";
  importInput.accept = ".json,application/json";
  importInput.setAttribute("aria-label", "导入选区草稿 JSON");
  importInput.addEventListener("change", () => {
    const file = importInput.files?.[0];
    importInput.value = "";
    if (file !== undefined) handlers.onImport(file);
  });
  importLabel.append(importInput);
  actions.append(save, exportButton, importLabel);
  root.append(status, actions);
  return {
    root,
    update(state: SelectedDraftControlState): void {
      const view = selectedDraftStatus(state);
      status.textContent = view.text;
      status.dataset.state = view.state;
      const gated = state.busy || state.identity === null || !state.sceneReady;
      save.disabled = gated;
      exportButton.disabled = gated;
      importInput.disabled = state.busy;
      const gateNote = state.identity !== null && !state.sceneReady && !state.busy
        ? "浏览器报告 sceneReady 后才能保存或导出选区草稿" : "";
      save.title = gateNote;
      exportButton.title = gateNote;
    },
  };
}
