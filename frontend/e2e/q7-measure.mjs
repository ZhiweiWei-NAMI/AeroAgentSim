/** Built-viewer benchmark. See docs/platform/viewer-evaluation.md for provenance. */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { resolve, join } from 'node:path';
import { syntheticFeed } from './q7-fixtures.mjs';
const require = createRequire(new URL('../package.json', import.meta.url));
const { chromium, expect: playwrightExpect } = require('@playwright/test');
const expect = playwrightExpect.configure({ timeout: 120000 });
const base = process.env.Q7_VIEWER_URL ?? 'http://127.0.0.1:18770';
const benchBase = process.env.Q7_BENCH_URL ?? 'http://127.0.0.1:18771';
const out = resolve(process.env.Q7_OUT ?? '/tmp/aas-q/q7');
mkdirSync(out + '/browser-tmp', { recursive: true }); mkdirSync(out + '/config', { recursive: true });
process.env.XDG_CONFIG_HOME = out + '/config'; process.chdir(out); process.env.TMPDIR = '/proc/self/cwd/browser-tmp';
process.env.NO_PROXY = process.env.no_proxy = '127.0.0.1,localhost';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? join(homedir(), '.cache/ms-playwright/chromium-1234/chrome-linux64/chrome');
const mode = process.env.Q7_GL ?? 'swiftshader';
if (!['hardware', 'swiftshader'].includes(mode)) throw Error('Unsupported Q7_GL: ' + mode);
const angle = process.env.Q7_ANGLE ?? 'vulkan';
if (!['gl-egl', 'vulkan'].includes(angle)) throw Error('Unsupported Q7_ANGLE: ' + angle);
const cases = process.env.Q7_CASES?.split(',');
const supportedCases = ['p1-slice-city', 'p1-scale-city-100', 'synthetic-1000', 'topology-2000-5000', 'bench'];
if (cases?.some(label => !supportedCases.includes(label))) throw Error('Unsupported Q7_CASES: ' + cases);
const resultPath = out + `/measurements-${mode}${cases ? '-' + cases.join('-') : ''}.json`;
const flags = mode === 'hardware'
  ? ['--use-gl=angle', '--use-angle=' + angle, ...(angle === 'vulkan' ? ['--enable-features=Vulkan', '--disable-vulkan-surface'] : [])]
  : ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
