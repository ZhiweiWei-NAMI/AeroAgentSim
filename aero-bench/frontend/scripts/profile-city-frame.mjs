/**
 * No-recording frame profiler for the city preview.
 *
 * The script measures the already-published frontend at a stable origin. It never builds,
 * publishes, or changes production state. Each repetition retains at least 300 rendered
 * preview frames and records frame cadence, CPU sections, renderer counters, and WebGL2 GPU
 * timer queries when the browser exposes EXT_disjoint_timer_query_webgl2.
 *
 * Default matrix:
 *   cameras: overview, street-1
 *   cases: baseline, no-shadows, no-reflections, no-vegetation, no-traffic, pixel-ratio-1
 *   repetitions: 3
 *   measured frames per repetition: 300
 *
 * Usage:
 *   node frontend/scripts/profile-city-frame.mjs \
 *     --origin=https://127.0.0.1:5209 \
 *     --cameras=validation/platform-plan-20261001/E1-browser-v2/cameras.json \
 *     --output=validation/codex-takeover-20261001/F
 *
 * Use --camera-mode=all for every stored camera, or --camera-names=overview,street-2 for an
 * explicit subset. The published default scene is used unless --scene is supplied.
 */
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const CASES = [
  "baseline",
  "no-shadows",
  "no-reflections",
  "no-vegetation",
  "no-traffic",
  "pixel-ratio-1",
];
const REPRESENTATIVE_CAMERAS = ["overview", "street-1"];
const GPU_ARGS = [
  "--enable-gpu",
  "--use-angle=vulkan",
  "--enable-features=Vulkan",
  "--disable-vulkan-surface",
  "--disable-software-rasterizer",
  "--ignore-gpu-blocklist",
  "--disable-background-timer-throttling",
  "--disable-renderer-backgrounding",
];

function parseArgs(argv) {
  const options = {};
  for (const arg of argv) {
    const match = /^--([^=]+)=(.*)$/.exec(arg);
    if (!match) throw new Error(`Arguments must use --name=value form: ${arg}`);
    options[match[1]] = match[2];
  }
  return options;
}

function integerOption(options, name, fallback, minimum) {
  const value = Number(options[name] ?? fallback);
  if (!Number.isInteger(value) || value < minimum) {
    throw new Error(`--${name} must be an integer >= ${minimum}, got ${options[name]}`);
  }
  return value;
}

function percentile(values, fraction) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const rank = fraction * (sorted.length - 1);
  const lower = Math.floor(rank);
  const upper = Math.ceil(rank);
  if (lower === upper) return sorted[lower];
  return sorted[lower] * (upper - rank) + sorted[upper] * (rank - lower);
}

function numericSummary(values) {
  const finite = values.filter(Number.isFinite);
  if (finite.length === 0) return null;
  const sum = finite.reduce((total, value) => total + value, 0);
  return {
    count: finite.length,
    mean: sum / finite.length,
    min: Math.min(...finite),
    p50: percentile(finite, 0.5),
    p95: percentile(finite, 0.95),
    max: Math.max(...finite),
  };
}

function round(value, digits = 4) {
  if (value === null || !Number.isFinite(value)) return value;
  return Number(value.toFixed(digits));
}

function roundedSummary(summary) {
  if (summary === null) return null;
  return Object.fromEntries(Object.entries(summary).map(([key, value]) =>
    [key, typeof value === "number" ? round(value) : value]));
}

const RENDERER_COUNTER_FIELDS = ["drawCalls", "triangles", "geometries", "textures", "programCount"];
const MATCHED_SCENE_COUNTER_FIELDS = ["drawCalls", "triangles", "geometries", "textures"];

export function rendererCounterStability(frames) {
  const missingFields = RENDERER_COUNTER_FIELDS.filter(field => frames.length === 0
    || frames.some(frame => !Number.isFinite(frame[field])));
  const ranges = Object.fromEntries(RENDERER_COUNTER_FIELDS.map(field => {
    const values = frames.map(frame => frame[field]).filter(Number.isFinite);
    return [field, values.length === 0 ? null : { min: Math.min(...values), max: Math.max(...values) }];
  }));
  const driftedFields = RENDERER_COUNTER_FIELDS.filter(field => {
    const range = ranges[field];
    return range !== null && range.min !== range.max;
  });
  return {
    status: missingFields.length ? "missing" : driftedFields.length === 0 ? "stable" : "drifted",
    missingFields,
    driftedFields,
    ranges,
  };
}

export function matchedSceneCounterDrift(summaries) {
  const grouped = new Map();
  for (const summary of summaries) {
    const key = `${summary.camera}\u0000${summary.caseName}`;
    const entries = grouped.get(key) ?? [];
    entries.push(summary);
    grouped.set(key, entries);
  }
  const drift = [];
  for (const [key, entries] of grouped) {
    const [camera, caseName] = key.split("\u0000");
    for (const field of MATCHED_SCENE_COUNTER_FIELDS) {
      const values = entries.map(entry => entry[field]?.p50).filter(Number.isFinite);
      const unique = [...new Set(values)];
      if (values.length !== entries.length || unique.length !== 1) {
        drift.push({ camera, caseName, field,
          repetitions: entries.map(entry => ({ repetition: entry.repetition, value: entry[field]?.p50 ?? null })) });
      }
    }
  }
  return drift;
}

