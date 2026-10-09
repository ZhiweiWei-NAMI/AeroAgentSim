import {flowTest as test,expect,api,click,hold,openWorkspace,openReplay,segment,step,assertSynchronized} from './helpers';

test('Validate → start → real operator accident → detection, award and city capture',async({page})=>{
 test.setTimeout(1_800_000);
 const draft=await openWorkspace(page,'Traffic accident · operate');
 // Recording is deliberate operator interaction: the current demo's 10s wall
 // timeout is too short while software-rendered city geometry loads. This is
 // an explicit temporary-draft policy, not a runtime or simulated-time change.
 const operator=draft.scenario.ingress_streams.find((stream:any)=>stream.id==='operator');expect(operator).toBeDefined();operator.timeout_s=300;
 await api(page,'/v1/studio/workspaces/'+draft.id,{scenario:draft.scenario});
 await page.reload({waitUntil:'domcontentloaded'});await expect(page.getByTestId('studio-step-validate')).toBeVisible({timeout:90_000});await step(page,'validate');
 await segment(page,async()=>{await hold(page,1000);await click(page.getByRole('button',{name:'Validate draft',exact:true}));},'Validate the AeroGraph scene and behaviour rules');
 await expect(page.getByRole('button',{name:'Start run',exact:true})).toBeEnabled({timeout:90_000});
 await segment(page,async()=>{await hold(page,1400);await click(page.getByRole('button',{name:'Start run',exact:true}));},'Start one shared discrete-event runtime');
 await page.waitForURL(/\/runs\/run-/,{timeout:90_000});
 const run=page.url().match(/\/runs\/(run-[a-f0-9]+)/)![1];
 try{
  await expect(page.getByTestId('dual-run-views')).toBeVisible({timeout:90_000});
  await page.getByLabel('Injection point',{exact:true}).selectOption('accident');
  await expect(page.getByRole('button',{name:'Submit typed ingress',exact:true})).toBeEnabled({timeout:60_000});
  await segment(page,async()=>{
   await click(page.getByLabel('Injection point',{exact:true}));await hold(page,850);await page.keyboard.press('Escape');
   await click(page.getByRole('button',{name:'Submit typed ingress',exact:true}));
   await expect(page.getByText('Admission receipts · 1',{exact:true})).toBeVisible({timeout:30_000});await hold(page,1300);
  },'Inject the accident · admission and execution are separate receipts');
  // Leave the operator controller before closing its source prefix in preparation.
  // This is real source progress, not a fabricated scenario result.
  await page.goto('/runs');
  await api(page,'/v1/runs/'+run+'/watermark',{stream_id:'operator',watermark_ns:draft.scenario.run.until_ns});
  await expect.poll(async()=>{
   const actual=(await api(page,'/v1/runs')).find((row:any)=>row.id===run);
   if(['faulted','stopped','interrupted'].includes(actual.status))throw Error(JSON.stringify(actual));
   return actual.status;
  },{timeout:1_500_000,intervals:[2000]}).toBe('completed');
  const photos=await api(page,'/v1/runs/'+run+'/artifacts');expect(photos.length).toBeGreaterThan(0);expect(photos[0].renderer_mode).toBe('browser');
  await openReplay(page,run);
  await expect(page.getByRole('button',{name:/^Seek capture at /}).first()).toBeVisible({timeout:90_000});
  await segment(page,async()=>{
   for(const marker of ['accident','award','capture']){
    await click(page.getByRole('button',{name:new RegExp('^Seek '+marker+' at ')}).first());await assertSynchronized(page);await hold(page,1500);
   }
  },'Replay the actual detection → award → capture chain · idle time omitted');
  await page.getByRole('button',{name:'Open photo',exact:true}).first().scrollIntoViewIfNeeded();
  await segment(page,async()=>{await click(page.getByRole('button',{name:'Open photo',exact:true}).first());},'Inspect the camera capture from the real city viewer');
  await expect(page.getByAltText('Stored capture photo')).toBeVisible({timeout:30_000});
  await page.getByAltText('Stored capture photo').scrollIntoViewIfNeeded();await segment(page,async()=>{await hold(page,2300);});
 }finally{
  const actual=(await api(page,'/v1/runs')).find((row:any)=>row.id===run);
  if(!['completed','stopped','faulted','interrupted'].includes(actual.status))await api(page,'/v1/runs/'+run+'/stop',{});
 }
});
