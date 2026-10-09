/** Real serve/replay visual checks. Run: node frontend/e2e/visual-smoke.mjs [API]. */
import { createRequire } from 'node:module';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, join } from 'node:path';
const require = createRequire(new URL('../package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const api=process.argv[2] ?? 'http://127.0.0.1:8007';
const out=resolve('frontend/test-results');mkdirSync(out,{recursive:true});
const temp=resolve('frontend/e2e/.tmp');mkdirSync(temp,{recursive:true});mkdirSync(temp+'/config',{recursive:true});process.env.XDG_CONFIG_HOME=temp+'/config';process.chdir(resolve('frontend/e2e'));process.env.TMPDIR='/proc/self/cwd/.tmp';
process.env.NO_PROXY=process.env.no_proxy='127.0.0.1,localhost';
const executablePath=process.env.AEROAGENTSIM_CHROMIUM;
const records=[], browserErrors=[];let missingAssets=false;
let browser;
try {
  // Try EGL before choosing an explicitly recorded software-rendering run.
  browser=await chromium.launch({executablePath,headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=gl-egl']});
  const probe=await browser.newPage();await probe.goto(api);const gpu=await probe.evaluate(()=>{const gl=document.createElement('canvas').getContext('webgl2');if(!gl)return null;const ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);});
  records.push({eglProbe:gpu});await probe.close();
  if(!gpu || /swiftshader|llvmpipe|software/i.test(gpu)){await browser.close();browser=undefined;}
} catch(error){records.push({eglProbeError:String(error)});if(browser)await browser.close();browser=undefined;}
if(!browser)browser=await chromium.launch({executablePath,headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader']});
try {
 const runs=await (await fetch(api+'/v1/runs')).json();
 const select=(stem)=>runs.filter(r=>r.id.includes(stem)&&r.status==='completed').at(-1);
 const slice=select('p1-slice-city'),scale=select('p1-scale-city');
 if(!slice||!scale)throw Error('Completed visual scenario runs required');
 const page=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});
 page.on('pageerror',error=>browserErrors.push(error.message));page.on('console',message=>{if(message.type()==='error'&&!message.location().url.endsWith('/logo192.png')&&!(missingAssets&&message.location().url.includes('/assets/')&&message.text().includes('404')))browserErrors.push(message.text());});
 for(const [run,modes,label] of [[slice,['orbit','follow','chase'],'slice'],[scale,['orbit'],'scale']]) {
  await page.goto(`${api}/runs/${encodeURIComponent(run.id)}?mode=replay&scene=huangpu`,{waitUntil:'domcontentloaded'});
  const viewport=page.getByTestId('viewport');await expect(viewport).toHaveAttribute('data-rendered','true',{timeout:120000});
  await expect(viewport).toHaveAttribute('data-city','loaded',{timeout:120000});
  await expect(viewport).toHaveAttribute('data-ibl','hdri',{timeout:120000});
  await expect.poll(()=>viewport.getAttribute('data-asset-status'),{timeout:120000}).not.toContain('Loading model:');
  // Pin a true terminal replay cut. The entity count is read from actual spatial facts.
  await expect(page.getByText('completed',{exact:true})).toBeVisible({timeout:120000});
  await page.getByRole('slider').focus();await page.keyboard.press('End');
  await expect(viewport).toHaveAttribute('data-spatial-count',label==='scale'?'1000':'5',{timeout:120000});
  await page.getByLabel('Toggle inspector').click();await page.getByLabel('View quality').selectOption(label==='scale'?'low':'med');
  for(const mode of modes) {
   await page.getByLabel('View camera').selectOption(mode);await page.waitForTimeout(3000);
   const samples=await page.evaluate(async()=>{const times=[];await new Promise(resolve=>{let start,last;function tick(t){if(start===undefined){start=t;last=t;}else{times.push(t-last);last=t;}if(t-start<6000)requestAnimationFrame(tick);else resolve();}requestAnimationFrame(tick);});times.sort((a,b)=>a-b);return {frames:times.length,seconds:times.reduce((a,b)=>a+b,0)/1000,fps:times.length*1000/times.reduce((a,b)=>a+b,0),p95FrameMs:times[Math.floor(times.length*.95)]};});
   const diagnostics=await viewport.evaluate(node=>({...node.dataset}));
   const screenshot=`${out}/p7a-${label}-${mode}.png`;await page.screenshot({path:screenshot,timeout:90000});
   records.push({run:run.id,scenario:run.scenario,mode,...samples,diagnostics,screenshot});console.log(JSON.stringify({mode,scenario:run.scenario,...samples,quality:diagnostics.quality,gpu:diagnostics.gpu}));
  }
 }
 // Exercise explicit no-sync degradation and the event director on a real feed.
 missingAssets=true;
 await page.route('**/assets/city/**',route=>route.fulfill({status:404,body:'Assets not synced'}));
 await page.route('**/assets/environment/**',route=>route.fulfill({status:404,body:'Assets not synced'}));
 await page.route('**/assets/models/**',route=>route.fulfill({status:404,body:'Assets not synced'}));
 await page.goto(`${api}/runs/${encodeURIComponent(slice.id)}?mode=replay&scene=huangpu&camera=cinematic`);
 await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true');
 await expect(page.getByTestId('viewport')).toHaveAttribute('data-spatial-count','5');
 await expect(page.locator('.scene-status')).toContainText(/unavailable/);
 await expect(page.getByTestId('viewport')).not.toHaveAttribute('data-city','loaded');
 await page.screenshot({path:out+'/p7a-without-assets.png',timeout:90000});
 records.push({missingAssets:'base scene and 5 spatial entities rendered with visible unavailable status',camera:'cinematic'});
 if(browserErrors.length)throw Error('Browser errors: '+JSON.stringify(browserErrors));
 records.push({browserErrors});
} finally {writeFileSync(out+'/visual-measurements.json',JSON.stringify(records,null,2)+'\n');await browser.close();}
console.log(JSON.stringify(records,null,2));
