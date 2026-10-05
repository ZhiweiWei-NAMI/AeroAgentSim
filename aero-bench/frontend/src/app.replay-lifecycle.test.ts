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
import { indexedReplayFixture } from "./testing/indexed-replay-fixture";
import { OTHER_RUN_ID, RUN_ID, publicTrace } from "./testing/trace-v3-fixture";
import type { PublicTraceStore, PublicTrace, SceneState } from "./trace";
import type { ReplayState } from "./state/replay";

interface AppBoundary {
  trace: PublicTrace | null;
  store: PublicTraceStore;
  replay: ReplayState;
  sealedReplayStatus: string;
  selection: { select(target: { kind: "entity"; id: string }): void; get(): unknown };
  currentSceneState(): SceneState | null;
}
const endpoint = "/run/public/public-trace.json";
let app: PublicTraceApp;
let state: AppBoundary;
let root: HTMLElement;

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
});

describe("indexed replay endpoint lifecycle", () => {
  it("loads a Control run only after its authenticated public byte closure is verified", async () => {
    const fixture = indexedReplayFixture(257);
    const source = {
      runId: RUN_ID,
      trace: vi.fn(async () => new Response(fixture.files.get("public-trace.json")!)),
      manifest: vi.fn(async () => new Response(fixture.files.get("replay-manifest.json")!)),
      asset: vi.fn(async (digest: string) => {
        const bytes = fixture.files.get(`artifacts/${digest}`) ?? fixture.files.get(`assets/${digest}`);
        return bytes === undefined ? new Response(null, { status: 404 }) : new Response(bytes);
      }),
    };
    const network = vi.fn(() => { throw new Error("Control replay must use authenticated closures"); });
    vi.stubGlobal("fetch", network);
    await app.loadControlReplay(source);
    expect(state.sealedReplayStatus).toBe("ready");
    expect(state.trace?.run_id).toBe(source.runId);
    expect(state.trace?.scene_states).toHaveLength(257);
    state.replay.goToTick(257);
    expect(state.currentSceneState()?.at.tick).toBe(257);
    expect(source.asset).toHaveBeenCalled();
    expect(network).not.toHaveBeenCalled();
  });

  it("rejects a Control manifest with a different run or trace before publishing anything", async () => {
    const fixture = indexedReplayFixture();
    const listener = vi.fn();
    state.store.subscribe(listener);
    for (const changed of [{ run_id: OTHER_RUN_ID }, { trace_sha256: "f".repeat(64) }]) {
      await expect(app.loadControlReplay({
        runId: RUN_ID,
        trace: async () => new Response(fixture.files.get("public-trace.json")!),
        manifest: async () => new Response(JSON.stringify({ ...fixture.manifest, ...changed })),
        asset: async () => { throw new Error("must reject before loading assets"); },
      })).rejects.toThrow("does not bind");
      expect(state.trace).toBeNull();
      expect(state.store.value).toBeNull();
    }
    expect(listener).not.toHaveBeenCalled();
  });
  it("keeps exact verified frames present on consecutive playback ticks and shard boundaries", async () => {
    const fixture = indexedReplayFixture(257);
    const fetch = vi.fn(fixture.fetcher);
    vi.stubGlobal("fetch", fetch);
    await app.loadEndpoint(endpoint);
    fetch.mockClear();
    for (const tick of [2, 3, 255, 256, 257, 1]) {
      state.replay.goToTick(tick);
      expect(state.currentSceneState()?.at.tick).toBe(tick);
      expect(state.currentSceneState()?.scene_state_digest).toBe(fixture.states[tick - 1]!.scene_state_digest);
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects indexed documents before notifying or rendering their contents", async () => {
    const fixture = indexedReplayFixture();
    const listener = vi.fn();
    state.store.subscribe(listener);
    await expect(app.loadDocument(fixture.trace)).rejects.toThrow("sealed replay endpoint");
    expect(listener).not.toHaveBeenCalled();
    expect(state.trace).toBeNull();
    expect(state.store.value).toBeNull();
    expect(state.sealedReplayStatus).toBe("failed");
    expect(root.textContent).toContain("FAILED");
    expect(root.textContent).not.toContain("fixture.uav");
  });

  it("publishes nothing until the final shard is verified", async () => {
    const fixture = indexedReplayFixture(257);
    let release!: (value: Response) => void;
    const shard = fixture.index.shards[1]!.relative_path;
    vi.stubGlobal("fetch", vi.fn(async (url: Parameters<typeof fetch>[0], init?: RequestInit) => {
      if (String(url).endsWith(shard)) return new Promise<Response>(resolve => { release = resolve; });
      return fixture.fetcher(url, init);
    }));
    const listener = vi.fn();
    state.store.subscribe(() => listener(state.sealedReplayStatus));
    const pending = app.loadEndpoint(endpoint);
    await vi.waitFor(() => expect(release).toBeTypeOf("function"));
    expect(state.sealedReplayStatus).toBe("loading");
    expect(root.querySelector<HTMLElement>(".loading-progress")?.dataset.stage).toBe("index");
    expect(state.trace).toBeNull();
    expect(state.replay.recorded()).toEqual([]);
    expect(listener).not.toHaveBeenCalled();
    expect(root.textContent).not.toContain("fixture.uav");
    release(new Response(fixture.files.get(shard)!));
    await pending;
    expect(listener).toHaveBeenCalledExactlyOnceWith("ready");
    expect(state.trace?.scene_states).toHaveLength(257);
  });

  it("cannot commit a stale endpoint even when fetch ignores abort", async () => {
    const fixture = indexedReplayFixture();
    let release!: (value: Response) => void;
    let signal: AbortSignal | undefined;
    vi.stubGlobal("fetch", vi.fn((_url, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>(resolve => { release = resolve; });
    }));
    const pending = app.loadEndpoint(endpoint);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await app.loadDocument(publicTrace({ run_id: OTHER_RUN_ID }));
    expect(signal?.aborted).toBe(true);
    release(new Response(fixture.files.get("public-trace.json")!));
    await rejected;
    expect(state.trace?.run_id).toBe(OTHER_RUN_ID);
    expect(state.sealedReplayStatus).toBe("ready");
    expect(root.querySelector<HTMLElement>(".loading-progress")?.hidden).toBe(true);
  });

  it("does not carry a selected entity into a newly loaded source", async () => {
    await app.loadDocument(publicTrace());
    state.selection.select({ kind: "entity", id: "fixture.uav" });
    expect(state.selection.get()).not.toBeNull();
    await app.loadDocument(publicTrace({ run_id: OTHER_RUN_ID }));
    expect(state.trace?.run_id).toBe(OTHER_RUN_ID);
    expect(state.selection.get()).toBeNull();
  });

  it("clears both old UI and store while a new endpoint is loading and after cancellation", async () => {
    await app.loadDocument(publicTrace());
    let release!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { release = resolve; })));
    const controller = new AbortController();
    const pending = app.loadEndpoint(endpoint, controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(state.trace).toBeNull();
    expect(state.store.value).toBeNull();
    expect(root.textContent).not.toContain("fixture.uav");
    controller.abort();
    release(new Response("{}"));
    await rejected;
    expect(state.sealedReplayStatus).toBe("failed");
    expect(state.replay.isPlaying()).toBe(false);
    expect(root.querySelector<HTMLElement>(".loading-progress")?.dataset.stage).toBe("failed");
    expect(state.store.value).toBeNull();
  });

  it("disposal prevents pending endpoint completion from resurrecting the UI", async () => {
    let release!: (value: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { release = resolve; })));
    const pending = app.loadEndpoint(endpoint);
    const rejected = expect(pending).rejects.toThrow();
    app.dispose();
    release(new Response("{}"));
    await rejected;
    expect(state.trace).toBeNull();
    expect(state.store.value).toBeNull();
    expect(state.replay.isPlaying()).toBe(false);
  });

  it("keeps its authenticated snapshot readable when the source endpoint later disappears", async () => {
    const fixture = indexedReplayFixture(257);
    const fetch = vi.fn(fixture.fetcher);
    vi.stubGlobal("fetch", fetch);
    await app.loadEndpoint(endpoint);
    fixture.files.delete(fixture.index.shards[0]!.relative_path);
    fetch.mockClear();
    state.replay.goToTick(2);
    expect(state.sealedReplayStatus).toBe("ready");
    expect(state.currentSceneState()?.scene_state_digest).toBe(fixture.states[1]!.scene_state_digest);
    expect(fetch).not.toHaveBeenCalled();
  });
});