function summarizeFrames(frames, metadata) {
  if (frames.length < 300) {
    throw new Error(`${metadata.camera}/${metadata.caseName}/r${metadata.repetition} retained ${frames.length} frames; 300 required`);
  }
  const frameIntervals = frames.map(frame => frame.frameIntervalMs).filter(Number.isFinite);
  const interval = numericSummary(frameIntervals);
  const gpuValues = frames.map(frame => frame.gpuMs).filter(Number.isFinite);
  const last = frames.at(-1);
  return {
    ...metadata,
    frameCount: frames.length,
    intervalCount: frameIntervals.length,
    fpsFromMeanInterval: interval === null || interval.mean <= 0 ? null : round(1000 / interval.mean),
    frameIntervalMs: roundedSummary(interval),
    mapFrameCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.mapFrameCpuMs))),
    rendererSubmitCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.rendererSubmitCpuMs))),
    trafficUpdateCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.trafficUpdateCpuMs))),
    vegetationUpdateCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.vegetationUpdateCpuMs))),
    reflectionRefreshCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.reflectionRefreshCpuMs))),
    sunShadowFocusCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.sunShadowFocusCpuMs))),
    streamUpdateCpuMs: roundedSummary(numericSummary(frames.map(frame => frame.streamUpdateCpuMs))),
    gpuFrameMs: roundedSummary(numericSummary(gpuValues)),
    gpuFrameCount: gpuValues.length,
    reflectionCaptureCount: frames.reduce((total, frame) => total + frame.reflectionCaptureCount, 0),
    rendererInvocations: roundedSummary(numericSummary(frames.map(frame => frame.rendererInvocationCount))),
    drawCalls: roundedSummary(numericSummary(frames.map(frame => frame.drawCalls))),
    triangles: roundedSummary(numericSummary(frames.map(frame => frame.triangles))),
    geometries: roundedSummary(numericSummary(frames.map(frame => frame.geometries))),
    textures: roundedSummary(numericSummary(frames.map(frame => frame.textures))),
    programCount: roundedSummary(numericSummary(frames.map(frame => frame.programCount))),
    rendererCounterStability: rendererCounterStability(frames),
    lastFrame: {
      pixelRatio: last?.pixelRatio ?? null,
      shadowMapEnabled: last?.shadowMapEnabled ?? null,
      vegetationVisible: last?.vegetationVisible ?? null,
      trafficVisible: last?.trafficVisible ?? null,
      drawCalls: last?.drawCalls ?? null,
      triangles: last?.triangles ?? null,
    },
  };
}

function aggregateRepetitions(repetitions) {
  const metric = (path) => repetitions.map(item => path(item)).filter(Number.isFinite);
  return {
    repetitions: repetitions.length,
    retainedFrames: repetitions.reduce((sum, item) => sum + item.frameCount, 0),
    fps: roundedSummary(numericSummary(metric(item => item.fpsFromMeanInterval))),
    frameIntervalP50Ms: roundedSummary(numericSummary(metric(item => item.frameIntervalMs?.p50))),
    frameIntervalP95Ms: roundedSummary(numericSummary(metric(item => item.frameIntervalMs?.p95))),
    mapFrameCpuP50Ms: roundedSummary(numericSummary(metric(item => item.mapFrameCpuMs?.p50))),
    rendererSubmitCpuP50Ms: roundedSummary(numericSummary(metric(item => item.rendererSubmitCpuMs?.p50))),
    gpuFrameP50Ms: roundedSummary(numericSummary(metric(item => item.gpuFrameMs?.p50))),
    drawCallsP50: roundedSummary(numericSummary(metric(item => item.drawCalls?.p50))),
    trianglesP50: roundedSummary(numericSummary(metric(item => item.triangles?.p50))),
    programsP50: roundedSummary(numericSummary(metric(item => item.programCount?.p50))),
  };
}

function regressionBudget(metric, comparator) {
  if (metric === null || metric.count < 3) return null;
  const observedRange = metric.max - metric.min;
  const limit = comparator === "max" ? metric.max + observedRange : Math.max(0, metric.min - observedRange);
  return { comparator, limit: round(limit), observedRange: round(observedRange) };
}

