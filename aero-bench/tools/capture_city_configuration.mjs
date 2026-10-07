import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeFileSync, mkdirSync } from 'node:fs';
import { parseArgs } from 'node:util';
const { values } = parseArgs({options: {
  origin: {type: 'string'}, output: {type: 'string'}, registration: {type: 'string'},
}});
for (const key of ['origin', 'output', 'registration']) {
  if (!values[key]) throw new Error(`Missing --${key}`);
}
const out = resolve(values.output);
mkdirSync(out, {recursive: true});
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { chromium } = createRequire(resolve(root, 'frontend/package.json'))('@playwright/test');
const start = performance.now();
const browser = await chromium.launch({ channel: 'chromium', headless: true,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 },
  recordVideo: { dir: resolve(out, 'configuration-video'), size: { width: 1600, height: 1000 } } });
const page = await context.newPage();
page.setDefaultTimeout(60000);
try {
  await page.goto(`${new URL(values.origin).origin}/city-studio.html?tab=compile`);
  await page.getByLabel('原生场景注册').selectOption(values.registration);
  await page.getByRole('button', { name: '载入所选注册的参考草稿', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('#studio-save-status')?.textContent?.includes('参考草稿已载入') || document.body.innerText.includes('参考草稿载入失败：'), null, { timeout: 120000 });
  const bodyText = await page.locator('body').innerText();
  if (bodyText.includes('参考草稿载入失败：')) throw new Error(bodyText.match(/参考草稿载入失败：[^\n]*/)[0]);
  await page.locator('#studio-save').click();
  await page.waitForFunction(() => document.querySelector('#studio-save-status')?.dataset.state === 'saved');
  await page.screenshot({ path: resolve(out, 'configuration-saved.png') });
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#studio-export').click();
  await (await downloadPromise).saveAs(resolve(out, 'saved-workspace.json'));
  const replyPromise = page.waitForResponse(response => response.url().includes('/authoring/v1/compilations') && response.request().method() === 'POST');
  await page.getByRole('button', { name: '编译当前草稿', exact: true }).click();
  const response = await replyPromise;
  const result = await response.json();
  writeFileSync(resolve(out, 'compilation-result.json'), JSON.stringify(result, null, 2));
  if (result.status !== 'compiled') throw new Error(JSON.stringify(result.blockers));
  await page.getByText('编译完成（尚未执行、尚未验证）', { exact: true }).waitFor();
  await page.screenshot({ path: resolve(out, 'configuration-compiled.png') });
  console.log(JSON.stringify({ compilation_id: result.compilation_id,
    run_id: result.runs[0].run_id, saved: true, elapsed_s: (performance.now() - start) / 1000 }));
} catch (error) {
  await page.screenshot({ path: resolve(out, `configuration-error-${Date.now()}.png`) });
  console.error(error.message);
  console.error((await page.locator('body').innerText()).slice(-7000));
  process.exitCode = 1;
} finally { await context.close(); await browser.close(); }
