/** GPU browser acceptance for unsaved weather controls on the building-only preview.
 * Usage: node scripts/capture-stage1-weather.mjs <origin> <output-directory> */
import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5310";
const output = resolve(process.argv[3] ?? "../validation/stage1-continuation-20260930/weather");
await mkdir(output, { recursive: true });
const report = { origin, scope: "unsaved visual weather; no formal weather or traffic execution", checks: [], errors: [], frames: [] };
const browser = await chromium.launch({ headless: true, args: ["--enable-gpu", "--use-angle=vulkan",
  "--enable-features=Vulkan", "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.on("pageerror", error => report.errors.push(error.message));
page.on("console", message => { if (message.type() === "error") report.errors.push(message.text()); });
page.on("response", response => {
  if (response.status() >= 400) report.errors.push(`${response.status()} ${new URL(response.url()).pathname}`);
});
await page.route(/\/src\/app\.ts(\?.*)?$/, async route => {
  const response = await route.fetch(), source = await response.text();
  const pattern = /this\.map = new PublicTraceMap\(/;
  if (!pattern.test(source)) throw new Error("Weather capture hook no longer matches app.ts");
  await route.fulfill({ response, body: source.replace(pattern, "window.__weatherMap = this.map = new PublicTraceMap(") });
});
async function state() {
  return page.evaluate(() => {
    const map = window.__weatherMap, weather = map.weather;
    const particles = map.scene.getObjectByName("city-weather-precipitation");
    const clouds = map.scene.getObjectByName("city-weather-clouds");
    return { settings: weather.provenance, requiresAnimation: weather.requiresAnimation,
      time: particles.material.uniforms.uTime.value, wind: particles.material.uniforms.uWind.value.toArray(),
      particlesVisible: particles.visible, cloudsVisible: clouds.visible,
      fog: { near: map.scene.fog.near, far: map.scene.fog.far },
      exposure: map.renderer.toneMappingExposure, sunIntensity: map.sun.intensity, trafficAbsent: map.trafficPreview === null,
      eye: map.camera.position.toArray(), target: map.controls.target.toArray() };
  });
}
function check(name, passed, details) {
  report.checks.push({ name, passed, details });
  if (!passed) throw new Error(`Weather acceptance failed: ${name}`);
}
try {
  await page.goto(`${origin}/?scene=1&city=/city-presentation/default-scene-v1.json`, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, undefined, { timeout: 120000 });
  const error = await page.locator("#city-map").getAttribute("data-scene-error-message");
  if (error) throw new Error(`Scene failed: ${error}`);
  await page.waitForFunction(() => window.__weatherMap.renderStreamer.progress.active === 0, undefined, { timeout: 120000 });
  report.renderer = await page.evaluate(() => {
    const gl = window.__weatherMap.renderer.getContext(), debug = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
  await page.evaluate(() => {
    const map = window.__weatherMap;
    map.camera.position.set(-260, 46, 12); map.controls.target.set(-330, 13, -65); map.controls.update();
    map.renderStreamer.update(map.camera); map.renderStaticFrame();
  });
  await page.waitForFunction(() => window.__weatherMap.renderStreamer.progress.active === 0);
  const preset = page.locator('[name="render-weather-preset"]');
  check("weather controls visible on building-only scene", await preset.isVisible(), await state());
  await preset.selectOption("rain");
  const started = await state();
  await page.waitForFunction(time => window.__weatherMap.weather.provenance.settings.precipitation === "rain"
    && window.__weatherMap.scene.getObjectByName("city-weather-precipitation").material.uniforms.uTime.value > time + 1,
  started.time, { timeout: 30000 });
  const advanced = await state();
  check("rain animates without traffic", advanced.trafficAbsent && advanced.particlesVisible && advanced.time > started.time + 1, advanced);
  await page.locator('[name="render-wind-speed"]').fill("2.5");
  await page.locator('[name="render-wind-speed"]').press("Tab");
  await page.locator('[name="render-wind-direction"]').fill("180");
  await page.locator('[name="render-wind-direction"]').press("Tab");
  const wind = await state();
  check("wind edits reach GPU uniforms", Math.abs(wind.wind[0]) < 1e-8 && Math.abs(wind.wind[1] - 2.5) < 1e-8, wind);
  await page.getByRole("button", { name: "暂停天气", exact: true }).click();
  const paused = await state(); await page.waitForTimeout(600);
  check("pause freezes weather time", (await state()).time === paused.time, paused);
  for (const value of ["clear", "rain", "fog", "snow"]) {
    await preset.selectOption(value);
    await page.evaluate(() => {
      const map = window.__weatherMap; map.previewSeconds = 34; map.configureCityPresentation({ timeOfDay: "day" });
    });
    await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
    const file = `${value}-neighborhood.png`;
    await page.locator("#city-map canvas").first().screenshot({ path: resolve(output, file) });
    report.frames.push({ file, sha256: createHash("sha256").update(await readFile(resolve(output, file))).digest("hex"), ...await state() });
  }
  const clear = report.frames.find(frame => frame.file.startsWith("clear"));
  const rain = report.frames.find(frame => frame.file.startsWith("rain"));
  check("rain attenuates sunlight and visibility", rain.sunIntensity < clear.sunIntensity && rain.fog.far < clear.fog.far, { clear, rain });
  await preset.selectOption("rain");
  await page.getByRole("button", { name: "播放天气", exact: true }).click();
  const resumed = await state();
  await page.waitForFunction(time => window.__weatherMap.scene.getObjectByName("city-weather-precipitation").material.uniforms.uTime.value > time + .5,
    resumed.time, { timeout: 30000 });
  check("resume restarts weather", true, await state());
  await preset.selectOption("clear");
  const stopped = await state(); await page.waitForTimeout(600);
  check("clear stops the unused animation loop", (await state()).time === stopped.time && !stopped.particlesVisible, stopped);
  check("no page or console errors", report.errors.length === 0, report.errors);
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL"; report.failure = String(error);
  report.sceneStatus = await page.evaluate(() => ({ mapPresent: Boolean(window.__weatherMap),
    data: { ...document.querySelector("#city-map")?.dataset }, startup: document.querySelector("#app")?.textContent.slice(0, 1600) }));
  await page.screenshot({ path: resolve(output, "failure.png") });
  throw error;
} finally {
  await writeFile(resolve(output, "report.json"), JSON.stringify(report, null, 2) + "\n");
  await browser.close();
}
console.log(JSON.stringify({ output, status: report.status, checks: report.checks.length, errors: report.errors.length }));
