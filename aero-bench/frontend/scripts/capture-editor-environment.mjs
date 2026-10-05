/** GPU browser acceptance for the city-studio draft environment loop on a
 * building-only render scene: time-of-day + rain edits persist as a v2 workspace
 * draft, survive a reload, and round-trip through export/import.
 * Usage: node scripts/capture-editor-environment.mjs <origin> <output-directory> */
import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5310";
const output = resolve(process.argv[3] ?? "../validation/frontend-opus-20260930/editor");
await mkdir(output, { recursive: true });
const STORAGE_KEY = "aero-bench.city-workspace.v2";
const report = {
  origin,
  scope: "city-studio draft environment: v2 persistence, reload restore, export/import round trip",
  checks: [], errors: [], frames: [],
};
const GPU_ARGS = ["--enable-gpu", "--use-angle=vulkan", "--enable-features=Vulkan",
  "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"];
function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// The stated GPU is required; a software renderer is reported as a failure, not substituted.
async function launchBrowser() {
  const browser = await chromium.launch({ headless: true, args: GPU_ARGS, timeout: 60000 });
  const probe = await browser.newPage();
  const renderer = await withTimeout(probe.evaluate(() => {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    if (gl === null) return null;
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  }), 30000, "GPU WebGL probe");
  await probe.close();
  if (renderer === null || !/NVIDIA/i.test(renderer)) {
    await browser.close();
    throw new Error(`GPU WebGL renderer required; got ${renderer}`);
  }
  return { browser, mode: "gpu", renderer };
}
const { browser, mode: launchMode, renderer: probeRenderer } = await launchBrowser();
report.launchMode = launchMode;
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on("pageerror", error => report.errors.push(error.message));
page.on("console", message => { if (message.type() === "error") report.errors.push(message.text()); });
page.on("response", response => {
  if (response.status() >= 400) report.errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
});
// The studio never exposes its map; publish it when PublicTraceMap is constructed.
await page.route(/\/src\/city-studio\.ts(\?.*)?$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  const pattern = /this\.map = new PublicTraceMap\(/;
  if (!pattern.test(source)) throw new Error("Editor environment hook no longer matches city-studio.ts");
  await route.fulfill({ response, body: source.replace(pattern, "window.__studioMap = this.map = new PublicTraceMap(") });
});
function check(name, passed, details) {
  report.checks.push({ name, passed, details });
  if (!passed) throw new Error(`Editor environment acceptance failed: ${name}`);
}
const draftEnvironment = () => page.evaluate(key => {
  const raw = window.localStorage.getItem(key);
  if (raw === null) return null;
  return JSON.parse(raw).environment;
}, STORAGE_KEY);
const mapState = () => page.evaluate(() => {
  const map = window.__studioMap;
  if (map === undefined) return null;
  return {
    workspaceApplied: map.workspaceConfig !== null,
    precipitation: map.weather?.provenance?.settings?.precipitation ?? null,
    camera: map.camera.position.toArray(), target: map.controls.target.toArray(),
  };
});
try {
  // Fresh profile: the studio must fall back to its default draft for the default scene.
  // "commit" because the vite dev server keeps domcontentloaded pending on this page;
  // the #studio-map readiness wait below is the real gate.
  await page.goto(`${origin}/city-studio.html?city=/city-presentation/default-scene-v1.json`, { waitUntil: "commit", timeout: 120000 });
  await page.waitForFunction(() => {
    const data = document.querySelector("#studio-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, undefined, { timeout: 120000 });
  const sceneError = await page.locator("#studio-map").getAttribute("data-scene-error-message");
  if (sceneError) throw new Error(`Scene failed: ${sceneError}`);
  check("render scene loads without stored draft", await page.evaluate(key => window.localStorage.getItem(key) === null, STORAGE_KEY),
    await page.evaluate(key => window.localStorage.getItem(key), STORAGE_KEY));
  await page.waitForFunction(() => window.__studioMap !== undefined
    && window.__studioMap.renderStreamer !== null
    && window.__studioMap.renderStreamer.progress.active === 0, undefined, { timeout: 120000 });
  report.renderer = await page.evaluate(() => {
    const gl = window.__studioMap.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
  check("workspace draft is applied on the render scene", (await mapState()).workspaceApplied, await mapState());

  // Camera is set once here and reused for both screenshots so the pair is comparable.
  await page.evaluate(() => {
    const map = window.__studioMap;
    map.camera.position.set(-240, 90, 120);
    map.controls.target.set(-160, 20, -120);
    map.controls.update();
    map.renderStreamer.update(map.camera);
    map.renderStaticFrame();
  });
  await page.screenshot({ path: resolve(output, "before-reload.png") });
  report.frames.push({
    file: "before-reload.png",
    sha256: createHash("sha256").update(await readFile(resolve(output, "before-reload.png"))).digest("hex"),
    ...await mapState(),
  });

  // Edit the draft through the studio UI: the runtime panel sets night, the
  // render-bar 时段 button cycles day→twilight→night on the scene.
  // The studio page hides the map's on-canvas bar through its pre-existing
  // `#city-studio .city-preview-controls` rule, so the render-bar buttons are
  // driven with DOM clicks on the real controls; every effect below (workspace
  // re-apply, persistence, scene lighting) is the genuine handler output.
  const precipitation = page.locator('[name="environment.precipitation"]');
  check("runtime panel shows the time-of-day select", await page.locator('[name="environment.timeOfDay"]').isVisible(),
    { precipitationVisible: await precipitation.isVisible() });
  const renderBarStart = await page.evaluate(() => window.__studioMap.cityTimeOfDay);
  const clickRenderBar = label => page.evaluate(aria => {
    const button = [...document.querySelectorAll("#studio-map button")]
      .find(node => node.getAttribute("aria-label") === aria);
    if (button === undefined) throw new Error(`render-bar button ${aria} not found`);
    button.click();
  }, label);
  await clickRenderBar("切换建筑渲染场景时段");
  await page.waitForFunction(previous => window.__studioMap.cityTimeOfDay !== previous
    && window.__studioMap.cityTimeOfDay === "twilight", renderBarStart, { timeout: 30000, polling: 250 });
  const barNote = page.locator('[data-role="render-weather-note"]');
  check("render-bar cycle lands on twilight and the note reports a workspace draft edit",
    await page.evaluate(() => window.__studioMap.cityTimeOfDay) === "twilight"
    && await barNote.getAttribute("data-mode") === "workspace"
    && (await barNote.textContent())?.includes("草稿环境 · 修改计入工作区草稿"),
    { note: await barNote.textContent(), timeOfDay: await page.evaluate(() => window.__studioMap.cityTimeOfDay) });
  const barEdit = await draftEnvironment();
  const saveState = () => page.evaluate(() => document.querySelector("#studio-save-status")?.dataset.state);
  check("render-bar edit marks the draft dirty without writing storage",
    barEdit?.timeOfDay !== "twilight" && await saveState() === "dirty", { stored: barEdit, state: await saveState() });
  await clickRenderBar("切换建筑渲染场景时段");
  await page.waitForFunction(() => window.__studioMap.cityTimeOfDay === "night", undefined,
    { timeout: 30000, polling: 250 });
  check("runtime panel follows the render-bar time of day",
    await page.locator('[name="environment.timeOfDay"]').inputValue() === "night",
    await page.locator('[name="environment.timeOfDay"]').inputValue());

  // Set the remaining weather fields in the runtime panel and save the draft.
  await precipitation.selectOption("rain");
  const rate = page.locator('[name="environment.precipitationRateMmPerH"]');
  await rate.fill("6");
  await rate.press("Tab");
  await page.locator("#studio-save").click();
  await page.waitForFunction(() => document.querySelector("#studio-save-status")?.dataset.state === "saved",
    undefined, { timeout: 30000, polling: 250 });
  const edited = await draftEnvironment();
  check("panel edits plus save produce a v2 draft with night rain",
    edited?.timeOfDay === "night" && edited?.precipitation === "rain" && edited?.precipitationRateMmPerH === 6,
    edited);
  check("map render bar keeps reporting the workspace draft",
    await barNote.getAttribute("data-mode") === "workspace",
    await barNote.textContent());

  // Reload: the studio must restore the persisted v2 draft on the render scene.
  await page.reload({ waitUntil: "commit", timeout: 120000 });
  await page.waitForFunction(() => {
    const data = document.querySelector("#studio-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, undefined, { timeout: 120000 });
  await page.waitForFunction(() => window.__studioMap !== undefined
    && window.__studioMap.renderStreamer !== null
    && window.__studioMap.renderStreamer.progress.active === 0, undefined, { timeout: 120000 });
  const restored = await draftEnvironment();
  check("v2 draft survives reload in localStorage",
    restored?.timeOfDay === "night" && restored?.precipitation === "rain" && restored?.precipitationRateMmPerH === 6,
    restored);
  const restoredMap = await mapState();
  check("restored draft drives the scene time of day",
    await page.evaluate(() => window.__studioMap.cityTimeOfDay) === "night"
    && await page.locator("#studio-map").getAttribute("data-city-time-of-day") === "night",
    restoredMap);
  check("restored rain reaches the scene weather",
    restoredMap.precipitation === "rain", restoredMap);
  check("runtime panel controls show the restored values",
    await page.locator('[name="environment.timeOfDay"]').inputValue() === "night"
    && await precipitation.inputValue() === "rain",
    { timeOfDay: await page.locator('[name="environment.timeOfDay"]').inputValue(),
      precipitation: await precipitation.inputValue() });
  await page.evaluate(() => {
    const map = window.__studioMap;
    map.camera.position.set(-240, 90, 120);
    map.controls.target.set(-160, 20, -120);
    map.controls.update();
    map.renderStreamer.update(map.camera);
    map.renderStaticFrame();
  });
  await page.screenshot({ path: resolve(output, "after-reload.png") });
  report.frames.push({
    file: "after-reload.png",
    sha256: createHash("sha256").update(await readFile(resolve(output, "after-reload.png"))).digest("hex"),
    ...await mapState(),
  });

  // Export the draft and re-import the same file: the round trip must keep night rain.
  const download = page.waitForEvent("download");
  await page.locator("#studio-export").click();
  const file = await download;
  const exportedPath = resolve(output, "exported-workspace.json");
  await file.saveAs(exportedPath);
  const exported = JSON.parse(await readFile(exportedPath, "utf8"));
  check("exported JSON is a v2 workspace with night rain",
    exported.schema_version === "aero-bench.city-workspace/v2"
    && exported.environment.timeOfDay === "night"
    && exported.environment.precipitation === "rain",
    { schema_version: exported.schema_version, environment: exported.environment });
  await page.locator('[name="environment.timeOfDay"]').selectOption("day");
  // Panel edits persist through the explicit save button (the render-bar path is
  // the one that auto-saves via onEnvironmentEdit). Wait for the apply to settle,
  // save, and confirm the draft flips to day in storage.
  await page.waitForFunction(() => !document.querySelector("#studio-save")?.disabled,
    undefined, { timeout: 30000, polling: 250 });
  await page.locator("#studio-save").click();
  await page.waitForFunction(() => {
    const raw = window.localStorage.getItem("aero-bench.city-workspace.v2");
    return raw !== null && JSON.parse(raw).environment.timeOfDay === "day";
  }, undefined, { timeout: 30000, polling: 250 });
  check("day edit plus explicit save lands in the draft before re-import",
    (await draftEnvironment())?.timeOfDay === "day", await draftEnvironment());
  await page.locator("#studio-import").setInputFiles(exportedPath);
  await page.waitForFunction(expected => {
    const raw = window.localStorage.getItem("aero-bench.city-workspace.v2");
    return raw !== null && JSON.parse(raw).environment.timeOfDay === expected;
  }, "night", undefined, { timeout: 30000 });
  const roundTripped = await draftEnvironment();
  check("re-import restores the exported night rain draft",
    roundTripped?.timeOfDay === "night" && roundTripped?.precipitation === "rain"
    && roundTripped?.precipitationRateMmPerH === 6,
    roundTripped);
  check("re-import keeps the render scene applied",
    (await mapState()).workspaceApplied, await mapState());
  check("no page or console errors", report.errors.length === 0, report.errors);
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL";
  report.failure = String(error);
  report.sceneStatus = await page.evaluate(() => ({
    mapPublished: typeof window.__studioMap !== "undefined",
    data: { ...document.querySelector("#studio-map")?.dataset },
    studio: document.querySelector("#city-studio")?.textContent.slice(0, 1600),
  }));
  await page.screenshot({ path: resolve(output, "failure.png") });
  throw error;
} finally {
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  await browser.close();
}
console.log(JSON.stringify({ output, status: report.status, checks: report.checks.length, errors: report.errors.length }));
