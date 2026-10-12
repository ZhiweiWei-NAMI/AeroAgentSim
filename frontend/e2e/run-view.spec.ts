import { expect, test, type Page, type APIRequestContext, type BrowserContext } from '@playwright/test';
import { join } from 'node:path';
import { mkdirSync, writeFileSync } from 'node:fs';

const templates = ['parcel-delivery', 'medical-delivery', 'powerline-inspection', 'crop-spraying', 'air-taxi', 'security-patrol', 'traffic-accident', 'city-day', 'food-delivery'];
const shots = join(process.env.AEROAGENTSIM_E2E_ROOT ?? '/tmp/aas-sol/run-view/e2e', 'screenshots');
mkdirSync(shots, { recursive: true });
test.use({ launchOptions: { executablePath: process.env.AEROAGENTSIM_CHROMIUM, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'] } });
let context: BrowserContext, page: Page;
let network: { url: string; method: string; status?: number; error?: string }[] = [];
test.beforeAll(async ({ browser }) => { context = await browser.newContext({ baseURL: `http://127.0.0.1:${process.env.AEROAGENTSIM_E2E_PORT ?? '4179'}` }); page = await context.newPage(); });
test.afterAll(async () => { await context.close(); });
test.afterEach(async ({}, info) => {
  if (info.status !== info.expectedStatus) {
    const name = info.title.includes('polished workbench shell') ? info.title.split(':')[1].trim().split(' ')[0] + '-shell' : info.title.split(':')[0];
    await page.screenshot({ path: join(shots, `${name}-failure.png`) });
    writeFileSync(join(shots, `${name}-failure.json`), JSON.stringify({ url: page.url(), status: await status(page), network }, null, 2));
  }
});
async function navigate(url: string) {
  if (page.url() === 'about:blank') await page.goto(url);
  else await page.evaluate(path => { history.pushState({}, '', path); dispatchEvent(new PopStateEvent('popstate')); }, url);
}
async function initialize(request: APIRequestContext, template: string) {
  const post = async (path: string, data: unknown) => { const response = await request.post(path, { data, timeout: 120_000 }); expect(response.ok(), await response.text()).toBe(true); return response.json(); };
  const created = await post(`/v1/templates/${template}/workspace`, { knobs: {} }), workspace = created.workspace_id;
  const project = await (await request.get(`/v1/studio/workspaces/${workspace}`)).json();
  expect((await post(`/v1/studio/workspaces/${workspace}/validate`, { revision: project.revision })).valid).toBe(true);
  const version = await post(`/v1/studio/workspaces/${workspace}/versions`, { revision: project.revision });
  const run = await post('/v1/runs', { studio_workspace: workspace, version: version.version, autostart: false });
  return { workspace, run: run.id };
}
async function rendered(page: Page) {
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true', { timeout: 90_000 });
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-city', 'loaded', { timeout: 360_000 });
}
const status = (page: Page) => page.getByTestId('workbench').getAttribute('data-status');
async function noCoveredPanels(page: Page) {
  const boxes = await Promise.all(['wb-objects', 'wb-inspector', 'wb-camera', 'wb-dock'].map(id => page.getByTestId(id).boundingBox()));
  const [objects, inspector, camera, dock] = boxes;
  for (const panel of [objects, inspector, camera]) if (panel && dock) expect(panel.y + panel.height).toBeLessThanOrEqual(dock.y + 1);
  const viewport = page.viewportSize()!;
  for (const button of await page.getByTestId('wb-top').getByRole('button').all()) { const rect = await button.boundingBox(); if (rect) { expect(rect.x).toBeGreaterThanOrEqual(0); expect(rect.x + rect.width).toBeLessThanOrEqual(viewport.width); } }
  expect(await page.locator('.wb-clock small').evaluate(el => el.getBoundingClientRect().height)).toBeLessThan(15);
  expect(await page.getByTestId('wb-project-switch').locator('span').first().evaluate(el => el.scrollWidth > el.clientWidth)).toBe(false);
}

for (const template of templates) test(`${template}: real initialized/live/history/replay views at three sizes`, async ({ request }) => {
  test.setTimeout(template === 'city-day' ? 600_000 : 300_000);
  const errors: string[] = [];
  const expectedFaults: string[] = [];
  let recordsFault = false;
  network = [];
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('dialog'); page.removeAllListeners('response'); page.removeAllListeners('requestfailed');
  page.on('response', response => { if (response.url().includes('/v1/runs')) network.push({ url: response.url(), method: response.request().method(), status: response.status() }); });
  page.on('requestfailed', request => { if (request.url().includes('/v1/runs')) network.push({ url: request.url(), method: request.method(), error: request.failure()?.errorText }); });
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => {
    if (m.type() !== 'error' || /ERR_ABORTED|favicon/.test(m.text())) return;
    if (recordsFault && /ERR_FAILED/.test(m.text()) && m.location().url.includes('/v1/runs?include_kpis=false')) expectedFaults.push(m.text());
    else errors.push(m.text());
  });
  page.on('dialog', d => d.accept());
  const { workspace, run } = await initialize(request, template);
  const liveUrl = `/w/${workspace}?run=${run}&mode=live`;
  await page.setViewportSize({ width: 1600, height: 1000 });
  await navigate(liveUrl);
  await expect.poll(() => status(page), { timeout: 90_000 }).toBe('initialized'); await rendered(page);
  if (template === 'food-delivery') {
    recordsFault = true;
    await page.route('**/v1/runs?include_kpis=false', route => route.abort('failed'));
    await expect(page.getByRole('alert').filter({ hasText: 'Run records unavailable' })).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: join(shots, 'food-delivery-records-error.png') });
    await page.unroute('**/v1/runs?include_kpis=false');
    await expect(page.getByRole('alert').filter({ hasText: 'Run records unavailable' })).toHaveCount(0, { timeout: 15_000 });
    recordsFault = false;
    writeFileSync(join(shots, 'food-delivery-records-error.json'), JSON.stringify({ expectedFaults }, null, 2));
  }
  await expect(page.getByTestId('wb-speed')).toBeVisible();
  const changed = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/speed`) && response.request().method() === 'POST');
  await page.getByTestId('wb-speed').selectOption('0.5', { timeout: 15_000 });
  const receipt = await changed; expect(receipt.ok()).toBe(true); expect((await receipt.json()).factor).toBe(0.5);
  const readback = await page.evaluate(async path => { const response = await fetch(path); if (!response.ok) throw Error(await response.text()); return response.json(); }, `/v1/runs/${run}/speed`);
  expect(readback.factor).toBe(0.5);
  if (template === 'food-delivery') for (const value of ['1', '2', '5', '10', '50', '100', 'max', '0.5']) {
    const changed = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/speed`) && response.request().method() === 'POST');
    await page.getByTestId('wb-speed').selectOption(value); const receipt = await changed;
    expect(receipt.ok()).toBe(true); expect((await receipt.json()).factor).toBe(value === 'max' ? null : Number(value));
  }
  await page.screenshot({ path: join(shots, `${template}-initialized.png`) });
  // A single explicit click starts the authoritative run; Space only controls playback.
  const started = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/start`) && response.request().method() === 'POST');
  await page.getByTestId('wb-start').click(); expect((await started).ok()).toBe(true);
  await expect.poll(() => status(page), { timeout: 90_000 }).toMatch(/running|waiting_for_input/);
  await page.getByTestId('wb-speed').selectOption('5');
  await expect.poll(async () => Number(await page.getByTestId('wb-track').locator('input').getAttribute('max')), { timeout: 60_000 }).toBeGreaterThan(15);
  await page.getByTestId('wb-pause').click();
  await expect.poll(() => status(page), { timeout: 60_000 }).toBe('paused');
  if (template === 'food-delivery') {
    await page.keyboard.press('Space'); await expect(page.getByTestId('wb-playback')).toHaveAttribute('aria-label', 'Pause playback');
    expect(await status(page)).toBe('paused');
    await page.keyboard.press('Space');
    await page.keyboard.press('c'); await expect(page.getByTestId('wb-config')).toBeVisible();
    await page.getByTestId('wb-configure').click(); await expect(page.getByTestId('wb-config')).toHaveCount(0);
    await page.keyboard.press('r'); await expect(page.getByTestId('wb-records')).toBeVisible();
    await page.getByTestId('wb-records-toggle').click();
    await page.getByRole('button', { name: 'Hide objects', exact: true }).click();
    await page.getByRole('button', { name: 'Hide inspector', exact: true }).click();
    await expect(page.getByTestId('wb-scene-frame')).toHaveCSS('left', '24px');
    await page.screenshot({ path: join(shots, 'food-delivery-panels-hidden.png') });
    await page.locator('.wb-collapsed-tab.left').click(); await page.locator('.wb-collapsed-tab.right').click();
  }
  const row = page.getByTestId('wb-objects').locator('.wb-row').filter({ hasText: /drone|courier|uav|无人机|aircraft/i }).first();
  await row.click();
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'follow');
  // A paused tracked subject should release the GPU once its camera arrives.
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-idle', 'true', { timeout: 30_000 });
  const selected = new URL(page.url()).searchParams.get('sel');
  await expect(page.getByTestId('wb-inspector').locator('.wb-entity-head')).toContainText(selected!);
  await page.getByTestId('wb-camera').getByTitle('Zoom out', { exact: true }).click();
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'orbit');
  await expect(page.getByTestId('wb-camera').getByTitle('Follow selection (F)', { exact: true })).toHaveAttribute('aria-pressed', 'false');
  await page.keyboard.press('f'); await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'follow');
  await page.keyboard.press('g'); await expect(page.getByTestId('wb-graph')).toBeVisible();
  await expect(page.getByTestId('entity-graph')).toBeVisible();
  const graph = page.getByTestId('wb-graph'), handle = graph.locator('.wb-graph-handle');
  const start = await graph.boundingBox(), drag = await handle.boundingBox();
  const metrics = await page.getByTestId('wb-kpis').boundingBox();
  expect(start!.y).toBeGreaterThanOrEqual(metrics!.y + metrics!.height);
  const distance = start!.x + start!.width + 90 < page.viewportSize()!.width ? 75 : -75;
  await page.mouse.move(drag!.x + 80, drag!.y + 15); await page.mouse.down(); await page.mouse.move(drag!.x + 80 + distance, drag!.y + 55, { steps: 4 }); await page.mouse.up();
  expect(Math.abs((await graph.boundingBox())!.x - start!.x)).toBeGreaterThan(60);
  await graph.getByTitle('Expand', { exact: true }).click(); await expect(graph).toHaveClass(/is-expanded/);
  const droneOption = graph.getByRole('combobox', { name: 'Graph entity', exact: true }).locator('option').filter({ hasText: /^(Drone |UAV |Survey drone |Sprayer |Patrol drone |Air taxi |Meal courier |Incident drone )/ }).first();
  expect(await droneOption.count()).toBe(1);
  {
    await graph.getByRole('combobox', { name: 'Graph entity', exact: true }).selectOption((await droneOption.getAttribute('value'))!);
    const graphSelected = graph.locator('.eg-node[aria-pressed=true]'); await expect(graphSelected).toHaveCount(1);
    await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'follow');
    await expect(page.getByTestId('wb-objects').locator('.wb-row[aria-current=true]')).toHaveCount(1);
    await graphSelected.click();
    await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'follow');
  }
  await page.screenshot({ path: join(shots, `${template}-graph.png`) });
  await page.keyboard.press('Escape'); await expect(graph).toHaveCount(0);
  const timeline = page.getByTestId('wb-track').locator('input');
  await timeline.fill('1'); await timeline.dispatchEvent('input');
  await expect(page.getByTestId('wb-history')).toBeVisible(); await expect(page.getByTestId('wb-status')).toContainText('Paused');
  await page.keyboard.press('l'); await expect(page.getByTestId('wb-history')).toHaveCount(0);
  await page.getByTestId('wb-dock').getByRole('button', { name: /^Events / }).click();
  for (const size of [{ width: 1280, height: 800 }, { width: 1600, height: 1000 }, { width: 1920, height: 1080 }]) {
    await page.setViewportSize(size); await rendered(page); await noCoveredPanels(page);
    await expect(page.getByTestId('wb-objects').locator('.wb-row[aria-current=true]')).toBeInViewport({ ratio: 1 });
    const list = await page.getByTestId('wb-objects').locator('.wb-panel-body').boundingBox(), current = await page.getByTestId('wb-objects').locator('.wb-row[aria-current=true]').boundingBox();
    expect(current!.y).toBeGreaterThanOrEqual(list!.y - 1); expect(current!.y + current!.height).toBeLessThanOrEqual(list!.y + list!.height + 1);
    const frame = await page.getByTestId('wb-scene-frame').boundingBox(), metrics = await page.getByTestId('wb-kpis').boundingBox();
    expect(frame!.y).toBeGreaterThanOrEqual(metrics!.y + metrics!.height);
    await page.screenshot({ path: join(shots, `${template}-dock-${size.width}.png`) });
  }
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(page.getByTestId('wb-inspector').getByRole('tab', { name: '经历', exact: true })).toBeVisible();
  await page.getByTestId('wb-inspector').getByRole('tab', { name: '经历', exact: true }).click();
  await page.getByTestId('wb-inspector').getByRole('tab', { name: '操作', exact: true }).click();
  await page.getByRole('button', { name: '切换到英文', exact: true }).click();
  await page.keyboard.press('Control+k'); await expect(page.getByRole('dialog')).toBeVisible(); await page.keyboard.press('Escape');
  await page.getByTestId('wb-resume').click(); await expect.poll(() => status(page), { timeout: 60_000 }).toMatch(/running|waiting_for_input/);
  await page.getByTestId('wb-stop').click(); await expect.poll(() => status(page), { timeout: 60_000 }).toBe('stopped');
  await navigate(`/w/${workspace}?run=${run}&mode=replay`); await rendered(page);
  await expect(page.getByTestId('wb-stop')).toHaveCount(0);
  await page.getByTestId('wb-track').locator('input').fill('1');
  await expect(page.locator('.wb-clock small')).toHaveText('Replay time');
  await page.getByTestId('wb-playback').click(); await expect(page.getByTestId('wb-playback')).toHaveAttribute('aria-label', 'Pause playback');
  await page.screenshot({ path: join(shots, `${template}-replay.png`) });
  await page.getByTestId('wb-playback').click();
  await page.getByTestId('wb-dock').getByRole('button', { name: 'Collapse dock', exact: true }).click();
  expect(errors).toEqual([]);
});

test('compact graph keeps plotting space when timeline is expanded', async ({ request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('dialog');
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/ERR_ABORTED|favicon/.test(m.text())) errors.push(m.text()); });
  page.on('dialog', d => d.accept());
  const { workspace, run } = await initialize(request, 'food-delivery');
  await page.setViewportSize({ width: 1280, height: 800 });
  await navigate(`/w/${workspace}?run=${run}&mode=live`);
  await rendered(page);
  await page.getByTestId('wb-dock').getByRole('button', { name: /^Events / }).click();
  await expect(page.getByTestId('wb-dock')).toHaveClass(/is-open/);
  await page.keyboard.press('g');
  await expect(page.getByTestId('wb-dock')).not.toHaveClass(/is-open/);
  const graph = page.getByTestId('wb-graph'), body = graph.locator('.wb-graph-body');
  const windowBox = await graph.boundingBox(), plot = await graph.locator('.eg-canvas').boundingBox(), bodyBox = await body.boundingBox();
  expect(windowBox!.height).toBeGreaterThan(390);
  expect(Math.min(plot!.y + plot!.height, bodyBox!.y + bodyBox!.height) - Math.max(plot!.y, bodyBox!.y)).toBeGreaterThan(200);
  await graph.getByRole('combobox', { name: 'Graph entity', exact: true }).selectOption({ label: 'Meal courier 1' });
  const current = graph.locator('.eg-node[aria-pressed=true]');
  await expect(current).toBeInViewport({ ratio: .999 });
  const currentBox = await current.boundingBox();
  expect(currentBox!.y).toBeGreaterThanOrEqual(bodyBox!.y);
  expect(currentBox!.y + currentBox!.height).toBeLessThanOrEqual(bodyBox!.y + bodyBox!.height - 8);
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-camera', 'follow');
  await page.screenshot({ path: join(shots, 'compact-graph-1280.png') });
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.getByTestId('wb-dock').getByRole('button', { name: /^Events / }).click();
  await expect(page.getByTestId('wb-dock')).toHaveClass(/is-open/);
  await page.keyboard.press('g');
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByTestId('wb-dock')).not.toHaveClass(/is-open/);
  await page.keyboard.press('Escape');
  await page.getByTestId('wb-stop').click();
  await expect.poll(() => status(page), { timeout: 60_000 }).toBe('stopped');
  expect(errors).toEqual([]);
});

test('real runtime controls stay synchronized while the journal transport is delayed', async ({ request }) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('dialog');
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error' && !/ERR_ABORTED|favicon/.test(m.text())) errors.push(m.text()); });
  page.on('dialog', d => d.accept());
  const { workspace, run } = await initialize(request, 'food-delivery');
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let continued!: () => void, delayed = false;
  const streamContinued = new Promise<void>(resolve => { continued = resolve; });
  await page.route(`**/v1/runs/${run}/stream?*`, async route => { delayed = true; await gate; await route.continue(); continued(); });
  try {
    await page.setViewportSize({ width: 1600, height: 1000 });
    await navigate(`/w/${workspace}?run=${run}&mode=live`); await rendered(page);
    await page.getByTestId('wb-speed').selectOption('0.5');
    await page.getByTestId('wb-start').click();
    await expect.poll(() => status(page), { timeout: 30_000 }).toBe('running');
    await expect(page.getByTestId('wb-start')).toHaveCount(0);
    await expect(page.getByTestId('wb-catching-up')).toBeVisible();
    await expect(page.getByTestId('wb-pause')).toBeEnabled();
    await page.screenshot({ path: join(shots, 'runtime-with-delayed-journal.png') });
    await page.getByTestId('wb-pause').click();
    await expect.poll(() => status(page), { timeout: 30_000 }).toBe('paused');
    await expect(page.getByTestId('wb-resume')).toBeEnabled();
    release(); await streamContinued; await page.unroute(`**/v1/runs/${run}/stream?*`);
    await expect(page.getByTestId('wb-catching-up')).toHaveCount(0, { timeout: 60_000 });
    await page.getByTestId('wb-objects').locator('.wb-row').filter({ hasText: 'Meal courier 1' }).click();
    await page.getByTestId('wb-inspector').getByRole('tab', { name: 'Operate', exact: true }).click();
    await expect(page.getByTestId('wb-inspector').getByRole('button', { name: 'Stop simulation', exact: true })).toHaveCount(0);
    await expect(page.getByTestId('wb-stop')).toBeEnabled();
    const keyboardControls: string[] = [];
    const recordControl = (request: import('@playwright/test').Request) => { if (request.method() === 'POST' && new RegExp(`/v1/runs/${run}/(start|pause|resume|stop)$`).test(request.url())) keyboardControls.push(request.url()); };
    page.on('request', recordControl);
    await page.locator('body').click({ position: { x: 8, y: 400 } });
    await page.keyboard.press('Space');
    await page.waitForTimeout(1500); // Give an accidental engine command time to reach the real API.
    expect(keyboardControls).toEqual([]);
    expect((await (await request.get(`/v1/runs/${run}/commits?from=1&limit=1`)).json()).status).toBe('paused');
    await expect(page.getByTestId('wb-inspector').getByRole('button', { name: 'Submit typed ingress', exact: true })).toBeDisabled();
    await expect(page.getByTestId('wb-inspector')).toContainText('Back to live before introducing an event.');
    await page.screenshot({ path: join(shots, 'operate-space-playback-only.png') });
    page.off('request', recordControl);
    await page.getByTestId('wb-resume').click();
    await expect.poll(() => status(page), { timeout: 30_000 }).toBe('running');
    await page.getByTestId('wb-stop').click();
    await expect.poll(() => status(page), { timeout: 30_000 }).toBe('stopped');
    await expect(page.getByTestId('wb-catching-up')).toHaveCount(0, { timeout: 60_000 });
    expect(errors).toEqual([]);
  } finally { release(); if (delayed) await streamContinued; await page.unroute(`**/v1/runs/${run}/stream?*`); }
});

for (const template of ['food-delivery', 'city-day', 'traffic-accident']) test(`polished workbench shell: ${template} in five states, both languages and three sizes`, async ({ request }) => {
  test.setTimeout(900_000);
  const errors: string[] = [];
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('response');
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/favicon|ERR_ABORTED/.test(message.text())) errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) errors.push(`${response.status()} ${response.request().method()} ${response.url()}`); });
  const { workspace, run } = await initialize(request, template);
  await request.post(`/v1/runs/${run}/speed`, { data: { factor: 0.5 } });
  await navigate(`/w/${workspace}?run=${run}&mode=live`); await rendered(page);
  await expect(page.getByTestId('wb-inspector')).toHaveAttribute('data-compact', 'true');
  const matrix = async (state: string) => {
    for (const size of [{ width: 1280, height: 800 }, { width: 1600, height: 1000 }, { width: 1920, height: 1080 }]) {
      await page.setViewportSize(size);
      for (const locale of ['en', 'zh']) {
        const toggle = page.getByRole('button', { name: locale === 'en' ? '切换到英文' : 'Switch to Chinese', exact: true });
        if (await toggle.count()) await toggle.click();
        await rendered(page); await noCoveredPanels(page);
        // Inspect real screen geometry, including labels in front of the glass.
        const covers = await page.evaluate(() => {
          const rect = (element: Element) => element.getBoundingClientRect();
          const obstacles = [...document.querySelectorAll('.wb-panel, .wb-kpis, .wb-top, .wb-dock, .wb-camera, .wb-history')].map(rect);
          return [...document.querySelectorAll<HTMLElement>('.entity-label:not([hidden])')].flatMap(element => {
            const label = rect(element);
            const covered = obstacles.some(box => label.left < box.right && label.right > box.left && label.top < box.bottom && label.bottom > box.top);
            return covered || label.left < 0 || label.right > innerWidth ? [element.textContent] : [];
          });
        }); expect(covers).toEqual([]);
        expect(await page.getByTestId('wb-kpis').innerText()).not.toMatch(/medical_mean_task_latency/);
        if (template === 'city-day' && locale === 'zh') await expect(page.getByTestId('wb-kpis')).toContainText('医疗任务平均用时');
        await page.screenshot({ path: join(shots, `${template}-${state}-${size.width}-${locale}.png`) });
      }
    }
    const toggle = page.getByRole('button', { name: '切换到英文', exact: true }); if (await toggle.count()) await toggle.click();
  };
  await matrix('initialized');
  const speeds = page.getByTestId('wb-speed'); expect(await speeds.locator('option').allTextContents()).toEqual(['0.5×', '1×', '2×', '5×', '10×', '50×', '100×', 'Max']);
  await speeds.selectOption('50'); await expect(speeds).toHaveValue('50');
  expect((await (await request.get(`/v1/runs/${run}/speed`)).json()).factor).toBe(50);
  await speeds.selectOption('0.5'); await page.getByTestId('wb-start').click(); await expect.poll(() => status(page), { timeout: 60_000 }).toBe('running');
  await matrix('running');
  await page.getByTestId('wb-pause').click(); await expect.poll(() => status(page), { timeout: 60_000 }).toBe('paused');
  const configuration = await (await request.get(`/v1/studio/runs/${run}/configuration`)).json();
  const drone = configuration.scenario.entities.find((row: any) => row.id === 'uav.alpha') ?? configuration.scenario.entities.find((row: any) => ['aas:TrafficUAV', 'lowalt:UAV'].includes(row.type)); expect(drone).toBeTruthy();
  await page.getByRole('textbox', { name: 'Search objects', exact: true }).fill(drone.id); await page.getByTestId('wb-objects').locator('.wb-row').first().click();
  await expect(page.getByTestId('wb-inspector')).toHaveAttribute('data-compact', 'false');
  await page.getByRole('button', { name: 'Focus', exact: true }).click(); await page.waitForTimeout(1000);
  const name = await page.locator('.wb-entity-head h2').innerText();
  const identityFields = page.locator('.inspection-fields > div').filter({ has: page.locator('dt').filter({ hasText: /^Label$|^Name$/ }) });
  if (await identityFields.count()) await expect(identityFields.locator('dd').first()).toHaveText(name);
  await matrix('paused');
  await page.getByRole('tab', { name: 'Operate', exact: true }).click();
  await expect(page.getByTestId('wb-inspector').getByRole('group', { name: 'Simulation controls', exact: true })).toHaveCount(0);
  await page.getByTestId('wb-inspector').locator('.wb-panel-body').evaluate(element => { element.scrollTop = 0; });
  await page.setViewportSize({ width: 1280, height: 800 }); await page.screenshot({ path: join(shots, `${template}-operate-1280-en.png`) });
  await page.getByRole('tab', { name: 'State', exact: true }).click();
  const timeline = page.getByTestId('wb-track').locator('input'); await timeline.fill('2'); await timeline.dispatchEvent('input');
  await expect(page.getByTestId('wb-history')).toBeVisible(); await page.getByTestId('wb-graph-toggle').click(); await expect(page.locator('.behaviour-run-graph')).toBeVisible();
  await page.setViewportSize({ width: 1280, height: 800 });
  const reachable = await page.getByTestId('wb-back-live').evaluate(element => { const box = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2)); }); expect(reachable).toBe(true);
  await page.screenshot({ path: join(shots, `${template}-history-graph-1280-en.png`) });
  await page.getByTestId('wb-back-live').click(); await expect(page.getByTestId('wb-history')).toHaveCount(0); await page.getByTestId('wb-graph-toggle').click();
  await speeds.selectOption('max'); await page.getByTestId('wb-resume').click(); await expect.poll(() => status(page), { timeout: 180_000 }).toBe('completed');
  await expect(page.getByTestId('wb-catching-up')).toHaveCount(0, { timeout: 180_000 }); await matrix('completed');
  const head = Number(await timeline.getAttribute('max')); await navigate(`/w/${workspace}?run=${run}&mode=replay&cut=${Math.max(2, Math.floor(head / 2))}`);
  await rendered(page); await expect(page.getByTestId('wb-seeking')).toHaveCount(0, { timeout: 120_000 });
  await matrix('replaying-history'); expect(errors).toEqual([]);
});
