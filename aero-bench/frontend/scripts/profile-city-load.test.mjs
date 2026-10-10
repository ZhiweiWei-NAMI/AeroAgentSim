import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { categorize, installLoadObservers, parseArguments, sliceCpuProfile, summarizeCpuProfile,
  summarizeLongTasks, summarizeResources } from "./profile-city-load.mjs";

const resource = (name, startTime, requestStart, responseStart, responseEnd, extra = {}) => ({
  name, startTime, fetchStart: startTime, requestStart, responseStart, responseEnd,
  encodedBodySize: 10, decodedBodySize: 20, transferSize: 30, nextHopProtocol: "h2", ...extra,
});

test("categorize uses path extensions, ignores queries and handles uppercase", () => {
  for (const extension of ["glb", "gltf", "bin", "json", "js", "css", "wasm"]) {
    assert.equal(categorize(`https://example.com/model.${extension.toUpperCase()}?x=.png#f`), extension);
  }
  for (const extension of ["webp", "png", "jpg", "jpeg", "ktx2", "basis"]) assert.equal(categorize(`/a.${extension}`), "image");
  for (const extension of ["woff", "woff2", "ttf", "otf", "eot"]) assert.equal(categorize(`/a.${extension}`), "font");
  assert.equal(categorize("/a.HTML?x=.glb"), "html");
  assert.equal(categorize("/a.htm"), "html");
  assert.equal(categorize("/api?file=a.glb"), "other");
  assert.equal(categorize("/folder.glb/no-extension"), "other");
  assert.equal(categorize("/glb"), "other");
});

test("resource summary keeps no-detail entries out of nearest-rank distributions", () => {
  const result = summarizeResources([
    resource("/a.glb", 0, 10, 20, 40),
    resource("/b.GLB?q=1", 5, 25, 30, 80),
    resource("/c.glb", 10, 0, 0, 15, { nextHopProtocol: "" }),
    resource("/d.js", 40, 41, 42, 45, { nextHopProtocol: "http/1.1" }),
  ], 45);
  assert.deepEqual(result.categories.glb, { count: 3, encodedBytes: 30, decodedBytes: 60,
    transferBytes: 90, noDetailedTiming: 1, lastResponseEnd: 80,
    queueing: { p50: 10, p95: 20, max: 20 }, download: { p50: 20, p95: 50, max: 50 } });
  assert.equal(result.maxInFlight, 3);
  assert.equal(result.noDetailedTiming, 1);
  assert.equal(result.afterSceneReadyShare, 0.25);
  assert.deepEqual(result.protocols, ["", "h2", "http/1.1"]);
  const many = summarizeResources(Array.from({ length: 20 }, (_, i) => resource("/a.bin", 0, i + 1, 30, 31 + i)), 100);
  assert.deepEqual(many.categories.bin.queueing, { p50: 10, p95: 19, max: 20 });
});

test("resource summary handles empty, timeout, missing detail and equal endpoints", () => {
  assert.deepEqual(summarizeResources([], null), { categories: {}, maxInFlight: 0,
    protocols: [], noDetailedTiming: 0, afterSceneReadyShare: null });
  assert.equal(summarizeResources([], 0).afterSceneReadyShare, 0);
  const result = summarizeResources([resource("/a.png", 0, 0, 0, 10), resource("/b.png", 10, 0, 0, 20),
    resource("/c.png", 10, 0, 0, 10)], 20);
  assert.equal(result.maxInFlight, 1);
  assert.deepEqual(result.categories.image.download, { p50: null, p95: null, max: null });
  assert.equal(result.afterSceneReadyShare, 0);
});

test("long-task summary clips to the scene boundary and calculates blocking time", () => {
  const tasks = [{ start: 0, duration: 60 }, { start: 60, duration: 40 },
    { start: 100, duration: 120 }, { start: 200, duration: 90 }];
  assert.deepEqual(summarizeLongTasks(tasks, 180), { count: 3, totalMs: 180, maxMs: 80, blockingMs: 40 });
  assert.deepEqual(summarizeLongTasks(tasks, null), { count: 4, totalMs: 310, maxMs: 120, blockingMs: 120 });
  assert.deepEqual(summarizeLongTasks([], 0), { count: 0, totalMs: 0, maxMs: 0, blockingMs: 0 });
});

