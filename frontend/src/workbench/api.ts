import { parseAuthoringJson, stringifyLossless } from '../feeds/lossless-json';
import { requestJson, requestText, TransportError } from '../feeds/transport';
import type { Workspace } from '../studio/api';
import { TemplateApi, type KnobValue } from '../newcomers/api';
import type { RunInfo } from '../feeds/http';

/** A server answer that is not success. `status` 501/404 means the capability is absent. */
export class ApiError extends TransportError {}

/** Keep the workbench's long-standing error type while preserving the transport classification. */
function classified(error: unknown): never {
  if (error instanceof TransportError) throw new ApiError(error.status, error.detail);
  throw error;
}

export type SectionId = 'space' | 'objects' | 'relations' | 'tasks' | 'components' | 'algorithms' | 'run';
export interface ValidationIssue { source: string; path: string; message: string; section?: SectionId; entity_id?: string; field?: string; binding?: string }
export interface Validation { valid: boolean; revision?: number; errors: string[]; issues?: ValidationIssue[]; scope?: string }
export interface VersionInfo { version: number; revision: number; digest: string; created_at: string; note?: string; validation: Validation; template?: { id: string; knobs: Record<string, KnobValue> }; derived_from?: DerivedFrom }
export interface DerivedFrom { run_id: string; workspace_id?: string; version?: number }
export interface Project extends Omit<Workspace, 'validation'> {
  revision: number; updated_at: string; validation?: Validation;
  template?: { id: string; knobs: Record<string, KnobValue> };
  derived_from?: DerivedFrom; latest_version: VersionInfo | null; dirty: boolean;
}
export interface RunRecord extends RunInfo { workspace_version?: number; workspace_digest?: string; derived_from?: DerivedFrom; title?: string; title_zh?: string }

export class WorkbenchApi {
  constructor(readonly base: string) {}
  async request<T>(path: string, init?: { method?: string; body?: unknown; signal?: AbortSignal }): Promise<T> {
    const requestInit: RequestInit = {
      method: init?.method ?? (init?.body === undefined ? 'GET' : 'POST'), signal: init?.signal,
      ...(init?.body === undefined && init?.method !== 'POST' ? {} : { headers: { 'Content-Type': 'application/json' }, body: stringifyLossless(init?.body ?? {}) }),
    };
    const result = await requestJson(this.base, path, requestInit, parseAuthoringJson)
      .then(value => value as T, error => classified(error));
    // These writes return legacy draft records as well as summaries. Only GET
    // owns the displayed version, validation, capabilities and authored scene.
    const workspace = /^\/v1\/studio\/workspaces\/([^/?]+)\/(knobs|import|place|remove)$/.exec(path);
    const body = init?.body as { dry_run?: boolean } | undefined;
    if (workspace && (init?.method ?? (init?.body === undefined ? 'GET' : 'POST')) !== 'GET' && body?.dry_run !== true) {
      return await this.project(decodeURIComponent(workspace[1]), init?.signal) as T;
    }
    return result;
  }
  // Projects (studio workspaces) and their versions.
  projects(signal?: AbortSignal) { return this.request<Project[]>('/v1/studio/workspaces', { signal }); }
  createProject(name: string) { return this.request<{ id: string }>('/v1/studio/workspaces', { body: { name } }); }
  project(id: string, signal?: AbortSignal) { return this.request<Project>(`/v1/studio/workspaces/${encodeURIComponent(id)}`, { signal }); }
  async save(id: string, scenario: Record<string, unknown>, revision: number, name?: string) {
    await this.request<unknown>(`/v1/studio/workspaces/${encodeURIComponent(id)}`, { body: { scenario, revision, ...(name ? { name } : {}) } });
    return this.project(id);
  }
  async rename(id: string, name: string, revision: number) {
    await this.request<unknown>(`/v1/studio/workspaces/${encodeURIComponent(id)}`, { body: { name, revision } });
    return this.project(id);
  }
  validate(id: string, revision: number) { return this.request<Validation>(`/v1/studio/workspaces/${encodeURIComponent(id)}/validate`, { body: { revision } }); }
  knobs(id: string, knobs: Record<string, KnobValue>, revision: number) { return this.request<Project>(`/v1/studio/workspaces/${encodeURIComponent(id)}/knobs`, { body: { knobs, revision } }); }
  versions(id: string, signal?: AbortSignal) { return this.request<VersionInfo[]>(`/v1/studio/workspaces/${encodeURIComponent(id)}/versions`, { signal }); }
  saveVersion(id: string, revision: number, note?: string) { return this.request<VersionInfo>(`/v1/studio/workspaces/${encodeURIComponent(id)}/versions`, { body: { revision, ...(note ? { note } : {}) } }); }
  async exportYaml(id: string) {
    try { return await requestText(this.base, `/v1/studio/workspaces/${encodeURIComponent(id)}/export`); }
    catch (error) { return classified(error); }
  }
  importYaml(id: string, text: string, revision: number) { return this.request<Project>(`/v1/studio/workspaces/${encodeURIComponent(id)}/import`, { body: { yaml: text, revision } }); }
  // Templates.
  templates(signal?: AbortSignal) { return new TemplateApi(this.base).templates(signal); }
  guide(id: string, signal?: AbortSignal) { return this.request<{ id: string; markdown: string }>(`/v1/templates/${encodeURIComponent(id)}/guide`, { signal }); }
  createFromTemplate(id: string, knobs: Record<string, KnobValue> = {}) { return this.request<{ workspace_id: string }>(`/v1/templates/${encodeURIComponent(id)}/workspace`, { body: { knobs } }); }
  // Runs.
  runs(signal?: AbortSignal) { return this.request<RunRecord[]>('/v1/runs?include_kpis=false', { signal }); }
  initialize(workspace: string, version: number) { return this.request<RunRecord>('/v1/runs', { body: { studio_workspace: workspace, version, autostart: false } }); }
  control(runId: string, action: 'start' | 'pause' | 'resume' | 'stop') { return this.request<{ id: string; requested: string; status: string }>(`/v1/runs/${encodeURIComponent(runId)}/${action}`, { method: 'POST', body: {} }); }
  speed(runId: string, signal?: AbortSignal) { return this.request<{ id: string; factor: number | null }>(`/v1/runs/${encodeURIComponent(runId)}/speed`, { signal }); }
  setSpeed(runId: string, factor: number | null) { return this.request<{ id: string; factor: number | null }>(`/v1/runs/${encodeURIComponent(runId)}/speed`, { body: { factor } }); }
  derive(runId: string, name?: string) { return this.request<{ workspace_id: string; derived_from: DerivedFrom }>(`/v1/runs/${encodeURIComponent(runId)}/derive-workspace`, { body: name ? { name } : {} }); }
}

/** Run statuses after which nothing can change. */
export const TERMINAL = new Set(['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout']);

/** Preserve record consumers when polling returned the same authoritative data. */
export function sameRunRecords(previous: RunRecord[] | undefined, next: RunRecord[]): boolean {
  return previous !== undefined && JSON.stringify(previous) === JSON.stringify(next);
}
