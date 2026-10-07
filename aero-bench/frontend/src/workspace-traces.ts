/** Same-origin catalog of public traces found in the AERO-BENCH worktree. */

export const WORKSPACE_TRACE_CATALOG = "./workspace-traces/catalog.json";
export const WORKSPACE_TRACE_CATALOG_SCHEMA = "aero-bench.viewer-workspace-traces/v1";
export const PUBLIC_TRACE_V3 = "aero-bench.public-trace/v3";

export interface WorkspaceTraceEntry {
  readonly id: string;
  readonly relative_path: string;
  readonly url: string;
  readonly schema_version: string | null;
  readonly run_id: string | null;
  readonly suite_id: string | null;
  readonly case_id: string | null;
  readonly phase: string | null;
  readonly size_bytes: number;
  readonly loadable: boolean;
  readonly blocker: string | null;
}

export interface WorkspaceTraceCatalog {
  readonly schema_version: string;
  readonly traces: readonly WorkspaceTraceEntry[];
}

export class WorkspaceTraceCatalogError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WorkspaceTraceCatalogError";
  }
}

function asEntry(value: unknown): WorkspaceTraceEntry | null {
  if (value === null || typeof value !== "object") {
    return null;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.id !== "string" || typeof record.relative_path !== "string" || typeof record.url !== "string") {
    return null;
  }
  if (typeof record.size_bytes !== "number" || !Number.isFinite(record.size_bytes) || typeof record.loadable !== "boolean") {
    return null;
  }
  return {
    id: record.id,
    relative_path: record.relative_path,
    url: record.url,
    schema_version: typeof record.schema_version === "string" ? record.schema_version : null,
    run_id: typeof record.run_id === "string" ? record.run_id : null,
    suite_id: typeof record.suite_id === "string" ? record.suite_id : null,
    case_id: typeof record.case_id === "string" ? record.case_id : null,
    phase: typeof record.phase === "string" ? record.phase : null,
    size_bytes: record.size_bytes,
    loadable: record.loadable,
    blocker: typeof record.blocker === "string" ? record.blocker : null,
  };
}

export function parseWorkspaceTraceCatalog(value: unknown): WorkspaceTraceCatalog {
  if (value === null || typeof value !== "object") {
    throw new WorkspaceTraceCatalogError("workspace trace catalog is not an object");
  }
  const record = value as Record<string, unknown>;
  if (record.schema_version !== WORKSPACE_TRACE_CATALOG_SCHEMA) {
    throw new WorkspaceTraceCatalogError("workspace trace catalog schema is not supported");
  }
  if (!Array.isArray(record.traces)) {
    throw new WorkspaceTraceCatalogError("workspace trace catalog traces are missing");
  }
  const traces: WorkspaceTraceEntry[] = [];
  for (const item of record.traces) {
    const entry = asEntry(item);
    if (entry === null) {
      throw new WorkspaceTraceCatalogError("workspace trace catalog entry is invalid");
    }
    traces.push(entry);
  }
  return { schema_version: WORKSPACE_TRACE_CATALOG_SCHEMA, traces };
}

export async function fetchWorkspaceTraceCatalog(signal?: AbortSignal): Promise<WorkspaceTraceCatalog> {
  const url = new URL(WORKSPACE_TRACE_CATALOG, window.location.href);
  let response: Response;
  try {
    response = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (error) {
    throw new WorkspaceTraceCatalogError(
      `workspace trace catalog request failed (${error instanceof Error ? error.message : "network error"})`,
    );
  }
  if (!response.ok) {
    throw new WorkspaceTraceCatalogError(`workspace trace catalog request failed (${response.status})`);
  }
  const contentType = response.headers.get("content-type") ?? "";
  const body = await response.text();
  if (contentType.includes("text/html") || body.trimStart().startsWith("<")) {
    throw new WorkspaceTraceCatalogError("workspace trace catalog returned HTML instead of JSON");
  }
  try {
    return parseWorkspaceTraceCatalog(JSON.parse(body) as unknown);
  } catch (error) {
    if (error instanceof WorkspaceTraceCatalogError) {
      throw error;
    }
    throw new WorkspaceTraceCatalogError("workspace trace catalog is not valid JSON");
  }
}

export function loadableWorkspaceTraces(catalog: WorkspaceTraceCatalog): readonly WorkspaceTraceEntry[] {
  return catalog.traces.filter((item) => item.loadable);
}
