import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
test('app boots, changes language, seeks and advances its single clock',async()=>{
  const dom=new JSDOM('<html><body><div id="app"></div></body></html>',{url:'http://localhost/'});
  const prior={document:globalThis.document,requestAnimationFrame:globalThis.requestAnimationFrame};
  let next=null;globalThis.document=dom.window.document;globalThis.requestAnimationFrame=callback=>{next=callback;};
  try{
    await import('../src/app.js');const d=dom.window.document;
    assert.ok(d.querySelector('.park-scene'));assert.ok(d.getElementById('app-title').textContent.includes('包裹'));
    d.querySelector('[data-language="en"]').click();assert.equal(d.documentElement.lang,'en');assert.equal(d.getElementById('app-title').textContent,'Parcel operations');
    d.querySelector('[data-jump="38"]').click();assert.equal(d.getElementById('timeline').value,'38');assert.ok(d.querySelector('.truth-true'));
    d.getElementById('play').click();next(0);next(100);assert.equal(d.getElementById('timeline').value,'38.1');
    d.getElementById('play').click();next(200);assert.equal(d.getElementById('timeline').value,'38.1');
    d.getElementById('reset').click();assert.equal(d.getElementById('timeline').value,'0.1');
    d.querySelector('[data-select="parcel.p1043"]').click();d.querySelector('[data-jump="78"]').click();assert.equal(d.querySelector('[data-select="parcel.p1043"]').getAttribute('aria-pressed'),'true');
    assert.ok(d.querySelector('.selected-heading').textContent.includes('P-1043'));
  }finally{globalThis.document=prior.document;globalThis.requestAnimationFrame=prior.requestAnimationFrame;dom.window.close();}
});
