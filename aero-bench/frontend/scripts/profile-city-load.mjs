/** City load timing. Summary durations are milliseconds; sizes are bytes. */
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const timeoutMs = 300_000;
const gpuArgs = [
  "--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
  "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist",
];

export function categorize(url) {
  const pathname = new URL(url, "http://resource.invalid").pathname;
  const extension = /\.([^./]+)$/.exec(pathname)?.[1].toLowerCase();
  if (["glb", "gltf", "bin", "json", "js", "css", "wasm"].includes(extension)) return extension;
  if (["webp", "png", "jpg", "jpeg", "ktx2", "basis"].includes(extension)) return "image";
  if (["woff", "woff2", "ttf", "otf", "eot"].includes(extension)) return "font";
  if (["html", "htm"].includes(extension)) return "html";
  return "other";
}

// Nearest-rank percentiles. Missing detailed timings produce null, never zero.
function distribution(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const rank = p => sorted.length ? sorted[Math.ceil(p * sorted.length) - 1] : null;
  return { p50: rank(0.5), p95: rank(0.95), max: sorted.at(-1) ?? null };
}

export function summarizeResources(entries, sceneReadyMs) {
  const groups = new Map();
  const events = [];
  for (const entry of entries) {
    const category = categorize(entry.name);
    if (!groups.has(category)) groups.set(category, {
      count: 0, encodedBytes: 0, decodedBytes: 0, transferBytes: 0,
      noDetailedTiming: 0, lastResponseEnd: 0, queueing: [], download: [],
    });
    const group = groups.get(category);
    group.count++;
    group.encodedBytes += entry.encodedBodySize;
    group.decodedBytes += entry.decodedBodySize;
    group.transferBytes += entry.transferSize;
    group.lastResponseEnd = Math.max(group.lastResponseEnd, entry.responseEnd);
    if (entry.requestStart === 0) group.noDetailedTiming++;
    else {
      group.queueing.push(entry.requestStart - entry.startTime);
      group.download.push(entry.responseEnd - entry.responseStart);
    }
    // In flight includes queueing, from startTime to responseEnd; zero spans are omitted.
    if (entry.responseEnd > entry.startTime) {
      events.push([entry.startTime, 1], [entry.responseEnd, -1]);
    }
  }
  let active = 0, maxInFlight = 0;
  // Half-open intervals: a response ending at t precedes a new request at t.
  for (const [, delta] of events.sort((a, b) => a[0] - b[0] || a[1] - b[1])) {
    active += delta;
    maxInFlight = Math.max(maxInFlight, active);
  }
  return {
    categories: Object.fromEntries([...groups].sort(([a], [b]) => a.localeCompare(b)).map(([key, group]) =>
      [key, { ...group, queueing: distribution(group.queueing), download: distribution(group.download) }])),
    maxInFlight,
    protocols: [...new Set(entries.map(entry => entry.nextHopProtocol))].sort(),
    noDetailedTiming: entries.filter(entry => entry.requestStart === 0).length,
    afterSceneReadyShare: sceneReadyMs === null ? null : entries.length
      ? entries.filter(entry => entry.responseEnd > sceneReadyMs).length / entries.length : 0,
  };
}

export function summarizeLongTasks(tasks, sceneReadyMs) {
  // A timeout has no scene-ready boundary. Summarize all tasks observed by the deadline.
  const durations = tasks.filter(task => sceneReadyMs === null || task.start < sceneReadyMs)
    .map(task => sceneReadyMs === null ? task.duration : Math.min(task.duration, sceneReadyMs - task.start));
  return { count: durations.length, totalMs: durations.reduce((sum, d) => sum + d, 0),
    maxMs: Math.max(0, ...durations), blockingMs: durations.reduce((sum, d) => sum + Math.max(0, d - 50), 0) };
}

