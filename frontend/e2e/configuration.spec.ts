import { expect, test, type Page } from '@playwright/test';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';

const root = process.env.AEROAGENTSIM_E2E_ROOT!;
const shots = join(root, 'screenshots'); mkdirSync(shots, { recursive: true });
const templates = ['parcel-delivery', 'medical-delivery', 'powerline-inspection', 'crop-spraying', 'air-taxi', 'security-patrol', 'traffic-accident', 'city-day', 'food-delivery'];
test.use({ launchOptions: { executablePath: process.env.AEROAGENTSIM_CHROMIUM, args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'] } });
function errorsOn(page: Page) {
  page.setDefaultTimeout(30_000);
  const errors: string[] = []; page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/ERR_ABORTED|favicon/.test(message.text())) errors.push(message.text()); }); return errors;
}
const state = (page: Page) => page.getByTestId('workbench').getAttribute('data-status');
const shot = (page: Page, name: string) => page.screenshot({ path: join(shots, `${name}.png`) });
const language = async (page: Page, zh: boolean) => {
  if ((await page.locator('html').getAttribute('lang') === 'zh-CN') !== zh) {
    const gallery = page.getByTestId('wb-gallery'), host = await gallery.isVisible() ? gallery : page.locator('.wb-top');
    await host.getByRole('button', { name: zh ? 'Switch to Chinese' : '切换到英文', exact: true }).click();
  }
};

for (const id of templates) test(`scenario ${id}: guide, every configuration section in both languages and sizes, held initialization`, async ({ page }) => {
  test.setTimeout(300_000); const errors = errorsOn(page); page.on('dialog', dialog => dialog.accept());
  await page.goto('/?gallery=1');
    await expect(page.getByTestId(`wb-template-${id}`)).toBeVisible({ timeout: 60_000 });
    await page.getByTestId(`wb-template-${id}`).click();
    await expect(page.locator('.wb-markdown')).toBeVisible();
    await expect(page.getByTestId('wb-create-project')).toBeEnabled();
    for (const width of [1280, 1920]) for (const zh of [false, true]) {
      await page.setViewportSize({ width, height: width === 1280 ? 800 : 1080 }); await language(page, zh);
      await shot(page, `${id}-guide-${width}-${zh ? 'zh' : 'en'}`);
      await expect(page.locator('.wb-overlay-card')).toHaveJSProperty('scrollWidth', await page.locator('.wb-overlay-card').evaluate(e => e.clientWidth));
    }
    await language(page, false); await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByTestId('wb-create-project').click(); await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('draft-preview')).toHaveAttribute('data-rendered', 'true', { timeout: 60_000 });
    await shot(page, `${id}-loading`);
    await expect(page.getByTestId('draft-preview')).toHaveAttribute('aria-busy', 'false', { timeout: 180_000 });
    await shot(page, `${id}-preview-1280`);
    for (const width of [1280, 1920]) for (const zh of [false, true]) {
      await page.setViewportSize({ width, height: width === 1280 ? 800 : 1080 }); await language(page, zh);
      const nav = page.locator('.wb-config > nav'), count = await nav.getByRole('button').count();
      for (let index = 0; index < count; index++) {
        await nav.getByRole('button').nth(index).click();
        const body = page.locator('.wb-config-body');
        if (index === count - 1 && await page.getByRole('button', { name: /Load scenario file|加载场景文件/, exact: true }).isVisible()) {
          await page.getByRole('button', { name: /Load scenario file|加载场景文件/, exact: true }).click(); await expect(body.locator('textarea')).toBeVisible({ timeout: 60_000 });
        }
        const dimensions = await body.evaluate(e => ({ width: e.clientWidth, content: e.scrollWidth }));
        expect(dimensions.content, `${id} ${width} ${zh} section ${index}`).toBeLessThanOrEqual(dimensions.width + 2);
        const unlabelled = await body.locator('input:not([type=hidden]),select,textarea').evaluateAll(elements => elements.filter(e => !((e as HTMLInputElement).labels?.length || e.getAttribute('aria-label') || e.getAttribute('aria-labelledby'))).map(e => e.outerHTML));
        expect(unlabelled, `${id} section ${index} labels`).toEqual([]);
        if (width === 1280 && !zh || index === 0) await shot(page, `${id}-section${index}-${width}-${zh ? 'zh' : 'en'}`);
      }
    }
    await language(page, false); await page.setViewportSize({ width: 1280, height: 800 });
    await page.getByTestId('wb-validate').click(); await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
    await page.getByTestId('wb-save-version').click(); await expect(page.getByTestId('wb-initialize')).toBeEnabled({ timeout: 60_000 });
    await page.getByTestId('wb-initialize').click(); await expect.poll(() => state(page), { timeout: 90_000 }).toBe('initialized');
    await page.locator('[data-testid=viewport][data-rendered=true]').waitFor({ timeout: 120_000 });
    await expect(page.locator('.wb-clock time')).toHaveText('00:00.0');
    await shot(page, `${id}-initialized`); await expect(page.getByTestId('wb-start')).toBeEnabled();
    console.log(`Verified ${id}: held initialization and all sections in four language/size combinations`);
  expect(errors).toEqual([]);
});

