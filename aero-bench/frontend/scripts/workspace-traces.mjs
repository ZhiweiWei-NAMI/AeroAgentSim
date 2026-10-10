/**
 * Scan the AERO-BENCH worktree for public-trace JSON and serve only those
 * files (plus digest-addressed public assets next to them). Private truth
 * directories are never listed or returned.
 *
 * The catalog is built off the server thread: the directory walk runs in a
 * worker that keeps directory listings between scans, and each trace's bytes
 * are read, parsed and SHA-256-hashed inside a small worker pool, so no single
 * step blocks the server event loop for long. Trace metadata (including the
 * digest) is cached per file by a stat key (size, mtimeMs, ino) and only
 * re-derived when a file changes; concurrent catalog requests share one
 * in-flight scan.
 */
import { createReadStream, readdirSync, realpathSync, statSync } from "node:fs";
import { stat } from "node:fs/promises";
import { dirname, extname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "node:worker_threads";

export const CATALOG_PATH = "/workspace-traces/catalog.json";
export const TRACE_PREFIX = "/workspace-traces/";
export const CATALOG_SCHEMA = "aero-bench.viewer-workspace-traces/v1";
export const PUBLIC_TRACE_V3 = "aero-bench.public-trace/v3";

const SKIP_DIRS = new Set([
  ".claude",
  ".git",
  ".grok",
  "dist",
  "fixtures",
  "node_modules",
  "private",
]);
const DIGEST_FILE = /^[0-9a-f]{64}$/;
const SCAN_CONCURRENCY = 16;
// One file per worker at a time; parsing is per-worker serial, so this cap
// scales the catalog's cold scan on multi-core hosts.
const WORKER_POOL_SIZE = 8;
const traceMetadataKeys = new Set(["schema_version", "run_id", "suite_id", "case_id", "phase"]);

function isPublicTraceFilename(name) {
  if (name.includes("schema")) {
    return false;
  }
  return name === "public-trace.json" || /^public-trace[-.].+\.json$/.test(name);
}

export function repoRootFromFrontend(frontendRoot) {
  return realpathSync(join(frontendRoot, ".."));
}

function shouldSkipDir(name) {
  return SKIP_DIRS.has(name) || name.startsWith(".");
}

function posixRel(root, absolute) {
  return relative(root, absolute).split(sep).join("/");
}

function pathToFileUrl(absolute) {
  return "file://" + absolute.split(sep).join("/");
}

// The worker reads one trace file, parses only the metadata object and
// hashes the bytes, so large files never block the server thread. It is
// built with eval to stay dependency-free. The parser URL must be computed
// here: inside an eval worker `import.meta.url` points at the eval wrapper,
// not at this module, so a relative computation would resolve to "/shared".
const PARSER_URL = pathToFileUrl(join(dirname(fileURLToPath(import.meta.url)), "..", "shared", "json-bytes.mjs"));
const TRACE_WORKER_SOURCE = `
const { parentPort } = require("node:worker_threads");
const { createHash } = require("node:crypto");
const { readFile } = require("node:fs/promises");
const SUPPORTED = ${JSON.stringify(PUBLIC_TRACE_V3)};
const KEYS = new Set(${JSON.stringify([...traceMetadataKeys])});
const PARSER_URL = ${JSON.stringify(PARSER_URL)};
let parseJsonObjectBytes = null;
async function loadParser() {
  if (parseJsonObjectBytes) return parseJsonObjectBytes;
  const module = await import(PARSER_URL);
  parseJsonObjectBytes = module.parseJsonObjectBytes;
  return parseJsonObjectBytes;
}
function unloadable(size_bytes, blocker) {
  return {
    schema_version: null,
    run_id: null,
    suite_id: null,
    case_id: null,
    phase: null,
    size_bytes,
    loadable: false,
    blocker,
  };
}
async function describe(absolute) {
  // Parser loading is infrastructure: if it fails, the failure must surface
  // as a failed scan, never as per-file "invalid JSON" blockers.
  const parse = await loadParser();
  const raw = await readFile(absolute);
  const size_bytes = raw.byteLength;
  try {
    const document = parse(raw, { keys: KEYS });
    if (document === null || typeof document !== "object" || Array.isArray(document)) {
      return unloadable(size_bytes, "trace is not a JSON object");
    }
    const schema_version = typeof document.schema_version === "string" ? document.schema_version : null;
    // The public trace contains the sealed SceneState projection. The
    // referenced producer artifact is evidence metadata and may live in a
    // separate sealed bundle; requiring a sibling file made valid v3 traces
    // incorrectly unloadable.
    const loadable = schema_version === SUPPORTED;
    return {
      content_sha256: createHash("sha256").update(raw).digest("hex"),
      schema_version,
      run_id: typeof document.run_id === "string" ? document.run_id : null,
      suite_id: typeof document.suite_id === "string" ? document.suite_id : null,
      case_id: typeof document.case_id === "string" ? document.case_id : null,
      phase: typeof document.phase === "string" ? document.phase : null,
      size_bytes,
      loadable,
      blocker: loadable ? null : "schema " + (schema_version ?? "unknown") + " is not supported",
    };
  } catch {
    return unloadable(size_bytes, "trace is not valid JSON");
  }
}
parentPort.on("message", async ({ absolute }) => {
  try {
    parentPort.postMessage({ ok: true, absolute, result: await describe(absolute) });
  } catch (error) {
    parentPort.postMessage({ ok: false, absolute, message: error instanceof Error ? error.message : "unreadable" });
  }
});
`;

/**
 * Small fixed worker pool: one file per worker at a time, results cross the
 * boundary as metadata only (never raw bytes). Broken workers are replaced
 * so one failure does not disable later scans. A live worker pins the Node
 * event loop even when unref()ed, so `disposeTraceWorkers()` must be called
 * when the embedding process should exit (tests do this).
 */
const workerPool = { idle: [], waiters: [], live: new Set() };
let poolFailure = null;

function scheduleFromIdle(entry) {
  const waiter = workerPool.waiters.shift();
  if (waiter) waiter.resolve(entry);
  else workerPool.idle.push(entry);
}

function workerFailure(error) {
  const failure = error instanceof Error ? error : new Error(String(error));
  failure.workerFailed = true;
  return failure;
}

function spawnTraceWorker() {
  const worker = new Worker(TRACE_WORKER_SOURCE, { eval: true });
  worker.unref();
  const entry = { worker, pending: [] };
  workerPool.live.add(entry);
  worker.on("message", message => {
    const pending = entry.pending.shift();
    if (pending) {
      if (message.ok) pending.resolve(message.result);
      else pending.reject(new Error(message.message));
    }
    scheduleFromIdle(entry);
  });
  worker.on("error", error => {
    for (const pending of entry.pending.splice(0)) pending.reject(workerFailure(error));
    const index = workerPool.idle.indexOf(entry);
    if (index >= 0) workerPool.idle.splice(index, 1);
    workerPool.live.delete(entry);
    try {
      scheduleFromIdle(spawnTraceWorker());
    } catch (spawnError) {
      poolFailure = spawnError instanceof Error ? spawnError : new Error(String(spawnError));
    }
  });
  worker.on("exit", () => {
    for (const pending of entry.pending.splice(0)) {
      pending.reject(workerFailure(new Error("trace worker exited before answering")));
    }
    workerPool.live.delete(entry);
  });
  return entry;
}

function acquireWorker() {
  if (poolFailure) throw poolFailure;
  const idle = workerPool.idle.pop();
  if (idle) return idle;
  if (workerPool.live.size < WORKER_POOL_SIZE) {
    return spawnTraceWorker();
  }
  return new Promise((resolve, reject) => workerPool.waiters.push({ resolve, reject }));
}

/**
 * Stop the walk worker and every trace-describing worker so the embedding
 * process can exit. In-flight scans fail with an infrastructure error
 * instead of hanging.
 */
export function disposeTraceWorkers() {
  if (walkWorker !== null) {
    walkWorker.worker.terminate();
    walkWorker = null;
  }
  for (const entry of workerPool.idle.splice(0)) entry.worker.terminate();
  for (const entry of [...workerPool.live]) entry.worker.terminate();
  workerPool.live.clear();
  for (const waiter of workerPool.waiters.splice(0)) {
    waiter.reject(workerFailure(new Error("trace worker pool was disposed")));
  }
}

function runOnWorker(absolute) {
  // acquireWorker() resolves synchronously to a pool entry when one is
  // available, and to a promise when the pool is full; Promise.resolve
  // accepts both. The entry is used inside .then so the postMessage happens
  // after the worker is secured for this job.
  return Promise.resolve(acquireWorker()).then(entry => new Promise((resolve, reject) => {
    entry.pending.push({ resolve, reject });
    entry.worker.postMessage({ absolute });
  }));
}

/** Reads issued on cache misses; tests assert deltas to count re-reads. */
export const traceReadStats = { reads: 0 };

const traceMetadataCache = new Map();

function statKey(stat) {
  return `${stat.size}:${stat.mtimeMs}:${stat.ino}`;
}

function unloadableMetadata(blocker) {
  return {
    schema_version: null,
    run_id: null,
    suite_id: null,
    case_id: null,
    phase: null,
    size_bytes: 0,
    loadable: false,
    blocker,
  };
}

/**
 * Peek a trace's catalog metadata. Cached by absolute path plus
 * (size, mtimeMs, ino), so unchanged files are never re-read.
 */
async function peekTraceAsync(absolute) {
  let statInfo;
  try {
    statInfo = await stat(absolute);
  } catch (error) {
    traceMetadataCache.delete(absolute);
    return unloadableMetadata(error instanceof Error ? error.message : "unreadable");
  }
  const key = statKey(statInfo);
  const cached = traceMetadataCache.get(absolute);
  if (cached && cached.key === key) return cached.value;
  try {
    traceReadStats.reads++;
    const metadata = await runOnWorker(absolute);
    // Only trust metadata that matches the file's current identity; if the
    // file changed while it was read, re-derive against what is on disk now.
    const current = await stat(absolute);
    if (statKey(current) !== key) return peekTraceAsync(absolute);
    traceMetadataCache.set(absolute, { key: statKey(current), value: metadata });
    return metadata;
  } catch (error) {
    // Infrastructure failures (worker crash, disposed pool, loader failure)
    // must fail the scan; the worker rejects only for per-file read errors,
    // which stay explicit unloadable entries, as in the old implementation.
    if (error && error.workerFailed) throw error;
    const metadata = unloadableMetadata(error instanceof Error ? error.message : "unreadable");
    traceMetadataCache.set(absolute, { key, value: metadata });
    return metadata;
  }
}

/**
 * Run `describe` over every path with bounded concurrency and positional
 * results. A single rejected describe rejects the whole batch: per-file
 * problems are returned as trace `blocker` values instead of rejections.
 */
async function describeAll(paths, describe) {
  const results = new Array(paths.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.min(SCAN_CONCURRENCY, paths.length) }, async () => {
    while (cursor < paths.length) {
      const index = cursor++;
      results[index] = await describe(paths[index]);
    }
  });
  await Promise.all(runners);
  return results;
}