test("CLI validates every argument and rejects unknown flags in a real process", () => {
  const defaults = parseArguments(["http://127.0.0.1:5416", "out"]);
  assert.deepEqual(defaults.paths, ["/"]);
  assert.equal(defaults.iterations, 1);
  assert.equal(defaults.gpu, false);
  assert.deepEqual(defaults.viewport, { width: 1600, height: 1000 });
  const options = parseArguments(["https://example.com", "out", "--paths=/,/?view=replay", "--iterations=2", "--gpu", "--cpu-profile", "--viewport=1440x900"]);
  assert.deepEqual(options.paths, ["/", "/?view=replay"]);
  assert.equal(options.gpu, true);
  assert.equal(options.cpuProfile, true);
  assert.equal(options.iterations, 2);
  assert.deepEqual(options.viewport, { width: 1440, height: 900 });
  for (const flag of ["--bad", "--gpu=true", "--cpu-profile=false", "--iterations=0", "--iterations=51",
    "--iterations=1.5", "--iterations=NaN", "--iterations=", "--paths=", "--paths=/,",
    "--paths=//elsewhere", "--paths=/x#fragment", "--paths=/bad%zz", "--paths=relative",
    "--viewport=0x1000", "--viewport=8193x1000", "--viewport=1600", "--viewport=1600X1000"]) {
    assert.throws(() => parseArguments(["http://127.0.0.1:5416", "out", flag]), undefined, flag);
  }
  assert.throws(() => parseArguments([]));
  assert.throws(() => parseArguments(["file:///tmp", "out"]));
  assert.throws(() => parseArguments(["http://example.com/path", "out"]));
  assert.throws(() => parseArguments(["http://example.com", "out", "--gpu", "--gpu"]));
  const child = spawnSync(process.execPath, [fileURLToPath(new URL("./profile-city-load.mjs", import.meta.url)),
    "http://127.0.0.1:5416", "out", "--bad"], { encoding: "utf8" });
  assert.equal(child.status, 1);
  assert.match(child.stderr, /Unknown or malformed flag: --bad/);
  assert.equal(child.stdout, "");
});

test("CPU sample self time uses microsecond deltas and reports one-based lines", () => {
  const profile = { nodes: [{ id: 1, callFrame: { functionName: "decode", url: "asset.js", lineNumber: 4, columnNumber: 0 } },
    { id: 2, callFrame: { functionName: "", url: "main.js", lineNumber: 0, columnNumber: 0 } }],
  samples: [1, 2, 1], timeDeltas: [1000, 2000, 3000] };
  assert.deepEqual(summarizeCpuProfile(profile), [
    { functionName: "decode", urlLine: "asset.js:5", selfMs: 4 },
    { functionName: "(anonymous)", urlLine: "main.js:1", selfMs: 2 },
  ]);
});

test("CPU profile slicing excludes pre-navigation and post-readiness samples", () => {
  const profile = { startTime: 100, endTime: 500, nodes: [{ id: 1, hitCount: 2, positionTicks: [{ line: 1, ticks: 2 }] }, { id: 2 }],
    samples: [1, 2, 1], timeDeltas: [100, 100, 100] };
  const sliced = sliceCpuProfile(profile, 150, 350);
  assert.deepEqual(sliced.samples, [1, 2, 1]);
  assert.deepEqual(sliced.timeDeltas, [50, 100, 50]);
  assert.equal(sliced.startTime, 150);
  assert.equal(sliced.endTime, 350);
  assert.equal(sliced.nodes[0].hitCount, 2);
  assert.equal(sliced.nodes[0].positionTicks, undefined);
  assert.deepEqual(sliceCpuProfile(profile, 200, 300).samples, [2]);
  assert.throws(() => sliceCpuProfile(profile, 50, 300), /does not cover/);
  assert.throws(() => sliceCpuProfile(profile, 150, 550), /does not cover/);
  const reordered = sliceCpuProfile({ ...profile, samples: [1, 2, 1], timeDeltas: [100, -20, 120] }, 100, 300);
  assert.deepEqual(reordered.samples, [2, 1, 1]);
  assert.deepEqual(reordered.timeDeltas, [80, 20, 100]);
});

test("init observer captures batched building increments and never guesses milestones", () => {
  let mutationCallback, bufferCallback;
  let now = 100;
  const map = { dataset: { buildingRenderLoaded: "2" }, querySelector: () => null,
    getAttribute: () => "2", hasAttribute: () => true };
  const global = {};
  runInNewContext(`(${installLoadObservers.toString()})()`, {
    globalThis: global,
    performance: { now: () => now, timeOrigin: 1_000_000, setResourceTimingBufferSize: size => assert.equal(size, 10000),
      addEventListener: (event, callback) => { assert.equal(event, "resourcetimingbufferfull"); bufferCallback = callback; },
      getEntriesByType: () => [] },
    document: { querySelector: selector => selector === "#city-map" ? map : null },
    MutationObserver: class { constructor(callback) { mutationCallback = callback; } observe() {} takeRecords() { return []; } },
    PerformanceObserver: class { observe() {} takeRecords() { return []; } },
  });
  global.__cityLoadProfile.buildingRenderLoaded.length = 0;
  mutationCallback([{ target: map, attributeName: "data-building-render-loaded", oldValue: "0" },
    { target: map, attributeName: "data-building-render-loaded", oldValue: "1" }]);
  bufferCallback();
  const raw = global.__cityLoadProfile.snapshot();
  assert.deepEqual(JSON.parse(JSON.stringify(raw.buildingRenderLoaded)), [{ time: 100, loaded: "1" }, { time: 100, loaded: "2" }]);
  assert.equal(raw.sceneReadyMs, null);
  assert.equal(raw.milestones.workspaceTraceOptionsMs, null);
  assert.equal(raw.timedOut, true);
  assert.equal(raw.resourceTimingBufferFull, 1);
  now = 300_001;
  map.dataset.sceneReady = "true";
  mutationCallback([]);
  assert.equal(global.__cityLoadProfile.snapshot().sceneReadyMs, null);
});
