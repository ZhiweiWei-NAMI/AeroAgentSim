import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  CityPerfOverlay,
  FramePerfAccumulator,
  budgetsPass,
  checkBudgets,
  type FrameSample,
} from "./city-perf-overlay";

function sample(frameMs: number, overrides: Partial<FrameSample["info"]["render"]> = {}): FrameSample {
  return {
    frameMs,
    info: {
      render: { calls: 100, triangles: 50_000, ...overrides },
      memory: { geometries: 10, textures: 5 },
    },
  };
}

describe("FramePerfAccumulator", () => {
  it("reports an explicit empty snapshot before any sample is pushed", () => {
    const accumulator = new FramePerfAccumulator(4);
    const stats = accumulator.snapshot(null);
    expect(stats.sampleCount).toBe(0);
    expect(stats.fps).toBe(0);
    expect(stats.frameMsP50).toBe(0);
    expect(stats.jsHeapUsedMb).toBeNull();
    expect(stats.jsHeapTotalMb).toBeNull();
  });

  it("rejects a non-positive-integer window size", () => {
    expect(() => new FramePerfAccumulator(0)).toThrow();
    expect(() => new FramePerfAccumulator(1.5)).toThrow();
  });

  it("rejects a non-finite or negative frameMs", () => {
    const accumulator = new FramePerfAccumulator(4);
    expect(() => accumulator.push(sample(Number.NaN))).toThrow();
    expect(() => accumulator.push(sample(-1))).toThrow();
  });

  it("computes p50/p95/max over a known distribution (linear-interpolation percentile)", () => {
    const accumulator = new FramePerfAccumulator(10);
    // 1..10 ms, evenly spaced; ascending order already.
    for (let ms = 1; ms <= 10; ms++) accumulator.push(sample(ms));
    const stats = accumulator.snapshot(null);
    expect(stats.sampleCount).toBe(10);
    // rank = 0.5 * 9 = 4.5 -> interpolate between index 4 (5) and 5 (6) => 5.5
    expect(stats.frameMsP50).toBeCloseTo(5.5, 10);
    // rank = 0.95 * 9 = 8.55 -> interpolate between index 8 (9) and 9 (10) => 9.55
    expect(stats.frameMsP95).toBeCloseTo(9.55, 10);
    expect(stats.frameMsMax).toBe(10);
    const meanMs = (1 + 2 + 3 + 4 + 5 + 6 + 7 + 8 + 9 + 10) / 10;
    expect(stats.fps).toBeCloseTo(1000 / meanMs, 10);
  });

  it("percentile is order-independent (push order does not matter, only values in window)", () => {
    const ascending = new FramePerfAccumulator(5);
    for (const ms of [1, 2, 3, 4, 5]) ascending.push(sample(ms));
    const shuffled = new FramePerfAccumulator(5);
    for (const ms of [5, 1, 4, 2, 3]) shuffled.push(sample(ms));
    expect(shuffled.snapshot(null).frameMsP50).toBeCloseTo(ascending.snapshot(null).frameMsP50, 10);
    expect(shuffled.snapshot(null).frameMsP95).toBeCloseTo(ascending.snapshot(null).frameMsP95, 10);
  });

  it("evicts the oldest sample once the window is full (ring buffer behavior)", () => {
    const accumulator = new FramePerfAccumulator(3);
    accumulator.push(sample(100));
    accumulator.push(sample(100));
    accumulator.push(sample(100));
    expect(accumulator.size).toBe(3);
    expect(accumulator.snapshot(null).frameMsMax).toBe(100);
    // Pushing a 4th sample must evict the first 100 ms sample, not grow the window.
    accumulator.push(sample(1));
    expect(accumulator.size).toBe(3);
    const stats = accumulator.snapshot(null);
    expect(stats.sampleCount).toBe(3);
    expect(stats.frameMsMax).toBe(100);
    // Window now holds [100, 100, 1]; min of the window should be 1, confirming the newest
    // sample is present and the window size did not exceed capacity.
    const sorted = [100, 100, 1].sort((left, right) => left - right);
    const median = sorted[Math.floor(sorted.length / 2)];
    if (median === undefined) throw new Error("unreachable: fixed 3-element array");
    expect(stats.frameMsP50).toBeCloseTo(median, 10);
  });

  it("evicting past a full cycle drops every original sample", () => {
    const accumulator = new FramePerfAccumulator(2);
    accumulator.push(sample(1));
    accumulator.push(sample(2));
    accumulator.push(sample(3));
    accumulator.push(sample(4));
    const stats = accumulator.snapshot(null);
    expect(stats.sampleCount).toBe(2);
    expect(stats.frameMsMax).toBe(4);
    expect(stats.frameMsP50).toBeCloseTo(3.5, 10);
  });

  it("reports the latest sample's draw calls, triangles, geometries and textures", () => {
    const accumulator = new FramePerfAccumulator(2);
    accumulator.push(sample(16, { calls: 10, triangles: 1000 }));
    accumulator.push(sample(16, { calls: 42, triangles: 9000 }));
    const stats = accumulator.snapshot(null);
    expect(stats.drawCalls).toBe(42);
    expect(stats.triangles).toBe(9000);
    expect(stats.geometries).toBe(10);
    expect(stats.textures).toBe(5);
  });

  it("converts JS heap bytes to MB when memory is supplied, and reports null otherwise", () => {
    const accumulator = new FramePerfAccumulator(2);
    accumulator.push(sample(16));
    const withMemory = accumulator.snapshot({ usedJSHeapSize: 16 * 1024 * 1024, totalJSHeapSize: 64 * 1024 * 1024 });
    expect(withMemory.jsHeapUsedMb).toBeCloseTo(16, 10);
    expect(withMemory.jsHeapTotalMb).toBeCloseTo(64, 10);
    const withoutMemory = accumulator.snapshot(null);
    expect(withoutMemory.jsHeapUsedMb).toBeNull();
    expect(withoutMemory.jsHeapTotalMb).toBeNull();
  });

  it("reset clears the window back to an empty state", () => {
    const accumulator = new FramePerfAccumulator(3);
    accumulator.push(sample(10));
    accumulator.push(sample(20));
    accumulator.reset();
    expect(accumulator.size).toBe(0);
    expect(accumulator.snapshot(null).sampleCount).toBe(0);
  });
});

