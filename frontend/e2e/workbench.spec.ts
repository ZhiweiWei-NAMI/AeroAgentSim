import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { writeFile } from 'node:fs/promises';

const TEMPLATES = ['parcel-delivery', 'medical-delivery', 'powerline-inspection', 'crop-spraying', 'air-taxi', 'security-patrol', 'traffic-accident', 'city-day', 'food-delivery'];
const status = (page: Page) => page.getByTestId('workbench').getAttribute('data-status');
const identity = (page: Page) => new URL(page.url()).searchParams.get('run')!;
const observedErrors = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => { page.setDefaultTimeout(30_000); page.setDefaultNavigationTimeout(90_000); });
test.use({ viewport: { width: 1600, height: 1000 }, launchOptions: {
  executablePath: process.env.AEROAGENTSIM_CHROMIUM,
  args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'],
} });
test.afterEach(async ({ page }, info) => {
  await info.attach('browser-errors', { body: JSON.stringify(observedErrors.get(page) ?? []), contentType: 'application/json' });
  if (info.status !== info.expectedStatus && !page.isClosed()) {
    await info.attach('failure-url', { body: page.url(), contentType: 'text/plain' });
    await shot(page, info, 'failure');
  }
});

function watchErrors(page: Page) {
  const errors: string[] = [];
  observedErrors.set(page, errors);
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/favicon|ERR_ABORTED/.test(message.text())) errors.push(message.text()); });
  return errors;
}

async function shot(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}.png`);
  await page.screenshot({ path, timeout: 30_000 });
  await info.attach(name, { path, contentType: 'image/png' });
}

async function createDraft(page: Page, template: string) {
  await page.goto('/?gallery=1');
  await page.getByTestId(`wb-template-${template}`).click();
  await expect(page.getByTestId('wb-guide')).toBeVisible();
  await page.getByTestId('wb-create-project').click();
  await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
  await expect(page).toHaveURL(/\/w\//);
}

async function initialize(page: Page) {
  await page.getByTestId('wb-validate').click();
  await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
  await page.getByTestId('wb-save-version').click();
  await expect(page.getByTestId('wb-initialize')).toBeEnabled({ timeout: 60_000 });
  await page.getByTestId('wb-initialize').click();
  await expect.poll(() => status(page), { timeout: 90_000 }).toBe('initialized');
  await expect(page.locator('[data-testid="viewport"][data-rendered="true"]')).toBeVisible({ timeout: 90_000 });
  // The canvas can paint city geometry before a large world feed finishes.
  await expect(page.getByTestId('wb-objects').locator('.wb-row').first()).toBeVisible({ timeout: 30_000 });
}

async function selectDrone(page: Page) {
  const configuration = await (await page.request.get(`/v1/studio/runs/${identity(page)}/configuration`)).json();
  const drone = configuration.scenario.entities.find((entity:any)=>['lowalt:UAV','aas:TrafficUAV'].includes(entity.type));
  expect(drone, 'The pinned world contains an aerial vehicle').toBeTruthy();
  await page.getByRole('textbox', { name: 'Search objects', exact: true }).fill(drone.id);
  const row = page.getByTestId('wb-objects').locator('.wb-row').first();
  await expect(row).toBeVisible();
  const id = await row.getAttribute('title');
  await row.click();
  await expect(row).toHaveAttribute('aria-current', 'true');
  await expect(page.getByTestId('wb-inspector')).toContainText(id!);
  await expect(page).toHaveURL(new RegExp(`sel=${encodeURIComponent(id!)}`));
  return id!;
}

async function stop(page: Page) {
  if (!['completed', 'stopped'].includes(await status(page) ?? '')) {
    await page.getByTestId('wb-stop').click();
  }
  await expect.poll(() => status(page), { timeout: 60_000 }).toMatch(/stopped|completed/);
}

async function openGraph(page: Page, selectedId: string) {
  await page.getByTestId('wb-graph-toggle').click();
  const graph = page.getByTestId('wb-graph');
  await expect(graph).toBeVisible();
  const selection = graph.getByRole('combobox', { name: 'Graph entity', exact: true });
  await expect(selection).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => selection.inputValue()).toContain(selectedId);
  await expect(graph.locator('.behaviour-run-graph')).toHaveAttribute('data-cut', /^\d+$/);
}

async function operatorFrontier(page: Page, value: string) {
  await page.getByTestId('wb-config').getByRole('button', { name: 'Run & recording', exact: true }).click();
  const frontier=page.getByTestId('wb-config').locator('[data-path="/ingress_streams/0/initial_watermark_ns"] input');
  await frontier.fill(value);await frontier.press('Tab');
}

async function injectAuthored(page: Page, template: string, info: TestInfo) {
  const configuration=await (await page.request.get(`/v1/studio/runs/${identity(page)}/configuration`)).json();
  await page.getByRole('tab',{name:'Operate',exact:true}).click();
  let point='accident';
  if(template==='security-patrol') {
    point='investigate';
    const task=configuration.scenario.entities.find((entity:any)=>entity.facts?.['lowalt.task_kind']==='incident_response');
    expect(task).toBeTruthy();
    for(const key of ['task','origin','destination'])await page.getByLabel(`Payload.${key}`,{exact:true}).selectOption(key==='task'?task.id:task.facts[`lowalt.task_${key}`]);
  } else if(template==='city-day') {
    point='close-vertiport';
    await page.getByRole('button',{name:'Inject close vertiport',exact:true}).click();
    const site=configuration.scenario.entities.find((entity:any)=>entity.type==='lowalt:Vertiport'&&entity.id==='east-port');
    expect(site).toBeTruthy();await page.getByLabel('Payload.site',{exact:true}).selectOption(site.id);
  }
  await expect(page.getByTestId(`inject-${point}`)).toBeEnabled();
  const response=page.waitForResponse(reply=>reply.url().endsWith('/ingress'));
  await page.getByTestId(`inject-${point}`).click();
  const admission = await (await response).json();
  expect(admission.disposition).toBe('accepted');
  await expect(page.getByTestId('wb-inspector')).toContainText('Admission receipts · 1');
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${identity(page)}/commits?from=1&limit=4096`)).json();
    return feed.commits.flatMap((commit:any)=>commit.receipts).some((receipt:any)=>receipt.commandId===admission.command_id&&receipt.status==='succeeded');
  }, { timeout: 60_000 }).toBe(true);
  await shot(page,info,'authored-injection');
  // Simulation control belongs to the top bar.
  await page.getByTestId('wb-pause').click();
  await expect.poll(()=>status(page),{timeout:30_000}).toBe('paused');
}

