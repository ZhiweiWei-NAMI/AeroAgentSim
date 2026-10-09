import {flowTest as test,expect,openWorkspace,step,click,fill,choose,hold,segment,api} from './helpers';

test('A predicate rule creates type bindings, event-chain states and operator injection points',async({page})=>{
 const draft=await openWorkspace(page,'Traffic accident · rules');
 const predicate='traffic.p.energy_ready';
 draft.scenario.behaviours[0].predicates[predicate]={profile:'committed_reactive/v1',roles:{actor:'aas:TrafficUAV'},expression:{op:'gt',args:[{field:'traffic.uav.energy_j',role:'actor',path:[]},{literal:{$number:'10000.0'}}]}};
 await api(page,'/v1/studio/workspaces/'+draft.id,{scenario:draft.scenario});
 await page.reload();await expect(page.getByTestId('studio-step-rules')).toBeVisible({timeout:90_000});await step(page,'rules');
 // A real, authored single-role predicate gives one instance per matching UAV.
 await segment(page,async()=>{
  await fill(page.getByLabel('Rule name',{exact:true}),'energy-ready-pause');
  await choose(page.getByRole('combobox',{name:'Rule predicate',exact:true}),'P Energy Ready');
  await choose(page.getByRole('combobox',{name:'Rule entity actor',exact:true}),'Every Traffic UAV');
  await choose(page.getByRole('combobox',{name:'Rule action kind',exact:true}),'Delay, then complete');
  await fill(page.getByLabel('Rule delay duration',{exact:true}),'1000000000');
  await click(page.getByRole('button',{name:'Add rule to draft',exact:true}));await hold(page,1600);
 },'When energy-ready becomes true for every Traffic UAV → delay, then complete');
 await segment(page,async()=>{
  const chains=page.locator('.ant-card').filter({has:page.locator('.ant-card-head-title').getByText('Event chains',{exact:true})});
  await chains.scrollIntoViewIfNeeded();await click(chains.locator('.ant-pagination-item').last());await hold(page,2000);
  await page.getByRole('heading',{name:'External events an operator can fire',exact:true}).scrollIntoViewIfNeeded();await hold(page,2200);
 },'Bindings instantiate chains; injection points accept typed operator events');
 const save=page.getByRole('button',{name:'Save workspace',exact:true});
 const saved=page.waitForResponse(response=>response.url().endsWith('/v1/studio/workspaces/'+draft.id)&&response.request().method()==='POST');await click(save);expect((await saved).ok()).toBe(true);
 const actual=await page.request.get('/v1/studio/workspaces/'+draft.id).then(response=>response.json());
 expect(actual.scenario.behaviours[0].chains['energy-ready-pause'].trigger).toEqual({predicate,edge:'entered'});
 expect(actual.scenario.behaviours[0].bindings.at(-1).match.actor).toEqual({is_a:'aas:TrafficUAV'});
 const compiled=await api(page,'/v1/studio/workspaces/'+draft.id+'/behaviours/0/validate',{});expect(compiled).toMatchObject({valid:true,compiler_available:true});
});