// The walk runs in its own worker. A repeated scan must check every directory
// of the tree (about 12,000 in the main checkout), and on the server thread
// that costs about 110 ms of async stats or blocks the event loop. The worker
// keeps each directory's listing between scans and lists a directory again
// only when its (mtimeMs, ino) key changes: adding, removing or renaming an
// entry updates the directory's mtime. Kernel timestamps are coarse, so a
// second change in the same clock tick can leave the mtime unchanged. A
// listing is therefore kept only when the directory's mtime is at least
// RACY_LISTING_MS older than the moment the listing started; any later change
// then moves the mtime. Directories met for the first time, or changed, are
// listed with bounded concurrency, which keeps a cold walk parallel.
const WALK_CONCURRENCY = 16;
const RACY_LISTING_MS = 2000;
const WALK_WORKER_SOURCE = `
const { parentPort } = require("node:worker_threads");
const { statSync } = require("node:fs");
const { readdir, stat } = require("node:fs/promises");
const { join } = require("node:path");
const SKIP_DIRS = new Set(${JSON.stringify([...SKIP_DIRS])});
const WALK_CONCURRENCY = ${WALK_CONCURRENCY};
const RACY_LISTING_MS = ${RACY_LISTING_MS};
${isPublicTraceFilename}
${shouldSkipDir}
const listingsByRoot = new Map();
function directoryKey(info) {
  return info.mtimeMs + ":" + info.ino;
}
async function list(directory) {
  const listedAt = Date.now();
  const [entries, info] = await Promise.all([readdir(directory, { withFileTypes: true }), stat(directory)]);
  const key = listedAt - info.mtimeMs >= RACY_LISTING_MS ? directoryKey(info) : null;
  const listing = { key, directories: [], traces: [] };
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (!shouldSkipDir(entry.name)) listing.directories.push(join(directory, entry.name));
    } else if (entry.isFile() && isPublicTraceFilename(entry.name)) {
      listing.traces.push(join(directory, entry.name));
    }
  }
  return listing;
}
async function walk(root) {
  const previous = listingsByRoot.get(root) ?? new Map();
  const current = new Map();
  const files = [];
  const queue = [root];
  let reads = 0;
  let active = 0;
  const accept = (directory, listing) => {
    current.set(directory, listing);
    queue.push(...listing.directories);
    files.push(...listing.traces);
  };
  await new Promise(resolve => {
    const pump = () => {
      while (queue.length > 0 && active < WALK_CONCURRENCY) {
        const directory = queue.pop();
        const cached = previous.get(directory);
        if (cached !== undefined && cached.key !== null) {
          let info;
          try {
            info = statSync(directory);
          } catch {
            continue;
          }
          if (directoryKey(info) === cached.key) {
            accept(directory, cached);
            continue;
          }
        }
        active++;
        // A directory that vanished or cannot be read has nothing to list.
        list(directory).then(listing => {
          reads++;
          accept(directory, listing);
        }, () => {}).then(() => {
          active--;
          pump();
        });
      }
      if (queue.length === 0 && active === 0) resolve();
    };
    pump();
  });
  listingsByRoot.set(root, current);
  return { files, reads };
}
parentPort.on("message", async ({ id, root }) => {
  try {
    parentPort.postMessage({ id, ok: true, ...(await walk(root)) });
  } catch (error) {
    parentPort.postMessage({ id, ok: false, message: error instanceof Error ? error.message : String(error) });
  }
});
`;

