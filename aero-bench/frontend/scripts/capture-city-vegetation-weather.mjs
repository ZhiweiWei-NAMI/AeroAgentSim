/** GPU browser acceptance for the render-scene vegetation layer: matched-camera captures
 * across weather and time of day, a measured wind-sway pixel difference, and frame cost
 * with the layer shown and hidden. Every value is read from the running viewer.
 * Usage: node scripts/capture-city-vegetation-weather.mjs <origin> <output-directory> */
import { chromium } from "playwright";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const origin = process.argv[2] ?? "http://127.0.0.1:5310";
const output = resolve(process.argv[3] ?? "../validation/frontend-opus-20260930/visual/browser");
const scene = "/city-presentation/default-scene-v1.json";
await mkdir(output, { recursive: true });
const report = { origin, scene, scope: "render-scene vegetation, weather and time-of-day matrix on the stated GPU",
  checks: [], errors: [], frames: [], performance: {} };
const check = (name, passed, details) => {
  report.checks.push({ name, passed, details });
  if (!passed) throw new Error(`Vegetation acceptance failed: ${name}`);
};

const browser = await chromium.launch({ headless: true, timeout: 60000, args: ["--enable-gpu", "--use-angle=vulkan",
  "--enable-features=Vulkan", "--disable-vulkan-surface", "--disable-software-rasterizer", "--ignore-gpu-blocklist"] });
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  page.on("pageerror", error => report.errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") report.errors.push(message.text()); });
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch(), source = await response.text();
    const token = "this.map = new PublicTraceMap(";
    if (source.split(token).length !== 2) throw new Error("Viewer map capture hook no longer matches app.ts");
    await route.fulfill({ response, body: source.replace(token, "window.__map = this.map = new PublicTraceMap(") });
  });
  const started = Date.now();
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scene)}`, { waitUntil: "domcontentloaded", timeout: 180000 });
  await page.waitForFunction(() => {
    const data = document.querySelector("#city-map")?.dataset;
    return data?.sceneReady === "true" || data?.sceneError === "true";
  }, undefined, { timeout: 180000, polling: 500 });
  const ready = await page.locator("#city-map").evaluate(root => ({ ...root.dataset }));
  check("render scene loads without error", ready.sceneError !== "true", ready.sceneErrorMessage ?? null);
  report.sceneReadyMs = Date.now() - started;
  const renderer = await page.evaluate(() => {
    const gl = window.__map.renderer.getContext(), debug = gl.getExtension("WEBGL_debug_renderer_info");
    return String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER));
  });
  report.renderer = renderer;
  check("WebGL runs on the NVIDIA GPU", /NVIDIA/.test(renderer), renderer);
  const vegetation = JSON.parse(ready.cityVegetation ?? "null");
  check("vegetation layer is loaded from the verified environment source", vegetation !== null
    && vegetation.greens > 0 && vegetation.trees > 0 && vegetation.clumps > 0, vegetation);
  report.vegetation = vegetation;

  // Freeze the preview clock; captures set time explicitly.
  const cameras = await page.evaluate(() => {
    const map = window.__map;
    map.previewPlaying = false; cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
    map.controls.enableDamping = false;
    const plan = map.cityVegetationLayer.group.userData.environmentPlan;
    const largest = [...plan.grass].sort((a, b) => b.areaM2 - a.areaM2)[0];
    let sx = 0, sz = 0, n = 0;
    for (const triangle of largest.triangles) for (const [x, z] of triangle) { sx += x; sz += z; n++; }
    const cx = sx / n, cz = sz / n;
    return { green: largest.id, greenAreaM2: largest.areaM2,
      street: { position: [cx + 34, 7, cz + 38], target: [cx, 2.5, cz] },
      aerial: { position: [cx + 260, 210, cz + 300], target: [cx, 0, cz] } };
  });
  report.cameras = cameras;
  const setView = (camera, timeOfDay, weather, timeS) => page.evaluate(({ camera, timeOfDay, weather, timeS }) => {
    const map = window.__map;
    map.configureCityPresentation({ timeOfDay, weather });
    map.previewPlaying = false; cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
    map.camera.position.set(...camera.position); map.controls.target.set(...camera.target); map.controls.update();
    map.renderStreamer?.update(map.camera);
    map.previewSeconds = timeS;
    map.focusSunShadow(); map.renderStaticFrame();
    return map.renderStreamer?.progress.active ?? 0;
  }, { camera, timeOfDay, weather, timeS });
  const settle = async () => {
    await page.waitForFunction(() => (window.__map.renderStreamer?.progress.active ?? 0) === 0, undefined,
      { timeout: 120000, polling: 250 });
    await page.evaluate(() => { window.__map.renderStaticFrame(); });
  };
  const shoot = async (name, details) => {
    const path = resolve(output, `${name}.png`);
    await page.locator("#city-map canvas").first().screenshot({ path });
    const bytes = await readFile(path);
    report.frames.push({ file: `${name}.png`, sha256: createHash("sha256").update(bytes).digest("hex"), ...details });
    return bytes;
  };

  const presets = await page.evaluate(async () => (await import("/src/city-weather.ts")).CITY_WEATHER_PRESETS);
  const calm = { ...presets.clear, windMps: 0 };
  // Matched cameras: every weather/time pair uses the identical pose and preview time.
  for (const view of ["street", "aerial"]) {
    for (const timeOfDay of ["day", "twilight", "night"]) {
      for (const weather of ["clear", "rain", "fog", "snow"]) {
        if (view === "aerial" && timeOfDay !== "day" && weather !== "clear") continue;
        await setView(cameras[view], timeOfDay, presets[weather], 10);
        await settle();
        await shoot(`${view}-${timeOfDay}-${weather}`, { view, timeOfDay, weather, timeS: 10 });
      }
    }
  }

  // Wind sway: identical camera and weather, preview time advanced by 1.5 s. Clear sky and
  // no precipitation, so the only time-dependent content is the vegetation wind.
  const pixels = async (timeS, weather) => {
    await setView(cameras.street, "day", weather, timeS); await settle();
    return page.evaluate(() => {
      const map = window.__map, gl = map.renderer.getContext();
      map.renderStaticFrame();
      const width = gl.drawingBufferWidth, height = gl.drawingBufferHeight;
      const data = new Uint8Array(width * height * 4);
      gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, data);
      return { width, height, data: Array.from(data) };
    });
  };
  const changed = (a, b) => {
    let count = 0;
    for (let i = 0; i < a.data.length; i += 4) {
      if (Math.abs(a.data[i] - b.data[i]) + Math.abs(a.data[i + 1] - b.data[i + 1])
          + Math.abs(a.data[i + 2] - b.data[i + 2]) > 12) count++;
    }
    return count / (a.width * a.height);
  };
  const windy = { ...presets.clear, windMps: 8, windDirectionDeg: 70 };
  const calmDiff = changed(await pixels(20, calm), await pixels(21.5, calm));
  const windDiff = changed(await pixels(20, windy), await pixels(21.5, windy));
  report.windSway = { calmChangedFraction: calmDiff, windChangedFraction: windDiff, windMps: 8, deltaS: 1.5 };
  check("calm air leaves the frame unchanged over time", calmDiff < 0.0005, report.windSway);
  check("wind moves vegetation pixels over time", windDiff > 0.002, report.windSway);

  // Frame cost on the GPU: gl.finish() bounds each synchronous render, with the layer shown and hidden.
  for (const view of ["street", "aerial"]) {
    await setView(cameras[view], "day", windy, 30); await settle();
    report.performance[view] = await page.evaluate(() => {
      const map = window.__map, gl = map.renderer.getContext();
      const measure = () => {
        const samples = [];
        for (let i = 0; i < 20; i++) { map.renderStaticFrame(); gl.finish(); }
        for (let i = 0; i < 120; i++) {
          map.previewSeconds += 1 / 60;
          const t0 = performance.now(); map.renderStaticFrame(); gl.finish(); samples.push(performance.now() - t0);
        }
        samples.sort((a, b) => a - b);
        return { medianMs: samples[60], p95Ms: samples[113], drawCalls: map.renderer.info.render.calls,
          triangles: map.renderer.info.render.triangles };
      };
      const shown = measure();
      map.cityVegetationLayer.group.visible = false;
      const hidden = measure();
      map.cityVegetationLayer.group.visible = true;
      return { shown, hidden };
    });
  }
  check("no page or console errors", report.errors.length === 0, report.errors.slice(0, 10));
  report.status = "PASS";
} catch (error) {
  report.status = "FAIL";
  report.failure = String(error?.stack ?? error);
  throw error;
} finally {
  await writeFile(resolve(output, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  await browser.close();
  console.log(JSON.stringify({ output, status: report.status, checks: report.checks.length, errors: report.errors.length }));
}
