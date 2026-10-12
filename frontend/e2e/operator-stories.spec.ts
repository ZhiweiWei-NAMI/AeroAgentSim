import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { TemporalFeedStore } from '../src/feeds/temporal-store';
import { explain, type CauseRef } from '../src/trace/cause-trace';

const stories = [
  { template: 'parcel-delivery', point: 'change-dropoff', selected: 'task-1', fields: { task: 'task-1:0', destination: 'customer-2:0' }, effect: /drop-off changed/, next: /Task (Assigned|Completed)/, size: [1280, 800] },
  { template: 'medical-delivery', point: 'prioritize-medical', selected: 'urgent-2', fields: { task: 'urgent-2:0', priority: '200' }, effect: /Urgent request raised/, next: /Task Assigned/, size: [1600, 1000] },
  { template: 'powerline-inspection', point: 'report-defect', selected: 'line-end', fields: { task: 'line-survey-2:0', site: 'line-end:0' }, effect: /Defect reported/, next: /Inspection Observed/, size: [1920, 1080] },
  { template: 'crop-spraying', point: 'pause-spraying', selected: 'field-1', fields: { task: 'field-1:0', duration_s: '20' }, effect: /Operator paused spraying/, next: /Spraying Resumed/, size: [1280, 800] },
  { template: 'air-taxi', point: 'close-vertiport', selected: 'east-port', fields: { site: 'east-port:0' }, effect: /Operator closed/, next: /Task (Replanned|Assigned)/, size: [1600, 1000] },
  { template: 'security-patrol', point: 'investigate', selected: 'investigation-1', fields: { task: 'investigation-1', origin: 'home', destination: 'incident-site' }, effect: /New .*investigation|New .*response|New .*incident/i, next: /Task (Assigned|Completed)/, size: [1920, 1080] },
  { template: 'city-day', point: 'close-vertiport', selected: 'east-port', fields: { site: 'east-port' }, effect: /Operator closed/, next: /Task (Replanned|Assigned)/, size: [1600, 1000] },
] as const;
const runId = (page: Page) => new URL(page.url()).searchParams.get('run')!;
const status = (page: Page) => page.getByTestId('workbench').getAttribute('data-status');
const errors = new WeakMap<Page, string[]>();
const httpErrors = new WeakMap<Page, string[]>();