export function parseArguments(args) {
  const [rawOrigin, outputDir, ...flags] = args;
  if (!rawOrigin || !outputDir || outputDir.startsWith("--") || !outputDir.trim()) {
    throw new Error("Usage: profile-city-load.mjs <origin> <output-dir> [--paths=/,/?view=replay] [--iterations=1..50] [--gpu] [--cpu-profile] [--viewport=1..8192x1..8192]");
  }
  const origin = new URL(rawOrigin);
  if (!/^https?:$/.test(origin.protocol) || origin.username || origin.password
    || origin.pathname !== "/" || origin.search || origin.hash || rawOrigin.trim() !== rawOrigin) {
    throw new Error("origin must be an HTTP(S) origin without credentials, path, query or fragment");
  }
  const options = { origin: origin.origin, outputDir: resolve(outputDir), paths: ["/"],
    iterations: 1, gpu: false, cpuProfile: false, viewport: { width: 1600, height: 1000 } };
  const seen = new Set();
  for (const flag of flags) {
    const key = flag.split("=")[0];
    if (seen.has(key)) throw new Error(`Duplicate flag: ${key}`);
    seen.add(key);
    if (flag === "--gpu") options.gpu = true;
    else if (flag === "--cpu-profile") options.cpuProfile = true;
    else if (flag.startsWith("--paths=")) {
      options.paths = flag.slice(8).split(",");
      for (const path of options.paths) {
        if (!path.startsWith("/") || path.startsWith("//") || /[\s#\\]/.test(path)
          || /%(?![a-f\d]{2})/i.test(path) || new URL(path, origin).origin !== origin.origin) {
          throw new Error("paths must be nonempty origin-relative paths without whitespace or fragments");
        }
      }
      if (new Set(options.paths).size !== options.paths.length) throw new Error("paths must be unique");
    } else if (flag.startsWith("--iterations=")) {
      const value = flag.slice(13);
      if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 50) throw new Error("iterations must be an integer in [1, 50]");
      options.iterations = Number(value);
    } else if (flag.startsWith("--viewport=")) {
      const match = /^(\d+)x(\d+)$/.exec(flag.slice(11));
      if (!match || match.slice(1).some(value => Number(value) < 1 || Number(value) > 8192)) {
        throw new Error("viewport must be WIDTHxHEIGHT with each dimension in [1, 8192]");
      }
      options.viewport = { width: Number(match[1]), height: Number(match[2]) };
    } else throw new Error(`Unknown or malformed flag: ${flag}`);
  }
  return options;
}

export function summarizeCpuProfile(profile) {
  const nodes = new Map(profile.nodes.map(node => [node.id, node.callFrame]));
  const totals = new Map();
  // CDP timeDeltas are microseconds, including the offset to the first sample.
  for (let index = 0; index < (profile.samples ?? []).length; index++) {
    const frame = nodes.get(profile.samples[index]);
    if (!frame) throw new Error("CPU profile sample references a missing node");
    const key = JSON.stringify([frame.functionName, frame.url, frame.lineNumber, frame.columnNumber]);
    const row = totals.get(key) ?? { functionName: frame.functionName || "(anonymous)",
      urlLine: `${frame.url}:${frame.lineNumber + 1}`, selfMs: 0 };
    row.selfMs += profile.timeDeltas[index] / 1000;
    totals.set(key, row);
  }
  return [...totals.values()].sort((a, b) => b.selfMs - a.selfMs).slice(0, 30);
}

// CDP profiling starts before navigation and stops after the readiness notification.
// Trim protocol overhead using a measured navigation clock and observed readiness.
export function sliceCpuProfile(profile, startTime, endTime) {
  if (!Number.isFinite(startTime) || !Number.isFinite(endTime) || endTime < startTime
    || startTime < profile.startTime || endTime > profile.endTime) {
    throw new Error(`CPU profile does not cover the navigation-to-load-end window: profile=${profile.startTime}..${profile.endTime}, window=${startTime}..${endTime}`);
  }
  const samples = [], timeDeltas = [];
  const hits = new Map();
  let timestamp = profile.startTime;
  if (profile.samples.length !== profile.timeDeltas.length) throw new Error("CPU profile sample/delta mismatch");
  // The measured Chromium stream contains negative deltas (out-of-order samples).
  // Recover absolute timestamps before ordering; do not clamp or discard deltas.
  const ordered = profile.samples.map((sample, index) => {
    const delta = profile.timeDeltas[index];
    if (!Number.isFinite(delta)) throw new Error("Invalid CPU profile time delta");
    timestamp += delta;
    return { sample, timestamp };
  }).sort((a, b) => a.timestamp - b.timestamp);
  let previous = profile.startTime;
  for (const entry of ordered) {
    const overlap = Math.min(entry.timestamp, endTime) - Math.max(previous, startTime);
    if (overlap > 0) {
      const sample = entry.sample;
      samples.push(sample);
      timeDeltas.push(overlap);
      hits.set(sample, (hits.get(sample) ?? 0) + 1);
    }
    previous = entry.timestamp;
  }
  return { ...profile, startTime, endTime, samples, timeDeltas,
    nodes: profile.nodes.map(({ positionTicks, ...node }) => ({ ...node, hitCount: hits.get(node.id) ?? 0 })) };
}

