/**
 * Real City Studio v3 save/reload/export acceptance.
 *
 * The browser authors every requested field through visible controls. The map hook is
 * read-only except for deterministic camera framing: it selects geometry that the same
 * production planner can render and measures the mounted authored-landscape plan.
 *
 * Usage:
 *   node scripts/capture-city-studio-v3-roundtrip.mjs <vite-origin> <output-dir>
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { relative, resolve } from "node:path";
import { chromium } from "playwright";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:5394").origin;
const output = resolve(process.argv[3]
  ?? "../validation/codex-takeover-20261001/U/studio-v3-roundtrip");
const timeoutMs = 300_000;
const storageKey = "aero-bench.city-workspace.v3";
const expected = Object.freeze({
  schemaVersion: "aero-bench.city-workspace/v3",
  seed: 424_242,
  name: "上海创作设计验收",
  orderId: "design-order-1",
  eventId: "event-1",
  landscapeId: "design-green-1",
});
const report = {
  schemaVersion: "aero-bench.city-studio-v3-roundtrip-acceptance/v1",
  status: "RUNNING",
  origin,
  output,
  startedAt: new Date().toISOString(),
  checks: [],
  screenshots: [],
  pageErrors: [],
  consoleErrors: [],
  requestFailures: [],
  authored: {},
  persisted: {},
  exported: {},
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

async function capturePanelAt(name, selector, edge = "top") {
  const details = await page.evaluate(({ targetSelector, targetEdge }) => {
    const scroller = document.querySelector("#studio-panel-scroll");
    const target = document.querySelector(targetSelector);
    if (!(scroller instanceof HTMLElement) || !(target instanceof HTMLElement)) {
      throw new Error(`Panel capture target is unavailable: ${targetSelector}`);
    }
    const targetTop = target.offsetTop;
    scroller.scrollTop = targetEdge === "bottom"
      ? targetTop + target.scrollHeight - scroller.clientHeight
      : targetTop;
    return { selector: targetSelector, edge: targetEdge, text: target.innerText,
      scrollTop: scroller.scrollTop, scrollHeight: target.scrollHeight };
  }, { targetSelector: selector, targetEdge: edge });
  await page.waitForTimeout(80);
  await screenshot(name);
  return details;
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
  const state = await page.locator("#studio-map").evaluate(root => ({ ...root.dataset }));
  assert.equal(state.sceneError, undefined);
  assert.equal(state.texturesReady, "true");
  assert.equal(state.buildingRenderTotal, "414");
  assert.equal(state.buildingRenderFailed, "0");
  return state;
}

async function activateTab(tab) {
  const link = page.locator(`#studio-tabs a[data-tab='${tab}']`);
  await link.click();
  await page.waitForFunction(value => {
    const target = document.querySelector(`#studio-tabs a[data-tab='${value}']`);
    const panel = document.querySelector("#studio-panel");
    return target?.classList.contains("is-active") && panel?.childElementCount > 0
      && panel.querySelector(".studio-panel-loading") === null;
  }, tab, { timeout: timeoutMs, polling: 100 });
}

async function changeInput(label, value) {
  const input = page.getByLabel(label, { exact: true });
  await input.fill(String(value));
  await input.dispatchEvent("change");
}

async function spatialMapView() {
  const svg = page.locator(".studio-spatial-map");
  await svg.scrollIntoViewIfNeeded();
  return svg.evaluate(node => {
    const box = node.getBoundingClientRect();
    const view = node.viewBox.baseVal;
    const scale = Math.min(box.width / view.width, box.height / view.height);
    const left = box.left + (box.width - view.width * scale) / 2;
    const top = box.top + (box.height - view.height * scale) / 2;
    const candidates = [];
    const background = node.firstElementChild;
    const openAt = (x, z) => {
      const hit = document.elementFromPoint(left + (x - view.x) * scale,
        top + (z - view.y) * scale);
      return hit === background || hit === node;
    };
    for (let row = 1; row <= 20; row += 1) {
      for (let column = 1; column <= 20; column += 1) {
        const x = view.x + view.width * column / 21;
        const z = view.y + view.height * row / 21;
        const hit = document.elementFromPoint(left + (x - view.x) * scale,
          top + (z - view.y) * scale);
        if (hit !== null && node.contains(hit)) {
          const clearanceScore = [[0, 0], [-15, 0], [15, 0], [0, -15], [0, 15],
            [-15, -15], [-15, 15], [15, -15], [15, 15]]
            .filter(([dx, dz]) => openAt(x + dx, z + dz)).length;
          candidates.push({ x, z, clearanceScore });
        }
      }
    }
    candidates.sort((left, right) => right.clearanceScore - left.clearanceScore);
    return { candidates, view: { x: view.x, z: view.y, width: view.width, height: view.height } };
  });
}

async function clickMapPoint(point) {
  const svg = page.locator(".studio-spatial-map");
  await svg.scrollIntoViewIfNeeded();
  const screen = await svg.evaluate((node, local) => {
    const box = node.getBoundingClientRect();
    const view = node.viewBox.baseVal;
    const scale = Math.min(box.width / view.width, box.height / view.height);
    const left = box.left + (box.width - view.width * scale) / 2;
    const top = box.top + (box.height - view.height * scale) / 2;
    return { x: left + (local.x - view.x) * scale,
      y: top + (local.z - view.y) * scale };
  }, point);
  await page.mouse.click(screen.x, screen.y);
}

async function placeFacility(toolLabel) {
  await page.getByRole("button", { name: toolLabel, exact: true }).click();
  const before = await page.locator("article[data-facility-id]").count();
  const { candidates } = await spatialMapView();
  assert(candidates.length > 0, "Spatial map has no candidate points");
  const failures = [];
  for (const point of candidates) {
    await clickMapPoint(point);
    await page.waitForTimeout(60);
    const cards = page.locator("article[data-facility-id]");
    if (await cards.count() === before + 1) {
      const id = await cards.last().getAttribute("data-facility-id");
      assert(id, "Placed facility has no ID");
      return { id, point, attempts: failures.length + 1, failures: failures.slice(0, 8) };
    }
    failures.push({ point,
      reason: (await page.locator(".studio-spatial-map-section [role='alert']").textContent())?.trim() ?? "" });
  }
  throw new Error(`No valid ${toolLabel} position after ${candidates.length} UI attempts`);
}

async function selectLandscapeCandidate(excluded) {
  return page.evaluate(async blocked => {
    const map = window.__aeroStudioMap;
    const geometry = map?.cityVegetationLayer?.authoredLandscapeGeometry;
    if (geometry === undefined) throw new Error("Verified authored-landscape geometry is unavailable");
    const { planCityAuthoredLandscape } = await import("/src/city-authored-landscape.ts");
    const coordinates = geometry.extent.outline;
    const xs = coordinates.map(point => point[0]);
    const zs = coordinates.map(point => point[1]);
    const bounds = { minX: Math.min(...xs), maxX: Math.max(...xs),
      minZ: Math.min(...zs), maxZ: Math.max(...zs) };
    const candidates = [];
    const width = 36, depth = 24;
    for (let row = 1; row <= 18; row += 1) {
      for (let column = 1; column <= 18; column += 1) {
        const cx = bounds.minX + (bounds.maxX - bounds.minX) * column / 19;
        const cz = bounds.minZ + (bounds.maxZ - bounds.minZ) * row / 19;
        if (blocked.some(point => Math.hypot(point.x - cx, point.z - cz) < 75)) continue;
        const item = { id: "design-green-1", label: "科技绿荫庭院", provenance: "authored",
          kind: "green", polygon: [
            { x: cx - width / 2, z: cz - depth / 2 },
            { x: cx + width / 2, z: cz - depth / 2 },
            { x: cx + width / 2, z: cz + depth / 2 },
            { x: cx - width / 2, z: cz + depth / 2 },
          ] };
        try {
          const plan = planCityAuthoredLandscape([item], geometry);
          const ratio = plan.stats.drawnAreaM2 / plan.stats.sourceAreaM2;
          if (plan.items.length === 1 && plan.stats.drawnAreaM2 >= 500 && ratio >= 0.8) {
            candidates.push({ item, predicted: plan.stats, ratio });
          }
        } catch { /* This grid point is not an authorable surface. */ }
      }
    }
    candidates.sort((left, right) => right.predicted.drawnAreaM2 - left.predicted.drawnAreaM2
      || right.ratio - left.ratio);
    if (candidates.length === 0) throw new Error("No nonempty authored-landscape candidate passed production planning");
    return candidates[0];
  }, excluded);
}