async function deriveAndCompare(page: Page, original: string, info: TestInfo) {
  await page.getByTestId('wb-records-toggle').click();
  const record = page.getByTestId('wb-records').locator('.wb-record').filter({ hasText: original });
  await record.getByRole('button', { name: 'Modify & retry', exact: true }).click();
  await expect(page.getByTestId('wb-config')).toBeVisible();
  const workspace = new URL(page.url()).pathname.split('/').at(-1)!;
  const response = await page.request.get(`/v1/studio/workspaces/${workspace}`);
  const draft=await response.json();expect(draft.derived_from.run_id).toBe(original);
  const parent = await (await page.request.get(`/v1/studio/runs/${original}/configuration`)).json();
  if(draft.scenario.ingress_streams?.[0]?.id==='operator')await operatorFrontier(page,String(draft.scenario.run.until_ns));
  // A direct form edit, absent from template knobs, must appear in comparison.
  await page.getByTestId('wb-config').getByRole('button', { name: 'Run & recording', exact: true }).click();
  const seed = page.getByTestId('wb-config').locator('[data-path="/run/seed"] input');
  await seed.fill('43'); await seed.press('Tab');
  await initialize(page);
  const variant = identity(page);
  expect(variant).not.toBe(original);
  const child = await (await page.request.get(`/v1/studio/runs/${variant}/configuration`)).json();
  // Compare the two compiled worlds: authored fleets expand into entities at
  // initialization and must not be mistaken for lost draft entities.
  expect(child.scenario.entities).toEqual(parent.scenario.entities);
  await page.getByTestId('wb-start').click();
  await expect.poll(()=>status(page),{timeout:60_000}).toMatch(/running|completed/);
  if(await status(page)!=='completed')await page.getByTestId('wb-speed').selectOption('max');
  await expect.poll(()=>status(page),{timeout:180_000}).toBe('completed');
  if(draft.scenario.id==='traffic-accident') {
    await page.getByTestId('wb-dock').getByRole('button', { name:'Photos', exact:true }).click();
    await expect(page.getByRole('button', { name:'Open photo', exact:true }).first()).toBeVisible({ timeout:60_000 });
    await page.getByRole('button', { name:'Open photo', exact:true }).first().click();
    const photo = page.getByRole('img', { name:'Stored capture photo', exact:true });
    await expect(photo).toBeVisible();
    expect(await photo.evaluate((element:HTMLImageElement)=>element.naturalWidth)).toBeGreaterThan(0);
    await photo.scrollIntoViewIfNeeded();
    await shot(page, info, 'accident-photo');
  }
  await page.getByTestId('wb-records-toggle').click();
  await page.getByTestId('wb-records').getByRole('button', { name: 'All runs', exact: true }).click();
  for (const id of [original, variant]) await page.getByTestId('wb-records').locator('.wb-record').filter({ hasText: id }).getByRole('checkbox').check();
  await page.getByTestId('wb-compare').click();
  await expect(page.getByTestId('comparison-configuration')).toContainText('/run/seed', { timeout: 60_000 });
  await expect(page.getByTestId('comparison-configuration')).toContainText('43');
  await expect(page.getByTestId('comparison-configuration')).toContainText('simulation durations may differ');
  await shot(page, info, 'comparison');
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(page.getByTestId('comparison-configuration')).toContainText('仿真时长可能不同');
  await page.getByRole('button', { name: '切换到英文', exact: true }).click();
}