function createAnalysis(repetitions, gpuStatus) {
  const grouped = new Map();
  for (const repetition of repetitions) {
    const key = `${repetition.camera}\u0000${repetition.caseName}`;
    const list = grouped.get(key) ?? [];
    list.push(repetition);
    grouped.set(key, list);
  }
  const aggregates = [];
  for (const [key, values] of grouped) {
    const [camera, caseName] = key.split("\u0000");
    aggregates.push({ camera, caseName, ...aggregateRepetitions(values) });
  }

  const rankedCosts = [];
  for (const camera of [...new Set(repetitions.map(item => item.camera))]) {
    const baseline = repetitions.filter(item => item.camera === camera && item.caseName === "baseline");
    for (const caseName of CASES.filter(value => value !== "baseline")) {
      const variant = repetitions.filter(item => item.camera === camera && item.caseName === caseName);
      const deltas = [];
      for (const base of baseline) {
        const candidate = variant.find(item => item.repetition === base.repetition);
        if (candidate === undefined) continue;
        const source = gpuStatus === "available" && base.gpuFrameMs !== null && candidate.gpuFrameMs !== null
          ? "gpuFrameP50Ms" : "frameIntervalP50Ms";
        const baseValue = source === "gpuFrameP50Ms" ? base.gpuFrameMs.p50 : base.frameIntervalMs.p50;
        const candidateValue = source === "gpuFrameP50Ms" ? candidate.gpuFrameMs.p50 : candidate.frameIntervalMs.p50;
        deltas.push({ repetition: base.repetition, source, baselineMs: baseValue,
          variantMs: candidateValue, estimatedCostMs: baseValue - candidateValue });
      }
      if (deltas.length === 0) continue;
      const costs = deltas.map(item => item.estimatedCostMs);
      rankedCosts.push({
        camera,
        isolatedCase: caseName,
        metric: deltas[0].source,
        estimatedCostMsP50: round(percentile(costs, 0.5)),
        spreadMinMs: round(Math.min(...costs)),
        spreadMaxMs: round(Math.max(...costs)),
        directionConsistent: costs.every(value => value >= 0) || costs.every(value => value <= 0),
        pairedRepetitions: deltas,
        interpretation: "Baseline minus isolated-case result; positive values mean the enabled feature cost time in this run.",
      });
    }
  }
  rankedCosts.sort((left, right) => right.estimatedCostMsP50 - left.estimatedCostMsP50);

  const baseline = repetitions.filter(item => item.caseName === "baseline");
  const observedEnvelope = items => {
    const metric = path => numericSummary(items.flatMap(item => {
      const value = path(item);
      return value === null || value === undefined ? [] : [value];
    }));
    return {
      fps: roundedSummary(metric(item => item.fpsFromMeanInterval)),
      frameIntervalP95Ms: roundedSummary(metric(item => item.frameIntervalMs?.p95)),
      mapFrameCpuP50Ms: roundedSummary(metric(item => item.mapFrameCpuMs?.p50)),
      gpuFrameP50Ms: roundedSummary(metric(item => item.gpuFrameMs?.p50)),
      drawCallsP50: roundedSummary(metric(item => item.drawCalls?.p50)),
      trianglesP50: roundedSummary(metric(item => item.triangles?.p50)),
    };
  };
  const observed = observedEnvelope(baseline);
  const cameraBudgets = [...new Set(baseline.map(item => item.camera))].map(camera => {
    const cameraObserved = observedEnvelope(baseline.filter(item => item.camera === camera));
    return {
      camera,
      observedEnvelope: cameraObserved,
      minFps: regressionBudget(cameraObserved.fps, "min"),
      maxFrameIntervalP95Ms: regressionBudget(cameraObserved.frameIntervalP95Ms, "max"),
      maxMapFrameCpuP50Ms: regressionBudget(cameraObserved.mapFrameCpuP50Ms, "max"),
      maxGpuFrameP50Ms: regressionBudget(cameraObserved.gpuFrameP50Ms, "max"),
      maxDrawCallsP50: regressionBudget(cameraObserved.drawCallsP50, "max"),
      maxTrianglesP50: regressionBudget(cameraObserved.trianglesP50, "max"),
    };
  });
  return {
    aggregates,
    rankedCosts,
    baselineObservedEnvelope: observed,
    proposedDiagnosticRegressionBudgets: {
      scope: "Provisional same-machine regression guards, not product frame-rate targets.",
      formula: "For max metrics: worst observed baseline plus one full observed baseline range. For minimum FPS: worst observed baseline minus one full observed baseline range.",
      assumptions: [
        "The stable published bundle, browser launch flags, viewport, device scale factor, camera, and GPU stay fixed.",
        "Three repetitions capture short-run spread but not day-to-day or thermal variance.",
        "Do not enforce these guards until a second independent uncontended run confirms the envelope.",
      ],
      byCamera: cameraBudgets,
    },
  };
}

async function hookBundle(page) {
  let hookedBundle = null;
  let identity = null;
  // Vite source server: expose the map from the app module itself.
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const token = "this.map = new PublicTraceMap(";
    if (source.split(token).length !== 2) throw new Error("Frame-profiler source map hook no longer matches exactly once");
    hookedBundle = new URL(route.request().url()).pathname;
    identity = { path: hookedBundle, sha256: createHash("sha256").update(source).digest("hex"),
      sizeBytes: Buffer.byteLength(source) };
    await route.fulfill({ response, body: source.replace(token, "window.__aeroVisualMap = this.map = new PublicTraceMap(") });
  });
  await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
    if (!pattern.test(source)) throw new Error("Frame-profiler map hook no longer matches the published bundle");
    hookedBundle = new URL(route.request().url()).pathname;
    identity = { path: hookedBundle, sha256: createHash("sha256").update(source).digest("hex"),
      sizeBytes: Buffer.byteLength(source) };
    await route.fulfill({ response, body: source.replace(pattern,
      match => match.replace("this.map=", "window.__aeroVisualMap=this.map=")) });
  });
  return Object.assign(() => hookedBundle, { identity: () => identity });
}

async function waitForSceneReady(page) {
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return (data?.sceneReady === "true" && data?.skyReady === "true") || data?.sceneError === "true";
  }, undefined, { timeout: 300000 });
  const error = await page.locator("#city-map").getAttribute("data-scene-error-message");
  if (error) throw new Error(`City scene failed: ${error}`);
}

