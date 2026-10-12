import {test,expect,type Page} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {TemporalFeedStore} from '../src/feeds/temporal-store';
import {explain,type CauseRef} from '../src/trace/cause-trace';

test.use({viewport:{width:1600,height:1000},launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM,args:['--use-gl=swiftshader','--enable-unsafe-swiftshader','--no-proxy-server']}});
const examples=[{template:'traffic-accident',name:'traffic-default'}, {template:'city-day',name:'city-day'}, {template:'traffic-accident',name:'traffic-later',accident:12}];
for(const example of examples)test(`${example.name}: committed oblique photos and accident trace`,async({page},info)=>{
  test.setTimeout(480_000);page.setDefaultTimeout(60_000);
  const errors:string[]=[];page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  const root=join(process.env.AEROAGENTSIM_E2E_ROOT!,'screenshots');await mkdir(root,{recursive:true});
  await page.goto('/?gallery=1');await page.getByTestId(`wb-template-${example.template}`).click();await page.getByTestId('wb-create-project').click();await expect(page.getByTestId('wb-config')).toBeVisible({timeout:90_000});
  if(example.accident){await page.getByTestId('knob-accident_time_s').fill(String(example.accident));await page.getByRole('button',{name:'Apply controls to draft',exact:true}).click();}
  await page.getByTestId('wb-validate').click();await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});await page.getByTestId('wb-save-version').click();await expect(page.getByTestId('wb-initialize')).toBeEnabled({timeout:90_000});await page.getByTestId('wb-initialize').click();
  await expect(page.getByTestId('workbench')).toHaveAttribute('data-status','initialized',{timeout:120_000});await expect(page.locator('[data-testid=viewport][data-rendered=true]')).toBeVisible({timeout:120_000});
  await page.getByTestId('wb-start').click();await page.getByTestId('wb-speed').selectOption('max');const run=new URL(page.url()).searchParams.get('run')!;
  try{
    await expect.poll(async()=>{const response=await page.request.get(`/v1/runs/${run}/artifacts`);expect(response.ok()).toBe(true);return (await response.json()).length;},{timeout:240_000}).toBeGreaterThan(0);
    const artifact=(await(await page.request.get(`/v1/runs/${run}/artifacts`)).json())[0];
    expect(artifact.renderer_mode).toBe('browser');expect(artifact.request.camera.revision).toBe('uav-gimbal-oblique/v1');expect(artifact.request.width).toBe(960);expect(artifact.request.height).toBe(540);
    expect(artifact.request.camera.subject.id).toBe('incident.01');expect(artifact.request.camera.subject.field).toBe('traffic.incident.position_enu_m');
    const png=await(await page.request.get(`/v1/runs/${run}/artifacts/${artifact.digest}/download`)).body();expect(png.readUInt32BE(16)).toBe(960);expect(png.readUInt32BE(20)).toBe(540);expect(png.length).toBeGreaterThan(50_000);
    await writeFile(join(root,`${example.name}-photo.png`),png);await writeFile(join(root,`${example.name}-metadata.json`),JSON.stringify(artifact,null,2));
    await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();await page.getByRole('button',{name:'Open photo',exact:true}).first().click();const photo=page.getByTestId('wb-dock').getByRole('img',{name:'Stored capture photo',exact:true});await expect(photo).toBeVisible();await expect.poll(()=>photo.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(960);
    for(const [width,height] of [[1280,800],[1600,1000],[1920,1080]]){
      await page.setViewportSize({width,height});await expect(page.locator('[data-testid=viewport][data-rendered=true]')).toBeVisible();await photo.scrollIntoViewIfNeeded();
      const preview=page.getByRole('button',{name:'View full-size photo',exact:true}),dock=page.getByTestId('wb-dock').locator('.wb-dock-body');
      const previewBox=await preview.boundingBox(),dockBox=await dock.boundingBox();
      expect(previewBox!.height).toBeGreaterThan(dockBox!.height*.65);expect(previewBox!.width).toBeGreaterThan(dockBox!.width*.6);
      await page.screenshot({path:join(root,`${example.name}-${width}-en.png`)});
      const identity=page.getByTestId('wb-inspector').locator('.wb-entity-head'),selection=await identity.textContent();
      await preview.click();const dialog=page.getByRole('dialog'),fullPhoto=dialog.getByRole('img',{name:'Stored capture photo',exact:true});
      await expect(fullPhoto).toBeVisible();await expect(fullPhoto).toHaveAttribute('src',(await photo.getAttribute('src'))!);
      await expect(dialog).toContainText(artifact.request.request_id);await expect(dialog).toContainText(`Source cut #${artifact.request.source_cut.index}`);
      await expect.poll(()=>fullPhoto.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(960);
      // The dialog zooms in; measure its settled size.
      await expect.poll(async()=>{const box=await fullPhoto.boundingBox();return box?`${box.width>=950?'wide':box.width}x${box.height>=530?'tall':box.height}`:'missing';}).toBe('widextall');
      await page.screenshot({path:join(root,`${example.name}-${width}-full-photo-en.png`)});
      await page.keyboard.press('Escape');await expect(dialog).toBeHidden({timeout:15_000});await expect(preview).toBeFocused();
      await expect(identity).toHaveText(selection!);
    }
    await page.getByRole('button',{name:'Switch to Chinese',exact:true}).click();await page.screenshot({path:join(root,`${example.name}-1920-zh.png`)});
    await page.getByRole('button',{name:'查看完整尺寸照片',exact:true}).click();const translatedDialog=page.getByRole('dialog');await expect(translatedDialog).toContainText('来源日志位置');
    await page.screenshot({path:join(root,`${example.name}-1920-full-photo-zh.png`)});await translatedDialog.getByRole('button',{name:/^(关闭|Close)$/}).click();await expect(translatedDialog).toBeHidden({timeout:15_000});
    await page.getByRole('button',{name:'切换到英文',exact:true}).click();
    if(example.name==='traffic-default'){
      await expect(page.getByTestId('workbench')).toHaveAttribute('data-status','completed',{timeout:180_000});
      const store=new TemporalFeedStore(await(await page.request.get(`/v1/runs/${run}/header`)).json());
      for(let cursor=1;;){const feed=await(await page.request.get(`/v1/runs/${run}/commits?from=${cursor}&limit=4096`)).json();feed.commits.forEach((c:any)=>store.ingest(c));if(!feed.commits.length)break;cursor=feed.next;}
      store.seek(store.commits.at(-1)!.at.ns);
      const activated=store.commits.flatMap(c=>(c.events??[]).map(event=>({event,cut:c.commitIndex}))).find(r=>r.event.text==='Accident activated')!;expect(activated).toBeDefined();
      const initial:CauseRef={kind:'event',cut:activated.cut,text:activated.event.text,entities:activated.event.entities};
      type Hop={ref:CauseRef;heading:string;slot?:'followUp'|'trigger'|'sender'|'choice';label?:string};
      const queue:Array<{ref:CauseRef;path:Hop[];slot?:Hop['slot'];label?:string}>=[{ref:initial,path:[]}],seen=new Set<string>();let path:Hop[]|undefined;
      while(queue.length){const node=queue.shift()!,key=JSON.stringify(node.ref);if(seen.has(key))continue;seen.add(key);const model=explain(store,node.ref);if(!model)continue;const hops=[...node.path,{ref:node.ref,heading:model.heading,slot:node.slot,label:node.label}];
        if(model.step?.chainId==='traffic.capture_execute'&&model.step.toState==='completed'){path=hops;break;}
        for(const item of model.followUps)if(item.ref)queue.push({ref:item.ref,path:hops,slot:'followUp',label:item.label});
        if(model.trigger?.ref)queue.push({ref:model.trigger.ref,path:hops,slot:'trigger'});if(model.sender?.ref)queue.push({ref:model.sender.ref,path:hops,slot:'sender'});for(const item of model.choices)queue.push({ref:item.ref,path:hops,slot:'choice',label:item.label});
      }
      expect(path,'committed accident → capture completion links').toBeDefined();await writeFile(join(root,'accident-photo-lineage.json'),JSON.stringify(path,null,2));
      await page.getByTestId('wb-dock').getByRole('button',{name:/^Events\b/}).click();const event=page.getByTestId('wb-dock').locator('.newc-event-item').filter({hasText:'Accident activated'}).first();await expect(event).toBeVisible();await event.getByRole('button',{name:'Why did this happen?',exact:true}).click();await expect(page.getByTestId('cause-trace')).toBeVisible();await page.screenshot({path:join(root,'why-accident.png')});
      for(const hop of path!.slice(1)){const trace=page.getByTestId('cause-trace');
        if(hop.slot==='followUp')await trace.locator('.ct-follow-btn').filter({hasText:hop.label}).first().click();else if(hop.slot==='choice')await trace.locator('.ct-choices .ct-link').filter({hasText:hop.label}).first().click();else if(hop.slot==='sender')await trace.getByRole('button',{name:'See that step',exact:true}).click();else await trace.locator('.ct-trigger .ct-link').filter({hasText:'See where it came from'}).first().click();await expect(trace.locator('.ct-title')).toHaveText(hop.heading);
      }
      await page.screenshot({path:join(root,'why-photo-completed.png')});await page.getByRole('button',{name:'Switch to Chinese',exact:true}).click();await page.screenshot({path:join(root,'why-photo-completed-zh.png')});
    }
    if(example.name==='traffic-default'){
      // Old requests carry their own sensor revision and cut. Rendering those
      // declarations against the archived scene must retain the exact footprint.
      await page.goto('/?capture=1&capture_assets='+encodeURIComponent(new URL('/v1/studio/demo-capture-assets',page.url()).href));
      await page.waitForFunction(()=>typeof window.aeroCapture?.render==='function');
      // Use the SAME issued inventory as the production template renderer,
      // including the traffic bundle alias and the selected actor catalog.
      const bundle=JSON.parse(execFileSync(resolve('..','.venv/bin/python'),['-c',
        'import json,sys; from pathlib import Path; from aeroagentsim.observations.committed import CommittedBrowserRenderer; p=Path(sys.argv[1]); c=json.loads((p/"scenario.json").read_text())["engines"]["capture"]["config"]["renderer"]; r=CommittedBrowserRenderer(c,p); print(json.dumps(r.manifest)); r.close()',
        join(process.env.AEROAGENTSIM_E2E_ROOT!,'runs',run)],{encoding:'utf8'}));
      const [header,configuration,prefix]=await Promise.all([
        page.request.get(`/v1/runs/${run}/header`).then(r=>r.json()),
        page.request.get(`/v1/studio/runs/${run}/configuration`).then(r=>r.json()),
        page.request.get(`/v1/runs/${run}/capture-requests/${encodeURIComponent(artifact.request.request_id)}/scene`).then(r=>r.json()),
      ]);
      const exact=new TemporalFeedStore(header);prefix.commits.forEach((c:any)=>exact.ingest(c));exact.seek(String(artifact.request.source_cut.instant[0]),artifact.request.source_cut.index);
      const scene={header,configuration,asset_manifest:bundle,snapshot:{source_cut:artifact.request.source_cut,entities:[...exact.entities.values()].map(e=>({key:e.key,typeId:e.typeId,fields:[...e.fields.values()]}))}};
      const photos:string[]=[];
      for(const revision of ['traffic-nadir/v1','traffic-city-nadir/v1']){
        const request={...artifact.request,width:256,height:192,camera:{revision,fov:55,frame:'enu',snapshot:artifact.request.camera.snapshot}};
        const result=await page.evaluate(input=>window.aeroCapture!.render(input),{service_run_id:run,request,scene});
        photos.push(result.png_data_url);
        const diagnostics=await page.getByTestId('capture-canvas').evaluate(el=>JSON.parse(el.dataset.camera!));
        const eye=artifact.request.camera.snapshot.entities.find((pose:any)=>pose.id===artifact.request.actor.id).position;
        expect(diagnostics.eye).toEqual([eye[0],eye[2],-eye[1]]);expect(diagnostics.target).toEqual([eye[0],0,-eye[1]]);expect(diagnostics.width).toBe(256);expect(diagnostics.height).toBe(192);
      }
      expect(photos[0]).toBe(photos[1]);
    }
    expect(errors).toEqual([]);
  }finally{await writeFile(info.outputPath('browser-errors.json'),JSON.stringify(errors));await page.request.post(`/v1/runs/${run}/stop`, { data: {} });}
});
