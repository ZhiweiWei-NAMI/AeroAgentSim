import { afterEach, describe, expect, it, vi } from "vitest";
import { ControlAssetQueue } from "./control-asset-queue";

afterEach(() => vi.useRealTimers());

describe("run-scoped Control assets", () => {
  it("shares inflight bytes and returns independent readable cached responses", async () => {
    const request = vi.fn(async () => new Response("same GLB bytes"));
    const queue = new ControlAssetQueue(request, 0);
    const [a, b] = await Promise.all([queue.fetch("same"), queue.fetch("same")]);
    expect(await a.text()).toBe("same GLB bytes"); expect(await b.text()).toBe("same GLB bytes");
    expect(await (await queue.fetch("same")).text()).toBe("same GLB bytes");
    expect(request).toHaveBeenCalledTimes(1);
    expect(queue.diagnostics).toMatchObject({ requests: 1, inflightJoins: 1, cacheHits: 1 });
  });
  it("paces distinct request starts even when four workers enqueue at once", async () => {
    vi.useFakeTimers();
    const times: number[] = [];
    const queue = new ControlAssetQueue(async () => { times.push(Date.now()); return new Response("GLB"); });
    const pending = Promise.all(["a", "b", "c", "d"].map(id => queue.fetch(id)));
    await vi.advanceTimersByTimeAsync(0); expect(times).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(599); expect(times).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1201); await pending;
    expect(times.slice(1).map((at, i) => at - times[i]!)).toEqual([600, 600, 600]);
  });
  it("downloads four real asset bodies concurrently without starting a fifth", async () => {
    const complete: (() => void)[] = [];
    const request = vi.fn(async () => new Response(new ReadableStream({
      start(controller) { complete.push(() => { controller.enqueue(new TextEncoder().encode("GLB")); controller.close(); }); },
    })));
    const queue = new ControlAssetQueue(request, 0, 4);
    const pending = ["a", "b", "c", "d", "e"].map(id => queue.fetch(id));
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    complete[0]!(); await pending[0];
    await vi.waitFor(() => expect(request).toHaveBeenCalledTimes(5));
    for (const finish of complete.slice(1)) finish();
    await Promise.all(pending);
  });
  it.each([[undefined, 60000], ["2", 2000]])("coordinates rate-limit cooldown (%s)", async (header, ms) => {
    vi.useFakeTimers();
    const request = vi.fn().mockResolvedValueOnce(new Response(null, { status: 429,
      headers: header ? { "Retry-After": header } : {} })).mockImplementation(async () => new Response("ok"));
    const queue = new ControlAssetQueue(request);
    const a = queue.fetch("a"), b = queue.fetch("b");
    await vi.advanceTimersByTimeAsync(0); expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(ms - 1); expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(await (await b).text()).toBe("ok");
    expect(request).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(600); expect(await (await a).text()).toBe("ok");
    expect(request).toHaveBeenCalledTimes(3);
  });
  it("does not abort a shared download when only one consumer cancels", async () => {
    let resolve!: (response: Response) => void;
    const request = vi.fn((_digest: string, _signal?: AbortSignal) => new Promise<Response>(r => { resolve = r; }));
    const queue = new ControlAssetQueue(request, 0, 1);
    const controller = new AbortController();
    const a = queue.fetch("same", controller.signal), b = queue.fetch("same");
    const cancelled = expect(a).rejects.toMatchObject({ name: "AbortError" });
    await Promise.resolve(); controller.abort(); await cancelled;
    expect(request.mock.calls[0]?.[1]?.aborted).not.toBe(true);
    resolve(new Response("ok")); expect(await (await b).text()).toBe("ok");
  });
  it("removes an aborted queued job without a network request", async () => {
    vi.useFakeTimers();
    const request = vi.fn(async () => new Response("ok"));
    const queue = new ControlAssetQueue(request);
    await queue.fetch("first");
    const controller = new AbortController();
    const pending = queue.fetch("cancelled", controller.signal);
    const cancelled = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort(); await cancelled;
    await vi.advanceTimersByTimeAsync(1000); expect(request).toHaveBeenCalledTimes(1);
  });
  it("bounds retries, never caches 429/403 and retains failures for the resolver", async () => {
    vi.useFakeTimers();
    const request = vi.fn(async () => new Response("limit", { status: 429, headers: { "Retry-After": "0" } }));
    const queue = new ControlAssetQueue(request, 0);
    const result = await queue.fetch("limited");
    expect(result.status).toBe(429); expect(request).toHaveBeenCalledTimes(5);
    expect(queue.diagnostics).toMatchObject({ retries: 4, cachedAssets: 0, failures: 1 });
    const denied = new ControlAssetQueue(async () => new Response("denied", { status: 403 }), 0);
    expect((await denied.fetch("a")).status).toBe(403);
    expect(denied.diagnostics.requests).toBe(1);
  });
  it("rejects oversize byte framing and isolates caches between runs", async () => {
    const invalid = new ControlAssetQueue(async () => new Response("x", { headers: { "Content-Length": "999999999" } }), 0);
    await expect(invalid.fetch("a")).rejects.toThrow("byte bound");
    const first = new ControlAssetQueue(async () => new Response("first"), 0);
    const second = new ControlAssetQueue(async () => new Response("second"), 0);
    expect(await (await first.fetch("a")).text()).toBe("first");
    expect(await (await second.fetch("a")).text()).toBe("second");
  });
  it("keeps the shared cooldown after the last allowed 429", async () => {
    vi.useFakeTimers();
    const request = vi.fn(async (digest: string) => digest === "a"
      ? new Response(null, { status: 429, headers: { "Retry-After": "2" } }) : new Response("ok"));
    const queue = new ControlAssetQueue(request, 0);
    const a = queue.fetch("a");
    await vi.advanceTimersByTimeAsync(8000);
    expect((await a).status).toBe(429); expect(request).toHaveBeenCalledTimes(5);
    const b = queue.fetch("b");
    await vi.advanceTimersByTimeAsync(1999); expect(request).toHaveBeenCalledTimes(5);
    await vi.advanceTimersByTimeAsync(1); expect((await b).status).toBe(200);
  });
});
