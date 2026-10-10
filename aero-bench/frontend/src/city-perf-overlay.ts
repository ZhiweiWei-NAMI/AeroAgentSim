/** Pure frame-statistics accumulator and a small DOM overlay for the city render preview.
 *
 * This module has no dependency on `map.ts` internals: callers push one `FrameSample` per
 * rendered frame (`{ info, frameMs }`, where `info` mirrors the shape of THREE's
 * `renderer.info`). It keeps a rolling window of the most recent samples and exposes
 * fps and frame-time percentiles, the latest draw-call/triangle/memory counters, and an
 * explicit (never guessed) JS heap reading when the browser exposes `performance.memory`.
 */

export interface RendererInfoLike {
  readonly render: { readonly calls: number; readonly triangles: number };
  readonly memory: { readonly geometries: number; readonly textures: number };
}

export interface FrameSample {
  readonly info: RendererInfoLike;
  /** Wall-clock duration of the frame in milliseconds (e.g. the delta between two
   *  `requestAnimationFrame` timestamps, or an explicit render-only measurement). */
  readonly frameMs: number;
}

export interface JsHeapInfoLike {
  readonly usedJSHeapSize: number;
  readonly totalJSHeapSize: number;
}

export interface FrameStatsSnapshot {
  readonly sampleCount: number;
  readonly windowSize: number;
  readonly fps: number;
  readonly frameMsP50: number;
  readonly frameMsP95: number;
  readonly frameMsMax: number;
  readonly drawCalls: number;
  readonly triangles: number;
  readonly geometries: number;
  readonly textures: number;
  readonly jsHeapUsedMb: number | null;
  readonly jsHeapTotalMb: number | null;
}

const BYTES_PER_MB = 1024 * 1024;

function toMb(bytes: number): number {
  return bytes / BYTES_PER_MB;
}

/** Linear-interpolation percentile over an ascending-sorted, non-empty array (numpy default). */
function percentile(sortedAscending: readonly number[], fraction: number): number {
  if (sortedAscending.length === 0) throw new Error("percentile requires at least one sample");
  if (fraction < 0 || fraction > 1) throw new Error(`percentile fraction must be in [0, 1], got ${fraction}`);
  const lastIndex = sortedAscending.length - 1;
  const rank = fraction * lastIndex;
  const lowerIndex = Math.floor(rank);
  const upperIndex = Math.ceil(rank);
  const lower = sortedAscending[lowerIndex];
  const upper = sortedAscending[upperIndex];
  if (lower === undefined || upper === undefined) throw new Error("percentile index out of range");
  if (lowerIndex === upperIndex) return lower;
  const weight = rank - lowerIndex;
  return lower * (1 - weight) + upper * weight;
}

/** Rolling-window accumulator of per-frame render statistics. Fixed capacity ring buffer:
 *  once `windowSize` samples have been pushed, each new sample evicts the oldest one. */
export class FramePerfAccumulator {
  private readonly windowSize: number;
  private readonly buffer: FrameSample[];
  private writeIndex = 0;
  private filled = false;

  constructor(windowSize = 120) {
    if (!Number.isInteger(windowSize) || windowSize < 1) {
      throw new Error(`windowSize must be a positive integer, got ${windowSize}`);
    }
    this.windowSize = windowSize;
    this.buffer = new Array<FrameSample>(windowSize);
  }

  /** Number of samples currently retained (<= windowSize). */
  get size(): number {
    return this.filled ? this.windowSize : this.writeIndex;
  }

  push(sample: FrameSample): void {
    if (!Number.isFinite(sample.frameMs) || sample.frameMs < 0) {
      throw new Error(`frameMs must be a finite, non-negative number, got ${sample.frameMs}`);
    }
    this.buffer[this.writeIndex] = sample;
    this.writeIndex = (this.writeIndex + 1) % this.windowSize;
    if (this.writeIndex === 0) this.filled = true;
  }