test('every ready-made world opens its scenario guide from the thumbnail', async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto('/?gallery=1');
  for (const id of TEMPLATES) {
    await page.getByTestId(`wb-template-${id}`).click();
    await expect(page.getByTestId('wb-guide')).toBeVisible();
    await expect(page.getByTestId('wb-create-project')).toBeEnabled();
    await page.goto('/?gallery=1');
  }
  expect(errors).toEqual([]);
});

test('food main line: forms, delay, receipts, historical cut, derive and compare', async ({ page }, info) => {
  test.setTimeout(480_000);
  const errors = watchErrors(page), writes: string[] = [];
  page.on('dialog', dialog => dialog.accept());
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  await createDraft(page, 'food-delivery');
  await page.getByTestId('knob-run_mode').click();
  await page.getByText('interactive', { exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Delivery drones', exact: true }).fill('3');
  await page.getByRole('spinbutton', { name: 'East kitchen preparation (s)', exact: true }).fill('40');
  await page.getByRole('spinbutton', { name: 'Orders per second', exact: true }).fill('0.1');
  await page.getByTestId('knob-allocator').click();
  await page.locator('.ant-select-item-option-content').filter({ hasText: /auction/i }).click();
  await page.getByRole('button', { name: 'Apply controls to draft', exact: true }).click();
  await expect(page.getByTestId('wb-config')).toContainText('Controls applied');
  await initialize(page);
  const run = identity(page);
  const configuration = await (await page.request.get(`/v1/studio/runs/${run}/configuration`)).json();
  expect(configuration.scenario.entities.filter((entity:any) => entity.type==='lowalt:UAV')).toHaveLength(3);
  expect(configuration.scenario.entities.find((entity:any) => entity.id==='restaurant-east').facts['lowalt.food_preparation_s']).toBe(40);
  await shot(page, info, 'initialized');
  for (const viewport of [{width:1280,height:800},{width:1920,height:1080}]) {
    await page.setViewportSize(viewport);
    await expect(page.getByTestId('wb-start')).toBeInViewport();
    await expect(page.getByRole('button',{name:'Switch to Chinese',exact:true})).toBeInViewport();
    await shot(page,info,`initialized-${viewport.width}`);
  }
  await page.setViewportSize({width:1600,height:1000});
  await page.getByTestId('wb-start').click();
  await expect.poll(() => status(page), { timeout: 60_000 }).toMatch(/running|waiting_for_input/);
  await selectDrone(page);
  await page.getByRole('tab', { name: 'Operate', exact: true }).click();
  await expect(page.getByTestId('inject-restaurant_delay')).toBeDisabled();
  // Wait for a real kitchen order; a delay injected before preparation would
  // produce an admission receipt but would change no dispatch decision.
  let preparing: any;
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${run}/commits?from=1&limit=4096`)).json();
    preparing = feed.commits.flatMap((commit:any)=>commit.messages).find((message:any)=>message.schemaId==='lowalt.food.preparation_started');
    return preparing!==undefined;
  }, { timeout: 60_000 }).toBe(true);
  await page.getByLabel('Payload.restaurant', { exact: true }).selectOption(preparing.payload.restaurant);
  await page.getByLabel('Payload.delay_s', { exact: true }).fill('45');
  await expect(page.getByTestId('inject-restaurant_delay')).toBeEnabled();
  const admitted = page.waitForResponse(response => response.url().endsWith('/ingress'));
  await page.getByTestId('inject-restaurant_delay').click();
  expect((await (await admitted).json()).disposition).toBe('accepted');
  await expect(page.getByTestId('wb-inspector')).toContainText('Admission receipts · 1');
  await shot(page, info, 'delay-receipt');
  await page.getByTestId('wb-speed').selectOption('50');
  await page.getByTestId('wb-dock').getByRole('button', { name: /^Events/ }).click();
  const delay = page.locator('.newc-event-item').filter({ hasText: /preparation delay/ }).first();
  await expect(delay).toBeVisible({ timeout: 90_000 });
  await expect(delay).not.toContainText('affected orders: none');
  await expect(delay).toContainText(/Meal order/);
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${run}/commits?from=1&limit=4096`)).json();
    const messages = feed.commits.flatMap((commit:any)=>commit.messages);
    const delayed = messages.find((message:any)=>message.schemaId==='lowalt.food.preparation_delayed'&&message.payload.tasks.includes(preparing.payload.task));
    const ready = messages.find((message:any)=>message.schemaId==='lowalt.food.ready'&&message.payload.task===preparing.payload.task);
    const assigned = messages.find((message:any)=>message.schemaId==='lowalt.task.assigned'&&message.payload.task===preparing.payload.task);
    return !!delayed && !!ready && !!assigned && BigInt(ready.at.ns)>=BigInt(preparing.payload.ready_ns)+45_000_000_000n;
  }, { timeout: 180_000 }).toBe(true);
  await shot(page, info, 'delayed-dispatch');
  await page.getByTestId('wb-pause').click();
  await expect.poll(() => status(page), { timeout: 30_000 }).toBe('paused');
  await delay.getByRole('button', { name: 'Why did this happen?', exact: true }).click();
  await expect(page.getByTestId('cause-trace')).toBeVisible();
  await expect(page.getByTestId('wb-history')).toBeVisible();
  await expect(page.getByTestId('inject-restaurant_delay')).toBeDisabled();
  await page.getByTestId('wb-graph-toggle').click();
  const cut = await page.locator('.behaviour-run-graph').getAttribute('data-cut');
  await expect(page.getByTestId('wb-inspector')).toContainText(`journal cut #${cut}`);
  await shot(page, info, 'historical-cut');
  // The cause panel currently lacks the input → domain-engine lineage. Keep
  // that unmet acceptance item visible without inventing a causal assertion.
  info.annotations.push({ type: 'known-product-gap', description: 'Restaurant delay Why? identifies the event but has no causal input or downstream lineage; owner: trace/cause-trace.ts and packs/lowalt/tasks.py.' });
  await page.getByTestId('wb-graph-toggle').click();
  await page.getByTestId('wb-back-live').click();
  await stop(page);
  await deriveAndCompare(page, run, info);
  expect(errors).toEqual([]);
});

