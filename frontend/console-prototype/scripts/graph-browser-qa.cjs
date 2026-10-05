/* Run only against an authorized preview. Does not start or bypass a preview host. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const baseURL = process.env.P02_CONSOLE_URL;
if (!baseURL) throw new Error('Set P02_CONSOLE_URL to an authorized preview before running visual checks.');
const output = process.env.P02_EVIDENCE_DIR || '/tmp/aero-graph-browser-evidence';
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const views = [{ width: 1440, height: 900 }, { width: 390, height: 844 }];
const locales = ['zh-CN', 'en-US'];
const reports = [];
(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.P02_CHROMIUM || '/usr/bin/chromium', headless: true });
  try {
    for (const viewport of views) for (const locale of locales) {
      const context = await browser.newContext({ viewport, acceptDownloads: true });
      const page = await context.newPage();
      const report = { viewport, locale, commit: revision, errors: [], requests: [], captures: [] };
      page.on('pageerror', error => report.errors.push(error.message));
      page.on('request', request => report.requests.push({ method: request.method(), url: request.url() }));
      page.on('requestfailed', request => report.errors.push(`${request.url()}: ${request.failure()?.errorText}`));
      page.on('response', response => { if (response.status() >= 400) report.errors.push(`HTTP ${response.status()} ${response.url()}`); });
      const click = selector => page.locator(selector).first().click();
      const inspect = async (collection, id) => {
        await click(`[data-gw-action="collection"][data-key="${collection}"]`);
        await click(`.gw-inventory [data-gw-action="select"][data-id="${id}"]`);
      };
      const capture = async name => {
        await page.evaluate(() => window.scrollTo(0, 0));
        const geometry = await page.evaluate(() => ({ viewport: innerWidth, document: document.body.scrollWidth }));
        assert.ok(geometry.document <= geometry.viewport + 1, `Page overflow at ${name}`);
        const filename = `${viewport.width}x${viewport.height}-${locale}-${name}.png`;
        await page.screenshot({ path: path.join(output, filename), fullPage: true });
        report.captures.push({ name, filename, geometry });
      };
      await page.goto(baseURL, { waitUntil: 'networkidle' });
      await page.selectOption('#locale', locale);
      await click('[data-tab="graph"]');
      await capture('entity-ownership');
      await inspect('commands', 'return-alpha');
      assert.ok(await page.locator('.gw-inspection').getByText('navigate-home', { exact: true }).count());
      await capture('command-behaviors');
      await inspect('predicates', 'energy-link-risk');
      assert.ok(await page.locator('.gw-ast').count());
      await capture('multi-state-rule');
      await inspect('strategies', 'alpha-strategy');
      assert.ok(await page.locator('.gw-edge-label').count());
      await click('.gw-edge-label');
      assert.ok(await page.locator('.gw-edge-inspector').count());
      await capture('typed-edge-scope');
      await click('[data-gw-action="close-edge"]');
      await click('.gw-inspection [data-gw-action="edit"]');
      const record = JSON.parse(await page.inputValue('#gw-record-json'));
      record.label = 'Reviewed local strategy';
      await page.fill('#gw-record-json', JSON.stringify(record, null, 2));
      await click('[data-gw-action="review"]');
      assert.ok(await page.locator('.gw-change-review').count());
      await capture('record-review');
      await click('[data-gw-action="cancel-edit"]');
      await page.selectOption('#gw-scenario', 'constraint-change');
      await click('[data-gw-action="execute"]');
      assert.match(await page.locator('.gw-result').textContent(), /PERMIT/);
      assert.match(await page.locator('.gw-result').textContent(), /BLOCK/);
      await capture('constraint-feedback');
      await page.reload({ waitUntil: 'networkidle' });
      assert.equal(await page.getAttribute('html', 'lang'), locale);
      assert.deepEqual(report.errors, []);
      const allowedOrigin = new URL(baseURL).origin;
      assert.ok(report.requests.every(request => request.method === 'GET' && new URL(request.url).origin === allowedOrigin), 'Only same-origin preview reads are allowed');
      reports.push(report);
      await context.close();
    }
  } finally {
    await browser.close();
    await fs.writeFile(path.join(output, 'graph-browser-report.json'), JSON.stringify(reports, null, 2));
  }
  console.log(`Verified ${reports.length} viewport/language groups. Evidence: ${output}`);
})().catch(error => { console.error(error); process.exitCode = 1; });
