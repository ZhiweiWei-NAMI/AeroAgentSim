import { ApiError, type Project, type WorkbenchApi } from '../api';

/** One edit in the vocabulary the assistant compiles; forms send the same operations (see GET /v1/studio/operations). */
export interface Operation { id: string; op: string; params: Record<string, unknown> }
export interface DiffRow { kind?: string; id?: string; label?: string; label_zh?: string; fields?: Array<{ name: string; before?: unknown; after?: unknown }> }
export interface OperationPreview { diff: { added: DiffRow[]; changed: DiffRow[]; removed: DiffRow[] }; notes: Array<string | { en?: string; zh?: string; text?: string }> }
export interface OperationSpec { op: string; topic?: string; label?: string; label_zh?: string; schema?: Record<string, unknown>; example?: Record<string, unknown> }
export interface Capabilities { lowalt: boolean; region: boolean; network: boolean; city: boolean }
/** A rejected operation names the operation and parameter so the form can mark that field. */
export interface OperationProblem { operation: string | null; field?: string; message: string }

const endpoint = (id: string) => `/v1/studio/workspaces/${encodeURIComponent(id)}/operations`;
let serial = 0;
/** Operation ids only identify edits within one request. */
export const operation = (op: string, params: Record<string, unknown> = {}): Operation => ({ id: `form-${++serial}`, op, params });

export function capabilities(project: Project): Capabilities | undefined {
  return (project as Project & { capabilities?: Capabilities }).capabilities;
}

export function previewOperations(api: WorkbenchApi, project: Project, operations: Operation[]) {
  return api.request<OperationPreview>(endpoint(project.id), { body: { operations, revision: project.revision, dry_run: true } });
}

/** Applies to the server draft (never saves a version or starts a run) and returns the full updated project. */
export async function applyOperations(api: WorkbenchApi, project: Project, operations: Operation[]) {
  await api.request<unknown>(endpoint(project.id), { body: { operations, revision: project.revision } });
  return api.project(project.id);
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
    return { operation: null, message: problem.status === 409 ? 'The draft changed elsewhere; reload it before applying. / 草稿已在别处修改，请重新加载后再应用。' : problem.detail };
  }
  return { operation: null, message: String(problem) };
}
