import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFile } from 'node:fs/promises';

const dom = new JSDOM(await readFile(new URL('../index.html', import.meta.url), 'utf8'), { url: 'http://console.test/', pretendToBeVisual: true });
for (const name of ['window', 'document', 'localStorage', 'history', 'location', 'FormData']) globalThis[name] = dom.window[name];
const failures = [];
dom.window.addEventListener('error', event => failures.push(event.message));
await import('../src/app.js');
const $ = selector => document.querySelector(selector);
const click = selector => { assert.ok($(selector), selector); $(selector).dispatchEvent(new window.MouseEvent('click',{bubbles:true})); };
const change = (selector, value) => { $(selector).value = String(value); $(selector).dispatchEvent(new window.Event('change', { bubbles: true })); };
const workspace = () => JSON.parse(localStorage.getItem('aero-console.workspace.v1'));
const waitFor = async predicate => { for (let i = 0; i < 100; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 10)); } assert.fail('UI operation timed out'); };

test('bilingual workflow preserves typed values, modal edits and replay identity', async t => {
  await t.test('pending input and user-authored Chinese values survive English switching', () => {
    $('#field-metadata-name').value = '保存版本 · 用户名称';
    change('#locale', 'en-US');
    assert.equal(document.documentElement.lang, 'en-US');
    assert.equal($('#field-metadata-name').value, '保存版本 · 用户名称');
    assert.equal(workspace().draft.metadata.name, '保存版本 · 用户名称');
    assert.match($('h1').textContent, /Simulation configuration/);
    assert.equal(localStorage.getItem('aero-console.locale.v1'), 'en-US');
  });
  await t.test('every configuration category has translated copy and accessible labels', () => {
    for (const tab of ['entities', 'mobility', 'network', 'compute', 'semantics', 'scenario']) {
      click(`[data-tab="${tab}"]`);
      assert.ok($('.panel-title'));
      const text = $('.editor-stack').textContent.replace('保存版本 · 用户名称', '');
      assert.equal(/[\u3400-\u9fff]/.test(text), false, `${tab} contains untranslated authored UI`);
    }
    assert.equal($('[data-action="help"]').getAttribute('aria-label'), 'View mode information');
  });
  await t.test('entity modal retains unsaved numbers, validation error and focus through switching', () => {
    click('[data-tab="entities"]'); click('[data-action="entity-edit"]');
    $('#entity-east').value = '123.45'; $('#entity-id').value = 'INVALID ID';
    click('[data-action="entity-save"]');
    assert.match($('#modal-error').textContent, /lowercase identifier/);
    change('#modal-locale', 'zh-CN');
    assert.equal($('#entity-east').value, '123.45'); assert.equal($('#entity-id').value, 'INVALID ID');
    assert.match($('#modal-error').textContent, /标识必须/);
    change('#modal-locale', 'en-US');
    assert.equal($('#entity-east').value, '123.45'); assert.match($('#modal-error').textContent, /lowercase identifier/);
    document.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    assert.equal($('#modal-root').children.length, 0);
  });
  await t.test('network variant diff survives language switching and Cancel does not apply', () => {
    click('[data-tab="network"]');
    $('[data-action="study-preview"][data-id="R4"]').closest('.study-profile').querySelector('select').value = '5755-40';
    click('[data-action="study-preview"][data-id="R4"]');
    change('#modal-locale', 'zh-CN');
    assert.equal($('[data-action="study-apply"]').dataset.variant, '5755-40');
    click('[data-action="modal-close"]');
    assert.equal(workspace().draft.network.study, undefined);
    change('#locale', 'en-US');
  });
  await t.test('language switch preserves selected run, cursor and exact entity evidence', async () => {
    click('[data-action="prepare"]'); await waitFor(() => $('[data-action="run-open"]'));
    click('[data-action="run-open"]'); change('#entity-select', 'uav-alpha'); click('[data-action="selection-lock"]');
    assert.equal($('#entity-select').disabled,true);
    click('[data-action="select-scene-entity"][data-id="uav-beta"]');
    assert.equal($('#entity-select').value,'uav-alpha');
    $('#timeline').value = '25'; $('#timeline').dispatchEvent(new window.Event('input', { bubbles: true }));
    const cursor = $('#cursor-label').textContent, run = $('#run-select').value;
    const key = $('#selection-key').textContent;
    change('#locale', 'zh-CN');
    assert.equal($('#cursor-label').textContent, cursor); assert.equal($('#run-select').value, run);
    assert.equal($('#entity-select').value, 'uav-alpha'); assert.equal($('#selection-key').textContent, key);
    assert.match(document.body.textContent, /null/); assert.match(document.body.textContent, /unknown/);
    assert.equal(workspace().view.locked,true); assert.equal(workspace().view.entity_id,'uav-alpha');
    assert.deepEqual(failures, []);
  });
  dom.window.dispatchEvent(new window.Event('beforeunload'));
  dom.window.close();
});
