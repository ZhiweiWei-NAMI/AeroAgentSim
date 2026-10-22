// Persistent JSON-lines bridge: browser lifecycle is independent of capture count.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import readline from 'node:readline';
import { writeSync, closeSync } from 'node:fs';
const [modules, viewerURL, executablePath, gl] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
let browser, page;
let readyFd = process.env.AEROAGENTSIM_RENDERER_READY_FD === undefined ? undefined : Number(process.env.AEROAGENTSIM_RENDERER_READY_FD);
const startup = reply => {
  if (readyFd === undefined) return;
  writeSync(readyFd, JSON.stringify(reply) + '\n'); closeSync(readyFd); readyFd = undefined;
};
try {
  browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}), args: ['--no-proxy-server', ...(gl === 'software' ? ['--use-gl=swiftshader', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : [])] });
  page = await browser.newPage();
  await page.goto(viewerURL, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.aeroCapture?.render === 'function', undefined, { timeout: 30_000 });
  // Use a dedicated channel: terminal diagnostics are not protocol replies,
  // and stdout remains the exact frame protocol for already-running clients.
  startup({ contract: 'aeroagentsim.viewer-ready/v1' });
  const lines = readline.createInterface({ input: process.stdin });
  for await (const line of lines) {
    try {
      const payload = JSON.parse(line);
      if (payload.operation === 'prepare') {
        const ready = await page.evaluate(async preparation => {
          if (typeof window.aeroCapture.prepare !== 'function') throw Error('Viewer cannot initialize its pinned camera assets');
          return await window.aeroCapture.prepare(preparation);
        }, payload.preparation);
        process.stdout.write(JSON.stringify(ready) + '\n');
        continue;
      }
      const { request, label, service_run_id, scene } = payload;
      await page.setViewportSize({ width: request.width, height: request.height });
      const frame = await page.evaluate(async ({ request, label, service_run_id, scene }) => {
        return await window.aeroCapture.render({ request, label, service_run_id, scene });
      }, { request, label, service_run_id, scene });
      if (!frame || typeof frame.png_data_url !== 'string' || !frame.png_data_url.startsWith('data:image/png;base64,')) {
        throw new Error('viewer must return actual PNG bytes and applied request metadata');
      }
      // Echo only metadata reported after the viewer has applied and verified its cut.
      process.stdout.write(JSON.stringify({ request: frame.request, png_base64: frame.png_data_url.slice(22),
        ...(frame.depth ? { depth: frame.depth, calibration: frame.calibration, camera_pose: frame.camera_pose } : {}) }) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
    }
  }
} catch (error) {
  startup({ error: String(error) });
  process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
  process.exitCode = 1;
} finally {
  await browser?.close();
}
