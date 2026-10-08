import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

test('author local city scenario, run the kernel and open real replay', async ({ page, request }) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto('/studio?api=http://127.0.0.1:8017');
  await page.getByLabel('Workspace name', { exact: true }).fill(`P7b ${Date.now()}`);
  await page.getByRole('button', { name: 'Create workspace', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Place entity', exact: true })).toBeVisible();
  const map = page.getByRole('img', { name: 'Region selector map' });
  await expect(map).toBeVisible();
  const rect = await map.boundingBox(); expect(rect).toBeTruthy();
  await page.mouse.move(rect!.x + rect!.width * .35, rect!.y + rect!.height * .35);
  await page.mouse.down(); await page.mouse.move(rect!.x + rect!.width * .65, rect!.y + rect!.height * .65); await page.mouse.up();
  await page.getByRole('button', { name: 'Select region', exact: true }).click();
  await expect(page.getByText(/Source diagnostics \(/)).toBeVisible({ timeout: 30_000 });
  for (let i = 1; i <= 2; i++) {
    await page.getByLabel('Entity ID', { exact: true }).fill(`uav-${i}`);
    await page.getByLabel('East', { exact: true }).fill(String((i-1)*20));
    await page.getByLabel('North', { exact: true }).fill(String((i-1)*12));
    await page.getByRole('button', { name: 'Place entity', exact: true }).click();
    await expect(page.getByRole('cell', { name: `uav-${i}`, exact: true })).toBeVisible({ timeout: 30_000 });
  }
  await page.getByLabel('Placement kind', { exact: true }).first().locator('.ant-select-selector').click();
  await page.locator('.ant-select-item-option').filter({hasText: /^facility$/}).click({timeout: 10000});
  await page.getByRole('button', { name: 'Place facility', exact: true }).click();
  await expect(page.getByRole('cell', { name: 'facility-1', exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByText('Scenario valid', { exact: true })).toBeVisible({ timeout: 30_000 });
  mkdirSync('test-results', { recursive: true });
  await page.screenshot({ path: 'test-results/p7b-studio.png', fullPage: true });
  await page.getByRole('button', { name: 'Run now', exact: true }).click();
  const replay = page.getByRole('link', { name: 'Open replay', exact: true });
  await expect(replay).toBeVisible({ timeout: 30_000 });
  const href = await replay.getAttribute('href'); const id = href!.split('/runs/')[1].split('?')[0];
  await expect.poll(async () => { const response = await request.get('http://127.0.0.1:8017/v1/runs'); return (await response.json()).find((r: { id:string }) => r.id===id)?.status; }, { timeout: 30_000 }).toBe('completed');
  const header = await (await request.get(`http://127.0.0.1:8017/v1/runs/${id}/header`)).json();
  expect(header.scene.city.kind).toBe('geojson');
  const commits = await (await request.get(`http://127.0.0.1:8017/v1/runs/${id}/commits`)).json();
  expect(commits.commits.length).toBeGreaterThan(0);
  await replay.click();
  await expect(page.getByTestId('viewport')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'test-results/p7b-replay.png', fullPage: true });
  expect(errors).toEqual([]);
});
