import {flowTest as test,expect,story,segment,click,hold,evidence,createProject,initialize,hero,selectObject,draftReady,status,remember,currentRun} from './helpers';
test('live history and back to live, then completed replay, comparison and modify retry',async({page})=>{
 const food=story('food-delay'),baseline=story('overview');
 // The prerequisite runs are complete. A fresh scheduled run supplies live
 // history without restarting or modifying either retained record.
 await createProject(page,'food-delivery');const live=await initialize(page);remember('records-live',live);
 try {
  await page.getByTestId('wb-speed').selectOption('5');await page.getByTestId('wb-start').click();
  await expect.poll(()=>status(page),{timeout:60_000}).toBe('running');
  const slider=page.getByRole('slider',{name:'Recorded timeline',exact:true});
  await expect.poll(async()=>Number(await slider.getAttribute('max')),{timeout:180_000}).toBeGreaterThan(10);
  await page.getByTestId('wb-speed').selectOption('1');const liveHead=Number(await slider.getAttribute('max'));
  await segment(page,async()=>{
   await slider.fill(String(Math.floor(liveHead/2)));await slider.dispatchEvent('input');
   await expect(page.getByTestId('wb-history')).toBeVisible();await expect(page.getByTestId('wb-back-live')).toBeVisible();await hold(page,2400);
  },'Seek into a running world: Viewing history and Back to live');
  await expect.poll(()=>status(page)).toBe('running');await evidence(page,'records-live-history');
  await segment(page,async()=>{await click(page.getByTestId('wb-back-live'));await expect(page.getByTestId('wb-history')).toBeHidden();await hold(page,1600);},'Back to live restores the latest moment of this running world');
  await expect.poll(()=>status(page)).toBe('running');await evidence(page,'records-back-live');
 } finally {
  // Single-page recording, with no live worker left behind on failure.
  const response=await page.request.post(`/v1/runs/${live.runId}/stop`);expect(response.ok(),await response.text()).toBe(true);
 }
 await expect.poll(()=>status(page),{timeout:120_000}).toBe('stopped');
 const records=page.getByTestId('wb-records'),record=()=>records.locator('.wb-record').filter({hasText:food.runId});
 await segment(page,async()=>{await click(page.getByTestId('wb-records-toggle'));await click(page.getByRole('button',{name:'All runs',exact:true}));},'Records retains finished runs and their pinned versions');
 await expect(record()).toBeVisible();await expect(record()).toContainText('Completed');
 await segment(page,async()=>{await hold(page,1800);await click(record().getByRole('button',{name:'Replay',exact:true}));});
 await expect(page).toHaveURL(/mode=replay/);await expect.poll(()=>status(page),{timeout:120_000}).toBe('completed');await expect(page.getByTestId('wb-catching-up')).toBeHidden({timeout:180_000});
 const replaySlider=page.getByRole('slider',{name:'Recorded timeline',exact:true});
 await expect.poll(async()=>Number(await replaySlider.getAttribute('max')),{timeout:180_000}).toBeGreaterThan(0);const replayHead=Number(await replaySlider.getAttribute('max'));
 await segment(page,async()=>{await replaySlider.fill(String(Math.floor(replayHead/2)));await replaySlider.dispatchEvent('input');await expect(page.getByTestId('wb-seeking')).toBeHidden();await hold(page,2400);},'Replay a finished run through its recorded timeline');
 await expect(page.locator('.wb-clock small')).toHaveText('Replay time');await expect(page.getByTestId('wb-history')).toBeHidden();await expect(page.getByTestId('wb-back-live')).toBeHidden();await evidence(page,'records-completed-replay');
 await segment(page,async()=>{await click(page.getByTestId('wb-records-toggle'));await click(page.getByRole('button',{name:'All runs',exact:true}));});
 for(const run of [food,baseline])await records.locator('.wb-record').filter({hasText:run.runId}).getByRole('checkbox').check();
 await segment(page,async()=>{await click(page.getByTestId('wb-compare'));});await expect(page.locator('.newc-compare')).toBeVisible({timeout:90_000});
 await segment(page,async()=>{await hold(page,5500);await evidence(page,'records-compare');await click(page.getByRole('button',{name:'Go back',exact:true}));},'Compare two real runs: configuration and outcomes');await evidence(page,'records-compare-return');
 await segment(page,async()=>{await click(record().getByRole('button',{name:'Modify & retry',exact:true}));},'Modify & retry creates a new draft and retains the source run');await draftReady(page);await segment(page,async()=>{await hold(page,4000);});await evidence(page,'records-derived');
});
test('running documentation screenshots in English and Chinese',async({page})=>{
 // Scheduled screenshot runs keep the top-bar Running state visible throughout capture.
 await createProject(page,'food-delivery');await initialize(page);await page.getByTestId('wb-speed').selectOption('1');await page.getByTestId('wb-start').click();await expect(page.getByTestId('wb-pause')).toBeVisible();remember('food-hero',currentRun(page));
 await expect.poll(async()=>{const time=(await page.locator('.wb-clock time').innerText()).split(':');return Number(time[0])*60+Number(time[1]);},{timeout:120_000}).toBeGreaterThanOrEqual(45);
 await selectObject(page,'drone-2');await hero(page,'workbench-food');await page.getByRole('button',{name:'Switch to Chinese',exact:true}).click();await hero(page,'workbench-food-zh');await page.getByRole('button',{name:'切换到英文',exact:true}).click();await page.getByTestId('wb-stop').click();await expect.poll(()=>status(page),{timeout:120_000}).toBe('stopped');
 // A separate real city-day run supplies the documentation hero, outside footage.
 await createProject(page,'city-day');await initialize(page);await page.getByTestId('wb-speed').selectOption('1');await page.getByTestId('wb-start').click();await expect(page.getByTestId('wb-pause')).toBeVisible();remember('city-hero',currentRun(page));await selectObject(page,'uav.bravo');await page.getByTestId('wb-graph-toggle').click();await expect(page.getByRole('combobox',{name:'Graph entity',exact:true})).toBeVisible();await hero(page,'workbench-city');
 await page.getByTestId('wb-stop').click();await expect.poll(()=>status(page),{timeout:120_000}).toBe('stopped');
});
