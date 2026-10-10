/**
 * Public Studio acceptance for one completed, independently audited traffic-preview job.
 *
 * The script imports the job's exact strict-v3 draft through the visible file control,
 * selects the explicit catalog profile, submits through the public panel, verifies the
 * complete mounted trace, and then proves that a demand edit marks it stale without
 * silently applying display limits.
 *
 * Usage:
 *   node scripts/capture-city-traffic-preview-live.mjs <vite-origin> <output-dir> <live-runtime-dir>
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { relative, resolve } from "node:path";
import { chromium } from "playwright";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:5395").origin;
const output = resolve(process.argv[3]
  ?? "../validation/codex-takeover-20261001/U/traffic-preview-live");
const runtime = resolve(process.argv[4]
  ?? "../validation/codex-takeover-20261001/T2-v3-live/runtime");
const timeoutMs = 300_000;
const storageKey = "aero-bench.city-workspace.v3";

const request = JSON.parse(await readFile(resolve(runtime, "client/request.json"), "utf8"));
const expectedJob = JSON.parse(await readFile(resolve(runtime, "client/final-job-response.json"), "utf8"));
const expectedTrace = JSON.parse(await readFile(resolve(runtime, "client/downloaded-trace.json"), "utf8"));
const liveResult = JSON.parse(await readFile(resolve(runtime, "live-result.json"), "utf8"));

assert.equal(request.schema_version, "aero-bench.traffic-preview-request/v1");
assert.equal(request.draft.schema_version, "aero-bench.city-workspace/v3");
assert.equal(expectedJob.state, "ready");
assert.equal(expectedJob.job_id, liveResult.job_id);
assert.equal(expectedJob.trace.sha256, liveResult.trace.sha256);
assert.equal(expectedJob.canonical_audit.sha256, liveResult.audit.sha256);

function traceSummary(trace) {
  const vehicles = new Set(), bicycles = new Set(), persons = new Set();
  for (const frame of trace.frames) {
    for (const sample of frame.vehicles) {
      vehicles.add(sample[0]);
      if (sample[4] === "bicycle") bicycles.add(sample[0]);
    }
    for (const sample of frame.persons) persons.add(sample[0]);
  }
  return {
    schemaVersion: trace.schema_version,
    durationSeconds: trace.duration_seconds,
    frames: trace.frames.length,
    signals: trace.signals.length,
    retainedVehicles: vehicles.size,
    retainedBicycles: bicycles.size,
    retainedPersons: persons.size,
    demandAuthoring: trace.demand_authoring,
  };
}

const expectedSummary = traceSummary(expectedTrace);
const report = {
  schemaVersion: "aero-bench.city-traffic-preview-browser-acceptance/v1",
  status: "RUNNING",
  origin,
  runtime,
  output,
  startedAt: new Date().toISOString(),
  expected: {
    jobId: expectedJob.job_id,
    profileId: expectedJob.profile_id,
    profileSha256: expectedJob.profile_sha256,
    workspaceSha256: expectedJob.workspace_sha256,
    traceSha256: expectedJob.trace.sha256,
    auditSha256: expectedJob.canonical_audit.sha256,
    trace: expectedSummary,
  },
  checks: [],
  screenshots: [],
  apiResponses: [],
  pageErrors: [],
  consoleErrors: [],
  requestFailures: [],
};

let browser;
let context;
let page;

async function check(name, action) {
  const started = performance.now();
  try {
    const details = await action();
    report.checks.push({ name, status: "PASS",
      durationMs: Math.round(performance.now() - started), details: details ?? null });
    return details;
  } catch (error) {
    report.checks.push({ name, status: "FAIL",
      durationMs: Math.round(performance.now() - started),
      error: error instanceof Error ? error.message : String(error) });
    throw error;
  }
}

async function screenshot(name, locator = page) {
  const path = resolve(output, name);
  await locator.screenshot({ path, animations: "disabled" });
  const bytes = await readFile(path);
  const entry = { file: relative(output, path), sizeBytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex") };
  report.screenshots.push(entry);
  return entry;
}

async function hookStudio(target) {
  await target.route(/\/src\/city-studio\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const token = "this.map = new PublicTraceMap(";
    assert.equal(source.split(token).length, 2, "Studio map hook must match exactly once");
    await route.fulfill({ response,
      body: source.replace(token, "window.__aeroStudioMap = this.map = new PublicTraceMap(") });
  });
}

async function waitForLoadedCity() {
  await page.locator('#studio-map[data-scene-ready="true"][data-sky-ready="true"]'
    + '[data-textures-ready="true"][data-building-render-total="414"]'
    + '[data-building-render-failed="0"]')
    .waitFor({ timeout: timeoutMs });
  await page.locator("#studio-map .city-scene-loading").waitFor({ state: "hidden", timeout: timeoutMs });
  await page.waitForFunction(() => window.__aeroStudioMap !== undefined
    && (window.__aeroStudioMap.renderStreamer?.progress.active ?? 0) === 0,
  undefined, { timeout: timeoutMs, polling: 250 });
  return page.locator("#studio-map").evaluate(root => ({ ...root.dataset }));
}

async function waitForProfile() {
  const select = page.getByLabel("SUMO 预览档案", { exact: true });
  await page.waitForFunction(profileId => {
    const control = document.querySelector('select[aria-label="SUMO 预览档案"]');
    return control instanceof HTMLSelectElement
      && [...control.options].some(option => option.value === profileId) && !control.disabled;
  }, request.profile_id, { timeout: timeoutMs, polling: 100 });
  return select;
}

async function readMapAuditState() {
  return page.evaluate(() => {
    const root = document.querySelector("#studio-map");
    const map = window.__aeroStudioMap;
    if (!(root instanceof HTMLElement) || map?.trafficPreview === null || map?.trafficPreview === undefined) {
      throw new Error("Studio traffic preview is unavailable");
    }
    const data = map.trafficPreview.data;
    const summary = (() => {
      const vehicles = new Set(), bicycles = new Set(), persons = new Set();
      for (const frame of data.frames) {
        for (const sample of frame.vehicles) {
          vehicles.add(sample[0]);
          if (sample[4] === "bicycle") bicycles.add(sample[0]);
        }
        for (const sample of frame.persons) persons.add(sample[0]);
      }
      return { schemaVersion: data.schema_version, durationSeconds: data.duration_seconds,
        frames: data.frames.length, signals: data.signals.length,
        retainedVehicles: vehicles.size, retainedBicycles: bicycles.size,
        retainedPersons: persons.size, demandAuthoring: data.demand_authoring };
    })();
    return {
      audited: JSON.parse(root.dataset.auditedTrafficPreview ?? "null"),
      stale: root.dataset.auditedTrafficStale,
      observed: {
        vehicles: Number(root.dataset.sumoObservedVehicleCount),
        bicycles: Number(root.dataset.sumoObservedBicycleCount),
        persons: Number(root.dataset.sumoObservedPersonCount),
      },
      retained: {
        vehicles: Number(root.dataset.previewRetainedVehicleCount),
        bicycles: Number(root.dataset.previewRetainedBicycleCount),
        persons: Number(root.dataset.previewRetainedPersonCount),
      },
      omittedVehicles: Number(root.dataset.previewOmittedVehicleCount),
      displayIdsIsNull: map.trafficPreview.displayIds === null,
      summary,
    };
  });
}

try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ channel: "chromium", headless: true, timeout: 60_000,
    args: ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
      "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"] });
  context = await browser.newContext({ ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  page = await context.newPage();
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") report.consoleErrors.push(message.text());
  });
  page.on("requestfailed", requestFailure => report.requestFailures.push({
    method: requestFailure.method(), url: requestFailure.url(),
    error: requestFailure.failure()?.errorText ?? "failed",
  }));
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/authoring/v1/traffic-preview")) {
      report.apiResponses.push({ method: response.request().method(), path: url.pathname,
        status: response.status(), etag: response.headers()["etag"] ?? null });
    }
  });
  await hookStudio(page);

  await check("load a fresh public Studio with the complete default city", async () => {
    await page.goto(`${origin}/city-studio.html?tab=runtime`,
      { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const state = await waitForLoadedCity();
    assert.equal(state.renderBackend, "hardware");
    assert.equal(await page.evaluate(key => localStorage.getItem(key), storageKey), null);
    return { renderer: state.renderBackend, buildings: Number(state.buildingRenderTotal) };
  });

  await check("import the job's exact strict-v3 draft through the visible Studio control", async () => {
    await page.locator("#studio-import").setInputFiles({
      name: "t2-strict-v3-workspace.json",
      mimeType: "application/json",
      buffer: Buffer.from(`${JSON.stringify(request.draft, null, 2)}\n`),
    });
    await page.locator('#studio-save-status[data-state="saved"]')
      .filter({ hasText: "t2-strict-v3-workspace.json 已导入并保存" })
      .waitFor({ timeout: timeoutMs });
    const stored = await page.evaluate(key => JSON.parse(localStorage.getItem(key) ?? "null"), storageKey);
    assert.deepEqual(stored, request.draft);
    assert.equal(await page.locator('[name="seed"]').inputValue(), String(request.draft.seed));
    assert.equal(await page.locator('[name="traffic.vehicles"]').inputValue(), String(request.draft.traffic.vehicles));
    assert.equal(await page.locator('[name="traffic.bicycles"]').inputValue(), String(request.draft.traffic.bicycles));
    assert.equal(await page.locator('[name="traffic.pedestrians"]').inputValue(), String(request.draft.traffic.pedestrians));
    return { seed: stored.seed, traffic: stored.traffic, schemaVersion: stored.schema_version };
  });

  await check("submit the explicit pinned profile through the public traffic panel", async () => {
    const select = await waitForProfile();
    await select.selectOption(request.profile_id);
    const duration = page.getByLabel("交通预览时长（秒）", { exact: true });
    await duration.fill(String(request.duration_seconds));
    const button = page.getByRole("button", { name: "生成审计交通预览", exact: true });
    await page.waitForFunction(() => {
      const control = [...document.querySelectorAll("button")]
        .find(item => item.textContent === "生成审计交通预览");
      return control instanceof HTMLButtonElement && !control.disabled;
    }, undefined, { timeout: timeoutMs, polling: 100 });
    await button.click();
    await page.locator('.traffic-preview-result[data-state="ready"]')
      .filter({ hasText: "离线交通轨迹已完成字节与身份校验" })
      .waitFor({ timeout: timeoutMs });
    const text = await page.locator(".traffic-preview-result").innerText();
    assert.match(text, new RegExp(expectedJob.job_id));
    assert.match(text, new RegExp(expectedJob.trace.sha256));
    assert.match(text, new RegExp(expectedJob.canonical_audit.sha256));
    return { panel: text };
  });

  const readyState = await check("mount the complete audited trace without display filtering", async () => {
    await page.waitForFunction(jobId => {
      const raw = document.querySelector("#studio-map")?.getAttribute("data-audited-traffic-preview");
      return raw !== null && JSON.parse(raw).jobId === jobId;
    }, expectedJob.job_id, { timeout: timeoutMs, polling: 250 });
    const state = await readMapAuditState();
    assert.deepEqual(state.audited, {
      jobId: expectedJob.job_id,
      profileSha256: expectedJob.profile_sha256,
      workspaceSha256: expectedJob.workspace_sha256,
      traceSha256: expectedJob.trace.sha256,
      auditSha256: expectedJob.canonical_audit.sha256,
      durationSeconds: expectedJob.duration_seconds,
      previewScope: "offline-engineering-preview",
      formalProviderBound: false,
      executed: false,
      verified: false,
    });
    assert.equal(state.stale, "false");
    assert.equal(state.displayIdsIsNull, true, "Audited replay unexpectedly retained display filtering");
    assert.deepEqual(state.summary, expectedSummary);
    assert.deepEqual(state.retained, {
      vehicles: expectedSummary.retainedVehicles,
      bicycles: expectedSummary.retainedBicycles,
      persons: expectedSummary.retainedPersons,
    });
    assert.equal(state.retained.vehicles, state.observed.vehicles - state.omittedVehicles);
    assert.equal(state.retained.bicycles <= state.observed.bicycles, true);
    assert.equal(state.retained.persons <= state.observed.persons, true);
    await page.locator('[data-role="audited-traffic-provenance"][data-provenance="recorded"]')
      .waitFor({ state: "visible", timeout: timeoutMs });
    await screenshot("01-ready-traffic-panel.png", page.locator(".traffic-preview-panel"));
    await page.evaluate(() => {
      const map = window.__aeroStudioMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation);
      map.previewAnimation = 0;
      map.focusPreviewGroundEntity("bicycle");
      map.renderPreviewFrame();
    });
    await screenshot("02-ready-audited-traffic-map.png", page.locator("#studio-map"));
    return state;
  });
  report.ready = readyState;

  const staleState = await check("reject a changed demand as stale without filtering the mounted trace", async () => {
    const vehicles = page.locator('[name="traffic.vehicles"]');
    await vehicles.fill(String(request.draft.traffic.vehicles + 1));
    await vehicles.dispatchEvent("change");
    await page.locator('.traffic-preview-result[data-state="stale"]')
      .filter({ hasText: "已装配轨迹已标记为过时" }).waitFor({ timeout: timeoutMs });
    await page.waitForFunction(() => document.querySelector("#studio-map")
      ?.getAttribute("data-audited-traffic-stale") === "true",
    undefined, { timeout: timeoutMs, polling: 100 });
    const state = await readMapAuditState();
    assert.equal(state.stale, "true");
    assert.equal(state.displayIdsIsNull, true, "Stale handling silently installed display limits");
    assert.deepEqual(state.audited, readyState.audited, "Stale edit replaced the audited artifact identity");
    assert.deepEqual(state.summary, readyState.summary, "Stale edit replaced the mounted trace");
    const text = await page.locator(".traffic-preview-result").innerText();
    assert.match(text, /不再与当前草稿匹配/);
    assert.doesNotMatch(text, /未载入、未显示该轨迹/);
    await screenshot("03-stale-demand-rejection.png", page.locator(".traffic-preview-panel"));
    return { map: state, panel: text, currentVehicles: request.draft.traffic.vehicles + 1 };
  });
  report.stale = staleState;

  await check("browser run has no page, console, request, or HTTP contract failure", async () => {
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.consoleErrors, []);
    assert.deepEqual(report.requestFailures, []);
    const posts = report.apiResponses.filter(item => item.method === "POST"
      && item.path === "/authoring/v1/traffic-previews");
    const assets = report.apiResponses.filter(item => item.method === "GET"
      && item.path === expectedJob.trace.url);
    assert.deepEqual(posts.map(item => item.status), [202]);
    assert.deepEqual(assets.map(item => [item.status, item.etag]), [[200, `"${expectedJob.trace.sha256}"`]]);
    return { apiResponses: report.apiResponses.length, postStatus: 202,
      assetStatus: 200, errors: 0 };
  });
} catch (error) {
  report.failure = error instanceof Error ? `${error.stack ?? error.message}` : String(error);
} finally {
  await context?.close().catch(() => undefined);
  await browser?.close().catch(() => undefined);
  report.completedAt = new Date().toISOString();
  report.status = report.failure === undefined && report.checks.every(item => item.status === "PASS")
    ? "PASS" : "FAIL";
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify({ status: report.status, output, checks: report.checks.length,
    screenshots: report.screenshots.length, failure: report.failure ?? null }, null, 2));
  if (report.status !== "PASS") process.exitCode = 1;
}