/** Directory listings issued on walk-cache misses; tests assert deltas to count re-listings. */
export const directoryReadStats = { reads: 0 };

let walkWorker = null;

function spawnWalkWorker() {
  const worker = new Worker(WALK_WORKER_SOURCE, { eval: true });
  worker.unref();
  const entry = { worker, pending: new Map(), nextId: 0 };
  const fail = error => {
    if (walkWorker === entry) walkWorker = null;
    for (const pending of entry.pending.values()) pending.reject(workerFailure(error));
    entry.pending.clear();
  };
  worker.on("message", ({ id, ok, files, reads, message }) => {
    const pending = entry.pending.get(id);
    entry.pending.delete(id);
    if (!ok) {
      pending.reject(new Error(message));
      return;
    }
    directoryReadStats.reads += reads;
    pending.resolve(files);
  });
  worker.on("error", fail);
  worker.on("exit", () => fail(new Error("trace walk worker exited before answering")));
  return entry;
}

function walkOnWorker(root) {
  if (walkWorker === null) walkWorker = spawnWalkWorker();
  const entry = walkWorker;
  const id = entry.nextId++;
  return new Promise((resolve, reject) => {
    entry.pending.set(id, { resolve, reject });
    entry.worker.postMessage({ id, root });
  });
}

/** Async catalog scan; concurrent callers for one repo share the scan. */
export function scanWorkspaceTraces(repoRoot) {
  const root = realpathSync(repoRoot);
  let scan = inflightScans.get(root);
  if (scan === undefined) {
    scan = scanCatalogFiles(root).finally(() => inflightScans.delete(root));
    inflightScans.set(root, scan);
  }
  return scan;
}