// Runs before application scripts in both the cold navigation and warm reload.
export function installLoadObservers() {
  performance.setResourceTimingBufferSize(10000);
  const milestones = { cityMapMs: null, canvasMs: null, skyReadyMs: null,
    texturesReadyMs: null, sceneReadyMs: null, workspaceTraceOptionsMs: null };
  const measure = globalThis.__cityLoadProfile = {
    milestones, buildingRenderLoaded: [], longTasks: [], resourceTimingBufferFull: 0,
  };
  performance.addEventListener("resourcetimingbufferfull", () => measure.resourceTimingBufferFull++);
  const appendTasks = entries => measure.longTasks.push(...entries.map(entry => ({ start: entry.startTime, duration: entry.duration })));
  const taskObserver = new PerformanceObserver(list => appendTasks(list.getEntries()));
  taskObserver.observe({ type: "longtask", buffered: true });
  let previousBuildingValue;
  const inspect = (records = []) => {
    const now = performance.now();
    const map = document.querySelector("#city-map");
    const first = (key, condition) => { if (now <= 300_000 && milestones[key] === null && condition) milestones[key] = now; };
    first("cityMapMs", map);
    first("canvasMs", map?.querySelector("canvas"));
    first("skyReadyMs", map?.dataset.skyReady === "true");
    first("texturesReadyMs", map?.dataset.texturesReady === "true");
    first("sceneReadyMs", map?.dataset.sceneReady === "true");
    first("workspaceTraceOptionsMs", document.querySelector("#workspace-trace-select")?.options.length > 1);
    // Mutation records retain old values. Reconstruct all changes within a batch,
    // rather than collapsing several increments into the final dataset value.
    const changes = records.filter(record => record.target === map && record.attributeName === "data-building-render-loaded");
    for (let index = 0; index < changes.length; index++) {
      const value = index + 1 < changes.length ? changes[index + 1].oldValue : map.getAttribute("data-building-render-loaded");
      if (value !== previousBuildingValue) measure.buildingRenderLoaded.push({ time: now, loaded: value });
      previousBuildingValue = value;
    }
    if (map && changes.length === 0 && map.hasAttribute("data-building-render-loaded")
      && previousBuildingValue !== map.dataset.buildingRenderLoaded) {
      previousBuildingValue = map.dataset.buildingRenderLoaded;
      measure.buildingRenderLoaded.push({ time: now, loaded: previousBuildingValue });
    }
  };
  const observer = new MutationObserver(inspect);
  observer.observe(document, { childList: true, subtree: true, attributes: true, attributeOldValue: true,
    attributeFilter: ["data-scene-ready", "data-sky-ready", "data-textures-ready", "data-building-render-loaded"] });
  inspect();
  measure.snapshot = () => {
    inspect(observer.takeRecords());
    appendTasks(taskObserver.takeRecords());
    const resourceFields = ["name", "initiatorType", "nextHopProtocol", "startTime", "fetchStart", "requestStart",
      "responseStart", "responseEnd", "transferSize", "encodedBodySize", "decodedBodySize"];
    return { milestones: { ...milestones }, sceneReadyMs: milestones.sceneReadyMs,
      timedOut: milestones.sceneReadyMs === null, observedEndMs: performance.now(),
      buildingRenderLoaded: [...measure.buildingRenderLoaded], longTasks: [...measure.longTasks],
      resourceTimingBufferFull: measure.resourceTimingBufferFull,
      navigation: performance.getEntriesByType("navigation")[0]?.toJSON() ?? null,
      timeOriginMs: performance.timeOrigin,
      resources: performance.getEntriesByType("resource").map(entry => Object.fromEntries(resourceFields.map(key => [key, entry[key]]))),
      dataset: { ...document.querySelector("#city-map")?.dataset } };
  };
}

