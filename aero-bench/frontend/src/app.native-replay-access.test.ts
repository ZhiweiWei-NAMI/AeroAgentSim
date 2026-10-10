import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("./map", () => ({ PublicTraceMap: class {
  isAvailable = true;
  render = vi.fn(); focus = vi.fn(() => true); setFollow = vi.fn();
  refreshCursorLabels = vi.fn(); destroy = vi.fn();
} }));

import { PublicTraceApp, requestRegisteredReplaySource } from "./app";
import { ControlClient, ControlProtocolError } from "./run-control";
import { RunSession } from "./run-store";
import { indexedReplayFixture } from "./testing/indexed-replay-fixture";
import { OTHER_RUN_ID, RUN_ID } from "./testing/trace-v3-fixture";
import { currentLanguage, setLanguage } from "./i18n";
import type { PublicTrace } from "./trace";

const origin = "http://127.0.0.1:5393";
const bootstrap = { operatorToken: "a".repeat(64), csrfToken: "b".repeat(64) };
const access = { schema_version: "aero-bench.replay-access-response/v1", read_only: true,
  credentials: { schema_version: "aero-bench.run-access-credentials/v1", run_id: RUN_ID,
    operator_token: "c".repeat(64), csrf_token: "d".repeat(64) } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
let app: PublicTraceApp | undefined;
afterEach(() => { app?.dispose(); app = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("registered replay-access client", () => {
  it("uses the strict replay-access route and run-scoped authenticated read closures", async () => {
    const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).endsWith("replay-access")
      ? json(access) : new Response("public bytes"));
    const client = new ControlClient({ baseUrl: origin, fetch: fetcher });
    const source = await requestRegisteredReplaySource(client, bootstrap, RUN_ID);
    const first = fetcher.mock.calls[0]!;
    expect(first[0]).toBe(`${origin}/v1/runs/${RUN_ID}/replay-access`);
    const init = (fetcher.mock.calls as unknown as [RequestInfo | URL, RequestInit][])[0]![1];
    expect(init).toMatchObject({ method: "POST", redirect: "error",
      body: JSON.stringify({ schema_version: "aero-bench.replay-access-request/v1" }) });
    expect(new Headers(init.headers).get("Authorization")).toBe(`Bearer ${bootstrap.operatorToken}`);
    expect(new Headers(init.headers).get("X-Aero-Bench-CSRF")).toBe(bootstrap.csrfToken);
    const signal = new AbortController().signal;
    await source.manifest(signal); await source.trace(signal); await source.asset("e".repeat(64), signal);
    const calls = fetcher.mock.calls as unknown as [RequestInfo | URL, RequestInit][];
    expect(calls.slice(1).map(([url]) => String(url))).toEqual([
      `${origin}/v1/runs/${RUN_ID}/public/replay-manifest`, `${origin}/v1/runs/${RUN_ID}/public/trace`,
      `${origin}/v1/runs/${RUN_ID}/assets/${"e".repeat(64)}`,
    ]);
    for (const [, init] of calls.slice(1)) {
      expect(new Headers(init.headers).get("Authorization")).toBe(`Bearer ${access.credentials.operator_token}`);
      expect(init.signal).toBe(signal);
    }
    expect(JSON.stringify(source)).toBe(JSON.stringify({ runId: RUN_ID }));
  });

  it.each([
    { ...access, read_only: false },
    { ...access, credentials: { ...access.credentials, operator_token: "short" } },
    { ...access, unexpected: true },
  ])("rejects a response outside the generated read-only contract", async value => {
    const client = new ControlClient({ baseUrl: origin, fetch: vi.fn(async () => json(value)) });
    await expect(requestRegisteredReplaySource(client, bootstrap, RUN_ID)).rejects.toThrow(ControlProtocolError);
  });

  it("rejects credentials bound to another run", async () => {
    const client = new ControlClient({ baseUrl: origin, fetch: vi.fn(async () => json({ ...access,
      credentials: { ...access.credentials, run_id: OTHER_RUN_ID } })) });
    await expect(requestRegisteredReplaySource(client, bootstrap, RUN_ID))
      .rejects.toThrow("replay access credentials belong to another run");
  });

  it("preserves an authoritative admission failure", async () => {
    const client = new ControlClient({ baseUrl: origin, fetch: vi.fn(async () => json({
      schema_version: "aero-bench.control-error-response/v1", error: { code: "replay.invalid", detail: "no verifier result" },
    }, 409)) });
    await expect(requestRegisteredReplaySource(client, bootstrap, RUN_ID))
      .rejects.toMatchObject({ status: 409, code: "replay.invalid", detail: "no verifier result" });
  });

  it("rejects invalid JSON and malformed input without leaking credentials", async () => {
    const fetcher = vi.fn(async () => new Response("not JSON"));
    const client = new ControlClient({ baseUrl: origin, fetch: fetcher });
    await expect(requestRegisteredReplaySource(client, bootstrap, RUN_ID)).rejects.toThrow("invalid JSON");
    fetcher.mockClear();
    await expect(requestRegisteredReplaySource(client, bootstrap, "../run")).rejects.toThrow("run_id");
    await expect(requestRegisteredReplaySource(client, { ...bootstrap, csrfToken: "short" }, RUN_ID))
      .rejects.toThrow("bootstrap CSRF token");
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects cancellation even if the metadata fetch ignores its signal", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
    const client = new ControlClient({ baseUrl: origin, fetch: fetcher });
    const controller = new AbortController();
    const pending = requestRegisteredReplaySource(client, bootstrap, RUN_ID, controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort(); finish(json(access)); await rejected;
    fetcher.mockClear();
    await expect(requestRegisteredReplaySource(client, bootstrap, RUN_ID, controller.signal))
      .rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).not.toHaveBeenCalled();
  });
});

interface AppBoundary {
  controlClient: ControlClient | null;
  bootstrap: typeof bootstrap | null;
  session: RunSession | null;
  replacementApp: PublicTraceApp | null;
  trace: PublicTrace | null;
  sealedReplayStatus: string;
  disconnectControlService(): void;
}

function liveApp(fetcher: typeof fetch) {
  document.body.replaceChildren();
  const root = document.createElement("div"); document.body.append(root);
  app = new PublicTraceApp(root, "live");
  const boundary = app as unknown as AppBoundary;
  const client = new ControlClient({ baseUrl: origin, fetch: fetcher });
  const session = new RunSession(client);
  // A unit-only catalog state; no backend execution, transitions or formal evidence.
  Object.assign(session.currentState, { selectedRunId: RUN_ID });
  boundary.controlClient = client; boundary.bootstrap = bootstrap; boundary.session = session;
  return { root, boundary, session };
}

describe("App registered replay entry", () => {
  it("updates the read-only entry label before connecting a Control service", () => {
    const previous = currentLanguage();
    const root = document.createElement("div"); document.body.append(root);
    app = new PublicTraceApp(root, "live");
    const button = root.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!;
    try {
      setLanguage("en"); expect(button.textContent).toBe("Open sealed replay");
      setLanguage("zh"); expect(button.textContent).toBe("打开已封存回放");
      expect(button.disabled).toBe(true);
    } finally { setLanguage(previous); }
  });

  it("opens an indexed sealed fixture without start, status, controls or event-stream requests", async () => {
    const fixture = indexedReplayFixture(257);
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/replay-access")) return json(access);
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${access.credentials.operator_token}`);
      if (url.pathname.endsWith("/public/trace")) return new Response(fixture.files.get("public-trace.json")!);
      if (url.pathname.endsWith("/public/replay-manifest")) return new Response(fixture.files.get("replay-manifest.json")!);
      const digest = url.pathname.split("/").at(-1)!;
      const bytes = fixture.files.get(`artifacts/${digest}`) ?? fixture.files.get(`assets/${digest}`);
      if (bytes === undefined) throw new Error(`unexpected unit route ${url.pathname}`);
      return new Response(bytes);
    });
    const { root, boundary } = liveApp(fetcher);
    expect(root.querySelectorAll(".control-buttons .action-button")).toHaveLength(5);
    expect(root.querySelector('.control-buttons [data-role="open-registered-replay"]')).toBeNull();
    const storage = vi.spyOn(Storage.prototype, "setItem");
    await app!.replayRegisteredRun();
    const replay = boundary.replacementApp as unknown as AppBoundary;
    expect(replay.sealedReplayStatus).toBe("ready");
    expect(replay.trace?.run_id).toBe(RUN_ID);
    expect(replay.trace?.scene_states).toHaveLength(257);
    expect(replay.session).toBeNull();
    expect(boundary.bootstrap).toBeNull();
    expect(storage).not.toHaveBeenCalled();
    for (const token of [bootstrap.operatorToken, bootstrap.csrfToken,
      access.credentials.operator_token, access.credentials.csrf_token]) expect(root.textContent).not.toContain(token);
    const calls = fetcher.mock.calls;
    expect(calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(calls.every(([input]) => /\/(?:replay-access|public\/(?:trace|replay-manifest)|assets\/[a-f0-9]{64})$/.test(String(input))))
      .toBe(true);
  });

  it("cannot resurrect a replay after disconnect while access is pending", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
    const { boundary } = liveApp(fetcher);
    const pending = app!.replayRegisteredRun();
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    boundary.disconnectControlService(); finish(json(access)); await rejected;
    expect(boundary.replacementApp).toBeNull();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("clears the disconnected service's catalog and disables its run controls", () => {
    const { root, boundary, session } = liveApp(vi.fn());
    const catalog = { suite_id: "suite.unit", suite_sha256: "f".repeat(64),
      runs: [{ run_id: RUN_ID, case_id: "case.unit", seed: 1, launch_site_id: "launch.unit" }] };
    (boundary as unknown as { renderCatalog(state: unknown): void }).renderCatalog({ ...session.currentState, catalog });
    expect(root.querySelector(`.run-select option[value="${RUN_ID}"]`)).not.toBeNull();
    boundary.disconnectControlService();
    expect(root.querySelector(".run-select")).toBeNull();
    expect(root.querySelector(".catalog-body")?.textContent).toBe("");
    expect(root.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!.disabled).toBe(true);
    const runControls = [...root.querySelectorAll<HTMLButtonElement>(".control-panel .action-button")]
      .filter(button => /启动运行|暂停|恢复|单步|停止|Start|Pause|Resume|Step|Stop/.test(button.textContent ?? ""));
    expect(runControls).toHaveLength(5);
    expect(runControls.every(button => button.disabled)).toBe(true);
  });

  it("does not open a stale run after the catalog selection changes", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
    const { boundary, session } = liveApp(fetcher);
    const pending = app!.replayRegisteredRun();
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    Object.assign(session.currentState, { selectedRunId: OTHER_RUN_ID });
    finish(json(access)); await rejected;
    expect(boundary.replacementApp).toBeNull();
    expect(boundary.bootstrap).toBe(bootstrap);
    expect(fetcher).toHaveBeenCalledOnce();
    expect(document.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!.disabled).toBe(false);
  });

  it("aborts pending admission when the App is disposed", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { finish = resolve; }));
    const { boundary } = liveApp(fetcher);
    const pending = app!.replayRegisteredRun();
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    app!.dispose(); finish(json(access)); await rejected;
    expect(boundary.replacementApp).toBeNull();
    expect(boundary.bootstrap).toBeNull();
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("leaves the catalog view intact when admission rejects the run", async () => {
    const { boundary } = liveApp(vi.fn(async () => json({ schema_version: "aero-bench.control-error-response/v1",
      error: { code: "replay.invalid", detail: "no verifier result" } }, 409)));
    await expect(app!.replayRegisteredRun()).rejects.toMatchObject({ code: "replay.invalid" });
    expect(boundary.replacementApp).toBeNull();
    expect(boundary.bootstrap).toBe(bootstrap);
    expect(document.querySelector<HTMLButtonElement>('[data-role="open-registered-replay"]')!.disabled).toBe(false);
  });
});
