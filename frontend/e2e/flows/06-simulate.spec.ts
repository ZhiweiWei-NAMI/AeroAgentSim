import {flowTest as test,expect,api,click,hold,openWorkspace,segment,step,assertSynchronized} from './helpers';

test('Validate → live simulation → operator accident → award and city photo',async({page})=>{
 test.setTimeout(600_000);
 const draft=await openWorkspace(page,'Traffic accident · operate');
 await step(page,'validate');
 await segment(page,async()=>{await hold(page,1000);await click(page.getByRole('button',{name:'Validate draft',exact:true}));},'Check the scene and rules before running');
 await expect(page.getByRole('button',{name:'Start run',exact:true})).toBeEnabled({timeout:90_000});
 await segment(page,async()=>{await hold(page,1000);await click(page.getByRole('button',{name:'Start run',exact:true}));},'Start the live simulation');
 await page.waitForURL(/\/runs\/run-/,{timeout:90_000});
 const run=page.url().match(/\/runs\/(run-[a-f0-9]+)/)![1];
 try{
  await expect(page.getByTestId('dual-run-views')).toBeVisible({timeout:90_000});
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-city','loaded',{timeout:90_000});
  await expect(page.getByRole('button',{name:'Submit typed ingress',exact:true})).toBeEnabled({timeout:60_000});
  await segment(page,async()=>{
   await click(page.getByRole('button',{name:'Submit typed ingress',exact:true}));
   await expect(page.getByText('Admission receipts · 1',{exact:true})).toBeVisible({timeout:30_000});await hold(page,1200);
  },'Inject an accident with one click');
  await page.getByRole('button',{name:'Alpha',exact:true}).click();
  await page.getByTestId('dual-run-views').scrollIntoViewIfNeeded();
  await segment(page,async()=>{
   const samples=await page.evaluate(async()=>{
    const values:{display:string;committed:string}[]=[],start=performance.now();
    await new Promise<void>(resolve=>{const sample=()=>{const node=document.querySelector<HTMLElement>('[data-testid=viewport]');if(node?.dataset.displayNs&&node.dataset.committedNs)values.push({display:node.dataset.displayNs,committed:node.dataset.committedNs});if(performance.now()-start<6500)requestAnimationFrame(sample);else resolve();};sample();});
    return values;
   });
   expect(samples.some(row=>BigInt(row.display)<BigInt(row.committed))).toBe(true);
   // Event commits can advance more often than pose samples; the display must
   // still move monotonically between those samples, behind the live head.
   expect(new Set(samples.map(row=>row.display)).size).toBeGreaterThan(2);
   expect(samples.every((row,index)=>index===0||BigInt(row.display)>=BigInt(samples[index-1].display))).toBe(true);
   await assertSynchronized(page);
  },'Follow live motion as agents respond');
  await expect(page.getByRole('button',{name:/^Seek award at /}).first()).toBeVisible({timeout:180_000});
  await segment(page,async()=>{
   await page.locator('.inspection-timeline').scrollIntoViewIfNeeded();await hold(page,2200);
  },'The task is awarded · both views follow the same live run');
  await expect.poll(async()=>{
   const actual=(await api(page,'/v1/runs')).find((row:any)=>row.id===run);
   if(['faulted','stopped','interrupted','input_timeout'].includes(actual.status))throw Error(JSON.stringify(actual));
   const photos=await api(page,'/v1/runs/'+run+'/artifacts');
   if(photos.length){expect(photos[0].renderer_mode).toBe('browser');expect(photos[0].request.camera.preset).toBe('actor-nadir');}
   return photos.length;
  },{timeout:300_000,intervals:[1000]}).toBeGreaterThan(0);
  await page.getByRole('button',{name:'Refresh stored artifacts',exact:true}).click();
  await expect(page.getByRole('button',{name:'Open photo',exact:true}).first()).toBeVisible({timeout:30_000});
  await segment(page,async()=>{await click(page.getByRole('button',{name:'Open photo',exact:true}).first());},'Open the photo taken by the city camera');
  await expect(page.getByAltText('Stored capture photo')).toBeVisible({timeout:30_000});
  await page.getByAltText('Stored capture photo').scrollIntoViewIfNeeded();await segment(page,async()=>{await hold(page,2300);});
 }finally{
  const actual=(await api(page,'/v1/runs')).find((row:any)=>row.id===run);
  if(!['completed','stopped','faulted','interrupted','input_timeout'].includes(actual.status))await api(page,'/v1/runs/'+run+'/stop',{});
 }
});
