import { describe, expect, it, vi } from "vitest";
import { nextTask, readBoundedResponse } from "./verified-bytes";

function streamed(chunks: readonly number[][], headers: Record<string, string> = {}) {
  const cancel = vi.fn();
  let next = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      const chunk = chunks[next++];
      if (chunk === undefined) controller.close();
      else controller.enqueue(new Uint8Array(chunk));
    },
    cancel,
  });
  return { response: new Response(body, { headers }), cancel };
}

describe("bounded Response consumer", () => {
  it("uses the original builtin consumer for a bounded identity-framed body", async () => {
    const { response, cancel } = streamed([[1, 2], [3], [4, 5]], { "Content-Length": "5" });
    const progress = vi.fn();
    const builtin = vi.spyOn(response, "arrayBuffer");
    const result = await readBoundedResponse(response, 5, "asset", undefined, 5, progress);
    expect([...new Uint8Array(result)]).toEqual([1, 2, 3, 4, 5]);
    expect(progress.mock.calls).toEqual([[0, 5], [5, 5]]);
    expect(builtin).toHaveBeenCalledOnce();
    expect(cancel).not.toHaveBeenCalled();
    expect(response.bodyUsed).toBe(true);
  });

  it("recognizes explicit identity encoding and validates the final length", async () => {
    const { response } = streamed([[1, 2]], { "Content-Length": "2", "Content-Encoding": "identity" });
    const builtin = vi.spyOn(response, "arrayBuffer");
    expect((await readBoundedResponse(response, 4, "asset")).byteLength).toBe(2);
    expect(builtin).toHaveBeenCalledOnce();
  });

  it("keeps unknown-length bodies incrementally bounded even with an expected size", async () => {
    const { response } = streamed([[1, 2], [3]]);
    const builtin = vi.spyOn(response, "arrayBuffer");
    const progress = vi.fn();
    expect((await readBoundedResponse(response, 3, "asset", undefined, 3, progress)).byteLength).toBe(3);
    expect(builtin).not.toHaveBeenCalled();
    expect(progress.mock.calls).toEqual([[0, 3], [2, 3], [3, 3]]);
  });

  it("rejects synthetic framed bodies that exceed their header before reporting completion", async () => {
    const { response } = streamed([[1, 2, 3]], { "Content-Length": "2" });
    const progress = vi.fn();
    await expect(readBoundedResponse(response, 2, "asset", undefined, 2, progress))
      .rejects.toThrow("asset byte length 3 differs from the declared size 2");
    expect(progress.mock.calls).toEqual([[0, 2]]);
  });

  it("races framed builtin reads against abort even when a synthetic stream ignores the signal", async () => {
    const controller = new AbortController();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(new ReadableStream<Uint8Array>({
      start(value) { streamController = value; },
    }), { headers: { "Content-Length": "1" } });
    const pending = readBoundedResponse(response, 1, "asset", controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort(new Error("custom abort reason"));
    await rejected;
    // The builtin owns this synthetic stream's lock; release the fixture itself.
    streamController.close();
  });

  it("cancels a framed body if initial progress aborts before builtin consumption", async () => {
    const controller = new AbortController();
    const { response, cancel } = streamed([[1]], { "Content-Length": "1" });
    const builtin = vi.spyOn(response, "arrayBuffer");
    await expect(readBoundedResponse(response, 1, "asset", controller.signal, 1,
      () => controller.abort())).rejects.toMatchObject({ name: "AbortError" });
    expect(builtin).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("removes its abort listener after a successful builtin read", async () => {
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const { response } = streamed([[1]], { "Content-Length": "1" });
    await readBoundedResponse(response, 1, "asset", controller.signal);
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
  });

  it("preserves framed upstream and progress errors", async () => {
    const failure = new Error("framed stream broke");
    const response = new Response(new ReadableStream<Uint8Array>({
      pull(controller) { controller.error(failure); },
    }), { headers: { "Content-Length": "1" } });
    await expect(readBoundedResponse(response, 1, "asset")).rejects.toBe(failure);
    const fixture = streamed([[1]], { "Content-Length": "1" });
    await expect(readBoundedResponse(fixture.response, 1, "asset", undefined, 1,
      () => { throw failure; })).rejects.toBe(failure);
    expect(fixture.cancel).toHaveBeenCalledOnce();
  });

  it("accepts an empty body at an explicit zero bound", async () => {
    const { response } = streamed([], { "Content-Length": "0" });
    expect((await readBoundedResponse(response, 0, "empty", undefined, 0)).byteLength).toBe(0);
  });

  it("cancels upstream on an oversized chunk before reporting it as accepted", async () => {
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); }, cancel,
    }));
    const progress = vi.fn();
    await expect(readBoundedResponse(response, 2, "asset", undefined, undefined, progress))
      .rejects.toThrow("asset exceeds the 2-byte limit");
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce());
    expect(cancel.mock.calls[0]![0]).toBeInstanceOf(Error);
    expect(cancel.mock.calls[0]![0].message).toBe("asset exceeds the 2-byte limit");
    expect(progress.mock.calls).toEqual([[0, undefined]]);
  });

  it("rejects a truncated declared body", async () => {
    const { response } = streamed([[1, 2]], { "Content-Length": "3" });
    await expect(readBoundedResponse(response, 3, "asset", undefined, 3))
      .rejects.toThrow("asset byte length 2 differs from the declared size 3");
  });

  it("checks an expected length even without an HTTP length", async () => {
    const { response } = streamed([[1, 2]]);
    await expect(readBoundedResponse(response, 3, "asset", undefined, 3))
      .rejects.toThrow("asset byte length 2 differs from the declared size 3");
  });

  it.each(["-1", "1.5", "three", "9007199254740992", "9"])(
    "rejects malformed or over-bound Content-Length %s and cancels the body", async header => {
      const { response, cancel } = streamed([[1]], { "Content-Length": header });
      await expect(readBoundedResponse(response, 4, "asset"))
        .rejects.toThrow("asset Content-Length differs from its declared byte bound");
      expect(cancel).toHaveBeenCalledOnce();
    });

  it("rejects a header contradicting a declared length before progress starts", async () => {
    const { response, cancel } = streamed([[1]], { "Content-Length": "1" });
    const progress = vi.fn();
    await expect(readBoundedResponse(response, 4, "asset", undefined, 2, progress))
      .rejects.toThrow(/Content-Length differs/);
    expect(progress).not.toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each([-1, 1.5, Number.NaN, Number.POSITIVE_INFINITY])("rejects invalid byte bound %s", async bound => {
    await expect(readBoundedResponse(new Response("x"), bound, "asset"))
      .rejects.toThrow("asset byte bound is invalid");
  });

  it("rejects a missing response body", async () => {
    await expect(readBoundedResponse(new Response(null), 4, "asset"))
      .rejects.toThrow("asset response has no body");
  });

  it("rejects a pre-aborted signal without claiming the response", async () => {
    const controller = new AbortController(); controller.abort();
    const { response } = streamed([[1]]);
    await expect(readBoundedResponse(response, 1, "asset", controller.signal))
      .rejects.toMatchObject({ name: "AbortError" });
    expect(response.bodyUsed).toBe(false);
  });

  it("cancels an open upstream on abort even when the response ignores the signal", async () => {
    const controller = new AbortController();
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({ cancel }));
    const pending = readBoundedResponse(response, 4, "asset", controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await rejected;
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce());
  });

  it("preserves an upstream failure without replacing its error", async () => {
    const failure = new Error("provider stream broke");
    const response = new Response(new ReadableStream<Uint8Array>({
      pull(controller) { controller.error(failure); },
    }));
    await expect(readBoundedResponse(response, 4, "asset")).rejects.toBe(failure);
  });

  it("cancels unclaimed input if the initial progress callback throws", async () => {
    const failure = new Error("progress callback broke");
    const { response, cancel } = streamed([[1]]);
    await expect(readBoundedResponse(response, 4, "asset", undefined, undefined,
      () => { throw failure; })).rejects.toBe(failure);
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("cancels upstream if a later progress callback throws", async () => {
    const failure = new Error("progress callback broke");
    const cancel = vi.fn();
    const response = new Response(new ReadableStream<Uint8Array>({
      pull(controller) { controller.enqueue(new Uint8Array([1])); }, cancel,
    }));
    await expect(readBoundedResponse(response, 4, "asset", undefined, undefined,
      completed => { if (completed > 0) throw failure; })).rejects.toBe(failure);
    await vi.waitFor(() => expect(cancel).toHaveBeenCalledOnce());
  });

  it("uses decoded bytes rather than compressed wire length for progress and the final size", async () => {
    const { response } = streamed([[1, 2, 3], [4, 5]], {
      "Content-Length": "3", "Content-Encoding": "gzip",
    });
    const progress = vi.fn();
    const builtin = vi.spyOn(response, "arrayBuffer");
    const result = await readBoundedResponse(response, 5, "trace", undefined, undefined, progress);
    expect(result.byteLength).toBe(5);
    expect(progress.mock.calls).toEqual([[0, undefined], [3, undefined], [5, undefined]]);
    expect(builtin).not.toHaveBeenCalled();
  });

  it("still enforces the decoded maximum for compressed responses", async () => {
    const { response } = streamed([[1, 2, 3], [4, 5]], {
      "Content-Length": "3", "Content-Encoding": "gzip",
    });
    await expect(readBoundedResponse(response, 4, "trace")).rejects.toThrow("trace exceeds the 4-byte limit");
  });
});

describe("nextTask", () => {
  it("resumes in a later task and rejects when the load was aborted meanwhile", async () => {
    const order: string[] = [];
    const resumed = nextTask().then(() => order.push("resumed"));
    setTimeout(() => order.push("next timer"), 0);
    await Promise.resolve();
    expect(order).toEqual([]);
    await resumed;
    expect(order).toEqual(["resumed"]);
    const controller = new AbortController();
    const pending = nextTask(controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow(/aborted/);
  });
});
