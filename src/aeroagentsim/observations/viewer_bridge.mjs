// Persistent JSON-lines bridge: browser lifecycle is independent of capture count.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import readline from 'node:readline';
const [modules, viewerURL, executablePath, gl] = process.argv.slice(2);
const { chromium } = await import(pathToFileURL(path.join(modules, 'playwright/index.mjs')).href);
let browser, page;
try {
  browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}), args: gl === 'software' ? ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] : [] });
  page = await browser.newPage();
  await page.goto(viewerURL, { waitUntil: 'domcontentloaded' });
  const lines = readline.createInterface({ input: process.stdin });
  for await (const line of lines) {
    try {
      const { request, label, service_run_id } = JSON.parse(line);
      await page.setViewportSize({ width: request.width, height: request.height });
      await page.waitForFunction(() => typeof window.aeroCapture?.render === 'function', undefined, { timeout: request.timeout_s * 1000 });
      const frame = await page.evaluate(async ({ request, label, service_run_id }) => {
        return await window.aeroCapture.render({ request, label, service_run_id });
      }, { request, label, service_run_id });
      if (!frame || typeof frame.png_data_url !== 'string' || !frame.png_data_url.startsWith('data:image/png;base64,')) {
        throw new Error('viewer must return actual PNG bytes and applied request metadata');
      }
      // Echo only metadata reported after the viewer has applied and verified its cut.
      process.stdout.write(JSON.stringify({ request: frame.request, png_base64: frame.png_data_url.slice(22) }) + '\n');
    } catch (error) {
      process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
    }
  }
} catch (error) {
  process.stdout.write(JSON.stringify({ error: String(error) }) + '\n');
  process.exitCode = 1;
} finally {
  await browser?.close();
}