const numeric = value => value === null ? "unobserved" : value.toFixed(2);
export function renderMarkdown(report) {
  const lines = ["# City load profile", "", `Origin: ${report.options.origin}`, "",
    "Times are milliseconds from navigation start. Bytes are Resource Timing sizes, not Content-Length.",
    "Milestones record first observations. Resource and CDP snapshots may arrive later while the main thread is busy. CDP duration metrics are seconds; JSHeapUsedSize is bytes.",
    "Queueing = requestStart - startTime (includes connection setup); download = responseEnd - responseStart.",
    "Percentiles use nearest rank and exclude requestStart=0. In-flight spans include queueing; intervals are half-open.",
    "Long tasks are clipped at scene-ready; timeout summaries cover the observed window.",
    "CPU profiles are trimmed to navigation-to-scene-ready (or the 300 s deadline). The document request's CDP wall/monotonic clock pair maps performance.timeOrigin to profile time; self times are sampled estimates.",
    "Resource Timing does not measure decode time. CPU samples and the dataset's scene phases support investigation, without assigning guessed decode durations.", ""];
  for (const load of report.loads) {
    const summary = load.resourceSummary;
    lines.push(`## ${load.path} / iteration ${load.iteration} / ${load.cache}`, "",
      `Scene-ready: ${numeric(load.sceneReadyMs)}; snapshot: ${numeric(load.observedEndMs)}; timed out: ${load.timedOut}; buffer-full events: ${load.resourceTimingBufferFull}.`, "",
      "| Category | Count | Encoded B | Decoded B | Transfer B | No detail | Queue p50 / p95 / max ms | Download p50 / p95 / max ms | Last response ms |",
      "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |");
    for (const [category, row] of Object.entries(summary.categories)) {
      const dist = d => [d.p50, d.p95, d.max].map(numeric).join(" / ");
      lines.push(`| ${category} | ${row.count} | ${row.encodedBytes} | ${row.decodedBytes} | ${row.transferBytes} | ${row.noDetailedTiming} | ${dist(row.queueing)} | ${dist(row.download)} | ${numeric(row.lastResponseEnd)} |`);
    }
    lines.push("", `Max in flight: ${summary.maxInFlight}; protocols: ${summary.protocols.map(p => p || "(unreported)").join(", ") || "none"}; no detailed timing: ${summary.noDetailedTiming}; after-scene-ready share: ${summary.afterSceneReadyShare === null ? "unobserved" : (summary.afterSceneReadyShare * 100).toFixed(2) + "%"}.`, "",
      "| Milestone | ms |", "| --- | ---: |",
      ...Object.entries(load.milestones).map(([name, value]) => `| ${name} | ${numeric(value)} |`), "",
      `Long tasks: count ${load.longTaskSummary.count}; total ${numeric(load.longTaskSummary.totalMs)} ms; max ${numeric(load.longTaskSummary.maxMs)} ms; blocking ${numeric(load.longTaskSummary.blockingMs)} ms.`, "",
      "| CDP metric | Value |", "| --- | ---: |",
      ...Object.entries(load.metrics).map(([name, value]) => `| ${name} | ${value} |`), "");
    if (load.cpuProfile) {
      lines.push(`CPU profile: ${load.cpuProfile.file}; unprofiled navigation prefix: ${numeric(load.cpuProfile.unprofiledPrefixMs)} ms; top functions by sampled self time:`, "",
        "| Function | URL:line | Self ms |", "| --- | --- | ---: |");
      const escape = value => value.replaceAll("|", "\\|").replaceAll("\n", " ");
      for (const row of load.cpuProfile.topFunctions) lines.push(`| ${escape(row.functionName)} | ${escape(row.urlLine)} | ${numeric(row.selfMs)} |`);
      lines.push("");
    }
  }
  return lines.join("\n") + "\n";
}

