import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

test('real parcel experiment: matched seeds, progress, measured results and a child replay', async ({ page, baseURL }) => {
  test.setTimeout(115_000);
  if (!baseURL) throw Error('Experiment e2e requires the real console backend');
  const screenshots = process.env.AEROAGENTSIM_UX_SCREENSHOTS ?? test.info().outputDir;
  mkdirSync(screenshots, { recursive: true });
  await page.goto(`/?api=${encodeURIComponent(baseURL)}`);
  await expect(page.getByTestId('template-parcel-delivery')).toBeVisible();
  if (await page.getByTestId('welcome-close').isVisible()) await page.getByTestId('welcome-close').click();
  await page.getByTestId('gallery-experiment').click();
  const panel = page.getByTestId('experiment-panel');
  await panel.getByTestId('experiment-template').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('Urban parcel delivery', { exact: true }).click();
  await panel.getByTestId('exp-knob-allocator').check();
  for (const choice of ['nearest', 'auction']) {
    await panel.getByTestId('exp-add-allocator').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(choice, { exact: true }).click();
  }
  await panel.getByRole('spinbutton', { name: 'Repeats per configuration', exact: true }).fill('2');
  await panel.getByRole('spinbutton', { name: 'Parallel runs', exact: true }).fill('2');
  await panel.getByRole('spinbutton', { name: 'Simulation duration in seconds', exact: true }).fill('120');
  await expect(panel.getByTestId('experiment-preview')).toHaveText(/2 configurations × 2 repeats = 4 runs/);
  const created = page.waitForResponse(response => response.url().endsWith('/v1/templates/parcel-delivery/experiments') && response.request().method() === 'POST');
  await panel.getByTestId('experiment-start').click();
  const response = await created;
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON()).toEqual({ vary: { allocator: ['nearest', 'auction'] }, seeds: 2, jobs: 2, horizon_s: 120 });
  const submitted = await response.json();
  expect(submitted.total).toBe(4);
  await expect(panel.getByTestId('experiment-report')).toBeVisible();
  await expect(panel.getByRole('progressbar', { name: 'Experiment progress' })).toBeVisible();
  await page.screenshot({ path: join(screenshots, 'experiment-progress.png') });
  const progressReply = page.waitForResponse(reply => reply.url().endsWith(`/v1/experiments/${submitted.id}`));
  await expect(panel.getByTestId('experiment-report')).toHaveText(/Completed — 4 of 4 runs succeeded/, { timeout: 85_000 });
  expect((await progressReply).ok()).toBe(true);
  const reportResponse = await page.request.get(`/v1/experiments/${submitted.id}`);
  expect(reportResponse.ok()).toBe(true);
  const report = await reportResponse.json();
  expect(report.failed).toBe(0); expect(report.completed).toBe(4);
  expect(report.table).toHaveLength(2);
  for (const option of report.table) {
    expect(option.metrics.tasks_completed.n).toBe(2);
    const cell = panel.getByTestId(`exp-cell-${option.config_id}-tasks_completed`);
    await expect(cell).toContainText(String(option.metrics.tasks_completed.mean));
    if (option.metrics.tasks_completed.best) await expect(cell).toHaveClass(/is-best/);
  }
  await test.info().attach('real-experiment-report', { body: JSON.stringify(report, null, 2), contentType: 'application/json' });
  await panel.getByTestId('experiment-results').scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(screenshots, 'experiment-result.png') });
  const run = report.runs[0];
  await panel.getByTestId(`exp-open-run-${run.config_id}-seed-${run.seed}`).click();
  await page.waitForURL(new RegExp(`/runs/${run.run_id}`));
  await expect(page.getByTestId('simple-run-view')).toBeVisible({ timeout: 15_000 });
  await page.getByRole('tab', { name: 'Results', exact: true }).click();
  await expect(page.getByText('Tasks completed', { exact: true })).toBeVisible();
});