const results = { mode, flags, startedAt: new Date().toISOString(), viewport: { width: 1440, height: 900 }, dpr: 1, records: [] };
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox', ...flags] });
results.chromium = browser.version();
const page = await browser.newPage({ viewport: results.viewport, deviceScaleFactor: 1 });
const browserCdp = await browser.newBrowserCDPSession();
page.setDefaultTimeout(120000);
let errors = [];
const consoleMessages = new Map();
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {
  if (!['warning', 'error'].includes(message.type())) return;
  const key = message.type() + ': ' + message.text();
  const entry = consoleMessages.get(key);
  if (entry) entry.count++;
  else consoleMessages.set(key, { type: message.type(), text: message.text(), count: 1 });
});
const percentile = (values, fraction) => {
  if (!values.length) throw Error('No measurement samples');
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(sorted.length * fraction) - 1];
};
function verifyRenderer(gpu) {
  if (!gpu) throw Error('Missing WebGL renderer');
  if (mode === 'hardware' && (/swiftshader|llvmpipe|softpipe|software|lavapipe|swrast/i.test(gpu) || !/nvidia|geforce|rtx/i.test(gpu))) {
    throw Error('Hardware measurement rejected: renderer is ' + gpu);
  }
  if (mode === 'swiftshader' && !/swiftshader/i.test(gpu)) throw Error('SwiftShader measurement rejected: renderer is ' + gpu);
}
async function measure(label, selector, extra = {}, exercise = false) {
  if (selector === '#city-map' || selector === '[data-testid="viewport"]') {
    const gpu = await page.locator(selector).getAttribute('data-gpu');
    verifyRenderer(gpu);
  }
  await page.waitForTimeout(2000);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  let watchdog;
  const data = await Promise.race([page.evaluate(async ({ selector, exercise }) => {
    const frames = [], diagnostics = [], target = document.querySelector(selector);
    if (!target) throw Error(`Missing measurement target: ${selector}`);
    let last, start;
    await new Promise(resolve => {
      function tick(now) {
        if (start === undefined) { start = now; last = now; }
        else { frames.push(now - last); last = now; diagnostics.push({ ...target.dataset }); }
        if (exercise) {
          const canvas = target.matches('canvas') ? target : target.querySelector('canvas');
          if (canvas) canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: frames.length % 20 < 10 ? 16 : -16, clientX: 450, clientY: 250, bubbles: true, cancelable: true }));
        }
        if (now - start < 6000) requestAnimationFrame(tick); else resolve();
      }
      requestAnimationFrame(tick);
    });
    return { frames, diagnostics };
  }, { selector, exercise }), new Promise((_, reject) => {
    watchdog = setTimeout(() => reject(Error('Frame sampling exceeded 60 seconds: ' + label)), 60000);
  })]).finally(() => clearTimeout(watchdog));
  const performanceMetrics = await cdp.send('Performance.getMetrics');
  const heap = performanceMetrics.metrics.find(m => m.name === 'JSHeapUsedSize');
  const root = await page.locator(selector).evaluate(node => ({ ...node.dataset }));
  if (selector === '#city-map' || selector === '[data-testid="viewport"]') verifyRenderer(root.gpu);
  const calls = data.diagnostics.filter(d => d.drawCalls !== undefined).map(d => Number(d.drawCalls));
  const triangles = data.diagnostics.filter(d => d.triangles !== undefined).map(d => Number(d.triangles));
  const render = data.diagnostics.filter(d => d.renderMs !== undefined).map(d => Number(d.renderMs));
  if (selector === '#city-map' || selector === '[data-testid="viewport"]') {
    if (!calls.length || !triangles.length || !render.length) throw Error('Missing spatial renderer counters: ' + label);
  }
  if ([...data.frames, ...calls, ...triangles, ...render].some(value => !Number.isFinite(value))) throw Error('Invalid measurement counter: ' + label);
  const screenshot = `${out}/${label}-${mode}.png`;
  await page.screenshot({ path: screenshot, timeout: 90000 });
  const gpuProcesses = await browserCdp.send('SystemInfo.getProcessInfo');
  // Shared device totals and the driver process table are raw observations, not
  // browser VRAM estimates. Correlate graphics PIDs with gpuProcesses offline.
  const nvidiaSnapshot = mode === 'hardware' ? execFileSync('nvidia-smi', [], { encoding: 'utf8', timeout: 10000 }) : null;
  const record = { label, ...extra, measuredAt: new Date().toISOString(), frames: data.frames.length, frameMs: { p50: percentile(data.frames, .5), p95: percentile(data.frames, .95), p99: percentile(data.frames, .99) },
    drawCalls: calls.length ? { p50: percentile(calls, .5), max: Math.max(...calls) } : null,
    triangles: triangles.length ? { p50: percentile(triangles, .5), max: Math.max(...triangles) } : null,
    renderCpuMs: render.length ? { p50: percentile(render, .5), p95: percentile(render, .95), p99: percentile(render, .99) } : null,
    jsHeapBytes: heap?.value ?? null, gpuProcesses, nvidiaSnapshot, diagnostics: root, screenshot, errors, consoleMessages: [...consoleMessages.values()] };
  results.records.push(record); results.rawSamples ??= {}; results.rawSamples[label] = data;
  writeFileSync(resultPath, JSON.stringify(results, null, 2) + '\n');
  const { nvidiaSnapshot: _snapshot, gpuProcesses: _processes, ...summary } = record;
  console.log(JSON.stringify(summary)); errors = []; consoleMessages.clear(); await cdp.detach();
}
const feeds = [
  ['p1-slice-city', () => JSON.parse(readFileSync(out + '/slice-feed.json', 'utf8')), true],
  ['p1-scale-city-100', () => JSON.parse(readFileSync(out + '/scale100-feed.json', 'utf8')), true],
  ['synthetic-1000', () => syntheticFeed(1000), false],
  ['topology-2000-5000', () => syntheticFeed(2000, true), false],
];
try {
  results.browserGpuInfo = await browserCdp.send('SystemInfo.getInfo');
  results.probeRenderer = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) throw Error('WebGL2 probe context unavailable');
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return renderer;
  });
  verifyRenderer(results.probeRenderer);
  for (const [label, loadFeed, city] of feeds) {
    if (cases && !cases.includes(label)) continue;
    const feed = loadFeed();
    console.log('Loading:', label);
    await page.route('**/v1/runs**', route => {
      const url = new URL(route.request().url());
      let body;
      if (url.pathname.endsWith('/header')) body = feed.header;
      else if (url.pathname.endsWith('/commits')) {
        const from = Number(url.searchParams.get('from')), limit = Number(url.searchParams.get('limit'));
        const commits = feed.commits.filter(c => c.commitIndex >= from).slice(0, limit);
        body = { commits, next: commits.length ? commits.at(-1).commitIndex + 1 : from, status: 'completed', finalCursor: feed.commits.at(-1).commitIndex + 1 };
      } else body = [{ id: feed.header.runId, scenario: label, status: 'completed', until_ns: feed.header.end.ns }];
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(`${base}/runs/${encodeURIComponent(feed.header.runId)}?mode=replay${city ? '&scene=huangpu' : ''}`, { waitUntil: 'domcontentloaded' });
    console.log('Page opened:', label);
    if (feed.header.presentation.length) {
      const viewport = page.getByTestId('viewport');
      await expect(viewport).toHaveAttribute('data-rendered', 'true');
      if (city) { await expect(viewport).toHaveAttribute('data-city', 'loaded'); await expect(viewport).toHaveAttribute('data-ibl', 'hdri'); }
      await page.getByLabel('View quality').selectOption('med');
      if (city) await expect.poll(() => viewport.getAttribute('data-models')).toBe('loaded');
      console.log('Rendering ready:', label);
      await measure(label, '[data-testid="viewport"]', { scene: city ? 'Huangpu east' : 'base', committedCut: 'first', quality: 'med' });
      if (label === 'p1-scale-city-100') {
        // Match the actual render surface and FOV, not just the outer page.
        await page.addStyleTag({ content: '.viewer-header,.viewer-timeline,.viewer-sidebar,.presentation-tabs,.viewport-caption,.director-toolbar,.mission-overlay,.scene-status,.scene-attribution{display:none!important}.viewer-presentation{inset:0!important}.viewer-demo{height:900px!important;min-height:0!important}.viewer-stage{height:900px!important}' });
        await measure('matched-aas-100', '[data-testid="viewport"]', { city: 'Huangpu east', entityCount: 100, quality: 'med' });
      }
    } else {
      await expect(page.getByTestId('viewer-presentation')).toHaveClass(/inspector-first/);
      await expect(page.getByTestId('topology-canvas')).toHaveAttribute('data-node-count', '2000');
      await expect(page.getByTestId('topology-canvas')).toHaveAttribute('data-edge-count', '5000');
      await measure(label, '[data-testid="topology-canvas"]', { entityCount: 2000, activeEdges: 5000, interaction: 'wheel zoom event every animation frame' }, true);
      await page.getByLabel('Search graph entities', { exact: true }).fill('fixture-0001');
      await page.getByLabel('Graph entity', { exact: true }).selectOption('fixture-0001:0');
      await expect(page.getByTestId('inspector')).toContainText('fixture-0001');
      await expect(page.locator('.mission-timeline summary')).toContainText('fixture-0001');
      await page.locator('.viewer-timeline [role="slider"]').focus();
      await page.keyboard.press('End');
      await expect(page.getByTestId('topology-canvas')).toHaveAttribute('data-edge-count', '4900');
      await page.keyboard.press('Home');
      await expect(page.getByTestId('topology-canvas')).toHaveAttribute('data-edge-count', '5000');
      await page.getByRole('button', { name: 'Queue / state', exact: true }).click();
      await expect(page.getByLabel('State type')).toHaveValue('load:Type1');
      const row = page.locator('tbody tr[aria-selected="true"]');
      await expect(row).toContainText('fixture-0001');
      await expect(row.locator('td code')).toHaveText(['"running"', '1']);
      await page.locator('.viewer-timeline [role="slider"]').focus();
      await page.keyboard.press('End');
      await expect(row.locator('td code')).toHaveText(['"done"', '41']);
      await page.keyboard.press('Home');
      await expect(row.locator('td code')).toHaveText(['"running"', '1']);
      results.browserChecks = { graphSelectionInspectorTimeline: true, edgeCloseAndRewind: true, stateSelectionAndScrub: true };
      await measure('state-2000', '.nonspatial-pane', { entityCount: 2000, interaction: 'paused after End/Home state verification' });
    }
    await page.unroute('**/v1/runs**');
  }
  if ((!cases || cases.includes('bench')) && process.env.Q7_SKIP_BENCH !== '1') {
    if (!existsSync(out + '/aerobench-copy/dist/index.html')) throw Error('AeroBench scratch production build is missing');
    await page.goto(`${benchBase}/?view=replay&scene=1&perf=1`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#city-map')).toHaveAttribute('data-scene-ready', 'true');
    console.log('AeroBench city ready');
    // The scratch-only hook exposes native renderer/camera; no alternate renderer.
    await page.addStyleTag({ content: '#city-map{position:fixed!important;inset:0!important;width:1440px!important;height:900px!important}.scene-navigation,.scene-view-toggle,.scene-library-link,.scene-language-switch,.city-perf-overlay,.operations-monitor{display:none!important}' });
    await page.evaluate(async () => {
      if (!window.__q7Bench) throw Error('Scratch AeroBench hook is missing');
      await window.__q7Bench.match();
    });
    console.log('AeroBench matched camera ready');
    await measure('matched-aerobench-native', '#city-map', { city: 'Huangpu east', camera: 'matched via scratch-only hook', scene: 'bundled SUMO traffic plus planned visual flight at 80.75s, 94 active entities' });
  }
} catch (error) {
  results.failure = String(error);
  try {
    results.failureDiagnostics = await page.locator('#city-map, [data-testid="viewport"], .nonspatial-pane').evaluateAll(nodes => nodes.map(node => ({ ...node.dataset })));
    await page.screenshot({ path: out + '/measurement-failure.png', timeout: 90000 });
  } catch (diagnosticError) { results.diagnosticFailure = String(diagnosticError); }
  throw error;
} finally {
  results.finishedAt = new Date().toISOString();
  writeFileSync(resultPath, JSON.stringify(results, null, 2) + '\n');
  await browser.close();
}