for (const template of TEMPLATES.filter(id => id !== 'food-delivery')) {
  test(`${template}: held world, single start, pause, speed, replay, derive and compare`, async ({ page }, info) => {
    test.setTimeout(template === 'city-day' ? 480_000 : template === 'traffic-accident' ? 420_000 : 300_000);
    page.on('dialog', dialog => dialog.accept());
    const errors = watchErrors(page), writes: string[] = [];
    page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
    await createDraft(page, template);
    const authoredOperator=['security-patrol','traffic-accident','city-day'].includes(template);
    if(authoredOperator) {
      await operatorFrontier(page,'0');
      const timeout=page.getByTestId('wb-config').locator('[data-path="/ingress_streams/0/timeout_s"] input');
      await timeout.fill('120');await timeout.press('Tab');
    }
    await initialize(page);
    const run = identity(page);
    // API pacing never controls playback. The selector currently omits 50×.
    for (const factor of [0.5, 5, 50, null]) {
      const response = await page.request.post(`/v1/runs/${run}/speed`, { data: { factor } });
      expect(response.ok()).toBe(true);expect((await response.json()).factor).toBe(factor);
    }
    await page.request.post(`/v1/runs/${run}/speed`, { data: { factor: 5 } });
    await shot(page, info, 'initialized');
    await page.getByTestId('wb-start').click();
    await expect.poll(() => status(page), { timeout: 60_000 }).toMatch(/running|waiting_for_input|completed/);
    if (!authoredOperator && await page.getByTestId('wb-pause').isVisible()) {
      await page.getByTestId('wb-pause').click();await expect.poll(() => status(page)).toMatch(/paused|completed/);
    }
    const selectedId = await selectDrone(page);
    if(authoredOperator)await injectAuthored(page,template,info);
    await openGraph(page, selectedId);
    await shot(page, info, 'selected-graph');await page.getByTestId('wb-graph-toggle').click();
    await stop(page);
    // Opening a finished run and using playback must send no runtime writes.
    await page.getByTestId('wb-records-toggle').click();
    await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:run}).getByTestId('wb-open-run').click();
    const beforeReplay = writes.length;
    await page.getByTestId('wb-playback').click();await page.getByTestId('wb-playback').click();
    expect(writes.slice(beforeReplay)).toEqual([]);expect(identity(page)).toBe(run);
    await deriveAndCompare(page, run, info);
    expect(errors).toEqual([]);
  });
}

test('refresh restores the same initialized world without control writes', async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page), writes: string[] = [];
  page.on('request', request => { if (request.method()==='POST') writes.push(request.url()); });
  await createDraft(page, 'food-delivery');
  await initialize(page);
  const run = identity(page), before = writes.length;
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 90_000 });
  await expect.poll(()=>status(page), { timeout: 60_000 }).toBe('initialized');
  expect(identity(page)).toBe(run);
  expect(writes.slice(before)).toEqual([]);
  await expect(page.locator('[data-testid="viewport"][data-rendered="true"]')).toBeVisible({ timeout: 90_000 });
  await shot(page, info, 'restored-initial-world');
  expect(errors).toEqual([]);
});

