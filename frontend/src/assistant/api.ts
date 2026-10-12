import { WorkbenchApi } from '../workbench/api';

/** Shapes of `/v1/assistant` (docs/product/assistant.md §3–4). */
export interface Connection {
  configured: boolean; source: 'operator' | 'user' | null; base_url: string | null; model: string | null;
  has_key: boolean; status: 'untested' | 'ok' | 'error'; detail: string | null; tested_at: string | null;
}

interface EventBase { seq: number; at: string }
export type AssistantEvent = EventBase & (
  | { kind: 'user'; text: string; context?: MessageContext }
  | { kind: 'assistant'; text: string; suggestions?: string[] }
  | { kind: 'tool'; call_id: string; name: string; status: 'running' | 'ok' | 'error'; label: string; label_zh?: string; detail?: string }
  | { kind: 'plan'; version: number; note: string; variant?: string | null; versions?: number[] }
  | { kind: 'plan_validated'; version: number; valid: boolean; errors?: string[] }
  | { kind: 'applied'; plan_version: number; workspace_id: string; version: number; report: ApplyReport }
  | { kind: 'error'; message: string; code?: string }
  | { kind: 'turn_started'; turn: number }
  | { kind: 'turn_finished'; turn: number; elapsed_s?: number; model_calls?: number; tokens?: number }
  | { kind: 'notice'; text: string }
);
export interface MessageContext { selected_entity?: string; run_id?: string; locale?: string }

export interface Place { id: string; role: string; label: string; label_zh?: string; position?: number[]; type?: string; contract?: Contract; details?: Record<string, unknown> }
export interface Contract { type?: string; ancestry?: string[]; label?: string }
export interface Fleet {
  id: string; role: string; label: string; label_zh?: string; count: number; members: string[]; vehicle_class?: string; task_kinds?: string[];
  start?: { place?: string | null; label?: string }; battery_pct?: number | null; payload_capacity_kg?: number | null; contract?: Contract;
}
export interface Release { mode: 'scheduled' | 'poisson' | string; times_s?: number[]; rate_per_s?: number; start_s?: number; end_s?: number }
export interface Workload { id: string; kind: string; label: string; label_zh?: string; count: number; tasks?: string[]; from: string[]; to: string[]; release?: Release; priority?: number; payload_kg?: number; protected?: boolean }
export interface SceneEvent { id: string; kind: string; label: string; label_zh?: string; at_s: number; place?: string; position?: number[]; layer?: string }
export interface Rule { id: string; label: string; label_zh?: string; value: unknown; unit?: string }
export interface Policy { mode?: 'prescribed' | 'agent'; goal?: string; order?: string[]; rules?: Record<string, unknown>; vehicles?: string[]; selection?: string; ask_radius_m?: number }
export interface Cooperation {
  watchers?: { vehicles?: string[]; notice_radius_m?: number; labels?: Record<string, string> }; responder?: Policy; helpers?: Policy & { labels?: Record<string, string> };
  handoff_points?: Array<{ id: string; label?: string; label_zh?: string; position?: number[] }>;
  incidents?: Array<{ id: string; kind: string; label?: string; label_zh?: string; at_s?: number; position?: number[] }>;
  photograph?: { dwell_s?: number; altitude_m?: number }; provider?: unknown;
}
export interface SceneSummary {
  title?: string; city?: string | null; duration_s?: number; run_mode?: string | null;
  environment?: { wind?: { speed_mps?: number; gusts?: Array<{ start_s: number; end_s: number; speed_mps: number }> }; zones?: Array<{ id: string; label: string; kind: string }> };
  fleets?: Fleet[]; places?: Place[]; workloads?: Workload[]; events?: SceneEvent[]; rules?: Rule[];
  /** The usual rule agents follow for each kind of work (assistant/scene.py ROUTINE_TEXT). */
  routine?: Array<{ kind: string; text: string; text_zh?: string }>;
  algorithms?: Record<string, string>; cooperation?: Cooperation | null; controls?: Record<string, unknown>;
}
export interface DiffItem { kind: string; id: string; label: string; label_zh?: string; fields?: Array<{ name: string; before: unknown; after: unknown }> }
export interface Diff { added: DiffItem[]; changed: DiffItem[]; removed: DiffItem[]; entities?: { added: string[]; changed: string[]; removed: string[] } }
export interface Lane { id: string; label: string; label_zh?: string; kind: 'fleet' | 'vehicle' | 'place' | 'person' | 'system' | string }
export interface Step {
  id: string; from: string; to?: string | null; text: string; text_zh?: string; layer: 'setup' | 'behaviour';
  decided_by?: 'prescribed' | 'agent' | null; branch?: { group: string; when: string } | null; at_s?: number | null; expect?: string | null;
}
export interface Interactions { lanes: Lane[]; steps: Step[] }
export interface Expectation {
  id: string; text: string; text_zh?: string; layer: 'setup' | 'behaviour'; match?: { schema: string; where?: Record<string, unknown> };
  approx_s?: number | null; precheck?: { ok: boolean; text: string; text_zh?: string } | null; depends_on?: string[];
}
export interface Operation { id: string; op: string; explain?: string; [key: string]: unknown }
/** Questions arrive as plain text or with suggested answers (assistant/tools.py QUESTIONS). */
export type OpenQuestion = string | { question: string; options?: string[] };
export const questionOf = (row: OpenQuestion) => typeof row === 'string' ? { question: row, options: [] as string[] } : { options: [] as string[], ...row };
export interface CompileIssue { op_id?: string; field?: string; message: string }
export interface PlanVersion {
  version: number; created_at: string; note: string; variant?: string | null; parent?: number | null;
  base: Record<string, unknown>; base_label?: string; title: string; summary: string;
  operations: Operation[]; assumptions: string[]; open_questions: OpenQuestion[];
  compile: { ok: boolean; errors: CompileIssue[]; warnings: Array<string | CompileIssue> };
  validation: { state: 'pending' | 'valid' | 'invalid'; errors?: string[] };
  scene?: SceneSummary; diff_base?: Diff; diff_previous?: Diff | null; interactions?: Interactions; expectations?: Expectation[];
}
/** A draft that failed to compile is stored with empty scene parts; it has no scene, sequence or expectations to show. */
export function compiledParts(plan: PlanVersion): PlanVersion {
  if (plan.compile.ok) return plan;
  return { ...plan, scene: undefined, interactions: undefined, expectations: undefined, diff_base: undefined, diff_previous: null };
}
export interface PlanVersionRow { version: number; note: string; variant?: string | null; parent?: number | null; created_at: string; compile_ok: boolean; validation_state: 'pending' | 'valid' | 'invalid' }
export interface ApplyReport { created: DiffItem[]; changed: DiffItem[]; removed: DiffItem[]; entities?: Diff['entities']; summary_text?: string; validation?: unknown }
export interface ApplyRecord { plan_version: number; workspace_id: string; version: number; revision: number; applied_at: string; report: ApplyReport }
export interface Session {
  id: string; created_at: string; updated_at: string; workspace_id: string | null; title: string; locale?: string; busy: boolean; last_seq: number;
  events: AssistantEvent[]; plan: PlanVersion | null; plan_versions: PlanVersionRow[]; alternatives: number[]; applied: ApplyRecord[];
  connection?: { configured: boolean; model: string | null; status?: string };
}
export interface SessionRow { id: string; title: string; workspace_id: string | null; updated_at: string; plan_version?: number | null; applied_versions?: number[] }
/** One expectation checked against the committed journal (assistant/story.py). */
export interface StoryEvidence { schema?: string; text?: string; text_zh?: string; at_ns?: string | number; cut?: number }
export interface StoryItem {
  id: string; text: string; text_zh?: string; status: 'happened' | 'missed' | 'pending' | 'not_taken'; progress?: { done: number; total: number; configured?: number };
  at_ns?: string | number | null; cut?: number | null; evidence?: StoryEvidence | null;
  explanation?: { text: string; text_zh?: string; evidence?: StoryEvidence[] } | null;
}
export interface Story {
  run_id: string; status?: string; terminal?: boolean; plan_version?: number | null; last_at_ns?: string | number | null;
  expectations: StoryItem[]; events: Array<{ schema: string; text: string; text_zh?: string; at_ns: string | number; cut?: number; entities?: string[] }>;
}
export interface PlanPreview { scenario: Record<string, any>; name: string; demo_console?: any }

