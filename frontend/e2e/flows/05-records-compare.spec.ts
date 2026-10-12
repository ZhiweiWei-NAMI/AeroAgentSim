import {flowTest as test,expect,story,openRun,segment,click,hold,evidence,createProject,initialize,hero,selectObject,draftReady,status,remember,currentRun} from './helpers';
test('records replay, back to live, comparison and modify retry',async({page})=>{
 const food=story('food-delay');await openRun(page,food,'live');await page.getByTestId('wb-records-toggle').click();await page.getByRole('button',{name:'All runs',exact:true}).click();const record=()=>page.getByTestId('wb-records').locator('.wb-record').filter({hasText:food.runId});await expect(record()).toBeVisible();
 await segment(page,async()=>{await hold(page,2200);await click(record().getByTestId('wb-open-run'));},'Records keeps each run and its pinned version');await expect(page).toHaveURL(/mode=replay/);await expect(page.getByTestId('wb-catching-up')).toBeHidden({timeout:180_000});
 const slider=page.getByRole('slider',{name:'Recorded timeline',exact:true});await expect.poll(async()=>Number(await slider.getAttribute('max')),{timeout:180_000}).toBeGreaterThan(0);const max=Number(await slider.getAttribute('max'));
 await segment(page,async()=>{await slider.fill(String(Math.floor(max/2)));await slider.dispatchEvent('input');await hold(page,2800);},'Replay browses history without changing the simulation');await expect(page.getByTestId('wb-history')).toBeVisible();await evidence(page,'records-history');
 await segment(page,async()=>{await click(page.getByTestId('wb-back-live'));await hold(page,1600);await click(page.getByTestId('wb-records-toggle'));await click(page.getByRole('button',{name:'All runs',exact:true}));},'Back to live restores the latest recorded state');
 for(const run of [food,story('overview')])await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:run.runId}).getByRole('checkbox').check();
 await segment(page,async()=>{await click(page.getByTestId('wb-compare'));});await expect(page.locator('.newc-compare')).toBeVisible({timeout:90_000});
 await segment(page,async()=>{await hold(page,5500);await evidence(page,'records-compare');await click(page.getByRole('button',{name:'Go back',exact:true}));},'Compare two real runs: configuration and outcomes');await evidence(page,'records-compare-return');
 await segment(page,async()=>{await click(record().getByRole('button',{name:'Modify & retry',exact:true}));},'Modify & retry creates a new draft and retains the source run');await draftReady(page);await segment(page,async()=>{await hold(page,4000);});await evidence(page,'records-derived');
});
test('records comparison and derivation evidence',async({page})=>{
 const food=story('food-delay');await openRun(page,food,'live');await page.getByTestId('wb-records-toggle').click();await page.getByRole('button',{name:'All runs',exact:true}).click();
 for(const run of [food,story('overview')])await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:run.runId}).getByRole('checkbox').check();
 await page.getByTestId('wb-compare').click();await expect(page.locator('.newc-compare')).toBeVisible({timeout:90_000});await evidence(page,'records-compare');await page.getByRole('button',{name:'Go back',exact:true}).click();
 await page.getByTestId('wb-records').locator('.wb-record').filter({hasText:food.runId}).getByRole('button',{name:'Modify & retry',exact:true}).click();await draftReady(page);await evidence(page,'records-derived');
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
