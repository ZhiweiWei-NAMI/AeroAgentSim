import {test as base,expect,type Locator,type Page} from '@playwright/test';
import {readFileSync,writeFileSync,existsSync,mkdirSync} from 'node:fs';
import {join,basename,dirname} from 'node:path';
import {parseAuthoringJson,stringifyLossless} from '../../src/feeds/lossless-json';

interface RecordingContext {baseURL:string;workspaceId:string;runId:string;events:Record<string,{cut:number;ns:string}>;finalCut:number}
interface Segment {path:string;start:number;duration:number}
const recordings=new WeakMap<Page,{start:number;segments:Segment[]}>();
export function recordingContext():RecordingContext {
 const file=process.env.AEROAGENTSIM_DOCS_CONTEXT;
 if(!file)throw Error('Prepare a real completed run and set AEROAGENTSIM_DOCS_CONTEXT.');
 return JSON.parse(readFileSync(file,'utf8'));
}
export const flowTest=base.extend({
 page:async({page},use,testInfo)=>{
  const clock={start:Date.now(),segments:[] as Segment[]};recordings.set(page,clock);
  await page.addInitScript(()=>{
   const install=()=>{
    if(document.getElementById('docs-cursor'))return;
    const style=document.createElement('style');style.textContent='#docs-cursor{position:fixed;width:18px;height:18px;border:3px solid white;border-radius:50%;background:#0d9488;box-shadow:0 0 0 2px #0d9488,0 3px 12px #0005;z-index:2147483647;pointer-events:none;transition:left .22s,top .22s;display:none}#docs-highlight{position:fixed;border:2px solid #0d9488;border-radius:6px;background:#0d94880c;z-index:2147483646;pointer-events:none;display:none}#docs-story{position:fixed;left:204px;bottom:16px;padding:9px 14px;border-radius:8px;color:white;background:#0c2438ed;font:600 13px system-ui;box-shadow:0 3px 12px #0002;z-index:2147483645;pointer-events:none;display:none}';document.head.append(style);
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
  let succeeded=false;
  try{await use(page);expect(errors).toEqual([]);succeeded=true;}finally{
   const video=page.video();
   if(testInfo.status!=='passed'){
    const debug=join(process.env.AEROAGENTSIM_DOCS_VIDEO_DIR??'/tmp/aas-q/e3e/videos',basename(testInfo.file,'.spec.ts'));mkdirSync(debug,{recursive:true});
    await page.screenshot({path:join(debug,'failure.png')});writeFileSync(join(debug,'failure.txt'),await page.locator('body').innerText());
   }
   await page.close();
   if(video&&clock.segments.length&&succeeded&&testInfo.status==='passed'){
    const root=process.env.AEROAGENTSIM_DOCS_VIDEO_DIR??'/tmp/aas-q/e3e/videos';
    const flow=basename(testInfo.file,'.spec.ts'),path=join(root,flow,'video.webm');mkdirSync(dirname(path),{recursive:true});
    await video.saveAs(path);
    const manifest=join(root,'recordings.json');const data=existsSync(manifest)?JSON.parse(readFileSync(manifest,'utf8')):{};
    data[flow]={blackPrelude:true,segments:clock.segments.map(row=>({...row,path})),speed:flow==='00-overview'?1.3:1};
    writeFileSync(manifest,JSON.stringify(data,null,2)+'\n');
    await testInfo.attach('recording-segments',{body:JSON.stringify(data[flow]),contentType:'application/json'});
   }
  }
 },
});
export {expect};
export async function caption(page:Page,text:string){await page.evaluate(text=>{const el=document.getElementById('docs-story');if(!el)throw Error('Recording caption overlay was not installed');el.textContent=text;el.style.display='block';},text);}
export async function segment(page:Page,work:()=>Promise<void>,title?:string){
 const clock=recordings.get(page);if(!clock)throw Error('Use flowTest for segmented recording');
 if(title)await caption(page,title);
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
 else{const control=locator.locator('xpath=ancestor::div[contains(concat(" ",@class," ")," ant-select ")][1]');await point(control);await control.click();await hold(locator.page(),300);
  const option=locator.page().getByTitle(label,{exact:true}).last();
  for(let attempt=0;attempt<8&&!await option.isVisible();attempt++){
   await locator.page().locator('.ant-select-dropdown:visible .rc-virtual-list-holder').evaluate(el=>{el.scrollTop+=el.clientHeight;});await hold(locator.page(),120);
  }
  await option.click();}
 await hold(locator.page(),650);
}
export async function api(page:Page,path:string,body?:unknown):Promise<any>{
 const response=body===undefined?await page.request.get(path):await page.request.post(path,{data:stringifyLossless(body),headers:{'Content-Type':'application/json'}});
 if(!response.ok())throw Error(path+': '+response.status()+' '+await response.text());
 return parseAuthoringJson(await response.text());
}
export async function openWorkspace(page:Page,name='Traffic accident · configuration'){
 const workspace=await api(page,'/v1/studio/workspaces',{name});
 const draft=await api(page,'/v1/studio/workspaces/'+workspace.id+'/templates/traffic-accident',{console:true,capture_mode:'city'});
 await page.goto('/studio?workspace='+workspace.id);await expect(page.getByTestId('studio-panel-scene')).toBeVisible({timeout:90_000});
 await expect(page.getByRole('button',{name:'Save workspace',exact:true})).toBeEnabled({timeout:90_000});
 return draft;
}
export async function step(page:Page,id:string){
 await click(page.getByTestId('studio-step-'+id));await expect(page.getByTestId('studio-panel-'+id)).toBeVisible();
 await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await hold(page,500);
}
export async function openReplay(page:Page,runId=recordingContext().runId){
 await page.goto('/inspect/'+runId+'?mode=replay');
 await expect(page.getByTestId('dual-run-views')).toBeVisible({timeout:90_000});
 await expect(page.getByTestId('viewport')).toHaveAttribute('data-city','loaded',{timeout:90_000});
 await expect(page.locator('.viewer-timeline').getByRole('slider')).not.toHaveAttribute('aria-valuemax','0',{timeout:90_000});
 await page.evaluate(()=>window.scrollTo(0,0));await hold(page,400);
}
export async function validate(page:Page){
 await click(page.getByRole('button',{name:'Validate',exact:true}));
 await expect(page.getByTestId('validation-status')).toContainText('Validated',{timeout:90_000});
}
export async function assertSynchronized(page:Page){
 const cut=await page.getByTestId('dual-run-views').getAttribute('data-cut');
 await expect(page.getByRole('region',{name:'Synchronized AeroGraph view'})).toHaveAttribute('data-cut',cut!);
 await expect(page.getByTestId('synchronized-city')).toHaveAttribute('data-cut',cut!);
}
