import {flowTest as test,expect,createProject,segment,click,fill,hold,evidence} from './helpers';
test('controls, seven sections, located validation and YAML',async({page})=>{
 await createProject(page,'food-delivery');
 await segment(page,async()=>{await hold(page,1600);await fill(page.getByRole('spinbutton',{name:'Delivery drones',exact:true}),'4');await click(page.getByRole('button',{name:'Apply controls to draft',exact:true}));},'Controls change the real draft');await expect(page.getByText('Controls applied to the draft.',{exact:true})).toBeVisible();
 const nav=page.locator('.wb-config > nav');
 await segment(page,async()=>{for(const name of ['Scene & space','Objects','Relations & resources','Tasks & behaviours','Execution components','Algorithms & agents','Run & recording'])await click(nav.getByRole('button',{name,exact:true}));},'Seven sections describe one world');
 const seed=page.locator('[data-path="/run/seed"] input');
 await segment(page,async()=>{await fill(seed,'1.5');await click(nav.getByRole('button',{name:'Objects',exact:true}));await click(page.getByTestId('wb-validate'));},'An invalid seed is located in its own field');
 await expect(page.getByTestId('wb-issues')).toBeVisible();await expect(seed.locator('..')).toHaveClass(/is-focus/);await evidence(page,'configure-validation');
 await segment(page,async()=>{await hold(page,2200);await fill(seed,'42');await click(page.getByTestId('wb-validate'));},'Correct the field and validate again');await expect(page.getByTestId('wb-save-version')).toBeEnabled();
 await segment(page,async()=>{await click(nav.getByRole('button',{name:'File (YAML)',exact:true}));await click(page.getByRole('button',{name:'Load scenario file',exact:true}));},'YAML and forms edit the same configuration');await expect(page.getByRole('textbox',{name:'Scenario YAML',exact:true})).toContainText('seed: 42');
 await segment(page,async()=>{await hold(page,4000);});await evidence(page,'configure-yaml');
});
