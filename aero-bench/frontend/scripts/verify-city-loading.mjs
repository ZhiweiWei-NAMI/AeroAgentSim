import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = new URL(process.argv[2] ?? "http://127.0.0.1:4173");
const output = resolve(process.argv[3] ?? "validation/city-loading-progress/browser");
await mkdir(output, { recursive: true });
const scene = JSON.parse(await readFile(new URL("../public/city-presentation/default-scene-v1.json", import.meta.url)));
const manifest = JSON.parse(await readFile(new URL(`../public${scene.mesh_pack.base_url}manifest.json`, import.meta.url)));
const delayedDigest = manifest.batches.at(-1).file.sha256;
const browser = await chromium.launch({ headless: true });
const report = { origin: origin.href, delayedDigest, progress: [], ready: null, failure: null, errors: [] };

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.on("pageerror", error => report.errors.push(error.message));
  await page.route(`**/assets/${delayedDigest}`, async route => {
    await new Promise(resolveDelay => setTimeout(resolveDelay, 2000));
    await route.continue();
  });
  await page.addInitScript(() => {
    globalThis.__cityProgress = [];
    let last = "";
    const observer = new MutationObserver(() => {
      const root = document.querySelector("#city-map");
      const bar = root?.querySelector(".city-scene-loading progress");
      if (!root || !bar) return;
      const sample = {
        stage: root.dataset.sceneLoadStage ?? "",
        value: bar.hasAttribute("value") ? bar.value : null,
        max: bar.max,
        hidden: root.querySelector(".city-scene-loading").hidden,
        ready: root.dataset.sceneReady === "true",
      };
      const key = JSON.stringify(sample);
      if (key !== last) { globalThis.__cityProgress.push({ at_ms: Math.round(performance.now()), ...sample }); last = key; }
    });
    observer.observe(document, { subtree: true, childList: true, attributes: true,
      attributeFilter: ["value", "max", "hidden", "data-scene-load-stage", "data-scene-ready"] });
  });
  await page.goto(new URL("?scene=1", origin).href, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.locator(".city-scene-loading:not([hidden]) progress[value]").waitFor({ timeout: 30000 });
  await page.waitForFunction(() => {
    const bar = document.querySelector(".city-scene-loading progress");
    return bar instanceof HTMLProgressElement && bar.max > 1 && bar.value > 0 && bar.value < bar.max;
  }, undefined, { timeout: 30000 });
  await page.screenshot({ path: resolve(output, "loading.png") });
  await page.locator("#city-map[data-scene-ready=true]").waitFor({ timeout: 180000 });
  await page.locator(".city-scene-loading").waitFor({ state: "hidden", timeout: 30000 });
  report.progress = await page.evaluate(() => globalThis.__cityProgress);
  report.ready = await page.locator("#city-map").evaluate(root => ({
    renderer: root.dataset.renderBackend, phaseSeconds: JSON.parse(root.dataset.sceneLoadPhaseSeconds),
    loadStage: root.dataset.sceneLoadStage, sceneError: root.dataset.sceneError ?? null,
    vehicleCount: Number(root.dataset.previewVisibleVehicles),
    pedestrianCount: Number(root.dataset.previewVisiblePedestrians),
    uavCount: Number(root.dataset.renderedUavCount),
  }));
  await page.screenshot({ path: resolve(output, "ready.png") });
  await context.close();

  const errorContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const errorPage = await errorContext.newPage();
  await errorPage.route("**/city-presentation/default-scene-v1.json", route =>
    route.fulfill({ status: 503, body: "test failure" }));
  await errorPage.goto(new URL("?scene=1", origin).href, { waitUntil: "domcontentloaded", timeout: 180000 });
  await errorPage.locator("#city-map[data-scene-error=true]").waitFor({ timeout: 30000 });
  report.failure = await errorPage.locator(".city-scene-loading").evaluate(panel => ({
    visible: !panel.hidden, role: panel.getAttribute("role"), text: panel.textContent,
  }));
  await errorPage.screenshot({ path: resolve(output, "failed.png") });
  await errorContext.close();
} finally {
  await browser.close();
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
}

if (report.errors.length || report.ready?.sceneError || report.ready?.vehicleCount < 1
    || report.ready?.pedestrianCount < 1 || report.ready?.uavCount < 1
    || !report.progress.some(sample => sample.value !== null && sample.value > 0 && sample.value < sample.max)
    || report.failure?.visible !== true || report.failure?.role !== "alert"
    || !report.failure.text.includes("503")) {
  throw new Error(`City loading check failed: ${JSON.stringify(report)}`);
}
console.log(JSON.stringify({ ready: report.ready, progressSamples: report.progress.length,
  failure: report.failure, errors: report.errors }, null, 2));
