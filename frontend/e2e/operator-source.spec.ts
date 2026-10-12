import { expect, test } from '@playwright/test';

test.use({ launchOptions: { executablePath: process.env.AEROAGENTSIM_CHROMIUM, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'] } });

// A second source producer can close an interval while a browser closure is
// in flight. Delaying that real request makes the concurrency reproducible.
test('operator source follows a concurrent closure without an invalid-prefix error', async ({ page }, info) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const workspace = await page.request.post('/v1/templates/food-delivery/workspace', { data: { knobs: { run_mode: 'interactive' } } });
  expect(workspace.status()).toBe(201);
  const id = (await workspace.json()).workspace_id;
  const draft = await (await page.request.get(`/v1/studio/workspaces/${id}`)).json();
  const validation = await page.request.post(`/v1/studio/workspaces/${id}/validate`, { data: { revision: draft.revision } });
  expect((await validation.json()).valid).toBe(true);
  const version = await page.request.post(`/v1/studio/workspaces/${id}/versions`, { data: { revision: draft.revision } });
  expect(version.status()).toBe(201);
  const initialized = await page.request.post('/v1/runs', { data: { studio_workspace: id, version: (await version.json()).version } });
  expect(initialized.status()).toBe(201);
  const run = (await initialized.json()).id;
  let release!: () => void, intercepted!: () => void;
  const held = new Promise<void>(resolve => { intercepted = resolve; });
  const gate = new Promise<void>(resolve => { release = resolve; });
  let writes = 0;
  await page.route('**/watermark', async route => {
    writes += 1;
    intercepted();
    await gate;
    await route.continue();
  });
  try {
    await page.goto(`/w/${id}?run=${run}&mode=live`);
    await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true', { timeout: 90_000 });
    await page.getByTestId('wb-start').click();
    await held;
    const receipt = page.waitForResponse(response => response.url().endsWith('/watermark'));
    const closed = await page.request.post(`/v1/runs/${run}/watermark`, { data: { stream_id: 'operator', watermark_ns: draft.scenario.run.until_ns } });
    expect(closed.ok()).toBe(true);
    release();
    const response = await receipt;
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({ watermark_ns: draft.scenario.run.until_ns, advanced: false });
    await page.getByTestId('wb-speed').selectOption('max');
    await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'completed', { timeout: 120_000 });
    await expect(page.getByText(/Operator input source unavailable/)).toHaveCount(0);
    expect(writes).toBe(1);
    for (const [width, height] of [[1280, 800], [1600, 1000], [1920, 1080]]) {
      await page.setViewportSize({ width, height });
      const path = info.outputPath(`source-closed-${width}-en.png`);
      await page.screenshot({ path });
      await info.attach(`source-closed-${width}`, { path, contentType: 'image/png' });
    }
    await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
    const chinese = info.outputPath('source-closed-1920-zh.png');
    await page.screenshot({ path: chinese });
    await info.attach('source-closed-zh', { path: chinese, contentType: 'image/png' });
    expect(errors).toEqual([]);
  } finally {
    release();
    await page.unrouteAll({ behavior: 'wait' });
    const progress = await (await page.request.get(`/v1/runs/${run}/inputs`)).json();
    if (!['completed', 'stopped'].includes(progress.status)) await page.request.post(`/v1/runs/${run}/stop`, { data: {} });
  }
});

test('operator source progresses during a busy scene and still yields to pause', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('dialog', dialog => dialog.accept());
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/ERR_ABORTED|favicon/.test(message.text())) errors.push(message.text()); });
  const post = async (path: string, data: unknown) => { const response = await page.request.post(path, { data }); expect(response.ok(), await response.text()).toBe(true); return response.json(); };
  const created = await post('/v1/templates/food-delivery/workspace', { knobs: { run_mode: 'interactive' } });
  const id = created.workspace_id, draft = await (await page.request.get(`/v1/studio/workspaces/${id}`)).json();
  expect((await post(`/v1/studio/workspaces/${id}/validate`, { revision: draft.revision })).valid).toBe(true);
  const version = await post(`/v1/studio/workspaces/${id}/versions`, { revision: draft.revision });
  const initialized = await post('/v1/runs', { studio_workspace: id, version: version.version, autostart: false });
  const run = initialized.id, progress = async () => (await (await page.request.get(`/v1/runs/${run}/inputs`)).json()).watermarks.operator as number;
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto(`/w/${id}?run=${run}&mode=live`);
  await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'initialized', { timeout: 90_000 });
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true', { timeout: 90_000 });
  await page.getByTestId('wb-speed').selectOption('10'); await page.getByTestId('wb-start').click();
  await expect.poll(progress, { timeout: 30_000 }).toBeGreaterThan(0);
  const before = await progress(); let finished = false;
  // A real long main-thread task models expensive scene work; API and worker
  // requests remain real. Read the authoritative frontier while it is blocked.
  const busy = page.evaluate(() => { const start = performance.now(); while (performance.now() - start < 6000) {} }).then(() => { finished = true; });
  await new Promise(resolve => setTimeout(resolve, 3000));
  const during = await progress(); const observedWhileBusy = !finished;
  await busy;
  expect(observedWhileBusy).toBe(true);
  expect(during - before, 'one in-flight UI request cannot account for three source boundaries').toBeGreaterThan(2_000_000_000);
  const pause = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/pause`) && response.request().method() === 'POST', { timeout: 30_000 });
  await page.getByTestId('wb-pause').click(); expect((await pause).ok()).toBe(true);
  await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'paused', { timeout: 30_000 });
  const paused = await progress(); await new Promise(resolve => setTimeout(resolve, 1000)); expect(await progress()).toBe(paused);
  await info.attach('real-source-progress', { body: JSON.stringify({ before, during, paused, observedWhileBusy }), contentType: 'application/json' });
  await page.screenshot({ path: info.outputPath('busy-scene-source-paused.png') });
  await page.getByTestId('wb-stop').click(); await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'stopped', { timeout: 30_000 });
  expect(errors).toEqual([]);
});