describe("checkBudgets", () => {
  const accumulator = new FramePerfAccumulator(4);
  for (const ms of [10, 10, 10, 10]) accumulator.push(sample(ms, { calls: 200, triangles: 100_000 }));
  const stats = accumulator.snapshot(null);

  it("only evaluates metrics with a declared budget", () => {
    const results = checkBudgets(stats, {});
    expect(results).toHaveLength(0);
    expect(budgetsPass(results)).toBe(true);
  });

  it("passes a min-comparator budget (fps) when the value meets the floor", () => {
    const results = checkBudgets(stats, { minFps: 50 }); // mean frame = 10ms -> 100 fps
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ metric: "fps", comparator: "min", limit: 50, pass: true });
  });

  it("fails a min-comparator budget when the value is below the floor", () => {
    const results = checkBudgets(stats, { minFps: 500 });
    expect(results[0]?.pass).toBe(false);
  });

  it("passes and fails max-comparator budgets correctly", () => {
    const results = checkBudgets(stats, { maxDrawCalls: 200, maxTriangles: 99_999 });
    const drawCalls = results.find(result => result.metric === "drawCalls");
    const triangles = results.find(result => result.metric === "triangles");
    expect(drawCalls).toMatchObject({ pass: true, limit: 200, value: 200 });
    expect(triangles).toMatchObject({ pass: false, limit: 99_999, value: 100_000 });
    expect(budgetsPass(results)).toBe(false);
  });

  it("fails explicitly, never passes silently, when the measured value is null", () => {
    const noMemoryStats = accumulator.snapshot(null);
    expect(noMemoryStats.jsHeapUsedMb).toBeNull();
    const results = checkBudgets(noMemoryStats, { maxJsHeapUsedMb: 256 });
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ metric: "jsHeapUsedMb", value: null, pass: false });
  });

  it("passes a jsHeapUsedMb budget when memory is present and under the limit", () => {
    const withMemory = accumulator.snapshot({ usedJSHeapSize: 100 * 1024 * 1024, totalJSHeapSize: 500 * 1024 * 1024 });
    const results = checkBudgets(withMemory, { maxJsHeapUsedMb: 256 });
    expect(results[0]).toMatchObject({ pass: true });
  });
});

describe("CityPerfOverlay", () => {
  let host: HTMLDivElement;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
  });

  afterEach(() => {
    host.remove();
  });

  it("starts hidden by default and does not render stats text while hidden", () => {
    const overlay = new CityPerfOverlay();
    overlay.attach(host);
    expect(overlay.isVisible()).toBe(false);
    expect(overlay.element.style.display).toBe("none");
    overlay.sample(sample(16));
    expect(overlay.element.textContent).toBe("");
  });

  it("toggle shows and hides the overlay and renders stats once visible", () => {
    const overlay = new CityPerfOverlay({ windowSize: 8 });
    overlay.attach(host);
    expect(overlay.toggle()).toBe(true);
    expect(overlay.element.style.display).toBe("block");
    overlay.sample(sample(16, { calls: 77, triangles: 12_345 }));
    expect(overlay.element.textContent).toContain("draws 77 tris 12345");
    expect(overlay.toggle()).toBe(false);
    expect(overlay.element.style.display).toBe("none");
  });

  it("reports null heap as 'n/a' in the rendered text when performance.memory is absent", () => {
    const overlay = new CityPerfOverlay({ visible: true });
    overlay.attach(host);
    overlay.sample(sample(16));
    expect(overlay.element.textContent).toContain("heap n/a");
  });

  it("renders OK/FAIL lines for declared budgets", () => {
    const overlay = new CityPerfOverlay({ visible: true, budgets: { maxDrawCalls: 10 } });
    overlay.attach(host);
    overlay.sample(sample(16, { calls: 50 }));
    expect(overlay.element.textContent).toContain("FAIL drawCalls");
  });

  it("detach removes the element from its parent", () => {
    const overlay = new CityPerfOverlay();
    overlay.attach(host);
    expect(host.contains(overlay.element)).toBe(true);
    overlay.detach();
    expect(host.contains(overlay.element)).toBe(false);
  });
});
