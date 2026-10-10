/** Focused FINISH capture for the 414-building render scene: loads, measures, screenshots. */
import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const origin = process.argv[2] ?? 'http://127.0.0.1:8137';
const output = resolve(process.argv[3] ?? '../validation/frontend-building-assets-mimo-20260930/evidence');
await mkdir(output, { recursive: true });
await mkdir(resolve(output, 'screenshots'), { recursive: true });

const SCENE_URL = `${origin}/?scene=1&city=%2Fcity-presentation%2Fbuilding-render-scene-v1.json`;
const browser = await chromium.launch({ headless: true });
const report = { origin, scene_url: SCENE_URL, console_errors: [], page_errors: [], request_failures: [] };

/** Patch the built app bundle to expose the map instance for deterministic camera control. */
async function newPage(context) {
  const page = await context.newPage();
  await page.route(/\/assets\/app-[^/]+\.js$/, async route => {
    const response = await route.fetch();
    const source = await response.text();
    const pattern = /this\.map=new [\w$]+\(this\.shell\.map\.querySelector\(`#city-map`\),\{/;
    if (!pattern.test(source)) throw new Error('map hook no longer matches the built bundle');
    await route.fulfill({ response, body: source.replace(pattern,
      match => match.replace('this.map=', 'window.__aeroFinishMap=this.map=')) });
  });
  page.on('pageerror', e => report.page_errors.push(e.message));
  page.on('console', m => { if (m.type() === 'error') report.console_errors.push(m.text()); });
  page.on('requestfailed', r => report.request_failures.push(`${r.url()} ${r.failure()?.errorText ?? ''}`));
  return page;
}

try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
  const page = await newPage(context);
  // Exact byte accounting via CDP: performance resource buffers cap at 250 entries.
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  const requestUrls = new Map();
  const loads = []; // { at_ms, url, encoded_bytes }
  cdp.on('Network.requestWillBeSent', params => {
    if (!params.request.url.startsWith('data:')) requestUrls.set(params.requestId, params.request.url);
  });
  cdp.on('Network.loadingFailed', params => {
    const url = requestUrls.get(params.requestId) ?? '';
    if (!url.startsWith('data:')) report.request_failures.push(`${url} ${params.errorText}`);
  });
  const startedAt = performance.now();
  cdp.on('Network.loadingFinished', params => {
    const url = requestUrls.get(params.requestId);
    if (url === undefined) return;
    loads.push({ at_ms: Math.round(performance.now() - startedAt), url,
                 encoded_bytes: params.encodedDataLength });
  });

  await page.goto(SCENE_URL, { waitUntil: 'domcontentloaded', timeout: 180000 });
  await page.waitForFunction(() => {
    const d = document.querySelector('#city-map')?.dataset;
    return d?.sceneReady === 'true' || d?.sceneError === 'true';
  }, undefined, { timeout: 180000 });
  report.t_scene_ready_ms = Math.round(performance.now() - startedAt);
  report.scene_ready = await page.locator('#city-map').evaluate(root => ({ ...root.dataset }));
  await page.screenshot({ path: resolve(output, 'screenshots', 'render-scene-ready.png') });

  // Wait for every render building to reach a terminal state via dataset counters.
  await page.waitForFunction(() => {
    const d = document.querySelector('#city-map')?.dataset;
    if (d?.sceneError === 'true') return true;
    const total = Number(d?.buildingRenderTotal ?? 0);
    const loaded = Number(d?.buildingRenderLoaded ?? 0);
    const failed = Number(d?.buildingRenderFailed ?? 0);
    return total > 0 && loaded + failed >= total;
  }, undefined, { timeout: 600000 });
  report.t_all_buildings_terminal_ms = Math.round(performance.now() - startedAt);
  report.scene_terminal = await page.locator('#city-map').evaluate(root => ({ ...root.dataset }));

  // Network split: bytes fetched before the first frame vs streamed afterwards (lazy loading).
  const readyMs = report.t_scene_ready_ms;
  const isRender = url => url.includes('/building-renders/');
  const sum = (filter, until = Number.POSITIVE_INFINITY) => loads
    .filter(entry => entry.at_ms <= until && filter(entry.url))
    .reduce((total, entry) => total + entry.encoded_bytes, 0);
  report.network = {
    requests_total: loads.length,
    bytes_total: sum(() => true),
    bytes_until_scene_ready: sum(() => true, readyMs),
    render_requests_total: loads.filter(entry => isRender(entry.url)).length,
    render_bytes_total: sum(isRender),
    render_bytes_until_scene_ready: sum(isRender, readyMs),
    render_last_request_at_ms: loads.filter(entry => isRender(entry.url))
      .reduce((latest, entry) => Math.max(latest, entry.at_ms), 0),
  };
  report.render_loaded_at_scene_ready = Number(report.scene_ready.buildingRenderLoaded ?? -1);

  for (const mood of ['day', 'dusk']) for (const view of ['street', 'aerial']) {
    await page.evaluate(({ mood, view }) => {
      const map = window.__aeroFinishMap;
      if (map === undefined) throw new Error('map hook missing; build bundle changed');
      map.previewPlaying = false;
      cancelAnimationFrame(map.previewAnimation); map.previewAnimation = 0;
      if (view === 'street') map.focusRenderStreet(); else map.frameExtent(map.packedScene.manifest.extent);
      map.setCityMood(mood);
      map.renderStaticFrame();
      map.renderStreamer?.update(map.camera);
    }, { mood, view });
    await page.waitForTimeout(700);
    const frame = await page.evaluate(() => {
      const d = document.querySelector('#city-map').dataset;
      return { frameRenderMs: d.frameRenderMs, drawCalls: d.rendererDrawCalls, triangles: d.rendererTriangles };
    });
    const name = `render-${view}-${mood}`;
    await page.screenshot({ path: resolve(output, 'screenshots', `${name}.png`) });
    report[name] = frame;
  }
  report.terminal_state = await page.locator('#city-map').evaluate(root => ({ ...root.dataset }));
  await context.close();
} finally {
  await browser.close();
  await writeFile(resolve(output, 'capture-report.json'), JSON.stringify(report, null, 2) + '\n');
}
if (report.page_errors.length || report.scene_ready?.sceneError === 'true') {
  console.error('CAPTURE FAILED');
  process.exit(2);
}
console.log('CAPTURE OK');
