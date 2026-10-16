import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const incidentId = 'conditions-fire';
const shots = join(process.env.AEROAGENTSIM_E2E_ROOT!, 'screenshots');
const visualNote = "Rain, visibility and light: visual only — shown in the 3D view and UAV captures; flight, sensing and task outcomes don't use it.";
const screenshot = (page: Page, step: string) => page.screenshot({ path: join(shots, `conditions-${step}.png`) });

async function confirm(page: Page, form: Locator, reviewed?: (review: Locator) => Promise<void>) {
  await form.getByRole('button', { name: 'Preview', exact: true }).click();
  const review = form.getByRole('group', { name: 'Preview of the change', exact: true });
  await expect(review).toBeVisible({ timeout: 60_000 });
  await reviewed?.(review);
  // The review closes when the request starts, before the real draft is saved.
  const applied = page.waitForResponse(response => response.request().method() === 'POST'
    && response.url().endsWith('/operations') && !response.request().postDataJSON().dry_run);
  await review.getByRole('button', { name: 'Confirm', exact: true }).click();
  expect((await applied).ok()).toBe(true);
  await expect(form.getByRole('button', { name: 'Preview', exact: true })).toBeVisible({ timeout: 60_000 });
}

async function json(page: Page, path: string) {
  const response = await page.request.get(path);
  expect(response.ok(), path).toBe(true);
  return response.json();
}

// Display-only framing through the existing viewport object, as in the camera
// look script. No incident, weather or capture state is introduced here.
async function frameFire(page: Page) {
  await page.evaluate(id => {
    const host = document.querySelector('[data-testid=viewport]') as any;
    const key = Object.keys(host).find(name => name.startsWith('__reactFiber$'))!;
    let view: any;
    for (let fiber = host[key]; fiber && !view; fiber = fiber.return) {
      for (let hook = fiber.memoizedState; hook; hook = hook.next) {
        const current = hook.memoizedState?.current;
        if (current?.camera && typeof current.focusPosition === 'function') { view = current; break; }
      }
    }
    if (!view) throw Error('The real run viewport is not mounted');
    const fire = view.scene.getObjectByName(`fx-incident-${id}`);
    if (!fire) throw Error(`The journaled fire ${id} has no scene object`);
    view.options.onCameraInteraction?.();
    view.setDirectorTarget(undefined); view.setCameraMode('orbit'); view.cameraTransition = undefined;
    const target = fire.position.clone(); target.y += 12;
    const pitch = 35 * Math.PI / 180, azimuth = .8 * Math.PI, distance = 250;
    view.controls.target.copy(target);
    view.camera.position.set(target.x + distance * Math.cos(pitch) * Math.sin(azimuth),
      target.y + distance * Math.sin(pitch), target.z + distance * Math.cos(pitch) * Math.cos(azimuth));
    view.camera.lookAt(target); view.controls.update(); view.revision++;
  }, incidentId);
}