async function authorLandscape(candidate) {
  await page.getByRole("button", { name: "添加创作景观", exact: true }).click();
  await changeInput("景观 ID", expected.landscapeId);
  await changeInput("景观标签", candidate.item.label);
  await page.getByLabel("景观类型", { exact: true }).selectOption(candidate.item.kind);
  for (let index = 0; index < candidate.item.polygon.length; index += 1) {
    await page.getByRole("button", { name: "添加顶点", exact: true }).click();
  }
  for (const [index, point] of candidate.item.polygon.entries()) {
    await changeInput(`顶点 ${index + 1} X / m`, point.x);
    await changeInput(`顶点 ${index + 1} Z / m`, point.z);
  }
  await page.getByRole("button", { name: "保存景观", exact: true }).click();
  await page.locator(`[data-landscape-id='${expected.landscapeId}']`).waitFor({ timeout: timeoutMs });
  const editor = page.locator("[data-role='landscape-editor']");
  if (await editor.count()) await editor.getByRole("button", { name: "取消", exact: true }).click();
  await page.waitForFunction(id => {
    const value = document.querySelector("#studio-map")?.getAttribute("data-city-authored-landscape");
    if (!value) return false;
    const plan = JSON.parse(value);
    return plan.items?.length === 1 && plan.items[0]?.id === id && plan.stats?.drawnAreaM2 > 0;
  }, expected.landscapeId, { timeout: timeoutMs, polling: 250 });
  const plan = await page.locator("#studio-map").evaluate(root =>
    JSON.parse(root.dataset.cityAuthoredLandscape ?? "null"));
  assert.equal(plan.schemaVersion, "aero-bench.city-authored-landscape-plan/v1");
  assert(plan.stats.drawnAreaM2 >= 500, "Rendered authored landscape is too small for visual evidence");
  assert.equal(plan.items[0].provenance, "authored");
  await page.locator('[data-role="authored-landscape-provenance"][data-provenance="authored"]')
    .waitFor({ state: "visible", timeout: timeoutMs });
  return plan;
}

