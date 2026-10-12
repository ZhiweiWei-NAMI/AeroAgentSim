import {flowTest as test,expect,gallery,segment,click,hold,draftReady,viewportReady,status,currentRun,remember,complete,evidence,selectObject} from './helpers';
test('gallery to an initialized and running world',async({page})=>{
 await gallery(page);
 await segment(page,async()=>{await hold(page,2200);await click(page.getByTestId('wb-template-food-delivery'));await hold(page,2200);},'Choose a world and read its guide');
 await segment(page,async()=>{await click(page.getByTestId('wb-create-project'));},'Create a project');await draftReady(page);
 await segment(page,async()=>{await hold(page,3200);await click(page.getByTestId('wb-validate'));},'Configure the draft, then validate');
 await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});
 await segment(page,async()=>{await hold(page,900);await click(page.getByTestId('wb-save-version'));},'Save an immutable version');await expect(page.getByTestId('wb-initialize')).toBeEnabled();
 await segment(page,async()=>{await click(page.getByTestId('wb-initialize'));},'Initialize first — the world stays at t = 0');
 await expect.poll(()=>status(page),{timeout:120_000}).toBe('initialized');await viewportReady(page);await expect(page.locator('.wb-clock time')).toHaveText('00:00.0');remember('overview',currentRun(page));
 await segment(page,async()=>{await hold(page,2200);await click(page.getByTestId('wb-start'));},'Start is a separate, explicit action');await expect(page.getByTestId('wb-pause')).toBeVisible();
 await segment(page,async()=>{await hold(page,2800);await click(page.getByTestId('wb-graph-toggle'));},'One scene: objects, inspector, KPIs, graph and timeline');await expect(page.getByRole('combobox',{name:'Graph entity',exact:true})).toBeVisible();
 await segment(page,async()=>{await hold(page,2200);await click(page.getByTestId('wb-graph-toggle'));await selectObject(page,'drone-1');await hold(page,2200);},'Select a drone to focus the scene and inspector');await evidence(page,'overview-after');await complete(page);
});
