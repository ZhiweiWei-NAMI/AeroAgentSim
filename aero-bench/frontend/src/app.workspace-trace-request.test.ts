import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn();
  focus = vi.fn(() => true);
  setFollow = vi.fn();
  refreshCursorLabels = vi.fn();
  destroy = vi.fn();
} }));

import { PublicTraceApp } from "./app";
import { RUN_ID, publicTrace } from "./testing/trace-v3-fixture";
import { WORKSPACE_TRACE_CATALOG_SCHEMA, type WorkspaceTraceEntry } from "./workspace-traces";

interface AppBoundary {
  trace: { run_id: string } | null;
}

const RELATIVE_PATH = "releases/fixture/public/public-trace.json";
const TRACE_URL = `/workspace-traces/${RELATIVE_PATH}`;

function catalogEntry(overrides: Partial<WorkspaceTraceEntry> = {}): WorkspaceTraceEntry {
  return {
    id: "releases.fixture.public-trace.json",
    relative_path: RELATIVE_PATH,
    url: TRACE_URL,
    schema_version: "aero-bench.public-trace/v3",
    run_id: RUN_ID,
    suite_id: "fixture.suite",
    case_id: "fixture.case",
    phase: "sealed",
    size_bytes: 12690,
    loadable: true,
    blocker: null,
    ...overrides,
  };
}

function catalog(...traces: WorkspaceTraceEntry[]): unknown {
  return { schema_version: WORKSPACE_TRACE_CATALOG_SCHEMA, traces };
}

let app: PublicTraceApp;
let state: AppBoundary;
let root: HTMLElement;

function navigate(query: string): void {
  window.history.replaceState({}, "", `/${query}`);
}

function sourceMessage(): HTMLElement | null {
  return root.querySelector<HTMLElement>(".source-message");
}

/** Stub fetch to serve one catalog and the entry's trace bytes over global fetch. */
function serve(options: { catalog: unknown; trace?: () => Response }): { traceRequests: () => number } {
  let traceRequests = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("workspace-traces/catalog.json")) {
      return new Response(JSON.stringify(options.catalog), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("public-trace.json")) {
      if (options.trace === undefined) {
        throw new Error(`no trace must be fetched, got ${url}`);
      }
      traceRequests += 1;
      return options.trace();
    }
    throw new Error(`unexpected fetch: ${url}`);
  }));
  return { traceRequests: () => traceRequests };
}

beforeEach(() => {
  document.body.replaceChildren();
  root = document.createElement("div");
  document.body.append(root);
  app = new PublicTraceApp(root, "replay");
  state = app as unknown as AppBoundary;
});

afterEach(() => {
  app.dispose();
  vi.unstubAllGlobals();
  navigate("");
});

describe("requested workspace trace handling", () => {
  it("reports a readable message when ?trace= matches no loadable entry", async () => {
    navigate("?view=replay&trace=missing");
    const served = serve({ catalog: catalog(catalogEntry()) });
    await app.start();
    const note = sourceMessage();
    expect(note?.hidden).toBe(false);
    expect(note?.textContent).toContain("请求的工作区轨迹不存在");
    expect(note?.textContent).toContain("missing");
    expect(state.trace).toBeNull();
    expect(served.traceRequests()).toBe(0);
  });

  it("does not auto-load another trace when the requested one is absent", async () => {
    navigate("?view=replay&trace=missing");
    const served = serve({ catalog: catalog(catalogEntry()) });
    await app.start();
    expect(state.trace).toBeNull();
    expect(served.traceRequests()).toBe(0);
    const select = root.querySelector<HTMLSelectElement>("#workspace-trace-select");
    expect(select?.value).toBe("");
  });

  it("treats a requested unloadable entry as absent", async () => {
    navigate(`?view=replay&trace=${RELATIVE_PATH}`);
    const served = serve({ catalog: catalog(catalogEntry({ loadable: false, blocker: "broken" })) });
    await app.start();
    const note = sourceMessage();
    expect(note?.hidden).toBe(false);
    expect(note?.textContent).toContain(RELATIVE_PATH);
    expect(state.trace).toBeNull();
    expect(served.traceRequests()).toBe(0);
  });

  it("auto-loads the requested entry when it is loadable", async () => {
    navigate(`?view=replay&trace=${RELATIVE_PATH}`);
    const served = serve({ catalog: catalog(catalogEntry()), trace: () => new Response(JSON.stringify(publicTrace())) });
    await app.start();
    expect(state.trace?.run_id).toBe(RUN_ID);
    expect(served.traceRequests()).toBe(1);
    // The jsdom scene pipeline may log its own asset note; what matters here is that the
    // requested-trace note never appears for a loadable request.
    expect(sourceMessage()?.textContent ?? "").not.toContain("请求的工作区轨迹不存在");
  });

  it("keeps auto-loading the single loadable entry when no trace is requested", async () => {
    navigate("?view=replay");
    const served = serve({ catalog: catalog(catalogEntry()), trace: () => new Response(JSON.stringify(publicTrace())) });
    await app.start();
    expect(state.trace?.run_id).toBe(RUN_ID);
    expect(served.traceRequests()).toBe(1);
  });

  it("keeps the empty-catalog note when no trace is requested", async () => {
    navigate("?view=replay");
    const served = serve({ catalog: catalog() });
    await app.start();
    const note = sourceMessage();
    expect(note?.hidden).toBe(false);
    expect(note?.textContent).toContain("工作区未扫描到 public-trace.json");
    expect(state.trace).toBeNull();
    expect(served.traceRequests()).toBe(0);
  });
});
