import {flowTest as test,expect,api,click,hold,openWorkspace,segment,step,assertSynchronized,openReplay} from './helpers';

test('Home → guided Studio → real run → synchronized Inspect',async({page})=>{
 await page.goto('/');await expect(page.getByTestId('home-page')).toBeVisible();
 await segment(page,async()=>{await hold(page,2300);await click(page.getByTestId('open-traffic-demo'));},'Configure, run and inspect one shared simulation');
 await expect(page.getByTestId('studio-panel-scene')).toBeVisible({timeout:90_000});
 await expect(page.getByText('Loading city geometry…',{exact:true})).toBeHidden({timeout:90_000});
 await segment(page,async()=>{
  await hold(page,1500);await step(page,'entities');await step(page,'predicates');await step(page,'rules');await step(page,'agents');await step(page,'validate');
  await click(page.getByRole('button',{name:'Validate draft',exact:true}));
 },'AeroGraph entities, predicates, behaviours and agents');
 await expect(page.getByRole('button',{name:'Start run',exact:true})).toBeEnabled({timeout:90_000});
 await segment(page,async()=>{await hold(page,1400);await click(page.getByRole('button',{name:'Start run',exact:true}));},'Validate your scenario, then start the simulation');
 await page.waitForURL(/\/runs\/run-/,{timeout:90_000});
 const run=page.url().match(/\/runs\/(run-[a-f0-9]+)/)![1];
 await api(page,'/v1/runs/'+run+'/stop',{});
 // The simulation story records this full lifecycle; the hero uses the completed
 // recorded run for inspection so loading/waiting never becomes demo footage.
 await openReplay(page);
 await segment(page,async()=>{
  await hold(page,2300);await click(page.getByRole('button',{name:'Bravo',exact:true}));
  await expect(page.locator('.inspection-sidebar')).toContainText('UAV Bravo');
  await expect(page.getByText(/Run identity unavailable/)).toBeHidden();await assertSynchronized(page);await hold(page,3000);
 },'Replay a completed run · explore the same moment in graph and city');
});
