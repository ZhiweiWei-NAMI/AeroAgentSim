import { expect, test, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';

test.use({ launchOptions: { executablePath: process.env.AEROAGENTSIM_CHROMIUM, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'] } });

async function initializedWorld(request: APIRequestContext, template: string) {
  const post = async (path: string, data: unknown) => {
    const response = await request.post(path, { data, timeout: 120_000 });
    expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  const created = await post(`/v1/templates/${template}/workspace`, { knobs: {} }), workspace = created.workspace_id;
  const draftResponse = await request.get(`/v1/studio/workspaces/${workspace}`); expect(draftResponse.ok()).toBe(true);
  const draft = await draftResponse.json();
  expect((await post(`/v1/studio/workspaces/${workspace}/validate`, { revision: draft.revision })).valid).toBe(true);
  const version = await post(`/v1/studio/workspaces/${workspace}/versions`, { revision: draft.revision });
  const run = await post('/v1/runs', { studio_workspace: workspace, version: version.version, autostart: false });
  return { workspace, run: run.id };
}

async function ready(page: Page) {
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true', { timeout: 180_000 });
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-city', 'loaded', { timeout: 180_000 });
  await expect(page.getByTestId('wb-catching-up')).toBeHidden({ timeout: 180_000 });
}

async function locale(page: Page, zh: boolean) {
  const toggle = page.getByRole('button', { name: zh ? 'Switch to Chinese' : '切换到英文', exact: true });
  if (await toggle.count()) await toggle.click();
  await expect(page.locator('html')).toHaveAttribute('lang', zh ? 'zh-CN' : 'en');
}

async function screenshot(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}.png`); await page.screenshot({ path });
  await info.attach(name, { path, contentType: 'image/png' });
}

function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  return errors;
}

async function contained(locator: Locator, parent: Locator) {
  await expect(locator).toBeInViewport({ ratio: .999 });
  const box = await locator.boundingBox(), bounds = await parent.boundingBox();
  expect(box).not.toBeNull(); expect(bounds).not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(bounds!.y - 1);
  expect(box!.y + box!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height + 1);
}

test('relation graph keeps filters and controls visible while revealing selected nodes at every workbench size', async ({ page, request }, info) => {
  test.setTimeout(480_000); const errors = collectErrors(page);
  const world = await initializedWorld(request, 'city-day');
  try {
    await page.goto(`/w/${world.workspace}?run=${world.run}&mode=live`); await ready(page); await locale(page, false);
    await page.getByTestId('wb-graph-toggle').click();
    const graph = page.getByTestId('wb-graph'), plot = graph.locator('.eg-canvas');
    for (const [width, height] of [[1280, 800], [1600, 1000], [1920, 1080]]) {
      await page.setViewportSize({ width, height });
      for (const zh of [false, true]) {
        await locale(page, zh);
        await graph.getByRole('combobox', { name: zh ? '图中实体' : 'Graph entity', exact: true }).selectOption('uav.bravo:0');
        await expect(graph.locator('.eg-node[aria-pressed=true]')).toBeVisible();
        // Deliberately scroll the plot; selecting Alpha must reveal its node
        // without taking either control row out of the floating window.
        await plot.evaluate(element => { element.scrollTop = element.scrollHeight; });
        await graph.getByRole('combobox', { name: zh ? '图中实体' : 'Graph entity', exact: true }).selectOption('uav.alpha:0');
        await contained(graph.locator('.eg-node[aria-pressed=true]'), plot);
        for (const select of [graph.locator('.behaviour-filters select').nth(0), graph.locator('.behaviour-filters select').nth(1)]) {
          await contained(select, graph.locator('.wb-graph-body'));
          expect(await select.evaluate(element => { const box = element.getBoundingClientRect(); return element.contains(document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)); })).toBe(true);
        }
        await contained(graph.locator('.eg-controls'), graph.locator('.wb-graph-body'));
        expect(await graph.locator('.wb-graph-body').evaluate(element => element.scrollTop)).toBe(0);
        await screenshot(page, info, `graph-controls-${width}-${zh ? 'zh' : 'en'}`);
      }
    }
    expect(errors).toEqual([]);
  } finally { const stopped = await request.post(`/v1/runs/${world.run}/stop`, { data: {} }); expect(stopped.ok() || stopped.status() === 409, "stop, or the run already finished").toBe(true); }
});

test('clearing object search reveals selection, grounded cameras retain context and inspector tabs retain separate scroll', async ({ page, request }, info) => {
  test.setTimeout(480_000); const errors = collectErrors(page);
  const world = await initializedWorld(request, 'food-delivery');
  try {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`/w/${world.workspace}?run=${world.run}&mode=live`); await ready(page); await locale(page, false);
    const inspector = page.getByTestId('wb-inspector'), objects = page.getByTestId('wb-objects'), viewport = page.getByTestId('viewport');
    const search = page.getByRole('textbox', { name: 'Search objects', exact: true });
    await search.fill('drone-1'); await objects.getByTitle('drone-1', { exact: true }).click(); await search.fill('');
    await expect(objects.getByTitle('drone-1', { exact: true })).toHaveAttribute('aria-current', 'true');
    await contained(objects.getByTitle('drone-1', { exact: true }), objects.locator('.wb-panel-body'));
    const offset = async () => viewport.evaluate(element => {
      const position = JSON.parse(element.dataset.cameraPosition!), target = JSON.parse(element.dataset.cameraTarget!);
      return { height: position[1] - target[1], horizontal: Math.hypot(position[0] - target[0], position[2] - target[2]) };
    });
    for (const [width, height] of [[1280, 800], [1600, 1000], [1920, 1080]]) {
      await page.setViewportSize({ width, height });
      await contained(objects.getByTitle('drone-1', { exact: true }), objects.locator('.wb-panel-body'));
      if (await viewport.getAttribute('data-camera') !== 'follow') await inspector.getByRole('button', { name: 'Follow', exact: true }).click();
      await expect(viewport).toHaveAttribute('data-camera', 'follow'); await expect(viewport).toHaveAttribute('data-idle', 'true', { timeout: 60_000 });
      await expect.poll(async () => (await offset()).height).toBeGreaterThanOrEqual(99);
      expect((await offset()).horizontal).toBeGreaterThan(100);
      await screenshot(page, info, `grounded-follow-search-cleared-${width}`);
      await inspector.getByRole('button', { name: 'Focus', exact: true }).click();
      await expect(viewport).toHaveAttribute('data-camera', 'orbit');
      await expect.poll(async () => (await offset()).height).toBeGreaterThanOrEqual(99);
      await expect(viewport).toHaveAttribute('data-idle', 'true', { timeout: 60_000 });
      await screenshot(page, info, `grounded-focus-${width}`);
    }

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByTestId('wb-dock').getByRole('button', { name: /^Events\b/ }).click();
    const body = inspector.locator('.wb-panel-body');
    await inspector.getByRole('tab', { name: 'Operate', exact: true }).click();
    await expect(inspector.getByLabel('Payload.restaurant', { exact: true })).toBeVisible({ timeout: 60_000 });
    await body.evaluate(element => { element.scrollTop = element.scrollHeight; });
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBeGreaterThan(50);
    const operateTop = await body.evaluate(element => element.scrollTop);
    await inspector.getByRole('tab', { name: 'State', exact: true }).click();
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(0);
    await screenshot(page, info, 'inspector-state-after-operate-1280');
    await body.evaluate(element => { element.scrollTop = 80; });
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(80);
    await inspector.getByRole('tab', { name: 'Operate', exact: true }).click();
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(operateTop);
    await inspector.getByRole('tab', { name: 'State', exact: true }).click();
    await expect.poll(() => body.evaluate(element => element.scrollTop)).toBe(80);
    await locale(page, true);
    const labels = await inspector.locator('.inspection-fields dt').allTextContents(); expect(labels.length).toBeGreaterThan(15);
    for (const label of labels) expect(label).toMatch(/[\u3400-\u9fff]/);
    await screenshot(page, info, 'inspector-fields-zh-1280');
    expect(errors).toEqual([]);
  } finally { const stopped = await request.post(`/v1/runs/${world.run}/stop`, { data: {} }); expect(stopped.ok() || stopped.status() === 409, "stop, or the run already finished").toBe(true); }
});
