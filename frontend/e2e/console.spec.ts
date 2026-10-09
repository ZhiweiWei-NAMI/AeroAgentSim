import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { fixtureCommits, fixtureHeader } from '../src/behaviours/extension-fixture';

test('fixture run synchronizes graph, 3D, identity and microstep while live tail grows', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', problem => errors.push(problem.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  let releaseTail!: () => void;
  const tail = new Promise<void>(resolve => { releaseTail = resolve; });
  await page.route('**/v1/runs', route => route.fulfill({json:[{id:'fixture-kernel-run',scenario:'Typed console fixture',status:'running',until_ns:'9007199254740993'}]}));
  await page.route('**/v1/runs/fixture-kernel-run/header', route => route.fulfill({json:fixtureHeader}));
  await page.route('**/v1/runs/fixture-kernel-run/commits?*', route => {
    const from = Number(new URL(route.request().url()).searchParams.get('from'));
    return route.fulfill({json:{commits:from === 1 ? fixtureCommits.slice(0,3) : [],next:4,status:'running'}});
  });
  await page.route('**/v1/runs/fixture-kernel-run/stream?*', async route => {
    await tail;
    await route.fulfill({contentType:'text/event-stream',body:`event: commit\ndata: ${JSON.stringify(fixtureCommits[3])}\n\nevent: end\ndata: {"status":"completed","finalCursor":5}\n\n`});
  });
  await page.route('**/v1/studio/runs/fixture-kernel-run/configuration', route => route.fulfill({json:{service_run_id:'fixture-kernel-run',kernel_run_id:'fixture-kernel-run',epoch:'fixture-epoch',scenario:{engines:{}}}}));
  await page.route('**/v1/runs/fixture-kernel-run/artifacts', route => route.fulfill({json:[]}));
  await page.goto('/runs/fixture-kernel-run?mode=live&api=http%3A%2F%2F127.0.0.1%3A4179', {waitUntil:'domcontentloaded'});
  await expect(page.getByTestId('dual-run-views')).toHaveAttribute('data-cut','3');
  await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true',{timeout:60_000});
  await page.getByRole('button', {name:/task → task-1/}).first().click();
  await expect(page.getByTestId('inspector')).toContainText('task-1');
  await expect(page.getByTestId('inspector')).toContainText('queued');
  await expect(page.locator('.run-selection-identity')).toContainText('fixture-epoch / task-1 / g1');
  await page.getByRole('button', {name:/Seek launch → moving/}).click();
  releaseTail();
  await expect(page.getByText(/4 commits · cut 3 · μ2/)).toBeVisible();
  await expect(page.getByTestId('dual-run-views')).toHaveAttribute('data-cut','3');
  await expect(page.locator('.chain-node')).toContainText('moving');
  await page.getByRole('button',{name:'Follow live',exact:true}).click();
  await expect(page.getByTestId('dual-run-views')).toHaveAttribute('data-cut','4');
  await expect(page.locator('.chain-node')).toContainText('done');
  await page.getByRole('button',{name:/vehicle → vehicle-1/}).click();
  await expect(page.getByTestId('inspector')).toContainText('[25,35,10]');
  mkdirSync('test-results/console/screenshots',{recursive:true});
  await page.evaluate(()=>{window.scrollTo(0,0);document.querySelector('.dual-graph-pane')?.scrollTo(0,0);});
  await page.screenshot({path:'test-results/console/screenshots/console-dual.png',fullPage:true});
  await page.screenshot({path:'test-results/console/screenshots/console-dual.jpg',fullPage:true,type:'jpeg',quality:70});
  expect(errors).toEqual([]);
});

test('capture bridge reads real WebGL PNG at the exact large-integer cut and rejects wrong identity', async ({ page }) => {
  const { createHash } = await import('node:crypto');
  const service = `run-${'a'.repeat(32)}`;
  const manifest = JSON.stringify({format:'aeroagentsim.capture-assets/v1',environment:'viewer-default/v1',files:[]});
  const digest = createHash('sha256').update(manifest).digest('hex');
  await page.route('**/capture-assets.json', route=>route.fulfill({contentType:'application/json',body:manifest}));
  await page.route(`**/v1/runs/${service}/header`,route=>route.fulfill({json:{...fixtureHeader,runId:service}}));
  await page.route(`**/v1/studio/runs/${service}/configuration`,route=>route.fulfill({json:{service_run_id:service,kernel_run_id:'fixture-kernel-run',epoch:'fixture-epoch',scenario:{engines:{}}}}));
  await page.route(`**/v1/runs/${service}/capture-requests/photo-1/scene`,route=>route.fulfill({json:{service_run_id:service,kernel_run_id:'fixture-kernel-run',commits:fixtureCommits}}));
  await page.goto('/runs?capture=1&capture_assets=%2Fcapture-assets.json&api=http%3A%2F%2F127.0.0.1%3A4179');
  await page.waitForFunction(()=>!!window.aeroCapture);
  const request={request_id:'photo-1',run_id:'fixture-kernel-run',actor:{run_id:'fixture-kernel-run',epoch:'fixture-epoch',id:'vehicle-1',generation:2,type_id:'vehicle'},source_cut:{index:4,instant:[{$integer:'9007199254740993'},0]},asset_digest:digest,camera:{revision:'fixture-camera/1',eye:[40,30,40],target:[25,10,-35],fov:55,frame:'render-world'},width:128,height:96,timeout_s:30};
  const captured=await page.evaluate(async input=>window.aeroCapture!.render(input),{request,service_run_id:service,label:'fixture photo'});
  expect(captured.request.source_cut).toEqual(request.source_cut);
  const bytes=Buffer.from(captured.png_data_url.split(',')[1], 'base64');
  expect(bytes.subarray(0,8).toString('hex')).toBe('89504e470d0a1a0a');
  expect(bytes.readUInt32BE(16)).toBe(128);expect(bytes.readUInt32BE(20)).toBe(96);
  const error=await page.evaluate(async input=>{try{await window.aeroCapture!.render(input);return null;}catch(problem){return String(problem);}}, {request:{...request,actor:{...request.actor,epoch:'different-epoch'}},service_run_id:service});
  expect(error).toContain('identity mismatch');
});