/** Installed inside the browser after the scene is ready. */
function installProfiler() {
  const map = window.__aeroVisualMap;
  if (!map) throw new Error("Frame profiler did not receive the exposed map");
  const renderer = map.renderer;
  const gl = renderer.getContext();
  const timerExtension = gl.getExtension("EXT_disjoint_timer_query_webgl2");
  const state = {
    active: false,
    label: "",
    frames: [],
    current: null,
    previousStart: null,
    pendingQueries: [],
    gpuStatus: timerExtension === null ? "unavailable" : "available",
    gpuFailure: timerExtension === null ? "EXT_disjoint_timer_query_webgl2 is not exposed" : null,
    querySequence: 0,
  };

  const pollQueries = () => {
    if (timerExtension === null || state.pendingQueries.length === 0) return;
    if (gl.getParameter(timerExtension.GPU_DISJOINT_EXT)) {
      state.gpuStatus = "disjoint";
      state.gpuFailure = "WebGL reported GPU_DISJOINT_EXT while timer queries were pending";
      for (const pending of state.pendingQueries) gl.deleteQuery(pending.query);
      state.pendingQueries.length = 0;
      for (const frame of state.frames) frame.gpuMs = null;
      return;
    }
    for (let index = state.pendingQueries.length - 1; index >= 0; index--) {
      const pending = state.pendingQueries[index];
      if (!gl.getQueryParameter(pending.query, gl.QUERY_RESULT_AVAILABLE)) continue;
      const nanoseconds = Number(gl.getQueryParameter(pending.query, gl.QUERY_RESULT));
      pending.frame.gpuMs = (pending.frame.gpuMs ?? 0) + nanoseconds / 1e6;
      pending.frame.gpuQueryResults += 1;
      gl.deleteQuery(pending.query);
      state.pendingQueries.splice(index, 1);
    }
  };

  const originalRendererRender = renderer.render.bind(renderer);
  renderer.render = function profiledRendererRender(...args) {
    const frame = state.active ? state.current : null;
    let query = null;
    if (frame !== null && timerExtension !== null && state.gpuStatus === "available") {
      try {
        query = gl.createQuery();
        if (query === null) throw new Error("gl.createQuery returned null");
        gl.beginQuery(timerExtension.TIME_ELAPSED_EXT, query);
      } catch (error) {
        if (query !== null) gl.deleteQuery(query);
        query = null;
        state.gpuStatus = "error";
        state.gpuFailure = `GPU timer query start failed: ${error instanceof Error ? error.message : String(error)}`;
      }
    }
    const started = performance.now();
    try {
      return originalRendererRender(...args);
    } finally {
      const elapsed = performance.now() - started;
      if (frame !== null) {
        frame.rendererSubmitCpuMs += elapsed;
        frame.rendererInvocationCount += 1;
      }
      if (query !== null) {
        try {
          gl.endQuery(timerExtension.TIME_ELAPSED_EXT);
          frame.gpuQueryCount += 1;
          state.pendingQueries.push({ query, frame, sequence: state.querySequence++ });
        } catch (error) {
          gl.deleteQuery(query);
          state.gpuStatus = "error";
          state.gpuFailure = `GPU timer query end failed: ${error instanceof Error ? error.message : String(error)}`;
        }
      }
      pollQueries();
    }
  };

  const wrapTimed = (owner, key, field, captureResult = false) => {
    if (owner === null || owner === undefined || typeof owner[key] !== "function") return false;
    const original = owner[key].bind(owner);
    owner[key] = function profiledSection(...args) {
      const started = performance.now();
      try {
        const result = original(...args);
        if (captureResult && state.current !== null && result === true) state.current.reflectionCaptureCount += 1;
        return result;
      } finally {
        if (state.current !== null) state.current[field] += performance.now() - started;
      }
    };
    return true;
  };

  const sections = {
    trafficUpdate: wrapTimed(map.trafficPreview, "update", "trafficUpdateCpuMs"),
    vegetationUpdate: wrapTimed(map.cityVegetationLayer, "update", "vegetationUpdateCpuMs"),
    reflectionRefresh: wrapTimed(map.localReflections, "refresh", "reflectionRefreshCpuMs", true),
    sunShadowFocus: wrapTimed(map, "focusSunShadow", "sunShadowFocusCpuMs"),
    streamUpdate: wrapTimed(map.renderStreamer, "update", "streamUpdateCpuMs"),
  };

  const originalFrame = map.renderPreviewFrame.bind(map);
  map.renderPreviewFrame = function profiledPreviewFrame(...args) {
    if (!state.active) return originalFrame(...args);
    const frameStarted = performance.now();
    const frame = {
      sequence: state.frames.length,
      frameIntervalMs: state.previousStart === null ? null : frameStarted - state.previousStart,
      mapFrameCpuMs: 0,
      rendererSubmitCpuMs: 0,
      rendererInvocationCount: 0,
      trafficUpdateCpuMs: 0,
      vegetationUpdateCpuMs: 0,
      reflectionRefreshCpuMs: 0,
      reflectionCaptureCount: 0,
      sunShadowFocusCpuMs: 0,
      streamUpdateCpuMs: 0,
      gpuMs: timerExtension === null ? null : 0,
      gpuQueryCount: 0,
      gpuQueryResults: 0,
    };
    state.previousStart = frameStarted;
    state.current = frame;
    try {
      return originalFrame(...args);
    } finally {
      frame.mapFrameCpuMs = performance.now() - frameStarted;
      frame.drawCalls = renderer.info.render.calls;
      frame.triangles = renderer.info.render.triangles;
      frame.geometries = renderer.info.memory.geometries;
      frame.textures = renderer.info.memory.textures;
      frame.programCount = renderer.info.programs?.length ?? null;
      frame.pixelRatio = renderer.getPixelRatio();
      frame.shadowMapEnabled = renderer.shadowMap.enabled;
      frame.vegetationVisible = (map.vegetationPresentation?.visible ?? true)
        && (map.cityVegetationLayer?.group.visible ?? true);
      frame.trafficVisible = map.trafficPreview?.group.visible ?? false;
      state.frames.push(frame);
      state.current = null;
      pollQueries();
    }
  };

  const rendererInfo = gl.getExtension("WEBGL_debug_renderer_info");
  const rendererName = String(gl.getParameter(rendererInfo?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  const countSceneObjects = () => {
    let shadowCasters = 0;
    let shadowLights = 0;
    let visibleObjects = 0;
    map.scene.traverse(object => {
      if (object.visible) visibleObjects += 1;
      if (object.visible && object.castShadow) shadowCasters += 1;
      if (object.visible && object.isLight && object.castShadow) shadowLights += 1;
    });
    return { shadowCasters, shadowLights, visibleObjects };
  };
  const streamerProgress = () => {
    if (map.renderStreamer === null) return null;
    const progress = map.renderStreamer.progress;
    const pending = progress.total - progress.loaded - progress.active - progress.failed;
    return { ...progress, pending,
      terminal: progress.active === 0 && pending === 0 };
  };

  window.__aeroFrameProfiler = {
    metadata: {
      renderer: rendererName,
      webglVersion: String(gl.getParameter(gl.VERSION)),
      shadingLanguageVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION)),
      gpuTimerStatus: state.gpuStatus,
      gpuTimerFailure: state.gpuFailure,
      sections,
      originalPixelRatio: renderer.getPixelRatio(),
      drawingBuffer: { width: gl.drawingBufferWidth, height: gl.drawingBufferHeight },
      sceneObjects: countSceneObjects(),
      initialStreamerProgress: streamerProgress(),
    },
    start(label) {
      if (state.active) throw new Error("Frame profiler is already active");
      state.label = label;
      state.frames = [];
      state.previousStart = null;
      state.active = true;
    },
    frameCount() { return state.frames.length; },
    async stop() {
      state.active = false;
      state.current = null;
      if (timerExtension !== null && state.pendingQueries.length > 0 && state.gpuStatus === "available") {
        gl.finish();
        const deadline = performance.now() + 5000;
        while (state.pendingQueries.length > 0 && performance.now() < deadline) {
          pollQueries();
          if (state.pendingQueries.length > 0) await new Promise(resolve => setTimeout(resolve, 10));
        }
        if (state.pendingQueries.length > 0) {
          state.gpuStatus = "timeout";
          state.gpuFailure = `${state.pendingQueries.length} GPU timer queries remained unavailable after gl.finish`;
          for (const pending of state.pendingQueries) gl.deleteQuery(pending.query);
          state.pendingQueries.length = 0;
          for (const frame of state.frames) frame.gpuMs = null;
        }
      }
      if (state.gpuStatus !== "available") {
        for (const pending of state.pendingQueries) gl.deleteQuery(pending.query);
        state.pendingQueries.length = 0;
        for (const frame of state.frames) frame.gpuMs = null;
      }
      return {
        label: state.label,
        frames: state.frames,
        gpuStatus: state.gpuStatus,
        gpuFailure: state.gpuFailure,
      };
    },
    streamerProgress,
    async waitForSceneQuiescence(timeoutMs) {
      const streamer = map.renderStreamer;
      if (streamer === null) {
        return { status: "not-present", elapsedMs: 0, before: null, after: null };
      }
      const before = streamerProgress();
      const started = performance.now();
      let timer = 0;
      try {
        await Promise.race([
          streamer.loadAll(),
          new Promise((_, reject) => {
            timer = window.setTimeout(() => reject(new Error(
              `Building render streamer did not settle within ${timeoutMs} ms`)), timeoutMs);
          }),
        ]);
      } finally {
        window.clearTimeout(timer);
      }
      const after = streamerProgress();
      if (after === null || !after.terminal) {
        throw new Error(`Building render streamer returned before terminal progress: ${JSON.stringify(after)}`);
      }
      if (after.failed !== 0 || after.loaded !== after.total) {
        throw new Error(`Building render streamer completed with missing assets: ${JSON.stringify(after)}`);
      }
      return { status: "complete", elapsedMs: performance.now() - started, before, after };
    },
    applyCamera(camera) {
      map.previewFollowId = null;
      map.setCameraMode("free");
      map.camera.position.set(...camera.eye);
      map.controls.target.set(...camera.target);
      map.controls.update();
      // Hold one recorded default-scene state across every case. The production update
      // functions still run every frame, but feature deltas are not confounded by different
      // traffic populations or camera-relative LOD states later in the 120 s replay.
      map.previewSeconds = 30;
      map.previewPlaybackRate = 0;
      map.configureCityPresentation({ mood: "day", reflectionsEnabled: true });
      map.previewLastFrameAt = performance.now();
    },
    applyCase(caseName) {
      const baselinePixelRatio = this.metadata.originalPixelRatio;
      map.previewSeconds = 30;
      map.previewPlaybackRate = 0;
      renderer.shadowMap.enabled = caseName !== "no-shadows";
      renderer.shadowMap.needsUpdate = true;
      renderer.setPixelRatio(caseName === "pixel-ratio-1" ? 1 : baselinePixelRatio);
      if (map.vegetationPresentation !== null) map.vegetationPresentation.visible = caseName !== "no-vegetation";
      if (map.cityVegetationLayer !== null) map.cityVegetationLayer.group.visible = caseName !== "no-vegetation";
      if (map.trafficPreview !== null) map.trafficPreview.group.visible = caseName !== "no-traffic";
      map.configureCityPresentation({ mood: "day", reflectionsEnabled: caseName !== "no-reflections" });
      map.previewLastFrameAt = performance.now();
      return {
        caseName,
        pixelRatio: renderer.getPixelRatio(),
        drawingBuffer: { width: gl.drawingBufferWidth, height: gl.drawingBufferHeight },
        shadowMapEnabled: renderer.shadowMap.enabled,
        vegetationVisible: (map.vegetationPresentation?.visible ?? true)
          && (map.cityVegetationLayer?.group.visible ?? true),
        trafficVisible: map.trafficPreview?.group.visible ?? false,
        sceneObjects: countSceneObjects(),
      };
    },
    async measureProbeTransition() {
      if (map.localReflections === null) return { status: "unavailable", reason: "local reflection owner is absent" };
      const wasPlaying = map.previewPlaying;
      map.previewPlaying = false;
      map.localReflections.invalidate();
      this.start("reflection-probe-transition");
      map.renderPreviewFrame();
      const result = await this.stop();
      map.previewPlaying = wasPlaying;
      map.previewLastFrameAt = performance.now();
      map.syncPreviewAnimation();
      const frame = result.frames[0];
      return {
        status: frame?.reflectionCaptureCount > 0 ? "captured" : "no-dirty-probe",
        gpuStatus: result.gpuStatus,
        gpuFailure: result.gpuFailure,
        frame: frame ?? null,
      };
    },
  };
}

