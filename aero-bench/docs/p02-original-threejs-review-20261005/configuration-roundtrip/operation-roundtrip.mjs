import { chromium } from '/mnt/data2/weizhiwei/AERO_BENCH/frontend/node_modules/playwright/index.mjs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
const root='/mnt/data2/weizhiwei/AERO_BENCH/validation/p02-original-threejs/operation-roundtrip';
await mkdir(root,{recursive:true});
const saved=(await readFile(root+'/../config-consumption/exact-saved-config.json','utf8')).trimEnd();
const trace='validation/platform-plan-20261001/W1-I5/runs-v8/c69f303f0be963d9fd4c393d88f7cb61d3508c539f080924d30b0d6f19f7deba/public/public-trace.json';
const runUrl='http://127.0.0.1:5413/?view=replay&trace='+encodeURIComponent(trace)+'&scene=1&city=/city-presentation/jingan-engineering-preview-v3.json';
const browser=await chromium.launch({channel:'chromium',headless:true,args:['--enable-gpu','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist']});
const page=await browser.newPage({viewport:{width:1600,height:1000}});
const report={scope:'Configuration editor round-trip only; original BENCH GLB preview and existing sealed-v8 replay. No new native run started.',providerStarted:false,errors:[],steps:[],gifFrames:[]};
page.on('pageerror',e=>report.errors.push(e.message.replace(/https?:\/\/[^\s)]+/g,'[URL withheld]').slice(0,350)));
async function snap(name,{frame=true}={}){
 const data=await page.evaluate(()=>{
  const box=el=>el?.getBoundingClientRect().toJSON();
  return {path:location.pathname,language:document.documentElement.lang,seed:document.querySelector('input[name="seed"]')?.value,
   storedSeed:JSON.parse(localStorage.getItem('aero-bench.city-workspace.v3')||'null')?.runtime?.seed,
   saveState:document.querySelector('#studio-save-status')?.dataset.state,
   previewStatus:document.querySelector('#studio-preview-state')?.textContent,
   body:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,viewport:[innerWidth,innerHeight]},
   panelScroll:{client:document.querySelector('#studio-panel-scroll')?.clientHeight,total:document.querySelector('#studio-panel-scroll')?.scrollHeight},
   seedEditable:document.querySelector('input[name="seed"]')?.disabled===false,
   selectedPanel:box(document.querySelector('.operations-monitor-detail')),
   previewKind:{text:document.querySelector('#studio-preview-kind')?.textContent,width:document.querySelector('#studio-preview-kind')?.clientWidth,scrollWidth:document.querySelector('#studio-preview-kind')?.scrollWidth},
   identity:{...document.querySelector('[data-saved-draft-sha256]')?.dataset},
   selected:document.querySelector('.entity-row.selected')?.textContent,
   returnLinks:[...document.querySelectorAll('[data-p02-return]')].map(a=>({text:a.textContent,path:new URL(a.href).pathname+new URL(a.href).search})),
  };
 });
 if(data.body.width>data.body.viewport[0]||data.body.height>data.body.viewport[1]) throw Error('Body overflow at '+name);
 await page.screenshot({path:root+'/'+name+'.png'});
 report.steps.push({name,...data});
 if(frame)report.gifFrames.push({file:name+'.png',duration_ms:1800});
 await writeFile(root+'/operation-evidence.json',JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({name,path:data.path,language:data.language,seed:data.seed,saveState:data.saveState,body:data.body}));
}
try{
 // Initialize this fresh browser profile ONCE. Reload cannot reset the edited draft.
 await page.addInitScript(value=>{
  if(localStorage.getItem('aero-bench.city-workspace.v3')===null)localStorage.setItem('aero-bench.city-workspace.v3',value);
  if(localStorage.getItem('aero-bench.viewer.language')===null)localStorage.setItem('aero-bench.viewer.language','en');
 },saved);
 await page.goto(runUrl,{waitUntil:'domcontentloaded',timeout:60000});
 await page.waitForFunction(()=>document.querySelector('#city-map')?.dataset.sceneReady==='true',null,{timeout:240000});
 await page.waitForFunction(()=>document.querySelector('.scrubber')?.disabled===false,null,{timeout:120000});
 await snap('01-run-before-configuration');
 await Promise.all([page.waitForURL('**/city-studio.html?**',{timeout:60000}),page.locator('.p02-view-button').nth(1).click()]);
 await page.waitForFunction(()=>document.querySelector('input[name="seed"]')!==null,null,{timeout:120000});
 await page.waitForFunction(()=>document.querySelector('#studio-preview-state')?.dataset.state==='ready',null,{timeout:240000});
 if(await page.locator('input[name="seed"]').inputValue()!=='20261002')throw Error('Wrong initial draft');
 await snap('02-studio-editor-en');
 await page.locator('input[name="seed"]').fill('20261003');
 await page.locator('input[name="seed"]').dispatchEvent('change');
 await snap('03-seed-edited');
 await page.locator('#studio-save').click();
 await snap('04-draft-saved');
 await page.reload({waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('#studio-preview-state')?.dataset.state==='ready',null,{timeout:240000});
 if(await page.locator('input[name="seed"]').inputValue()!=='20261003')throw Error('Saved edited seed not retained across reload');
 report.editedValueSurvivesReload=true;
 await snap('05-edited-draft-reloaded');
 await page.locator('[data-studio-language="zh"]').click();
 await snap('06-studio-editor-zh');
 await page.setViewportSize({width:1200,height:800});await page.waitForTimeout(500);
 await snap('07-studio-narrow-zh',{frame:false});
 await page.locator('[data-studio-language="en"]').click();
 await snap('08-studio-narrow-en',{frame:false});
 await page.setViewportSize({width:1600,height:1000});
 await page.evaluate(()=>{const p=document.querySelector('#studio-panel-scroll');p.scrollTop=200;});
 report.panelScrollWorks=await page.locator('#studio-panel-scroll').evaluate(p=>p.scrollTop>0&&p.scrollHeight>p.clientHeight);
 // Restore and save the exact approved configuration before compile/handoff.
 await page.locator('input[name="seed"]').fill('20261002');
 await page.locator('input[name="seed"]').dispatchEvent('change');
 await page.locator('#studio-save').click();
 await page.reload({waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('input[name="seed"]')?.value==='20261002',null,{timeout:120000});
 const stored=await page.evaluate(()=>localStorage.getItem('aero-bench.city-workspace.v3'));
 report.approvedDraftRestoredExactly=stored===saved;
 if(!report.approvedDraftRestoredExactly)throw Error('Approved draft bytes not restored exactly');
 await page.locator('[data-tab="compile"]').click();
 await page.getByRole('button',{name:'Compile saved draft',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-saved-draft-sha256]'),null,{timeout:120000});
 await page.getByRole('button',{name:'Open run handoff',exact:true}).click();
 await snap('09-matching-compiled-handoff');
 const returnLink=page.locator('a[data-p02-return="run"]').last();
 await Promise.all([page.waitForURL(url=>url.pathname==='/'),returnLink.click()]);
 await page.waitForFunction(()=>document.querySelector('#city-map')?.dataset.sceneReady==='true',null,{timeout:240000});
 report.returnedToOriginalRunSource=page.url()===runUrl;
 if(!report.returnedToOriginalRunSource)throw Error('Studio return changed Run source');
 await snap('10-original-replay-restored');
 report.status='configuration-roundtrip-passed-native-execution-pending';
}catch(e){report.failure=String(e);await page.screenshot({path:root+'/failure.png'}).catch(()=>{});throw e;}
finally{await writeFile(root+'/operation-evidence.json',JSON.stringify(report,null,2)+'\n');await browser.close();}