  reset(): void {
    this.buffer.length = 0;
    this.buffer.length = this.windowSize;
    this.writeIndex = 0;
    this.filled = false;
  }

  /** Samples ordered oldest-first within the current window. */
  private orderedSamples(): FrameSample[] {
    if (!this.filled) return this.buffer.slice(0, this.writeIndex);
    return [...this.buffer.slice(this.writeIndex), ...this.buffer.slice(0, this.writeIndex)];
  }

  /** Snapshot the current window. `memory` is explicitly `null` when the caller's runtime
   *  does not expose `performance.memory` (non-Chromium browsers); it is never guessed. */
  snapshot(memory: JsHeapInfoLike | null): FrameStatsSnapshot {
    const jsHeapUsedMb = memory ? toMb(memory.usedJSHeapSize) : null;
    const jsHeapTotalMb = memory ? toMb(memory.totalJSHeapSize) : null;
    const count = this.size;
    if (count === 0) {
      return {
        sampleCount: 0, windowSize: this.windowSize, fps: 0,
        frameMsP50: 0, frameMsP95: 0, frameMsMax: 0,
        drawCalls: 0, triangles: 0, geometries: 0, textures: 0,
        jsHeapUsedMb, jsHeapTotalMb,
      };
    }
    const samples = this.orderedSamples();
    const frameMsSorted = samples.map(sample => sample.frameMs).sort((left, right) => left - right);
    const meanFrameMs = frameMsSorted.reduce((total, value) => total + value, 0) / frameMsSorted.length;
    const latest = samples[samples.length - 1];
    if (latest === undefined) throw new Error("unreachable: non-empty window has no latest sample");
    const max = frameMsSorted[frameMsSorted.length - 1];
    if (max === undefined) throw new Error("unreachable: non-empty window has no max frame time");
    return {
      sampleCount: count,
      windowSize: this.windowSize,
      fps: meanFrameMs > 0 ? 1000 / meanFrameMs : 0,
      frameMsP50: percentile(frameMsSorted, 0.5),
      frameMsP95: percentile(frameMsSorted, 0.95),
      frameMsMax: max,
      drawCalls: latest.info.render.calls,
      triangles: latest.info.render.triangles,
      geometries: latest.info.memory.geometries,
      textures: latest.info.memory.textures,
      jsHeapUsedMb,
      jsHeapTotalMb,
    };
  }
}

export interface FrameBudgets {
  readonly minFps?: number;
  readonly maxFrameMsP50?: number;
  readonly maxFrameMsP95?: number;
  readonly maxFrameMsMax?: number;
  readonly maxDrawCalls?: number;
  readonly maxTriangles?: number;
  readonly maxGeometries?: number;
  readonly maxTextures?: number;
  readonly maxJsHeapUsedMb?: number;
}

export type BudgetComparator = "min" | "max";

export interface BudgetCheckResult {
  readonly metric: string;
  readonly value: number | null;
  readonly limit: number;
  readonly comparator: BudgetComparator;
  readonly pass: boolean;
}

/** Evaluate declared budgets against a stats snapshot. Only metrics with a declared budget
 *  are checked. A `null` measured value (e.g. missing JS heap) never silently passes: the
 *  check fails explicitly, since the budget cannot be verified. */
