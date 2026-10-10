import {describe,expect,it} from 'vitest';
import type {ChainInstance,FeedCommit,RunHeader} from '../contracts/viewer-feed';
import {TemporalFeedStore} from '../feeds/temporal-store';
import {entityStory} from './entity-story';
import {businessProjection} from './projection';
import {at,emptyCommit,instance} from '../behaviours/extension-fixture';

const drone={id:'drone-1',generation:0},task={id:'task-1',generation:0},stop={id:'customer-4',generation:0},zone={id:'central-no-fly',generation:0};
const header:RunHeader={contract:'aeroagentsim.viewer-feed/v1',runId:'story',registryDigest:'test',start:at('0'),fields:[],presentation:[],
 types:['UAV','Task','Customer','AirspaceZone'].map(type=>({typeId:`lowalt:${type}`,displayName:type,ancestors:[]})),
 behaviour:{extensions:[],packages:[{predicates:{battery_below_reserve:{parameters:{reserve_percent:20}},wind_above_limit:{parameters:{parcel_limit_mps:12}}}}]}};
const fact=(entity:typeof drone,fieldId:string,value:unknown,ns='0')=>({entity,fieldId:`lowalt.${fieldId}`,value,producer:'test',validFrom:at(ns)});
const chain=(name:string,state:string):ChainInstance=>({...instance,instanceId:name,templateId:name,state,transitionId:'trigger',roles:{uav:drone,zone},validFrom:at('0')});
function session(changes:FeedCommit['facts']=[],chains:ChainInstance[]=[]){
 const store=new TemporalFeedStore(header);
 store.ingest({...emptyCommit(1),created:[{...drone,typeId:'lowalt:UAV'},{...task,typeId:'lowalt:Task'},{...stop,typeId:'lowalt:Customer'},{...zone,typeId:'lowalt:AirspaceZone'}],
  facts:[fact(drone,'vehicle_class','parcel'),fact(drone,'soc',.18),fact(drone,'charging',false),fact(drone,'weather_held',false),fact(drone,'returning_to_charge',false),fact(task,'task_vehicle',drone.id),fact(task,'task_kind','parcel'),fact(task,'task_state','picked_up'),fact(task,'task_destination',stop.id),fact(stop,'label','Customer rooftop 4'),fact(zone,'label','Central No-Fly'),...changes],chainInstances:chains,
  events:[{at_ns:'0',category:'logistics',severity:'notice',text:'Parcel picked up',text_zh:'已领取包裹',entities:[drone.id,task.id]}]});store.seek('0');
 return store;
}
const story=(store:TemporalFeedStore,locale='en-US')=>entityStory(store,store.entities.get('drone-1:0')!,businessProjection(store.commits,store.viewCursor!.knownAt,store.viewCursor!.validAt.ns).events,locale);

