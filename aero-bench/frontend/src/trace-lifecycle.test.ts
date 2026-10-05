import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_PUBLIC_TRACE_BYTES, PublicTraceStore } from "./trace";
import { publicTrace } from "./testing/trace-v3-fixture";

afterEach(() => vi.unstubAllGlobals());

describe("raw public-trace candidate lifecycle", () => {
  it("rejects a candidate if the caller aborts between hashing and acceptance", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(publicTrace())));
    const controller = new AbortController();
    const store = new PublicTraceStore();
    const candidate = await store.fetchRelative("/public-trace.json", controller.signal);
    controller.abort();
    expect(() => store.acceptFetched(candidate)).toThrow("not a pending");
    expect(store.value).toBeNull();
    store.dispose();
  });

  it("clears pending authority and freezes the entire hashed payload", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(publicTrace())));
    const store = new PublicTraceStore();
    const candidate = await store.fetchRelative("/public-trace.json");
    expect(candidate.sourcePayloadSizeBytes).toBeGreaterThan(0);
    expect(Object.isFrozen(candidate.trace.scene_states[0]!.at)).toBe(true);
    store.clear();
    expect(() => store.acceptFetched(candidate)).toThrow("not a pending");
    store.dispose();
  });

  it("rejects an oversized declared trace before buffering its body", async () => {
    vi.stubGlobal("fetch", async () => new Response("{}", { headers: { "Content-Length": String(MAX_PUBLIC_TRACE_BYTES + 1) } }));
    const store = new PublicTraceStore();
    await expect(store.fetchRelative("/public-trace.json")).rejects.toThrow("byte bound");
    expect(store.value).toBeNull();
    store.dispose();
  });
});
