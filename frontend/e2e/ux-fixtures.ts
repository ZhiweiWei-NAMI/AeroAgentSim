/** Explicit adapters for the API job's endpoints, which are not on this branch.
 * The console has no fixture fallback. Starting/importing the accident still
 * calls the real authoring/compiler/run backend. */
import type {Page,Route} from '@playwright/test';
import {parseAuthoringJson,stringifyLossless} from '../src/feeds/lossless-json';
import type {AlgorithmDescriptor,ScenarioTemplate} from '../src/newcomers/api';
export const templateFixtures:ScenarioTemplate[]=[
 {id:'traffic-accident',title:'Respond to a traffic accident',category:'emergency',summary:'Watch two drones coordinate a response and take a photo of an accident.',duration_s:140},
 {id:'parcel-delivery',title:'Deliver parcels across the city',category:'logistics',summary:'Find out how fleet size and task assignment change delivery time.',duration_s:300},
 {id:'powerline-inspection',title:'Inspect a power line',category:'inspection',summary:'Send drones along a route and see which sections they inspect.',duration_s:240},
 {id:'crop-survey',title:'Survey a field',category:'agriculture',summary:'Try a survey route and explore how much ground the drones cover.',duration_s:300},
 {id:'district-patrol',title:'Patrol a neighbourhood',category:'security',summary:'Coordinate a patrol and explore the effect of changing routes.',duration_s:180},
 {id:'air-taxi',title:'Connect two landing sites',category:'mobility',summary:'Explore passenger trips and shared airspace between two sites.',duration_s:300},
].map(t=>({...t,format:'aeroagentsim.template/v1',difficulty:'beginner',scenario:'scenario.yaml',observe:['Watch the vehicles move and the event chain progress.'],try:['Change a setting, then compare the two runs.'],knobs:t.id==='traffic-accident'?[{id:'physics_hz',label:'Motion updates per second',type:'integer',default:1,min:1,max:5}]:[{id:'fleet_size',label:'Number of drones',type:'integer',default:6,min:2,max:20},{id:'allocator',label:'Task assignment',type:'choice',default:'nearest',options:['nearest','auction','hungarian']}],kpis:['tasks_completed','mean_task_latency_s']} as ScenarioTemplate));
export const algorithmFixtures:AlgorithmDescriptor[]=[
 {id:'nearest',slot:'allocation',label:'Nearest free drone',description:'Give the task to the closest available drone.'},
 {id:'auction',slot:'allocation',label:'Auction',description:'Drones bid for a task; the lowest cost wins.'},
 {id:'hungarian',slot:'allocation',label:'Matching',description:'Match several drones and tasks together to reduce total cost.'},
 {id:'llm',slot:'allocation',label:'Language-model agent',description:'Ask a configured decision agent to propose an assignment.'},
 {id:'straight',slot:'path_planning',label:'Straight line',description:'Fly directly between two places.'},
 {id:'corridor_astar',slot:'path_planning',label:'Safe corridors',description:'Find a route around restricted airspace.'},
 {id:'altitude_layers',slot:'conflict_resolution',label:'Separate heights',description:'Give conflicting drones different flying heights.'},
 {id:'hold_and_go',slot:'conflict_resolution',label:'Wait, then go',description:'Pause one drone until the route is clear.'},
 {id:'threshold_return',slot:'charging',label:'Return before battery runs low',description:'Return to a charging pad when the reserve reaches its limit.'},
];
async function realJson(page:Page,base:string,path:string,body?:unknown):Promise<any>{const response=body===undefined?await page.request.get(base+path):await page.request.post(base+path,{headers:{'Content-Type':'application/json'},data:stringifyLossless(body)});if(!response.ok())throw Error(`${path}: ${response.status()} ${await response.text()}`);return parseAuthoringJson(await response.text());}
export async function installUxApiFixtures(page:Page,base:string){
 const json=(route:Route,value:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
 await page.route('**/v1/algorithms',route=>json(route,Object.fromEntries(['allocation','path_planning','conflict_resolution','charging'].map(slot=>[slot,algorithmFixtures.filter(row=>row.slot===slot)]))));
 await page.route('**/v1/templates**',async route=>{
  const url=new URL(route.request().url()),match=url.pathname.match(/^\/v1\/templates(?:\/([^/]+)(?:\/(runs|workspace))?)?$/);
  if(!match)return route.continue();
  if(!match[1])return json(route,templateFixtures);
  const template=templateFixtures.find(row=>row.id===decodeURIComponent(match[1]));if(!template)return json(route,{detail:'Unknown fixture template'},404);
  if(!match[2])return json(route,template);
  if(template.id!=='traffic-accident')return json(route,{detail:'This UI fixture has no runtime scenario. Use the real accident demo.'},422);
  try{
   const body=route.request().postDataJSON(),knobs=body.knobs??{};
   if(Object.keys(knobs).some(key=>key!=='physics_hz')||knobs.physics_hz!==undefined&&knobs.physics_hz!==1)return json(route,{detail:'The test adapter supports only the demo’s real default timing.'},422);
   const ws=await realJson(page,base,'/v1/studio/workspaces',{name:'Traffic accident · newcomer workflow'});
   const draft=await realJson(page,base,`/v1/studio/workspaces/${ws.id}/templates/traffic-accident`,{console:true,capture_mode:'city'});
   if(match[2]==='workspace')return json(route,{workspace_id:ws.id},201);
   const validation=await realJson(page,base,`/v1/studio/workspaces/${ws.id}/validate`,{});if(!validation.valid)throw Error(JSON.stringify(validation));
   const run=await realJson(page,base,'/v1/runs',{scenario:draft.scenario,studio_workspace:ws.id});return json(route,{run_id:run.id,workspace_id:ws.id},201);
  }catch(error){return json(route,{detail:String(error)},500);}
 });
 // Results intentionally remain unavailable until the API job supplies committed
 // measurements. A separate comparison screenshot uses explicitly marked data.
}
/** The demo's authored accident is at 8 s, once its cars reach the junction.
 * Earlier injections are admitted but cannot satisfy its proximity predicate.
 * Wait on displayed simulation time, rather than a wall-clock sleep. */
export async function waitForTrafficJunction(page:Page){
 await page.waitForFunction(()=>{
  const text=document.querySelector('.simple-timeline time')?.textContent;
  const match=text?.match(/^(\d+):(\d+(?:\.\d+)?)/);
  return !!match&&Number(match[1])*60+Number(match[2])>=8;
 },undefined,{timeout:60_000});
}