test('Studio edits one chain model and retains unsupported content through server validation', async ({page})=>{
  const authorPackage={format:'aeroagentsim.behaviour-package/v1',id:'fixture.rules',revision:1,future_extension:{keep:false,value:null},predicates:{ready:{profile:'committed_reactive/v1',roles:{task:'task'},expression:{literal:true}}},chains:{response:{roles:{task:'task'},trigger:{predicate:'ready',edge:'entered'},preconditions:['ready'],initial:'waiting',terminal:['done'],states:['waiting','done'],transitions:[{id:'launch',from:'waiting',on:{instance:'activated'},to:'done',priority:1,actions:[],future_transition:{keep:true}}]}},bindings:[],conflicts:[],injection_points:[]};
  const workspace={id:'workspace-fixture',name:'Authoring fixture',scenario:{id:'authored',registry:{types:[{id:'task',parents:[],abstract:false}],fields:[],relations:[]},engines:{},entities:[],bindings:{exact:[],rules:[]},behaviours:[authorPackage]}};
  let saved=workspace;
  await page.route('**/v1/studio/catalog',route=>route.fulfill({json:{extracts:[],engines:[]}}));
  await page.route('**/v1/studio/types?*',route=>route.fulfill({json:{types:[],fields:[],relations:[]}}));
  await page.route('**/v1/studio/workspaces',route=>route.fulfill({json:[workspace]}));
  await page.route('**/v1/studio/workspaces/workspace-fixture',route=>{
    if(route.request().method()==='POST')saved={...workspace,...route.request().postDataJSON()};
    return route.fulfill({json:saved});
  });
  await page.route('**/v1/studio/workspaces/workspace-fixture/behaviours/0/validate',route=>route.fulfill({json:{valid:false,compiler_available:false,errors:[{path:'$.chains.response.transitions[0].guard',message:'Compiler is not integrated in this fixture'}]}}));
  await page.goto('/studio?api=http%3A%2F%2F127.0.0.1%3A4179');
  await page.getByTestId('workspace-picker').click();
  await page.getByText('Authoring fixture',{exact:true}).last().click();
  await page.getByTestId('studio-step-rules').click();
  await page.getByRole('button',{name:'chains',exact:true}).click();
  await page.getByLabel('Transition 0 id').fill('finish');
  await expect(page.getByLabel('Graph transition finish')).toBeVisible();
  await page.getByRole('button',{name:'Validate package on server'}).click();
  await expect(page.getByRole('button',{name:/Compiler is not integrated/})).toBeVisible();
  expect(saved.scenario.behaviours[0].chains.response.transitions[0].id).toBe('finish');
  expect(saved.scenario.behaviours[0].future_extension).toEqual({keep:false,value:null});
  expect(saved.scenario.behaviours[0].chains.response.transitions[0].future_transition).toEqual({keep:true});
  await expect(page.getByRole('button',{name:'Run now'})).toBeDisabled();
  await page.locator('.behaviour-editor').screenshot({path:'test-results/console/screenshots/console-authoring.jpg',type:'jpeg',quality:65});
  await page.getByRole('button',{name:'raw',exact:true}).click();
  await page.getByLabel('Behaviour package JSON').fill('{"id":');
  await expect(page.getByRole('button',{name:'Validate package on server'})).toBeDisabled();
});

test('demo-sized graph filters 100 real identities and keeps selected relation context',async({page})=>{
 const commit={...fixtureCommits[0],created:[...fixtureCommits[0].created,...Array.from({length:97},(_,index)=>({id:`extra-${index}`,generation:1,typeId:'vehicle'}))],facts:[...fixtureCommits[0].facts,...Array.from({length:97},(_,index)=>({entity:{id:`extra-${index}`,generation:1},fieldId:'position',value:[index%10*5,Math.floor(index/10)*5,0],producer:'motion',validFrom:{ns:'0',microstep:0}}))]};
 await page.route('**/v1/runs',route=>route.fulfill({json:[{id:'fixture-kernel-run',scenario:'100 entity fixture',status:'completed',until_ns:'0'}]}));
 await page.route('**/v1/runs/fixture-kernel-run/header',route=>route.fulfill({json:{...fixtureHeader,end:{ns:'0',microstep:0}}}));
 await page.route('**/v1/runs/fixture-kernel-run/commits?*',route=>route.fulfill({json:{commits:[commit],next:2,status:'completed'}}));
 await page.route('**/v1/runs/fixture-kernel-run/artifacts',route=>route.fulfill({json:[]}));
 await page.route('**/v1/studio/runs/fixture-kernel-run/configuration',route=>route.fulfill({json:{scenario:{engines:{}}}}));
 await page.goto('/runs/fixture-kernel-run?mode=replay&api=http%3A%2F%2F127.0.0.1%3A4179');
 await expect(page.getByTestId('viewport')).toHaveAttribute('data-rendered','true');
 await expect(page.getByText('100 / 100 entities',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:/task → task-1/}).first().click();
 await page.getByLabel('Focus + neighbors').check();
 await expect(page.getByText('2 / 100 entities',{exact:true})).toBeVisible();
 await page.getByLabel('Graph directory').selectOption('tasks');
 await expect(page.getByText('1 / 100 entities',{exact:true})).toBeVisible();
 await expect(page.getByTestId('inspector')).toContainText('task-1');
});
