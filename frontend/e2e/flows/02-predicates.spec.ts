import {flowTest as test,expect,openWorkspace,step,click,fill,hold,segment} from './helpers';

test('Edit an ancestry-aware predicate threshold and compile the real draft',async({page})=>{
 await openWorkspace(page,'Traffic accident · predicates');await step(page,'predicates');
 await segment(page,async()=>{
  await hold(page,1800);
  const row=page.locator('.guided-table tbody tr').filter({hasText:'P Road Conflict'});
  for(let i=0;i<5&&!await row.isVisible();i++){const next=page.getByTestId('studio-panel-predicates').locator('.ant-pagination-next:not(.ant-pagination-disabled)');await expect(next).toHaveCount(1);await click(next);}
  await click(row.locator('input[type=radio]'));await hold(page,1200);
  const field=page.getByLabel('Predicate expression.args[0].args[0] field',{exact:true});
  await fill(field,'traffic.road.blocked_by');
  await expect(page.locator('datalist[id="Predicate expression.args[0].args[0]-fields"] option[value="traffic.road.blocked_by"]')).toHaveCount(1);
  await hold(page,1200);
 },'AeroGraph fields constrain the predicate · count vehicles blocking the road');
 await segment(page,async()=>{
  await fill(page.getByLabel(/^Predicate expression\.args\[1\] literal(?: exact integer)?$/),'1');await hold(page,1900);
  await click(page.getByRole('button',{name:'Validate',exact:true}));
 },'Set the blocking threshold to 1 · validate with the real compiler');
 await expect(page.getByTestId('validation-status')).toContainText('Validated',{timeout:120_000});
 await segment(page,async()=>{await page.evaluate(()=>window.scrollTo(0,0));await hold(page,2500);},'The compiler accepts the edited scenario');
});
