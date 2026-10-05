import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFile} from 'node:fs/promises';

// DOM emulation only. This does not claim browser layout, screenshots, or visual QA.
const html=await readFile(new URL('../index.html',import.meta.url),'utf8');
const dom=new JSDOM(html,{url:'http://console.test/',pretendToBeVisual:true});
for(const name of ['window','document','localStorage','history','location','FormData'])globalThis[name]=dom.window[name];
const errors=[];dom.window.addEventListener('error',event=>errors.push(event.error||event.message));
await import('../src/app.js');
const $=s=>document.querySelector(s);
const click=s=>{assert.ok($(s),`Element ${s} exists`);$(s).click();};
const change=(s,v)=>{assert.ok($(s),`Field ${s} exists`);$(s).value=String(v);$(s).dispatchEvent(new window.Event('change',{bubbles:true}));};
const waitUntil=async fn=>{for(let i=0;i<100;i++){if(fn())return;await new Promise(r=>setTimeout(r,10));}assert.fail('UI operation did not finish');};

test('complete DOM workflow: author, validate, version, diff, study, fixture and replay',async t=>{
 await t.test('initial configuration page exposes actual primary controls',()=>{assert.match(document.body.textContent,/仿真配置/);assert.ok($('#field-metadata-name'));assert.ok($('[data-action="prepare"]'));assert.equal(errors.length,0);});
 await t.test('all six configuration categories render',()=>{for(const tab of ['entities','mobility','network','compute','semantics','scenario']){click(`[data-tab="${tab}"]`);assert.ok($('.panel'));assert.equal(errors.length,0);}});
 await t.test('save baseline and compare a real changed field',()=>{click('[data-action="save"]');change('#field-metadata-name','Reviewable test scenario');click('[data-nav="versions"]');assert.match(document.body.textContent,/metadata.name/);assert.match(document.body.textContent,/Reviewable test scenario/);});
 await t.test('cancel version restore does not change draft; confirm restores',()=>{click('[data-action="version-load"]');click('[data-action="modal-close"]');click('[data-nav="configuration"]');assert.equal($('#field-metadata-name').value,'Reviewable test scenario');click('[data-nav="versions"]');click('[data-action="version-load"]');click('[data-action="version-load-confirm"]');click('[data-nav="configuration"]');assert.equal($('#field-metadata-name').value,'Campus delivery lab');});
 await t.test('entity modal invalid ID is rejected and escaping cancels',()=>{click('[data-tab="entities"]');click('[data-action="entity-add"]');$('#entity-id').value='INVALID ID';click('[data-action="entity-save"]');assert.ok($('#modal-error').textContent.length);document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal($('#modal-root').children.length,0);});
 await t.test('valid entity edit persists and labels are escaped',()=>{click('[data-action="entity-edit"]');$('#entity-east').value='-70';click('[data-action="entity-save"]');assert.equal($('#modal-root').children.length,0);assert.match($('.data-table').textContent,/-70/);});
 await t.test('network research profile requires explicit confirmation and keeps units separate',()=>{click('[data-tab="network"]');click('[data-action="study-preview"][data-id="R1"]');assert.match($('#modal-root').textContent,/network.study/);click('[data-action="modal-close"]');assert.equal($('#field-network-study-phy-mcs'),null);click('[data-action="study-preview"][data-id="R1"]');click('[data-action="study-apply"]');assert.ok($('#field-network-study-phy-mcs'));assert.match(document.body.textContent,/信道宽度/);assert.match(document.body.textContent,/PHY 名义速率/);assert.match(document.body.textContent,/offered load/);});
 await t.test('nullable traffic fields preserve unknown rather than zero',()=>{change('#field-network-study-traffic-offered_load_mbps','');const saved=JSON.parse(localStorage.getItem('aero-console.workspace.v1'));assert.equal(saved.draft.network.study.traffic.offered_load_mbps,null);});
 await t.test('invalid editable time disables prepare, then recovery enables it',()=>{click('[data-tab="scenario"]');change('#field-scenario-step_ms',0);assert.equal($('[data-action="prepare"]').disabled,true);click('[data-action="validate"]');assert.match(document.body.textContent,/配置需要修正/);change('#field-scenario-step_ms',100);assert.equal($('[data-action="prepare"]').disabled,false);});
 await t.test('fixture creation finishes and source evidence remains explicit',async()=>{click('[data-action="prepare"]');await waitUntil(()=>$('[data-action="run-open"]'));assert.match(document.body.textContent,/已封存/);click('[data-action="run-open"]');assert.ok($('#timeline'));assert.match(document.body.textContent,/unknown · 未执行/);assert.match(document.body.textContent,/BENCH renderer not mounted/);});
 await t.test('shared cursor and entity selection update evidence',()=>{click('[data-action="next-frame"]');assert.match($('#cursor-label').textContent,/1.0/);change('#entity-select','uav-beta');assert.match($('.entity-detail-heading').textContent,/uav-beta/);assert.match(document.body.textContent,/Not bound/);const slider=$('#timeline');slider.value='25';slider.dispatchEvent(new window.Event('input',{bubbles:true}));assert.match(document.body.textContent,/authored evidence gap/);assert.match(document.body.textContent,/null/);});
 await t.test('playback stops on navigation and modal dismissal is safe',()=>{click('[data-action="play"]');click('[data-nav="adapters"]');assert.match(document.body.textContent,/适配器与来源/);click('[data-action="help"]');click('[data-action="modal-close"]');assert.equal($('#modal-root').children.length,0);assert.equal(errors.length,0);});
 dom.window.dispatchEvent(new window.Event('beforeunload'));
 dom.window.close();
});
