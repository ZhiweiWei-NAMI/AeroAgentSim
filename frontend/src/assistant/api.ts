import { WorkbenchApi } from '../workbench/api';
import type { Workspace } from '../studio/api';

/** Shapes of `/v1/assistant` (docs/product/assistant.md §3–4). */
export interface Connection {
  configured: boolean; source: 'operator' | 'user' | null; base_url: string | null; model: string | null;
  has_key: boolean; status: 'untested' | 'ok' | 'error'; detail: string | null; tested_at: string | null;
}

export type TextSource = 'model' | 'platform' | 'unknown';
export type TurnStatus = 'running' | 'completed' | 'budget_exhausted' | 'provider_error' | 'error' | 'cancelled' | 'interrupted' | 'unknown';
/** Missing provenance in legacy payloads is unknown, never implicitly model-authored. */
export interface TextPart { text: string; source?: TextSource }
interface TextProvenance { parts?: TextPart[]; source?: TextSource; status?: TurnStatus; turn?: number }
interface EventBase { seq: number; at: string }
export type AssistantEvent = EventBase & (
  | { kind: 'user'; text: string; context?: MessageContext }
  | ({ kind: 'assistant'; text: string; suggestions?: string[] } & TextProvenance)
  | { kind: 'tool'; call_id: string; name: string; status: 'running' | 'ok' | 'error'; label: string; label_zh?: string; detail?: string }
  | { kind: 'plan'; version: number; note: string; variant?: string | null; versions?: number[] }
  | { kind: 'plan_validated'; version: number; valid: boolean; errors?: PlanIssue[] }
  | { kind: 'applied'; plan_version: number; workspace_id: string; version: number; report: ApplyReport }
  | ({ kind: 'error'; message: string; code?: string } & TextProvenance)
  | { kind: 'turn_started'; turn: number }
  | { kind: 'turn_finished'; turn: number; status?: TurnStatus; elapsed_s?: number; model_calls?: number; tokens?: number }
  | ({ kind: 'notice'; text: string } & TextProvenance)
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
export interface DiffField {
  name: string; before: unknown; after: unknown; label?: string; label_zh?: string;
  before_text?: string; before_text_zh?: string; after_text?: string; after_text_zh?: string;
}
export interface DiffItem {
  kind: string; kind_label?: string; kind_label_zh?: string; id: string; label: string; label_zh?: string; fields?: DiffField[];
}
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
export interface OperationText { en: string; zh: string }
export interface OperationDescriptor {
  id: string; title: OperationText; summary?: OperationText; parameters: Record<string, unknown>; aliases?: string[];
  prerequisites: Array<{ operation: string; params: Record<string, unknown>; conditional: boolean; note: string | null; warning: string | null }>;
}
export interface OperationDescriptors {
  api_version: 1; families: Array<{ name: string; operations: OperationDescriptor[] }>;
}
/** Client-only display metadata, never an authored recipe or an approval result. */
export interface OperationCopy {
  descriptors?: OperationDescriptors; error?: string;
  /** A shared, non-rejecting display-only update; session and plan reads never await it. */
  pending?: Promise<OperationCopy>;
}
/** Questions arrive as plain text or with suggested answers (assistant/tools.py QUESTIONS). */
export type OpenQuestion = string | { question: string; options?: string[] };
export const questionOf = (row: OpenQuestion) => typeof row === 'string' ? { question: row, options: [] as string[] } : { options: [] as string[], ...row };
export interface CompileIssue { op_id?: string | null; field?: string | null; message: string }
export type PlanIssue = string | CompileIssue;
export const issueOf = (issue: PlanIssue): CompileIssue => typeof issue === 'string' ? { message: issue } : issue;
export interface PlanVersion {
  version: number; created_at: string; note: string; variant?: string | null; parent?: number | null;
  base: Record<string, unknown>; base_label?: string; title: string; summary: string;
  operations: Operation[]; assumptions: string[]; open_questions: OpenQuestion[];
  compile: { ok: boolean; errors: CompileIssue[]; warnings: Array<string | CompileIssue>; operations?: Operation[] };
  validation: { state: 'pending' | 'valid' | 'invalid'; errors?: PlanIssue[] };
  scene?: SceneSummary; diff_base?: Diff; diff_previous?: Diff | null; interactions?: Interactions; expectations?: Expectation[];
  /** Attached by this client from the session-cached descriptor request. */
  operationCopy?: OperationCopy;
}
/** A draft that failed to compile is stored with empty scene parts; it has no scene, sequence or expectations to show. */
export function compiledParts(plan: PlanVersion): PlanVersion {
  if (plan.compile.ok) return plan;
  return { ...plan, scene: undefined, interactions: undefined, expectations: undefined, diff_base: undefined, diff_previous: null };
}
export interface PlanVersionRow { version: number; note: string; variant?: string | null; parent?: number | null; created_at: string; compile_ok: boolean; validation_state: 'pending' | 'valid' | 'invalid' }
export interface ApplyReport { created: DiffItem[]; changed: DiffItem[]; removed: DiffItem[]; entities?: Diff['entities']; summary_text?: string; validation?: unknown }
export interface ApplyRecord { plan_version: number; workspace_id: string; version: number; revision: number; applied_at: string; report: ApplyReport }
export interface SessionTurn {
  turn: number; status: TurnStatus; parts: TextPart[];
  /** Exact provider final-message text, not the transport envelope; null means no text. */
  model_reply?: string | null;
  elapsed_s?: number; model_calls?: number; tokens?: number;
}
export interface Session {
  id: string; created_at: string; updated_at: string; workspace_id: string | null; title: string; locale?: string; busy: boolean; last_seq: number;
  events: AssistantEvent[]; plan: PlanVersion | null; plan_versions: PlanVersionRow[]; alternatives: number[]; applied: ApplyRecord[];
  turns?: SessionTurn[];
  connection?: { configured: boolean; model: string | null; status?: string };
}
export interface SessionRow { id: string; title: string; workspace_id: string | null; updated_at: string; plan_version?: number | null; applied_versions?: number[] }
/** One expectation checked against the committed journal (assistant/story.py). */
export interface StoryEvidence { schema?: string; text?: string; text_zh?: string; at_ns?: string | number; cut?: number }
export interface StoryItem {
  id: string; text: string; text_zh?: string; status: 'happened' | 'missed' | 'pending' | 'not_taken' | 'not_evaluated'; progress?: { done: number; total: number; configured?: number };
  at_ns?: string | number | null; cut?: number | null; evidence?: StoryEvidence | null;
  explanation?: { text: string; text_zh?: string; evidence?: StoryEvidence[] } | null;
}
export interface Story {
  run_id: string; status?: string; terminal?: boolean; plan_version?: number | null; last_at_ns?: string | number | null;
  expectations: StoryItem[]; events: Array<{ schema: string; text: string; text_zh?: string; at_ns: string | number; cut?: number; entities?: string[] }>;
}
/** C10 geometry is already in the proposal's local east/north/up metres. */
export interface RegionPreview {
  origin: { lat: number; lon: number; alt: number }; bounds: number[];
  geometry: { buildings: { type: string; features: any[] }; roads: Array<{ id: string; points: number[][] }>; truncated: boolean };
}
export interface PlanPreview {
  scenario: Record<string, any>; name: string; demo_console?: Workspace['demo_console'];
  fixture_digests?: Workspace['fixture_digests'];
  region?: Workspace['region']; scene?: Workspace['scene']; region_preview?: RegionPreview;
  scene_source?: Workspace['scene_source']; preview?: Workspace['preview'];
}

export class AssistantApi {
  private readonly http: WorkbenchApi;
  private readonly descriptors = new Map<string, Promise<OperationDescriptors>>();
  private readonly copies = new Map<string, OperationCopy>();
  constructor(readonly base: string) { this.http = new WorkbenchApi(base); }
  private path(rest: string) { return `/v1/assistant${rest}`; }
  /** Share the request (including failures) across refreshes, versions and locales in one conversation. */
  operations(sessionId: string): Promise<OperationDescriptors> {
    let request = this.descriptors.get(sessionId);
    if (!request) {
      // No caller-owned abort signal: cancelling a plan read must not poison the shared request.
      request = this.http.request<OperationDescriptors>(this.path('/operations')).then(value => {
        if (value.api_version !== 1) throw new Error('Unsupported assistant operation descriptors / 不支持的助手操作说明版本');
        return value;
      });
      this.descriptors.set(sessionId, request);
    }
    return request;
  }
  private operationCopy(sessionId: string): OperationCopy {
    const cached = this.copies.get(sessionId);
    if (cached) return cached;
    const pending = this.operations(sessionId).then<OperationCopy, OperationCopy>(
      descriptors => ({ descriptors }),
      problem => ({ error: problem instanceof Error ? problem.message : String(problem) }),
    ).then(copy => { this.copies.set(sessionId, copy); return copy; });
    const copy = { pending };
    this.copies.set(sessionId, copy);
    return copy;
  }
  private describedSession(session: Session): Session {
    const copy = this.operationCopy(session.id);
    return { ...session, plan: session.plan ? { ...session.plan, operationCopy: copy } : null };
  }
  connection(signal?: AbortSignal) { return this.http.request<Connection>(this.path('/connection'), { signal }); }
  connect(body: { base_url: string; model: string; api_key?: string; clear_key?: boolean }) { return this.http.request<Connection>(this.path('/connection'), { body }); }
  test() { return this.http.request<Connection>(this.path('/connection/test'), { body: {} }); }
  sessions(workspaceId?: string, signal?: AbortSignal) { return this.http.request<SessionRow[]>(this.path(`/sessions${workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''}`), { signal }); }
  async create(workspaceId?: string, locale?: string) { return this.describedSession(await this.http.request<Session>(this.path('/sessions'), { body: { ...(workspaceId ? { workspace_id: workspaceId } : {}), ...(locale ? { locale } : {}) } })); }
  async session(id: string, signal?: AbortSignal) { return this.describedSession(await this.http.request<Session>(this.path(`/sessions/${encodeURIComponent(id)}`), { signal })); }
  send(id: string, text: string, context?: MessageContext) { return this.http.request<{ turn: number; seq: number }>(this.path(`/sessions/${encodeURIComponent(id)}/messages`), { body: { text, ...(context ? { context } : {}) } }); }
  events(id: string, after: number, wait: number, signal?: AbortSignal) { return this.http.request<{ events: AssistantEvent[]; last_seq: number; busy: boolean }>(this.path(`/sessions/${encodeURIComponent(id)}/events?after=${after}&wait=${wait}`), { signal }); }
  cancel(id: string) { return this.http.request<{ cancelled: boolean }>(this.path(`/sessions/${encodeURIComponent(id)}/cancel`), { body: {} }); }
  async plan(id: string, version: number, signal?: AbortSignal): Promise<PlanVersion> {
    const plan = compiledParts(await this.http.request<PlanVersion>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}`), { signal }));
    return { ...plan, operationCopy: this.operationCopy(id) };
  }
  preview(id: string, version: number, signal?: AbortSignal) { return this.http.request<PlanPreview>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}/scenario`), { signal }); }
  async choose(id: string, version: number) { return this.describedSession(await this.http.request<Session>(this.path(`/sessions/${encodeURIComponent(id)}/plans/${version}/choose`), { body: {} })); }
  apply(id: string, version: number) { return this.http.request<ApplyRecord>(this.path(`/sessions/${encodeURIComponent(id)}/apply`), { body: { plan_version: version } }); }
  story(id: string, runId: string, signal?: AbortSignal, locale?: string) { return this.http.request<Story>(this.path(`/sessions/${encodeURIComponent(id)}/story/${encodeURIComponent(runId)}${locale ? `?locale=${encodeURIComponent(locale)}` : ''}`), { signal }); }
}
