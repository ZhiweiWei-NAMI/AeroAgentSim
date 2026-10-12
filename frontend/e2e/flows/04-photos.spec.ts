import {flowTest as test,expect,createProject,initialize,segment,click,hold,evidence,complete} from './helpers';
import {TemporalFeedStore} from '../../src/feeds/temporal-store';
import {explain,type CauseRef} from '../../src/trace/cause-trace';
import type {RunHeader,FeedCommit} from '../../src/contracts/viewer-feed';
test('accident dispatch and a real oblique stored photo',async({page})=>{
 await createProject(page,'traffic-accident');await initialize(page);await page.getByTestId('wb-speed').selectOption('max');
 await segment(page,async()=>{await click(page.getByTestId('wb-start'));await hold(page,2200);},'Start the shipped scheduled accident scenario');
 await complete(page);
 await page.getByTestId('wb-dock').getByRole('button',{name:/^Events\b/}).click();const event=page.locator('.newc-event-item').filter({hasText:/Accident activated/}).first();await expect(event).toBeVisible({timeout:180_000});
 await segment(page,async()=>{await click(event.getByRole('button',{name:'Why did this happen?',exact:true}));await hold(page,3000);},'The authored accident records its response chain');
 await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();const open=page.getByRole('button',{name:'Open photo',exact:true}).first();await expect(open).toBeVisible({timeout:300_000});
 // Follow the recorded response through the real dispatch transition.
 const id=new URL(page.url()).searchParams.get('run')!,header=await (await page.request.get(`/v1/runs/${id}/header`)).json() as RunHeader,store=new TemporalFeedStore(header);
 for(let cursor=1;;){const feed=await (await page.request.get(`/v1/runs/${id}/commits?from=${cursor}&limit=4096`)).json() as {commits:FeedCommit[];next:number};feed.commits.forEach(commit=>store.ingest(commit));if(!feed.commits.length)break;cursor=feed.next;}
 store.seek(store.commits.at(-1)!.at.ns);
 const activated=store.commits.flatMap(commit=>(commit.events??[]).map(event=>({event,cut:commit.commitIndex}))).find(row=>row.event.text==='Accident activated')!;
 expect(activated).toBeDefined();
 const initial:CauseRef={kind:'event',cut:activated.cut,text:activated.event.text,entities:activated.event.entities};expect(explain(store,initial)?.step).toBeDefined();
 type Hop={ref:CauseRef;heading:string;slot?:'followUp'|'trigger'|'sender'|'choice';label?:string};
 const queue:Array<{ref:CauseRef;path:Hop[];slot?:Hop['slot'];label?:string}>=[{ref:initial,path:[]}],seen=new Set<string>();let path:Hop[]|undefined;
 while(queue.length){const node=queue.shift()!,key=JSON.stringify(node.ref);if(seen.has(key))continue;seen.add(key);const model=explain(store,node.ref);if(!model)continue;const hops=[...node.path,{ref:node.ref,heading:model.heading,slot:node.slot,label:node.label}];if(model.step?.chainId==='traffic.capture_execute'&&model.step.toState==='enroute'){path=hops;break;}for(const next of model.followUps)if(next.ref)queue.push({ref:next.ref,path:hops,slot:'followUp',label:next.label});if(model.trigger?.ref)queue.push({ref:model.trigger.ref,path:hops,slot:'trigger'});if(model.sender?.ref)queue.push({ref:model.sender.ref,path:hops,slot:'sender'});for(const next of model.choices)queue.push({ref:next.ref,path:hops,slot:'choice',label:next.label});}
 expect(path,'the recorded accident must reach physical dispatch').toBeDefined();
 await page.getByTestId('wb-dock').getByRole('button',{name:/^Events\b/}).click();await event.getByRole('button',{name:'Why did this happen?',exact:true}).click();
 for(const hop of path!.slice(1)){const trace=page.getByTestId('cause-trace');if(hop.slot==='followUp')await trace.locator('.ct-follow-btn').filter({hasText:hop.label}).first().click();else if(hop.slot==='choice')await trace.locator('.ct-choices .ct-link').filter({hasText:hop.label}).first().click();else if(hop.slot==='sender')await trace.getByRole('button',{name:'See that step',exact:true}).click();else await trace.locator('.ct-trigger .ct-link').filter({hasText:'See where it came from'}).first().click();await expect(trace.locator('.ct-title')).toHaveText(hop.heading);}
 await segment(page,async()=>{await hold(page,3500);},'Dispatch: the assigned UAV begins its recorded flight');await evidence(page,'photos-dispatch');await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();
 await segment(page,async()=>{await hold(page,1800);await click(open);},'Photos contains the real UAV oblique capture');const photo=page.getByRole('img',{name:'Stored capture photo',exact:true});await expect.poll(()=>photo.evaluate((el:HTMLImageElement)=>el.naturalWidth)).toBe(960);await expect(photo).toBeInViewport();
 await segment(page,async()=>{await hold(page,6000);});await evidence(page,'photos-oblique');await complete(page);
});
