import {flowTest as test,expect,createProject,interactive,initialize,segment,click,fill,choose,hold,selectObject,remember,complete,evidence} from './helpers';
test('live speed, linked selection and restaurant delay receipt',async({page})=>{
 await createProject(page,'food-delivery');await interactive(page);await page.getByRole('spinbutton',{name:'East kitchen preparation (s)',exact:true}).fill('80');await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();await expect(page.getByText('Controls applied to the draft.',{exact:true})).toBeVisible();const run=await initialize(page);remember('food-delay',run);await page.getByTestId('wb-start').click();await expect(page.getByTestId('wb-pause')).toBeVisible();
 await segment(page,async()=>{await hold(page,2400);await choose(page.getByTestId('wb-speed'),'5×');await choose(page.getByTestId('wb-speed'),'1×');await selectObject(page,'drone-1');},'Running world: simulation speed and a drone in the object list');
 const label=page.locator('.entity-label[data-entity-id="drone-1"]:visible');await expect(label).toBeVisible();
 await segment(page,async()=>{await click(label);await click(page.getByRole('button',{name:'Show in graph',exact:true}));},'Select in 3D, then inspect the same object in the graph');
 const picker=page.getByRole('combobox',{name:'Graph entity',exact:true});await expect(picker).toBeVisible();
 await segment(page,async()=>{await choose(picker,await picker.locator('option[value="drone-2:0"]').innerText());await expect(picker).toHaveValue('drone-2:0');await expect(page.getByTestId('wb-inspector')).toContainText('drone-2');await expect(page.getByTestId('wb-objects').locator('.wb-row[aria-current=true]')).toContainText('drone-2');await hold(page,1800);await click(page.getByTestId('wb-graph-toggle'));await selectObject(page,'drone-1');await click(page.getByRole('button',{name:'Follow',exact:true}));await hold(page,1600);},'Graph selection flies the camera; the inspector follows');
 await expect(page.getByTestId('wb-inspector')).toContainText('drone-1');
 await segment(page,async()=>{await click(page.getByRole('tab',{name:'Operate',exact:true}));},'Introduce an authored event through its typed form');
 const restaurant=page.getByLabel('Payload.restaurant',{exact:true});await expect(restaurant).toBeVisible();expect(await restaurant.evaluate(el=>el.tagName)).toBe('SELECT');
 await segment(page,async()=>{await choose(restaurant,await restaurant.locator('option[value="restaurant-east"]').innerText());await fill(page.getByLabel('Payload.delay_s',{exact:true}),'20');await click(page.getByTestId('inject-restaurant_delay'));},'Pick a restaurant, add 20 seconds, and submit');
 await expect(page.getByTestId('wb-inspector')).toContainText('Admission receipts');
 await page.getByTestId('wb-inspector').locator('summary').filter({hasText:/^Admission receipts/}).scrollIntoViewIfNeeded();
 await segment(page,async()=>{await hold(page,4000);},'The receipt records the real command admission');await evidence(page,'operate-receipt');await complete(page);
});
