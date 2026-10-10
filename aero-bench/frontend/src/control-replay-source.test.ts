import { afterEach, describe, expect, it, vi } from "vitest";
import { controlReplayBaseHref, controlReplayFetch, type ControlReplaySource } from "./control-replay-source";

const runId = "a".repeat(64);
const digest = "b".repeat(64);
const origin = "https://viewer.test";
const base = controlReplayBaseHref(runId, origin);
function source(): ControlReplaySource {
  return { runId, trace: vi.fn(async () => new Response("trace")),
    manifest: vi.fn(async () => new Response("manifest")), asset: vi.fn(async () => new Response("asset")) };
}
afterEach(() => vi.useRealTimers());

describe("authenticated Control replay directory", () => {
  it("routes only public trace, manifest and content-addressed sealed files", async () => {
    const input = source();
    const request = controlReplayFetch(input, base);
    expect(await (await request(new URL("public-trace.json", base))).text()).toBe("trace");
    expect(await (await request(new URL("replay/public-trace.json", base))).text()).toBe("trace");
    expect(await (await request(new URL("replay/replay-manifest.json", base))).text()).toBe("manifest");
    for (const kind of ["assets", "artifacts"]) await request(new URL(`replay/${kind}/${digest}`, base));
    expect(input.asset).toHaveBeenCalledTimes(2);
    expect(input.asset).toHaveBeenCalledWith(digest, undefined);
  });
  it("rejects other runs, origins, private paths and query parameters before requesting bytes", async () => {
    const input = source();
    const request = controlReplayFetch(input, base);
    for (const path of ["../private/truth.json", "replay/private/truth.json", "public-trace.json?token=secret",
      `https://other.test/__control-replay/${runId}/public-trace.json`,
      `${origin}/__control-replay/${digest}/public-trace.json`]) {
      await expect(request(new URL(path, base))).rejects.toThrow();
    }
    expect(input.trace).not.toHaveBeenCalled();
    expect(input.asset).not.toHaveBeenCalled();
  });
  it("rejects writes passed in either Request or RequestInit", async () => {
    const input = source();
    const request = controlReplayFetch(input, base);
    const url = new URL("public-trace.json", base);
    await expect(request(url, { method: "POST" })).rejects.toThrow("read-only");
    await expect(request(new Request(url, { method: "POST" }))).rejects.toThrow("read-only");
    expect(input.trace).not.toHaveBeenCalled();
  });
  it("passes abort signals without placing credentials in virtual URLs", async () => {
    const input = source();
    const request = controlReplayFetch(input, base);
    const controller = new AbortController();
    await request(new URL("replay/replay-manifest.json", base), { signal: controller.signal });
    expect(input.manifest).toHaveBeenCalledWith(controller.signal);
    const req = new Request(new URL("public-trace.json", base), { signal: controller.signal });
    await request(req);
    expect(input.trace).toHaveBeenCalledWith(req.signal);
  });
  it("requires an exact run digest", () => {
    expect(() => controlReplayBaseHref("unknown", origin)).toThrow("run_id");
  });

  it("uses the 60-second default when an asset 429 has no Retry-After", async () => {
    vi.useFakeTimers();
    const input = source();
    vi.mocked(input.asset)
      .mockResolvedValueOnce(new Response(null, { status: 429 }))
      .mockResolvedValueOnce(new Response("asset"));
    const pending = controlReplayFetch(input, base)(new URL(`replay/assets/${digest}`, base));
    await vi.advanceTimersByTimeAsync(0);
    expect(input.asset).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(59_999);
    expect(input.asset).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await (await pending).text()).toBe("asset");
    expect(input.asset).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["delay seconds", "2", 2_000],
    ["HTTP date", "Thu, 01 Oct 2026 12:00:05 GMT", 5_000],
  ])("respects Retry-After %s", async (_label, retryAfter, delayMs) => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-01T12:00:00Z"));
    const input = source();
    vi.mocked(input.asset)
      .mockResolvedValueOnce(new Response(null, { status: 429, headers: { "Retry-After": retryAfter } }))
      .mockResolvedValueOnce(new Response("asset"));
    const pending = controlReplayFetch(input, base)(new URL(`replay/artifacts/${digest}`, base));
    await vi.advanceTimersByTimeAsync(delayMs - 1);
    expect(input.asset).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(await (await pending).text()).toBe("asset");
    expect(input.asset).toHaveBeenCalledTimes(2);
  });

  it("does not retry authentication failures or non-asset reads", async () => {
    vi.useFakeTimers();
    const input = source();
    vi.mocked(input.asset).mockResolvedValue(new Response(null, { status: 401 }));
    vi.mocked(input.manifest).mockResolvedValue(new Response(null, { status: 429 }));
    expect((await controlReplayFetch(input, base)(new URL(`replay/assets/${digest}`, base))).status).toBe(401);
    expect((await controlReplayFetch(input, base)(new URL("replay/replay-manifest.json", base))).status).toBe(429);
    expect(input.asset).toHaveBeenCalledTimes(1);
    expect(input.manifest).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts a pending asset backoff without issuing another request", async () => {
    vi.useFakeTimers();
    const input = source();
    vi.mocked(input.asset).mockResolvedValue(new Response(null, { status: 429 }));
    const controller = new AbortController();
    const pending = controlReplayFetch(input, base)(new URL(`replay/assets/${digest}`, base), {
      signal: controller.signal,
    });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(input.asset).toHaveBeenCalledTimes(1);
  });

  it("returns the terminal 429 after four bounded asset retries", async () => {
    vi.useFakeTimers();
    const input = source();
    vi.mocked(input.asset).mockImplementation(async () =>
      new Response(null, { status: 429, headers: { "Retry-After": "0" } }));
    const pending = controlReplayFetch(input, base)(new URL(`replay/assets/${digest}`, base));
    await vi.runAllTimersAsync();
    expect((await pending).status).toBe(429);
    expect(input.asset).toHaveBeenCalledTimes(5);
  });
});
