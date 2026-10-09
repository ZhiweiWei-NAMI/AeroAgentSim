import {flowTest as test,expect,openWorkspace,click,fill,choose,hold,segment} from './helpers';

test('Explore AeroGraph types, ancestry, field writers and workspace entities',async({page})=>{
 const draft=await openWorkspace(page,'Traffic accident · explore');
 await page.goto('/aerograph?workspace='+draft.id+'&type=aas%3ATrafficUAV');
 await expect(page.getByTestId('aerograph-tree')).toContainText('Traffic UAV',{timeout:90_000});
 await segment(page,async()=>{
  await fill(page.getByLabel('Search types'),'Traffic UAV');
  await click(page.getByTestId('aerograph-tree').getByText('Traffic UAV',{exact:true}).first());await hold(page,2100);
  const parent=page.locator('.aerograph-crumbs').getByRole('button').last();
  await click(parent);await hold(page,1400);
  await click(page.getByTestId('aerograph-tree').getByText('Traffic UAV',{exact:true}).first());
 },'Search Traffic UAV · follow its AeroGraph ancestry and relation graph');
 await segment(page,async()=>{
  const fields=page.locator('.aerograph-panel').filter({has:page.getByRole('heading',{name:'Fields',exact:true})});
  await fields.scrollIntoViewIfNeeded();await expect(fields).toContainText('Kinematic');await hold(page,2200);
  const entities=page.locator('.aerograph-panel').filter({has:page.getByRole('heading',{name:'Workspace entities of this type',exact:true})});
  await entities.scrollIntoViewIfNeeded();await hold(page,1400);
  await click(entities.getByRole('link').first());
 },'Inspect declared field writers · jump to a real workspace entity');
 await expect(page.locator('.guided-entity-drawer')).toBeVisible({timeout:90_000});
 await segment(page,async()=>{await hold(page,2900);},'The entity opens in Studio with its initial fields, relations and ancestry');
});
