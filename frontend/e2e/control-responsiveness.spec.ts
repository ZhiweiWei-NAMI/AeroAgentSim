import { expect, test } from '@playwright/test';

test('delayed inspector status reads cannot block top-bar controls in either language', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error' && !/favicon|ERR_ABORTED/.test(message.text())) errors.push(message.text()); });
  page.on('dialog', dialog => dialog.accept());
  await page.goto('/?gallery=1');
  await page.getByTestId('wb-template-food-delivery').click();
  await page.getByTestId('wb-create-project').click();
  await page.getByTestId('wb-validate').click();
  await expect(page.getByTestId('wb-save-version')).toBeEnabled({ timeout: 120_000 });
  await page.getByTestId('wb-save-version').click();
  await expect(page.getByTestId('wb-initialize')).toBeEnabled();
  await page.getByTestId('wb-initialize').click();
  const status = () => page.getByTestId('workbench').getAttribute('data-status');
  await expect.poll(status, { timeout: 90_000 }).toBe('initialized');
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered', 'true', { timeout: 90_000 });
  const run = new URL(page.url()).searchParams.get('run')!;
  const config = (await (await page.request.get(`/v1/studio/runs/${run}/configuration`)).json()).scenario;
  expect(config.ingress_streams.find((source: { id: string }) => source.id === 'operator').initial_watermark_ns).toBe(config.run.until_ns);
  await page.getByTestId('wb-start').click();
  await expect(page.getByTestId('wb-pause')).toBeVisible();
  await page.getByTestId('wb-pause').click();
  await expect.poll(status, { timeout: 30_000 }).toBe('paused');
  await page.getByRole('textbox', { name: 'Search objects', exact: true }).fill('restaurant-east');
  await page.getByTestId('wb-objects').locator('.wb-row').filter({ hasText: 'restaurant-east' }).click();
  let release!: () => void, reached!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  const requested = new Promise<void>(resolve => { reached = resolve; });
  // This is the inspector's read-only status request, not feed delivery or RPC.
  await page.route(/\/commits\?from=\d+&limit=1$/, async route => { reached(); await held; await route.continue(); });
  try {
    await page.getByRole('tab', { name: 'Operate', exact: true }).click();
    await requested;
    const resume = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/resume`) && response.request().method() === 'POST', { timeout: 10_000 });
    await page.getByTestId('wb-resume').click();
    expect((await resume).ok()).toBe(true);
    await expect.poll(status, { timeout: 30_000 }).toBe('running');
    await page.getByTestId('wb-pause').click();
    await expect.poll(status, { timeout: 30_000 }).toBe('paused');
    await page.getByRole('button', { name: 'Switch to Chinese', exact: true }).click();
    const resumed = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/resume`) && response.request().method() === 'POST', { timeout: 10_000 });
    await page.getByTestId('wb-resume').click(); expect((await resumed).ok()).toBe(true);
    await expect.poll(status, { timeout: 30_000 }).toBe('running');
    const stop = page.waitForResponse(response => response.url().endsWith(`/v1/runs/${run}/stop`) && response.request().method() === 'POST', { timeout: 10_000 });
    await page.getByTestId('wb-stop').click(); expect((await stop).ok()).toBe(true);
    await expect.poll(status, { timeout: 30_000 }).toBe('stopped');
  } finally {
    await page.screenshot({ path: info.outputPath('inspector-status-controls.png') });
    release();
  }
  expect(errors).toEqual([]);
});
