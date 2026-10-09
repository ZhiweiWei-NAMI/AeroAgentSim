import {test,expect} from '@playwright/test';
import {mkdirSync} from 'node:fs';
const screenshots=process.env.AEROAGENTSIM_SCREENSHOT_DIR ?? '/tmp/aas-q/e3f/screenshots';
test('real demo draft renders all guided steps, compiles and starts a run',async({page})=>{
 mkdirSync(screenshots,{recursive:true});
 const pageErrors:string[]=[];page.on('pageerror',error=>pageErrors.push(String(error)));
 await page.goto('/');await page.getByTestId('open-traffic-demo').click();
 await expect(page.getByTestId('studio-step-scene')).toBeVisible({timeout:90_000});
 await expect(page.getByLabel('Workspace name')).toHaveValue('Traffic accident (demo)');
 await page.getByText('Loading city geometry…',{exact:true}).waitFor({state:'hidden',timeout:90_000});
 for(const id of ['scene','entities','plugins','predicates','rules','agents','validate']){
  console.log(`Guided step: ${id}`);
  await page.getByTestId(`studio-step-${id}`).click();
  const panel=page.getByTestId(`studio-panel-${id}`);await expect(panel).toBeVisible();
  if(id==='entities'){
   await panel.locator('.guided-entities-group-label').filter({hasText:'Traffic UAV'}).click();
   await panel.getByRole('button',{name:'Inspect',exact:true}).first().click();
   await expect(page.getByRole('dialog').getByText('Type ancestry:')).toBeVisible();
   await page.locator('.ant-drawer-content-wrapper').evaluate(async element => {
    await Promise.all(element.getAnimations({subtree:true}).map(animation => animation.finished));
   });
   await page.screenshot({path:`${screenshots}/entity-detail.png`});
   await page.getByRole('dialog').getByRole('button',{name:'Close',exact:true}).click();
  }
  if(id==='plugins') await expect(panel.getByRole('combobox',{name:'Engine air_motion',exact:true})).toBeVisible();
  if(id==='predicates') await expect(panel.getByRole('combobox',{name:'Predicate evaluation profile',exact:true})).toBeVisible();
  if(id==='rules') await expect(panel.getByRole('combobox',{name:'Rule predicate',exact:true})).toBeVisible();
  if(id==='agents') await expect(panel.getByText('Scripted decisions',{exact:true})).toBeVisible();
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`${screenshots}/studio-${id}.png`});
 }
 await page.getByRole('button',{name:'Validate draft',exact:true}).click();
 await expect(page.getByRole('button',{name:'Start run',exact:true})).toBeEnabled({timeout:90_000});
 await expect(page.getByTestId('validation-status')).toContainText('Validated');
 await expect(page.locator('.guided-step-content')).toHaveAttribute('aria-busy','false');
 await page.screenshot({path:`${screenshots}/studio-validated.png`});
 await page.getByRole('button',{name:'Start run',exact:true}).click();
 await page.waitForURL(/\/runs\/run-/,{timeout:90_000});
 const workspace=(await page.request.get('/v1/studio/workspaces').then(response=>response.json())).find((row:{validation?:{valid:boolean}})=>row.validation?.valid===true).id;
 const id=page.url().match(/\/runs\/(run-[a-f0-9]+)/)![1];
 try{
  await expect(page.getByTestId('dual-run-views')).toBeVisible({timeout:60_000});
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-city','loaded',{timeout:60_000});
  await expect(page.getByTestId('inspect-views')).toHaveAttribute('data-city-binding','traffic-city');
  const runs=await page.request.get('/v1/runs');expect(runs.ok()).toBe(true);
  expect((await runs.json()).some((run:{id:string})=>run.id===id)).toBe(true);
  expect(pageErrors).toEqual([]);
 }finally{const stopped=await page.request.post(`/v1/runs/${id}/stop`);expect(stopped.ok()).toBe(true);}
 await page.goto(`/inspect/${id}?mode=replay`);
 await expect(page.getByTestId('viewport')).toHaveAttribute('data-city','loaded',{timeout:60_000});
 await expect(page.getByTestId('inspect-views')).toHaveAttribute('data-city-binding','traffic-city');
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`${screenshots}/inspect.png`});
 await page.goto(`/aerograph?workspace=${workspace}`);
 await expect(page.getByTestId('aerograph-tree')).toBeVisible({timeout:60_000});
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`${screenshots}/aerograph.png`,fullPage:true});
 await page.goto('/runs');await expect(page.getByRole('heading',{name:'Runs',exact:true})).toBeVisible();
 await page.screenshot({path:`${screenshots}/runs.png`});
 await page.goto('/inspect');await expect(page.getByRole('heading',{name:'Inspect',exact:true})).toBeVisible();
 await page.screenshot({path:`${screenshots}/inspect-list.png`});
 await page.goto('/');await expect(page.getByTestId('workspace-card').first()).toBeVisible();
 expect(await page.getByTestId('workspace-card').count()).toBeLessThanOrEqual(6);
 await page.screenshot({path:`${screenshots}/home.png`});
});
