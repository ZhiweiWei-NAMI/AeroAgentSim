import {flowTest as test,expect,story,openRun,segment,click,hold,evidence} from './helpers';
test('restaurant delay to ready, dispatch and delivered cause trace',async({page})=>{
 await openRun(page,story('food-delay'));await page.getByTestId('wb-dock').getByRole('button',{name:/^Events\b/}).click();
 const event=page.locator('.newc-event-item').filter({hasText:/preparation delay/}).first();await expect(event).toBeVisible();await event.scrollIntoViewIfNeeded();
 await segment(page,async()=>{await hold(page,2800);await click(event.getByRole('button',{name:'Why did this happen?',exact:true}));},'Timeline events link to their recorded causes');
 const trace=page.getByTestId('cause-trace');await expect(trace).toContainText(/Food Ready/);await expect(trace).toContainText(/Task Assigned/);await expect(trace).toContainText(/Food Delivered/);
 await segment(page,async()=>{await hold(page,3500);await click(trace.locator('.ct-follow-btn').filter({hasText:/Food Ready/}).first());await hold(page,2400);},'Delay → preparation ready');await evidence(page,'why-ready');
 await trace.locator('.ct-head').getByRole('button',{name:'← Back',exact:true}).click();
 await segment(page,async()=>{await click(trace.locator('.ct-follow-btn').filter({hasText:/Task Assigned/}).first());await hold(page,3000);},'Ready → task assigned to a courier');
 await trace.locator('.ct-head').getByRole('button',{name:'← Back',exact:true}).click();
 await segment(page,async()=>{await click(trace.locator('.ct-follow-btn').filter({hasText:/Food Delivered/}).first());await hold(page,5000);},'Dispatch → physical delivery');await evidence(page,'why-delivered');
});