export class AssistantApi {
  private readonly http: WorkbenchApi;
  constructor(readonly base: string) { this.http = new WorkbenchApi(base); }
  private path(rest: string) { return `/v1/assistant${rest}`; }
  connection(signal?: AbortSignal) { return this.http.request<Connection>(this.path('/connection'), { signal }); }
  connect(body: { base_url: string; model: string; api_key?: string; clear_key?: boolean }) { return this.http.request<Connection>(this.path('/connection'), { body }); }
  test() { return this.http.request<Connection>(this.path('/connection/test'), { body: {} }); }
  sessions(workspaceId?: string, signal?: AbortSignal) { return this.http.request<SessionRow[]>(this.path(`/sessions${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`), { signal }); }
  create(workspaceId?: string, locale?: string) { return this.http.request<Session>(this.path('/sessions'), { body: { ...(workspaceId ? { workspace_id: workspaceId } : {}), ...(locale ? { locale } : {}) } }); }
  session(id: string, signal?: AbortSignal) { return this.http.request<Session>(this.path(`/sessions/${encodeURIComponent(id)}`), { signal }); }
  send(id: string, text: string, context?: MessageContext) { return this.http.request<{ turn: number; seq: number }>(this.path(`/sessions/${encodeURIComponent(id)}/messages`), { body: { text, ...(context ? { context } : {}) } }); }
  events(id: string, after: number, wait: number, signal?: AbortSignal) { return this.http.request<{ events: AssistantEvent[]; last_seq: number; busy: boolean }>(this.path(`/sessions/${encodeURIComponent(id)}/events?after=${after}&wait=${wait}`), { signal }); }
  cancel(id: string) { return this.http.request<{ cancelled: boolean }>(this.path(`/sessions/${encodeURIComponent(id)}/cancel`), { body: {} }); }
  async plan(id: string, version: number, signal?: AbortSignal) { return compiledParts(await this.http.request<PlanVersion>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}`), { signal })); }
  preview(id: string, version: number, signal?: AbortSignal) { return this.http.request<PlanPreview>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}/scenario`), { signal }); }
  choose(id: string, version: number) { return this.http.request<Session>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}/choose`), { body: {} }); }
  apply(id: string, version: number) { return this.http.request<ApplyRecord>(this.path(`/sessions/${encodeURIComponent(id)}/apply`), { body: { plan_version: version } }); }
  story(id: string, runId: string, signal?: AbortSignal) { return this.http.request<Story>(this.path(`/sessions/${encodeURIComponent(id)}/story/${encodeURIComponent(runId)}`), { signal }); }
}