async function collectFrames(page, label, frameCount) {
  await page.evaluate(({ label }) => window.__aeroFrameProfiler.start(label), { label });
  await page.waitForFunction(goal => window.__aeroFrameProfiler.frameCount() >= goal,
    frameCount + 1, { timeout: 300000 });
  const result = await page.evaluate(() => window.__aeroFrameProfiler.stop());
  return { ...result, frames: result.frames.slice(1, frameCount + 1) };
}

async function runCamera(page, camera, options, progress) {
  await page.evaluate(cameraArg => window.__aeroFrameProfiler.applyCamera(cameraArg), camera);
  await page.evaluate(() => window.__aeroFrameProfiler.applyCase("baseline"));
  const settle = await collectFrames(page, `${camera.name}/camera-settle`, options.warmupFrames);
  const cameraSettle = {
    frameCount: settle.frames.length,
    rendererCounterStability: rendererCounterStability(settle.frames),
    streamerProgress: await page.evaluate(() => window.__aeroFrameProfiler.streamerProgress()),
  };
  const transition = await page.evaluate(() => window.__aeroFrameProfiler.measureProbeTransition());
  const repetitions = [];
  for (let repetition = 1; repetition <= options.repetitions; repetition++) {
    for (const caseName of options.cases) {
      const applied = await page.evaluate(caseArg => window.__aeroFrameProfiler.applyCase(caseArg), caseName);
      await collectFrames(page, `${camera.name}/${caseName}/r${repetition}/warmup`, options.warmupFrames);
      const measured = await collectFrames(page, `${camera.name}/${caseName}/r${repetition}`, options.frames);
      const summary = summarizeFrames(measured.frames, { camera: camera.name, caseName, repetition });
      summary.applied = applied;
      summary.gpuStatus = measured.gpuStatus;
      summary.gpuFailure = measured.gpuFailure;
      summary.streamerProgress = await page.evaluate(() => window.__aeroFrameProfiler.streamerProgress());
      repetitions.push({ summary, frames: measured.frames });
      await progress(summary);
    }
  }
  return { camera: camera.name, cameraSettle, reflectionProbeTransition: transition, repetitions };
}

