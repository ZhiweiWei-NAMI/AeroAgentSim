// Task-owned capture driver for the isolated host mount example (PR12 light
// style pass). Uses ONLY the existing Playwright installation in
// aero-bench/frontend/node_modules and the already-cached Chromium under
// ~/.cache/ms-playwright (no installs, no downloads). It starts the host's own
// loopback server (server.mjs) as a child process, drives the REAL demo page
// (demo.js feed, one host cursor) to demo tick 44 with parcel.p1042 selected,
// captures desktop + mobile screenshots for zh and en, then stops the server.
//
// Usage: node capture.mjs [outdir]
// Screenshots are actual captures of the demo page; no pixel or DOM data is
// authored here. Asserted state is read back from the live page and recorded
// in screenshots/capture.json.
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
// A fresh clone uses its installed sibling frontend dependency. A caller may
// explicitly select an existing local install; a missing module remains an error.
const playwrightModule = process.env.P02_PLAYWRIGHT_MODULE
  ?? path.join(here, '..', 'frontend', 'node_modules', 'playwright');
const { chromium } = require(playwrightModule);

const PORT = 4493;
const TICK = 44; // demo tick: RSSI degradation window, rule truth true, parcel holding
const PARCEL = 'parcel.p1042';
const outDir = path.resolve(process.argv[2] ?? path.join(here, 'screenshots'));

const server = spawn(process.execPath, [path.join(here, 'server.mjs')], {
  env: { ...process.env, PORT: String(PORT) },
  stdio: ['ignore', 'pipe', 'pipe'],
});
const serverOutput = [];
server.stdout.on('data', d => serverOutput.push(String(d)));
server.stderr.on('data', d => serverOutput.push(String(d)));

async function waitForServer(url, tries = 50) {
  for (let i = 0; i < tries; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch { /* not listening yet */ }
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error(`host server did not become ready at ${url}`);
}

const baseUrl = `http://127.0.0.1:${PORT}`;
try {
  await waitForServer(baseUrl);
  await mkdir(outDir, { recursive: true });
  const browser = await chromium.launch();
  const report = { base: baseUrl, tick: TICK, files: {}, pageErrors: [] };
  const startedAt = new Date().toISOString();

  for (const language of ['zh', 'en']) {
    for (const mobile of [false, true]) {
      const label = `${language}${mobile ? '-mobile' : ''}`;
      const context = await browser.newContext({
        viewport: mobile ? { width: 390, height: 844 } : { width: 1600, height: 1000 },
        deviceScaleFactor: 2,
        isMobile: mobile,
        hasTouch: mobile,
      });
      const page = await context.newPage();
      page.on('pageerror', e => report.pageErrors.push(`pageerror(${label}): ${e.message}`));
      page.on('console', m => {
        if (m.type() === 'error') report.pageErrors.push(`console(${label}): ${m.text()}`);
      });
      await page.goto(`${baseUrl}/?lang=${language}`, { waitUntil: 'networkidle' });
      console.error(`[capture] ${label}: page loaded, waiting for host cursor`);
      const ready = await page.waitForFunction(() => window.__parcelHost?.activeCursor, null, { timeout: 20000 });
      if (!ready) throw new Error(`host cursor not available (${label})`);
      // Drive the single host cursor to the demo tick; no second clock.
      await page.evaluate(([tick, id]) => {
        window.__parcelHost.cursor.seek(tick);
        window.__parcelHost.selectTarget(id);
      }, [TICK, PARCEL]);
      console.error(`[capture] ${label}: seek(${TICK}) + select(${PARCEL}) done, waiting for scene marker`);
      await page.waitForFunction(
        ([id]) => document.querySelector(`[data-entity-id="${id}"].selected`) !== null,
        [PARCEL], { timeout: 20000 },
      );
      await page.waitForTimeout(250);
      const state = await page.evaluate(([id]) => {
        const marker = document.querySelector(`[data-entity-id="${id}"].selected`);
        return {
          timeText: document.getElementById('time-value')?.textContent ?? null,
          tickText: document.getElementById('tick-value')?.textContent ?? null,
          selectedJson: document.getElementById('selection-json')?.textContent ?? null,
          sourceMode: document.getElementById('source-mode')?.textContent ?? null,
          ruleTruth: document.querySelector('.truth-chip')?.textContent ?? null,
          stateBanner: document.querySelector('.state-banner')?.textContent?.trim() ?? null,
          sceneBackground: getComputedStyle(document.querySelector('.park-scene')?.querySelector('rect') ?? document.body).fill ?? null,
          rootBackground: getComputedStyle(document.body).backgroundColor,
          selectedMarker: marker !== null,
          identityNotePresent: document.querySelector('.identity-note') !== null,
        };
      }, [PARCEL]);
      const file = `host-demo-${label}.png`;
      await page.screenshot({ path: path.join(outDir, file), fullPage: true });
      report.files[file] = { language, mobile, tick: TICK, selection: PARCEL, ...state };
      await context.close();
    }
  }
  await browser.close();
  report.capturedAt = new Date().toISOString();
  report.startedAt = startedAt;
  report.tool = 'playwright (existing frontend/node_modules + cached chromium); no installs';
  report.demoFeed = 'authored demo motion + labelled demo parcel/custody/rule evidence (authority host-demo-evidence)';
  report.evidence = 'fixture-only: labelled demo host evidence; no real BENCH motion and no Atlas runtime evidence in any capture';
  report.realBenchMotion = false;
  report.realBenchMotionReason = 'No sealed public replay artifact exists in this export; captures show the demo feed only.';
  report.visualAcceptance = 'pending — captures are actual page states, but visual acceptance of the PR12 light style belongs to the parent review';
  await writeFile(path.join(outDir, 'capture.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report, null, 2));
} finally {
  server.kill('SIGTERM');
  await new Promise(r => server.once('exit', r));
  console.error(`[capture] demo server stopped; server log: ${serverOutput.join('').trim().split('\n').at(-1) ?? ''}`);
}
