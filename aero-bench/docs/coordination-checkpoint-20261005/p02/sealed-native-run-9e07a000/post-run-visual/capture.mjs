import { chromium } from '/mnt/data2/weizhiwei/AERO_BENCH/frontend/node_modules/playwright/index.mjs';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { parseJsonObjectBytes } from '/mnt/data2/weizhiwei/AERO_BENCH/frontend/shared/json-bytes.mjs';
const out='/mnt/data2/weizhiwei/AERO_BENCH/validation/p02_same_run_9e07_visual_review';
const trace='validation/p02_same_run_9e07_visual_review/public/public-trace.json';
const run='9e07a000f35961e5d4bca89553729a8e743143781aad6d213fed722abfd324fd';
const report={scope:'same native run 9e07; post-run authoritative replay',run_id:run,status:'loading',screenshots:[],errors:[],failures:[],frames:[]};
async function save(){await writeFile(out+'/visual-receipt.json',JSON.stringify(report,null,2)+'\n');}
console.log('CAPTURE_PID='+process.pid);await writeFile(out+'/capture.pid',String(process.pid)+'\n');
const ticks=new Set(),poses=[];
const metadata=parseJsonObjectBytes(await readFile(out+'/public/public-trace.json'),{keys:new Set(['run_id','phase','time']),onArrayItem(key,item){
if(['scene_states','events','mission_events','mission_status_history','network_events','network_frames','sensor_frames'].includes(key))ticks.add(item.at.tick);
if(key==='scene_states'){const p=item.samples.find(x=>x.entity_id==='uav.inspector');if(p)poses.push({tick:item.at.tick,sim_time_ns:item.at.sim_time_ns,sample_digest:p.sample_digest,pose:p.pose,velocity:p.velocity,attributes:p.attributes});}
}});
if(metadata.run_id!==run)throw Error('Wrong public trace');ticks.add(metadata.time.tick);
const timeline=[...ticks].sort((a,b)=>a-b);report.trace={...metadata,scene_frame_count:poses.length,timeline_ticks:timeline.length};await save();
const browser=await chromium.launch({channel:'chromium',headless:true,args:['--enable-gpu','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist']});
const page=await browser.newPage({viewport:{width:1600,height:1000}});
page.on('pageerror',e=>report.errors.push(e.message.slice(0,300)));
page.on('requestfailed',r=>report.failures.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));
try{
await page.addInitScript(()=>localStorage.setItem('aero-bench.viewer.language','en'));
await page.goto('http://127.0.0.1:5412/?view=replay&trace='+encodeURIComponent(trace)+'&scene=1',{waitUntil:'domcontentloaded',timeout:60000});
await page.waitForFunction(()=>document.querySelector('.scrubber')?.disabled===false,null,{timeout:600000});
await page.waitForFunction(()=>document.querySelector('#city-map')?.dataset.sceneReady==='true'&&document.querySelector('#city-map')?.dataset.texturesReady==='true',null,{timeout:600000});
const pause=page.getByRole('button',{name:/^(Pause|暂停)$/});if(await pause.isVisible())await pause.click();
await page.evaluate(()=>{const label=document.createElement('div');label.id='same-run-review-label';label.textContent='Same native run 9e07 · post-run authoritative replay';Object.assign(label.style,{position:'fixed',left:'50%',top:'58px',transform:'translateX(-50%)',zIndex:'10000',background:'#102238',color:'#fff',padding:'7px 12px',font:'13px sans-serif',borderRadius:'5px',pointerEvents:'none'});document.body.appendChild(label);});
async function capture(tick,name){const index=timeline.indexOf(tick);if(index<0)throw Error('Tick missing');await page.evaluate(index=>{const slider=document.querySelector('.scrubber');slider.value=String(index);slider.dispatchEvent(new Event('input',{bubbles:true}));},index);await page.waitForTimeout(650);
const state=await page.evaluate(()=>({map:{...document.querySelector('#city-map')?.dataset},cursor:document.querySelector('.scrubber')?.value,clock:document.querySelector('.playback-clock')?.textContent,selected:document.querySelector('.operations-monitor-fleet-row[aria-pressed="true"]')?.textContent,detail:document.querySelector('.operations-monitor-detail')?.textContent,body:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],viewport:[innerWidth,innerHeight]}));
if(state.map.sceneReady!=='true'||state.map.texturesReady!=='true')throw Error('Resources not ready');if(state.body[0]>state.viewport[0]||state.body[1]>state.viewport[1])throw Error('Overflow');await page.screenshot({path:out+'/'+name+'.png'});report.screenshots.push(name+'.png');report.frames.push({name,tick,authoritative_pose:poses.find(p=>p.tick===tick),...state});await save();}
const row=page.locator('.operations-monitor-fleet-row').filter({hasText:'uav.inspector'}).first();if(await row.count())await row.click();
await capture(120,'in-flight-tick120');await capture(240,'in-flight-tick240');await capture(300,'touchdown-tick300');
await page.setViewportSize({width:1280,height:900});await capture(240,'narrow-tick240');await page.setViewportSize({width:1600,height:1000});
await mkdir(out+'/gif-frames',{recursive:true});for(let tick=80;tick<=260;tick+=10)await capture(tick,'gif-frames/frame-'+String(tick).padStart(3,'0'));
report.status='captured';await save();console.log('CAPTURE_COMPLETE');
}catch(e){report.status='failed';report.error=String(e);await page.screenshot({path:out+'/failure.png'});await save();throw e;}finally{await browser.close();}