test.use({ launchOptions: { executablePath: process.env.AEROAGENTSIM_CHROMIUM, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'] } });
test.beforeEach(({ page }) => {
  page.setDefaultTimeout(30_000); page.setDefaultNavigationTimeout(90_000);
  const rows: string[] = []; errors.set(page, rows);
  const requests: string[] = []; httpErrors.set(page, requests);
  page.on('response', async response => {
    if (response.status() >= 400) requests.push(`${response.status()} ${response.request().method()} ${response.url()} ${await response.text().catch(String)}`);
  });
  page.on('pageerror', error => rows.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/favicon|ERR_ABORTED/.test(message.text())) rows.push(message.text()); });
});
test.afterEach(async ({ page }, info) => {
  await info.attach('browser-errors', { body: JSON.stringify(errors.get(page)), contentType: 'application/json' });
  await info.attach('http-errors', { body: JSON.stringify(httpErrors.get(page)), contentType: 'application/json' });
  if (info.status !== info.expectedStatus && !page.isClosed()) await shot(page, info, 'failure');
  const id = runId(page);
  if (id && !['completed','stopped'].includes(await status(page) ?? '')) await page.request.post(`/v1/runs/${id}/stop`);
});
async function shot(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(name + '.png'); await page.screenshot({ path }); await info.attach(name, { path, contentType: 'image/png' });
}
async function createInteractive(page: Page, template: string) {
  await page.goto('/?gallery=1'); await page.getByTestId(`wb-template-${template}`).click(); await expect(page.getByTestId('wb-guide')).toBeVisible();
  await page.getByTestId('wb-create-project').click(); await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
  await page.getByTestId('knob-run_mode').click(); await page.locator('.ant-select-item-option').filter({ hasText: /^interactive$/ }).click();
  if (template === 'traffic-accident') await page.getByTestId('knob-accident_time_s').fill('30');
  await page.getByRole('button', { name: 'Apply controls to draft', exact: true }).click();
  await page.getByTestId('wb-validate').click(); await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
  await page.getByTestId('wb-save-version').click(); await expect(page.getByTestId('wb-initialize')).toBeEnabled({ timeout: 60_000 });
  await page.getByTestId('wb-initialize').click(); await expect.poll(() => status(page), { timeout: 90_000 }).toBe('initialized');
  await expect(page.locator('[data-testid="viewport"][data-rendered="true"]')).toBeVisible({ timeout: 90_000 });
  await page.getByTestId('wb-start').click(); await expect.poll(() => status(page), { timeout: 60_000 }).toMatch(/running|waiting_for_input/);
  await page.getByTestId('wb-speed').selectOption(template === 'traffic-accident' ? '0.5' : '1');
}
async function select(page: Page, id: string) {
  await page.getByRole('textbox', { name: 'Search objects', exact: true }).fill(id);
  const row = page.getByTestId('wb-objects').locator('.wb-row').filter({ hasText: id }).first();
  await row.click(); await expect(row).toHaveAttribute('aria-current', 'true');
  await page.getByRole('tab', { name: 'Operate', exact: true }).click();
}
async function inject(page: Page, point: string, fields: Record<string, string>) {
  const form = page.getByTestId('inject-' + point);
  if (!(await form.count())) await page.getByRole('button', { name: 'Inject ' + point.replace(/[-_]/g, ' '), exact: true }).click();
  for (const [key, value] of Object.entries(fields)) {
    const input = page.getByLabel('Payload.' + key, { exact: true });
    if (await input.evaluate(element => element.tagName === 'SELECT')) await input.selectOption(value); else await input.fill(value);
  }
  await expect(form).toBeEnabled();
  const response = page.waitForResponse(reply => reply.url().endsWith('/ingress'));
  await form.click(); const admission = await (await response).json(); expect(admission.disposition).toBe('accepted');
  await expect(page.getByTestId('wb-inspector')).toContainText(/Admission receipts/);
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${runId(page)}/commits?from=1&limit=4096`)).json();
    return feed.commits.some((commit: any) => commit.receipts.some((receipt: any) => receipt.commandId === admission.command_id && receipt.status === 'succeeded'));
  }, { timeout: 60_000 }).toBe(true);
  await page.getByTestId('wb-inspector').locator('summary').filter({ hasText: /^Admission receipts/ }).scrollIntoViewIfNeeded();
  return admission;
}
async function closeOperatorInterval(page: Page) {
  // Each test submits its only operator input through the UI. Explicitly close
  // the declared source afterward so completion does not depend on the
  // live producer continuing through the rest of the scenario horizon.
  const configuration = await (await page.request.get(`/v1/studio/runs/${runId(page)}/configuration`)).json();
  const horizon = configuration.scenario.run.until_ns; expect(Number.isSafeInteger(horizon)).toBe(true);
  const closed = await page.request.post(`/v1/runs/${runId(page)}/watermark`, { data: { stream_id: 'operator', watermark_ns: horizon } });
  expect(closed.ok()).toBe(true);
}
async function why(page: Page, effect: RegExp) {
  await page.getByTestId('wb-dock').getByRole('button', { name: /^Events\b/ }).click();
  const event = page.getByTestId('wb-dock').locator('.newc-event-item').filter({ hasText: effect }).first();
  await expect(event).toBeVisible({ timeout: 90_000 });
  await event.getByRole('button', { name: 'Why did this happen?', exact: true }).click();
  await expect(page.getByTestId('cause-trace')).toBeVisible();
  await expect(page.getByTestId('cause-trace').locator('.ct-title')).toBeInViewport();
  if (await page.getByTestId('cause-trace').locator('.ct-message-story').count()) await expect(page.getByTestId('cause-trace').locator('.ct-next h4')).toBeInViewport();
  await expect(page.getByTestId('cause-trace')).not.toContainText('no linked chain step');
}
for (const story of stories) test(`${story.template}: ${story.point} has a receipt and a recorded effect in Why`, async ({ page }, info) => {
  await page.setViewportSize({ width: story.size[0], height: story.size[1] });
  await createInteractive(page, story.template); await select(page, story.selected); await inject(page, story.point, story.fields);
  await shot(page, info, 'receipt');
  await closeOperatorInterval(page);
  await page.getByTestId('wb-speed').selectOption('max');
  if (story.template === 'city-day') {
    // The closure's physical reroute is the acceptance result; the full city
    // horizon includes other business streams after this operator story.
    await expect.poll(async () => {
      const feed = await (await page.request.get(`/v1/runs/${runId(page)}/commits?from=1&limit=4096`)).json();
      return feed.commits.some((commit: any) => commit.messages.some((message: any) => message.schemaId === 'lowalt.task.replanned' && message.payload.provenance));
    }, { timeout: 60_000 }).toBe(true);
  } else await expect.poll(() => status(page), { timeout: 180_000 }).toBe('completed');
  await why(page, story.effect);
  await expect(page.getByTestId('cause-trace')).toContainText(story.next);
  await shot(page, info, 'why-effect');
  const next = page.getByTestId('cause-trace').locator('.ct-follow-btn').filter({ hasText: story.next }).first(); await next.click();
  await expect(page.getByTestId('cause-trace').locator('.ct-trigger').first()).toContainText(/Operator/);
  await shot(page, info, 'why-downstream'); expect(errors.get(page)).toEqual([]);
});

test('traffic-accident: the operator accident walks through recorded dispatch to the stored photo', async ({ page }, info) => {
  test.setTimeout(420_000);
  await page.setViewportSize({ width: 1920, height: 1080 });
  await createInteractive(page, 'traffic-accident'); await select(page, 'incident.01');
  const admission = await inject(page, 'accident', {});
  expect(Number(admission.activation_ns), 'the operator event must precede the authored accident timer').toBeLessThan(30_000_000_000);
  await shot(page, info, 'traffic-receipt');
  await page.getByTestId('wb-speed').selectOption('max');
  await closeOperatorInterval(page);
  const header = await (await page.request.get(`/v1/runs/${runId(page)}/header`)).json();
  await expect.poll(() => status(page), { timeout: 180_000 }).toBe('completed');
  const store = new TemporalFeedStore(header);
  for (let cursor = 1;;) {
    const feed = await (await page.request.get(`/v1/runs/${runId(page)}/commits?from=${cursor}&limit=4096`)).json();
    feed.commits.forEach((commit: any) => store.ingest(commit)); if (!feed.commits.length) break; cursor = feed.next;
  }
  store.seek(store.commits.at(-1)!.at.ns);
  const stored = store.commits.flatMap(commit => commit.messages).find(message => message.schemaId === 'traffic.capture.stored'); expect(stored).toBeDefined();
  const activated = store.commits.flatMap(commit => (commit.events ?? []).map(event => ({ event, cut: commit.commitIndex }))).find(row => row.event.text === 'Accident activated')!;
  type Hop = { ref: CauseRef; heading: string; slot?: 'followUp' | 'trigger' | 'sender' | 'choice'; label?: string };
  const initial: CauseRef = { kind: 'event', cut: activated.cut, text: activated.event.text, entities: activated.event.entities };
  expect(explain(store, initial)?.step?.stepId, 'the recorded accident must come from the operator event').toBe('inject');
  const queue: Array<{ ref: CauseRef; path: Hop[]; slot?: Hop['slot']; label?: string }> = [{ ref: initial, path: [] }], seen = new Set<string>();
  let path: Hop[] | undefined;
  while (queue.length) {
    const node = queue.shift()!, key = JSON.stringify(node.ref); if (seen.has(key)) continue; seen.add(key);
    const model = explain(store, node.ref); if (!model) continue;
    const hops = [...node.path, { ref: node.ref, heading: model.heading, slot: node.slot, label: node.label }];
    if (model.step?.chainId === 'traffic.capture_execute' && model.step.toState === 'completed') { path = hops; break; }
    for (const item of model.followUps) if (item.ref) queue.push({ ref: item.ref, path: hops, slot: 'followUp', label: item.label });
    if (model.trigger?.ref) queue.push({ ref: model.trigger.ref, path: hops, slot: 'trigger' });
    if (model.sender?.ref) queue.push({ ref: model.sender.ref, path: hops, slot: 'sender' });
    for (const item of model.choices) queue.push({ ref: item.ref, path: hops, slot: 'choice', label: item.label });
  }
  expect(path, 'recorded links must connect the accident to physical capture completion').toBeDefined();
  await info.attach('recorded-lineage-path', { body: JSON.stringify(path), contentType: 'application/json' });
  await why(page, /Accident activated/); await shot(page, info, 'why-traffic-accident');
  for (const hop of path!.slice(1)) {
    const trace = page.getByTestId('cause-trace');
    if (hop.slot === 'followUp') await trace.locator('.ct-follow-btn').filter({ hasText: hop.label }).first().click();
    else if (hop.slot === 'choice') await trace.locator('.ct-choices .ct-link').filter({ hasText: hop.label }).first().click();
    else if (hop.slot === 'sender') await trace.getByRole('button', { name: 'See that step', exact: true }).click();
    else await trace.locator('.ct-trigger .ct-link').filter({ hasText: 'See where it came from' }).first().click();
    await expect(trace.locator('.ct-title')).toHaveText(hop.heading);
    if (hop.heading === 'Capture Execute: Dispatching → Enroute') await shot(page, info, 'why-traffic-dispatch');
  }
  await shot(page, info, 'why-traffic-completed');
  await page.getByTestId('wb-dock').getByRole('button', { name: 'Photos', exact: true }).click();
  await page.getByRole('button', { name: 'Refresh stored artifacts', exact: true }).click();
  await page.getByRole('button', { name: 'Open photo', exact: true }).first().click();
  const photo = page.getByRole('img', { name: 'Stored capture photo', exact: true });
  await photo.scrollIntoViewIfNeeded(); await expect(photo).toBeInViewport();
  await expect(page.getByTestId('wb-history')).toContainText('Viewing history');
  await shot(page, info, 'traffic-photo');
  await page.getByRole('button',{name:'Switch to Chinese',exact:true}).click();
  await expect(page.getByTestId('wb-history')).toContainText('正在查看历史时刻');
  await expect(page.getByTestId('wb-back-live')).toHaveText(/返回实时/);
  await shot(page,info,'traffic-photo-history-zh');
  await page.getByTestId('wb-back-live').click();await expect(page.getByTestId('wb-history')).toBeHidden();
  await expect(page.getByTestId('workbench')).toHaveAttribute('data-status','completed');
  expect(errors.get(page)).toEqual([]);
});

test('food-delivery: delay leads to preparation readiness, dispatch and physical delivery in both languages', async ({ page }, info) => {
  await page.setViewportSize({ width: 1600, height: 1000 }); await createInteractive(page, 'food-delivery');
  await select(page, 'restaurant-east');
  await expect.poll(async () => {
    const feed = await (await page.request.get(`/v1/runs/${runId(page)}/commits?from=1&limit=4096`)).json();
    return feed.commits.some((commit: any) => commit.messages.some((message: any) => message.schemaId === 'lowalt.food.preparation_started' && message.payload.restaurant === 'restaurant-east'));
  }, { timeout: 60_000 }).toBe(true);
  await inject(page, 'restaurant_delay', { restaurant: 'restaurant-east', delay_s: '20' });
  await closeOperatorInterval(page);
  await page.getByTestId('wb-speed').selectOption('max'); await expect.poll(() => status(page), { timeout: 180_000 }).toBe('completed');
  await why(page, /preparation delay/);
  for (const text of [/Food Ready/, /Task Assigned/, /Food Delivered/]) await expect(page.getByTestId('cause-trace')).toContainText(text);
  for (const [width,height] of [[1280,800],[1600,1000],[1920,1080]]) {
    await page.setViewportSize({ width, height });
    await page.getByTestId('cause-trace').locator('.ct-head').scrollIntoViewIfNeeded(); await shot(page, info, `why-food-${width}-en`);
    await page.getByTestId('cause-trace').locator('.ct-next').scrollIntoViewIfNeeded(); await shot(page, info, `why-food-follow-ups-${width}-en`);
  }
  await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
  await expect(page.getByTestId('cause-trace')).toContainText('为什么');
  await shot(page, info, 'why-food-1920-zh'); expect(errors.get(page)).toEqual([]);
});

async function productShot(page:Page,info:TestInfo,name:string,both=false){
  const original=page.viewportSize()!;
  for(const width of both?[1280,1920]:[original.width]){
    await page.setViewportSize({width,height:width===1280?800:1080});
    for(const chinese of [false,true]){
      const toggle=page.getByRole('button',{name:chinese?'Switch to Chinese':'切换到英文',exact:true});if(await toggle.count())await toggle.last().click();
      await shot(page,info,`${name}-${width}-${chinese?'zh':'en'}`);
    }
  }
  await page.getByRole('button',{name:'切换到英文',exact:true}).last().click();await page.setViewportSize(original);
}
async function selectInstance(page:Page,id:string){
  await page.getByRole('textbox',{name:/^(Search objects|搜索对象)$/}).fill(id);
  await expect(page.getByRole('textbox',{name:/^(Search objects|搜索对象)$/})).toHaveValue(id);
  const row=page.getByTestId('wb-objects').locator('.wb-row').filter({hasText:id}).first();await row.click();await expect(row).toHaveAttribute('aria-current','true');
  await expect(page.getByTestId('wb-inspector')).toContainText(id);return row;
}
for(const template of ['food-delivery','traffic-accident'])test(`${template}: product walk with manual selection, live photos, compare and retry in both languages`,async({page},info)=>{
  test.setTimeout(template==='traffic-accident'?1_200_000:600_000);page.on('dialog',d=>d.accept());await page.setViewportSize({width:1280,height:800});
  await page.goto('/?gallery=1');await page.getByTestId('wb-template-'+template).click();await productShot(page,info,'guide');
  await page.getByTestId('wb-create-project').click();await expect(page.getByTestId('wb-config')).toBeVisible({timeout:60_000});
  await page.getByTestId('knob-run_mode').click();await page.locator('.ant-select-item-option').filter({hasText:/^interactive$/}).click();
  if(template==='food-delivery')await page.getByRole('spinbutton',{name:'East kitchen preparation (s)',exact:true}).fill('80');
  if(template==='traffic-accident'){
    // Explicitly apply the template's declared 1 Hz setting. The untouched
    // draft currently retains the source world's 15 Hz physics engines.
    await page.getByTestId('knob-physics_hz').fill('0.5');await page.getByTestId('knob-physics_hz').fill('1');
  }
  await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();await expect(page.getByTestId('wb-config')).toContainText('Controls applied to the draft.');
  if(template==='traffic-accident'){
    // The current traffic camera config names a different asset set from the
    // real capture manifest. Author the matching live asset ID explicitly;
    // retain the default-config failure separately in the product report.
    const assets=await(await page.request.get('/v1/studio/demo-capture-assets')).json();
    await page.getByTestId('wb-config').getByRole('button',{name:'Execution components',exact:true}).click();
    for(const owner of ['capture','capture_bridge']){
      const detail=page.getByTestId('wb-config').locator('details').filter({has:page.locator('summary > b').filter({hasText:new RegExp('^'+owner+'$')})}).first();
      await detail.locator('summary').first().click();
      if(owner==='capture'){const renderer=detail.locator('summary').filter({hasText:'renderer'});if(!await renderer.evaluate(el=>(el.parentElement as HTMLDetailsElement).open))await renderer.click();}
      const path=owner==='capture'?'/engines/capture/config/renderer/asset_digest':'/engines/capture_bridge/config/asset_digest';
      await page.getByTestId('wb-config').locator(`[data-path="${path}"] input`).fill(assets.asset_id);
    }
  }
  await productShot(page,info,'configured');await page.getByTestId('wb-validate').click();await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});
  await page.getByTestId('wb-save-version').click();await expect(page.getByTestId('wb-initialize')).toBeEnabled();await page.getByTestId('wb-initialize').click();
  await expect.poll(()=>status(page),{timeout:90_000}).toBe('initialized');await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true',{timeout:90_000});
  await productShot(page,info,'initialized',true);const original=runId(page);
  await page.getByTestId('wb-speed').selectOption('5');await page.getByTestId('wb-start').click();await expect(page.getByTestId('wb-pause')).toBeVisible();
  const selected=template==='food-delivery'?'restaurant-east':'incident-capture-01';await selectInstance(page,selected);await page.getByRole('tab',{name:'Operate',exact:true}).click();
  if(template==='food-delivery'){
    await expect(page.getByLabel('Payload.restaurant',{exact:true})).toHaveValue('restaurant-east');expect(await page.getByLabel('Payload.restaurant').evaluate(el=>el.tagName)).toBe('SELECT');
    await page.getByLabel('Payload.delay_s',{exact:true}).fill('45');await selectInstance(page,'meal-1');
  }
  const pinned=template==='food-delivery'?'meal-1':'incident-capture-01';
  const button=page.getByTestId('inject-'+(template==='food-delivery'?'restaurant_delay':'accident'));await expect(button).toBeEnabled();
  const response=page.waitForResponse(reply=>reply.url().endsWith('/ingress'));await button.click();expect((await(await response).json()).disposition).toBe('accepted');
  await expect(page.getByTitle('Event director: the camera visits what is happening',{exact:true})).toHaveAttribute('aria-pressed','false');
  await page.getByTestId('wb-pause').click();await expect.poll(()=>status(page)).toBe('paused');await productShot(page,info,'injection-receipt');
  await page.getByTestId('wb-graph-toggle').click();await page.getByTestId('wb-graph').getByTitle('Expand',{exact:true}).click();
  const graph=page.getByTestId('wb-graph');
  for(const width of [1280,1600,1920]){
    await page.setViewportSize({width,height:width===1280?800:width===1600?1000:1080});const g=await graph.boundingBox(),c=await page.getByTestId('wb-camera').boundingBox();expect(g!.x+g!.width).toBeLessThanOrEqual(c!.x);
    await expect(page.getByTestId('wb-camera').getByRole('button').last()).toBeInViewport();
  }
  await productShot(page,info,'expanded-graph',true);
  // A graph selection is also manual and must survive later live director cues.
  await graph.getByRole('combobox',{name:'Graph entity',exact:true}).selectOption(pinned+':0');
  await page.getByTestId('wb-graph-toggle').click();await page.getByTestId('wb-speed').selectOption('50');
  if(template==='traffic-accident')await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();
  await page.getByTestId('wb-resume').click();
  if(template==='traffic-accident'){
    // Open before capture: no Refresh click and no scripted scrolling is allowed.
    await expect(page.getByRole('button',{name:'Open photo',exact:true}).first()).toBeVisible({timeout:900_000});await page.getByRole('button',{name:'Open photo',exact:true}).first().click();
    const photo=page.getByRole('img',{name:'Stored capture photo',exact:true});await expect.poll(()=>photo.evaluate((el:HTMLImageElement)=>el.naturalWidth)).toBeGreaterThan(0);await expect(photo).toBeInViewport();if(await page.getByTestId('wb-pause').count())await page.getByTestId('wb-pause').click();await productShot(page,info,'live-open-photo',true);
  }else{
    await expect.poll(async()=>{const feed=await(await page.request.get(`/v1/runs/${original}/commits?from=1&limit=4096`)).json();return feed.commits.some((c:any)=>c.messages.some((m:any)=>m.schemaId==='lowalt.food.ready'));},{timeout:120_000}).toBe(true);
  }
  if(await page.getByTestId('wb-pause').count())await page.getByTestId('wb-pause').click();await expect.poll(()=>status(page)).toBe('paused');
  await expect(page.getByTestId('wb-inspector')).toContainText(pinned);await expect(page.getByTestId('wb-objects').locator('.wb-row[aria-current=true]')).toContainText(pinned);await productShot(page,info,'manual-selection-retained');
  await page.getByTestId('wb-dock').getByRole('button',{name:/^Events\b/}).click();
  const effect=template==='food-delivery'?/preparation delay/:/Accident activated/;const event=page.locator('.newc-event-item').filter({hasText:effect}).first();await expect(event).toBeVisible({timeout:120_000});
  await event.getByRole('button',{name:'Why did this happen?',exact:true}).click();await expect(page.getByTestId('cause-trace')).toBeVisible();await expect(page.getByTestId('wb-history')).toBeVisible();await expect(button).toBeDisabled();await expect(page.getByTestId('waiting-banner')).toBeHidden();await productShot(page,info,'why-history',true);
  await page.getByTestId('wb-back-live').click();
  if(template==='food-delivery'){await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();await expect(page.getByText('No stored artifacts.',{exact:true})).toBeVisible();await productShot(page,info,'photos-empty-honest');}
  if(await page.getByTestId('wb-stop').count()){await page.getByTestId('wb-stop').click();await expect.poll(()=>status(page)).toMatch(/completed|stopped/);}
  await page.getByTestId('wb-records-toggle').click();await expect(page.getByTestId('wb-records')).toBeVisible();await productShot(page,info,'records');
  await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:original}).getByRole('button',{name:'Modify & retry',exact:true}).click();await expect(page.getByTestId('wb-config')).toBeVisible();
  await page.getByTestId('wb-config').getByRole('button',{name:'Run & recording',exact:true}).click();await page.getByTestId('wb-config').locator('[data-path="/run/seed"] input').fill('43');await productShot(page,info,'modify-retry');
  await page.getByTestId('wb-validate').click();await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});await page.getByTestId('wb-save-version').click();await expect(page.getByTestId('wb-initialize')).toBeEnabled({timeout:60_000});await page.getByTestId('wb-initialize').click();await expect.poll(()=>status(page),{timeout:90_000}).toBe('initialized');const variant=runId(page);
  await page.getByTestId('wb-start').click();await expect(page.getByTestId('wb-stop')).toBeVisible();await page.getByTestId('wb-stop').click();await expect.poll(()=>status(page),{timeout:60_000}).toBe('stopped');
  await page.getByTestId('wb-records-toggle').click();await page.getByTestId('wb-records').getByRole('button',{name:'All runs',exact:true}).click();for(const id of [original,variant])await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:id}).getByRole('checkbox').check();
  await page.getByTestId('wb-compare').click();await expect(page.getByTestId('comparison-configuration')).toContainText('/run/seed',{timeout:60_000});await expect(page.getByTestId('comparison-configuration')).toContainText('43');await productShot(page,info,'compare',true);
  expect(errors.get(page)).toEqual([]);
});