export async function main(args = process.argv.slice(2)) {
  const options = parseArguments(args);
  // Import only after validation: malformed CLI arguments need no installed browser.
  const { chromium } = await import("playwright");
  await mkdir(options.outputDir, { recursive: true });
  const report = { schemaVersion: "aero-bench.city-load-profile/v1", startedAt: new Date().toISOString(), options, loads: [] };
  const browser = await chromium.launch({ headless: true, ...(options.gpu ? { channel: "chromium" } : {}),
    args: [...(options.gpu ? gpuArgs : []), "--no-proxy-server"] });
  try {
    for (const [pathIndex, path] of options.paths.entries()) {
      for (let iteration = 1; iteration <= options.iterations; iteration++) {
        const context = await browser.newContext({ viewport: options.viewport });
        try {
          await context.addInitScript(installLoadObservers);
          const page = await context.newPage();
          for (const cache of ["cold", "warm"]) {
            const session = await context.newCDPSession(page);
            await session.send("Performance.enable");
            const { frameTree } = await session.send("Page.getFrameTree");
            let documentRequestClock = null;
            if (options.cpuProfile) {
              await session.send("Network.enable");
              session.on("Network.requestWillBeSent", event => {
                if (event.type === "Document" && event.frameId === frameTree.frame.id && documentRequestClock === null) {
                  documentRequestClock = { timestamp: event.timestamp, wallTime: event.wallTime, url: event.request.url };
                }
              });
            }
            if (options.cpuProfile) {
              await session.send("Profiler.enable");
              await session.send("Profiler.setSamplingInterval", { interval: 1000 });
              await session.send("Profiler.start");
            }
            try {
              const navigationOptions = { waitUntil: "commit", timeout: timeoutMs };
              if (cache === "cold") await page.goto(new URL(path, options.origin).href, navigationOptions);
              else await page.reload(navigationOptions);
              const navigationElapsedMs = await page.evaluate(() => performance.now());
              await page.waitForFunction(() => globalThis.__cityLoadProfile
                && (globalThis.__cityLoadProfile.milestones.sceneReadyMs !== null || performance.now() >= 300_000),
              null, { polling: 50, timeout: Math.max(1, timeoutMs - navigationElapsedMs) + 5000 });
            } catch (error) {
              if (error.name !== "TimeoutError") throw error;
              // Keep genuine deadline expirations; transport/programming failures still throw.
            }
            const raw = await page.evaluate(() => globalThis.__cityLoadProfile.snapshot());
            // Read metrics at the boundary, before serializing potentially large profiles.
            const { metrics } = await session.send("Performance.getMetrics");
            let cpuProfile;
            if (options.cpuProfile) {
              const { profile } = await session.send("Profiler.stop");
              if (documentRequestClock === null) throw new Error("Missing main-document CDP request clock");
              // Anchor this document's timeOrigin with its own request.
              const windowStartUs = (documentRequestClock.timestamp
                + (raw.timeOriginMs / 1000 - documentRequestClock.wallTime)) * 1_000_000;
              const windowEndUs = windowStartUs + (raw.sceneReadyMs ?? 300_000) * 1000;
              // Chromium may begin the returned profile after navigation starts.
              // Keep that measured gap explicit; never synthesize CPU samples.
              const capturedStartUs = Math.max(windowStartUs, profile.startTime);
              const scopedProfile = sliceCpuProfile(profile, capturedStartUs, windowEndUs);
              const slug = `${pathIndex + 1}-${path.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-|-$/g, "") || "root"}`;
              const file = `${slug}-${iteration}-${cache}.cpuprofile`;
              await writeFile(resolve(options.outputDir, file), JSON.stringify(scopedProfile) + "\n");
              cpuProfile = { file, samplingIntervalUs: 1000, sourceStartTimeUs: profile.startTime,
                sourceEndTimeUs: profile.endTime, windowStartUs, windowEndUs,
                capturedStartUs, unprofiledPrefixMs: (capturedStartUs - windowStartUs) / 1000,
                documentRequestClock,
                sourceNegativeDeltaCount: profile.timeDeltas.filter(delta => delta < 0).length,
                rawProfile: profile,
                topFunctions: summarizeCpuProfile(scopedProfile) };
            }
            const metricNames = ["JSHeapUsedSize", "ScriptDuration", "TaskDuration", "LayoutDuration", "RecalcStyleDuration"];
            const selected = Object.fromEntries(metricNames.map(name => {
              const metric = metrics.find(metric => metric.name === name);
              if (!metric) throw new Error(`Missing CDP metric: ${name}`);
              return [name, metric.value];
            }));
            const load = { path, iteration, cache, ...raw, metrics: selected,
              metricUnits: { JSHeapUsedSize: "bytes", durations: "seconds" },
              ...(cpuProfile ? { cpuProfile } : {}),
              resourceSummary: summarizeResources(raw.resources, raw.sceneReadyMs),
              longTaskSummary: summarizeLongTasks(raw.longTasks, raw.sceneReadyMs) };
            report.loads.push(load);
            await session.detach();
            console.log(JSON.stringify({ path, iteration, cache, sceneReadyMs: raw.sceneReadyMs,
              timedOut: raw.timedOut, resources: raw.resources.length, resourceTimingBufferFull: raw.resourceTimingBufferFull }));
          }
        } finally { await context.close(); }
      }
    }
  } finally {
    await browser.close();
    report.finishedAt = new Date().toISOString();
    await writeFile(resolve(options.outputDir, "load-profile.json"), JSON.stringify(report, null, 2) + "\n");
    await writeFile(resolve(options.outputDir, "load-profile.md"), renderMarkdown(report));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
