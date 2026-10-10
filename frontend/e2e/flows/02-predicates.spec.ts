import {flowTest as test,expect,api,step,click,fill,hold,segment,openReplay} from './helpers';

test('Edit the shared battery reserve and follow predicate → chain → committed state',async({page})=>{
 const receipt=await api(page,'/v1/templates/parcel-delivery/workspace',{knobs:{}});
 const wid=receipt.workspace_id;
 await page.goto('/studio?workspace='+wid);
 await expect(page.getByTestId('studio-panel-scene')).toBeVisible({timeout:90_000});
 await step(page,'predicates');
 const predicates=page.getByTestId('studio-panel-predicates');
 for(const name of ['Battery Below Reserve','Inside No Fly Zone','Closer Than Safety Distance','Wind Above Limit'])await expect(predicates).toContainText(name);
 await segment(page,async()=>{
  await hold(page,1800);
  await fill(page.getByLabel(/^parameters\.reserve_percent(?:\.\$number)?$/),'85');
  await hold(page,1800);
  await click(page.getByRole('button',{name:'Save workspace',exact:true}));
  await click(page.getByRole('button',{name:'Validate',exact:true}));
 },'Edit a real policy: recall the drone below 85% battery');
 await expect(page.getByTestId('validation-status')).toContainText('Validated',{timeout:120_000});
 await segment(page,async()=>{
  await step(page,'rules');
  const chains=page.locator('.ant-card').filter({has:page.locator('.ant-card-head-title').getByText('Event chains',{exact:true})});
  await chains.scrollIntoViewIfNeeded();
  for(const name of ['Battery Reserve','No Fly Zone','Separation','Wind Limit'])await expect(chains).toContainText(name);
  await hold(page,2400);
 },'The same four rules are visible, editable event chains');
 // Run the saved draft, not a synthetic trace. Omit backend waiting from footage.
 const draft=await api(page,'/v1/studio/workspaces/'+wid);
 const started=await api(page,'/v1/runs',{scenario:draft.scenario,studio_workspace:wid});
 await expect(async()=>{
  const runs=await api(page,'/v1/runs');
  const run=runs.find((row:any)=>row.id===started.id);
  expect(run?.status).not.toMatch(/faulted|stopped|interrupted|input_timeout/);
  expect(run?.status).toBe('completed');
 }).toPass({timeout:240_000,intervals:[1000,2000]});
 let cursor=1,finalCut=0,low=false;
 while(true){
  const batch=await api(page,`/v1/runs/${started.id}/commits?from=${cursor}&limit=1000`);
  if(!batch.commits.length)break;
  for(const commit of batch.commits){finalCut=commit.commitIndex;low ||= commit.messages.some((message:any)=>message.schemaId==='lowalt.energy.low');}
  cursor=batch.next;
 }
 expect(low).toBe(true);
 await openReplay(page,started.id,'simple');
 const timeline=page.locator('.simple-timeline').getByRole('slider');
 await expect(timeline).toHaveAttribute('aria-valuemax',String(finalCut-1),{timeout:120_000});
 await timeline.focus();await timeline.press('End');
 const event=page.locator('.newc-event-item').filter({hasText:/battery low|low battery/}).first();
 await expect(event).toBeVisible({timeout:30_000});
 await segment(page,async()=>{await click(event.getByRole('button',{name:'Why did this happen?',exact:true}));},'Replay the edited policy · ask why the battery event happened');
 const trace=page.getByTestId('cause-trace');
 await expect(trace).toBeVisible();
 if(await trace.getByRole('button',{name:'See that step',exact:true}).isVisible())await click(trace.getByRole('button',{name:'See that step',exact:true}));
 await expect(trace).toContainText('Battery Reserve');
 await expect(trace).toContainText('reserve percent (85)');
 await expect(trace).toContainText('returning to charge');
 await expect(trace.locator('.ct-before')).toContainText('false');
 await expect(trace.locator('.ct-after')).toContainText('true');
 await segment(page,async()=>{await trace.scrollIntoViewIfNeeded();await hold(page,4500);},'Committed battery → predicate → chain → state change, with one writer');
});
