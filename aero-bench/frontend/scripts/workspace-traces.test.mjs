import assert from "node:assert/strict";
import { closeSync, mkdtempSync, mkdirSync, openSync, rmSync, statSync, utimesSync, writeFileSync, writeSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { performance } from "node:perf_hooks";
import {
  scanWorkspaceTraces,
  traceReadStats,
  workspaceTracesMiddleware,
} from "./workspace-traces.mjs";

let moduleVariant = 0;
// Each fresh import gets its own metadata cache and read counter, so cache
// assertions cannot be perturbed by other tests in the same process. A live
// worker pins the event loop, so every module instance whose pool ran a
// scan — including the top-level import — is disposed after the tests.
const freshModules = [];
async function freshModule() {
  moduleVariant++;
  const mod = await import(`./workspace-traces.mjs?t21-variant-${moduleVariant}`);
  freshModules.push(mod);
  return mod;
}
freshModules.push(await import("./workspace-traces.mjs"));
test.after(async () => {
  for (const mod of freshModules) mod.disposeTraceWorkers();
});

function writeTrace(directory, name, document) {
  writeFileSync(join(directory, name), JSON.stringify(document));
}

test("catalog reads metadata after array contents and invalidates changed-file metadata", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-test-"));
  try {
    const path = join(root, "public-trace.json");
    writeFileSync(path, '{"events":[{"value":"巡检,]"}],"run_id":"first","schema_version":"aero-bench.public-trace/v3"}');
    assert.equal((await scanWorkspaceTraces(root)).traces[0].run_id, "first");
    writeFileSync(path, '{"run_id":"other","schema_version":"unsupported"}');
    utimesSync(path, new Date(), new Date(Date.now() + 1000));
    assert.deepEqual((await scanWorkspaceTraces(root)).traces, []);
    writeFileSync(path, '{"schema_version":"aero-bench.public-trace/v3","events":[1,]}');
    assert.deepEqual((await scanWorkspaceTraces(root)).traces, []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

function request(middleware, url) {
  return new Promise((resolve, reject) => {
    const response = new PassThrough();
    response.setHeader = () => {};
    response.on("error", reject);
    response.resume();
    response.on("finish", () => resolve(response.statusCode));
    middleware({ method: "GET", url }, response, () => reject(new Error("unexpected fallthrough")));
  });
}

test("engineering replay serves public files, not adjacent private input JSON/SDF", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-workspace-test-"));
  try {
    const frontend = join(root, "frontend");
    const replay = join(root, "runs/unit/public/replay");
    mkdirSync(frontend);
    mkdirSync(join(replay, "artifacts"), { recursive: true });
    mkdirSync(join(root, "inputs"));
    // Catalog-only fixture, not a valid sealed trace or execution proof.
    writeFileSync(join(replay, "public-trace.json"), JSON.stringify({ schema_version: "aero-bench.public-trace/v3" }));
    writeFileSync(join(replay, "replay-manifest.json"), "{}");
    writeFileSync(join(replay, "artifacts", "a".repeat(64)), "unit fixture");
    writeFileSync(join(root, "inputs/policy.json"), '{"private_input":true}');
    writeFileSync(join(root, "inputs/scene.sdf"), "<sdf />");
    const middleware = workspaceTracesMiddleware(frontend);
    assert.equal(await request(middleware, "/workspace-traces/runs/unit/public/replay/replay-manifest.json"), 200);
    assert.equal(await request(middleware, `/workspace-traces/runs/unit/public/replay/artifacts/${"a".repeat(64)}`), 200);
    assert.equal(await request(middleware, "/workspace-traces/inputs/policy.json"), 404);
    assert.equal(await request(middleware, "/workspace-traces/inputs/scene.sdf"), 404);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});


test("a failed catalog scan reaches the connect error handler instead of hanging the request", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-missing-"));
  mkdirSync(join(root, "frontend"));
  const middleware = workspaceTracesMiddleware(join(root, "frontend"));
  // The repository disappears after the server started, so the scan itself fails.
  rmSync(root, { recursive: true, force: true });
  const forwarded = await new Promise((resolve, reject) => {
    const response = new PassThrough();
    response.setHeader = () => {};
    response.on("finish", () => reject(new Error("a failed scan must not answer 200")));
    middleware({ method: "GET", url: "/workspace-traces/catalog.json" }, response, resolve);
  });
  assert.ok(forwarded instanceof Error);
  assert.equal(forwarded.code, "ENOENT");
});

test("a startup warm-up fills the cache once and a catalog request waits for it", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-warm-"));
  try {
    mkdirSync(join(root, "frontend"));
    mkdirSync(join(root, "public"));
    for (const name of ["public-trace.json", "public-trace-b.json"]) {
      writeTrace(join(root, "public"), name, { schema_version: "aero-bench.public-trace/v3", run_id: name, events: [] });
    }
    const mod = await freshModule();
    const middleware = mod.workspaceTracesMiddleware(join(root, "frontend"));
    const warnings = [];
    const before = mod.traceReadStats.reads;
    // Not awaited: the request arrives while the warm-up scan is still running.
    const warming = middleware.warm({ warn: message => warnings.push(message) });
    assert.equal(await request(middleware, "/workspace-traces/catalog.json"), 200);
    await warming;
    assert.equal(mod.traceReadStats.reads - before, 2);
    assert.deepEqual(warnings, []);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("a failed warm-up is logged and the catalog request still reports its own scan error", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-warm-missing-"));
  mkdirSync(join(root, "frontend"));
  const middleware = workspaceTracesMiddleware(join(root, "frontend"));
  rmSync(root, { recursive: true, force: true });
  const warnings = [];
  await middleware.warm({ warn: message => warnings.push(message) });
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /warm-up failed: .*ENOENT/);
  const forwarded = await new Promise((resolve, reject) => {
    const response = new PassThrough();
    response.setHeader = () => {};
    response.on("finish", () => reject(new Error("a failed scan must not answer 200")));
    middleware({ method: "GET", url: "/workspace-traces/catalog.json" }, response, resolve);
  });
  assert.equal(forwarded.code, "ENOENT");
});

test("the plugin warms only servers with a close event and lets the process exit after close", () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-plugin-"));
  try {
    mkdirSync(join(root, "frontend"));
    mkdirSync(join(root, "public"));
    for (const name of ["public-trace.json", "public-trace-b.json"]) {
      writeTrace(join(root, "public"), name, { schema_version: "aero-bench.public-trace/v3", run_id: name, events: [] });
    }
    // A child process: live trace workers would keep it running until the timeout.
    const script = `
      import { EventEmitter } from "node:events";
      const { workspaceTracesPlugin, traceReadStats } = await import(${JSON.stringify(new URL("./workspace-traces.mjs", import.meta.url).href)});
      const plugin = workspaceTracesPlugin(${JSON.stringify(join(root, "frontend"))});
      const logger = { warn: message => { throw new Error(message); } };
      plugin.configureServer({ middlewares: { use() {} }, httpServer: null, config: { logger } });
      if (traceReadStats.reads !== 0) throw new Error("a middleware-mode server must not warm the catalog");
      const httpServer = new EventEmitter();
      plugin.configurePreviewServer({ middlewares: { use() {} }, httpServer, config: { logger } });
      httpServer.emit("close");
      process.on("exit", () => { if (traceReadStats.reads !== 2) process.exitCode = 3; });
    `;
    // A file, not `--input-type=module -e`: workers inherit that flag and would evaluate their CommonJS source as ESM.
    writeFileSync(join(root, "probe.mjs"), script);
    const child = spawnSync(process.execPath, [join(root, "probe.mjs")], { encoding: "utf8", timeout: 30_000 });
    assert.equal(child.signal, null, "the process did not exit after the server closed");
    assert.equal(child.status, 0, child.stderr);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("catalog removes identical replay copies and fixtures while retaining distinct content for the same run", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-dedup-"));
  try {
    mkdirSync(join(root, "public/replay"), { recursive: true });
    mkdirSync(join(root, "fixtures"));
    const content = JSON.stringify({ schema_version: "aero-bench.public-trace/v3", run_id: "same-run", events: [] });
    writeFileSync(join(root, "public/public-trace.json"), content);
    writeFileSync(join(root, "public/replay/public-trace.json"), content);
    writeFileSync(join(root, "fixtures/public-trace.json"), content.replace("same-run", "fixture"));
    writeFileSync(join(root, "public/public-trace-distinct.json"), content.replace('"events":[]', '"events":[1]'));
    const paths = (await scanWorkspaceTraces(root)).traces.map(entry => entry.relative_path);
    assert.deepEqual(paths, ["public/public-trace-distinct.json", "public/public-trace.json"]);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

/** Build a fixture tree covering every catalog branch the middleware has. */
function buildEqualityFixture(root) {
  const supported = { schema_version: "aero-bench.public-trace/v3", run_id: "run-1", suite_id: "s", case_id: "c", phase: "p", events: [[1, 2]] };
  const nested = JSON.stringify(supported, null, 2);
  mkdirSync(join(root, "public/replay"), { recursive: true });
  mkdirSync(join(root, "runs/inspection/public/artifacts"), { recursive: true });
  mkdirSync(join(root, "fixtures"), { recursive: true });
  mkdirSync(join(root, "private"), { recursive: true });
  mkdirSync(join(root, "runs/inspection/.hidden"), { recursive: true });
  writeFileSync(join(root, "public/public-trace.json"), nested);
  // Byte-identical nested copy must be deduped away.
  writeFileSync(join(root, "public/replay/public-trace.json"), nested);
  // Distinct content stays listed.
  writeFileSync(
    join(root, "runs/inspection/public/public-trace-alpha.json"),
    JSON.stringify({ ...supported, run_id: "run-2", events: [{ value: "巡检,]" }] }),
  );
  // Unsupported schema stays out but keeps the directory public.
  writeFileSync(
    join(root, "runs/inspection/public/public-trace-old.json"),
    JSON.stringify({ schema_version: "aero-bench.public-trace/v2", run_id: "run-3" }),
  );
  // Broken JSON and non-object documents stay unloadable, not fatal.
  writeFileSync(join(root, "runs/inspection/public/public-trace-broken.json"), '{"events":[1,]');
  writeFileSync(join(root, "runs/inspection/public/public-trace-array.json"), "[1,2]");
  writeFileSync(join(root, "runs/inspection/public/public-trace-schema.json"), JSON.stringify(supported));
  // Excluded locations: fixtures, private truth, dot directories.
  writeFileSync(join(root, "fixtures/public-trace.json"), nested);
  writeFileSync(join(root, "private/public-trace.json"), nested);
  writeFileSync(join(root, "runs/inspection/.hidden/public-trace.json"), nested);
  writeFileSync(join(root, "runs/inspection/public/artifacts/" + "b".repeat(64)), "artifact bytes");
}

// Golden output of the previous synchronous implementation (`git show 7da84132:frontend/scripts/workspace-traces.mjs`)
// on this fixture, recorded by the manager on 2026-10-02.
const SYNCHRONOUS_CATALOG_GOLDEN = {
  traces: [
    { id: "public.public-trace.json", relative_path: "public/public-trace.json",
      url: "/workspace-traces/public/public-trace.json", schema_version: "aero-bench.public-trace/v3",
      run_id: "run-1", suite_id: "s", case_id: "c", phase: "p", size_bytes: 174, loadable: true, blocker: null },
    { id: "runs.inspection.public.public-trace-alpha.json", relative_path: "runs/inspection/public/public-trace-alpha.json",
      url: "/workspace-traces/runs/inspection/public/public-trace-alpha.json", schema_version: "aero-bench.public-trace/v3",
      run_id: "run-2", suite_id: "s", case_id: "c", phase: "p", size_bytes: 137, loadable: true, blocker: null },
  ],
  publicDirs: ["/public", "/public/replay", "/runs/inspection/public"],
};

test("async catalog equals the previous synchronous catalog on a fixture tree", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-equal-"));
  try {
    buildEqualityFixture(root);
    const actual = await scanWorkspaceTraces(root);
    assert.deepEqual(actual.traces, SYNCHRONOUS_CATALOG_GOLDEN.traces);
    assert.deepEqual([...actual.publicDirs].map(directory => directory.slice(actual.root.length)).sort(),
      SYNCHRONOUS_CATALOG_GOLDEN.publicDirs);
    // A repeated scan is byte-stable with the fresh one.
    assert.deepEqual(await scanWorkspaceTraces(root), actual);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("stat-keyed cache re-reads changed files, drops deleted files and skips unchanged files", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-cache-"));
  try {
    mkdirSync(join(root, "public"), { recursive: true });
    const keep = join(root, "public/public-trace-keep.json");
    const changing = join(root, "public/public-trace-change.json");
    const removed = join(root, "public/public-trace-removed.json");
    const supported = { schema_version: "aero-bench.public-trace/v3", run_id: "keep", events: [] };
    writeFileSync(keep, JSON.stringify(supported));
    writeFileSync(changing, JSON.stringify({ ...supported, run_id: "before" }));
    writeFileSync(removed, JSON.stringify({ ...supported, run_id: "gone" }));
    const mod = await freshModule();
    const first = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(first.traces.map(trace => trace.run_id).sort(), ["before", "gone", "keep"]);
    const readsAfterFirst = mod.traceReadStats.reads;
    assert.equal(readsAfterFirst, 3);
    // Unchanged tree: nothing is re-read.
    const warm = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(warm.traces, first.traces);
    assert.equal(mod.traceReadStats.reads, readsAfterFirst);
    // A modified file is re-read and its new metadata is served.
    writeFileSync(changing, JSON.stringify({ ...supported, run_id: "after" }));
    utimesSync(changing, new Date(), new Date(Date.now() + 5000));
    const afterChange = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(afterChange.traces.map(trace => trace.run_id).sort(), ["after", "gone", "keep"]);
    assert.equal(mod.traceReadStats.reads, readsAfterFirst + 1);
    // A deleted file disappears from the catalog without being re-read.
    const readsAfterChange = mod.traceReadStats.reads;
    rmSync(removed);
    const afterDelete = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(afterDelete.traces.map(trace => trace.run_id).sort(), ["after", "keep"]);
    assert.equal(mod.traceReadStats.reads, readsAfterChange);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

// Directory mtimes older than the walk's racy window, so their listings are kept between scans.
function ageDirectories(root, relativePaths) {
  const past = new Date(Date.now() - 60_000);
  for (const relativePath of relativePaths) utimesSync(join(root, relativePath), past, past);
}

test("the walk lists a directory again only when its entries change", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-walk-"));
  try {
    const supported = { schema_version: "aero-bench.public-trace/v3", events: [] };
    mkdirSync(join(root, "a/public"), { recursive: true });
    mkdirSync(join(root, "b/deep/public"), { recursive: true });
    mkdirSync(join(root, "c"));
    writeTrace(join(root, "a/public"), "public-trace.json", { ...supported, run_id: "a" });
    writeTrace(join(root, "b/deep/public"), "public-trace.json", { ...supported, run_id: "deep" });
    const tree = ["a/public", "a", "b/deep/public", "b/deep", "b", "c", "."];
    ageDirectories(root, tree);
    const mod = await freshModule();
    const runIds = async () => (await mod.scanWorkspaceTraces(root)).traces.map(trace => trace.run_id).sort();
    const listed = async () => {
      const before = mod.directoryReadStats.reads;
      const ids = await runIds();
      return [ids, mod.directoryReadStats.reads - before];
    };
    assert.deepEqual(await listed(), [["a", "deep"], tree.length]);
    // Unchanged tree: every listing is reused.
    assert.deepEqual(await listed(), [["a", "deep"], 0]);
    // A new file changes only its directory's key.
    writeTrace(join(root, "c"), "public-trace.json", { ...supported, run_id: "c" });
    assert.deepEqual(await listed(), [["a", "c", "deep"], 1]);
    // That listing started inside the racy window, so it is listed again until the mtime ages.
    assert.deepEqual(await listed(), [["a", "c", "deep"], 1]);
    ageDirectories(root, ["c"]);
    assert.deepEqual(await listed(), [["a", "c", "deep"], 1]);
    assert.deepEqual(await listed(), [["a", "c", "deep"], 0]);
    // A new subdirectory is found through its parent's changed key and listed whole.
    mkdirSync(join(root, "a/new/public"), { recursive: true });
    writeTrace(join(root, "a/new/public"), "public-trace.json", { ...supported, run_id: "new" });
    ageDirectories(root, ["a/new/public", "a/new"]);
    assert.deepEqual(await listed(), [["a", "c", "deep", "new"], 3]);
    // A removed subtree disappears through its parent's changed key. "a" is listed again too:
    // its listing above started inside the racy window.
    rmSync(join(root, "b/deep"), { recursive: true });
    assert.deepEqual(await listed(), [["a", "c", "new"], 2]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a listing read in the same clock tick as the directory's last change is not reused", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-racy-"));
  try {
    const directory = join(root, "runs");
    mkdirSync(directory);
    // A recent mtime that utimesSync can restore exactly below.
    const tick = new Date(Date.now() - 100);
    utimesSync(directory, tick, tick);
    const tickMtimeMs = statSync(directory).mtimeMs;
    const mod = await freshModule();
    assert.deepEqual((await mod.scanWorkspaceTraces(root)).traces, []);
    // A second change in the same tick leaves the directory's (mtime, ino) key unchanged.
    writeTrace(directory, "public-trace.json", { schema_version: "aero-bench.public-trace/v3", run_id: "same-tick", events: [] });
    utimesSync(directory, tick, tick);
    assert.equal(statSync(directory).mtimeMs, tickMtimeMs);
    assert.deepEqual((await mod.scanWorkspaceTraces(root)).traces.map(trace => trace.run_id), ["same-tick"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("concurrent catalog requests share a single in-flight scan", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-flight-"));
  try {
    mkdirSync(join(root, "public"), { recursive: true });
    const supported = { schema_version: "aero-bench.public-trace/v3", run_id: "flight", events: [] };
    writeTrace(join(root, "public"), "public-trace.json", supported);
    writeTrace(join(root, "public"), "public-trace-second.json", { ...supported, run_id: "flight-2" });
    const mod = await freshModule();
    const before = mod.traceReadStats.reads;
    const [shared, concurrent] = await Promise.all([
      mod.scanWorkspaceTraces(root),
      mod.scanWorkspaceTraces(root),
    ]);
    assert.deepEqual(concurrent.traces, shared.traces);
    // Two files, one scan: exactly one read per cache miss, none duplicated.
    assert.equal(mod.traceReadStats.reads - before, 2);
    // A request after the scan finished reuses the warm cache (no re-reads).
    const sequential = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(sequential.traces, shared.traces);
    assert.equal(mod.traceReadStats.reads - before, 2);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("a 200 MB trace scan keeps the event loop responsive", async () => {
  const root = mkdtempSync(join(tmpdir(), "aero-catalog-heavy-"));
  try {
    mkdirSync(join(root, "runs/public"), { recursive: true });
    const bigPath = join(root, "runs/public/public-trace.json");
    const target = 200 * 1024 * 1024;
    // A realistic trace body: large-ish objects per event, like recorded
    // frames, rather than a degenerate flat number array.
    const event = JSON.stringify({
      t: 0.016,
      pose: { p: [1.5, 2.25, 3.125], q: [0.5, 0.5, 0.5, 0.5] },
      state: { mode: "inspection", battery: 0.87, flags: ["a", "b", "c"] },
    });
    const head = `{"schema_version":"aero-bench.public-trace/v3","run_id":"heavy","events":[${event}`;
    const fd = openSync(bigPath, "w");
    try {
      writeSync(fd, head);
      const chunk = `,${event}`.repeat(1024);
      for (let written = head.length; written < target - 4; written += chunk.length) {
        writeSync(fd, chunk);
      }
      writeSync(fd, "]}");
    } finally {
      closeSync(fd);
    }
    const mod = await freshModule();
    let scanError = null;
    const scanPromise = mod.scanWorkspaceTraces(root).catch(error => {
      scanError = error;
      return { traces: [] };
    });
    // Sample event-loop lag with immediate timers for as long as the scan
    // runs; every sample must return inside the ticket's 50 ms bound.
    let running = true;
    scanPromise.then(() => { running = false; });
    const lags = [];
    while (running) {
      const start = performance.now();
      await new Promise(resolve => setTimeout(resolve, 0));
      lags.push(performance.now() - start);
    }
    await scanPromise;
    assert.equal(scanError, null);
    assert.ok(lags.length > 0, "expected at least one event-loop sample during the scan");
    const worst = Math.max(...lags);
    assert.ok(worst < 50, `event-loop lag ${worst.toFixed(1)} ms exceeded the 50 ms bound`);
    const catalog = await mod.scanWorkspaceTraces(root);
    assert.deepEqual(catalog.traces.map(trace => [trace.relative_path, trace.loadable]), [
      ["runs/public/public-trace.json", true],
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
