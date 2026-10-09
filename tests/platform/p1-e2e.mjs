import { createRequire } from 'node:module';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
const require = createRequire(new URL('../../frontend/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const api = process.env.P1_API ?? 'http://127.0.0.1:8002';
const artifactRoot = process.env.P1_SCREENSHOTS ?? 'tests/platform/screenshots';
// Preserve authored JSON decimal tokens: JS stringify would turn 10.0 into 10.
const body = process.env.P1_SCENARIO_BODY ? '{"scenario":'+readFileSync(process.env.P1_SCENARIO_BODY,'utf8')+'}' : JSON.stringify({scenario_path:'scenarios/p1-slice.yaml'});
const response = await fetch(`${api}/v1/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
if (!response.ok) throw Error(await response.text());
const run = await response.json();
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ?? process.env.AEROAGENTSIM_CHROMIUM, headless: true, args: ['--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', error => errors.push(error.message));
  let streams = 0;
  // One actual transport opening fails; the client must resume the recorded cursor.
  await page.route('**/stream?*', async route => { if (++streams === 1) await route.abort('connectionreset'); else await route.continue(); });
  await page.goto(`${api}/runs/${encodeURIComponent(run.id)}?mode=live`);
  await page.getByTestId('viewport').waitFor();
  await expect(page.getByText('running', { exact: true })).toBeVisible({ timeout: 60000 });
  const count = () => page.locator('footer').innerText().then(text => Number(/(\d+) commits/.exec(text)?.[1]));
  let initial = await count();
  await expect.poll(count, { timeout: 60000 }).toBeGreaterThan(initial);
  // Freeze a knowledge cut while more journal records arrive.
  const slider = page.getByRole('slider');
  await slider.focus(); await page.keyboard.press('Home');
  const frozen = await slider.getAttribute('aria-valuenow');
  initial = await count();
  await expect.poll(count, { timeout: 60000 }).toBeGreaterThan(initial);
  await expect(slider).toHaveAttribute('aria-valuenow', frozen);
  await page.getByRole('button', { name: 'Follow live', exact: true }).click();
  await expect(page.getByText('completed', { exact: true })).toBeVisible({ timeout: 180000 });
  const terminal = (await (await fetch(`${api}/v1/runs`)).json()).find(row => row.id === run.id);
  const finalCursor = terminal.final_cursor;
  if (!Number.isSafeInteger(finalCursor)) throw Error('Terminal manifest lacks final cursor');
  await expect.poll(count, { timeout: 60000 }).toBe(finalCursor - 1);
  const recorded = await (await fetch(`${api}/v1/runs/${run.id}/commits?from=1&limit=4096`)).json();
  if (!recorded.commits.some(commit => commit.edges.length)) throw Error('No actual relation edges');
  if (streams < 2) throw Error('Growing SSE reconnect was not exercised');
  mkdirSync(artifactRoot, { recursive: true });
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(artifactRoot, 'p1-slice-live.png'), fullPage: true });
  await page.locator('div[aria-label="Feed mode"]').click();
  await Promise.all([page.waitForResponse(response=>response.url().endsWith(`/v1/runs/${run.id}/header`)),page.getByText('Replay',{exact:true}).last().click()]);
  await expect.poll(count, { timeout: 60000 }).toBe(finalCursor - 1);
  await page.getByRole('slider').focus(); await page.keyboard.press('End');
  await expect(page.getByTestId('inspector')).toContainText('aas.motion.move_to');
  await expect(page.getByTestId('inspector')).toContainText('succeeded');
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(artifactRoot, 'p1-slice-replay.png'), fullPage: true });
  // Chromium reports the intentionally aborted request as a console transport error.
  const unexpected = errors.filter(error => !error.includes('ERR_CONNECTION_RESET'));
  if (unexpected.length) throw Error(`Browser errors: ${JSON.stringify(unexpected)}`);
  console.log(JSON.stringify({ run: run.id, finalCursor, streams, commits: finalCursor - 1, console_errors: unexpected.length, screenshots: artifactRoot }));
} finally { await browser.close(); }
