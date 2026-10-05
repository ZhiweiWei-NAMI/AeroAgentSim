/** Real viewer capture; calibration is applied through its modules, with no fabricated scene data. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = process.argv[2] ?? 'http://127.0.0.1:5246';
const output = resolve(process.argv[3] ?? '../evidence/weather-lighting-calibration');
const scene = process.argv[4] ?? '/city-presentation/building-render-scene-v1.json';
const mode = process.argv[5] ?? 'full';
await mkdir(resolve(output, 'screenshots'), { recursive: true });
const report = { origin, scene, source: 'real viewer, verified 414 runtime; dev-server measurement',
  page_errors: [], console_errors: [], request_failures: [], frames: [] };
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader'] });

try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await context.newPage();
  await page.route(/\/src\/app\.ts(?:\?.*)?$/, async route => {
    const response = await route.fetch(), source = await response.text();
    const token = 'this.map = new PublicTraceMap(';
    if (source.split(token).length !== 2) throw new Error('Viewer map capture hook no longer matches');
    await route.fulfill({ response, body: source.replace(token, 'window.__weatherMap = this.map = new PublicTraceMap(') });
  });
  page.on('pageerror', e => report.page_errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') report.console_errors.push(m.text()); });
  page.on('requestfailed', r => report.request_failures.push(`${r.url()} ${r.failure()?.errorText ?? ''}`));
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  const requestUrls = new Map(), transfers = [];
  const start = performance.now();
  cdp.on('Network.requestWillBeSent', p => { if (!p.request.url.startsWith('data:')) requestUrls.set(p.requestId, p.request.url); });
  cdp.on('Network.loadingFinished', p => {
    const url = requestUrls.get(p.requestId);
    if (url) transfers.push({ url, at_ms: performance.now() - start, encoded_bytes: p.encodedDataLength });
  });
  await page.goto(`${origin}/?scene=1&city=${encodeURIComponent(scene)}`, { waitUntil: 'domcontentloaded', timeout: 180000 });
  await page.waitForFunction(() => {
    const d = document.querySelector('#city-map')?.dataset;
    return d?.sceneReady === 'true' || d?.sceneError === 'true';
  }, undefined, { timeout: 180000 });
  report.scene_ready_ms = performance.now() - start;
  await page.waitForFunction(() => {
    const d = document.querySelector('#city-map')?.dataset;
    return d?.sceneError === 'true' || Number(d?.buildingRenderLoaded ?? 0) + Number(d?.buildingRenderFailed ?? 0) === 414;
  }, undefined, { timeout: 600000 });
  report.all_414_terminal_ms = performance.now() - start;
  report.terminal = await page.locator('#city-map').evaluate(root => ({ ...root.dataset }));
  if (report.terminal.sceneError === 'true' || report.terminal.buildingRenderLoaded !== '414'
      || report.terminal.buildingRenderFailed !== '0') throw new Error('414 buildings did not load without failure');

  report.hardware = await page.evaluate(async () => {
    const THREE = await import('/node_modules/.vite/deps/three.js');
    const map = window.__weatherMap;
    map.previewPlaying = false; map.previewFollowId = null;
    cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
    map.controls.enableDamping = false;
    map.controls.minPolarAngle = 0; map.controls.maxPolarAngle = Math.PI;
    // Equal full-size shadow policy for old and calibrated captures, even on software GL.
    map.renderer.shadowMap.enabled = true; map.renderer.shadowMap.needsUpdate = true;
    const gl = map.renderer.getContext(), debug = gl.getExtension('WEBGL_debug_renderer_info');
    window.__weatherTHREE = THREE;
    return { renderer: debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      vendor: debug ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      gpu_timer_query: Boolean(gl.getExtension('EXT_disjoint_timer_query_webgl2')),
      shadow_map_size: map.sun.shadow.mapSize.toArray() };
  });

  const allViews = [
    { name: 'street' },
    { name: 'aerial', eye: [150, 260, 280], target: [-100, 15, -70] },
    { name: 'glass-near', eye: [-323, 35, -399], target: [-378.59359719, 45, -452.969913749] },
    { name: 'masonry-near', eye: [155, 8, -335], target: [98.816396105, 20, -396.577060482] },
  ];
  const views = mode === 'sky-check' ? allViews.slice(0, 2) : allViews;

  async function frame(label, view, timeOfDay, preset, calibrated) {
    const result = await page.evaluate(async ({ label, view, timeOfDay, preset, calibrated }) => {
      const map = window.__weatherMap, THREE = window.__weatherTHREE;
      if (view.name === 'street') map.focusRenderStreet();
      else { map.camera.position.set(...view.eye); map.controls.target.set(...view.target); map.controls.update(); }
      map.previewSeconds = 34;
      const mood = timeOfDay === 'day' ? 'day' : 'dusk';
      map.setCityMood(mood);
      let calibration = null;
      if (calibrated) {
        const api = window.__weatherApi;
        const weather = api.cityVisualWeather(preset), sample = api.sampleCityLighting({ solar: api.cityVisualSolar(timeOfDay), weather });
        const environment = window.__calibratedEnvironment.textureFor(timeOfDay);
        api.applyCityLightingCalibration({ scene: map.scene, renderer: map.renderer, sun: map.sun,
          hemisphere: map.hemisphere, ambient: map.ambient }, sample, environment);
        api.focusCityCalibratedSun(map.sun, sample, window.__weatherEnvelope, map.controls.target, map.camera);
        map.scene.background = null;
        api.applyCitySkyCalibration(map.cityDaySky, sample);
        map.weather.configureEnvelope(window.__weatherEnvelope);
        map.weather.setWeatherInput(weather); map.weather.setLighting(sample);
        calibration = api.setCityBuildingCalibration(map.buildingPresentation, sample, environment);
        map.localReflections.configure(map.buildingPresentation, map.camera.position.clone().add(new THREE.Vector3(0, -5, 0)), true);
        map.localReflections.invalidate();
      } else {
        const { CITY_WEATHER_PRESETS } = await import('/src/city-weather.ts');
        map.setCityWeather(CITY_WEATHER_PRESETS[preset]); map.weather.setMood(mood);
      }
      map.weather.update(34); map.sun.shadow.needsUpdate = true;
      map.renderer.render(map.scene, map.camera); // Shadow map precedes the optional cube capture.
      map.localReflections.refresh(); map.renderer.render(map.scene, map.camera);
      const gl = map.renderer.getContext(); gl.finish();
      const costs = [], gpuCosts = [], ext = gl.getExtension('EXT_disjoint_timer_query_webgl2');
      const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
      for (let i = 0; i < 12; i++) {
        const query = ext ? gl.createQuery() : null;
        const start = performance.now();
        if (query) gl.beginQuery(ext.TIME_ELAPSED_EXT, query);
        map.weather.update(34); map.renderer.render(map.scene, map.camera);
        if (query) gl.endQuery(ext.TIME_ELAPSED_EXT);
        gl.finish(); costs.push(performance.now() - start);
        if (query) {
          for (let retry = 0; retry < 120 && !gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE); retry++) {
            await new Promise(resolve => requestAnimationFrame(resolve));
          }
          if (gl.getParameter(ext.GPU_DISJOINT_EXT)) throw new Error('GPU timer was disjoint');
          if (!gl.getQueryParameter(query, gl.QUERY_RESULT_AVAILABLE)) throw new Error('GPU timer did not complete');
          gpuCosts.push(gl.getQueryParameter(query, gl.QUERY_RESULT) / 1e6); gl.deleteQuery(query);
        }
      }
      const lights = {};
      map.scene.traverse(node => { if (node.isLight) lights[node.type] = (lights[node.type] ?? 0) + 1; });
      return { label, view: view.name, timeOfDay, preset, calibrated, calibration,
        eye: map.camera.position.toArray(), target: map.controls.target.toArray(),
        completion_frame_median_ms: median(costs), completion_frame_max_ms: Math.max(...costs),
        gpu_frame_median_ms: gpuCosts.length ? median(gpuCosts) : null,
        frame_samples: costs, draws: map.renderer.info.render.calls, triangles: map.renderer.info.render.triangles,
        lights, shadow_enabled: map.renderer.shadowMap.enabled, paused_time_seconds: map.previewSeconds,
        environment_intensity: map.scene.environmentIntensity, exposure: map.renderer.toneMappingExposure,
        fog_near: map.scene.fog.near,
        fog_far: map.scene.fog.far };
    }, { label, view, timeOfDay, preset, calibrated });
    await page.locator('#city-map canvas').screenshot({ path: resolve(output, 'screenshots', `${label}.png`) });
    report.frames.push(result);
    console.log(`${label}: ${result.completion_frame_median_ms.toFixed(1)}ms ${result.draws} draws`);
  }

  for (const time of ['day', 'night']) for (const view of views) {
    await frame(`baseline-${time}-clear-${view.name}`, view, time, 'clear', false);
  }
  const hdrStart = performance.now();
  report.hdr = await page.evaluate(async () => {
    const lighting = await import('/src/city-lighting-calibration.ts');
    const weather = await import('/src/city-weather-calibration.ts');
    const presentation = await import('/src/city-presentation.ts');
    window.__weatherApi = { ...lighting, ...weather, ...presentation };
    const map = window.__weatherMap, THREE = window.__weatherTHREE;
    const manifest = map.renderStreamer.manifest;
    const points = manifest.buildings.flatMap(entry => [
      new THREE.Vector3(entry.envelope.min_e, entry.envelope.base_up, -entry.envelope.max_n),
      new THREE.Vector3(entry.envelope.max_e, entry.envelope.top_up, -entry.envelope.min_n),
    ]);
    window.__weatherEnvelope = new THREE.Box3().setFromPoints(points);
    window.__calibratedEnvironment = new lighting.CityCalibratedEnvironment(map.renderer);
    const result = await window.__calibratedEnvironment.load();
    return { asset: lighting.CITY_CALIBRATION_HDR, calibration: result,
      envelope: { min: window.__weatherEnvelope.min.toArray(), max: window.__weatherEnvelope.max.toArray() } };
  });
  report.hdr_load_pmrem_ms = performance.now() - hdrStart;
  const conditions = mode === 'sky-check' ? [['day', 'cloudy'], ['twilight', 'clear'], ['night', 'clear']]
    : ['day', 'twilight', 'night'].flatMap(time => ['clear', 'cloudy', 'rain'].map(preset => [time, preset]));
  for (const [time, preset] of conditions) {
    for (const view of views) await frame(`calibrated-${time}-${preset}-${view.name}`, view, time, preset, true);
  }
  await frame('calibrated-day-fog-street', views[0], 'day', 'fog', true);
  report.network = { requests: transfers.length, encoded_bytes: transfers.reduce((n, t) => n + t.encoded_bytes, 0),
    until_scene_ready_bytes: transfers.filter(t => t.at_ms <= report.scene_ready_ms).reduce((n, t) => n + t.encoded_bytes, 0),
    hdr: transfers.filter(t => t.url.includes('/DaySkyHDRI041B.hdr')),
    building_render_bytes: transfers.filter(t => t.url.includes('/building-renders/')).reduce((n, t) => n + t.encoded_bytes, 0) };
  if (report.page_errors.length || report.console_errors.length || report.request_failures.length) {
    throw new Error('Capture encountered page, console or request failures; consult report');
  }
  report.status = 'CAPTURE_COMPLETE_VISUAL_REVIEW_REQUIRED';
  await context.close();
} catch (error) {
  report.status = 'FAILED'; report.error = String(error);
  throw error;
} finally {
  await browser.close();
  await writeFile(resolve(output, 'capture-report.json'), JSON.stringify(report, null, 2) + '\n');
}