async function focusAuthoredLandscape(polygon) {
  await page.evaluate(points => {
    const map = window.__aeroStudioMap;
    map.previewPlaying = false;
    cancelAnimationFrame(map.previewAnimation);
    map.previewAnimation = 0;
    map.controls.enableDamping = false;
    const xs = points.map(point => point.x), zs = points.map(point => point.z);
    const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const cz = (Math.min(...zs) + Math.max(...zs)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...zs) - Math.min(...zs), 30);
    map.setCameraMode("free");
    map.camera.position.set(cx + span * 0.9, span * 0.8, cz + span * 0.95);
    map.controls.target.set(cx, 0, cz);
    map.controls.update();
    map.renderPreviewFrame();
  }, polygon);
}

async function authorRuntime() {
  await activateTab("runtime");
  const seed = page.locator('[name="seed"]');
  await seed.fill(String(expected.seed));
  await seed.dispatchEvent("change");
  await page.locator('[name="environment.preset"]').selectOption("heavyRain");
  await page.locator('[name="environment.timeOfDay"]').selectOption("twilight");
  await page.waitForFunction(() => {
    const data = document.querySelector("#studio-map")?.dataset;
    return data?.workspacePrecipitation === "rain" && data?.cityTimeOfDay === "twilight";
  }, undefined, { timeout: timeoutMs, polling: 250 });
}

