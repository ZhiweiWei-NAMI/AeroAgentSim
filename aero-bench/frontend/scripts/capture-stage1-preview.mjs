/** Stage 1 preview capture: fixed cameras x moods against the Vite dev server.
 * Usage: node scripts/capture-stage1-preview.mjs <origin> <outputDir> [mood,mood...] [query]
 * Hooks only the dev-served app module to expose the map for camera control. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = process.argv[2] ?? 'http://127.0.0.1:5310';
const output = resolve(process.argv[3] ?? '.stage1/captures/current');
const moods = (process.argv[4] ?? 'day,twilight,night').split(',');
const query = process.argv[5] ?? 'scene=1';
await mkdir(output, { recursive: true });

const CAMERAS = [
  { name: 'aerial', eye: [150, 260, 280], target: [-100, 15, -70] },
  { name: 'rooftops', eye: [-125, 100, 70], target: [50, 36, -140] },
  { name: 'neighborhood', eye: [-260, 46, 12], target: [-330, 13, -65] },
  { name: 'street' },
];

/** Deterministic eye-level camera: a street-width gap near the dense centre, facing the tallest nearby mass. */
function streetCamera(map) {
  const boxes = map.previewBuildingObstacles;
  if (!boxes?.length) throw new Error('Street camera needs building envelopes');
  const center = map.previewCityCenter;
  let best = null;
  for (let dx = -300; dx <= 300; dx += 6) for (let dz = -300; dz <= 300; dz += 6) {
    const x = center.x + dx, z = center.z + dz;
    let clearance = Infinity, nearby = 0, height = 0, cx = 0, cz = 0;
    for (const box of boxes) {
      const ex = Math.max(box.min.x - x, 0, x - box.max.x), ez = Math.max(box.min.z - z, 0, z - box.max.z);
      const distance = Math.hypot(ex, ez);
      clearance = Math.min(clearance, distance);
      if (distance < 120) { const h = box.max.y; nearby += h; height = Math.max(height, h);
        cx += h * (box.min.x + box.max.x) / 2; cz += h * (box.min.z + box.max.z) / 2; }
    }
    if (clearance < 5 || clearance > 11 || nearby === 0) continue;
    if (best === null || nearby > best.nearby) best = { x, z, nearby };
  }
  if (best === null) throw new Error('No street-width gap found for the street camera');
  // Look along the street: the horizontal direction with the longest free run from the eye.
  const freeRun = angle => {
    const dx = Math.cos(angle), dz = Math.sin(angle);
    for (let step = 2; step <= 400; step += 2) {
      const x = best.x + dx * step, z = best.z + dz * step;
      if (boxes.some(box => x >= box.min.x && x <= box.max.x && z >= box.min.z && z <= box.max.z)) return step;
    }
    return 400;
  };
  let angle = 0, run = -1;
  for (let index = 0; index < 72; index++) {
    const candidate = index * Math.PI / 36, length = freeRun(candidate);
    if (length > run) { run = length; angle = candidate; }
  }
  return { eye: [best.x, 1.7, best.z], target: [best.x + Math.cos(angle) * 120, 9, best.z + Math.sin(angle) * 120], freeRun: run };
}

async function settle(page) {
  await page.waitForFunction(() => {
    const progress = window.__aeroVisualMap.renderStreamer?.progress;
    return progress === undefined || progress.active === 0;
  }, undefined, { timeout: 120000 });
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
}

const browser = await chromium.launch({ headless: true, args: [
  '--enable-gpu', '--use-angle=vulkan', '--enable-features=Vulkan',
  '--disable-vulkan-surface', '--disable-software-rasterizer', '--ignore-gpu-blocklist',
] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
await page.route(/\/src\/app\.ts(\?.*)?$/, async route => {
  const response = await route.fetch();
  const source = await response.text();
  const pattern = /this\.map = new PublicTraceMap\(/;
  if (!pattern.test(source)) throw new Error('Capture map hook no longer matches src/app.ts');
  await route.fulfill({ response, body: source.replace(pattern, 'window.__aeroVisualMap = this.map = new PublicTraceMap(') });
});
const report = { origin, query, moods, frames: [], errors };
try {
  const start = performance.now();
  await page.goto(`${origin}/?${query}`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const data = document.querySelector('#city-map')?.dataset;
    return (data?.sceneReady === 'true' && data?.skyReady === 'true') || data?.sceneError === 'true';
  }, undefined, { timeout: 300000 });
  report.loadMs = Math.round(performance.now() - start);
  const sceneError = await page.locator('#city-map').getAttribute('data-scene-error-message');
  if (sceneError) {
    report.sceneError = sceneError;
    await page.screenshot({ path: resolve(output, 'scene-error.png') });
    throw new Error(`City scene failed: ${sceneError}`);
  }
  await page.evaluate(source => { window.__aeroStreetCamera = new Function(`return (${source})`)()(window.__aeroVisualMap); },
    streetCamera.toString());
  report.streetCamera = await page.evaluate(() => window.__aeroStreetCamera);
  for (const mood of moods) for (const view of CAMERAS) {
    const stats = await page.evaluate(({ mood, view }) => {
      const map = window.__aeroVisualMap;
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      map.previewFollowId = null;
      map.previewSeconds = 34;
      map.setCameraMode('free');
      const pose = view.name === 'street' ? window.__aeroStreetCamera : view;
      map.camera.position.set(...pose.eye);
      map.controls.target.set(...pose.target);
      map.controls.update();
      map.renderStreamer?.update(map.camera);
      map.configureCityPresentation({ mood: mood === 'day' ? 'day' : 'dusk', timeOfDay: mood });
      if (map.trafficPreview !== null) map.renderPreviewFrame(); else map.renderStaticFrame();
      const gl = map.renderer.getContext(), debug = gl.getExtension('WEBGL_debug_renderer_info');
      return { calls: map.renderer.info.render.calls, triangles: map.renderer.info.render.triangles,
        programs: map.renderer.info.programs.length, textures: map.renderer.info.memory.textures,
        geometries: map.renderer.info.memory.geometries, exposure: map.renderer.toneMappingExposure,
        eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        buildingCount: map.buildingPresentation?.userData.buildingCount,
        renderer: String(gl.getParameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl.RENDERER)),
        canvas: [map.renderer.domElement.width, map.renderer.domElement.height] };
    }, { mood, view });
    await settle(page);
    await page.evaluate(() => { const map = window.__aeroVisualMap;
      if (map.trafficPreview !== null) map.renderPreviewFrame(); else map.renderStaticFrame(); });
    await settle(page);
    stats.progress = await page.evaluate(() => window.__aeroVisualMap.renderStreamer?.progress
      && (({ total, loaded, active, failed }) => ({ total, loaded, active, failed }))(window.__aeroVisualMap.renderStreamer.progress));
    stats.timeOfDay = await page.evaluate(() => document.querySelector('#city-map').dataset.cityTimeOfDay);
    const file = `${mood}-${view.name}.png`;
    await page.locator('#city-map canvas').first().screenshot({ path: resolve(output, file) });
    report.frames.push({ file, mood, view: view.name, ...stats });
  }
} finally {
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
console.log(JSON.stringify({ output, loadMs: report.loadMs, frames: report.frames.length,
  errors: errors.length, sceneError: report.sceneError }));