test('real fire, weather, replay and incident photograph through the workbench', async ({ page }, info) => {
  test.setTimeout(480_000); page.setDefaultTimeout(60_000);
  await mkdir(shots, { recursive: true });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.addInitScript(() => localStorage.setItem('aeroagentsim.locale', 'en-US'));
  let run: string | undefined, completed = false;
  try {
    await test.step('gallery project and a responder using the installed city camera', async () => {
      await page.goto('/?gallery=1');
      await page.getByTestId('wb-template-city-day').click();
      await page.getByTestId('wb-create-project').click();
      await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 90_000 });
      await screenshot(page, '01-project');
      const nav = page.locator('.wb-config > nav');
      await nav.getByRole('button', { name: 'Tasks & behaviours', exact: true }).click();
      // city-day installs traffic_capture_bridge but has no cooperation by
      // default. Assign a real lowalt UAV through set_cooperation in the UI.
      await page.getByLabel('Watcher vehicles', { exact: true }).selectOption(['drone-3']);
      await confirm(page, page.getByRole('group', { name: 'Cooperation', exact: true }));
      await nav.getByRole('button', { name: 'Execution components', exact: true }).click();
      const components = page.locator('[data-section=components]');
      await components.locator(':scope > .wb-form-section > details > summary').click();
      for (const name of ['tasks', 'cooperation', 'responder', 'rules']) {
        const summary = components.locator('summary').filter({ has: page.getByText(name, { exact: true }) });
        await expect(summary).toBeVisible();
        if (!await summary.evaluate(element => (element.parentElement as HTMLDetailsElement).open)) await summary.click();
      }
      // Permit interruption of an ordinary parcel task so the response reaches
      // the fire before 100 s. The default protected medical rule is retained.
      await components.locator('[data-path="/engines/tasks/config/cooperation/responder/rules/require_replacement"] select').selectOption('false');
      await page.getByRole('button', { name: 'Save draft', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled({ timeout: 60_000 });
      await nav.getByRole('button', { name: 'Run & recording', exact: true }).click();
      await page.getByLabel('Duration (s)', { exact: true }).fill('120');
      await page.getByRole('button', { name: 'Preview duration', exact: true }).click();
      const durationReview = page.getByRole('group', { name: 'Preview of the change', exact: true });
      await expect(durationReview).toContainText('120', { timeout: 60_000 });
      const applied = page.waitForResponse(response => response.request().method() === 'POST'
        && response.url().endsWith('/operations') && !response.request().postDataJSON().dry_run);
      await durationReview.getByRole('button', { name: 'Confirm', exact: true }).click();
      expect((await applied).ok()).toBe(true);
      await expect(page.getByRole('button', { name: 'Preview duration', exact: true })).toBeEnabled({ timeout: 60_000 });
    });

    await test.step('schedule a building fire in Tasks and confirm its review', async () => {
      await page.locator('.wb-config > nav').getByRole('button', { name: 'Tasks & behaviours', exact: true }).click();
      await page.locator('.wb-coop > details > summary').filter({ hasText: 'Incidents' }).click();
      const form = page.getByRole('group', { name: 'Add incident', exact: true });
      await form.getByLabel('Incident ID', { exact: true }).fill(incidentId);
      // Ground-level home departure, next to the buildings on the parcel route.
      await form.getByLabel('Incident position', { exact: true }).fill('-13.05,-100.037,0');
      await form.getByRole('button', { name: 'Fire', exact: true }).click();
      await form.getByLabel('Affected radius (m)', { exact: true }).fill('30');
      await form.getByLabel('Starts at (s)', { exact: true }).fill('20');
      await form.getByLabel('Ends at (s)', { exact: true }).fill('100');
      await screenshot(page, '02-incident-form');
      await confirm(page, form, async review => {
        await expect(review).toContainText('Building fire');
        await screenshot(page, '03-incident-review');
      });
      const incident = page.locator('[data-section=tasks]').getByTestId('wb-incident').filter({ hasText: incidentId });
      await expect(incident).toContainText('00:20–01:40', { timeout: 60_000 });
      await expect(incident).toContainText('r 30 m');
    });

    await test.step('NW wind and visual weather through the Weather card', async () => {
      await page.locator('.wb-config > nav').getByRole('button', { name: 'Execution components', exact: true }).click();
      const wind = page.getByRole('group', { name: 'Background wind', exact: true });
      await wind.getByLabel('Background speed (m/s)', { exact: true }).fill('6');
      await wind.getByTestId('wb-wind-from').getByRole('button', { name: 'From north-west', exact: true }).click();
      await expect(wind.getByRole('button', { name: 'From north-west', exact: true })).toHaveAttribute('aria-pressed', 'true');
      await confirm(page, wind);
      const weather = page.getByRole('group', { name: 'Conditions', exact: true });
      await weather.getByTestId('wb-rain-presets').getByRole('button', { name: 'Moderate rain · 4 mm/h', exact: true }).click();
      await weather.getByTestId('wb-visibility-presets').getByRole('button', { name: 'Mist · 2 km', exact: true }).click();
      await weather.getByTestId('wb-light-presets').getByRole('button', { name: 'Dusk', exact: true }).click();
      for (const name of ['Moderate rain · 4 mm/h', 'Mist · 2 km', 'Dusk']) {
        await expect(weather.getByRole('button', { name, exact: true })).toHaveAttribute('aria-pressed', 'true');
      }
      await expect(weather.getByRole('note').filter({ hasText: visualNote })).toBeVisible();
      await screenshot(page, '04-weather-form');
      await confirm(page, weather, async review => {
        await expect(review).toContainText('Rain (mm/h)');
        await expect(review).toContainText('Visibility (m)');
        await expect(review).toContainText('Light');
        await screenshot(page, '05-weather-review');
      });
      const hud = page.getByTestId('preview-conditions').getByTestId('wb-conditions-hud');
      await expect(hud).toContainText('Preview at 00:40', { timeout: 60_000 });
      for (const text of ['Fire active', 'Moderate rain', 'Mist · 2 km', 'Dusk', 'Wind 6.0 m/s · from NW']) await expect(hud).toContainText(text);
      await expect(page.getByTestId('draft-preview')).toHaveAttribute('data-rendered', 'true');
      await expect(page.getByTestId('draft-preview')).toHaveAttribute('aria-busy', 'false', { timeout: 120_000 });
      await page.getByTestId('wb-config').getByRole('button', { name: 'Close configuration', exact: true }).click();
      await screenshot(page, '06-draft-preview');
    });

    await test.step('save a version, initialize and watch the active fire', async () => {
      await page.getByTestId('wb-configure').click();
      await page.getByTestId('wb-validate').click();
      await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
      await page.getByTestId('wb-save-version').click();
      await expect(page.getByTestId('wb-initialize')).toBeEnabled({ timeout: 60_000 });
      await page.getByTestId('wb-initialize').click();
      await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'initialized', { timeout: 120_000 });
      run = new URL(page.url()).searchParams.get('run')!;
      expect(run).toBeTruthy();
      await expect(page.getByTestId('viewport')).toHaveAttribute('data-city', 'loaded', { timeout: 120_000 });
      await screenshot(page, '07-initialized');
      await page.getByTestId('wb-speed').selectOption('5');
      const started = Date.now();
      await page.getByTestId('wb-start').click();
      await expect(page.getByTestId('wb-conditions-hud')).toContainText('Fire active', { timeout: 45_000 });
      await expect.poll(async () => {
        const clock = await page.locator('.wb-clock time').innerText();
        const [minutes, seconds] = clock.split(':').map(Number);
        return minutes * 60 + seconds;
      }, { timeout: 30_000 }).toBeGreaterThanOrEqual(25);
      await page.getByTestId('wb-pause').click();
      await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'paused', { timeout: 60_000 });
      await frameFire(page);
      const pin = page.locator('.entity-label').filter({ hasText: 'Building fire · Active' });
      await expect(pin).toBeVisible({ timeout: 15_000 });
      await expect(page.getByTestId('wb-conditions-hud')).toContainText('Fire active');
      await screenshot(page, '08-live-active');
      await page.getByTestId('wb-speed').selectOption('max');
      await page.getByTestId('wb-resume').click();
      await expect(page.getByTestId('workbench')).toHaveAttribute('data-status', 'completed', { timeout: 90_000 });
      completed = true;
      expect(Date.now() - started, 'watching the real run takes under 90 s').toBeLessThan(90_000);
      await expect(page.getByTestId('wb-catching-up')).toHaveCount(0, { timeout: 60_000 });
      await screenshot(page, '09-completed');
    });

    const artifacts = await json(page, `/v1/runs/${run}/artifacts`);
    const photo = artifacts.find((row: any) => row.request.camera.subject?.id === incidentId);
    expect(photo, 'the cooperation responder must produce a real incident photograph').toBeDefined();
    expect(photo.renderer_mode).toBe('browser');
    expect(photo.request.request_id).toMatch(/^cooperation\/conditions-fire\/drone-3\//);
    expect(photo.request.camera.revision).toBe('uav-gimbal-oblique/v1');
    const photoNs = BigInt(photo.request.source_cut.instant[0]);
    expect(photoNs).toBeGreaterThanOrEqual(20_000_000_000n);
    expect(photoNs).toBeLessThan(100_000_000_000n);
    await writeFile(join(shots, 'conditions-photo-metadata.json'), JSON.stringify(photo, null, 2));

    // The slider uses journal offsets, not seconds. Derive offsets from the
    // actual committed feed, so engine tick/commit frequency cannot break seeks.
    const commits: any[] = [];
    for (let from = 0; ;) {
      const feed = await json(page, `/v1/runs/${run}/commits?from=${from}&limit=4096`);
      commits.push(...feed.commits);
      if (!feed.commits.length) break;
      from = feed.next;
    }
    const seek = async (seconds: number) => {
      const ns = BigInt(seconds) * 1_000_000_000n;
      const offset = commits.reduce((last, commit, index) => BigInt(commit.at.ns) <= ns ? index : last, -1);
      expect(offset).toBeGreaterThanOrEqual(0);
      const slider = page.getByRole('slider', { name: 'Recorded timeline', exact: true });
      await expect(slider).toHaveAttribute('max', String(commits.length - 1), { timeout: 120_000 });
      await slider.fill(String(offset));
      await expect(page.getByTestId('wb-seeking')).toHaveCount(0, { timeout: 60_000 });
    };

    await test.step('open the completed replay and seek active and ended fire', async () => {
      await page.getByTestId('wb-records-toggle').click();
      // Opening the same run in replay replaces the live session after the
      // first React render. Wait for that session's header, not the old slider.
      const replayHeader = page.waitForResponse(response => response.request().method() === 'GET'
        && new URL(response.url()).pathname === `/v1/runs/${run}/header`);
      await page.getByTestId('wb-records').locator('.wb-record').filter({ hasText: run! }).getByTestId('wb-open-run').click();
      expect((await replayHeader).ok()).toBe(true);
      await expect(page.getByTestId('workbench')).toHaveAttribute('data-mode', 'replay');
      await expect(page.locator('.wb-clock time')).toHaveText('00:00.0', { timeout: 60_000 });
      await expect(page.getByTestId('wb-catching-up')).toHaveCount(0, { timeout: 60_000 });
      await seek(55);
      await expect(page.getByTestId('wb-conditions-hud')).toContainText('Fire active');
      await frameFire(page);
      await expect(page.locator('.entity-label').filter({ hasText: 'Building fire · Active' })).toBeVisible();
      await screenshot(page, '10-replay-active');
      await seek(110);
      await expect(page.locator('.entity-label').filter({ hasText: 'Fire · ended' })).toBeVisible();
      await expect(page.getByTestId('wb-conditions-hud')).not.toContainText('Fire active');
      await screenshot(page, '11-replay-ended');
    });

    await test.step('Photos opens and saves the actual incident capture', async () => {
      await page.getByTestId('wb-dock').getByRole('button', { name: 'Photos', exact: true }).click();
      const row = page.getByTestId('wb-dock').locator('article').filter({ hasText: photo.request.request_id });
      await row.getByRole('button', { name: 'Seek photo moment', exact: true }).click();
      await expect(page.getByTestId('wb-conditions-hud')).toContainText('Fire active');
      await row.getByRole('button', { name: 'Open photo', exact: true }).click();
      const image = page.getByTestId('wb-dock').getByRole('img', { name: 'Stored capture photo', exact: true });
      await expect(image).toBeVisible();
      await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(photo.request.width);
      const response = await page.request.get(`/v1/runs/${run}/artifacts/${photo.digest}/download`);
      expect(response.ok()).toBe(true);
      const png = await response.body();
      expect(png.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
      expect(png.readUInt32BE(16)).toBe(photo.request.width);
      expect(png.readUInt32BE(20)).toBe(photo.request.height);
      expect(png.length).toBeGreaterThan(50_000);
      await writeFile(join(shots, 'conditions-incident-photo.png'), png);
      await screenshot(page, '12-photos');
      await page.getByRole('button', { name: 'View full-size photo', exact: true }).click();
      await expect(page.getByRole('dialog').getByRole('img', { name: 'Stored capture photo', exact: true })).toBeVisible();
      await screenshot(page, '13-full-photo');
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toBeHidden();
      await page.getByRole('button', { name: 'Collapse dock', exact: true }).click();
    });

    await test.step('Chinese HUD and no browser errors', async () => {
      await seek(55);
      await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
      const hud = page.getByTestId('wb-conditions-hud');
      for (const text of ['西北风', '火情进行中', '中雨', '轻雾', '黄昏']) await expect(hud).toContainText(text);
      await screenshot(page, '14-zh');
      expect(errors).toEqual([]);
    });
  } finally {
    await writeFile(info.outputPath('browser-errors.json'), JSON.stringify(errors, null, 2));
    if (run && !completed) await page.request.post(`/v1/runs/${run}/stop`, { data: {} });
  }
});
