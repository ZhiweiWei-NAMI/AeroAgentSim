/** Real published-region UI acceptance; only the marked 503 case is injected. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:5394").origin;
const output = resolve(process.argv[3] ?? "../validation/codex-takeover-20261001/S/region-switch");
const inputPath = resolve(process.argv[4]
  ?? fileURLToPath(new URL("../../validation/codex-takeover-20261001/U/studio-v3-roundtrip-run4/city-workspace-v3-export.json", import.meta.url)));
const storageKey = "aero-bench.city-workspace.v3";
const timeout = 300_000;
const report = {
  schemaVersion: "aero-bench.city-region-switch-browser-acceptance/v1",
  status: "RUNNING", origin, startedAt: new Date().toISOString(),
  checks: [], screenshots: [], pageErrors: [], consoleErrors: [], resourceDiagnostics: [],
  negativeCase: { injectedStatus: 503, routeHits: 0 },
};
let browser;
let page;
let negativePhase = false;

async function check(name, action) {
  const started = performance.now();
  try {
    const details = await action();
    report.checks.push({ name, status: "PASS", durationMs: Math.round(performance.now() - started), details });
    await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
    return details;
  } catch (error) {
    report.checks.push({ name, status: "FAIL", error: String(error) });
    throw error;
  }
}

async function screenshot(file) {
  await page.screenshot({ path: resolve(output, file), animations: "disabled" });
  const bytes = await readFile(resolve(output, file));
  report.screenshots.push({ file, sizeBytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex") });
}

async function storedText() {
  return page.evaluate(key => localStorage.getItem(key), storageKey);
}

async function waitLoaded(source, buildings) {
  const result = await page.waitForFunction(({ expectedSource, expectedBuildings }) => {
    const map = document.querySelector("#studio-map");
    const data = map?.dataset;
    if (data?.sceneError || document.querySelector('[data-role="scene-preset-result"]')?.dataset.state === "error") {
      return "failed";
    }
    return data?.sceneReady === "true" && data.texturesReady === "true"
      && data.skyReady === "true" && data.roadAssetsVerified === "true"
      && data.roadAssetsSourceScene === expectedSource
      && Number(data.buildingRenderTotal) === expectedBuildings && data.buildingRenderFailed === "0"
      && map.querySelector(".city-scene-loading")?.hidden === true;
  }, { expectedSource: source, expectedBuildings: buildings }, { timeout, polling: 250 });
  if (await result.jsonValue() === "failed") {
    throw new Error(await page.locator('[data-role="scene-preset-result"]').innerText()
      || await page.locator("#studio-map").getAttribute("data-scene-error-message")
      || "Published region failed to load");
  }
  return page.locator("#studio-map").evaluate(root => ({ ...root.dataset }));
}

async function waitPersisted(path) {
  await page.waitForFunction(({ key, expectedPath }) => {
    const raw = localStorage.getItem(key);
    return raw !== null && JSON.parse(raw).scenePath === expectedPath
      && document.querySelector("#studio-save-status")?.dataset.state === "saved";
  }, { key: storageKey, expectedPath: path }, { timeout, polling: 200 });
  return JSON.parse(await storedText());
}

try {
  await mkdir(output, { recursive: true });
  const input = await readFile(inputPath);
  const incoming = JSON.parse(input);
  assert.equal(incoming.schema_version, "aero-bench.city-workspace/v3");
  assert(incoming.facilities.length > 0 && incoming.orders.length > 0 && incoming.authoredLandscape.length > 0,
    "Region-switch fixture must contain real authored scene-local data");
  report.initialDraft = { path: inputPath, sha256: createHash("sha256").update(input).digest("hex"),
    sizeBytes: input.length, facilities: incoming.facilities.length, orders: incoming.orders.length,
    authoredLandscape: incoming.authoredLandscape.length };
  browser = await chromium.launch({ headless: true, channel: "chromium", args: [
    "--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
    "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist",
  ] });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1, ignoreHTTPSErrors: true });
  page = await context.newPage();
  page.setDefaultTimeout(timeout);
  page.on("pageerror", error => report.pageErrors.push(String(error)));
  page.on("console", message => {
    if (message.type() !== "error") return;
    const url = new URL(message.location().url, origin);
    const item = { message: message.text(), url: message.location().url,
      expectedNegativeFixture: negativePhase && message.text().includes("503")
        && url.pathname === "/city-presentation/jingan-engineering-preview-v3.json" };
    // Preserve the browser's optional favicon request separately from application failures.
    if (url.origin === origin && url.pathname === "/favicon.ico"
        && message.text() === "Failed to load resource: the server responded with a status of 404 (Not Found)") {
      report.resourceDiagnostics.push({ ...item, classification: "unpublished-optional-favicon" });
    } else report.consoleErrors.push(item);
  });
  await check("load current published Huangpu city in isolated browser", async () => {
    await page.goto(`${origin}/city-studio.html?tab=runtime`, { waitUntil: "domcontentloaded", timeout });
    const data = await waitLoaded("shanghai-huangpu-east-v1", 414);
    assert.equal(data.renderBackend, "hardware");
    assert.equal(await storedText(), null);
    return { backend: data.renderBackend, source: data.roadAssetsSourceScene, buildings: data.buildingRenderTotal };
  });
  const saved = await check("import a previously authored strict-v3 draft through the file UI", async () => {
    await page.locator("#studio-import").setInputFiles({
      name: "previous-region-draft.json", mimeType: "application/json", buffer: input,
    });
    const draft = await waitPersisted(incoming.scenePath);
    assert.deepEqual(draft, incoming);
    await page.waitForFunction(count => document.querySelector("#studio-map")?.dataset.workspaceFacilityCount === String(count),
      incoming.facilities.length, { timeout });
    return await storedText();
  });
  await check("choosing Jing'an alone leaves draft and persisted bytes unchanged", async () => {
    await page.getByLabel("普通城市预览地区", { exact: true }).selectOption("jingan");
    assert.equal(await storedText(), saved);
    assert.equal(await page.locator("#studio-name").inputValue(), incoming.name);
    assert.match(await page.locator('[data-role="scene-preset-consequence"]').innerText(), /不会跨区迁移/);
    assert.equal(await page.locator("#studio-map").getAttribute("data-road-assets-source-scene"), "shanghai-huangpu-east-v1");
    await screenshot("01-inert-region-selection.png");
    return { selection: "jingan", persistedUnchanged: true };
  });
  await check("injected unavailable new scene preserves the full previous draft", async () => {
    negativePhase = true;
    const routePattern = "**/city-presentation/jingan-engineering-preview-v3.json";
    await page.route(routePattern, async route => {
      report.negativeCase.routeHits += 1;
      await route.fulfill({ status: 503, contentType: "application/json",
        body: '{"acceptance_injected_unavailable":true}' });
    });
    await page.getByRole("button", { name: "校验并切换地区", exact: true }).click();
    await page.locator('[data-role="scene-preset-result"][data-state="error"]').waitFor();
    assert.equal(await storedText(), saved);
    assert.equal(await page.locator("#studio-name").inputValue(), incoming.name);
    assert.equal(await page.locator("#studio-map").getAttribute("data-road-assets-source-scene"), "shanghai-huangpu-east-v1");
    const error = await page.locator('[data-role="scene-preset-result"]').innerText();
    assert.match(error, /当前草稿与本地存储未改动/);
    assert.equal(report.negativeCase.routeHits, 1);
    await screenshot("02-rejected-region-load.png");
    await page.unroute(routePattern);
    negativePhase = false;
    return { injectedFixture: true, persistedUnchanged: true, error };
  });
  await check("explicit switch loads verified Jing'an and clears foreign scene-local data", async () => {
    // Failed loading remounts the panel from the unchanged active draft.
    await page.getByLabel("普通城市预览地区", { exact: true }).selectOption("jingan");
    await page.getByRole("button", { name: "校验并切换地区", exact: true }).click();
    const data = await waitLoaded("shanghai-jingan-osm-v1", 43);
    const draft = await waitPersisted("/city-presentation/jingan-engineering-preview-v3.json");
    assert.equal(draft.seed, 24_427);
    assert.deepEqual(draft.traffic, { vehicles: 60, pedestrians: 3, bicycles: 4 });
    for (const key of ["facilities", "fleet", "orders", "performanceProfiles", "authoredLandscape",
      "airspace", "events", "actionRules", "stateKeyframes", "labelRules"]) assert.deepEqual(draft[key], [], key);
    assert.equal(await page.locator("#studio-map-place").innerText(), "上海 · 静安");
    assert.equal(data.cityAuthoredLandscape, undefined);
    const drawn = JSON.parse(data.cityGroundCoverDrawnSet);
    report.jingan = { draft, map: data, drawn };
    await screenshot("03-jingan-published-preview.png");
    return { source: data.roadAssetsSourceScene, buildings: data.buildingRenderTotal, drawn, draft };
  });
  await check("reload retains Jing'an rather than reverting to the default city", async () => {
    await page.reload({ waitUntil: "domcontentloaded", timeout });
    const data = await waitLoaded("shanghai-jingan-osm-v1", 43);
    assert.equal(await page.getByLabel("普通城市预览地区", { exact: true }).inputValue(), "jingan");
    assert.equal(JSON.parse(await storedText()).scenePath, "/city-presentation/jingan-engineering-preview-v3.json");
    await screenshot("04-jingan-reloaded.png");
    return { source: data.roadAssetsSourceScene, buildings: data.buildingRenderTotal };
  });
  await check("explicit return to Huangpu creates a fresh Huangpu draft", async () => {
    await page.getByLabel("普通城市预览地区", { exact: true }).selectOption("huangpu");
    await page.getByRole("button", { name: "校验并切换地区", exact: true }).click();
    const data = await waitLoaded("shanghai-huangpu-east-v1", 414);
    const draft = await waitPersisted("/city-presentation/default-scene-v1.json");
    assert.equal(draft.seed, 1);
    assert.deepEqual(draft.facilities, []);
    assert.deepEqual(draft.orders, []);
    assert.deepEqual(draft.authoredLandscape, []);
    assert.equal(await page.locator("#studio-map-place").innerText(), "上海 · 黄浦");
    await screenshot("05-huangpu-new-draft.png");
    return { source: data.roadAssetsSourceScene, buildings: data.buildingRenderTotal, seed: draft.seed };
  });
  await check("no unaccounted application browser failures", async () => {
    assert.deepEqual(report.pageErrors, []);
    assert.deepEqual(report.consoleErrors.filter(item => !item.expectedNegativeFixture), []);
    return { expectedNegativeConsoleErrors: report.consoleErrors.filter(item => item.expectedNegativeFixture).length,
      resourceDiagnostics: report.resourceDiagnostics };
  });
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL";
  report.error = error instanceof Error ? error.stack : String(error);
  if (page) {
    report.lastMap = await page.locator("#studio-map").evaluate(root => ({ ...root.dataset })).catch(() => null);
    await screenshot("failure.png").catch(() => {});
  }
  process.exitCode = 1;
} finally {
  await browser?.close();
  report.finishedAt = new Date().toISOString();
  await mkdir(output, { recursive: true });
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  console.log(JSON.stringify({ status: report.status, checks: report.checks.length, output, error: report.error }));
}
