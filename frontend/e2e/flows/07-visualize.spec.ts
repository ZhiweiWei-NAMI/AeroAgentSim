import {flowTest as test,expect,openReplay,click,choose,hold,segment,assertSynchronized} from './helpers';

test('Replay cameras, shared event markers, graph selection and the real captured photo',async({page})=>{
 await openReplay(page);
 await expect(page.getByRole('button',{name:/^Seek capture at /}).first()).toBeVisible({timeout:90_000});
 await segment(page,async()=>{
  await hold(page,1500);await click(page.getByRole('button',{name:'Reporter',exact:true}));await hold(page,1000);
  await click(page.getByRole('button',{name:'Edge overview',exact:true}));await hold(page,1000);
  await click(page.getByRole('button',{name:'Bravo',exact:true}));await hold(page,1000);
  for(const marker of ['accident','award','capture']){
   await click(page.getByRole('button',{name:new RegExp('^Seek '+marker+' at ')}).first());await assertSynchronized(page);await hold(page,1200);
  }
  await click(page.getByRole('button',{name:'Alpha',exact:true}));
  const bravo=page.locator('.eg-node[aria-label^="UAV Bravo,"]');await click(bravo.locator('.eg-node-dot'));await expect(bravo).toHaveAttribute('aria-pressed','true');await expect(page.locator('.inspection-sidebar')).toContainText('UAV Bravo');await assertSynchronized(page);await hold(page,1300);
 },'Explore recorded events with one synchronized graph + 3D cursor');
 await page.getByRole('button',{name:'Open photo',exact:true}).first().scrollIntoViewIfNeeded();
 await segment(page,async()=>{await click(page.getByRole('button',{name:'Open photo',exact:true}).first());},'Open the photo captured by the actual city viewer');
 await expect(page.getByAltText('Stored capture photo')).toBeVisible({timeout:30_000});
 await page.getByAltText('Stored capture photo').scrollIntoViewIfNeeded();
 await segment(page,async()=>{await hold(page,2800);},'Recorded camera pixels at the capture moment');
});