async function main() {
const args = parseArgs(process.argv.slice(2));
const origin = args.origin ?? "https://127.0.0.1:5209";
const scenePath = args.scene ?? "/city-presentation/default-scene-v1.json";
const hostCondition = args["host-condition"] ?? "unassessed";
if (!["unassessed", "contended", "uncontended"].includes(hostCondition)) {
  throw new Error(`--host-condition must be unassessed, contended, or uncontended, got ${hostCondition}`);
}
const camerasPath = resolve(args.cameras ?? "validation/platform-plan-20261001/E1-browser-v2/cameras.json");
const output = resolve(args.output ?? "validation/codex-takeover-20261001/F");
const frames = integerOption(args, "frames", 300, 300);
const repetitions = integerOption(args, "repetitions", 3, 3);
const warmupFrames = integerOption(args, "warmup-frames", 45, 1);
const calibrationFrames = integerOption(args, "calibration-frames", 90, 30);
const streamingTimeoutMs = integerOption(args, "streaming-timeout-ms", 300000, 1000);
const cameraMode = args["camera-mode"] ?? "representative";
if (!["representative", "all"].includes(cameraMode)) {
  throw new Error(`--camera-mode must be representative or all, got ${cameraMode}`);
}
const requestedCases = args.cases?.split(",").filter(Boolean) ?? CASES;
if (requestedCases.length === 0 || requestedCases.some(name => !CASES.includes(name))) {
  throw new Error(`--cases must contain only: ${CASES.join(", ")}`);
}

const allCameras = JSON.parse(await readFile(camerasPath, "utf8"));
if (!Array.isArray(allCameras) || allCameras.some(camera => typeof camera.name !== "string"
    || !Array.isArray(camera.eye) || !Array.isArray(camera.target))) {
  throw new Error("Camera file must be an array of named eye/target views");
}
const requestedCameraNames = args["camera-names"]?.split(",").filter(Boolean)
  ?? (cameraMode === "all" ? allCameras.map(camera => camera.name) : REPRESENTATIVE_CAMERAS);
const cameras = requestedCameraNames.map(name => {
  const camera = allCameras.find(candidate => candidate.name === name);
  if (camera === undefined) throw new Error(`Camera ${name} is absent from ${camerasPath}`);
  return camera;
});
if (cameras.length === 0) throw new Error("At least one camera is required");

await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chromium", headless: true, args: GPU_ARGS });
const context = await browser.newContext({
  ignoreHTTPSErrors: true,
  viewport: { width: 1600, height: 1000 },
  deviceScaleFactor: 1.5,
});
const page = await context.newPage();
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));
// A source server can reload the page when another process edits a module; any navigation
// after the first load invalidates the run instead of silently re-measuring a fresh page.
const navigations = [];
page.on("framenavigated", frame => { if (frame === page.mainFrame()) navigations.push(frame.url()); });
const sourceFiles = ["src/map.ts", "src/app.ts", "src/city-presentation.ts", "src/city-vegetation-layer.ts",
  "src/city-local-reflections.ts"];
