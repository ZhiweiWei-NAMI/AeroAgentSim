import { expect, test } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createServer, type AddressInfo } from 'node:net';
import { resolve } from 'node:path';

// The conversation runs against the deterministic scripted model; everything
// else (compile, validation, apply, initialize) is the real platform.
let model: ChildProcess | undefined, endpoint = '';
const freePort = () => new Promise<number>((done, fail) => {
  const server = createServer().once('error', fail).listen(0, '127.0.0.1', () => { const { port } = server.address() as AddressInfo; server.close(() => done(port)); });
});

test.beforeAll(async () => {
  const port = await freePort();
  model = spawn(process.env.AEROAGENTSIM_PYTHON ?? resolve('..', '.venv/bin/python'), [resolve('..', 'tools/assistant/fake_model.py'), '--port', String(port)], { stdio: 'ignore' });
  endpoint = `http://127.0.0.1:${port}/v1`;
  await expect.poll(async () => { try { return (await fetch(`${endpoint}/models`)).ok; } catch { return false; } }, { timeout: 20_000 }).toBe(true);
});
test.afterAll(() => { model?.kill(); });

test('describe, review, revise, confirm and initialize a scenario in conversation', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('wb-describe').click();
  await expect(page.getByTestId('wb-assistant')).toBeVisible();

  // Connect the model API from the panel; the key never comes back to the browser.
  // A scratch root reused from an earlier run already has a connection: reopen it.
  await expect(page.getByTestId('as-connect').or(page.getByTestId('as-input'))).toBeVisible({ timeout: 30_000 });
  if (!await page.getByTestId('as-connect').isVisible()) await page.getByTestId('as-model-chip').click();
  if (!await page.getByTestId('as-connect-url').isVisible()) await page.getByTestId('as-connect').getByRole('button', { name: 'Change' }).click();
  await page.getByTestId('as-connect-url').fill(endpoint);
  await page.getByTestId('as-connect-model').fill('assistant-scripted');
  await page.getByTestId('as-connect-key').fill('scripted-key');
  await page.getByTestId('as-connect-save').click();
  await expect(page.getByTestId('as-input')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('as-connect-key')).toHaveCount(0);

  // A proposal is a preview: nothing exists until Confirm.
  await page.getByTestId('as-input').fill('Can we create a scenario that mixes food delivery and logistics?');
  await page.getByTestId('as-send').click();
  const board = page.getByTestId('as-plan-board');
  await expect(board).toHaveAttribute('data-stage', 'proposed', { timeout: 120_000 });
  await expect(page.getByTestId('as-plan-state')).toContainText(/v1/);
  await expect(page.getByTestId('as-stage-tag')).toBeVisible();
  await expect(page.getByTestId('as-question')).toContainText('decide for itself');
  await expect(page).toHaveURL(/\/(\?|$)/);

  await page.getByTestId('as-tab-interactions').click();
  await expect(page.getByTestId('as-diagram')).toBeVisible();
  await expect(page.getByTestId('as-seq-step').first()).toBeVisible();

  // A natural revision makes a new version and explains what changed.
  await page.getByTestId('as-input').fill('Let the courier decide');
  await page.getByTestId('as-send').click();
  await expect(page.getByTestId('as-version-2')).toBeVisible({ timeout: 120_000 });
  await page.getByTestId('as-tab-changes').click();
  await expect(board.getByTestId('as-delta').first()).toContainText('Cooperation rules');
  await expect(board).toContainText('agent');

  await expect(page.getByTestId('as-validation')).toHaveAttribute('data-state', 'valid', { timeout: 180_000 });
  await page.getByTestId('as-confirm').click();
  await expect(board).toHaveAttribute('data-stage', 'configured', { timeout: 60_000 });
  await expect(page.getByTestId('as-apply-report')).toBeVisible();
  await expect(page).toHaveURL(/\/w\/studio-/);

  // Switching to the form keeps the same project and scene.
  const url = page.url().split('?')[0];
  await page.getByTestId('wb-assistant').getByTestId('as-mode-form').click();
  await expect(page.getByTestId('wb-config')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('wb-assistant')).toHaveCount(0, { timeout: 10_000 });
  expect(page.url().split('?')[0]).toBe(url);
  await page.getByTestId('wb-config').getByTestId('as-mode-chat').click();
  await expect(page.getByTestId('wb-config')).toHaveCount(0, { timeout: 10_000 });
  await expect(page.getByTestId('wb-assistant')).toBeVisible();

  await page.getByTestId('as-initialize').click();
  await expect(page.getByTestId('wb-start')).toBeVisible({ timeout: 180_000 });
  await expect(page).toHaveURL(/run=run-/);
});
