import {test as base,expect,type Locator,type Page} from '@playwright/test';
import {readFileSync,writeFileSync,existsSync,mkdirSync} from 'node:fs';
import {join,basename,dirname} from 'node:path';

export interface StoryRun { workspaceId: string; runId: string }
interface RecordingContext {baseURL:string; stories:Record<string, StoryRun>}
interface Segment {path:string;start:number;duration:number}
const recordings=new WeakMap<Page,{start:number;segments:Segment[]}>();
export function recordingContext():RecordingContext {
 const file=process.env.AEROAGENTSIM_DOCS_CONTEXT;
 if(!file)throw Error('Set AEROAGENTSIM_DOCS_CONTEXT.');
 return JSON.parse(readFileSync(file,'utf8'));
}
export const flowTest=base.extend({
 page:async({page,baseURL},use,testInfo)=>{
  if(!baseURL)throw Error('Recording requires a backend URL');
  const templates=await page.request.get(baseURL+'/v1/templates');
  if(!templates.ok())throw Error('Template API: '+templates.status());
  await page.addInitScript(()=>{if(location.protocol==='http:'||location.protocol==='https:')localStorage.setItem('aeroagentsim.locale','en-US');});
  page.setDefaultTimeout(60_000); page.setDefaultNavigationTimeout(90_000);
  page.on('dialog', dialog=>dialog.accept());
  const clock={start:Date.now(),segments:[] as Segment[]};recordings.set(page,clock);
  await page.addInitScript(()=>{
   const install=()=>{
    if(document.getElementById('docs-cursor'))return;
    const style=document.createElement('style');style.textContent='#docs-cursor{position:fixed;width:18px;height:18px;border:3px solid white;border-radius:50%;background:#0d9488;box-shadow:0 0 0 2px #0d9488,0 3px 12px #0005;z-index:2147483647;pointer-events:none;transition:left .22s,top .22s;display:none}#docs-highlight{position:fixed;border:2px solid #0d9488;border-radius:6px;background:#0d94880c;z-index:2147483646;pointer-events:none;display:none}#docs-story{position:fixed;left:50%;transform:translateX(-50%);bottom:116px;max-width:620px;padding:9px 14px;border-radius:8px;color:white;background:#0c2438ed;font:600 13px system-ui;box-shadow:0 3px 12px #0002;z-index:2147483645;pointer-events:none;display:none}';document.head.append(style);
    for(const id of ['docs-cursor','docs-highlight','docs-story']){const el=document.createElement('div');el.id=id;document.body.append(el);}
   };document.readyState==='loading'?document.addEventListener('DOMContentLoaded',install):install();
  });
  // A visible prelude establishes the screencast origin before API preparation.
  // Warm the video encoder before a visible white → black timestamp marker.
  // A black page immediately after creation can be omitted by Chromium.
  await page.goto('data:text/html,<html style="background:white"><body></body></html>',{waitUntil:'domcontentloaded'});
  await page.waitForTimeout(1200);
  await page.evaluate(()=>{document.documentElement.style.background='black';});
  clock.start=Date.now();await page.waitForTimeout(800);
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  page.on('console', message=>{if(message.type()==='error')errors.push(message.text());});
  let succeeded=false;
  try{await use(page);expect(errors).toEqual([]);succeeded=true;}finally{
   const video=page.video();
   const logDir=join(requiredEnv('AEROAGENTSIM_DOCS_VIDEO_DIR'),basename(testInfo.file,'.spec.ts'));mkdirSync(logDir,{recursive:true});
   writeFileSync(join(logDir,'browser-errors.json'),JSON.stringify(errors));
   if(testInfo.status!=='passed'){
    const debug=join(requiredEnv('AEROAGENTSIM_DOCS_VIDEO_DIR'),basename(testInfo.file,'.spec.ts'));mkdirSync(debug,{recursive:true});
    await page.screenshot({path:join(debug,'failure.png')});writeFileSync(join(debug,'failure.txt'),await page.locator('body').innerText());
   }
   await page.close();
   if(video&&clock.segments.length&&succeeded&&testInfo.status==='passed'){
    const root=requiredEnv('AEROAGENTSIM_DOCS_VIDEO_DIR');
    const flow=basename(testInfo.file,'.spec.ts'),path=join(root,flow,'video.webm');mkdirSync(dirname(path),{recursive:true});
    await video.saveAs(path);
    const manifest=join(root,'recordings.json');const data=existsSync(manifest)?JSON.parse(readFileSync(manifest,'utf8')):{};
    data[flow]={blackPrelude:true,segments:clock.segments.map(row=>({...row,path})),speed:1};
    writeFileSync(manifest,JSON.stringify(data,null,2)+'\n');
    await testInfo.attach('recording-segments',{body:JSON.stringify(data[flow]),contentType:'application/json'});
   }
  }
 },
});
export {expect};
export async function caption(page:Page,text:string){await page.evaluate(text=>{const el=document.getElementById('docs-story');if(!el)throw Error('Recording caption overlay was not installed');el.textContent=text;el.style.display='block';const configuring=!!document.querySelector('[data-testid=wb-config]'),dock=document.querySelector('.wb-dock.is-open');const bottom=dock?window.innerHeight-dock.getBoundingClientRect().top+12:116;Object.assign(el.style,configuring?{left:'24px',top:'124px',bottom:'auto',transform:'none',maxWidth:'540px'}:{left:'50%',top:'auto',bottom:bottom+'px',transform:'translateX(-50%)',maxWidth:'620px'});},text);}
export async function segment(page:Page,work:()=>Promise<void>,title?:string){
 const clock=recordings.get(page);if(!clock)throw Error('Use flowTest for segmented recording');
 if(title){console.log(title);await caption(page,title);}
 const start=(Date.now()-clock.start)/1000;await work();const duration=(Date.now()-clock.start)/1000-start;
 if(duration>0)clock.segments.push({path:'',start,duration});
}
export async function hold(page:Page,ms=800){await page.waitForTimeout(ms);}
async function point(locator:Locator){
 await locator.scrollIntoViewIfNeeded();await expect(locator).toBeVisible();
 const box=await locator.boundingBox();if(!box)throw Error('Clicked element has no visible geometry');
 await locator.page().evaluate(box=>{
  const cursor=document.getElementById('docs-cursor'),highlight=document.getElementById('docs-highlight');
  if(!cursor||!highlight)throw Error('Recording cursor overlay was not installed');
  if(cursor){cursor.style.display='block';cursor.style.left=box.x+box.width/2-9+'px';cursor.style.top=box.y+box.height/2-9+'px';}
  if(highlight){highlight.style.display='block';Object.assign(highlight.style,{left:box.x-3+'px',top:box.y-3+'px',width:box.width+6+'px',height:box.height+6+'px'});}
 },box);await hold(locator.page(),250);
}
export async function click(locator:Locator){await point(locator);await locator.click();await hold(locator.page(),650);}
export async function fill(locator:Locator,value:string){await point(locator);await locator.fill(value);await hold(locator.page(),650);}
export async function choose(locator:Locator,label:string){
 await point(locator);
 if(await locator.evaluate(el=>el.tagName==='SELECT'))await locator.selectOption({label});
 else {await locator.click();await locator.page().locator('.ant-select-item-option').filter({hasText:new RegExp('^'+label+'$')}).click();}
 await hold(locator.page(),650);
}
function requiredEnv(name:string):string {const value=process.env[name];if(!value)throw Error('Missing '+name);return value;}
export const status=(page:Page)=>page.getByTestId('workbench').getAttribute('data-status');
export function remember(name:string,run:StoryRun){const file=requiredEnv('AEROAGENTSIM_DOCS_CONTEXT'),context=recordingContext();context.stories[name]=run;writeFileSync(file,JSON.stringify(context,null,2));}
export function story(name:string):StoryRun {const run=recordingContext().stories[name];if(!run)throw Error('Record '+name+' first');return run;}
export function currentRun(page:Page):StoryRun {const runId=new URL(page.url()).searchParams.get('run');if(!runId)throw Error('No initialized run');return {workspaceId:new URL(page.url()).pathname.split('/').at(-1)!,runId};}
export async function english(page:Page){const zh=page.getByRole('button',{name:'切换到英文',exact:true});if(await zh.count())await zh.last().click();}
export async function gallery(page:Page){await page.goto('/?gallery=1');await expect(page.getByTestId('wb-gallery')).toBeVisible();await english(page);}
export async function createProject(page:Page,template:string){await gallery(page);await page.getByTestId('wb-template-'+template).click();await page.getByTestId('wb-create-project').click();await draftReady(page);}
export async function draftReady(page:Page){await expect(page.getByTestId('wb-config')).toBeVisible({timeout:120_000});await expect(page.getByTestId('draft-preview')).toHaveAttribute('data-rendered','true',{timeout:180_000});await expect(page.getByTestId('draft-preview')).toHaveAttribute('aria-busy','false',{timeout:180_000});}
export async function viewportReady(page:Page){await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true',{timeout:180_000});await hold(page,1200);}
export async function initialize(page:Page){await page.getByTestId('wb-validate').click();await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});await page.getByTestId('wb-save-version').click();await expect(page.getByTestId('wb-initialize')).toBeEnabled();await page.getByTestId('wb-initialize').click();await expect.poll(()=>status(page),{timeout:120_000}).toBe('initialized');await viewportReady(page);await expect(page.locator('.wb-clock time')).toHaveText('00:00.0');return currentRun(page);}
export async function interactive(page:Page){await choose(page.getByTestId('knob-run_mode'),'interactive');await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();await expect(page.getByText('Controls applied to the draft.',{exact:true})).toBeVisible();}
export async function openRun(page:Page,run:StoryRun,mode='replay'){await page.goto(`/w/${run.workspaceId}?run=${run.runId}&mode=${mode}`);await english(page);await viewportReady(page);await expect(page.getByTestId('wb-catching-up')).toBeHidden({timeout:180_000});if(mode==='replay'){const slider=page.getByRole('slider',{name:'Recorded timeline',exact:true});await slider.fill((await slider.getAttribute('max'))!);await slider.dispatchEvent('input');await expect(page.getByTestId('wb-seeking')).toBeHidden();}}
export async function selectObject(page:Page,id:string){const search=page.getByRole('textbox',{name:'Search objects',exact:true});await fill(search,id);const row=page.getByTestId('wb-objects').getByTitle(id,{exact:true});await click(row);await expect(row).toHaveAttribute('aria-current','true');await expect(page.getByTestId('wb-inspector')).toContainText(id);await hold(page,1200);await fill(search,'');}
export async function complete(page:Page){if(await status(page)!=='completed')await page.getByTestId('wb-speed').selectOption('max');await expect.poll(()=>status(page),{timeout:600_000}).toBe('completed');await expect(page.getByTestId('wb-catching-up')).toBeHidden({timeout:180_000});}
export async function evidence(page:Page,name:string){const dir=join(requiredEnv('AEROAGENTSIM_DOCS_WORKDIR'),'screenshots');mkdirSync(dir,{recursive:true});await page.screenshot({path:join(dir,name+'.png')});}
export async function hero(page:Page,name:string){
 // One live page owns the operator source. Resize that page for every image.
 await page.setViewportSize({width:1600,height:1000});
 await page.evaluate(()=>{for(const id of ['docs-cursor','docs-highlight','docs-story']){const el=document.getElementById(id);if(el)el.style.display='none';}});
 const zh=name.endsWith('-zh');
 await page.getByTestId('wb-camera').getByTitle(zh?'重置视角':'Reset view',{exact:true}).click();
 await viewportReady(page);await expect(page.getByTestId('viewport')).toHaveAttribute('data-city','loaded',{timeout:180_000});await expect(page.locator('html')).toHaveAttribute('lang',zh?'zh-CN':'en');
 await expect(page.getByTestId('wb-inspector')).not.toContainText('Nothing selected');await expect.poll(()=>status(page),{timeout:30_000}).toBe('running');await hold(page,1600);await evidence(page,name);
 await page.setViewportSize({width:1920,height:1080});await viewportReady(page);await evidence(page,name+'-1920');await page.setViewportSize({width:1280,height:800});
}
