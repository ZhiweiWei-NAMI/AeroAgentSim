import {flowTest as test,expect,recordingContext,click,fill,hold,segment,step,api,openWorkspace} from './helpers';

test('Recorded LangGraph decisions, live-provider profile and granted tools',async({page})=>{
 const context=recordingContext();
 const recorded=await api(page,'/v1/studio/runs/'+context.runId+'/configuration');
 const workspace=await openWorkspace(page,'Traffic accident · agents');
 await api(page,'/v1/studio/workspaces/'+workspace.id,{scenario:recorded.scenario});
 await page.goto('/studio?workspace='+workspace.id);await expect(page.getByTestId('studio-step-agents')).toBeVisible({timeout:90_000});await step(page,'agents');
 await segment(page,async()=>{
  await expect(page.getByText('Recorded model responses',{exact:true})).toBeVisible();await hold(page,1700);
  await page.locator('.ant-card-head-title').filter({hasText:'Graph & tool access'}).scrollIntoViewIfNeeded();await hold(page,2100);
  const profile=process.env.AEROAGENTSIM_DOCS_PROVIDER_PROFILE??'default';
  await page.getByLabel('Live provider profile').scrollIntoViewIfNeeded();
  await fill(page.getByLabel('Live provider profile'),profile);
  await click(page.getByRole('button',{name:'Use live LangGraph for next run',exact:true}));
 },'Choose a live model for the next run · configuration only');
 await expect(page.getByText('Live LangGraph',{exact:true})).toBeVisible({timeout:90_000});
 await segment(page,async()=>{await page.evaluate(()=>window.scrollTo(0,0));await hold(page,1800);},'Choose where agents decide and which tools they may use');
 await page.goto('/agents/'+context.runId+'?mode=replay');
 await expect(page.locator('.agent-decision-card').first()).toBeVisible({timeout:90_000});
 await expect(page.getByRole('heading',{name:'Loading decisions',exact:true})).toBeHidden();
 await segment(page,async()=>{
  await page.locator('.agent-decision-card').first().scrollIntoViewIfNeeded();await hold(page,2200);
  await page.locator('.agent-proposed-call').first().scrollIntoViewIfNeeded();await hold(page,2500);
 },'Review recorded agent observations, responses and proposed actions');
});