const hashSources = async () => Object.fromEntries(await Promise.all(sourceFiles.map(async file => {
  try {
    const bytes = await readFile(resolve(new URL("..", import.meta.url).pathname, file));
    return [file, createHash("sha256").update(bytes).digest("hex")];
  } catch (error) {
    return [file, `unreadable: ${error.message}`];
  }
})));
const sourceHashesAtStart = await hashSources();
const getHookedBundle = await hookBundle(page);
const runStarted = performance.now();
let report;
try {
  const loadStarted = performance.now();
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scenePath)}`, { waitUntil: "domcontentloaded" });
  await waitForSceneReady(page);
  const sceneLoadSeconds = (performance.now() - loadStarted) / 1000;
  await page.evaluate(installProfiler);
  const metadata = await page.evaluate(() => window.__aeroFrameProfiler.metadata);
  if (/swiftshader|llvmpipe|software/i.test(metadata.renderer)) {
    throw new Error(`Hardware GPU is required; browser reported ${metadata.renderer}`);
  }

  await page.evaluate(camera => window.__aeroFrameProfiler.applyCamera(camera), cameras[0]);
  await page.evaluate(() => window.__aeroFrameProfiler.applyCase("baseline"));
  const sceneQuiescence = await page.evaluate(timeoutMs =>
    window.__aeroFrameProfiler.waitForSceneQuiescence(timeoutMs), streamingTimeoutMs);
  if (scenePath === "/city-presentation/default-scene-v1.json" && sceneQuiescence.status !== "complete") {
    throw new Error(`Default scene has no completed building render streamer: ${JSON.stringify(sceneQuiescence)}`);
  }
  await collectFrames(page, "calibration/warmup", warmupFrames);
  const calibration = await collectFrames(page, "calibration/measured", calibrationFrames);
  const calibrationCounterStability = rendererCounterStability(calibration.frames);
  if (calibrationCounterStability.status !== "stable") {
    throw new Error(`Renderer counters drifted after scene quiescence: ${JSON.stringify(calibrationCounterStability)}`);
  }
  const calibrationIntervals = calibration.frames.map(frame => frame.frameIntervalMs).filter(Number.isFinite);
  const calibrationIntervalMean = numericSummary(calibrationIntervals)?.mean;
  if (!(calibrationIntervalMean > 0)) throw new Error("Calibration produced no frame intervals");
  const observedFps = 1000 / calibrationIntervalMean;
  const matrixFrameCount = cameras.length * requestedCases.length * repetitions * (warmupFrames + frames + 2);
  const cameraSettleFrameCount = cameras.length * (warmupFrames + 1);
  const transitionFrameAllowance = cameras.length;
  const estimatedMatrixSeconds = (matrixFrameCount + transitionFrameAllowance) / observedFps;
  const estimatedTotalSeconds = sceneLoadSeconds + sceneQuiescence.elapsedMs / 1000
    + (warmupFrames + calibrationFrames + 2 + cameraSettleFrameCount) / observedFps + estimatedMatrixSeconds;
  const runPlan = {
    status: "CALIBRATED",
    hostCondition,
    origin,
    scenePath,
    publishedBundle: getHookedBundle(),
    publishedBundleIdentity: getHookedBundle.identity(),
    cameras: cameras.map(camera => camera.name),
    cases: requestedCases,
    repetitions,
    measuredFramesPerRepetition: frames,
    warmupFramesPerCase: warmupFrames,
    calibrationFrames,
    streamingTimeoutMs,
    sceneLoadSeconds: round(sceneLoadSeconds),
    sceneQuiescence: { ...sceneQuiescence, elapsedMs: round(sceneQuiescence.elapsedMs) },
    calibrationCounterStability,
    calibrationObservedFps: round(observedFps),
    plannedMeasuredFrameCount: cameras.length * requestedCases.length * repetitions * frames,
    plannedWarmupFrameCount: cameras.length * requestedCases.length * repetitions * warmupFrames,
    plannedCameraSettleFrameCount: cameraSettleFrameCount,
    estimatedMatrixSeconds: round(estimatedMatrixSeconds),
    estimatedTotalSeconds: round(estimatedTotalSeconds),
    estimateFormula: "Observed calibration FPS applied to all planned measured, warm-up, camera-settle, and transition frames, plus measured scene-load and streamer-quiescence time.",
    estimateCaveat: hostCondition === "uncontended"
      ? "Case costs vary; the run is scheduled for an uncontended interval."
      : "Case costs vary, and unrelated GPU or host load can extend the observed duration.",
  };
  await writeFile(resolve(output, "run-plan.json"), `${JSON.stringify(runPlan, null, 2)}\n`);
  console.log(JSON.stringify({ event: "calibrated", ...runPlan }));

  const raw = [];
  const summaries = [];
  const cameraResults = [];
  const progress = async summary => {
    summaries.push(summary);
    console.log(JSON.stringify({ event: "repetition-complete", camera: summary.camera,
      caseName: summary.caseName, repetition: summary.repetition, fps: summary.fpsFromMeanInterval,
      frameP95Ms: summary.frameIntervalMs.p95, gpuP50Ms: summary.gpuFrameMs?.p50 ?? null,
      gpuStatus: summary.gpuStatus }));
  };
  for (const camera of cameras) {
    const result = await runCamera(page, camera,
      { cases: requestedCases, repetitions, warmupFrames, frames }, progress);
    cameraResults.push({ camera: result.camera, cameraSettle: result.cameraSettle,
      reflectionProbeTransition: result.reflectionProbeTransition });
    for (const repetition of result.repetitions) {
      raw.push({ camera: result.camera, caseName: repetition.summary.caseName,
        repetition: repetition.summary.repetition, frames: repetition.frames });
    }
  }
  const gpuStatuses = [...new Set(summaries.map(item => item.gpuStatus))];
  const gpuStatus = gpuStatuses.length === 1 ? gpuStatuses[0] : "mixed";
  const analysis = createAnalysis(summaries, gpuStatus);
  const failures = [];
  const unstableRepetitions = summaries.filter(summary =>
    summary.rendererCounterStability.status !== "stable").map(summary =>
    ({ camera: summary.camera, caseName: summary.caseName, repetition: summary.repetition,
      driftedFields: summary.rendererCounterStability.driftedFields }));
  const matchedCounterDrift = matchedSceneCounterDrift(summaries);
  const sourceHashesAtEnd = await hashSources();
  const changedSources = sourceFiles.filter(file => sourceHashesAtStart[file] !== sourceHashesAtEnd[file]);
  if (navigations.length !== 1) failures.push(`page navigated ${navigations.length} times (expected exactly the initial load)`);
  if (pageErrors.length > 0) failures.push(`${pageErrors.length} page error(s)`);
  if (summaries.some(item => item.frameCount < frames)) failures.push("one or more repetitions retained too few frames");
  if (summaries.length !== cameras.length * requestedCases.length * repetitions) {
    failures.push("the completed repetition count differs from the planned matrix");
  }
  if (unstableRepetitions.length > 0) {
    failures.push(`${unstableRepetitions.length} measured repetition(s) had renderer counter drift`);
  }
  if (matchedCounterDrift.length > 0) {
    failures.push(`${matchedCounterDrift.length} matched scene counter(s) differed across repetitions`);
  }
  if (summaries.some(summary => sceneQuiescence.status === "complete"
      && (summary.streamerProgress === null || !summary.streamerProgress.terminal
        || summary.streamerProgress.failed !== 0))) {
    failures.push("building render streamer left its complete terminal state during the matrix");
  }
  analysis.proposedDiagnosticRegressionBudgets.validity = failures.length === 0
    ? "VALID_FOR_THIS_DIAGNOSTIC_RUN"
    : "INVALID_DUE_TO_RUN_FAILURES";
  report = {
    status: failures.length === 0 ? "PASS" : "FAIL",
    failures,
    scope: "No-recording diagnostic profile of the stable published city preview",
    hostCondition,
    acceptanceUse: failures.length > 0 ? "INVALID_DUE_TO_RUN_FAILURES"
      : hostCondition === "contended" ? "DIAGNOSTIC_ONLY_PENDING_AN_INDEPENDENT_UNCONTENDED_CONFIRMATION"
        : "PROVISIONAL_PENDING_AN_INDEPENDENT_CONFIRMATION",
    hostPressureEvidence: "host-pressure.json",
    snapshotNote: "The profiler did not rebuild or publish the frontend; publishedBundle identifies the exact stable snapshot measured by this run.",
    elapsedSeconds: round((performance.now() - runStarted) / 1000),
    origin,
    scenePath,
    publishedBundle: getHookedBundle(),
    publishedBundleIdentity: getHookedBundle.identity(),
    servedFrom: getHookedBundle()?.startsWith("/src/") ? "vite-source-server" : "published-bundle",
    sourceHashesAtStart,
    sourceHashesAtEnd,
    sourceFilesChangedDuringRun: changedSources,
    sourceChangeNote: "A changed hash means the file was edited during the run; the measured page kept the modules it loaded unless a navigation was recorded.",
    navigations,
    camerasFile: camerasPath,
    cameras: cameras.map(camera => camera.name),
    cases: requestedCases,
    repetitions,
    measuredFramesPerRepetition: frames,
    warmupFramesPerCase: warmupFrames,
    totalMeasuredFrames: summaries.reduce((sum, item) => sum + item.frameCount, 0),
    sceneQuiescence: { ...sceneQuiescence, elapsedMs: round(sceneQuiescence.elapsedMs) },
    calibrationCounterStability,
    rendererCounterValidity: {
      status: unstableRepetitions.length === 0 && matchedCounterDrift.length === 0 ? "stable" : "drifted",
      unstableRepetitions,
      matchedCounterDrift,
    },
    viewportCssPixels: { width: 1600, height: 1000 },
    deviceScaleFactor: 1.5,
    metadata,
    gpuTimerStatus: gpuStatus,
    gpuTimerFailures: [...new Set(summaries.map(item => item.gpuFailure).filter(Boolean))],
    pageErrors,
    cameraTransitions: cameraResults,
    repetitionsSummary: summaries,
    ...analysis,
    isolationNotes: {
      controlledReplayState: "Playback remains active but its rate is zero at recorded time 30 s, so every case renders and updates the same traffic and vegetation state.",
      measurementOverhead: "Frame cadence and CPU sections include the profiler wrappers and one WebGL elapsed-time query per renderer.render call; GPU values are the query results themselves.",
      shaderAccounting: "renderer.info.programs is recorded per frame. Three.js exposes the live program count here, not a separate steady-state shader-update duration.",
      sceneQuiescence: "The default building streamer must load every declared building without failures before calibration. Renderer resource and draw counters must then remain stable within each measured repetition and match across repetitions of the same camera/case.",
      shadows: "Disables renderer.shadowMap while keeping scene lights and geometry fixed.",
      reflections: "Uses the production presentation option; steady frames exclude the separately reported dirty-probe transition.",
      vegetation: "Hides both vegetation roots. Their production LOD/wind update remains active and is reported as CPU time.",
      traffic: "Hides the SUMO preview root. Production traffic interpolation remains active and is reported as CPU time.",
      pixelRatio: "Compares the 1.5 production cap under deviceScaleFactor 1.5 with pixel ratio 1.0.",
    },
  };
  await writeFile(resolve(output, "samples.json"), `${JSON.stringify(raw)}\n`);
  await writeFile(resolve(output, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify({ event: "complete", status: report.status, elapsedSeconds: report.elapsedSeconds,
    totalMeasuredFrames: report.totalMeasuredFrames, gpuTimerStatus: report.gpuTimerStatus,
    topCosts: report.rankedCosts.slice(0, 6) }));
  if (failures.length > 0) process.exitCode = 1;
} catch (error) {
  await writeFile(resolve(output, "report.json"), `${JSON.stringify({
    status: "FAIL", acceptanceUse: "INVALID_DUE_TO_RUN_FAILURES",
    failures: [String(error?.stack ?? error)], origin, scenePath, hostCondition,
    publishedBundle: getHookedBundle(), publishedBundleIdentity: getHookedBundle.identity(),
    elapsedSeconds: round((performance.now() - runStarted) / 1000),
    pageErrors, navigations, proposedDiagnosticRegressionBudgets: null,
  }, null, 2)}\n`);
  throw error;
} finally {
  await page.close();
  await context.close();
  await browser.close();
}
}

if (process.argv[1] === new URL(import.meta.url).pathname) await main();