async function authorOrder(vertiportId, hubId) {
  await activateTab("algorithm");
  const panel = page.locator(".studio-logistics-panel");
  await panel.getByRole("button", { name: "添加手动订单", exact: true }).click();
  await panel.getByLabel("新订单 ID", { exact: true }).fill(expected.orderId);
  await panel.getByLabel("新订单 来源设施", { exact: true }).selectOption(vertiportId);
  await panel.getByLabel("新订单 目的设施", { exact: true }).selectOption(hubId);
  await panel.getByLabel("新订单 货物重量 / kg", { exact: true }).fill("2.5");
  await panel.getByLabel("新订单 释放时间 / s", { exact: true }).fill("15");
  await panel.getByLabel("新订单 交付期限 / s", { exact: true }).fill("420");
  await panel.getByRole("button", { name: "保存订单", exact: true }).click();
  await panel.locator(`[data-order-id='${expected.orderId}']`).waitFor({ timeout: timeoutMs });
  await panel.getByRole("button", { name: "编辑生成配置", exact: true }).click();
  await panel.getByLabel("生成随机种子", { exact: true }).fill("314159");
  await panel.getByLabel("生成订单数量", { exact: true }).fill("6");
  await panel.getByLabel("生成开始时间 / s", { exact: true }).fill("30");
  await panel.getByLabel("生成结束时间 / s", { exact: true }).fill("900");
  await panel.getByLabel("生成最小货重 / kg", { exact: true }).fill("0.4");
  await panel.getByLabel("生成最大货重 / kg", { exact: true }).fill("3.5");
  await panel.getByLabel("生成交付期限超前 / s", { exact: true }).fill("240");
  await panel.getByRole("button", { name: "保存生成配置", exact: true }).click();
}

async function authorEvent() {
  await activateTab("events");
  await page.getByRole("button", { name: "添加事件", exact: true }).click();
  const form = page.locator('[role="group"][aria-label^="事件 "]');
  await form.getByLabel("发生时间（秒）", { exact: true }).fill("45");
  await form.getByLabel("事件类型", { exact: true }).selectOption("weather.changed");
  await form.getByLabel("目标 ID", { exact: true }).fill("district.authored-1");
  await form.getByLabel("事件载荷（JSON 对象）", { exact: true })
    .fill('{"precipitation":"rain","rate_mm_per_h":12}');
  await page.locator(`[data-event-id='${expected.eventId}']`).waitFor({ timeout: timeoutMs });
}

async function readStoredDraft() {
  return page.evaluate(key => {
    const value = localStorage.getItem(key);
    return value === null ? null : JSON.parse(value);
  }, storageKey);
}

function assertDraft(draft) {
  assert(draft !== null, "Workspace v3 was not saved");
  assert.equal(draft.schema_version, expected.schemaVersion);
  assert.equal(draft.name, expected.name);
  assert.equal(draft.seed, expected.seed);
  assert.equal(draft.environment.precipitation, "rain");
  assert.equal(draft.environment.precipitationRateMmPerH, 12);
  assert.equal(draft.environment.timeOfDay, "twilight");
  assert.equal(draft.orders.length, 1);
  assert.equal(draft.orders[0].id, expected.orderId);
  assert.equal(draft.orderGeneration.seed, 314_159);
  assert.equal(draft.orderGeneration.maxOrders, 6);
  assert.equal(draft.events.length, 1);
  assert.equal(draft.events[0].id, expected.eventId);
  assert.deepEqual(draft.events[0].payload, { precipitation: "rain", rate_mm_per_h: 12 });
  assert.equal(draft.authoredLandscape.length, 1);
  assert.equal(draft.authoredLandscape[0].id, expected.landscapeId);
  assert.equal(draft.authoredLandscape[0].provenance, "authored");
  assert.equal(draft.facilities.length, 2);
}

