import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const screenshots = process.env.AEROAGENTSIM_SCREENSHOT_DIR ?? '/tmp/aas-q/s2';
async function choose(control: Locator, value?: string) {
  await control.press('Enter');
  if (value) await control.page().locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(value, { exact: true }).click();
  else { await control.press('ArrowDown'); await control.press('Enter'); }
}
async function fits(page: Page) {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test('English default, persistent Chinese switch and URL override cover every primary page', async ({ page }) => {
  test.setTimeout(240_000);
  mkdirSync(screenshots, { recursive: true });
  const errors: string[] = [], failedRequests: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.url()}`); });
  await page.addInitScript(() => localStorage.setItem('aas:welcome-dismissed', '1'));
  await page.goto('/');
  await expect(page.getByTestId('nav-home')).toHaveText('Home');
  await choose(page.getByTestId('language-switch').getByRole('combobox'), '中文');
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await expect(page.getByTestId('nav-home')).toHaveText('首页');
  await page.reload();
  await expect(page.getByTestId('nav-home')).toHaveText('首页');
  await page.getByTestId('template-parcel-delivery').waitFor();
  await fits(page); await page.screenshot({ path: join(screenshots, 'home-zh.png'), fullPage: true });
  await page.getByTestId('customize-template-parcel-delivery').click();
  const custom = page.getByTestId('customize-panel-parcel-delivery');
  await expect(custom).toContainText('无人机');
  await expect(custom).not.toContainText('Things to try');
  await page.getByRole('dialog').getByRole('button', { name: /关闭|Close/ }).click();
  await page.getByTestId('studio-template-parcel-delivery').click();
  await page.waitForURL(/studio\?workspace=/);
  for (const step of ['scene', 'entities', 'plugins', 'predicates', 'rules', 'agents', 'validate']) {
    await page.getByTestId(`studio-step-${step}`).click();
    await expect(page.getByTestId(`studio-panel-${step}`)).toBeVisible();
    await fits(page);
  }
  await page.getByTestId('studio-step-scene').click();
  await page.getByText(/正在加载城市/).waitFor({ state: 'hidden', timeout: 90_000 });
  await page.screenshot({ path: join(screenshots, 'studio-zh.png'), fullPage: true });
  await page.getByTestId('nav-aerograph').click();
  await expect(page.getByTestId('aerograph-page')).toHaveAttribute('data-ready', 'true');
  await fits(page);
  await page.getByTestId('nav-runs').click(); await expect(page.getByTestId('runs-page')).toBeVisible();
  await page.getByTestId('nav-home').click();
  await page.getByTestId('run-template-parcel-delivery').click();
  await page.waitForURL(/runs\/run-/);
  const run = new URL(page.url()).pathname.split('/').at(-1)!;
  try {
    await expect(page.getByTestId('simple-run-view')).toBeVisible();
    await expect(page.getByTestId('viewport')).toHaveAttribute('data-city', 'loaded', { timeout: 90_000 });
    await expect(page.locator('.newc-event-text').first()).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('.newc-event-text').first()).toHaveText(/[\u3400-\u9fff]/);
    await page.getByRole('checkbox', { name: '显示所有标签', exact: true }).check();
    await expect(page.locator('.simple-city')).not.toContainText(/Customer rooftop|charging pad|Parcel mission/);
    await expect(page.locator('.simple-city')).toContainText('客户屋顶');
    await page.getByRole('checkbox', { name: '显示所有标签', exact: true }).uncheck();
    await fits(page); await page.screenshot({ path: join(screenshots, 'simple-zh.png'), fullPage: true });
    await page.locator('.newc-event-why').first().click();
    await expect(page.getByTestId('cause-trace').locator('.ct-title')).toBeVisible();
    await page.getByTestId('cause-trace').locator('.ct-head button').last().click();
    await page.getByTestId('expert-toggle').click();
    await page.getByRole('tab', { name: '结果', exact: true }).click();
    await expect(page.locator('.newc-kpi-card').first()).toBeVisible();
    await page.getByTestId('expert-toggle').click();
    await expect(page.getByTestId('inspect-views')).toBeVisible();
    await fits(page); await page.screenshot({ path: join(screenshots, 'inspect-zh.png'), fullPage: true });
    await page.getByTestId('nav-inspect').click();
    await expect(page.getByTestId('inspect-views')).toBeVisible();
    await page.getByRole('link', { name: '智能体决策', exact: true }).click();
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  } finally {
    const rows = await page.request.get('/v1/runs').then(response => response.json());
    if (rows.some((row: { id: string; status: string }) => row.id === run && !['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout'].includes(row.status))) {
      expect((await page.request.post(`/v1/runs/${run}/stop`, {data:{}})).ok()).toBe(true);
    }
  }
  await page.goto('/?lang=en'); await expect(page.getByTestId('nav-home')).toHaveText('Home');
  await page.goto('/?lang=zh'); await expect(page.getByTestId('nav-home')).toHaveText('首页');
  expect(errors).toEqual([]); expect(failedRequests).toEqual([]);
});

test('gallery filters, every template customization and comparison help are usable', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('aas:welcome-dismissed', '1'));
  await page.goto('/?lang=en');
  await page.getByTestId('template-parcel-delivery').waitFor();
  for (const filter of await page.locator('.template-filter').all()) await filter.click();
  await page.getByTestId('filter-all').click();
  const templates = await page.request.get('/v1/templates').then(response => response.json());
  for (const template of templates) {
    await page.getByTestId(`customize-template-${template.id}`).click();
    const panel = page.getByTestId(`customize-panel-${template.id}`);
    for (const number of await panel.getByRole('spinbutton').all()) await number.press('ArrowUp');
    for (const select of await panel.getByRole('combobox').all()) await choose(select);
    for (const toggle of await panel.getByRole('switch').all()) await toggle.click();
    await expect(panel.getByRole('button', { name: 'Run with these settings', exact: true })).toBeEnabled();
    await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  }
  // Exercise the actual Run now button on every shipped card, not a mock API.
  for (const template of templates) {
    await page.getByTestId(`run-template-${template.id}`).click();
    await page.waitForURL(/runs\/run-/);
    const run = new URL(page.url()).pathname.split('/').at(-1)!;
    const snapshot = await page.request.get(`/v1/runs/${run}/commits?from=1&limit=1`);
    expect(snapshot.ok()).toBe(true);
    const state = (await snapshot.json()).status;
    if (!['completed', 'stopped', 'faulted', 'interrupted', 'input_timeout'].includes(state)) {
      const stopped = await page.request.post(`/v1/runs/${run}/stop`, {data:{}});
      expect([200, 409]).toContain(stopped.status()); // Completion may race cleanup.
    }
    await page.goto('/?lang=en');
    await page.getByTestId(`template-${template.id}`).waitFor();
  }
  await page.getByTestId('gallery-experiment').click();
  await expect(page.getByTestId('experiment-panel')).toBeVisible();
  await fits(page);
  await page.getByRole('dialog').getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByTestId('open-welcome').click(); await page.getByTestId('welcome-close').click();
  await page.getByRole('button', { name: 'Help', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Keyboard shortcuts', exact: true }).click();
  await expect(page.getByTestId('shortcuts-help')).toBeVisible();
  await page.keyboard.press('Escape');
  expect(errors).toEqual([]);
});
