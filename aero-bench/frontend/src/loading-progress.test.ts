import { describe, expect, it } from "vitest";
import { LoadingProgressView } from "./loading-progress";
import { readBoundedResponse } from "./verified-bytes";

describe("measured loading progress", () => {
  it("rejects a truncated stream before reporting it as downloaded", async () => {
    await expect(readBoundedResponse(new Response("abc", { headers: { "Content-Length": "4" } }), 10, "trace"))
      .rejects.toThrow("byte length 3");
  });
  it("reports received bytes while the stream is still open", async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>;
    // Without a framing Content-Length the reader keeps its incremental pipe; the declared size sets the total.
    const response = new Response(new ReadableStream<Uint8Array>({ start(controller) { stream = controller; } }));
    const counts: number[] = [];
    const view = new LoadingProgressView();
    const pending = readBoundedResponse(response, 10, "test", undefined, 4, (completed, total) => {
      counts.push(completed);
      view.update({ stage: "download", completed, total });
    });
    stream.enqueue(new Uint8Array([1, 2]));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(counts).toEqual([0, 2]);
    expect(view.root.textContent).toContain("50%");
    stream.enqueue(new Uint8Array([3, 4]));
    stream.close();
    expect(new Uint8Array(await pending)).toEqual(new Uint8Array([1, 2, 3, 4]));
    expect(counts).toEqual([0, 2, 4]);
    view.update({ stage: "parse" });
    expect(view.root.querySelector("progress")?.hasAttribute("value")).toBe(false);
    expect(view.root.textContent).not.toContain("100%");
  });

  it("does not invent a total for unknown or compressed stream sizes", async () => {
    for (const headers of [new Headers(), new Headers({ "Content-Length": "2", "Content-Encoding": "gzip" })]) {
      const view = new LoadingProgressView();
      await readBoundedResponse(new Response("abcd", { headers }), 10, "test", undefined, undefined,
        (completed, total) => view.update({ stage: "download", completed, total }));
      expect(view.root.querySelector("progress")?.hasAttribute("value")).toBe(false);
      expect(view.root.textContent).not.toContain("%");
    }
  });

  it("keeps scene readiness distinct from data download and shows failures", () => {
    const view = new LoadingProgressView();
    view.update({ stage: "scene", completed: 2, total: 5 });
    expect(view.root.dataset.stage).toBe("scene");
    expect(view.root.textContent).toContain("2 / 5");
    view.update({ stage: "render" });
    expect(view.root.querySelector("progress")?.hidden).toBe(false);
    view.update({ stage: "failed", detail: "missing mesh" });
    expect(view.root.dataset.stage).toBe("failed");
    expect(view.root.textContent).toContain("missing mesh");
    view.clear();
    expect(view.root.hidden).toBe(true);
  });
});