test('pause and resume survive refresh without restarting or resubmitting', async ({ page }, info) => {
  test.setTimeout(180_000);
  const errors = watchErrors(page), writes: string[] = [];
  page.on('dialog', dialog => dialog.accept());
  page.on('request', request => { if (request.method() === 'POST') writes.push(request.url()); });
  // Use a real pinned interactive world; source frontier zero keeps the
  // simulation held while the browser proves lifecycle controls and refresh.
  const workspace = (await (await page.request.post('/v1/templates/food-delivery/workspace', {
    data: { knobs: { run_mode: 'interactive' } },
  })).json()).workspace_id;
  expect((await (await page.request.post(`/v1/studio/workspaces/${workspace}/validate`, { data: {} })).json()).valid).toBe(true);
  const revision = (await (await page.request.get(`/v1/studio/workspaces/${workspace}`)).json()).revision;
  expect((await page.request.post(`/v1/studio/workspaces/${workspace}/versions`, { data: { revision } })).status()).toBe(201);
  const run = (await (await page.request.post('/v1/runs', { data: { studio_workspace: workspace, version: 1 } })).json()).id;
  await page.goto(`/?run=${encodeURIComponent(run)}&mode=live`);
  await expect.poll(() => status(page), { timeout: 60_000 }).toBe('initialized');
  await expect(page.locator('[data-testid="viewport"][data-rendered="true"]')).toBeVisible({ timeout: 90_000 });
  await page.getByTestId('wb-start').click();
  await expect.poll(() => status(page)).toBe('waiting_for_input');
  await page.getByTestId('wb-pause').click();
  await expect.poll(() => status(page)).toBe('paused');
  await selectDrone(page);
  await page.getByRole('tab', { name: 'Operate', exact: true }).click();
  await page.getByLabel('Payload.restaurant', { exact: true }).selectOption('restaurant-east');
  await page.getByLabel('Payload.delay_s', { exact: true }).fill('45');
  const admitted = page.waitForResponse(reply => reply.url().endsWith(`/v1/runs/${run}/ingress`));
  await page.getByTestId('inject-restaurant_delay').click();
  const admission = await (await admitted).json();
  expect(admission.disposition).toBe('accepted');
  const before = writes.length;
  await page.reload({ waitUntil: 'domcontentloaded', timeout: 90_000 });
  await expect.poll(() => status(page), { timeout: 60_000 }).toBe('paused');
  expect(identity(page)).toBe(run);
  expect(writes.slice(before)).toEqual([]);
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(page.getByTestId('wb-resume')).toContainText('恢复');
  await page.getByTestId('wb-resume').click();
  await expect.poll(() => status(page)).toBe('waiting_for_input');
  await page.getByRole('button', { name: '切换到英文', exact: true }).click();
  expect(writes.filter(url => url.endsWith(`/v1/runs/${run}/start`))).toHaveLength(1);
  expect(writes.filter(url => url.endsWith(`/v1/runs/${run}/resume`))).toHaveLength(1);
  expect(writes.filter(url => url.endsWith(`/v1/runs/${run}/ingress`))).toHaveLength(1);
  await selectDrone(page);
  await page.getByRole('tab', { name: 'Operate', exact: true }).click();
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${run}/commits?from=1&limit=4096`)).json();
    const receipts = feed.commits.flatMap((commit:any) => commit.receipts);
    return receipts.filter((receipt:any) => receipt.commandId === admission.command_id && receipt.status === 'succeeded').length;
  }, { timeout: 30_000 }).toBe(1);
  await expect(page.locator('[data-testid="viewport"][data-rendered="true"]')).toBeVisible({ timeout: 90_000 });
  await shot(page, info, 'resumed-same-world');
  await stop(page);
  expect(errors).toEqual([]);
});

test('earlier addresses land in the same workbench', async ({ page }) => {
  await page.goto('/studio');await expect(page.getByTestId('wb-gallery')).toBeVisible();
  await page.goto('/runs');await expect(page.getByTestId('wb-records')).toBeVisible();
});

test('operator input belongs to the live session across State, refresh, history and replay', async ({ page }, info) => {
  test.setTimeout(480_000);
  const errors = watchErrors(page); page.on('dialog', dialog => dialog.accept());
  await createDraft(page, 'food-delivery'); await operatorFrontier(page, '0');
  const config = page.getByTestId('wb-config');
  await config.locator('[data-path="/run/until_ns"] input').fill('60000000000');
  await initialize(page); const run = identity(page), liveURL = page.url();
  await selectDrone(page);
  await expect(page.getByRole('tab', { name: 'State', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.getByTestId('wb-start').click();
  const input = async () => (await (await page.request.get(`/v1/runs/${run}/inputs`)).json()).watermarks.operator as number;
  await expect.poll(input, { timeout: 20_000 }).toBeGreaterThan(0);
  const beforeRefresh = await input(); await page.reload();
  await expect.poll(input, { timeout: 30_000 }).toBeGreaterThan(beforeRefresh);
  // Pause promptly; screenshot rendering must not consume the whole input horizon.
  await expect(page.getByTestId('wb-pause')).toBeVisible({ timeout: 90_000 });
  await page.getByTestId('wb-pause').click(); await expect.poll(() => status(page), { timeout: 30_000 }).toBe('paused');
  await expect(page.getByRole('tab', { name: 'State', exact: true })).toBeVisible({ timeout: 90_000 });
  await shot(page, info, 'input-producing-after-state-refresh');
  const paused = await input(); await page.waitForTimeout(2200); expect(await input()).toBe(paused);
  const writes: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && /\/(watermark|ingress|pause|resume|stop)$/.test(request.url())) writes.push(request.url()); });
  await page.goto(liveURL + '&cut=2'); await expect(page.getByTestId('wb-history')).toBeVisible({ timeout: 90_000 });
  await page.waitForTimeout(2200); expect(writes).toEqual([]); expect(await input()).toBe(paused);
  await shot(page, info, 'restored-history-readonly');
  await page.goto(liveURL.replace('mode=live', 'mode=replay') + '&cut=2');
  await expect(page.getByTestId('wb-start')).toHaveCount(0); await page.waitForTimeout(2200); expect(writes).toEqual([]);
  await page.goto(liveURL); await expect(page.getByTestId('wb-resume')).toBeVisible({ timeout: 90_000 });
  await page.getByTestId('wb-resume').click();
  await expect.poll(() => status(page), { timeout: 90_000 }).toBe('completed');
  await shot(page, info, 'input-produced-through-authored-horizon');
  await page.reload(); await expect.poll(() => status(page), { timeout: 90_000 }).toBe('completed');
  await expect(page.getByTestId('wb-speed')).toHaveCount(0); expect(errors).toEqual([]);
});

test('records without a project give a useful next step and graph loading is bilingual', async ({ page }, info) => {
  const errors = watchErrors(page);
  await page.goto('/?records=1'); await expect(page.getByTestId('wb-records')).toBeVisible();
  await expect(page.getByText('Loading project…', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Open a run from the list, or choose a scenario to create a project.')).toBeVisible();
  await shot(page, info, 'records-no-project');
  await createDraft(page, 'food-delivery'); await initialize(page);
  let release!: () => void;
  const loading = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/BehaviourRunGraph-*.js', async route => { await loading; await route.continue(); });
  await page.getByTestId('wb-graph-toggle').click(); await expect(page.getByTestId('wb-graph-loading')).toHaveText('Loading relation graph…');
  await shot(page, info, 'graph-loading-en');
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(page.getByTestId('wb-graph-loading')).toHaveText('正在加载关系图…'); await shot(page, info, 'graph-loading-zh');
  release(); await expect(page.locator('.behaviour-run-graph')).toBeVisible({ timeout: 90_000 }); expect(errors).toEqual([]);
});

test('unsaved coordinates redraw the draft preview and survive closing configuration', async ({ page }, info) => {
  const errors = watchErrors(page); await createDraft(page, 'food-delivery');
  const config = page.getByTestId('wb-config'); await config.getByRole('button', { name: 'Objects', exact: true }).click();
  const workspace = new URL(page.url()).pathname.split('/').at(-1)!;
  const original = await (await page.request.get(`/v1/studio/workspaces/${workspace}`)).json();
  const index = original.scenario.entities.findIndex((row: any) => Array.isArray(row.facts?.['lowalt.site_position'])); expect(index).toBeGreaterThanOrEqual(0);
  const actor = original.scenario.entities[index], position = actor.facts['lowalt.site_position'];
  await config.getByRole('textbox', { name: 'Filter instances' }).fill(actor.id);
  await config.getByRole('button', { name: 'Edit', exact: true }).click();
  const coordinate = config.locator(`[data-path="/entities/${index}/facts/lowalt.site_position"] input`);
  await expect(page.getByTestId('draft-preview')).toHaveAttribute('data-rendered', 'true', { timeout: 120_000 });
  await shot(page, info, 'draft-before-unsaved-coordinate');
  await coordinate.fill(`${position[0] + 500}, ${position[1]}, ${position[2]}`);
  const markers = async () => JSON.parse((await page.getByTestId('draft-preview').getAttribute('data-marker-positions'))!);
  await expect.poll(async () => (await markers()).find((row: any) => row.id === actor.id)?.position[0]).toBe(position[0] + 500);
  const server = await (await page.request.get(`/v1/studio/workspaces/${workspace}`)).json();
  expect(server.revision).toBe(original.revision); expect(server.scenario.entities[index].facts['lowalt.site_position']).toEqual(position);
  await expect(page.locator('.wb-stage-note')).toContainText('Unsaved draft preview'); await shot(page, info, 'draft-after-unsaved-coordinate');
  await config.getByRole('button', { name: 'Close configuration', exact: true }).click();
  await expect.poll(async () => (await markers()).find((row: any) => row.id === actor.id)?.position[0]).toBe(position[0] + 500);
  expect(errors).toEqual([]);
});

test('simulation-speed operator input respects kernel requests, refresh and read-only history', async ({ page }, info) => {
  test.setTimeout(420_000); const errors=watchErrors(page);page.on('dialog',d=>d.accept());
  await createDraft(page,'food-delivery');await page.getByTestId('knob-run_mode').click();await page.locator('.ant-select-item-option').filter({hasText:/^interactive$/}).click();await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();await expect(page.getByTestId('wb-config')).toContainText('Controls applied to the draft.');await operatorFrontier(page,'0');
  await page.getByTestId('wb-config').locator('[data-path="/run/until_ns"] input').fill('120000000000');
  await page.getByTestId('wb-config').getByRole('button',{name:'Scene & space',exact:true}).click();
  await page.getByTestId('wb-config').getByLabel('City',{exact:true}).selectOption('none');
  await initialize(page);const run=identity(page),live=page.url();
  const source=async()=>Number((await(await page.request.get(`/v1/runs/${run}/inputs`)).json()).watermarks.operator);
  let requested=0;const audit:Array<{watermark:number;requested:number}>=[];
  // Record the real requested boundary before releasing the response to the
  // app. An asynchronous response listener can otherwise lag its next POST.
  await page.route('**/commits?from=1&limit=1',async route=>{const response=await route.fetch(),value=await response.json();if(value.waiting?.stream_ids.includes('operator'))requested=Number(value.waiting.at_ns);await route.fulfill({response});});
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/watermark'))audit.push({watermark:request.postDataJSON().watermark_ns,requested});});
  await page.getByTestId('wb-speed').selectOption('5');await page.getByTestId('wb-start').click();
  await expect.poll(source,{timeout:60_000}).toBeGreaterThan(2_000_000_000);
  await page.getByTestId('wb-pause').click();await expect.poll(()=>status(page),{timeout:60_000}).toBe('paused');
  const slow=await source();await page.getByTestId('wb-speed').selectOption('50');await page.getByTestId('wb-resume').click();
  const start=Date.now();await expect.poll(source,{timeout:60_000}).toBeGreaterThan(slow+10_000_000_000);
  await page.getByTestId('wb-pause').click();await expect.poll(()=>status(page),{timeout:60_000}).toBe('paused');
  const evidence={slow,fast:await source(),wall_ms:Date.now()-start,audit},auditPath=info.outputPath('speed-and-boundary-audit.json');await writeFile(auditPath,JSON.stringify(evidence,null,2));await info.attach('speed-and-boundary-audit',{path:auditPath,contentType:'application/json'});
  expect(audit.length).toBeGreaterThan(5);expect(audit.every(row=>row.watermark<=row.requested)).toBe(true);expect(new Set(audit.map(row=>row.watermark)).size).toBe(audit.length);
  const paused=await source();await page.reload();await expect.poll(()=>status(page),{timeout:90_000}).toBe('paused');await page.waitForTimeout(1200);expect(await source()).toBe(paused);
  const writes:string[]=[];page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/watermark'))writes.push(request.url());});
  await page.goto(live+'&cut=2');await expect(page.getByTestId('wb-history')).toBeVisible();await page.waitForTimeout(1200);expect(writes).toEqual([]);
  await page.goto(live.replace('mode=live','mode=replay')+'&cut=2');await expect(page.getByTestId('workbench')).toHaveAttribute('data-mode','replay');await expect(page.getByTestId('wb-pause')).toHaveCount(0);await page.waitForTimeout(1200);expect(writes).toEqual([]);
  await page.goto(live);await expect(page.getByTestId('wb-resume')).toBeVisible({timeout:90_000});await page.getByTestId('wb-speed').selectOption('max');await page.getByTestId('wb-resume').click();
  await expect.poll(()=>status(page),{timeout:180_000}).toBe('completed');await shot(page,info,'max-input-through-authored-horizon');expect(await source()).toBe(120_000_000_000);expect(audit.every(row=>row.watermark<=row.requested)).toBe(true);expect(new Set(audit.map(row=>row.watermark)).size).toBe(audit.length);await writeFile(auditPath,JSON.stringify({...evidence,completed:await source(),audit,errors},null,2));expect(errors).toEqual([]);
});

test('pause and stop wait for pending live input confirmations without source errors',async({page},info)=>{
 test.setTimeout(240_000);const errors=watchErrors(page);page.on('dialog',dialog=>dialog.accept());await createDraft(page,'food-delivery');
 await page.getByTestId('knob-run_mode').click();await page.locator('.ant-select-item-option').filter({hasText:/^interactive$/}).click();await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();await expect(page.getByTestId('wb-config')).toContainText('Controls applied to the draft.');await initialize(page);
 let release!:()=>void,started=false;const pending=new Promise<void>(resolve=>{release=resolve;}),controls:string[]=[],watermarks:string[]=[];
 await page.route('**/watermark',async route=>{started=true;await pending;await route.continue();});
 page.on('request',request=>{if(request.method()!=='POST')return;if(request.url().endsWith('/pause'))controls.push(request.url());if(request.url().endsWith('/watermark'))watermarks.push(request.url());});
 await page.getByTestId('wb-speed').selectOption('max');await page.getByTestId('wb-start').click();await expect.poll(()=>started,{timeout:60_000}).toBe(true);
 await page.getByTestId('wb-pause').click();await expect(page.getByTestId('wb-pause')).toBeDisabled();await page.waitForTimeout(200);expect(controls).toEqual([]);await shot(page,info,'pause-waits-for-input-confirmation');
 release();await expect.poll(()=>status(page),{timeout:60_000}).toBe('paused');expect(controls).toHaveLength(1);const count=watermarks.length;await page.waitForTimeout(1200);expect(watermarks).toHaveLength(count);await expect(page.getByText(/Operator input source unavailable/)).toHaveCount(0);await shot(page,info,'paused-without-input-errors');
 await page.getByTestId('wb-resume').click();await expect.poll(()=>watermarks.length,{timeout:60_000}).toBeGreaterThan(count);await page.getByTestId('wb-stop').click();await expect.poll(()=>status(page),{timeout:60_000}).toBe('stopped');expect(errors).toEqual([]);
});

test('comparison shows results beside compact source locations and retains their exact values',async({page},info)=>{
 test.setTimeout(240_000);const errors=watchErrors(page);
 const post=async(path:string,data:unknown={})=>{const response=await page.request.post(path,{data});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const freeze=async(workspace:string)=>{const project=await(await page.request.get(`/v1/studio/workspaces/${workspace}`)).json();expect((await post(`/v1/studio/workspaces/${workspace}/validate`,{revision:project.revision})).valid).toBe(true);const version=await post(`/v1/studio/workspaces/${workspace}/versions`,{revision:project.revision});const run=await post('/v1/runs',{studio_workspace:workspace,version:version.version,autostart:false});await post(`/v1/runs/${run.id}/stop`);await expect.poll(async()=>{const rows=await(await page.request.get('/v1/runs?include_kpis=false')).json();return rows.find((r:any)=>r.id===run.id)?.status;},{timeout:60_000}).toBe('stopped');return run.id;};
 const original=await post('/v1/templates/food-delivery/workspace',{knobs:{run_mode:'interactive'}}),first=await freeze(original.workspace_id);
 const derived=await post(`/v1/runs/${first}/derive-workspace`),project=await(await page.request.get(`/v1/studio/workspaces/${derived.workspace_id}`)).json();
 project.scenario.run.seed=43;await post(`/v1/studio/workspaces/${project.id}`,{scenario:project.scenario,revision:project.revision});const second=await freeze(project.id);
 await page.goto(`/?run=${first}&mode=replay&records=1`);await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true',{timeout:90_000});await expect(page.getByTestId('wb-records')).toBeVisible();
 await page.getByTestId('wb-records').getByRole('button',{name:'All runs',exact:true}).click();for(const id of [first,second])await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:id}).getByRole('checkbox').check();await page.getByTestId('wb-compare').click();
 const comparison=page.locator('.newc-compare');await expect(comparison.getByTestId('comparison-configuration')).toContainText('/run/seed');await expect(comparison.locator('.newc-config-source').first()).toBeVisible();
 for(const width of [1280,1920]){await page.setViewportSize({width,height:width===1280?800:1080});for(const zh of [false,true]){if((await page.locator('html').getAttribute('lang')==='zh-CN')!==zh)await page.getByRole('button',{name:zh?'Switch to Chinese':'切换到英文',exact:true}).click();await expect(comparison.locator('.newc-compare-col h3').first()).toBeInViewport({ratio:1});expect((await page.locator('.wb-overlay-card').boundingBox())!.y).toBeLessThan(160);await shot(page,info,`compact-comparison-${width}-${zh?'zh':'en'}`);}}
 const source=comparison.locator('.newc-config-source').first(),full=await source.locator('summary').getAttribute('title');expect(full!.length).toBeGreaterThan(96);await source.locator('summary').click();await expect(source.locator('code')).toHaveText(JSON.stringify(full));await expect(source.locator('code')).toBeVisible();await shot(page,info,'comparison-exact-source-expanded');expect(errors).toEqual([]);
});

test('camera controls stay clickable beside compact and selected inspectors with the dock open', async ({ page }, info) => {
  const errors = watchErrors(page);
  await createDraft(page, 'food-delivery');
  await initialize(page);
  await page.getByTestId('wb-dock').getByRole('button', { name: 'Results', exact: true }).click();
  const camera = page.getByTestId('wb-camera'), inspector = page.getByTestId('wb-inspector');
  const check = async (name: string) => {
    const pad = (await camera.boundingBox())!, panel = (await inspector.boundingBox())!;
    expect(pad.x + pad.width).toBeLessThanOrEqual(panel.x);
    const covered = await camera.locator('button:enabled').evaluateAll(buttons => buttons.flatMap(button => {
      const rect = button.getBoundingClientRect(), hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return hit?.closest('button') === button ? [] : [button.getAttribute('title')];
    }));
    expect(covered).toEqual([]);
    await camera.getByTitle('Zoom out', { exact: true }).click();
    await camera.getByTitle('Reset view', { exact: true }).click();
    await expect.poll(() => status(page)).toBe('initialized');
    await shot(page, info, name);
  };
  await expect(inspector).toHaveAttribute('data-compact', 'true');
  for (const [width, height] of [[1280, 800], [1600, 1000], [1920, 1080]]) {
    await page.setViewportSize({ width, height });
    await check(`camera-compact-${width}-en`);
  }
  await page.getByRole('textbox', { name: 'Search objects', exact: true }).fill('restaurant-east');
  await page.getByTestId('wb-objects').locator('.wb-row').filter({ hasText: 'restaurant-east' }).first().click();
  await expect(inspector).toHaveAttribute('data-compact', 'false');
  for (const [width, height] of [[1280, 800], [1600, 1000], [1920, 1080]]) {
    await page.setViewportSize({ width, height });
    await check(`camera-selected-${width}-en`);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(inspector).toContainText('站点位置');
  await expect(inspector).toContainText('站点开放');
  await expect(inspector).toContainText('站点容量');
  await camera.getByTitle('缩小', { exact: true }).click();
  await camera.getByTitle('重置视角', { exact: true }).click();
  await shot(page, info, 'camera-selected-1280-zh');
  expect(errors).toEqual([]);
});