async function captureOrders(label) {
  await activateTab("algorithm");
  await capturePanelAt(`${label}-orders-top.png`, ".studio-logistics-panel", "top");
  await capturePanelAt(`${label}-orders-bottom.png`, ".studio-logistics-panel", "bottom");
}

try {
  await mkdir(output, { recursive: true });
  browser = await chromium.launch({ channel: "chromium", headless: true, timeout: 60_000,
    args: ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
      "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"] });
  context = await browser.newContext({ ignoreHTTPSErrors: true,
    viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, acceptDownloads: true });
  page = await context.newPage();
  page.on("pageerror", error => report.pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") report.consoleErrors.push(message.text());
  });
  page.on("requestfailed", request => report.requestFailures.push({
    method: request.method(), url: request.url(), error: request.failure()?.errorText ?? "failed",
  }));
  await hookStudio(page);

  await check("load a fresh strict-v3 Studio workspace and complete city", async () => {
    await page.goto(`${origin}/city-studio.html?tab=spatial`,
      { waitUntil: "domcontentloaded", timeout: timeoutMs });
    const state = await waitForLoadedCity();
    assert.equal(await readStoredDraft(), null, "Isolated browser context unexpectedly has a saved draft");
    return { renderer: state.renderBackend, buildings: Number(state.buildingRenderTotal) };
  });

  const facilities = await check("place valid vertiport and hub through the spatial UI", async () => {
    await activateTab("spatial");
    const vertiport = await placeFacility("放置起降点");
    const hub = await placeFacility("放置物流中转站");
    await page.waitForFunction(() => document.querySelector("#studio-map")?.dataset.workspaceFacilityCount === "2",
      undefined, { timeout: timeoutMs, polling: 250 });
    return { vertiport, hub };
  });

  const candidate = await check("select a nonempty design polygon with the production planner", async () => {
    const selected = await selectLandscapeCandidate([facilities.vertiport.point, facilities.hub.point]);
    assert(selected.predicted.drawnAreaM2 > 0);
    return selected;
  });

  const landscapePlan = await check("author and render a nonempty landscape through visible controls", async () => {
    const plan = await authorLandscape(candidate);
    await focusAuthoredLandscape(candidate.item.polygon);
    await screenshot("01-authored-landscape-rendered.png", page.locator("#studio-map"));
    await screenshot("01-authored-landscape-panel.png", page.locator(`[data-landscape-id='${expected.landscapeId}']`));
    return plan;
  });
  report.authored.landscapeCandidate = candidate;
  report.authored.landscapePlan = landscapePlan;

  await check("author strict-v3 seed and environment", async () => {
    await authorRuntime();
    await screenshot("02-runtime-seed-environment.png");
    return { seed: expected.seed, precipitation: "rain", rateMmPerH: 12, timeOfDay: "twilight" };
  });

  await check("author one manual order and deterministic generation inputs", async () => {
    await authorOrder(facilities.vertiport.id, facilities.hub.id);
    await captureOrders("03-authored");
    return { orderId: expected.orderId, generationSeed: 314_159, maxOrders: 6 };
  });

  await check("author a deterministic event and payload timeline", async () => {
    await authorEvent();
    await screenshot("04-authored-event-editor.png", page.locator('[role="group"][aria-label^="事件 "]'));
    const timeline = await capturePanelAt("04-authored-event-timeline.png", ".studio-event-timeline");
    assert.match(timeline.text, /weather\.changed/);
    assert.match(timeline.text, /rate_mm_per_h/);
    return { eventId: expected.eventId, atS: 45, type: "weather.changed" };
  });

  const saved = await check("save exact workspace v3 to isolated browser storage", async () => {
    const name = page.locator("#studio-name");
    await name.fill(expected.name);
    await name.dispatchEvent("change");
    await page.waitForFunction(() => {
      const control = document.querySelector("#studio-save");
      return control instanceof HTMLButtonElement && !control.disabled;
    }, undefined, { timeout: timeoutMs, polling: 100 });
    await page.locator("#studio-save").click();
    await page.locator('#studio-save-status[data-state="saved"]')
      .filter({ hasText: "已保存到本地浏览器" }).waitFor({ timeout: timeoutMs });
    const draft = await readStoredDraft();
    assertDraft(draft);
    return draft;
  });
  report.persisted.beforeReload = saved;

  await check("reload and restore every authored v3 field and rendered design", async () => {
    await page.reload({ waitUntil: "domcontentloaded", timeout: timeoutMs });
    await waitForLoadedCity();
    await page.locator("#studio-save-status").filter({ hasText: "本地草稿已加载" })
      .waitFor({ timeout: timeoutMs });
    const restored = await readStoredDraft();
    assert.deepEqual(restored, saved);
    assertDraft(restored);
    await page.waitForFunction(id => {
      const raw = document.querySelector("#studio-map")?.getAttribute("data-city-authored-landscape");
      if (!raw) return false;
      const plan = JSON.parse(raw);
      return plan.items?.[0]?.id === id && plan.stats?.drawnAreaM2 > 0;
    }, expected.landscapeId, { timeout: timeoutMs, polling: 250 });
    const restoredPlan = await page.locator("#studio-map").evaluate(root =>
      JSON.parse(root.dataset.cityAuthoredLandscape ?? "null"));
    assert.deepEqual(restoredPlan, landscapePlan);
    report.persisted.afterReload = restored;
    report.persisted.restoredLandscapePlan = restoredPlan;
    await activateTab("spatial");
    await page.locator(`[data-landscape-id='${expected.landscapeId}']`).waitFor();
    await focusAuthoredLandscape(restored.authoredLandscape[0].polygon);
    await screenshot("05-reloaded-authored-landscape.png", page.locator("#studio-map"));
    await captureOrders("06-reloaded");
    await activateTab("events");
    await page.locator(`[data-event-id='${expected.eventId}']`).waitFor();
    const timeline = await capturePanelAt("07-reloaded-event-timeline.png", ".studio-event-timeline");
    assert.match(timeline.text, /weather\.changed/);
    assert.match(timeline.text, /rate_mm_per_h/);
    return { storedBytes: Buffer.byteLength(JSON.stringify(restored)),
      landscapeDrawnAreaM2: restoredPlan.stats.drawnAreaM2 };
  });

  await check("export the same strict-v3 document without losing fields", async () => {
    await page.waitForFunction(() => {
      const control = document.querySelector("#studio-export");
      return control instanceof HTMLButtonElement && !control.disabled;
    }, undefined, { timeout: timeoutMs, polling: 100 });
    const [download] = await Promise.all([
      page.waitForEvent("download", { timeout: timeoutMs }),
      page.locator("#studio-export").click(),
    ]);
    const exportPath = resolve(output, "city-workspace-v3-export.json");
    await download.saveAs(exportPath);
    const bytes = await readFile(exportPath);
    const exported = JSON.parse(bytes.toString("utf8"));
    assert.deepEqual(exported, saved);
    assertDraft(exported);
    report.exported = { file: relative(output, exportPath), suggestedFilename: download.suggestedFilename(),
      sizeBytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex"), document: exported };
    return { file: report.exported.file, sha256: report.exported.sha256,
      sizeBytes: report.exported.sizeBytes };
  });

  await check("browser run has no hidden page, console, or request failure", async () => {
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.consoleErrors, []);
    assert.deepEqual(report.requestFailures, []);
    return { pageErrors: 0, consoleErrors: 0, requestFailures: 0 };
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