const inflightScans = new Map();

async function scanCatalogFiles(root) {
  const files = await walkOnWorker(root);
  // Prefer the published entry over its byte-identical nested replay copy.
  files.sort((left, right) => left.split(sep).length - right.split(sep).length || left.localeCompare(right));
  const described = await describeAll(files, peekTraceAsync);
  const traces = [];
  const seenContents = new Set();
  const publicDirs = new Set();
  for (let index = 0; index < files.length; index++) {
    const absolute = files[index];
    const relative_path = posixRel(root, absolute);
    if (relative_path.split("/").includes("private")) {
      continue;
    }
    const { content_sha256, ...peeked } = described[index];
    publicDirs.add(dirname(absolute));
    if (!peeked.loadable || seenContents.has(content_sha256)) continue;
    seenContents.add(content_sha256);
    traces.push({
      id: relative_path.replaceAll("/", "."),
      relative_path,
      url: `${TRACE_PREFIX}${relative_path}`,
      ...peeked,
    });
  }
  traces.sort((left, right) => left.relative_path.localeCompare(right.relative_path));
  return { root, traces, publicDirs };
}

function isAllowedAsset(root, publicDirs, absolute) {
  const rel = posixRel(root, absolute);
  if (rel.split("/").includes("private")) {
    return false;
  }
  const dir = dirname(absolute);
  const parent = dirname(dir);
  const bucket = basenameSafe(dir);
  const name = basenameSafe(absolute);
  if ((bucket === "assets" || bucket === "artifacts") && DIGEST_FILE.test(name)) {
    return publicDirs.has(parent);
  }
  // JSON/SDF extension alone is not a public grant: task inputs and sealed
  // verifier evidence may use the same extensions elsewhere in the worktree.
  return name === "replay-manifest.json" && (
    publicDirs.has(dir) || (bucket === "replay" && publicDirs.has(parent))
  );
}

