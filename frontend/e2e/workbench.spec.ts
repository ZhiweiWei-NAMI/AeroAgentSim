import { expect, test, type Page, type TestInfo } from '@playwright/test';

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
    for(const key of ['task','origin','destination'])await page.getByLabel(`Payload.${key}`,{exact:true}).fill(key==='task'?task.id:task.facts[`lowalt.task_${key}`]);
  } else if(template==='city-day') {
    point='close-vertiport';
    await page.getByRole('button',{name:'Inject close vertiport',exact:true}).click();
    const site=configuration.scenario.entities.find((entity:any)=>entity.type==='lowalt:Vertiport'&&entity.id==='east-port');
    expect(site).toBeTruthy();await page.getByLabel('Payload.site',{exact:true}).fill(site.id);
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
  // Serialized inspector control shares the source-progress queue.
  await page.getByRole('button',{name:'Pause simulation',exact:true}).click();
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
  test.setTimeout(300_000);
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
  await page.getByLabel('Payload.restaurant', { exact: true }).fill(preparing.payload.restaurant);
  await page.getByLabel('Payload.delay_s', { exact: true }).fill('45');
  await expect(page.getByTestId('inject-restaurant_delay')).toBeEnabled();
  const admitted = page.waitForResponse(response => response.url().endsWith('/ingress'));
  await page.getByTestId('inject-restaurant_delay').click();
  expect((await (await admitted).json()).disposition).toBe('accepted');
  await expect(page.getByTestId('wb-inspector')).toContainText('Admission receipts · 1');
  await shot(page, info, 'delay-receipt');
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
  }, { timeout: 90_000 }).toBe(true);
  await shot(page, info, 'delayed-dispatch');
  await page.getByRole('button',{name:'Pause simulation',exact:true}).click();
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
  await page.getByLabel('Payload.restaurant', { exact: true }).fill('restaurant-east');
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
