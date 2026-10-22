import { expect, test, type Page } from '@playwright/test';
import { join } from 'node:path';
import { mkdirSync } from 'node:fs';

const root = process.env.AEROAGENTSIM_E2E_ROOT!;
const shots = join(root, 'screenshots'); mkdirSync(shots, { recursive: true });

test.use({
  viewport: { width: 1440, height: 900 },
  launchOptions: {
    executablePath: process.env.AEROAGENTSIM_CHROMIUM,
    args: ['--no-sandbox', '--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'],
  },
});

function errorsOn(page: Page) {
  page.setDefaultTimeout(30_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/ERR_ABORTED|favicon/.test(message.text())) errors.push(message.text()); });
  return errors;
}

const shot = (page: Page, name: string) => page.screenshot({ path: join(shots, `${name}.png`) });

/** The same setting, including its accessibility label, in each locale. */
const copy = (zh: boolean, english: string, chinese: string) => zh ? chinese : english;

async function language(page: Page, zh: boolean) {
  if ((await page.locator('html').getAttribute('lang') === 'zh-CN') !== zh) {
    const gallery = page.getByTestId('wb-gallery');
    const host = await gallery.isVisible() ? gallery : page.locator('.wb-top');
    await host.getByRole('button', { name: zh ? 'Switch to Chinese' : '切换到英文', exact: true }).click();
  }
  await expect(page.locator('html')).toHaveAttribute('lang', zh ? 'zh-CN' : 'en');
}

async function createFoodDelivery(page: Page, zh: boolean) {
  await page.goto('/?gallery=1');
  await language(page, zh);
  await page.getByTestId('wb-template-food-delivery').click();
  await expect(page.getByTestId('wb-guide')).toBeVisible();
  await page.getByTestId('wb-create-project').click();
  await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 60_000 });
}

test('configure, confirm and validation failure keep the same controls in both locales', async ({ page }) => {
  test.setTimeout(300_000);
  const errors = errorsOn(page);
  page.on('dialog', dialog => dialog.accept());

  for (const zh of [false, true]) {
    await createFoodDelivery(page, zh);
    const label = (english: string, chinese: string) => copy(zh, english, chinese);

    // Configure: the typed control keeps its wire id and gains only a localized label.
    const fleet = page.getByTestId('knob-fleet_size');
    await expect(fleet).toBeVisible();
    await expect(page.getByRole('spinbutton', { name: label('Delivery drones', '送餐无人机数量'), exact: true })).toBeVisible();
    await fleet.fill('3');
    await expect(fleet).toHaveValue('3');
    await expect(page.getByRole('button', { name: label('Save draft', '保存草稿'), exact: true })).toBeEnabled();

    // Confirm: saving the draft and then the validated version are separate, explicit actions.
    await page.getByRole('button', { name: label('Save draft', '保存草稿'), exact: true }).click();
    await expect(page.getByRole('button', { name: label('Save draft', '保存草稿'), exact: true })).toBeDisabled();
    await page.getByTestId('wb-validate').click();
    await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
    await page.getByTestId('wb-save-version').click();
    await expect(page.getByTestId('wb-initialize')).toBeEnabled({ timeout: 60_000 });
    await shot(page, `product-configure-${zh ? 'zh' : 'en'}`);

    // Validation failure: an invalid seed must not leave Save version enabled.
    await page.getByTestId('wb-config').getByRole('button', { name: label('Run & recording', '运行与记录'), exact: true }).click();
    const seed = page.getByRole('spinbutton', { name: label('Random seed', '随机种子'), exact: true });
    await expect(seed).toBeVisible();
    await seed.fill('1.5');
    await seed.press('Tab');
    await page.getByTestId('wb-validate').click();
    await expect(page.getByTestId('wb-issues')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('wb-issues')).toHaveAttribute('role', 'alert');
    await expect(page.getByTestId('wb-save-version')).toBeDisabled();
    await expect(page.getByTestId('wb-validate')).toBeEnabled();
    await shot(page, `product-validation-failure-${zh ? 'zh' : 'en'}`);

    // The failure belongs to this draft; restore the fixture value before leaving the locale.
    await seed.fill('7');
    await page.getByTestId('wb-validate').click();
    await expect(page.getByTestId('wb-issues')).toHaveCount(0, { timeout: 60_000 });
    await expect(page.getByRole('button', { name: label('Validate', '校验'), exact: true })).toBeEnabled();
  }

  expect(errors).toEqual([]);
});

async function completedRun(page: Page) {
  const created = await page.request.post('/v1/templates/food-delivery/workspace', { data: { knobs: {} } });
  expect(created.ok()).toBe(true);
  const id = (await created.json()).workspace_id;
  const project = await (await page.request.get(`/v1/studio/workspaces/${id}`)).json();
  const validation = await page.request.post(`/v1/studio/workspaces/${id}/validate`, { data: { revision: project.revision } });
  expect((await validation.json()).valid).toBe(true);
  const saved = await page.request.post(`/v1/studio/workspaces/${id}/versions`, { data: { revision: project.revision } });
  expect(saved.ok()).toBe(true);
  const initialized = await page.request.post('/v1/runs', { data: { studio_workspace: id, version: (await saved.json()).version, autostart: false } });
  expect(initialized.ok()).toBe(true);
  const held = await initialized.json();
  expect((await page.request.post(`/v1/runs/${held.id}/speed`, { data: { factor: null } })).ok()).toBe(true);
  expect((await page.request.post(`/v1/runs/${held.id}/start`, { data: {} })).ok()).toBe(true);
  await expect.poll(async () => (await (await page.request.get(`/v1/runs/${held.id}/commits?limit=1`)).json()).status, { timeout: 180_000 }).toBe('completed');
  return held.id as string;
}

test('comparison states and actions mean the same thing in both locales', async ({ page }) => {
  test.setTimeout(300_000);
  const errors = errorsOn(page);
  await completedRun(page);
  await completedRun(page);
  await page.goto('/?records=1');
  await expect(page.getByTestId('wb-records')).toBeVisible({ timeout: 60_000 });
  const checks = page.getByTestId('wb-records').getByRole('checkbox');
  await expect.poll(() => checks.count()).toBeGreaterThanOrEqual(2);
  await checks.nth(0).check();
  await checks.nth(1).check();
  await language(page, false);
  await page.getByTestId('wb-compare').click();
  await expect(page.locator('.newc-compare')).toBeVisible({ timeout: 60_000 });

  for (const zh of [false, true]) {
    // The comparison overlay covers the shell language control; reopen it
    // after switching so both locales describe the same selected runs.
    if (zh) {
      await page.getByRole('button', { name: 'Go back', exact: true }).click();
      await language(page, true);
      await page.getByTestId('wb-compare').click();
      await expect(page.locator('.newc-compare')).toBeVisible({ timeout: 60_000 });
    }
    const label = (english: string, chinese: string) => copy(zh, english, chinese);
    await expect(page.getByRole('region', { name: label('Comparison summary', '对比总览') })).toBeVisible();
    await expect(page.getByRole('button', { name: label('Go back', '返回'), exact: true })).toBeEnabled();
    // Finality is explicit, not inferred from the presence of numbers.
    await expect(page.locator('[data-testid^="comparison-state-"]').first()).toHaveText(/(Final|Provisional|Finality unknown|已最终|暂定|最终性未知)/);
    await expect(page.getByTestId('comparison-refresh')).toBeVisible();
    await expect(page.locator('.newc-compare-table caption')).toContainText(label('Start here:', '先看这里：'));
    await shot(page, `product-comparison-${zh ? 'zh' : 'en'}`);
  }

  expect(errors).toEqual([]);
});
