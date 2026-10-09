import {flowTest as test,expect,openWorkspace,step,click,fill,choose,hold,segment} from './helpers';

test('Configure replaceable engines and the weather field writer',async({page})=>{
 await openWorkspace(page,'Traffic accident · plugins');await step(page,'plugins');
 await segment(page,async()=>{
  await choose(page.getByRole('combobox',{name:'Engine air_motion',exact:true}),'PX4 Gazebo');
  await hold(page,1300);
  await page.getByRole('heading',{name:'Field writers / replacement profiles'}).scrollIntoViewIfNeeded();
  await expect(page.locator('.guided-ownership-table')).toContainText('PX4 Gazebo');await hold(page,1600);
 },'Choose PX4/Gazebo for flight and check which fields it controls');
 await segment(page,async()=>{
  await choose(page.getByRole('combobox',{name:'Engine air_motion',exact:true}),'Kinematic');
  await click(page.locator('.ant-collapse-header').filter({hasText:'Road traffic'}));
  await choose(page.getByRole('combobox',{name:'Engine road_motion',exact:true}),'Sumo');await hold(page,1300);
 },'Switch back to Kinematic, then choose SUMO for traffic');
 await segment(page,async()=>{
  await click(page.locator('.ant-collapse-header').filter({hasText:'Weather & environment'}));
  await fill(page.getByLabel('Weather profiles.wind.region.oo:digital_twin.wind.horizontalVelocity.mode',{exact:true}),'windy');
  await fill(page.getByLabel('Weather profiles.wind.region.oo:digital_twin.wind.horizontalVelocity.value[0][0]',{exact:true}),'3');
  await hold(page,1800);
  await page.getByRole('heading',{name:'Field writers / replacement profiles'}).scrollIntoViewIfNeeded();await hold(page,1700);
 },'Set wind to 3 m/s · see which plugin updates each field');
 // These are draft profile selections; launching native engines requires their config and validation.
});
