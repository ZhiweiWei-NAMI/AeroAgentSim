/* Actual Chromium interactions and screenshots. No server or simulation calls. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const output = process.env.P02_EVIDENCE_DIR || '/workspace/scratch/p02-browser-evidence';
const baseURL = process.env.P02_CONSOLE_URL || 'http://127.0.0.1:4317/';
const executablePath = process.env.P02_CHROMIUM || '/usr/bin/chromium';
const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
const locales = ['zh-CN', 'en-US'];
const viewports = [{ width: 1440, height: 900 }, { width: 390, height: 844 }];
const cases = [];

(async () => {
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ executablePath, headless: true });
  try {
    for (const viewport of viewports) for (const locale of locales) {
      const context = await browser.newContext({ viewport, acceptDownloads: true });
      const page = await context.newPage();
      const errors = [], requests = [], images = [], interactions = [], overflow = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
      page.on('request', request => requests.push({ method: request.method(), url: request.url() }));
      page.on('requestfailed', request => errors.push(`${request.url()}: ${request.failure()?.errorText}`));
      page.on('response', response => { if (response.status() >= 400) errors.push(`HTTP ${response.status()} ${response.url()}`); });
      const click = async action => page.locator(`[data-action="${action}"]`).first().click();
      const nav = async id => page.locator(`[data-nav="${id}"]`).click();
      const tab = async id => page.locator(`[data-tab="${id}"]`).click();
      const saved = () => page.evaluate(() => JSON.parse(localStorage.getItem('aero-console.workspace.v1')));
      const field = async (selector, value) => {
        await page.locator(selector).fill(String(value));
        await page.locator(selector).dispatchEvent('change');
      };
      const screenshot = async scenario => {
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.locator('#toast').evaluate(el => el.classList.add('hidden'));
        const layout = await page.evaluate(() => ({ body: document.body.scrollWidth, viewport: innerWidth }));
        assert.ok(layout.body <= layout.viewport + 1, `${scenario}: document horizontally overflows`);
        overflow.push({ scenario, ...layout });
        const filename = `${viewport.width}x${viewport.height}-${locale}-${scenario}.png`;
        // Full-page images retain the exact viewport width. Dialog captures show
        // the viewport so mobile fixed headers/footers can be visually inspected.
        await page.screenshot({ path: path.join(output, filename), fullPage: !await page.locator('[role="dialog"]').count() });
        images.push({ filename, scenario, viewport, locale, tested_commit: revision, full_page: !await page.locator('[role="dialog"]').count() });
      };

      await page.goto(baseURL, { waitUntil: 'networkidle' });
      await page.selectOption('#locale', locale);
      assert.equal(await page.getAttribute('html', 'lang'), locale);
      const contrast = await page.evaluate(() => {
        const luminance = color => {
          const rgb = color.match(/[\d.]+/g).slice(0, 3).map(value => Number(value) / 255)
            .map(value => value <= .04045 ? value / 12.92 : Math.pow((value + .055) / 1.055, 2.4));
          return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
        };
        return ['.panel-description', '.field-hint', '.field label', '.button.primary', '.nav-button.active'].map(selector => {
          const element = document.querySelector(selector), foreground = getComputedStyle(element).color;
          let background;
          for (let node = element; node; node = node.parentElement) {
            const color = getComputedStyle(node).backgroundColor;
            if (color !== 'rgba(0, 0, 0, 0)') { background = color; break; }
          }
          const a = luminance(foreground), b = luminance(background);
          return { selector, foreground, background, ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05) };
        });
      });
      assert.ok(contrast.every(item => item.ratio >= 4.5), 'Sampled normal-text contrast must be at least 4.5');
      await screenshot('scenario');
      await field('#field-metadata-name', 'Campus review · 用户输入');
      await click('save');
      assert.equal((await saved()).versions.length, 1);
      await field('#field-scenario-step_ms', 0);
      assert.equal(await page.locator('[data-action="prepare"]').isDisabled(), true);
      await click('validate');
      assert.ok((await page.locator('.error-text').first().innerText()).length > 0);
      await screenshot('validation');
      const validationLayout=await page.locator('.validation-notice').evaluate(el=>({display:getComputedStyle(el).display,width:el.clientWidth,children:[...el.children].map(c=>({width:c.getBoundingClientRect().width,font:parseFloat(getComputedStyle(c).fontSize)}))}));
      assert.equal(validationLayout.display,'block');
      assert.ok(validationLayout.children.every(c=>c.width>=validationLayout.width-30));
      await field('#field-scenario-step_ms', 100);
      assert.equal(await page.locator('[data-action="prepare"]').isDisabled(), false);
      interactions.push('Save immutable baseline; invalid step disables prepare; recovery reenables it');

      await tab('entities'); await click('entity-edit');
      await page.fill('#entity-east', '123.45');
      await page.fill('#entity-id', 'INVALID ID'); await click('entity-save');
      assert.ok((await page.locator('#modal-error').innerText()).length > 0);
      const alternate = locale === 'zh-CN' ? 'en-US' : 'zh-CN';
      await page.selectOption('#modal-locale', alternate);
      assert.equal(await page.inputValue('#entity-east'), '123.45');
      assert.equal(await page.inputValue('#entity-id'), 'INVALID ID');
      await page.selectOption('#modal-locale', locale);
      await page.fill('#entity-id', 'uav-alpha');
      await screenshot('entity-editor');
      const modalFooter = await page.locator('.modal-footer').boundingBox();
      assert.ok(modalFooter.y + modalFooter.height <= viewport.height + 1, 'Modal footer is clipped');
      await click('entity-save'); assert.equal((await saved()).draft.entities[0].position_enu_m[0], 123.45);
      await click('entity-add'); await page.keyboard.press('Escape');
      assert.equal(await page.locator('[role="dialog"]').count(), 0);
      await click('entity-add'); await click('modal-close');
      interactions.push('Reject invalid ID; preserve unsaved modal fields across both language switches; save entity; Escape and Close dismiss');

      await tab('network');
      await screenshot('network-profiles');
      const profile = id => page.locator(`[data-action="study-preview"][data-id="${id}"]`);
      await profile('R1').click(); await click('modal-close');
      assert.equal((await saved()).draft.network.study, undefined);
      await profile('R4').locator('..').locator('select').selectOption('5755-40');
      await profile('R4').click();
      await page.selectOption('#modal-locale', alternate);
      assert.equal(await page.locator('[data-action="study-apply"]').getAttribute('data-variant'), '5755-40');
      await page.selectOption('#modal-locale', locale);
      await screenshot('network-diff');
      await click('study-apply');
      assert.equal((await saved()).draft.network.study.variant_id, '5755-40');
      assert.equal((await saved()).draft.network.radio_profiles[0].channel_width_mhz, 40);
      await profile('R3').locator('..').locator('select').selectOption('mcs7');
      await profile('R3').click(); await click('study-apply');
      assert.equal((await saved()).draft.network.study.phy.nominal_rate_mbps, 65);
      await field('#field-network-study-traffic-offered_load_mbps', '');
      assert.equal((await saved()).draft.network.study.traffic.offered_load_mbps, null);
      interactions.push('R1 Cancel leaves draft untouched; R4 5755-40 and R3 mcs7 Apply preserve exact units; nullable offered load remains null');

      await click('save'); await nav('versions');
      assert.equal((await page.locator('h1').innerText()).trim(),locale==='en-US'?'Configuration versions':'配置版本');
      await screenshot('versions');
      const baseline = (await saved()).versions[0].id;
      await page.locator(`[data-action="version-load"][data-id="${baseline}"]`).click();
      await click('modal-close'); assert.equal((await saved()).draft.network.study.profile_id, 'R3');
      await page.locator(`[data-action="version-load"][data-id="${baseline}"]`).click();
      await click('version-load-confirm'); assert.equal((await saved()).draft.network.study, undefined);
      interactions.push('Version comparison displays field diff; Cancel preserves draft; confirmed Load restores baseline without deleting versions');

      await nav('configuration'); await tab('scenario');
      let pending = page.waitForEvent('download'); await click('export');
      const exported = await pending, configurationPath = path.join(output, `${viewport.width}-${locale}-configuration.json`);
      await exported.saveAs(configurationPath);
      const configuration = JSON.parse(await fs.readFile(configurationPath, 'utf8'));
      assert.equal(configuration.config.metadata.name, 'Campus review · 用户输入');
      await field('#field-metadata-name', 'Changed after export');
      await nav('versions'); await click('import');
      await page.locator('#import-file').setInputFiles(configurationPath);
      await page.locator('#confirm-import').waitFor();
      await page.selectOption('#modal-locale', alternate); await page.selectOption('#modal-locale', locale);
      await page.click('#confirm-import');
      assert.equal((await saved()).draft.metadata.name, configuration.config.metadata.name);
      await click('import');
      await page.locator('#import-file').setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
      assert.ok((await page.locator('#toast').innerText()).length > 0);
      assert.equal((await saved()).draft.metadata.name, configuration.config.metadata.name);
      await page.reload({ waitUntil: 'networkidle' });
      assert.equal(await page.getAttribute('html', 'lang'), locale);
      assert.equal((await saved()).draft.metadata.name, configuration.config.metadata.name);
      interactions.push('Actual download export/import roundtrip; import modal survives language switches; invalid import rejected; draft and language survive reload');

      await nav('configuration');
      await page.evaluate(() => { const button = document.querySelector('[data-action="prepare"]'); button.click(); button.click(); });
      await page.locator('[data-action="run-open"]').first().waitFor();
      assert.equal((await saved()).runs.length, 1, 'Repeated Prepare must not duplicate a pending run');
      await click('run-open'); await page.selectOption('#entity-select', 'uav-alpha'); await click('selection-lock');
      assert.equal(await page.locator('#entity-select').isDisabled(), true);
      await page.locator('.scene-entity[data-action="select-scene-entity"][data-id="uav-beta"]').click();
      assert.equal(await page.inputValue('#entity-select'), 'uav-alpha');
      await page.locator('#timeline').evaluate(el => { el.value = '25'; el.dispatchEvent(new Event('input', { bubbles: true })); });
      const cursor = await page.locator('#cursor-label').innerText(), run = await page.inputValue('#run-select');
      const evidence = await page.locator('#selection-key').innerText();
      await page.selectOption('#locale', alternate); await page.selectOption('#locale', locale);
      assert.equal(await page.locator('#cursor-label').innerText(), cursor);
      assert.equal(await page.inputValue('#run-select'), run);
      assert.equal(await page.inputValue('#entity-select'), 'uav-alpha');
      assert.equal(await page.locator('#selection-key').innerText(), evidence);
      assert.match(await page.locator('body').innerText(), /null/);
      assert.equal(await page.locator('[data-action="selection-lock"]').getAttribute('aria-pressed'), 'true');
      const gap=page.locator('.scene-gap-notice');
      assert.equal(await gap.isVisible(),true);
      assert.ok(await gap.evaluate(el=>el.scrollWidth<=el.clientWidth));
      if(viewport.width===390){
        const labels=page.locator('.scene-label-button');
        assert.equal(await labels.count(),(await saved()).runs[0].frames[0].entities.length);
        assert.ok(await labels.first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize)>=14));
        await page.locator('.scene-label-button[data-id="uav-beta"]').click();
        assert.equal(await page.inputValue('#entity-select'),'uav-alpha');
      }
      await screenshot('replay-gap-evidence');
      await page.reload({waitUntil:'networkidle'});
      assert.equal(await page.inputValue('#entity-select'), 'uav-alpha');
      assert.equal(await page.locator('#cursor-label').innerText(), cursor);
      assert.equal(await page.locator('#entity-select').isDisabled(), true);
      pending = page.waitForEvent('download'); await click('observation-export');
      const observations = await pending;
      const observationPath = path.join(output, `${viewport.width}-${locale}-observations.json`);
      await observations.saveAs(observationPath);
      await click('play'); await nav('adapters');
      await nav('replay');
      assert.equal(await page.locator('[data-action="play"]').innerText(), locale === 'zh-CN' ? '播放快照' : 'Play snapshots');
      await click('help'); await page.keyboard.press('Escape');
      assert.equal(await page.locator('[role="dialog"]').count(), 0);
      interactions.push('Repeated Prepare creates one run; 25 s seek preserves null UAV evidence; entity lock rejects other marker clicks and survives reload; language switch keeps run/cursor/selection/key; actual neutral observations download; navigation stops playback');

      const forbidden = requests.filter(request => request.method !== 'GET' || /\/api\/|\/v1\/|^wss?:/.test(request.url));
      assert.deepEqual(forbidden, [], 'Fixture UI must never send lifecycle or source requests');
      assert.deepEqual(errors, []);
      cases.push({ viewport, locale, tested_commit: revision, baseURL, images, interactions, overflow, contrast, browser_errors: errors, request_count: requests.length, lifecycle_requests: forbidden, observations: observationPath });
      await context.close();
      console.log(`${viewport.width}×${viewport.height} ${locale}: seven screenshots; interaction assertions passed`);
    }
  } finally { await browser.close(); }
  await fs.writeFile(path.join(output, 'browser-results.json'), JSON.stringify({ tested_commit: revision, browser: 'Chromium', executablePath, cases }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