describe('committed one-line entity stories',()=>{
 it('explains battery recall using the authored reserve and recorded chain',()=>{
  const store=session([fact(drone,'returning_to_charge',true)],[chain('battery_reserve','returning')]);
  expect(story(store).status).toBe('Returning to charge — battery 18% below reserve 20%');
  expect(story(store).cause).toEqual({kind:'step',instanceId:'battery_reserve',revision:0});
  expect(story(store,'zh-CN').status).toBe('返回充电 — 电量 18% 低于预留阈值 20%');
 });
 it('prioritizes a weather hold over a task and explains wind against its recorded limit',()=>{
  const store=session([fact(drone,'weather_held',true),fact(drone,'wind_mps',14),fact(drone,'wind_limit_mps',12)],[chain('wind_limit','held')]);
  expect(story(store).status).toBe('Holding — wind 14 m/s above limit 12 m/s');
  expect(story(store,'zh-CN').status).toBe('等待 — 风速 14 米/秒超过限值 12 米/秒');
 });
 it('shows charging rather than a stale return and never invents a missing threshold',()=>{
  const store=session([fact(drone,'returning_to_charge',true),fact(drone,'charging',true)]);
  expect(story(store).status).toBe('Charging — replenishing battery');
  const unknown=new TemporalFeedStore({...header,behaviour:{extensions:[]}});unknown.ingest({...store.commits[0],facts:store.commits[0].facts.filter(row=>row.fieldId!=='lowalt.charging')});unknown.seek('0');
  expect(story(unknown).status).toBe('Returning to charge — battery reserve rule active');
  expect(story(unknown).status).not.toContain('20%');
 });
 it('uses ongoing no-fly chain roles, drops the old explanation after exit, and respects an exact cut',()=>{
  const reroute=chain('no_fly_zone','rerouting'),store=session([], [reroute]);
  expect(story(store).status).toBe('Re-routing around Central No-Fly');
  store.ingest({...emptyCommit(2,'1000000000'),chainInstances:[{...reroute,op:'close',validTo:at('1000000000')},{...reroute,state:'monitoring',transitionId:'left-zone',revision:1,validFrom:at('1000000000')}]});
  store.seek('1000000000');expect(story(store).status).toBe('Delivering parcel to Customer rooftop 4');
  store.seek('0',1);expect(story(store).status).toBe('Re-routing around Central No-Fly');
 });
 it('derives destination and business only from the active assignment, not a suspended task',()=>{
  const store=session();const suspended={id:'task-2',generation:0};
  store.ingest({...emptyCommit(2),created:[{...suspended,typeId:'lowalt:Task'}],facts:[fact(suspended,'task_vehicle',drone.id),fact(suspended,'task_state','paused'),fact(suspended,'task_kind','medical')]});store.seek('0');
  expect(story(store)).toMatchObject({business:'Parcel',vehicleClass:'Parcel',battery:.18,nextStop:'Customer rooftop 4',status:'Delivering parcel to Customer rooftop 4'});
  expect(story(store).task?.key).toEqual(task);
  expect(story(store,'zh-CN').status).toBe('正在向客户屋顶 4配送包裹');
 });
 it('describes a recorded separation hold only until its committed deadline',()=>{
  const store=session();store.ingest({...emptyCommit(2),messages:[{id:'hold',kind:'event',schemaId:'lowalt.control',source:'airspace',at:at('0'),subjects:[drone],payload:{vehicle:drone.id,action:'hold',reason:'separation conflict',until_ns:1000000000}}]});store.seek('0');
  expect(story(store).status).toBe('Holding — maintaining safe separation');expect(story(store).cause).toEqual({kind:'message',messageId:'hold',cut:2});
  store.seek('1000000000');expect(story(store).status).toBe('Delivering parcel to Customer rooftop 4');store.seek('0',1);expect(story(store).status).toBe('Delivering parcel to Customer rooftop 4');
 });
 it('explains an engine-recorded detour only while its zone and assignment remain active',()=>{
  const store=session();store.ingest({...emptyCommit(2),facts:[fact(zone,'zone_active',true)],messages:[{id:'detour',kind:'event',schemaId:'lowalt.task.replanned',source:'tasks',at:at('0'),subjects:[drone,task,zone],payload:{vehicle:drone.id,task:task.id,zone:zone.id,reason:'active no-fly zone'}}]});store.seek('0');
  expect(story(store).status).toBe('Re-routing around Central No-Fly');expect(story(store).cause).toEqual({kind:'message',messageId:'detour',cut:2});store.ingest({...emptyCommit(3),facts:[fact(zone,'zone_active',false)]});store.seek('0');expect(story(store).status).toBe('Delivering parcel to Customer rooftop 4');
 });
 it('shows the recorded charging destination instead of the interrupted task stop',()=>{
  const original=session([fact(drone,'returning_to_charge',true)]),pad={id:'pad-1',generation:0};
  const store=new TemporalFeedStore({...header,types:[...header.types,{typeId:'lowalt:ChargingPad',displayName:'Charging pad',ancestors:[]}]});
  store.ingest({...original.commits[0],created:[...original.commits[0].created,{...pad,typeId:'lowalt:ChargingPad'}],facts:[...original.commits[0].facts,fact(pad,'site_position',[5,6,0]),fact(pad,'label','Charging pad 1')],messages:[{id:'return',kind:'event',schemaId:'lowalt.control',source:'energy',at:at('0'),subjects:[drone],payload:{vehicle:drone.id,action:'return_to_pad',target:[5,6,0]}}]});store.seek('0');expect(story(store).nextStop).toBe('Charging pad 1');
  store.ingest({...emptyCommit(2),facts:[fact(drone,'charging',true)],messages:[{id:'charge',kind:'event',schemaId:'lowalt.energy.charging_started',source:'energy',at:at('0'),subjects:[drone,pad],payload:{vehicle:drone.id,pad:pad.id}}]});store.seek('0');expect(story(store).nextStop).toBe('Charging pad 1');
 });
 it('shows only the last six associated business events and never includes a later commit',()=>{
  const store=session();
  for(let i=2;i<=10;i++)store.ingest({...emptyCommit(i),events:[{at_ns:'0',severity:'info',category:'logistics',text:`event ${i}`,entities:[i===9?'other':drone.id]}]});
  store.seek('0',8);const events=businessProjection(store.commits,10,'0').events;
  const value=entityStory(store,store.entities.get('drone-1:0')!,events);
  expect(value.journey.map(row=>row.text)).toEqual(['event 3','event 4','event 5','event 6','event 7','event 8']);expect(value.latestEvent?.cut).toBe(8);
 });
 it('uses the latest recorded narrative as a localized fallback, without claiming a new action',()=>{
  const store=session([fact(task,'task_state','completed')]);
  expect(story(store).status).toBe('Parcel picked up');expect(story(store,'zh-CN').status).toBe('已领取包裹');
  expect(story(store).cause).toMatchObject({kind:'event',cut:1,text:'Parcel picked up'});
 });
});