export function checkBudgets(stats: FrameStatsSnapshot, budgets: FrameBudgets): BudgetCheckResult[] {
  const results: BudgetCheckResult[] = [];
  const addMax = (metric: string, value: number | null, limit: number | undefined): void => {
    if (limit === undefined) return;
    results.push({ metric, value, limit, comparator: "max", pass: value !== null && value <= limit });
  };
  const addMin = (metric: string, value: number | null, limit: number | undefined): void => {
    if (limit === undefined) return;
    results.push({ metric, value, limit, comparator: "min", pass: value !== null && value >= limit });
  };
  addMin("fps", stats.fps, budgets.minFps);
  addMax("frameMsP50", stats.frameMsP50, budgets.maxFrameMsP50);
  addMax("frameMsP95", stats.frameMsP95, budgets.maxFrameMsP95);
  addMax("frameMsMax", stats.frameMsMax, budgets.maxFrameMsMax);
  addMax("drawCalls", stats.drawCalls, budgets.maxDrawCalls);
  addMax("triangles", stats.triangles, budgets.maxTriangles);
  addMax("geometries", stats.geometries, budgets.maxGeometries);
  addMax("textures", stats.textures, budgets.maxTextures);
  addMax("jsHeapUsedMb", stats.jsHeapUsedMb, budgets.maxJsHeapUsedMb);
  return results;
}

export function budgetsPass(results: readonly BudgetCheckResult[]): boolean {
  return results.every(result => result.pass);
}

function readJsHeap(): JsHeapInfoLike | null {
  const candidate = (performance as Performance & { memory?: JsHeapInfoLike }).memory;
  return candidate ?? null;
}

export interface CityPerfOverlayOptions {
  readonly windowSize?: number;
  readonly budgets?: FrameBudgets;
  readonly visible?: boolean;
}

/** A small, self-contained DOM overlay rendering the rolling stats and budget pass/fail.
 *  Does not read `window.performance.memory` unless present; never fabricates a reading. */
export class CityPerfOverlay {
  readonly accumulator: FramePerfAccumulator;
  readonly element: HTMLElement;
  private readonly budgets: FrameBudgets;
  private visible: boolean;

  constructor(options: CityPerfOverlayOptions = {}) {
    this.accumulator = new FramePerfAccumulator(options.windowSize ?? 120);
    this.budgets = options.budgets ?? {};
    this.visible = options.visible ?? false;
    this.element = document.createElement("pre");
    this.element.className = "city-perf-overlay";
    this.element.style.cssText = "position:absolute;top:8px;right:8px;z-index:1000;margin:0;"
      + "padding:6px 8px;background:rgba(0,0,0,0.65);color:#0f0;font:11px/1.4 monospace;"
      + "white-space:pre;pointer-events:none;";
    this.element.style.display = this.visible ? "block" : "none";
  }

  attach(parent: HTMLElement): void {
    parent.appendChild(this.element);
  }

  detach(): void {
    this.element.remove();
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
    this.element.style.display = visible ? "block" : "none";
    if (visible) this.render();
  }

  toggle(): boolean {
    this.setVisible(!this.visible);
    return this.visible;
  }

  isVisible(): boolean {
    return this.visible;
  }

  /** Push one frame sample and, if visible, re-render the overlay text. */
  sample(frame: FrameSample): FrameStatsSnapshot {
    this.accumulator.push(frame);
    const stats = this.accumulator.snapshot(readJsHeap());
    if (this.visible) this.renderStats(stats);
    return stats;
  }

  snapshot(): FrameStatsSnapshot {
    return this.accumulator.snapshot(readJsHeap());
  }

  private render(): void {
    this.renderStats(this.snapshot());
  }

  private renderStats(stats: FrameStatsSnapshot): void {
    const results = checkBudgets(stats, this.budgets);
    const lines = [
      `fps ${stats.fps.toFixed(1)}`,
      `frame p50/p95/max ${stats.frameMsP50.toFixed(2)}/${stats.frameMsP95.toFixed(2)}/${stats.frameMsMax.toFixed(2)} ms`,
      `draws ${stats.drawCalls} tris ${stats.triangles}`,
      `geo ${stats.geometries} tex ${stats.textures}`,
      `heap ${stats.jsHeapUsedMb === null ? "n/a" : `${stats.jsHeapUsedMb.toFixed(1)} MB`}`,
      ...results.map(result => `${result.pass ? "OK" : "FAIL"} ${result.metric}`),
    ];
    this.element.textContent = lines.join("\n");
  }
}
