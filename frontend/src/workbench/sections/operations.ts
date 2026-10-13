import { useCallback, useSyncExternalStore } from 'react';
import { ApiError, type Project, type WorkbenchApi } from '../api';

/** One edit in the vocabulary the assistant compiles; forms send the same operations (see GET /v1/studio/operations). */
export interface Operation { id: string; op: string; params: Record<string, unknown> }
export interface DiffField { name: string; before?: unknown; after?: unknown; label?: string; label_zh?: string; before_text?: string; after_text?: string }
export interface DiffRow { kind?: string; kind_label?: string; kind_label_zh?: string; id?: string; label?: string; label_zh?: string; fields?: DiffField[] }
export interface OperationPreview { diff: { added: DiffRow[]; changed: DiffRow[]; removed: DiffRow[] }; notes: Array<string | { en?: string; zh?: string; text?: string }> }
export interface OperationSpec { op: string; topic?: string; label?: string; label_zh?: string; schema?: Record<string, unknown>; example?: Record<string, unknown> }
export interface Capabilities { lowalt: boolean; region: boolean; network: boolean; city: boolean }
/** A rejected operation names the operation and parameter so the form can mark that field. */
export interface OperationProblem { operation: string | null; field?: string; message: string }

const endpoint = (id: string) => `/v1/studio/workspaces/${encodeURIComponent(id)}/operations`;
export interface WorkspaceOperationsState { applying: boolean; project?: Project }
const idle: WorkspaceOperationsState = { applying: false };
const workspaceStates = new Map<string, WorkspaceOperationsState>();
const workspaceListeners = new Map<string, Set<() => void>>();
type WorkspaceBase = Pick<Project, 'id' | 'revision'>;
// ConfigPanel's saveDraft checks/acquires the gate synchronously, before its first await.
// Permit that one save inside a larger mutation without granting async callers reentrancy.
let preparing: { id: string; claimed: boolean } | undefined;

export const getOperationsState = (workspaceId: string): WorkspaceOperationsState => workspaceStates.get(workspaceId) ?? idle;
export function subscribeOperations(workspaceId: string, listener: () => void): () => void {
  const listeners = workspaceListeners.get(workspaceId) ?? new Set<() => void>();
  listeners.add(listener); workspaceListeners.set(workspaceId, listeners);
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      workspaceListeners.delete(workspaceId);
      if (!getOperationsState(workspaceId).applying) workspaceStates.delete(workspaceId);
    }
  };
}
function publishOperations(workspaceId: string, state: WorkspaceOperationsState) {
  workspaceStates.set(workspaceId, state);
  workspaceListeners.get(workspaceId)?.forEach(listener => listener());
}
/** A mutation response bridges the interval before the parent renders the new project. */
export function currentOperationsProject(project: Project): Project {
  const current = getOperationsState(project.id).project;
  return current && current.revision > project.revision ? current : project;
}
export function useWorkspaceOperations(project: Project) {
  const subscribe = useCallback((listener: () => void) => subscribeOperations(project.id, listener), [project.id]);
  const snapshot = useSyncExternalStore(subscribe, () => getOperationsState(project.id), () => idle);
  return { ...snapshot, project: currentOperationsProject(project) };
}
export class OperationStateError extends Error {
  constructor(readonly reason: 'applying' | 'stale') {
    super(reason === 'applying' ? 'Applying another change… / 正在应用另一项变更…'
      : 'The draft was updated. Preview again before applying. / 草稿已更新，请重新预览后再应用。');
  }
}
export function assertOperationsReady(project: WorkspaceBase) {
  const state = getOperationsState(project.id);
  if (state.applying && !(preparing?.id === project.id && !preparing.claimed)) throw new OperationStateError('applying');
  if (state.project && state.project.revision > project.revision) throw new OperationStateError('stale');
}

/** One apply per workspace, including the authoritative read after a mutation.
 * Never queue an old confirmation and apply it against a different revision.
 * Run settings and raw draft saves use the same gate as C11 operations.
 */
export async function mutateWorkspace(project: WorkspaceBase, apply: (base: WorkspaceBase) => Promise<Project>, prepare?: () => Promise<Project | undefined>): Promise<Project> {
  assertOperationsReady(project);
  const nested = getOperationsState(project.id).applying;
  if (nested) preparing!.claimed = true;
  else publishOperations(project.id, { ...getOperationsState(project.id), applying: true });
  try {
    let base = project;
    if (prepare) {
      const previous = preparing;
      let pending: Promise<Project | undefined>;
      preparing = { id: project.id, claimed: false };
      try { pending = prepare(); } finally { preparing = previous; }
      const saved = await pending;
      if (!saved) throw Error('The draft could not be saved. Fix the highlighted inputs before applying. / 草稿保存失败，请修正标记的输入后再应用。');
      base = saved;
      publishOperations(project.id, { applying: true, project: saved });
    }
    const updated = await apply(base);
    publishOperations(project.id, { applying: true, project: updated });
    return updated;
  } finally {
    if (!nested) {
      publishOperations(project.id, { ...getOperationsState(project.id), applying: false });
      if (!workspaceListeners.has(project.id)) workspaceStates.delete(project.id);
    }
  }
}
let serial = 0;
/** Operation ids only identify edits within one request. */
export const operation = (op: string, params: Record<string, unknown> = {}): Operation => ({ id: `form-${++serial}`, op, params });

/** Canonical research inputs: object key order is immaterial; array order is meaningful. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
}

/** Transport ids identify requests, never the inputs reviewed by the researcher. */
export function operationFreshnessKey(operations: Operation[], revision: number): string {
  return canonicalJson({ revision, operations: operations.map(({ op, params }) => ({ op, params })) });
}

/** The operations contract also returns bilingual strings. Split only the language boundary,
 * preserving slashes in units, identifiers and paths. */
export function operationText(text: string, zh: boolean): string {
  const boundary = /\s+\/\s+(?=[\u3400-\u9fff])/;
  const match = boundary.exec(text);
  if (!match) return text;
  return zh ? text.slice(match.index + match[0].length).trim() : text.slice(0, match.index).trim();
}

export function capabilities(project: Project): Capabilities | undefined {
  return (project as Project & { capabilities?: Capabilities }).capabilities;
}

export function previewOperations(api: WorkbenchApi, project: Project, operations: Operation[]) {
  assertOperationsReady(project);
  return api.request<OperationPreview>(endpoint(project.id), { body: { operations, revision: project.revision, dry_run: true } });
}

/** Applies to the server draft (never saves a version or starts a run) and returns the full updated project. */
export async function applyOperations(api: WorkbenchApi, project: Project, operations: Operation[]) {
  return mutateWorkspace(project, async () => {
    await api.request<unknown>(endpoint(project.id), { body: { operations, revision: project.revision } });
    return api.project(project.id);
  });
}

export function operationCatalogue(api: WorkbenchApi, signal?: AbortSignal) {
  return api.request<OperationSpec[]>('/v1/studio/operations', { signal });
}

/** Reads the structured 422 detail; other failures keep their message. */
export function operationProblem(problem: unknown): OperationProblem {
  if (problem instanceof ApiError) {
    try {
      const detail = JSON.parse(problem.detail) as Partial<OperationProblem>;
      if (detail && typeof detail.message === 'string') return { operation: detail.operation ?? null, field: detail.field, message: detail.message };
    } catch { /* Plain-text detail. */ }
    return { operation: null, message: problem.status === 409 ? 'The draft changed since this preview. Reload the draft and preview again. / 草稿在预览后已变化，请重新加载草稿并再次预览。' : problem.detail };
  }
  return { operation: null, message: String(problem) };
}