function publicDirectory(root, directory) {
  if (posixRel(root, directory).split("/").some(shouldSkipDir)) return false;
  try {
    return readdirSync(directory, { withFileTypes: true }).some(
      entry => entry.isFile() && isPublicTraceFilename(entry.name),
    );
  } catch {
    return false;
  }
}

function basenameSafe(path) {
  const parts = path.split(sep);
  return parts[parts.length - 1] ?? "";
}

function resolveUnderRoot(root, requestPath) {
  const rel = requestPath.replace(/^\/+/, "").replaceAll("\\", "/");
  if (rel.includes("..") || rel.includes("\0")) {
    return null;
  }
  const absolute = join(root, ...rel.split("/"));
  let real;
  try {
    real = realpathSync(absolute);
  } catch {
    return null;
  }
  const rootReal = realpathSync(root);
  if (real !== rootReal && !real.startsWith(rootReal + sep)) {
    return null;
  }
  return real;
}

function mediaTypeFor(path) {
  switch (extname(path).toLowerCase()) {
    case ".json":
      return "application/json; charset=utf-8";
    case ".png":
      return "image/png";
    case ".bin":
      return "application/octet-stream";
    default:
      return "application/octet-stream";
  }
}

export function workspaceTracesMiddleware(frontendRoot) {
  const repoRoot = repoRootFromFrontend(frontendRoot);
  let warmup = null;

  const middleware = async function workspaceTraces(req, res, next) {
    const url = req.url ?? "";
    const pathOnly = url.split("?")[0] ?? "";
    if (req.method !== "GET" && req.method !== "HEAD") {
      next();
      return;
    }
    if (pathOnly === CATALOG_PATH) {
      let scanned;
      try {
        // Wait for the startup scan so a cold cache is not read twice.
        if (warmup !== null) await warmup;
        scanned = await scanWorkspaceTraces(repoRoot);
      } catch (error) {
        // Connect only catches synchronous throws; hand an async scan failure to its error handler.
        next(error);
        return;
      }
      const body = JSON.stringify({
        schema_version: CATALOG_SCHEMA,
        traces: scanned.traces,
      });
      res.statusCode = 200;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store");
      res.end(body);
      return;
    }
    if (!pathOnly.startsWith(TRACE_PREFIX)) {
      next();
      return;
    }
    const relative_path = decodeURIComponent(pathOnly.slice(TRACE_PREFIX.length));
    const absolute = resolveUnderRoot(repoRoot, relative_path);
    if (absolute === null) {
      res.statusCode = 404;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ error: "workspace trace not found" }));
      return;
    }
    // Asset requests need only the adjacent public grant, not a worktree scan
    // and repeated JSON parsing of every large trace. Recheck directories on
    // each request rather than caching stale publication permissions.
    const requested = posixRel(repoRoot, absolute);
    const directory = dirname(absolute);
    const publicDirs = new Set(
      [directory, dirname(directory)].filter(path => publicDirectory(repoRoot, path)),
    );
    const isTrace = publicDirs.has(directory)
      && isPublicTraceFilename(basenameSafe(absolute))
      && !requested.split("/").slice(0, -1).some(shouldSkipDir)
      && statSync(absolute).isFile();
    if (!isTrace && !isAllowedAsset(repoRoot, publicDirs, absolute)) {
      res.statusCode = 404;
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.end(JSON.stringify({ error: "workspace path is not a public trace or declared asset" }));
      return;
    }
    res.statusCode = 200;
    res.setHeader("Content-Type", mediaTypeFor(absolute));
    res.setHeader("Cache-Control", "no-store");
    if (req.method === "HEAD") {
      res.end();
      return;
    }
    createReadStream(absolute).pipe(res);
  };
  /**
   * Fill the per-file metadata cache when the server starts; the first catalog request
   * otherwise reads every trace. A failure is logged here and reported again by the
   * request's own scan.
   */
  middleware.warm = logger => {
    // The scan can throw synchronously (a missing root); the async wrapper turns that into a rejection.
    warmup = (async () => { await scanWorkspaceTraces(repoRoot); })().catch(error => {
      logger.warn(`workspace trace catalog warm-up failed: ${error.message}`);
    });
    return warmup;
  };
  return middleware;
}

export function workspaceTracesPlugin(frontendRoot) {
  const root = frontendRoot ?? join(dirname(fileURLToPath(import.meta.url)), "..");
  const middleware = workspaceTracesMiddleware(root);
  const attach = server => {
    server.middlewares.use(middleware);
    // A middleware-mode server has no close event to release the worker pool, so it skips the warm-up.
    if (server.httpServer === null) return;
    const warmup = middleware.warm(server.config.logger);
    // Dispose after the warm-up settles; a scan still running would spawn workers that pin the process.
    server.httpServer.once("close", () => {
      void warmup.finally(disposeTraceWorkers);
    });
  };
  return {
    name: "aero-workspace-traces",
    configureServer: attach,
    configurePreviewServer: attach,
  };
}