test('draft recovery, typed inputs, rule editor, YAML export/import and contract return', async ({ page }) => {
  test.setTimeout(360_000); const errors = errorsOn(page); page.on('dialog', dialog => dialog.accept());
  await page.goto('/?gallery=1'); await page.getByTestId('wb-template-food-delivery').click(); await page.getByTestId('wb-create-project').click();
  await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
  const fleet = page.getByRole('spinbutton', { name: 'Delivery drones', exact: true }); await expect(fleet).toBeVisible();
  const original = await fleet.inputValue(); await fleet.fill('');
  await expect(fleet).toHaveAttribute('aria-invalid', 'true'); await expect(page.getByTestId('wb-validate')).toBeDisabled();
  await fleet.fill(original); await expect(page.getByTestId('wb-validate')).toBeEnabled();
  await fleet.fill(String(Number(original) + 1)); await page.getByRole('button', { name: 'Apply controls to draft', exact: true }).click();
  await expect(page.getByTestId('wb-validate')).toBeEnabled();
  const nav = page.locator('.wb-config > nav'); await nav.getByRole('button', { name: 'Run & recording', exact: true }).click();
  const seed = page.locator('[data-path="/run/seed"] input'); await seed.fill('73');
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeEnabled(); await page.reload();
  await expect(seed).toHaveValue('73'); await expect(page.locator('.wb-restored')).toBeVisible();
  await seed.fill(''); await expect(seed).toHaveAttribute('aria-invalid', 'true'); await expect(page.getByTestId('wb-validate')).toBeDisabled(); await shot(page, 'invalid-numeric');
  await seed.fill('1.5'); await page.getByTestId('wb-validate').click(); await expect(page.getByTestId('wb-issues')).toBeVisible({ timeout: 60_000 });
  await expect(seed.locator('..')).toHaveClass(/is-focus/); await shot(page, 'validation-jump');
  await seed.fill('73'); await page.getByRole('button', { name: 'Save draft', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled();
  const projectId = new URL(page.url()).pathname.split('/').pop()!;
  const project = await (await page.request.get(`/v1/studio/workspaces/${projectId}`)).json(); expect(project.scenario.run.seed).toBe(73);
  await nav.getByRole('button', { name: 'Tasks & behaviours', exact: true }).click();
  await page.getByRole('button', { name: 'Edit predicates, triggers and event chains…', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Behaviour package authoring' })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole('button', { name: 'Conditions (predicates)', exact: true })).toBeVisible(); await shot(page, 'structured-rule-editor');
  await nav.getByRole('button', { name: 'File (YAML)', exact: true }).click();
  await page.getByRole('button', { name: 'Load scenario file', exact: true }).click(); const yaml = page.getByRole('textbox', { name: 'Scenario YAML', exact: true }); await expect(yaml).toContainText('seed: 73');
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: 'Download YAML', exact: true }).click(); const file = await download; await file.saveAs(join(root, 'export.yaml'));
  await yaml.fill((await yaml.inputValue()).replace('seed: 73', 'seed: 74')); await page.getByRole('button', { name: 'Import into draft', exact: true }).click();
  await expect(page.getByText('Imported into the draft.', { exact: true })).toBeVisible();
  await nav.getByRole('button', { name: 'Objects', exact: true }).click();
  await page.getByRole('button', { name: 'Inspect type contract', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Type contract', exact: true })).toBeVisible(); await shot(page, 'contract-overlay');
  await page.getByRole('button', { name: '← Back to the field you were editing', exact: true }).click(); await expect(nav.getByRole('button', { name: 'Objects', exact: true })).toHaveAttribute('aria-current', 'true');
  expect(errors).toEqual([]);
});

test('records compare two through four runs, replay, and derive preserves the original', async ({ page }) => {
  test.setTimeout(360_000); const errors = errorsOn(page); page.on('dialog', dialog => dialog.accept());
  await page.goto('/?records=1'); await expect(page.getByTestId('wb-records')).toBeVisible();
  // Create an actual terminal journal for replay. This explicitly starts a held run.
  const created = await page.request.post('/v1/templates/food-delivery/workspace', { data: { knobs: {} } }); expect(created.ok()).toBe(true);
  const projectId = (await created.json()).workspace_id;
  const project = await (await page.request.get(`/v1/studio/workspaces/${projectId}`)).json();
  const validation = await page.request.post(`/v1/studio/workspaces/${projectId}/validate`, { data: { revision: project.revision } }); expect((await validation.json()).valid).toBe(true);
  const saved = await page.request.post(`/v1/studio/workspaces/${projectId}/versions`, { data: { revision: project.revision } }); expect(saved.ok()).toBe(true);
  const initialized = await page.request.post('/v1/runs', { data: { studio_workspace: projectId, version: (await saved.json()).version, autostart: false } }); expect(initialized.ok()).toBe(true);
  const held = await initialized.json();
  expect((await page.request.post(`/v1/runs/${held.id}/speed`, { data: { factor: null } })).ok()).toBe(true);
  expect((await page.request.post(`/v1/runs/${held.id}/start`, { data: {} })).ok()).toBe(true);
  await expect.poll(async () => (await (await page.request.get(`/v1/runs/${held.id}/commits?limit=1`)).json()).status, { timeout: 180_000 }).toBe('completed');
  const checks = page.getByTestId('wb-records').getByRole('checkbox'); await expect.poll(() => checks.count()).toBeGreaterThanOrEqual(5);
  await checks.nth(0).check(); await checks.nth(1).check();
  for (let count = 2; count <= 4; count++) {
    await page.getByTestId('wb-compare').click(); await expect(page.locator('.newc-compare')).toBeVisible({ timeout: 60_000 });
    await shot(page, `compare-${count}`); await page.getByRole('button', { name: 'Go back', exact: true }).click();
    if (count < 4) await checks.nth(count).check();
  }
  await expect(checks.nth(4)).toBeDisabled();
  const record = page.getByTestId('wb-records').locator('.wb-record').filter({ hasText: held.id }); const id = held.id;
  await expect(record.getByRole('button', { name: 'Replay', exact: true })).toBeVisible({ timeout: 15_000 });
  await record.getByTestId('wb-open-run').click(); await expect(page).toHaveURL(/mode=replay/);
  await page.getByTestId('wb-records-toggle').click(); await page.getByRole('button', { name: 'All runs', exact: true }).click();
  await page.getByTestId('wb-records').locator('.wb-record').filter({ hasText: id }).getByRole('button', { name: 'Modify & retry', exact: true }).click(); await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
  const derivedId = new URL(page.url()).pathname.split('/').pop()!, derived = await (await page.request.get(`/v1/studio/workspaces/${derivedId}`)).json();
  expect(derived.derived_from.run_id).toBe(id); expect((await page.request.get(`/v1/runs/${id}/header`)).ok()).toBe(true); await shot(page, 'derived-draft');
  expect(errors).toEqual([]);
});
