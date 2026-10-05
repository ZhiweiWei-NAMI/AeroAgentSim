import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {sampleFixtureFrame} from '../src/fixture.js';
import {mountParcelView} from '../src/view.js';
test('DOM component picks, follows, seeks and switches language while retaining selection',()=>{
  const dom=new JSDOM('<div id="root"></div>');const root=dom.window.document.getElementById('root');
  let selection=null,seek=null;
  const view=mountParcelView(root,{onSelectionChange:value=>selection=value,onSeek:value=>seek=value});
  view.setFrame(sampleFixtureFrame(10));
  assert.ok(root.textContent.includes('包裹清单')||root.textContent.includes('场景对象'));
  root.querySelector('[data-select="uav.01"]').click();assert.equal(selection.entityId,'uav.01');
  root.querySelector('[data-action="follow"]').click();assert.equal(root.querySelector('[data-action="follow"]').getAttribute('aria-pressed'),'true');
  view.setFrame(sampleFixtureFrame(41));assert.equal(view.getSelection().entityId,'uav.01');assert.equal(view.getSelection().frameKey,sampleFixtureFrame(41).frameKey);
  const box=root.querySelector('svg').getAttribute('viewBox');assert.ok(box.endsWith('660 430'));
  view.setLanguage('en');assert.ok(root.textContent.includes('Frame evidence')||root.textContent.includes('ENU position'));assert.equal(view.getSelection().entityId,'uav.01');assert.ok(root.textContent.includes('Holding position'));
  root.querySelector('[data-seek="78"]').click();assert.equal(seek,78);
  root.querySelector('[data-action="fit"]').click();assert.equal(root.querySelector('[data-action="follow"]').getAttribute('aria-pressed'),'false');
  root.querySelector('[data-select="parcel.p1042"]').click();assert.equal(selection.entityId,'parcel.p1042');
  view.setFrame(sampleFixtureFrame(80));assert.equal(view.getSelection().entity.custodian,'locker');assert.ok(root.textContent.includes('In locker'));
  view.setFrame(sampleFixtureFrame(2));assert.equal(view.getSelection().entity.custodian,'warehouse');assert.equal(view.getSelection().entityId,'parcel.p1042');
  assert.equal(view.selectTarget('missing.id'),false);assert.equal(view.getSelection().entityId,'parcel.p1042');
  view.destroy();assert.equal(root.childElementCount,0);
});
test('SVG picking binds physical parcel to inspector and duplicate-free labels',()=>{
  const dom=new JSDOM('<div id="root"></div>');const root=dom.window.document.getElementById('root');
  const view=mountParcelView(root);view.setFrame(sampleFixtureFrame(45));
  root.querySelector('[data-entity-id="parcel.p1043"]').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));
  assert.equal(view.getSelection().entityId,'parcel.p1043');assert.equal(view.getSelection().entity.custodian,'warehouse');
  for(const id of ['parcel.p1042','parcel.p1043','parcel.p1044'])assert.equal(root.querySelectorAll(`[data-entity-id="${id}"]`).length,1);
});
test('future event ledger is explicit and can be hidden again',()=>{
  const dom=new JSDOM('<div id="root"></div>');const root=dom.window.document.getElementById('root');
  const view=mountParcelView(root);view.setFrame(sampleFixtureFrame(2));assert.equal(root.querySelectorAll('.event-row').length,0);
  let checkbox=root.querySelector('[data-action="planned"]');checkbox.checked=true;checkbox.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.ok(root.querySelectorAll('.event-row.future').length>0);
  checkbox=root.querySelector('[data-action="planned"]');checkbox.checked=false;checkbox.dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(root.querySelectorAll('.event-row').length,0);
});
test('keyboard focus survives host frame updates',()=>{
  const dom=new JSDOM('<div id="root"></div>');const root=dom.window.document.getElementById('root');const view=mountParcelView(root);
  view.setFrame(sampleFixtureFrame(35));root.querySelector('[data-select="ugv.01"]').focus();view.setFrame(sampleFixtureFrame(36));assert.equal(dom.window.document.activeElement.getAttribute('data-select'),'ugv.01');
});
test('fixture source labels and unbound Atlas result remain visible in both languages',()=>{
  const dom=new JSDOM('<div id="root"></div>');const root=dom.window.document.getElementById('root');const view=mountParcelView(root);
  for(const language of ['zh','en']){view.setLanguage(language);view.setFrame(sampleFixtureFrame(42));assert.ok(root.textContent.includes(language==='zh'?'Atlas 结果：未知':'Atlas result: unknown'));assert.ok(root.textContent.includes('authored-fixture'));assert.ok(root.textContent.includes('−90 dBm'));}
});
