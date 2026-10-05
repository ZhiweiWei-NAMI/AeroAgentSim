/** Temporary U-worker screenshot capture: viewer + studio, before/after UI polish.
 * Not part of the repo; deleted after use. */
import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';

const origin = process.argv[2];
const outDir = process.argv[3];
const insecure = process.argv[4] === 'insecure';
await mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true, ignoreHTTPSErrors: insecure });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, ignoreHTTPSErrors: insecure });
const errors = [];
page.on('pageerror', e => errors.push(String(e)));

async function shoot(path, name, waitSelectorHidden) {
  await page.goto(`${origin}${path}`, { waitUntil: 'load', timeout: 60000 });
  if (waitSelectorHidden) {
    await page.waitForSelector(waitSelectorHidden, { state: 'hidden', timeout: 90000 }).catch(e => console.log('wait-hidden timeout', waitSelectorHidden, String(e)));
  }
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false });
  console.log('captured', name);
}

await shoot('/?scene=1&city=/city-presentation/default-scene-v1.json', 'viewer-full', '.loading-progress');
await page.waitForTimeout(500);
// Try toggling perf overlay via query/keypress F9 if supported.
try {
  await page.keyboard.press('F9');
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${outDir}/viewer-perf-overlay.png` });
  console.log('captured viewer-perf-overlay');
} catch (e) { console.log('perf overlay toggle skipped', String(e)); }

await shoot('/city-studio.html', 'studio-full', '.studio-panel-loading');

if (errors.length) console.log('page errors:', errors.slice(0, 10));
await browser.close();
